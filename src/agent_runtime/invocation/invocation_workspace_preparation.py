"""Prepare and recover Runtime-owned Attempt workspaces safely."""

from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping

try:  # pragma: no branch - selected once for the host platform
    import fcntl as _fcntl
except ImportError:  # Windows has no fcntl module
    _fcntl = None

if _fcntl is None:
    try:  # pragma: no branch - selected once for the host platform
        import msvcrt as _msvcrt
    except ImportError:  # Neither a POSIX nor a Windows host
        _msvcrt = None
else:
    _msvcrt = None

from ..foundation.foundation_contract_validation import validate_id


_MARKER_NAME = ".agent_runtime_attempt.json"
_LEASE_DIRECTORY_NAME = ".agent_runtime_attempt_leases"
# Windows byte-range locks cover explicit offsets, not the whole file. The lease
# locks one byte far past the PID record, so the record stays readable and
# rewritable while the lock is held. Exported for tests that hold the lease.
_WINDOWS_LEASE_LOCK_OFFSET = 1 << 30


def _acquire_lease_lock(handle: Any) -> None:
    """Take a non-blocking exclusive OS lock that the kernel drops on exit.

    POSIX uses ``flock``. Windows uses a ``LockFileEx`` byte-range lock through
    ``msvcrt.locking``; it conflicts with every other handle, including a second
    open of the same file in this process. Windows releases it after the owning
    process terminates, but asynchronously, so for a short time after a crash a
    new lease can still see contention. Contention raises ``OSError`` with
    ``EACCES`` or ``EAGAIN`` on both platforms.
    """

    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        return
    os.lseek(handle.fileno(), _WINDOWS_LEASE_LOCK_OFFSET, os.SEEK_SET)
    _msvcrt.locking(handle.fileno(), _msvcrt.LK_NBLCK, 1)


def _release_lease_lock(handle: Any) -> None:
    if _fcntl is not None:
        _fcntl.flock(handle.fileno(), _fcntl.LOCK_UN)
        return
    os.lseek(handle.fileno(), _WINDOWS_LEASE_LOCK_OFFSET, os.SEEK_SET)
    _msvcrt.locking(handle.fileno(), _msvcrt.LK_UNLCK, 1)


class AttemptWorkspaceConflictError(RuntimeError):
    """Raised when a deterministic Attempt path belongs to another identity."""


@contextmanager
def lease_attempt_workspace(workspace: Path) -> Iterator[Path]:
    """Hold an OS-backed exclusive lock for one provider invocation.

    The persistent lock file lives beside the provider-writable Attempt
    directory, not inside it. The kernel releases the lock if the worker exits,
    including after a crash; a same-process duplicate using a separate open
    file description is fenced just like a duplicate in another process.
    """

    workspace = workspace.resolve()
    lease_directory = workspace.parent / _LEASE_DIRECTORY_NAME
    try:
        lease_directory.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise AttemptWorkspaceConflictError(
            "Attempt workspace lease directory cannot be prepared"
        ) from exc
    if lease_directory.is_symlink() or not lease_directory.is_dir():
        raise AttemptWorkspaceConflictError(
            "Attempt workspace lease path is not a Runtime directory"
        )

    lease_path = lease_directory / f"{workspace.name}.lock"
    if _fcntl is None and _msvcrt is None:
        # Crash-safe duplicate-dispatch fencing relies on a kernel-released
        # lock (POSIX flock or a Windows byte-range lock). Without either there
        # is no correct substitute, so fail closed rather than fence open.
        raise AttemptWorkspaceConflictError(
            "Attempt workspace leasing requires a POSIX host (fcntl) or a "
            "Windows host (msvcrt); duplicate-dispatch fencing is unsupported "
            "on this platform"
        )

    try:
        handle = open(lease_path, "a+", encoding="utf-8")
    except OSError as exc:
        raise AttemptWorkspaceConflictError(
            "Attempt workspace lease cannot be opened"
        ) from exc
    try:
        try:
            _acquire_lease_lock(handle)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise AttemptWorkspaceConflictError(
                    "Attempt workspace is leased by a live duplicate invocation"
                ) from exc
            raise AttemptWorkspaceConflictError(
                "Attempt workspace lease cannot be acquired"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        try:
            yield workspace
        finally:
            _release_lease_lock(handle)
    finally:
        handle.close()


def prepare_attempt_workspace(
    *,
    workspace_root: Path,
    attempt_identity: Mapping[str, Any],
) -> Path:
    """Create or recover the exact workspace owned by one Runtime Attempt.

    A repeated invocation with the same identity reuses its own drafts. A
    pre-existing directory without the Runtime marker is accepted only when it
    is empty, which also recovers a crash between ``mkdir`` and marker commit.
    """

    attempt_id = attempt_identity.get("attempt_id")
    validate_id("attempt_id", attempt_id)
    normalized = dict(attempt_identity)
    if any(type(key) is not str for key in normalized):
        raise ValueError("attempt workspace identity keys must be strings")
    marker_payload = json.dumps(
        normalized,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )

    root = workspace_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    workspace = root / attempt_id
    try:
        workspace.mkdir()
    except FileExistsError:
        if workspace.is_symlink() or not workspace.is_dir():
            raise AttemptWorkspaceConflictError(
                "Attempt workspace path is not a Runtime directory"
            )

    marker = workspace / _MARKER_NAME
    if marker.exists():
        if marker.is_symlink() or not marker.is_file():
            raise AttemptWorkspaceConflictError(
                "Attempt workspace marker is not a regular file"
            )
        try:
            existing_payload = marker.read_text(encoding="utf-8")
        except OSError as exc:
            raise AttemptWorkspaceConflictError(
                "Attempt workspace marker cannot be read"
            ) from exc
        if existing_payload != marker_payload:
            raise AttemptWorkspaceConflictError(
                "Attempt workspace belongs to a different execution identity"
            )
        return workspace

    if any(workspace.iterdir()):
        raise AttemptWorkspaceConflictError(
            "Unowned non-empty Attempt workspace cannot be claimed"
        )
    try:
        marker.write_text(marker_payload, encoding="utf-8", errors="strict")
    except OSError as exc:
        raise AttemptWorkspaceConflictError(
            "Attempt workspace marker cannot be committed"
        ) from exc
    return workspace


__all__ = [
    "AttemptWorkspaceConflictError",
    "lease_attempt_workspace",
    "prepare_attempt_workspace",
]
