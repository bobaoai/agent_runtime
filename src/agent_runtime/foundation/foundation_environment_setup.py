"""Small, local setup shared by root-based Runtime tool entry points."""

from __future__ import annotations

import hashlib
from importlib.resources import files
import json
import os
from pathlib import Path
import stat
import tempfile


_SKILLS = ("agent-runtime-registration", "agent-runtime-test-run")
_HOSTS = (".agents", ".claude")
_FORMAT = "agent_runtime_setup_v1"
# Exact predecessor operator content accepted for the initial source migration.
# Names alone never authorize replacement of an existing Skill.
_LEGACY_SKILL_SHA256 = {
    "agent-runtime-registration": frozenset({
        "7b6a34f87164201ddd420ce3f0e9dcefb98e7d8c2a393e8d36f558eef8a7458a",
        # 0.2.0.dev7 packaged content, replaced when the Test Run rename lands.
        "a7627eb1d9ce3f5fd99776f6fc95c9fa51a734ef8ce118173a6ef51c5699def6",
    }),
}
# Operator Skills no longer shipped. Only their exact packaged content, or the
# hash recorded by the previous setup, is removed; anything else is local work.
_RETIRED_SKILL_SHA256 = {
    "agent-runtime-evaluation": frozenset({
        "9ae353b814fdc10d96b8dea61c9996ca8dee229f0aac0f8bec8638de4f0f7059",
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
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise ValueError(f"Runtime setup target is not a regular file: {path}")
    return path.read_bytes()


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


def load_runtime_config(root: Path) -> dict[str, object]:
    """Read optional host resource defaults from root/.runtime/config.json.

    Args:
        root: Host configuration root, never a model read grant. The optional
            JSON object accepts only provider_cli_paths and read_only_dependencies.
            provider_cli_paths maps claude_cli or codex_cli to executable paths;
            read_only_dependencies is a list of trusted dependency directory paths.
            Relative locators are resolved against root. Tilde is not expanded.
    Returns:
        A new dictionary with provider_cli_paths (a mapping to Path values) and
        read_only_dependencies (a tuple of Path values). A missing config file
        supplies empty defaults. Locators are normalized here; the execution
        consumer checks the existence and type of resources it actually selects.
        Unused provider/dependency defaults never open resources or add tools.
    Raises:
        ValueError: Malformed JSON, unknown fields/providers, duplicate keys,
            empty locators, invalid field types, or a non-regular config path.
        OSError: Native file access failure; no alternate config is searched.
    Effects:
        Reads this one file only and writes nothing. Does not run setup, choose
        a model/Profile, read credentials, probe executables, connect to PG, or
        discover material. Explicit execution arguments take precedence: a
        dependency tuple overrides the default, including an explicitly empty
        tuple; None selects these defaults. A program argument overrides only
        the chosen transport's locator. External stores remain explicit API
        objects, not implicit connections from this configuration.
    """
    root = Path(root).resolve()
    body = _read(_target(root, ".runtime", "config.json"))
    if body is None:
        return {"provider_cli_paths": {}, "read_only_dependencies": ()}

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate Runtime config key")
            result[key] = value
        return result

    document = json.loads(body, object_pairs_hook=unique_object)
    if type(document) is not dict or set(document) - {"provider_cli_paths", "read_only_dependencies"}:
        raise ValueError("Runtime config accepts only provider_cli_paths and read_only_dependencies")
    providers = document.get("provider_cli_paths", {})
    dependencies = document.get("read_only_dependencies", [])
    if type(providers) is not dict or set(providers) - {"claude_cli", "codex_cli"}:
        raise ValueError("provider_cli_paths must map claude_cli/codex_cli to resource locators")
    if type(dependencies) is not list:
        raise ValueError("read_only_dependencies must be a list of directory locators")

    def locator(value):
        if type(value) is not str or not value.strip() or "\x00" in value:
            raise ValueError("Runtime resource locator must be a non-empty path")
        path = Path(value)
        return Path(os.path.abspath(path if path.is_absolute() else root / path))

    return {"provider_cli_paths": {key: locator(value) for key, value in providers.items()},
            "read_only_dependencies": tuple(locator(value) for value in dependencies)}


def _remove(path: Path) -> None:
    path.unlink(missing_ok=True)
    try:
        path.parent.rmdir()
    except OSError:
        pass


def setup_runtime(root: Path) -> tuple[Path, ...]:
    """Check one host root and fill in missing Runtime setup resources.

    Called by the registration/load CLIs after argument parsing and by the
    Test Run API before definition preparation, ahead of the requested operation. Hosts may also call it as part of
    their setup. A ready environment performs bounded local reads and no writes;
    there is no separate Skill installation command.

    Args:
        root: Explicit host directory, interpreted as a pathlib path. Tilde is
            not expanded here; a shell may expand it before calling a CLI.
            Creates .runtime when missing and places
            the two bundled operator Skills in .agents/skills and .claude/skills.
            A retired agent-runtime-evaluation Skill with exactly its packaged
            content or its previously recorded hash is removed after the
            replacement Skill is written.
            It is neither a model read root nor a model or database binding.
    Returns:
        Paths actually written or removed, or an empty tuple when setup is already current.
        Existing tool stdout remains the original operation's result.
    Raises:
        ValueError: Invalid setup metadata, a non-regular file or symlink in a managed target path,
            or locally changed/unknown same-name Skill content, including a
            changed retired Skill. The target is included in the error; resolve
            that content with its owner. Nothing is written when this happens.
        OSError: Missing package resources or native file failure. Some setup
            writes may have completed; repeat setup with the same installed
            package to finish preparation, not the model operation blindly.
    Effects:
        Reads only two packaged Skill files, four fixed host Skill files, two
        retired host Skill files and
        .runtime/setup.json. The latter records its format and last installed
        Skill SHA-256 values, covering raw UTF-8 file bytes, not Module identity.
        Preserves registered module/workflow definitions and all other Skills.
        Known predecessor content or unchanged managed content may be upgraded;
        unknown local content is never overwritten by name alone.
        Preflights targets before writing, writes new Skills, then removes
        managed retired Skill files and their emptied folders, and writes setup
        metadata last. Metadata still naming a retired Skill is readable.
        Partial preparation is recoverable with the same package; a retired
        Skill left under new metadata is still recognized by its exact hash. The host serializes setup on one root; there is no
        cross-process transaction, lock, history, credential or request service.
        Does not scan a workspace/catalog, connect to PG, call/login a provider,
        change models, install software, or validate business object inputs.
        Import and CLI help do not initialize a host environment.
    """
    root = Path(root).resolve()
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
                    or not isinstance(previous, dict) or set(previous) - set(_SKILLS) - set(_RETIRED_SKILL_SHA256)
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
    removals = []
    for host in _HOSTS:
        for name, accepted in _RETIRED_SKILL_SHA256.items():
            path = _target(root, host, "skills", name, "SKILL.md")
            before = _read(path)
            if before is None:
                folder = path.parent
                if folder.is_dir() and not folder.is_symlink() and not any(folder.iterdir()):
                    removals.append((path, None))
                continue
            digest = _digest(before)
            if digest not in accepted and digest != previous.get(name):
                raise ValueError(f"Runtime setup found local content in a retired Skill: {path}")
            removals.append((path, before))
    state_content = (json.dumps({"format": _FORMAT, "skill_sha256": hashes}, sort_keys=True,
                               indent=2) + "\n").encode()
    state = None
    if before_state != state_content:
        state = (state_path, before_state, state_content)
    written = []
    for path, before, content in pending:
        _target(root, *path.relative_to(root).parts)
        if _read(path) != before:
            raise ValueError(f"Runtime setup target changed during preparation: {path}")
        _write(path, content)
        written.append(path)
    for path, before in removals:
        _target(root, *path.relative_to(root).parts)
        if _read(path) != before:
            raise ValueError(f"Runtime setup target changed during preparation: {path}")
        _remove(path)
        written.append(path)
    if state is not None:
        path, before, content = state
        _target(root, *path.relative_to(root).parts)
        if _read(path) != before:
            raise ValueError(f"Runtime setup target changed during preparation: {path}")
        _write(path, content)
        written.append(path)
    return tuple(written)
