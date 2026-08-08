"""Render the code-owned Agent Runtime architecture registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..registry.registry_architecture_registration import (
    RUNTIME_MIGRATION_DEBT_PATHS,
    RUNTIME_PRODUCT_MODULE_REGISTRATIONS,
    RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS,
    RUNTIME_SOURCE_FILE_REGISTRATIONS,
    RUNTIME_STRUCTURAL_SOURCE_PATHS,
    validate_registry_architecture_registration,
)


ARCHITECTURE_PROJECTION_SCHEMA_VERSION = (
    "agent_runtime_architecture_projection_v2"
)


def build_runtime_architecture_projection(
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Return the validated target, structural, and migration-debt source map."""

    root = (project_root or Path(__file__).resolve().parents[3]).resolve()
    errors = validate_registry_architecture_registration(root)
    if errors:
        raise RuntimeError(
            "invalid Runtime architecture registration: " + "; ".join(errors)
        )
    directories = {
        row.source_directory_id: row.source_directory
        for row in RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS
    }
    return {
        "schema_version": ARCHITECTURE_PROJECTION_SCHEMA_VERSION,
        "product_modules": [
            {
                "module_id": row.module_id,
                "source_directory": directories[row.module_id],
                "responsibility": row.responsibility,
                "owner_contract_ref": row.owner_contract_ref,
            }
            for row in RUNTIME_PRODUCT_MODULE_REGISTRATIONS
        ],
        "target_source_files": [
            {
                "source_path": row.source_path,
                "module_id": row.module_id,
                "physical_directory": directories[row.source_directory_id],
                "subject": row.subject,
                "nominalized_action": row.nominalized_action,
                "owner_contract_ref": row.owner_contract_ref,
            }
            for row in RUNTIME_SOURCE_FILE_REGISTRATIONS
        ],
        "structural_source_files": list(RUNTIME_STRUCTURAL_SOURCE_PATHS),
        "migration_debt_source_files": list(RUNTIME_MIGRATION_DEBT_PATHS),
    }


def render_runtime_architecture_markdown() -> str:
    """Render the committed human projection without manual source inventory."""

    projection = build_runtime_architecture_projection()
    lines = [
        "# Generated Agent Runtime Architecture",
        "",
        (
            "> Generated from "
            "`src/agent_runtime/registry/registry_architecture_registration.py`. "
            "Do not edit by hand."
        ),
        "",
        "## Summary",
        "",
        "| Disposition | Files |",
        "| --- | ---: |",
        f"| Target implementation | `{len(projection['target_source_files'])}` |",
        f"| Structural package file | `{len(projection['structural_source_files'])}` |",
        f"| Explicit migration debt | `{len(projection['migration_debt_source_files'])}` |",
        "",
        "## Product Modules",
        "",
        "| Module | Source directory | Responsibility | Design owner |",
        "| --- | --- | --- | --- |",
    ]
    for module in projection["product_modules"]:
        lines.append(
            f"| `{module['module_id']}` | `{module['source_directory']}` | "
            f"{module['responsibility']} | `{module['owner_contract_ref']}` |"
        )
    lines.extend(
        [
            "",
            "## Target Source Files",
            "",
            "| Source | Logical module | Physical directory | Subject | Action | Design owner |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for source in projection["target_source_files"]:
        lines.append(
            f"| `{source['source_path']}` | `{source['module_id']}` | "
            f"`{source['physical_directory']}` | `{source['subject']}` | "
            f"`{source['nominalized_action']}` | "
            f"`{source['owner_contract_ref']}` |"
        )
    lines.extend(["", "## Explicit Migration Debt", ""])
    for source_path in projection["migration_debt_source_files"]:
        lines.append(f"- `{source_path}`")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "ARCHITECTURE_PROJECTION_SCHEMA_VERSION",
    "build_runtime_architecture_projection",
    "render_runtime_architecture_markdown",
]
