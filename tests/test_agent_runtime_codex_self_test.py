"""Public Codex evaluation with real temporary resources and no Product authority."""
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Event, Thread

import pytest

from agent_runtime import evaluate_local_workflow_module
from agent_runtime import Module, RuntimeModulePlugin, prepare_local_workflow_module, register_runtime_module_plugin
from agent_runtime.execution import execution_local_invocation as local
from agent_runtime.execution import execution_module_invocation as kernel
from agent_runtime.contracts.invocation_adapter_definition import AuthorizedAgentExecutionRequest
from agent_runtime.invocation import invocation_codex_module_invocation as codex
from agent_runtime.registry import RuntimeReleaseRegistry
from agent_runtime.testing.conformance_local_evaluation import main
from test_agent_runtime_module_authoring import _task_project, _requirements, SKILL_ID
from test_agent_runtime_reviewer_registration_cli import _files


@pytest.fixture
def environment(tmp_path, monkeypatch):
    source = _task_project(tmp_path / "source", module_id="summarize_note")
    requirements = _requirements(execution_mode="tool_free", tool_policy=(),
        attempt_workspace_policy="none", timeout_seconds=30, max_attempts=1)
    module = Module.from_registration(source, skill_id=SKILL_ID, module_id="summarize_note",
                                     execution_requirements=requirements)
    workflow = module.to_workflow(module.export(module_version="v1")).export()
    root = tmp_path / "host"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        "codex_self_test", "v1", workflow.origin_bundle), root=root)
    credential_root = tmp_path / "host_login"
    credential_root.mkdir()
    (credential_root / "auth.json").write_text('{"synthetic_secret":"never-pass-to-model"}')
    monkeypatch.setenv("CODEX_HOME", str(credential_root))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    resources, calls, controls, adapters = [], [], {}, []
    real_resources, adapter_type = local.ModuleSelfTestResources, codex.CodexCliModuleExecutor
    def create_resources(**fields):
        resource = real_resources(**fields)
        resources.append(resource)
        return resource
    monkeypatch.setattr(local, "ModuleSelfTestResources", create_resources)
    def adapter(**fields):
        instance = adapter_type(**fields)
        original = instance.execute
        def execute(request, host):
            if controls.get("before_execute"):
                controls["before_execute"](instance, request, host)
            return original(request, host)
        instance.execute = execute
        adapters.append(instance)
        return instance
    monkeypatch.setattr(codex, "CodexCliModuleExecutor", adapter)
    # Preserve the class-owned binding facts used by pure model preparation.
    adapter.executor_adapter_id = adapter_type.executor_adapter_id
    adapter.executor_adapter_revision = adapter_type.executor_adapter_revision
    def process(**fields):
        assert fields["environment"]["CODEX_HOME"] != str(credential_root)
        assert "never-pass-to-model" not in fields["prompt"] + json.dumps(fields["argv"])
        assert callable(fields.get("launch_guard"))
        if "--version" in fields["argv"]:
            fields["launch_guard"](lambda: None)
            result = subprocess.CompletedProcess(fields["argv"], 0, "codex-cli 0.153.4\n", "")
            result.stdout_bytes, result.stderr_bytes = b"codex-cli 0.153.4\n", b""
            return result
        if controls.get("before_launch"):
            controls["before_launch"]()
        fields["launch_guard"](lambda: calls.append(fields))
        if controls.get("during_provider"):
            controls["during_provider"]()
        events = [
            {"type": "thread.started", "thread_id": "synthetic"}, {"type": "turn.started"},
            *controls.get("tool_events", []),
            {"type": "item.completed", "item": {"type": "agent_message", "id": "answer",
                "text": json.dumps({"summary": controls.get("summary", "non_pass")})}},
            {"type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 3,
                "cached_input_tokens": 2, "cache_write_input_tokens": 1}},
        ]
        raw = b"\n".join(json.dumps(row).encode() for row in events) + b"\n"
        result = subprocess.CompletedProcess(fields["argv"], 0, raw.decode(), "")
        result.stdout_bytes, result.stderr_bytes = raw, b""
        return result
    monkeypatch.setattr(codex, "run_cli_process", process)
    monkeypatch.setattr(kernel, "_authorize_model_attempt", lambda **kw: pytest.fail("No Product authorization in self-test"))
    return root, requirements, resources, calls, controls, adapters


def invoke(environment, **changes):
    return evaluate_local_workflow_module(environment[0], "summarize_note", input_payload={},
        transport_kind="codex_cli", model_id="gpt-6-astra", reasoning_profile="xhigh",
        cli_path=Path(sys.executable), **changes)


def test_public_codex_evaluation_uses_live_resources_and_same_log(environment):
    root, _, resources, calls, _, _ = environment
    before = _files(root)
    record = invoke(environment)
    assert record["status"] == "completed", record["failure_detail"]
    assert record["output"] == {"summary": "non_pass"}
    assert record["persistence"] == "not_requested" and record["managed_runtime"]
    assert len(calls) == 1 and _files(root) == before
    assert record["self_test_binding"]["adapter"]["adapter_revision"] == "v4"
    assert "entitlement" not in json.dumps(record) and "never-pass-to-model" not in json.dumps(record)
    assert record["execution_log"]["complete"]
    assert record["execution_log"]["attempts"][0]["provider_log"] == record["provider_trace"]
    assert record["usage"] == record["execution_trace"]["attempts"][0]["usage"]
    assert not resources[0]._workspace.exists()
    assert not Path(calls[0]["environment"]["CODEX_HOME"]).exists()


@pytest.mark.parametrize("field,value", [("model_id", None), ("reasoning_profile", None), ("model_id", ""), ("reasoning_profile", "")])
def test_codex_requires_explicit_model_and_effort_without_default_or_writes(environment, field, value):
    root, _, _, calls, *_ = environment
    before = _files(root)
    values = {"model_id": "gpt-6-astra", "reasoning_profile": "xhigh", field: value}
    with pytest.raises(ValueError, match="requires explicit"):
        prepare_local_workflow_module(root, "summarize_note", transport_kind="codex_cli", **values)
    assert not calls and _files(root) == before


@pytest.mark.parametrize("effort", ["two words", "line\nbreak", "quoted\"value"])
def test_codex_invalid_nonempty_effort_precedes_store_resources_and_process(environment, monkeypatch, effort):
    from agent_runtime.invocation import invocation_codex_environment as private_state
    root, _, resources, calls, _, adapters = environment
    before = _files(root)
    touched = []
    def forbidden(*args, **kwargs):
        touched.append(True)
        pytest.fail("invalid effort reached a store, resource or process")
    class Store:
        register_bundle = forbidden
        load_release_registry = forbidden
    monkeypatch.setattr(tempfile, "TemporaryDirectory", forbidden)
    monkeypatch.setattr(private_state, "prepare_codex_environment", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    selection = dict(transport_kind="codex_cli", model_id="gpt-6-astra", reasoning_profile=effort)
    with pytest.raises(ValueError, match="effort|reasoning_profile"):
        prepare_local_workflow_module(root, "summarize_note", release_store=Store(), **selection)
    with pytest.raises(ValueError, match="effort|reasoning_profile"):
        evaluate_local_workflow_module(root, "summarize_note", input_payload={},
            cli_path=Path(sys.executable), **selection)
    assert touched == resources == calls == adapters == []
    assert _files(root) == before


@pytest.mark.parametrize("effort", ["max", "ultra", "persistent", "model_custom"])
def test_codex_preparation_preserves_current_cli_effort_syntax(environment, effort):
    root, _, resources, calls, _, adapters = environment
    before = _files(root)
    saved, variant = prepare_local_workflow_module(root, "summarize_note", transport_kind="codex_cli",
        model_id="explicit-model", reasoning_profile=effort)
    binding, = variant.policy_document()["bindings"]
    profile = saved.registry.get_execution_profile(binding["execution_profile_release_ref"],
        binding["execution_profile_release_sha256"])
    assert profile.reasoning_profile == effort and profile.model_id == "explicit-model"
    assert resources == calls == adapters == [] and _files(root) == before


def test_public_codex_log_preserves_orphan_progress_without_inventing_execution(environment):
    root, _, _, calls, controls, _ = environment
    before = _files(root)
    event = {"type": "item.updated", "item": {"type": "command_execution", "id": "c",
        "command": "unknown", "aggregated_output": "partial", "status": "in_progress", "exit_code": None}}
    controls["tool_events"] = [event]
    record = invoke(environment)
    assert record["status"] == "completed", record["failure_detail"]
    assert len(calls) == 1 and _files(root) == before
    log = record["execution_log"]
    assert not log["complete"]
    attempt, = log["attempts"]
    assert "unpaired_tool_call:c" in attempt["issues"]
    row, = attempt["tool_calls"]
    assert row["status"] == "incomplete" and row["request"] is None and row["response"] is None
    from agent_runtime import parse_cli_log
    assert "tool_log" not in attempt["provider_log"]
    view = parse_cli_log(attempt["provider_log"])
    assert event in [entry["event"] for entry in view["events"]]


def test_public_codex_cli_resolves_relative_executable_before_actual_attempt_cwd(environment, tmp_path, monkeypatch, capsys):
    from agent_runtime import setup_runtime
    from agent_runtime.invocation.invocation_process_execution import run_cli_process
    monkeypatch.setattr(codex, "run_cli_process", run_cli_process)
    root, _, resources, _, _, _ = environment
    executable = root / "bin" / "codex"
    executable.parent.mkdir()
    launched = tmp_path / "launched.jsonl"
    events = [
        {"type": "thread.started", "thread_id": "synthetic"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "id": "answer",
            "text": json.dumps({"summary": "relative executable ran"})}},
        {"type": "turn.completed", "usage": {"input_tokens": 8, "output_tokens": 3}},
    ]
    executable.write_text(f"#!{sys.executable}\n" + f'''
import json, os, sys
with open({str(launched)!r}, "a") as output:
    output.write(json.dumps({{"argv": sys.argv, "cwd": os.getcwd()}}) + "\\n")
if "--version" in sys.argv:
    print("codex-cli synthetic-local-process")
else:
    assert "exec" in sys.argv and sys.stdin.read()
    for event in {events!r}:
        print(json.dumps(event))
''')
    executable.chmod(0o700)
    payload = tmp_path / "input.json"
    payload.write_text("{}")
    monkeypatch.chdir(root)
    # Include normal setup before measuring invocation-only mutation.
    setup_runtime(root)
    before = _files(root)
    exit_code = main(["--root", str(root), "--workflow", "summarize_note", "--input", str(payload),
        "--transport", "codex_cli", "--model", "gpt-6-astra", "--effort", "xhigh",
        "--cli-path", "./bin/codex"])
    captured = capsys.readouterr()
    assert exit_code == 0, captured.out + captured.err
    record = json.loads(captured.out)
    assert record["output"] == {"summary": "relative executable ran"}
    processes = [json.loads(line) for line in launched.read_text().splitlines()]
    assert len(processes) == 2 and "--version" in processes[0]["argv"]
    assert all(Path(process["argv"][0]) == executable.resolve() for process in processes)
    assert all(Path(process["cwd"]) != root for process in processes)
    assert record["execution_log"]["complete"] and _files(root) == before
    assert not resources[0]._workspace.exists()


def test_codex_model_change_keeps_definition_and_changes_only_execution_selection(environment):
    root = environment[0]
    before = _files(root)
    selections = [prepare_local_workflow_module(root, "summarize_note", transport_kind="codex_cli",
        model_id=model, reasoning_profile="xhigh") for model in ("model-a", "model-b")]
    assert selections[0][0].release == selections[1][0].release
    profiles = [saved.registry.snapshot().execution_profiles[0] for saved, _ in selections]
    assert profiles[0].release_sha256 != profiles[1].release_sha256
    assert all(profile.tool_policy == () and profile.model_defaults_version is None for profile in profiles)
    assert _files(root) == before


@pytest.mark.parametrize("phase", ["before_launch", "during_provider"])
def test_codex_closed_resources_prevent_launch_or_accepting_late_output(environment, phase):
    _, _, resources, calls, controls, _ = environment
    controls[phase] = lambda: resources[0].close()
    record = invoke(environment)
    assert record["status"] == "failed" and record["output"] is None
    assert record["failure_detail"]["failure_code"] == "self_test_resources_unavailable"
    assert len(calls) == int(phase == "during_provider")
    if calls:
        assert record["usage"]["input_tokens"] == 8
        assert record["provider_trace"]["provider_terminal"]["type"] == "turn.completed"


@pytest.mark.parametrize("field,value", [
    ("self_test_binding_ref", "artifact:forged"), ("self_test_binding_sha256", "a"*64),
    ("input_closure_sha256", "b"*64), ("execution_profile_sha256", "c"*64),
])
def test_codex_forged_binding_rejected_before_any_process(environment, field, value):
    _, _, _, calls, controls, _ = environment
    def forge(adapter, request, host):
        values = asdict(request)
        values.pop("request_sha256")
        values["authorized_inputs"] = request.authorized_inputs
        values[field] = value
        forged = AuthorizedAgentExecutionRequest.build(**values)
        with pytest.raises((PermissionError, KeyError)):
            # Call the real class implementation directly, avoiding the fixture hook.
            type(adapter).execute(adapter, forged, host)
        raise PermissionError("probe stops original invocation after forged request is refused")
    controls["before_execute"] = forge
    record = invoke(environment)
    assert record["status"] == "failed" and calls == []


def test_existing_cli_routes_codex_and_preserves_exit_semantics(environment, tmp_path, capsys):
    payload = tmp_path / "input.json"
    payload.write_text("{}")
    argv = ["--root", str(environment[0]), "--workflow", "summarize_note", "--input", str(payload),
            "--transport", "codex_cli", "--model", "gpt-6-astra", "--effort", "xhigh", "--cli-path", sys.executable]
    assert main(argv) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["output"] == {"summary": "non_pass"}
    assert record["self_test_binding"]["adapter"]["transport_kind"] == "codex_cli"
    with pytest.raises(SystemExit) as refused:
        main(argv + ["--save-to-pg"])
    assert refused.value.code == 2 and len(environment[3]) == 1


def test_codex_resource_close_serializes_only_with_launch_not_the_entire_invocation(environment):
    _, _, resources, calls, controls, _ = environment
    def before(adapter, request, host):
        resource = resources[0]
        closing, closed = Event(), Event()
        def close():
            closing.set()
            resource.close()
            closed.set()
        thread = Thread(target=close)
        def launch_effect():
            thread.start()
            assert closing.wait(2)
            assert not closed.is_set(), "close crossed the admitted launch effect"
            return "created"
        assert host.guard_self_test_launch(request, launch_effect, adapter=adapter,
            artifact_host=adapter._artifact_host, workspace_root=adapter._workspace_root) == "created"
        thread.join(2)
        assert closed.is_set(), "close should finish without waiting for a full invocation"
        with pytest.raises(PermissionError):
            host.guard_self_test_launch(request, lambda: pytest.fail("closed launch"), adapter=adapter,
                artifact_host=adapter._artifact_host, workspace_root=adapter._workspace_root)
    controls["before_execute"] = before
    record = invoke(environment)
    assert record["status"] == "failed" and calls == []


@pytest.mark.parametrize("port", ["artifact_host", "workspace_root", "adapter"])
def test_codex_live_resources_cannot_be_reused_with_different_ports(environment, port):
    _, _, _, calls, controls, _ = environment
    def before(adapter, request, host):
        ports = dict(adapter=adapter, artifact_host=adapter._artifact_host, workspace_root=adapter._workspace_root)
        ports[port] = adapter._workspace_root / "different" if port == "workspace_root" else object()
        with pytest.raises(PermissionError):
            host.validate_self_test_binding(request, **ports)
        raise PermissionError("probe stopped after crossed port rejection")
    controls["before_execute"] = before
    record = invoke(environment)
    assert record["status"] == "failed" and calls == []


def test_codex_does_not_shrink_reviewer_default_tools_to_make_a_profile(tmp_path):
    from agent_runtime import ModuleReviewer
    from test_agent_runtime_reviewer_registration_cli import _source, _register, MODULE_ID
    root = tmp_path / "host"
    source, _ = _source(tmp_path / "source")
    _register(root, source)
    before = _files(root)
    with pytest.raises(ValueError):
        prepare_local_workflow_module(root, MODULE_ID, transport_kind="codex_cli",
                                     model_id="gpt-6-astra", reasoning_profile="xhigh")
    assert _files(root) == before
