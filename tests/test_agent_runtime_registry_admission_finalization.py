from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.contracts.registry_release_definition import (
    ReleaseAdmissionIntent,
    ReleaseAdmissionRecord,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
    SchemaAssetRelease,
)
from agent_runtime.registry.registry_release_compilation import (
    candidate_admission_intent,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


_TIME = "2026-08-19T20:00:00Z"


def _schema(schema_id: str) -> SchemaAssetRelease:
    release_ref = f"schema:{schema_id}@v1"
    return SchemaAssetRelease.build(
        schema_asset_id=schema_id,
        schema_asset_version="v1",
        release_ref=release_ref,
        schema_document={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": release_ref,
            "type": "object",
        },
    )


class _Clock:
    def __init__(self, value: str = _TIME) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        return self.value


def test_admission_intent_is_timestamp_free_and_hash_complete() -> None:
    intent = candidate_admission_intent(_schema("intent_schema"))

    assert type(intent) is ReleaseAdmissionIntent
    assert set(intent.as_dict()) == {
        "admission_id",
        "subject_kind",
        "subject_id",
        "release_ref",
        "release_sha256",
        "state",
        "evidence_members",
        "admission_intent_sha256",
    }
    assert "recorded_at_utc" not in intent.as_dict()
    assert not hasattr(ReleaseAdmissionRecord, "build")
    with pytest.raises(ValueError, match="intent hash mismatch"):
        replace(intent, release_sha256="f" * 64).validate()


def test_one_store_commit_instant_finalizes_all_new_bundle_intents() -> None:
    first = _schema("first_schema")
    second = _schema("second_schema")
    clock = _Clock()
    registry = RuntimeReleaseRegistry(recording_clock=clock)

    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(first, second),
            admission_intents=(
                candidate_admission_intent(first),
                candidate_admission_intent(second),
            ),
        )
    )

    admissions = registry.snapshot().admissions
    assert clock.calls == 1
    assert {record.recorded_at_utc for record in admissions} == {_TIME}
    assert {
        record.admission_intent_sha256 for record in admissions
    } == {
        candidate_admission_intent(first).admission_intent_sha256,
        candidate_admission_intent(second).admission_intent_sha256,
    }


def test_closure_failure_precedes_clock_and_rolls_back() -> None:
    schema = _schema("missing_schema")
    clock = _Clock()
    registry = RuntimeReleaseRegistry(recording_clock=clock)

    with pytest.raises(KeyError, match="unknown schema_asset release"):
        registry.register_bundle(
            RuntimeReleaseBundle(
                admission_intents=(candidate_admission_intent(schema),)
            )
        )

    assert clock.calls == 0
    assert registry.snapshot().admissions == ()


def test_identical_replay_reuses_record_without_clock() -> None:
    schema = _schema("replay_schema")
    intent = candidate_admission_intent(schema)
    clock = _Clock()
    registry = RuntimeReleaseRegistry(recording_clock=clock)
    bundle = RuntimeReleaseBundle(
        schema_assets=(schema,),
        admission_intents=(intent,),
    )

    registry.register_bundle(bundle)
    first = registry.snapshot().admissions
    registry.register_bundle(bundle)

    assert registry.snapshot().admissions == first
    assert clock.calls == 1


def test_same_id_changed_intent_fails_without_clock_or_mutation() -> None:
    schema = _schema("collision_schema")
    intent = candidate_admission_intent(schema)
    clock = _Clock()
    registry = RuntimeReleaseRegistry(recording_clock=clock)
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(schema,),
            admission_intents=(intent,),
        )
    )
    before = registry.snapshot()
    changed = ReleaseAdmissionIntent.build(
        admission_id=intent.admission_id,
        subject_kind=ReleaseSubjectKind.SCHEMA_ASSET,
        subject_id=schema.schema_asset_id,
        release_ref=schema.release_ref,
        release_sha256=schema.release_sha256,
        state=ReleaseAdmissionState.RETIRED,
        evidence_members=(),
    )

    with pytest.raises(ValueError, match="admission_id collision"):
        registry.register_bundle(
            RuntimeReleaseBundle(admission_intents=(changed,))
        )

    assert registry.snapshot() == before
    assert clock.calls == 1


@pytest.mark.parametrize("clock_value", ("not-a-time", None))
def test_invalid_or_failed_store_clock_rolls_back(clock_value: str | None) -> None:
    schema = _schema("clock_schema")

    def clock() -> str:
        if clock_value is None:
            raise RuntimeError("clock unavailable")
        return clock_value

    registry = RuntimeReleaseRegistry(recording_clock=clock)
    with pytest.raises((RuntimeError, ValueError)):
        registry.register_bundle(
            RuntimeReleaseBundle(
                schema_assets=(schema,),
                admission_intents=(candidate_admission_intent(schema),),
            )
        )
    assert registry.snapshot().schema_assets == ()
    assert registry.snapshot().admissions == ()


def test_final_record_round_trip_and_bundle_rejects_final_record() -> None:
    schema = _schema("round_trip_schema")
    registry = RuntimeReleaseRegistry(recording_clock=lambda: _TIME)
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(schema,),
            admission_intents=(candidate_admission_intent(schema),),
        )
    )
    record = registry.snapshot().admissions[0]

    assert ReleaseAdmissionRecord.from_dict(record.as_dict()) == record
    with pytest.raises(ValueError, match="ReleaseAdmissionIntent"):
        RuntimeReleaseRegistry().register_bundle(
            RuntimeReleaseBundle(admission_intents=(record,))  # type: ignore[arg-type]
        )
