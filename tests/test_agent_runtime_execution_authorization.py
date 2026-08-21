from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.contracts.execution_authorization_definition import (
    ExecutionAuthorizationContextEnvelope,
    ExecutionAuthorizationContextState,
    ExecutionAuthorizationFenceState,
    GatewayDecisionEffect,
    ProductAuthorizationContextStatus,
)
from agent_runtime.contracts.registry_release_definition import (
    ModuleEntryPolicy,
    ModuleKind,
    OutputResolutionPolicy,
    RuntimeModuleRelease,
)
from agent_runtime.execution.execution_authorization_coordination import (
    ExecutionAuthorizationController,
    InMemoryExecutionAuthorizationLedger,
)


NOW = "2026-08-05T12:00:00Z"


def _envelope() -> ExecutionAuthorizationContextEnvelope:
    return ExecutionAuthorizationContextEnvelope.build(
        context_id="execution_context_001",
        context_ref="product-authorization:execution-context-001",
        workflow_execution_id="workflow_execution_001",
        workflow_release_id="workflow_release_001",
        principal_id="principal_human_001",
        actor_workload_id="runtime_workload_001",
        tenant_id="tenant_alpha_001",
        cell_id="cell_alpha_001",
        authorization_decision_ref="product-authorization:decision-001",
        catalog_release_ref="product-authorization:catalog-001",
        input_scope_refs=("canonical-source:source-001",),
        effective_at_utc="2026-08-05T11:00:00Z",
        expiry_at_utc="2026-08-05T13:00:00Z",
    )


class _Client:
    def __init__(self, envelope: ExecutionAuthorizationContextEnvelope) -> None:
        self.envelope = envelope
        self.state = ExecutionAuthorizationContextState.EFFECTIVE
        self.reason_code = "context_effective"
        self.calls: list[tuple[str, str, str]] = []

    def validate_execution_context(
        self,
        context_id: str,
        tenant_id: str,
        observed_at_utc: str,
    ) -> ProductAuthorizationContextStatus:
        self.calls.append((context_id, tenant_id, observed_at_utc))
        return ProductAuthorizationContextStatus.build(
            status_ref=(
                f"product-authorization:context-status-{len(self.calls):03d}"
            ),
            context_id=self.envelope.context_id,
            context_ref=self.envelope.context_ref,
            context_sha256=self.envelope.context_sha256,
            state=self.state,
            reason_code=self.reason_code,
            observed_at_utc=observed_at_utc,
        )


def _module(
    *,
    operation_ids: tuple[str, ...] = ("knowledge_search", "publish_report"),
) -> RuntimeModuleRelease:
    return RuntimeModuleRelease.build(
        module_id="research_writer",
        module_version="1.0.0",
        release_ref="runtime-module:research_writer@1",
        module_kind=ModuleKind.AGENT,
        owner_contract_ref="design-doc:research-writer@1",
        owner_contract_sha256="1" * 64,
        executable_ref=None,
        executable_sha256=None,
        input_schema_ref="schema:research-writer-input@1",
        input_schema_sha256="4" * 64,
        output_schema_ref="schema:research-writer-output@1",
        output_schema_sha256="5" * 64,
        prompt_bundle_ref="prompt:research-writer@1",
        prompt_bundle_sha256="6" * 64,
        declared_operation_ids=operation_ids,
        behavior_policy_ref="behavior-policy:task-scoped@1",
        behavior_policy_sha256="7" * 64,
        evaluation_policy_ref="evaluation-policy:research-writer@1",
        evaluation_policy_sha256="8" * 64,
        retry_policy_ref="retry-policy:bounded@1",
        retry_policy_sha256="9" * 64,
        compatible_transport_kinds=("in_process_test",),
        entry_policy=ModuleEntryPolicy.WORKFLOW_BOUND,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )


class _ModuleReleaseClient:
    def __init__(self, module: RuntimeModuleRelease) -> None:
        self.module = module

    def resolve_registered_module_release(
        self,
        release_ref: str,
        release_sha256: str,
    ) -> RuntimeModuleRelease:
        if (
            release_ref != self.module.release_ref
            or release_sha256 != self.module.release_sha256
        ):
            raise KeyError("unknown registered Module Release")
        return self.module


def _controller(
    module: RuntimeModuleRelease | None = None,
) -> tuple[ExecutionAuthorizationController, _Client]:
    envelope = _envelope()
    client = _Client(envelope)
    registered_module = module or _module()
    return (
        ExecutionAuthorizationController(
            client=client,
            ledger=InMemoryExecutionAuthorizationLedger(),
            module_release_client=_ModuleReleaseClient(registered_module),
        ),
        client,
    )


def _admit(controller: ExecutionAuthorizationController):
    envelope = _envelope()
    return controller.bind_execution_context(
        envelope=envelope,
        expected_workflow_execution_id=envelope.workflow_execution_id,
        expected_workflow_release_id=envelope.workflow_release_id,
        expected_principal_id=envelope.principal_id,
        expected_actor_workload_id=envelope.actor_workload_id,
        expected_tenant_id=envelope.tenant_id,
        expected_cell_id=envelope.cell_id,
        execution_input_package_ref="execution-input:package-001",
        execution_input_package_sha256="a" * 64,
        observed_at_utc=NOW,
    )


def test_admission_binds_subject_and_actor_without_execution_principal() -> None:
    controller, client = _controller()

    admission = _admit(controller)

    assert admission.binding.principal_id == "principal_human_001"
    assert admission.binding.actor_workload_id == "runtime_workload_001"
    assert admission.binding.principal_id != admission.binding.actor_workload_id
    assert admission.fence.state is ExecutionAuthorizationFenceState.OPEN
    assert client.calls == [
        ("execution_context_001", "tenant_alpha_001", NOW)
    ]


def test_binding_and_fence_replay_ignore_host_clock_progress() -> None:
    envelope = _envelope()

    class _StableClient(_Client):
        def validate_execution_context(
            self,
            context_id: str,
            tenant_id: str,
            observed_at_utc: str,
        ) -> ProductAuthorizationContextStatus:
            self.calls.append((context_id, tenant_id, observed_at_utc))
            return ProductAuthorizationContextStatus.build(
                status_ref="product-authorization:context-status-stable",
                context_id=self.envelope.context_id,
                context_ref=self.envelope.context_ref,
                context_sha256=self.envelope.context_sha256,
                state=self.state,
                reason_code=self.reason_code,
                observed_at_utc=observed_at_utc,
            )

    client = _StableClient(envelope)
    controller = ExecutionAuthorizationController(
        client=client,
        ledger=InMemoryExecutionAuthorizationLedger(),
        module_release_client=_ModuleReleaseClient(_module()),
    )

    first = _admit(controller)
    replay = _admit(controller)
    fence_replay = controller.revalidate(
        binding_ref=first.binding.binding_ref,
        observed_at_utc=NOW,
    )

    assert replay.binding is first.binding
    assert replay.fence == first.fence
    assert fence_replay == first.fence
    assert first.binding.recorded_at_utc == NOW
    assert first.fence.recorded_at_utc == NOW


def test_caller_selected_tenant_mismatch_fails_before_binding() -> None:
    controller, client = _controller()
    envelope = _envelope()

    with pytest.raises(PermissionError, match="closure mismatch"):
        controller.bind_execution_context(
            envelope=envelope,
            expected_workflow_execution_id=envelope.workflow_execution_id,
            expected_workflow_release_id=envelope.workflow_release_id,
            expected_principal_id=envelope.principal_id,
            expected_actor_workload_id=envelope.actor_workload_id,
            expected_tenant_id="tenant_beta_001",
            expected_cell_id=envelope.cell_id,
            execution_input_package_ref="execution-input:package-001",
            execution_input_package_sha256="a" * 64,
            observed_at_utc=NOW,
        )

    assert client.calls == []


def test_revocation_monotonically_fences_reentry() -> None:
    controller, client = _controller()
    admission = _admit(controller)
    client.state = ExecutionAuthorizationContextState.REVOKED
    client.reason_code = "context_revoked"

    fenced = controller.revalidate(
        binding_ref=admission.binding.binding_ref,
        observed_at_utc="2026-08-05T12:05:00Z",
    )
    client.state = ExecutionAuthorizationContextState.EFFECTIVE
    client.reason_code = "context_effective"
    repeated = controller.revalidate(
        binding_ref=admission.binding.binding_ref,
        observed_at_utc="2026-08-05T12:06:00Z",
    )

    assert fenced.state is ExecutionAuthorizationFenceState.FENCED
    assert fenced.reason_code == "context_revoked"
    assert repeated == fenced


def test_standard_operation_records_intent_without_grant() -> None:
    controller, _ = _controller()
    admission = _admit(controller)
    module = _module()

    intent = controller.commit_protected_operation_intent(
        binding_ref=admission.binding.binding_ref,
        module_run_id="module_run_001",
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        operation_id="knowledge_search",
        resource_ref="knowledge-corpus:research-001",
        enforcing_gateway_id="knowledge_gateway",
        idempotency_key="knowledge_search_001",
        requires_grant=False,
        operation_grant_ref=None,
        observed_at_utc=NOW,
    )
    observation = controller.record_gateway_observation(
        intent_ref=intent.intent_ref,
        decision_ref="product-authorization:decision-002",
        decision_sha256="b" * 64,
        effect=GatewayDecisionEffect.ALLOW,
        effect_evidence_ref=None,
        grant_disposition_ref=None,
        observed_at_utc=NOW,
    )

    assert intent.requires_grant is False
    assert intent.operation_grant_ref is None
    assert observation.grant_disposition_ref is None


def test_protected_operation_replay_converges_across_retries() -> None:
    controller, _ = _controller()
    admission = _admit(controller)
    module = _module()
    intent_kwargs = dict(
        binding_ref=admission.binding.binding_ref,
        module_run_id="module_run_replay_001",
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        operation_id="knowledge_search",
        resource_ref="knowledge-corpus:research-001",
        enforcing_gateway_id="knowledge_gateway",
        idempotency_key="knowledge_search_replay_001",
        requires_grant=False,
        operation_grant_ref=None,
        observed_at_utc=NOW,
    )

    first_intent = controller.commit_protected_operation_intent(**intent_kwargs)
    replayed_intent = controller.commit_protected_operation_intent(
        **intent_kwargs
    )

    assert replayed_intent is first_intent
    assert first_intent.recorded_at_utc == NOW

    observation_kwargs = dict(
        intent_ref=first_intent.intent_ref,
        decision_ref="product-authorization:decision-replay-001",
        decision_sha256="b" * 64,
        effect=GatewayDecisionEffect.ALLOW,
        effect_evidence_ref=None,
        grant_disposition_ref=None,
        observed_at_utc=NOW,
    )

    first_observation = controller.record_gateway_observation(
        **observation_kwargs
    )
    replayed_observation = controller.record_gateway_observation(
        **observation_kwargs
    )

    assert replayed_observation is first_observation
    assert first_observation.recorded_at_utc == NOW


def test_high_risk_operation_requires_grant_and_terminal_disposition() -> None:
    controller, _ = _controller()
    admission = _admit(controller)
    module = _module()
    base = dict(
        binding_ref=admission.binding.binding_ref,
        module_run_id="module_run_001",
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        operation_id="publish_report",
        resource_ref="research-report:theme-001",
        enforcing_gateway_id="publication_gateway",
        idempotency_key="publish_report_001",
        requires_grant=True,
        observed_at_utc=NOW,
    )

    with pytest.raises(ValueError, match="needs operation_grant_ref"):
        controller.commit_protected_operation_intent(
            **base,
            operation_grant_ref=None,
        )

    intent = controller.commit_protected_operation_intent(
        **base,
        operation_grant_ref="product-authorization:operation-grant-001",
    )
    with pytest.raises(ValueError, match="needs disposition ref"):
        controller.record_gateway_observation(
            intent_ref=intent.intent_ref,
            decision_ref="product-authorization:decision-003",
            decision_sha256="c" * 64,
            effect=GatewayDecisionEffect.ALLOW,
            effect_evidence_ref="publication:effect-001",
            grant_disposition_ref=None,
            observed_at_utc=NOW,
        )


def test_registered_module_release_is_the_permission_source() -> None:
    module = _module(operation_ids=("model_execute",))
    controller, _ = _controller(module)
    admission = _admit(controller)

    with pytest.raises(PermissionError, match="absent from Module Release"):
        controller.commit_protected_operation_intent(
            binding_ref=admission.binding.binding_ref,
            module_run_id="module_run_undeclared_operation",
            module_release_ref=module.release_ref,
            module_release_sha256=module.release_sha256,
            operation_id="knowledge_search",
            resource_ref="knowledge-corpus:research-001",
            enforcing_gateway_id="knowledge_gateway",
            idempotency_key="knowledge_search_undeclared",
            requires_grant=False,
            operation_grant_ref=None,
            observed_at_utc=NOW,
        )

def test_runtime_authorization_core_has_no_product_or_backend_dependency() -> None:
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8").lower()
        for path in (
            (
                "src/agent_runtime/execution/"
                "execution_authorization_resolution.py"
            ),
            (
                "src/agent_runtime/execution/"
                "execution_authorization_coordination.py"
            ),
            (
                "src/agent_runtime/execution/"
                "execution_operation_resolution.py"
            ),
            (
                "src/agent_runtime/contracts/"
                "execution_authorization_definition.py"
            ),
        )
    )

    assert "src.product_authorization" not in source
    assert "psycopg" not in source
    assert "temporal" not in source
    assert "claude" not in source
    assert "codex" not in source
