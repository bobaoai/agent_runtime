from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.contracts.execution_operation_definition import (
    AuthorizationEffect,
    ExecutionAuthorizationBinding,
    ExecutionAuthorizationStatus,
    ExecutionAuthorizationStatusEvidence,
    ExecutionControlFenceStatus,
)
from agent_runtime.contracts.execution_event_definition import (
    ExternalEventExecutionSnapshot,
)
from agent_runtime.contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ModuleEntryPolicy,
    ModuleKind,
    OutputResolutionPolicy,
    ReleaseAdmissionIntent,
    ReleaseAdmissionState,
    ReleaseSubjectKind,
    RuntimeModuleRelease,
    RetryPolicyRelease,
    SchemaAssetRelease,
    WorkflowEdge,
    WorkflowNodeKind,
    WorkflowNodeBinding,
    WorkflowRelease,
)
from agent_runtime.registry.registry_release_compilation import (
    runtime_owned_policy_schema_assets,
)
from agent_runtime.execution.execution_event_ingestion import (
    ExternalActionAuthorizationEvidence,
    ExternalEventIngressRequest,
    ExecutionSnapshotToken,
    InMemoryExternalEventIngress,
    TrustedRequestContext,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


NOW = "2026-08-05T12:00:00Z"


def _module_schema_assets() -> tuple[SchemaAssetRelease, SchemaAssetRelease]:
    def build(schema_id: str) -> SchemaAssetRelease:
        release_ref = f"schema:{schema_id}@1"
        return SchemaAssetRelease.build(
            schema_asset_id=schema_id.replace("-", "_"),
            schema_asset_version="1",
            release_ref=release_ref,
            schema_document={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": release_ref,
                "type": "object",
                "additionalProperties": True,
            },
        )

    return build("event_input"), build("event_output")


def _policy_releases() -> tuple[
    tuple[object, ...],
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    RetryPolicyRelease,
]:
    schemas = runtime_owned_policy_schema_assets()
    schema_by_ref = {schema.release_ref: schema for schema in schemas}
    behavior_schema = schema_by_ref["schema:runtime_behavior_policy@v1"]
    evaluation_schema = schema_by_ref["schema:runtime_evaluation_policy@v1"]
    retry_schema = schema_by_ref["schema:runtime_retry_policy@v1"]
    behavior = BehaviorPolicyRelease.build(
        policy_id="isolated",
        policy_version="1",
        release_ref="behavior-policy:isolated@1",
        policy_schema_ref=behavior_schema.release_ref,
        policy_schema_sha256=behavior_schema.schema_sha256,
        policy_document={
            "context_isolation": "workflow_execution_isolated",
        },
    )
    evaluation = EvaluationPolicyRelease.build(
        policy_id="none",
        policy_version="1",
        release_ref="evaluation-policy:none@1",
        policy_schema_ref=evaluation_schema.release_ref,
        policy_schema_sha256=evaluation_schema.schema_sha256,
        policy_document={"evaluation_mode": "none"},
    )
    retry = RetryPolicyRelease.build(
        policy_id="bounded",
        policy_version="1",
        release_ref="retry-policy:bounded@1",
        policy_schema_ref=retry_schema.release_ref,
        policy_schema_sha256=retry_schema.schema_sha256,
        policy_document={"max_attempts": 3},
    )
    return schemas, behavior, evaluation, retry


def _module(
    *,
    behavior_policy_sha256: str,
    evaluation_policy_sha256: str,
    retry_policy_sha256: str,
    input_schema_sha256: str,
    output_schema_sha256: str,
) -> RuntimeModuleRelease:
    return RuntimeModuleRelease.build(
        module_id="research_event_handler",
        module_version="1.0.0",
        release_ref="runtime-module:research_event_handler@1",
        module_kind=ModuleKind.DETERMINISTIC,
        owner_contract_ref="design-doc:external-event@1",
        owner_contract_sha256="1" * 64,
        executable_ref="python:tests.test_agent_runtime_external_event_ingress._module",
        executable_sha256="0" * 64,
        input_schema_ref="schema:event_input@1",
        input_schema_sha256=input_schema_sha256,
        output_schema_ref="schema:event_output@1",
        output_schema_sha256=output_schema_sha256,
        prompt_bundle_ref=None,
        prompt_bundle_sha256=None,
        declared_operation_ids=(),
        behavior_policy_ref="behavior-policy:isolated@1",
        behavior_policy_sha256=behavior_policy_sha256,
        evaluation_policy_ref="evaluation-policy:none@1",
        evaluation_policy_sha256=evaluation_policy_sha256,
        retry_policy_ref="retry-policy:bounded@1",
        retry_policy_sha256=retry_policy_sha256,
        compatible_transport_kinds=("in_process_test",),
        entry_policy=ModuleEntryPolicy.WORKFLOW_BOUND,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )


def _workflow(
    module: RuntimeModuleRelease,
    *,
    approval_is_terminal: bool = False,
) -> WorkflowRelease:
    return WorkflowRelease.build(
        workflow_id="research_external_event",
        workflow_version="1.0.0",
        workflow_contract_version="contract_v1",
        release_ref="runtime-workflow:research_external_event@1",
        owner_contract_ref="design-doc:external-event@1",
        owner_contract_sha256="7" * 64,
        graph_ref="python:tests.test_agent_runtime_external_event_ingress._workflow",
        graph_sha256="c" * 64,
        initial_node_id="waiting_state",
        nodes=(
            WorkflowNodeBinding(
                node_id="waiting_state",
                node_kind=WorkflowNodeKind.MODULE,
                module_release_ref=module.release_ref,
                module_release_sha256=module.release_sha256,
                input_mapping_ref="input-map:waiting@1",
                input_mapping_sha256="8" * 64,
            ),
        ) if approval_is_terminal else (
            WorkflowNodeBinding(
                node_id="waiting_state",
                node_kind=WorkflowNodeKind.MODULE,
                module_release_ref=module.release_ref,
                module_release_sha256=module.release_sha256,
                input_mapping_ref="input-map:waiting@1",
                input_mapping_sha256="8" * 64,
            ),
            WorkflowNodeBinding(
                node_id="next_state",
                node_kind=WorkflowNodeKind.MODULE,
                module_release_ref=module.release_ref,
                module_release_sha256=module.release_sha256,
                input_mapping_ref="input-map:next@1",
                input_mapping_sha256="9" * 64,
            ),
        ),
        edges=(
            WorkflowEdge(
                source_node_id="waiting_state",
                outcome_id="approval_received",
                target_node_id=None if approval_is_terminal else "next_state",
                terminal=approval_is_terminal,
            ),
        ) if approval_is_terminal else (
            WorkflowEdge(
                source_node_id="waiting_state",
                outcome_id="approval_received",
                target_node_id="next_state",
                terminal=False,
            ),
            WorkflowEdge(
                source_node_id="next_state",
                outcome_id="complete_execution",
                target_node_id=None,
                terminal=True,
            ),
        ),
        authorization_manifest_ref="authorization-manifest:event@1",
        authorization_manifest_sha256="a" * 64,
        execution_release_ref="execution-release:event@1",
        execution_release_sha256="b" * 64,
    )


def _catalog(
    *,
    workflow_state: ReleaseAdmissionState = ReleaseAdmissionState.ACTIVE,
    approval_is_terminal: bool = False,
) -> tuple[RuntimeReleaseRegistry, WorkflowRelease]:
    schemas, behavior, evaluation, retry = _policy_releases()
    input_schema, output_schema = _module_schema_assets()
    module = _module(
        behavior_policy_sha256=behavior.release_sha256,
        evaluation_policy_sha256=evaluation.release_sha256,
        retry_policy_sha256=retry.release_sha256,
        input_schema_sha256=input_schema.schema_sha256,
        output_schema_sha256=output_schema.schema_sha256,
    )
    workflow = _workflow(module, approval_is_terminal=approval_is_terminal)
    admissions = [
        ReleaseAdmissionIntent.build(
            admission_id="workflow_admission_candidate_001",
            subject_kind=ReleaseSubjectKind.WORKFLOW,
            subject_id=workflow.workflow_id,
            release_ref=workflow.release_ref,
            release_sha256=workflow.release_sha256,
            state=ReleaseAdmissionState.CANDIDATE,
            evidence_members=(),
        )
    ]
    if workflow_state is ReleaseAdmissionState.ACTIVE:
        admissions.append(
            ReleaseAdmissionIntent.build(
                admission_id="workflow_admission_active_001",
                subject_kind=ReleaseSubjectKind.WORKFLOW,
                subject_id=workflow.workflow_id,
                release_ref=workflow.release_ref,
                release_sha256=workflow.release_sha256,
                state=ReleaseAdmissionState.ACTIVE,
                evidence_members=(),
            )
        )
    catalog = RuntimeReleaseRegistry()
    catalog.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(*schemas, input_schema, output_schema),
            behavior_policies=(behavior,),
            evaluation_policies=(evaluation,),
            retry_policies=(retry,),
            modules=(module,),
            workflows=(workflow,),
            admission_intents=tuple(admissions),
        )
    )
    return catalog, workflow


def _snapshot() -> ExternalEventExecutionSnapshot:
    return ExternalEventExecutionSnapshot(
        backend_id="temporal_backend_001",
        backend_execution_id="backend-execution-001",
        workflow_execution_id="workflow_execution_001",
        workflow_id="research_external_event",
        workflow_contract_version="1.0.0",
        execution_release_ref="execution-release:event@1",
        runtime_release_ref="runtime-release:agent-runtime@1",
        graph_sha256="c" * 64,
        domain_state_id="waiting_state",
        runtime_status_id="waiting",
        transition_sequence=0,
        retry_sequence=0,
        terminal=False,
        wait_policy_ref="wait-policy:external-approval@1",
    )


def _token(
    *,
    transition_sequence: int = 0,
    domain_state_id: str = "waiting_state",
    runtime_status_id: str = "waiting",
) -> ExecutionSnapshotToken:
    return ExecutionSnapshotToken.build(
        snapshot_id=f"snapshot_{transition_sequence:03d}",
        snapshot_ref=f"runtime-snapshot:snapshot-{transition_sequence:03d}",
        workflow_execution_id="workflow_execution_001",
        transition_sequence=transition_sequence,
        domain_state_id=domain_state_id,
        runtime_status_id=runtime_status_id,
    )


def _binding(workflow: WorkflowRelease) -> ExecutionAuthorizationBinding:
    return ExecutionAuthorizationBinding.build(
        binding_id="execution_binding_001",
        binding_ref="runtime-authorization:execution-binding-001",
        workflow_execution_id="workflow_execution_001",
        tenant_id="tenant_synthetic_001",
        cell_id="cell_synthetic_001",
        initiating_principal_id="principal_human_001",
        execution_principal_id="principal_runtime_001",
        workflow_release_ref=workflow.release_ref,
        workflow_release_sha256=workflow.release_sha256,
        authorization_decision_ref="product-authorization:workflow-decision-001",
        authorization_decision_sha256="d" * 64,
        execution_principal_delegation_ref="product-authorization:delegation-001",
        execution_principal_delegation_sha256="e" * 64,
        execution_input_package_ref="execution-input:package-001",
        execution_input_package_sha256="f" * 64,
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
        evidence_id="event_status_001",
        evidence_ref="runtime-authorization:event-status-001",
        binding_ref=binding.binding_ref,
        binding_sha256=binding.binding_sha256,
        status=status,
        control_fence_ref="runtime-control:event-fence-001",
        control_fence_sha256="1" * 64,
        control_fence_status=fence,
        recorded_at_utc=NOW,
    )


def _context() -> TrustedRequestContext:
    return TrustedRequestContext(
        actor_principal_id="principal_human_001",
        tenant_id="tenant_synthetic_001",
        cell_id="cell_synthetic_001",
        authenticated_session_ref="identity-session:session-001",
    )


def _request(token: ExecutionSnapshotToken) -> ExternalEventIngressRequest:
    return ExternalEventIngressRequest.build(
        ingress_request_id="event_ingress_request_001",
        request_ref="runtime-event-request:request-001",
        idempotency_key="event_idempotency_001",
        workflow_execution_id="workflow_execution_001",
        expected_snapshot_ref=token.snapshot_ref,
        expected_snapshot_sha256=token.snapshot_sha256,
        expected_transition_sequence=token.transition_sequence,
        expected_domain_state=token.domain_state_id,
        requested_event_type="approval_received",
        decision_artifact_ref="artifact:approval-decision-001",
        decision_artifact_sha256="2" * 64,
    )


def _authorization(
    request: ExternalEventIngressRequest,
    binding: ExecutionAuthorizationBinding,
    *,
    effect: AuthorizationEffect = AuthorizationEffect.ALLOW,
) -> ExternalActionAuthorizationEvidence:
    return ExternalActionAuthorizationEvidence(
        effect=effect,
        request_ref="product-authorization:external-request-001",
        request_sha256="3" * 64,
        decision_ref="product-authorization:external-decision-001",
        decision_sha256="4" * 64,
        actor_principal_id="principal_human_001",
        tenant_id="tenant_synthetic_001",
        cell_id="cell_synthetic_001",
        workflow_execution_id=request.workflow_execution_id,
        requested_event_type=request.requested_event_type,
        decision_artifact_ref=request.decision_artifact_ref,
        decision_artifact_sha256=request.decision_artifact_sha256,
        execution_authorization_binding_ref=binding.binding_ref,
        execution_authorization_binding_sha256=binding.binding_sha256,
        effective_at_utc="2026-08-05T11:30:00Z",
        expiry_at_utc="2026-08-05T12:30:00Z",
    )


def _prepare(
    ingress: InMemoryExternalEventIngress,
    *,
    release_registry: RuntimeReleaseRegistry,
    workflow: WorkflowRelease,
    binding: ExecutionAuthorizationBinding,
    snapshot: ExternalEventExecutionSnapshot,
    token: ExecutionSnapshotToken,
    request: ExternalEventIngressRequest,
    authorization: ExternalActionAuthorizationEvidence,
):
    return ingress.prepare_ingress(
        request=request,
        trusted_context=_context(),
        authorization=authorization,
        execution_binding=binding,
        status_evidence=_status(binding),
        snapshot=snapshot,
        snapshot_token=token,
        release_registry=release_registry,
        workflow_release_ref=workflow.release_ref,
        workflow_release_sha256=workflow.release_sha256,
        claim_at_utc=NOW,
    )


def test_ingress_application_and_acknowledgement_are_exact_and_idempotent() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    snapshot = _snapshot()
    token = _token()
    request = _request(token)
    authorization = _authorization(request, binding)
    ingress_service = InMemoryExternalEventIngress()

    first_ingress = _prepare(
        ingress_service,
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=snapshot,
        token=token,
        request=request,
        authorization=authorization,
    )
    second_ingress = _prepare(
        ingress_service,
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=snapshot,
        token=token,
        request=request,
        authorization=authorization,
    )
    assert first_ingress == second_ingress
    assert first_ingress.event.target_domain_state == "next_state"

    application = ingress_service.apply(
        ingress=first_ingress,
        current_snapshot=snapshot,
        current_snapshot_token=token,
        current_status_evidence=_status(binding),
        claim_at_utc=NOW,
    )
    assert application == ingress_service.apply(
        ingress=first_ingress,
        current_snapshot=snapshot,
        current_snapshot_token=token,
        current_status_evidence=_status(binding),
        claim_at_utc=NOW,
    )

    acknowledgement = ingress_service.acknowledge(
        ingress=first_ingress,
        application=application,
        post_snapshot_token=_token(
            transition_sequence=1,
            domain_state_id="next_state",
            runtime_status_id="running",
        ),
        backend_acknowledgement_ref="backend-ack:event-001",
        backend_acknowledgement_sha256="5" * 64,
    )
    assert acknowledgement.application_record_ref == application.application_record_ref
    assert acknowledgement.event_ref == first_ingress.event.event_ref


def test_ingress_replay_returns_committed_record_across_retry_timestamps() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    snapshot = _snapshot()
    token = _token()
    request = _request(token)
    authorization = _authorization(request, binding)
    service = InMemoryExternalEventIngress()

    first = _prepare(
        service,
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=snapshot,
        token=token,
        request=request,
        authorization=authorization,
    )
    replay = service.prepare_ingress(
        request=request,
        trusted_context=_context(),
        authorization=authorization,
        execution_binding=binding,
        status_evidence=_status(binding),
        snapshot=snapshot,
        snapshot_token=token,
        release_registry=catalog,
        workflow_release_ref=workflow.release_ref,
        workflow_release_sha256=workflow.release_sha256,
        claim_at_utc="2026-08-05T12:00:01Z",
    )

    assert replay is first
    assert replay.recorded_at_utc == NOW


def test_ingress_replay_converges_after_execution_leaves_waiting() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    token = _token()
    request = _request(token)
    authorization = _authorization(request, binding)
    service = InMemoryExternalEventIngress()

    first = _prepare(
        service,
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=_snapshot(),
        token=token,
        request=request,
        authorization=authorization,
    )
    replay = service.prepare_ingress(
        request=request,
        trusted_context=_context(),
        authorization=authorization,
        execution_binding=binding,
        status_evidence=_status(binding),
        snapshot=replace(
            _snapshot(),
            domain_state_id="next_state",
            runtime_status_id="running",
            transition_sequence=1,
        ),
        snapshot_token=_token(
            transition_sequence=1,
            domain_state_id="next_state",
            runtime_status_id="running",
        ),
        release_registry=catalog,
        workflow_release_ref=workflow.release_ref,
        workflow_release_sha256=workflow.release_sha256,
        claim_at_utc="2026-08-05T12:00:02Z",
    )

    assert replay is first

    mutated_request = ExternalEventIngressRequest.build(
        ingress_request_id="event_ingress_request_001",
        request_ref="runtime-event-request:request-001",
        idempotency_key="event_idempotency_001",
        workflow_execution_id="workflow_execution_001",
        expected_snapshot_ref=token.snapshot_ref,
        expected_snapshot_sha256=token.snapshot_sha256,
        expected_transition_sequence=token.transition_sequence,
        expected_domain_state=token.domain_state_id,
        requested_event_type="approval_received",
        decision_artifact_ref="artifact:approval-decision-002",
        decision_artifact_sha256="2" * 64,
    )
    with pytest.raises(ValueError, match="ingress idempotency conflict"):
        service.prepare_ingress(
            request=mutated_request,
            trusted_context=_context(),
            authorization=authorization,
            execution_binding=binding,
            status_evidence=_status(binding),
            snapshot=_snapshot(),
            snapshot_token=token,
            release_registry=catalog,
            workflow_release_ref=workflow.release_ref,
            workflow_release_sha256=workflow.release_sha256,
            claim_at_utc="2026-08-05T12:00:03Z",
        )


def test_ingress_replay_revalidates_authority_and_exact_lineage() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    snapshot = _snapshot()
    token = _token()
    request = _request(token)
    authorization = _authorization(request, binding)
    service = InMemoryExternalEventIngress()
    _prepare(
        service,
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=snapshot,
        token=token,
        request=request,
        authorization=authorization,
    )
    replay_arguments = {
        "request": request,
        "trusted_context": _context(),
        "authorization": authorization,
        "execution_binding": binding,
        "status_evidence": _status(binding),
        "snapshot": snapshot,
        "snapshot_token": token,
        "release_registry": catalog,
        "workflow_release_ref": workflow.release_ref,
        "workflow_release_sha256": workflow.release_sha256,
        "claim_at_utc": NOW,
    }

    with pytest.raises(ValueError, match="trusted identity mismatch"):
        service.prepare_ingress(
            **(
                replay_arguments
                | {
                    "trusted_context": replace(
                        _context(), tenant_id="tenant_other_001"
                    )
                }
            )
        )
    with pytest.raises(PermissionError, match="denied"):
        service.prepare_ingress(
            **(
                replay_arguments
                | {
                    "authorization": replace(
                        authorization, effect=AuthorizationEffect.DENY
                    )
                }
            )
        )
    closed_fence_replay = service.prepare_ingress(
        **(
            replay_arguments
            | {
                "status_evidence": _status(
                    binding,
                    status=ExecutionAuthorizationStatus.INVALIDATED,
                    fence=ExecutionControlFenceStatus.FENCED,
                )
            }
        )
    )
    with pytest.raises(ValueError, match="ingress idempotency conflict"):
        service.prepare_ingress(
            **(
                replay_arguments
                | {
                    "authorization": replace(
                        authorization,
                        decision_ref=(
                            "product-authorization:external-decision-002"
                        ),
                        decision_sha256="8" * 64,
                    )
                }
            )
        )
    expired_window_replay = service.prepare_ingress(
        **(
            replay_arguments
            | {"claim_at_utc": "2026-08-05T12:30:00Z"}
        )
    )
    unavailable_release_replay = service.prepare_ingress(
        **(
            replay_arguments
            | {"release_registry": RuntimeReleaseRegistry()}
        )
    )
    assert closed_fence_replay is expired_window_replay is unavailable_release_replay


def test_application_replay_converges_after_wait_transition_applied() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    snapshot = _snapshot()
    token = _token()
    request = _request(token)
    authorization = _authorization(request, binding)
    service = InMemoryExternalEventIngress()

    ingress_record = _prepare(
        service,
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=snapshot,
        token=token,
        request=request,
        authorization=authorization,
    )
    committed = service.apply(
        ingress=ingress_record,
        current_snapshot=snapshot,
        current_snapshot_token=token,
        current_status_evidence=_status(binding),
        claim_at_utc=NOW,
    )

    replay = service.apply(
        ingress=ingress_record,
        current_snapshot=replace(
            snapshot,
            domain_state_id="next_state",
            runtime_status_id="running",
            transition_sequence=1,
        ),
        current_snapshot_token=_token(
            transition_sequence=1,
            domain_state_id="next_state",
            runtime_status_id="running",
        ),
        current_status_evidence=_status(binding),
        claim_at_utc="2026-08-05T12:00:03Z",
    )

    assert replay is committed


def test_terminal_external_event_targets_runtime_completed_state() -> None:
    catalog, workflow = _catalog(approval_is_terminal=True)
    binding = _binding(workflow)
    token = _token()
    request = _request(token)

    ingress = _prepare(
        InMemoryExternalEventIngress(),
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=_snapshot(),
        token=token,
        request=request,
        authorization=_authorization(request, binding),
    )

    assert ingress.event.target_domain_state == "completed"


def test_application_rejects_snapshot_that_does_not_match_its_token() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    snapshot = _snapshot()
    token = _token()
    request = _request(token)
    service = InMemoryExternalEventIngress()
    ingress = _prepare(
        service,
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=snapshot,
        token=token,
        request=request,
        authorization=_authorization(request, binding),
    )

    with pytest.raises(PermissionError, match="stale_external_action"):
        service.apply(
            ingress=ingress,
            current_snapshot=replace(
                snapshot,
                workflow_execution_id="workflow_execution_other",
            ),
            current_snapshot_token=token,
            current_status_evidence=_status(binding),
            claim_at_utc=NOW,
        )


def test_deny_or_unadmitted_workflow_produces_no_ingress_record() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    token = _token()
    request = _request(token)

    with pytest.raises(PermissionError, match="denied"):
        _prepare(
            InMemoryExternalEventIngress(),
            release_registry=catalog,
            workflow=workflow,
            binding=binding,
            snapshot=_snapshot(),
            token=token,
            request=request,
            authorization=_authorization(
                request,
                binding,
                effect=AuthorizationEffect.DENY,
            ),
        )

    candidate_catalog, candidate_workflow = _catalog(
        workflow_state=ReleaseAdmissionState.CANDIDATE
    )
    candidate_binding = _binding(candidate_workflow)
    with pytest.raises(PermissionError, match="not admitted"):
        _prepare(
            InMemoryExternalEventIngress(),
            release_registry=candidate_catalog,
            workflow=candidate_workflow,
            binding=candidate_binding,
            snapshot=_snapshot(),
            token=token,
            request=request,
            authorization=_authorization(request, candidate_binding),
        )


def test_stale_snapshot_and_unknown_event_fail_before_ingress_commit() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    token = _token()
    request = _request(token)

    with pytest.raises(PermissionError, match="stale_external_action"):
        _prepare(
            InMemoryExternalEventIngress(),
            release_registry=catalog,
            workflow=workflow,
            binding=binding,
            snapshot=_snapshot(),
            token=_token(transition_sequence=1),
            request=request,
            authorization=_authorization(request, binding),
        )

    unknown_request = ExternalEventIngressRequest.build(
        **{
            key: value
            for key, value in request.__dict__.items()
            if key != "request_sha256"
        }
        | {"requested_event_type": "unknown_event"},
    )
    with pytest.raises(ValueError, match="Product decision mismatch"):
        _prepare(
            InMemoryExternalEventIngress(),
            release_registry=catalog,
            workflow=workflow,
            binding=binding,
            snapshot=_snapshot(),
            token=token,
            request=unknown_request,
            authorization=_authorization(request, binding),
        )


def test_identity_and_decision_artifact_substitution_fail_closed() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    token = _token()
    request = _request(token)
    authorization = _authorization(request, binding)

    with pytest.raises(ValueError, match="trusted identity mismatch"):
        InMemoryExternalEventIngress().prepare_ingress(
            request=request,
            trusted_context=replace(_context(), tenant_id="tenant_other_001"),
            authorization=authorization,
            execution_binding=binding,
            status_evidence=_status(binding),
            snapshot=_snapshot(),
            snapshot_token=token,
            release_registry=catalog,
            workflow_release_ref=workflow.release_ref,
            workflow_release_sha256=workflow.release_sha256,
            claim_at_utc=NOW,
        )

    with pytest.raises(ValueError, match="Product decision mismatch"):
        _prepare(
            InMemoryExternalEventIngress(),
            release_registry=catalog,
            workflow=workflow,
            binding=binding,
            snapshot=_snapshot(),
            token=token,
            request=request,
            authorization=replace(
                authorization,
                decision_artifact_sha256="6" * 64,
            ),
        )


def test_invalidation_between_ingress_and_application_blocks_transition() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    snapshot = _snapshot()
    token = _token()
    request = _request(token)
    service = InMemoryExternalEventIngress()
    ingress = _prepare(
        service,
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=snapshot,
        token=token,
        request=request,
        authorization=_authorization(request, binding),
    )

    with pytest.raises(PermissionError, match="no longer effective"):
        service.apply(
            ingress=ingress,
            current_snapshot=snapshot,
            current_snapshot_token=token,
            current_status_evidence=_status(
                binding,
                status=ExecutionAuthorizationStatus.INVALIDATED,
                fence=ExecutionControlFenceStatus.FENCED,
            ),
            claim_at_utc=NOW,
        )


def test_ingress_and_application_hash_tampering_is_detected() -> None:
    catalog, workflow = _catalog()
    binding = _binding(workflow)
    snapshot = _snapshot()
    token = _token()
    request = _request(token)
    service = InMemoryExternalEventIngress()
    ingress = _prepare(
        service,
        release_registry=catalog,
        workflow=workflow,
        binding=binding,
        snapshot=snapshot,
        token=token,
        request=request,
        authorization=_authorization(request, binding),
    )
    with pytest.raises(ValueError, match="ingress record hash mismatch"):
        replace(ingress, recorded_at_utc="2026-08-05T12:00:01Z").validate()

    application = service.apply(
        ingress=ingress,
        current_snapshot=snapshot,
        current_snapshot_token=token,
        current_status_evidence=_status(binding),
        claim_at_utc=NOW,
    )
    with pytest.raises(ValueError, match="application record hash mismatch"):
        replace(application, recorded_at_utc="2026-08-05T12:00:01Z").validate()
