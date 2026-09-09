"""Bounded local CLI process execution shared by provider adapters."""

from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
from threading import Event, Thread
from typing import Callable


DEFAULT_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024


class CliProcessError(subprocess.CalledProcessError):
    """Runtime capture failure, separate from the process's actual exit status."""

    def __init__(self, returncode: int | None, cmd, *, stop_reason: str, message: str,
                 output: str, stderr: str, cleanup_error: str | None = None,
                 stream_error: str | None = None) -> None:
        super().__init__(returncode, cmd, output=output, stderr=stderr)
        self.stop_reason = stop_reason
        self.cleanup_error = cleanup_error
        self.stream_error = stream_error
        self.message = message

    def __str__(self) -> str:
        return f"{self.message} (process exit code: {self.returncode})"


class CliProcessTimeout(subprocess.TimeoutExpired):
    """Keep the timeout interface while retaining observed cleanup/exit facts."""

    def __init__(self, cmd, timeout, *, returncode: int | None, output: str, stderr: str,
                 cleanup_error: str | None = None, stream_error: str | None = None) -> None:
        super().__init__(cmd, timeout, output=output, stderr=stderr)
        self.returncode = returncode
        self.stop_reason = "timeout"
        self.cleanup_error = cleanup_error
        self.stream_error = stream_error

    def __str__(self) -> str:
        message = super().__str__()
        if self.stream_error:
            message += "; stream processing: " + self.stream_error
        if self.cleanup_error:
            message += "; cleanup: " + self.cleanup_error
        return message


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


def run_cli_process(
    *, argv: list[str], prompt: str, cwd: Path, timeout_seconds: int,
    environment: dict[str, str], max_output_bytes: int = DEFAULT_PROCESS_OUTPUT_BYTES,
    on_stdout_line: Callable[[str], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Drain both streams, stop the group on failure, preserve bounded output.

    The size bound is shared by both streams. Reaching it fails execution;
    the captured prefix is never reported as a complete transcript.
    """
    if type(max_output_bytes) is not int or max_output_bytes < 1:
        raise ValueError("CLI process output limit must be positive")
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError("CLI process timeout must be positive")
    process = subprocess.Popen(
        argv, cwd=cwd, env=environment, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
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
            if failure is not None:
                break
            if time.monotonic() >= deadline:
                mark_failure("timeout")
                break
            exhausted.wait(0.02)
    finally:
        # Descendants must not outlive the one-shot invocation, even if the
        # CLI parent exits before a descendant closes an inherited output pipe.
        try:
            _stop_process_group(process)
        except Exception as exc:
            cleanup_errors.append(str(exc))
        for worker in workers:
            worker.join(timeout=1)
    if any(worker.is_alive() for worker in workers):
        cleanup_errors.append("CLI output pipe did not close after group cleanup")
    cleanup_error = "; ".join(cleanup_errors) or None
    stream_error = "; ".join(map(str, callback_errors)) or None
    if cleanup_error:
        mark_failure("cleanup_error")
    with lock:
        stdout, stderr = (bytes(value).decode("utf-8", errors="replace") for value in captured)
    if failure == "timeout":
        raise CliProcessTimeout(argv, timeout_seconds, returncode=process.returncode,
                                output=stdout, stderr=stderr, cleanup_error=cleanup_error, stream_error=stream_error)
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
                              output=stdout, stderr=stderr, cleanup_error=cleanup_error, stream_error=stream_error)
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)


__all__ = ["CliProcessError", "CliProcessTimeout", "run_cli_process"]
