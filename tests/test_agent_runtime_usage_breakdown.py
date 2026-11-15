"""Ordinary input beside the cache-inclusive total (Design 08 §11.2).

input_tokens is the total including cache. ordinary_input_tokens, cache_read_tokens and
cache_creation_tokens are its reported parts; output is separate. Unknown stays None and
no part is derived from the total. Historical usage events keep their exact bytes.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from agent_runtime.contracts.invocation_adapter_definition import AgentExecutionResult
from agent_runtime.contracts.ledger_lineage_definition import ModuleUsageObservation
from agent_runtime.contracts.ledger_record_definition import (
    LegacyAuthorizationLedgerRecord,
    LegacyRuntimeRecordBatch,
    RuntimeRecordBatch,
    UsageEvent,
    canonical_json,
    runtime_record_as_dict,
    sha256_json,
)
from agent_runtime.inspection import build_runtime_execution_inspection, build_workflow_review_bundle
from agent_runtime.inspection.inspection_snapshot_rendering import render_workflow_review_html
from agent_runtime.ledger import aggregate_model_usage
from agent_runtime.ledger.ledger_postgres_persistence import (
    _canonical_sha256,
    _json_bytes,
    _persisted_record_as_dict,
    _persisted_record_matches,
    deserialize_runtime_record,
)
from test_agent_runtime_execution_inspection import _record, _trace, _trace_with
from test_agent_runtime_public_adapter_contracts import _provider_result

FIXTURE = Path(__file__).parent / "fixtures/claude_v2_replay/committed.json"

# (total, ordinary, cache read, cache creation): each tuple is a valid report.
VALID = [
    (26, 2, 11, 13),      # Claude with all parts known: the total is their sum.
    (None, 2, 11, None),  # A cache part unknown: total unknown, ordinary still reported.
    (14, 2, None, None),  # Total known, caches unknown: ordinary only bounded by the total.
    (12, None, 4, None),  # Codex shape: ordinary unknown, never derived from 12 - 4.
    (0, 0, 0, 0),         # Zero is a reported value, not unknown.
    (None, None, None, None),
]
INVALID = {
    "ordinary above the total": (5, 6, None, None),
    "all four known but the total is not their sum": (26, 3, 11, 13),
    "cache counted twice into the total": (50, 2, 11, 13),
    "negative ordinary": (None, -1, None, None),
    "boolean ordinary": (None, True, None, None),
    "string ordinary": (None, "2", None, None),
}


def _observation(total, ordinary, read, creation):
    return ModuleUsageObservation(input_tokens=total, output_tokens=5, cache_read_tokens=read,
                                  cache_creation_tokens=creation, ordinary_input_tokens=ordinary)


def _result(total, ordinary, read, creation):
    return replace(_provider_result(), input_tokens=total, ordinary_input_tokens=ordinary,
                   cache_read_tokens=read, cache_creation_tokens=creation)


def _event(total, ordinary, read, creation):
    return UsageEvent(usage_event_id="usage_event_001", operation_id="model_call_001",
                      workflow_execution_id="execution_001", module_run_id="module_run_001",
                      variant_id="variant_001", attempt_id="attempt_001", grant_id="grant_001",
                      resource_id="provider_001", action_id="invoke_model", input_tokens=total,
                      output_tokens=5, cache_read_tokens=read, cache_creation_tokens=creation,
                      estimated_cost_usd=None, provider_charge_usd=None,
                      recorded_at_utc="2026-11-15T12:00:00Z", ordinary_input_tokens=ordinary)


CARRIERS = {"AgentExecutionResult": _result, "ModuleUsageObservation": _observation, "UsageEvent": _event}


@pytest.mark.deterministic
@pytest.mark.parametrize("carrier", CARRIERS)
@pytest.mark.parametrize("counts", VALID)
def test_every_usage_carrier_accepts_reported_parts_and_unknowns(carrier, counts):
    value = CARRIERS[carrier](*counts)
    value.validate()
    assert value.ordinary_input_tokens == counts[1]


@pytest.mark.deterministic
@pytest.mark.parametrize("carrier", CARRIERS)
@pytest.mark.parametrize("case", INVALID)
def test_every_usage_carrier_rejects_impossible_or_double_counted_input(carrier, case):
    with pytest.raises(ValueError):
        CARRIERS[carrier](*INVALID[case]).validate()


@pytest.mark.deterministic
def test_existing_constructors_keep_working_and_report_ordinary_input_as_unknown():
    result = _provider_result()
    observation = ModuleUsageObservation(input_tokens=3, output_tokens=2, cache_read_tokens=None,
                                         cache_creation_tokens=None)
    for value in (result, observation):
        value.validate()
        assert value.ordinary_input_tokens is None
    assert observation.as_dict() == {"input_tokens": 3, "output_tokens": 2, "cache_read_tokens": None,
                                     "cache_creation_tokens": None, "ordinary_input_tokens": None}


def _fixture_cases():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


@pytest.mark.deterministic
@pytest.mark.parametrize("workspace", ["none", "own_draft_read_write"])
def test_records_committed_by_an_earlier_runtime_keep_their_exact_bytes_and_hashes(workspace):
    """The fixture holds records actually committed by Runtime 86b5892, before the field existed."""
    case = next(item for item in _fixture_cases() if item["workspace"] == workspace)
    stored = case["records"]
    usage_rows = [item for item in stored if item["record_type"] == "UsageEvent"]
    assert usage_rows and all("ordinary_input_tokens" not in item["record"] for item in usage_rows)
    records = []
    for index, item in enumerate(stored):
        record = deserialize_runtime_record(item)
        records.append(record)
        assert _json_bytes(_persisted_record_as_dict(record)) == _json_bytes(item)
        assert _persisted_record_matches(record, expected_index=index, persisted_index=index,
                                         persisted_record_type=item["record_type"],
                                         persisted_record_sha256=_canonical_sha256(item["record"]),
                                         persisted_payload_canonical=_json_bytes(item["record"]))
    for item in usage_rows:
        event = deserialize_runtime_record(item)
        assert event.ordinary_input_tokens is None
        assert _json_bytes(event.as_dict()) == _json_bytes(item["record"])
    # The batch hash over the historical Runtime records equals the hash of their stored bytes.
    execution_id = case["original_result"]["module_run"]["workflow_execution_id"]
    current = [(record, item) for record, item in zip(records, stored)
               if not isinstance(record, LegacyAuthorizationLedgerRecord)]
    assert any(type(record) is UsageEvent for record, _ in current)
    batch = RuntimeRecordBatch(execution_id, "restore_predecessor_history", tuple(record for record, _ in current))
    assert batch.transaction_sha256 == sha256_json({
        "workflow_execution_id": execution_id, "transaction_id": "restore_predecessor_history",
        "records": [item for _, item in current]})
    LegacyRuntimeRecordBatch(execution_id, "restore_predecessor_history", tuple(records)).validate()


@pytest.mark.deterministic
def test_new_usage_event_stores_a_known_ordinary_count_and_omits_an_unknown_one():
    known = _event(26, 2, 11, 13)
    stored = runtime_record_as_dict(known)
    assert stored["record"]["ordinary_input_tokens"] == 2
    assert deserialize_runtime_record(json.loads(canonical_json(stored))) == known
    unknown = _event(26, None, 11, 13)
    stored = runtime_record_as_dict(unknown)
    assert "ordinary_input_tokens" not in stored["record"]
    assert deserialize_runtime_record(json.loads(canonical_json(stored))) == unknown
    assert sha256_json(unknown.as_dict()) == sha256_json({
        key: value for key, value in known.as_dict().items() if key != "ordinary_input_tokens"})


@pytest.mark.deterministic
def test_aggregation_sums_each_input_part_on_its_own_and_counts_unknown_modules():
    rows = [{"input_tokens": 26, "ordinary_input_tokens": 2, "output_tokens": 5,
             "cache_read_tokens": 11, "cache_creation_tokens": 13},
            {"input_tokens": 7, "ordinary_input_tokens": 7, "output_tokens": 3,
             "cache_read_tokens": 0, "cache_creation_tokens": 0}]
    total = aggregate_model_usage(rows)
    assert (total["input_tokens"], total["ordinary_input_tokens"], total["cache_read_tokens"],
            total["cache_creation_tokens"], total["output_tokens"]) == (33, 9, 11, 13, 8)
    assert total["ordinary_input_tokens_unknown_module_count"] == 0
    # A Codex-shaped row reports total and cache but no ordinary count: it stays unknown.
    rows.append({"input_tokens": 12, "ordinary_input_tokens": None, "output_tokens": 1,
                 "cache_read_tokens": 4, "cache_creation_tokens": 0})
    partial = aggregate_model_usage(rows)
    assert partial["input_tokens"] == 45 and partial["cache_read_tokens"] == 15
    assert partial["ordinary_input_tokens"] is None
    assert partial["ordinary_input_tokens_unknown_module_count"] == 1


def _inspection(ordinary):
    base = _trace()
    usage = _record(base, UsageEvent)
    assert (usage.input_tokens, usage.cache_read_tokens, usage.cache_creation_tokens) == (120, 0, 0)
    records = tuple(replace(row, ordinary_input_tokens=ordinary) if row is usage else row for row in base.records)
    return build_runtime_execution_inspection(_trace_with(base, records))


@pytest.mark.deterministic
@pytest.mark.parametrize("ordinary", [120, None])
def test_inspector_attempt_and_workflow_usage_show_ordinary_input_or_unknown(ordinary):
    inspection = _inspection(ordinary)
    for view in (inspection["trace"]["usage"], inspection["modules"][0]["attempts"][0]):
        assert (view["input_tokens"], view["ordinary_input_tokens"], view["cache_read_tokens"],
                view["cache_creation_tokens"], view["output_tokens"]) == (120, ordinary, 0, 0, 30)


@pytest.mark.deterministic
def test_review_page_shows_four_input_counts_on_attempt_cards_and_workflow_metrics():
    html = render_workflow_review_html(build_workflow_review_bundle(_inspection(120)))
    labels = (("Input total (incl. cache)", "input_tokens"), ("Ordinary input", "ordinary_input_tokens"),
              ("Cache read", "cache_read_tokens"), ("Cache creation", "cache_creation_tokens"),
              ("Output tokens", "output_tokens"))
    attempt_cards = "".join(f'${{card("{label}",attempt.{field}??"unknown")}}' for label, field in labels)
    workflow_metrics = ",".join(f'["{label}",usage.{field}??"unknown"]' for label, field in labels)
    assert attempt_cards in html
    assert workflow_metrics in html
    assert '"ordinary_input_tokens":120' in html
    assert '"Input tokens"' not in html
