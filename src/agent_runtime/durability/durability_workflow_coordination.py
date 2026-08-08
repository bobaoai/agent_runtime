"""Domain-neutral coordination between durable cursors and Module Activities.

The coordinator owns the crash boundary between a committed ``ModuleOutcome``
and the backend acknowledgement that advances the durable cursor.  It never
loads domain content and never interprets Module, artifact, or verdict meaning.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..contracts.registry_release_definition import WorkflowRelease
from ..contracts.registry_workflow_definition import (
    ModuleDispatchRequest,
    ModuleOutcome,
    ModuleOutcomeDisposition,
)
from ..registry.registry_graph_projection import project_workflow_release_graph
from ..contracts.execution_host_definition import RuntimeWorkflowStartRequest
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from ..contracts.durability_topology_definition import (
    BackendExecutionRef,
    ExecutionSnapshot,
    ExternalEvent,
    WorkflowGraphProjection,
)


class DurableExecutionStopReason(StrEnum):
    """Reason a bounded coordinator call returned control to its caller."""

    TERMINAL = "terminal"
    WAIT = "wait"
    RETRYABLE_FAILURE = "retryable_failure"
    DISPATCH_LIMIT = "dispatch_limit"


@runtime_checkable
class DurableWorkflowCursor(Protocol):
    """Backend-neutral cursor operations required by the coordinator."""

    async def start(
        self,
        request: RuntimeWorkflowStartRequest,
    ) -> BackendExecutionRef:
        """Start or recover one exact Workflow Execution."""

        ...

    async def query(
        self,
        execution: BackendExecutionRef,
    ) -> ExecutionSnapshot:
        """Return the backend-authoritative cursor."""

        ...

    async def signal(
        self,
        execution: BackendExecutionRef,
        event: ExternalEvent,
    ) -> ExecutionSnapshot:
        """Acknowledge one committed outcome and advance the cursor."""

        ...


@runtime_checkable
class CellModuleActivityBridge(Protocol):
    """Cell-local bridge from a ref-only dispatch to one committed outcome."""

    def dispatch(self, request: ModuleDispatchRequest) -> ModuleOutcome:
        """Execute or replay one stable logical dispatch."""

        ...

    def get_committed_outcome(
        self,
        workflow_execution_id: str,
        dispatch_id: str,
    ) -> ModuleOutcome | None:
        """Resolve the committed result used for backend acknowledgement."""

        ...


@dataclass(frozen=True)
class DurableExecutionProgress:
    """Bounded result of one coordinator drive call."""

    backend_execution: BackendExecutionRef
    snapshot: ExecutionSnapshot
    stop_reason: DurableExecutionStopReason
    dispatch_count: int
    last_outcome: ModuleOutcome | None

    def validate(self) -> None:
        """Validate one bounded coordinator result and its stop semantics."""

        self.backend_execution.validate()
        self.snapshot.validate()
        if type(self.stop_reason) is not DurableExecutionStopReason:
            raise ValueError("stop_reason must be DurableExecutionStopReason")
        if not isinstance(self.dispatch_count, int) or self.dispatch_count < 0:
            raise ValueError("dispatch_count must be a non-negative integer")
        if self.last_outcome is not None:
            self.last_outcome.validate()
            if (
                self.last_outcome.workflow_execution_id
                != self.backend_execution.workflow_execution_id
            ):
                raise PermissionError("progress crossed Workflow Execution")
        if self.snapshot.workflow_execution_id != (
            self.backend_execution.workflow_execution_id
        ):
            raise PermissionError("snapshot crossed Workflow Execution")
        if self.stop_reason is DurableExecutionStopReason.TERMINAL:
            if not self.snapshot.terminal:
                raise ValueError("terminal progress requires a terminal snapshot")
        elif self.snapshot.terminal:
            raise ValueError("nonterminal progress cannot carry a terminal snapshot")
        if self.stop_reason is DurableExecutionStopReason.WAIT:
            if (
                self.last_outcome is None
                or self.last_outcome.disposition is not ModuleOutcomeDisposition.WAIT
            ):
                raise ValueError("wait progress requires a wait ModuleOutcome")
        if self.stop_reason is DurableExecutionStopReason.RETRYABLE_FAILURE:
            if (
                self.last_outcome is None
                or self.last_outcome.disposition
                is not ModuleOutcomeDisposition.RETRYABLE_FAILURE
            ):
                raise ValueError(
                    "retryable failure progress requires its ModuleOutcome"
                )


class DurableExecutionCoordinator:
    """Drive exact Workflow Releases across committed Module outcomes."""

    service_id = "durable_execution_coordinator"

    def __init__(
        self,
        *,
        cursor: DurableWorkflowCursor,
        release_registry: RuntimeReleaseRegistry,
        activity_bridge: CellModuleActivityBridge,
    ) -> None:
        if not isinstance(cursor, DurableWorkflowCursor):
            raise TypeError("cursor does not implement DurableWorkflowCursor")
        if type(release_registry) is not RuntimeReleaseRegistry:
            raise TypeError("release_registry must be RuntimeReleaseRegistry")
        if not isinstance(activity_bridge, CellModuleActivityBridge):
            raise TypeError("activity_bridge does not implement CellModuleActivityBridge")
        self._cursor = cursor
        self._release_registry = release_registry
        self._activity_bridge = activity_bridge

    async def drive(
        self,
        request: RuntimeWorkflowStartRequest,
        *,
        max_dispatches: int = 100,
    ) -> DurableExecutionProgress:
        """Drive until terminal, wait, retryable failure, or the call bound."""

        request.validate()
        if not isinstance(max_dispatches, int) or max_dispatches < 1:
            raise ValueError("max_dispatches must be a positive integer")
        release = self._release_registry.get_workflow(
            request.workflow_release_ref,
            request.workflow_release_sha256,
        )
        self._validate_start_release(request, release)
        graph = project_workflow_release_graph(release)
        execution = await self._cursor.start(request)
        execution.validate()
        if execution.workflow_execution_id != request.workflow_execution_id:
            raise PermissionError("durable cursor crossed start execution identity")
        snapshot = await self._cursor.query(execution)
        self._validate_snapshot(snapshot, release, graph, execution)
        if snapshot.terminal:
            return self._progress(
                execution,
                snapshot,
                DurableExecutionStopReason.TERMINAL,
                dispatch_count=0,
                last_outcome=None,
            )

        last_outcome: ModuleOutcome | None = None
        for dispatch_count in range(1, max_dispatches + 1):
            dispatch = self._build_dispatch(release, snapshot, request)
            outcome = self._activity_bridge.dispatch(dispatch)
            outcome.validate()
            self._validate_outcome(dispatch, outcome)
            committed = self._activity_bridge.get_committed_outcome(
                dispatch.workflow_execution_id,
                dispatch.dispatch_id,
            )
            if committed != outcome:
                raise RuntimeError(
                    "Activity bridge returned an uncommitted ModuleOutcome"
                )
            last_outcome = outcome

            if outcome.disposition is ModuleOutcomeDisposition.WAIT:
                return self._progress(
                    execution,
                    snapshot,
                    DurableExecutionStopReason.WAIT,
                    dispatch_count,
                    outcome,
                )
            if outcome.disposition is ModuleOutcomeDisposition.RETRYABLE_FAILURE:
                return self._progress(
                    execution,
                    snapshot,
                    DurableExecutionStopReason.RETRYABLE_FAILURE,
                    dispatch_count,
                    outcome,
                )

            snapshot = await self._cursor.signal(
                execution,
                ExternalEvent(
                    event_id=dispatch.dispatch_id,
                    event_type="module_outcome",
                    workflow_execution_id=dispatch.workflow_execution_id,
                    expected_state=dispatch.current_state_id,
                    target_state=str(outcome.target_state_id),
                    evidence_ref=outcome.outcome_ref,
                ),
            )
            self._validate_snapshot(snapshot, release, graph, execution)
            if snapshot.terminal:
                return self._progress(
                    execution,
                    snapshot,
                    DurableExecutionStopReason.TERMINAL,
                    dispatch_count,
                    outcome,
                )

        return self._progress(
            execution,
            snapshot,
            DurableExecutionStopReason.DISPATCH_LIMIT,
            max_dispatches,
            last_outcome,
        )

    @staticmethod
    def _build_dispatch(
        release: WorkflowRelease,
        snapshot: ExecutionSnapshot,
        request: RuntimeWorkflowStartRequest,
    ) -> ModuleDispatchRequest:
        node = next(
            (
                candidate
                for candidate in release.nodes
                if candidate.node_id == snapshot.current_state
            ),
            None,
        )
        if (
            node is None
            or node.module_release_ref is None
            or node.module_release_sha256 is None
        ):
            raise ValueError("current Workflow node has no exact Module Release")
        sequence = len(snapshot.applied_events)
        digest = hashlib.sha256(
            "\x1f".join(
                (
                    snapshot.workflow_execution_id,
                    release.release_sha256,
                    request.execution_profile_selection_sha256,
                    snapshot.current_state,
                    str(sequence),
                )
            ).encode("utf-8")
        ).hexdigest()[:24]
        dispatch = ModuleDispatchRequest(
            workflow_execution_id=snapshot.workflow_execution_id,
            workflow_id=release.workflow_id,
            workflow_contract_version=release.workflow_contract_version,
            execution_release_ref=release.execution_release_ref,
            graph_sha256=release.graph_sha256,
            current_state_id=snapshot.current_state,
            transition_sequence=sequence,
            dispatch_id=f"dispatch_{digest}",
            workflow_release_ref=release.release_ref,
            workflow_release_sha256=release.release_sha256,
            execution_profile_selection_ref=(
                request.execution_profile_selection_ref
            ),
            execution_profile_selection_sha256=(
                request.execution_profile_selection_sha256
            ),
            module_release_ref=node.module_release_ref,
            module_release_sha256=node.module_release_sha256,
        )
        dispatch.validate()
        return dispatch

    @staticmethod
    def _validate_start_release(
        request: RuntimeWorkflowStartRequest,
        release: WorkflowRelease,
    ) -> None:
        if (
            request.execution_release_ref != release.execution_release_ref
            or request.execution_release_sha256
            != release.execution_release_sha256
        ):
            raise PermissionError("start request crossed Workflow execution release")

    @staticmethod
    def _validate_snapshot(
        snapshot: ExecutionSnapshot,
        release: WorkflowRelease,
        graph: WorkflowGraphProjection,
        execution: BackendExecutionRef,
    ) -> None:
        snapshot.validate()
        graph.validate()
        execution.validate()
        if (
            snapshot.backend_id != execution.backend_id
            or snapshot.backend_execution_id != execution.backend_execution_id
            or snapshot.workflow_execution_id
            != execution.workflow_execution_id
        ):
            raise PermissionError("durable snapshot crossed backend execution")
        if snapshot.workflow_id != release.workflow_id:
            raise PermissionError("durable cursor crossed Workflow identity")
        if snapshot.graph_sha256 != graph.graph_sha256:
            raise PermissionError("durable cursor crossed Workflow graph projection")
        state = graph.initial_state
        for event in snapshot.applied_events:
            if event.expected_state != state:
                raise ValueError("durable cursor event chain has a stale source state")
            if event.target_state not in graph.allowed_targets(state):
                raise ValueError("durable cursor event chain has an illegal transition")
            state = event.target_state
        if snapshot.current_state != state:
            raise ValueError("durable cursor state differs from its event chain")
        expected_terminal = state in graph.terminal_states
        if snapshot.terminal is not expected_terminal:
            raise ValueError("durable cursor terminal flag differs from graph state")

    @staticmethod
    def _validate_outcome(
        dispatch: ModuleDispatchRequest,
        outcome: ModuleOutcome,
    ) -> None:
        if (
            outcome.dispatch_id != dispatch.dispatch_id
            or outcome.workflow_execution_id != dispatch.workflow_execution_id
            or outcome.expected_state_id != dispatch.current_state_id
        ):
            raise PermissionError("ModuleOutcome crossed durable dispatch identity")

    @staticmethod
    def _progress(
        execution: BackendExecutionRef,
        snapshot: ExecutionSnapshot,
        stop_reason: DurableExecutionStopReason,
        dispatch_count: int,
        last_outcome: ModuleOutcome | None,
    ) -> DurableExecutionProgress:
        progress = DurableExecutionProgress(
            backend_execution=execution,
            snapshot=snapshot,
            stop_reason=stop_reason,
            dispatch_count=dispatch_count,
            last_outcome=last_outcome,
        )
        progress.validate()
        return progress


__all__ = [
    "CellModuleActivityBridge",
    "DurableExecutionCoordinator",
    "DurableExecutionProgress",
    "DurableExecutionStopReason",
    "DurableWorkflowCursor",
]
