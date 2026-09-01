from __future__ import annotations

from dataclasses import fields, replace
import json

from agent_runtime.contracts.registry_release_definition import ModuleKind
from agent_runtime.registry.registry_release_compilation import (
    AgentModuleReleaseCandidate,
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    ExecutionProfileReleaseSpec,
    NonAgentModuleReleaseCandidate,
    RetryPolicyReleaseCandidate,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_execution_profile_release,
    compile_non_agent_module_release,
    compile_retry_policy_release,
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


def _policies():
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
    return behavior, evaluation, retry


def _agent_candidate() -> AgentModuleReleaseCandidate:
    behavior, evaluation, retry = _policies()
    return AgentModuleReleaseCandidate(
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


def _profile(profile_id: str, model_id: str):
    return compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
            execution_profile_id=profile_id,
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
            release_version="v1",
        )
    )


def test_model_profile_variants_do_not_recompile_the_module() -> None:
    compiled = compile_agent_module_release(_agent_candidate())
    opus = _profile("opus_5_xhigh", "claude-opus-5")
    codex = _profile("codex_5_6_xhigh", "gpt-5.6")

    assert opus.release_sha256 != codex.release_sha256
    assert compiled.module.release_sha256 == compile_agent_module_release(
        _agent_candidate()
    ).module.release_sha256


def test_execution_profile_has_only_provider_and_execution_configuration() -> None:
    profile = _profile("opus_5_xhigh", "claude-opus-5")

    assert {field.name for field in fields(ExecutionProfileReleaseSpec)} == {
        "execution_profile_id",
        "executor_adapter_id",
        "executor_adapter_revision",
        "transport_kind",
        "provider_id",
        "model_id",
        "reasoning_profile",
        "execution_mode",
        "semantic_input_delivery_mode",
        "attempt_workspace_policy",
        "gateway_access_reasons",
        "output_constraint_mode",
        "tool_policy",
        "network_policy",
        "timeout_seconds",
        "release_version",
    }
    assert set(profile.as_dict()) == {
        "execution_profile_id",
        "execution_profile_version",
        "release_ref",
        "executor_adapter_id",
        "executor_adapter_revision",
        "transport_kind",
        "provider_id",
        "model_id",
        "reasoning_profile",
        "execution_mode",
        "semantic_input_delivery_mode",
        "attempt_workspace_policy",
        "gateway_access_reasons",
        "output_constraint_mode",
        "tool_policy",
        "network_policy",
        "timeout_seconds",
        "release_sha256",
    }


def test_policy_change_reissues_module_without_changing_profiles() -> None:
    candidate = _agent_candidate()
    original = compile_agent_module_release(candidate)
    profile = _profile("opus_5_xhigh", "claude-opus-5")
    retry_v2 = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="bounded_candidate",
            policy_version="v2",
            max_attempts=5,
        )
    )
    revised = compile_agent_module_release(
        replace(
            candidate,
            module_version="candidate_v2",
            retry_policy_ref=retry_v2.release_ref,
            retry_policy_sha256=retry_v2.release_sha256,
        )
    )

    assert revised.module.release_sha256 != original.module.release_sha256
    assert profile == _profile("opus_5_xhigh", "claude-opus-5")


def test_non_agent_candidate_hashes_supplied_executable_content() -> None:
    behavior, evaluation, retry = _policies()
    candidate = NonAgentModuleReleaseCandidate(
        module_id="deterministic_module",
        module_version="v1",
        module_kind=ModuleKind.DETERMINISTIC,
        owner_contract_ref="host-source:design/deterministic@v1",
        owner_contract_content="# Deterministic owner\n",
        executable_ref="wheel-member:deterministic/module.py@v1",
        executable_content=b"def execute():\n    return 1\n",
        input_schema_ref="schema:deterministic_input@v1",
        input_schema_document=_schema("schema:deterministic_input@v1"),
        output_schema_ref="schema:deterministic_output@v1",
        output_schema_document=_schema("schema:deterministic_output@v1"),
        declared_operation_ids=(),
        compatible_transport_kinds=("in_process",),
        behavior_policy_ref=behavior.release_ref,
        behavior_policy_sha256=behavior.release_sha256,
        evaluation_policy_ref=evaluation.release_ref,
        evaluation_policy_sha256=evaluation.release_sha256,
        retry_policy_ref=retry.release_ref,
        retry_policy_sha256=retry.release_sha256,
    )
    compiled = compile_non_agent_module_release(candidate)
    revised = compile_non_agent_module_release(
        replace(
            candidate,
            module_version="v2",
            executable_content=b"def execute():\n    return 2\n",
        )
    )

    assert compiled.module.executable_sha256 != revised.module.executable_sha256
    assert compiled.schema_assets[0].release_ref == candidate.input_schema_ref
