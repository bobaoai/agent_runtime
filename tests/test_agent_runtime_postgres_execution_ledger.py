from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
import threading
from typing import Any
import uuid

import pytest

from agent_runtime.contracts.ledger_record_definition import (
    EvaluationCoverageBinding,
    EvaluationSet,
    ExecutionInputRef,
    ExecutionOutputRef,
    RuntimeRecordBatch,
    WorkflowExecutionRecord,
)
from agent_runtime.ledger import (
    PostgresRuntimeExecutionQueryStore,
    PostgresRuntimeExecutionRecordStore,
    RuntimeExecutionContent,
    RuntimeExecutionPageCursor,
    deserialize_runtime_batch,
    postgres_execution_ledger_ddl,
    serialize_runtime_batch,
)
from agent_runtime.ledger.ledger_postgres_persistence import (
    _canonical_payload,
    _canonical_sha256,
    _json_bytes,
    _persisted_record_as_dict,
    _persisted_record_matches,
)


EXECUTION_ID = "execution_postgres_001"
RECORDED_AT = "2026-08-08T12:00:00Z"


@pytest.fixture
def postgres_test_schema() -> str:
    if not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"):
        pytest.skip("requires AGENT_RUNTIME_TEST_DATABASE_URL")
    import psycopg

    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    schema = f"agent_runtime_test_{uuid.uuid4().hex[:20]}"
    try:
        yield schema
    finally:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def _execution(input_record: ExecutionInputRef) -> WorkflowExecutionRecord:
    input_refs = (input_record.input_ref,)
    return WorkflowExecutionRecord(
        workflow_execution_id=EXECUTION_ID,
        workflow_id="workflow_postgres",
        workflow_contract_version="v1",
        tenant_id="tenant_postgres",
        cell_id="cell_postgres",
        principal_id="principal_postgres",
        execution_release_ref="release-ref:postgres-v1",
        graph_sha256="a" * 64,
        runtime_execution_binding_ref="runtime-binding:postgres-v1",
        runtime_execution_binding_sha256="b" * 64,
        authorization_decision_ref="authorization-decision:postgres-v1",
        authorization_decision_sha256="c" * 64,
        execution_principal_delegation_ref="execution-delegation:postgres-v1",
        execution_principal_delegation_sha256="d" * 64,
        entitlement_snapshot_ref="entitlement-ref:postgres-v1",
        entitlement_snapshot_hash="e" * 64,
        execution_input_package_refs=input_refs,
        execution_input_package_sha256="f" * 64,
        recorded_at_utc=RECORDED_AT,
    )


def test_postgres_execution_ddl_has_immutable_records_content_and_query_indexes() -> None:
    ddl = "\n".join(postgres_execution_ledger_ddl())

    assert "workflow_execution" in ddl
    assert "execution_transaction" in ddl
    assert "execution_record" in ddl
    assert "execution_content" in ddl
    assert "reject_execution_ledger_mutation" in ddl
    assert "workflow_execution_scope_time_idx" in ddl
    assert "workflow_execution_time_idx" in ddl
    assert "execution_transaction_trace_idx" in ddl
    assert "execution_record_type_idx" in ddl


@pytest.mark.parametrize("schema", ("Public", "bad-name", "a" * 64))
def test_postgres_execution_store_rejects_unsafe_schema_names(schema: str) -> None:
    with pytest.raises(ValueError, match="schema name"):
        PostgresRuntimeExecutionRecordStore(lambda: object(), schema=schema)


def test_runtime_batch_codec_round_trips_nested_records_and_enums() -> None:
    input_record = ExecutionInputRef(
        execution_input_id="input_codec_001",
        workflow_execution_id=EXECUTION_ID,
        input_type_id="agent_input",
        schema_version="v1",
        input_ref="artifact-ref:codec-input-001",
        input_sha256="0" * 64,
        byte_size=0,
        media_type="application/json",
        recorded_at_utc=RECORDED_AT,
    )
    coverage = EvaluationCoverageBinding(
        candidate_output_bundle_sha256="1" * 64,
        evaluator_id="evaluator_001",
        evaluation_run_ref="evaluation-run:001",
        evaluation_run_sha256="2" * 64,
        evaluation_result_ref="evaluation-result:001",
        evaluation_result_sha256="3" * 64,
        rubric_ref="rubric:001",
        rubric_sha256="4" * 64,
        result_execution_output_ref="artifact-ref:evaluation-001",
        result_execution_output_sha256="5" * 64,
    )
    evaluation_set = EvaluationSet.build(
        evaluation_set_id="evaluation_set_001",
        workflow_execution_id=EXECUTION_ID,
        source_module_run_id="module_run_001",
        candidate_output_bundle_sha256s=("1" * 64,),
        required_evaluator_ids=("evaluator_001",),
        rubric_refs=("rubric:001",),
        rubric_sha256s=("4" * 64,),
        coverage_bindings=(coverage,),
        evaluation_result_refs=("evaluation-result:001",),
        veto_disposition="passed",
        recorded_at_utc=RECORDED_AT,
    )
    batch = RuntimeRecordBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_codec_001",
        records=(_execution(input_record), input_record, evaluation_set),
    )

    restored = deserialize_runtime_batch(serialize_runtime_batch(batch))

    assert restored == batch
    assert restored.transaction_sha256 == batch.transaction_sha256


def test_canonical_payload_bytes_survive_jsonb_numeric_rewriting() -> None:
    payload = {"large": 1e30, "negative_zero": -0.0}
    canonical = _json_bytes(payload)
    rewritten = _json_bytes(
        {"large": 10**30, "negative_zero": 0.0}
    )

    assert canonical != rewritten
    assert _canonical_payload(memoryview(canonical)) == payload


def test_record_integrity_compares_json_arrays_not_python_tuple_types() -> None:
    record = ExecutionOutputRef(
        execution_output_id="output_postgres_001",
        workflow_execution_id=EXECUTION_ID,
        output_type_id="task_context",
        schema_version="v1",
        output_ref="artifact-ref:postgres-output-001",
        output_sha256="8" * 64,
        byte_size=12,
        media_type="application/json",
        recorded_at_utc=RECORDED_AT,
        source_artifact_refs=("artifact-ref:postgres-input-001",),
    )
    serialized = _persisted_record_as_dict(record)
    canonical = _json_bytes(serialized["record"])
    decoded = _canonical_payload(canonical)

    assert decoded["source_artifact_refs"] == [
        "artifact-ref:postgres-input-001"
    ]
    assert record.source_artifact_refs == (
        "artifact-ref:postgres-input-001",
    )
    assert _persisted_record_matches(
        record,
        expected_index=0,
        persisted_index=0,
        persisted_record_type="ExecutionOutputRef",
        persisted_record_sha256=_canonical_sha256(decoded),
        persisted_payload_canonical=memoryview(canonical),
    )


@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_execution_store_survives_reopen_and_verifies_content(
    postgres_test_schema: str,
) -> None:
    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    body = b'{"task":"inspect the agent"}'
    content_hash = hashlib.sha256(body).hexdigest()
    input_record = ExecutionInputRef(
        execution_input_id="input_postgres_001",
        workflow_execution_id=EXECUTION_ID,
        input_type_id="agent_input",
        schema_version="v1",
        input_ref="artifact-ref:postgres-input-001",
        input_sha256=content_hash,
        byte_size=len(body),
        media_type="application/json",
        recorded_at_utc=RECORDED_AT,
    )
    batch = RuntimeRecordBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_postgres_bootstrap",
        records=(_execution(input_record), input_record),
    )
    store = PostgresRuntimeExecutionRecordStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    )
    store.initialize_schema()

    first = store.commit(batch)
    replay = PostgresRuntimeExecutionRecordStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    ).commit(batch)
    content = RuntimeExecutionContent(
        workflow_execution_id=EXECUTION_ID,
        content_ref=input_record.input_ref,
        content_sha256=content_hash,
        media_type=input_record.media_type,
        body=body,
        recorded_at_utc=RECORDED_AT,
    )
    reopened = PostgresRuntimeExecutionRecordStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    )
    reopened.commit_content(content)
    replayed_content = reopened.commit_content(
        replace(content, recorded_at_utc="2026-08-08T12:00:01Z")
    )
    second_execution_id = "execution_postgres_002"
    second_input = replace(
        input_record,
        execution_input_id="input_postgres_002",
        workflow_execution_id=second_execution_id,
        input_ref="artifact-ref:postgres-input-002",
    )
    second_execution = replace(
        _execution(second_input),
        workflow_execution_id=second_execution_id,
        recorded_at_utc="2026-08-08T11:00:00Z",
    )
    reopened.commit(
        RuntimeRecordBatch(
            workflow_execution_id=second_execution_id,
            transaction_id="transaction_postgres_second",
            records=(second_execution, second_input),
        )
    )
    queries = PostgresRuntimeExecutionQueryStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert reopened.load_trace(EXECUTION_ID).records == batch.records
    assert reopened.load_content(EXECUTION_ID, input_record.input_ref).body == body
    assert reopened.list_executions()[0].workflow_execution_id == EXECUTION_ID
    assert queries.load_trace(EXECUTION_ID).records == batch.records
    assert queries.load_content(EXECUTION_ID, input_record.input_ref).body == body
    assert queries.list_executions()[0].workflow_execution_id == EXECUTION_ID
    assert queries.list_executions()[0].recorded_at_utc.endswith("Z")
    first_page = queries.list_executions(limit=1)
    second_page = queries.list_executions(
        limit=1,
        before=RuntimeExecutionPageCursor(
            recorded_at_utc=first_page[0].recorded_at_utc,
            workflow_execution_id=first_page[0].workflow_execution_id,
        ),
    )
    assert second_page[0].workflow_execution_id == second_execution_id
    assert replayed_content.body == body

    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {postgres_test_schema}.execution_record
                    (workflow_execution_id, transaction_id,
                     transaction_record_index, record_type, record_sha256,
                     payload, payload_canonical)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    EXECUTION_ID,
                    batch.transaction_id,
                    99,
                    "CorruptRecord",
                    hashlib.sha256(b"{}").hexdigest(),
                    "{}",
                    b"{}",
                ),
            )
    with pytest.raises(RuntimeError, match="record count mismatch"):
        queries.load_trace(EXECUTION_ID)


def test_postgres_stages_output_content_before_ledger_reference(
    postgres_test_schema: str,
) -> None:
    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    input_record = ExecutionInputRef(
        execution_input_id="input_postgres_stage_001",
        workflow_execution_id=EXECUTION_ID,
        input_type_id="agent_input",
        schema_version="v1",
        input_ref="artifact-ref:postgres-stage-input-001",
        input_sha256="0" * 64,
        byte_size=0,
        media_type="application/json",
        recorded_at_utc=RECORDED_AT,
    )
    store = PostgresRuntimeExecutionRecordStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    )
    store.initialize_schema()
    queries = PostgresRuntimeExecutionQueryStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    )
    store.commit(
        RuntimeRecordBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_postgres_stage_bootstrap",
            records=(_execution(input_record), input_record),
        )
    )
    body = b'{"result":"staged-before-reference"}'
    content_hash = hashlib.sha256(body).hexdigest()
    content = RuntimeExecutionContent(
        workflow_execution_id=EXECUTION_ID,
        content_ref="artifact-ref:postgres-staged-output-001",
        content_sha256=content_hash,
        media_type="application/json",
        body=body,
        recorded_at_utc=RECORDED_AT,
    )

    with pytest.raises(ValueError, match="not declared"):
        store.commit_content(content)
    assert store.stage_content(content) == content
    assert store.stage_content(
        replace(content, recorded_at_utc="2026-08-08T12:00:01Z")
    ).body == body
    assert store.list_content_metadata(EXECUTION_ID) == ()
    assert store.load_content(EXECUTION_ID, content.content_ref) is None
    assert queries.list_content_metadata(EXECUTION_ID) == ()
    assert queries.load_content(EXECUTION_ID, content.content_ref) is None

    output = ExecutionOutputRef(
        execution_output_id="execution_output_postgres_stage_001",
        workflow_execution_id=EXECUTION_ID,
        output_type_id="agent_output",
        schema_version="v1",
        output_ref=content.content_ref,
        output_sha256=content.content_sha256,
        byte_size=len(body),
        media_type=content.media_type,
        recorded_at_utc=RECORDED_AT,
        logical_name="result",
    )
    store.commit(
        RuntimeRecordBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_postgres_stage_output",
            records=(output,),
        )
    )
    assert store.load_trace(EXECUTION_ID).records_of_type(
        ExecutionOutputRef
    ) == (output,)
    assert tuple(
        row["content_ref"]
        for row in queries.list_content_metadata(EXECUTION_ID)
    ) == (content.content_ref,)
    assert tuple(
        row["content_ref"]
        for row in store.list_content_metadata(EXECUTION_ID)
    ) == (content.content_ref,)
    assert store.load_content(EXECUTION_ID, content.content_ref).body == body
    assert queries.load_content(EXECUTION_ID, content.content_ref).body == body

@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
@pytest.mark.parametrize(
    "table_name",
    (
        "workflow_execution",
        "execution_transaction",
        "execution_record",
        "execution_content",
    ),
)
@pytest.mark.parametrize("operation", ("update", "delete"))
def test_postgres_execution_authority_rejects_update_and_delete(
    table_name: str,
    operation: str,
    postgres_test_schema: str,
) -> None:
    import psycopg

    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    body = b'{"task":"inspect the agent"}'
    content_hash = hashlib.sha256(body).hexdigest()
    input_record = ExecutionInputRef(
        execution_input_id="input_postgres_001",
        workflow_execution_id=EXECUTION_ID,
        input_type_id="agent_input",
        schema_version="v1",
        input_ref="artifact-ref:postgres-input-001",
        input_sha256=content_hash,
        byte_size=len(body),
        media_type="application/json",
        recorded_at_utc=RECORDED_AT,
    )
    batch = RuntimeRecordBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_postgres_bootstrap",
        records=(_execution(input_record), input_record),
    )
    store = PostgresRuntimeExecutionRecordStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    )
    store.initialize_schema()
    store.commit(batch)
    store.commit_content(
        RuntimeExecutionContent(
            workflow_execution_id=EXECUTION_ID,
            content_ref=input_record.input_ref,
            content_sha256=content_hash,
            media_type=input_record.media_type,
            body=body,
            recorded_at_utc=RECORDED_AT,
        )
    )

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(
                psycopg.errors.RaiseException,
                match="execution records are immutable",
            ):
                statement = (
                    f"UPDATE {postgres_test_schema}.{table_name} "
                    "SET workflow_execution_id = workflow_execution_id "
                    "WHERE workflow_execution_id = %s"
                    if operation == "update"
                    else f"DELETE FROM {postgres_test_schema}.{table_name} "
                    "WHERE workflow_execution_id = %s"
                )
                cursor.execute(statement, (EXECUTION_ID,))


class _RecordingCursor:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.closed = False

    def execute(self, statement: str, parameters: Any = None) -> None:
        self.statements.append(statement)

    def close(self) -> None:
        self.closed = True

    def fetchall(self) -> list[Any]:
        return []


class _RecordingConnection:
    def __init__(self, cursor: _RecordingCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _RecordingCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def test_postgres_execution_store_initializes_schema_transactionally() -> None:
    cursor = _RecordingCursor()
    connection = _RecordingConnection(cursor)
    store = PostgresRuntimeExecutionRecordStore(lambda: connection)

    store.initialize_schema()

    assert cursor.statements[0] == "SET client_encoding TO 'UTF8'"
    assert "pg_advisory_xact_lock" in cursor.statements[1]
    assert tuple(cursor.statements[2:]) == postgres_execution_ledger_ddl()
    assert connection.committed is True
    assert connection.rolled_back is False
    assert cursor.closed is True
    assert connection.closed is True


def test_postgres_execution_query_store_marks_database_transaction_read_only() -> None:
    cursor = _RecordingCursor()
    connection = _RecordingConnection(cursor)
    queries = PostgresRuntimeExecutionQueryStore(lambda: connection)

    assert queries.list_executions() == ()
    assert cursor.statements[0] == (
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
    )
    assert cursor.statements[1] == "SET client_encoding TO 'UTF8'"
    assert "SELECT workflow_execution_id" in cursor.statements[2]
    assert connection.committed is True


@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_execution_schema_initialization_is_concurrency_safe(
    postgres_test_schema: str,
) -> None:
    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]

    def initialize(_: int) -> None:
        PostgresRuntimeExecutionRecordStore.from_dsn(
            database_url,
            schema=postgres_test_schema,
        ).initialize_schema()

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(initialize, range(16)))


@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_trace_read_uses_one_snapshot_during_concurrent_commit(
    postgres_test_schema: str,
) -> None:
    import psycopg

    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    input_record = ExecutionInputRef(
        execution_input_id="input_postgres_snapshot_001",
        workflow_execution_id=EXECUTION_ID,
        input_type_id="agent_input",
        schema_version="v1",
        input_ref="artifact-ref:postgres-snapshot-input-001",
        input_sha256="0" * 64,
        byte_size=0,
        media_type="application/json",
        recorded_at_utc=RECORDED_AT,
    )
    initial_batch = RuntimeRecordBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_postgres_snapshot_initial",
        records=(_execution(input_record), input_record),
    )
    later_input = replace(
        input_record,
        execution_input_id="input_postgres_snapshot_002",
        input_ref="artifact-ref:postgres-snapshot-input-002",
    )
    later_batch = RuntimeRecordBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_postgres_snapshot_later",
        records=(later_input,),
    )
    writer = PostgresRuntimeExecutionRecordStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    )
    writer.initialize_schema()
    writer.commit(initial_batch)
    first_select_finished = threading.Event()
    later_commit_finished = threading.Event()

    class CoordinatedCursor:
        def __init__(self, cursor: Any) -> None:
            self._cursor = cursor

        def execute(self, statement: str, parameters: Any = None):
            result = self._cursor.execute(statement, parameters)
            if (
                "FROM " + postgres_test_schema + ".execution_transaction" in statement
                and "JOIN" not in statement
            ):
                first_select_finished.set()
                assert later_commit_finished.wait(10)
            return result

        def __getattr__(self, name: str) -> Any:
            return getattr(self._cursor, name)

    class CoordinatedConnection:
        def __init__(self) -> None:
            self._connection = psycopg.connect(
                database_url,
                options="-c client_encoding=UTF8 -c timezone=UTC",
            )

        def cursor(self) -> CoordinatedCursor:
            return CoordinatedCursor(self._connection.cursor())

        def __getattr__(self, name: str) -> Any:
            return getattr(self._connection, name)

    coordinated_queries = PostgresRuntimeExecutionQueryStore(
        CoordinatedConnection,
        schema=postgres_test_schema,
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        read = executor.submit(coordinated_queries.load_trace, EXECUTION_ID)
        assert first_select_finished.wait(10)
        try:
            writer.commit(later_batch)
        finally:
            later_commit_finished.set()
        trace_during_commit = read.result(timeout=10)

    assert trace_during_commit.records == initial_batch.records
    final_trace = PostgresRuntimeExecutionQueryStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    ).load_trace(EXECUTION_ID)
    assert final_trace.records == initial_batch.records + later_batch.records


@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_mutations_reconstruct_from_committed_facts(
    postgres_test_schema: str,
    monkeypatch,
) -> None:
    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    first_input = ExecutionInputRef(
        execution_input_id="input_postgres_cache_001",
        workflow_execution_id=EXECUTION_ID,
        input_type_id="agent_input",
        schema_version="v1",
        input_ref="artifact-ref:postgres-cache-input-001",
        input_sha256="0" * 64,
        byte_size=0,
        media_type="application/json",
        recorded_at_utc=RECORDED_AT,
    )
    first_batch = RuntimeRecordBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_postgres_cache_initial",
        records=(_execution(first_input), first_input),
    )
    second_input = replace(
        first_input,
        execution_input_id="input_postgres_cache_002",
        input_ref="artifact-ref:postgres-cache-input-002",
    )
    second_batch = RuntimeRecordBatch(
        workflow_execution_id=EXECUTION_ID,
        transaction_id="transaction_postgres_cache_later",
        records=(second_input,),
    )
    store = PostgresRuntimeExecutionRecordStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    )
    store.initialize_schema()
    load_calls = 0
    original_load = store._load_committed_batches

    def count_loads(*args, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal load_calls
        load_calls += 1
        return original_load(*args, **kwargs)

    monkeypatch.setattr(store, "_load_committed_batches", count_loads)

    store.commit(first_batch)
    store.commit(second_batch)

    # The ledger holds no in-process reference cache: each mutation
    # reconstructs the validated reference from that execution's own committed
    # facts, so the second commit reloads rather than reusing a cached prefix.
    assert load_calls >= 2
    assert store.load_trace(EXECUTION_ID).records == (
        first_batch.records + second_batch.records
    )


def test_postgres_replayed_recovery_persists_exactly_one_disposition(
    postgres_test_schema: str,
) -> None:
    import hmac as hmac_module

    from agent_runtime.contracts.ledger_record_definition import (
        AttemptClaim,
        AttemptOrphanedRecord,
        LegacyAttemptBeginBatch,
        WorkflowAttemptRecord,
        WorkflowAttemptStartedRecord,
        WorkflowModuleExecutionVariantRecord,
        WorkflowModuleRunRecord,
        sha256_json,
        sha256_text,
    )
    from agent_runtime.ledger.ledger_workflow_module_recording import (
        WorkflowModuleLedgerBinding,
        WorkflowModuleLedgerRecorder,
    )

    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    input_record = ExecutionInputRef(
        execution_input_id="input_postgres_recovery_001",
        workflow_execution_id=EXECUTION_ID,
        input_type_id="agent_input",
        schema_version="v1",
        input_ref="artifact-ref:postgres-recovery-input-001",
        input_sha256="1" * 64,
        byte_size=12,
        media_type="application/json",
        recorded_at_utc=RECORDED_AT,
    )
    module = WorkflowModuleRunRecord(
        workflow_execution_id=EXECUTION_ID,
        module_run_id="module_postgres_recovery_001",
        state_id="state_postgres",
        module_id="module_postgres",
        input_refs=(input_record.input_ref,),
        input_closure_sha256=sha256_json([input_record.input_ref]),
        recorded_at_utc=RECORDED_AT,
    )
    variant = WorkflowModuleExecutionVariantRecord(
        workflow_execution_id=EXECUTION_ID,
        module_run_id=module.module_run_id,
        variant_id="variant_postgres_recovery_001",
        module_id=module.module_id,
        agent_execution_adapter_id="adapter_postgres",
        execution_profile_id="profile_postgres",
        model_id="model_postgres",
        reasoning_profile="effort_postgres",
        prompt_sha256="2" * 64,
        static_module_sha256="3" * 64,
        input_closure_sha256=module.input_closure_sha256,
        entitlement_snapshot_hash="e" * 64,
        agent_execution_adapter_revision="adapter_revision_v1",
        runtime_version="runtime_v1",
        tool_policy=("no_tools",),
        context_mode="stateless",
        output_schema_sha256="f" * 64,
        timeout_seconds=120,
        max_attempts=1,
        execution_profile_sha256="4" * 64,
        recorded_at_utc=RECORDED_AT,
    )
    store = PostgresRuntimeExecutionRecordStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    )
    store.initialize_schema()
    store.commit(
        RuntimeRecordBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_postgres_recovery_bootstrap",
            records=(_execution(input_record), input_record, module, variant),
        )
    )

    secret = b"stable-postgres-recovery-secret-32-bytes"
    attempt_id = "attempt_postgres_recovery_001"
    claim = AttemptClaim(
        workflow_execution_id=EXECUTION_ID,
        attempt_id=attempt_id,
        claim_token=hmac_module.new(
            secret,
            f"{EXECUTION_ID}\x1f{attempt_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
    )
    store.begin_attempt(
        LegacyAttemptBeginBatch(
            workflow_execution_id=EXECUTION_ID,
            transaction_id="transaction_postgres_recovery_begin",
            start=WorkflowAttemptStartedRecord(
                workflow_execution_id=EXECUTION_ID,
                dispatch_id="dispatch_postgres_recovery_001",
                module_run_id=module.module_run_id,
                variant_id=variant.variant_id,
                attempt_id=attempt_id,
                parent_attempt_id=None,
                attempt_ordinal=1,
                trace_id="trace_postgres_recovery_001",
                request_sha256="5" * 64,
                claim_token_hash=sha256_text(claim.claim_token),
                input_closure_sha256=module.input_closure_sha256,
                execution_profile_sha256=variant.execution_profile_sha256,
                entitlement_snapshot_hash=variant.entitlement_snapshot_hash,
                timeout_seconds=variant.timeout_seconds,
                recorded_at_utc=RECORDED_AT,
            ),
            claim=claim,
            grants=(),
        )
    )

    def recorder() -> WorkflowModuleLedgerRecorder:
        return WorkflowModuleLedgerRecorder(
            WorkflowModuleLedgerBinding(
                record_store=PostgresRuntimeExecutionRecordStore.from_dsn(
                    database_url,
                    schema=postgres_test_schema,
                ),
                entitlement_snapshot_hash=variant.entitlement_snapshot_hash,
                claim_token_secret=secret,
            )
        )

    first = recorder().recover_expired_attempt(
        workflow_execution_id=EXECUTION_ID,
        attempt_id=attempt_id,
        observed_at_utc="2026-08-08T12:02:01Z",
    )
    replay = recorder().recover_expired_attempt(
        workflow_execution_id=EXECUTION_ID,
        attempt_id=attempt_id,
        observed_at_utc="2026-08-08T12:07:33Z",
    )

    assert first.commit_receipt.replayed is False
    assert replay.commit_receipt.replayed is True
    trace = PostgresRuntimeExecutionRecordStore.from_dsn(
        database_url,
        schema=postgres_test_schema,
    ).load_trace(EXECUTION_ID)
    orphans = trace.records_of_type(AttemptOrphanedRecord)
    terminals = trace.records_of_type(WorkflowAttemptRecord)
    assert len(orphans) == 1
    assert len(terminals) == 1
    assert orphans[0].recorded_at_utc == "2026-08-08T12:02:00Z"
    assert terminals[0].recorded_at_utc == "2026-08-08T12:02:00Z"
