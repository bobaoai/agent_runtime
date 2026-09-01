from __future__ import annotations

import builtins
from copy import deepcopy
from dataclasses import fields
import json
from pathlib import Path
from typing import Any

import pytest

from agent_runtime.contracts.registry_release_definition import (
    ModuleEntryPolicy,
    OutputResolutionPolicy,
)
from agent_runtime.foundation import (
    strict_output_schema_projection,
    validate_json_document_against_schema,
)
from agent_runtime.registry.registry_release_compilation import (
    AgentModuleReleaseCandidate,
    BehaviorPolicyReleaseCandidate,
    CompiledAgentModuleRelease,
    EvaluationPolicyReleaseCandidate,
    RetryPolicyReleaseCandidate,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_retry_policy_release,
    runtime_owned_policy_schema_assets,
    sha256_text,
)


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "digestion_evidence_router_candidate.json"
)


def _load_candidate_payload() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _compile_module_case(payload: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    policies = payload["policies"]
    module_payload = payload["module"]

    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(**policies["behavior"])
    )
    evaluation = compile_evaluation_policy_release(
        EvaluationPolicyReleaseCandidate(**policies["evaluation"])
    )
    retry = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(**policies["retry"])
    )
    compiled = compile_agent_module_release(
        AgentModuleReleaseCandidate(
            module_id=module_payload["module_id"],
            module_version=module_payload["module_version"],
            owner_contract_ref=module_payload["owner_contract_ref"],
            owner_contract_content=module_payload["owner_contract_content"],
            input_schema_ref=module_payload["input_schema_ref"],
            input_schema_document=json.dumps(
                module_payload["input_schema"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            output_schema_ref=module_payload["output_schema_ref"],
            output_schema_document=json.dumps(
                module_payload["output_schema"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            instruction_source_ref=module_payload["instruction_source_ref"],
            instruction_text=module_payload["instruction_text"],
            declared_operation_ids=tuple(module_payload["declared_operation_ids"]),
            compatible_transport_kinds=tuple(
                module_payload["compatible_transport_kinds"]
            ),
            behavior_policy_ref=behavior.release_ref,
            behavior_policy_sha256=behavior.release_sha256,
            evaluation_policy_ref=evaluation.release_ref,
            evaluation_policy_sha256=evaluation.release_sha256,
            retry_policy_ref=retry.release_ref,
            retry_policy_sha256=retry.release_sha256,
            entry_policy=ModuleEntryPolicy(module_payload["entry_policy"]),
            output_resolution_policy=OutputResolutionPolicy(
                module_payload["output_resolution_policy"]
            ),
        )
    )
    return compiled, behavior, evaluation, retry


def _module_schema_document(
    compiled: Any,
    release_ref: str,
    schema_sha256: str,
) -> dict[str, Any]:
    asset = next(
        item for item in compiled.schema_assets if item.release_ref == release_ref
    )
    assert asset.schema_sha256 == schema_sha256
    return asset.schema_document()


def test_fixed_json_candidate_compiles_without_registry_or_host_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _load_candidate_payload()
    original_read_text = Path.read_text
    original_read_bytes = Path.read_bytes
    original_path_open = Path.open
    original_builtin_open = builtins.open

    forbidden_host_parts = {".claude", ".agents", "designDoc", "runtime_modules"}

    def _is_host_authoring_path(value: object) -> bool:
        try:
            return bool(forbidden_host_parts.intersection(Path(value).parts))
        except TypeError:
            return False

    def _forbid_host_authoring_read(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> str:
        if _is_host_authoring_path(path):
            raise AssertionError(
                f"Module compilation attempted host authoring read: {path.as_posix()}"
            )
        return original_read_text(path, *args, **kwargs)

    def _forbid_host_authoring_bytes(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> bytes:
        if _is_host_authoring_path(path):
            raise AssertionError(
                f"Module compilation attempted host authoring read: {path.as_posix()}"
            )
        return original_read_bytes(path, *args, **kwargs)

    def _forbid_host_authoring_path_open(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> Any:
        if _is_host_authoring_path(path):
            raise AssertionError(
                f"Module compilation attempted host authoring read: {path.as_posix()}"
            )
        return original_path_open(path, *args, **kwargs)

    def _forbid_host_authoring_builtin_open(
        file: object,
        *args: object,
        **kwargs: object,
    ) -> Any:
        if _is_host_authoring_path(file):
            raise AssertionError(
                f"Module compilation attempted host authoring read: {file}"
            )
        return original_builtin_open(file, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _forbid_host_authoring_read)
    monkeypatch.setattr(Path, "read_bytes", _forbid_host_authoring_bytes)
    monkeypatch.setattr(Path, "open", _forbid_host_authoring_path_open)
    monkeypatch.setattr(builtins, "open", _forbid_host_authoring_builtin_open)
    with pytest.raises(AssertionError, match="host authoring read"):
        Path(".claude/skills/example/prompt.md").read_text(encoding="utf-8")
    first, behavior, evaluation, retry = _compile_module_case(payload)
    second, _, _, _ = _compile_module_case(payload)

    assert first == second
    assert first.module.behavior_policy_sha256 == behavior.release_sha256
    assert first.module.evaluation_policy_sha256 == evaluation.release_sha256
    assert first.module.retry_policy_sha256 == retry.release_sha256
    assert behavior.policy_document() == {
        "context_isolation": "workflow_execution_isolated"
    }
    assert evaluation.policy_document() == {"evaluation_mode": "module_candidate"}
    assert retry.policy_document() == {"max_attempts": 3}
    assert behavior.policy_schema_ref == "schema:runtime_behavior_policy@v1"
    assert evaluation.policy_schema_ref == "schema:runtime_evaluation_policy@v1"
    assert retry.policy_schema_ref == "schema:runtime_retry_policy@v1"
    policy_schemas = {
        schema.release_ref: schema
        for schema in runtime_owned_policy_schema_assets()
    }
    for release in (behavior, evaluation, retry):
        assert (
            release.policy_schema_sha256
            == policy_schemas[release.policy_schema_ref].schema_sha256
        )
        assert set(release.as_dict()) == {
            "policy_id",
            "policy_version",
            "release_ref",
            "policy_schema_ref",
            "policy_schema_sha256",
            "canonical_policy_json",
            "policy_sha256",
            "release_sha256",
        }
    assert first.module.owner_contract_sha256 == sha256_text(
        payload["module"]["owner_contract_content"]
    )

    assert {field.name for field in fields(AgentModuleReleaseCandidate)} == {
        "module_id",
        "module_version",
        "owner_contract_ref",
        "owner_contract_content",
        "input_schema_ref",
        "input_schema_document",
        "output_schema_ref",
        "output_schema_document",
        "instruction_source_ref",
        "instruction_text",
        "declared_operation_ids",
        "compatible_transport_kinds",
        "behavior_policy_ref",
        "behavior_policy_sha256",
        "evaluation_policy_ref",
        "evaluation_policy_sha256",
        "retry_policy_ref",
        "retry_policy_sha256",
        "entry_policy",
        "output_resolution_policy",
    }
    assert {field.name for field in fields(BehaviorPolicyReleaseCandidate)} == {
        "policy_id",
        "policy_version",
        "context_isolation",
    }
    assert {field.name for field in fields(EvaluationPolicyReleaseCandidate)} == {
        "policy_id",
        "policy_version",
        "evaluation_mode",
    }
    assert {field.name for field in fields(RetryPolicyReleaseCandidate)} == {
        "policy_id",
        "policy_version",
        "max_attempts",
    }
    assert set(first.module.as_dict()) == {
        "module_id",
        "module_version",
        "release_ref",
        "module_kind",
        "owner_contract_ref",
        "owner_contract_sha256",
        "executable_ref",
        "executable_sha256",
        "input_schema_ref",
        "input_schema_sha256",
        "output_schema_ref",
        "output_schema_sha256",
        "prompt_bundle_ref",
        "prompt_bundle_sha256",
        "declared_operation_ids",
        "behavior_policy_ref",
        "behavior_policy_sha256",
        "evaluation_policy_ref",
        "evaluation_policy_sha256",
        "retry_policy_ref",
        "retry_policy_sha256",
        "compatible_transport_kinds",
        "entry_policy",
        "output_resolution_policy",
        "release_sha256",
    }
    assert {field.name for field in fields(CompiledAgentModuleRelease)} == {
        "schema_assets",
        "prompt_components",
        "prompt_bundle",
        "module",
    }


def test_prompt_policy_and_module_io_contracts_are_closed() -> None:
    payload = _load_candidate_payload()
    compiled, _, _, _ = _compile_module_case(payload)
    module_payload = payload["module"]

    assert module_payload["instruction_text"] in compiled.prompt_bundle.compiled_static_body
    assert compiled.module.prompt_bundle_ref == compiled.prompt_bundle.release_ref
    assert compiled.module.prompt_bundle_sha256 == compiled.prompt_bundle.release_sha256
    assert tuple(
        (member.member_ref, member.member_sha256)
        for member in compiled.prompt_bundle.members
    ) == tuple(
        (component.release_ref, component.release_sha256)
        for component in compiled.prompt_components
    )

    assert compiled.module.input_schema_ref == module_payload["input_schema_ref"]
    assert compiled.module.output_schema_ref == module_payload["output_schema_ref"]
    input_schema = _module_schema_document(
        compiled,
        compiled.module.input_schema_ref,
        compiled.module.input_schema_sha256,
    )
    output_schema = _module_schema_document(
        compiled,
        compiled.module.output_schema_ref,
        compiled.module.output_schema_sha256,
    )
    strict_projection = json.dumps(
        strict_output_schema_projection(output_schema),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    assert strict_projection in compiled.prompt_bundle.compiled_static_body

    validate_json_document_against_schema(
        {
            "source_content": "Lumentum reported an accelerating 1.6T ramp.",
            "authorized_expertise": [
                {
                    "expertise_id": "optical_interconnect",
                    "description": "Optical interconnect architecture.",
                }
            ],
        },
        input_schema,
    )
    validate_json_document_against_schema(
        {
            "selected_routes": [
                {
                    "expertise_id": "optical_interconnect",
                    "lens_id": "optical_demand_and_product_mix",
                    "rationale": "The Source contains a direct product-mix signal.",
                }
            ],
            "unmatched_reason": None,
        },
        output_schema,
    )

    with pytest.raises(ValueError, match="does not satisfy"):
        validate_json_document_against_schema(
            {"authorized_expertise": []},
            input_schema,
        )
    with pytest.raises(ValueError, match="does not satisfy"):
        validate_json_document_against_schema(
            {
                "source_content": "x",
                "authorized_expertise": [],
                "invented_field": "x",
            },
            input_schema,
        )
    with pytest.raises(ValueError, match="does not satisfy"):
        validate_json_document_against_schema(
            {
                "selected_routes": [
                    {
                        "expertise_id": "optical_interconnect",
                        "lens_id": "invented_without_rationale",
                    }
                ],
                "unmatched_reason": None,
            },
            output_schema,
        )
    with pytest.raises(ValueError, match="does not satisfy"):
        validate_json_document_against_schema(
            {
                "selected_routes": [],
                "unmatched_reason": None,
                "invented_field": "x",
            },
            output_schema,
        )


@pytest.mark.parametrize(
    "mutation",
    ("instruction", "input_schema", "output_schema", "retry_policy"),
)
def test_module_hash_changes_with_semantic_content(mutation: str) -> None:
    baseline_payload = _load_candidate_payload()
    changed_payload = deepcopy(baseline_payload)

    if mutation == "instruction":
        changed_payload["module"]["instruction_text"] += " Preserve source scope."
    elif mutation == "input_schema":
        changed_payload["module"]["input_schema"]["properties"][
            "source_content"
        ]["minLength"] = 2
    elif mutation == "output_schema":
        changed_payload["module"]["output_schema"]["properties"][
            "selected_routes"
        ]["minItems"] = 1
    else:
        changed_payload["policies"]["retry"]["max_attempts"] = 5

    baseline, _, _, _ = _compile_module_case(baseline_payload)
    changed, _, _, _ = _compile_module_case(changed_payload)

    assert changed.module.release_sha256 != baseline.module.release_sha256


def test_invalid_schema_and_retry_policy_fail_closed() -> None:
    wrong_schema = _load_candidate_payload()
    wrong_schema["module"]["output_schema"]["$id"] = "schema:wrong@v1"
    with pytest.raises(ValueError, match="differs from release ref"):
        _compile_module_case(wrong_schema)

    invalid_schema = _load_candidate_payload()
    invalid_schema["module"]["output_schema"]["properties"][
        "unmatched_reason"
    ]["minLength"] = "two"
    with pytest.raises(ValueError, match="invalid Draft 2020-12 JSON Schema"):
        _compile_module_case(invalid_schema)

    invalid_retry = _load_candidate_payload()
    invalid_retry["policies"]["retry"]["max_attempts"] = 0
    with pytest.raises(ValueError, match="between 1 and 100"):
        _compile_module_case(invalid_retry)

    invalid_behavior = _load_candidate_payload()
    invalid_behavior["policies"]["behavior"][
        "context_isolation"
    ] = "shared_process"
    with pytest.raises(ValueError, match="does not satisfy its registered schema"):
        _compile_module_case(invalid_behavior)

    invalid_evaluation = _load_candidate_payload()
    invalid_evaluation["policies"]["evaluation"][
        "evaluation_mode"
    ] = "freeform"
    with pytest.raises(ValueError, match="does not satisfy its registered schema"):
        _compile_module_case(invalid_evaluation)
