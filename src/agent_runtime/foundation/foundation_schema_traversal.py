"""Import-free traversal primitives for JSON Schema documents."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy


_SCHEMA_MAP_KEYWORDS = frozenset(
    {"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"}
)
_SCHEMA_ARRAY_KEYWORDS = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
_SCHEMA_SINGLE_KEYWORDS = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)


def transform_json_schema_nodes(
    schema: dict[str, object],
    transform: Callable[[dict[str, object]], dict[str, object]],
) -> dict[str, object]:
    """Transform schema nodes without interpreting container-map keys."""

    def walk(value: object) -> object:
        if isinstance(value, bool):
            return value
        if not isinstance(value, dict):
            return deepcopy(value)
        projected: dict[str, object] = {}
        for key, item in value.items():
            if key in _SCHEMA_MAP_KEYWORDS and isinstance(item, dict):
                projected[key] = {
                    name: walk(child) for name, child in item.items()
                }
            elif key in _SCHEMA_ARRAY_KEYWORDS and isinstance(item, list):
                projected[key] = [walk(child) for child in item]
            elif key in _SCHEMA_SINGLE_KEYWORDS:
                if key == "items" and isinstance(item, list):
                    projected[key] = [walk(child) for child in item]
                else:
                    projected[key] = walk(item)
            else:
                projected[key] = deepcopy(item)
        return transform(projected)

    transformed = walk(schema)
    if not isinstance(transformed, dict):
        raise AssertionError("JSON Schema root must remain an object")
    return transformed


def iter_json_schema_nodes(
    schema: dict[str, object],
) -> Iterator[tuple[str, dict[str, object]]]:
    """Yield only real schema nodes with deterministic JSON Pointer paths."""

    def walk(value: object, path: str) -> Iterator[tuple[str, dict[str, object]]]:
        if isinstance(value, bool) or not isinstance(value, dict):
            return
        yield path, value
        for key, item in value.items():
            if key in _SCHEMA_MAP_KEYWORDS and isinstance(item, dict):
                for name, child in item.items():
                    yield from walk(child, f"{path}/{key}/{name}")
            elif key in _SCHEMA_ARRAY_KEYWORDS and isinstance(item, list):
                for index, child in enumerate(item):
                    yield from walk(child, f"{path}/{key}/{index}")
            elif key in _SCHEMA_SINGLE_KEYWORDS:
                if key == "items" and isinstance(item, list):
                    for index, child in enumerate(item):
                        yield from walk(child, f"{path}/{key}/{index}")
                else:
                    yield from walk(item, f"{path}/{key}")

    yield from walk(schema, "#")


def resolve_local_schema_reference(
    schema: object,
    root_schema: dict[str, object],
    *,
    seen_refs: frozenset[str] = frozenset(),
) -> object:
    """Resolve one local JSON Pointer ref while preserving sibling keywords."""

    if not isinstance(schema, dict):
        return schema
    reference = schema.get("$ref")
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return schema
    if reference in seen_refs:
        return schema
    target: object = root_schema
    for raw_token in reference[2:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or token not in target:
            return schema
        target = target[token]
    resolved = resolve_local_schema_reference(
        target,
        root_schema,
        seen_refs=seen_refs | {reference},
    )
    if not isinstance(resolved, dict):
        return resolved
    siblings = {key: value for key, value in schema.items() if key != "$ref"}
    return {**resolved, **siblings}


def strict_output_schema_projection(
    canonical_schema: dict[str, object],
) -> dict[str, object]:
    """Project one registered Schema Asset into provider-neutral strict form."""

    hidden = {"$schema", "$id", "$comment", "title"}
    projected = transform_json_schema_nodes(
        canonical_schema,
        lambda node: {
            key: value for key, value in node.items() if key not in hidden
        },
    )

    def declares_type(node: dict[str, object], expected: str) -> bool:
        declared = node.get("type")
        return declared == expected or (
            isinstance(declared, list) and expected in declared
        )

    for path, node in iter_json_schema_nodes(projected):
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
    return projected


__all__ = [
    "iter_json_schema_nodes",
    "resolve_local_schema_reference",
    "strict_output_schema_projection",
    "transform_json_schema_nodes",
]
