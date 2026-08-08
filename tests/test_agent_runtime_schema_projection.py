from __future__ import annotations

import json

import pytest

from agent_runtime.invocation.invocation_prompt_assembly import (
    OUTPUT_SCHEMA_MARKER,
    codex_native_output_schema,
    normalize_codex_native_output,
)
from agent_runtime.invocation.invocation_schema_projection import (
    task_plane_output_schema,
)


def _schema_with_keyword_named_properties() -> dict[str, object]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Runtime output",
        "type": "object",
        "properties": {
            "items": {
                "title": "Items",
                "type": "array",
                "items": {"type": "string"},
            },
            "properties": {"type": "string"},
            "required": {"type": "string"},
            "title": {"type": "string"},
        },
        "required": ["items", "properties", "required", "title"],
        "additionalProperties": False,
        "$defs": {
            "items": {
                "type": "object",
                "properties": {
                    "additionalProperties": {"type": "boolean"},
                },
                "required": ["additionalProperties"],
                "additionalProperties": False,
            }
        },
    }


def _compiled_prompt(schema: dict[str, object]) -> str:
    return (
        "Produce one result."
        + OUTPUT_SCHEMA_MARKER
        + "\n"
        + json.dumps(schema, ensure_ascii=False, sort_keys=True)
    )


def test_task_projection_preserves_user_names_inside_schema_maps() -> None:
    projected = task_plane_output_schema(_schema_with_keyword_named_properties())

    assert "title" not in projected
    assert list(projected["properties"]) == [
        "items",
        "properties",
        "required",
        "title",
    ]
    assert "title" not in projected["properties"]["items"]
    assert "items" in projected["$defs"]


def test_codex_projection_does_not_treat_property_maps_as_schema_nodes() -> None:
    projected = codex_native_output_schema(
        _compiled_prompt(task_plane_output_schema(_schema_with_keyword_named_properties()))
    )

    assert list(projected["properties"]) == [
        "items",
        "properties",
        "required",
        "title",
    ]
    assert projected["properties"]["properties"] == {"type": "string"}
    assert projected["$defs"]["items"]["required"] == [
        "additionalProperties"
    ]


def test_claude_projection_does_not_treat_property_maps_as_schema_nodes() -> None:
    pytest.importorskip("claude_agent_sdk")
    from agent_runtime.invocation.invocation_claude_module_invocation import (
        _structured_output_format,
    )

    projected = _structured_output_format(
        _compiled_prompt(task_plane_output_schema(_schema_with_keyword_named_properties()))
    )["schema"]

    assert list(projected["properties"]) == [
        "items",
        "properties",
        "required",
        "title",
    ]
    assert projected["properties"]["properties"] == {"type": "string"}


def test_codex_normalization_resolves_local_ref_before_removing_nulls() -> None:
    canonical_schema = {
        "type": "object",
        "properties": {"result": {"$ref": "#/$defs/verdict"}},
        "required": ["result"],
        "$defs": {
            "verdict": {
                "type": "object",
                "properties": {
                    "score": {"type": ["integer", "null"]},
                    "note": {"type": "string"},
                },
                "required": ["score", "note"],
                "additionalProperties": False,
            }
        },
        "additionalProperties": False,
    }

    normalized = normalize_codex_native_output(
        payload={"result": {"score": None, "note": "fine"}},
        canonical_schema=canonical_schema,
    )

    assert normalized == {"result": {"score": None, "note": "fine"}}
