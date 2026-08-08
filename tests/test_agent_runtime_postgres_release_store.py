from __future__ import annotations

from typing import Any

import pytest

from agent_runtime.registry import (
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


@pytest.mark.parametrize("schema", ("Public", "bad-name", "a" * 64))
def test_postgres_store_rejects_unsafe_schema_identifiers(schema: str) -> None:
    with pytest.raises(ValueError, match="schema name"):
        PostgresRuntimeReleaseStore(lambda: object(), schema=schema)
