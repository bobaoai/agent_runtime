from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.registry import (
    BehaviorPolicyReleaseCandidate,
    RegistryActivePointerDispositionCandidate,
    RegistryAdmissionDispositionCandidate,
    RegistryMigrationCandidateSet,
    RegistryReleaseDispositionCandidate,
    compile_registry_migration_candidate_set,
)


def _candidate_set() -> RegistryMigrationCandidateSet:
    target_ref = "behavior-policy:workflow_execution_isolated@v2"
    return RegistryMigrationCandidateSet(
        candidate_set_id="synthetic_registry_migration",
        behavior_policies=(
            BehaviorPolicyReleaseCandidate(
                policy_id="workflow_execution_isolated",
                policy_version="v2",
                context_isolation="workflow_execution_isolated",
            ),
        ),
        release_dispositions=(
            RegistryReleaseDispositionCandidate(
                source_table="behavior_policy_release",
                source_release_ref=(
                    "behavior-policy:workflow_execution_isolated@v1"
                ),
                source_release_sha256="a" * 64,
                disposition="reissued",
                target_release_ref=target_ref,
            ),
        ),
        admission_dispositions=(
            RegistryAdmissionDispositionCandidate(
                source_admission_id="admission_behavior_v1_active",
                source_admission_sha256="b" * 64,
                disposition="removed",
                target_admission_id=None,
            ),
        ),
    )


def test_candidate_set_compiles_target_and_resolves_exact_hashes() -> None:
    first = compile_registry_migration_candidate_set(_candidate_set())
    second = compile_registry_migration_candidate_set(_candidate_set())

    target = first.target_bundle.behavior_policies[0]
    assert first == second
    assert first.release_dispositions[0].target_release_sha256 == (
        target.release_sha256
    )
    assert first.admission_dispositions[0].target_admission_sha256 is None
    assert RegistryMigrationCandidateSet.from_dict(
        _candidate_set().as_dict()
    ) == _candidate_set()


def test_candidate_set_rejects_host_path_field_or_value() -> None:
    with pytest.raises(ValueError, match="host path value"):
        compile_registry_migration_candidate_set(
            replace(_candidate_set(), candidate_set_id="/tmp/candidate.json")
        )


def test_pointer_disposition_rejects_a_non_entry_release_family() -> None:
    target_ref = "behavior-policy:workflow_execution_isolated@v2"
    with pytest.raises(ValueError, match="only Module or Workflow"):
        compile_registry_migration_candidate_set(
            replace(
                _candidate_set(),
                active_pointer_dispositions=(
                    RegistryActivePointerDispositionCandidate(
                        subject_kind="behavior_policy",
                        subject_id="workflow_execution_isolated",
                        source_release_ref=(
                            "behavior-policy:workflow_execution_isolated@v1"
                        ),
                        source_release_sha256="a" * 64,
                        disposition="replaced",
                        target_release_ref=target_ref,
                    ),
                ),
            )
        )
