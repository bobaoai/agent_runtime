#!/usr/bin/env python3
"""Project exact portable Governance Skills into supported Primary Agent hosts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
MANIFEST_VERSION = "governance_skill_manifest_v2"
PROJECT_BINDING_MANIFEST_VERSION = (
    "governance_skill_project_binding_manifest_v1"
)
SOURCE_PREFIX = PurePosixPath("09_soul/governance/skills")
PROJECT_BINDING_SOURCE_PREFIX = PurePosixPath("governance_bindings/skills")
HOST_TARGET_PREFIXES = {
    "claude": PurePosixPath(".claude/skills"),
    "codex": PurePosixPath(".agents/skills"),
}
PORTABLE_SOURCE_FORBIDDEN_FRAGMENTS = (
    b"/Users/",
    b"designDoc/temp/",
    b"trading_platform",
)


class GovernanceSkillReleaseError(ValueError):
    """Raised when the portable Governance Skill release is invalid."""


@dataclass(frozen=True)
class SkillProjection:
    host_id: str
    target: str


@dataclass(frozen=True)
class SkillSourceFile:
    source: str
    sha256: str
    projections: tuple[SkillProjection, ...]


@dataclass(frozen=True)
class PortableGovernanceSkill:
    skill_id: str
    required_t0_layer_ids: tuple[str, ...]
    primary_agent_entry_role: str
    primary_agent_entry_subject: str
    source_files: tuple[SkillSourceFile, ...]


@dataclass(frozen=True)
class GovernanceSkillManifest:
    manifest_version: str
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


def _known_t0_layer_ids(project_root: Path) -> set[str]:
    path = project_root / T0_MANIFEST_RELATIVE_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload["portable_t0_contracts"]
        return {row["t0_layer_id"] for row in rows}
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise GovernanceSkillReleaseError(
            f"cannot resolve portable T0 manifest: {path}"
        ) from exc


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
        {"manifest_version", "portable_governance_skills"},
        context="manifest",
    )
    if raw["manifest_version"] != MANIFEST_VERSION:
        raise GovernanceSkillReleaseError(
            f"unsupported manifest version: {raw['manifest_version']}"
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
                "primary_agent_entry_role",
                "primary_agent_entry_subject",
                "source_files",
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
        role = row["primary_agent_entry_role"]
        subject = row["primary_agent_entry_subject"]
        if not isinstance(role, str) or not role or not isinstance(subject, str) or not subject:
            raise GovernanceSkillReleaseError(
                f"{context} entry role and subject must be non-empty strings"
            )
        source_file_rows = row["source_files"]
        if not isinstance(source_file_rows, list) or not source_file_rows:
            raise GovernanceSkillReleaseError(
                f"{context}.source_files must be non-empty"
            )
        source_files: list[SkillSourceFile] = []
        for file_index, source_file in enumerate(source_file_rows):
            file_context = f"{context}.source_files[{file_index}]"
            if not isinstance(source_file, dict):
                raise GovernanceSkillReleaseError(
                    f"{file_context} must be an object"
                )
            _require_exact_keys(
                source_file,
                {"source", "sha256", "projections"},
                context=file_context,
            )
            source = _validated_relative_path(
                source_file["source"],
                required_prefix=SOURCE_PREFIX,
                context=f"{file_context}.source",
            )
            source_path = PurePosixPath(source)
            expected_source_prefix = SOURCE_PREFIX / skill_id
            try:
                source_relative = source_path.relative_to(expected_source_prefix)
            except ValueError as exc:
                raise GovernanceSkillReleaseError(
                    f"{file_context}.source must be inside the {skill_id} source root"
                ) from exc
            if not source_relative.parts:
                raise GovernanceSkillReleaseError(
                    f"{file_context}.source must identify one source file"
                )
            sha256 = source_file["sha256"]
            if (
                not isinstance(sha256, str)
                or len(sha256) != 64
                or any(char not in "0123456789abcdef" for char in sha256)
            ):
                raise GovernanceSkillReleaseError(
                    f"{file_context}.sha256 must be 64 lowercase hexadecimal characters"
                )
            projection_rows = source_file["projections"]
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
                        f"{skill_id} host projection"
                    )
                if PurePosixPath(*target_relative.parts[1:]) != source_relative:
                    raise GovernanceSkillReleaseError(
                        f"{projection_context}.target must preserve the source-relative "
                        f"path {source_relative.as_posix()}"
                    )
                if source_relative != PurePosixPath("SKILL.md") and host_id == "codex":
                    raise GovernanceSkillReleaseError(
                        f"{projection_context} cannot project Runtime Module assets "
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
            source_files.append(
                SkillSourceFile(
                    source=source,
                    sha256=sha256,
                    projections=tuple(projections),
                )
            )
        skill_sources = [source_file.source for source_file in source_files]
        skill_source_duplicates = sorted(
            {
                source
                for source in skill_sources
                if skill_sources.count(source) > 1
            }
        )
        if skill_source_duplicates:
            raise GovernanceSkillReleaseError(
                f"{context}.source_files repeat sources: {skill_source_duplicates}"
            )
        skill_source = (
            SOURCE_PREFIX / skill_id / "SKILL.md"
        ).as_posix()
        if skill_sources.count(skill_source) != 1:
            raise GovernanceSkillReleaseError(
                f"{context}.source_files must contain exactly one {skill_source}"
            )
        skills.append(
            PortableGovernanceSkill(
                skill_id=skill_id,
                required_t0_layer_ids=tuple(required_t0),
                primary_agent_entry_role=role,
                primary_agent_entry_subject=subject,
                source_files=tuple(source_files),
            )
        )
    for label, values in (
        ("skill_id", [skill.skill_id for skill in skills]),
        (
            "source",
            [
                source_file.source
                for skill in skills
                for source_file in skill.source_files
            ],
        ),
        (
            "target",
            [
                projection.target
                for skill in skills
                for source_file in skill.source_files
                for projection in source_file.projections
            ],
        ),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise GovernanceSkillReleaseError(
                f"duplicate Governance Skill {label}: {duplicates}"
            )
    return GovernanceSkillManifest(
        manifest_version=MANIFEST_VERSION,
        portable_governance_skills=tuple(skills),
    )


def _validated_source_payloads(
    project_root: Path, manifest: GovernanceSkillManifest
) -> tuple[tuple[PortableGovernanceSkill, SkillSourceFile, bytes], ...]:
    known_t0 = _known_t0_layer_ids(project_root)
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
    payloads: list[tuple[PortableGovernanceSkill, SkillSourceFile, bytes]] = []
    for skill in manifest.portable_governance_skills:
        unknown_t0 = sorted(set(skill.required_t0_layer_ids) - known_t0)
        if unknown_t0:
            raise GovernanceSkillReleaseError(
                f"Governance Skill requires unknown T0 layers: {skill.skill_id}: {unknown_t0}"
            )
        binding = bindings.get(skill.skill_id)
        for source_file in skill.source_files:
            source_path = _resolve_without_symlink_escape(
                project_root, source_file.source
            )
            try:
                payload = source_path.read_bytes()
            except OSError as exc:
                raise GovernanceSkillReleaseError(
                    f"cannot read Governance Skill source: "
                    f"{source_file.source}"
                ) from exc
            actual_hash = _sha256_bytes(payload)
            if actual_hash != source_file.sha256:
                raise GovernanceSkillReleaseError(
                    f"Governance Skill source hash mismatch: "
                    f"{source_file.source}; declared={source_file.sha256}; "
                    f"actual={actual_hash}"
                )
            for fragment in PORTABLE_SOURCE_FORBIDDEN_FRAGMENTS:
                if fragment in payload:
                    raise GovernanceSkillReleaseError(
                        "portable Governance Skill contains project-local identity: "
                        f"{source_file.source}: {fragment.decode('utf-8')}"
                    )
            if PurePosixPath(source_file.source).name == "SKILL.md":
                metadata = _frontmatter_scalars(
                    payload, source=source_file.source
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
                authority = metadata.get("first_authority_ref", "")
                if not authority or not (project_root / authority).is_file():
                    raise GovernanceSkillReleaseError(
                        "Governance Skill authority does not resolve: "
                        f"{skill.skill_id}: {authority}"
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
            payloads.append((skill, source_file, payload))
    return tuple(payloads)


def check_governance_skill_release(
    project_root: Path, manifest_path: Path | None = None
) -> GovernanceSkillReleaseReport:
    root = project_root.resolve()
    manifest = load_governance_skill_manifest(root, manifest_path)
    payloads = _validated_source_payloads(root, manifest)
    issues: list[GovernanceSkillReleaseIssue] = []
    projection_count = 0
    for skill, source_file, source_payload in payloads:
        for projection in source_file.projections:
            projection_count += 1
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
    for skill in manifest.portable_governance_skills:
        runtime_projection_root = (
            root
            / HOST_TARGET_PREFIXES["claude"]
            / skill.skill_id
            / "runtime_modules"
        )
        if not runtime_projection_root.exists():
            continue
        if (
            not runtime_projection_root.is_dir()
            or runtime_projection_root.is_symlink()
        ):
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_runtime_projection_invalid",
                    path=runtime_projection_root.relative_to(root).as_posix(),
                    detail="runtime_modules projection must be a real directory",
                )
            )
            continue
        expected_runtime_targets = {
            projection.target
            for source_file in skill.source_files
            for projection in source_file.projections
            if projection.host_id == "claude"
            and "runtime_modules" in PurePosixPath(projection.target).parts
        }
        actual_runtime_targets: set[str] = set()
        for entry in runtime_projection_root.rglob("*"):
            if entry.is_symlink():
                issues.append(
                    GovernanceSkillReleaseIssue(
                        code="governance_skill_runtime_projection_invalid",
                        path=entry.relative_to(root).as_posix(),
                        detail="runtime_modules projection cannot contain symlinks",
                    )
                )
            elif entry.is_file():
                actual_runtime_targets.add(entry.relative_to(root).as_posix())
        for orphan_path in sorted(actual_runtime_targets - expected_runtime_targets):
            issues.append(
                GovernanceSkillReleaseIssue(
                    code="governance_skill_runtime_projection_orphan",
                    path=orphan_path,
                    detail="runtime_modules projection file is not declared by source_files",
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
    for _skill, source_file, source_payload in payloads:
        for projection in source_file.projections:
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
