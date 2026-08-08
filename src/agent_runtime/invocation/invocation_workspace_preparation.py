"""Prepare and recover Runtime-owned Attempt workspaces safely."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..contracts.registry_contract_validation import validate_id


_MARKER_NAME = ".agent_runtime_attempt.json"


class AttemptWorkspaceConflictError(RuntimeError):
    """Raised when a deterministic Attempt path belongs to another identity."""


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
    "prepare_attempt_workspace",
]
