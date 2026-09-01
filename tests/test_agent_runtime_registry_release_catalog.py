from __future__ import annotations

from dataclasses import dataclass, fields
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
    ReleaseSubjectKind,
    SchemaAssetRelease,
    WorkflowEdge,
    WorkflowNodeKind,
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
from agent_runtime.inspection.inspection_release_rendering import (
    build_runtime_release_inventory,
    render_runtime_release_markdown,
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
                "schema_version": "workflow_execution_binding_v1",
                "workflow_id": "registry_catalog_workflow",
                "variant_policy_family": "execution_variant_policy",
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
        ),
        records=records,
        module_variant=module_variant,
        workflow_variant=workflow_variant,
    )


def test_release_inspection_projects_every_registered_policy_family() -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(case.bundle)
    before = registry.snapshot()

    inventory = build_runtime_release_inventory(registry)
    markdown = render_runtime_release_markdown(registry)
    repeated_inventory = build_runtime_release_inventory(registry)
    repeated_markdown = render_runtime_release_markdown(registry)

    assert [item["release_ref"] for item in inventory["behavior_policies"]] == [
        case.records[ReleaseSubjectKind.BEHAVIOR_POLICY].release_ref
    ]
    assert [item["release_ref"] for item in inventory["evaluation_policies"]] == [
        case.records[ReleaseSubjectKind.EVALUATION_POLICY].release_ref
    ]
    assert [item["release_ref"] for item in inventory["retry_policies"]] == [
        case.records[ReleaseSubjectKind.RETRY_POLICY].release_ref
    ]
    assert [
        item["release_ref"]
        for item in inventory["execution_variant_policies"]
    ] == sorted(
        [case.module_variant.release_ref, case.workflow_variant.release_ref]
    )
    assert "## Policies" in markdown
    assert "Execution Variant" in markdown
    assert repeated_inventory == inventory
    assert repeated_markdown == markdown
    assert registry.snapshot() == before


def test_complete_catalog_registers_all_families_and_variant_origins() -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    result = registry.register_bundle(case.bundle)

    snapshot = registry.snapshot()
    assert result.submitted_bundle == case.bundle
    assert result.catalog_snapshot == snapshot
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


def test_registry_release_replay_is_idempotent() -> None:
    case = _complete_registry_case()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(case.bundle)
    before = registry.snapshot()

    registry.register_bundle(case.bundle)

    assert registry.snapshot() == before
    module = case.records[ReleaseSubjectKind.RUNTIME_MODULE]
    assert registry.get_module(module.release_ref, module.release_sha256) is module


def test_active_pointer_gates_module_and_workflow_product_entry() -> None:
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
    with pytest.raises(PermissionError, match="no active standalone entry"):
        registry.assert_module_execution_allowed(
            module,
            ModuleExecutionPurpose.STANDALONE,
        )
    with pytest.raises(PermissionError, match="no active entry"):
        registry.assert_workflow_execution_allowed(
            workflow,
            ModuleExecutionPurpose.WORKFLOW,
        )

    module_pointer = registry.set_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        module.module_id,
        module.release_ref,
        module.release_sha256,
    )
    workflow_pointer = registry.set_active_release(
        ReleaseSubjectKind.WORKFLOW,
        workflow.workflow_id,
        workflow.release_ref,
        workflow.release_sha256,
    )

    registry.assert_module_execution_allowed(
        module,
        ModuleExecutionPurpose.STANDALONE,
    )
    registry.assert_workflow_execution_allowed(
        workflow,
        ModuleExecutionPurpose.WORKFLOW,
    )
    assert module_pointer.active_release_ref == module.release_ref
    assert workflow_pointer.active_release_ref == workflow.release_ref
    assert registry.resolve_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        module.module_id,
    ) == module
    assert registry.resolve_active_release(
        ReleaseSubjectKind.WORKFLOW,
        workflow.workflow_id,
    ) == workflow


def test_active_pointer_replacement_and_clear_use_exact_preconditions() -> None:
    case = _complete_registry_case()
    original = case.records[ReleaseSubjectKind.RUNTIME_MODULE]
    replacement_fields = {
        field.name: getattr(original, field.name)
        for field in fields(original)
        if field.name != "release_sha256"
    }
    replacement_fields.update(
        {
            "module_version": "v2",
            "release_ref": f"runtime-module:{original.module_id}@v2",
        }
    )
    replacement = type(original).build(**replacement_fields)
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(case.bundle)
    registry.register_bundle(RuntimeReleaseBundle(modules=(replacement,)))
    registry.set_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        original.module_id,
        original.release_ref,
        original.release_sha256,
    )
    registry.set_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        replacement.module_id,
        replacement.release_ref,
        replacement.release_sha256,
    )
    assert registry.resolve_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        replacement.module_id,
    ) == replacement
    with pytest.raises(ValueError, match="current target differs"):
        registry.clear_active_release(
            ReleaseSubjectKind.RUNTIME_MODULE,
            original.module_id,
            expected_release_ref=original.release_ref,
            expected_release_sha256=original.release_sha256,
        )
    cleared = registry.clear_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        replacement.module_id,
        expected_release_ref=replacement.release_ref,
        expected_release_sha256=replacement.release_sha256,
    )
    assert cleared.active_release_ref is None
    assert registry.clear_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        replacement.module_id,
        expected_release_ref=replacement.release_ref,
        expected_release_sha256=replacement.release_sha256,
    ) == cleared

    profile = case.records[ReleaseSubjectKind.EXECUTION_PROFILE]
    with pytest.raises(ValueError, match="only Module or Workflow"):
        registry.set_active_release(
            ReleaseSubjectKind.EXECUTION_PROFILE,
            profile.execution_profile_id,
            profile.release_ref,
            profile.release_sha256,
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


def test_registry_facade_exports_slice_2ab_and_slice_2c_public_surfaces() -> None:
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
        "RuntimeActiveReleasePointerResult",
        "RuntimeReleaseBundle",
        "RuntimeReleaseRegistry",
        "WorkflowNodeReleaseCandidate",
        "WorkflowReleaseCandidate",
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
    slice_2c_exports = {
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
        "compile_registry_migration_candidate_set",
    }

    public_exports = set(runtime_registry.__all__)
    assert slice_2ab_exports <= public_exports
    assert slice_2c_exports <= public_exports
