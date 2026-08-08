"""Provider-neutral contracts for durable Agent Runtime backends."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
import re
from typing import Any, ClassVar, Protocol, runtime_checkable

from .registry_contract_validation import (
    validate_bool,
    validate_id,
    validate_int,
    validate_opaque_ref,
    validate_sha256,
    validate_string_tuple,
    validate_token,
)
from .registry_workflow_definition import ExternalEvent


_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


def _validate_token(label: str, value: Any) -> None:
    validate_token(label, value, pattern=_TOKEN)


@dataclass(frozen=True)
class StartExecutionRequest:
    """Ref-only immutable execution identity accepted by a durable backend."""

    record_type: ClassVar[str] = "start_execution_request"

    workflow_execution_id: str
    workflow_id: str
    workflow_contract_version: str
    execution_release_ref: str
    runtime_release_ref: str
    graph_projection_ref: str
    graph_sha256: str
    cell_binding_ref: str
    cell_binding_sha256: str
    entitlement_snapshot_ref: str
    entitlement_snapshot_hash: str
    execution_input_package_refs: tuple[str, ...]
    execution_input_package_sha256: str
    max_transition_count: int
    max_dispatch_count: int
    backend_namespace: str
    task_queue: str

    def validate(self) -> None:
        """Validate exact start identity without resolving Cell-local content."""

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("workflow_id", self.workflow_id),
        ):
            validate_id(label, value)
        _validate_token("workflow_contract_version", self.workflow_contract_version)
        for label, value in (
            ("execution_release_ref", self.execution_release_ref),
            ("runtime_release_ref", self.runtime_release_ref),
            ("graph_projection_ref", self.graph_projection_ref),
            ("cell_binding_ref", self.cell_binding_ref),
            ("entitlement_snapshot_ref", self.entitlement_snapshot_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("graph_sha256", self.graph_sha256),
            ("cell_binding_sha256", self.cell_binding_sha256),
            ("entitlement_snapshot_hash", self.entitlement_snapshot_hash),
            (
                "execution_input_package_sha256",
                self.execution_input_package_sha256,
            ),
        ):
            validate_sha256(label, value)
        validate_string_tuple(
            "execution_input_package_refs",
            self.execution_input_package_refs,
            item_validator=validate_opaque_ref,
            require_non_empty=True,
        )
        validate_int("max_transition_count", self.max_transition_count, minimum=1)
        validate_int("max_dispatch_count", self.max_dispatch_count, minimum=1)
        _validate_token("backend_namespace", self.backend_namespace)
        _validate_token("task_queue", self.task_queue)

    def as_dict(self) -> dict[str, Any]:
        """Return the exact durable start codec."""

        self.validate()
        payload = asdict(self)
        payload["execution_input_package_refs"] = list(
            self.execution_input_package_refs
        )
        return payload


@dataclass(frozen=True)
class BackendExecutionRef:
    """Opaque durable-backend execution identity."""

    record_type: ClassVar[str] = "backend_execution_ref"

    backend_id: str
    backend_namespace: str
    backend_execution_id: str
    workflow_execution_id: str

    def validate(self) -> None:
        """Validate bounded execution identity."""

        validate_id("backend_id", self.backend_id)
        _validate_token("backend_namespace", self.backend_namespace)
        _validate_token("backend_execution_id", self.backend_execution_id)
        validate_id("workflow_execution_id", self.workflow_execution_id)


@dataclass(frozen=True)
class CancellationRequest:
    """Authorized ref-only request to cancel Runtime execution."""

    record_type: ClassVar[str] = "cancellation_request"

    request_id: str
    workflow_execution_id: str
    expected_domain_state_id: str
    reason_code: str
    reason_artifact_ref: str
    reason_artifact_sha256: str
    authorization_ref: str
    graph_sha256: str

    def validate(self) -> None:
        """Validate cancellation identity without raw operator prose."""

        for label, value in (
            ("request_id", self.request_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("expected_domain_state_id", self.expected_domain_state_id),
            ("reason_code", self.reason_code),
        ):
            validate_id(label, value)
        validate_opaque_ref("reason_artifact_ref", self.reason_artifact_ref)
        validate_opaque_ref("authorization_ref", self.authorization_ref)
        validate_sha256("reason_artifact_sha256", self.reason_artifact_sha256)
        validate_sha256("graph_sha256", self.graph_sha256)


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Bounded durable cursor view with separate domain and Runtime state."""

    record_type: ClassVar[str] = "execution_snapshot"

    backend_id: str
    backend_execution_id: str
    workflow_execution_id: str
    workflow_id: str
    workflow_contract_version: str
    execution_release_ref: str
    runtime_release_ref: str
    graph_sha256: str
    domain_state_id: str
    runtime_status_id: str
    transition_sequence: int
    retry_sequence: int
    terminal: bool
    active_dispatch_id: str | None = None
    wait_policy_ref: str | None = None
    committed_outcome_ref: str | None = None
    suspension_code: str | None = None
    acknowledged_event_ref: str | None = None
    acknowledged_application_ref: str | None = None
    acknowledged_cancellation_ref: str | None = None

    def validate(self) -> None:
        """Validate bounded snapshot identity and optional ref-only state."""

        for label, value in (
            ("backend_id", self.backend_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("workflow_id", self.workflow_id),
            ("domain_state_id", self.domain_state_id),
            ("runtime_status_id", self.runtime_status_id),
        ):
            validate_id(label, value)
        _validate_token("backend_execution_id", self.backend_execution_id)
        _validate_token("workflow_contract_version", self.workflow_contract_version)
        validate_opaque_ref("execution_release_ref", self.execution_release_ref)
        validate_opaque_ref("runtime_release_ref", self.runtime_release_ref)
        validate_sha256("graph_sha256", self.graph_sha256)
        validate_int("transition_sequence", self.transition_sequence, minimum=0)
        validate_int("retry_sequence", self.retry_sequence, minimum=0)
        validate_bool("terminal", self.terminal)
        if self.active_dispatch_id is not None:
            validate_id("active_dispatch_id", self.active_dispatch_id)
        for label, value in (
            ("wait_policy_ref", self.wait_policy_ref),
            ("committed_outcome_ref", self.committed_outcome_ref),
            ("acknowledged_event_ref", self.acknowledged_event_ref),
            ("acknowledged_application_ref", self.acknowledged_application_ref),
            ("acknowledged_cancellation_ref", self.acknowledged_cancellation_ref),
        ):
            if value is not None:
                validate_opaque_ref(label, value)
        if self.suspension_code is not None:
            validate_id("suspension_code", self.suspension_code)


@dataclass(frozen=True)
class BackendEvent:
    """Bounded audit projection of one durable backend event."""

    record_type: ClassVar[str] = "backend_event"

    backend_event_id: str
    backend_id: str
    backend_execution_id: str
    workflow_execution_id: str
    event_type_id: str
    payload_ref: str | None = None

    def validate(self) -> None:
        """Validate event identity and optional Cell-local payload reference."""

        for label, value in (
            ("backend_event_id", self.backend_event_id),
            ("backend_id", self.backend_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("event_type_id", self.event_type_id),
        ):
            validate_id(label, value)
        _validate_token("backend_execution_id", self.backend_execution_id)
        if self.payload_ref is not None:
            validate_opaque_ref("payload_ref", self.payload_ref)


@runtime_checkable
class DurableBackendAdapter(Protocol):
    """Canonical asynchronous control protocol for durable backends."""

    async def start(self, request: StartExecutionRequest) -> BackendExecutionRef:
        """Start one pinned ref-only workflow execution."""

        ...

    async def apply_external_event(
        self,
        execution: BackendExecutionRef,
        event: ExternalEvent,
    ) -> ExecutionSnapshot:
        """Apply one acknowledged event and return its exact snapshot."""

        ...

    async def query(self, execution: BackendExecutionRef) -> ExecutionSnapshot:
        """Return the current bounded execution snapshot."""

        ...

    async def request_cancellation(
        self,
        execution: BackendExecutionRef,
        request: CancellationRequest,
    ) -> ExecutionSnapshot:
        """Commit one typed cancellation request and return its snapshot."""

        ...

    async def recover(self, execution: BackendExecutionRef) -> ExecutionSnapshot:
        """Recover one server-authoritative durable execution."""

        ...

    async def list_events(
        self,
        execution: BackendExecutionRef,
    ) -> Sequence[BackendEvent]:
        """Return bounded backend events in local execution identity."""

        ...


__all__ = [
    "BackendEvent",
    "BackendExecutionRef",
    "CancellationRequest",
    "DurableBackendAdapter",
    "ExecutionSnapshot",
    "StartExecutionRequest",
]
