"""Public SDK used by domain packages to declare Runtime plugins."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import ClassVar, Protocol

from ..contracts import (
    AgenticWorkflowConformancePackage,
    ModuleInputProjection,
    ModuleInputProjectionContract,
    ConformanceContractBinding,
    ConformanceContractKind,
    DynamicInputBinding,
    WorkflowManagementLifecycle,
    WorkflowAdmissionState,
    WorkflowRuntimeRegistration,
    validate_domain_runtime_manifest,
)
from ..contracts.durability_topology_definition import (
    BackendAdmissionState,
    BackendDescriptor,
    BackendEvaluationRole,
)
from .registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistrationResult,
    RuntimeReleaseRegistry,
)


_ID = re.compile(r"^[a-z][a-z0-9_]*$")


class WorkflowRegistrationSink(Protocol):
    """Minimal registry capability required to install one domain plugin."""

    def register_many(
        self,
        registrations: tuple[WorkflowRuntimeRegistration, ...],
    ) -> None:
        """Atomically register a validated collection of workflows."""


class RuntimeReleaseRegistrationSink(Protocol):
    """Minimal target-model registry capability required by a Module plugin."""

    def register_bundle(
        self,
        bundle: RuntimeReleaseBundle,
    ) -> RuntimeReleaseRegistrationResult:
        """Atomically register one dependency-closed target-model bundle."""


@dataclass(frozen=True)
class DomainRuntimePlugin:
    """Versioned domain entry point containing workflow registrations."""

    record_type: ClassVar[str] = "domain_runtime_plugin"

    plugin_id: str
    plugin_version: str
    registrations: tuple[WorkflowRuntimeRegistration, ...]

    def validate(self) -> None:
        """Validate plugin identity, domain ownership, and workflow uniqueness."""

        if not _ID.fullmatch(self.plugin_id):
            raise ValueError(f"invalid plugin_id: {self.plugin_id!r}")
        if not self.plugin_version:
            raise ValueError("plugin_version is required")
        if not isinstance(self.registrations, tuple):
            raise ValueError("plugin registrations must be an immutable tuple")
        if not self.registrations:
            raise ValueError("domain Runtime plugin requires registrations")
        workflow_ids: set[str] = set()
        domains: set[str] = set()
        for registration in self.registrations:
            registration.validate()
            if registration.workflow_id in workflow_ids:
                raise ValueError(
                    f"duplicate workflow in plugin: {registration.workflow_id}"
                )
            workflow_ids.add(registration.workflow_id)
            domains.add(registration.domain)
        if len(domains) != 1:
            raise ValueError("one domain Runtime plugin cannot mix domain owners")


@dataclass(frozen=True)
class RuntimeModulePlugin:
    """Versioned target-model plugin containing Module and Workflow releases."""

    record_type: ClassVar[str] = "runtime_module_plugin"

    plugin_id: str
    plugin_version: str
    release_bundle: RuntimeReleaseBundle

    def validate(self) -> None:
        """Validate plugin identity without reinterpreting release semantics."""

        if not _ID.fullmatch(self.plugin_id):
            raise ValueError(f"invalid plugin_id: {self.plugin_id!r}")
        if not self.plugin_version:
            raise ValueError("plugin_version is required")
        if type(self.release_bundle) is not RuntimeReleaseBundle:
            raise ValueError("release_bundle must be a RuntimeReleaseBundle")
        if self.release_bundle.is_empty():
            raise ValueError("Runtime Module plugin requires release records")

    @classmethod
    def from_bundle(
        cls, release_bundle: RuntimeReleaseBundle, *,
        plugin_id: str | None = None, plugin_version: str | None = None,
    ) -> "RuntimeModulePlugin":
        """Wrap a compiled bundle, taking its package label from the bundle itself.

        The plugin identity is a registration label: register_runtime_module_plugin
        validates it, and neither the local .runtime files nor the Registry stores
        persist it. An owner-chosen label may be given (both values together).
        Otherwise code reads it from the bundle: its single Workflow's ID and
        version, or, without Workflows, its single Module's ID and version.

        Raises:
            ValueError: Only one of plugin_id/plugin_version was given, or the
                label cannot be read unambiguously (several or no Workflows and
                Modules). Give the owner's label explicitly in that case.
        Effects:
            Pure construction; validate() still runs at registration.
        """
        if (plugin_id is None) != (plugin_version is None):
            raise ValueError("Give plugin_id and plugin_version together, or neither to read them from the bundle")
        if plugin_id is None:
            if type(release_bundle) is not RuntimeReleaseBundle:
                raise ValueError("release_bundle must be a RuntimeReleaseBundle")
            if len(release_bundle.workflows) == 1:
                target = release_bundle.workflows[0]
                plugin_id, plugin_version = target.workflow_id, target.workflow_version
            elif not release_bundle.workflows and len(release_bundle.modules) == 1:
                target = release_bundle.modules[0]
                plugin_id, plugin_version = target.module_id, target.module_version
            else:
                raise ValueError(
                    f"The bundle has {len(release_bundle.workflows)} Workflows and {len(release_bundle.modules)} "
                    "Modules; its package label cannot be read unambiguously. Give the owner's plugin identity")
        return cls(plugin_id, plugin_version, release_bundle)


def register_runtime_plugin(
    registry: WorkflowRegistrationSink,
    plugin: DomainRuntimePlugin,
) -> None:
    """Validate and atomically install one explicitly loaded domain plugin."""

    plugin.validate()
    registry.register_many(plugin.registrations)


def register_runtime_module_plugin(
    registry: RuntimeReleaseRegistrationSink,
    plugin: RuntimeModulePlugin,
    *,
    root: Path | None = None,
) -> RuntimeReleaseRegistrationResult:
    """Register a plugin, optionally saving its reusable definitions under root.

    The existing Registry performs registration first. When root is supplied,
    save_runtime_registration writes the successful result to .runtime/module
    and .runtime/workflow. File errors propagate without rolling back a prior
    Registry commit; retrying the original bundle is safe. Omit root to retain
    the original store-only behavior. No active pointer or host configuration
    is selected or changed.
    """

    plugin.validate()
    result = registry.register_bundle(plugin.release_bundle)
    if root is not None:
        from .registry_local_persistence import save_runtime_registration
        save_runtime_registration(root, result)
    return result


def _registered_version(registry, kind, subject_id, version):
    """Find an exact identity/version in the already restored catalog."""
    records = getattr(registry.snapshot(), kind + "s")
    matches = [record for record in records
               if (getattr(record, kind + "_id"), getattr(record, kind + "_version"))
               == (subject_id, version)]
    if len(matches) > 1:
        raise ValueError("Registered definition version has conflicting identities")
    return matches[0] if matches else None


def _export_from_registered_source(source, module, registry):
    """Read an existing export without recompiling or resolving current defaults.

    This value is used only by the shared Module.to_workflow constructor.
    Its origin_bundle must not be used to recover historical Policy Schemas;
    the registered exact closure supplies those independently.
    """
    from ..contracts.registry_release_definition import (
        ModuleKind, PromptComponentKind, SchemaAssetRelease,
    )
    from .registry_module_authoring import ModuleExport
    from .registry_release_compilation import (
        AgentModuleReleaseCandidate, CompiledAgentModuleRelease, sha256_text,
    )

    module.validate()
    if (module.module_kind is not ModuleKind.AGENT or module.module_id != source.module_id
            or module.owner_contract_ref != source.owner_contract_ref
            or module.owner_contract_sha256 != sha256_text(source.owner_contract_content)):
        raise ValueError("Registered Module source collision: identity or owner changed")
    schemas = []
    for direction in ("input", "output"):
        ref = getattr(module, direction + "_schema_ref")
        stored = registry.get_schema_asset(ref, getattr(module, direction + "_schema_sha256"))
        if getattr(source, direction + "_schema_ref") != ref:
            raise ValueError("Registered Module source collision: schema reference changed")
        observed = SchemaAssetRelease.build(
            schema_asset_id=stored.schema_asset_id,
            schema_asset_version=stored.schema_asset_version,
            release_ref=ref,
            schema_document=json.loads(getattr(source, direction + "_schema_document")),
        )
        if observed != stored:
            raise ValueError("Registered Module source collision: schema content changed")
        schemas.append(stored)
    prompt = registry.get_prompt_bundle(module.prompt_bundle_ref, module.prompt_bundle_sha256)
    components = tuple(registry.get_prompt_component(member.member_ref, member.member_sha256)
                       for member in prompt.members)
    instructions = [item for item in components if item.component_kind is PromptComponentKind.TASK_INSTRUCTION]
    if len(instructions) != 1:
        raise ValueError("Registered Module source has no unique task instruction")
    instruction = instructions[0]
    if (len(instruction.source_members) != 1
            or instruction.source_members[0].member_ref != source.instruction_source_ref
            or instruction.source_members[0].member_sha256 != sha256_text(source.instruction_text)
            or instruction.formatted_content != source.instruction_text):
        raise ValueError("Registered Module source collision: instruction or Skill changed")
    behavior = registry.get_behavior_policy(module.behavior_policy_ref, module.behavior_policy_sha256)
    evaluation = registry.get_evaluation_policy(module.evaluation_policy_ref, module.evaluation_policy_sha256)
    retry = registry.get_retry_policy(module.retry_policy_ref, module.retry_policy_sha256)
    candidate = AgentModuleReleaseCandidate(
        module_id=module.module_id, module_version=module.module_version,
        owner_contract_ref=module.owner_contract_ref, owner_contract_content=source.owner_contract_content,
        input_schema_ref=module.input_schema_ref, input_schema_document=source.input_schema_document,
        output_schema_ref=module.output_schema_ref, output_schema_document=source.output_schema_document,
        instruction_source_ref=source.instruction_source_ref, instruction_text=source.instruction_text,
        declared_operation_ids=module.declared_operation_ids,
        compatible_transport_kinds=module.compatible_transport_kinds,
        behavior_policy_ref=module.behavior_policy_ref, behavior_policy_sha256=module.behavior_policy_sha256,
        evaluation_policy_ref=module.evaluation_policy_ref, evaluation_policy_sha256=module.evaluation_policy_sha256,
        retry_policy_ref=module.retry_policy_ref, retry_policy_sha256=module.retry_policy_sha256,
        entry_policy=module.entry_policy, output_resolution_policy=module.output_resolution_policy,
        reviewer_defaults=module.reviewer_defaults, execution_requirements=module.execution_requirements,
    )
    return ModuleExport(
        source=source, candidate=candidate,
        compiled=CompiledAgentModuleRelease(
            schema_assets=tuple(schemas), prompt_components=components,
            prompt_bundle=prompt, module=module,
        ),
        behavior_policy=behavior, evaluation_policy=evaluation, retry_policy=retry,
    )


def _verify_single_node_workflow(workflow, module):
    """Keep an existing graph only when it is the requested exact one-node target."""
    from ..contracts.registry_release_definition import WorkflowNodeKind
    workflow.validate()
    if len(workflow.nodes) != 1:
        raise ValueError("Registered single-node Workflow must have one exact Module node")
    node = workflow.nodes[0]
    if (node.node_kind is not WorkflowNodeKind.MODULE
            or workflow.initial_node_id != node.node_id
            or (node.module_release_ref, node.module_release_sha256)
            != (module.release_ref, module.release_sha256)):
        raise ValueError("Registered Workflow differs from the exact Module target")


def _source_location(root, *, source, skill_id, module_id, source_root):
    """Exact loader arguments: from an explicit source declaration, or the explicit identities."""
    if source is not None:
        if skill_id is not None or module_id is not None or source_root is not None:
            raise ValueError("Give source, or source_root with skill_id and module_id, not both")
        from .registry_module_loading import locate_module_registration
        location = locate_module_registration(Path(source))
        return location.project_root, location.skill_id, location.module_id
    if skill_id is None or module_id is None:
        raise ValueError("Registration needs the explicit Module source, or skill_id with module_id")
    return (Path(root) if source_root is None else Path(source_root)), skill_id, module_id


def _register_source_workflow(root, source, *, module_version, workflow_id, plugin_id, construct, same_definition):
    """Compile or reuse one source's Module and its one-node Workflow, save them and verify the readback.

    construct(source) returns the authoring object for a new version;
    same_definition(previous) raises when a requested non-source setting
    differs from an already registered version. The source itself is compared
    by _export_from_registered_source.
    """
    from ..foundation.foundation_contract_validation import validate_snake_case_name
    from .registry_local_persistence import _restore_registry, _closure, load_runtime_registration
    from .registry_module_loading import MODULE_REGISTRATION_SCHEMA_VERSION
    from .registry_module_authoring import Module
    from .registry_release_compilation import compile_workflow_release

    root = Path(root)
    module_id = source.module_id
    workflow_id = module_id if workflow_id is None else workflow_id
    validate_snake_case_name("workflow_id", workflow_id)
    if source.schema_version != MODULE_REGISTRATION_SCHEMA_VERSION:
        raise ValueError("Registration requires a migrated v4 task source")
    registry = _restore_registry(root)
    previous = _registered_version(registry, "module", module_id, module_version)
    if previous is None:
        exported = construct(source).export(module_version=module_version)
        fixed = exported.origin_bundle
    else:
        same_definition(previous)
        exported = _export_from_registered_source(source, previous, registry)
        fixed = _closure(registry, previous, supplied_variants=())
    module = exported.module_release
    workflow = _registered_version(registry, "workflow", workflow_id, module_version)
    if workflow is None:
        # Workflow.export would call the restored export's origin_bundle and
        # resolve today's Policy Schemas. Compile only the shared graph candidate.
        graph = Module.to_workflow(exported, workflow_id=workflow_id)
        workflow = compile_workflow_release(graph.candidate)
    _verify_single_node_workflow(workflow, module)
    bundle = replace(fixed, workflows=(workflow,))
    # The source command has no caller-selected bundle order. Give first and
    # repeated registration the same stable record order without altering any
    # record payload or ordered members inside a record.
    bundle = RuntimeReleaseBundle(**{
        field.name: tuple(sorted(getattr(bundle, field.name), key=lambda record: record.release_ref))
        for field in fields(RuntimeReleaseBundle)
    })
    result = register_runtime_module_plugin(
        registry, RuntimeModulePlugin(plugin_id, module_version, bundle), root=root,
    )
    for kind, expected in (("module", module), ("workflow", workflow)):
        saved = load_runtime_registration(root, kind, getattr(expected, kind + "_id"), module_version)
        actual_fixed = _closure(saved.registry, saved.release, supplied_variants=())
        expected_fixed = _closure(registry, expected, supplied_variants=())
        if saved.release != expected or actual_fixed.as_dict() != expected_fixed.as_dict():
            raise ValueError("Saved registration differs from the exact definition closure")
    return result


def register_reviewer(
    root: Path,
    *,
    module_version: str,
    source: Path | None = None,
    skill_id: str | None = None,
    module_id: str | None = None,
    workflow_id: str | None = None,
    source_root: Path | None = None,
) -> RuntimeReleaseRegistrationResult:
    """Register v4 Reviewer source and its exact one-node Workflow under root.

    A new definition uses ModuleReviewer's fixed environment and the generic
    Module compiler. Registration never selects a model or accepts technical
    Policy/Profile overrides. The source-specific entry checks common Reviewer
    output format once; ordinary plugin registration remains role-neutral.

    An existing version is compared with the captured owner, canonical schemas
    and exact task instruction, then reused with its original dependencies.
    Current defaults and Policy Schema factories never rewrite historical
    definitions. New content requires a new version; no version is auto-created.
    Does not install software, connect to PG or run a model.

    Args:
        root: Local destination for .runtime/module and .runtime/workflow.
        module_version: Exact definition version chosen by the source owner.
        source: The explicitly selected Module source directory or its
            module_registration.json. skill_id and module_id are read from that
            declaration (locate_module_registration); exclusive with the three
            exact arguments below.
        skill_id: Lower-level exact kebab-case source Skill identity, with
            module_id and optional source_root, for callers that already hold
            the loader arguments as code values.
        module_id: Lower-level exact snake_case Module identity.
        workflow_id: Explicit Workflow name; None selects module_id. Existing
            suffix-named graphs are neither renamed nor deleted.
        source_root: Lower-level explicit authoring root; None uses root.
    Returns:
        Native RuntimeReleaseRegistrationResult with definition-only submitted
        Module/Workflow/Prompt/Schema/Policy records. Profile and Variant arrays
        are empty; the catalog may retain historical choices. Fresh local
        readback verifies both targets and their exact fixed dependencies.
        selection_from_registration(result, root=root) turns it into the
        exact execution selection without retyping its refs or hashes.
    Raises:
        FileNotFoundError: The selected source is absent.
        ValueError: Invalid v4 source, source location, common format, changed
            source at an existing version, graph target mismatch, Registry
            conflict, or both/neither source forms given.
        OSError: Original source/file failure. A prior Registry transaction or
            first file may already have succeeded. Retry the same request to
            reuse its original Module and finish the missing Workflow.
        TypeError: Unrecognized model/Policy parameters, before function IO.
    Effects:
        Reads the captured source once and the existing local catalog. New
        records are compiled/registered and saved by the existing APIs. Repeat
        registration preserves original bytes/hash/order and historical bindings.
        Does not install software, connect to PG, discover credentials, change
        an active pointer, run a model or create a receipt/recovery service.
        Explicit PG plugin registration remains a separate existing store API.
    """
    from .registry_module_loading import load_reviewer_registration
    from .registry_module_authoring import ModuleReviewer

    project_root, skill_id, module_id = _source_location(
        root, source=source, skill_id=skill_id, module_id=module_id, source_root=source_root)
    loaded = load_reviewer_registration(project_root, skill_id=skill_id, module_id=module_id)
    return _register_source_workflow(
        root, loaded, module_version=module_version, workflow_id=workflow_id,
        plugin_id=module_id + "_reviewer", construct=ModuleReviewer, same_definition=lambda previous: None)


def register_module(
    root: Path,
    *,
    source: Path,
    module_version: str,
    execution_requirements,
    workflow_id: str | None = None,
    declared_operation_ids: tuple[str, ...] = ("model_execute",),
    entry_policy=None,
    output_resolution_policy=None,
) -> RuntimeReleaseRegistrationResult:
    """Register an ordinary v4 Module source and its exact one-node Workflow under root.

    The non-Reviewer counterpart of register_reviewer, with the same compiler,
    repeat and conflict rules and the same verified local readback. The Module
    keeps its own input/output schemas; no Reviewer format is required.

    Args:
        root: Local destination for .runtime/module and .runtime/workflow.
        source: The explicitly selected Module source directory or its
            module_registration.json; skill_id and module_id come from that
            declaration.
        module_version: Exact definition version chosen by the source owner.
        execution_requirements: The owner's ModuleExecutionRequirements, for
            example module_requirements_preset("code_change"). Fixed with the
            definition.
        workflow_id: Explicit Workflow name; None selects the Module ID.
        declared_operation_ids: Model operation and real domain operations.
        entry_policy: ModuleEntryPolicy; None keeps Module's default
            (standalone_allowed).
        output_resolution_policy: OutputResolutionPolicy; None keeps Module's
            default (evaluated_single).
    Returns:
        Native RuntimeReleaseRegistrationResult, as register_reviewer; pass it
        to selection_from_registration for the execution selection. The CLI
        form is agent-runtime-registry register-module --source --preset
        --version, which also prints and optionally writes that selection.
    Raises:
        FileNotFoundError: The selected source is absent.
        ValueError: Invalid source or location, invalid requirements, a
            registered version whose source, requirements, operations or
            policies differ (a new version is required), graph mismatch or
            Registry conflict.
        ModuleAuthoringError: Invalid operation declaration.
        OSError: Native file failure; retry the same request after readback.
    Effects:
        As register_reviewer: local registration and readback only; no model,
        PG, credential, active pointer or software installation.
    """
    from .registry_module_authoring import Module

    options = {"declared_operation_ids": declared_operation_ids}
    if entry_policy is not None:
        options["entry_policy"] = entry_policy
    if output_resolution_policy is not None:
        options["output_resolution_policy"] = output_resolution_policy
    loaded = Module.from_source(Path(source), execution_requirements=execution_requirements, **options)

    def same_definition(previous):
        for name, current, value in (
                ("execution requirements", previous.get_execution_requirements(), loaded.execution_requirements),
                ("declared operations", previous.declared_operation_ids, loaded.declared_operation_ids),
                ("entry policy", previous.entry_policy, loaded.entry_policy),
                ("output resolution policy", previous.output_resolution_policy, loaded.output_resolution_policy)):
            if current != value:
                raise ValueError(f"Registered Module {previous.module_id}@{previous.module_version} has different "
                                 f"{name}; register changed content under a new version")

    return _register_source_workflow(
        root, loaded.source, module_version=module_version, workflow_id=workflow_id,
        plugin_id=loaded.source.module_id, construct=lambda _: loaded, same_definition=same_definition)


__all__ = [
    "AgenticWorkflowConformancePackage",
    "BackendAdmissionState",
    "BackendDescriptor",
    "BackendEvaluationRole",
    "DomainRuntimePlugin",
    "RuntimeModulePlugin",
    "RuntimeReleaseBundle",
    "RuntimeReleaseRegistry",
    "RuntimeReleaseRegistrationSink",
    "ModuleInputProjection",
    "ModuleInputProjectionContract",
    "ConformanceContractBinding",
    "ConformanceContractKind",
    "DynamicInputBinding",
    "WorkflowAdmissionState",
    "WorkflowManagementLifecycle",
    "WorkflowRegistrationSink",
    "WorkflowRuntimeRegistration",
    "register_runtime_plugin",
    "register_runtime_module_plugin",
    "register_module",
    "register_reviewer",
    "validate_domain_runtime_manifest",
]
