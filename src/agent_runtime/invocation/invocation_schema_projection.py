"""Provider-neutral projections of registered Runtime Schema Assets."""

from __future__ import annotations

from ..foundation.foundation_schema_traversal import (
    iter_json_schema_nodes,
    resolve_local_schema_reference,
    transform_json_schema_nodes,
)


def task_plane_output_schema(
    canonical_schema: dict[str, object],
) -> dict[str, object]:
    """Remove Runtime-only annotations from one registered Schema Asset."""

    hidden = {"$schema", "$id", "$comment", "title"}

    projected = transform_json_schema_nodes(
        canonical_schema,
        lambda node: {
            key: value for key, value in node.items() if key not in hidden
        },
    )
    _validate_strict_schema_keyword_types(projected)
    return projected


def _validate_strict_schema_keyword_types(schema: dict[str, object]) -> None:
    """Reject ambiguous keyword and type pairs before provider invocation."""

    def declares_type(node: dict[str, object], expected: str) -> bool:
        declared = node.get("type")
        return declared == expected or (
            isinstance(declared, list) and expected in declared
        )

    for path, node in iter_json_schema_nodes(schema):
        if any(
            keyword in node
            for keyword in ("properties", "required", "additionalProperties")
        ) and not declares_type(node, "object"):
            raise ValueError(f"strict output schema needs type object at {path}")
        if any(
            keyword in node
            for keyword in ("items", "minItems", "maxItems", "uniqueItems")
        ) and not declares_type(node, "array"):
            raise ValueError(f"strict output schema needs type array at {path}")


__all__ = [
    "iter_json_schema_nodes",
    "resolve_local_schema_reference",
    "task_plane_output_schema",
    "transform_json_schema_nodes",
]
