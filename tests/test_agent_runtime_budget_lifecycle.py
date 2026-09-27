"""Real Provider lifecycle checks for managed work and declared commands.

The managed cases use Claude's actual CLI and Runtime callback graph. The
command case uses the public Test Run, an actual Claude command-tool call and
the Runtime-bound host deadline that stops its real sandboxed process.
"""

import json
import os
from pathlib import Path
import shutil
import sys
from threading import Event, Thread

import psutil
import pytest


REAL_RUN = pytest.mark.skipif(
    os.environ.get("AGENT_RUNTIME_REAL_RUN") != "1",
    reason="requires explicit real-process/Provider opt-in",
)


def _claude_executable():
    executable = os.environ.get("AGENT_RUNTIME_CLAUDE_BIN") or shutil.which("claude")
    if executable is None:
        pytest.skip("unverified: installed Claude CLI is unavailable")
    return executable


def _write_record(tmp_path, name, record):
    directory = Path(os.environ.get("AGENT_RUNTIME_BUDGET_EVIDENCE_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open("x", encoding="utf-8") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)


def _slow_child_reviewer():
    from agent_runtime.testing.conformance_agent_examples import _module, _REVIEW_INPUT, _REVIEW_OUTPUT

    return _module(
        "task_reviewer",
        "Your FIRST action must be a Bash tool call with command exactly `sleep 120` "
        "and run_in_background=true. Then keep this review active by using Bash "
        "to wait for that process to finish: run "
        "`until ! pgrep -f '(^|/)sleep 120$' >/dev/null; do sleep 2; done` "
        "in the foreground. Return accepted/reason JSON only after the background "
        "sleep has ended. If interrupted, do not claim acceptance.",
        _REVIEW_INPUT, _REVIEW_OUTPUT, tools=("shell",), draft=True,
    )


def _parameters(root, *, parent_seconds, child_seconds):
    directory = root / ".runtime/execution_parameters"
    (directory / "workflows").mkdir(parents=True)
    (directory / "workspace.json").write_text(json.dumps({
        "schema_version": "runtime_execution_parameters_v2",
        "transport_kind": "claude_cli",
        "model_id": "claude-sonnet-5",
        "reasoning_profile": "low",
        "run_timeout_seconds": parent_seconds,
    }), encoding="utf-8")
    (directory / "workflows/capability_task_reviewer.json").write_text(json.dumps({
        "schema_version": "runtime_execution_parameters_v2",
        "run_timeout_seconds": child_seconds,
    }), encoding="utf-8")


def _owned_sleep():
    """Retain a process identity found under this test's real Runtime process."""
    try:
        children = psutil.Process(os.getpid()).children(recursive=True)
    except psutil.Error:
        return None
    for child in children:
        try:
            command = child.cmdline()
            if len(command) == 2 and Path(command[0]).name == "sleep" and command[1] == "120":
                return {"pid": child.pid, "created": child.create_time(), "command": command}
        except psutil.Error:
            continue
    return None


def _still_running(identity):
    if identity is None:
        return False
    try:
        process = psutil.Process(identity["pid"])
        return (process.create_time() == identity["created"] and process.is_running()
                and process.status() != psutil.STATUS_ZOMBIE)
    except psutil.Error:
        return False


def _cleanup_observed(identity):
    if _still_running(identity):
        process = psutil.Process(identity["pid"])
        process.terminate()
        try:
            process.wait(timeout=3)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        return True
    return False


@pytest.mark.real_run
@REAL_RUN
def test_child_own_deadline_ends_child_and_parent_continues(tmp_path, monkeypatch):
    """A shorter child budget fails its real CLI, leaving the parent to report it."""
    from agent_runtime.testing import conformance_agent_execution as execution
    from agent_runtime.invocation import invocation_claude_cli_execution as claude

    monkeypatch.setattr(execution, "build_example_reviewer", _slow_child_reviewer)
    original_adapter = claude.ClaudeAdapter
    real_process = claude.run_cli_process
    observed = {}

    def trace_real_process(**fields):
        if fields["timeout_seconds"] == 5:
            original_guard = fields["launch_guard"]

            def guard(create):
                def observe_create():
                    process = create()
                    observed.setdefault("child_providers", []).append({
                        "pid": process.pid, "created": psutil.Process(process.pid).create_time(),
                    })
                    return process

                return original_guard(observe_create)

            fields["launch_guard"] = guard
        return real_process(**fields)

    class TracedClaudeAdapter(original_adapter):
        def __init__(self, **fields):
            super().__init__(**fields, process_runner=trace_real_process)

    monkeypatch.setattr(claude, "ClaudeAdapter", TracedClaudeAdapter)
    root = tmp_path / "root"
    _parameters(root, parent_seconds=150, child_seconds=5)
    result = execution.run_agent_example(root, "agent_evaluation_example", cli_path=_claude_executable())
    child_processes = observed.get("child_providers", [])
    remaining = [identity for identity in child_processes if _still_running(identity)]
    _write_record(tmp_path, "child_shorter_deadline.json", {
        "runtime_record": result, "observed_child_providers": child_processes,
        "providers_surviving_runtime": remaining,
    })
    assert child_processes, "at least one child must launch the actual Claude CLI process"
    assert not remaining, "the child Provider survived its own deadline"
    assert result["child_executions"]
    for row in result["child_executions"]:
        child = row["record"]
        assert child["execution_budget"]["requested_timeout_seconds"] == 5
        assert child["execution_budget"]["limiting_scope"] == "local"
        assert child["failure_class"] == "timeout", child.get("failure_detail")
        assert child["failure_detail"]["failure_code"] == "claude_cli_timeout"
        assert child["provider_trace"]["stop_reason"] == "timeout"
        assert child["provider_trace"].get("cleanup_error") is None
        assert child["provider_trace"]["argv"], "the real child Provider process must launch"
        assert child["status"] == "failed"
    tested = next(row for row in result["execution"]["nodes"]
                  if row["dispatch"]["current_state_id"] == "tested_agent")
    assert tested["status"] == "completed", tested.get("failure_detail")
    assert tested["output"]["task_completed"] is False
    assert result["status"] == "completed", result.get("failure")
    assert result["resources_cleaned"] is True


@pytest.mark.real_run
@REAL_RUN
def test_parent_cancellation_stops_active_child_and_preserves_cause(tmp_path, monkeypatch):
    """A trusted host cancels the graph after observing the real child command."""
    from agent_runtime.testing import conformance_agent_execution as execution
    from agent_runtime.execution import execution_workflow_evaluation as workflow

    monkeypatch.setattr(execution, "build_example_reviewer", _slow_child_reviewer)
    root = tmp_path / "root"
    _parameters(root, parent_seconds=90, child_seconds=90)
    captured = {}
    original_init = workflow.WorkflowSelfTestResources.__init__

    def capture_resources(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        captured["resources"] = self

    monkeypatch.setattr(workflow.WorkflowSelfTestResources, "__init__", capture_resources)
    stop = Event()

    def cancel_when_child_runs():
        while not stop.wait(0.05):
            identity = _owned_sleep()
            if identity is not None and "resources" in captured:
                captured["sleep"] = identity
                captured["resources"].request_cancel("test host cancelled active parent")
                captured["cancel_sent"] = True
                return

    watcher = Thread(target=cancel_when_child_runs, daemon=True)
    watcher.start()
    try:
        result = execution.run_agent_example(root, "agent_evaluation_example", cli_path=_claude_executable())
    finally:
        stop.set()
        watcher.join(timeout=5)

    identity = captured.get("sleep")
    remaining = _still_running(identity)
    test_cleanup = _cleanup_observed(identity)
    _write_record(tmp_path, "parent_cancellation.json", {
        "runtime_record": result, "observed_sleep": identity,
        "cancel_sent": captured.get("cancel_sent", False),
        "sleep_survived_runtime": remaining, "test_cleanup_sent": test_cleanup,
    })
    assert captured.get("cancel_sent") and identity is not None
    assert not remaining, "the child process survived parent cancellation"
    assert result["status"] == "cancelled", result.get("failure")
    assert result["stop_reason"] == "cancelled"
    assert result["resources_cleaned"] is True
    assert len(result["child_executions"]) == 1
    child = result["child_executions"][0]["record"]
    assert child["status"] == "cancelled", child.get("failure_detail")
    assert child["failure_class"] == "cancelled"
    assert child["failure_detail"]["failure_code"] == "claude_cli_interrupted"
    assert child["provider_trace"]["stop_reason"] == "cancelled"
    assert child["provider_trace"].get("cleanup_error") is None
    tested = next(row for row in result["execution"]["nodes"]
                  if row["dispatch"]["current_state_id"] == "tested_agent")
    assert tested["status"] == "cancelled", tested.get("failure_detail")
    assert tested["failure_class"] == "cancelled"
    assert tested["failure_detail"]["failure_code"] == "claude_cli_interrupted"


@pytest.mark.real_run
@REAL_RUN
def test_public_runtime_deadline_reaches_real_declared_command(tmp_path):
    """The public Test Run carries its live budget into the real command host."""
    from agent_runtime import Module, RuntimeModulePlugin, register_runtime_module_plugin, run_local_workflow_test
    from agent_runtime.registry import RuntimeReleaseRegistry
    from agent_runtime.testing.conformance_agent_examples import _module, _object, _TEXT, _REVIEW_OUTPUT

    exported = Module.to_workflow(_module(
        "command_budget_probe",
        "Your FIRST action must be the Runtime MCP tool "
        "mcp__runtime_commands__sandbox_command_execute with command_id `slow`. "
        "Wait for the actual command response before returning accepted/reason JSON. "
        "Never substitute native Bash or claim that the command finished early.",
        _object(task=_TEXT), _REVIEW_OUTPUT, tools=("shell",), draft=True,
    )).export()
    root = tmp_path / "root"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        "declared_command_budget_probe", "v2", exported.origin_bundle), root=root)
    command = {"command_id": "slow", "argv": [sys.executable, "-I", "-B", "-c",
        "import time;print('command-started',flush=True);time.sleep(120)"],
        "cwd": "scratch", "timeout_seconds": 35}
    record = run_local_workflow_test(
        root, exported.workflow_release.workflow_id,
        input_payload={"task": "Run the declared slow command and report its actual result."},
        transport_kind="claude_cli", model_id="claude-sonnet-5", reasoning_profile="low",
        run_timeout_seconds=35, cli_path=_claude_executable(), read_only_dependencies=(),
        commands=(command,),
    )
    _write_record(tmp_path, "public_declared_command_budget.json", {"runtime_record": record})
    calls = record["provider_trace"]["local_command_calls"]
    assert len(calls) == 1, calls
    local_call = calls[0]
    assert local_call["request"] == {"command_id": "slow"}
    response = local_call["response"]
    assert response["allowed"] is True
    assert response["stdout"].startswith("command-started\n")
    assert response["failure"]["stop_reason"] == "timeout"
    assert record["execution_budget"]["requested_timeout_seconds"] == 35
    assert record["provider_trace"]["stop_reason"] == "timeout"
    assert record["failure_class"] == "timeout"
