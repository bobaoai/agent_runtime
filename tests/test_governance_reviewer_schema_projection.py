from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from agent_runtime.invocation.invocation_schema_projection import (
    NativeOutputSchemaProjectionError,
    claude_native_output_schema,
    codex_native_output_schema,
    iter_json_schema_nodes,
    task_plane_output_schema,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REVIEWER_OUTPUT_SCHEMAS = tuple(
    sorted(
        REPO_ROOT.glob(
            ".claude/skills/*/runtime_modules/*/schemas/output.schema.json"
        )
    )
)


def test_registered_reviewer_schema_inventory_has_five_members() -> None:
    assert len(REVIEWER_OUTPUT_SCHEMAS) == 5


def _finite_checklist_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "checks": {
                "type": "array",
                "minItems": 2,
                "maxItems": 2,
                "prefixItems": [
                    {
                        "$ref": "#/$defs/check_result",
                        "properties": {"check_id": {"const": "first"}},
                    },
                    {
                        "$ref": "#/$defs/check_result",
                        "properties": {"check_id": {"const": "second"}},
                    },
                ],
                "items": False,
                "uniqueItems": True,
            }
        },
        "required": ["checks"],
        "additionalProperties": False,
        "$defs": {
            "check_result": {
                "type": "object",
                "properties": {
                    "check_id": {"enum": ["first", "second"]},
                    "assessment": {"type": "string"},
                },
                "required": ["check_id", "assessment"],
                "additionalProperties": False,
            }
        },
    }


@pytest.mark.parametrize(
    "schema_path",
    REVIEWER_OUTPUT_SCHEMAS,
    ids=lambda path: path.parents[1].name,
)
def test_every_registered_reviewer_schema_projects_for_claude_and_codex(
    schema_path: Path,
) -> None:
    canonical_schema = json.loads(schema_path.read_text(encoding="utf-8"))
    task_schema = task_plane_output_schema(canonical_schema)

    claude_schema = claude_native_output_schema(task_schema)
    codex_schema = codex_native_output_schema(task_schema)

    for projected in (claude_schema, codex_schema):
        assert projected["type"] == "object"
        assert not any(
            "prefixItems" in node
            for _path, node in iter_json_schema_nodes(projected)
        )
        assert not any(
            "uniqueItems" in node
            for _path, node in iter_json_schema_nodes(projected)
        )


def test_provider_projection_relaxes_position_but_canonical_validation_keeps_it(
) -> None:
    canonical_schema = _finite_checklist_schema()
    reversed_checks = {
        "checks": [
            {"check_id": "second", "assessment": "second first"},
            {"check_id": "first", "assessment": "first second"},
        ]
    }

    for projected in (
        claude_native_output_schema(canonical_schema),
        codex_native_output_schema(canonical_schema),
    ):
        assert not list(
            Draft202012Validator(projected).iter_errors(reversed_checks)
        )
    assert list(
        Draft202012Validator(canonical_schema).iter_errors(reversed_checks)
    )


def test_positional_projection_rejects_different_item_contracts() -> None:
    schema = _finite_checklist_schema()
    schema["$defs"]["other_result"] = schema["$defs"]["check_result"]
    schema["properties"]["checks"]["prefixItems"][1]["$ref"] = (
        "#/$defs/other_result"
    )

    for projector in (
        claude_native_output_schema,
        codex_native_output_schema,
    ):
        with pytest.raises(
            NativeOutputSchemaProjectionError,
            match="do not share one local item contract",
        ):
            projector(schema)


def test_positional_projection_rejects_nested_property_ref_as_item_contract() -> None:
    schema = _finite_checklist_schema()
    nested_item = {
        "type": "object",
        "properties": {
            "nested": {"$ref": "#/$defs/check_result"},
        },
    }
    schema["properties"]["checks"]["prefixItems"] = [
        nested_item,
        nested_item,
    ]

    for projector in (
        claude_native_output_schema,
        codex_native_output_schema,
    ):
        with pytest.raises(
            NativeOutputSchemaProjectionError,
            match="declare one direct local item contract",
        ):
            projector(schema)


def test_positional_projection_requires_finite_tuple_tail() -> None:
    schema = _finite_checklist_schema()
    schema["properties"]["checks"]["items"] = {
        "$ref": "#/$defs/check_result"
    }

    with pytest.raises(
        NativeOutputSchemaProjectionError,
        match="requires an exact finite tuple",
    ):
        claude_native_output_schema(schema)
