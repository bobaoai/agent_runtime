from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from tools.public_repository_validation import (
    load_public_repository_manifest,
    main,
    reachable_repository_objects,
    tracked_repository_paths,
    validate_public_repository,
    validate_public_repository_history,
    validate_public_repository_tree,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.casefold().encode("utf-8")).hexdigest()


def _synthetic_private_manifest() -> tuple[dict[str, object], str, str]:
    path_segment = "private_boundary_segment"
    content_fragment = "private_boundary_reference/"
    manifest = deepcopy(load_public_repository_manifest())
    manifest["forbidden_path_segment_sha256"] = [_fingerprint(path_segment)]
    manifest["forbidden_content_fingerprints"] = [
        {
            "length": len(content_fragment),
            "sha256": _fingerprint(content_fragment),
        }
    ]
    return manifest, path_segment, content_fragment


def test_current_repository_matches_public_boundary() -> None:
    assert validate_public_repository(root=REPOSITORY_ROOT) == ()


def test_synthetic_forbidden_segment_fails_public_boundary(tmp_path: Path) -> None:
    manifest, path_segment, _content_fragment = _synthetic_private_manifest()
    private_path = f"src/agent_runtime/{path_segment}/private_runtime.py"
    findings = validate_public_repository_tree(
        root=tmp_path,
        tracked_paths=(private_path,),
        manifest=manifest,
    )

    assert "forbidden private segment" in "\n".join(findings)


@pytest.mark.parametrize(
    "nested_path",
    (
        "designDoc/agent_runtime_00_extra/private/host_binding.md",
        "designDoc/agent_runtime_00_x.md/private/leak.md",
        "tests/test_agent_runtime_x.py/private/leak.py",
    ),
)
def test_nested_private_subtree_cannot_match_public_pattern(
    tmp_path: Path,
    nested_path: str,
) -> None:
    manifest = load_public_repository_manifest()

    findings = validate_public_repository_tree(
        root=tmp_path,
        tracked_paths=(nested_path,),
        manifest=manifest,
    )

    assert findings
    assert any(
        marker in "\n".join(findings)
        for marker in (
            "forbidden private segment",
            "outside public boundary",
        )
    )


@pytest.mark.parametrize(
    "leading_path",
    (
        "vendor/tests/test_agent_runtime_private.py",
        "evil/designDoc/agent_runtime_00_leak.md",
        "a/b/c/tests/test_runtime_leak.py",
    ),
)
def test_public_pattern_is_anchored_at_repository_root(
    tmp_path: Path,
    leading_path: str,
) -> None:
    manifest = load_public_repository_manifest()

    findings = validate_public_repository_tree(
        root=tmp_path,
        tracked_paths=(leading_path,),
        manifest=manifest,
    )

    assert any("outside public boundary" in item for item in findings)


def test_synthetic_private_reference_fails_public_boundary(tmp_path: Path) -> None:
    manifest, _path_segment, content_fragment = _synthetic_private_manifest()
    readme = tmp_path / "README.md"
    readme.write_text(
        f"Load {content_fragment}result.json before release.\n",
        encoding="utf-8",
    )
    findings = validate_public_repository_tree(
        root=tmp_path,
        tracked_paths=("README.md",),
        manifest=manifest,
    )

    assert any("forbidden private reference" in item for item in findings)


@pytest.mark.parametrize("case_transform", (str.upper, str.title))
def test_forbidden_path_segment_check_is_case_insensitive(
    tmp_path: Path,
    case_transform,
) -> None:
    manifest, path_segment, _content_fragment = _synthetic_private_manifest()
    mixed_case_path = (
        "src/agent_runtime/"
        f"{case_transform(path_segment)}/private_runtime.py"
    )
    findings = validate_public_repository_tree(
        root=tmp_path,
        tracked_paths=(mixed_case_path,),
        manifest=manifest,
    )

    assert any("forbidden private segment" in item for item in findings)


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )


def _git_output(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_reachable_history_cannot_retain_deleted_private_material(
    tmp_path: Path,
) -> None:
    manifest, path_segment, content_fragment = _synthetic_private_manifest()
    _run_git(tmp_path, "init", "--initial-branch=main")
    _run_git(tmp_path, "config", "user.name", "Runtime Boundary Test")
    _run_git(tmp_path, "config", "user.email", "runtime-boundary@example.invalid")
    private_directory = tmp_path / path_segment
    private_directory.mkdir()
    private_path = private_directory / "private_result.md"
    private_path.write_text("internal review result\n", encoding="utf-8")
    readme = tmp_path / "README.md"
    readme.write_text(f"Load {content_fragment}state.\n", encoding="utf-8")
    _run_git(tmp_path, "add", ".")
    _run_git(tmp_path, "commit", "-m", f"record {content_fragment} material")
    _run_git(
        tmp_path,
        "tag",
        "-a",
        "private-history",
        "-m",
        f"tag {content_fragment} material",
    )
    private_path.unlink()
    private_directory.rmdir()
    readme.write_text("Public Runtime.\n", encoding="utf-8")
    _run_git(tmp_path, "add", "-A")
    _run_git(tmp_path, "commit", "-m", "remove private material")

    objects = reachable_repository_objects(tmp_path)
    findings = validate_public_repository_history(
        root=tmp_path,
        manifest=manifest,
        reachable_objects=objects,
    )

    assert any(
        "reachable history path contains forbidden private segment: "
        f"{path_segment}/private_result.md"
        in item
        for item in findings
    )
    assert any(
        "reachable history object contains forbidden private reference: blob:"
        in item and ":README.md:" in item
        for item in findings
    )
    assert any(
        "reachable history object contains forbidden private reference: commit:"
        in item and ":<metadata>:" in item
        for item in findings
    )
    assert any(
        "reachable history object contains forbidden private reference: tag:"
        in item and ":<metadata>:" in item
        for item in findings
    )


def test_forbidden_content_fingerprint_is_case_insensitive(tmp_path: Path) -> None:
    manifest, _path_segment, content_fragment = _synthetic_private_manifest()
    readme = tmp_path / "README.md"
    readme.write_text(
        f"Load {content_fragment.swapcase()} state.\n",
        encoding="utf-8",
    )

    findings = validate_public_repository_tree(
        root=tmp_path,
        tracked_paths=("README.md",),
        manifest=manifest,
    )

    assert any("forbidden private reference" in item for item in findings)


def test_detached_head_is_included_in_reachable_history(tmp_path: Path) -> None:
    manifest, _path_segment, content_fragment = _synthetic_private_manifest()
    _run_git(tmp_path, "init", "--initial-branch=main")
    _run_git(tmp_path, "config", "user.name", "Runtime Boundary Test")
    _run_git(tmp_path, "config", "user.email", "runtime-boundary@example.invalid")
    readme = tmp_path / "README.md"
    readme.write_text("Public Runtime.\n", encoding="utf-8")
    _run_git(tmp_path, "add", "README.md")
    _run_git(tmp_path, "commit", "-m", "public base")
    public_commit = _git_output(tmp_path, "rev-parse", "HEAD")
    readme.write_text(f"Load {content_fragment}state.\n", encoding="utf-8")
    _run_git(tmp_path, "add", "README.md")
    _run_git(tmp_path, "commit", "-m", "detached private content")
    private_commit = _git_output(tmp_path, "rev-parse", "HEAD")
    _run_git(tmp_path, "checkout", "--detach", private_commit)
    _run_git(tmp_path, "branch", "-f", "main", public_commit)

    findings = validate_public_repository_history(
        root=tmp_path,
        manifest=manifest,
    )

    assert any(
        "reachable history object contains forbidden private reference: blob:"
        in item and ":README.md:" in item
        for item in findings
    )


def test_gitlink_path_is_checked_in_reachable_history(tmp_path: Path) -> None:
    manifest, path_segment, _content_fragment = _synthetic_private_manifest()
    _run_git(tmp_path, "init", "--initial-branch=main")
    _run_git(tmp_path, "config", "user.name", "Runtime Boundary Test")
    _run_git(tmp_path, "config", "user.email", "runtime-boundary@example.invalid")
    readme = tmp_path / "README.md"
    readme.write_text("Public Runtime.\n", encoding="utf-8")
    _run_git(tmp_path, "add", "README.md")
    _run_git(tmp_path, "commit", "-m", "public base")
    target_commit = _git_output(tmp_path, "rev-parse", "HEAD")
    gitlink_path = f"src/agent_runtime/{path_segment}"
    _run_git(
        tmp_path,
        "update-index",
        "--add",
        "--cacheinfo",
        "160000",
        target_commit,
        gitlink_path,
    )
    _run_git(tmp_path, "commit", "-m", "add synthetic gitlink")

    findings = validate_public_repository_history(
        root=tmp_path,
        manifest=manifest,
    )

    assert any(
        f"reachable history path contains forbidden private segment: {gitlink_path}"
        in item
        for item in findings
    )


def test_extensionless_text_is_content_scanned(tmp_path: Path) -> None:
    manifest, _path_segment, content_fragment = _synthetic_private_manifest()
    license_path = tmp_path / "LICENSE"
    license_path.write_text(
        f"Synthetic license with {content_fragment} content.\n",
        encoding="utf-8",
    )

    findings = validate_public_repository_tree(
        root=tmp_path,
        tracked_paths=("LICENSE",),
        manifest=manifest,
    )

    assert any("forbidden private reference" in item for item in findings)


def test_manifest_rejects_malformed_path_segment_fingerprint(
    tmp_path: Path,
) -> None:
    manifest = deepcopy(load_public_repository_manifest())
    manifest["forbidden_path_segment_sha256"] = ["short"]
    manifest_path = tmp_path / "public_repository_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(
        ValueError,
        match="invalid forbidden path-segment fingerprint",
    ):
        load_public_repository_manifest(manifest_path)


def test_manifest_rejects_malformed_content_fingerprint(tmp_path: Path) -> None:
    manifest = deepcopy(load_public_repository_manifest())
    manifest["forbidden_content_fingerprints"] = [
        {"length": 4, "sha256": "short"}
    ]
    manifest_path = tmp_path / "public_repository_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid forbidden content fingerprint"):
        load_public_repository_manifest(manifest_path)


def test_release_cli_requires_explicit_non_empty_private_policy(
    capsys,
) -> None:
    with pytest.raises(SystemExit):
        main(["--root", str(REPOSITORY_ROOT)])

    result = main(
        [
            "--root",
            str(REPOSITORY_ROOT),
            "--manifest",
            str(REPOSITORY_ROOT / "public_repository_manifest.json"),
        ]
    )

    assert result == 1
    assert "requires a non-empty private policy" in capsys.readouterr().out


def test_required_public_path_cannot_disappear(tmp_path: Path) -> None:
    manifest = deepcopy(load_public_repository_manifest())
    manifest["required_paths"] = ["src/agent_runtime/__init__.py"]

    findings = validate_public_repository_tree(
        root=tmp_path,
        tracked_paths=(),
        manifest=manifest,
    )

    assert findings == (
        "missing required public path: src/agent_runtime/__init__.py",
    )


def test_public_repository_rejects_tracked_symlink(tmp_path: Path) -> None:
    manifest = deepcopy(load_public_repository_manifest())
    manifest["required_paths"] = []
    target = tmp_path / "README.md"
    target.symlink_to("outside.md")

    findings = validate_public_repository_tree(
        root=tmp_path,
        tracked_paths=("README.md",),
        manifest=manifest,
    )

    assert findings == (
        "public repository cannot track a symlink: README.md",
    )


def test_git_inventory_is_sorted_and_contains_manifest() -> None:
    paths = tracked_repository_paths(REPOSITORY_ROOT)

    assert paths == tuple(sorted(paths))
    assert "public_repository_manifest.json" in paths
