"""Freeze and compare symbol-level imports made by a downstream consumer."""

from __future__ import annotations

import ast
import hashlib
import json
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
        for disposition in ("keep", "replace", "retire")
    }
    return {
        "manifest_schema_version": "runtime_downstream_consumer_manifest_v1",
        "consumer_id": consumer_id,
        "consumer_git_commit": consumer_git_commit,
        "source_roots": list(normalized_roots),
        "sites": sites,
        "summary": {
            "site_count": len(sites),
            "source_file_count": len({site["source_path"] for site in sites}),
            "disposition_counts": disposition_counts,
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
        "runtime_downstream_consumer_manifest_v1"
    ):
        return ("downstream consumer manifest has unsupported schema version",)
    rebuilt = build_downstream_consumer_manifest(
        consumer_id=manifest["consumer_id"],
        consumer_root=consumer_root,
        consumer_git_commit=manifest["consumer_git_commit"],
        source_roots=manifest["source_roots"],
    )
    if rebuilt["sites"] != manifest["sites"]:
        return ("downstream Runtime import surface differs from frozen manifest",)
    if rebuilt["summary"] != manifest["summary"]:
        return ("downstream Runtime import summary differs from frozen manifest",)
    return ()


__all__ = [
    "build_downstream_consumer_manifest",
    "validate_downstream_consumer_manifest",
]
