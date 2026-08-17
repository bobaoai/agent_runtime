"""Provider-neutral contracts for one Agent execution Attempt."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
from typing import Any, Protocol, runtime_checkable

from ..foundation.foundation_contract_validation import (
    validate_bool,
    validate_enum_string,
    validate_exact_record_instance,
    validate_exact_record_tuple,
    validate_usd_amount,
    validate_id,
    validate_int,
    validate_opaque_ref,
    validate_sha256,
    validate_string_tuple,
    validate_token,
    validate_utc_timestamp,
)
from .ledger_lineage_definition import ModuleToolCallObservation
from .registry_release_definition import (
    EXECUTION_MODES,
    NETWORK_POLICIES,
    OUTPUT_CONSTRAINT_MODES,
    SEMANTIC_INPUT_DELIVERY_MODES,
)


_TOKEN = re.compile(r"^[A-Za-z0-9_.:/-]{1,255}$")


def _validate_token(label: str, value: Any) -> None:
    validate_token(label, value, pattern=_TOKEN)


def _validate_local_handle(label: str, value: Any) -> None:
    """Validate one opaque Cell lookup key without accepting traversal syntax."""

    _validate_token(label, value)
    if value.startswith("/") or any(
        segment in {"", ".", ".."} for segment in value.split("/")
    ):
        raise ValueError(f"{label} must be an opaque Cell-local lookup key")


def isolated_execution_scope_id(
    isolated_scope_ref: str,
    isolated_scope_sha256: str,
) -> str:
    """Derive the canonical AR09 identity of one isolated Module scope."""

    validate_opaque_ref("isolated_scope_ref", isolated_scope_ref)
    validate_sha256("isolated_scope_sha256", isolated_scope_sha256)
    digest = hashlib.sha256(
        "\x1f".join(
            (isolated_scope_ref, isolated_scope_sha256)
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"isolated_scope_{digest}"


def _validate_ref_hash_group(
    group_label: str,
    pairs: tuple[tuple[str, Any], ...],
    *,
    ref_labels: frozenset[str],
) -> bool:
    """Validate one all-or-nothing evidence group; return True when present."""

    values = tuple(value for _, value in pairs)
    if all(value is None for value in values):
        return False
    if any(value is None for value in values):
        raise ValueError(f"{group_label} evidence fields must be all set or all null")
    for label, value in pairs:
        if label in ref_labels:
            validate_opaque_ref(label, value)
        else:
            validate_sha256(label, value)
    return True


@dataclass(frozen=True)
class AgentExecutionAdapterDescriptor:
    """Versioned discovery and admission metadata for one provider adapter."""

    adapter_contract_version: str
    adapter_id: str
    adapter_revision: str
    provider_id: str
    transport_family: str
    transport_kind: str
    runtime_package_id: str
    runtime_package_version: str
    supported_context_modes: tuple[str, ...]
    supported_output_constraint_modes: tuple[str, ...]
    supported_read_isolation_modes: tuple[str, ...]
    supported_execution_modes: tuple[str, ...]
    supported_input_delivery_modes: tuple[str, ...]
    supported_network_policies: tuple[str, ...]
    supports_dynamic_operation_authorization: bool
    admission_state: str

    def validate(self) -> None:
        """Validate opaque adapter identity and bounded advertised capability."""

        validate_exact_record_instance(
            "adapter descriptor",
            self,
            expected_type=AgentExecutionAdapterDescriptor,
        )

        for label, value in (
            ("adapter_id", self.adapter_id),
            ("provider_id", self.provider_id),
            ("transport_kind", self.transport_kind),
            ("runtime_package_id", self.runtime_package_id),
            ("admission_state", self.admission_state),
        ):
            validate_id(label, value)
        for label, value in (
            ("adapter_contract_version", self.adapter_contract_version),
            ("adapter_revision", self.adapter_revision),
            ("runtime_package_version", self.runtime_package_version),
        ):
            _validate_token(label, value)
        validate_enum_string(
            "transport_family",
            self.transport_family,
            allowed={"sdk", "api", "cli", "in_process"},
        )
        for label, values in (
            ("supported_context_modes", self.supported_context_modes),
            (
                "supported_output_constraint_modes",
                self.supported_output_constraint_modes,
            ),
            (
                "supported_read_isolation_modes",
                self.supported_read_isolation_modes,
            ),
            ("supported_execution_modes", self.supported_execution_modes),
            (
                "supported_input_delivery_modes",
                self.supported_input_delivery_modes,
            ),
            ("supported_network_policies", self.supported_network_policies),
        ):
            validate_string_tuple(
                label,
                values,
                item_validator=validate_id,
                require_non_empty=True,
            )
        if not set(self.supported_output_constraint_modes).issubset(
            OUTPUT_CONSTRAINT_MODES
        ):
            raise ValueError("invalid supported_output_constraint_modes")
        for label, values, allowed in (
            (
                "supported_execution_modes",
                self.supported_execution_modes,
                EXECUTION_MODES,
            ),
            (
                "supported_input_delivery_modes",
                self.supported_input_delivery_modes,
                SEMANTIC_INPUT_DELIVERY_MODES,
            ),
            (
                "supported_network_policies",
                self.supported_network_policies,
                NETWORK_POLICIES,
            ),
        ):
            if not set(values).issubset(allowed):
                raise ValueError(f"invalid {label}")
        validate_bool(
            "supports_dynamic_operation_authorization",
            self.supports_dynamic_operation_authorization,
        )


@dataclass(frozen=True)
class AuthorizedExecutionInput:
    """Hash-bound input resolved through one authorized Cell lookup table."""

    execution_input_id: str
    input_ref: str
    input_sha256: str
    schema_ref: str
    schema_sha256: str
    media_type: str
    logical_name: str
    local_handle: str

    def validate(self) -> None:
        """Validate metadata while leaving content behind the local handle."""

        validate_exact_record_instance(
            "authorized execution input",
            self,
            expected_type=AuthorizedExecutionInput,
        )

        validate_id("execution_input_id", self.execution_input_id)
        validate_opaque_ref("input_ref", self.input_ref)
        validate_sha256("input_sha256", self.input_sha256)
        validate_opaque_ref("schema_ref", self.schema_ref)
        validate_sha256("schema_sha256", self.schema_sha256)
        for label, value in (
            ("media_type", self.media_type),
            ("logical_name", self.logical_name),
        ):
            _validate_token(label, value)
        _validate_local_handle("local_handle", self.local_handle)


@dataclass(frozen=True)
class AdapterContextRequest:
    """Provider-neutral native-context request and reconstruction identity."""

    mode: str
    compatibility_sha256: str
    context_type: str
    resume_mode: str
    read_isolation: str
    reconstruction_input_refs: tuple[str, ...]
    context_ref: str | None = None
    parent_variant_id: str | None = None

    def validate(self) -> None:
        """Validate context policy without treating native state as authority."""

        validate_exact_record_instance(
            "adapter context request",
            self,
            expected_type=AdapterContextRequest,
        )

        validate_enum_string(
            "adapter context mode",
            self.mode,
            allowed={"stateless", "create", "resume", "reconstruct"},
        )
        validate_sha256("compatibility_sha256", self.compatibility_sha256)
        for label, value in (
            ("context_type", self.context_type),
            ("resume_mode", self.resume_mode),
            ("read_isolation", self.read_isolation),
        ):
            validate_id(label, value)
        validate_string_tuple(
            "reconstruction_input_refs",
            self.reconstruction_input_refs,
            item_validator=validate_opaque_ref,
            require_non_empty=False,
        )
        if self.context_ref is not None:
            validate_opaque_ref("context_ref", self.context_ref)
        if self.parent_variant_id is not None:
            validate_id("parent_variant_id", self.parent_variant_id)
        if self.mode == "resume" and self.context_ref is None:
            raise ValueError("resume context mode requires context_ref")


@dataclass(frozen=True)
class AdapterContextResult:
    """Bounded context disposition returned by a provider adapter."""

    disposition_id: str
    context_ref: str | None
    compatibility_sha256: str

    def validate(self) -> None:
        """Validate context disposition and opaque native reference."""

        validate_exact_record_instance(
            "adapter context result",
            self,
            expected_type=AdapterContextResult,
        )

        validate_id("disposition_id", self.disposition_id)
        validate_sha256("compatibility_sha256", self.compatibility_sha256)
        if self.context_ref is not None:
            validate_opaque_ref("context_ref", self.context_ref)


@dataclass(frozen=True)
class ProviderOperationIntent:
    """Bounded provider callback intent awaiting Runtime authorization."""

    workflow_execution_id: str
    module_run_id: str
    variant_id: str
    attempt_id: str
    capability_id: str
    resource_id: str
    action_id: str
    entitlement_snapshot_hash: str
    idempotency_key: str
    expires_after_seconds: int

    def validate(self) -> None:
        """Validate bounded operation identity before host authorization."""

        validate_exact_record_instance(
            "operation authorization request",
            self,
            expected_type=ProviderOperationIntent,
        )

        for label, value in (
            ("workflow_execution_id", self.workflow_execution_id),
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("capability_id", self.capability_id),
            ("resource_id", self.resource_id),
            ("action_id", self.action_id),
            ("idempotency_key", self.idempotency_key),
        ):
            validate_id(label, value)
        validate_sha256(
            "entitlement_snapshot_hash", self.entitlement_snapshot_hash
        )
        validate_int(
            "expires_after_seconds",
            self.expires_after_seconds,
            minimum=1,
            maximum=86_400,
        )


@dataclass(frozen=True)
class AuthorizedOperationReceipt:
    """Bounded proof that one dynamic provider operation was authorized."""

    receipt_id: str
    decision_ref: str
    decision_sha256: str
    grant_disposition_ref: str | None
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate the bounded authorization receipt references."""

        validate_exact_record_instance(
            "authorized operation receipt",
            self,
            expected_type=AuthorizedOperationReceipt,
        )

        validate_id("receipt_id", self.receipt_id)
        validate_opaque_ref("decision_ref", self.decision_ref)
        validate_sha256("decision_sha256", self.decision_sha256)
        if self.grant_disposition_ref is not None:
            validate_opaque_ref(
                "grant_disposition_ref", self.grant_disposition_ref
            )
        validate_utc_timestamp("recorded_at_utc", self.recorded_at_utc)


@dataclass(frozen=True)
class AgentExecutionFailure:
    """Bounded provider failure with Cell-local detail lineage."""

    failure_class: str
    retry_disposition_id: str
    failure_scope_id: str
    retry_after_seconds: int | None = None
    detail_ref: str | None = None
    detail_sha256: str | None = None

    def validate(self) -> None:
        """Validate failure taxonomy without shared raw exception prose."""

        validate_exact_record_instance(
            "Agent execution failure",
            self,
            expected_type=AgentExecutionFailure,
        )

        allowed = {
            "authentication",
            "authorization",
            "quota",
            "rate_limit",
            "timeout",
            "dependency_unavailable",
            "transport",
            "provider",
            "schema",
            "policy_violation",
            "context_unavailable",
            "cancelled",
            "unknown",
        }
        validate_enum_string(
            "Agent execution failure class",
            self.failure_class,
            allowed=allowed,
        )
        validate_id("retry_disposition_id", self.retry_disposition_id)
        validate_id("failure_scope_id", self.failure_scope_id)
        if self.retry_after_seconds is not None:
            validate_int("retry_after_seconds", self.retry_after_seconds, minimum=0)
        if (self.detail_ref is None) != (self.detail_sha256 is None):
            raise ValueError("failure detail ref and hash must be paired")
        if self.detail_ref is not None:
            validate_opaque_ref("detail_ref", self.detail_ref)
            validate_sha256("detail_sha256", self.detail_sha256 or "")


@dataclass(frozen=True)
class OutputSubmission:
    """One opaque output slot staged through the Attempt-local handle table."""

    output_slot_id: str
    local_handle: str

    def validate(self) -> None:
        """Validate output identity without reading provider content."""

        validate_exact_record_instance(
            "output submission",
            self,
            expected_type=OutputSubmission,
        )

        validate_id("output_slot_id", self.output_slot_id)
        _validate_local_handle("local_handle", self.local_handle)


@dataclass(frozen=True)
class AgentExecutionResult:
    """Infrastructure result returned for Runtime-controlled finalization."""

    terminal_status: str
    provider_id: str
    model_id: str
    runtime_version: str
    outputs: tuple[OutputSubmission, ...]
    model_operation_ref_ids: tuple[str, ...]
    tool_operation_ref_ids: tuple[str, ...]
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_creation_tokens: int | None
    estimated_cost_usd: str | None
    provider_charge_usd: str | None
    context: AdapterContextResult
    failure: AgentExecutionFailure | None
    cell_local_trace_ref: str
    cell_local_trace_sha256: str
    tool_observations: tuple[ModuleToolCallObservation, ...] = ()

    def validate(self) -> None:
        """Validate normalized output, usage, failure, and local trace lineage."""

        validate_exact_record_instance(
            "Agent execution result",
            self,
            expected_type=AgentExecutionResult,
        )

        validate_enum_string(
            "Agent execution terminal status",
            self.terminal_status,
            allowed={"completed", "failed", "cancelled"},
        )
        validate_id("provider_id", self.provider_id)
        _validate_token("model_id", self.model_id)
        _validate_token("runtime_version", self.runtime_version)
        validate_exact_record_tuple(
            "outputs",
            self.outputs,
            expected_type=OutputSubmission,
            item_validator=OutputSubmission.validate,
            unique_key=lambda output: output.output_slot_id,
            unique_key_label="output_slot_id",
            require_non_empty=False,
        )
        for label, refs in (
            ("model_operation_ref_ids", self.model_operation_ref_ids),
            ("tool_operation_ref_ids", self.tool_operation_ref_ids),
        ):
            validate_string_tuple(
                label,
                refs,
                item_validator=validate_id,
                require_non_empty=False,
            )
        validate_exact_record_tuple(
            "tool_observations",
            self.tool_observations,
            expected_type=ModuleToolCallObservation,
            item_validator=ModuleToolCallObservation.validate,
            unique_key=lambda observation: observation.tool_call_id,
            unique_key_label="tool_call_id",
            require_non_empty=False,
        )
        if tuple(
            observation.tool_call_id
            for observation in self.tool_observations
        ) != self.tool_operation_ref_ids:
            raise ValueError(
                "tool_operation_ref_ids must exactly match tool observations"
            )
        for label, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
            ("cache_read_tokens", self.cache_read_tokens),
            ("cache_creation_tokens", self.cache_creation_tokens),
        ):
            if value is not None:
                validate_int(label, value, minimum=0)
        for label, value in (
            ("estimated_cost_usd", self.estimated_cost_usd),
            ("provider_charge_usd", self.provider_charge_usd),
        ):
            if value is not None:
                validate_usd_amount(label, value)
        validate_exact_record_instance(
            "result context",
            self.context,
            expected_type=AdapterContextResult,
        )
        AdapterContextResult.validate(self.context)
        if (self.terminal_status == "completed") == (self.failure is not None):
            raise ValueError("completed result forbids failure; other statuses require it")
        if self.failure is not None:
            validate_exact_record_instance(
                "result failure",
                self.failure,
                expected_type=AgentExecutionFailure,
            )
            AgentExecutionFailure.validate(self.failure)
        validate_opaque_ref("cell_local_trace_ref", self.cell_local_trace_ref)
        validate_sha256("cell_local_trace_sha256", self.cell_local_trace_sha256)

    def as_dict(self) -> dict[str, Any]:
        """Return the normalized content-free result representation."""

        AgentExecutionResult.validate(self)
        return asdict(self)


@dataclass(frozen=True)
class AuthorizedAgentExecutionRequest:
    """Canonical AR09 request bound to Product decision and Runtime claim evidence.

    Authorization evidence fields resolve to the Stack-A authority records of
    ``agent_runtime_09``: the execution authorization context binding, the
    protected-operation intent, the Product operation decision, and the Gateway
    authorization observation binding decision to intent. Each evidence group
    is present completely or not at all; empty evidence is admissible only for
    the operation-free ``in_process`` Test/Evaluation conjunction defined in
    ``agent_runtime_08``.
    """

    workflow_execution_id: str | None
    isolated_scope_ref: str | None
    isolated_scope_sha256: str | None
    module_run_id: str
    variant_id: str
    attempt_id: str
    module_id: str
    module_release_ref: str
    module_release_sha256: str
    execution_profile_id: str
    execution_profile_ref: str
    execution_profile_sha256: str
    attempt_begin_receipt_ref: str
    attempt_begin_receipt_sha256: str
    prompt_envelope_ref: str | None
    prompt_envelope_sha256: str | None
    output_schema_ref: str
    output_schema_sha256: str
    execution_authorization_binding_ref: str | None
    execution_authorization_binding_sha256: str | None
    protected_operation_intent_ref: str | None
    protected_operation_intent_sha256: str | None
    product_operation_decision_ref: str | None
    product_operation_decision_sha256: str | None
    gateway_authorization_observation_ref: str | None
    gateway_authorization_observation_sha256: str | None
    operation_grant_ref: str | None
    operation_grant_sha256: str | None
    grant_disposition_ref: str | None
    input_closure_sha256: str
    data_use_purpose_id: str
    authorized_inputs: tuple[AuthorizedExecutionInput, ...]
    request_sha256: str
    idempotency_key: str

    def _identity_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("request_sha256")
        payload["authorized_inputs"] = [asdict(item) for item in self.authorized_inputs]
        return payload

    @classmethod
    def build(cls, **fields: Any) -> "AuthorizedAgentExecutionRequest":
        """Build a content-addressed canonical authorized request."""

        payload = dict(fields)
        payload["authorized_inputs"] = tuple(payload["authorized_inputs"])
        provisional = cls(**payload, request_sha256="0" * 64)
        request_sha256 = hashlib.sha256(
            json.dumps(
                provisional._identity_payload(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        record = cls(**payload, request_sha256=request_sha256)
        record.validate()
        return record

    def validate(self) -> None:
        """Validate scope, authority evidence chain, and input bindings."""

        validate_exact_record_instance(
            "authorized Agent execution request",
            self,
            expected_type=AuthorizedAgentExecutionRequest,
        )

        has_isolated_scope = _validate_ref_hash_group(
            "isolated scope",
            (
                ("isolated_scope_ref", self.isolated_scope_ref),
                ("isolated_scope_sha256", self.isolated_scope_sha256),
            ),
            ref_labels=frozenset({"isolated_scope_ref"}),
        )
        if (self.workflow_execution_id is None) == (not has_isolated_scope):
            raise ValueError(
                "request requires exactly one execution scope: a Workflow "
                "Execution ID or an isolated Module scope"
            )
        if self.workflow_execution_id is not None:
            validate_id("workflow_execution_id", self.workflow_execution_id)

        for label, value in (
            ("module_run_id", self.module_run_id),
            ("variant_id", self.variant_id),
            ("attempt_id", self.attempt_id),
            ("module_id", self.module_id),
            ("execution_profile_id", self.execution_profile_id),
            ("data_use_purpose_id", self.data_use_purpose_id),
            ("idempotency_key", self.idempotency_key),
        ):
            validate_id(label, value)
        for label, value in (
            ("module_release_ref", self.module_release_ref),
            ("execution_profile_ref", self.execution_profile_ref),
            ("attempt_begin_receipt_ref", self.attempt_begin_receipt_ref),
            ("output_schema_ref", self.output_schema_ref),
        ):
            validate_opaque_ref(label, value)
        for label, value in (
            ("module_release_sha256", self.module_release_sha256),
            ("execution_profile_sha256", self.execution_profile_sha256),
            ("attempt_begin_receipt_sha256", self.attempt_begin_receipt_sha256),
            ("output_schema_sha256", self.output_schema_sha256),
            ("input_closure_sha256", self.input_closure_sha256),
            ("request_sha256", self.request_sha256),
        ):
            validate_sha256(label, value)
        _validate_ref_hash_group(
            "prompt envelope",
            (
                ("prompt_envelope_ref", self.prompt_envelope_ref),
                ("prompt_envelope_sha256", self.prompt_envelope_sha256),
            ),
            ref_labels=frozenset({"prompt_envelope_ref"}),
        )

        has_binding = _validate_ref_hash_group(
            "execution authorization binding",
            (
                (
                    "execution_authorization_binding_ref",
                    self.execution_authorization_binding_ref,
                ),
                (
                    "execution_authorization_binding_sha256",
                    self.execution_authorization_binding_sha256,
                ),
            ),
            ref_labels=frozenset({"execution_authorization_binding_ref"}),
        )
        has_operation = _validate_ref_hash_group(
            "operation decision",
            (
                (
                    "protected_operation_intent_ref",
                    self.protected_operation_intent_ref,
                ),
                (
                    "protected_operation_intent_sha256",
                    self.protected_operation_intent_sha256,
                ),
                (
                    "product_operation_decision_ref",
                    self.product_operation_decision_ref,
                ),
                (
                    "product_operation_decision_sha256",
                    self.product_operation_decision_sha256,
                ),
                (
                    "gateway_authorization_observation_ref",
                    self.gateway_authorization_observation_ref,
                ),
                (
                    "gateway_authorization_observation_sha256",
                    self.gateway_authorization_observation_sha256,
                ),
            ),
            ref_labels=frozenset(
                {
                    "protected_operation_intent_ref",
                    "product_operation_decision_ref",
                    "gateway_authorization_observation_ref",
                }
            ),
        )
        has_grant = _validate_ref_hash_group(
            "operation grant",
            (
                ("operation_grant_ref", self.operation_grant_ref),
                ("operation_grant_sha256", self.operation_grant_sha256),
                ("grant_disposition_ref", self.grant_disposition_ref),
            ),
            ref_labels=frozenset(
                {"operation_grant_ref", "grant_disposition_ref"}
            ),
        )
        if has_grant and not has_operation:
            raise ValueError(
                "an operation grant requires the operation decision evidence"
            )
        if has_operation and not has_binding:
            raise ValueError(
                "operation decision evidence requires the execution "
                "authorization binding"
            )

        validate_exact_record_tuple(
            "authorized_inputs",
            self.authorized_inputs,
            expected_type=AuthorizedExecutionInput,
            item_validator=AuthorizedExecutionInput.validate,
            unique_key=lambda item: item.execution_input_id,
            unique_key_label="execution_input_id",
            require_non_empty=False,
        )
        handles = tuple(item.local_handle for item in self.authorized_inputs)
        if len(handles) != len(set(handles)):
            raise ValueError(
                "authorized_inputs must declare unique local_handle keys"
            )
        expected_request_sha256 = hashlib.sha256(
            json.dumps(
                self._identity_payload(),
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        if self.request_sha256 != expected_request_sha256:
            raise ValueError("authorized Agent execution request hash mismatch")

    @property
    def has_operation_evidence(self) -> bool:
        """Report whether the complete operation-decision evidence is bound."""

        return self.protected_operation_intent_ref is not None

    @property
    def execution_scope_id(self) -> str:
        """Return the exact Workflow or isolated-scope AR09 identity."""

        if self.workflow_execution_id is not None:
            return self.workflow_execution_id
        if (
            self.isolated_scope_ref is None
            or self.isolated_scope_sha256 is None
        ):
            raise ValueError("request execution scope is incomplete")
        return isolated_execution_scope_id(
            self.isolated_scope_ref,
            self.isolated_scope_sha256,
        )


@runtime_checkable
class AuthorizedAgentExecutionHost(Protocol):
    """Canonical host after Product decision resolution; it cannot mint authority."""

    def read_authorized_input(self, local_handle: str) -> bytes:
        """Read through a request-bound lookup key, never a filesystem path."""

        ...

    def stage_output_bytes(
        self,
        submission: OutputSubmission,
        content: bytes,
    ) -> None:
        """Stage bytes behind one output handle for Runtime finalization."""

        ...

    def authorize_operation(
        self,
        request: ProviderOperationIntent,
    ) -> AuthorizedOperationReceipt:
        """Authorize one dynamic provider operation before it is approved."""

        ...


@runtime_checkable
class AuthorizedAgentExecutionAdapter(Protocol):
    """Canonical provider adapter consuming exact Product/Runtime bindings."""

    @property
    def descriptor(self) -> AgentExecutionAdapterDescriptor:
        """Return immutable canonical adapter admission metadata."""

        ...

    def execute(
        self,
        request: AuthorizedAgentExecutionRequest,
        host: AuthorizedAgentExecutionHost,
    ) -> AgentExecutionResult:
        """Execute one fully authorized Attempt without minting authority."""

        ...


__all__ = [
    "AdapterContextRequest",
    "AdapterContextResult",
    "AuthorizedAgentExecutionAdapter",
    "AuthorizedAgentExecutionHost",
    "AuthorizedAgentExecutionRequest",
    "AuthorizedOperationReceipt",
    "AgentExecutionAdapterDescriptor",
    "AgentExecutionFailure",
    "AgentExecutionResult",
    "AuthorizedExecutionInput",
    "ProviderOperationIntent",
    "isolated_execution_scope_id",
    "OutputSubmission",
]
