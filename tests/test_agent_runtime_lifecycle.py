from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
from types import SimpleNamespace

import pytest

from agent_runtime.contracts import ModuleOutcome, ModuleOutcomeDisposition
from agent_runtime.contracts.ledger_lineage_definition import (
    ModuleAttemptRecord,
    ModuleUsageObservation,
)
from agent_runtime.ledger.ledger_record_persistence import InMemoryRuntimeExecutionRecordStore
from agent_runtime.ledger.ledger_workflow_module_recording import (
    WorkflowModuleLedgerBinding,
    WorkflowModuleLedgerRecorder,
)
from agent_runtime.contracts.ledger_record_definition import (
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
    stable_runtime_id,
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


def test_committed_batch_rebuild_restores_active_attempt_claim(monkeypatch) -> None:
    committed_batches: list[RuntimeRecordBatch | LegacyRuntimeRecordBatch] = []

    class CapturingStore(InMemoryRuntimeExecutionRecordStore):
        def commit(self, batch):  # type: ignore[no-untyped-def]
            receipt = super().commit(batch)
            if not receipt.replayed:
                committed_batches.append(batch)
            return receipt

    source = CapturingStore()
    _bootstrap(source)
    claim = _begin(source)
    validation_calls = 0
    original_validate = InMemoryRuntimeExecutionRecordStore._validate_candidate

    def count_validation(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal validation_calls
        validation_calls += 1
        return original_validate(self, *args, **kwargs)

    monkeypatch.setattr(
        InMemoryRuntimeExecutionRecordStore,
        "_validate_candidate",
        count_validation,
    )
    rebuilt = InMemoryRuntimeExecutionRecordStore.from_committed_batches(
        committed_batches
    )
    assert validation_calls == 1

    receipt = rebuilt.authorize_operation(
        LegacyOperationGrantBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_rebuilt_grant",
            claim=claim,
            grants=(
                _grant(
                    grant_id="grant_synthetic_model_rebuilt",
                    idempotency_key="operation_synthetic_model_rebuilt",
                ),
            ),
        )
    )

    assert receipt.commit_receipt.replayed is False


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


def _derived_claim_token(secret: bytes) -> str:
    """Mirror the recorder's HMAC claim derivation for recovery fixtures."""

    return hmac.new(
        secret,
        f"{EXECUTION_ID}\x1f{ATTEMPT_ID}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def test_recorder_recovers_expired_attempt_after_restart_with_stable_secret() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    secret = b"stable-workflow-execution-secret-32-bytes"
    claim = _claim(_derived_claim_token(secret))
    store.begin_attempt(
        LegacyAttemptBeginBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_recovery_begin",
            start=_start(claim),
            claim=claim,
            grants=(_grant(),),
        )
    )
    binding = WorkflowModuleLedgerBinding(
        record_store=store,
        entitlement_snapshot_hash=ENTITLEMENT_HASH,
        claim_token_secret=secret,
    )

    with pytest.raises(ValueError, match="lease has not expired"):
        WorkflowModuleLedgerRecorder(binding).recover_expired_attempt(
            workflow_execution_id=EXECUTION_ID,
            attempt_id=ATTEMPT_ID,
            observed_at_utc="2026-08-02T12:01:59Z",
        )

    first = WorkflowModuleLedgerRecorder(binding).recover_expired_attempt(
        workflow_execution_id=EXECUTION_ID,
        attempt_id=ATTEMPT_ID,
        observed_at_utc="2026-08-02T12:02:01Z",
    )
    replay = WorkflowModuleLedgerRecorder(binding).recover_expired_attempt(
        workflow_execution_id=EXECUTION_ID,
        attempt_id=ATTEMPT_ID,
        observed_at_utc="2026-08-02T12:03:00Z",
    )

    assert first.commit_receipt.replayed is False
    assert replay.commit_receipt.replayed is True
    trace = store.load_trace(EXECUTION_ID)
    terminal = trace.records_of_type(WorkflowAttemptRecord)[0]
    orphaned = trace.records_of_type(AttemptOrphanedRecord)[0]
    assert terminal.period_end_at_utc == "2026-08-02T12:02:00Z"
    assert terminal.failure_class == "orphaned_attempt"
    assert orphaned.reason_code == "attempt_lease_expired"
    # Recovery record timestamps derive from the durable deadline, never the
    # caller's observation instant, so the disposition is byte-deterministic.
    assert orphaned.recorded_at_utc == "2026-08-02T12:02:00Z"
    assert terminal.recorded_at_utc == "2026-08-02T12:02:00Z"


def test_expired_attempt_recovery_rejects_changed_host_secret() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    original_secret = b"original-workflow-secret-material-32-bytes"
    claim = _claim(_derived_claim_token(original_secret))
    store.begin_attempt(
        LegacyAttemptBeginBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_changed_secret_begin",
            start=_start(claim),
            claim=claim,
            grants=(_grant(),),
        )
    )
    recorder = WorkflowModuleLedgerRecorder(
        WorkflowModuleLedgerBinding(
            record_store=store,
            entitlement_snapshot_hash=ENTITLEMENT_HASH,
            claim_token_secret=b"different-workflow-secret-material-32-bytes",
        )
    )

    with pytest.raises(PermissionError, match="secret differs"):
        recorder.recover_expired_attempt(
            workflow_execution_id=EXECUTION_ID,
            attempt_id=ATTEMPT_ID,
            observed_at_utc="2026-08-02T12:02:01Z",
        )

    assert store.load_trace(EXECUTION_ID).records_of_type(WorkflowAttemptRecord) == ()


def test_duplicate_live_orphan_does_not_evict_a_newer_retry_claim() -> None:
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
    assert store.orphan_attempt(claim, batch).commit_receipt.replayed is False

    # A newer retry attempt B re-claims the same logical dispatch.
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
    store.begin_attempt(
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

    # An at-least-once DUPLICATE of the dead attempt's orphan arrives AFTER B
    # re-claimed the dispatch. It is a valid idempotent replay but must not
    # evict B's live claim.
    assert store.orphan_attempt(claim, batch).commit_receipt.replayed is True

    assert store._active_claims[(EXECUTION_ID, DISPATCH_ID, VARIANT_ID)] == (
        second_claim.attempt_id,
        second_start.claim_token_hash,
    )


def test_late_orphan_record_does_not_erase_new_attempt_claim_on_rebuild() -> None:
    committed_batches: list[RuntimeRecordBatch | LegacyRuntimeRecordBatch] = []

    class CapturingStore(InMemoryRuntimeExecutionRecordStore):
        def commit(self, batch):  # type: ignore[no-untyped-def]
            receipt = super().commit(batch)
            if not receipt.replayed:
                committed_batches.append(batch)
            return receipt

    source = CapturingStore()
    _bootstrap(source)
    _begin(source)
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
    source.commit(
        RuntimeRecordBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_terminal_before_orphan",
            records=(terminal,),
        )
    )
    second_claim = AttemptClaim(
        workflow_execution_id=EXECUTION_ID,
        attempt_id="attempt_synthetic_002",
        claim_token="claim-token-abcdefghijklmnopqrstuvwxyz-002",
    )
    second_start = replace(
        _start(second_claim),
        attempt_id=second_claim.attempt_id,
        parent_attempt_id=ATTEMPT_ID,
        attempt_ordinal=2,
        trace_id="trace_synthetic_002",
        request_sha256="5" * 64,
        recorded_at_utc=END,
    )
    second_begin = LegacyAttemptBeginBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_synthetic_retry_before_orphan",
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
    second_begin.validate()
    committed_batches.append(second_begin.as_record_batch())
    committed_batches.append(
        RuntimeRecordBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_late_orphan_disposition",
            records=(
                AttemptOrphanedRecord(
                    orphaned_record_id="attempt_orphaned_synthetic_001",
                    workflow_execution_id=EXECUTION_ID,
                    dispatch_id=DISPATCH_ID,
                    module_run_id=STEP_ID,
                    variant_id=VARIANT_ID,
                    attempt_id=ATTEMPT_ID,
                    reason_code="worker_lost",
                    context_disposition_id="invalidate",
                    recorded_at_utc=orphaned_at,
                ),
            ),
        )
    )

    rebuilt = InMemoryRuntimeExecutionRecordStore.from_committed_batches(
        committed_batches
    )
    receipt = rebuilt.authorize_operation(
        LegacyOperationGrantBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_retry_live_grant",
            claim=second_claim,
            grants=(
                replace(
                    _grant(),
                    grant_id="grant_synthetic_model_003",
                    attempt_id=second_claim.attempt_id,
                    idempotency_key="operation_synthetic_model_003",
                    recorded_at_utc=END,
                ),
            ),
        )
    )

    assert receipt.commit_receipt.replayed is False


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


def test_recovery_after_normal_finalize_is_rejected_as_unnecessary() -> None:
    store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=lambda _: True
    )
    _bootstrap(store)
    secret = b"stable-workflow-execution-secret-32-bytes"
    claim = _claim(_derived_claim_token(secret))
    store.begin_attempt(
        LegacyAttemptBeginBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_finalized_begin",
            start=_start(claim),
            claim=claim,
            grants=(_grant(),),
        )
    )
    store.finalize_attempt(claim, _finalization())
    recorder = WorkflowModuleLedgerRecorder(
        WorkflowModuleLedgerBinding(
            record_store=store,
            entitlement_snapshot_hash=ENTITLEMENT_HASH,
            claim_token_secret=secret,
        )
    )

    with pytest.raises(
        ValueError, match="already finalized with a provider result"
    ):
        recorder.recover_expired_attempt(
            workflow_execution_id=EXECUTION_ID,
            attempt_id=ATTEMPT_ID,
            observed_at_utc="2026-08-02T12:02:01Z",
        )

    assert store.load_trace(EXECUTION_ID).records_of_type(
        AttemptOrphanedRecord
    ) == ()


def test_cross_writer_identical_orphan_converges_as_replay() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    secret = b"stable-workflow-execution-secret-32-bytes"
    claim = _claim(_derived_claim_token(secret))
    store.begin_attempt(
        LegacyAttemptBeginBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_cross_writer_begin",
            start=_start(claim),
            claim=claim,
            grants=(_grant(),),
        )
    )
    WorkflowModuleLedgerRecorder(
        WorkflowModuleLedgerBinding(
            record_store=store,
            entitlement_snapshot_hash=ENTITLEMENT_HASH,
            claim_token_secret=secret,
        )
    ).recover_expired_attempt(
        workflow_execution_id=EXECUTION_ID,
        attempt_id=ATTEMPT_ID,
        observed_at_utc="2026-08-02T12:02:01Z",
    )
    trace = store.load_trace(EXECUTION_ID)
    committed_terminal = trace.records_of_type(WorkflowAttemptRecord)[0]
    committed_orphan = trace.records_of_type(AttemptOrphanedRecord)[0]

    # A second writer that derived the same deterministic disposition commits
    # under its own transaction_id and must converge on the committed fact.
    receipt = store.orphan_attempt(
        claim,
        AttemptOrphaningBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_other_caller_orphan",
            terminal_attempt=committed_terminal,
            orphaned=committed_orphan,
        ),
    )

    assert receipt.commit_receipt.replayed is True
    assert receipt.commit_receipt.transaction_id == "transaction_other_caller_orphan"
    assert receipt.orphaned_record_id == committed_orphan.orphaned_record_id
    final = store.load_trace(EXECUTION_ID)
    assert final.records_of_type(AttemptOrphanedRecord) == (committed_orphan,)
    assert final.records_of_type(WorkflowAttemptRecord) == (committed_terminal,)


def test_begin_attempt_rejects_reclaim_of_terminal_attempt() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    secret = b"stable-workflow-execution-secret-32-bytes"
    recorder = WorkflowModuleLedgerRecorder(
        WorkflowModuleLedgerBinding(
            record_store=store,
            entitlement_snapshot_hash=ENTITLEMENT_HASH,
            claim_token_secret=secret,
        )
    )
    # The recorder reads only the identity and closure fields from the
    # execution request when durably claiming an Attempt.
    request = SimpleNamespace(
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        module_run_id=STEP_ID,
        request_sha256="3" * 64,
        input_closure_sha256=sha256_json(["artifact-ref:synthetic-input-001"]),
    )
    variant = SimpleNamespace(variant_id=VARIANT_ID)
    profile = SimpleNamespace(release_sha256="2" * 64, timeout_seconds=120)

    first = recorder.begin_attempt(
        request=request,
        variant=variant,
        profile=profile,
        attempt_id=ATTEMPT_ID,
        attempt_ordinal=1,
        recorded_at_utc=START,
    )
    replayed = recorder.begin_attempt(
        request=request,
        variant=variant,
        profile=profile,
        attempt_id=ATTEMPT_ID,
        attempt_ordinal=1,
        recorded_at_utc="2026-08-02T12:00:05Z",
    )
    assert replayed.claim_token == first.claim_token

    recorder.recover_expired_attempt(
        workflow_execution_id=EXECUTION_ID,
        attempt_id=ATTEMPT_ID,
        observed_at_utc="2026-08-02T12:02:01Z",
    )

    with pytest.raises(
        PermissionError, match="already terminal and cannot be re-claimed"
    ):
        recorder.begin_attempt(
            request=request,
            variant=variant,
            profile=profile,
            attempt_id=ATTEMPT_ID,
            attempt_ordinal=1,
            recorded_at_utc="2026-08-02T12:03:00Z",
        )


@pytest.mark.parametrize(
    "observed_at_utc",
    [
        "2026-08-02 12:02:01Z",
        "2026-08-02T12:02Z",
        "20260802T120201Z",
        "2026-08-02T12:02:01+00:00",
        "2026-08-02T12:02:01",
    ],
)
def test_recovery_rejects_non_canonical_observed_at(observed_at_utc: str) -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    secret = b"stable-workflow-execution-secret-32-bytes"
    claim = _claim(_derived_claim_token(secret))
    store.begin_attempt(
        LegacyAttemptBeginBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_observed_at_begin",
            start=_start(claim),
            claim=claim,
            grants=(_grant(),),
        )
    )
    recorder = WorkflowModuleLedgerRecorder(
        WorkflowModuleLedgerBinding(
            record_store=store,
            entitlement_snapshot_hash=ENTITLEMENT_HASH,
            claim_token_secret=secret,
        )
    )

    with pytest.raises(ValueError, match="observed_at_utc"):
        recorder.recover_expired_attempt(
            workflow_execution_id=EXECUTION_ID,
            attempt_id=ATTEMPT_ID,
            observed_at_utc=observed_at_utc,
        )


def test_ledger_binding_requires_committed_invocation_reader() -> None:
    class StoreWithoutInvocationReader:
        commit = staticmethod(lambda batch: None)
        begin_attempt = staticmethod(lambda batch: None)
        authorize_operation = staticmethod(lambda batch: None)
        finalize_attempt = staticmethod(lambda claim, batch: None)
        orphan_attempt = staticmethod(lambda claim, batch: None)
        load_trace = staticmethod(lambda workflow_execution_id: None)

    with pytest.raises(
        ValueError, match="record_store must implement get_committed_invocation"
    ):
        WorkflowModuleLedgerBinding(
            record_store=StoreWithoutInvocationReader(),
            entitlement_snapshot_hash=ENTITLEMENT_HASH,
            claim_token_secret=b"stable-workflow-execution-secret-32-bytes",
        ).validate()


def test_orphan_disposition_vocabulary_is_pinned() -> None:
    def _orphaned(reason_code: str, context_disposition_id: str) -> AttemptOrphanedRecord:
        return AttemptOrphanedRecord(
            orphaned_record_id="attempt_orphaned_synthetic_vocab",
            workflow_execution_id=EXECUTION_ID,
            dispatch_id=DISPATCH_ID,
            module_run_id=STEP_ID,
            variant_id=VARIANT_ID,
            attempt_id=ATTEMPT_ID,
            reason_code=reason_code,
            context_disposition_id=context_disposition_id,
            recorded_at_utc=END,
        )

    for reason_code in ("attempt_lease_expired", "worker_lost", "operator_requested"):
        for context_disposition_id in ("invalidate", "retain"):
            _orphaned(reason_code, context_disposition_id).validate()

    with pytest.raises(ValueError, match="reason_code is outside the contract"):
        _orphaned("cosmic_ray", "invalidate").validate()
    with pytest.raises(
        ValueError, match="context_disposition_id is outside the contract"
    ):
        _orphaned("attempt_lease_expired", "purge").validate()


def _recorder_over(store: InMemoryRuntimeExecutionRecordStore) -> WorkflowModuleLedgerRecorder:
    return WorkflowModuleLedgerRecorder(
        WorkflowModuleLedgerBinding(
            record_store=store,
            entitlement_snapshot_hash=ENTITLEMENT_HASH,
            claim_token_secret=b"stable-workflow-execution-secret-32-bytes",
        )
    )


def _kernel_request() -> SimpleNamespace:
    # The recorder reads only the identity and closure fields from the
    # execution request across begin and finalize.
    return SimpleNamespace(
        workflow_execution_id=EXECUTION_ID,
        dispatch_id=DISPATCH_ID,
        module_run_id=STEP_ID,
        request_sha256="3" * 64,
        input_closure_sha256=sha256_json(["artifact-ref:synthetic-input-001"]),
    )


def test_retry_attempt_finalizes_with_its_durable_start_lineage() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    recorder = _recorder_over(store)
    request = _kernel_request()
    variant = SimpleNamespace(variant_id=VARIANT_ID)
    profile = SimpleNamespace(release_sha256="2" * 64, timeout_seconds=120)

    recorder.begin_attempt(
        request=request,
        variant=variant,
        profile=profile,
        attempt_id=ATTEMPT_ID,
        attempt_ordinal=1,
        recorded_at_utc=START,
    )
    recorder.recover_expired_attempt(
        workflow_execution_id=EXECUTION_ID,
        attempt_id=ATTEMPT_ID,
        observed_at_utc="2026-08-02T12:02:01Z",
    )
    retry_attempt_id = "attempt_synthetic_002"
    recorder.begin_attempt(
        request=request,
        variant=variant,
        profile=profile,
        attempt_id=retry_attempt_id,
        attempt_ordinal=2,
        recorded_at_utc="2026-08-02T12:03:00Z",
        parent_attempt_id=ATTEMPT_ID,
    )
    recorder.finalize_attempt(
        request=request,
        module=SimpleNamespace(
            module_id="module_synthetic", module_version="v1"
        ),
        profile=profile,
        attempt=ModuleAttemptRecord(
            module_run_id=STEP_ID,
            variant_id=VARIANT_ID,
            attempt_id=retry_attempt_id,
            status="completed",
            output_refs=(),
            usage=ModuleUsageObservation(
                input_tokens=None,
                output_tokens=None,
                cache_read_tokens=None,
                cache_creation_tokens=None,
            ),
            failure_class=None,
            period_start_at_utc="2026-08-02T12:03:00Z",
            period_end_at_utc="2026-08-02T12:04:00Z",
            recorded_at_utc="2026-08-02T12:04:00Z",
        ),
        outputs=(),
        artifact_host=SimpleNamespace(),
    )

    trace = store.load_trace(EXECUTION_ID)
    terminals = {
        row.attempt_id: row
        for row in trace.records_of_type(WorkflowAttemptRecord)
    }
    retry_terminal = terminals[retry_attempt_id]
    # The retry finalizes with its true durable lineage instead of being
    # rewritten to a first attempt, so its successful result stays projectable.
    assert retry_terminal.attempt_ordinal == 2
    assert retry_terminal.parent_attempt_id == ATTEMPT_ID
    assert retry_terminal.status == "completed"
    assert retry_terminal.trace_id == stable_runtime_id(
        "trace", EXECUTION_ID, retry_attempt_id
    )
    assert terminals[ATTEMPT_ID].attempt_ordinal == 1
    assert [
        row.attempt_id
        for row in trace.records_of_type(InvocationCommitRecord)
    ] == [retry_attempt_id]


def test_recovery_sweep_terminalizes_only_expired_unterminal_leases() -> None:
    store = InMemoryRuntimeExecutionRecordStore()
    _bootstrap(store)
    slow_variant_id = "variant_synthetic_002"
    store.commit(
        LegacyRuntimeRecordBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_synthetic_slow_variant",
            records=(
                WorkflowModuleExecutionVariantRecord(
                    workflow_execution_id=EXECUTION_ID,
                    module_run_id=STEP_ID,
                    variant_id=slow_variant_id,
                    module_id="module_synthetic",
                    agent_execution_adapter_id="adapter_synthetic",
                    execution_profile_id="profile_synthetic_slow",
                    model_id="model_synthetic",
                    reasoning_profile="effort_synthetic",
                    prompt_sha256="c" * 64,
                    static_module_sha256="d" * 64,
                    input_closure_sha256=sha256_json(
                        ["artifact-ref:synthetic-input-001"]
                    ),
                    entitlement_snapshot_hash=ENTITLEMENT_HASH,
                    agent_execution_adapter_revision="adapter_revision_v1",
                    runtime_version="runtime_v1",
                    tool_policy=("no_tools",),
                    context_mode="stateless",
                    output_schema_sha256="f" * 64,
                    timeout_seconds=3600,
                    max_attempts=2,
                    execution_profile_sha256="2" * 64,
                    recorded_at_utc=START,
                ),
            ),
        )
    )
    recorder = _recorder_over(store)
    request = _kernel_request()
    recorder.begin_attempt(
        request=request,
        variant=SimpleNamespace(variant_id=VARIANT_ID),
        profile=SimpleNamespace(release_sha256="2" * 64, timeout_seconds=120),
        attempt_id=ATTEMPT_ID,
        attempt_ordinal=1,
        recorded_at_utc=START,
    )
    slow_attempt_id = "attempt_synthetic_slow_001"
    recorder.begin_attempt(
        request=request,
        variant=SimpleNamespace(variant_id=slow_variant_id),
        profile=SimpleNamespace(release_sha256="2" * 64, timeout_seconds=3600),
        attempt_id=slow_attempt_id,
        attempt_ordinal=1,
        recorded_at_utc=START,
    )

    with pytest.raises(ValueError, match="observed_at_utc"):
        recorder.recover_expired_attempts(
            workflow_execution_id=EXECUTION_ID,
            observed_at_utc="2026-08-02 12:02:01Z",
        )

    # Observed between the two deadlines: the 120s lease is expired while the
    # 3600s lease is still active and must not be terminalized by the sweep.
    receipts = recorder.recover_expired_attempts(
        workflow_execution_id=EXECUTION_ID,
        observed_at_utc="2026-08-02T12:02:01Z",
    )

    assert [row.orphaned_record_id for row in receipts] == [
        stable_runtime_id("attempt_orphaned", EXECUTION_ID, ATTEMPT_ID)
    ]
    trace = store.load_trace(EXECUTION_ID)
    assert [
        row.attempt_id for row in trace.records_of_type(AttemptOrphanedRecord)
    ] == [ATTEMPT_ID]

    # A repeated sweep at the same instant finds the lease already terminal
    # and recovers nothing further.
    assert (
        recorder.recover_expired_attempts(
            workflow_execution_id=EXECUTION_ID,
            observed_at_utc="2026-08-02T12:02:01Z",
        )
        == ()
    )
