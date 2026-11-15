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
from typing import Any, Callable

from .invocation_host_platform_selection import HOST_PLATFORM


DEFAULT_PROCESS_OUTPUT_BYTES = 16 * 1024 * 1024
# Bound on waiting for an observer call in flight at the result handoff.
_DELIVERY_FENCE_SECONDS = 5.0
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


class CliProcessStartupError(OSError):
    """The CLI process did not start, so there is no capture.

    process_created is True only when the host had created a child before the
    failure. cleanup_error is None when no child was created or every created
    process was conclusively stopped; otherwise it states the unconfirmed
    cleanup, so callers never infer process stop from the message.
    """

    def __init__(self, cause: OSError, *, process_created: bool, cleanup_error: str | None) -> None:
        super().__init__(*cause.args)
        if cause.filename is not None:
            self.filename = cause.filename
        if cause.filename2 is not None:
            self.filename2 = cause.filename2
        self.process_created = process_created
        self.cleanup_error = cleanup_error

    def __str__(self) -> str:
        message = super().__str__()
        return message + ("; cleanup: " + self.cleanup_error if self.cleanup_error else "")


@dataclass(frozen=True)
class CliProcessCreationEvidence:
    """Process facts a caller may attach, as ``cli_process_evidence``, to its own exception.

    For an exception raised outside run_cli_process after it created a process,
    this records whether that process's stop was confirmed (cleanup_error is
    None); cli_process_stop_confirmed reads it. run_cli_process itself reports a
    launch-guard failure after creation as CliProcessStartupError or
    CliProcessInterrupted carrying cleanup_error.
    """

    process_created: bool
    cleanup_error: str | None


def cli_process_stop_confirmed(error: BaseException) -> bool | None:
    """Whether a run_cli_process failure states that every created process stopped.

    True or False for a process-module error or a guard failure carrying
    CliProcessCreationEvidence; None when the error carries no process facts, so
    callers keep whatever they already knew. Never inferred from message text.
    """
    evidence = getattr(error, "cli_process_evidence", None)
    if isinstance(evidence, CliProcessCreationEvidence):
        return evidence.cleanup_error is None
    if isinstance(error, (CliProcessError, CliProcessTimeout, CliProcessInterrupted, CliProcessStartupError)):
        return error.cleanup_error is None
    return None


def _stop_unstarted(process) -> str | None:
    """Stop a created process whose launch did not complete and close its pipes; report uncertainty."""
    errors = []
    try:
        HOST_PLATFORM.stop_cli_process(process)
    except Exception as exc:
        errors.append(str(exc))
    for stream in (process.stdin, process.stdout, process.stderr):
        try:
            stream.close()
        except Exception as exc:
            errors.append("CLI pipe could not be closed: " + str(exc))
    return "; ".join(errors) or None


def _run_cli_process(
    *, argv: list[str], prompt: str, cwd: Path, timeout_seconds: int,
    environment: dict[str, str], max_output_bytes: int = DEFAULT_PROCESS_OUTPUT_BYTES,
    on_stdout_line: Callable[[str], bool] | None = None,
    launch_guard: Callable[[Callable[[], Any]], Any] | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    user_cancel_requested: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
    progress_channel: CliProgressChannel | None = None,
    interrupted: _CliInterruptState,
) -> subprocess.CompletedProcess[str]:
    """Drain both streams, stop the started process, preserve exact captured bytes.

    The size bound is shared by both streams. Reaching it fails execution;
    the captured prefix is never reported as a complete transcript. An optional
    host launch_guard orders the actual host process creation with resource
    invalidation; it releases before stream processing so in-flight calls can
    still be fenced. A process created before the guard fails is stopped, never
    left running; the failure is reported as CliProcessStartupError (or
    CliProcessInterrupted for an interruption) with that stop's cleanup
    certainty. HostPlatform owns creation and the
    host-native stop of the started process (POSIX: its session group); every
    exit path stops it before joining stream workers. CLI-internal processes
    are the CLI's own and are not enumerated. Three facts stay separate:
    process exit (returncode), stop confirmation (cleanup_error is None) and
    log completeness. A worker still blocked after the bounded join, for
    example on a pipe a CLI-internal process kept open, sets
    ``output_streams_closed`` False on the result instead of failing the call;
    True only means both pipes reached EOF. The handoff is fenced: once the
    returned or raised snapshot is fixed, such a worker neither appends bytes nor
    calls on_stdout_line, so the caller's observer state cannot change after
    return (later bytes are not part of this invocation's record). CompletedProcess and capture
    exceptions expose stdout_bytes/stderr_bytes; the original string attributes
    remain display-compatible. Decode replacement characters never replace the
    raw bytes. User interruption raises CliProcessInterrupted after cleanup,
    retaining the same observed prefix. A host start failure raises
    CliProcessStartupError with explicit cleanup certainty.
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
        if launched:
            raise RuntimeError("CLI launch guard attempted a second process creation")
        try:
            created = HOST_PLATFORM.create_cli_process(argv, cwd=cwd, environment=environment)
        except OSError as exc:
            raise CliProcessStartupError(exc, process_created=getattr(exc, "process_created", False) is True,
                                         cleanup_error=getattr(exc, "cleanup_error", None)) from exc
        launched.append(created)
        return created
    launched: list = []
    try:
        process = launch_guard(launch) if launch_guard is not None else launch()
        if launched and process is not launched[0]:
            raise TypeError("CLI launch guard must return the process it created")
    except BaseException as failure:
        if not launched:
            raise
        # The guard failed after actual creation: stop that process instead of orphaning it.
        cleanup_error = _stop_unstarted(launched[0])
        if isinstance(failure, KeyboardInterrupt):
            interrupted.requested = True
            raise CliProcessInterrupted(returncode=launched[0].returncode, output="", stderr="",
                                        stdout_bytes=b"", stderr_bytes=b"", cleanup_error=cleanup_error) from failure
        if not isinstance(failure, Exception):
            raise
        raise CliProcessStartupError(OSError(f"CLI launch did not complete after process creation: {failure}"),
                                     process_created=True, cleanup_error=cleanup_error) from failure
    lock = Lock()
    # Held while the observer runs and while the result is handed off, so no
    # observer call can happen once the returned snapshot is fixed.
    delivery = Lock()
    handed_off = False
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

    def deliver(text: str) -> bool:
        """Pass one line to the observer unless the result was already handed off."""
        with delivery:
            if handed_off:
                return False
            if not on_stdout_line(text):
                mark_failure("observer_stopped")
                stopped.set()
                return False
            return True

    def drain(stream, index: int) -> None:
        nonlocal total, failure
        pending = bytearray()
        try:
            while block := stream.read1(8192):
                with lock:
                    if handed_off:
                        return  # Bytes after the handoff are neither captured nor delivered.
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
                        if not deliver(line.decode("utf-8")):
                            break
            if pending and on_stdout_line is not None and not stopped.is_set() and not exhausted.is_set():
                deliver(pending.decode("utf-8"))
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
    try:
        while process.poll() is None:
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
        # Stop the started process first; readable pipe ends stay open so the
        # workers drain what was already written.
        try:
            HOST_PLATFORM.stop_cli_process(process)
        except KeyboardInterrupt:
            interrupted.requested = True
            try:
                HOST_PLATFORM.stop_cli_process(process)
            except (Exception, KeyboardInterrupt) as exc:
                cleanup_errors.append("Interrupted process stop: " + str(exc))
        except Exception as exc:
            cleanup_errors.append(str(exc))
        for worker in workers:
            try:
                worker.join(timeout=1)
            except KeyboardInterrupt:
                interrupted.requested = True
                cleanup_errors.append("Interrupted output-worker join")
    # Workers close their own streams; one still blocked (a pipe another process
    # holds open) is a log-completeness fact, not a failure to stop the started process.
    output_streams_closed = not any(worker.is_alive() for worker in workers[:2])
    # Fix the handoff: wait for any observer call in flight, then close delivery and
    # capture together. A worker still blocked on a held pipe neither appends nor
    # calls the observer after this point, so the returned facts cannot change.
    fenced = delivery.acquire(timeout=_DELIVERY_FENCE_SECONDS)
    try:
        with lock:
            handed_off = True
            stdout_bytes, stderr_bytes = map(bytes, captured)
    finally:
        if fenced:
            delivery.release()
    if not fenced:
        # An observer call is still running: it may change caller state after return,
        # so this invocation cannot be reported as a clean result.
        callback_errors.append(RuntimeError("CLI event observer did not return before the result handoff"))
        mark_failure("stream_error")
    cleanup_error = "; ".join(cleanup_errors) or None
    stream_error = "; ".join(map(str, callback_errors)) or None
    if cleanup_error:
        mark_failure("cleanup_error")
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
    result.output_streams_closed = output_streams_closed
    return result


def run_cli_process(
    *, argv: list[str], prompt: str, cwd: Path, timeout_seconds: int,
    environment: dict[str, str], max_output_bytes: int = DEFAULT_PROCESS_OUTPUT_BYTES,
    on_stdout_line: Callable[[str], bool] | None = None,
    launch_guard: Callable[[Callable[[], Any]], Any] | None = None,
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
    process creation and during waiting; the launch guard still orders creation
    with close. On Windows, argv[0] must name a native .exe (bare names resolve
    only through the supplied PATH); a .cmd/.bat/.ps1 launcher is not started
    because its interpreter would re-parse the exact argv. The result carries
    output_streams_closed (EOF on both pipes; not a completeness claim).
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


__all__ = ["CliEventSummary", "CliProcessCreationEvidence", "CliProcessError", "CliProcessInterrupted",
           "CliProcessProgress", "CliProcessStartupError", "CliProcessTimeout", "CliProgressChannel",
           "cli_process_stop_confirmed", "run_cli_process"]


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
