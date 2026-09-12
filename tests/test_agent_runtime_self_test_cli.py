"""The no-PG path uses the real preparation/kernel/Adapter with stub transport."""
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from threading import Event, Thread

import pytest

from agent_runtime import evaluate_local_workflow_module
from agent_runtime.execution import execution_local_invocation as local
from agent_runtime.execution.execution_module_invocation import run_workflow_module
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.contracts.invocation_adapter_definition import AuthorizedAgentExecutionRequest
from agent_runtime.testing.execution_local_evaluation import main
from test_agent_runtime_claude_native_tools import _fake_cli, _init, _result
from test_agent_runtime_reviewer_registration_cli import _source, _register, _files, MODULE_ID


@pytest.fixture
def environment(tmp_path, monkeypatch):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    executable = _fake_cli(tmp_path)
    calls, handles = [], []
    output = {"verdict": "passed", "check_results": [], "findings": [], "safe_next_step": "done"}
    adapter_type = claude.ClaudeCliNativeToolsModuleExecutor
    resources_type = local.ModuleSelfTestResources
    controls = {}
    def resources(**kwargs):
        handle = resources_type(**kwargs)
        handles.append((handle, kwargs))
        return handle
    def process(**kwargs):
        guard = kwargs.get("launch_guard", lambda launch: launch())
        guard(lambda: calls.append(kwargs))
        if "during_provider" in controls:
            controls["during_provider"]()
        argv = kwargs["argv"]
        for event in ({**_init(), "model": argv[argv.index("--model")+1]},
                      {"type":"assistant", "message":{"model":controls.get("response_model", argv[argv.index("--model")+1].removesuffix("[1m]")), "content":[]}},
                      _result(structured_output=output, **controls.get("result", {}))):
            assert kwargs["on_stdout_line"](json.dumps(event))
        return subprocess.CompletedProcess(argv, 0, "", "")
    def make_adapter(**kw):
        adapter = adapter_type(**kw, process_runner=process)
        execute = adapter.execute
        def invoke(request, host):
            if "before_execute" in controls:
                controls["before_execute"](adapter, request, host)
            return execute(request, host)
        adapter.execute = invoke
        return adapter
    monkeypatch.setattr(claude, "ClaudeCliNativeToolsModuleExecutor", make_adapter)
    monkeypatch.setattr(local, "ModuleSelfTestResources", resources)
    temporary_directory = local.tempfile.TemporaryDirectory
    monkeypatch.setattr(local.tempfile, "TemporaryDirectory",
        lambda *args, **kw: temporary_directory(*args, **{**kw, "dir": tmp_path}))
    for name in ("PLATFORM_DATABASE_URL", "AGENT_RUNTIME_TEST_DATABASE_URL", "DDM_REVIEW_EXECUTOR"):
        monkeypatch.delenv(name, raising=False)
    # Any accidental attempt to connect is a test failure, not a skipped test.
    from agent_runtime.registry import PostgresRuntimeReleaseStore
    from agent_runtime.ledger import PostgresRuntimeExecutionRecordStore
    for store in (PostgresRuntimeReleaseStore, PostgresRuntimeExecutionRecordStore):
        monkeypatch.setattr(store, "from_dsn", lambda *a, **kw: pytest.fail("self-test cannot connect to PG"))
    return root, executable, calls, handles, controls, output


def invoke(environment, **kwargs):
    root, executable, *_ = environment
    return evaluate_local_workflow_module(root, MODULE_ID, input_payload={}, cli_path=executable, **kwargs)


def test_self_test_runs_without_pg_or_product_authorization(environment):
    root, _, calls, handles, _, _ = environment
    before = _files(root)
    record = invoke(environment)
    assert record["status"] == "completed", record["failure_detail"]
    assert record["persistence"] == "not_requested" and len(calls) == 1
    assert record["workflow_execution_id"] == record["execution_trace"]["module_run"]["workflow_execution_id"]
    assert record["attempt_id"] == record["execution_trace"]["attempts"][0]["attempt_id"]
    assert record["usage"] == record["execution_trace"]["attempts"][0]["usage"]
    assert record["workflow_release_ref"].startswith("runtime-workflow:")
    assert _files(root) == before
    assert "entitlement" not in json.dumps(record)
    resources = handles[0][0]
    assert not resources._workspace.exists()
    with pytest.raises(PermissionError, match="closed"):
        resources.require_active()


def test_new_model_is_new_call_not_a_saved_binding(environment):
    root, _, calls, *_ = environment
    before = _files(root)
    a = invoke(environment, model_id="model-a")
    b = invoke(environment, model_id="model-b")
    assert a["status"] == b["status"] == "completed"
    assert a["workflow_release_sha256"] == b["workflow_release_sha256"]
    assert a["execution_profile_sha256"] != b["execution_profile_sha256"]
    assert a["workflow_execution_id"] != b["workflow_execution_id"]
    assert _files(root) == before and len(calls) == 2


def test_closed_resources_reject_late_output(environment):
    _, _, _, handles, controls, _ = environment
    controls["during_provider"] = lambda: handles[0][0].close()
    record = invoke(environment)
    assert record["status"] == "failed"
    assert record["output"] is None
    assert record["usage"]["input_tokens"] == 7 and record["usage"]["output_tokens"] == 3
    assert record["provider_trace"]["result"]["structured_output"] == environment[-1]
    assert record["failure_detail"]["failure_code"] == "self_test_resources_unavailable"


def test_wrong_transport_stops_before_provider(environment):
    with pytest.raises(ValueError, match="Unsupported Reviewer model transport"):
        invoke(environment, transport_kind="codex_cli")
    assert environment[2] == []


def test_self_test_rejects_actual_model_mismatch(environment):
    environment[4]["response_model"] = "claude-sonnet-4-6"
    record = invoke(environment)
    assert record["status"] == "failed" and record["output"] is None
    assert record["failure_detail"]["failure_code"] == "claude_cli_model_identity_mismatch"
    assert record["provider_trace"]["response_models"] == ["claude-sonnet-4-6"]
    assert record["usage"]["input_tokens"] == 7


def test_cli_executable_permission_is_not_a_closed_test_resource(environment):
    environment[1].chmod(0o600)
    record = invoke(environment)
    assert record["status"] == "failed" and environment[2] == []
    assert record["failure_class"] == "dependency_unavailable"
    assert record["failure_detail"]["failure_code"] == "ADAPTER_BINDING_UNAVAILABLE"
    assert "Permission denied" in record["failure_detail"]["message"]


def test_cli_success_non_pass_and_execution_failure(environment, tmp_path, capsys):
    root, executable, calls, _, controls, output = environment
    payload = tmp_path / "input.json"
    payload.write_text("{}")
    args = ["--root", str(root), "--workflow", MODULE_ID, "--input", str(payload),
            "--cli-path", str(executable)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "completed"
    output.update(verdict="non_pass", findings=[{"finding_id":"fix_one", "severity":"fix",
        "evidence":{"source_ref":"candidate", "locator":"section_one", "observation":"Missing requirement"},
        "requirement":"Include the requirement", "impact":"Incomplete candidate",
        "accountable_owner_ref":"subject_owner", "required_change":"Revise the candidate"}])
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["output"]["verdict"] == "non_pass"
    controls["result"] = {"is_error": True}
    assert main(args) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    before = len(calls)
    with pytest.raises(SystemExit) as failure:
        main([*args, "--save-to-pg"])
    assert failure.value.code == 2 and len(calls) == before


def test_cli_interrupt_is_not_a_verdict(monkeypatch, tmp_path, capsys):
    from agent_runtime.testing import execution_local_evaluation as cli
    def interrupted(*args, **kw):
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "evaluate_local_workflow_module", interrupted)
    payload = tmp_path / "input.json"
    payload.write_text("{}")
    assert main(["--root", str(tmp_path), "--workflow", "test", "--input", str(payload)]) == 130
    captured = capsys.readouterr()
    assert not captured.out and json.loads(captured.err)["error_type"] == "KeyboardInterrupt"


@pytest.mark.parametrize("field,value", [
    ("self_test_binding_ref", "artifact:forged"), ("self_test_binding_sha256", "1"*64),
    ("input_closure_sha256", "1"*64), ("execution_profile_ref", "execution-profile:other@v1"),
    ("workflow_execution_id", "other_execution"), ("attempt_id", "other_attempt"),
])
def test_forged_adapter_requests_cannot_use_live_host(environment, field, value):
    _, _, _, _, controls, _ = environment
    def before(adapter, request, host):
        fields = asdict(request)
        fields.pop("request_sha256")
        fields["authorized_inputs"] = request.authorized_inputs
        fields[field] = value
        forged = AuthorizedAgentExecutionRequest.build(**fields)
        with pytest.raises(PermissionError):
            host.validate_self_test_binding(forged, adapter=adapter,
                artifact_host=adapter._artifacts, workspace_root=adapter._workspace_root)
    controls["before_execute"] = before
    assert invoke(environment)["status"] == "completed"


@pytest.mark.parametrize("port", ["ledger", "artifact_host", "adapter", "workspace_root", "request", "authority"])
def test_resources_cannot_cross_requests_or_ports(environment, port):
    from agent_runtime.execution import AgentExecutionAdapterRegistry, InMemoryCellArtifactStore
    from agent_runtime.ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger
    _, _, _, handles, controls, _ = environment
    def before(adapter, canonical, host):
        resource, config = handles[0]
        registry = AgentExecutionAdapterRegistry()
        registry.register(adapter)
        if port in {"adapter", "workspace_root"}:
            with pytest.raises(PermissionError):
                host.validate_self_test_binding(canonical,
                    adapter=object() if port == "adapter" else adapter,
                    artifact_host=config["artifact_host"],
                    workspace_root=Path("/") if port == "workspace_root" else config["workspace_root"])
            return
        request = config["request"]
        if port == "request":
            fields = {key: value for key, value in asdict(request).items()
                      if key not in {"input_closure_sha256", "request_sha256"}}
            fields.update(request_id="another_request", inputs=request.inputs, variants=request.variants)
            request = type(request).build(**fields)
        with pytest.raises((PermissionError, ValueError)):
            run_workflow_module(request, release_registry=config["registry"], adapters=registry,
                artifact_host=InMemoryCellArtifactStore() if port == "artifact_host" else config["artifact_host"],
                ledger=InMemoryModuleExecutionLedger() if port == "ledger" else config["ledger"],
                authority=object() if port == "authority" else None, self_test=resource)
    controls["before_execute"] = before
    assert invoke(environment)["status"] == "completed"


def test_self_test_fields_do_not_change_legacy_request_hash(environment):
    _, _, _, _, controls, _ = environment
    def before(adapter, request, host):
        values = asdict(request)
        values.pop("request_sha256")
        values.pop("self_test_binding_ref")
        values.pop("self_test_binding_sha256")
        raw = json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        values["authorized_inputs"] = request.authorized_inputs
        old = AuthorizedAgentExecutionRequest.build(**values)
        assert old.request_sha256 == hashlib.sha256(raw).hexdigest()
        mixed = {**values, "self_test_binding_ref": request.self_test_binding_ref,
            "self_test_binding_sha256": request.self_test_binding_sha256,
            "execution_authorization_binding_ref": "external:binding", "execution_authorization_binding_sha256": "1"*64}
        with pytest.raises(ValueError, match="cannot be mixed"):
            AuthorizedAgentExecutionRequest.build(**mixed)
        with pytest.raises(PermissionError, match="self-test"):
            adapter.execute(request, object())
    controls["before_execute"] = before
    # Call the class implementation to avoid re-entering the fixture interceptor.
    original = controls["before_execute"]
    def once(*args):
        controls.pop("before_execute")
        return original(*args)
    controls["before_execute"] = once
    assert invoke(environment)["status"] == "completed"


def test_installed_console_executes_without_checkout_or_pg(tmp_path):
    from test_agent_runtime_packaging_boundary import _build_runtime_wheel
    wheel = _build_runtime_wheel(tmp_path / "build")
    installed = tmp_path / "installed"
    subprocess.run([sys.executable, "-m", "pip", "install", "--no-index", "--no-deps", "--no-cache-dir",
        "--disable-pip-version-check", "--target", str(installed), str(wheel)],
        cwd=tmp_path, check=True, capture_output=True, text=True)
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "registered"
    _register(root, source)
    before = _files(root)
    executable = tmp_path / "provider_fixture"
    output = {"verdict": "passed", "check_results": [], "findings": [], "safe_next_step": "done"}
    events = " ".join(shlex.quote(json.dumps(event)) for event in (_init(), _result(structured_output=output)))
    executable.write_text("#!/bin/sh\ncase \"$1\" in\n--version|--help) "
        "printf '%s\\n' '2.1.999 --safe-mode --restricted --tools --settings --effort --strict-mcp-config --json-schema';;\n"
        "*) /bin/cat >/dev/null; printf '%s\\n' " + events + ";;\nesac\n")
    executable.chmod(0o700)
    payload = tmp_path / "input.json"
    payload.write_text("{}")
    child = {key:value for key,value in os.environ.items()
             if key not in {"PLATFORM_DATABASE_URL", "AGENT_RUNTIME_TEST_DATABASE_URL", "DDM_REVIEW_EXECUTOR"}}
    child["PYTHONPATH"] = str(installed)
    argv = [str(installed/"bin/agent-runtime-evaluate"), "--root", str(root), "--workflow", MODULE_ID,
            "--input", str(payload), "--cli-path", str(executable)]
    completed = subprocess.run(argv, cwd=tmp_path, env=child, capture_output=True, text=True, timeout=60)
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    record = json.loads(completed.stdout)
    assert record["output"] == output and record["persistence"] == "not_requested"
    after = _files(root)
    assert all(after[path] == content for path, content in before.items())
    assert set(after) - set(before) == {".runtime/setup.json", *(
        f"{host}/skills/{name}/SKILL.md" for host in (".agents", ".claude")
        for name in ("agent-runtime-registration", "agent-runtime-evaluation"))}
    refused = subprocess.run([*argv, "--save-to-pg"], cwd=tmp_path, env=child, capture_output=True, text=True)
    assert refused.returncode == 2 and not refused.stdout


@pytest.mark.parametrize("operation", ["launch", "read"])
def test_close_is_ordered_after_an_admitted_effect(environment, monkeypatch, operation):
    _, _, calls, handles, controls, _ = environment
    def before(adapter, request, host):
        resources, _ = handles[0]
        closing, closed = Event(), Event()
        def close():
            closing.set()
            resources.close()
            closed.set()
        worker = Thread(target=close)
        def effect():
            worker.start()
            assert closing.wait(2)
            assert not closed.is_set(), "close must not pass an admitted effect"
            return "effect"
        if operation == "launch":
            assert host.guard_self_test_launch(request, effect, adapter=adapter,
                artifact_host=adapter._artifacts, workspace_root=adapter._workspace_root) == "effect"
        else:
            original = adapter._artifacts.read_bytes
            input_ref = request.authorized_inputs[0].input_ref
            def read(ref, digest):
                if ref == input_ref:
                    effect()
                return original(ref, digest)
            monkeypatch.setattr(adapter._artifacts, "read_bytes", read)
            assert host.read_authorized_input(request.authorized_inputs[0].local_handle)
        worker.join(2)
        assert closed.is_set()
        with pytest.raises(PermissionError):
            host.guard_self_test_launch(request, lambda: pytest.fail("late launch"), adapter=adapter,
                artifact_host=adapter._artifacts, workspace_root=adapter._workspace_root)
        with pytest.raises(PermissionError):
            host.read_authorized_input(request.authorized_inputs[0].local_handle)
    controls["before_execute"] = before
    record = invoke(environment)
    assert record["status"] == "failed" and calls == []
    assert record["failure_detail"]["disposition"] == "self_test_resources_unavailable"
