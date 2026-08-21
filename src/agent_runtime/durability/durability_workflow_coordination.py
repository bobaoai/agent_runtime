"""Domain-neutral coordination between durable cursors and Module Activities.

The coordinator owns the crash boundary between a committed ``ModuleOutcome``
and the backend acknowledgement that advances the durable cursor.  It never
loads domain content and never interprets Module, artifact, or verdict meaning.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..contracts.registry_release_definition import (
    WorkflowParallelGroupBinding,
    WorkflowRelease,
)
from ..contracts.registry_workflow_definition import (
    ModuleDispatchRequest,
    ModuleOutcome,
    ModuleOutcomeDisposition,
)
from ..registry.registry_graph_projection import project_workflow_release_graph
from ..contracts.execution_host_definition import RuntimeWorkflowStartRequest
from ..contracts.durability_backend_definition import DurableBackendAdapter
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from ..contracts.durability_execution_definition import (
    BackendExecutionRef,
    ExecutionSnapshot,
    ExternalEvent,
    WorkflowGraphProjection,
)


class DurableExecutionStopReason(StrEnum):
    """Reason a bounded coordinator call returned control to its caller."""

    TERMINAL = "terminal"
    CANCELLED = "cancelled"
    WAIT = "wait"
    RETRYABLE_FAILURE = "retryable_failure"
    BLOCKED = "blocked"
    DISPATCH_LIMIT = "dispatch_limit"


@runtime_checkable
class CellModuleActivityBridge(Protocol):
    """Cell-local bridge from a ref-only dispatch to one committed outcome.

    ``dispatch`` may be called concurrently for branches in the same registered
    parallel group. Implementations must isolate per-dispatch mutable state and
    make shared ledger or provider-session access concurrency-safe.
    """

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
        if self.stop_reason in {
            DurableExecutionStopReason.TERMINAL,
            DurableExecutionStopReason.CANCELLED,
        }:
            if not self.snapshot.terminal:
                raise ValueError("terminal progress requires a terminal snapshot")
            if (
                self.stop_reason is DurableExecutionStopReason.CANCELLED
                and self.snapshot.runtime_status_id != "cancelled"
            ):
                raise ValueError("cancelled progress requires cancelled Runtime status")
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
        if (
            self.stop_reason is DurableExecutionStopReason.BLOCKED
            and (
                self.last_outcome is None
                or self.last_outcome.disposition
                is ModuleOutcomeDisposition.RETRYABLE_FAILURE
            )
        ):
            raise ValueError(
                "blocked progress requires a non-retryable blocking ModuleOutcome"
            )


class DurableExecutionCoordinator:
    """Drive exact Workflow Releases across committed Module outcomes."""

    service_id = "durable_execution_coordinator"

    def __init__(
        self,
        *,
        cursor: DurableBackendAdapter,
        release_registry: RuntimeReleaseRegistry,
        activity_bridge: CellModuleActivityBridge,
        max_parallel_dispatches: int = 16,
    ) -> None:
        if not isinstance(cursor, DurableBackendAdapter):
            raise TypeError("cursor does not implement DurableBackendAdapter")
        if type(release_registry) is not RuntimeReleaseRegistry:
            raise TypeError("release_registry must be RuntimeReleaseRegistry")
        if not isinstance(activity_bridge, CellModuleActivityBridge):
            raise TypeError("activity_bridge does not implement CellModuleActivityBridge")
        if (
            not isinstance(max_parallel_dispatches, int)
            or max_parallel_dispatches < 1
        ):
            raise ValueError("max_parallel_dispatches must be a positive integer")
        self._cursor = cursor
        self._release_registry = release_registry
        self._activity_bridge = activity_bridge
        self._max_parallel_dispatches = max_parallel_dispatches

    async def drive(
        self,
        request: RuntimeWorkflowStartRequest,
        *,
        max_dispatches: int = 100,
        max_committed_retry_scan: int = 1_000,
    ) -> DurableExecutionProgress:
        """Drive until terminal, wait, retryable failure, or the call bound."""

        request.validate()
        if not isinstance(max_dispatches, int) or max_dispatches < 1:
            raise ValueError("max_dispatches must be a positive integer")
        if (
            not isinstance(max_committed_retry_scan, int)
            or max_committed_retry_scan < 1
        ):
            raise ValueError(
                "max_committed_retry_scan must be a positive integer"
            )
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
                self._terminal_stop_reason(snapshot),
                dispatch_count=0,
                last_outcome=None,
            )

        last_outcome: ModuleOutcome | None = None
        dispatch_count = 0
        while dispatch_count < max_dispatches:
            parallel_group = self._parallel_group_for_state(
                release,
                snapshot.current_state,
            )
            if parallel_group is not None:
                branch_outcomes, new_dispatch_count = (
                    await self._dispatch_parallel_group(
                        release=release,
                        group=parallel_group,
                        snapshot=snapshot,
                        request=request,
                        remaining_dispatches=max_dispatches - dispatch_count,
                        max_dispatches=max_dispatches,
                        max_committed_retry_scan=max_committed_retry_scan,
                    )
                )
                if branch_outcomes is None:
                    return self._progress(
                        execution,
                        snapshot,
                        DurableExecutionStopReason.DISPATCH_LIMIT,
                        dispatch_count,
                        last_outcome,
                    )
                dispatch_count += new_dispatch_count
                last_outcome = branch_outcomes[-1]
                blocked_outcomes = tuple(
                    outcome
                    for outcome in branch_outcomes
                    if outcome.disposition
                    is not ModuleOutcomeDisposition.RETRYABLE_FAILURE
                    and not self._parallel_branch_can_join(
                        parallel_group,
                        outcome,
                    )
                )
                if blocked_outcomes:
                    return self._progress(
                        execution,
                        snapshot,
                        DurableExecutionStopReason.BLOCKED,
                        dispatch_count,
                        blocked_outcomes[0],
                    )
                retryable_failures = tuple(
                    outcome
                    for outcome in branch_outcomes
                    if outcome.disposition
                    is ModuleOutcomeDisposition.RETRYABLE_FAILURE
                )
                if retryable_failures:
                    return self._progress(
                        execution,
                        snapshot,
                        DurableExecutionStopReason.RETRYABLE_FAILURE,
                        dispatch_count,
                        retryable_failures[0],
                    )
                completion_digest = hashlib.sha256(
                    "\x1f".join(
                        (
                            snapshot.workflow_execution_id,
                            release.release_sha256,
                            parallel_group.group_id,
                            str(len(snapshot.applied_events)),
                            *(outcome.outcome_sha256 for outcome in branch_outcomes),
                        )
                    ).encode("utf-8")
                ).hexdigest()
                snapshot = await self._cursor.apply_external_event(
                    execution,
                    ExternalEvent(
                        event_id=f"parallel_{completion_digest[:24]}",
                        event_type=parallel_group.completion_outcome_id,
                        workflow_execution_id=snapshot.workflow_execution_id,
                        expected_state=parallel_group.control_node_id,
                        target_state=parallel_group.join_node_id,
                        evidence_ref=(
                            "parallel-group-completion:"
                            f"{parallel_group.group_id}/{completion_digest}"
                        ),
                    ),
                )
                self._validate_snapshot(snapshot, release, graph, execution)
                if snapshot.terminal:
                    return self._progress(
                        execution,
                        snapshot,
                        self._terminal_stop_reason(snapshot),
                        dispatch_count,
                        last_outcome,
                    )
                continue

            dispatch = self._build_dispatch(release, snapshot, request)
            dispatch_count += 1
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

            snapshot = await self._cursor.apply_external_event(
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
                    self._terminal_stop_reason(snapshot),
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

    async def _dispatch_parallel_group(
        self,
        *,
        release: WorkflowRelease,
        group: WorkflowParallelGroupBinding,
        snapshot: ExecutionSnapshot,
        request: RuntimeWorkflowStartRequest,
        remaining_dispatches: int,
        max_dispatches: int,
        max_committed_retry_scan: int,
    ) -> tuple[tuple[ModuleOutcome, ...] | None, int]:
        """Resolve committed branches and concurrently dispatch missing work."""

        resolved: dict[str, ModuleOutcome] = {}
        pending: list[ModuleDispatchRequest] = []
        retry_scan_exhausted: list[str] = []
        for branch_node_id in group.branch_node_ids:
            retry_sequence = 0
            while True:
                if retry_sequence >= max_committed_retry_scan:
                    retry_scan_exhausted.append(branch_node_id)
                    break
                dispatch = self._build_dispatch(
                    release,
                    snapshot,
                    request,
                    node_id=branch_node_id,
                    retry_sequence=retry_sequence,
                )
                committed = self._activity_bridge.get_committed_outcome(
                    dispatch.workflow_execution_id,
                    dispatch.dispatch_id,
                )
                if committed is None:
                    pending.append(dispatch)
                    break
                committed.validate()
                self._validate_outcome(dispatch, committed)
                if (
                    committed.disposition
                    is ModuleOutcomeDisposition.RETRYABLE_FAILURE
                ):
                    retry_sequence += 1
                    continue
                resolved[branch_node_id] = committed
                break

        blocked = self._parallel_blocking_outcomes(group, resolved)
        if blocked:
            return blocked, 0
        if retry_scan_exhausted:
            raise RuntimeError(
                "parallel branch committed retry scan exceeded safety bound: "
                + ", ".join(retry_scan_exhausted)
            )
        if len(pending) > max_dispatches:
            raise ValueError(
                f"max_dispatches cannot admit parallel group {group.group_id!r}; "
                f"at least {len(pending)} dispatches are required"
            )
        if len(pending) > remaining_dispatches:
            return None, 0
        if pending:
            dispatch_slots = asyncio.Semaphore(self._max_parallel_dispatches)

            async def dispatch_one(
                dispatch: ModuleDispatchRequest,
            ) -> ModuleOutcome:
                async with dispatch_slots:
                    return await asyncio.to_thread(
                        self._activity_bridge.dispatch,
                        dispatch,
                    )

            raw_results = await asyncio.gather(
                *(dispatch_one(dispatch) for dispatch in pending),
                return_exceptions=True,
            )
            failures: list[BaseException] = []
            for dispatch, raw_outcome in zip(pending, raw_results, strict=True):
                if isinstance(raw_outcome, BaseException):
                    failures.append(raw_outcome)
                    continue
                if type(raw_outcome) is not ModuleOutcome:
                    raise TypeError("Activity bridge returned an invalid ModuleOutcome")
                outcome = raw_outcome
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
                resolved[dispatch.current_state_id] = outcome
            blocked = self._parallel_blocking_outcomes(group, resolved)
            if failures:
                first_failure = failures[0]
                if not isinstance(first_failure, Exception):
                    raise first_failure
                raise RuntimeError("parallel Module dispatch failed") from first_failure
            if blocked:
                return blocked, len(pending)

        return (
            tuple(resolved[branch_node_id] for branch_node_id in group.branch_node_ids),
            len(pending),
        )

    @staticmethod
    def _build_dispatch(
        release: WorkflowRelease,
        snapshot: ExecutionSnapshot,
        request: RuntimeWorkflowStartRequest,
        *,
        node_id: str | None = None,
        retry_sequence: int = 0,
    ) -> ModuleDispatchRequest:
        selected_node_id = snapshot.current_state if node_id is None else node_id
        node = next(
            (
                candidate
                for candidate in release.nodes
                if candidate.node_id == selected_node_id
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
        identity_fields = (
            snapshot.workflow_execution_id,
            release.release_sha256,
            request.execution_profile_selection_sha256,
            snapshot.current_state,
            str(sequence),
        )
        if node_id is not None or retry_sequence:
            identity_fields = (
                *identity_fields,
                selected_node_id,
                str(retry_sequence),
            )
        digest = hashlib.sha256(
            "\x1f".join(identity_fields).encode("utf-8")
        ).hexdigest()[:24]
        dispatch = ModuleDispatchRequest(
            workflow_execution_id=snapshot.workflow_execution_id,
            workflow_id=release.workflow_id,
            workflow_contract_version=release.workflow_contract_version,
            execution_release_ref=release.execution_release_ref,
            graph_sha256=release.graph_sha256,
            current_state_id=selected_node_id,
            transition_sequence=sequence,
            dispatch_id=f"dispatch_{digest}",
            retry_sequence=retry_sequence,
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
    def _parallel_group_for_state(
        release: WorkflowRelease,
        state_id: str,
    ) -> WorkflowParallelGroupBinding | None:
        groups = tuple(
            group
            for group in release.parallel_groups
            if group.control_node_id == state_id
        )
        if len(groups) > 1:
            raise ValueError("Workflow state owns several parallel groups")
        return None if not groups else groups[0]

    @staticmethod
    def _parallel_branch_can_join(
        group: WorkflowParallelGroupBinding,
        outcome: ModuleOutcome,
    ) -> bool:
        """Return whether one committed branch result can enter the join."""

        return (
            outcome.disposition is ModuleOutcomeDisposition.TRANSITION
            and outcome.target_state_id == group.join_node_id
        )

    @classmethod
    def _parallel_blocking_outcomes(
        cls,
        group: WorkflowParallelGroupBinding,
        resolved: dict[str, ModuleOutcome],
    ) -> tuple[ModuleOutcome, ...]:
        """Return committed non-joinable outcomes in declared branch order."""

        return tuple(
            resolved[branch_node_id]
            for branch_node_id in group.branch_node_ids
            if branch_node_id in resolved
            and resolved[branch_node_id].disposition
            is not ModuleOutcomeDisposition.RETRYABLE_FAILURE
            and not cls._parallel_branch_can_join(
                group,
                resolved[branch_node_id],
            )
        )

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
        expected_domain_terminal = state in graph.terminal_states
        expected_terminal = (
            expected_domain_terminal or snapshot.runtime_status_id == "cancelled"
        )
        if snapshot.terminal is not expected_terminal:
            raise ValueError("durable cursor terminal flag differs from graph state")
        if snapshot.runtime_status_id == "completed" and not expected_domain_terminal:
            raise ValueError("completed Runtime status requires terminal domain state")

    @staticmethod
    def _terminal_stop_reason(
        snapshot: ExecutionSnapshot,
    ) -> DurableExecutionStopReason:
        return (
            DurableExecutionStopReason.CANCELLED
            if snapshot.runtime_status_id == "cancelled"
            else DurableExecutionStopReason.TERMINAL
        )

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
]
