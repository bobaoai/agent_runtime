from __future__ import annotations

import pytest

from agent_runtime.invocation.invocation_tool_definition import (
    ProviderToolDefinition,
    validate_provider_tool_set,
)


def _tool(name: str) -> ProviderToolDefinition:
    return ProviderToolDefinition(
        tool_name=name,
        description=f"Read {name}",
        input_schema={"type": "object", "additionalProperties": False},
    )


def test_gateway_tool_definitions_are_set_equal_not_order_equal() -> None:
    names = validate_provider_tool_set(
        (_tool("search_evidence"), _tool("read_source")),
        ("read_source", "search_evidence"),
    )

    assert names == ("search_evidence", "read_source")


def test_gateway_tool_definitions_reject_duplicate_names() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_provider_tool_set(
            (_tool("read_source"), _tool("read_source")),
            ("read_source",),
        )
