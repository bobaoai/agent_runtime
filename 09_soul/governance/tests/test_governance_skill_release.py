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
    t0_manifest = {
        "portable_t0_contracts": [{"t0_layer_id": "the_example"}]
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
        "manifest_version": "governance_skill_manifest_v1",
        "portable_governance_skills": [
            {
                "skill_id": "engineering-example",
                "source": source,
                "sha256": declared_hash or _hash(payload),
                "required_t0_layer_ids": [required_t0],
                "primary_agent_entry_role": "authoring",
                "primary_agent_entry_subject": "engineering_change_candidate",
                "projections": projections,
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


def test_production_governance_skill_release_is_clean() -> None:
    report = release.check_governance_skill_release(REPO_ROOT)

    assert report.is_clean
    assert report.skill_count == 5
    assert report.projection_count == 10


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


def test_manifest_rejects_project_local_source_identity(tmp_path: Path) -> None:
    payload = _skill_payload() + b"\nUse /Users/example/project.\n"
    _write_fixture_project(tmp_path, source_payload=payload)

    with pytest.raises(
        release.GovernanceSkillReleaseError,
        match="project-local identity",
    ):
        release.check_governance_skill_release(tmp_path)


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
