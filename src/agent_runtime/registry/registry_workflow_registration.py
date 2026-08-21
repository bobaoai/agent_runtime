"""Register predecessor domain workflow declarations during target migration.

New product workflows are registered as immutable ``WorkflowRelease`` records
through :mod:`registry_release_registration`.  This registry remains isolated
while the repository's older domain plugins are migrated to that release model;
it is not a second execution graph and it owns no durable state.
"""

from __future__ import annotations

import importlib
from types import MappingProxyType
from typing import Any, Mapping

from ..contracts import (
    WorkflowRuntimeRegistration,
    validate_domain_runtime_manifest,
)


def resolve_registration_reference(reference: str) -> Any:
    """Resolve an explicit ``module:attribute`` registration reference."""

    module_name, separator, attribute_name = reference.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError(f"invalid runtime reference: {reference!r}")
    value: Any = importlib.import_module(module_name)
    for attribute in attribute_name.split("."):
        value = getattr(value, attribute)
    return value


class WorkflowRuntimeRegistry:
    """Validated registry for predecessor domain workflow declarations."""

    def __init__(self) -> None:
        self._registrations: dict[str, WorkflowRuntimeRegistration] = {}

    def register(self, registration: WorkflowRuntimeRegistration) -> None:
        """Register one workflow through the atomic collection path."""

        self.register_many((registration,))

    def register_many(
        self,
        registrations: tuple[WorkflowRuntimeRegistration, ...],
    ) -> None:
        """Atomically register a validated collection of domain workflows."""

        if not registrations:
            raise ValueError("register_many requires at least one registration")
        workflow_ids: set[str] = set()
        for registration in registrations:
            registration.validate()
            if registration.workflow_id in workflow_ids:
                raise ValueError(
                    "duplicate workflow in registration batch: "
                    f"{registration.workflow_id}"
                )
            workflow_ids.add(registration.workflow_id)
        collisions = workflow_ids.intersection(self._registrations)
        if collisions:
            raise ValueError(f"workflow already registered: {sorted(collisions)[0]}")
        updated = dict(self._registrations)
        updated.update(
            (registration.workflow_id, registration)
            for registration in registrations
        )
        self._registrations = updated

    def get(self, workflow_id: str) -> WorkflowRuntimeRegistration:
        """Return one registered workflow or fail with a stable unknown-id error."""

        try:
            return self._registrations[workflow_id]
        except KeyError as exc:
            raise KeyError(f"unknown workflow registration: {workflow_id}") from exc

    def all(self) -> Mapping[str, WorkflowRuntimeRegistration]:
        """Return a deterministic read-only snapshot of all registrations."""

        return MappingProxyType(dict(sorted(self._registrations.items())))

    def resolve_driver(self, workflow_id: str) -> Any:
        """Resolve the executable workflow's domain-owned driver reference."""

        registration = self.get(workflow_id)
        if not registration.executable or registration.driver_ref is None:
            raise RuntimeError(f"workflow is not executable: {workflow_id}")
        return resolve_registration_reference(registration.driver_ref)

    def resolve_store(self, workflow_id: str) -> Any:
        """Resolve the executable workflow's domain-owned store reference."""

        registration = self.get(workflow_id)
        if not registration.executable or registration.store_ref is None:
            raise RuntimeError(f"workflow has no execution store: {workflow_id}")
        return resolve_registration_reference(registration.store_ref)

    def resolve_domain_manifest(self, workflow_id: str) -> Mapping[str, Any]:
        """Resolve a domain-owned manifest while treating its IDs as opaque."""

        registration = self.get(workflow_id)
        if not registration.domain_manifest_ref:
            raise RuntimeError(f"workflow has no domain manifest: {workflow_id}")
        manifest = resolve_registration_reference(registration.domain_manifest_ref)
        if not isinstance(manifest, Mapping):
            raise TypeError(f"domain manifest must be a mapping: {workflow_id}")
        return validate_domain_runtime_manifest(
            manifest,
            expected_contract_version=registration.contract_version,
            expected_initial_state=registration.initial_state,
        )


__all__ = [
    "WorkflowRuntimeRegistry",
    "resolve_registration_reference",
]
