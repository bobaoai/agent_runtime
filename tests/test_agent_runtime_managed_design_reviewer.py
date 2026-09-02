from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from agent_runtime import ModuleReviewer
from agent_runtime.contracts.execution_authorization_definition import (
    ExecutionAuthorizationContextEnvelope,
    ExecutionAuthorizationContextState,
    GatewayDecisionEffect,
    OperationAuthorizationQuery,
    ProductAuthorizationContextStatus,
    ProductOperationDecision,
)
from agent_runtime.contracts.execution_module_definition import (
    ModuleExecutionRequest,
    ModuleInputBinding,
    ModuleVariantRequest,
)
from agent_runtime.contracts.invocation_adapter_definition import (
    AdapterContextResult,
    AgentExecutionAdapterDescriptor,
    AgentExecutionResult,
    AuthorizedAgentExecutionRequest,
    OutputSubmission,
)
from agent_runtime.contracts.registry_release_definition import (
    ModuleExecutionPurpose,
)
from agent_runtime.execution.execution_authorization_coordination import (
    ExecutionAuthorizationController,
    InMemoryExecutionAuthorizationLedger,
)
from agent_runtime.execution.execution_content_staging import (
    InMemoryCellArtifactStore,
)
from agent_runtime.execution.execution_module_invocation import (
    AgentExecutionAdapterRegistry,
    ModuleExecutionAuthority,
    isolated_execution_scope_id,
    run_module,
)
from agent_runtime.foundation.foundation_json_schema_validation import (
    validate_json_document_against_schema,
)
from agent_runtime.invocation.invocation_claude_module_invocation import (
    ClaudeAgentSdkInlineModuleExecutor,
)
from agent_runtime.invocation.invocation_prompt_assembly import (
    NATIVE_STRUCTURED_OUTPUT,
    build_inline_provider_prompt,
)
from agent_runtime.ledger.ledger_lineage_recording import (
    InMemoryModuleExecutionLedger,
)
from agent_runtime.registry import (
    BehaviorPolicyReleaseCandidate,
    EvaluationPolicyReleaseCandidate,
    ExecutionProfileReleaseSpec,
    RetryPolicyReleaseCandidate,
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_execution_profile_release,
    compile_retry_policy_release,
    runtime_owned_policy_schema_assets,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_ID = "design_contract_reviewer"
SKILL_ID = "the-design-authoring"
TEST_TIME = "2026-09-01T21:30:00Z"
RUN_PROVIDER_INTEGRATION = os.environ.get("RUN_PROVIDER_INTEGRATION") == "1"


def _required_check_ids() -> tuple[str, ...]:
    source = ModuleReviewer.from_registration(
        REPO_ROOT,
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    ).source
    input_schema = json.loads(source.input_schema_document)
    return tuple(
        item["const"]
        for item in input_schema["properties"]["required_check_ids"][
            "prefixItems"
        ]
    )


def _review_input() -> dict[str, object]:
    candidate = REPO_ROOT / "designDoc/agent_runtime_11_agent_capability_verification.md"
    context_specs = (
        (
            "the_design_doc_management",
            "governing_contract",
            "designDoc/the_design_doc_management.md",
        ),
        ("the_charter", "charter", "designDoc/the_charter.md"),
        ("the_agent_runtime", "dependency_contract", "designDoc/the_agent_runtime.md"),
        (
            "the_software_delivery",
            "dependency_contract",
            "designDoc/the_software_delivery.md",
        ),
        (
            "agent_runtime_00_execution_charter",
            "parent_contract",
            "designDoc/agent_runtime_00_execution_charter.md",
        ),
    )
    contexts = []
    for document_id, role, relative_path in context_specs:
        path = REPO_ROOT / relative_path
        contexts.append(
            {
                "document_id": document_id,
                "context_role": role,
                "owner_ref": relative_path,
                "title": path.stem,
                "body": path.read_text(encoding="utf-8"),
            }
        )
    for index in range(1, 11):
        for path in sorted((REPO_ROOT / "designDoc").glob(f"agent_runtime_{index:02d}_*.md")):
            contexts.append(
                {
                    "document_id": path.stem,
                    "context_role": "peer_contract",
                    "owner_ref": f"designDoc/{path.name}",
                    "title": path.stem,
                    "body": path.read_text(encoding="utf-8"),
                }
            )
    return {
        "schema_version": "design_contract_reviewer_input_v6",
        "module_id": MODULE_ID,
        "review_request": {
            "reviewed_subject_kind": "t2_design",
            "review_purpose": "contract_admission",
            "candidate_revision_class": "new_document",
            "intended_result": (
                "Define one complete Agent capability verification contract and "
                "canonical Example whose executable test cases also serve as the "
                "operator runbook."
            ),
            "architecture_change_summary": (
                "Add one verification-only T2 and delegate it from the Runtime T1; "
                "do not change production Runtime behavior."
            ),
        },
        "candidate_documents": [
            {
                "document_id": "agent_runtime_11_agent_capability_verification",
                "layer": "T2",
                "title": "Agent Capability Verification",
                "owner_ref": "designDoc/agent_runtime_11_agent_capability_verification.md",
                "parent_ref": "designDoc/agent_runtime_00_execution_charter.md",
                "owned_object": "Agent Capability Verification Contract",
                "body": candidate.read_text(encoding="utf-8"),
            }
        ],
        "context_documents": contexts,
        "required_check_ids": list(_required_check_ids()),
        "prior_findings": [
            {
                "finding_id": "f1_peer_conformance_overlap",
                "disposition": "claimed_fixed",
                "body": "Capability rows name the owning peer T2 as the semantic contract; executable case failures retain the peer owner and error identity without inventing peer conformance results.",
            },
            {
                "finding_id": "f2_test_binding_code_truth",
                "disposition": "claimed_fixed",
                "body": "Mutable paths, test counts and case rows moved out of Design; Code Projection and generated inspection own them.",
            },
            {
                "finding_id": "f3_required_gate_authority",
                "disposition": "claimed_fixed",
                "body": "The run input now carries Software Delivery required_environment_gate_ids decision evidence separately from host availability inspection.",
            },
            {
                "finding_id": "f4_review_completion_conflation",
                "disposition": "claimed_fixed",
                "body": "Verification-suite implementation admission is one release decision; ordinary candidate verification consumes its immutable ref/hash.",
            },
            {
                "finding_id": "f5_capability_owner_routing",
                "disposition": "claimed_fixed",
                "body": "Every capability row names its owning T2 and the exact subject-bound executable evidence consumed by aggregate verification.",
            },
            {
                "finding_id": "n1_reader_gain_host_integrator",
                "disposition": "claimed_fixed",
                "body": "Reader persona now includes Operator and Example User, and Host Integrator gain is stated as a post-reading judgment capability.",
            },
            {
                "finding_id": "f1_run_input_evidence_closure",
                "disposition": "claimed_fixed",
                "body": "The sole run interface carries required case IDs, environment decisions and the one existing 05 peer result, and returns the subject-bound result directly.",
            },
            {
                "finding_id": "f2_capability_evidence_source",
                "disposition": "claimed_fixed",
                "body": "Sections 4.1-4.5 use subject-bound executable cases against exact owning Design contracts; section 4.6 fixes environment mode and the existing 05 peer-result mode.",
            },
            {
                "finding_id": "f3_environment_and_runner_binding",
                "disposition": "claimed_fixed",
                "body": "Design now fixes environment-gate classes and runner contracts while Code Projection owns selected products, commands and paths.",
            },
            {
                "finding_id": "n1_suite_admission_review_authority",
                "disposition": "claimed_fixed",
                "body": "Software Delivery is now the explicit owner-qualified authority for suite implementation review and release admission.",
            },
            {
                "finding_id": "f1_inspection_persistence_parity_owner",
                "disposition": "claimed_fixed",
                "body": "Live Inspection now verifies one 06 snapshot against exact source facts owned by 01 Registry and 04 Ledger; it no longer assigns persistent storage ownership to Inspection.",
            },
            {
                "finding_id": "f2_peer_evidence_subject_binding",
                "disposition": "claimed_fixed",
                "body": "Completion now requires every counted case, environment and referenced peer result to bind the same exact Runtime subject and required dependency/configuration closure.",
            },
            {
                "finding_id": "f3_peer_result_producer_unresolved",
                "disposition": "claimed_fixed",
                "body": "The T2 no longer requires invented peer result producers: sections 4.1-4.5 use executable case evidence, while only the already-declared 05 conformance result is referenced.",
            },
            {
                "finding_id": "n1_interface_effect_scope_wording",
                "disposition": "claimed_fixed",
                "body": "The run interface Effects cell now names test-identity temporary resources as well as verification evidence and excludes production mutation.",
            },
            {
                "finding_id": "f1_local_case_failure_identity",
                "disposition": "claimed_fixed",
                "body": "The Flowmap now separates a product capability failure, which preserves the Owning Design error and owner, from a verification-harness failure, which alone uses AGENT_CAPABILITY_TEST_FAILED.",
            },
            {
                "finding_id": "n1_capsule_input_closure",
                "disposition": "claimed_fixed",
                "body": "Intent Capsule inputs now name the admitted suite, Software Delivery required-gate decision, host availability inspection and exact 05 conformance result used by the public run interface.",
            },
            {
                "finding_id": "f1_environment_dependent_capability_completion",
                "disposition": "claimed_fixed",
                "body": "Persistent Inspection moved into section 4.6; when Software Delivery requires that environment gate it must be passed, while unavailable remains not_run and aggregate verification incomplete.",
            },
            {
                "finding_id": "f2_live_inspection_evidence_class_unassigned",
                "disposition": "claimed_fixed",
                "body": "Persistent Inspection is now unambiguously an environment_gate executed by the code-owned integration runner against the selected persistent-store binding.",
            },
            {
                "finding_id": "n1_flowmap_gate_failure_routing",
                "disposition": "claimed_fixed",
                "body": "The Flowmap now exposes failed or unavailable persistent-store, durable-backend and provider gates before aggregate evidence closure.",
            },
            {
                "finding_id": "f1_gateway_environment_gate_unresolved",
                "disposition": "claimed_fixed",
                "body": "Gateway remains the executable Authorized Gateway Read capability in section 4.2 and is no longer named as a separate environment-gate class anywhere in the T2.",
            },
            {
                "finding_id": "f2_gateway_gate_implementation_decision",
                "disposition": "claimed_fixed",
                "body": "The environment-gate set is now uniquely persistent store, durable backend and live provider adapter; implementation need not invent a Gateway gate.",
            },
            {
                "finding_id": "n1_capsule_hook_owner_qualification",
                "disposition": "claimed_fixed",
                "body": "The capsule now names the exact 05 StandaloneReleaseConformanceResult rather than implying this T2 owns a clean-package gate.",
            },
            {
                "finding_id": "f1_optional_environment_gate_result_state",
                "disposition": "claimed_fixed",
                "body": "An optional environment gate may remain not_run; if executed, its true passed or failed result is recorded but excluded from aggregate completion.",
            },
            {
                "finding_id": "f1_run_input_failure_closure",
                "disposition": "claimed_fixed",
                "body": "The existing AGENT_CAPABILITY_COVERAGE_INCOMPLETE now covers any required input ref/hash that cannot resolve, mismatches, or names an unadmitted verification suite, and the Flowmap routes input resolution failure to it.",
            },
            {
                "finding_id": "f2_run_input_failure_implementability",
                "disposition": "claimed_fixed",
                "body": "Required-input validation failure has one result and owner: coverage incomplete under this T2; callers provide the exact admitted closure and rerun rather than inventing a mapping.",
            },
        ],
    }


def _valid_review_output() -> dict[str, object]:
    return {
        "verdict": "passed",
        "design_judgment": {
            "intended_result": "The exact verification result is explicit.",
            "reader_gain": "Readers can run the Example and interpret each gate.",
            "prose_and_meaning_preservation": "Prose preserves the governing meaning.",
            "authority_structure": "Verification does not own Runtime behavior.",
            "layer_content_fit": "The bounded test contract fits T2.",
            "boundary_coherence": "Test, runbook, and production owners remain separate.",
            "inheritance_and_dependencies": "Parent and peer dependencies are explicit.",
            "intent_code_separation": "Stable intent and current observations are separate.",
            "flow_interface_error": "Flow, interfaces, and errors close.",
            "failure_completion_recovery": "Failure and completion are explicit.",
            "implementability": "The Example can be implemented without redesign.",
        },
        "check_results": [
            {
                "check_id": check_id,
                "disposition": "passed",
                "assessment": f"{check_id} passed on the exact candidate.",
                "finding_ids": [],
            }
            for check_id in _required_check_ids()
        ],
        "findings": [],
        "safe_next_step": "Implement the executable Example and runbook projection.",
    }


def _compiled_reviewer():
    reviewer = ModuleReviewer.from_registration(
        REPO_ROOT,
        skill_id=SKILL_ID,
        module_id=MODULE_ID,
    )
    behavior = compile_behavior_policy_release(
        BehaviorPolicyReleaseCandidate(
            policy_id="workflow_execution_isolated",
            policy_version="v1",
            context_isolation="workflow_execution_isolated",
        )
    )
    evaluation = compile_evaluation_policy_release(
        EvaluationPolicyReleaseCandidate(
            policy_id="module_candidate",
            policy_version="v1",
            evaluation_mode="module_candidate",
        )
    )
    retry = compile_retry_policy_release(
        RetryPolicyReleaseCandidate(
            policy_id="bounded_candidate",
            policy_version="v1",
            max_attempts=3,
        )
    )
    profile = compile_execution_profile_release(
        ExecutionProfileReleaseSpec(
            execution_profile_id="profile_design_contract_reviewer_opus_5_xhigh_v1",
            executor_adapter_id="claude_agent_sdk_inline_executor",
            executor_adapter_revision="v1",
            transport_kind="claude_agent_sdk",
            provider_id="anthropic",
            model_id="claude-opus-5",
            reasoning_profile="xhigh",
            execution_mode="tool_free",
            semantic_input_delivery_mode="inline",
            attempt_workspace_policy="none",
            gateway_access_reasons=(),
            output_constraint_mode=NATIVE_STRUCTURED_OUTPUT,
            tool_policy=(),
            network_policy="denied",
            timeout_seconds=900,
            release_version="v1",
        )
    )
    exported = reviewer.export(
        module_version="v1",
        behavior_policy=behavior,
        evaluation_policy=evaluation,
        retry_policy=retry,
        execution_profile=profile,
    )
    assert exported.execution_variant is not None
    return reviewer, exported


def _registered_reviewer():
    reviewer, exported = _compiled_reviewer()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(exported.origin_bundle)
    variant_schema = next(
        asset
        for asset in runtime_owned_policy_schema_assets()
        if asset.release_ref == exported.execution_variant.policy_schema_ref
    )
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(variant_schema,),
            execution_profiles=(exported.execution_profile,),
            execution_variant_policies=(exported.execution_variant,),
        )
    )
    return reviewer, exported, registry


def _execution_input(exported, artifact_host: InMemoryCellArtifactStore):
    payload = _review_input()
    input_schema = json.loads(exported.source.input_schema_document)
    validate_json_document_against_schema(payload, input_schema)
    content = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    stored = artifact_host.put_bytes(
        artifact_kind_id="design_contract_review_input",
        schema_version="v6",
        schema_ref=exported.module_release.input_schema_ref,
        schema_sha256=exported.module_release.input_schema_sha256,
        media_type="application/json",
        content=content,
        idempotency_key="agent_capability_design_review_input",
        logical_name="design_review_input",
    )
    binding = ModuleInputBinding(
        logical_name="design_review_input",
        input_ref=stored.artifact_ref,
        input_sha256=stored.artifact_sha256,
        schema_ref=exported.module_release.input_schema_ref,
        schema_sha256=exported.module_release.input_schema_sha256,
        media_type="application/json",
    )
    prompt = build_inline_provider_prompt(
        compiled_static_body=exported.compiled.prompt_bundle.compiled_static_body,
        execution_specific_instructions="Review only the exact typed candidate input.",
        inputs=((binding, content),),
        output_constraint_mode=NATIVE_STRUCTURED_OUTPUT,
    )
    prompt_ref = artifact_host.put_bytes(
        artifact_kind_id="prompt_envelope",
        schema_version="prompt_envelope_v1",
        schema_ref="schema:prompt_envelope@v1",
        schema_sha256="1" * 64,
        media_type="text/plain",
        content=prompt.encode("utf-8"),
        idempotency_key="agent_capability_design_review_prompt",
    )
    request = ModuleExecutionRequest.build(
        request_id="request_agent_capability_design_review",
        purpose=ModuleExecutionPurpose.EVALUATION,
        module_release_ref=exported.module_release.release_ref,
        module_release_sha256=exported.module_release.release_sha256,
        isolated_scope_ref="scope-ref:agent-capability-design-review",
        isolated_scope_sha256="2" * 64,
        input_package_ref=stored.artifact_ref,
        input_package_sha256=stored.artifact_sha256,
        inputs=(binding,),
        variants=(
            ModuleVariantRequest(
                arm_key="opus_5_xhigh",
                replicate_index=0,
                execution_profile_ref=exported.execution_profile.release_ref,
                execution_profile_sha256=exported.execution_profile.release_sha256,
                prompt_envelope_ref=prompt_ref.artifact_ref,
                prompt_envelope_sha256=prompt_ref.artifact_sha256,
            ),
        ),
        idempotency_key="agent_capability_design_review",
    )
    return request


class _ProductAuthorityDouble:
    def __init__(self, envelope: ExecutionAuthorizationContextEnvelope) -> None:
        self.envelope = envelope

    def validate_execution_context(
        self,
        context_id: str,
        tenant_id: str,
        observed_at_utc: str,
    ) -> ProductAuthorizationContextStatus:
        assert context_id == self.envelope.context_id
        assert tenant_id == self.envelope.tenant_id
        return ProductAuthorizationContextStatus.build(
            status_ref="product-status:managed-design-review",
            context_id=self.envelope.context_id,
            context_ref=self.envelope.context_ref,
            context_sha256=self.envelope.context_sha256,
            state=ExecutionAuthorizationContextState.EFFECTIVE,
            reason_code="context_effective",
            observed_at_utc=observed_at_utc,
        )

    def authorize_operation(
        self,
        query: OperationAuthorizationQuery,
    ) -> ProductOperationDecision:
        return ProductOperationDecision(
            query_id=query.query_id,
            query_sha256=query.query_sha256,
            decision_ref=f"product-decision:{query.query_id}",
            decision_sha256=hashlib.sha256(
                query.query_sha256.encode("utf-8")
            ).hexdigest(),
            effect=GatewayDecisionEffect.ALLOW,
            reason_code="operation_allowed",
            observed_at_utc=query.observed_at_utc,
        )


def _authority(registry, request):
    scope = isolated_execution_scope_id(
        request.isolated_scope_ref,
        request.isolated_scope_sha256,
    )
    envelope = ExecutionAuthorizationContextEnvelope.build(
        context_id="context_managed_design_review",
        context_ref="product-context:managed-design-review",
        workflow_execution_id=scope,
        workflow_release_id="module_isolated_evaluation",
        principal_id="principal_runtime_verification",
        actor_workload_id="workload_design_contract_reviewer",
        tenant_id="tenant_runtime_verification",
        cell_id="cell_managed_design_review",
        authorization_decision_ref="product-decision:managed-design-review",
        catalog_release_ref="catalog:managed-design-review@v1",
        input_scope_refs=(request.input_package_ref,),
        effective_at_utc="2026-01-01T00:00:00Z",
        expiry_at_utc="2027-01-01T00:00:00Z",
    )
    product = _ProductAuthorityDouble(envelope)
    controller = ExecutionAuthorizationController(
        client=product,
        ledger=InMemoryExecutionAuthorizationLedger(),
        module_release_client=registry,
    )
    admission = controller.bind_execution_context(
        envelope=envelope,
        expected_workflow_execution_id=scope,
        expected_workflow_release_id="module_isolated_evaluation",
        expected_principal_id="principal_runtime_verification",
        expected_actor_workload_id="workload_design_contract_reviewer",
        expected_tenant_id="tenant_runtime_verification",
        expected_cell_id="cell_managed_design_review",
        execution_input_package_ref=request.input_package_ref,
        execution_input_package_sha256=request.input_package_sha256,
        observed_at_utc=TEST_TIME,
    )
    return ModuleExecutionAuthority(
        controller=controller,
        binding=admission.binding,
        authorization_client=product,
        enforcing_gateway_id="agent_runtime_module_kernel",
        environment_id="local_verification",
    )


class _StaticDesignReviewerAdapter:
    def __init__(self, registry, artifact_host) -> None:
        self.registry = registry
        self.artifact_host = artifact_host

    @property
    def descriptor(self) -> AgentExecutionAdapterDescriptor:
        return AgentExecutionAdapterDescriptor(
            adapter_contract_version="v1",
            adapter_id="claude_agent_sdk_inline_executor",
            adapter_revision="v1",
            provider_id="anthropic",
            transport_family="sdk",
            transport_kind="claude_agent_sdk",
            runtime_package_id="agent_runtime_core",
            runtime_package_version="0.0.0",
            supported_context_modes=("stateless",),
            supported_output_constraint_modes=(NATIVE_STRUCTURED_OUTPUT,),
            supported_read_isolation_modes=("entitled_refs",),
            supported_execution_modes=("tool_free",),
            supported_input_delivery_modes=("inline",),
            supported_network_policies=("denied",),
            supports_dynamic_operation_authorization=False,
            admission_state="in_process_test_double",
        )

    def execute(self, request: AuthorizedAgentExecutionRequest, host) -> AgentExecutionResult:
        profile = self.registry.get_execution_profile(
            request.execution_profile_ref,
            request.execution_profile_sha256,
        )
        content = json.dumps(_valid_review_output(), sort_keys=True).encode("utf-8")
        submission = OutputSubmission(
            output_slot_id="result",
            local_handle="output/result.json",
        )
        host.stage_output_bytes(submission, content)
        trace_ref, trace_sha256 = self.artifact_host.commit_attempt_trace(
            module_run_id=request.module_run_id,
            variant_id=request.variant_id,
            attempt_id=request.attempt_id,
            content=b'{"transport":"managed_design_reviewer_test"}',
            media_type="application/json",
        )
        return AgentExecutionResult(
            terminal_status="completed",
            provider_id=profile.provider_id,
            model_id=profile.model_id,
            runtime_version="0.0.0",
            outputs=(submission,),
            model_operation_ref_ids=(),
            tool_operation_ref_ids=(),
            input_tokens=10,
            output_tokens=10,
            cache_read_tokens=None,
            cache_creation_tokens=None,
            estimated_cost_usd=None,
            provider_charge_usd=None,
            context=AdapterContextResult(
                disposition_id="stateless_closed",
                context_ref=None,
                compatibility_sha256=request.execution_profile_sha256,
            ),
            failure=None,
            cell_local_trace_ref=trace_ref,
            cell_local_trace_sha256=trace_sha256,
            tool_observations=(),
        )


def _run(adapter, exported, registry, artifact_host):
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    request = _execution_input(exported, artifact_host)
    result = run_module(
        request,
        release_registry=registry,
        adapters=adapters,
        artifact_host=artifact_host,
        ledger=InMemoryModuleExecutionLedger(),
        authority=_authority(registry, request),
        clock=lambda: TEST_TIME,
    )
    assert len(result.outputs) == 1
    payload = json.loads(
        artifact_host.read_bytes(
            result.outputs[0].output_ref,
            result.outputs[0].output_sha256,
        )
    )
    validate_json_document_against_schema(
        payload,
        json.loads(exported.source.output_schema_document),
    )
    return result, payload


def test_registered_design_reviewer_runs_through_runtime_with_static_adapter(
) -> None:
    _, exported, registry = _registered_reviewer()
    artifact_host = InMemoryCellArtifactStore(
        artifact_kind_by_schema_ref={
            exported.module_release.output_schema_ref: "design_review_result"
        }
    )
    result, payload = _run(
        _StaticDesignReviewerAdapter(registry, artifact_host),
        exported,
        registry,
        artifact_host,
    )

    assert result.attempts[0].status == "completed"
    assert payload["verdict"] == "passed"


@pytest.mark.skipif(
    not RUN_PROVIDER_INTEGRATION,
    reason="set RUN_PROVIDER_INTEGRATION=1 for managed design reviewer smoke",
)
def test_registered_design_reviewer_runs_live_opus_5_through_runtime(
    tmp_path: Path,
) -> None:
    _, exported, registry = _registered_reviewer()
    artifact_host = InMemoryCellArtifactStore(
        artifact_kind_by_schema_ref={
            exported.module_release.output_schema_ref: "design_review_result"
        }
    )
    executor = ClaudeAgentSdkInlineModuleExecutor(
        release_registry=registry,
        artifact_host=artifact_host,
        workspace_root=tmp_path / "workspaces",
        max_turns=12,
    )
    result, payload = _run(executor, exported, registry, artifact_host)

    assert result.attempts[0].status == "completed"
    assert payload["verdict"] == "passed", json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    assert result.attempts[0].usage.input_tokens is not None
    assert result.attempts[0].usage.output_tokens is not None
