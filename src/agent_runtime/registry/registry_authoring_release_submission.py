"""Submit one completed authoring build to an authoritative Runtime store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .registry_authoring_release_building import RuntimeReleaseBuildResult
from .registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


class RuntimeReleaseRegistrationStore(Protocol):
    """One authoritative registration operation used by the Host Client."""

    def register_bundle(
        self,
        bundle: RuntimeReleaseBundle,
        /,
    ) -> RuntimeReleaseRegistry:
        """Atomically register one completed bundle and return exact authority."""


@dataclass(frozen=True)
class RuntimeReleaseRegistrationReport:
    """Content-free exact result of one Host Client registration."""

    inventory_ref: str
    inventory_sha256: str
    plugin_id: str
    plugin_version: str
    registered_release_identities: tuple[tuple[str, str], ...]
    root_workflow_identities: tuple[tuple[str, str], ...]
    admission_intent_sha256s: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "inventory_ref": self.inventory_ref,
            "inventory_sha256": self.inventory_sha256,
            "plugin_id": self.plugin_id,
            "plugin_version": self.plugin_version,
            "registered_release_identities": [
                {"release_ref": release_ref, "release_sha256": release_sha256}
                for release_ref, release_sha256 in self.registered_release_identities
            ],
            "root_workflow_identities": [
                {"release_ref": release_ref, "release_sha256": release_sha256}
                for release_ref, release_sha256 in self.root_workflow_identities
            ],
            "admission_intent_sha256s": list(self.admission_intent_sha256s),
        }


def _snapshot_identities(
    registry: RuntimeReleaseRegistry,
) -> dict[str, str]:
    snapshot = registry.snapshot()
    return {
        record.release_ref: record.release_sha256
        for records in (
            snapshot.schema_assets,
            snapshot.prompt_components,
            snapshot.prompt_bundles,
            snapshot.behavior_policies,
            snapshot.evaluation_policies,
            snapshot.retry_policies,
            snapshot.execution_variant_policies,
            snapshot.execution_profiles,
            snapshot.modules,
            snapshot.workflows,
        )
        for record in records
    }


def register_runtime_release_set(
    store: RuntimeReleaseRegistrationStore,
    build_result: RuntimeReleaseBuildResult,
) -> RuntimeReleaseRegistrationReport:
    """Register one build exactly once and verify its complete Release closure."""

    if type(build_result) is not RuntimeReleaseBuildResult:
        raise TypeError("build_result must be a RuntimeReleaseBuildResult")
    registered = store.register_bundle(build_result.plugin.release_bundle)
    if type(registered) is not RuntimeReleaseRegistry:
        raise TypeError("Runtime Release Store must return RuntimeReleaseRegistry")
    expected = dict(build_result.release_identities())
    actual = _snapshot_identities(registered)
    mismatches = {
        release_ref: (release_sha256, actual.get(release_ref))
        for release_ref, release_sha256 in expected.items()
        if actual.get(release_ref) != release_sha256
    }
    if mismatches:
        raise RuntimeError(
            "registered Runtime Release closure differs from build result: "
            f"{mismatches}"
        )
    expected_intents = tuple(
        sorted(
            intent.admission_intent_sha256
            for intent in build_result.plugin.release_bundle.admission_intents
        )
    )
    committed_intents = {
        admission.admission_intent_sha256
        for admission in registered.snapshot().admissions
    }
    if not set(expected_intents).issubset(committed_intents):
        raise RuntimeError("registered Admission closure differs from build result")
    root_identities = tuple(
        sorted(
            (workflow.release_ref, workflow.release_sha256)
            for workflow in build_result.root_workflows
        )
    )
    return RuntimeReleaseRegistrationReport(
        inventory_ref=build_result.inventory_ref,
        inventory_sha256=build_result.inventory_sha256,
        plugin_id=build_result.plugin.plugin_id,
        plugin_version=build_result.plugin.plugin_version,
        registered_release_identities=tuple(sorted(expected.items())),
        root_workflow_identities=root_identities,
        admission_intent_sha256s=expected_intents,
    )


__all__ = [
    "RuntimeReleaseRegistrationReport",
    "RuntimeReleaseRegistrationStore",
    "register_runtime_release_set",
]
