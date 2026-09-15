"""Process-local cursor for one trusted temporary Workflow execution.

This owns cursor state, not node scheduling, production admission or storage.
The existing Coordinator drives it. Closing the instance ends its recovery scope.
"""
from __future__ import annotations

import hashlib
import json
from threading import RLock

from ..contracts.durability_topology_definition import (
    BackendEvent, BackendExecutionRef, ExecutionSnapshot, ExternalEvent, WorkflowGraphProjection,
)
from ..foundation.foundation_contract_validation import validate_id, validate_opaque_ref, validate_sha256


class LocalWorkflowBackend:
    """One live, non-persistent cursor; no production start/cancellation DTOs."""

    def __init__(self, *, transition_guard=None):
        if transition_guard is not None and not callable(transition_guard):
            raise TypeError('transition_guard must be a trusted callable or None')
        self._transition_guard = transition_guard
        self._lock = RLock()
        self._active = True
        self._execution = None
        self._events = []
        self._waiting = None
        self._cancellation = None

    def _require_active(self):
        if not self._active:
            raise RuntimeError("local Workflow resources are closed; cross-process recovery is unavailable")

    def start_local(self, *, graph: WorkflowGraphProjection, workflow_release_ref: str,
                    workflow_release_sha256: str, selection_ref: str, selection_sha256: str,
                    input_closure_sha256: str, workflow_execution_id: str) -> BackendExecutionRef:
        """Pin Execution-validated graph/binding facts without accessing a Registry.

        Execution owns release resolution, input schemas and selection meaning.
        The cursor owns exact supplied bindings, state and event consistency only.
        """
        if type(graph) is not WorkflowGraphProjection:
            raise TypeError("local start requires WorkflowGraphProjection")
        graph.validate()
        validate_id("workflow_execution_id", workflow_execution_id)
        validate_opaque_ref('workflow_release_ref', workflow_release_ref)
        validate_opaque_ref('selection_ref', selection_ref)
        for name, value in (('workflow_release_sha256', workflow_release_sha256),
                            ('selection_sha256', selection_sha256), ('input_closure_sha256', input_closure_sha256)):
            validate_sha256(name, value)
        body = json.dumps({'workflow_execution_id': workflow_execution_id,
            'workflow_release_ref': workflow_release_ref, 'workflow_release_sha256': workflow_release_sha256,
            'selection_ref': selection_ref, 'selection_sha256': selection_sha256,
            'graph_sha256': graph.graph_sha256, 'input_closure_sha256': input_closure_sha256},
            sort_keys=True, separators=(',', ':')).encode()
        digest = hashlib.sha256(body).hexdigest()
        with self._lock:
            self._require_active()
            if self._execution is not None:
                if digest != self._start_sha256:
                    raise ValueError("local cursor is already bound to another start")
                return self._execution
            self._execution = BackendExecutionRef('local_process', 'temporary', workflow_execution_id, workflow_execution_id)
            self._execution.validate()
            self._bindings = (workflow_release_ref, workflow_release_sha256, selection_ref, selection_sha256)
            self._graph = graph
            self._start_sha256 = digest
            self._state = self._graph.initial_state
            self._status = 'running'
            return self._execution

    def _check(self, execution):
        self._require_active()
        if self._execution is None or execution != self._execution:
            raise PermissionError("local cursor crossed execution identity")

    def _snapshot(self):
        cancelled = self._cancellation
        result = ExecutionSnapshot(
            backend_id=self._execution.backend_id, backend_execution_id=self._execution.backend_execution_id,
            workflow_execution_id=self._execution.workflow_execution_id, workflow_id=self._graph.workflow_id,
            graph_sha256=self._graph.graph_sha256, start_request_sha256=self._start_sha256,
            current_state=self._state, terminal=self._status != 'running', applied_events=tuple(self._events),
            runtime_status_id=self._status,
            acknowledged_cancellation_id=None if cancelled is None else cancelled[0],
            acknowledged_cancellation_ref=None if cancelled is None else cancelled[1],
        )
        result.validate()
        return result

    @property
    def start_sha256(self):
        with self._lock:
            self._require_active()
            if self._execution is None:
                raise ValueError("local Workflow has not started")
            return self._start_sha256

    def snapshot(self, execution):
        """Read the same locked cursor from a node's worker thread."""
        with self._lock:
            self._check(execution)
            return self._snapshot()

    def validate_started(self, execution, *, workflow_release_ref, workflow_release_sha256,
                         selection_ref, selection_sha256, expected_start_sha256):
        """Bind the Coordinator's supplied selection to this actual local start."""
        with self._lock:
            self._check(execution)
            if ((workflow_release_ref, workflow_release_sha256, selection_ref, selection_sha256) != self._bindings
                    or expected_start_sha256 != self._start_sha256):
                raise PermissionError("local cursor start/selection binding mismatch")

    async def query(self, execution):
        return self.snapshot(execution)

    async def recover(self, execution):
        """Resume within this live instance only, never after process restart."""
        return await self.query(execution)

    async def apply_external_event(self, execution, event):
        def apply():
            with self._lock:
                return self._apply_event(execution, event)
        return apply() if self._transition_guard is None else self._transition_guard(apply)

    def _apply_event(self, execution, event):
        """Apply with the cursor lock already held and resource guard admitted."""
        if type(event) is not ExternalEvent:
            raise TypeError("local cursor requires an exact ExternalEvent")
        event.validate()
        with self._lock:
            self._check(execution)
            if event.workflow_execution_id != execution.workflow_execution_id:
                raise PermissionError("event crossed local Workflow")
            prior = next((item for item in self._events if item.event_id == event.event_id), None)
            if prior is not None:
                if prior != event:
                    raise ValueError("event identity was reused with different content")
                return self._snapshot()
            if self._status != 'running':
                raise ValueError("terminal local Workflow cannot advance")
            if event.expected_state != self._state:
                raise ValueError("event expected_state is stale")
            if event.target_state not in self._graph.allowed_targets(self._state):
                raise ValueError("event requests an undeclared graph transition")
            self._events.append(event)
            self._state = event.target_state
            self._waiting = None
            self._status = 'completed' if self._state in self._graph.terminal_states else 'running'
            return self._snapshot()

    def acknowledge_wait(self, execution, *, expected_state, dispatch_id, outcome_ref, outcome_sha256):
        """Remember a caller-validated wait's exact reference and cursor position."""
        validate_id('expected_state', expected_state)
        validate_id('dispatch_id', dispatch_id)
        validate_opaque_ref('outcome_ref', outcome_ref)
        validate_sha256('outcome_sha256', outcome_sha256)
        binding = (expected_state, dispatch_id, outcome_ref, outcome_sha256)
        def acknowledge():
            with self._lock:
                self._check(execution)
                if expected_state != self._state:
                    raise PermissionError("wait crossed local execution/state")
                if self._status != 'running':
                    raise ValueError("terminal local Workflow cannot wait")
                if self._waiting is not None and self._waiting != binding:
                    raise ValueError("another wait already owns this cursor state")
                self._waiting = binding
        return acknowledge() if self._transition_guard is None else self._transition_guard(acknowledge)

    def waiting_outcome(self, execution):
        """Read the current wait's exact committed-outcome binding, or None."""
        with self._lock:
            self._check(execution)
            if self._waiting is None:
                return None
            return dict(zip(('expected_state', 'dispatch_id', 'outcome_ref', 'outcome_sha256'), self._waiting))

    async def resume_event(self, execution, event, *, expected_wait_ref=None, expected_wait_sha256=None):
        """Apply a matching host test event only to an acknowledged live wait."""
        if type(event) is not ExternalEvent:
            raise TypeError("local wait requires an exact ExternalEvent")
        event.validate()
        def resume():
            with self._lock:
                self._check(execution)
                prior = next((item for item in self._events if item.event_id == event.event_id), None)
                if prior is None:
                    if self._waiting is None:
                        raise ValueError("local Workflow has no acknowledged wait")
                    if (expected_wait_ref, expected_wait_sha256) != self._waiting[2:]:
                        raise ValueError('wait changed before event application')
                return self._apply_event(execution, event)
        return resume() if self._transition_guard is None else self._transition_guard(resume)

    def request_cancel(self, execution, *, reason: str):
        """A trusted local host requests cancellation; no production decision is made."""
        if type(reason) is not str or not reason.strip():
            raise ValueError("local cancellation requires a reason")
        with self._lock:
            self._check(execution)
            if self._status == 'completed':
                raise ValueError("completed local Workflow cannot be cancelled")
            if self._cancellation is None:
                digest = hashlib.sha256((execution.workflow_execution_id + '\0' + reason).encode()).hexdigest()
                self._cancellation = ('cancel_' + digest[:24], 'local-cancellation:' + digest, reason)
            self._status = 'cancelled'
            return self._snapshot()

    async def list_events(self, execution):
        with self._lock:
            self._check(execution)
            rows = [BackendEvent(execution.backend_id, execution.backend_execution_id,
                execution.workflow_execution_id, item.event_type, item.evidence_ref) for item in self._events]
            if self._cancellation is not None:
                rows.append(BackendEvent(execution.backend_id, execution.backend_execution_id,
                    execution.workflow_execution_id, 'runtime_cancellation', self._cancellation[1]))
            return tuple(rows)

    def close(self):
        with self._lock:
            self._active = False


__all__ = ['LocalWorkflowBackend']
