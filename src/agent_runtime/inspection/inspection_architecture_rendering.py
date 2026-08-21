"""Render the code-owned Agent Runtime architecture registration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..registry.registry_architecture_registration import (
    RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS,
    RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS,
    RUNTIME_MIGRATION_DEBT_PATHS,
    RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS,
    RUNTIME_SOURCE_FILE_REGISTRATIONS,
    RUNTIME_SUPPORTING_PLANE_REGISTRATIONS,
    RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS,
    RUNTIME_STRUCTURAL_SOURCE_PATHS,
    validate_registry_architecture_registration,
    validate_runtime_architecture_registration,
)


ARCHITECTURE_PROJECTION_SCHEMA_VERSION = (
    "agent_runtime_architecture_projection_v4"
)


def build_runtime_architecture_projection(
    project_root: Path | None = None,
) -> dict[str, Any]:
    """Return the architecture map from either a wheel or source checkout.

    Registry-internal closure is always validated. Repository file and Design
    Contract presence are additionally validated only when ``project_root`` is
    supplied by a source-checkout audit.
    """

    source_root = project_root
    if source_root is None:
        candidate = Path(__file__).resolve().parents[3]
        if (candidate / "pyproject.toml").is_file() and (
            candidate / "designDoc"
        ).is_dir():
            source_root = candidate
    errors = (
        validate_registry_architecture_registration(source_root)
        if source_root is not None
        else validate_runtime_architecture_registration()
    )
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
        "logical_responsibilities": [
            {
                "responsibility_id": row.responsibility_id,
                "responsibility": row.responsibility,
                "owner_contract_ref": row.owner_contract_ref,
            }
            for row in RUNTIME_LOGICAL_RESPONSIBILITY_REGISTRATIONS
        ],
        "supporting_planes": [
            {
                "supporting_plane_id": row.supporting_plane_id,
                "purpose": row.purpose,
                "owner_contract_ref": row.owner_contract_ref,
            }
            for row in RUNTIME_SUPPORTING_PLANE_REGISTRATIONS
        ],
        "physical_source_directories": [
            {
                "source_directory_id": row.source_directory_id,
                "source_directory": row.source_directory,
                "purpose": row.purpose,
            }
            for row in RUNTIME_SOURCE_DIRECTORY_REGISTRATIONS
        ],
        "implementation_bindings": [
            {
                "implementation_binding_id": row.implementation_binding_id,
                "logical_responsibility_id": row.logical_responsibility_id,
                "technology_id": row.technology_id,
                "implementation_source_paths": list(
                    row.implementation_source_paths
                ),
            }
            for row in RUNTIME_IMPLEMENTATION_BINDING_REGISTRATIONS
        ],
        "target_source_files": [
            {
                "source_path": row.source_path,
                "logical_responsibility_id": row.logical_responsibility_id,
                "physical_directory": directories[row.source_directory_id],
                "subject": row.subject,
                "nominalized_action": row.nominalized_action,
                "owner_contract_ref": row.owner_contract_ref,
                "implementation_binding_id": row.implementation_binding_id,
            }
            for row in RUNTIME_SOURCE_FILE_REGISTRATIONS
        ],
        "supporting_source_files": [
            {
                "source_path": row.source_path,
                "supporting_plane_id": row.supporting_plane_id,
                "physical_directory": directories[row.source_directory_id],
                "subject": row.subject,
                "nominalized_action": row.nominalized_action,
                "owner_contract_ref": row.owner_contract_ref,
            }
            for row in RUNTIME_SUPPORTING_SOURCE_FILE_REGISTRATIONS
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
        f"| Supporting-plane implementation | `{len(projection['supporting_source_files'])}` |",
        f"| Structural package file | `{len(projection['structural_source_files'])}` |",
        f"| Explicit migration debt | `{len(projection['migration_debt_source_files'])}` |",
        "",
        "## Logical Responsibilities",
        "",
        "| Responsibility | Owns | Design owner |",
        "| --- | --- | --- |",
    ]
    for responsibility in projection["logical_responsibilities"]:
        lines.append(
            f"| `{responsibility['responsibility_id']}` | "
            f"{responsibility['responsibility']} | "
            f"`{responsibility['owner_contract_ref']}` |"
        )
    lines.extend(
        [
            "",
            "## Supporting Planes",
            "",
            "| Supporting plane | Purpose | Design owner |",
            "| --- | --- | --- |",
        ]
    )
    for supporting_plane in projection["supporting_planes"]:
        lines.append(
            f"| `{supporting_plane['supporting_plane_id']}` | "
            f"{supporting_plane['purpose']} | "
            f"`{supporting_plane['owner_contract_ref']}` |"
        )
    lines.extend(
        [
            "",
            "## Physical Source Directories",
            "",
            "| Directory ID | Physical path | Purpose |",
            "| --- | --- | --- |",
        ]
    )
    for directory in projection["physical_source_directories"]:
        lines.append(
            f"| `{directory['source_directory_id']}` | "
            f"`{directory['source_directory']}` | {directory['purpose']} |"
        )
    lines.extend(
        [
            "",
            "## Implementation Bindings",
            "",
            "| Binding | Logical responsibility | Technology | Sources |",
            "| --- | --- | --- | --- |",
        ]
    )
    for binding in projection["implementation_bindings"]:
        sources = "<br/>".join(
            f"`{path}`" for path in binding["implementation_source_paths"]
        )
        lines.append(
            f"| `{binding['implementation_binding_id']}` | "
            f"`{binding['logical_responsibility_id']}` | "
            f"`{binding['technology_id']}` | {sources} |"
        )
    lines.extend(
        [
            "",
            "## Target Source Files",
            "",
            "| Source | Logical responsibility | Physical directory | Implementation binding | Subject | Action | Design owner |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for source in projection["target_source_files"]:
        binding = source["implementation_binding_id"] or ""
        lines.append(
            f"| `{source['source_path']}` | "
            f"`{source['logical_responsibility_id']}` | "
            f"`{source['physical_directory']}` | `{binding}` | "
            f"`{source['subject']}` | "
            f"`{source['nominalized_action']}` | "
            f"`{source['owner_contract_ref']}` |"
        )
    lines.extend(
        [
            "",
            "## Supporting Source Files",
            "",
            "| Source | Supporting plane | Physical directory | Subject | Action | Design owner |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for source in projection["supporting_source_files"]:
        lines.append(
            f"| `{source['source_path']}` | "
            f"`{source['supporting_plane_id']}` | "
            f"`{source['physical_directory']}` | "
            f"`{source['subject']}` | "
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
