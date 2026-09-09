from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from agent_runtime import (
    EXECUTION_PROFILE_UNAVAILABLE,
    MODULE_EXECUTION_PROFILE_INCOMPATIBLE,
    MODULE_OPERATION_DECLARATION_INVALID,
    Module,
    ModuleAuthoringError,
    ModuleEntryPolicy,
    ModuleExecutionPurpose,
    ModuleReviewer,
)
from agent_runtime.registry import (
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    ExecutionProfileReleaseSpec,
    RetryPolicyReleaseCandidate,
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_execution_profile_release,
    compile_retry_policy_release,
    load_module_registration,
    runtime_owned_policy_schema_assets,
)
from agent_runtime.contracts.registry_release_definition import (
    partition_module_operation_ids,
)
from agent_runtime.execution import execution_module_invocation
from agent_runtime.registry import registry_module_authoring


SKILL_ID = "test-design-authoring"
MODULE_ID = "test_design_reviewer"


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
    ) + "\n"


def _reviewer_output_schema(schema_ref: str) -> str:
    string = {"type": "string", "minLength": 1}
    return json.dumps({
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": schema_ref,
        "type": "object", "additionalProperties": False,
        "$defs": {
            "evidence": {"type": "object", "additionalProperties": False,
                "properties": {name: string for name in ("source_ref", "locator", "observation")},
                "required": ["source_ref", "locator", "observation"]},
            "finding": {"type": "object", "additionalProperties": False,
                "properties": {"finding_id": string,
                    "severity": {**string, "enum": ["block", "fix", "note"]},
                    "evidence": {"$ref": "#/$defs/evidence"}, "requirement": string,
                    "impact": string, "accountable_owner_ref": string, "required_change": string},
                "required": ["finding_id", "severity", "evidence", "requirement", "impact", "accountable_owner_ref", "required_change"]},
            "check_result": {"type": "object", "additionalProperties": False,
                "properties": {"check_id": string,
                    "disposition": {**string, "enum": ["passed", "finding", "not_applicable", "not_run"]},
                    "assessment": string, "finding_ids": {"type": "array", "uniqueItems": True, "items": string}},
                "required": ["check_id", "disposition", "assessment", "finding_ids"]}},
        "properties": {"verdict": {**string, "enum": ["passed", "non_pass", "blocked"]},
            "check_results": {"type": "array", "items": {"$ref": "#/$defs/check_result"}},
            "findings": {"type": "array", "items": {"$ref": "#/$defs/finding"}},
            "safe_next_step": string},
        "required": ["verdict", "check_results", "findings", "safe_next_step"]},
        sort_keys=True, separators=(",", ":")) + "\n"


def _project(tmp_path: Path) -> Path:
    project_root = tmp_path / "project"
    module_root = (
        project_root
        / ".claude/skills"
        / SKILL_ID
        / "runtime_modules"
        / MODULE_ID
    )
    (module_root / "schemas").mkdir(parents=True)
    (module_root / "tests").mkdir()
    (project_root / "designDoc").mkdir()
    (project_root / ".claude/skills" / SKILL_ID / "SKILL.md").write_text(
        f"# Test Skill\n\nMust call `{MODULE_ID}` independently.\n",
        encoding="utf-8",
    )
    (project_root / "designDoc/test_design.md").write_text(
        f"# Test Design\n\nOwns `{MODULE_ID}`.\n",
        encoding="utf-8",
    )
    (module_root / "prompt.md").write_text(
        "Review one exact frozen subject and return typed JSON.\n",
        encoding="utf-8",
    )
    input_ref = f"schema:{MODULE_ID}_input@v1"
    output_ref = f"schema:{MODULE_ID}_output@v1"
    (module_root / "schemas/input.schema.json").write_text(
        _schema(input_ref), encoding="utf-8"
    )
    (module_root / "schemas/output.schema.json").write_text(
        _reviewer_output_schema(output_ref), encoding="utf-8"
    )
    (module_root / "tests/positive_case.json").write_text(
        "{}\n", encoding="utf-8"
    )
    registration = {
        "schema_version": "runtime_module_registration_v2",
        "skill_id": SKILL_ID,
        "module_id": MODULE_ID,
        "owner_contract_path": "designDoc/test_design.md",
        "input_schema_ref": input_ref,
        "input_schema_path": "schemas/input.schema.json",
        "output_schema_ref": output_ref,
        "output_schema_path": "schemas/output.schema.json",
        "declared_operation_ids": ["model_execute"],
        "compatible_transport_kinds": ["claude_agent_sdk", "codex_cli"],
        "behavior_policy_ref": "behavior-policy:workflow_execution_isolated@v1",
        "evaluation_policy_ref": "evaluation-policy:module_candidate@v1",
        "retry_policy_ref": "retry-policy:bounded_candidate@v1",
        "entry_policy": "standalone_allowed",
        "output_resolution_policy": "evaluated_single",
    }
    (module_root / "module_registration.json").write_text(
        json.dumps(registration, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return project_root


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


def _profile(*, model_id: str = "claude-opus-5"):
    return compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
            execution_profile_id=f"profile_{MODULE_ID}_opus_5_xhigh_v1",
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


def _gateway_profile(*, tool_policy: tuple[str, ...] = ("repository_read",)):
    return compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
            execution_profile_id=f"profile_{MODULE_ID}_gateway_v1",
            executor_adapter_id="claude_agent_sdk_gateway_executor",
            executor_adapter_revision="v3",
            transport_kind="claude_agent_sdk",
            provider_id="anthropic",
            model_id="claude-opus-5",
            reasoning_profile="xhigh",
            execution_mode="agent",
            semantic_input_delivery_mode="gateway_read",
            attempt_workspace_policy="none",
            gateway_access_reasons=("authorized_package_external_exploration",),
            output_constraint_mode="native_structured_output",
            tool_policy=tool_policy,
            network_policy="gateway_only",
            timeout_seconds=900,
            release_version="v1",
        )
    )


def _source_hashes(project_root: Path) -> dict[str, str]:
    return {
        path.relative_to(project_root).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in project_root.rglob("*")
        if path.is_file()
    }


def test_runtime_loads_one_exact_module_registration(tmp_path: Path) -> None:
    project_root = _project(tmp_path)

    source = load_module_registration(
        project_root,
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )

    assert source.skill_id == SKILL_ID
    assert source.module_id == MODULE_ID
    assert source.behavior_policy_ref == (
        "behavior-policy:workflow_execution_isolated@v1"
    )
    assert source.instruction_source_ref == (
        f"skill-instruction:{SKILL_ID}:{MODULE_ID}"
    )
    assert source.owner_contract_ref.startswith("owner-contract-sha256:")
    assert not hasattr(source, "owner_contract_path")


def test_authoring_and_execution_share_module_operation_classification() -> None:
    assert (
        registry_module_authoring.partition_module_operation_ids
        is partition_module_operation_ids
    )
    assert (
        execution_module_invocation.partition_module_operation_ids
        is partition_module_operation_ids
    )
    assert not hasattr(registry_module_authoring, "TOOL_FREE_OPERATION_IDS")
    assert not hasattr(
        execution_module_invocation,
        "_MODEL_INVOCATION_OPERATION_IDS",
    )


def test_owner_file_relocation_does_not_change_module_candidate_or_release(
    tmp_path: Path,
) -> None:
    project_root = _project(tmp_path)
    behavior, evaluation, retry = _policies()
    original = ModuleReviewer.from_registration(
        project_root,
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    ).export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=None,
    )
    original_path = project_root / "designDoc/test_design.md"
    relocated_path = project_root / "designDoc/relocated_design.md"
    original_path.rename(relocated_path)
    registration_path = (
        project_root
        / ".claude/skills"
        / SKILL_ID
        / "runtime_modules"
        / MODULE_ID
        / "module_registration.json"
    )
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    registration["owner_contract_path"] = "designDoc/relocated_design.md"
    registration_path.write_text(
        json.dumps(registration, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    relocated = ModuleReviewer.from_registration(
        project_root,
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    ).export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=None,
    )

    assert relocated.source.owner_contract_ref == original.source.owner_contract_ref
    assert relocated.candidate == original.candidate
    assert relocated.module_release == original.module_release


def test_loader_rejects_retired_field_wrong_schema_and_missing_owner_declaration(
    tmp_path: Path,
) -> None:
    project_root = _project(tmp_path)
    registration_path = (
        project_root
        / ".claude/skills"
        / SKILL_ID
        / "runtime_modules"
        / MODULE_ID
        / "module_registration.json"
    )
    original = json.loads(registration_path.read_text(encoding="utf-8"))

    retired = dict(original)
    retired["context_policy_ref"] = retired.pop("behavior_policy_ref")
    registration_path.write_text(json.dumps(retired), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid v2 shape"):
        load_module_registration(project_root, skill_id=SKILL_ID, module_id=MODULE_ID)

    wrong_schema = dict(original)
    wrong_schema["input_schema_ref"] = "schema:other_input@v1"
    registration_path.write_text(json.dumps(wrong_schema), encoding="utf-8")
    with pytest.raises(ValueError, match="must use schema:test_design_reviewer_input"):
        load_module_registration(project_root, skill_id=SKILL_ID, module_id=MODULE_ID)

    registration_path.write_text(json.dumps(original), encoding="utf-8")
    (project_root / "designDoc/test_design.md").write_text(
        "# Owner without declaration\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="owner Design Doc does not declare"):
        load_module_registration(project_root, skill_id=SKILL_ID, module_id=MODULE_ID)


@pytest.mark.parametrize(
    "relative_path",
    (
        "module_registration.json",
        "prompt.md",
        "schemas/input.schema.json",
        "schemas/output.schema.json",
    ),
)
def test_loader_rejects_symlinked_fixed_module_sources(
    tmp_path: Path,
    relative_path: str,
) -> None:
    project_root = _project(tmp_path)
    module_root = (
        project_root
        / ".claude/skills"
        / SKILL_ID
        / "runtime_modules"
        / MODULE_ID
    )
    source_path = module_root / relative_path
    external = tmp_path / f"external_{source_path.name}"
    external.write_bytes(source_path.read_bytes())
    source_path.unlink()
    source_path.symlink_to(external)

    with pytest.raises(ValueError, match="symlink"):
        load_module_registration(project_root, skill_id=SKILL_ID, module_id=MODULE_ID)


def test_loader_rejects_owner_path_traversal(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    registration_path = (
        project_root
        / ".claude/skills"
        / SKILL_ID
        / "runtime_modules"
        / MODULE_ID
        / "module_registration.json"
    )
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    registration["owner_contract_path"] = "../outside.md"
    registration_path.write_text(json.dumps(registration), encoding="utf-8")

    with pytest.raises(ValueError, match="must stay inside the repository"):
        load_module_registration(project_root, skill_id=SKILL_ID, module_id=MODULE_ID)


def test_loader_reads_only_the_selected_module_closure(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    sibling = (
        project_root
        / ".claude/skills"
        / SKILL_ID
        / "runtime_modules"
        / "unselected_reviewer"
    )
    sibling.mkdir()
    (sibling / "invalid_unselected_source").write_text(
        "This sibling is intentionally not a valid Module.\n",
        encoding="utf-8",
    )

    source = load_module_registration(
        project_root,
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )

    assert source.module_id == MODULE_ID


def test_module_reviewer_exports_release_and_profile_independently(
    tmp_path: Path,
) -> None:
    project_root = _project(tmp_path)
    reviewer = ModuleReviewer.from_registration(
        project_root,
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    behavior, evaluation, retry = _policies()

    first = reviewer.export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=_profile(),
    )
    revised = reviewer.export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=_profile(model_id="claude-opus-5-next"),
    )

    assert isinstance(reviewer, Module)
    assert first.module_release.release_sha256 == revised.module_release.release_sha256
    assert first.execution_variant is not None
    assert revised.execution_variant is not None
    assert first.execution_variant.release_sha256 != (
        revised.execution_variant.release_sha256
    )


def test_module_reviewer_no_profile_is_candidate_with_blocker(tmp_path: Path) -> None:
    reviewer = ModuleReviewer.from_registration(
        _project(tmp_path),
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    behavior, evaluation, retry = _policies()

    exported = reviewer.export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=None,
    )

    assert exported.execution_profile is None
    assert exported.execution_variant is None
    assert exported.execution_blocker_code == EXECUTION_PROFILE_UNAVAILABLE


def test_module_reviewer_export_always_rejects_legacy_output_format(
    tmp_path: Path,
) -> None:
    project_root = _project(tmp_path)
    output_path = (
        project_root / ".claude/skills" / SKILL_ID / "runtime_modules"
        / MODULE_ID / "schemas/output.schema.json"
    )
    output_path.write_text(
        _schema(f"schema:{MODULE_ID}_output@v1"), encoding="utf-8"
    )
    reviewer = ModuleReviewer.from_registration(
        project_root, skill_id=SKILL_ID, module_id=MODULE_ID
    )
    behavior, evaluation, retry = _policies()

    with pytest.raises(ValueError, match="top-level"):
        reviewer.export(
            module_version="v1",
            behavior_policy=behavior,
            evaluation_policy=evaluation,
            retry_policy=retry,
            execution_profile=None,
        )


def test_module_reviewer_rejects_tool_profile_mismatch(tmp_path: Path) -> None:
    reviewer = ModuleReviewer.from_registration(
        _project(tmp_path),
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    reviewer = ModuleReviewer(
        source=replace(
            reviewer.source,
            declared_operation_ids=("model_execute", "repository_read"),
        )
    )
    behavior, evaluation, retry = _policies()

    with pytest.raises(ModuleAuthoringError) as exc_info:
        reviewer.export(
            module_version="v1",
            behavior_policy=behavior,
            evaluation_policy=evaluation,
            retry_policy=retry,
            execution_profile=_profile(),
        )
    assert exc_info.value.error_code == MODULE_EXECUTION_PROFILE_INCOMPATIBLE


def test_module_reviewer_accepts_exact_gateway_tool_profile(tmp_path: Path) -> None:
    reviewer = ModuleReviewer.from_registration(
        _project(tmp_path),
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    reviewer = ModuleReviewer(
        source=replace(
            reviewer.source,
            declared_operation_ids=("model_execute", "repository_read"),
        )
    )
    behavior, evaluation, retry = _policies()

    exported = reviewer.export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=_gateway_profile(),
    )

    assert exported.execution_blocker_code is None
    assert exported.execution_variant is not None
    assert exported.execution_profile == _gateway_profile()


def test_module_reviewer_rejects_profile_with_undeclared_tool(
    tmp_path: Path,
) -> None:
    reviewer = ModuleReviewer.from_registration(
        _project(tmp_path),
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    reviewer = ModuleReviewer(
        source=replace(
            reviewer.source,
            declared_operation_ids=("model_execute", "repository_read"),
        )
    )
    behavior, evaluation, retry = _policies()

    with pytest.raises(ModuleAuthoringError) as exc_info:
        reviewer.export(
            module_version="v1",
            behavior_policy=behavior,
            evaluation_policy=evaluation,
            retry_policy=retry,
            execution_profile=_gateway_profile(
                tool_policy=("repository_read", "repository_search")
            ),
        )
    assert exc_info.value.error_code == MODULE_EXECUTION_PROFILE_INCOMPATIBLE


def test_module_reviewer_rejects_profile_transport_mismatch(
    tmp_path: Path,
) -> None:
    reviewer = ModuleReviewer.from_registration(
        _project(tmp_path),
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    reviewer = ModuleReviewer(
        source=replace(
            reviewer.source,
            compatible_transport_kinds=("codex_cli",),
        )
    )
    behavior, evaluation, retry = _policies()

    with pytest.raises(ModuleAuthoringError) as exc_info:
        reviewer.export(
            module_version="v1",
            behavior_policy=behavior,
            evaluation_policy=evaluation,
            retry_policy=retry,
            execution_profile=_profile(),
        )
    assert exc_info.value.error_code == MODULE_EXECUTION_PROFILE_INCOMPATIBLE


def test_module_reviewer_rejects_invalid_model_operation_declaration(
    tmp_path: Path,
) -> None:
    reviewer = ModuleReviewer.from_registration(
        _project(tmp_path),
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    reviewer = ModuleReviewer(
        source=replace(
            reviewer.source,
            declared_operation_ids=("repository_read",),
        )
    )
    behavior, evaluation, retry = _policies()

    with pytest.raises(ModuleAuthoringError) as exc_info:
        reviewer.export(
            module_version="v1",
            behavior_policy=behavior,
            evaluation_policy=evaluation,
            retry_policy=retry,
            execution_profile=_gateway_profile(),
        )
    assert exc_info.value.error_code == MODULE_OPERATION_DECLARATION_INVALID


def test_module_export_origin_bundle_excludes_profile_and_variant(
    tmp_path: Path,
) -> None:
    reviewer = ModuleReviewer.from_registration(
        _project(tmp_path),
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    behavior, evaluation, retry = _policies()
    exported = reviewer.export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=_profile(),
    )

    origin = exported.origin_bundle
    assert origin.modules == (exported.module_release,)
    assert origin.behavior_policies == (behavior,)
    assert origin.evaluation_policies == (evaluation,)
    assert origin.retry_policies == (retry,)
    assert origin.execution_profiles == ()
    assert origin.execution_variant_policies == ()


def test_project_resolves_registered_facts_and_writes_nothing(tmp_path: Path) -> None:
    project_root = _project(tmp_path)
    before = _source_hashes(project_root)
    reviewer = ModuleReviewer.from_registration(
        project_root,
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    behavior, evaluation, retry = _policies()
    profile = _profile()
    exported = reviewer.export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=profile,
    )
    assert exported.execution_variant is not None
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(
                *runtime_owned_policy_schema_assets(),
                *exported.compiled.schema_assets,
            ),
            prompt_components=exported.compiled.prompt_components,
            prompt_bundles=(exported.compiled.prompt_bundle,),
            behavior_policies=(behavior,),
            evaluation_policies=(evaluation,),
            retry_policies=(retry,),
            execution_profiles=(profile,),
            modules=(exported.module_release,),
            execution_variant_policies=(exported.execution_variant,),
        )
    )

    projection = reviewer.project(registry, exported)

    assert _source_hashes(project_root) == before
    assert projection["skill_id"] == SKILL_ID
    assert projection["module_release_ref"] == exported.module_release.release_ref
    assert projection["execution_profile"] == {
        "release_ref": profile.release_ref,
        "release_sha256": profile.release_sha256,
    }
    assert projection["execution_variant"] == {
        "release_ref": exported.execution_variant.release_ref,
        "release_sha256": exported.execution_variant.release_sha256,
    }
    assert projection["active"] is False
    registry.assert_module_execution_allowed(
        exported.module_release,
        ModuleExecutionPurpose.TEST,
    )
    assert exported.module_release.entry_policy is ModuleEntryPolicy.STANDALONE_ALLOWED
