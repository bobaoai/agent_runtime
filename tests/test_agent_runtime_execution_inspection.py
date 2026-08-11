from __future__ import annotations

from agent_runtime.contracts.ledger_record_definition import (
    CommitReceipt,
    ExecutionInputRef,
    ExecutionOutputRef,
    ModelCallRecord,
    RuntimeExecutionTrace,
    UsageEvent,
    WorkflowAttemptRecord,
    WorkflowExecutionRecord,
    WorkflowModuleExecutionVariantRecord,
    WorkflowModuleRunRecord,
    sha256_json,
)
from agent_runtime.inspection import (
    build_runtime_execution_inspection,
    build_workflow_review_bundle,
)


UTC_START = "2026-08-10T12:00:00Z"
UTC_END = "2026-08-10T12:01:00Z"
HASH = "a" * 64


def _trace() -> RuntimeExecutionTrace:
    execution_id = "execution_inspection_001"
    input_ref = "artifact-ref:inspection-source"
    output_ref = "output-ref:inspection-evidence"
    execution = WorkflowExecutionRecord(
        workflow_execution_id=execution_id,
        workflow_id="workflow_inspection",
        workflow_contract_version="v1",
        tenant_id="tenant_inspection",
        cell_id="cell_inspection",
        principal_id="principal_inspection",
        execution_release_ref="workflow-release:inspection@v1",
        graph_sha256=HASH,
        runtime_execution_binding_ref="runtime-binding:inspection@v1",
        runtime_execution_binding_sha256="b" * 64,
        authorization_decision_ref="authorization-decision:inspection@v1",
        authorization_decision_sha256="c" * 64,
        execution_principal_delegation_ref="delegation-ref:inspection@v1",
        execution_principal_delegation_sha256="d" * 64,
        entitlement_snapshot_ref="entitlement-ref:inspection@v1",
        entitlement_snapshot_hash="e" * 64,
        execution_input_package_refs=(input_ref,),
        execution_input_package_sha256="f" * 64,
        recorded_at_utc=UTC_START,
    )
    input_record = ExecutionInputRef(
        execution_input_id="execution_input_inspection_001",
        workflow_execution_id=execution_id,
        input_type_id="canonical_source",
        schema_version="v1",
        input_ref=input_ref,
        input_sha256="1" * 64,
        byte_size=100,
        media_type="text/markdown",
        recorded_at_utc=UTC_START,
        logical_name="canonical_source.md",
    )
    module = WorkflowModuleRunRecord(
        workflow_execution_id=execution_id,
        module_run_id="module_run_inspection_001",
        state_id="produce_evidence",
        module_id="digestion_evidence_producer",
        input_refs=(input_ref,),
        input_closure_sha256=sha256_json([input_ref]),
        recorded_at_utc=UTC_START,
    )
    variant = WorkflowModuleExecutionVariantRecord(
        workflow_execution_id=execution_id,
        module_run_id=module.module_run_id,
        variant_id="variant_inspection_001",
        module_id=module.module_id,
        agent_execution_adapter_id="claude_agent_sdk",
        execution_profile_id="fable_5_max",
        model_id="claude-fable-5",
        reasoning_profile="max",
        prompt_sha256="2" * 64,
        static_module_sha256="3" * 64,
        input_closure_sha256=module.input_closure_sha256,
        entitlement_snapshot_hash=execution.entitlement_snapshot_hash,
        agent_execution_adapter_revision="v1",
        runtime_version="v1",
        tool_policy=("no_tools",),
        context_mode="inline",
        output_schema_sha256="4" * 64,
        timeout_seconds=300,
        max_attempts=1,
        execution_profile_sha256="5" * 64,
        recorded_at_utc=UTC_START,
    )
    attempt = WorkflowAttemptRecord(
        workflow_execution_id=execution_id,
        module_run_id=module.module_run_id,
        variant_id=variant.variant_id,
        attempt_id="attempt_inspection_001",
        parent_attempt_id=None,
        attempt_ordinal=1,
        status="completed",
        period_start_at_utc=UTC_START,
        period_end_at_utc=UTC_END,
        recorded_at_utc=UTC_END,
        trace_id="trace_inspection_001",
        execution_output_refs=(output_ref,),
        failure_class=None,
    )
    model_call = ModelCallRecord(
        model_call_id="model_call_inspection_001",
        workflow_execution_id=execution_id,
        module_run_id=module.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=attempt.attempt_id,
        grant_id="grant_inspection_001",
        resource_id="provider_anthropic",
        action_id="model_invoke",
        agent_execution_adapter_id=variant.agent_execution_adapter_id,
        model_id=variant.model_id,
        status_id="completed",
        recorded_at_utc=UTC_END,
    )
    usage = UsageEvent(
        usage_event_id="usage_inspection_001",
        operation_id=model_call.model_call_id,
        workflow_execution_id=execution_id,
        module_run_id=module.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=attempt.attempt_id,
        grant_id=model_call.grant_id,
        resource_id=model_call.resource_id,
        action_id=model_call.action_id,
        input_tokens=120,
        output_tokens=30,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        estimated_cost_usd=None,
        provider_charge_usd=None,
        recorded_at_utc=UTC_END,
    )
    output = ExecutionOutputRef(
        execution_output_id="execution_output_inspection_001",
        workflow_execution_id=execution_id,
        output_type_id="evidence",
        schema_version="v1",
        output_ref=output_ref,
        output_sha256="6" * 64,
        byte_size=80,
        media_type="application/json",
        recorded_at_utc=UTC_END,
        module_run_id=module.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=attempt.attempt_id,
        logical_name="evidence.json",
    )
    records = (
        execution,
        input_record,
        module,
        variant,
        attempt,
        model_call,
        usage,
        output,
    )
    return RuntimeExecutionTrace(
        workflow_execution_id=execution_id,
        records=records,
        commit_receipts=(
            CommitReceipt(
                workflow_execution_id=execution_id,
                transaction_id="transaction_inspection_001",
                transaction_sha256="7" * 64,
                record_count=len(records),
                committed_outcome_refs=(),
                replayed=False,
            ),
        ),
    )


def test_execution_trace_projects_directly_to_portable_inspector_view() -> None:
    inspection = build_runtime_execution_inspection(_trace())

    # A completed Module Attempt does not establish Workflow terminality.  Only
    # an admitted Checkpoint can carry the durable Workflow status.
    assert inspection["trace"]["workflow"]["status"] == "running"
    assert inspection["trace"]["usage"]["input_tokens"] == 120
    assert inspection["projection_boundary"] == {
        "authority": "agent_runtime_execution_ledger",
        "projection_kind": "rebuildable_read_model",
        "content_included": False,
        "authorization_decision_made": False,
    }
    module = inspection["modules"][0]
    assert module["module_run"]["workflow_node_id"] == "produce_evidence"
    assert module["module_run"]["status"] == "completed"
    assert module["module_run"]["module_release_ref"] is None
    assert module["variants"][0]["execution_profile"]["model_id"] == (
        "claude-fable-5"
    )
    assert module["variants"][0]["execution_profile"]["provider_id"] is None
    assert module["variants"][0]["execution_profile"][
        "agent_execution_adapter_id"
    ] == "claude_agent_sdk"
    assert module["attempts"][0]["input_tokens"] == 120
    assert module["attempts"][0]["tool_calls"][0]["call_kind"] == "model"
    assert module["artifacts"][0]["direction"] == "input"
    assert module["artifacts"][1]["direction"] == "output"

    bundle = build_workflow_review_bundle(inspection)
    assert bundle["workflow_execution_id"] == "execution_inspection_001"


def test_execution_projection_is_rebuilt_without_persisting_a_second_ledger() -> None:
    first = build_runtime_execution_inspection(_trace())
    second = build_runtime_execution_inspection(_trace())

    assert first == second
    assert first["trace"]["record_count"] == 8
    assert [row["record_type"] for row in first["records"]] == [
        "WorkflowExecutionRecord",
        "ExecutionInputRef",
        "WorkflowModuleRunRecord",
        "WorkflowModuleExecutionVariantRecord",
        "WorkflowAttemptRecord",
        "ModelCallRecord",
        "UsageEvent",
        "ExecutionOutputRef",
    ]
