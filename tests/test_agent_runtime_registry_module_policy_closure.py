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


def test_registry_accepts_complete_module_dependency_closure() -> None:
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


@pytest.mark.parametrize(
    ("missing", "message"),
    (
        ("prompt_bundle", "unknown Prompt Bundle"),
        ("behavior_policy", "unknown Behavior Policy"),
        ("evaluation_policy", "unknown Evaluation Policy"),
        ("retry_policy", "unknown Retry Policy"),
        ("input_schema", "unknown Schema Asset"),
        ("output_schema", "unknown Schema Asset"),
    ),
)
def test_registry_requires_each_exact_module_dependency(
    missing: str,
    message: str,
) -> None:
    compiled, behavior, evaluation, retry = _compiled_module_case()
    schema_assets = [
        *runtime_owned_policy_schema_assets(),
        *compiled.schema_assets,
    ]
    if missing == "input_schema":
        schema_assets = [
            schema
            for schema in schema_assets
            if schema.release_ref != compiled.module.input_schema_ref
        ]
    elif missing == "output_schema":
        schema_assets = [
            schema
            for schema in schema_assets
            if schema.release_ref != compiled.module.output_schema_ref
        ]
    prompt_bundles = (
        () if missing == "prompt_bundle" else (compiled.prompt_bundle,)
    )
    behavior_policies = () if missing == "behavior_policy" else (behavior,)
    evaluation_policies = (
        () if missing == "evaluation_policy" else (evaluation,)
    )
    retry_policies = () if missing == "retry_policy" else (retry,)

    with pytest.raises(KeyError, match=message):
        RuntimeReleaseRegistry().register_bundle(
            RuntimeReleaseBundle(
                schema_assets=tuple(schema_assets),
                prompt_components=compiled.prompt_components,
                prompt_bundles=prompt_bundles,
                behavior_policies=behavior_policies,
                evaluation_policies=evaluation_policies,
                retry_policies=retry_policies,
                modules=(compiled.module,),
            )
        )
