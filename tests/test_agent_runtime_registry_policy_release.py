from __future__ import annotations

import agent_runtime.registry.registry_release_registration as release_registration
import pytest

from agent_runtime.contracts.registry_release_definition import (
    BehaviorPolicyRelease,
)
from agent_runtime.registry.registry_release_compilation import (
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    ExecutionVariantPolicyReleaseCandidate,
    ExecutionVariantProfileBindingCandidate,
    RetryPolicyReleaseCandidate,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_execution_variant_policy_release,
    compile_retry_policy_release,
    runtime_owned_policy_schema_assets,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


def _policy_releases():
    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    evaluation = compile_evaluation_policy_release(
        EvaluationPolicyReleaseCandidate(
            policy_id="module_candidate",
            policy_version="v1",
            evaluation_mode="module_candidate",
        )
    )
    retry = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="bounded_candidate",
            policy_version="v1",
            max_attempts=3,
        )
    )
    return behavior, evaluation, retry


def test_runtime_owned_policy_schemas_and_documents_are_closed() -> None:
    schemas = runtime_owned_policy_schema_assets()
    assert {schema.schema_asset_id for schema in schemas} == {
        "runtime_behavior_policy",
        "runtime_evaluation_policy",
        "runtime_retry_policy",
        "runtime_execution_variant_policy",
    }
    assert all(
        schema.schema_document()["additionalProperties"] is False
        for schema in schemas
    )

    behavior, evaluation, retry = _policy_releases()
    assert behavior.policy_document() == {
        "context_isolation": "workflow_execution_isolated"
    }
    assert evaluation.policy_document() == {"evaluation_mode": "module_candidate"}
    assert retry.policy_document() == {"max_attempts": 3}

    with pytest.raises(ValueError, match="registered schema"):
        compile_evaluation_policy_release(
            EvaluationPolicyReleaseCandidate(
                policy_id="invalid",
                policy_version="v1",
                evaluation_mode="invented_mode",
            )
        )


def test_policy_bundle_registers_with_exact_schema_closure() -> None:
    behavior, evaluation, retry = _policy_releases()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=runtime_owned_policy_schema_assets(),
            behavior_policies=(behavior,),
            evaluation_policies=(evaluation,),
            retry_policies=(retry,),
        )
    )

    assert registry.get_behavior_policy(
        behavior.release_ref, behavior.release_sha256
    ) == behavior
    assert registry.get_evaluation_policy(
        evaluation.release_ref, evaluation.release_sha256
    ) == evaluation
    assert registry.get_retry_policy(retry.release_ref, retry.release_sha256) == retry
    snapshot = registry.snapshot()
    assert snapshot.behavior_policies == (behavior,)
    assert snapshot.evaluation_policies == (evaluation,)
    assert snapshot.retry_policies == (retry,)


def test_persisted_catalog_restore_does_not_rerun_authoring_schema_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    behavior, evaluation, retry = _policy_releases()
    bundle = RuntimeReleaseBundle(
        schema_assets=runtime_owned_policy_schema_assets(),
        behavior_policies=(behavior,),
        evaluation_policies=(evaluation,),
        retry_policies=(retry,),
    )
    expected = RuntimeReleaseRegistry()
    expected.register_bundle(bundle)

    def unavailable(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise RuntimeError("authoring validator unavailable")

    monkeypatch.setattr(
        release_registration,
        "validate_json_schema_document",
        unavailable,
    )
    monkeypatch.setattr(
        release_registration,
        "validate_json_document_against_schema",
        unavailable,
    )
    restored = RuntimeReleaseRegistry()
    restored._restore_persisted_bundle(bundle)

    assert restored.snapshot() == expected.snapshot()
    fresh = RuntimeReleaseRegistry()
    before = fresh.snapshot()
    with pytest.raises(RuntimeError, match="authoring validator unavailable"):
        fresh.register_bundle(bundle)
    assert fresh.snapshot() == before


def test_policy_bundle_refuses_missing_or_substituted_schema() -> None:
    behavior, _, _ = _policy_releases()
    registry = RuntimeReleaseRegistry()
    with pytest.raises(KeyError, match="unknown Schema Asset"):
        registry.register_bundle(RuntimeReleaseBundle(behavior_policies=(behavior,)))
    assert registry.snapshot().behavior_policies == ()

    retry_schema = next(
        schema
        for schema in runtime_owned_policy_schema_assets()
        if schema.schema_asset_id == "runtime_retry_policy"
    )
    substituted = BehaviorPolicyRelease.build(
        policy_id="workflow_execution_isolated",
        policy_version="v2",
        release_ref="behavior-policy:workflow_execution_isolated@v2",
        policy_schema_ref=retry_schema.release_ref,
        policy_schema_sha256=retry_schema.schema_sha256,
        policy_document={"context_isolation": "workflow_execution_isolated"},
    )
    with pytest.raises(ValueError, match="registered schema"):
        registry.register_bundle(
            RuntimeReleaseBundle(
                schema_assets=(retry_schema,),
                behavior_policies=(substituted,),
            )
        )


def test_schema_and_policy_are_registered_release_families() -> None:
    behavior, _, _ = _policy_releases()
    schema = runtime_owned_policy_schema_assets()[0]
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(schema,),
            behavior_policies=(behavior,),
        )
    )

    assert registry.get_schema_asset(schema.release_ref, schema.schema_sha256) == schema
    assert registry.get_behavior_policy(
        behavior.release_ref,
        behavior.release_sha256,
    ) == behavior


def test_execution_variant_policy_requires_exact_origin_and_profiles() -> None:
    candidate = ExecutionVariantPolicyReleaseCandidate(
        policy_id="source_to_evidence_ab",
        policy_version="v1",
        origin_kind="standalone_module",
        origin_release_ref="runtime-module:evidence_producer@v1",
        origin_release_sha256="a" * 64,
        bindings=(
            ExecutionVariantProfileBindingCandidate(
                position_id="evidence_producer",
                execution_profile_release_ref="execution-profile:opus_5@v1",
                execution_profile_release_sha256="b" * 64,
            ),
        ),
    )
    release = compile_execution_variant_policy_release(candidate)
    assert release.policy_document()["bindings"][0]["position_id"] == (
        "evidence_producer"
    )

    registry = RuntimeReleaseRegistry()
    with pytest.raises(KeyError, match="unknown Runtime Module"):
        registry.register_bundle(
            RuntimeReleaseBundle(
                schema_assets=runtime_owned_policy_schema_assets(),
                execution_variant_policies=(release,),
            )
        )

    duplicate = ExecutionVariantPolicyReleaseCandidate(
        **{
            **candidate.__dict__,
            "bindings": candidate.bindings + candidate.bindings,
        }
    )
    with pytest.raises(ValueError, match="position_id values must be unique"):
        compile_execution_variant_policy_release(duplicate)
