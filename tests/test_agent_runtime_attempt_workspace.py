from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from agent_runtime.invocation.invocation_workspace_preparation import (
    AttemptWorkspaceConflictError,
    lease_attempt_workspace,
    prepare_attempt_workspace,
)


_LEASE_DIRECTORY_NAME = ".agent_runtime_attempt_leases"


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


def test_same_process_duplicate_dispatch_is_fenced_for_full_lease_scope(
    tmp_path: Path,
) -> None:
    workspace = prepare_attempt_workspace(
        workspace_root=tmp_path,
        attempt_identity=_identity(),
    )

    with lease_attempt_workspace(workspace):
        with pytest.raises(AttemptWorkspaceConflictError, match="live duplicate"):
            with lease_attempt_workspace(workspace):
                pytest.fail("duplicate invocation acquired the Attempt workspace")

    with lease_attempt_workspace(workspace) as reacquired:
        assert reacquired == workspace


def test_workspace_lock_is_outside_provider_writable_directory(
    tmp_path: Path,
) -> None:
    workspace = prepare_attempt_workspace(
        workspace_root=tmp_path,
        attempt_identity=_identity(),
    )

    with lease_attempt_workspace(workspace):
        lease = (
            tmp_path
            / _LEASE_DIRECTORY_NAME
            / f"{workspace.name}.lock"
        )
        assert lease.is_file()
        assert workspace not in lease.parents

    assert not list(workspace.glob("*.lock"))


def test_workspace_lock_is_released_when_holder_process_crashes(
    tmp_path: Path,
) -> None:
    workspace = prepare_attempt_workspace(
        workspace_root=tmp_path,
        attempt_identity=_identity(),
    )
    with lease_attempt_workspace(workspace):
        lease = (
            tmp_path
            / _LEASE_DIRECTORY_NAME
            / f"{workspace.name}.lock"
        )

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,sys; "
                "handle=open(sys.argv[1], 'a+'); "
                "fcntl.flock(handle.fileno(), fcntl.LOCK_EX); "
                "print('locked', flush=True); sys.stdin.read()"
            ),
            str(lease),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "locked"
        with pytest.raises(AttemptWorkspaceConflictError, match="live duplicate"):
            with lease_attempt_workspace(workspace):
                pytest.fail("duplicate invocation acquired the Attempt workspace")
        holder.kill()
        holder.wait()
        with lease_attempt_workspace(workspace) as recovered:
            assert recovered == workspace
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait()


def test_attempt_workspace_rejects_path_like_attempt_identity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="attempt_id"):
        prepare_attempt_workspace(
            workspace_root=tmp_path,
            attempt_identity={"attempt_id": "../outside"},
        )

    assert not (tmp_path.parent / "outside").exists()
