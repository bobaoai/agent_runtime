from __future__ import annotations

import asyncio
from dataclasses import replace
import os
from typing import Any

import pytest


pytest.importorskip("temporalio")

from agent_runtime.contracts.durability_backend_definition import (  # noqa: E402
    DurableBackendAdapter,
)
from agent_runtime.contracts.durability_execution_definition import (  # noqa: E402
    BackendExecutionRef,
    DurableExecutionBinding,
    GraphStateProjection,
    WorkflowGraphProjection,
)
from agent_runtime.contracts.execution_host_definition import (  # noqa: E402
    RuntimeCancellationRequest,
    RuntimeWorkflowStartRequest,
)
from agent_runtime.contracts.registry_release_definition import (  # noqa: E402
    ModuleExecutionPurpose,
)
from agent_runtime.durability.durability_temporal_coordination import (  # noqa: E402
    TemporalDurableCursorWorkflow,
    TemporalWorkflowReleaseBackendAdapter,
)
from agent_runtime.registry.registry_release_registration import (  # noqa: E402
    RuntimeReleaseRegistry,
)
from temporalio.testing import WorkflowEnvironment  # noqa: E402


HASH = "a" * 64
NOW = "2026-08-08T12:00:00Z"


def _binding() -> DurableExecutionBinding:
    return DurableExecutionBinding(
        tenant_id="tenant-alpha",
        cell_id="cell-alpha",
        backend_id="temporal",
        backend_namespace="runtime.production",
        backend_endpoint_ref="temporal-endpoint:alpha",
        backend_persistence_ref="temporal-persistence:alpha",
    )


def _start_payload() -> dict[str, Any]:
    request = RuntimeWorkflowStartRequest(
        workflow_execution_id="wf-exec-001",
        tenant_id="tenant-alpha",
        cell_id="cell-alpha",
        workflow_release_ref="runtime-workflow:workflow-alpha@v1",
        workflow_release_sha256=HASH,
        execution_release_ref="execution-release:workflow-alpha@v1",
        execution_release_sha256=HASH,
        execution_profile_selection_ref="profile-selection:workflow-alpha@v1",
        execution_profile_selection_sha256=HASH,
        runtime_execution_binding_ref="runtime-binding:workflow-alpha@v1",
        runtime_execution_binding_sha256=HASH,
        execution_authorization_binding_ref="authorization:workflow-alpha@v1",
        execution_authorization_binding_sha256=HASH,
        execution_start_admission_ref="start-admission:workflow-alpha@v1",
        execution_start_admission_sha256=HASH,
        execution_input_package_refs=("artifact:workflow-alpha-input",),
        execution_input_package_sha256=HASH,
        idempotency_key="start-alpha",
        recorded_at_utc=NOW,
    )
    states = (
        GraphStateProjection(
            state_id="completed",
            allowed_next_state_ids=(),
        ),
        GraphStateProjection(
            state_id="waiting-review",
            allowed_next_state_ids=("completed",),
        ),
    )
    provisional = WorkflowGraphProjection(
        projection_version="target-test-v1",
        workflow_id="workflow-alpha",
        workflow_contract_version="v1",
        graph_authority_ref=(
            "tests.test_agent_runtime_temporal_target_adapter:TARGET_GRAPH"
        ),
        initial_state="waiting-review",
        terminal_states=("completed",),
        states=states,
        graph_sha256="0" * 64,
    )
    graph = replace(provisional, graph_sha256=provisional.computed_sha256())
    execution = request.as_dict()
    execution.update(
        workflow_id="workflow-alpha",
        workflow_contract_version="v1",
    )
    return {"execution": execution, "graph": graph.to_backend_payload()}


def _payload(*, cancelled: bool) -> dict[str, Any]:
    return {
        "workflow_id": "workflow-alpha",
        "workflow_execution_id": "wf-exec-001",
        "graph_sha256": HASH,
        "start_request_sha256": "b" * 64,
        "current_state": "waiting-review",
        "terminal": cancelled,
        "applied_events": [],
        "runtime_status_id": "cancelled" if cancelled else "running",
        "acknowledged_cancellation_id": (
            "cancellation-alpha" if cancelled else None
        ),
        "acknowledged_cancellation_ref": (
            "cancellation-reason:alpha" if cancelled else None
        ),
    }


class _Handle:
    id = "wf-exec-001"

    def __init__(self) -> None:
        self.cancel_payload: dict[str, Any] | None = None

    async def query(self, _query: Any) -> dict[str, Any]:
        return _payload(cancelled=self.cancel_payload is not None)

    async def execute_update(
        self,
        _update: Any,
        payload: dict[str, Any],
        *,
        id: str,
    ) -> dict[str, Any]:
        assert id == payload["cancellation_request_id"]
        self.cancel_payload = payload
        return _payload(cancelled=True)


class _Client:
    def __init__(self) -> None:
        self.handle = _Handle()

    def get_workflow_handle(self, _execution_id: str) -> _Handle:
        return self.handle


def test_target_temporal_adapter_satisfies_canonical_backend_contract() -> None:
    adapter = TemporalWorkflowReleaseBackendAdapter(
        client=_Client(),  # type: ignore[arg-type]
        binding=_binding(),
        release_registry=RuntimeReleaseRegistry(),
        task_queue="runtime-alpha",
        execution_purpose=ModuleExecutionPurpose.WORKFLOW,
    )

    assert isinstance(adapter, DurableBackendAdapter)


def test_target_temporal_cancellation_is_typed_and_acknowledged() -> None:
    client = _Client()
    adapter = TemporalWorkflowReleaseBackendAdapter(
        client=client,  # type: ignore[arg-type]
        binding=_binding(),
        release_registry=RuntimeReleaseRegistry(),
        task_queue="runtime-alpha",
        execution_purpose=ModuleExecutionPurpose.WORKFLOW,
    )
    execution = BackendExecutionRef(
        backend_id="temporal",
        backend_namespace="runtime.production",
        backend_execution_id="wf-exec-001",
        workflow_execution_id="wf-exec-001",
    )
    request = RuntimeCancellationRequest(
        cancellation_request_id="cancellation-alpha",
        workflow_execution_id="wf-exec-001",
        expected_snapshot_ref="runtime-snapshot:alpha",
        expected_snapshot_sha256=HASH,
        reason_code="operator-requested",
        reason_artifact_ref="cancellation-reason:alpha",
        reason_artifact_sha256=HASH,
        authorization_decision_ref="authorization-decision:alpha",
        authorization_decision_sha256=HASH,
        idempotency_key="cancel-alpha",
        recorded_at_utc=NOW,
    )

    snapshot = asyncio.run(adapter.request_cancellation(execution, request))
    events = asyncio.run(adapter.list_events(execution))

    assert snapshot.current_state == "waiting-review"
    assert snapshot.runtime_status_id == "cancelled"
    assert snapshot.terminal is True
    assert snapshot.acknowledged_cancellation_id == "cancellation-alpha"
    assert events[-1].event_type == "runtime_cancellation"
    assert events[-1].payload_ref == "cancellation-reason:alpha"


def test_cancelled_cursor_rejects_new_events_and_stays_snapshot_valid() -> None:
    cursor = TemporalDurableCursorWorkflow()
    cursor._workflow_id = "workflow-alpha"
    cursor._workflow_execution_id = "wf-exec-001"
    cursor._graph_sha256 = HASH
    cursor._start_request_sha256 = "b" * 64
    cursor._current_state = "waiting-review"
    cursor._terminal_states = {"completed"}
    cursor._allowed_targets = {
        "waiting-review": ("completed",),
        "completed": (),
    }
    cancellation = RuntimeCancellationRequest(
        cancellation_request_id="cancellation-alpha",
        workflow_execution_id="wf-exec-001",
        expected_snapshot_ref="runtime-snapshot:alpha",
        expected_snapshot_sha256=HASH,
        reason_code="operator-requested",
        reason_artifact_ref="cancellation-reason:alpha",
        reason_artifact_sha256=HASH,
        authorization_decision_ref="authorization-decision:alpha",
        authorization_decision_sha256=HASH,
        idempotency_key="cancel-alpha",
        recorded_at_utc=NOW,
    )
    cursor._validate_cancellation(cancellation.as_dict())
    cursor._cancellation_by_id = cancellation.as_dict()
    cursor._runtime_status_id = "cancelled"

    with pytest.raises(
        ValueError,
        match="cancelled Temporal execution cannot advance",
    ):
        cursor._validate_event(
            {
                "event_id": "event-approve-001",
                "event_type": "review_approved",
                "workflow_execution_id": "wf-exec-001",
                "expected_state": "waiting-review",
                "target_state": "completed",
                "evidence_ref": "evidence:approve-001",
            }
        )

    snapshot = cursor.execution_snapshot()
    assert snapshot["current_state"] == "waiting-review"
    assert snapshot["runtime_status_id"] == "cancelled"
    assert snapshot["terminal"] is True
    assert snapshot["acknowledged_cancellation_id"] == "cancellation-alpha"


async def _run_target_temporal_cancellation_integration() -> None:
    environment = await WorkflowEnvironment.start_local(ui=False)
    try:
        binding = replace(_binding(), backend_namespace="default")
        adapter = TemporalWorkflowReleaseBackendAdapter(
            client=environment.client,
            binding=binding,
            release_registry=RuntimeReleaseRegistry(),
            task_queue="runtime-target-adapter",
            execution_purpose=ModuleExecutionPurpose.WORKFLOW,
        )
        execution = BackendExecutionRef(
            backend_id="temporal",
            backend_namespace="default",
            backend_execution_id="wf-exec-001",
            workflow_execution_id="wf-exec-001",
        )
        request = RuntimeCancellationRequest(
            cancellation_request_id="cancellation-alpha",
            workflow_execution_id="wf-exec-001",
            expected_snapshot_ref="runtime-snapshot:alpha",
            expected_snapshot_sha256=HASH,
            reason_code="operator-requested",
            reason_artifact_ref="cancellation-reason:alpha",
            reason_artifact_sha256=HASH,
            authorization_decision_ref="authorization-decision:alpha",
            authorization_decision_sha256=HASH,
            idempotency_key="cancel-alpha",
            recorded_at_utc=NOW,
        )
        async with adapter.build_worker():
            handle = await environment.client.start_workflow(
                TemporalDurableCursorWorkflow.run,
                _start_payload(),
                id="wf-exec-001",
                task_queue="runtime-target-adapter",
            )
            before = await adapter.query(execution)
            cancelled = await adapter.request_cancellation(execution, request)
            replay = await adapter.request_cancellation(execution, request)
            result = await handle.result()

        assert before.current_state == "waiting-review"
        assert before.runtime_status_id == "running"
        assert cancelled == replay
        assert cancelled.current_state == before.current_state
        assert cancelled.runtime_status_id == "cancelled"
        assert result["runtime_status_id"] == "cancelled"
    finally:
        await environment.shutdown()


@pytest.mark.skipif(
    os.environ.get("RUN_TEMPORAL_INTEGRATION") != "1",
    reason="set RUN_TEMPORAL_INTEGRATION=1 to start local Temporal dev server",
)
def test_real_target_temporal_cancellation_is_replay_safe() -> None:
    asyncio.run(_run_target_temporal_cancellation_integration())
