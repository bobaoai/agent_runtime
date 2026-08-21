from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import pytest

import agent_runtime
import agent_runtime.contracts as runtime_contracts
import agent_runtime.registry as runtime_registry
from agent_runtime.contracts import (
    registry_release_definition as release_definition,
)
from agent_runtime.contracts.registry_release_definition import (
    ExecutionProfileRelease,
    ExecutionVariantPolicyRelease,
    ModuleEntryPolicy,
    ModuleExecutionPurpose,
    ReleaseAdmissionIntent,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    WorkflowEdge,
    WorkflowNodeBinding,
    WorkflowNodeKind,
    WorkflowRelease,
)
from agent_runtime.registry.registry_release_compilation import (
    AgentModuleReleaseCandidate,
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    ExecutionProfileReleaseSpec,
    ExecutionVariantPolicyReleaseCandidate,
    ExecutionVariantProfileBindingCandidate,
    RetryPolicyReleaseCandidate,
    WorkflowNodeReleaseCandidate,
    WorkflowReleaseCandidate,
    candidate_admission_intent,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_execution_profile_release,
    compile_execution_variant_policy_release,
    compile_retry_policy_release,
    compile_workflow_release,
    runtime_owned_policy_schema_assets,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


@dataclass(frozen=True)
class _CompleteRegistryCase:
    bundle: RuntimeReleaseBundle
    records: dict[ReleaseSubjectKind, Any]
    module_variant: ExecutionVariantPolicyRelease
    workflow_variant: ExecutionVariantPolicyRelease


def _schema(schema_ref: str) -> str:
    return json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": schema_ref,
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }
    )


def _profile(
    *,
    model_id: str = "claude-opus-5",
    release_version: str = "v1",
) -> ExecutionProfileRelease:
    return compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
            execution_profile_id="registry_catalog_profile",
            executor_adapter_id="test_adapter",
            executor_adapter_revision="v1",
            transport_kind="in_process_test",
            provider_id="test_provider",
            model_id=model_id,
            reasoning_profile="xhigh",
            execution_mode="tool_free",
            semantic_input_delivery_mode="inline",
            attempt_workspace_policy="none",
            gateway_access_reasons=(),
            output_constraint_mode="prompt_only_json",
            tool_policy=(),
            network_policy="denied",
            timeout_seconds=60,
            release_version=release_version,
        )
    )


def _profile_with_release_ref(
    profile: ExecutionProfileRelease,
    release_ref: str,
) -> ExecutionProfileRelease:
    return ExecutionProfileRelease.build(
        execution_profile_id=profile.execution_profile_id,
        execution_profile_version=profile.execution_profile_version,
        release_ref=release_ref,
        executor_adapter_id=profile.executor_adapter_id,
        executor_adapter_revision=profile.executor_adapter_revision,
        transport_kind=profile.transport_kind,
        provider_id=profile.provider_id,
        model_id=profile.model_id,
        reasoning_profile=profile.reasoning_profile,
        execution_mode=profile.execution_mode,
        semantic_input_delivery_mode=profile.semantic_input_delivery_mode,
        attempt_workspace_policy=profile.attempt_workspace_policy,
        gateway_access_reasons=profile.gateway_access_reasons,
        output_constraint_mode=profile.output_constraint_mode,
        tool_policy=profile.tool_policy,
        network_policy=profile.network_policy,
        timeout_seconds=profile.timeout_seconds,
    )


def _complete_registry_case() -> _CompleteRegistryCase:
    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    evaluation = compile_evaluation_policy_release(
        EvaluationPolicyReleaseCandidate(
            policy_id="module_candidate",
            policy_version="v1",
            evaluation_mode="module_candidate",
        )
    )
    retry = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="bounded_candidate",
            policy_version="v1",
            max_attempts=3,
        )
    )
    compiled_module = compile_agent_module_release(
        AgentModuleReleaseCandidate(
            module_id="registry_catalog_module",
            module_version="v1",
            owner_contract_ref="host-source:design/registry_catalog@v1",
            owner_contract_content="# Registry Catalog Module\n",
            input_schema_ref="schema:registry_catalog_input@v1",
            input_schema_document=_schema("schema:registry_catalog_input@v1"),
            output_schema_ref="schema:registry_catalog_output@v1",
            output_schema_document=_schema("schema:registry_catalog_output@v1"),
            instruction_source_ref="host-source:skill/registry_catalog/prompt@v1",
            instruction_text="Return one registry catalog result.\n",
            declared_operation_ids=("invoke_model",),
            compatible_transport_kinds=("claude_agent_sdk", "codex_cli"),
            behavior_policy_ref=behavior.release_ref,
            behavior_policy_sha256=behavior.release_sha256,
            evaluation_policy_ref=evaluation.release_ref,
            evaluation_policy_sha256=evaluation.release_sha256,
            retry_policy_ref=retry.release_ref,
            retry_policy_sha256=retry.release_sha256,
            entry_policy=ModuleEntryPolicy.STANDALONE_ALLOWED,
        )
    )
    profile = _profile()
    workflow = compile_workflow_release(
        WorkflowReleaseCandidate(
            workflow_id="registry_catalog_workflow",
            workflow_version="v1",
            workflow_contract_version="contract_v1",
            owner_contract_ref="host-source:design/registry_catalog_workflow@v1",
            owner_contract_content="# Registry Catalog Workflow\n",
            graph_ref="host-content:workflow/registry_catalog_workflow/graph@v1",
            initial_node_id="produce",
            nodes=(
                WorkflowNodeReleaseCandidate(
                    node_id="produce",
                    node_kind=WorkflowNodeKind.MODULE,
                    module_release_ref=compiled_module.module.release_ref,
                    module_release_sha256=compiled_module.module.release_sha256,
                    input_mapping_ref="input-map:registry_catalog/produce@v1",
                    input_mapping_document={"source": "workflow_input"},
                ),
            ),
            edges=(
                WorkflowEdge(
                    source_node_id="produce",
                    outcome_id="completed",
                    target_node_id=None,
                    terminal=True,
                ),
            ),
            authorization_manifest_ref=(
                "authorization-manifest:registry_catalog_workflow@v1"
            ),
            authorization_manifest_document={
                "required_operation_ids": ["invoke_model"]
            },
            execution_binding_ref=(
                "execution-binding:registry_catalog_workflow@v1"
            ),
            execution_binding_document={
                "variant_policy_ref": (
                    "execution-variant-policy:registry_catalog_workflow@v1"
                )
            },
        )
    )
    module_variant = compile_execution_variant_policy_release(
        ExecutionVariantPolicyReleaseCandidate(
            policy_id="registry_catalog_module",
            policy_version="v1",
            origin_kind="standalone_module",
            origin_release_ref=compiled_module.module.release_ref,
            origin_release_sha256=compiled_module.module.release_sha256,
            bindings=(
                ExecutionVariantProfileBindingCandidate(
                    position_id=compiled_module.module.module_id,
                    execution_profile_release_ref=profile.release_ref,
                    execution_profile_release_sha256=profile.release_sha256,
                ),
            ),
        )
    )
    workflow_variant = compile_execution_variant_policy_release(
        ExecutionVariantPolicyReleaseCandidate(
            policy_id="registry_catalog_workflow",
            policy_version="v1",
            origin_kind="workflow",
            origin_release_ref=workflow.release_ref,
            origin_release_sha256=workflow.release_sha256,
            bindings=(
                ExecutionVariantProfileBindingCandidate(
                    position_id="produce",
                    execution_profile_release_ref=profile.release_ref,
                    execution_profile_release_sha256=profile.release_sha256,
                ),
            ),
        )
    )
    records: dict[ReleaseSubjectKind, Any] = {
        ReleaseSubjectKind.SCHEMA_ASSET: compiled_module.schema_assets[0],
        ReleaseSubjectKind.PROMPT_COMPONENT: compiled_module.prompt_components[0],
        ReleaseSubjectKind.PROMPT_BUNDLE: compiled_module.prompt_bundle,
        ReleaseSubjectKind.BEHAVIOR_POLICY: behavior,
        ReleaseSubjectKind.EVALUATION_POLICY: evaluation,
        ReleaseSubjectKind.RETRY_POLICY: retry,
        ReleaseSubjectKind.EXECUTION_VARIANT_POLICY: workflow_variant,
        ReleaseSubjectKind.EXECUTION_PROFILE: profile,
        ReleaseSubjectKind.RUNTIME_MODULE: compiled_module.module,
        ReleaseSubjectKind.WORKFLOW: workflow,
    }
    admission_intents = tuple(
        candidate_admission_intent(record)
        for record in records.values()
    )
    return _CompleteRegistryCase(
        bundle=RuntimeReleaseBundle(
            schema_assets=(
                *runtime_owned_policy_schema_assets(),
                *compiled_module.schema_assets,
            ),
            prompt_components=compiled_module.prompt_components,
            prompt_bundles=(compiled_module.prompt_bundle,),
            behavior_policies=(behavior,),
            evaluation_policies=(evaluation,),
            retry_policies=(retry,),
            execution_variant_policies=(module_variant, workflow_variant),
            execution_profiles=(profile,),
            modules=(compiled_module.module,),
            workflows=(workflow,),
            admission_intents=admission_intents,
        ),
        records=records,
        module_variant=module_variant,
        workflow_variant=workflow_variant,
    )


def _admission(
    record: Any,
    *,
    admission_id: str,
    state: ReleaseAdmissionState,
) -> ReleaseAdmissionIntent:
    candidate = candidate_admission_intent(record)
    return ReleaseAdmissionIntent.build(
        admission_id=admission_id,
        subject_kind=candidate.subject_kind,
        subject_id=candidate.subject_id,
        release_ref=candidate.release_ref,
        release_sha256=candidate.release_sha256,
        state=state,
        evidence_members=(),
    )


def _module_with_retry_hash(
    module: RuntimeModuleRelease,
    retry_policy_sha256: str,
) -> RuntimeModuleRelease:
    return RuntimeModuleRelease.build(
        module_id=module.module_id,
        module_version=module.module_version,
        release_ref=module.release_ref,
        module_kind=module.module_kind,
        owner_contract_ref=module.owner_contract_ref,
        owner_contract_sha256=module.owner_contract_sha256,
        executable_ref=module.executable_ref,
        executable_sha256=module.executable_sha256,
        input_schema_ref=module.input_schema_ref,
        input_schema_sha256=module.input_schema_sha256,
        output_schema_ref=module.output_schema_ref,
        output_schema_sha256=module.output_schema_sha256,
        prompt_bundle_ref=module.prompt_bundle_ref,
        prompt_bundle_sha256=module.prompt_bundle_sha256,
        declared_operation_ids=module.declared_operation_ids,
        behavior_policy_ref=module.behavior_policy_ref,
        behavior_policy_sha256=module.behavior_policy_sha256,
        evaluation_policy_ref=module.evaluation_policy_ref,
        evaluation_policy_sha256=module.evaluation_policy_sha256,
        retry_policy_ref=module.retry_policy_ref,
        retry_policy_sha256=retry_policy_sha256,
        compatible_transport_kinds=module.compatible_transport_kinds,
        entry_policy=module.entry_policy,
        output_resolution_policy=module.output_resolution_policy,
    )


def _workflow_with_module_hash(
    workflow: WorkflowRelease,
    module_release_sha256: str,
) -> WorkflowRelease:
    nodes = (
        WorkflowNodeBinding(
            node_id=workflow.nodes[0].node_id,
            node_kind=workflow.nodes[0].node_kind,
            module_release_ref=workflow.nodes[0].module_release_ref,
            module_release_sha256=module_release_sha256,
            input_mapping_ref=workflow.nodes[0].input_mapping_ref,
            input_mapping_sha256=workflow.nodes[0].input_mapping_sha256,
        ),
    )
    return WorkflowRelease.build(
        workflow_id=workflow.workflow_id,
        workflow_version=workflow.workflow_version,
        workflow_contract_version=workflow.workflow_contract_version,
        release_ref=workflow.release_ref,
        owner_contract_ref=workflow.owner_contract_ref,
        owner_contract_sha256=workflow.owner_contract_sha256,
        graph_ref=workflow.graph_ref,
        graph_sha256=workflow.graph_sha256,
        initial_node_id=workflow.initial_node_id,
        nodes=nodes,
        edges=workflow.edges,
        authorization_manifest_ref=workflow.authorization_manifest_ref,
        authorization_manifest_sha256=workflow.authorization_manifest_sha256,
        execution_release_ref=workflow.execution_release_ref,
        execution_release_sha256=workflow.execution_release_sha256,
        parallel_groups=workflow.parallel_groups,
    )


def _bundle_for_closure_test(
    case: _CompleteRegistryCase,
    *,
    modules: tuple[RuntimeModuleRelease, ...],
    workflows: tuple[WorkflowRelease, ...] = (),
    variants: tuple[ExecutionVariantPolicyRelease, ...] = (),
) -> RuntimeReleaseBundle:
    return RuntimeReleaseBundle(
        schema_assets=case.bundle.schema_assets,
        prompt_components=case.bundle.prompt_components,
        prompt_bundles=case.bundle.prompt_bundles,
        behavior_policies=case.bundle.behavior_policies,
        evaluation_policies=case.bundle.evaluation_policies,
        retry_policies=case.bundle.retry_policies,
        execution_profiles=case.bundle.execution_profiles,
        modules=modules,
        workflows=workflows,
        execution_variant_policies=variants,
    )


def test_complete_catalog_registers_all_families_and_variant_origins() -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(case.bundle)

    snapshot = registry.snapshot()
    assert {record.subject_kind for record in snapshot.admissions} == set(
        ReleaseSubjectKind
    )
    assert registry.get_execution_profile(
        case.records[ReleaseSubjectKind.EXECUTION_PROFILE].release_ref,
        case.records[ReleaseSubjectKind.EXECUTION_PROFILE].release_sha256,
    ) is case.records[ReleaseSubjectKind.EXECUTION_PROFILE]
    assert registry.get_workflow(
        case.records[ReleaseSubjectKind.WORKFLOW].release_ref,
        case.records[ReleaseSubjectKind.WORKFLOW].release_sha256,
    ) is case.records[ReleaseSubjectKind.WORKFLOW]
    assert registry.get_execution_variant_policy(
        case.module_variant.release_ref,
        case.module_variant.release_sha256,
    ) is case.module_variant
    assert registry.get_execution_variant_policy(
        case.workflow_variant.release_ref,
        case.workflow_variant.release_sha256,
    ) is case.workflow_variant
    expected_prefixes = {
        ReleaseSubjectKind.SCHEMA_ASSET: "schema:",
        ReleaseSubjectKind.PROMPT_COMPONENT: "prompt-component:",
        ReleaseSubjectKind.PROMPT_BUNDLE: "prompt-bundle:",
        ReleaseSubjectKind.BEHAVIOR_POLICY: "behavior-policy:",
        ReleaseSubjectKind.EVALUATION_POLICY: "evaluation-policy:",
        ReleaseSubjectKind.RETRY_POLICY: "retry-policy:",
        ReleaseSubjectKind.EXECUTION_VARIANT_POLICY: (
            "execution-variant-policy:"
        ),
        ReleaseSubjectKind.EXECUTION_PROFILE: "execution-profile:",
        ReleaseSubjectKind.RUNTIME_MODULE: "runtime-module:",
        ReleaseSubjectKind.WORKFLOW: "runtime-workflow:",
    }
    assert {
        kind: record.release_ref.startswith(expected_prefixes[kind])
        for kind, record in case.records.items()
    } == {kind: True for kind in ReleaseSubjectKind}


@pytest.mark.parametrize(
    ("subject_kind", "getter_name", "hash_field"),
    (
        (ReleaseSubjectKind.SCHEMA_ASSET, "get_schema_asset", "schema_sha256"),
        (
            ReleaseSubjectKind.PROMPT_COMPONENT,
            "get_prompt_component",
            "release_sha256",
        ),
        (ReleaseSubjectKind.PROMPT_BUNDLE, "get_prompt_bundle", "release_sha256"),
        (
            ReleaseSubjectKind.BEHAVIOR_POLICY,
            "get_behavior_policy",
            "release_sha256",
        ),
        (
            ReleaseSubjectKind.EVALUATION_POLICY,
            "get_evaluation_policy",
            "release_sha256",
        ),
        (ReleaseSubjectKind.RETRY_POLICY, "get_retry_policy", "release_sha256"),
        (
            ReleaseSubjectKind.EXECUTION_VARIANT_POLICY,
            "get_execution_variant_policy",
            "release_sha256",
        ),
        (
            ReleaseSubjectKind.EXECUTION_PROFILE,
            "get_execution_profile",
            "release_sha256",
        ),
        (ReleaseSubjectKind.RUNTIME_MODULE, "get_module", "release_sha256"),
        (ReleaseSubjectKind.WORKFLOW, "get_workflow", "release_sha256"),
    ),
)
def test_exact_retrieval_rejects_wrong_hash_for_every_release_family(
    subject_kind: ReleaseSubjectKind,
    getter_name: str,
    hash_field: str,
) -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(case.bundle)
    record = case.records[subject_kind]
    assert getattr(record, hash_field) != "0" * 64

    with pytest.raises(ValueError, match="hash mismatch"):
        getattr(registry, getter_name)(record.release_ref, "0" * 64)


def test_module_registration_uses_the_declared_retry_policy_hash() -> None:
    case = _complete_registry_case()
    module = case.records[ReleaseSubjectKind.RUNTIME_MODULE]
    corrupted = _module_with_retry_hash(module, "0" * 64)

    with pytest.raises(ValueError, match="Retry Policy release hash mismatch"):
        RuntimeReleaseRegistry().register_bundle(
            _bundle_for_closure_test(case, modules=(corrupted,))
        )


def test_workflow_registration_uses_the_declared_module_hash() -> None:
    case = _complete_registry_case()
    module = case.records[ReleaseSubjectKind.RUNTIME_MODULE]
    workflow = case.records[ReleaseSubjectKind.WORKFLOW]
    corrupted = _workflow_with_module_hash(workflow, "0" * 64)

    with pytest.raises(ValueError, match="Runtime Module release hash mismatch"):
        RuntimeReleaseRegistry().register_bundle(
            _bundle_for_closure_test(
                case,
                modules=(module,),
                workflows=(corrupted,),
            )
        )


def test_variant_registration_uses_the_declared_profile_hash() -> None:
    case = _complete_registry_case()
    module = case.records[ReleaseSubjectKind.RUNTIME_MODULE]
    document = case.module_variant.policy_document()
    document["bindings"][0]["execution_profile_release_sha256"] = "0" * 64
    corrupted = ExecutionVariantPolicyRelease.build(
        policy_id=case.module_variant.policy_id,
        policy_version=case.module_variant.policy_version,
        release_ref=case.module_variant.release_ref,
        policy_schema_ref=case.module_variant.policy_schema_ref,
        policy_schema_sha256=case.module_variant.policy_schema_sha256,
        policy_document=document,
    )

    with pytest.raises(ValueError, match="Execution Profile release hash mismatch"):
        RuntimeReleaseRegistry().register_bundle(
            _bundle_for_closure_test(
                case,
                modules=(module,),
                variants=(corrupted,),
            )
        )


@pytest.mark.parametrize(
    ("field_name", "wrong_subject_kind"),
    (
        ("schema_assets", ReleaseSubjectKind.PROMPT_COMPONENT),
        ("prompt_components", ReleaseSubjectKind.SCHEMA_ASSET),
        ("prompt_bundles", ReleaseSubjectKind.SCHEMA_ASSET),
        ("behavior_policies", ReleaseSubjectKind.EVALUATION_POLICY),
        ("evaluation_policies", ReleaseSubjectKind.BEHAVIOR_POLICY),
        ("retry_policies", ReleaseSubjectKind.BEHAVIOR_POLICY),
        ("execution_variant_policies", ReleaseSubjectKind.BEHAVIOR_POLICY),
        ("execution_profiles", ReleaseSubjectKind.BEHAVIOR_POLICY),
        ("modules", ReleaseSubjectKind.BEHAVIOR_POLICY),
        ("workflows", ReleaseSubjectKind.BEHAVIOR_POLICY),
        ("admission_intents", ReleaseSubjectKind.BEHAVIOR_POLICY),
    ),
)
def test_bundle_slots_reject_records_from_another_release_family(
    field_name: str,
    wrong_subject_kind: ReleaseSubjectKind,
) -> None:
    case = _complete_registry_case()
    bundle = RuntimeReleaseBundle(
        **{field_name: (case.records[wrong_subject_kind],)}
    )

    with pytest.raises(ValueError, match=f"release bundle {field_name}"):
        RuntimeReleaseRegistry().register_bundle(bundle)


def test_registry_replay_is_idempotent_without_admission_duplication() -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(case.bundle)
    before = registry.snapshot()

    registry.register_bundle(case.bundle)

    assert registry.snapshot() == before
    module = case.records[ReleaseSubjectKind.RUNTIME_MODULE]
    assert registry.get_module(module.release_ref, module.release_sha256) is module


def test_candidate_and_active_admission_gate_module_and_workflow_execution() -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(case.bundle)
    module = case.records[ReleaseSubjectKind.RUNTIME_MODULE]
    workflow = case.records[ReleaseSubjectKind.WORKFLOW]

    registry.assert_module_execution_allowed(module, ModuleExecutionPurpose.TEST)
    registry.assert_workflow_execution_allowed(
        workflow,
        ModuleExecutionPurpose.TEST,
    )
    with pytest.raises(PermissionError, match="not admitted for standalone"):
        registry.assert_module_execution_allowed(
            module,
            ModuleExecutionPurpose.STANDALONE,
        )
    with pytest.raises(PermissionError, match="not admitted for workflow"):
        registry.assert_workflow_execution_allowed(
            workflow,
            ModuleExecutionPurpose.WORKFLOW,
        )

    module_active = _admission(
        module,
        admission_id="admission_runtime_module_registry_catalog_active",
        state=ReleaseAdmissionState.ACTIVE,
    )
    workflow_active = _admission(
        workflow,
        admission_id="admission_workflow_registry_catalog_active",
        state=ReleaseAdmissionState.ACTIVE,
    )
    registry.register_bundle(
        RuntimeReleaseBundle(admission_intents=(module_active, workflow_active))
    )

    registry.assert_module_execution_allowed(
        module,
        ModuleExecutionPurpose.STANDALONE,
    )
    registry.assert_workflow_execution_allowed(
        workflow,
        ModuleExecutionPurpose.WORKFLOW,
    )
    assert registry.active_release_ref(
        ReleaseSubjectKind.RUNTIME_MODULE,
        module.module_id,
    ) == module.release_ref
    assert registry.active_release_ref(
        ReleaseSubjectKind.WORKFLOW,
        workflow.workflow_id,
    ) == workflow.release_ref


def test_active_replacement_requires_supersession_and_retirement_clears_pointer() -> None:
    original = _profile(model_id="claude-opus-5", release_version="v1")
    replacement = _profile(model_id="gpt-5.6", release_version="v2")
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            execution_profiles=(original, replacement),
            admission_intents=(
                candidate_admission_intent(
                    original,
                ),
                candidate_admission_intent(
                    replacement,
                ),
            ),
        )
    )
    registry.register_bundle(
        RuntimeReleaseBundle(
            admission_intents=(
                _admission(
                    original,
                    admission_id="admission_execution_profile_original_active",
                    state=ReleaseAdmissionState.ACTIVE,
                ),
            )
        )
    )
    replacement_active = _admission(
        replacement,
        admission_id="admission_execution_profile_replacement_active",
        state=ReleaseAdmissionState.ACTIVE,
    )

    with pytest.raises(ValueError, match="old release to be superseded"):
        registry.register_bundle(
            RuntimeReleaseBundle(admission_intents=(replacement_active,))
        )

    registry.register_bundle(
        RuntimeReleaseBundle(
            admission_intents=(
                _admission(
                    original,
                    admission_id="admission_execution_profile_original_superseded",
                    state=ReleaseAdmissionState.SUPERSEDED,
                ),
                replacement_active,
            )
        )
    )
    assert registry.active_release_ref(
        ReleaseSubjectKind.EXECUTION_PROFILE,
        replacement.execution_profile_id,
    ) == replacement.release_ref

    registry.register_bundle(
        RuntimeReleaseBundle(
            admission_intents=(
                _admission(
                    replacement,
                    admission_id="admission_execution_profile_replacement_retired",
                    state=ReleaseAdmissionState.RETIRED,
                ),
            )
        )
    )
    with pytest.raises(KeyError, match="no active execution_profile release"):
        registry.active_release_ref(
            ReleaseSubjectKind.EXECUTION_PROFILE,
            replacement.execution_profile_id,
        )


def test_failed_bundle_leaves_a_populated_registry_unchanged() -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    retained_schema = case.records[ReleaseSubjectKind.SCHEMA_ASSET]
    registry.register_bundle(RuntimeReleaseBundle(schema_assets=(retained_schema,)))
    before = registry.snapshot()
    new_schema = next(
        schema
        for schema in case.bundle.schema_assets
        if schema.release_ref != retained_schema.release_ref
        and schema.schema_asset_id == "registry_catalog_output"
    )
    behavior = case.records[ReleaseSubjectKind.BEHAVIOR_POLICY]

    with pytest.raises(KeyError, match="unknown Schema Asset"):
        registry.register_bundle(
            RuntimeReleaseBundle(
                schema_assets=(new_schema,),
                behavior_policies=(behavior,),
            )
        )

    assert registry.snapshot() == before


def test_release_ref_collision_is_rejected() -> None:
    original = _profile(model_id="claude-opus-5")
    conflicting = _profile(model_id="gpt-5.6")
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(RuntimeReleaseBundle(execution_profiles=(original,)))

    with pytest.raises(ValueError, match="release_ref collision"):
        registry.register_bundle(
            RuntimeReleaseBundle(execution_profiles=(conflicting,))
        )


def test_release_version_key_collision_is_rejected() -> None:
    original = _profile()
    conflicting = _profile_with_release_ref(
        original,
        "execution-profile:registry_catalog_profile_alias@v1",
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(RuntimeReleaseBundle(execution_profiles=(original,)))

    with pytest.raises(ValueError, match="release version already registered"):
        registry.register_bundle(
            RuntimeReleaseBundle(execution_profiles=(conflicting,))
        )


def test_schema_release_ref_collision_is_rejected() -> None:
    schema = _complete_registry_case().records[ReleaseSubjectKind.SCHEMA_ASSET]
    revised_document = schema.schema_document()
    revised_document["description"] = "Different canonical schema content."
    conflicting = SchemaAssetRelease.build(
        schema_asset_id=schema.schema_asset_id,
        schema_asset_version=schema.schema_asset_version,
        release_ref=schema.release_ref,
        schema_document=revised_document,
    )
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(RuntimeReleaseBundle(schema_assets=(schema,)))

    with pytest.raises(ValueError, match="Schema Asset release_ref collision"):
        registry.register_bundle(RuntimeReleaseBundle(schema_assets=(conflicting,)))


def test_admission_id_collision_is_rejected() -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(case.bundle)
    schema = case.records[ReleaseSubjectKind.SCHEMA_ASSET]
    original = candidate_admission_intent(schema)
    conflicting = _admission(
        schema,
        admission_id=original.admission_id,
        state=ReleaseAdmissionState.RETIRED,
    )

    with pytest.raises(ValueError, match="admission_id collision"):
        registry.register_bundle(RuntimeReleaseBundle(admission_intents=(conflicting,)))


def test_illegal_admission_transition_is_rejected() -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(case.bundle)
    schema = case.records[ReleaseSubjectKind.SCHEMA_ASSET]
    repeated_candidate = _admission(
        schema,
        admission_id="admission_schema_asset_registry_catalog_repeated_candidate",
        state=ReleaseAdmissionState.CANDIDATE,
    )

    with pytest.raises(ValueError, match="illegal release admission transition"):
        registry.register_bundle(
            RuntimeReleaseBundle(admission_intents=(repeated_candidate,))
        )


def test_execution_variant_requires_at_least_one_profile_binding() -> None:
    candidate = ExecutionVariantPolicyReleaseCandidate(
        policy_id="empty_variant",
        policy_version="v1",
        origin_kind="standalone_module",
        origin_release_ref="runtime-module:registry_catalog_module@v1",
        origin_release_sha256="a" * 64,
        bindings=(),
    )

    with pytest.raises(ValueError, match="at least one profile binding"):
        compile_execution_variant_policy_release(candidate)

    variant_schema = next(
        schema
        for schema in runtime_owned_policy_schema_assets()
        if schema.schema_asset_id == "runtime_execution_variant_policy"
    )
    empty_release = ExecutionVariantPolicyRelease.build(
        policy_id="empty_variant",
        policy_version="v1",
        release_ref="execution-variant-policy:empty_variant@v1",
        policy_schema_ref=variant_schema.release_ref,
        policy_schema_sha256=variant_schema.schema_sha256,
        policy_document={
            "origin_kind": "standalone_module",
            "origin_release_ref": "runtime-module:registry_catalog_module@v1",
            "origin_release_sha256": "a" * 64,
            "bindings": [],
        },
    )
    with pytest.raises(ValueError, match="at least one profile binding"):
        RuntimeReleaseRegistry().register_bundle(
            RuntimeReleaseBundle(
                schema_assets=(variant_schema,),
                execution_variant_policies=(empty_release,),
            )
        )


def test_predecessor_profile_selection_types_are_absent_from_public_surfaces() -> None:
    retired_names = {
        "WorkflowExecutionProfileSelection",
        "WorkflowNodeExecutionProfileBinding",
    }

    for module in (agent_runtime, runtime_contracts, release_definition):
        assert retired_names.isdisjoint(module.__dict__)
        assert retired_names.isdisjoint(module.__all__)


def test_registry_facade_exports_2c1_without_unimplemented_2c2_migration() -> None:
    slice_2ab_exports = {
        "AgentModuleReleaseCandidate",
        "BehaviorPolicyReleaseCandidate",
        "CompiledAgentModuleRelease",
        "CompiledNonAgentModuleRelease",
        "EvaluationPolicyReleaseCandidate",
        "ExecutionProfileReleaseSpec",
        "ExecutionVariantPolicyReleaseCandidate",
        "ExecutionVariantProfileBindingCandidate",
        "NonAgentModuleReleaseCandidate",
        "RetryPolicyReleaseCandidate",
        "RuntimeReleaseBundle",
        "RuntimeReleaseRegistry",
        "WorkflowNodeReleaseCandidate",
        "WorkflowReleaseCandidate",
        "candidate_admission_intent",
        "compile_agent_module_release",
        "compile_behavior_policy_release",
        "compile_evaluation_policy_release",
        "compile_execution_profile_release",
        "compile_execution_variant_policy_release",
        "compile_non_agent_module_release",
        "compile_prompt_bundle_release",
        "compile_retry_policy_release",
        "compile_workflow_release",
        "runtime_owned_policy_schema_assets",
    }
    slice_2c1_exports = {
        "PostgresRuntimeReleaseQueryStore",
        "PostgresRuntimeReleaseStore",
        "REGISTRY_SCHEMA_RELEASE_ID",
    }
    slice_2c2_exports = {
        "CompiledRegistryMigrationCandidateSet",
        "RegistryActivePointerDisposition",
        "RegistryActivePointerDispositionCandidate",
        "RegistryAdmissionDispositionCandidate",
        "RegistryAdmissionIdentityDisposition",
        "RegistryMigrationCandidateSet",
        "RegistryReleaseDispositionCandidate",
        "RegistryReleaseIdentityDisposition",
        "RegistrySchemaInstallation",
        "RegistrySchemaMigrationPlan",
        "RegistryTargetAdmissionCandidate",
        "compile_registry_migration_candidate_set",
    }

    public_exports = set(runtime_registry.__all__)
    assert slice_2ab_exports <= public_exports
    assert slice_2c1_exports <= public_exports
    assert slice_2c2_exports.isdisjoint(public_exports)
