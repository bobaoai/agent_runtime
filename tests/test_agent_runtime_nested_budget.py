"""Real managed child: a parent deadline stops an already running Provider tool."""

import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
from threading import Event, Thread
import time

import pytest


SLEEP_COMMAND = re.compile(r"(?:^|/)sleep\s+120(?:\s|$)")


def _processes():
    # Enumerate identities only. Command text is queried for this test's
    # descendants after their ancestry has been proved.
    result = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,pgid=,lstart="],
                            capture_output=True, text=True, check=True)
    rows = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split()
        if len(parts) == 8 and all(value.isdigit() for value in parts[:3]):
            rows[int(parts[0])] = (int(parts[1]), int(parts[2]), " ".join(parts[3:]))
    return rows


def _descends_from(pid, ancestor, rows):
    seen = set()
    while pid in rows and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        pid = rows[pid][0]
    return False


def _selected_commands(pids):
    if not pids:
        return {}
    result = subprocess.run(["/bin/ps", "-p", ",".join(map(str, pids)), "-o", "pid=,command="],
                            capture_output=True, text=True, check=False)
    commands = {}
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            commands[int(parts[0])] = parts[1]
    return commands


def _owned_ancestry(pid, rows, commands):
    lineage = []
    while pid in rows:
        ppid, pgid, started_at = rows[pid]
        command = commands.get(pid, "")
        lineage.append({"pid": pid, "ppid": ppid, "pgid": pgid,
                        "started_at": started_at,
                        "program": Path(command.split()[0]).name if command else None})
        if pid == os.getpid():
            break
        pid = ppid
    return lineage


@pytest.mark.real_run
@pytest.mark.skipif(os.environ.get("AGENT_RUNTIME_REAL_RUN") != "1",
                    reason="requires a real authenticated Claude CLI")
def test_parent_expiry_stops_active_child_provider_shell(tmp_path, monkeypatch):
    claude_cli = os.environ.get("AGENT_RUNTIME_CLAUDE_BIN") or shutil.which("claude")
    if not claude_cli:
        pytest.skip("unverified: an installed Claude CLI is required")
    evidence = Path(os.environ.get("AGENT_RUNTIME_BUDGET_EVIDENCE_DIR", str(tmp_path))) / "nested_expiry_record.json"
    if evidence.exists():
        raise FileExistsError("provide a fresh evidence directory; existing runs are immutable")
    from agent_runtime.testing import conformance_agent_execution as execution
    from agent_runtime.testing.conformance_agent_examples import _module, _REVIEW_INPUT, _REVIEW_OUTPUT

    original_factory = execution._ExampleToolFactory
    callback_timing = {}

    def waiting_factory(definition, invoke):
        if definition.tool_name != "review_candidate":
            return original_factory(definition, invoke)

        def invoke_near_parent_deadline(session, payload):
            parent = session.parent_run
            assert parent is not None and parent.attempt_id == session.request.attempt_id
            callback_timing["remaining_on_entry_seconds"] = parent._budget.remaining()
            while parent._budget.remaining() > 8:
                time.sleep(0.05)
            callback_timing["remaining_before_child_seconds"] = parent._budget.remaining()
            return invoke(session, payload)

        return original_factory(definition, invoke_near_parent_deadline)

    def slow_child():
        return _module("task_reviewer",
            "Review the submitted draft against every required fact. Your FIRST action must be a Bash "
            "tool call with command exactly `sleep 120` and run_in_background=true. This is a real "
            "background process. Then keep this review active by using Bash to wait for that process to "
            "finish: run `until ! pgrep -f '(^|/)sleep 120$' >/dev/null; do sleep 2; done` in the "
            "foreground. The loop checks the actual process condition; it is not a substitute for the "
            "120-second task. Return accepted/reason JSON only after the background sleep has ended. "
            "If a tool is interrupted, do not claim the review was accepted.",
            _REVIEW_INPUT, _REVIEW_OUTPUT, tools=("shell",), draft=True)

    monkeypatch.setattr(execution, "build_example_reviewer", slow_child)
    monkeypatch.setattr(execution, "_ExampleToolFactory", waiting_factory)
    root = tmp_path / "root"
    parameters = root / ".runtime/execution_parameters"
    (parameters / "workflows").mkdir(parents=True)
    (parameters / "workspace.json").write_text(json.dumps({
        "schema_version": "runtime_execution_parameters_v2", "transport_kind": "claude_cli",
        "model_id": "claude-sonnet-5", "reasoning_profile": "low", "run_timeout_seconds": 60,
    }), encoding="utf-8")
    (parameters / "workflows/capability_task_reviewer.json").write_text(json.dumps({
        "schema_version": "runtime_execution_parameters_v2", "run_timeout_seconds": 3600,
    }), encoding="utf-8")

    stop = Event()
    observed = {}
    observation_errors = []

    def observe_sleep():
        while not stop.wait(0.1):
            try:
                rows = _processes()
            except Exception as exc:
                observation_errors.append(f"{type(exc).__name__}: {exc}")
                return
            owned = [pid for pid in rows if _descends_from(pid, os.getpid(), rows)]
            commands = _selected_commands(owned)
            for pid, command in commands.items():
                if SLEEP_COMMAND.search(command) and pid in rows:
                    ppid, pgid, started_at = rows[pid]
                    observed.setdefault(pid, {"command": command, "ppid": ppid,
                        "pgid": pgid, "started_at": started_at,
                        "ancestry": _owned_ancestry(pid, rows, commands),
                        "observed_at_monotonic": time.monotonic()})

    watcher = Thread(target=observe_sleep, daemon=True)
    watcher.start()
    try:
        try:
            result = execution.run_agent_example(root, "agent_evaluation_example", cli_path=claude_cli)
        except BaseException as exc:
            evidence.parent.mkdir(parents=True, exist_ok=True)
            evidence.write_text(json.dumps({"execution_error": {"type": type(exc).__name__,
                "message": str(exc)}, "observed_sleep_processes": observed,
                "callback_timing": callback_timing, "observation_errors": observation_errors},
                ensure_ascii=False, indent=2), encoding="utf-8")
            raise
    finally:
        stop.set()
        watcher.join(timeout=5)

    remaining = {}
    for _ in range(50):
        rows = _processes()
        same = [pid for pid, identity in observed.items()
                if pid in rows and rows[pid][2] == identity["started_at"]]
        commands = _selected_commands(same)
        remaining = {pid: {"command": commands[pid], "ppid": rows[pid][0],
                           "pgid": rows[pid][1], "started_at": rows[pid][2]}
                     for pid in same if pid in commands and SLEEP_COMMAND.search(commands[pid])}
        if not remaining:
            break
        time.sleep(0.1)
    cleanup_sent = []
    for pid, identity in remaining.items():
        if identity["command"] == "sleep 120" and identity["started_at"] == observed[pid]["started_at"]:
            try:
                os.kill(pid, signal.SIGTERM)
                cleanup_sent.append(pid)
            except ProcessLookupError:
                pass
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"runtime_record": result,
        "observed_sleep_processes": observed, "remaining_sleep_processes": remaining,
        "test_cleanup_signals": cleanup_sent, "callback_timing": callback_timing,
        "observation_errors": observation_errors},
        ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8")

    assert not observation_errors, observation_errors
    assert 0 < callback_timing["remaining_before_child_seconds"] <= 8
    assert observed, "No actual sleep 120 process started under this Runtime test process"
    assert not remaining, "The child sleep process survived the parent deadline"
    assert result["child_executions"], "No recorded managed child execution"
    child = result["child_executions"][0]["record"]
    calls = child["execution_log"]["tool_calls"]
    assert any(row["tool_name"] == "Bash" and (row.get("request") or {}).get("command") == "sleep 120"
               and (row.get("request") or {}).get("run_in_background") is True for row in calls), calls
    assert child["failure_class"] == "timeout", child.get("failure_detail")
    assert child["failure_detail"]["failure_code"] == "claude_cli_timeout"
    assert child["status"] != "completed"
    assert result["status"] != "completed" and result["resources_cleaned"], result.get("failure")
    assert result["failure"]["error_type"] == "TimeoutError"
    parent = next(row for row in result["execution"]["nodes"]
                  if row["dispatch"]["current_state_id"] == "tested_agent")
    assert parent["failure_class"] == "timeout"
    assert parent["failure_detail"]["failure_code"] == "claude_cli_timeout"
    assert result["execution_budget"]["elapsed_seconds"] >= 60
