from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent_runtime.contracts import (
    CellModuleDispatchContext,
    PredecessorWorkflowExternalEvent,
    RuntimeExecutionServices,
    ModuleDispatchRequest,
    ModuleOutcome,
    ModuleOutcomeDisposition,
)
from agent_runtime.ledger import (
    InMemoryRuntimeExecutionRecordStore,
    RuntimeExecutionRecordStore,
)
from agent_runtime.contracts.ledger_record_definition import (
    WorkflowAttemptRecord,
    LegacyExecutionEntitlementSnapshot,
    ExecutionInputRef,
    ExternalEventApplicationRecord,
    ModelCallRecord,
    ToolCallRecord,
    LegacyRuntimeRecordBatch,
    LegacyModuleCapabilityGrant,
    WorkflowModuleRunRecord,
    WorkflowModuleExecutionVariantRecord,
    UsageEvent,
    WorkflowExecutionRecord,
    sha256_json,
)


UTC_START = "2026-08-02T12:00:00Z"
UTC_END = "2026-08-02T12:01:00Z"
UTC_LATE = "2026-08-02T12:02:00Z"
ENTITLEMENT_HASH = "e" * 64


def _dispatch() -> ModuleDispatchRequest:
    return ModuleDispatchRequest(
        workflow_execution_id="execution_opaque_001",
        workflow_id="workflow_opaque",
        workflow_contract_version="v1",
        execution_release_ref="release-ref:opaque-v1",
        graph_sha256="a" * 64,
        current_state_id="state_alpha",
        transition_sequence=0,
        dispatch_id="dispatch_opaque_001",
    )


def _outcome(
    *,
    target_state_id: str = "state_beta",
    evidence_artifact_ref: str = "artifact-ref:evidence-001",
) -> ModuleOutcome:
    return ModuleOutcome.build(
        dispatch_id="dispatch_opaque_001",
        workflow_execution_id="execution_opaque_001",
        expected_state_id="state_alpha",
        disposition=ModuleOutcomeDisposition.TRANSITION,
        target_state_id=target_state_id,
        module_run_id="module_opaque_001",
        attempt_ids=("attempt_opaque_001",),
        evidence_artifact_refs=(evidence_artifact_ref,),
        outcome_ref="outcome-ref:opaque-001",
    )


def _lineage_records() -> tuple[object, ...]:
    execution = WorkflowExecutionRecord(
        workflow_execution_id="execution_opaque_001",
        workflow_id="workflow_opaque",
        workflow_contract_version="v1",
        tenant_id="tenant_opaque",
        cell_id="cell_opaque",
        principal_id="principal_opaque",
        execution_release_ref="release-ref:opaque-v1",
        graph_sha256="a" * 64,
        runtime_execution_binding_ref="runtime-binding:opaque-v1",
        runtime_execution_binding_sha256="c" * 64,
        authorization_decision_ref="authorization-decision:opaque-v1",
        authorization_decision_sha256="d" * 64,
        execution_principal_delegation_ref="execution-delegation:opaque-v1",
        execution_principal_delegation_sha256="e" * 64,
        entitlement_snapshot_ref="entitlement-ref:opaque-v1",
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        execution_input_package_refs=("artifact-ref:evidence-001",),
        execution_input_package_sha256="b" * 64,
        recorded_at_utc=UTC_START,
    )
    entitlement = LegacyExecutionEntitlementSnapshot(
        entitlement_snapshot_id="entitlement_opaque_001",
        workflow_execution_id=execution.workflow_execution_id,
        tenant_id=execution.tenant_id,
        cell_id=execution.cell_id,
        principal_id=execution.principal_id,
        entitlement_snapshot_ref=execution.entitlement_snapshot_ref,
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        recorded_at_utc=UTC_START,
    )
    module = WorkflowModuleRunRecord(
        workflow_execution_id=execution.workflow_execution_id,
        module_run_id="module_opaque_001",
        state_id="state_opaque",
        module_id="module_opaque",
        input_refs=("artifact-ref:evidence-001",),
        input_closure_sha256=sha256_json(["artifact_input_001"]),
        recorded_at_utc=UTC_START,
    )
    variant = WorkflowModuleExecutionVariantRecord(
        workflow_execution_id=execution.workflow_execution_id,
        module_run_id=module.module_run_id,
        variant_id="variant_opaque_001",
        module_id="module_opaque",
        agent_execution_adapter_id="adapter_opaque",
        execution_profile_id="profile_opaque",
        model_id="model_opaque",
        reasoning_profile="profile_reasoning",
        prompt_sha256="c" * 64,
        static_module_sha256="d" * 64,
        input_closure_sha256=module.input_closure_sha256,
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        agent_execution_adapter_revision="adapter_revision_v1",
        runtime_version="runtime_v1",
        tool_policy=("policy_opaque",),
        context_mode="context_opaque",
        output_schema_sha256="1" * 64,
        timeout_seconds=60,
        max_attempts=1,
        execution_profile_sha256="2" * 64,
        recorded_at_utc=UTC_START,
    )
    attempt = WorkflowAttemptRecord(
        workflow_execution_id=execution.workflow_execution_id,
        module_run_id=module.module_run_id,
        variant_id=variant.variant_id,
        attempt_id="attempt_opaque_001",
        parent_attempt_id=None,
        attempt_ordinal=1,
        status="completed",
        period_start_at_utc=UTC_START,
        period_end_at_utc=UTC_END,
        recorded_at_utc=UTC_END,
        trace_id="trace_opaque_001",
        execution_output_refs=(),
        failure_class=None,
    )
    artifact = ExecutionInputRef(
        execution_input_id="artifact_evidence_001",
        workflow_execution_id=execution.workflow_execution_id,
        input_type_id="evidence_opaque",
        schema_version="v1",
        input_ref="artifact-ref:evidence-001",
        input_sha256="4" * 64,
        byte_size=10,
        media_type="application/json",
        recorded_at_utc=UTC_END,
    )
    return execution, entitlement, module, variant, attempt, artifact


def _grant() -> LegacyModuleCapabilityGrant:
    return LegacyModuleCapabilityGrant(
        grant_id="grant_opaque_001",
        workflow_execution_id="execution_opaque_001",
        module_run_id="module_opaque_001",
        variant_id="variant_opaque_001",
        attempt_id="attempt_opaque_001",
        capability_id="capability_opaque",
        resource_id="resource_opaque",
        action_id="action_opaque",
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        idempotency_key="operation_opaque_001",
        expires_after_seconds=300,
        recorded_at_utc=UTC_START,
    )


def _call(*, call_id: str = "model_call_opaque_001") -> ModelCallRecord:
    return ModelCallRecord(
        model_call_id=call_id,
        workflow_execution_id="execution_opaque_001",
        module_run_id="module_opaque_001",
        variant_id="variant_opaque_001",
        attempt_id="attempt_opaque_001",
        grant_id="grant_opaque_001",
        resource_id="resource_opaque",
        action_id="action_opaque",
        agent_execution_adapter_id="adapter_opaque",
        model_id="model_opaque",
        status_id="completed",
        recorded_at_utc=UTC_END,
    )


def _usage() -> UsageEvent:
    return UsageEvent(
        usage_event_id="usage_opaque_001",
        operation_id="model_call_opaque_001",
        workflow_execution_id="execution_opaque_001",
        module_run_id="module_opaque_001",
        variant_id="variant_opaque_001",
        attempt_id="attempt_opaque_001",
        grant_id="grant_opaque_001",
        resource_id="resource_opaque",
        action_id="action_opaque",
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=None,
        cache_creation_tokens=None,
        estimated_cost_usd=None,
        provider_charge_usd=None,
        recorded_at_utc=UTC_END,
    )


def _event_application() -> ExternalEventApplicationRecord:
    return ExternalEventApplicationRecord(
        event_id="event_application_001",
        workflow_execution_id="execution_opaque_001",
        event_type_id="pm_decision",
        expected_state_id="state_waiting",
        target_state_id="state_beta",
        decision_artifact_ref="artifact-ref:evidence-001",
        decision_artifact_sha256="4" * 64,
        authorization_ref="authorization-ref:allow-001",
        graph_sha256="a" * 64,
        recorded_at_utc=UTC_END,
    )


def test_dispatch_context_and_outcomes_are_minimal_and_structural() -> None:
    dispatch = _dispatch()
    assert set(dispatch.as_dict()) == {
        "workflow_execution_id",
        "workflow_id",
        "workflow_contract_version",
        "execution_release_ref",
        "graph_sha256",
        "current_state_id",
        "transition_sequence",
        "retry_sequence",
        "dispatch_id",
    }
    assert "input" not in " ".join(dispatch.as_dict())
    context = CellModuleDispatchContext(
        dispatch=dispatch,
        cell_binding_ref="cell-binding-ref:opaque-001",
        entitlement_snapshot_ref="entitlement-ref:opaque-v1",
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
    )
    context.validate()
    assert context.workflow_execution_id == dispatch.workflow_execution_id

    _outcome().validate()
    ModuleOutcome.build(
        dispatch_id=dispatch.dispatch_id,
        workflow_execution_id=dispatch.workflow_execution_id,
        expected_state_id=dispatch.current_state_id,
        disposition=ModuleOutcomeDisposition.WAIT,
        wait_policy_ref="wait-policy-ref:opaque-001",
        outcome_ref="outcome-ref:wait-001",
    ).validate()
    ModuleOutcome.build(
        dispatch_id=dispatch.dispatch_id,
        workflow_execution_id=dispatch.workflow_execution_id,
        expected_state_id=dispatch.current_state_id,
        disposition=ModuleOutcomeDisposition.RETRYABLE_FAILURE,
        failure_class="provider_timeout",
        outcome_ref="outcome-ref:retry-001",
    ).validate()
    with pytest.raises(ValueError, match="wait requires"):
        ModuleOutcome.build(
            dispatch_id=dispatch.dispatch_id,
            workflow_execution_id=dispatch.workflow_execution_id,
            expected_state_id=dispatch.current_state_id,
            disposition=ModuleOutcomeDisposition.WAIT,
            outcome_ref="outcome-ref:invalid-wait",
        )


def test_external_event_is_authorized_hash_bound_and_content_free() -> None:
    event = PredecessorWorkflowExternalEvent(
        event_id="event_opaque_001",
        workflow_execution_id="execution_opaque_001",
        expected_domain_state="state_waiting",
        target_domain_state="state_resumed",
        event_type="event_opaque",
        decision_artifact_ref="artifact-ref:decision-001",
        decision_artifact_sha256="3" * 64,
        authorization_ref="authorization-ref:allow-001",
        graph_sha256="a" * 64,
    )
    assert set(event.as_dict()) == {
        "event_id",
        "workflow_execution_id",
        "expected_domain_state",
        "target_domain_state",
        "event_type",
        "decision_artifact_ref",
        "decision_artifact_sha256",
        "authorization_ref",
        "graph_sha256",
    }


def test_atomic_store_commits_grant_bound_trace_and_replays_transaction() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    assert isinstance(store, RuntimeExecutionRecordStore)
    batch = LegacyRuntimeRecordBatch(
        workflow_execution_id="execution_opaque_001",
        transaction_id="transaction_opaque_001",
        records=(*_lineage_records(), _grant(), _call(), _usage(), _outcome()),
    )

    receipt = store.commit(batch)
    replay = store.commit(batch)

    assert receipt.replayed is False
    assert replay.replayed is True
    assert replay.transaction_sha256 == receipt.transaction_sha256
    assert store.get_committed_outcome(
        "execution_opaque_001", "dispatch_opaque_001"
    ) == _outcome()
    trace = store.load_trace("execution_opaque_001")
    assert len(trace.records) == len(batch.records)
    assert trace.records_of_type(UsageEvent) == (_usage(),)

    with pytest.raises(ValueError, match="transaction_id reuse"):
        store.commit(
            replace(
                batch,
                records=(
                    *batch.records[:-1],
                    _outcome(target_state_id="state_gamma"),
                ),
            )
        )


def test_store_rejects_missing_mismatched_expired_and_replayed_grants_atomically() -> None:
    missing_grant = LegacyRuntimeRecordBatch(
        workflow_execution_id="execution_opaque_001",
        transaction_id="transaction_missing_grant",
        records=(*_lineage_records(), _call(), _usage()),
    )
    store = InMemoryRuntimeExecutionRecordStore()
    with pytest.raises(PermissionError, match="no matching LegacyModuleCapabilityGrant"):
        store.commit(missing_grant)
    assert store.load_trace("execution_opaque_001").records == ()

    wrong_action = LegacyRuntimeRecordBatch(
        workflow_execution_id="execution_opaque_001",
        transaction_id="transaction_wrong_action",
        records=(
            *_lineage_records(),
            _grant(),
            replace(_call(), action_id="different_action"),
        ),
    )
    with pytest.raises(PermissionError, match="action_id"):
        store.commit(wrong_action)

    expired = LegacyRuntimeRecordBatch(
        workflow_execution_id="execution_opaque_001",
        transaction_id="transaction_expired_grant",
        records=(
            *_lineage_records(),
            replace(_grant(), expires_after_seconds=1),
            _call(),
        ),
    )
    with pytest.raises(PermissionError, match="expired"):
        store.commit(expired)

    replayed_grant = LegacyRuntimeRecordBatch(
        workflow_execution_id="execution_opaque_001",
        transaction_id="transaction_replayed_grant",
        records=(
            *_lineage_records(),
            _grant(),
            _call(),
            replace(_call(), model_call_id="model_call_opaque_002"),
        ),
    )
    with pytest.raises(PermissionError, match="multiple operations"):
        store.commit(replayed_grant)


@pytest.mark.parametrize("attempt_status", ("failed", "cancelled"))
def test_store_accepts_late_failure_observations_only_with_terminal_failure(
    attempt_status: str,
) -> None:
    lineage = list(_lineage_records())
    lineage[4] = replace(
        lineage[4],
        status=attempt_status,
        failure_class="timeout",
        period_end_at_utc=UTC_END,
        recorded_at_utc=UTC_LATE,
    )
    grant = replace(_grant(), expires_after_seconds=1)
    tool = ToolCallRecord(
        tool_call_id="tool_call_opaque_001",
        workflow_execution_id="execution_opaque_001",
        module_run_id="module_opaque_001",
        variant_id="variant_opaque_001",
        attempt_id="attempt_opaque_001",
        grant_id=grant.grant_id,
        resource_id=grant.resource_id,
        action_id=grant.action_id,
        tool_id="tool_opaque",
        status_id="completed",
        recorded_at_utc=UTC_LATE,
    )
    usage = replace(
        _usage(),
        usage_event_id="usage_tool_opaque_001",
        operation_id=tool.tool_call_id,
        recorded_at_utc=UTC_LATE,
    )
    store = InMemoryRuntimeExecutionRecordStore()

    store.commit(
        LegacyRuntimeRecordBatch(
            workflow_execution_id="execution_opaque_001",
            transaction_id=f"transaction_late_{attempt_status}_tool",
            records=(*lineage, grant, tool, usage),
        )
    )

    trace = store.load_trace("execution_opaque_001")
    assert trace.records_of_type(ToolCallRecord) == (tool,)
    assert trace.records_of_type(UsageEvent) == (usage,)


def test_store_rejects_forged_failure_label_and_posthoc_call_attachment() -> None:
    completed_lineage = _lineage_records()
    expired_grant = replace(_grant(), expires_after_seconds=1)
    forged = replace(
        _call(),
        status_id="failed",
        recorded_at_utc=UTC_END,
    )
    usage = replace(_usage(), recorded_at_utc=UTC_END)
    store = InMemoryRuntimeExecutionRecordStore()

    with pytest.raises(PermissionError, match="expired"):
        store.commit(
            LegacyRuntimeRecordBatch(
                workflow_execution_id="execution_opaque_001",
                transaction_id="transaction_forged_failed_label",
                records=(*completed_lineage, expired_grant, forged, usage),
            )
        )

    store.commit(
        LegacyRuntimeRecordBatch(
            workflow_execution_id="execution_opaque_001",
            transaction_id="transaction_terminal_without_call",
            records=(*completed_lineage, _grant()),
        )
    )
    with pytest.raises(ValueError, match="existing terminal Attempt"):
        store.commit(
            LegacyRuntimeRecordBatch(
                workflow_execution_id="execution_opaque_001",
                transaction_id="transaction_posthoc_call",
                records=(_call(), _usage()),
            )
        )


def test_store_rejects_mismatched_late_failure_record_time() -> None:
    lineage = list(_lineage_records())
    lineage[4] = replace(
        lineage[4],
        status="failed",
        failure_class="timeout",
        recorded_at_utc=UTC_LATE,
    )
    grant = replace(_grant(), expires_after_seconds=1)
    call = replace(_call(), recorded_at_utc=UTC_END)
    usage = replace(_usage(), recorded_at_utc=UTC_END)

    with pytest.raises(PermissionError, match="differs from terminal Attempt"):
        InMemoryRuntimeExecutionRecordStore().commit(
            LegacyRuntimeRecordBatch(
                workflow_execution_id="execution_opaque_001",
                transaction_id="transaction_mismatched_late_record_time",
                records=(*lineage, grant, call, usage),
            )
        )


def test_store_rejects_posthoc_usage_for_existing_terminal_attempt() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    store.commit(
        LegacyRuntimeRecordBatch(
            workflow_execution_id="execution_opaque_001",
            transaction_id="transaction_complete_call_and_usage",
            records=(*_lineage_records(), _grant(), _call(), _usage()),
        )
    )
    extra_usage = replace(
        _usage(),
        usage_event_id="usage_opaque_posthoc",
    )

    with pytest.raises(ValueError, match="UsageEvent cannot be appended"):
        store.commit(
            LegacyRuntimeRecordBatch(
                workflow_execution_id="execution_opaque_001",
                transaction_id="transaction_posthoc_usage",
                records=(extra_usage,),
            )
        )


def test_store_rejects_incomplete_canonical_lineage_and_missing_usage() -> None:
    lineage = _lineage_records()
    execution, entitlement, module, variant, attempt, artifact = lineage

    cases = (
        (
            "transaction_variant_without_module",
            (execution, entitlement, variant, attempt, artifact),
            "unknown.*Module Run",
        ),
        (
            "transaction_attempt_wrong_variant_module",
            (
                execution,
                entitlement,
                module,
                replace(variant, module_run_id="module_other_001"),
                attempt,
                artifact,
            ),
            "Variant references an unknown Module Run",
        ),
        (
            "transaction_input_cross_execution",
            (
                execution,
                entitlement,
                module,
                variant,
                attempt,
                replace(
                    artifact,
                    workflow_execution_id="execution_other_001",
                ),
            ),
            "cross-execution record",
        ),
        (
            "transaction_module_unknown_input",
            (
                execution,
                entitlement,
                replace(module, input_refs=("artifact-ref:missing-input",)),
                variant,
                attempt,
                artifact,
            ),
            "outside the frozen execution package and prior outputs",
        ),
        (
            "transaction_variant_wrong_input_hash",
            (
                execution,
                entitlement,
                module,
                replace(variant, input_closure_sha256="9" * 64),
                attempt,
                artifact,
            ),
            "input closure hash differs",
        ),
        (
            "transaction_outcome_unknown_evidence",
            (
                execution,
                entitlement,
                module,
                variant,
                replace(attempt, execution_output_refs=()),
                artifact,
                _grant(),
                _call(),
                _usage(),
                _outcome(evidence_artifact_ref="artifact-ref:missing-evidence"),
            ),
            "evidence is outside the frozen execution package and prior outputs",
        ),
        (
            "transaction_call_without_usage",
            (*lineage, _grant(), _call()),
            "requires exactly one UsageEvent",
        ),
        (
            "transaction_attempt_over_variant_limit",
            (
                execution,
                entitlement,
                module,
                variant,
                replace(attempt, attempt_ordinal=2),
                artifact,
            ),
            "exceeds Variant max_attempts",
        ),
        (
            "transaction_attempt_duplicate_ordinal",
            (
                execution,
                entitlement,
                module,
                variant,
                attempt,
                replace(
                    attempt,
                    attempt_id="attempt_opaque_002",
                    parent_attempt_id=attempt.attempt_id,
                    trace_id="trace_opaque_002",
                ),
                artifact,
            ),
            "unique and contiguous",
        ),
    )
    for transaction_id, records, message in cases:
        store = InMemoryRuntimeExecutionRecordStore()
        with pytest.raises((ValueError, PermissionError), match=message):
            store.commit(
                LegacyRuntimeRecordBatch(
                    workflow_execution_id=execution.workflow_execution_id,
                    transaction_id=transaction_id,
                    records=records,
                )
            )
        assert store.load_trace(execution.workflow_execution_id).records == ()


def test_store_rejects_entitlement_drift_and_cross_execution_batch() -> None:
    lineage = _lineage_records()
    execution, entitlement, *rest = lineage

    drifted = InMemoryRuntimeExecutionRecordStore()
    with pytest.raises(PermissionError, match="entitlement snapshot hash changed"):
        drifted.commit(
            LegacyRuntimeRecordBatch(
                workflow_execution_id=execution.workflow_execution_id,
                transaction_id="transaction_entitlement_drift",
                records=(
                    execution,
                    replace(
                        entitlement,
                        entitlement_snapshot_hash="9" * 64,
                    ),
                    *rest,
                ),
            )
        )
    assert drifted.load_trace(execution.workflow_execution_id).records == ()

    crossed = InMemoryRuntimeExecutionRecordStore()
    with pytest.raises(ValueError, match="cross-execution record"):
        crossed.commit(
            LegacyRuntimeRecordBatch(
                workflow_execution_id=execution.workflow_execution_id,
                transaction_id="transaction_cross_execution",
                records=(
                    execution,
                    entitlement,
                    replace(rest[-1], workflow_execution_id="execution_other_001"),
                ),
            )
        )
    assert crossed.load_trace(execution.workflow_execution_id).records == ()


def test_store_commits_acknowledged_external_event_and_rejects_scope_drift() -> None:
    lineage = _lineage_records()
    execution = lineage[0]
    store = InMemoryRuntimeExecutionRecordStore()
    store.commit(
        LegacyRuntimeRecordBatch(
            workflow_execution_id=execution.workflow_execution_id,
            transaction_id="transaction_external_event_001",
            records=(*lineage, _event_application()),
        )
    )
    assert store.load_trace(execution.workflow_execution_id).records_of_type(
        ExternalEventApplicationRecord
    ) == (_event_application(),)

    invalid_cases = (
        (
            "transaction_external_event_duplicate",
            (*lineage, _event_application(), replace(_event_application(), authorization_ref="authorization-ref:allow-002")),
            "duplicate ExternalEventApplicationRecord identity",
        ),
        (
            "transaction_external_event_unknown_artifact",
            (*lineage, replace(_event_application(), decision_artifact_ref="artifact-ref:missing-decision")),
            "unknown input or output",
        ),
        (
            "transaction_external_event_artifact_hash",
            (*lineage, replace(_event_application(), decision_artifact_sha256="5" * 64)),
            "Artifact hash differs",
        ),
        (
            "transaction_external_event_graph",
            (*lineage, replace(_event_application(), graph_sha256="6" * 64)),
            "graph hash differs",
        ),
    )
    for transaction_id, records, message in invalid_cases:
        isolated = InMemoryRuntimeExecutionRecordStore()
        with pytest.raises(ValueError, match=message):
            isolated.commit(
                LegacyRuntimeRecordBatch(
                    workflow_execution_id=execution.workflow_execution_id,
                    transaction_id=transaction_id,
                    records=records,
                )
            )
        assert isolated.load_trace(execution.workflow_execution_id).records == ()


def test_runtime_services_exposes_only_mediated_driver_operations() -> None:
    methods = {
        name
        for name, value in RuntimeExecutionServices.__dict__.items()
        if callable(value) and not name.startswith("_")
    }
    assert methods == {
        "commit_outcome",
        "commit_external_event_application",
        "create_module_run",
        "invoke_module",
        "read_artifact_bytes",
        "record_execution_output",
        "resolve_artifact_ref",
        "resolve_execution_profile",
    }


def test_gate_c_core_has_no_project_domain_imports() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    for relative_path in (
            "src/agent_runtime/contracts/registry_workflow_definition.py",
            "src/agent_runtime/contracts/ledger_record_definition.py",
            "src/agent_runtime/ledger/ledger_record_persistence.py",
    ):
        source = (repo_root / relative_path).read_text(encoding="utf-8")
        assert "host_business_workflow" not in source
        assert "src.digestion" not in source
        assert "src.research" not in source
        assert "src.trade" not in source
