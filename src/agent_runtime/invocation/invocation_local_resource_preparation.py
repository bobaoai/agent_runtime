"""Capture invocation-local files and command descriptions without granting effects."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys

from ..contracts.registry_release_definition import ExecutionProfileRelease
from ..foundation.foundation_contract_validation import validate_sha256
from .invocation_host_platform_selection import HOST_PLATFORM


LOCAL_RESOURCES_SCHEMA_REF = "schema:runtime_local_resources@v1"
LOCAL_RESOURCES_LOGICAL_NAME = "local_resources"
LOCAL_RESOURCES_MEDIA_TYPE = "application/json"
_VERSION = "runtime_local_resources_v1"
_FILE_KEYS = {"relative_path", "sha256", "executable", "content_base64"}
_COMMAND_KEYS = {"command_id", "argv", "cwd", "timeout_seconds"}
_ROOT_KEYS = {"schema_version", "material_root", "material_files", "read_only_dependencies", "commands"}


def _canonical(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


LOCAL_RESOURCES_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": LOCAL_RESOURCES_SCHEMA_REF,
    "type": "object", "additionalProperties": False, "required": sorted(_ROOT_KEYS),
    "properties": {
        "schema_version": {"const": _VERSION}, "material_root": {"type": ["string", "null"]},
        "material_files": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": sorted(_FILE_KEYS),
            "properties": {"relative_path": {"type": "string", "minLength": 1},
                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                "executable": {"type": "boolean"}, "content_base64": {"type": "string"}}}},
        "read_only_dependencies": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
        "commands": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": sorted(_COMMAND_KEYS),
            "properties": {"command_id": {"type": "string", "minLength": 1},
                "argv": {"type": "array", "minItems": 1, "items": {"type": "string", "minLength": 1}},
                "cwd": {"type": "string", "minLength": 1},
                "timeout_seconds": {"type": "integer", "minimum": 1}}}},
    },
}
LOCAL_RESOURCES_SCHEMA_SHA256 = hashlib.sha256(_canonical(LOCAL_RESOURCES_SCHEMA)).hexdigest()


class LocalResourceError(ValueError):
    """Return an existing Adapter error family with bounded local resource detail."""

    def __init__(self, message: str, *, error_code: str = "ADAPTER_REQUEST_INVALID"):
        self.error_code = error_code
        super().__init__(message)


def _relative(value: str) -> tuple[str, ...]:
    if (type(value) is not str or not value or "\x00" in value or "\\" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or PurePosixPath(value).is_absolute()):
        raise LocalResourceError("resource path must be a normalized relative POSIX path")
    return PurePosixPath(value).parts


def _absolute(value):
    if type(value) is not str or "\x00" in value or not Path(value).is_absolute() or ".." in Path(value).parts:
        raise LocalResourceError("private resource locator must be an absolute path")


def _file_rows(rows, *, captured):
    if type(rows) not in {tuple, list}:
        raise LocalResourceError("material_files must be a sequence")
    names = set()
    for row in rows:
        if type(row) is not dict or set(row) != (_FILE_KEYS if captured else _FILE_KEYS - {"content_base64"}):
            raise LocalResourceError("material file fields differ from the declared contract")
        _relative(row["relative_path"])
        if row["relative_path"] in names:
            raise LocalResourceError("duplicate material relative_path")
        names.add(row["relative_path"])
        try:
            validate_sha256("material sha256", row["sha256"])
        except ValueError as exc:
            raise LocalResourceError(str(exc)) from exc
        if type(row["executable"]) is not bool:
            raise LocalResourceError("material executable must be a boolean")
        if captured:
            if type(row["content_base64"]) is not str:
                raise LocalResourceError("material content_base64 must be text")
            try:
                body = base64.b64decode(row["content_base64"], validate=True)
            except (ValueError, UnicodeError) as exc:
                raise LocalResourceError("invalid material base64") from exc
            if base64.b64encode(body).decode("ascii") != row["content_base64"] or hashlib.sha256(body).hexdigest() != row["sha256"]:
                raise LocalResourceError("captured material bytes do not match their hash")
    for name in names:
        if any(str(parent) in names for parent in PurePosixPath(name).parents if str(parent) != "."):
            raise LocalResourceError("a material file cannot also be a parent directory")


def _commands(rows, *, files, timeout=None):
    if type(rows) not in {tuple, list}:
        raise LocalResourceError("commands must be a sequence")
    directories = {"source", "scratch"}
    for row in files:
        directories.update("source/" + str(parent) for parent in PurePosixPath(row["relative_path"]).parents if str(parent) != ".")
    identities = set()
    for row in rows:
        if type(row) is not dict or set(row) != _COMMAND_KEYS:
            raise LocalResourceError("command fields differ from the declared contract")
        identity = row["command_id"]
        if type(identity) is not str or not identity.strip() or "\x00" in identity or identity in identities:
            raise LocalResourceError("command_id must be nonempty and unique in this invocation")
        identities.add(identity)
        if (type(row["argv"]) is not list or not row["argv"]
                or any(type(value) is not str or not value or "\x00" in value for value in row["argv"])):
            raise LocalResourceError("command argv must be a nonempty string array without NUL")
        parts = _relative(row["cwd"])
        if parts[0] not in {"source", "scratch"}:
            raise LocalResourceError("command cwd must be source or scratch, optionally with subdirectories")
        if parts[0] == "source" and row["cwd"] not in directories:
            raise LocalResourceError("command source cwd is absent from the frozen file tree")
        if type(row["timeout_seconds"]) is not int or row["timeout_seconds"] < 1 or (
                timeout is not None and row["timeout_seconds"] > timeout):
            raise LocalResourceError("command timeout must be positive and within the Profile budget")


def _dependencies(values):
    if type(values) not in {tuple, list}:
        raise LocalResourceError("read_only_dependencies must be a sequence")
    seen = set()
    for value in values:
        _absolute(value)
        if value == str(Path(value).anchor) or value in seen:
            raise LocalResourceError("dependency roots must be unique and cannot grant the filesystem root")
        seen.add(value)


# Current bindings that accept frozen materials, dependencies and commands.
_RESOURCE_BINDINGS = {("claude_cli_adapter", "v4", "claude_cli"),
                      ("codex_cli_agent_workspace_executor", "v5", "codex_cli"),
                      ("antigravity_cli_adapter", "v1", "antigravity_cli")}


def _profile_resources(profile, *, used, commands):
    if type(profile) is not ExecutionProfileRelease:
        raise LocalResourceError("profile must be an exact ExecutionProfileRelease")
    profile.validate()
    if not used:
        return
    if ((profile.executor_adapter_id, profile.executor_adapter_revision,
            profile.transport_kind) not in _RESOURCE_BINDINGS
            or profile.execution_mode != "agent" or profile.semantic_input_delivery_mode != "inline"
            or profile.network_policy not in {"denied", "provider_managed"} or profile.gateway_access_reasons
            or not set(profile.tool_policy) & {"read", "search", "edit", "shell"}):
        raise LocalResourceError("local files/dependencies/commands require a supported agent binding "
                                 "(Claude v4, Codex workspace v5 or Antigravity v1) with a native file tool",
                                 error_code="ADAPTER_CAPABILITY_UNSUPPORTED")
    if commands and sys.platform != "darwin":
        raise LocalResourceError("declared local commands require macOS sandbox-exec",
                                 error_code="ADAPTER_CAPABILITY_UNSUPPORTED")
    if commands and ("shell" not in profile.tool_policy or profile.attempt_workspace_policy != "own_draft_read_write"):
        raise LocalResourceError("local commands require shell and private draft workspace",
                                 error_code="ADAPTER_CAPABILITY_UNSUPPORTED")


def _host_material(operation, *args):
    """Run a HostPlatform material operation; its own refusals become LocalResourceError."""
    try:
        return operation(*args)
    except LocalResourceError:
        raise
    except ValueError as exc:
        raise LocalResourceError(str(exc)) from exc


def capture_local_resources(*, profile, material_root=None, material_files=(), read_only_dependencies=(), commands=()) -> bytes | None:
    """Freeze explicit files, dependency roots and command descriptions once.

    The explicitly supplied root locator is resolved once (including host path
    aliases); relative manifest members never traverse parents or follow links.
    File content and execution bits must match the supplied manifest. No model,
    command or registry is invoked. Dependency directories are authorized roots,
    not recursively captured installations. The Adapter must additionally reject
    overlap with its concrete private state/authentication/control paths.
    With no files, dependencies or commands, return None and read no source root.

    Args:
        profile: Exact execution Profile; capabilities are checked, never added.
        material_root: Explicit host locator for the source tree, resolved once.
        material_files: Relative path, SHA-256 and boolean executable declarations.
        read_only_dependencies: Explicit existing directory locators, not snapshots.
        commands: Exact command_id/argv/logical cwd/timeout declarations; no execution.
    Returns:
        Canonical private JSON bytes with captured base64 files, or None.
    Raises:
        LocalResourceError: Invalid declarations or an unsupported capability set.
        OSError: A selected source/dependency cannot be read safely; no fallback.
    Effects:
        Reads only selected source files and directory metadata. Writes nothing.
    """
    _profile_resources(profile, used=False, commands=())
    if type(read_only_dependencies) not in {tuple, list}:
        raise LocalResourceError("read_only_dependencies must be a sequence")
    _file_rows(material_files, captured=False)
    _commands(commands, files=material_files, timeout=profile.timeout_seconds)
    used = bool(material_files or read_only_dependencies or commands)
    _profile_resources(profile, used=used, commands=commands)
    if not used:
        return None
    if material_files and material_root is None:
        raise LocalResourceError("material_files require material_root")
    roots = []
    for value in read_only_dependencies:
        root = Path(value).resolve(strict=True)
        if not root.is_dir():
            raise LocalResourceError("read-only dependency must be an existing directory")
        roots.append(str(root))
    _dependencies(roots)
    captured = []
    origin = None
    if material_root is not None:
        path = Path(material_root).resolve(strict=True)
        if ".." in path.parts:
            raise LocalResourceError("directory locator cannot contain parent traversal")
        for item in material_files:
            content, executable = _host_material(HOST_PLATFORM.read_material_file, path, _relative(item["relative_path"]))
            # A host without execute bits (Windows) reports None: the manifest value is kept.
            if hashlib.sha256(content).hexdigest() != item["sha256"] or (
                    executable is not None and executable != item["executable"]):
                raise LocalResourceError("source bytes or executable mode differ from the material manifest")
            captured.append({**item, "content_base64": base64.b64encode(content).decode("ascii")})
        origin = str(path)
    document = {"schema_version": _VERSION, "material_root": origin, "material_files": captured,
                "read_only_dependencies": roots, "commands": list(commands)}
    body = _canonical(document)
    validate_local_resources(profile=profile, body=body)
    return body


def _selected_parts(root: Path, value) -> tuple[str, ...]:
    """Relative path parts of one explicitly selected file under root, without resolving links."""
    if not isinstance(value, (str, os.PathLike)) or not str(value) or "\x00" in str(value):
        raise LocalResourceError("a selected material must be a non-empty path")
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            candidate = Path(os.path.abspath(candidate)).relative_to(root)
        except ValueError as exc:
            raise LocalResourceError(f"selected material {value} is outside material_root {root}") from exc
    parts = candidate.parts
    if not parts or any(part in {"", ".", ".."} for part in parts) or candidate.anchor:
        raise LocalResourceError(f"selected material {value} must be a file path inside material_root")
    _relative("/".join(parts))
    return tuple(parts)


def _whole_folder(root: Path) -> list[tuple[str, ...]]:
    """Every ordinary file below root; a link, reparse point or special file is refused, not skipped."""
    found = []

    def visit(folder: Path, prefix: tuple[str, ...]) -> None:
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise LocalResourceError(f"{Path(entry.path)} is a link or reparse point; select the material "
                                             "files explicitly instead of the whole folder")
                if stat.S_ISDIR(info.st_mode):
                    visit(Path(entry.path), prefix + (entry.name,))
                elif stat.S_ISREG(info.st_mode):
                    found.append(prefix + (entry.name,))
                else:
                    raise LocalResourceError(f"{Path(entry.path)} is not an ordinary file or folder")

    visit(root, ())
    return found


def prepare_material_files(material_root, selected=None) -> tuple[dict, ...]:
    """Compute the frozen material list for files explicitly selected under one explicit folder.

    This is the ordinary way to build material_files: the caller chooses the
    folder and the files, and code reads the actual bytes to produce each
    relative_path, sha256 and executable value. The result goes unchanged to
    run_local_workflow_test (or write_local_resources), whose capture reads the
    files again and refuses any later change; no hash is ever refreshed to hide
    drift.

    Args:
        material_root: The explicit folder that holds the materials; resolved
            once, as capture resolves it. It is never the host root by default.
        selected: Which files to use. None selects the whole folder: every
            ordinary file below it, refusing (not skipping) any link, reparse
            point or special entry. A sequence selects exactly those files,
            each relative to material_root (native or POSIX separators) or an
            absolute path inside it. An empty sequence selects nothing and
            returns an empty tuple; it is never widened to the folder.
    Returns:
        Tuple of {relative_path, sha256, executable} rows sorted by POSIX
        relative_path. executable is the file's execute bit where the host has
        one; on Windows, which has none, it is False.
    Raises:
        FileNotFoundError: material_root or a selected file is missing.
        LocalResourceError: A path leaves the folder, repeats, names a folder,
            passes a link or reparse point, or the whole folder has no file.
        OSError: A selected file cannot be read.
    Effects:
        Reads only the selected files (and, for the whole folder, its directory
        entries). Writes nothing; starts no model or command.
    """
    root = Path(material_root).resolve(strict=True)
    if not root.is_dir():
        raise LocalResourceError(f"material_root {root} is not a folder")
    if selected is None:
        chosen = _whole_folder(root)
        if not chosen:
            raise LocalResourceError(f"material_root {root} contains no files to select")
    else:
        if isinstance(selected, (str, os.PathLike)) or not isinstance(selected, (tuple, list)):
            raise LocalResourceError("selected must be None (whole folder) or a sequence of file paths")
        chosen = [_selected_parts(root, value) for value in selected]
    rows = {}
    for parts in chosen:
        name = "/".join(parts)
        if name in rows:
            raise LocalResourceError(f"material {name} is selected twice")
        target = root.joinpath(*parts)
        if not target.exists() and not target.is_symlink():
            raise FileNotFoundError(f"selected material is missing: {target}")
        content, executable = _host_material(HOST_PLATFORM.read_material_file, root, parts)
        rows[name] = {"relative_path": name, "sha256": hashlib.sha256(content).hexdigest(),
                      "executable": bool(executable)}
    result = tuple(rows[name] for name in sorted(rows))
    _file_rows(result, captured=False)
    return result


def write_local_resources(path, *, material_root, material_files, read_only_dependencies=(), commands=()) -> Path:
    """Write a resources file for --resources from code-computed values (exclusive create).

    material_files are normally prepare_material_files rows; material_root and
    dependency locators are written as absolute paths. The existing reader and
    capture validate the file again at use, so a material that changed after
    this file was written is refused then. An existing file is never
    overwritten and its folder must exist. Returns the resolved path.
    """
    _file_rows(material_files, captured=False)
    if material_files and material_root is None:
        raise LocalResourceError("material_files require material_root")
    if type(read_only_dependencies) not in {tuple, list} or type(commands) not in {tuple, list}:
        raise LocalResourceError("read_only_dependencies and commands must be sequences")
    _commands(commands, files=material_files)
    document = {"material_root": None if material_root is None else str(Path(material_root).resolve(strict=True)),
                "material_files": [dict(row) for row in material_files],
                "read_only_dependencies": [str(Path(value).resolve(strict=True)) for value in read_only_dependencies],
                "commands": [dict(row) for row in commands]}
    path = Path(path)
    with open(path, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return path.resolve()


def parse_local_resources(body: bytes) -> dict:
    """Validate a private control package without reopening its source paths.

    Returns the validated document with schema_version, material_root,
    material_files, read_only_dependencies and commands. Malformed fields,
    duplicate keys/paths/IDs and mismatched bytes raise LocalResourceError.
    This pure decoder establishes neither live resource permission nor a Profile
    timeout/capability decision; the host/Adapter still binds those actual inputs.
    """
    if type(body) is not bytes:
        raise LocalResourceError("local_resources must be bytes")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise LocalResourceError("duplicate JSON resource key")
            result[key] = value
        return result
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=unique)
        _canonical(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise LocalResourceError("invalid local_resources JSON") from exc
    if type(value) is not dict or set(value) != _ROOT_KEYS or value["schema_version"] != _VERSION:
        raise LocalResourceError("unsupported local_resources format")
    if value["material_root"] is not None:
        _absolute(value["material_root"])
    _file_rows(value["material_files"], captured=True)
    if value["material_files"] and value["material_root"] is None:
        raise LocalResourceError("captured files lack their private origin locator")
    _dependencies(value["read_only_dependencies"])
    _commands(value["commands"], files=value["material_files"])
    return value


def validate_local_resources(*, profile, body: bytes | None) -> dict | None:
    """Validate an already captured package against this exact execution Profile.

    Pure validation for every resource-consuming entry, including live self-test
    binding and the Adapter. Parses the same private schema and applies the same
    capability/command-budget rules as capture, without reopening original files
    or dependency roots. Another Profile's successful capture is not permission
    to use its resources. None retains the no-resource path and returns None.

    Raises LocalResourceError for unsupported capabilities or invalid resources;
    native Profile validation errors remain ValueError. Grants no live authority.
    """
    _profile_resources(profile, used=False, commands=())
    if body is None:
        return None
    document = parse_local_resources(body)
    _profile_resources(profile, used=True, commands=document["commands"])
    _commands(document["commands"], files=document["material_files"], timeout=profile.timeout_seconds)
    return document


def assert_local_materials_unchanged(body: bytes, *, materials_root: Path) -> None:
    """Check the exact source-copy file/directory set, bytes and read-only state.

    Does not read original material_root or dependency installations. Other files
    in materials_root, such as task_input, remain owned by the existing Adapter.
    The host operation walks the copy without following links and requires the
    read-only state it set (POSIX modes 0444/0555 and 0555 directories; Windows
    read-only file attribute); execute bits are compared where the host has them.
    Drift rejects output; read-only state by itself is not an OS isolation
    guarantee. Missing, added, linked or changed tree members raise
    LocalResourceError with ADAPTER_POLICY_VIOLATION. Invalid control bytes
    retain ADAPTER_REQUEST_INVALID.
    """
    document = parse_local_resources(body)
    expected = {row["relative_path"]: (row["sha256"], row["executable"]) for row in document["material_files"]}
    expected_dirs = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != "."}
    try:
        actual, directories = HOST_PLATFORM.material_tree_state(Path(materials_root) / "source")
        observed = {name: (digest, expected[name][1] if executable is None and name in expected else executable)
                    for name, (digest, executable) in actual.items()}
        if observed != expected or directories != expected_dirs:
            raise LocalResourceError("material tree differs from the frozen files or modes")
    except (OSError, ValueError) as exc:
        raise LocalResourceError("frozen material tree changed or became unavailable",
                                 error_code="ADAPTER_POLICY_VIOLATION") from exc


def materialize_local_resources(body: bytes, *, materials_root: Path) -> None:
    """Create materials_root/source from captured bytes, never from the origin.

    materials_root must already exist. An existing nonempty source must match
    exactly; different content is not overwritten or cleaned up. Binary bytes
    are retained; the host operation makes the copy read-only (POSIX executable
    files 0555, others 0444, directories 0555; Windows read-only attribute).
    Writes only this source-copy tree. Invalid control bytes or existing drift
    raises LocalResourceError; native write errors remain OSError and may leave
    a partial owned tree. No source/dependency files are modified or commands run.
    """
    document = parse_local_resources(body)
    source = Path(materials_root) / "source"
    if source.is_symlink():
        raise LocalResourceError("material source copy path is a link")
    if source.exists():
        if any(source.iterdir()):
            assert_local_materials_unchanged(body, materials_root=materials_root)
            return
        source.rmdir()
    files = [(PurePosixPath(row["relative_path"]).parts, base64.b64decode(row["content_base64"], validate=True),
              row["executable"]) for row in document["material_files"]]
    _host_material(HOST_PLATFORM.write_material_tree, source, files)
    assert_local_materials_unchanged(body, materials_root=materials_root)


def stage_attempt_materials(*, request, host, profile, materials: Path, dependencies, set_stage) -> tuple[dict, bytes | None]:
    """Write one Attempt's authorized inputs and optional frozen resources under materials.

    Shared by the Claude and Codex workspace adapters. set_stage reports the
    current stage to the caller: authorized_input_read while the host reads an
    input, otherwise material_preparation. A symlinked or conflicting material
    raises AttemptWorkspaceConflictError whose message is the policy refusal
    reason. Returns the written material hashes and the resource package body,
    or None when the request carries no local resources.
    """
    from ..contracts.invocation_adapter_definition import SelfTestResourceUnavailableError
    from .invocation_workspace_preparation import AttemptWorkspaceConflictError
    material_hashes, resources_body = {}, None
    names = [item.logical_name for item in request.authorized_inputs]
    if len(names) != len(set(names)):
        raise ValueError("material names must be unique")
    for item in request.authorized_inputs:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", item.logical_name):
            raise ValueError("logical_name is not a safe material filename")
        target = materials / item.logical_name
        if target.is_symlink():
            raise AttemptWorkspaceConflictError("material file is a symlink")
        set_stage("authorized_input_read")
        body = host.read_authorized_input(item.local_handle)
        set_stage("material_preparation")
        if hashlib.sha256(body).hexdigest() != item.input_sha256:
            raise ValueError("authorized input hash mismatch")
        if item.schema_ref == LOCAL_RESOURCES_SCHEMA_REF:
            if (resources_body is not None or item.schema_sha256 != LOCAL_RESOURCES_SCHEMA_SHA256
                    or item.media_type != LOCAL_RESOURCES_MEDIA_TYPE or item.logical_name != LOCAL_RESOURCES_LOGICAL_NAME):
                raise ValueError("Local resource control input has invalid metadata")
            if request.self_test_binding_ref is None:
                raise PermissionError("Local resources require a live Runtime self-test binding")
            resource = validate_local_resources(profile=profile, body=body)
            if tuple(resource["read_only_dependencies"]) != tuple(map(str, dependencies)):
                raise SelfTestResourceUnavailableError("Adapter dependencies differ from the bound local resources")
            resources_body = body
            materialize_local_resources(body, materials_root=materials)
            continue
        if target.exists() and target.read_bytes() != body:
            raise AttemptWorkspaceConflictError("existing material content differs")
        if not target.exists():
            target.write_bytes(body)
        material_hashes[str(target)] = item.input_sha256
    return material_hashes, resources_body


INVOCATION_LOCATIONS_HEADING = "## Runtime Invocation Locations"
_TEMPORARY_VARIABLES = ("TMPDIR", "TEMP", "TMP")


def invocation_locations(*, working_directory, writable_directories=(), materials_directory=None,
                         read_only_dependencies=(), runtime_python=None) -> dict:
    """Return the locations one agent Attempt's adapter actually prepared, as plain JSON values.

    Shared by the Claude and Codex workspace adapters after they prepare the
    Attempt and before the CLI starts. Every value is passed in by the adapter
    from what it created or resolved for this call: the CLI working directory,
    the directories its configuration lets file tools write, the Attempt's
    read-only materials folder (task_input, plus source when a frozen material
    package exists), the selected read-only dependency roots and, for a
    Shell-capable call, the Runtime Python executable that leads PATH. Empty
    values are omitted. Temporary files are described by variable names only:
    the per-call folder itself can hold private control files. The origin
    material_root, the Attempt root and archive, CLI homes, connection
    configuration and credentials are never accepted here. Pure; no IO.
    """
    values = {"working_directory": str(working_directory)}
    if writable_directories:
        values["writable_directories"] = [str(path) for path in writable_directories]
    if materials_directory is not None:
        values["materials_directory"] = str(materials_directory)
    if read_only_dependencies:
        values["read_only_dependencies"] = [str(path) for path in read_only_dependencies]
    if runtime_python is not None:
        values["runtime_python"] = str(runtime_python)
    values["temporary_directory_variables"] = list(_TEMPORARY_VARIABLES)
    return values


def compose_invocation_prompt(locations: dict | None, envelope: str) -> str:
    """Return the exact CLI stdin: the location section, a blank line, then the unchanged envelope.

    locations None (a tool-free call) returns the envelope itself. The envelope
    text is never altered or reordered, so a prompt_only_json constraint stays
    last; its ref and hash keep identifying the frozen envelope only, not this
    composed text. Keys are rendered sorted, so the same values always give the
    same text and a recorded locations dictionary reproduces the stdin exactly.
    Adapters record the returned text as the Attempt trace's actual_prompt, which
    Inspection reads back. Pure; no IO.
    """
    if locations is None:
        return envelope
    return (INVOCATION_LOCATIONS_HEADING + "\n\n"
            "Runtime prepared these exact locations for this call. The materials directory is read-only and "
            "holds task_input and, when the task lists frozen source files, their source folder.\n\n"
            + json.dumps(locations, ensure_ascii=False, indent=2, sort_keys=True) + "\n\n" + envelope)


def describe_local_resources(*, profile, body: bytes | None) -> str:
    """Render the Claude, Codex or Antigravity workspace layout for exact request rebuilding.

    Claude v2/v3 and Codex workspace v3 keep their exact original texts for
    committed replay; they grant no execution capability. The v4 texts describe
    the actual layout of each workspace policy (private draft, explicit project,
    none); explicit command/callback extension calls keep the earlier scratch
    layout. Antigravity v1 file_review uses the Codex scratch-cwd text. Earlier
    bindings and Codex and Antigravity tool-free keep empty descriptions. Only
    relative paths appear, so the prompt hash does not vary per Attempt.
    Resource capture/admission and the Adapter still require the current revision.
    The private material_root/read_only_dependencies
    fields and base64 file bodies are not rendered. Exact command argv and
    logical cwd remain explicit task data, including any supplied program locator.
    """
    document = validate_local_resources(profile=profile, body=body)
    binding = (profile.executor_adapter_id, profile.executor_adapter_revision, profile.transport_kind)
    if binding in {("claude_cli_adapter", "v4", "claude_cli"), ("codex_cli_agent_workspace_executor", "v4", "codex_cli"), ("codex_cli_agent_workspace_executor", "v5", "codex_cli")}:
        return _describe_v4(profile, document, codex=binding[0] == "codex_cli_agent_workspace_executor")
    if binding == ("antigravity_cli_adapter", "v1", "antigravity_cli") and profile.execution_mode == "agent":
        # The same scratch-cwd layout as Codex: cwd is the private scratch, materials at ../materials.
        return _describe_v4(profile, document, codex=True)
    if (profile.executor_adapter_id, profile.executor_adapter_revision, profile.transport_kind) == (
            "codex_cli_agent_workspace_executor", "v3", "codex_cli"):
        sections = ["## Runtime Input Resources", "The task input is available at ../materials/task_input.",
                    "Your current working directory is the only location you may modify; write temporary files "
                    "in its .tmp directory, which is TMPDIR. Every other location is read-only or inaccessible."]
        if document is not None:
            sections.append("Frozen source files are under ../materials/source. Only the listed copies are task "
                            "materials; their contents are not inlined here.")
            sections.append(json.dumps([{key: row[key] for key in ("relative_path", "sha256", "executable")}
                                       for row in document["material_files"]], ensure_ascii=False, sort_keys=True))
            if document["commands"]:
                sections.append("Use the MCP tool sandbox_command_execute of the runtime_commands server with only "
                                "command_id to execute a listed command. The logical cwd selects source or scratch "
                                "(your working directory); command results are execution facts, not a business verdict.")
                sections.append(json.dumps(document["commands"], ensure_ascii=False, sort_keys=True))
        return "\n\n".join(sections)
    if (profile.executor_adapter_id, profile.executor_adapter_revision, profile.transport_kind) not in {
            ("claude_cli_adapter", "v2", "claude_cli"), ("claude_cli_adapter", "v3", "claude_cli")}:
        return ""
    draft = profile.attempt_workspace_policy == "own_draft_read_write"
    base = "../materials" if draft else "materials"
    sections = ["## Runtime Input Resources", f"The task input is available at {base}/task_input."]
    sections.append("Your writable working directory is scratch; use TMPDIR for temporary files."
                    if draft else "No model-writable task workspace is authorized.")
    if document is not None:
        sections.append(f"Frozen source files are under {base}/source. Only the listed copies are task materials; their contents are not inlined here.")
        sections.append(json.dumps([{key: row[key] for key in ("relative_path", "sha256", "executable")}
                                   for row in document["material_files"]], ensure_ascii=False, sort_keys=True))
        if document["commands"]:
            sections.append("Use the Runtime MCP tool mcp__runtime_commands__sandbox_command_execute with only command_id to execute a listed command. The logical cwd selects source or scratch; command results are execution facts, not a business verdict.")
            sections.append(json.dumps(document["commands"], ensure_ascii=False, sort_keys=True))
    return "\n\n".join(sections)


def _describe_v4(profile, document, *, codex: bool) -> str:
    from .invocation_tool_definition import callback_tool_names
    policy = profile.attempt_workspace_policy
    commands = document is not None and bool(document["commands"])
    extension = commands or bool(callback_tool_names(profile.tool_policy))
    listing = None if document is None else json.dumps(
        [{key: row[key] for key in ("relative_path", "sha256", "executable")} for row in document["material_files"]],
        ensure_ascii=False, sort_keys=True)
    sections = ["## Runtime Input Resources"]
    if policy == "explicit_project_read_write":
        sections.append("Your working directory is the project directory provided for this task; your changes there "
                        "are the delivery. The task input is included in this prompt and also in the read-only "
                        "materials directory provided for this call.")
        source = "the source folder of that materials directory"
    elif policy == "own_draft_read_write" and (codex or extension):
        sections += ["The task input is available at ../materials/task_input.",
                     "Your current working directory is your writable scratch area; use TMPDIR for temporary files. "
                     "Other locations follow the CLI's own permissions."]
        source = "../materials/source"
    elif policy == "own_draft_read_write":
        sections += ["The task input is available at materials/task_input.",
                     "Your working directory contains materials (read-only) and scratch, the only place you may "
                     "write; use TMPDIR for temporary files."]
        source = "materials/source"
    else:
        sections += ["The task input is available at materials/task_input.",
                     "No model-writable task workspace is authorized."]
        source = "materials/source"
    if listing is not None:
        sections.append(f"Frozen source files are under {source}. Only the listed copies are task materials; "
                        "their contents are not inlined here.")
        sections.append(listing)
        if commands:
            tool = ("the MCP tool sandbox_command_execute of the runtime_commands server" if codex
                    else "the Runtime MCP tool mcp__runtime_commands__sandbox_command_execute")
            sections.append(f"Use {tool} with only command_id to execute a listed command. The logical cwd selects "
                            "source or scratch; command results are execution facts, not a business verdict.")
            sections.append(json.dumps(document["commands"], ensure_ascii=False, sort_keys=True))
    return "\n\n".join(sections)


__all__ = ["LOCAL_RESOURCES_SCHEMA_REF", "LOCAL_RESOURCES_SCHEMA_SHA256", "LOCAL_RESOURCES_MEDIA_TYPE",
           "LOCAL_RESOURCES_LOGICAL_NAME", "capture_local_resources", "parse_local_resources",
           "prepare_material_files", "write_local_resources",
           "validate_local_resources", "materialize_local_resources", "assert_local_materials_unchanged",
           "describe_local_resources", "stage_attempt_materials", "LocalResourceError",
           "INVOCATION_LOCATIONS_HEADING", "invocation_locations", "compose_invocation_prompt"]
