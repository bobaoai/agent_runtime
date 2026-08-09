from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from typing import Any
import uuid

import pytest

from agent_runtime.contracts.ledger_record_definition import (
    EvaluationCoverageBinding,
    EvaluationSet,
    ExecutionInputRef,
    RuntimeRecordBatch,
    WorkflowExecutionRecord,
)
from agent_runtime.ledger import (
    PostgresRuntimeExecutionQueryStore,
    PostgresRuntimeExecutionRecordStore,
    RuntimeExecutionContent,
    deserialize_runtime_batch,
    postgres_execution_ledger_ddl,
    serialize_runtime_batch,
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
        offset=1,
    )
    assert second_page[0].workflow_execution_id == second_execution_id

    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {postgres_test_schema}.execution_record
                    (workflow_execution_id, transaction_id,
                     transaction_record_index, record_type, record_sha256,
                     payload)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    EXECUTION_ID,
                    batch.transaction_id,
                    99,
                    "CorruptRecord",
                    hashlib.sha256(b"{}").hexdigest(),
                    "{}",
                ),
            )
    with pytest.raises(RuntimeError, match="record count mismatch"):
        queries.load_trace(EXECUTION_ID)


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

    assert tuple(cursor.statements) == postgres_execution_ledger_ddl()
    assert connection.committed is True
    assert connection.rolled_back is False
    assert cursor.closed is True
    assert connection.closed is True


def test_postgres_execution_query_store_marks_database_transaction_read_only() -> None:
    cursor = _RecordingCursor()
    connection = _RecordingConnection(cursor)
    queries = PostgresRuntimeExecutionQueryStore(lambda: connection)

    assert queries.list_executions() == ()
    assert cursor.statements[0] == "SET TRANSACTION READ ONLY"
    assert "SELECT workflow_execution_id" in cursor.statements[1]
    assert connection.committed is True
