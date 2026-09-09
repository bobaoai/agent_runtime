"""Optional Postgres system of record for registered Runtime releases.

The adapter imports no database client at module import time.  Hosts may pass a
DB-API compatible connection factory or use ``from_dsn`` with the optional
``agent-runtime-core[postgres]`` dependency.  Release rows are immutable;
only the explicit active-pointer table is mutable.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    ExecutionProfileRelease,
    EvaluationPolicyRelease,
    ExecutionVariantPolicyRelease,
    PromptComponentRelease,
    PromptBundleRelease,
    ReleaseSubjectKind,
    RetryPolicyRelease,
    ModuleRelease,
    SchemaAssetRelease,
    WorkflowRelease,
)
from ..registry.registry_release_registration import (
    RuntimeActiveReleasePointerResult,
    RuntimeReleaseBundle,
    RuntimeReleaseRegistrationResult,
    RuntimeReleaseRegistry,
    RuntimeReleaseRegistrySnapshot,
)
from .registry_schema_migration import RegistrySchemaMigrationPlan
from ..foundation.foundation_contract_validation import (
    format_utc_timestamp,
    validate_utc_timestamp,
)


_SCHEMA_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_DEFAULT_SCHEMA = "agent_runtime_control_v2"
_SCHEMA_RELEASE_REF = "registry-schema:agent_runtime_control@v2"
_MIGRATION_CONTROL_SCHEMA = "agent_runtime_registry_migration"
_MIGRATION_CONTROL_COLUMNS = (
    "migration_id",
    "source_schema",
    "target_schema",
    "source_structure_sha256",
    "source_row_identity_sha256",
    "plan_sha256",
    "source_fence_state",
    "planned_at_utc",
)
_V1_RELEASE_TABLES = (
    "schema_asset_release",
    "prompt_component_release",
    "prompt_bundle_release",
    "execution_profile_release",
    "runtime_module_release",
    "workflow_release",
)
_V1_TABLES = (
    *_V1_RELEASE_TABLES,
    "workflow_node_binding",
    "workflow_edge",
    "workflow_parallel_group_binding",
    "release_admission",
    "active_release_pointer",
)
_RELEASE_TABLES = (
    "schema_asset_release",
    "prompt_component_release",
    "prompt_bundle_release",
    "behavior_policy_release",
    "evaluation_policy_release",
    "retry_policy_release",
    "execution_variant_policy_release",
    "execution_profile_release",
    "runtime_module_release",
    "workflow_release",
)
_REGISTRY_TABLES = (
    *_RELEASE_TABLES,
    "workflow_node_binding",
    "workflow_edge",
    "workflow_parallel_group_binding",
    "active_release_pointer",
    "registry_release_identity_migration",
    "registry_schema_installation",
)


def _catalog_mutation_lock_key(schema: str) -> str:
    return f"{schema}:runtime_release_catalog_mutation"


def _registry_schema_release_sha256() -> str:
    return _canonical_sha256(
        {
            "schema_release_ref": _SCHEMA_RELEASE_REF,
            "release_tables": list(_RELEASE_TABLES),
            "registry_tables": list(_REGISTRY_TABLES),
            "schema_version": 2,
        }
    )


@dataclass(frozen=True)
class RegistrySchemaInstallation:
    """Observed installation state for one configured Registry schema."""

    schema: str
    state: str
    schema_release_ref: str | None
    schema_release_sha256: str | None
    structure_sha256: str | None
    installed_at_utc: str | None

    def validate(self) -> None:
        _validate_schema(self.schema)
        if self.state not in {"ready", "installing", "unknown"}:
            raise ValueError("unsupported Registry schema installation state")
        values = (
            self.schema_release_ref,
            self.schema_release_sha256,
            self.structure_sha256,
            self.installed_at_utc,
        )
        if self.state == "unknown":
            if any(value is not None for value in values):
                raise ValueError("unknown Registry schema cannot claim identity")
            return
        if any(value is None for value in values):
            raise ValueError("known Registry schema requires complete identity")
        if self.schema_release_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.schema_release_sha256
        ):
            raise ValueError("invalid Registry schema release hash")
        if self.structure_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.structure_sha256
        ):
            raise ValueError("invalid Registry schema structure hash")
        validate_utc_timestamp("installed_at_utc", self.installed_at_utc)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_postgres_value(value: Any) -> Any:
    """Normalize textual driver values before canonical JSON or comparison."""

    if isinstance(value, memoryview):
        value = bytes(value)
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _normalize_postgres_rows(rows: Any) -> list[list[Any]]:
    """Return deterministic JSON-compatible rows from one driver result."""

    return [
        [_normalize_postgres_value(value) for value in row]
        for row in rows
    ]


def _validate_schema(schema: str) -> str:
    if type(schema) is not str or not _SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("invalid Postgres schema name")
    return schema


def postgres_release_ddl(schema: str = _DEFAULT_SCHEMA) -> tuple[str, ...]:
    """Return deterministic DDL for the target Runtime release registry."""

    schema = _validate_schema(schema)
    release_table_ddl = tuple(
        f"""
        CREATE TABLE {schema}.{table} (
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
        f"CREATE SCHEMA {schema}",
        *release_table_ddl,
        f"""
        CREATE TABLE {schema}.workflow_node_binding (
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
        CREATE TABLE {schema}.workflow_edge (
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
        CREATE TABLE {schema}.workflow_parallel_group_binding (
            workflow_release_ref TEXT NOT NULL REFERENCES
                {schema}.workflow_release(release_ref),
            group_id TEXT NOT NULL,
            control_node_id TEXT NOT NULL,
            join_node_id TEXT NOT NULL,
            join_policy TEXT NOT NULL,
            row_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            PRIMARY KEY (workflow_release_ref, group_id),
            UNIQUE (workflow_release_ref, control_node_id),
            CHECK (row_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE {schema}.active_release_pointer (
            subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            release_ref TEXT NOT NULL,
            release_sha256 CHAR(64) NOT NULL,
            PRIMARY KEY (subject_kind, subject_id),
            CHECK (release_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE {schema}.registry_release_identity_migration (
            migration_id TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_release_ref TEXT NOT NULL,
            source_release_sha256 CHAR(64) NOT NULL,
            disposition TEXT NOT NULL
                CHECK (disposition IN ('unchanged', 'reissued', 'retired')),
            target_release_ref TEXT,
            target_release_sha256 CHAR(64),
            row_sha256 CHAR(64) NOT NULL,
            PRIMARY KEY (migration_id, source_table, source_release_ref),
            CHECK (source_release_sha256 ~ '^[0-9a-f]{{64}}$'),
            CHECK (target_release_sha256 IS NULL OR
                   target_release_sha256 ~ '^[0-9a-f]{{64}}$'),
            CHECK (row_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE {schema}.registry_schema_installation (
            singleton_id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton_id),
            schema_release_ref TEXT NOT NULL,
            schema_release_sha256 CHAR(64) NOT NULL,
            structure_sha256 CHAR(64) NOT NULL,
            installation_state TEXT NOT NULL
                CHECK (installation_state IN ('installing', 'ready')),
            installed_at_utc TIMESTAMPTZ NOT NULL,
            CHECK (schema_release_sha256 ~ '^[0-9a-f]{{64}}$'),
            CHECK (structure_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
    )


def serialize_registry_tables(
    snapshot: RuntimeReleaseRegistrySnapshot,
) -> Mapping[str, tuple[Mapping[str, Any], ...]]:
    """Project one validated release_registry snapshot into normalized Postgres rows."""

    release_rows: dict[str, tuple[Mapping[str, Any], ...]] = {
        "schema_asset_release": tuple(
            _release_row(
                record.schema_asset_id,
                record.schema_asset_version,
                record,
            )
            for record in snapshot.schema_assets
        ),
        "prompt_component_release": tuple(
            _release_row(
                record.prompt_component_id,
                record.prompt_component_version,
                record,
            )
            for record in snapshot.prompt_components
        ),
        "prompt_bundle_release": tuple(
            _release_row(
                record.prompt_bundle_id,
                record.prompt_bundle_version,
                record,
            )
            for record in snapshot.prompt_bundles
        ),
        "behavior_policy_release": tuple(
            _release_row(record.policy_id, record.policy_version, record)
            for record in snapshot.behavior_policies
        ),
        "evaluation_policy_release": tuple(
            _release_row(record.policy_id, record.policy_version, record)
            for record in snapshot.evaluation_policies
        ),
        "retry_policy_release": tuple(
            _release_row(record.policy_id, record.policy_version, record)
            for record in snapshot.retry_policies
        ),
        "execution_variant_policy_release": tuple(
            _release_row(record.policy_id, record.policy_version, record)
            for record in snapshot.execution_variant_policies
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
    parallel_group_rows = tuple(
        {
            "workflow_release_ref": workflow.release_ref,
            "group_id": group.group_id,
            "control_node_id": group.control_node_id,
            "join_node_id": group.join_node_id,
            "join_policy": group.join_policy.value,
            "row_sha256": _canonical_sha256(group.as_dict()),
            "payload": group.as_dict(),
        }
        for workflow in snapshot.workflows
        for group in workflow.parallel_groups
    )
    release_hashes = {
        record.release_ref: record.release_sha256
        for records in (
            snapshot.schema_assets,
            snapshot.prompt_components,
            snapshot.prompt_bundles,
            snapshot.behavior_policies,
            snapshot.evaluation_policies,
            snapshot.retry_policies,
            snapshot.execution_variant_policies,
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
            "workflow_node_binding": node_rows,
            "workflow_edge": edge_rows,
            "workflow_parallel_group_binding": parallel_group_rows,
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


def _release_identity(record: Any) -> tuple[str, str]:
    for id_field, version_field in (
        ("schema_asset_id", "schema_asset_version"),
        ("prompt_component_id", "prompt_component_version"),
        ("prompt_bundle_id", "prompt_bundle_version"),
        ("policy_id", "policy_version"),
        ("execution_profile_id", "execution_profile_version"),
        ("module_id", "module_version"),
        ("workflow_id", "workflow_version"),
    ):
        if hasattr(record, id_field) and hasattr(record, version_field):
            return (getattr(record, id_field), getattr(record, version_field))
    raise TypeError("Runtime release has no stable id/version identity")


class PostgresRuntimeReleaseStore:
    """Postgres persistence adapter for immutable control-plane releases."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        schema: str = _DEFAULT_SCHEMA,
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
        schema: str = _DEFAULT_SCHEMA,
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

    def create_schema(
        self,
        *,
        installed_at_utc: str,
    ) -> RegistrySchemaInstallation:
        """Explicitly install one clean v2 Registry schema.

        This administrator operation performs DDL. Ordinary registration and
        reads call :meth:`installed_schema_release` and refuse any schema that
        is absent, partial, installing, structurally changed, or unsupported.
        """

        validate_utc_timestamp("installed_at_utc", installed_at_utc)

        def create(cursor: Any) -> RegistrySchemaInstallation:
            for statement in postgres_release_ddl(self.schema):
                cursor.execute(statement)
            structure_sha256 = _schema_structure_sha256(cursor, self.schema)
            installation = RegistrySchemaInstallation(
                schema=self.schema,
                state="ready",
                schema_release_ref=_SCHEMA_RELEASE_REF,
                schema_release_sha256=_registry_schema_release_sha256(),
                structure_sha256=structure_sha256,
                installed_at_utc=installed_at_utc,
            )
            installation.validate()
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.registry_schema_installation
                    (singleton_id, schema_release_ref, schema_release_sha256,
                     structure_sha256, installation_state, installed_at_utc)
                VALUES (TRUE, %s, %s, %s, %s, %s)
                """,
                (
                    installation.schema_release_ref,
                    installation.schema_release_sha256,
                    installation.structure_sha256,
                    installation.state,
                    installation.installed_at_utc,
                ),
            )
            return installation

        return self._transaction(create)

    def installed_schema_release(self) -> RegistrySchemaInstallation:
        """Inspect the configured schema without creating or changing it."""

        return self._transaction(
            lambda cursor: _inspect_schema_installation(cursor, self.schema),
            read_only=True,
        )

    def migrate_schema(
        self,
        plan: RegistrySchemaMigrationPlan,
    ) -> RegistrySchemaInstallation:
        """Execute one explicit side-by-side v1-to-v2 migration plan."""

        _validate_migration_plan_target(plan, self.schema)
        connection = self._connection_factory()
        cursor = connection.cursor()
        lock_key = _migration_lock_key(plan)
        try:
            cursor.execute(
                "SELECT pg_catalog.pg_advisory_lock(pg_catalog.hashtext(%s))",
                (lock_key,),
            )
            _prepare_schema_migration(cursor, plan)
            connection.commit()
            _install_migration_target(cursor, plan)
            connection.commit()
            installation = self._finish_schema_migration(cursor, plan)
            connection.commit()
            return installation
        except Exception:
            connection.rollback()
            raise
        finally:
            try:
                cursor.execute(
                    "SELECT pg_catalog.pg_advisory_unlock(pg_catalog.hashtext(%s))",
                    (lock_key,),
                )
            finally:
                cursor.close()
                connection.close()

    def resume_schema_migration(
        self,
        plan: RegistrySchemaMigrationPlan,
    ) -> RegistrySchemaInstallation:
        """Resume the exact reviewed plan from a durable installing target."""

        _validate_migration_plan_target(plan, self.schema)
        connection = self._connection_factory()
        cursor = connection.cursor()
        lock_key = _migration_lock_key(plan)
        try:
            cursor.execute(
                "SELECT pg_catalog.pg_advisory_lock(pg_catalog.hashtext(%s))",
                (lock_key,),
            )
            _require_matching_migration_control(cursor, plan)
            installation = _inspect_schema_installation(cursor, plan.target_schema)
            if installation.state != "installing":
                raise RuntimeError(
                    "Registry migration resume requires an installing target"
                )
            if _source_row_identity_sha256(cursor, plan.source_schema) != (
                plan.source_row_identity_sha256
            ):
                raise RuntimeError("fenced Registry source row identity changed")
            result = self._finish_schema_migration(cursor, plan)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            try:
                cursor.execute(
                    "SELECT pg_catalog.pg_advisory_unlock(pg_catalog.hashtext(%s))",
                    (lock_key,),
                )
            finally:
                cursor.close()
                connection.close()

    def abort_schema_migration(
        self,
        plan: RegistrySchemaMigrationPlan,
    ) -> None:
        """Remove the v1 source-write fence only after target removal out of band."""

        _validate_migration_plan_target(plan, self.schema)
        connection = self._connection_factory()
        cursor = connection.cursor()
        lock_key = _migration_lock_key(plan)
        try:
            cursor.execute(
                "SELECT pg_catalog.pg_advisory_lock(pg_catalog.hashtext(%s))",
                (lock_key,),
            )
            _require_matching_migration_control(cursor, plan)
            if _schema_exists(cursor, plan.target_schema):
                raise RuntimeError(
                    "Registry migration abort requires the unselected target "
                    "schema to be removed first"
                )
            for table in _V1_TABLES:
                cursor.execute(
                    f"DROP TRIGGER IF EXISTS runtime_registry_write_fence "
                    f"ON {plan.source_schema}.{table}"
                )
            cursor.execute(
                f"""
                DELETE FROM {_MIGRATION_CONTROL_SCHEMA}.
                    registry_schema_migration_control
                WHERE migration_id = %s AND plan_sha256 = %s
                """,
                (plan.migration_id, plan.plan_sha256),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            try:
                cursor.execute(
                    "SELECT pg_catalog.pg_advisory_unlock(pg_catalog.hashtext(%s))",
                    (lock_key,),
                )
            finally:
                cursor.close()
                connection.close()

    def register_bundle(
        self,
        candidate_bundle: RuntimeReleaseBundle,
    ) -> RuntimeReleaseRegistrationResult:
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

        def register(cursor: Any) -> RuntimeReleaseRegistrationResult:
            _require_ready_schema(cursor, self.schema)
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (_catalog_mutation_lock_key(self.schema),),
            )
            release_registry = self._load_release_registry(cursor)
            result = release_registry.register_bundle(candidate_bundle)
            self._write_release_registry(cursor, release_registry)
            return result

        return self._transaction(register)

    def set_active_release(
        self,
        subject_kind: ReleaseSubjectKind,
        subject_id: str,
        release_ref: str,
        release_sha256: str,
    ) -> RuntimeActiveReleasePointerResult:
        """Atomically set one persisted Module or Workflow active pointer."""

        def set_pointer(cursor: Any) -> RuntimeActiveReleasePointerResult:
            _require_ready_schema(cursor, self.schema)
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (_catalog_mutation_lock_key(self.schema),),
            )
            release_registry = self._load_release_registry_unchecked(cursor)
            before = release_registry.snapshot().active_release_refs.get(
                f"{subject_kind.value}:{subject_id}"
            )
            result = release_registry.set_active_release(
                subject_kind,
                subject_id,
                release_ref,
                release_sha256,
            )
            if before == result.active_release_ref:
                return result
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.active_release_pointer
                    (subject_kind, subject_id, release_ref, release_sha256)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (subject_kind, subject_id) DO UPDATE
                    SET release_ref = EXCLUDED.release_ref,
                        release_sha256 = EXCLUDED.release_sha256
                WHERE {self.schema}.active_release_pointer.release_ref
                          IS DISTINCT FROM EXCLUDED.release_ref
                   OR {self.schema}.active_release_pointer.release_sha256
                          IS DISTINCT FROM EXCLUDED.release_sha256
                """,
                (
                    subject_kind.value,
                    subject_id,
                    result.active_release_ref,
                    result.active_release_sha256,
                ),
            )
            return result

        return self._transaction(set_pointer)

    def clear_active_release(
        self,
        subject_kind: ReleaseSubjectKind,
        subject_id: str,
        *,
        expected_release_ref: str,
        expected_release_sha256: str,
    ) -> RuntimeActiveReleasePointerResult:
        """Atomically clear one persisted pointer with an exact precondition."""

        def clear_pointer(cursor: Any) -> RuntimeActiveReleasePointerResult:
            _require_ready_schema(cursor, self.schema)
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (_catalog_mutation_lock_key(self.schema),),
            )
            release_registry = self._load_release_registry_unchecked(cursor)
            before = release_registry.snapshot().active_release_refs.get(
                f"{subject_kind.value}:{subject_id}"
            )
            result = release_registry.clear_active_release(
                subject_kind,
                subject_id,
                expected_release_ref=expected_release_ref,
                expected_release_sha256=expected_release_sha256,
            )
            if before is None:
                return result
            cursor.execute(
                f"""
                DELETE FROM {self.schema}.active_release_pointer
                WHERE subject_kind = %s
                  AND subject_id = %s
                  AND release_ref = %s
                  AND release_sha256 = %s
                RETURNING release_ref
                """,
                (
                    subject_kind.value,
                    subject_id,
                    expected_release_ref,
                    expected_release_sha256,
                ),
            )
            if cursor.fetchone() is None:
                raise RuntimeError("active pointer changed during exact clear")
            return result

        return self._transaction(clear_pointer)

    def load_release_registry(self) -> RuntimeReleaseRegistry:
        """Load persisted immutable releases and active pointers."""

        return self._transaction(self._load_release_registry)

    def _load_release_registry(self, cursor: Any) -> RuntimeReleaseRegistry:
        """Load one complete release_registry through an existing transaction cursor."""

        _require_ready_schema(cursor, self.schema)
        return self._load_release_registry_unchecked(cursor)

    def _load_release_registry_unchecked(
        self,
        cursor: Any,
    ) -> RuntimeReleaseRegistry:
        """Load catalog rows after an administrator verified migration state."""

        records: dict[str, list[Any]] = {}
        decoders = {
            "schema_asset_release": SchemaAssetRelease.from_dict,
            "prompt_component_release": (
                PromptComponentRelease.from_dict
            ),
            "prompt_bundle_release": PromptBundleRelease.from_dict,
            "behavior_policy_release": BehaviorPolicyRelease.from_dict,
            "evaluation_policy_release": EvaluationPolicyRelease.from_dict,
            "retry_policy_release": RetryPolicyRelease.from_dict,
            "execution_variant_policy_release": (
                ExecutionVariantPolicyRelease.from_dict
            ),
            "execution_profile_release": ExecutionProfileRelease.from_dict,
            "runtime_module_release": ModuleRelease.from_dict,
            "workflow_release": WorkflowRelease.from_dict,
        }
        for table, decoder in decoders.items():
            cursor.execute(
                f"SELECT subject_id, release_version, release_ref, "
                f"release_sha256, payload FROM {self.schema}.{table} "
                "ORDER BY release_ref"
            )
            decoded: list[Any] = []
            for row in cursor.fetchall():
                record = decoder(_payload(row[4]))
                record.validate()
                subject_id, release_version = _release_identity(record)
                observed = tuple(
                    _normalize_postgres_value(value) for value in row[:4]
                )
                expected = (
                    subject_id,
                    release_version,
                    record.release_ref,
                    record.release_sha256,
                )
                if observed != expected:
                    raise RuntimeError(
                        f"Postgres {table} identity columns differ from payload"
                    )
                decoded.append(record)
            records[table] = decoded
        release_registry = RuntimeReleaseRegistry()
        bundle = RuntimeReleaseBundle(
            schema_assets=tuple(records["schema_asset_release"]),
            prompt_components=tuple(
                records["prompt_component_release"]
            ),
            prompt_bundles=tuple(records["prompt_bundle_release"]),
            behavior_policies=tuple(records["behavior_policy_release"]),
            evaluation_policies=tuple(records["evaluation_policy_release"]),
            retry_policies=tuple(records["retry_policy_release"]),
            execution_variant_policies=tuple(
                records["execution_variant_policy_release"]
            ),
            execution_profiles=tuple(records["execution_profile_release"]),
            modules=tuple(records["runtime_module_release"]),
            workflows=tuple(records["workflow_release"]),
        )
        if not bundle.is_empty():
            release_registry._restore_persisted_bundle(bundle)
        cursor.execute(
            f"""
            SELECT subject_kind, subject_id, release_ref, release_sha256
            FROM {self.schema}.active_release_pointer
            ORDER BY subject_kind, subject_id
            """
        )
        persisted_active: dict[str, str] = {}
        for row in cursor.fetchall():
            subject_kind = ReleaseSubjectKind(_normalize_postgres_value(row[0]))
            subject_id = _normalize_postgres_value(row[1])
            release_ref = _normalize_postgres_value(row[2])
            release_sha256 = _normalize_postgres_value(row[3])
            release_registry.set_active_release(
                subject_kind,
                subject_id,
                release_ref,
                release_sha256,
            )
            persisted_active[f"{subject_kind.value}:{subject_id}"] = release_ref
        if persisted_active != dict(release_registry.snapshot().active_release_refs):
            raise RuntimeError(
                "Postgres active release pointers do not resolve exactly"
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
        for row in rows["workflow_node_binding"]:
            self._put_node(cursor, row)
        for row in rows["workflow_edge"]:
            self._put_edge(cursor, row)
        for row in rows["workflow_parallel_group_binding"]:
            self._put_parallel_group(cursor, row)

    def _write_active_pointers(
        self,
        cursor: Any,
        snapshot: RuntimeReleaseRegistrySnapshot,
    ) -> None:
        rows = serialize_registry_tables(snapshot)
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

    def _finish_schema_migration(
        self,
        cursor: Any,
        plan: RegistrySchemaMigrationPlan,
    ) -> RegistrySchemaInstallation:
        """Populate, verify, and mark one installing target ready."""

        target_registry = RuntimeReleaseRegistry()
        target_registry.register_bundle(plan.target_bundle)
        for disposition in plan.active_pointer_dispositions:
            if disposition.disposition == "removed":
                continue
            if (
                disposition.target_release_ref is None
                or disposition.target_release_sha256 is None
            ):
                raise RuntimeError("retained active pointer has no exact target")
            target_registry.set_active_release(
                ReleaseSubjectKind(disposition.subject_kind),
                disposition.subject_id,
                disposition.target_release_ref,
                disposition.target_release_sha256,
            )
        _validate_target_lifecycle_dispositions(target_registry, plan)
        self._write_release_registry(cursor, target_registry)
        self._write_active_pointers(cursor, target_registry.snapshot())
        _write_identity_migration_rows(cursor, plan)
        if _source_row_identity_sha256(cursor, plan.source_schema) != (
            plan.source_row_identity_sha256
        ):
            raise RuntimeError("fenced Registry source row identity changed")
        reloaded = self._load_release_registry_unchecked(cursor)
        if reloaded.snapshot() != target_registry.snapshot():
            raise RuntimeError("migrated Registry catalog differs after reload")
        cursor.execute(
            f"""
            UPDATE {plan.target_schema}.registry_schema_installation
            SET installation_state = 'ready'
            WHERE singleton_id = TRUE
              AND schema_release_ref = %s
              AND schema_release_sha256 = %s
              AND installation_state = 'installing'
            RETURNING structure_sha256, installed_at_utc
            """,
            (
                plan.target_schema_release_ref,
                plan.target_schema_release_sha256,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError("Registry migration target is not installing")
        cursor.execute(
            f"""
            UPDATE {_MIGRATION_CONTROL_SCHEMA}.registry_schema_migration_control
            SET source_fence_state = 'ready'
            WHERE migration_id = %s AND plan_sha256 = %s
            """,
            (plan.migration_id, plan.plan_sha256),
        )
        installed_at_utc = row[1]
        if not isinstance(installed_at_utc, str):
            installed_at_utc = format_utc_timestamp(installed_at_utc)
        installation = RegistrySchemaInstallation(
            schema=plan.target_schema,
            state="ready",
            schema_release_ref=plan.target_schema_release_ref,
            schema_release_sha256=plan.target_schema_release_sha256,
            structure_sha256=_normalize_postgres_value(row[0]),
            installed_at_utc=installed_at_utc,
        )
        installation.validate()
        return installation

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

    def _put_parallel_group(self, cursor: Any, row: Mapping[str, Any]) -> None:
        self._put_child(
            cursor,
            table="workflow_parallel_group_binding",
            key_columns=("workflow_release_ref", "group_id"),
            row=row,
            additional_columns=(
                "control_node_id",
                "join_node_id",
                "join_policy",
            ),
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

    def _transaction(
        self,
        operation: Callable[[Any], Any],
        *,
        read_only: bool = False,
    ) -> Any:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            try:
                if read_only:
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


class PostgresRuntimeReleaseQueryStore:
    """Least-authority PostgreSQL reader for immutable Workflow releases."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        schema: str = _DEFAULT_SCHEMA,
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
        schema: str = _DEFAULT_SCHEMA,
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
            _require_ready_schema(cursor, self.schema)
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


def _validate_migration_plan_target(
    plan: RegistrySchemaMigrationPlan,
    configured_schema: str,
) -> None:
    if type(plan) is not RegistrySchemaMigrationPlan:
        raise ValueError("plan must be a RegistrySchemaMigrationPlan")
    plan.validate()
    if plan.target_schema != configured_schema:
        raise ValueError("migration target differs from configured Registry schema")
    if (
        plan.target_schema_release_ref != _SCHEMA_RELEASE_REF
        or plan.target_schema_release_sha256
        != _registry_schema_release_sha256()
    ):
        raise ValueError("migration targets an unsupported Registry schema release")


def _migration_lock_key(plan: RegistrySchemaMigrationPlan) -> str:
    return (
        f"agent_runtime_registry_migration:{plan.source_schema}:"
        f"{plan.target_schema}"
    )


def _schema_exists(cursor: Any, schema: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM pg_catalog.pg_namespace WHERE nspname = %s
        )
        """,
        (schema,),
    )
    row = cursor.fetchone()
    return bool(row and row[0])


def _source_row_identity_payload(cursor: Any, schema: str) -> dict[str, Any]:
    releases: dict[str, list[list[Any]]] = {}
    for table in _V1_RELEASE_TABLES:
        cursor.execute(
            f"""
            SELECT release_ref, release_sha256
            FROM {schema}.{table}
            ORDER BY release_ref
            """
        )
        releases[table] = _normalize_postgres_rows(cursor.fetchall())
    cursor.execute(
        f"""
        SELECT admission_id, admission_sha256, release_ref, release_sha256
        FROM {schema}.release_admission
        ORDER BY admission_sequence
        """
    )
    admissions = _normalize_postgres_rows(cursor.fetchall())
    cursor.execute(
        f"""
        SELECT subject_kind, subject_id, release_ref, release_sha256
        FROM {schema}.active_release_pointer
        ORDER BY subject_kind, subject_id
        """
    )
    active_pointers = _normalize_postgres_rows(cursor.fetchall())
    return {
        "releases": releases,
        "admissions": admissions,
        "active_pointers": active_pointers,
    }


def _source_row_identity_sha256(cursor: Any, schema: str) -> str:
    return _canonical_sha256(_source_row_identity_payload(cursor, schema))


def _validate_source_disposition_closure(
    cursor: Any,
    plan: RegistrySchemaMigrationPlan,
) -> None:
    source = _source_row_identity_payload(cursor, plan.source_schema)
    source_release_keys = {
        (table, str(row[0]), str(row[1]))
        for table, rows in source["releases"].items()
        for row in rows
    }
    disposition_keys = {
        (
            disposition.source_table,
            disposition.source_release_ref,
            disposition.source_release_sha256,
        )
        for disposition in plan.release_dispositions
    }
    if disposition_keys != source_release_keys:
        raise RuntimeError(
            "Registry migration plan does not disposition every predecessor "
            "release exactly once"
        )
    source_refs = {row[2] for row in source["admissions"]} | {
        row[2] for row in source["active_pointers"]
    }
    disposition_refs = {
        disposition.source_release_ref
        for disposition in plan.release_dispositions
    }
    if not source_refs.issubset(disposition_refs):
        raise RuntimeError(
            "Registry migration omits admission or active-pointer lineage"
        )
    source_admissions = {
        (str(row[0]), str(row[1])) for row in source["admissions"]
    }
    admission_dispositions = {
        (
            disposition.source_admission_id,
            disposition.source_admission_sha256,
        )
        for disposition in plan.admission_dispositions
    }
    if admission_dispositions != source_admissions:
        raise RuntimeError(
            "Registry migration plan does not disposition every predecessor "
            "admission exactly once"
        )
    source_pointers = {
        (str(row[0]), str(row[1]), str(row[2]), str(row[3]))
        for row in source["active_pointers"]
    }
    pointer_dispositions = {
        (
            disposition.subject_kind,
            disposition.subject_id,
            disposition.source_release_ref,
            disposition.source_release_sha256,
        )
        for disposition in plan.active_pointer_dispositions
    }
    if pointer_dispositions != source_pointers:
        raise RuntimeError(
            "Registry migration plan does not disposition every predecessor "
            "active pointer exactly once"
        )


def _validate_target_lifecycle_dispositions(
    release_registry: RuntimeReleaseRegistry,
    plan: RegistrySchemaMigrationPlan,
) -> None:
    snapshot = release_registry.snapshot()
    for disposition in plan.admission_dispositions:
        if disposition.disposition != "removed":
            raise RuntimeError(
                "target Registry cannot retain predecessor admission state"
            )

    target_active = dict(snapshot.active_release_refs)
    releases = (
        *snapshot.schema_assets,
        *snapshot.prompt_components,
        *snapshot.prompt_bundles,
        *snapshot.behavior_policies,
        *snapshot.evaluation_policies,
        *snapshot.retry_policies,
        *snapshot.execution_variant_policies,
        *snapshot.execution_profiles,
        *snapshot.modules,
        *snapshot.workflows,
    )
    target_hashes = {
        release.release_ref: release.release_sha256 for release in releases
    }
    for disposition in plan.active_pointer_dispositions:
        key = f"{disposition.subject_kind}:{disposition.subject_id}"
        if disposition.disposition == "removed":
            if key in target_active:
                raise RuntimeError(
                    "removed predecessor active pointer remains in target Registry"
                )
            continue
        if target_active.get(key) != disposition.target_release_ref:
            raise RuntimeError(
                "target Registry active pointer differs from migration disposition"
            )
        if target_hashes.get(disposition.target_release_ref) != (
            disposition.target_release_sha256
        ):
            raise RuntimeError(
                "target Registry active release differs from migration disposition"
            )


def _migration_control_ddl() -> tuple[str, ...]:
    return (
        f"CREATE SCHEMA IF NOT EXISTS {_MIGRATION_CONTROL_SCHEMA}",
        f"""
        CREATE TABLE IF NOT EXISTS {_MIGRATION_CONTROL_SCHEMA}.
            registry_schema_migration_control (
            migration_id TEXT PRIMARY KEY,
            source_schema TEXT NOT NULL,
            target_schema TEXT NOT NULL,
            source_structure_sha256 CHAR(64) NOT NULL,
            source_row_identity_sha256 CHAR(64) NOT NULL,
            plan_sha256 CHAR(64) NOT NULL,
            source_fence_state TEXT NOT NULL
                CHECK (source_fence_state IN
                       ('write_fenced', 'installing', 'ready')),
            planned_at_utc TIMESTAMPTZ NOT NULL,
            UNIQUE (source_schema, target_schema),
            CHECK (source_structure_sha256 ~ '^[0-9a-f]{{64}}$'),
            CHECK (source_row_identity_sha256 ~ '^[0-9a-f]{{64}}$'),
            CHECK (plan_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE OR REPLACE FUNCTION {_MIGRATION_CONTROL_SCHEMA}.
            reject_registry_v1_write()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Registry v1 writes are fenced during migration';
        END;
        $$
        """.strip(),
    )


def _require_migration_control_structure(cursor: Any) -> None:
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (
            _MIGRATION_CONTROL_SCHEMA,
            "registry_schema_migration_control",
        ),
    )
    columns = tuple(
        _normalize_postgres_value(row[0])
        for row in cursor.fetchall()
    )
    if columns != _MIGRATION_CONTROL_COLUMNS:
        raise RuntimeError(
            "Registry migration-control table has an unsupported structure"
        )


def _prepare_schema_migration(
    cursor: Any,
    plan: RegistrySchemaMigrationPlan,
) -> None:
    if _schema_exists(cursor, plan.target_schema):
        raise RuntimeError("Registry migration target schema already exists")
    if not _schema_exists(cursor, plan.source_schema):
        raise RuntimeError("Registry migration source schema is absent")
    if _schema_structure_sha256(cursor, plan.source_schema) != (
        plan.source_structure_sha256
    ):
        raise RuntimeError("Registry migration source structure changed")
    if _source_row_identity_sha256(cursor, plan.source_schema) != (
        plan.source_row_identity_sha256
    ):
        raise RuntimeError("Registry migration source row identity changed")
    _validate_source_disposition_closure(cursor, plan)
    for statement in _migration_control_ddl():
        cursor.execute(statement)
    _require_migration_control_structure(cursor)
    cursor.execute(
        f"""
        INSERT INTO {_MIGRATION_CONTROL_SCHEMA}.
            registry_schema_migration_control
            (migration_id, source_schema, target_schema,
             source_structure_sha256, source_row_identity_sha256,
             plan_sha256, source_fence_state, planned_at_utc)
        VALUES (%s, %s, %s, %s, %s, %s, 'write_fenced', %s)
        """,
        (
            plan.migration_id,
            plan.source_schema,
            plan.target_schema,
            plan.source_structure_sha256,
            plan.source_row_identity_sha256,
            plan.plan_sha256,
            plan.planned_at_utc,
        ),
    )
    for table in _V1_TABLES:
        cursor.execute(
            f"""
            CREATE TRIGGER runtime_registry_write_fence
            BEFORE INSERT OR UPDATE OR DELETE ON {plan.source_schema}.{table}
            FOR EACH STATEMENT EXECUTE FUNCTION
                {_MIGRATION_CONTROL_SCHEMA}.reject_registry_v1_write()
            """
        )


def _install_migration_target(
    cursor: Any,
    plan: RegistrySchemaMigrationPlan,
) -> None:
    for statement in postgres_release_ddl(plan.target_schema):
        cursor.execute(statement)
    structure_sha256 = _schema_structure_sha256(cursor, plan.target_schema)
    cursor.execute(
        f"""
        INSERT INTO {plan.target_schema}.registry_schema_installation
            (singleton_id, schema_release_ref, schema_release_sha256,
             structure_sha256, installation_state, installed_at_utc)
        VALUES (TRUE, %s, %s, %s, 'installing', %s)
        """,
        (
            plan.target_schema_release_ref,
            plan.target_schema_release_sha256,
            structure_sha256,
            plan.planned_at_utc,
        ),
    )
    cursor.execute(
        f"""
        UPDATE {_MIGRATION_CONTROL_SCHEMA}.registry_schema_migration_control
        SET source_fence_state = 'installing'
        WHERE migration_id = %s AND plan_sha256 = %s
        """,
        (plan.migration_id, plan.plan_sha256),
    )


def _require_matching_migration_control(
    cursor: Any,
    plan: RegistrySchemaMigrationPlan,
) -> None:
    cursor.execute(
        f"""
        SELECT source_schema, target_schema, source_structure_sha256,
               source_row_identity_sha256, plan_sha256, source_fence_state
        FROM {_MIGRATION_CONTROL_SCHEMA}.registry_schema_migration_control
        WHERE migration_id = %s
        """,
        (plan.migration_id,),
    )
    row = cursor.fetchone()
    expected = (
        plan.source_schema,
        plan.target_schema,
        plan.source_structure_sha256,
        plan.source_row_identity_sha256,
        plan.plan_sha256,
    )
    if row is None or tuple(
        _normalize_postgres_value(value) for value in row[:5]
    ) != expected:
        raise RuntimeError("Registry migration control differs from reviewed plan")


def _write_identity_migration_rows(
    cursor: Any,
    plan: RegistrySchemaMigrationPlan,
) -> None:
    for disposition in plan.release_dispositions:
        payload = disposition.as_dict()
        cursor.execute(
            f"""
            INSERT INTO {plan.target_schema}.registry_release_identity_migration
                (migration_id, source_table, source_release_ref,
                 source_release_sha256, disposition, target_release_ref,
                 target_release_sha256, row_sha256)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (migration_id, source_table, source_release_ref)
            DO UPDATE SET row_sha256 =
                registry_release_identity_migration.row_sha256
            WHERE registry_release_identity_migration.row_sha256 =
                EXCLUDED.row_sha256
            RETURNING row_sha256
            """,
            (
                plan.migration_id,
                disposition.source_table,
                disposition.source_release_ref,
                disposition.source_release_sha256,
                disposition.disposition,
                disposition.target_release_ref,
                disposition.target_release_sha256,
                _canonical_sha256(payload),
            ),
        )
        if cursor.fetchone() is None:
            raise RuntimeError("Registry identity migration row changed")


def _schema_structure_sha256(cursor: Any, schema: str) -> str:
    """Hash the live v2 table, column, constraint, and index structure."""

    cursor.execute(
        """
        SELECT c.relname, a.attnum, a.attname,
               pg_catalog.format_type(a.atttypid, a.atttypmod),
               a.attnotnull,
               COALESCE(pg_catalog.pg_get_expr(d.adbin, d.adrelid), '')
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute AS a ON a.attrelid = c.oid
        LEFT JOIN pg_catalog.pg_attrdef AS d
          ON d.adrelid = c.oid AND d.adnum = a.attnum
        WHERE n.nspname = %s
          AND c.relkind IN ('r', 'p')
          AND a.attnum > 0
          AND NOT a.attisdropped
        ORDER BY c.relname, a.attnum
        """,
        (schema,),
    )
    columns = _normalize_postgres_rows(cursor.fetchall())
    cursor.execute(
        """
        SELECT c.relname, con.conname, con.contype,
               pg_catalog.pg_get_constraintdef(con.oid, TRUE)
        FROM pg_catalog.pg_constraint AS con
        JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname = %s
        ORDER BY c.relname, con.conname
        """,
        (schema,),
    )
    constraints = _normalize_postgres_rows(cursor.fetchall())
    cursor.execute(
        """
        SELECT tablename, indexname, indexdef
        FROM pg_catalog.pg_indexes
        WHERE schemaname = %s
        ORDER BY tablename, indexname
        """,
        (schema,),
    )
    indexes = _normalize_postgres_rows(cursor.fetchall())
    return _canonical_sha256(
        {
            "columns": columns,
            "constraints": constraints,
            "indexes": indexes,
        }
    )


def _unknown_schema_installation(schema: str) -> RegistrySchemaInstallation:
    installation = RegistrySchemaInstallation(
        schema=schema,
        state="unknown",
        schema_release_ref=None,
        schema_release_sha256=None,
        structure_sha256=None,
        installed_at_utc=None,
    )
    installation.validate()
    return installation


def _inspect_schema_installation(
    cursor: Any,
    schema: str,
) -> RegistrySchemaInstallation:
    cursor.execute(
        "SELECT pg_catalog.to_regclass(%s)",
        (f"{schema}.registry_schema_installation",),
    )
    existence = cursor.fetchone()
    if existence is None or existence[0] is None:
        return _unknown_schema_installation(schema)
    cursor.execute(
        f"""
        SELECT schema_release_ref, schema_release_sha256, structure_sha256,
               installation_state, installed_at_utc
        FROM {schema}.registry_schema_installation
        WHERE singleton_id = TRUE
        """
    )
    row = cursor.fetchone()
    if row is None:
        return _unknown_schema_installation(schema)
    installed_at_utc = row[4]
    if not isinstance(installed_at_utc, str):
        installed_at_utc = format_utc_timestamp(installed_at_utc)
    installation = RegistrySchemaInstallation(
        schema=schema,
        schema_release_ref=_normalize_postgres_value(row[0]),
        schema_release_sha256=_normalize_postgres_value(row[1]),
        structure_sha256=_normalize_postgres_value(row[2]),
        state=_normalize_postgres_value(row[3]),
        installed_at_utc=installed_at_utc,
    )
    installation.validate()
    if (
        installation.schema_release_ref != _SCHEMA_RELEASE_REF
        or installation.schema_release_sha256
        != _registry_schema_release_sha256()
    ):
        return _unknown_schema_installation(schema)
    if _schema_structure_sha256(cursor, schema) != installation.structure_sha256:
        return _unknown_schema_installation(schema)
    return installation


def _require_ready_schema(cursor: Any, schema: str) -> None:
    installation = _inspect_schema_installation(cursor, schema)
    if installation.state != "ready":
        raise RuntimeError(
            "Registry PostgreSQL schema is absent, installing, structurally "
            "changed, or unsupported"
        )


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
    "RegistrySchemaInstallation",
    "postgres_release_ddl",
    "serialize_registry_tables",
]
