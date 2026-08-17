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
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MANIFEST_RELATIVE_PATH = Path(
    "09_soul/governance/governance_t0_manifest.json"
)
MANIFEST_VERSION = "governance_t0_manifest_v1"
SOURCE_PREFIX = PurePosixPath("09_soul/governance/t0")
TARGET_PREFIX = PurePosixPath("designDoc")
PORTABLE_SOURCE_FORBIDDEN_FRAGMENTS = (
    b"/Users/",
    b"bobaoai/agent_runtime",
    b"designDoc/temp/",
    b"src/",
    b"tests/",
    b"trading_platform",
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


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


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
        {"manifest_version", "charter", "portable_t0_contracts"},
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

    return GovernanceT0Manifest(
        manifest_version=MANIFEST_VERSION,
        charter_target=charter_target,
        portable_t0_contracts=tuple(contracts),
    )


def _validated_source_payloads(
    project_root: Path,
    manifest: GovernanceT0Manifest,
) -> tuple[tuple[PortableT0Contract, bytes], ...]:
    payloads: list[tuple[PortableT0Contract, bytes]] = []
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
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.apply:
            report = apply_governance_t0_release(args.project_root)
        else:
            report = check_governance_t0_release(args.project_root)
    except GovernanceT0ReleaseError as exc:
        print(f"invalid Hoveath T0 release: {exc}", file=sys.stderr)
        return 2
    _print_report(report)
    return 0 if report.is_clean else 1


if __name__ == "__main__":
    raise SystemExit(main())
