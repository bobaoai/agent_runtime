from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_runtime.foundation.foundation_contract_validation import (
    format_utc_timestamp,
    parse_utc_timestamp,
)
from agent_runtime.contracts.ledger_record_definition import (
    WorkflowAttemptRecord,
    AttemptOutputBundle,
    ExecutionOutputRef,
    ModelUsageRecord,
    WorkflowModuleRunRecord,
    WorkflowModuleExecutionVariantRecord,
    attempt_output_bundle_sha256,
    sha256_json,
    stable_runtime_id,
    write_immutable_json,
)


def test_module_variant_attempt_and_canonical_output_lineage_validate() -> None:
    execution_id = "csex_execution_001"
    input_ref = "artifact-ref:csex_execution_001:source_package"
    output_ref = "output-ref:worker-output-001"
    module = WorkflowModuleRunRecord(
        workflow_execution_id=execution_id,
        module_run_id="module_extraction_001",
        state_id="state_extraction",
        module_id="extraction_worker",
        input_refs=(input_ref,),
        input_closure_sha256=sha256_json([input_ref]),
        recorded_at_utc="2026-08-02T12:00:00Z",
    )
    variant = WorkflowModuleExecutionVariantRecord(
        workflow_execution_id=execution_id,
        module_run_id=module.module_run_id,
        variant_id="variant_fable_001",
        module_id="extraction_worker",
        agent_execution_adapter_id="claude_agent_sdk",
        execution_profile_id="csex_fable_5_max_v1",
        model_id="claude-fable-5",
        reasoning_profile="max",
        prompt_sha256="b" * 64,
        static_module_sha256="c" * 64,
        input_closure_sha256=module.input_closure_sha256,
        entitlement_snapshot_hash="e" * 64,
        agent_execution_adapter_revision="traced_claude_sdk_v1",
        runtime_version="0.1.0",
        tool_policy=("no_tools", "one_turn"),
        context_mode="stateless_artifact_rebuild",
        output_schema_sha256="f" * 64,
        timeout_seconds=900,
        max_attempts=1,
        execution_profile_sha256="0" * 64,
        recorded_at_utc="2026-08-02T12:00:00Z",
    )
    attempt = WorkflowAttemptRecord(
        workflow_execution_id=execution_id,
        module_run_id=module.module_run_id,
        variant_id=variant.variant_id,
        attempt_id="attempt_fable_001",
        parent_attempt_id=None,
        attempt_ordinal=1,
        status="completed",
        period_start_at_utc="2026-08-02T12:00:00Z",
        period_end_at_utc="2026-08-02T12:01:00Z",
        recorded_at_utc="2026-08-02T12:01:00Z",
        trace_id="trace_fable_001",
        execution_output_refs=(output_ref,),
        failure_class=None,
    )
    output = ExecutionOutputRef(
        execution_output_id="execution_output_worker_001",
        workflow_execution_id=execution_id,
        output_type_id="canonical_source_extraction",
        schema_version="v1",
        output_ref=output_ref,
        output_sha256="a" * 64,
        byte_size=120,
        media_type="application/json",
        recorded_at_utc="2026-08-02T12:01:00Z",
        module_run_id=module.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=attempt.attempt_id,
        logical_name="source_extraction.json",
    )
    bundle_hash = attempt_output_bundle_sha256((output,))
    bundle = AttemptOutputBundle(
        attempt_output_bundle_id="output_bundle_worker_001",
        workflow_execution_id=execution_id,
        module_run_id=module.module_run_id,
        variant_id=variant.variant_id,
        attempt_id=attempt.attempt_id,
        execution_output_refs=(output.output_ref,),
        bundle_sha256=bundle_hash,
        recorded_at_utc="2026-08-02T12:01:00Z",
    )

    assert module.as_dict()["workflow_execution_id"] == execution_id
    assert output.as_dict()["execution_output_id"] == "execution_output_worker_001"
    assert output.as_dict()["output_ref"] == output_ref
    assert "artifact_id" not in output.as_dict()
    assert variant.as_dict()["agent_execution_adapter_id"] == "claude_agent_sdk"
    assert variant.as_dict()["module_id"] == "extraction_worker"
    assert variant.as_dict()["tool_policy"] == ["no_tools", "one_turn"]
    assert attempt.as_dict()["execution_output_refs"] == [output_ref]
    assert bundle.as_dict()["execution_output_refs"] == [output_ref]
    assert bundle.bundle_sha256 == bundle_hash
    assert "usage" not in attempt.as_dict()
    assert "round_index" not in module.as_dict()

    with pytest.raises(ValueError, match="invalid recorded_at_utc"):
        replace(module, recorded_at_utc="2026-08-02T12:00:00+05:30").validate()
    with pytest.raises(ValueError, match="invalid recorded_at_utc"):
        replace(module, recorded_at_utc="2026-08-02 12:00:00Z").validate()


def test_execution_output_ref_rejects_legacy_constructor_fields() -> None:
    with pytest.raises(TypeError):
        ExecutionOutputRef(
            artifact_id="legacy_output_001",
            workflow_execution_id="legacy_execution_001",
            artifact_kind_id="legacy_output_type",
            schema_version="v1",
            artifact_ref="output-ref:legacy-output-001",
            artifact_sha256="a" * 64,
            byte_size=1,
            media_type="application/json",
            recorded_at_utc="2026-08-02T12:00:00Z",
        )


def test_unknown_usage_stays_null_and_negative_values_fail() -> None:
    unknown = ModelUsageRecord(None, None, None, None, None, None)
    assert unknown.as_dict()["input_tokens"] is None
    with pytest.raises(ValueError, match="non-negative"):
        ModelUsageRecord(-1, 0, 0, 0, None).validate()


@pytest.mark.parametrize(
    "value",
    (float("nan"), 1.5, "1.5", "1e30", "-0.001", "1.2500", "abc"),
)
def test_usage_rejects_non_canonical_cost_amounts(value: object) -> None:
    with pytest.raises(ValueError, match="canonical USD amount"):
        ModelUsageRecord(0, 0, 0, 0, value).validate()


def test_usage_accepts_canonical_cost_amounts() -> None:
    ModelUsageRecord(1, 1, 0, 0, "0.000", "1.250").validate()


def test_canonical_record_constructors_never_return_legacy_types() -> None:
    with pytest.raises(TypeError):
        WorkflowModuleRunRecord(
            workflow_execution_id="execution_legacy_001",
            module_run_id="module_legacy_001",
            module_key="legacy_module",
            round_index=0,
            input_artifact_ids=("artifact_legacy_001",),
            input_closure_sha256="a" * 64,
            created_at_utc="2026-08-02T12:00:00Z",
        )


def test_stable_runtime_id_is_deterministic_and_parent_sensitive() -> None:
    first = stable_runtime_id("module", "execution_1", "worker", "input_a")
    assert first == stable_runtime_id("module", "execution_1", "worker", "input_a")
    assert first != stable_runtime_id("module", "execution_1", "worker", "input_b")


def test_immutable_json_replay_is_noop_and_drift_fails(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    assert write_immutable_json(path, {"status": "complete"}) is True
    assert write_immutable_json(path, {"status": "complete"}) is False
    with pytest.raises(FileExistsError, match="immutable"):
        write_immutable_json(path, {"status": "changed"})


@pytest.mark.parametrize(
    "value",
    [
        "2026-08-02 12:00:00Z",
        "2026-08-02T12:00Z",
        "20260802T120000Z",
        "2026-08-02T12:00:00",
        "2026-08-02T12:00:00+00:00",
        "2026-08-02T12:00:00.1234567Z",
    ],
)
def test_canonical_timestamp_parse_rejects_non_canonical_text(value: str) -> None:
    with pytest.raises(ValueError, match="invalid observed_at_utc"):
        parse_utc_timestamp("observed_at_utc", value)


def test_canonical_timestamp_helpers_round_trip() -> None:
    for text in ("2026-08-02T12:00:00Z", "2026-08-02T12:00:00.000001Z"):
        instant = parse_utc_timestamp("recorded_at_utc", text)
        assert instant.tzinfo is not None
        assert instant.utcoffset() == timedelta(0)
        assert format_utc_timestamp(instant) == text

    with pytest.raises(ValueError, match="aware UTC instant"):
        format_utc_timestamp(datetime(2026, 8, 2, 12, 0, 0))
    with pytest.raises(ValueError, match="aware UTC instant"):
        format_utc_timestamp(
            datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone(timedelta(hours=2)))
        )
