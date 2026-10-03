"""Live CLI progress observation: bounded, non-persistent, never altering a run."""

import dataclasses
import os
import subprocess
import sys
import threading
import time

import pytest

from agent_runtime.invocation import invocation_process_execution as cli_process
from agent_runtime.invocation.invocation_process_execution import (
    CliEventSummary, CliProcessProgress, CliProgressChannel, run_cli_process,
)


class Recorder:
    """Observer that records delivered snapshots; can block until released."""

    def __init__(self, *, block_first=False, raise_first=False):
        self.snapshots, self.calls = [], 0
        self.release = threading.Event()
        self.entered = threading.Event()
        self._block_first, self._raise_first = block_first, raise_first

    def __call__(self, snapshot):
        self.calls += 1
        if self.calls == 1 and self._raise_first:
            raise RuntimeError("display failed")
        if self.calls == 1 and self._block_first:
            self.entered.set()
            self.release.wait(30)
        self.snapshots.append(snapshot)

    def triggers(self):
        return [item.update_trigger for item in self.snapshots]

    def wait_finished(self, timeout=5.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if any(item.update_trigger == "process_finished" for item in self.snapshots):
                return True
            time.sleep(0.01)
        return False


def _child(code):
    return [sys.executable, "-u", "-c", code]


def _run(tmp_path, code, *, channel=None, timeout_seconds=10, **options):
    """Return (kind, payload) so observed and unobserved outcomes compare exactly."""
    try:
        result = run_cli_process(argv=_child(code), prompt="", cwd=tmp_path, timeout_seconds=timeout_seconds,
                                 environment=dict(os.environ), progress_channel=channel, **options)
        return "returned", (result.returncode, result.stdout_bytes, result.stderr_bytes)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        return type(exc).__name__, (getattr(exc, "returncode", None), exc.stdout_bytes, exc.stderr_bytes,
                                    getattr(exc, "stop_reason", None))


def _comparable(outcome, case):
    """Output-limit exit codes race in the baseline runner (0 vs -9); compare the rest."""
    kind, payload = outcome
    return (kind, payload[1:]) if case == "output_limit" else outcome


LIFECYCLE = {
    "early_exit": ("import sys; sys.exit(3)", {}),
    "normal": ("import sys; print('out'); print('err', file=sys.stderr)", {}),
    "exit_one": ("import sys; print('x'); sys.exit(1)", {}),
    "output_limit": ("print('x' * 100000)", {"max_output_bytes": 1024}),
    "timeout": ("import time; time.sleep(30)", {"timeout_seconds": 1}),
}


@pytest.mark.deterministic
def test_snapshot_fields_are_exactly_the_safe_display_set():
    assert [field.name for field in dataclasses.fields(CliProcessProgress)] == [
        "update_trigger", "updates_dropped", "process_id", "process_running", "elapsed_seconds",
        "stdout_byte_count", "stderr_byte_count", "current_cli_event"]
    assert [field.name for field in dataclasses.fields(CliEventSummary)] == ["phase", "tool_category", "command_id"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        CliEventSummary("init_observed").phase = "x"


@pytest.mark.deterministic
def test_channel_and_runner_reject_invalid_observation_objects(tmp_path):
    with pytest.raises(TypeError):
        CliProgressChannel("not callable")
    with pytest.raises(TypeError):
        run_cli_process(argv=_child("pass"), prompt="", cwd=tmp_path, timeout_seconds=5,
                        environment=dict(os.environ), progress_channel=object())


@pytest.mark.real_run
def test_stalled_zero_byte_process_publishes_heartbeats(tmp_path, monkeypatch):
    monkeypatch.setattr(cli_process, "_PROGRESS_HEARTBEAT_SECONDS", 0.1)
    observer = Recorder()
    _run(tmp_path, "import time; time.sleep(1.5)", channel=CliProgressChannel(observer))
    assert observer.wait_finished()
    beats = [item for item in observer.snapshots if item.update_trigger == "heartbeat"]
    assert observer.triggers()[0] == "process_started" and observer.triggers()[-1] == "process_finished"
    assert "event_received" not in observer.triggers()
    assert all(item.stdout_byte_count == item.stderr_byte_count == 0 and item.current_cli_event is None
               for item in beats)
    # A heartbeat built as the child exits may truthfully report it stopped; judge only the stall window.
    stalled = [item for item in beats if item.elapsed_seconds < 1.4]
    assert len(stalled) >= 3 and all(item.process_running for item in stalled)
    elapsed = [item.elapsed_seconds for item in observer.snapshots]
    assert elapsed == sorted(elapsed)


@pytest.mark.real_run
@pytest.mark.parametrize("case", sorted(LIFECYCLE))
def test_lifecycle_publishes_start_and_finish_without_changing_results(tmp_path, case):
    code, options = LIFECYCLE[case]
    baseline = _run(tmp_path, code, **options)
    observer = Recorder()
    observed = _run(tmp_path, code, channel=CliProgressChannel(observer), **options)
    assert _comparable(observed, case) == _comparable(baseline, case)
    assert observer.wait_finished()
    first, last = observer.snapshots[0], observer.snapshots[-1]
    assert first.update_trigger == "process_started" and last.update_trigger == "process_finished"
    assert last.process_running is False and first.process_id == last.process_id
    captured = observed[1]
    assert (last.stdout_byte_count, last.stderr_byte_count) == (len(captured[1]), len(captured[2]))


@pytest.mark.real_run
def test_user_cancellation_is_unchanged_and_still_finishes(tmp_path):
    def cancel_after(delay):
        start = time.monotonic()
        return lambda: time.monotonic() - start > delay
    baseline = _run(tmp_path, "import time; time.sleep(30)", user_cancel_requested=cancel_after(0.3))
    observer = Recorder()
    observed = _run(tmp_path, "import time; time.sleep(30)", channel=CliProgressChannel(observer),
                    user_cancel_requested=cancel_after(0.3))
    assert observed[0] == baseline[0] == "CliProcessInterrupted"
    assert observed[1][1:] == baseline[1][1:]
    assert observer.wait_finished()


@pytest.mark.real_run
@pytest.mark.parametrize("case", ["normal", "timeout", "output_limit"])
def test_blocked_observer_is_not_waited_for_after_process_exit(tmp_path, case):
    code, options = LIFECYCLE[case]
    started = time.monotonic()
    baseline = _run(tmp_path, code, **options)
    baseline_seconds = time.monotonic() - started
    observer = Recorder(block_first=True)
    started = time.monotonic()
    observed = _run(tmp_path, code, channel=CliProgressChannel(observer), **options)
    observed_seconds = time.monotonic() - started
    try:
        assert observer.entered.is_set() and not observer.release.is_set()
        assert _comparable(observed, case) == _comparable(baseline, case)
        assert observed_seconds < baseline_seconds + 0.5
    finally:
        observer.release.set()


@pytest.mark.real_run
def test_events_drained_before_the_input_worker_starts_are_still_published(tmp_path, monkeypatch):
    """Controlled scheduling: hold the stdin worker's start until both fast stdout events are drained.

    A channel attached only after every worker started would see these events while no process is
    attached and publish nothing for them; attaching before any worker starts publishes both.
    """
    drained = threading.Event()
    real_thread = cli_process.Thread
    class HeldInputThread(real_thread):
        def start(self):
            target = getattr(self, "_target", None)
            if getattr(target, "__name__", "") == "write_input":
                assert drained.wait(10), "fast child output was not drained"
            return super().start()
    monkeypatch.setattr(cli_process, "Thread", HeldInputThread)
    request = CliEventSummary("tool_requested", "shell")
    result = CliEventSummary("tool_result_observed", "shell")
    code = "import sys\nsys.stdout.write('request\\nresult\\n'); sys.stdout.flush()"
    def publisher(channel_box):
        seen = []
        def on_line(line):
            seen.append(line)
            if channel_box:
                channel_box[0].event(request if line == "request" else result)
            if len(seen) == 2:
                drained.set()
            return True
        return on_line
    baseline = _run(tmp_path, code, on_stdout_line=publisher([]))
    drained.clear()
    observer = Recorder()
    channel = CliProgressChannel(observer)
    observed = _run(tmp_path, code, channel=channel, on_stdout_line=publisher([channel]))
    assert observed == baseline and observed[0] == "returned" and observed[1][0] == 0
    assert observed[1][1] == b"request\nresult\n"
    assert observer.wait_finished()
    received = [item.current_cli_event for item in observer.snapshots if item.update_trigger == "event_received"]
    assert received == [request, result]
    assert observer.triggers()[0] == "process_started" and observer.triggers()[-1] == "process_finished"


@pytest.mark.real_run
def test_blocked_observer_loss_is_counted_and_never_replayed(tmp_path):
    channel_box = {}
    observer = Recorder(block_first=True)
    channel = channel_box["channel"] = CliProgressChannel(observer)
    def on_line(line):
        channel_box["channel"].event(CliEventSummary("tool_requested", "shell"))
        return True
    code = "import time\nfor i in range(30): print(i)\nimport sys; sys.stdout.flush(); time.sleep(1.0)"
    releaser = threading.Thread(target=lambda: (observer.entered.wait(5), time.sleep(0.4), observer.release.set()))
    releaser.start()
    observed = _run(tmp_path, code, channel=channel, on_stdout_line=on_line)
    releaser.join()
    assert observed[0] == "returned" and observed[1][0] == 0
    assert observer.wait_finished()
    published = 1 + 30 + 1  # process_started, 30 events, process_finished; no heartbeat within 10 s
    assert len(observer.snapshots) + sum(item.updates_dropped for item in observer.snapshots) == published
    assert observer.snapshots[1].updates_dropped >= 1
    events = [item for item in observer.snapshots if item.update_trigger == "event_received"]
    assert len(events) <= 16


@pytest.mark.real_run
def test_concurrent_producers_never_mix_fields_or_deadlock(tmp_path):
    observer = Recorder()
    channel = CliProgressChannel(observer)
    stream = CliEventSummary("tool_result_observed", "read")
    hook = [CliEventSummary(phase, "declared_command", f"cmd_{index}")
            for index in range(40) for phase in ("declared_command_started", "declared_command_finished")]
    def on_line(line):
        channel.event(stream)
        return True
    def side():
        for summary in hook:
            channel.event(summary)
            time.sleep(0.002)
    worker = threading.Thread(target=side)
    code = "import time\nfor i in range(60):\n    print(i, flush=True); time.sleep(0.005)"
    worker.start()
    observed = _run(tmp_path, code, channel=channel, on_stdout_line=on_line)
    worker.join(5)
    assert observed[0] == "returned" and not worker.is_alive()
    assert observer.wait_finished()
    allowed = {stream, None, *hook}
    assert all(item.current_cli_event in allowed for item in observer.snapshots)
    counts = [item.stdout_byte_count for item in observer.snapshots]
    assert counts == sorted(counts)


@pytest.mark.real_run
@pytest.mark.parametrize("trigger", ["process_started", "event_received", "observation_stopped",
                                     "heartbeat", "process_finished"])
def test_channel_faults_never_change_the_run(tmp_path, monkeypatch, trigger):
    monkeypatch.setattr(cli_process, "_PROGRESS_HEARTBEAT_SECONDS", 0.05)
    original = CliProgressChannel._publish
    def faulty(self, name):
        if name == trigger:
            raise RuntimeError("injected channel fault")
        return original(self, name)
    monkeypatch.setattr(CliProgressChannel, "_publish", faulty)
    observer = Recorder()
    channel = CliProgressChannel(observer)
    def on_line(line):
        channel.event(CliEventSummary("tool_requested", "shell"))
        if line == "2":
            channel.stop_events()
        return True
    code = "import time\nfor i in range(4): print(i, flush=True); time.sleep(0.1)"
    for case_code in (code, LIFECYCLE["timeout"][0]):
        options = {"timeout_seconds": 1} if case_code != code else {}
        baseline = _run(tmp_path, case_code, on_stdout_line=lambda line: True, **options)
        observed = _run(tmp_path, case_code, channel=channel, on_stdout_line=on_line, **options)
        assert observed == baseline
        assert observed[0] in {"returned", "CliProcessTimeout"}
        channel = CliProgressChannel(observer)


@pytest.mark.real_run
def test_observer_that_raises_is_not_called_again(tmp_path):
    observer = Recorder(raise_first=True)
    baseline = _run(tmp_path, "print('a'); print('b')")
    observed = _run(tmp_path, "print('a'); print('b')", channel=CliProgressChannel(observer))
    time.sleep(0.2)
    assert observed == baseline and observer.calls == 1 and observer.snapshots == []


@pytest.mark.real_run
def test_without_channel_no_progress_thread_runs(tmp_path):
    seen = []
    def on_line(line):
        seen.append(any(item.name == "agent-runtime-cli-progress" for item in threading.enumerate()))
        return True
    _run(tmp_path, "print('a')", on_stdout_line=on_line)
    assert seen == [False]


@pytest.mark.real_run
def test_snapshots_carry_no_stream_content(tmp_path):
    observer = Recorder()
    code = "import sys; print('SENTINEL_OUT_7f3a'); print('SENTINEL_ERR_7f3a', file=sys.stderr)"
    channel = CliProgressChannel(observer)
    _run(tmp_path, code, channel=channel,
         on_stdout_line=lambda line: (channel.event(CliEventSummary("model_activity_observed")), True)[1])
    assert observer.wait_finished()
    assert all("SENTINEL" not in repr(item) for item in observer.snapshots)


# ---- A2: Claude stream summarizer, declared-command hook and Adapter wiring ----

import base64
import functools
import json
from pathlib import Path
from unittest.mock import patch

from agent_runtime.execution import AgentExecutionAdapterRegistry, InMemoryCellArtifactStore
from agent_runtime.execution.execution_module_invocation import run_module
from agent_runtime.foundation.foundation_json_encoding import decode_cli_event
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.invocation.invocation_local_command_execution import LOCAL_COMMAND_CLI_TOOL_NAME
from agent_runtime.ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger

import test_agent_runtime_claude_native_tools as native
import test_agent_runtime_local_commands as local_commands


class StubChannel:
    """Records what the summarizer publishes; deterministic, no process."""

    def __init__(self, *, fail=False):
        self.events, self.stops, self._fail = [], 0, fail

    def event(self, summary):
        if self._fail:
            raise RuntimeError("channel fault")
        self.events.append(summary)

    def stop_events(self):
        self.stops += 1


def _summarizer(*, native_output=True, callbacks=(), fail=False):
    channel = StubChannel(fail=fail)
    events = claude._ClaudeEventProgress(channel, native_structured_output=native_output)
    events.bind_callback_tools(callbacks)
    return events, channel


def _use(identity, name, **arguments):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": identity, "name": name,
                                                          "input": arguments}]}}


def _answer(identity, content="tool output"):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": identity,
                                                     "content": content}]}}


def _text(text="model text"):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


THINKING = {"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 12}
INIT = {"type": "system", "subtype": "init", "model": "claude-opus-5[1m]"}
FINAL = {"type": "result", "subtype": "success"}


def _phases(channel):
    return [(item.phase, item.tool_category, item.command_id) for item in channel.events]


@pytest.mark.deterministic
def test_categories_and_phases_follow_the_closed_table():
    events, channel = _summarizer(callbacks=("mcp__runtime_tools__lookup",))
    for event in [INIT, THINKING, THINKING, _text(), _use("a", "Read"), _answer("a"), _use("b", "Grep"),
                  _answer("b"), _use("c", "Bash"), _answer("c"), _use("d", LOCAL_COMMAND_CLI_TOOL_NAME, command_id="x"),
                  _answer("d"), _use("e", "mcp__runtime_tools__lookup"), _answer("e"), _use("f", "StructuredOutput"),
                  _answer("f"), _use("g", "WebFetch"), _answer("g"), {"type": "rate_limit_event"},
                  {"type": "stream_event"}, {"type": "system", "subtype": "status"}, FINAL]:
        events.record(event)
    assert _phases(channel) == [
        ("init_observed", None, None), ("model_activity_observed", None, None),
        ("tool_requested", "read", None), ("tool_result_observed", "read", None),
        ("tool_requested", "search", None), ("tool_result_observed", "search", None),
        ("tool_requested", "shell", None), ("tool_result_observed", "shell", None),
        ("tool_requested", "declared_command", None), ("tool_result_observed", "declared_command", None),
        ("tool_requested", "declared_callback", None), ("tool_result_observed", "declared_callback", None),
        ("tool_requested", "structured_output", None), ("tool_result_observed", "structured_output", None),
        ("tool_requested", "other_tool", None), ("tool_result_observed", "other_tool", None),
        ("final_result_observed", None, None)]
    prompt_only, channel = _summarizer(native_output=False)
    prompt_only.record(_use("h", "StructuredOutput"))
    assert _phases(channel) == [("tool_requested", "other_tool", None)]
    last_block, channel = _summarizer()
    last_block.record({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "i", "name": "Read"}, {"type": "tool_use", "id": "j", "name": "Bash"}]}})
    last_block.record(_answer("i"))
    assert _phases(channel) == [("tool_requested", "shell", None), ("tool_result_observed", "read", None)]


@pytest.mark.deterministic
def test_declared_command_summaries_carry_only_the_bound_identifier():
    events, channel = _summarizer()
    events.declared_command("liveness_probe", False)
    events.declared_command("liveness_probe", True)
    assert _phases(channel) == [("declared_command_started", "declared_command", "liveness_probe"),
                                ("declared_command_finished", "declared_command", "liveness_probe")]


@pytest.mark.deterministic
def test_result_categories_come_only_from_bounded_correlation():
    events, channel = _summarizer()
    events.record(_use("a", "Read"))
    events.record(_use("b", "Bash"))
    events.record(_answer("b"))
    events.record(_answer("a"))
    events.record(_answer("never_requested"))
    assert _phases(channel)[2:] == [("tool_result_observed", "shell", None), ("tool_result_observed", "read", None),
                                    ("tool_result_observed", "unknown", None)]
    assert events._pending == {}
    events.record(_use("oldest", "Grep"))
    for index in range(64):
        events.record(_use(f"newer_{index}", "Read"))
    assert len(events._pending) == 64 and "oldest" not in events._pending
    events.record(_answer("oldest"))
    assert channel.events[-1] == claude.CliEventSummary("tool_result_observed", "unknown")
    for index in range(64):
        events.record(_answer(f"newer_{index}"))
    assert events._pending == {}
    assert all("newer_" not in repr(item) and "oldest" not in repr(item) for item in channel.events)


@pytest.mark.deterministic
def test_malformed_nested_events_never_raise_and_publish_only_safe_facts():
    events, channel = _summarizer()
    cases = [
        ({"type": "assistant", "message": "not a dict"}, ("model_activity_observed", None, None)),
        ({"type": "assistant", "message": {"content": "not a list"}}, None),
        ({"type": "user", "message": {"content": "not a list"}}, None),
        ({"type": "assistant", "message": {"content": ["string block", None, 3]}}, None),
        ({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read"}]}},
         ("tool_requested", "read", None)),
        ({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": 7, "name": "Bash"}]}},
         ("tool_requested", "shell", None)),
        ({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": ["x"], "name": ["Read"]}]}},
         ("tool_requested", "other_tool", None)),
        ({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "", "name": 5}]}},
         ("tool_requested", "other_tool", None)),
        ({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": {"a": 1}}]}},
         ("tool_result_observed", "unknown", None)),
        ({"type": "user", "message": {"content": [{"type": "tool_result"}]}},
         ("tool_result_observed", "unknown", None)),
    ]
    for event, expected in cases:
        before = len(channel.events)
        events.record(event)
        published = _phases(channel)[before:]
        if expected is None:
            assert published == [] or published == [("model_activity_observed", None, None)]
        else:
            assert published == [expected]
    assert events._pending == {} and channel.stops == 0


@pytest.mark.deterministic
def test_summarizer_faults_stop_event_observation_once_and_never_raise(monkeypatch):
    events, channel = _summarizer()
    monkeypatch.setattr(claude._ClaudeEventProgress, "_summarize", lambda self, event: 1 / 0)
    events.record(INIT)
    events.record(INIT)
    events.declared_command("x", False)
    assert channel.stops == 1 and channel.events == []
    faulty, failing_channel = _summarizer(fail=True)
    faulty.declared_command("x", False)
    faulty.declared_command("x", True)
    assert failing_channel.stops == 1


def _stream_child(lines, *, pause=0.0, tail=0.0, stderr=None):
    """A local child printing stream-json lines; this is not a Claude Provider."""
    payload = json.dumps([json.dumps(line) for line in lines])
    code = (f"import sys, time, json\nlines = json.loads({payload!r})\n"
            + (f"sys.stderr.write({stderr!r}); sys.stderr.flush()\n" if stderr else "")
            + f"for line in lines:\n    print(line, flush=True); time.sleep({pause})\ntime.sleep({tail})\n")
    return code


def _observed_run(tmp_path, code, observer, *, native_output=True):
    channel = CliProgressChannel(observer)
    events = claude._ClaudeEventProgress(channel, native_structured_output=native_output)
    def on_line(line):
        try:
            event = decode_cli_event(line)
        except ValueError:
            return True
        events.record(event)
        return True
    return _run(tmp_path, code, channel=channel, on_stdout_line=on_line)


class TimedRecorder(Recorder):
    def __init__(self, **options):
        super().__init__(**options)
        self.started, self.arrivals = time.monotonic(), []

    def __call__(self, snapshot):
        super().__call__(snapshot)
        self.arrivals.append(time.monotonic() - self.started)


@pytest.mark.real_run
def test_short_tool_activity_is_delivered_on_receipt(tmp_path):
    observer = TimedRecorder()
    code = _stream_child([INIT, _use("t1", "Bash", command="SECRET"), _answer("t1"), FINAL], pause=0.3, tail=0.3)
    outcome = _observed_run(tmp_path, code, observer)
    assert outcome[0] == "returned" and observer.wait_finished()
    received = [(item, at) for item, at in zip(observer.snapshots, observer.arrivals)
                if item.update_trigger == "event_received"]
    assert [(item.current_cli_event.phase, item.current_cli_event.tool_category) for item, _ in received] == [
        ("init_observed", None), ("tool_requested", "shell"), ("tool_result_observed", "shell"),
        ("final_result_observed", None)]
    assert all(item.process_running and item.updates_dropped == 0 and at < 2.0 for item, at in received)
    assert "heartbeat" not in observer.triggers() and observer.triggers()[-1] == "process_finished"


@pytest.mark.real_run
def test_events_written_in_one_chunk_are_all_delivered_in_order(tmp_path):
    observer = Recorder()
    chunk = "\n".join(json.dumps(line) for line in [INIT, _use("g1", "Grep"), _answer("g1")])
    code = f"import sys, time\nsys.stdout.write({chunk!r} + '\\n'); sys.stdout.flush(); time.sleep(0.2)"
    assert _observed_run(tmp_path, code, observer)[0] == "returned"
    assert observer.wait_finished()
    received = [item for item in observer.snapshots if item.update_trigger == "event_received"]
    assert [(item.current_cli_event.phase, item.current_cli_event.tool_category) for item in received] == [
        ("init_observed", None), ("tool_requested", "search"), ("tool_result_observed", "search")]
    assert all(item.updates_dropped == 0 for item in observer.snapshots)


@pytest.mark.real_run
def test_delivered_snapshots_never_contain_event_content(tmp_path):
    observer = Recorder()
    thinking = {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "SENTINEL_THINK_91"}]}}
    lines = [INIT, thinking, _text("SENTINEL_TEXT_91"), _use("SENTINEL_ID_91", "Bash", command="SENTINEL_ARG_91"),
             _answer("SENTINEL_ID_91", "SENTINEL_OUTPUT_91"), FINAL]
    code = _stream_child(lines, pause=0.05, stderr="SENTINEL_STDERR_91\n")
    assert _observed_run(tmp_path, code, observer)[0] == "returned" and observer.wait_finished()
    assert observer.snapshots and all("SENTINEL" not in repr(item) for item in observer.snapshots)


@pytest.mark.real_run
def test_declared_command_hook_sees_only_bound_commands(tmp_path, monkeypatch):
    calls = []
    def hook(command_id, finished):
        calls.append((command_id, finished))
    monkeypatch.setattr(local_commands, "LocalCommandSession",
                        functools.partial(local_commands.LocalCommandSession, on_declared_command=hook))
    env = local_commands.session_fixture(tmp_path, [local_commands.command("ok", "print('ok')")])
    with env.session as session:
        assert session.invoke("ok")["returncode"] == 0
        assert session.invoke("unknown")["allowed"] is False
        session.validate_completion()
    assert calls == [("ok", False), ("ok", True)]


@pytest.mark.real_run
def test_raising_declared_command_hook_changes_nothing(tmp_path, monkeypatch):
    def normalized(session_records, responses):
        return ([(row["status"], row["request"], row["response"]["returncode"], row["response"]["stdout"])
                 for row in session_records],
                [(item["allowed"], item["returncode"], item["stdout"], item["failure"]) for item in responses])
    outcomes = []
    for hook in (None, lambda command_id, finished: 1 / 0):
        workspace = tmp_path / ("plain" if hook is None else "hooked")
        workspace.mkdir()
        factory = local_commands.LocalCommandSession if hook is None else functools.partial(
            local_commands.LocalCommandSession, on_declared_command=hook)
        monkeypatch.setattr(local_commands, "LocalCommandSession", factory)
        env = local_commands.session_fixture(workspace, [local_commands.command("ok", "print('ok')"),
                                                         local_commands.command("bad", "import sys; sys.exit(4)")])
        with env.session as session:
            responses = [session.invoke("ok"), session.invoke("bad"), session.invoke("unknown")]
            session.validate_completion()
            outcomes.append(normalized(session.records, responses))
    assert outcomes[0] == outcomes[1]


# ---- fake_run: same Adapter run with and without an observer (scripted local Claude CLI) ----

def _scripted_claude(directory, lines, *, exit_code=0, interrupt=False, flag=None):
    """Substitute named for these comparisons: a local script standing in for the Claude CLI."""
    stream = directory / "stream.json"
    stream.write_text(json.dumps({"lines": [line if isinstance(line, str) else json.dumps(line) for line in lines],
                                  "exit": exit_code, "interrupt": interrupt,
                                  "flag": None if flag is None else str(flag)}))
    executable = directory / "claude"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import json, os, signal, sys, time\n"
        "if '--version' in sys.argv or '--help' in sys.argv:\n"
        "    print('2.1.999 --safe-mode --restricted --tools --settings --effort --json-schema '\n"
        "          '--setting-sources --strict-mcp-config --add-dir'); sys.exit(0)\n"
        f"plan = json.load(open({str(stream)!r}))\n"
        "for line in plan['lines']:\n"
        "    print(line, flush=True); time.sleep(0.02)\n"
        "if plan['interrupt']:\n"
        "    os.kill(os.getppid(), signal.SIGINT); time.sleep(30)\n"
        "time.sleep(0.5)  # stay alive so a Runtime stop deterministically ends the process\n"
        "if plan['flag']:\n"
        "    open(plan['flag'], 'w').close()\n"
        "sys.exit(plan['exit'])\n")
    executable.chmod(0o700)
    return executable


def _adapter_run(directory, lines, *, observer=None, exit_code=0, interrupt=False):
    directory.mkdir(parents=True)
    compiled, registry, cell, request, authority = native._environment(directory)
    adapter = claude.ClaudeAdapter(
        release_registry=registry, artifact_host=cell, workspace_root=directory / "attempts",
        cli_path=_scripted_claude(directory, lines, exit_code=exit_code, interrupt=interrupt),
        adapter_binding=(compiled.execution_profile.executor_adapter_id,
                         compiled.execution_profile.executor_adapter_revision),
        **({} if observer is None else {"progress_observer": observer}))
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    temporary_directory = claude.tempfile.TemporaryDirectory
    with patch.object(claude.tempfile, "TemporaryDirectory",
                      lambda *args, **kw: temporary_directory(*args, **{**kw, "dir": directory})):
        run = run_module(request, release_registry=registry, adapters=adapters, artifact_host=cell,
                         ledger=InMemoryModuleExecutionLedger(), authority=authority, clock=lambda: native._TEST_TIME)
    attempt = run.attempts[-1]
    detail = (json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
              if attempt.failure_detail_ref else {})
    trace = (json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
             if attempt.provider_trace_ref else {})
    keys = ("initialization", "result", "response_models", "observed_response_models", "exit_code", "stop_reason",
            "event_error", "process_output_complete", "byte_capture_exact")
    streams = {name: base64.b64decode(value["data"]) for name, value in trace.get("raw_streams", {}).items()}
    outputs = [cell.read_bytes(item.output_ref, item.output_sha256) for item in run.outputs]
    return {"status": attempt.status, "failure_class": attempt.failure_class,
            "failure_code": detail.get("failure_code"), "outputs": outputs,
            "usage": attempt.usage.as_dict(), "trace": {key: trace.get(key) for key in keys}, "streams": streams}


MALFORMED = [{"type": "assistant", "message": {"content": "x"}},
             {"type": "assistant", "message": {"content": [None, {"type": "tool_use", "id": ["u"], "name": 3}]}},
             {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": {"x": 1}}]}}]
STREAMS = {
    "valid_with_malformed": ([native._init(), *MALFORMED, native._tool_use("r1"), native._tool_result("r1"),
                              native._result()], {}),
    "duplicate_result": ([native._init(), native._result(), native._result()], {}),
    "missing_init": ([native._result()], {}),
    "error_exit": ([native._init(), native._result(is_error=True, subtype="error_during_execution")],
                   {"exit_code": 1}),
    "user_cancellation": ([native._init()], {"interrupt": True}),
}


@pytest.mark.fake_run
@pytest.mark.parametrize("case", sorted(STREAMS))
@pytest.mark.parametrize("fault", [None, "summarizer", "channel"])
def test_adapter_terminal_result_is_identical_with_or_without_observer(tmp_path, monkeypatch, case, fault):
    lines, options = STREAMS[case]
    baseline = _adapter_run(tmp_path / "off", lines, **options)
    if fault == "summarizer":
        monkeypatch.setattr(claude._ClaudeEventProgress, "_summarize", lambda self, event: 1 / 0)
    if fault == "channel":
        monkeypatch.setattr(CliProgressChannel, "_publish", lambda self, trigger: 1 / 0)
    observer = Recorder()
    observed = _adapter_run(tmp_path / "on", lines, observer=observer, **options)
    assert observed == baseline
    if case == "user_cancellation":
        assert observed["status"] == "cancelled" and observed["failure_code"] == "claude_cli_interrupted"
    if case == "valid_with_malformed":
        assert observed["status"] == "completed"
    if fault is None:
        assert observer.wait_finished()
        assert "event_received" in observer.triggers()
    if fault == "summarizer":
        assert observer.wait_finished() and "observation_stopped" in observer.triggers()
        assert "event_received" not in observer.triggers()
    if fault == "channel":
        time.sleep(0.2)
        assert observer.snapshots == []


# ---- A3: Test Run attaches observation only for a resolved claude_cli transport ----

from agent_runtime import Module, RuntimeModulePlugin, register_runtime_module_plugin, run_local_workflow_test
from agent_runtime.execution import execution_run_budget
from agent_runtime.execution.execution_parameter_resolution import PARAMETER_FILE_FORMAT
from agent_runtime.invocation import invocation_codex_module_invocation as codex
from agent_runtime.registry import RuntimeReleaseRegistry

from test_agent_runtime_codex_self_test import environment, invoke as codex_invoke  # noqa: F401 (fixture)
from test_agent_runtime_module_authoring import SKILL_ID, _requirements, _task_project

WORKSPACE_PARAMETERS = ".runtime/execution_parameters/workspace.json"
WORKFLOW_PARAMETERS = ".runtime/execution_parameters/workflows/summarize_note.json"


def _parameter_file(root, relative, **values):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": PARAMETER_FILE_FORMAT, **values}))


def _claude_root(directory):
    source = _task_project(directory / "source", module_id="summarize_note")
    requirements = _requirements(execution_mode="tool_free", tool_policy=(), attempt_workspace_policy="none",
                                 output_constraint_mode="native_structured_output", max_attempts=1)
    module = Module.from_registration(source, skill_id=SKILL_ID, module_id="summarize_note",
                                      execution_requirements=requirements)
    workflow = Module.to_workflow(module.export(module_version="v1")).export()
    root = directory / "host"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        "observation_example", "v1", workflow.origin_bundle), root=root)
    return root


CLAUDE_LINES = [native._init(()), native._tool_use("r1", "Read"), native._tool_result("r1"),
                native._result(structured_output={"summary": "observed output"})]


def _normalized_record(record):
    return {key: record.get(key) for key in ("status", "output", "failure_class", "usage", "model", "effort",
                                            "execution_profile_ref", "persistence", "managed_runtime")} | {
        "failure_code": (record.get("failure_detail") or {}).get("failure_code")}


@pytest.mark.deterministic
def test_test_run_rejects_a_non_callable_observer_before_setup(tmp_path):
    root = tmp_path / "untouched"
    with pytest.raises(TypeError, match="progress_observer"):
        run_local_workflow_test(root, "summarize_note", input_payload={}, progress_observer="not callable")
    assert not (root / ".runtime").exists()


@pytest.mark.fake_run
@pytest.mark.parametrize("selection", ["explicit", "workspace_file"])
def test_codex_test_run_ignores_the_observer_and_is_unchanged(environment, monkeypatch, selection):
    """Substitutes: the existing Codex process-runner fixture; the observer must never be called."""
    root, _, _, calls, _, adapters = environment
    constructed = []
    current = codex.CodexCliModuleExecutor
    def recording(**fields):
        constructed.append(sorted(fields))
        return current(**fields)
    recording.executor_adapter_id, recording.executor_adapter_revision = (
        current.executor_adapter_id, current.executor_adapter_revision)
    monkeypatch.setattr(codex, "CodexCliModuleExecutor", recording)
    if selection == "workspace_file":
        _parameter_file(root, WORKSPACE_PARAMETERS, transport_kind="codex_cli", model_id="gpt-6-astra",
                        reasoning_profile="xhigh")
        run = lambda **extra: run_local_workflow_test(root, "summarize_note", input_payload={},
                                                     cli_path=Path(sys.executable), **extra)
    else:
        run = lambda **extra: codex_invoke(environment, **extra)
    observer = Recorder()
    plain = run()
    observed = run(progress_observer=observer)
    time.sleep(0.2)
    assert observer.calls == 0
    assert constructed[0] == constructed[1]
    assert sorted(calls[0]) == sorted(calls[1]) and "progress_channel" not in calls[1]
    assert _normalized_record(observed) == _normalized_record(plain)
    assert observed["status"] == "completed"


@pytest.mark.fake_run
@pytest.mark.parametrize("selection", ["explicit", "workflow_file", "workspace_file", "runtime_default"])
def test_test_run_attaches_claude_observation_after_transport_resolution(tmp_path, selection):
    """Substitute: a scripted local Claude executable; Test Run, ClaudeAdapter and process runner are real."""
    records, observers = [], []
    for observed in (False, True):
        directory = tmp_path / ("observed" if observed else "plain")
        directory.mkdir()
        root = _claude_root(directory)
        if selection == "workflow_file":
            _parameter_file(root, WORKFLOW_PARAMETERS, transport_kind="claude_cli")
        if selection == "workspace_file":
            _parameter_file(root, WORKSPACE_PARAMETERS, transport_kind="claude_cli")
        options = {"transport_kind": "claude_cli"} if selection == "explicit" else {}
        observer = Recorder()
        if observed:
            options["progress_observer"] = observer
        record = run_local_workflow_test(root, "summarize_note", input_payload={},
                                         cli_path=_scripted_claude(directory, CLAUDE_LINES), **options)
        observers.append(list(observer.snapshots))
        records.append(record)
    assert records[0]["status"] == "completed", records[0]["failure_detail"]
    assert _normalized_record(records[1]) == _normalized_record(records[0])
    assert observers[0] == []
    delivered = observers[1]
    assert delivered and delivered[0].update_trigger == "process_started"
    received = [item.current_cli_event for item in delivered if item.update_trigger == "event_received"]
    assert claude.CliEventSummary("tool_requested", "read") in received
    assert claude.CliEventSummary("tool_result_observed", "read") in received


@pytest.mark.fake_run
def test_blocked_display_cannot_turn_a_near_deadline_completion_into_timeout(tmp_path, monkeypatch):
    """Substitute: a scripted local Claude executable; the RunBudget clock is controlled at the boundary.

    When the scripted CLI is about to exit, the budget clock jumps so only 0.75 s of effective budget
    remains. A display that delayed _finish while its observer is blocked would cross that deadline.
    """
    results = []
    real_budget = execution_run_budget.RunBudget
    for observed in (False, True):
        directory = tmp_path / ("observed" if observed else "plain")
        directory.mkdir()
        flag = directory / "about_to_exit"
        state = {"offset": None, "deadline": None}
        def anchor_at_exit():
            # Anchor the jump when the CLI is about to exit, not when the budget is next read.
            while not flag.exists():
                time.sleep(0.002)
            state["offset"] = (state["deadline"] - 0.75) - time.monotonic()
        def clock():
            return time.monotonic() + (state["offset"] or 0.0)
        def budget(*args, **kwargs):
            created = real_budget(*args, **kwargs, clock=clock)
            state["deadline"] = created.deadline
            return created
        monkeypatch.setattr(execution_run_budget, "RunBudget", budget)
        observer = Recorder(block_first=True)
        options = {"progress_observer": observer} if observed else {}
        watcher = threading.Thread(target=anchor_at_exit, daemon=True)
        watcher.start()
        try:
            record = run_local_workflow_test(_claude_root(directory), "summarize_note", input_payload={},
                transport_kind="claude_cli", run_timeout_seconds=600,
                cli_path=_scripted_claude(directory, CLAUDE_LINES, flag=flag), **options)
            if observed:
                assert observer.entered.is_set() and not observer.release.is_set()
        finally:
            observer.release.set()
        assert state["offset"] is not None
        results.append(record)
    plain, observed_record = results
    assert plain["status"] == observed_record["status"] == "completed", observed_record["failure_detail"]
    assert _normalized_record(observed_record) == _normalized_record(plain)
    assert (observed_record.get("failure_detail") or {}).get("failure_code") not in {
        "provider_completed_after_deadline", "run_budget_expired_before_commit"}


# ---- Gated Provider case: actual Claude CLI through the public Test Run ----

import hashlib
import shutil

REAL_CLAUDE_GATE = pytest.mark.skipif(os.environ.get("AGENT_RUNTIME_REAL_RUN") != "1",
                                      reason="unverified: set AGENT_RUNTIME_REAL_RUN=1 with an authenticated Claude CLI")
# The Test Run record's top-level keys; observation must add none.
TEST_RUN_RECORD_KEYS = frozenset({
    "attempt_id", "effort", "execution", "execution_budget", "execution_log", "execution_parameter_sources",
    "execution_profile_ref", "execution_profile_sha256", "execution_trace", "execution_variant_ref",
    "execution_variant_sha256", "failure_class", "failure_detail", "input_bindings", "input_closure_sha256",
    "managed_runtime", "model", "module_release_ref", "module_release_sha256", "module_run_id", "output",
    "persistence", "provider_trace", "runtime_version", "self_test_binding", "status", "usage",
    "workflow_execution_id", "workflow_release_ref", "workflow_release_sha256"})
NATIVE_CATEGORIES = frozenset({"read", "search", "shell"})
PROBE_MATERIAL = b"live progress probe material 4d1e\n"


def _live_progress_probe_root(tmp_path):
    from agent_runtime.testing.conformance_agent_examples import _module, _object, _TEXT, _REVIEW_OUTPUT
    exported = Module.to_workflow(_module(
        "live_progress_probe",
        "First use the Read tool to read ../materials/source/note.txt. Then call the Runtime MCP tool "
        "mcp__runtime_commands__sandbox_command_execute exactly once with command_id `liveness_probe` and "
        "wait for its actual response. Use no other tools. Then return accepted=true with a one-line reason.",
        _object(task=_TEXT), _REVIEW_OUTPUT, tools=("read", "shell"), draft=True,
    )).export()
    root = tmp_path / "root"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        "live_progress_probe", "v1", exported.origin_bundle), root=root)
    materials = tmp_path / "materials"
    materials.mkdir()
    (materials / "note.txt").write_bytes(PROBE_MATERIAL)
    resources = dict(
        material_root=materials, read_only_dependencies=(),
        material_files=({"relative_path": "note.txt", "sha256": hashlib.sha256(PROBE_MATERIAL).hexdigest(),
                         "executable": False},),
        commands=({"command_id": "liveness_probe", "argv": [sys.executable, "-I", "-B", "-c",
                   "import time; time.sleep(2); print('liveness probe ok 4d1e')"],
                   "cwd": "scratch", "timeout_seconds": 60},))
    return root, exported.workflow_release.workflow_id, resources


@pytest.mark.real_run
@REAL_CLAUDE_GATE
def test_real_claude_test_run_delivers_live_tool_and_declared_command_progress(tmp_path):
    """Real entry: the installed Claude CLI on claude-opus-5-5 xhigh through the public Test Run.

    Snapshots stay in memory; this test writes no run record or process output to disk.
    """
    executable = os.environ.get("AGENT_RUNTIME_CLAUDE_BIN") or shutil.which("claude")
    if executable is None:
        pytest.skip("unverified: installed Claude CLI is unavailable")
    root, workflow_id, resources = _live_progress_probe_root(tmp_path)
    observer = TimedRecorder()
    record = run_local_workflow_test(
        root, workflow_id, input_payload={"task": "Read the note, run the declared probe once, then report."},
        transport_kind="claude_cli", model_id="claude-opus-5-5", reasoning_profile="xhigh",
        run_timeout_seconds=600, cli_path=executable, progress_observer=observer, **resources)
    assert record["status"] == "completed", record["failure_detail"]
    assert (record["model"], record["effort"]) == ("claude-opus-5-5", "xhigh")
    assert set(record) == TEST_RUN_RECORD_KEYS
    assert observer.wait_finished()
    snapshots = observer.snapshots
    finished = next(index for index, item in enumerate(snapshots) if item.update_trigger == "process_finished")
    live = [(index, item.current_cli_event) for index, item in enumerate(snapshots[:finished])
            if item.update_trigger == "event_received" and item.process_running]
    requests = [(index, event.tool_category) for index, event in live
                if event.phase == "tool_requested" and event.tool_category in NATIVE_CATEGORIES]
    results = [(index, event.tool_category) for index, event in live if event.phase == "tool_result_observed"]
    assert any(request_at < result_at and request_category == result_category
               for request_at, request_category in requests for result_at, result_category in results), live
    declared = {(event.phase, event.command_id) for _, event in live if event.tool_category == "declared_command"}
    assert {("declared_command_started", "liveness_probe"),
            ("declared_command_finished", "liveness_probe")} <= declared, live
    assert all("4d1e" not in repr(item) for item in snapshots)
