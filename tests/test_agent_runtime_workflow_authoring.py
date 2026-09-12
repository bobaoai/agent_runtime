from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from agent_runtime import (
    WORKFLOW_MODULE_CLOSURE_INVALID,
    Module,
    ModuleReviewer,
    Workflow,
    WorkflowAuthoringError,
)
from agent_runtime.contracts.registry_release_definition import (
    WorkflowEdge,
    WorkflowNodeKind,
)
from agent_runtime.registry import (
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    ExecutionProfileReleaseSpec,
    ExecutionVariantPolicyReleaseCandidate,
    ExecutionVariantProfileBindingCandidate,
    ModuleRegistrationSource,
    RetryPolicyReleaseCandidate,
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
    WorkflowNodeReleaseCandidate,
    WorkflowReleaseCandidate,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_execution_profile_release,
    compile_execution_variant_policy_release,
    compile_retry_policy_release,
    runtime_owned_policy_schema_assets,
)
from agent_runtime.contracts.registry_release_definition import (
    ModuleEntryPolicy,
    OutputResolutionPolicy,
    ReleaseSubjectKind,
)


def _schema(schema_ref: str) -> str:
    return json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": schema_ref,
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _reviewer_output_schema(schema_ref: str) -> str:
    string = {"type": "string", "minLength": 1}
    return json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": schema_ref,
        "type": "object", "additionalProperties": False,
        "$defs": {
            "evidence": {"type": "object", "additionalProperties": False, "properties": {name: string for name in ("source_ref", "locator", "observation")}, "required": ["source_ref", "locator", "observation"]},
            "finding": {"type": "object", "additionalProperties": False, "properties": {"finding_id": string, "severity": {**string, "enum": ["block", "fix", "note"]}, "evidence": {"$ref": "#/$defs/evidence"}, "requirement": string, "impact": string, "accountable_owner_ref": string, "required_change": string}, "required": ["finding_id", "severity", "evidence", "requirement", "impact", "accountable_owner_ref", "required_change"]},
            "check_result": {"type": "object", "additionalProperties": False, "properties": {"check_id": string, "disposition": {**string, "enum": ["passed", "finding", "not_applicable", "not_run"]}, "assessment": string, "finding_ids": {"type": "array", "uniqueItems": True, "items": string}}, "required": ["check_id", "disposition", "assessment", "finding_ids"]}},
        "properties": {"verdict": {**string, "enum": ["passed", "non_pass", "blocked"]}, "check_results": {"type": "array", "items": {"$ref": "#/$defs/check_result"}}, "findings": {"type": "array", "items": {"$ref": "#/$defs/finding"}}, "safe_next_step": string},
        "required": ["verdict", "check_results", "findings", "safe_next_step"]}, sort_keys=True, separators=(",", ":"))


def _policies():
    return (
        compile_behavior_policy_release(
            BehaviorPolicyReleaseCandidate(
                policy_id="workflow_execution_isolated",
                policy_version="v1",
                context_isolation="workflow_execution_isolated",
            )
        ),
        compile_evaluation_policy_release(
            EvaluationPolicyReleaseCandidate(
                policy_id="module_candidate",
                policy_version="v1",
                evaluation_mode="module_candidate",
            )
        ),
        compile_retry_policy_release(
            RetryPolicyReleaseCandidate(
                policy_id="bounded_candidate",
                policy_version="v1",
                max_attempts=3,
            )
        ),
    )


def _profile(profile_id: str, model_id: str):
    return compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
            execution_profile_id=profile_id,
            executor_adapter_id="claude_agent_sdk_inline_executor",
            executor_adapter_revision="v2",
            transport_kind="claude_agent_sdk",
            provider_id="anthropic",
            model_id=model_id,
            reasoning_profile="xhigh",
            execution_mode="tool_free",
            semantic_input_delivery_mode="inline",
            attempt_workspace_policy="none",
            gateway_access_reasons=(),
            output_constraint_mode="native_structured_output",
            tool_policy=(),
            network_policy="denied",
            timeout_seconds=900,
            release_version="v1",
        )
    )


def _module_export():
    module_id = "workflow_authoring_reviewer"
    owner_content = "# Workflow Authoring Reviewer\n"
    source = ModuleRegistrationSource(
        skill_id="workflow-authoring-test",
        module_id=module_id,
        owner_contract_ref=(
            "owner-contract-sha256:"
            + hashlib.sha256(owner_content.encode("utf-8")).hexdigest()
        ),
        owner_contract_content=owner_content,
        input_schema_ref=f"schema:{module_id}_input@v1",
        input_schema_document=_schema(f"schema:{module_id}_input@v1"),
        output_schema_ref=f"schema:{module_id}_output@v1",
        output_schema_document=_reviewer_output_schema(f"schema:{module_id}_output@v1"),
        instruction_text="Return one exact result.\n",
        declared_operation_ids=("model_execute",),
        compatible_transport_kinds=("claude_agent_sdk",),
        behavior_policy_ref="behavior-policy:workflow_execution_isolated@v1",
        evaluation_policy_ref="evaluation-policy:module_candidate@v1",
        retry_policy_ref="retry-policy:bounded_candidate@v1",
        entry_policy=ModuleEntryPolicy.WORKFLOW_BOUND,
        output_resolution_policy=OutputResolutionPolicy.EVALUATED_SINGLE,
    )
    behavior, evaluation, retry = _policies()
    exported = ModuleReviewer(source=source).export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=None,
    )
    return exported


def _workflow_candidate(module_release_ref: str, module_release_sha256: str):
    workflow_id = "portable_review_workflow"
    return WorkflowReleaseCandidate(
        workflow_id=workflow_id,
        workflow_version="v1",
        workflow_contract_version="contract_v1",
        owner_contract_ref="owner-contract:portable-review-workflow@v1",
        owner_contract_content="# Portable Review Workflow\n",
        graph_ref="workflow-graph:portable-review-workflow@v1",
        initial_node_id="first_review",
        nodes=(
            WorkflowNodeReleaseCandidate(
                node_id="first_review",
                node_kind=WorkflowNodeKind.MODULE,
                module_release_ref=module_release_ref,
                module_release_sha256=module_release_sha256,
                input_mapping_ref="input-map:portable-review/first@v1",
                input_mapping_document={"source": "workflow_input"},
            ),
            WorkflowNodeReleaseCandidate(
                node_id="second_review",
                node_kind=WorkflowNodeKind.MODULE,
                module_release_ref=module_release_ref,
                module_release_sha256=module_release_sha256,
                input_mapping_ref="input-map:portable-review/second@v1",
                input_mapping_document={"source": "first_review"},
            ),
        ),
        edges=(
            WorkflowEdge(
                source_node_id="first_review",
                outcome_id="completed",
                target_node_id="second_review",
                terminal=False,
            ),
            WorkflowEdge(
                source_node_id="second_review",
                outcome_id="completed",
                target_node_id=None,
                terminal=True,
            ),
        ),
        authorization_manifest_ref="authorization-manifest:portable-review@v1",
        authorization_manifest_document={
            "required_operation_ids": ["model_execute"]
        },
        execution_binding_ref=f"execution-binding:{workflow_id}@v1",
        execution_binding_document={
            "schema_version": "workflow_execution_binding_v1",
            "workflow_id": workflow_id,
            "variant_policy_family": "execution_variant_policy",
        },
    )


def _workflow_export():
    module_export = _module_export()
    candidate = _workflow_candidate(
        module_export.module_release.release_ref,
        module_export.module_release.release_sha256,
    )
    return Workflow.from_graph(
        candidate,
        module_exports=(module_export,),
    ).export()


def test_plain_module_and_reviewer_inherit_one_exact_export_constructor(monkeypatch):
    from agent_runtime.registry import registry_module_authoring as authoring
    from agent_runtime.registry import compile_agent_module_release

    seed = _module_export()
    source = replace(seed.source, module_id="plain_writer",
                     output_schema_document=_schema(seed.source.output_schema_ref))
    candidate = authoring._candidate(source, module_version="v1",
        behavior_policy=seed.behavior_policy, evaluation_policy=seed.evaluation_policy,
        retry_policy=seed.retry_policy)
    exported = replace(seed, source=source, candidate=candidate,
                       compiled=compile_agent_module_release(candidate))

    class PlainModule(Module):
        @classmethod
        def from_registration(cls, *args, **kwargs):
            pytest.fail("conversion must not load source")

        def export(self, **kwargs):
            return exported

        def project(self, *args, **kwargs):
            pytest.fail("conversion must not inspect a Registry")

    plain = PlainModule()
    fixed = plain.export()
    before = fixed.origin_bundle

    def forbidden(*args, **kwargs):
        pytest.fail("conversion must only consume the exact Module export")

    with monkeypatch.context() as patch:
        for name in ("load_module_registration", "compile_agent_module_release",
                     "resolve_reviewer_policy", "reviewer_execution_profile", "_validate_reviewer_output_schema"):
            patch.setattr(authoring, name, forbidden)
        patch.setattr(PlainModule, "export", forbidden)
        patch.setattr(RuntimeReleaseRegistry, "register_bundle", forbidden)
        workflow = plain.to_workflow(fixed)
    result = workflow.export()
    module = fixed.module_release
    assert result.workflow_release.workflow_id == module.module_id == "plain_writer"
    assert result.workflow_release.workflow_version == module.module_version
    assert result.workflow_release.release_ref != module.release_ref
    assert result.workflow_release.initial_node_id == "module"
    assert result.workflow_release.nodes[0].module_release_ref == module.release_ref
    assert result.workflow_release.nodes[0].module_release_sha256 == module.release_sha256
    assert workflow.module_exports[0] is fixed
    assert fixed.origin_bundle == before
    assert result.origin_bundle.modules == before.modules
    assert result.origin_bundle.schema_assets == tuple(sorted(before.schema_assets, key=lambda row: row.release_ref))
    assert module.reviewer_defaults is None
    assert result.origin_bundle.execution_profiles == result.origin_bundle.execution_variant_policies == ()
    assert ModuleReviewer.to_workflow is Module.to_workflow
    assert "to_workflow" not in ModuleReviewer.__dict__
    assert not hasattr(Workflow, "for_reviewer")


def test_single_module_explicit_workflow_name_preserves_module():
    exported = _module_export()
    workflow = ModuleReviewer.to_workflow(exported, workflow_id="explicit_pipeline").export()
    assert workflow.workflow_release.workflow_id == "explicit_pipeline"
    assert workflow.origin_bundle.modules == (exported.module_release,)
    custom_graph = _workflow_export().workflow_release
    assert custom_graph.workflow_id == "portable_review_workflow"
    assert [node.node_id for node in custom_graph.nodes] == ["first_review", "second_review"]


@pytest.mark.parametrize("name", ["", "../escape", "CamelCase", "space name", 42])
def test_single_module_invalid_workflow_name_is_not_a_default(name):
    with pytest.raises(ValueError, match="workflow_id"):
        Module.to_workflow(_module_export(), workflow_id=name)


def test_single_module_rejects_non_export():
    with pytest.raises(WorkflowAuthoringError) as failure:
        Module.to_workflow(object())
    assert failure.value.error_code == WORKFLOW_MODULE_CLOSURE_INVALID


def _target_variant_bundle(exported, profile_id: str, model_id: str):
    profile = _profile(profile_id, model_id)
    variant = compile_execution_variant_policy_release(
        ExecutionVariantPolicyReleaseCandidate(
            policy_id=f"{profile_id}_workflow_variant",
            policy_version="v1",
            origin_kind="workflow",
            origin_release_ref=exported.workflow_release.release_ref,
            origin_release_sha256=exported.workflow_release.release_sha256,
            bindings=(
                ExecutionVariantProfileBindingCandidate(
                    position_id="first_review",
                    execution_profile_release_ref=profile.release_ref,
                    execution_profile_release_sha256=profile.release_sha256,
                ),
                ExecutionVariantProfileBindingCandidate(
                    position_id="second_review",
                    execution_profile_release_ref=profile.release_ref,
                    execution_profile_release_sha256=profile.release_sha256,
                ),
            ),
        )
    )
    schema_asset = next(
        asset
        for asset in runtime_owned_policy_schema_assets()
        if asset.release_ref == variant.policy_schema_ref
    )
    return RuntimeReleaseBundle(
        schema_assets=(schema_asset,),
        execution_profiles=(profile,),
        execution_variant_policies=(variant,),
    ), profile, variant


def test_workflow_export_has_exact_target_independent_origin_closure() -> None:
    exported = _workflow_export()
    bundle = exported.origin_bundle

    assert bundle.workflows == (exported.workflow_release,)
    assert len(bundle.modules) == 1
    assert len(bundle.behavior_policies) == 1
    assert len(bundle.evaluation_policies) == 1
    assert len(bundle.retry_policies) == 1
    assert bundle.execution_profiles == ()
    assert bundle.execution_variant_policies == ()
    assert exported.workflow_release.execution_release_ref == (
        "execution-binding:portable_review_workflow@v1"
    )


def test_workflow_refuses_module_hash_outside_exact_graph_closure() -> None:
    module_export = _module_export()
    candidate = _workflow_candidate(
        module_export.module_release.release_ref,
        "f" * 64,
    )

    with pytest.raises(WorkflowAuthoringError) as exc_info:
        Workflow.from_graph(
            candidate,
            module_exports=(module_export,),
        ).export()
    assert exc_info.value.error_code == WORKFLOW_MODULE_CLOSURE_INVALID


def test_same_workflow_export_registers_into_two_independent_registries() -> None:
    exported = _workflow_export()
    registry_a = RuntimeReleaseRegistry()
    registry_b = RuntimeReleaseRegistry()

    first_a = registry_a.register_bundle(exported.origin_bundle)
    first_b = registry_b.register_bundle(exported.origin_bundle)
    replay_a = registry_a.register_bundle(exported.origin_bundle)

    assert first_a.catalog_snapshot.workflows == first_b.catalog_snapshot.workflows
    assert replay_a.catalog_snapshot == first_a.catalog_snapshot
    assert registry_a.snapshot() == registry_b.snapshot()


def test_target_registries_keep_profile_variant_and_active_pointer_independent() -> None:
    exported = _workflow_export()
    registry_a = RuntimeReleaseRegistry()
    registry_b = RuntimeReleaseRegistry()
    registry_a.register_bundle(exported.origin_bundle)
    registry_b.register_bundle(exported.origin_bundle)
    bundle_a, profile_a, variant_a = _target_variant_bundle(
        exported,
        "runtime_a_profile",
        "claude-opus-5",
    )
    bundle_b, profile_b, variant_b = _target_variant_bundle(
        exported,
        "runtime_b_profile",
        "claude-sonnet-5",
    )

    registry_a.register_bundle(bundle_a)
    registry_b.register_bundle(bundle_b)
    registry_a.set_active_release(
        ReleaseSubjectKind.WORKFLOW,
        exported.workflow_release.workflow_id,
        exported.workflow_release.release_ref,
        exported.workflow_release.release_sha256,
    )

    assert profile_a.release_ref != profile_b.release_ref
    assert variant_a.release_ref != variant_b.release_ref
    assert registry_a.snapshot().active_release_refs == {
        "workflow:portable_review_workflow": exported.workflow_release.release_ref
    }
    assert registry_b.snapshot().active_release_refs == {}
    assert registry_a.snapshot().workflows == registry_b.snapshot().workflows
