from __future__ import annotations

from dataclasses import fields, replace
import json
import hashlib

import pytest

from agent_runtime.contracts.registry_release_definition import (
    ModuleKind, ModuleRelease, ModuleExecutionRequirements, ReviewerDefaults,
)
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


def _execution_requirements():
    return ModuleExecutionRequirements(
        context_isolation="workflow_execution_isolated", execution_mode="agent",
        semantic_input_delivery_mode="inline", attempt_workspace_policy="own_draft_read_write",
        tool_policy=("read", "search", "shell"), gateway_access_reasons=(),
        network_policy="denied", output_constraint_mode="native_structured_output",
        timeout_seconds=1200, max_attempts=3,
    )


@pytest.mark.parametrize("snapshot_version", [None, "v1", "v2"])
def test_legacy_module_codec_and_requirements_view_preserve_exact_payload(snapshot_version):
    snapshot = None if snapshot_version is None else ReviewerDefaults(version=snapshot_version)
    candidate = replace(_agent_candidate(), reviewer_defaults=snapshot)
    module = compile_agent_module_release(candidate).module
    payload = module.as_dict()
    before = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert "execution_requirements" not in payload
    assert payload["compatible_transport_kinds"] == ["claude_agent_sdk", "codex_cli"]
    if snapshot_version is None:
        assert "reviewer_defaults" not in payload
    else:
        assert payload["reviewer_defaults"]["version"] == snapshot_version
        ReviewerDefaults.from_dict(payload["reviewer_defaults"]).validate()
    restored = ModuleRelease.from_dict(json.loads(before))
    restored.validate()
    # Produced by this unchanged _agent_candidate fixture and the original
    # compiler in a cold process at commit 5218e06801109ef1b2e4469d7b0584488431d27a.
    # A second cold process using the new reader independently confirmed them.
    golden_hashes = {
        None: "2df9170175d4ee371162bc5f8b5dc13dd6de529e4bc87be41c59b6526b2dd29c",
        "v1": "0bf719e11e3d74d1f8618af545d46034cfce86adcc2838e7c46fe5be19043e3f",
        "v2": "653832288991bdd72ac0d80262a1aa5d87bfbc0d8a14c893cc229b5d1210c264",
    }
    assert restored.release_sha256 == golden_hashes[snapshot_version]
    requirements = restored.get_execution_requirements()
    assert requirements == (None if snapshot is None else _execution_requirements())
    assert json.dumps(restored.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) == before
    assert restored.release_ref == module.release_ref
    assert restored.release_sha256 == module.release_sha256
    expected_hash = hashlib.sha256(json.dumps(
        {k: v for k, v in payload.items() if k != "release_sha256"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    assert restored.release_sha256 == expected_hash


def test_new_requirements_codec_excludes_legacy_keys_and_rejects_mixed_payloads():
    from agent_runtime.registry import RuntimeReleaseBundle
    candidate = replace(_agent_candidate(), module_version="requirements_v1", compatible_transport_kinds=(),
                        execution_requirements=_execution_requirements())
    module = compile_agent_module_release(candidate).module
    payload = module.as_dict()
    assert "reviewer_defaults" not in payload
    assert "compatible_transport_kinds" not in payload
    assert payload["execution_requirements"] == _execution_requirements().as_dict()
    assert ModuleRelease.from_dict(payload) == module
    assert module.get_execution_requirements() == _execution_requirements()
    old_plain = compile_agent_module_release(_agent_candidate()).module
    old_snapshot = compile_agent_module_release(replace(
        _agent_candidate(), module_version="old_snapshot",
        reviewer_defaults=ReviewerDefaults(version="v2"))).module
    bundle = RuntimeReleaseBundle(modules=(old_plain, old_snapshot, module))
    assert RuntimeReleaseBundle.from_dict(bundle.as_dict()).as_dict() == bundle.as_dict()
    for name, value in (("reviewer_defaults", None), ("compatible_transport_kinds", [])):
        with pytest.raises(ValueError, match="shape"):
            ModuleRelease.from_dict({**payload, name: value})
    altered = {**payload, "execution_requirements": {
        **payload["execution_requirements"], "timeout_seconds": 1000}}
    with pytest.raises(ValueError, match="hash mismatch"):
        ModuleRelease.from_dict(altered).validate()


@pytest.mark.parametrize("field", ModuleExecutionRequirements._fields)
def test_requirement_decoder_does_not_fill_missing_fields(field):
    payload = _execution_requirements().as_dict()
    payload.pop(field)
    with pytest.raises(ValueError, match="shape"):
        ModuleExecutionRequirements.from_dict(payload)


@pytest.mark.parametrize("value", [None, [], {}, {"version": "v1"}])
def test_invalid_or_unknown_requirement_shape_is_rejected(value):
    with pytest.raises(ValueError):
        ModuleExecutionRequirements.from_dict(value)


def test_compiler_rejects_mixed_new_and_legacy_capabilities():
    for changes in (
        {"compatible_transport_kinds": ("claude_cli",)},
        {"reviewer_defaults": ReviewerDefaults()},
    ):
        candidate = replace(_agent_candidate(), compatible_transport_kinds=(),
                            execution_requirements=_execution_requirements())
        with pytest.raises(ValueError, match="cannot mix"):
            compile_agent_module_release(replace(candidate, **changes))


def test_legacy_timeout_is_not_clamped_by_requirements_mapping():
    module = compile_agent_module_release(replace(
        _agent_candidate(), reviewer_defaults=ReviewerDefaults(timeout_seconds=86401))).module
    before = module.as_dict()
    with pytest.raises(ValueError):
        module.get_execution_requirements()
    assert module.as_dict() == before


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
        "model_defaults_version",
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
