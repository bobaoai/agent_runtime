from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time

import pytest

from agent_runtime.contracts.execution_authorization_definition import (
    ExecutionAuthorizationContextEnvelope,
    ExecutionAuthorizationContextState,
    ExecutionAuthorizationFence,
    ExecutionAuthorizationFenceState,
    GatewayDecisionEffect,
    OperationAuthorizationQuery,
    ProductAuthorizationContextStatus,
    ProductOperationDecision,
)
from agent_runtime.contracts.execution_module_definition import (
    ModuleExecutionRequest,
    ModuleInputBinding,
    ModuleVariantRequest,
    WorkflowModuleExecutionRequest,
)
from agent_runtime.contracts.invocation_adapter_definition import (
    AdapterContextResult,
    AgentExecutionAdapterDescriptor,
    AgentExecutionFailure,
    AgentExecutionResult,
    AuthorizedAgentExecutionRequest,
    OutputSubmission,
    ProviderOperationIntent,
)
from agent_runtime.contracts.ledger_lineage_definition import (
    ModuleOutputResolutionRecord,
    ModuleToolCallObservation,
)
from agent_runtime.contracts.ledger_record_definition import (
    ExecutionInputRef,
    LegacyModuleCapabilityGrant,
    ModelCallRecord,
    RuntimeRecordBatch,
    ToolCallRecord,
    UsageEvent,
    WorkflowAttemptRecord,
    WorkflowAttemptStartedRecord,
    WorkflowExecutionRecord,
    WorkflowModuleExecutionVariantRecord,
    WorkflowModuleRunRecord,
)
from agent_runtime.contracts.registry_release_definition import (
    ModuleExecutionPurpose,
    OutputResolutionPolicy,
    ReleaseAdmissionRecord,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
)
from agent_runtime.execution.execution_authorization_coordination import (
    ExecutionAuthorizationController,
    InMemoryExecutionAuthorizationLedger,
)
from agent_runtime.execution.execution_content_staging import InMemoryCellArtifactStore
from agent_runtime.execution.execution_module_invocation import (
    AgentExecutionAdapterRegistry,
    AttemptToolReconciliationRequiredError,
    ModuleExecutionAuthority,
    isolated_execution_scope_id,
    run_module,
    run_workflow_module,
)
from agent_runtime.invocation.invocation_codex_module_invocation import (
    CodexCliAgentWorkspaceModuleExecutor,
    CodexCliInvocationResult,
    CodexCliModuleExecutor,
)
from agent_runtime.invocation import (
    invocation_codex_module_invocation as codex_module,
)
from agent_runtime.invocation.invocation_workspace_preparation import (
    AttemptWorkspaceConflictError,
)
from agent_runtime.invocation.invocation_tool_definition import (
    ProviderToolDefinition,
)
from agent_runtime.invocation.invocation_prompt_assembly import (
    NATIVE_STRUCTURED_OUTPUT,
    OUTPUT_SCHEMA_MARKER,
    build_inline_provider_prompt,
    codex_native_output_schema,
)
from agent_runtime.invocation.invocation_schema_projection import (
    task_plane_output_schema,
)
from agent_runtime.ledger.ledger_lineage_recording import (
    InMemoryModuleExecutionLedger,
)
from agent_runtime.ledger.ledger_record_persistence import (
    InMemoryRuntimeExecutionRecordStore,
)
from agent_runtime.ledger.ledger_workflow_module_recording import (
    WorkflowModuleLedgerBinding,
    WorkflowModuleLedgerRecorder,
)
from agent_runtime.registry.registry_release_compilation import (
    AgentModuleReleaseCandidate,
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    ExecutionProfileReleaseSpec,
    NonAgentModuleReleaseCandidate,
    RetryPolicyReleaseCandidate,
    candidate_admission_intent,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_execution_profile_release,
    compile_non_agent_module_release,
    compile_retry_policy_release,
    runtime_owned_policy_schema_assets,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


_OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "schema:native_output@v1",
    "type": "object",
    "properties": {
        "value": {"type": "string"},
        "note": {"type": "string"},
    },
    "required": ["value"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class _CompiledNativeModule:
    schema_assets: tuple
    prompt_components: tuple
    prompt_bundle: object
    behavior_policy: object
    evaluation_policy: object
    retry_policy: object
    execution_profile: object
    module: object


def _compile_native_module(
    tmp_path: Path,
    *,
    declared_operation_ids: tuple[str, ...] = ("invoke_model",),
    output_resolution_policy: OutputResolutionPolicy = (
        OutputResolutionPolicy.EVALUATED_SINGLE
    ),
    execution_profile_id: str = "native_profile",
    executor_adapter_id: str = "codex_cli_agent_executor",
    executor_adapter_revision: str = "v2",
    transport_kind: str = "codex_cli",
    provider_id: str = "openai",
    model_id: str = "native_model",
    reasoning_profile: str = "none",
    timeout_seconds: int = 60,
    execution_mode: str = "tool_free",
    semantic_input_delivery_mode: str = "inline",
    attempt_workspace_policy: str = "none",
    gateway_access_reasons: tuple[str, ...] = (),
    tool_policy: tuple[str, ...] = (),
    network_policy: str = "denied",
):
    del tmp_path
    behavior_policy = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    evaluation_policy = compile_evaluation_policy_release(
        EvaluationPolicyReleaseCandidate(
            policy_id="native",
            policy_version="v1",
            evaluation_mode="module_candidate",
        )
    )
    retry_policy = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="bounded",
            policy_version="v1",
            max_attempts=2,
        )
    )
    input_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "schema:native_input@v1",
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
        "additionalProperties": False,
    }
    compiled_module = compile_agent_module_release(
        AgentModuleReleaseCandidate(
            module_id="native_module",
            module_version="v1",
            owner_contract_ref="contract:native_module@v1",
            owner_contract_content=(
                "# Owner\n\nRegistered Module: `native_module`.\n"
            ),
            input_schema_ref="schema:native_input@v1",
            input_schema_document=json.dumps(input_schema),
            output_schema_ref="schema:native_output@v1",
            output_schema_document=json.dumps(_OUTPUT_SCHEMA),
            instruction_source_ref="authoring-source:native_instruction@v1",
            instruction_text="Produce the native result.\n",
            declared_operation_ids=declared_operation_ids,
            compatible_transport_kinds=(transport_kind,),
            behavior_policy_ref=behavior_policy.release_ref,
            behavior_policy_sha256=behavior_policy.release_sha256,
            evaluation_policy_ref=evaluation_policy.release_ref,
            evaluation_policy_sha256=evaluation_policy.release_sha256,
            retry_policy_ref=retry_policy.release_ref,
            retry_policy_sha256=retry_policy.release_sha256,
            output_resolution_policy=output_resolution_policy,
        )
    )
    execution_profile = compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
            execution_profile_id=execution_profile_id,
            executor_adapter_id=executor_adapter_id,
            executor_adapter_revision=executor_adapter_revision,
            transport_kind=transport_kind,
            provider_id=provider_id,
            model_id=model_id,
            reasoning_profile=reasoning_profile,
            execution_mode=execution_mode,
            semantic_input_delivery_mode=semantic_input_delivery_mode,
            attempt_workspace_policy=attempt_workspace_policy,
            gateway_access_reasons=gateway_access_reasons,
            output_constraint_mode=NATIVE_STRUCTURED_OUTPUT,
            tool_policy=tool_policy,
            network_policy=network_policy,
            timeout_seconds=timeout_seconds,
            release_version="v1",
        )
    )
    return _CompiledNativeModule(
        schema_assets=(
            *runtime_owned_policy_schema_assets(),
            *compiled_module.schema_assets,
        ),
        prompt_components=compiled_module.prompt_components,
        prompt_bundle=compiled_module.prompt_bundle,
        behavior_policy=behavior_policy,
        evaluation_policy=evaluation_policy,
        retry_policy=retry_policy,
        execution_profile=execution_profile,
        module=compiled_module.module,
    )


_TEST_TIME = "2026-08-09T12:00:00Z"
_RUN_PROVIDER_INTEGRATION = os.environ.get("RUN_PROVIDER_INTEGRATION") == "1"


def _register_compiled_for_evaluation(compiled) -> RuntimeReleaseRegistry:
    admissions = tuple(
        candidate_admission_intent(record)
        for record in (
            *compiled.prompt_components,
            compiled.prompt_bundle,
            compiled.behavior_policy,
            compiled.evaluation_policy,
            compiled.retry_policy,
            compiled.execution_profile,
            compiled.module,
        )
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=compiled.schema_assets,
            prompt_components=compiled.prompt_components,
            prompt_bundles=(compiled.prompt_bundle,),
            behavior_policies=(compiled.behavior_policy,),
            evaluation_policies=(compiled.evaluation_policy,),
            retry_policies=(compiled.retry_policy,),
            execution_profiles=(compiled.execution_profile,),
            modules=(compiled.module,),
            admission_intents=admissions,
        )
    )
    return registry


def _evaluation_prompt(
    artifact_host,
    compiled,
    *,
    suffix: str,
    execution_specific_instructions: str = "",
):
    envelope = build_inline_provider_prompt(
        compiled_static_body=compiled.prompt_bundle.compiled_static_body,
        execution_specific_instructions=execution_specific_instructions,
        inputs=(),
        output_constraint_mode=NATIVE_STRUCTURED_OUTPUT,
    )
    return artifact_host.put_bytes(
        artifact_kind_id="prompt_envelope",
        schema_version="prompt_envelope_v1",
        schema_ref="schema:prompt_envelope@v1",
        schema_sha256="1" * 64,
        media_type="text/plain",
        content=envelope.encode("utf-8"),
        idempotency_key=f"prompt_envelope_{suffix}",
    )


def _evaluation_request(
    compiled,
    prompt_ref,
    *,
    suffix: str,
    purpose: ModuleExecutionPurpose = ModuleExecutionPurpose.EVALUATION,
) -> ModuleExecutionRequest:
    return ModuleExecutionRequest.build(
        request_id=f"request_native_{suffix}",
        purpose=purpose,
        module_release_ref=compiled.module.release_ref,
        module_release_sha256=compiled.module.release_sha256,
        isolated_scope_ref=f"scope-ref:native-{suffix}",
        isolated_scope_sha256="2" * 64,
        input_package_ref=f"artifact-ref:input-package-{suffix}",
        input_package_sha256="3" * 64,
        inputs=(),
        variants=(
            ModuleVariantRequest(
                arm_key=f"native_{suffix}",
                replicate_index=0,
                execution_profile_ref=compiled.execution_profile.release_ref,
                execution_profile_sha256=(
                    compiled.execution_profile.release_sha256
                ),
                prompt_envelope_ref=prompt_ref.artifact_ref,
                prompt_envelope_sha256=prompt_ref.artifact_sha256,
            ),
        ),
        idempotency_key=f"idempotency_native_{suffix}",
    )


class _ProductAuthorityDouble:
    """Mutable in-process Product Authorization double for kernel tests."""

    def __init__(self, envelope: ExecutionAuthorizationContextEnvelope) -> None:
        self.envelope = envelope
        self.context_state = ExecutionAuthorizationContextState.EFFECTIVE
        self.operation_effect = GatewayDecisionEffect.ALLOW
        self.operation_effects: dict[str, GatewayDecisionEffect] = {}
        self.operation_queries: list[OperationAuthorizationQuery] = []

    def validate_execution_context(
        self,
        context_id: str,
        tenant_id: str,
        observed_at_utc: str,
    ) -> ProductAuthorizationContextStatus:
        assert context_id == self.envelope.context_id
        assert tenant_id == self.envelope.tenant_id
        return ProductAuthorizationContextStatus.build(
            status_ref="product-status:isolated-test",
            context_id=self.envelope.context_id,
            context_ref=self.envelope.context_ref,
            context_sha256=self.envelope.context_sha256,
            state=self.context_state,
            reason_code=(
                "context_effective"
                if self.context_state
                is ExecutionAuthorizationContextState.EFFECTIVE
                else "context_revoked"
            ),
            observed_at_utc=observed_at_utc,
        )

    def authorize_operation(
        self,
        query: OperationAuthorizationQuery,
    ) -> ProductOperationDecision:
        self.operation_queries.append(query)
        effect = self.operation_effects.get(query.operation_id, self.operation_effect)
        return ProductOperationDecision(
            query_id=query.query_id,
            query_sha256=query.query_sha256,
            decision_ref=f"product-decision:{query.query_id}",
            decision_sha256=hashlib.sha256(
                query.query_sha256.encode("utf-8")
            ).hexdigest(),
            effect=effect,
            reason_code=(
                "operation_allowed"
                if effect is GatewayDecisionEffect.ALLOW
                else "operation_denied"
            ),
            observed_at_utc=query.observed_at_utc,
        )


def _evaluation_authority(
    release_registry: RuntimeReleaseRegistry,
    request: ModuleExecutionRequest,
    *,
    observed_at_utc: str = _TEST_TIME,
    scope_id: str | None = None,
    input_package_ref: str | None = None,
    input_package_sha256: str | None = None,
) -> tuple[ModuleExecutionAuthority, _ProductAuthorityDouble]:
    scope = scope_id or isolated_execution_scope_id(
        request.isolated_scope_ref,
        request.isolated_scope_sha256,
    )
    envelope = ExecutionAuthorizationContextEnvelope.build(
        context_id="context_isolated_test",
        context_ref="product-context:isolated-test",
        workflow_execution_id=scope,
        workflow_release_id="module_isolated_evaluation",
        principal_id="principal_test",
        actor_workload_id="workload_agent_runtime_tests",
        tenant_id="tenant_test",
        cell_id="cell_test",
        authorization_decision_ref="product-decision:isolated-context",
        catalog_release_ref="catalog:isolated-test@v1",
        input_scope_refs=(request.input_package_ref,),
        effective_at_utc="2026-01-01T00:00:00Z",
        expiry_at_utc="2027-01-01T00:00:00Z",
    )
    product = _ProductAuthorityDouble(envelope)
    controller = ExecutionAuthorizationController(
        client=product,
        ledger=InMemoryExecutionAuthorizationLedger(),
        module_release_client=release_registry,
    )
    admission = controller.bind_execution_context(
        envelope=envelope,
        expected_workflow_execution_id=scope,
        expected_workflow_release_id="module_isolated_evaluation",
        expected_principal_id="principal_test",
        expected_actor_workload_id="workload_agent_runtime_tests",
        expected_tenant_id="tenant_test",
        expected_cell_id="cell_test",
        execution_input_package_ref=(
            input_package_ref or request.input_package_ref
        ),
        execution_input_package_sha256=(
            input_package_sha256 or request.input_package_sha256
        ),
        observed_at_utc=observed_at_utc,
    )
    authority = ModuleExecutionAuthority(
        controller=controller,
        binding=admission.binding,
        authorization_client=product,
        enforcing_gateway_id="agent_runtime_module_kernel",
        environment_id="development",
    )
    return authority, product


class _StubInlineAdapter:
    """Canonical in-process adapter double with observable invocation count."""

    def __init__(
        self,
        *,
        release_registry: RuntimeReleaseRegistry,
        artifact_host: InMemoryCellArtifactStore,
        adapter_id: str = "stub_inline_executor",
        adapter_revision: str = "v1",
        provider_id: str = "provider_stub",
        transport_kind: str = "in_process_test",
        transport_family: str = "in_process",
        supported_execution_modes: tuple[str, ...] = ("tool_free",),
        supported_input_delivery_modes: tuple[str, ...] = ("inline",),
        supported_network_policies: tuple[str, ...] = ("denied",),
        supports_dynamic_operation_authorization: bool = False,
        payload: bytes = b'{"value":"stub"}',
        on_execute=None,
        terminal_status: str = "completed",
        result_type_override=None,
    ) -> None:
        self._release_registry = release_registry
        self._artifact_host = artifact_host
        self._adapter_id = adapter_id
        self._adapter_revision = adapter_revision
        self._provider_id = provider_id
        self._transport_kind = transport_kind
        self._transport_family = transport_family
        self._supported_execution_modes = supported_execution_modes
        self._supported_input_delivery_modes = supported_input_delivery_modes
        self._supported_network_policies = supported_network_policies
        self._supports_dynamic_operation_authorization = (
            supports_dynamic_operation_authorization
        )
        self._payload = payload
        self._on_execute = on_execute
        self._terminal_status = terminal_status
        self._result_type_override = result_type_override
        self.calls = 0

    @property
    def descriptor(self) -> AgentExecutionAdapterDescriptor:
        return AgentExecutionAdapterDescriptor(
            adapter_contract_version="v1",
            adapter_id=self._adapter_id,
            adapter_revision=self._adapter_revision,
            provider_id=self._provider_id,
            transport_family=self._transport_family,
            transport_kind=self._transport_kind,
            runtime_package_id="agent_runtime_core",
            runtime_package_version="0.0.0",
            supported_context_modes=("stateless",),
            supported_output_constraint_modes=(
                "prompt_only_json",
                "native_structured_output",
            ),
            supported_read_isolation_modes=("entitled_refs",),
            supported_execution_modes=self._supported_execution_modes,
            supported_input_delivery_modes=self._supported_input_delivery_modes,
            supported_network_policies=self._supported_network_policies,
            supports_dynamic_operation_authorization=(
                self._supports_dynamic_operation_authorization
            ),
            admission_state="in_process_test_double",
        )

    skip_staging = False

    def execute(self, request: AuthorizedAgentExecutionRequest, host):
        self.calls += 1
        profile = self._release_registry.get_execution_profile(
            request.execution_profile_ref,
            request.execution_profile_sha256,
        )
        tool_observations: tuple[ModuleToolCallObservation, ...] = ()
        if self._on_execute is not None:
            tool_observations = self._on_execute(request, host) or ()
        if self._result_type_override is not None:
            return self._result_type_override
        trace_ref, trace_sha256 = self._artifact_host.commit_attempt_trace(
            module_run_id=request.module_run_id,
            variant_id=request.variant_id,
            attempt_id=request.attempt_id,
            content=b'{"transport":"in_process_test"}',
            media_type="application/json",
        )
        submission = OutputSubmission(
            output_slot_id="result",
            local_handle="output/result.json",
        )
        outputs: tuple[OutputSubmission, ...] = ()
        failure = None
        if self._terminal_status == "completed":
            if not self.skip_staging:
                host.stage_output_bytes(submission, self._payload)
            outputs = (submission,)
        else:
            host.stage_output_bytes(submission, self._payload)
            failure = AgentExecutionFailure(
                failure_class="provider",
                retry_disposition_id="retry_allowed",
                failure_scope_id="attempt_only",
                retry_after_seconds=None,
                detail_ref=None,
                detail_sha256=None,
            )
        return AgentExecutionResult(
            terminal_status=self._terminal_status,
            provider_id=profile.provider_id,
            model_id=profile.model_id,
            runtime_version="0.0.0",
            outputs=outputs,
            model_operation_ref_ids=(),
            tool_operation_ref_ids=tuple(
                observation.tool_call_id for observation in tool_observations
            ),
            input_tokens=3,
            output_tokens=2,
            cache_read_tokens=None,
            cache_creation_tokens=None,
            estimated_cost_usd=None,
            provider_charge_usd=None,
            context=AdapterContextResult(
                disposition_id="stateless_closed",
                context_ref=None,
                compatibility_sha256=request.execution_profile_sha256,
            ),
            failure=failure,
            cell_local_trace_ref=trace_ref,
            cell_local_trace_sha256=trace_sha256,
            tool_observations=tool_observations,
        )


def _stub_compiled(tmp_path: Path, **overrides):
    parameters = {
        "output_resolution_policy": OutputResolutionPolicy.DIRECT_SINGLE,
        "execution_profile_id": "stub_profile",
        "executor_adapter_id": "stub_inline_executor",
        "executor_adapter_revision": "v1",
        "transport_kind": "in_process_test",
        "provider_id": "provider_stub",
        "model_id": "model_stub",
    }
    parameters.update(overrides)
    return _compile_native_module(tmp_path, **parameters)


def _registered_stub(
    compiled,
    artifact_host: InMemoryCellArtifactStore,
    **adapter_overrides,
) -> tuple[RuntimeReleaseRegistry, AgentExecutionAdapterRegistry, _StubInlineAdapter]:
    registry = _register_compiled_for_evaluation(compiled)
    adapter = _StubInlineAdapter(
        release_registry=registry,
        artifact_host=artifact_host,
        **adapter_overrides,
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    return registry, adapters, adapter


def _assert_completed_provider_run(run, artifact_host) -> dict[str, object]:
    assert run.module_run.purpose is ModuleExecutionPurpose.EVALUATION
    assert len(run.attempts) == 1
    attempt = run.attempts[0]
    if attempt.status != "completed":
        detail = None
        if (
            attempt.failure_detail_ref is not None
            and attempt.failure_detail_sha256 is not None
        ):
            detail = json.loads(
                artifact_host.read_bytes(
                    attempt.failure_detail_ref,
                    attempt.failure_detail_sha256,
                )
            )
        pytest.fail(
            f"Provider Attempt failed: {attempt.failure_class}; detail={detail!r}"
        )
    assert len(run.outputs) == 1
    assert run.resolution is not None
    assert run.resolution.resolution_status == "resolved"
    return json.loads(
        artifact_host.read_bytes(
            run.outputs[0].output_ref,
            run.outputs[0].output_sha256,
        )
    )


def test_workflow_module_replays_after_resolution_commit_crash(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    artifact_host = InMemoryCellArtifactStore()
    registry, adapters, adapter = _registered_stub(compiled, artifact_host)
    prompt_ref = _evaluation_prompt(
        artifact_host,
        compiled,
        suffix="workflow_ledger",
    )
    input_content = b'{"value":"workflow_input"}'
    input_ref = artifact_host.put_bytes(
        artifact_kind_id="native_input",
        schema_version="v1",
        schema_ref=compiled.module.input_schema_ref,
        schema_sha256=compiled.module.input_schema_sha256,
        media_type="application/json",
        content=input_content,
        idempotency_key="workflow_ledger_input",
        logical_name="task_input",
    )
    input_binding = ModuleInputBinding(
        logical_name="task_input",
        input_ref=input_ref.artifact_ref,
        input_sha256=input_ref.artifact_sha256,
        schema_ref=compiled.module.input_schema_ref,
        schema_sha256=compiled.module.input_schema_sha256,
        media_type="application/json",
    )
    workflow_request = WorkflowModuleExecutionRequest.build(
        request_id="request_workflow_ledger",
        purpose=ModuleExecutionPurpose.EVALUATION,
        workflow_execution_id="execution_workflow_ledger",
        dispatch_id="dispatch_workflow_ledger",
        workflow_node_id="state_native_module",
        module_run_id="module_run_workflow_ledger",
        module_release_ref=compiled.module.release_ref,
        module_release_sha256=compiled.module.release_sha256,
        input_package_ref=input_ref.artifact_ref,
        input_package_sha256=input_ref.artifact_sha256,
        inputs=(input_binding,),
        variants=(
            ModuleVariantRequest(
                arm_key="default",
                replicate_index=0,
                execution_profile_ref=compiled.execution_profile.release_ref,
                execution_profile_sha256=(
                    compiled.execution_profile.release_sha256
                ),
                prompt_envelope_ref=prompt_ref.artifact_ref,
                prompt_envelope_sha256=prompt_ref.artifact_sha256,
            ),
        ),
        idempotency_key="idempotency_workflow_ledger",
    )
    execution = WorkflowExecutionRecord(
        workflow_execution_id=workflow_request.workflow_execution_id,
        workflow_id="workflow_native_module",
        workflow_contract_version="v1",
        tenant_id="tenant_test",
        cell_id="cell_test",
        principal_id="principal_test",
        execution_release_ref="execution-release:native-workflow@v1",
        graph_sha256="a" * 64,
        runtime_execution_binding_ref="runtime-binding:native-workflow@v1",
        runtime_execution_binding_sha256="b" * 64,
        authorization_decision_ref="authorization-decision:native-workflow@v1",
        authorization_decision_sha256="c" * 64,
        execution_principal_delegation_ref="delegation:native-workflow@v1",
        execution_principal_delegation_sha256="d" * 64,
        entitlement_snapshot_ref="entitlement:native-workflow@v1",
        entitlement_snapshot_hash="e" * 64,
        execution_input_package_refs=(input_ref.artifact_ref,),
        execution_input_package_sha256=input_ref.artifact_sha256,
        recorded_at_utc=_TEST_TIME,
    )
    execution_input = ExecutionInputRef(
        execution_input_id="execution_input_workflow_ledger",
        workflow_execution_id=workflow_request.workflow_execution_id,
        input_type_id="native_input",
        schema_version="v1",
        input_ref=input_ref.artifact_ref,
        input_sha256=input_ref.artifact_sha256,
        byte_size=len(input_content),
        media_type="application/json",
        recorded_at_utc=_TEST_TIME,
        logical_name="task_input",
    )

    class _AttemptContentStore:
        def __init__(self) -> None:
            self.staged = {}

        def stage_content(self, content):
            content.validate()
            self.staged[content.content_ref] = content
            return content

        def commit_content(self, content):
            raise AssertionError("Attempt outputs must stage before finalization")

        def contains(self, output) -> bool:
            content = self.staged.get(output.output_ref)
            return (
                content is not None
                and content.content_sha256 == output.output_sha256
            )

    content_store = _AttemptContentStore()
    record_store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=content_store.contains
    )
    record_store.commit(
        RuntimeRecordBatch(
            workflow_execution_id=workflow_request.workflow_execution_id,
            transaction_id="transaction_workflow_ledger_bootstrap",
            records=(execution, execution_input),
        )
    )
    authority, _ = _evaluation_authority(
        registry,
        workflow_request,
        scope_id=workflow_request.workflow_execution_id,
        input_package_ref=workflow_request.input_package_ref,
        input_package_sha256=workflow_request.input_package_sha256,
    )
    class _CrashAfterInvocationRecorder(WorkflowModuleLedgerRecorder):
        def record_output_resolution(self, *, request, resolution) -> None:
            raise RuntimeError("simulated crash after invocation commit")

    recorder = _CrashAfterInvocationRecorder(
        WorkflowModuleLedgerBinding(
            record_store=record_store,
            entitlement_snapshot_hash=execution.entitlement_snapshot_hash,
            claim_token_secret=b"workflow-ledger-test-secret-32-bytes",
            content_store=content_store,
        )
    )
    provider_entry_observation: dict[str, int] = {}

    def observe_provider_entry(_request, _host):
        current = record_store.load_trace(
            workflow_request.workflow_execution_id
        )
        provider_entry_observation["attempt_starts"] = len(
            current.records_of_type(WorkflowAttemptStartedRecord)
        )
        provider_entry_observation["grants"] = len(
            current.records_of_type(LegacyModuleCapabilityGrant)
        )
        return ()

    adapter._on_execute = observe_provider_entry

    with pytest.raises(
        RuntimeError, match="simulated crash after invocation commit"
    ):
        run_workflow_module(
            workflow_request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            authority=authority,
            clock=lambda: _TEST_TIME,
        )

    assert adapter.calls == 1
    assert provider_entry_observation == {"attempt_starts": 1, "grants": 1}
    trace = record_store.load_trace(workflow_request.workflow_execution_id)
    assert len(trace.records_of_type(WorkflowModuleRunRecord)) == 1
    assert len(
        trace.records_of_type(WorkflowModuleExecutionVariantRecord)
    ) == 1
    assert len(trace.records_of_type(WorkflowAttemptRecord)) == 1
    model_calls = trace.records_of_type(ModelCallRecord)
    assert len(model_calls) == 1
    assert model_calls[0].authorization_intent_ref is not None
    assert model_calls[0].authorization_decision_ref is not None
    assert model_calls[0].authorization_observation_ref is not None
    usage = trace.records_of_type(UsageEvent)
    assert len(usage) == 1
    assert usage[0].input_tokens == 3
    assert usage[0].output_tokens == 2
    assert len(content_store.staged) == 1

    replay = run_workflow_module(
        workflow_request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=WorkflowModuleLedgerRecorder(
            WorkflowModuleLedgerBinding(
                record_store=record_store,
                entitlement_snapshot_hash=execution.entitlement_snapshot_hash,
                claim_token_secret=b"workflow-ledger-test-secret-32-bytes",
                content_store=content_store,
            )
        ),
        authority=authority,
        clock=lambda: "2026-08-09T13:00:00Z",
    )
    assert adapter.calls == 1
    assert _assert_completed_provider_run(replay, artifact_host) == {
        "value": "stub"
    }
    assert len(
        record_store.load_trace(
            workflow_request.workflow_execution_id
        ).records_of_type(ModuleOutputResolutionRecord)
    ) == 1


def _failure_detail(run, artifact_host) -> dict[str, object]:
    attempt = run.attempts[0]
    assert attempt.failure_detail_ref is not None
    return json.loads(
        artifact_host.read_bytes(
            attempt.failure_detail_ref,
            attempt.failure_detail_sha256,
        )
    )


@pytest.mark.parametrize(
    (
        "adapter_terminal_status",
        "adapter_raises",
        "observed_at",
        "expected_status",
    ),
    (
        ("completed", False, "2026-08-09T12:02:00Z", "failed"),
        ("failed", False, "2026-08-09T12:02:00Z", "failed"),
        ("completed", True, "2026-08-09T12:02:00Z", "failed"),
        ("completed", False, "2026-08-09T12:01:00Z", "failed"),
        ("completed", False, "2026-08-09T12:00:59Z", "completed"),
    ),
)
def test_late_adapter_result_commits_deadline_bounded_timeout_attempt(
    tmp_path: Path,
    adapter_terminal_status: str,
    adapter_raises: bool,
    observed_at: str,
    expected_status: str,
) -> None:
    start_at = "2026-08-09T12:00:00Z"
    deadline_at = "2026-08-09T12:01:00Z"
    compiled = _stub_compiled(tmp_path, timeout_seconds=60)
    artifact_host = InMemoryCellArtifactStore()

    def raise_after_provider_entry(_request, _host):
        raise RuntimeError("adapter failed after the deadline")

    registry, adapters, adapter = _registered_stub(
        compiled,
        artifact_host,
        terminal_status=adapter_terminal_status,
        on_execute=(raise_after_provider_entry if adapter_raises else None),
    )
    prompt_ref = _evaluation_prompt(
        artifact_host,
        compiled,
        suffix=f"late_{adapter_terminal_status}",
    )
    input_content = b'{"value":"deadline_input"}'
    input_ref = artifact_host.put_bytes(
        artifact_kind_id="native_input",
        schema_version="v1",
        schema_ref=compiled.module.input_schema_ref,
        schema_sha256=compiled.module.input_schema_sha256,
        media_type="application/json",
        content=input_content,
        idempotency_key=f"late_input_{adapter_terminal_status}",
        logical_name="task_input",
    )
    input_binding = ModuleInputBinding(
        logical_name="task_input",
        input_ref=input_ref.artifact_ref,
        input_sha256=input_ref.artifact_sha256,
        schema_ref=compiled.module.input_schema_ref,
        schema_sha256=compiled.module.input_schema_sha256,
        media_type="application/json",
    )
    execution_id = f"execution_late_{adapter_terminal_status}"
    workflow_request = WorkflowModuleExecutionRequest.build(
        request_id=f"request_late_{adapter_terminal_status}",
        purpose=ModuleExecutionPurpose.EVALUATION,
        workflow_execution_id=execution_id,
        dispatch_id=f"dispatch_late_{adapter_terminal_status}",
        workflow_node_id="state_native_module",
        module_run_id=f"module_run_late_{adapter_terminal_status}",
        module_release_ref=compiled.module.release_ref,
        module_release_sha256=compiled.module.release_sha256,
        input_package_ref=input_ref.artifact_ref,
        input_package_sha256=input_ref.artifact_sha256,
        inputs=(input_binding,),
        variants=(
            ModuleVariantRequest(
                arm_key="default",
                replicate_index=0,
                execution_profile_ref=compiled.execution_profile.release_ref,
                execution_profile_sha256=(
                    compiled.execution_profile.release_sha256
                ),
                prompt_envelope_ref=prompt_ref.artifact_ref,
                prompt_envelope_sha256=prompt_ref.artifact_sha256,
            ),
        ),
        idempotency_key=f"idempotency_late_{adapter_terminal_status}",
    )
    execution = WorkflowExecutionRecord(
        workflow_execution_id=execution_id,
        workflow_id="workflow_native_module",
        workflow_contract_version="v1",
        tenant_id="tenant_test",
        cell_id="cell_test",
        principal_id="principal_test",
        execution_release_ref="execution-release:native-workflow@v1",
        graph_sha256="a" * 64,
        runtime_execution_binding_ref="runtime-binding:native-workflow@v1",
        runtime_execution_binding_sha256="b" * 64,
        authorization_decision_ref="authorization-decision:native-workflow@v1",
        authorization_decision_sha256="c" * 64,
        execution_principal_delegation_ref="delegation:native-workflow@v1",
        execution_principal_delegation_sha256="d" * 64,
        entitlement_snapshot_ref="entitlement:native-workflow@v1",
        entitlement_snapshot_hash="e" * 64,
        execution_input_package_refs=(input_ref.artifact_ref,),
        execution_input_package_sha256=input_ref.artifact_sha256,
        recorded_at_utc=start_at,
    )
    execution_input = ExecutionInputRef(
        execution_input_id=f"execution_input_late_{adapter_terminal_status}",
        workflow_execution_id=execution_id,
        input_type_id="native_input",
        schema_version="v1",
        input_ref=input_ref.artifact_ref,
        input_sha256=input_ref.artifact_sha256,
        byte_size=len(input_content),
        media_type="application/json",
        recorded_at_utc=start_at,
        logical_name="task_input",
    )
    record_store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=lambda row: (
            artifact_host.read_bytes(row.output_ref, row.output_sha256)
            is not None
        )
    )
    record_store.commit(
        RuntimeRecordBatch(
            workflow_execution_id=execution_id,
            transaction_id=f"transaction_late_{adapter_terminal_status}",
            records=(execution, execution_input),
        )
    )
    authority, _ = _evaluation_authority(
        registry,
        workflow_request,
        scope_id=execution_id,
        input_package_ref=input_ref.artifact_ref,
        input_package_sha256=input_ref.artifact_sha256,
    )

    def clock() -> str:
        return observed_at if adapter.calls else start_at

    run = run_workflow_module(
        workflow_request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=WorkflowModuleLedgerRecorder(
            WorkflowModuleLedgerBinding(
                record_store=record_store,
                entitlement_snapshot_hash=execution.entitlement_snapshot_hash,
                claim_token_secret=b"d" * 32,
            )
        ),
        authority=authority,
        clock=clock,
    )

    assert adapter.calls == 1
    assert len(run.attempts) == 1
    attempt = run.attempts[0]
    assert attempt.status == expected_status
    assert attempt.recorded_at_utc == observed_at
    if expected_status == "completed":
        assert attempt.failure_class is None
        assert attempt.period_end_at_utc == observed_at
        assert len(run.outputs) == 1
    else:
        assert attempt.failure_class == "timeout"
        assert attempt.period_end_at_utc == deadline_at
        assert run.outputs == ()
        detail = _failure_detail(run, artifact_host)
        assert detail["deadline_at_utc"] == deadline_at
        if adapter_raises:
            assert detail["original_failure_class"] == "unknown"
            assert detail["disposition"] == "adapter_conformance_failure"
        else:
            assert detail["disposition"] == "execution_deadline_exceeded"
            assert detail["observed_terminal_status"] == (
                adapter_terminal_status
            )
    assert attempt.usage.input_tokens == (None if adapter_raises else 3)
    assert attempt.usage.output_tokens == (None if adapter_raises else 2)
    terminal = record_store.load_trace(execution_id).records_of_type(
        WorkflowAttemptRecord
    )
    assert len(terminal) == 1
    assert terminal[0].period_end_at_utc == (
        observed_at if expected_status == "completed" else deadline_at
    )
    assert terminal[0].recorded_at_utc == observed_at
    calls = record_store.load_trace(execution_id).records_of_type(
        ModelCallRecord
    )
    assert len(calls) == 1
    assert calls[0].status_id == expected_status
    assert calls[0].recorded_at_utc == observed_at
    usage = record_store.load_trace(execution_id).records_of_type(UsageEvent)
    assert len(usage) == 1
    assert usage[0].recorded_at_utc == observed_at


def _emit_live_provider_evidence(run, compiled) -> None:
    print(
        "LIVE_PROVIDER_EVIDENCE="
        + json.dumps(
            {
                "module_run_id": run.module_run.module_run_id,
                "attempt_id": run.attempts[0].attempt_id,
                "provider_id": compiled.execution_profile.provider_id,
                "model_id": compiled.execution_profile.model_id,
                "transport_kind": compiled.execution_profile.transport_kind,
                "output_sha256": run.outputs[0].output_sha256,
                "usage": run.attempts[0].usage.as_dict(),
            },
            sort_keys=True,
        )
    )


class _ProviderIntegrationToolSessionFactory:
    """Controlled governed-read seam used with a real Claude model."""

    def __init__(self, artifact_host: InMemoryCellArtifactStore) -> None:
        self._artifact_host = artifact_host
        self.sessions: list[_ProviderIntegrationToolSession] = []

    def open_session(self, request):
        session = _ProviderIntegrationToolSession(
            request,
            self._artifact_host,
        )
        self.sessions.append(session)
        return session


class _ProviderIntegrationToolSession:
    def __init__(self, request, artifact_host) -> None:
        self._request = request
        self._artifact_host = artifact_host
        self._observations: list[ModuleToolCallObservation] = []
        self._intent_count = 0

    @property
    def definitions(self):
        return (
            ProviderToolDefinition(
                tool_name="read_source",
                description="Read the authorized evaluation source by source_id",
                input_schema={
                    "type": "object",
                    "properties": {"source_id": {"type": "string"}},
                    "required": ["source_id"],
                    "additionalProperties": False,
                },
            ),
        )

    def operation_intent(self, tool_name, payload):
        self._intent_count += 1
        return ProviderOperationIntent(
            workflow_execution_id=self._request.execution_scope_id,
            module_run_id=self._request.module_run_id,
            variant_id=self._request.variant_id,
            attempt_id=self._request.attempt_id,
            capability_id=tool_name,
            resource_id=payload["source_id"],
            action_id=tool_name,
            entitlement_snapshot_hash=(
                self._request.execution_authorization_binding_sha256
            ),
            idempotency_key=(
                f"live_gateway_{self._request.attempt_id}_{self._intent_count}"
            ),
            expires_after_seconds=30,
        )

    def invoke(self, tool_name, payload, authorization):
        authorization.validate()
        ordinal = len(self._observations) + 1
        request_bytes = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        response = {
            "source_id": payload["source_id"],
            "fact": "runtime_gateway_authorized",
        }
        response_bytes = json.dumps(
            response,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request_artifact = self._artifact_host.put_bytes(
            artifact_kind_id="gateway_tool_request",
            schema_version="gateway_tool_request_v1",
            schema_ref="schema:gateway_tool_request@v1",
            schema_sha256="7" * 64,
            media_type="application/json",
            content=request_bytes,
            idempotency_key=(
                f"live_gateway_request_{self._request.attempt_id}_{ordinal}"
            ),
        )
        response_artifact = self._artifact_host.put_bytes(
            artifact_kind_id="gateway_tool_response",
            schema_version="gateway_tool_response_v1",
            schema_ref="schema:gateway_tool_response@v1",
            schema_sha256="8" * 64,
            media_type="application/json",
            content=response_bytes,
            idempotency_key=(
                f"live_gateway_response_{self._request.attempt_id}_{ordinal}"
            ),
        )
        self._observations.append(
            ModuleToolCallObservation(
                tool_call_id=f"live_gateway_call_{ordinal:03d}",
                tool_name=tool_name,
                request_ref=request_artifact.artifact_ref,
                request_sha256=request_artifact.artifact_sha256,
                response_ref=response_artifact.artifact_ref,
                response_sha256=response_artifact.artifact_sha256,
            )
        )
        return response

    def validate_completion(self) -> None:
        if len(self._observations) != 1:
            raise ValueError("live Gateway smoke requires exactly one governed read")

    @property
    def observations(self):
        return tuple(self._observations)


def test_run_module_evaluation_executes_registered_codex_transport(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="codex_stub")
    captured: dict[str, object] = {}

    def invoker(**fields) -> CodexCliInvocationResult:
        captured.update(fields)
        stdout = "\n".join(
            (
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "agent_message",
                            "text": json.dumps({"value": "through_run_module"}),
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {
                            "input_tokens": 11,
                            "output_tokens": 7,
                            "cached_input_tokens": 3,
                        },
                    }
                ),
            )
        )
        return CodexCliInvocationResult(returncode=0, stdout=stdout, stderr="")

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(compiled, prompt_ref, suffix="codex_stub")
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert _assert_completed_provider_run(run, artifact_host) == {
        "value": "through_run_module"
    }
    assert run.attempts[0].usage.input_tokens == 11
    assert run.attempts[0].usage.output_tokens == 7
    assert captured["argv"][1] == "exec"


def test_run_module_records_registered_codex_transport_failure(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="codex_failure")

    def invoker(**_fields) -> CodexCliInvocationResult:
        return CodexCliInvocationResult(
            returncode=9,
            stdout=json.dumps(
                {
                    "type": "turn.failed",
                    "usage": {"input_tokens": 5, "output_tokens": 0},
                }
            ),
            stderr="provider unavailable",
        )

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(compiled, prompt_ref, suffix="codex_failure")
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert run.attempts[0].status == "failed"
    assert run.attempts[0].failure_class == "provider"
    assert run.attempts[0].failure_detail_ref is not None
    assert run.attempts[0].usage.input_tokens == 5
    assert run.resolution is None


def test_tool_free_profile_rejects_undeclared_gateway_surface_before_provider(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(
        tmp_path,
        declared_operation_ids=("invoke_model", "read_protected_source"),
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="protected")
    entered = False

    def invoker(**_fields) -> CodexCliInvocationResult:
        nonlocal entered
        entered = True
        raise AssertionError("protected Module reached Provider")

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(compiled, prompt_ref, suffix="protected")
    authority, _ = _evaluation_authority(registry, request)

    with pytest.raises(NotImplementedError, match="outside the admitted"):
        run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
            authority=authority,
        )

    assert entered is False


def test_run_module_rejects_codex_agent_draft_workspace_before_provider(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="codex_workspace_profile",
        executor_adapter_id="codex_cli_agent_workspace_executor",
        executor_adapter_revision="v1",
        execution_mode="agent",
        attempt_workspace_policy="own_draft_read_write",
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="workspace")
    entered = False

    def invoker(**_fields) -> CodexCliInvocationResult:
        nonlocal entered
        entered = True
        raise AssertionError("unadmitted Codex workspace reached Provider")

    executor = CodexCliAgentWorkspaceModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(compiled, prompt_ref, suffix="workspace")
    authority, _ = _evaluation_authority(registry, request)

    with pytest.raises(NotImplementedError, match="outside the admitted"):
        run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
            authority=authority,
            clock=lambda: _TEST_TIME,
        )

    assert entered is False


def test_run_module_executes_claude_agent_draft_workspace_slice(
    tmp_path: Path,
) -> None:
    claude_module = pytest.importorskip(
        "agent_runtime.invocation.invocation_claude_module_invocation"
    )
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="claude_workspace_profile",
        executor_adapter_id=(
            "claude_agent_sdk_inline_draft_workspace_executor"
        ),
        executor_adapter_revision="v1",
        transport_kind="claude_agent_sdk",
        provider_id="anthropic",
        model_id="claude-workspace-test",
        execution_mode="agent",
        attempt_workspace_policy="own_draft_read_write",
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(
        artifact_host,
        compiled,
        suffix="claude_workspace",
    )
    observed: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        async for _message in prompt:
            pass
        hook = options.hooks["PreToolUse"][0].hooks[0]
        draft = Path(options.cwd) / "draft.txt"
        decision = await hook(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": str(draft),
                    "content": "workspace_slice",
                },
            },
            None,
            {},
        )
        observed["decision"] = decision
        draft.write_text("workspace_slice", encoding="utf-8")
        yield claude_module.ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="session_workspace",
            usage={"input_tokens": 13, "output_tokens": 5},
            structured_output={"value": draft.read_text(encoding="utf-8")},
        )

    executor = claude_module.ClaudeAgentSdkInlineDraftWorkspaceModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        query_fn=fake_query,
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(
        compiled,
        prompt_ref,
        suffix="claude_workspace",
    )
    authority, product = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert _assert_completed_provider_run(run, artifact_host) == {
        "value": "workspace_slice"
    }
    assert observed["decision"]["hookSpecificOutput"][
        "permissionDecision"
    ] == "allow"
    assert len(product.operation_queries) == 1


def test_run_module_provider_transport_still_rejects_production_purpose(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="production")
    entered = False

    def invoker(**_fields) -> CodexCliInvocationResult:
        nonlocal entered
        entered = True
        raise AssertionError("production request reached Provider")

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(
        compiled,
        prompt_ref,
        suffix="production",
        purpose=ModuleExecutionPurpose.WORKFLOW,
    )

    with pytest.raises(NotImplementedError, match="production Module execution"):
        run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
        )

    assert entered is False


def test_model_module_without_authority_is_rejected_before_dispatch(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    artifact_host = InMemoryCellArtifactStore()
    registry, adapters, adapter = _registered_stub(compiled, artifact_host)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="no_authority")
    request = _evaluation_request(compiled, prompt_ref, suffix="no_authority")

    with pytest.raises(PermissionError, match="module execution authority"):
        run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
        )

    assert adapter.calls == 0


def test_denied_product_decision_fails_the_attempt_without_provider_call(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    artifact_host = InMemoryCellArtifactStore()
    registry, adapters, adapter = _registered_stub(compiled, artifact_host)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="denied")
    request = _evaluation_request(compiled, prompt_ref, suffix="denied")
    authority, product = _evaluation_authority(registry, request)
    product.operation_effect = GatewayDecisionEffect.DENY

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert adapter.calls == 0
    attempt = run.attempts[0]
    assert attempt.status == "failed"
    assert attempt.failure_class == "authorization"
    assert attempt.output_refs == ()
    assert run.outputs == ()
    assert run.resolution is None
    detail = _failure_detail(run, artifact_host)
    assert detail["disposition"] == "product_operation_denied"
    assert detail["reason_code"] == "operation_denied"


def test_fence_closed_while_result_in_flight_quarantines_the_late_result(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="in_flight")
    request = _evaluation_request(compiled, prompt_ref, suffix="in_flight")
    authority, product = _evaluation_authority(registry, request)

    def revoke_during_execution(_request, _host) -> None:
        product.context_state = ExecutionAuthorizationContextState.REVOKED

    adapter = _StubInlineAdapter(
        release_registry=registry,
        artifact_host=artifact_host,
        on_execute=revoke_during_execution,
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert adapter.calls == 1
    attempt = run.attempts[0]
    assert attempt.status == "failed"
    assert attempt.failure_class == "authorization"
    assert attempt.output_refs == ()
    assert run.outputs == ()
    assert run.resolution is None
    assert attempt.usage.input_tokens == 3
    detail = _failure_detail(run, artifact_host)
    assert detail["disposition"] == "stale_result_quarantined"


def test_fence_closed_at_dispatch_records_failed_attempt_and_replays(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    artifact_host = InMemoryCellArtifactStore()
    registry, adapters, adapter = _registered_stub(compiled, artifact_host)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="fence_dispatch")
    request = _evaluation_request(compiled, prompt_ref, suffix="fence_dispatch")
    authority, product = _evaluation_authority(registry, request)
    product.context_state = ExecutionAuthorizationContextState.REVOKED
    ledger = InMemoryModuleExecutionLedger()

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=ledger,
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert adapter.calls == 0
    attempt = run.attempts[0]
    assert attempt.status == "failed"
    assert attempt.failure_class == "authorization"
    assert run.outputs == ()
    assert run.resolution is None
    detail = _failure_detail(run, artifact_host)
    assert detail["disposition"] == "authorization_refused_at_dispatch"
    assert "fence is closed" in detail["reason"]

    replay = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=ledger,
        authority=authority,
        clock=lambda: _TEST_TIME,
    )
    assert replay == run
    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("mutation", "payload"),
    (
        ("unstaged_slot", b'{"value":"stub"}'),
        ("not_json", b"not json at all"),
        ("schema_violation", b'{"unexpected":1}'),
    ),
)
def test_nonconformant_completed_results_record_bounded_failures(
    tmp_path: Path,
    mutation: str,
    payload: bytes,
) -> None:
    compiled = _stub_compiled(tmp_path)
    artifact_host = InMemoryCellArtifactStore()
    registry, adapters, adapter = _registered_stub(
        compiled,
        artifact_host,
        payload=payload,
    )
    if mutation == "unstaged_slot":
        adapter.skip_staging = True
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix=mutation)
    request = _evaluation_request(compiled, prompt_ref, suffix=mutation)
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert adapter.calls == 1
    attempt = run.attempts[0]
    assert attempt.status == "failed"
    assert attempt.failure_class == "unknown"
    assert attempt.output_refs == ()
    assert run.outputs == ()
    assert run.resolution is None
    detail = _failure_detail(run, artifact_host)
    assert detail["disposition"] == "adapter_conformance_failure"


def test_result_refs_outside_the_kernel_store_are_a_conformance_failure(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    kernel_store = InMemoryCellArtifactStore()
    foreign_store = InMemoryCellArtifactStore()
    registry = _register_compiled_for_evaluation(compiled)
    adapter = _StubInlineAdapter(
        release_registry=registry,
        artifact_host=foreign_store,
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    prompt_ref = _evaluation_prompt(kernel_store, compiled, suffix="split_store")
    request = _evaluation_request(compiled, prompt_ref, suffix="split_store")
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=kernel_store,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    attempt = run.attempts[0]
    assert attempt.status == "failed"
    assert attempt.failure_class == "unknown"
    assert run.resolution is None
    detail = _failure_detail(run, kernel_store)
    assert detail["disposition"] == "adapter_conformance_failure"


def test_long_input_logical_names_project_into_the_canonical_request(
    tmp_path: Path,
) -> None:
    from agent_runtime.contracts.execution_module_definition import (
        ModuleInputBinding,
    )

    compiled = _stub_compiled(tmp_path)
    artifact_host = InMemoryCellArtifactStore()
    registry, adapters, adapter = _registered_stub(compiled, artifact_host)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="long_input")
    observed: dict[str, object] = {}

    def capture(request, _host) -> None:
        observed["inputs"] = request.authorized_inputs

    adapter._on_execute = capture
    long_name = "a" * 160
    request = ModuleExecutionRequest.build(
        request_id="request_native_long_input",
        purpose=ModuleExecutionPurpose.EVALUATION,
        module_release_ref=compiled.module.release_ref,
        module_release_sha256=compiled.module.release_sha256,
        isolated_scope_ref="scope-ref:native-long-input",
        isolated_scope_sha256="2" * 64,
        input_package_ref="artifact-ref:input-package-long-input",
        input_package_sha256="3" * 64,
        inputs=(
            ModuleInputBinding(
                logical_name=long_name,
                input_ref="artifact-ref:long-input",
                input_sha256="4" * 64,
                schema_ref="schema:long_input@v1",
                schema_sha256="5" * 64,
                media_type="application/json",
            ),
        ),
        variants=(
            ModuleVariantRequest(
                arm_key="native_long_input",
                replicate_index=0,
                execution_profile_ref=compiled.execution_profile.release_ref,
                execution_profile_sha256=(
                    compiled.execution_profile.release_sha256
                ),
                prompt_envelope_ref=prompt_ref.artifact_ref,
                prompt_envelope_sha256=prompt_ref.artifact_sha256,
            ),
        ),
        idempotency_key="idempotency_native_long_input",
    )
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert run.attempts[0].status == "completed"
    projected = observed["inputs"][0]
    assert projected.execution_input_id == long_name
    assert projected.schema_ref == "schema:long_input@v1"
    assert projected.schema_sha256 == "5" * 64


def test_fence_commit_is_atomic_against_concurrent_invalidation(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    registry = _register_compiled_for_evaluation(compiled)
    request = _evaluation_request(
        compiled,
        _evaluation_prompt(
            InMemoryCellArtifactStore(), compiled, suffix="atomic"
        ),
        suffix="atomic",
    )
    authority, product = _evaluation_authority(registry, request)
    controller = authority.controller
    binding_ref = authority.binding.binding_ref

    entered = threading.Event()
    fence_flipped = threading.Event()

    def invalidate() -> None:
        entered.wait(timeout=5)
        product.context_state = ExecutionAuthorizationContextState.REVOKED
        controller.revalidate(
            binding_ref=binding_ref,
            observed_at_utc="2026-08-09T12:00:01Z",
        )
        fence_flipped.set()

    invalidator = threading.Thread(target=invalidate)
    invalidator.start()

    def finalize(fence: ExecutionAuthorizationFence) -> str:
        entered.set()
        time.sleep(0.2)
        assert not fence_flipped.is_set(), (
            "a fence transition interleaved inside the atomic commit"
        )
        return fence.state.value

    observed_state = controller.finalize_under_current_fence(
        binding_ref,
        finalize,
    )
    invalidator.join(timeout=5)

    assert observed_state == ExecutionAuthorizationFenceState.OPEN.value
    assert fence_flipped.is_set()
    closed = controller._ledger.current_fence(binding_ref)
    assert closed.state is ExecutionAuthorizationFenceState.FENCED


@pytest.mark.parametrize(
    "mismatch",
    ("input_package", "execution_scope"),
)
def test_authority_binding_closure_mismatch_is_zero_invocation(
    tmp_path: Path,
    mismatch: str,
) -> None:
    compiled = _stub_compiled(tmp_path)
    artifact_host = InMemoryCellArtifactStore()
    registry, adapters, adapter = _registered_stub(compiled, artifact_host)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix=mismatch)
    request = _evaluation_request(compiled, prompt_ref, suffix=mismatch)
    if mismatch == "input_package":
        authority, _ = _evaluation_authority(
            registry,
            request,
            input_package_ref="artifact-ref:another-package",
            input_package_sha256="9" * 64,
        )
    else:
        authority, _ = _evaluation_authority(
            registry,
            request,
            scope_id=isolated_execution_scope_id(
                "scope-ref:another-execution",
                "8" * 64,
            ),
        )

    with pytest.raises(PermissionError, match="authority closure mismatch"):
        run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
            authority=authority,
            clock=lambda: _TEST_TIME,
        )

    assert adapter.calls == 0


def test_wrong_adapter_revision_is_zero_invocation(tmp_path: Path) -> None:
    compiled = _stub_compiled(tmp_path)
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    adapter = _StubInlineAdapter(
        release_registry=registry,
        artifact_host=artifact_host,
        adapter_revision="v2",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="revision")
    request = _evaluation_request(compiled, prompt_ref, suffix="revision")
    authority, _ = _evaluation_authority(registry, request)

    with pytest.raises(KeyError, match="stub_inline_executor@v1"):
        run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
            authority=authority,
            clock=lambda: _TEST_TIME,
        )

    assert adapter.calls == 0


def test_descriptor_capability_mismatch_is_zero_invocation(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    adapter = _StubInlineAdapter(
        release_registry=registry,
        artifact_host=artifact_host,
        supported_execution_modes=("agent",),
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="capability")
    request = _evaluation_request(compiled, prompt_ref, suffix="capability")
    authority, _ = _evaluation_authority(registry, request)

    with pytest.raises(PermissionError, match="capability does not cover"):
        run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
            authority=authority,
            clock=lambda: _TEST_TIME,
        )

    assert adapter.calls == 0


@pytest.mark.parametrize("misbehavior", ("raises", "invalid_type"))
def test_adapter_misbehavior_records_bounded_conformance_failure(
    tmp_path: Path,
    misbehavior: str,
) -> None:
    compiled = _stub_compiled(tmp_path)
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    if misbehavior == "raises":
        def explode(_request, _host) -> None:
            raise RuntimeError("adapter internal defect")

        adapter = _StubInlineAdapter(
            release_registry=registry,
            artifact_host=artifact_host,
            on_execute=explode,
        )
    else:
        adapter = _StubInlineAdapter(
            release_registry=registry,
            artifact_host=artifact_host,
            result_type_override={"not": "a result"},
        )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix=misbehavior)
    request = _evaluation_request(compiled, prompt_ref, suffix=misbehavior)
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    attempt = run.attempts[0]
    assert attempt.status == "failed"
    assert attempt.failure_class == "unknown"
    assert attempt.output_refs == ()
    assert run.outputs == ()
    assert run.resolution is None
    detail = _failure_detail(run, artifact_host)
    assert detail["disposition"] == "adapter_conformance_failure"


def test_failed_result_with_staged_bytes_produces_no_resolution(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    artifact_host = InMemoryCellArtifactStore()
    registry, adapters, adapter = _registered_stub(
        compiled,
        artifact_host,
        terminal_status="failed",
    )
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="staged_failed")
    request = _evaluation_request(compiled, prompt_ref, suffix="staged_failed")
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert adapter.calls == 1
    assert run.attempts[0].status == "failed"
    assert run.attempts[0].failure_class == "provider"
    assert run.outputs == ()
    assert run.resolution is None


def test_duplicate_request_replays_without_reinvoking_the_provider(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    artifact_host = InMemoryCellArtifactStore()
    registry, adapters, adapter = _registered_stub(compiled, artifact_host)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="duplicate")
    request = _evaluation_request(compiled, prompt_ref, suffix="duplicate")
    authority, _ = _evaluation_authority(registry, request)
    ledger = InMemoryModuleExecutionLedger()

    first = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=ledger,
        authority=authority,
        clock=lambda: _TEST_TIME,
    )
    replay = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=ledger,
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert adapter.calls == 1
    assert replay == first
    assert first.attempts[0].status == "completed"


def _operation_free_release(
    *,
    transport_kind: str,
    executor_adapter_id: str,
    executor_adapter_revision: str,
    provider_id: str = "provider_stub",
) -> tuple[RuntimeReleaseRegistry, object, object]:
    """Register one deterministic operation-free Module and its profile."""

    from agent_runtime.contracts.registry_release_definition import (
        ModuleEntryPolicy,
        ModuleKind,
    )

    profile = compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
        execution_profile_id="profile_operation_free",
        executor_adapter_id=executor_adapter_id,
        executor_adapter_revision=executor_adapter_revision,
        transport_kind=transport_kind,
        provider_id=provider_id,
        model_id="model_stub",
        reasoning_profile="none",
        execution_mode="tool_free",
        semantic_input_delivery_mode="inline",
        attempt_workspace_policy="none",
        gateway_access_reasons=(),
        output_constraint_mode="prompt_only_json",
        tool_policy=(),
        network_policy="denied",
        timeout_seconds=60,
        release_version="v1",
        )
    )
    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="operation_free",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    evaluation = compile_evaluation_policy_release(
        EvaluationPolicyReleaseCandidate(
            policy_id="operation_free",
            policy_version="v1",
            evaluation_mode="none",
        )
    )
    retry = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="operation_free",
            policy_version="v1",
            max_attempts=1,
        )
    )
    input_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "schema:operation_free_input@v1",
        "type": "object",
    }
    output_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "schema:operation_free_output@v1",
        "type": "object",
    }
    compiled = compile_non_agent_module_release(NonAgentModuleReleaseCandidate(
        module_id="module_operation_free",
        module_version="v1",
        module_kind=ModuleKind.DETERMINISTIC,
        owner_contract_ref="contract:operation-free@v1",
        owner_contract_content="# Operation Free\n",
        executable_ref="python:operation.free.run",
        executable_content=b"def run(value): return value\n",
        input_schema_ref="schema:operation_free_input@v1",
        input_schema_document=json.dumps(input_schema),
        output_schema_ref="schema:operation_free_output@v1",
        output_schema_document=json.dumps(output_schema),
        declared_operation_ids=(),
        behavior_policy_ref=behavior.release_ref,
        behavior_policy_sha256=behavior.release_sha256,
        evaluation_policy_ref=evaluation.release_ref,
        evaluation_policy_sha256=evaluation.release_sha256,
        retry_policy_ref=retry.release_ref,
        retry_policy_sha256=retry.release_sha256,
        compatible_transport_kinds=(transport_kind,),
        entry_policy=ModuleEntryPolicy.STANDALONE_ALLOWED,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    ))
    module = compiled.module
    schema_assets = (
        *runtime_owned_policy_schema_assets(),
        *compiled.schema_assets,
    )
    admissions = tuple(
        candidate_admission_intent(record)
        for record in (
            *schema_assets,
            behavior,
            evaluation,
            retry,
            profile,
            module,
        )
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=schema_assets,
            behavior_policies=(behavior,),
            evaluation_policies=(evaluation,),
            retry_policies=(retry,),
            execution_profiles=(profile,),
            modules=(module,),
            admission_intents=admissions,
        )
    )
    return registry, module, profile


def _operation_free_request(module, profile, *, suffix: str) -> ModuleExecutionRequest:
    return ModuleExecutionRequest.build(
        request_id=f"request_operation_free_{suffix}",
        purpose=ModuleExecutionPurpose.TEST,
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        isolated_scope_ref=f"scope-ref:operation-free-{suffix}",
        isolated_scope_sha256="2" * 64,
        input_package_ref=f"artifact-ref:operation-free-{suffix}",
        input_package_sha256="3" * 64,
        inputs=(),
        variants=(
            ModuleVariantRequest(
                arm_key=f"operation_free_{suffix}",
                replicate_index=0,
                execution_profile_ref=profile.release_ref,
                execution_profile_sha256=profile.release_sha256,
                prompt_envelope_ref=None,
                prompt_envelope_sha256=None,
            ),
        ),
        idempotency_key=f"idempotency_operation_free_{suffix}",
    )


def test_operation_free_in_process_module_runs_without_authority(
    tmp_path: Path,
) -> None:
    registry, module, profile = _operation_free_release(
        transport_kind="in_process_test",
        executor_adapter_id="stub_inline_executor",
        executor_adapter_revision="v1",
    )
    artifact_host = InMemoryCellArtifactStore()
    adapter = _StubInlineAdapter(
        release_registry=registry,
        artifact_host=artifact_host,
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    request = _operation_free_request(module, profile, suffix="local")

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        clock=lambda: _TEST_TIME,
    )

    assert adapter.calls == 1
    assert run.attempts[0].status == "completed"
    assert run.resolution is not None
    assert run.resolution.resolution_status == "resolved"


def test_operation_free_module_cannot_use_a_provider_transport(
    tmp_path: Path,
) -> None:
    registry, module, profile = _operation_free_release(
        transport_kind="codex_cli",
        executor_adapter_id="codex_cli_agent_executor",
        executor_adapter_revision="v2",
        provider_id="openai",
    )
    artifact_host = InMemoryCellArtifactStore()
    entered = False

    def invoker(**_fields) -> CodexCliInvocationResult:
        nonlocal entered
        entered = True
        raise AssertionError("operation-free Module reached Provider")

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _operation_free_request(module, profile, suffix="codex")

    with pytest.raises(
        PermissionError,
        match="declared model invocation operation",
    ):
        run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
        )

    assert entered is False


def test_dynamic_operation_callback_fails_closed_outside_gateway_profile(
    tmp_path: Path,
) -> None:
    compiled = _stub_compiled(tmp_path)
    registry = _register_compiled_for_evaluation(compiled)
    observed: dict[str, object] = {}

    def probe_dynamic_authorization(request, host) -> None:
        intent = ProviderOperationIntent(
            workflow_execution_id="isolated_scope_dynamic_probe",
            module_run_id=request.module_run_id,
            variant_id=request.variant_id,
            attempt_id=request.attempt_id,
            capability_id="capability_probe",
            resource_id="resource_probe",
            action_id="action_probe",
            entitlement_snapshot_hash="6" * 64,
            idempotency_key="dynamic_probe_001",
            expires_after_seconds=30,
        )
        try:
            host.authorize_operation(intent)
        except PermissionError as exc:
            observed["denied"] = str(exc)

    artifact_host = InMemoryCellArtifactStore()
    adapter = _StubInlineAdapter(
        release_registry=registry,
        artifact_host=artifact_host,
        on_execute=probe_dynamic_authorization,
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="dynamic")
    request = _evaluation_request(compiled, prompt_ref, suffix="dynamic")
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert "outside the admitted Gateway profile" in str(observed["denied"])
    assert run.attempts[0].status == "failed"
    assert run.attempts[0].failure_class == "authorization"
    assert run.resolution is None


def _gateway_stub_compiled(tmp_path: Path):
    return _stub_compiled(
        tmp_path,
        declared_operation_ids=("invoke_model", "read_source"),
        executor_adapter_id="claude_agent_sdk_gateway_executor",
        executor_adapter_revision="v2",
        transport_kind="claude_agent_sdk",
        provider_id="anthropic",
        execution_mode="agent",
        semantic_input_delivery_mode="gateway_read",
        attempt_workspace_policy="none",
        gateway_access_reasons=("oversized_knowledge_retrieval",),
        tool_policy=("read_source",),
        network_policy="gateway_only",
    )


def _gateway_tool_callback(
    artifact_host: InMemoryCellArtifactStore,
    resource_calls: list[object],
    *,
    before_authorization=None,
    mismatched_lineage: bool = False,
):
    def callback(request, host):
        if before_authorization is not None:
            before_authorization()
        intent = ProviderOperationIntent(
            workflow_execution_id=(
                "isolated_scope_wrong"
                if mismatched_lineage
                else request.execution_scope_id
            ),
            module_run_id=request.module_run_id,
            variant_id=request.variant_id,
            attempt_id=request.attempt_id,
            capability_id="read_source",
            resource_id="resource_gateway_test",
            action_id="read_source",
            entitlement_snapshot_hash=(
                request.execution_authorization_binding_sha256
            ),
            idempotency_key=f"gateway_read_{request.attempt_id}",
            expires_after_seconds=30,
        )
        receipt = host.authorize_operation(intent)
        receipt.validate()
        resource_calls.append(receipt)
        request_artifact = artifact_host.put_bytes(
            artifact_kind_id="gateway_tool_request",
            schema_version="gateway_tool_request_v1",
            schema_ref="schema:gateway_tool_request@v1",
            schema_sha256="7" * 64,
            media_type="application/json",
            content=b'{"resource_id":"resource_gateway_test"}',
            idempotency_key=f"gateway_request_{request.attempt_id}",
        )
        response_artifact = artifact_host.put_bytes(
            artifact_kind_id="gateway_tool_response",
            schema_version="gateway_tool_response_v1",
            schema_ref="schema:gateway_tool_response@v1",
            schema_sha256="8" * 64,
            media_type="application/json",
            content=b'{"value":"authorized_resource"}',
            idempotency_key=f"gateway_response_{request.attempt_id}",
        )
        return (
            ModuleToolCallObservation(
                tool_call_id="gateway_call_001",
                tool_name="read_source",
                request_ref=request_artifact.artifact_ref,
                request_sha256=request_artifact.artifact_sha256,
                response_ref=response_artifact.artifact_ref,
                response_sha256=response_artifact.artifact_sha256,
            ),
        )

    return callback


def _registered_gateway_stub(
    tmp_path: Path,
    artifact_host: InMemoryCellArtifactStore,
    *,
    on_execute,
    supports_dynamic_operation_authorization: bool = True,
):
    compiled = _gateway_stub_compiled(tmp_path)
    registry = _register_compiled_for_evaluation(compiled)
    adapter = _StubInlineAdapter(
        release_registry=registry,
        artifact_host=artifact_host,
        adapter_id="claude_agent_sdk_gateway_executor",
        adapter_revision="v2",
        provider_id="anthropic",
        transport_kind="claude_agent_sdk",
        transport_family="sdk",
        supported_execution_modes=("agent",),
        supported_input_delivery_modes=("gateway_read",),
        supported_network_policies=("gateway_only",),
        supports_dynamic_operation_authorization=(
            supports_dynamic_operation_authorization
        ),
        on_execute=on_execute,
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="gateway")
    request = _evaluation_request(compiled, prompt_ref, suffix="gateway")
    authority, product = _evaluation_authority(registry, request)
    return compiled, registry, adapters, adapter, request, authority, product


def test_gateway_read_authorizes_each_resource_call_and_records_lineage(
    tmp_path: Path,
) -> None:
    artifact_host = InMemoryCellArtifactStore()
    resource_calls: list[object] = []
    (
        _compiled,
        registry,
        adapters,
        adapter,
        request,
        authority,
        product,
    ) = _registered_gateway_stub(
        tmp_path,
        artifact_host,
        on_execute=_gateway_tool_callback(artifact_host, resource_calls),
    )

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert adapter.calls == 1
    assert len(resource_calls) == 1
    assert tuple(query.operation_id for query in product.operation_queries) == (
        "invoke_model",
        "read_source",
    )
    assert run.attempts[0].status == "completed"
    assert len(run.attempts[0].tool_calls) == 1
    assert run.attempts[0].tool_calls[0].tool_name == "read_source"
    assert run.resolution is not None


def test_workflow_gateway_call_records_and_replays_exact_content_lineage(
    tmp_path: Path,
) -> None:
    artifact_host = InMemoryCellArtifactStore()
    resource_calls: list[object] = []
    (
        compiled,
        registry,
        adapters,
        adapter,
        isolated_request,
        _isolated_authority,
        _product,
    ) = _registered_gateway_stub(
        tmp_path,
        artifact_host,
        on_execute=_gateway_tool_callback(artifact_host, resource_calls),
    )
    input_content = b'{"value":"gateway_workflow_input"}'
    input_artifact = artifact_host.put_bytes(
        artifact_kind_id="gateway_workflow_input",
        schema_version="v1",
        schema_ref=compiled.module.input_schema_ref,
        schema_sha256=compiled.module.input_schema_sha256,
        media_type="application/json",
        content=input_content,
        idempotency_key="gateway_workflow_input",
        logical_name="task_input",
    )
    input_binding = ModuleInputBinding(
        logical_name="task_input",
        input_ref=input_artifact.artifact_ref,
        input_sha256=input_artifact.artifact_sha256,
        schema_ref=compiled.module.input_schema_ref,
        schema_sha256=compiled.module.input_schema_sha256,
        media_type="application/json",
    )
    workflow_request = WorkflowModuleExecutionRequest.build(
        request_id="request_workflow_gateway",
        purpose=ModuleExecutionPurpose.EVALUATION,
        workflow_execution_id="execution_workflow_gateway",
        dispatch_id="dispatch_workflow_gateway",
        workflow_node_id="state_gateway_module",
        module_run_id="module_run_workflow_gateway",
        module_release_ref=isolated_request.module_release_ref,
        module_release_sha256=isolated_request.module_release_sha256,
        input_package_ref=input_artifact.artifact_ref,
        input_package_sha256=input_artifact.artifact_sha256,
        inputs=(input_binding,),
        variants=isolated_request.variants,
        idempotency_key="idempotency_workflow_gateway",
    )
    execution = WorkflowExecutionRecord(
        workflow_execution_id=workflow_request.workflow_execution_id,
        workflow_id="workflow_gateway_module",
        workflow_contract_version="v1",
        tenant_id="tenant_test",
        cell_id="cell_test",
        principal_id="principal_test",
        execution_release_ref="execution-release:gateway-workflow@v1",
        graph_sha256="a" * 64,
        runtime_execution_binding_ref="runtime-binding:gateway-workflow@v1",
        runtime_execution_binding_sha256="b" * 64,
        authorization_decision_ref=(
            "authorization-decision:gateway-workflow@v1"
        ),
        authorization_decision_sha256="c" * 64,
        execution_principal_delegation_ref="delegation:gateway-workflow@v1",
        execution_principal_delegation_sha256="d" * 64,
        entitlement_snapshot_ref="entitlement:gateway-workflow@v1",
        entitlement_snapshot_hash="e" * 64,
        execution_input_package_refs=(
            workflow_request.input_package_ref,
        ),
        execution_input_package_sha256=(
            workflow_request.input_package_sha256
        ),
        recorded_at_utc=_TEST_TIME,
    )
    execution_input = ExecutionInputRef(
        execution_input_id="execution_input_workflow_gateway",
        workflow_execution_id=workflow_request.workflow_execution_id,
        input_type_id="gateway_input_package",
        schema_version="v1",
        input_ref=workflow_request.input_package_ref,
        input_sha256=workflow_request.input_package_sha256,
        byte_size=len(input_content),
        media_type="application/json",
        recorded_at_utc=_TEST_TIME,
        logical_name="input_package",
    )
    record_store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=lambda row: (
            artifact_host.read_bytes(row.output_ref, row.output_sha256)
            is not None
        )
    )
    record_store.commit(
        RuntimeRecordBatch(
            workflow_execution_id=workflow_request.workflow_execution_id,
            transaction_id="transaction_workflow_gateway_bootstrap",
            records=(execution, execution_input),
        )
    )
    authority, _ = _evaluation_authority(
        registry,
        workflow_request,
        scope_id=workflow_request.workflow_execution_id,
        input_package_ref=workflow_request.input_package_ref,
        input_package_sha256=workflow_request.input_package_sha256,
    )
    binding = WorkflowModuleLedgerBinding(
        record_store=record_store,
        entitlement_snapshot_hash=execution.entitlement_snapshot_hash,
        claim_token_secret=b"workflow-gateway-test-secret-32-bytes",
    )

    run = run_workflow_module(
        workflow_request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=WorkflowModuleLedgerRecorder(binding),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )
    replay = run_workflow_module(
        workflow_request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=WorkflowModuleLedgerRecorder(binding),
        authority=authority,
        clock=lambda: "2026-08-09T13:00:00Z",
    )

    assert adapter.calls == 1
    assert replay == run
    calls = record_store.load_trace(
        workflow_request.workflow_execution_id
    ).records_of_type(ToolCallRecord)
    assert len(calls) == 1
    assert calls[0].request_ref == run.attempts[0].tool_calls[0].request_ref
    assert calls[0].response_ref == run.attempts[0].tool_calls[0].response_ref
    assert calls[0].authorization_intent_ref is not None
    assert calls[0].authorization_decision_ref is not None
    assert calls[0].authorization_observation_ref is not None


def test_workflow_adapter_exception_after_tool_grant_requires_reconciliation(
    tmp_path: Path,
) -> None:
    artifact_host = InMemoryCellArtifactStore()
    resource_calls: list[object] = []
    authorize_tool = _gateway_tool_callback(artifact_host, resource_calls)

    def authorize_then_raise(request, host):
        authorize_tool(request, host)
        raise RuntimeError("adapter crashed after tool authorization")

    (
        compiled,
        registry,
        adapters,
        _adapter,
        isolated_request,
        _isolated_authority,
        _product,
    ) = _registered_gateway_stub(
        tmp_path,
        artifact_host,
        on_execute=authorize_then_raise,
    )
    input_content = b'{"value":"gateway_reconciliation_input"}'
    input_artifact = artifact_host.put_bytes(
        artifact_kind_id="gateway_workflow_input",
        schema_version="v1",
        schema_ref=compiled.module.input_schema_ref,
        schema_sha256=compiled.module.input_schema_sha256,
        media_type="application/json",
        content=input_content,
        idempotency_key="gateway_reconciliation_input",
        logical_name="task_input",
    )
    input_binding = ModuleInputBinding(
        logical_name="task_input",
        input_ref=input_artifact.artifact_ref,
        input_sha256=input_artifact.artifact_sha256,
        schema_ref=compiled.module.input_schema_ref,
        schema_sha256=compiled.module.input_schema_sha256,
        media_type="application/json",
    )
    workflow_request = WorkflowModuleExecutionRequest.build(
        request_id="request_gateway_reconciliation",
        purpose=ModuleExecutionPurpose.EVALUATION,
        workflow_execution_id="execution_gateway_reconciliation",
        dispatch_id="dispatch_gateway_reconciliation",
        workflow_node_id="state_gateway_module",
        module_run_id="module_run_gateway_reconciliation",
        module_release_ref=isolated_request.module_release_ref,
        module_release_sha256=isolated_request.module_release_sha256,
        input_package_ref=input_artifact.artifact_ref,
        input_package_sha256=input_artifact.artifact_sha256,
        inputs=(input_binding,),
        variants=isolated_request.variants,
        idempotency_key="idempotency_gateway_reconciliation",
    )
    execution = WorkflowExecutionRecord(
        workflow_execution_id=workflow_request.workflow_execution_id,
        workflow_id="workflow_gateway_module",
        workflow_contract_version="v1",
        tenant_id="tenant_test",
        cell_id="cell_test",
        principal_id="principal_test",
        execution_release_ref="execution-release:gateway-workflow@v1",
        graph_sha256="a" * 64,
        runtime_execution_binding_ref="runtime-binding:gateway-workflow@v1",
        runtime_execution_binding_sha256="b" * 64,
        authorization_decision_ref="authorization-decision:gateway-workflow@v1",
        authorization_decision_sha256="c" * 64,
        execution_principal_delegation_ref="delegation:gateway-workflow@v1",
        execution_principal_delegation_sha256="d" * 64,
        entitlement_snapshot_ref="entitlement:gateway-workflow@v1",
        entitlement_snapshot_hash="e" * 64,
        execution_input_package_refs=(workflow_request.input_package_ref,),
        execution_input_package_sha256=(
            workflow_request.input_package_sha256
        ),
        recorded_at_utc=_TEST_TIME,
    )
    execution_input = ExecutionInputRef(
        execution_input_id="execution_input_gateway_reconciliation",
        workflow_execution_id=workflow_request.workflow_execution_id,
        input_type_id="gateway_input_package",
        schema_version="v1",
        input_ref=workflow_request.input_package_ref,
        input_sha256=workflow_request.input_package_sha256,
        byte_size=len(input_content),
        media_type="application/json",
        recorded_at_utc=_TEST_TIME,
        logical_name="input_package",
    )
    record_store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=lambda row: (
            artifact_host.read_bytes(row.output_ref, row.output_sha256)
            is not None
        )
    )
    record_store.commit(
        RuntimeRecordBatch(
            workflow_execution_id=workflow_request.workflow_execution_id,
            transaction_id="transaction_gateway_reconciliation_bootstrap",
            records=(execution, execution_input),
        )
    )
    authority, _ = _evaluation_authority(
        registry,
        workflow_request,
        scope_id=workflow_request.workflow_execution_id,
        input_package_ref=workflow_request.input_package_ref,
        input_package_sha256=workflow_request.input_package_sha256,
    )

    with pytest.raises(
        AttemptToolReconciliationRequiredError,
        match="reconcile the durable Attempt",
    ):
        run_workflow_module(
            workflow_request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=WorkflowModuleLedgerRecorder(
                WorkflowModuleLedgerBinding(
                    record_store=record_store,
                    entitlement_snapshot_hash=(
                        execution.entitlement_snapshot_hash
                    ),
                    claim_token_secret=b"r" * 32,
                )
            ),
            authority=authority,
            clock=lambda: _TEST_TIME,
        )

    trace = record_store.load_trace(workflow_request.workflow_execution_id)
    assert len(trace.records_of_type(WorkflowAttemptStartedRecord)) == 1
    assert trace.records_of_type(WorkflowAttemptRecord) == ()
    assert len(trace.records_of_type(LegacyModuleCapabilityGrant)) == 2
    assert resource_calls


def test_gateway_read_denial_never_enters_resource_callable(
    tmp_path: Path,
) -> None:
    artifact_host = InMemoryCellArtifactStore()
    resource_calls: list[object] = []
    (
        _compiled,
        registry,
        adapters,
        _adapter,
        request,
        authority,
        product,
    ) = _registered_gateway_stub(
        tmp_path,
        artifact_host,
        on_execute=_gateway_tool_callback(artifact_host, resource_calls),
    )
    product.operation_effects["read_source"] = GatewayDecisionEffect.DENY

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert resource_calls == []
    assert run.attempts[0].status == "failed"
    assert run.attempts[0].failure_class == "authorization"
    assert run.outputs == ()
    assert run.resolution is None


def test_gateway_read_revalidates_fence_before_resource_call(
    tmp_path: Path,
) -> None:
    artifact_host = InMemoryCellArtifactStore()
    resource_calls: list[object] = []
    product_holder: dict[str, _ProductAuthorityDouble] = {}
    (
        _compiled,
        registry,
        adapters,
        _adapter,
        request,
        authority,
        product,
    ) = _registered_gateway_stub(
        tmp_path,
        artifact_host,
        on_execute=_gateway_tool_callback(
            artifact_host,
            resource_calls,
            before_authorization=lambda: setattr(
                product_holder["product"],
                "context_state",
                ExecutionAuthorizationContextState.REVOKED,
            ),
        ),
    )
    product_holder["product"] = product

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert resource_calls == []
    assert run.attempts[0].status == "failed"
    assert run.attempts[0].failure_class == "authorization"
    assert tuple(query.operation_id for query in product.operation_queries) == (
        "invoke_model",
    )


def test_gateway_read_rejects_cross_attempt_lineage_before_resource_call(
    tmp_path: Path,
) -> None:
    artifact_host = InMemoryCellArtifactStore()
    resource_calls: list[object] = []
    (
        _compiled,
        registry,
        adapters,
        _adapter,
        request,
        authority,
        product,
    ) = _registered_gateway_stub(
        tmp_path,
        artifact_host,
        on_execute=_gateway_tool_callback(
            artifact_host,
            resource_calls,
            mismatched_lineage=True,
        ),
    )

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert resource_calls == []
    assert run.attempts[0].failure_class == "authorization"
    assert len(product.operation_queries) == 1


def test_gateway_profile_requires_dynamic_authorization_descriptor(
    tmp_path: Path,
) -> None:
    artifact_host = InMemoryCellArtifactStore()
    resource_calls: list[object] = []
    (
        _compiled,
        registry,
        adapters,
        adapter,
        request,
        authority,
        product,
    ) = _registered_gateway_stub(
        tmp_path,
        artifact_host,
        on_execute=_gateway_tool_callback(artifact_host, resource_calls),
        supports_dynamic_operation_authorization=False,
    )

    with pytest.raises(PermissionError, match="dynamic operation authorization"):
        run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifact_host,
            ledger=InMemoryModuleExecutionLedger(),
            authority=authority,
            clock=lambda: _TEST_TIME,
        )

    assert adapter.calls == 0
    assert product.operation_queries == []
    assert resource_calls == []


@pytest.mark.parametrize("deny_tool", [False, True])
def test_claude_gateway_executor_routes_tool_through_kernel_authorization(
    tmp_path: Path,
    monkeypatch,
    deny_tool: bool,
) -> None:
    claude_module = pytest.importorskip(
        "agent_runtime.invocation.invocation_claude_module_invocation"
    )
    compiled = _compile_native_module(
        tmp_path,
        declared_operation_ids=("invoke_model", "read_source"),
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="claude_gateway_profile",
        executor_adapter_id="claude_agent_sdk_gateway_executor",
        executor_adapter_revision="v2",
        transport_kind="claude_agent_sdk",
        provider_id="anthropic",
        model_id="claude-gateway-test",
        execution_mode="agent",
        semantic_input_delivery_mode="gateway_read",
        gateway_access_reasons=("oversized_knowledge_retrieval",),
        tool_policy=("read_source",),
        network_policy="gateway_only",
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    events: list[str] = []

    class FakeSdkTool:
        def __init__(self, *, name, description, input_schema, handler):
            self.name = name
            self.description = description
            self.input_schema = input_schema
            self.handler = handler

    monkeypatch.setattr(claude_module, "SdkMcpTool", FakeSdkTool)
    monkeypatch.setattr(
        claude_module,
        "create_sdk_mcp_server",
        lambda **fields: fields,
    )

    class ToolSession:
        def __init__(self, request) -> None:
            self.request = request
            self._observations: list[ModuleToolCallObservation] = []

        @property
        def definitions(self):
            return (
                ProviderToolDefinition(
                    tool_name="read_source",
                    description="Read one authorized source",
                    input_schema={
                        "type": "object",
                        "properties": {"source_id": {"type": "string"}},
                        "required": ["source_id"],
                        "additionalProperties": False,
                    },
                ),
            )

        def operation_intent(self, tool_name, payload):
            events.append("intent")
            return ProviderOperationIntent(
                workflow_execution_id=self.request.execution_scope_id,
                module_run_id=self.request.module_run_id,
                variant_id=self.request.variant_id,
                attempt_id=self.request.attempt_id,
                capability_id=tool_name,
                resource_id=payload["source_id"],
                action_id=tool_name,
                entitlement_snapshot_hash=(
                    self.request.execution_authorization_binding_sha256
                ),
                idempotency_key=f"claude_gateway_{self.request.attempt_id}",
                expires_after_seconds=30,
            )

        def invoke(self, tool_name, payload, authorization):
            events.append("resource")
            authorization.validate()
            request_artifact = artifact_host.put_bytes(
                artifact_kind_id="gateway_tool_request",
                schema_version="gateway_tool_request_v1",
                schema_ref="schema:gateway_tool_request@v1",
                schema_sha256="7" * 64,
                media_type="application/json",
                content=json.dumps(payload, sort_keys=True).encode("utf-8"),
                idempotency_key=f"claude_request_{self.request.attempt_id}",
            )
            response = {"value": "authorized_source"}
            response_artifact = artifact_host.put_bytes(
                artifact_kind_id="gateway_tool_response",
                schema_version="gateway_tool_response_v1",
                schema_ref="schema:gateway_tool_response@v1",
                schema_sha256="8" * 64,
                media_type="application/json",
                content=json.dumps(response, sort_keys=True).encode("utf-8"),
                idempotency_key=f"claude_response_{self.request.attempt_id}",
            )
            self._observations.append(
                ModuleToolCallObservation(
                    tool_call_id="claude_gateway_call_001",
                    tool_name=tool_name,
                    request_ref=request_artifact.artifact_ref,
                    request_sha256=request_artifact.artifact_sha256,
                    response_ref=response_artifact.artifact_ref,
                    response_sha256=response_artifact.artifact_sha256,
                )
            )
            return response

        def validate_completion(self) -> None:
            assert len(self._observations) == 1

        @property
        def observations(self):
            return tuple(self._observations)

    class ToolSessionFactory:
        def open_session(self, request):
            return ToolSession(request)

    async def fake_query(*, prompt, options):
        async for _message in prompt:
            pass
        server = next(iter(options.mcp_servers.values()))
        tool = server["tools"][0]
        try:
            tool_result = await tool.handler({"source_id": "source_001"})
        except PermissionError:
            assert deny_tool is True
        else:
            assert deny_tool is False
            assert json.loads(tool_result["content"][0]["text"]) == {
                "value": "authorized_source"
            }
        yield claude_module.ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=2,
            session_id="session_gateway_test",
            usage={"input_tokens": 5, "output_tokens": 3},
            structured_output={"value": "gateway_complete"},
        )

    executor = claude_module.ClaudeAgentSdkGatewayModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        tool_session_factory=ToolSessionFactory(),
        workspace_root=tmp_path / "workspaces",
        query_fn=fake_query,
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    prompt_ref = _evaluation_prompt(
        artifact_host,
        compiled,
        suffix="claude_gateway",
    )
    request = _evaluation_request(
        compiled,
        prompt_ref,
        suffix="claude_gateway",
    )
    authority, product = _evaluation_authority(registry, request)
    if deny_tool:
        product.operation_effects["read_source"] = GatewayDecisionEffect.DENY

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
        clock=lambda: _TEST_TIME,
    )

    assert events == (["intent"] if deny_tool else ["intent", "resource"])
    assert tuple(query.operation_id for query in product.operation_queries) == (
        "invoke_model",
        "read_source",
    )
    if deny_tool:
        assert run.attempts[0].status == "failed"
        assert run.attempts[0].failure_class == "authorization"
        assert run.outputs == ()
        assert run.resolution is None
    else:
        assert _assert_completed_provider_run(run, artifact_host) == {
            "value": "gateway_complete"
        }
        assert run.attempts[0].tool_calls[0].tool_name == "read_source"


@pytest.mark.skipif(
    not _RUN_PROVIDER_INTEGRATION,
    reason="set RUN_PROVIDER_INTEGRATION=1 for live Provider smoke tests",
)
def test_live_codex_evaluation_runs_through_run_module(tmp_path: Path) -> None:
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        model_id=os.environ.get("AGENT_RUNTIME_TEST_CODEX_MODEL", "gpt-5.6-sol"),
        reasoning_profile="low",
        timeout_seconds=300,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="codex_live")
    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(compiled, prompt_ref, suffix="codex_live")
    authority, _ = _evaluation_authority(
        registry,
        request,
        observed_at_utc=_TEST_TIME,
    )

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
    )

    output = _assert_completed_provider_run(run, artifact_host)
    assert isinstance(output["value"], str)
    assert run.attempts[0].usage.input_tokens is not None
    assert run.attempts[0].usage.output_tokens is not None
    _emit_live_provider_evidence(run, compiled)


@pytest.mark.skipif(
    not _RUN_PROVIDER_INTEGRATION,
    reason="set RUN_PROVIDER_INTEGRATION=1 for live Provider smoke tests",
)
def test_live_claude_evaluation_runs_through_run_module(tmp_path: Path) -> None:
    claude_module = pytest.importorskip(
        "agent_runtime.invocation.invocation_claude_module_invocation"
    )
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="native_claude_profile",
        executor_adapter_id="claude_agent_sdk_inline_executor",
        executor_adapter_revision="v1",
        transport_kind="claude_agent_sdk",
        provider_id="anthropic",
        model_id=os.environ.get(
            "AGENT_RUNTIME_TEST_CLAUDE_MODEL",
            "claude-fable-5",
        ),
        reasoning_profile="low",
        timeout_seconds=300,
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="claude_live")
    executor = claude_module.ClaudeAgentSdkInlineModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(compiled, prompt_ref, suffix="claude_live")
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
    )

    output = _assert_completed_provider_run(run, artifact_host)
    assert isinstance(output["value"], str)
    assert run.attempts[0].usage.output_tokens is not None
    _emit_live_provider_evidence(run, compiled)


@pytest.mark.skipif(
    not _RUN_PROVIDER_INTEGRATION,
    reason="set RUN_PROVIDER_INTEGRATION=1 for live Provider smoke tests",
)
def test_live_claude_agent_workspace_runs_through_run_module(
    tmp_path: Path,
) -> None:
    claude_module = pytest.importorskip(
        "agent_runtime.invocation.invocation_claude_module_invocation"
    )
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="live_claude_workspace_profile",
        executor_adapter_id=(
            "claude_agent_sdk_inline_draft_workspace_executor"
        ),
        executor_adapter_revision="v1",
        transport_kind="claude_agent_sdk",
        provider_id="anthropic",
        model_id=os.environ.get(
            "AGENT_RUNTIME_TEST_CLAUDE_MODEL",
            "claude-fable-5",
        ),
        reasoning_profile="low",
        timeout_seconds=300,
        execution_mode="agent",
        attempt_workspace_policy="own_draft_read_write",
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    challenge = "workspace_challenge_9c51b7"

    async def live_query(*, prompt, options):
        (Path(options.cwd) / "workspace-challenge.txt").write_text(
            challenge,
            encoding="utf-8",
        )
        async for message in claude_module.query(prompt=prompt, options=options):
            yield message

    prompt_ref = _evaluation_prompt(
        artifact_host,
        compiled,
        suffix="claude_workspace_live",
        execution_specific_instructions=(
            "MANDATORY TOOL EVIDENCE: call Read with file_path exactly "
            '"workspace-challenge.txt". Its contents are not in this prompt. '
            "Then call Write with file_path exactly "
            '"workspace-proof.txt" to copy those exact contents, and call Read '
            "again with that exact relative path to verify the copy. Do not use "
            "a leading slash, tilde, parent path, or any other tool. Return the "
            "required JSON object only afterward, with value set to the exact "
            "challenge contents."
        ),
    )
    executor = claude_module.ClaudeAgentSdkInlineDraftWorkspaceModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        query_fn=live_query,
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(
        compiled,
        prompt_ref,
        suffix="claude_workspace_live",
    )
    authority, _ = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
    )

    output = _assert_completed_provider_run(run, artifact_host)
    assert output["value"] == challenge
    workspace_proofs = tuple(
        (tmp_path / "workspaces").glob("*/workspace-proof.txt")
    )
    assert len(workspace_proofs) == 1
    assert workspace_proofs[0].read_text(encoding="utf-8").strip() == (
        challenge
    )
    _emit_live_provider_evidence(run, compiled)


@pytest.mark.skipif(
    not _RUN_PROVIDER_INTEGRATION,
    reason="set RUN_PROVIDER_INTEGRATION=1 for live Provider smoke tests",
)
def test_live_claude_gateway_read_runs_through_run_module(
    tmp_path: Path,
) -> None:
    claude_module = pytest.importorskip(
        "agent_runtime.invocation.invocation_claude_module_invocation"
    )
    compiled = _compile_native_module(
        tmp_path,
        declared_operation_ids=("invoke_model", "read_source"),
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="live_claude_gateway_profile",
        executor_adapter_id="claude_agent_sdk_gateway_executor",
        executor_adapter_revision="v2",
        transport_kind="claude_agent_sdk",
        provider_id="anthropic",
        model_id=os.environ.get(
            "AGENT_RUNTIME_TEST_CLAUDE_MODEL",
            "claude-fable-5",
        ),
        reasoning_profile="low",
        timeout_seconds=300,
        execution_mode="agent",
        semantic_input_delivery_mode="gateway_read",
        gateway_access_reasons=("oversized_knowledge_retrieval",),
        tool_policy=("read_source",),
        network_policy="gateway_only",
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    tool_sessions = _ProviderIntegrationToolSessionFactory(artifact_host)
    prompt_ref = _evaluation_prompt(
        artifact_host,
        compiled,
        suffix="claude_gateway_live",
        execution_specific_instructions=(
            "Call read_source exactly once with source_id source_001. Use the "
            "returned fact as the value in the required JSON object."
        ),
    )
    executor = claude_module.ClaudeAgentSdkGatewayModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        tool_session_factory=tool_sessions,
        workspace_root=tmp_path / "workspaces",
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(executor)
    request = _evaluation_request(
        compiled,
        prompt_ref,
        suffix="claude_gateway_live",
    )
    authority, product = _evaluation_authority(registry, request)

    run = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=authority,
    )

    output = _assert_completed_provider_run(run, artifact_host)
    assert output["value"] == "runtime_gateway_authorized"
    assert len(tool_sessions.sessions) == 1
    assert len(tool_sessions.sessions[0].observations) == 1
    assert tuple(query.operation_id for query in product.operation_queries) == (
        "invoke_model",
        "read_source",
    )
    assert run.attempts[0].tool_calls == tool_sessions.sessions[0].observations
    _emit_live_provider_evidence(run, compiled)


def _direct_adapter_request(
    compiled,
    prompt_ref,
    *,
    suffix: str,
) -> AuthorizedAgentExecutionRequest:
    return AuthorizedAgentExecutionRequest.build(
        workflow_execution_id=None,
        isolated_scope_ref=f"scope-ref:native-{suffix}",
        isolated_scope_sha256="3" * 64,
        module_run_id=f"module_run_{suffix}",
        variant_id=f"variant_{suffix}",
        attempt_id=f"attempt_{suffix}",
        module_id=compiled.module.module_id,
        module_release_ref=compiled.module.release_ref,
        module_release_sha256=compiled.module.release_sha256,
        execution_profile_id=compiled.execution_profile.execution_profile_id,
        execution_profile_ref=compiled.execution_profile.release_ref,
        execution_profile_sha256=compiled.execution_profile.release_sha256,
        attempt_begin_receipt_ref=f"attempt-begin:attempt_{suffix}",
        attempt_begin_receipt_sha256="4" * 64,
        prompt_envelope_ref=prompt_ref.artifact_ref,
        prompt_envelope_sha256=prompt_ref.artifact_sha256,
        output_schema_ref=compiled.module.output_schema_ref,
        output_schema_sha256=compiled.module.output_schema_sha256,
        execution_authorization_binding_ref=(
            "runtime-authorization:binding_direct_test"
        ),
        execution_authorization_binding_sha256="5" * 64,
        protected_operation_intent_ref="runtime-authorization:intent_direct_test",
        protected_operation_intent_sha256="6" * 64,
        product_operation_decision_ref="product-decision:direct_test",
        product_operation_decision_sha256="7" * 64,
        gateway_authorization_observation_ref=(
            "runtime-authorization:observation_direct_test"
        ),
        gateway_authorization_observation_sha256="8" * 64,
        operation_grant_ref=None,
        operation_grant_sha256=None,
        grant_disposition_ref=None,
        input_closure_sha256=hashlib.sha256(b"[]").hexdigest(),
        data_use_purpose_id="module_evaluation_execution",
        authorized_inputs=(),
        idempotency_key=f"idempotency_direct_{suffix}",
    )


class _RecordingHost:
    """Minimal canonical host double for direct adapter tests."""

    def __init__(self) -> None:
        self.staged: dict[str, bytes] = {}

    def read_authorized_input(self, local_handle: str) -> bytes:
        raise PermissionError(f"no authorized input: {local_handle}")

    def stage_output_bytes(self, submission, content: bytes) -> None:
        submission.validate()
        self.staged[submission.output_slot_id] = content

    def authorize_operation(self, request):
        raise PermissionError("dynamic operation authorization is not available")


def test_claude_tool_free_executor_honors_configured_turn_budget(
    tmp_path: Path,
) -> None:
    claude_module = pytest.importorskip(
        "agent_runtime.invocation.invocation_claude_module_invocation"
    )
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="native_claude_profile",
        executor_adapter_id="claude_agent_sdk_inline_executor",
        executor_adapter_revision="v1",
        transport_kind="claude_agent_sdk",
        provider_id="anthropic",
        model_id="claude-opus-test",
        reasoning_profile="xhigh",
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(
        artifact_host,
        compiled,
        suffix="claude_turn_budget",
    )
    observed: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        observed["max_turns"] = options.max_turns
        async for _message in prompt:
            pass
        yield claude_module.ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=2,
            session_id="session_test",
            usage={"input_tokens": 3, "output_tokens": 2},
            structured_output={"value": "completed"},
        )

    executor = claude_module.ClaudeAgentSdkInlineModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        query_fn=fake_query,
        max_turns=3,
    )
    host = _RecordingHost()
    result = executor.execute(
        _direct_adapter_request(
            compiled,
            prompt_ref,
            suffix="claude_turn_budget",
        ),
        host,
    )

    assert observed["max_turns"] == 3
    assert result.terminal_status == "completed"
    assert json.loads(host.staged["result"]) == {"value": "completed"}


@pytest.mark.parametrize("escape_workspace", [False, True])
def test_claude_workspace_executor_gates_every_tool_with_pre_hook(
    tmp_path: Path,
    escape_workspace: bool,
) -> None:
    claude_module = pytest.importorskip(
        "agent_runtime.invocation.invocation_claude_module_invocation"
    )
    compiled = _compile_native_module(
        tmp_path,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="claude_workspace_profile",
        executor_adapter_id=(
            "claude_agent_sdk_inline_draft_workspace_executor"
        ),
        executor_adapter_revision="v1",
        transport_kind="claude_agent_sdk",
        provider_id="anthropic",
        model_id="claude-workspace-test",
        execution_mode="agent",
        attempt_workspace_policy="own_draft_read_write",
    )
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(
        artifact_host,
        compiled,
        suffix="claude_workspace_control",
    )
    observed: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        observed["tools"] = tuple(options.tools)
        async for _message in prompt:
            pass
        matcher = options.hooks["PreToolUse"][0]
        observed["matcher"] = matcher.matcher
        hook = matcher.hooks[0]
        decision = await hook(
            {
                "tool_name": "Read",
                "tool_input": {
                    "file_path": str(
                        tmp_path.parent / "escaped.txt"
                        if escape_workspace
                        else Path(options.cwd) / "draft.txt"
                    )
                },
            },
            None,
            {},
        )
        observed["decision"] = decision
        yield claude_module.ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="session_workspace_control",
            usage={"input_tokens": 3, "output_tokens": 2},
            structured_output={"value": "completed"},
        )

    executor = claude_module.ClaudeAgentSdkInlineDraftWorkspaceModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        query_fn=fake_query,
    )
    result = executor.execute(
        _direct_adapter_request(
            compiled,
            prompt_ref,
            suffix="claude_workspace_control",
        ),
        _RecordingHost(),
    )

    assert observed["tools"] == ("Read", "Write", "Edit")
    assert observed["matcher"] == "^(?:Read|Write|Edit)$"
    matcher = re.compile(observed["matcher"])
    assert matcher.fullmatch("Read") is not None
    assert matcher.fullmatch("TodoWrite") is None
    hook_output = observed["decision"]["hookSpecificOutput"]
    if escape_workspace:
        assert hook_output["permissionDecision"] == "deny"
        assert result.terminal_status == "failed"
        assert result.failure.failure_class == "policy_violation"
    else:
        assert hook_output["permissionDecision"] == "allow"
        assert result.terminal_status == "completed"


def test_claude_profile_refusal_trace_is_bounded() -> None:
    claude_module = pytest.importorskip(
        "agent_runtime.invocation.invocation_claude_module_invocation"
    )

    refusal = claude_module._bounded_profile_refusal(
        tool_name="T" * 10_000,
        reason="R" * 10_000,
        path="/" + "P" * 10_000,
    )

    assert len(refusal["tool_name"]) == 128
    assert len(refusal["reason"]) == 1024
    assert len(refusal["path"]) == 2048

    refusals: list[dict[str, str]] = []
    for ordinal in range(12):
        claude_module._record_profile_refusal(
            refusals,
            tool_name=f"Tool{ordinal}",
            reason="denied",
            path=f"path-{ordinal}",
        )

    assert len(refusals) == 8
    assert refusals[-1]["tool_name"] == "Tool7"


def test_codex_native_structured_output_executes_end_to_end(
    tmp_path: Path,
    monkeypatch,
) -> None:
    compiled = _compile_native_module(tmp_path)
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=compiled.schema_assets,
            prompt_components=compiled.prompt_components,
            prompt_bundles=(compiled.prompt_bundle,),
            behavior_policies=(compiled.behavior_policy,),
            evaluation_policies=(compiled.evaluation_policy,),
            retry_policies=(compiled.retry_policy,),
            execution_profiles=(compiled.execution_profile,),
            modules=(compiled.module,),
        )
    )
    artifact_host = InMemoryCellArtifactStore()
    envelope = build_inline_provider_prompt(
        compiled_static_body=compiled.prompt_bundle.compiled_static_body,
        execution_specific_instructions="",
        inputs=(),
        output_constraint_mode=NATIVE_STRUCTURED_OUTPUT,
    )
    prompt_ref = artifact_host.put_bytes(
        artifact_kind_id="prompt_envelope",
        schema_version="prompt_envelope_v1",
        schema_ref="schema:prompt_envelope@v1",
        schema_sha256="1" * 64,
        media_type="text/plain",
        content=envelope.encode("utf-8"),
        idempotency_key="prompt_envelope_native",
    )
    captured: dict[str, object] = {}
    lease_events: list[str] = []

    @contextmanager
    def tracked_lease(workspace: Path):
        lease_events.append(f"enter:{workspace.name}")
        try:
            yield workspace
        finally:
            lease_events.append(f"exit:{workspace.name}")

    monkeypatch.setattr(codex_module, "lease_attempt_workspace", tracked_lease)

    def invoker(
        *,
        argv: list[str],
        prompt: str,
        cwd: Path,
        timeout_seconds: int,
    ) -> CodexCliInvocationResult:
        assert lease_events == ["enter:attempt_native_001"]
        captured["argv"] = list(argv)
        captured["prompt"] = prompt
        schema_path = Path(argv[argv.index("--output-schema") + 1])
        captured["schema"] = json.loads(
            schema_path.read_text(encoding="utf-8")
        )
        provider_payload = {"value": "ok", "note": None}
        stdout = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": json.dumps(provider_payload),
                },
            }
        )
        return CodexCliInvocationResult(returncode=0, stdout=stdout, stderr="")

    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=invoker,
        codex_bin="codex-test-stub",
    )
    host = _RecordingHost()
    result = executor.execute(
        _direct_adapter_request(compiled, prompt_ref, suffix="native_001"),
        host,
    )

    assert OUTPUT_SCHEMA_MARKER not in str(captured["prompt"])
    registry_canonical_schema = json.loads(
        json.dumps(_OUTPUT_SCHEMA, sort_keys=True)
    )
    assert captured["schema"] == codex_native_output_schema(
        task_plane_output_schema(registry_canonical_schema)
    )
    canonical = json.dumps(
        {"value": "ok"},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert result.terminal_status == "completed"
    assert result.outputs[0].output_slot_id == "result"
    assert host.staged["result"] == canonical
    assert result.provider_id == compiled.execution_profile.provider_id
    assert result.cell_local_trace_ref.startswith("cell-artifact:")
    assert lease_events == [
        "enter:attempt_native_001",
        "exit:attempt_native_001",
    ]

    @contextmanager
    def conflicting_lease(_workspace: Path):
        raise AttemptWorkspaceConflictError("live duplicate invocation")
        yield  # pragma: no cover

    monkeypatch.setattr(codex_module, "lease_attempt_workspace", conflicting_lease)
    conflicted = executor.execute(
        _direct_adapter_request(compiled, prompt_ref, suffix="native_002"),
        _RecordingHost(),
    )
    assert conflicted.terminal_status == "failed"
    assert conflicted.failure is not None
    assert conflicted.failure.failure_class == "dependency_unavailable"
    assert conflicted.failure.retry_disposition_id == "retry_denied"
    detail = json.loads(
        artifact_host.read_bytes(
            conflicted.failure.detail_ref,
            conflicted.failure.detail_sha256,
        )
    )
    assert detail["retryable"] is False
    assert detail["failure_code"] == "codex_attempt_workspace_unavailable"


def test_direct_adapter_refuses_model_invocation_without_evidence(
    tmp_path: Path,
) -> None:
    compiled = _compile_native_module(tmp_path)
    registry = _register_compiled_for_evaluation(compiled)
    artifact_host = InMemoryCellArtifactStore()
    prompt_ref = _evaluation_prompt(artifact_host, compiled, suffix="no_evidence")
    executor = CodexCliModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        invoker=lambda **_fields: pytest.fail("Provider must not be reached"),
        codex_bin="codex-test-stub",
    )
    granted = _direct_adapter_request(compiled, prompt_ref, suffix="no_evidence")
    fields = {
        name: getattr(granted, name)
        for name in granted.__dataclass_fields__
        if name != "request_sha256"
    }
    fields.update(
        execution_authorization_binding_ref=None,
        execution_authorization_binding_sha256=None,
        protected_operation_intent_ref=None,
        protected_operation_intent_sha256=None,
        product_operation_decision_ref=None,
        product_operation_decision_sha256=None,
        gateway_authorization_observation_ref=None,
        gateway_authorization_observation_sha256=None,
    )
    request = AuthorizedAgentExecutionRequest.build(**fields)

    with pytest.raises(PermissionError, match="operation authorization evidence"):
        executor.execute(request, _RecordingHost())


def test_adapters_no_longer_reference_the_removed_prompt_bundle_local() -> None:
    invocation_root = (
        Path(__file__).resolve().parent.parent
        / "src"
        / "agent_runtime"
        / "invocation"
    )
    for module_name in (
        "invocation_codex_module_invocation",
        "invocation_claude_module_invocation",
    ):
        tree = ast.parse(
            (invocation_root / f"{module_name}.py").read_text(
                encoding="utf-8"
            )
        )
        loaded_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        assert "prompt_bundle" not in loaded_names, module_name


def test_execution_profile_excludes_context_and_retry_semantics() -> None:
    profile = compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
            execution_profile_id="profile_bounded_attempts",
            executor_adapter_id="claude_agent_sdk",
            executor_adapter_revision="v1",
            transport_kind="in_process",
            provider_id="anthropic",
            model_id="model_stub",
            reasoning_profile="none",
            execution_mode="tool_free",
            semantic_input_delivery_mode="inline",
            attempt_workspace_policy="none",
            gateway_access_reasons=(),
            output_constraint_mode="prompt_only_json",
            tool_policy=(),
            network_policy="denied",
            timeout_seconds=60,
            release_version="v1",
        )
    )
    payload = profile.as_dict()

    assert "context_policy_ref" not in payload
    assert "context_policy_sha256" not in payload
    assert "max_attempts" not in payload

    retry = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="bounded_attempts",
            policy_version="v1",
            max_attempts=3,
        )
    )
    assert retry.policy_document()["max_attempts"] == 3
