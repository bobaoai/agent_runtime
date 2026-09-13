from __future__ import annotations

import json

import pytest

from agent_runtime.registry.registry_release_compilation import (
    AgentModuleReleaseCandidate,
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    RetryPolicyReleaseCandidate,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_retry_policy_release,
    runtime_owned_policy_schema_assets,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


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


def _compiled_module_case():
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
    compiled = compile_agent_module_release(
        AgentModuleReleaseCandidate(
            module_id="synthetic_module",
            module_version="candidate_v1",
            owner_contract_ref="host-source:design/owner@v1",
            owner_contract_content="# Owner\n\nSynthetic Module.\n",
            input_schema_ref="schema:synthetic_input@v1",
            input_schema_document=_schema("schema:synthetic_input@v1"),
            output_schema_ref="schema:synthetic_output@v1",
            output_schema_document=_schema("schema:synthetic_output@v1"),
            instruction_source_ref="host-source:skill/module/prompt@v1",
            instruction_text="Produce the synthetic result.\n",
            declared_operation_ids=("invoke_model",),
            compatible_transport_kinds=("claude_agent_sdk", "codex_cli"),
            behavior_policy_ref=behavior.release_ref,
            behavior_policy_sha256=behavior.release_sha256,
            evaluation_policy_ref=evaluation.release_ref,
            evaluation_policy_sha256=evaluation.release_sha256,
            retry_policy_ref=retry.release_ref,
            retry_policy_sha256=retry.release_sha256,
        )
    )
    return compiled, behavior, evaluation, retry


def test_registry_requires_exact_policy_closure_for_compiled_module() -> None:
    compiled, behavior, evaluation, retry = _compiled_module_case()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(
                *runtime_owned_policy_schema_assets(),
                *compiled.schema_assets,
            ),
            prompt_components=compiled.prompt_components,
            prompt_bundles=(compiled.prompt_bundle,),
            behavior_policies=(behavior,),
            evaluation_policies=(evaluation,),
            retry_policies=(retry,),
            modules=(compiled.module,),
        )
    )
    assert registry.get_module(
        compiled.module.release_ref,
        compiled.module.release_sha256,
    ) == compiled.module

    missing_policy = RuntimeReleaseRegistry()
    with pytest.raises(KeyError, match="unknown Retry Policy"):
        missing_policy.register_bundle(
            RuntimeReleaseBundle(
                schema_assets=(
                    *runtime_owned_policy_schema_assets(),
                    *compiled.schema_assets,
                ),
                prompt_components=compiled.prompt_components,
                prompt_bundles=(compiled.prompt_bundle,),
                behavior_policies=(behavior,),
                evaluation_policies=(evaluation,),
                modules=(compiled.module,),
            )
        )


def _modern_export(tmp_path, **requirements_changes):
    from agent_runtime import Module
    from test_agent_runtime_module_authoring import _task_project, _requirements, SKILL_ID
    root = _task_project(tmp_path, module_id="summarize_note")
    return Module.from_registration(root, skill_id=SKILL_ID, module_id="summarize_note",
        execution_requirements=_requirements(**requirements_changes)).export(module_version="v1")


def _profile_for_requirements(requirements, **changes):
    from agent_runtime.registry import ExecutionProfileReleaseSpec, compile_execution_profile_release
    values = {name: getattr(requirements, name) for name in requirements._profile_fields}
    values.update(changes)
    return compile_execution_profile_release(ExecutionProfileReleaseSpec(
        execution_profile_id="consistency_probe", release_version="v1",
        executor_adapter_id="claude_cli_adapter", executor_adapter_revision="v1",
        transport_kind="claude_cli", provider_id="anthropic", model_id="explicit-model",
        reasoning_profile="xhigh", **values,
    ))


def _variant_delta(module, profile, *, origin=None, position=None):
    from agent_runtime.registry import (
        ExecutionVariantPolicyReleaseCandidate, ExecutionVariantProfileBindingCandidate,
        compile_execution_variant_policy_release,
    )
    from agent_runtime.contracts.registry_release_definition import WorkflowRelease
    target = origin or module
    variant = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id="consistency_probe", policy_version="v1",
        origin_kind="workflow" if type(target) is WorkflowRelease else "standalone_module",
        origin_release_ref=target.release_ref, origin_release_sha256=target.release_sha256,
        bindings=(ExecutionVariantProfileBindingCandidate(
            position or module.module_id, profile.release_ref, profile.release_sha256),),
    ))
    schema = next(asset for asset in runtime_owned_policy_schema_assets()
                  if asset.release_ref == variant.policy_schema_ref)
    return RuntimeReleaseBundle(schema_assets=(schema,), execution_profiles=(profile,),
                                execution_variant_policies=(variant,))


@pytest.mark.parametrize("field,value,base", [
    ("execution_mode", "tool_free", {"tool_policy": (), "attempt_workspace_policy": "none"}),
    ("semantic_input_delivery_mode", "managed_attachment", {"tool_policy": (), "attempt_workspace_policy": "none"}),
    ("attempt_workspace_policy", "none", {"tool_policy": ()}),
    ("tool_policy", ("read",), {}),
    ("gateway_access_reasons", ("external_fact_verification",),
     {"semantic_input_delivery_mode": "gateway_read", "attempt_workspace_policy": "none",
      "network_policy": "gateway_only", "gateway_access_reasons": ("authorized_package_external_exploration",)}),
    ("network_policy", "direct_sandboxed", {"tool_policy": (), "attempt_workspace_policy": "none"}),
    ("output_constraint_mode", "prompt_only_json", {}),
    ("timeout_seconds", 900, {}),
])
def test_registry_checks_each_frozen_profile_field_atomically(tmp_path, field, value, base):
    exported = _modern_export(tmp_path, **base)
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(exported.origin_bundle)
    before = registry.snapshot()
    module = exported.module_release
    correct = _profile_for_requirements(module.execution_requirements)
    altered = _profile_for_requirements(module.execution_requirements, **{field: value})
    altered.validate()  # This test reaches Registry consistency, not schema rejection.
    with pytest.raises(ValueError, match="Module execution requirements"):
        registry.register_bundle(_variant_delta(module, altered))
    assert registry.snapshot() == before
    registry.register_bundle(_variant_delta(module, correct))


@pytest.mark.parametrize("kind", ["context", "attempts"])
def test_new_module_policy_consistency_is_not_reviewer_only(tmp_path, kind):
    from dataclasses import fields, replace
    from agent_runtime.contracts.registry_release_definition import BehaviorPolicyRelease, ModuleRelease, SchemaAssetRelease
    exported = _modern_export(tmp_path)
    fields_map = {field.name: getattr(exported.module_release, field.name)
                  for field in fields(ModuleRelease) if field.name != "release_sha256"}
    bundle = exported.origin_bundle
    if kind == "attempts":
        fields_map["execution_requirements"] = replace(exported.module_release.execution_requirements, max_attempts=2)
    else:
        ref = "schema:external_behavior@v1"
        schema = SchemaAssetRelease.build(schema_asset_id="external_behavior", schema_asset_version="v1",
            release_ref=ref, schema_document={"$id": ref,
            "$schema": "https://json-schema.org/draft/2020-12/schema", "type": "object",
            "additionalProperties": False, "properties": {"context_isolation": {"type": "string"}},
            "required": ["context_isolation"]})
        behavior = BehaviorPolicyRelease.build(policy_id="external_behavior", policy_version="v1",
            release_ref="behavior-policy:external_behavior@v1", policy_schema_ref=ref,
            policy_schema_sha256=schema.schema_sha256, policy_document={"context_isolation": "shared"})
        bundle = replace(bundle, schema_assets=(*bundle.schema_assets, schema), behavior_policies=(behavior,))
        fields_map["behavior_policy_ref"] = behavior.release_ref
        fields_map["behavior_policy_sha256"] = behavior.release_sha256
    invalid = ModuleRelease.build(**fields_map)
    registry = RuntimeReleaseRegistry()
    before = registry.snapshot()
    with pytest.raises(ValueError, match="exact Module policies"):
        registry.register_bundle(replace(bundle, modules=(invalid,)))
    assert registry.snapshot() == before


def test_workflow_variant_position_must_resolve_before_capability_check(tmp_path):
    from agent_runtime import Module
    exported = _modern_export(tmp_path)
    target = Module.to_workflow(exported).export()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(target.origin_bundle)
    before = registry.snapshot()
    profile = _profile_for_requirements(exported.module_release.execution_requirements)
    delta = _variant_delta(exported.module_release, profile,
                           origin=target.workflow_release, position="unknown_node")
    with pytest.raises(ValueError, match="position"):
        registry.register_bundle(delta)
    assert registry.snapshot() == before


@pytest.mark.parametrize("record_kind", ["modern", "legacy_v1", "legacy_v2", "legacy_without_snapshot"])
@pytest.mark.parametrize("position", ["alternative_slot", "another_slot"])
def test_standalone_nondefault_position_uses_exact_origin_for_profile_checks(tmp_path, record_kind, position):
    from dataclasses import fields
    from agent_runtime import ReviewerDefaults
    from test_agent_runtime_module_authoring import _requirements
    from test_agent_runtime_reviewer_registration_cli import _source, _legacy_export
    if record_kind == "modern":
        exported = _modern_export(tmp_path)
    else:
        source, _ = _source(tmp_path)
        snapshot = None if record_kind == "legacy_without_snapshot" else ReviewerDefaults(
            version="v2" if record_kind == "legacy_v2" else "v1")
        exported = _legacy_export(source, defaults=snapshot)
    module = exported.module_release
    assert position != module.module_id
    requirements = module.get_execution_requirements()
    profile = _profile_for_requirements(requirements or _requirements())
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(exported.origin_bundle)
    before_module = module.as_dict()
    delta = _variant_delta(module, profile, position=position)
    registry.register_bundle(delta)
    variant = registry.get_execution_variant_policy(
        delta.execution_variant_policies[0].release_ref,
        delta.execution_variant_policies[0].release_sha256)
    assert variant.policy_document()["bindings"][0]["position_id"] == position
    saved = RuntimeReleaseBundle(**{
        field.name: getattr(registry.snapshot(), field.name)
        for field in fields(RuntimeReleaseBundle)})
    restored = RuntimeReleaseRegistry()
    restored._restore_persisted_bundle(RuntimeReleaseBundle.from_dict(saved.as_dict()))
    assert restored.get_module(module.release_ref, module.release_sha256).as_dict() == before_module
    assert restored.get_execution_variant_policy(variant.release_ref, variant.release_sha256) == variant

    rejected = RuntimeReleaseRegistry()
    rejected.register_bundle(exported.origin_bundle)
    before = rejected.snapshot()
    if requirements is not None:
        mismatched = _profile_for_requirements(requirements, timeout_seconds=1000)
        with pytest.raises(ValueError, match="(Module execution requirements|Reviewer capabilities)"):
            rejected.register_bundle(_variant_delta(module, mismatched, position=position))
    else:
        # No snapshot means no invented budget; exact Profile hash resolution
        # still applies to the non-default label and may never be bypassed.
        original = delta.execution_variant_policies[0]
        document = original.policy_document()
        document["bindings"][0]["execution_profile_release_sha256"] = "0" * 64
        altered = type(original).build(policy_id=original.policy_id, policy_version=original.policy_version,
            release_ref=original.release_ref, policy_schema_ref=original.policy_schema_ref,
            policy_schema_sha256=original.policy_schema_sha256, policy_document=document)
        from dataclasses import replace
        with pytest.raises(ValueError, match="hash"):
            rejected.register_bundle(replace(delta, execution_variant_policies=(altered,)))
    assert rejected.snapshot() == before


def test_legacy_snapshot_transport_list_is_history_not_variant_veto(tmp_path):
    from agent_runtime import ReviewerDefaults
    from test_agent_runtime_reviewer_registration_cli import _source, _legacy_export
    source, _ = _source(tmp_path)
    old = _legacy_export(source, defaults=ReviewerDefaults(version="v2"))
    assert "claude_cli" not in old.module_release.compatible_transport_kinds
    before = old.module_release.as_dict()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(old.origin_bundle)
    profile = _profile_for_requirements(old.module_release.get_execution_requirements())
    registry.register_bundle(_variant_delta(old.module_release, profile))
    assert registry.get_module(old.module_release.release_ref, old.module_release.release_sha256).as_dict() == before
    altered = _profile_for_requirements(old.module_release.get_execution_requirements(), timeout_seconds=1000)
    fresh = RuntimeReleaseRegistry()
    fresh.register_bundle(old.origin_bundle)
    with pytest.raises(ValueError, match="Reviewer capabilities"):
        fresh.register_bundle(_variant_delta(old.module_release, altered))


def test_catalog_restore_preserves_old_large_budget_without_executability_conversion(tmp_path):
    from dataclasses import replace
    from agent_runtime import ReviewerDefaults
    from test_agent_runtime_reviewer_registration_cli import _source, _legacy_export
    source, _ = _source(tmp_path / "legacy")
    old = _legacy_export(source, defaults=ReviewerDefaults(timeout_seconds=86401))
    modern = _modern_export(tmp_path / "modern")
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(old.origin_bundle)
    registry.register_bundle(modern.origin_bundle)
    saved = RuntimeReleaseBundle(**{
        name: getattr(registry.snapshot(), name) for name in RuntimeReleaseBundle.__dataclass_fields__
        if name != "record_type"})
    restored = RuntimeReleaseRegistry()
    restored._restore_persisted_bundle(RuntimeReleaseBundle.from_dict(saved.as_dict()))
    observed = restored.get_module(old.module_release.release_ref, old.module_release.release_sha256)
    assert observed.as_dict() == old.module_release.as_dict()
    assert restored.get_module(modern.module_release.release_ref, modern.module_release.release_sha256) == modern.module_release
    with pytest.raises(ValueError, match="86400"):
        observed.get_execution_requirements()



def test_registry_requires_exact_schema_closure_for_compiled_module() -> None:
    compiled, behavior, evaluation, retry = _compiled_module_case()

    with pytest.raises(KeyError, match="unknown Schema Asset"):
        RuntimeReleaseRegistry().register_bundle(
            RuntimeReleaseBundle(
                schema_assets=runtime_owned_policy_schema_assets(),
                prompt_components=compiled.prompt_components,
                prompt_bundles=(compiled.prompt_bundle,),
                behavior_policies=(behavior,),
                evaluation_policies=(evaluation,),
                retry_policies=(retry,),
                modules=(compiled.module,),
            )
        )
