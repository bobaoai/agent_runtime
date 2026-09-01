"""Provider-neutral projections of registered Runtime Schema Assets."""

from __future__ import annotations

from ..foundation import (
    iter_json_schema_nodes,
    resolve_local_schema_reference,
    strict_output_schema_projection,
    transform_json_schema_nodes,
)


def task_plane_output_schema(
    canonical_schema: dict[str, object],
) -> dict[str, object]:
    """Remove Runtime-only annotations from one registered Schema Asset."""

    return strict_output_schema_projection(canonical_schema)


__all__ = [
    "iter_json_schema_nodes",
    "resolve_local_schema_reference",
    "task_plane_output_schema",
    "transform_json_schema_nodes",
]
