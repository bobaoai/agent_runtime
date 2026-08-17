"""Provider-neutral authorization evidence bound to Agent Runtime execution.

Product Authorization owns policy, decisions, delegations, grants, and denials.
This module owns only immutable Runtime records that bind those external facts
to exact Runtime release and execution identities.
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
    validate_opaque_ref,
    validate_sha256,
    validate_snake_case_name,
    validate_utc_timestamp,
)


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_datetime(value: str) -> datetime:
    validate_utc_timestamp("timestamp", value)
    return datetime.fromisoformat(value[:-1] + "+00:00")


class AuthorizationEffect(StrEnum):
    """Product-owned decision discriminator transported into Runtime."""

    ALLOW = "allow"
    DENY = "deny"


class ExecutionAuthorizationStatus(StrEnum):
    """Current Product status observed for one immutable execution binding."""

    EFFECTIVE = "effective"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"
    REVOKED = "revoked"


class ExecutionControlFenceStatus(StrEnum):
    """Whether a protected Runtime transition may still begin."""

    OPEN = "open"
    FENCED = "fenced"


class OperationAuthorizationResolution(StrEnum):
    """Terminal Runtime disposition of one Product authorization result."""

    GRANT_BOUND = "grant_bound"
    DENIED = "denied"


@dataclass(frozen=True)
class ExecutionAuthorizationBinding:
    """Immutable Product authority closure for one Workflow Execution."""

    record_type: ClassVar[str] = "execution_authorization_binding"

    binding_id: str
    binding_ref: str
    workflow_execution_id: str
    tenant_id: str
    cell_id: str
    initiating_principal_id: str
    execution_principal_id: str
    workflow_release_ref: str
    workflow_release_sha256: str
    authorization_decision_ref: str
    authorization_decision_sha256: str
    execution_principal_delegation_ref: str
    execution_principal_delegation_sha256: str
    execution_input_package_ref: str
    execution_input_package_sha256: str
    effective_at_utc: str
    expiry_at_utc: str
    recorded_at_utc: str
    binding_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "binding_sha256"
        }

    def validate(self) -> None:
        """Validate the execution authority closure, time window, and hash."""

        for label, value in (
            ("binding_id", self.binding_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
            ("initiating_principal_id", self.initiating_principal_id),
            ("execution_principal_id", self.execution_principal_id),
        ):
            validate_id(label, value)
        for label, value in (
            ("binding_ref", self.binding_ref),
            ("workflow_release_ref", self.workflow_release_ref),
            ("authorization_decision_ref", self.authorization_decision_ref),
            (
                "execution_principal_delegation_ref",
                self.execution_principal_delegation_ref,
            ),
            ("execution_input_package_ref", self.execution_input_package_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("workflow_release_sha256", self.workflow_release_sha256),
            ("authorization_decision_sha256", self.authorization_decision_sha256),
            (
                "execution_principal_delegation_sha256",
                self.execution_principal_delegation_sha256,
            ),
            ("execution_input_package_sha256", self.execution_input_package_sha256),
            ("binding_sha256", self.binding_sha256),
        ):
            validate_sha256(label, value)
        for label, value in (
            ("effective_at_utc", self.effective_at_utc),
            ("expiry_at_utc", self.expiry_at_utc),
            ("recorded_at_utc", self.recorded_at_utc),
        ):
            validate_utc_timestamp(label, value)
        if _as_datetime(self.effective_at_utc) >= _as_datetime(self.expiry_at_utc):
            raise ValueError("authorization binding validity window is empty")
        if self.binding_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("execution authorization binding hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible binding body."""

        self.validate()
        return {**self._payload(), "binding_sha256": self.binding_sha256}

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionAuthorizationBinding":
        """Build and validate one hash-complete execution binding."""

        provisional = cls(**fields, binding_sha256="0" * 64)
        record = cls(
            **fields,
            binding_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ExecutionAuthorizationStatusEvidence:
    """Current binding and execution-control evidence for one protected claim."""

    record_type: ClassVar[str] = "execution_authorization_status_evidence"

    evidence_id: str
    evidence_ref: str
    binding_ref: str
    binding_sha256: str
    status: ExecutionAuthorizationStatus
    control_fence_ref: str
    control_fence_sha256: str
    control_fence_status: ExecutionControlFenceStatus
    recorded_at_utc: str
    evidence_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_ref": self.evidence_ref,
            "binding_ref": self.binding_ref,
            "binding_sha256": self.binding_sha256,
            "status": self.status.value,
            "control_fence_ref": self.control_fence_ref,
            "control_fence_sha256": self.control_fence_sha256,
            "control_fence_status": self.control_fence_status.value,
            "recorded_at_utc": self.recorded_at_utc,
        }

    def validate(self) -> None:
        """Validate current Product status and execution-control evidence."""

        validate_id("evidence_id", self.evidence_id)
        for label, value in (
            ("evidence_ref", self.evidence_ref),
            ("binding_ref", self.binding_ref),
            ("control_fence_ref", self.control_fence_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("binding_sha256", self.binding_sha256),
            ("control_fence_sha256", self.control_fence_sha256),
            ("evidence_sha256", self.evidence_sha256),
        ):
            validate_sha256(label, value)
        if type(self.status) is not ExecutionAuthorizationStatus:
            raise ValueError("status must be an ExecutionAuthorizationStatus")
        if type(self.control_fence_status) is not ExecutionControlFenceStatus:
            raise ValueError(
                "control_fence_status must be an ExecutionControlFenceStatus"
            )
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        if self.evidence_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("execution authorization status evidence hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionAuthorizationStatusEvidence":
        """Build and validate one hash-complete status-evidence record."""

        provisional = cls(**fields, evidence_sha256="0" * 64)
        record = cls(
            **fields,
            evidence_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class OperationAuthorizationRequest:
    """Persisted exact protected-operation request constructed by Runtime."""

    record_type: ClassVar[str] = "operation_authorization_request"

    authorization_request_id: str
    authorization_request_ref: str
    workflow_execution_id: str
    execution_authorization_binding_ref: str
    execution_authorization_binding_sha256: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    module_release_ref: str
    module_release_sha256: str
    enforcement_service_id: str
    operation_id: str
    resource_id: str
    action_id: str
    data_use_purpose_id: str
    idempotency_key: str
    expiry_at_utc: str
    recorded_at_utc: str
    authorization_request_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "authorization_request_sha256"
        }

    def validate(self) -> None:
        """Validate one exact protected-operation request and validity window."""

        for label, value in (
            ("authorization_request_id", self.authorization_request_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("resource_id", self.resource_id),
            ("idempotency_key", self.idempotency_key),
        ):
            validate_id(label, value)
        for label, value in (
            ("enforcement_service_id", self.enforcement_service_id),
            ("operation_id", self.operation_id),
            ("action_id", self.action_id),
            ("data_use_purpose_id", self.data_use_purpose_id),
        ):
            validate_snake_case_name(label, value)
        for label, value in (
            ("authorization_request_ref", self.authorization_request_ref),
            (
                "execution_authorization_binding_ref",
                self.execution_authorization_binding_ref,
            ),
            ("module_release_ref", self.module_release_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            (
                "execution_authorization_binding_sha256",
                self.execution_authorization_binding_sha256,
            ),
            ("module_release_sha256", self.module_release_sha256),
            ("authorization_request_sha256", self.authorization_request_sha256),
        ):
            validate_sha256(label, value)
        validate_utc_timestamp("expiry_at_utc", self.expiry_at_utc)
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        if _as_datetime(self.recorded_at_utc) >= _as_datetime(self.expiry_at_utc):
            raise ValueError("operation authorization request is already expired")
        if self.authorization_request_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("operation authorization request hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible operation request."""

        self.validate()
        return {
            **self._payload(),
            "authorization_request_sha256": self.authorization_request_sha256,
        }

    @classmethod
    def build(cls, **fields: Any) -> "OperationAuthorizationRequest":
        """Build and validate one hash-complete operation request."""

        provisional = cls(**fields, authorization_request_sha256="0" * 64)
        record = cls(
            **fields,
            authorization_request_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ProductOperationAuthorizationResult:
    """Non-authoritative transport projection of exact Product-owned records."""

    record_type: ClassVar[str] = "product_operation_authorization_result"

    result_ref: str
    result_sha256: str
    effect: AuthorizationEffect
    authorization_request_ref: str
    authorization_request_sha256: str
    selected_authority_ref: str
    selected_authority_sha256: str
    issuer_id: str
    effective_at_utc: str
    expiry_at_utc: str

    def validate(self) -> None:
        """Validate the exact Product-owned result projection."""

        for label, value in (
            ("result_ref", self.result_ref),
            ("authorization_request_ref", self.authorization_request_ref),
            ("selected_authority_ref", self.selected_authority_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("result_sha256", self.result_sha256),
            ("authorization_request_sha256", self.authorization_request_sha256),
            ("selected_authority_sha256", self.selected_authority_sha256),
        ):
            validate_sha256(label, value)
        if type(self.effect) is not AuthorizationEffect:
            raise ValueError("effect must be an AuthorizationEffect")
        validate_id("issuer_id", self.issuer_id)
        validate_utc_timestamp("effective_at_utc", self.effective_at_utc)
        validate_utc_timestamp("expiry_at_utc", self.expiry_at_utc)
        if _as_datetime(self.effective_at_utc) >= _as_datetime(self.expiry_at_utc):
            raise ValueError("Product authorization result validity window is empty")


@dataclass(frozen=True)
class OperationGrantBindingRecord:
    """Runtime ordering evidence committed before an allowed Gateway call."""

    record_type: ClassVar[str] = "operation_grant_binding_record"

    binding_id: str
    binding_ref: str
    authorization_request_ref: str
    authorization_request_sha256: str
    product_result_ref: str
    product_result_sha256: str
    operation_grant_ref: str
    operation_grant_sha256: str
    execution_authorization_status_evidence_ref: str
    execution_authorization_status_evidence_sha256: str
    recorded_at_utc: str
    binding_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "binding_sha256"
        }

    def validate(self) -> None:
        """Validate the grant-to-request binding and immutable hash."""

        validate_id("binding_id", self.binding_id)
        for label, value in (
            ("binding_ref", self.binding_ref),
            ("authorization_request_ref", self.authorization_request_ref),
            ("product_result_ref", self.product_result_ref),
            ("operation_grant_ref", self.operation_grant_ref),
            (
                "execution_authorization_status_evidence_ref",
                self.execution_authorization_status_evidence_ref,
            ),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("authorization_request_sha256", self.authorization_request_sha256),
            ("product_result_sha256", self.product_result_sha256),
            ("operation_grant_sha256", self.operation_grant_sha256),
            (
                "execution_authorization_status_evidence_sha256",
                self.execution_authorization_status_evidence_sha256,
            ),
            ("binding_sha256", self.binding_sha256),
        ):
            validate_sha256(label, value)
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        if self.binding_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("operation grant binding hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "OperationGrantBindingRecord":
        """Build and validate one hash-complete grant binding."""

        provisional = cls(**fields, binding_sha256="0" * 64)
        record = cls(
            **fields,
            binding_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class OperationAuthorizationResolutionRecord:
    """Terminal Runtime resolution for one Product authorization result."""

    record_type: ClassVar[str] = "operation_authorization_resolution_record"

    resolution_id: str
    authorization_request_ref: str
    authorization_request_sha256: str
    product_result_ref: str
    product_result_sha256: str
    selected_authority_ref: str
    selected_authority_sha256: str
    resolution: OperationAuthorizationResolution
    operation_grant_binding_ref: str | None
    operation_grant_binding_sha256: str | None
    recorded_at_utc: str
    resolution_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "resolution_id": self.resolution_id,
            "authorization_request_ref": self.authorization_request_ref,
            "authorization_request_sha256": self.authorization_request_sha256,
            "product_result_ref": self.product_result_ref,
            "product_result_sha256": self.product_result_sha256,
            "selected_authority_ref": self.selected_authority_ref,
            "selected_authority_sha256": self.selected_authority_sha256,
            "resolution": self.resolution.value,
            "operation_grant_binding_ref": self.operation_grant_binding_ref,
            "operation_grant_binding_sha256": self.operation_grant_binding_sha256,
            "recorded_at_utc": self.recorded_at_utc,
        }

    def validate(self) -> None:
        """Validate the terminal authorization resolution and grant closure."""

        validate_id("resolution_id", self.resolution_id)
        for label, value in (
            ("authorization_request_ref", self.authorization_request_ref),
            ("product_result_ref", self.product_result_ref),
            ("selected_authority_ref", self.selected_authority_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("authorization_request_sha256", self.authorization_request_sha256),
            ("product_result_sha256", self.product_result_sha256),
            ("selected_authority_sha256", self.selected_authority_sha256),
            ("resolution_sha256", self.resolution_sha256),
        ):
            validate_sha256(label, value)
        if type(self.resolution) is not OperationAuthorizationResolution:
            raise ValueError(
                "resolution must be an OperationAuthorizationResolution"
            )
        binding_pair = (
            self.operation_grant_binding_ref,
            self.operation_grant_binding_sha256,
        )
        if self.resolution is OperationAuthorizationResolution.GRANT_BOUND:
            if any(value is None for value in binding_pair):
                raise ValueError("grant_bound resolution requires grant binding")
        elif any(value is not None for value in binding_pair):
            raise ValueError("denied resolution cannot carry grant binding")
        if self.operation_grant_binding_ref is not None:
            validate_opaque_ref(
                "operation_grant_binding_ref", self.operation_grant_binding_ref
            )
            validate_sha256(
                "operation_grant_binding_sha256",
                self.operation_grant_binding_sha256,
            )
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        if self.resolution_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("operation authorization resolution hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "OperationAuthorizationResolutionRecord":
        """Build and validate one hash-complete terminal resolution."""

        provisional = cls(**fields, resolution_sha256="0" * 64)
        record = cls(
            **fields,
            resolution_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


__all__ = [
    "AuthorizationEffect",
    "ExecutionAuthorizationBinding",
    "ExecutionAuthorizationStatus",
    "ExecutionAuthorizationStatusEvidence",
    "ExecutionControlFenceStatus",
    "OperationAuthorizationRequest",
    "OperationAuthorizationResolution",
    "OperationAuthorizationResolutionRecord",
    "OperationGrantBindingRecord",
    "ProductOperationAuthorizationResult",
]
