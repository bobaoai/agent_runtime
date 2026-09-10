"""Public SDK used by domain packages to declare Runtime plugins."""

from __future__ import annotations

import re
from dataclasses import dataclass
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
    budget and a limit of three attempts. Registration never selects a model.

    Repeating a definition version retains its saved capabilities and policies.
    Changed source needs a new approved definition version. Source operation
    and transport declarations are preserved; execution preparation checks
    compatibility with the independently selected model.
    Registration does not install software, connect to PG, invoke a model,
    activate releases or create request receipts.

    New default-aware records require upgraded catalog readers. An older
    Runtime can fail while loading a shared catalog containing even one new
    record, regardless of its active selection. Upgrade affected readers before
    registering such records in a shared store; retaining an old active pointer
    is not a compatibility boundary. Never repair this by rewriting old records.

    Args:
        root: Host destination for .runtime/module/<id>/<version>.json and
            .runtime/workflow/<id>_review/<version>.json. Existing files retain
            historical bindings; those do not select new executions' models.
        skill_id: Exact kebab-case source Skill identity.
        module_id: Exact snake_case Reviewer identity declared by the source.
        module_version: Approved definition version; never generated on conflict.
        source_root: Explicit authoring root, defaulting to root.
        model_id: Retired registration parameter. Non-None is rejected before
            IO; pass model choices to prepare_local_workflow_module instead.
        reasoning_profile: Retired parameter, rejected like model_id.
        release_registry: Optional exact policy lookup. Ordinary registration
            uses saved definitions and Runtime-owned policies.
    Returns:
        Native registration result containing fixed Module, Workflow, Prompt,
        Schema and Policy/ReviewerDefaults dependencies. Submitted Profile and
        Variant arrays are empty. A fresh read verifies both definitions.
        Definition-only registration is complete, not blocked on a model.
    Raises:
        ModuleAuthoringError: Invalid source operation declaration.
        ValueError: Model parameters supplied to registration, invalid source,
            schema or policies, or immutable definition/version conflicts.
        OSError: Native source/persistence failure; inspect saved facts and
            repeat the same request instead of inventing a new version.
    Effects:
        Reads source, compiles and saves fixed definitions only. Does not select
        or compile a model Profile, invoke a provider, discover credentials or
        create an environment. Existing historical files are not cleaned up.
        Model selection and its compatibility checks belong to execution.
    """
    if model_id is not None or reasoning_profile is not None:
        raise ValueError("Model parameters belong to prepare_local_workflow_module, not Reviewer registration")

    from .registry_local_persistence import _restore_registry, _bundle, load_runtime_registration
    from .registry_module_authoring import ModuleReviewer
    from .registry_workflow_authoring import Workflow

    registry = _restore_registry(root)
    if release_registry is not None:
        registry.register_bundle(_bundle(release_registry.snapshot()))
    reviewer = ModuleReviewer.from_registration(
        Path(root) if source_root is None else Path(source_root), skill_id=skill_id, module_id=module_id)
    try:
        previous = load_runtime_registration(root, "module", module_id, module_version).release
    except FileNotFoundError:
        previous = None
    options = {"release_registry": registry, "execution_profile": None}
    if previous is not None:
        options.update(
            behavior_policy=registry.get_behavior_policy(previous.behavior_policy_ref, previous.behavior_policy_sha256),
            evaluation_policy=registry.get_evaluation_policy(previous.evaluation_policy_ref, previous.evaluation_policy_sha256),
            retry_policy=registry.get_retry_policy(previous.retry_policy_ref, previous.retry_policy_sha256),
            reviewer_defaults=previous.reviewer_defaults,
        )
    exported = reviewer.export(module_version=module_version, **options)
    workflow_export = Workflow.for_reviewer(exported).export()
    # Policy lookup may include independently registered model configurations.
    # Only fixed definitions enter this registration's submitted catalog.
    result = register_runtime_module_plugin(
        RuntimeReleaseRegistry(),
        RuntimeModulePlugin(module_id + "_reviewer", module_version, workflow_export.origin_bundle),
        root=Path(root),
    )
    for kind, expected in (("module", exported.module_release), ("workflow", workflow_export.workflow_release)):
        saved = load_runtime_registration(root, kind, getattr(expected, kind + "_id"), module_version)
        if saved.release != expected:
            raise ValueError("Saved registration differs from compiled result")
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
