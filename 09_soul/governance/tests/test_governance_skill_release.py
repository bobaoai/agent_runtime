from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "09_soul/governance/governance_skill_release.py"
SPEC = importlib.util.spec_from_file_location("governance_skill_release", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _skill_payload(
    *, name: str = "engineering-example", role: str = "authoring"
) -> bytes:
    return (
        "---\n"
        f"name: {name}\n"
        "skill_class: primary_agent_development\n"
        f"primary_agent_entry_role: {role}\n"
        "primary_agent_entry_subject: engineering_change_candidate\n"
        "first_authority_ref: designDoc/the_example.md\n"
        "description: Designs one example change.\n"
        "---\n\n"
        "# Engineering Example\n"
    ).encode("utf-8")


def _write_fixture_project(
    root: Path,
    *,
    source_payload: bytes | None = None,
    target_payload: bytes | None = None,
    declared_hash: str | None = None,
    required_t0: str = "the_example",
    codex_target: str = ".agents/skills/engineering-example/SKILL.md",
    source: str = "09_soul/governance/skills/engineering-example/SKILL.md",
    binding_payload: bytes | None = None,
) -> Path:
    payload = source_payload or _skill_payload()
    source_path = root / source
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(payload)
    authority = root / "designDoc/the_example.md"
    authority.parent.mkdir(parents=True, exist_ok=True)
    authority.write_text("# Example T0\n", encoding="utf-8")
    t0_source = root / "09_soul/governance/t0/the_example.md"
    t0_source.parent.mkdir(parents=True, exist_ok=True)
    t0_payload = b"# Example T0\n"
    t0_source.write_bytes(t0_payload)
    (root / "designDoc/the_charter.md").write_text(
        "# Project Charter\n",
        encoding="utf-8",
    )
    t0_manifest = {
        "manifest_version": "governance_t0_manifest_v2",
        "charter": {
            "mode": "project_specific",
            "target": "designDoc/the_charter.md",
        },
        "retired_t0_targets": [],
        "portable_t0_contracts": [
            {
                "t0_layer_id": "the_example",
                "source": "09_soul/governance/t0/the_example.md",
                "target": "designDoc/the_example.md",
                "sha256": _hash(t0_payload),
            }
        ]
    }
    t0_path = root / "09_soul/governance/governance_t0_manifest.json"
    t0_path.parent.mkdir(parents=True, exist_ok=True)
    t0_path.write_text(json.dumps(t0_manifest), encoding="utf-8")
    projections = [
        {
            "host_id": "claude",
            "target": ".claude/skills/engineering-example/SKILL.md",
        },
        {"host_id": "codex", "target": codex_target},
    ]
    if target_payload is not None:
        for projection in projections:
            target = root / projection["target"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(target_payload)
    manifest = {
        "manifest_version": "governance_skill_manifest_v4",
        "retired_projection_roots": [],
        "portable_governance_skills": [
            {
                "skill_id": "engineering-example",
                "required_t0_layer_ids": [required_t0],
                "required_soul_resource_ids": [],
                "primary_agent_entry_role": "authoring",
                "primary_agent_entry_subject": "engineering_change_candidate",
                "package_files": [
                    {
                        "source": source,
                        "sha256": declared_hash or _hash(payload),
                        "projections": projections,
                    }
                ],
            }
        ],
    }
    manifest_path = root / "09_soul/governance/governance_skill_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    if binding_payload is not None:
        binding_source = root / "governance_bindings/skills/engineering-example.md"
        binding_source.parent.mkdir(parents=True, exist_ok=True)
        binding_source.write_bytes(binding_payload)
        binding_manifest = {
            "manifest_version": "governance_skill_project_binding_manifest_v1",
            "bindings": [
                {
                    "skill_id": "engineering-example",
                    "source": (
                        "governance_bindings/skills/engineering-example.md"
                    ),
                    "sha256": _hash(binding_payload),
                }
            ],
        }
        binding_manifest_path = (
            root / "governance_bindings/governance_skill_binding_manifest.json"
        )
        binding_manifest_path.write_text(
            json.dumps(binding_manifest), encoding="utf-8"
        )
    return manifest_path


def _add_runtime_module_fixture(
    root: Path,
    manifest_path: Path,
    *,
    export_id: str = "example_reviewer",
    registration_skill_id: str = "engineering-example",
    input_schema_path: str = "schemas/input.schema.json",
    input_schema_ref: str = "schema:example_reviewer_input@v1",
    input_schema_id: str | None = None,
    omit_input_schema_ref: bool = False,
    omit_input_schema_id: bool = False,
    include_validation_case: bool = True,
) -> dict[str, bytes]:
    module_root = (
        "09_soul/governance/skills/engineering-example/"
        "runtime_modules/example_reviewer"
    )
    registration = {
        "module_id": "example_reviewer",
        "export_id": export_id,
        "skill_id": registration_skill_id,
        "input_schema_path": input_schema_path,
        "output_schema_path": "schemas/output.schema.json",
        "output_schema_ref": "schema:example_reviewer_output@v1",
    }
    if not omit_input_schema_ref:
        registration["input_schema_ref"] = input_schema_ref
    input_schema = {}
    if not omit_input_schema_id:
        input_schema["$id"] = input_schema_id or input_schema_ref
    assets = {
        "module_registration.json": json.dumps(registration).encode("utf-8"),
        "prompt.md": b"Review exactly one frozen candidate.\n",
        "schemas/input.schema.json": json.dumps(input_schema).encode("utf-8"),
        "schemas/output.schema.json": json.dumps(
            {"$id": "schema:example_reviewer_output@v1"}
        ).encode("utf-8"),
    }
    if include_validation_case:
        case_semantics = {
            "positive": ("none", "schema_valid_and_reviewable"),
            "negative": ("additional_model_output_field", "schema_rejected"),
            "schema_drift": (
                "output_schema_ref_hash_mismatch",
                "registration_rejected",
            ),
        }
        for case_kind in ("positive", "negative", "schema_drift"):
            mutation, expected_disposition = case_semantics[case_kind]
            assets[f"validation_cases/{case_kind}_case.json"] = json.dumps(
                {
                    "case_id": f"example_reviewer_{case_kind}",
                    "case_kind": case_kind,
                    "module_id": "example_reviewer",
                    "mutation": mutation,
                    "expected_disposition": expected_disposition,
                }
            ).encode("utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative_path, payload in assets.items():
        source_ref = f"{module_root}/{relative_path}"
        source_path = root / source_ref
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(payload)
        manifest["portable_governance_skills"][0]["package_files"].append(
            {
                "source": source_ref,
                "sha256": _hash(payload),
                "projections": [
                    {
                        "host_id": "claude",
                        "target": (
                            ".claude/skills/engineering-example/"
                            f"runtime_modules/example_reviewer/{relative_path}"
                        ),
                    }
                ],
            }
        )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return assets


def test_production_governance_skill_release_is_clean() -> None:
    report = release.check_governance_skill_release(REPO_ROOT)

    assert report.is_clean
    assert report.skill_count == 7
    assert report.projection_count == 49


def test_skill_authoring_method_is_distinct_from_skill_management_t0() -> None:
    manifest = release.load_governance_skill_manifest(REPO_ROOT)
    skill = next(
        item
        for item in manifest.portable_governance_skills
        if item.skill_id == "the-skill-authoring"
    )

    assert skill.required_t0_layer_ids == (
        "the_skill_management",
        "the_system_change_governance",
    )
    assert skill.primary_agent_entry_subject == "skill_work_package"
    assert {
        projection.target
        for package_file in skill.package_files
        for projection in package_file.projections
    } == {
        ".claude/skills/the-skill-authoring/SKILL.md",
        ".agents/skills/the-skill-authoring/SKILL.md",
    }
    assert {
        ".claude/skills/the-skill-management",
        ".agents/skills/the-skill-management",
    }.issubset(set(manifest.retired_projection_roots))


def test_engineering_reviewer_output_schema_is_strict_projection_ready() -> None:
    module_root = (
        REPO_ROOT
        / "09_soul/governance/skills/engineering-change-review/runtime_modules/"
        "engineering_change_reviewer"
    )
    registration = json.loads(
        (module_root / "module_registration.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (module_root / "schemas/output.schema.json").read_text(encoding="utf-8")
    )

    assert registration["output_schema_ref"] == (
        "schema:engineering_change_reviewer_output@v2"
    )
    assert schema["$id"] == registration["output_schema_ref"]
    assert all(
        clause[branch]["type"] == "object"
        for clause in schema["allOf"]
        for branch in ("if", "then")
    )


def test_readme_skill_table_matches_manifest_identity_and_role() -> None:
    manifest = release.load_governance_skill_manifest(REPO_ROOT)
    expected = {
        (skill.skill_id, skill.primary_agent_entry_role)
        for skill in manifest.portable_governance_skills
    }
    lines = (REPO_ROOT / "09_soul/governance/README.md").read_text(
        encoding="utf-8"
    ).splitlines()
    header_index = lines.index("| Skill | Entry role | Responsibility |")
    observed: set[tuple[str, str]] = set()
    for line in lines[header_index + 2 :]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        observed.add((cells[0].strip("`"), cells[1]))

    assert observed == expected


def test_design_reviewer_schema_accepts_charter_candidate_layer() -> None:
    schema_path = (
        REPO_ROOT
        / "09_soul/governance/skills/the-contract-audit/runtime_modules"
        / "design_contract_reviewer/schemas/input.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$defs"]["candidate_document"]["properties"]["layer"][
        "enum"
    ] == ["Charter", "T0", "T1", "T2"]


def test_structure_reviewer_has_distinct_subject_and_check_contract() -> None:
    module_root = (
        REPO_ROOT
        / "09_soul/governance/skills/the-contract-audit/runtime_modules"
        / "structure_change_reviewer"
    )
    registration = json.loads(
        (module_root / "module_registration.json").read_text(encoding="utf-8")
    )
    input_schema = json.loads(
        (module_root / "schemas/input.schema.json").read_text(encoding="utf-8")
    )
    output_schema = json.loads(
        (module_root / "schemas/output.schema.json").read_text(encoding="utf-8")
    )

    assert registration["module_id"] == "structure_change_reviewer"
    assert input_schema["$id"] == registration["input_schema_ref"]
    assert output_schema["$id"] == registration["output_schema_ref"]
    assert {
        "structure_change_proposal",
        "peer_registry_snapshot",
        "required_check_ids",
    }.issubset(input_schema["required"])
    assert "parent_authority_decision" not in output_schema["properties"]


def test_structure_review_law_and_subject_routing_match_module_contract() -> None:
    scg = (
        REPO_ROOT / "09_soul/governance/t0/the_system_change_governance.md"
    ).read_text(encoding="utf-8")
    ddm = (
        REPO_ROOT / "09_soul/governance/t0/the_design_doc_management.md"
    ).read_text(encoding="utf-8")
    audit_skill = (
        REPO_ROOT / "09_soul/governance/skills/the-contract-audit/SKILL.md"
    ).read_text(encoding="utf-8")

    for operation in (
        "create",
        "promote",
        "split",
        "merge",
        "replace",
        "rename",
        "retire",
    ):
        assert f"`{operation}`" in scg
    for decision in (
        "create_new",
        "promote_existing",
        "split_existing",
        "merge_existing",
        "specialize_existing",
        "replace_existing",
        "rename_existing",
        "retire_existing",
    ):
        assert f"`{decision}`" in scg
    assert "`system_change_governance_reviewer` for one frozen" in scg
    assert "`structure_change_reviewer` for one immutable" in scg
    assert "Frozen System Change Governance Design candidate" in audit_skill
    assert "Immutable `StructureChangeProposal`" in audit_skill
    assert "replaces, renames, or\n   retires a registered structure" in ddm


def test_scg_design_reviewer_has_no_general_routing_registry_contract() -> None:
    module_root = (
        REPO_ROOT
        / "09_soul/governance/skills/the-contract-audit/runtime_modules"
        / "system_change_governance_reviewer"
    )
    prompt = (module_root / "prompt.md").read_text(encoding="utf-8")
    input_schema = json.loads(
        (module_root / "schemas/input.schema.json").read_text(encoding="utf-8")
    )
    output_schema = json.loads(
        (module_root / "schemas/output.schema.json").read_text(encoding="utf-8")
    )

    assert "routing_registry_projection" not in input_schema["properties"]
    assert "required_routing_checks" not in input_schema["properties"]
    assert "routing_coverage" not in output_schema["properties"]
    assert "general Task Routing Registry" in " ".join(prompt.split())


def test_production_module_exports_declare_all_required_validation_case_kinds() -> None:
    manifest = release.load_governance_skill_manifest(REPO_ROOT)
    required = {"positive", "negative", "schema_drift"}
    observed: dict[tuple[str, str], set[str]] = {}
    for skill in manifest.portable_governance_skills:
        for package_file in skill.package_files:
            relative = Path(package_file.source).relative_to(
                Path("09_soul/governance/skills") / skill.skill_id
            )
            if len(relative.parts) < 4 or relative.parts[0] != "runtime_modules":
                continue
            if relative.parts[2] != "validation_cases":
                continue
            fixture = json.loads(
                (REPO_ROOT / package_file.source).read_text(encoding="utf-8")
            )
            key = (skill.skill_id, relative.parts[1])
            assert set(fixture) == {
                "case_id",
                "case_kind",
                "module_id",
                "mutation",
                "expected_disposition",
            }
            assert fixture["module_id"] == relative.parts[1]
            observed.setdefault(key, set()).add(fixture["case_kind"])

    assert observed
    assert all(required.issubset(case_kinds) for case_kinds in observed.values())


def test_manifest_rejects_incomplete_runtime_module_export(
    tmp_path: Path,
) -> None:
    manifest = json.loads(
        (REPO_ROOT / "09_soul/governance/governance_skill_manifest.json")
        .read_text(encoding="utf-8")
    )
    contract_audit = next(
        row
        for row in manifest["portable_governance_skills"]
        if row["skill_id"] == "the-contract-audit"
    )
    contract_audit["package_files"] = [
        row
        for row in contract_audit["package_files"]
        if not row["source"].endswith(
            "design_contract_reviewer/schemas/input.schema.json"
        )
    ]
    manifest_path = tmp_path / "governance_skill_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="Runtime Module export is missing required assets",
    ):
        release.load_governance_skill_manifest(REPO_ROOT, manifest_path)


def test_manifest_rejects_runtime_module_without_validation_case(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(
        tmp_path, manifest_path, include_validation_case=False
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="requires declared validation cases",
    ):
        release.load_governance_skill_manifest(tmp_path, manifest_path)


def test_manifest_rejects_incomplete_validation_case_declaration(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(tmp_path, manifest_path)
    case_path = (
        tmp_path
        / "09_soul/governance/skills/engineering-example/runtime_modules/"
        "example_reviewer/validation_cases/negative_case.json"
    )
    fixture = json.loads(case_path.read_text(encoding="utf-8"))
    fixture.pop("expected_disposition")
    case_path.write_text(json.dumps(fixture), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for package_file in manifest["portable_governance_skills"][0][
        "package_files"
    ]:
        if package_file["source"].endswith(
            "validation_cases/negative_case.json"
        ):
            package_file["sha256"] = _hash(case_path.read_bytes())
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="must be an exact declaration",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_runtime_module_identity_mismatch(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(
        tmp_path, manifest_path, export_id="other_reviewer"
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="directory, module_id, and export_id must match",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_undeclared_runtime_module_schema_path(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(
        tmp_path,
        manifest_path,
        input_schema_path="schemas/not_declared.schema.json",
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="does not resolve to a declared asset",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_runtime_module_schema_ref_mismatch(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(
        tmp_path,
        manifest_path,
        input_schema_id="schema:example_reviewer_input@v2",
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="schema ref must match the resolved schema",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_runtime_module_skill_identity_mismatch(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(
        tmp_path,
        manifest_path,
        registration_skill_id="other-skill",
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="skill_id must match its owning Skill",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_absent_runtime_module_schema_ref_and_id(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(
        tmp_path,
        manifest_path,
        omit_input_schema_ref=True,
        omit_input_schema_id=True,
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="schema ref must match the resolved schema",
    ):
        release.check_governance_skill_release(tmp_path)


def test_check_reports_missing_projections(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path)

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_projection_missing",
        "governance_skill_projection_missing",
    ]


def test_check_reports_projection_drift(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path, target_payload=b"stale\n")

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_projection_drift",
        "governance_skill_projection_drift",
    ]


def test_check_reports_retired_projection_root(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    release.apply_governance_skill_release(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retired_root = ".claude/skills/legacy-governance"
    manifest["retired_projection_roots"] = [retired_root]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    retired_path = tmp_path / retired_root
    retired_path.mkdir(parents=True)
    (retired_path / "SKILL.md").write_text("stale\n", encoding="utf-8")

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_retired_projection_present"
    ]


def test_apply_preserves_retired_and_undeclared_host_members(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retired_root = ".claude/skills/legacy-governance"
    manifest["retired_projection_roots"] = [retired_root]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    retired_member = tmp_path / retired_root / "SKILL.md"
    retired_member.parent.mkdir(parents=True)
    retired_member.write_text("legacy\n", encoding="utf-8")
    undeclared_member = (
        tmp_path / ".claude/skills/engineering-example/notes.md"
    )
    undeclared_member.parent.mkdir(parents=True, exist_ok=True)
    undeclared_member.write_text("user-owned\n", encoding="utf-8")

    report = release.apply_governance_skill_release(tmp_path)

    assert retired_member.read_text(encoding="utf-8") == "legacy\n"
    assert undeclared_member.read_text(encoding="utf-8") == "user-owned\n"
    assert {issue.code for issue in report.issues} == {
        "governance_skill_retired_projection_present",
        "governance_skill_undeclared_package_member",
    }


def test_check_reports_active_reference_to_retired_governance_identity(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["retired_projection_roots"] = [
        ".claude/skills/legacy-governance"
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    release.apply_governance_skill_release(tmp_path)
    live_skill = tmp_path / ".claude/skills/live-skill/SKILL.md"
    live_skill.parent.mkdir(parents=True)
    live_skill.write_text(
        "Route legacy work to `legacy-governance`.\n", encoding="utf-8"
    )

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_retired_projection_reference"
    ]


def test_retired_identity_scan_allows_distinct_successor_identity(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["retired_projection_roots"] = [
        ".claude/skills/legacy-governance"
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    release.apply_governance_skill_release(tmp_path)
    successor = tmp_path / ".claude/skills/live-skill/SKILL.md"
    successor.parent.mkdir(parents=True)
    successor.write_text(
        "Use `legacy-governance-v2` as a distinct active identity.\n",
        encoding="utf-8",
    )

    report = release.check_governance_skill_release(tmp_path)

    assert report.is_clean


def test_retired_identity_scan_rejects_reference_inside_non_utf8_payload(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["retired_projection_roots"] = [
        ".claude/skills/legacy-governance"
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    release.apply_governance_skill_release(tmp_path)
    live_asset = tmp_path / ".claude/skills/live-skill/non_utf8.bin"
    live_asset.parent.mkdir(parents=True)
    live_asset.write_bytes(b"\xff legacy-governance \xfe")

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_retired_projection_reference"
    ]


def test_check_reports_undeclared_active_package_member(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path)
    release.apply_governance_skill_release(tmp_path)
    extra = tmp_path / ".claude/skills/engineering-example/extra.md"
    extra.write_text("undeclared\n", encoding="utf-8")

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_undeclared_package_member"
    ]
    assert report.issues[0].path == (
        ".claude/skills/engineering-example/extra.md"
    )


def test_check_reports_undeclared_source_package_member(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path)
    release.apply_governance_skill_release(tmp_path)
    extra = (
        tmp_path
        / "09_soul/governance/skills/engineering-example/extra.md"
    )
    extra.write_text("undeclared\n", encoding="utf-8")

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_undeclared_source_member"
    ]
    assert report.issues[0].path == (
        "09_soul/governance/skills/engineering-example/extra.md"
    )


def test_check_reports_undeclared_source_package_directory(
    tmp_path: Path,
) -> None:
    _write_fixture_project(tmp_path)
    release.apply_governance_skill_release(tmp_path)
    extra = (
        tmp_path
        / "09_soul/governance/skills/engineering-example/empty_extra"
    )
    extra.mkdir()

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_undeclared_source_directory"
    ]


def test_check_reports_undeclared_skill_source_root_member(
    tmp_path: Path,
) -> None:
    _write_fixture_project(tmp_path)
    release.apply_governance_skill_release(tmp_path)
    extra = tmp_path / "09_soul/governance/skills/undeclared-skill"
    extra.mkdir()

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_undeclared_source_member"
    ]


def test_apply_writes_exact_host_projections(tmp_path: Path) -> None:
    payload = _skill_payload()
    _write_fixture_project(tmp_path, source_payload=payload)

    report = release.apply_governance_skill_release(tmp_path)

    assert report.is_clean
    assert (
        tmp_path / ".claude/skills/engineering-example/SKILL.md"
    ).read_bytes() == payload
    assert (
        tmp_path / ".agents/skills/engineering-example/SKILL.md"
    ).read_bytes() == payload


def test_apply_projects_declared_runtime_assets_only_to_canonical_package(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    assets = _add_runtime_module_fixture(tmp_path, manifest_path)

    report = release.apply_governance_skill_release(tmp_path)

    assert report.is_clean
    assert report.skill_count == 1
    assert report.projection_count == 9
    assert (
        tmp_path
        / ".claude/skills/engineering-example/"
        "runtime_modules/example_reviewer/prompt.md"
    ).read_bytes() == assets["prompt.md"]
    assert not (
        tmp_path
        / ".agents/skills/engineering-example/"
        "runtime_modules/example_reviewer/prompt.md"
    ).exists()


def test_manifest_rejects_runtime_asset_projected_to_codex(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    asset = b"Review.\n"
    asset_source = (
        tmp_path
        / "09_soul/governance/skills/engineering-example/"
        "runtime_modules/example_reviewer/prompt.md"
    )
    asset_source.parent.mkdir(parents=True)
    asset_source.write_bytes(asset)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["portable_governance_skills"][0]["package_files"].append(
        {
            "source": (
                "09_soul/governance/skills/engineering-example/"
                "runtime_modules/example_reviewer/prompt.md"
            ),
            "sha256": _hash(asset),
            "projections": [
                {
                    "host_id": "codex",
                    "target": (
                        ".agents/skills/engineering-example/"
                        "runtime_modules/example_reviewer/prompt.md"
                    ),
                }
            ],
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="cannot project Runtime package assets",
    ):
        release.load_governance_skill_manifest(tmp_path, manifest_path)


def test_apply_composes_registered_project_binding_after_portable_method(
    tmp_path: Path,
) -> None:
    payload = _skill_payload()
    binding = b"## Project Runtime Bindings\n\nModule: `example_module`.\n"
    _write_fixture_project(
        tmp_path,
        source_payload=payload,
        binding_payload=binding,
    )

    report = release.apply_governance_skill_release(tmp_path)

    assert report.is_clean
    expected = payload + b"\n" + binding
    assert (
        tmp_path / ".claude/skills/engineering-example/SKILL.md"
    ).read_bytes() == expected
    assert (
        tmp_path / ".agents/skills/engineering-example/SKILL.md"
    ).read_bytes() == expected


def test_project_binding_hash_drift_is_rejected(tmp_path: Path) -> None:
    binding = b"## Project Runtime Bindings\n\nModule: `example_module`.\n"
    _write_fixture_project(tmp_path, binding_payload=binding)
    binding_path = tmp_path / "governance_bindings/skills/engineering-example.md"
    binding_path.write_bytes(binding + b"changed\n")

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="project binding hash mismatch",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_source_hash_drift(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path, declared_hash="0" * 64)

    with pytest.raises(release.GovernanceSkillReleaseError, match="hash mismatch"):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_unknown_t0_dependency(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path, required_t0="the_missing")

    with pytest.raises(release.GovernanceSkillReleaseError, match="unknown T0"):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_frontmatter_mismatch(tmp_path: Path) -> None:
    payload = _skill_payload(role="review")
    _write_fixture_project(tmp_path, source_payload=payload)

    with pytest.raises(
        release.GovernanceSkillReleaseError, match="frontmatter mismatch"
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_first_authority_outside_declared_t0_closure(
    tmp_path: Path,
) -> None:
    payload = _skill_payload().replace(
        b"designDoc/the_example.md", b"designDoc/not_a_t0.md"
    )
    _write_fixture_project(tmp_path, source_payload=payload)
    (tmp_path / "designDoc/not_a_t0.md").write_text(
        "# Not a T0\n", encoding="utf-8"
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="first authority is outside its declared T0 closure",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_escaping_first_authority_path(
    tmp_path: Path,
) -> None:
    payload = _skill_payload().replace(
        b"designDoc/the_example.md", b"../outside.md"
    )
    _write_fixture_project(tmp_path, source_payload=payload)

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="first_authority_ref",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_project_local_source_identity(tmp_path: Path) -> None:
    payload = _skill_payload() + b"\nUse /Users/example/project.\n"
    _write_fixture_project(tmp_path, source_payload=payload)

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="project-local identity",
    ):
        release.check_governance_skill_release(tmp_path)


@pytest.mark.parametrize(
    "fragment",
    (
        "src/project/module.py",
        "tests/test_project_module.py",
        ".venv/bin/python",
        "09_soul/governance/t0/the_example.md",
    ),
)
def test_manifest_rejects_portable_implementation_paths(
    tmp_path: Path,
    fragment: str,
) -> None:
    payload = _skill_payload() + f"\nUse {fragment}.\n".encode("utf-8")
    _write_fixture_project(tmp_path, source_payload=payload)

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="project-local identity",
    ):
        release.check_governance_skill_release(tmp_path)


def test_project_policy_rejects_declared_local_identity(tmp_path: Path) -> None:
    payload = _skill_payload() + b"\nUse local-product-identity.\n"
    _write_fixture_project(tmp_path, source_payload=payload)
    policy_path = tmp_path / "governance_bindings/governance_release_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "governance_release_policy_v2",
                "forbidden_source_fragments": ["local_product_identity"],
                "retired_t0_targets": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="project-local identity",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_rejects_undeclared_cited_t0_dependency(
    tmp_path: Path,
) -> None:
    payload = _skill_payload() + b"\nRead designDoc/the_other.md.\n"
    _write_fixture_project(tmp_path, source_payload=payload)
    other_authority = tmp_path / "designDoc/the_other.md"
    other_authority.write_text("# Other T0\n", encoding="utf-8")
    other_source = tmp_path / "09_soul/governance/t0/the_other.md"
    other_payload = b"# Other T0\n"
    other_source.write_bytes(other_payload)
    t0_path = tmp_path / "09_soul/governance/governance_t0_manifest.json"
    t0_manifest = json.loads(t0_path.read_text(encoding="utf-8"))
    t0_manifest["portable_t0_contracts"].append(
        {
            "t0_layer_id": "the_other",
            "source": "09_soul/governance/t0/the_other.md",
            "target": "designDoc/the_other.md",
            "sha256": _hash(other_payload),
        }
    )
    t0_path.write_text(json.dumps(t0_manifest), encoding="utf-8")

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="undeclared T0 dependencies",
    ):
        release.check_governance_skill_release(tmp_path)


def test_skill_release_rejects_structurally_invalid_t0_manifest(
    tmp_path: Path,
) -> None:
    _write_fixture_project(tmp_path)
    t0_path = tmp_path / "09_soul/governance/governance_t0_manifest.json"
    t0_manifest = json.loads(t0_path.read_text(encoding="utf-8"))
    del t0_manifest["portable_t0_contracts"][0]["sha256"]
    t0_path.write_text(json.dumps(t0_manifest), encoding="utf-8")

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="cannot validate portable T0 manifest",
    ):
        release.check_governance_skill_release(tmp_path)


def test_skill_release_rejects_missing_required_soul_resource(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["portable_governance_skills"][0][
        "required_soul_resource_ids"
    ] = ["soul:communication"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="Soul resource does not resolve",
    ):
        release.check_governance_skill_release(tmp_path)


def test_manifest_allows_charter_citation_without_t0_dependency(
    tmp_path: Path,
) -> None:
    payload = _skill_payload() + b"\nRead designDoc/the_charter.md.\n"
    _write_fixture_project(tmp_path, source_payload=payload)

    report = release.apply_governance_skill_release(tmp_path)

    assert report.is_clean


def test_manifest_rejects_escaping_projection_path(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(
        tmp_path, codex_target=".agents/skills/../outside/SKILL.md"
    )

    with pytest.raises(release.GovernanceSkillReleaseError):
        release.load_governance_skill_manifest(tmp_path, manifest_path)


def test_manifest_rejects_symlinked_projection_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".agents").symlink_to(outside, target_is_directory=True)
    _write_fixture_project(tmp_path)

    with pytest.raises(release.GovernanceSkillReleaseError, match="symlink"):
        release.check_governance_skill_release(tmp_path)
