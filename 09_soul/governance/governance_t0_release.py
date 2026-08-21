#!/usr/bin/env python3
"""Release the Hoveath portable T0 baseline into one project.

This module is intentionally stdlib-only. It copies exact portable Design
law projections and verifies drift. Portable sources may contain logical
surface references but no project-local implementation paths. The consuming
project owns its Charter, Registry, Code Projection, specializations, and
implementation validators.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MANIFEST_RELATIVE_PATH = Path(
    "09_soul/governance/governance_t0_manifest.json"
)
PROJECT_RELEASE_POLICY_RELATIVE_PATH = Path(
    "governance_bindings/governance_release_policy.json"
)
MANIFEST_VERSION = "governance_t0_manifest_v2"
PROJECT_RELEASE_POLICY_VERSION = "governance_release_policy_v2"
SOURCE_PREFIX = PurePosixPath("09_soul/governance/t0")
TARGET_PREFIX = PurePosixPath("designDoc")
GOVERNANCE_PACKAGE_ROOT = PurePosixPath("09_soul/governance")
GOVERNANCE_PACKAGE_ROOT_MEMBERS = {
    "README.md",
    "governance_t0_manifest.json",
    "governance_t0_release.py",
    "governance_skill_manifest.json",
    "governance_skill_release.py",
    "t0",
    "skills",
    "tests",
}
PORTABLE_SOURCE_FORBIDDEN_FRAGMENTS = (
    b"/Users/",
    b"09_soul/",
    b"designDoc/temp/",
    b"src/",
    b"tests/",
    b".venv/",
)
_IDENTITY_SEPARATOR_PATTERN = re.compile(rb"[-_ ]+")
_T0_REFERENCE_PATTERN = re.compile(
    rb"(?:designDoc/)?(the_[a-z0-9_]+)\.md"
)


class GovernanceT0ReleaseError(ValueError):
    """Raised when the portable release definition is invalid or unsafe."""


@dataclass(frozen=True)
class PortableT0Contract:
    t0_layer_id: str
    source: str
    target: str
    sha256: str


@dataclass(frozen=True)
class GovernanceT0Manifest:
    manifest_version: str
    charter_target: str
    retired_t0_targets: tuple[str, ...]
    portable_t0_contracts: tuple[PortableT0Contract, ...]


@dataclass(frozen=True)
class GovernanceT0ReleaseIssue:
    code: str
    path: str
    detail: str


@dataclass(frozen=True)
class GovernanceT0ReleaseReport:
    charter_target: str
    contract_count: int
    issues: tuple[GovernanceT0ReleaseIssue, ...]

    @property
    def is_clean(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class ProjectGovernanceReleasePolicy:
    forbidden_source_fragments: tuple[bytes, ...]
    retired_t0_targets: tuple[str, ...]


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _load_project_governance_release_policy(
    project_root: Path,
) -> ProjectGovernanceReleasePolicy:
    path = project_root / PROJECT_RELEASE_POLICY_RELATIVE_PATH
    if not path.exists():
        return ProjectGovernanceReleasePolicy((), ())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernanceT0ReleaseError(
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
        raise GovernanceT0ReleaseError(
            "unsupported project Governance release policy version: "
            f"{payload['schema_version']}"
        )
    rows = payload["forbidden_source_fragments"]
    if (
        not isinstance(rows, list)
        or any(not isinstance(item, str) or not item for item in rows)
        or len(set(rows)) != len(rows)
    ):
        raise GovernanceT0ReleaseError(
            "forbidden_source_fragments must be unique non-empty strings"
        )
    retired_rows = payload["retired_t0_targets"]
    if (
        not isinstance(retired_rows, list)
        or any(not isinstance(item, str) or not item for item in retired_rows)
        or len(set(retired_rows)) != len(retired_rows)
    ):
        raise GovernanceT0ReleaseError(
            "retired_t0_targets must be unique non-empty strings"
        )
    return ProjectGovernanceReleasePolicy(
        forbidden_source_fragments=tuple(
            item.encode("utf-8") for item in rows
        ),
        retired_t0_targets=tuple(
            _validated_relative_path(
                item,
                required_prefix=TARGET_PREFIX,
                context=f"project retired_t0_targets[{index}]",
            )
            for index, item in enumerate(retired_rows)
        )
    )


def _project_forbidden_source_fragments(project_root: Path) -> tuple[bytes, ...]:
    return _load_project_governance_release_policy(
        project_root
    ).forbidden_source_fragments


def _project_retired_t0_targets(project_root: Path) -> tuple[str, ...]:
    return _load_project_governance_release_policy(project_root).retired_t0_targets


def _require_exact_keys(
    payload: dict[str, Any],
    expected: set[str],
    *,
    context: str,
) -> None:
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise GovernanceT0ReleaseError(
            f"{context} keys mismatch: missing={missing}, extra={extra}"
        )


def _validated_relative_path(
    raw_path: object,
    *,
    required_prefix: PurePosixPath,
    context: str,
) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise GovernanceT0ReleaseError(f"{context} must be a non-empty string")
    if "\\" in raw_path:
        raise GovernanceT0ReleaseError(f"{context} must use POSIX separators")
    candidate = PurePosixPath(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts or str(candidate) != raw_path:
        raise GovernanceT0ReleaseError(
            f"{context} must be a normalized repository-relative path: {raw_path}"
        )
    try:
        candidate.relative_to(required_prefix)
    except ValueError as exc:
        raise GovernanceT0ReleaseError(
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
            raise GovernanceT0ReleaseError(
                f"managed path cannot traverse a symlink: {relative_path}"
            )
    resolved_parent = candidate.parent.resolve()
    try:
        resolved_parent.relative_to(root)
    except ValueError as exc:
        raise GovernanceT0ReleaseError(
            f"managed path escapes project root: {relative_path}"
        ) from exc
    return candidate


def load_governance_t0_manifest(
    project_root: Path,
    manifest_path: Path | None = None,
) -> GovernanceT0Manifest:
    """Load and structurally validate one installed Hoveath manifest."""

    root = project_root.resolve()
    resolved_manifest = manifest_path or root / MANIFEST_RELATIVE_PATH
    try:
        raw = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GovernanceT0ReleaseError(
            f"cannot read governance T0 manifest: {resolved_manifest}"
        ) from exc
    if not isinstance(raw, dict):
        raise GovernanceT0ReleaseError("manifest root must be an object")
    _require_exact_keys(
        raw,
        {
            "manifest_version",
            "charter",
            "retired_t0_targets",
            "portable_t0_contracts",
        },
        context="manifest",
    )
    if raw["manifest_version"] != MANIFEST_VERSION:
        raise GovernanceT0ReleaseError(
            f"unsupported manifest version: {raw['manifest_version']}"
        )

    charter = raw["charter"]
    if not isinstance(charter, dict):
        raise GovernanceT0ReleaseError("charter must be an object")
    _require_exact_keys(charter, {"mode", "target"}, context="charter")
    if charter["mode"] != "project_specific":
        raise GovernanceT0ReleaseError("charter mode must be project_specific")
    charter_target = _validated_relative_path(
        charter["target"],
        required_prefix=TARGET_PREFIX,
        context="charter.target",
    )

    retired_rows = raw["retired_t0_targets"]
    if (
        not isinstance(retired_rows, list)
        or any(not isinstance(item, str) or not item for item in retired_rows)
    ):
        raise GovernanceT0ReleaseError(
            "retired_t0_targets must be an array of non-empty strings"
        )
    retired_t0_targets = tuple(
        _validated_relative_path(
            item,
            required_prefix=TARGET_PREFIX,
            context=f"retired_t0_targets[{index}]",
        )
        for index, item in enumerate(retired_rows)
    )
    if len(set(retired_t0_targets)) != len(retired_t0_targets):
        raise GovernanceT0ReleaseError(
            "retired_t0_targets must not contain duplicates"
        )

    rows = raw["portable_t0_contracts"]
    if not isinstance(rows, list) or not rows:
        raise GovernanceT0ReleaseError(
            "portable_t0_contracts must be a non-empty array"
        )
    contracts: list[PortableT0Contract] = []
    for index, row in enumerate(rows):
        context = f"portable_t0_contracts[{index}]"
        if not isinstance(row, dict):
            raise GovernanceT0ReleaseError(f"{context} must be an object")
        _require_exact_keys(
            row,
            {"t0_layer_id", "source", "target", "sha256"},
            context=context,
        )
        t0_layer_id = row["t0_layer_id"]
        if not isinstance(t0_layer_id, str) or not t0_layer_id.startswith("the_"):
            raise GovernanceT0ReleaseError(
                f"{context}.t0_layer_id must be a canonical the_* identity"
            )
        source = _validated_relative_path(
            row["source"],
            required_prefix=SOURCE_PREFIX,
            context=f"{context}.source",
        )
        target = _validated_relative_path(
            row["target"],
            required_prefix=TARGET_PREFIX,
            context=f"{context}.target",
        )
        sha256 = row["sha256"]
        if (
            not isinstance(sha256, str)
            or len(sha256) != 64
            or any(char not in "0123456789abcdef" for char in sha256)
        ):
            raise GovernanceT0ReleaseError(
                f"{context}.sha256 must be 64 lowercase hexadecimal characters"
            )
        contracts.append(
            PortableT0Contract(
                t0_layer_id=t0_layer_id,
                source=source,
                target=target,
                sha256=sha256,
            )
        )

    for label, values in (
        ("t0_layer_id", [row.t0_layer_id for row in contracts]),
        ("source", [row.source for row in contracts]),
        ("target", [row.target for row in contracts]),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise GovernanceT0ReleaseError(
                f"duplicate portable contract {label}: {duplicates}"
            )
    if charter_target in {row.target for row in contracts}:
        raise GovernanceT0ReleaseError(
            "project-specific Charter cannot be a portable contract target"
        )
    active_targets = {row.target for row in contracts}
    overlap = sorted(active_targets & set(retired_t0_targets))
    if overlap:
        raise GovernanceT0ReleaseError(
            f"retired T0 targets overlap active contracts: {overlap}"
        )

    return GovernanceT0Manifest(
        manifest_version=MANIFEST_VERSION,
        charter_target=charter_target,
        retired_t0_targets=retired_t0_targets,
        portable_t0_contracts=tuple(contracts),
    )


def _validated_source_payloads(
    project_root: Path,
    manifest: GovernanceT0Manifest,
) -> tuple[tuple[PortableT0Contract, bytes], ...]:
    payloads: list[tuple[PortableT0Contract, bytes]] = []
    allowed_t0_references = {
        contract.target for contract in manifest.portable_t0_contracts
    } | {manifest.charter_target}
    project_forbidden_fragments = _project_forbidden_source_fragments(
        project_root
    )
    for contract in manifest.portable_t0_contracts:
        source_path = _resolve_without_symlink_escape(project_root, contract.source)
        try:
            payload = source_path.read_bytes()
        except OSError as exc:
            raise GovernanceT0ReleaseError(
                f"cannot read portable T0 source: {contract.source}"
            ) from exc
        actual_hash = _sha256_bytes(payload)
        if actual_hash != contract.sha256:
            raise GovernanceT0ReleaseError(
                f"portable T0 source hash mismatch: {contract.source}; "
                f"declared={contract.sha256}; actual={actual_hash}"
            )
        for fragment in PORTABLE_SOURCE_FORBIDDEN_FRAGMENTS:
            if fragment in payload:
                raise GovernanceT0ReleaseError(
                    "portable T0 source contains a project-local "
                    f"implementation path or identity: {contract.source}: "
                    f"{fragment.decode('utf-8')}"
                )
        folded_payload = _IDENTITY_SEPARATOR_PATTERN.sub(
            b"_", payload.lower()
        )
        for fragment in project_forbidden_fragments:
            folded_fragment = _IDENTITY_SEPARATOR_PATTERN.sub(
                b"_", fragment.lower()
            )
            if folded_fragment in folded_payload:
                raise GovernanceT0ReleaseError(
                    "portable T0 source contains a project-local "
                    f"implementation path or identity: {contract.source}: "
                    f"{fragment.decode('utf-8')}"
                )
        cited_t0_references = {
            f"designDoc/{match.decode('ascii')}.md"
            for match in _T0_REFERENCE_PATTERN.findall(payload)
        }
        unknown_t0_references = sorted(
            cited_t0_references - allowed_t0_references
        )
        if unknown_t0_references:
            raise GovernanceT0ReleaseError(
                "portable T0 source cites unknown or retired T0 contracts: "
                f"{contract.source}: {unknown_t0_references}"
            )
        payloads.append((contract, payload))
    return tuple(payloads)


def check_governance_t0_release(
    project_root: Path,
    manifest_path: Path | None = None,
) -> GovernanceT0ReleaseReport:
    """Return project Charter and portable-projection drift findings."""

    root = project_root.resolve()
    manifest = load_governance_t0_manifest(root, manifest_path)
    payloads = _validated_source_payloads(root, manifest)
    issues: list[GovernanceT0ReleaseIssue] = []

    charter_path = _resolve_without_symlink_escape(root, manifest.charter_target)
    if not charter_path.is_file():
        issues.append(
            GovernanceT0ReleaseIssue(
                code="project_charter_missing",
                path=manifest.charter_target,
                detail="the consuming project must supply its own Charter",
            )
        )
    else:
        charter_payload = charter_path.read_bytes()
        allowed_t0_references = {
            contract.target for contract in manifest.portable_t0_contracts
        } | {manifest.charter_target}
        cited_t0_references = {
            f"designDoc/{match.decode('ascii')}.md"
            for match in _T0_REFERENCE_PATTERN.findall(charter_payload)
        }
        unknown_t0_references = sorted(
            cited_t0_references - allowed_t0_references
        )
        for unknown_reference in unknown_t0_references:
            issues.append(
                GovernanceT0ReleaseIssue(
                    code="project_charter_unknown_t0_reference",
                    path=manifest.charter_target,
                    detail=(
                        "project Charter cites an unknown or retired T0 "
                        f"contract: {unknown_reference}"
                    ),
                )
            )

    for contract, source_payload in payloads:
        target_path = _resolve_without_symlink_escape(root, contract.target)
        if not target_path.is_file():
            issues.append(
                GovernanceT0ReleaseIssue(
                    code="portable_t0_target_missing",
                    path=contract.target,
                    detail=f"missing projection for {contract.t0_layer_id}",
                )
            )
            continue
        target_payload = target_path.read_bytes()
        if target_payload != source_payload:
            issues.append(
                GovernanceT0ReleaseIssue(
                    code="portable_t0_target_drift",
                    path=contract.target,
                    detail=(
                        f"projection differs from {contract.source}; "
                        f"expected={contract.sha256}; "
                        f"actual={_sha256_bytes(target_payload)}"
                    ),
                )
            )
    project_retired_targets = _project_retired_t0_targets(root)
    active_targets = {
        contract.target for contract in manifest.portable_t0_contracts
    }
    overlap = sorted(active_targets & set(project_retired_targets))
    if overlap:
        raise GovernanceT0ReleaseError(
            f"project retired T0 targets overlap active contracts: {overlap}"
        )
    effective_retired_targets = (
        manifest.retired_t0_targets + project_retired_targets
    )
    for retired_target in effective_retired_targets:
        retired_path = _resolve_without_symlink_escape(root, retired_target)
        if retired_path.exists():
            issues.append(
                GovernanceT0ReleaseIssue(
                    code="portable_t0_retired_target_present",
                    path=retired_target,
                    detail="retired portable T0 target remains discoverable",
                )
            )
    discovered_targets = {
        path.relative_to(root).as_posix()
        for path in (root / "designDoc").glob("the_*.md")
        if path.is_file() or path.is_symlink()
    }
    allowed_targets = (
        active_targets
        | {manifest.charter_target}
        | set(effective_retired_targets)
    )
    for undeclared_target in sorted(discovered_targets - allowed_targets):
        issues.append(
            GovernanceT0ReleaseIssue(
                code="portable_t0_undeclared_target",
                path=undeclared_target,
                detail="undeclared T0-like Design target remains discoverable",
            )
        )
    declared_sources = {
        contract.source for contract in manifest.portable_t0_contracts
    }
    source_root = _resolve_without_symlink_escape(
        root, SOURCE_PREFIX.as_posix()
    )
    discovered_sources = {
        path.relative_to(root).as_posix()
        for path in source_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    for undeclared_source in sorted(discovered_sources - declared_sources):
        issues.append(
            GovernanceT0ReleaseIssue(
                code="portable_t0_undeclared_source_member",
                path=undeclared_source,
                detail="portable T0 source root contains an undeclared member",
            )
        )

    package_root = _resolve_without_symlink_escape(
        root, GOVERNANCE_PACKAGE_ROOT.as_posix()
    )
    for entry in sorted(package_root.iterdir(), key=lambda item: item.name):
        if entry.name == "__pycache__":
            continue
        if entry.name not in GOVERNANCE_PACKAGE_ROOT_MEMBERS:
            issues.append(
                GovernanceT0ReleaseIssue(
                    code="governance_package_undeclared_root_member",
                    path=entry.relative_to(root).as_posix(),
                    detail="Governance package root contains an undeclared member",
                )
            )

    return GovernanceT0ReleaseReport(
        charter_target=manifest.charter_target,
        contract_count=len(payloads),
        issues=tuple(issues),
    )


def _write_bytes_atomically(target_path: Path, payload: bytes) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target_path.name}.",
        suffix=".tmp",
        dir=target_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, target_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def apply_governance_t0_release(
    project_root: Path,
    manifest_path: Path | None = None,
    *,
    allow_projection_drift: bool = False,
) -> GovernanceT0ReleaseReport:
    """Write exact portable projections, then return the drift report."""

    root = project_root.resolve()
    manifest = load_governance_t0_manifest(root, manifest_path)
    payloads = _validated_source_payloads(root, manifest)
    charter_path = _resolve_without_symlink_escape(root, manifest.charter_target)
    if not charter_path.is_file():
        raise GovernanceT0ReleaseError(
            f"project-specific Charter is required before T0 release: "
            f"{manifest.charter_target}"
        )
    preflight = check_governance_t0_release(root, manifest_path)
    drifted_targets = [
        issue.path
        for issue in preflight.issues
        if issue.code == "portable_t0_target_drift"
    ]
    if drifted_targets and not allow_projection_drift:
        raise GovernanceT0ReleaseError(
            "portable T0 projection drift must be reviewed before apply; "
            "use --allow-projection-drift only for an approved replacement: "
            f"{sorted(drifted_targets)}"
        )
    for contract, source_payload in payloads:
        target_path = _resolve_without_symlink_escape(root, contract.target)
        _write_bytes_atomically(target_path, source_payload)
    return check_governance_t0_release(root, manifest_path)


def _print_report(report: GovernanceT0ReleaseReport) -> None:
    print(
        f"Hoveath T0 release: {report.contract_count} portable contract(s); "
        f"charter={report.charter_target}"
    )
    if report.is_clean:
        print("clean")
        return
    for issue in report.issues:
        print(f"{issue.code}: {issue.path}: {issue.detail}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check or apply the Hoveath portable T0 release."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--allow-projection-drift",
        action="store_true",
        help="replace drifted T0 projections after explicit review",
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.apply:
            report = apply_governance_t0_release(
                args.project_root,
                allow_projection_drift=args.allow_projection_drift,
            )
        else:
            report = check_governance_t0_release(args.project_root)
    except GovernanceT0ReleaseError as exc:
        print(f"invalid Hoveath T0 release: {exc}", file=sys.stderr)
        return 2
    _print_report(report)
    return 0 if report.is_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
