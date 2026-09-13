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
    ModuleExecutionRequirements,
    OutputResolutionPolicy,
    load_reviewer_registration,
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


def _task_project(tmp_path: Path, *, module_id: str = MODULE_ID) -> Path:
    """Explicit v4 fixture; _project remains the unchanged legacy consumer source."""
    project = _project(tmp_path)
    parent = project / ".claude/skills" / SKILL_ID / "runtime_modules"
    old_root = parent / MODULE_ID
    target = parent / module_id
    if target != old_root:
        old_root.rename(target)
        for p in (project / ".claude/skills" / SKILL_ID / "SKILL.md",
                  project / "designDoc/test_design.md"):
            p.write_text(p.read_text().replace(MODULE_ID, module_id))
    registration_path = target / "module_registration.json"
    old_registration = json.loads(registration_path.read_text())
    fields = ("schema_version", "skill_id", "module_id", "owner_contract_path",
              "input_schema_ref", "input_schema_path", "output_schema_ref", "output_schema_path")
    payload = {key: old_registration[key] for key in fields}
    payload["schema_version"] = "runtime_module_registration_v4"
    payload["module_id"] = module_id
    for direction in ("input", "output"):
        ref = f"schema:{module_id}_{direction}@v1"
        payload[f"{direction}_schema_ref"] = ref
        p = target / f"schemas/{direction}.schema.json"
        document = json.loads(p.read_text())
        document["$id"] = ref
        if module_id == "summarize_note" and direction == "output":
            document = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                        "$id": ref, "type": "object", "additionalProperties": False,
                        "properties": {"summary": {"type": "string"}},
                        "required": ["summary"]}
        p.write_text(json.dumps(document) + "\n")
    registration_path.write_text(json.dumps(payload, sort_keys=True) + "\n")
    if module_id == "summarize_note":
        (target / "prompt.md").write_text("Summarize the provided note.\n")
    return project


def _requirements(**changes):
    values = dict(context_isolation="workflow_execution_isolated", execution_mode="agent",
                  semantic_input_delivery_mode="inline",
                  attempt_workspace_policy="own_draft_read_write",
                  tool_policy=("read", "search", "shell"), gateway_access_reasons=(),
                  network_policy="denied", output_constraint_mode="native_structured_output",
                  timeout_seconds=1200, max_attempts=3)
    return ModuleExecutionRequirements(**{**values, **changes})


def _native_profile(**changes):
    base = _profile()
    values = dict(execution_mode="agent", semantic_input_delivery_mode="inline",
                  attempt_workspace_policy="own_draft_read_write",
                  tool_policy=("read", "search", "shell"), gateway_access_reasons=(),
                  network_policy="denied", timeout_seconds=1200)
    candidate = replace(base, **{**values, **changes})
    return type(base).build(**{
        **candidate._payload(), "tool_policy": candidate.tool_policy,
        "gateway_access_reasons": candidate.gateway_access_reasons,
    })



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
    project_root = _task_project(tmp_path)
    behavior, evaluation, retry = _policies()
    original = ModuleReviewer.from_registration(
        project_root,
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    ).export(
        module_version="v1",
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


def test_ordinary_module_loads_exports_and_projects_non_review_schema(tmp_path, monkeypatch):
    from agent_runtime.registry import registry_reviewer_defaults
    from agent_runtime.execution import execution_local_invocation
    project = _task_project(tmp_path, module_id="summarize_note")
    before = _source_hashes(project)
    def forbidden(*args, **kwargs):
        pytest.fail("ordinary authoring must not use Reviewer format or model defaults")
    monkeypatch.setattr(registry_reviewer_defaults, "_validate_reviewer_output_schema", forbidden)
    monkeypatch.setattr(execution_local_invocation, "_execution_profile_for_requirements", forbidden)
    ordinary = Module.from_registration(project, skill_id=SKILL_ID,
        module_id="summarize_note", execution_requirements=_requirements())
    exported = ordinary.export(module_version="v1")
    assert exported.compiled.schema_assets[1].schema_document()["properties"] == {
        "summary": {"type": "string"}}
    assert exported.module_release.module_id == "summarize_note"
    assert exported.module_release.reviewer_defaults is None
    assert exported.module_release.execution_requirements == _requirements()
    assert exported == ordinary.export(module_version="v1")
    assert not exported.origin_bundle.execution_profiles
    assert not exported.origin_bundle.execution_variant_policies
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(exported.origin_bundle)
    facts = ordinary.project(registry, exported)
    assert facts["module_release_sha256"] == exported.module_release.release_sha256
    assert facts["execution_requirements"] == _requirements().as_dict()
    assert not ({"active", "execution_profile", "execution_variant",
                 "execution_blocker_code", "compatible_transport_kinds"} & set(facts))
    assert _source_hashes(project) == before


def test_reviewer_inherits_all_module_methods_and_supplies_only_environment(tmp_path):
    source = load_reviewer_registration(_task_project(tmp_path), skill_id=SKILL_ID, module_id=MODULE_ID)
    reviewer = ModuleReviewer(source)
    ordinary = Module(source, execution_requirements=_requirements())
    assert reviewer.export(module_version="v1") == ordinary.export(module_version="v1")
    for name in ("__init__", "from_registration", "export", "project", "to_workflow"):
        assert name not in ModuleReviewer.__dict__
    assert ModuleReviewer.export is Module.export
    assert ModuleReviewer.to_workflow is Module.to_workflow
    assert reviewer.execution_requirements == _requirements()
    with pytest.raises(ValueError, match="cannot be overridden"):
        ModuleReviewer(source, execution_requirements=_requirements())
    with pytest.raises(ValueError, match="requires explicit"):
        Module(source)
    with pytest.raises(TypeError):
        Module(source, _requirements())
    with pytest.raises(TypeError):
        ModuleReviewer(source, _requirements())


def test_module_reviewer_exports_release_and_profile_independently(tmp_path):
    reviewer = ModuleReviewer.from_registration(_task_project(tmp_path), skill_id=SKILL_ID, module_id=MODULE_ID)
    for name in ("behavior_policy", "evaluation_policy", "retry_policy", "execution_profile",
                 "release_registry", "reviewer_defaults", "model_id", "reasoning_profile"):
        with pytest.raises(TypeError, match="unexpected keyword"):
            reviewer.export(module_version="v1", **{name: None})
    exported = reviewer.export(module_version="v1")
    for name in ("execution_profile", "execution_variant_candidate", "execution_variant", "execution_blocker_code"):
        assert not hasattr(exported, name)
    assert exported.evaluation_policy.policy_document() == {"evaluation_mode": "none"}
    first_profile = _profile()
    second_profile = _profile(model_id="another_model")
    assert first_profile.release_sha256 != second_profile.release_sha256
    assert exported == reviewer.export(module_version="v1")


def test_legacy_source_is_readable_but_not_silently_reauthored(tmp_path):
    project = _project(tmp_path)
    original = _source_hashes(project)
    source = load_module_registration(project, skill_id=SKILL_ID, module_id=MODULE_ID)
    assert source.schema_version == "runtime_module_registration_v2"
    with pytest.raises(ValueError, match="migrated v4"):
        ModuleReviewer(source)
    assert _source_hashes(project) == original


@pytest.mark.parametrize("field,value", [
    ("compatible_transport_kinds", ["claude_cli"]),
    ("declared_operation_ids", ["model_execute"]),
    ("behavior_policy_ref", "behavior-policy:workflow_execution_isolated@v1"),
    ("evaluation_policy_ref", "evaluation-policy:module_candidate@v1"),
    ("retry_policy_ref", "retry-policy:bounded_candidate@v1"),
    ("entry_policy", "standalone_allowed"),
    ("output_resolution_policy", "evaluated_single"),
])
def test_v4_rejects_execution_configuration_in_source(tmp_path, field, value):
    project = _task_project(tmp_path)
    path = project / ".claude/skills" / SKILL_ID / "runtime_modules" / MODULE_ID / "module_registration.json"
    payload = json.loads(path.read_text())
    payload[field] = value
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="invalid v4 shape"):
        load_module_registration(project, skill_id=SKILL_ID, module_id=MODULE_ID)


def test_reviewer_format_gate_checks_the_same_single_loaded_source(tmp_path, monkeypatch):
    from agent_runtime.registry import registry_module_loading as loading
    from agent_runtime.registry import registry_reviewer_defaults as reviewer_source
    project = _task_project(tmp_path)
    original_loader = loading.load_module_registration
    original_check = reviewer_source._validate_reviewer_output_schema
    calls = []
    def once(*args, **kwargs):
        source = original_loader(*args, **kwargs)
        calls.append(source)
        return source
    def check(document):
        assert len(calls) == 1
        assert document == json.loads(calls[0].output_schema_document)
        original_check(document)
    monkeypatch.setattr(loading, "load_module_registration", once)
    monkeypatch.setattr(reviewer_source, "_validate_reviewer_output_schema", check)
    source = load_reviewer_registration(project, skill_id=SKILL_ID, module_id=MODULE_ID)
    assert source is calls[0]
    ModuleReviewer(source).export(module_version="v1")
    assert len(calls) == 1


def test_reviewer_source_gate_rejects_non_review_schema_not_generic_export(tmp_path):
    project = _task_project(tmp_path, module_id="summarize_note")
    with pytest.raises(ValueError, match="common format"):
        load_reviewer_registration(project, skill_id=SKILL_ID, module_id="summarize_note")
    # The preset by itself promises environment, not a role-format attestation.
    ModuleReviewer.from_registration(project, skill_id=SKILL_ID,
        module_id="summarize_note").export(module_version="v1")


def test_tool_free_requirements_and_independent_model_bindings(tmp_path):
    source = load_module_registration(_task_project(tmp_path), skill_id=SKILL_ID, module_id=MODULE_ID)
    req = _requirements(execution_mode="tool_free", attempt_workspace_policy="none",
                        tool_policy=(), timeout_seconds=900)
    module = Module(source, execution_requirements=req,
                    output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE)
    exported = module.export(module_version="v1")
    req.assert_profile(_profile())
    req.assert_profile(_profile(model_id="another_model"))
    with pytest.raises(ValueError):
        req.assert_profile(_native_profile())
    assert exported.module_release.execution_requirements.tool_policy == ()
    assert exported.module_release.output_resolution_policy is OutputResolutionPolicy.DIRECT_SINGLE


def test_gateway_and_real_domain_operations_are_preserved(tmp_path):
    source = load_module_registration(_task_project(tmp_path), skill_id=SKILL_ID, module_id=MODULE_ID)
    req = _requirements(semantic_input_delivery_mode="gateway_read",
        attempt_workspace_policy="none", network_policy="gateway_only",
        tool_policy=("repository_read",), timeout_seconds=900,
        gateway_access_reasons=("authorized_package_external_exploration",))
    req.assert_profile(_gateway_profile())
    with pytest.raises(ValueError):
        req.assert_profile(_gateway_profile(tool_policy=("undeclared_tool",)))
    module = Module(source, execution_requirements=req,
                    declared_operation_ids=("model_execute", "write_business_data"))
    exported = module.export(module_version="v1")
    assert exported.module_release.declared_operation_ids == ("model_execute", "write_business_data")
    graph = module.to_workflow(exported).export()
    assert "write_business_data" in graph.candidate.authorization_manifest_document["operations"]


@pytest.mark.parametrize("operations", [(), ("read",), ("model_execute", "invoke_model"), ("model_execute", "model_execute")])
def test_invalid_model_operation_declaration_is_still_rejected(tmp_path, operations):
    source = load_module_registration(_task_project(tmp_path), skill_id=SKILL_ID, module_id=MODULE_ID)
    with pytest.raises(ModuleAuthoringError) as failure:
        Module(source, execution_requirements=_requirements(), declared_operation_ids=operations)
    assert failure.value.error_code == MODULE_OPERATION_DECLARATION_INVALID


def test_project_rejects_same_source_with_different_requirements_and_missing_dependencies(tmp_path):
    source = load_module_registration(_task_project(tmp_path), skill_id=SKILL_ID, module_id=MODULE_ID)
    first = Module(source, execution_requirements=_requirements())
    second = Module(source, execution_requirements=_requirements(timeout_seconds=1100))
    a, b = first.export(module_version="v1"), second.export(module_version="v2")
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(a.origin_bundle)
    with pytest.raises(ValueError, match="captured task"):
        first.project(registry, b)
    with pytest.raises(Exception):
        first.project(RuntimeReleaseRegistry(), a)
    assert a.module_release.release_sha256 != b.module_release.release_sha256


@pytest.mark.parametrize("field,value", [
    ("execution_mode", "tool_free"),
    ("semantic_input_delivery_mode", "managed_attachment"),
    ("attempt_workspace_policy", "none"),
    ("tool_policy", ("read",)),
    ("gateway_access_reasons", ("external_fact_verification",)),
    ("network_policy", "direct_sandboxed"),
    ("output_constraint_mode", "prompt_only_json"),
    ("timeout_seconds", 1100),
])
def test_each_profile_capability_difference_is_rejected(field, value):
    original = _native_profile()
    try:
        altered = _native_profile(**{field: value})
    except ValueError:
        # A forbidden combination must already fail the shared shape gate.
        return
    with pytest.raises(ValueError, match="differs"):
        _requirements().assert_profile(altered)
    _requirements().assert_profile(original)


@pytest.mark.parametrize("changes", [
    {"context_isolation": "shared"}, {"max_attempts": 0}, {"max_attempts": 101},
    {"max_attempts": True}, {"timeout_seconds": True}, {"timeout_seconds": 86401},
    {"tool_policy": ("read", "read")}, {"tool_policy": ["read"]},
    {"gateway_access_reasons": ["external_fact_verification"]},
])
def test_invalid_requirements_are_rejected(changes):
    with pytest.raises(ValueError):
        _requirements(**changes).validate()


def test_retry_policy_has_one_requirements_source_and_content_version(tmp_path):
    source = load_module_registration(_task_project(tmp_path), skill_id=SKILL_ID, module_id=MODULE_ID)
    original = Module(source, execution_requirements=_requirements()).export(module_version="v1")
    adjusted = Module(source, execution_requirements=_requirements(max_attempts=4)).export(module_version="v2")
    assert original.retry_policy.release_ref == "retry-policy:bounded_candidate@v1"
    assert adjusted.retry_policy.policy_document() == {"max_attempts": 4}
    assert adjusted.retry_policy.release_ref != original.retry_policy.release_ref
    assert adjusted.behavior_policy.policy_document()["context_isolation"] == adjusted.module_release.execution_requirements.context_isolation
