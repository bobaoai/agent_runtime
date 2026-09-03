from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]
MODULE_PATH = REPO_ROOT / "09_soul/governance/t0/validation/t0_release.py"
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
        "manifest_version": "governance_t0_manifest_v3",
        "charter": {
            "mode": "project_specific",
            "target": "designDoc/the_charter.md",
        },
        "retired_t0_targets": [],
        "portable_t0_contracts": [
            {
                "t0_layer_id": "the_example",
                "source": source,
                "target": target,
                "sha256": declared_hash or _hash(source_payload),
            }
        ],
        "artifact_contracts": [],
    }
    manifest_path = root / "09_soul/governance/governance_t0_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def test_production_portable_t0_release_is_clean() -> None:
    report = release.check_governance_t0_release(REPO_ROOT)

    assert report.is_clean
    assert report.contract_count == 11
    assert report.charter_target == "designDoc/the_charter.md"


def test_production_artifact_contract_bindings_are_owner_local_and_hash_closed() -> None:
    manifest = release.load_governance_t0_manifest(REPO_ROOT)

    assert {
        binding.artifact_contract_id: binding.owner_t0_layer_id
        for binding in manifest.artifact_contracts
    } == {
        "design_document_artifact_contract": "the_design_doc_management",
        "skill_definition_artifact_contract": "the_skill_management",
    }
    for binding in manifest.artifact_contracts:
        assert _hash((REPO_ROOT / binding.source).read_bytes()) == binding.source_sha256
        assert _hash((REPO_ROOT / binding.adapter).read_bytes()) == binding.adapter_sha256


def test_artifact_contract_binding_rejects_unknown_owner(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(tmp_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_contracts"] = [
        {
            "artifact_contract_id": "example_artifact_contract",
            "owner_t0_layer_id": "the_missing",
            "source": "09_soul/governance/t0/validation/artifact_contracts/example.json",
            "source_sha256": "0" * 64,
            "adapter": "09_soul/governance/t0/validation/artifact_contracts/example.py",
            "adapter_sha256": "0" * 64,
        }
    ]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        release.GovernanceT0ReleaseError,
        match="owner_t0_layer_id is unknown",
    ):
        release.load_governance_t0_manifest(tmp_path, manifest_path)


def test_all_portable_t0s_use_one_exact_intent_capsule_shape() -> None:
    expected_keys = {
        "layer",
        "t0_layer_id",
        "status",
        "canonical_owner",
        "owned_system_object",
        "scope",
        "non_goals",
        "inputs",
        "outputs",
        "truth_surfaces",
        "runtime_triggers",
        "downstream_consumers",
        "open_decisions",
        "review_gate",
        "runtime_surface_ledger",
        "verification_hooks",
    }
    paths = [
        REPO_ROOT / "designDoc/the_charter.md",
        *sorted((REPO_ROOT / "09_soul/governance/t0").glob("the_*.md")),
    ]
    for path in paths:
        body = path.read_text(encoding="utf-8")
        assert sum(
            line == "## 0. Intent Capsule" for line in body.splitlines()
        ) == 1
        capsule = body.split("## 0. Intent Capsule", maxsplit=1)[1]
        yaml_body = capsule.split("```yaml", maxsplit=1)[1].split(
            "```", maxsplit=1
        )[0]
        keys = {
            line.split(":", maxsplit=1)[0]
            for line in yaml_body.splitlines()
            if line and not line[0].isspace() and ":" in line
        }
        if path.name == "the_charter.md":
            assert keys in (
                expected_keys,
                expected_keys - {"t0_layer_id"},
            )
        else:
            assert keys == expected_keys


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


def test_check_reports_retired_t0_target(tmp_path: Path) -> None:
    manifest_path = _write_fixture_project(
        tmp_path,
        target_payload=b"# Portable T0\n",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    retired_target = "designDoc/the_retired.md"
    manifest["retired_t0_targets"] = [retired_target]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / retired_target).write_text("# Retired\n", encoding="utf-8")

    report = release.check_governance_t0_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "portable_t0_retired_target_present"
    ]


def test_check_reports_undeclared_t0_like_target(tmp_path: Path) -> None:
    _write_fixture_project(
        tmp_path,
        target_payload=b"# Portable T0\n",
    )
    undeclared = tmp_path / "designDoc/the_shadow.md"
    undeclared.write_text("# Shadow\n", encoding="utf-8")

    report = release.check_governance_t0_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "portable_t0_undeclared_target"
    ]


def test_check_reports_undeclared_t0_source_member(tmp_path: Path) -> None:
    _write_fixture_project(
        tmp_path,
        target_payload=b"# Portable T0\n",
    )
    extra = tmp_path / "09_soul/governance/t0/the_shadow.md"
    extra.write_text("# Shadow\n", encoding="utf-8")

    report = release.check_governance_t0_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "portable_t0_undeclared_source_member"
    ]


def test_check_reports_undeclared_governance_package_root_member(
    tmp_path: Path,
) -> None:
    _write_fixture_project(tmp_path, target_payload=b"# Portable T0\n")
    governance_root = tmp_path / "09_soul/governance"
    (governance_root / "README.md").write_text("# Governance\n", encoding="utf-8")
    (governance_root / "governance_skill_manifest.json").write_text("{}", encoding="utf-8")
    (governance_root / "skills").mkdir()
    (governance_root / "unexpected.txt").write_text("extra\n", encoding="utf-8")

    report = release.check_governance_t0_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "governance_package_undeclared_root_member"
    ]


def test_release_rejects_unknown_cited_t0_contract(tmp_path: Path) -> None:
    payload = b"# Portable T0\n\nSee [Missing](the_missing.md).\n"
    _write_fixture_project(tmp_path, source_payload=payload)

    with pytest.raises(
        release.GovernanceT0ReleaseError,
        match="unknown or retired T0 contracts",
    ):
        release.check_governance_t0_release(tmp_path)


def test_release_rejects_unknown_charter_t0_reference(tmp_path: Path) -> None:
    _write_fixture_project(tmp_path)
    charter = tmp_path / "designDoc/the_charter.md"
    charter.write_text(
        "# Project Charter\n\nSee [Missing](the_missing.md).\n",
        encoding="utf-8",
    )

    report = release.check_governance_t0_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "project_charter_unknown_t0_reference",
        "portable_t0_target_missing",
    ]


def test_apply_requires_project_specific_charter_before_writing(
    tmp_path: Path,
) -> None:
    _write_fixture_project(tmp_path, charter=False)

    with pytest.raises(release.GovernanceT0ReleaseError, match="Charter is required"):
        release.apply_governance_t0_release(tmp_path)

    assert not (tmp_path / "designDoc/the_example.md").exists()


def test_apply_refuses_to_replace_drifted_projection_without_override(
    tmp_path: Path,
) -> None:
    payload = b"# Portable T0\n\nStable intent.\n"
    _write_fixture_project(
        tmp_path,
        source_payload=payload,
        target_payload=b"stale\n",
    )

    with pytest.raises(
        release.GovernanceT0ReleaseError,
        match="projection drift must be reviewed before apply",
    ):
        release.apply_governance_t0_release(tmp_path)

    assert (tmp_path / "designDoc/the_example.md").read_bytes() == b"stale\n"


def test_apply_replaces_reviewed_drift_with_explicit_override(
    tmp_path: Path,
) -> None:
    payload = b"# Portable T0\n\nStable intent.\n"
    _write_fixture_project(
        tmp_path,
        source_payload=payload,
        target_payload=b"stale\n",
    )

    report = release.apply_governance_t0_release(
        tmp_path, allow_projection_drift=True
    )

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


def test_portable_source_rejects_virtual_environment_path(
    tmp_path: Path,
) -> None:
    payload = b"# Portable T0\n\ncommand: .venv/bin/python\n"
    _write_fixture_project(tmp_path, source_payload=payload)

    with pytest.raises(
        release.GovernanceT0ReleaseError,
        match="project-local implementation path or identity",
    ):
        release.check_governance_t0_release(tmp_path)


def test_portable_source_rejects_upstream_distribution_path(
    tmp_path: Path,
) -> None:
    payload = b"# Portable T0\n\nRead 09_soul/governance/t0/source.md.\n"
    _write_fixture_project(tmp_path, source_payload=payload)

    with pytest.raises(
        release.GovernanceT0ReleaseError,
        match="project-local implementation path or identity",
    ):
        release.check_governance_t0_release(tmp_path)


def test_project_policy_rejects_declared_t0_local_identity(
    tmp_path: Path,
) -> None:
    payload = b"# Portable T0\n\nlocal-product-identity\n"
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
        release.GovernanceT0ReleaseError,
        match="project-local implementation path or identity",
    ):
        release.check_governance_t0_release(tmp_path)


def test_project_policy_reports_retired_t0_target(tmp_path: Path) -> None:
    _write_fixture_project(
        tmp_path,
        target_payload=b"# Portable T0\n",
    )
    retired_target = "designDoc/the_project_retired.md"
    policy_path = tmp_path / "governance_bindings/governance_release_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "governance_release_policy_v3",
                "forbidden_source_fragments": [],
                "retired_t0_targets": [retired_target],
                "project_specific_t0_targets": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / retired_target).write_text("# Retired\n", encoding="utf-8")

    report = release.check_governance_t0_release(tmp_path)

    assert [issue.code for issue in report.issues] == [
        "portable_t0_retired_target_present"
    ]


def test_project_policy_allows_registered_project_specific_t0_target(
    tmp_path: Path,
) -> None:
    _write_fixture_project(tmp_path, target_payload=b"# Portable T0\n")
    project_specific_target = "designDoc/the_project_workflow.md"
    policy_path = tmp_path / "governance_bindings/governance_release_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "governance_release_policy_v3",
                "forbidden_source_fragments": [],
                "retired_t0_targets": [],
                "project_specific_t0_targets": [project_specific_target],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / project_specific_target).write_text(
        "# Project workflow index\n", encoding="utf-8"
    )

    report = release.check_governance_t0_release(tmp_path)

    assert report.is_clean


def test_project_policy_rejects_project_specific_target_overlap(
    tmp_path: Path,
) -> None:
    _write_fixture_project(tmp_path, target_payload=b"# Portable T0\n")
    policy_path = tmp_path / "governance_bindings/governance_release_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "governance_release_policy_v3",
                "forbidden_source_fragments": [],
                "retired_t0_targets": [],
                "project_specific_t0_targets": ["designDoc/the_example.md"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        release.GovernanceT0ReleaseError,
        match="project-specific T0 targets overlap",
    ):
        release.check_governance_t0_release(tmp_path)


def test_project_policy_is_read_once_per_t0_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_fixture_project(tmp_path, target_payload=b"# Portable T0\n")
    policy_path = tmp_path / "governance_bindings/governance_release_policy.json"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        json.dumps(
            {
                "schema_version": "governance_release_policy_v3",
                "forbidden_source_fragments": [],
                "retired_t0_targets": [],
                "project_specific_t0_targets": [],
            }
        ),
        encoding="utf-8",
    )
    original_read_text = Path.read_text
    observations = 0

    def counting_read_text(path: Path, *args, **kwargs):
        nonlocal observations
        if path == policy_path:
            observations += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counting_read_text)

    report = release.check_governance_t0_release(tmp_path)

    assert report.is_clean
    assert observations == 1


def test_manifest_rejects_symlinked_target_ancestor(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    design_doc = tmp_path / "designDoc"
    design_doc.symlink_to(outside, target_is_directory=True)
    _write_fixture_project(tmp_path, charter=False)

    with pytest.raises(release.GovernanceT0ReleaseError, match="symlink"):
        release.check_governance_t0_release(tmp_path)
