"""Load fixed Git authoring sources for Agent Runtime Module registration.

The working tree is never a production execution authority.  This reader is
used only by registration and release tooling to load one closed, reviewable
Module source before its validated values, prompt bytes, and source hashes are
compiled into immutable Runtime releases.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from ..foundation.foundation_contract_validation import validate_snake_case_name
from ..contracts.registry_release_definition import (
    ModuleEntryPolicy,
    OutputResolutionPolicy,
)


MODULE_REGISTRATION_DIRECTORY = "runtime_modules"
MODULE_REGISTRATION_FILENAME = "module_registration.json"
MODULE_PROMPT_FILENAME = "prompt.md"
SKILL_PROJECTION_FILENAME = "SKILL.md"
MODULE_REGISTRATION_SCHEMA_VERSION = "runtime_module_registration_v2"
MODULE_REGISTRATION_HOST_ROOT = PurePosixPath(".claude", "skills")

_SKILL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_ROOT_ENTRY_NAMES = frozenset(
    {MODULE_REGISTRATION_FILENAME, MODULE_PROMPT_FILENAME, "schemas", "tests"}
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "skill_id",
        "module_id",
        "owner_contract_path",
        "input_schema_ref",
        "input_schema_path",
        "output_schema_ref",
        "output_schema_path",
        "declared_operation_ids",
        "compatible_transport_kinds",
        "context_policy_ref",
        "evaluation_policy_ref",
        "retry_policy_ref",
        "entry_policy",
        "output_resolution_policy",
    }
)


def _repo_path(label: str, value: Any) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{label} must be a non-empty repository-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError(f"{label} must stay inside the repository")
    if path.as_posix() != value:
        raise ValueError(f"{label} must use normalized POSIX syntax")
    return value


def _reject_symlink_path(
    project_root: Path,
    relative_path: PurePosixPath,
    *,
    label: str,
) -> Path:
    """Return one logical repo path only when no child component is a symlink."""

    candidate = project_root
    for component in relative_path.parts:
        candidate = candidate / component
        if candidate.is_symlink():
            raise ValueError(f"{label} cannot traverse a symlink: {candidate}")
    return candidate


def _checked_repo_entry(
    project_root: Path,
    relative_path: str | PurePosixPath,
    *,
    label: str,
    expected_kind: str,
) -> Path:
    """Resolve one required symlink-free repository file or directory."""

    logical_path = PurePosixPath(relative_path)
    candidate = _reject_symlink_path(
        project_root,
        logical_path,
        label=label,
    )
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise ValueError(f"{label} is missing: {candidate}") from exc
    if not resolved.is_relative_to(project_root.resolve()):
        raise ValueError(f"{label} escapes the repository: {candidate}")
    if expected_kind == "file" and not candidate.is_file():
        raise ValueError(f"{label} must be a file: {candidate}")
    if expected_kind == "directory" and not candidate.is_dir():
        raise ValueError(f"{label} must be a directory: {candidate}")
    return candidate


def _module_schema_path(
    label: str,
    value: Any,
    *,
    relative_directory: PurePosixPath,
) -> str:
    """Resolve one portable Module-relative schema path for compilation."""

    relative_path = PurePosixPath(_repo_path(label, value))
    if not relative_path.parts or relative_path.parts[0] != "schemas":
        raise ValueError(f"{label} must stay inside the Module schemas directory")
    return (relative_directory / relative_path).as_posix()


def _string(label: str, value: Any) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _sorted_unique_strings(label: str, value: Any) -> tuple[str, ...]:
    if type(value) is not list or not value:
        raise ValueError(f"{label} must be a non-empty JSON array")
    if any(type(item) is not str or not item for item in value):
        raise ValueError(f"{label} must contain non-empty strings")
    values = tuple(value)
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must not contain duplicates")
    if values != tuple(sorted(values)):
        raise ValueError(f"{label} must be sorted")
    return values


def _read_utf8_text(path: Path, *, label: str) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc
    if not text.strip():
        raise ValueError(f"{label} must be non-empty")
    if "\x00" in text:
        raise ValueError(f"{label} contains a null byte")
    return text


def _declares_identifier(text: str, identifier: str) -> bool:
    """Return whether prose declares one exact snake_case identifier token."""

    return re.search(
        rf"(?<![a-z0-9_]){re.escape(identifier)}(?![a-z0-9_])",
        text,
    ) is not None


def _validate_module_owned_schema_closure(
    *,
    project_root: Path,
    relative_directory: PurePosixPath,
    directory: Path,
    schema_paths: tuple[str, str],
) -> None:
    """Reject undeclared or misplaced schema files in one Module directory."""

    declared_owned_paths: set[str] = set()
    for schema_path in schema_paths:
        pure_schema_path = PurePosixPath(schema_path)
        try:
            module_relative = pure_schema_path.relative_to(relative_directory)
        except ValueError:
            continue
        if not module_relative.parts or module_relative.parts[0] != "schemas":
            raise ValueError(
                "Module-owned schema path must stay inside the Module schemas directory"
            )
        declared_owned_paths.add(schema_path)

    schema_root = directory / "schemas"
    if not schema_root.exists():
        if declared_owned_paths:
            raise ValueError("Module-owned schemas directory is missing")
        return
    if not schema_root.is_dir() or schema_root.is_symlink():
        raise ValueError("Runtime Module schemas entry must be a real directory")

    actual_owned_paths: set[str] = set()
    for entry in schema_root.rglob("*"):
        if entry.is_symlink() or not entry.is_file():
            raise ValueError(
                "Runtime Module schemas directory may contain only regular files"
            )
        actual_owned_paths.add(entry.relative_to(project_root).as_posix())
    if actual_owned_paths != declared_owned_paths:
        missing = sorted(declared_owned_paths - actual_owned_paths)
        extra = sorted(actual_owned_paths - declared_owned_paths)
        raise ValueError(
            "Runtime Module schema closure differs from registration: "
            f"missing={missing}, extra={extra}"
        )


@dataclass(frozen=True)
class RuntimeModuleRegistrationSource:
    """One closed Module registration source loaded from a Skill."""

    skill_id: str
    module_id: str
    owner_contract_path: str
    input_schema_ref: str
    input_schema_path: str
    output_schema_ref: str
    output_schema_path: str
    declared_operation_ids: tuple[str, ...]
    compatible_transport_kinds: tuple[str, ...]
    context_policy_ref: str
    evaluation_policy_ref: str
    retry_policy_ref: str
    entry_policy: ModuleEntryPolicy
    output_resolution_policy: OutputResolutionPolicy
    module_directory_path: str
    registration_path: str
    prompt_path: str
    prompt_text: str

    @property
    def owner_contract_ref(self) -> str:
        """Return the repository ref for the Module owner contract."""

        return f"repo-file:{self.owner_contract_path}"


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Module registration must be valid UTF-8 JSON: {path}"
        ) from exc
    if type(payload) is not dict:
        raise ValueError("Module registration must be one JSON object")
    if payload.get("schema_version") != MODULE_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("unsupported Runtime Module registration schema_version")
    if set(payload) != _MANIFEST_KEYS:
        missing = sorted(_MANIFEST_KEYS - set(payload))
        extra = sorted(set(payload) - _MANIFEST_KEYS)
        raise ValueError(
            "Module registration has an invalid shape: "
            f"missing={missing}, extra={extra}"
        )
    return payload


def load_runtime_module_registration(
    project_root: Path,
    *,
    skill_id: str,
    module_id: str,
) -> RuntimeModuleRegistrationSource:
    """Load one Module only from the fixed Skill authoring channel."""

    if type(skill_id) is not str or not _SKILL_ID_PATTERN.fullmatch(skill_id):
        raise ValueError("skill_id must use canonical kebab-case")
    validate_snake_case_name("module_id", module_id)
    relative_directory = (
        MODULE_REGISTRATION_HOST_ROOT
        / skill_id
        / MODULE_REGISTRATION_DIRECTORY
        / module_id
    )
    directory = _checked_repo_entry(
        project_root,
        relative_directory,
        label="Runtime Module registration directory",
        expected_kind="directory",
    )

    root_entries = tuple(directory.iterdir())
    symlink_entries = sorted(
        entry.name for entry in root_entries if entry.is_symlink()
    )
    if symlink_entries:
        raise ValueError(
            "Runtime Module registration root cannot contain symlinks: "
            f"{symlink_entries}"
        )
    entry_names = {entry.name for entry in root_entries}
    unexpected = sorted(entry_names - _ROOT_ENTRY_NAMES)
    if unexpected:
        raise ValueError(
            f"Runtime Module registration has undeclared root entries: {unexpected}"
        )

    registration_path = _checked_repo_entry(
        project_root,
        relative_directory / MODULE_REGISTRATION_FILENAME,
        label="Runtime Module registration",
        expected_kind="file",
    )
    prompt_path = _checked_repo_entry(
        project_root,
        relative_directory / MODULE_PROMPT_FILENAME,
        label="Runtime Module prompt",
        expected_kind="file",
    )

    payload = _load_json_object(registration_path)
    if payload["skill_id"] != skill_id:
        raise ValueError("registration skill_id differs from its Skill directory")
    if payload["module_id"] != module_id:
        raise ValueError("registration module_id differs from its Module directory")
    owner_contract_path = _repo_path(
        "owner_contract_path", payload["owner_contract_path"]
    )
    input_schema_path = _module_schema_path(
        "input_schema_path",
        payload["input_schema_path"],
        relative_directory=relative_directory,
    )
    output_schema_path = _module_schema_path(
        "output_schema_path",
        payload["output_schema_path"],
        relative_directory=relative_directory,
    )
    checked_files = {
        label: _checked_repo_entry(
            project_root,
            relative_path,
            label=label,
            expected_kind="file",
        )
        for label, relative_path in (
            ("owner_contract_path", owner_contract_path),
            ("input_schema_path", input_schema_path),
            ("output_schema_path", output_schema_path),
        )
    }
    _validate_module_owned_schema_closure(
        project_root=project_root,
        relative_directory=relative_directory,
        directory=directory,
        schema_paths=(input_schema_path, output_schema_path),
    )

    skill_path = _checked_repo_entry(
        project_root,
        MODULE_REGISTRATION_HOST_ROOT / skill_id / SKILL_PROJECTION_FILENAME,
        label="Skill registration projection",
        expected_kind="file",
    )
    skill_text = _read_utf8_text(skill_path, label="Skill registration projection")
    if not _declares_identifier(skill_text, module_id):
        raise ValueError(
            "Skill registration projection does not declare exact module_id: "
            f"{module_id}"
        )

    module_owner_path = checked_files["owner_contract_path"]
    module_owner_text = _read_utf8_text(
        module_owner_path,
        label="Module owner Design Doc",
    )
    if not _declares_identifier(module_owner_text, module_id):
        raise ValueError(
            "Module owner Design Doc does not declare exact module_id: "
            f"{module_id}"
        )

    prompt_text = _read_utf8_text(prompt_path, label="Runtime Module prompt")
    if not prompt_text.endswith("\n"):
        raise ValueError("Runtime Module prompt must end with one newline")
    if prompt_text.startswith("---\n"):
        raise ValueError("Runtime Module prompt must not contain frontmatter")
    if "\x00" in prompt_text:
        raise ValueError("Runtime Module prompt contains a null byte")

    try:
        entry_policy = ModuleEntryPolicy(payload["entry_policy"])
        output_resolution_policy = OutputResolutionPolicy(
            payload["output_resolution_policy"]
        )
    except ValueError as exc:
        raise ValueError("Module registration contains an invalid policy") from exc

    return RuntimeModuleRegistrationSource(
        skill_id=skill_id,
        module_id=module_id,
        owner_contract_path=owner_contract_path,
        input_schema_ref=_string("input_schema_ref", payload["input_schema_ref"]),
        input_schema_path=input_schema_path,
        output_schema_ref=_string(
            "output_schema_ref", payload["output_schema_ref"]
        ),
        output_schema_path=output_schema_path,
        declared_operation_ids=_sorted_unique_strings(
            "declared_operation_ids", payload["declared_operation_ids"]
        ),
        compatible_transport_kinds=_sorted_unique_strings(
            "compatible_transport_kinds",
            payload["compatible_transport_kinds"],
        ),
        context_policy_ref=_string(
            "context_policy_ref", payload["context_policy_ref"]
        ),
        evaluation_policy_ref=_string(
            "evaluation_policy_ref", payload["evaluation_policy_ref"]
        ),
        retry_policy_ref=_string(
            "retry_policy_ref", payload["retry_policy_ref"]
        ),
        entry_policy=entry_policy,
        output_resolution_policy=output_resolution_policy,
        module_directory_path=relative_directory.as_posix(),
        registration_path=(
            relative_directory / MODULE_REGISTRATION_FILENAME
        ).as_posix(),
        prompt_path=(relative_directory / MODULE_PROMPT_FILENAME).as_posix(),
        prompt_text=prompt_text,
    )


def load_skill_runtime_module_registrations(
    project_root: Path,
    *,
    skill_id: str,
) -> tuple[RuntimeModuleRegistrationSource, ...]:
    """Load every Module registration source in one Skill in stable order."""

    if type(skill_id) is not str or not _SKILL_ID_PATTERN.fullmatch(skill_id):
        raise ValueError("invalid skill_id")
    root = _checked_repo_entry(
        project_root,
        MODULE_REGISTRATION_HOST_ROOT
        / skill_id
        / MODULE_REGISTRATION_DIRECTORY,
        label="Skill Runtime Module registration directory",
        expected_kind="directory",
    )
    module_entries = tuple(root.iterdir())
    if any(entry.is_symlink() for entry in module_entries):
        raise ValueError(
            "runtime_modules cannot contain symlinked Module directories"
        )
    module_ids = tuple(sorted(entry.name for entry in module_entries))
    if not module_ids or any(
        not (root / module_id).is_dir() for module_id in module_ids
    ):
        raise ValueError("runtime_modules may contain only Module directories")
    return tuple(
        load_runtime_module_registration(
            project_root,
            skill_id=skill_id,
            module_id=module_id,
        )
        for module_id in module_ids
    )


def load_project_runtime_module_registrations(
    project_root: Path,
) -> tuple[RuntimeModuleRegistrationSource, ...]:
    """Load every fixed Runtime Module registration in one repository."""

    skill_root = _reject_symlink_path(
        project_root,
        MODULE_REGISTRATION_HOST_ROOT,
        label="Skill registration host root",
    )
    if not skill_root.exists():
        return ()
    skill_root = _checked_repo_entry(
        project_root,
        MODULE_REGISTRATION_HOST_ROOT,
        label="Skill registration host root",
        expected_kind="directory",
    )
    skill_entries = tuple(skill_root.iterdir())
    if any(entry.is_symlink() for entry in skill_entries):
        raise ValueError("Skill registration host root cannot contain symlinks")
    skill_ids = tuple(
        sorted(
            entry.name
            for entry in skill_entries
            if entry.is_dir() and (entry / MODULE_REGISTRATION_DIRECTORY).exists()
        )
    )
    registrations: list[RuntimeModuleRegistrationSource] = []
    for skill_id in skill_ids:
        registrations.extend(
            load_skill_runtime_module_registrations(
                project_root,
                skill_id=skill_id,
            )
        )
    return tuple(registrations)


__all__ = [
    "MODULE_REGISTRATION_HOST_ROOT",
    "MODULE_REGISTRATION_DIRECTORY",
    "MODULE_PROMPT_FILENAME",
    "MODULE_REGISTRATION_FILENAME",
    "MODULE_REGISTRATION_SCHEMA_VERSION",
    "SKILL_PROJECTION_FILENAME",
    "RuntimeModuleRegistrationSource",
    "load_project_runtime_module_registrations",
    "load_runtime_module_registration",
    "load_skill_runtime_module_registrations",
]
