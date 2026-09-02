"""Provider-neutral projections of registered Runtime Schema Assets."""

from __future__ import annotations

from copy import deepcopy

from ..foundation import (
    iter_json_schema_nodes,
    resolve_local_schema_reference,
    strict_output_schema_projection,
    transform_json_schema_nodes,
)


_CLAUDE_NATIVE_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "additionalItems",
        "allOf",
        "anyOf",
        "contains",
        "contentEncoding",
        "contentMediaType",
        "contentSchema",
        "dependentRequired",
        "dependentSchemas",
        "else",
        "if",
        "maxContains",
        "minContains",
        "not",
        "oneOf",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
        "uniqueItems",
    }
)
_CLAUDE_NATIVE_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "const",
        "description",
        "enum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)
_CODEX_NATIVE_UNSUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "allOf",
        "oneOf",
        "not",
        "dependentRequired",
        "dependentSchemas",
        "if",
        "then",
        "else",
        "uniqueItems",
    }
)
_CODEX_NATIVE_SUPPORTED_SCHEMA_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "anyOf",
        "const",
        "description",
        "enum",
        "format",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "multipleOf",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)
_SCHEMA_MAP_KEYWORDS = frozenset(
    {"$defs", "definitions", "dependentSchemas", "patternProperties", "properties"}
)
_SCHEMA_ARRAY_KEYWORDS = frozenset(
    {"allOf", "anyOf", "oneOf", "prefixItems"}
)
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


class NativeOutputSchemaProjectionError(ValueError):
    """The selected Adapter cannot represent one registered schema shape."""


def _positional_item_contract_ref(item: object) -> str:
    if not isinstance(item, dict):
        raise NativeOutputSchemaProjectionError(
            "each positional item must be one schema object"
        )
    direct_reference = item.get("$ref")
    if isinstance(direct_reference, str) and direct_reference.startswith("#/"):
        return direct_reference
    all_of = item.get("allOf")
    if isinstance(all_of, list):
        direct_refs = [
            child["$ref"]
            for child in all_of
            if isinstance(child, dict)
            and isinstance(child.get("$ref"), str)
            and child["$ref"].startswith("#/")
        ]
        if len(direct_refs) == 1:
            return direct_refs[0]
    raise NativeOutputSchemaProjectionError(
        "each positional item must declare one direct local item contract"
    )


def _project_provider_schema_subset(
    registered_output_schema: dict[str, object],
    *,
    unsupported_keys: frozenset[str],
) -> dict[str, object]:
    """Prune unsupported branches and relax each surviving finite tuple.

    Provider framing loses the position-specific constraints. Runtime retains
    and applies the complete registered schema after provider execution.
    """

    def walk(value: object) -> object:
        if isinstance(value, bool):
            return value
        if not isinstance(value, dict):
            return deepcopy(value)
        node = dict(value)
        prefix_items = node.get("prefixItems")
        if prefix_items is not None:
            if not isinstance(prefix_items, list) or not prefix_items:
                raise NativeOutputSchemaProjectionError(
                    "prefixItems must be a non-empty schema list"
                )
            if node.get("items") is not False:
                raise NativeOutputSchemaProjectionError(
                    "positional array projection requires an exact finite tuple"
                )
            item_refs: list[str] = []
            for item in prefix_items:
                item_refs.append(_positional_item_contract_ref(item))
            if len(set(item_refs)) != 1:
                raise NativeOutputSchemaProjectionError(
                    "positional items do not share one local item contract"
                )
            node.pop("prefixItems")
            node["items"] = {"$ref": item_refs[0]}

        projected: dict[str, object] = {}
        for key, item in node.items():
            if key in unsupported_keys:
                continue
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
        return projected

    transformed = walk(registered_output_schema)
    if not isinstance(transformed, dict):
        raise AssertionError("JSON Schema root must remain an object")
    return transformed


def _prune_unreferenced_definitions(
    schema: dict[str, object],
) -> dict[str, object]:
    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        return schema

    def referenced_definition_names(
        value: object,
        *,
        skip_definitions: bool,
    ) -> set[str]:
        names: set[str] = set()
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                names.add(reference.split("/", 3)[2])
            for key, child in value.items():
                if skip_definitions and key == "$defs":
                    continue
                names.update(
                    referenced_definition_names(
                        child,
                        skip_definitions=False,
                    )
                )
        elif isinstance(value, list):
            for child in value:
                names.update(
                    referenced_definition_names(
                        child,
                        skip_definitions=False,
                    )
                )
        return names

    reachable = referenced_definition_names(schema, skip_definitions=True)
    queue = list(reachable)
    while queue:
        name = queue.pop()
        definition = definitions.get(name)
        if definition is None:
            continue
        for dependency in referenced_definition_names(
            definition,
            skip_definitions=False,
        ):
            if dependency not in reachable:
                reachable.add(dependency)
                queue.append(dependency)

    projected = dict(schema)
    if reachable:
        projected["$defs"] = {
            name: definition
            for name, definition in definitions.items()
            if name in reachable
        }
    else:
        projected.pop("$defs")
    return projected


def _validate_native_schema_subset(
    schema: dict[str, object],
    *,
    provider_name: str,
    supported_keys: frozenset[str],
    require_all_properties: bool,
) -> None:
    for path, node in iter_json_schema_nodes(schema):
        unknown = set(node) - supported_keys
        if unknown:
            rendered = ", ".join(sorted(unknown))
            raise NativeOutputSchemaProjectionError(
                f"{provider_name} native output schema has unsupported keys "
                f"at {path}: {rendered}"
            )
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                raise NativeOutputSchemaProjectionError(
                    f"{provider_name} schema properties must be an object at {path}"
                )
            if node.get("additionalProperties") is not False:
                raise NativeOutputSchemaProjectionError(
                    f"{provider_name} schema must forbid additional properties "
                    f"at {path}"
                )
            if require_all_properties:
                required = node.get("required")
                if not isinstance(required, list) or required != list(properties):
                    raise NativeOutputSchemaProjectionError(
                        f"{provider_name} schema must require every ordered "
                        f"property at {path}"
                    )
        if "items" in node and isinstance(node["items"], bool):
            raise NativeOutputSchemaProjectionError(
                f"{provider_name} schema requires an item schema at {path}"
            )
        alternatives = node.get("anyOf")
        if alternatives is not None and (
            not isinstance(alternatives, list) or not alternatives
        ):
            raise NativeOutputSchemaProjectionError(
                f"{provider_name} schema anyOf must be non-empty at {path}"
            )


def claude_native_output_schema(
    registered_output_schema: dict[str, object],
) -> dict[str, object]:
    """Project the registered schema onto Claude native structured output."""

    projected = _project_provider_schema_subset(
        registered_output_schema,
        unsupported_keys=_CLAUDE_NATIVE_UNSUPPORTED_SCHEMA_KEYS,
    )
    projected = _prune_unreferenced_definitions(projected)
    if projected.get("type") != "object":
        raise NativeOutputSchemaProjectionError(
            "Claude native output schema root must be one object"
        )
    _validate_native_schema_subset(
        projected,
        provider_name="Claude",
        supported_keys=_CLAUDE_NATIVE_SUPPORTED_SCHEMA_KEYS,
        require_all_properties=False,
    )
    return projected


def codex_native_output_schema(
    registered_output_schema: dict[str, object],
) -> dict[str, object]:
    """Project the registered schema onto Codex native structured output."""

    def make_nullable(value: dict[str, object]) -> dict[str, object]:
        projected = dict(value)
        declared_type = projected.get("type")
        if isinstance(declared_type, str):
            projected["type"] = [declared_type, "null"]
            return projected
        if isinstance(declared_type, list):
            if "null" not in declared_type:
                projected["type"] = [*declared_type, "null"]
            return projected
        enum_values = projected.get("enum")
        if isinstance(enum_values, list):
            if None not in enum_values:
                projected["enum"] = [*enum_values, None]
            return projected
        any_of = projected.get("anyOf")
        if isinstance(any_of, list):
            if {"type": "null"} not in any_of:
                projected["anyOf"] = [*any_of, {"type": "null"}]
            return projected
        return {"anyOf": [projected, {"type": "null"}]}

    def project_node(node: dict[str, object]) -> dict[str, object]:
        projected = {
            key: item
            for key, item in node.items()
            if key not in _CODEX_NATIVE_UNSUPPORTED_SCHEMA_KEYS
        }
        properties = projected.get("properties")
        if isinstance(properties, dict):
            canonical_required = node.get("required", [])
            required_names = (
                set(canonical_required)
                if isinstance(canonical_required, list)
                else set()
            )
            for name, property_schema in tuple(properties.items()):
                if name in required_names or not isinstance(property_schema, dict):
                    continue
                properties[name] = make_nullable(property_schema)
            projected["required"] = list(properties)
            projected["additionalProperties"] = False
        return projected

    positional_projection = _project_provider_schema_subset(
        registered_output_schema,
        unsupported_keys=_CODEX_NATIVE_UNSUPPORTED_SCHEMA_KEYS,
    )
    projected = transform_json_schema_nodes(
        positional_projection,
        project_node,
    )
    projected = _prune_unreferenced_definitions(projected)
    if projected.get("type") != "object":
        raise NativeOutputSchemaProjectionError(
            "Codex native output schema root must be one object"
        )
    _validate_native_schema_subset(
        projected,
        provider_name="Codex",
        supported_keys=_CODEX_NATIVE_SUPPORTED_SCHEMA_KEYS,
        require_all_properties=True,
    )
    return projected


def task_plane_output_schema(
    canonical_schema: dict[str, object],
) -> dict[str, object]:
    """Remove Runtime-only annotations from one registered Schema Asset."""

    return strict_output_schema_projection(canonical_schema)


__all__ = [
    "NativeOutputSchemaProjectionError",
    "claude_native_output_schema",
    "codex_native_output_schema",
    "iter_json_schema_nodes",
    "resolve_local_schema_reference",
    "task_plane_output_schema",
    "transform_json_schema_nodes",
]
