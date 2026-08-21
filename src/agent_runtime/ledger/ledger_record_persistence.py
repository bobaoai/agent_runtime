"""Atomic provider-neutral reference ledger for Agent Runtime executions.

The in-memory implementation is a conformance reference. It keeps no artifact
content or domain meaning. A persistence adapter can implement the same public
protocol while preserving the transaction and grant checks below.
"""

from __future__ import annotations

from dataclasses import replace
from threading import RLock
from typing import Callable, Iterable, Protocol, runtime_checkable

from ..contracts.registry_workflow_definition import ModuleOutcome
from ..contracts.ledger_record_definition import (
    LegacyAttemptBeginBatch,
    AttemptBeginReceipt,
    AttemptClaim,
    AttemptFinalizationBatch,
    AttemptFinalizationReceipt,
    AttemptOrphanedRecord,
    AttemptOrphaningBatch,
    AttemptOrphaningReceipt,
    WorkflowAttemptRecord,
    WorkflowAttemptStartedRecord,
    BackendAcknowledgementReceipt,
    BackendAcknowledgementRecord,
    CheckpointRecord,
    ContextBinding,
    ContextEvent,
    CommitReceipt,
    LegacyExecutionEntitlementSnapshot,
    ExecutionInputRef,
    ExecutionOutputRef,
    ExternalEventApplicationRecord,
    InvocationCommitRecord,
    ModelCallRecord,
    LegacyOperationGrantBatch,
    LegacyOperationGrantReceipt,
    OutcomeCommitBatch,
    OutcomeCommitReceipt,
    RuntimeExecutionTrace,
    PersistedRuntimeRecord,
    LegacyRuntimeRecordBatch,
    RuntimeRecordBatch,
    LegacyModuleCapabilityGrant,
    WorkflowModuleRunRecord,
    WorkflowModuleExecutionVariantRecord,
    StaleOutputRecord,
    ToolCallRecord,
    UsageEvent,
    WorkflowExecutionRecord,
    attempt_output_bundle_sha256,
    sha256_text,
    stable_runtime_id,
)


@runtime_checkable
class RuntimeExecutionRecordStore(Protocol):
    """Atomic persistence boundary consumed by Runtime execution services."""

    def commit(
        self,
        batch: RuntimeRecordBatch | LegacyRuntimeRecordBatch,
    ) -> CommitReceipt:
        """Atomically append one validated Runtime record batch."""

        ...

    def begin_attempt(self, batch: LegacyAttemptBeginBatch) -> AttemptBeginReceipt:
        """Commit an Attempt start and static grants before provider entry."""

        ...

    def authorize_operation(
        self,
        batch: LegacyOperationGrantBatch,
    ) -> LegacyOperationGrantReceipt:
        """Commit exact dynamic grants while the Attempt claim is active."""

        ...

    def finalize_attempt(
        self,
        claim: AttemptClaim,
        batch: AttemptFinalizationBatch,
    ) -> AttemptFinalizationReceipt:
        """Compare the active claim and atomically terminalize the Attempt."""

        ...

    def orphan_attempt(
        self,
        claim: AttemptClaim,
        batch: AttemptOrphaningBatch,
    ) -> AttemptOrphaningReceipt:
        """Terminalize an unrecoverable started Attempt before retry."""

        ...

    def commit_outcome(self, batch: OutcomeCommitBatch) -> OutcomeCommitReceipt:
        """Commit one ModuleOutcome and pre-ack checkpoint atomically."""

        ...

    def acknowledge_backend(
        self,
        record: BackendAcknowledgementRecord,
    ) -> BackendAcknowledgementReceipt:
        """Append one idempotent backend acknowledgement."""

        ...

    def get_committed_outcome(
        self,
        workflow_execution_id: str,
        dispatch_id: str,
    ) -> ModuleOutcome | None:
        """Return the already committed dispatch result, if any."""

        ...

    def get_committed_invocation(
        self,
        workflow_execution_id: str,
        dispatch_id: str,
    ) -> InvocationCommitRecord | None:
        """Return a finalized provider result before Domain Outcome commit."""

        ...

    def load_trace(
        self,
        workflow_execution_id: str,
    ) -> RuntimeExecutionTrace:
        """Return the immutable content-free execution trace."""

        ...


class InMemoryRuntimeExecutionRecordStore:
    """Thread-safe atomic reference store with fail-closed lineage validation."""

    def __init__(
        self,
        *,
        execution_output_integrity_check: Callable[[ExecutionOutputRef], bool]
        | None = None,
    ) -> None:
        self._lock = RLock()
        self._execution_output_integrity_check = execution_output_integrity_check
        self._records: dict[str, tuple[PersistedRuntimeRecord, ...]] = {}
        self._transactions: dict[tuple[str, str], tuple[str, CommitReceipt]] = {}
        self._receipts: dict[str, tuple[CommitReceipt, ...]] = {}
        self._outcomes: dict[tuple[str, str], ModuleOutcome] = {}
        self._invocations: dict[tuple[str, str], InvocationCommitRecord] = {}
        self._active_claims: dict[tuple[str, str, str], tuple[str, str]] = {}

    @classmethod
    def from_committed_batches(
        cls,
        batches: Iterable[RuntimeRecordBatch | LegacyRuntimeRecordBatch],
        *,
        execution_output_integrity_check: Callable[[ExecutionOutputRef], bool]
        | None = None,
    ) -> "InMemoryRuntimeExecutionRecordStore":
        """Rebuild validated state and active claims from committed batches."""

        store = cls(
            execution_output_integrity_check=execution_output_integrity_check,
        )
        materialized = tuple(batches)
        records_by_execution: dict[str, list[PersistedRuntimeRecord]] = {}
        for batch in materialized:
            batch.validate()
            records_by_execution.setdefault(batch.workflow_execution_id, []).extend(
                batch.records
            )
        # A restored prefix has already crossed an atomic persistence boundary.
        # Validate its semantic closure once per execution, then rebuild the
        # in-memory indexes without replaying every historical prefix through
        # _validate_candidate (which made an n-batch restore quadratic).
        for workflow_execution_id, records in records_by_execution.items():
            store._validate_candidate(workflow_execution_id, tuple(records), ())
        for batch in materialized:
            store._record_validated_batch(batch)
        store._rebuild_active_claims()
        return store

    def _rebuild_active_claims(self) -> None:
        """Derive live claim state from append-only start and terminal facts."""

        self._active_claims.clear()
        starts: dict[tuple[str, str], WorkflowAttemptStartedRecord] = {}
        for workflow_execution_id, records in self._records.items():
            for record in records:
                if isinstance(record, WorkflowAttemptStartedRecord):
                    starts[(workflow_execution_id, record.attempt_id)] = record
                    self._active_claims[
                        (
                            workflow_execution_id,
                            record.dispatch_id,
                            record.variant_id,
                        )
                    ] = (record.attempt_id, record.claim_token_hash)
                    continue
                if not isinstance(
                    record,
                    (WorkflowAttemptRecord, AttemptOrphanedRecord),
                ):
                    continue
                start = starts.get((workflow_execution_id, record.attempt_id))
                if start is not None:
                    self._release_claim_if_owned(workflow_execution_id, start)

    def _release_claim_if_owned(self, workflow_execution_id, start) -> None:
        """Release the active claim only while it still belongs to this Attempt.

        A duplicate or late terminal for an already-dead Attempt is a valid
        idempotent replay, but it must never evict a newer retry Attempt that
        has re-claimed the same logical dispatch. The pop is therefore guarded
        by exact claim ownership, identically on the live finalize/orphan paths
        and on reference reconstruction.
        """

        claim_key = (
            workflow_execution_id,
            start.dispatch_id,
            start.variant_id,
        )
        if self._active_claims.get(claim_key) == (
            start.attempt_id,
            start.claim_token_hash,
        ):
            self._active_claims.pop(claim_key, None)

    def begin_attempt(self, batch: LegacyAttemptBeginBatch) -> AttemptBeginReceipt:
        """Commit the claim boundary before any external operation can start."""

        batch.validate()
        start = batch.start
        claim_key = (
            batch.workflow_execution_id,
            start.dispatch_id,
            start.variant_id,
        )
        with self._lock:
            active = self._active_claims.get(claim_key)
            expected = (start.attempt_id, start.claim_token_hash)
            if active is not None and active != expected:
                raise RuntimeError("logical dispatch already has an active Attempt claim")
            receipt = self.commit(batch.as_record_batch())
            terminals = self.load_trace(batch.workflow_execution_id).records_of_type(
                WorkflowAttemptRecord
            )
            if not any(row.attempt_id == start.attempt_id for row in terminals):
                self._active_claims[claim_key] = expected
            return AttemptBeginReceipt(commit_receipt=receipt, claim=batch.claim)

    def authorize_operation(
        self,
        batch: LegacyOperationGrantBatch,
    ) -> LegacyOperationGrantReceipt:
        """Append exact grants only while the matching claim remains active."""

        batch.validate()
        with self._lock:
            start = self._started_attempt(batch.claim)
            self._require_active_claim(batch.claim, start)
            for grant in batch.grants:
                if (
                    grant.module_run_id != start.module_run_id
                    or grant.variant_id != start.variant_id
                ):
                    raise PermissionError("operation grant differs from active Attempt")
            receipt = self.commit(batch.as_record_batch())
            return LegacyOperationGrantReceipt(
                commit_receipt=receipt,
                grant_ids=tuple(grant.grant_id for grant in batch.grants),
            )

    def finalize_attempt(
        self,
        claim: AttemptClaim,
        batch: AttemptFinalizationBatch,
    ) -> AttemptFinalizationReceipt:
        """Compare the claim and publish exactly one terminal provider result."""

        claim.validate()
        batch.validate()
        if (
            claim.workflow_execution_id != batch.workflow_execution_id
            or claim.attempt_id != batch.terminal_attempt.attempt_id
        ):
            raise PermissionError("finalization claim differs from terminal Attempt")
        with self._lock:
            start = self._started_attempt(claim)
            trace = self.load_trace(batch.workflow_execution_id)
            prior_terminal = next(
                (
                    row
                    for row in trace.records_of_type(WorkflowAttemptRecord)
                    if row.attempt_id == claim.attempt_id
                ),
                None,
            )
            if prior_terminal is None:
                self._require_active_claim(claim, start)
            elif prior_terminal != batch.terminal_attempt:
                raise ValueError("Attempt already has a different terminal record")
            execution_outputs = {
                record.output_ref: record
                for record in batch.records
                if isinstance(record, ExecutionOutputRef)
            }
            if batch.terminal_attempt.execution_output_refs:
                if self._execution_output_integrity_check is None:
                    raise RuntimeError(
                        "Attempt finalization requires an immutable "
                        "ExecutionOutputRef integrity check"
                    )
                for output_ref in batch.terminal_attempt.execution_output_refs:
                    output = execution_outputs.get(output_ref)
                    if output is None or not self._execution_output_integrity_check(
                        output
                    ):
                        raise FileNotFoundError(
                            f"verified execution-output bytes are missing: {output_ref}"
                        )
            receipt = self.commit(batch.as_record_batch())
            self._release_claim_if_owned(batch.workflow_execution_id, start)
            return AttemptFinalizationReceipt(
                commit_receipt=receipt,
                invocation_commit_id=batch.invocation_commit.invocation_commit_id,
            )

    def orphan_attempt(
        self,
        claim: AttemptClaim,
        batch: AttemptOrphaningBatch,
    ) -> AttemptOrphaningReceipt:
        """Append a failed terminal and bounded orphan disposition atomically."""

        claim.validate()
        batch.validate()
        if (
            claim.workflow_execution_id != batch.workflow_execution_id
            or claim.attempt_id != batch.terminal_attempt.attempt_id
        ):
            raise PermissionError("orphan claim differs from terminal Attempt")
        with self._lock:
            start = self._started_attempt(claim)
            if batch.orphaned.dispatch_id != start.dispatch_id:
                raise ValueError("orphan disposition dispatch differs from Attempt start")
            trace = self.load_trace(batch.workflow_execution_id)
            prior_terminal = next(
                (
                    row
                    for row in trace.records_of_type(WorkflowAttemptRecord)
                    if row.attempt_id == claim.attempt_id
                ),
                None,
            )
            prior_orphaned = next(
                (
                    row
                    for row in trace.records_of_type(AttemptOrphanedRecord)
                    if row.attempt_id == claim.attempt_id
                ),
                None,
            )
            if prior_terminal is None and prior_orphaned is None:
                self._require_active_claim(claim, start)
            elif (
                prior_terminal != batch.terminal_attempt
                or prior_orphaned != batch.orphaned
            ):
                raise ValueError("Attempt already has a different orphan disposition")
            else:
                # The identical disposition is already committed — possibly
                # under another caller's transaction. Converge on the committed
                # fact as a replay instead of re-appending records.
                self._release_claim_if_owned(batch.workflow_execution_id, start)
                record_batch = batch.as_record_batch()
                return AttemptOrphaningReceipt(
                    commit_receipt=CommitReceipt(
                        workflow_execution_id=batch.workflow_execution_id,
                        transaction_id=batch.transaction_id,
                        transaction_sha256=record_batch.transaction_sha256,
                        record_count=len(record_batch.records),
                        committed_outcome_refs=(),
                        replayed=True,
                    ),
                    orphaned_record_id=batch.orphaned.orphaned_record_id,
                )
            receipt = self.commit(batch.as_record_batch())
            self._release_claim_if_owned(batch.workflow_execution_id, start)
            return AttemptOrphaningReceipt(
                commit_receipt=receipt,
                orphaned_record_id=batch.orphaned.orphaned_record_id,
            )

    def commit_outcome(self, batch: OutcomeCommitBatch) -> OutcomeCommitReceipt:
        """Commit the Outcome and its local recovery checkpoint together."""

        batch.validate()
        receipt = self.commit(batch.as_record_batch())
        return OutcomeCommitReceipt(
            commit_receipt=receipt,
            checkpoint_id=batch.checkpoint.checkpoint_id,
        )

    def acknowledge_backend(
        self,
        record: BackendAcknowledgementRecord,
    ) -> BackendAcknowledgementReceipt:
        """Append a backend acknowledgement without mutating its checkpoint."""

        record.validate()
        receipt = self.commit(
            RuntimeRecordBatch(
                workflow_execution_id=record.workflow_execution_id,
                transaction_id=stable_runtime_id(
                    "transaction",
                    record.workflow_execution_id,
                    record.backend_acknowledgement_id,
                ),
                records=(record,),
            )
        )
        return BackendAcknowledgementReceipt(
            commit_receipt=receipt,
            backend_acknowledgement_id=record.backend_acknowledgement_id,
        )

    def commit(
        self,
        batch: RuntimeRecordBatch | LegacyRuntimeRecordBatch,
    ) -> CommitReceipt:
        """Internal atomic primitive retained for host migration callers."""

        batch.validate()
        transaction_sha256 = batch.transaction_sha256
        transaction_key = (batch.workflow_execution_id, batch.transaction_id)
        with self._lock:
            prior = self._transactions.get(transaction_key)
            if prior is not None:
                prior_sha256, prior_receipt = prior
                if prior_sha256 != transaction_sha256:
                    raise ValueError(
                        "transaction_id reuse with different RuntimeRecordBatch"
                    )
                return replace(prior_receipt, replayed=True)

            existing = self._records.get(batch.workflow_execution_id, ())
            candidate = existing + batch.records
            self._validate_candidate(batch.workflow_execution_id, candidate, existing)

            return self._record_validated_batch(batch)

    def _record_validated_batch(
        self,
        batch: RuntimeRecordBatch | LegacyRuntimeRecordBatch,
    ) -> CommitReceipt:
        """Index one batch whose complete execution closure was already validated."""

        transaction_sha256 = batch.transaction_sha256
        transaction_key = (batch.workflow_execution_id, batch.transaction_id)
        prior = self._transactions.get(transaction_key)
        if prior is not None:
            prior_sha256, prior_receipt = prior
            if prior_sha256 != transaction_sha256:
                raise ValueError(
                    "transaction_id reuse with different RuntimeRecordBatch"
                )
            return replace(prior_receipt, replayed=True)
        committed_outcomes = tuple(
            record.outcome_ref
            for record in batch.records
            if isinstance(record, ModuleOutcome)
        )
        receipt = CommitReceipt(
            workflow_execution_id=batch.workflow_execution_id,
            transaction_id=batch.transaction_id,
            transaction_sha256=transaction_sha256,
            record_count=len(batch.records),
            committed_outcome_refs=committed_outcomes,
            replayed=False,
        )
        self._records[batch.workflow_execution_id] = (
            self._records.get(batch.workflow_execution_id, ()) + batch.records
        )
        self._transactions[transaction_key] = (transaction_sha256, receipt)
        self._receipts[batch.workflow_execution_id] = (
            self._receipts.get(batch.workflow_execution_id, ()) + (receipt,)
        )
        for record in batch.records:
            if isinstance(record, ModuleOutcome):
                self._outcomes[(batch.workflow_execution_id, record.dispatch_id)] = record
            elif isinstance(record, InvocationCommitRecord):
                self._invocations[
                    (batch.workflow_execution_id, record.dispatch_id)
                ] = record
        return receipt

    def _started_attempt(self, claim: AttemptClaim) -> WorkflowAttemptStartedRecord:
        trace = self.load_trace(claim.workflow_execution_id)
        start = next(
            (
                row
                for row in trace.records_of_type(WorkflowAttemptStartedRecord)
                if row.attempt_id == claim.attempt_id
            ),
            None,
        )
        if start is None:
            raise PermissionError("Attempt claim has no durable start record")
        if sha256_text(claim.claim_token) != start.claim_token_hash:
            raise PermissionError("Attempt claim token does not match durable start")
        return start

    def _require_active_claim(
        self,
        claim: AttemptClaim,
        start: WorkflowAttemptStartedRecord,
    ) -> None:
        active = self._active_claims.get(
            (claim.workflow_execution_id, start.dispatch_id, start.variant_id)
        )
        if active != (claim.attempt_id, start.claim_token_hash):
            raise PermissionError("Attempt claim is not active")

    def get_committed_outcome(
        self,
        workflow_execution_id: str,
        dispatch_id: str,
    ) -> ModuleOutcome | None:
        """Return one immutable result for stable crash-after-commit replay."""

        with self._lock:
            return self._outcomes.get((workflow_execution_id, dispatch_id))

    def get_committed_invocation(
        self,
        workflow_execution_id: str,
        dispatch_id: str,
    ) -> InvocationCommitRecord | None:
        """Return one immutable provider commit for crash-safe Outcome recovery."""

        with self._lock:
            return self._invocations.get((workflow_execution_id, dispatch_id))

    def load_trace(
        self,
        workflow_execution_id: str,
    ) -> RuntimeExecutionTrace:
        """Return a snapshot that cannot mutate the authoritative store."""

        with self._lock:
            return RuntimeExecutionTrace(
                workflow_execution_id=workflow_execution_id,
                records=tuple(self._records.get(workflow_execution_id, ())),
                commit_receipts=tuple(self._receipts.get(workflow_execution_id, ())),
            )

    def _validate_candidate(
        self,
        workflow_execution_id: str,
        candidate: tuple[PersistedRuntimeRecord, ...],
        existing: tuple[PersistedRuntimeRecord, ...],
    ) -> None:
        executions = tuple(
            record for record in candidate if isinstance(record, WorkflowExecutionRecord)
        )
        if len(executions) > 1:
            raise ValueError("Workflow Execution record is immutable and unique")
        execution = executions[0] if executions else None
        if execution is None:
            raise ValueError("execution trace requires WorkflowExecutionRecord")

        entitlement_hashes = {
            record.entitlement_snapshot_hash
            for record in candidate
            if isinstance(record, LegacyExecutionEntitlementSnapshot)
        }
        if execution is not None:
            entitlement_hashes.add(execution.entitlement_snapshot_hash)
        if len(entitlement_hashes) > 1:
            raise PermissionError("execution entitlement snapshot hash changed")
        frozen_entitlement_hash = next(iter(entitlement_hashes), None)

        for record in candidate:
            record_execution_id = getattr(record, "workflow_execution_id", None)
            if (
                record_execution_id is not None
                and record_execution_id != workflow_execution_id
            ):
                raise ValueError("execution trace contains cross-execution record")
            record_entitlement_hash = getattr(
                record, "entitlement_snapshot_hash", None
            )
            if (
                frozen_entitlement_hash is not None
                and record_entitlement_hash is not None
                and record_entitlement_hash != frozen_entitlement_hash
            ):
                raise PermissionError("record entitlement snapshot hash changed")

        module_runs = self._unique_map(candidate, WorkflowModuleRunRecord, "module_run_id")
        variants = self._unique_map(candidate, WorkflowModuleExecutionVariantRecord, "variant_id")
        attempt_starts = self._unique_map(
            candidate,
            WorkflowAttemptStartedRecord,
            "attempt_id",
        )
        attempts = self._unique_map(candidate, WorkflowAttemptRecord, "attempt_id")
        invocation_commits = self._unique_map(
            candidate,
            InvocationCommitRecord,
            "invocation_commit_id",
        )
        orphaned_attempts = self._unique_map(
            candidate,
            AttemptOrphanedRecord,
            "orphaned_record_id",
        )
        stale_outputs = self._unique_map(
            candidate,
            StaleOutputRecord,
            "stale_output_id",
        )
        execution_inputs = self._unique_map(
            candidate,
            ExecutionInputRef,
            "execution_input_id",
        )
        execution_outputs = self._unique_map(
            candidate,
            ExecutionOutputRef,
            "execution_output_id",
        )
        grants = self._unique_map(candidate, LegacyModuleCapabilityGrant, "grant_id")
        model_calls = self._unique_map(candidate, ModelCallRecord, "model_call_id")
        tool_calls = self._unique_map(candidate, ToolCallRecord, "tool_call_id")
        usage_events = self._unique_map(candidate, UsageEvent, "usage_event_id")
        event_applications = self._unique_map(
            candidate,
            ExternalEventApplicationRecord,
            "event_id",
        )
        context_bindings = self._unique_map(
            candidate,
            ContextBinding,
            "context_binding_id",
        )
        context_events = self._unique_map(
            candidate,
            ContextEvent,
            "context_event_id",
        )
        checkpoints = self._unique_map(
            candidate,
            CheckpointRecord,
            "checkpoint_id",
        )
        backend_acknowledgements = self._unique_map(
            candidate,
            BackendAcknowledgementRecord,
            "backend_acknowledgement_id",
        )

        operations: dict[str, ModelCallRecord | ToolCallRecord] = {
            **model_calls,
            **tool_calls,
        }
        if len(operations) != len(model_calls) + len(tool_calls):
            raise ValueError("model and tool operation IDs must be globally unique")

        execution_inputs_by_ref: dict[str, ExecutionInputRef] = {}
        for input_binding in execution_inputs.values():
            prior = execution_inputs_by_ref.setdefault(
                input_binding.input_ref,
                input_binding,
            )
            if prior is not input_binding:
                raise ValueError(
                    f"duplicate ExecutionInputRef reference: {input_binding.input_ref}"
                )

        if any(
            input_ref not in execution_inputs_by_ref
            for input_ref in execution.execution_input_package_refs
        ):
            raise ValueError(
                "Workflow Execution package references an unknown ExecutionInputRef"
            )

        execution_outputs_by_ref: dict[str, ExecutionOutputRef] = {}
        for output in execution_outputs.values():
            prior = execution_outputs_by_ref.setdefault(output.output_ref, output)
            if prior is not output:
                raise ValueError(
                    f"duplicate ExecutionOutputRef reference: {output.output_ref}"
                )
        for output in execution_outputs.values():
            if any(
                source_ref not in execution_inputs_by_ref
                and source_ref not in execution_outputs_by_ref
                for source_ref in output.source_artifact_refs
            ):
                raise ValueError(
                    "ExecutionOutputRef source is outside the frozen execution "
                    "package and prior outputs"
                )
            if output.module_run_id is None:
                continue
            module = module_runs.get(output.module_run_id)
            if module is None:
                raise ValueError(
                    "ExecutionOutputRef references an unknown producing Module Run"
                )
            if output.variant_id is None:
                continue
            variant = variants.get(output.variant_id)
            if variant is None or variant.module_run_id != output.module_run_id:
                raise ValueError(
                    "ExecutionOutputRef producer Variant does not belong to its Module Run"
                )
            if output.attempt_id is None:
                continue
            attempt = attempts.get(output.attempt_id)
            if attempt is None or attempt.variant_id != output.variant_id:
                raise ValueError(
                    "ExecutionOutputRef producer Attempt does not belong to its Variant"
                )

        for module in module_runs.values():
            if any(
                input_ref not in execution_inputs_by_ref
                and input_ref not in execution_outputs_by_ref
                for input_ref in module.input_refs
            ):
                raise ValueError(
                    "Module Run input is outside the frozen execution package and prior outputs"
                )

        for variant in variants.values():
            module = module_runs.get(variant.module_run_id)
            if module is None:
                raise ValueError("Variant references an unknown Module Run")
            if variant.module_id != module.module_id:
                raise ValueError("Variant module does not match its Module Run")
            if variant.input_closure_sha256 != module.input_closure_sha256:
                raise ValueError("Variant input closure hash differs from its Module Run")

        for start in attempt_starts.values():
            variant = variants.get(start.variant_id)
            if variant is None:
                raise ValueError("Attempt start references an unknown Variant")
            if start.module_run_id != variant.module_run_id:
                raise ValueError("Attempt start Variant does not belong to its Module Run")
            if start.attempt_ordinal > variant.max_attempts:
                raise ValueError("Attempt start ordinal exceeds Variant max_attempts")
            if start.input_closure_sha256 != variant.input_closure_sha256:
                raise ValueError("Attempt start input closure differs from Variant")
            if start.execution_profile_sha256 != variant.execution_profile_sha256:
                raise ValueError("Attempt start execution profile differs from Variant")
            if start.entitlement_snapshot_hash != variant.entitlement_snapshot_hash:
                raise PermissionError("Attempt start entitlement differs from Variant")
            if start.timeout_seconds != variant.timeout_seconds:
                raise ValueError("Attempt start timeout differs from Variant")
            if start.parent_attempt_id is not None:
                parent = attempt_starts.get(start.parent_attempt_id)
                if parent is None:
                    raise ValueError("Attempt start references an unknown parent Attempt")
                if parent.variant_id != start.variant_id:
                    raise ValueError("parent Attempt start belongs to another Variant")

        starts_by_variant: dict[str, list[WorkflowAttemptStartedRecord]] = {}
        for start in attempt_starts.values():
            starts_by_variant.setdefault(start.variant_id, []).append(start)
        for variant_id, rows in starts_by_variant.items():
            ordinals = sorted(row.attempt_ordinal for row in rows)
            if ordinals != list(range(1, len(rows) + 1)):
                raise ValueError(
                    f"Attempt start ordinals for Variant {variant_id!r} must be "
                    "unique and contiguous from 1"
                )

        active_starts: dict[tuple[str, str], WorkflowAttemptStartedRecord] = {}
        for start in attempt_starts.values():
            if start.attempt_id in attempts:
                continue
            active_key = (start.dispatch_id, start.variant_id)
            prior_active = active_starts.setdefault(active_key, start)
            if prior_active is not start:
                raise RuntimeError(
                    "logical dispatch has multiple unterminated Attempt starts"
                )

        for attempt in attempts.values():
            variant = variants.get(attempt.variant_id)
            if variant is None:
                raise ValueError("Attempt references an unknown Variant")
            if attempt.module_run_id != variant.module_run_id:
                raise ValueError("Attempt Variant does not belong to its Module Run")
            if attempt.attempt_ordinal > variant.max_attempts:
                raise ValueError("Attempt ordinal exceeds Variant max_attempts")
            start = attempt_starts.get(attempt.attempt_id)
            if start is not None:
                for field in (
                    "workflow_execution_id",
                    "module_run_id",
                    "variant_id",
                    "parent_attempt_id",
                    "attempt_ordinal",
                    "trace_id",
                ):
                    if getattr(attempt, field) != getattr(start, field):
                        raise ValueError(f"terminal Attempt {field} differs from start")
                if attempt.period_start_at_utc != start.recorded_at_utc:
                    raise ValueError("terminal Attempt period start differs from start record")
                if _parse_utc(attempt.period_end_at_utc) > start.deadline_at():
                    raise ValueError("terminal Attempt ended after its execution deadline")
            if attempt.parent_attempt_id is not None:
                parent = attempts.get(attempt.parent_attempt_id)
                if parent is None:
                    raise ValueError("Attempt references an unknown parent Attempt")
                if parent.variant_id != attempt.variant_id:
                    raise ValueError("parent Attempt belongs to another Variant")
            for output_ref in attempt.execution_output_refs:
                output = execution_outputs_by_ref.get(output_ref)
                if output is None:
                    raise ValueError(
                        "Attempt output references an unknown ExecutionOutputRef"
                    )
                if (
                    output.module_run_id != attempt.module_run_id
                    or output.variant_id != attempt.variant_id
                    or output.attempt_id != attempt.attempt_id
                ):
                    raise ValueError(
                        "Attempt ExecutionOutputRef has different producer lineage"
                    )

        attempts_by_variant: dict[str, list[WorkflowAttemptRecord]] = {}
        for attempt in attempts.values():
            attempts_by_variant.setdefault(attempt.variant_id, []).append(attempt)
        for variant_id, rows in attempts_by_variant.items():
            ordinals = sorted(row.attempt_ordinal for row in rows)
            if ordinals != list(range(1, len(rows) + 1)):
                raise ValueError(
                    f"Attempt ordinals for Variant {variant_id!r} must be unique "
                    "and contiguous from 1"
                )

        for application in event_applications.values():
            output = execution_outputs_by_ref.get(application.decision_artifact_ref)
            input_binding = execution_inputs_by_ref.get(
                application.decision_artifact_ref
            )
            if output is None and input_binding is None:
                raise ValueError(
                    "ExternalEventApplicationRecord decision references an unknown "
                    "input or output"
                )
            decision_sha256 = (
                output.output_sha256
                if output is not None
                else input_binding.input_sha256
            )
            if decision_sha256 != application.decision_artifact_sha256:
                raise ValueError(
                    "ExternalEventApplicationRecord decision Artifact hash differs"
                )
            if application.graph_sha256 != execution.graph_sha256:
                raise ValueError(
                    "ExternalEventApplicationRecord graph hash differs from execution"
                )

        for binding in context_bindings.values():
            variant = variants.get(binding.variant_id)
            if variant is None or variant.module_run_id != binding.module_run_id:
                raise ValueError("ContextBinding lacks complete Module/Variant lineage")

        for event in context_events.values():
            binding = context_bindings.get(event.context_binding_id)
            if binding is None:
                raise ValueError("ContextEvent references an unknown ContextBinding")
            if (
                event.module_run_id != binding.module_run_id
                or event.variant_id != binding.variant_id
            ):
                raise ValueError("ContextEvent lineage differs from its ContextBinding")
            if event.context_ref is not None and event.context_ref != binding.context_ref:
                raise ValueError("ContextEvent ref differs from its ContextBinding")
            if event.attempt_id is not None:
                attempt = attempts.get(event.attempt_id)
                if attempt is None or attempt.variant_id != event.variant_id:
                    raise ValueError("ContextEvent Attempt differs from its Variant")

        grant_idempotency_keys: set[str] = set()
        for grant in grants.values():
            if grant.idempotency_key in grant_idempotency_keys:
                raise PermissionError("LegacyModuleCapabilityGrant idempotency key was replayed")
            grant_idempotency_keys.add(grant.idempotency_key)
            module = module_runs.get(grant.module_run_id)
            variant = variants.get(grant.variant_id)
            attempt_lineage = attempt_starts.get(grant.attempt_id) or attempts.get(
                grant.attempt_id
            )
            if module is None or variant is None or attempt_lineage is None:
                raise ValueError(
                    "LegacyModuleCapabilityGrant lacks complete Module/Variant/Attempt lineage"
                )
            if getattr(variant, "module_run_id") != grant.module_run_id:
                raise ValueError("grant Variant does not belong to its Module Run")
            if getattr(attempt_lineage, "variant_id") != grant.variant_id:
                raise ValueError("grant Attempt does not belong to its Variant")
            start = attempt_starts.get(grant.attempt_id)
            if start is not None:
                grant_time = _parse_utc(grant.recorded_at_utc)
                if grant_time < _parse_utc(start.recorded_at_utc):
                    raise PermissionError("LegacyModuleCapabilityGrant precedes Attempt start")
                if grant_time > start.deadline_at():
                    raise PermissionError("LegacyModuleCapabilityGrant starts after Attempt deadline")

        grant_operations: dict[str, set[str]] = {}
        existing_attempt_ids = {
            record.attempt_id
            for record in existing
            if isinstance(record, WorkflowAttemptRecord)
        }
        existing_operation_ids = {
            record.operation_id
            for record in existing
            if isinstance(record, (ModelCallRecord, ToolCallRecord))
        }
        for operation in operations.values():
            grant = grants.get(operation.grant_id)
            if grant is None:
                raise PermissionError("call record has no matching LegacyModuleCapabilityGrant")
            attempt = attempts.get(operation.attempt_id)
            if attempt is None:
                raise ValueError("call record requires a terminal Attempt")
            if (
                operation.operation_id not in existing_operation_ids
                and operation.attempt_id in existing_attempt_ids
            ):
                raise ValueError(
                    "call record cannot be appended to an existing terminal Attempt"
                )
            self._validate_grant_binding(
                grant,
                operation,
                workflow_execution_id=workflow_execution_id,
                frozen_entitlement_hash=frozen_entitlement_hash,
                owning_attempt=attempt,
            )
            self._validate_operation_lineage(
                operation,
                module_runs,
                variants,
                attempt,
            )
            grant_operations.setdefault(grant.grant_id, set()).add(
                operation.operation_id
            )

        for grant_id, operation_ids in grant_operations.items():
            if len(operation_ids) > 1:
                raise PermissionError(
                    f"single-use grant {grant_id!r} is bound to multiple operations"
                )

        operation_usage: dict[str, str] = {}
        existing_usage_ids = {
            record.usage_event_id
            for record in existing
            if isinstance(record, UsageEvent)
        }
        for usage in usage_events.values():
            operation = operations.get(usage.operation_id)
            if operation is None:
                raise PermissionError("UsageEvent has no matching call record")
            grant = grants.get(usage.grant_id)
            if grant is None:
                raise PermissionError("UsageEvent has no matching LegacyModuleCapabilityGrant")
            attempt = attempts.get(usage.attempt_id)
            if attempt is None:
                raise ValueError("UsageEvent requires a terminal Attempt")
            if (
                usage.usage_event_id not in existing_usage_ids
                and usage.attempt_id in existing_attempt_ids
            ):
                raise ValueError(
                    "UsageEvent cannot be appended to an existing terminal Attempt"
                )
            self._validate_grant_binding(
                grant,
                usage,
                workflow_execution_id=workflow_execution_id,
                frozen_entitlement_hash=frozen_entitlement_hash,
                owning_attempt=attempt,
            )
            for field in (
                "workflow_execution_id",
                "module_run_id",
                "variant_id",
                "attempt_id",
                "grant_id",
                "resource_id",
                "action_id",
            ):
                if getattr(usage, field) != getattr(operation, field):
                    raise PermissionError(
                        f"UsageEvent {field} does not match its operation"
                    )
            if usage.operation_id in operation_usage:
                raise ValueError("one operation cannot have multiple UsageEvents")
            operation_usage[usage.operation_id] = usage.usage_event_id

        missing_usage = set(operations).difference(operation_usage)
        if missing_usage:
            raise ValueError(
                "every call record requires exactly one UsageEvent; "
                "unknown provider usage must be recorded with null fields"
            )

        invocation_attempt_ids: set[str] = set()
        for invocation in invocation_commits.values():
            attempt = attempts.get(invocation.attempt_id)
            start = attempt_starts.get(invocation.attempt_id)
            if attempt is None or start is None:
                raise ValueError("InvocationCommitRecord requires started terminal Attempt")
            if invocation.attempt_id in invocation_attempt_ids:
                raise ValueError("Attempt has multiple InvocationCommitRecords")
            invocation_attempt_ids.add(invocation.attempt_id)
            for field in (
                "workflow_execution_id",
                "module_run_id",
                "variant_id",
                "attempt_id",
            ):
                if getattr(invocation, field) != getattr(attempt, field):
                    raise ValueError(f"InvocationCommitRecord {field} differs from Attempt")
            if invocation.dispatch_id != start.dispatch_id:
                raise ValueError("InvocationCommitRecord dispatch differs from Attempt start")
            if invocation.request_sha256 != start.request_sha256:
                raise ValueError("InvocationCommitRecord request differs from Attempt start")
            if invocation.terminal_status != attempt.status:
                raise ValueError("InvocationCommitRecord status differs from Attempt")
            expected_bundle_hash = attempt_output_bundle_sha256(
                tuple(
                    execution_outputs_by_ref[ref]
                    for ref in attempt.execution_output_refs
                )
            )
            if invocation.attempt_output_bundle_sha256 != expected_bundle_hash:
                raise ValueError("InvocationCommitRecord output bundle differs from Attempt")
            if _parse_utc(invocation.recorded_at_utc) < _parse_utc(
                attempt.period_end_at_utc
            ):
                raise ValueError("InvocationCommitRecord precedes terminal Attempt end")

        orphaned_attempt_ids: set[str] = set()
        for orphaned in orphaned_attempts.values():
            start = attempt_starts.get(orphaned.attempt_id)
            attempt = attempts.get(orphaned.attempt_id)
            if start is None or attempt is None:
                raise ValueError("AttemptOrphanedRecord requires started terminal Attempt")
            if orphaned.attempt_id in orphaned_attempt_ids:
                raise ValueError("Attempt has multiple orphan dispositions")
            orphaned_attempt_ids.add(orphaned.attempt_id)
            for field in (
                "workflow_execution_id",
                "module_run_id",
                "variant_id",
                "attempt_id",
            ):
                if getattr(orphaned, field) != getattr(start, field):
                    raise ValueError(f"AttemptOrphanedRecord {field} differs from start")
            if orphaned.dispatch_id != start.dispatch_id:
                raise ValueError("AttemptOrphanedRecord dispatch differs from start")
            if attempt.status != "failed" or attempt.failure_class != "orphaned_attempt":
                raise ValueError("orphan disposition requires failed orphaned Attempt")
            if _parse_utc(orphaned.recorded_at_utc) < _parse_utc(
                attempt.period_end_at_utc
            ):
                raise ValueError("AttemptOrphanedRecord precedes terminal Attempt end")

        stale_attempt_ids: set[str] = set()
        for stale in stale_outputs.values():
            start = attempt_starts.get(stale.attempt_id)
            attempt = attempts.get(stale.attempt_id)
            if start is None or attempt is None:
                raise ValueError("StaleOutputRecord requires started terminal Attempt")
            if stale.attempt_id in stale_attempt_ids:
                raise ValueError("Attempt has multiple stale-output dispositions")
            stale_attempt_ids.add(stale.attempt_id)
            for field in (
                "workflow_execution_id",
                "module_run_id",
                "variant_id",
                "attempt_id",
            ):
                if getattr(stale, field) != getattr(start, field):
                    raise ValueError(f"StaleOutputRecord {field} differs from start")
            if stale.dispatch_id != start.dispatch_id:
                raise ValueError("StaleOutputRecord dispatch differs from start")
            if (
                attempt.status != "failed"
                or attempt.failure_class != "stale_rejected"
                or attempt.execution_output_refs
            ):
                raise ValueError(
                    "stale output requires failed stale_rejected Attempt with no outputs"
                )
            if _parse_utc(stale.recorded_at_utc) < _parse_utc(
                attempt.period_end_at_utc
            ):
                raise ValueError("StaleOutputRecord precedes terminal Attempt end")

        prior_outcomes = {
            record.dispatch_id
            for record in existing
            if isinstance(record, ModuleOutcome)
        }
        new_outcomes: set[str] = set()
        for record in candidate[len(existing) :]:
            if isinstance(record, ModuleOutcome):
                if record.dispatch_id in prior_outcomes or record.dispatch_id in new_outcomes:
                    raise ValueError("dispatch_id already has a committed ModuleOutcome")
                new_outcomes.add(record.dispatch_id)
                if record.module_run_id is not None and record.module_run_id not in module_runs:
                    raise ValueError("ModuleOutcome references an unknown Module Run")
                if any(attempt_id not in attempts for attempt_id in record.attempt_ids):
                    raise ValueError("ModuleOutcome references an unknown Attempt")
                if record.module_run_id is not None and any(
                    attempts[attempt_id].module_run_id != record.module_run_id
                    for attempt_id in record.attempt_ids
                ):
                    raise ValueError("ModuleOutcome Attempt belongs to another Module Run")
                if any(
                    evidence_ref not in execution_inputs_by_ref
                    and evidence_ref not in execution_outputs_by_ref
                    for evidence_ref in record.evidence_artifact_refs
                ):
                    raise ValueError(
                        "ModuleOutcome evidence is outside the frozen execution package "
                        "and prior outputs"
                    )

        outcomes_by_dispatch = {
            record.dispatch_id: record
            for record in candidate
            if isinstance(record, ModuleOutcome)
        }
        for checkpoint in checkpoints.values():
            outcome = outcomes_by_dispatch.get(checkpoint.dispatch_id)
            if outcome is None:
                raise ValueError("CheckpointRecord references an unknown ModuleOutcome")
            if (
                checkpoint.committed_outcome_ref != outcome.outcome_ref
                or checkpoint.committed_outcome_sha256 != outcome.outcome_sha256
            ):
                raise ValueError("CheckpointRecord Outcome binding differs")
            if checkpoint.execution_release_ref != execution.execution_release_ref:
                raise ValueError("CheckpointRecord execution release differs")
            if checkpoint.graph_sha256 != execution.graph_sha256:
                raise ValueError("CheckpointRecord graph differs")

        for acknowledgement in backend_acknowledgements.values():
            checkpoint = checkpoints.get(acknowledgement.checkpoint_id)
            if checkpoint is None:
                raise ValueError(
                    "BackendAcknowledgementRecord references an unknown checkpoint"
                )
            if (
                acknowledgement.dispatch_id != checkpoint.dispatch_id
                or acknowledgement.outcome_ref != checkpoint.committed_outcome_ref
                or acknowledgement.outcome_sha256
                != checkpoint.committed_outcome_sha256
            ):
                raise ValueError("backend acknowledgement differs from checkpoint")
            if _parse_utc(acknowledgement.recorded_at_utc) < _parse_utc(
                checkpoint.recorded_at_utc
            ):
                raise ValueError("backend acknowledgement precedes checkpoint")

    @staticmethod
    def _unique_map(
        records: tuple[PersistedRuntimeRecord, ...],
        record_type: type,
        identity_field: str,
    ) -> dict[str, object]:
        result: dict[str, object] = {}
        for record in records:
            if not isinstance(record, record_type):
                continue
            identity = getattr(record, identity_field)
            if identity in result:
                raise ValueError(
                    f"duplicate {record_type.__name__} identity: {identity}"
                )
            result[identity] = record
        return result

    @staticmethod
    def _validate_operation_lineage(
        operation: ModelCallRecord | ToolCallRecord,
        module_runs: dict[str, object],
        variants: dict[str, object],
        owning_attempt: WorkflowAttemptRecord,
    ) -> None:
        module = module_runs.get(operation.module_run_id)
        variant = variants.get(operation.variant_id)
        if module is None or variant is None:
            raise ValueError("call record lacks complete Module/Variant lineage")
        if getattr(variant, "module_run_id") != operation.module_run_id:
            raise ValueError("call Variant does not belong to its Module Run")
        if owning_attempt.variant_id != operation.variant_id:
            raise ValueError("call Attempt does not belong to its Variant")

    @staticmethod
    def _validate_grant_binding(
        grant: LegacyModuleCapabilityGrant,
        operation: ModelCallRecord | ToolCallRecord | UsageEvent,
        *,
        workflow_execution_id: str,
        frozen_entitlement_hash: str | None,
        owning_attempt: WorkflowAttemptRecord,
    ) -> None:
        for field in (
            "workflow_execution_id",
            "module_run_id",
            "variant_id",
            "attempt_id",
            "resource_id",
            "action_id",
        ):
            if getattr(grant, field) != getattr(operation, field):
                raise PermissionError(
                    f"LegacyModuleCapabilityGrant {field} does not match operation"
                )
        if grant.workflow_execution_id != workflow_execution_id:
            raise PermissionError("LegacyModuleCapabilityGrant crossed Workflow Execution")
        if (
            frozen_entitlement_hash is not None
            and grant.entitlement_snapshot_hash != frozen_entitlement_hash
        ):
            raise PermissionError("LegacyModuleCapabilityGrant entitlement does not match execution")
        operation_time = _parse_utc(operation.recorded_at_utc)
        if operation_time < _parse_utc(grant.recorded_at_utc):
            raise PermissionError("operation precedes LegacyModuleCapabilityGrant")
        if operation_time > grant.expires_at():
            if owning_attempt.status not in {"failed", "cancelled"}:
                raise PermissionError(
                    "LegacyModuleCapabilityGrant expired before operation commit"
                )
            if operation.recorded_at_utc != owning_attempt.recorded_at_utc:
                raise PermissionError(
                    "late failure observation differs from terminal Attempt record time"
                )


def _parse_utc(value: str):
    """Parse a validated UTC timestamp for grant-expiry comparison."""

    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00"))


ReferenceRuntimeExecutionRecordStore = InMemoryRuntimeExecutionRecordStore


__all__ = [
    "InMemoryRuntimeExecutionRecordStore",
    "ReferenceRuntimeExecutionRecordStore",
    "RuntimeExecutionRecordStore",
]
