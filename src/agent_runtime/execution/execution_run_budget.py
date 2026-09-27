"""Process-local work deadlines for synchronous Runtime entries and managed children.

No clock instant is serialized. Records contain only durations and existing
Attempt lineage. A live parent is obtained from a bound Runtime host, never
from task JSON or Provider-authored arguments.
"""
from __future__ import annotations

import os
import math
import time
from threading import RLock
from typing import Callable


_PARENT_TOKEN = object()


class ParentRunContext:
    """Read-only parent constraint issued for one active Runtime Attempt."""

    def __init__(self, *, _token, budget, attempt_id, active, cancelled):
        if _token is not _PARENT_TOKEN:
            raise TypeError("parent_run must be issued by the active Runtime host")
        self._budget = budget
        self._attempt_id = attempt_id
        self._active = active
        self._cancelled = cancelled
        self._pid = os.getpid()

    @property
    def attempt_id(self):
        return self._attempt_id

    def require_active(self):
        if self._pid != os.getpid():
            raise PermissionError("parent_run cannot cross a process boundary")
        self._budget.require_active()
        self._active()

    def cancelled(self):
        return bool(self._cancelled()) or self._budget.user_cancel_requested()


class RunBudget:
    """One shared monotonic deadline; retries and child calls cannot reset it."""

    def __init__(self, requested_timeout_seconds: int, *, started: float,
                 parent_run: ParentRunContext | None = None,
                 clock: Callable[[], float] = time.monotonic):
        from .execution_parameter_resolution import _validate_parameter
        _validate_parameter("run_timeout_seconds", requested_timeout_seconds)
        if parent_run is not None:
            if type(parent_run) is not ParentRunContext:
                raise TypeError("parent_run must be a live Runtime parent context")
            parent_run.require_active()
        if type(started) not in (int, float) or not math.isfinite(started) or not callable(clock):
            raise ValueError("run budget requires a finite local start and clock")
        self._clock = clock
        self._started = started
        self._requested = requested_timeout_seconds
        self._parent = parent_run
        self._pid = os.getpid()
        self._closed = False
        self._lock = RLock()
        local_deadline = started + requested_timeout_seconds
        parent_deadline = None if parent_run is None else parent_run._budget.deadline
        self._limiting_scope = "parent" if parent_deadline is not None and parent_deadline <= local_deadline else "local"
        self._deadline = min(local_deadline, parent_deadline) if parent_deadline is not None else local_deadline

    @property
    def deadline(self) -> float:
        return self._deadline

    def expired(self) -> bool:
        return self._clock() >= self._deadline

    def remaining(self) -> float:
        return max(0.0, self._deadline - self._clock())

    def require_active(self):
        with self._lock:
            if self._pid != os.getpid() or self._closed:
                raise PermissionError("Runtime run context is closed or belongs to another process")
        if self.expired():
            raise TimeoutError(f"Runtime {self._limiting_scope} work budget expired")
        if self._parent is not None:
            self._parent.require_active()

    def user_cancel_requested(self) -> bool:
        return self._parent is not None and self._parent.cancelled()

    def resource_closed(self) -> bool:
        if self._parent is None:
            return False
        try:
            self._parent.require_active()
        except TimeoutError:
            return False  # The process deadline preserves timeout as its cause.
        except PermissionError:
            return True
        return False

    def bind_parent(self, *, attempt_id: str, active, cancelled) -> ParentRunContext:
        self.require_active()
        if not isinstance(attempt_id, str) or not attempt_id or not callable(active) or not callable(cancelled):
            raise ValueError("parent context requires an accurate active Attempt binding")
        active()
        return ParentRunContext(_token=_PARENT_TOKEN, budget=self, attempt_id=attempt_id,
                                active=active, cancelled=cancelled)

    def as_record(self) -> dict:
        return {"requested_timeout_seconds": self._requested,
                "effective_timeout_seconds": max(0.0, self._deadline-self._started),
                "elapsed_seconds": max(0.0, self._clock()-self._started),
                "limiting_scope": self._limiting_scope,
                "parent_attempt_id": None if self._parent is None else self._parent.attempt_id}

    def close(self):
        with self._lock:
            self._closed = True
