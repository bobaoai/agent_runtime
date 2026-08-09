"""Optional Postgres system of record for admitted Runtime releases.

The adapter imports no database client at module import time.  Hosts may pass a
DB-API compatible connection factory or use ``from_dsn`` with the optional
``agent-runtime-core[postgres]`` dependency.  Release rows are immutable;
only the explicit active-pointer table is mutable.
"""

from __future__ import annotations

import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ..contracts.registry_release_definition import (
    ExecutionProfileRelease,
    PromptBundleRelease,
    ReleaseAdmissionRecord,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    SkillPackageRelease,
    WorkflowRelease,
)
from ..registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
    RuntimeReleaseRegistrySnapshot,
)


_SCHEMA_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_RELEASE_TABLES = (
    "skill_package_release",
    "schema_asset_release",
    "prompt_bundle_release",
    "execution_profile_release",
    "runtime_module_release",
    "workflow_release",
)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_schema(schema: str) -> str:
    if type(schema) is not str or not _SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("invalid Postgres schema name")
    return schema


def postgres_release_ddl(schema: str = "agent_runtime_control") -> tuple[str, ...]:
    """Return deterministic DDL for the target Runtime release registry."""

    schema = _validate_schema(schema)
    release_table_ddl = tuple(
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.{table} (
            subject_id TEXT NOT NULL,
            release_version TEXT NOT NULL,
            release_ref TEXT PRIMARY KEY,
            release_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            UNIQUE (subject_id, release_version),
            CHECK (release_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip()
        for table in _RELEASE_TABLES
    )
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        *release_table_ddl,
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.skill_module_export_binding (
            skill_package_release_ref TEXT NOT NULL REFERENCES
                {schema}.skill_package_release(release_ref),
            export_id TEXT NOT NULL,
            module_id TEXT NOT NULL,
            row_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (skill_package_release_ref, export_id),
            UNIQUE (skill_package_release_ref, module_id),
            CHECK (row_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.workflow_node_binding (
            workflow_release_ref TEXT NOT NULL REFERENCES
                {schema}.workflow_release(release_ref),
            node_id TEXT NOT NULL,
            node_kind TEXT NOT NULL,
            module_release_ref TEXT REFERENCES
                {schema}.runtime_module_release(release_ref),
            row_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (workflow_release_ref, node_id),
            CHECK (row_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.workflow_edge (
            workflow_release_ref TEXT NOT NULL REFERENCES
                {schema}.workflow_release(release_ref),
            source_node_id TEXT NOT NULL,
            outcome_id TEXT NOT NULL,
            row_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (workflow_release_ref, source_node_id, outcome_id),
            CHECK (row_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.release_admission (
            admission_sequence BIGSERIAL PRIMARY KEY,
            admission_id TEXT NOT NULL UNIQUE,
            subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            release_ref TEXT NOT NULL,
            release_sha256 CHAR(64) NOT NULL,
            state TEXT NOT NULL,
            recorded_at_utc TIMESTAMPTZ NOT NULL,
            admission_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            UNIQUE (subject_kind, release_ref, admission_sha256),
            CHECK (release_sha256 ~ '^[0-9a-f]{{64}}$'),
            CHECK (admission_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.active_release_pointer (
            subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            release_ref TEXT NOT NULL,
            release_sha256 CHAR(64) NOT NULL,
            PRIMARY KEY (subject_kind, subject_id),
            CHECK (release_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
    )


def serialize_registry_tables(
    snapshot: RuntimeReleaseRegistrySnapshot,
) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    """Project one validated release_registry snapshot into normalized Postgres rows."""

    release_rows: dict[str, tuple[Mapping[str, Any], ...]] = {
        "skill_package_release": tuple(
            _release_row(
                record.skill_package_id,
                record.skill_package_version,
                record,
            )
            for record in snapshot.skill_packages
        ),
        "schema_asset_release": tuple(
            _release_row(
                record.schema_asset_id,
                record.schema_asset_version,
                record,
            )
            for record in snapshot.schema_assets
        ),
        "prompt_bundle_release": tuple(
            _release_row(
                record.prompt_bundle_id,
                record.prompt_bundle_version,
                record,
            )
            for record in snapshot.prompt_bundles
        ),
        "execution_profile_release": tuple(
            _release_row(
                record.execution_profile_id,
                record.execution_profile_version,
                record,
            )
            for record in snapshot.execution_profiles
        ),
        "runtime_module_release": tuple(
            _release_row(record.module_id, record.module_version, record)
            for record in snapshot.modules
        ),
        "workflow_release": tuple(
            _release_row(record.workflow_id, record.workflow_version, record)
            for record in snapshot.workflows
        ),
    }
    export_rows = tuple(
        {
            "skill_package_release_ref": package.release_ref,
            "export_id": export.export_id,
            "module_id": export.module_id,
            "row_sha256": _canonical_sha256(export.as_dict()),
            "payload": export.as_dict(),
        }
        for package in snapshot.skill_packages
        for export in package.module_exports
    )
    node_rows = tuple(
        {
            "workflow_release_ref": workflow.release_ref,
            "node_id": node.node_id,
            "node_kind": node.node_kind.value,
            "module_release_ref": node.module_release_ref,
            "row_sha256": _canonical_sha256(node.as_dict()),
            "payload": node.as_dict(),
        }
        for workflow in snapshot.workflows
        for node in workflow.nodes
    )
    edge_rows = tuple(
        {
            "workflow_release_ref": workflow.release_ref,
            "source_node_id": edge.source_node_id,
            "outcome_id": edge.outcome_id,
            "row_sha256": _canonical_sha256(edge.as_dict()),
            "payload": edge.as_dict(),
        }
        for workflow in snapshot.workflows
        for edge in workflow.edges
    )
    admission_rows = tuple(
        {
            "admission_id": admission.admission_id,
            "subject_kind": admission.subject_kind.value,
            "subject_id": admission.subject_id,
            "release_ref": admission.release_ref,
            "release_sha256": admission.release_sha256,
            "state": admission.state.value,
            "recorded_at_utc": admission.recorded_at_utc,
            "admission_sha256": admission.admission_sha256,
            "payload": admission.as_dict(),
        }
        for admission in snapshot.admissions
    )
    release_hashes = {
        record.release_ref: record.release_sha256
        for records in (
            snapshot.skill_packages,
            snapshot.prompt_bundles,
            snapshot.execution_profiles,
            snapshot.modules,
            snapshot.workflows,
        )
        for record in records
    }
    active_rows = tuple(
        {
            "subject_kind": compound_key.split(":", 1)[0],
            "subject_id": compound_key.split(":", 1)[1],
            "release_ref": release_ref,
            "release_sha256": release_hashes[release_ref],
        }
        for compound_key, release_ref in snapshot.active_release_refs.items()
    )
    return MappingProxyType(
        {
            **release_rows,
            "skill_module_export_binding": export_rows,
            "workflow_node_binding": node_rows,
            "workflow_edge": edge_rows,
            "release_admission": admission_rows,
            "active_release_pointer": active_rows,
        }
    )


def _release_row(
    subject_id: str,
    release_version: str,
    record: Any,
) -> Mapping[str, Any]:
    return {
        "subject_id": subject_id,
        "release_version": release_version,
        "release_ref": record.release_ref,
        "release_sha256": record.release_sha256,
        "payload": record.as_dict(),
    }


class PostgresRuntimeReleaseStore:
    """Postgres persistence adapter for immutable control-plane releases."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        schema: str = "agent_runtime_control",
    ) -> None:
        if not callable(connection_factory):
            raise ValueError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self.schema = _validate_schema(schema)

    @classmethod
    def from_dsn(
        cls,
        database_url: str,
        *,
        schema: str = "agent_runtime_control",
        connect_timeout: int = 8,
    ) -> "PostgresRuntimeReleaseStore":
        """Create the optional adapter without importing psycopg in Runtime core."""

        if type(database_url) is not str or not database_url:
            raise ValueError("database_url is required")
        if type(connect_timeout) is not int or connect_timeout < 1:
            raise ValueError("connect_timeout must be a positive integer")
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise RuntimeError(
                "Postgres adapter requires agent-runtime-core[postgres]"
            ) from exc

        return cls(
            lambda: psycopg.connect(
                database_url,
                connect_timeout=connect_timeout,
                options="-c client_encoding=UTF8 -c timezone=UTC",
            ),
            schema=schema,
        )

    def initialize_schema(self) -> None:
        """Create missing registry tables without mutating existing releases."""

        self._transaction(
            lambda cursor: [
                cursor.execute(statement)
                for statement in postgres_release_ddl(self.schema)
            ]
        )

    def register_bundle(
        self,
        candidate_bundle: RuntimeReleaseBundle,
    ) -> RuntimeReleaseRegistry:
        """Merge one candidate bundle into current Runtime authority atomically.

        Registration always reads the persisted release_registry before it evaluates the
        candidate.  The transaction-scoped advisory lock prevents two
        registrars from deriving and publishing conflicting active-pointer
        projections from the same prior state.
        """

        if type(candidate_bundle) is not RuntimeReleaseBundle:
            raise ValueError("candidate_bundle must be a RuntimeReleaseBundle")
        if candidate_bundle.is_empty():
            raise ValueError("candidate_bundle must not be empty")

        def register(cursor: Any) -> RuntimeReleaseRegistry:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"{self.schema}:runtime_release_registration",),
            )
            release_registry = self._load_release_registry(cursor)
            release_registry.register_bundle(candidate_bundle)
            self._write_release_registry(cursor, release_registry)
            return release_registry

        return self._transaction(register)

    def load_release_registry(self) -> RuntimeReleaseRegistry:
        """Load and revalidate all persisted releases and admission transitions."""

        return self._transaction(self._load_release_registry)

    def _load_release_registry(self, cursor: Any) -> RuntimeReleaseRegistry:
        """Load one complete release_registry through an existing transaction cursor."""

        records: dict[str, list[Any]] = {}
        decoders = {
            "skill_package_release": SkillPackageRelease.from_dict,
            "schema_asset_release": SchemaAssetRelease.from_dict,
            "prompt_bundle_release": PromptBundleRelease.from_dict,
            "execution_profile_release": ExecutionProfileRelease.from_dict,
            "runtime_module_release": RuntimeModuleRelease.from_dict,
            "workflow_release": WorkflowRelease.from_dict,
        }
        for table, decoder in decoders.items():
            cursor.execute(
                f"SELECT payload FROM {self.schema}.{table} ORDER BY release_ref"
            )
            records[table] = [decoder(_payload(row[0])) for row in cursor.fetchall()]
        cursor.execute(
            f"""
            SELECT payload
            FROM {self.schema}.release_admission
            ORDER BY admission_sequence
            """
        )
        admissions = [
            ReleaseAdmissionRecord.from_dict(_payload(row[0]))
            for row in cursor.fetchall()
        ]
        release_registry = RuntimeReleaseRegistry()
        bundle = RuntimeReleaseBundle(
            skill_packages=tuple(records["skill_package_release"]),
            schema_assets=tuple(records["schema_asset_release"]),
            prompt_bundles=tuple(records["prompt_bundle_release"]),
            execution_profiles=tuple(records["execution_profile_release"]),
            modules=tuple(records["runtime_module_release"]),
            workflows=tuple(records["workflow_release"]),
            admissions=tuple(admissions),
        )
        if not bundle.is_empty():
            release_registry.register_bundle(bundle)
        cursor.execute(
            f"""
            SELECT subject_kind, subject_id, release_ref
            FROM {self.schema}.active_release_pointer
            ORDER BY subject_kind, subject_id
            """
        )
        persisted_active = {
            f"{row[0]}:{row[1]}": row[2] for row in cursor.fetchall()
        }
        if persisted_active != dict(release_registry.snapshot().active_release_refs):
            raise RuntimeError(
                "Postgres active release pointers do not match admission history"
            )
        return release_registry

    def _write_release_registry(
        self,
        cursor: Any,
        release_registry: RuntimeReleaseRegistry,
    ) -> None:
        """Persist a complete validated release_registry through the current transaction."""

        if type(release_registry) is not RuntimeReleaseRegistry:
            raise ValueError("release_registry must be an exact RuntimeReleaseRegistry")
        rows = serialize_registry_tables(release_registry.snapshot())
        for table in _RELEASE_TABLES:
            for row in rows[table]:
                self._put_release(cursor, table, row)
        for row in rows["skill_module_export_binding"]:
            self._put_export(cursor, row)
        for row in rows["workflow_node_binding"]:
            self._put_node(cursor, row)
        for row in rows["workflow_edge"]:
            self._put_edge(cursor, row)
        for row in rows["release_admission"]:
            self._put_admission(cursor, row)
        cursor.execute(f"DELETE FROM {self.schema}.active_release_pointer")
        for row in rows["active_release_pointer"]:
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.active_release_pointer
                    (subject_kind, subject_id, release_ref, release_sha256)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    row["subject_kind"],
                    row["subject_id"],
                    row["release_ref"],
                    row["release_sha256"],
                ),
            )

    def _put_release(
        self, cursor: Any, table: str, row: Mapping[str, Any]
    ) -> None:
        cursor.execute(
            f"""
            INSERT INTO {self.schema}.{table}
                (subject_id, release_version, release_ref, release_sha256, payload)
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (release_ref) DO UPDATE
                SET release_sha256 = {table}.release_sha256
                WHERE {table}.release_sha256 = EXCLUDED.release_sha256
            RETURNING release_sha256
            """,
            (
                row["subject_id"],
                row["release_version"],
                row["release_ref"],
                row["release_sha256"],
                _json(row["payload"]),
            ),
        )
        returned = cursor.fetchone()
        if returned is None:
            raise ValueError(f"immutable {table} release_ref collision")

    def _put_export(self, cursor: Any, row: Mapping[str, Any]) -> None:
        self._put_child(
            cursor,
            table="skill_module_export_binding",
            key_columns=("skill_package_release_ref", "export_id"),
            row=row,
            additional_columns=("module_id",),
        )

    def _put_node(self, cursor: Any, row: Mapping[str, Any]) -> None:
        self._put_child(
            cursor,
            table="workflow_node_binding",
            key_columns=("workflow_release_ref", "node_id"),
            row=row,
            additional_columns=("node_kind", "module_release_ref"),
        )

    def _put_edge(self, cursor: Any, row: Mapping[str, Any]) -> None:
        self._put_child(
            cursor,
            table="workflow_edge",
            key_columns=("workflow_release_ref", "source_node_id", "outcome_id"),
            row=row,
            additional_columns=(),
        )

    def _put_child(
        self,
        cursor: Any,
        *,
        table: str,
        key_columns: tuple[str, ...],
        row: Mapping[str, Any],
        additional_columns: tuple[str, ...],
    ) -> None:
        columns = (*key_columns, *additional_columns, "row_sha256", "payload")
        placeholders = ", ".join(["%s"] * (len(columns) - 1) + ["%s::jsonb"])
        conflict_columns = ", ".join(key_columns)
        cursor.execute(
            f"""
            INSERT INTO {self.schema}.{table} ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT ({conflict_columns}) DO UPDATE
                SET row_sha256 = {table}.row_sha256
                WHERE {table}.row_sha256 = EXCLUDED.row_sha256
            RETURNING row_sha256
            """,
            tuple(row[column] for column in (*key_columns, *additional_columns))
            + (row["row_sha256"], _json(row["payload"])),
        )
        if cursor.fetchone() is None:
            raise ValueError(f"immutable {table} row collision")

    def _put_admission(self, cursor: Any, row: Mapping[str, Any]) -> None:
        cursor.execute(
            f"""
            INSERT INTO {self.schema}.release_admission
                (admission_id, subject_kind, subject_id, release_ref,
                 release_sha256, state, recorded_at_utc, admission_sha256, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (admission_id) DO UPDATE
                SET admission_sha256 = release_admission.admission_sha256
                WHERE release_admission.admission_sha256 = EXCLUDED.admission_sha256
            RETURNING admission_sha256
            """,
            (
                row["admission_id"],
                row["subject_kind"],
                row["subject_id"],
                row["release_ref"],
                row["release_sha256"],
                row["state"],
                row["recorded_at_utc"],
                row["admission_sha256"],
                _json(row["payload"]),
            ),
        )
        if cursor.fetchone() is None:
            raise ValueError("immutable release admission collision")

    def _transaction(self, operation: Callable[[Any], Any]) -> Any:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            try:
                result = operation(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class PostgresRuntimeReleaseQueryStore:
    """Least-authority PostgreSQL reader for immutable Workflow releases."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        schema: str = "agent_runtime_control",
    ) -> None:
        if not callable(connection_factory):
            raise ValueError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self.schema = _validate_schema(schema)

    @classmethod
    def from_dsn(
        cls,
        database_url: str,
        *,
        schema: str = "agent_runtime_control",
        connect_timeout: int = 8,
    ) -> "PostgresRuntimeReleaseQueryStore":
        if type(database_url) is not str or not database_url:
            raise ValueError("database_url is required")
        if type(connect_timeout) is not int or connect_timeout < 1:
            raise ValueError("connect_timeout must be a positive integer")
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional install
            raise RuntimeError(
                "Postgres adapter requires agent-runtime-core[postgres]"
            ) from exc
        return cls(
            lambda: psycopg.connect(
                database_url,
                connect_timeout=connect_timeout,
                options="-c client_encoding=UTF8 -c timezone=UTC",
            ),
            schema=schema,
        )

    def load_workflow_release(self, release_ref: str) -> WorkflowRelease | None:
        if type(release_ref) is not str or not release_ref:
            raise ValueError("release_ref is required")

        def load(cursor: Any) -> WorkflowRelease | None:
            cursor.execute(
                f"""
                SELECT payload
                FROM {self.schema}.workflow_release
                WHERE release_ref = %s
                """,
                (release_ref,),
            )
            row = cursor.fetchone()
            return None if row is None else WorkflowRelease.from_dict(_payload(row[0]))

        return self._transaction(load)

    def _transaction(self, operation: Callable[[Any], Any]) -> Any:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute("SET TRANSACTION READ ONLY")
                result = operation(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload(value: Any) -> Mapping[str, Any]:
    if isinstance(value, str):
        decoded = json.loads(value)
    else:
        decoded = value
    if not isinstance(decoded, Mapping):
        raise ValueError("Postgres release payload must decode to a mapping")
    return decoded


__all__ = [
    "PostgresRuntimeReleaseQueryStore",
    "PostgresRuntimeReleaseStore",
    "postgres_release_ddl",
    "serialize_registry_tables",
]
