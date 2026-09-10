"""Public SDK used by domain packages to declare Runtime plugins."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
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


def register_reviewer(
    root: Path,
    *,
    skill_id: str,
    module_id: str,
    module_version: str,
    source_root: Path | None = None,
    model_id: str | None = None,
    reasoning_profile: str | None = None,
    release_registry: RuntimeReleaseRegistry | None = None,
) -> RuntimeReleaseRegistrationResult:
    """Register approved Reviewer source and its one-node Workflow under root.

    Runtime defaults provide isolated context, read/search/shell, read-only
    materials, private scratch, denied tool network, a 1200-second attempt
    budget and a limit of three attempts. The independent model preset v1 is
    claude_cli / claude-opus-5[1m] / xhigh. Registration does not schedule retries.

    Repeating a definition version retains its saved capabilities and binding.
    Explicit model/reasoning overrides create a distinct execution binding;
    changed source needs a new approved definition version. Source transport
    declarations are never expanded automatically. This command only reads,
    compiles, saves and verifies registration; it does not install software,
    connect to PG, invoke a model, activate releases or create request receipts.

    New default-aware records require upgraded catalog readers. An older
    Runtime can fail while loading a shared catalog containing even one new
    record, regardless of its active selection. Upgrade affected readers before
    registering such records in a shared store; retaining an old active pointer
    is not a compatibility boundary. Never repair this by rewriting old records.

    Args:
        root: Host destination. Creates .runtime/module/<id>/<version>.json and
            .runtime/workflow/<id>_review/<version>.json through the existing
            save API. Existing directories and immutable records are reused.
        skill_id: Exact kebab-case source Skill identity.
        module_id: Exact snake_case Reviewer identity declared by the source.
        module_version: Approved definition version; never generated on conflict.
        source_root: Explicit authoring root, defaulting to root. Source files
            are loaded through ModuleReviewer.from_registration, not executed.
        model_id: Optional independent Claude model override. Ordinary calls
            use Runtime's preset; repeat registration preserves saved bindings.
        reasoning_profile: Optional independent reasoning override.
        release_registry: Optional existing exact policy lookup. Ordinary local
            registration restores the saved root and Runtime-owned policies.
    Returns:
        Native registration result with Module, Workflow, policies and exact
        Workflow Profile/Variant. A fresh local read verifies both definitions.
        No precompiled bundle or manually assembled Profile is required.
    Raises:
        ModuleAuthoringError: Source transport or tool boundary is incompatible.
        ValueError: Source/schema/policy errors, version conflicts or ambiguous
            saved bindings. Fix the source/configuration, never silently change
            transport, permissions or the requested version.
        OSError: Native source or persistence failure; inspect already saved
            facts and repeat the same request, not a newly invented version.
    Effects:
        Reads approved source, compiles and saves registration only. Does not
        install software, create an environment, connect to PG, invoke a model,
        activate releases, or create execution receipts. The host owns approval
        of source and later execution. Defaults are frozen at first registration;
        an explicit model change creates a distinct Profile/Variant binding.
    """
    from .registry_local_persistence import _restore_registry, _bundle, load_runtime_registration
    from .registry_module_authoring import ModuleReviewer
    from .registry_workflow_authoring import Workflow
    from .registry_reviewer_defaults import content_version
    from .registry_release_compilation import (
        ExecutionVariantPolicyReleaseCandidate, ExecutionVariantProfileBindingCandidate,
        compile_execution_variant_policy_release, runtime_owned_policy_schema_assets,
    )

    registry = _restore_registry(root)
    if release_registry is not None:
        registry.register_bundle(_bundle(release_registry.snapshot()))
    reviewer = ModuleReviewer.from_registration(
        Path(root) if source_root is None else Path(source_root), skill_id=skill_id, module_id=module_id)
    workflow_id = module_id + "_review"
    try:
        previous = load_runtime_registration(root, "module", module_id, module_version).release
    except FileNotFoundError:
        previous = None
    try:
        saved_workflow = load_runtime_registration(root, "workflow", workflow_id, module_version)
    except FileNotFoundError:
        saved_workflow = None
    options = {"release_registry": registry, "model_id": model_id, "reasoning_profile": reasoning_profile}
    selection = None
    if previous is not None:
        options.update(
            behavior_policy=registry.get_behavior_policy(previous.behavior_policy_ref, previous.behavior_policy_sha256),
            evaluation_policy=registry.get_evaluation_policy(previous.evaluation_policy_ref, previous.evaluation_policy_sha256),
            retry_policy=registry.get_retry_policy(previous.retry_policy_ref, previous.retry_policy_sha256),
            reviewer_defaults=previous.reviewer_defaults,
        )
    if saved_workflow is not None:
        selections = saved_workflow.registry.snapshot().execution_variant_policies
        if len(selections) != 1 or len(selections[0].policy_document()["bindings"]) != 1:
            raise ValueError("Saved Reviewer Workflow must have exactly one execution binding")
        old_selection = selections[0]
        binding = old_selection.policy_document()["bindings"][0]
        old_profile = saved_workflow.registry.get_execution_profile(
            binding["execution_profile_release_ref"], binding["execution_profile_release_sha256"])
        if model_id is None and reasoning_profile is None:
            selection = old_selection
            options["execution_profile"] = old_profile
        else:
            options["model_id"] = old_profile.model_id if model_id is None else model_id
            options["reasoning_profile"] = old_profile.reasoning_profile if reasoning_profile is None else reasoning_profile
    exported = reviewer.export(module_version=module_version, **options)
    workflow_export = Workflow.for_reviewer(exported).export()
    workflow, profile = workflow_export.workflow_release, exported.execution_profile
    if selection is None:
        selection = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
            policy_id=workflow_id + "_variant",
            policy_version=content_version({"workflow": workflow.release_sha256, "profile": profile.release_sha256}),
            origin_kind="workflow", origin_release_ref=workflow.release_ref,
            origin_release_sha256=workflow.release_sha256,
            bindings=(ExecutionVariantProfileBindingCandidate("review", profile.release_ref, profile.release_sha256),),
        ))
    schemas = {record.release_ref: record for record in workflow_export.origin_bundle.schema_assets}
    variant_schema = next((record for record in runtime_owned_policy_schema_assets()
                           if record.release_ref == selection.policy_schema_ref
                           and record.schema_sha256 == selection.policy_schema_sha256), None)
    if variant_schema is None:
        variant_schema = registry.get_schema_asset(selection.policy_schema_ref, selection.policy_schema_sha256)
    schemas[variant_schema.release_ref] = variant_schema
    bundle = replace(workflow_export.origin_bundle, schema_assets=tuple(schemas.values()),
                     execution_profiles=(profile,), execution_variant_policies=(selection,))
    result = register_runtime_module_plugin(
        registry, RuntimeModulePlugin(module_id + "_reviewer", module_version, bundle), root=Path(root))
    for kind, expected in (("module", exported.module_release), ("workflow", workflow)):
        saved = load_runtime_registration(root, kind, getattr(expected, kind + "_id"), module_version)
        if saved.release != expected:
            raise ValueError("Saved registration differs from compiled result")
        if kind == "workflow":
            if (saved.registry.get_execution_variant_policy(selection.release_ref, selection.release_sha256) != selection
                    or saved.registry.get_execution_profile(profile.release_ref, profile.release_sha256) != profile):
                raise ValueError("Saved execution binding differs from registered result")
    return result


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
    "register_reviewer",
    "validate_domain_runtime_manifest",
]
