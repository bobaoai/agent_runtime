from __future__ import annotations

import json
from pathlib import Path
import re

from jsonschema import Draft202012Validator

from agent_runtime.registry.registry_module_loading import (
    load_runtime_module_registration,
)
from agent_runtime.registry.registry_release_compilation import (
    AgentModuleReleaseSpec,
    compile_agent_module_release,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
ENGINEERING_MODULE_ROOT = (
    REPO_ROOT
    / "09_soul/governance/skills/engineering-change-review/runtime_modules"
    / "engineering_change_reviewer"
)
DESIGN_PROMPT = (
    REPO_ROOT
    / "09_soul/governance/skills/the-contract-audit/runtime_modules"
    / "design_contract_reviewer/prompt.md"
)


def _schema(name: str) -> dict[str, object]:
    return json.loads(
        (ENGINEERING_MODULE_ROOT / "schemas" / name).read_text(encoding="utf-8")
    )


def test_engineering_change_reviewer_loads_from_canonical_projection() -> None:
    registration = load_runtime_module_registration(
        REPO_ROOT,
        skill_id="engineering-change-review",
        module_id="engineering_change_reviewer",
    )

    assert registration.owner_contract_path == "designDoc/the_software_delivery.md"
    assert registration.input_schema_path == (
        ".claude/skills/engineering-change-review/runtime_modules/"
        "engineering_change_reviewer/schemas/input.schema.json"
    )
    assert registration.output_schema_path == (
        ".claude/skills/engineering-change-review/runtime_modules/"
        "engineering_change_reviewer/schemas/output.schema.json"
    )


def test_engineering_change_reviewer_compiles_as_one_runtime_module() -> None:
    compiled = compile_agent_module_release(
        REPO_ROOT,
        AgentModuleReleaseSpec(
            module_id="engineering_change_reviewer",
            skill_id="engineering-change-review",
            skill_projection_path=(
                ".claude/skills/engineering-change-review/runtime_modules/"
                "engineering_change_reviewer/prompt.md"
            ),
            owner_contract_ref="repo-file:designDoc/the_software_delivery.md",
            owner_contract_path="designDoc/the_software_delivery.md",
            input_schema_ref="schema:engineering_change_reviewer_input@v1",
            output_schema_ref="schema:engineering_change_reviewer_output@v1",
            declared_operation_ids=("model_execute",),
            execution_profile_id="engineering_change_review_opus_5_xhigh",
            executor_adapter_id="claude_agent_sdk",
            executor_adapter_revision="v1",
            transport_kind="claude_agent_sdk",
            provider_id="anthropic",
            model_id="claude-opus-5",
            reasoning_profile="xhigh",
            output_constraint_mode="native_structured_output",
            timeout_seconds=1800,
            input_schema_path=(
                ".claude/skills/engineering-change-review/runtime_modules/"
                "engineering_change_reviewer/schemas/input.schema.json"
            ),
            output_schema_path=(
                ".claude/skills/engineering-change-review/runtime_modules/"
                "engineering_change_reviewer/schemas/output.schema.json"
            ),
            execution_mode="agent",
            compatible_transport_kinds=("claude_agent_sdk", "codex_cli"),
            context_policy_ref="context-policy:repository_review_isolated@v1",
            evaluation_policy_ref="evaluation-policy:engineering_change_review@v1",
            retry_policy_ref="retry-policy:bounded_candidate@v1",
        ),
    )

    assert compiled.module.source_skill_id == "engineering-change-review"
    assert compiled.module.owner_contract_ref == (
        "repo-file:designDoc/the_software_delivery.md"
    )
    assert compiled.module.release_ref == (
        "runtime-module:engineering_change_reviewer@candidate_v1"
    )
    assert tuple(asset.release_ref for asset in compiled.schema_assets) == (
        "schema:engineering_change_reviewer_input@v1",
        "schema:engineering_change_reviewer_output@v1",
    )


def test_engineering_change_reviewer_schemas_fix_task_plane_boundaries() -> None:
    input_schema = _schema("input.schema.json")
    output_schema = _schema("output.schema.json")
    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator.check_schema(output_schema)

    expected_checks = {
        "subject_closure",
        "gate_reproduction",
        "design_conformance",
        "architecture_and_dependency",
        "contract_and_projection",
        "failure_recovery_and_rollback",
        "prior_finding_closure",
        "review_and_admission_separation",
    }
    check_schema = output_schema["properties"]["check_results"]
    assert set(check_schema["properties"]) == expected_checks
    assert set(check_schema["required"]) == expected_checks
    assert check_schema["additionalProperties"] is False

    output_properties = set(output_schema["properties"])
    assert "subject_mode" not in output_properties
    assert "subject_sha256" not in output_properties
    assert "provider_id" not in output_properties
    assert output_schema["properties"]["review_disposition"]["enum"] == [
        "pass",
        "changes_required",
        "not_reproducible",
    ]


def test_reviewer_prompts_keep_design_and_implementation_patterns_separate() -> None:
    design_prompt = DESIGN_PROMPT.read_text(encoding="utf-8")
    engineering_prompt = (ENGINEERING_MODULE_ROOT / "prompt.md").read_text(
        encoding="utf-8"
    )

    design_patterns = set(re.findall(r"design_pattern\.[a-z_]+", design_prompt))
    engineering_patterns = set(
        re.findall(r"engineering_pattern\.[a-z_]+", engineering_prompt)
    )
    assert design_patterns == {
        "design_pattern.dimension_mixing",
        "design_pattern.incomplete_peer_comparison",
        "design_pattern.duplicated_authority",
        "design_pattern.mutable_truth_leakage",
        "design_pattern.unjustified_abstraction",
        "design_pattern.ceremony_over_result",
    }
    assert engineering_patterns == {
        "engineering_pattern.incomplete_subject_closure",
        "engineering_pattern.projection_drift",
        "engineering_pattern.late_validation",
        "engineering_pattern.ambient_boundary_escape",
        "engineering_pattern.architecture_bypass",
        "engineering_pattern.private_dependency",
        "engineering_pattern.prose_only_enforcement",
        "engineering_pattern.ineffective_test_gate",
        "engineering_pattern.failure_contract_drift",
        "engineering_pattern.failure_misattribution",
    }
    assert "engineering_pattern." not in design_prompt
    assert "design_pattern." not in engineering_prompt
