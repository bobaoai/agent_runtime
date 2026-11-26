from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from agent_runtime.invocation.invocation_prompt_assembly import (
    normalize_codex_native_output,
)
from agent_runtime.invocation.invocation_schema_projection import (
    claude_native_output_schema,
    codex_native_output_schema,
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
    assert "$defs" not in projected


def test_codex_projection_preserves_referenced_keyword_named_definition() -> None:
    canonical_schema = _schema_with_keyword_named_properties()
    canonical_schema["properties"]["definition_ref"] = {
        "$ref": "#/$defs/items"
    }
    canonical_schema["required"].append("definition_ref")

    projected = codex_native_output_schema(
        task_plane_output_schema(canonical_schema)
    )

    assert projected["properties"]["definition_ref"] == {
        "$ref": "#/$defs/items"
    }
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


def test_codex_projection_defers_unique_items_to_canonical_validation() -> None:
    canonical_schema = {
        "type": "object",
        "properties": {
            "finding_ids": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string"},
            }
        },
        "required": ["finding_ids"],
        "additionalProperties": False,
    }

    projected = codex_native_output_schema(canonical_schema)

    assert "uniqueItems" not in projected["properties"]["finding_ids"]
    assert not list(
        Draft202012Validator(canonical_schema).iter_errors(
            {"finding_ids": ["same", "different"]}
        )
    )
    assert list(
        Draft202012Validator(canonical_schema).iter_errors(
            {"finding_ids": ["same", "same"]}
        )
    )


def test_codex_projection_rejects_empty_any_of() -> None:
    canonical_schema = {
        "type": "object",
        "properties": {
            "result": {"anyOf": []},
        },
        "required": ["result"],
        "additionalProperties": False,
    }

    with pytest.raises(ValueError, match="anyOf must be non-empty"):
        codex_native_output_schema(canonical_schema)


def test_claude_projection_does_not_treat_property_maps_as_schema_nodes() -> None:
    projected = claude_native_output_schema(
        task_plane_output_schema(_schema_with_keyword_named_properties())
    )

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


# --- Claude keeps supported anyOf through the same recursive projection ----------------------

def _summary_branch() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name", "count", "tags"],
        "additionalProperties": False,
    }


def _with_property(name: str, schema: dict[str, object], **extra) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["completed", "blocked"]},
            name: schema,
        },
        "required": ["status", name],
        "additionalProperties": False,
        **extra,
    }


@pytest.mark.parametrize("null_first", [False, True])
def test_claude_projection_keeps_a_nullable_object_with_its_required_fields(null_first) -> None:
    alternatives = [_summary_branch(), {"type": "null"}]
    canonical = _with_property("summary", {"anyOf": alternatives[::-1] if null_first else alternatives})
    projected = claude_native_output_schema(task_plane_output_schema(canonical))
    assert projected == canonical  # Nothing of the shape is erased.
    validator = Draft202012Validator(projected)
    assert validator.is_valid({"status": "completed", "summary": {"name": "a", "count": 1, "tags": []}})
    assert validator.is_valid({"status": "blocked", "summary": None})
    assert not validator.is_valid({"status": "completed", "summary": {"name": "a", "count": 1}})
    assert not validator.is_valid({"status": "completed", "summary": {"other": 1}})


def test_claude_projection_keeps_local_ref_and_two_object_alternatives() -> None:
    circle = {"type": "object", "properties": {"radius": {"type": "number"}},
              "required": ["radius"], "additionalProperties": False}
    square = {"type": "object", "properties": {"side": {"type": "number"}},
              "required": ["side"], "additionalProperties": False}
    canonical = {
        "type": "object",
        "properties": {
            "shape": {"anyOf": [circle, square]},
            "origin": {"anyOf": [{"$ref": "#/$defs/point"}, {"type": "null"}]},
        },
        "required": ["shape", "origin"],
        "additionalProperties": False,
        "$defs": {"point": {"type": "object", "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                            "required": ["x", "y"], "additionalProperties": False}},
    }
    projected = claude_native_output_schema(task_plane_output_schema(canonical))
    assert projected == canonical  # Both unions and the referenced definition reach Claude.


def test_claude_projection_still_prunes_unsupported_keywords_inside_kept_branches() -> None:
    branch = {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
    canonical = _with_property("labels", {"anyOf": [branch, {"type": "null"}]})
    projected = claude_native_output_schema(task_plane_output_schema(canonical))
    assert projected["properties"]["labels"] == {
        "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]}


def test_claude_projection_allows_annotations_beside_any_of() -> None:
    canonical = _with_property("summary", {"title": "Summary", "description": "Optional summary",
                                           "anyOf": [_summary_branch(), {"type": "null"}]})
    projected = claude_native_output_schema(task_plane_output_schema(canonical))
    assert projected["properties"]["summary"]["anyOf"][0]["required"] == ["name", "count", "tags"]


# Untyped properties/required are already refused by the strict task-plane projection, so the
# reachable combinations are anyOf beside type, alone or with the object keywords.
@pytest.mark.parametrize("siblings,named", [
    ({"type": "object"}, "type"),
    ({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"],
      "additionalProperties": False}, "properties"),
])
def test_claude_projection_refuses_any_of_beside_structural_keywords(siblings, named) -> None:
    canonical = _with_property("summary", {**siblings, "anyOf": [_summary_branch(), {"type": "null"}]})
    with pytest.raises(ValueError, match=f"cannot combine anyOf with .*{named}.* at #/properties/summary"):
        claude_native_output_schema(task_plane_output_schema(canonical))


def test_codex_projection_of_a_nullable_required_object_is_unchanged() -> None:
    canonical = _with_property("summary", {"anyOf": [_summary_branch(), {"type": "null"}]})
    projected = codex_native_output_schema(task_plane_output_schema(canonical))
    assert projected == canonical  # Every property is already required; Codex keeps anyOf as before.
