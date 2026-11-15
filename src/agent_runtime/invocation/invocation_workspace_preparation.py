"""Prepare, retain, seal and maintain Runtime-owned Attempt workspaces safely."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import errno
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Iterator, Mapping

from ..foundation.foundation_contract_validation import validate_id
from .invocation_host_platform_selection import HOST_PLATFORM, UnsupportedHostError


_MARKER_NAME = ".agent_runtime_attempt.json"
_SEAL_NAME = ".agent_runtime_attempt_seal.json"
_LEASE_DIRECTORY_NAME = ".agent_runtime_attempt_leases"
_UTC_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class AttemptWorkspaceConflictError(RuntimeError):
    """Raised when a deterministic Attempt path belongs to another identity."""


def _parse_utc_instant(label: str, value: Any) -> datetime:
    """Parse an RFC 3339 UTC instant written with the Z suffix (Timestamp T0 ``_at_utc``)."""
    if type(value) is not str or not _UTC_INSTANT.fullmatch(value):
        raise ValueError(f"{label} must be an RFC 3339 UTC instant ending in Z")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _ordinary_directory(path: Path) -> bool:
    """True only for a real directory that is not a symlink or reparse point."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISDIR(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & _REPARSE)


@contextmanager
def _hold_lease(workspace: Path) -> Iterator[Path]:
    """Hold the OS-backed exclusive lock beside one Attempt directory.

    The lock file lives in a sibling directory, not inside the Attempt. The
    final path component is not resolved, so a replaced Attempt path cannot
    move the lock elsewhere. The operating system releases the lock if the
    holder exits, including after a crash; on Windows that release can lag
    briefly. A host without a lock implementation fails closed.
    """
    workspace = Path(workspace)
    lease_directory = workspace.parent.resolve() / _LEASE_DIRECTORY_NAME
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
    try:
        handle = open(lease_path, "a+", encoding="utf-8")
    except OSError as exc:
        raise AttemptWorkspaceConflictError(
            "Attempt workspace lease cannot be opened"
        ) from exc
    try:
        try:
            HOST_PLATFORM.acquire_lease_lock(handle)
        except UnsupportedHostError as exc:
            # Crash-safe duplicate-dispatch fencing needs a kernel-released
            # lock; without one there is no correct substitute, so fail closed.
            raise AttemptWorkspaceConflictError(str(exc)) from exc
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
            HOST_PLATFORM.release_lease_lock(handle)
    finally:
        handle.close()


@contextmanager
def lease_attempt_workspace(workspace: Path) -> Iterator[Path]:
    """Hold an OS-backed exclusive lock for one provider invocation.

    The persistent lock file lives beside the provider-writable Attempt
    directory, not inside it. The operating system releases the lock if the
    worker exits, including after a crash; on Windows that release can lag the
    process exit briefly. A same-process duplicate using a separate open file
    is fenced just like a duplicate in another process. The lock itself comes
    from the selected host platform; a host without one fails closed. A sealed
    Attempt is refused after the lock is taken, so a seal written between
    preparation and leasing is honoured.
    """

    with _hold_lease(Path(workspace).resolve()) as held:
        if (held / _SEAL_NAME).exists():
            raise AttemptWorkspaceConflictError(
                "Attempt workspace is sealed and is not reopened for execution"
            )
        yield held


def prepare_attempt_workspace(
    *,
    workspace_root: Path,
    attempt_identity: Mapping[str, Any],
) -> Path:
    """Create or recover the exact workspace owned by one Runtime Attempt.

    A repeated invocation with the same identity reuses its own drafts until
    the host seals the folder; a sealed folder is never reopened for
    execution. A pre-existing directory without the Runtime marker is accepted
    only when it is empty, which also recovers a crash between ``mkdir`` and
    marker commit.
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
        if (workspace / _SEAL_NAME).exists():
            raise AttemptWorkspaceConflictError(
                "Attempt workspace is sealed and is not reopened for execution"
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


def validate_project_directory(path: Path, *, workspace_root: Path) -> Path:
    """Resolve the caller's writable project directory and keep it apart from Runtime folders.

    It must be an existing absolute directory, not a filesystem root, must not
    overlap the Attempt workspace root in either direction, and must not be or
    contain the host temporary root where Runtime creates its private per-call
    directories. Runtime never deletes, seals or cleans it.
    """
    import tempfile
    candidate = Path(path)
    if not candidate.is_absolute():
        raise ValueError("project_directory must be an absolute path")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("project_directory must be an existing directory")
    if resolved == Path(resolved.anchor):
        raise ValueError("project_directory cannot be a filesystem root")
    root = Path(workspace_root).resolve()
    if resolved == root or resolved.is_relative_to(root) or root.is_relative_to(resolved):
        raise ValueError(f"project_directory overlaps the Runtime Attempt workspace root {root}")
    temporary = Path(tempfile.gettempdir()).resolve()
    if temporary == resolved or temporary.is_relative_to(resolved):
        raise ValueError(f"project_directory contains the host temporary root {temporary}")
    return resolved


def _read_record(path: Path) -> dict | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def seal_attempt_workspace(*, workspace_root: Path, attempt_id: str, effective_at_utc: str) -> Path:
    """Record that the host has archived this Attempt folder and ended its writable use.

    The seal is ``.agent_runtime_attempt_seal.json`` holding ``attempt_id`` and
    ``effective_at_utc``: the host's UTC instant from which the sealed state
    applies and from which retention is measured. The folder must be an ordinary
    directory whose Runtime marker names ``attempt_id``, and no live invocation
    may hold its lease. Repeating the identical seal returns the folder; a
    different seal raises ``AttemptWorkspaceConflictError``. Access control is
    never changed.

    Raises:
        ValueError: Invalid id or instant.
        AttemptWorkspaceConflictError: Missing, foreign, leased or differently sealed folder.
    """
    validate_id("attempt_id", attempt_id)
    _parse_utc_instant("effective_at_utc", effective_at_utc)
    workspace = Path(workspace_root).resolve() / attempt_id
    with _hold_lease(workspace):
        if not _ordinary_directory(workspace):
            raise AttemptWorkspaceConflictError("Attempt workspace to seal is not an ordinary directory")
        marker = _read_record(workspace / _MARKER_NAME)
        if marker is None or marker.get("attempt_id") != attempt_id:
            raise AttemptWorkspaceConflictError("Attempt workspace marker does not name this Attempt")
        record = {"attempt_id": attempt_id, "effective_at_utc": effective_at_utc}
        seal = workspace / _SEAL_NAME
        if seal.exists() or seal.is_symlink():
            if _read_record(seal) != record:
                raise AttemptWorkspaceConflictError("Attempt workspace already has a different seal")
            return workspace
        with open(seal, "x", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
    return workspace


_ARCHIVE_NAME = "archive"
_ARCHIVE_FILE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _ordinary_file(path: Path) -> bool:
    """True only for a regular file that is not a symlink or reparse point."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not (getattr(info, "st_file_attributes", 0) & _REPARSE)


def write_attempt_archive(*, workspace_root: Path, attempt_id: str, file_name: str, content: bytes) -> Path:
    """Write one archive file inside this Attempt's own archive folder, or refuse.

    Under the Attempt's lease, the Attempt must be an ordinary directory whose
    marker names ``attempt_id``, and ``archive`` inside it must be an ordinary
    directory (created here when absent) - never a symlink, junction or other
    reparse point. The file is created exclusively, then the Attempt, archive
    folder and file are checked again. These are path checks (as for Windows
    material checks): a redirection seen before writing means nothing is
    written; one seen afterwards is reported as a refusal, never as success.
    Existing content is never replaced or removed, so a refused scene stays as
    found.

    Returns:
        The written file's path.
    Raises:
        ValueError: Invalid Attempt id or file name, or content not bytes.
        AttemptWorkspaceConflictError: The Attempt or archive folder is not
            Runtime's own ordinary directory, or a check after writing failed.
        OSError: The file could not be created (including one that already exists).
    """
    validate_id("attempt_id", attempt_id)
    if type(file_name) is not str or not _ARCHIVE_FILE.fullmatch(file_name):
        raise ValueError("archive file name must be a plain file name")
    if type(content) is not bytes:
        raise ValueError("archive content must be bytes")
    root = Path(workspace_root).resolve()
    workspace = root / attempt_id
    archive = workspace / _ARCHIVE_NAME
    target = archive / file_name

    def owned(require_target: bool) -> bool:
        marker = _read_record(workspace / _MARKER_NAME)
        return (_ordinary_directory(workspace) and marker is not None and marker.get("attempt_id") == attempt_id
                and _ordinary_directory(archive) and archive.resolve() == workspace.resolve() / _ARCHIVE_NAME
                and (not require_target or (_ordinary_file(target)
                                            and target.resolve() == archive.resolve() / file_name)))

    with _hold_lease(workspace):
        if not _ordinary_directory(workspace):
            raise AttemptWorkspaceConflictError("Attempt to archive is not an ordinary Runtime directory")
        if not os.path.lexists(archive):
            os.mkdir(archive)  # Default mode: inherits the Attempt's access, as before.
        if not owned(require_target=False):
            raise AttemptWorkspaceConflictError(
                "Attempt archive folder is not Runtime's own ordinary directory; nothing was written")
        with open(target, "xb") as stream:
            stream.write(content)
        if not owned(require_target=True):
            raise AttemptWorkspaceConflictError(
                f"Attempt archive at {target} changed while it was written; it is not reported as saved")
    return target


@dataclass(frozen=True)
class AttemptWorkspaceRetentionFacts:
    """Host-supplied execution facts for one retained Attempt folder.

    execution_ended: the Attempt's execution reached a terminal state.
    process_stop_confirmed: Runtime confirmed its started CLI process exited
        (no cleanup_error in the provider trace and no *_cleanup_failed code).
    archive_complete: the required logs and results are saved, gaps stated.
    retention_hold: an exception or recovery reference requires keeping it.
    """

    execution_ended: bool
    process_stop_confirmed: bool
    archive_complete: bool
    retention_hold: bool

    def validate(self) -> None:
        """Require every fact to be a bool; raises ValueError naming the first that is not."""
        for name in ("execution_ended", "process_stop_confirmed", "archive_complete", "retention_hold"):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be a boolean")


@dataclass(frozen=True)
class AttemptWorkspaceMaintenanceResult:
    """What maintenance did with one listed Attempt folder.

    outcome: cleanable (query: every condition met), retained (some condition
    not met; see retained_reasons), deleted (removed by this call) or
    deletion_failed (removal failed; the remainder is kept, see failure_message).
    """

    attempt_id: str
    outcome: str
    retained_reasons: tuple[str, ...] = ()
    failure_message: str | None = None


_INTENTS = frozenset({"query", "cleanup"})


def maintain_attempt_workspaces(
    *,
    workspace_root: Path,
    facts: Mapping[str, AttemptWorkspaceRetentionFacts],
    retention_seconds: int | None,
    observed_at_utc: str,
    intent: str,
) -> tuple[AttemptWorkspaceMaintenanceResult, ...]:
    """Query or clean expired, owned, sealed Attempt folders under host policy.

    Only the Attempt ids in ``facts`` (the host's archive set) are considered,
    in sorted order; other folders and lease files are never touched. Every
    input is validated before any path is used. For each listed id, under its
    non-blocking lease: the path must be an ordinary directory (no link or
    reparse point) whose marker names the id; its identity is captured; the seal
    record and the host facts are checked; ``retention_seconds`` (None means no
    automatic deletion) is compared using the seal's ``effective_at_utc`` and the
    host's ``observed_at_utc``, both RFC 3339 UTC instants from the host clock.
    With intent ``cleanup`` and no retained reason, the folder is removed through
    the host disposal bound to the captured identity, lifting read-only state
    only inside the owned tree; a replaced folder is never deleted. A disposal
    error becomes ``deletion_failed`` naming the location and never widens
    deletion. Runtime folders only: caller project directories are never listed.

    Raises:
        ValueError: Invalid intent, retention, instant, fact or Attempt id.
    """
    if intent not in _INTENTS:
        raise ValueError("intent must be query or cleanup")
    if retention_seconds is not None and (type(retention_seconds) is not int or retention_seconds < 1):
        raise ValueError("retention_seconds must be a positive integer or None")
    observed = _parse_utc_instant("observed_at_utc", observed_at_utc)
    if not isinstance(facts, Mapping):
        raise ValueError("facts must map Attempt ids to AttemptWorkspaceRetentionFacts")
    for attempt_id, fact in facts.items():
        validate_id("attempt_id", attempt_id)
        if type(fact) is not AttemptWorkspaceRetentionFacts:
            raise ValueError("facts values must be AttemptWorkspaceRetentionFacts")
        fact.validate()
    root = Path(workspace_root).resolve()
    results = []
    for attempt_id in sorted(facts):
        fact = facts[attempt_id]
        workspace = root / attempt_id
        try:
            with _hold_lease(workspace):
                results.append(_maintain_one(workspace, attempt_id, fact, retention_seconds, observed, intent))
        except AttemptWorkspaceConflictError:
            results.append(AttemptWorkspaceMaintenanceResult(attempt_id, "retained", ("lease_active",)))
    return tuple(results)


def _maintain_one(workspace: Path, attempt_id: str, fact: AttemptWorkspaceRetentionFacts,
                  retention_seconds: int | None, observed: datetime, intent: str) -> AttemptWorkspaceMaintenanceResult:
    if not _ordinary_directory(workspace):
        return AttemptWorkspaceMaintenanceResult(attempt_id, "retained", ("ownership_unconfirmed",))
    try:
        identity = HOST_PLATFORM.private_directory_identity(workspace)
    except OSError:
        return AttemptWorkspaceMaintenanceResult(attempt_id, "retained", ("ownership_unconfirmed",))
    marker = _read_record(workspace / _MARKER_NAME)
    if marker is None or marker.get("attempt_id") != attempt_id:
        return AttemptWorkspaceMaintenanceResult(attempt_id, "retained", ("ownership_unconfirmed",))
    seal = _read_record(workspace / _SEAL_NAME)
    sealed_at = None
    if seal is not None and seal.get("attempt_id") == attempt_id:
        try:
            sealed_at = _parse_utc_instant("effective_at_utc", seal.get("effective_at_utc"))
        except ValueError:
            sealed_at = None
    if sealed_at is None:
        return AttemptWorkspaceMaintenanceResult(attempt_id, "retained", ("seal_unconfirmed",))
    reasons = []
    if not fact.execution_ended:
        reasons.append("execution_not_ended")
    if not fact.process_stop_confirmed:
        reasons.append("process_stop_unconfirmed")
    if not fact.archive_complete:
        reasons.append("archive_incomplete")
    if fact.retention_hold:
        reasons.append("retention_hold")
    if retention_seconds is None:
        reasons.append("retention_policy_absent")
    elif observed < sealed_at:
        reasons.append("time_evidence_inconsistent")
    elif observed < sealed_at + timedelta(seconds=retention_seconds):
        reasons.append("retention_not_expired")
    if reasons:
        return AttemptWorkspaceMaintenanceResult(attempt_id, "retained", tuple(reasons))
    if intent == "query":
        return AttemptWorkspaceMaintenanceResult(attempt_id, "cleanable")
    try:
        HOST_PLATFORM.remove_private_directory(workspace, identity, clear_read_only=True)
    except OSError as exc:
        return AttemptWorkspaceMaintenanceResult(attempt_id, "deletion_failed",
                                                 failure_message=f"{workspace}: {exc}")
    return AttemptWorkspaceMaintenanceResult(attempt_id, "deleted")


def utc_now_instant() -> str:
    """The current host UTC instant in the RFC 3339 Z form used by seals."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


__all__ = [
    "AttemptWorkspaceConflictError",
    "AttemptWorkspaceMaintenanceResult",
    "AttemptWorkspaceRetentionFacts",
    "lease_attempt_workspace",
    "maintain_attempt_workspaces",
    "prepare_attempt_workspace",
    "seal_attempt_workspace",
    "write_attempt_archive",
]
