from __future__ import annotations

import pytest

from agent_runtime.invocation.invocation_prompt_assembly import (
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
        task_plane_output_schema(_schema_with_keyword_named_properties())
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


def test_codex_projection_defers_one_of_to_canonical_validation() -> None:
    canonical_schema = {
        "type": "object",
        "properties": {
            "verdict": {"enum": ["accepted", "revision_required"]},
            "findings": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["verdict", "findings"],
        "additionalProperties": False,
        "oneOf": [
            {
                "properties": {
                    "verdict": {"const": "accepted"},
                    "findings": {"maxItems": 0},
                }
            },
            {
                "properties": {
                    "verdict": {"const": "revision_required"},
                    "findings": {"minItems": 1},
                }
            },
        ],
    }

    projected = codex_native_output_schema(canonical_schema)

    assert "oneOf" not in projected
    assert projected["properties"] == canonical_schema["properties"]


def test_claude_projection_does_not_treat_property_maps_as_schema_nodes() -> None:
    pytest.importorskip("claude_agent_sdk")
    from agent_runtime.invocation.invocation_claude_module_invocation import (
        _structured_output_format,
    )

    projected = _structured_output_format(
        task_plane_output_schema(_schema_with_keyword_named_properties())
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


def _recursive_tree_schema(leaf: dict[str, object]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {"tree": {"$ref": "#/$defs/node"}},
        "required": [],
        "additionalProperties": False,
        "$defs": {
            "node": {"anyOf": [{"$ref": "#/$defs/node"}, leaf]},
        },
    }


def test_codex_normalization_terminates_on_recursive_ref_cycle() -> None:
    normalized = normalize_codex_native_output(
        payload={"tree": None},
        canonical_schema=_recursive_tree_schema({"type": "string"}),
    )

    assert normalized == {}


def test_codex_normalization_keeps_null_on_nullable_recursive_ref() -> None:
    normalized = normalize_codex_native_output(
        payload={"tree": None},
        canonical_schema=_recursive_tree_schema({"type": "null"}),
    )

    assert normalized == {"tree": None}
