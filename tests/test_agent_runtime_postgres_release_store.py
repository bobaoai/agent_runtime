from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.registry import (
    PostgresRuntimeReleaseQueryStore,
    PostgresRuntimeReleaseStore,
    REGISTRY_SCHEMA_RELEASE_ID,
)


class _RecordingCursor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.statements: list[str] = []
        self.closed = False

    def execute(self, statement: str, parameters: Any = None) -> None:
        if self.fail:
            raise RuntimeError("database unavailable")
        self.statements.append(statement)

    def close(self) -> None:
        self.closed = True

    def fetchone(self) -> Any:
        return None


class _ChildRowCursor:
    def __init__(self) -> None:
        self.statement = ""
        self.parameters: tuple[Any, ...] = ()

    def execute(self, statement: str, parameters: tuple[Any, ...]) -> None:
        self.statement = statement
        self.parameters = parameters

    def fetchone(self) -> tuple[str]:
        return ("a" * 64,)


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


def test_registry_schema_release_id_is_stable() -> None:
    assert REGISTRY_SCHEMA_RELEASE_ID == "agent_runtime_registry_v2"


def test_postgres_store_writes_required_workflow_node_columns() -> None:
    cursor = _ChildRowCursor()
    store = PostgresRuntimeReleaseStore(lambda: object())

    store._put_node(  # noqa: SLF001 - focused persistence regression
        cursor,
        {
            "workflow_release_ref": "workflow:example@v1",
            "node_id": "route",
            "node_kind": "module",
            "module_release_ref": "runtime-module:route@v1",
            "row_sha256": "a" * 64,
            "payload": {"node_id": "route", "node_kind": "module"},
        },
    )

    assert "node_kind" in cursor.statement
    assert cursor.parameters[:4] == (
        "workflow:example@v1",
        "route",
        "module",
        "runtime-module:route@v1",
    )


@pytest.mark.parametrize("schema", ("Public", "bad-name", "a" * 64))
def test_postgres_store_rejects_unsafe_schema_identifiers(schema: str) -> None:
    with pytest.raises(ValueError, match="schema name"):
        PostgresRuntimeReleaseStore(lambda: object(), schema=schema)


def test_postgres_release_query_store_marks_database_transaction_read_only() -> None:
    cursor = _RecordingCursor()
    connection = _RecordingConnection(cursor)
    queries = PostgresRuntimeReleaseQueryStore(lambda: connection)

    with pytest.raises(RuntimeError, match="not ready: absent"):
        queries.load_workflow_release("workflow-release:missing@v1")
    assert cursor.statements[0] == "SET TRANSACTION READ ONLY"
    assert "pg_namespace" in cursor.statements[1]
    assert connection.rolled_back is True
    assert cursor.closed is True
    assert connection.closed is True
