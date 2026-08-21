from __future__ import annotations

import ast
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


def test_workspace_module_imports_without_fcntl_but_leasing_requires_posix() -> None:
    source = """
import builtins
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))

original_import = builtins.__import__
def without_fcntl(name, *args, **kwargs):
    if name == "fcntl":
        raise ModuleNotFoundError("simulated Windows host")
    return original_import(name, *args, **kwargs)
builtins.__import__ = without_fcntl

from agent_runtime.invocation.invocation_workspace_preparation import (
    AttemptWorkspaceConflictError,
    lease_attempt_workspace,
    prepare_attempt_workspace,
)
with tempfile.TemporaryDirectory() as directory:
    workspace = prepare_attempt_workspace(
        workspace_root=Path(directory),
        attempt_identity={
            "attempt_id": "attempt_windows_001",
            "module_run_id": "module_run_windows_001",
            "variant_id": "variant_windows_001",
        },
    )
    try:
        with lease_attempt_workspace(workspace):
            raise AssertionError("lease admitted without a POSIX lock")
    except AttemptWorkspaceConflictError as exc:
        assert "POSIX" in str(exc), str(exc)
"""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_provider_adapters_hold_lease_around_provider_entry_and_classify_conflicts() -> None:
    invocation_root = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent_runtime"
        / "invocation"
    )
    expected_provider_entry = {
        "invocation_codex_module_invocation.py": "_invoker",
        "invocation_claude_module_invocation.py": "consume",
    }
    for file_name, provider_name in expected_provider_entry.items():
        tree = ast.parse((invocation_root / file_name).read_text(encoding="utf-8"))
        leased_blocks = [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.With, ast.AsyncWith))
            and any(
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "lease_attempt_workspace"
                for call in ast.walk(node)
            )
        ]
        assert len(leased_blocks) == 1, file_name
        assert any(
            isinstance(name, ast.Name)
            and name.id == provider_name
            or isinstance(name, ast.Attribute)
            and name.attr == provider_name
            for name in ast.walk(leased_blocks[0])
        ), file_name
        conflict_handlers = [
            handler
            for handler in ast.walk(tree)
            if isinstance(handler, ast.ExceptHandler)
            and isinstance(handler.type, ast.Name)
            and handler.type.id == "AttemptWorkspaceConflictError"
        ]
        assert len(conflict_handlers) >= 1, file_name
        assert any(
            isinstance(value, ast.Constant)
            and value.value == "dependency_unavailable"
            for handler in conflict_handlers
            for value in ast.walk(handler)
        ), file_name
