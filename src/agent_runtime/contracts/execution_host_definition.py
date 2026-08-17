"""Agent Runtime-owned public interface consumed by product hosts.

Durable-backend selection and backend-native identities remain behind Runtime.
No Agency Platform type appears in this interface.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from ..foundation.foundation_contract_validation import (
    validate_bool,
    validate_id,
    validate_opaque_ref,
    validate_sha256,
    validate_string_tuple,
    validate_utc_timestamp,
)
from .execution_event_definition import (
    ExternalEventAcknowledgement,
    ExternalEventIngressRequest,
)


def _validate_utc(label: str, value: Any) -> None:
    validate_utc_timestamp(label, value)


class RuntimeExecutionStatus(StrEnum):
    """Runtime lifecycle state; it is not a domain completion verdict."""

    STARTING = "starting"
    RUNNING = "running"
    WAITING = "waiting"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    AUTHORIZATION_INVALIDATED = "authorization_invalidated"


@dataclass(frozen=True)
class RuntimeWorkflowStartRequest:
    """Authorized host request for one exact Runtime Workflow Release."""

    workflow_execution_id: str
    tenant_id: str
    cell_id: str
    workflow_release_ref: str
    workflow_release_sha256: str
    execution_release_ref: str
    execution_release_sha256: str
    execution_profile_selection_ref: str
    execution_profile_selection_sha256: str
    runtime_execution_binding_ref: str
    runtime_execution_binding_sha256: str
    execution_authorization_binding_ref: str
    execution_authorization_binding_sha256: str
    execution_start_admission_ref: str
    execution_start_admission_sha256: str
    execution_input_package_refs: tuple[str, ...]
    execution_input_package_sha256: str
    idempotency_key: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate exact release, authority, input, and idempotency bindings."""

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
            ("idempotency_key", self.idempotency_key),
        ):
            validate_id(label, value)
        for label, value in (
            ("workflow_release_ref", self.workflow_release_ref),
            ("execution_release_ref", self.execution_release_ref),
            (
                "execution_profile_selection_ref",
                self.execution_profile_selection_ref,
            ),
            ("runtime_execution_binding_ref", self.runtime_execution_binding_ref),
            (
                "execution_authorization_binding_ref",
                self.execution_authorization_binding_ref,
            ),
            ("execution_start_admission_ref", self.execution_start_admission_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("workflow_release_sha256", self.workflow_release_sha256),
            ("execution_release_sha256", self.execution_release_sha256),
            (
                "execution_profile_selection_sha256",
                self.execution_profile_selection_sha256,
            ),
            (
                "runtime_execution_binding_sha256",
                self.runtime_execution_binding_sha256,
            ),
            (
                "execution_authorization_binding_sha256",
                self.execution_authorization_binding_sha256,
            ),
            (
                "execution_start_admission_sha256",
                self.execution_start_admission_sha256,
            ),
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
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the exact JSON-compatible start-request shape."""

        self.validate()
        payload = asdict(self)
        payload["execution_input_package_refs"] = list(
            self.execution_input_package_refs
        )
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RuntimeWorkflowStartRequest":
        """Reconstruct an exact host start request without accepting extra keys."""

        expected_keys = set(cls.__dataclass_fields__)
        if set(payload) != expected_keys:
            raise ValueError("Runtime Workflow start request has an invalid shape")
        normalized = dict(payload)
        raw_refs = normalized["execution_input_package_refs"]
        if type(raw_refs) is not list:
            raise ValueError("execution_input_package_refs must be a list")
        normalized["execution_input_package_refs"] = tuple(raw_refs)
        request = cls(**normalized)
        request.validate()
        return request


@dataclass(frozen=True)
class RuntimeExecutionHandle:
    """Opaque Runtime execution identity returned to a product host."""

    workflow_execution_id: str
    tenant_id: str
    cell_id: str
    runtime_execution_ref: str
    workflow_release_ref: str
    workflow_release_sha256: str
    start_receipt_ref: str
    start_receipt_sha256: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate the host-visible identity and start receipt closure."""

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
        ):
            validate_id(label, value)
        for label, value in (
            ("runtime_execution_ref", self.runtime_execution_ref),
            ("workflow_release_ref", self.workflow_release_ref),
            ("start_receipt_ref", self.start_receipt_ref),
        ):
            validate_opaque_ref(label, value)
        validate_sha256("workflow_release_sha256", self.workflow_release_sha256)
        validate_sha256("start_receipt_sha256", self.start_receipt_sha256)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)


@dataclass(frozen=True)
class RuntimeExecutionView:
    """Bounded Runtime-owned lifecycle view for one host-visible execution."""

    handle: RuntimeExecutionHandle
    runtime_status: RuntimeExecutionStatus
    domain_state_id: str
    terminal: bool
    snapshot_ref: str
    snapshot_sha256: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate one bounded host-facing lifecycle projection."""

        self.handle.validate()
        if type(self.runtime_status) is not RuntimeExecutionStatus:
            raise ValueError("runtime_status must be RuntimeExecutionStatus")
        validate_id("domain_state_id", self.domain_state_id)
        validate_bool("terminal", self.terminal)
        validate_opaque_ref("snapshot_ref", self.snapshot_ref)
        validate_sha256("snapshot_sha256", self.snapshot_sha256)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)


@dataclass(frozen=True)
class RuntimeCancellationRequest:
    """Authorized cancellation request at the Runtime host boundary."""

    cancellation_request_id: str
    workflow_execution_id: str
    expected_snapshot_ref: str
    expected_snapshot_sha256: str
    reason_code: str
    reason_artifact_ref: str
    reason_artifact_sha256: str
    authorization_decision_ref: str
    authorization_decision_sha256: str
    idempotency_key: str
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate an authorized, snapshot-bound cancellation request."""

        for label, value in (
            ("cancellation_request_id", self.cancellation_request_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("reason_code", self.reason_code),
            ("idempotency_key", self.idempotency_key),
        ):
            validate_id(label, value)
        for label, value in (
            ("expected_snapshot_ref", self.expected_snapshot_ref),
            ("reason_artifact_ref", self.reason_artifact_ref),
            ("authorization_decision_ref", self.authorization_decision_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("expected_snapshot_sha256", self.expected_snapshot_sha256),
            ("reason_artifact_sha256", self.reason_artifact_sha256),
            ("authorization_decision_sha256", self.authorization_decision_sha256),
        ):
            validate_sha256(label, value)
        _validate_utc("recorded_at_utc", self.recorded_at_utc)

    def as_dict(self) -> dict[str, Any]:
        """Return the exact ref-only cancellation command payload."""

        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RuntimeCancellationRequest":
        """Reconstruct an exact cancellation request without extra fields."""

        if set(payload) != set(cls.__dataclass_fields__):
            raise ValueError("Runtime cancellation request has an invalid shape")
        request = cls(**payload)
        request.validate()
        return request


@dataclass(frozen=True)
class RuntimeReconciliationResult:
    """Runtime-owned reconciliation result with no backend-native payload."""

    workflow_execution_id: str
    disposition_id: str
    execution_view: RuntimeExecutionView
    evidence_refs: tuple[str, ...]
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate one evidence-bearing reconciliation result."""

        validate_id("workflow_execution_id", self.workflow_execution_id)
        validate_id("disposition_id", self.disposition_id)
        self.execution_view.validate()
        if self.execution_view.handle.workflow_execution_id != self.workflow_execution_id:
            raise ValueError("reconciliation result crossed Workflow Execution")
        validate_string_tuple(
            "evidence_refs",
            self.evidence_refs,
            item_validator=validate_opaque_ref,
            require_non_empty=True,
        )
        _validate_utc("recorded_at_utc", self.recorded_at_utc)


@runtime_checkable
class AgentRuntimeProductHostApi(Protocol):
    """Runtime-owned asynchronous control interface for product hosts."""

    async def start_workflow(
        self,
        request: RuntimeWorkflowStartRequest,
    ) -> RuntimeExecutionHandle:
        """Start one exact authorized Workflow Execution."""

        ...

    async def get_execution(
        self,
        handle: RuntimeExecutionHandle,
    ) -> RuntimeExecutionView:
        """Return one bounded Runtime execution view."""

        ...

    async def request_cancellation(
        self,
        handle: RuntimeExecutionHandle,
        request: RuntimeCancellationRequest,
    ) -> RuntimeExecutionView:
        """Request authorized cancellation and return the resulting view."""

        ...

    async def submit_external_event(
        self,
        request: ExternalEventIngressRequest,
    ) -> ExternalEventAcknowledgement:
        """Submit one authorized external event through Runtime ingress."""

        ...

    async def reconcile(
        self,
        handle: RuntimeExecutionHandle,
    ) -> RuntimeReconciliationResult:
        """Reconcile one execution against Runtime-owned evidence."""

        ...


__all__ = [
    "AgentRuntimeProductHostApi",
    "RuntimeCancellationRequest",
    "RuntimeExecutionHandle",
    "RuntimeExecutionStatus",
    "RuntimeExecutionView",
    "RuntimeReconciliationResult",
    "RuntimeWorkflowStartRequest",
]
