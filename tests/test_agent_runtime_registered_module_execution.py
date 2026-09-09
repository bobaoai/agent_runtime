"""Public preparation of a registered Module and its native recorded execution."""

from dataclasses import fields, replace
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import uuid
import threading

import pytest

from agent_runtime import (
    ExecutionAuthorizationContextEnvelope, GatewayDecisionEffect,
    OutputResolutionPolicy, WorkflowEdge, run_registered_workflow_module,
)
from agent_runtime.execution import AgentExecutionAdapterRegistry, InMemoryCellArtifactStore
from agent_runtime.contracts.ledger_lineage_definition import ModuleOutputResolutionRecord
from agent_runtime.contracts.ledger_record_definition import (
    CommitReceipt, ExecutionInputRef, WorkflowAttemptStartedRecord, WorkflowExecutionRecord,
)
from agent_runtime.contracts.registry_release_definition import WorkflowNodeKind
from agent_runtime.invocation.invocation_codex_module_invocation import (
    CodexCliInvocationResult, CodexCliModuleExecutor,
)
from agent_runtime.ledger import (
    InMemoryRuntimeExecutionRecordStore, PostgresRuntimeExecutionQueryStore,
    PostgresRuntimeExecutionRecordStore, WorkflowModuleLedgerRecorder,
    WorkflowExecutionLedgerBinding, WorkflowExecutionLedgerRecorder,
)
from agent_runtime.registry import (
    AgentModuleReleaseCandidate, ExecutionVariantPolicyReleaseCandidate,
    ExecutionVariantProfileBindingCandidate, PostgresRuntimeReleaseStore,
    RuntimeReleaseBundle, WorkflowNodeReleaseCandidate, WorkflowReleaseCandidate,
    compile_agent_module_release, compile_execution_variant_policy_release,
    compile_workflow_release,
)

from test_agent_runtime_native_structured_output import (
    _compile_native_module, _register_compiled_for_evaluation, _ProductAuthorityDouble,
    _TEST_TIME,
)


class _Contents:
    def __init__(self):
        self.values = {}

    def stage_content(self, content):
        content.validate()
        key = (content.workflow_execution_id, content.content_ref)
        prior = self.values.get(key)
        if prior is not None:
            assert (prior.content_sha256, prior.body, prior.media_type) == (
                content.content_sha256, content.body, content.media_type
            )
        self.values[key] = content
        return content

    commit_content = stage_content

    def contains(self, output):
        return any(value.content_ref == output.output_ref
                   and value.content_sha256 == output.output_sha256
                   and hashlib.sha256(value.body).hexdigest() == output.output_sha256
                   for value in self.values.values())


class _Host:
    """Explicit test ports; permission answers never come from the new helper."""

    def __init__(self, cell, workflow):
        self.cell = cell
        self.workflow = workflow
        self.calls = 0
        self.client = None
        self.operation_effect = GatewayDecisionEffect.ALLOW
        self.context_mutation = None
        self.reject = False

    def authorize(self, request):
        self.calls += 1
        if self.reject:
            raise PermissionError("host declined the request")
        refs = tuple(self.cell.put_bytes(
            artifact_kind_id=kind, schema_version="v1", schema_ref="schema:host_evidence@v1",
            schema_sha256=hashlib.sha256(b'{"type":"object"}').hexdigest(),
            content=json.dumps({"private_host_fact": kind}).encode(), media_type="application/json",
            logical_name=kind, idempotency_key=request.workflow_execution_id + "_" + kind,
        ) for kind in ("decision", "delegation", "entitlement"))
        values = dict(
            context_id="context_" + request.workflow_execution_id,
            context_ref="host-context:" + request.workflow_execution_id,
            workflow_execution_id=request.workflow_execution_id, workflow_release_id=self.workflow.workflow_id,
            principal_id="principal_test", actor_workload_id="runtime_test_host", tenant_id="tenant_test",
            cell_id="cell_test", authorization_decision_ref=refs[0].artifact_ref,
            catalog_release_ref=self.workflow.release_ref, input_scope_refs=(request.input_package_ref,),
            effective_at_utc="2026-01-01T00:00:00Z", expiry_at_utc="2027-01-01T00:00:00Z",
        )
        if self.context_mutation:
            values.update(self.context_mutation)
        envelope = ExecutionAuthorizationContextEnvelope.build(**values)
        self.client = _ProductAuthorityDouble(envelope)
        self.client.operation_effect = self.operation_effect
        return envelope, *refs

    def validate_execution_context(self, *args):
        return self.client.validate_execution_context(*args)

    def authorize_operation(self, query):
        return self.client.authorize_operation(query)


def _environment(tmp_path, *, policy=OutputResolutionPolicy.EVALUATED_SINGLE,
                 input_schema=None, output_schema=None, pg=None):
    options = {} if output_schema is None else {"output_schema_document": output_schema}
    compiled = _compile_native_module(tmp_path, output_resolution_policy=policy, **options)
    if input_schema is not None:
        changed = compile_agent_module_release(AgentModuleReleaseCandidate(
            module_id=compiled.module.module_id, module_version="candidate_v1",
            owner_contract_ref=compiled.module.owner_contract_ref,
            owner_contract_content="# Owner\n\nRegistered Module: `native_module`.\n",
            input_schema_ref=compiled.module.input_schema_ref, input_schema_document=json.dumps(input_schema),
            output_schema_ref=compiled.module.output_schema_ref,
            output_schema_document=json.dumps(output_schema),
            instruction_source_ref="host-source:native-skill/native_module/prompt@candidate_v1",
            instruction_text="Produce the native result.\n", declared_operation_ids=("invoke_model",),
            compatible_transport_kinds=("codex_cli",),
            behavior_policy_ref=compiled.behavior_policy.release_ref, behavior_policy_sha256=compiled.behavior_policy.release_sha256,
            evaluation_policy_ref=compiled.evaluation_policy.release_ref, evaluation_policy_sha256=compiled.evaluation_policy.release_sha256,
            retry_policy_ref=compiled.retry_policy.release_ref, retry_policy_sha256=compiled.retry_policy.release_sha256,
            output_resolution_policy=policy,
        ))
        for name in ("module", "schema_assets", "prompt_components", "prompt_bundle"):
            setattr(compiled, name, getattr(changed, name))
    registry = _register_compiled_for_evaluation(compiled)
    workflow_candidate = WorkflowReleaseCandidate(
        workflow_id="single_module", workflow_version="v1", workflow_contract_version="v1",
        owner_contract_ref=compiled.module.owner_contract_ref,
        owner_contract_content="# Owner\n\nRegistered Module: `native_module`.\n",
        graph_ref="workflow-graph:single_module@v1", initial_node_id="run",
        nodes=(WorkflowNodeReleaseCandidate(node_id="run", node_kind=WorkflowNodeKind.MODULE,
            module_release_ref=compiled.module.release_ref, module_release_sha256=compiled.module.release_sha256,
            input_mapping_ref="input-mapping:single_module@v1", input_mapping_document={"task_input": "payload"}),),
        edges=(WorkflowEdge("run", "complete", None, True),),
        authorization_manifest_ref="authorization-manifest:single_module@v1",
        authorization_manifest_document={"operations": ["invoke_model"]},
        execution_binding_ref="execution-binding:single_module@v1",
        execution_binding_document={"schema_version": "workflow_execution_binding_v1",
            "variant_policy_family": "execution_variant_policy", "workflow_id": "single_module"},
    )
    workflow = compile_workflow_release(workflow_candidate)
    selection = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id="single_module_profile", policy_version="v1", origin_kind="workflow",
        origin_release_ref=workflow.release_ref, origin_release_sha256=workflow.release_sha256,
        bindings=(ExecutionVariantProfileBindingCandidate("run", compiled.execution_profile.release_ref,
                                                         compiled.execution_profile.release_sha256),),
    ))
    registry.register_bundle(RuntimeReleaseBundle(workflows=(workflow,), execution_variant_policies=(selection,)))
    if pg:
        release_store, store, query = pg
        snapshot = registry.snapshot()
        release_store.register_bundle(RuntimeReleaseBundle(**{
            field.name: getattr(snapshot, field.name) for field in fields(RuntimeReleaseBundle)
        }))
        registry = release_store.load_release_registry()
        contents = store
    else:
        contents = _Contents()
        store = InMemoryRuntimeExecutionRecordStore(execution_output_integrity_check=contents.contains)
        query = None
    cell = InMemoryCellArtifactStore()
    host = _Host(cell, workflow)
    calls = []
    state = {"payload": {"value": "done"}, "provider_error": False}

    def invoke(*, argv, prompt, cwd, timeout_seconds):
        assert timeout_seconds == compiled.execution_profile.timeout_seconds
        assert cwd.is_relative_to(tmp_path)
        assert "--output-schema" in argv
        trace = store.load_trace(state["execution_id"])
        assert trace.records_of_type(WorkflowExecutionRecord)
        assert trace.records_of_type(WorkflowAttemptStartedRecord)
        assert "private_host_fact" not in prompt
        calls.append({"argv": argv, "prompt": prompt})
        if state["provider_error"]:
            return CodexCliInvocationResult(1, "provider unavailable", "")
        output = {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(state["payload"])}}
        usage = {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}
        return CodexCliInvocationResult(0, json.dumps(output) + "\n" + json.dumps(usage), "")

    original_authorize = host.authorize

    def authorize(request):
        state["execution_id"] = request.workflow_execution_id
        return original_authorize(request)

    adapters = AgentExecutionAdapterRegistry()
    adapters.register(CodexCliModuleExecutor(release_registry=registry, artifact_host=cell,
        workspace_root=tmp_path / "attempts", invoker=invoke, codex_bin="controlled-codex"))
    kwargs = dict(release_registry=registry, workflow=workflow, variant_policy=selection, authorize=authorize,
        context_client=host, operation_client=host, enforcing_gateway_id="agent_runtime_module_kernel",
        environment_id="development", adapters=adapters, artifact_host=cell, record_store=store,
        content_store=contents, claim_token_secret=b"host-claim-material-never-model-visible", clock=lambda: _TEST_TIME)
    return SimpleNamespace(kwargs=kwargs, module=compiled.module, profile=compiled.execution_profile,
                           registry=registry, host=host, cell=cell, store=store, contents=contents,
                           calls=calls, state=state, query=query, workflow_candidate=workflow_candidate)


def _run(env, *, payload=None, key="review_one", **changes):
    kwargs = {**env.kwargs, **changes}
    return run_registered_workflow_module(module_id=env.module.module_id,
        input_payload={"value": "example"} if payload is None else payload,
        idempotency_key=key, **kwargs)


def test_candidate_result_replay_preserves_policy_and_recorded_bytes(tmp_path):
    env = _environment(tmp_path)
    registry_before = env.registry.snapshot()
    first = _run(env)
    trace_before = env.store.load_trace(first.module_run.workflow_execution_id)
    second = _run(env, clock=lambda: "2026-08-10T12:00:00Z")
    assert first.resolution is second.resolution is None
    assert first.module_run.workflow_execution_id == second.module_run.workflow_execution_id
    assert len(env.calls) == env.host.calls == 1
    assert env.store.load_trace(first.module_run.workflow_execution_id) == trace_before
    assert not trace_before.records_of_type(ModuleOutputResolutionRecord)
    assert env.registry.snapshot() == registry_before
    assert first.attempts[0].usage.input_tokens == 3
    assert first.attempts[0].usage.output_tokens == 2
    body = env.cell.read_bytes(first.outputs[0].output_ref, first.outputs[0].output_sha256)
    assert json.loads(body) == {"value": "done"}
    assert first.outputs == second.outputs


def test_second_schema_is_not_reviewer_specific(tmp_path):
    input_schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "schema:native_input@v1",
                    "type": "object", "properties": {"ready": {"type": "boolean"}},
                    "required": ["ready"], "additionalProperties": False}
    output_schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "schema:native_output@v1",
                     "type": "object", "properties": {"count": {"type": "integer"}},
                     "required": ["count"], "additionalProperties": False}
    env = _environment(tmp_path, input_schema=input_schema, output_schema=output_schema)
    env.state["payload"] = {"count": 7}
    result = _run(env, payload={"ready": True})
    assert json.loads(env.cell.read_bytes(result.outputs[0].output_ref, result.outputs[0].output_sha256)) == {"count": 7}


@pytest.mark.parametrize("payload", [{}, {"value": 1}, {"value": float("nan")}, []])
def test_invalid_input_stops_before_authorization_or_provider(tmp_path, payload):
    env = _environment(tmp_path)
    with pytest.raises(Exception):
        _run(env, payload=payload)
    assert env.host.calls == len(env.calls) == 0


def test_wrong_target_is_not_inferred_from_workflow(tmp_path):
    env = _environment(tmp_path)
    with pytest.raises(ValueError, match="requested target"):
        run_registered_workflow_module(module_id="different_module", input_payload={"value": "x"},
                                       idempotency_key="wrong_target", **env.kwargs)
    assert env.host.calls == len(env.calls) == 0


@pytest.mark.parametrize("field", ["workflow_execution_id", "workflow_release_id", "authorization_decision_ref", "input_scope_refs"])
def test_host_context_cannot_cross_frozen_request(tmp_path, field):
    env = _environment(tmp_path)
    env.host.context_mutation = {field: ("other:input",) if field == "input_scope_refs"
                                else "other:decision" if field == "authorization_decision_ref" else "other_identity"}
    with pytest.raises(PermissionError):
        _run(env)
    assert not env.calls


def test_host_refusal_and_operation_denial_are_not_replaced_by_allow(tmp_path):
    env = _environment(tmp_path)
    env.host.reject = True
    with pytest.raises(PermissionError, match="host declined"):
        _run(env)
    env.host.reject = False
    env.host.operation_effect = GatewayDecisionEffect.DENY
    result = _run(env)
    assert result.attempts[0].status == "failed"
    assert not env.calls


def test_same_key_changed_input_is_refused(tmp_path):
    env = _environment(tmp_path)
    _run(env)
    with pytest.raises(ValueError):
        _run(env, payload={"value": "changed"})
    assert len(env.calls) == env.host.calls == 1


def _selection(env, *, position="run", origin="workflow", profile=None):
    workflow = env.kwargs["workflow"]
    source = workflow if origin == "workflow" else env.module
    profile = profile or env.profile
    selection = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id="alternate_selection", policy_version="v1", origin_kind=origin,
        origin_release_ref=source.release_ref, origin_release_sha256=source.release_sha256,
        bindings=(ExecutionVariantProfileBindingCandidate(position, profile.release_ref, profile.release_sha256),),
    ))
    env.registry.register_bundle(RuntimeReleaseBundle(execution_variant_policies=(selection,)))
    return selection


@pytest.mark.parametrize("case", ["workflow_bytes", "variant_bytes", "wrong_node", "wrong_origin", "agent_profile"])
def test_registered_configuration_is_checked_before_provider(tmp_path, case):
    env = _environment(tmp_path)
    changes = {}
    if case == "workflow_bytes":
        changes["workflow"] = replace(env.kwargs["workflow"], workflow_id="tampered")
    elif case == "variant_bytes":
        changes["variant_policy"] = replace(env.kwargs["variant_policy"], policy_id="tampered")
    elif case == "wrong_node":
        changes["variant_policy"] = _selection(env, position="other_node")
    elif case == "wrong_origin":
        changes["variant_policy"] = _selection(env, origin="standalone_module")
    else:
        compiled = _compile_native_module(tmp_path, execution_profile_id="draft_profile", execution_mode="agent",
                                          attempt_workspace_policy="own_draft_read_write")
        env.registry.register_bundle(RuntimeReleaseBundle(execution_profiles=(compiled.execution_profile,)))
        changes["variant_policy"] = _selection(env, profile=compiled.execution_profile)
    with pytest.raises(ValueError):
        _run(env, **changes)
    assert env.host.calls == len(env.calls) == 0


def test_replay_checks_persistent_identity_with_a_fresh_staging_host(tmp_path):
    env = _environment(tmp_path)
    result = _run(env)
    fresh = InMemoryCellArtifactStore()
    with pytest.raises(ValueError, match="already bound"):
        _run(env, payload={"value": "changed"}, artifact_host=fresh)
    selection = _selection(env)
    with pytest.raises(ValueError, match="already bound"):
        _run(env, variant_policy=selection)
    replay = _run(env, artifact_host=InMemoryCellArtifactStore())
    assert result.outputs == replay.outputs
    assert result.resolution is replay.resolution is None
    assert env.host.calls == len(env.calls) == 1


def test_multi_node_workflow_uses_its_existing_graph_entry(tmp_path):
    env = _environment(tmp_path)
    candidate = env.workflow_candidate
    workflow = compile_workflow_release(replace(
        candidate, workflow_version="v2", graph_ref="workflow-graph:single_module@v2",
        execution_binding_ref="execution-binding:single_module@v2",
        nodes=(*candidate.nodes, replace(candidate.nodes[0], node_id="second")),
        edges=(WorkflowEdge("run", "complete", "second", False), WorkflowEdge("second", "complete", None, True)),
    ))
    env.registry.register_bundle(RuntimeReleaseBundle(workflows=(workflow,)))
    with pytest.raises(ValueError, match="one registered Module node"):
        _run(env, workflow=workflow)
    assert len(env.calls) == env.host.calls == 0


@pytest.mark.parametrize("stage", ["start", "content", "finalization"])
def test_storage_failure_never_returns_success(tmp_path, stage, monkeypatch):
    env = _environment(tmp_path)
    def fail(*args, **kwargs):
        raise RuntimeError("storage failure at " + stage)
    if stage == "start":
        monkeypatch.setattr(env.store, "commit", fail)
    elif stage == "content":
        monkeypatch.setattr(env.contents, "commit_content", fail)
    else:
        monkeypatch.setattr(env.store, "finalize_attempt", fail)
    with pytest.raises(RuntimeError, match="storage failure"):
        _run(env)
    assert len(env.calls) == (1 if stage == "finalization" else 0)
    if stage != "start":
        with pytest.raises(RuntimeError, match="recovery"):
            _run(env)
        assert env.host.calls == 1


@pytest.mark.parametrize("failure", ["provider", "schema"])
def test_failed_attempt_replays_without_reinvocation(tmp_path, failure):
    env = _environment(tmp_path)
    if failure == "provider":
        env.state["provider_error"] = True
    else:
        env.state["payload"] = {"unexpected": True}
    result = _run(env)
    assert result.attempts[0].status == "failed"
    assert result.attempts[0].failure_class == failure
    assert _run(env).attempts[0].status == "failed"
    assert len(env.calls) == env.host.calls == 1


@pytest.fixture
def pg_stores():
    dsn = os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires explicit AGENT_RUNTIME_TEST_DATABASE_URL")
    import psycopg
    prefix = "module_entry_" + uuid.uuid4().hex[:16]
    registry = PostgresRuntimeReleaseStore.from_dsn(dsn, schema=prefix + "_registry")
    store = PostgresRuntimeExecutionRecordStore.from_dsn(dsn, schema=prefix + "_execution")
    try:
        registry.create_schema(installed_at_utc=_TEST_TIME)
        store.initialize_schema()
        yield registry, store, lambda: PostgresRuntimeExecutionQueryStore.from_dsn(dsn, schema=prefix + "_execution")
    finally:
        from psycopg import sql
        with psycopg.connect(dsn) as connection:
            for schema in (prefix + "_registry", prefix + "_execution"):
                connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


@pytest.mark.parametrize("failure", [None, "provider", "schema"])
def test_postgres_fresh_read_and_replay_preserve_candidate_policy(tmp_path, pg_stores, failure):
    env = _environment(tmp_path, pg=pg_stores)
    if failure == "provider":
        env.state["provider_error"] = True
    elif failure == "schema":
        env.state["payload"] = {"wrong": 3}
    result = _run(env)
    execution_id = result.module_run.workflow_execution_id
    before = env.query().load_trace(execution_id)
    replay = _run(env)
    after = env.query().load_trace(execution_id)
    assert before == after
    assert not after.records_of_type(ModuleOutputResolutionRecord)
    assert result.resolution is replay.resolution is None
    assert len(env.calls) == env.host.calls == 1
    assert result.attempts[0].status == ("completed" if failure is None else "failed")
    for row in after.records_of_type(ExecutionInputRef):
        content = env.query().load_content(execution_id, row.input_ref)
        assert hashlib.sha256(content.body).hexdigest() == row.input_sha256
    for output in result.outputs:
        content = env.query().load_content(execution_id, output.output_ref)
        assert hashlib.sha256(content.body).hexdigest() == output.output_sha256


def test_postgres_direct_single_repairs_committed_output_gap(tmp_path, pg_stores, monkeypatch):
    env = _environment(tmp_path, policy=OutputResolutionPolicy.DIRECT_SINGLE, pg=pg_stores)
    original = WorkflowModuleLedgerRecorder.record_output_resolution
    def crash(self, *, request, resolution):
        if resolution is not None:
            raise RuntimeError("crash after Attempt commit")
        return original(self, request=request, resolution=resolution)
    monkeypatch.setattr(WorkflowModuleLedgerRecorder, "record_output_resolution", crash)
    with pytest.raises(RuntimeError, match="after Attempt commit"):
        _run(env)
    execution_id = env.state["execution_id"]
    assert not env.query().load_trace(execution_id).records_of_type(ModuleOutputResolutionRecord)
    monkeypatch.setattr(WorkflowModuleLedgerRecorder, "record_output_resolution", original)
    repaired = _run(env)
    assert repaired.resolution.resolution_mode == "direct_single"
    assert repaired.resolution.resolution_status == "resolved"
    assert len(env.calls) == env.host.calls == 1
    assert len(env.query().load_trace(execution_id).records_of_type(ModuleOutputResolutionRecord)) == 1


def test_public_exports_share_one_entry():
    from agent_runtime.execution import run_registered_workflow_module as public
    assert public is run_registered_workflow_module


def test_execution_start_returns_native_receipt_without_changing_existing_writes(tmp_path):
    env = _environment(tmp_path)
    result = _run(env)
    trace = env.store.load_trace(result.module_run.workflow_execution_id)
    execution = trace.records_of_type(WorkflowExecutionRecord)[0]
    inputs = trace.records_of_type(ExecutionInputRef)
    recorder = WorkflowExecutionLedgerRecorder(WorkflowExecutionLedgerBinding(env.store, env.cell, env.contents))
    receipt = recorder.record_execution_start(execution=execution, inputs=inputs)
    assert type(receipt) is CommitReceipt and receipt.replayed is True
    assert env.store.load_trace(execution.workflow_execution_id) == trace
    with pytest.raises(ValueError):
        recorder.record_execution_start(execution=replace(execution, principal_id="changed"), inputs=inputs)
    fresh = InMemoryRuntimeExecutionRecordStore(execution_output_integrity_check=env.contents.contains)
    fresh_recorder = WorkflowExecutionLedgerRecorder(WorkflowExecutionLedgerBinding(fresh, env.cell, env.contents))
    first = fresh_recorder.record_execution_start(execution=execution, inputs=inputs)
    assert type(first) is CommitReceipt and first.replayed is False
    fresh_recorder.record_execution_start(execution=execution, inputs=inputs)  # Existing callers may ignore the result.
    assert fresh.load_trace(execution.workflow_execution_id).records_of_type(WorkflowExecutionRecord) == (execution,)


def test_postgres_concurrent_first_call_has_one_provider_entry(tmp_path, pg_stores, monkeypatch):
    first = _environment(tmp_path / "first", pg=pg_stores)
    second = _environment(tmp_path / "second", pg=pg_stores)
    store = pg_stores[1]
    original_read = store.load_trace
    initial_reads = threading.Barrier(2)
    provider_entered = threading.Event()
    release_provider = threading.Event()
    observed_thread = threading.local()
    provider_calls = []

    def read(execution_id):
        snapshot = original_read(execution_id)
        if not getattr(observed_thread, "initial_read", False):
            observed_thread.initial_read = True
            assert not snapshot.records
            initial_reads.wait(timeout=10)
        return snapshot

    monkeypatch.setattr(store, "load_trace", read)
    for label, env in (("first", first), ("second", second)):
        adapter = env.kwargs["adapters"].resolve(env.profile.executor_adapter_id, env.profile.executor_adapter_revision)
        original_invoke = adapter._invoker
        def invoke(*, _original=original_invoke, _label=label, **kwargs):
            provider_calls.append(_label)
            provider_entered.set()
            assert release_provider.wait(timeout=15)
            return _original(**kwargs)
        monkeypatch.setattr(adapter, "_invoker", invoke)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(_run, env) for env in (first, second)]
        try:
            assert provider_entered.wait(timeout=10)
            completed, pending = wait(futures, timeout=10, return_when=FIRST_COMPLETED)
            assert len(completed) == len(pending) == 1
            with pytest.raises(RuntimeError, match="recovery"):
                next(iter(completed)).result()
            assert len(provider_calls) == 1
        finally:
            release_provider.set()
        winner = next(iter(pending)).result(timeout=10)
    monkeypatch.setattr(store, "load_trace", original_read)
    assert winner.attempts[0].status == "completed"
    assert len(provider_calls) == 1
    before = first.query().load_trace(winner.module_run.workflow_execution_id)
    replay = _run(first)
    assert replay.outputs == winner.outputs and replay.resolution is None
    assert first.query().load_trace(winner.module_run.workflow_execution_id) == before
    assert not before.records_of_type(ModuleOutputResolutionRecord)
    assert len(provider_calls) == 1
