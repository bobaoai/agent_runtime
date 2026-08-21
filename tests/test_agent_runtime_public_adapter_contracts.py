from __future__ import annotations

from dataclasses import dataclass, fields, replace
from decimal import Decimal
import json

import pytest

from agent_runtime.contracts.durability_backend_definition import (
    ExecutionSnapshot,
    ExternalEvent,
    RuntimeCancellationRequest,
    RuntimeWorkflowStartRequest,
)
from agent_runtime.contracts.ledger_lineage_definition import (
    ModuleAttemptRecord,
    ModuleUsageObservation,
)
from agent_runtime.contracts.invocation_adapter_definition import (
    AdapterContextRequest,
    AdapterContextResult,
    AgentExecutionAdapterDescriptor,
    AgentExecutionFailure,
    AgentExecutionResult,
    AuthorizedAgentExecutionAdapter,
    AuthorizedAgentExecutionRequest,
    AuthorizedExecutionInput,
    ProviderOperationIntent,
    OutputSubmission,
)


def _durable_start_request() -> RuntimeWorkflowStartRequest:
    return RuntimeWorkflowStartRequest(
        workflow_execution_id="execution_synthetic_001",
        tenant_id="tenant_synthetic_001",
        cell_id="cell_synthetic_001",
        workflow_release_ref="runtime-workflow:synthetic@v1",
        workflow_release_sha256="a" * 64,
        execution_release_ref="release-ref:execution-v1",
        execution_release_sha256="b" * 64,
        execution_profile_selection_ref="profile-selection:synthetic@v1",
        execution_profile_selection_sha256="c" * 64,
        runtime_execution_binding_ref="runtime-binding:synthetic@v1",
        runtime_execution_binding_sha256="d" * 64,
        execution_authorization_binding_ref="authorization:synthetic@v1",
        execution_authorization_binding_sha256="e" * 64,
        execution_start_admission_ref="start-admission:synthetic@v1",
        execution_start_admission_sha256="f" * 64,
        execution_input_package_refs=("artifact-ref:input-001",),
        execution_input_package_sha256="1" * 64,
        idempotency_key="start_synthetic_001",
        recorded_at_utc="2026-08-08T12:00:00Z",
    )


def _provider_result() -> AgentExecutionResult:
    return AgentExecutionResult(
        terminal_status="completed",
        provider_id="provider_synthetic",
        model_id="model-v1",
        runtime_version="runtime-v1",
        outputs=(
            OutputSubmission(
                output_slot_id="output_primary",
                local_handle="workspace/output-primary.json",
            ),
        ),
        model_operation_ref_ids=("operation_model_001",),
        tool_operation_ref_ids=(),
        input_tokens=12,
        output_tokens=4,
        cache_read_tokens=0,
        cache_creation_tokens=None,
        estimated_cost_usd="0.250",
        provider_charge_usd=None,
        context=AdapterContextResult(
            disposition_id="context_closed",
            context_ref=None,
            compatibility_sha256="f" * 64,
        ),
        failure=None,
        cell_local_trace_ref="cell-trace:result-001",
        cell_local_trace_sha256="1" * 64,
    )


def _provider_descriptor() -> AgentExecutionAdapterDescriptor:
    return AgentExecutionAdapterDescriptor(
        adapter_contract_version="v1",
        adapter_id="adapter_synthetic",
        adapter_revision="revision-v1",
        provider_id="provider_synthetic",
        transport_family="sdk",
        transport_kind="synthetic_sdk",
        runtime_package_id="runtime_provider_synthetic",
        runtime_package_version="0.1.0",
        supported_context_modes=("stateless", "resume"),
        supported_output_constraint_modes=(
            "prompt_only_json",
            "native_structured_output",
        ),
        supported_read_isolation_modes=("entitled_refs",),
        supported_execution_modes=("tool_free", "agent"),
        supported_input_delivery_modes=("inline",),
        supported_network_policies=("denied",),
        supports_dynamic_operation_authorization=True,
        admission_state="conformance_tested",
    )


def _authorized_execution_input() -> AuthorizedExecutionInput:
    return AuthorizedExecutionInput(
        execution_input_id="execution_input_synthetic_001",
        input_ref="artifact-ref:input-001",
        input_sha256="2" * 64,
        schema_ref="schema:input_primary@v1",
        schema_sha256="8" * 64,
        media_type="application/json",
        logical_name="input-primary",
        local_handle="workspace/input-primary.json",
    )


def _operation_authorization_request() -> ProviderOperationIntent:
    return ProviderOperationIntent(
        workflow_execution_id="execution_synthetic_001",
        module_run_id="module_synthetic_001",
        variant_id="variant_synthetic_001",
        attempt_id="attempt_synthetic_001",
        capability_id="capability_synthetic",
        resource_id="resource_synthetic",
        action_id="action_synthetic",
        entitlement_snapshot_hash="a" * 64,
        idempotency_key="idempotency_synthetic_001",
        expires_after_seconds=30,
    )


def _provider_failure() -> AgentExecutionFailure:
    return AgentExecutionFailure(
        failure_class="timeout",
        retry_disposition_id="retry_allowed",
        failure_scope_id="attempt_only",
        retry_after_seconds=15,
        detail_ref="cell-trace:failure-001",
        detail_sha256="1" * 64,
    )


def _authorized_provider_request() -> AuthorizedAgentExecutionRequest:
    return AuthorizedAgentExecutionRequest.build(
        workflow_execution_id="execution_synthetic_001",
        isolated_scope_ref=None,
        isolated_scope_sha256=None,
        module_run_id="module_synthetic_001",
        variant_id="variant_synthetic_001",
        attempt_id="attempt_synthetic_001",
        module_id="module_synthetic",
        module_release_ref="runtime-module:module_synthetic@v1",
        module_release_sha256="a" * 64,
        execution_profile_id="profile_synthetic",
        execution_profile_ref="execution-profile:profile_synthetic@v1",
        execution_profile_sha256="b" * 64,
        attempt_begin_receipt_ref="attempt-begin-receipt:attempt_synthetic_001",
        attempt_begin_receipt_sha256="c" * 64,
        prompt_envelope_ref="prompt-envelope:attempt_synthetic_001",
        prompt_envelope_sha256="d" * 64,
        output_schema_ref="schema:synthetic_output@v1",
        output_schema_sha256="e" * 64,
        execution_authorization_binding_ref="execution-authorization-binding:binding_001",
        execution_authorization_binding_sha256="1" * 64,
        protected_operation_intent_ref="protected-operation-intent:intent_001",
        protected_operation_intent_sha256="2" * 64,
        product_operation_decision_ref="product-operation-decision:decision_001",
        product_operation_decision_sha256="3" * 64,
        gateway_authorization_observation_ref=(
            "gateway-authorization-observation:observation_001"
        ),
        gateway_authorization_observation_sha256="4" * 64,
        operation_grant_ref="operation-grant:grant_001",
        operation_grant_sha256="5" * 64,
        grant_disposition_ref="operation-grant-disposition:disposition_001",
        input_closure_sha256="7" * 64,
        data_use_purpose_id="synthetic_invoke",
        authorized_inputs=(_authorized_execution_input(),),
        idempotency_key="idempotency_authorized_001",
    )


def test_canonical_adapter_request_binds_product_and_runtime_authority() -> None:
    request = _authorized_provider_request()

    request.validate()
    assert request.module_release_ref == "runtime-module:module_synthetic@v1"
    assert request.execution_profile_ref == "execution-profile:profile_synthetic@v1"
    assert request.prompt_envelope_ref == "prompt-envelope:attempt_synthetic_001"
    assert AuthorizedAgentExecutionAdapter.__name__ == (
        "AuthorizedAgentExecutionAdapter"
    )


def _request_fields_without_hash() -> dict[str, object]:
    granted = _authorized_provider_request()
    return {
        field.name: getattr(granted, field.name)
        for field in fields(type(granted))
        if field.name != "request_sha256"
    }


def test_ordinary_adapter_request_validates_without_single_use_grant() -> None:
    fields_without_hash = _request_fields_without_hash()
    fields_without_hash.update(
        operation_grant_ref=None,
        operation_grant_sha256=None,
        grant_disposition_ref=None,
    )

    ordinary = AuthorizedAgentExecutionRequest.build(**fields_without_hash)

    ordinary.validate()
    assert ordinary.operation_grant_ref is None
    assert ordinary.grant_disposition_ref is None


def test_adapter_request_rejects_partial_high_risk_grant_binding() -> None:
    fields_without_hash = _request_fields_without_hash()
    fields_without_hash["grant_disposition_ref"] = None

    with pytest.raises(
        ValueError,
        match="operation grant evidence fields must be all set or all null",
    ):
        AuthorizedAgentExecutionRequest.build(**fields_without_hash)


def test_operation_free_request_validates_with_empty_evidence_groups() -> None:
    fields_without_hash = _request_fields_without_hash()
    fields_without_hash.update(
        workflow_execution_id=None,
        isolated_scope_ref="scope-ref:isolated-001",
        isolated_scope_sha256="9" * 64,
        execution_authorization_binding_ref=None,
        execution_authorization_binding_sha256=None,
        protected_operation_intent_ref=None,
        protected_operation_intent_sha256=None,
        product_operation_decision_ref=None,
        product_operation_decision_sha256=None,
        gateway_authorization_observation_ref=None,
        gateway_authorization_observation_sha256=None,
        operation_grant_ref=None,
        operation_grant_sha256=None,
        grant_disposition_ref=None,
    )

    request = AuthorizedAgentExecutionRequest.build(**fields_without_hash)

    assert request.has_operation_evidence is False
    assert request.workflow_execution_id is None
    assert request.isolated_scope_ref == "scope-ref:isolated-001"


@pytest.mark.parametrize(
    ("overrides", "match"),
    (
        (
            {"execution_authorization_binding_sha256": None},
            "execution authorization binding evidence",
        ),
        (
            {"protected_operation_intent_sha256": None},
            "operation decision evidence",
        ),
        (
            {"gateway_authorization_observation_ref": None},
            "operation decision evidence",
        ),
        (
            {
                "execution_authorization_binding_ref": None,
                "execution_authorization_binding_sha256": None,
            },
            "requires the execution",
        ),
        (
            {
                "protected_operation_intent_ref": None,
                "protected_operation_intent_sha256": None,
                "product_operation_decision_ref": None,
                "product_operation_decision_sha256": None,
                "gateway_authorization_observation_ref": None,
                "gateway_authorization_observation_sha256": None,
            },
            "grant requires the operation decision",
        ),
    ),
)
def test_adapter_request_rejects_incoherent_evidence_chains(
    overrides: dict[str, object],
    match: str,
) -> None:
    fields_without_hash = _request_fields_without_hash()
    fields_without_hash.update(overrides)

    with pytest.raises(ValueError, match=match):
        AuthorizedAgentExecutionRequest.build(**fields_without_hash)


@pytest.mark.parametrize(
    "overrides",
    (
        {
            "isolated_scope_ref": "scope-ref:isolated-001",
            "isolated_scope_sha256": "9" * 64,
        },
        {"workflow_execution_id": None},
        {"isolated_scope_ref": "scope-ref:isolated-001"},
    ),
)
def test_adapter_request_requires_exactly_one_execution_scope(
    overrides: dict[str, object],
) -> None:
    fields_without_hash = _request_fields_without_hash()
    fields_without_hash.update(overrides)

    with pytest.raises(ValueError, match="scope"):
        AuthorizedAgentExecutionRequest.build(**fields_without_hash)


@pytest.mark.parametrize(
    ("field_name", "invalid_values"),
    (
        ("supported_execution_modes", ("shell_everything",)),
        ("supported_input_delivery_modes", ("ambient_filesystem",)),
        ("supported_network_policies", ("unrestricted",)),
    ),
)
def test_descriptor_rejects_capability_values_outside_the_contract(
    field_name: str,
    invalid_values: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match=field_name):
        replace(_provider_descriptor(), **{field_name: invalid_values}).validate()


@pytest.mark.parametrize("local_handle", ("../secret", "workspace/../secret", "/secret"))
def test_adapter_local_handles_reject_path_traversal(local_handle: str) -> None:
    with pytest.raises(ValueError, match="opaque Cell-local lookup key"):
        replace(_authorized_execution_input(), local_handle=local_handle).validate()
    with pytest.raises(ValueError, match="opaque Cell-local lookup key"):
        OutputSubmission(
            output_slot_id="output_primary",
            local_handle=local_handle,
        ).validate()


class _AuthorizedExecutionInputSubclass(AuthorizedExecutionInput):
    """Type-identity negative for exact public record tuple validation."""


class _OutputSubmissionSubclass(OutputSubmission):
    """Type-identity negative for exact public record tuple validation."""


class _IdentityHashedString(str):
    """String lookalike whose Python equality diverges from JSON equality."""

    def __hash__(self) -> int:
        return id(self)

    def __eq__(self, other: object) -> bool:
        return self is other


class _UnhashableString(str):
    """String lookalike that previously leaked TypeError from the key set."""

    __hash__ = None  # type: ignore[assignment]


class _StatefulTuple(tuple[str, ...]):
    """Tuple lookalike that changes content after its first iteration."""

    iteration_count: int

    def __new__(cls, *values: str) -> "_StatefulTuple":
        instance = super().__new__(cls, values)
        instance.iteration_count = 0
        return instance

    def __iter__(self):  # type: ignore[override]
        self.iteration_count += 1
        if self.iteration_count == 1:
            return super().__iter__()
        return iter(("secret_payload",))


@dataclass(frozen=True)
class _ForeignNestedRecord:
    """Foreign dataclass whose no-op validator previously bypassed the boundary."""

    secret_payload: str = "raw_provider_secret"

    def validate(self) -> None:
        pass


@dataclass(frozen=True)
class _AgentExecutionResultWithSecret(AgentExecutionResult):
    """Result subclass that would be recursively emitted by dataclasses.asdict."""

    secret_payload: str = "raw_provider_secret"


def _non_tuple_container(kind: str, record: object) -> object:
    if kind == "list":
        return [record]
    if kind == "set":
        return {record}
    return (item for item in (record,))


def test_durable_start_and_snapshot_are_exact_ref_only_contracts() -> None:
    request = _durable_start_request()
    payload = request.as_dict()
    assert set(payload) == {
        field.name for field in fields(RuntimeWorkflowStartRequest)
    }
    assert "content" not in " ".join(payload)
    assert "prompt" not in " ".join(payload)

    snapshot = ExecutionSnapshot(
        backend_id="backend_synthetic",
        backend_execution_id="backend-execution-001",
        workflow_execution_id=request.workflow_execution_id,
        workflow_id="workflow_synthetic",
        graph_sha256="a" * 64,
        start_request_sha256="b" * 64,
        current_state="state_waiting",
        terminal=False,
        applied_events=(),
    )
    snapshot.validate()
    assert snapshot.current_state == "state_waiting"


def test_cursor_event_export_is_explicit_and_bare_name_is_absent() -> None:
    import agent_runtime
    import agent_runtime.contracts as contracts

    assert contracts.DurableCursorExternalEvent is ExternalEvent
    assert agent_runtime.DurableCursorExternalEvent is ExternalEvent
    assert agent_runtime.AuthorizedExternalEvent is not ExternalEvent
    assert not hasattr(contracts, "ExternalEvent")
    assert not hasattr(agent_runtime, "ExternalEvent")


def test_module_attempt_rejects_reversed_period() -> None:
    attempt = ModuleAttemptRecord(
        module_run_id="module_synthetic_001",
        variant_id="variant_synthetic_001",
        attempt_id="attempt_synthetic_001",
        status="failed",
        output_refs=(),
        usage=ModuleUsageObservation(None, None, None, None),
        failure_class="provider_failure",
        period_start_at_utc="2026-08-08T12:01:00Z",
        period_end_at_utc="2026-08-08T12:00:00Z",
        recorded_at_utc="2026-08-08T12:01:00Z",
    )

    with pytest.raises(ValueError, match="precedes"):
        attempt.validate()


def test_durable_snapshot_terminal_flag_matches_runtime_status() -> None:
    snapshot = ExecutionSnapshot(
        backend_id="backend_synthetic",
        backend_execution_id="backend-execution-001",
        workflow_execution_id="execution_synthetic_001",
        workflow_id="workflow_synthetic",
        graph_sha256="a" * 64,
        start_request_sha256="b" * 64,
        current_state="state_waiting",
        terminal=True,
        applied_events=(),
        runtime_status_id="running",
    )

    with pytest.raises(ValueError, match="terminal flag"):
        snapshot.validate()


def test_cancellation_requires_authorized_artifact_and_hash() -> None:
    request = RuntimeCancellationRequest(
        cancellation_request_id="cancellation_synthetic_001",
        workflow_execution_id="execution_synthetic_001",
        expected_snapshot_ref="runtime-snapshot:synthetic@v1",
        expected_snapshot_sha256="d" * 64,
        reason_code="operator_requested",
        reason_artifact_ref="artifact-ref:cancellation-001",
        reason_artifact_sha256="e" * 64,
        authorization_decision_ref="authorization-ref:allow-001",
        authorization_decision_sha256="f" * 64,
        idempotency_key="cancel_synthetic_001",
        recorded_at_utc="2026-08-08T12:01:00Z",
    )
    request.validate()
    with pytest.raises(ValueError, match="reason_artifact_ref"):
        replace(request, reason_artifact_ref="raw operator explanation").validate()


def test_provider_descriptor_and_context_reject_implicit_authority() -> None:
    descriptor = _provider_descriptor()
    descriptor.validate()
    with pytest.raises(ValueError, match="transport_family"):
        replace(descriptor, transport_family="shell_string").validate()

    context = AdapterContextRequest(
        mode="resume",
        compatibility_sha256="f" * 64,
        context_type="provider_native",
        resume_mode="exact_match",
        read_isolation="entitled_refs",
        reconstruction_input_refs=("artifact-ref:continuity-001",),
        context_ref="provider-context:opaque-001",
    )
    context.validate()
    with pytest.raises(ValueError, match="requires context_ref"):
        replace(context, context_ref=None).validate()


def test_provider_failure_keeps_raw_detail_behind_cell_local_ref() -> None:
    failure = AgentExecutionFailure(
        failure_class="timeout",
        retry_disposition_id="retry_allowed",
        failure_scope_id="attempt_only",
        retry_after_seconds=15,
        detail_ref="cell-trace:failure-001",
        detail_sha256="1" * 64,
    )
    failure.validate()
    assert "message" not in {field.name for field in fields(AgentExecutionFailure)}
    assert "exception" not in {field.name for field in fields(AgentExecutionFailure)}
    with pytest.raises(ValueError, match="paired"):
        replace(failure, detail_sha256=None).validate()


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("workflow_execution_id", 123),
        ("execution_release_ref", 123),
        ("execution_input_package_refs", (123,)),
        ("recorded_at_utc", 1.5),
    ),
)
def test_durable_start_rejects_python_coercions(
    field_name: str,
    invalid_value: object,
) -> None:
    request = replace(_durable_start_request(), **{field_name: invalid_value})

    with pytest.raises(ValueError):
        request.validate()


@pytest.mark.parametrize("invalid_terminal", (1, 0.0, "false"))
def test_durable_snapshot_rejects_non_boolean_terminal(
    invalid_terminal: object,
) -> None:
    snapshot = ExecutionSnapshot(
        backend_id="backend_synthetic",
        backend_execution_id="backend-execution-001",
        workflow_execution_id="execution_synthetic_001",
        workflow_id="workflow_synthetic",
        graph_sha256="a" * 64,
        start_request_sha256="b" * 64,
        current_state="state_waiting",
        terminal=False,
        applied_events=(),
    )

    with pytest.raises(ValueError, match="boolean"):
        replace(snapshot, terminal=invalid_terminal).validate()


def test_provider_operation_expiry_rejects_bool_and_float_counts() -> None:
    request = ProviderOperationIntent(
        workflow_execution_id="execution_synthetic_001",
        module_run_id="module_synthetic_001",
        variant_id="variant_synthetic_001",
        attempt_id="attempt_synthetic_001",
        capability_id="capability_synthetic",
        resource_id="resource_synthetic",
        action_id="action_synthetic",
        entitlement_snapshot_hash="a" * 64,
        idempotency_key="idempotency_synthetic_001",
        expires_after_seconds=30,
    )

    for invalid_value in (True, 30.5):
        with pytest.raises(ValueError, match="integer"):
            replace(
                request,
                expires_after_seconds=invalid_value,
            ).validate()


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("provider_id", 123),
        ("cell_local_trace_ref", 123),
        ("input_tokens", True),
        ("output_tokens", 1.5),
        ("estimated_cost_usd", float("nan")),
        ("estimated_cost_usd", float("inf")),
        ("provider_charge_usd", Decimal("0.1")),
    ),
)
def test_provider_result_rejects_non_json_or_ambiguous_scalars(
    field_name: str,
    invalid_value: object,
) -> None:
    result = replace(_provider_result(), **{field_name: invalid_value})

    with pytest.raises(ValueError):
        result.validate()


@pytest.mark.parametrize("container_kind", ("list", "set", "generator"))
def test_provider_request_requires_exact_tuple_for_authorized_inputs(
    container_kind: str,
) -> None:
    invalid_inputs = _non_tuple_container(
        container_kind,
        _authorized_execution_input(),
    )

    with pytest.raises(ValueError, match="authorized_inputs must be an exact tuple"):
        replace(_authorized_provider_request(), authorized_inputs=invalid_inputs).validate()


@pytest.mark.parametrize(
    "invalid_item",
    (
        "not-an-authorized-input",
        _AuthorizedExecutionInputSubclass(
            execution_input_id="execution_input_synthetic_002",
            input_ref="artifact-ref:input-002",
            input_sha256="c" * 64,
            schema_ref="schema:input_secondary@v1",
            schema_sha256="9" * 64,
            media_type="application/json",
            logical_name="input-secondary",
            local_handle="workspace/input-secondary.json",
        ),
    ),
)
def test_provider_request_requires_exact_authorized_input_record_type(
    invalid_item: object,
) -> None:
    with pytest.raises(ValueError, match="exact AuthorizedExecutionInput records"):
        replace(_authorized_provider_request(), authorized_inputs=(invalid_item,)).validate()


def test_provider_request_rejects_duplicate_input_local_handles() -> None:
    first_input = _authorized_execution_input()
    colliding_input = replace(
        first_input,
        execution_input_id="execution_input_synthetic_002",
        input_ref="artifact-ref:input-002",
    )

    with pytest.raises(ValueError, match="unique local_handle"):
        replace(
            _authorized_provider_request(),
            authorized_inputs=(first_input, colliding_input),
        ).validate()


def test_provider_request_rejects_duplicate_authorized_input_ids() -> None:
    first_input = _authorized_execution_input()
    duplicate_input = replace(
        first_input,
        input_ref="artifact-ref:input-duplicate",
        local_handle="workspace/input-duplicate.json",
    )

    with pytest.raises(ValueError, match="unique execution_input_id"):
        replace(
            _authorized_provider_request(),
            authorized_inputs=(first_input, duplicate_input),
        ).validate()


@pytest.mark.parametrize("key_type", (_IdentityHashedString, _UnhashableString))
def test_provider_request_rejects_noncanonical_string_record_keys(
    key_type: type[str],
) -> None:
    first_input = replace(
        _authorized_execution_input(),
        execution_input_id=key_type("execution_input_synthetic_001"),
    )
    second_input = replace(
        _authorized_execution_input(),
        execution_input_id=key_type("execution_input_synthetic_001"),
        input_ref="artifact-ref:input-duplicate",
        local_handle="workspace/input-duplicate.json",
    )

    with pytest.raises(ValueError):
        replace(
            _authorized_provider_request(),
            authorized_inputs=(first_input, second_input),
        ).validate()


@pytest.mark.parametrize("container_kind", ("list", "set", "generator"))
def test_provider_result_requires_exact_tuple_for_outputs(
    container_kind: str,
) -> None:
    invalid_outputs = _non_tuple_container(
        container_kind,
        _provider_result().outputs[0],
    )

    with pytest.raises(ValueError, match="outputs must be an exact tuple"):
        replace(_provider_result(), outputs=invalid_outputs).validate()


@pytest.mark.parametrize(
    "invalid_item",
    (
        "not-an-output-submission",
        _OutputSubmissionSubclass(
            output_slot_id="output_secondary",
            local_handle="workspace/output-secondary.json",
        ),
    ),
)
def test_provider_result_requires_exact_output_submission_record_type(
    invalid_item: object,
) -> None:
    with pytest.raises(ValueError, match="exact OutputSubmission records"):
        replace(_provider_result(), outputs=(invalid_item,)).validate()


def test_provider_result_rejects_duplicate_output_slot_ids() -> None:
    first_output = _provider_result().outputs[0]
    duplicate_output = replace(
        first_output,
        local_handle="workspace/output-primary-duplicate.json",
    )

    with pytest.raises(ValueError, match="unique output_slot_id"):
        replace(
            _provider_result(),
            outputs=(first_output, duplicate_output),
        ).validate()


@pytest.mark.parametrize("key_type", (_IdentityHashedString, _UnhashableString))
def test_provider_result_rejects_noncanonical_string_record_keys(
    key_type: type[str],
) -> None:
    first_output = replace(
        _provider_result().outputs[0],
        output_slot_id=key_type("output_primary"),
    )
    second_output = replace(
        _provider_result().outputs[0],
        output_slot_id=key_type("output_primary"),
        local_handle="workspace/output-primary-duplicate.json",
    )

    with pytest.raises(ValueError):
        replace(
            _provider_result(),
            outputs=(first_output, second_output),
        ).validate()


def test_provider_result_serializes_as_strict_json() -> None:
    _authorized_provider_request().validate()
    payload = _provider_result().as_dict()

    decoded = json.loads(json.dumps(payload, allow_nan=False))

    assert decoded["terminal_status"] == "completed"
    assert decoded["input_tokens"] == 12
    assert decoded["outputs"] == [
        {
            "output_slot_id": "output_primary",
            "local_handle": "workspace/output-primary.json",
        }
    ]


def test_provider_string_tuple_fields_reject_stateful_tuple_subclasses() -> None:
    context = AdapterContextRequest(
        mode="reconstruct",
        compatibility_sha256="f" * 64,
        context_type="provider_native",
        resume_mode="artifact_reconstruction",
        read_isolation="entitled_refs",
        reconstruction_input_refs=("artifact-ref:continuity-001",),
    )
    cases = (
        (
            _durable_start_request(),
            "execution_input_package_refs",
            ("artifact-ref:input-001",),
        ),
        (
            _provider_descriptor(),
            "supported_context_modes",
            ("stateless", "resume"),
        ),
        (
            _provider_descriptor(),
            "supported_output_constraint_modes",
            ("prompt_only_json", "native_structured_output"),
        ),
        (
            _provider_descriptor(),
            "supported_read_isolation_modes",
            ("entitled_refs",),
        ),
        (
            context,
            "reconstruction_input_refs",
            ("artifact-ref:continuity-001",),
        ),
        (
            _provider_result(),
            "model_operation_ref_ids",
            ("operation_model_001",),
        ),
        (
            _provider_result(),
            "tool_operation_ref_ids",
            ("operation_tool_001",),
        ),
    )

    for record, field_name, safe_values in cases:
        stateful_values = _StatefulTuple(*safe_values)

        with pytest.raises(ValueError, match=rf"{field_name} must be an exact tuple"):
            replace(record, **{field_name: stateful_values}).validate()

        assert stateful_values.iteration_count == 0

def test_every_provider_dto_rejects_top_level_subclasses() -> None:
    context = AdapterContextRequest(
        mode="stateless",
        compatibility_sha256="f" * 64,
        context_type="provider_native",
        resume_mode="not_applicable",
        read_isolation="entitled_refs",
        reconstruction_input_refs=(),
    )
    records = (
        _provider_descriptor(),
        _authorized_execution_input(),
        context,
        _provider_result().context,
        _operation_authorization_request(),
        _provider_failure(),
        _authorized_provider_request(),
        _provider_result().outputs[0],
        _provider_result(),
    )

    for record in records:
        record_type = type(record)
        untrusted_type = type(
            f"_Untrusted{record_type.__name__}",
            (record_type,),
            {},
        )
        untrusted_record = untrusted_type(
            **{
                field.name: getattr(record, field.name)
                for field in fields(record_type)
            }
        )

        with pytest.raises(ValueError, match=rf"exact {record_type.__name__} record"):
            untrusted_record.validate()


def test_provider_result_subclass_cannot_serialize_added_secret_field() -> None:
    result = _provider_result()
    untrusted_result = _AgentExecutionResultWithSecret(
        **{
            field.name: getattr(result, field.name)
            for field in fields(AgentExecutionResult)
        }
    )

    with pytest.raises(ValueError, match="exact AgentExecutionResult record"):
        untrusted_result.as_dict()


def test_provider_result_as_dict_uses_class_qualified_validation() -> None:
    result = _provider_result()
    object.__setattr__(result, "provider_id", _ForeignNestedRecord())
    object.__setattr__(result, "validate", lambda: None)

    with pytest.raises(ValueError, match="provider_id"):
        result.as_dict()


@pytest.mark.parametrize("field_name", ("context", "failure"))
def test_provider_result_rejects_foreign_nested_dataclass_before_asdict(
    field_name: str,
) -> None:
    values: dict[str, object] = {field_name: _ForeignNestedRecord()}
    if field_name == "failure":
        values["terminal_status"] = "failed"
    result = replace(_provider_result(), **values)

    with pytest.raises(ValueError, match=rf"result {field_name} must be an exact"):
        result.as_dict()


@pytest.mark.parametrize("field_name", ("context", "failure"))
def test_provider_result_ignores_shadowed_nested_validators(
    field_name: str,
) -> None:
    result = _provider_result()
    if field_name == "context":
        nested_record = result.context
        object.__setattr__(nested_record, "context_ref", _ForeignNestedRecord())
    else:
        nested_record = _provider_failure()
        object.__setattr__(nested_record, "detail_ref", _ForeignNestedRecord())
        result = replace(result, terminal_status="failed", failure=nested_record)
    object.__setattr__(nested_record, "validate", lambda: None)

    with pytest.raises(ValueError):
        result.as_dict()


@pytest.mark.parametrize(
    "field_name",
    (
        "module_release_sha256",
        "execution_profile_sha256",
        "attempt_begin_receipt_sha256",
        "prompt_envelope_sha256",
        "output_schema_sha256",
        "execution_authorization_binding_sha256",
        "protected_operation_intent_sha256",
        "product_operation_decision_sha256",
        "gateway_authorization_observation_sha256",
        "operation_grant_sha256",
        "input_closure_sha256",
        "request_sha256",
    ),
)
def test_authorized_provider_request_rejects_hash_tampering(
    field_name: str,
) -> None:
    request = _authorized_provider_request()
    invalid_value = "0" * 64 if getattr(request, field_name) != "0" * 64 else "1" * 64

    with pytest.raises(ValueError):
        replace(request, **{field_name: invalid_value}).validate()


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    (
        ("workflow_execution_id", "BAD ID"),
        ("module_run_id", "BAD ID"),
        ("module_release_ref", "latest"),
        ("execution_profile_ref", "mutable/profile"),
        ("attempt_begin_receipt_ref", "receipt without scheme"),
        ("prompt_envelope_ref", "data:secret"),
        ("output_schema_ref", "schema without version scheme"),
        ("execution_authorization_binding_ref", "inline authority"),
        ("operation_grant_ref", "data:secret"),
        ("data_use_purpose_id", "BAD ID"),
        ("authorized_inputs", [_authorized_execution_input()]),
    ),
)
def test_authorized_provider_request_rejects_malformed_fields(
    field_name: str,
    invalid_value: object,
) -> None:
    with pytest.raises(ValueError):
        replace(
            _authorized_provider_request(),
            **{field_name: invalid_value},
        ).validate()


def test_authorized_provider_request_rejects_top_level_subclass() -> None:
    request = _authorized_provider_request()
    subclass = type(
        "_UntrustedAuthorizedAgentExecutionRequest",
        (AuthorizedAgentExecutionRequest,),
        {},
    )
    untrusted = subclass(
        **{field.name: getattr(request, field.name) for field in fields(type(request))}
    )

    with pytest.raises(
        ValueError,
        match="exact AuthorizedAgentExecutionRequest record",
    ):
        untrusted.validate()
