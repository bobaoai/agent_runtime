from __future__ import annotations

from dataclasses import dataclass, fields, replace
import json
from pathlib import Path
from typing import Any

import pytest

from agent_runtime.contracts.inspection_release_definition import (
    ReleaseInventorySelection,
)
from agent_runtime.contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ExecutionProfileRelease,
    ExecutionVariantPolicyRelease,
    ModuleEntryPolicy,
    ModuleKind,
    OutputResolutionPolicy,
    PromptBundleRelease,
    PromptComponentKind,
    PromptComponentRelease,
    ReleaseAdmissionIntent,
    ReleaseAdmissionRecord,
    ReleaseAdmissionState,
    ReleaseMember,
    ReleaseSubjectKind,
    RetryPolicyRelease,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    WorkflowEdge,
    WorkflowNodeBinding,
    WorkflowNodeKind,
    WorkflowRelease,
)
from agent_runtime.inspection.inspection_release_rendering import (
    RELEASE_INVENTORY_SCHEMA_VERSION,
    build_runtime_release_inventory,
    render_runtime_release_markdown,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseRegistrySnapshot,
)


_HASH = "a" * 64
_TIME = "2026-08-19T12:00:00Z"
_FAMILY_FIELDS = (
    "schema_assets",
    "prompt_components",
    "prompt_bundles",
    "behavior_policies",
    "evaluation_policies",
    "retry_policies",
    "execution_variant_policies",
    "execution_profiles",
    "modules",
    "workflows",
)


@dataclass(frozen=True)
class _InspectionCase:
    snapshot: RuntimeReleaseRegistrySnapshot
    records: dict[ReleaseSubjectKind, Any]


def _profile(
    *,
    release_ref: str = "execution-profile:inventory_profile@v1",
) -> ExecutionProfileRelease:
    return ExecutionProfileRelease.build(
        execution_profile_id="inventory_profile",
        execution_profile_version="v1",
        release_ref=release_ref,
        executor_adapter_id="inventory_adapter",
        executor_adapter_revision="v1",
        transport_kind="in_process_test",
        provider_id="inventory_provider",
        model_id="inventory_model",
        reasoning_profile="xhigh",
        execution_mode="tool_free",
        semantic_input_delivery_mode="inline",
        attempt_workspace_policy="none",
        gateway_access_reasons=(),
        output_constraint_mode="prompt_only_json",
        tool_policy=(),
        network_policy="denied",
        timeout_seconds=60,
    )


def _admission(
    record: Any,
    kind: ReleaseSubjectKind,
    subject_id: str,
    *,
    admission_id: str,
    state: ReleaseAdmissionState = ReleaseAdmissionState.CANDIDATE,
    recorded_at_utc: str = _TIME,
) -> ReleaseAdmissionRecord:
    intent = ReleaseAdmissionIntent.build(
        admission_id=admission_id,
        subject_kind=kind,
        subject_id=subject_id,
        release_ref=record.release_ref,
        release_sha256=record.release_sha256,
        state=state,
        evidence_members=(
            ReleaseMember(
                member_ref=f"review-evidence:{admission_id}@v1",
                member_sha256="e" * 64,
                media_type="application/json",
            ),
        ),
    )
    return ReleaseAdmissionRecord._from_intent(  # noqa: SLF001 - persisted fixture
        intent,
        recorded_at_utc=recorded_at_utc,
    )


def _case() -> _InspectionCase:
    schema = SchemaAssetRelease.build(
        schema_asset_id="inventory_schema",
        schema_asset_version="v1",
        release_ref="schema:inventory_schema@v1",
        schema_document={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "schema:inventory_schema@v1",
            "type": "object",
        },
    )
    source_members = (
        ReleaseMember(
            member_ref="domain-release:inventory_source_zeta@v1",
            member_sha256="1" * 64,
            media_type="application/json",
        ),
        ReleaseMember(
            member_ref="domain-release:inventory_source_alpha@v1",
            member_sha256="2" * 64,
            media_type="application/json",
        ),
    )
    component = PromptComponentRelease.build(
        prompt_component_id="inventory_task_instruction",
        prompt_component_version="v1",
        release_ref="prompt-component:inventory_task_instruction@v1",
        component_kind=PromptComponentKind.TASK_INSTRUCTION,
        media_type="text/markdown",
        formatter_id="inventory_formatter",
        formatter_version="v1",
        source_members=source_members,
        formatted_content="SECRET_PROMPT_BODY\n",
    )
    prompt_bundle = PromptBundleRelease.build(
        prompt_bundle_id="inventory_prompt",
        prompt_bundle_version="v1",
        release_ref="prompt-bundle:inventory_prompt@v1",
        compiler_version="v1",
        members=(
            ReleaseMember(
                member_ref=component.release_ref,
                member_sha256=component.release_sha256,
                media_type="text/markdown",
            ),
        ),
        compiled_static_body="SECRET_COMPILED_PROMPT_BODY\n",
    )
    policy_args = {
        "policy_version": "v1",
        "policy_schema_ref": schema.release_ref,
        "policy_schema_sha256": schema.schema_sha256,
        "policy_document": {"secret_policy_body": True},
    }
    behavior = BehaviorPolicyRelease.build(
        policy_id="inventory_behavior",
        release_ref="behavior-policy:inventory_behavior@v1",
        **policy_args,
    )
    evaluation = EvaluationPolicyRelease.build(
        policy_id="inventory_evaluation",
        release_ref="evaluation-policy:inventory_evaluation@v1",
        **policy_args,
    )
    retry = RetryPolicyRelease.build(
        policy_id="inventory_retry",
        release_ref="retry-policy:inventory_retry@v1",
        **policy_args,
    )
    variant = ExecutionVariantPolicyRelease.build(
        policy_id="inventory_variant",
        release_ref="execution-variant-policy:inventory_variant@v1",
        **policy_args,
    )
    profile = _profile()
    module = RuntimeModuleRelease.build(
        module_id="inventory_module",
        module_version="v1",
        release_ref="runtime-module:inventory_module@v1",
        module_kind=ModuleKind.AGENT,
        owner_contract_ref="design-contract:inventory_module@v1",
        owner_contract_sha256="3" * 64,
        executable_ref=None,
        executable_sha256=None,
        input_schema_ref=schema.release_ref,
        input_schema_sha256=schema.schema_sha256,
        output_schema_ref=schema.release_ref,
        output_schema_sha256=schema.schema_sha256,
        prompt_bundle_ref=prompt_bundle.release_ref,
        prompt_bundle_sha256=prompt_bundle.release_sha256,
        declared_operation_ids=("invoke_model",),
        behavior_policy_ref=behavior.release_ref,
        behavior_policy_sha256=behavior.release_sha256,
        evaluation_policy_ref=evaluation.release_ref,
        evaluation_policy_sha256=evaluation.release_sha256,
        retry_policy_ref=retry.release_ref,
        retry_policy_sha256=retry.release_sha256,
        compatible_transport_kinds=("in_process_test",),
        entry_policy=ModuleEntryPolicy.STANDALONE_ALLOWED,
        output_resolution_policy=OutputResolutionPolicy.EVALUATED_SINGLE,
    )
    workflow = WorkflowRelease.build(
        workflow_id="inventory_workflow",
        workflow_version="v1",
        workflow_contract_version="contract_v1",
        release_ref="runtime-workflow:inventory_workflow@v1",
        owner_contract_ref="design-contract:inventory_workflow@v1",
        owner_contract_sha256="4" * 64,
        graph_ref="workflow-graph:inventory_workflow@v1",
        graph_sha256="5" * 64,
        initial_node_id="produce",
        nodes=(
            WorkflowNodeBinding(
                node_id="produce",
                node_kind=WorkflowNodeKind.MODULE,
                module_release_ref=module.release_ref,
                module_release_sha256=module.release_sha256,
                input_mapping_ref="input-mapping:inventory_workflow@v1",
                input_mapping_sha256="6" * 64,
            ),
        ),
        edges=(
            WorkflowEdge(
                source_node_id="produce",
                outcome_id="completed",
                target_node_id=None,
                terminal=True,
            ),
        ),
        authorization_manifest_ref="authorization-manifest:inventory_workflow@v1",
        authorization_manifest_sha256="7" * 64,
        execution_release_ref="execution-binding:inventory_workflow@v1",
        execution_release_sha256="8" * 64,
        parallel_groups=(),
    )
    records: dict[ReleaseSubjectKind, Any] = {
        ReleaseSubjectKind.SCHEMA_ASSET: schema,
        ReleaseSubjectKind.PROMPT_COMPONENT: component,
        ReleaseSubjectKind.PROMPT_BUNDLE: prompt_bundle,
        ReleaseSubjectKind.BEHAVIOR_POLICY: behavior,
        ReleaseSubjectKind.EVALUATION_POLICY: evaluation,
        ReleaseSubjectKind.RETRY_POLICY: retry,
        ReleaseSubjectKind.EXECUTION_VARIANT_POLICY: variant,
        ReleaseSubjectKind.EXECUTION_PROFILE: profile,
        ReleaseSubjectKind.RUNTIME_MODULE: module,
        ReleaseSubjectKind.WORKFLOW: workflow,
    }
    subject_ids = {
        ReleaseSubjectKind.SCHEMA_ASSET: schema.schema_asset_id,
        ReleaseSubjectKind.PROMPT_COMPONENT: component.prompt_component_id,
        ReleaseSubjectKind.PROMPT_BUNDLE: prompt_bundle.prompt_bundle_id,
        ReleaseSubjectKind.BEHAVIOR_POLICY: behavior.policy_id,
        ReleaseSubjectKind.EVALUATION_POLICY: evaluation.policy_id,
        ReleaseSubjectKind.RETRY_POLICY: retry.policy_id,
        ReleaseSubjectKind.EXECUTION_VARIANT_POLICY: variant.policy_id,
        ReleaseSubjectKind.EXECUTION_PROFILE: profile.execution_profile_id,
        ReleaseSubjectKind.RUNTIME_MODULE: module.module_id,
        ReleaseSubjectKind.WORKFLOW: workflow.workflow_id,
    }
    admissions = tuple(
        _admission(
            record,
            kind,
            subject_ids[kind],
            admission_id=f"admission_{kind.value}",
        )
        for kind, record in records.items()
    )
    return _InspectionCase(
        snapshot=RuntimeReleaseRegistrySnapshot(
            schema_assets=(schema,),
            prompt_components=(component,),
            prompt_bundles=(prompt_bundle,),
            behavior_policies=(behavior,),
            evaluation_policies=(evaluation,),
            retry_policies=(retry,),
            execution_variant_policies=(variant,),
            execution_profiles=(profile,),
            modules=(module,),
            workflows=(workflow,),
            admissions=admissions,
            active_release_refs={},
        ),
        records=records,
    )


def _selection(case: _InspectionCase) -> ReleaseInventorySelection:
    return ReleaseInventorySelection.build(
        tuple(record.release_ref for record in case.records.values())
    )


def test_selection_identity_hash_and_empty_inventory() -> None:
    empty = ReleaseInventorySelection.build(())
    case = _case()
    inventory = build_runtime_release_inventory(
        snapshot=case.snapshot,
        selection=empty,
    )

    assert empty.selection_id == (
        "release_inventory_selection_1c6bb3a7362cb21194eaab02"
    )
    assert all(inventory[field_name] == [] for field_name in _FAMILY_FIELDS)
    assert inventory["admissions"] == []
    assert inventory["active_release_refs"] == {}
    assert "| Runtime Modules | `0` |" in render_runtime_release_markdown(inventory)
    with pytest.raises(ValueError, match="unique"):
        ReleaseInventorySelection.build(("schema:duplicate@v1",) * 2)
    with pytest.raises(ValueError, match="must be derived"):
        replace(empty, selection_id="release_inventory_selection_wrong").validate()


def test_inventory_projects_all_ten_families_with_exact_row_shapes() -> None:
    case = _case()
    inventory = build_runtime_release_inventory(
        snapshot=case.snapshot,
        selection=_selection(case),
    )

    assert RELEASE_INVENTORY_SCHEMA_VERSION == "agent_runtime_release_inventory_v4"
    assert all(len(inventory[field_name]) == 1 for field_name in _FAMILY_FIELDS)
    assert len(inventory["admissions"]) == 10
    assert set(inventory["behavior_policies"][0]) == {
        "subject_kind", "subject_id", "version", "release_ref",
        "release_sha256", "latest_admission_state", "active",
        "policy_schema_ref", "policy_schema_sha256", "policy_sha256",
    }
    assert set(inventory["modules"][0]) == {
        "subject_kind", "subject_id", "version", "release_ref",
        "release_sha256", "latest_admission_state", "active", "module_kind",
        "owner_contract_ref", "owner_contract_sha256", "executable_ref",
        "executable_sha256", "input_schema_ref", "input_schema_sha256",
        "output_schema_ref", "output_schema_sha256", "prompt_bundle_ref",
        "prompt_bundle_sha256", "declared_operation_ids",
        "behavior_policy_ref", "behavior_policy_sha256",
        "evaluation_policy_ref", "evaluation_policy_sha256",
        "retry_policy_ref", "retry_policy_sha256", "compatible_transport_kinds",
        "entry_policy", "output_resolution_policy",
    }
    assert inventory["workflows"][0]["node_count"] == 1
    assert inventory["workflows"][0]["edge_count"] == 1
    assert inventory["workflows"][0]["parallel_group_count"] == 0


def test_inventory_is_content_free_and_preserves_declared_member_order() -> None:
    case = _case()
    inventory = build_runtime_release_inventory(
        snapshot=case.snapshot,
        selection=_selection(case),
    )
    encoded = json.dumps(inventory, sort_keys=True)

    assert "SECRET_PROMPT_BODY" not in encoded
    assert "SECRET_COMPILED_PROMPT_BODY" not in encoded
    assert "secret_policy_body" not in encoded
    assert "canonical_schema_json" not in encoded
    assert "canonical_policy_json" not in encoded
    assert "nodes" not in inventory["workflows"][0]
    assert inventory["prompt_components"][0]["source_members"] == [
        member.as_dict()
        for member in case.records[
            ReleaseSubjectKind.PROMPT_COMPONENT
        ].source_members
    ]


def test_selection_filters_rows_admissions_pointers_and_unselected_changes() -> None:
    case = _case()
    component = case.records[ReleaseSubjectKind.PROMPT_COMPONENT]
    selection = ReleaseInventorySelection.build((component.release_ref,))
    active_key = f"prompt_component:{component.prompt_component_id}"
    unselected_fields = {
        "prompt_component_id": "unselected_instruction",
        "prompt_component_version": "v1",
        "release_ref": "prompt-component:unselected_instruction@v1",
        "component_kind": PromptComponentKind.TASK_INSTRUCTION,
        "media_type": "text/markdown",
        "formatter_id": "inventory_formatter",
        "formatter_version": "v1",
        "source_members": component.source_members,
    }
    unselected_before = PromptComponentRelease.build(
        **unselected_fields,
        formatted_content="unselected before\n",
    )
    unselected_after = PromptComponentRelease.build(
        **unselected_fields,
        formatted_content="unselected after\n",
    )
    snapshot = replace(
        case.snapshot,
        prompt_components=(component, unselected_before),
        active_release_refs={active_key: component.release_ref},
    )
    baseline = build_runtime_release_inventory(
        snapshot=snapshot,
        selection=selection,
    )
    changed = replace(
        snapshot,
        prompt_components=(unselected_after, component),
        active_release_refs={
            active_key: component.release_ref,
            "workflow:unselected": "runtime-workflow:unselected@v1",
        },
    )

    assert build_runtime_release_inventory(
        snapshot=changed,
        selection=selection,
    ) == baseline
    assert baseline["prompt_components"][0]["active"] is True
    assert baseline["active_release_refs"] == {active_key: component.release_ref}
    assert {row["release_ref"] for row in baseline["admissions"]} == {
        component.release_ref
    }
    assert sum(len(baseline[field_name]) for field_name in _FAMILY_FIELDS) == 1


def test_selected_unadmitted_release_is_visible_with_null_state() -> None:
    case = _case()
    component = case.records[ReleaseSubjectKind.PROMPT_COMPONENT]
    snapshot = replace(case.snapshot, admissions=())
    inventory = build_runtime_release_inventory(
        snapshot=snapshot,
        selection=ReleaseInventorySelection.build((component.release_ref,)),
    )

    assert inventory["prompt_components"][0]["latest_admission_state"] is None
    assert inventory["admissions"] == []


def test_admission_match_and_same_timestamp_lifecycle_rank() -> None:
    case = _case()
    component = case.records[ReleaseSubjectKind.PROMPT_COMPONENT]
    candidate = _admission(
        component,
        ReleaseSubjectKind.PROMPT_COMPONENT,
        component.prompt_component_id,
        admission_id="admission_component_candidate",
    )
    active = _admission(
        component,
        ReleaseSubjectKind.PROMPT_COMPONENT,
        component.prompt_component_id,
        admission_id="admission_component_active",
        state=ReleaseAdmissionState.ACTIVE,
    )
    snapshot = replace(case.snapshot, admissions=(active, candidate))
    inventory = build_runtime_release_inventory(
        snapshot=snapshot,
        selection=ReleaseInventorySelection.build((component.release_ref,)),
    )

    assert tuple(state.value for state in ReleaseAdmissionState) == (
        "candidate", "shadow_executable", "production_canary", "active",
        "superseded", "retired",
    )
    assert inventory["prompt_components"][0]["latest_admission_state"] == "active"
    assert [row["admission_id"] for row in inventory["admissions"]] == [
        "admission_component_active", "admission_component_candidate",
    ]

    mismatched = _admission(
        component,
        ReleaseSubjectKind.PROMPT_BUNDLE,
        component.prompt_component_id,
        admission_id="admission_wrong_family",
    )
    with pytest.raises(ValueError, match="subject_kind"):
        build_runtime_release_inventory(
            snapshot=replace(case.snapshot, admissions=(mismatched,)),
            selection=ReleaseInventorySelection.build((component.release_ref,)),
        )


def test_unknown_and_cross_family_ambiguous_selected_refs_are_refused() -> None:
    case = _case()
    with pytest.raises(ValueError, match="unknown selected release_ref"):
        build_runtime_release_inventory(
            snapshot=case.snapshot,
            selection=ReleaseInventorySelection.build(("schema:unknown@v1",)),
        )

    prompt_bundle = case.records[ReleaseSubjectKind.PROMPT_BUNDLE]
    ambiguous_profile = _profile(release_ref=prompt_bundle.release_ref)
    ambiguous_snapshot = replace(
        case.snapshot,
        execution_profiles=(ambiguous_profile,),
        admissions=(),
    )
    with pytest.raises(ValueError, match="ambiguous selected release_ref"):
        build_runtime_release_inventory(
            snapshot=ambiguous_snapshot,
            selection=ReleaseInventorySelection.build((prompt_bundle.release_ref,)),
        )


def test_projection_order_hash_renderer_and_snapshot_are_deterministic() -> None:
    case = _case()
    selection = _selection(case)
    before = case.snapshot
    first = build_runtime_release_inventory(
        snapshot=case.snapshot,
        selection=selection,
    )
    reordered = replace(
        case.snapshot,
        admissions=tuple(reversed(case.snapshot.admissions)),
        active_release_refs=dict(reversed(tuple(case.snapshot.active_release_refs.items()))),
    )
    second = build_runtime_release_inventory(
        snapshot=reordered,
        selection=selection,
    )

    assert first == second
    assert case.snapshot == before
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert render_runtime_release_markdown(first) == render_runtime_release_markdown(second)
    assert render_runtime_release_markdown(first).startswith(
        "# Agent Runtime Release Inventory"
    )


def test_renderer_refuses_wrong_version_or_changed_projected_facts() -> None:
    case = _case()
    inventory = build_runtime_release_inventory(
        snapshot=case.snapshot,
        selection=_selection(case),
    )
    with pytest.raises(ValueError, match="unsupported"):
        render_runtime_release_markdown(
            {**inventory, "schema_version": "agent_runtime_release_inventory_v3"}
        )
    changed = json.loads(json.dumps(inventory))
    changed["modules"][0]["model_id"] = "not_a_module_field"
    with pytest.raises(ValueError, match="row fields"):
        render_runtime_release_markdown(changed)


def test_snapshot_field_fence_and_predecessor_source_absence() -> None:
    assert tuple(field.name for field in fields(RuntimeReleaseRegistrySnapshot)) == (
        *_FAMILY_FIELDS,
        "admissions",
        "active_release_refs",
    )
    source = (
        Path(__file__).resolve().parents[1]
        / "src/agent_runtime/inspection/inspection_release_rendering.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "WorkflowRuntimeRegistry",
        "BackendCandidateSet",
        "TEMPORAL_DESCRIPTOR",
        "build_runtime_inventory",
        "build_runtime_surface_inventory",
        "render_runtime_surface_markdown",
        "argparse",
    ):
        assert forbidden not in source
    assert "\nINVENTORY_SCHEMA_VERSION =" not in source
    assert "skill_packages" not in source
