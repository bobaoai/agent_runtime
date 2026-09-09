from __future__ import annotations

from copy import deepcopy

import pytest

from agent_runtime.invocation.invocation_schema_projection import (
    claude_native_output_schema,
    codex_native_output_schema,
    task_plane_output_schema,
)
from agent_runtime.foundation.foundation_json_schema_validation import (
    validate_json_document_against_schema,
)
from agent_runtime.registry.registry_module_authoring import (
    _validate_reviewer_output_schema,
)


def schema(schema_ref: str = "schema:example_reviewer_output@v1") -> dict:
    string = {"type": "string", "minLength": 1}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema", "$id": schema_ref,
        "type": "object", "additionalProperties": False,
        "$defs": {
            "evidence": {"type": "object", "additionalProperties": False,
                "properties": {name: string for name in ("source_ref", "locator", "observation")},
                "required": ["source_ref", "locator", "observation"]},
            "finding": {"type": "object", "additionalProperties": False,
                "properties": {"finding_id": string,
                    "severity": {**string, "enum": ["block", "fix", "note"]},
                    "evidence": {"$ref": "#/$defs/evidence"}, "requirement": string,
                    "impact": string, "accountable_owner_ref": string, "required_change": string},
                "required": ["finding_id", "severity", "evidence", "requirement", "impact", "accountable_owner_ref", "required_change"]},
            "check_result": {"type": "object", "additionalProperties": False,
                "properties": {"check_id": {**string, "enum": ["scope", "prose"]},
                    "disposition": {**string, "enum": ["passed", "finding", "not_applicable", "not_run"]},
                    "assessment": string,
                    "finding_ids": {"type": "array", "uniqueItems": True, "items": string}},
                "required": ["check_id", "disposition", "assessment", "finding_ids"]}},
        "properties": {
            "verdict": {**string, "enum": ["passed", "non_pass", "blocked"]},
            "check_results": {"type": "array", "items": {"$ref": "#/$defs/check_result"}},
            "findings": {"type": "array", "items": {"$ref": "#/$defs/finding"}},
            "safe_next_step": string},
        "required": ["verdict", "check_results", "findings", "safe_next_step"],
    }


def test_different_module_specific_schemas_share_the_format() -> None:
    _validate_reviewer_output_schema(schema())
    _validate_reviewer_output_schema(schema("schema:other_reviewer_output@v9"))


@pytest.mark.parametrize("array_path", ("check_results", "findings", "finding_ids"))
def test_prefix_items_cannot_bypass_a_common_item_contract(array_path: str) -> None:
    value = schema()
    target = (
        value["$defs"]["check_result"]["properties"]["finding_ids"]
        if array_path == "finding_ids" else value["properties"][array_path]
    )
    target["prefixItems"] = [{}]
    with pytest.raises(ValueError, match="homogeneous array"):
        _validate_reviewer_output_schema(value)


def test_finding_must_be_an_object() -> None:
    value = schema()
    value["$defs"]["finding"].pop("type")
    with pytest.raises(ValueError, match="finding must be one closed object"):
        _validate_reviewer_output_schema(value)


def test_finding_class_is_not_required_but_declared_subject_fields_are_allowed() -> None:
    value = schema()
    finding = value["$defs"]["finding"]
    finding["properties"]["affected_candidate_ref"] = {"type": "string", "minLength": 1}
    finding["required"].append("affected_candidate_ref")
    _validate_reviewer_output_schema(value)


@pytest.mark.parametrize("missing", ("verdict", "check_results", "findings", "safe_next_step"))
def test_top_level_common_field_is_required(missing: str) -> None:
    value = schema()
    value["properties"].pop(missing)
    value["required"].remove(missing)
    with pytest.raises(ValueError, match="top-level"):
        _validate_reviewer_output_schema(value)


@pytest.mark.parametrize(
    ("target", "missing"),
    (("root", "verdict"), ("check_result", "assessment"),
     ("finding", "impact"), ("evidence", "locator")),
)
def test_common_required_entry_cannot_be_omitted(target: str, missing: str) -> None:
    value = schema()
    node = value if target == "root" else value["$defs"][target]
    node["required"].remove(missing)
    with pytest.raises(ValueError, match="required fields|common fields"):
        _validate_reviewer_output_schema(value)


@pytest.mark.parametrize(
    ("target", "missing"),
    (("check_result", "finding_ids"), ("finding", "requirement"),
     ("evidence", "observation")),
)
def test_nested_common_property_cannot_be_omitted(target: str, missing: str) -> None:
    value = schema()
    value["$defs"][target]["properties"].pop(missing)
    with pytest.raises(ValueError, match="fields|common fields"):
        _validate_reviewer_output_schema(value)


def test_legacy_judgment_wrapper_is_rejected() -> None:
    value = schema()
    value["properties"]["design_judgment"] = {"type": "object"}
    value["required"].append("design_judgment")
    with pytest.raises(ValueError, match="top-level"):
        _validate_reviewer_output_schema(value)


def test_common_schema_projects_for_both_providers() -> None:
    value = schema()
    _validate_reviewer_output_schema(value)
    task_schema = task_plane_output_schema(value)
    assert claude_native_output_schema(task_schema)["type"] == "object"
    assert codex_native_output_schema(task_schema)["type"] == "object"


@pytest.mark.parametrize("projector", (claude_native_output_schema, codex_native_output_schema))
@pytest.mark.parametrize(
    ("schema_ref", "check_ids"),
    (("schema:first_reviewer_output@v1", ["scope", "prose"]),
     ("schema:second_reviewer_output@v3", ["boundary", "prose"])),
)
def test_provider_projection_returns_to_its_module_specific_canonical_schema(
    projector, schema_ref: str, check_ids: list[str]
) -> None:
    value = schema(schema_ref)
    value["$defs"]["check_result"]["properties"]["check_id"]["enum"] = check_ids
    output = {
        "verdict": "passed",
        "check_results": [{"check_id": check_ids[0], "disposition": "passed",
            "assessment": "current candidate passes", "finding_ids": []}],
        "findings": [], "safe_next_step": "continue",
    }
    projected = projector(task_plane_output_schema(value))
    validate_json_document_against_schema(output, projected)
    validate_json_document_against_schema(output, value)

    duplicate_ids = deepcopy(output)
    duplicate_ids["check_results"][0]["finding_ids"] = ["same", "same"]
    validate_json_document_against_schema(duplicate_ids, projected)
    with pytest.raises(ValueError, match="does not satisfy"):
        validate_json_document_against_schema(duplicate_ids, value)


def test_canonical_schema_rejects_non_object_finding_payload() -> None:
    value = schema()
    output = {
        "verdict": "passed",
        "check_results": [{"check_id": "scope", "disposition": "passed", "assessment": "ok", "finding_ids": []}],
        "findings": [],
        "safe_next_step": "continue",
    }
    validate_json_document_against_schema(output, value)
    with pytest.raises(ValueError, match="does not satisfy"):
        validate_json_document_against_schema(output | {"findings": [None]}, value)


def test_wrong_common_enum_is_rejected() -> None:
    value = deepcopy(schema())
    value["properties"]["verdict"]["enum"] = ["passed", "failed"]
    with pytest.raises(ValueError, match="verdict enum"):
        _validate_reviewer_output_schema(value)


def test_non_string_enum_member_returns_value_error() -> None:
    value = schema()
    value["properties"]["verdict"]["enum"].append({})
    with pytest.raises(ValueError, match="verdict enum"):
        _validate_reviewer_output_schema(value)


@pytest.mark.parametrize("keyword", ("$schema", "$id", "$ref"))
@pytest.mark.parametrize(
    "path",
    (
        ("properties", "verdict"),
        ("$defs", "check_result", "properties", "disposition"),
        ("$defs", "finding", "properties", "severity"),
    ),
)
def test_common_enum_cannot_change_schema_or_reference_scope(
    keyword: str, path: tuple[str, ...]
) -> None:
    value = schema()
    node = value
    for segment in path:
        node = node[segment]
    node[keyword] = (
        "https://json-schema.org/draft/2020-12/schema"
        if keyword == "$schema"
        else "urn:scope-change"
    )

    with pytest.raises(ValueError):
        _validate_reviewer_output_schema(value)


def test_enum_order_and_redundant_min_length_do_not_change_the_format() -> None:
    value = schema()
    value["properties"]["verdict"] = {
        "type": "string", "enum": ["blocked", "non_pass", "passed"]
    }
    value["$defs"]["finding"]["properties"]["severity"] = {
        "type": "string", "enum": ["note", "fix", "block"]
    }
    value["$defs"]["check_result"]["properties"]["disposition"] = {
        "type": "string", "enum": ["not_run", "not_applicable", "finding", "passed"]
    }
    value["$defs"]["check_result"]["properties"]["finding_ids"]["items"] = {
        "type": "string"
    }
    value["properties"]["check_results"]["items"]["description"] = "Common check result."
    value["properties"]["findings"]["items"]["title"] = "Finding"
    value["$defs"]["finding"]["properties"]["evidence"]["$comment"] = "Exact evidence."
    _validate_reviewer_output_schema(value)


@pytest.mark.parametrize("definition", (None, "finding", "check_result", "evidence"))
def test_pattern_properties_cannot_reopen_common_object(definition: str | None) -> None:
    value = schema()
    target = value if definition is None else value["$defs"][definition]
    target["patternProperties"] = {"^design_judgment$": {}}
    with pytest.raises(ValueError, match="closed object"):
        _validate_reviewer_output_schema(value)


def test_pattern_properties_bypass_would_accept_extra_payload_without_format_check() -> None:
    value = schema()
    value["patternProperties"] = {"^design_judgment$": {}}
    output = {
        "verdict": "passed", "check_results": [], "findings": [],
        "safe_next_step": "continue", "design_judgment": {},
    }
    validate_json_document_against_schema(output, value)
    with pytest.raises(ValueError, match="closed object"):
        _validate_reviewer_output_schema(value)


def test_local_ref_cannot_be_rebased_by_items_schema() -> None:
    value = schema()
    value["properties"]["check_results"]["items"].update({
        "$id": "urn:review-local-array",
        "type": "null",
        "$defs": {"check_result": {}},
    })
    with pytest.raises(ValueError, match="local reference scope"):
        _validate_reviewer_output_schema(value)


def test_local_ref_cannot_be_rebased_by_array_schema() -> None:
    value = schema()
    value["properties"]["check_results"]["$id"] = "urn:review-array"
    with pytest.raises(ValueError, match="local reference scope"):
        _validate_reviewer_output_schema(value)


def test_local_evidence_ref_cannot_carry_assertion_siblings() -> None:
    value = schema()
    value["$defs"]["finding"]["properties"]["evidence"]["type"] = "null"
    with pytest.raises(ValueError, match="direct local ref"):
        _validate_reviewer_output_schema(value)
