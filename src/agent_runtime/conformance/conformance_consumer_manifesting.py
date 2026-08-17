"""Freeze and compare symbol-level imports made by a downstream consumer."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
import warnings
from pathlib import Path
from typing import Any, Iterable


_PREDECESSOR_MODULE_PREFIXES = (
    "agent_runtime.contracts.execution_operation_definition",
    "agent_runtime.contracts.registry_workflow_definition",
    "agent_runtime.execution.execution_event_ingestion",
    "agent_runtime.execution.execution_operation_authorization",
    "agent_runtime.registry.registry_workflow_registration",
)
_SITE_DECISION_KEYS = frozenset(
    {"disposition", "disposition_reason", "disposition_source"}
)
_DISPOSITIONS = ("keep", "replace", "retire")
_DISPOSITION_SOURCES = ("derived_default", "owner_decision")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _disposition_for(imported_module: str) -> tuple[str, str]:
    if any(
        imported_module == prefix or imported_module.startswith(prefix + ".")
        for prefix in _PREDECESSOR_MODULE_PREFIXES
    ):
        return (
            "retire",
            "predecessor Runtime surface scheduled for removal",
        )
    return (
        "replace",
        "current import is frozen until the target responsibility-owned public surface is admitted",
    )


def _site_record(
    *,
    source_path: str,
    line_number: int,
    imported_module: str,
    imported_symbol: str | None,
    local_name: str | None,
) -> dict[str, Any]:
    identity_payload = {
        "source_path": source_path,
        "line_number": line_number,
        "imported_module": imported_module,
        "imported_symbol": imported_symbol,
        "local_name": local_name,
    }
    disposition, reason = _disposition_for(imported_module)
    return {
        "site_id": "consumer_import_"
        + hashlib.sha256(_canonical_json(identity_payload).encode("utf-8")).hexdigest()[:24],
        **identity_payload,
        "disposition": disposition,
        "disposition_reason": reason,
        "disposition_source": "derived_default",
    }


def _collect_source_sites(
    consumer_root: Path,
    source_path: Path,
) -> tuple[dict[str, Any], ...]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
    relative_path = source_path.relative_to(consumer_root).as_posix()
    sites: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("agent_runtime"):
                    continue
                sites.append(
                    _site_record(
                        source_path=relative_path,
                        line_number=node.lineno,
                        imported_module=alias.name,
                        imported_symbol=None,
                        local_name=alias.asname,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            if node.level or not node.module or not node.module.startswith(
                "agent_runtime"
            ):
                continue
            for alias in node.names:
                sites.append(
                    _site_record(
                        source_path=relative_path,
                        line_number=node.lineno,
                        imported_module=node.module,
                        imported_symbol=alias.name,
                        local_name=alias.asname,
                    )
                )
    return tuple(sites)


def build_downstream_consumer_manifest(
    *,
    consumer_id: str,
    consumer_root: Path,
    consumer_git_commit: str,
    source_roots: Iterable[str] = ("src", "tests"),
) -> dict[str, Any]:
    """Build one deterministic symbol-level Runtime import manifest."""

    consumer_root = consumer_root.resolve()
    normalized_roots = tuple(source_roots)
    sites: list[dict[str, Any]] = []
    for source_root in normalized_roots:
        root = consumer_root / source_root
        if not root.is_dir():
            continue
        for source_path in sorted(root.rglob("*.py")):
            if "__pycache__" in source_path.parts:
                continue
            sites.extend(_collect_source_sites(consumer_root, source_path))
    sites.sort(
        key=lambda row: (
            row["source_path"],
            row["line_number"],
            row["imported_module"],
            row["imported_symbol"] or "",
            row["local_name"] or "",
        )
    )
    disposition_counts = {
        disposition: sum(
            1 for site in sites if site["disposition"] == disposition
        )
        for disposition in _DISPOSITIONS
    }
    disposition_source_counts = {
        source: sum(
            1 for site in sites if site["disposition_source"] == source
        )
        for source in _DISPOSITION_SOURCES
    }
    return {
        "manifest_schema_version": "runtime_downstream_consumer_manifest_v2",
        "consumer_id": consumer_id,
        "consumer_git_commit": consumer_git_commit,
        "source_roots": list(normalized_roots),
        "sites": sites,
        "summary": {
            "site_count": len(sites),
            "source_file_count": len({site["source_path"] for site in sites}),
            "disposition_counts": disposition_counts,
            "disposition_source_counts": disposition_source_counts,
        },
    }


def validate_downstream_consumer_manifest(
    manifest: dict[str, Any],
    *,
    consumer_root: Path,
) -> tuple[str, ...]:
    """Return drift between one frozen manifest and the current consumer tree."""

    required_keys = {
        "manifest_schema_version",
        "consumer_id",
        "consumer_git_commit",
        "source_roots",
        "sites",
        "summary",
    }
    if set(manifest) != required_keys:
        return ("downstream consumer manifest has invalid top-level keys",)
    if manifest["manifest_schema_version"] != (
        "runtime_downstream_consumer_manifest_v2"
    ):
        return ("downstream consumer manifest has unsupported schema version",)
    rebuilt = build_downstream_consumer_manifest(
        consumer_id=manifest["consumer_id"],
        consumer_root=consumer_root,
        consumer_git_commit=manifest["consumer_git_commit"],
        source_roots=manifest["source_roots"],
    )
    manifest_sites = manifest["sites"]
    if not isinstance(manifest_sites, list):
        return ("downstream consumer manifest sites must be a list",)

    def derived_site(site: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in site.items()
            if key not in _SITE_DECISION_KEYS
        }

    if [derived_site(site) for site in rebuilt["sites"]] != [
        derived_site(site) for site in manifest_sites
    ]:
        return ("downstream Runtime import surface differs from frozen manifest",)

    errors: list[str] = []
    for rebuilt_site, site in zip(rebuilt["sites"], manifest_sites, strict=True):
        disposition = site.get("disposition")
        disposition_source = site.get("disposition_source")
        disposition_reason = site.get("disposition_reason")
        if disposition not in _DISPOSITIONS:
            errors.append(f"{site['site_id']}: invalid disposition")
        if disposition_source not in _DISPOSITION_SOURCES:
            errors.append(f"{site['site_id']}: invalid disposition source")
        if not isinstance(disposition_reason, str) or not disposition_reason:
            errors.append(f"{site['site_id']}: disposition reason is required")
        if disposition_source == "derived_default" and any(
            site.get(key) != rebuilt_site.get(key)
            for key in _SITE_DECISION_KEYS
        ):
            errors.append(
                f"{site['site_id']}: derived disposition differs from generator"
            )
    if errors:
        return tuple(errors)

    expected_summary = {
        "site_count": len(manifest_sites),
        "source_file_count": len(
            {site["source_path"] for site in manifest_sites}
        ),
        "disposition_counts": {
            disposition: sum(
                1 for site in manifest_sites
                if site["disposition"] == disposition
            )
            for disposition in _DISPOSITIONS
        },
        "disposition_source_counts": {
            source: sum(
                1 for site in manifest_sites
                if site["disposition_source"] == source
            )
            for source in _DISPOSITION_SOURCES
        },
    }
    if expected_summary != manifest["summary"]:
        return ("downstream Runtime import summary differs from frozen manifest",)
    return ()


def validate_downstream_consumer_retirement_readiness(
    manifest: dict[str, Any],
) -> tuple[str, ...]:
    """Reject facade retirement until every site has an owner decision."""

    undecided_site_ids = sorted(
        site["site_id"]
        for site in manifest.get("sites", ())
        if site.get("disposition_source") != "owner_decision"
    )
    if not undecided_site_ids:
        return ()
    return (
        "downstream Runtime consumer dispositions still require owner decisions: "
        + ", ".join(undecided_site_ids),
    )


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or check one downstream Runtime consumer manifest."
    )
    parser.add_argument("--consumer-id", required=True)
    parser.add_argument("--consumer-root", required=True, type=Path)
    parser.add_argument("--consumer-git-commit", required=True)
    parser.add_argument(
        "--source-root",
        action="append",
        dest="source_roots",
        default=None,
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path)
    action.add_argument("--check-manifest", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Build or reproduce one exact downstream import-surface artifact."""

    args = _parse_arguments(argv)
    if args.check_manifest is not None:
        manifest = json.loads(args.check_manifest.read_text(encoding="utf-8"))
        if args.consumer_id != manifest.get("consumer_id"):
            print("consumer id differs from frozen manifest", file=sys.stderr)
            return 1
        if args.consumer_git_commit != manifest.get("consumer_git_commit"):
            print("consumer Git commit differs from frozen manifest", file=sys.stderr)
            return 1
        if (
            args.source_roots is not None
            and args.source_roots != manifest.get("source_roots")
        ):
            print("consumer source roots differ from frozen manifest", file=sys.stderr)
            return 1
        errors = validate_downstream_consumer_manifest(
            manifest,
            consumer_root=args.consumer_root,
        )
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        return 0
    source_roots = tuple(args.source_roots or ("src", "tests"))
    manifest = build_downstream_consumer_manifest(
        consumer_id=args.consumer_id,
        consumer_root=args.consumer_root,
        consumer_git_commit=args.consumer_git_commit,
        source_roots=source_roots,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


__all__ = [
    "build_downstream_consumer_manifest",
    "main",
    "validate_downstream_consumer_manifest",
    "validate_downstream_consumer_retirement_readiness",
]


if __name__ == "__main__":
    raise SystemExit(main())
