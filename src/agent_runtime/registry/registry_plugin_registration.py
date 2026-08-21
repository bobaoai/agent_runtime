"""Public SDK used by domain packages to declare Runtime plugins."""

from __future__ import annotations

import re
from dataclasses import dataclass
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
from ..contracts.durability_execution_definition import (
    BackendAdmissionState,
    BackendDescriptor,
    BackendEvaluationRole,
)
from .registry_release_registration import (
    RuntimeReleaseBundle,
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


__all__ = [
    "AgenticWorkflowConformancePackage",
    "BackendAdmissionState",
    "BackendDescriptor",
    "BackendEvaluationRole",
    "DomainRuntimePlugin",
    "RuntimeModulePlugin",
    "RuntimeReleaseBundle",
    "RuntimeReleaseRegistry",
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
    "validate_domain_runtime_manifest",
]
