"""Immutable workflow-registration contracts shared with domain plugins."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..ledger import RuntimeExecutionRecordStore
    from ..contracts.ledger_record_definition import ExternalEventApplicationRecord


_ID = re.compile(r"^[a-z][a-z0-9_]*$")
_CAPABILITY_ID = re.compile(
    r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
)
_PYTHON_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_RUNTIME_REF = re.compile(
    rf"^{_PYTHON_IDENTIFIER}(?:\.{_PYTHON_IDENTIFIER})*:"
    rf"{_PYTHON_IDENTIFIER}(?:\.{_PYTHON_IDENTIFIER})*$"
)
_MANIFEST_ID_KEYS = (
    "state_ids",
    "module_ids",
    "artifact_kind_ids",
    "evaluation_binding_ids",
    "execution_profile_ids",
)
_REQUIRED_MANIFEST_KEYS = frozenset(
    {
        "manifest_version",
        "contract_version",
        *_MANIFEST_ID_KEYS,
    }
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_REF = re.compile(r"^[a-z][a-z0-9+.-]{0,63}:[^\s]{1,447}$")
_SECRET_MARKERS = ("password=", "token=", "secret=", "apikey=", "api_key=")


def _validate_id(label: str, value: str) -> None:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")


def _validate_sha256(label: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"invalid {label}: expected lowercase SHA-256")


def _validate_token(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(character.isspace() for character in value)
        or len(value) > 512
    ):
        raise ValueError(f"invalid {label}: bounded non-whitespace token required")
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"invalid {label}: embedded secret is forbidden")


def _validate_opaque_ref(label: str, value: str) -> None:
    if not isinstance(value, str) or not _OPAQUE_REF.fullmatch(value):
        raise ValueError(f"invalid {label}: scheme-prefixed opaque ref required")
    if value.partition(":")[0] == "data":
        raise ValueError(f"invalid {label}: inline data refs are forbidden")
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"invalid {label}: embedded secret is forbidden")


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ModuleOutcomeDisposition(StrEnum):
    """Runtime dispositions; domain terminal meaning remains opaque."""

    TRANSITION = "transition"
    WAIT = "wait"
    RETRYABLE_FAILURE = "retryable_failure"


@dataclass(frozen=True)
class ModuleDispatchRequest:
    """Minimal content-free dispatch persisted by a durable cursor."""

    workflow_execution_id: str
    workflow_id: str
    workflow_contract_version: str
    execution_release_ref: str
    graph_sha256: str
    current_state_id: str
    transition_sequence: int
    dispatch_id: str
    retry_sequence: int = 0
    workflow_release_ref: str | None = None
    workflow_release_sha256: str | None = None
    execution_profile_selection_ref: str | None = None
    execution_profile_selection_sha256: str | None = None
    module_release_ref: str | None = None
    module_release_sha256: str | None = None

    def validate(self) -> None:
        """Reject malformed identity without interpreting any domain ID."""

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("workflow_id", self.workflow_id),
            ("current_state_id", self.current_state_id),
            ("dispatch_id", self.dispatch_id),
        ):
            _validate_id(label, value)
        _validate_token("workflow_contract_version", self.workflow_contract_version)
        _validate_opaque_ref("execution_release_ref", self.execution_release_ref)
        _validate_sha256("graph_sha256", self.graph_sha256)
        if not isinstance(self.transition_sequence, int) or self.transition_sequence < 0:
            raise ValueError("transition_sequence must be a non-negative integer")
        if not isinstance(self.retry_sequence, int) or self.retry_sequence < 0:
            raise ValueError("retry_sequence must be a non-negative integer")
        target_release_values = (
            self.workflow_release_ref,
            self.workflow_release_sha256,
            self.execution_profile_selection_ref,
            self.execution_profile_selection_sha256,
        )
        if any(value is not None for value in target_release_values):
            if any(value is None for value in target_release_values):
                raise ValueError("dispatch target release closure must be complete")
            _validate_opaque_ref(
                "workflow_release_ref",
                str(self.workflow_release_ref),
            )
            _validate_sha256(
                "workflow_release_sha256",
                str(self.workflow_release_sha256),
            )
            _validate_opaque_ref(
                "execution_profile_selection_ref",
                str(self.execution_profile_selection_ref),
            )
            _validate_sha256(
                "execution_profile_selection_sha256",
                str(self.execution_profile_selection_sha256),
            )
        if (self.module_release_ref is None) != (
            self.module_release_sha256 is None
        ):
            raise ValueError("dispatch Module Release ref and hash must be paired")
        if self.module_release_ref is not None:
            _validate_opaque_ref(
                "module_release_ref",
                str(self.module_release_ref),
            )
            _validate_sha256(
                "module_release_sha256",
                str(self.module_release_sha256),
            )

    def as_dict(self) -> dict[str, Any]:
        """Return the validated ref-only transport representation."""

        self.validate()
        payload = {
            "workflow_execution_id": self.workflow_execution_id,
            "workflow_id": self.workflow_id,
            "workflow_contract_version": self.workflow_contract_version,
            "execution_release_ref": self.execution_release_ref,
            "graph_sha256": self.graph_sha256,
            "current_state_id": self.current_state_id,
            "transition_sequence": self.transition_sequence,
            "retry_sequence": self.retry_sequence,
            "dispatch_id": self.dispatch_id,
        }
        if self.workflow_release_ref is not None:
            payload.update(
                workflow_release_ref=self.workflow_release_ref,
                workflow_release_sha256=self.workflow_release_sha256,
                execution_profile_selection_ref=(
                    self.execution_profile_selection_ref
                ),
                execution_profile_selection_sha256=(
                    self.execution_profile_selection_sha256
                ),
            )
        if self.module_release_ref is not None:
            payload.update(
                module_release_ref=self.module_release_ref,
                module_release_sha256=self.module_release_sha256,
            )
        return payload


@dataclass(frozen=True)
class CellModuleDispatchContext:
    """Cell-local binding added to a durable dispatch before driver entry."""

    dispatch: ModuleDispatchRequest
    cell_binding_ref: str
    entitlement_snapshot_ref: str
    entitlement_snapshot_hash: str
    execution_authorization_binding_ref: str | None = None
    execution_authorization_binding_hash: str | None = None
    execution_start_admission_ref: str | None = None
    execution_start_admission_hash: str | None = None

    def validate(self) -> None:
        """Validate Cell and frozen-scope references without loading their bodies."""

        self.dispatch.validate()
        _validate_opaque_ref("cell_binding_ref", self.cell_binding_ref)
        _validate_opaque_ref(
            "entitlement_snapshot_ref", self.entitlement_snapshot_ref
        )
        _validate_sha256(
            "entitlement_snapshot_hash", self.entitlement_snapshot_hash
        )
        authority_values = (
            self.execution_authorization_binding_ref,
            self.execution_authorization_binding_hash,
            self.execution_start_admission_ref,
            self.execution_start_admission_hash,
        )
        if any(value is not None for value in authority_values):
            if any(value is None for value in authority_values):
                raise ValueError("dispatch authorization closure must be complete")
            _validate_opaque_ref(
                "execution_authorization_binding_ref",
                self.execution_authorization_binding_ref,
            )
            _validate_sha256(
                "execution_authorization_binding_hash",
                self.execution_authorization_binding_hash,
            )
            _validate_opaque_ref(
                "execution_start_admission_ref",
                self.execution_start_admission_ref,
            )
            _validate_sha256(
                "execution_start_admission_hash",
                self.execution_start_admission_hash,
            )

    def require_authorization_closure(self) -> tuple[str, str, str, str]:
        """Return the complete protected-dispatch closure or fail closed."""

        self.validate()
        values = (
            self.execution_authorization_binding_ref,
            self.execution_authorization_binding_hash,
            self.execution_start_admission_ref,
            self.execution_start_admission_hash,
        )
        if any(value is None for value in values):
            raise ValueError("dispatch authorization closure is required")
        return (
            str(values[0]),
            str(values[1]),
            str(values[2]),
            str(values[3]),
        )

    @property
    def workflow_execution_id(self) -> str:
        """Expose the execution identity without duplicating transport state."""

        return self.dispatch.workflow_execution_id


@dataclass(frozen=True)
class ModuleOutcome:
    """Committed, ref-only result of exactly one state dispatch."""

    dispatch_id: str
    workflow_execution_id: str
    expected_state_id: str
    disposition: ModuleOutcomeDisposition
    target_state_id: str | None
    wait_policy_ref: str | None
    module_run_id: str | None
    selection_ref: str | None
    attempt_ids: tuple[str, ...]
    evidence_artifact_refs: tuple[str, ...]
    failure_class: str | None
    outcome_ref: str
    outcome_sha256: str
    output_resolution_refs: tuple[str, ...] = ()
    output_resolution_sha256s: tuple[str, ...] = ()

    @classmethod
    def build(
        cls,
        *,
        dispatch_id: str,
        workflow_execution_id: str,
        expected_state_id: str,
        disposition: ModuleOutcomeDisposition,
        outcome_ref: str,
        target_state_id: str | None = None,
        wait_policy_ref: str | None = None,
        module_run_id: str | None = None,
        selection_ref: str | None = None,
        attempt_ids: tuple[str, ...] = (),
        evidence_artifact_refs: tuple[str, ...] = (),
        output_resolution_refs: tuple[str, ...] = (),
        output_resolution_sha256s: tuple[str, ...] = (),
        failure_class: str | None = None,
    ) -> "ModuleOutcome":
        """Build a content-addressed outcome from its immutable fields."""

        if type(disposition) is not ModuleOutcomeDisposition:
            raise ValueError("disposition must be ModuleOutcomeDisposition")

        base = {
            "dispatch_id": dispatch_id,
            "workflow_execution_id": workflow_execution_id,
            "expected_state_id": expected_state_id,
            "disposition": str(disposition),
            "target_state_id": target_state_id,
            "wait_policy_ref": wait_policy_ref,
            "module_run_id": module_run_id,
            "selection_ref": selection_ref,
            "attempt_ids": list(attempt_ids),
            "evidence_artifact_refs": list(evidence_artifact_refs),
            "output_resolution_refs": list(output_resolution_refs),
            "output_resolution_sha256s": list(output_resolution_sha256s),
            "failure_class": failure_class,
            "outcome_ref": outcome_ref,
        }
        outcome = cls(
            dispatch_id=dispatch_id,
            workflow_execution_id=workflow_execution_id,
            expected_state_id=expected_state_id,
            disposition=disposition,
            target_state_id=target_state_id,
            wait_policy_ref=wait_policy_ref,
            module_run_id=module_run_id,
            selection_ref=selection_ref,
            attempt_ids=attempt_ids,
            evidence_artifact_refs=evidence_artifact_refs,
            failure_class=failure_class,
            outcome_ref=outcome_ref,
            outcome_sha256=_canonical_sha256(base),
            output_resolution_refs=output_resolution_refs,
            output_resolution_sha256s=output_resolution_sha256s,
        )
        outcome.validate()
        return outcome

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "dispatch_id": self.dispatch_id,
            "workflow_execution_id": self.workflow_execution_id,
            "expected_state_id": self.expected_state_id,
            "disposition": str(self.disposition),
            "target_state_id": self.target_state_id,
            "wait_policy_ref": self.wait_policy_ref,
            "module_run_id": self.module_run_id,
            "selection_ref": self.selection_ref,
            "attempt_ids": list(self.attempt_ids),
            "evidence_artifact_refs": list(self.evidence_artifact_refs),
            "output_resolution_refs": list(self.output_resolution_refs),
            "output_resolution_sha256s": list(self.output_resolution_sha256s),
            "failure_class": self.failure_class,
            "outcome_ref": self.outcome_ref,
        }

    def validate(self) -> None:
        """Enforce disposition shape and content-addressed identity."""

        for label, value in (
            ("dispatch_id", self.dispatch_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("expected_state_id", self.expected_state_id),
        ):
            _validate_id(label, value)
        if type(self.disposition) is not ModuleOutcomeDisposition:
            raise ValueError("disposition must be ModuleOutcomeDisposition")
        disposition = self.disposition
        if disposition is ModuleOutcomeDisposition.TRANSITION:
            if self.target_state_id is None or self.wait_policy_ref is not None:
                raise ValueError("transition requires target_state_id and forbids wait_policy_ref")
        elif disposition is ModuleOutcomeDisposition.WAIT:
            if self.target_state_id is not None or self.wait_policy_ref is None:
                raise ValueError("wait requires wait_policy_ref and forbids target_state_id")
        elif self.target_state_id is not None or self.wait_policy_ref is not None:
            raise ValueError("retryable_failure forbids target_state_id and wait_policy_ref")
        if self.target_state_id is not None:
            _validate_id("target_state_id", self.target_state_id)
        if self.module_run_id is not None:
            _validate_id("module_run_id", self.module_run_id)
        for label, value in (
            ("wait_policy_ref", self.wait_policy_ref),
            ("selection_ref", self.selection_ref),
        ):
            if value is not None:
                _validate_opaque_ref(label, value)
        _validate_opaque_ref("outcome_ref", self.outcome_ref)
        if len(self.attempt_ids) != len(set(self.attempt_ids)):
            raise ValueError("attempt_ids must be unique")
        for attempt_id in self.attempt_ids:
            _validate_id("attempt_id", attempt_id)
        if len(self.evidence_artifact_refs) != len(set(self.evidence_artifact_refs)):
            raise ValueError("evidence_artifact_refs must be unique")
        for artifact_ref in self.evidence_artifact_refs:
            _validate_opaque_ref("evidence_artifact_ref", artifact_ref)
        if len(self.output_resolution_refs) != len(
            self.output_resolution_sha256s
        ):
            raise ValueError("Outcome output resolution refs and hashes disagree")
        if len(self.output_resolution_refs) != len(set(self.output_resolution_refs)):
            raise ValueError("Outcome output resolution refs must be unique")
        for resolution_ref in self.output_resolution_refs:
            _validate_opaque_ref("output_resolution_ref", resolution_ref)
        for resolution_sha256 in self.output_resolution_sha256s:
            _validate_sha256("output_resolution_sha256", resolution_sha256)
        if self.failure_class is not None:
            _validate_id("failure_class", self.failure_class)
        _validate_sha256("outcome_sha256", self.outcome_sha256)
        if self.outcome_sha256 != _canonical_sha256(self._identity_payload()):
            raise ValueError("outcome_sha256 does not match ModuleOutcome contents")

    def as_dict(self) -> dict[str, Any]:
        """Return the validated immutable outcome representation."""

        self.validate()
        return {**self._identity_payload(), "outcome_sha256": self.outcome_sha256}


@dataclass(frozen=True)
class ExternalEvent:
    """Authorized, graph-bound event that resumes one waiting execution."""

    event_id: str
    workflow_execution_id: str
    expected_domain_state: str
    target_domain_state: str
    event_type: str
    decision_artifact_ref: str
    decision_artifact_sha256: str
    authorization_ref: str
    graph_sha256: str

    def validate(self) -> None:
        """Validate a ref-only event without interpreting its domain IDs."""

        for label, value in (
            ("event_id", self.event_id),
            ("workflow_execution_id", self.workflow_execution_id),
            ("expected_domain_state", self.expected_domain_state),
            ("target_domain_state", self.target_domain_state),
            ("event_type", self.event_type),
        ):
            _validate_id(label, value)
        _validate_opaque_ref("decision_artifact_ref", self.decision_artifact_ref)
        _validate_sha256("decision_artifact_sha256", self.decision_artifact_sha256)
        _validate_opaque_ref("authorization_ref", self.authorization_ref)
        _validate_sha256("graph_sha256", self.graph_sha256)

    def as_dict(self) -> dict[str, str]:
        """Return the validated content-free event representation."""

        self.validate()
        return {
            "event_id": self.event_id,
            "workflow_execution_id": self.workflow_execution_id,
            "expected_domain_state": self.expected_domain_state,
            "target_domain_state": self.target_domain_state,
            "event_type": self.event_type,
            "decision_artifact_ref": self.decision_artifact_ref,
            "decision_artifact_sha256": self.decision_artifact_sha256,
            "authorization_ref": self.authorization_ref,
            "graph_sha256": self.graph_sha256,
        }


@dataclass(frozen=True)
class ResolvedArtifactRef:
    """Entitled Cell-local artifact identity returned without artifact content."""

    artifact_id: str
    artifact_kind_id: str
    artifact_ref: str
    artifact_sha256: str
    schema_version: str
    logical_name: str | None = None

    def validate(self) -> None:
        """Validate opaque identity, reference, schema token, and content hash."""

        _validate_id("artifact_id", self.artifact_id)
        _validate_id("artifact_kind_id", self.artifact_kind_id)
        _validate_opaque_ref("artifact_ref", self.artifact_ref)
        _validate_sha256("artifact_sha256", self.artifact_sha256)
        _validate_token("schema_version", self.schema_version)
        if self.logical_name is not None:
            _validate_token("logical_name", self.logical_name)


@dataclass(frozen=True)
class ResolvedExecutionProfile:
    """Hash-bound execution-profile identity resolved inside the Cell."""

    execution_profile_id: str
    execution_profile_ref: str
    execution_profile_sha256: str
    agent_execution_adapter_id: str

    def validate(self) -> None:
        """Validate the opaque profile and admitted adapter identities."""

        _validate_id("execution_profile_id", self.execution_profile_id)
        _validate_opaque_ref("execution_profile_ref", self.execution_profile_ref)
        _validate_sha256(
            "execution_profile_sha256", self.execution_profile_sha256
        )
        _validate_id(
            "agent_execution_adapter_id", self.agent_execution_adapter_id
        )


@dataclass(frozen=True)
class ModuleRunCreationRequest:
    """Domain-selected input refs for Runtime-owned Module Run creation."""

    dispatch_id: str
    state_id: str
    module_id: str
    input_refs: tuple[str, ...]

    def validate(self) -> None:
        """Validate the request without deriving meaning from its IDs."""

        for label, value in (
            ("dispatch_id", self.dispatch_id),
            ("state_id", self.state_id),
            ("module_id", self.module_id),
        ):
            _validate_id(label, value)
        if not self.input_refs:
            raise ValueError("Module Run creation requires input_refs")
        if len(self.input_refs) != len(set(self.input_refs)):
            raise ValueError("input_refs must be unique")
        for input_ref in self.input_refs:
            _validate_opaque_ref("input_ref", input_ref)


@dataclass(frozen=True)
class ModuleRunCreationResult:
    """Runtime-created Module identity and frozen input-closure hash."""

    module_run_id: str
    input_closure_sha256: str

    def validate(self) -> None:
        """Validate the Runtime-created Module handle."""

        _validate_id("module_run_id", self.module_run_id)
        _validate_sha256("input_closure_sha256", self.input_closure_sha256)


@dataclass(frozen=True)
class ExecutionOutputRegistrationRequest:
    """Content-addressed metadata for one deterministic Runtime output."""

    output_type_id: str
    schema_version: str
    media_type: str
    idempotency_key: str
    module_run_id: str | None = None
    variant_id: str | None = None
    attempt_id: str | None = None
    logical_name: str | None = None
    source_artifact_refs: tuple[str, ...] = ()

    def validate(self) -> None:
        """Validate metadata while keeping output bytes outside Runtime records."""

        _validate_id("output_type_id", self.output_type_id)
        _validate_token("schema_version", self.schema_version)
        _validate_token("media_type", self.media_type)
        _validate_id("idempotency_key", self.idempotency_key)
        if self.logical_name is not None:
            _validate_token("logical_name", self.logical_name)
        lineage = (self.module_run_id, self.variant_id, self.attempt_id)
        if self.variant_id is not None and self.module_run_id is None:
            raise ValueError("variant_id requires module_run_id")
        if self.attempt_id is not None and self.variant_id is None:
            raise ValueError("attempt_id requires variant_id")
        for label, value in zip(
            ("module_run_id", "variant_id", "attempt_id"), lineage, strict=True
        ):
            if value is not None:
                _validate_id(label, value)
        if len(self.source_artifact_refs) != len(set(self.source_artifact_refs)):
            raise ValueError("source_artifact_refs must be unique")
        for value in self.source_artifact_refs:
            _validate_opaque_ref("source_artifact_ref", value)


@dataclass(frozen=True)
class ExecutionOutputRegistrationResult:
    """Runtime-owned immutable output identity returned to the driver."""

    execution_output_id: str
    execution_output_ref: str
    execution_output_sha256: str
    output_resolution_ref: str | None = None

    def validate(self) -> None:
        """Validate the registered execution-output handle."""

        _validate_id("execution_output_id", self.execution_output_id)
        _validate_opaque_ref("execution_output_ref", self.execution_output_ref)
        _validate_sha256(
            "execution_output_sha256",
            self.execution_output_sha256,
        )
        if self.output_resolution_ref is not None:
            _validate_opaque_ref(
                "output_resolution_ref", self.output_resolution_ref
            )


@dataclass(frozen=True)
class ModuleInvocationRequest:
    """One attempt-scoped request mediated by Runtime execution services."""

    module_run_id: str
    module_id: str
    execution_profile_id: str
    resource_id: str
    action_id: str
    input_refs: tuple[str, ...]
    idempotency_key: str
    sibling_variant_count: int = 1
    sibling_variant_ordinal: int = 0

    def validate(self) -> None:
        """Validate opaque module, operation, profile, and input identities."""

        for label, value in (
            ("module_run_id", self.module_run_id),
            ("module_id", self.module_id),
            ("execution_profile_id", self.execution_profile_id),
            ("resource_id", self.resource_id),
            ("action_id", self.action_id),
            ("idempotency_key", self.idempotency_key),
        ):
            _validate_id(label, value)
        if not self.input_refs:
            raise ValueError("Module invocation requires input_refs")
        if len(self.input_refs) != len(set(self.input_refs)):
            raise ValueError("input_refs must be unique")
        for input_ref in self.input_refs:
            _validate_opaque_ref("input_ref", input_ref)
        if not isinstance(self.sibling_variant_count, int) or (
            self.sibling_variant_count < 1
        ):
            raise ValueError("sibling_variant_count must be positive")
        if not isinstance(self.sibling_variant_ordinal, int) or not (
            0 <= self.sibling_variant_ordinal < self.sibling_variant_count
        ):
            raise ValueError("sibling_variant_ordinal is outside its variant group")


@dataclass(frozen=True)
class ModuleInvocationResult:
    """Runtime-owned refs staged for the dispatch's atomic outcome commit."""

    module_run_id: str
    variant_id: str
    attempt_id: str
    execution_output_refs: tuple[str, ...]
    model_call_refs: tuple[str, ...]
    tool_call_refs: tuple[str, ...]
    usage_event_refs: tuple[str, ...]
    terminal_status: str
    failure_class: str | None = None
    output_resolution_ref: str | None = None

    def validate(self) -> None:
        """Validate lineage and returned refs without interpreting the result."""

        for label, value in (
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("terminal_status", self.terminal_status),
        ):
            _validate_id(label, value)
        if self.failure_class is not None:
            _validate_id("failure_class", self.failure_class)
        if self.output_resolution_ref is not None:
            _validate_opaque_ref("output_resolution_ref", self.output_resolution_ref)
        for label, refs in (
            ("execution_output_ref", self.execution_output_refs),
            ("model_call_ref", self.model_call_refs),
            ("tool_call_ref", self.tool_call_refs),
            ("usage_event_ref", self.usage_event_refs),
        ):
            if len(refs) != len(set(refs)):
                raise ValueError(f"{label}s must be unique")
            for value in refs:
                _validate_opaque_ref(label, value)


@runtime_checkable
class RuntimeExecutionServices(Protocol):
    """Only Runtime-mediated execution surface exposed to a Domain Driver."""

    @property
    def record_store(self) -> "RuntimeExecutionRecordStore":
        """Return the execution-pinned record store."""

        ...

    def resolve_artifact_ref(self, artifact_ref: str) -> ResolvedArtifactRef:
        """Resolve and entitlement-check one Cell-local artifact reference."""

        ...

    def read_artifact_bytes(self, artifact_ref: str) -> bytes:
        """Read entitled Cell-local content that never enters durable transport."""

        ...

    def resolve_execution_profile(
        self,
        module_id: str,
        execution_profile_id: str,
    ) -> ResolvedExecutionProfile:
        """Resolve one execution-pinned profile for an opaque module ID."""

        ...

    def create_module_run(
        self,
        request: ModuleRunCreationRequest,
    ) -> ModuleRunCreationResult:
        """Freeze domain-selected input refs into one Runtime Module Run."""

        ...

    def invoke_module(
        self,
        request: ModuleInvocationRequest,
    ) -> ModuleInvocationResult:
        """Issue grants, invoke an adapter, and stage one canonical Attempt."""

        ...

    def record_execution_output(
        self,
        request: ExecutionOutputRegistrationRequest,
        content: bytes,
    ) -> ExecutionOutputRegistrationResult:
        """Write Cell-local bytes and register one immutable Runtime output."""

        ...

    def commit_outcome(self, outcome: ModuleOutcome) -> ModuleOutcome:
        """Commit or replay one idempotent dispatch outcome."""

        ...

    def commit_external_event_application(
        self,
        record: "ExternalEventApplicationRecord",
        *,
        closes_execution: bool,
    ) -> "ExternalEventApplicationRecord":
        """Atomically acknowledge an event and any execution-context close."""

        ...


@runtime_checkable
class DomainDriver(Protocol):
    """Provider-neutral one-state domain dispatch contract."""

    def dispatch(
        self,
        context: CellModuleDispatchContext,
        services: RuntimeExecutionServices,
    ) -> ModuleOutcome:
        """Execute exactly the current opaque state and return one outcome."""

        ...


def validate_runtime_ref(label: str, value: str) -> None:
    """Validate one explicit Python ``module:attribute`` reference."""

    if (
        not isinstance(value, str)
        or len(value) > 512
        or not _RUNTIME_REF.fullmatch(value)
    ):
        raise ValueError(
            f"invalid {label}: expected an explicit module:attribute reference"
        )


def validate_capability_id(value: str) -> None:
    """Validate one bounded dotted capability identifier."""

    if (
        not isinstance(value, str)
        or len(value) > 128
        or not _CAPABILITY_ID.fullmatch(value)
    ):
        raise ValueError(f"invalid capability: {value!r}")


class WorkflowAdmissionState(StrEnum):
    """Product admission level for one domain workflow registration."""

    CONTRACT_ONLY = "contract_only"
    SHADOW_EXECUTABLE = "shadow_executable"
    PRODUCTION_CANARY = "production_canary"
    ACTIVE = "active"


@dataclass(frozen=True)
class WorkflowRuntimeRegistration:
    """Connect one domain-owned graph to shared Runtime capabilities."""

    workflow_id: str
    domain: str
    registration_version: str
    contract_version: str
    intent_ref: str
    graph_authority_ref: str
    admission_state: WorkflowAdmissionState
    capabilities: frozenset[str]
    entitlement_mode: str
    domain_manifest_ref: str | None = None
    initial_state: str | None = None
    driver_ref: str | None = None
    store_ref: str | None = None
    default_backend_id: str | None = None
    allowed_backend_ids: tuple[str, ...] = ()

    @property
    def executable(self) -> bool:
        """Return whether the registration may enter an execution path."""

        return self.admission_state is not WorkflowAdmissionState.CONTRACT_ONLY

    def validate(self) -> None:
        """Validate provider-neutral registration shape and executable closure."""

        for label, value in (
            ("workflow_id", self.workflow_id),
            ("domain", self.domain),
        ):
            _validate_id(label, value)
        if not self.registration_version or not self.contract_version:
            raise ValueError("registration and contract versions are required")
        if not self.intent_ref or not self.graph_authority_ref:
            raise ValueError("intent and graph authority refs are required")
        if not isinstance(self.capabilities, frozenset):
            raise ValueError("workflow capabilities must be an immutable frozenset")
        if not self.capabilities:
            raise ValueError("workflow registration requires capabilities")
        for capability in self.capabilities:
            validate_capability_id(capability)
        if not self.entitlement_mode:
            raise ValueError("workflow registration requires entitlement_mode")
        if self.domain_manifest_ref is not None:
            validate_runtime_ref("domain_manifest_ref", self.domain_manifest_ref)
        if self.initial_state is not None:
            _validate_id("initial_state", self.initial_state)
        if not isinstance(self.allowed_backend_ids, tuple):
            raise ValueError("allowed_backend_ids must be an immutable tuple")
        if len(self.allowed_backend_ids) != len(set(self.allowed_backend_ids)):
            raise ValueError("allowed backend ids must be unique")
        for backend_id in self.allowed_backend_ids:
            _validate_id("allowed backend id", backend_id)
        if self.executable:
            if not self.driver_ref or not self.store_ref:
                raise ValueError(
                    "executable workflow requires driver_ref and store_ref"
                )
            if not self.domain_manifest_ref:
                raise ValueError("executable workflow requires domain_manifest_ref")
            if not self.initial_state:
                raise ValueError("executable workflow requires initial_state")
            if not self.default_backend_id:
                raise ValueError("executable workflow requires default_backend_id")
            if self.default_backend_id not in self.allowed_backend_ids:
                raise ValueError("default backend must be in allowed_backend_ids")
            for label, value in (
                ("graph_authority_ref", self.graph_authority_ref),
                ("driver_ref", self.driver_ref),
                ("store_ref", self.store_ref),
            ):
                validate_runtime_ref(label, value)
        elif any(
            (
                self.domain_manifest_ref,
                self.driver_ref,
                self.store_ref,
                self.default_backend_id,
                self.allowed_backend_ids,
                self.initial_state,
            )
        ):
            raise ValueError(
                "contract-only workflow cannot advertise executable runtime refs"
            )


def validate_domain_runtime_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_contract_version: str,
    expected_initial_state: str | None = None,
) -> Mapping[str, Any]:
    """Return a top-level immutable normalized domain-manifest view."""

    missing = _REQUIRED_MANIFEST_KEYS.difference(manifest)
    if missing:
        raise ValueError(f"domain manifest missing required keys: {sorted(missing)}")
    manifest_version = manifest["manifest_version"]
    contract_version = manifest["contract_version"]
    if not isinstance(manifest_version, str) or not manifest_version:
        raise ValueError("domain manifest requires manifest_version")
    if contract_version != expected_contract_version:
        raise ValueError(
            "domain manifest contract mismatch: "
            f"{contract_version!r} != {expected_contract_version!r}"
        )

    normalized: dict[str, Any] = dict(manifest)
    for key in _MANIFEST_ID_KEYS:
        raw_values = manifest[key]
        if isinstance(raw_values, str) or not isinstance(raw_values, Sequence):
            raise ValueError(f"domain manifest {key} must be a finite sequence")
        values = tuple(raw_values)
        if key == "state_ids" and not values:
            raise ValueError("domain manifest state_ids must not be empty")
        if len(values) != len(set(values)):
            raise ValueError(f"domain manifest {key} must be unique")
        for value in values:
            if not isinstance(value, str):
                raise ValueError(f"domain manifest {key} must contain strings")
            _validate_id(f"domain manifest {key} value", value)
        normalized[key] = values

    if (
        expected_initial_state is not None
        and expected_initial_state not in normalized["state_ids"]
    ):
        raise ValueError("domain manifest does not declare registration initial_state")
    return MappingProxyType(normalized)


__all__ = [
    "CellModuleDispatchContext",
    "ModuleInvocationRequest",
    "ModuleInvocationResult",
    "DomainDriver",
    "ExternalEvent",
    "ExecutionOutputRegistrationRequest",
    "ExecutionOutputRegistrationResult",
    "ResolvedArtifactRef",
    "ResolvedExecutionProfile",
    "RuntimeExecutionServices",
    "ModuleDispatchRequest",
    "ModuleOutcome",
    "ModuleOutcomeDisposition",
    "ModuleRunCreationRequest",
    "ModuleRunCreationResult",
    "WorkflowAdmissionState",
    "WorkflowRuntimeRegistration",
    "validate_capability_id",
    "validate_domain_runtime_manifest",
    "validate_runtime_ref",
]
