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


def denied(identity, name="Read"):
    # Same public field shape as the real Runtime deny_read probe, whose
    # permission event carries tool_use_id and no fabricated shell exit code.
    return {"type": "system", "subtype": "permission_denied", "tool_use_id": identity,
            "tool_name": name, "decision_reason_type": "other", "decision_reason": "outside scope"}


@pytest.mark.parametrize("mode", ["event", "summary", "all", "out_of_order"])
def test_permission_sources_describe_one_failed_call_without_duplicate_tool_result(mode):
    refusal = denied("read_1")
    terminal = {**native._result(), "permission_denials": [refusal]} if mode != "event" else native._result()
    events = [use("read_1", "Read", file_path="outside")]
    if mode in {"event", "all", "out_of_order"}:
        events.append(refusal)
    if mode in {"all", "out_of_order"}:
        events.append(reply("read_1", failed=True))
    if mode == "out_of_order":
        events = events[1:] + events[:1]
    events += [use("read_2", "Read", file_path="allowed"), reply("read_2"), terminal]
    parsed = parse_cli_log(trace(events))
    assert parsed["complete"] and parsed["issues"] == []
    assert len(parsed["tool_calls"]) == 2
    first, second = parsed["tool_calls"]
    assert first["tool_call_id"] == "read_1" and first["status"] == "failed"
    assert second["tool_call_id"] == "read_2" and second["status"] == "completed"
    assert ("tool_result" in first["response"]) == (mode in {"all", "out_of_order"})
    assert "exit_code" not in first and "grant_id" not in first
    assert [row["event"] for row in parsed["events"]] == events


@pytest.mark.parametrize("kind", ["event", "summary", "result"])
def test_missing_denial_identity_is_retained_without_name_based_pairing(kind):
    unknown = denied(None)
    terminal = native._result()
    body = unknown if kind == "event" else reply(None, failed=True)
    if kind == "summary":
        body = {**terminal, "permission_denials": [unknown]}
        terminal = None
    events = [use("a", "Read"), use("b", "Read"), body]
    if terminal:
        events.append(terminal)
    parsed = parse_cli_log(trace(events))
    assert not parsed["complete"]
    assert any(issue.startswith("missing_tool_call_id:") for issue in parsed["issues"])
    assert [row["tool_call_id"] for row in parsed["tool_calls"]] == ["a", "b"]
    assert all(row["status"] == "incomplete" for row in parsed["tool_calls"])
    assert body in [row["event"] for row in parsed["events"]]


@pytest.mark.parametrize("extra,issue", [
    (reply("a"), "duplicate_tool_response:a"),
    (denied("a"), "conflicting_tool_results:a"),
    (use("a", "Read"), "duplicate_tool_request:a"),
    ({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a",
        "content": "untrusted error flag", "is_error": "false"}]}}, "invalid_tool_error_flag:2"),
])
def test_ambiguous_call_never_reports_trustworthy_success(extra, issue):
    original_result = reply("a")
    parsed = parse_cli_log(trace([use("a", "Read"), original_result, extra, native._result()]))
    assert not parsed["complete"] and issue in parsed["issues"]
    assert parsed["tool_calls"][0]["status"] == "incomplete"
    assert parsed["tool_calls"][0]["response"]["tool_result"] == original_result["message"]["content"][0]


def test_codex_observed_command_events_preserve_reverse_completion_and_exact_output():
    # CLI 0.153.4 real smoke: review_artifacts/cli_native_smoke_r1/events.jsonl,
    # whole-file SHA-256 07517305284645aa3855b9e0f68958c6036a1d6b6caea19b83675deb9bc0b0c7.
    # Commands and input response are the original event fields; second output
    # is shortened here solely to keep this synthetic interleaving case small.
    one = {"id": "item_1", "type": "command_execution", "command": "/bin/zsh -c 'cat input.json'",
           "aggregated_output": "", "exit_code": None, "status": "in_progress"}
    two = {**one, "id": "item_2", "command": "/bin/zsh -c 'cat .agents/skills/add-fixture/SKILL.md'"}
    complete_one = {**one, "aggregated_output": '{"numbers":[19,23,8]}\n', "exit_code": 0, "status": "completed"}
    complete_two = {**two, "aggregated_output": "synthetic skill output\n", "exit_code": 0, "status": "completed"}
    events = [{"type": "item.started", "item": one}, {"type": "item.started", "item": two},
              {"type": "item.updated", "item": {**two, "aggregated_output": "partial"}},
              {"type": "item.completed", "item": complete_two},
              {"type": "item.completed", "item": complete_one}, {"type": "turn.completed"}]
    parsed = parse_cli_log(trace(events, transport="codex_cli"))
    assert parsed["complete"]
    assert [row["tool_call_id"] for row in parsed["tool_calls"]] == ["item_1", "item_2"]
    assert parsed["tool_calls"][0]["response"] == complete_one
    assert parsed["tool_calls"][1]["response_event_indices"] == [3]
    assert parsed["tool_calls"][1]["request_event_indices"] == [1, 3]
    assert "stdout" not in parsed["tool_calls"][0]["response"]
    assert [row["event"] for row in parsed["events"]] == events


@pytest.mark.parametrize("kind", ["command_execution", "file_change", "web_search", "mcp_tool_call"])
@pytest.mark.parametrize("identity", ["progress_1", None])
def test_codex_orphan_progress_is_incomplete_without_inventing_request_or_result(kind, identity):
    item = {"type": kind, "id": identity, "status": "in_progress", "tool": "read"}
    events = [{"type": "item.updated", "item": item}, {"type": "turn.completed"}]
    parsed = parse_cli_log(trace(events, transport="codex_cli"))
    assert not parsed["complete"]
    assert [row["event"] for row in parsed["events"]] == events
    if identity is None:
        assert "missing_tool_call_id:0" in parsed["issues"]
        assert parsed["tool_calls"] == []
    else:
        assert "unpaired_tool_call:progress_1" in parsed["issues"]
        row, = parsed["tool_calls"]
        assert row["tool_call_id"] == identity and row["status"] == "incomplete"
        assert row["request"] is None and row["response"] is None
        assert row["request_event_indices"] == row["response_event_indices"] == []


def test_codex_progress_then_self_contained_completion_retains_real_completion():
    item = {"type": "command_execution", "id": "c", "command": "echo ready",
            "aggregated_output": "", "exit_code": None, "status": "in_progress"}
    completed = {**item, "aggregated_output": "ready\n", "exit_code": 0, "status": "completed"}
    events = [{"type": "item.updated", "item": item},
              {"type": "item.completed", "item": completed}, {"type": "turn.completed"}]
    parsed = parse_cli_log(trace(events, transport="codex_cli"))
    assert parsed["complete"]
    row, = parsed["tool_calls"]
    assert row["status"] == "completed" and row["response"] == completed
    assert row["request_event_indices"] == row["response_event_indices"] == [1]


@pytest.mark.parametrize("kind,request_fields,status,limitation", [
    ("file_change", {"changes": [{"path": "a/added.txt", "kind": "add"},
                                  {"path": "c/modified.txt", "kind": "update"}]}, "completed", "provider_request_content_unavailable"),
    ("file_change", {"changes": [{"path": "file.txt", "kind": "update"}]}, "failed", "provider_request_content_unavailable"),
    ("web_search", {"query": "rust async await", "action": {"type": "search", "query": "rust async await"}},
     None, "provider_result_content_unavailable"),
])
def test_codex_completion_only_public_fields_do_not_invent_missing_tool_content(kind, request_fields, status, limitation):
    # Official protocol fixtures (not a real run of our installed CLI):
    # github.com/openai/codex/blob/1715e55076737158ba61d43158ede504de6d4ce1/
    # codex-rs/exec/tests/event_processor_with_json_output.rs:367,817,880.
    item = {"id": "item_0", "type": kind, **request_fields}
    if status:
        item["status"] = status
    parsed = parse_cli_log(trace([{"type": "item.completed", "item": item},
                                 {"type": "turn.completed"}], transport="codex_cli"))
    assert not parsed["complete"] and parsed["issues"] == [limitation + ":item_0"]
    row = parsed["tool_calls"][0]
    assert row["request"] == request_fields and row["response"] == item
    assert row["request_event_indices"] == row["response_event_indices"] == [0]
    assert row["status"] == ("failed" if status == "failed" else "completed")
    assert "diff" not in row["request"] and "results" not in row["response"]


def test_codex_search_start_empty_query_is_completed_by_actual_query():
    start = {"id": "s", "type": "web_search", "query": "", "action": {"type": "other"}}
    end = {**start, "query": "actual query", "action": {"type": "search", "query": "actual query"}}
    parsed = parse_cli_log(trace([{"type": "item.started", "item": start},
        {"type": "item.completed", "item": end}, {"type": "turn.completed"}], transport="codex_cli"))
    assert parsed["tool_calls"][0]["request"]["query"] == "actual query"
    assert parsed["issues"] == ["provider_result_content_unavailable:s"]


def test_codex_failed_turn_can_have_complete_failed_tool_records():
    item = {"id": "c", "type": "command_execution", "command": "false",
            "aggregated_output": "", "exit_code": 1, "status": "failed"}
    parsed = parse_cli_log(trace([{"type": "item.completed", "item": item},
        {"type": "turn.failed", "error": {"message": "provider failure"}}], transport="codex_cli"))
    assert parsed["complete"] and parsed["tool_calls"][0]["status"] == "failed"
    assert parsed["tool_calls"][0]["response"]["exit_code"] == 1


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


@pytest.mark.parametrize("with_progress", [False, True])
def test_codex_mcp_logs_preserve_real_command_results(with_progress):
    result = {"command_id": "unit", "allowed": True, "returncode": 0, "stdout": "153 passed\n", "stderr": ""}
    item = {"id": "call1", "type": "mcp_tool_call", "tool": "sandbox_command_execute",
            "arguments": {"command_id": "unit"}}
    events = [{"type": "item.started", "item": item},
        *([{"type": "item.updated", "item": {**item, "status": "in_progress"}}] if with_progress else []),
        {"type": "item.completed",
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


@pytest.mark.parametrize("phase", ["before_call", "launch_guard"])
def test_pending_default_sigint_does_not_start_a_new_cli(tmp_path, phase):
    driver = """
import json,os,signal,sys
from pathlib import Path
from unittest.mock import patch
from agent_runtime.invocation import invocation_process_execution as capture
previous=signal.getsignal(signal.SIGINT)
def guard(launch):
    os.kill(os.getpid(),signal.SIGINT)
    return launch()
with patch.object(capture.subprocess,'Popen',side_effect=AssertionError('cancelled CLI must not launch')) as start:
    try:
        with capture._capture_cli_interrupts():
            if sys.argv[1]=='before_call': os.kill(os.getpid(),signal.SIGINT)
            capture.run_cli_process(argv=[sys.executable,'-c','pass'],prompt='',cwd=Path.cwd(),
                environment=dict(os.environ),timeout_seconds=1,
                launch_guard=guard if sys.argv[1]=='launch_guard' else None)
    except capture.CliProcessInterrupted as exc:
        assert exc.stdout_bytes==exc.stderr_bytes==b'' and exc.returncode is None
        assert start.call_count==0
    else: raise AssertionError('expected interruption')
assert signal.getsignal(signal.SIGINT) is previous
print(json.dumps({'provider_processes':start.call_count,'cancelled':True}))
"""
    process = subprocess.run([sys.executable, "-c", driver, phase], cwd=tmp_path, capture_output=True, text=True,
        timeout=10, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {"provider_processes": 0, "cancelled": True}


def test_default_sigint_during_adapter_preflight_prevents_provider_launch(tmp_path):
    driver = """
import json,os,signal,sys
from pathlib import Path
import pytest
from agent_runtime import read_execution_log
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.invocation import invocation_process_execution as capture
from test_agent_runtime_execution_logging import native,_run_real_native_streams
previous=signal.getsignal(signal.SIGINT)
actual_run=claude.subprocess.run
actual_start=capture.subprocess.Popen
starts=[]
signals=[]
def preflight(argv,*args,**kwargs):
    result=actual_run(argv,*args,**kwargs)
    if argv[1]=='--help':
        signals.append(True)
        os.kill(os.getpid(),signal.SIGINT)
    return result
def start(argv,*args,**kwargs):
    if argv[0]==sys.executable:
        starts.append(True)
        raise AssertionError('provider process must not start after preflight cancellation')
    return actual_start(argv,*args,**kwargs)
with pytest.MonkeyPatch.context() as patch:
    patch.setattr(claude.subprocess,'run',preflight)
    patch.setattr(capture.subprocess,'Popen',start)
    run,cell=_run_real_native_streams(Path.cwd(),patch,b'')
log=read_execution_log(run.module_run,attempts=run.attempts,read_content=cell.read_bytes,include_private_content=True)
assert signals and not starts
assert log['attempts'][0]['status']=='cancelled'
assert log['attempts'][0]['failure_detail']['failure_code']=='claude_cli_interrupted'
assert signal.getsignal(signal.SIGINT) is previous
print(json.dumps({'provider_processes':0,'cancelled':True,'preflight_signal':True}))
"""
    project = Path(__file__).resolve().parents[1]
    process = subprocess.run([sys.executable, "-c", driver], cwd=tmp_path, capture_output=True, text=True,
        timeout=15, env={**os.environ, "PYTHONPATH": os.pathsep.join((str(project / "src"), str(project / "tests")))})
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {"provider_processes": 0, "cancelled": True, "preflight_signal": True}


@pytest.mark.parametrize("phase", ["cleanup", "join"])
@pytest.mark.parametrize("outcome", ["completed", "timeout", "output_limit"])
def test_first_real_interrupt_during_shutdown_preserves_bytes_and_restores_handler(tmp_path, phase, outcome):
    driver = """
import json,os,signal,sys
from pathlib import Path
from agent_runtime.invocation import invocation_process_execution as capture
previous=signal.getsignal(signal.SIGINT)
phase,outcome=sys.argv[1:]
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
    child="import os,time;os.write(1,b'before shutdown\\\\n');os.write(2,b'actual stderr')"
    options={}
    if outcome=='timeout': child+=';time.sleep(10)'
    if outcome=='output_limit':
        child="import os;os.write(1,b'a'*10000)"
        options['max_output_bytes']=64
    capture.run_cli_process(argv=[sys.executable,'-u','-c',
        child],prompt='',cwd=Path.cwd(),environment=dict(os.environ),timeout_seconds=1,**options)
except capture.CliProcessInterrupted as exc:
    print(json.dumps({'raw':exc.stdout_bytes.decode(),'stderr':exc.stderr_bytes.decode(),
        'stop':exc.stop_reason,'returncode':exc.returncode,
        'restored':signal.getsignal(signal.SIGINT) is previous,'cleanup_error':exc.cleanup_error,
        'prior':exc.prior_stop_reason}))
"""
    process = subprocess.run([sys.executable, "-c", driver, phase, outcome], cwd=tmp_path, capture_output=True, text=True,
        timeout=15, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")})
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["stop"] == "cancelled" and result["restored"] and result["cleanup_error"] is None
    assert result["prior"] == (None if outcome == "completed" else outcome)
    assert result["raw"] == ("a" * 64 if outcome == "output_limit" else "before shutdown\n")
    assert result["stderr"] == ("" if outcome == "output_limit" else "actual stderr")
    assert result["returncode"] is not None
    if outcome == "completed":
        assert result["returncode"] == 0


def _run_real_native_streams(tmp_path, monkeypatch, raw, stderr=b"", *, timeout=False):
    env = native._environment(tmp_path)
    _, registry, cell, request, authority = env
    def actual_process(**kwargs):
        options = {**kwargs, "argv": [sys.executable, "-c",
            f"import os,time;os.write(2,{stderr!r});os.write(1,{raw!r});time.sleep({10 if timeout else 0})"]}
        if timeout:
            options["timeout_seconds"] = 1
        return run_cli_process(**options)
    adapter = native.claude.ClaudeAdapter(
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
    return run, cell


def test_deep_event_from_real_process_cannot_prevent_adapter_trace_commit(tmp_path, monkeypatch):
    # CPython's C JSON decoder can have a higher nesting limit than Python
    # frames; exceed both in supported interpreters, without changing either.
    depth = max(10000, sys.getrecursionlimit() * 10)
    raw = json.dumps(native._init()).encode() + b'\n{"nested":' + b'[' * depth + b'0' + b']' * depth + b'}\n'
    run, cell = _run_real_native_streams(tmp_path, monkeypatch, raw)
    assert run.attempts[0].status == "failed"
    log = read_execution_log(run.module_run, attempts=run.attempts, read_content=cell.read_bytes,
                             include_private_content=True)
    attempt = log["attempts"][0]
    assert cli_stream_bytes(attempt["provider_log"], "stdout") == raw
    assert not attempt["complete"] and "invalid_event:1" in attempt["issues"]


def test_deep_final_result_keeps_exact_streams_tool_history_and_usage(tmp_path, monkeypatch):
    depth = max(10000, sys.getrecursionlimit() * 10)
    nested = '{"nested":' + '[' * depth + '0' + ']' * depth + '}'
    events = [native._init(), use("before_bad_result", "Read", file_path="material.txt"),
              reply("before_bad_result"), native._result(structured_output=None, result=nested)]
    raw = b"\n".join(json.dumps(event).encode() for event in events) + b"\n"
    stderr = b"actual diagnostics before invalid final output\n"
    run, cell = _run_real_native_streams(tmp_path, monkeypatch, raw, stderr)
    assert run.attempts[0].status == "failed" and run.attempts[0].failure_class == "schema"
    log = read_execution_log(run.module_run, attempts=run.attempts, read_content=cell.read_bytes,
                             include_private_content=True)
    attempt = log["attempts"][0]
    assert log["complete"]  # Complete captured log; invalid model output is separate.
    assert cli_stream_bytes(attempt["provider_log"], "stdout") == raw
    assert cli_stream_bytes(attempt["provider_log"], "stderr") == stderr
    assert attempt["provider_log"]["result"]["usage"] == events[-1]["usage"]
    assert [c["tool_call_id"] for c in attempt["tool_calls"]] == ["before_bad_result"]
    assert attempt["tool_calls"][0]["response"]["tool_result"] == events[2]["message"]["content"][0]


@pytest.mark.parametrize("outcome,phase", [
    ("success", "output_validation"), ("success", "normalization"), ("success", "trace_finalization"),
    ("success", "cleanup"), ("timeout", "normalization"), ("timeout", "failure_detail"),
    ("timeout", "trace_finalization"), ("timeout", "cleanup"),
    ("timeout", "process_cleanup"),
])
def test_default_sigint_after_capture_still_delivers_the_complete_attempt(tmp_path, outcome, phase):
    driver = """
import json,os,signal,sys
from pathlib import Path
import pytest
from agent_runtime import read_execution_log
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.invocation import invocation_process_execution as capture
from agent_runtime.invocation.invocation_cli_logging import cli_stream_bytes
from test_agent_runtime_execution_logging import native,use,reply,_run_real_native_streams
previous=signal.getsignal(signal.SIGINT)
assert previous is signal.default_int_handler
events=[native._init(),use('before_interrupt','Read',file_path='material.txt'),reply('before_interrupt'),native._result()]
raw=b'\\n'.join(json.dumps(event).encode() for event in events)+b'\\n'
stderr=b'actual captured stderr\\n'
phase,outcome=sys.argv[1:]
sent=[]
def send_once():
    if not sent:
        sent.append(True)
        os.kill(os.getpid(),signal.SIGINT)
with pytest.MonkeyPatch.context() as patch:
    adapter_results=[]
    original_execute=claude.ClaudeAdapter.execute
    def observe_result(*args,**kwargs):
        value=original_execute(*args,**kwargs)
        adapter_results.append(value)
        return value
    patch.setattr(claude.ClaudeAdapter,'execute',observe_result)
    if phase=='output_validation':
        original=claude.Draft202012Validator
        class InterruptingValidator:
            def __init__(self,*args,**kwargs): self.delegate=original(*args,**kwargs)
            def validate(self,value):
                send_once()
                return self.delegate.validate(value)
        patch.setattr(claude,'Draft202012Validator',InterruptingValidator)
    elif phase=='normalization':
        name='parse_cli_log'
        original=getattr(claude,name)
        def interrupt(*args,**kwargs):
            send_once()
            return original(*args,**kwargs)
        patch.setattr(claude,name,interrupt)
    else:
        owner=(capture if phase=='process_cleanup' else claude.tempfile.TemporaryDirectory
               if phase=='cleanup' else native.InMemoryCellArtifactStore)
        name={'cleanup':'cleanup','failure_detail':'commit_failure_detail','trace_finalization':'commit_attempt_trace',
              'process_cleanup':'_stop_process_group'}[phase]
        original=getattr(owner,name)
        def interrupt(*args,**kwargs):
            send_once()
            return original(*args,**kwargs)
        patch.setattr(owner,name,interrupt)
    run,cell=_run_real_native_streams(Path.cwd(),patch,raw,stderr,timeout=outcome=='timeout')
assert len(adapter_results)==1
assert adapter_results[0].failure.retry_disposition_id=='retry_denied'
assert adapter_results[0].outputs==()
log=read_execution_log(run.module_run,attempts=run.attempts,read_content=cell.read_bytes,include_private_content=True)
attempt=log['attempts'][0]
assert sent and log['complete'] is (outcome=='success')
assert cli_stream_bytes(attempt['provider_log'],'stdout')==raw
assert cli_stream_bytes(attempt['provider_log'],'stderr')==stderr
assert attempt['provider_log']['result']['usage']==events[-1]['usage']
assert attempt['tool_calls'][0]['tool_call_id']=='before_interrupt'
detail=json.loads(cell.read_bytes(run.attempts[0].failure_detail_ref,run.attempts[0].failure_detail_sha256))
assert detail==attempt['failure_detail']
assert detail['failure_class']=='cancelled' and detail['retryable'] is False
if outcome=='timeout':
    if phase=='process_cleanup':
        assert attempt['provider_log']['stop_reason']=='cancelled'
        assert attempt['provider_log']['prior_stop_reason']=='timeout'
    else:
        assert attempt['provider_log']['stop_reason']=='timeout'
        assert attempt['provider_log']['adapter_failure']['failure_class']=='timeout'
        assert attempt['provider_log']['adapter_failure']['retry_disposition_id']=='retry_allowed'
assert signal.getsignal(signal.SIGINT) is previous
print(json.dumps({'status':attempt['status'],'complete':log['complete'],'restored':True}))
"""
    project = Path(__file__).resolve().parents[1]
    process = subprocess.run([sys.executable, "-c", driver, phase, outcome], cwd=tmp_path, capture_output=True, text=True,
        timeout=15, env={**os.environ, "PYTHONPATH": os.pathsep.join((str(project / "src"), str(project / "tests")))})
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == {"status": "cancelled", "complete": outcome == "success", "restored": True}


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


@pytest.mark.parametrize("outcome", ["success", "timeout", "schema"])
@pytest.mark.parametrize("cancel", [False, True])
def test_cleanup_oserror_delivers_existing_trace_usage_and_failure(tmp_path, outcome, cancel):
    driver = """
import json,os,signal,sys
from pathlib import Path
import pytest
from agent_runtime import read_execution_log
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.invocation.invocation_cli_logging import cli_stream_bytes
from test_agent_runtime_execution_logging import native,use,reply,_run_real_native_streams
outcome,cancel=sys.argv[1:]
events=[native._init(),use('before_cleanup','Read',file_path='material.txt'),reply('before_cleanup'),
        native._result(**({'structured_output':{}} if outcome=='schema' else {}))]
raw=b'\\n'.join(json.dumps(event).encode() for event in events)+b'\\n'
stderr=b'actual diagnostic stream\\n'
original=claude.tempfile.TemporaryDirectory.cleanup
def fail_cleanup(instance):
    original(instance)
    if cancel=='yes': os.kill(os.getpid(),signal.SIGINT)
    raise OSError('fixture cleanup failure')
with pytest.MonkeyPatch.context() as patch:
    patch.setattr(claude.tempfile.TemporaryDirectory,'cleanup',fail_cleanup)
    run,cell=_run_real_native_streams(Path.cwd(),patch,raw,stderr,timeout=outcome=='timeout')
log=read_execution_log(run.module_run,attempts=run.attempts,read_content=cell.read_bytes,include_private_content=True)
attempt=log['attempts'][0]
assert attempt['status']==('cancelled' if cancel=='yes' else 'failed')
assert cli_stream_bytes(attempt['provider_log'],'stdout')==raw
assert cli_stream_bytes(attempt['provider_log'],'stderr')==stderr
assert attempt['provider_log']['result']['usage']==events[-1]['usage']
assert attempt['tool_calls'][0]['tool_call_id']=='before_cleanup'
detail=json.loads(cell.read_bytes(run.attempts[0].failure_detail_ref,run.attempts[0].failure_detail_sha256))
assert detail==attempt['failure_detail']
assert detail['failure_code']==('claude_cli_interrupted' if cancel=='yes' else 'claude_cli_cleanup_failed')
assert 'fixture cleanup failure' in detail['provider_error_message'] and detail['retryable'] is False
if outcome!='success': assert attempt['provider_log']['adapter_failure']['failure_class']==outcome
assert run.attempts[0].usage.input_tokens==7
print(json.dumps({'status':attempt['status'],'trace_preserved':True,'cleanup_failure_recorded':True}))
"""
    project = Path(__file__).resolve().parents[1]
    process = subprocess.run([sys.executable, "-c", driver, outcome, "yes" if cancel else "no"],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
        env={**os.environ, "PYTHONPATH": os.pathsep.join((str(project / "src"), str(project / "tests")))})
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["trace_preserved"] is True


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
