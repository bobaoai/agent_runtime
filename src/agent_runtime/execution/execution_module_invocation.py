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
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Callable, Mapping

from ..foundation.foundation_contract_validation import (
    format_utc_timestamp,
    parse_utc_timestamp,
    validate_id,
)
from ..foundation.foundation_retry_policy_validation import (
    validate_retry_attempt_budget,
)
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
    WorkflowModuleExecutionRequest,
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
    isolated_execution_scope_id,
)
from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ExecutionProfileRelease,
    ModuleExecutionPurpose,
    OutputResolutionPolicy,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
    RetryPolicyRelease,
    RuntimeModuleRelease,
)
from ..contracts.ledger_lineage_definition import (
    ModuleAttemptRecord,
    ModuleAttemptStartedRecord,
    ModuleExecutionVariantRecord,
    ModuleOutputResolutionRecord,
    ModuleToolCallObservation,
    ModuleRunRecord,
    ModuleUsageObservation,
)
from ..contracts.ledger_record_definition import (
    WorkflowAttemptRecord,
    WorkflowAttemptStartedRecord,
)
from ..invocation.invocation_tool_definition import ModuleArtifactHost
from ..ledger.ledger_workflow_module_recording import (
    WorkflowModuleLedgerRecorder,
)
from ..registry.registry_release_registration import (
    RuntimeReleaseRegistry,
    allowed_release_admission_states,
)
from .execution_authorization_coordination import ExecutionAuthorizationController
from .execution_authorization_resolution import ProductOperationAuthorizationClient


_MODEL_INVOCATION_OPERATION_IDS = frozenset({"invoke_model", "model_execute"})


class AttemptToolReconciliationRequiredError(RuntimeError):
    """An Adapter failed after a tool grant but before observation closure."""


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
        module: RuntimeModuleRelease,
        profile: ExecutionProfileRelease,
        purpose: ModuleExecutionPurpose,
        authority: ModuleExecutionAuthority | None,
        workflow_ledger: WorkflowModuleLedgerRecorder | None,
        clock: Callable[[], str],
    ) -> None:
        self._request = request
        self._artifact_host = artifact_host
        self._module = module
        self._profile = profile
        self._purpose = purpose
        self._authority = authority
        self._workflow_ledger = workflow_ledger
        self._clock = clock
        self._inputs_by_handle = {
            item.local_handle: item for item in request.authorized_inputs
        }
        self._staged: dict[str, tuple[OutputSubmission, bytes]] = {}
        self._authorized_operation_names: list[str] = []
        self._dynamic_authorization_refused = False

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
        """Authorize one exact Gateway tool before its resource call."""

        try:
            return self._authorize_operation(request)
        except Exception:
            self._dynamic_authorization_refused = True
            raise

    def _authorize_operation(
        self,
        request: ProviderOperationIntent,
    ) -> AuthorizedOperationReceipt:
        """Resolve one dynamic operation while the public wrapper tracks denial."""

        if type(request) is not ProviderOperationIntent:
            raise ValueError("request must be an exact ProviderOperationIntent")
        request.validate()
        if self._authority is None:
            raise PermissionError("dynamic operation requires execution authority")
        if not (
            self._profile.execution_mode == "agent"
            and self._profile.semantic_input_delivery_mode == "gateway_read"
            and self._profile.attempt_workspace_policy == "none"
            and self._profile.network_policy == "gateway_only"
            and self._profile.tool_policy
        ):
            raise PermissionError(
                "dynamic operation is outside the admitted Gateway profile"
            )
        exact_lineage = (
            request.workflow_execution_id
            == self._request.execution_scope_id,
            request.module_run_id == self._request.module_run_id,
            request.variant_id == self._request.variant_id,
            request.attempt_id == self._request.attempt_id,
            request.entitlement_snapshot_hash
            == self._request.execution_authorization_binding_sha256,
        )
        if not all(exact_lineage):
            raise PermissionError(
                "dynamic operation crossed its authorized Attempt lineage"
            )
        if (
            request.capability_id != request.action_id
            or request.capability_id not in self._profile.tool_policy
            or request.action_id not in self._module.declared_operation_ids
        ):
            raise PermissionError(
                "dynamic operation is absent from the Profile and Module closure"
            )

        observed_at_utc = self._clock()
        authority = self._authority
        resource_ref = f"gateway-resource:{request.resource_id}"
        intent = authority.controller.commit_protected_operation_intent(
            binding_ref=authority.binding.binding_ref,
            module_run_id=self._request.module_run_id,
            module_release_ref=self._module.release_ref,
            module_release_sha256=self._module.release_sha256,
            operation_id=request.action_id,
            resource_ref=resource_ref,
            enforcing_gateway_id=authority.enforcing_gateway_id,
            idempotency_key=request.idempotency_key,
            requires_grant=False,
            operation_grant_ref=None,
            observed_at_utc=observed_at_utc,
        )
        query = OperationAuthorizationQuery.build(
            query_id=_stable_id("operation_query", intent.intent_sha256),
            idempotency_key=request.idempotency_key,
            principal_id=authority.binding.principal_id,
            actor_workload_id=authority.binding.actor_workload_id,
            operation_id=request.action_id,
            resource_type="gateway_resource",
            resource_ref=resource_ref,
            tenant_id=authority.binding.tenant_id,
            cell_id=authority.binding.cell_id,
            purpose_id=_data_use_purpose_id(self._purpose),
            environment_id=authority.environment_id,
            workflow_release_id=authority.binding.workflow_release_id,
            execution_context_id=authority.binding.context_id,
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
            or decision.observed_at_utc != query.observed_at_utc
        ):
            raise PermissionError(
                "dynamic Product Authorization decision closure mismatch"
            )
        observation = authority.controller.record_gateway_observation(
            intent_ref=intent.intent_ref,
            decision_ref=decision.decision_ref,
            decision_sha256=decision.decision_sha256,
            effect=decision.effect,
            effect_evidence_ref=None,
            grant_disposition_ref=None,
            observed_at_utc=observed_at_utc,
        )
        if decision.effect is not GatewayDecisionEffect.ALLOW:
            raise PermissionError(
                f"dynamic operation denied: {decision.reason_code}"
            )
        if self._workflow_ledger is not None:
            self._workflow_ledger.authorize_tool_call(
                request,
                authorization_intent_ref=intent.intent_ref,
                authorization_intent_sha256=intent.intent_sha256,
                authorization_decision_ref=decision.decision_ref,
                authorization_decision_sha256=decision.decision_sha256,
                authorization_observation_ref=observation.observation_ref,
                authorization_observation_sha256=(
                    observation.observation_sha256
                ),
                recorded_at_utc=observed_at_utc,
            )
        receipt = AuthorizedOperationReceipt(
            receipt_id=_stable_id(
                "authorized_operation_receipt",
                observation.observation_sha256,
            ),
            decision_ref=decision.decision_ref,
            decision_sha256=decision.decision_sha256,
            grant_disposition_ref=None,
            recorded_at_utc=observed_at_utc,
        )
        receipt.validate()
        self._authorized_operation_names.append(request.action_id)
        return receipt

    def assert_tool_observation_closure(
        self,
        observations: tuple[ModuleToolCallObservation, ...],
    ) -> None:
        """Require exact ordered lineage for every dynamically allowed call."""

        if self._dynamic_authorization_refused:
            raise PermissionError(
                "at least one dynamic operation was refused during the Attempt"
            )
        observed_names = tuple(item.tool_name for item in observations)
        if observed_names != tuple(self._authorized_operation_names):
            raise PermissionError(
                "tool observations differ from Runtime-authorized operations"
            )

    @property
    def authorized_operation_count(self) -> int:
        """Return the number of dynamic operations granted in this Attempt."""

        return len(self._authorized_operation_names)

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
    if request.purpose not in {
        ModuleExecutionPurpose.TEST,
        ModuleExecutionPurpose.EVALUATION,
    }:
        raise NotImplementedError(
            "production Module execution awaits AR09 target authorization admission"
        )
    return _run_module(
        request,
        release_registry=release_registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=ledger,
        authority=authority,
        workflow_ledger=None,
        clock=clock,
    )


def run_workflow_module(
    request: WorkflowModuleExecutionRequest,
    *,
    release_registry: RuntimeReleaseRegistry,
    adapters: AgentExecutionAdapterRegistry,
    artifact_host: ModuleArtifactHost,
    ledger: ModuleExecutionLedger,
    workflow_ledger: WorkflowModuleLedgerRecorder,
    authority: ModuleExecutionAuthority | None = None,
    clock: Callable[[], str] = _utc_now,
) -> ModuleRunResult:
    """Run one Module under an admitted Workflow Execution authority.

    The request carries the durable dispatch, Workflow node, and Module Run
    IDs. The recorder commits start/authorization facts before provider entry,
    atomically commits the result, and replays an already committed invocation
    without calling the provider again.
    """

    if type(request) is not WorkflowModuleExecutionRequest:
        raise ValueError(
            "request must be an exact WorkflowModuleExecutionRequest"
    )
    request.validate()
    if len(request.variants) != 1:
        raise NotImplementedError(
            "one Workflow Module Activity currently admits one Variant; "
            "A/B arms use separate durable dispatches"
        )
    module = release_registry.get_module(
        request.module_release_ref,
        request.module_release_sha256,
    )
    replay = workflow_ledger.replay_result(request=request, module=module)
    if replay is not None:
        return replay
    return _run_module(
        request,
        release_registry=release_registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=ledger,
        authority=authority,
        workflow_ledger=workflow_ledger,
        clock=clock,
    )


def _run_module(
    request: ModuleExecutionRequest | WorkflowModuleExecutionRequest,
    *,
    release_registry: RuntimeReleaseRegistry,
    adapters: AgentExecutionAdapterRegistry,
    artifact_host: ModuleArtifactHost,
    ledger: ModuleExecutionLedger,
    authority: ModuleExecutionAuthority | None,
    workflow_ledger: WorkflowModuleLedgerRecorder | None,
    clock: Callable[[], str],
) -> ModuleRunResult:
    """Shared kernel after isolated or Workflow-bound request admission."""

    existing = ledger.existing_result(request)
    if existing is not None:
        return existing

    module = release_registry.get_module(
        request.module_release_ref,
        request.module_release_sha256,
    )
    release_registry.assert_module_execution_allowed(module, request.purpose)
    _validate_module_execution_authority(
        module=module,
        request=request,
        authority=authority,
    )
    _, _, retry_policy = _resolve_and_admit_module_policies(
        release_registry,
        module=module,
        purpose=request.purpose,
    )
    max_attempts = validate_retry_attempt_budget(
        retry_policy.policy_document().get("max_attempts")
    )
    _assert_module_dependencies_shadow_executable(release_registry, module)
    if (
        module.output_resolution_policy is OutputResolutionPolicy.DIRECT_SINGLE
        and len(request.variants) != 1
    ):
        raise ValueError("direct_single Module Run requires exactly one Variant")

    started_at_utc = clock()
    module_run_id = (
        request.module_run_id
        if type(request) is WorkflowModuleExecutionRequest
        else _stable_id("module_run", request.request_id, request.request_sha256)
    )
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
        isolated_scope_ref=(
            None
            if type(request) is WorkflowModuleExecutionRequest
            else request.isolated_scope_ref
        ),
        isolated_scope_sha256=(
            None
            if type(request) is WorkflowModuleExecutionRequest
            else request.isolated_scope_sha256
        ),
        recorded_at_utc=started_at_utc,
        workflow_execution_id=(
            request.workflow_execution_id
            if type(request) is WorkflowModuleExecutionRequest
            else None
        ),
    )

    resolved_profiles: list[ExecutionProfileRelease] = []
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
            _assert_admitted_test_evaluation_profile(
                module,
                profile,
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
        attempt_ordinal = (
            request.attempt_ordinal
            if type(request) is WorkflowModuleExecutionRequest
            else 1
        )
        if attempt_ordinal > max_attempts:
            raise ValueError("Attempt ordinal exceeds Retry Policy max_attempts")
        attempt_id = _stable_id(
            "module_attempt", variant_id, str(attempt_ordinal)
        )
        resolved_profiles.append(profile)
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
                recorded_at_utc=started_at_utc,
            )
        )
        attempt_starts.append(
            ModuleAttemptStartedRecord(
                module_run_id=module_run_id,
                variant_id=variant_id,
                attempt_id=attempt_id,
                attempt_ordinal=attempt_ordinal,
                recorded_at_utc=started_at_utc,
            )
        )

    if workflow_ledger is not None:
        if type(request) is not WorkflowModuleExecutionRequest:
            raise ValueError(
                "canonical Workflow ledger supplied for an isolated Module Run"
            )
        for variant, attempt_start in zip(
            variant_records,
            attempt_starts,
            strict=True,
        ):
            _assert_workflow_attempt_may_start(
                workflow_ledger=workflow_ledger,
                request=request,
                variant_id=variant.variant_id,
                attempt_id=attempt_start.attempt_id,
                max_attempts=max_attempts,
            )

    concurrent_result = ledger.begin(
        request,
        module_run,
        tuple(variant_records),
        tuple(attempt_starts),
    )
    if concurrent_result is not None:
        return concurrent_result

    if workflow_ledger is not None:
        assert type(request) is WorkflowModuleExecutionRequest
        workflow_ledger.record_module_start(
            request=request,
            module=module,
            variants=tuple(variant_records),
            profiles=tuple(resolved_profiles),
            retry_policy_ref=retry_policy.release_ref,
            retry_policy_sha256=retry_policy.release_sha256,
            max_attempts=max_attempts,
            recorded_at_utc=started_at_utc,
        )
        for variant, profile, attempt_start in zip(
            variant_records,
            resolved_profiles,
            attempt_starts,
            strict=True,
        ):
            workflow_ledger.begin_attempt(
                request=request,
                variant=variant,
                profile=profile,
                attempt_id=attempt_start.attempt_id,
                attempt_ordinal=attempt_start.attempt_ordinal,
                parent_attempt_id=request.parent_attempt_id,
                recorded_at_utc=attempt_start.recorded_at_utc,
            )

    resolved_adapters: list[AuthorizedAgentExecutionAdapter] = []
    for profile in resolved_profiles:
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
        resolved_adapters.append(adapter)

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
            workflow_ledger=workflow_ledger,
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
        workflow_execution_id=(
            request.workflow_execution_id
            if type(request) is WorkflowModuleExecutionRequest
            else None
        ),
    )
    if workflow_ledger is not None:
        assert type(request) is WorkflowModuleExecutionRequest
        resolution = workflow_ledger.canonicalize_output_resolution(
            request=request,
            resolution=resolution,
        )
        workflow_ledger.record_output_resolution(
            request=request,
            resolution=resolution,
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
    run_request: ModuleExecutionRequest | WorkflowModuleExecutionRequest,
    module: RuntimeModuleRelease,
    profile: ExecutionProfileRelease,
    adapter: AuthorizedAgentExecutionAdapter,
    variant_request: ModuleVariantRequest,
    variant: ModuleExecutionVariantRecord,
    attempt_start: ModuleAttemptStartedRecord,
    artifact_host: ModuleArtifactHost,
    authority: ModuleExecutionAuthority | None,
    workflow_ledger: WorkflowModuleLedgerRecorder | None,
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
                ended_at_utc=clock(),
                payload={
                    "disposition": "authorization_refused_at_dispatch",
                    "exception_type": type(exc).__name__,
                    "reason": str(exc),
                },
                artifact_host=artifact_host,
                ledger=ledger,
                workflow_ledger=workflow_ledger,
                workflow_request=(
                    run_request
                    if type(run_request) is WorkflowModuleExecutionRequest
                    else None
                ),
                module=module,
                profile=profile,
            )
        if evidence.decision.effect is not GatewayDecisionEffect.ALLOW:
            return _record_failed_attempt(
                variant=variant,
                attempt_start=attempt_start,
                failure_class="authorization",
                usage=_empty_usage(),
                ended_at_utc=clock(),
                payload={
                    "disposition": "product_operation_denied",
                    "reason_code": evidence.decision.reason_code,
                    "decision_ref": evidence.decision.decision_ref,
                },
                artifact_host=artifact_host,
                ledger=ledger,
                workflow_ledger=workflow_ledger,
                workflow_request=(
                    run_request
                    if type(run_request) is WorkflowModuleExecutionRequest
                    else None
                ),
                module=module,
                profile=profile,
            )

        if workflow_ledger is not None:
            assert type(run_request) is WorkflowModuleExecutionRequest
            workflow_ledger.authorize_model_call(
                request=run_request,
                profile=profile,
                variant_id=variant.variant_id,
                attempt_id=attempt_start.attempt_id,
                operation_id=next(
                    operation_id
                    for operation_id in module.declared_operation_ids
                    if operation_id in _MODEL_INVOCATION_OPERATION_IDS
                ),
                authorization_intent_ref=evidence.intent.intent_ref,
                authorization_intent_sha256=evidence.intent.intent_sha256,
                authorization_decision_ref=evidence.decision.decision_ref,
                authorization_decision_sha256=(
                    evidence.decision.decision_sha256
                ),
                authorization_observation_ref=(
                    evidence.observation.observation_ref
                ),
                authorization_observation_sha256=(
                    evidence.observation.observation_sha256
                ),
                recorded_at_utc=clock(),
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
        module=module,
        profile=profile,
        purpose=run_request.purpose,
        authority=authority,
        workflow_ledger=workflow_ledger,
        clock=clock,
    )

    staged: tuple[tuple[OutputSubmission, bytes], ...] = ()
    validated_tool_observations: tuple[ModuleToolCallObservation, ...] = ()
    tool_observation_closure_validated = False
    try:
        result = adapter.execute(canonical_request, host)
        if type(result) is not AgentExecutionResult:
            raise TypeError("adapter returned an invalid result type")
        result.validate()
        host.assert_tool_observation_closure(result.tool_observations)
        if (
            result.provider_id != profile.provider_id
            or result.model_id != profile.model_id
        ):
            raise ValueError("adapter result provider identity differs from profile")
        _assert_result_lineage_resolvable(artifact_host, result)
        validated_tool_observations = result.tool_observations
        tool_observation_closure_validated = True
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
    except PermissionError as exc:
        if (
            host.authorized_operation_count
            and not tool_observation_closure_validated
        ):
            raise AttemptToolReconciliationRequiredError(
                "Adapter failed after tool authorization; reconcile the "
                "durable Attempt before retry"
            ) from exc
        return _record_failed_attempt(
            variant=variant,
            attempt_start=attempt_start,
            failure_class="authorization",
            usage=_empty_usage(),
            ended_at_utc=clock(),
            payload={
                "disposition": "dynamic_operation_authorization_refused",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            },
            artifact_host=artifact_host,
            ledger=ledger,
            workflow_ledger=workflow_ledger,
            workflow_request=(
                run_request
                if type(run_request) is WorkflowModuleExecutionRequest
                else None
            ),
            module=module,
            profile=profile,
            tool_calls=validated_tool_observations,
        )
    except Exception as exc:
        if (
            host.authorized_operation_count
            and not tool_observation_closure_validated
        ):
            raise AttemptToolReconciliationRequiredError(
                "Adapter failed after tool authorization; reconcile the "
                "durable Attempt before retry"
            ) from exc
        return _record_failed_attempt(
            variant=variant,
            attempt_start=attempt_start,
            failure_class="unknown",
            usage=_empty_usage(),
            ended_at_utc=clock(),
            payload={
                "disposition": "adapter_conformance_failure",
                "exception_type": type(exc).__name__,
                "message": str(exc),
            },
            artifact_host=artifact_host,
            ledger=ledger,
            workflow_ledger=workflow_ledger,
            workflow_request=(
                run_request
                if type(run_request) is WorkflowModuleExecutionRequest
                else None
            ),
            module=module,
            profile=profile,
            tool_calls=validated_tool_observations,
        )

    usage = ModuleUsageObservation(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )
    ended_at_utc = clock()
    deadline = parse_utc_timestamp(
        "attempt_start.recorded_at_utc",
        attempt_start.recorded_at_utc,
    ) + timedelta(seconds=profile.timeout_seconds)
    if parse_utc_timestamp("ended_at_utc", ended_at_utc) >= deadline:
        return _record_failed_attempt(
            variant=variant,
            attempt_start=attempt_start,
            failure_class="timeout",
            usage=usage,
            ended_at_utc=ended_at_utc,
            period_end_at_utc=format_utc_timestamp(deadline),
            payload={
                "disposition": "execution_deadline_exceeded",
                "deadline_at_utc": format_utc_timestamp(deadline),
                "observed_at_utc": ended_at_utc,
                "observed_terminal_status": result.terminal_status,
                "observed_failure_class": (
                    result.failure.failure_class
                    if result.failure is not None
                    else None
                ),
                "adapter_trace_ref": result.cell_local_trace_ref,
                "adapter_trace_sha256": result.cell_local_trace_sha256,
            },
            artifact_host=artifact_host,
            ledger=ledger,
            workflow_ledger=workflow_ledger,
            workflow_request=(
                run_request
                if type(run_request) is WorkflowModuleExecutionRequest
                else None
            ),
            module=module,
            profile=profile,
            tool_calls=result.tool_observations,
        )

    if result.terminal_status != "completed":
        assert result.failure is not None
        attempt = _failed_attempt(
            variant=variant,
            attempt_start=attempt_start,
            failure_class=result.failure.failure_class,
            usage=usage,
            ended_at_utc=ended_at_utc,
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
            tool_calls=result.tool_observations,
        )
        ledger.commit_attempt(attempt)
        if workflow_ledger is not None:
            assert type(run_request) is WorkflowModuleExecutionRequest
            workflow_ledger.finalize_attempt(
                request=run_request,
                module=module,
                profile=profile,
                attempt=attempt,
                outputs=(),
                artifact_host=artifact_host,
            )
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
                ended_at_utc=ended_at_utc,
                payload={
                    "disposition": "stale_result_quarantined",
                    "fence_ref": fence.fence_ref,
                    "reason_code": fence.reason_code,
                },
                artifact_host=artifact_host,
                ledger=ledger,
                workflow_ledger=workflow_ledger,
                workflow_request=(
                    run_request
                    if type(run_request) is WorkflowModuleExecutionRequest
                    else None
                ),
                module=module,
                profile=profile,
                tool_calls=result.tool_observations,
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
            period_end_at_utc=ended_at_utc,
            recorded_at_utc=ended_at_utc,
            tool_calls=result.tool_observations,
            prompt_envelope_ref=variant.prompt_envelope_ref,
            prompt_envelope_sha256=variant.prompt_envelope_sha256,
        )
        ledger.commit_attempt(completed)
        if workflow_ledger is not None:
            assert type(run_request) is WorkflowModuleExecutionRequest
            workflow_ledger.finalize_attempt(
                request=run_request,
                module=module,
                profile=profile,
                attempt=completed,
                outputs=tuple(committed),
                artifact_host=artifact_host,
            )
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
    operation_id = next(
        operation_id
        for operation_id in module.declared_operation_ids
        if operation_id in _MODEL_INVOCATION_OPERATION_IDS
    )
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
        or decision.observed_at_utc != query.observed_at_utc
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
    run_request: ModuleExecutionRequest | WorkflowModuleExecutionRequest,
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
    workflow_bound = type(run_request) is WorkflowModuleExecutionRequest
    return AuthorizedAgentExecutionRequest.build(
        workflow_execution_id=(
            run_request.workflow_execution_id if workflow_bound else None
        ),
        isolated_scope_ref=(
            None if workflow_bound else run_request.isolated_scope_ref
        ),
        isolated_scope_sha256=(
            None if workflow_bound else run_request.isolated_scope_sha256
        ),
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
    request: ModuleExecutionRequest | WorkflowModuleExecutionRequest,
) -> None:
    """Bind the caller-supplied authority to this exact isolated run."""

    expected_scope_id = (
        request.workflow_execution_id
        if type(request) is WorkflowModuleExecutionRequest
        else isolated_execution_scope_id(
            request.isolated_scope_ref,
            request.isolated_scope_sha256,
        )
    )
    exact = (
        binding.workflow_execution_id == expected_scope_id,
        binding.execution_input_package_ref == request.input_package_ref,
        binding.execution_input_package_sha256 == request.input_package_sha256,
    )
    if not all(exact):
        raise PermissionError("module execution authority closure mismatch")


def _validate_module_execution_authority(
    *,
    module: RuntimeModuleRelease,
    request: ModuleExecutionRequest | WorkflowModuleExecutionRequest,
    authority: ModuleExecutionAuthority | None,
) -> None:
    """Validate the request-bound authority before resolving Module Policies."""

    model_operation_ids = tuple(
        operation_id
        for operation_id in module.declared_operation_ids
        if operation_id in _MODEL_INVOCATION_OPERATION_IDS
    )
    if module.declared_operation_ids and len(model_operation_ids) != 1:
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
        return
    if authority is not None:
        raise ValueError(
            "a module execution authority was supplied for an operation-free "
            "Module; nothing would consume or enforce it"
        )


def _resolve_and_admit_module_policies(
    release_registry: RuntimeReleaseRegistry,
    *,
    module: RuntimeModuleRelease,
    purpose: ModuleExecutionPurpose,
) -> tuple[
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    RetryPolicyRelease,
]:
    """Resolve exact Module Policies and require purpose-specific admission."""

    behavior_policy = release_registry.get_behavior_policy(
        module.behavior_policy_ref,
        module.behavior_policy_sha256,
    )
    evaluation_policy = release_registry.get_evaluation_policy(
        module.evaluation_policy_ref,
        module.evaluation_policy_sha256,
    )
    retry_policy = release_registry.get_retry_policy(
        module.retry_policy_ref,
        module.retry_policy_sha256,
    )
    allowed = allowed_release_admission_states(purpose)
    for kind, release_ref in (
        (ReleaseSubjectKind.BEHAVIOR_POLICY, behavior_policy.release_ref),
        (ReleaseSubjectKind.EVALUATION_POLICY, evaluation_policy.release_ref),
        (ReleaseSubjectKind.RETRY_POLICY, retry_policy.release_ref),
    ):
        state = release_registry.get_admission_state(kind, release_ref)
        if state not in allowed:
            raise PermissionError(
                f"{kind.value} release is not admitted for "
                f"{purpose.value}: {state.value}"
            )
    return behavior_policy, evaluation_policy, retry_policy


def _assert_workflow_attempt_may_start(
    *,
    workflow_ledger: WorkflowModuleLedgerRecorder,
    request: WorkflowModuleExecutionRequest,
    variant_id: str,
    attempt_id: str,
    max_attempts: int,
) -> None:
    """Prove one contiguous committed Attempt lineage before execution effects."""

    request.validate()
    validate_retry_attempt_budget(max_attempts)
    if request.attempt_ordinal > max_attempts:
        raise ValueError("Attempt ordinal exceeds Retry Policy max_attempts")
    starts: tuple[WorkflowAttemptStartedRecord, ...] = (
        workflow_ledger.committed_attempt_starts(
            workflow_execution_id=request.workflow_execution_id,
        )
    )
    duplicate_ordinal = next(
        (
            row
            for row in starts
            if row.variant_id == variant_id
            and row.attempt_ordinal == request.attempt_ordinal
        ),
        None,
    )
    if duplicate_ordinal is not None:
        raise ValueError(
            "Variant already has a committed Attempt with this ordinal"
        )
    if any(row.attempt_id == attempt_id for row in starts):
        raise ValueError("derived Attempt identity is already committed")

    if request.attempt_ordinal == 1:
        return
    parent = next(
        (
            row
            for row in starts
            if row.attempt_id == request.parent_attempt_id
        ),
        None,
    )
    if parent is None:
        raise ValueError("retry Attempt requires a committed parent Attempt start")
    if parent.module_run_id != request.module_run_id:
        raise ValueError("parent Attempt start belongs to another Module Run")
    if parent.variant_id != variant_id:
        raise ValueError("parent Attempt start belongs to another Variant")
    if parent.attempt_ordinal != request.attempt_ordinal - 1:
        raise ValueError("retry Attempt parent ordinal is not contiguous")
    terminals: tuple[WorkflowAttemptRecord, ...] = (
        workflow_ledger.committed_attempts(
            workflow_execution_id=request.workflow_execution_id,
        )
    )
    parent_terminal = next(
        (
            row
            for row in terminals
            if row.attempt_id == parent.attempt_id
        ),
        None,
    )
    if parent_terminal is None:
        raise ValueError("retry Attempt parent is not terminal")
    if parent_terminal.status == "completed":
        raise ValueError("completed Attempt cannot parent a retry")


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
    if (
        profile.tool_policy
        and not descriptor.supports_dynamic_operation_authorization
    ):
        raise PermissionError(
            "Gateway Execution Profile requires an adapter with dynamic "
            "operation authorization"
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
    ended_at_utc: str,
    period_end_at_utc: str | None = None,
    payload: Mapping[str, Any],
    artifact_host: ModuleArtifactHost,
    ledger: ModuleExecutionLedger,
    workflow_ledger: WorkflowModuleLedgerRecorder | None = None,
    workflow_request: WorkflowModuleExecutionRequest | None = None,
    module: RuntimeModuleRelease | None = None,
    profile: ExecutionProfileRelease | None = None,
    tool_calls: tuple[ModuleToolCallObservation, ...] = (),
) -> tuple[ModuleAttemptRecord, tuple[ModuleOutputBinding, ...]]:
    """Commit one kernel-owned failed Attempt with its bounded diagnostic."""

    effective_failure_class = failure_class
    effective_period_end_at_utc = period_end_at_utc or ended_at_utc
    effective_payload = dict(payload)
    if profile is not None:
        deadline = parse_utc_timestamp(
            "attempt_start.recorded_at_utc",
            attempt_start.recorded_at_utc,
        ) + timedelta(seconds=profile.timeout_seconds)
        if parse_utc_timestamp("ended_at_utc", ended_at_utc) >= deadline:
            effective_failure_class = "timeout"
            effective_period_end_at_utc = format_utc_timestamp(deadline)
            if failure_class != "timeout":
                effective_payload.setdefault(
                    "original_failure_class",
                    failure_class,
                )
            effective_payload.setdefault(
                "disposition",
                "execution_deadline_exceeded",
            )
            effective_payload.setdefault(
                "deadline_at_utc",
                format_utc_timestamp(deadline),
            )
            effective_payload.setdefault("observed_at_utc", ended_at_utc)

    attempt = _failed_attempt(
        variant=variant,
        attempt_start=attempt_start,
        failure_class=effective_failure_class,
        usage=usage,
        ended_at_utc=ended_at_utc,
        period_end_at_utc=effective_period_end_at_utc,
        detail=_commit_kernel_failure_detail(
            artifact_host,
            variant=variant,
            attempt_start=attempt_start,
            failure_class=effective_failure_class,
            payload=effective_payload,
        ),
        tool_calls=tool_calls,
    )
    ledger.commit_attempt(attempt)
    if workflow_ledger is not None:
        if (
            workflow_request is None
            or module is None
            or profile is None
        ):
            raise ValueError(
                "Workflow failure finalization requires request, Module, and Profile"
            )
        workflow_ledger.finalize_attempt(
            request=workflow_request,
            module=module,
            profile=profile,
            attempt=attempt,
            outputs=(),
            artifact_host=artifact_host,
        )
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
    for observation in result.tool_observations:
        artifact_host.read_bytes(
            observation.request_ref,
            observation.request_sha256,
        )
        artifact_host.read_bytes(
            observation.response_ref,
            observation.response_sha256,
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
    ended_at_utc: str,
    period_end_at_utc: str | None = None,
    status: str = "failed",
    detail: tuple[str, str] | None = None,
    tool_calls: tuple[ModuleToolCallObservation, ...] = (),
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
        period_end_at_utc=period_end_at_utc or ended_at_utc,
        recorded_at_utc=ended_at_utc,
        tool_calls=tool_calls,
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


def _assert_admitted_test_evaluation_profile(
    module: RuntimeModuleRelease,
    profile: ExecutionProfileRelease,
) -> None:
    """Admit the exact model-backed Test/Evaluation capability slices.

    Registration validates the dimensions independently. The execution kernel
    intentionally admits only reviewed conjunctions, so a newly representable
    hybrid cannot become executable by accident.
    """

    non_model_operations = frozenset(module.declared_operation_ids).difference(
        _MODEL_INVOCATION_OPERATION_IDS
    )
    if (
        profile.execution_mode == "tool_free"
        and profile.semantic_input_delivery_mode == "inline"
        and profile.attempt_workspace_policy == "none"
        and profile.network_policy == "denied"
        and not profile.tool_policy
        and not profile.gateway_access_reasons
        and not non_model_operations
    ):
        return
    if (
        profile.execution_mode == "agent"
        and profile.semantic_input_delivery_mode == "inline"
        and profile.attempt_workspace_policy == "own_draft_read_write"
        and profile.network_policy == "denied"
        and not profile.tool_policy
        and not profile.gateway_access_reasons
        and not non_model_operations
        and profile.executor_adapter_id
        == "claude_agent_sdk_inline_draft_workspace_executor"
        and profile.executor_adapter_revision == "v1"
        and profile.transport_kind == "claude_agent_sdk"
        and profile.provider_id == "anthropic"
    ):
        return
    if (
        profile.execution_mode == "agent"
        and profile.semantic_input_delivery_mode == "gateway_read"
        and profile.attempt_workspace_policy == "none"
        and profile.network_policy == "gateway_only"
        and bool(profile.tool_policy)
        and bool(profile.gateway_access_reasons)
        and frozenset(profile.tool_policy) == non_model_operations
        and profile.executor_adapter_id == "claude_agent_sdk_gateway_executor"
        and profile.executor_adapter_revision == "v2"
        and profile.transport_kind == "claude_agent_sdk"
        and profile.provider_id == "anthropic"
    ):
        return
    raise NotImplementedError(
        "model-backed Test/Evaluation profile is outside the admitted "
        "tool-free, draft-workspace, and Gateway-read slices"
    )


def _assert_module_dependencies_shadow_executable(
    release_registry: RuntimeReleaseRegistry,
    module: RuntimeModuleRelease,
) -> None:
    dependencies: list[tuple[ReleaseSubjectKind, str]] = []
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
    *,
    workflow_execution_id: str | None = None,
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
            workflow_execution_id=workflow_execution_id,
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
    "run_workflow_module",
]
