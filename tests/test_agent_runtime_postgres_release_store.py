from __future__ import annotations

import json
import os
from threading import Event, Thread
from typing import Any
import uuid

import pytest

from agent_runtime.contracts.registry_release_definition import (
    LegacyReleaseAdmissionRecord,
    LegacyReleaseAdmissionState,
    ModuleEntryPolicy,
    ReleaseSubjectKind,
)
from agent_runtime.registry import (
    AgentModuleReleaseCandidate,
    RegistryAdmissionIdentityDisposition,
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    PostgresRuntimeReleaseQueryStore,
    PostgresRuntimeReleaseStore,
    RegistryReleaseIdentityDisposition,
    RegistrySchemaMigrationPlan,
    RetryPolicyReleaseCandidate,
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_retry_policy_release,
    postgres_release_ddl,
    runtime_owned_policy_schema_assets,
    serialize_registry_tables,
)
from agent_runtime.registry.registry_postgres_persistence import (
    _canonical_sha256,
    _install_migration_target,
    _normalize_postgres_rows,
    _prepare_schema_migration,
    _registry_schema_release_sha256,
    _schema_structure_sha256,
    _source_row_identity_sha256,
)


class _RecordingCursor:
    def __init__(self, *, fail: bool = False, ready_schema: bool = False) -> None:
        self.fail = fail
        self.ready_schema = ready_schema
        self.statements: list[str] = []
        self.closed = False
        self._next_one: Any = None
        self._next_all: tuple[Any, ...] = ()

    def execute(self, statement: str, parameters: Any = None) -> None:
        if self.fail:
            raise RuntimeError("database unavailable")
        self.statements.append(statement)
        normalized = " ".join(statement.split())
        self._next_one = None
        self._next_all = ()
        if self.ready_schema and "to_regclass" in normalized:
            self._next_one = (
                "agent_runtime_control_v2.registry_schema_installation",
            )
        elif (
            self.ready_schema
            and normalized.startswith("SELECT schema_release_ref")
        ):
            empty_structure = _canonical_sha256(
                {"columns": [], "constraints": [], "indexes": []}
            )
            self._next_one = (
                "registry-schema:agent_runtime_control@v2",
                _registry_schema_release_sha256(),
                empty_structure,
                "ready",
                "2026-08-17T20:00:00Z",
            )

    def close(self) -> None:
        self.closed = True

    def fetchone(self) -> Any:
        result = self._next_one
        self._next_one = None
        return result

    def fetchall(self) -> tuple[Any, ...]:
        result = self._next_all
        self._next_all = ()
        return result


class _ChildRowCursor:
    def __init__(self) -> None:
        self.statement = ""
        self.parameters: tuple[Any, ...] = ()

    def execute(self, statement: str, parameters: tuple[Any, ...]) -> None:
        self.statement = statement
        self.parameters = parameters

    def fetchone(self) -> tuple[str]:
        return ("a" * 64,)


class _CatalogStructureCursor:
    def __init__(self, *, byte_text: bool) -> None:
        text = (lambda value: value.encode("utf-8")) if byte_text else (lambda value: value)
        self._rows = iter(
            (
                ((text("release_table"), 1, text("release_ref"), text("text"), True, text("")),),
                ((text("release_table"), text("release_pk"), text("p"), text("PRIMARY KEY (release_ref)")),),
                ((text("release_table"), text("release_pk"), text("CREATE UNIQUE INDEX release_pk")),),
            )
        )

    def execute(self, statement: str, parameters: Any = None) -> None:
        del statement, parameters

    def fetchall(self) -> tuple[Any, ...]:
        return next(self._rows)


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


@pytest.fixture
def postgres_release_test_schema() -> str:
    if not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"):
        pytest.skip("requires AGENT_RUNTIME_TEST_DATABASE_URL")
    import psycopg

    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    schema = f"agent_runtime_registry_test_{uuid.uuid4().hex[:18]}"
    try:
        yield schema
    finally:
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def _empty_v1_migration_plan(
    database_url: str,
    *,
    source_schema: str,
    target_schema: str,
) -> tuple[RegistrySchemaMigrationPlan, Any]:
    import psycopg

    excluded_v2_tables = {
        "behavior_policy_release",
        "evaluation_policy_release",
        "retry_policy_release",
        "execution_variant_policy_release",
        "registry_release_identity_migration",
        "registry_schema_installation",
    }
    predecessor_ddl = tuple(
        statement
        for statement in postgres_release_ddl(source_schema)
        if not any(
            f"{source_schema}.{table}" in statement
            for table in excluded_v2_tables
        )
    )
    predecessor_ddl = (
        *predecessor_ddl,
        f"""
        CREATE TABLE {source_schema}.release_admission (
            admission_sequence BIGSERIAL PRIMARY KEY,
            admission_id TEXT NOT NULL UNIQUE,
            subject_kind TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            release_ref TEXT NOT NULL,
            release_sha256 CHAR(64) NOT NULL,
            state TEXT NOT NULL,
            recorded_at_utc TIMESTAMPTZ NOT NULL,
            admission_sha256 CHAR(64) NOT NULL,
            payload JSONB NOT NULL
        )
        """,
    )
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            for statement in predecessor_ddl:
                cursor.execute(statement)
            unchanged_schema = runtime_owned_policy_schema_assets()[0]
            cursor.execute(
                f"""
                INSERT INTO {source_schema}.schema_asset_release
                    (subject_id, release_version, release_ref,
                     release_sha256, payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                """,
                (
                    unchanged_schema.schema_asset_id,
                    unchanged_schema.schema_asset_version,
                    unchanged_schema.release_ref,
                    unchanged_schema.release_sha256,
                    json.dumps(unchanged_schema.as_dict()),
                ),
            )
            candidate_admission = LegacyReleaseAdmissionRecord.build(
                admission_id="admission_runtime_behavior_policy_candidate",
                subject_kind=ReleaseSubjectKind.SCHEMA_ASSET,
                subject_id=unchanged_schema.schema_asset_id,
                release_ref=unchanged_schema.release_ref,
                release_sha256=unchanged_schema.release_sha256,
                state=LegacyReleaseAdmissionState.CANDIDATE,
                evidence_members=(),
                recorded_at_utc="2026-08-17T19:59:59Z",
            )
            active_admission = LegacyReleaseAdmissionRecord.build(
                admission_id="admission_runtime_behavior_policy_active",
                subject_kind=ReleaseSubjectKind.SCHEMA_ASSET,
                subject_id=unchanged_schema.schema_asset_id,
                release_ref=unchanged_schema.release_ref,
                release_sha256=unchanged_schema.release_sha256,
                state=LegacyReleaseAdmissionState.ACTIVE,
                evidence_members=(),
                recorded_at_utc="2026-08-17T20:00:00Z",
            )
            for admission in (candidate_admission, active_admission):
                cursor.execute(
                    f"""
                    INSERT INTO {source_schema}.release_admission
                        (admission_id, subject_kind, subject_id, release_ref,
                         release_sha256, state, recorded_at_utc,
                         admission_sha256, payload)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    """,
                    (
                        admission.admission_id,
                        admission.subject_kind.value,
                        admission.subject_id,
                        admission.release_ref,
                        admission.release_sha256,
                        admission.state.value,
                        admission.recorded_at_utc,
                        admission.admission_sha256,
                        json.dumps(admission.as_dict()),
                    ),
                )
            source_structure_sha256 = _schema_structure_sha256(
                cursor,
                source_schema,
            )
            source_row_identity_sha256 = _source_row_identity_sha256(
                cursor,
                source_schema,
            )
    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    plan = RegistrySchemaMigrationPlan.build(
        migration_id=f"registry_v1_to_v2_{uuid.uuid4().hex[:16]}",
        source_schema=source_schema,
        target_schema=target_schema,
        source_structure_sha256=source_structure_sha256,
        source_row_identity_sha256=source_row_identity_sha256,
        target_schema_release_ref="registry-schema:agent_runtime_control@v2",
        target_schema_release_sha256=_registry_schema_release_sha256(),
        target_bundle=RuntimeReleaseBundle(
            schema_assets=runtime_owned_policy_schema_assets(),
            behavior_policies=(behavior,),
        ),
        release_dispositions=(
            RegistryReleaseIdentityDisposition(
                source_table="schema_asset_release",
                source_release_ref=unchanged_schema.release_ref,
                source_release_sha256=unchanged_schema.release_sha256,
                disposition="unchanged",
                target_release_ref=unchanged_schema.release_ref,
                target_release_sha256=unchanged_schema.release_sha256,
            ),
        ),
        admission_dispositions=(
            RegistryAdmissionIdentityDisposition(
                source_admission_id=candidate_admission.admission_id,
                source_admission_sha256=candidate_admission.admission_sha256,
                disposition="removed",
                target_admission_id=None,
                target_admission_sha256=None,
            ),
            RegistryAdmissionIdentityDisposition(
                source_admission_id=active_admission.admission_id,
                source_admission_sha256=active_admission.admission_sha256,
                disposition="removed",
                target_admission_id=None,
                target_admission_sha256=None,
            ),
        ),
        active_pointer_dispositions=(),
        planned_at_utc="2026-08-17T20:00:00Z",
    )
    return plan, behavior


def _clean_migration_test_state(
    database_url: str,
    *,
    source_schema: str,
    target_schema: str,
) -> None:
    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(f"DROP SCHEMA IF EXISTS {source_schema} CASCADE")
            cursor.execute(f"DROP SCHEMA IF EXISTS {target_schema} CASCADE")
            cursor.execute(
                "SELECT pg_catalog.to_regclass(%s)",
                (
                    "agent_runtime_registry_migration."
                    "registry_schema_migration_control",
                ),
            )
            if cursor.fetchone()[0] is not None:
                cursor.execute(
                    """
                    DELETE FROM agent_runtime_registry_migration.
                        registry_schema_migration_control
                    WHERE source_schema = %s AND target_schema = %s
                    """,
                    (source_schema, target_schema),
                )


def test_schema_structure_hash_normalizes_postgres_text_bytes() -> None:
    assert _schema_structure_sha256(
        _CatalogStructureCursor(byte_text=True),
        "agent_runtime_control_v2",
    ) == _schema_structure_sha256(
        _CatalogStructureCursor(byte_text=False),
        "agent_runtime_control_v2",
    )


def test_postgres_row_normalization_decodes_bytes_and_memoryview() -> None:
    assert _normalize_postgres_rows(
        ((b"release_ref", memoryview(b"release_sha256"), 1, True),)
    ) == [["release_ref", "release_sha256", 1, True]]


def test_postgres_store_creates_registered_schema_transactionally() -> None:
    cursor = _RecordingCursor()
    connection = _RecordingConnection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    installation = store.create_schema(
        installed_at_utc="2026-08-17T20:00:00Z"
    )

    assert tuple(cursor.statements[: len(postgres_release_ddl())]) == (
        postgres_release_ddl()
    )
    assert installation.state == "ready"
    assert "registry_schema_installation" in cursor.statements[-1]
    assert connection.committed is True
    assert connection.rolled_back is False
    assert cursor.closed is True
    assert connection.closed is True


def test_postgres_store_rolls_back_schema_failure() -> None:
    cursor = _RecordingCursor(fail=True)
    connection = _RecordingConnection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    with pytest.raises(RuntimeError, match="database unavailable"):
        store.create_schema(installed_at_utc="2026-08-17T20:00:00Z")

    assert connection.committed is False
    assert connection.rolled_back is True
    assert cursor.closed is True
    assert connection.closed is True


def test_postgres_projection_has_all_normalized_registry_tables() -> None:
    rows = serialize_registry_tables(RuntimeReleaseRegistry().snapshot())

    assert set(rows) == {
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
        "workflow_node_binding",
        "workflow_edge",
        "workflow_parallel_group_binding",
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
    cursor = _RecordingCursor(ready_schema=True)
    connection = _RecordingConnection(cursor)
    queries = PostgresRuntimeReleaseQueryStore(lambda: connection)

    assert queries.load_workflow_release("workflow-release:missing@v1") is None
    assert cursor.statements[0] == "SET TRANSACTION READ ONLY"
    assert "FROM agent_runtime_control_v2.workflow_release" in cursor.statements[-1]
    assert not any(
        statement.lstrip().startswith(("CREATE", "ALTER", "DROP"))
        for statement in cursor.statements
    )
    assert connection.committed is True


def test_postgres_store_reports_unknown_schema_without_creating_it() -> None:
    cursor = _RecordingCursor()
    connection = _RecordingConnection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    installation = store.installed_schema_release()

    assert installation.state == "unknown"
    assert cursor.statements[0] == "SET TRANSACTION READ ONLY"
    assert not any("CREATE" in statement for statement in cursor.statements)


def test_postgres_store_refuses_ordinary_load_without_ready_schema() -> None:
    cursor = _RecordingCursor()
    connection = _RecordingConnection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    with pytest.raises(RuntimeError, match="absent, installing"):
        store.load_release_registry()

    assert not any("CREATE" in statement for statement in cursor.statements)
    assert connection.rolled_back is True


@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_v2_persists_reloads_and_replays_policy_releases(
    postgres_release_test_schema: str,
) -> None:
    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    evaluation = compile_evaluation_policy_release(
        EvaluationPolicyReleaseCandidate(
            policy_id="module_candidate",
            policy_version="v1",
            evaluation_mode="module_candidate",
        )
    )
    retry = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="bounded_candidate",
            policy_version="v1",
            max_attempts=3,
        )
    )
    bundle = RuntimeReleaseBundle(
        schema_assets=runtime_owned_policy_schema_assets(),
        behavior_policies=(behavior,),
        evaluation_policies=(evaluation,),
        retry_policies=(retry,),
    )
    store = PostgresRuntimeReleaseStore.from_dsn(
        database_url,
        schema=postgres_release_test_schema,
    )

    created = store.create_schema(installed_at_utc="2026-08-17T19:59:59Z")
    assert created.state == "ready"
    first_result = store.register_bundle(bundle)
    first = first_result.catalog_snapshot
    assert first_result.submitted_bundle == bundle
    reopened = PostgresRuntimeReleaseStore.from_dsn(
        database_url,
        schema=postgres_release_test_schema,
    )
    assert reopened.installed_schema_release() == created
    second = reopened.load_release_registry().snapshot()
    replayed_result = reopened.register_bundle(bundle)
    replayed = replayed_result.catalog_snapshot
    assert replayed_result.submitted_bundle == bundle

    assert second == first
    assert replayed == first
    assert second.behavior_policies == (behavior,)
    assert second.evaluation_policies == (evaluation,)
    assert second.retry_policies == (retry,)


@pytest.mark.parametrize(
    ("mutation", "expected_error"),
    (
        ("identity_column", "identity columns differ from payload"),
        ("payload_hash", "BehaviorPolicyRelease release hash mismatch"),
    ),
)
@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_reload_rejects_column_or_payload_identity_drift(
    postgres_release_test_schema: str,
    mutation: str,
    expected_error: str,
) -> None:
    import psycopg

    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    store = PostgresRuntimeReleaseStore.from_dsn(
        database_url,
        schema=postgres_release_test_schema,
    )
    store.create_schema(installed_at_utc="2026-08-17T19:59:59Z")
    store.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=runtime_owned_policy_schema_assets(),
            behavior_policies=(behavior,),
        )
    )
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            if mutation == "identity_column":
                cursor.execute(
                    f"""
                    UPDATE {postgres_release_test_schema}.behavior_policy_release
                    SET subject_id = 'tampered_subject'
                    WHERE release_ref = %s
                    """,
                    (behavior.release_ref,),
                )
            else:
                cursor.execute(
                    f"""
                    UPDATE {postgres_release_test_schema}.behavior_policy_release
                    SET payload = jsonb_set(
                        payload,
                        '{{release_sha256}}',
                        to_jsonb(%s::text)
                    )
                    WHERE release_ref = %s
                    """,
                    ("0" * 64, behavior.release_ref),
                )

    with pytest.raises((RuntimeError, ValueError), match=expected_error):
        store.load_release_registry()


@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_active_pointer_set_clear_and_reload(
    postgres_release_test_schema: str,
) -> None:
    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    evaluation = compile_evaluation_policy_release(
        EvaluationPolicyReleaseCandidate(
            policy_id="module_candidate",
            policy_version="v1",
            evaluation_mode="module_candidate",
        )
    )
    retry = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="bounded_candidate",
            policy_version="v1",
            max_attempts=3,
        )
    )
    schema = lambda ref: json.dumps(
        {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": ref,
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        }
    )
    compiled = compile_agent_module_release(
        AgentModuleReleaseCandidate(
            module_id="postgres_pointer_module",
            module_version="v1",
            owner_contract_ref="owner-contract:test@v1",
            owner_contract_content="# Owner\n",
            input_schema_ref="schema:postgres_pointer_module_input@v1",
            input_schema_document=schema(
                "schema:postgres_pointer_module_input@v1"
            ),
            output_schema_ref="schema:postgres_pointer_module_output@v1",
            output_schema_document=schema(
                "schema:postgres_pointer_module_output@v1"
            ),
            instruction_source_ref="skill-instruction:test:pointer",
            instruction_text="Return one result.\n",
            declared_operation_ids=("model_execute",),
            compatible_transport_kinds=("claude_agent_sdk",),
            behavior_policy_ref=behavior.release_ref,
            behavior_policy_sha256=behavior.release_sha256,
            evaluation_policy_ref=evaluation.release_ref,
            evaluation_policy_sha256=evaluation.release_sha256,
            retry_policy_ref=retry.release_ref,
            retry_policy_sha256=retry.release_sha256,
            entry_policy=ModuleEntryPolicy.STANDALONE_ALLOWED,
        )
    )
    bundle = RuntimeReleaseBundle(
        schema_assets=(
            *runtime_owned_policy_schema_assets(),
            *compiled.schema_assets,
        ),
        prompt_components=compiled.prompt_components,
        prompt_bundles=(compiled.prompt_bundle,),
        behavior_policies=(behavior,),
        evaluation_policies=(evaluation,),
        retry_policies=(retry,),
        modules=(compiled.module,),
    )
    store = PostgresRuntimeReleaseStore.from_dsn(
        database_url,
        schema=postgres_release_test_schema,
    )
    store.create_schema(installed_at_utc="2026-08-17T19:59:59Z")
    store.register_bundle(bundle)

    import psycopg

    started = Event()
    completed = Event()
    activation_results: list[Any] = []
    activation_errors: list[BaseException] = []

    def activate() -> None:
        started.set()
        try:
            activation_results.append(
                store.set_active_release(
                    ReleaseSubjectKind.RUNTIME_MODULE,
                    compiled.module.module_id,
                    compiled.module.release_ref,
                    compiled.module.release_sha256,
                )
            )
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            activation_errors.append(exc)
        finally:
            completed.set()

    lock_key = f"{postgres_release_test_schema}:runtime_release_catalog_mutation"
    with psycopg.connect(database_url) as blocker:
        with blocker.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(hashtext(%s))",
                (lock_key,),
            )
            thread = Thread(target=activate)
            thread.start()
            assert started.wait(timeout=1)
            assert not completed.wait(timeout=0.2)
            cursor.execute(
                "SELECT pg_advisory_unlock(hashtext(%s))",
                (lock_key,),
            )
    thread.join(timeout=2)
    assert completed.is_set()
    assert activation_errors == []
    activated = activation_results[0]
    assert activated.active_release_ref == compiled.module.release_ref
    assert store.load_release_registry().resolve_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        compiled.module.module_id,
    ) == compiled.module

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT xmin::text
                FROM {postgres_release_test_schema}.active_release_pointer
                WHERE subject_kind = 'runtime_module' AND subject_id = %s
                """,
                (compiled.module.module_id,),
            )
            first_xmin = cursor.fetchone()[0]
    replayed = store.set_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        compiled.module.module_id,
        compiled.module.release_ref,
        compiled.module.release_sha256,
    )
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT xmin::text
                FROM {postgres_release_test_schema}.active_release_pointer
                WHERE subject_kind = 'runtime_module' AND subject_id = %s
                """,
                (compiled.module.module_id,),
            )
            replay_xmin = cursor.fetchone()[0]
    assert replayed == activated
    assert replay_xmin == first_xmin

    registration_started = Event()
    registration_completed = Event()
    registration_errors: list[BaseException] = []

    def replay_registration() -> None:
        registration_started.set()
        try:
            store.register_bundle(bundle)
        except BaseException as exc:  # pragma: no cover - diagnostic capture
            registration_errors.append(exc)
        finally:
            registration_completed.set()

    with psycopg.connect(database_url) as blocker:
        with blocker.cursor() as cursor:
            cursor.execute(
                "SELECT pg_advisory_lock(hashtext(%s))",
                (lock_key,),
            )
            registration_thread = Thread(target=replay_registration)
            registration_thread.start()
            assert registration_started.wait(timeout=1)
            assert not registration_completed.wait(timeout=0.2)
            cursor.execute(
                "SELECT pg_advisory_unlock(hashtext(%s))",
                (lock_key,),
            )
    registration_thread.join(timeout=2)
    assert registration_completed.is_set()
    assert registration_errors == []
    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT release_ref, release_sha256, xmin::text
                FROM {postgres_release_test_schema}.active_release_pointer
                WHERE subject_kind = 'runtime_module' AND subject_id = %s
                """,
                (compiled.module.module_id,),
            )
            pointer_after_registration = cursor.fetchone()
    assert pointer_after_registration == (
        compiled.module.release_ref,
        compiled.module.release_sha256,
        first_xmin,
    )

    cleared = store.clear_active_release(
        ReleaseSubjectKind.RUNTIME_MODULE,
        compiled.module.module_id,
        expected_release_ref=compiled.module.release_ref,
        expected_release_sha256=compiled.module.release_sha256,
    )
    assert cleared.active_release_ref is None
    with pytest.raises(KeyError, match="no active runtime_module release"):
        store.load_release_registry().resolve_active_release(
            ReleaseSubjectKind.RUNTIME_MODULE,
            compiled.module.module_id,
        )


@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_side_by_side_migration_fences_v1_and_publishes_v2(
    postgres_release_test_schema: str,
) -> None:
    import psycopg

    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    source_schema = f"agent_runtime_v1_test_{uuid.uuid4().hex[:18]}"
    target_schema = postgres_release_test_schema
    try:
        plan, behavior = _empty_v1_migration_plan(
            database_url,
            source_schema=source_schema,
            target_schema=target_schema,
        )
        store = PostgresRuntimeReleaseStore.from_dsn(
            database_url,
            schema=target_schema,
        )

        installation = store.migrate_schema(plan)

        assert installation.state == "ready"
        assert store.installed_schema_release() == installation
        assert store.load_release_registry().snapshot().behavior_policies == (
            behavior,
        )
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                with pytest.raises(
                    psycopg.errors.RaiseException,
                    match="writes are fenced",
                ):
                    cursor.execute(
                        f"""
                        INSERT INTO {source_schema}.schema_asset_release
                            (subject_id, release_version, release_ref,
                             release_sha256, payload)
                        VALUES ('blocked', 'v1', 'schema:blocked@v1',
                                %s, '{{}}'::jsonb)
                        """,
                        ("a" * 64,),
                    )
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                with pytest.raises(
                    psycopg.errors.RaiseException,
                    match="writes are fenced",
                ):
                    cursor.execute(
                        f"""
                        INSERT INTO {source_schema}.release_admission
                            (admission_id, subject_kind, subject_id,
                             release_ref, release_sha256, state,
                             recorded_at_utc, admission_sha256, payload)
                        VALUES ('blocked_legacy_admission', 'schema_asset',
                                'blocked', 'schema:blocked@v1', %s,
                                'candidate', NOW(), %s, '{{}}'::jsonb)
                        """,
                        ("a" * 64, "b" * 64),
                    )
        with pytest.raises(RuntimeError, match="target schema to be removed"):
            store.abort_schema_migration(plan)
    finally:
        _clean_migration_test_state(
            database_url,
            source_schema=source_schema,
            target_schema=target_schema,
        )


@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_interrupted_installation_refuses_load_and_resumes(
    postgres_release_test_schema: str,
) -> None:
    import psycopg

    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    source_schema = f"agent_runtime_v1_test_{uuid.uuid4().hex[:18]}"
    target_schema = postgres_release_test_schema
    try:
        plan, behavior = _empty_v1_migration_plan(
            database_url,
            source_schema=source_schema,
            target_schema=target_schema,
        )
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                _prepare_schema_migration(cursor, plan)
                connection.commit()
                _install_migration_target(cursor, plan)
                connection.commit()
        store = PostgresRuntimeReleaseStore.from_dsn(
            database_url,
            schema=target_schema,
        )

        assert store.installed_schema_release().state == "installing"
        with pytest.raises(RuntimeError, match="absent, installing"):
            store.load_release_registry()

        resumed = store.resume_schema_migration(plan)

        assert resumed.state == "ready"
        assert store.load_release_registry().snapshot().behavior_policies == (
            behavior,
        )
    finally:
        _clean_migration_test_state(
            database_url,
            source_schema=source_schema,
            target_schema=target_schema,
        )


@pytest.mark.skipif(
    not os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL"),
    reason="requires AGENT_RUNTIME_TEST_DATABASE_URL",
)
def test_postgres_abort_reopens_v1_only_after_target_is_absent(
    postgres_release_test_schema: str,
) -> None:
    import psycopg

    database_url = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    source_schema = f"agent_runtime_v1_test_{uuid.uuid4().hex[:18]}"
    target_schema = postgres_release_test_schema
    try:
        plan, _ = _empty_v1_migration_plan(
            database_url,
            source_schema=source_schema,
            target_schema=target_schema,
        )
        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                _prepare_schema_migration(cursor, plan)
        store = PostgresRuntimeReleaseStore.from_dsn(
            database_url,
            schema=target_schema,
        )

        store.abort_schema_migration(plan)

        with psycopg.connect(database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    INSERT INTO {source_schema}.schema_asset_release
                        (subject_id, release_version, release_ref,
                         release_sha256, payload)
                    VALUES ('allowed', 'v1', 'schema:allowed@v1',
                            %s, '{{}}'::jsonb)
                    """,
                    ("a" * 64,),
                )
    finally:
        _clean_migration_test_state(
            database_url,
            source_schema=source_schema,
            target_schema=target_schema,
        )
