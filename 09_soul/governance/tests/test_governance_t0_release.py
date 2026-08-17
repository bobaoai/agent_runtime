from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "09_soul/governance/governance_t0_release.py"
SPEC = importlib.util.spec_from_file_location("governance_t0_release", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def _hash(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_fixture_project(
    root: Path,
    *,
    source_payload: bytes = b"# Portable T0\n",
    target_payload: bytes | None = None,
    charter: bool = True,
    source: str = "09_soul/governance/t0/the_example.md",
    target: str = "designDoc/the_example.md",
    declared_hash: str | None = None,
) -> Path:
    source_path = root / source
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(source_payload)
    if charter:
        charter_path = root / "designDoc/the_charter.md"
        charter_path.parent.mkdir(parents=True, exist_ok=True)
        charter_path.write_text("# Project Charter\n", encoding="utf-8")
    if target_payload is not None:
        target_path = root / target
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(target_payload)
    manifest = {
        "manifest_version": "governance_t0_manifest_v1",
        "charter": {
            "mode": "project_specific",
            "target": "designDoc/the_charter.md",
        },
        "portable_t0_contracts": [
            {
                "t0_layer_id": "the_example",
                "source": source,
                "target": target,
                "sha256": declared_hash or _hash(source_payload),
            }
        ],
    }
    manifest_path = root / "09_soul/governance/governance_t0_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_production_portable_t0_release_is_clean() -> None:
    report = release.check_governance_t0_release(REPO_ROOT)

    assert report.is_clean
    assert report.contract_count == 10
    assert report.charter_target == "designDoc/the_charter.md"


def test_check_reports_missing_charter_and_target(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path, charter=False)

    report = release.check_governance_t0_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "project_charter_missing",
        "portable_t0_target_missing",
    ]


def test_check_reports_projection_drift(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path, target_payload=b"# Local fork\n")

    report = release.check_governance_t0_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "portable_t0_target_drift"
    ]


def test_apply_requires_project_specific_charter_before_writing(
    tmp_path: Path,
) -> None:
    _write_fixture_project(tmp_path, charter=False)

    with pytest.raises(release.GovernanceT0ReleaseError, match="Charter is required"):
        release.apply_governance_t0_release(tmp_path)

    assert not (tmp_path / "designDoc/the_example.md").exists()


def test_apply_replaces_projection_with_exact_source(tmp_path: Path) -> None:
    payload = b"# Portable T0\n\nStable intent.\n"
    _write_fixture_project(
        tmp_path,
        source_payload=payload,
        target_payload=b"stale\n",
    )

    report = release.apply_governance_t0_release(tmp_path)

    assert report.is_clean
    assert (tmp_path / "designDoc/the_example.md").read_bytes() == payload


@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("../outside.md", "designDoc/the_example.md"),
        ("09_soul/governance/t0/the_example.md", "/tmp/the_example.md"),
        ("09_soul/governance/t0/the_example.md", "other/the_example.md"),
    ],
)
def test_manifest_rejects_escaping_or_wrong_surface_paths(
    tmp_path: Path,
    source: str,
    target: str,
) -> None:
    manifest_path = _write_fixture_project(
        tmp_path,
        source=source,
        target=target,
    )

    with pytest.raises(release.GovernanceT0ReleaseError):
        release.load_governance_t0_manifest(tmp_path, manifest_path)


def test_manifest_rejects_source_hash_drift(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path, declared_hash="0" * 64)

    with pytest.raises(release.GovernanceT0ReleaseError, match="hash mismatch"):
        release.check_governance_t0_release(tmp_path)


def test_portable_source_rejects_project_local_implementation_path(
    tmp_path: Path,
) -> None:
    payload = b"# Portable T0\n\ntruth: src/project/local_registry.py\n"
    _write_fixture_project(tmp_path, source_payload=payload)

    with pytest.raises(
        release.GovernanceT0ReleaseError,
        match="project-local implementation path or identity",
    ):
        release.check_governance_t0_release(tmp_path)


def test_manifest_rejects_symlinked_target_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    design_doc = tmp_path / "designDoc"
    design_doc.symlink_to(outside, target_is_directory=True)
    _write_fixture_project(tmp_path, charter=False)

    with pytest.raises(release.GovernanceT0ReleaseError, match="symlink"):
        release.check_governance_t0_release(tmp_path)
