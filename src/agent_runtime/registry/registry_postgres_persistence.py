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
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Callable, Mapping

from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    ExecutionProfileRelease,
    EvaluationPolicyRelease,
    ExecutionVariantPolicyRelease,
    PromptComponentRelease,
    PromptBundleRelease,
    ReleaseAdmissionRecord,
    RetryPolicyRelease,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    WorkflowRelease,
)
from ..registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
    RuntimeReleaseRegistrySnapshot,
)
from ..foundation.foundation_contract_validation import validate_utc_timestamp


_SCHEMA_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
REGISTRY_SCHEMA_RELEASE_ID = "agent_runtime_registry_v2"
_INSTALLATION_STATES = frozenset({"installing", "ready"})
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

_REQUIRED_TABLE_COLUMNS = {
    "registry_schema_installation": (
        "singleton",
        "schema_release_id",
        "schema_fingerprint_sha256",
        "installation_state",
        "recorded_at_utc",
    ),
    **{
        table: (
            "subject_id",
            "release_version",
            "release_ref",
            "release_sha256",
            "payload",
        )
        for table in _RELEASE_TABLES
    },
    "workflow_node_binding": (
        "workflow_release_ref",
        "node_id",
        "node_kind",
        "module_release_ref",
        "row_sha256",
        "payload",
    ),
    "workflow_edge": (
        "workflow_release_ref",
        "source_node_id",
        "outcome_id",
        "row_sha256",
        "payload",
    ),
    "workflow_parallel_group_binding": (
        "workflow_release_ref",
        "group_id",
        "control_node_id",
        "join_node_id",
        "join_policy",
        "row_sha256",
        "payload",
    ),
    "release_admission": (
        "admission_sequence",
        "admission_id",
        "subject_kind",
        "subject_id",
        "release_ref",
        "release_sha256",
        "state",
        "admission_intent_sha256",
        "recorded_at_utc",
        "admission_sha256",
        "payload",
    ),
    "active_release_pointer": (
        "subject_kind",
        "subject_id",
        "release_ref",
        "release_sha256",
    ),
}


def _column_signature(table: str, column: str) -> tuple[str, str, str, str]:
    if column == "singleton":
        data_type = "boolean"
    elif column == "admission_sequence":
        data_type = "bigint"
    elif column == "payload":
        data_type = "jsonb"
    elif column == "recorded_at_utc":
        data_type = "timestamp with time zone"
    elif column.endswith("sha256"):
        data_type = "character"
    else:
        data_type = "text"
    nullable = "YES" if (
        table == "workflow_node_binding" and column == "module_release_ref"
    ) else "NO"
    return table, column, data_type, nullable


_REQUIRED_COLUMN_SIGNATURES = tuple(
    _column_signature(table, column)
    for table, columns in sorted(_REQUIRED_TABLE_COLUMNS.items())
    for column in columns
)

_REQUIRED_KEY_CONSTRAINTS = tuple(
    sorted(
        [
            ("registry_schema_installation", "PRIMARY KEY", "singleton"),
            *(
                (table, "PRIMARY KEY", "release_ref")
                for table in _RELEASE_TABLES
            ),
            *(
                (table, "UNIQUE", "subject_id,release_version")
                for table in _RELEASE_TABLES
            ),
            (
                "workflow_node_binding",
                "PRIMARY KEY",
                "workflow_release_ref,node_id",
            ),
            (
                "workflow_node_binding",
                "FOREIGN KEY",
                "workflow_release_ref",
            ),
            (
                "workflow_node_binding",
                "FOREIGN KEY",
                "module_release_ref",
            ),
            (
                "workflow_edge",
                "PRIMARY KEY",
                "workflow_release_ref,source_node_id,outcome_id",
            ),
            ("workflow_edge", "FOREIGN KEY", "workflow_release_ref"),
            (
                "workflow_parallel_group_binding",
                "PRIMARY KEY",
                "workflow_release_ref,group_id",
            ),
            (
                "workflow_parallel_group_binding",
                "UNIQUE",
                "workflow_release_ref,control_node_id",
            ),
            (
                "workflow_parallel_group_binding",
                "FOREIGN KEY",
                "workflow_release_ref",
            ),
            ("release_admission", "PRIMARY KEY", "admission_sequence"),
            ("release_admission", "UNIQUE", "admission_id"),
            (
                "release_admission",
                "UNIQUE",
                "subject_kind,release_ref,admission_sha256",
            ),
            (
                "active_release_pointer",
                "PRIMARY KEY",
                "subject_kind,subject_id",
            ),
        ]
    )
)

_HASH_CHECK = "CHECK ({column} ~ '^[0-9a-f]{{64}}$'::text)"
_REQUIRED_CHECK_CONSTRAINTS = tuple(
    sorted(
        [
            *(
                (
                    table,
                    f"{table}_release_sha256_check",
                    _HASH_CHECK.format(column="release_sha256"),
                )
                for table in _RELEASE_TABLES
            ),
            (
                "workflow_node_binding",
                "workflow_node_binding_row_sha256_check",
                _HASH_CHECK.format(column="row_sha256"),
            ),
            (
                "workflow_edge",
                "workflow_edge_row_sha256_check",
                _HASH_CHECK.format(column="row_sha256"),
            ),
            (
                "workflow_parallel_group_binding",
                "workflow_parallel_group_binding_row_sha256_check",
                _HASH_CHECK.format(column="row_sha256"),
            ),
            (
                "release_admission",
                "release_admission_release_sha256_check",
                _HASH_CHECK.format(column="release_sha256"),
            ),
            (
                "release_admission",
                "release_admission_admission_intent_sha256_check",
                _HASH_CHECK.format(column="admission_intent_sha256"),
            ),
            (
                "release_admission",
                "release_admission_admission_sha256_check",
                _HASH_CHECK.format(column="admission_sha256"),
            ),
            (
                "active_release_pointer",
                "active_release_pointer_release_sha256_check",
                _HASH_CHECK.format(column="release_sha256"),
            ),
            (
                "registry_schema_installation",
                "registry_schema_installation_hash",
                _HASH_CHECK.format(column="schema_fingerprint_sha256"),
            ),
            (
                "registry_schema_installation",
                "registry_schema_installation_singleton",
                "CHECK (singleton)",
            ),
            (
                "registry_schema_installation",
                "registry_schema_installation_state",
                "CHECK (installation_state = ANY (ARRAY['installing'::text, 'ready'::text]))",
            ),
        ]
    )
)


def _expected_schema_fingerprint() -> str:
    payload = {
        "schema_release_id": REGISTRY_SCHEMA_RELEASE_ID,
        "columns": [list(row) for row in _REQUIRED_COLUMN_SIGNATURES],
        "key_constraints": [list(row) for row in _REQUIRED_KEY_CONSTRAINTS],
        "check_constraints": [
            list(row) for row in _REQUIRED_CHECK_CONSTRAINTS
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


REGISTRY_SCHEMA_FINGERPRINT_SHA256 = _expected_schema_fingerprint()


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


def _registry_v2_ddl(schema: str = "agent_runtime_control") -> tuple[str, ...]:
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
        f"""
        CREATE TABLE {schema}.registry_schema_installation (
            singleton BOOLEAN PRIMARY KEY DEFAULT TRUE,
            schema_release_id TEXT NOT NULL,
            schema_fingerprint_sha256 CHAR(64) NOT NULL,
            installation_state TEXT NOT NULL,
            recorded_at_utc TIMESTAMPTZ NOT NULL,
            CONSTRAINT registry_schema_installation_singleton
                CHECK (singleton),
            CONSTRAINT registry_schema_installation_hash
                CHECK (schema_fingerprint_sha256 ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT registry_schema_installation_state
                CHECK (installation_state IN ('installing', 'ready'))
        )
        """.strip(),
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
        CREATE TABLE {schema}.release_admission (
            admission_sequence BIGSERIAL PRIMARY KEY,
            admission_id TEXT NOT NULL UNIQUE,
            subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            release_ref TEXT NOT NULL,
            release_sha256 CHAR(64) NOT NULL,
            state TEXT NOT NULL,
            admission_intent_sha256 CHAR(64) NOT NULL,
            recorded_at_utc TIMESTAMPTZ NOT NULL,
            admission_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            UNIQUE (subject_kind, release_ref, admission_sha256),
            CHECK (release_sha256 ~ '^[0-9a-f]{{64}}$'),
            CHECK (admission_intent_sha256 ~ '^[0-9a-f]{{64}}$'),
            CHECK (admission_sha256 ~ '^[0-9a-f]{{64}}$')
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
    )


def _serialize_registry_tables(
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
    admission_rows = tuple(
        {
            "admission_id": admission.admission_id,
            "subject_kind": admission.subject_kind.value,
            "subject_id": admission.subject_id,
            "release_ref": admission.release_ref,
            "release_sha256": admission.release_sha256,
            "state": admission.state.value,
            "admission_intent_sha256": admission.admission_intent_sha256,
            "recorded_at_utc": admission.recorded_at_utc,
            "admission_sha256": admission.admission_sha256,
            "payload": admission.as_dict(),
        }
        for admission in snapshot.admissions
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

    def installed_schema_release(self) -> str:
        """Report the exact installed Registry schema state."""

        return self._transaction(self._installed_schema_release)

    def create_schema(self) -> None:
        """Create one absent Registry v2 namespace exactly once."""

        def create(cursor: Any) -> None:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"{self.schema}:registry_schema_create",),
            )
            state = self._installed_schema_release(cursor)
            if state != "absent":
                raise RuntimeError(
                    "Registry create_schema requires an absent namespace: "
                    f"{state}"
                )
            for statement in _registry_v2_ddl(self.schema):
                cursor.execute(statement)
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.registry_schema_installation
                    (singleton, schema_release_id, schema_fingerprint_sha256,
                     installation_state, recorded_at_utc)
                VALUES (TRUE, %s, %s, 'ready', transaction_timestamp())
                """,
                (
                    REGISTRY_SCHEMA_RELEASE_ID,
                    REGISTRY_SCHEMA_FINGERPRINT_SHA256,
                ),
            )

        self._transaction(create)

    def _installed_schema_release(self, cursor: Any) -> str:
        cursor.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = %s)",
            (self.schema,),
        )
        namespace_row = cursor.fetchone()
        if namespace_row is None or not bool(namespace_row[0]):
            return "absent"
        cursor.execute(
            """
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = %s
            ORDER BY table_name, ordinal_position
            """,
            (self.schema,),
        )
        actual_columns = tuple(
            tuple(map(str, row)) for row in cursor.fetchall()
        )
        if actual_columns != _REQUIRED_COLUMN_SIGNATURES:
            return "unknown"
        cursor.execute(
            """
            SELECT tc.table_name, tc.constraint_type,
                   string_agg(kcu.column_name, ',' ORDER BY kcu.ordinal_position)
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_schema = kcu.constraint_schema
             AND tc.constraint_name = kcu.constraint_name
             AND tc.table_name = kcu.table_name
            WHERE tc.table_schema = %s
              AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE', 'FOREIGN KEY')
            GROUP BY tc.table_name, tc.constraint_name, tc.constraint_type
            ORDER BY tc.table_name, tc.constraint_type, 3
            """,
            (self.schema,),
        )
        actual_constraints = tuple(
            sorted(tuple(map(str, row)) for row in cursor.fetchall())
        )
        if actual_constraints != _REQUIRED_KEY_CONSTRAINTS:
            return "unknown"
        cursor.execute(
            """
            SELECT rel.relname, con.conname, pg_get_constraintdef(con.oid, true)
            FROM pg_constraint AS con
            JOIN pg_class AS rel ON rel.oid = con.conrelid
            JOIN pg_namespace AS nsp ON nsp.oid = rel.relnamespace
            WHERE nsp.nspname = %s AND con.contype = 'c'
            ORDER BY rel.relname, con.conname
            """,
            (self.schema,),
        )
        actual_checks = tuple(
            tuple(map(str, row)) for row in cursor.fetchall()
        )
        if actual_checks != _REQUIRED_CHECK_CONSTRAINTS:
            return "unknown"
        cursor.execute(
            f"""
            SELECT schema_release_id, schema_fingerprint_sha256,
                   installation_state
            FROM {self.schema}.registry_schema_installation
            WHERE singleton = TRUE
            """
        )
        row = cursor.fetchone()
        if row is None:
            return "unknown"
        release_id, fingerprint, state = map(str, row)
        if (
            release_id != REGISTRY_SCHEMA_RELEASE_ID
            or fingerprint != REGISTRY_SCHEMA_FINGERPRINT_SHA256
            or state not in _INSTALLATION_STATES
        ):
            return "unknown"
        return REGISTRY_SCHEMA_RELEASE_ID if state == "ready" else "installing"

    def _require_ready(self, cursor: Any) -> None:
        state = self._installed_schema_release(cursor)
        if state != REGISTRY_SCHEMA_RELEASE_ID:
            raise RuntimeError(f"Registry schema is not ready: {state}")

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
            self._require_ready(cursor)

            def recording_clock() -> str:
                cursor.execute("SELECT clock_timestamp()")
                timestamp_row = cursor.fetchone()
                if timestamp_row is None:
                    raise RuntimeError(
                        "Postgres recording timestamp is unavailable"
                    )
                return _utc_text(timestamp_row[0])

            release_registry = self._load_release_registry(
                cursor,
                recording_clock=recording_clock,
            )
            release_registry.register_bundle(candidate_bundle)
            self._write_release_registry(cursor, release_registry)
            return release_registry

        return self._transaction(register)

    def load_release_registry(self) -> RuntimeReleaseRegistry:
        """Load and revalidate all persisted releases and admission transitions."""

        def load(cursor: Any) -> RuntimeReleaseRegistry:
            self._require_ready(cursor)
            return self._load_release_registry(cursor)

        return self._transaction(load)

    def _load_release_registry(
        self,
        cursor: Any,
        *,
        recording_clock: Callable[[], str] | None = None,
    ) -> RuntimeReleaseRegistry:
        """Load one complete release_registry through an existing transaction cursor."""

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
        snapshot = RuntimeReleaseRegistrySnapshot(
            schema_assets=tuple(records["schema_asset_release"]),
            prompt_components=tuple(records["prompt_component_release"]),
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
            admissions=tuple(admissions),
            active_release_refs=persisted_active,
        )
        return RuntimeReleaseRegistry.restore_persisted_snapshot(
            snapshot,
            recording_clock=(recording_clock or _reject_non_store_recording_clock),
        )

    def _write_release_registry(
        self,
        cursor: Any,
        release_registry: RuntimeReleaseRegistry,
    ) -> None:
        """Persist a complete validated release_registry through the current transaction."""

        if type(release_registry) is not RuntimeReleaseRegistry:
            raise ValueError("release_registry must be an exact RuntimeReleaseRegistry")
        rows = _serialize_registry_tables(release_registry.snapshot())
        for table in _RELEASE_TABLES:
            for row in rows[table]:
                self._put_release(cursor, table, row)
        for row in rows["workflow_node_binding"]:
            self._put_node(cursor, row)
        for row in rows["workflow_edge"]:
            self._put_edge(cursor, row)
        for row in rows["workflow_parallel_group_binding"]:
            self._put_parallel_group(cursor, row)
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

    def _put_admission(self, cursor: Any, row: Mapping[str, Any]) -> None:
        cursor.execute(
            f"""
            INSERT INTO {self.schema}.release_admission
                (admission_id, subject_kind, subject_id, release_ref,
                 release_sha256, state, admission_intent_sha256,
                 recorded_at_utc, admission_sha256, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
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
                row["admission_intent_sha256"],
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
            PostgresRuntimeReleaseStore(
                self._connection_factory,
                schema=self.schema,
            )._require_ready(cursor)
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


def _utc_text(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("Postgres recording timestamp must be timezone-aware")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if type(value) is str:
        validate_utc_timestamp("recorded_at_utc", value)
        return value
    raise ValueError("Postgres recording timestamp has an unsupported type")


def _reject_non_store_recording_clock() -> str:
    raise RuntimeError(
        "a PostgreSQL-loaded Registry cannot finalize admissions outside its store"
    )


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
    "REGISTRY_SCHEMA_RELEASE_ID",
]
