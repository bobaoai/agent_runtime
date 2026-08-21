"""Runtime-owned execution contracts shared by durable backends.

The module validates opaque execution scope, authorization snapshot identity,
backend bindings, graph state, and ref-only payload boundaries. Host principal
routing, region policy, credential selection, and data placement remain outside
Runtime core.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any

from .registry_workflow_definition import (
    validate_capability_id,
    validate_runtime_ref,
)
from ..foundation.foundation_contract_validation import validate_bool, validate_id


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE_REF = re.compile(r"^[a-z][a-z0-9+.-]{0,63}:[^\s]{1,447}$")
_SECRET_MARKERS = ("password=", "token=", "secret=", "apikey=", "api_key=")
_MAX_BACKEND_STRING_CHARS = 512
_CONTENT_KEYS = frozenset(
    {
        "body",
        "content",
        "draft",
        "prompt",
        "provider_output",
        "raw_message",
        "revision_packet",
        "search_trace",
        "source",
        "source_body",
    }
)


class BackendAdmissionState(StrEnum):
    """How far a durable backend has progressed through product admission."""

    DEPENDENCY_PROBE = "dependency_probe"
    INTEGRATION_TESTED = "integration_tested"
    CONFORMANCE_TESTED = "conformance_tested"
    PRODUCTION_CANARY = "production_canary"
    ACTIVE = "active"
    REJECTED = "rejected"


class BackendEvaluationRole(StrEnum):
    """Product decision role assigned after backend evaluation."""

    REFERENCE = "reference"
    EVALUATION_CANDIDATE = "evaluation_candidate"
    SELECTED_CANDIDATE = "selected_candidate"
    REJECTED_CANDIDATE = "rejected_candidate"


def _validate_id(label: str, value: str) -> None:
    validate_id(label, value)


def _validate_opaque_ref(label: str, value: str) -> None:
    if not isinstance(value, str) or not _OPAQUE_REF.fullmatch(value):
        raise ValueError(
            f"invalid {label}: expected a bounded scheme-prefixed opaque ref"
        )
    if value.partition(":")[0] == "data":
        raise ValueError(f"invalid {label}: inline data refs are forbidden")
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"invalid {label}: embedded secret is forbidden")


def _validate_token(label: str, value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or any(character.isspace() for character in value)
        or len(value) > 128
    ):
        raise ValueError(f"invalid {label}: bounded non-whitespace token is required")
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_MARKERS):
        raise ValueError(f"invalid {label}: embedded secret is forbidden")


def _canonical_hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


@dataclass(frozen=True)
class EntitlementSnapshot:
    """Immutable capability set bound to one workflow execution."""

    tenant_id: str
    snapshot_id: str
    capabilities: frozenset[str]
    snapshot_hash: str

    @classmethod
    def build(
        cls,
        *,
        tenant_id: str,
        snapshot_id: str,
        capabilities: Sequence[str],
    ) -> "EntitlementSnapshot":
        """Build and validate a hash-bound immutable capability snapshot."""

        normalized = frozenset(capabilities)
        digest = _canonical_hash(
            {
                "tenant_id": tenant_id,
                "snapshot_id": snapshot_id,
                "capabilities": sorted(normalized),
            }
        )
        snapshot = cls(
            tenant_id=tenant_id,
            snapshot_id=snapshot_id,
            capabilities=normalized,
            snapshot_hash=digest,
        )
        snapshot.validate()
        return snapshot

    def validate(self) -> None:
        """Validate identity, capability syntax, and the content hash."""

        _validate_id("tenant_id", self.tenant_id)
        _validate_id("snapshot_id", self.snapshot_id)
        if not self.capabilities:
            raise ValueError("entitlement snapshot requires capabilities")
        for capability in self.capabilities:
            validate_capability_id(capability)
        if not _SHA256.fullmatch(self.snapshot_hash):
            raise ValueError("invalid entitlement snapshot hash")
        expected = _canonical_hash(
            {
                "tenant_id": self.tenant_id,
                "snapshot_id": self.snapshot_id,
                "capabilities": sorted(self.capabilities),
            }
        )
        if self.snapshot_hash != expected:
            raise ValueError("entitlement snapshot hash does not match contents")


@dataclass(frozen=True)
class DurableExecutionBinding:
    """Host-supplied opaque scope and endpoint refs for one durable execution."""

    tenant_id: str
    cell_id: str
    backend_id: str
    backend_namespace: str
    backend_endpoint_ref: str
    backend_persistence_ref: str

    def validate(self) -> None:
        """Validate execution scope and durable-backend references."""

        for label, value in (
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
            ("backend_id", self.backend_id),
            ("backend_namespace", self.backend_namespace),
        ):
            _validate_id(label, value)
        for label, value in (
            ("backend_endpoint_ref", self.backend_endpoint_ref),
            ("backend_persistence_ref", self.backend_persistence_ref),
        ):
            _validate_opaque_ref(label, value)


@dataclass(frozen=True)
class ArtifactRef:
    """Content-addressed reference passed across the backend payload boundary."""

    artifact_id: str
    uri_ref: str
    sha256: str

    def validate(self) -> None:
        """Validate the artifact identity, opaque reference, and content hash."""

        _validate_id("artifact_id", self.artifact_id)
        _validate_opaque_ref("uri_ref", self.uri_ref)
        if not _SHA256.fullmatch(self.sha256):
            raise ValueError(f"invalid artifact sha256: {self.sha256!r}")

    @classmethod
    def from_backend_payload(cls, payload: Mapping[str, Any]) -> "ArtifactRef":
        """Reconstruct one exact artifact-ref codec from backend history."""

        if set(payload) != {"artifact_id", "uri_ref", "sha256"}:
            raise ValueError("backend artifact ref has an invalid shape")
        artifact = cls(
            artifact_id=payload["artifact_id"],
            uri_ref=payload["uri_ref"],
            sha256=payload["sha256"],
        )
        artifact.validate()
        return artifact


@dataclass(frozen=True)
class ExecutionEnvelope:
    """Workflow-scoped identity and artifact references frozen at intake."""

    tenant_id: str
    cell_id: str
    principal_ref: str
    workflow_id: str
    workflow_contract_version: str
    workflow_execution_id: str
    entitlement_snapshot_id: str
    entitlement_snapshot_hash: str
    artifacts: tuple[ArtifactRef, ...]

    def validate_ref_only_identity(self) -> None:
        """Validate content-free execution identity without a host Cell lookup."""

        for label, value in (
            ("tenant_id", self.tenant_id),
            ("cell_id", self.cell_id),
            ("workflow_id", self.workflow_id),
            ("workflow_execution_id", self.workflow_execution_id),
        ):
            _validate_id(label, value)
        _validate_opaque_ref("principal_ref", self.principal_ref)
        _validate_token("workflow_contract_version", self.workflow_contract_version)
        _validate_id("entitlement_snapshot_id", self.entitlement_snapshot_id)
        if not _SHA256.fullmatch(self.entitlement_snapshot_hash):
            raise ValueError("invalid entitlement snapshot hash")
        if not self.artifacts:
            raise ValueError("execution envelope requires artifact refs")
        for artifact in self.artifacts:
            artifact.validate()

    def validate(self, binding: DurableExecutionBinding) -> None:
        """Validate workflow identity against its injected durable binding."""

        binding.validate()
        self.validate_ref_only_identity()
        if self.tenant_id != binding.tenant_id or self.cell_id != binding.cell_id:
            raise PermissionError("execution envelope does not match routed cell")

    def to_backend_payload(
        self,
        binding: DurableExecutionBinding,
    ) -> dict[str, Any]:
        """Return the validated content-free workflow-start identity payload."""

        self.validate(binding)
        payload: dict[str, Any] = {
            "tenant_id": self.tenant_id,
            "cell_id": self.cell_id,
            "principal_ref": self.principal_ref,
            "workflow_id": self.workflow_id,
            "workflow_contract_version": self.workflow_contract_version,
            "workflow_execution_id": self.workflow_execution_id,
            "entitlement_snapshot_id": self.entitlement_snapshot_id,
            "entitlement_snapshot_hash": self.entitlement_snapshot_hash,
            "artifacts": [
                {
                    "artifact_id": artifact.artifact_id,
                    "uri_ref": artifact.uri_ref,
                    "sha256": artifact.sha256,
                }
                for artifact in self.artifacts
            ],
        }
        assert_ref_only_backend_payload(payload)
        return payload

    @classmethod
    def from_backend_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> "ExecutionEnvelope":
        """Reconstruct the exact predecessor start codec from backend history."""

        expected_keys = {
            "tenant_id",
            "cell_id",
            "principal_ref",
            "workflow_id",
            "workflow_contract_version",
            "workflow_execution_id",
            "entitlement_snapshot_id",
            "entitlement_snapshot_hash",
            "artifacts",
        }
        if set(payload) != expected_keys:
            raise ValueError("backend execution envelope has an invalid shape")
        raw_artifacts = payload["artifacts"]
        if type(raw_artifacts) is not list:
            raise ValueError("backend execution artifacts must be a list")
        envelope = cls(
            tenant_id=payload["tenant_id"],
            cell_id=payload["cell_id"],
            principal_ref=payload["principal_ref"],
            workflow_id=payload["workflow_id"],
            workflow_contract_version=payload["workflow_contract_version"],
            workflow_execution_id=payload["workflow_execution_id"],
            entitlement_snapshot_id=payload["entitlement_snapshot_id"],
            entitlement_snapshot_hash=payload["entitlement_snapshot_hash"],
            artifacts=tuple(
                ArtifactRef.from_backend_payload(artifact)
                for artifact in raw_artifacts
            ),
        )
        envelope.validate_ref_only_identity()
        return envelope


def assert_ref_only_backend_payload(
    payload: Mapping[str, Any],
    *,
    forbidden_sentinels: Sequence[str] = (),
) -> None:
    """Reject content fields and known private sentinels in backend payloads."""

    def visit(value: Any, path: str) -> None:
        if isinstance(value, bytes):
            raise ValueError(f"backend payload contains bytes at {path}")
        if isinstance(value, Mapping):
            for key, child in value.items():
                if str(key).lower() in _CONTENT_KEYS:
                    raise ValueError(
                        f"backend payload contains content field at {path}.{key}"
                    )
                visit(child, f"{path}.{key}")
            return
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")
            return
        if isinstance(value, str):
            if len(value) > _MAX_BACKEND_STRING_CHARS:
                raise ValueError(f"backend payload string is too large at {path}")
            lowered = value.lower()
            if any(marker in lowered for marker in _SECRET_MARKERS):
                raise ValueError(f"backend payload contains embedded secret at {path}")
            for sentinel in forbidden_sentinels:
                if sentinel and sentinel in value:
                    raise ValueError(f"backend payload contains private sentinel at {path}")

    visit(payload, "payload")


@dataclass(frozen=True)
class FrozenExecutionScope:
    """Entitlement binding fixed for the complete Workflow Execution."""

    workflow_execution_id: str
    entitlement_snapshot_id: str
    entitlement_snapshot_hash: str

    @classmethod
    def bind(
        cls,
        workflow_execution_id: str,
        snapshot: EntitlementSnapshot,
    ) -> "FrozenExecutionScope":
        """Bind one Workflow Execution to the supplied entitlement snapshot."""

        _validate_id("workflow_execution_id", workflow_execution_id)
        snapshot.validate()
        return cls(
            workflow_execution_id=workflow_execution_id,
            entitlement_snapshot_id=snapshot.snapshot_id,
            entitlement_snapshot_hash=snapshot.snapshot_hash,
        )

    def assert_compatible(self, snapshot: EntitlementSnapshot) -> None:
        """Reject any entitlement change during the Workflow Execution."""

        snapshot.validate()
        if (
            snapshot.snapshot_id != self.entitlement_snapshot_id
            or snapshot.snapshot_hash != self.entitlement_snapshot_hash
        ):
            raise PermissionError("entitlement changed during workflow execution")


@dataclass(frozen=True)
class ModuleVariantBinding:
    """Bind one model or backend variant to its shared Module Run identity."""

    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    backend_id: str
    execution_profile_ref: str
    entitlement_snapshot_hash: str

    def validate(self) -> None:
        """Validate Module, Variant, backend, profile, and entitlement identity."""

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("backend_id", self.backend_id),
        ):
            _validate_id(label, value)
        _validate_opaque_ref("execution_profile_ref", self.execution_profile_ref)
        if not _SHA256.fullmatch(self.entitlement_snapshot_hash):
            raise ValueError("invalid entitlement snapshot hash")


@dataclass(frozen=True)
class BackendDescriptor:
    """Code-owned capability and readiness metadata for a durable backend."""

    backend_id: str
    adapter_contract_version: str
    sdk_package: str
    admission_state: BackendAdmissionState
    evaluation_role: BackendEvaluationRole
    implementation_ref: str | None
    supports_isolated_namespace: bool
    supports_shared_namespace: bool
    requires_external_service: bool

    def validate(self) -> None:
        """Validate backend readiness metadata and role-state compatibility."""

        _validate_id("backend_id", self.backend_id)
        _validate_token(
            "adapter_contract_version", self.adapter_contract_version
        )
        _validate_token("sdk_package", self.sdk_package)
        if not isinstance(self.admission_state, BackendAdmissionState):
            raise ValueError("backend admission_state must use the Runtime enum")
        if not isinstance(self.evaluation_role, BackendEvaluationRole):
            raise ValueError("backend evaluation_role must use the Runtime enum")
        for label, value in (
            ("supports_isolated_namespace", self.supports_isolated_namespace),
            ("supports_shared_namespace", self.supports_shared_namespace),
            ("requires_external_service", self.requires_external_service),
        ):
            if type(value) is not bool:
                raise ValueError(f"backend {label} must be boolean")
        if not self.supports_isolated_namespace:
            raise ValueError(
                "Runtime backend must support an isolated execution namespace"
            )
        if self.admission_state not in {
            BackendAdmissionState.DEPENDENCY_PROBE,
            BackendAdmissionState.REJECTED,
        }:
            if not self.implementation_ref:
                raise ValueError(
                    "backend beyond dependency probe requires implementation_ref"
                )
        if self.implementation_ref is not None:
            _validate_token("implementation_ref", self.implementation_ref)
        valid_states_by_role = {
            BackendEvaluationRole.REFERENCE: {
                BackendAdmissionState.INTEGRATION_TESTED,
                BackendAdmissionState.PRODUCTION_CANARY,
                BackendAdmissionState.ACTIVE,
            },
            BackendEvaluationRole.EVALUATION_CANDIDATE: {
                BackendAdmissionState.DEPENDENCY_PROBE,
                BackendAdmissionState.CONFORMANCE_TESTED,
                BackendAdmissionState.INTEGRATION_TESTED,
            },
            BackendEvaluationRole.SELECTED_CANDIDATE: {
                BackendAdmissionState.CONFORMANCE_TESTED,
                BackendAdmissionState.INTEGRATION_TESTED,
                BackendAdmissionState.PRODUCTION_CANARY,
                BackendAdmissionState.ACTIVE,
            },
            BackendEvaluationRole.REJECTED_CANDIDATE: {
                BackendAdmissionState.REJECTED,
            },
        }
        if self.admission_state not in valid_states_by_role[self.evaluation_role]:
            raise ValueError(
                "backend admission_state and evaluation_role are incompatible"
            )


_BACKEND_DESCRIPTOR_FIELDS = (
    "backend_id",
    "adapter_contract_version",
    "sdk_package",
    "admission_state",
    "evaluation_role",
    "implementation_ref",
    "supports_isolated_namespace",
    "supports_shared_namespace",
    "requires_external_service",
)


def normalize_backend_descriptor(descriptor: object) -> BackendDescriptor:
    """Normalize one structurally compatible descriptor into this import lane.

    A source checkout can temporarily expose both ``agent_runtime.*`` and
    ``src.agent_runtime``.  Python then creates distinct dataclass and StrEnum
    identities from the same source file.  The candidate set is the composition
    boundary, so it copies the exact public fields into its own lane before it
    performs identity-sensitive admission checks.  This applies to every
    backend descriptor and contains no backend-specific exception.
    """

    if isinstance(descriptor, BackendDescriptor):
        descriptor.validate()
        return descriptor

    if isinstance(descriptor, Mapping):
        if set(descriptor) != set(_BACKEND_DESCRIPTOR_FIELDS):
            raise ValueError("backend descriptor mapping has an invalid shape")
        values = {field: descriptor[field] for field in _BACKEND_DESCRIPTOR_FIELDS}
    else:
        missing_fields = tuple(
            field
            for field in _BACKEND_DESCRIPTOR_FIELDS
            if not hasattr(descriptor, field)
        )
        if missing_fields:
            raise ValueError(
                "backend descriptor is missing public fields: "
                f"{', '.join(missing_fields)}"
            )
        values = {
            field: getattr(descriptor, field)
            for field in _BACKEND_DESCRIPTOR_FIELDS
        }

    admission_state = values["admission_state"]
    evaluation_role = values["evaluation_role"]
    if not isinstance(admission_state, str):
        raise ValueError("backend admission_state must be a string enum value")
    if not isinstance(evaluation_role, str):
        raise ValueError("backend evaluation_role must be a string enum value")
    try:
        normalized_admission_state = BackendAdmissionState(admission_state)
        normalized_evaluation_role = BackendEvaluationRole(evaluation_role)
    except ValueError as exc:
        raise ValueError("backend descriptor has an unknown enum value") from exc

    normalized = BackendDescriptor(
        backend_id=values["backend_id"],
        adapter_contract_version=values["adapter_contract_version"],
        sdk_package=values["sdk_package"],
        admission_state=normalized_admission_state,
        evaluation_role=normalized_evaluation_role,
        implementation_ref=values["implementation_ref"],
        supports_isolated_namespace=values["supports_isolated_namespace"],
        supports_shared_namespace=values["supports_shared_namespace"],
        requires_external_service=values["requires_external_service"],
    )
    normalized.validate()
    return normalized


@dataclass(frozen=True)
class GraphStateProjection:
    """One state and its allowed successors in a compiled domain graph."""

    state_id: str
    allowed_next_state_ids: tuple[str, ...]

    def validate(self) -> None:
        """Validate a deterministic, duplicate-free adjacency row."""

        _validate_id("state_id", self.state_id)
        if tuple(sorted(self.allowed_next_state_ids)) != self.allowed_next_state_ids:
            raise ValueError("allowed next states must use stable sorted order")
        if len(self.allowed_next_state_ids) != len(set(self.allowed_next_state_ids)):
            raise ValueError("allowed next states must be unique")
        for target in self.allowed_next_state_ids:
            _validate_id("allowed_next_state_id", target)

    def to_backend_payload(self) -> dict[str, Any]:
        """Return the content-free adjacency row persisted by a backend."""

        self.validate()
        return {
            "state_id": self.state_id,
            "allowed_next_state_ids": list(self.allowed_next_state_ids),
        }


@dataclass(frozen=True)
class WorkflowGraphProjection:
    """Immutable backend projection compiled from one domain graph authority."""

    projection_version: str
    workflow_id: str
    workflow_contract_version: str
    graph_authority_ref: str
    initial_state: str
    terminal_states: tuple[str, ...]
    states: tuple[GraphStateProjection, ...]
    graph_sha256: str

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "projection_version": self.projection_version,
            "workflow_id": self.workflow_id,
            "workflow_contract_version": self.workflow_contract_version,
            "graph_authority_ref": self.graph_authority_ref,
            "initial_state": self.initial_state,
            "terminal_states": list(self.terminal_states),
            "states": [state.to_backend_payload() for state in self.states],
        }

    def computed_sha256(self) -> str:
        """Return the content hash implied by the projection fields."""

        return _canonical_hash(self._identity_payload())

    def validate(self) -> None:
        """Validate topology closure and its content-addressed identity."""

        _validate_token("projection_version", self.projection_version)
        _validate_id("workflow_id", self.workflow_id)
        _validate_token(
            "workflow_contract_version", self.workflow_contract_version
        )
        validate_runtime_ref("graph_authority_ref", self.graph_authority_ref)
        _validate_id("initial_state", self.initial_state)
        if not self.states:
            raise ValueError("workflow graph projection requires states")
        if tuple(sorted(self.states, key=lambda row: row.state_id)) != self.states:
            raise ValueError("workflow graph states must use stable sorted order")
        state_ids = tuple(state.state_id for state in self.states)
        if len(state_ids) != len(set(state_ids)):
            raise ValueError("workflow graph states must be unique")
        for state in self.states:
            state.validate()
        state_id_set = set(state_ids)
        if self.initial_state not in state_id_set:
            raise ValueError("workflow graph initial_state is not declared")
        targets = {
            target
            for state in self.states
            for target in state.allowed_next_state_ids
        }
        undeclared = targets - state_id_set
        if undeclared:
            raise ValueError(
                f"workflow graph targets undeclared states: {sorted(undeclared)}"
            )
        expected_terminal_states = tuple(
            state.state_id
            for state in self.states
            if not state.allowed_next_state_ids
        )
        if self.terminal_states != expected_terminal_states:
            raise ValueError("workflow graph terminal_states do not match adjacency")
        if not self.terminal_states:
            raise ValueError("workflow graph requires a terminal state")
        if not _SHA256.fullmatch(self.graph_sha256):
            raise ValueError("invalid workflow graph sha256")
        if self.graph_sha256 != self.computed_sha256():
            raise ValueError("workflow graph sha256 does not match projection")

    def to_backend_payload(self) -> dict[str, Any]:
        """Return the validated ref-only graph projection."""

        self.validate()
        payload = {**self._identity_payload(), "graph_sha256": self.graph_sha256}
        assert_ref_only_backend_payload(payload)
        return payload

    @classmethod
    def from_backend_payload(
        cls,
        payload: Mapping[str, Any],
    ) -> "WorkflowGraphProjection":
        """Rebuild and validate a projection read from backend persistence."""

        expected_keys = {
            "projection_version",
            "workflow_id",
            "workflow_contract_version",
            "graph_authority_ref",
            "initial_state",
            "terminal_states",
            "states",
            "graph_sha256",
        }
        if set(payload) != expected_keys:
            raise ValueError("workflow graph projection has an invalid shape")
        raw_states = payload["states"]
        if not isinstance(raw_states, Sequence) or isinstance(raw_states, str | bytes):
            raise ValueError("workflow graph states must be a sequence")
        states: list[GraphStateProjection] = []
        for row in raw_states:
            if not isinstance(row, Mapping) or set(row) != {
                "state_id",
                "allowed_next_state_ids",
            }:
                raise ValueError("workflow graph state has an invalid shape")
            raw_targets = row["allowed_next_state_ids"]
            if type(raw_targets) is not list:
                raise ValueError("workflow graph state targets must be a list")
            state = GraphStateProjection(
                state_id=row["state_id"],
                allowed_next_state_ids=tuple(raw_targets),
            )
            state.validate()
            states.append(state)
        projection = cls(
            projection_version=str(payload["projection_version"]),
            workflow_id=str(payload["workflow_id"]),
            workflow_contract_version=str(payload["workflow_contract_version"]),
            graph_authority_ref=str(payload["graph_authority_ref"]),
            initial_state=str(payload["initial_state"]),
            terminal_states=tuple(str(row) for row in payload["terminal_states"]),
            states=tuple(states),
            graph_sha256=str(payload["graph_sha256"]),
        )
        projection.validate()
        return projection

    def allowed_targets(self, state_id: str) -> tuple[str, ...]:
        """Return the declared successors for one state, failing on unknown state."""

        _validate_id("state_id", state_id)
        for state in self.states:
            if state.state_id == state_id:
                return state.allowed_next_state_ids
        raise KeyError(f"unknown projected workflow state: {state_id}")


@dataclass(frozen=True)
class ExternalEvent:
    """One idempotent, evidence-bearing request to advance a durable cursor."""

    event_id: str
    event_type: str
    workflow_execution_id: str
    expected_state: str
    target_state: str
    evidence_ref: str

    def validate(self) -> None:
        """Validate identity and keep event content outside backend persistence."""

        for label, value in (
            ("event_id", self.event_id),
            ("event_type", self.event_type),
            ("workflow_execution_id", self.workflow_execution_id),
            ("expected_state", self.expected_state),
            ("target_state", self.target_state),
        ):
            _validate_id(label, value)
        _validate_opaque_ref("evidence_ref", self.evidence_ref)

    def to_backend_payload(self) -> dict[str, str]:
        """Return the validated event payload accepted by a backend Update."""

        self.validate()
        payload = {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "workflow_execution_id": self.workflow_execution_id,
            "expected_state": self.expected_state,
            "target_state": self.target_state,
            "evidence_ref": self.evidence_ref,
        }
        assert_ref_only_backend_payload(payload)
        return payload


@dataclass(frozen=True)
class BackendExecutionRef:
    """Map a backend-native execution identity to local workflow identity."""

    backend_id: str
    backend_namespace: str
    backend_execution_id: str
    workflow_execution_id: str

    def validate(self) -> None:
        """Validate the opaque backend identity and Runtime execution binding."""

        _validate_id("backend_id", self.backend_id)
        _validate_token("backend_namespace", self.backend_namespace)
        _validate_token("backend_execution_id", self.backend_execution_id)
        _validate_id("workflow_execution_id", self.workflow_execution_id)


@dataclass(frozen=True)
class BackendEvent:
    """Backend event projected into the local audit identity space."""

    backend_id: str
    backend_execution_id: str
    workflow_execution_id: str
    event_type: str
    payload_ref: str | None = None


@dataclass(frozen=True)
class ExecutionSnapshot:
    """Content-free durable cursor state returned by a backend."""

    backend_id: str
    backend_execution_id: str
    workflow_execution_id: str
    workflow_id: str
    graph_sha256: str
    start_request_sha256: str
    current_state: str
    terminal: bool
    applied_events: tuple[ExternalEvent, ...]
    runtime_status_id: str = "running"
    acknowledged_cancellation_id: str | None = None
    acknowledged_cancellation_ref: str | None = None

    def validate(self) -> None:
        """Validate snapshot identity and its recorded external events."""

        _validate_id("backend_id", self.backend_id)
        _validate_token("backend_execution_id", self.backend_execution_id)
        _validate_id("workflow_execution_id", self.workflow_execution_id)
        _validate_id("workflow_id", self.workflow_id)
        _validate_id("runtime_status_id", self.runtime_status_id)
        if not _SHA256.fullmatch(self.graph_sha256):
            raise ValueError("invalid snapshot graph sha256")
        if not _SHA256.fullmatch(self.start_request_sha256):
            raise ValueError("invalid snapshot start request sha256")
        _validate_id("current_state", self.current_state)
        validate_bool("terminal", self.terminal)
        if self.runtime_status_id not in {"running", "completed", "cancelled"}:
            raise ValueError("invalid durable Runtime status")
        expected_terminal = self.runtime_status_id != "running"
        if self.terminal is not expected_terminal:
            raise ValueError("terminal flag must match durable Runtime status")
        if (self.acknowledged_cancellation_id is None) != (
            self.acknowledged_cancellation_ref is None
        ):
            raise ValueError("cancellation acknowledgement fields must be paired")
        if self.acknowledged_cancellation_id is not None:
            _validate_id(
                "acknowledged_cancellation_id",
                self.acknowledged_cancellation_id,
            )
            _validate_opaque_ref(
                "acknowledged_cancellation_ref",
                self.acknowledged_cancellation_ref,
            )
            if self.runtime_status_id != "cancelled":
                raise ValueError(
                    "cancellation acknowledgement requires cancelled status"
                )
        seen_event_ids: set[str] = set()
        for event in self.applied_events:
            event.validate()
            if event.workflow_execution_id != self.workflow_execution_id:
                raise ValueError("snapshot event crossed workflow execution")
            if event.event_id in seen_event_ids:
                raise ValueError("snapshot event ids must be unique")
            seen_event_ids.add(event.event_id)


class BackendCandidateSet:
    """Validated set of durable backend candidates and admission states."""

    def __init__(self, descriptors: Sequence[object]) -> None:
        candidates: dict[str, BackendDescriptor] = {}
        for candidate in descriptors:
            descriptor = normalize_backend_descriptor(candidate)
            if descriptor.backend_id in candidates:
                raise ValueError(f"duplicate backend: {descriptor.backend_id}")
            candidates[descriptor.backend_id] = descriptor
        self._descriptors = MappingProxyType(candidates)

    def get(self, backend_id: str) -> BackendDescriptor:
        """Return one admitted descriptor or a stable unknown-id error."""

        try:
            return self._descriptors[backend_id]
        except KeyError as exc:
            raise KeyError(f"unknown durable backend: {backend_id}") from exc

    def all(self) -> Mapping[str, BackendDescriptor]:
        """Return a deterministic read-only snapshot of backend descriptors."""

        return MappingProxyType(dict(sorted(self._descriptors.items())))

    def selected_candidate(self) -> BackendDescriptor:
        """Return the single selected production candidate, or fail closed."""

        selected = tuple(
            descriptor
            for descriptor in self._descriptors.values()
            if descriptor.evaluation_role
            is BackendEvaluationRole.SELECTED_CANDIDATE
        )
        if len(selected) != 1:
            raise RuntimeError(
                "backend candidate set must contain exactly one selected candidate"
            )
        return selected[0]
