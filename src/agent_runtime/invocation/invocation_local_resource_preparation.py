"""Capture invocation-local files and command descriptions without granting effects."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys

from ..contracts.registry_release_definition import ExecutionProfileRelease
from ..foundation.foundation_contract_validation import validate_sha256


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


def _profile_resources(profile, *, used, commands):
    if type(profile) is not ExecutionProfileRelease:
        raise LocalResourceError("profile must be an exact ExecutionProfileRelease")
    profile.validate()
    if not used:
        return
    if (sys.platform != "darwin" or profile.executor_adapter_id != "claude_cli_adapter"
            or profile.executor_adapter_revision != "v3" or profile.transport_kind != "claude_cli"
            or profile.execution_mode != "agent" or profile.semantic_input_delivery_mode != "inline"
            or profile.network_policy != "denied" or profile.gateway_access_reasons
            or not set(profile.tool_policy) & {"read", "search", "shell"}):
        raise LocalResourceError("local files/dependencies/commands require the supported macOS Claude v3 agent resources",
                                 error_code="ADAPTER_CAPABILITY_UNSUPPORTED")
    if commands and ("shell" not in profile.tool_policy or profile.attempt_workspace_policy != "own_draft_read_write"):
        raise LocalResourceError("local commands require shell and private draft workspace",
                                 error_code="ADAPTER_CAPABILITY_UNSUPPORTED")


@contextmanager
def _directory(path: Path):
    """Open each path component without following a symlink."""
    path = Path(path).absolute()
    if ".." in path.parts:
        raise LocalResourceError("directory locator cannot contain parent traversal")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def _read_file(directory, parts):
    descriptor = os.dup(directory)
    try:
        for name in parts[:-1]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise LocalResourceError("materials must be regular files")
            body = stream.read()
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns, before.st_mode) != (after.st_size, after.st_mtime_ns, after.st_mode):
                raise LocalResourceError("material changed while being captured")
            return body, stat.S_IMODE(after.st_mode)
    finally:
        os.close(descriptor)


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
        with _directory(path) as root_fd:
            for item in material_files:
                content, mode = _read_file(root_fd, _relative(item["relative_path"]))
                if hashlib.sha256(content).hexdigest() != item["sha256"] or bool(mode & 0o111) != item["executable"]:
                    raise LocalResourceError("source bytes or executable mode differ from the material manifest")
                captured.append({**item, "content_base64": base64.b64encode(content).decode("ascii")})
        origin = str(path)
    document = {"schema_version": _VERSION, "material_root": origin, "material_files": captured,
                "read_only_dependencies": roots, "commands": list(commands)}
    body = _canonical(document)
    validate_local_resources(profile=profile, body=body)
    return body


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


def _tree(directory, prefix=""):
    files, directories = {}, set()
    for name in os.listdir(directory):
        relative = prefix + name
        mode = os.stat(name, dir_fd=directory, follow_symlinks=False).st_mode
        if stat.S_ISDIR(mode):
            descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            try:
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o555:
                    raise LocalResourceError("material directory permissions changed")
                children, subdirs = _tree(descriptor, relative + "/")
                files.update(children)
                directories.update(subdirs)
                directories.add(relative)
            finally:
                os.close(descriptor)
        elif stat.S_ISREG(mode):
            content, file_mode = _read_file(directory, (name,))
            files[relative] = (hashlib.sha256(content).hexdigest(), file_mode)
        else:
            raise LocalResourceError("material tree contains a symlink or special file")
    return files, directories


def assert_local_materials_unchanged(body: bytes, *, materials_root: Path) -> None:
    """Check the exact source-copy file/directory set, bytes and normalized modes.

    Does not read original material_root or dependency installations. Other files
    in materials_root, such as task_input, remain owned by the existing Adapter.
    Drift rejects output; chmod by itself is not a sandbox enforcement guarantee.
    Missing, added, linked or changed tree members raise LocalResourceError with
    ADAPTER_POLICY_VIOLATION. Invalid control bytes retain ADAPTER_REQUEST_INVALID.
    """
    document = parse_local_resources(body)
    expected = {row["relative_path"]: (row["sha256"], 0o555 if row["executable"] else 0o444)
                for row in document["material_files"]}
    expected_dirs = {str(parent) for name in expected for parent in PurePosixPath(name).parents if str(parent) != "."}
    try:
        with _directory(Path(materials_root) / "source") as directory:
            if stat.S_IMODE(os.fstat(directory).st_mode) != 0o555:
                raise LocalResourceError("material source directory permissions changed")
            actual, directories = _tree(directory)
        if actual != expected or directories != expected_dirs:
            raise LocalResourceError("material tree differs from the frozen files or modes")
    except (OSError, LocalResourceError) as exc:
        raise LocalResourceError("frozen material tree changed or became unavailable",
                                 error_code="ADAPTER_POLICY_VIOLATION") from exc


def materialize_local_resources(body: bytes, *, materials_root: Path) -> None:
    """Create materials_root/source from captured bytes, never from the origin.

    materials_root must already exist. An existing nonempty source must match
    exactly; different content is not overwritten or cleaned up. Binary bytes
    are retained and executable files use 0555, other files 0444, directories 0555.
    Writes only this source-copy tree. Invalid control bytes or existing drift
    raises LocalResourceError; native write errors remain OSError and may leave
    a partial owned tree. No source/dependency files are modified or commands run.
    """
    document = parse_local_resources(body)
    source = Path(materials_root) / "source"
    with _directory(Path(materials_root)) as parent:
        try:
            os.mkdir("source", mode=0o700, dir_fd=parent)
        except FileExistsError:
            with _directory(source) as existing:
                if os.listdir(existing):
                    assert_local_materials_unchanged(body, materials_root=materials_root)
                    return
        # All child directories are constructed before read-only modes are set.
        with _directory(source) as directory:
            paths = sorted({str(parent) for row in document["material_files"]
                for parent in PurePosixPath(row["relative_path"]).parents if str(parent) != "."}, key=lambda p: (p.count("/"), p))
            for relative in paths:
                with _directory(source / PurePosixPath(relative).parent) as folder:
                    os.mkdir(PurePosixPath(relative).name, mode=0o700, dir_fd=folder)
            for row in document["material_files"]:
                path = PurePosixPath(row["relative_path"])
                with _directory(source / path.parent) as folder:
                    fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=folder)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(base64.b64decode(row["content_base64"], validate=True))
                        os.fchmod(stream.fileno(), 0o555 if row["executable"] else 0o444)
            for relative in reversed(paths):
                with _directory(source / relative) as folder:
                    os.fchmod(folder, 0o555)
            os.fchmod(directory, 0o555)
    assert_local_materials_unchanged(body, materials_root=materials_root)


def describe_local_resources(*, profile, body: bytes | None) -> str:
    """Render the current and historical Claude layout for exact request rebuilding.

    v2's original description remains required for committed replay; it grants
    no execution capability. Earlier bindings and Codex retain empty descriptions.
    Resource capture/admission and the Adapter still require the current revision.
    The private material_root/read_only_dependencies
    fields and base64 file bodies are not rendered. Exact command argv and
    logical cwd remain explicit task data, including any supplied program locator.
    """
    document = validate_local_resources(profile=profile, body=body)
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


__all__ = ["LOCAL_RESOURCES_SCHEMA_REF", "LOCAL_RESOURCES_SCHEMA_SHA256", "LOCAL_RESOURCES_MEDIA_TYPE",
           "LOCAL_RESOURCES_LOGICAL_NAME", "capture_local_resources", "parse_local_resources",
           "validate_local_resources", "materialize_local_resources", "assert_local_materials_unchanged",
           "describe_local_resources", "LocalResourceError"]
