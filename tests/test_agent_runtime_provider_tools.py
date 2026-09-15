"""Exact node preparation and provider tools, without live model invocation."""
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_runtime import evaluate_local_workflow_module
from agent_runtime.contracts.registry_release_definition import WorkflowEdge
from agent_runtime.execution import InMemoryCellArtifactStore
from agent_runtime.execution.execution_local_invocation import _run_prepared_workflow_node
from agent_runtime.execution.execution_module_invocation import _prepare_registered_workflow_module
from agent_runtime.ledger import InMemoryModuleExecutionLedger
from agent_runtime.registry import (
    RuntimeReleaseBundle, compile_workflow_release, compile_execution_variant_policy_release,
    ExecutionVariantPolicyReleaseCandidate, ExecutionVariantProfileBindingCandidate,
)
from test_agent_runtime_native_structured_output import _operation_free_release, _StubInlineAdapter
from test_agent_runtime_workflow_release_compilation import _candidate


@pytest.fixture
def nodes(tmp_path):
    registry, module, profile = _operation_free_release(
        transport_kind="in_process_test", executor_adapter_id="stub_inline_executor", executor_adapter_revision="v1")
    candidate = _candidate()
    first = replace(candidate.nodes[0], module_release_ref=module.release_ref,
                    module_release_sha256=module.release_sha256)
    second = replace(first, node_id="second")
    workflow = compile_workflow_release(replace(candidate, nodes=(first, second), edges=(
        WorkflowEdge(first.node_id, "completed", second.node_id, False),
        WorkflowEdge(second.node_id, "completed", None, True)),
        authorization_manifest_document={"required_operation_ids": []}))
    selection = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id="node_selection", policy_version="v1", origin_kind="workflow",
        origin_release_ref=workflow.release_ref, origin_release_sha256=workflow.release_sha256,
        bindings=tuple(ExecutionVariantProfileBindingCandidate(node.node_id, profile.release_ref, profile.release_sha256)
                       for node in workflow.nodes)))
    registry.register_bundle(RuntimeReleaseBundle(workflows=(workflow,), execution_variant_policies=(selection,)))
    artifacts, ledger = InMemoryCellArtifactStore(), InMemoryModuleExecutionLedger()
    class Parent:
        active = True
        requests = []
        def require_active(self):
            if not self.active:
                raise PermissionError("parent closed")
        def check_node_scope(self, **scope):
            self.require_active()
            assert scope["workflow"] is workflow and scope["variant"] is selection
            assert scope["registry"] is registry and scope["artifact_host"] is artifacts and scope["ledger"] is ledger
            request = scope["request"]
            assert request.workflow_execution_id == "one_execution"
            assert request.dispatch_id == "dispatch_" + request.workflow_node_id
            assert Path(scope["workspace_root"]).parent == tmp_path
            self.requests.append(request)
    return SimpleNamespace(registry=registry, module=module, profile=profile, workflow=workflow,
                           selection=selection, artifacts=artifacts, ledger=ledger, parent=Parent(), root=tmp_path)


def prepare(env, node_id, **changes):
    return _prepare_registered_workflow_module(**dict(
        module_id=env.module.module_id, input_payload={"node": node_id}, idempotency_key="key_" + node_id,
        release_registry=env.registry, workflow=env.workflow, variant_policy=env.selection,
        artifact_host=env.artifacts, workflow_node_id=node_id, workflow_execution_id="one_execution",
        dispatch_id="dispatch_" + node_id, module_run_id="run_" + node_id, **changes))


def test_graph_preparation_preserves_execution_and_isolates_node_inputs(nodes):
    first = prepare(nodes, "produce")
    second = prepare(nodes, "second")
    assert first[0].workflow_execution_id == second[0].workflow_execution_id == "one_execution"
    assert first[0].module_run_id != second[0].module_run_id
    assert first[3].artifact_ref != second[3].artifact_ref
    assert first[4] is second[4] is None  # deterministic node has no fictitious model prompt
    assert prepare(nodes, "produce")[0] == first[0]


def test_shared_helper_executes_zero_operation_nodes_through_actual_kernel(nodes):
    adapter = _StubInlineAdapter(release_registry=nodes.registry, artifact_host=nodes.artifacts)
    records = []
    for node_id in ("produce", "second"):
        workspace = nodes.root / node_id
        workspace.mkdir()
        result, record = _run_prepared_workflow_node(
            registry=nodes.registry, workflow=nodes.workflow, selection=nodes.selection,
            node_id=node_id, input_payload={"node": node_id}, idempotency_key="key_" + node_id,
            workflow_execution_id="one_execution", dispatch_id="dispatch_" + node_id,
            module_run_id="run_" + node_id, artifact_host=nodes.artifacts, ledger=nodes.ledger,
            workspace_root=workspace, adapter=adapter, workflow_resources=nodes.parent)
        assert result.module_run.workflow_execution_id == record["workflow_execution_id"] == "one_execution"
        assert record["status"] == "completed" and record["output"] == {"value": "stub"}
        assert record["input_bindings"][0]["input_ref"]
        assert record["execution_log"]["attempts"]
        records.append(record)
    assert adapter.calls == 2 and nodes.parent.requests
    assert records[0]["attempt_id"] != records[1]["attempt_id"]
    assert not nodes.module.declared_operation_ids


@pytest.mark.parametrize("failure", ["missing_parent", "closed_parent", "wrong_dispatch", "wrong_node"])
def test_node_boundary_rejects_before_adapter(nodes, failure):
    adapter = _StubInlineAdapter(release_registry=nodes.registry, artifact_host=nodes.artifacts)
    workspace = nodes.root / "attempt"
    workspace.mkdir()
    if failure == "closed_parent":
        nodes.parent.active = False
    with pytest.raises((ValueError, PermissionError, AssertionError)):
        _run_prepared_workflow_node(
            registry=nodes.registry, workflow=nodes.workflow, selection=nodes.selection,
            node_id="missing" if failure == "wrong_node" else "second", input_payload={}, idempotency_key="key",
            workflow_execution_id="one_execution", dispatch_id="wrong" if failure == "wrong_dispatch" else "dispatch_second",
            module_run_id="run", artifact_host=nodes.artifacts, ledger=nodes.ledger,
            workspace_root=workspace, adapter=adapter,
            workflow_resources=None if failure == "missing_parent" else nodes.parent)
    assert adapter.calls == 0


class CallbackFactory:
    def __init__(self):
        from agent_runtime.invocation.invocation_tool_definition import ProviderToolDefinition
        self.definitions = (ProviderToolDefinition("inspect_note", "Return the supplied number plus one.",
            {"type": "object", "additionalProperties": False, "properties": {"value": {"type": "integer"}}, "required": ["value"]}),)
        self.calls, self.requests, self.closed = [], [], False

    def open_session(self, request):
        self.requests.append(request)
        factory = self
        class Session:
            definitions = factory.definitions
            observations = ()
            def invoke(self, name, payload, authorization):
                assert authorization is None
                factory.calls.append((name, payload))
                return {"answer": payload["value"] + 1}
            def validate_completion(self):
                pass
            def close(self):
                factory.closed = True
        session = Session()
        session.request = request
        return session


def callback_run(tmp_path, monkeypatch, factory, *, payload=None, missing_observation=False, sequence=None):
    from agent_runtime.execution import execution_local_invocation as local
    from agent_runtime.invocation import invocation_claude_cli_execution as claude
    from agent_runtime.invocation.invocation_local_command_mcp import exchange
    from test_agent_runtime_local_model_preparation import _resource_test_module
    from test_agent_runtime_claude_native_tools import _fake_cli, _init, _result, _tool_use, _tool_result
    root = _resource_test_module(tmp_path, tools=("inspect_note",))
    adapter_type = claude.ClaudeAdapter
    def process(**fields):
        fields["launch_guard"](lambda: None)
        args = fields["argv"]
        assert args[args.index("--tools") + 1] == ""
        config = json.loads(args[args.index("--mcp-config") + 1])
        if not config["mcpServers"]:
            events = [_init(()), _result(structured_output={"summary": "independent child"})]
            raw = "\n".join(json.dumps(event) for event in events).encode()
            for line in raw.decode().splitlines():
                assert fields["on_stdout_line"](line)
            result = subprocess.CompletedProcess(args, 0, raw.decode(), "")
            result.stdout_bytes, result.stderr_bytes = raw, b""
            return result
        assert '"name":"inspect_note"' in fields["prompt"]
        endpoint = Path(config["mcpServers"]["runtime_tools"]["args"][-2])
        init = _init(())
        tool = "mcp__runtime_tools__inspect_note"
        init["tools"].append(tool)
        init["mcp_servers"] = [{"name": "runtime_tools", "status": "connected"}]
        value = {"value": 41} if payload is None else payload
        events = [init]
        for index, selected_value in enumerate(sequence if sequence is not None else [value]):
            identity = "actual_provider_call" if index == 0 else "actual_provider_call_" + str(index)
            events.append(_tool_use(identity, tool, **selected_value))
            response = exchange(endpoint, {"method": "invoke", "tool_name": "inspect_note", "payload": selected_value})
            if not missing_observation:
                events.append(_tool_result(identity, failed=response["status"] != "completed", content=json.dumps(response)))
        events.append(_result(structured_output={"summary": "observed"}))
        lines = [json.dumps(event) for event in events]
        for line in lines:
            assert fields["on_stdout_line"](line)
        raw = "\n".join(lines).encode()
        result = subprocess.CompletedProcess(args, 0, raw.decode(), "")
        result.stdout_bytes, result.stderr_bytes = raw, b""
        return result
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **fields: adapter_type(**fields, process_runner=process))
    return evaluate_local_workflow_module(root, "summarize_note", input_payload={},
        cli_path=_fake_cli(tmp_path), tool_session_factory=factory)


def test_actual_callback_ipc_joins_provider_ids_and_frozen_prompt(tmp_path, monkeypatch):
    factory = CallbackFactory()
    record = callback_run(tmp_path, monkeypatch, factory)
    assert record["status"] == "completed", record["failure_detail"]
    assert factory.calls == [("inspect_note", {"value": 41})] and factory.closed
    log = record["execution_log"]
    assert log["complete"], log
    call, = log["tool_calls"]
    assert call["provider_tool_call_id"] == "actual_provider_call"
    assert call["response"]["result"] == {"answer": 42}
    assert call["tool_call_id"] == call["response"]["local_call_id"]
    assert factory.requests[0].self_test_binding_ref


def test_bad_callback_payload_is_denied_without_running_it(tmp_path, monkeypatch):
    factory = CallbackFactory()
    record = callback_run(tmp_path, monkeypatch, factory, payload={"value": 41, "target": "unapproved"})
    assert record["status"] == "completed" and not factory.calls
    call, = record["execution_log"]["tool_calls"]
    assert call["status"] == "failed" and call["response"]["allowed"] is False


def test_callback_permission_denial_can_be_followed_by_success(tmp_path, monkeypatch):
    factory = CallbackFactory()
    old_open = factory.open_session
    def open_session(request):
        session = old_open(request)
        old_invoke = session.invoke
        def invoke(name, payload, authorization):
            if payload["value"] == 0:
                raise PermissionError("fixture refused this value")
            return old_invoke(name, payload, authorization)
        session.invoke = invoke
        return session
    factory.open_session = open_session
    record = callback_run(tmp_path, monkeypatch, factory, sequence=[{"value": 0}, {"value": 41}])
    assert record["status"] == "completed", record["failure_detail"]
    assert record["execution_log"]["complete"]
    assert [item["status"] for item in record["execution_log"]["tool_calls"]] == ["failed", "completed"]
    assert factory.calls == [("inspect_note", {"value": 41})]


def test_callback_cannot_hide_actual_resource_definition_drift(tmp_path, monkeypatch):
    factory = CallbackFactory()
    old_open = factory.open_session
    def open_session(request):
        session = old_open(request)
        def invoke(name, payload, authorization):
            factory.definitions = ()
            raise PermissionError("ordinary refusal does not hide changed resources")
        session.invoke = invoke
        return session
    factory.open_session = open_session
    record = callback_run(tmp_path, monkeypatch, factory)
    assert record["status"] == "failed" and factory.closed
    assert record["provider_trace"]["local_callback_calls"][0]["response"]["status"] == "failed"


def test_unobserved_callback_result_cannot_be_a_complete_log(tmp_path, monkeypatch):
    record = callback_run(tmp_path, monkeypatch, CallbackFactory(), missing_observation=True)
    assert record["status"] == "completed"
    assert not record["execution_log"]["complete"]


def test_changed_session_definitions_prevent_provider_entry(tmp_path, monkeypatch):
    factory = CallbackFactory()
    original = factory.open_session
    def changed(request):
        session = original(request)
        session.definitions = ()
        return session
    factory.open_session = changed
    record = callback_run(tmp_path, monkeypatch, factory)
    assert record["status"] == "failed" and not factory.calls and factory.closed


def test_session_from_another_request_is_rejected(tmp_path, monkeypatch):
    factory = CallbackFactory()
    original = factory.open_session
    def wrong(request):
        session = original(request)
        session.request = replace(request, attempt_id="another_attempt")
        return session
    factory.open_session = wrong
    record = callback_run(tmp_path, monkeypatch, factory)
    assert record["status"] == "failed" and not factory.calls and factory.closed


@pytest.mark.parametrize("kind", ["missing", "empty", "extra", "schema"])
def test_factory_binding_errors_prevent_session_and_provider(tmp_path, monkeypatch, kind):
    factory = CallbackFactory()
    if kind == "empty":
        factory.definitions = ()
    elif kind == "extra":
        factory.definitions += (replace(factory.definitions[0], tool_name="another_tool"),)
    elif kind == "schema":
        factory.definitions = (replace(factory.definitions[0], input_schema={"type": "invalid"}),)
    with pytest.raises(Exception):
        callback_run(tmp_path, monkeypatch, None if kind == "missing" else factory)
    assert not factory.requests and not factory.calls


def test_callback_executes_only_the_prepared_child_through_kernel(tmp_path, monkeypatch):
    from agent_runtime.execution import execution_local_invocation as local
    from agent_runtime.invocation import invocation_claude_cli_execution as claude
    from test_agent_runtime_local_model_preparation import _resource_test_module
    from test_agent_runtime_claude_native_tools import _fake_cli
    child_root = _resource_test_module(tmp_path / "child_source", tools=())
    child, selection = local.prepare_local_workflow_module(child_root, "summarize_note")
    factory = CallbackFactory()
    factory.definitions = (replace(factory.definitions[0], input_schema={"type": "object", "additionalProperties": False,
        "properties": {"input_payload": {"type": "object", "additionalProperties": False}}, "required": ["input_payload"]}),)
    children = []
    old_open = factory.open_session
    def open_child_session(request):
        session = old_open(request)
        def invoke(name, payload, authorization):
            assert authorization is None and set(payload) == {"input_payload"}
            factory.calls.append((name, payload))
            workspace = tmp_path / "child_attempt"
            workspace.mkdir()
            artifacts, ledger = InMemoryCellArtifactStore(), InMemoryModuleExecutionLedger()
            adapter = claude.ClaudeAdapter(release_registry=child.registry, artifact_host=artifacts,
                workspace_root=workspace, cli_path=_fake_cli(tmp_path / "child_source"))
            run, record = local._run_prepared_workflow_node(registry=child.registry, workflow=child.release,
                selection=selection, input_payload=payload["input_payload"], idempotency_key="child_requested",
                artifact_host=artifacts, ledger=ledger, workspace_root=workspace, adapter=adapter)
            children.append((run, record))
            return {"child_execution_id": record["workflow_execution_id"], "output": record["output"]}
        session.invoke = invoke
        return session
    factory.open_session = open_child_session
    # Preparing the child and providing a factory performs no child execution.
    assert children == []
    result = callback_run(tmp_path / "parent", monkeypatch, factory, payload={"input_payload": {}})
    assert result["status"] == "completed", result["failure_detail"]
    assert len(children) == len(factory.calls) == 1
    child_run, child_record = children[0]
    assert child_record["status"] == "completed", child_record["failure_detail"]
    assert child_record["output"] == {"summary": "independent child"}
    assert child_record["workflow_release_sha256"] == child.release.release_sha256
    assert child_record["workflow_execution_id"] != result["workflow_execution_id"]
    assert child_record["attempt_id"] != result["attempt_id"]
    assert child_record["execution_log"]["complete"] is None
    assert child_record["execution_log"]["attempts"][0]["provider_log"]["raw_streams"]
    from agent_runtime import parse_cli_log
    initialization = next(row["event"] for row in parse_cli_log(child_record["provider_trace"])["events"]
                          if row["event"].get("subtype") == "init")
    assert initialization["mcp_servers"] == []
    call, = result["execution_log"]["tool_calls"]
    assert call["response"]["result"]["child_execution_id"] == child_record["workflow_execution_id"]


def test_multi_agent_preparation_reuses_same_model_converter_and_loads_once(tmp_path, monkeypatch):
    from agent_runtime.execution import execution_local_invocation as local
    from agent_runtime import RuntimeModulePlugin, register_runtime_module_plugin
    from agent_runtime.registry import RuntimeReleaseRegistry
    from test_agent_runtime_workflow_authoring import _workflow_export
    exported = _workflow_export()
    root = tmp_path / "root"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin("graph", "v1", exported.origin_bundle), root=root)
    load = local.load_runtime_registration
    calls = []
    def observe(*args):
        calls.append(args)
        return load(*args)
    monkeypatch.setattr(local, "load_runtime_registration", observe)
    saved, variant = local.prepare_local_workflow(root, exported.workflow_release.workflow_id)
    assert len(calls) == 1
    assert saved.release == exported.workflow_release
    assert {item["position_id"] for item in variant.policy_document()["bindings"]} == {"first_review", "second_review"}
    profiles = saved.registry.snapshot().execution_profiles
    assert len(profiles) == 1 and profiles[0].executor_adapter_revision == "v3"
    with pytest.raises(ValueError, match="one Workflow"):
        local.prepare_local_workflow_module(root, exported.workflow_release.workflow_id)


def test_single_node_preparation_identity_is_unchanged_between_entries(tmp_path):
    from agent_runtime.execution import execution_local_invocation as local
    from test_agent_runtime_local_model_preparation import _resource_test_module
    root = _resource_test_module(tmp_path, tools=())
    one, a = local.prepare_local_workflow_module(root, "summarize_note")
    graph, b = local.prepare_local_workflow(root, "summarize_note")
    assert one.release == graph.release and a == b


@pytest.mark.parametrize("user", [True, False])
def test_process_cancellation_from_other_thread_stops_actual_process(tmp_path, user):
    from agent_runtime.invocation.invocation_process_execution import run_cli_process, CliProcessInterrupted, CliProcessError
    event = threading.Event()
    timer = threading.Timer(0.2, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(CliProcessInterrupted if user else CliProcessError) as caught:
            run_cli_process(argv=[sys.executable, "-I", "-B", "-c", "import time;print('started',flush=True);time.sleep(15)"],
                prompt="", cwd=tmp_path, timeout_seconds=20, environment={},
                **{("user_cancel_requested" if user else "cancel_requested"): event.is_set})
        assert time.monotonic() - started < 5
        assert caught.value.stdout_bytes == b"started\n"
        assert caught.value.stop_reason == ("cancelled" if user else "resource_closed")
    finally:
        timer.join()


@pytest.mark.parametrize("user", [True, False])
def test_node_helper_cancels_actual_child_process_with_distinct_reason(tmp_path, user):
    from agent_runtime.execution import execution_local_invocation as local
    from agent_runtime.invocation.invocation_claude_cli_execution import ClaudeAdapter
    from test_agent_runtime_local_model_preparation import _resource_test_module
    from test_agent_runtime_claude_native_tools import _init
    root = _resource_test_module(tmp_path / "definition", tools=())
    saved, selection = local.prepare_local_workflow_module(root, "summarize_note")
    marker = tmp_path / "child_started"
    executable = tmp_path / "local_cli"
    executable.write_text(f"#!{sys.executable}\n" + "import json,os,sys,time\n"
        + "if '--help' in sys.argv or '--version' in sys.argv:\n"
        + " print('fixture --safe-mode --restricted --tools --settings --effort --json-schema --strict-mcp-config --add-dir')\n"
        + "else:\n"
        + f" open({str(marker)!r},'w').write(str(os.getpid()))\n"
        + f" print({json.dumps(_init(()))!r},flush=True)\n"
        + " time.sleep(30)\n")
    executable.chmod(0o700)
    workspace = tmp_path / "child_workspace"
    workspace.mkdir()
    artifacts, ledger = InMemoryCellArtifactStore(), InMemoryModuleExecutionLedger()
    adapter = ClaudeAdapter(release_registry=saved.registry, artifact_host=artifacts,
                            workspace_root=workspace, cli_path=executable)
    closed = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(local._run_prepared_workflow_node, registry=saved.registry, workflow=saved.release,
            selection=selection, input_payload={}, idempotency_key="cancelled_child", artifact_host=artifacts,
            ledger=ledger, workspace_root=workspace, adapter=adapter,
            **{("user_cancel_requested" if user else "resource_cancel_requested"): closed.is_set})
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists(), "actual local child process did not start"
        closed.set()
        run, record = pending.result(timeout=5)
    assert record["status"] == ("cancelled" if user else "failed"), record["failure_detail"]
    assert record["provider_trace"]["stop_reason"] == ("cancelled" if user else "resource_closed")
    assert record["provider_trace"]["raw_streams"]["stdout"]["data"]
    assert run.attempts[0].failure_detail_ref
    if user:
        assert record["failure_detail"]["failure_code"] == "claude_cli_interrupted"
    else:
        assert record["failure_detail"].get("failure_code") != "claude_cli_interrupted"
