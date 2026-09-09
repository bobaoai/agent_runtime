from __future__ import annotations

from pathlib import Path

import agent_runtime
from agent_runtime.contracts.registry_release_definition import (
    ModuleEntryPolicy,
    ModuleRelease,
)
from agent_runtime.registry import (
    AgentModuleReleaseCandidate,
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    RetryPolicyReleaseCandidate,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_retry_policy_release,
)


def _schema(schema_ref: str) -> str:
    return (
        '{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        f'"$id":"{schema_ref}","type":"object",'
        '"additionalProperties":false,"properties":{}}'
    )


def test_module_release_symbol_rename_preserves_persisted_identity() -> None:
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
    module = compile_agent_module_release(
        AgentModuleReleaseCandidate(
            module_id="module_release_symbol_case",
            module_version="v1",
            owner_contract_ref="test:module_release_symbol_owner@v1",
            owner_contract_content="# Owner\n",
            input_schema_ref="schema:module_release_symbol_input@v1",
            input_schema_document=_schema(
                "schema:module_release_symbol_input@v1"
            ),
            output_schema_ref="schema:module_release_symbol_output@v1",
            output_schema_document=_schema(
                "schema:module_release_symbol_output@v1"
            ),
            instruction_source_ref="test:module_release_symbol_prompt@v1",
            instruction_text="Return one typed result.\n",
            declared_operation_ids=("model_execute",),
            compatible_transport_kinds=("claude_agent_sdk",),
            behavior_policy_ref=behavior.release_ref,
            behavior_policy_sha256=behavior.release_sha256,
            evaluation_policy_ref=evaluation.release_ref,
            evaluation_policy_sha256=evaluation.release_sha256,
            retry_policy_ref=retry.release_ref,
            retry_policy_sha256=retry.release_sha256,
            entry_policy=ModuleEntryPolicy.STANDALONE_ALLOWED,
        )
    ).module
    assert type(module) is ModuleRelease
    assert module.record_type == "runtime_module_release"
    assert module.release_ref == "runtime-module:module_release_symbol_case@v1"
    assert module.release_sha256 == (
        "f5efbb3c5701ccd9cfeb864478bcaad7efa70b66228889d181fe545ea9396e69"
    )


def test_removed_runtime_module_release_symbol_has_no_alias() -> None:
    assert "ModuleRelease" in agent_runtime.__all__
    assert "RuntimeModuleRelease" not in agent_runtime.__all__
    assert not hasattr(agent_runtime, "RuntimeModuleRelease")

    package_root = Path(agent_runtime.__file__).resolve().parent
    surviving_sources = [
        path
        for path in package_root.rglob("*.py")
        if "RuntimeModuleRelease" in path.read_text(encoding="utf-8")
    ]
    assert surviving_sources == []
