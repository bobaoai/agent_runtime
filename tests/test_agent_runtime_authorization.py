from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.execution.execution_operation_authorization import (
    InMemoryAuthorizationLedger,
    RuntimeAuthorizationCoordinator,
)
from agent_runtime.contracts.execution_operation_definition import (
    AuthorizationEffect,
    ExecutionAuthorizationBinding,
    ExecutionAuthorizationStatus,
    ExecutionAuthorizationStatusEvidence,
    ExecutionControlFenceStatus,
    OperationAuthorizationRequest,
    OperationAuthorizationResolution,
    ProductOperationAuthorizationResult,
)
from agent_runtime.contracts.registry_release_definition import (
    ModuleEntryPolicy,
    ModuleKind,
    OutputResolutionPolicy,
    RuntimeModuleRelease,
)


NOW = "2026-08-05T12:00:00Z"


def _module() -> RuntimeModuleRelease:
    return RuntimeModuleRelease.build(
        module_id="research_source_verifier",
        module_version="1.0.0",
        release_ref="runtime-module:research_source_verifier@1",
        module_kind=ModuleKind.DETERMINISTIC,
        owner_contract_ref="design-doc:research-source-verifier@1",
        owner_contract_sha256="1" * 64,
        executable_ref="python:tests.test_agent_runtime_authorization._module",
        executable_sha256="0" * 64,
        input_schema_ref="schema:source-verifier-input@1",
        input_schema_sha256="2" * 64,
        output_schema_ref="schema:source-verifier-output@1",
        output_schema_sha256="3" * 64,
        prompt_bundle_ref=None,
        prompt_bundle_sha256=None,
        declared_operation_ids=("knowledge_search",),
        behavior_policy_ref="behavior-policy:isolated@1",
        behavior_policy_sha256="4" * 64,
        evaluation_policy_ref="evaluation-policy:source-fidelity@1",
        evaluation_policy_sha256="5" * 64,
        retry_policy_ref="retry-policy:bounded@1",
        retry_policy_sha256="6" * 64,
        compatible_transport_kinds=("in_process_test",),
        entry_policy=ModuleEntryPolicy.WORKFLOW_BOUND,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )


def _binding() -> ExecutionAuthorizationBinding:
    return ExecutionAuthorizationBinding.build(
        binding_id="execution_binding_001",
        binding_ref="runtime-authorization:execution-binding-001",
        workflow_execution_id="workflow_execution_001",
        tenant_id="tenant_synthetic_001",
        cell_id="cell_synthetic_001",
        initiating_principal_id="principal_human_001",
        execution_principal_id="principal_runtime_001",
        workflow_release_ref="runtime-workflow:source-evidence@1",
        workflow_release_sha256="7" * 64,
        authorization_decision_ref="product-authorization:decision-001",
        authorization_decision_sha256="8" * 64,
        execution_principal_delegation_ref="product-authorization:delegation-001",
        execution_principal_delegation_sha256="9" * 64,
        execution_input_package_ref="execution-input:package-001",
        execution_input_package_sha256="a" * 64,
        effective_at_utc="2026-08-05T11:00:00Z",
        expiry_at_utc="2026-08-05T13:00:00Z",
        recorded_at_utc="2026-08-05T11:00:00Z",
    )


def _status(
    binding: ExecutionAuthorizationBinding,
    *,
    status: ExecutionAuthorizationStatus = ExecutionAuthorizationStatus.EFFECTIVE,
    fence: ExecutionControlFenceStatus = ExecutionControlFenceStatus.OPEN,
) -> ExecutionAuthorizationStatusEvidence:
    return ExecutionAuthorizationStatusEvidence.build(
        evidence_id="authorization_status_001",
        evidence_ref="runtime-authorization:status-001",
        binding_ref=binding.binding_ref,
        binding_sha256=binding.binding_sha256,
        status=status,
        control_fence_ref="runtime-control:fence-001",
        control_fence_sha256="b" * 64,
        control_fence_status=fence,
        recorded_at_utc=NOW,
    )


def _request(
    binding: ExecutionAuthorizationBinding,
    module: RuntimeModuleRelease,
    *,
    operation_id: str = "knowledge_search",
    binding_ref: str | None = None,
) -> OperationAuthorizationRequest:
    return OperationAuthorizationRequest.build(
        authorization_request_id="operation_request_001",
        authorization_request_ref="runtime-authorization:operation-request-001",
        workflow_execution_id=binding.workflow_execution_id,
        execution_authorization_binding_ref=binding_ref or binding.binding_ref,
        execution_authorization_binding_sha256=binding.binding_sha256,
        module_run_id="module_run_001",
        variant_id="module_variant_001",
        attempt_id="module_attempt_001",
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        enforcement_service_id="knowledge_gateway_001",
        operation_id=operation_id,
        resource_id="research_corpus_001",
        action_id="search_metadata_001",
        data_use_purpose_id="research_evidence_001",
        idempotency_key="operation_idempotency_001",
        expiry_at_utc="2026-08-05T12:30:00Z",
        recorded_at_utc=NOW,
    )


class _Adapter:
    def __init__(self, *, effect: AuthorizationEffect) -> None:
        self.effect = effect
        self.calls = 0
        self.request_override_ref: str | None = None
        self.effective_at_utc = "2026-08-05T11:30:00Z"
        self.expiry_at_utc = "2026-08-05T12:30:00Z"

    def authorize_operation(
        self,
        request: OperationAuthorizationRequest,
    ) -> ProductOperationAuthorizationResult:
        self.calls += 1
        selected_kind = "grant" if self.effect is AuthorizationEffect.ALLOW else "denial"
        return ProductOperationAuthorizationResult(
            result_ref="product-authorization:operation-result-001",
            result_sha256="c" * 64,
            effect=self.effect,
            authorization_request_ref=(
                self.request_override_ref or request.authorization_request_ref
            ),
            authorization_request_sha256=request.authorization_request_sha256,
            selected_authority_ref=(
                f"product-authorization:{selected_kind}-001"
            ),
            selected_authority_sha256="d" * 64,
            issuer_id="product_authorization_001",
            effective_at_utc=self.effective_at_utc,
            expiry_at_utc=self.expiry_at_utc,
        )


def _coordinator(adapter: _Adapter) -> RuntimeAuthorizationCoordinator:
    return RuntimeAuthorizationCoordinator(
        adapter=adapter,
        ledger=InMemoryAuthorizationLedger(),
        clock=lambda: NOW,
    )


def test_allow_commits_grant_binding_and_exact_retry_is_idempotent() -> None:
    module = _module()
    binding = _binding()
    request = _request(binding, module)
    adapter = _Adapter(effect=AuthorizationEffect.ALLOW)
    coordinator = _coordinator(adapter)

    first = coordinator.authorize_and_bind_operation(
        request=request,
        execution_binding=binding,
        status_evidence=_status(binding),
        module_release=module,
    )
    second = coordinator.authorize_and_bind_operation(
        request=request,
        execution_binding=binding,
        status_evidence=_status(binding),
        module_release=module,
    )

    assert first == second
    assert adapter.calls == 1
    assert first.grant_binding is not None
    assert first.resolution.resolution is OperationAuthorizationResolution.GRANT_BOUND
    assert first.resolution.operation_grant_binding_ref == first.grant_binding.binding_ref


def test_deny_is_terminal_and_produces_no_grant_binding() -> None:
    module = _module()
    binding = _binding()
    result = _coordinator(_Adapter(effect=AuthorizationEffect.DENY)).authorize_and_bind_operation(
        request=_request(binding, module),
        execution_binding=binding,
        status_evidence=_status(binding),
        module_release=module,
    )

    assert result.grant_binding is None
    assert result.resolution.resolution is OperationAuthorizationResolution.DENIED


@pytest.mark.parametrize(
    ("status", "fence", "message"),
    [
        (
            ExecutionAuthorizationStatus.INVALIDATED,
            ExecutionControlFenceStatus.OPEN,
            "not effective",
        ),
        (
            ExecutionAuthorizationStatus.EFFECTIVE,
            ExecutionControlFenceStatus.FENCED,
            "fence is closed",
        ),
    ],
)
def test_stale_authority_or_closed_fence_fails_before_product_call(
    status: ExecutionAuthorizationStatus,
    fence: ExecutionControlFenceStatus,
    message: str,
) -> None:
    module = _module()
    binding = _binding()
    adapter = _Adapter(effect=AuthorizationEffect.ALLOW)

    with pytest.raises(PermissionError, match=message):
        _coordinator(adapter).authorize_and_bind_operation(
            request=_request(binding, module),
            execution_binding=binding,
            status_evidence=_status(binding, status=status, fence=fence),
            module_release=module,
        )
    assert adapter.calls == 0


def test_undeclared_operation_fails_before_product_call() -> None:
    module = _module()
    binding = _binding()
    adapter = _Adapter(effect=AuthorizationEffect.ALLOW)

    with pytest.raises(PermissionError, match="absent from Module Release"):
        _coordinator(adapter).authorize_and_bind_operation(
            request=_request(binding, module, operation_id="external_send"),
            execution_binding=binding,
            status_evidence=_status(binding),
            module_release=module,
        )
    assert adapter.calls == 0


def test_binding_or_module_substitution_fails_before_product_call() -> None:
    module = _module()
    binding = _binding()
    adapter = _Adapter(effect=AuthorizationEffect.ALLOW)
    with pytest.raises(ValueError, match="execution binding mismatch"):
        _coordinator(adapter).authorize_and_bind_operation(
            request=_request(
                binding,
                module,
                binding_ref="runtime-authorization:other",
            ),
            execution_binding=binding,
            status_evidence=_status(binding),
            module_release=module,
        )

    other_module = _module()
    other_module = RuntimeModuleRelease.build(
        **{
            key: value
            for key, value in other_module.__dict__.items()
            if key != "release_sha256"
        }
        | {"release_ref": "runtime-module:other@1"},
    )
    with pytest.raises(ValueError, match="Module Release mismatch"):
        _coordinator(adapter).authorize_and_bind_operation(
            request=_request(binding, module),
            execution_binding=binding,
            status_evidence=_status(binding),
            module_release=other_module,
        )
    assert adapter.calls == 0


def test_product_result_request_substitution_fails_closed() -> None:
    module = _module()
    binding = _binding()
    adapter = _Adapter(effect=AuthorizationEffect.ALLOW)
    adapter.request_override_ref = "runtime-authorization:other-request"

    with pytest.raises(ValueError, match="result request mismatch"):
        _coordinator(adapter).authorize_and_bind_operation(
            request=_request(binding, module),
            execution_binding=binding,
            status_evidence=_status(binding),
            module_release=module,
        )


def test_product_result_outside_validity_fails_closed() -> None:
    module = _module()
    binding = _binding()
    adapter = _Adapter(effect=AuthorizationEffect.ALLOW)
    adapter.effective_at_utc = "2026-08-05T12:05:00Z"

    with pytest.raises(PermissionError, match="outside validity"):
        _coordinator(adapter).authorize_and_bind_operation(
            request=_request(binding, module),
            execution_binding=binding,
            status_evidence=_status(binding),
            module_release=module,
        )


def test_authorization_records_reject_hash_and_timestamp_tampering() -> None:
    binding = _binding()
    with pytest.raises(ValueError, match="binding hash mismatch"):
        replace(binding, workflow_release_sha256="f" * 64).validate()

    module = _module()
    request = _request(binding, module)
    with pytest.raises(ValueError, match="already expired"):
        replace(
            request,
            expiry_at_utc="2026-08-05T11:59:59Z",
            authorization_request_sha256="e" * 64,
        ).validate()
