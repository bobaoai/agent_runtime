from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.contracts.execution_host_definition import (
    RuntimeCancellationRequest,
    RuntimeExecutionHandle,
    RuntimeExecutionStatus,
    RuntimeExecutionView,
    RuntimeReconciliationResult,
    RuntimeWorkflowStartRequest,
)


NOW = "2026-08-05T18:00:00Z"
HASH = "1" * 64


def _start_request() -> RuntimeWorkflowStartRequest:
    return RuntimeWorkflowStartRequest(
        workflow_execution_id="workflowexecution_alpha",
        tenant_id="tenant_alpha",
        cell_id="cell_alpha",
        workflow_release_ref="runtime:workflow_release_v1",
        workflow_release_sha256=HASH,
        execution_release_ref="runtime:execution_release_v1",
        execution_release_sha256=HASH,
        execution_profile_selection_ref="runtime:profile_selection_v1",
        execution_profile_selection_sha256=HASH,
        runtime_execution_binding_ref="runtime:execution_binding_v1",
        runtime_execution_binding_sha256=HASH,
        execution_authorization_binding_ref="authorization:binding_v1",
        execution_authorization_binding_sha256=HASH,
        execution_start_admission_ref="runtime:start_admission_v1",
        execution_start_admission_sha256=HASH,
        execution_input_package_refs=("runtime:input_package_v1",),
        execution_input_package_sha256=HASH,
        idempotency_key="start_alpha",
        recorded_at_utc=NOW,
    )


def _handle() -> RuntimeExecutionHandle:
    return RuntimeExecutionHandle(
        workflow_execution_id="workflowexecution_alpha",
        tenant_id="tenant_alpha",
        cell_id="cell_alpha",
        runtime_execution_ref="runtime:execution_alpha",
        workflow_release_ref="runtime:workflow_release_v1",
        workflow_release_sha256=HASH,
        start_receipt_ref="runtime:start_receipt_v1",
        start_receipt_sha256=HASH,
        recorded_at_utc=NOW,
    )


def _view() -> RuntimeExecutionView:
    return RuntimeExecutionView(
        handle=_handle(),
        runtime_status=RuntimeExecutionStatus.RUNNING,
        domain_state_id="state_alpha",
        terminal=False,
        snapshot_ref="runtime:snapshot_v1",
        snapshot_sha256=HASH,
        recorded_at_utc=NOW,
    )


def test_runtime_host_api_records_are_backend_and_platform_neutral() -> None:
    request = _start_request()
    request.validate()
    _handle().validate()
    _view().validate()

    keys = set(request.as_dict())
    assert not any("temporal" in key or "dagster" in key for key in keys)
    assert not any("backend" in key or "task_queue" in key for key in keys)
    assert not any("platform" in key for key in keys)


def test_runtime_host_api_rejects_cross_execution_reconciliation() -> None:
    result = RuntimeReconciliationResult(
        workflow_execution_id="workflowexecution_other",
        disposition_id="consistent",
        execution_view=_view(),
        evidence_refs=("runtime:reconciliation_evidence_v1",),
        recorded_at_utc=NOW,
    )
    with pytest.raises(ValueError, match="crossed Workflow Execution"):
        result.validate()


def test_runtime_cancellation_is_authorized_and_snapshot_bound() -> None:
    request = RuntimeCancellationRequest(
        cancellation_request_id="cancellation_alpha",
        workflow_execution_id="workflowexecution_alpha",
        expected_snapshot_ref="runtime:snapshot_v1",
        expected_snapshot_sha256=HASH,
        reason_code="user_requested",
        reason_artifact_ref="runtime:cancellation_reason_v1",
        reason_artifact_sha256=HASH,
        authorization_decision_ref="authorization:decision_v1",
        authorization_decision_sha256=HASH,
        idempotency_key="cancel_alpha",
        recorded_at_utc=NOW,
    )
    request.validate()

    with pytest.raises(ValueError, match="expected_snapshot_sha256"):
        replace(request, expected_snapshot_sha256="invalid").validate()


@pytest.mark.parametrize(
    "recorded_at_utc",
    (
        "2026-08-08T12:00:00+05:30",
        "2026-08-08T12:00:00+00:00",
        "2026-08-08T12:00:00",
    ),
)
def test_host_contracts_require_canonical_z_utc(recorded_at_utc: str) -> None:
    with pytest.raises(ValueError, match="ending in Z"):
        replace(_start_request(), recorded_at_utc=recorded_at_utc).validate()
