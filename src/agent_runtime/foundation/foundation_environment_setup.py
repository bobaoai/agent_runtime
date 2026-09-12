"""Small, local setup shared by root-based Runtime tool entry points."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
from pathlib import Path
import tempfile


_SKILLS = ("agent-runtime-registration", "agent-runtime-evaluation")
_HOSTS = (".agents", ".claude")
_FORMAT = "agent_runtime_setup_v1"
# Exact predecessor operator content accepted for the initial source migration.
# Names alone never authorize replacement of an existing Skill.
_LEGACY_SKILL_SHA256 = {
    "agent-runtime-registration": frozenset({
        "7b6a34f87164201ddd420ce3f0e9dcefb98e7d8c2a393e8d36f558eef8a7458a",
    }),
}


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _target(root: Path, *parts: str) -> Path:
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Runtime setup target is a symlink: {path}")
    return path


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def setup_runtime(root: Path) -> tuple[Path, ...]:
    """Check one host root and fill in missing Runtime setup resources.

    Called by the existing registration/load and evaluation CLIs after argument
    parsing, before the requested operation. Hosts may also call it as part of
    their setup. A ready environment performs bounded local reads and no writes;
    there is no separate Skill installation command.

    Args:
        root: Explicit host directory. Creates .runtime when missing and places
            the two bundled operator Skills in .agents/skills and .claude/skills.
            It is neither a model read root nor a model or database binding.
    Returns:
        Paths actually written, or an empty tuple when setup is already current.
        Existing tool stdout remains the original operation's result.
    Raises:
        ValueError: Invalid setup metadata, a symlink in a managed target path,
            or locally changed/unknown same-name Skill content. The target is
            included in the error; resolve that content with its owner.
        OSError: Missing package resources or native file failure. Some setup
            writes may have completed; repeat setup with the same installed
            package to finish preparation, not the model operation blindly.
    Effects:
        Reads only two packaged Skill files, four fixed host Skill files and
        .runtime/setup.json. The latter records its format and last installed
        Skill SHA-256 values, covering raw UTF-8 file bytes, not Module identity.
        Preserves registered module/workflow definitions and all other Skills.
        Known predecessor content or unchanged managed content may be upgraded;
        unknown local content is never overwritten by name alone.
        Preflights targets before writing, replaces individual files atomically,
        and writes setup metadata last. Partial preparation is recoverable with
        the same package. The host serializes setup on one root; there is no
        cross-process transaction, lock, history, credential or request service.
        Does not scan a workspace/catalog, connect to PG, call/login a provider,
        change models, install software, or validate business object inputs.
        Import and CLI help do not initialize a host environment.
    """
    root = Path(root).expanduser().resolve()
    if root.exists() and not root.is_dir():
        raise NotADirectoryError(f"Runtime setup root is not a directory: {root}")
    package = files("agent_runtime")
    resources = {name: package.joinpath("skills", name, "SKILL.md").read_bytes() for name in _SKILLS}
    hashes = {name: _digest(body) for name, body in resources.items()}
    state_path = _target(root, ".runtime", "setup.json")
    before_state = _read(state_path)
    previous = {}
    if before_state is not None:
        try:
            state = json.loads(before_state)
            previous = state["skill_sha256"]
            if (set(state) != {"format", "skill_sha256"} or state["format"] != _FORMAT
                    or not isinstance(previous, dict) or set(previous) - set(_SKILLS)
                    or any(not isinstance(value, str) or len(value) != 64
                           or any(char not in "0123456789abcdef" for char in value)
                           for value in previous.values())):
                raise ValueError("invalid setup fields")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid Runtime setup metadata: {state_path}") from exc
    pending = []
    for host in _HOSTS:
        for name, content in resources.items():
            path = _target(root, host, "skills", name, "SKILL.md")
            before = _read(path)
            if before is not None and before != content:
                digest = _digest(before)
                if digest != previous.get(name) and digest not in _LEGACY_SKILL_SHA256.get(name, ()):
                    raise ValueError(f"Runtime setup found local Skill content: {path}")
            if before != content:
                pending.append((path, before, content))
    state_content = (json.dumps({"format": _FORMAT, "skill_sha256": hashes}, sort_keys=True,
                               indent=2) + "\n").encode()
    if before_state != state_content:
        pending.append((state_path, before_state, state_content))
    written = []
    for path, before, content in pending:
        _target(root, *path.relative_to(root).parts)
        if _read(path) != before:
            raise ValueError(f"Runtime setup target changed during preparation: {path}")
        _write(path, content)
        written.append(path)
    return tuple(written)
