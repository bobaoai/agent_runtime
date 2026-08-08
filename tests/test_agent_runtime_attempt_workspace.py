from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.invocation.invocation_workspace_preparation import (
    AttemptWorkspaceConflictError,
    prepare_attempt_workspace,
)


def _identity(**overrides: str) -> dict[str, str]:
    identity = {
        "attempt_id": "attempt_demo_001",
        "module_run_id": "module_run_demo_001",
        "variant_id": "variant_demo_001",
        "module_release_sha256": "a" * 64,
        "execution_profile_sha256": "b" * 64,
        "prompt_envelope_sha256": "c" * 64,
    }
    identity.update(overrides)
    return identity


def test_exact_attempt_retry_recovers_its_existing_workspace(tmp_path: Path) -> None:
    first = prepare_attempt_workspace(
        workspace_root=tmp_path,
        attempt_identity=_identity(),
    )
    draft = first / "draft.md"
    draft.write_text("partial work", encoding="utf-8")

    replay = prepare_attempt_workspace(
        workspace_root=tmp_path,
        attempt_identity=_identity(),
    )

    assert replay == first
    assert draft.read_text(encoding="utf-8") == "partial work"


def test_attempt_workspace_rejects_identity_collision(tmp_path: Path) -> None:
    prepare_attempt_workspace(
        workspace_root=tmp_path,
        attempt_identity=_identity(),
    )

    with pytest.raises(AttemptWorkspaceConflictError, match="different"):
        prepare_attempt_workspace(
            workspace_root=tmp_path,
            attempt_identity=_identity(variant_id="variant_other_001"),
        )


def test_empty_directory_from_interrupted_initialization_is_recoverable(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "attempt_demo_001"
    workspace.mkdir()

    recovered = prepare_attempt_workspace(
        workspace_root=tmp_path,
        attempt_identity=_identity(),
    )

    assert recovered == workspace


def test_attempt_workspace_rejects_path_like_attempt_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="attempt_id"):
        prepare_attempt_workspace(
            workspace_root=tmp_path,
            attempt_identity={"attempt_id": "../outside"},
        )

    assert not (tmp_path.parent / "outside").exists()
