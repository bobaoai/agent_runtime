"""Local process tests, without model calls or user authentication."""

import os
import subprocess
import sys

import pytest

from agent_runtime.invocation.invocation_process_execution import run_cli_process
from agent_runtime.invocation import invocation_process_execution as cli_process


def test_launch_guard_orders_actual_popen_not_the_whole_invocation(tmp_path, monkeypatch):
    observed = []
    popen = cli_process.subprocess.Popen
    def launch(*args, **kwargs):
        observed.append("popen")
        return popen(*args, **kwargs)
    monkeypatch.setattr(cli_process.subprocess, "Popen", launch)
    def guard(create):
        observed.append("admitted")
        process = create()
        observed.append("guard_released")
        return process
    result = run_cli_process(argv=[sys.executable, "-c", "print('done')"], prompt="", cwd=tmp_path,
        timeout_seconds=5, environment=dict(os.environ), launch_guard=guard)
    assert result.returncode == 0 and observed == ["admitted", "popen", "guard_released"]
    def refuse(create):
        raise PermissionError("resources closed")
    with pytest.raises(PermissionError):
        run_cli_process(argv=[sys.executable, "-c", "print('must not run')"], prompt="", cwd=tmp_path,
            timeout_seconds=5, environment=dict(os.environ), launch_guard=refuse)
    assert observed.count("popen") == 1


def test_process_captures_both_streams_past_diagnostic_size(tmp_path):
    result = run_cli_process(
        argv=[sys.executable, "-c", "import sys; print('x'*100000); print('y'*100000,file=sys.stderr)"],
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
        run_cli_process(argv=[sys.executable, "-u", "-c", "import time; print('stop'); time.sleep(30)"],
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
    original_popen = subprocess.Popen
    def launch(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(cli_process.subprocess, "Popen", launch)
    def stop(line):
        assert processes[0].wait(timeout=3) == 0
        if observer_error:
            raise ValueError("observer fixture failure")
        return False
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_cli_process(argv=[sys.executable, "-u", "-c",
                "import sys; print('diagnostic',file=sys.stderr); print('finished')"],
            prompt="", cwd=tmp_path, timeout_seconds=5, environment=dict(os.environ), on_stdout_line=stop)
    assert error.value.returncode == processes[0].returncode == 0
    assert error.value.stop_reason == ("stream_error" if observer_error else "observer_stopped")
    assert error.value.stdout == "finished\n" and error.value.stderr == "diagnostic\n"
    if observer_error:
        assert "observer fixture failure" in str(error.value)


def test_output_limit_retains_real_exit_and_separate_diagnostic(tmp_path, monkeypatch):
    original_stop = cli_process._stop_process_group
    def stop_after_exit(process):
        assert process.wait(timeout=3) == 0
        original_stop(process)
    monkeypatch.setattr(cli_process, "_stop_process_group", stop_after_exit)
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_cli_process(argv=[sys.executable, "-u", "-c", "print('x'*100000)"], prompt="", cwd=tmp_path,
            timeout_seconds=5, max_output_bytes=1024, environment=dict(os.environ))
    assert error.value.returncode == 0
    assert error.value.stop_reason == "output_limit"
    assert error.value.stdout == "x" * 1024 and error.value.stderr == ""
    assert "log incomplete" in str(error.value)


@pytest.mark.parametrize("timed_out", [False, True])
def test_cleanup_failure_preserves_output_and_primary_failure(tmp_path, monkeypatch, timed_out):
    original_stop = cli_process._stop_process_group
    actual = []
    def failing_cleanup(process):
        original_stop(process)
        actual.append(process.returncode)
        raise RuntimeError("cleanup fixture failure")
    monkeypatch.setattr(cli_process, "_stop_process_group", failing_cleanup)
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


def test_unclosed_pipe_preserves_captured_diagnostic(tmp_path, monkeypatch):
    class ReportedAlive(cli_process.Thread):
        def is_alive(self):
            return True
    monkeypatch.setattr(cli_process, "Thread", ReportedAlive)
    with pytest.raises(subprocess.CalledProcessError) as error:
        run_cli_process(argv=[sys.executable, "-u", "-c", "print('pipe evidence')"], prompt="", cwd=tmp_path,
            timeout_seconds=5, environment=dict(os.environ))
    assert error.value.returncode == 0 and error.value.stop_reason == "cleanup_error"
    assert "pipe evidence" in error.value.stdout
    assert "pipe did not close" in error.value.cleanup_error


def test_timeout_is_not_replaced_by_late_observer_failure(tmp_path, monkeypatch):
    from threading import Event
    release_observer = Event()
    original_stop = cli_process._stop_process_group
    def cleanup(process):
        original_stop(process)
        release_observer.set()
    monkeypatch.setattr(cli_process, "_stop_process_group", cleanup)
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
    script = ("import sys,time\nfrom pathlib import Path\nprint('ready',flush=True)\n"
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
