"""Fixed Git authoring source for Agent Runtime Module registration.

The working tree is never a production execution authority.  This reader is
used only by registration and release tooling to load one closed, reviewable
Module export before its validated values, prompt bytes, and source hashes are
compiled into immutable Runtime releases.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from ..contracts.registry_contract_validation import validate_snake_case_name
from ..contracts.registry_release_definition import (
    ModuleEntryPolicy,
    OutputResolutionPolicy,
)


MODULE_EXPORT_DIRECTORY = "runtime_modules"
MODULE_REGISTRATION_FILENAME = "module_registration.json"
MODULE_PROMPT_FILENAME = "prompt.md"
MODULE_REGISTRATION_SCHEMA_VERSION = "runtime_module_registration_v1"
CANONICAL_SKILL_PACKAGE_ROOT = PurePosixPath(".claude", "skills")

_SKILL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_ROOT_ENTRY_NAMES = frozenset(
    {MODULE_REGISTRATION_FILENAME, MODULE_PROMPT_FILENAME, "tests"}
)
_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "skill_package_id",
        "skill_id",
        "export_id",
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


@dataclass(frozen=True)
class RuntimeModuleExportSource:
    """One closed Module registration source loaded from a Skill Package."""

    skill_package_id: str
    skill_id: str
    export_id: str
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
        """Return the repository release reference for the owning contract."""

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
    if set(payload) != _MANIFEST_KEYS:
        missing = sorted(_MANIFEST_KEYS - set(payload))
        extra = sorted(set(payload) - _MANIFEST_KEYS)
        raise ValueError(
            "Module registration has an invalid shape: "
            f"missing={missing}, extra={extra}"
        )
    return payload


def load_runtime_module_export(
    project_root: Path,
    *,
    skill_id: str,
    module_id: str,
) -> RuntimeModuleExportSource:
    """Load one Module only from the fixed Skill Package read channel."""

    if type(skill_id) is not str or not _SKILL_ID_PATTERN.fullmatch(skill_id):
        raise ValueError("skill_id must use canonical kebab-case")
    validate_snake_case_name("module_id", module_id)
    relative_directory = (
        CANONICAL_SKILL_PACKAGE_ROOT
        / skill_id
        / MODULE_EXPORT_DIRECTORY
        / module_id
    )
    directory = project_root / relative_directory
    if not directory.is_dir():
        raise ValueError(f"Runtime Module export directory is missing: {directory}")

    entry_names = {entry.name for entry in directory.iterdir()}
    unexpected = sorted(entry_names - _ROOT_ENTRY_NAMES)
    if unexpected:
        raise ValueError(
            f"Runtime Module export has undeclared root entries: {unexpected}"
        )

    registration_path = directory / MODULE_REGISTRATION_FILENAME
    prompt_path = directory / MODULE_PROMPT_FILENAME
    if not registration_path.is_file():
        raise ValueError(
            f"Runtime Module registration is missing: {registration_path}"
        )
    if not prompt_path.is_file():
        raise ValueError(f"Runtime Module prompt is missing: {prompt_path}")

    payload = _load_json_object(registration_path)
    if payload["schema_version"] != MODULE_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("unsupported Runtime Module registration schema_version")
    if payload["skill_id"] != skill_id:
        raise ValueError("registration skill_id differs from its Skill directory")
    if payload["module_id"] != module_id:
        raise ValueError("registration module_id differs from its Module directory")
    if payload["export_id"] != module_id:
        raise ValueError("registration export_id must equal module_id")

    skill_package_id = _string("skill_package_id", payload["skill_package_id"])
    validate_snake_case_name("skill_package_id", skill_package_id)
    validate_snake_case_name("export_id", payload["export_id"])

    owner_contract_path = _repo_path(
        "owner_contract_path", payload["owner_contract_path"]
    )
    input_schema_path = _repo_path(
        "input_schema_path", payload["input_schema_path"]
    )
    output_schema_path = _repo_path(
        "output_schema_path", payload["output_schema_path"]
    )
    for label, relative_path in (
        ("owner_contract_path", owner_contract_path),
        ("input_schema_path", input_schema_path),
        ("output_schema_path", output_schema_path),
    ):
        if not (project_root / relative_path).is_file():
            raise ValueError(f"{label} does not resolve to a repository file")

    try:
        prompt_text = prompt_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("Runtime Module prompt must be UTF-8") from exc
    if not prompt_text.strip():
        raise ValueError("Runtime Module prompt must be non-empty")
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

    return RuntimeModuleExportSource(
        skill_package_id=skill_package_id,
        skill_id=skill_id,
        export_id=module_id,
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


def load_skill_runtime_module_exports(
    project_root: Path,
    *,
    skill_id: str,
) -> tuple[RuntimeModuleExportSource, ...]:
    """Load every Module export in one Skill Package in stable order."""

    if type(skill_id) is not str or not _SKILL_ID_PATTERN.fullmatch(skill_id):
        raise ValueError("invalid skill_id")
    root = (
        project_root
        / CANONICAL_SKILL_PACKAGE_ROOT
        / skill_id
        / MODULE_EXPORT_DIRECTORY
    )
    if not root.is_dir():
        raise ValueError(f"Skill has no Runtime Module export directory: {root}")
    module_ids = tuple(sorted(entry.name for entry in root.iterdir()))
    if not module_ids or any(not (root / module_id).is_dir() for module_id in module_ids):
        raise ValueError("runtime_modules may contain only Module directories")
    exports = tuple(
        load_runtime_module_export(
            project_root,
            skill_id=skill_id,
            module_id=module_id,
        )
        for module_id in module_ids
    )
    package_ids = {export.skill_package_id for export in exports}
    owner_paths = {export.owner_contract_path for export in exports}
    if len(package_ids) != 1:
        raise ValueError("one Skill Package must declare one skill_package_id")
    if len(owner_paths) != 1:
        raise ValueError("one Skill Package must declare one owner contract")
    return exports


__all__ = [
    "CANONICAL_SKILL_PACKAGE_ROOT",
    "MODULE_EXPORT_DIRECTORY",
    "MODULE_PROMPT_FILENAME",
    "MODULE_REGISTRATION_FILENAME",
    "MODULE_REGISTRATION_SCHEMA_VERSION",
    "RuntimeModuleExportSource",
    "load_runtime_module_export",
    "load_skill_runtime_module_exports",
]
