"""Provider-neutral assembly of the exact application-controlled model text."""

from __future__ import annotations

import hashlib
import json

from ..contracts.execution_module_definition import ModuleInputBinding
from ..foundation.foundation_schema_traversal import (
    resolve_local_schema_reference,
    transform_json_schema_nodes,
)
from .invocation_schema_projection import task_plane_output_schema


PROMPT_ONLY_JSON = "prompt_only_json"
NATIVE_STRUCTURED_OUTPUT = "native_structured_output"
OUTPUT_CONSTRAINT_MODES = frozenset(
    {PROMPT_ONLY_JSON, NATIVE_STRUCTURED_OUTPUT}
)
OUTPUT_SCHEMA_MARKER = "\n## Required Output Shape\n"
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


def validate_registered_output_schema(
    *,
    compiled_static_body: str,
    canonical_schema: dict[str, object],
) -> dict[str, object]:
    """Prove that one Prompt Bundle uses its bound Schema Asset projection."""

    registered_projection = task_plane_output_schema(canonical_schema)
    if provider_output_schema(compiled_static_body) != registered_projection:
        raise ValueError(
            "Prompt Bundle output shape differs from registered Schema Asset"
        )
    return registered_projection


def _validate_output_constraint_mode(value: str) -> None:
    if value not in OUTPUT_CONSTRAINT_MODES:
        raise ValueError(f"unknown output_constraint_mode: {value}")


def split_prompt_bundle_output_schema(
    compiled_static_body: str,
) -> tuple[str, dict[str, object]]:
    """Return task instructions and the one registered task-plane schema."""

    if type(compiled_static_body) is not str or not compiled_static_body.strip():
        raise ValueError("compiled_static_body must be non-empty text")
    if OUTPUT_SCHEMA_MARKER not in compiled_static_body:
        raise ValueError("Prompt Bundle lacks one Required Output Shape")
    instructions, schema_text = compiled_static_body.split(
        OUTPUT_SCHEMA_MARKER,
        1,
    )
    if OUTPUT_SCHEMA_MARKER in schema_text:
        raise ValueError("Prompt Bundle contains multiple Required Output Shapes")
    try:
        schema, end = json.JSONDecoder().raw_decode(schema_text.lstrip())
    except json.JSONDecodeError as exc:
        raise ValueError("Prompt Bundle output schema is invalid JSON") from exc
    trailing = schema_text.lstrip()[end:].strip()
    if trailing:
        raise ValueError("Prompt Bundle contains text after Required Output Shape")
    if not isinstance(schema, dict):
        raise ValueError("Prompt Bundle output schema must be one JSON object")
    if not instructions.strip():
        raise ValueError("Prompt Bundle task instructions are empty")
    return instructions.rstrip(), schema


def provider_output_schema(
    compiled_static_body: str,
) -> dict[str, object]:
    """Return a detached provider projection source for native output control."""

    _instructions, schema = split_prompt_bundle_output_schema(
        compiled_static_body
    )
    return schema


def codex_native_output_schema(
    registered_output_schema: dict[str, object],
) -> dict[str, object]:
    """Project the registered task schema onto Codex native Structured Outputs.

    OpenAI accepts only a JSON Schema subset for strict Structured Outputs.
    Runtime keeps the complete registered task schema authoritative and
    validates the returned artifact against it after provider execution.
    """

    def make_nullable(value: dict[str, object]) -> dict[str, object]:
        """Represent one canonical optional property in Codex strict JSON."""

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

    projected = transform_json_schema_nodes(
        registered_output_schema,
        project_node,
    )
    if not isinstance(projected, dict) or projected.get("type") != "object":
        raise ValueError("Codex native output schema root must be one object")
    _validate_codex_native_schema(projected)
    return projected


def _validate_codex_native_schema(schema: dict[str, object]) -> None:
    """Fail before invocation for schema shapes outside the admitted subset."""

    def walk(node: object, path: str) -> None:
        if not isinstance(node, dict):
            return
        unknown = set(node) - _CODEX_NATIVE_SUPPORTED_SCHEMA_KEYS
        if unknown:
            rendered = ", ".join(sorted(unknown))
            raise ValueError(
                f"Codex native output schema has unsupported keys at {path}: "
                f"{rendered}"
            )
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict):
                raise ValueError(f"Codex schema properties must be an object at {path}")
            required = node.get("required")
            if not isinstance(required, list) or required != list(properties):
                raise ValueError(
                    f"Codex schema must require every ordered property at {path}"
                )
            if node.get("additionalProperties") is not False:
                raise ValueError(
                    f"Codex schema object must forbid additional properties at {path}"
                )
            for name, child in properties.items():
                walk(child, f"{path}/properties/{name}")
        definitions = node.get("$defs")
        if definitions is not None:
            if not isinstance(definitions, dict):
                raise ValueError(f"Codex schema $defs must be an object at {path}")
            for name, child in definitions.items():
                walk(child, f"{path}/$defs/{name}")
        if "items" in node:
            walk(node["items"], f"{path}/items")
        alternatives = node.get("anyOf")
        if alternatives is not None:
            if not isinstance(alternatives, list) or not alternatives:
                raise ValueError(f"Codex schema anyOf must be non-empty at {path}")
            for index, child in enumerate(alternatives):
                walk(child, f"{path}/anyOf/{index}")

    walk(schema, "#")


def normalize_codex_native_output(
    *,
    payload: dict[str, object],
    canonical_schema: dict[str, object],
) -> dict[str, object]:
    """Remove Adapter-only null placeholders before canonical validation.

    Codex strict Structured Outputs requires every declared object property to
    be present.  The native schema projection therefore represents canonical
    optional properties as nullable.  This inverse projection removes only
    those synthetic nulls.  Required canonical nulls remain part of the
    committed output.
    """

    def canonical_schema_allows_null(
        schema: object,
        *,
        seen_refs: frozenset[str] = frozenset(),
    ) -> bool:
        """Return whether null is meaningful in the registered task schema."""

        if isinstance(schema, dict):
            reference = schema.get("$ref")
            if isinstance(reference, str):
                if reference in seen_refs:
                    # A reference cycle cannot introduce a new null allowance.
                    return False
                schema = resolve_local_schema_reference(
                    schema,
                    canonical_schema,
                    seen_refs=seen_refs,
                )
                seen_refs = seen_refs | {reference}
        if not isinstance(schema, dict):
            return False
        declared_type = schema.get("type")
        if declared_type == "null":
            return True
        if isinstance(declared_type, list) and "null" in declared_type:
            return True
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and None in enum_values:
            return True
        if schema.get("const", object()) is None:
            return True
        for keyword in ("anyOf", "oneOf"):
            alternatives = schema.get(keyword)
            if isinstance(alternatives, list) and any(
                canonical_schema_allows_null(option, seen_refs=seen_refs)
                for option in alternatives
            ):
                return True
        return False

    def normalize(value: object, schema: object) -> object:
        schema = resolve_local_schema_reference(schema, canonical_schema)
        if isinstance(value, list):
            item_schema = schema.get("items", {}) if isinstance(schema, dict) else {}
            return [normalize(item, item_schema) for item in value]
        if not isinstance(value, dict):
            return value
        schema_object = schema if isinstance(schema, dict) else {}
        required = schema_object.get("required", [])
        required_names = set(required) if isinstance(required, list) else set()
        properties = schema_object.get("properties", {})
        property_schemas = properties if isinstance(properties, dict) else {}
        normalized: dict[str, object] = {}
        for key, item in value.items():
            property_schema = property_schemas.get(key, {})
            if (
                item is None
                and key not in required_names
                and not canonical_schema_allows_null(property_schema)
            ):
                continue
            normalized[key] = normalize(item, property_schema)
        return normalized

    normalized = normalize(payload, canonical_schema)
    if not isinstance(normalized, dict):
        raise AssertionError("Codex normalized output root must remain an object")
    return normalized


def model_visible_static_instructions(
    *,
    compiled_static_body: str,
    output_constraint_mode: str,
) -> str:
    """Render the static text once for the selected output transport."""

    _validate_output_constraint_mode(output_constraint_mode)
    instructions, schema = split_prompt_bundle_output_schema(
        compiled_static_body
    )
    if output_constraint_mode == NATIVE_STRUCTURED_OUTPUT:
        return instructions
    return (
        instructions
        + OUTPUT_SCHEMA_MARKER
        + "\n"
        + json.dumps(
            schema,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def validate_prompt_output_constraint(
    *,
    prompt: str,
    output_constraint_mode: str,
) -> None:
    """Reject a Prompt Envelope that conflicts with its frozen output mode."""

    _validate_output_constraint_mode(output_constraint_mode)
    static_region = prompt.split("\n## Task Input\n", 1)[0]
    marker_count = static_region.count(OUTPUT_SCHEMA_MARKER)
    if output_constraint_mode == PROMPT_ONLY_JSON and marker_count != 1:
        raise ValueError(
            "prompt_only_json requires exactly one Prompt output schema"
        )
    if output_constraint_mode == NATIVE_STRUCTURED_OUTPUT and marker_count:
        raise ValueError(
            "native_structured_output forbids a Prompt output schema"
        )


def _prompt_only_completion_instruction(output_constraint_mode: str) -> str:
    if output_constraint_mode == PROMPT_ONLY_JSON:
        return (
            "Return exactly one JSON object matching the Required Output Shape. "
            "Do not add a Markdown code fence or explanatory prose."
        )
    return ""


def build_inline_provider_prompt(
    *,
    compiled_static_body: str,
    execution_specific_instructions: str,
    inputs: tuple[tuple[ModuleInputBinding, bytes], ...],
    output_constraint_mode: str,
) -> str:
    """Build one shared inline Prompt Envelope for every provider Adapter."""

    if type(execution_specific_instructions) is not str:
        raise ValueError("execution_specific_instructions must be text")
    sections = [
        model_visible_static_instructions(
            compiled_static_body=compiled_static_body,
            output_constraint_mode=output_constraint_mode,
        )
    ]
    if execution_specific_instructions.strip():
        sections.append(execution_specific_instructions.strip())
    input_sections: list[str] = []
    for item in inputs:
        if type(item) is not tuple or len(item) != 2:
            raise ValueError("each model input must be one binding/content tuple")
        binding, content = item
        if type(binding) is not ModuleInputBinding or type(content) is not bytes:
            raise ValueError("model input requires exact binding and bytes")
        binding.validate()
        if hashlib.sha256(content).hexdigest() != binding.input_sha256:
            raise ValueError("model input content hash mismatch")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("model-visible input must be UTF-8 text") from exc
        input_sections.append(f"### {binding.logical_name}\n{text}")
    if input_sections:
        sections.append("## Task Input\n\n" + "\n\n".join(input_sections))
    completion = _prompt_only_completion_instruction(output_constraint_mode)
    if completion:
        sections.append(completion)
    return "\n\n".join(sections).rstrip() + "\n"


def build_gateway_provider_prompt(
    *,
    compiled_static_body: str,
    gateway_tool_names: tuple[str, ...],
    output_constraint_mode: str,
) -> str:
    """Build shared initial text for governed Gateway-backed input delivery."""

    if not gateway_tool_names or any(
        type(name) is not str or not name for name in gateway_tool_names
    ):
        raise ValueError("gateway_tool_names must be non-empty names")
    tools = ", ".join(f"`{name}`" for name in gateway_tool_names)
    sections = [
        model_visible_static_instructions(
            compiled_static_body=compiled_static_body,
            output_constraint_mode=output_constraint_mode,
        ),
        (
            "Use the complete authorized semantic input available through "
            f"{tools} before making the requested judgment."
        ),
    ]
    completion = _prompt_only_completion_instruction(output_constraint_mode)
    if completion:
        sections.append(completion)
    return "\n\n".join(sections).rstrip() + "\n"


__all__ = [
    "NATIVE_STRUCTURED_OUTPUT",
    "OUTPUT_CONSTRAINT_MODES",
    "OUTPUT_SCHEMA_MARKER",
    "PROMPT_ONLY_JSON",
    "build_gateway_provider_prompt",
    "build_inline_provider_prompt",
    "codex_native_output_schema",
    "model_visible_static_instructions",
    "normalize_codex_native_output",
    "provider_output_schema",
    "split_prompt_bundle_output_schema",
    "task_plane_output_schema",
    "validate_prompt_output_constraint",
    "validate_registered_output_schema",
]
