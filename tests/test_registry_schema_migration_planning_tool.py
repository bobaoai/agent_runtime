from __future__ import annotations

import hashlib
import json

import pytest

from agent_runtime.contracts.registry_release_definition import (
    LegacyReleaseAdmissionRecord,
    LegacyReleaseAdmissionState,
    ReleaseSubjectKind,
)
from agent_runtime.registry import (
    RegistryAdmissionDispositionCandidate,
    RegistryMigrationCandidateSet,
    RegistryReleaseDispositionCandidate,
    runtime_owned_policy_schema_assets,
)
from tools.registry_schema_migration_planning import (
    build_plan_from_snapshot,
    load_candidate_set,
)


def _empty_source() -> dict[str, object]:
    return {
        "release_rows": {
            "schema_asset_release": [],
            "prompt_component_release": [],
            "prompt_bundle_release": [],
            "execution_profile_release": [],
            "runtime_module_release": [],
            "workflow_release": [],
        },
        "admissions": [],
        "active_pointers": [],
    }


def _canonical_sha256(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def test_candidate_artifact_hash_and_path_value_are_enforced(tmp_path) -> None:
    candidate = RegistryMigrationCandidateSet(candidate_set_id="empty_v2")
    payload = candidate.as_dict()
    artifact = tmp_path / "candidate.json"
    artifact.write_text(json.dumps(payload), encoding="utf-8")

    assert load_candidate_set(
        artifact,
        expected_sha256=_canonical_sha256(payload),
    ) == candidate
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        load_candidate_set(artifact, expected_sha256="0" * 64)

    payload["candidate_set_id"] = "/tmp/host_candidate.json"
    artifact.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="host path value"):
        load_candidate_set(
            artifact,
            expected_sha256=_canonical_sha256(payload),
        )


def test_planner_decodes_unchanged_row_and_replays_identically() -> None:
    unchanged = runtime_owned_policy_schema_assets()[0]
    source = _empty_source()
    source["release_rows"]["schema_asset_release"].append(
        {
            "release_ref": unchanged.release_ref,
            "release_sha256": unchanged.release_sha256,
            "payload": unchanged.as_dict(),
        }
    )
    candidate = RegistryMigrationCandidateSet(
        candidate_set_id="unchanged_schema",
        release_dispositions=(
            RegistryReleaseDispositionCandidate(
                source_table="schema_asset_release",
                source_release_ref=unchanged.release_ref,
                source_release_sha256=unchanged.release_sha256,
                disposition="unchanged",
                target_release_ref=unchanged.release_ref,
            ),
        ),
    )
    fields = {
        "source_snapshot": source,
        "candidate_set": candidate,
        "migration_id": "registry_v1_to_v2",
        "source_schema": "agent_runtime_control",
        "target_schema": "agent_runtime_control_v2",
        "source_structure_sha256": "a" * 64,
        "source_row_identity_sha256": "b" * 64,
        "planned_at_utc": "2026-08-17T20:00:00Z",
    }

    first = build_plan_from_snapshot(**fields)
    second = build_plan_from_snapshot(**fields)

    assert second.as_dict() == first.as_dict()
    assert first.release_dispositions[0].disposition == "unchanged"
    assert unchanged in first.target_bundle.schema_assets


def test_planner_refuses_incomplete_predecessor_disposition() -> None:
    unchanged = runtime_owned_policy_schema_assets()[0]
    source = _empty_source()
    source["release_rows"]["schema_asset_release"].append(
        {
            "release_ref": unchanged.release_ref,
            "release_sha256": unchanged.release_sha256,
            "payload": unchanged.as_dict(),
        }
    )

    with pytest.raises(ValueError, match="every predecessor release"):
        build_plan_from_snapshot(
            source_snapshot=source,
            candidate_set=RegistryMigrationCandidateSet(
                candidate_set_id="incomplete"
            ),
            migration_id="registry_v1_to_v2",
            source_schema="agent_runtime_control",
            target_schema="agent_runtime_control_v2",
            source_structure_sha256="a" * 64,
            source_row_identity_sha256="b" * 64,
            planned_at_utc="2026-08-17T20:00:00Z",
        )


def test_planner_decodes_removed_predecessor_release_before_disposition() -> None:
    release = runtime_owned_policy_schema_assets()[0]
    malformed = release.as_dict()
    malformed["schema_asset_id"] = "tampered_schema"
    source = _empty_source()
    source["release_rows"]["schema_asset_release"].append(
        {
            "release_ref": release.release_ref,
            "release_sha256": release.release_sha256,
            "payload": malformed,
        }
    )
    candidate = RegistryMigrationCandidateSet(
        candidate_set_id="remove_malformed_release",
        release_dispositions=(
            RegistryReleaseDispositionCandidate(
                source_table="schema_asset_release",
                source_release_ref=release.release_ref,
                source_release_sha256=release.release_sha256,
                disposition="removed",
                target_release_ref=None,
            ),
        ),
    )

    with pytest.raises(ValueError, match="schema asset release_ref"):
        build_plan_from_snapshot(
            source_snapshot=source,
            candidate_set=candidate,
            migration_id="registry_v1_to_v2",
            source_schema="agent_runtime_control",
            target_schema="agent_runtime_control_v2",
            source_structure_sha256="a" * 64,
            source_row_identity_sha256="b" * 64,
            planned_at_utc="2026-08-17T20:00:00Z",
        )


def test_planner_decodes_removed_predecessor_admission_before_disposition() -> None:
    release = runtime_owned_policy_schema_assets()[0]
    admission = LegacyReleaseAdmissionRecord.build(
        admission_id="admission_schema_asset_legacy_candidate",
        subject_kind=ReleaseSubjectKind.SCHEMA_ASSET,
        subject_id=release.schema_asset_id,
        release_ref=release.release_ref,
        release_sha256=release.release_sha256,
        state=LegacyReleaseAdmissionState.CANDIDATE,
        evidence_members=(),
        recorded_at_utc="2026-08-17T20:00:00Z",
    )
    malformed = admission.as_dict()
    malformed["subject_id"] = "tampered_schema"
    source = _empty_source()
    source["release_rows"]["schema_asset_release"].append(
        {
            "release_ref": release.release_ref,
            "release_sha256": release.release_sha256,
            "payload": release.as_dict(),
        }
    )
    source["admissions"].append(
        {
            "admission_id": admission.admission_id,
            "admission_sha256": admission.admission_sha256,
            "release_ref": admission.release_ref,
            "release_sha256": admission.release_sha256,
            "payload": malformed,
        }
    )
    candidate = RegistryMigrationCandidateSet(
        candidate_set_id="remove_malformed_admission",
        release_dispositions=(
            RegistryReleaseDispositionCandidate(
                source_table="schema_asset_release",
                source_release_ref=release.release_ref,
                source_release_sha256=release.release_sha256,
                disposition="unchanged",
                target_release_ref=release.release_ref,
            ),
        ),
        admission_dispositions=(
            RegistryAdmissionDispositionCandidate(
                source_admission_id=admission.admission_id,
                source_admission_sha256=admission.admission_sha256,
                disposition="removed",
                target_admission_id=None,
            ),
        ),
    )

    with pytest.raises(ValueError, match="release admission hash mismatch"):
        build_plan_from_snapshot(
            source_snapshot=source,
            candidate_set=candidate,
            migration_id="registry_v1_to_v2",
            source_schema="agent_runtime_control",
            target_schema="agent_runtime_control_v2",
            source_structure_sha256="a" * 64,
            source_row_identity_sha256="b" * 64,
            planned_at_utc="2026-08-17T20:00:00Z",
        )
