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
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


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
MANIFEST_VERSION = "governance_skill_manifest_v5"
PROJECT_RELEASE_POLICY_VERSION = "governance_release_policy_v3"
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
_KEBAB_IDENTITY_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SNAKE_IDENTITY_PATTERN_FRAGMENT = r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*"
_MANAGED_TARGET_PATTERN = re.compile(
    rf"^(?:workflow|runtime-module):{_SNAKE_IDENTITY_PATTERN_FRAGMENT}"
    r"@(?:candidate_v|v)[1-9][0-9]*$"
)
_ADMITTED_TARGET_PATTERN = re.compile(
    r"^(?:workflow|runtime-module|deterministic-integration):"
    rf"{_SNAKE_IDENTITY_PATTERN_FRAGMENT}@v[1-9][0-9]*$"
)
_DETERMINISTIC_TARGET_PATTERN = re.compile(
    rf"^deterministic-integration:{_SNAKE_IDENTITY_PATTERN_FRAGMENT}"
    r"@v[1-9][0-9]*$"
)
_UTC_INSTANT_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?Z$"
)
_SOUL_OR_T0_INSTRUCTION_RESOURCE_ID_PATTERN = re.compile(
    r"^(?:soul|t0):[a-z0-9_]+$"
)
_SKILL_INSTRUCTION_RESOURCE_ID_PATTERN = re.compile(
    rf"^skill:{_SNAKE_IDENTITY_PATTERN_FRAGMENT}$"
)
_INSTRUCTION_RESOURCE_ID_PATTERN = re.compile(
    rf"^(?:(?:soul|t0):[a-z0-9_]+|skill:{_SNAKE_IDENTITY_PATTERN_FRAGMENT})$"
)
_SKILL_PARENT_DEPENDENCY_ID_PATTERN = re.compile(
    r"^skill:([a-z0-9]+(?:-[a-z0-9]+)*)$"
)
_EMBEDDED_RESOURCE_MARKER_PATTERN = re.compile(
    rb"^<!-- embedded-resource:((?:soul|t0):[a-z0-9_]+|"
    rb"skill:[a-z][a-z0-9]*(?:_[a-z0-9]+)*):(start|end) -->$",
    re.MULTILINE,
)
_REVIEWER_PROMPT_SOURCE_PATTERN = re.compile(
    rf"^09_soul/governance/skills/[a-z0-9]+(?:-[a-z0-9]+)*/"
    rf"runtime_modules/({_SNAKE_IDENTITY_PATTERN_FRAGMENT})/prompt\.md$"
)
_REVIEWER_PROMPT_H2_PATTERN = re.compile(
    rb"^##\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE
)
_REVIEWER_PROMPT_EXPECTED_SECTIONS = (
    (0, "review_contract_universal"),
    (1, "Review Task"),
    (2, "Inputs, Decision, and Output"),
    (3, "Boundaries and Failure Routing"),
    (4, "Design Review Checklist"),
)
_UNIVERSAL_REVIEW_RESOURCE_IDS = frozenset(
    {
        "t0:review_contract_universal_review_style",
    }
)


def _skill_parent_identity(value: str) -> str | None:
    match = _SKILL_PARENT_DEPENDENCY_ID_PATTERN.fullmatch(value)
    return match.group(1) if match is not None else None


GOVERNANCE_SKILL_MANIFEST_INVALID = "GOVERNANCE_SKILL_MANIFEST_INVALID"
GOVERNANCE_SKILL_OWNER_INVALID = "GOVERNANCE_SKILL_OWNER_INVALID"
GOVERNANCE_SKILL_LIFECYCLE_INVALID = "GOVERNANCE_SKILL_LIFECYCLE_INVALID"
GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID = (
    "GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID"
)
GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID = (
    "GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID"
)
GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID = (
    "GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID"
)
GOVERNANCE_SKILL_PROJECTION_WRITE_FAILED = (
    "GOVERNANCE_SKILL_PROJECTION_WRITE_FAILED"
)


class GovernanceSkillReleaseError(ValueError):
    """Raised when the portable Governance Skill release is invalid."""

    def __init__(
        self,
        detail: str,
        *,
        code: str = GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
    ) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class SkillProjection:
    host_id: str
    target: str


@dataclass(frozen=True)
class InstructionResourceSelector:
    kind: str
    start: str | None
    end_exclusive: str | None


@dataclass(frozen=True)
class InstructionResource:
    resource_id: str
    parent_dependency_id: str
    source: str
    selector: InstructionResourceSelector
    sha256: str


@dataclass(frozen=True)
class SkillPackageFile:
    source: str
    sha256: str
    embedded_resource_ids: tuple[str, ...]
    reviewer_module_id: str | None
    projections: tuple[SkillProjection, ...]


@dataclass(frozen=True)
class SkillRetirementTombstone:
    retired_identity_kind: str
    retired_identity: str
    replacement_skill_id: str
    reason: str
    final_sha256: str
    effective_at_utc: str
    retired_projection_roots: tuple[str, ...]


@dataclass(frozen=True)
class PortableGovernanceSkill:
    skill_id: str
    required_t0_layer_ids: tuple[str, ...]
    required_soul_resource_ids: tuple[str, ...]
    primary_agent_entry_role: str
    primary_agent_entry_subject: str
    accountable_owner_ref: str
    lifecycle_state: str
    managed_target_ref: str | None
    direct_entry_disposition: str
    predecessor_skill_id: str | None
    retirement_tombstone: SkillRetirementTombstone | None
    package_files: tuple[SkillPackageFile, ...]


@dataclass(frozen=True)
class GovernanceSkillManifest:
    manifest_version: str
    forbidden_projection_roots: tuple[str, ...]
    instruction_resources: tuple[InstructionResource, ...]
    portable_governance_skills: tuple[PortableGovernanceSkill, ...]


@dataclass(frozen=True)
class ValidatedSkillPackagePayload:
    skill: PortableGovernanceSkill
    package_file: SkillPackageFile
    source_payload: bytes
    composed_source_payload: bytes
    projection_payload: bytes


@dataclass(frozen=True)
class GovernanceSkillValidatedInput:
    manifest: GovernanceSkillManifest
    source_payloads: tuple[ValidatedSkillPackagePayload, ...]
    soul_resource_payloads: tuple[tuple[str, bytes], ...]
    instruction_resource_payloads: tuple[
        tuple[InstructionResource, bytes], ...
    ]


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


def _instruction_source_lines(
    payload: bytes, *, source: str
) -> tuple[tuple[str, int, int, bool], ...]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GovernanceSkillReleaseError(
            f"instruction resource source must be UTF-8: {source}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        ) from exc
    if "\r" in text:
        raise GovernanceSkillReleaseError(
            f"instruction resource source must use LF line endings: {source}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
    rows: list[tuple[str, int, int, bool]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        has_lf = line.endswith("\n")
        content = line[:-1] if has_lf else line
        encoded = line.encode("utf-8")
        rows.append((content, offset, offset + len(encoded), has_lf))
        offset += len(encoded)
    return tuple(rows)


def _unique_instruction_boundary(
    rows: tuple[tuple[str, int, int, bool], ...],
    boundary: str,
    *,
    source: str,
) -> tuple[int, int, bool]:
    matches = [
        (start, end, has_lf)
        for content, start, end, has_lf in rows
        if content == boundary
    ]
    if len(matches) != 1:
        raise GovernanceSkillReleaseError(
            "instruction resource boundary must occur exactly once: "
            f"{source}: {boundary!r}; observed={len(matches)}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
    return matches[0]


def _load_registered_artifact_contract_adapter(
    project_root: Path,
    binding: Any,
    source_payload: bytes,
    *,
    error_code: str,
) -> Any:
    actual_source_hash = _sha256_bytes(source_payload)
    if actual_source_hash != binding.source_sha256:
        raise GovernanceSkillReleaseError(
            "artifact-contract source hash mismatch: "
            f"{binding.source}; declared={binding.source_sha256}; "
            f"actual={actual_source_hash}",
            code=error_code,
        )
    adapter_path = _resolve_without_symlink_escape(project_root, binding.adapter)
    try:
        adapter_payload = adapter_path.read_bytes()
    except OSError as exc:
        raise GovernanceSkillReleaseError(
            f"cannot read artifact-contract adapter: {binding.adapter}",
            code=error_code,
        ) from exc
    actual_adapter_hash = _sha256_bytes(adapter_payload)
    if actual_adapter_hash != binding.adapter_sha256:
        raise GovernanceSkillReleaseError(
            "artifact-contract adapter hash mismatch: "
            f"{binding.adapter}; declared={binding.adapter_sha256}; "
            f"actual={actual_adapter_hash}",
            code=error_code,
        )
    module_name = (
        "_hoveath_artifact_contract_"
        + binding.artifact_contract_id
        + "_"
        + binding.adapter_sha256[:12]
    )
    spec = importlib.util.spec_from_file_location(module_name, adapter_path)
    if spec is None or spec.loader is None:
        raise GovernanceSkillReleaseError(
            f"cannot load artifact-contract adapter: {binding.adapter}",
            code=error_code,
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except (OSError, ImportError, TypeError, ValueError) as exc:
        raise GovernanceSkillReleaseError(
            f"cannot execute artifact-contract adapter: {binding.adapter}",
            code=error_code,
        ) from exc
    finally:
        sys.modules.pop(module_name, None)
    return module


def _render_artifact_contract_resource(
    project_root: Path,
    resource: InstructionResource,
    source_payload: bytes,
    artifact_contracts_by_source: dict[str, Any],
    *,
    renderer_name: str,
) -> bytes:
    binding = artifact_contracts_by_source.get(resource.source)
    if binding is None:
        raise GovernanceSkillReleaseError(
            "artifact-contract instruction resource is not registered by T0: "
            f"{resource.resource_id}: {resource.source}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
    if resource.parent_dependency_id != binding.owner_t0_layer_id:
        raise GovernanceSkillReleaseError(
            "artifact-contract instruction resource owner mismatch: "
            f"{resource.resource_id}: parent={resource.parent_dependency_id}; "
            f"owner={binding.owner_t0_layer_id}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
    module = _load_registered_artifact_contract_adapter(
        project_root,
        binding,
        source_payload,
        error_code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
    )
    try:
        renderer = getattr(module, renderer_name)
        selected = renderer(
            _resolve_without_symlink_escape(project_root, binding.source)
        )
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        raise GovernanceSkillReleaseError(
            f"artifact-contract projection failed: {resource.resource_id}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        ) from exc
    if not isinstance(selected, bytes):
        raise GovernanceSkillReleaseError(
            "artifact-contract projector must return bytes: "
            f"{resource.resource_id}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
    return selected


def _select_instruction_resource_bytes(
    resource: InstructionResource,
    source_payload: bytes,
    *,
    project_root: Path | None = None,
    artifact_contracts_by_source: dict[str, Any] | None = None,
) -> bytes:
    # Validate the declared source before selecting or rendering from it.  A
    # derived artifact-contract checklist must not hide an invalid source
    # encoding or line-ending contract behind its rendered output.
    rows = _instruction_source_lines(source_payload, source=resource.source)
    selector = resource.selector
    if selector.kind in {
        "artifact_contract_author_self_check",
        "artifact_contract_review_checklist",
    }:
        if project_root is None or artifact_contracts_by_source is None:
            raise GovernanceSkillReleaseError(
                "artifact-contract checklist selection requires T0 bindings",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        renderer_name = (
            "render_author_self_check_instruction"
            if selector.kind == "artifact_contract_author_self_check"
            else "render_reviewer_checklist"
        )
        selected = _render_artifact_contract_resource(
            project_root,
            resource,
            source_payload,
            artifact_contracts_by_source,
            renderer_name=renderer_name,
        )
    elif selector.kind == "whole_file":
        selected = source_payload
    else:
        assert selector.start is not None
        assert selector.end_exclusive is not None
        start_offset, start_end, _start_has_lf = _unique_instruction_boundary(
            rows,
            selector.start,
            source=resource.source,
        )
        end_offset, _end_end, _end_has_lf = _unique_instruction_boundary(
            rows,
            selector.end_exclusive,
            source=resource.source,
        )
        if start_offset >= end_offset:
            raise GovernanceSkillReleaseError(
                "instruction resource selector boundaries are reversed: "
                f"{resource.resource_id}",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        if selector.kind == "heading_range":
            selected = source_payload[start_offset:end_offset]
        else:
            selected = source_payload[start_end:end_offset]
    if not selected or not selected.endswith(b"\n"):
        raise GovernanceSkillReleaseError(
            "instruction resource selection must be non-empty and end with LF: "
            f"{resource.resource_id}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
    actual_hash = _sha256_bytes(selected)
    if actual_hash != resource.sha256:
        raise GovernanceSkillReleaseError(
            "instruction resource selection hash mismatch: "
            f"{resource.resource_id}; declared={resource.sha256}; "
            f"actual={actual_hash}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
    return selected


def _embedded_resource_spans(
    source_payload: bytes,
    embedded_resource_ids: tuple[str, ...],
) -> dict[str, tuple[int, int, int, int]]:
    markers: dict[str, dict[str, list[tuple[int, int]]]] = {}
    for match in _EMBEDDED_RESOURCE_MARKER_PATTERN.finditer(source_payload):
        if match.end() >= len(source_payload) or source_payload[match.end()] != 10:
            raise GovernanceSkillReleaseError(
                "embedded resource marker line must end with LF",
                code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
            )
        resource_id = match.group(1).decode("ascii")
        marker_kind = match.group(2).decode("ascii")
        markers.setdefault(resource_id, {}).setdefault(marker_kind, []).append(
            (match.start(), match.end() + 1)
        )
    declared = set(embedded_resource_ids)
    observed = set(markers)
    if observed != declared:
        raise GovernanceSkillReleaseError(
            "embedded resource marker declarations differ from source markers: "
            f"missing={sorted(declared - observed)}; "
            f"undeclared={sorted(observed - declared)}",
            code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
        )
    spans: dict[str, tuple[int, int, int, int]] = {}
    occupied: list[tuple[int, int, str]] = []
    for resource_id in embedded_resource_ids:
        marker_rows = markers[resource_id]
        starts = marker_rows.get("start", [])
        ends = marker_rows.get("end", [])
        if len(starts) != 1 or len(ends) != 1:
            raise GovernanceSkillReleaseError(
                "embedded resource markers must occur exactly once: "
                f"{resource_id}; starts={len(starts)}; ends={len(ends)}",
                code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
            )
        marker_start, body_start = starts[0]
        body_end, marker_end = ends[0]
        if body_start > body_end:
            raise GovernanceSkillReleaseError(
                f"embedded resource markers are reversed: {resource_id}",
                code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
            )
        spans[resource_id] = (
            marker_start,
            body_start,
            body_end,
            marker_end,
        )
        occupied.append((marker_start, marker_end, resource_id))
    occupied.sort()
    for previous, current in zip(occupied, occupied[1:]):
        if current[0] < previous[1]:
            raise GovernanceSkillReleaseError(
                "embedded resource target blocks overlap: "
                f"{previous[2]} and {current[2]}",
                code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
            )
    return spans


def compose_governance_skill_package_file(
    source_payload: bytes,
    embedded_resource_ids: Iterable[str],
    instruction_resource_payloads: Mapping[str, bytes],
) -> bytes:
    """Return one composed package candidate without writing any file."""

    resource_ids = tuple(embedded_resource_ids)
    if len(set(resource_ids)) != len(resource_ids):
        raise GovernanceSkillReleaseError(
            "embedded_resource_ids must not contain duplicates",
            code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
        )
    missing = sorted(set(resource_ids) - set(instruction_resource_payloads))
    if missing:
        raise GovernanceSkillReleaseError(
            f"embedded resources are undefined: {missing}",
            code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
        )
    spans = _embedded_resource_spans(source_payload, resource_ids)
    composed = source_payload
    replacements = sorted(
        (
            spans[resource_id][1],
            spans[resource_id][2],
            instruction_resource_payloads[resource_id],
        )
        for resource_id in resource_ids
    )
    for body_start, body_end, replacement in reversed(replacements):
        composed = composed[:body_start] + replacement + composed[body_end:]
    return composed


def validate_reviewer_prompt_artifact(
    payload: bytes,
    *,
    embedded_resource_ids: tuple[str, ...],
) -> None:
    """Validate the Review-Contract-owned prompt layout and resource placement."""

    try:
        payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GovernanceSkillReleaseError(
            "Reviewer prompt must be UTF-8",
            code=GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
        ) from exc
    if b"\r" in payload:
        raise GovernanceSkillReleaseError(
            "Reviewer prompt must use LF line endings",
            code=GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
        )
    matches = list(_REVIEWER_PROMPT_H2_PATTERN.finditer(payload))
    observed_sections = tuple(
        (int(match.group(1)), match.group(2).decode("utf-8"))
        for match in matches
    )
    if observed_sections != _REVIEWER_PROMPT_EXPECTED_SECTIONS:
        raise GovernanceSkillReleaseError(
            "Reviewer prompt sections must be exactly 0 through 4 in the "
            f"registered order: observed={observed_sections}",
            code=GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
        )
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(payload)
        if not payload[body_start:body_end].strip():
            raise GovernanceSkillReleaseError(
                "Reviewer prompt section must not be empty: "
                f"{observed_sections[index][0]}",
                code=GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
            )

    universal_resource_ids = tuple(
        resource_id
        for resource_id in embedded_resource_ids
        if resource_id in _UNIVERSAL_REVIEW_RESOURCE_IDS
    )
    checklist_resource_ids = tuple(
        resource_id
        for resource_id in embedded_resource_ids
        if resource_id not in _UNIVERSAL_REVIEW_RESOURCE_IDS
    )
    if len(universal_resource_ids) != 1 or len(checklist_resource_ids) != 1:
        raise GovernanceSkillReleaseError(
            "Reviewer prompt must declare exactly one universal resource and "
            "one Design checklist resource",
            code=GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
        )
    spans = _embedded_resource_spans(payload, embedded_resource_ids)
    section_spans = {
        int(match.group(1)): (
            match.end(),
            matches[index + 1].start() if index + 1 < len(matches) else len(payload),
        )
        for index, match in enumerate(matches)
    }

    def require_resource_in_section(resource_id: str, section_index: int) -> None:
        marker_start, _body_start, _body_end, marker_end = spans[resource_id]
        section_start, section_end = section_spans[section_index]
        if not (section_start <= marker_start and marker_end <= section_end):
            raise GovernanceSkillReleaseError(
                "Reviewer prompt embedded resource is in the wrong section: "
                f"{resource_id}; expected={section_index}",
                code=GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
            )

    require_resource_in_section(universal_resource_ids[0], 0)
    require_resource_in_section(checklist_resource_ids[0], 4)


def _payload_references_identity(payload: bytes, identity: str) -> bool:
    """Match one exact kebab-case identity without flagging a successor ID."""

    encoded_identity = identity.encode("utf-8")
    pattern = re.compile(
        rb"(?<![a-z0-9-])"
        + re.escape(encoded_identity)
        + rb"(?![a-z0-9-])"
    )
    return pattern.search(payload) is not None


def _project_forbidden_source_fragments(project_root: Path) -> tuple[bytes, ...]:
    module = _load_governance_t0_release_module()
    try:
        policy = module.load_project_governance_release_policy(project_root)
    except (OSError, module.GovernanceT0ReleaseError) as exc:
        raise GovernanceSkillReleaseError(
            "cannot validate project Governance release policy before Skill release"
        ) from exc
    return policy.forbidden_source_fragments


def _require_exact_keys(
    payload: dict[str, Any], expected: set[str], *, context: str
) -> None:
    actual = set(payload)
    if actual != expected:
        raise GovernanceSkillReleaseError(
            f"{context} keys mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _require_exact_keys_with_code(
    payload: dict[str, Any],
    expected: set[str],
    *,
    context: str,
    code: str,
) -> None:
    try:
        _require_exact_keys(payload, expected, context=context)
    except GovernanceSkillReleaseError as exc:
        raise GovernanceSkillReleaseError(str(exc), code=code) from exc


def _validated_owner_ref(raw_value: object, *, context: str) -> str:
    if (
        not isinstance(raw_value, str)
        or re.fullmatch(r"designDoc/the_[a-z0-9_]+\.md", raw_value) is None
    ):
        raise GovernanceSkillReleaseError(
            f"{context} must be an exact designDoc/the_<owner>.md reference",
            code=GOVERNANCE_SKILL_OWNER_INVALID,
        )
    return raw_value


def _validated_managed_target_ref(
    raw_value: object,
    *,
    lifecycle_state: str,
    context: str,
) -> str | None:
    if lifecycle_state == "developing":
        if raw_value is not None:
            raise GovernanceSkillReleaseError(
                f"{context} must be null while lifecycle_state is developing",
                code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
            )
        return None
    if not isinstance(raw_value, str):
        raise GovernanceSkillReleaseError(
            f"{context} must be a managed target identity",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    if lifecycle_state == "migration_planned":
        valid = bool(
            _MANAGED_TARGET_PATTERN.fullmatch(raw_value)
            or _DETERMINISTIC_TARGET_PATTERN.fullmatch(raw_value)
        )
    else:
        valid = bool(_ADMITTED_TARGET_PATTERN.fullmatch(raw_value))
    if not valid:
        raise GovernanceSkillReleaseError(
            f"{context} is invalid for lifecycle_state {lifecycle_state}: {raw_value}",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    return raw_value


def _validated_effective_at_utc(raw_value: object, *, context: str) -> str:
    if not isinstance(raw_value, str) or not _UTC_INSTANT_PATTERN.fullmatch(
        raw_value
    ):
        raise GovernanceSkillReleaseError(
            f"{context} must be a canonical RFC3339 UTC instant",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    try:
        parsed = datetime.fromisoformat(raw_value[:-1] + "+00:00")
    except ValueError as exc:
        raise GovernanceSkillReleaseError(
            f"{context} must be a valid RFC3339 UTC instant",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        ) from exc
    if parsed.tzinfo != timezone.utc:
        raise GovernanceSkillReleaseError(
            f"{context} must use UTC",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    return raw_value


def _validated_retirement_tombstone(
    raw_value: object,
    *,
    context: str,
    skill_id: str,
    predecessor_skill_id: str | None,
    forbidden_projection_roots: tuple[str, ...],
) -> SkillRetirementTombstone:
    if not isinstance(raw_value, dict):
        raise GovernanceSkillReleaseError(
            f"{context} must be an object",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    _require_exact_keys_with_code(
        raw_value,
        {
            "retired_identity_kind",
            "retired_identity",
            "replacement_skill_id",
            "reason",
            "final_sha256",
            "effective_at_utc",
            "retired_projection_roots",
        },
        context=context,
        code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
    )
    identity_kind = raw_value["retired_identity_kind"]
    if not isinstance(identity_kind, str) or identity_kind not in {
        "predecessor_skill",
        "direct_entry",
    }:
        raise GovernanceSkillReleaseError(
            f"{context}.retired_identity_kind is unsupported: {identity_kind!r}",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    retired_identity = raw_value["retired_identity"]
    if not isinstance(retired_identity, str) or not _KEBAB_IDENTITY_PATTERN.fullmatch(
        retired_identity
    ):
        raise GovernanceSkillReleaseError(
            f"{context}.retired_identity must be a kebab-case identity",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    replacement_skill_id = raw_value["replacement_skill_id"]
    if replacement_skill_id != skill_id:
        raise GovernanceSkillReleaseError(
            f"{context}.replacement_skill_id must equal enclosing skill_id {skill_id}",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    reason = raw_value["reason"]
    if not isinstance(reason, str) or not reason.strip():
        raise GovernanceSkillReleaseError(
            f"{context}.reason must be a non-empty string",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    final_sha256 = raw_value["final_sha256"]
    if (
        not isinstance(final_sha256, str)
        or len(final_sha256) != 64
        or any(char not in "0123456789abcdef" for char in final_sha256)
    ):
        raise GovernanceSkillReleaseError(
            f"{context}.final_sha256 must be 64 lowercase hexadecimal characters",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    effective_at_utc = _validated_effective_at_utc(
        raw_value["effective_at_utc"],
        context=f"{context}.effective_at_utc",
    )
    raw_roots = raw_value["retired_projection_roots"]
    if not isinstance(raw_roots, list) or not raw_roots:
        raise GovernanceSkillReleaseError(
            f"{context}.retired_projection_roots must be a non-empty array",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    try:
        roots = tuple(
            _validated_retired_projection_root(
                item,
                context=f"{context}.retired_projection_roots[{index}]",
            )
            for index, item in enumerate(raw_roots)
        )
    except GovernanceSkillReleaseError as exc:
        raise GovernanceSkillReleaseError(
            str(exc), code=GOVERNANCE_SKILL_LIFECYCLE_INVALID
        ) from exc
    if len(set(roots)) != len(roots):
        raise GovernanceSkillReleaseError(
            f"{context}.retired_projection_roots must not contain duplicates",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    if any(PurePosixPath(root).name != retired_identity for root in roots):
        raise GovernanceSkillReleaseError(
            f"{context}.retired_projection_roots must end in {retired_identity}",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    if not set(roots).issubset(forbidden_projection_roots):
        raise GovernanceSkillReleaseError(
            f"{context}.retired_projection_roots must be forbidden by the manifest",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    if identity_kind == "predecessor_skill":
        if predecessor_skill_id != retired_identity:
            raise GovernanceSkillReleaseError(
                f"{context} predecessor identity must equal predecessor_skill_id",
                code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
            )
    elif predecessor_skill_id is not None or retired_identity == skill_id:
        raise GovernanceSkillReleaseError(
            f"{context} direct_entry identity must be legacy and use no predecessor",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    return SkillRetirementTombstone(
        retired_identity_kind=identity_kind,
        retired_identity=retired_identity,
        replacement_skill_id=replacement_skill_id,
        reason=reason,
        final_sha256=final_sha256,
        effective_at_utc=effective_at_utc,
        retired_projection_roots=roots,
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
        relative = candidate.relative_to(required_prefix)
    except ValueError as exc:
        raise GovernanceSkillReleaseError(
            f"{context} must be under {required_prefix.as_posix()}: {raw_path}"
        ) from exc
    if not relative.parts:
        raise GovernanceSkillReleaseError(
            f"{context} must identify a member under "
            f"{required_prefix.as_posix()}: {raw_path}"
        )
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


def _load_governance_t0_release_module() -> Any:
    module_path = Path(__file__).with_name("t0_release.py")
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
        return module
    except (OSError, ImportError) as exc:
        raise GovernanceSkillReleaseError(
            f"cannot load portable T0 release validator: {module_path}: {exc}"
        ) from exc


def _load_governance_t0_manifest(project_root: Path) -> Any:
    module = _load_governance_t0_release_module()
    try:
        return module.load_governance_t0_manifest(project_root)
    except (OSError, module.GovernanceT0ReleaseError) as exc:
        raise GovernanceSkillReleaseError(
            "cannot validate portable T0 manifest before Skill release: "
            f"{project_root / T0_MANIFEST_RELATIVE_PATH}: {exc}"
        ) from exc


def _known_t0_contracts(project_root: Path) -> dict[str, str | None]:
    manifest = _load_governance_t0_manifest(project_root)
    contracts: dict[str, str | None] = {
        row.target: row.t0_layer_id
        for row in manifest.portable_t0_contracts
    }
    contracts[manifest.charter_target] = None
    return contracts


def _known_t0_artifact_contracts(project_root: Path) -> dict[str, Any]:
    manifest = _load_governance_t0_manifest(project_root)
    return {binding.source: binding for binding in manifest.artifact_contracts}


def _validate_runtime_module_asset_declarations(
    skill_id: str,
    package_files: tuple[SkillPackageFile, ...] | list[SkillPackageFile],
    source_payloads: dict[str, bytes],
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
        skill_source_path = (package_root / "SKILL.md").as_posix()
        try:
            skill_source = source_payloads[skill_source_path]
        except KeyError as exc:
            raise GovernanceSkillReleaseError(
                f"Runtime Module export requires containing SKILL.md: {skill_id}"
            ) from exc
        if re.search(
            rb"(?<![a-z0-9_])"
            + re.escape(module_id.encode("utf-8"))
            + rb"(?![a-z0-9_])",
            skill_source,
        ) is None:
            raise GovernanceSkillReleaseError(
                "containing Skill does not declare independent Runtime Module: "
                f"{skill_id}/{module_id}"
            )
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
        validation_root = module_root / "tests"
        validation_sources = sorted(
            source
            for source in declared_sources
            if validation_root in source.parents
        )
        if not validation_sources:
            raise GovernanceSkillReleaseError(
                "Runtime Module export requires declared test fixtures: "
                f"{skill_id}/{module_id}"
            )
        observed_case_kinds: set[str] = set()
        for source in validation_sources:
            try:
                fixture = json.loads(
                    source_payloads[source.as_posix()].decode("utf-8")
                )
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GovernanceSkillReleaseError(
                    f"Runtime Module test fixture must be UTF-8 JSON: {source}"
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
                    "Runtime Module test fixture must be an exact declaration: "
                    f"{source}"
                )
            if fixture.get("module_id") != module_id:
                raise GovernanceSkillReleaseError(
                    "Runtime Module test fixture module_id mismatch: "
                    f"{source}"
                )
            for field in ("case_id", "mutation", "expected_disposition"):
                value = fixture.get(field)
                if not isinstance(value, str) or not value:
                    raise GovernanceSkillReleaseError(
                        "Runtime Module test fixture field must be a non-empty "
                        f"string: {source}: {field}"
                    )
            case_kind = fixture.get("case_kind")
            if not isinstance(case_kind, str) or case_kind not in {
                "positive",
                "negative",
                "schema_drift",
            }:
                raise GovernanceSkillReleaseError(
                    "Runtime Module test fixture has unsupported case_kind: "
                    f"{source}: {case_kind!r}"
                )
            observed_case_kinds.add(case_kind)
        required_case_kinds = {"positive", "negative", "schema_drift"}
        missing_case_kinds = sorted(required_case_kinds - observed_case_kinds)
        if missing_case_kinds:
            raise GovernanceSkillReleaseError(
                "Runtime Module export is missing required test fixture kinds: "
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
        if registration.get("module_id") != module_id:
            raise GovernanceSkillReleaseError(
                "Runtime Module directory and module_id must match: "
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
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
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GovernanceSkillReleaseError(
            f"cannot read Governance Skill manifest: {resolved_manifest}",
            code=GOVERNANCE_SKILL_MANIFEST_INVALID,
        ) from exc
    if not isinstance(raw, dict):
        raise GovernanceSkillReleaseError(
            "manifest root must be an object",
            code=GOVERNANCE_SKILL_MANIFEST_INVALID,
        )
    _require_exact_keys_with_code(
        raw,
        {
            "manifest_version",
            "forbidden_projection_roots",
            "instruction_resources",
            "portable_governance_skills",
        },
        context="manifest",
        code=GOVERNANCE_SKILL_MANIFEST_INVALID,
    )
    if raw["manifest_version"] != MANIFEST_VERSION:
        raise GovernanceSkillReleaseError(
            f"unsupported manifest version: {raw['manifest_version']}",
            code=GOVERNANCE_SKILL_MANIFEST_INVALID,
        )
    forbidden_rows = raw["forbidden_projection_roots"]
    if (
        not isinstance(forbidden_rows, list)
        or any(not isinstance(item, str) for item in forbidden_rows)
    ):
        raise GovernanceSkillReleaseError(
            "forbidden_projection_roots must be an array of strings",
            code=GOVERNANCE_SKILL_MANIFEST_INVALID,
        )
    try:
        forbidden_projection_roots = tuple(
            _validated_retired_projection_root(
                item,
                context=f"forbidden_projection_roots[{index}]",
            )
            for index, item in enumerate(forbidden_rows)
        )
    except GovernanceSkillReleaseError as exc:
        raise GovernanceSkillReleaseError(
            str(exc), code=GOVERNANCE_SKILL_MANIFEST_INVALID
        ) from exc
    if len(set(forbidden_projection_roots)) != len(forbidden_projection_roots):
        raise GovernanceSkillReleaseError(
            "forbidden_projection_roots must not contain duplicates",
            code=GOVERNANCE_SKILL_MANIFEST_INVALID,
        )
    raw_instruction_resources = raw["instruction_resources"]
    if not isinstance(raw_instruction_resources, list):
        raise GovernanceSkillReleaseError(
            "instruction_resources must be an array",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
    instruction_resources: list[InstructionResource] = []
    for resource_index, resource_row in enumerate(raw_instruction_resources):
        resource_context = f"instruction_resources[{resource_index}]"
        if not isinstance(resource_row, dict):
            raise GovernanceSkillReleaseError(
                f"{resource_context} must be an object",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        _require_exact_keys_with_code(
            resource_row,
            {
                "resource_id",
                "parent_dependency_id",
                "source",
                "selector",
                "sha256",
            },
            context=resource_context,
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
        resource_id = resource_row["resource_id"]
        if (
            not isinstance(resource_id, str)
            or not _INSTRUCTION_RESOURCE_ID_PATTERN.fullmatch(resource_id)
        ):
            raise GovernanceSkillReleaseError(
                f"{resource_context}.resource_id is invalid: {resource_id!r}",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        parent_dependency_id = resource_row["parent_dependency_id"]
        if not isinstance(parent_dependency_id, str) or not (
            _SOUL_OR_T0_INSTRUCTION_RESOURCE_ID_PATTERN.fullmatch(
                parent_dependency_id
            )
            or _SKILL_PARENT_DEPENDENCY_ID_PATTERN.fullmatch(
                parent_dependency_id
            )
            or re.fullmatch(r"the_[a-z0-9_]+", parent_dependency_id)
        ):
            raise GovernanceSkillReleaseError(
                f"{resource_context}.parent_dependency_id is invalid: "
                f"{parent_dependency_id!r}",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        resource_is_skill_owned = bool(
            _SKILL_INSTRUCTION_RESOURCE_ID_PATTERN.fullmatch(resource_id)
        )
        parent_skill_id = _skill_parent_identity(parent_dependency_id)
        if resource_is_skill_owned != (parent_skill_id is not None):
            raise GovernanceSkillReleaseError(
                f"{resource_context} skill-owned resource and parent identity "
                "must use the skill: namespace together",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        try:
            resource_source = _validated_relative_path(
                resource_row["source"],
                required_prefix=PurePosixPath("09_soul"),
                context=f"{resource_context}.source",
            )
        except GovernanceSkillReleaseError as exc:
            raise GovernanceSkillReleaseError(
                str(exc),
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            ) from exc
        if parent_skill_id is not None:
            expected_skill_source = (
                SOURCE_PREFIX / parent_skill_id / "SKILL.md"
            ).as_posix()
            if resource_source != expected_skill_source:
                raise GovernanceSkillReleaseError(
                    f"{resource_context}.source for a skill-owned resource must "
                    f"be the exact owning SKILL.md: {expected_skill_source}",
                    code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
                )
        raw_selector = resource_row["selector"]
        if not isinstance(raw_selector, dict):
            raise GovernanceSkillReleaseError(
                f"{resource_context}.selector must be an object",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        _require_exact_keys_with_code(
            raw_selector,
            {"kind", "start", "end_exclusive"},
            context=f"{resource_context}.selector",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
        selector_kind = raw_selector["kind"]
        selector_start = raw_selector["start"]
        selector_end = raw_selector["end_exclusive"]
        if selector_kind not in {
            "artifact_contract_author_self_check",
            "artifact_contract_review_checklist",
            "heading_range",
            "whole_file",
            "marker_range",
        }:
            raise GovernanceSkillReleaseError(
                f"{resource_context}.selector.kind is unsupported: "
                f"{selector_kind!r}",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        if selector_kind in {
            "artifact_contract_author_self_check",
            "artifact_contract_review_checklist",
            "whole_file",
        }:
            if selector_start is not None or selector_end is not None:
                raise GovernanceSkillReleaseError(
                    f"{resource_context} selector boundaries must be null",
                    code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
                )
        elif any(
            not isinstance(boundary, str)
            or not boundary
            or "\n" in boundary
            or "\r" in boundary
            for boundary in (selector_start, selector_end)
        ):
            raise GovernanceSkillReleaseError(
                f"{resource_context} selector boundaries must be exact non-empty lines",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        resource_sha256 = resource_row["sha256"]
        if (
            not isinstance(resource_sha256, str)
            or len(resource_sha256) != 64
            or any(char not in "0123456789abcdef" for char in resource_sha256)
        ):
            raise GovernanceSkillReleaseError(
                f"{resource_context}.sha256 must be 64 lowercase hexadecimal characters",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        instruction_resources.append(
            InstructionResource(
                resource_id=resource_id,
                parent_dependency_id=parent_dependency_id,
                source=resource_source,
                selector=InstructionResourceSelector(
                    kind=selector_kind,
                    start=selector_start,
                    end_exclusive=selector_end,
                ),
                sha256=resource_sha256,
            )
        )
    instruction_resource_ids = [
        resource.resource_id for resource in instruction_resources
    ]
    duplicate_instruction_resource_ids = sorted(
        {
            resource_id
            for resource_id in instruction_resource_ids
            if instruction_resource_ids.count(resource_id) > 1
        }
    )
    if duplicate_instruction_resource_ids:
        raise GovernanceSkillReleaseError(
            "instruction_resources repeat resource_id values: "
            f"{duplicate_instruction_resource_ids}",
            code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
    instruction_resources_by_id = {
        resource.resource_id: resource for resource in instruction_resources
    }
    rows = raw["portable_governance_skills"]
    if not isinstance(rows, list) or not rows:
        raise GovernanceSkillReleaseError(
            "portable_governance_skills must be a non-empty array",
            code=GOVERNANCE_SKILL_MANIFEST_INVALID,
        )
    declared_skill_ids_for_resource_resolution = {
        row.get("skill_id")
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("skill_id"), str)
    }
    for resource in instruction_resources:
        parent_skill_id = _skill_parent_identity(
            resource.parent_dependency_id
        )
        if (
            parent_skill_id is not None
            and parent_skill_id
            not in declared_skill_ids_for_resource_resolution
        ):
            raise GovernanceSkillReleaseError(
                "instruction resource has unknown parent Skill: "
                f"{resource.resource_id}: {resource.parent_dependency_id}",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
    skills: list[PortableGovernanceSkill] = []
    for index, row in enumerate(rows):
        context = f"portable_governance_skills[{index}]"
        if not isinstance(row, dict):
            raise GovernanceSkillReleaseError(
                f"{context} must be an object",
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
        if "accountable_owner_ref" not in row:
            raise GovernanceSkillReleaseError(
                f"{context}.accountable_owner_ref is required",
                code=GOVERNANCE_SKILL_OWNER_INVALID,
            )
        lifecycle_keys = {
            "lifecycle_state",
            "managed_target_ref",
            "direct_entry_disposition",
            "predecessor_skill_id",
            "retirement_tombstone",
        }
        missing_lifecycle_keys = sorted(lifecycle_keys - set(row))
        if missing_lifecycle_keys:
            raise GovernanceSkillReleaseError(
                f"{context} lifecycle keys missing: {missing_lifecycle_keys}",
                code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
            )
        _require_exact_keys_with_code(
            row,
            {
                "skill_id",
                "required_t0_layer_ids",
                "required_soul_resource_ids",
                "primary_agent_entry_role",
                "primary_agent_entry_subject",
                "accountable_owner_ref",
                "lifecycle_state",
                "managed_target_ref",
                "direct_entry_disposition",
                "predecessor_skill_id",
                "retirement_tombstone",
                "package_files",
            },
            context=context,
            code=GOVERNANCE_SKILL_MANIFEST_INVALID,
        )
        skill_id = row["skill_id"]
        if (
            not isinstance(skill_id, str)
            or not _KEBAB_IDENTITY_PATTERN.fullmatch(skill_id)
        ):
            raise GovernanceSkillReleaseError(
                f"{context}.skill_id must be a non-empty kebab-case identity",
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
        accountable_owner_ref = _validated_owner_ref(
            row["accountable_owner_ref"],
            context=f"{context}.accountable_owner_ref",
        )
        lifecycle_state = row["lifecycle_state"]
        if not isinstance(lifecycle_state, str) or lifecycle_state not in {
            "developing",
            "migration_planned",
            "managed",
            "direct_entry_retired",
        }:
            raise GovernanceSkillReleaseError(
                f"{context}.lifecycle_state is unsupported: {lifecycle_state!r}",
                code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
            )
        managed_target_ref = _validated_managed_target_ref(
            row["managed_target_ref"],
            lifecycle_state=lifecycle_state,
            context=f"{context}.managed_target_ref",
        )
        direct_entry_disposition = row["direct_entry_disposition"]
        expected_disposition = (
            "retired" if lifecycle_state == "direct_entry_retired" else "active"
        )
        if direct_entry_disposition != expected_disposition:
            raise GovernanceSkillReleaseError(
                f"{context}.direct_entry_disposition must be "
                f"{expected_disposition} for {lifecycle_state}",
                code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
            )
        predecessor_skill_id = row["predecessor_skill_id"]
        if predecessor_skill_id is not None and (
            not isinstance(predecessor_skill_id, str)
            or not _KEBAB_IDENTITY_PATTERN.fullmatch(predecessor_skill_id)
            or predecessor_skill_id == skill_id
        ):
            raise GovernanceSkillReleaseError(
                f"{context}.predecessor_skill_id must be null or a different "
                "kebab-case identity",
                code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
            )
        retirement_tombstone: SkillRetirementTombstone | None
        if lifecycle_state == "direct_entry_retired":
            retirement_tombstone = _validated_retirement_tombstone(
                row["retirement_tombstone"],
                context=f"{context}.retirement_tombstone",
                skill_id=skill_id,
                predecessor_skill_id=predecessor_skill_id,
                forbidden_projection_roots=forbidden_projection_roots,
            )
        elif row["retirement_tombstone"] is not None:
            raise GovernanceSkillReleaseError(
                f"{context}.retirement_tombstone must be null for {lifecycle_state}",
                code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
            )
        else:
            retirement_tombstone = None
        required_t0 = row["required_t0_layer_ids"]
        if (
            not isinstance(required_t0, list)
            or not required_t0
            or any(not isinstance(item, str) or not item for item in required_t0)
            or len(set(required_t0)) != len(required_t0)
        ):
            raise GovernanceSkillReleaseError(
                f"{context}.required_t0_layer_ids must be unique non-empty strings",
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
        required_soul = row["required_soul_resource_ids"]
        if (
            not isinstance(required_soul, list)
            or any(not isinstance(item, str) or not item for item in required_soul)
            or len(set(required_soul)) != len(required_soul)
        ):
            raise GovernanceSkillReleaseError(
                f"{context}.required_soul_resource_ids must be unique strings",
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
        role = row["primary_agent_entry_role"]
        subject = row["primary_agent_entry_subject"]
        if not isinstance(role, str) or not role or not isinstance(subject, str) or not subject:
            raise GovernanceSkillReleaseError(
                f"{context} entry role and subject must be non-empty strings",
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
        package_file_rows = row["package_files"]
        if not isinstance(package_file_rows, list) or not package_file_rows:
            raise GovernanceSkillReleaseError(
                f"{context}.package_files must be non-empty",
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
        package_files: list[SkillPackageFile] = []
        for file_index, package_file in enumerate(package_file_rows):
            file_context = f"{context}.package_files[{file_index}]"
            if not isinstance(package_file, dict):
                raise GovernanceSkillReleaseError(
                    f"{file_context} must be an object",
                    code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                )
            _require_exact_keys_with_code(
                package_file,
                {
                    "source",
                    "sha256",
                    "embedded_resource_ids",
                    "projections",
                },
                context=file_context,
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
            try:
                source = _validated_relative_path(
                    package_file["source"],
                    required_prefix=SOURCE_PREFIX,
                    context=f"{file_context}.source",
                )
            except GovernanceSkillReleaseError as exc:
                raise GovernanceSkillReleaseError(
                    str(exc), code=GOVERNANCE_SKILL_MANIFEST_INVALID
                ) from exc
            source_path = PurePosixPath(source)
            expected_source_prefix = SOURCE_PREFIX / skill_id
            try:
                source_relative = source_path.relative_to(expected_source_prefix)
            except ValueError as exc:
                raise GovernanceSkillReleaseError(
                    f"{file_context}.source must be inside the {skill_id} package",
                    code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                ) from exc
            if not source_relative.parts:
                raise GovernanceSkillReleaseError(
                    f"{file_context}.source must identify one package file",
                    code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                )
            sha256 = package_file["sha256"]
            if (
                not isinstance(sha256, str)
                or len(sha256) != 64
                or any(char not in "0123456789abcdef" for char in sha256)
            ):
                raise GovernanceSkillReleaseError(
                    f"{file_context}.sha256 must be 64 lowercase hexadecimal characters",
                    code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                )
            raw_embedded_resource_ids = package_file["embedded_resource_ids"]
            if not isinstance(raw_embedded_resource_ids, list) or any(
                not isinstance(item, str)
                or not _INSTRUCTION_RESOURCE_ID_PATTERN.fullmatch(item)
                for item in raw_embedded_resource_ids
            ):
                raise GovernanceSkillReleaseError(
                    f"{file_context}.embedded_resource_ids must be an array of "
                    "stable resource IDs",
                    code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
                )
            embedded_resource_ids = tuple(raw_embedded_resource_ids)
            if len(set(embedded_resource_ids)) != len(embedded_resource_ids):
                raise GovernanceSkillReleaseError(
                    f"{file_context}.embedded_resource_ids must not contain duplicates",
                    code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
                )
            unknown_embedded_resource_ids = sorted(
                set(embedded_resource_ids) - set(instruction_resources_by_id)
            )
            if unknown_embedded_resource_ids:
                raise GovernanceSkillReleaseError(
                    f"{file_context} names undefined embedded resources: "
                    f"{unknown_embedded_resource_ids}",
                    code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
                )
            allowed_parent_dependencies = set(required_soul) | set(required_t0)
            unrequired_resource_ids: list[str] = []
            for resource_id in embedded_resource_ids:
                parent_dependency_id = instruction_resources_by_id[
                    resource_id
                ].parent_dependency_id
                parent_skill_id = _skill_parent_identity(
                    parent_dependency_id
                )
                if parent_skill_id is not None:
                    if parent_skill_id != skill_id:
                        unrequired_resource_ids.append(resource_id)
                elif parent_dependency_id not in allowed_parent_dependencies:
                    unrequired_resource_ids.append(resource_id)
            unrequired_resource_ids.sort()
            if unrequired_resource_ids:
                raise GovernanceSkillReleaseError(
                    f"{file_context} embeds resources outside the Skill dependency "
                    f"closure: {unrequired_resource_ids}",
                    code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
                )
            reviewer_module_id: str | None = None
            if _UNIVERSAL_REVIEW_RESOURCE_IDS.intersection(
                embedded_resource_ids
            ):
                reviewer_prompt_match = _REVIEWER_PROMPT_SOURCE_PATTERN.fullmatch(
                    source
                )
                if reviewer_prompt_match is not None:
                    reviewer_module_id = reviewer_prompt_match.group(1)
            projection_rows = package_file["projections"]
            if not isinstance(projection_rows, list) or not projection_rows:
                raise GovernanceSkillReleaseError(
                    f"{file_context}.projections must be non-empty",
                    code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                )
            projections: list[SkillProjection] = []
            for projection_index, projection in enumerate(projection_rows):
                projection_context = (
                    f"{file_context}.projections[{projection_index}]"
                )
                if not isinstance(projection, dict):
                    raise GovernanceSkillReleaseError(
                        f"{projection_context} must be an object",
                        code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                    )
                _require_exact_keys_with_code(
                    projection,
                    {"host_id", "target"},
                    context=projection_context,
                    code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                )
                host_id = projection["host_id"]
                if (
                    not isinstance(host_id, str)
                    or host_id not in HOST_TARGET_PREFIXES
                ):
                    raise GovernanceSkillReleaseError(
                        f"{projection_context}.host_id is not supported: {host_id}",
                        code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                    )
                try:
                    target = _validated_relative_path(
                        projection["target"],
                        required_prefix=HOST_TARGET_PREFIXES[host_id],
                        context=f"{projection_context}.target",
                    )
                except GovernanceSkillReleaseError as exc:
                    raise GovernanceSkillReleaseError(
                        str(exc), code=GOVERNANCE_SKILL_MANIFEST_INVALID
                    ) from exc
                target_relative = PurePosixPath(target).relative_to(
                    HOST_TARGET_PREFIXES[host_id]
                )
                if target_relative.parts[0] != skill_id:
                    raise GovernanceSkillReleaseError(
                        f"{projection_context}.target must remain inside the "
                        f"{skill_id} host package",
                        code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                    )
                if PurePosixPath(*target_relative.parts[1:]) != source_relative:
                    raise GovernanceSkillReleaseError(
                        f"{projection_context}.target must preserve the package-relative "
                        f"path {source_relative.as_posix()}",
                        code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                    )
                if source_relative != PurePosixPath("SKILL.md") and host_id == "codex":
                    raise GovernanceSkillReleaseError(
                        f"{projection_context} cannot project Runtime package assets "
                        "into the Codex Skill entry",
                        code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                    )
                projections.append(
                    SkillProjection(host_id=host_id, target=target)
                )
            if len({projection.host_id for projection in projections}) != len(
                projections
            ):
                raise GovernanceSkillReleaseError(
                    f"{file_context}.projections repeat a host",
                    code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                )
            if source_relative == PurePosixPath("SKILL.md") and {
                projection.host_id for projection in projections
            } != set(HOST_TARGET_PREFIXES):
                raise GovernanceSkillReleaseError(
                    f"{file_context} SKILL.md must project once to every host",
                    code=GOVERNANCE_SKILL_MANIFEST_INVALID,
                )
            package_files.append(
                SkillPackageFile(
                    source=source,
                    sha256=sha256,
                    embedded_resource_ids=embedded_resource_ids,
                    reviewer_module_id=reviewer_module_id,
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
                f"{context}.package_files repeat sources: {skill_source_duplicates}",
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
        skill_source = (
            SOURCE_PREFIX / skill_id / "SKILL.md"
        ).as_posix()
        if skill_sources.count(skill_source) != 1:
            raise GovernanceSkillReleaseError(
                f"{context}.package_files must contain exactly one {skill_source}",
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
        skills.append(
            PortableGovernanceSkill(
                skill_id=skill_id,
                required_t0_layer_ids=tuple(required_t0),
                required_soul_resource_ids=tuple(required_soul),
                primary_agent_entry_role=role,
                primary_agent_entry_subject=subject,
                accountable_owner_ref=accountable_owner_ref,
                lifecycle_state=lifecycle_state,
                managed_target_ref=managed_target_ref,
                direct_entry_disposition=direct_entry_disposition,
                predecessor_skill_id=predecessor_skill_id,
                retirement_tombstone=retirement_tombstone,
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
                f"duplicate Governance Skill {label}: {duplicates}",
                code=GOVERNANCE_SKILL_MANIFEST_INVALID,
            )
    active_projection_roots = {
        (HOST_TARGET_PREFIXES[host_id] / skill.skill_id).as_posix()
        for skill in skills
        for host_id in HOST_TARGET_PREFIXES
    }
    overlap = sorted(set(forbidden_projection_roots) & active_projection_roots)
    if overlap:
        raise GovernanceSkillReleaseError(
            f"forbidden projection roots overlap active Skills: {overlap}",
            code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
        )
    for skill in skills:
        tombstone = skill.retirement_tombstone
        if (
            tombstone is not None
            and set(tombstone.retired_projection_roots)
            & active_projection_roots
        ):
            raise GovernanceSkillReleaseError(
                f"retirement tombstone roots overlap active Skills: {skill.skill_id}",
                code=GOVERNANCE_SKILL_LIFECYCLE_INVALID,
            )
    return GovernanceSkillManifest(
        manifest_version=MANIFEST_VERSION,
        forbidden_projection_roots=forbidden_projection_roots,
        instruction_resources=tuple(instruction_resources),
        portable_governance_skills=tuple(skills),
    )


def _validated_source_payloads(
    project_root: Path, manifest: GovernanceSkillManifest
) -> tuple[
    tuple[ValidatedSkillPackagePayload, ...],
    tuple[tuple[str, bytes], ...],
    tuple[tuple[InstructionResource, bytes], ...],
]:
    known_t0_contracts = _known_t0_contracts(project_root)
    artifact_contracts_by_source = _known_t0_artifact_contracts(project_root)
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
    payloads: list[ValidatedSkillPackagePayload] = []
    owner_rows: list[tuple[PortableGovernanceSkill, object]] = []
    project_forbidden_fragments = _project_forbidden_source_fragments(
        project_root
    )
    source_payload_cache: dict[str, bytes] = {}

    def read_source_once(
        relative_path: str,
        *,
        context: str,
        error_code: str = GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
    ) -> bytes:
        cached = source_payload_cache.get(relative_path)
        if cached is not None:
            return cached
        try:
            source_path = _resolve_without_symlink_escape(
                project_root, relative_path
            )
            payload = source_path.read_bytes()
        except (OSError, GovernanceSkillReleaseError) as exc:
            raise GovernanceSkillReleaseError(
                f"cannot read {context}: {relative_path}",
                code=error_code,
            ) from exc
        source_payload_cache[relative_path] = payload
        return payload

    required_resource_ids: list[str] = []
    for skill in manifest.portable_governance_skills:
        unknown_soul = sorted(
            set(skill.required_soul_resource_ids) - set(SOUL_RESOURCE_PATHS)
        )
        if unknown_soul:
            raise GovernanceSkillReleaseError(
                "Governance Skill requires unknown Soul resources: "
                f"{skill.skill_id}: {unknown_soul}"
            )
        for resource_id in skill.required_soul_resource_ids:
            if resource_id not in required_resource_ids:
                required_resource_ids.append(resource_id)

    instruction_resource_payloads: list[
        tuple[InstructionResource, bytes]
    ] = []
    for resource in manifest.instruction_resources:
        parent_id = resource.parent_dependency_id
        if parent_id.startswith("soul:"):
            parent_is_known = parent_id in SOUL_RESOURCE_PATHS
        elif parent_id.startswith("skill:"):
            parent_is_known = _skill_parent_identity(parent_id) in {
                skill.skill_id
                for skill in manifest.portable_governance_skills
            }
        else:
            parent_is_known = parent_id in known_t0
        if not parent_is_known:
            raise GovernanceSkillReleaseError(
                "instruction resource has unknown parent dependency: "
                f"{resource.resource_id}: {parent_id}",
                code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
            )
        source_payload = read_source_once(
            resource.source,
            context=f"instruction resource source {resource.resource_id}",
            error_code=GOVERNANCE_SKILL_INSTRUCTION_RESOURCE_INVALID,
        )
        instruction_resource_payloads.append(
            (
                resource,
                _select_instruction_resource_bytes(
                    resource,
                    source_payload,
                    project_root=project_root,
                    artifact_contracts_by_source=artifact_contracts_by_source,
                ),
            )
        )
    instruction_payloads_by_id = {
        resource.resource_id: payload
        for resource, payload in instruction_resource_payloads
    }

    requires_skill_artifact_contract = any(
        "soul:bestpractice_ai_facing_writing"
        in package_file.embedded_resource_ids
        for skill in manifest.portable_governance_skills
        for package_file in skill.package_files
        if package_file.source.endswith("/SKILL.md")
    )
    skill_artifact_adapter: Any | None = None
    if requires_skill_artifact_contract:
        skill_artifact_bindings = [
            binding
            for binding in artifact_contracts_by_source.values()
            if binding.owner_t0_layer_id == "the_skill_management"
        ]
        if len(skill_artifact_bindings) != 1:
            raise GovernanceSkillReleaseError(
                "Skill Management must register exactly one Skill artifact contract",
                code=GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
            )
        skill_artifact_binding = skill_artifact_bindings[0]
        skill_artifact_adapter = _load_registered_artifact_contract_adapter(
            project_root,
            skill_artifact_binding,
            read_source_once(
                skill_artifact_binding.source,
                context="Skill artifact contract source",
            ),
            error_code=GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
        )

    soul_resource_payloads: list[tuple[str, bytes]] = []
    for resource_id in required_resource_ids:
        resource_payload = read_source_once(
            SOUL_RESOURCE_PATHS[resource_id].as_posix(),
            context=f"Governance Skill Soul resource {resource_id}",
        )
        soul_resource_payloads.append((resource_id, resource_payload))

    for skill in manifest.portable_governance_skills:
        unknown_t0 = sorted(set(skill.required_t0_layer_ids) - known_t0)
        if unknown_t0:
            raise GovernanceSkillReleaseError(
                f"Governance Skill requires unknown T0 layers: {skill.skill_id}: {unknown_t0}"
            )
        binding = bindings.get(skill.skill_id)
        binding_payload: bytes | None = None
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

        skill_source_payloads: dict[str, bytes] = {}
        skill_payload_rows: list[ValidatedSkillPackagePayload] = []
        raw_authority: object = None
        for package_file in skill.package_files:
            source_payload = read_source_once(
                package_file.source,
                context="Governance Skill package source",
            )
            actual_hash = _sha256_bytes(source_payload)
            if actual_hash != package_file.sha256:
                raise GovernanceSkillReleaseError(
                    f"Governance Skill package source hash mismatch: "
                    f"{package_file.source}; declared={package_file.sha256}; "
                    f"actual={actual_hash}"
                )
            skill_source_payloads[package_file.source] = source_payload
            for fragment in PORTABLE_SOURCE_FORBIDDEN_FRAGMENTS:
                if fragment in source_payload:
                    raise GovernanceSkillReleaseError(
                        "portable Governance Skill contains project-local identity: "
                        f"{package_file.source}: {fragment.decode('utf-8')}"
                    )
            folded_payload = _IDENTITY_SEPARATOR_PATTERN.sub(
                b"_", source_payload.lower()
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
                for match in _T0_PATH_PATTERN.findall(source_payload)
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
                    source_payload, source=package_file.source
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
                raw_authority = metadata.get("first_authority_ref", "")
            composed_source_payload = compose_governance_skill_package_file(
                source_payload,
                package_file.embedded_resource_ids,
                instruction_payloads_by_id,
            )
            if (
                package_relative == PurePosixPath("SKILL.md")
                and "soul:bestpractice_ai_facing_writing"
                in package_file.embedded_resource_ids
            ):
                assert skill_artifact_adapter is not None
                try:
                    skill_artifact_adapter.validate_artifact(
                        composed_source_payload,
                        embedded_resource_ids=package_file.embedded_resource_ids,
                    )
                except (AttributeError, TypeError, ValueError) as exc:
                    raise GovernanceSkillReleaseError(
                        "Governance authoring Skill violates the registered "
                        f"Skill artifact contract: {package_file.source}: {exc}",
                        code=GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID,
                    ) from exc
            if package_file.reviewer_module_id is not None:
                validate_reviewer_prompt_artifact(
                    composed_source_payload,
                    embedded_resource_ids=package_file.embedded_resource_ids,
                )
            projection_payload = source_payload
            if (
                package_relative == PurePosixPath("SKILL.md")
                and binding_payload is not None
            ):
                projection_payload = (
                    source_payload + b"\n" + binding_payload
                )
            skill_payload_rows.append(
                ValidatedSkillPackagePayload(
                    skill=skill,
                    package_file=package_file,
                    source_payload=source_payload,
                    composed_source_payload=composed_source_payload,
                    projection_payload=projection_payload,
                )
            )
        _validate_runtime_module_asset_declarations(
            skill.skill_id,
            skill.package_files,
            skill_source_payloads,
        )
        _validate_runtime_module_registration_closure(
            skill, skill_source_payloads
        )
        payloads.extend(skill_payload_rows)
        owner_rows.append((skill, raw_authority))

    for skill, raw_authority in owner_rows:
        try:
            authority = _validated_relative_path(
                raw_authority,
                required_prefix=TARGET_PREFIX,
                context=f"{skill.skill_id}.first_authority_ref",
            )
        except GovernanceSkillReleaseError as exc:
            raise GovernanceSkillReleaseError(
                str(exc), code=GOVERNANCE_SKILL_OWNER_INVALID
            ) from exc
        if authority != skill.accountable_owner_ref:
            raise GovernanceSkillReleaseError(
                "Governance Skill accountable owner differs from the "
                "hash-validated frontmatter: "
                f"{skill.skill_id}: manifest={skill.accountable_owner_ref}; "
                f"frontmatter={authority}",
                code=GOVERNANCE_SKILL_OWNER_INVALID,
            )
        try:
            authority_path = _resolve_without_symlink_escape(
                project_root, authority
            )
        except GovernanceSkillReleaseError as exc:
            raise GovernanceSkillReleaseError(
                str(exc), code=GOVERNANCE_SKILL_OWNER_INVALID
            ) from exc
        if not authority_path.is_file():
            raise GovernanceSkillReleaseError(
                "Governance Skill authority does not resolve: "
                f"{skill.skill_id}: {authority}",
                code=GOVERNANCE_SKILL_OWNER_INVALID,
            )
        authority_t0 = known_t0_contracts.get(authority)
        if authority_t0 not in skill.required_t0_layer_ids:
            raise GovernanceSkillReleaseError(
                "Governance Skill first authority is outside its declared "
                f"T0 closure: {skill.skill_id}: {authority}",
                code=GOVERNANCE_SKILL_OWNER_INVALID,
            )

    return (
        tuple(payloads),
        tuple(soul_resource_payloads),
        tuple(instruction_resource_payloads),
    )


def _validated_governance_skill_release_input(
    project_root: Path, manifest_path: Path | None = None
) -> GovernanceSkillValidatedInput:
    root = project_root.resolve()
    manifest = load_governance_skill_manifest(root, manifest_path)
    (
        payloads,
        soul_resource_payloads,
        instruction_resource_payloads,
    ) = _validated_source_payloads(
        root, manifest
    )
    return GovernanceSkillValidatedInput(
        manifest=manifest,
        source_payloads=payloads,
        soul_resource_payloads=soul_resource_payloads,
        instruction_resource_payloads=instruction_resource_payloads,
    )


def _check_validated_governance_skill_release(
    project_root: Path,
    validated_input: GovernanceSkillValidatedInput,
) -> GovernanceSkillReleaseReport:
    root = project_root.resolve()
    manifest = validated_input.manifest
    payloads = validated_input.source_payloads
    issues: list[GovernanceSkillReleaseIssue] = []
    projection_count = 0

    def resolve_for_report(
        relative_path: str,
        *,
        code: str,
        detail: str,
    ) -> Path | None:
        try:
            return _resolve_without_symlink_escape(root, relative_path)
        except GovernanceSkillReleaseError as exc:
            issues.append(
                GovernanceSkillReleaseIssue(
                    code=code,
                    path=relative_path,
                    detail=f"{detail}: {exc}",
                )
            )
            return None

    declared_targets_by_root: dict[str, set[str]] = {}
    for payload in payloads:
        skill = payload.skill
        package_file = payload.package_file
        if payload.source_payload != payload.composed_source_payload:
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_embedded_block_drift",
                    path=package_file.source,
                    detail=(
                        "embedded instruction resource differs from its "
                        f"declared canonical selection for {skill.skill_id}"
                    ),
                )
            )
        for projection in package_file.projections:
            projection_count += 1
            projection_root = (
                HOST_TARGET_PREFIXES[projection.host_id] / skill.skill_id
            ).as_posix()
            declared_targets_by_root.setdefault(projection_root, set()).add(
                projection.target
            )
            target_path = resolve_for_report(
                projection.target,
                code="governance_skill_projection_read_failed",
                detail="cannot resolve declared projection",
            )
            if target_path is None:
                continue
            if not target_path.is_file():
                issues.append(
                    GovernanceSkillReleaseIssue(
                        code="governance_skill_projection_missing",
                        path=projection.target,
                        detail=f"missing {projection.host_id} projection for {skill.skill_id}",
                    )
                )
                continue
            try:
                target_payload = target_path.read_bytes()
            except OSError as exc:
                issues.append(
                    GovernanceSkillReleaseIssue(
                        code="governance_skill_projection_read_failed",
                        path=projection.target,
                        detail=f"cannot read declared projection: {exc}",
                    )
                )
                continue
            if target_payload != payload.projection_payload:
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
        root_path = resolve_for_report(
            projection_root,
            code="governance_skill_projection_discovery_failed",
            detail="cannot resolve managed projection root",
        )
        if root_path is None:
            continue
        if not root_path.exists():
            continue
        try:
            actual_members = {
                path.relative_to(root).as_posix()
                for path in root_path.rglob("*")
                if path.is_file() or path.is_symlink()
            }
        except OSError as exc:
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_projection_discovery_failed",
                    path=projection_root,
                    detail=f"cannot inspect managed projection root: {exc}",
                )
            )
            continue
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
        source_root_path = resolve_for_report(
            source_root,
            code="governance_skill_source_discovery_failed",
            detail="cannot resolve managed source root",
        )
        if source_root_path is None:
            continue
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
        try:
            source_members = tuple(source_root_path.rglob("*"))
        except OSError as exc:
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_source_discovery_failed",
                    path=source_root,
                    detail=f"cannot inspect managed source root: {exc}",
                )
            )
            continue
        actual_sources = {
            path.relative_to(root).as_posix()
            for path in source_members
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
            for path in source_members
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
    source_prefix_path = resolve_for_report(
        SOURCE_PREFIX.as_posix(),
        code="governance_skill_source_discovery_failed",
        detail="cannot resolve portable source root",
    )
    if source_prefix_path is None:
        source_root_entries = []
    else:
        try:
            source_root_entries = sorted(
                source_prefix_path.iterdir(), key=lambda path: path.name
            )
        except OSError as exc:
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_source_discovery_failed",
                    path=SOURCE_PREFIX.as_posix(),
                    detail=f"cannot inspect portable source root: {exc}",
                )
            )
            source_root_entries = []
    for entry in source_root_entries:
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
    for forbidden_root in manifest.forbidden_projection_roots:
        forbidden_path = resolve_for_report(
            forbidden_root,
            code="governance_skill_projection_discovery_failed",
            detail="cannot resolve forbidden projection root",
        )
        if forbidden_path is None:
            continue
        if forbidden_path.exists():
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_forbidden_projection_present",
                    path=forbidden_root,
                    detail="forbidden governance projection remains discoverable",
                )
            )
    forbidden_identities = {
        PurePosixPath(forbidden_root).name
        for forbidden_root in manifest.forbidden_projection_roots
    }
    for host_root in HOST_TARGET_PREFIXES.values():
        host_path = resolve_for_report(
            host_root.as_posix(),
            code="governance_skill_projection_discovery_failed",
            detail="cannot resolve host projection root",
        )
        if host_path is None:
            continue
        if not host_path.exists():
            continue
        try:
            host_members = sorted(host_path.rglob("*"))
        except OSError as exc:
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_projection_discovery_failed",
                    path=host_root.as_posix(),
                    detail=f"cannot inspect host projection root: {exc}",
                )
            )
            continue
        for path in host_members:
            if not path.is_file() or path.is_symlink():
                continue
            try:
                payload = path.read_bytes()
            except OSError as exc:
                issues.append(
                    GovernanceSkillReleaseIssue(
                        code="governance_skill_projection_read_failed",
                        path=path.relative_to(root).as_posix(),
                        detail=f"cannot read host projection: {exc}",
                    )
                )
                continue
            for forbidden_identity in sorted(forbidden_identities):
                if _payload_references_identity(payload, forbidden_identity):
                    issues.append(
                        GovernanceSkillReleaseIssue(
                            code="governance_skill_forbidden_projection_reference",
                            path=path.relative_to(root).as_posix(),
                            detail=(
                                "active host Skill surface references forbidden "
                                f"governance identity: {forbidden_identity}"
                            ),
                        )
                    )
    return GovernanceSkillReleaseReport(
        skill_count=len(manifest.portable_governance_skills),
        projection_count=projection_count,
        issues=tuple(issues),
    )


def check_governance_skill_release(
    project_root: Path, manifest_path: Path | None = None
) -> GovernanceSkillReleaseReport:
    root = project_root.resolve()
    validated_input = _validated_governance_skill_release_input(
        root, manifest_path
    )
    return _check_validated_governance_skill_release(root, validated_input)


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
    validated_input = _validated_governance_skill_release_input(
        root, manifest_path
    )
    drifted_sources = [
        payload.package_file.source
        for payload in validated_input.source_payloads
        if payload.source_payload != payload.composed_source_payload
    ]
    if drifted_sources:
        raise GovernanceSkillReleaseError(
            "accepted Governance Skill sources contain stale embedded "
            f"instruction resources: {sorted(drifted_sources)}",
            code=GOVERNANCE_SKILL_EMBEDDED_RESOURCE_INVALID,
        )
    for payload in validated_input.source_payloads:
        package_file = payload.package_file
        for projection in package_file.projections:
            try:
                _write_bytes_atomically(
                    _resolve_without_symlink_escape(root, projection.target),
                    payload.projection_payload,
                )
            except (OSError, GovernanceSkillReleaseError) as exc:
                raise GovernanceSkillReleaseError(
                    f"cannot write Governance Skill projection: "
                    f"{projection.target}: {exc}",
                    code=GOVERNANCE_SKILL_PROJECTION_WRITE_FAILED,
                ) from exc
    return _check_validated_governance_skill_release(root, validated_input)


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
        print(f"{exc.code}: {exc.detail}", file=sys.stderr)
        return 2
    _print_report(report)
    return 0 if report.is_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
