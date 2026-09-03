#!/usr/bin/env python3
"""Skill-Management-owned Skill validation and Reviewer checklist projection."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ERROR_CODE = "GOVERNANCE_SKILL_SOURCE_CLOSURE_INVALID"
AUTHOR_SELF_CHECK_CANDIDATE_MISMATCH = "AUTHOR_SELF_CHECK_CANDIDATE_MISMATCH"
AUTHOR_SELF_CHECK_COVERAGE_INVALID = "AUTHOR_SELF_CHECK_COVERAGE_INVALID"
AUTHOR_SELF_CHECK_EVIDENCE_MISSING = "AUTHOR_SELF_CHECK_EVIDENCE_MISSING"
AUTHOR_SELF_CHECK_UNRESOLVED = "AUTHOR_SELF_CHECK_UNRESOLVED"
CONTRACT_PATH = Path(__file__).with_suffix(".json")
_H2_PATTERN = re.compile(r"^##\s+(\d+)\.\s+(.+?)\s*$", re.MULTILINE)
_H3_PATTERN = re.compile(r"^###\s+(\d+\.\d+)\s+(.+?)\s*$", re.MULTILINE)
_PLACEHOLDERS = {"todo", "tbd", "placeholder", "待补充", "待定"}


class SkillArtifactContractError(ValueError):
    """Raised when a Skill artifact cannot satisfy Skill Management."""

    code = ERROR_CODE


class AuthorSelfCheckValidationError(ValueError):
    """Raised when an invocation-local Author Self-Check cannot enter review."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True)
class SkillArtifactValidationResult:
    subject_sha256: str
    skill_id: str
    skill_class: str
    headings: tuple[str, ...]
    validator_ids: tuple[str, ...]


@dataclass(frozen=True)
class AuthorSelfCheckValidationResult:
    candidate_sha256: str
    required_check_ids: tuple[str, ...]
    row_count: int


def _require_exact_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise SkillArtifactContractError(
            f"{context} keys mismatch: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SkillArtifactContractError(f"cannot read Skill artifact contract: {path}") from exc
    if not isinstance(contract, dict):
        raise SkillArtifactContractError("Skill artifact contract root must be an object")
    _require_exact_keys(
        contract,
        {
            "$id",
            "contract_version",
            "artifact_contract_id",
            "owner_t0_layer_id",
            "artifact_kind",
            "frontmatter",
            "required_sections",
            "conditional_sections",
            "author_self_check",
            "reviewer_checklist",
        },
        "contract",
    )
    if contract["contract_version"] != "skill_artifact_contract_v3":
        raise SkillArtifactContractError("unsupported Skill artifact contract version")
    if contract["owner_t0_layer_id"] != "the_skill_management":
        raise SkillArtifactContractError("Skill artifact contract owner mismatch")
    frontmatter = contract["frontmatter"]
    if not isinstance(frontmatter, dict):
        raise SkillArtifactContractError("frontmatter contract must be an object")
    _require_exact_keys(
        frontmatter,
        {
            "required_top_level_fields",
            "required_metadata_fields",
            "primary_agent_development_fields",
            "skill_classes",
        },
        "frontmatter",
    )
    sections = contract["required_sections"]
    if not isinstance(sections, list) or len(sections) != 6:
        raise SkillArtifactContractError("Skill contract must define exactly six required sections")
    for index, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise SkillArtifactContractError(f"required_sections[{index - 1}] must be an object")
        expected = {"index", "heading"}
        if index == 4:
            expected.add("required_subsections")
        _require_exact_keys(section, expected, f"required_sections[{index - 1}]")
        if section["index"] != index or not isinstance(section["heading"], str) or not section["heading"]:
            raise SkillArtifactContractError("Skill required-section identity is invalid")
    conditional_sections = contract["conditional_sections"]
    if not isinstance(conditional_sections, list) or len(conditional_sections) != 1:
        raise SkillArtifactContractError(
            "Skill contract must define exactly one conditional section"
        )
    conditional = conditional_sections[0]
    if not isinstance(conditional, dict):
        raise SkillArtifactContractError("conditional_sections[0] must be an object")
    _require_exact_keys(
        conditional,
        {"resource_id", "index", "heading", "placement"},
        "conditional_sections[0]",
    )
    if conditional != {
        "resource_id": "soul:bestpractice_ai_facing_writing",
        "index": 0,
        "heading": "AI-facing Authoring Rules",
        "placement": "before_required_sections",
    }:
        raise SkillArtifactContractError(
            "Skill conditional-section identity is invalid"
        )
    _validated_author_self_check(contract["author_self_check"])
    _validated_checklist(contract["reviewer_checklist"])
    return contract


def _validated_author_self_check(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillArtifactContractError("author_self_check must be an object")
    _require_exact_keys(
        value,
        {
            "resource_id",
            "heading",
            "placement",
            "checklist_resource_by_skill_id",
            "instruction_lines",
        },
        "author_self_check",
    )
    if value["resource_id"] != "t0:skill_author_self_check":
        raise SkillArtifactContractError("Author Self-Check resource identity mismatch")
    if value["heading"] != "Author Self-Check" or value["placement"] != "inside_method":
        raise SkillArtifactContractError("Author Self-Check section identity is invalid")
    checklist_by_skill_id = value["checklist_resource_by_skill_id"]
    if (
        not isinstance(checklist_by_skill_id, dict)
        or len(checklist_by_skill_id) != 4
        or any(
            not isinstance(skill_id, str)
            or not skill_id
            or not isinstance(resource_id, str)
            or not resource_id
            for skill_id, resource_id in checklist_by_skill_id.items()
        )
        or len(set(checklist_by_skill_id.values())) != len(checklist_by_skill_id)
    ):
        raise SkillArtifactContractError("Author Self-Check checklist bindings are invalid")
    lines = value["instruction_lines"]
    if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
        raise SkillArtifactContractError("Author Self-Check instruction lines are invalid")
    if not lines or not any(line for line in lines):
        raise SkillArtifactContractError("Author Self-Check instruction is empty")
    return value


def _validated_checklist(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SkillArtifactContractError("reviewer_checklist must be an object")
    _require_exact_keys(
        value,
        {"resource_id", "intro_lines", "checks", "outro_lines"},
        "reviewer_checklist",
    )
    if value["resource_id"] != "t0:skill_candidate_review_checklist":
        raise SkillArtifactContractError("Skill checklist resource identity mismatch")
    for field in ("intro_lines", "outro_lines"):
        lines = value[field]
        if not isinstance(lines, list) or any(not isinstance(line, str) or not line for line in lines):
            raise SkillArtifactContractError(f"reviewer_checklist.{field} is invalid")
    checks = value["checks"]
    if not isinstance(checks, list) or len(checks) != 11:
        raise SkillArtifactContractError("Skill checklist must contain exactly eleven checks")
    check_ids: list[str] = []
    for index, check in enumerate(checks, start=1):
        if not isinstance(check, dict):
            raise SkillArtifactContractError(f"reviewer_checklist.checks[{index - 1}] must be an object")
        _require_exact_keys(check, {"order", "check_id", "required_result"}, f"reviewer_checklist.checks[{index - 1}]")
        if check["order"] != index:
            raise SkillArtifactContractError("Skill checklist order is not continuous")
        if not isinstance(check["check_id"], str) or not check["check_id"]:
            raise SkillArtifactContractError("Skill checklist check_id must be non-empty")
        if not isinstance(check["required_result"], str) or not check["required_result"]:
            raise SkillArtifactContractError("Skill checklist required_result must be non-empty")
        check_ids.append(check["check_id"])
    if len(set(check_ids)) != len(check_ids):
        raise SkillArtifactContractError("Skill checklist check_id values must be unique")
    return value


def render_reviewer_checklist(contract_path: Path = CONTRACT_PATH) -> bytes:
    checklist = _validated_checklist(load_contract(contract_path)["reviewer_checklist"])
    lines = [*checklist["intro_lines"], ""]
    for check in checklist["checks"]:
        lines.append(f"{check['order']}. `{check['check_id']}`：{check['required_result']}")
    lines.extend(("", *checklist["outro_lines"]))
    return ("\n".join(lines) + "\n").encode("utf-8")


def render_author_self_check_instruction(contract_path: Path = CONTRACT_PATH) -> bytes:
    instruction = _validated_author_self_check(
        load_contract(contract_path)["author_self_check"]
    )
    return ("\n".join(instruction["instruction_lines"]) + "\n").encode("utf-8")


def validate_author_self_check_submission(
    candidate_payload: bytes,
    *,
    declared_candidate_sha256: str,
    required_check_ids: tuple[str, ...],
    rows: tuple[dict[str, object], ...],
) -> AuthorSelfCheckValidationResult:
    """Validate one transient self-check without judging semantic evidence."""

    actual_sha256 = hashlib.sha256(candidate_payload).hexdigest()
    if declared_candidate_sha256 != actual_sha256:
        raise AuthorSelfCheckValidationError(
            AUTHOR_SELF_CHECK_CANDIDATE_MISMATCH,
            "declared candidate SHA-256 differs from the exact review candidate",
        )
    if (
        not required_check_ids
        or len(set(required_check_ids)) != len(required_check_ids)
        or any(not isinstance(check_id, str) or not check_id for check_id in required_check_ids)
    ):
        raise AuthorSelfCheckValidationError(
            AUTHOR_SELF_CHECK_COVERAGE_INVALID,
            "required check IDs must be a unique non-empty sequence",
        )
    observed_check_ids: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {
            "check_id",
            "exact_evidence",
            "local_result",
            "unresolved_finding",
        }:
            raise AuthorSelfCheckValidationError(
                AUTHOR_SELF_CHECK_COVERAGE_INVALID,
                f"row {index} has an invalid field set",
            )
        check_id = row["check_id"]
        if not isinstance(check_id, str) or not check_id:
            raise AuthorSelfCheckValidationError(
                AUTHOR_SELF_CHECK_COVERAGE_INVALID,
                f"row {index} has no check_id",
            )
        observed_check_ids.append(check_id)
        if any(
            not isinstance(row[field], str) or not row[field].strip()
            for field in ("exact_evidence", "local_result")
        ):
            raise AuthorSelfCheckValidationError(
                AUTHOR_SELF_CHECK_EVIDENCE_MISSING,
                f"row {index} has empty exact evidence or local result",
            )
        unresolved = row["unresolved_finding"]
        if unresolved is not None and (
            not isinstance(unresolved, str) or unresolved.strip()
        ):
            raise AuthorSelfCheckValidationError(
                AUTHOR_SELF_CHECK_UNRESOLVED,
                f"row {index} declares an unresolved finding",
            )
    if tuple(observed_check_ids) != required_check_ids:
        raise AuthorSelfCheckValidationError(
            AUTHOR_SELF_CHECK_COVERAGE_INVALID,
            "self-check rows do not match the canonical required check sequence",
        )
    return AuthorSelfCheckValidationResult(
        candidate_sha256=actual_sha256,
        required_check_ids=required_check_ids,
        row_count=len(rows),
    )


def _frontmatter_scalars(text: str) -> tuple[dict[str, str], dict[str, str]]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        raise SkillArtifactContractError("Skill candidate lacks frontmatter")
    try:
        closing = lines[1:].index("---") + 1
    except ValueError as exc:
        raise SkillArtifactContractError("Skill frontmatter is unterminated") from exc
    top_level: dict[str, str] = {}
    metadata: dict[str, str] = {}
    in_metadata = False
    for line in lines[1:closing]:
        if line == "metadata:":
            top_level["metadata"] = "mapping"
            in_metadata = True
            continue
        if in_metadata and line.startswith("  ") and ":" in line:
            key, raw_value = line[2:].split(":", maxsplit=1)
            metadata[key.strip()] = raw_value.strip().strip("\"'")
            continue
        if line and not line[0].isspace() and ":" in line:
            in_metadata = False
            key, raw_value = line.split(":", maxsplit=1)
            top_level[key.strip()] = raw_value.strip().strip("\"'")
    return top_level, metadata


def validate_artifact(
    payload: bytes,
    *,
    embedded_resource_ids: tuple[str, ...] = (),
) -> SkillArtifactValidationResult:
    contract = load_contract()
    if any(not isinstance(item, str) or not item for item in embedded_resource_ids):
        raise SkillArtifactContractError(
            "embedded_resource_ids must contain non-empty strings"
        )
    if len(set(embedded_resource_ids)) != len(embedded_resource_ids):
        raise SkillArtifactContractError(
            "embedded_resource_ids must not contain duplicates"
        )
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillArtifactContractError("Skill candidate must be UTF-8") from exc
    if "\r" in text:
        raise SkillArtifactContractError("Skill candidate must use LF line endings")
    top_level, metadata = _frontmatter_scalars(text)
    frontmatter_contract = contract["frontmatter"]
    missing_top = sorted(set(frontmatter_contract["required_top_level_fields"]) - set(top_level))
    missing_metadata = sorted(set(frontmatter_contract["required_metadata_fields"]) - set(metadata))
    if missing_top or missing_metadata:
        raise SkillArtifactContractError(
            f"Skill frontmatter is incomplete: top={missing_top}, metadata={missing_metadata}"
        )
    skill_class = metadata["skill_class"]
    if skill_class not in frontmatter_contract["skill_classes"]:
        raise SkillArtifactContractError(f"unsupported Skill class: {skill_class}")
    if skill_class == "primary_agent_development":
        missing_primary = sorted(
            set(frontmatter_contract["primary_agent_development_fields"]) - set(metadata)
        )
        if missing_primary:
            raise SkillArtifactContractError(
                f"primary_agent_development metadata is incomplete: {missing_primary}"
            )
    for field in ("name", "description"):
        if not top_level[field]:
            raise SkillArtifactContractError(f"Skill frontmatter field is empty: {field}")

    matches = list(_H2_PATTERN.finditer(text))
    if not matches:
        raise SkillArtifactContractError("Skill candidate has no numbered level-two sections")
    numbers = [int(match.group(1)) for match in matches]
    conditional = contract["conditional_sections"][0]
    has_conditional_section = conditional["resource_id"] in embedded_resource_ids
    expected_numbers = (
        list(range(0, len(contract["required_sections"]) + 1))
        if has_conditional_section
        else list(range(1, len(contract["required_sections"]) + 1))
    )
    if numbers != expected_numbers:
        start = "zero" if has_conditional_section else "one"
        raise SkillArtifactContractError(
            f"Skill section numbering must be continuous from {start}"
        )
    headings = [match.group(2) for match in matches]
    required_headings = [section["heading"] for section in contract["required_sections"]]
    expected_headings = (
        [conditional["heading"], *required_headings]
        if has_conditional_section
        else required_headings
    )
    if headings != expected_headings:
        raise SkillArtifactContractError("Skill required sections are missing, renamed, duplicated, or out of order")
    if len(set(headings)) != len(headings):
        raise SkillArtifactContractError("Skill section headings must be unique")
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if not body or body.casefold() in _PLACEHOLDERS:
            raise SkillArtifactContractError(f"Skill section is empty or placeholder-only: {headings[index]}")

    required_subsections = contract["required_sections"][3]["required_subsections"]
    subsection_matches = list(_H3_PATTERN.finditer(text))
    observed_subsections = [
        (match.group(1), match.group(2)) for match in subsection_matches
    ]
    for subsection in required_subsections:
        expected = (subsection["index"], subsection["heading"])
        if observed_subsections.count(expected) != 1:
            raise SkillArtifactContractError(
                "Execution Contract subsection must occur exactly once: "
                f"{subsection['index']} {subsection['heading']}"
            )
    expected_order = [
        observed_subsections.index((item["index"], item["heading"]))
        for item in required_subsections
    ]
    if expected_order != sorted(expected_order):
        raise SkillArtifactContractError("Execution Contract subsections are out of order")
    author_self_check = _validated_author_self_check(contract["author_self_check"])
    self_check_resource_id = author_self_check["resource_id"]
    checklist_by_skill_id = author_self_check["checklist_resource_by_skill_id"]
    checklist_resource_ids = set(checklist_by_skill_id.values())
    has_self_check = self_check_resource_id in embedded_resource_ids
    declared_checklists = checklist_resource_ids.intersection(embedded_resource_ids)
    observed_self_check_matches = [
        match
        for match in subsection_matches
        if match.group(2) == author_self_check["heading"]
    ]
    if has_self_check:
        if len(observed_self_check_matches) != 1:
            raise SkillArtifactContractError(
                "Author Self-Check subsection must occur exactly once"
            )
        expected_checklist = checklist_by_skill_id.get(top_level["name"])
        if expected_checklist is None or declared_checklists != {expected_checklist}:
            raise SkillArtifactContractError(
                "Author Self-Check checklist does not match the registered Skill binding"
            )
        method_index = next(
            index
            for index, match in enumerate(matches)
            if match.group(1) == "6" and match.group(2) == "Method"
        )
        method_start = matches[method_index].end()
        method_end = (
            matches[method_index + 1].start()
            if method_index + 1 < len(matches)
            else len(text)
        )
        self_check_match = observed_self_check_matches[0]
        if not (
            self_check_match.group(1).startswith("6.")
            and method_start <= self_check_match.start() < method_end
        ):
            raise SkillArtifactContractError(
                "Author Self-Check must be inside the Method section"
            )
    elif declared_checklists or observed_self_check_matches:
        raise SkillArtifactContractError(
            "Author Self-Check section and resource declarations must be complete"
        )
    return SkillArtifactValidationResult(
        subject_sha256=hashlib.sha256(payload).hexdigest(),
        skill_id=top_level["name"],
        skill_class=skill_class,
        headings=tuple(headings),
        validator_ids=(
            "skill_utf8_and_line_endings",
            "skill_frontmatter_contract",
            "skill_required_section_schema",
            "skill_section_content",
            "skill_execution_contract_subsections",
            "skill_author_self_check_contract",
        ),
    )
