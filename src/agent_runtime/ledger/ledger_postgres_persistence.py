"""PostgreSQL system of record for Runtime execution facts and content.

The adapter stores immutable batches and individual ledger records, then
rebuilds the reference ledger before every mutation.  PostgreSQL serialization
and the reference validator therefore share one transaction boundary and one
set of execution invariants.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from types import MappingProxyType, UnionType
from typing import Any, Callable, Mapping, Union, get_args, get_origin, get_type_hints

from ..contracts.ledger_content_definition import RuntimeExecutionContent
from ..contracts.ledger_record_definition import (
    AttemptBeginReceipt,
    AttemptClaim,
    AttemptFinalizationBatch,
    AttemptFinalizationReceipt,
    AttemptOrphaningBatch,
    AttemptOrphaningReceipt,
    BackendAcknowledgementReceipt,
    BackendAcknowledgementRecord,
    CommitReceipt,
    ExecutionOutputRef,
    InvocationCommitRecord,
    LegacyAttemptBeginBatch,
    LegacyOperationGrantBatch,
    LegacyOperationGrantReceipt,
    LegacyRuntimeRecordBatch,
    OutcomeCommitBatch,
    OutcomeCommitReceipt,
    PersistedRuntimeRecord,
    RuntimeExecutionTrace,
    RuntimeLedgerRecord,
    RuntimeRecordBatch,
    WorkflowExecutionRecord,
    runtime_record_as_dict,
    sha256_text,
    stable_runtime_id,
)
from ..contracts.registry_workflow_definition import ModuleOutcome
from .ledger_record_persistence import InMemoryRuntimeExecutionRecordStore


_SCHEMA_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_RECORD_TYPES = {
    record_type.__name__: record_type for record_type in get_args(PersistedRuntimeRecord)
}


def _validate_schema(schema: str) -> str:
    if type(schema) is not str or not _SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("invalid Postgres schema name")
    return schema


def postgres_execution_ledger_ddl(
    schema: str = "agent_runtime_execution",
) -> tuple[str, ...]:
    """Return idempotent DDL for the authoritative execution ledger."""

    schema = _validate_schema(schema)
    return (
        f"CREATE SCHEMA IF NOT EXISTS {schema}",
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.workflow_execution (
            workflow_execution_id TEXT PRIMARY KEY,
            workflow_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            cell_id TEXT NOT NULL,
            principal_id TEXT NOT NULL,
            execution_release_ref TEXT NOT NULL,
            recorded_at_utc TIMESTAMPTZ NOT NULL,
            record_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            payload_canonical BYTEA NOT NULL,
            CHECK (record_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.execution_transaction (
            commit_sequence BIGSERIAL PRIMARY KEY,
            workflow_execution_id TEXT NOT NULL REFERENCES
                {schema}.workflow_execution(workflow_execution_id),
            transaction_id TEXT NOT NULL,
            transaction_sha256 CHAR(64) NOT NULL,
            record_count INTEGER NOT NULL CHECK (record_count > 0),
            committed_outcome_refs JSONB NOT NULL,
            batch_payload JSONB NOT NULL,
            batch_payload_canonical BYTEA NOT NULL,
            committed_at_utc TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (workflow_execution_id, transaction_id),
            CHECK (transaction_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.execution_record (
            record_sequence BIGSERIAL PRIMARY KEY,
            workflow_execution_id TEXT NOT NULL,
            transaction_id TEXT NOT NULL,
            transaction_record_index INTEGER NOT NULL
                CHECK (transaction_record_index >= 0),
            record_type TEXT NOT NULL,
            record_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL,
            payload_canonical BYTEA NOT NULL,
            UNIQUE (
                workflow_execution_id,
                transaction_id,
                transaction_record_index
            ),
            FOREIGN KEY (workflow_execution_id, transaction_id) REFERENCES
                {schema}.execution_transaction(
                    workflow_execution_id,
                    transaction_id
                ),
            CHECK (record_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE TABLE IF NOT EXISTS {schema}.execution_content (
            workflow_execution_id TEXT NOT NULL REFERENCES
                {schema}.workflow_execution(workflow_execution_id),
            content_ref TEXT NOT NULL,
            content_sha256 CHAR(64) NOT NULL,
            media_type TEXT NOT NULL,
            byte_size BIGINT NOT NULL CHECK (byte_size >= 0),
            body BYTEA NOT NULL,
            recorded_at_utc TIMESTAMPTZ NOT NULL,
            PRIMARY KEY (workflow_execution_id, content_ref),
            CHECK (content_sha256 ~ '^[0-9a-f]{{64}}$')
        )
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS workflow_execution_scope_time_idx
        ON {schema}.workflow_execution
            (tenant_id, cell_id, recorded_at_utc DESC, workflow_execution_id)
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS workflow_execution_time_idx
        ON {schema}.workflow_execution
            (recorded_at_utc DESC, workflow_execution_id DESC)
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS execution_transaction_trace_idx
        ON {schema}.execution_transaction
            (workflow_execution_id, commit_sequence)
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS execution_record_type_idx
        ON {schema}.execution_record
            (workflow_execution_id, record_type, record_sequence)
        """.strip(),
        f"""
        CREATE INDEX IF NOT EXISTS execution_content_hash_idx
        ON {schema}.execution_content
            (workflow_execution_id, content_sha256)
        """.strip(),
        f"""
        CREATE OR REPLACE FUNCTION {schema}.reject_execution_ledger_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'Agent Runtime execution records are immutable';
        END;
        $$
        """.strip(),
        *(
            f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_trigger
                    WHERE tgname = '{table}_immutable'
                      AND tgrelid = '{schema}.{table}'::regclass
                ) THEN
                    CREATE TRIGGER {table}_immutable
                    BEFORE UPDATE OR DELETE ON {schema}.{table}
                    FOR EACH ROW EXECUTE FUNCTION
                        {schema}.reject_execution_ledger_mutation();
                END IF;
            END;
            $$
            """.strip()
            for table in (
                "workflow_execution",
                "execution_transaction",
                "execution_record",
                "execution_content",
            )
        ),
    )


@dataclass(frozen=True)
class RuntimeExecutionDescriptor:
    """Content-free execution identity used by authorized query surfaces."""

    workflow_execution_id: str
    workflow_id: str
    tenant_id: str
    cell_id: str
    principal_id: str
    execution_release_ref: str
    recorded_at_utc: str

    def as_dict(self) -> dict[str, str]:
        return {
            "workflow_execution_id": self.workflow_execution_id,
            "workflow_id": self.workflow_id,
            "tenant_id": self.tenant_id,
            "cell_id": self.cell_id,
            "principal_id": self.principal_id,
            "execution_release_ref": self.execution_release_ref,
            "recorded_at_utc": self.recorded_at_utc,
        }


@dataclass(frozen=True)
class RuntimeExecutionPageCursor:
    """Stable keyset boundary for descending execution-time pagination."""

    recorded_at_utc: str
    workflow_execution_id: str

    def validate(self) -> None:
        if type(self.workflow_execution_id) is not str or not self.workflow_execution_id:
            raise ValueError("pagination workflow_execution_id is required")
        try:
            timestamp = _timestamp(self.recorded_at_utc)
        except (AttributeError, ValueError) as exc:
            raise ValueError("pagination recorded_at_utc is invalid") from exc
        if timestamp.tzinfo is None:
            raise ValueError("pagination recorded_at_utc requires a timezone")


class PostgresRuntimeExecutionRecordStore:
    """Atomic PostgreSQL implementation of the Runtime execution store."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        schema: str = "agent_runtime_execution",
        execution_output_integrity_check: Callable[[ExecutionOutputRef], bool]
        | None = None,
    ) -> None:
        if not callable(connection_factory):
            raise ValueError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self._execution_output_integrity_check = execution_output_integrity_check
        self.schema = _validate_schema(schema)

    @classmethod
    def from_dsn(
        cls,
        database_url: str,
        *,
        schema: str = "agent_runtime_execution",
        connect_timeout: int = 8,
    ) -> "PostgresRuntimeExecutionRecordStore":
        if type(database_url) is not str or not database_url:
            raise ValueError("database_url is required")
        if type(connect_timeout) is not int or connect_timeout < 1:
            raise ValueError("connect_timeout must be a positive integer")
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional dependency
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
        def initialize(cursor: Any) -> None:
            cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"agent-runtime:execution-schema:{self.schema}",),
            )
            for statement in postgres_execution_ledger_ddl(self.schema):
                cursor.execute(statement)

        self._transaction(initialize)

    def begin_attempt(self, batch: LegacyAttemptBeginBatch) -> AttemptBeginReceipt:
        return self._mutate(
            batch.workflow_execution_id,
            batch.as_record_batch(),
            lambda store: store.begin_attempt(batch),
        )

    def authorize_operation(
        self,
        batch: LegacyOperationGrantBatch,
    ) -> LegacyOperationGrantReceipt:
        return self._mutate(
            batch.workflow_execution_id,
            batch.as_record_batch(),
            lambda store: store.authorize_operation(batch),
        )

    def finalize_attempt(
        self,
        claim: AttemptClaim,
        batch: AttemptFinalizationBatch,
    ) -> AttemptFinalizationReceipt:
        return self._mutate(
            batch.workflow_execution_id,
            batch.as_record_batch(),
            lambda store: store.finalize_attempt(claim, batch),
        )

    def orphan_attempt(
        self,
        claim: AttemptClaim,
        batch: AttemptOrphaningBatch,
    ) -> AttemptOrphaningReceipt:
        return self._mutate(
            batch.workflow_execution_id,
            batch.as_record_batch(),
            lambda store: store.orphan_attempt(claim, batch),
        )

    def commit_outcome(self, batch: OutcomeCommitBatch) -> OutcomeCommitReceipt:
        return self._mutate(
            batch.workflow_execution_id,
            batch.as_record_batch(),
            lambda store: store.commit_outcome(batch),
        )

    def acknowledge_backend(
        self,
        record: BackendAcknowledgementRecord,
    ) -> BackendAcknowledgementReceipt:
        batch = RuntimeRecordBatch(
            workflow_execution_id=record.workflow_execution_id,
            transaction_id=stable_runtime_id(
                "transaction",
                record.workflow_execution_id,
                record.backend_acknowledgement_id,
            ),
            records=(record,),
        )
        return self._mutate(
            record.workflow_execution_id,
            batch,
            lambda store: store.acknowledge_backend(record),
        )

    def commit(
        self,
        batch: RuntimeRecordBatch | LegacyRuntimeRecordBatch,
    ) -> CommitReceipt:
        return self._mutate(
            batch.workflow_execution_id,
            batch,
            lambda store: store.commit(batch),
        )

    def get_committed_outcome(
        self,
        workflow_execution_id: str,
        dispatch_id: str,
    ) -> ModuleOutcome | None:
        return self._read_transaction(
            lambda cursor: self._load_reference(cursor, workflow_execution_id)
            .get_committed_outcome(workflow_execution_id, dispatch_id)
        )

    def get_committed_invocation(
        self,
        workflow_execution_id: str,
        dispatch_id: str,
    ) -> InvocationCommitRecord | None:
        return self._read_transaction(
            lambda cursor: self._load_reference(cursor, workflow_execution_id)
            .get_committed_invocation(workflow_execution_id, dispatch_id)
        )

    def load_trace(self, workflow_execution_id: str) -> RuntimeExecutionTrace:
        return self._read_transaction(
            lambda cursor: self._load_reference(
                cursor, workflow_execution_id
            ).load_trace(workflow_execution_id)
        )

    def list_executions(
        self,
        *,
        limit: int = 100,
        before: RuntimeExecutionPageCursor | None = None,
    ) -> tuple[RuntimeExecutionDescriptor, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if before is not None:
            if type(before) is not RuntimeExecutionPageCursor:
                raise ValueError("before must be a RuntimeExecutionPageCursor")
            before.validate()

        def load(cursor: Any) -> tuple[RuntimeExecutionDescriptor, ...]:
            boundary = ""
            parameters: tuple[Any, ...] = (limit,)
            if before is not None:
                boundary = (
                    "WHERE (recorded_at_utc, workflow_execution_id) "
                    "< (%s::timestamptz, %s)"
                )
                parameters = (
                    before.recorded_at_utc,
                    before.workflow_execution_id,
                    limit,
                )
            cursor.execute(
                f"""
                SELECT workflow_execution_id, workflow_id, tenant_id, cell_id,
                       principal_id, execution_release_ref,
                       recorded_at_utc
                FROM {self.schema}.workflow_execution
                {boundary}
                ORDER BY recorded_at_utc DESC, workflow_execution_id DESC
                LIMIT %s
                """,
                parameters,
            )
            return tuple(_execution_descriptor(row) for row in cursor.fetchall())

        return self._read_transaction(load)

    def get_execution_descriptor(
        self,
        workflow_execution_id: str,
    ) -> RuntimeExecutionDescriptor | None:
        def load(cursor: Any) -> RuntimeExecutionDescriptor | None:
            cursor.execute(
                f"""
                SELECT workflow_execution_id, workflow_id, tenant_id, cell_id,
                       principal_id, execution_release_ref,
                       recorded_at_utc
                FROM {self.schema}.workflow_execution
                WHERE workflow_execution_id = %s
                """,
                (workflow_execution_id,),
            )
            row = cursor.fetchone()
            return None if row is None else _execution_descriptor(row)

        return self._read_transaction(load)

    def commit_content(
        self,
        content: RuntimeExecutionContent,
    ) -> RuntimeExecutionContent:
        """Stage immutable bytes for a content ref the ledger already declared."""

        content.validate()

        def commit(cursor: Any) -> RuntimeExecutionContent:
            self._lock_execution(cursor, content.workflow_execution_id)
            reference = self._load_reference(cursor, content.workflow_execution_id)
            trace = reference.load_trace(content.workflow_execution_id)
            if not trace.records:
                raise ValueError("content requires an existing Workflow Execution")
            known_hashes = _referenced_content_hashes(trace)
            if content.content_ref not in known_hashes:
                raise ValueError("content_ref is not declared by the execution ledger")
            expected_hash = known_hashes[content.content_ref]
            if expected_hash is not None and expected_hash != content.content_sha256:
                raise ValueError("content hash differs from the execution ledger")
            self._insert_content(cursor, content)
            return content

        return self._transaction(commit)

    def stage_content(
        self,
        content: RuntimeExecutionContent,
    ) -> RuntimeExecutionContent:
        """Stage immutable output bytes before their formal reference commit.

        Staged content is non-authoritative until the execution ledger points
        to it. A crash can therefore leave only a collectible unreferenced body,
        never an authoritative output reference whose bytes do not exist.
        """

        content.validate()

        def stage(cursor: Any) -> RuntimeExecutionContent:
            self._lock_execution(cursor, content.workflow_execution_id)
            cursor.execute(
                f"""
                SELECT 1
                FROM {self.schema}.workflow_execution
                WHERE workflow_execution_id = %s
                """,
                (content.workflow_execution_id,),
            )
            if cursor.fetchone() is None:
                raise ValueError(
                    "staged content requires an existing Workflow Execution"
                )
            self._insert_content(cursor, content)
            return content

        return self._transaction(stage)

    def list_content_metadata(
        self,
        workflow_execution_id: str,
    ) -> tuple[Mapping[str, Any], ...]:
        def load(cursor: Any) -> tuple[Mapping[str, Any], ...]:
            trace = self._load_reference(
                cursor,
                workflow_execution_id,
            ).load_trace(workflow_execution_id)
            declared_refs = _referenced_content_hashes(trace)
            if not declared_refs:
                return ()
            cursor.execute(
                f"""
                SELECT content_ref, content_sha256, media_type, byte_size,
                       recorded_at_utc
                FROM {self.schema}.execution_content
                WHERE workflow_execution_id = %s
                ORDER BY content_ref
                """,
                (workflow_execution_id,),
            )
            return tuple(
                MappingProxyType(
                    {
                        "content_ref": row[0],
                        "content_sha256": row[1],
                        "media_type": row[2],
                        "byte_size": row[3],
                        "recorded_at_utc": _utc_text(row[4]),
                    }
                )
                for row in cursor.fetchall()
                if row[0] in declared_refs
            )

        return self._read_transaction(load)

    def load_content(
        self,
        workflow_execution_id: str,
        content_ref: str,
    ) -> RuntimeExecutionContent | None:
        def load(cursor: Any) -> RuntimeExecutionContent | None:
            trace = self._load_reference(
                cursor,
                workflow_execution_id,
            ).load_trace(workflow_execution_id)
            if content_ref not in _referenced_content_hashes(trace):
                return None
            return self._load_content(
                cursor,
                workflow_execution_id,
                content_ref,
            )

        return self._read_transaction(load)

    def _load_content(
        self,
        cursor: Any,
        workflow_execution_id: str,
        content_ref: str,
    ) -> RuntimeExecutionContent | None:
        cursor.execute(
            f"""
            SELECT content_sha256, media_type, byte_size, body,
                   recorded_at_utc
            FROM {self.schema}.execution_content
            WHERE workflow_execution_id = %s AND content_ref = %s
            """,
            (workflow_execution_id, content_ref),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        body = bytes(row[3])
        if row[2] != len(body):
            raise RuntimeError("persisted execution content byte size mismatch")
        content = RuntimeExecutionContent(
            workflow_execution_id=workflow_execution_id,
            content_ref=content_ref,
            content_sha256=row[0],
            media_type=row[1],
            body=body,
            recorded_at_utc=_utc_text(row[4]),
        )
        try:
            content.validate()
        except ValueError as exc:
            raise RuntimeError("persisted execution content failed integrity check") from exc
        return content

    def _insert_content(
        self,
        cursor: Any,
        content: RuntimeExecutionContent,
    ) -> None:
        """Insert one immutable body or verify a byte-identical replay."""

        cursor.execute(
            f"""
            INSERT INTO {self.schema}.execution_content
                (workflow_execution_id, content_ref, content_sha256,
                 media_type, byte_size, body, recorded_at_utc)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (workflow_execution_id, content_ref) DO NOTHING
            RETURNING content_sha256
            """,
            (
                content.workflow_execution_id,
                content.content_ref,
                content.content_sha256,
                content.media_type,
                len(content.body),
                content.body,
                content.recorded_at_utc,
            ),
        )
        if cursor.fetchone() is not None:
            return
        existing = self._load_content(
            cursor,
            content.workflow_execution_id,
            content.content_ref,
        )
        # A byte-identical retry converges idempotently. recorded_at_utc is a
        # staging timestamp and never participates in immutable content identity.
        if (
            existing is None
            or existing.content_sha256 != content.content_sha256
            or existing.media_type != content.media_type
            or existing.body != content.body
        ):
            raise ValueError("immutable execution content_ref collision")

    def _mutate(
        self,
        workflow_execution_id: str,
        batch: RuntimeRecordBatch | LegacyRuntimeRecordBatch,
        operation: Callable[[InMemoryRuntimeExecutionRecordStore], Any],
    ) -> Any:
        def mutate(cursor: Any) -> Any:
            # The per-execution advisory lock serializes writers across the
            # fleet. A Workflow Execution's control lineage is bounded (see the
            # execution-model invariants in agent_runtime_06), so the reference
            # is reconstructed from that execution's own committed facts on each
            # mutation rather than cached.
            self._lock_execution(cursor, workflow_execution_id)
            reference = self._load_reference(cursor, workflow_execution_id)
            result = operation(reference)
            receipt = (
                result
                if isinstance(result, CommitReceipt)
                else result.commit_receipt
            )
            if not receipt.replayed:
                self._persist_batch(cursor, batch, receipt)
            return result

        return self._transaction(mutate)

    def _lock_execution(self, cursor: Any, workflow_execution_id: str) -> None:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (f"{self.schema}:{workflow_execution_id}",),
        )

    def _load_reference(
        self,
        cursor: Any,
        workflow_execution_id: str,
    ) -> InMemoryRuntimeExecutionRecordStore:
        batches = self._load_committed_batches(cursor, workflow_execution_id)
        integrity_check = self._execution_output_integrity_check
        if integrity_check is None:
            integrity_check = lambda output: self._content_matches(cursor, output)
        return InMemoryRuntimeExecutionRecordStore.from_committed_batches(
            batches,
            execution_output_integrity_check=integrity_check,
        )

    def _load_committed_batches(
        self,
        cursor: Any,
        workflow_execution_id: str,
    ) -> tuple[RuntimeRecordBatch | LegacyRuntimeRecordBatch, ...]:
        cursor.execute(
            f"""
            SELECT transaction_id, transaction_sha256, record_count,
                   committed_outcome_refs, batch_payload_canonical
            FROM {self.schema}.execution_transaction
            WHERE workflow_execution_id = %s
            ORDER BY commit_sequence
            """,
            (workflow_execution_id,),
        )
        transaction_rows = tuple(cursor.fetchall())
        cursor.execute(
            f"""
            SELECT record.transaction_id, record.transaction_record_index,
                   record.record_type, record.record_sha256,
                   record.payload_canonical
            FROM {self.schema}.execution_record AS record
            JOIN {self.schema}.execution_transaction AS transaction
              ON transaction.workflow_execution_id = record.workflow_execution_id
             AND transaction.transaction_id = record.transaction_id
            WHERE record.workflow_execution_id = %s
            ORDER BY transaction.commit_sequence,
                     record.transaction_record_index
            """,
            (workflow_execution_id,),
        )
        records_by_transaction: dict[str, list[tuple[Any, ...]]] = {}
        for row in cursor.fetchall():
            records_by_transaction.setdefault(row[0], []).append(row[1:])

        batches: list[RuntimeRecordBatch | LegacyRuntimeRecordBatch] = []
        for row in transaction_rows:
            transaction_id, transaction_sha256, record_count = row[:3]
            # The canonical BYTEA is the sole integrity authority: it is
            # self-validating on deserialize and the record/transaction sha256
            # anchors it. The jsonb column is a query projection only and is
            # never read back to cross-check a read.
            batch = deserialize_runtime_batch(_canonical_payload(row[4]))
            if (
                batch.workflow_execution_id != workflow_execution_id
                or batch.transaction_id != transaction_id
                or batch.transaction_sha256 != transaction_sha256
                or len(batch.records) != record_count
            ):
                raise RuntimeError("persisted execution transaction failed integrity check")
            committed_outcome_refs = _json_string_tuple(row[3])
            expected_outcome_refs = tuple(
                record.outcome_ref
                for record in batch.records
                if isinstance(record, ModuleOutcome)
            )
            if committed_outcome_refs != expected_outcome_refs:
                raise RuntimeError("persisted execution outcome refs failed integrity check")
            persisted_records = records_by_transaction.pop(transaction_id, [])
            if len(persisted_records) != len(batch.records):
                raise RuntimeError("persisted execution record count mismatch")
            for expected_index, (record, persisted) in enumerate(
                zip(batch.records, persisted_records, strict=True)
            ):
                (
                    index,
                    record_type,
                    record_sha256,
                    record_payload_canonical,
                ) = persisted
                if not _persisted_record_matches(
                    record,
                    expected_index=expected_index,
                    persisted_index=index,
                    persisted_record_type=record_type,
                    persisted_record_sha256=record_sha256,
                    persisted_payload_canonical=record_payload_canonical,
                ):
                    raise RuntimeError("persisted execution record failed integrity check")
            batches.append(batch)
        if records_by_transaction:
            raise RuntimeError("persisted execution records lack a transaction")
        self._verify_execution_projection(cursor, workflow_execution_id, batches)
        return tuple(batches)

    def _verify_execution_projection(
        self,
        cursor: Any,
        workflow_execution_id: str,
        batches: list[RuntimeRecordBatch | LegacyRuntimeRecordBatch],
    ) -> None:
        cursor.execute(
            f"""
            SELECT record_sha256, payload_canonical
            FROM {self.schema}.workflow_execution
            WHERE workflow_execution_id = %s
            """,
            (workflow_execution_id,),
        )
        row = cursor.fetchone()
        execution = next(
            (
                record
                for batch in batches
                for record in batch.records
                if isinstance(record, WorkflowExecutionRecord)
            ),
            None,
        )
        if execution is None:
            if row is not None:
                raise RuntimeError("Workflow Execution projection lacks ledger facts")
            return
        if row is None:
            raise RuntimeError("Workflow Execution projection is missing")
        payload = _canonical_payload(row[1])
        if (
            row[0] != _canonical_sha256(payload)
            or dict(payload) != execution.as_dict()
        ):
            raise RuntimeError("Workflow Execution projection failed integrity check")

    def _content_matches(self, cursor: Any, output: ExecutionOutputRef) -> bool:
        content = self._load_content(cursor, output.workflow_execution_id, output.output_ref)
        return content is not None and content.content_sha256 == output.output_sha256

    def _persist_batch(
        self,
        cursor: Any,
        batch: RuntimeRecordBatch | LegacyRuntimeRecordBatch,
        receipt: CommitReceipt,
    ) -> None:
        execution = next(
            (
                record
                for record in batch.records
                if isinstance(record, WorkflowExecutionRecord)
            ),
            None,
        )
        if execution is not None:
            payload = execution.as_dict()
            record_hash = _canonical_sha256(payload)
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.workflow_execution
                    (workflow_execution_id, workflow_id, tenant_id, cell_id,
                     principal_id, execution_release_ref, recorded_at_utc,
                     record_sha256, payload, payload_canonical)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (workflow_execution_id) DO NOTHING
                RETURNING record_sha256
                """,
                (
                    execution.workflow_execution_id,
                    execution.workflow_id,
                    execution.tenant_id,
                    execution.cell_id,
                    execution.principal_id,
                    execution.execution_release_ref,
                    execution.recorded_at_utc,
                    record_hash,
                    _json(payload),
                    _json_bytes(payload),
                ),
            )
            if cursor.fetchone() is None:
                raise ValueError("immutable Workflow Execution record collision")
        cursor.execute(
            f"""
            INSERT INTO {self.schema}.execution_transaction
                (workflow_execution_id, transaction_id, transaction_sha256,
                 record_count, committed_outcome_refs, batch_payload,
                 batch_payload_canonical)
            VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (workflow_execution_id, transaction_id) DO NOTHING
            RETURNING transaction_sha256
            """,
            (
                receipt.workflow_execution_id,
                receipt.transaction_id,
                receipt.transaction_sha256,
                receipt.record_count,
                _json(list(receipt.committed_outcome_refs)),
                _json(batch.as_dict()),
                _json_bytes(batch.as_dict()),
            ),
        )
        if cursor.fetchone() is None:
            raise RuntimeError("serialized execution transaction lost its advisory lock")
        for index, record in enumerate(batch.records):
            serialized = _persisted_record_as_dict(record)
            payload = serialized["record"]
            cursor.execute(
                f"""
                INSERT INTO {self.schema}.execution_record
                    (workflow_execution_id, transaction_id,
                     transaction_record_index, record_type, record_sha256,
                     payload, payload_canonical)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                """,
                (
                    batch.workflow_execution_id,
                    batch.transaction_id,
                    index,
                    serialized["record_type"],
                    _canonical_sha256(payload),
                    _json(payload),
                    _json_bytes(payload),
                ),
            )

    def _transaction(self, operation: Callable[[Any], Any]) -> Any:
        return _run_in_transaction(
            self._connection_factory, operation, read_only=False
        )

    def _read_transaction(self, operation: Callable[[Any], Any]) -> Any:
        return _run_in_transaction(
            self._connection_factory, operation, read_only=True
        )


class PostgresRuntimeExecutionQueryStore:
    """Read-only PostgreSQL execution and content query boundary."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        schema: str = "agent_runtime_execution",
    ) -> None:
        if not callable(connection_factory):
            raise ValueError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self.schema = _validate_schema(schema)
        self._decoder = PostgresRuntimeExecutionRecordStore(
            connection_factory,
            schema=schema,
        )

    @classmethod
    def from_dsn(
        cls,
        database_url: str,
        *,
        schema: str = "agent_runtime_execution",
        connect_timeout: int = 8,
    ) -> "PostgresRuntimeExecutionQueryStore":
        if type(database_url) is not str or not database_url:
            raise ValueError("database_url is required")
        if type(connect_timeout) is not int or connect_timeout < 1:
            raise ValueError("connect_timeout must be a positive integer")
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - optional dependency
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

    def list_executions(
        self,
        *,
        limit: int = 100,
        before: RuntimeExecutionPageCursor | None = None,
    ) -> tuple[RuntimeExecutionDescriptor, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if before is not None:
            if type(before) is not RuntimeExecutionPageCursor:
                raise ValueError("before must be a RuntimeExecutionPageCursor")
            before.validate()

        def load(cursor: Any) -> tuple[RuntimeExecutionDescriptor, ...]:
            boundary = ""
            parameters: tuple[Any, ...] = (limit,)
            if before is not None:
                boundary = (
                    "WHERE (recorded_at_utc, workflow_execution_id) "
                    "< (%s::timestamptz, %s)"
                )
                parameters = (
                    before.recorded_at_utc,
                    before.workflow_execution_id,
                    limit,
                )
            cursor.execute(
                f"""
                SELECT workflow_execution_id, workflow_id, tenant_id, cell_id,
                       principal_id, execution_release_ref,
                       recorded_at_utc
                FROM {self.schema}.workflow_execution
                {boundary}
                ORDER BY recorded_at_utc DESC, workflow_execution_id DESC
                LIMIT %s
                """,
                parameters,
            )
            return tuple(_execution_descriptor(row) for row in cursor.fetchall())

        return self._transaction(load)

    def get_execution_descriptor(
        self,
        workflow_execution_id: str,
    ) -> RuntimeExecutionDescriptor | None:
        def load(cursor: Any) -> RuntimeExecutionDescriptor | None:
            cursor.execute(
                f"""
                SELECT workflow_execution_id, workflow_id, tenant_id, cell_id,
                       principal_id, execution_release_ref,
                       recorded_at_utc
                FROM {self.schema}.workflow_execution
                WHERE workflow_execution_id = %s
                """,
                (workflow_execution_id,),
            )
            row = cursor.fetchone()
            return None if row is None else _execution_descriptor(row)

        return self._transaction(load)

    def load_trace(self, workflow_execution_id: str) -> RuntimeExecutionTrace:
        return self._transaction(
            lambda cursor: self._decoder._load_reference(
                cursor,
                workflow_execution_id,
            ).load_trace(workflow_execution_id)
        )

    def list_content_metadata(
        self,
        workflow_execution_id: str,
    ) -> tuple[Mapping[str, Any], ...]:
        def load(cursor: Any) -> tuple[Mapping[str, Any], ...]:
            trace = self._decoder._load_reference(
                cursor,
                workflow_execution_id,
            ).load_trace(workflow_execution_id)
            declared_refs = _referenced_content_hashes(trace)
            if not declared_refs:
                return ()
            cursor.execute(
                f"""
                SELECT content_ref, content_sha256, media_type, byte_size,
                       recorded_at_utc
                FROM {self.schema}.execution_content
                WHERE workflow_execution_id = %s
                ORDER BY content_ref
                """,
                (workflow_execution_id,),
            )
            return tuple(
                MappingProxyType(
                    {
                        "content_ref": row[0],
                        "content_sha256": row[1],
                        "media_type": row[2],
                        "byte_size": row[3],
                        "recorded_at_utc": _utc_text(row[4]),
                    }
                )
                for row in cursor.fetchall()
                if row[0] in declared_refs
            )

        return self._transaction(load)

    def load_content(
        self,
        workflow_execution_id: str,
        content_ref: str,
    ) -> RuntimeExecutionContent | None:
        def load(cursor: Any) -> RuntimeExecutionContent | None:
            trace = self._decoder._load_reference(
                cursor,
                workflow_execution_id,
            ).load_trace(workflow_execution_id)
            if content_ref not in _referenced_content_hashes(trace):
                return None
            return self._decoder._load_content(
                cursor,
                workflow_execution_id,
                content_ref,
            )

        return self._transaction(load)

    def _transaction(self, operation: Callable[[Any], Any]) -> Any:
        return _run_in_transaction(
            self._connection_factory, operation, read_only=True
        )


def _run_in_transaction(
    connection_factory: Callable[[], Any],
    operation: Callable[[Any], Any],
    *,
    read_only: bool,
) -> Any:
    """Run one operation in a fresh transaction with enforced UTF-8 decoding.

    Read paths use one REPEATABLE READ snapshot so a multi-statement read cannot
    tear under a concurrent committer. Every transaction forces client_encoding
    to UTF8 so TEXT columns decode as str regardless of the caller-supplied
    connection factory; the ledger stores UTF-8 text and BYTEA and must not
    depend on the connection's default encoding for correctness.
    """

    connection = connection_factory()
    try:
        cursor = connection.cursor()
        try:
            if read_only:
                cursor.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
            cursor.execute("SET client_encoding TO 'UTF8'")
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


def serialize_runtime_batch(
    batch: RuntimeRecordBatch | LegacyRuntimeRecordBatch,
) -> Mapping[str, Any]:
    batch.validate()
    return MappingProxyType(batch.as_dict())


def deserialize_runtime_batch(
    payload: Mapping[str, Any],
) -> RuntimeRecordBatch | LegacyRuntimeRecordBatch:
    workflow_execution_id = payload.get("workflow_execution_id")
    transaction_id = payload.get("transaction_id")
    records_payload = payload.get("records")
    if not isinstance(records_payload, list):
        raise ValueError("persisted Runtime batch records must be a list")
    records = tuple(deserialize_runtime_record(item) for item in records_payload)
    batch_type = (
        LegacyRuntimeRecordBatch
        if payload.get("legacy_compatibility") is True
        else RuntimeRecordBatch
    )
    batch = batch_type(
        workflow_execution_id=workflow_execution_id,
        transaction_id=transaction_id,
        records=records,
    )
    batch.validate()
    return batch


def deserialize_runtime_record(payload: Mapping[str, Any]) -> PersistedRuntimeRecord:
    if not isinstance(payload, Mapping):
        raise ValueError("persisted Runtime record must be a mapping")
    record_type_name = payload.get("record_type")
    record_payload = payload.get("record")
    record_type = _RECORD_TYPES.get(record_type_name)
    if record_type is None or not isinstance(record_payload, Mapping):
        raise ValueError("unsupported persisted Runtime record")
    hints = get_type_hints(record_type)
    kwargs = {
        field.name: _decode_value(hints[field.name], record_payload[field.name])
        for field in fields(record_type)
        if field.name in record_payload
    }
    record = record_type(**kwargs)
    record.validate()
    return record


def _decode_value(annotation: Any, value: Any) -> Any:
    origin = get_origin(annotation)
    if origin in (Union, UnionType):
        if value is None and type(None) in get_args(annotation):
            return None
        for member in get_args(annotation):
            if member is type(None):
                continue
            try:
                return _decode_value(member, value)
            except (TypeError, ValueError):
                continue
        raise ValueError("persisted value does not match its union type")
    if origin is tuple:
        members = get_args(annotation)
        if not isinstance(value, (list, tuple)):
            raise ValueError("persisted tuple must be an array")
        if len(members) == 2 and members[1] is Ellipsis:
            return tuple(_decode_value(members[0], item) for item in value)
        if len(members) != len(value):
            raise ValueError("persisted fixed tuple length mismatch")
        return tuple(_decode_value(member, item) for member, item in zip(members, value))
    if origin is list:
        if not isinstance(value, list):
            raise ValueError("persisted list must be an array")
        return [_decode_value(get_args(annotation)[0], item) for item in value]
    if origin in (dict, Mapping):
        if not isinstance(value, Mapping):
            raise ValueError("persisted mapping must be an object")
        key_type, value_type = get_args(annotation)
        return {
            _decode_value(key_type, key): _decode_value(value_type, item)
            for key, item in value.items()
        }
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return annotation(value)
    if isinstance(annotation, type) and is_dataclass(annotation):
        if not isinstance(value, Mapping):
            raise ValueError("persisted dataclass must be an object")
        hints = get_type_hints(annotation)
        return annotation(
            **{
                field.name: _decode_value(hints[field.name], value[field.name])
                for field in fields(annotation)
                if field.name in value
            }
        )
    if annotation is Any:
        return value
    if annotation is float and type(value) in (int, float):
        return float(value)
    if annotation in (str, int, float, bool):
        if type(value) is not annotation:
            raise ValueError("persisted scalar type mismatch")
        return value
    return value


def _persisted_record_as_dict(record: PersistedRuntimeRecord) -> dict[str, Any]:
    if isinstance(record, RuntimeLedgerRecord):
        return runtime_record_as_dict(record)
    return {
        "record_type": type(record).__name__,
        "record": record.as_dict(),
    }


def _persisted_record_matches(
    record: PersistedRuntimeRecord,
    *,
    expected_index: int,
    persisted_index: int,
    persisted_record_type: str,
    persisted_record_sha256: str,
    persisted_payload_canonical: Any,
) -> bool:
    """Compare storage bytes without confusing JSON arrays with Python tuples."""

    expected = _persisted_record_as_dict(record)
    decoded_payload = _canonical_payload(persisted_payload_canonical)
    return (
        persisted_index == expected_index
        and persisted_record_type == expected["record_type"]
        and persisted_record_sha256 == _canonical_sha256(decoded_payload)
        and bytes(persisted_payload_canonical)
        == _json_bytes(expected["record"])
    )


def _referenced_content_hashes(
    trace: RuntimeExecutionTrace,
) -> dict[str, str | None]:
    references: dict[str, str | None] = {}
    for record in trace.records:
        payload = _persisted_record_as_dict(record)["record"]
        for key, value in payload.items():
            if not isinstance(value, str):
                continue
            hash_key = None
            if key.endswith("_ref"):
                hash_key = f"{key[:-4]}_sha256"
            elif key == "input_ref":
                hash_key = "input_sha256"
            elif key == "output_ref":
                hash_key = "output_sha256"
            if hash_key is not None:
                expected_hash = payload.get(hash_key)
                references[value] = (
                    expected_hash
                    if isinstance(expected_hash, str)
                    and re.fullmatch(r"[0-9a-f]{64}", expected_hash)
                    else None
                )
    return references


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    return sha256_text(_json(payload))


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _utc_text(value: Any) -> str:
    timestamp = value if isinstance(value, datetime) else _timestamp(str(value))
    if timestamp.tzinfo is None:
        raise RuntimeError("persisted timestamp lacks a timezone")
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _execution_descriptor(row: tuple[Any, ...]) -> RuntimeExecutionDescriptor:
    return RuntimeExecutionDescriptor(
        workflow_execution_id=row[0],
        workflow_id=row[1],
        tenant_id=row[2],
        cell_id=row[3],
        principal_id=row[4],
        execution_release_ref=row[5],
        recorded_at_utc=_utc_text(row[6]),
    )


def _json_string_tuple(value: Any) -> tuple[str, ...]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, list) or any(type(item) is not str for item in decoded):
        raise RuntimeError("persisted execution outcome refs must be a JSON array")
    return tuple(decoded)


def _json(payload: Any) -> str:
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_bytes(payload: Any) -> bytes:
    return _json(payload).encode("utf-8")


def _canonical_payload(value: Any) -> Mapping[str, Any]:
    try:
        encoded = bytes(value)
        decoded = json.loads(encoded.decode("utf-8"))
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("persisted canonical JSON bytes are invalid") from exc
    if not isinstance(decoded, Mapping) or _json_bytes(decoded) != encoded:
        raise RuntimeError("persisted canonical JSON bytes are not canonical")
    return decoded


__all__ = [
    "PostgresRuntimeExecutionQueryStore",
    "PostgresRuntimeExecutionRecordStore",
    "RuntimeExecutionContent",
    "RuntimeExecutionDescriptor",
    "RuntimeExecutionPageCursor",
    "deserialize_runtime_batch",
    "deserialize_runtime_record",
    "postgres_execution_ledger_ddl",
    "serialize_runtime_batch",
]
