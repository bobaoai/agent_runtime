from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.contracts.ledger_record_definition import (
    AttemptOrphanedRecord,
    CheckpointRecord,
    CommitReceipt,
    ExecutionInputRef,
    ExecutionOutputRef,
    LegacyModuleCapabilityGrant,
    ModelCallRecord,
    RuntimeExecutionTrace,
    UsageEvent,
    WorkflowAttemptRecord,
    WorkflowAttemptStartedRecord,
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
from agent_runtime.contracts.registry_workflow_definition import (
    ModuleOutcome,
    ModuleOutcomeDisposition,
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
        prompt_envelope_ref="cell-artifact:prompt_inspection_001",
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
        "observed_at_utc": None,
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
    assert module["variants"][0]["prompt_envelope_ref"] == (
        "cell-artifact:prompt_inspection_001"
    )
    assert module["attempts"][0]["input_tokens"] == 120
    assert module["attempts"][0]["tool_calls"][0]["call_kind"] == "model"
    assert module["artifacts"][0]["direction"] == "input"
    assert module["artifacts"][1]["direction"] == "output"

    bundle = build_workflow_review_bundle(inspection)
    assert bundle["workflow_execution_id"] == "execution_inspection_001"


def test_started_attempt_is_visible_and_expiry_requires_explicit_observation() -> None:
    base = _trace()
    module = _record(base, WorkflowModuleRunRecord)
    variant = _record(base, WorkflowModuleExecutionVariantRecord)
    terminal = _record(base, WorkflowAttemptRecord)
    start = WorkflowAttemptStartedRecord(
        workflow_execution_id=base.workflow_execution_id,
        dispatch_id="dispatch_inspection_001",
        module_run_id=module.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=terminal.attempt_id,
        parent_attempt_id=None,
        attempt_ordinal=1,
        trace_id=terminal.trace_id,
        request_sha256="8" * 64,
        claim_token_hash="9" * 64,
        input_closure_sha256=module.input_closure_sha256,
        execution_profile_sha256=variant.execution_profile_sha256,
        entitlement_snapshot_hash=variant.entitlement_snapshot_hash,
        timeout_seconds=variant.timeout_seconds,
        recorded_at_utc=UTC_START,
    )
    removed_types = (
        WorkflowAttemptRecord,
        ModelCallRecord,
        UsageEvent,
        ExecutionOutputRef,
    )
    records = tuple(row for row in base.records if not isinstance(row, removed_types))
    records = (*records, start)

    running = build_runtime_execution_inspection(_trace_with(base, records))
    expired = build_runtime_execution_inspection(
        _trace_with(base, records),
        observed_at_utc="2026-08-10T12:06:00Z",
    )

    running_module = running["modules"][0]
    assert running_module["module_run"]["status"] == "running"
    assert running_module["attempt_starts"][0]["lease_state"] == "unresolved"
    assert running_module["attempts"] == []
    assert (
        len(
            [
                row
                for row in running_module["attempt_starts"]
                if row["terminal_status"] is None
            ]
        )
        == 1
    )
    assert "incomplete_attempts" not in running_module
    assert running["trace"]["workflow"]["recovery_required"] is False
    assert running["trace"]["workflow"]["modules_requiring_recovery"] == []
    assert expired["trace"]["workflow"]["status"] == "recovery_required"
    assert expired["trace"]["workflow"]["recovery_required"] is True
    assert expired["trace"]["workflow"]["modules_requiring_recovery"] == [
        "module_run_inspection_001"
    ]
    assert expired["modules"][0]["module_run"]["status"] == "recovery_required"
    assert expired["modules"][0]["attempt_starts"][0]["lease_state"] == "expired"
    assert expired["modules"][0]["attempt_starts"][0]["deadline_at_utc"] == (
        "2026-08-10T12:05:00Z"
    )


def test_execution_projection_includes_derived_module_inputs() -> None:
    base = _trace()
    module = _record(base, WorkflowModuleRunRecord)
    variant = _record(base, WorkflowModuleExecutionVariantRecord)
    derived = ExecutionOutputRef(
        execution_output_id="execution_output_inspection_context_001",
        workflow_execution_id=base.workflow_execution_id,
        output_type_id="task_prompt_context",
        schema_version="v1",
        output_ref="output-ref:inspection-context",
        output_sha256="8" * 64,
        byte_size=40,
        media_type="application/json",
        recorded_at_utc=UTC_START,
        logical_name="task_prompt_context",
        source_artifact_refs=("artifact-ref:inspection-source",),
    )
    module_with_context = replace(
        module,
        input_refs=(*module.input_refs, derived.output_ref),
        input_closure_sha256=sha256_json(
            [*module.input_refs, derived.output_ref]
        ),
    )
    variant_with_context = replace(
        variant,
        input_closure_sha256=module_with_context.input_closure_sha256,
    )
    records = []
    for row in base.records:
        if row is module:
            records.extend((derived, module_with_context))
        elif row is variant:
            records.append(variant_with_context)
        else:
            records.append(row)

    inspection = build_runtime_execution_inspection(
        _trace_with(base, tuple(records))
    )

    inputs = [
        row
        for row in inspection["modules"][0]["artifacts"]
        if row["direction"] == "input"
    ]
    assert [row["artifact_ref"] for row in inputs] == [
        "artifact-ref:inspection-source",
        derived.output_ref,
    ]


def test_execution_projection_accepts_deterministic_workflow_outcome() -> None:
    base = _trace()
    outcome = ModuleOutcome.build(
        dispatch_id="dispatch_inspection_001",
        workflow_execution_id=base.workflow_execution_id,
        expected_state_id="admit_source",
        disposition=ModuleOutcomeDisposition.TRANSITION,
        target_state_id="produce_evidence",
        module_run_id=None,
        outcome_ref="outcome-ref:inspection-admission",
    )

    inspection = build_runtime_execution_inspection(
        _trace_with(base, (*base.records, outcome))
    )

    assert inspection["trace"]["workflow"]["status"] == "running"
    assert inspection["records"][-1]["record_type"] == "ModuleOutcome"


def test_execution_projection_serializes_committed_legacy_grant() -> None:
    base = _trace()
    attempt = _record(base, WorkflowAttemptRecord)
    model_call = _record(base, ModelCallRecord)
    execution = _record(base, WorkflowExecutionRecord)
    grant = LegacyModuleCapabilityGrant(
        grant_id=model_call.grant_id,
        workflow_execution_id=base.workflow_execution_id,
        module_run_id=attempt.module_run_id,
        variant_id=attempt.variant_id,
        attempt_id=attempt.attempt_id,
        capability_id="model_execute",
        resource_id=model_call.resource_id,
        action_id=model_call.action_id,
        entitlement_snapshot_hash=execution.entitlement_snapshot_hash,
        idempotency_key="grant_inspection_idempotency_001",
        expires_after_seconds=300,
        recorded_at_utc=UTC_START,
    )
    model_call_index = base.records.index(model_call)
    records = (
        *base.records[:model_call_index],
        grant,
        *base.records[model_call_index:],
    )

    inspection = build_runtime_execution_inspection(_trace_with(base, records))

    assert inspection["records"][model_call_index]["record_type"] == (
        "LegacyModuleCapabilityGrant"
    )


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


def _start_for(
    base: RuntimeExecutionTrace,
    variant: WorkflowModuleExecutionVariantRecord,
    attempt_id: str,
    *,
    attempt_ordinal: int = 1,
    trace_id: str = "trace_inspection_start",
) -> WorkflowAttemptStartedRecord:
    module = _record(base, WorkflowModuleRunRecord)
    return WorkflowAttemptStartedRecord(
        workflow_execution_id=base.workflow_execution_id,
        dispatch_id="dispatch_inspection_001",
        module_run_id=variant.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=attempt_id,
        parent_attempt_id=None,
        attempt_ordinal=attempt_ordinal,
        trace_id=trace_id,
        request_sha256="8" * 64,
        claim_token_hash="9" * 64,
        input_closure_sha256=module.input_closure_sha256,
        execution_profile_sha256=variant.execution_profile_sha256,
        entitlement_snapshot_hash=variant.entitlement_snapshot_hash,
        timeout_seconds=variant.timeout_seconds,
        recorded_at_utc=UTC_START,
    )


def test_projection_rejects_a_start_outside_its_variant_lineage() -> None:
    base = _trace()
    variant = _record(base, WorkflowModuleExecutionVariantRecord)
    ghost = _start_for(
        base,
        replace(variant, variant_id="variant_inspection_ghost"),
        "attempt_inspection_ghost",
    )
    records = (*base.records, ghost)

    with pytest.raises(
        ValueError, match="Attempt start outside its Variant lineage"
    ):
        build_runtime_execution_inspection(_trace_with(base, records))


def test_projection_rejects_a_terminal_attempt_off_its_start_lineage() -> None:
    base = _trace()
    variant = _record(base, WorkflowModuleExecutionVariantRecord)
    terminal = _record(base, WorkflowAttemptRecord)
    drifted_start = _start_for(
        base,
        variant,
        terminal.attempt_id,
        attempt_ordinal=terminal.attempt_ordinal + 1,
        trace_id=terminal.trace_id,
    )
    records = (*base.records, drifted_start)

    with pytest.raises(
        ValueError, match="terminal Attempt outside its start lineage"
    ):
        build_runtime_execution_inspection(_trace_with(base, records))


def test_projection_rejects_an_orphan_disposition_without_its_start() -> None:
    base = _trace()
    terminal = _record(base, WorkflowAttemptRecord)
    orphaned = AttemptOrphanedRecord(
        orphaned_record_id="attempt_orphaned_inspection_001",
        workflow_execution_id=base.workflow_execution_id,
        dispatch_id="dispatch_inspection_001",
        module_run_id=terminal.module_run_id,
        variant_id=terminal.variant_id,
        attempt_id=terminal.attempt_id,
        reason_code="attempt_lease_expired",
        context_disposition_id="invalidate",
        recorded_at_utc=UTC_END,
    )
    records = (*base.records, orphaned)

    with pytest.raises(
        ValueError, match="orphan disposition outside its Attempt lineage"
    ):
        build_runtime_execution_inspection(_trace_with(base, records))


def test_one_expired_variant_lease_is_not_masked_by_another_active_variant() -> None:
    base = _trace()
    variant = _record(base, WorkflowModuleExecutionVariantRecord)
    slow_variant = replace(
        variant,
        variant_id="variant_inspection_002",
        timeout_seconds=3600,
    )
    fast_start = _start_for(
        base, variant, "attempt_inspection_001", trace_id="trace_inspection_001"
    )
    slow_start = _start_for(
        base,
        slow_variant,
        "attempt_inspection_002",
        trace_id="trace_inspection_002",
    )
    removed_types = (
        WorkflowAttemptRecord,
        ModelCallRecord,
        UsageEvent,
        ExecutionOutputRef,
    )
    records = tuple(
        row for row in base.records if not isinstance(row, removed_types)
    )
    records = (*records, slow_variant, fast_start, slow_start)

    # Observed between the two deadlines: the 300s lease is expired while the
    # 3600s lease is still active.
    projection = build_runtime_execution_inspection(
        _trace_with(base, records),
        observed_at_utc="2026-08-10T12:06:00Z",
    )

    module = projection["modules"][0]
    lease_states = {
        row["attempt_id"]: row["lease_state"]
        for row in module["attempt_starts"]
    }
    assert lease_states == {
        "attempt_inspection_001": "expired",
        "attempt_inspection_002": "active",
    }
    assert module["module_run"]["status"] == "recovery_required"
    assert projection["trace"]["workflow"]["status"] == "recovery_required"


@pytest.mark.parametrize(
    "observed_at_utc",
    [
        "2026-08-10 12:06:00Z",
        "2026-08-10T12:06Z",
        "2026-08-10T12:06:00+00:00",
    ],
)
def test_projection_rejects_non_canonical_observation_instants(
    observed_at_utc: str,
) -> None:
    with pytest.raises(ValueError, match="observed_at_utc"):
        build_runtime_execution_inspection(
            _trace(), observed_at_utc=observed_at_utc
        )


def test_committed_checkpoint_status_never_masks_the_recovery_signal() -> None:
    base = _trace()
    variant = _record(base, WorkflowModuleExecutionVariantRecord)
    module = _record(base, WorkflowModuleRunRecord)
    start = _start_for(base, variant, "attempt_inspection_001")
    checkpoint = CheckpointRecord(
        checkpoint_id="checkpoint_inspection_001",
        workflow_execution_id=base.workflow_execution_id,
        dispatch_id="dispatch_inspection_001",
        execution_release_ref="workflow-release:inspection@v1",
        graph_sha256=HASH,
        entitlement_snapshot_hash="e" * 64,
        current_state_id=module.state_id,
        runtime_status_id="running",
        committed_outcome_ref="outcome-ref:inspection-checkpoint",
        committed_outcome_sha256=HASH,
        recorded_at_utc=UTC_END,
    )
    removed_types = (
        WorkflowAttemptRecord,
        ModelCallRecord,
        UsageEvent,
        ExecutionOutputRef,
    )
    records = tuple(
        row for row in base.records if not isinstance(row, removed_types)
    )
    records = (*records, start, checkpoint)

    projection = build_runtime_execution_inspection(
        _trace_with(base, records),
        observed_at_utc="2026-08-10T12:06:00Z",
    )

    workflow = projection["trace"]["workflow"]
    # The checkpoint owns the workflow status; the expired lease travels on
    # its own field instead of overwriting that committed fact.
    assert workflow["status"] == "running"
    assert workflow["recovery_required"] is True
    assert workflow["modules_requiring_recovery"] == [module.module_run_id]
    assert projection["modules"][0]["module_run"]["status"] == (
        "recovery_required"
    )
