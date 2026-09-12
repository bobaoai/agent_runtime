"""Real stream capture and complete log projection, without model/PG access."""
import base64
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_runtime import parse_cli_log, read_execution_log
from agent_runtime.invocation.invocation_cli_logging import captured_cli_streams, cli_stream_bytes
from agent_runtime.invocation.invocation_process_execution import run_cli_process, CliProcessInterrupted
import test_agent_runtime_claude_native_tools as native


def use(identity, name="Bash", **arguments):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": identity, "name": name, "input": arguments}]}}


def reply(identity, *, failed=False, **details):
    return {"type": "user", "message": {"content": [{"type": "tool_result",
        "tool_use_id": identity, "content": "actual output", "is_error": failed}]},
        "tool_use_result": details}


def trace(events, *, complete=True, raw=None, transport="claude_cli"):
    raw = raw if raw is not None else b"\n".join(json.dumps(e).encode() for e in events)
    result = subprocess.CompletedProcess([], 0, raw.decode("utf-8", errors="replace"), "")
    result.stdout_bytes, result.stderr_bytes = raw, b""
    return {"transport": transport, "stdout": result.stdout, "stderr": result.stderr,
            "process_output_complete": complete, **captured_cli_streams(result)}


def test_parallel_tool_pairing_preserves_full_requests_results_and_original_events():
    events = [native._init(), use("read_1", "Read", file_path="material.txt", limit=100),
              use("shell_1", command="python -c 'print(1)'"),
              reply("shell_1", stdout="1\n", stderr="warning", returncode=7),
              reply("read_1", lines=["first", "second"]), native._result()]
    original = trace(events)
    log = parse_cli_log(original)
    assert log["complete"] and log["issues"] == []
    assert [c["tool_call_id"] for c in log["tool_calls"]] == ["read_1", "shell_1"]
    assert log["tool_calls"][0]["response_event_indices"] == [4]
    shell = log["tool_calls"][1]
    assert shell["request"] == {"command": "python -c 'print(1)'"}
    assert shell["response"]["tool_use_result"] == {"stdout": "1\n", "stderr": "warning", "returncode": 7}
    assert all(c["source_kind"] == "provider_native" and "grant_id" not in c for c in log["tool_calls"])
    assert [e["event"] for e in log["events"]] == events
    assert json.loads(cli_stream_bytes(original, "stdout").splitlines()[1]) == events[1]


@pytest.mark.parametrize("events,issue", [
    ([use("a"), native._result()], "unpaired_tool_call:a"),
    ([reply("a"), native._result()], "unpaired_tool_call:a"),
    ([use("a"), use("a"), reply("a"), native._result()], "duplicate_tool_request:a"),
    ([use("a"), reply("a"), reply("a"), native._result()], "duplicate_tool_response:a"),
    ([use(None), native._result()], "missing_tool_call_id:0"),
    ([use("a"), reply("a")], "terminal_event_missing_or_duplicated"),
])
def test_missing_or_ambiguous_events_are_not_an_empty_complete_log(events, issue):
    log = parse_cli_log(trace(events))
    assert not log["complete"] and issue in log["issues"]


def test_tool_error_is_recorded_separately_from_log_completeness():
    log = parse_cli_log(trace([use("a", "Read", file_path="missing"), reply("a", failed=True), native._result()]))
    assert log["complete"] and log["tool_calls"][0]["status"] == "failed"
    assert "returncode" not in log["tool_calls"][0]


def test_invalid_json_and_non_utf8_preserve_exact_raw_bytes():
    raw = b'{"type":"result"}\nnot-json\n\xff\xfe\n'
    original = trace([], raw=raw)
    log = parse_cli_log(original)
    assert not log["complete"] and {"invalid_event:1", "invalid_event:2"} <= set(log["issues"])
    assert cli_stream_bytes(original, "stdout") == raw


@pytest.mark.parametrize("raw", [b'{"type":"assistant","value":NaN}', b'{"type":"assistant","value":"\\ud800"}'])
def test_non_serializable_event_is_retained_without_poisoning_the_trace(raw):
    original = trace([], raw=raw)
    parsed = parse_cli_log(original)
    assert not parsed["complete"] and "invalid_event:0" in parsed["issues"]
    assert cli_stream_bytes(original, "stdout") == raw


def test_codex_mcp_logs_preserve_real_command_results():
    result = {"command_id": "unit", "allowed": True, "returncode": 0, "stdout": "153 passed\n", "stderr": ""}
    item = {"id": "call1", "type": "mcp_tool_call", "tool": "sandbox_command_execute",
            "arguments": {"command_id": "unit"}}
    events = [{"type": "item.started", "item": item}, {"type": "item.completed",
        "item": {**item, "status": "completed", "error": None,
                 "result": {"content": [{"type": "text", "text": json.dumps(result)}]}}}, {"type": "turn.completed"}]
    log = parse_cli_log(trace(events, transport="codex_cli"))
    assert log["complete"]
    assert log["tool_calls"][0]["request"] == {"command_id": "unit"}
    assert log["tool_calls"][0]["response"] == result
    unsupported = [{"type": "item.completed", "item": {"type": "command_execution"}}, {"type": "turn.completed"}]
    assert not parse_cli_log(trace(unsupported, transport="codex_cli"))["complete"]


@pytest.mark.parametrize("text", ['{"nested":' + '[' * 10000 + '0' + ']' * 10000 + '}', '{"text":"\\ud800"}'])
def test_codex_uninterpretable_result_text_remains_the_actual_response(text):
    item = {"id": "c", "type": "mcp_tool_call", "tool": "repository_read", "arguments": {"path": "file"}}
    raw_result = {"content": [{"type": "text", "text": text}]}
    events = [{"type": "item.started", "item": item}, {"type": "item.completed",
              "item": {**item, "status": "completed", "error": None, "result": raw_result}},
              {"type": "turn.completed"}]
    log = parse_cli_log(trace(events, transport="codex_cli"))
    assert log["complete"] and log["tool_calls"][0]["response"] == raw_result


def test_real_process_keeps_non_utf8_and_large_streams(tmp_path):
    stdout, stderr = b"a" * 100000 + b"\xff", b"b" * 100000 + b"\xfe"
    process = run_cli_process(argv=[sys.executable, "-c",
        "import os;os.write(1,b'a'*100000+b'\\xff');os.write(2,b'b'*100000+b'\\xfe')"],
        cwd=tmp_path, prompt="", environment=dict(os.environ), timeout_seconds=5)
    recorded = captured_cli_streams(process)
    assert recorded["byte_capture_exact"]
    assert process.stdout_bytes == stdout and process.stderr_bytes == stderr
    assert cli_stream_bytes(recorded, "stdout") == stdout
    assert cli_stream_bytes(recorded, "stderr") == stderr


def test_real_user_interrupt_preserves_prefix_after_process_cleanup(tmp_path):
    driver = """
import json,os,sys
from pathlib import Path
from agent_runtime.invocation.invocation_process_execution import run_cli_process,CliProcessInterrupted
try:
    run_cli_process(argv=[sys.executable,'-u','-c',
        "import os,signal,time;print('before interrupt',flush=True);time.sleep(.2);os.kill(os.getppid(),signal.SIGINT);time.sleep(30)"],
        prompt='',cwd=Path.cwd(),environment=dict(os.environ),timeout_seconds=10)
except CliProcessInterrupted as exc:
    print(json.dumps({'raw':exc.stdout_bytes.decode(),'stop':exc.stop_reason,'returncode':exc.returncode}))
"""
    process = subprocess.run([sys.executable, "-c", driver], cwd=tmp_path, capture_output=True, text=True,
        timeout=15, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["raw"] == "before interrupt\n" and result["stop"] == "cancelled"
    assert result["returncode"] != 0


@pytest.mark.parametrize("phase", ["cleanup", "join"])
def test_first_real_interrupt_during_shutdown_preserves_bytes_and_restores_handler(tmp_path, phase):
    driver = """
import json,os,signal,sys
from pathlib import Path
from agent_runtime.invocation import invocation_process_execution as capture
previous=signal.getsignal(signal.SIGINT)
phase=sys.argv[1]
original=capture._stop_process_group if phase=='cleanup' else capture.Thread.join
sent=[]
def interrupt(*args,**kwargs):
    if not sent:
        sent.append(True)
        os.kill(os.getpid(),signal.SIGINT)
    return original(*args,**kwargs)
if phase=='cleanup': capture._stop_process_group=interrupt
else: capture.Thread.join=interrupt
try:
    capture.run_cli_process(argv=[sys.executable,'-u','-c',
        "import os;os.write(1,b'before shutdown\\\\n');os.write(2,b'actual stderr')"],
        prompt='',cwd=Path.cwd(),environment=dict(os.environ),timeout_seconds=5)
except capture.CliProcessInterrupted as exc:
    print(json.dumps({'raw':exc.stdout_bytes.decode(),'stderr':exc.stderr_bytes.decode(),
        'stop':exc.stop_reason,'returncode':exc.returncode,
        'restored':signal.getsignal(signal.SIGINT) is previous,'cleanup_error':exc.cleanup_error}))
"""
    process = subprocess.run([sys.executable, "-c", driver, phase], cwd=tmp_path, capture_output=True, text=True,
        timeout=15, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result == {"raw": "before shutdown\n", "stderr": "actual stderr", "stop": "cancelled",
                      "returncode": 0, "restored": True, "cleanup_error": None}


def test_deep_event_from_real_process_cannot_prevent_adapter_trace_commit(tmp_path, monkeypatch):
    env = native._environment(tmp_path)
    _, registry, cell, request, authority = env
    # CPython's C JSON decoder can have a higher nesting limit than Python
    # frames; exceed both in supported interpreters, without changing either.
    depth = max(10000, sys.getrecursionlimit() * 10)
    raw = json.dumps(native._init()).encode() + b'\n{"nested":' + b'[' * depth + b'0' + b']' * depth + b'}\n'
    def actual_process(**kwargs):
        return run_cli_process(**{**kwargs, "argv": [sys.executable, "-c", f"import os;os.write(1,{raw!r})"]})
    adapter = native.claude.ClaudeCliNativeToolsModuleExecutor(
        release_registry=registry, artifact_host=cell, workspace_root=tmp_path / "attempts",
        cli_path=native._fake_cli(tmp_path), process_runner=actual_process)
    adapters = native.AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    temporary_directory = native.claude.tempfile.TemporaryDirectory
    monkeypatch.setattr(native.claude.tempfile, "TemporaryDirectory", lambda *args, **kw:
                        temporary_directory(*args, **{**kw, "dir": tmp_path}))
    run = native.run_module(request, release_registry=registry, adapters=adapters, artifact_host=cell,
                           ledger=native.InMemoryModuleExecutionLedger(), authority=authority,
                           clock=lambda: native._TEST_TIME)
    assert run.attempts[0].status == "failed"
    log = read_execution_log(run.module_run, attempts=run.attempts, read_content=cell.read_bytes,
                             include_private_content=True)
    attempt = log["attempts"][0]
    assert cli_stream_bytes(attempt["provider_log"], "stdout") == raw
    assert not attempt["complete"] and "invalid_event:1" in attempt["issues"]


@pytest.mark.parametrize("outcome", ["success", "timeout"])
def test_unexpected_normalizer_failure_preserves_original_trace(tmp_path, monkeypatch, outcome):
    def broken_parser(trace):
        raise RuntimeError("unexpected parser failure")
    monkeypatch.setattr(native.claude, "parse_cli_log", broken_parser)
    def events(call):
        yield native._init()
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("fixture", 1)
        yield native._result()
    run, cell = native._run(native._environment(tmp_path), tmp_path, events)
    log = read_execution_log(run.module_run, attempts=run.attempts, read_content=cell.read_bytes,
                             include_private_content=True)
    attempt = log["attempts"][0]
    assert attempt["status"] == ("completed" if outcome == "success" else "failed")
    assert not attempt["complete"] and "normalization_failed:RuntimeError" in attempt["issues"]
    assert json.loads(cli_stream_bytes(attempt["provider_log"], "stdout").splitlines()[0]) == native._init()


def test_runtime_reader_is_private_by_default_and_does_not_create_grants(tmp_path):
    events = [native._init(), use("tool1", "Read", file_path="material.txt"), reply("tool1"), native._result()]
    run, cell = native._run(native._environment(tmp_path), tmp_path, lambda call: (e for e in events))
    assert run.attempts[0].status == "completed"
    def forbidden(*args):
        pytest.fail("metadata query must not read private content")
    metadata = read_execution_log(run.module_run, attempts=run.attempts, read_content=forbidden)
    assert metadata["complete"] is None and metadata["attempts"][0]["tool_calls"] is None
    result = read_execution_log(run.module_run, attempts=run.attempts,
                                read_content=cell.read_bytes, include_private_content=True)
    assert result["complete"] and result["attempts"][0]["tool_calls"][0]["tool_call_id"] == "tool1"
    assert run.attempts[0].tool_calls == ()
    assert result["attempts"][0]["provider_log"]["raw_streams"]["stdout"]["encoding"] == "base64"
    with pytest.raises(ValueError, match="hash"):
        read_execution_log(run.module_run, attempts=run.attempts, include_private_content=True,
                           read_content=lambda ref, digest: b"corrupt")
    with pytest.raises(ValueError, match="identities"):
        read_execution_log(run.module_run, attempts=(replace(run.attempts[0], module_run_id="another_run"),),
                           read_content=cell.read_bytes)


def test_cancelled_attempt_retains_requested_call_and_original_log(tmp_path):
    events = [native._init(), use("before_cancel", "Read", file_path="material.txt")]
    raw = b"\n".join(json.dumps(e).encode() for e in events)
    def interrupted(call):
        yield from events
        raise CliProcessInterrupted(returncode=-9, output=raw.decode(), stderr="cancelled",
            stdout_bytes=raw, stderr_bytes=b"cancelled")
    run, cell = native._run(native._environment(tmp_path), tmp_path, interrupted)
    assert run.attempts[0].status == "cancelled"
    log = read_execution_log(run.module_run, attempts=run.attempts, read_content=cell.read_bytes, include_private_content=True)
    first = log["attempts"][0]
    assert not first["complete"] and first["tool_calls"][0]["status"] == "incomplete"
    assert cli_stream_bytes(first["provider_log"], "stdout") == raw


def test_single_attempt_failure_keeps_log_without_changing_retry_policy(tmp_path):
    calls = []
    def events(call):
        calls.append(True)
        yield native._init()
        yield use("reused_provider_id", command="test")
        if len(calls) == 1:
            raise subprocess.TimeoutExpired("fixture", 1)
        yield reply("reused_provider_id")
        yield native._result()
    run, cell = native._run(native._environment(tmp_path), tmp_path, events)
    # run_module is a single invocation; the Workflow's retry dispatcher owns
    # subsequent Attempts. Logging must not add its own retry loop.
    assert len(run.attempts) == 1
    log = read_execution_log(run.module_run, attempts=run.attempts, read_content=cell.read_bytes, include_private_content=True)
    assert [a["status"] for a in log["attempts"]] == ["failed"]
    assert log["attempts"][0]["tool_calls"][0]["tool_call_id"] == "reused_provider_id"
    assert not log["complete"]


@pytest.mark.parametrize("outcome", ["success", "timeout", "cancelled", "bad_output"])
def test_private_trace_is_committed_before_cli_temporary_cleanup(tmp_path, monkeypatch, outcome):
    env = native._environment(tmp_path)
    cell = env[2]
    committed_while_alive = []
    commit = cell.commit_attempt_trace
    def observed_commit(**kwargs):
        body = json.loads(kwargs["content"])
        temporary = Path(body["environment"]["TMPDIR"])
        assert temporary.is_dir()
        committed_while_alive.append(temporary)
        return commit(**kwargs)
    monkeypatch.setattr(cell, "commit_attempt_trace", observed_commit)
    def events(call):
        yield native._init()
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("fixture", 1)
        if outcome == "cancelled":
            raw = json.dumps(native._init()).encode()
            raise CliProcessInterrupted(returncode=-9, output=raw.decode(), stderr="",
                                        stdout_bytes=raw, stderr_bytes=b"")
        yield native._result(**({"structured_output": {}} if outcome == "bad_output" else {}))
    run, _ = native._run(env, tmp_path, events)
    assert committed_while_alive and all(not path.exists() for path in committed_while_alive)
    log = read_execution_log(run.module_run, attempts=run.attempts,
                             read_content=cell.read_bytes, include_private_content=True)
    assert log["attempts"][0]["provider_log"]["raw_streams"]
