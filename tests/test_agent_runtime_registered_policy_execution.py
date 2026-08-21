from __future__ import annotations

from dataclasses import fields, replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from agent_runtime.contracts import ModuleOutcome, ModuleOutcomeDisposition
from agent_runtime.contracts.execution_authorization_definition import (
    ExecutionAuthorizationContextEnvelope,
    ExecutionAuthorizationContextState,
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
    ModuleExecutionVariantRecord,
)
from agent_runtime.contracts.ledger_record_definition import (
    CheckpointRecord,
    ExecutionInputRef,
    LegacyModuleCapabilityGrant,
    OutcomeCommitBatch,
    RuntimeRecordBatch,
    WorkflowAttemptRecord,
    WorkflowAttemptStartedRecord,
    WorkflowExecutionRecord,
    WorkflowModuleExecutionVariantRecord,
    WorkflowModuleRunRecord,
)
from agent_runtime.contracts.registry_release_definition import (
    ExecutionProfileRelease,
    ModuleEntryPolicy,
    ModuleExecutionPurpose,
    ModuleKind,
    ReleaseAdmissionIntent,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
    RuntimeModuleRelease,
    SchemaAssetRelease,
)
from agent_runtime.execution.execution_content_staging import (
    InMemoryCellArtifactStore,
)
from agent_runtime.execution.execution_authorization_coordination import (
    ExecutionAuthorizationController,
    InMemoryExecutionAuthorizationLedger,
)
from agent_runtime.execution import execution_module_invocation as execution
from agent_runtime.execution.execution_module_invocation import (
    AgentExecutionAdapterRegistry,
    ModuleExecutionAuthority,
    run_module,
    run_workflow_module,
)
from agent_runtime.foundation.foundation_retry_policy_validation import (
    RETRY_POLICY_MAX_ATTEMPTS,
    validate_retry_attempt_budget,
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
    RetryPolicyReleaseCandidate,
    candidate_admission_intent,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_retry_policy_release,
    runtime_owned_policy_schema_assets,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
    allowed_release_admission_states,
)


_TIME = "2026-08-18T12:00:00Z"
_ENTITLEMENT = "e" * 64


def _subject(record: object) -> tuple[ReleaseSubjectKind, str]:
    if record.__class__.__name__ == "BehaviorPolicyRelease":
        return ReleaseSubjectKind.BEHAVIOR_POLICY, record.policy_id
    if record.__class__.__name__ == "EvaluationPolicyRelease":
        return ReleaseSubjectKind.EVALUATION_POLICY, record.policy_id
    if record.__class__.__name__ == "RetryPolicyRelease":
        return ReleaseSubjectKind.RETRY_POLICY, record.policy_id
    if isinstance(record, ExecutionProfileRelease):
        return ReleaseSubjectKind.EXECUTION_PROFILE, record.execution_profile_id
    if isinstance(record, RuntimeModuleRelease):
        return ReleaseSubjectKind.RUNTIME_MODULE, record.module_id
    if isinstance(record, SchemaAssetRelease):
        return ReleaseSubjectKind.SCHEMA_ASSET, record.schema_asset_id
    raise TypeError(type(record).__name__)


def _transition(
    registry: RuntimeReleaseRegistry,
    record: object,
    state: ReleaseAdmissionState,
    *,
    sequence: int,
) -> None:
    kind, subject_id = _subject(record)
    registry.register_bundle(
        RuntimeReleaseBundle(
            admission_intents=(
                ReleaseAdmissionIntent.build(
                    admission_id=(
                        f"admission_{kind.value}_{subject_id}_{state.value}_{sequence}"
                    ),
                    subject_kind=kind,
                    subject_id=subject_id,
                    release_ref=record.release_ref,
                    release_sha256=record.release_sha256,
                    state=state,
                    evidence_members=(),
                ),
            )
        )
    )


def _advance_to(
    registry: RuntimeReleaseRegistry,
    record: object,
    state: ReleaseAdmissionState,
) -> None:
    if state is ReleaseAdmissionState.CANDIDATE:
        return
    if state is ReleaseAdmissionState.SHADOW_EXECUTABLE:
        _transition(registry, record, state, sequence=1)
        return
    if state is ReleaseAdmissionState.PRODUCTION_CANARY:
        _transition(
            registry,
            record,
            ReleaseAdmissionState.SHADOW_EXECUTABLE,
            sequence=1,
        )
        _transition(registry, record, state, sequence=2)
        return
    if state is ReleaseAdmissionState.ACTIVE:
        _transition(registry, record, state, sequence=1)
        return
    if state is ReleaseAdmissionState.SUPERSEDED:
        _transition(
            registry,
            record,
            ReleaseAdmissionState.ACTIVE,
            sequence=1,
        )
        _transition(registry, record, state, sequence=2)
        return
    if state is ReleaseAdmissionState.RETIRED:
        _transition(registry, record, state, sequence=1)
        return
    raise AssertionError(state)


def _release_set(
    *,
    max_attempts: int = 3,
    entry_policy: ModuleEntryPolicy = ModuleEntryPolicy.STANDALONE_ALLOWED,
) -> SimpleNamespace:
    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    evaluation = compile_evaluation_policy_release(
        EvaluationPolicyReleaseCandidate(
            policy_id="registered_policy_test",
            policy_version="v1",
            evaluation_mode="deterministic_candidate",
        )
    )
    retry = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="registered_policy_test",
            policy_version="v1",
            max_attempts=max_attempts,
        )
    )
    input_schema = SchemaAssetRelease.build(
        schema_asset_id="registered_policy_input",
        schema_asset_version="v1",
        release_ref="schema:registered_policy_input@v1",
        schema_document={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "schema:registered_policy_input@v1",
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    output_schema = SchemaAssetRelease.build(
        schema_asset_id="registered_policy_output",
        schema_asset_version="v1",
        release_ref="schema:registered_policy_output@v1",
        schema_document={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "schema:registered_policy_output@v1",
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    profile = ExecutionProfileRelease.build(
        execution_profile_id="registered_policy_profile",
        execution_profile_version="v1",
        release_ref="execution-profile:registered-policy-profile@v1",
        executor_adapter_id="registered_policy_test_executor",
        executor_adapter_revision="v1",
        transport_kind="in_process_test",
        provider_id="provider_stub",
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
    )
    module = RuntimeModuleRelease.build(
        module_id="registered_policy_module",
        module_version="v1",
        release_ref="runtime-module:registered-policy-module@v1",
        module_kind=ModuleKind.DETERMINISTIC,
        owner_contract_ref="contract:registered-policy-module@v1",
        owner_contract_sha256="a" * 64,
        executable_ref="callable:registered-policy-module@v1",
        executable_sha256="b" * 64,
        input_schema_ref=input_schema.release_ref,
        input_schema_sha256=input_schema.schema_sha256,
        output_schema_ref=output_schema.release_ref,
        output_schema_sha256=output_schema.schema_sha256,
        prompt_bundle_ref=None,
        prompt_bundle_sha256=None,
        declared_operation_ids=(),
        behavior_policy_ref=behavior.release_ref,
        behavior_policy_sha256=behavior.release_sha256,
        evaluation_policy_ref=evaluation.release_ref,
        evaluation_policy_sha256=evaluation.release_sha256,
        retry_policy_ref=retry.release_ref,
        retry_policy_sha256=retry.release_sha256,
        compatible_transport_kinds=("in_process_test",),
        entry_policy=entry_policy,
        output_resolution_policy=execution.OutputResolutionPolicy.DIRECT_SINGLE,
    )
    return SimpleNamespace(
        behavior=behavior,
        evaluation=evaluation,
        retry=retry,
        input_schema=input_schema,
        output_schema=output_schema,
        profile=profile,
        module=module,
    )


def _agent_release_set() -> SimpleNamespace:
    base = _release_set()
    compiled = compile_agent_module_release(
        AgentModuleReleaseCandidate(
            module_id="registered_policy_agent",
            module_version="v1",
            owner_contract_ref="contract:registered-policy-agent@v1",
            owner_contract_content="# Registered Policy Agent\n",
            input_schema_ref=base.input_schema.release_ref,
            input_schema_document=base.input_schema.canonical_schema_json,
            output_schema_ref=base.output_schema.release_ref,
            output_schema_document=base.output_schema.canonical_schema_json,
            instruction_source_ref="host-source:registered-policy-agent@v1",
            instruction_text="Return one JSON object.",
            declared_operation_ids=("invoke_model",),
            compatible_transport_kinds=("in_process_test",),
            behavior_policy_ref=base.behavior.release_ref,
            behavior_policy_sha256=base.behavior.release_sha256,
            evaluation_policy_ref=base.evaluation.release_ref,
            evaluation_policy_sha256=base.evaluation.release_sha256,
            retry_policy_ref=base.retry.release_ref,
            retry_policy_sha256=base.retry.release_sha256,
            entry_policy=ModuleEntryPolicy.WORKFLOW_BOUND,
            output_resolution_policy=execution.OutputResolutionPolicy.DIRECT_SINGLE,
        )
    )
    return SimpleNamespace(
        behavior=base.behavior,
        evaluation=base.evaluation,
        retry=base.retry,
        input_schema=compiled.schema_assets[0],
        output_schema=compiled.schema_assets[1],
        profile=base.profile,
        module=compiled.module,
        prompt_components=compiled.prompt_components,
        prompt_bundle=compiled.prompt_bundle,
    )


def _registry(
    releases: SimpleNamespace,
    *,
    states: dict[str, ReleaseAdmissionState] | None = None,
) -> RuntimeReleaseRegistry:
    prompt_components = tuple(getattr(releases, "prompt_components", ()))
    prompt_bundles = (
        (releases.prompt_bundle,)
        if hasattr(releases, "prompt_bundle")
        else ()
    )
    registered = (
        releases.behavior,
        releases.evaluation,
        releases.retry,
        *prompt_components,
        *prompt_bundles,
        releases.profile,
        releases.module,
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(
                *runtime_owned_policy_schema_assets(),
                releases.input_schema,
                releases.output_schema,
            ),
            behavior_policies=(releases.behavior,),
            evaluation_policies=(releases.evaluation,),
            retry_policies=(releases.retry,),
            prompt_components=prompt_components,
            prompt_bundles=prompt_bundles,
            execution_profiles=(releases.profile,),
            modules=(releases.module,),
            admission_intents=tuple(
                candidate_admission_intent(record)
                for record in registered
            ),
        )
    )
    for name, state in (states or {}).items():
        _advance_to(registry, getattr(releases, name), state)
    return registry


class _Adapter:
    def __init__(
        self,
        registry: RuntimeReleaseRegistry,
        artifacts: InMemoryCellArtifactStore,
        *,
        terminal_status: str = "completed",
        on_execute=None,
        payload: bytes = b'{"value":"ok"}',
    ) -> None:
        self.registry = registry
        self.artifacts = artifacts
        self.terminal_status = terminal_status
        self.on_execute = on_execute
        self.payload = payload
        self.calls = 0

    @property
    def descriptor(self) -> AgentExecutionAdapterDescriptor:
        return AgentExecutionAdapterDescriptor(
            adapter_contract_version="v1",
            adapter_id="registered_policy_test_executor",
            adapter_revision="v1",
            provider_id="provider_stub",
            transport_family="in_process",
            transport_kind="in_process_test",
            runtime_package_id="agent_runtime_core",
            runtime_package_version="0.0.0",
            supported_context_modes=("stateless",),
            supported_output_constraint_modes=("prompt_only_json",),
            supported_read_isolation_modes=("entitled_refs",),
            supported_execution_modes=("tool_free",),
            supported_input_delivery_modes=("inline",),
            supported_network_policies=("denied",),
            supports_dynamic_operation_authorization=False,
            admission_state="test_double",
        )

    def execute(
        self,
        request: AuthorizedAgentExecutionRequest,
        host,
    ) -> AgentExecutionResult:
        self.calls += 1
        if self.on_execute is not None:
            self.on_execute(request, host)
        trace_ref, trace_sha256 = self.artifacts.commit_attempt_trace(
            module_run_id=request.module_run_id,
            variant_id=request.variant_id,
            attempt_id=request.attempt_id,
            content=b'{"transport":"in_process_test"}',
            media_type="application/json",
        )
        outputs: tuple[OutputSubmission, ...] = ()
        failure = None
        if self.terminal_status == "completed":
            submission = OutputSubmission(
                output_slot_id="result",
                local_handle="outputs/result.json",
            )
            host.stage_output_bytes(submission, self.payload)
            outputs = (submission,)
        else:
            failure = AgentExecutionFailure(
                failure_class="provider",
                retry_disposition_id="retry_allowed",
                failure_scope_id="attempt_only",
                retry_after_seconds=None,
                detail_ref=None,
                detail_sha256=None,
            )
        profile = self.registry.get_execution_profile(
            request.execution_profile_ref,
            request.execution_profile_sha256,
        )
        return AgentExecutionResult(
            terminal_status=self.terminal_status,
            provider_id=profile.provider_id,
            model_id=profile.model_id,
            runtime_version="0.0.0",
            outputs=outputs,
            model_operation_ref_ids=(),
            tool_operation_ref_ids=(),
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
        )


class _ContentStore:
    def __init__(self) -> None:
        self.staged: dict[str, object] = {}

    def stage_content(self, content):
        content.validate()
        self.staged[content.content_ref] = content
        return content

    def contains(self, output) -> bool:
        content = self.staged.get(output.output_ref)
        return (
            content is not None
            and content.content_sha256 == output.output_sha256
        )


def _input(
    releases: SimpleNamespace,
    artifacts: InMemoryCellArtifactStore,
    *,
    suffix: str,
) -> tuple[ModuleInputBinding, object]:
    content = json.dumps({"value": suffix}).encode("utf-8")
    ref = artifacts.put_bytes(
        artifact_kind_id="registered_policy_input",
        schema_version="v1",
        schema_ref=releases.input_schema.release_ref,
        schema_sha256=releases.input_schema.schema_sha256,
        media_type="application/json",
        content=content,
        idempotency_key=f"registered_policy_input_{suffix}",
        logical_name="task_input",
    )
    return (
        ModuleInputBinding(
            logical_name="task_input",
            input_ref=ref.artifact_ref,
            input_sha256=ref.artifact_sha256,
            schema_ref=releases.input_schema.release_ref,
            schema_sha256=releases.input_schema.schema_sha256,
            media_type="application/json",
        ),
        ref,
    )


def _variant(
    releases: SimpleNamespace,
    *,
    arm_key: str = "default",
    prompt_ref: object | None = None,
) -> ModuleVariantRequest:
    return ModuleVariantRequest(
        arm_key=arm_key,
        replicate_index=0,
        execution_profile_ref=releases.profile.release_ref,
        execution_profile_sha256=releases.profile.release_sha256,
        prompt_envelope_ref=(
            prompt_ref.artifact_ref if prompt_ref is not None else None
        ),
        prompt_envelope_sha256=(
            prompt_ref.artifact_sha256 if prompt_ref is not None else None
        ),
    )


def _isolated_request(
    releases: SimpleNamespace,
    input_binding: ModuleInputBinding,
    input_ref: object,
    *,
    purpose: ModuleExecutionPurpose = ModuleExecutionPurpose.TEST,
) -> ModuleExecutionRequest:
    return ModuleExecutionRequest.build(
        request_id="request_registered_policy_isolated",
        purpose=purpose,
        module_release_ref=releases.module.release_ref,
        module_release_sha256=releases.module.release_sha256,
        isolated_scope_ref="scope-ref:registered-policy-isolated",
        isolated_scope_sha256="c" * 64,
        input_package_ref=input_ref.artifact_ref,
        input_package_sha256=input_ref.artifact_sha256,
        inputs=(input_binding,),
        variants=(_variant(releases),),
        idempotency_key="idempotency_registered_policy_isolated",
    )


def _workflow_request(
    releases: SimpleNamespace,
    input_binding: ModuleInputBinding,
    input_ref: object,
    *,
    ordinal: int,
    parent_attempt_id: str | None,
    arm_key: str = "default",
    request_key: str | None = None,
    prompt_ref: object | None = None,
    module_run_id: str = "module_run_registered_policy",
) -> WorkflowModuleExecutionRequest:
    key = request_key or f"{ordinal}_{arm_key}"
    return WorkflowModuleExecutionRequest.build(
        request_id=f"request_registered_policy_workflow_{key}",
        purpose=ModuleExecutionPurpose.EVALUATION,
        workflow_execution_id="execution_registered_policy",
        dispatch_id=f"dispatch_registered_policy_{key}",
        workflow_node_id="registered_policy_node",
        module_run_id=module_run_id,
        module_release_ref=releases.module.release_ref,
        module_release_sha256=releases.module.release_sha256,
        input_package_ref=input_ref.artifact_ref,
        input_package_sha256=input_ref.artifact_sha256,
        inputs=(input_binding,),
        variants=(
            _variant(releases, arm_key=arm_key, prompt_ref=prompt_ref),
        ),
        idempotency_key=f"idempotency_registered_policy_{key}",
        attempt_ordinal=ordinal,
        parent_attempt_id=parent_attempt_id,
    )


def _workflow_recorder(
    input_binding: ModuleInputBinding,
    input_ref: object,
) -> tuple[WorkflowModuleLedgerRecorder, InMemoryRuntimeExecutionRecordStore]:
    content_store = _ContentStore()
    record_store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=content_store.contains
    )
    record_store.commit(
        RuntimeRecordBatch(
            workflow_execution_id="execution_registered_policy",
            transaction_id="transaction_registered_policy_bootstrap",
            records=(
                WorkflowExecutionRecord(
                    workflow_execution_id="execution_registered_policy",
                    workflow_id="workflow_registered_policy",
                    workflow_contract_version="v1",
                    tenant_id="tenant_test",
                    cell_id="cell_test",
                    principal_id="principal_test",
                    execution_release_ref="execution-release:registered-policy@v1",
                    graph_sha256="1" * 64,
                    runtime_execution_binding_ref=(
                        "runtime-binding:registered-policy@v1"
                    ),
                    runtime_execution_binding_sha256="2" * 64,
                    authorization_decision_ref="authorization:registered-policy@v1",
                    authorization_decision_sha256="3" * 64,
                    execution_principal_delegation_ref=(
                        "delegation:registered-policy@v1"
                    ),
                    execution_principal_delegation_sha256="4" * 64,
                    entitlement_snapshot_ref="entitlement:registered-policy@v1",
                    entitlement_snapshot_hash=_ENTITLEMENT,
                    execution_input_package_refs=(input_ref.artifact_ref,),
                    execution_input_package_sha256=input_ref.artifact_sha256,
                    recorded_at_utc=_TIME,
                ),
                ExecutionInputRef(
                    execution_input_id="execution_input_registered_policy",
                    workflow_execution_id="execution_registered_policy",
                    input_type_id="registered_policy_input",
                    schema_version="v1",
                    input_ref=input_binding.input_ref,
                    input_sha256=input_binding.input_sha256,
                    byte_size=len(json.dumps({"value": "workflow"}).encode("utf-8")),
                    media_type="application/json",
                    recorded_at_utc=_TIME,
                    logical_name="task_input",
                ),
            ),
        )
    )
    recorder = WorkflowModuleLedgerRecorder(
        WorkflowModuleLedgerBinding(
            record_store=record_store,
            entitlement_snapshot_hash=_ENTITLEMENT,
            claim_token_secret=b"registered-policy-test-secret-32bytes",
            content_store=content_store,
        )
    )
    return recorder, record_store


def _adapters(registry, artifacts, *, status="completed"):
    adapter = _Adapter(registry, artifacts, terminal_status=status)
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    return adapters, adapter


class _ProductAuthorizationDouble:
    def __init__(
        self,
        envelope: ExecutionAuthorizationContextEnvelope,
        *,
        record_store: InMemoryRuntimeExecutionRecordStore,
        authorization_ledger: InMemoryExecutionAuthorizationLedger,
        events: list[str],
    ) -> None:
        self.envelope = envelope
        self.record_store = record_store
        self.authorization_ledger = authorization_ledger
        self.events = events

    def validate_execution_context(
        self,
        context_id: str,
        tenant_id: str,
        observed_at_utc: str,
    ) -> ProductAuthorizationContextStatus:
        assert context_id == self.envelope.context_id
        assert tenant_id == self.envelope.tenant_id
        return ProductAuthorizationContextStatus.build(
            status_ref="product-status:registered-policy",
            context_id=self.envelope.context_id,
            context_ref=self.envelope.context_ref,
            context_sha256=self.envelope.context_sha256,
            state=ExecutionAuthorizationContextState.EFFECTIVE,
            reason_code="context_effective",
            observed_at_utc=observed_at_utc,
        )

    def authorize_operation(
        self,
        query: OperationAuthorizationQuery,
    ) -> ProductOperationDecision:
        starts = self.record_store.load_trace(
            self.envelope.workflow_execution_id
        ).records_of_type(WorkflowAttemptStartedRecord)
        assert len(starts) == 1, "Attempt start must precede Product authorization"
        intents = self.authorization_ledger.intents_for_execution(
            self.envelope.workflow_execution_id
        )
        assert len(intents) == 1, "protected intent must precede Product authorization"
        self.events.append("product_authorization")
        return ProductOperationDecision(
            query_id=query.query_id,
            query_sha256=query.query_sha256,
            decision_ref=f"product-decision:{query.query_id}",
            decision_sha256=hashlib.sha256(
                query.query_sha256.encode("utf-8")
            ).hexdigest(),
            effect=GatewayDecisionEffect.ALLOW,
            reason_code="operation_allowed",
            observed_at_utc=query.observed_at_utc,
        )


def _workflow_authority(
    registry: RuntimeReleaseRegistry,
    request: WorkflowModuleExecutionRequest,
    *,
    record_store: InMemoryRuntimeExecutionRecordStore,
    events: list[str],
) -> tuple[ModuleExecutionAuthority, InMemoryExecutionAuthorizationLedger]:
    envelope = ExecutionAuthorizationContextEnvelope.build(
        context_id="context_registered_policy",
        context_ref="product-context:registered-policy",
        workflow_execution_id=request.workflow_execution_id,
        workflow_release_id="workflow_registered_policy",
        principal_id="principal_test",
        actor_workload_id="workload_agent_runtime_test",
        tenant_id="tenant_test",
        cell_id="cell_test",
        authorization_decision_ref="product-decision:registered-policy",
        catalog_release_ref="catalog:registered-policy@v1",
        input_scope_refs=(request.input_package_ref,),
        effective_at_utc="2026-01-01T00:00:00Z",
        expiry_at_utc="2027-01-01T00:00:00Z",
    )
    authorization_ledger = InMemoryExecutionAuthorizationLedger()
    product = _ProductAuthorizationDouble(
        envelope,
        record_store=record_store,
        authorization_ledger=authorization_ledger,
        events=events,
    )
    controller = ExecutionAuthorizationController(
        client=product,
        ledger=authorization_ledger,
        module_release_client=registry,
    )
    admission = controller.bind_execution_context(
        envelope=envelope,
        expected_workflow_execution_id=request.workflow_execution_id,
        expected_workflow_release_id="workflow_registered_policy",
        expected_principal_id="principal_test",
        expected_actor_workload_id="workload_agent_runtime_test",
        expected_tenant_id="tenant_test",
        expected_cell_id="cell_test",
        execution_input_package_ref=request.input_package_ref,
        execution_input_package_sha256=request.input_package_sha256,
        observed_at_utc=_TIME,
    )
    return (
        ModuleExecutionAuthority(
            controller=controller,
            binding=admission.binding,
            authorization_client=product,
            enforcing_gateway_id="agent_runtime_test_gateway",
            environment_id="test",
        ),
        authorization_ledger,
    )


def test_exact_policy_resolution_and_hash_refusal() -> None:
    releases = _release_set()
    registry = _registry(releases)
    resolved = execution._resolve_and_admit_module_policies(
        registry,
        module=releases.module,
        purpose=ModuleExecutionPurpose.TEST,
    )
    assert resolved == (releases.behavior, releases.evaluation, releases.retry)

    wrong_hash = replace(releases.module, retry_policy_sha256="f" * 64)
    with pytest.raises(ValueError, match="hash mismatch"):
        execution._resolve_and_admit_module_policies(
            registry,
            module=wrong_hash,
            purpose=ModuleExecutionPurpose.TEST,
        )
    missing = replace(
        releases.module,
        evaluation_policy_ref="evaluation-policy:missing@v1",
    )
    with pytest.raises(KeyError, match="unknown Evaluation Policy"):
        execution._resolve_and_admit_module_policies(
            registry,
            module=missing,
            purpose=ModuleExecutionPurpose.TEST,
        )


@pytest.mark.parametrize("family", ("behavior", "evaluation", "retry"))
@pytest.mark.parametrize(
    ("purpose", "state", "allowed"),
    tuple(
        (purpose, state, state in allowed_release_admission_states(purpose))
        for purpose in ModuleExecutionPurpose
        for state in ReleaseAdmissionState
    ),
)
def test_policy_admission_matrix_is_exhaustive_per_family(
    family: str,
    purpose: ModuleExecutionPurpose,
    state: ReleaseAdmissionState,
    allowed: bool,
) -> None:
    releases = _release_set()
    baseline = (
        ReleaseAdmissionState.ACTIVE
        if purpose in {
            ModuleExecutionPurpose.WORKFLOW,
            ModuleExecutionPurpose.STANDALONE,
        }
        else ReleaseAdmissionState.CANDIDATE
    )
    policy_states = {
        name: (state if name == family else baseline)
        for name in ("behavior", "evaluation", "retry")
    }
    registry = _registry(releases, states=policy_states)
    if allowed:
        execution._resolve_and_admit_module_policies(
            registry,
            module=releases.module,
            purpose=purpose,
        )
    else:
        with pytest.raises(PermissionError, match=f"{family}_policy release"):
            execution._resolve_and_admit_module_policies(
                registry,
                module=releases.module,
                purpose=purpose,
            )


def test_retry_bound_matches_registered_schema() -> None:
    retry_schema = next(
        row
        for row in runtime_owned_policy_schema_assets()
        if row.schema_asset_id == "runtime_retry_policy"
    )
    schema = json.loads(retry_schema.canonical_schema_json)
    assert schema["properties"]["max_attempts"]["minimum"] == 1
    assert schema["properties"]["max_attempts"]["maximum"] == (
        RETRY_POLICY_MAX_ATTEMPTS
    )
    assert validate_retry_attempt_budget(RETRY_POLICY_MAX_ATTEMPTS) == 100
    for invalid in (True, 0, 101, "3"):
        with pytest.raises(ValueError, match="max_attempts"):
            validate_retry_attempt_budget(invalid)
        with pytest.raises(ValueError, match="max_attempts"):
            compile_retry_policy_release(
                RetryPolicyReleaseCandidate(
                    policy_id="invalid_retry_bound",
                    policy_version="v1",
                    max_attempts=invalid,
                )
            )


def test_contract_field_sets_preserve_retry_ownership() -> None:
    request_fields = {field.name for field in fields(WorkflowModuleExecutionRequest)}
    assert {"attempt_ordinal", "parent_attempt_id"}.issubset(request_fields)
    assert "max_attempts" in {
        field.name for field in fields(WorkflowModuleExecutionVariantRecord)
    }
    assert "max_attempts" not in {
        field.name for field in fields(ModuleExecutionVariantRecord)
    }
    profile_fields = {field.name for field in fields(ExecutionProfileRelease)}
    assert "max_attempts" not in profile_fields
    assert "context_policy_ref" not in profile_fields


def test_isolated_module_resolves_policies_and_runs_one_attempt() -> None:
    releases = _release_set()
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="isolated")
    adapters, adapter = _adapters(registry, artifacts)

    result = run_module(
        _isolated_request(releases, input_binding, input_ref),
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        clock=lambda: _TIME,
    )

    assert adapter.calls == 1
    assert len(result.attempts) == 1
    assert result.attempts[0].status == "completed"
    assert result.resolution is not None
    assert "max_attempts" not in result.variants[0].as_dict()


def test_workflow_retry_keeps_one_module_and_variant() -> None:
    releases = _release_set(max_attempts=2)
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="workflow")
    adapters, adapter = _adapters(registry, artifacts, status="failed")
    recorder, record_store = _workflow_recorder(input_binding, input_ref)

    first = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
    )
    first_result = run_workflow_module(
        first,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: _TIME,
    )
    adapter.terminal_status = "completed"
    second = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=2,
        parent_attempt_id=first_result.attempts[0].attempt_id,
    )
    second_result = run_workflow_module(
        second,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: "2026-08-18T12:00:01Z",
    )

    assert first_result.attempts[0].status == "failed"
    assert second_result.attempts[0].status == "completed"
    assert adapter.calls == 2
    trace = record_store.load_trace("execution_registered_policy")
    assert len(trace.records_of_type(WorkflowModuleRunRecord)) == 1
    assert len(trace.records_of_type(WorkflowModuleExecutionVariantRecord)) == 1
    starts = trace.records_of_type(WorkflowAttemptStartedRecord)
    assert [row.attempt_ordinal for row in starts] == [1, 2]
    terminals = trace.records_of_type(WorkflowAttemptRecord)
    assert terminals[1].parent_attempt_id == terminals[0].attempt_id


def test_replay_after_invocation_commit_creates_no_attempt_or_provider_call(
    monkeypatch,
) -> None:
    releases = _release_set()
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="replay")
    adapters, adapter = _adapters(registry, artifacts)
    recorder, record_store = _workflow_recorder(input_binding, input_ref)
    request = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
    )
    original = recorder.record_output_resolution
    calls = 0

    def crash_once(*, request, resolution):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated crash after invocation commit")
        return original(request=request, resolution=resolution)

    monkeypatch.setattr(recorder, "record_output_resolution", crash_once)
    with pytest.raises(RuntimeError, match="after invocation commit"):
        run_workflow_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: _TIME,
        )
    trace = record_store.load_trace("execution_registered_policy")
    assert adapter.calls == 1
    assert len(trace.records_of_type(WorkflowAttemptStartedRecord)) == 1

    replay = run_workflow_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: "2026-08-18T12:00:30Z",
    )
    assert replay.attempts[0].status == "completed"
    assert replay.resolution is not None
    assert adapter.calls == 1
    assert len(
        record_store.load_trace(
            "execution_registered_policy"
        ).records_of_type(WorkflowAttemptStartedRecord)
    ) == 1


def test_replay_after_outcome_before_backend_ack_creates_no_attempt() -> None:
    releases = _release_set()
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="outcome")
    adapters, adapter = _adapters(registry, artifacts)
    recorder, record_store = _workflow_recorder(input_binding, input_ref)
    request = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
    )
    first = run_workflow_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: _TIME,
    )
    outcome = ModuleOutcome.build(
        dispatch_id=request.dispatch_id,
        workflow_execution_id=request.workflow_execution_id,
        expected_state_id=request.workflow_node_id,
        disposition=ModuleOutcomeDisposition.TRANSITION,
        target_state_id="state_complete",
        module_run_id=request.module_run_id,
        attempt_ids=(first.attempts[0].attempt_id,),
        evidence_artifact_refs=tuple(
            output.output_ref for output in first.outputs
        ),
        outcome_ref="outcome-ref:registered-policy-complete",
    )
    checkpoint = CheckpointRecord(
        checkpoint_id="checkpoint_registered_policy_complete",
        workflow_execution_id=request.workflow_execution_id,
        dispatch_id=request.dispatch_id,
        execution_release_ref="execution-release:registered-policy@v1",
        graph_sha256="1" * 64,
        entitlement_snapshot_hash=_ENTITLEMENT,
        current_state_id="state_complete",
        runtime_status_id="completed",
        committed_outcome_ref=outcome.outcome_ref,
        committed_outcome_sha256=outcome.outcome_sha256,
        recorded_at_utc="2026-08-18T12:00:01Z",
    )
    record_store.commit_outcome(
        OutcomeCommitBatch(
            workflow_execution_id=request.workflow_execution_id,
            transaction_id="transaction_registered_policy_outcome",
            outcome=outcome,
            checkpoint=checkpoint,
        )
    )

    replay = run_workflow_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: "2026-08-18T12:00:02Z",
    )
    assert replay.attempts[0].attempt_id == first.attempts[0].attempt_id
    assert replay.attempts[0].status == "completed"
    assert replay.resolution == first.resolution
    assert adapter.calls == 1
    assert len(
        record_store.load_trace(
            request.workflow_execution_id
        ).records_of_type(WorkflowAttemptStartedRecord)
    ) == 1


def test_orphan_before_invocation_may_parent_one_bounded_retry() -> None:
    releases = _release_set(max_attempts=2)
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="orphan")
    recorder, record_store = _workflow_recorder(input_binding, input_ref)
    empty_adapters = AgentExecutionAdapterRegistry()
    first = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
    )
    with pytest.raises(KeyError, match="registered_policy_test_executor@v1"):
        run_workflow_module(
            first,
            release_registry=registry,
            adapters=empty_adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: _TIME,
        )
    starts = record_store.load_trace(
        "execution_registered_policy"
    ).records_of_type(WorkflowAttemptStartedRecord)
    assert len(starts) == 1
    assert record_store.get_committed_invocation(
        "execution_registered_policy",
        first.dispatch_id,
    ) is None

    adapters, adapter = _adapters(registry, artifacts)
    retry = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=2,
        parent_attempt_id=starts[0].attempt_id,
    )
    with pytest.raises(ValueError, match="parent is not terminal"):
        run_workflow_module(
            retry,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: "2026-08-18T12:00:30Z",
        )
    assert adapter.calls == 0

    recorder.recover_expired_attempt(
        workflow_execution_id="execution_registered_policy",
        attempt_id=starts[0].attempt_id,
        observed_at_utc="2026-08-18T12:01:00Z",
    )
    result = run_workflow_module(
        retry,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: "2026-08-18T12:01:01Z",
    )
    assert result.attempts[0].status == "completed"
    assert adapter.calls == 1
    trace = record_store.load_trace("execution_registered_policy")
    assert [
        row.attempt_ordinal
        for row in trace.records_of_type(WorkflowAttemptStartedRecord)
    ] == [1, 2]


def test_output_repair_consumes_the_same_retry_budget() -> None:
    releases = _release_set(max_attempts=2)
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="repair")
    adapter = _Adapter(
        registry,
        artifacts,
        payload=b'{"wrong":"shape"}',
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    recorder, record_store = _workflow_recorder(input_binding, input_ref)
    first = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
    )
    first_result = run_workflow_module(
        first,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: _TIME,
    )
    assert first_result.attempts[0].status == "failed"
    adapter.payload = b'{"value":"repaired"}'
    repair = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=2,
        parent_attempt_id=first_result.attempts[0].attempt_id,
    )
    repaired = run_workflow_module(
        repair,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: "2026-08-18T12:00:01Z",
    )
    assert repaired.attempts[0].status == "completed"
    assert adapter.calls == 2
    variants = record_store.load_trace(
        "execution_registered_policy"
    ).records_of_type(WorkflowModuleExecutionVariantRecord)
    assert len(variants) == 1
    assert variants[0].max_attempts == 2


def test_completed_attempt_cannot_parent_retry_or_repeat_provider() -> None:
    releases = _release_set(max_attempts=2)
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="completed")
    adapters, adapter = _adapters(registry, artifacts)
    recorder, record_store = _workflow_recorder(input_binding, input_ref)
    first = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
    )
    completed = run_workflow_module(
        first,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: _TIME,
    )
    retry = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=2,
        parent_attempt_id=completed.attempts[0].attempt_id,
    )
    with pytest.raises(ValueError, match="completed Attempt"):
        run_workflow_module(
            retry,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: "2026-08-18T12:00:01Z",
        )
    assert adapter.calls == 1
    trace = record_store.load_trace("execution_registered_policy")
    assert len(trace.records_of_type(WorkflowAttemptStartedRecord)) == 1
    assert len(trace.records_of_type(WorkflowAttemptRecord)) == 1


def test_retry_lineage_refuses_missing_cross_variant_duplicate_and_budget() -> None:
    releases = _release_set(max_attempts=3)
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="lineage")
    adapters, adapter = _adapters(registry, artifacts, status="failed")
    recorder, record_store = _workflow_recorder(input_binding, input_ref)
    first = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
    )
    first_result = run_workflow_module(
        first,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        clock=lambda: _TIME,
    )
    parent_id = first_result.attempts[0].attempt_id
    starts_before = record_store.load_trace(
        "execution_registered_policy"
    ).records_of_type(WorkflowAttemptStartedRecord)

    missing = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=2,
        parent_attempt_id="module_attempt_missing",
    )
    with pytest.raises(ValueError, match="committed parent"):
        run_workflow_module(
            missing,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: "2026-08-18T12:00:01Z",
        )

    cross_variant = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=2,
        parent_attempt_id=parent_id,
        arm_key="other",
    )
    with pytest.raises(ValueError, match="another Variant"):
        run_workflow_module(
            cross_variant,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: "2026-08-18T12:00:02Z",
        )

    cross_run = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=2,
        parent_attempt_id=parent_id,
        request_key="cross_module_run",
        module_run_id="module_run_other",
    )
    with pytest.raises(ValueError, match="another Module Run"):
        run_workflow_module(
            cross_run,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: "2026-08-18T12:00:02Z",
        )

    duplicate = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
        request_key="duplicate_ordinal_one",
    )
    with pytest.raises(ValueError, match="already has a committed Attempt"):
        run_workflow_module(
            duplicate,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: "2026-08-18T12:00:03Z",
        )

    non_contiguous = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=3,
        parent_attempt_id=parent_id,
        request_key="non_contiguous_parent",
    )
    with pytest.raises(ValueError, match="not contiguous"):
        run_workflow_module(
            non_contiguous,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: "2026-08-18T12:00:04Z",
        )

    over_budget = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=4,
        parent_attempt_id=parent_id,
        request_key="over_budget",
    )
    with pytest.raises(ValueError, match="exceeds Retry Policy"):
        run_workflow_module(
            over_budget,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: "2026-08-18T12:00:04Z",
        )

    assert adapter.calls == 1
    assert record_store.load_trace(
        "execution_registered_policy"
    ).records_of_type(WorkflowAttemptStartedRecord) == starts_before


def test_attempt_identity_is_timestamp_free() -> None:
    def committed_start(recorded_at_utc: str) -> WorkflowAttemptStartedRecord:
        releases = _release_set()
        registry = _registry(releases)
        artifacts = InMemoryCellArtifactStore()
        input_binding, input_ref = _input(
            releases,
            artifacts,
            suffix="timestamp_free",
        )
        adapters, _ = _adapters(registry, artifacts, status="failed")
        recorder, record_store = _workflow_recorder(input_binding, input_ref)
        run_workflow_module(
            _workflow_request(
                releases,
                input_binding,
                input_ref,
                ordinal=1,
                parent_attempt_id=None,
            ),
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder,
            clock=lambda: recorded_at_utc,
        )
        return record_store.load_trace(
            "execution_registered_policy"
        ).records_of_type(WorkflowAttemptStartedRecord)[0]

    first = committed_start(_TIME)
    second = committed_start("2026-08-18T13:00:00Z")
    assert first.recorded_at_utc != second.recorded_at_utc
    assert first.variant_id == second.variant_id
    assert first.attempt_id == second.attempt_id
    assert first.attempt_id == execution._stable_id(
        "module_attempt",
        first.variant_id,
        "1",
    )


def test_authority_shape_is_checked_before_policy_resolution(monkeypatch) -> None:
    releases = _release_set()
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="ordering")
    adapters, adapter = _adapters(registry, artifacts)
    observed = {"policy_resolved": False}
    original = registry.get_behavior_policy

    def observe(*args, **kwargs):
        observed["policy_resolved"] = True
        return original(*args, **kwargs)

    monkeypatch.setattr(registry, "get_behavior_policy", observe)
    with pytest.raises(ValueError, match="nothing would consume"):
        run_module(
            _isolated_request(releases, input_binding, input_ref),
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=InMemoryModuleExecutionLedger(),
            authority=object(),
            clock=lambda: _TIME,
        )
    assert not observed["policy_resolved"]
    assert adapter.calls == 0


def test_entry_policy_refusal_precedes_every_execution_effect() -> None:
    releases = _release_set(entry_policy=ModuleEntryPolicy.WORKFLOW_BOUND)
    registry = _registry(
        releases,
        states={"module": ReleaseAdmissionState.ACTIVE},
    )
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="entry")
    adapters, adapter = _adapters(registry, artifacts)
    ledger = InMemoryModuleExecutionLedger()
    request = _isolated_request(
        releases,
        input_binding,
        input_ref,
        purpose=ModuleExecutionPurpose.STANDALONE,
    )

    with pytest.raises(PermissionError, match="workflow-bound Module"):
        execution._run_module(
            request,
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=ledger,
            authority=None,
            workflow_ledger=None,
            clock=lambda: _TIME,
        )
    assert ledger._run_records == {}
    assert adapter.calls == 0


def test_attempt_start_and_protected_intent_precede_provider_invocation() -> None:
    releases = _agent_release_set()
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="authorized")
    prompt_ref = artifacts.put_bytes(
        artifact_kind_id="prompt_envelope",
        schema_version="v1",
        schema_ref="schema:prompt_envelope@v1",
        schema_sha256="9" * 64,
        media_type="text/plain",
        content=b"Return one JSON object.",
        idempotency_key="registered_policy_authorized_prompt",
        logical_name="prompt_envelope",
    )
    request = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
        prompt_ref=prompt_ref,
    )
    recorder, record_store = _workflow_recorder(input_binding, input_ref)
    events: list[str] = []
    authority, authorization_ledger = _workflow_authority(
        registry,
        request,
        record_store=record_store,
        events=events,
    )

    def observe_invocation(_request, _host) -> None:
        trace = record_store.load_trace("execution_registered_policy")
        assert len(trace.records_of_type(WorkflowAttemptStartedRecord)) == 1
        assert len(trace.records_of_type(LegacyModuleCapabilityGrant)) == 1
        assert len(
            authorization_ledger.intents_for_execution(
                "execution_registered_policy"
            )
        ) == 1
        assert events == ["product_authorization"]
        events.append("provider_invocation")

    adapter = _Adapter(registry, artifacts, on_execute=observe_invocation)
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    result = run_workflow_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        workflow_ledger=recorder,
        authority=authority,
        clock=lambda: _TIME,
    )

    assert result.attempts[0].status == "completed"
    assert events == ["product_authorization", "provider_invocation"]


def test_policy_refusal_precedes_ledger_and_adapter_effects() -> None:
    releases = _release_set()
    registry = _registry(
        releases,
        states={"retry": ReleaseAdmissionState.RETIRED},
    )
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="refusal")
    adapters, adapter = _adapters(registry, artifacts)
    ledger = InMemoryModuleExecutionLedger()

    with pytest.raises(PermissionError, match="retry_policy release"):
        run_module(
            _isolated_request(releases, input_binding, input_ref),
            release_registry=registry,
            adapters=adapters,
            artifact_host=artifacts,
            ledger=ledger,
            clock=lambda: _TIME,
        )
    assert adapter.calls == 0
    assert ledger._run_records == {}


def test_tool_free_attempt_cannot_escape_through_the_runtime_host() -> None:
    releases = _release_set()
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="escape")
    external_effects: list[str] = []

    def attempt_network(request, host) -> None:
        host.authorize_operation(
            ProviderOperationIntent(
                workflow_execution_id=request.execution_scope_id,
                module_run_id=request.module_run_id,
                variant_id=request.variant_id,
                attempt_id=request.attempt_id,
                capability_id="network_connect",
                resource_id="external_endpoint",
                action_id="network_connect",
                entitlement_snapshot_hash="f" * 64,
                idempotency_key="network_escape_probe",
                expires_after_seconds=30,
            )
        )
        external_effects.append("network_called")

    adapter = _Adapter(registry, artifacts, on_execute=attempt_network)
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    result = run_module(
        _isolated_request(releases, input_binding, input_ref),
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifacts,
        ledger=InMemoryModuleExecutionLedger(),
        clock=lambda: _TIME,
    )
    assert result.attempts[0].status == "failed"
    assert result.attempts[0].failure_class == "authorization"
    assert external_effects == []


def test_ledger_rejects_retry_primitives_that_differ_from_module() -> None:
    releases = _release_set()
    registry = _registry(releases)
    artifacts = InMemoryCellArtifactStore()
    input_binding, input_ref = _input(releases, artifacts, suffix="ledger")
    recorder, _ = _workflow_recorder(input_binding, input_ref)
    profile = registry.get_execution_profile(
        releases.profile.release_ref,
        releases.profile.release_sha256,
    )
    variant = ModuleExecutionVariantRecord(
        module_run_id="module_run_registered_policy",
        variant_id="module_variant_registered_policy",
        arm_key="default",
        replicate_index=0,
        execution_profile_ref=profile.release_ref,
        execution_profile_sha256=profile.release_sha256,
        prompt_envelope_ref=None,
        prompt_envelope_sha256=None,
        input_closure_sha256=hashlib.sha256(
            json.dumps(
                [input_binding.as_dict()],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        recorded_at_utc=_TIME,
    )
    request = _workflow_request(
        releases,
        input_binding,
        input_ref,
        ordinal=1,
        parent_attempt_id=None,
    )
    with pytest.raises(ValueError, match="differ from the Module binding"):
        recorder.record_module_start(
            request=request,
            module=releases.module,
            variants=(variant,),
            profiles=(profile,),
            retry_policy_ref="retry-policy:wrong@v1",
            retry_policy_sha256=releases.retry.release_sha256,
            max_attempts=3,
            recorded_at_utc=_TIME,
        )
    with pytest.raises(ValueError, match="max_attempts"):
        recorder.record_module_start(
            request=request,
            module=releases.module,
            variants=(variant,),
            profiles=(profile,),
            retry_policy_ref=releases.retry.release_ref,
            retry_policy_sha256=releases.retry.release_sha256,
            max_attempts=101,
            recorded_at_utc=_TIME,
        )
