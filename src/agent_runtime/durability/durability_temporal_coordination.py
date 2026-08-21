"""Generic Temporal durable cursor for registered Agent Runtime graphs.

The workflow code is deliberately domain-blind.  It receives a hash-bound graph
projection compiled from ``WorkflowRuntimeRegistration``, persists the current
state, and applies evidence-bearing external events through acknowledged
Temporal Updates.  Domain drivers execute roles and decide which legal edge to
request; Temporal never invents a role, verdict, loop, or terminal state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from typing import Any

from temporalio import workflow
from temporalio.client import Client
from temporalio.common import (
    WorkflowIDConflictPolicy,
    WorkflowIDReusePolicy,
)
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from .durability_backend_registration import TEMPORAL_DESCRIPTOR
from ..contracts.registry_release_definition import ModuleExecutionPurpose
from ..registry.registry_graph_projection import project_workflow_release_graph
from ..contracts.execution_host_definition import (
    RuntimeCancellationRequest,
    RuntimeWorkflowStartRequest,
)
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from ..contracts.durability_execution_definition import (
    BackendEvent,
    BackendExecutionRef,
    DurableExecutionBinding,
    ExecutionSnapshot,
    ExternalEvent,
    WorkflowGraphProjection,
    assert_ref_only_backend_payload,
)


TEMPORAL_DURABLE_WORKFLOW_NAME = "agent_runtime_durable_cursor_v1"
TEMPORAL_DURABLE_UPDATE_NAME = "apply_external_event"
TEMPORAL_DURABLE_CANCELLATION_UPDATE_NAME = "request_cancellation"
TEMPORAL_DURABLE_QUERY_NAME = "execution_snapshot"


_TARGET_EXECUTION_CONTEXT_KEYS = {
    "workflow_id",
    "workflow_contract_version",
}


def _canonical_payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _validated_temporal_start_payload(
    start_payload: Mapping[str, Any],
) -> tuple[Mapping[str, Any], WorkflowGraphProjection, str]:
    """Validate the complete allowlisted history payload and return its identity."""

    if set(start_payload) != {"execution", "graph"}:
        raise ValueError("Temporal start payload has an invalid shape")
    execution = start_payload["execution"]
    graph_payload = start_payload["graph"]
    if not isinstance(execution, Mapping) or not isinstance(graph_payload, Mapping):
        raise ValueError("Temporal start payload members must be mappings")
    assert_ref_only_backend_payload(start_payload)
    graph = WorkflowGraphProjection.from_backend_payload(graph_payload)

    if not _TARGET_EXECUTION_CONTEXT_KEYS.issubset(execution):
        raise ValueError("target Temporal execution lacks workflow context")
    request_payload = {
        key: value
        for key, value in execution.items()
        if key not in _TARGET_EXECUTION_CONTEXT_KEYS
    }
    request = RuntimeWorkflowStartRequest.from_dict(request_payload)
    if request.workflow_execution_id != execution["workflow_execution_id"]:
        raise ValueError("target Temporal execution identity is inconsistent")

    if execution["workflow_id"] != graph.workflow_id:
        raise ValueError("Temporal execution does not match projected workflow")
    if execution["workflow_contract_version"] != graph.workflow_contract_version:
        raise ValueError("Temporal execution does not match projected contract")
    return execution, graph, _canonical_payload_sha256(start_payload)


def _external_event_from_payload(payload: Mapping[str, Any]) -> ExternalEvent:
    expected_keys = {
        "event_id",
        "event_type",
        "workflow_execution_id",
        "expected_state",
        "target_state",
        "evidence_ref",
    }
    if set(payload) != expected_keys:
        raise ValueError("external event has an invalid shape")
    event = ExternalEvent(
        event_id=str(payload["event_id"]),
        event_type=str(payload["event_type"]),
        workflow_execution_id=str(payload["workflow_execution_id"]),
        expected_state=str(payload["expected_state"]),
        target_state=str(payload["target_state"]),
        evidence_ref=str(payload["evidence_ref"]),
    )
    event.validate()
    return event


@workflow.defn(name=TEMPORAL_DURABLE_WORKFLOW_NAME)
class TemporalDurableCursorWorkflow:
    """Persist and validate the cursor for any registered domain graph."""

    def __init__(self) -> None:
        self._workflow_id = "unbound"
        self._workflow_execution_id = "unbound"
        self._graph_sha256 = "0" * 64
        self._start_request_sha256 = "0" * 64
        self._current_state = "unbound"
        self._terminal_states: set[str] = set()
        self._allowed_targets: dict[str, tuple[str, ...]] = {}
        self._events: list[dict[str, str]] = []
        self._events_by_id: dict[str, dict[str, str]] = {}
        self._runtime_status_id = "running"
        self._cancellation_by_id: dict[str, Any] | None = None

    @workflow.run
    async def run(self, start_payload: dict[str, Any]) -> dict[str, Any]:
        """Bind one immutable projection and wait until its cursor is terminal."""

        execution, graph, start_request_sha256 = _validated_temporal_start_payload(
            start_payload
        )

        self._workflow_id = graph.workflow_id
        self._workflow_execution_id = str(execution["workflow_execution_id"])
        self._graph_sha256 = graph.graph_sha256
        self._start_request_sha256 = start_request_sha256
        self._current_state = graph.initial_state
        self._terminal_states = set(graph.terminal_states)
        self._allowed_targets = {
            state.state_id: state.allowed_next_state_ids for state in graph.states
        }
        await workflow.wait_condition(
            lambda: (
                self._current_state in self._terminal_states
                or self._runtime_status_id == "cancelled"
            )
        )
        return self.execution_snapshot()

    def _validate_event(self, event_payload: Mapping[str, Any]) -> ExternalEvent:
        event = _external_event_from_payload(event_payload)
        if event.workflow_execution_id != self._workflow_execution_id:
            raise PermissionError("Temporal event crossed workflow execution")
        prior = self._events_by_id.get(event.event_id)
        if prior is not None:
            if prior != event.to_backend_payload():
                raise ValueError("Temporal event id was reused with different content")
            return event
        if self._runtime_status_id == "cancelled":
            raise ValueError("cancelled Temporal execution cannot advance")
        if self._current_state in self._terminal_states:
            raise ValueError("terminal Temporal execution cannot advance")
        if event.expected_state != self._current_state:
            raise ValueError("Temporal event expected_state is stale")
        if event.target_state not in self._allowed_targets[self._current_state]:
            raise ValueError("Temporal event requests an illegal graph transition")
        return event

    @workflow.update(name=TEMPORAL_DURABLE_UPDATE_NAME)
    def apply_external_event(self, event_payload: dict[str, str]) -> dict[str, Any]:
        """Apply one idempotent transition and acknowledge its resulting cursor."""

        event = self._validate_event(event_payload)
        normalized = event.to_backend_payload()
        if event.event_id in self._events_by_id:
            return self.execution_snapshot()
        self._events.append(normalized)
        self._events_by_id[event.event_id] = normalized
        self._current_state = event.target_state
        return self.execution_snapshot()

    @apply_external_event.validator
    def validate_external_event(self, event_payload: dict[str, str]) -> None:
        """Reject invalid Updates before they are admitted to workflow history."""

        self._validate_event(event_payload)

    def _validate_cancellation(
        self,
        request_payload: Mapping[str, Any],
    ) -> RuntimeCancellationRequest:
        request = RuntimeCancellationRequest.from_dict(dict(request_payload))
        if request.workflow_execution_id != self._workflow_execution_id:
            raise PermissionError("Temporal cancellation crossed workflow execution")
        if self._current_state in self._terminal_states:
            raise ValueError("completed Temporal execution cannot be cancelled")
        if self._cancellation_by_id is not None:
            if self._cancellation_by_id != request.as_dict():
                raise ValueError(
                    "Temporal cancellation id was reused with different content"
                )
        return request

    @workflow.update(name=TEMPORAL_DURABLE_CANCELLATION_UPDATE_NAME)
    def request_cancellation(
        self,
        request_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Acknowledge one typed cancellation without changing domain state."""

        request = self._validate_cancellation(request_payload)
        if self._cancellation_by_id is None:
            self._cancellation_by_id = request.as_dict()
            self._runtime_status_id = "cancelled"
        return self.execution_snapshot()

    @request_cancellation.validator
    def validate_cancellation(self, request_payload: dict[str, Any]) -> None:
        """Reject invalid cancellation Updates before history admission."""

        self._validate_cancellation(request_payload)

    @workflow.query(name=TEMPORAL_DURABLE_QUERY_NAME)
    def execution_snapshot(self) -> dict[str, Any]:
        """Return a content-free snapshot for operators and local drivers."""

        domain_terminal = self._current_state in self._terminal_states
        runtime_status_id = (
            "completed" if domain_terminal else self._runtime_status_id
        )
        return {
            "workflow_id": self._workflow_id,
            "workflow_execution_id": self._workflow_execution_id,
            "graph_sha256": self._graph_sha256,
            "start_request_sha256": self._start_request_sha256,
            "current_state": self._current_state,
            "terminal": domain_terminal or runtime_status_id == "cancelled",
            "applied_events": list(self._events),
            "runtime_status_id": runtime_status_id,
            "acknowledged_cancellation_id": (
                None
                if self._cancellation_by_id is None
                else self._cancellation_by_id["cancellation_request_id"]
            ),
            "acknowledged_cancellation_ref": (
                None
                if self._cancellation_by_id is None
                else self._cancellation_by_id["reason_artifact_ref"]
            ),
        }


@dataclass(frozen=True)
class TemporalWorkflowReleaseBackendAdapter:
    """Temporal cursor adapter backed by the target Runtime Release Registry."""

    client: Client
    binding: DurableExecutionBinding
    release_registry: RuntimeReleaseRegistry
    task_queue: str
    execution_purpose: ModuleExecutionPurpose

    descriptor = TEMPORAL_DESCRIPTOR

    def __post_init__(self) -> None:
        self.binding.validate()
        if self.binding.backend_id != "temporal":
            raise ValueError("Temporal adapter requires a Temporal Cell binding")
        if not self.task_queue or any(
            character.isspace() for character in self.task_queue
        ):
            raise ValueError("Temporal adapter requires a bounded task queue")
        if type(self.execution_purpose) is not ModuleExecutionPurpose:
            raise ValueError("execution_purpose must be ModuleExecutionPurpose")

    def build_worker(self) -> Worker:
        """Build a worker containing only the domain-blind durable cursor."""

        return Worker(
            self.client,
            task_queue=self.task_queue,
            workflows=[TemporalDurableCursorWorkflow],
        )

    def _validate_execution(self, execution: BackendExecutionRef) -> None:
        if execution.backend_id != "temporal":
            raise PermissionError("foreign backend execution ref")
        if execution.backend_namespace != self.binding.backend_namespace:
            raise PermissionError("Temporal execution crossed Cell namespace")

    async def start(
        self,
        request: RuntimeWorkflowStartRequest,
    ) -> BackendExecutionRef:
        """Start one exact admitted target Workflow Release idempotently."""

        request.validate()
        if (
            request.tenant_id != self.binding.tenant_id
            or request.cell_id != self.binding.cell_id
        ):
            raise PermissionError("Temporal start request crossed Cell binding")
        release = self.release_registry.get_workflow(
            request.workflow_release_ref,
            request.workflow_release_sha256,
        )
        self.release_registry.assert_workflow_execution_allowed(
            release,
            self.execution_purpose,
        )
        if (
            request.execution_release_ref != release.execution_release_ref
            or request.execution_release_sha256
            != release.execution_release_sha256
        ):
            raise PermissionError("Temporal start crossed execution release")
        graph = project_workflow_release_graph(release)
        execution_payload = request.as_dict()
        execution_payload.update(
            workflow_id=release.workflow_id,
            workflow_contract_version=release.workflow_contract_version,
        )
        start_payload = {
            "execution": execution_payload,
            "graph": graph.to_backend_payload(),
        }
        assert_ref_only_backend_payload(start_payload)
        start_request_sha256 = _canonical_payload_sha256(start_payload)
        try:
            handle = await self.client.start_workflow(
                TemporalDurableCursorWorkflow.run,
                start_payload,
                id=request.workflow_execution_id,
                task_queue=self.task_queue,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            )
        except WorkflowAlreadyStartedError:
            handle = self.client.get_workflow_handle(
                request.workflow_execution_id
            )
        execution = BackendExecutionRef(
            backend_id="temporal",
            backend_namespace=self.binding.backend_namespace,
            backend_execution_id=handle.id,
            workflow_execution_id=request.workflow_execution_id,
        )
        snapshot = await self.query(execution)
        if (
            snapshot.workflow_id != release.workflow_id
            or snapshot.graph_sha256 != graph.graph_sha256
            or snapshot.start_request_sha256 != start_request_sha256
        ):
            raise RuntimeError("Temporal workflow id collides with another start request")
        return execution

    async def apply_external_event(
        self,
        execution: BackendExecutionRef,
        event: ExternalEvent,
    ) -> ExecutionSnapshot:
        """Apply one evidence-bearing target-graph transition."""

        self._validate_execution(execution)
        event.validate()
        if event.workflow_execution_id != execution.workflow_execution_id:
            raise PermissionError("Temporal event crossed local workflow identity")
        handle = self.client.get_workflow_handle(execution.backend_execution_id)
        raw = await handle.execute_update(
            TemporalDurableCursorWorkflow.apply_external_event,
            event.to_backend_payload(),
            id=event.event_id,
        )
        return _snapshot_from_payload(execution, raw)

    async def signal(
        self,
        execution: BackendExecutionRef,
        event: ExternalEvent,
    ) -> ExecutionSnapshot:
        """Compatibility alias for predecessor coordinator callers."""

        return await self.apply_external_event(execution, event)

    async def query(self, execution: BackendExecutionRef) -> ExecutionSnapshot:
        """Query the current target-release cursor."""

        self._validate_execution(execution)
        handle = self.client.get_workflow_handle(execution.backend_execution_id)
        raw = await handle.query(
            TemporalDurableCursorWorkflow.execution_snapshot
        )
        return _snapshot_from_payload(execution, raw)

    async def request_cancellation(
        self,
        execution: BackendExecutionRef,
        request: RuntimeCancellationRequest,
    ) -> ExecutionSnapshot:
        """Apply one typed, acknowledged Runtime cancellation Update."""

        self._validate_execution(execution)
        request.validate()
        if request.workflow_execution_id != execution.workflow_execution_id:
            raise PermissionError("Temporal cancellation crossed local execution")
        handle = self.client.get_workflow_handle(execution.backend_execution_id)
        raw = await handle.execute_update(
            TemporalDurableCursorWorkflow.request_cancellation,
            request.as_dict(),
            id=request.cancellation_request_id,
        )
        return _snapshot_from_payload(execution, raw)

    async def recover(self, execution: BackendExecutionRef) -> ExecutionSnapshot:
        """Recover the server-authoritative cursor after host re-entry."""

        return await self.query(execution)

    async def list_events(
        self,
        execution: BackendExecutionRef,
    ) -> Sequence[BackendEvent]:
        """Map target cursor commands into local Runtime event identity."""

        snapshot = await self.query(execution)
        events = [
            BackendEvent(
                backend_id="temporal",
                backend_execution_id=execution.backend_execution_id,
                workflow_execution_id=execution.workflow_execution_id,
                event_type=event.event_type,
                payload_ref=event.evidence_ref,
            )
            for event in snapshot.applied_events
        ]
        if snapshot.acknowledged_cancellation_id is not None:
            events.append(
                BackendEvent(
                    backend_id="temporal",
                    backend_execution_id=execution.backend_execution_id,
                    workflow_execution_id=execution.workflow_execution_id,
                    event_type="runtime_cancellation",
                    payload_ref=snapshot.acknowledged_cancellation_ref,
                )
            )
        return tuple(events)


def _snapshot_from_payload(
    execution: BackendExecutionRef,
    payload: Mapping[str, Any],
) -> ExecutionSnapshot:
    expected_keys = {
        "workflow_id",
        "workflow_execution_id",
        "graph_sha256",
        "start_request_sha256",
        "current_state",
        "terminal",
        "applied_events",
        "runtime_status_id",
        "acknowledged_cancellation_id",
        "acknowledged_cancellation_ref",
    }
    if set(payload) != expected_keys:
        raise ValueError("Temporal execution snapshot has an invalid shape")
    events = tuple(
        _external_event_from_payload(row) for row in payload["applied_events"]
    )
    snapshot = ExecutionSnapshot(
        backend_id="temporal",
        backend_execution_id=execution.backend_execution_id,
        workflow_execution_id=str(payload["workflow_execution_id"]),
        workflow_id=str(payload["workflow_id"]),
        graph_sha256=str(payload["graph_sha256"]),
        start_request_sha256=str(payload["start_request_sha256"]),
        current_state=str(payload["current_state"]),
        terminal=bool(payload["terminal"]),
        applied_events=events,
        runtime_status_id=str(payload["runtime_status_id"]),
        acknowledged_cancellation_id=(
            None
            if payload["acknowledged_cancellation_id"] is None
            else str(payload["acknowledged_cancellation_id"])
        ),
        acknowledged_cancellation_ref=(
            None
            if payload["acknowledged_cancellation_ref"] is None
            else str(payload["acknowledged_cancellation_ref"])
        ),
    )
    snapshot.validate()
    if snapshot.workflow_execution_id != execution.workflow_execution_id:
        raise PermissionError("Temporal snapshot crossed local workflow identity")
    return snapshot
