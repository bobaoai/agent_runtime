"""Local process tests, without model calls or user authentication."""

import os
import subprocess
import sys

import pytest

from agent_runtime.invocation.invocation_process_execution import run_cli_process
from agent_runtime.invocation import invocation_process_execution as cli_process


def test_resource_cancellation_is_not_user_interruption(tmp_path):
    from threading import Event, Timer
    closed = Event()
    timer = Timer(.25, closed.set)
    timer.start()
    try:
        with pytest.raises(cli_process.CliProcessError) as error:
            run_cli_process(argv=[sys.executable, "-u", "-c", "import time;print('running');time.sleep(20)"],
                prompt="", cwd=tmp_path, timeout_seconds=5, environment=dict(os.environ), cancel_requested=closed.is_set)
        assert error.value.stop_reason == "resource_closed" and "running" in error.value.stdout
        assert error.value.returncode != 0
        with pytest.raises(cli_process.CliProcessError) as before:
            run_cli_process(argv=["not-a-real-program"], prompt="", cwd=tmp_path, timeout_seconds=5,
                            environment=dict(os.environ), cancel_requested=closed.is_set)
        assert before.value.returncode is None and before.value.stdout_bytes == b""
    finally:
        timer.cancel()


@pytest.mark.real_run
def test_launch_guard_orders_actual_popen_not_the_whole_invocation(tmp_path, monkeypatch):
    observed = []
    create = cli_process.HOST_PLATFORM.create_cli_process
    def launch(argv, **kwargs):
        if argv[0] == sys.executable:
            observed.append("create")
        return create(argv, **kwargs)
    monkeypatch.setattr(cli_process.HOST_PLATFORM, "create_cli_process", launch)
    def guard(create):
        observed.append("admitted")
        process = create()
        observed.append("guard_released")
        return process
    result = run_cli_process(argv=[sys.executable, "-c", "print('done')"], prompt="", cwd=tmp_path,
        timeout_seconds=5, environment=dict(os.environ), launch_guard=guard)
    assert result.returncode == 0 and observed == ["admitted", "create", "guard_released"]
    def refuse(create):
        raise PermissionError("resources closed")
    with pytest.raises(PermissionError):
        run_cli_process(argv=[sys.executable, "-c", "print('must not run')"], prompt="", cwd=tmp_path,
            timeout_seconds=5, environment=dict(os.environ), launch_guard=refuse)
    assert observed.count("create") == 1


# Child fixtures write explicit bytes: Python's text-mode print writes CRLF on
# Windows, and Runtime captures the child's actual bytes without normalizing them.
def test_process_captures_both_streams_past_diagnostic_size(tmp_path):
    result = run_cli_process(
        argv=[sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'x'*100000+b'\\n');"
              "sys.stderr.buffer.write(b'y'*100000+b'\\n')"],
        prompt="", cwd=tmp_path, timeout_seconds=5, environment=dict(os.environ),
    )
    assert result.returncode == 0
    assert result.stdout == "x" * 100000 + "\n"
    assert result.stderr == "y" * 100000 + "\n"


def test_process_timeout_keeps_output_and_stops(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired) as error:
        run_cli_process(
            argv=[sys.executable, "-u", "-c", "import time; print('before timeout'); time.sleep(30)"],
            prompt="", cwd=tmp_path, timeout_seconds=1, environment=dict(os.environ),
        )
    assert "before timeout" in error.value.stdout


def test_process_output_bound_is_explicit_failure(tmp_path):
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_cli_process(
            argv=[sys.executable, "-u", "-c", "print('x'*100000)"], prompt="", cwd=tmp_path,
            timeout_seconds=5, max_output_bytes=1024, environment=dict(os.environ),
        )
    assert len(error.value.stdout.encode()) <= 1024
    assert error.value.stop_reason == "output_limit" and "log incomplete" in str(error.value)


def test_process_stdout_callback_stops_before_timeout(tmp_path):
    observed = []
    def stop(line):
        observed.append(line)
        return False
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_cli_process(argv=[sys.executable, "-u", "-c",
                "import sys,time; sys.stdout.buffer.write(b'stop\\n'); sys.stdout.flush(); time.sleep(30)"],
            prompt="", cwd=tmp_path, timeout_seconds=3, environment=dict(os.environ), on_stdout_line=stop)
    assert observed == ["stop"]
    assert error.value.stop_reason == "observer_stopped" and "stop" in error.value.stdout
    if os.name == "posix":
        assert error.value.returncode == -9
    else:
        assert error.value.returncode is not None


@pytest.mark.parametrize("observer_error", [False, True])
def test_observer_stop_retains_already_completed_process_exit(tmp_path, monkeypatch, observer_error):
    processes = []
    original_create = cli_process.HOST_PLATFORM.create_cli_process
    def launch(*args, **kwargs):
        process = original_create(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(cli_process.HOST_PLATFORM, "create_cli_process", launch)
    def stop(line):
        assert processes[0].wait(timeout=3) == 0
        if observer_error:
            raise ValueError("observer fixture failure")
        return False
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_cli_process(argv=[sys.executable, "-u", "-c",
                "import sys; sys.stderr.buffer.write(b'diagnostic\\n'); sys.stderr.flush();"
                "sys.stdout.buffer.write(b'finished\\n')"],
            prompt="", cwd=tmp_path, timeout_seconds=5, environment=dict(os.environ), on_stdout_line=stop)
    assert error.value.returncode == processes[0].returncode == 0
    assert error.value.stop_reason == ("stream_error" if observer_error else "observer_stopped")
    assert error.value.stdout == "finished\n" and error.value.stderr == "diagnostic\n"
    if observer_error:
        assert "observer fixture failure" in str(error.value)


def test_output_limit_retains_real_exit_and_separate_diagnostic(tmp_path, monkeypatch):
    original_stop = cli_process.HOST_PLATFORM.stop_cli_process
    def stop_after_exit(process):
        assert process.wait(timeout=3) == 0
        original_stop(process)
    monkeypatch.setattr(cli_process.HOST_PLATFORM, "stop_cli_process", stop_after_exit)
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_cli_process(argv=[sys.executable, "-u", "-c", "print('x'*100000)"], prompt="", cwd=tmp_path,
            timeout_seconds=5, max_output_bytes=1024, environment=dict(os.environ))
    assert error.value.returncode == 0
    assert error.value.stop_reason == "output_limit"
    assert error.value.stdout == "x" * 1024 and error.value.stderr == ""
    assert "log incomplete" in str(error.value)


@pytest.mark.parametrize("timed_out", [False, True])
def test_cleanup_failure_preserves_output_and_primary_failure(tmp_path, monkeypatch, timed_out):
    original_stop = cli_process.HOST_PLATFORM.stop_cli_process
    actual = []
    def failing_cleanup(process):
        original_stop(process)
        actual.append(process.returncode)
        raise RuntimeError("cleanup fixture failure")
    monkeypatch.setattr(cli_process.HOST_PLATFORM, "stop_cli_process", failing_cleanup)
    script = "import sys,time; print('before cleanup'); print('stderr evidence',file=sys.stderr)"
    if timed_out:
        script += "; time.sleep(30)"
    with pytest.raises(subprocess.TimeoutExpired if timed_out else subprocess.CalledProcessError) as error:
        run_cli_process(argv=[sys.executable, "-u", "-c", script], prompt="", cwd=tmp_path,
            timeout_seconds=1, environment=dict(os.environ))
    assert len(actual) == 1 and error.value.returncode == actual[0]
    assert error.value.stop_reason == ("timeout" if timed_out else "cleanup_error")
    assert error.value.cleanup_error == "cleanup fixture failure"
    assert "before cleanup" in error.value.stdout and "stderr evidence" in error.value.stderr


@pytest.mark.deterministic
def test_unclosed_pipe_is_a_log_gap_not_a_stop_failure(tmp_path, monkeypatch):
    class ReportedAlive(cli_process.Thread):
        def is_alive(self):
            return True
    monkeypatch.setattr(cli_process, "Thread", ReportedAlive)
    result = run_cli_process(argv=[sys.executable, "-u", "-c", "print('pipe evidence')"], prompt="", cwd=tmp_path,
        timeout_seconds=5, environment=dict(os.environ))
    # Stop of the started process is confirmed; only log completeness is unknown.
    assert result.returncode == 0 and "pipe evidence" in result.stdout
    assert result.output_streams_closed is False


def test_no_observer_call_or_capture_after_the_result_handoff(tmp_path):
    """A process the CLI started keeps the pipe and writes later: nothing reaches the caller after return."""
    import time
    child = tmp_path / "late_child.py"
    child.write_text("import sys, time\ntime.sleep(2.5)\nsys.stdout.write('late\\n')\nsys.stdout.flush()\n")
    root = tmp_path / "root.py"
    root.write_text("import subprocess, sys\nsys.stdout.write('early\\n')\nsys.stdout.flush()\n"
                    f"subprocess.Popen([sys.executable, {str(child)!r}], stdin=subprocess.DEVNULL,\n"
                    "                 stdout=sys.stdout.fileno(), stderr=subprocess.DEVNULL)\n")
    lines = []

    def observe(line):
        lines.append(line)
        return True

    result = run_cli_process(argv=[sys.executable, str(root)], prompt="", cwd=tmp_path, timeout_seconds=30,
                             environment=dict(os.environ), on_stdout_line=observe)
    delivered_at_return, captured_at_return = list(lines), result.stdout_bytes
    time.sleep(4)  # The held pipe delivers 'late' well after the bounded join and handoff.
    assert result.returncode == 0 and b"early" in captured_at_return and b"late" not in captured_at_return
    assert lines == delivered_at_return and not any("late" in line for line in lines)
    if os.name == "nt":  # POSIX stops the whole session group, so the child never writes there.
        assert result.output_streams_closed is False


def test_an_observer_still_running_at_handoff_is_a_stream_failure_not_a_result(tmp_path, monkeypatch):
    from threading import Event
    monkeypatch.setattr(cli_process, "_DELIVERY_FENCE_SECONDS", 0.5)
    release = Event()

    def observer(line):
        release.wait(timeout=10)
        return True

    try:
        with pytest.raises(cli_process.CliProcessError) as error:
            run_cli_process(argv=[sys.executable, "-u", "-c", "print('event')"], prompt="", cwd=tmp_path,
                            timeout_seconds=10, environment=dict(os.environ), on_stdout_line=observer)
    finally:
        release.set()
    assert error.value.stop_reason == "stream_error"
    assert "did not return before the result handoff" in str(error.value)


def test_timeout_is_not_replaced_by_late_observer_failure(tmp_path, monkeypatch):
    from threading import Event
    release_observer = Event()
    original_stop = cli_process.HOST_PLATFORM.stop_cli_process
    def cleanup(process):
        original_stop(process)
        release_observer.set()
    monkeypatch.setattr(cli_process.HOST_PLATFORM, "stop_cli_process", cleanup)
    def observer(line):
        assert release_observer.wait(timeout=4)
        raise ValueError("late observer failure")
    with pytest.raises(subprocess.TimeoutExpired) as error:
        run_cli_process(argv=[sys.executable, "-u", "-c", "import time; print('before timeout'); time.sleep(30)"],
            prompt="", cwd=tmp_path, timeout_seconds=1, environment=dict(os.environ), on_stdout_line=observer)
    assert error.value.stop_reason == "timeout"
    assert "before timeout" in error.value.stdout
    assert error.value.stream_error == "late observer failure"


def test_output_limit_preserves_late_stream_diagnostic(tmp_path, monkeypatch):
    from threading import Event
    events = []
    def capture_event():
        event = Event()
        events.append(event)
        return event
    monkeypatch.setattr(cli_process, "Event", capture_event)
    signal_file = tmp_path / "observer_entered"
    script = ("import sys,time\nfrom pathlib import Path\nsys.stdout.buffer.write(b'ready\\n'); sys.stdout.flush()\n"
              f"while not Path({str(signal_file)!r}).exists(): time.sleep(.001)\n"
              "sys.stderr.write('x'*100000); sys.stderr.flush()\n")
    def observer(line):
        signal_file.touch()
        assert events[0].wait(timeout=3)
        raise ValueError("stream diagnostic after output limit")
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_cli_process(argv=[sys.executable, "-u", "-c", script], prompt="", cwd=tmp_path,
            timeout_seconds=5, max_output_bytes=1024, environment=dict(os.environ), on_stdout_line=observer)
    assert error.value.stop_reason == "output_limit"
    assert error.value.stream_error == "stream diagnostic after output limit"
    assert error.value.stdout == "ready\n"
    assert error.value.stderr == "x" * (1024 - len("ready\n"))
