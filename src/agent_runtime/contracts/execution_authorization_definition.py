"""Provider-neutral execution authorization definitions owned by Runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import hashlib
import json
from typing import Any, ClassVar, Mapping

from ..foundation.foundation_contract_validation import (
    validate_bool,
    validate_id,
    validate_opaque_ref,
    validate_sha256,
    validate_snake_case_name,
    validate_string_tuple,
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


class ExecutionAuthorizationContextState(StrEnum):
    """Product-observed lifecycle state of an execution context."""

    EFFECTIVE = "effective"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ExecutionAuthorizationFenceState(StrEnum):
    """Monotonic Runtime fence state for protected dispatch."""

    OPEN = "open"
    FENCED = "fenced"


class GatewayDecisionEffect(StrEnum):
    """Terminal allow or deny effect observed from an enforcing Gateway."""

    ALLOW = "allow"
    DENY = "deny"


@dataclass(frozen=True)
class OperationAuthorizationQuery:
    """Runtime-owned operation query projected to a host authorization port."""

    query_id: str
    idempotency_key: str
    principal_id: str
    actor_workload_id: str
    operation_id: str
    resource_type: str
    resource_ref: str
    tenant_id: str
    cell_id: str
    purpose_id: str
    environment_id: str
    workflow_release_id: str
    execution_context_id: str
    enforcing_gateway_id: str
    observed_at_utc: str
    query_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "query_sha256"
        }

    def validate(self) -> None:
        """Validate query identity, scope, timestamp, and content hash."""

        for label, value in (
            ("query_id", self.query_id),
            ("idempotency_key", self.idempotency_key),
            ("principal_id", self.principal_id),
            ("actor_workload_id", self.actor_workload_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
            ("workflow_release_id", self.workflow_release_id),
            ("execution_context_id", self.execution_context_id),
        ):
            validate_id(label, value)
        for label, value in (
            ("operation_id", self.operation_id),
            ("resource_type", self.resource_type),
            ("purpose_id", self.purpose_id),
            ("environment_id", self.environment_id),
            ("enforcing_gateway_id", self.enforcing_gateway_id),
        ):
            validate_snake_case_name(label, value)
        validate_opaque_ref("resource_ref", self.resource_ref)
        validate_utc_timestamp("observed_at_utc", self.observed_at_utc)
        validate_sha256("query_sha256", self.query_sha256)
        if self.query_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("operation authorization query hash mismatch")

    def as_dict(self) -> dict[str, Any]:
        """Return the validated JSON-compatible query body."""

        self.validate()
        return {**self._payload(), "query_sha256": self.query_sha256}

    @classmethod
    def build(cls, **fields: Any) -> "OperationAuthorizationQuery":
        """Build one hash-complete operation authorization query."""

        provisional = cls(**fields, query_sha256="0" * 64)
        record = cls(
            **fields,
            query_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ProductOperationDecision:
    """Opaque Product decision projection returned to Runtime infrastructure."""

    query_id: str
    query_sha256: str
    decision_ref: str
    decision_sha256: str
    effect: GatewayDecisionEffect
    reason_code: str
    observed_at_utc: str

    def validate(self) -> None:
        """Validate the opaque Product operation decision projection."""

        validate_id("query_id", self.query_id)
        validate_sha256("query_sha256", self.query_sha256)
        validate_opaque_ref("decision_ref", self.decision_ref)
        validate_sha256("decision_sha256", self.decision_sha256)
        if type(self.effect) is not GatewayDecisionEffect:
            raise ValueError("effect must be GatewayDecisionEffect")
        validate_snake_case_name("reason_code", self.reason_code)
        validate_utc_timestamp("observed_at_utc", self.observed_at_utc)


@dataclass(frozen=True)
class ExecutionAuthorizationContextEnvelope:
    """Product-owned execution context projected through a host adapter."""

    record_type: ClassVar[str] = "execution_authorization_context_envelope"

    context_id: str
    context_ref: str
    workflow_execution_id: str
    workflow_release_id: str
    principal_id: str
    actor_workload_id: str
    tenant_id: str
    cell_id: str
    authorization_decision_ref: str
    catalog_release_ref: str
    input_scope_refs: tuple[str, ...]
    effective_at_utc: str
    expiry_at_utc: str
    context_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "context_ref": self.context_ref,
            "workflow_execution_id": self.workflow_execution_id,
            "workflow_release_id": self.workflow_release_id,
            "principal_id": self.principal_id,
            "actor_workload_id": self.actor_workload_id,
            "tenant_id": self.tenant_id,
            "cell_id": self.cell_id,
            "authorization_decision_ref": self.authorization_decision_ref,
            "catalog_release_ref": self.catalog_release_ref,
            "input_scope_refs": list(self.input_scope_refs),
            "effective_at_utc": self.effective_at_utc,
            "expiry_at_utc": self.expiry_at_utc,
        }

    def validate(self) -> None:
        """Validate the Product context identity, scope, window, and hash."""

        for label, value in (
            ("context_id", self.context_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("workflow_release_id", self.workflow_release_id),
            ("principal_id", self.principal_id),
            ("actor_workload_id", self.actor_workload_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
        ):
            validate_id(label, value)
        for label, value in (
            ("context_ref", self.context_ref),
            ("authorization_decision_ref", self.authorization_decision_ref),
            ("catalog_release_ref", self.catalog_release_ref),
        ):
            validate_opaque_ref(label, value)
        validate_string_tuple(
            "input_scope_refs",
            self.input_scope_refs,
            item_validator=validate_opaque_ref,
            require_non_empty=True,
        )
        validate_utc_timestamp("effective_at_utc", self.effective_at_utc)
        validate_utc_timestamp("expiry_at_utc", self.expiry_at_utc)
        if _as_datetime(self.effective_at_utc) >= _as_datetime(self.expiry_at_utc):
            raise ValueError("execution authorization context window is empty")
        validate_sha256("context_sha256", self.context_sha256)
        if self.context_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("execution authorization context hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionAuthorizationContextEnvelope":
        """Build one hash-complete Product context envelope."""

        provisional = cls(**fields, context_sha256="0" * 64)
        record = cls(
            **fields,
            context_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ProductAuthorizationContextStatus:
    """Opaque Product status result consumed through the Runtime client port."""

    record_type: ClassVar[str] = "product_authorization_context_status"

    status_ref: str
    context_id: str
    context_ref: str
    context_sha256: str
    state: ExecutionAuthorizationContextState
    reason_code: str
    observed_at_utc: str
    status_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "status_ref": self.status_ref,
            "context_id": self.context_id,
            "context_ref": self.context_ref,
            "context_sha256": self.context_sha256,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "observed_at_utc": self.observed_at_utc,
        }

    def validate(self) -> None:
        """Validate one Product context status observation and its hash."""

        validate_opaque_ref("status_ref", self.status_ref)
        validate_id("context_id", self.context_id)
        validate_opaque_ref("context_ref", self.context_ref)
        validate_sha256("context_sha256", self.context_sha256)
        if type(self.state) is not ExecutionAuthorizationContextState:
            raise ValueError("state must be ExecutionAuthorizationContextState")
        validate_snake_case_name("reason_code", self.reason_code)
        validate_utc_timestamp("observed_at_utc", self.observed_at_utc)
        validate_sha256("status_sha256", self.status_sha256)
        if self.status_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Product authorization context status hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ProductAuthorizationContextStatus":
        """Build one hash-complete Product context status observation."""

        provisional = cls(**fields, status_sha256="0" * 64)
        record = cls(
            **fields,
            status_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ExecutionAuthorizationContextBinding:
    """Runtime-owned immutable binding of one Product context to one run."""

    record_type: ClassVar[str] = "execution_authorization_context_binding"

    binding_id: str
    binding_ref: str
    workflow_execution_id: str
    workflow_release_id: str
    execution_input_package_ref: str
    execution_input_package_sha256: str
    context_id: str
    context_ref: str
    context_sha256: str
    principal_id: str
    actor_workload_id: str
    tenant_id: str
    cell_id: str
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
        """Validate the immutable execution-to-authorization binding."""

        for label, value in (
            ("binding_id", self.binding_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("workflow_release_id", self.workflow_release_id),
            ("context_id", self.context_id),
            ("principal_id", self.principal_id),
            ("actor_workload_id", self.actor_workload_id),
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
        ):
            validate_id(label, value)
        for label, value in (
            ("binding_ref", self.binding_ref),
            ("execution_input_package_ref", self.execution_input_package_ref),
            ("context_ref", self.context_ref),
        ):
            validate_opaque_ref(label, value)
        validate_sha256(
            "execution_input_package_sha256",
            self.execution_input_package_sha256,
        )
        validate_sha256("context_sha256", self.context_sha256)
        for label, value in (
            ("effective_at_utc", self.effective_at_utc),
            ("expiry_at_utc", self.expiry_at_utc),
            ("recorded_at_utc", self.recorded_at_utc),
        ):
            validate_utc_timestamp(label, value)
        if _as_datetime(self.effective_at_utc) >= _as_datetime(self.expiry_at_utc):
            raise ValueError("execution authorization binding window is empty")
        validate_sha256("binding_sha256", self.binding_sha256)
        if self.binding_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("execution authorization binding hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionAuthorizationContextBinding":
        """Build one hash-complete execution authorization binding."""

        provisional = cls(**fields, binding_sha256="0" * 64)
        record = cls(
            **fields,
            binding_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ExecutionAuthorizationFence:
    """Current Runtime dispatch fence derived from a Product status observation."""

    record_type: ClassVar[str] = "execution_authorization_fence"

    fence_id: str
    fence_ref: str
    binding_ref: str
    binding_sha256: str
    state: ExecutionAuthorizationFenceState
    reason_code: str
    product_status_ref: str
    product_status_sha256: str
    recorded_at_utc: str
    fence_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "fence_id": self.fence_id,
            "fence_ref": self.fence_ref,
            "binding_ref": self.binding_ref,
            "binding_sha256": self.binding_sha256,
            "state": self.state.value,
            "reason_code": self.reason_code,
            "product_status_ref": self.product_status_ref,
            "product_status_sha256": self.product_status_sha256,
            "recorded_at_utc": self.recorded_at_utc,
        }

    def validate(self) -> None:
        """Validate one monotonic dispatch fence and its Product evidence."""

        validate_id("fence_id", self.fence_id)
        for label, value in (
            ("fence_ref", self.fence_ref),
            ("binding_ref", self.binding_ref),
            ("product_status_ref", self.product_status_ref),
        ):
            validate_opaque_ref(label, value)
        validate_sha256("binding_sha256", self.binding_sha256)
        validate_sha256("product_status_sha256", self.product_status_sha256)
        if type(self.state) is not ExecutionAuthorizationFenceState:
            raise ValueError("state must be ExecutionAuthorizationFenceState")
        validate_snake_case_name("reason_code", self.reason_code)
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        validate_sha256("fence_sha256", self.fence_sha256)
        if self.fence_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("execution authorization fence hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ExecutionAuthorizationFence":
        """Build one hash-complete execution authorization fence."""

        provisional = cls(**fields, fence_sha256="0" * 64)
        record = cls(
            **fields,
            fence_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class ProtectedOperationIntent:
    """Runtime intent committed before a protected Gateway dispatch."""

    record_type: ClassVar[str] = "protected_operation_intent"

    intent_id: str
    intent_ref: str
    binding_ref: str
    binding_sha256: str
    workflow_execution_id: str
    module_run_id: str
    module_release_ref: str
    module_release_sha256: str
    operation_id: str
    resource_ref: str
    enforcing_gateway_id: str
    idempotency_key: str
    requires_grant: bool
    operation_grant_ref: str | None
    recorded_at_utc: str
    intent_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if key != "intent_sha256"
        }

    def validate(self) -> None:
        """Validate a protected-operation intent committed before dispatch."""

        for label, value in (
            ("intent_id", self.intent_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("idempotency_key", self.idempotency_key),
        ):
            validate_id(label, value)
        for label, value in (
            ("operation_id", self.operation_id),
            ("enforcing_gateway_id", self.enforcing_gateway_id),
        ):
            validate_snake_case_name(label, value)
        for label, value in (
            ("intent_ref", self.intent_ref),
            ("binding_ref", self.binding_ref),
            ("module_release_ref", self.module_release_ref),
            ("resource_ref", self.resource_ref),
        ):
            validate_opaque_ref(label, value)
        validate_sha256("binding_sha256", self.binding_sha256)
        validate_sha256("module_release_sha256", self.module_release_sha256)
        validate_bool("requires_grant", self.requires_grant)
        if self.requires_grant:
            if self.operation_grant_ref is None:
                raise ValueError("grant-required operation needs operation_grant_ref")
            validate_opaque_ref("operation_grant_ref", self.operation_grant_ref)
        elif self.operation_grant_ref is not None:
            raise ValueError("standard operation must not bind an operation grant")
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        validate_sha256("intent_sha256", self.intent_sha256)
        if self.intent_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("protected operation intent hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "ProtectedOperationIntent":
        """Build one hash-complete protected-operation intent."""

        provisional = cls(**fields, intent_sha256="0" * 64)
        record = cls(
            **fields,
            intent_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


@dataclass(frozen=True)
class GatewayAuthorizationObservation:
    """Runtime observation of the enforcing Gateway's terminal result refs."""

    record_type: ClassVar[str] = "gateway_authorization_observation"

    observation_id: str
    observation_ref: str
    intent_ref: str
    intent_sha256: str
    decision_ref: str
    decision_sha256: str
    effect: GatewayDecisionEffect
    effect_evidence_ref: str | None
    grant_disposition_ref: str | None
    recorded_at_utc: str
    observation_sha256: str

    def _payload(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "observation_ref": self.observation_ref,
            "intent_ref": self.intent_ref,
            "intent_sha256": self.intent_sha256,
            "decision_ref": self.decision_ref,
            "decision_sha256": self.decision_sha256,
            "effect": self.effect.value,
            "effect_evidence_ref": self.effect_evidence_ref,
            "grant_disposition_ref": self.grant_disposition_ref,
            "recorded_at_utc": self.recorded_at_utc,
        }

    def validate(self) -> None:
        """Validate one enforcing-Gateway decision and effect observation."""

        validate_id("observation_id", self.observation_id)
        for label, value in (
            ("observation_ref", self.observation_ref),
            ("intent_ref", self.intent_ref),
            ("decision_ref", self.decision_ref),
        ):
            validate_opaque_ref(label, value)
        validate_sha256("intent_sha256", self.intent_sha256)
        validate_sha256("decision_sha256", self.decision_sha256)
        if type(self.effect) is not GatewayDecisionEffect:
            raise ValueError("effect must be GatewayDecisionEffect")
        for label, value in (
            ("effect_evidence_ref", self.effect_evidence_ref),
            ("grant_disposition_ref", self.grant_disposition_ref),
        ):
            if value is not None:
                validate_opaque_ref(label, value)
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)
        validate_sha256("observation_sha256", self.observation_sha256)
        if self.observation_sha256 != _canonical_sha256(self._payload()):
            raise ValueError("Gateway authorization observation hash mismatch")

    @classmethod
    def build(cls, **fields: Any) -> "GatewayAuthorizationObservation":
        """Build one hash-complete Gateway authorization observation."""

        provisional = cls(**fields, observation_sha256="0" * 64)
        record = cls(
            **fields,
            observation_sha256=_canonical_sha256(provisional._payload()),
        )
        record.validate()
        return record


__all__ = [
    "ExecutionAuthorizationContextBinding",
    "ExecutionAuthorizationContextEnvelope",
    "ExecutionAuthorizationContextState",
    "ExecutionAuthorizationFence",
    "ExecutionAuthorizationFenceState",
    "GatewayAuthorizationObservation",
    "GatewayDecisionEffect",
    "OperationAuthorizationQuery",
    "ProductOperationDecision",
    "ProductAuthorizationContextStatus",
    "ProtectedOperationIntent",
]
