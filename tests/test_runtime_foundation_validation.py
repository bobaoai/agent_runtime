from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent_runtime.foundation.foundation_contract_validation import (
    format_utc_timestamp,
    format_usd_amount,
    parse_utc_timestamp,
    validate_bool,
    validate_id,
    validate_int,
    validate_opaque_ref,
    validate_sha256,
    validate_snake_case_name,
    validate_utc_timestamp,
    validate_usd_amount,
)
from agent_runtime.foundation.foundation_schema_traversal import (
    iter_json_schema_nodes,
    resolve_local_schema_reference,
    transform_json_schema_nodes,
)


def test_foundation_accepts_canonical_identifiers_and_references() -> None:
    validate_id("module_id", "module.alpha")
    validate_snake_case_name("field", "recorded_at_utc")
    validate_sha256("sha256", "a" * 64)
    validate_opaque_ref("content_ref", "ledger:content-alpha")


@pytest.mark.parametrize(
    ("validator", "value"),
    [
        (validate_id, True),
        (validate_snake_case_name, "Not_Snake"),
        (validate_sha256, "A" * 64),
        (validate_opaque_ref, "data:text/plain,secret"),
    ],
)
def test_foundation_rejects_noncanonical_values(validator, value) -> None:
    with pytest.raises(ValueError):
        validator("value", value)


def test_foundation_preserves_exact_json_scalar_types() -> None:
    validate_bool("enabled", False)
    validate_int("attempt", 1, minimum=1, maximum=3)

    with pytest.raises(ValueError, match="must be an integer"):
        validate_int("attempt", True)
    with pytest.raises(ValueError, match="must be boolean"):
        validate_bool("enabled", 0)


def test_foundation_timestamp_round_trip_is_byte_canonical() -> None:
    instant = datetime(2026, 8, 17, 12, 34, 56, 123456, tzinfo=timezone.utc)
    text = format_utc_timestamp(instant)

    assert text == "2026-08-17T12:34:56.123456Z"
    validate_utc_timestamp("recorded_at_utc", text)
    assert parse_utc_timestamp("recorded_at_utc", text) == instant


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-17 12:34:56Z",
        "2026-08-17T12:34Z",
        "2026-08-17T12:34:56+00:00",
    ],
)
def test_foundation_rejects_noncanonical_utc_timestamp(value: str) -> None:
    with pytest.raises(ValueError, match="UTC timestamp"):
        validate_utc_timestamp("recorded_at_utc", value)


def test_foundation_usd_formatting_never_erases_positive_cost() -> None:
    assert format_usd_amount(0) == "0.000"
    assert format_usd_amount("0.0001") == "0.001"
    assert format_usd_amount("1.2345") == "1.235"
    validate_usd_amount("estimated_cost_usd", "1.235")


def test_foundation_rejects_secret_bearing_opaque_reference() -> None:
    with pytest.raises(ValueError, match="bounded opaque ref required"):
        validate_opaque_ref("credential_ref", "vault:token=secret-value")


def test_schema_traversal_never_treats_container_keys_as_schema_nodes() -> None:
    schema = {
        "type": "object",
        "properties": {
            "items": {"type": "string", "title": "keep field"},
            "properties": {"type": "integer", "title": "keep field"},
        },
        "$defs": {
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
            }
        },
    }

    transformed = transform_json_schema_nodes(
        schema,
        lambda node: {key: value for key, value in node.items() if key != "title"},
    )

    assert transformed["properties"]["items"] == {"type": "string"}
    assert transformed["properties"]["properties"] == {"type": "integer"}
    assert [path for path, _node in iter_json_schema_nodes(schema)] == [
        "#",
        "#/properties/items",
        "#/properties/properties",
        "#/$defs/items",
        "#/$defs/items/properties/title",
    ]


def test_local_schema_reference_preserves_reference_siblings() -> None:
    root = {
        "$defs": {"value": {"type": "string", "minLength": 2}},
        "type": "object",
    }

    assert resolve_local_schema_reference(
        {"$ref": "#/$defs/value", "description": "resolved value"},
        root,
    ) == {
        "type": "string",
        "minLength": 2,
        "description": "resolved value",
    }
