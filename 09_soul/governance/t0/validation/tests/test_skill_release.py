from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError


REPO_ROOT = Path(__file__).resolve().parents[5]
MODULE_PATH = REPO_ROOT / "09_soul/governance/t0/validation/skill_release.py"
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
        "manifest_version": "governance_t0_manifest_v3",
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
        ],
        "artifact_contracts": [],
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
        "manifest_version": "governance_skill_manifest_v5",
        "forbidden_projection_roots": [],
        "instruction_resources": [],
        "portable_governance_skills": [
            {
                "skill_id": "engineering-example",
                "required_t0_layer_ids": [required_t0],
                "required_soul_resource_ids": [],
                "primary_agent_entry_role": "authoring",
                "primary_agent_entry_subject": "engineering_change_candidate",
                "accountable_owner_ref": "designDoc/the_example.md",
                "lifecycle_state": "developing",
                "managed_target_ref": None,
                "direct_entry_disposition": "active",
                "predecessor_skill_id": None,
                "retirement_tombstone": None,
                "package_files": [
                    {
                        "source": source,
                        "sha256": declared_hash or _hash(payload),
                        "embedded_resource_ids": [],
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


def _load_fixture_manifest(manifest_path: Path) -> dict[str, object]:
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _store_fixture_manifest(
    manifest_path: Path, manifest: dict[str, object]
) -> None:
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def _add_instruction_resource_fixture(
    root: Path,
    manifest_path: Path,
    *,
    embedded_payload: bytes,
) -> tuple[bytes, bytes]:
    resource_id = "soul:communication_authoring_prose"
    resource_source = root / "09_soul/core/COMMUNICATION.md"
    resource_source.parent.mkdir(parents=True, exist_ok=True)
    resource_source_payload = (
        b"# Communication\n"
        b"## Selected\n"
        b"canonical instruction\n"
        b"## Unselected\n"
        b"not embedded\n"
    )
    resource_source.write_bytes(resource_source_payload)
    selected_payload = b"## Selected\ncanonical instruction\n"
    source_payload = (
        _skill_payload()
        + b"\n<!-- embedded-resource:"
        + resource_id.encode("ascii")
        + b":start -->\n"
        + embedded_payload
        + b"<!-- embedded-resource:"
        + resource_id.encode("ascii")
        + b":end -->\n"
    )
    source_ref = (
        "09_soul/governance/skills/engineering-example/SKILL.md"
    )
    (root / source_ref).write_bytes(source_payload)
    manifest = _load_fixture_manifest(manifest_path)
    skill = _fixture_skill_row(manifest)
    skill["required_soul_resource_ids"] = ["soul:communication"]
    package_file = skill["package_files"][0]
    package_file["sha256"] = _hash(source_payload)
    package_file["embedded_resource_ids"] = [resource_id]
    manifest["instruction_resources"] = [
        {
            "resource_id": resource_id,
            "parent_dependency_id": "soul:communication",
            "source": "09_soul/core/COMMUNICATION.md",
            "selector": {
                "kind": "heading_range",
                "start": "## Selected",
                "end_exclusive": "## Unselected",
            },
            "sha256": _hash(selected_payload),
        }
    ]
    _store_fixture_manifest(manifest_path, manifest)
    return source_payload, selected_payload


def _add_skill_owned_instruction_resource_fixture(
    root: Path,
    manifest_path: Path,
    *,
    embedded_payload: bytes = b"canonical checklist\n",
) -> tuple[bytes, bytes, bytes]:
    resource_id = "skill:design_contract_review_checklist"
    selector_start = (
        "<!-- skill-specific-review-checklist:example_reviewer:start -->"
    )
    selector_end = (
        "<!-- skill-specific-review-checklist:example_reviewer:end -->"
    )
    selected_payload = b"canonical checklist\n"
    skill_payload = (
        _skill_payload()
        + b"\n"
        + selector_start.encode("ascii")
        + b"\n"
        + selected_payload
        + selector_end.encode("ascii")
        + b"\n"
    )
    skill_source = (
        root / "09_soul/governance/skills/engineering-example/SKILL.md"
    )
    skill_source.write_bytes(skill_payload)
    prompt_ref = (
        "09_soul/governance/skills/engineering-example/"
        "runtime_modules/example_reviewer/prompt.md"
    )
    prompt_payload = (
        b"review prompt\n"
        b"<!-- embedded-resource:skill:design_contract_review_checklist:start -->\n"
        + embedded_payload
        + b"<!-- embedded-resource:skill:design_contract_review_checklist:end -->\n"
    )
    prompt_path = root / prompt_ref
    _add_runtime_module_fixture(root, manifest_path)
    prompt_path.write_bytes(prompt_payload)

    manifest = _load_fixture_manifest(manifest_path)
    skill = _fixture_skill_row(manifest)
    skill["package_files"][0]["sha256"] = _hash(skill_payload)
    prompt_file = next(
        package_file
        for package_file in skill["package_files"]
        if package_file["source"] == prompt_ref
    )
    prompt_file["sha256"] = _hash(prompt_payload)
    prompt_file["embedded_resource_ids"] = [resource_id]
    manifest["instruction_resources"] = [
        {
            "resource_id": resource_id,
            "parent_dependency_id": "skill:engineering-example",
            "source": (
                "09_soul/governance/skills/engineering-example/SKILL.md"
            ),
            "selector": {
                "kind": "marker_range",
                "start": selector_start,
                "end_exclusive": selector_end,
            },
            "sha256": _hash(selected_payload),
        }
    ]
    _store_fixture_manifest(manifest_path, manifest)
    return skill_payload, prompt_payload, selected_payload


def _fixture_skill_row(manifest: dict[str, object]) -> dict[str, object]:
    rows = manifest["portable_governance_skills"]
    assert isinstance(rows, list) and len(rows) == 1
    row = rows[0]
    assert isinstance(row, dict)
    return row


def _direct_entry_tombstone(
    *,
    retired_identity: str = "legacy-governance",
    replacement_skill_id: str = "engineering-example",
) -> dict[str, object]:
    return {
        "retired_identity_kind": "direct_entry",
        "retired_identity": retired_identity,
        "replacement_skill_id": replacement_skill_id,
        "reason": "Managed execution replaced the legacy direct entry.",
        "final_sha256": "a" * 64,
        "effective_at_utc": "2026-08-24T12:00:00Z",
        "retired_projection_roots": [
            f".claude/skills/{retired_identity}",
            f".agents/skills/{retired_identity}",
        ],
    }


def _add_runtime_module_fixture(
    root: Path,
    manifest_path: Path,
    *,
    registration_module_id: str = "example_reviewer",
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
        "module_id": registration_module_id,
        "skill_id": registration_skill_id,
        "owner_contract_path": "designDoc/the_example.md",
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
            assets[f"tests/{case_kind}_case.json"] = json.dumps(
                {
                    "case_id": f"example_reviewer_{case_kind}",
                    "case_kind": case_kind,
                    "module_id": "example_reviewer",
                    "mutation": mutation,
                    "expected_disposition": expected_disposition,
                }
            ).encode("utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    skill_source_ref = (
        "09_soul/governance/skills/engineering-example/SKILL.md"
    )
    skill_source_path = root / skill_source_ref
    skill_payload = skill_source_path.read_bytes()
    if b"example_reviewer" not in skill_payload:
        skill_payload += (
            b"\nIndependent Runtime Reviewer Module: `example_reviewer`.\n"
        )
        skill_source_path.write_bytes(skill_payload)
    skill_package_file = next(
        item
        for item in manifest["portable_governance_skills"][0]["package_files"]
        if item["source"] == skill_source_ref
    )
    skill_package_file["sha256"] = _hash(skill_payload)
    for relative_path, payload in assets.items():
        source_ref = f"{module_root}/{relative_path}"
        source_path = root / source_ref
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_bytes(payload)
        manifest["portable_governance_skills"][0]["package_files"].append(
            {
                "source": source_ref,
                "sha256": _hash(payload),
                "embedded_resource_ids": [],
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


def test_runtime_module_requires_containing_skill_declaration(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(tmp_path, manifest_path)
    skill_path = (
        tmp_path / "09_soul/governance/skills/engineering-example/SKILL.md"
    )
    skill_payload = skill_path.read_bytes().replace(b"example_reviewer", b"other_module")
    skill_path.write_bytes(skill_payload)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["portable_governance_skills"][0]["package_files"][0][
        "sha256"
    ] = _hash(skill_payload)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="containing Skill does not declare independent Runtime Module",
    ):
        release.check_governance_skill_release(tmp_path, manifest_path)


def test_production_governance_skill_release_is_clean() -> None:
    report = release.check_governance_skill_release(REPO_ROOT)
    manifest = release.load_governance_skill_manifest(REPO_ROOT)

    assert report.is_clean
    assert report.skill_count == 7
    assert report.projection_count == sum(
        len(package_file.projections)
        for skill in manifest.portable_governance_skills
        for package_file in skill.package_files
    )
    expected_owners = {
        "the-task-routing": "designDoc/the_task_routing.md",
        "the-system-change": "designDoc/the_system_change_governance.md",
        "the-design-authoring": "designDoc/the_design_doc_management.md",
        "the-skill-authoring": "designDoc/the_skill_management.md",
        "the-review-authoring": "designDoc/the_review_contract.md",
        "engineering-code-design": "designDoc/the_software_delivery.md",
        "engineering-change-review": "designDoc/the_software_delivery.md",
    }
    actual = {
        skill.skill_id: (
            skill.accountable_owner_ref,
            skill.lifecycle_state,
            skill.managed_target_ref,
            skill.direct_entry_disposition,
            skill.predecessor_skill_id,
            skill.retirement_tombstone,
        )
        for skill in manifest.portable_governance_skills
    }
    assert actual == {
        skill_id: (owner, "developing", None, "active", None, None)
        for skill_id, owner in expected_owners.items()
    }
    for skill in manifest.portable_governance_skills:
        source = next(
            package_file
            for package_file in skill.package_files
            if package_file.source.endswith("/SKILL.md")
            and "/runtime_modules/" not in package_file.source
        )
        payload = (REPO_ROOT / source.source).read_bytes()
        assert _hash(payload) == source.sha256
        metadata = release._frontmatter_scalars(payload, source=source.source)
        assert metadata["first_authority_ref"] == skill.accountable_owner_ref


def test_v4_manifest_fails_with_stable_manifest_code(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    manifest["manifest_version"] = "governance_skill_manifest_v4"
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_MANIFEST_INVALID


@pytest.mark.parametrize(
    "payload",
    (
        b"\xff\xfe",
        b"{",
        b"[]",
    ),
)
def test_unreadable_or_invalid_manifest_uses_stable_manifest_code(
    tmp_path: Path, payload: bytes
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest_path.write_bytes(payload)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_MANIFEST_INVALID


def test_missing_owner_precedes_missing_lifecycle(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    row.pop("accountable_owner_ref")
    row.pop("lifecycle_state")
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_OWNER_INVALID


def test_missing_lifecycle_key_has_stable_code(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    _fixture_skill_row(manifest).pop("managed_target_ref")
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_LIFECYCLE_INVALID


def test_non_scalar_lifecycle_state_uses_stable_lifecycle_code(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    _fixture_skill_row(manifest)["lifecycle_state"] = []
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_LIFECYCLE_INVALID


def test_unknown_manifest_entry_key_has_stable_code(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    _fixture_skill_row(manifest)["invented_lifecycle_hint"] = "no"
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_MANIFEST_INVALID


def test_hash_valid_owner_mismatch_has_stable_owner_code(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    _fixture_skill_row(manifest)["accountable_owner_ref"] = (
        "designDoc/the_other.md"
    )
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.check_governance_skill_release(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_OWNER_INVALID
    assert "hash-validated frontmatter" in str(caught.value)


def test_complete_source_closure_precedes_owner_equality(tmp_path: Path) -> None:
    binding = b"## Project binding\n\nExact project seam.\n"
    manifest_path = _write_fixture_project(
        tmp_path, binding_payload=binding
    )
    manifest = _load_fixture_manifest(manifest_path)
    _fixture_skill_row(manifest)["accountable_owner_ref"] = (
        "designDoc/the_other.md"
    )
    _store_fixture_manifest(manifest_path, manifest)
    binding_path = tmp_path / "governance_bindings/skills/engineering-example.md"
    binding_path.write_bytes(binding + b"hash drift\n")

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.check_governance_skill_release(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID
    assert "project binding hash mismatch" in str(caught.value)


def test_soul_resource_payload_is_read_once_and_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    _fixture_skill_row(manifest)["required_soul_resource_ids"] = [
        "soul:communication"
    ]
    _store_fixture_manifest(manifest_path, manifest)
    resource_path = tmp_path / "09_soul/core/COMMUNICATION.md"
    resource_path.parent.mkdir(parents=True)
    resource_payload = b"# Communication\n"
    resource_path.write_bytes(resource_payload)
    original_read_bytes = Path.read_bytes
    resource_reads = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal resource_reads
        if path == resource_path:
            resource_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    validated = release._validated_governance_skill_release_input(tmp_path)

    assert resource_reads == 1
    assert validated.soul_resource_payloads == (
        ("soul:communication", resource_payload),
    )


@pytest.mark.parametrize(
    ("selector", "source_payload", "expected"),
    (
        (
            release.InstructionResourceSelector(
                kind="heading_range",
                start="## Selected",
                end_exclusive="## Next",
            ),
            b"# Source\n## Selected\nbody\n## Next\nnext\n",
            b"## Selected\nbody\n",
        ),
        (
            release.InstructionResourceSelector(
                kind="marker_range",
                start="<!-- start -->",
                end_exclusive="<!-- end -->",
            ),
            b"before\n<!-- start -->\nbody\n<!-- end -->\nafter\n",
            b"body\n",
        ),
        (
            release.InstructionResourceSelector(
                kind="whole_file",
                start=None,
                end_exclusive=None,
            ),
            b"whole resource\n",
            b"whole resource\n",
        ),
    ),
)
def test_instruction_resource_selectors_return_exact_bytes(
    selector: object,
    source_payload: bytes,
    expected: bytes,
) -> None:
    resource = release.InstructionResource(
        resource_id="soul:example_instruction",
        parent_dependency_id="soul:communication",
        source="09_soul/core/COMMUNICATION.md",
        selector=selector,
        sha256=_hash(expected),
    )

    assert release._select_instruction_resource_bytes(
        resource, source_payload
    ) == expected


def test_compose_replaces_only_declared_blocks_for_two_consumers() -> None:
    resource_id = "soul:example_instruction"
    selected = b"canonical instruction\n"
    consumers = (
        (
            b"first before\n"
            b"<!-- embedded-resource:soul:example_instruction:start -->\n"
            b"stale first\n"
            b"<!-- embedded-resource:soul:example_instruction:end -->\n"
            b"first after\n",
            b"first before\n"
            b"<!-- embedded-resource:soul:example_instruction:start -->\n"
            b"canonical instruction\n"
            b"<!-- embedded-resource:soul:example_instruction:end -->\n"
            b"first after\n",
        ),
        (
            b"second before\n"
            b"<!-- embedded-resource:soul:example_instruction:start -->\n"
            b"stale second\n"
            b"<!-- embedded-resource:soul:example_instruction:end -->\n"
            b"second after\n",
            b"second before\n"
            b"<!-- embedded-resource:soul:example_instruction:start -->\n"
            b"canonical instruction\n"
            b"<!-- embedded-resource:soul:example_instruction:end -->\n"
            b"second after\n",
        ),
    )

    for source, expected in consumers:
        assert release.compose_governance_skill_package_file(
            source,
            (resource_id,),
            {resource_id: selected},
        ) == expected


@pytest.mark.parametrize(
    ("selector", "source_payload", "match"),
    (
        (
            release.InstructionResourceSelector(
                kind="whole_file", start=None, end_exclusive=None
            ),
            b"\xff",
            "must be UTF-8",
        ),
        (
            release.InstructionResourceSelector(
                kind="whole_file", start=None, end_exclusive=None
            ),
            b"line\r\n",
            "must use LF",
        ),
        (
            release.InstructionResourceSelector(
                kind="heading_range",
                start="## Selected",
                end_exclusive="## Next",
            ),
            b"## Selected\none\n## Selected\ntwo\n## Next\n",
            "must occur exactly once",
        ),
        (
            release.InstructionResourceSelector(
                kind="heading_range",
                start="## Selected",
                end_exclusive="## Next",
            ),
            b"## Next\nnext\n## Selected\nselected\n",
            "boundaries are reversed",
        ),
        (
            release.InstructionResourceSelector(
                kind="marker_range",
                start="<!-- start -->",
                end_exclusive="<!-- end -->",
            ),
            b"<!-- start -->\n<!-- end -->\n",
            "must be non-empty and end with LF",
        ),
        (
            release.InstructionResourceSelector(
                kind="whole_file", start=None, end_exclusive=None
            ),
            b"no terminal LF",
            "must be non-empty and end with LF",
        ),
    ),
)
def test_instruction_selector_guards_reject(
    selector: object,
    source_payload: bytes,
    match: str,
) -> None:
    resource = release.InstructionResource(
        resource_id="soul:example_instruction",
        parent_dependency_id="soul:communication",
        source="09_soul/core/COMMUNICATION.md",
        selector=selector,
        sha256=_hash(b"irrelevant\n"),
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError, match=match
    ) as caught:
        release._select_instruction_resource_bytes(resource, source_payload)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID
    )


def test_instruction_resource_source_is_read_once_across_dependency_and_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"## Selected\ncanonical instruction\n",
    )
    resource_path = tmp_path / "09_soul/core/COMMUNICATION.md"
    original_read_bytes = Path.read_bytes
    resource_reads = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal resource_reads
        if path == resource_path:
            resource_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    validated = release._validated_governance_skill_release_input(tmp_path)

    assert resource_reads == 1
    assert len(validated.instruction_resource_payloads) == 1


def test_skill_owned_instruction_resource_selects_and_composes_exact_bytes(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    skill_payload, prompt_payload, selected_payload = (
        _add_skill_owned_instruction_resource_fixture(tmp_path, manifest_path)
    )

    validated = release._validated_governance_skill_release_input(tmp_path)
    prompt = next(
        payload
        for payload in validated.source_payloads
        if payload.package_file.source.endswith("example_reviewer/prompt.md")
    )

    assert validated.instruction_resource_payloads[0][1] == selected_payload
    assert prompt.source_payload == prompt_payload
    assert prompt.composed_source_payload == prompt_payload
    assert next(
        payload.source_payload
        for payload in validated.source_payloads
        if payload.package_file.source.endswith("/SKILL.md")
    ) == skill_payload


def test_skill_owned_source_skill_file_is_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_skill_owned_instruction_resource_fixture(tmp_path, manifest_path)
    skill_path = (
        tmp_path / "09_soul/governance/skills/engineering-example/SKILL.md"
    )
    original_read_bytes = Path.read_bytes
    skill_reads = 0

    def counted_read_bytes(path: Path) -> bytes:
        nonlocal skill_reads
        if path == skill_path:
            skill_reads += 1
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", counted_read_bytes)

    release._validated_governance_skill_release_input(tmp_path)

    assert skill_reads == 1


def test_manifest_rejects_unknown_skill_owned_resource_parent(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_skill_owned_instruction_resource_fixture(tmp_path, manifest_path)
    manifest = _load_fixture_manifest(manifest_path)
    manifest["instruction_resources"][0]["parent_dependency_id"] = (
        "skill:missing-skill"
    )
    manifest["instruction_resources"][0]["source"] = (
        "09_soul/governance/skills/missing-skill/SKILL.md"
    )
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(
        release.GovernanceSkillReleaseError, match="unknown parent Skill"
    ) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID
    )


def test_skill_owned_instruction_resource_rejects_non_skill_parent(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"## Selected\ncanonical instruction\n",
    )
    manifest = _load_fixture_manifest(manifest_path)
    manifest["instruction_resources"][0]["resource_id"] = (
        "skill:engineering_example"
    )
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="must use the skill: namespace together",
    ) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID
    )


@pytest.mark.parametrize(
    "source",
    (
        "09_soul/governance/skills/another-skill/SKILL.md",
        "09_soul/governance/skills/engineering-example/checklist.md",
    ),
)
def test_manifest_rejects_noncanonical_skill_owned_resource_source(
    tmp_path: Path,
    source: str,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_skill_owned_instruction_resource_fixture(tmp_path, manifest_path)
    manifest = _load_fixture_manifest(manifest_path)
    manifest["instruction_resources"][0]["source"] = source
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(
        release.GovernanceSkillReleaseError, match="exact owning SKILL.md"
    ) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID
    )


def test_manifest_rejects_cross_skill_owned_resource_consumption(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_skill_owned_instruction_resource_fixture(tmp_path, manifest_path)
    manifest = _load_fixture_manifest(manifest_path)
    owner = _fixture_skill_row(manifest)
    prompt_file = next(
        package_file
        for package_file in owner["package_files"]
        if str(package_file["source"]).endswith(
            "example_reviewer/prompt.md"
        )
    )
    owner["package_files"].remove(prompt_file)
    other_skill_payload = _skill_payload(name="other-skill")
    other_skill_source = (
        tmp_path / "09_soul/governance/skills/other-skill/SKILL.md"
    )
    other_skill_source.parent.mkdir(parents=True, exist_ok=True)
    other_skill_source.write_bytes(other_skill_payload)
    other_prompt_source = str(prompt_file["source"]).replace(
        "engineering-example", "other-skill"
    )
    other_prompt_path = tmp_path / other_prompt_source
    other_prompt_path.parent.mkdir(parents=True, exist_ok=True)
    original_prompt_path = tmp_path / str(prompt_file["source"])
    other_prompt_path.write_bytes(original_prompt_path.read_bytes())
    prompt_file["source"] = other_prompt_source
    prompt_file["projections"][0]["target"] = str(
        prompt_file["projections"][0]["target"]
    ).replace("engineering-example", "other-skill")
    other_skill = {
        **owner,
        "skill_id": "other-skill",
        "package_files": [
            {
                "source": (
                    "09_soul/governance/skills/other-skill/SKILL.md"
                ),
                "sha256": _hash(other_skill_payload),
                "embedded_resource_ids": [],
                "projections": [
                    {
                        "host_id": "claude",
                        "target": ".claude/skills/other-skill/SKILL.md",
                    },
                    {
                        "host_id": "codex",
                        "target": ".agents/skills/other-skill/SKILL.md",
                    },
                ],
            },
            prompt_file,
        ],
    }
    manifest["portable_governance_skills"].append(other_skill)
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="outside the Skill dependency closure",
    ) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
    )


def test_skill_owned_embedded_resource_drift_is_reported_and_apply_rejects(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_skill_owned_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"stale checklist\n",
    )

    report = release.check_governance_skill_release(tmp_path)

    assert any(
        issue.code == "governance_skill_embedded_block_drift"
        and issue.path.endswith("example_reviewer/prompt.md")
        for issue in report.issues
    )
    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.apply_governance_skill_release(tmp_path)
    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
    )


def test_compose_rejects_undeclared_skill_owned_marker() -> None:
    source_payload = (
        b"<!-- embedded-resource:skill:design_contract_review_checklist:start -->\n"
        b"body\n"
        b"<!-- embedded-resource:skill:design_contract_review_checklist:end -->\n"
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError, match="declarations differ"
    ) as caught:
        release.compose_governance_skill_package_file(
            source_payload,
            (),
            {},
        )

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
    )


@pytest.mark.parametrize(
    "resource_id",
    (
        "skill:invalid-id",
        "soul:invalid-id",
        "t0:invalid-id",
    ),
)
def test_embedded_marker_parser_does_not_widen_identity_grammar(
    resource_id: str,
) -> None:
    source_payload = (
        f"<!-- embedded-resource:{resource_id}:start -->\n"
        "body\n"
        f"<!-- embedded-resource:{resource_id}:end -->\n"
    ).encode("ascii")

    with pytest.raises(
        release.GovernanceSkillReleaseError, match="declarations differ"
    ) as caught:
        release.compose_governance_skill_package_file(
            source_payload,
            (resource_id,),
            {resource_id: b"canonical\n"},
        )

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
    )


def test_check_reports_embedded_instruction_drift(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"stale instruction\n",
    )

    report = release.check_governance_skill_release(tmp_path)

    assert any(
        issue.code == "governance_skill_embedded_block_drift"
        for issue in report.issues
    )


def test_embedded_drift_does_not_reclassify_accepted_projection_payload(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    source_payload, _selected = _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"stale instruction\n",
    )
    for target in (
        ".claude/skills/engineering-example/SKILL.md",
        ".agents/skills/engineering-example/SKILL.md",
    ):
        target_path = tmp_path / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(source_payload)

    report = release.check_governance_skill_release(tmp_path)
    issue_codes = {issue.code for issue in report.issues}

    assert "governance_skill_embedded_block_drift" in issue_codes
    assert "governance_skill_projection_drift" not in issue_codes


def test_apply_rejects_embedded_drift_before_any_projection_write(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"stale instruction\n",
    )

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.apply_governance_skill_release(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
    )
    assert not (
        tmp_path / ".claude/skills/engineering-example/SKILL.md"
    ).exists()
    assert not (
        tmp_path / ".agents/skills/engineering-example/SKILL.md"
    ).exists()


def test_apply_projects_exact_composed_source(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    source_payload, _selected = _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"## Selected\ncanonical instruction\n",
    )

    report = release.apply_governance_skill_release(tmp_path)

    assert report.is_clean
    assert (
        tmp_path / ".claude/skills/engineering-example/SKILL.md"
    ).read_bytes() == source_payload
    assert (
        tmp_path / ".agents/skills/engineering-example/SKILL.md"
    ).read_bytes() == source_payload


def test_manifest_rejects_undefined_embedded_resource(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    _fixture_skill_row(manifest)["package_files"][0][
        "embedded_resource_ids"
    ] = ["soul:missing_instruction"]
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
    )
    assert "undefined embedded resources" in str(caught.value)


def test_manifest_rejects_resource_outside_skill_dependency_closure(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"## Selected\ncanonical instruction\n",
    )
    manifest = _load_fixture_manifest(manifest_path)
    _fixture_skill_row(manifest)["required_soul_resource_ids"] = []
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
    )
    assert "outside the Skill dependency closure" in str(caught.value)


def test_instruction_resource_hash_mismatch_uses_stable_error(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"## Selected\ncanonical instruction\n",
    )
    manifest = _load_fixture_manifest(manifest_path)
    manifest["instruction_resources"][0]["sha256"] = "0" * 64
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.check_governance_skill_release(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID
    )
    assert "selection hash mismatch" in str(caught.value)


def test_missing_instruction_source_uses_stable_error(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"## Selected\ncanonical instruction\n",
    )
    manifest = _load_fixture_manifest(manifest_path)
    manifest["instruction_resources"][0]["source"] = (
        "09_soul/core/MISSING.md"
    )
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.check_governance_skill_release(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID
    )
    assert "cannot read instruction resource source" in str(caught.value)


def test_instruction_source_error_precedes_shared_soul_source_error(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"## Selected\ncanonical instruction\n",
    )
    (tmp_path / "09_soul/core/COMMUNICATION.md").unlink()

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.check_governance_skill_release(tmp_path)

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID
    )
    assert "cannot read instruction resource source" in str(caught.value)


@pytest.mark.parametrize(
    ("case", "match"),
    (
        ("unsupported_kind", "selector.kind is unsupported"),
        ("whole_file_boundaries", "boundaries must be null"),
        ("invalid_boundary", "exact non-empty lines"),
        ("invalid_hash", "64 lowercase hexadecimal"),
        ("duplicate_resource", "repeat resource_id"),
        ("invalid_parent", "parent_dependency_id is invalid"),
        ("invalid_skill_parent", "parent_dependency_id is invalid"),
        ("mismatched_skill_parent", "must use the skill: namespace together"),
        ("duplicate_embedded_id", "must not contain duplicates"),
        ("instruction_resources_not_array", "must be an array"),
        ("resource_not_object", "must be an object"),
        ("resource_extra_key", "keys mismatch"),
        ("selector_extra_key", "keys mismatch"),
        ("invalid_resource_id", "resource_id is invalid"),
        ("invalid_skill_resource_id", "resource_id is invalid"),
        ("invalid_source", "normalized repository-relative path"),
        ("invalid_embedded_element", "stable resource IDs"),
        ("invalid_skill_embedded_id", "stable resource IDs"),
        ("unknown_parent", "unknown parent dependency"),
    ),
)
def test_manifest_instruction_declaration_guards_reject(
    tmp_path: Path,
    case: str,
    match: str,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_instruction_resource_fixture(
        tmp_path,
        manifest_path,
        embedded_payload=b"## Selected\ncanonical instruction\n",
    )
    manifest = _load_fixture_manifest(manifest_path)
    resource = manifest["instruction_resources"][0]
    package_file = _fixture_skill_row(manifest)["package_files"][0]
    if case == "unsupported_kind":
        resource["selector"]["kind"] = "line_guess"
    elif case == "whole_file_boundaries":
        resource["selector"]["kind"] = "whole_file"
    elif case == "invalid_boundary":
        resource["selector"]["start"] = ""
    elif case == "invalid_hash":
        resource["sha256"] = "ABC"
    elif case == "duplicate_resource":
        manifest["instruction_resources"].append(dict(resource))
    elif case == "invalid_parent":
        resource["parent_dependency_id"] = "invalid-parent"
    elif case == "invalid_skill_parent":
        resource["parent_dependency_id"] = "skill:engineering_example"
    elif case == "mismatched_skill_parent":
        resource["parent_dependency_id"] = "skill:engineering-example"
    elif case == "duplicate_embedded_id":
        package_file["embedded_resource_ids"] *= 2
    elif case == "instruction_resources_not_array":
        manifest["instruction_resources"] = {}
    elif case == "resource_not_object":
        manifest["instruction_resources"] = [None]
    elif case == "resource_extra_key":
        resource["invented"] = "no"
    elif case == "selector_extra_key":
        resource["selector"]["invented"] = "no"
    elif case == "invalid_resource_id":
        resource["resource_id"] = "Soul:invalid"
    elif case == "invalid_skill_resource_id":
        resource["resource_id"] = "skill:invalid-id"
    elif case == "invalid_source":
        resource["source"] = "09_soul/../outside.md"
    elif case == "invalid_embedded_element":
        package_file["embedded_resource_ids"] = [7]
    elif case == "invalid_skill_embedded_id":
        package_file["embedded_resource_ids"] = ["skill:invalid-id"]
    elif case == "unknown_parent":
        resource["parent_dependency_id"] = "soul:unknown_resource"
        package_file["embedded_resource_ids"] = []
    else:  # pragma: no cover - the parameter table is closed above.
        raise AssertionError(case)
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(
        release.GovernanceSkillReleaseError, match=match
    ) as caught:
        if case == "unknown_parent":
            release.check_governance_skill_release(tmp_path)
        else:
            release.load_governance_skill_manifest(tmp_path)

    expected_code = (
        release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
        if case
        in {
            "duplicate_embedded_id",
            "invalid_embedded_element",
            "invalid_skill_embedded_id",
        }
        else release.GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID
    )
    assert caught.value.code == expected_code


def test_loader_parses_reviewer_module_id_from_audit_prompt_path(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    resource_id = "t0:review_contract_universal_review_style"
    selected = b"# Example T0\n"
    source_ref = (
        "09_soul/governance/skills/engineering-example/"
        "runtime_modules/example_reviewer/prompt.md"
    )
    source_payload = (
        b"<!-- embedded-resource:"
        + resource_id.encode("ascii")
        + b":start -->\n"
        + selected
        + b"<!-- embedded-resource:"
        + resource_id.encode("ascii")
        + b":end -->\n"
    )
    source_path = tmp_path / source_ref
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source_payload)
    manifest["instruction_resources"] = [
        {
            "resource_id": resource_id,
            "parent_dependency_id": "the_example",
            "source": "09_soul/governance/t0/the_example.md",
            "selector": {
                "kind": "whole_file",
                "start": None,
                "end_exclusive": None,
            },
            "sha256": _hash(selected),
        }
    ]
    _fixture_skill_row(manifest)["package_files"].append(
        {
            "source": source_ref,
            "sha256": _hash(source_payload),
            "embedded_resource_ids": [resource_id],
            "projections": [
                {
                    "host_id": "claude",
                    "target": (
                        ".claude/skills/engineering-example/"
                        "runtime_modules/example_reviewer/prompt.md"
                    ),
                }
            ],
        }
    )
    _store_fixture_manifest(manifest_path, manifest)

    loaded = release.load_governance_skill_manifest(tmp_path)
    prompt = next(
        package_file
        for package_file in loaded.portable_governance_skills[0].package_files
        if package_file.source == source_ref
    )

    assert prompt.reviewer_module_id == "example_reviewer"
    assert loaded.portable_governance_skills[0].package_files[0].reviewer_module_id is None


@pytest.mark.parametrize(
    ("source_payload", "resource_ids", "match"),
    (
        (
            b"no markers\n",
            ("soul:example_instruction",),
            "declarations differ",
        ),
        (
            (
                b"<!-- embedded-resource:soul:example_instruction:start -->\n"
                b"one\n"
                b"<!-- embedded-resource:soul:example_instruction:end -->\n"
                b"<!-- embedded-resource:soul:example_instruction:start -->\n"
                b"two\n"
                b"<!-- embedded-resource:soul:example_instruction:end -->\n"
            ),
            ("soul:example_instruction",),
            "exactly once",
        ),
        (
            (
                b"<!-- embedded-resource:soul:first:start -->\n"
                b"<!-- embedded-resource:soul:second:start -->\n"
                b"two\n"
                b"<!-- embedded-resource:soul:first:end -->\n"
                b"<!-- embedded-resource:soul:second:end -->\n"
            ),
            ("soul:first", "soul:second"),
            "overlap",
        ),
        (
            (
                b"<!-- embedded-resource:soul:example_instruction:start -->\n"
                b"body\n"
                b"<!-- embedded-resource:soul:example_instruction:end -->"
            ),
            ("soul:example_instruction",),
            "marker line must end with LF",
        ),
        (
            (
                b"<!-- embedded-resource:soul:example_instruction:end -->\n"
                b"body\n"
                b"<!-- embedded-resource:soul:example_instruction:start -->\n"
            ),
            ("soul:example_instruction",),
            "markers are reversed",
        ),
    ),
)
def test_compose_rejects_invalid_marker_closure(
    source_payload: bytes,
    resource_ids: tuple[str, ...],
    match: str,
) -> None:
    resources = {resource_id: b"canonical\n" for resource_id in resource_ids}

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match=match,
    ) as caught:
        release.compose_governance_skill_package_file(
            source_payload,
            resource_ids,
            resources,
        )

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
    )


def test_compose_rejects_duplicate_resource_id() -> None:
    resource_id = "soul:example_instruction"

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.compose_governance_skill_package_file(
            b"<!-- embedded-resource:soul:example_instruction:start -->\n"
            b"old\n"
            b"<!-- embedded-resource:soul:example_instruction:end -->\n",
            (resource_id, resource_id),
            {resource_id: b"canonical\n"},
        )

    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID
    )
    assert "duplicates" in str(caught.value)


@pytest.mark.parametrize(
    ("state", "target"),
    (
        ("migration_planned", "workflow:example_flow@candidate_v1"),
        ("migration_planned", "runtime-module:example_reviewer@v2"),
        ("migration_planned", "deterministic-integration:example_gate@v1"),
        ("managed", "workflow:example_flow@v1"),
        ("managed", "runtime-module:example_reviewer@v2"),
        ("managed", "deterministic-integration:example_gate@v1"),
    ),
)
def test_legal_managed_target_grammars_pass(
    tmp_path: Path, state: str, target: str
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    row["lifecycle_state"] = state
    row["managed_target_ref"] = target
    _store_fixture_manifest(manifest_path, manifest)

    loaded = release.load_governance_skill_manifest(tmp_path)

    assert loaded.portable_governance_skills[0].managed_target_ref == target


@pytest.mark.parametrize(
    ("state", "target"),
    (
        ("migration_planned", "workflow:example_flow@candidate_v0"),
        ("migration_planned", "unknown:example_flow@v1"),
        ("managed", "workflow:example_flow@candidate_v1"),
        ("managed", "runtime-module:Example@v1"),
        ("managed", "workflow:example__flow@v1"),
        ("managed", "workflow:example_flow_@v1"),
        ("managed", "deterministic-integration:example_gate@candidate_v1"),
    ),
)
def test_invalid_managed_target_grammars_fail(
    tmp_path: Path, state: str, target: str
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    row["lifecycle_state"] = state
    row["managed_target_ref"] = target
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_LIFECYCLE_INVALID


@pytest.mark.parametrize(
    "skill_id",
    ("Engineering-Example", "engineering--example", "engineering_example"),
)
def test_non_normalized_skill_identity_is_rejected(
    tmp_path: Path, skill_id: str
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    _fixture_skill_row(manifest)["skill_id"] = skill_id
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="kebab-case identity",
    ) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_MANIFEST_INVALID


def test_direct_entry_retired_tombstone_passes(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    roots = [
        ".claude/skills/legacy-governance",
        ".agents/skills/legacy-governance",
    ]
    manifest["forbidden_projection_roots"] = roots
    row["lifecycle_state"] = "direct_entry_retired"
    row["managed_target_ref"] = "runtime-module:example_reviewer@v1"
    row["direct_entry_disposition"] = "retired"
    row["retirement_tombstone"] = _direct_entry_tombstone()
    _store_fixture_manifest(manifest_path, manifest)

    loaded = release.load_governance_skill_manifest(tmp_path)

    tombstone = loaded.portable_governance_skills[0].retirement_tombstone
    assert tombstone is not None
    assert tombstone.effective_at_utc == "2026-08-24T12:00:00Z"
    assert tombstone.retired_projection_roots == tuple(roots)


def test_predecessor_tombstone_passes(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    roots = [
        ".claude/skills/old-engineering-example",
        ".agents/skills/old-engineering-example",
    ]
    manifest["forbidden_projection_roots"] = roots
    row["lifecycle_state"] = "direct_entry_retired"
    row["managed_target_ref"] = "workflow:example_flow@v1"
    row["direct_entry_disposition"] = "retired"
    row["predecessor_skill_id"] = "old-engineering-example"
    tombstone = _direct_entry_tombstone(
        retired_identity="old-engineering-example"
    )
    tombstone["retired_identity_kind"] = "predecessor_skill"
    row["retirement_tombstone"] = tombstone
    _store_fixture_manifest(manifest_path, manifest)

    loaded = release.load_governance_skill_manifest(tmp_path)

    assert (
        loaded.portable_governance_skills[0]
        .retirement_tombstone.retired_identity_kind
        == "predecessor_skill"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("replacement_skill_id", "another-skill"),
        ("final_sha256", "not-a-hash"),
        ("effective_at_utc", "2026-08-24T12:00:00-07:00"),
        ("retired_identity", "engineering-example"),
        ("retired_identity_kind", []),
    ),
)
def test_invalid_direct_entry_tombstone_relations_fail(
    tmp_path: Path, field: str, value: object
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    manifest["forbidden_projection_roots"] = [
        ".claude/skills/legacy-governance",
        ".agents/skills/legacy-governance",
    ]
    row["lifecycle_state"] = "direct_entry_retired"
    row["managed_target_ref"] = "runtime-module:example_reviewer@v1"
    row["direct_entry_disposition"] = "retired"
    tombstone = _direct_entry_tombstone()
    tombstone[field] = value
    row["retirement_tombstone"] = tombstone
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_LIFECYCLE_INVALID


def test_tombstone_rejects_extra_key(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    manifest["forbidden_projection_roots"] = [
        ".claude/skills/legacy-governance",
        ".agents/skills/legacy-governance",
    ]
    row["lifecycle_state"] = "direct_entry_retired"
    row["managed_target_ref"] = "runtime-module:example_reviewer@v1"
    row["direct_entry_disposition"] = "retired"
    tombstone = _direct_entry_tombstone()
    tombstone["historical_note"] = "not part of the contract"
    row["retirement_tombstone"] = tombstone
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_LIFECYCLE_INVALID


def test_predecessor_tombstone_requires_matching_identity(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    manifest["forbidden_projection_roots"] = [
        ".claude/skills/old-engineering-example",
        ".agents/skills/old-engineering-example",
    ]
    row["lifecycle_state"] = "direct_entry_retired"
    row["managed_target_ref"] = "workflow:example_flow@v1"
    row["direct_entry_disposition"] = "retired"
    row["predecessor_skill_id"] = "different-predecessor"
    tombstone = _direct_entry_tombstone(
        retired_identity="old-engineering-example"
    )
    tombstone["retired_identity_kind"] = "predecessor_skill"
    row["retirement_tombstone"] = tombstone
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_LIFECYCLE_INVALID


@pytest.mark.parametrize("mode", ("unregistered", "duplicate"))
def test_tombstone_roots_require_registered_unique_guards(
    tmp_path: Path, mode: str
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    manifest["forbidden_projection_roots"] = [
        ".claude/skills/legacy-governance",
        ".agents/skills/legacy-governance",
    ]
    row["lifecycle_state"] = "direct_entry_retired"
    row["managed_target_ref"] = "runtime-module:example_reviewer@v1"
    row["direct_entry_disposition"] = "retired"
    tombstone = _direct_entry_tombstone()
    if mode == "unregistered":
        tombstone["retired_projection_roots"] = [
            ".claude/skills/unregistered-governance"
        ]
        tombstone["retired_identity"] = "unregistered-governance"
    else:
        tombstone["retired_projection_roots"] = [
            ".claude/skills/legacy-governance",
            ".claude/skills/legacy-governance",
        ]
    row["retirement_tombstone"] = tombstone
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_LIFECYCLE_INVALID


def test_forbidden_root_cannot_overlap_active_skill(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    manifest["forbidden_projection_roots"] = [
        ".claude/skills/engineering-example"
    ]
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_LIFECYCLE_INVALID


def test_apply_uses_one_validated_input_through_post_write_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture_project(tmp_path)
    calls = {"load": 0, "source": 0}
    original_load = release.load_governance_skill_manifest
    original_source = release._validated_source_payloads

    def counted_load(*args: object, **kwargs: object) -> object:
        calls["load"] += 1
        return original_load(*args, **kwargs)

    def counted_source(*args: object, **kwargs: object) -> object:
        calls["source"] += 1
        return original_source(*args, **kwargs)

    monkeypatch.setattr(release, "load_governance_skill_manifest", counted_load)
    monkeypatch.setattr(release, "_validated_source_payloads", counted_source)

    report = release.apply_governance_skill_release(tmp_path)

    assert report.is_clean
    assert calls == {"load": 1, "source": 1}


def test_check_uses_one_shared_validated_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture_project(tmp_path)
    calls = {"load": 0, "source": 0}
    original_load = release.load_governance_skill_manifest
    original_source = release._validated_source_payloads

    def counted_load(*args: object, **kwargs: object) -> object:
        calls["load"] += 1
        return original_load(*args, **kwargs)

    def counted_source(*args: object, **kwargs: object) -> object:
        calls["source"] += 1
        return original_source(*args, **kwargs)

    monkeypatch.setattr(release, "load_governance_skill_manifest", counted_load)
    monkeypatch.setattr(release, "_validated_source_payloads", counted_source)

    release.check_governance_skill_release(tmp_path)

    assert calls == {"load": 1, "source": 1}


def test_apply_writes_nothing_when_input_validation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    manifest["manifest_version"] = "governance_skill_manifest_v4"
    _store_fixture_manifest(manifest_path, manifest)
    writes = 0

    def counted_write(_target: Path, _payload: bytes) -> None:
        nonlocal writes
        writes += 1

    monkeypatch.setattr(release, "_write_bytes_atomically", counted_write)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.apply_governance_skill_release(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_MANIFEST_INVALID
    assert writes == 0


def test_projection_write_failure_is_typed_and_retry_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture_project(tmp_path)
    original_write = release._write_bytes_atomically
    calls = 0

    def fail_second_write(target: Path, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic write failure")
        original_write(target, payload)

    monkeypatch.setattr(release, "_write_bytes_atomically", fail_second_write)
    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.apply_governance_skill_release(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_PROJECTION_WRITE_FAILED
    assert ".agents/skills/engineering-example/SKILL.md" in str(caught.value)
    assert (
        tmp_path / ".claude/skills/engineering-example/SKILL.md"
    ).is_file()
    assert not (
        tmp_path / ".agents/skills/engineering-example/SKILL.md"
    ).exists()

    monkeypatch.setattr(release, "_write_bytes_atomically", original_write)
    assert release.apply_governance_skill_release(tmp_path).is_clean


def test_cli_prints_typed_error_code_and_exits_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    manifest["manifest_version"] = "governance_skill_manifest_v4"
    _store_fixture_manifest(manifest_path, manifest)

    exit_code = release.main(["--check", "--project-root", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert captured.err.startswith(
        f"{release.GOVERNANCE_SKILL_MANIFEST_INVALID}: "
    )


def test_skill_authoring_method_is_distinct_from_skill_management_t0() -> None:
    manifest = release.load_governance_skill_manifest(REPO_ROOT)
    skill = next(
        item
        for item in manifest.portable_governance_skills
        if item.skill_id == "the-skill-authoring"
    )

    assert skill.required_t0_layer_ids == (
        "the_review_contract",
        "the_skill_management",
        "the_system_change_governance",
    )
    assert skill.primary_agent_entry_subject == "system_change_plan_step"
    skill_source = next(
        package_file
        for package_file in skill.package_files
        if package_file.source.endswith("/SKILL.md")
    )
    assert {
        projection.target
        for projection in skill_source.projections
    } == {
        ".claude/skills/the-skill-authoring/SKILL.md",
        ".agents/skills/the-skill-authoring/SKILL.md",
    }
    module_assets = [
        package_file
        for package_file in skill.package_files
        if "/runtime_modules/" in package_file.source
    ]
    assert module_assets
    assert all(
        "/runtime_modules/skill_candidate_reviewer/" in package_file.source
        for package_file in module_assets
    )
    assert not any(
        "/runtime_modules/the_skill_authoring/" in package_file.source
        for package_file in skill.package_files
    )
    retired_module_root = (
        REPO_ROOT
        / "09_soul/governance/skills/the-skill-authoring/runtime_modules/"
        "the_skill_authoring"
    )
    assert not any(path.is_file() for path in retired_module_root.rglob("*"))
    for package_file in module_assets:
        assert {
            (projection.host_id, projection.target)
            for projection in package_file.projections
        } == {
            (
                "claude",
                package_file.source.replace(
                    "09_soul/governance/skills/",
                    ".claude/skills/",
                    1,
                ),
            )
        }
    assert {
        ".claude/skills/the-skill-management",
        ".agents/skills/the-skill-management",
    }.issubset(set(manifest.forbidden_projection_roots))


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
        "schema:engineering_change_reviewer_output@v4"
    )
    assert schema["$id"] == registration["output_schema_ref"]
    assert schema["properties"]["software_delivery_readiness"]["enum"] == [
        "accepted",
        "changes_required",
        "not_reproducible",
    ]
    assert all(
        clause[branch]["type"] == "object"
        for clause in schema["allOf"]
        for branch in ("if", "then")
    )


def test_engineering_reviewer_input_carries_plan_body_and_exact_commit() -> None:
    module_root = (
        REPO_ROOT
        / "09_soul/governance/skills/engineering-change-review/runtime_modules/"
        "engineering_change_reviewer"
    )
    registration = json.loads(
        (module_root / "module_registration.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (module_root / "schemas/input.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    assert registration["input_schema_ref"] == schema["$id"]
    assert schema["properties"]["system_change_plan_step"] == {
        "$ref": "#/$defs/hashed_body"
    }

    hashed_body = {"ref": "ref", "sha256": "a" * 64, "body": "body"}
    payload = {
        "schema_version": "engineering_change_reviewer_input_v4",
        "module_id": "engineering_change_reviewer",
        "system_change_plan_step": hashed_body,
        "subject": {
            "commit_ref": "1" * 40,
            "parent_ref": "2" * 40,
            "subject_sha256": "b" * 64,
            "diff_sha256": "c" * 64,
            "paths": [
                {
                    "path": "src/example.py",
                    "state": "modified",
                    "content_sha256": "d" * 64,
                }
            ],
        },
        "code_design_basis": hashed_body,
        "sandbox_command_plan": {
            "ref": "commands",
            "sha256": "e" * 64,
            "commands": [],
        },
        "acceptance_criteria": ["passes"],
        "prior_findings": [],
    }
    validator = Draft202012Validator(schema)
    validator.validate(payload)
    with pytest.raises(ValidationError):
        validator.validate(
            payload
            | {
                "subject": payload["subject"] | {"commit_ref": None},
            }
        )
    with pytest.raises(ValidationError):
        validator.validate(payload | {"change_set_manifest": hashed_body})
    with pytest.raises(ValidationError):
        validator.validate(
            payload
            | {"subject": payload["subject"] | {"subject_mode": "commit_ref"}}
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
        / "09_soul/governance/skills/the-design-authoring/runtime_modules"
        / "design_contract_reviewer/schemas/input.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["$defs"]["candidate_document"]["properties"]["layer"][
        "enum"
    ] == ["Charter", "T0", "T1", "T2"]


def test_design_reviewer_source_belongs_to_design_authoring_package() -> None:
    manifest = json.loads(
        (REPO_ROOT / "09_soul/governance/governance_skill_manifest.json")
        .read_text(encoding="utf-8")
    )
    packages = {
        row["skill_id"]: {item["source"] for item in row["package_files"]}
        for row in manifest["portable_governance_skills"]
    }
    source_prefix = (
        "09_soul/governance/skills/the-design-authoring/runtime_modules/"
        "design_contract_reviewer/"
    )

    assert any(
        source.startswith(source_prefix)
        for source in packages["the-design-authoring"]
    )
    assert "the-contract-audit" not in packages

    registration = json.loads(
        (
            REPO_ROOT
            / source_prefix
            / "module_registration.json"
        ).read_text(encoding="utf-8")
    )
    assert registration["skill_id"] == "the-design-authoring"
    assert registration["owner_contract_path"] == (
        "designDoc/the_design_doc_management.md"
    )


def test_system_change_reviewer_source_belongs_to_system_change_package() -> None:
    manifest = json.loads(
        (REPO_ROOT / "09_soul/governance/governance_skill_manifest.json")
        .read_text(encoding="utf-8")
    )
    packages = {
        row["skill_id"]: {item["source"] for item in row["package_files"]}
        for row in manifest["portable_governance_skills"]
    }
    source_prefix = (
        "09_soul/governance/skills/the-system-change/runtime_modules/"
        "system_change_plan_reviewer/"
    )

    assert any(
        source.startswith(source_prefix)
        for source in packages["the-system-change"]
    )
    assert "the-contract-audit" not in packages

    registration = json.loads(
        (
            REPO_ROOT
            / source_prefix
            / "module_registration.json"
        ).read_text(encoding="utf-8")
    )
    assert registration["module_id"] == "system_change_plan_reviewer"
    assert registration["skill_id"] == "the-system-change"
    assert registration["owner_contract_path"] == (
        "designDoc/the_system_change_governance.md"
    )


def test_skill_reviewer_source_belongs_to_skill_authoring_package() -> None:
    manifest = json.loads(
        (REPO_ROOT / "09_soul/governance/governance_skill_manifest.json")
        .read_text(encoding="utf-8")
    )
    packages = {
        row["skill_id"]: {item["source"] for item in row["package_files"]}
        for row in manifest["portable_governance_skills"]
    }
    source_prefix = (
        "09_soul/governance/skills/the-skill-authoring/runtime_modules/"
        "skill_candidate_reviewer/"
    )

    assert any(
        source.startswith(source_prefix)
        for source in packages["the-skill-authoring"]
    )
    assert "the-contract-audit" not in packages

    registration = json.loads(
        (
            REPO_ROOT
            / source_prefix
            / "module_registration.json"
        ).read_text(encoding="utf-8")
    )
    assert registration["skill_id"] == "the-skill-authoring"
    assert registration["owner_contract_path"] == (
        "designDoc/the_skill_management.md"
    )


def test_structure_review_is_merged_into_design_reviewer() -> None:
    manifest = json.loads(
        (REPO_ROOT / "09_soul/governance/governance_skill_manifest.json")
        .read_text(encoding="utf-8")
    )
    package_sources = {
        item["source"]
        for row in manifest["portable_governance_skills"]
        for item in row["package_files"]
    }
    module_root = (
        REPO_ROOT
        / "09_soul/governance/skills/the-design-authoring/runtime_modules"
        / "design_contract_reviewer"
    )
    input_schema = json.loads(
        (module_root / "schemas/input.schema.json").read_text(encoding="utf-8")
    )
    prompt = (module_root / "prompt.md").read_text(encoding="utf-8")
    flattened = " ".join(prompt.split())

    assert not any("structure_change_reviewer/" in source for source in package_sources)
    assert not (
        REPO_ROOT
        / "09_soul/governance/skills/the-contract-audit/runtime_modules"
        / "structure_change_reviewer"
    ).exists()
    assert "peer_boundary_design_review" in input_schema["properties"][
        "review_request"
    ]["properties"]["review_purpose"]["enum"]
    assert "peer_contract" in input_schema["$defs"]["context_document"][
        "properties"
    ]["context_role"]["enum"]
    assert "peer_authority_and_inheritance" in flattened
    assert "拆分、合并" in flattened
    assert "predecessor/successor coverage" in flattened
    assert "StructureChangeProposal" in flattened
    assert "不能从 ambient repository 推断" in flattened


def test_system_change_plan_reviewer_has_no_general_routing_registry_contract() -> None:
    module_root = (
        REPO_ROOT
        / "09_soul/governance/skills/the-system-change/runtime_modules"
        / "system_change_plan_reviewer"
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
    flattened = " ".join(prompt.split())
    assert "只判断规划和路由" in flattened
    assert "不审核或编写下游 Design" in flattened
    assert "general Task Routing Registry" not in flattened


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
            if relative.parts[2] != "tests":
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


def test_non_scalar_runtime_validation_case_kind_uses_source_closure_error(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(tmp_path, manifest_path)
    source_ref = (
        "09_soul/governance/skills/engineering-example/runtime_modules/"
        "example_reviewer/tests/negative_case.json"
    )
    source_path = tmp_path / source_ref
    fixture = json.loads(source_path.read_text(encoding="utf-8"))
    fixture["case_kind"] = []
    payload = json.dumps(fixture).encode("utf-8")
    source_path.write_bytes(payload)
    manifest = _load_fixture_manifest(manifest_path)
    package_file = next(
        item
        for item in _fixture_skill_row(manifest)["package_files"]
        if item["source"] == source_ref
    )
    package_file["sha256"] = _hash(payload)
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.check_governance_skill_release(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID
    assert "unsupported case_kind" in str(caught.value)


def test_reviewer_module_prompts_share_one_universal_review_style() -> None:
    manifest = release.load_governance_skill_manifest(REPO_ROOT)
    artifact_contracts_by_source = release._known_t0_artifact_contracts(
        REPO_ROOT
    )
    instruction_payloads = {
        item.resource_id: release._select_instruction_resource_bytes(
            item,
            (REPO_ROOT / item.source).read_bytes(),
            project_root=REPO_ROOT,
            artifact_contracts_by_source=artifact_contracts_by_source,
        )
        for item in manifest.instruction_resources
    }
    expected_paths_by_resource = {
        "t0:review_contract_universal_review_style": {
            "09_soul/governance/skills/engineering-change-review/SKILL.md",
            "09_soul/governance/skills/the-system-change/runtime_modules/"
            "system_change_plan_reviewer/prompt.md",
            "09_soul/governance/skills/the-design-authoring/runtime_modules/"
            "design_contract_reviewer/prompt.md",
            "09_soul/governance/skills/the-skill-authoring/runtime_modules/"
            "skill_candidate_reviewer/prompt.md",
            "09_soul/governance/skills/the-review-authoring/runtime_modules/"
            "reviewer_reviewer/prompt.md",
            "09_soul/governance/skills/engineering-change-review/runtime_modules/"
            "engineering_change_reviewer/prompt.md",
        },
    }
    assert "t0:contract_audit_universal_review_style" not in instruction_payloads
    for resource_id, expected_paths in expected_paths_by_resource.items():
        consumers = {
            package_file.source
            for skill in manifest.portable_governance_skills
            for package_file in skill.package_files
            if resource_id in package_file.embedded_resource_ids
        }

        assert consumers == expected_paths
        for relative_path in consumers:
            package_file = next(
                item
                for skill in manifest.portable_governance_skills
                for item in skill.package_files
                if item.source == relative_path
            )
            source = (REPO_ROOT / package_file.source).read_bytes()
            assert release.compose_governance_skill_package_file(
                source,
                package_file.embedded_resource_ids,
                instruction_payloads,
            ) == source


def test_skill_candidate_reviewer_owns_complete_subject_method() -> None:
    skill_source = (
        REPO_ROOT
        / "09_soul/governance/skills/the-skill-authoring/SKILL.md"
    ).read_text(encoding="utf-8")
    prompt_source = (
        REPO_ROOT
        / "09_soul/governance/skills/the-skill-authoring/runtime_modules/"
        "skill_candidate_reviewer/prompt.md"
    ).read_text(encoding="utf-8")

    assert "Skill Candidate Review Checklist Source" not in skill_source
    assert "skill-specific-review-checklist" not in skill_source
    assert "## 1. Review Task" in prompt_source
    assert "## 4. Design Review Checklist" in prompt_source
    for check_id in (
        "identity_discovery_class_and_source",
        "task_and_reader_gain",
        "entry_exit_and_routing",
        "inputs_authority_freshness_and_conflicts",
        "outputs_completion_failure_and_handoff",
        "boundaries_and_observable_violations",
        "method_result_certainty_and_agent_freedom",
        "design_and_revision_fidelity",
        "prompt_boundary_hygiene",
        "skill_agent_workflow_tool_separation",
        "runtime_ready_prompt_closure_if_declared",
    ):
        assert f"`{check_id}`" in prompt_source
    for non_semantic_check_id in (
        "host_projection_closure",
        "migration_and_retirement",
    ):
        assert non_semantic_check_id not in prompt_source
    assert "`review_checklist`" not in prompt_source
    assert "Reviewer 不重复执行 mechanical checks" in prompt_source
    assert "同一次调用中返回全部 actionable findings" in prompt_source
    assert "不能设置固定 finding 数量" in prompt_source
    assert "skill_candidate_artifact_contract" not in prompt_source
    assert prompt_source.count(
        "embedded-resource:t0:review_contract_universal_review_style:start"
    ) == 1
    assert prompt_source.count(
        "embedded-resource:t0:review_contract_universal_review_style:end"
    ) == 1
    prompt_check_positions = [
        prompt_source.index(f"{index}. `{check_id}`")
        for index, check_id in enumerate(
            (
                "identity_discovery_class_and_source",
                "task_and_reader_gain",
                "entry_exit_and_routing",
                "inputs_authority_freshness_and_conflicts",
                "outputs_completion_failure_and_handoff",
                "boundaries_and_observable_violations",
                "method_result_certainty_and_agent_freedom",
                "design_and_revision_fidelity",
                "prompt_boundary_hygiene",
                "skill_agent_workflow_tool_separation",
                "runtime_ready_prompt_closure_if_declared",
            ),
            start=1,
        )
    ]
    assert prompt_check_positions == sorted(prompt_check_positions)


def test_skill_reviewer_slice_projections_are_exact(tmp_path: Path) -> None:
    manifest = json.loads(
        (REPO_ROOT / "09_soul/governance/governance_skill_manifest.json")
        .read_text(encoding="utf-8")
    )
    for ambient_skill in manifest["portable_governance_skills"]:
        if ambient_skill["skill_id"] == "the-skill-authoring":
            continue
        for package_file in ambient_skill["package_files"]:
            package_file["sha256"] = _hash(
                (REPO_ROOT / package_file["source"]).read_bytes()
            )
    manifest_path = tmp_path / "governance_skill_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False),
        encoding="utf-8",
    )

    report = release.check_governance_skill_release(REPO_ROOT, manifest_path)
    slice_issues = [
        issue
        for issue in report.issues
        if issue.path.startswith(
            "09_soul/governance/skills/the-skill-authoring/"
        )
        or issue.path.startswith(".claude/skills/the-skill-authoring/")
        or issue.path.startswith(".agents/skills/the-skill-authoring/")
    ]

    assert slice_issues == []
    assert not any(
        "/runtime_modules/the_skill_authoring/" in issue.path
        for issue in report.issues
    )


def test_skill_authoring_has_indexed_semantic_structure_and_owned_exits() -> None:
    source = (
        REPO_ROOT
        / "09_soul/governance/skills/the-skill-authoring/SKILL.md"
    ).read_text(encoding="utf-8")

    assert [
        line
        for line in source.splitlines()
        if line.startswith("## ") and line[3:4].isdigit()
    ] == [
        "## 0. AI-facing Authoring Rules",
        "## 1. Task",
        "## 2. Reader Gain",
        "## 3. Entry and Exit",
        "## 4. Execution Contract",
        "## 5. Boundaries",
        "## 6. Method",
    ]
    for subheading in (
        line
        for line in source.splitlines()
        if line.startswith("### ") and line[4:5].isdigit()
    ):
        prefix = subheading.removeprefix("### ").split(" ", 1)[0]
        parent, child = prefix.split(".")
        assert parent.isdigit() and child.isdigit()
    for disposition in (
        "`blocked_reproducibility`",
        "`blocked_boundary`",
        "`blocked_owner`",
    ):
        assert disposition in source
    assert "本 Skill 不计算 candidate hash" in source
    assert "Authoring invocation 加载 Reviewer prompt 自审" in source
    assert "`soul:bestpractice_ai_facing_writing`" in source
    assert (
        "embedded-resource:soul:bestpractice_ai_facing_writing:start" in source
    )
    assert "安装的 `bestpractice_skill_writing.md`" not in source
    retired_host_root = (
        REPO_ROOT
        / ".claude/skills/the-skill-authoring/runtime_modules/"
        "the_skill_authoring"
    )
    assert not any(path.is_file() for path in retired_host_root.rglob("*"))


def test_skill_management_defines_skill_meaning_without_owning_file_layout() -> None:
    contract = (
        REPO_ROOT / "09_soul/governance/t0/the_skill_management.md"
    ).read_text(encoding="utf-8")

    required_headings = (
        "`Task`",
        "`Reader Gain`",
        "`Entry and Exit`",
        "`Execution Contract`",
        "`Boundaries`",
        "`Method`",
    )
    complete_skill = contract.split("### 6.1 完整 Skill", 1)[1].split(
        "### 6.2 Skill class", 1
    )[0]

    for heading in required_headings:
        assert complete_skill.count(heading) == 1
    assert "具体 heading 编号" in complete_skill
    assert "`the-skill-authoring` 与 code-owned enforcement" in complete_skill
    assert "这些是系统级完整性要求" in complete_skill


def test_ddm_and_skill_review_instruction_consumer_mapping_is_exact() -> None:
    manifest = release.load_governance_skill_manifest(REPO_ROOT)
    expected = {
        "09_soul/governance/skills/the-skill-authoring/runtime_modules/"
        "skill_candidate_reviewer/prompt.md": (
            "t0:review_contract_universal_review_style",
            "t0:skill_candidate_review_checklist",
        ),
        "09_soul/governance/skills/the-design-authoring/runtime_modules/"
        "design_contract_reviewer/prompt.md": (
            "t0:review_contract_universal_review_style",
            "t0:design_contract_review_checklist",
        ),
    }
    expected_sources = set(expected)
    actual = {
        package_file.source: package_file.embedded_resource_ids
        for skill in manifest.portable_governance_skills
        for package_file in skill.package_files
        if package_file.source in expected_sources
    }

    assert actual == expected
    reviewer_module_ids = {
        package_file.reviewer_module_id
        for skill in manifest.portable_governance_skills
        for package_file in skill.package_files
        if package_file.reviewer_module_id is not None
    }
    assert reviewer_module_ids == {
        "design_contract_reviewer",
        "engineering_change_reviewer",
        "reviewer_reviewer",
        "skill_candidate_reviewer",
        "system_change_plan_reviewer",
    }


def test_skill_reviewer_contract_is_exact_for_one_skill_candidate() -> None:
    module_root = (
        REPO_ROOT
        / "09_soul/governance/skills/the-skill-authoring/runtime_modules/"
        "skill_candidate_reviewer"
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
    Draft202012Validator.check_schema(input_schema)
    Draft202012Validator.check_schema(output_schema)
    assert registration["input_schema_ref"] == input_schema["$id"]
    assert registration["output_schema_ref"] == output_schema["$id"]

    skill_checks = [
        item["const"]
        for item in input_schema["$defs"][
            "skill_candidate_required_check_ids"
        ]["prefixItems"]
    ]
    assert len(skill_checks) == 11
    assert skill_checks[-1] == "runtime_ready_prompt_closure_if_declared"

    shared_input = {
        "schema_version": "skill_candidate_reviewer_input_v9",
        "module_id": "skill_candidate_reviewer",
        "owning_design_closure": [
            {
                "document_id": "owner",
                "document_kind": "design_contract",
                "body": "owner",
            }
        ],
        "source_and_projection_closure": [
            {
                "document_id": "source",
                "document_kind": "skill",
                "body": "source",
            }
        ],
        "prior_findings": [],
    }
    zero_prompt_payload = shared_input | {
        "skill_candidate": {
            "skill_id": "example",
            "candidate_revision": 1,
            "candidate_body": "body",
            "runtime_ready_prompts": [],
        },
        "required_check_ids": skill_checks,
    }
    multiple_prompt_payload = shared_input | {
        "skill_candidate": {
            "skill_id": "example",
            "candidate_revision": 1,
            "candidate_body": "body",
            "runtime_ready_prompts": [
                {"prompt_id": "author", "prompt_body": "complete author prompt"},
                {"prompt_id": "reviewer", "prompt_body": "complete reviewer prompt"},
            ],
        },
        "required_check_ids": skill_checks,
    }
    validator = Draft202012Validator(input_schema)
    validator.validate(zero_prompt_payload)
    validator.validate(multiple_prompt_payload)
    with pytest.raises(ValidationError):
        validator.validate(
            shared_input
            | {
                "project_binding_addendum": {
                    "skill_id": "example",
                    "binding_source_ref": "binding:example",
                    "addendum_body": "body",
                },
                "required_check_ids": skill_checks,
            }
        )
    with pytest.raises(ValidationError):
        validator.validate(zero_prompt_payload | {"unexpected": True})
    with pytest.raises(ValidationError):
        validator.validate(
            zero_prompt_payload
            | {
                "skill_candidate": {
                    **zero_prompt_payload["skill_candidate"],
                    "change_disposition": "update",
                }
            }
        )
    with pytest.raises(ValidationError):
        validator.validate(
            zero_prompt_payload
            | {
                "skill_candidate": {
                    **zero_prompt_payload["skill_candidate"],
                    "runtime_ready_prompts": [
                        {"prompt_id": "incomplete"}
                    ],
                }
            }
        )

    output_check_ids = set(output_schema["$defs"]["check_id"]["enum"])
    assert output_check_ids == set(skill_checks)
    assert "prose_and_meaning_preservation" not in output_check_ids
    assert "prose_and_meaning_preservation" in output_schema["required"]
    assert output_schema["properties"]["reviewed_subject_kind"] == {
        "const": "skill_candidate"
    }

    output_validator = Draft202012Validator(output_schema)
    skill_output = {
        "reviewed_subject_kind": "skill_candidate",
        "layer_disposition": "passed",
        "prose_and_meaning_preservation": "same bytes; no prose defect",
        "check_results": [
            {
                "check_id": check_id,
                "disposition": "passed",
                "assessment": f"candidate evidence for {check_id}",
                "finding_ids": [],
            }
            for check_id in skill_checks
        ],
        "findings": [],
        "safe_next_step": "accountable owner decision",
    }
    output_validator.validate(skill_output)
    no_prompt_output = skill_output | {
        "check_results": [
            *skill_output["check_results"][:-1],
            {
                "check_id": "runtime_ready_prompt_closure_if_declared",
                "disposition": "not_applicable",
                "assessment": "candidate declares no Runtime-ready prompt",
                "finding_ids": [],
            },
        ]
    }
    output_validator.validate(no_prompt_output)
    with pytest.raises(ValidationError):
        output_validator.validate(
            skill_output | {"reviewed_subject_kind": "project_binding_addendum"}
        )
    with pytest.raises(ValidationError):
        output_validator.validate(
            skill_output
            | {
                "check_results": [
                    {
                        key: value
                        for key, value in skill_output["check_results"][0].items()
                        if key != "assessment"
                    },
                    *skill_output["check_results"][1:],
                ]
            }
        )
    with pytest.raises(ValidationError):
        output_validator.validate(
            skill_output
            | {
                "check_results": [
                    {
                        **skill_output["check_results"][0],
                        "disposition": "not_applicable",
                    },
                    *skill_output["check_results"][1:],
                ]
            }
        )
    with pytest.raises(ValidationError):
        output_validator.validate(
            skill_output | {"check_results": skill_output["check_results"][:-1]}
        )
    with pytest.raises(ValidationError):
        output_validator.validate(
            skill_output
            | {
                "check_results": [
                    skill_output["check_results"][1],
                    skill_output["check_results"][0],
                    *skill_output["check_results"][2:],
                ]
            }
        )
    with pytest.raises(ValidationError):
        output_validator.validate(
            skill_output
            | {
                "check_results": [
                    {
                        "check_id": skill_checks[0],
                        "disposition": "finding",
                        "assessment": "candidate is incomplete",
                        "finding_ids": ["finding-1"],
                    },
                    *skill_output["check_results"][1:],
                ],
                "findings": [
                    {
                        "finding_id": "finding-1",
                        "severity": "fix",
                        "check_id": skill_checks[0],
                        "evidence": "candidate is incomplete",
                        "accountable_owner_ref": "designDoc/the_skill_management.md",
                        "required_change": "restore the missing result",
                    }
                ],
            }
        )
    with pytest.raises(ValidationError):
        output_validator.validate(
            skill_output
            | {
                "layer_disposition": "non_pass",
                "check_results": [
                    {
                        **skill_output["check_results"][0],
                        "disposition": "finding",
                    },
                    *skill_output["check_results"][1:],
                ],
                "findings": [
                    {
                        "finding_id": "finding-1",
                        "severity": "fix",
                        "check_id": skill_checks[0],
                        "evidence": "candidate is incomplete",
                        "accountable_owner_ref": "designDoc/the_skill_management.md",
                        "required_change": "restore the missing result",
                    }
                ],
            }
        )


def test_system_change_plan_reviewer_output_check_ids_are_closed() -> None:
    module_root = (
        REPO_ROOT
        / "09_soul/governance/skills/the-system-change/runtime_modules/"
        "system_change_plan_reviewer"
    )
    input_schema = json.loads(
        (module_root / "schemas/input.schema.json").read_text(encoding="utf-8")
    )
    output_schema = json.loads(
        (module_root / "schemas/output.schema.json").read_text(encoding="utf-8")
    )
    expected = {
        item["const"]
        for item in input_schema["properties"]["required_check_ids"][
            "prefixItems"
        ]
    }
    assert set(output_schema["$defs"]["semantic_check_id"]["enum"]) == expected
    assert output_schema["$defs"]["semantic_check_results"]["uniqueItems"] is True
    assert "prose_and_meaning_preservation" not in expected
    assert "prose_result" in output_schema["required"]


def test_manifest_rejects_incomplete_runtime_module_export(
    tmp_path: Path,
) -> None:
    manifest = json.loads(
        (REPO_ROOT / "09_soul/governance/governance_skill_manifest.json")
        .read_text(encoding="utf-8")
    )
    design_authoring = next(
        row
        for row in manifest["portable_governance_skills"]
        if row["skill_id"] == "the-design-authoring"
    )
    for package_file in design_authoring["package_files"]:
        package_file["sha256"] = _hash(
            (REPO_ROOT / package_file["source"]).read_bytes()
        )
    design_authoring["package_files"] = [
        row
        for row in design_authoring["package_files"]
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
        release.check_governance_skill_release(REPO_ROOT, manifest_path)


def test_manifest_rejects_runtime_module_without_validation_case(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(
        tmp_path, manifest_path, include_validation_case=False
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="requires declared test fixtures",
    ):
        release.check_governance_skill_release(tmp_path, manifest_path)


def test_manifest_rejects_incomplete_validation_case_declaration(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    _add_runtime_module_fixture(tmp_path, manifest_path)
    case_path = (
        tmp_path
        / "09_soul/governance/skills/engineering-example/runtime_modules/"
        "example_reviewer/tests/negative_case.json"
    )
    fixture = json.loads(case_path.read_text(encoding="utf-8"))
    fixture.pop("expected_disposition")
    case_path.write_text(json.dumps(fixture), encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for package_file in manifest["portable_governance_skills"][0][
        "package_files"
    ]:
        if package_file["source"].endswith(
            "tests/negative_case.json"
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
        tmp_path, manifest_path, registration_module_id="other_reviewer"
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="directory and module_id must match",
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


def test_projection_read_failure_remains_in_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture_project(tmp_path)
    release.apply_governance_skill_release(tmp_path)
    failed_target = (
        tmp_path / ".claude/skills/engineering-example/SKILL.md"
    )
    original_read_bytes = Path.read_bytes

    def fail_declared_projection(path: Path) -> bytes:
        if path == failed_target:
            raise PermissionError("synthetic read denial")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_declared_projection)

    report = release.check_governance_skill_release(tmp_path)

    assert any(
        issue.code == "governance_skill_projection_read_failed"
        and issue.path == ".claude/skills/engineering-example/SKILL.md"
        for issue in report.issues
    )


def test_projection_discovery_failure_remains_in_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture_project(tmp_path)
    release.apply_governance_skill_release(tmp_path)
    failed_root = tmp_path / ".claude/skills/engineering-example"
    original_rglob = Path.rglob

    def fail_managed_root(path: Path, pattern: str) -> object:
        if path == failed_root:
            raise PermissionError("synthetic discovery denial")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", fail_managed_root)

    report = release.check_governance_skill_release(tmp_path)

    assert any(
        issue.code == "governance_skill_projection_discovery_failed"
        and issue.path == ".claude/skills/engineering-example"
        for issue in report.issues
    )


def test_check_reports_forbidden_projection_root(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    release.apply_governance_skill_release(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retired_root = ".claude/skills/legacy-governance"
    manifest["forbidden_projection_roots"] = [retired_root]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    retired_path = tmp_path / retired_root
    retired_path.mkdir(parents=True)
    (retired_path / "SKILL.md").write_text("stale\n", encoding="utf-8")

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_forbidden_projection_present"
    ]


def test_apply_preserves_forbidden_and_undeclared_host_members(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retired_root = ".claude/skills/legacy-governance"
    manifest["forbidden_projection_roots"] = [retired_root]
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
        "governance_skill_forbidden_projection_present",
        "governance_skill_undeclared_package_member",
    }


def test_check_reports_active_reference_to_forbidden_governance_identity(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["forbidden_projection_roots"] = [
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
        "governance_skill_forbidden_projection_reference"
    ]


def test_forbidden_identity_scan_handles_non_ascii_manifest_value(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["forbidden_projection_roots"] = [
        ".claude/skills/légacy"
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    release.apply_governance_skill_release(tmp_path)
    live_skill = tmp_path / ".claude/skills/live-skill/SKILL.md"
    live_skill.parent.mkdir(parents=True)
    live_skill.write_text("Route legacy work to `légacy`.\n", encoding="utf-8")

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_forbidden_projection_reference"
    ]


def test_forbidden_identity_scan_allows_distinct_successor_identity(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["forbidden_projection_roots"] = [
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


def test_forbidden_identity_scan_rejects_reference_inside_non_utf8_payload(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["forbidden_projection_roots"] = [
        ".claude/skills/legacy-governance"
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    release.apply_governance_skill_release(tmp_path)
    live_asset = tmp_path / ".claude/skills/live-skill/non_utf8.bin"
    live_asset.parent.mkdir(parents=True)
    live_asset.write_bytes(b"\xff legacy-governance \xfe")

    report = release.check_governance_skill_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_skill_forbidden_projection_reference"
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


def test_apply_retry_converges_after_one_projection_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _skill_payload()
    manifest_path = _write_fixture_project(tmp_path, source_payload=payload)
    source_path = (
        tmp_path / "09_soul/governance/skills/engineering-example/SKILL.md"
    )
    manifest_before = manifest_path.read_bytes()
    source_before = source_path.read_bytes()
    original_write = release._write_bytes_atomically
    writes = 0

    def fail_second_write(target_path: Path, target_payload: bytes) -> None:
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError("synthetic second projection failure")
        original_write(target_path, target_payload)

    monkeypatch.setattr(
        release,
        "_write_bytes_atomically",
        fail_second_write,
    )
    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.apply_governance_skill_release(tmp_path)
    assert (
        caught.value.code
        == release.GOVERNANCE_SKILL_PROJECTION_WRITE_FAILED
    )
    monkeypatch.setattr(
        release,
        "_write_bytes_atomically",
        original_write,
    )

    report = release.apply_governance_skill_release(tmp_path)

    assert report.is_clean
    assert source_path.read_bytes() == source_before
    assert manifest_path.read_bytes() == manifest_before
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
            "embedded_resource_ids": [],
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
        match="accountable owner differs from the hash-validated frontmatter",
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
                    "schema_version": "governance_release_policy_v3",
                    "forbidden_source_fragments": ["local_product_identity"],
                    "retired_t0_targets": [],
                    "project_specific_t0_targets": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="project-local identity",
    ):
        release.check_governance_skill_release(tmp_path)


def test_skill_release_uses_t0_policy_path_validation(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path)
    policy_path = tmp_path / "governance_bindings/governance_release_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "governance_release_policy_v3",
                "forbidden_source_fragments": [],
                "retired_t0_targets": [],
                "project_specific_t0_targets": ["../outside.md"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="cannot validate project Governance release policy",
    ):
        release.check_governance_skill_release(tmp_path)


@pytest.mark.parametrize("payload", (b"\xff\xfe", b"[]"))
def test_malformed_project_policy_uses_source_closure_error(
    tmp_path: Path, payload: bytes
) -> None:
    _write_fixture_project(tmp_path)
    policy_path = tmp_path / "governance_bindings/governance_release_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_bytes(payload)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.check_governance_skill_release(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID


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
        match="cannot read Governance Skill Soul resource",
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


def test_manifest_rejects_projection_target_equal_to_host_root(
    tmp_path: Path,
) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = _load_fixture_manifest(manifest_path)
    row = _fixture_skill_row(manifest)
    row["package_files"][0]["projections"][1]["target"] = ".agents/skills"
    _store_fixture_manifest(manifest_path, manifest)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.load_governance_skill_manifest(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_MANIFEST_INVALID
    assert "must identify a member" in str(caught.value)


def test_check_reports_symlinked_projection_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".agents").symlink_to(outside, target_is_directory=True)
    _write_fixture_project(tmp_path)

    report = release.check_governance_skill_release(tmp_path)

    assert any(
        issue.code.startswith("governance_skill_projection_")
        and "symlink" in issue.detail
        for issue in report.issues
    )


def test_apply_maps_symlinked_projection_ancestor_to_write_failure(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".agents").symlink_to(outside, target_is_directory=True)
    _write_fixture_project(tmp_path)

    with pytest.raises(release.GovernanceSkillReleaseError) as caught:
        release.apply_governance_skill_release(tmp_path)

    assert caught.value.code == release.GOVERNANCE_SKILL_PROJECTION_WRITE_FAILED
    assert "symlink" in str(caught.value)
