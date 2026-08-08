"""Provider-neutral projections of registered Runtime Schema Assets."""

from __future__ import annotations


def task_plane_output_schema(
    canonical_schema: dict[str, object],
) -> dict[str, object]:
    """Remove Runtime-only annotations from one registered Schema Asset."""

    hidden = {"$schema", "$id", "$comment", "title"}

    def project(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: project(item)
                for key, item in value.items()
                if key not in hidden
            }
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    projected = project(canonical_schema)
    if not isinstance(projected, dict):
        raise ValueError("registered output schema must remain one object")
    _validate_strict_schema_keyword_types(projected)
    return projected


def _validate_strict_schema_keyword_types(schema: dict[str, object]) -> None:
    """Reject ambiguous keyword and type pairs before provider invocation."""

    def declares_type(node: dict[str, object], expected: str) -> bool:
        declared = node.get("type")
        return declared == expected or (
            isinstance(declared, list) and expected in declared
        )

    def walk(value: object, path: str) -> None:
        if isinstance(value, dict):
            if any(
                keyword in value
                for keyword in ("properties", "required", "additionalProperties")
            ) and not declares_type(value, "object"):
                raise ValueError(
                    f"strict output schema needs type object at {path}"
                )
            if any(
                keyword in value
                for keyword in ("items", "minItems", "maxItems", "uniqueItems")
            ) and not declares_type(value, "array"):
                raise ValueError(
                    f"strict output schema needs type array at {path}"
                )
            for key, item in value.items():
                walk(item, f"{path}/{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}/{index}")

    walk(schema, "#")


__all__ = ["task_plane_output_schema"]
