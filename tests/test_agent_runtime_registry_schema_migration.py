from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.registry import (
    BehaviorPolicyReleaseCandidate,
    RegistryReleaseIdentityDisposition,
    RegistrySchemaMigrationPlan,
    RuntimeReleaseBundle,
    compile_behavior_policy_release,
    runtime_owned_policy_schema_assets,
)


def _target_bundle() -> RuntimeReleaseBundle:
    policy = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    return RuntimeReleaseBundle(
        schema_assets=runtime_owned_policy_schema_assets(),
        behavior_policies=(policy,),
    )


def _plan() -> RegistrySchemaMigrationPlan:
    disposition = RegistryReleaseIdentityDisposition(
        source_table="prompt_component_release",
        source_release_ref="prompt-component:instruction@v1",
        source_release_sha256="1" * 64,
        disposition="reissued",
        target_release_ref="prompt-component:instruction@v2",
        target_release_sha256="2" * 64,
    )
    return RegistrySchemaMigrationPlan.build(
        migration_id="registry_v1_to_v2",
        source_schema="agent_runtime_control",
        target_schema="agent_runtime_control_v2",
        source_structure_sha256="3" * 64,
        source_row_identity_sha256="4" * 64,
        target_schema_release_ref="registry-schema:agent_runtime_control@v2",
        target_schema_release_sha256="5" * 64,
        target_bundle=_target_bundle(),
        release_dispositions=(disposition,),
        admission_dispositions=(),
        active_pointer_dispositions=(),
        planned_at_utc="2026-08-17T20:00:00Z",
    )


def test_registry_schema_migration_plan_is_content_addressed_and_replayable() -> None:
    first = _plan()
    second = _plan()

    assert second == first
    assert second.plan_sha256 == first.plan_sha256
    assert RegistrySchemaMigrationPlan.from_dict(first.as_dict()) == first


def test_registry_schema_migration_plan_rejects_in_place_mutation() -> None:
    plan = _plan()

    with pytest.raises(ValueError, match="plan hash mismatch"):
        replace(plan, source_row_identity_sha256="6" * 64).validate()


def test_registry_schema_migration_requires_side_by_side_target() -> None:
    plan = _plan()

    with pytest.raises(ValueError, match="side-by-side"):
        RegistrySchemaMigrationPlan.build(
            **{
                **{
                    field: getattr(plan, field)
                    for field in (
                        "migration_id",
                        "source_schema",
                        "target_schema",
                        "source_structure_sha256",
                        "source_row_identity_sha256",
                        "target_schema_release_ref",
                        "target_schema_release_sha256",
                        "target_bundle",
                        "release_dispositions",
                        "admission_dispositions",
                        "active_pointer_dispositions",
                        "planned_at_utc",
                    )
                },
                "target_schema": plan.source_schema,
            }
        )


def test_registry_release_disposition_preserves_unchanged_identity() -> None:
    with pytest.raises(ValueError, match="preserve exact identity"):
        RegistryReleaseIdentityDisposition(
            source_table="schema_asset_release",
            source_release_ref="schema:input@v1",
            source_release_sha256="7" * 64,
            disposition="unchanged",
            target_release_ref="schema:input@v2",
            target_release_sha256="8" * 64,
        ).validate()
