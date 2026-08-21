"""Compile one path-free authoring source set into a Runtime Module plugin."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from ..contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ExecutionProfileRelease,
    ExecutionVariantPolicyRelease,
    ModuleKind,
    PromptBundleRelease,
    PromptComponentRelease,
    ReleaseAdmissionIntent,
    RetryPolicyRelease,
    RuntimeModuleRelease,
    SchemaAssetRelease,
    WorkflowEdge,
    WorkflowNodeBinding,
    WorkflowParallelGroupBinding,
    WorkflowRelease,
)
from .registry_authoring_inventory_loading import (
    RuntimeAuthoringSourceSet,
    _ModuleSource,
    _VariantPolicySource,
    _WorkflowSource,
)
from .registry_plugin_registration import RuntimeModulePlugin
from .registry_release_compilation import (
    AgentModuleReleaseCandidate,
    ExecutionVariantPolicyReleaseCandidate,
    ExecutionVariantProfileBindingCandidate,
    NonAgentModuleReleaseCandidate,
    WorkflowNodeReleaseCandidate,
    WorkflowReleaseCandidate,
    candidate_admission_intent,
    compile_agent_module_release,
    compile_behavior_policy_release,
    compile_evaluation_policy_release,
    compile_execution_profile_release,
    compile_execution_variant_policy_release,
    compile_non_agent_module_release,
    compile_retry_policy_release,
    compile_workflow_release,
    runtime_owned_policy_schema_assets,
)
from .registry_release_registration import RuntimeReleaseBundle


@dataclass(frozen=True)
class RuntimeReleaseBuildResult:
    """Path-free compiled release set produced from one exact inventory."""

    inventory_ref: str
    inventory_sha256: str
    plugin: RuntimeModulePlugin
    root_workflows: tuple[WorkflowRelease, ...]

    def release_identities(self) -> tuple[tuple[str, str], ...]:
        """Return the exact sorted Release refs and hashes in this build."""

        return tuple(
            sorted(
                (record.release_ref, record.release_sha256)
                for record in _bundle_releases(self.plugin.release_bundle)
            )
        )


def _deduplicate_releases(records: Iterable[Any]) -> tuple[Any, ...]:
    by_ref: dict[str, Any] = {}
    for record in records:
        prior = by_ref.setdefault(record.release_ref, record)
        if prior.release_sha256 != record.release_sha256:
            raise ValueError(
                f"one release_ref names different content: {record.release_ref}"
            )
    return tuple(by_ref[key] for key in sorted(by_ref))


def _bundle_releases(bundle: RuntimeReleaseBundle) -> tuple[Any, ...]:
    return (
        *bundle.schema_assets,
        *bundle.prompt_components,
        *bundle.prompt_bundles,
        *bundle.behavior_policies,
        *bundle.evaluation_policies,
        *bundle.retry_policies,
        *bundle.execution_variant_policies,
        *bundle.execution_profiles,
        *bundle.modules,
        *bundle.workflows,
    )


def _resolve_release(
    releases: dict[str, Any],
    release_ref: str,
    *,
    label: str,
) -> Any:
    try:
        return releases[release_ref]
    except KeyError as exc:
        raise ValueError(
            f"{label} does not resolve in the authoring source set"
        ) from exc


def _compile_module(
    source: _ModuleSource,
    *,
    behavior_by_ref: dict[str, BehaviorPolicyRelease],
    evaluation_by_ref: dict[str, EvaluationPolicyRelease],
    retry_by_ref: dict[str, RetryPolicyRelease],
) -> tuple[
    tuple[SchemaAssetRelease, ...],
    tuple[PromptComponentRelease, ...],
    PromptBundleRelease | None,
    RuntimeModuleRelease,
]:
    behavior = _resolve_release(
        behavior_by_ref,
        source.behavior_policy_ref,
        label="Module Behavior Policy",
    )
    evaluation = _resolve_release(
        evaluation_by_ref,
        source.evaluation_policy_ref,
        label="Module Evaluation Policy",
    )
    retry = _resolve_release(
        retry_by_ref,
        source.retry_policy_ref,
        label="Module Retry Policy",
    )
    if source.module_kind is ModuleKind.AGENT:
        if source.instruction_source_ref is None or source.instruction_text is None:
            raise ValueError("Agent Module source lacks instruction content")
        compiled = compile_agent_module_release(
            AgentModuleReleaseCandidate(
                module_id=source.module_id,
                module_version=source.module_version,
                owner_contract_ref=source.owner_contract_ref,
                owner_contract_content=source.owner_contract_content,
                input_schema_ref=source.input_schema_ref,
                input_schema_document=source.input_schema_document,
                output_schema_ref=source.output_schema_ref,
                output_schema_document=source.output_schema_document,
                instruction_source_ref=source.instruction_source_ref,
                instruction_text=source.instruction_text,
                declared_operation_ids=source.declared_operation_ids,
                compatible_transport_kinds=source.compatible_transport_kinds,
                behavior_policy_ref=behavior.release_ref,
                behavior_policy_sha256=behavior.release_sha256,
                evaluation_policy_ref=evaluation.release_ref,
                evaluation_policy_sha256=evaluation.release_sha256,
                retry_policy_ref=retry.release_ref,
                retry_policy_sha256=retry.release_sha256,
                entry_policy=source.entry_policy,
                output_resolution_policy=source.output_resolution_policy,
            )
        )
        return (
            compiled.schema_assets,
            compiled.prompt_components,
            compiled.prompt_bundle,
            compiled.module,
        )
    if source.executable_ref is None or source.executable_content is None:
        raise ValueError("non-Agent Module source lacks executable content")
    compiled_non_agent = compile_non_agent_module_release(
        NonAgentModuleReleaseCandidate(
            module_id=source.module_id,
            module_version=source.module_version,
            module_kind=source.module_kind,
            owner_contract_ref=source.owner_contract_ref,
            owner_contract_content=source.owner_contract_content,
            executable_ref=source.executable_ref,
            executable_content=source.executable_content,
            input_schema_ref=source.input_schema_ref,
            input_schema_document=source.input_schema_document,
            output_schema_ref=source.output_schema_ref,
            output_schema_document=source.output_schema_document,
            declared_operation_ids=source.declared_operation_ids,
            compatible_transport_kinds=source.compatible_transport_kinds,
            behavior_policy_ref=behavior.release_ref,
            behavior_policy_sha256=behavior.release_sha256,
            evaluation_policy_ref=evaluation.release_ref,
            evaluation_policy_sha256=evaluation.release_sha256,
            retry_policy_ref=retry.release_ref,
            retry_policy_sha256=retry.release_sha256,
            entry_policy=source.entry_policy,
            output_resolution_policy=source.output_resolution_policy,
        )
    )
    return (
        compiled_non_agent.schema_assets,
        (),
        None,
        compiled_non_agent.module,
    )


def _compile_workflow(
    source: _WorkflowSource,
    *,
    modules_by_source_id: dict[str, RuntimeModuleRelease],
) -> WorkflowRelease:
    nodes: list[WorkflowNodeReleaseCandidate] = []
    for node in source.nodes:
        if node.module_source_id is None:
            module_ref = None
            module_sha256 = None
        else:
            try:
                module = modules_by_source_id[node.module_source_id]
            except KeyError as exc:
                raise ValueError(
                    "Workflow Module source_id does not resolve"
                ) from exc
            module_ref = module.release_ref
            module_sha256 = module.release_sha256
        nodes.append(
            WorkflowNodeReleaseCandidate(
                node_id=node.node_id,
                node_kind=node.node_kind,
                module_release_ref=module_ref,
                module_release_sha256=module_sha256,
                input_mapping_ref=node.input_mapping_ref,
                input_mapping_document=node.input_mapping_document,
            )
        )
    edges = tuple(
        WorkflowEdge(
            source_node_id=edge.source_node_id,
            outcome_id=edge.outcome_id,
            target_node_id=edge.target_node_id,
            terminal=edge.terminal,
        )
        for edge in source.edges
    )
    groups = tuple(
        WorkflowParallelGroupBinding(
            group_id=group.group_id,
            control_node_id=group.control_node_id,
            branch_node_ids=group.branch_node_ids,
            join_node_id=group.join_node_id,
            completion_outcome_id=group.completion_outcome_id,
            join_policy=group.join_policy,
        )
        for group in source.parallel_groups
    )
    return compile_workflow_release(
        WorkflowReleaseCandidate(
            workflow_id=source.workflow_id,
            workflow_version=source.workflow_version,
            workflow_contract_version=source.workflow_contract_version,
            owner_contract_ref=source.owner_contract_ref,
            owner_contract_content=source.owner_contract_content,
            graph_ref=source.graph_ref,
            initial_node_id=source.initial_node_id,
            nodes=tuple(nodes),
            edges=edges,
            parallel_groups=groups,
            authorization_manifest_ref=source.authorization_manifest_ref,
            authorization_manifest_document=source.authorization_manifest_document,
            execution_binding_ref=source.execution_binding_ref,
            execution_binding_document=source.execution_binding_document,
        )
    )


def _compile_variant_policy(
    source: _VariantPolicySource,
    *,
    workflows_by_source_id: dict[str, WorkflowRelease],
    modules_by_source_id: dict[str, RuntimeModuleRelease],
    profiles_by_source_id: dict[str, ExecutionProfileRelease],
) -> ExecutionVariantPolicyRelease:
    if source.origin_kind == "workflow":
        origins: dict[str, Any] = workflows_by_source_id
    else:
        origins = modules_by_source_id
    try:
        origin = origins[source.origin_source_id]
    except KeyError as exc:
        raise ValueError("Variant Policy origin_source_id does not resolve") from exc
    bindings: list[ExecutionVariantProfileBindingCandidate] = []
    for binding in source.bindings:
        try:
            profile = profiles_by_source_id[binding.execution_profile_source_id]
        except KeyError as exc:
            raise ValueError(
                "Variant Policy Execution Profile source_id does not resolve"
            ) from exc
        bindings.append(
            ExecutionVariantProfileBindingCandidate(
                position_id=binding.position_id,
                execution_profile_release_ref=profile.release_ref,
                execution_profile_release_sha256=profile.release_sha256,
            )
        )
    return compile_execution_variant_policy_release(
        ExecutionVariantPolicyReleaseCandidate(
            policy_id=source.policy_id,
            policy_version=source.policy_version,
            origin_kind=source.origin_kind,
            origin_release_ref=origin.release_ref,
            origin_release_sha256=origin.release_sha256,
            bindings=tuple(bindings),
        )
    )


def build_runtime_release_set(
    source_set: RuntimeAuthoringSourceSet,
) -> RuntimeReleaseBuildResult:
    """Build one dependency-closed release set without filesystem access."""

    if type(source_set) is not RuntimeAuthoringSourceSet:
        raise TypeError("source_set must be a RuntimeAuthoringSourceSet")
    behavior = tuple(
        compile_behavior_policy_release(candidate)
        for candidate in source_set.behavior_policy_candidates
    )
    evaluation = tuple(
        compile_evaluation_policy_release(candidate)
        for candidate in source_set.evaluation_policy_candidates
    )
    retry = tuple(
        compile_retry_policy_release(candidate)
        for candidate in source_set.retry_policy_candidates
    )
    profiles = tuple(
        compile_execution_profile_release(spec)
        for spec in source_set.execution_profile_specs
    )
    behavior_by_ref = {item.release_ref: item for item in behavior}
    evaluation_by_ref = {item.release_ref: item for item in evaluation}
    retry_by_ref = {item.release_ref: item for item in retry}
    profiles_by_id = {
        spec.execution_profile_id: release
        for spec, release in zip(
            source_set.execution_profile_specs,
            profiles,
            strict=True,
        )
    }

    module_rows = tuple(
        _compile_module(
            source,
            behavior_by_ref=behavior_by_ref,
            evaluation_by_ref=evaluation_by_ref,
            retry_by_ref=retry_by_ref,
        )
        for source in source_set.module_sources
    )
    modules_by_source_id = {
        source.source_id: row[3]
        for source, row in zip(source_set.module_sources, module_rows, strict=True)
    }
    workflows = tuple(
        _compile_workflow(
            source,
            modules_by_source_id=modules_by_source_id,
        )
        for source in source_set.workflow_sources
    )
    workflows_by_source_id = {
        source.source_id: workflow
        for source, workflow in zip(
            source_set.workflow_sources,
            workflows,
            strict=True,
        )
    }
    variants = tuple(
        _compile_variant_policy(
            source,
            workflows_by_source_id=workflows_by_source_id,
            modules_by_source_id=modules_by_source_id,
            profiles_by_source_id=profiles_by_id,
        )
        for source in source_set.variant_policy_sources
    )

    schema_assets = _deduplicate_releases(
        (
            *runtime_owned_policy_schema_assets(),
            *(schema for row in module_rows for schema in row[0]),
        )
    )
    prompt_components = _deduplicate_releases(
        component for row in module_rows for component in row[1]
    )
    prompt_bundles = _deduplicate_releases(
        row[2] for row in module_rows if row[2] is not None
    )
    modules = tuple(row[3] for row in module_rows)
    release_records = (
        *schema_assets,
        *prompt_components,
        *prompt_bundles,
        *behavior,
        *evaluation,
        *retry,
        *variants,
        *profiles,
        *modules,
        *workflows,
    )
    admission_intents: tuple[ReleaseAdmissionIntent, ...] = tuple(
        candidate_admission_intent(record) for record in release_records
    )
    bundle = RuntimeReleaseBundle(
        schema_assets=tuple(schema_assets),
        prompt_components=tuple(prompt_components),
        prompt_bundles=tuple(prompt_bundles),
        behavior_policies=behavior,
        evaluation_policies=evaluation,
        retry_policies=retry,
        execution_variant_policies=variants,
        execution_profiles=profiles,
        modules=modules,
        workflows=workflows,
        admission_intents=admission_intents,
    )
    plugin = RuntimeModulePlugin(
        plugin_id=source_set.plugin_id,
        plugin_version=source_set.plugin_version,
        release_bundle=bundle,
    )
    plugin.validate()
    try:
        root_workflows = tuple(
            workflows_by_source_id[source_id]
            for source_id in source_set.root_workflow_source_ids
        )
    except KeyError as exc:  # pragma: no cover - loader validates this boundary
        raise ValueError("root Workflow source does not resolve") from exc
    return RuntimeReleaseBuildResult(
        inventory_ref=source_set.inventory_ref,
        inventory_sha256=source_set.inventory_sha256,
        plugin=plugin,
        root_workflows=root_workflows,
    )


__all__ = [
    "RuntimeReleaseBuildResult",
    "build_runtime_release_set",
]
