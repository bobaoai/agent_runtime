from __future__ import annotations

from dataclasses import replace

import pytest

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
from agent_runtime.contracts.registry_release_definition import (
    WorkflowEdge,
    WorkflowNodeBinding,
    WorkflowNodeKind,
    WorkflowRelease,
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


def _trace_with(base: RuntimeExecutionTrace, records: tuple) -> RuntimeExecutionTrace:
    """Rebuild one trace around a modified record tuple."""

    receipt = base.commit_receipts[0]
    return RuntimeExecutionTrace(
        workflow_execution_id=base.workflow_execution_id,
        records=records,
        commit_receipts=(
            replace(receipt, record_count=len(records)),
        ),
    )


def _record(base: RuntimeExecutionTrace, record_type: type):
    return next(row for row in base.records if type(row) is record_type)


def test_retry_success_supersedes_the_failed_attempt_status() -> None:
    base = _trace()
    completed = _record(base, WorkflowAttemptRecord)
    failed = replace(
        completed,
        attempt_id="attempt_inspection_000",
        attempt_ordinal=1,
        status="failed",
        failure_class="provider",
        execution_output_refs=(),
    )
    retried = replace(
        completed,
        parent_attempt_id=failed.attempt_id,
        attempt_ordinal=2,
    )
    records = tuple(
        row for row in base.records if type(row) is not WorkflowAttemptRecord
    )
    index = base.records.index(completed)
    records = records[: index] + (failed, retried) + records[index:]

    inspection = build_runtime_execution_inspection(_trace_with(base, records))

    module = inspection["modules"][0]
    assert module["module_run"]["status"] == "completed"
    assert inspection["trace"]["workflow"]["status"] == "running"
    assert [row["status"] for row in module["attempts"]] == [
        "failed",
        "completed",
    ]


def test_projection_rejects_an_attempt_bound_to_a_foreign_variant() -> None:
    base = _trace()
    module = _record(base, WorkflowModuleRunRecord)
    variant = _record(base, WorkflowModuleExecutionVariantRecord)
    attempt = _record(base, WorkflowAttemptRecord)
    foreign_module = replace(
        module,
        module_run_id="module_run_inspection_002",
        state_id="verify_evidence",
    )
    cross_attempt = replace(
        attempt,
        attempt_id="attempt_inspection_002",
        module_run_id=foreign_module.module_run_id,
        variant_id=variant.variant_id,
    )
    records = (*base.records, foreign_module, cross_attempt)

    with pytest.raises(ValueError, match="Attempt outside its Variant lineage"):
        build_runtime_execution_inspection(_trace_with(base, records))


@pytest.mark.parametrize(
    "record_type",
    (UsageEvent, ModelCallRecord, ExecutionOutputRef),
)
def test_projection_rejects_records_off_their_attempt_lineage(
    record_type: type,
) -> None:
    base = _trace()
    target = _record(base, record_type)
    detached = replace(target, variant_id="variant_inspection_other")
    records = tuple(
        detached if row is target else row for row in base.records
    )

    with pytest.raises(ValueError, match="outside its Attempt lineage"):
        build_runtime_execution_inspection(_trace_with(base, records))


def test_projection_rejects_usage_without_its_call_record() -> None:
    base = _trace()
    usage = _record(base, UsageEvent)
    detached = replace(usage, operation_id="model_call_missing_001")
    records = tuple(detached if row is usage else row for row in base.records)

    with pytest.raises(ValueError, match="UsageEvent without its call record"):
        build_runtime_execution_inspection(_trace_with(base, records))


def test_projection_rejects_a_workflow_release_for_another_workflow() -> None:
    foreign_release = WorkflowRelease.build(
        workflow_id="workflow_other",
        workflow_version="1.0.0",
        workflow_contract_version="v1",
        release_ref="runtime-workflow:workflow_other@1",
        owner_contract_ref="design-doc:inspection@1",
        owner_contract_sha256="7" * 64,
        graph_ref=(
            "python:tests.test_agent_runtime_execution_inspection._graph"
        ),
        graph_sha256="c" * 64,
        initial_node_id="produce_evidence",
        nodes=(
            WorkflowNodeBinding(
                node_id="produce_evidence",
                node_kind=WorkflowNodeKind.MODULE,
                module_release_ref="runtime-module:inspection@1",
                module_release_sha256="9" * 64,
                input_mapping_ref="input-map:inspection@1",
                input_mapping_sha256="8" * 64,
            ),
        ),
        edges=(
            WorkflowEdge(
                source_node_id="produce_evidence",
                outcome_id="evidence_produced",
                target_node_id=None,
                terminal=True,
            ),
        ),
        authorization_manifest_ref="authorization-manifest:inspection@1",
        authorization_manifest_sha256="a" * 64,
        execution_release_ref="execution-release:inspection@1",
        execution_release_sha256="b" * 64,
    )

    with pytest.raises(ValueError, match="does not match the execution"):
        build_runtime_execution_inspection(
            _trace(),
            workflow_release=foreign_release,
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
