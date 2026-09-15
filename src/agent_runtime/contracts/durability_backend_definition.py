"""Canonical provider-neutral contract for durable Agent Runtime backends.

The contract deliberately reuses the target host request and topology records.
It does not define or alias a parallel family of start, execution, event, or
snapshot DTOs.  The one sanctioned derived view is the ingress-plane read
model ExternalEventExecutionSnapshot in execution_event_definition, which
projects this cursor state for wait authorization and must not grow into a
second cursor contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .durability_topology_definition import (
    BackendEvent,
    BackendExecutionRef,
    ExecutionSnapshot,
    ExternalEvent,
)
from .execution_host_definition import (
    RuntimeCancellationRequest,
    RuntimeWorkflowStartRequest,
)


@runtime_checkable
class WorkflowCursor(Protocol):
    """Control an already started execution; this interface grants no authority.

    A process-local implementation can resume only while its resources live.
    Durable start and authorized cancellation remain on DurableBackendAdapter.
    """

    async def apply_external_event(
        self,
        execution: BackendExecutionRef,
        event: ExternalEvent,
    ) -> ExecutionSnapshot:
        """Apply one acknowledged external event and return its snapshot."""

        ...

    async def query(self, execution: BackendExecutionRef) -> ExecutionSnapshot:
        """Return the current backend-authoritative bounded snapshot."""

        ...

    async def recover(self, execution: BackendExecutionRef) -> ExecutionSnapshot:
        """Return the same execution within this backend's recovery boundary."""

        ...

    async def list_events(
        self,
        execution: BackendExecutionRef,
    ) -> Sequence[BackendEvent]:
        """Return bounded backend events in Runtime execution identity."""

        ...


@runtime_checkable
class DurableBackendAdapter(WorkflowCursor, Protocol):
    """A durable cursor with the original authorized host start/cancel boundary."""

    async def start(self, request: RuntimeWorkflowStartRequest) -> BackendExecutionRef:
        """Start or recover one exact pinned Workflow Execution."""
        ...

    async def request_cancellation(
        self, execution: BackendExecutionRef, request: RuntimeCancellationRequest,
    ) -> ExecutionSnapshot:
        """Apply one typed authorized cancellation, preserving its host contract."""
        ...


__all__ = [
    "BackendEvent",
    "BackendExecutionRef",
    "DurableBackendAdapter",
    "ExecutionSnapshot",
    "ExternalEvent",
    "RuntimeCancellationRequest",
    "RuntimeWorkflowStartRequest",
    "WorkflowCursor",
]
