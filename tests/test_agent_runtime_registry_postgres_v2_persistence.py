from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
import os
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from agent_runtime.contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ExecutionProfileRelease,
    ExecutionVariantPolicyRelease,
    ModuleEntryPolicy,
    ModuleKind,
    OutputResolutionPolicy,
    PromptComponentKind,
    PromptComponentRelease,
    PromptBundleRelease,
    ReleaseMember,
    RetryPolicyRelease,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    WorkflowEdge,
    WorkflowNodeBinding,
    WorkflowNodeKind,
    WorkflowParallelGroupBinding,
    WorkflowParallelJoinPolicy,
    WorkflowRelease,
)
from agent_runtime.registry.registry_postgres_persistence import (
    REGISTRY_SCHEMA_FINGERPRINT_SHA256,
    REGISTRY_SCHEMA_RELEASE_ID,
    PostgresRuntimeReleaseQueryStore,
    PostgresRuntimeReleaseStore,
    _REQUIRED_COLUMN_SIGNATURES,
    _REQUIRED_CHECK_CONSTRAINTS,
    _REQUIRED_KEY_CONSTRAINTS,
    _REQUIRED_TABLE_COLUMNS,
    _serialize_registry_tables,
)
from agent_runtime.inspection.inspection_postgres_querying import (
    PostgresWorkflowInspectionRepository,
)
from agent_runtime.ledger.ledger_postgres_persistence import (
    PostgresRuntimeExecutionQueryStore,
)
from agent_runtime.registry.registry_release_compilation import (
    candidate_admission_intent,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
    RuntimeReleaseRegistrySnapshot,
)


_DDL_STATEMENT_VERBS = frozenset(
    {"ALTER", "COMMENT", "CREATE", "DROP", "TRUNCATE"}
)


class _Cursor:
    def __init__(
        self,
        *,
        namespace_exists: bool,
        columns: dict[str, tuple[str, ...]] | None = None,
        installation: tuple[str, str, str] | None = None,
    ) -> None:
        self.namespace_exists = namespace_exists
        self.columns = columns or {}
        self.installation = installation
        self.statements: list[str] = []
        self.parameters: list[Any] = []
        self.current_statement = ""
        self.closed = False

    def execute(self, statement: str, parameters: Any = None) -> None:
        self.current_statement = " ".join(statement.split())
        self.statements.append(self.current_statement)
        self.parameters.append(parameters)

    def fetchone(self) -> Any:
        statement = self.current_statement
        if "SELECT EXISTS" in statement and "pg_namespace" in statement:
            return (self.namespace_exists,)
        if "FROM agent_runtime_control.registry_schema_installation" in statement:
            return self.installation
        if statement == "SELECT clock_timestamp()":
            return (datetime(2026, 8, 19, 20, 0, tzinfo=UTC),)
        if "RETURNING" in statement:
            return ("a" * 64,)
        if "FROM agent_runtime_control.workflow_release" in statement:
            return None
        return None

    def fetchall(self) -> list[Any]:
        statement = self.current_statement
        if "FROM information_schema.columns" in statement:
            if self.columns == dict(_REQUIRED_TABLE_COLUMNS):
                return list(_REQUIRED_COLUMN_SIGNATURES)
            return [
                (table, column, "text", "NO")
                for table, columns in sorted(self.columns.items())
                for column in columns
            ]
        if "FROM information_schema.table_constraints" in statement:
            return list(_REQUIRED_KEY_CONSTRAINTS)
        if "FROM pg_constraint AS con" in statement:
            return list(_REQUIRED_CHECK_CONSTRAINTS)
        return []

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _Cursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _ready_cursor(*, state: str = "ready") -> _Cursor:
    return _Cursor(
        namespace_exists=True,
        columns=dict(_REQUIRED_TABLE_COLUMNS),
        installation=(
            REGISTRY_SCHEMA_RELEASE_ID,
            REGISTRY_SCHEMA_FINGERPRINT_SHA256,
            state,
        ),
    )


def _assert_zero_ddl(statements: list[str]) -> None:
    for statement in statements:
        assert statement.split(maxsplit=1)[0].upper() not in _DDL_STATEMENT_VERBS


def _minimal_schema(schema_id: str) -> SchemaAssetRelease:
    release_ref = f"schema:{schema_id}@v1"
    return SchemaAssetRelease.build(
        schema_asset_id=schema_id,
        schema_asset_version="v1",
        release_ref=release_ref,
        schema_document={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": release_ref,
            "type": "object",
        },
    )


@pytest.mark.parametrize(
    ("cursor", "expected"),
    (
        (_Cursor(namespace_exists=False), "absent"),
        (_Cursor(namespace_exists=True), "unknown"),
        (_ready_cursor(state="installing"), "installing"),
        (_ready_cursor(), REGISTRY_SCHEMA_RELEASE_ID),
    ),
)
def test_installed_schema_release_has_one_four_value_domain(
    cursor: _Cursor,
    expected: str,
) -> None:
    store = PostgresRuntimeReleaseStore(lambda: _Connection(cursor))

    assert store.installed_schema_release() == expected


def test_create_schema_is_explicit_absent_only_ddl() -> None:
    cursor = _Cursor(namespace_exists=False)
    connection = _Connection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    store.create_schema()

    assert any(
        statement == "CREATE SCHEMA agent_runtime_control"
        for statement in cursor.statements
    )
    assert any("registry_schema_installation" in statement for statement in cursor.statements)
    assert connection.committed is True
    assert connection.rolled_back is False


def test_create_schema_refuses_every_existing_namespace() -> None:
    cursor = _Cursor(namespace_exists=True)
    connection = _Connection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    with pytest.raises(RuntimeError, match="requires an absent namespace: unknown"):
        store.create_schema()

    _assert_zero_ddl(cursor.statements)
    assert connection.rolled_back is True


@pytest.mark.parametrize("state", ("absent", "installing", "unknown"))
def test_ordinary_load_refuses_every_non_ready_state(state: str) -> None:
    if state == "absent":
        cursor = _Cursor(namespace_exists=False)
    elif state == "installing":
        cursor = _ready_cursor(state="installing")
    else:
        cursor = _Cursor(namespace_exists=True)
    connection = _Connection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    with pytest.raises(RuntimeError, match=f"not ready: {state}"):
        store.load_release_registry()

    _assert_zero_ddl(cursor.statements)


@pytest.mark.parametrize("state", ("absent", "installing", "unknown"))
def test_ordinary_register_refuses_every_non_ready_state(state: str) -> None:
    if state == "absent":
        cursor = _Cursor(namespace_exists=False)
    elif state == "installing":
        cursor = _ready_cursor(state="installing")
    else:
        cursor = _Cursor(namespace_exists=True)
    connection = _Connection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    with pytest.raises(RuntimeError, match=f"not ready: {state}"):
        store.register_bundle(
            RuntimeReleaseBundle(
                schema_assets=(_minimal_schema(f"non_ready_{state}"),)
            )
        )

    _assert_zero_ddl(cursor.statements)
    assert connection.rolled_back is True


def test_ready_empty_registry_load_and_query_execute_zero_ddl() -> None:
    load_cursor = _ready_cursor()
    loaded = PostgresRuntimeReleaseStore(
        lambda: _Connection(load_cursor)
    ).load_release_registry()

    query_cursor = _ready_cursor()
    result = PostgresRuntimeReleaseQueryStore(
        lambda: _Connection(query_cursor)
    ).load_workflow_release("runtime-workflow:missing@v1")

    assert loaded.snapshot().schema_assets == ()
    assert result is None
    _assert_zero_ddl(load_cursor.statements)
    _assert_zero_ddl(query_cursor.statements)
    assert query_cursor.statements[0] == "SET TRANSACTION READ ONLY"


def test_postgres_loaded_registry_refuses_non_store_admission_finalization() -> None:
    cursor = _ready_cursor()
    loaded = PostgresRuntimeReleaseStore(
        lambda: _Connection(cursor)
    ).load_release_registry()
    schema = _minimal_schema("outside_store_clock")

    with pytest.raises(
        RuntimeError,
        match="cannot finalize admissions outside its store",
    ):
        loaded.register_bundle(
            RuntimeReleaseBundle(
                schema_assets=(schema,),
                admission_intents=(candidate_admission_intent(schema),),
            )
        )

    assert loaded.snapshot().schema_assets == ()
    assert loaded.snapshot().admissions == ()


def test_inspection_repository_inherits_registry_readiness_refusal() -> None:
    release_cursor = _Cursor(namespace_exists=False)
    release_queries = PostgresRuntimeReleaseQueryStore(
        lambda: _Connection(release_cursor)
    )
    execution_queries = PostgresRuntimeExecutionQueryStore(lambda: object())
    repository = PostgresWorkflowInspectionRepository(
        execution_queries,
        release_queries=release_queries,
    )

    class _Trace:
        def records_of_type(self, record_type: type[Any]) -> tuple[Any, ...]:
            return (
                SimpleNamespace(
                    workflow_release_ref="runtime-workflow:inspection@v1",
                    execution_release_ref="execution:inspection@v1",
                ),
            )

    with pytest.raises(RuntimeError, match="not ready: absent"):
        repository.load_workflow_release(_Trace())  # type: ignore[arg-type]

    assert release_cursor.statements[0] == "SET TRANSACTION READ ONLY"


def test_postgres_recording_time_finalizes_admission_intent() -> None:
    schema = _minimal_schema("postgres_time_schema")
    cursor = _ready_cursor()
    connection = _Connection(cursor)
    store = PostgresRuntimeReleaseStore(lambda: connection)

    registry = store.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(schema,),
            admission_intents=(candidate_admission_intent(schema),),
        )
    )

    admission = registry.snapshot().admissions[0]
    assert admission.recorded_at_utc == "2026-08-19T20:00:00Z"
    assert "recorded_at_utc" not in candidate_admission_intent(schema).as_dict()
    assert cursor.statements[0] == "SELECT pg_advisory_xact_lock(hashtext(%s))"
    assert cursor.statements.count("SELECT clock_timestamp()") == 1
    _assert_zero_ddl(cursor.statements)
    assert connection.committed is True


def test_restore_refuses_active_pointer_mismatch() -> None:
    schema = _minimal_schema("pointer_mismatch")
    registry = RuntimeReleaseRegistry(
        recording_clock=lambda: "2026-08-19T20:00:00Z"
    )
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(schema,),
            admission_intents=(candidate_admission_intent(schema),),
        )
    )
    snapshot = registry.snapshot()
    tampered = replace(
        snapshot,
        active_release_refs={
            f"schema_asset:{schema.schema_asset_id}": schema.release_ref
        },
    )

    with pytest.raises(RuntimeError, match="active release pointers"):
        RuntimeReleaseRegistry.restore_persisted_snapshot(tampered)


def test_restore_refuses_tampered_admission_record() -> None:
    schema = _minimal_schema("tampered_admission")
    registry = RuntimeReleaseRegistry(
        recording_clock=lambda: "2026-08-19T20:00:00Z"
    )
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(schema,),
            admission_intents=(candidate_admission_intent(schema),),
        )
    )
    snapshot = registry.snapshot()
    tampered_admission = replace(
        snapshot.admissions[0],
        admission_sha256="f" * 64,
    )
    tampered = replace(snapshot, admissions=(tampered_admission,))

    with pytest.raises(ValueError, match="release admission hash mismatch"):
        RuntimeReleaseRegistry.restore_persisted_snapshot(tampered)


def test_row_projection_covers_prompt_component_and_parallel_group() -> None:
    component = PromptComponentRelease.build(
        prompt_component_id="postgres_prompt_component",
        prompt_component_version="v1",
        release_ref="prompt-component:postgres_prompt_component@v1",
        component_kind=PromptComponentKind.TASK_INSTRUCTION,
        media_type="text/markdown",
        formatter_id="postgres_test_formatter",
        formatter_version="v1",
        source_members=(
            ReleaseMember(
                member_ref="source:postgres_prompt_component@v1",
                member_sha256="1" * 64,
                media_type="application/json",
            ),
        ),
        formatted_content="Test.\n",
    )
    module = _parallel_module()
    workflow = _parallel_workflow(module)
    rows = _serialize_registry_tables(
        RuntimeReleaseRegistrySnapshot(
            schema_assets=(),
            prompt_components=(component,),
            prompt_bundles=(),
            behavior_policies=(),
            evaluation_policies=(),
            retry_policies=(),
            execution_variant_policies=(),
            execution_profiles=(),
            modules=(module,),
            workflows=(workflow,),
            admissions=(),
            active_release_refs={},
        )
    )

    assert rows["prompt_component_release"][0]["payload"]["formatted_content"] == "Test.\n"
    assert rows["workflow_parallel_group_binding"][0]["group_id"] == "review_group"


def _real_postgres() -> tuple[Any, str, str]:
    database_url = os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("AGENT_RUNTIME_TEST_DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    schema = f"agent_runtime_2c1_{uuid4().hex[:12]}"
    return psycopg, database_url, schema


def test_real_postgres_create_register_reload_and_replay() -> None:
    psycopg, database_url, schema_name = _real_postgres()
    store = PostgresRuntimeReleaseStore.from_dsn(
        database_url,
        schema=schema_name,
    )
    bundle = _complete_bundle_for_real_postgres()
    try:
        assert store.installed_schema_release() == "absent"
        store.create_schema()
        assert store.installed_schema_release() == REGISTRY_SCHEMA_RELEASE_ID
        first = store.register_bundle(bundle).snapshot()
        loaded = store.load_release_registry().snapshot()
        replayed = store.register_bundle(bundle).snapshot()
        assert loaded == first == replayed
        assert len(first.admissions) == 10
        schema = bundle.schema_assets[0]
        conflicting = SchemaAssetRelease.build(
            schema_asset_id=schema.schema_asset_id,
            schema_asset_version=schema.schema_asset_version,
            release_ref=schema.release_ref,
            schema_document={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": schema.release_ref,
                "type": "object",
                "title": "conflicting",
            },
        )
        with pytest.raises(ValueError, match="release_ref collision"):
            store.register_bundle(
                RuntimeReleaseBundle(schema_assets=(conflicting,))
            )
        assert store.load_release_registry().snapshot() == first
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')


def test_real_postgres_refuses_existing_installing_and_drifted_namespace() -> None:
    psycopg, database_url, schema_name = _real_postgres()
    store = PostgresRuntimeReleaseStore.from_dsn(
        database_url,
        schema=schema_name,
    )
    try:
        store.create_schema()
        with pytest.raises(RuntimeError, match="requires an absent namespace"):
            store.create_schema()
        with psycopg.connect(database_url) as connection:
            connection.execute(
                f"UPDATE {schema_name}.registry_schema_installation "
                "SET installation_state = 'installing'"
            )
        assert store.installed_schema_release() == "installing"
        with pytest.raises(RuntimeError, match="not ready: installing"):
            store.load_release_registry()
        with psycopg.connect(database_url) as connection:
            connection.execute(
                f"UPDATE {schema_name}.registry_schema_installation "
                "SET installation_state = 'ready'"
            )
            connection.execute(f"CREATE TABLE {schema_name}.unexpected_table (id TEXT)")
        assert store.installed_schema_release() == "unknown"
        with pytest.raises(RuntimeError, match="not ready: unknown"):
            store.load_release_registry()
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')


def test_real_postgres_fingerprint_detects_key_constraint_drift() -> None:
    psycopg, database_url, schema_name = _real_postgres()
    store = PostgresRuntimeReleaseStore.from_dsn(
        database_url,
        schema=schema_name,
    )
    try:
        store.create_schema()
        with psycopg.connect(database_url) as connection:
            connection.execute(
                f"ALTER TABLE {schema_name}.schema_asset_release DROP CONSTRAINT "
                "schema_asset_release_subject_id_release_version_key"
            )
        assert store.installed_schema_release() == "unknown"
        with pytest.raises(RuntimeError, match="not ready: unknown"):
            store.load_release_registry()
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE')


def _schema_for_real_postgres() -> SchemaAssetRelease:
    return SchemaAssetRelease.build(
        schema_asset_id="real_postgres_schema",
        schema_asset_version="v1",
        release_ref="schema:real_postgres_schema@v1",
        schema_document={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "schema:real_postgres_schema@v1",
            "type": "object",
        },
    )


def _complete_bundle_for_real_postgres() -> RuntimeReleaseBundle:
    schema = _schema_for_real_postgres()
    component = PromptComponentRelease.build(
        prompt_component_id="real_postgres_instruction",
        prompt_component_version="v1",
        release_ref="prompt-component:real_postgres_instruction@v1",
        component_kind=PromptComponentKind.TASK_INSTRUCTION,
        media_type="text/markdown",
        formatter_id="real_postgres_formatter",
        formatter_version="v1",
        source_members=(
            ReleaseMember(
                member_ref="source:real_postgres_instruction@v1",
                member_sha256="1" * 64,
                media_type="application/json",
            ),
        ),
        formatted_content="Return one object.\n",
    )
    prompt_bundle = PromptBundleRelease.build(
        prompt_bundle_id="real_postgres_prompt",
        prompt_bundle_version="v1",
        release_ref="prompt-bundle:real_postgres_prompt@v1",
        compiler_version="v1",
        members=(
            ReleaseMember(
                member_ref=component.release_ref,
                member_sha256=component.release_sha256,
                media_type=component.media_type,
            ),
        ),
        compiled_static_body=component.formatted_content,
    )

    def policy(cls: Any, policy_id: str, prefix: str, document: dict[str, Any]):
        return cls.build(
            policy_id=policy_id,
            policy_version="v1",
            release_ref=f"{prefix}:{policy_id}@v1",
            policy_schema_ref=schema.release_ref,
            policy_schema_sha256=schema.schema_sha256,
            policy_document=document,
        )

    behavior = policy(
        BehaviorPolicyRelease,
        "real_postgres_behavior",
        "behavior-policy",
        {"context_isolation": "workflow_execution_isolated"},
    )
    evaluation = policy(
        EvaluationPolicyRelease,
        "real_postgres_evaluation",
        "evaluation-policy",
        {"evaluation_mode": "module_candidate"},
    )
    retry = policy(
        RetryPolicyRelease,
        "real_postgres_retry",
        "retry-policy",
        {"max_attempts": 2},
    )
    profile = ExecutionProfileRelease.build(
        execution_profile_id="real_postgres_profile",
        execution_profile_version="v1",
        release_ref="execution-profile:real_postgres_profile@v1",
        executor_adapter_id="real_postgres_adapter",
        executor_adapter_revision="v1",
        transport_kind="in_process_test",
        provider_id="real_postgres_provider",
        model_id="real_postgres_model",
        reasoning_profile="none",
        execution_mode="tool_free",
        semantic_input_delivery_mode="inline",
        attempt_workspace_policy="none",
        gateway_access_reasons=(),
        output_constraint_mode="prompt_only_json",
        tool_policy=(),
        network_policy="denied",
        timeout_seconds=60,
    )
    module = RuntimeModuleRelease.build(
        module_id="real_postgres_module",
        module_version="v1",
        release_ref="runtime-module:real_postgres_module@v1",
        module_kind=ModuleKind.AGENT,
        owner_contract_ref="contract:real_postgres_module@v1",
        owner_contract_sha256="2" * 64,
        executable_ref=None,
        executable_sha256=None,
        input_schema_ref=schema.release_ref,
        input_schema_sha256=schema.schema_sha256,
        output_schema_ref=schema.release_ref,
        output_schema_sha256=schema.schema_sha256,
        prompt_bundle_ref=prompt_bundle.release_ref,
        prompt_bundle_sha256=prompt_bundle.release_sha256,
        declared_operation_ids=("invoke_model",),
        behavior_policy_ref=behavior.release_ref,
        behavior_policy_sha256=behavior.release_sha256,
        evaluation_policy_ref=evaluation.release_ref,
        evaluation_policy_sha256=evaluation.release_sha256,
        retry_policy_ref=retry.release_ref,
        retry_policy_sha256=retry.release_sha256,
        compatible_transport_kinds=("in_process_test",),
        entry_policy=ModuleEntryPolicy.STANDALONE_ALLOWED,
        output_resolution_policy=OutputResolutionPolicy.EVALUATED_SINGLE,
    )
    workflow = WorkflowRelease.build(
        workflow_id="real_postgres_workflow",
        workflow_version="v1",
        workflow_contract_version="contract_v1",
        release_ref="runtime-workflow:real_postgres_workflow@v1",
        owner_contract_ref="contract:real_postgres_workflow@v1",
        owner_contract_sha256="3" * 64,
        graph_ref="workflow-graph:real_postgres_workflow@v1",
        graph_sha256="4" * 64,
        initial_node_id="run",
        nodes=(
            WorkflowNodeBinding(
                node_id="run",
                node_kind=WorkflowNodeKind.MODULE,
                module_release_ref=module.release_ref,
                module_release_sha256=module.release_sha256,
                input_mapping_ref="input-map:real_postgres_workflow@v1",
                input_mapping_sha256="5" * 64,
            ),
        ),
        edges=(WorkflowEdge("run", "completed", None, True),),
        authorization_manifest_ref="authorization:real_postgres_workflow@v1",
        authorization_manifest_sha256="6" * 64,
        execution_release_ref="execution:real_postgres_workflow@v1",
        execution_release_sha256="7" * 64,
    )
    variant = policy(
        ExecutionVariantPolicyRelease,
        "real_postgres_variant",
        "execution-variant-policy",
        {
            "origin_kind": "workflow",
            "origin_release_ref": workflow.release_ref,
            "origin_release_sha256": workflow.release_sha256,
            "bindings": [
                {
                    "position_id": "run",
                    "execution_profile_release_ref": profile.release_ref,
                    "execution_profile_release_sha256": profile.release_sha256,
                }
            ],
        },
    )
    releases = (
        schema,
        component,
        prompt_bundle,
        behavior,
        evaluation,
        retry,
        variant,
        profile,
        module,
        workflow,
    )
    return RuntimeReleaseBundle(
        schema_assets=(schema,),
        prompt_components=(component,),
        prompt_bundles=(prompt_bundle,),
        behavior_policies=(behavior,),
        evaluation_policies=(evaluation,),
        retry_policies=(retry,),
        execution_variant_policies=(variant,),
        execution_profiles=(profile,),
        modules=(module,),
        workflows=(workflow,),
        admission_intents=tuple(
            candidate_admission_intent(release) for release in releases
        ),
    )


def _parallel_module() -> RuntimeModuleRelease:
    return RuntimeModuleRelease.build(
        module_id="postgres_parallel_module",
        module_version="v1",
        release_ref="runtime-module:postgres_parallel_module@v1",
        module_kind=ModuleKind.DETERMINISTIC,
        owner_contract_ref="contract:postgres_parallel@v1",
        owner_contract_sha256="2" * 64,
        executable_ref="callable:postgres_parallel@v1",
        executable_sha256="3" * 64,
        input_schema_ref="schema:postgres_parallel_input@v1",
        input_schema_sha256="4" * 64,
        output_schema_ref="schema:postgres_parallel_output@v1",
        output_schema_sha256="5" * 64,
        prompt_bundle_ref=None,
        prompt_bundle_sha256=None,
        declared_operation_ids=(),
        behavior_policy_ref="behavior-policy:postgres_parallel@v1",
        behavior_policy_sha256="6" * 64,
        evaluation_policy_ref="evaluation-policy:postgres_parallel@v1",
        evaluation_policy_sha256="7" * 64,
        retry_policy_ref="retry-policy:postgres_parallel@v1",
        retry_policy_sha256="8" * 64,
        compatible_transport_kinds=("in_process_test",),
        entry_policy=ModuleEntryPolicy.WORKFLOW_BOUND,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )


def _parallel_workflow(module: RuntimeModuleRelease) -> WorkflowRelease:
    def module_node(node_id: str) -> WorkflowNodeBinding:
        return WorkflowNodeBinding(
            node_id=node_id,
            node_kind=WorkflowNodeKind.MODULE,
            module_release_ref=module.release_ref,
            module_release_sha256=module.release_sha256,
            input_mapping_ref=f"input-map:{node_id}@v1",
            input_mapping_sha256="9" * 64,
        )

    return WorkflowRelease.build(
        workflow_id="postgres_parallel_workflow",
        workflow_version="v1",
        workflow_contract_version="contract_v1",
        release_ref="runtime-workflow:postgres_parallel_workflow@v1",
        owner_contract_ref="contract:postgres_parallel_workflow@v1",
        owner_contract_sha256="a" * 64,
        graph_ref="workflow-graph:postgres_parallel_workflow@v1",
        graph_sha256="b" * 64,
        initial_node_id="parallel",
        nodes=(
            WorkflowNodeBinding(
                node_id="parallel",
                node_kind=WorkflowNodeKind.CONTROL,
                module_release_ref=None,
                module_release_sha256=None,
                input_mapping_ref=None,
                input_mapping_sha256=None,
            ),
            module_node("left"),
            module_node("right"),
            module_node("join"),
        ),
        edges=(
            WorkflowEdge("parallel", "completed", "join", False),
            WorkflowEdge("left", "completed", "join", False),
            WorkflowEdge("right", "completed", "join", False),
            WorkflowEdge("join", "completed", None, True),
        ),
        authorization_manifest_ref="authorization:postgres_parallel@v1",
        authorization_manifest_sha256="c" * 64,
        execution_release_ref="execution:postgres_parallel@v1",
        execution_release_sha256="d" * 64,
        parallel_groups=(
            WorkflowParallelGroupBinding(
                group_id="review_group",
                control_node_id="parallel",
                branch_node_ids=("left", "right"),
                join_node_id="join",
                completion_outcome_id="completed",
                join_policy=WorkflowParallelJoinPolicy.ALL_REQUIRED,
            ),
        ),
    )
