#!/usr/bin/env python3
"""Project exact portable Governance Skills into supported Primary Agent hosts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MANIFEST_RELATIVE_PATH = Path(
    "09_soul/governance/governance_skill_manifest.json"
)
T0_MANIFEST_RELATIVE_PATH = Path(
    "09_soul/governance/governance_t0_manifest.json"
)
PROJECT_BINDING_MANIFEST_RELATIVE_PATH = Path(
    "governance_bindings/governance_skill_binding_manifest.json"
)
PROJECT_RELEASE_POLICY_RELATIVE_PATH = Path(
    "governance_bindings/governance_release_policy.json"
)
MANIFEST_VERSION = "governance_skill_manifest_v4"
PROJECT_RELEASE_POLICY_VERSION = "governance_release_policy_v2"
PROJECT_BINDING_MANIFEST_VERSION = (
    "governance_skill_project_binding_manifest_v1"
)
SOURCE_PREFIX = PurePosixPath("09_soul/governance/skills")
TARGET_PREFIX = PurePosixPath("designDoc")
PROJECT_BINDING_SOURCE_PREFIX = PurePosixPath("governance_bindings/skills")
HOST_TARGET_PREFIXES = {
    "claude": PurePosixPath(".claude/skills"),
    "codex": PurePosixPath(".agents/skills"),
}
PORTABLE_SOURCE_FORBIDDEN_FRAGMENTS = (
    b"/Users/",
    b"09_soul/",
    b"designDoc/temp/",
    b"src/",
    b"tests/",
    b".venv/",
)
SOUL_RESOURCE_PATHS = {
    "soul:axiom_a14_prompt_boundary_hygiene_2026": Path(
        "09_soul/axioms/a14_prompt_boundary_hygiene.md"
    ),
    "soul:axiom_a21_skill_agent_boundary_2026": Path(
        "09_soul/axioms/a21_skill_agent_boundary.md"
    ),
    "soul:communication": Path("09_soul/core/COMMUNICATION.md"),
    "soul:bestpractice_skill_writing": Path(
        "09_soul/skills/bestpractice_skill_writing.md"
    ),
    "soul:bestpractice_doc_self_review": Path(
        "09_soul/skills/bestpractice_doc_self_review.md"
    ),
}

_T0_PATH_PATTERN = re.compile(
    rb"(?:designDoc/)?(the_[a-z0-9_]+)\.md"
)
_IDENTITY_SEPARATOR_PATTERN = re.compile(rb"[-_ ]+")


class GovernanceSkillReleaseError(ValueError):
    """Raised when the portable Governance Skill release is invalid."""


@dataclass(frozen=True)
class SkillProjection:
    host_id: str
    target: str


@dataclass(frozen=True)
class SkillPackageFile:
    source: str
    sha256: str
    projections: tuple[SkillProjection, ...]


@dataclass(frozen=True)
class PortableGovernanceSkill:
    skill_id: str
    required_t0_layer_ids: tuple[str, ...]
    required_soul_resource_ids: tuple[str, ...]
    primary_agent_entry_role: str
    primary_agent_entry_subject: str
    package_files: tuple[SkillPackageFile, ...]


@dataclass(frozen=True)
class GovernanceSkillManifest:
    manifest_version: str
    retired_projection_roots: tuple[str, ...]
    portable_governance_skills: tuple[PortableGovernanceSkill, ...]


@dataclass(frozen=True)
class ProjectGovernanceSkillBinding:
    skill_id: str
    source: str
    sha256: str


@dataclass(frozen=True)
class GovernanceSkillReleaseIssue:
    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class GovernanceSkillReleaseReport:
    skill_count: int
    projection_count: int
    issues: tuple[GovernanceSkillReleaseIssue, ...]

    @property
    def is_clean(self) -> bool:
        return not self.issues


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _payload_references_identity(payload: bytes, identity: str) -> bool:
    """Match one exact kebab-case identity without flagging a successor ID."""

    encoded_identity = identity.encode("ascii")
    pattern = re.compile(
        rb"(?<![a-z0-9-])"
        + re.escape(encoded_identity)
        + rb"(?![a-z0-9-])"
    )
    return pattern.search(payload) is not None


def _project_forbidden_source_fragments(project_root: Path) -> tuple[bytes, ...]:
    path = project_root / PROJECT_RELEASE_POLICY_RELATIVE_PATH
    if not path.exists():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernanceSkillReleaseError(
            f"cannot read project Governance release policy: {path}"
        ) from exc
    _require_exact_keys(
        payload,
        {
            "schema_version",
            "forbidden_source_fragments",
            "retired_t0_targets",
        },
        context="project_release_policy",
    )
    if payload["schema_version"] != PROJECT_RELEASE_POLICY_VERSION:
        raise GovernanceSkillReleaseError(
            "unsupported project Governance release policy version: "
            f"{payload['schema_version']}"
        )
    rows = payload["forbidden_source_fragments"]
    if (
        not isinstance(rows, list)
        or any(not isinstance(item, str) or not item for item in rows)
        or len(set(rows)) != len(rows)
    ):
        raise GovernanceSkillReleaseError(
            "forbidden_source_fragments must be unique non-empty strings"
        )
    retired_rows = payload["retired_t0_targets"]
    if (
        not isinstance(retired_rows, list)
        or any(not isinstance(item, str) or not item for item in retired_rows)
        or len(set(retired_rows)) != len(retired_rows)
    ):
        raise GovernanceSkillReleaseError(
            "retired_t0_targets must be unique non-empty strings"
        )
    return tuple(item.encode("utf-8") for item in rows)


def _require_exact_keys(
    payload: dict[str, Any], expected: set[str], *, context: str
) -> None:
    actual = set(payload)
    if actual != expected:
        raise GovernanceSkillReleaseError(
            f"{context} keys mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _validated_relative_path(
    raw_path: object, *, required_prefix: PurePosixPath, context: str
) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise GovernanceSkillReleaseError(f"{context} must be a non-empty string")
    if "\\" in raw_path:
        raise GovernanceSkillReleaseError(f"{context} must use POSIX separators")
    candidate = PurePosixPath(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts or str(candidate) != raw_path:
        raise GovernanceSkillReleaseError(
            f"{context} must be a normalized repository-relative path: {raw_path}"
        )
    try:
        candidate.relative_to(required_prefix)
    except ValueError as exc:
        raise GovernanceSkillReleaseError(
            f"{context} must be under {required_prefix.as_posix()}: {raw_path}"
        ) from exc
    return raw_path


def _validated_retired_projection_root(raw_path: object, *, context: str) -> str:
    if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
        raise GovernanceSkillReleaseError(
            f"{context} must be a non-empty normalized host path"
        )
    candidate = PurePosixPath(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts or str(candidate) != raw_path:
        raise GovernanceSkillReleaseError(
            f"{context} must be a normalized repository-relative path: {raw_path}"
        )
    for prefix in HOST_TARGET_PREFIXES.values():
        try:
            relative = candidate.relative_to(prefix)
        except ValueError:
            continue
        if len(relative.parts) == 1:
            return raw_path
    raise GovernanceSkillReleaseError(
        f"{context} must name one top-level supported host Skill directory"
    )


def _resolve_without_symlink_escape(project_root: Path, relative_path: str) -> Path:
    root = project_root.resolve()
    candidate = root / relative_path
    current = root
    for part in PurePosixPath(relative_path).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise GovernanceSkillReleaseError(
                f"managed path cannot traverse a symlink: {relative_path}"
            )
    try:
        candidate.parent.resolve().relative_to(root)
    except ValueError as exc:
        raise GovernanceSkillReleaseError(
            f"managed path escapes project root: {relative_path}"
        ) from exc
    return candidate


def _frontmatter_scalars(payload: bytes, *, source: str) -> dict[str, str]:
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise GovernanceSkillReleaseError(f"Skill must be UTF-8: {source}") from exc
    if not lines or lines[0].strip() != "---":
        raise GovernanceSkillReleaseError(f"Skill lacks frontmatter: {source}")
    try:
        closing = next(
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as exc:
        raise GovernanceSkillReleaseError(
            f"Skill frontmatter is unterminated: {source}"
        ) from exc
    values: dict[str, str] = {}
    in_metadata = False
    for line in lines[1:closing]:
        if line == "metadata:":
            in_metadata = True
            continue
        if not line:
            continue
        if in_metadata and line.startswith("  ") and ":" in line:
            normalized_line = line[2:]
        elif not line[0].isspace() and ":" in line:
            in_metadata = False
            normalized_line = line
        else:
            continue
        key, raw_value = normalized_line.split(":", maxsplit=1)
        value = raw_value.strip()
        if value.startswith(("\"", "'")) and value.endswith(value[0]):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _known_t0_contracts(project_root: Path) -> dict[str, str | None]:
    module_path = Path(__file__).with_name("governance_t0_release.py")
    module_name = "_hoveath_governance_t0_release"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise GovernanceSkillReleaseError(
            f"cannot load portable T0 release validator: {module_path}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
        manifest = module.load_governance_t0_manifest(project_root)
    except (OSError, ImportError, module.GovernanceT0ReleaseError) as exc:
        raise GovernanceSkillReleaseError(
            "cannot validate portable T0 manifest before Skill release: "
            f"{project_root / T0_MANIFEST_RELATIVE_PATH}: {exc}"
        ) from exc
    contracts: dict[str, str | None] = {
        row.target: row.t0_layer_id
        for row in manifest.portable_t0_contracts
    }
    contracts[manifest.charter_target] = None
    return contracts


def _validate_runtime_module_asset_declarations(
    project_root: Path,
    skill_id: str,
    package_files: tuple[SkillPackageFile, ...] | list[SkillPackageFile],
) -> None:
    package_root = SOURCE_PREFIX / skill_id
    declared_sources = {PurePosixPath(item.source) for item in package_files}
    module_ids: set[str] = set()
    for source in declared_sources:
        relative = source.relative_to(package_root)
        if not relative.parts or relative.parts[0] != "runtime_modules":
            continue
        if len(relative.parts) < 3:
            raise GovernanceSkillReleaseError(
                f"Runtime Module asset path is incomplete: {source.as_posix()}"
            )
        module_ids.add(relative.parts[1])

    for module_id in sorted(module_ids):
        module_root = package_root / "runtime_modules" / module_id
        required_sources = {
            module_root / "module_registration.json",
            module_root / "prompt.md",
            module_root / "schemas" / "input.schema.json",
            module_root / "schemas" / "output.schema.json",
        }
        missing = sorted(
            path.as_posix() for path in required_sources - declared_sources
        )
        if missing:
            raise GovernanceSkillReleaseError(
                "Runtime Module export is missing required assets: "
                f"{skill_id}/{module_id}: {missing}"
            )
        validation_root = module_root / "validation_cases"
        validation_sources = sorted(
            source
            for source in declared_sources
            if validation_root in source.parents
        )
        if not validation_sources:
            raise GovernanceSkillReleaseError(
                "Runtime Module export requires declared validation cases: "
                f"{skill_id}/{module_id}"
            )
        observed_case_kinds: set[str] = set()
        for source in validation_sources:
            path = _resolve_without_symlink_escape(
                project_root, source.as_posix()
            )
            try:
                fixture = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise GovernanceSkillReleaseError(
                    f"Runtime Module validation case must be UTF-8 JSON: {source}"
                ) from exc
            required_fixture_fields = {
                "case_id",
                "case_kind",
                "module_id",
                "mutation",
                "expected_disposition",
            }
            if not isinstance(fixture, dict) or set(fixture) != required_fixture_fields:
                raise GovernanceSkillReleaseError(
                    "Runtime Module validation case must be an exact declaration: "
                    f"{source}"
                )
            if fixture.get("module_id") != module_id:
                raise GovernanceSkillReleaseError(
                    "Runtime Module validation case module_id mismatch: "
                    f"{source}"
                )
            for field in ("case_id", "mutation", "expected_disposition"):
                value = fixture.get(field)
                if not isinstance(value, str) or not value:
                    raise GovernanceSkillReleaseError(
                        "Runtime Module validation case field must be a non-empty "
                        f"string: {source}: {field}"
                    )
            case_kind = fixture.get("case_kind")
            if case_kind not in {"positive", "negative", "schema_drift"}:
                raise GovernanceSkillReleaseError(
                    "Runtime Module validation case has unsupported case_kind: "
                    f"{source}: {case_kind!r}"
                )
            observed_case_kinds.add(case_kind)
        required_case_kinds = {"positive", "negative", "schema_drift"}
        missing_case_kinds = sorted(required_case_kinds - observed_case_kinds)
        if missing_case_kinds:
            raise GovernanceSkillReleaseError(
                "Runtime Module export is missing required validation case kinds: "
                f"{skill_id}/{module_id}: {missing_case_kinds}"
            )


def _validate_runtime_module_registration_closure(
    skill: PortableGovernanceSkill,
    source_payloads: dict[str, bytes],
) -> None:
    package_root = SOURCE_PREFIX / skill.skill_id
    registration_sources = sorted(
        source
        for source in source_payloads
        if PurePosixPath(source).name == "module_registration.json"
        and "runtime_modules" in PurePosixPath(source).parts
    )
    for registration_source in registration_sources:
        registration_path = PurePosixPath(registration_source)
        relative = registration_path.relative_to(package_root)
        module_id = relative.parts[1]
        module_root = package_root / "runtime_modules" / module_id
        try:
            registration = json.loads(
                source_payloads[registration_source].decode("utf-8")
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GovernanceSkillReleaseError(
                f"Runtime Module registration must be UTF-8 JSON: {registration_source}"
            ) from exc
        if not isinstance(registration, dict):
            raise GovernanceSkillReleaseError(
                f"Runtime Module registration must be an object: {registration_source}"
            )
        if registration.get("module_id") != module_id or registration.get(
            "export_id"
        ) != module_id:
            raise GovernanceSkillReleaseError(
                "Runtime Module directory, module_id, and export_id must match: "
                f"{registration_source}"
            )
        if registration.get("skill_id") != skill.skill_id:
            raise GovernanceSkillReleaseError(
                "Runtime Module registration skill_id must match its owning Skill: "
                f"{registration_source}"
            )
        for field in ("input_schema_path", "output_schema_path"):
            raw_path = registration.get(field)
            if not isinstance(raw_path, str) or not raw_path or "\\" in raw_path:
                raise GovernanceSkillReleaseError(
                    f"Runtime Module {field} must be a normalized relative path: "
                    f"{registration_source}"
                )
            schema_path = PurePosixPath(raw_path)
            if (
                schema_path.is_absolute()
                or ".." in schema_path.parts
                or schema_path.as_posix() != raw_path
            ):
                raise GovernanceSkillReleaseError(
                    f"Runtime Module {field} must be a normalized relative path: "
                    f"{registration_source}"
                )
            resolved_schema = (module_root / schema_path).as_posix()
            if resolved_schema not in source_payloads:
                raise GovernanceSkillReleaseError(
                    f"Runtime Module {field} does not resolve to a declared asset: "
                    f"{registration_source}: {raw_path}"
                )
            try:
                schema = json.loads(
                    source_payloads[resolved_schema].decode("utf-8")
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GovernanceSkillReleaseError(
                    f"Runtime Module schema must be UTF-8 JSON: {resolved_schema}"
                ) from exc
            schema_ref_field = field.replace("_path", "_ref")
            schema_id = schema.get("$id") if isinstance(schema, dict) else None
            registered_schema_ref = registration.get(schema_ref_field)
            if (
                not isinstance(schema_id, str)
                or not schema_id
                or not isinstance(registered_schema_ref, str)
                or not registered_schema_ref
                or schema_id != registered_schema_ref
            ):
                raise GovernanceSkillReleaseError(
                    "Runtime Module schema ref must match the resolved schema $id: "
                    f"{registration_source}: {schema_ref_field}"
                )


def load_project_governance_skill_bindings(
    project_root: Path,
    binding_manifest_path: Path | None = None,
) -> tuple[ProjectGovernanceSkillBinding, ...]:
    """Load optional project-local Skill binding addenda."""

    root = project_root.resolve()
    resolved_manifest = (
        binding_manifest_path or root / PROJECT_BINDING_MANIFEST_RELATIVE_PATH
    )
    if not resolved_manifest.exists():
        return ()
    try:
        raw = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernanceSkillReleaseError(
            f"cannot read Governance Skill binding manifest: {resolved_manifest}"
        ) from exc
    if not isinstance(raw, dict):
        raise GovernanceSkillReleaseError(
            "Governance Skill binding manifest root must be an object"
        )
    _require_exact_keys(
        raw,
        {"manifest_version", "bindings"},
        context="project_binding_manifest",
    )
    if raw["manifest_version"] != PROJECT_BINDING_MANIFEST_VERSION:
        raise GovernanceSkillReleaseError(
            "unsupported Governance Skill binding manifest version: "
            f"{raw['manifest_version']}"
        )
    rows = raw["bindings"]
    if not isinstance(rows, list):
        raise GovernanceSkillReleaseError("project bindings must be an array")
    bindings: list[ProjectGovernanceSkillBinding] = []
    for index, row in enumerate(rows):
        context = f"bindings[{index}]"
        if not isinstance(row, dict):
            raise GovernanceSkillReleaseError(f"{context} must be an object")
        _require_exact_keys(
            row, {"skill_id", "source", "sha256"}, context=context
        )
        skill_id = row["skill_id"]
        if not isinstance(skill_id, str) or not skill_id:
            raise GovernanceSkillReleaseError(
                f"{context}.skill_id must be a non-empty string"
            )
        source = _validated_relative_path(
            row["source"],
            required_prefix=PROJECT_BINDING_SOURCE_PREFIX,
            context=f"{context}.source",
        )
        if PurePosixPath(source).name != f"{skill_id}.md":
            raise GovernanceSkillReleaseError(
                f"{context}.source must end with {skill_id}.md"
            )
        sha256 = row["sha256"]
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(char not in "0123456789abcdef" for char in sha256)
        ):
            raise GovernanceSkillReleaseError(
                f"{context}.sha256 must be 64 lowercase hexadecimal characters"
            )
        bindings.append(
            ProjectGovernanceSkillBinding(
                skill_id=skill_id,
                source=source,
                sha256=sha256,
            )
        )
    skill_ids = [binding.skill_id for binding in bindings]
    duplicates = sorted(
        {skill_id for skill_id in skill_ids if skill_ids.count(skill_id) > 1}
    )
    if duplicates:
        raise GovernanceSkillReleaseError(
            f"duplicate project Governance Skill bindings: {duplicates}"
        )
    return tuple(bindings)


def load_governance_skill_manifest(
    project_root: Path, manifest_path: Path | None = None
) -> GovernanceSkillManifest:
    root = project_root.resolve()
    resolved_manifest = manifest_path or root / MANIFEST_RELATIVE_PATH
    try:
        raw = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernanceSkillReleaseError(
            f"cannot read Governance Skill manifest: {resolved_manifest}"
        ) from exc
    if not isinstance(raw, dict):
        raise GovernanceSkillReleaseError("manifest root must be an object")
    _require_exact_keys(
        raw,
        {
            "manifest_version",
            "retired_projection_roots",
            "portable_governance_skills",
        },
        context="manifest",
    )
    if raw["manifest_version"] != MANIFEST_VERSION:
        raise GovernanceSkillReleaseError(
            f"unsupported manifest version: {raw['manifest_version']}"
        )
    retired_rows = raw["retired_projection_roots"]
    if (
        not isinstance(retired_rows, list)
        or any(not isinstance(item, str) for item in retired_rows)
    ):
        raise GovernanceSkillReleaseError(
            "retired_projection_roots must be an array of strings"
        )
    retired_projection_roots = tuple(
        _validated_retired_projection_root(
            item,
            context=f"retired_projection_roots[{index}]",
        )
        for index, item in enumerate(retired_rows)
    )
    if len(set(retired_projection_roots)) != len(retired_projection_roots):
        raise GovernanceSkillReleaseError(
            "retired_projection_roots must not contain duplicates"
        )
    rows = raw["portable_governance_skills"]
    if not isinstance(rows, list) or not rows:
        raise GovernanceSkillReleaseError(
            "portable_governance_skills must be a non-empty array"
        )
    skills: list[PortableGovernanceSkill] = []
    for index, row in enumerate(rows):
        context = f"portable_governance_skills[{index}]"
        if not isinstance(row, dict):
            raise GovernanceSkillReleaseError(f"{context} must be an object")
        _require_exact_keys(
            row,
            {
                "skill_id",
                "required_t0_layer_ids",
                "required_soul_resource_ids",
                "primary_agent_entry_role",
                "primary_agent_entry_subject",
                "package_files",
            },
            context=context,
        )
        skill_id = row["skill_id"]
        if not isinstance(skill_id, str) or not skill_id or "_" in skill_id:
            raise GovernanceSkillReleaseError(
                f"{context}.skill_id must be a non-empty kebab-case identity"
            )
        required_t0 = row["required_t0_layer_ids"]
        if (
            not isinstance(required_t0, list)
            or not required_t0
            or any(not isinstance(item, str) or not item for item in required_t0)
            or len(set(required_t0)) != len(required_t0)
        ):
            raise GovernanceSkillReleaseError(
                f"{context}.required_t0_layer_ids must be unique non-empty strings"
            )
        required_soul = row["required_soul_resource_ids"]
        if (
            not isinstance(required_soul, list)
            or any(not isinstance(item, str) or not item for item in required_soul)
            or len(set(required_soul)) != len(required_soul)
        ):
            raise GovernanceSkillReleaseError(
                f"{context}.required_soul_resource_ids must be unique strings"
            )
        role = row["primary_agent_entry_role"]
        subject = row["primary_agent_entry_subject"]
        if not isinstance(role, str) or not role or not isinstance(subject, str) or not subject:
            raise GovernanceSkillReleaseError(
                f"{context} entry role and subject must be non-empty strings"
            )
        package_file_rows = row["package_files"]
        if not isinstance(package_file_rows, list) or not package_file_rows:
            raise GovernanceSkillReleaseError(
                f"{context}.package_files must be non-empty"
            )
        package_files: list[SkillPackageFile] = []
        for file_index, package_file in enumerate(package_file_rows):
            file_context = f"{context}.package_files[{file_index}]"
            if not isinstance(package_file, dict):
                raise GovernanceSkillReleaseError(
                    f"{file_context} must be an object"
                )
            _require_exact_keys(
                package_file,
                {"source", "sha256", "projections"},
                context=file_context,
            )
            source = _validated_relative_path(
                package_file["source"],
                required_prefix=SOURCE_PREFIX,
                context=f"{file_context}.source",
            )
            source_path = PurePosixPath(source)
            expected_source_prefix = SOURCE_PREFIX / skill_id
            try:
                source_relative = source_path.relative_to(expected_source_prefix)
            except ValueError as exc:
                raise GovernanceSkillReleaseError(
                    f"{file_context}.source must be inside the {skill_id} package"
                ) from exc
            if not source_relative.parts:
                raise GovernanceSkillReleaseError(
                    f"{file_context}.source must identify one package file"
                )
            sha256 = package_file["sha256"]
            if (
                not isinstance(sha256, str)
                or len(sha256) != 64
                or any(char not in "0123456789abcdef" for char in sha256)
            ):
                raise GovernanceSkillReleaseError(
                    f"{file_context}.sha256 must be 64 lowercase hexadecimal characters"
                )
            projection_rows = package_file["projections"]
            if not isinstance(projection_rows, list) or not projection_rows:
                raise GovernanceSkillReleaseError(
                    f"{file_context}.projections must be non-empty"
                )
            projections: list[SkillProjection] = []
            for projection_index, projection in enumerate(projection_rows):
                projection_context = (
                    f"{file_context}.projections[{projection_index}]"
                )
                if not isinstance(projection, dict):
                    raise GovernanceSkillReleaseError(
                        f"{projection_context} must be an object"
                    )
                _require_exact_keys(
                    projection,
                    {"host_id", "target"},
                    context=projection_context,
                )
                host_id = projection["host_id"]
                if host_id not in HOST_TARGET_PREFIXES:
                    raise GovernanceSkillReleaseError(
                        f"{projection_context}.host_id is not supported: {host_id}"
                    )
                target = _validated_relative_path(
                    projection["target"],
                    required_prefix=HOST_TARGET_PREFIXES[host_id],
                    context=f"{projection_context}.target",
                )
                target_relative = PurePosixPath(target).relative_to(
                    HOST_TARGET_PREFIXES[host_id]
                )
                if target_relative.parts[0] != skill_id:
                    raise GovernanceSkillReleaseError(
                        f"{projection_context}.target must remain inside the "
                        f"{skill_id} host package"
                    )
                if PurePosixPath(*target_relative.parts[1:]) != source_relative:
                    raise GovernanceSkillReleaseError(
                        f"{projection_context}.target must preserve the package-relative "
                        f"path {source_relative.as_posix()}"
                    )
                if source_relative != PurePosixPath("SKILL.md") and host_id == "codex":
                    raise GovernanceSkillReleaseError(
                        f"{projection_context} cannot project Runtime package assets "
                        "into the Codex Skill entry"
                    )
                projections.append(
                    SkillProjection(host_id=host_id, target=target)
                )
            if len({projection.host_id for projection in projections}) != len(
                projections
            ):
                raise GovernanceSkillReleaseError(
                    f"{file_context}.projections repeat a host"
                )
            if source_relative == PurePosixPath("SKILL.md") and {
                projection.host_id for projection in projections
            } != set(HOST_TARGET_PREFIXES):
                raise GovernanceSkillReleaseError(
                    f"{file_context} SKILL.md must project once to every host"
                )
            package_files.append(
                SkillPackageFile(
                    source=source,
                    sha256=sha256,
                    projections=tuple(projections),
                )
            )
        skill_sources = [package_file.source for package_file in package_files]
        skill_source_duplicates = sorted(
            {
                source
                for source in skill_sources
                if skill_sources.count(source) > 1
            }
        )
        if skill_source_duplicates:
            raise GovernanceSkillReleaseError(
                f"{context}.package_files repeat sources: {skill_source_duplicates}"
            )
        skill_source = (
            SOURCE_PREFIX / skill_id / "SKILL.md"
        ).as_posix()
        if skill_sources.count(skill_source) != 1:
            raise GovernanceSkillReleaseError(
                f"{context}.package_files must contain exactly one {skill_source}"
            )
        _validate_runtime_module_asset_declarations(
            root, skill_id, package_files
        )
        skills.append(
            PortableGovernanceSkill(
                skill_id=skill_id,
                required_t0_layer_ids=tuple(required_t0),
                required_soul_resource_ids=tuple(required_soul),
                primary_agent_entry_role=role,
                primary_agent_entry_subject=subject,
                package_files=tuple(package_files),
            )
        )
    for label, values in (
        ("skill_id", [skill.skill_id for skill in skills]),
        (
            "source",
            [
                package_file.source
                for skill in skills
                for package_file in skill.package_files
            ],
        ),
        (
            "target",
            [
                projection.target
                for skill in skills
                for package_file in skill.package_files
                for projection in package_file.projections
            ],
        ),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise GovernanceSkillReleaseError(
                f"duplicate Governance Skill {label}: {duplicates}"
            )
    active_projection_roots = {
        (HOST_TARGET_PREFIXES[host_id] / skill.skill_id).as_posix()
        for skill in skills
        for host_id in HOST_TARGET_PREFIXES
    }
    overlap = sorted(set(retired_projection_roots) & active_projection_roots)
    if overlap:
        raise GovernanceSkillReleaseError(
            f"retired projection roots overlap active Skills: {overlap}"
        )
    return GovernanceSkillManifest(
        manifest_version=MANIFEST_VERSION,
        retired_projection_roots=retired_projection_roots,
        portable_governance_skills=tuple(skills),
    )


def _validated_source_payloads(
    project_root: Path, manifest: GovernanceSkillManifest
) -> tuple[tuple[PortableGovernanceSkill, SkillPackageFile, bytes], ...]:
    known_t0_contracts = _known_t0_contracts(project_root)
    known_t0 = {
        layer_id
        for layer_id in known_t0_contracts.values()
        if layer_id is not None
    }
    bindings = {
        binding.skill_id: binding
        for binding in load_project_governance_skill_bindings(project_root)
    }
    unknown_binding_skills = sorted(
        set(bindings) - {skill.skill_id for skill in manifest.portable_governance_skills}
    )
    if unknown_binding_skills:
        raise GovernanceSkillReleaseError(
            "project binding names an unknown portable Governance Skill: "
            f"{unknown_binding_skills}"
        )
    payloads: list[tuple[PortableGovernanceSkill, SkillPackageFile, bytes]] = []
    project_forbidden_fragments = _project_forbidden_source_fragments(
        project_root
    )
    for skill in manifest.portable_governance_skills:
        unknown_t0 = sorted(set(skill.required_t0_layer_ids) - known_t0)
        if unknown_t0:
            raise GovernanceSkillReleaseError(
                f"Governance Skill requires unknown T0 layers: {skill.skill_id}: {unknown_t0}"
            )
        unknown_soul = sorted(
            set(skill.required_soul_resource_ids) - set(SOUL_RESOURCE_PATHS)
        )
        if unknown_soul:
            raise GovernanceSkillReleaseError(
                "Governance Skill requires unknown Soul resources: "
                f"{skill.skill_id}: {unknown_soul}"
            )
        for resource_id in skill.required_soul_resource_ids:
            resource_path = _resolve_without_symlink_escape(
                project_root, SOUL_RESOURCE_PATHS[resource_id].as_posix()
            )
            if not resource_path.is_file():
                raise GovernanceSkillReleaseError(
                    "Governance Skill Soul resource does not resolve: "
                    f"{skill.skill_id}: {resource_id}: {resource_path}"
                )
        binding = bindings.get(skill.skill_id)
        skill_source_payloads: dict[str, bytes] = {}
        for package_file in skill.package_files:
            source_path = _resolve_without_symlink_escape(
                project_root, package_file.source
            )
            try:
                payload = source_path.read_bytes()
            except OSError as exc:
                raise GovernanceSkillReleaseError(
                    f"cannot read Governance Skill package source: "
                    f"{package_file.source}"
                ) from exc
            actual_hash = _sha256_bytes(payload)
            if actual_hash != package_file.sha256:
                raise GovernanceSkillReleaseError(
                    f"Governance Skill package source hash mismatch: "
                    f"{package_file.source}; declared={package_file.sha256}; "
                    f"actual={actual_hash}"
                )
            skill_source_payloads[package_file.source] = payload
            for fragment in PORTABLE_SOURCE_FORBIDDEN_FRAGMENTS:
                if fragment in payload:
                    raise GovernanceSkillReleaseError(
                        "portable Governance Skill contains project-local identity: "
                        f"{package_file.source}: {fragment.decode('utf-8')}"
                    )
            folded_payload = _IDENTITY_SEPARATOR_PATTERN.sub(
                b"_", payload.lower()
            )
            for fragment in project_forbidden_fragments:
                folded_fragment = _IDENTITY_SEPARATOR_PATTERN.sub(
                    b"_", fragment.lower()
                )
                if folded_fragment in folded_payload:
                    raise GovernanceSkillReleaseError(
                        "portable Governance Skill contains project-local identity: "
                        f"{package_file.source}: {fragment.decode('utf-8')}"
                    )
            cited_t0_paths = {
                f"designDoc/{match.decode('ascii')}.md"
                for match in _T0_PATH_PATTERN.findall(payload)
            }
            unknown_cited_paths = sorted(
                cited_t0_paths - set(known_t0_contracts)
            )
            if unknown_cited_paths:
                raise GovernanceSkillReleaseError(
                    "Governance Skill cites unknown portable T0 contracts: "
                    f"{skill.skill_id}: {unknown_cited_paths}"
                )
            undeclared_cited_t0 = sorted(
                {
                    known_t0_contracts[path]
                    for path in cited_t0_paths
                    if known_t0_contracts[path] is not None
                }
                - set(skill.required_t0_layer_ids)
            )
            if undeclared_cited_t0:
                raise GovernanceSkillReleaseError(
                    "Governance Skill package has undeclared T0 dependencies: "
                    f"{skill.skill_id}: {undeclared_cited_t0}"
                )
            package_relative = PurePosixPath(package_file.source).relative_to(
                SOURCE_PREFIX / skill.skill_id
            )
            if package_relative == PurePosixPath("SKILL.md"):
                metadata = _frontmatter_scalars(
                    payload, source=package_file.source
                )
                expected = {
                    "name": skill.skill_id,
                    "skill_class": "primary_agent_development",
                    "primary_agent_entry_role": skill.primary_agent_entry_role,
                    "primary_agent_entry_subject": skill.primary_agent_entry_subject,
                }
                for key, value in expected.items():
                    if metadata.get(key) != value:
                        raise GovernanceSkillReleaseError(
                            "Governance Skill frontmatter mismatch: "
                            f"{skill.skill_id}: {key}={metadata.get(key)!r}, "
                            f"expected={value!r}"
                        )
                authority = _validated_relative_path(
                    metadata.get("first_authority_ref", ""),
                    required_prefix=TARGET_PREFIX,
                    context=f"{skill.skill_id}.first_authority_ref",
                )
                authority_path = _resolve_without_symlink_escape(
                    project_root, authority
                )
                if not authority_path.is_file():
                    raise GovernanceSkillReleaseError(
                        "Governance Skill authority does not resolve: "
                        f"{skill.skill_id}: {authority}"
                    )
                authority_t0 = known_t0_contracts.get(authority)
                if authority_t0 not in skill.required_t0_layer_ids:
                    raise GovernanceSkillReleaseError(
                        "Governance Skill first authority is outside its declared "
                        f"T0 closure: {skill.skill_id}: {authority}"
                    )
                if binding is not None:
                    binding_path = _resolve_without_symlink_escape(
                        project_root, binding.source
                    )
                    try:
                        binding_payload = binding_path.read_bytes()
                    except OSError as exc:
                        raise GovernanceSkillReleaseError(
                            "cannot read Governance Skill project binding: "
                            f"{binding.source}"
                        ) from exc
                    actual_binding_hash = _sha256_bytes(binding_payload)
                    if actual_binding_hash != binding.sha256:
                        raise GovernanceSkillReleaseError(
                            "Governance Skill project binding hash mismatch: "
                            f"{binding.source}; declared={binding.sha256}; "
                            f"actual={actual_binding_hash}"
                        )
                    if binding_payload.startswith(b"---\n"):
                        raise GovernanceSkillReleaseError(
                            "Governance Skill project binding must not contain "
                            f"frontmatter: {binding.source}"
                        )
                    if not binding_payload.endswith(b"\n"):
                        raise GovernanceSkillReleaseError(
                            "Governance Skill project binding must end with one "
                            f"newline: {binding.source}"
                        )
                    payload = payload + b"\n" + binding_payload
            payloads.append((skill, package_file, payload))
        _validate_runtime_module_registration_closure(
            skill, skill_source_payloads
        )
    return tuple(payloads)


def check_governance_skill_release(
    project_root: Path, manifest_path: Path | None = None
) -> GovernanceSkillReleaseReport:
    root = project_root.resolve()
    manifest = load_governance_skill_manifest(root, manifest_path)
    payloads = _validated_source_payloads(root, manifest)
    issues: list[GovernanceSkillReleaseIssue] = []
    projection_count = 0
    declared_targets_by_root: dict[str, set[str]] = {}
    for skill, package_file, source_payload in payloads:
        for projection in package_file.projections:
            projection_count += 1
            projection_root = (
                HOST_TARGET_PREFIXES[projection.host_id] / skill.skill_id
            ).as_posix()
            declared_targets_by_root.setdefault(projection_root, set()).add(
                projection.target
            )
            target_path = _resolve_without_symlink_escape(root, projection.target)
            if not target_path.is_file():
                issues.append(
                    GovernanceSkillReleaseIssue(
                        code="governance_skill_projection_missing",
                        path=projection.target,
                        detail=f"missing {projection.host_id} projection for {skill.skill_id}",
                    )
                )
            elif target_path.read_bytes() != source_payload:
                issues.append(
                    GovernanceSkillReleaseIssue(
                        code="governance_skill_projection_drift",
                        path=projection.target,
                        detail=(
                            "projection differs from the released portable method "
                            f"and registered project binding for {skill.skill_id}"
                        ),
                    )
                )
    for projection_root, declared_targets in sorted(
        declared_targets_by_root.items()
    ):
        root_path = _resolve_without_symlink_escape(root, projection_root)
        if not root_path.exists():
            continue
        actual_members = {
            path.relative_to(root).as_posix()
            for path in root_path.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        for undeclared_path in sorted(actual_members - declared_targets):
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_undeclared_package_member",
                    path=undeclared_path,
                    detail=(
                        "managed governance Skill package contains an "
                        "undeclared host member"
                    ),
                )
            )
    for skill in manifest.portable_governance_skills:
        source_root = (SOURCE_PREFIX / skill.skill_id).as_posix()
        source_root_path = _resolve_without_symlink_escape(root, source_root)
        declared_sources = {
            package_file.source for package_file in skill.package_files
        }
        declared_directories: set[str] = set()
        source_root_pure = PurePosixPath(source_root)
        for source in declared_sources:
            current = PurePosixPath(source).parent
            while current != source_root_pure:
                declared_directories.add(current.as_posix())
                current = current.parent
        actual_sources = {
            path.relative_to(root).as_posix()
            for path in source_root_path.rglob("*")
            if path.is_file() or path.is_symlink()
        }
        for undeclared_path in sorted(actual_sources - declared_sources):
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_undeclared_source_member",
                    path=undeclared_path,
                    detail=(
                        "managed governance Skill source package contains an "
                        "undeclared member"
                    ),
                )
            )
        actual_directories = {
            path.relative_to(root).as_posix()
            for path in source_root_path.rglob("*")
            if path.is_dir() and not path.is_symlink()
        }
        for undeclared_directory in sorted(
            actual_directories - declared_directories
        ):
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_undeclared_source_directory",
                    path=undeclared_directory,
                    detail=(
                        "managed governance Skill source package contains an "
                        "undeclared directory"
                    ),
                )
            )
    declared_skill_roots = {
        (SOURCE_PREFIX / skill.skill_id).as_posix()
        for skill in manifest.portable_governance_skills
    }
    source_prefix_path = _resolve_without_symlink_escape(
        root, SOURCE_PREFIX.as_posix()
    )
    for entry in sorted(source_prefix_path.iterdir(), key=lambda path: path.name):
        entry_ref = entry.relative_to(root).as_posix()
        if entry_ref not in declared_skill_roots:
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_undeclared_source_member",
                    path=entry_ref,
                    detail=(
                        "portable Governance Skill source root contains an "
                        "undeclared package or file"
                    ),
                )
            )
    for retired_root in manifest.retired_projection_roots:
        retired_path = _resolve_without_symlink_escape(root, retired_root)
        if retired_path.exists():
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_retired_projection_present",
                    path=retired_root,
                    detail="retired governance projection remains discoverable",
                )
            )
    retired_identities = {
        PurePosixPath(retired_root).name
        for retired_root in manifest.retired_projection_roots
    }
    for host_root in HOST_TARGET_PREFIXES.values():
        host_path = _resolve_without_symlink_escape(root, host_root.as_posix())
        if not host_path.exists():
            continue
        for path in sorted(host_path.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            payload = path.read_bytes()
            for retired_identity in sorted(retired_identities):
                if _payload_references_identity(payload, retired_identity):
                    issues.append(
                        GovernanceSkillReleaseIssue(
                            code="governance_skill_retired_projection_reference",
                            path=path.relative_to(root).as_posix(),
                            detail=(
                                "active host Skill surface references retired "
                                f"governance identity: {retired_identity}"
                            ),
                        )
                    )
    return GovernanceSkillReleaseReport(
        skill_count=len(manifest.portable_governance_skills),
        projection_count=projection_count,
        issues=tuple(issues),
    )


def _write_bytes_atomically(target_path: Path, payload: bytes) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.", suffix=".tmp", dir=target_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, target_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def apply_governance_skill_release(
    project_root: Path, manifest_path: Path | None = None
) -> GovernanceSkillReleaseReport:
    root = project_root.resolve()
    manifest = load_governance_skill_manifest(root, manifest_path)
    payloads = _validated_source_payloads(root, manifest)
    for _skill, package_file, source_payload in payloads:
        for projection in package_file.projections:
            _write_bytes_atomically(
                _resolve_without_symlink_escape(root, projection.target), source_payload
            )
    return check_governance_skill_release(root, manifest_path)


def _print_report(report: GovernanceSkillReleaseReport) -> None:
    print(
        f"Hoveath Governance Skill release: {report.skill_count} skill(s), "
        f"{report.projection_count} projection(s)"
    )
    if report.is_clean:
        print("clean")
        return
    for issue in report.issues:
        print(f"{issue.code}: {issue.path}: {issue.detail}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check or apply the Hoveath portable Governance Skill release."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        report = (
            apply_governance_skill_release(args.project_root)
            if args.apply
            else check_governance_skill_release(args.project_root)
        )
    except GovernanceSkillReleaseError as exc:
        print(f"invalid Hoveath Governance Skill release: {exc}", file=sys.stderr)
        return 2
    _print_report(report)
    return 0 if report.is_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
