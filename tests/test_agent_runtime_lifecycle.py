from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.contracts import ModuleOutcome, ModuleOutcomeDisposition
from agent_runtime.execution.execution_record_persistence import InMemoryRuntimeExecutionRecordStore
from agent_runtime.contracts.execution_record_definition import (
    LegacyAttemptBeginBatch,
    AttemptClaim,
    AttemptFinalizationBatch,
    AttemptOrphanedRecord,
    AttemptOrphaningBatch,
    WorkflowAttemptRecord,
    AttemptOutputBundle,
    WorkflowAttemptStartedRecord,
    BackendAcknowledgementRecord,
    CheckpointRecord,
    LegacyExecutionEntitlementSnapshot,
    ExecutionInputRef,
    ExecutionOutputRef,
    InvocationCommitRecord,
    ModelCallRecord,
    LegacyOperationGrantBatch,
    OutcomeCommitBatch,
    RuntimeRecordBatch,
    LegacyRuntimeRecordBatch,
    LegacyModuleCapabilityGrant,
    WorkflowModuleRunRecord,
    WorkflowModuleExecutionVariantRecord,
    StaleOutputRecord,
    UsageEvent,
    WorkflowExecutionRecord,
    attempt_output_bundle_sha256,
    sha256_json,
    sha256_text,
)


EXECUTION_ID = "execution_synthetic_001"
DISPATCH_ID = "dispatch_synthetic_001"
STEP_ID = "module_synthetic_001"
VARIANT_ID = "variant_synthetic_001"
ATTEMPT_ID = "attempt_synthetic_001"
ENTITLEMENT_HASH = "e" * 64
START = "2026-08-02T12:00:00Z"
END = "2026-08-02T12:01:00Z"
CHECKPOINT_TIME = "2026-08-02T12:01:01Z"
ACK_TIME = "2026-08-02T12:01:02Z"


def _bootstrap(store: InMemoryRuntimeExecutionRecordStore) -> None:
    input_artifact = ExecutionInputRef(
        execution_input_id="artifact_synthetic_input_001",
        workflow_execution_id=EXECUTION_ID,
        input_type_id="input_bundle",
        schema_version="v1",
        input_ref="artifact-ref:synthetic-input-001",
        input_sha256="1" * 64,
        byte_size=12,
        media_type="application/json",
        recorded_at_utc=START,
    )
    module = WorkflowModuleRunRecord(
        workflow_execution_id=EXECUTION_ID,
        module_run_id=STEP_ID,
        state_id="state_synthetic",
        module_id="module_synthetic",
        input_refs=(input_artifact.input_ref,),
        input_closure_sha256=sha256_json([input_artifact.input_ref]),
        recorded_at_utc=START,
    )
    store.commit(
        LegacyRuntimeRecordBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_bootstrap",
            records=(
                WorkflowExecutionRecord(
                    workflow_execution_id=EXECUTION_ID,
                    workflow_id="workflow_synthetic",
                    workflow_contract_version="v1",
                    tenant_id="tenant_synthetic",
                    cell_id="cell_synthetic",
                    principal_id="principal_synthetic",
                    execution_release_ref="release-ref:synthetic-v1",
                    graph_sha256="a" * 64,
                    runtime_execution_binding_ref="runtime-binding:synthetic-v1",
                    runtime_execution_binding_sha256="c" * 64,
                    authorization_decision_ref="authorization-decision:synthetic-v1",
                    authorization_decision_sha256="d" * 64,
                    execution_principal_delegation_ref="execution-delegation:synthetic-v1",
                    execution_principal_delegation_sha256="e" * 64,
                    entitlement_snapshot_ref="entitlement-ref:synthetic-v1",
                    entitlement_snapshot_hash=ENTITLEMENT_HASH,
                    execution_input_package_refs=(input_artifact.input_ref,),
                    execution_input_package_sha256="b" * 64,
                    recorded_at_utc=START,
                ),
                LegacyExecutionEntitlementSnapshot(
                    entitlement_snapshot_id="entitlement_synthetic_001",
                    workflow_execution_id=EXECUTION_ID,
                    tenant_id="tenant_synthetic",
                    cell_id="cell_synthetic",
                    principal_id="principal_synthetic",
                    entitlement_snapshot_ref="entitlement-ref:synthetic-v1",
                    entitlement_snapshot_hash=ENTITLEMENT_HASH,
                    recorded_at_utc=START,
                ),
                input_artifact,
                module,
                WorkflowModuleExecutionVariantRecord(
                    workflow_execution_id=EXECUTION_ID,
                    module_run_id=STEP_ID,
                    variant_id=VARIANT_ID,
                    module_id="module_synthetic",
                    agent_execution_adapter_id="adapter_synthetic",
                    execution_profile_id="profile_synthetic",
                    model_id="model_synthetic",
                    reasoning_profile="effort_synthetic",
                    prompt_sha256="c" * 64,
                    static_module_sha256="d" * 64,
                    input_closure_sha256=module.input_closure_sha256,
                    entitlement_snapshot_hash=ENTITLEMENT_HASH,
                    agent_execution_adapter_revision="adapter_revision_v1",
                    runtime_version="runtime_v1",
                    tool_policy=("no_tools",),
                    context_mode="stateless",
                    output_schema_sha256="f" * 64,
                    timeout_seconds=120,
                    max_attempts=2,
                    execution_profile_sha256="2" * 64,
                    recorded_at_utc=START,
                ),
            ),
        )
    )


def test_canonical_runtime_batch_rejects_pre_ar09_authority_records() -> None:
    legacy = LegacyExecutionEntitlementSnapshot(
        entitlement_snapshot_id="entitlement_legacy_001",
        workflow_execution_id=EXECUTION_ID,
        tenant_id="tenant_synthetic",
        cell_id="cell_synthetic",
        principal_id="principal_synthetic",
        entitlement_snapshot_ref="entitlement-ref:legacy-v1",
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        recorded_at_utc=START,
    )
    with pytest.raises(TypeError, match="unsupported Runtime record"):
        RuntimeRecordBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_reject_legacy_authority",
            records=(legacy,),  # type: ignore[arg-type]
        ).validate()


def _claim(token: str = "claim-token-abcdefghijklmnopqrstuvwxyz-001") -> AttemptClaim:
    return AttemptClaim(
        workflow_execution_id=EXECUTION_ID,
        attempt_id=ATTEMPT_ID,
        claim_token=token,
    )


def _start(claim: AttemptClaim | None = None) -> WorkflowAttemptStartedRecord:
    active_claim = claim or _claim()
    trace_id = "trace_synthetic_001"
    return WorkflowAttemptStartedRecord(
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        parent_attempt_id=None,
        attempt_ordinal=1,
        trace_id=trace_id,
        request_sha256="3" * 64,
        claim_token_hash=sha256_text(active_claim.claim_token),
        input_closure_sha256=sha256_json(["artifact-ref:synthetic-input-001"]),
        execution_profile_sha256="2" * 64,
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        timeout_seconds=120,
        recorded_at_utc=START,
    )


def _grant(
    *,
    grant_id: str = "grant_synthetic_model_001",
    idempotency_key: str = "operation_synthetic_model_001",
    action_id: str = "invoke_model",
    recorded_at_utc: str = START,
) -> LegacyModuleCapabilityGrant:
    return LegacyModuleCapabilityGrant(
        grant_id=grant_id,
        workflow_execution_id=EXECUTION_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        capability_id="capability_synthetic",
        resource_id="resource_synthetic",
        action_id=action_id,
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        idempotency_key=idempotency_key,
        expires_after_seconds=120,
        recorded_at_utc=recorded_at_utc,
    )


def _finalization() -> AttemptFinalizationBatch:
    output_ref = "artifact-ref:synthetic-output-001"
    terminal = WorkflowAttemptRecord(
        workflow_execution_id=EXECUTION_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        parent_attempt_id=None,
        attempt_ordinal=1,
        status="completed",
        period_start_at_utc=START,
        period_end_at_utc=END,
        recorded_at_utc=END,
        trace_id="trace_synthetic_001",
        execution_output_refs=(output_ref,),
        failure_class=None,
    )
    output = ExecutionOutputRef(
        execution_output_id="execution_output_synthetic_001",
        workflow_execution_id=EXECUTION_ID,
        output_type_id="synthetic_output",
        schema_version="v1",
        output_ref=output_ref,
        output_sha256="4" * 64,
        byte_size=14,
        media_type="application/json",
        recorded_at_utc=END,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
    )
    call = ModelCallRecord(
        model_call_id="model_call_synthetic_001",
        workflow_execution_id=EXECUTION_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        grant_id="grant_synthetic_model_001",
        resource_id="resource_synthetic",
        action_id="invoke_model",
        agent_execution_adapter_id="adapter_synthetic",
        model_id="model_synthetic",
        status_id="completed",
        recorded_at_utc=END,
    )
    usage = UsageEvent(
        usage_event_id="usage_synthetic_001",
        operation_id=call.model_call_id,
        workflow_execution_id=EXECUTION_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        grant_id=call.grant_id,
        resource_id=call.resource_id,
        action_id=call.action_id,
        input_tokens=10,
        output_tokens=5,
        cache_read_tokens=None,
        cache_creation_tokens=None,
        estimated_cost_usd=None,
        provider_charge_usd=None,
        recorded_at_utc=END,
    )
    transaction_id = "transaction_synthetic_finalize"
    invocation = InvocationCommitRecord(
        invocation_commit_id="invocation_commit_synthetic_001",
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        request_sha256="3" * 64,
        attempt_output_bundle_sha256=attempt_output_bundle_sha256((output,)),
        terminal_status="completed",
        context_disposition_id="stateless",
        commit_transaction_id=transaction_id,
        recorded_at_utc=END,
    )
    return AttemptFinalizationBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id=transaction_id,
        terminal_attempt=terminal,
        invocation_commit=invocation,
        records=(output, call, usage),
    )


def _outcome() -> ModuleOutcome:
    return ModuleOutcome.build(
        dispatch_id=DISPATCH_ID,
        workflow_execution_id=EXECUTION_ID,
        expected_state_id="state_synthetic",
        disposition=ModuleOutcomeDisposition.TRANSITION,
        target_state_id="state_complete",
        module_run_id=STEP_ID,
        attempt_ids=(ATTEMPT_ID,),
        evidence_artifact_refs=("artifact-ref:synthetic-output-001",),
        outcome_ref="outcome-ref:synthetic-001",
    )


def _begin(store: InMemoryRuntimeExecutionRecordStore) -> AttemptClaim:
    claim = _claim()
    receipt = store.begin_attempt(
        LegacyAttemptBeginBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_begin",
            start=_start(claim),
            claim=claim,
            grants=(_grant(),),
        )
    )
    assert receipt.commit_receipt.replayed is False
    return receipt.claim


def test_typed_lifecycle_orders_begin_finalize_outcome_and_backend_ack() -> None:
    store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=lambda _: True
    )
    _bootstrap(store)
    claim = _begin(store)

    finalization = _finalization()
    finalized = store.finalize_attempt(claim, finalization)
    assert finalized.commit_receipt.replayed is False
    assert store.finalize_attempt(claim, finalization).commit_receipt.replayed is True
    assert store.get_committed_invocation(EXECUTION_ID, DISPATCH_ID) == (
        finalization.invocation_commit
    )
    output_bundles = store.load_trace(EXECUTION_ID).records_of_type(
        AttemptOutputBundle
    )
    assert len(output_bundles) == 1
    assert output_bundles[0].execution_output_refs == (
        "artifact-ref:synthetic-output-001",
    )
    assert output_bundles[0].bundle_sha256 == (
        finalization.invocation_commit.attempt_output_bundle_sha256
    )

    outcome = _outcome()
    checkpoint = CheckpointRecord(
        checkpoint_id="checkpoint_synthetic_001",
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        execution_release_ref="release-ref:synthetic-v1",
        graph_sha256="a" * 64,
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        current_state_id="state_complete",
        runtime_status_id="completed",
        committed_outcome_ref=outcome.outcome_ref,
        committed_outcome_sha256=outcome.outcome_sha256,
        recorded_at_utc=CHECKPOINT_TIME,
    )
    store.commit_outcome(
        OutcomeCommitBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_outcome",
            outcome=outcome,
            checkpoint=checkpoint,
        )
    )
    acknowledgement = BackendAcknowledgementRecord(
        backend_acknowledgement_id="backend_ack_synthetic_001",
        workflow_execution_id=EXECUTION_ID,
        backend_id="backend_synthetic",
        backend_execution_id="backend_execution_synthetic_001",
        dispatch_id=DISPATCH_ID,
        checkpoint_id=checkpoint.checkpoint_id,
        outcome_ref=outcome.outcome_ref,
        outcome_sha256=outcome.outcome_sha256,
        acknowledgement_kind="activity_completed",
        recorded_at_utc=ACK_TIME,
    )
    ack_receipt = store.acknowledge_backend(acknowledgement)
    assert ack_receipt.commit_receipt.replayed is False
    assert store.acknowledge_backend(acknowledgement).commit_receipt.replayed is True

    trace = store.load_trace(EXECUTION_ID)
    record_types = tuple(type(record).__name__ for record in trace.records)
    assert record_types.index("WorkflowAttemptStartedRecord") < record_types.index(
        "ModelCallRecord"
    )
    assert record_types.index("InvocationCommitRecord") < record_types.index(
        "ModuleOutcome"
    )
    assert record_types.index("CheckpointRecord") < record_types.index(
        "BackendAcknowledgementRecord"
    )


def test_begin_failure_prevents_provider_entry_and_second_active_claim() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    provider_entries = 0
    claim = _claim()
    invalid_begin = LegacyAttemptBeginBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_synthetic_bad_begin",
        start=_start(claim),
        claim=claim,
        grants=(replace(_grant(), variant_id="variant_other_001"),),
    )
    with pytest.raises(ValueError, match="crossed start lineage"):
        store.begin_attempt(invalid_begin)
    assert provider_entries == 0
    assert store.load_trace(EXECUTION_ID).records_of_type(WorkflowAttemptStartedRecord) == ()

    _begin(store)
    second_claim = AttemptClaim(
        workflow_execution_id=EXECUTION_ID,
        attempt_id="attempt_synthetic_002",
        claim_token="claim-token-abcdefghijklmnopqrstuvwxyz-002",
    )
    second_start = replace(
        _start(second_claim),
        attempt_id=second_claim.attempt_id,
        attempt_ordinal=2,
        parent_attempt_id=ATTEMPT_ID,
        claim_token_hash=sha256_text(second_claim.claim_token),
    )
    with pytest.raises(RuntimeError, match="active Attempt claim"):
        store.begin_attempt(
            LegacyAttemptBeginBatch(
                workflow_execution_id=EXECUTION_ID,
                transaction_id="transaction_synthetic_second_begin",
                start=second_start,
                claim=second_claim,
                grants=(
                    replace(
                        _grant(),
                        grant_id="grant_synthetic_model_002",
                        attempt_id=second_claim.attempt_id,
                        idempotency_key="operation_synthetic_model_002",
                    ),
                ),
            )
        )


def test_claim_grant_and_ack_drift_fail_closed_without_partial_commit() -> None:
    store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=lambda _: True
    )
    _bootstrap(store)
    claim = _begin(store)

    wrong_claim = replace(
        claim,
        claim_token="claim-token-abcdefghijklmnopqrstuvwxyz-wrong",
    )
    with pytest.raises(PermissionError, match="token"):
        store.finalize_attempt(wrong_claim, _finalization())
    assert store.load_trace(EXECUTION_ID).records_of_type(WorkflowAttemptRecord) == ()

    with pytest.raises(PermissionError, match="precedes Attempt start"):
        store.authorize_operation(
            LegacyOperationGrantBatch(
                workflow_execution_id=EXECUTION_ID,
                transaction_id="transaction_synthetic_early_grant",
                claim=claim,
                grants=(
                    _grant(
                        grant_id="grant_synthetic_tool_001",
                        idempotency_key="operation_synthetic_tool_001",
                        action_id="invoke_tool",
                        recorded_at_utc="2026-08-02T11:59:59Z",
                    ),
                ),
            )
        )

    store.finalize_attempt(claim, _finalization())
    outcome = _outcome()
    checkpoint = CheckpointRecord(
        checkpoint_id="checkpoint_synthetic_001",
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        execution_release_ref="release-ref:synthetic-v1",
        graph_sha256="a" * 64,
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        current_state_id="state_complete",
        runtime_status_id="completed",
        committed_outcome_ref=outcome.outcome_ref,
        committed_outcome_sha256=outcome.outcome_sha256,
        recorded_at_utc=CHECKPOINT_TIME,
    )
    store.commit_outcome(
        OutcomeCommitBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_outcome",
            outcome=outcome,
            checkpoint=checkpoint,
        )
    )
    with pytest.raises(ValueError, match="differs from checkpoint"):
        store.acknowledge_backend(
            BackendAcknowledgementRecord(
                backend_acknowledgement_id="backend_ack_synthetic_bad",
                workflow_execution_id=EXECUTION_ID,
                backend_id="backend_synthetic",
                backend_execution_id="backend_execution_synthetic_001",
                dispatch_id=DISPATCH_ID,
                checkpoint_id=checkpoint.checkpoint_id,
                outcome_ref=outcome.outcome_ref,
                outcome_sha256="9" * 64,
                acknowledgement_kind="activity_completed",
                recorded_at_utc=ACK_TIME,
            )
        )
    assert (
        store.load_trace(EXECUTION_ID).records_of_type(BackendAcknowledgementRecord)
        == ()
    )


def test_orphan_disposition_terminalizes_start_before_next_attempt() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    claim = _begin(store)
    orphaned_at = "2026-08-02T12:00:30Z"
    terminal = WorkflowAttemptRecord(
        workflow_execution_id=EXECUTION_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        parent_attempt_id=None,
        attempt_ordinal=1,
        status="failed",
        period_start_at_utc=START,
        period_end_at_utc=orphaned_at,
        recorded_at_utc=orphaned_at,
        trace_id="trace_synthetic_001",
        execution_output_refs=(),
        failure_class="orphaned_attempt",
    )
    orphaned = AttemptOrphanedRecord(
        orphaned_record_id="attempt_orphaned_synthetic_001",
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        reason_code="worker_lost",
        context_disposition_id="invalidate",
        recorded_at_utc=orphaned_at,
    )
    batch = AttemptOrphaningBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_synthetic_orphan",
        terminal_attempt=terminal,
        orphaned=orphaned,
    )
    first_orphan = store.orphan_attempt(
        claim,
        batch,
    )
    assert first_orphan.commit_receipt.replayed is False
    assert store.orphan_attempt(claim, batch).commit_receipt.replayed is True

    second_claim = AttemptClaim(
        workflow_execution_id=EXECUTION_ID,
        attempt_id="attempt_synthetic_002",
        claim_token="claim-token-abcdefghijklmnopqrstuvwxyz-002",
    )
    second_start = WorkflowAttemptStartedRecord(
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=second_claim.attempt_id,
        parent_attempt_id=ATTEMPT_ID,
        attempt_ordinal=2,
        trace_id="trace_synthetic_002",
        request_sha256="5" * 64,
        claim_token_hash=sha256_text(second_claim.claim_token),
        input_closure_sha256=sha256_json(["artifact-ref:synthetic-input-001"]),
        execution_profile_sha256="2" * 64,
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        timeout_seconds=120,
        recorded_at_utc=END,
    )
    receipt = store.begin_attempt(
        LegacyAttemptBeginBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_retry_begin",
            start=second_start,
            claim=second_claim,
            grants=(
                replace(
                    _grant(),
                    grant_id="grant_synthetic_model_002",
                    attempt_id=second_claim.attempt_id,
                    idempotency_key="operation_synthetic_model_002",
                    recorded_at_utc=END,
                ),
            ),
        )
    )
    assert receipt.claim.attempt_id == "attempt_synthetic_002"
    assert store.load_trace(EXECUTION_ID).records_of_type(AttemptOrphanedRecord) == (
        orphaned,
    )


def test_stale_result_is_quarantined_without_normal_output_artifacts() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    claim = _begin(store)
    terminal = WorkflowAttemptRecord(
        workflow_execution_id=EXECUTION_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        parent_attempt_id=None,
        attempt_ordinal=1,
        status="failed",
        period_start_at_utc=START,
        period_end_at_utc=END,
        recorded_at_utc=END,
        trace_id="trace_synthetic_001",
        execution_output_refs=(),
        failure_class="stale_rejected",
    )
    transaction_id = "transaction_synthetic_stale"
    invocation = InvocationCommitRecord(
        invocation_commit_id="invocation_commit_synthetic_stale",
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        request_sha256="3" * 64,
        attempt_output_bundle_sha256=attempt_output_bundle_sha256(()),
        terminal_status="failed",
        context_disposition_id="invalidate",
        commit_transaction_id=transaction_id,
        recorded_at_utc=END,
    )
    stale = StaleOutputRecord(
        stale_output_id="stale_output_synthetic_001",
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        module_run_id=STEP_ID,
        variant_id=VARIANT_ID,
        attempt_id=ATTEMPT_ID,
        quarantine_ref="quarantine-ref:synthetic-output-001",
        quarantine_sha256="6" * 64,
        reason_code="dispatch_superseded",
        recorded_at_utc=END,
    )
    store.finalize_attempt(
        claim,
        AttemptFinalizationBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id=transaction_id,
            terminal_attempt=terminal,
            invocation_commit=invocation,
            records=(stale,),
        ),
    )
    trace = store.load_trace(EXECUTION_ID)
    assert trace.records_of_type(StaleOutputRecord) == (stale,)
    assert all(
        output.output_ref != stale.quarantine_ref
        for output in trace.records_of_type(ExecutionOutputRef)
    )


def test_missing_output_bytes_reject_finalization_without_terminal_commit() -> None:
    store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=lambda _: False
    )
    _bootstrap(store)
    claim = _begin(store)
    with pytest.raises(FileNotFoundError, match="execution-output bytes are missing"):
        store.finalize_attempt(claim, _finalization())
    trace = store.load_trace(EXECUTION_ID)
    assert trace.records_of_type(WorkflowAttemptRecord) == ()
    assert trace.records_of_type(InvocationCommitRecord) == ()
