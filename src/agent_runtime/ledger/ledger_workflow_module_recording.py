"""Canonical Workflow Module lifecycle recording.

The execution kernel owns provider calls; this recorder owns the durable
before/after boundary around those calls.  It writes Module Run and Variant
identity before an Attempt, records an active claim before provider entry,
records every operation authorization before its effect, and atomically
finalizes outputs, calls, usage, and the invocation commit.

The recorder never stores prompt, source, output, or failure-detail content.
Those bytes remain behind the Cell-local artifact host.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
from ..contracts.execution_module_definition import (
    ModuleOutputBinding,
    ModuleRunResult,
    WorkflowModuleExecutionRequest,
)
from ..contracts.invocation_adapter_definition import ProviderOperationIntent
from ..contracts.ledger_lineage_definition import (
    ModuleAttemptRecord,
    ModuleExecutionVariantRecord,
    ModuleOutputResolutionRecord,
    ModuleRunRecord,
    ModuleToolCallObservation,
    ModuleUsageObservation,
)
from ..contracts.ledger_record_definition import (
    AttemptClaim,
    AttemptFinalizationBatch,
    AttemptOrphanedRecord,
    AttemptOrphaningBatch,
    AttemptOrphaningReceipt,
    AttemptOutputBundle,
    ExecutionOutputRef,
    InvocationCommitRecord,
    LegacyAttemptBeginBatch,
    LegacyModuleCapabilityGrant,
    LegacyOperationGrantBatch,
    ModelCallRecord,
    RuntimeRecordBatch,
    ToolCallRecord,
    UsageEvent,
    WorkflowAttemptRecord,
    WorkflowAttemptStartedRecord,
    WorkflowModuleExecutionVariantRecord,
    WorkflowModuleRunRecord,
    attempt_output_bundle_sha256,
    sha256_text,
    stable_runtime_id,
)
from ..foundation.foundation_contract_validation import (
    format_utc_timestamp,
    parse_utc_timestamp,
    validate_sha256,
)
from ..foundation.foundation_retry_policy_validation import (
    validate_retry_attempt_budget,
)
from ..contracts.registry_release_definition import (
    ExecutionProfileRelease,
    RuntimeModuleRelease,
)
from ..invocation.invocation_tool_definition import (
    ModuleArtifactHost,
    runtime_package_version,
)
from .ledger_execution_content_recording import (
    RuntimeExecutionContentStore,
    record_execution_content,
)
from .ledger_record_persistence import RuntimeExecutionRecordStore


@dataclass(frozen=True)
class WorkflowModuleLedgerBinding:
    """Execution-pinned persistence and claim-signing authority.

    ``claim_token_secret`` is host-owned secret material.  It must remain
    stable for the life of the Workflow Execution so a durable replay derives
    the same active-claim token without persisting that token in the ledger.
    """

    record_store: RuntimeExecutionRecordStore
    entitlement_snapshot_hash: str
    claim_token_secret: bytes
    content_store: RuntimeExecutionContentStore | None = None

    def validate(self) -> None:
        """Validate the store surface, frozen entitlement, and signing key."""

        for method_name in (
            "commit",
            "begin_attempt",
            "authorize_operation",
            "finalize_attempt",
            "orphan_attempt",
            "get_committed_invocation",
            "load_trace",
        ):
            if not callable(getattr(self.record_store, method_name, None)):
                raise ValueError(
                    f"record_store must implement {method_name}"
                )
        validate_sha256(
            "entitlement_snapshot_hash", self.entitlement_snapshot_hash
        )
        if (
            type(self.claim_token_secret) is not bytes
            or len(self.claim_token_secret) < 32
        ):
            raise ValueError("claim_token_secret must contain at least 32 bytes")
        if self.content_store is not None and not callable(
            getattr(self.content_store, "stage_content", None)
        ):
            raise ValueError("content_store must implement stage_content")


@dataclass(frozen=True)
class _AuthorizedOperationLedgerEvidence:
    """Compatibility grant plus the current AR09 evidence closure."""

    grant: LegacyModuleCapabilityGrant
    authorization_intent_ref: str
    authorization_intent_sha256: str
    authorization_decision_ref: str
    authorization_decision_sha256: str
    authorization_observation_ref: str
    authorization_observation_sha256: str


class WorkflowModuleLedgerRecorder:
    """Write one Workflow-bound Module Activity to the canonical ledger."""

    def __init__(self, binding: WorkflowModuleLedgerBinding) -> None:
        binding.validate()
        self._binding = binding
        self._claims: dict[str, AttemptClaim] = {}
        self._model_grants: dict[
            str, _AuthorizedOperationLedgerEvidence
        ] = {}
        self._tool_grants: dict[
            str, list[_AuthorizedOperationLedgerEvidence]
        ] = {}

    @property
    def record_store(self) -> RuntimeExecutionRecordStore:
        """Return the execution-pinned canonical store."""

        return self._binding.record_store

    def record_module_start(
        self,
        *,
        request: WorkflowModuleExecutionRequest,
        module: RuntimeModuleRelease,
        variants: tuple[ModuleExecutionVariantRecord, ...],
        profiles: tuple[ExecutionProfileRelease, ...],
        retry_policy_ref: str,
        retry_policy_sha256: str,
        max_attempts: int,
        recorded_at_utc: str,
    ) -> None:
        """Commit Module Run and all immutable Variant identities once."""

        request.validate()
        module.validate()
        if (
            retry_policy_ref != module.retry_policy_ref
            or retry_policy_sha256 != module.retry_policy_sha256
        ):
            raise ValueError("Retry Policy primitives differ from the Module binding")
        validate_retry_attempt_budget(max_attempts)
        if len(variants) != len(profiles) or len(variants) != len(
            request.variants
        ):
            raise ValueError("Workflow Module Variant/profile closure differs")
        module_record = WorkflowModuleRunRecord(
            workflow_execution_id=request.workflow_execution_id,
            module_run_id=request.module_run_id,
            state_id=request.workflow_node_id,
            module_id=module.module_id,
            input_refs=tuple(item.input_ref for item in request.inputs),
            input_closure_sha256=request.input_closure_sha256,
            recorded_at_utc=recorded_at_utc,
        )
        variant_records = tuple(
            WorkflowModuleExecutionVariantRecord(
                workflow_execution_id=request.workflow_execution_id,
                module_run_id=request.module_run_id,
                variant_id=variant.variant_id,
                module_id=module.module_id,
                agent_execution_adapter_id=profile.executor_adapter_id,
                execution_profile_id=profile.execution_profile_id,
                model_id=profile.model_id,
                reasoning_profile=profile.reasoning_profile,
                prompt_sha256=(
                    variant.prompt_envelope_sha256
                    or module.prompt_bundle_sha256
                    or module.release_sha256
                ),
                static_module_sha256=module.release_sha256,
                input_closure_sha256=request.input_closure_sha256,
                entitlement_snapshot_hash=(
                    self._binding.entitlement_snapshot_hash
                ),
                agent_execution_adapter_revision=(
                    profile.executor_adapter_revision
                ),
                runtime_version=runtime_package_version(),
                tool_policy=profile.tool_policy or ("no_tools",),
                context_mode=profile.semantic_input_delivery_mode,
                output_schema_sha256=module.output_schema_sha256,
                timeout_seconds=profile.timeout_seconds,
                max_attempts=max_attempts,
                execution_profile_sha256=profile.release_sha256,
                recorded_at_utc=recorded_at_utc,
                prompt_envelope_ref=variant.prompt_envelope_ref,
            )
            for variant, profile in zip(variants, profiles, strict=True)
        )
        existing = self.record_store.load_trace(
            request.workflow_execution_id
        )
        prior_module = _one_by_id(
            existing.records_of_type(WorkflowModuleRunRecord),
            "module_run_id",
            request.module_run_id,
        )
        if prior_module is not None:
            _require_same_without_time(prior_module, module_record)
            for row in variant_records:
                prior = _one_by_id(
                    existing.records_of_type(
                        WorkflowModuleExecutionVariantRecord
                    ),
                    "variant_id",
                    row.variant_id,
                )
                if prior is None:
                    raise ValueError(
                        "replayed Module Run is missing an admitted Variant"
                    )
                _require_same_without_time(prior, row)
            return
        self.record_store.commit(
            RuntimeRecordBatch(
                workflow_execution_id=request.workflow_execution_id,
                transaction_id=stable_runtime_id(
                    "transaction",
                    request.workflow_execution_id,
                    request.module_run_id,
                    "module_start",
                ),
                records=(module_record, *variant_records),
            )
        )

    def committed_attempt_starts(
        self,
        *,
        workflow_execution_id: str,
    ) -> tuple[WorkflowAttemptStartedRecord, ...]:
        """Return committed Attempt-start facts without interpreting them."""

        return self.record_store.load_trace(
            workflow_execution_id
        ).records_of_type(
            WorkflowAttemptStartedRecord
        )

    def committed_attempts(
        self,
        *,
        workflow_execution_id: str,
    ) -> tuple[WorkflowAttemptRecord, ...]:
        """Return committed terminal Attempt facts without interpreting them."""

        return self.record_store.load_trace(
            workflow_execution_id
        ).records_of_type(
            WorkflowAttemptRecord
        )

    def replay_result(
        self,
        *,
        request: WorkflowModuleExecutionRequest,
        module: RuntimeModuleRelease,
    ) -> ModuleRunResult | None:
        """Reconstruct a committed result without re-entering a provider."""

        invocation = self.record_store.get_committed_invocation(
            request.workflow_execution_id,
            request.dispatch_id,
        )
        if invocation is None:
            return None
        if (
            invocation.module_run_id != request.module_run_id
            or invocation.request_sha256 != request.request_sha256
        ):
            raise ValueError("committed invocation differs from replay request")
        trace = self.record_store.load_trace(request.workflow_execution_id)
        formal_module = _one_by_id(
            trace.records_of_type(WorkflowModuleRunRecord),
            "module_run_id",
            request.module_run_id,
        )
        formal_variant = _one_by_id(
            trace.records_of_type(WorkflowModuleExecutionVariantRecord),
            "variant_id",
            invocation.variant_id,
        )
        formal_attempt = _one_by_id(
            trace.records_of_type(WorkflowAttemptRecord),
            "attempt_id",
            invocation.attempt_id,
        )
        if (
            formal_module is None
            or formal_variant is None
            or formal_attempt is None
        ):
            raise ValueError("committed invocation lacks canonical lineage")
        tool_calls = tuple(
            row
            for row in trace.records_of_type(ToolCallRecord)
            if row.attempt_id == formal_attempt.attempt_id
        )
        tool_observations = tuple(
            _tool_observation_from_record(row) for row in tool_calls
        )
        requested_variant = next(
            (
                row
                for row in request.variants
                if row.execution_profile_sha256
                == formal_variant.execution_profile_sha256
            ),
            None,
        )
        if requested_variant is None:
            raise ValueError("replay request omits the committed Variant profile")
        usage_rows = tuple(
            row
            for row in trace.records_of_type(UsageEvent)
            if row.attempt_id == formal_attempt.attempt_id
        )
        model_calls = {
            row.model_call_id
            for row in trace.records_of_type(ModelCallRecord)
            if row.attempt_id == formal_attempt.attempt_id
        }
        model_usage = tuple(
            row for row in usage_rows if row.operation_id in model_calls
        )
        if len(model_usage) > 1:
            raise ValueError("one Attempt cannot replay multiple model usages")
        usage = model_usage[0] if model_usage else None
        execution_outputs_by_ref = {
            row.output_ref: row
            for row in trace.records_of_type(ExecutionOutputRef)
        }
        outputs = tuple(
            ModuleOutputBinding(
                logical_name=(row.logical_name or "result"),
                output_ref=row.output_ref,
                output_sha256=row.output_sha256,
                schema_ref=module.output_schema_ref,
                schema_sha256=module.output_schema_sha256,
                media_type=row.media_type,
            )
            for row in (
                execution_outputs_by_ref[output_ref]
                for output_ref in formal_attempt.execution_output_refs
            )
        )
        resolutions = tuple(
            row
            for row in trace.records_of_type(ModuleOutputResolutionRecord)
            if row.source_module_run_id == request.module_run_id
        )
        if len(resolutions) > 1:
            raise ValueError("Module Run has multiple output resolutions")
        resolution = resolutions[0] if resolutions else None
        if formal_attempt.status == "completed" and resolution is None:
            if not outputs:
                raise ValueError(
                    "completed invocation lacks canonical execution outputs"
                )
            resolution = self._direct_output_resolution(
                request=request,
                resolved_execution_output_refs=(
                    formal_attempt.execution_output_refs
                ),
                recorded_at_utc=formal_attempt.recorded_at_utc,
            )
            self.record_output_resolution(
                request=request,
                resolution=resolution,
            )
        return ModuleRunResult(
            module_run=ModuleRunRecord(
                module_run_id=request.module_run_id,
                request_id=request.request_id,
                request_sha256=request.request_sha256,
                purpose=request.purpose,
                module_release_ref=module.release_ref,
                module_release_sha256=module.release_sha256,
                input_package_ref=request.input_package_ref,
                input_package_sha256=request.input_package_sha256,
                input_closure_sha256=request.input_closure_sha256,
                isolated_scope_ref=None,
                isolated_scope_sha256=None,
                recorded_at_utc=formal_module.recorded_at_utc,
                workflow_execution_id=request.workflow_execution_id,
            ),
            variants=(
                ModuleExecutionVariantRecord(
                    module_run_id=request.module_run_id,
                    variant_id=formal_variant.variant_id,
                    arm_key=requested_variant.arm_key,
                    replicate_index=requested_variant.replicate_index,
                    execution_profile_ref=(
                        requested_variant.execution_profile_ref
                    ),
                    execution_profile_sha256=(
                        requested_variant.execution_profile_sha256
                    ),
                    prompt_envelope_ref=(
                        requested_variant.prompt_envelope_ref
                    ),
                    prompt_envelope_sha256=(
                        requested_variant.prompt_envelope_sha256
                    ),
                    input_closure_sha256=request.input_closure_sha256,
                    recorded_at_utc=formal_variant.recorded_at_utc,
                ),
            ),
            attempts=(
                ModuleAttemptRecord(
                    module_run_id=request.module_run_id,
                    variant_id=formal_variant.variant_id,
                    attempt_id=formal_attempt.attempt_id,
                    status=formal_attempt.status,
                    output_refs=formal_attempt.execution_output_refs,
                    usage=ModuleUsageObservation(
                        input_tokens=(usage.input_tokens if usage else None),
                        output_tokens=(usage.output_tokens if usage else None),
                        cache_read_tokens=(
                            usage.cache_read_tokens if usage else None
                        ),
                        cache_creation_tokens=(
                            usage.cache_creation_tokens if usage else None
                        ),
                    ),
                    failure_class=formal_attempt.failure_class,
                    period_start_at_utc=formal_attempt.period_start_at_utc,
                    period_end_at_utc=formal_attempt.period_end_at_utc,
                    recorded_at_utc=formal_attempt.recorded_at_utc,
                    tool_calls=tool_observations,
                    prompt_envelope_ref=(
                        requested_variant.prompt_envelope_ref
                    ),
                    prompt_envelope_sha256=(
                        requested_variant.prompt_envelope_sha256
                    ),
                ),
            ),
            outputs=outputs,
            resolution=resolution,
        )

    def record_output_resolution(
        self,
        *,
        request: WorkflowModuleExecutionRequest,
        resolution: ModuleOutputResolutionRecord | None,
    ) -> None:
        """Commit the Runtime-owned downstream output authority once."""

        if resolution is None:
            return
        if resolution.workflow_execution_id != request.workflow_execution_id:
            raise ValueError("output resolution crossed Workflow Execution")
        trace = self.record_store.load_trace(request.workflow_execution_id)
        prior = _one_by_id(
            trace.records_of_type(ModuleOutputResolutionRecord),
            "module_output_resolution_id",
            resolution.module_output_resolution_id,
        )
        if prior is not None:
            _require_same_without_time(prior, resolution)
            return
        self.record_store.commit(
            RuntimeRecordBatch(
                workflow_execution_id=request.workflow_execution_id,
                transaction_id=stable_runtime_id(
                    "transaction",
                    request.workflow_execution_id,
                    resolution.module_output_resolution_id,
                ),
                records=(resolution,),
            )
        )

    def canonicalize_output_resolution(
        self,
        *,
        request: WorkflowModuleExecutionRequest,
        resolution: ModuleOutputResolutionRecord | None,
    ) -> ModuleOutputResolutionRecord | None:
        """Bind a provisional direct resolution to canonical output bundles."""

        if resolution is None:
            return None
        if resolution.resolution_mode != "direct_single":
            raise NotImplementedError(
                "evaluated and selected Workflow resolutions require their "
                "evaluation ledger records before canonicalization"
            )
        canonical = self._direct_output_resolution(
            request=request,
            resolved_execution_output_refs=(
                resolution.resolved_execution_output_refs
            ),
            recorded_at_utc=resolution.recorded_at_utc,
        )
        if (
            canonical.module_output_resolution_id
            != resolution.module_output_resolution_id
        ):
            raise ValueError("provisional output resolution identity changed")
        return canonical

    def _direct_output_resolution(
        self,
        *,
        request: WorkflowModuleExecutionRequest,
        resolved_execution_output_refs: tuple[str, ...],
        recorded_at_utc: str,
    ) -> ModuleOutputResolutionRecord:
        """Build direct output authority from this dispatch's Attempt bundle."""

        invocation = self.record_store.get_committed_invocation(
            request.workflow_execution_id,
            request.dispatch_id,
        )
        if invocation is None or invocation.terminal_status != "completed":
            raise ValueError(
                "direct_single resolution requires a completed invocation"
            )
        bundles = tuple(
            row
            for row in self.record_store.load_trace(
                request.workflow_execution_id
            ).records_of_type(AttemptOutputBundle)
            if row.module_run_id == request.module_run_id
            and row.attempt_id == invocation.attempt_id
        )
        if len(bundles) != 1:
            raise ValueError(
                "direct_single resolution requires this dispatch's canonical "
                "Attempt bundle"
            )
        bundle = bundles[0]
        if tuple(resolved_execution_output_refs) != tuple(
            bundle.execution_output_refs
        ):
            raise ValueError(
                "direct_single resolution differs from its Attempt bundle"
            )
        return ModuleOutputResolutionRecord.build(
            module_output_resolution_id=(
                stable_runtime_id(
                    "module_resolution", request.module_run_id, "resolved"
                )
            ),
            workflow_execution_id=request.workflow_execution_id,
            source_module_run_id=request.module_run_id,
            resolution_mode="direct_single",
            candidate_output_bundle_refs=(
                f"attempt-output-bundle:{bundle.attempt_output_bundle_id}",
            ),
            candidate_output_bundle_sha256s=(bundle.bundle_sha256,),
            evaluation_set_ref=None,
            selection_ref=None,
            resolved_execution_output_refs=resolved_execution_output_refs,
            resolution_status="resolved",
            recorded_at_utc=recorded_at_utc,
        )

    def begin_attempt(
        self,
        *,
        request: WorkflowModuleExecutionRequest,
        variant: ModuleExecutionVariantRecord,
        profile: ExecutionProfileRelease,
        attempt_id: str,
        attempt_ordinal: int,
        recorded_at_utc: str,
        parent_attempt_id: str | None = None,
    ) -> AttemptClaim:
        """Durably claim one Attempt before authorization or provider entry."""

        claim = AttemptClaim(
            workflow_execution_id=request.workflow_execution_id,
            attempt_id=attempt_id,
            claim_token=self._claim_token(
                request.workflow_execution_id, attempt_id
            ),
        )
        start = WorkflowAttemptStartedRecord(
            workflow_execution_id=request.workflow_execution_id,
            dispatch_id=request.dispatch_id,
            module_run_id=request.module_run_id,
            variant_id=variant.variant_id,
            attempt_id=attempt_id,
            parent_attempt_id=parent_attempt_id,
            attempt_ordinal=attempt_ordinal,
            trace_id=stable_runtime_id(
                "trace", request.workflow_execution_id, attempt_id
            ),
            request_sha256=request.request_sha256,
            claim_token_hash=sha256_text(claim.claim_token),
            input_closure_sha256=request.input_closure_sha256,
            execution_profile_sha256=profile.release_sha256,
            entitlement_snapshot_hash=(
                self._binding.entitlement_snapshot_hash
            ),
            timeout_seconds=profile.timeout_seconds,
            recorded_at_utc=recorded_at_utc,
        )
        trace = self.record_store.load_trace(request.workflow_execution_id)
        prior = _one_by_id(
            trace.records_of_type(WorkflowAttemptStartedRecord),
            "attempt_id",
            attempt_id,
        )
        if prior is not None:
            _require_same_without_time(prior, start)
            if prior.claim_token_hash != sha256_text(claim.claim_token):
                raise PermissionError("replayed Attempt claim token changed")
            terminal = _one_by_id(
                trace.records_of_type(WorkflowAttemptRecord),
                "attempt_id",
                attempt_id,
            )
            if terminal is not None:
                # A terminalized (finalized or orphaned) Attempt cannot be
                # re-claimed; failing here beats a late store-level
                # 'claim is not active' after authorization work was done.
                raise PermissionError(
                    "Attempt is already terminal and cannot be re-claimed"
                )
            self._claims[attempt_id] = claim
            return claim
        receipt = self.record_store.begin_attempt(
            LegacyAttemptBeginBatch(
                workflow_execution_id=request.workflow_execution_id,
                transaction_id=stable_runtime_id(
                    "transaction",
                    request.workflow_execution_id,
                    attempt_id,
                    "attempt_begin",
                ),
                start=start,
                claim=claim,
                grants=(),
            )
        )
        self._claims[attempt_id] = receipt.claim
        return receipt.claim

    def recover_expired_attempt(
        self,
        *,
        workflow_execution_id: str,
        attempt_id: str,
        observed_at_utc: str,
        reason_code: str = "attempt_lease_expired",
        context_disposition_id: str = "invalidate",
    ) -> AttemptOrphaningReceipt:
        """Terminalize one expired start without inventing provider results.

        Recovery derives the original active claim from the execution-pinned
        host secret.  A restarted host must therefore receive the same
        ``claim_token_secret`` that was used when the Attempt began.

        ``observed_at_utc`` only gates the expiry decision.  Every committed
        record timestamp derives from the durable deadline, so the orphan
        disposition is byte-deterministic: concurrent recoverers and later
        re-runs converge on the same records and the store arbitrates replay
        under its own lock — the recorder performs no pre-read arbitration.
        """

        observed_at = parse_utc_timestamp("observed_at_utc", observed_at_utc)
        trace = self.record_store.load_trace(workflow_execution_id)
        start = _one_by_id(
            trace.records_of_type(WorkflowAttemptStartedRecord),
            "attempt_id",
            attempt_id,
        )
        if start is None:
            raise ValueError("expired Attempt recovery requires a durable start")
        if start.workflow_execution_id != workflow_execution_id:
            raise ValueError("Attempt start belongs to another Workflow Execution")
        if (
            start.entitlement_snapshot_hash
            != self._binding.entitlement_snapshot_hash
        ):
            raise PermissionError("Attempt recovery entitlement snapshot changed")
        if not start.lease_expired_at(observed_at):
            raise ValueError("Attempt lease has not expired")

        claim = AttemptClaim(
            workflow_execution_id=workflow_execution_id,
            attempt_id=attempt_id,
            claim_token=self._claim_token(workflow_execution_id, attempt_id),
        )
        if not hmac.compare_digest(
            sha256_text(claim.claim_token),
            start.claim_token_hash,
        ):
            raise PermissionError(
                "Attempt recovery claim secret differs from durable start"
            )

        deadline_at_utc = format_utc_timestamp(start.deadline_at())
        terminal = WorkflowAttemptRecord(
            workflow_execution_id=workflow_execution_id,
            module_run_id=start.module_run_id,
            variant_id=start.variant_id,
            attempt_id=start.attempt_id,
            parent_attempt_id=start.parent_attempt_id,
            attempt_ordinal=start.attempt_ordinal,
            status="failed",
            period_start_at_utc=start.recorded_at_utc,
            period_end_at_utc=deadline_at_utc,
            recorded_at_utc=deadline_at_utc,
            trace_id=start.trace_id,
            execution_output_refs=(),
            failure_class="orphaned_attempt",
        )
        orphaned = AttemptOrphanedRecord(
            orphaned_record_id=stable_runtime_id(
                "attempt_orphaned",
                workflow_execution_id,
                attempt_id,
            ),
            workflow_execution_id=workflow_execution_id,
            dispatch_id=start.dispatch_id,
            module_run_id=start.module_run_id,
            variant_id=start.variant_id,
            attempt_id=start.attempt_id,
            reason_code=reason_code,
            context_disposition_id=context_disposition_id,
            recorded_at_utc=deadline_at_utc,
        )

        try:
            receipt = self.record_store.orphan_attempt(
                claim,
                AttemptOrphaningBatch(
                    workflow_execution_id=workflow_execution_id,
                    transaction_id=stable_runtime_id(
                        "transaction",
                        workflow_execution_id,
                        attempt_id,
                        "attempt_orphan",
                    ),
                    terminal_attempt=terminal,
                    orphaned=orphaned,
                ),
            )
        except ValueError as exc:
            # Terminal records are immutable, so a post-hoc read classifies
            # the conflict accurately: a normal finalize that won the race is
            # a benign fact, not a half-written orphan disposition.
            committed = self.record_store.load_trace(workflow_execution_id)
            committed_terminal = _one_by_id(
                committed.records_of_type(WorkflowAttemptRecord),
                "attempt_id",
                attempt_id,
            )
            committed_orphan = _one_by_id(
                committed.records_of_type(AttemptOrphanedRecord),
                "attempt_id",
                attempt_id,
            )
            if committed_terminal is not None and committed_orphan is None:
                raise ValueError(
                    "Attempt already finalized with a provider result; "
                    "recovery is unnecessary"
                ) from exc
            raise
        self._claims.pop(attempt_id, None)
        self._model_grants.pop(attempt_id, None)
        self._tool_grants.pop(attempt_id, None)
        return receipt

    def recover_expired_attempts(
        self,
        *,
        workflow_execution_id: str,
        observed_at_utc: str,
    ) -> tuple[AttemptOrphaningReceipt, ...]:
        """Terminalize every expired in-flight lease of one Workflow Execution.

        This is the production recovery entry: one observation instant sweeps
        the committed starts and recovers each lease that is expired and not
        yet terminal.  Each recovery reuses the single-Attempt path, so byte
        determinism and store-side replay arbitration hold per Attempt, and a
        sweep that races a concurrent recoverer converges as replays.
        """

        observed_at = parse_utc_timestamp("observed_at_utc", observed_at_utc)
        trace = self.record_store.load_trace(workflow_execution_id)
        terminal_attempt_ids = {
            row.attempt_id
            for row in trace.records_of_type(WorkflowAttemptRecord)
        }
        receipts: list[AttemptOrphaningReceipt] = []
        for start in trace.records_of_type(WorkflowAttemptStartedRecord):
            if start.attempt_id in terminal_attempt_ids:
                continue
            if not start.lease_expired_at(observed_at):
                continue
            receipts.append(
                self.recover_expired_attempt(
                    workflow_execution_id=workflow_execution_id,
                    attempt_id=start.attempt_id,
                    observed_at_utc=observed_at_utc,
                )
            )
        return tuple(receipts)

    def authorize_model_call(
        self,
        *,
        request: WorkflowModuleExecutionRequest,
        profile: ExecutionProfileRelease,
        variant_id: str,
        attempt_id: str,
        operation_id: str,
        authorization_intent_ref: str,
        authorization_intent_sha256: str,
        authorization_decision_ref: str,
        authorization_decision_sha256: str,
        authorization_observation_ref: str,
        authorization_observation_sha256: str,
        recorded_at_utc: str,
    ) -> LegacyModuleCapabilityGrant:
        """Record the single model-call grant after Product authorization."""

        grant = LegacyModuleCapabilityGrant(
            grant_id=stable_runtime_id(
                "grant", request.workflow_execution_id, attempt_id, "model"
            ),
            workflow_execution_id=request.workflow_execution_id,
            module_run_id=request.module_run_id,
            variant_id=variant_id,
            attempt_id=attempt_id,
            capability_id=operation_id,
            resource_id=profile.execution_profile_id,
            action_id=operation_id,
            entitlement_snapshot_hash=(
                self._binding.entitlement_snapshot_hash
            ),
            idempotency_key=stable_runtime_id(
                "grant_key", request.workflow_execution_id, attempt_id, "model"
            ),
            expires_after_seconds=profile.timeout_seconds,
            recorded_at_utc=recorded_at_utc,
        )
        committed = self._authorize_grant(attempt_id, grant)
        self._model_grants[attempt_id] = _AuthorizedOperationLedgerEvidence(
            grant=committed,
            authorization_intent_ref=authorization_intent_ref,
            authorization_intent_sha256=authorization_intent_sha256,
            authorization_decision_ref=authorization_decision_ref,
            authorization_decision_sha256=authorization_decision_sha256,
            authorization_observation_ref=authorization_observation_ref,
            authorization_observation_sha256=(
                authorization_observation_sha256
            ),
        )
        return committed

    def authorize_tool_call(
        self,
        intent: ProviderOperationIntent,
        *,
        authorization_intent_ref: str,
        authorization_intent_sha256: str,
        authorization_decision_ref: str,
        authorization_decision_sha256: str,
        authorization_observation_ref: str,
        authorization_observation_sha256: str,
        recorded_at_utc: str,
    ) -> LegacyModuleCapabilityGrant:
        """Record one dynamic tool grant after its Product authorization."""

        intent.validate()
        grant = LegacyModuleCapabilityGrant(
            grant_id=stable_runtime_id(
                "grant",
                intent.workflow_execution_id,
                intent.attempt_id,
                intent.idempotency_key,
            ),
            workflow_execution_id=intent.workflow_execution_id,
            module_run_id=intent.module_run_id,
            variant_id=intent.variant_id,
            attempt_id=intent.attempt_id,
            capability_id=intent.capability_id,
            resource_id=intent.resource_id,
            action_id=intent.action_id,
            entitlement_snapshot_hash=(
                self._binding.entitlement_snapshot_hash
            ),
            idempotency_key=intent.idempotency_key,
            expires_after_seconds=intent.expires_after_seconds,
            recorded_at_utc=recorded_at_utc,
        )
        committed = self._authorize_grant(intent.attempt_id, grant)
        evidence = _AuthorizedOperationLedgerEvidence(
            grant=committed,
            authorization_intent_ref=authorization_intent_ref,
            authorization_intent_sha256=authorization_intent_sha256,
            authorization_decision_ref=authorization_decision_ref,
            authorization_decision_sha256=authorization_decision_sha256,
            authorization_observation_ref=authorization_observation_ref,
            authorization_observation_sha256=(
                authorization_observation_sha256
            ),
        )
        self._tool_grants.setdefault(intent.attempt_id, []).append(evidence)
        return committed

    def finalize_attempt(
        self,
        *,
        request: WorkflowModuleExecutionRequest,
        module: RuntimeModuleRelease,
        profile: ExecutionProfileRelease,
        attempt: ModuleAttemptRecord,
        outputs: tuple[ModuleOutputBinding, ...],
        artifact_host: ModuleArtifactHost,
    ) -> None:
        """Atomically publish terminal Attempt, outputs, calls, and usage."""

        claim = self._claims.get(attempt.attempt_id)
        if claim is None:
            raise RuntimeError("Attempt finalization lacks an active claim")
        start = _one_by_id(
            self.record_store.load_trace(
                request.workflow_execution_id
            ).records_of_type(WorkflowAttemptStartedRecord),
            "attempt_id",
            attempt.attempt_id,
        )
        if start is None:
            raise ValueError(
                "Attempt finalization requires its durable start record"
            )
        execution_outputs = tuple(
            ExecutionOutputRef(
                execution_output_id=stable_runtime_id(
                    "execution_output",
                    request.workflow_execution_id,
                    attempt.attempt_id,
                    output.output_ref,
                    output.output_sha256,
                ),
                workflow_execution_id=request.workflow_execution_id,
                output_type_id=f"{module.module_id}.output",
                schema_version=module.module_version,
                output_ref=output.output_ref,
                output_sha256=output.output_sha256,
                byte_size=len(
                    artifact_host.read_bytes(
                        output.output_ref, output.output_sha256
                    )
                ),
                media_type=output.media_type,
                recorded_at_utc=attempt.recorded_at_utc,
                module_run_id=attempt.module_run_id,
                variant_id=attempt.variant_id,
                attempt_id=attempt.attempt_id,
                logical_name=output.logical_name,
            )
            for output in outputs
        )
        terminal = WorkflowAttemptRecord(
            workflow_execution_id=request.workflow_execution_id,
            module_run_id=attempt.module_run_id,
            variant_id=attempt.variant_id,
            attempt_id=attempt.attempt_id,
            # Retry lineage is owned by the durable start record: a retry
            # Attempt finalizes with its true ordinal and parent instead of
            # being rewritten to a first attempt.
            parent_attempt_id=start.parent_attempt_id,
            attempt_ordinal=start.attempt_ordinal,
            status=attempt.status,
            period_start_at_utc=attempt.period_start_at_utc,
            period_end_at_utc=attempt.period_end_at_utc,
            recorded_at_utc=attempt.recorded_at_utc,
            trace_id=start.trace_id,
            execution_output_refs=tuple(
                output.output_ref for output in execution_outputs
            ),
            failure_class=attempt.failure_class,
        )
        child_records: list[object] = [*execution_outputs]
        model_evidence = self._model_grants.get(attempt.attempt_id)
        if model_evidence is not None:
            model_grant = model_evidence.grant
            model_call = ModelCallRecord(
                model_call_id=stable_runtime_id(
                    "model_call",
                    request.workflow_execution_id,
                    attempt.attempt_id,
                ),
                workflow_execution_id=request.workflow_execution_id,
                module_run_id=attempt.module_run_id,
                variant_id=attempt.variant_id,
                attempt_id=attempt.attempt_id,
                grant_id=model_grant.grant_id,
                resource_id=model_grant.resource_id,
                action_id=model_grant.action_id,
                agent_execution_adapter_id=profile.executor_adapter_id,
                model_id=profile.model_id,
                status_id=attempt.status,
                recorded_at_utc=attempt.recorded_at_utc,
                authorization_intent_ref=(
                    model_evidence.authorization_intent_ref
                ),
                authorization_intent_sha256=(
                    model_evidence.authorization_intent_sha256
                ),
                authorization_decision_ref=(
                    model_evidence.authorization_decision_ref
                ),
                authorization_decision_sha256=(
                    model_evidence.authorization_decision_sha256
                ),
                authorization_observation_ref=(
                    model_evidence.authorization_observation_ref
                ),
                authorization_observation_sha256=(
                    model_evidence.authorization_observation_sha256
                ),
            )
            child_records.extend(
                (
                    model_call,
                    UsageEvent(
                        usage_event_id=stable_runtime_id(
                            "usage_event",
                            request.workflow_execution_id,
                            model_call.model_call_id,
                        ),
                        operation_id=model_call.model_call_id,
                        workflow_execution_id=request.workflow_execution_id,
                        module_run_id=attempt.module_run_id,
                        variant_id=attempt.variant_id,
                        attempt_id=attempt.attempt_id,
                        grant_id=model_grant.grant_id,
                        resource_id=model_grant.resource_id,
                        action_id=model_grant.action_id,
                        input_tokens=attempt.usage.input_tokens,
                        output_tokens=attempt.usage.output_tokens,
                        cache_read_tokens=attempt.usage.cache_read_tokens,
                        cache_creation_tokens=(
                            attempt.usage.cache_creation_tokens
                        ),
                        estimated_cost_usd=None,
                        provider_charge_usd=None,
                        recorded_at_utc=attempt.recorded_at_utc,
                    ),
                )
            )
        tool_grants = tuple(self._tool_grants.get(attempt.attempt_id, ()))
        if len(tool_grants) != len(attempt.tool_calls):
            raise PermissionError(
                "authorized tool grants differ from provider observations"
            )
        for observation, evidence in zip(
            attempt.tool_calls, tool_grants, strict=True
        ):
            grant = evidence.grant
            tool_call = ToolCallRecord(
                tool_call_id=observation.tool_call_id,
                workflow_execution_id=request.workflow_execution_id,
                module_run_id=attempt.module_run_id,
                variant_id=attempt.variant_id,
                attempt_id=attempt.attempt_id,
                grant_id=grant.grant_id,
                resource_id=grant.resource_id,
                action_id=grant.action_id,
                tool_id=observation.tool_name,
                status_id="completed",
                recorded_at_utc=attempt.recorded_at_utc,
                request_ref=observation.request_ref,
                request_sha256=observation.request_sha256,
                response_ref=observation.response_ref,
                response_sha256=observation.response_sha256,
                authorization_intent_ref=evidence.authorization_intent_ref,
                authorization_intent_sha256=(
                    evidence.authorization_intent_sha256
                ),
                authorization_decision_ref=(
                    evidence.authorization_decision_ref
                ),
                authorization_decision_sha256=(
                    evidence.authorization_decision_sha256
                ),
                authorization_observation_ref=(
                    evidence.authorization_observation_ref
                ),
                authorization_observation_sha256=(
                    evidence.authorization_observation_sha256
                ),
            )
            child_records.extend(
                (
                    tool_call,
                    UsageEvent(
                        usage_event_id=stable_runtime_id(
                            "usage_event",
                            request.workflow_execution_id,
                            tool_call.tool_call_id,
                        ),
                        operation_id=tool_call.tool_call_id,
                        workflow_execution_id=request.workflow_execution_id,
                        module_run_id=attempt.module_run_id,
                        variant_id=attempt.variant_id,
                        attempt_id=attempt.attempt_id,
                        grant_id=grant.grant_id,
                        resource_id=grant.resource_id,
                        action_id=grant.action_id,
                        input_tokens=None,
                        output_tokens=None,
                        cache_read_tokens=None,
                        cache_creation_tokens=None,
                        estimated_cost_usd=None,
                        provider_charge_usd=None,
                        recorded_at_utc=attempt.recorded_at_utc,
                    ),
                )
            )
        transaction_id = stable_runtime_id(
            "transaction",
            request.workflow_execution_id,
            attempt.attempt_id,
            "attempt_finalize",
        )
        invocation = InvocationCommitRecord(
            invocation_commit_id=stable_runtime_id(
                "invocation_commit",
                request.workflow_execution_id,
                request.dispatch_id,
                attempt.attempt_id,
            ),
            workflow_execution_id=request.workflow_execution_id,
            dispatch_id=request.dispatch_id,
            module_run_id=attempt.module_run_id,
            variant_id=attempt.variant_id,
            attempt_id=attempt.attempt_id,
            request_sha256=request.request_sha256,
            attempt_output_bundle_sha256=attempt_output_bundle_sha256(
                execution_outputs
            ),
            terminal_status=attempt.status,
            context_disposition_id=stable_runtime_id(
                "context_disposition",
                request.workflow_execution_id,
                attempt.attempt_id,
            ),
            commit_transaction_id=transaction_id,
            recorded_at_utc=attempt.recorded_at_utc,
        )
        if self._binding.content_store is not None:
            for output in execution_outputs:
                record_execution_content(
                    content_store=self._binding.content_store,
                    content_reader=artifact_host,
                    workflow_execution_id=request.workflow_execution_id,
                    content_ref=output.output_ref,
                    content_sha256=output.output_sha256,
                    media_type=output.media_type,
                    recorded_at_utc=output.recorded_at_utc,
                    reference_is_committed=False,
                )
        self.record_store.finalize_attempt(
            claim,
            AttemptFinalizationBatch(
                workflow_execution_id=request.workflow_execution_id,
                transaction_id=transaction_id,
                terminal_attempt=terminal,
                invocation_commit=invocation,
                records=tuple(child_records),
            ),
        )

    def _claim_token(self, workflow_execution_id: str, attempt_id: str) -> str:
        message = f"{workflow_execution_id}\x1f{attempt_id}".encode("utf-8")
        return hmac.new(
            self._binding.claim_token_secret,
            message,
            hashlib.sha256,
        ).hexdigest()

    def _authorize_grant(
        self,
        attempt_id: str,
        grant: LegacyModuleCapabilityGrant,
    ) -> LegacyModuleCapabilityGrant:
        claim = self._claims.get(attempt_id)
        if claim is None:
            raise RuntimeError("operation authorization lacks an active Attempt")
        trace = self.record_store.load_trace(grant.workflow_execution_id)
        prior = _one_by_id(
            trace.records_of_type(LegacyModuleCapabilityGrant),
            "grant_id",
            grant.grant_id,
        )
        if prior is not None:
            _require_same_without_time(prior, grant)
            return prior
        self.record_store.authorize_operation(
            LegacyOperationGrantBatch(
                workflow_execution_id=grant.workflow_execution_id,
                transaction_id=stable_runtime_id(
                    "transaction",
                    grant.workflow_execution_id,
                    grant.grant_id,
                ),
                claim=claim,
                grants=(grant,),
            )
        )
        return grant


def _one_by_id(rows, field_name: str, expected_id: str):
    matches = tuple(row for row in rows if getattr(row, field_name) == expected_id)
    if len(matches) > 1:
        raise ValueError(f"duplicate Runtime ledger identity: {expected_id}")
    return matches[0] if matches else None


def _tool_observation_from_record(
    row: ToolCallRecord,
) -> ModuleToolCallObservation:
    """Rebuild one provider-neutral tool observation from canonical refs."""

    if (
        row.request_ref is None
        or row.request_sha256 is None
        or row.response_ref is None
        or row.response_sha256 is None
    ):
        raise ValueError(
            "committed Gateway call lacks request/response content lineage"
        )
    observation = ModuleToolCallObservation(
        tool_call_id=row.tool_call_id,
        tool_name=row.tool_id,
        request_ref=row.request_ref,
        request_sha256=row.request_sha256,
        response_ref=row.response_ref,
        response_sha256=row.response_sha256,
    )
    observation.validate()
    return observation


def _require_same_without_time(left, right) -> None:
    left_payload = left.as_dict()
    right_payload = right.as_dict()
    left_payload.pop("recorded_at_utc", None)
    right_payload.pop("recorded_at_utc", None)
    if left_payload != right_payload:
        raise ValueError("durable replay changed committed Runtime identity")


__all__ = [
    "WorkflowModuleLedgerBinding",
    "WorkflowModuleLedgerRecorder",
]
