"""Bounded local CLI process execution shared by provider adapters."""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
from threading import Event, Thread, current_thread, main_thread
from typing import Callable


DEFAULT_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024


class _CliInterruptState:
    def __init__(self):
        self.requested = False

    def __call__(self, signum, frame):
        self.requested = True


@contextmanager
def _capture_cli_interrupts():
    """Share default SIGINT capture across an Adapter and its nested process.

    The Adapter keeps this scope until its result/trace has been finalized. A
    nested process sees the same request immediately and performs group cleanup.
    Callers retain the completed record if cancellation arrives after finalization
    has begun, rather than discarding captured output during trace serialization.
    Custom signal policies and non-main threads are not replaced.
    """
    previous = signal.getsignal(signal.SIGINT) if current_thread() is main_thread() else None
    if isinstance(previous, _CliInterruptState):
        yield previous
        return
    state = _CliInterruptState()
    installed = previous is signal.default_int_handler
    if installed:
        signal.signal(signal.SIGINT, state)
    try:
        yield state
    finally:
        if installed:
            signal.signal(signal.SIGINT, previous)


class CliProcessError(subprocess.CalledProcessError):
    """Runtime capture failure, separate from the process's actual exit status."""

    def __init__(self, returncode: int | None, cmd, *, stop_reason: str, message: str,
                 output: str, stderr: str, cleanup_error: str | None = None,
                 stream_error: str | None = None, stdout_bytes: bytes | None = None,
                 stderr_bytes: bytes | None = None) -> None:
        super().__init__(returncode, cmd, output=output, stderr=stderr)
        self.stop_reason = stop_reason
        self.cleanup_error = cleanup_error
        self.stream_error = stream_error
        self.message = message
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes

    def __str__(self) -> str:
        return f"{self.message} (process exit code: {self.returncode})"


class CliProcessTimeout(subprocess.TimeoutExpired):
    """Keep the timeout interface while retaining observed cleanup/exit facts."""

    def __init__(self, cmd, timeout, *, returncode: int | None, output: str, stderr: str,
                 cleanup_error: str | None = None, stream_error: str | None = None,
                 stdout_bytes: bytes | None = None, stderr_bytes: bytes | None = None) -> None:
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.returncode = returncode
        self.stop_reason = "timeout"
        self.cleanup_error = cleanup_error
        self.stream_error = stream_error
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes

    def __str__(self) -> str:
        message = super().__str__()
        if self.stream_error:
            message += "; stream processing: " + self.stream_error
        if self.cleanup_error:
            message += "; cleanup: " + self.cleanup_error
        return message


class CliProcessInterrupted(KeyboardInterrupt):
    """A user interruption with the actual captured streams after group cleanup."""

    def __init__(self, *, returncode, output, stderr, stdout_bytes, stderr_bytes,
                 cleanup_error=None, stream_error=None):
        super().__init__("CLI process interrupted")
        self.returncode = returncode
        self.output = self.stdout = output
        self.stderr = stderr
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes
        self.cleanup_error = cleanup_error
        self.stream_error = stream_error
        self.stop_reason = "cancelled"


def _stop_process_group(process: subprocess.Popen) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif process.poll() is None:
        process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("CLI process did not stop after cleanup") from exc


def _run_cli_process(
    *, argv: list[str], prompt: str, cwd: Path, timeout_seconds: int,
    environment: dict[str, str], max_output_bytes: int = DEFAULT_PROCESS_OUTPUT_BYTES,
    on_stdout_line: Callable[[str], bool] | None = None,
    launch_guard: Callable[[Callable[[], subprocess.Popen]], subprocess.Popen] | None = None,
    interrupted: _CliInterruptState,
) -> subprocess.CompletedProcess[str]:
    """Drain both streams, stop the group on failure, preserve exact captured bytes.

    The size bound is shared by both streams. Reaching it fails execution;
    the captured prefix is never reported as a complete transcript. An optional
    host launch_guard orders the actual Popen with resource invalidation; it
    releases before stream processing so in-flight calls can still be fenced.
    CompletedProcess and capture exceptions expose stdout_bytes/stderr_bytes;
    the original string attributes remain display-compatible. Decode replacement
    characters never replace the raw bytes. User interruption raises
    CliProcessInterrupted after cleanup, retaining the same observed prefix.
    """
    if type(max_output_bytes) is not int or max_output_bytes < 1:
        raise ValueError("CLI process output limit must be positive")
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError("CLI process timeout must be positive")
    def launch():
        return subprocess.Popen(
            argv, cwd=cwd, env=environment, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    process = launch_guard(launch) if launch_guard is not None else launch()
    from threading import Lock
    lock = Lock()
    exhausted = Event()
    stopped = Event()
    callback_errors: list[Exception] = []
    captured: list[bytearray] = [bytearray(), bytearray()]
    total = 0
    failure: str | None = None

    def mark_failure(reason: str) -> None:
        nonlocal failure
        with lock:
            if failure is None:
                failure = reason

    def drain(stream, index: int) -> None:
        nonlocal total, failure
        pending = bytearray()
        try:
            while block := stream.read1(8192):
                with lock:
                    available = max_output_bytes - total
                    accepted = block[:available]
                    captured[index].extend(accepted)
                    total += len(accepted)
                    if len(accepted) != len(block):
                        if failure is None:
                            failure = "output_limit"
                        exhausted.set()
                if index == 0 and on_stdout_line is not None and not stopped.is_set() and not exhausted.is_set():
                    pending.extend(accepted)
                    while b"\n" in pending:
                        line, _, rest = pending.partition(b"\n")
                        pending = rest
                        if not on_stdout_line(line.decode("utf-8")):
                            mark_failure("observer_stopped")
                            stopped.set()
                            break
            if pending and not stopped.is_set() and not exhausted.is_set():
                if not on_stdout_line(pending.decode("utf-8")):
                    mark_failure("observer_stopped")
                    stopped.set()
        except Exception as exc:
            callback_errors.append(exc)
            mark_failure("stream_error")
            stopped.set()
        finally:
            stream.close()

    def write_input() -> None:
        try:
            process.stdin.write(prompt.encode("utf-8"))
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        finally:
            process.stdin.close()

    workers = [Thread(target=drain, args=(process.stdout, 0), daemon=True),
               Thread(target=drain, args=(process.stderr, 1), daemon=True),
               Thread(target=write_input, daemon=True)]
    for worker in workers:
        worker.start()
    import time
    deadline = time.monotonic() + timeout_seconds
    cleanup_errors: list[str] = []
    try:
        while process.poll() is None:
            if interrupted.requested:
                mark_failure("cancelled")
                break
            if failure is not None:
                break
            if time.monotonic() >= deadline:
                mark_failure("timeout")
                break
            exhausted.wait(0.02)
    except KeyboardInterrupt:
        interrupted.requested = True
        mark_failure("cancelled")
    finally:
        # Descendants must not outlive the one-shot invocation, even if the
        # CLI parent exits before a descendant closes an inherited output pipe.
        try:
            _stop_process_group(process)
        except KeyboardInterrupt:
            interrupted.requested = True
            try:
                _stop_process_group(process)
            except (Exception, KeyboardInterrupt) as exc:
                cleanup_errors.append("Interrupted process cleanup: " + str(exc))
        except Exception as exc:
            cleanup_errors.append(str(exc))
        for worker in workers:
            try:
                worker.join(timeout=1)
            except KeyboardInterrupt:
                interrupted.requested = True
                cleanup_errors.append("Interrupted output-worker join")
    if any(worker.is_alive() for worker in workers):
        cleanup_errors.append("CLI output pipe did not close after group cleanup")
    cleanup_error = "; ".join(cleanup_errors) or None
    stream_error = "; ".join(map(str, callback_errors)) or None
    if cleanup_error:
        mark_failure("cleanup_error")
    with lock:
        stdout_bytes, stderr_bytes = map(bytes, captured)
    stdout, stderr = (value.decode("utf-8", errors="replace") for value in (stdout_bytes, stderr_bytes))
    if interrupted.requested or failure == "cancelled":
        raise CliProcessInterrupted(returncode=process.returncode, output=stdout, stderr=stderr,
            stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes,
            cleanup_error=cleanup_error, stream_error=stream_error)
    if failure == "timeout":
        raise CliProcessTimeout(argv, timeout_seconds, returncode=process.returncode,
                                output=stdout, stderr=stderr, cleanup_error=cleanup_error, stream_error=stream_error,
                                stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes)
    if failure is not None:
        message = {"observer_stopped": "Runtime stopped the CLI event stream",
                   "stream_error": "CLI stream processing failed",
                   "output_limit": "Runtime process output limit reached; log incomplete",
                   "cleanup_error": "CLI process cleanup failed"}[failure]
        if stream_error:
            message += "; stream processing: " + stream_error
        if cleanup_error:
            message += "; " + cleanup_error
        raise CliProcessError(process.returncode, argv, stop_reason=failure, message=message,
                              output=stdout, stderr=stderr, cleanup_error=cleanup_error, stream_error=stream_error,
                              stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes)
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    result.stdout_bytes, result.stderr_bytes = stdout_bytes, stderr_bytes
    return result


def run_cli_process(
    *, argv: list[str], prompt: str, cwd: Path, timeout_seconds: int,
    environment: dict[str, str], max_output_bytes: int = DEFAULT_PROCESS_OUTPUT_BYTES,
    on_stdout_line: Callable[[str], bool] | None = None,
    launch_guard: Callable[[Callable[[], subprocess.Popen]], subprocess.Popen] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Capture bounded exact streams through process shutdown and output handoff.

    Results and capture failures retain stdout_bytes/stderr_bytes alongside the
    original display strings. Exceeding the shared byte budget reports incomplete
    output. On the main thread, the default SIGINT behavior is deferred until
    cleanup and captured-byte handoff; the previous handler is always restored.
    Custom handlers and non-main-thread signal policy are not replaced.
    """
    with _capture_cli_interrupts() as interrupted:
        return _run_cli_process(argv=argv, prompt=prompt, cwd=cwd, timeout_seconds=timeout_seconds,
            environment=environment, max_output_bytes=max_output_bytes, on_stdout_line=on_stdout_line,
            launch_guard=launch_guard, interrupted=interrupted)


__all__ = ["CliProcessError", "CliProcessTimeout", "CliProcessInterrupted", "run_cli_process"]
