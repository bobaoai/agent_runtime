"""Bounded local CLI process execution shared by provider adapters."""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, replace
import os
import math
import time
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event, Lock, Thread, current_thread, main_thread
from typing import Callable

import psutil


DEFAULT_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024
_PROGRESS_PENDING_LIMIT = 16
_PROGRESS_HEARTBEAT_SECONDS = 10.0


@dataclass(frozen=True)
class CliEventSummary:
    """Safe description of the latest recognized CLI activity; never content.

    phase is one of init_observed, model_activity_observed, tool_requested,
    tool_result_observed, final_result_observed, declared_command_started or
    declared_command_finished. tool_category is a closed safe category or None;
    command_id is set only when Runtime bound a declared command.
    """

    phase: str
    tool_category: str | None = None
    command_id: str | None = None


@dataclass(frozen=True)
class CliProcessProgress:
    """One volatile live-observation snapshot; display only, never a record.

    update_trigger is process_started, event_received, heartbeat,
    process_finished or observation_stopped. updates_dropped counts snapshots
    lost since the previous delivery because the observer fell behind. Byte
    counts are capture lengths; zero means no observable output, not lack of
    model progress. elapsed_seconds is process-local monotonic time.
    """

    update_trigger: str
    updates_dropped: int
    process_id: int
    process_running: bool
    elapsed_seconds: float
    stdout_byte_count: int
    stderr_byte_count: int
    current_cli_event: CliEventSummary | None


class CliProgressChannel:
    """Bounded, in-order, non-persistent delivery of live process snapshots.

    Producers (an Adapter's stdout summarizer or declared-command hook) call
    event or stop_events; the process host attaches, heartbeats after quiet
    periods and finishes. Each publication builds an immutable snapshot under
    one short lock and joins a buffer of at most 16 undelivered snapshots; the
    oldest is dropped beyond that and the next delivery reports updates_dropped.
    A daemon relay calls the observer outside every lock, in publication order.
    Nothing is persisted and no history is kept. Every method an execution path
    calls swallows its own Exception and disables the channel, so observation
    never changes capture, deadlines, cleanup or results. _finish publishes the
    last snapshot and returns without waiting for the relay or observer; a
    blocked observer may therefore miss queued updates after the run returns.
    An observer that raises is not called again.
    """

    def __init__(self, observer: Callable[[CliProcessProgress], None]) -> None:
        if not callable(observer):
            raise TypeError("progress observer must be callable")
        self._observer = observer
        self._lock = Lock()
        self._ready = Event()
        self._pending: deque[CliProcessProgress] = deque()
        self._dropped = 0
        self._current: CliEventSummary | None = None
        self._events_stopped = False
        self._process = None
        self._started_monotonic = 0.0
        self._byte_counts: Callable[[], tuple[int, int]] | None = None
        self._last_published = 0.0
        self._closed = False

    def event(self, summary: CliEventSummary) -> None:
        """Publish a recognized transition; a no-op after stop or finish."""
        try:
            with self._lock:
                if self._closed or self._events_stopped:
                    return
                self._current = summary
                if self._process is not None:
                    self._publish("event_received")
        except Exception:
            self._fail()

    def stop_events(self) -> None:
        """Stop event summaries after a summarizer fault; process facts continue."""
        try:
            with self._lock:
                if self._closed or self._events_stopped:
                    return
                self._events_stopped = True
                self._current = None
                if self._process is not None:
                    self._publish("observation_stopped")
        except Exception:
            self._fail()

    def _attach(self, process, started_monotonic: float, byte_counts: Callable[[], tuple[int, int]]) -> None:
        try:
            with self._lock:
                if self._closed:
                    return
                self._process, self._started_monotonic, self._byte_counts = process, started_monotonic, byte_counts
                self._publish("process_started")
            Thread(target=self._deliver, daemon=True, name="agent-runtime-cli-progress").start()
        except Exception:
            self._fail()

    def _heartbeat_if_quiet(self, now: float) -> None:
        try:
            with self._lock:
                if (not self._closed and self._process is not None
                        and now - self._last_published >= _PROGRESS_HEARTBEAT_SECONDS):
                    self._publish("heartbeat")
        except Exception:
            self._fail()

    def _finish(self) -> None:
        try:
            with self._lock:
                if not self._closed and self._process is not None:
                    self._publish("process_finished")
                self._closed = True
                self._ready.set()
        except Exception:
            self._fail()

    def _publish(self, trigger: str) -> None:
        # Caller holds self._lock; byte_counts takes the capture lock after it.
        stdout_count, stderr_count = self._byte_counts()
        now = time.monotonic()
        snapshot = CliProcessProgress(
            update_trigger=trigger, updates_dropped=0, process_id=self._process.pid,
            process_running=self._process.poll() is None,
            elapsed_seconds=max(0.0, now - self._started_monotonic),
            stdout_byte_count=stdout_count, stderr_byte_count=stderr_count,
            current_cli_event=None if self._events_stopped else self._current)
        if len(self._pending) >= _PROGRESS_PENDING_LIMIT:
            self._pending.popleft()
            self._dropped += 1
        self._pending.append(snapshot)
        self._last_published = now
        self._ready.set()

    def _fail(self) -> None:
        try:
            with self._lock:
                self._closed = True
                self._pending.clear()
                self._ready.set()
        except Exception:
            pass

    def _deliver(self) -> None:
        while True:
            self._ready.wait()
            with self._lock:
                if self._pending:
                    snapshot = self._pending.popleft()
                    if self._dropped:
                        snapshot = replace(snapshot, updates_dropped=self._dropped)
                        self._dropped = 0
                elif self._closed:
                    return
                else:
                    self._ready.clear()
                    continue
            try:
                self._observer(snapshot)
            except Exception:
                self._fail()
                return


def _runtime_python_executable() -> Path:
    """Return this process's Python entry, preserving virtual-environment links."""
    if not sys.executable:
        raise FileNotFoundError("Current Runtime Python executable is unavailable")
    executable = Path(sys.executable).absolute()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise FileNotFoundError("Current Runtime Python executable is unavailable")
    return executable


def _runtime_python_read_roots() -> tuple[Path, ...]:
    """Current environment and standard library; no discovery or alternative."""
    roots = tuple(dict.fromkeys(Path(value).resolve(strict=True) for value in (sys.prefix, sys.base_prefix)))
    if any(not path.is_dir() or path == Path(path.anchor) for path in roots):
        raise ValueError("Current Runtime Python must have concrete installation directories")
    return roots


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
    Callers check the request after trace serialization as well, rather than
    discarding captured output when a signal arrives during record handoff.
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
    """A user interruption with captured streams and any earlier capture failure.

    prior_stop_reason retains an observed timeout/output-limit/stream failure
    that existed before cancellation was handed off; no event time is inferred.
    """

    def __init__(self, *, returncode, output, stderr, stdout_bytes, stderr_bytes,
                 cleanup_error=None, stream_error=None, prior_stop_reason=None):
        super().__init__("CLI process interrupted")
        self.returncode = returncode
        self.output = self.stdout = output
        self.stderr = stderr
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes
        self.cleanup_error = cleanup_error
        self.stream_error = stream_error
        self.stop_reason = "cancelled"
        self.prior_stop_reason = prior_stop_reason


def _remember_descendants(process):
    """Retain psutil identities while ancestry establishes invocation ownership.

    No command lines or environments are read. A vanished intermediate parent
    can hide never-observed descendants; this is not an OS daemon container.
    """
    owned = getattr(process, "_runtime_descendants", None)
    if owned is None:
        owned = process._runtime_descendants = set()
    parent = getattr(process, "_runtime_process_identity", None)
    if parent is None and process.poll() is None:
        try:
            parent = psutil.Process(process.pid)
            parent.create_time()
            process._runtime_process_identity = parent
        except psutil.NoSuchProcess:
            parent = None
    roots = ([parent] if parent is not None else []) + list(owned)
    for root in roots:
        try:
            for child in root.children(recursive=True):
                child.create_time()  # Identity must be observable before retention.
                owned.add(child)
        except psutil.NoSuchProcess:
            continue


def _stop_process_group(process: subprocess.Popen) -> None:
    errors = []
    try:
        _remember_descendants(process)
    except Exception as exc:
        errors.append("descendant ownership could not be confirmed: " + str(exc))
    owned = list(getattr(process, "_runtime_descendants", ()))
    for child in owned:
        try:
            if not child.is_running():
                continue
            child.kill()  # psutil checks PID reuse before signaling.
        except (psutil.NoSuchProcess, ProcessLookupError):
            pass
        except (psutil.Error, OSError) as exc:
            errors.append("owned descendant could not be stopped: " + str(exc))
    # Popen created this session/group. Its leader may already have exited
    # before discovery; unobserved same-group children still belong to it.
    # Always terminate the known group, independently of ancestry sampling.
    try:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except PermissionError:
                # Darwin may report EPERM for an exited, unreaped group leader.
                # Reap only if already finished and retry the same known group;
                # a live leader or a second denial remains a cleanup failure.
                if process.poll() is None:
                    raise
                os.killpg(process.pid, signal.SIGKILL)
        elif process.poll() is None:
            process.kill()
    except ProcessLookupError:
        pass
    except OSError as exc:
        errors.append("Provider group could not be stopped: " + str(exc))
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired as exc:
        errors.append("CLI process did not stop after cleanup: " + str(exc))
    try:
        _, alive = psutil.wait_procs(owned, timeout=3)
        survivors = [child for child in alive
                     if child.is_running() and child.status() != psutil.STATUS_ZOMBIE]
        if survivors:
            errors.append("Runtime-owned background processes did not stop")
    except psutil.Error as exc:
        errors.append("descendant termination could not be confirmed: " + str(exc))
    if errors:
        raise RuntimeError("; ".join(errors))


def _run_cli_process(
    *, argv: list[str], prompt: str, cwd: Path, timeout_seconds: int,
    environment: dict[str, str], max_output_bytes: int = DEFAULT_PROCESS_OUTPUT_BYTES,
    on_stdout_line: Callable[[str], bool] | None = None,
    launch_guard: Callable[[Callable[[], subprocess.Popen]], subprocess.Popen] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    user_cancel_requested: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
    progress_channel: CliProgressChannel | None = None,
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
    if deadline_monotonic is not None and (type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)):
        raise ValueError("deadline_monotonic must be a finite process-local value")
    if progress_channel is not None and not isinstance(progress_channel, CliProgressChannel):
        raise TypeError("progress_channel must be a CliProgressChannel")
    def launch():
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            raise CliProcessTimeout(argv, 0, returncode=None, output="", stderr="",
                                    stdout_bytes=b"", stderr_bytes=b"")
        if interrupted.requested or (user_cancel_requested is not None and user_cancel_requested()):
            raise CliProcessInterrupted(returncode=None, output="", stderr="",
                                        stdout_bytes=b"", stderr_bytes=b"")
        if cancel_requested is not None and cancel_requested():
            raise CliProcessError(None, argv, stop_reason="resource_closed", message="Runtime process resources are closed",
                                  output="", stderr="", stdout_bytes=b"", stderr_bytes=b"")
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
    if progress_channel is not None:
        # Attach before any worker starts, so fast stdout events are published.
        def byte_counts() -> tuple[int, int]:
            with lock:
                return len(captured[0]), len(captured[1])
        progress_channel._attach(process, time.monotonic(), byte_counts)
    for worker in workers:
        worker.start()
    process_started = time.monotonic()
    deadline = process_started + timeout_seconds
    if deadline_monotonic is not None:
        deadline = min(deadline, deadline_monotonic)
    cleanup_errors: list[str] = []
    ownership_check = 0.0
    try:
        while process.poll() is None:
            if os.name == "posix" and time.monotonic() >= ownership_check:
                try:
                    _remember_descendants(process)
                except psutil.Error as exc:
                    cleanup_errors.append("descendant observation unavailable: " + str(exc))
                    mark_failure("cleanup_error")
                    break
                ownership_check = time.monotonic() + 0.1
            if progress_channel is not None:
                progress_channel._heartbeat_if_quiet(time.monotonic())
            if time.monotonic() >= deadline:
                mark_failure("timeout")
                break
            if interrupted.requested or (user_cancel_requested is not None and user_cancel_requested()):
                interrupted.requested = True
                mark_failure("cancelled")
                break
            if failure is not None:
                break
            if cancel_requested is not None and cancel_requested():
                mark_failure("resource_closed")
                break
            exhausted.wait(0.02)
    except KeyboardInterrupt:
        interrupted.requested = True
        mark_failure("cancelled")
    except Exception as exc:
        callback_errors.append(exc)
        mark_failure("resource_closed")
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
    if progress_channel is not None:
        progress_channel._finish()
    stdout, stderr = (value.decode("utf-8", errors="replace") for value in (stdout_bytes, stderr_bytes))
    if interrupted.requested or failure == "cancelled":
        raise CliProcessInterrupted(returncode=process.returncode, output=stdout, stderr=stderr,
            stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes,
            cleanup_error=cleanup_error, stream_error=stream_error,
            prior_stop_reason=failure if failure != "cancelled" else None)
    if failure == "timeout":
        raise CliProcessTimeout(argv, max(0.0, deadline-process_started), returncode=process.returncode,
                                output=stdout, stderr=stderr, cleanup_error=cleanup_error, stream_error=stream_error,
                                stdout_bytes=stdout_bytes, stderr_bytes=stderr_bytes)
    if failure is not None:
        message = {"observer_stopped": "Runtime stopped the CLI event stream",
                   "stream_error": "CLI stream processing failed",
                   "output_limit": "Runtime process output limit reached; log incomplete",
                   "resource_closed": "Runtime process resources are closed",
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
    cancel_requested: Callable[[], bool] | None = None,
    user_cancel_requested: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
    progress_channel: CliProgressChannel | None = None,
) -> subprocess.CompletedProcess[str]:
    """Capture bounded exact streams through process shutdown and output handoff.

    Results and capture failures retain stdout_bytes/stderr_bytes alongside the
    original display strings. Exceeding the shared byte budget reports incomplete
    output. On the main thread, the default SIGINT behavior is deferred until
    cleanup and captured-byte handoff; the previous handler is always restored.
    Custom handlers and non-main-thread signal policy are not replaced.
    An optional trusted cancel_requested callback stops resource-bound commands
    with resource_closed, not a forged user interruption. It is checked before
    Popen and during waiting; the launch guard still orders creation with close.
    user_cancel_requested is a separate trusted callback for user cancellation
    from another thread; it stops the process and raises CliProcessInterrupted
    with captured bytes. It never converts resource closure into a user action.
    An optional progress_channel receives volatile process snapshots: when the
    process starts, after 10 s without a publication, and once at the end. Its
    producers add event updates. It never alters capture, deadlines, cleanup or
    the result, and the final snapshot is published without waiting for the
    observer. None keeps the original behavior and threads.
    """
    with _capture_cli_interrupts() as interrupted:
        return _run_cli_process(argv=argv, prompt=prompt, cwd=cwd, timeout_seconds=timeout_seconds,
            environment=environment, max_output_bytes=max_output_bytes, on_stdout_line=on_stdout_line,
            launch_guard=launch_guard, cancel_requested=cancel_requested,
            user_cancel_requested=user_cancel_requested, deadline_monotonic=deadline_monotonic,
            progress_channel=progress_channel, interrupted=interrupted)


__all__ = ["CliEventSummary", "CliProcessError", "CliProcessInterrupted", "CliProcessProgress",
           "CliProcessTimeout", "CliProgressChannel", "run_cli_process"]


def invocation_deadline(host, request):
    """Read a live budget only from the host bound to this exact request."""
    reader = getattr(host, "run_deadline", None)
    return None if reader is None else reader(request)


def preflight_timeout(limit, deadline, command):
    """Bound a Runtime subprocess preflight without restarting the run clock."""
    if deadline is None:
        return limit
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(command, 0)
    return min(limit, remaining)


def invocation_budget_expired(host, request):
    """Keep an enclosing deadline distinct from a subprocess's own timeout."""
    deadline = invocation_deadline(host, request)
    return deadline is not None and time.monotonic() >= deadline
