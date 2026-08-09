"""Authorization-enforcing Test and Evaluation execution kernel.

One canonical adapter contract carries every Module invocation. A Module that
declares a model operation requires committed AR09 authorization evidence
resolved before the provider transport is entered; the committed
execution-authorization fence is re-read inside the same atomic commit that
makes outputs authoritative. Production purposes still fail closed before any
adapter resolution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Callable, Mapping

from ..contracts.registry_contract_validation import validate_id
from ..contracts.execution_authorization_definition import (
    ExecutionAuthorizationContextBinding,
    ExecutionAuthorizationFence,
    ExecutionAuthorizationFenceState,
    GatewayAuthorizationObservation,
    GatewayDecisionEffect,
    OperationAuthorizationQuery,
    ProductOperationDecision,
    ProtectedOperationIntent,
)
from ..contracts.execution_module_definition import (
    ModuleExecutionLedger,
    ModuleExecutionRequest,
    ModuleOutputBinding,
    ModuleRunResult,
    ModuleVariantRequest,
)
from ..contracts.invocation_adapter_definition import (
    AgentExecutionAdapterDescriptor,
    AgentExecutionResult,
    AuthorizedAgentExecutionAdapter,
    AuthorizedAgentExecutionRequest,
    AuthorizedExecutionInput,
    AuthorizedOperationReceipt,
    OutputSubmission,
    ProviderOperationIntent,
)
from ..contracts.registry_release_definition import (
    ExecutionProfileRelease,
    ModuleExecutionPurpose,
    OutputResolutionPolicy,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
    RuntimeModuleRelease,
)
from ..contracts.ledger_lineage_definition import (
    ModuleAttemptRecord,
    ModuleAttemptStartedRecord,
    ModuleExecutionVariantRecord,
    ModuleOutputResolutionRecord,
    ModuleRunRecord,
    ModuleUsageObservation,
)
from ..invocation.invocation_tool_definition import ModuleArtifactHost
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from .execution_authorization_coordination import ExecutionAuthorizationController
from .execution_authorization_resolution import ProductOperationAuthorizationClient


_MODEL_INVOCATION_OPERATION_IDS = frozenset({"invoke_model", "model_execute"})


def _canonical_sha256(payload: Mapping[str, Any] | list[Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def isolated_execution_scope_id(
    isolated_scope_ref: str,
    isolated_scope_sha256: str,
) -> str:
    """Derive the deterministic execution scope identity of one isolated run.

    AR09 authority records carry this identity in their pre-split
    ``workflow_execution_id`` field; a caller never fabricates a Workflow
    Execution for an isolated Module Run.
    """

    return _stable_id("isolated_scope", isolated_scope_ref, isolated_scope_sha256)


def _data_use_purpose_id(purpose: ModuleExecutionPurpose) -> str:
    return f"module_{purpose.value}_execution"


class AgentExecutionAdapterRegistry:
    """Duplicate-safe registry keyed by exact adapter ID and revision."""

    def __init__(self) -> None:
        self._adapters: dict[tuple[str, str], AuthorizedAgentExecutionAdapter] = {}

    def register(self, adapter: AuthorizedAgentExecutionAdapter) -> None:
        """Register one adapter under its exact descriptor identity."""

        descriptor = getattr(adapter, "descriptor", None)
        if type(descriptor) is not AgentExecutionAdapterDescriptor:
            raise ValueError("adapter must expose an exact descriptor")
        descriptor.validate()
        if not callable(getattr(adapter, "execute", None)):
            raise ValueError("adapter must implement execute(request, host)")
        key = (descriptor.adapter_id, descriptor.adapter_revision)
        if key in self._adapters:
            raise ValueError(
                f"adapter already registered: {key[0]}@{key[1]}"
            )
        self._adapters[key] = adapter

    def resolve(
        self,
        adapter_id: str,
        adapter_revision: str,
    ) -> AuthorizedAgentExecutionAdapter:
        """Resolve one registered adapter by exact ID and revision."""

        validate_id("adapter_id", adapter_id)
        try:
            return self._adapters[(adapter_id, adapter_revision)]
        except KeyError as exc:
            raise KeyError(
                f"unknown Agent execution adapter: {adapter_id}@{adapter_revision}"
            ) from exc


@dataclass(frozen=True)
class ModuleExecutionAuthority:
    """AR09 authority surface required for protected Module execution."""

    controller: ExecutionAuthorizationController
    binding: ExecutionAuthorizationContextBinding
    authorization_client: ProductOperationAuthorizationClient
    enforcing_gateway_id: str
    environment_id: str

    def validate(self) -> None:
        """Validate the authority closure without calling the Product port."""

        if type(self.controller) is not ExecutionAuthorizationController:
            raise ValueError(
                "controller must be an exact ExecutionAuthorizationController"
            )
        if type(self.binding) is not ExecutionAuthorizationContextBinding:
            raise ValueError(
                "binding must be an exact ExecutionAuthorizationContextBinding"
            )
        self.binding.validate()
        if not callable(
            getattr(self.authorization_client, "authorize_operation", None)
        ):
            raise ValueError(
                "authorization_client must implement authorize_operation"
            )


@dataclass(frozen=True)
class _AttemptAuthorizationEvidence:
    """Committed AR09 evidence resolved before one provider invocation."""

    intent: ProtectedOperationIntent
    decision: ProductOperationDecision
    observation: GatewayAuthorizationObservation


class _AttemptExecutionHost:
    """Request-bound host: authorized reads in, staged non-authoritative bytes out."""

    def __init__(
        self,
        *,
        request: AuthorizedAgentExecutionRequest,
        artifact_host: ModuleArtifactHost,
    ) -> None:
        self._artifact_host = artifact_host
        self._inputs_by_handle = {
            item.local_handle: item for item in request.authorized_inputs
        }
        self._staged: dict[str, tuple[OutputSubmission, bytes]] = {}

    def read_authorized_input(self, local_handle: str) -> bytes:
        entry = self._inputs_by_handle.get(local_handle)
        if entry is None:
            raise PermissionError(
                f"input handle is outside the authorized request table: {local_handle}"
            )
        content = self._artifact_host.read_bytes(entry.input_ref, entry.input_sha256)
        if hashlib.sha256(content).hexdigest() != entry.input_sha256:
            raise ValueError("authorized input content hash mismatch")
        return content

    def stage_output_bytes(
        self,
        submission: OutputSubmission,
        content: bytes,
    ) -> None:
        if type(submission) is not OutputSubmission:
            raise ValueError("submission must be an exact OutputSubmission")
        submission.validate()
        if type(content) is not bytes:
            raise ValueError("staged output content must be bytes")
        if submission.output_slot_id in self._staged:
            raise ValueError(
                f"output slot already staged: {submission.output_slot_id}"
            )
        self._staged[submission.output_slot_id] = (submission, content)

    def authorize_operation(
        self,
        request: ProviderOperationIntent,
    ) -> AuthorizedOperationReceipt:
        if type(request) is not ProviderOperationIntent:
            raise ValueError("request must be an exact ProviderOperationIntent")
        request.validate()
        raise PermissionError(
            "dynamic operation authorization awaits the Gateway capability slice"
        )

    def staged_output(self, output_slot_id: str) -> bytes:
        try:
            return self._staged[output_slot_id][1]
        except KeyError as exc:
            raise ValueError(
                f"adapter reported an unstaged output slot: {output_slot_id}"
            ) from exc


def run_module(
    request: ModuleExecutionRequest,
    *,
    release_registry: RuntimeReleaseRegistry,
    adapters: AgentExecutionAdapterRegistry,
    artifact_host: ModuleArtifactHost,
    ledger: ModuleExecutionLedger,
    authority: ModuleExecutionAuthority | None = None,
    clock: Callable[[], str] = _utc_now,
) -> ModuleRunResult:
    """Run one registered Module through the Test/Evaluation execution kernel.

    The kernel accepts only isolated ``test`` and ``evaluation`` purposes. A
    Module that declares a model operation requires ``authority``; its AR09
    binding, fence, protected-operation intent, and Product operation decision
    are resolved and validated before any provider transport is entered, and
    the committed fence is re-read inside the atomic finalization that makes
    outputs authoritative. Empty authorization evidence is admissible only for
    the operation-free ``in_process`` conjunction. Every other protected
    operation and every production purpose fails before adapter resolution.
    """

    if type(request) is not ModuleExecutionRequest:
        raise ValueError("request must be an exact ModuleExecutionRequest")
    request.validate()
    existing = ledger.existing_result(request)
    if existing is not None:
        return existing
    if request.purpose not in {
        ModuleExecutionPurpose.TEST,
        ModuleExecutionPurpose.EVALUATION,
    }:
        raise NotImplementedError(
            "production Module execution awaits AR09 target authorization admission"
        )

    module = release_registry.get_module(
        request.module_release_ref,
        request.module_release_sha256,
    )
    release_registry.assert_module_execution_allowed(module, request.purpose)
    _assert_module_dependencies_shadow_executable(release_registry, module)
    unsupported_operation_ids = (
        set(module.declared_operation_ids) - _MODEL_INVOCATION_OPERATION_IDS
    )
    if unsupported_operation_ids:
        raise NotImplementedError(
            "protected Module operations await AR09 request and grant binding: "
            + ", ".join(sorted(unsupported_operation_ids))
        )
    if len(module.declared_operation_ids) > 1:
        raise ValueError(
            "the model-backed slice admits exactly one declared model operation"
        )
    if module.declared_operation_ids:
        if authority is None:
            raise PermissionError(
                "a Module that declares a model operation requires a "
                "module execution authority"
            )
        authority.validate()
        _assert_authority_binding_closure(authority.binding, request)
    elif authority is not None:
        raise ValueError(
            "a module execution authority was supplied for an operation-free "
            "Module; nothing would consume or enforce it"
        )
    if (
        module.output_resolution_policy is OutputResolutionPolicy.DIRECT_SINGLE
        and len(request.variants) != 1
    ):
        raise ValueError("direct_single Module Run requires exactly one Variant")

    started_at = clock()
    module_run_id = _stable_id("module_run", request.request_id, request.request_sha256)
    module_run = ModuleRunRecord(
        module_run_id=module_run_id,
        request_id=request.request_id,
        request_sha256=request.request_sha256,
        purpose=request.purpose,
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        input_package_ref=request.input_package_ref,
        input_package_sha256=request.input_package_sha256,
        input_closure_sha256=request.input_closure_sha256,
        isolated_scope_ref=request.isolated_scope_ref,
        isolated_scope_sha256=request.isolated_scope_sha256,
        recorded_at_utc=started_at,
    )

    resolved_profiles: list[ExecutionProfileRelease] = []
    resolved_adapters: list[AuthorizedAgentExecutionAdapter] = []
    variant_records: list[ModuleExecutionVariantRecord] = []
    attempt_starts: list[ModuleAttemptStartedRecord] = []
    for variant_request in request.variants:
        profile = release_registry.get_execution_profile(
            variant_request.execution_profile_ref,
            variant_request.execution_profile_sha256,
        )
        _assert_profile_shadow_executable(release_registry, profile)
        if profile.transport_kind not in module.compatible_transport_kinds:
            raise ValueError("Execution Profile transport is incompatible with Module")
        if module.declared_operation_ids:
            _assert_model_only_test_evaluation_profile(profile)
        if profile.tool_policy:
            raise NotImplementedError(
                "model-visible tool profiles await the Gateway capability slice"
            )
        adapter = adapters.resolve(
            profile.executor_adapter_id,
            profile.executor_adapter_revision,
        )
        descriptor = adapter.descriptor
        _assert_descriptor_covers_profile(descriptor, profile)
        if (
            descriptor.transport_family != "in_process"
            and not module.declared_operation_ids
        ):
            raise PermissionError(
                "a provider transport requires a declared model invocation operation"
            )
        if module.prompt_bundle_ref is not None and (
            variant_request.prompt_envelope_ref is None
        ):
            raise ValueError("Agent Module Variant requires a Cell-local Prompt Envelope")
        variant_id = _stable_id(
            "module_variant",
            module_run_id,
            variant_request.arm_key,
            str(variant_request.replicate_index),
            profile.release_sha256,
            variant_request.prompt_envelope_sha256 or "none",
        )
        attempt_id = _stable_id("module_attempt", variant_id, "1")
        resolved_profiles.append(profile)
        resolved_adapters.append(adapter)
        variant_records.append(
            ModuleExecutionVariantRecord(
                module_run_id=module_run_id,
                variant_id=variant_id,
                arm_key=variant_request.arm_key,
                replicate_index=variant_request.replicate_index,
                execution_profile_ref=profile.release_ref,
                execution_profile_sha256=profile.release_sha256,
                prompt_envelope_ref=variant_request.prompt_envelope_ref,
                prompt_envelope_sha256=variant_request.prompt_envelope_sha256,
                input_closure_sha256=request.input_closure_sha256,
                recorded_at_utc=started_at,
            )
        )
        attempt_starts.append(
            ModuleAttemptStartedRecord(
                module_run_id=module_run_id,
                variant_id=variant_id,
                attempt_id=attempt_id,
                attempt_ordinal=1,
                recorded_at_utc=started_at,
            )
        )

    concurrent_result = ledger.begin(
        request,
        module_run,
        tuple(variant_records),
        tuple(attempt_starts),
    )
    if concurrent_result is not None:
        return concurrent_result

    attempts: list[ModuleAttemptRecord] = []
    outputs: list[ModuleOutputBinding] = []
    for variant_request, profile, adapter, variant, attempt_start in zip(
        request.variants,
        resolved_profiles,
        resolved_adapters,
        variant_records,
        attempt_starts,
        strict=True,
    ):
        attempt, attempt_outputs = _execute_attempt(
            run_request=request,
            module=module,
            profile=profile,
            adapter=adapter,
            variant_request=variant_request,
            variant=variant,
            attempt_start=attempt_start,
            artifact_host=artifact_host,
            authority=authority,
            ledger=ledger,
            clock=clock,
            release_registry=release_registry,
        )
        attempts.append(attempt)
        outputs.extend(attempt_outputs)

    resolution = _resolve_shadow_outputs(
        module,
        module_run_id,
        tuple(variant_records),
        tuple(attempts),
        tuple(outputs),
        clock(),
    )
    result = ModuleRunResult(
        module_run=module_run,
        variants=tuple(variant_records),
        attempts=tuple(attempts),
        outputs=tuple(outputs),
        resolution=resolution,
    )
    ledger.commit_result(request.request_id, result)
    return result


def _execute_attempt(
    *,
    run_request: ModuleExecutionRequest,
    module: RuntimeModuleRelease,
    profile: ExecutionProfileRelease,
    adapter: AuthorizedAgentExecutionAdapter,
    variant_request: ModuleVariantRequest,
    variant: ModuleExecutionVariantRecord,
    attempt_start: ModuleAttemptStartedRecord,
    artifact_host: ModuleArtifactHost,
    authority: ModuleExecutionAuthority | None,
    ledger: ModuleExecutionLedger,
    clock: Callable[[], str],
    release_registry: RuntimeReleaseRegistry,
) -> tuple[ModuleAttemptRecord, tuple[ModuleOutputBinding, ...]]:
    """Authorize, invoke, and atomically finalize one Attempt."""

    evidence: _AttemptAuthorizationEvidence | None = None
    if authority is not None:
        try:
            evidence = _authorize_model_attempt(
                authority=authority,
                module=module,
                profile=profile,
                purpose=run_request.purpose,
                module_run_id=variant.module_run_id,
                attempt_id=attempt_start.attempt_id,
                observed_at_utc=clock(),
            )
        except (PermissionError, TypeError, ValueError) as exc:
            return _record_failed_attempt(
                variant=variant,
                attempt_start=attempt_start,
                failure_class="authorization",
                usage=_empty_usage(),
                ended_at=clock(),
                payload={
                    "disposition": "authorization_refused_at_dispatch",
                    "exception_type": type(exc).__name__,
                    "reason": str(exc),
                },
                artifact_host=artifact_host,
                ledger=ledger,
            )
        if evidence.decision.effect is not GatewayDecisionEffect.ALLOW:
            return _record_failed_attempt(
                variant=variant,
                attempt_start=attempt_start,
                failure_class="authorization",
                usage=_empty_usage(),
                ended_at=clock(),
                payload={
                    "disposition": "product_operation_denied",
                    "reason_code": evidence.decision.reason_code,
                    "decision_ref": evidence.decision.decision_ref,
                },
                artifact_host=artifact_host,
                ledger=ledger,
            )

    canonical_request = _build_canonical_request(
        run_request=run_request,
        module=module,
        profile=profile,
        variant_request=variant_request,
        variant=variant,
        attempt_start=attempt_start,
        evidence=evidence,
        authority=authority,
    )
    host = _AttemptExecutionHost(
        request=canonical_request,
        artifact_host=artifact_host,
    )

    staged: tuple[tuple[OutputSubmission, bytes], ...] = ()
    try:
        result = adapter.execute(canonical_request, host)
        if type(result) is not AgentExecutionResult:
            raise TypeError("adapter returned an invalid result type")
        result.validate()
        if (
            result.provider_id != profile.provider_id
            or result.model_id != profile.model_id
        ):
            raise ValueError("adapter result provider identity differs from profile")
        _assert_result_lineage_resolvable(artifact_host, result)
        if result.terminal_status == "completed":
            if not result.outputs:
                raise ValueError(
                    "completed adapter result requires at least one output"
                )
            staged = tuple(
                (submission, host.staged_output(submission.output_slot_id))
                for submission in result.outputs
            )
            for submission, content in staged:
                _assert_staged_output_conforms(
                    release_registry,
                    module=module,
                    output_slot_id=submission.output_slot_id,
                    content=content,
                )
    except Exception as exc:
        return _record_failed_attempt(
            variant=variant,
            attempt_start=attempt_start,
            failure_class="unknown",
            usage=_empty_usage(),
            ended_at=clock(),
            payload={
                "disposition": "adapter_conformance_failure",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            },
            artifact_host=artifact_host,
            ledger=ledger,
        )

    usage = ModuleUsageObservation(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )
    ended_at = clock()

    if result.terminal_status != "completed":
        assert result.failure is not None
        attempt = _failed_attempt(
            variant=variant,
            attempt_start=attempt_start,
            failure_class=result.failure.failure_class,
            usage=usage,
            ended_at=ended_at,
            status=(
                "cancelled"
                if result.terminal_status == "cancelled"
                else "failed"
            ),
            detail=(
                (result.failure.detail_ref, result.failure.detail_sha256)
                if result.failure.detail_ref is not None
                and result.failure.detail_sha256 is not None
                else None
            ),
        )
        ledger.commit_attempt(attempt)
        return attempt, ()

    # Artifact bytes are committed before the fence critical section: staged
    # content is not authoritative until the attempt record references it, and
    # hashing large outputs must not serialize the authorization ledger.
    committed: list[ModuleOutputBinding] = []
    for submission, content in staged:
        output = artifact_host.commit_output(
            module_run_id=variant.module_run_id,
            variant_id=variant.variant_id,
            attempt_id=attempt_start.attempt_id,
            logical_name=submission.output_slot_id,
            content=content,
            schema_ref=module.output_schema_ref,
            schema_sha256=module.output_schema_sha256,
            media_type="application/json",
        )
        output.validate()
        if (
            output.output_sha256 != hashlib.sha256(content).hexdigest()
            or output.schema_ref != module.output_schema_ref
            or output.schema_sha256 != module.output_schema_sha256
        ):
            raise ValueError(
                "Module artifact host returned a mismatched output binding"
            )
        committed.append(output)

    def finalize(fence: ExecutionAuthorizationFence | None) -> tuple[
        ModuleAttemptRecord, tuple[ModuleOutputBinding, ...]
    ]:
        if fence is not None and fence.state is not (
            ExecutionAuthorizationFenceState.OPEN
        ):
            return _record_failed_attempt(
                variant=variant,
                attempt_start=attempt_start,
                failure_class="authorization",
                usage=usage,
                ended_at=ended_at,
                payload={
                    "disposition": "stale_result_quarantined",
                    "fence_ref": fence.fence_ref,
                    "reason_code": fence.reason_code,
                },
                artifact_host=artifact_host,
                ledger=ledger,
            )
        completed = ModuleAttemptRecord(
            module_run_id=variant.module_run_id,
            variant_id=variant.variant_id,
            attempt_id=attempt_start.attempt_id,
            status="completed",
            output_refs=tuple(output.output_ref for output in committed),
            usage=usage,
            failure_class=None,
            period_start_at_utc=attempt_start.recorded_at_utc,
            period_end_at_utc=ended_at,
            recorded_at_utc=ended_at,
            tool_calls=(),
            prompt_envelope_ref=variant.prompt_envelope_ref,
            prompt_envelope_sha256=variant.prompt_envelope_sha256,
        )
        ledger.commit_attempt(completed)
        return completed, tuple(committed)

    if authority is not None:
        authority.controller.revalidate(
            binding_ref=authority.binding.binding_ref,
            observed_at_utc=clock(),
        )
        return authority.controller.finalize_under_current_fence(
            authority.binding.binding_ref,
            finalize,
        )
    return finalize(None)


def _authorize_model_attempt(
    *,
    authority: ModuleExecutionAuthority,
    module: RuntimeModuleRelease,
    profile: ExecutionProfileRelease,
    purpose: ModuleExecutionPurpose,
    module_run_id: str,
    attempt_id: str,
    observed_at_utc: str,
) -> _AttemptAuthorizationEvidence:
    """Commit the AR09 intent and resolve the Product decision before dispatch.

    The intent commit revalidates the execution authorization fence itself and
    raises when the fence is closed; a second kernel-side revalidation here
    would only double the Product round-trips.
    """

    binding = authority.binding
    operation_id = module.declared_operation_ids[0]
    intent = authority.controller.commit_protected_operation_intent(
        binding_ref=binding.binding_ref,
        module_run_id=module_run_id,
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        operation_id=operation_id,
        resource_ref=profile.release_ref,
        enforcing_gateway_id=authority.enforcing_gateway_id,
        idempotency_key=attempt_id,
        requires_grant=False,
        operation_grant_ref=None,
        observed_at_utc=observed_at_utc,
    )
    query = OperationAuthorizationQuery.build(
        query_id=_stable_id("operation_query", intent.intent_sha256),
        idempotency_key=attempt_id,
        principal_id=binding.principal_id,
        actor_workload_id=binding.actor_workload_id,
        operation_id=operation_id,
        resource_type="execution_profile",
        resource_ref=profile.release_ref,
        tenant_id=binding.tenant_id,
        cell_id=binding.cell_id,
        purpose_id=_data_use_purpose_id(purpose),
        environment_id=authority.environment_id,
        workflow_release_id=binding.workflow_release_id,
        execution_context_id=binding.context_id,
        enforcing_gateway_id=authority.enforcing_gateway_id,
        observed_at_utc=observed_at_utc,
    )
    decision = authority.authorization_client.authorize_operation(query)
    if type(decision) is not ProductOperationDecision:
        raise TypeError("Product Authorization returned an invalid decision")
    decision.validate()
    if (
        decision.query_id != query.query_id
        or decision.query_sha256 != query.query_sha256
    ):
        raise PermissionError("Product Authorization decision closure mismatch")
    observation = authority.controller.record_gateway_observation(
        intent_ref=intent.intent_ref,
        decision_ref=decision.decision_ref,
        decision_sha256=decision.decision_sha256,
        effect=decision.effect,
        effect_evidence_ref=None,
        grant_disposition_ref=None,
        observed_at_utc=observed_at_utc,
    )
    return _AttemptAuthorizationEvidence(
        intent=intent,
        decision=decision,
        observation=observation,
    )


def _build_canonical_request(
    *,
    run_request: ModuleExecutionRequest,
    module: RuntimeModuleRelease,
    profile: ExecutionProfileRelease,
    variant_request: ModuleVariantRequest,
    variant: ModuleExecutionVariantRecord,
    attempt_start: ModuleAttemptStartedRecord,
    evidence: _AttemptAuthorizationEvidence | None,
    authority: ModuleExecutionAuthority | None,
) -> AuthorizedAgentExecutionRequest:
    """Freeze one canonical adapter request from committed kernel facts."""

    receipt_payload = {
        "module_run_id": attempt_start.module_run_id,
        "variant_id": attempt_start.variant_id,
        "attempt_id": attempt_start.attempt_id,
        "attempt_ordinal": attempt_start.attempt_ordinal,
        "recorded_at_utc": attempt_start.recorded_at_utc,
    }
    authorized_inputs = tuple(
        AuthorizedExecutionInput(
            execution_input_id=binding.logical_name,
            input_ref=binding.input_ref,
            input_sha256=binding.input_sha256,
            schema_ref=binding.schema_ref,
            schema_sha256=binding.schema_sha256,
            media_type=binding.media_type,
            logical_name=binding.logical_name,
            local_handle=f"inputs/{binding.logical_name}",
        )
        for binding in run_request.inputs
    )
    return AuthorizedAgentExecutionRequest.build(
        workflow_execution_id=None,
        isolated_scope_ref=run_request.isolated_scope_ref,
        isolated_scope_sha256=run_request.isolated_scope_sha256,
        module_run_id=variant.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=attempt_start.attempt_id,
        module_id=module.module_id,
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        execution_profile_id=profile.execution_profile_id,
        execution_profile_ref=profile.release_ref,
        execution_profile_sha256=profile.release_sha256,
        attempt_begin_receipt_ref=f"attempt-begin:{attempt_start.attempt_id}",
        attempt_begin_receipt_sha256=_canonical_sha256(receipt_payload),
        prompt_envelope_ref=variant_request.prompt_envelope_ref,
        prompt_envelope_sha256=variant_request.prompt_envelope_sha256,
        output_schema_ref=module.output_schema_ref,
        output_schema_sha256=module.output_schema_sha256,
        execution_authorization_binding_ref=(
            authority.binding.binding_ref if authority is not None else None
        ),
        execution_authorization_binding_sha256=(
            authority.binding.binding_sha256 if authority is not None else None
        ),
        protected_operation_intent_ref=(
            evidence.intent.intent_ref if evidence is not None else None
        ),
        protected_operation_intent_sha256=(
            evidence.intent.intent_sha256 if evidence is not None else None
        ),
        product_operation_decision_ref=(
            evidence.decision.decision_ref if evidence is not None else None
        ),
        product_operation_decision_sha256=(
            evidence.decision.decision_sha256 if evidence is not None else None
        ),
        gateway_authorization_observation_ref=(
            evidence.observation.observation_ref if evidence is not None else None
        ),
        gateway_authorization_observation_sha256=(
            evidence.observation.observation_sha256 if evidence is not None else None
        ),
        operation_grant_ref=None,
        operation_grant_sha256=None,
        grant_disposition_ref=None,
        input_closure_sha256=run_request.input_closure_sha256,
        data_use_purpose_id=_data_use_purpose_id(run_request.purpose),
        authorized_inputs=authorized_inputs,
        idempotency_key=attempt_start.attempt_id,
    )


def _assert_authority_binding_closure(
    binding: ExecutionAuthorizationContextBinding,
    request: ModuleExecutionRequest,
) -> None:
    """Bind the caller-supplied authority to this exact isolated run."""

    expected_scope_id = isolated_execution_scope_id(
        request.isolated_scope_ref,
        request.isolated_scope_sha256,
    )
    exact = (
        binding.workflow_execution_id == expected_scope_id,
        binding.execution_input_package_ref == request.input_package_ref,
        binding.execution_input_package_sha256 == request.input_package_sha256,
    )
    if not all(exact):
        raise PermissionError("module execution authority closure mismatch")


def _assert_descriptor_covers_profile(
    descriptor: AgentExecutionAdapterDescriptor,
    profile: ExecutionProfileRelease,
) -> None:
    """Reject an adapter whose advertised capability cannot carry the profile."""

    if type(descriptor) is not AgentExecutionAdapterDescriptor:
        raise ValueError("adapter must expose an exact descriptor")
    descriptor.validate()
    exact = (
        descriptor.adapter_id == profile.executor_adapter_id,
        descriptor.adapter_revision == profile.executor_adapter_revision,
        descriptor.transport_kind == profile.transport_kind,
        descriptor.provider_id == profile.provider_id,
    )
    if not all(exact):
        raise PermissionError(
            "adapter descriptor identity differs from the Execution Profile"
        )
    covers = (
        profile.execution_mode in descriptor.supported_execution_modes,
        profile.semantic_input_delivery_mode
        in descriptor.supported_input_delivery_modes,
        profile.network_policy in descriptor.supported_network_policies,
        profile.output_constraint_mode
        in descriptor.supported_output_constraint_modes,
    )
    if not all(covers):
        raise PermissionError(
            "adapter descriptor capability does not cover the Execution Profile"
        )


def _empty_usage() -> ModuleUsageObservation:
    return ModuleUsageObservation(
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        cache_creation_tokens=None,
    )


def _record_failed_attempt(
    *,
    variant: ModuleExecutionVariantRecord,
    attempt_start: ModuleAttemptStartedRecord,
    failure_class: str,
    usage: ModuleUsageObservation,
    ended_at: str,
    payload: Mapping[str, Any],
    artifact_host: ModuleArtifactHost,
    ledger: ModuleExecutionLedger,
) -> tuple[ModuleAttemptRecord, tuple[ModuleOutputBinding, ...]]:
    """Commit one kernel-owned failed Attempt with its bounded diagnostic."""

    attempt = _failed_attempt(
        variant=variant,
        attempt_start=attempt_start,
        failure_class=failure_class,
        usage=usage,
        ended_at=ended_at,
        detail=_commit_kernel_failure_detail(
            artifact_host,
            variant=variant,
            attempt_start=attempt_start,
            failure_class=failure_class,
            payload=payload,
        ),
    )
    ledger.commit_attempt(attempt)
    return attempt, ()


def _assert_result_lineage_resolvable(
    artifact_host: ModuleArtifactHost,
    result: AgentExecutionResult,
) -> None:
    """Require adapter-reported Cell refs to resolve through the kernel host.

    An adapter composed against a different artifact store would otherwise
    commit ledger records whose trace and failure-detail refs the Runtime's
    own content boundary cannot serve.
    """

    artifact_host.read_bytes(
        result.cell_local_trace_ref,
        result.cell_local_trace_sha256,
    )
    if result.failure is not None and result.failure.detail_ref is not None:
        artifact_host.read_bytes(
            result.failure.detail_ref,
            result.failure.detail_sha256,
        )


def _assert_staged_output_conforms(
    release_registry: RuntimeReleaseRegistry,
    *,
    module: RuntimeModuleRelease,
    output_slot_id: str,
    content: bytes,
) -> None:
    """Validate staged bytes before finalization can make them authoritative.

    Provider adapters validate before staging; the kernel re-checks because it
    is the finalization authority and an in-process double bypasses adapter
    validation entirely. The committed binding claims the registered output
    schema, so the bytes must actually satisfy it.
    """

    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(
            f"staged output {output_slot_id} is not canonical JSON"
        ) from exc
    try:
        schema_asset = release_registry.get_schema_asset(
            module.output_schema_ref,
            module.output_schema_sha256,
        )
    except KeyError:
        # Deterministic modules may reference an output schema that is not
        # registered as a schema asset; the JSON media claim is still checked.
        return
    # Imported lazily so the dependency-free core namespace stays importable
    # from a clean wheel without provider extras.
    from jsonschema import Draft202012Validator

    errors = sorted(
        Draft202012Validator(schema_asset.schema_document()).iter_errors(
            payload
        ),
        key=lambda error: tuple(str(item) for item in error.path),
    )
    if errors:
        first = errors[0]
        location = "/".join(str(item) for item in first.path) or "#"
        raise ValueError(
            f"staged output {output_slot_id} violates the registered Module "
            f"schema at {location}: {first.message}"
        )


def _commit_kernel_failure_detail(
    artifact_host: ModuleArtifactHost,
    *,
    variant: ModuleExecutionVariantRecord,
    attempt_start: ModuleAttemptStartedRecord,
    failure_class: str,
    payload: Mapping[str, Any],
) -> tuple[str, str] | None:
    """Commit one bounded Cell-local kernel diagnostic for a failed Attempt."""

    detail = artifact_host.commit_failure_detail(
        module_run_id=variant.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=attempt_start.attempt_id,
        failure_class=failure_class,
        content=json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"),
        media_type="application/json",
    )
    detail.validate()
    return (detail.detail_ref, detail.detail_sha256)


def _failed_attempt(
    *,
    variant: ModuleExecutionVariantRecord,
    attempt_start: ModuleAttemptStartedRecord,
    failure_class: str,
    usage: ModuleUsageObservation,
    ended_at: str,
    status: str = "failed",
    detail: tuple[str, str] | None = None,
) -> ModuleAttemptRecord:
    return ModuleAttemptRecord(
        module_run_id=variant.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=attempt_start.attempt_id,
        status=status,
        output_refs=(),
        usage=usage,
        failure_class=failure_class,
        period_start_at_utc=attempt_start.recorded_at_utc,
        period_end_at_utc=ended_at,
        recorded_at_utc=ended_at,
        tool_calls=(),
        prompt_envelope_ref=variant.prompt_envelope_ref,
        prompt_envelope_sha256=variant.prompt_envelope_sha256,
        failure_detail_ref=detail[0] if detail is not None else None,
        failure_detail_sha256=detail[1] if detail is not None else None,
    )


def _assert_profile_shadow_executable(
    release_registry: RuntimeReleaseRegistry,
    profile: ExecutionProfileRelease,
) -> None:
    state = release_registry.get_admission_state(
        ReleaseSubjectKind.EXECUTION_PROFILE,
        profile.release_ref,
    )
    if state not in {
        ReleaseAdmissionState.CANDIDATE,
        ReleaseAdmissionState.SHADOW_EXECUTABLE,
        ReleaseAdmissionState.PRODUCTION_CANARY,
        ReleaseAdmissionState.ACTIVE,
    }:
        raise PermissionError(f"Execution Profile is not shadow-executable: {state.value}")


def _assert_model_only_test_evaluation_profile(
    profile: ExecutionProfileRelease,
) -> None:
    """Admit only the first model-backed Test/Evaluation capability slice.

    Registration already guarantees that a ``tool_free`` profile carries no
    tools, no writable Attempt workspace, inline delivery, and a denied
    network; re-encoding those invariants here is how the gate and the
    profile contract drift apart.
    """

    if profile.execution_mode != "tool_free":
        raise NotImplementedError(
            "model-backed Test/Evaluation admits only tool_free Execution "
            "Profiles"
        )


def _assert_module_dependencies_shadow_executable(
    release_registry: RuntimeReleaseRegistry,
    module: RuntimeModuleRelease,
) -> None:
    dependencies: list[tuple[ReleaseSubjectKind, str]] = []
    if module.source_skill_package_ref is not None:
        if module.source_skill_package_sha256 is None:
            raise ValueError("Module source Skill Package hash is missing")
        release_registry.get_skill_package(
            module.source_skill_package_ref,
            module.source_skill_package_sha256,
        )
        dependencies.append(
            (ReleaseSubjectKind.SKILL_PACKAGE, module.source_skill_package_ref)
        )
    if module.prompt_bundle_ref is not None:
        if module.prompt_bundle_sha256 is None:
            raise ValueError("Module Prompt Bundle hash is missing")
        prompt_bundle = release_registry.get_prompt_bundle(
            module.prompt_bundle_ref,
            module.prompt_bundle_sha256,
        )
        dependencies.append(
            (ReleaseSubjectKind.PROMPT_BUNDLE, module.prompt_bundle_ref)
        )
        dependencies.extend(
            (
                ReleaseSubjectKind.PROMPT_COMPONENT,
                member.member_ref,
            )
            for member in prompt_bundle.members
            if member.member_ref.startswith("prompt-component:")
        )
    allowed = {
        ReleaseAdmissionState.CANDIDATE,
        ReleaseAdmissionState.SHADOW_EXECUTABLE,
        ReleaseAdmissionState.PRODUCTION_CANARY,
        ReleaseAdmissionState.ACTIVE,
    }
    for kind, release_ref in dependencies:
        state = release_registry.get_admission_state(kind, release_ref)
        if state not in allowed:
            raise PermissionError(
                f"{kind.value} dependency is not shadow-executable: {state.value}"
            )


def _resolve_shadow_outputs(
    module: RuntimeModuleRelease,
    module_run_id: str,
    variants: tuple[ModuleExecutionVariantRecord, ...],
    attempts: tuple[ModuleAttemptRecord, ...],
    outputs: tuple[ModuleOutputBinding, ...],
    recorded_at_utc: str,
) -> ModuleOutputResolutionRecord | None:
    successful = tuple(attempt for attempt in attempts if attempt.status == "completed")
    if len(successful) != len(attempts):
        return None
    if module.output_resolution_policy is OutputResolutionPolicy.DIRECT_SINGLE:
        candidate_ref = f"attempt-output-bundle:{successful[0].attempt_id}"
        candidate_sha256 = _canonical_sha256(
            [output.as_dict() for output in outputs]
        )
        return ModuleOutputResolutionRecord.build(
            module_output_resolution_id=_stable_id(
                "module_resolution", module_run_id, "resolved"
            ),
            workflow_execution_id=None,
            source_module_run_id=module_run_id,
            resolution_mode=module.output_resolution_policy.value,
            candidate_output_bundle_refs=(candidate_ref,),
            candidate_output_bundle_sha256s=(candidate_sha256,),
            evaluation_set_ref=None,
            selection_ref=None,
            resolved_execution_output_refs=tuple(
                output.output_ref for output in outputs
            ),
            resolution_status="resolved",
            recorded_at_utc=recorded_at_utc,
        )
    return None


__all__ = [
    "AgentExecutionAdapterRegistry",
    "ModuleExecutionAuthority",
    "ModuleExecutionRequest",
    "ModuleRunResult",
    "ModuleVariantRequest",
    "isolated_execution_scope_id",
    "run_module",
]
