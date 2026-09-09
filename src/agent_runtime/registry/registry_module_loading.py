"""Load one exact host Module registration for Runtime authoring."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any

from ..contracts.registry_release_definition import (
    ModuleEntryPolicy,
    OutputResolutionPolicy,
)


MODULE_REGISTRATION_SCHEMA_VERSION = "runtime_module_registration_v2"
MODULE_REGISTRATION_FILENAME = "module_registration.json"
MODULE_PROMPT_FILENAME = "prompt.md"
MODULE_INPUT_SCHEMA_PATH = PurePosixPath("schemas/input.schema.json")
MODULE_OUTPUT_SCHEMA_PATH = PurePosixPath("schemas/output.schema.json")
MODULE_AUTHORING_ROOT = PurePosixPath(".claude/skills")

_SKILL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_MODULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
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
        "behavior_policy_ref",
        "evaluation_policy_ref",
        "retry_policy_ref",
        "entry_policy",
        "output_resolution_policy",
    }
)


def _non_empty_string(label: str, value: Any) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _sorted_unique_strings(label: str, value: Any) -> tuple[str, ...]:
    if type(value) is not list or not value:
        raise ValueError(f"{label} must be a non-empty JSON array")
    if any(type(item) is not str or not item for item in value):
        raise ValueError(f"{label} must contain non-empty strings")
    result = tuple(value)
    if result != tuple(sorted(set(result))):
        raise ValueError(f"{label} must be sorted and unique")
    return result


def _repository_path(label: str, value: Any) -> PurePosixPath:
    text = _non_empty_string(label, value)
    path = PurePosixPath(text)
    if path.is_absolute() or "." in path.parts or ".." in path.parts:
        raise ValueError(f"{label} must stay inside the repository")
    if path.as_posix() != text:
        raise ValueError(f"{label} must use normalized POSIX syntax")
    return path


def _checked_entry(
    project_root: Path,
    relative_path: PurePosixPath,
    *,
    label: str,
    expected_kind: str,
) -> Path:
    candidate = project_root
    for component in relative_path.parts:
        candidate = candidate / component
        if candidate.is_symlink():
            raise ValueError(f"{label} cannot traverse a symlink: {candidate}")
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


def _read_utf8(path: Path, *, label: str) -> str:
    try:
        value = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{label} must be UTF-8") from exc
    if not value.strip() or "\x00" in value:
        raise ValueError(f"{label} must be non-empty UTF-8 text")
    return value


def _declares_identifier(text: str, identifier: str) -> bool:
    return re.search(
        rf"(?<![a-z0-9_]){re.escape(identifier)}(?![a-z0-9_])",
        text,
    ) is not None


def _module_schema_ref(
    label: str,
    value: Any,
    *,
    module_id: str,
    direction: str,
) -> str:
    schema_ref = _non_empty_string(label, value)
    schema_name, separator, version = schema_ref.removeprefix("schema:").rpartition(
        "@"
    )
    if (
        not schema_ref.startswith("schema:")
        or not separator
        or schema_name != f"{module_id}_{direction}"
        or not version
    ):
        raise ValueError(
            f"{label} must use schema:{module_id}_{direction}@<version>"
        )
    return schema_ref


@dataclass(frozen=True)
class ModuleRegistrationSource:
    """Exact path-free content loaded from one Module registration."""

    skill_id: str
    module_id: str
    owner_contract_ref: str
    owner_contract_content: str
    input_schema_ref: str
    input_schema_document: str
    output_schema_ref: str
    output_schema_document: str
    instruction_text: str
    declared_operation_ids: tuple[str, ...]
    compatible_transport_kinds: tuple[str, ...]
    behavior_policy_ref: str
    evaluation_policy_ref: str
    retry_policy_ref: str
    entry_policy: ModuleEntryPolicy
    output_resolution_policy: OutputResolutionPolicy

    @property
    def instruction_source_ref(self) -> str:
        return f"skill-instruction:{self.skill_id}:{self.module_id}"


def load_module_registration(
    project_root: Path,
    *,
    skill_id: str,
    module_id: str,
) -> ModuleRegistrationSource:
    """Read one fixed `.claude/skills/<skill>/runtime_modules/<module>` source."""

    project_root = project_root.resolve()
    if type(skill_id) is not str or _SKILL_ID_PATTERN.fullmatch(skill_id) is None:
        raise ValueError("skill_id must use canonical kebab-case")
    if type(module_id) is not str or _MODULE_ID_PATTERN.fullmatch(module_id) is None:
        raise ValueError("module_id must use canonical snake_case")
    relative_directory = (
        MODULE_AUTHORING_ROOT / skill_id / "runtime_modules" / module_id
    )
    directory = _checked_entry(
        project_root,
        relative_directory,
        label="Module authoring directory",
        expected_kind="directory",
    )
    root_entries = tuple(directory.iterdir())
    if any(entry.is_symlink() for entry in root_entries):
        raise ValueError("Module authoring root cannot contain symlinks")
    unexpected = sorted({entry.name for entry in root_entries} - _ROOT_ENTRY_NAMES)
    if unexpected:
        raise ValueError(f"Module authoring has undeclared root entries: {unexpected}")

    registration_path = _checked_entry(
        project_root,
        relative_directory / MODULE_REGISTRATION_FILENAME,
        label="Module registration",
        expected_kind="file",
    )
    prompt_path = _checked_entry(
        project_root,
        relative_directory / MODULE_PROMPT_FILENAME,
        label="Module prompt",
        expected_kind="file",
    )
    input_schema_path = _checked_entry(
        project_root,
        relative_directory / MODULE_INPUT_SCHEMA_PATH,
        label="Module input schema",
        expected_kind="file",
    )
    output_schema_path = _checked_entry(
        project_root,
        relative_directory / MODULE_OUTPUT_SCHEMA_PATH,
        label="Module output schema",
        expected_kind="file",
    )
    for entry in directory.rglob("*"):
        if entry.is_symlink():
            raise ValueError("Module authoring closure cannot contain symlinks")
        if not entry.is_file() and not entry.is_dir():
            raise ValueError("Module authoring closure has a special entry")
    schema_files = {
        entry.relative_to(directory).as_posix()
        for entry in (directory / "schemas").rglob("*")
        if entry.is_file()
    }
    if schema_files != {
        MODULE_INPUT_SCHEMA_PATH.as_posix(),
        MODULE_OUTPUT_SCHEMA_PATH.as_posix(),
    }:
        raise ValueError(
            "Module schemas must be exactly schemas/input.schema.json and "
            "schemas/output.schema.json"
        )

    try:
        payload = json.loads(registration_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Module registration must be UTF-8 JSON") from exc
    if type(payload) is not dict or set(payload) != _MANIFEST_KEYS:
        missing = sorted(
            _MANIFEST_KEYS - set(payload)
            if isinstance(payload, dict)
            else _MANIFEST_KEYS
        )
        extra = sorted(set(payload) - _MANIFEST_KEYS) if isinstance(payload, dict) else []
        raise ValueError(
            "Module registration has an invalid v2 shape: "
            f"missing={missing}, extra={extra}"
        )
    if payload["schema_version"] != MODULE_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("unsupported Module registration schema_version")
    if payload["skill_id"] != skill_id:
        raise ValueError("registration skill_id differs from its Skill directory")
    if payload["module_id"] != module_id:
        raise ValueError("registration module_id differs from its Module directory")
    if payload["input_schema_path"] != MODULE_INPUT_SCHEMA_PATH.as_posix():
        raise ValueError("input_schema_path must use the fixed Module-local name")
    if payload["output_schema_path"] != MODULE_OUTPUT_SCHEMA_PATH.as_posix():
        raise ValueError("output_schema_path must use the fixed Module-local name")

    owner_contract_path = _repository_path(
        "owner_contract_path", payload["owner_contract_path"]
    )
    owner_path = _checked_entry(
        project_root,
        owner_contract_path,
        label="Module owner Design Doc",
        expected_kind="file",
    )
    skill_path = _checked_entry(
        project_root,
        MODULE_AUTHORING_ROOT / skill_id / "SKILL.md",
        label="canonical Skill projection",
        expected_kind="file",
    )
    skill_content = _read_utf8(skill_path, label="canonical Skill projection")
    owner_content = _read_utf8(owner_path, label="Module owner Design Doc")
    if not _declares_identifier(skill_content, module_id):
        raise ValueError(f"canonical Skill does not declare module_id: {module_id}")
    if not _declares_identifier(owner_content, module_id):
        raise ValueError(f"owner Design Doc does not declare module_id: {module_id}")

    prompt = _read_utf8(prompt_path, label="Module prompt")
    if not prompt.endswith("\n"):
        raise ValueError("Module prompt must end with one newline")
    if prompt.startswith("---\n"):
        raise ValueError("Module prompt must not contain frontmatter")
    input_schema = _read_utf8(input_schema_path, label="Module input schema")
    output_schema = _read_utf8(output_schema_path, label="Module output schema")
    try:
        input_document = json.loads(input_schema)
        output_document = json.loads(output_schema)
    except json.JSONDecodeError as exc:
        raise ValueError("Module schema must be valid JSON") from exc
    if type(input_document) is not dict or type(output_document) is not dict:
        raise ValueError("Module schema must be one JSON object")
    input_schema_ref = _module_schema_ref(
        "input_schema_ref",
        payload["input_schema_ref"],
        module_id=module_id,
        direction="input",
    )
    output_schema_ref = _module_schema_ref(
        "output_schema_ref",
        payload["output_schema_ref"],
        module_id=module_id,
        direction="output",
    )
    if input_document.get("$id") != input_schema_ref:
        raise ValueError("input_schema_ref differs from input schema $id")
    if output_document.get("$id") != output_schema_ref:
        raise ValueError("output_schema_ref differs from output schema $id")
    try:
        entry_policy = ModuleEntryPolicy(payload["entry_policy"])
        output_policy = OutputResolutionPolicy(payload["output_resolution_policy"])
    except ValueError as exc:
        raise ValueError("Module registration contains an invalid policy") from exc

    return ModuleRegistrationSource(
        skill_id=skill_id,
        module_id=module_id,
        owner_contract_ref=(
            "owner-contract-sha256:"
            + hashlib.sha256(owner_content.encode("utf-8")).hexdigest()
        ),
        owner_contract_content=owner_content,
        input_schema_ref=input_schema_ref,
        input_schema_document=input_schema,
        output_schema_ref=output_schema_ref,
        output_schema_document=output_schema,
        instruction_text=prompt,
        declared_operation_ids=_sorted_unique_strings(
            "declared_operation_ids", payload["declared_operation_ids"]
        ),
        compatible_transport_kinds=_sorted_unique_strings(
            "compatible_transport_kinds", payload["compatible_transport_kinds"]
        ),
        behavior_policy_ref=_non_empty_string(
            "behavior_policy_ref", payload["behavior_policy_ref"]
        ),
        evaluation_policy_ref=_non_empty_string(
            "evaluation_policy_ref", payload["evaluation_policy_ref"]
        ),
        retry_policy_ref=_non_empty_string(
            "retry_policy_ref", payload["retry_policy_ref"]
        ),
        entry_policy=entry_policy,
        output_resolution_policy=output_policy,
    )


__all__ = [
    "MODULE_AUTHORING_ROOT",
    "MODULE_REGISTRATION_SCHEMA_VERSION",
    "ModuleRegistrationSource",
    "load_module_registration",
]
