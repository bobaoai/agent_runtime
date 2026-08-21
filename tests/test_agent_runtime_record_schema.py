from __future__ import annotations

import dataclasses
from typing import get_args

from agent_runtime.contracts.ledger_record_definition import PersistedRuntimeRecord
from agent_runtime.inspection.inspection_record_schema_rendering import (
    RECORD_SCHEMA_PROJECTION_VERSION,
    build_runtime_record_schema_projection,
    render_runtime_record_schema_markdown,
)


def _union_member_names() -> set[str]:
    return {cls.__name__ for cls in get_args(PersistedRuntimeRecord)}


def test_projection_covers_every_ledger_record_type() -> None:
    projection = build_runtime_record_schema_projection()

    projected = {record["record"] for record in projection["records"]}
    assert projected == _union_member_names()
    assert projection["record_count"] == len(projected)
    assert projection["schema_version"] == RECORD_SCHEMA_PROJECTION_VERSION


def test_every_identity_and_reference_field_exists_on_its_record() -> None:
    types = {cls.__name__: cls for cls in get_args(PersistedRuntimeRecord)}
    projection = build_runtime_record_schema_projection()

    for record in projection["records"]:
        fields = {f.name for f in dataclasses.fields(types[record["record"]])}
        assert record["identity"] in fields, record["record"]
        for reference in record["references"]:
            assert reference["field"] in fields, (record["record"], reference)


def test_reference_targets_resolve_to_known_records() -> None:
    projection = build_runtime_record_schema_projection()
    known = _union_member_names()

    for edge in projection["edges"]:
        assert edge["from"] in known
        assert edge["to"] in known


def test_operation_id_is_registered_as_a_polymorphic_call_reference() -> None:
    projection = build_runtime_record_schema_projection()

    usage = next(r for r in projection["records"] if r["record"] == "UsageEvent")
    operation_ref = next(
        reference
        for reference in usage["references"]
        if reference["field"] == "operation_id"
    )
    assert operation_ref["kind"] == "polymorphic"
    assert set(operation_ref["targets"]) == {"ModelCallRecord", "ToolCallRecord"}


def test_planes_partition_control_and_observation() -> None:
    projection = build_runtime_record_schema_projection()

    planes = {record["record"]: record["plane"] for record in projection["records"]}
    assert planes["UsageEvent"] == "observation"
    assert planes["ModelCallRecord"] == "observation"
    assert planes["ToolCallRecord"] == "observation"
    assert planes["WorkflowExecutionRecord"] == "control"
    assert planes["ModuleOutcome"] == "control"
    assert set(planes.values()) == {"control", "observation"}


def test_projection_is_deterministic() -> None:
    assert (
        build_runtime_record_schema_projection()
        == build_runtime_record_schema_projection()
    )
    assert render_runtime_record_schema_markdown() == (
        render_runtime_record_schema_markdown()
    )
