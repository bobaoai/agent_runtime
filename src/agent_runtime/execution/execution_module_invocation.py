"""Authorization-enforcing Test and Evaluation execution kernel.

One canonical adapter contract carries every Module invocation. Model calls
require either external AR09 authority or explicit live self-test resources.
Their respective boundaries are checked before dispatch and terminal commit.
Absence of production authority never implicitly selects the self-test path.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any, Callable, Mapping

from ..foundation.foundation_contract_validation import parse_utc_timestamp, validate_id, validate_opaque_ref, validate_sha256
from ..contracts.execution_authorization_definition import (
    ExecutionAuthorizationContextEnvelope,
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
    ModuleInputBinding,
    ModuleOutputBinding,
    ModuleRunResult,
    ModuleVariantRequest,
    WorkflowModuleExecutionRequest,
)
from ..contracts.registry_release_definition import (
    MODEL_INVOCATION_OPERATION_IDS,
    partition_module_operation_ids,
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
    SelfTestResourceUnavailableError,
    isolated_execution_scope_id,
)
from ..contracts.registry_release_definition import (
    ExecutionProfileRelease,
    ExecutionVariantPolicyRelease,
    ModuleExecutionPurpose,
    OutputResolutionPolicy,
    ModuleRelease,
    WorkflowNodeKind,
    WorkflowRelease,
)
from ..contracts.registry_workflow_definition import ResolvedArtifactRef
from ..contracts.ledger_record_definition import (
    CommitReceipt, ExecutionInputRef, WorkflowExecutionRecord, WorkflowAttemptStartedRecord,
    WorkflowModuleRunRecord, WorkflowModuleExecutionVariantRecord,
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
from ..invocation.invocation_tool_definition import ModuleArtifactHost
from ..ledger.ledger_workflow_module_recording import (
    WorkflowModuleLedgerBinding,
    WorkflowModuleLedgerRecorder,
)
from ..ledger.ledger_workflow_execution_recording import (
    WorkflowExecutionLedgerBinding,
    WorkflowExecutionLedgerRecorder,
)
from ..ledger.ledger_execution_content_recording import RuntimeExecutionContentStore
from ..ledger.ledger_record_persistence import RuntimeExecutionRecordStore
from ..ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger
from ..invocation.invocation_prompt_assembly import build_inline_provider_prompt
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from .execution_authorization_coordination import (
    ExecutionAuthorizationController,
    InMemoryExecutionAuthorizationLedger,
)
from .execution_authorization_resolution import (
    ProductAuthorizationContextClient,
    ProductOperationAuthorizationClient,
)
from .execution_self_test_binding import ModuleSelfTestResources


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
        module: ModuleRelease,
        profile: ExecutionProfileRelease,
        purpose: ModuleExecutionPurpose,
        authority: ModuleExecutionAuthority | None,
        workflow_ledger: WorkflowModuleLedgerRecorder | None,
        clock: Callable[[], str],
        self_test: ModuleSelfTestResources | None = None,
    ) -> None:
        self._request = request
        self._artifact_host = artifact_host
        self._module = module
        self._profile = profile
        self._purpose = purpose
        self._authority = authority
        self._workflow_ledger = workflow_ledger
        self._clock = clock
        self._self_test = self_test
        self._inputs_by_handle = {
            item.local_handle: item for item in request.authorized_inputs
        }
        self._staged: dict[str, tuple[OutputSubmission, bytes]] = {}
        self._authorized_operation_names: list[str] = []
        self._dynamic_authorization_refused = False

    def read_authorized_input(self, local_handle: str) -> bytes:
        def read():
            entry = self._inputs_by_handle.get(local_handle)
            if entry is None:
                raise PermissionError(f"input handle is outside the authorized request table: {local_handle}")
            content = self._artifact_host.read_bytes(entry.input_ref, entry.input_sha256)
            if hashlib.sha256(content).hexdigest() != entry.input_sha256:
                raise ValueError("authorized input content hash mismatch")
            return content
        return self._self_test.guarded(read) if self._self_test is not None else read()

    def stage_output_bytes(
        self,
        submission: OutputSubmission,
        content: bytes,
    ) -> None:
        if self._self_test is not None:
            self._self_test.require_active()
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

    def validate_self_test_binding(self, request, *, adapter, artifact_host, workspace_root):
        """Resolve self-test evidence against the actual live host resources."""
        if self._self_test is None or request != self._request:
            raise SelfTestResourceUnavailableError("self-test request has no matching trusted host")
        self._self_test.check_invocation(request, adapter=adapter,
            artifact_host=artifact_host, workspace_root=workspace_root)

    def guard_self_test_launch(self, request, launch, *, adapter, artifact_host, workspace_root):
        """Order actual process creation with closing this request's resources."""
        if self._self_test is None:
            raise SelfTestResourceUnavailableError("self-test launch has no trusted resources")
        def guarded():
            self.validate_self_test_binding(request, adapter=adapter,
                artifact_host=artifact_host, workspace_root=workspace_root)
            return launch()
        return self._self_test.guarded(guarded)

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

    def staged_output(self, output_slot_id: str) -> bytes:
        try:
            return self._staged[output_slot_id][1]
        except KeyError as exc:
            raise ValueError(
                f"adapter reported an unstaged output slot: {output_slot_id}"
            ) from exc

    def recoverable_result_evidence(self, result: AgentExecutionResult):
        """Keep independently verified facts even when the result is rejected.

        A partial tool sequence cannot identify which same-named call is missing.
        Preserve a complete validated sequence or none; durable grants remain.
        """
        usage = _empty_usage()
        for name in usage.as_dict():
            try:
                reported_usage = replace(usage, **{name: getattr(result, name)})
                reported_usage.validate()
            except (TypeError, ValueError):
                continue
            usage = reported_usage

        trace = None
        try:
            validate_opaque_ref("provider_trace_ref", result.cell_local_trace_ref)
            validate_sha256("provider_trace_sha256", result.cell_local_trace_sha256)
            body = self._artifact_host.read_bytes(
                result.cell_local_trace_ref, result.cell_local_trace_sha256)
            if hashlib.sha256(body).hexdigest() == result.cell_local_trace_sha256:
                trace = (result.cell_local_trace_ref, result.cell_local_trace_sha256)
        except Exception:
            pass

        calls: list[ModuleToolCallObservation] = []
        seen_call_ids: set[str] = set()
        observations = result.tool_observations if type(result.tool_observations) is tuple else ()
        if len(observations) != len(self._authorized_operation_names):
            return usage, trace, ()
        for item, name in zip(observations, self._authorized_operation_names):
            try:
                if type(item) is not ModuleToolCallObservation:
                    raise ValueError("invalid tool observation type")
                item.validate()
                if item.tool_name != name or item.tool_call_id in seen_call_ids:
                    raise ValueError("tool observation sequence is not complete and unique")
                for ref, digest in ((item.request_ref, item.request_sha256),
                                    (item.response_ref, item.response_sha256)):
                    if hashlib.sha256(self._artifact_host.read_bytes(ref, digest)).hexdigest() != digest:
                        raise ValueError("tool evidence content hash mismatch")
            except Exception:
                return usage, trace, ()
            calls.append(item)
            seen_call_ids.add(item.tool_call_id)
        return usage, trace, tuple(calls)


def _prepare_registered_workflow_module(
    *, module_id, input_payload, idempotency_key, release_registry, workflow,
    variant_policy, adapters, artifact_host,
):
    """Freeze the same validated request for persistent and temporary executions."""
    from jsonschema import Draft202012Validator

    validate_id("module_id", module_id)
    validate_id("idempotency_key", idempotency_key)
    if type(input_payload) is not dict:
        raise ValueError("input_payload must be one JSON object")
    if type(workflow) is not WorkflowRelease or type(variant_policy) is not ExecutionVariantPolicyRelease:
        raise ValueError("execution requires exact Workflow and Variant Policy releases")
    if release_registry.get_workflow(workflow.release_ref, workflow.release_sha256) != workflow:
        raise ValueError("Workflow differs from the registered release")
    if release_registry.get_execution_variant_policy(variant_policy.release_ref, variant_policy.release_sha256) != variant_policy:
        raise ValueError("Variant Policy differs from the registered release")
    release_registry.assert_workflow_execution_allowed(workflow, ModuleExecutionPurpose.EVALUATION)
    if len(workflow.nodes) != 1 or workflow.nodes[0].node_kind is not WorkflowNodeKind.MODULE:
        raise ValueError("entry requires one registered Module node")
    node = workflow.nodes[0]
    if workflow.initial_node_id != node.node_id:
        raise ValueError("the Module node must be the Workflow entry")
    module = release_registry.get_module(node.module_release_ref, node.module_release_sha256)
    if module.module_id != module_id:
        raise ValueError("Workflow Module differs from the requested target")
    release_registry.assert_module_execution_allowed(module, ModuleExecutionPurpose.EVALUATION)
    selection = variant_policy.policy_document()
    if (
        selection["origin_kind"] != "workflow"
        or selection["origin_release_ref"] != workflow.release_ref
        or selection["origin_release_sha256"] != workflow.release_sha256
        or len(selection["bindings"]) != 1
        or selection["bindings"][0]["position_id"] != node.node_id
    ):
        raise ValueError("Variant Policy must select the exact Workflow Module node")
    selected = selection["bindings"][0]
    profile = release_registry.get_execution_profile(
        selected["execution_profile_release_ref"], selected["execution_profile_release_sha256"]
    )
    try:
        _assert_admitted_test_evaluation_profile(module, profile)
    except NotImplementedError as exc:
        raise ValueError(str(exc)) from exc
    adapter = adapters.resolve(profile.executor_adapter_id, profile.executor_adapter_revision)
    _assert_descriptor_covers_profile(adapter.descriptor, profile)
    for method in ("put_bytes", "artifact", "resolve_artifact_ref"):
        if not callable(getattr(artifact_host, method, None)):
            raise ValueError(f"artifact_host must implement {method}")
    schema = release_registry.get_schema_asset(module.input_schema_ref, module.input_schema_sha256)
    input_bytes = json.dumps(input_payload, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    Draft202012Validator(schema.schema_document()).validate(json.loads(input_bytes))
    execution_id = _stable_id("execution", idempotency_key)
    put_bytes = getattr(artifact_host, "put_bytes")
    task = put_bytes(
        artifact_kind_id="module_input", schema_version=module.input_schema_ref.rsplit("@", 1)[-1],
        schema_ref=module.input_schema_ref, schema_sha256=module.input_schema_sha256,
        media_type="application/json", content=input_bytes,
        idempotency_key=execution_id + "_task", logical_name="task_input",
    )
    input_binding = ModuleInputBinding("task_input", task.artifact_ref, task.artifact_sha256,
                                      module.input_schema_ref, module.input_schema_sha256, "application/json")
    bundle = release_registry.get_prompt_bundle(module.prompt_bundle_ref, module.prompt_bundle_sha256)
    prompt = build_inline_provider_prompt(
        compiled_static_body=bundle.compiled_static_body, execution_specific_instructions="",
        inputs=((input_binding, input_bytes),), output_constraint_mode=profile.output_constraint_mode,
    )
    prompt_ref = put_bytes(
        artifact_kind_id="prompt_envelope", schema_version="v1",
        schema_ref="schema:prompt_envelope@v1", schema_sha256=_canonical_sha256({"type": "string"}),
        media_type="text/plain", content=prompt.encode("utf-8"),
        idempotency_key=execution_id + "_prompt", logical_name="prompt_envelope",
    )
    request = WorkflowModuleExecutionRequest.build(
        request_id=_stable_id("request", execution_id), purpose=ModuleExecutionPurpose.EVALUATION,
        workflow_execution_id=execution_id, dispatch_id=_stable_id("dispatch", execution_id),
        workflow_node_id=node.node_id, module_run_id=_stable_id("module_run", execution_id),
        module_release_ref=module.release_ref, module_release_sha256=module.release_sha256,
        input_package_ref=task.artifact_ref, input_package_sha256=task.artifact_sha256,
        inputs=(input_binding,), variants=(ModuleVariantRequest(
            "default", 0, profile.release_ref, profile.release_sha256,
            prompt_ref.artifact_ref, prompt_ref.artifact_sha256,
        ),), idempotency_key=idempotency_key,
    )
    return request, module, profile, task, prompt_ref


def run_registered_workflow_module(
    *,
    module_id: str,
    input_payload: dict[str, Any],
    idempotency_key: str,
    release_registry: RuntimeReleaseRegistry,
    workflow: WorkflowRelease,
    variant_policy: ExecutionVariantPolicyRelease,
    authorize: Callable[
        [WorkflowModuleExecutionRequest],
        tuple[ExecutionAuthorizationContextEnvelope, ResolvedArtifactRef,
              ResolvedArtifactRef, ResolvedArtifactRef],
    ],
    context_client: ProductAuthorizationContextClient,
    operation_client: ProductOperationAuthorizationClient,
    enforcing_gateway_id: str,
    environment_id: str,
    adapters: AgentExecutionAdapterRegistry,
    artifact_host: ModuleArtifactHost,
    record_store: RuntimeExecutionRecordStore,
    content_store: RuntimeExecutionContentStore,
    claim_token_secret: bytes,
    clock: Callable[[], str] = _utc_now,
) -> ModuleRunResult:
    """Execute a fixed registered Module through one-node Workflow evaluation.

    Use this entry after the host has configured exact registered releases,
    compatible execution resources and authorization interfaces. Bind those
    environment arguments once in the host integration; each invocation supplies
    the intended module_id, JSON input_payload and an idempotency_key unique
    within the record store. This function does not construct a ModuleReviewer,
    load its authoring files, register releases or select a provider from the
    input. It currently accepts one Module node in evaluation purpose, not
    arbitrary multi-node Workflows or every possible Profile/Adapter combination.

    Args:
        module_id: Exact Module identity matching the Workflow's Module node.
        input_payload: JSON object conforming to that registered input schema.
            Put this invocation's task data here, according to its schema.
            The fixed prompt is read from the Registry.
        idempotency_key: Valid Runtime ID for this logical execution. The same
            key with unchanged inputs/releases returns a committed result;
            new task material requires a different key.
        release_registry: Loaded Registry with exact Workflow, Module, Prompt,
            Schema, Policy, Profile and Variant records. No source discovery.
        workflow: Registered WorkflowRelease whose sole node is this Module.
        variant_policy: Registered execution binding for that exact Workflow
            and node, selecting one exact ExecutionProfileRelease. A standalone
            Module Variant returned by ModuleReviewer.export is not this binding.
        authorize: Host callback receiving the prepared WorkflowModuleExecutionRequest.
            Return exactly (ExecutionAuthorizationContextEnvelope, decision_ref,
            delegation_ref, entitlement_ref). The last three values are existing
            ResolvedArtifactRef objects staged in artifact_host. Context meaning
            and permission decisions remain with the host; Runtime verifies
            their consistency. The callback is not called on committed replay.
        context_client: Host interface validating execution authorization context.
        operation_client: Host interface deciding permitted operations.
        enforcing_gateway_id: Explicit gateway identity used for authorization.
        environment_id: Explicit execution environment identity.
        adapters: Registry of installed executors matching the selected Profile.
            Actual compatibility is checked before starting the provider.
        artifact_host: ModuleArtifactHost with put_bytes, artifact and
            resolve_artifact_ref, as supplied by InMemoryCellArtifactStore.
            Holds staged input, prompt and authorization evidence for recording.
        record_store: Destination for authoritative execution and Attempt records.
        content_store: Explicit destination for their referenced input, prompt,
            output and diagnostic content. Persistence is determined by the
            supplied stores; an in-memory store does not prove PostgreSQL saving.
        claim_token_secret: Host-supplied bytes for the existing execution claim
            mechanism. Keep secret; do not place it in the task input or prompt.
        clock: Existing Runtime-compatible UTC clock; defaults to Runtime's clock.

    Returns:
        ModuleRunResult containing recorded Attempts, output references and
        any Runtime resolution. A returned failed Attempt is an execution
        failure, not a subject verdict. On success, the subject owner still
        validates and consumes the Module output. Inspect content through the
        configured store with the recorded execution ID and ref/hash.

    Raises:
        ValueError: Invalid target/input shape, inconsistent release or Variant
            binding, unsupported Profile, incomplete host interfaces, or reuse
            of an execution key with different input/releases. Correct the
            explicit request; do not silently pick another release or Profile.
        jsonschema.exceptions.ValidationError: Input violates its registered schema.
        PermissionError: Inconsistent host authorization evidence. Return to
            the authorization owner; do not manufacture replacement evidence.
        RuntimeError: A matching execution started but has no committed result
            available here. Use the existing Runtime recovery owner rather
            than resubmitting under a new key to repeat unknown effects.
        Exception: Registry, storage, authorization and downstream execution
            failures retain their native error contracts and owners. Inspect
            any committed facts before deciding whether a retry is safe.

    Effects:
        Stages bounded input/prompt content, records an execution start and
        invokes the selected Adapter through Runtime. Attempts, outputs, usage
        and diagnostics are recorded through the supplied stores. Provider
        effects require the admitted binding and authorization. No registration,
        activation, DDL, credential discovery or global configuration change.
        Committed replay does not repeat the provider call. This convenience
        entry's returned exceptions are not a separate uniform error-code enum.
    """
    validate_id("enforcing_gateway_id", enforcing_gateway_id)
    validate_id("environment_id", environment_id)
    if not callable(authorize):
        raise ValueError("host authorize callback is required")
    if content_store is None:
        raise ValueError("recorded execution requires an explicit content store")
    workflow_binding = WorkflowExecutionLedgerBinding(record_store, artifact_host, content_store)
    workflow_binding.validate()
    WorkflowModuleLedgerBinding(record_store, "0" * 64, claim_token_secret, content_store).validate()
    request, module, profile, task, prompt_ref = _prepare_registered_workflow_module(
        module_id=module_id, input_payload=input_payload, idempotency_key=idempotency_key,
        release_registry=release_registry, workflow=workflow, variant_policy=variant_policy,
        adapters=adapters, artifact_host=artifact_host,
    )
    execution_id = request.workflow_execution_id
    trace = record_store.load_trace(execution_id)
    starts = trace.records_of_type(WorkflowExecutionRecord)
    if starts:
        if len(starts) != 1:
            raise ValueError("execution has conflicting start records")
        prior = starts[0]
        if (
            prior.workflow_release_ref != workflow.release_ref
            or prior.workflow_release_sha256 != workflow.release_sha256
            or prior.execution_profile_selection_ref != variant_policy.release_ref
            or prior.execution_profile_selection_sha256 != variant_policy.release_sha256
            or prior.execution_input_package_sha256 != task.artifact_sha256
        ):
            raise ValueError("execution key was already bound to different inputs or releases")
        recorder = WorkflowModuleLedgerRecorder(WorkflowModuleLedgerBinding(
            record_store, prior.entitlement_snapshot_hash, claim_token_secret, content_store
        ))
        replay = recorder.replay_result(request=request, module=module)
        if replay is not None:
            return replay
        raise RuntimeError("execution already started; existing Runtime recovery is required")
    authorization = authorize(request)
    if type(authorization) is not tuple or len(authorization) != 4:
        raise ValueError("authorize must return a context and three existing evidence refs")
    envelope, decision, delegation, entitlement = authorization
    if type(envelope) is not ExecutionAuthorizationContextEnvelope:
        raise ValueError("host must provide an ExecutionAuthorizationContextEnvelope")
    evidence = (decision, delegation, entitlement)
    for ref in evidence:
        if type(ref) is not ResolvedArtifactRef:
            raise ValueError("host evidence must use ResolvedArtifactRef")
        ref.validate()
        if getattr(artifact_host, "resolve_artifact_ref")(ref.artifact_ref) != ref:
            raise ValueError("host evidence differs from its staged artifact")
        body = artifact_host.read_bytes(ref.artifact_ref, ref.artifact_sha256)
        if hashlib.sha256(body).hexdigest() != ref.artifact_sha256:
            raise ValueError("host evidence content hash mismatch")
    if envelope.authorization_decision_ref != decision.artifact_ref:
        raise PermissionError("context decision differs from supplied host evidence")
    if request.input_package_ref not in envelope.input_scope_refs:
        raise PermissionError("host context does not include the frozen input")
    recorded_at_utc = clock()
    controller = ExecutionAuthorizationController(
        client=context_client, ledger=InMemoryExecutionAuthorizationLedger(), module_release_client=release_registry,
    )
    admission = controller.bind_execution_context(
        envelope=envelope, expected_workflow_execution_id=execution_id,
        expected_workflow_release_id=workflow.workflow_id, expected_principal_id=envelope.principal_id,
        expected_actor_workload_id=envelope.actor_workload_id, expected_tenant_id=envelope.tenant_id,
        expected_cell_id=envelope.cell_id, execution_input_package_ref=request.input_package_ref,
        execution_input_package_sha256=request.input_package_sha256, observed_at_utc=recorded_at_utc,
    )
    authority = ModuleExecutionAuthority(controller, admission.binding, operation_client,
                                         enforcing_gateway_id, environment_id)
    artifacts = tuple({ref.artifact_ref: ref for ref in (task, prompt_ref, *evidence)}.values())
    execution = WorkflowExecutionRecord(
        workflow_execution_id=execution_id, workflow_id=workflow.workflow_id,
        workflow_contract_version=workflow.workflow_contract_version, tenant_id=envelope.tenant_id,
        cell_id=envelope.cell_id, principal_id=envelope.principal_id,
        execution_release_ref=workflow.execution_release_ref, graph_sha256=workflow.graph_sha256,
        runtime_execution_binding_ref=workflow.execution_release_ref,
        runtime_execution_binding_sha256=workflow.execution_release_sha256,
        authorization_decision_ref=decision.artifact_ref, authorization_decision_sha256=decision.artifact_sha256,
        execution_principal_delegation_ref=delegation.artifact_ref,
        execution_principal_delegation_sha256=delegation.artifact_sha256,
        entitlement_snapshot_ref=entitlement.artifact_ref, entitlement_snapshot_hash=entitlement.artifact_sha256,
        execution_input_package_refs=tuple(ref.artifact_ref for ref in artifacts),
        execution_input_package_sha256=request.input_package_sha256, recorded_at_utc=recorded_at_utc,
        workflow_release_ref=workflow.release_ref, workflow_release_sha256=workflow.release_sha256,
        execution_release_sha256=workflow.execution_release_sha256,
        execution_profile_selection_ref=variant_policy.release_ref,
        execution_profile_selection_sha256=variant_policy.release_sha256,
    )
    inputs = tuple(ExecutionInputRef(
        execution_input_id=_stable_id("execution_input", execution_id, ref.artifact_ref),
        workflow_execution_id=execution_id, input_type_id=ref.artifact_kind_id, schema_version=ref.schema_version,
        input_ref=ref.artifact_ref, input_sha256=ref.artifact_sha256,
        byte_size=len(artifact_host.read_bytes(ref.artifact_ref, ref.artifact_sha256)),
        media_type=getattr(artifact_host, "artifact")(ref.artifact_ref).media_type,
        recorded_at_utc=recorded_at_utc, logical_name=ref.logical_name,
    ) for ref in artifacts)
    receipt = WorkflowExecutionLedgerRecorder(workflow_binding).record_execution_start(execution=execution, inputs=inputs)
    if type(receipt) is not CommitReceipt or type(receipt.replayed) is not bool:
        raise ValueError("execution start must return the native commit receipt")
    recorder = WorkflowModuleLedgerRecorder(WorkflowModuleLedgerBinding(
        record_store, entitlement.artifact_sha256, claim_token_secret, content_store,
    ))
    if receipt.replayed:
        replay = recorder.replay_result(request=request, module=module)
        if replay is not None:
            return replay
        raise RuntimeError("execution already started; existing Runtime recovery is required")
    return run_workflow_module(
        request, release_registry=release_registry, adapters=adapters, artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(), workflow_ledger=recorder, authority=authority, clock=clock,
    )


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
    workflow_ledger: WorkflowModuleLedgerRecorder | None = None,
    authority: ModuleExecutionAuthority | None = None,
    clock: Callable[[], str] = _utc_now,
    self_test: ModuleSelfTestResources | None = None,
) -> ModuleRunResult:
    """Run one Module under an admitted Workflow Execution boundary.

    The request carries the durable dispatch, Workflow node, and Module Run
    IDs. The recorder commits start/authorization facts before provider entry,
    atomically commits the result, and replays an already committed invocation
    without calling the provider again.
    For non-persistent self-tests only, self_test supplies an exact live
    ModuleSelfTestResources and workflow_ledger is omitted. That path uses the
    supplied memory Ledger and has no durable replay or production authority.
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
    if self_test is not None:
        if type(self_test) is not ModuleSelfTestResources or workflow_ledger is not None or authority is not None:
            raise PermissionError("self-test resources cannot be mixed with external execution ports")
        self_test.check_scope(request=request, registry=release_registry, adapters=adapters,
                              artifact_host=artifact_host, ledger=ledger)
    elif workflow_ledger is None:
        raise ValueError("Workflow execution requires a durable Ledger or explicit self-test resources")
    else:
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
        self_test=self_test,
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
    self_test: ModuleSelfTestResources | None = None,
) -> ModuleRunResult:
    """Shared kernel after isolated or Workflow-bound request admission."""

    existing = ledger.existing_result(request)
    if existing is not None:
        return existing
    if not callable(getattr(ledger, "record_attempt_start", None)):
        raise TypeError("ModuleExecutionLedger requires record_attempt_start")

    module = release_registry.get_module(
        request.module_release_ref,
        request.module_release_sha256,
    )
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
    behavior_mode = behavior_policy.policy_document()["context_isolation"]
    if behavior_mode != "workflow_execution_isolated":
        raise ValueError("unsupported Module Behavior Policy")
    evaluation_mode = evaluation_policy.policy_document()["evaluation_mode"]
    max_attempts = retry_policy.policy_document()["max_attempts"]
    release_registry.assert_module_execution_allowed(module, request.purpose)
    model_operation_ids = tuple(
        operation_id
        for operation_id in module.declared_operation_ids
        if operation_id in MODEL_INVOCATION_OPERATION_IDS
    )
    candidate_purpose = request.purpose in {
        ModuleExecutionPurpose.TEST,
        ModuleExecutionPurpose.EVALUATION,
    }
    if evaluation_mode == "module_candidate":
        if not candidate_purpose or len(model_operation_ids) != 1:
            raise ValueError(
                "module_candidate Evaluation Policy requires a candidate "
                "purpose and one model operation"
            )
    elif evaluation_mode == "deterministic_candidate":
        if not candidate_purpose or model_operation_ids:
            raise ValueError(
                "deterministic_candidate Evaluation Policy requires a "
                "candidate purpose and no model operation"
            )
    elif evaluation_mode != "none":
        raise ValueError("unsupported Module Evaluation Policy")
    if module.declared_operation_ids and len(model_operation_ids) != 1:
        raise ValueError(
            "the model-backed slice admits exactly one declared model operation"
        )
    if module.declared_operation_ids:
        if authority is None and self_test is None:
            raise PermissionError(
                "a Module that declares a model operation requires a "
                "module execution authority"
            )
        if authority is not None:
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

    started_at_utc = clock()
    module_run_id = (
        request.module_run_id
        if type(request) is WorkflowModuleExecutionRequest
        else _stable_id("module_run", request.request_id, request.request_sha256)
    )
    prior_variants = {}
    if workflow_ledger is not None:
        trace = workflow_ledger.record_store.load_trace(request.workflow_execution_id)
        prior_module = next((row for row in trace.records_of_type(WorkflowModuleRunRecord)
                             if row.module_run_id == module_run_id), None)
        if prior_module is not None:
            started_at_utc = prior_module.recorded_at_utc
        prior_variants = {row.variant_id: row for row in trace.records_of_type(WorkflowModuleExecutionVariantRecord)
                          if row.module_run_id == module_run_id}
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
    resolved_adapters: list[AuthorizedAgentExecutionAdapter] = []
    variant_records: list[ModuleExecutionVariantRecord] = []
    prepared_attempts: list[tuple[str, int]] = []
    for variant_request in request.variants:
        profile = release_registry.get_execution_profile(
            variant_request.execution_profile_ref,
            variant_request.execution_profile_sha256,
        )
        if profile.transport_kind not in module.compatible_transport_kinds:
            raise ValueError("Execution Profile transport is incompatible with Module")
        if module.declared_operation_ids:
            _assert_admitted_test_evaluation_profile(
                module,
                profile,
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
                recorded_at_utc=(prior_variants[variant_id].recorded_at_utc
                                 if variant_id in prior_variants else started_at_utc),
            )
        )
        prepared_attempts.append((attempt_id, attempt_ordinal))

    concurrent_result = ledger.begin(
        request,
        module_run,
        tuple(variant_records),
        (),
    )
    if concurrent_result is not None:
        return concurrent_result

    if workflow_ledger is not None:
        if type(request) is not WorkflowModuleExecutionRequest:
            raise ValueError(
                "canonical Workflow ledger supplied for an isolated Module Run"
            )
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

    attempts: list[ModuleAttemptRecord] = []
    outputs: list[ModuleOutputBinding] = []
    for variant_request, profile, adapter, variant, (attempt_id, attempt_ordinal) in zip(
        request.variants,
        resolved_profiles,
        resolved_adapters,
        variant_records,
        prepared_attempts,
        strict=True,
    ):
        attempt, attempt_outputs = _execute_attempt(
            run_request=request,
            module=module,
            profile=profile,
            adapter=adapter,
            variant_request=variant_request,
            variant=variant,
            attempt_id=attempt_id,
            attempt_ordinal=attempt_ordinal,
            artifact_host=artifact_host,
            authority=authority,
            workflow_ledger=workflow_ledger,
            ledger=ledger,
            clock=clock,
            release_registry=release_registry,
            self_test=self_test,
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
    module: ModuleRelease,
    profile: ExecutionProfileRelease,
    adapter: AuthorizedAgentExecutionAdapter,
    variant_request: ModuleVariantRequest,
    variant: ModuleExecutionVariantRecord,
    attempt_id: str,
    attempt_ordinal: int,
    artifact_host: ModuleArtifactHost,
    authority: ModuleExecutionAuthority | None,
    workflow_ledger: WorkflowModuleLedgerRecorder | None,
    ledger: ModuleExecutionLedger,
    clock: Callable[[], str],
    release_registry: RuntimeReleaseRegistry,
    self_test: ModuleSelfTestResources | None = None,
) -> tuple[ModuleAttemptRecord, tuple[ModuleOutputBinding, ...]]:
    """Authorize, invoke, and atomically finalize one Attempt."""

    attempt_start = ModuleAttemptStartedRecord(
        module_run_id=variant.module_run_id, variant_id=variant.variant_id,
        attempt_id=attempt_id, attempt_ordinal=attempt_ordinal, recorded_at_utc=clock(),
    )
    if workflow_ledger is not None:
        if type(run_request) is not WorkflowModuleExecutionRequest:
            raise ValueError("Workflow ledger requires a Workflow Module request")
        workflow_ledger.begin_attempt(
            request=run_request,
            variant=variant,
            profile=profile,
            attempt_id=attempt_start.attempt_id,
            attempt_ordinal=attempt_start.attempt_ordinal,
            recorded_at_utc=attempt_start.recorded_at_utc,
            parent_attempt_id=run_request.parent_attempt_id,
        )
        starts = tuple(row for row in workflow_ledger.record_store.load_trace(
            run_request.workflow_execution_id).records_of_type(WorkflowAttemptStartedRecord)
            if row.attempt_id == attempt_start.attempt_id)
        if len(starts) != 1:
            raise RuntimeError("durable Attempt start is unavailable")
        attempt_start = replace(attempt_start, recorded_at_utc=starts[0].recorded_at_utc)
    ledger.record_attempt_start(attempt_start)

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
                    if operation_id in MODEL_INVOCATION_OPERATION_IDS
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
        self_test=self_test,
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
        self_test=self_test,
    )

    staged: tuple[tuple[OutputSubmission, bytes], ...] = ()
    result = None
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
        reported_usage, verified_trace, verified_tool_calls = (
            host.recoverable_result_evidence(result)
            if type(result) is AgentExecutionResult else (_empty_usage(), None, ())
        )
        authorization_failure = isinstance(exc, PermissionError)
        return _record_failed_attempt(
            variant=variant,
            attempt_start=attempt_start,
            failure_class="authorization" if authorization_failure else "unknown",
            usage=reported_usage,
            ended_at_utc=clock(),
            payload={
                "disposition": ("self_test_resources_unavailable" if authorization_failure and self_test is not None
                                else "dynamic_operation_authorization_refused" if authorization_failure
                                else "adapter_conformance_failure"),
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
            tool_calls=verified_tool_calls,
            provider_trace=verified_trace,
        )

    usage = ModuleUsageObservation(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cache_read_tokens=result.cache_read_tokens,
        cache_creation_tokens=result.cache_creation_tokens,
    )
    ended_at_utc = clock()

    if (result.terminal_status == "completed"
        and parse_utc_timestamp("period_end_at_utc", ended_at_utc)
        > parse_utc_timestamp("recorded_at_utc", attempt_start.recorded_at_utc)
          + timedelta(seconds=profile.timeout_seconds)):
        return _record_failed_attempt(
            variant=variant, attempt_start=attempt_start, failure_class="timeout",
            usage=usage, ended_at_utc=ended_at_utc,
            payload={"failure_code": "provider_completed_after_deadline"},
            artifact_host=artifact_host, ledger=ledger, workflow_ledger=workflow_ledger,
            workflow_request=run_request if type(run_request) is WorkflowModuleExecutionRequest else None,
            module=module, profile=profile, tool_calls=result.tool_observations,
            provider_trace=(result.cell_local_trace_ref, result.cell_local_trace_sha256),
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
            provider_trace=(result.cell_local_trace_ref, result.cell_local_trace_sha256),
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
                provider_trace=(result.cell_local_trace_ref, result.cell_local_trace_sha256),
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
            provider_trace_ref=result.cell_local_trace_ref,
            provider_trace_sha256=result.cell_local_trace_sha256,
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
    if self_test is not None:
        try:
            return self_test.guarded(lambda: finalize(None))
        except PermissionError as exc:
            return _record_failed_attempt(
                variant=variant, attempt_start=attempt_start, failure_class="authorization",
                usage=usage, ended_at_utc=ended_at_utc,
                payload={"disposition": "self_test_resources_unavailable", "reason": str(exc)},
                artifact_host=artifact_host, ledger=ledger, workflow_ledger=None,
                workflow_request=run_request, module=module, profile=profile,
                tool_calls=result.tool_observations,
                provider_trace=(result.cell_local_trace_ref, result.cell_local_trace_sha256),
            )
    return finalize(None)


def _authorize_model_attempt(
    *,
    authority: ModuleExecutionAuthority,
    module: ModuleRelease,
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
        if operation_id in MODEL_INVOCATION_OPERATION_IDS
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
    module: ModuleRelease,
    profile: ExecutionProfileRelease,
    variant_request: ModuleVariantRequest,
    variant: ModuleExecutionVariantRecord,
    attempt_start: ModuleAttemptStartedRecord,
    evidence: _AttemptAuthorizationEvidence | None,
    authority: ModuleExecutionAuthority | None,
    self_test: ModuleSelfTestResources | None = None,
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
        self_test_binding_ref=self_test.binding_ref if self_test is not None else None,
        self_test_binding_sha256=self_test.binding_sha256 if self_test is not None else None,
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
        profile.semantic_input_delivery_mode in {"gateway_read", "hybrid"}
        and profile.tool_policy
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
    payload: Mapping[str, Any],
    artifact_host: ModuleArtifactHost,
    ledger: ModuleExecutionLedger,
    workflow_ledger: WorkflowModuleLedgerRecorder | None = None,
    workflow_request: WorkflowModuleExecutionRequest | None = None,
    module: ModuleRelease | None = None,
    profile: ExecutionProfileRelease | None = None,
    tool_calls: tuple[ModuleToolCallObservation, ...] = (),
    provider_trace: tuple[str, str] | None = None,
) -> tuple[ModuleAttemptRecord, tuple[ModuleOutputBinding, ...]]:
    """Commit one kernel-owned failed Attempt with its bounded diagnostic."""

    attempt = _failed_attempt(
        variant=variant,
        attempt_start=attempt_start,
        failure_class=failure_class,
        usage=usage,
        ended_at_utc=ended_at_utc,
        detail=_commit_kernel_failure_detail(
            artifact_host,
            variant=variant,
            attempt_start=attempt_start,
            failure_class=failure_class,
            payload=payload,
        ),
        tool_calls=tool_calls,
        provider_trace=provider_trace,
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
    module: ModuleRelease,
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
    status: str = "failed",
    detail: tuple[str, str] | None = None,
    tool_calls: tuple[ModuleToolCallObservation, ...] = (),
    provider_trace: tuple[str, str] | None = None,
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
        period_end_at_utc=ended_at_utc,
        recorded_at_utc=ended_at_utc,
        tool_calls=tool_calls,
        prompt_envelope_ref=variant.prompt_envelope_ref,
        prompt_envelope_sha256=variant.prompt_envelope_sha256,
        failure_detail_ref=detail[0] if detail is not None else None,
        failure_detail_sha256=detail[1] if detail is not None else None,
        provider_trace_ref=provider_trace[0] if provider_trace is not None else None,
        provider_trace_sha256=provider_trace[1] if provider_trace is not None else None,
    )


def _assert_admitted_test_evaluation_profile(
    module: ModuleRelease,
    profile: ExecutionProfileRelease,
) -> None:
    """Admit the exact model-backed Test/Evaluation capability slices.

    Registration validates the dimensions independently. The execution kernel
    intentionally admits only reviewed conjunctions, so a newly representable
    hybrid cannot become executable by accident.
    """

    if module.reviewer_defaults is not None:
        module.reviewer_defaults.assert_profile(profile)
    _, non_model_operations = partition_module_operation_ids(
        module.declared_operation_ids
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
        and bool(profile.tool_policy)
        and not profile.gateway_access_reasons
        and not non_model_operations
        and profile.executor_adapter_id
        == "claude_cli_native_tools_executor"
        and profile.executor_adapter_revision == "v2"
        and profile.transport_kind == "claude_cli"
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
        and profile.executor_adapter_revision == "v3"
        and profile.transport_kind == "claude_agent_sdk"
        and profile.provider_id == "anthropic"
    ):
        return
    raise NotImplementedError(
        "model-backed Test/Evaluation profile is outside the admitted "
        "tool-free, draft-workspace, and Gateway-read slices"
    )


def _resolve_shadow_outputs(
    module: ModuleRelease,
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
