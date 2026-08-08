from __future__ import annotations

import hashlib

import pytest

from agent_runtime.execution.execution_content_staging import InMemoryCellArtifactStore
from agent_runtime.contracts.registry_workflow_definition import ExecutionOutputRegistrationRequest


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def test_cell_artifact_store_is_hash_bound_and_idempotent() -> None:
    store = InMemoryCellArtifactStore()
    first = store.put_bytes(
        artifact_kind_id="canonical_source_version",
        schema_version="canonical_source_version_v1",
        schema_ref="schema:canonical_source_version@v1",
        schema_sha256="1" * 64,
        media_type="application/json",
        content=b'{"source":"test"}',
        idempotency_key="source_test",
        logical_name="source",
    )
    second = store.put_bytes(
        artifact_kind_id="canonical_source_version",
        schema_version="canonical_source_version_v1",
        schema_ref="schema:canonical_source_version@v1",
        schema_sha256="1" * 64,
        media_type="application/json",
        content=b'{"source":"test"}',
        idempotency_key="source_test",
        logical_name="source",
    )

    assert first == second
    assert store.read_bytes(first.artifact_ref, first.artifact_sha256) == (
        b'{"source":"test"}'
    )
    with pytest.raises(PermissionError, match="hash mismatch"):
        store.read_bytes(first.artifact_ref, "f" * 64)
    with pytest.raises(ValueError, match="idempotency conflict"):
        store.put_bytes(
            artifact_kind_id="canonical_source_version",
            schema_version="canonical_source_version_v1",
            schema_ref="schema:canonical_source_version@v1",
            schema_sha256="1" * 64,
            media_type="application/json",
            content=b'{"source":"changed"}',
            idempotency_key="source_test",
            logical_name="source",
        )


def test_cell_artifact_store_maps_provider_output_schema_to_domain_kind() -> None:
    schema_ref = "schema:source_evidence_producer_output@v1"
    store = InMemoryCellArtifactStore(
        artifact_kind_by_schema_ref={schema_ref: "evidence_candidate"}
    )
    output = store.commit_output(
        module_run_id="module_run_test",
        variant_id="variant_test",
        attempt_id="attempt_test",
        logical_name="result",
        content=b'{"status":"drafted"}',
        schema_ref=schema_ref,
        schema_sha256="2" * 64,
        media_type="application/json",
    )

    resolved = store.resolve_artifact_ref(output.output_ref)
    assert resolved.artifact_kind_id == "evidence_candidate"
    assert output.output_sha256 == _sha('{"status":"drafted"}')


def test_module_output_idempotency_identity_is_tuple_unambiguous() -> None:
    store = InMemoryCellArtifactStore()
    common = {
        "logical_name": "result",
        "schema_ref": "schema:module_output@v1",
        "schema_sha256": "2" * 64,
        "media_type": "application/json",
    }
    first = store.commit_output(
        module_run_id="module_a_b",
        variant_id="variant_c",
        attempt_id="attempt_d",
        content=b'{"value":1}',
        **common,
    )
    second = store.commit_output(
        module_run_id="module_a",
        variant_id="b_variant_c",
        attempt_id="attempt_d",
        content=b'{"value":2}',
        **common,
    )

    assert first.output_ref != second.output_ref


def test_deterministic_output_registration_uses_same_store() -> None:
    store = InMemoryCellArtifactStore()
    result = store.record_execution_output(
        ExecutionOutputRegistrationRequest(
            output_type_id="evidence_branch_verdict",
            schema_version="evidence_branch_verdict_v1",
            media_type="application/json",
            idempotency_key="verdict_test",
            source_artifact_refs=("cell-artifact:source_test",),
        ),
        b'{"verdict":"accepted"}',
    )

    assert store.resolve_artifact_ref(
        result.execution_output_ref
    ).artifact_kind_id == "evidence_branch_verdict"
