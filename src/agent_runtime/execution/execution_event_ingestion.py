"""Authorized external-event ingress and atomic application contract.

This module is the in-memory conformance implementation. Production adapters
must preserve the same immutable records and ordering in a Cell-local
serializable ledger and acknowledged durable-backend operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from typing import Any, ClassVar, Mapping

from ..foundation.foundation_contract_validation import (
    validate_id,
    validate_int,
    validate_opaque_ref,
    validate_sha256,
    validate_snake_case_name,
    validate_utc_timestamp,
)
from ..contracts.execution_operation_definition import (
    AuthorizationEffect,
    ExecutionAuthorizationBinding,
    ExecutionAuthorizationStatus,
    ExecutionAuthorizationStatusEvidence,
    ExecutionControlFenceStatus,
)
from ..contracts.execution_event_definition import (
    ExternalEventExecutionSnapshot,
    ExternalEventAcknowledgement,
    ExternalEventIngressRequest,
)
from ..contracts.registry_release_definition import (
    ReleaseAdmissionState,
    ReleaseSubjectKind,
    WorkflowRelease,
)
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from ..registry.registry_graph_projection import RUNTIME_TERMINAL_STATE_ID


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _as_datetime(value: str) -> datetime:
    validate_utc_timestamp("timestamp", value)
    return datetime.fromisoformat(value[:-1] + "+00:00")


class ExternalEventApplicationDisposition(StrEnum):
    """Bounded result of one Cell-local wait claim."""

    APPLIED = "applied"


@dataclass(frozen=True)
class TrustedRequestContext:
    """Transient server-side identity context supplied by an authenticated edge."""

    record_type: ClassVar[str] = "trusted_request_context"

    actor_principal_id: str
    tenant_id: str
    cell_id: str
    authenticated_session_ref: str

    def validate(self) -> None:
        """Validate trusted server-side identity and isolation context."""

        for label, value in (
            ("actor_principal_id", self.actor_principal_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
        ):
            validate_id(label, value)
        validate_opaque_ref("authenticated_session_ref", self.authenticated_session_ref)


@dataclass(frozen=True)
class ExecutionSnapshotToken:
    """Content-addressed optimistic-concurrency token for one durable snapshot."""

    record_type: ClassVar[str] = "execution_snapshot_token"

    snapshot_id: str
    snapshot_ref: str
    workflow_execution_id: str
    transition_sequence: int
    domain_state_id: str
    runtime_status_id: str
    snapshot_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "snapshot_sha256"
        }

    def validate(self) -> None:
        """Validate the optimistic-concurrency snapshot token and hash."""

        for label, value in (
            ("snapshot_id", self.snapshot_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("domain_state_id", self.domain_state_id),
            ("runtime_status_id", self.runtime_status_id),
        ):
            validate_id(label, value)
        validate_opaque_ref("snapshot_ref", self.snapshot_ref)
        validate_int("transition_sequence", self.transition_sequence, minimum=0)
        validate_sha256("snapshot_sha256", self.snapshot_sha256)
        if self.snapshot_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("execution snapshot token hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionSnapshotToken":
        """Build and validate one hash-complete snapshot token."""

        provisional = cls(**fields, snapshot_sha256="0" * 64)
        record = cls(
            **fields,
            snapshot_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ExternalActionAuthorizationEvidence:
    """Transport projection of one exact Product-owned external-action result."""

    record_type: ClassVar[str] = "external_action_authorization_evidence"

    effect: AuthorizationEffect
    request_ref: str
    request_sha256: str
    decision_ref: str
    decision_sha256: str
    actor_principal_id: str
    tenant_id: str
    cell_id: str
    workflow_execution_id: str
    requested_event_type: str
    decision_artifact_ref: str
    decision_artifact_sha256: str
    execution_authorization_binding_ref: str
    execution_authorization_binding_sha256: str
    effective_at_utc: str
    expiry_at_utc: str

    def validate(self) -> None:
        """Validate the exact Product external-action authority projection."""

        if type(self.effect) is not AuthorizationEffect:
            raise ValueError("effect must be an AuthorizationEffect")
        for label, value in (
            ("actor_principal_id", self.actor_principal_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
            ("workflow_execution_id", self.workflow_execution_id),
        ):
            validate_id(label, value)
        validate_snake_case_name(
            "requested_event_type", self.requested_event_type
        )
        for label, value in (
            ("request_ref", self.request_ref),
            ("decision_ref", self.decision_ref),
            ("decision_artifact_ref", self.decision_artifact_ref),
            (
                "execution_authorization_binding_ref",
                self.execution_authorization_binding_ref,
            ),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("request_sha256", self.request_sha256),
            ("decision_sha256", self.decision_sha256),
            ("decision_artifact_sha256", self.decision_artifact_sha256),
            (
                "execution_authorization_binding_sha256",
                self.execution_authorization_binding_sha256,
            ),
        ):
            validate_sha256(label, value)
        validate_utc_timestamp("effective_at_utc", self.effective_at_utc)
        validate_utc_timestamp("expiry_at_utc", self.expiry_at_utc)
        if _as_datetime(self.effective_at_utc) >= _as_datetime(self.expiry_at_utc):
            raise ValueError("external-action decision validity window is empty")


@dataclass(frozen=True)
class ExternalEvent:
    """Content-free event derived from Product authority and a Workflow edge."""

    record_type: ClassVar[str] = "external_event"

    event_id: str
    event_ref: str
    ingress_request_ref: str
    ingress_request_sha256: str
    workflow_execution_id: str
    workflow_release_ref: str
    workflow_release_sha256: str
    expected_snapshot_ref: str
    expected_snapshot_sha256: str
    expected_transition_sequence: int
    expected_domain_state: str
    target_domain_state: str
    event_type: str
    wait_policy_ref: str
    decision_artifact_ref: str
    decision_artifact_sha256: str
    product_authorization_request_ref: str
    product_authorization_request_sha256: str
    product_authorization_decision_ref: str
    product_authorization_decision_sha256: str
    execution_authorization_binding_ref: str
    execution_authorization_binding_sha256: str
    event_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "event_sha256"
        }

    def validate(self) -> None:
        """Validate the derived event, authority closure, and immutable hash."""

        for label, value in (
            ("event_id", self.event_id),
            ("workflow_execution_id", self.workflow_execution_id),
        ):
            validate_id(label, value)
        for label, value in (
            ("expected_domain_state", self.expected_domain_state),
            ("target_domain_state", self.target_domain_state),
            ("event_type", self.event_type),
        ):
            validate_snake_case_name(label, value)
        for label, value in (
            ("event_ref", self.event_ref),
            ("ingress_request_ref", self.ingress_request_ref),
            ("workflow_release_ref", self.workflow_release_ref),
            ("expected_snapshot_ref", self.expected_snapshot_ref),
            ("wait_policy_ref", self.wait_policy_ref),
            ("decision_artifact_ref", self.decision_artifact_ref),
            (
                "product_authorization_request_ref",
                self.product_authorization_request_ref,
            ),
            (
                "product_authorization_decision_ref",
                self.product_authorization_decision_ref,
            ),
            (
                "execution_authorization_binding_ref",
                self.execution_authorization_binding_ref,
            ),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("ingress_request_sha256", self.ingress_request_sha256),
            ("workflow_release_sha256", self.workflow_release_sha256),
            ("expected_snapshot_sha256", self.expected_snapshot_sha256),
            ("decision_artifact_sha256", self.decision_artifact_sha256),
            (
                "product_authorization_request_sha256",
                self.product_authorization_request_sha256,
            ),
            (
                "product_authorization_decision_sha256",
                self.product_authorization_decision_sha256,
            ),
            (
                "execution_authorization_binding_sha256",
                self.execution_authorization_binding_sha256,
            ),
            ("event_sha256", self.event_sha256),
        ):
            validate_sha256(label, value)
        validate_int(
            "expected_transition_sequence",
            self.expected_transition_sequence,
            minimum=0,
        )
        if self.event_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("external event hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ExternalEvent":
        """Build and validate one hash-complete external event."""

        provisional = cls(**fields, event_sha256="0" * 64)
        record = cls(
            **fields,
            event_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ExternalEventIngressRecord:
    """Immutable Cell outbox intent accepted before backend submission."""

    record_type: ClassVar[str] = "external_event_ingress_record"

    ingress_record_id: str
    ingress_record_ref: str
    event: ExternalEvent
    recorded_at_utc: str
    ingress_record_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "ingress_record_id": self.ingress_record_id,
            "ingress_record_ref": self.ingress_record_ref,
            "event": self.event._payload() | {"event_sha256": self.event.event_sha256},
            "recorded_at_utc": self.recorded_at_utc,
        }

    def validate(self) -> None:
        """Validate the immutable Cell outbox intent and event closure."""

        validate_id("ingress_record_id", self.ingress_record_id)
        validate_opaque_ref("ingress_record_ref", self.ingress_record_ref)
        if type(self.event) is not ExternalEvent:
            raise ValueError("event must be an exact ExternalEvent")
        self.event.validate()
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        validate_sha256("ingress_record_sha256", self.ingress_record_sha256)
        if self.ingress_record_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("external-event ingress record hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ExternalEventIngressRecord":
        """Build and validate one hash-complete ingress record."""

        provisional = cls(**fields, ingress_record_sha256="0" * 64)
        record = cls(
            **fields,
            ingress_record_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ExternalEventApplicationRecord:
    """Sole Runtime evidence that one waiting transition was applied."""

    record_type: ClassVar[str] = "external_event_application_record"

    application_record_id: str
    application_record_ref: str
    ingress_record_ref: str
    ingress_record_sha256: str
    event: ExternalEvent
    pre_application_snapshot_ref: str
    pre_application_snapshot_sha256: str
    pre_application_transition_sequence: int
    authorization_status_evidence_ref: str
    authorization_status_evidence_sha256: str
    disposition: ExternalEventApplicationDisposition
    recorded_at_utc: str
    application_record_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "application_record_id": self.application_record_id,
            "application_record_ref": self.application_record_ref,
            "ingress_record_ref": self.ingress_record_ref,
            "ingress_record_sha256": self.ingress_record_sha256,
            "event": self.event._payload() | {"event_sha256": self.event.event_sha256},
            "pre_application_snapshot_ref": self.pre_application_snapshot_ref,
            "pre_application_snapshot_sha256": self.pre_application_snapshot_sha256,
            "pre_application_transition_sequence": (
                self.pre_application_transition_sequence
            ),
            "authorization_status_evidence_ref": (
                self.authorization_status_evidence_ref
            ),
            "authorization_status_evidence_sha256": (
                self.authorization_status_evidence_sha256
            ),
            "disposition": self.disposition.value,
            "recorded_at_utc": self.recorded_at_utc,
        }

    def validate(self) -> None:
        """Validate the applied transition evidence and pre-state closure."""

        validate_id("application_record_id", self.application_record_id)
        for label, value in (
            ("application_record_ref", self.application_record_ref),
            ("ingress_record_ref", self.ingress_record_ref),
            ("pre_application_snapshot_ref", self.pre_application_snapshot_ref),
            (
                "authorization_status_evidence_ref",
                self.authorization_status_evidence_ref,
            ),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("ingress_record_sha256", self.ingress_record_sha256),
            (
                "pre_application_snapshot_sha256",
                self.pre_application_snapshot_sha256,
            ),
            (
                "authorization_status_evidence_sha256",
                self.authorization_status_evidence_sha256,
            ),
            ("application_record_sha256", self.application_record_sha256),
        ):
            validate_sha256(label, value)
        validate_int(
            "pre_application_transition_sequence",
            self.pre_application_transition_sequence,
            minimum=0,
        )
        if type(self.event) is not ExternalEvent:
            raise ValueError("event must be an exact ExternalEvent")
        self.event.validate()
        if type(self.disposition) is not ExternalEventApplicationDisposition:
            raise ValueError(
                "disposition must be ExternalEventApplicationDisposition"
            )
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        if self.application_record_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("external-event application record hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ExternalEventApplicationRecord":
        """Build and validate one hash-complete application record."""

        provisional = cls(**fields, application_record_sha256="0" * 64)
        record = cls(
            **fields,
            application_record_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


class InMemoryExternalEventIngress:
    """Duplicate-safe conformance implementation of ingress and wait claim."""

    service_id: ClassVar[str] = "in_memory_external_event_ingress"

    def __init__(self) -> None:
        self._ingress_by_request: dict[str, ExternalEventIngressRecord] = {}
        self._application_by_event: dict[str, ExternalEventApplicationRecord] = {}

    def prepare_ingress(
        self,
        *,
        request: ExternalEventIngressRequest,
        trusted_context: TrustedRequestContext,
        authorization: ExternalActionAuthorizationEvidence,
        execution_binding: ExecutionAuthorizationBinding,
        status_evidence: ExecutionAuthorizationStatusEvidence,
        snapshot: ExternalEventExecutionSnapshot,
        snapshot_token: ExecutionSnapshotToken,
        release_registry: RuntimeReleaseRegistry,
        workflow_release_ref: str,
        workflow_release_sha256: str,
        claim_at_utc: str,
    ) -> ExternalEventIngressRecord:
        """Validate authority and graph closure, then commit one outbox intent."""

        request.validate()
        trusted_context.validate()
        authorization.validate()
        execution_binding.validate()
        status_evidence.validate()
        snapshot.validate()
        snapshot_token.validate()
        validate_utc_timestamp("claim_at_utc", claim_at_utc)
        existing = self._ingress_by_request.get(request.request_ref)
        if existing is not None:
            # Once the outbox intent has committed, an exact retry must converge
            # even if admission, the execution fence, the wait snapshot, or the
            # original authority window has since changed. Those live gates
            # protect new work; they cannot invalidate an immutable committed
            # intent that a crashed caller is trying to recover.
            if existing.event.ingress_request_sha256 != request.request_sha256:
                raise ValueError("external-event ingress idempotency conflict")
            self._validate_replay_closure(
                existing=existing,
                request=request,
                trusted_context=trusted_context,
                authorization=authorization,
                execution_binding=execution_binding,
            )
            return existing
        workflow = release_registry.get_workflow(
            workflow_release_ref,
            workflow_release_sha256,
        )
        if release_registry.get_admission_state(
            ReleaseSubjectKind.WORKFLOW,
            workflow.release_ref,
        ) not in {
            ReleaseAdmissionState.PRODUCTION_CANARY,
            ReleaseAdmissionState.ACTIVE,
        }:
            raise PermissionError("Workflow Release is not admitted for ingress")
        self._validate_prepare_closure(
            request=request,
            trusted_context=trusted_context,
            authorization=authorization,
            execution_binding=execution_binding,
            status_evidence=status_evidence,
            snapshot=snapshot,
            snapshot_token=snapshot_token,
            workflow=workflow,
            claim_at_utc=claim_at_utc,
            require_wait_match=True,
        )

        routes = tuple(
            edge
            for edge in workflow.edges
            if edge.source_node_id == snapshot.domain_state_id
            and edge.outcome_id == request.requested_event_type
        )
        if len(routes) != 1:
            raise PermissionError("external event has no unique graph edge")
        route = routes[0]
        event_id = _stable_id(
            "external_event",
            request.request_sha256,
            authorization.decision_sha256,
            workflow.release_sha256,
        )
        event = ExternalEvent.build(
            event_id=event_id,
            event_ref=f"runtime-event:{event_id}",
            ingress_request_ref=request.request_ref,
            ingress_request_sha256=request.request_sha256,
            workflow_execution_id=request.workflow_execution_id,
            workflow_release_ref=workflow.release_ref,
            workflow_release_sha256=workflow.release_sha256,
            expected_snapshot_ref=request.expected_snapshot_ref,
            expected_snapshot_sha256=request.expected_snapshot_sha256,
            expected_transition_sequence=request.expected_transition_sequence,
            expected_domain_state=request.expected_domain_state,
            target_domain_state=(
                RUNTIME_TERMINAL_STATE_ID
                if route.terminal
                else str(route.target_node_id)
            ),
            event_type=request.requested_event_type,
            wait_policy_ref=snapshot.wait_policy_ref or "wait-policy:missing",
            decision_artifact_ref=request.decision_artifact_ref,
            decision_artifact_sha256=request.decision_artifact_sha256,
            product_authorization_request_ref=authorization.request_ref,
            product_authorization_request_sha256=authorization.request_sha256,
            product_authorization_decision_ref=authorization.decision_ref,
            product_authorization_decision_sha256=authorization.decision_sha256,
            execution_authorization_binding_ref=execution_binding.binding_ref,
            execution_authorization_binding_sha256=execution_binding.binding_sha256,
        )
        record_id = _stable_id(
            "external_event_ingress",
            request.request_sha256,
            event.event_sha256,
        )
        record = ExternalEventIngressRecord.build(
            ingress_record_id=record_id,
            ingress_record_ref=f"runtime-event-ingress:{record_id}",
            event=event,
            recorded_at_utc=claim_at_utc,
        )
        self._ingress_by_request[request.request_ref] = record
        return record

    def apply(
        self,
        *,
        ingress: ExternalEventIngressRecord,
        current_snapshot: ExternalEventExecutionSnapshot,
        current_snapshot_token: ExecutionSnapshotToken,
        current_status_evidence: ExecutionAuthorizationStatusEvidence,
        claim_at_utc: str,
    ) -> ExternalEventApplicationRecord:
        """Atomically revalidate the wait and commit the sole application record."""

        ingress.validate()
        current_snapshot.validate()
        current_snapshot_token.validate()
        current_status_evidence.validate()
        validate_utc_timestamp("claim_at_utc", claim_at_utc)
        event = ingress.event
        existing = self._application_by_event.get(event.event_ref)
        if existing is not None:
            # At-least-once delivery: once the application record committed,
            # the same lineage converges on it even though the wait transition
            # has already advanced the snapshot past the staleness gate below.
            if (
                existing.ingress_record_ref != ingress.ingress_record_ref
                or existing.ingress_record_sha256
                != ingress.ingress_record_sha256
                or existing.event != event
            ):
                raise ValueError(
                    "external-event application idempotency conflict"
                )
            return existing
        if (
            current_snapshot.workflow_execution_id
            != current_snapshot_token.workflow_execution_id
            or current_snapshot.domain_state_id
            != current_snapshot_token.domain_state_id
            or current_snapshot.runtime_status_id
            != current_snapshot_token.runtime_status_id
            or current_snapshot.transition_sequence
            != current_snapshot_token.transition_sequence
            or current_snapshot.workflow_execution_id
            != event.workflow_execution_id
            or
            current_snapshot_token.snapshot_ref != event.expected_snapshot_ref
            or current_snapshot_token.snapshot_sha256
            != event.expected_snapshot_sha256
            or current_snapshot_token.transition_sequence
            != event.expected_transition_sequence
            or current_snapshot.domain_state_id != event.expected_domain_state
            or current_snapshot.runtime_status_id != "waiting"
        ):
            raise PermissionError("stale_external_action")
        if (
            current_status_evidence.binding_ref
            != event.execution_authorization_binding_ref
            or current_status_evidence.binding_sha256
            != event.execution_authorization_binding_sha256
        ):
            raise ValueError("external-event authorization binding mismatch")
        if (
            current_status_evidence.status
            is not ExecutionAuthorizationStatus.EFFECTIVE
            or current_status_evidence.control_fence_status
            is not ExecutionControlFenceStatus.OPEN
        ):
            raise PermissionError("external-event authority is no longer effective")

        application_id = _stable_id(
            "external_event_application",
            ingress.ingress_record_sha256,
            current_status_evidence.evidence_sha256,
        )
        application = ExternalEventApplicationRecord.build(
            application_record_id=application_id,
            application_record_ref=f"runtime-event-application:{application_id}",
            ingress_record_ref=ingress.ingress_record_ref,
            ingress_record_sha256=ingress.ingress_record_sha256,
            event=event,
            pre_application_snapshot_ref=current_snapshot_token.snapshot_ref,
            pre_application_snapshot_sha256=(
                current_snapshot_token.snapshot_sha256
            ),
            pre_application_transition_sequence=(
                current_snapshot_token.transition_sequence
            ),
            authorization_status_evidence_ref=current_status_evidence.evidence_ref,
            authorization_status_evidence_sha256=(
                current_status_evidence.evidence_sha256
            ),
            disposition=ExternalEventApplicationDisposition.APPLIED,
            recorded_at_utc=claim_at_utc,
        )
        self._application_by_event[event.event_ref] = application
        return application

    @staticmethod
    def acknowledge(
        *,
        ingress: ExternalEventIngressRecord,
        application: ExternalEventApplicationRecord,
        post_snapshot_token: ExecutionSnapshotToken,
        backend_acknowledgement_ref: str,
        backend_acknowledgement_sha256: str,
    ) -> ExternalEventAcknowledgement:
        """Construct an acknowledgement only from one exact applied lineage."""

        ingress.validate()
        application.validate()
        post_snapshot_token.validate()
        if (
            application.ingress_record_ref != ingress.ingress_record_ref
            or application.ingress_record_sha256 != ingress.ingress_record_sha256
            or application.event != ingress.event
        ):
            raise ValueError("application and ingress lineage mismatch")
        acknowledgement = ExternalEventAcknowledgement(
            event_ref=ingress.event.event_ref,
            event_sha256=ingress.event.event_sha256,
            ingress_record_ref=ingress.ingress_record_ref,
            ingress_record_sha256=ingress.ingress_record_sha256,
            application_record_ref=application.application_record_ref,
            application_record_sha256=application.application_record_sha256,
            execution_snapshot_ref=post_snapshot_token.snapshot_ref,
            execution_snapshot_sha256=post_snapshot_token.snapshot_sha256,
            backend_acknowledgement_ref=backend_acknowledgement_ref,
            backend_acknowledgement_sha256=backend_acknowledgement_sha256,
        )
        acknowledgement.validate()
        return acknowledgement

    @staticmethod
    def _validate_prepare_closure(
        *,
        request: ExternalEventIngressRequest,
        trusted_context: TrustedRequestContext,
        authorization: ExternalActionAuthorizationEvidence,
        execution_binding: ExecutionAuthorizationBinding,
        status_evidence: ExecutionAuthorizationStatusEvidence,
        snapshot: ExternalEventExecutionSnapshot,
        snapshot_token: ExecutionSnapshotToken,
        workflow: WorkflowRelease,
        claim_at_utc: str,
        require_wait_match: bool,
    ) -> None:
        if authorization.effect is not AuthorizationEffect.ALLOW:
            raise PermissionError("external action was denied")
        if (
            request.workflow_execution_id != snapshot.workflow_execution_id
            or request.workflow_execution_id != snapshot_token.workflow_execution_id
            or request.workflow_execution_id != authorization.workflow_execution_id
            or request.workflow_execution_id != execution_binding.workflow_execution_id
        ):
            raise ValueError("external-event Workflow Execution mismatch")
        if (
            trusted_context.actor_principal_id != authorization.actor_principal_id
            or trusted_context.tenant_id != authorization.tenant_id
            or trusted_context.cell_id != authorization.cell_id
            or trusted_context.tenant_id != execution_binding.tenant_id
            or trusted_context.cell_id != execution_binding.cell_id
        ):
            raise ValueError("external-event trusted identity mismatch")
        if (
            request.requested_event_type != authorization.requested_event_type
            or request.decision_artifact_ref != authorization.decision_artifact_ref
            or request.decision_artifact_sha256
            != authorization.decision_artifact_sha256
        ):
            raise ValueError("external-event Product decision mismatch")
        if (
            authorization.execution_authorization_binding_ref
            != execution_binding.binding_ref
            or authorization.execution_authorization_binding_sha256
            != execution_binding.binding_sha256
            or status_evidence.binding_ref != execution_binding.binding_ref
            or status_evidence.binding_sha256 != execution_binding.binding_sha256
        ):
            raise ValueError("external-event execution binding mismatch")
        if (
            status_evidence.status is not ExecutionAuthorizationStatus.EFFECTIVE
            or status_evidence.control_fence_status
            is not ExecutionControlFenceStatus.OPEN
        ):
            raise PermissionError("external-event execution authority is unavailable")
        if (
            workflow.release_ref != execution_binding.workflow_release_ref
            or workflow.release_sha256 != execution_binding.workflow_release_sha256
            or snapshot.workflow_id != workflow.workflow_id
        ):
            raise ValueError("external-event Workflow Release mismatch")
        if (
            snapshot_token.domain_state_id != snapshot.domain_state_id
            or snapshot_token.runtime_status_id != snapshot.runtime_status_id
            or snapshot_token.transition_sequence != snapshot.transition_sequence
        ):
            raise PermissionError("stale_external_action")
        if require_wait_match:
            if snapshot.terminal or snapshot.runtime_status_id != "waiting":
                raise PermissionError("Workflow Execution is not waiting")
            if snapshot.wait_policy_ref is None:
                raise ValueError("waiting snapshot requires wait_policy_ref")
            if (
                request.expected_snapshot_ref != snapshot_token.snapshot_ref
                or request.expected_snapshot_sha256
                != snapshot_token.snapshot_sha256
                or request.expected_transition_sequence
                != snapshot_token.transition_sequence
                or request.expected_domain_state != snapshot.domain_state_id
            ):
                raise PermissionError("stale_external_action")
        claim_time = _as_datetime(claim_at_utc)
        if not (
            _as_datetime(authorization.effective_at_utc)
            <= claim_time
            < _as_datetime(authorization.expiry_at_utc)
            and _as_datetime(execution_binding.effective_at_utc)
            <= claim_time
            < _as_datetime(execution_binding.expiry_at_utc)
        ):
            raise PermissionError("external-event authority is outside validity")

    @staticmethod
    def _validate_replay_closure(
        *,
        existing: ExternalEventIngressRecord,
        request: ExternalEventIngressRequest,
        trusted_context: TrustedRequestContext,
        authorization: ExternalActionAuthorizationEvidence,
        execution_binding: ExecutionAuthorizationBinding,
    ) -> None:
        """Require a retry to reproduce the committed authority lineage."""

        event = existing.event
        if authorization.effect is not AuthorizationEffect.ALLOW:
            raise PermissionError("external action was denied")
        if (
            trusted_context.actor_principal_id != authorization.actor_principal_id
            or trusted_context.tenant_id != authorization.tenant_id
            or trusted_context.cell_id != authorization.cell_id
            or trusted_context.tenant_id != execution_binding.tenant_id
            or trusted_context.cell_id != execution_binding.cell_id
        ):
            raise ValueError("external-event trusted identity mismatch")
        if (
            event.ingress_request_ref != request.request_ref
            or event.ingress_request_sha256 != request.request_sha256
            or event.workflow_execution_id != request.workflow_execution_id
            or event.workflow_release_ref != execution_binding.workflow_release_ref
            or event.workflow_release_sha256
            != execution_binding.workflow_release_sha256
            or event.expected_snapshot_ref != request.expected_snapshot_ref
            or event.expected_snapshot_sha256 != request.expected_snapshot_sha256
            or event.expected_transition_sequence
            != request.expected_transition_sequence
            or event.expected_domain_state != request.expected_domain_state
            or event.event_type != request.requested_event_type
            or event.decision_artifact_ref != request.decision_artifact_ref
            or event.decision_artifact_sha256
            != request.decision_artifact_sha256
            or event.product_authorization_request_ref
            != authorization.request_ref
            or event.product_authorization_request_sha256
            != authorization.request_sha256
            or event.product_authorization_decision_ref
            != authorization.decision_ref
            or event.product_authorization_decision_sha256
            != authorization.decision_sha256
            or event.execution_authorization_binding_ref
            != execution_binding.binding_ref
            or event.execution_authorization_binding_sha256
            != execution_binding.binding_sha256
        ):
            raise ValueError("external-event ingress idempotency conflict")


__all__ = [
    "ExternalActionAuthorizationEvidence",
    "ExternalEvent",
    "ExternalEventAcknowledgement",
    "ExternalEventApplicationDisposition",
    "ExternalEventApplicationRecord",
    "ExternalEventIngressRecord",
    "ExternalEventIngressRequest",
    "ExecutionSnapshotToken",
    "InMemoryExternalEventIngress",
    "TrustedRequestContext",
]
