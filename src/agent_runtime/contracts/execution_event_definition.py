"""Public records for authorized external-event ingress and acknowledgement."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, ClassVar, Mapping

from ..foundation.foundation_contract_validation import (
    validate_bool,
    validate_id,
    validate_int,
    validate_opaque_ref,
    validate_sha256,
    validate_snake_case_name,
    validate_token,
)


_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


@dataclass(frozen=True)
class ExternalEventExecutionSnapshot:
    """Ingress-plane read model of one durable execution at wait authorization.

    This record projects the canonical cursor state
    (durability_execution_definition.ExecutionSnapshot) for external-event
    ingress and is not a second durability contract: backend_id,
    backend_execution_id, workflow_execution_id, workflow_id, graph_sha256,
    and terminal mirror the canonical snapshot, and domain_state_id mirrors
    its current_state.  The remaining fields carry the host wait vocabulary
    (runtime_status_id such as "waiting", transition and retry sequences,
    release refs, wait_policy_ref).  Ingress admits only the "waiting"
    status, so the cursor's cancellation-acknowledgement fields have no
    representation here by design.
    """

    record_type: ClassVar[str] = "external_event_execution_snapshot"

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

    def validate(self) -> None:
        """Validate the ref-only state observed by external-event ingress."""

        for label, value in (
            ("backend_id", self.backend_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("workflow_id", self.workflow_id),
            ("domain_state_id", self.domain_state_id),
            ("runtime_status_id", self.runtime_status_id),
        ):
            validate_id(label, value)
        validate_id("backend_execution_id", self.backend_execution_id)
        validate_token(
            "workflow_contract_version",
            self.workflow_contract_version,
            pattern=_TOKEN,
        )
        validate_opaque_ref("execution_release_ref", self.execution_release_ref)
        validate_opaque_ref("runtime_release_ref", self.runtime_release_ref)
        validate_sha256("graph_sha256", self.graph_sha256)
        validate_int("transition_sequence", self.transition_sequence, minimum=0)
        validate_int("retry_sequence", self.retry_sequence, minimum=0)
        validate_bool("terminal", self.terminal)
        if self.active_dispatch_id is not None:
            validate_id("active_dispatch_id", self.active_dispatch_id)
        if self.wait_policy_ref is not None:
            validate_opaque_ref("wait_policy_ref", self.wait_policy_ref)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ExternalEventIngressRequest:
    """One typed external-action intention supplied by the Product Gateway."""

    record_type: ClassVar[str] = "external_event_ingress_request"

    ingress_request_id: str
    request_ref: str
    idempotency_key: str
    workflow_execution_id: str
    expected_snapshot_ref: str
    expected_snapshot_sha256: str
    expected_transition_sequence: int
    expected_domain_state: str
    requested_event_type: str
    decision_artifact_ref: str
    decision_artifact_sha256: str
    request_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "request_sha256"
        }

    def validate(self) -> None:
        """Validate the caller intention, expected snapshot, and request hash."""

        for label, value in (
            ("ingress_request_id", self.ingress_request_id),
            ("idempotency_key", self.idempotency_key),
            ("workflow_execution_id", self.workflow_execution_id),
        ):
            validate_id(label, value)
        validate_snake_case_name(
            "expected_domain_state", self.expected_domain_state
        )
        validate_snake_case_name(
            "requested_event_type", self.requested_event_type
        )
        for label, value in (
            ("request_ref", self.request_ref),
            ("expected_snapshot_ref", self.expected_snapshot_ref),
            ("decision_artifact_ref", self.decision_artifact_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("expected_snapshot_sha256", self.expected_snapshot_sha256),
            ("decision_artifact_sha256", self.decision_artifact_sha256),
            ("request_sha256", self.request_sha256),
        ):
            validate_sha256(label, value)
        validate_int(
            "expected_transition_sequence",
            self.expected_transition_sequence,
            minimum=0,
        )
        if self.request_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("external-event ingress request hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ExternalEventIngressRequest":
        """Build and validate one hash-complete ingress request."""

        provisional = cls(**fields, request_sha256="0" * 64)
        record = cls(
            **fields,
            request_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ExternalEventAcknowledgement:
    """Content-free backend acknowledgement of one applied event."""

    record_type: ClassVar[str] = "external_event_acknowledgement"

    event_ref: str
    event_sha256: str
    ingress_record_ref: str
    ingress_record_sha256: str
    application_record_ref: str
    application_record_sha256: str
    execution_snapshot_ref: str
    execution_snapshot_sha256: str
    backend_acknowledgement_ref: str
    backend_acknowledgement_sha256: str

    def validate(self) -> None:
        """Validate the backend acknowledgement and applied snapshot refs."""

        for label, value in (
            ("event_ref", self.event_ref),
            ("ingress_record_ref", self.ingress_record_ref),
            ("application_record_ref", self.application_record_ref),
            ("execution_snapshot_ref", self.execution_snapshot_ref),
            ("backend_acknowledgement_ref", self.backend_acknowledgement_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("event_sha256", self.event_sha256),
            ("ingress_record_sha256", self.ingress_record_sha256),
            ("application_record_sha256", self.application_record_sha256),
            ("execution_snapshot_sha256", self.execution_snapshot_sha256),
            (
                "backend_acknowledgement_sha256",
                self.backend_acknowledgement_sha256,
            ),
        ):
            validate_sha256(label, value)


__all__ = [
    "ExternalEventExecutionSnapshot",
    "ExternalEventAcknowledgement",
    "ExternalEventIngressRequest",
]
