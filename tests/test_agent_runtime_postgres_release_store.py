from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.registry import (
    PostgresRuntimeReleaseQueryStore,
    PostgresRuntimeReleaseStore,
    RuntimeReleaseRegistry,
    postgres_release_ddl,
    serialize_registry_tables,
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


def test_postgres_store_initializes_registered_schema_transactionally() -> None:
    cursor = _RecordingCursor()
    connection = _RecordingConnection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    store.initialize_schema()

    assert tuple(cursor.statements) == postgres_release_ddl()
    assert connection.committed is True
    assert connection.rolled_back is False
    assert cursor.closed is True
    assert connection.closed is True


def test_postgres_store_rolls_back_schema_failure() -> None:
    cursor = _RecordingCursor(fail=True)
    connection = _RecordingConnection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    with pytest.raises(RuntimeError, match="database unavailable"):
        store.initialize_schema()

    assert connection.committed is False
    assert connection.rolled_back is True
    assert cursor.closed is True
    assert connection.closed is True


def test_postgres_projection_has_all_normalized_registry_tables() -> None:
    rows = serialize_registry_tables(RuntimeReleaseRegistry().snapshot())

    assert set(rows) == {
        "skill_package_release",
        "schema_asset_release",
        "prompt_component_release",
        "prompt_bundle_release",
        "execution_profile_release",
        "runtime_module_release",
        "workflow_release",
        "skill_module_export_binding",
        "workflow_node_binding",
        "workflow_edge",
        "release_admission",
        "active_release_pointer",
    }
    assert all(value == () for value in rows.values())


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

    assert queries.load_workflow_release("workflow-release:missing@v1") is None
    assert cursor.statements[0] == "SET TRANSACTION READ ONLY"
    assert "FROM agent_runtime_control.workflow_release" in cursor.statements[1]
    assert connection.committed is True
