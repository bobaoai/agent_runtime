"""Codex terminal and captured-result regressions; no Provider or PG access."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agent_runtime import parse_cli_log, read_execution_log
from agent_runtime.invocation import invocation_codex_module_invocation as codex
from agent_runtime.foundation.foundation_json_encoding import cli_stream_bytes
from agent_runtime.invocation.invocation_process_execution import (
    CliProcessError, CliProcessInterrupted, CliProcessTimeout,
)
import test_agent_runtime_native_structured_output as native


def message(value="ok"):
    return {"type": "item.completed", "item": {
        "id": "answer", "type": "agent_message", "text": json.dumps({"value": value}),
    }}


def terminal(**usage):
    return {"type": "turn.completed", "usage": {
        "input_tokens": 12, "output_tokens": 5, "cached_input_tokens": 4,
        "cache_write_input_tokens": 2, **usage,
    }}


def raw_events(events):
    return b"\n".join(json.dumps(event).encode() for event in events) + b"\n"


def process(events=None, *, raw=None, stderr=b"", returncode=0, exact=True):
    raw = raw if raw is not None else raw_events(events or [message(), terminal()])
    fields = {"returncode": returncode, "stdout": raw.decode("utf-8", errors="replace"),
              "stderr": stderr.decode("utf-8", errors="replace")}
    if exact:
        fields.update(stdout_bytes=raw, stderr_bytes=stderr)
    return codex.CodexCliInvocationResult(**fields)


def environment(tmp_path, invocation, *, host=None, **compile_options):
    compile_options.setdefault("executor_adapter_revision", "v4")
    compiled = native._compile_native_module(tmp_path, **compile_options)
    registry = native._register_compiled_for_evaluation(compiled)
    artifacts = native.InMemoryCellArtifactStore()
    prompt = native._evaluation_prompt(artifacts, compiled, suffix="codex_outcome")
    calls = []

    def invoker(**fields):
        calls.append(fields)
        return invocation(fields)

    auth = tmp_path / "fixture_auth.json"
    auth.write_text("SYNTHETIC_CREDENTIAL_NOT_PARSED")
    executor = codex.CodexCliModuleExecutor(release_registry=registry, artifact_host=artifacts,
        workspace_root=tmp_path / "workspaces", invoker=invoker, codex_bin="never-executed-codex",
        auth_file=auth)
    request = native._direct_adapter_request(compiled, prompt, suffix="codex_outcome")
    return SimpleNamespace(compiled=compiled, registry=registry, artifacts=artifacts, prompt=prompt,
        calls=calls, executor=executor, request=request, host=host or native._RecordingHost())


def execute(env):
    result = env.executor.execute(env.request, env.host)
    trace = json.loads(env.artifacts.read_bytes(result.cell_local_trace_ref, result.cell_local_trace_sha256))
    return result, trace


def detail(env, result):
    return native._direct_failure_detail(result, env.artifacts)


@pytest.mark.parametrize("events", [
    [message()],
    [message(), terminal(), terminal()],
    [message(), terminal(), {"type": "turn.failed", "error": {"message": "failed"}}],
    [message(), terminal(), message("late")],
    [{"type": "turn.started"}, message(), {"type": "turn.started"}, terminal()],
    [message(), {"type": "thread.started", "thread_id": "other"}, terminal()],
])
def test_no_message_can_override_missing_or_ambiguous_terminal(tmp_path, events):
    env = environment(tmp_path, lambda _: process(events))
    result, trace = execute(env)
    assert result.terminal_status == "failed"
    assert result.failure.failure_class == "provider"
    assert detail(env, result)["failure_code"] == "codex_cli_result_missing_or_invalid"
    assert not env.host.staged and len(env.calls) == 1
    assert cli_stream_bytes(trace, "stdout") == raw_events(events)


@pytest.mark.parametrize("tool_event", [{"type": "item.completed", "item": None},
    {"type": "item.started", "item": "unknown shape"},
    {"type": "item.completed", "item": {"id": "tool", "type": "mcp_tool_call", "unknown": True}}])
@pytest.mark.parametrize("after_terminal", [False, True])
def test_tool_items_do_not_determine_turn_order_or_require_a_parser(tmp_path, monkeypatch, tool_event, after_terminal):
    from agent_runtime.inspection import inspection_execution_logging as inspection
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("ordinary execution must not parse detailed logs")
    monkeypatch.setattr(inspection, "parse_cli_log", forbidden)
    events = [{"type": "thread.started"}, {"type": "turn.started"}, message(), terminal()]
    events.insert(len(events) if after_terminal else 0, tool_event)
    env = environment(tmp_path, lambda _: process(events))
    result, trace = execute(env)
    assert result.terminal_status == "completed" and env.host.staged
    assert cli_stream_bytes(trace, "stdout") == raw_events(events)
    assert not trace["provider_stream_issues"] and "tool_log" not in trace and calls == []


@pytest.mark.parametrize("events,code", [
    ([message(), {"type": "turn.failed", "error": {"message": "stopped"}}], 0),
    ([message(), terminal()], 8),
])
def test_process_and_provider_failure_deny_output_independently(tmp_path, events, code):
    env = environment(tmp_path, lambda _: process(events, returncode=code, stderr=b"actual diagnostic"))
    result, trace = execute(env)
    assert result.failure.failure_class == "provider" and not result.outputs
    assert trace["returncode"] == code
    assert cli_stream_bytes(trace, "stderr") == b"actual diagnostic"
    assert detail(env, result)["transport_exit_code"] == code


@pytest.mark.parametrize("end,exit_code,bad_output,expected_failure", [
    (terminal(), 0, False, None),
    ({"type": "turn.failed", "error": {"message": "stopped"}}, 0, False, "codex_cli_error_result"),
    (terminal(), 7, False, "codex_cli_nonzero_exit"),
    (None, 0, False, "codex_cli_result_missing_or_invalid"),
    (terminal(), 0, True, "codex_cli_output_schema_violation"),
])
def test_top_level_error_preserves_the_actual_terminal_and_exit_result(tmp_path, end, exit_code, bad_output, expected_failure):
    """Codex 0.153.4 emits transient StreamError as top-level JSONL error.

    At upstream commit 3d2ee51ca2d5db578f328aa75e20aa22c0197c9a,
    app-server/src/bespoke_event_handling.rs sets will_retry=true; exec/src/
    event_processor_with_jsonl_output.rs omits that flag from the error event.
    exec/src/lib.rs determines failure from !will_retry or the turn terminal.
    These fixtures exercise the format without invoking a model or matching prose.
    """
    diagnostic = {"type": "error", "message": "Unclassified provider diagnostic"}
    answer = message()
    if bad_output:
        answer["item"]["text"] = '{"unexpected":true}'
    events = [{"type": "thread.started"}, {"type": "turn.started"}, diagnostic, answer]
    if end is not None:
        events.append(end)
    env = environment(tmp_path, lambda _: process(events, returncode=exit_code))
    result, trace = execute(env)
    assert cli_stream_bytes(trace, "stdout") == raw_events(events)
    assert trace["returncode"] == exit_code and len(env.calls) == 1
    if expected_failure is None:
        assert result.terminal_status == "completed" and result.failure is None
        assert json.loads(env.host.staged["result"]) == {"value": "ok"}
    else:
        assert result.terminal_status == "failed" and not result.outputs and not env.host.staged
        assert detail(env, result)["failure_code"] == expected_failure


@pytest.mark.parametrize("value", ["non_pass", "blocked", "passed"])
def test_business_values_and_nonfatal_item_error_do_not_change_technical_success(tmp_path, value):
    events = [{"type": "thread.started", "thread_id": "thread_one"}, {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "warning", "type": "error", "message": "recoverable"}},
        message(value), terminal()]
    env = environment(tmp_path, lambda _: process(events))
    result, trace = execute(env)
    assert result.terminal_status == "completed" and len(env.calls) == 1
    assert json.loads(env.host.staged["result"]) == {"value": value}
    assert result.input_tokens == 12 and result.cache_read_tokens == 4 and result.cache_creation_tokens == 2
    assert trace["provider_terminal"]["type"] == "turn.completed"


def test_startup_diagnostic_before_turn_is_not_prior_task_activity(tmp_path):
    """Reproduce the actual CLI 0.153.4 startup order from dev3 live smoke.

    The message is deliberately different from the observed feature advisory:
    nonfatal item semantics, not a message whitelist, determine this behavior.
    """
    advisory = {"type": "item.completed", "item": {"id": "notice", "type": "error",
                "message": "synthetic nonfatal startup diagnostic"}}
    events = [{"type": "thread.started", "thread_id": "one"}, advisory,
              {"type": "turn.started"}, message(), terminal()]
    env = environment(tmp_path, lambda _: process(events))
    result, trace = execute(env)
    assert result.terminal_status == "completed" and len(env.calls) == 1
    assert trace["provider_stream_issues"] == []
    assert cli_stream_bytes(trace, "stdout") == raw_events(events)
    assert advisory in [row["event"] for row in parse_cli_log(trace)["events"]]


@pytest.mark.parametrize("value", [True, -1, "12", "unknown", [], {}])
@pytest.mark.parametrize("failed", [False, True])
def test_bad_usage_is_not_coerced_or_allowed_to_hide_provider_failure(tmp_path, value, failed):
    end = terminal(input_tokens=value)
    if failed:
        end.update(type="turn.failed", error={"message": "real provider failure"})
    env = environment(tmp_path, lambda _: process([message(), end]))
    result, trace = execute(env)
    assert result.terminal_status == "failed" and result.input_tokens is None
    assert result.failure.failure_class == ("provider" if failed else "schema")
    assert trace["usage_issues"] == ["invalid_usage:input_tokens"]
    assert result.output_tokens == 5 and not env.host.staged


def test_missing_usage_stays_unknown_and_alias_conflict_is_reported(tmp_path):
    env = environment(tmp_path, lambda _: process([message(), {"type": "turn.completed"}]))
    result, _ = execute(env)
    assert result.terminal_status == "completed"
    assert (result.input_tokens, result.output_tokens, result.cache_read_tokens, result.cache_creation_tokens) == (None,) * 4
    parsed = codex._parse_codex_stream(raw_events([message(), terminal(cache_read_tokens=99)]))
    assert parsed["usage"]["cache_read_tokens"] is None
    assert parsed["usage_issues"] == ["invalid_usage:cache_read_tokens"]


@pytest.mark.parametrize("text,expected", [(None, "codex_cli_output_json_invalid"),
    ("not-json", "codex_cli_output_json_invalid"), ("[]", "codex_cli_output_json_invalid"),
    ('{"value":7}', "codex_cli_output_schema_violation"),
    ('{"value":"ok","extra":1}', "codex_cli_output_schema_violation")])
def test_valid_terminal_does_not_replace_canonical_output_validation(tmp_path, text, expected):
    answer = message()
    answer["item"]["text"] = text
    env = environment(tmp_path, lambda _: process([answer, terminal()]))
    result, trace = execute(env)
    assert detail(env, result)["failure_code"] == expected
    assert trace["provider_terminal"]["type"] == "turn.completed" and not env.host.staged


@pytest.mark.parametrize("fault", ["timeout", "output_limit", "stream_error", "cancelled"])
def test_capture_exceptions_keep_bytes_usage_and_original_stop_facts(tmp_path, fault):
    raw, err = raw_events([message(), terminal()]), b"stderr prefix\xff"

    def invoke(_):
        fields = dict(returncode=-9, output=raw.decode(), stderr=err.decode(errors="replace"),
                      stdout_bytes=raw, stderr_bytes=err)
        if fault == "timeout":
            raise CliProcessTimeout(["fixture"], 1, **fields)
        if fault == "cancelled":
            raise CliProcessInterrupted(**fields, prior_stop_reason="timeout")
        raise CliProcessError(cmd=["fixture"], stop_reason=fault, message="capture failure", **fields)

    env = environment(tmp_path, invoke)
    result, trace = execute(env)
    assert result.terminal_status == ("cancelled" if fault == "cancelled" else "failed")
    assert result.failure.failure_class == (fault if fault in {"timeout", "cancelled"} else "transport")
    assert not trace["process_output_complete"] and trace["byte_capture_exact"]
    assert cli_stream_bytes(trace, "stdout") == raw and cli_stream_bytes(trace, "stderr") == err
    assert trace["stop_reason"] == fault and result.input_tokens == 12
    assert not env.host.staged and len(env.calls) == 1
    if fault == "cancelled":
        assert trace["prior_stop_reason"] == "timeout"
        assert result.failure.retry_disposition_id == "retry_denied"


def test_standard_timeout_partial_bytes_are_retained_without_inferred_exit(tmp_path):
    raw = raw_events([{"type": "turn.started"}])

    def invoke(_):
        raise subprocess.TimeoutExpired(["fixture"], 1, output=raw, stderr=b"partial")

    env = environment(tmp_path, invoke)
    result, trace = execute(env)
    assert result.failure.failure_class == "timeout"
    assert cli_stream_bytes(trace, "stdout") == raw
    assert trace["returncode"] is None and result.input_tokens is None


@pytest.mark.parametrize("legacy", [False, True])
def test_private_raw_stream_is_distinct_from_bounded_redacted_display(tmp_path, legacy):
    events = [{"type": "item.completed", "item": {"id": "r", "type": "reasoning",
              "text": "PRIVATE_PROVIDER_SUMMARY"}}, message(), terminal()]
    raw, err = raw_events(events), b"x" * 100000 + b"\xff"
    env = environment(tmp_path, lambda _: process(raw=raw, stderr=err, exact=not legacy))
    result, trace = execute(env)
    assert result.terminal_status == "completed"
    assert trace["byte_capture_exact"] is not legacy
    assert "PRIVATE_PROVIDER_SUMMARY" not in trace["stdout"]
    assert b"PRIVATE_PROVIDER_SUMMARY" in cli_stream_bytes(trace, "stdout")
    assert trace["display_redacted"] and "[truncated]" in trace["stderr"]
    assert cli_stream_bytes(trace, "stderr") == (err if not legacy else err.decode(errors="replace").encode())


@pytest.mark.parametrize("phase", ["normalize", "stage"])
def test_post_capture_processing_failure_preserves_existing_evidence(tmp_path, monkeypatch, phase):
    def broken(*_args, **_kwargs):
        raise RuntimeError("synthetic processing failure")

    env = environment(tmp_path, lambda _: process())
    if phase == "normalize":
        monkeypatch.setattr(codex, "normalize_codex_native_output", broken)
    else:
        monkeypatch.setattr(env.host, "stage_output_bytes", broken)
    result, trace = execute(env)
    assert cli_stream_bytes(trace, "stdout") == raw_events([message(), terminal()])
    assert result.input_tokens == 12
    assert result.terminal_status == "failed" and not result.outputs
    assert detail(env, result)["failure_code"] == "ADAPTER_CONFORMANCE_FAILED"


@pytest.mark.parametrize("failed", [False, True])
def test_trace_is_committed_before_cleanup_failure(tmp_path, monkeypatch, failed):
    env = environment(tmp_path, lambda _: process(returncode=7 if failed else 0))
    committed = []
    original = env.artifacts.commit_attempt_trace

    def commit(**fields):
        committed.append(json.loads(fields["content"]))
        return original(**fields)

    @contextmanager
    def cleanup_failure(_workspace):
        yield
        assert len(committed) == 1
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(env.artifacts, "commit_attempt_trace", commit)
    monkeypatch.setattr(codex, "lease_attempt_workspace", cleanup_failure)
    result, trace = execute(env)
    assert result.terminal_status == "failed" and not result.outputs
    assert detail(env, result)["failure_code"] == "codex_cli_cleanup_failed"
    assert result.input_tokens == 12 and trace == committed[0]
    if failed:
        assert trace["adapter_failure"]["failure_code"] == "codex_cli_nonzero_exit"


def test_run_module_reads_failed_tool_and_replays_without_another_provider_call(tmp_path):
    # A requested operation was refused, not a successful undeclared capability.
    item = {"id": "refused", "type": "mcp_tool_call", "server": "fixture",
            "tool": "read", "arguments": {"path": "denied"}}
    events = [{"type": "item.started", "item": item},
        {"type": "item.completed", "item": {**item, "status": "failed", "error": {"message": "denied"}}},
        message("non_pass"), terminal()]
    env = environment(tmp_path, lambda _: process(events),
        output_resolution_policy=native.OutputResolutionPolicy.DIRECT_SINGLE)
    adapters = native.AgentExecutionAdapterRegistry()
    adapters.register(env.executor)
    request = native._evaluation_request(env.compiled, env.prompt, suffix="codex_outcome")
    authority, _ = native._evaluation_authority(env.registry, request)
    ledger = native.InMemoryModuleExecutionLedger()

    def run():
        return native.run_module(request, release_registry=env.registry, adapters=adapters,
            artifact_host=env.artifacts, ledger=ledger, authority=authority, clock=lambda: native._TEST_TIME)

    result = run()
    metadata = read_execution_log(result.module_run, attempts=result.attempts, read_content=env.artifacts.read_bytes)
    assert metadata["tool_calls"] is None
    log = read_execution_log(result.module_run, attempts=result.attempts, read_content=env.artifacts.read_bytes,
                             include_private_content=True)
    assert result.attempts[0].status == "completed" and len(env.calls) == 1
    assert log["tool_calls"][0]["status"] == "failed"
    assert log["tool_calls"][0]["tool_call_id"] == "refused"
    assert "grant_id" not in log["tool_calls"][0] and result.attempts[0].tool_calls == ()
    assert cli_stream_bytes(log["attempts"][0]["provider_log"], "stdout") == raw_events(events)
    assert run() == result and len(env.calls) == 1


def test_default_invoker_preserves_effective_arguments_and_environment(tmp_path, monkeypatch):
    captured = {}

    def runner(**fields):
        captured.clear()
        captured.update(fields)
        result = subprocess.CompletedProcess(fields["argv"], 0, "out", "err")
        result.stdout_bytes, result.stderr_bytes = b"out", b"err"
        return result

    monkeypatch.setattr(codex, "run_cli_process", runner)
    monkeypatch.setenv("RUNTIME_CODEX_TEST_SENTINEL", "unchanged")
    argv = ["codex-fixed", "exec", "--json", "-"]
    result = codex._default_invoke(argv=argv, prompt="same prompt", cwd=tmp_path, timeout_seconds=17,
                                   environment=dict(os.environ))
    assert captured == {"argv": argv, "prompt": "same prompt", "cwd": tmp_path,
                        "timeout_seconds": 17, "environment": dict(os.environ), "launch_guard": None}
    assert result.stdout_bytes == b"out" and result.stderr_bytes == b"err"


def test_default_invoker_real_local_process_preserves_large_non_utf8_streams(tmp_path):
    result = codex._default_invoke(argv=[sys.executable, "-I", "-B", "-c",
        "import os;os.write(1,b'a'*100000+b'\\xff');os.write(2,b'b'*100000+b'\\xfe')"],
        prompt="", cwd=tmp_path, timeout_seconds=5, environment=dict(os.environ))
    assert result.returncode == 0
    assert result.stdout_bytes == b"a" * 100000 + b"\xff"
    assert result.stderr_bytes == b"b" * 100000 + b"\xfe"


@pytest.mark.parametrize("kind", ["checked_exit", "unexpected"])
def test_exception_with_captured_streams_keeps_original_evidence(tmp_path, kind):
    raw = raw_events([message(), terminal()])

    def invoke(_):
        if kind == "checked_exit":
            raise subprocess.CalledProcessError(3, ["fixture"], output=raw, stderr=b"actual stderr")
        error = RuntimeError("invoker contract broke after capture")
        error.stdout_bytes, error.stderr_bytes = raw, b"actual stderr"
        raise error

    env = environment(tmp_path, invoke)
    result, trace = execute(env)
    assert result.failure.failure_class == ("provider" if kind == "checked_exit" else "unknown")
    assert cli_stream_bytes(trace, "stdout") == raw
    assert cli_stream_bytes(trace, "stderr") == b"actual stderr"
    assert result.input_tokens == 12 and not result.outputs


def test_output_permission_failure_preserves_trace_and_its_owner(tmp_path, monkeypatch):
    env = environment(tmp_path, lambda _: process())

    def refused(*_args):
        raise PermissionError("output resource is no longer available")

    monkeypatch.setattr(env.host, "stage_output_bytes", refused)
    result, trace = execute(env)
    assert result.failure.failure_class == "authorization"
    assert detail(env, result)["failure_code"] == "output_authorization_refused"
    assert result.input_tokens == 12 and trace["provider_terminal"]["type"] == "turn.completed"


@pytest.mark.parametrize("phase", ["normalize", "cleanup"])
def test_late_cancellation_preserves_committed_provider_trace(tmp_path, monkeypatch, phase):
    interrupted = SimpleNamespace(requested=False)

    @contextmanager
    def interrupt_scope():
        yield interrupted

    env = environment(tmp_path, lambda _: process())
    monkeypatch.setattr(codex, "_capture_cli_interrupts", interrupt_scope)
    if phase == "normalize":
        original = codex.normalize_codex_native_output

        def normalize(**fields):
            interrupted.requested = True
            return original(**fields)

        monkeypatch.setattr(codex, "normalize_codex_native_output", normalize)
    else:
        @contextmanager
        def lease(_workspace):
            yield
            interrupted.requested = True

        monkeypatch.setattr(codex, "lease_attempt_workspace", lease)
    result, trace = execute(env)
    assert result.terminal_status == "cancelled" and not result.outputs
    assert result.failure.retry_disposition_id == "retry_denied"
    assert detail(env, result)["failure_code"] == "codex_cli_interrupted"
    assert cli_stream_bytes(trace, "stdout") == raw_events([message(), terminal()])
    assert result.input_tokens == 12 and len(env.calls) == 1


@pytest.mark.parametrize("outcome", ["timeout", "cancelled"])
def test_real_process_failure_delivers_captured_adapter_result(tmp_path, outcome):
    root = Path(__file__).resolve().parents[1]
    script = f"""
import json, sys
from pathlib import Path
sys.path[:0] = [{str(root / 'src')!r}, {str(root / 'tests')!r}]
import test_agent_runtime_codex_execution_outcomes as case
raw = case.raw_events([case.message(), case.terminal()])
child = 'import os,signal,time;os.write(1,' + repr(raw) + ');os.write(2,b"before stop\\\\xff");'
if {outcome!r} == 'cancelled':
    child += 'time.sleep(.1);os.kill(os.getppid(),signal.SIGINT);'
child += 'time.sleep(30)'
def invocation(fields):
    return case.codex._default_invoke(argv=[sys.executable,'-I','-B','-c',child],
        prompt=fields['prompt'],cwd=fields['cwd'],timeout_seconds=1,
        environment=fields['environment'],launch_guard=fields['launch_guard'])
env = case.environment(Path.cwd(), invocation)
result, trace = case.execute(env)
assert case.cli_stream_bytes(trace,'stdout') == raw
print(json.dumps({{'status':result.terminal_status,'failure':result.failure.failure_class,
    'stderr':list(case.cli_stream_bytes(trace,'stderr')),'usage':result.input_tokens,
    'complete':trace['process_output_complete'],'exact':trace['byte_capture_exact'],
    'calls':len(env.calls)}}))
"""
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", script], cwd=tmp_path,
                               capture_output=True, text=True, timeout=10)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {"status": "cancelled" if outcome == "cancelled" else "failed",
        "failure": outcome, "stderr": list(b"before stop\xff"), "usage": 12,
        "complete": False, "exact": True, "calls": 1}


@pytest.mark.parametrize("item", [
    {"id": "call", "type": "command_execution", "command": "true", "status": "completed",
     "exit_code": 0, "aggregated_output": ""},
    {"id": "call", "type": "command_execution", "command": "false", "status": "failed",
     "exit_code": 1, "aggregated_output": "command failed"},
    {"id": "call", "type": "mcp_tool_call", "server": "fixture", "tool": "read",
     "arguments": {"path": "file"}, "status": "completed", "result": {"content": []}},
    {"id": "call", "type": "file_change", "changes": [{"path": "file", "kind": "add"}],
     "status": "completed"},
    {"id": "call", "type": "web_search", "query": "fixture query", "action": {"type": "search"}},
])
def test_tool_events_do_not_override_the_final_result(tmp_path, item):
    events = ([{"type": "item.started", "item": {**item, "status": "in_progress"}}]
              if item["type"] == "mcp_tool_call" else [])
    events += [{"type": "item.completed", "item": item}, message(), terminal()]
    env = environment(tmp_path, lambda _: process(events))
    result, trace = execute(env)
    assert result.terminal_status == "completed" and result.failure is None
    assert "tool_log" not in trace and "undeclared_tool_effects" not in trace
    assert cli_stream_bytes(trace, "stdout") == raw_events(events)
    assert trace["provider_terminal"]["type"] == "turn.completed"
    assert env.host.staged and len(env.calls) == 1


@pytest.mark.parametrize("status,exit_code", [("declined", None), ("failed", None)])
def test_refused_or_unknown_command_execution_does_not_prove_a_tool_effect(tmp_path, status, exit_code):
    item = {"id": "call", "type": "command_execution", "command": "fixture command",
            "status": status, "exit_code": exit_code, "aggregated_output": "not available"}
    env = environment(tmp_path, lambda _: process([
        {"type": "item.completed", "item": item}, message(), terminal()]))
    result, trace = execute(env)
    assert result.terminal_status == "completed"
    assert "undeclared_tool_effects" not in trace
    assert parse_cli_log(trace)["tool_calls"][0]["status"] == "failed"


def test_ambiguous_tool_records_do_not_create_execution_facts(tmp_path):
    item = {"id": "call", "type": "command_execution", "command": "true",
            "status": "completed", "exit_code": 0, "aggregated_output": ""}
    event = {"type": "item.completed", "item": item}
    env = environment(tmp_path, lambda _: process([event, event, message(), terminal()]))
    result, trace = execute(env)
    assert result.terminal_status == "completed"
    assert "undeclared_tool_effects" not in trace
    inspected = parse_cli_log(trace)
    assert not inspected["complete"]
    assert inspected["tool_calls"][0]["status"] == "incomplete"


def test_actual_extra_tool_and_later_provider_failure_both_remain_visible(tmp_path):
    item = {"id": "call", "type": "command_execution", "command": "false",
            "status": "failed", "exit_code": 1, "aggregated_output": "failed after launch"}
    events = [{"type": "item.completed", "item": item},
              {"type": "turn.failed", "error": {"message": "provider stopped"}}]
    env = environment(tmp_path, lambda _: process(events))
    result, trace = execute(env)
    assert result.failure.failure_class == "provider" and result.failure.retry_disposition_id == "retry_allowed"
    assert trace["provider_terminal"]["type"] == "turn.failed"
    assert "undeclared_tool_effects" not in trace
    assert parse_cli_log(trace)["tool_calls"][0]["response"]["exit_code"] == 1
    assert not result.outputs and len(env.calls) == 1


def test_declined_command_with_exit_code_is_a_visible_conflict_not_inferred_execution(tmp_path):
    item = {"id": "call", "type": "command_execution", "command": "fixture",
            "status": "declined", "exit_code": 0, "aggregated_output": ""}
    env = environment(tmp_path, lambda _: process([
        {"type": "item.completed", "item": item}, message(), terminal()]))
    result, trace = execute(env)
    assert result.terminal_status == "completed"
    assert "undeclared_tool_effects" not in trace
    inspected = parse_cli_log(trace)
    assert "conflicting_tool_results:call" in inspected["issues"]
    assert inspected["tool_calls"][0]["status"] == "incomplete"


def test_non_result_stdout_bytes_are_archived_without_rejecting_the_final_result(tmp_path):
    raw = b"\xff\xfe\n" + raw_events([message(), terminal()])
    env = environment(tmp_path, lambda _: process(raw=raw))
    result, trace = execute(env)
    assert result.terminal_status == "completed" and env.host.staged
    assert cli_stream_bytes(trace, "stdout") == raw and trace["byte_capture_exact"]
    assert not trace["provider_stream_issues"]
    assert not parse_cli_log(trace)["complete"]


# Codex rust-v0.153.4, commit 3d2ee51ca2d5db578f328aa75e20aa22c0197c9a:
# protocol/src/openai_models.rs:50-62,131-146 has these named values plus Custom.
# This is CLI syntax coverage, not a claim that every model advertises each value.
@pytest.mark.parametrize("effort", [
    "none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "persistent",
    "automatic", "model_custom", "future:v2",
])
def test_codex_named_and_custom_effort_preserve_exact_profile_and_command(tmp_path, effort):
    compiled = native._compile_native_module(tmp_path, executor_adapter_revision="v4", reasoning_profile=effort)
    profile = compiled.execution_profile
    before = profile.as_dict()
    assert codex._execution_expectation(profile).executor_adapter_revision == "v4"
    command = codex.build_command(profile=profile, workspace=tmp_path, codex_bin="not-invoked")
    settings = [command[index + 1] for index, value in enumerate(command) if value == "-c"]
    assert "model_reasoning_effort=" + json.dumps(effort) in settings
    assert type(profile).from_dict(before).as_dict() == before == profile.as_dict()


@pytest.mark.parametrize("effort", ["max", "ultra", "automatic", "model_custom"])
def test_direct_codex_adapter_preserves_named_and_custom_effort(tmp_path, effort):
    env = environment(tmp_path, lambda _: process(), reasoning_profile=effort)
    before = env.compiled.execution_profile.as_dict()
    result, trace = execute(env)
    assert result.terminal_status == "completed" and len(env.calls) == 1
    command = env.calls[0]["argv"]
    settings = [command[index + 1] for index, value in enumerate(command) if value == "-c"]
    assert "model_reasoning_effort=" + json.dumps(effort) in settings
    assert trace["effort"] == effort and env.compiled.execution_profile.as_dict() == before


@pytest.mark.parametrize("revision", ["v3", "v4"])
def test_historical_codex_effort_is_not_reclassified_during_profile_read(tmp_path, revision):
    profile = native._compile_native_module(tmp_path, executor_adapter_revision=revision,
                                             reasoning_profile="ultra").execution_profile
    document = profile.as_dict()
    restored = type(profile).from_dict(json.loads(json.dumps(document)))
    assert restored.as_dict() == document and restored.release_sha256 == profile.release_sha256
