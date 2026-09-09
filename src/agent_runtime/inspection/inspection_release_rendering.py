"""Emit a stable, machine-readable inventory of Agent Runtime registrations.

The inventory is deliberately assembled from code-owned registrations and
backend descriptors.  It lets tests, operators, and generated documentation
inspect names, authority references, admission states, and implementation
readiness without treating a hand-maintained architecture diagram as runtime
truth.
"""

from __future__ import annotations

import argparse
import json
from typing import Any, Sequence

from ..durability.durability_backend_registration import TEMPORAL_DESCRIPTOR
from .inspection_architecture_rendering import (
    build_runtime_architecture_projection,
    render_runtime_architecture_markdown,
)
from ..registry.registry_workflow_registration import WorkflowRuntimeRegistry
from ..contracts.durability_topology_definition import BackendCandidateSet
from ..registry.registry_release_registration import RuntimeReleaseRegistry


INVENTORY_SCHEMA_VERSION = "agent_runtime_inventory_v3"
RELEASE_INVENTORY_SCHEMA_VERSION = "agent_runtime_release_inventory_v4"


def build_runtime_inventory(
    *,
    registry: WorkflowRuntimeRegistry | None = None,
    backend_candidate_set: BackendCandidateSet | None = None,
) -> dict[str, Any]:
    """Return a deterministic inventory for an explicit host composition.

    The standalone package has no built-in domain workflows. Callers that want
    a product inventory pass the host's registry and backend release_registry.
    """

    registry = registry or WorkflowRuntimeRegistry()
    backend_candidate_set = backend_candidate_set or BackendCandidateSet(
        (TEMPORAL_DESCRIPTOR,)
    )
    registered_backend_ids = set(backend_candidate_set.all())

    workflows = []
    for registration in registry.all().values():
        missing_backend_ids = (
            set(registration.allowed_backend_ids) - registered_backend_ids
        )
        if missing_backend_ids:
            raise RuntimeError(
                f"workflow {registration.workflow_id} references unknown backends: "
                f"{sorted(missing_backend_ids)}"
            )
        workflows.append(
            {
                "workflow_id": registration.workflow_id,
                "domain": registration.domain,
                "registration_version": registration.registration_version,
                "contract_version": registration.contract_version,
                "intent_ref": registration.intent_ref,
                "graph_authority_ref": registration.graph_authority_ref,
                "domain_manifest_ref": registration.domain_manifest_ref,
                "admission_state": registration.admission_state.value,
                "capabilities": sorted(registration.capabilities),
                "entitlement_mode": registration.entitlement_mode,
                "initial_state": registration.initial_state,
                "executable": registration.executable,
                "driver_ref": registration.driver_ref,
                "store_ref": registration.store_ref,
                "default_backend_id": registration.default_backend_id,
                "allowed_backend_ids": list(registration.allowed_backend_ids),
            }
        )

    backends = []
    for descriptor in backend_candidate_set.all().values():
        backends.append(
            {
                "backend_id": descriptor.backend_id,
                "adapter_contract_version": descriptor.adapter_contract_version,
                "sdk_package": descriptor.sdk_package,
                "admission_state": descriptor.admission_state.value,
                "evaluation_role": descriptor.evaluation_role.value,
                "implementation_ref": descriptor.implementation_ref,
                "supports_dedicated": descriptor.supports_dedicated,
                "supports_pooled": descriptor.supports_pooled,
                "requires_external_service": descriptor.requires_external_service,
            }
        )

    return {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "selected_backend_id": backend_candidate_set.selected_candidate().backend_id,
        "workflows": workflows,
        "durable_backend_candidates": backends,
    }


def build_runtime_surface_inventory() -> dict[str, Any]:
    """Compatibility wrapper for the target architecture projection."""

    return build_runtime_architecture_projection()


def build_runtime_release_inventory(
    release_registry: RuntimeReleaseRegistry,
) -> dict[str, Any]:
    """Return a content-free inspection of one target Runtime Release Registry."""

    if type(release_registry) is not RuntimeReleaseRegistry:
        raise ValueError("release_registry must be a RuntimeReleaseRegistry")
    snapshot = release_registry.snapshot()
    return {
        "schema_version": RELEASE_INVENTORY_SCHEMA_VERSION,
        "schema_assets": [
            {
                "schema_asset_id": release.schema_asset_id,
                "version": release.schema_asset_version,
                "release_ref": release.release_ref,
                "schema_sha256": release.schema_sha256,
                "release_sha256": release.release_sha256,
            }
            for release in snapshot.schema_assets
        ],
        "prompt_components": [
            {
                "prompt_component_id": release.prompt_component_id,
                "version": release.prompt_component_version,
                "release_ref": release.release_ref,
                "release_sha256": release.release_sha256,
                "component_kind": release.component_kind.value,
                "media_type": release.media_type,
                "formatter_id": release.formatter_id,
                "formatter_version": release.formatter_version,
                "formatted_content_sha256": release.formatted_content_sha256,
            }
            for release in snapshot.prompt_components
        ],
        "prompt_bundles": [
            {
                "prompt_bundle_id": release.prompt_bundle_id,
                "version": release.prompt_bundle_version,
                "release_ref": release.release_ref,
                "release_sha256": release.release_sha256,
            }
            for release in snapshot.prompt_bundles
        ],
        "behavior_policies": [
            {
                "policy_id": release.policy_id,
                "version": release.policy_version,
                "release_ref": release.release_ref,
                "release_sha256": release.release_sha256,
                "policy_schema_ref": release.policy_schema_ref,
                "policy_schema_sha256": release.policy_schema_sha256,
            }
            for release in snapshot.behavior_policies
        ],
        "evaluation_policies": [
            {
                "policy_id": release.policy_id,
                "version": release.policy_version,
                "release_ref": release.release_ref,
                "release_sha256": release.release_sha256,
                "policy_schema_ref": release.policy_schema_ref,
                "policy_schema_sha256": release.policy_schema_sha256,
            }
            for release in snapshot.evaluation_policies
        ],
        "retry_policies": [
            {
                "policy_id": release.policy_id,
                "version": release.policy_version,
                "release_ref": release.release_ref,
                "release_sha256": release.release_sha256,
                "policy_schema_ref": release.policy_schema_ref,
                "policy_schema_sha256": release.policy_schema_sha256,
            }
            for release in snapshot.retry_policies
        ],
        "execution_variant_policies": [
            {
                "policy_id": release.policy_id,
                "version": release.policy_version,
                "release_ref": release.release_ref,
                "release_sha256": release.release_sha256,
                "policy_schema_ref": release.policy_schema_ref,
                "policy_schema_sha256": release.policy_schema_sha256,
            }
            for release in snapshot.execution_variant_policies
        ],
        "execution_profiles": [
            {
                "execution_profile_id": release.execution_profile_id,
                "version": release.execution_profile_version,
                "release_ref": release.release_ref,
                "release_sha256": release.release_sha256,
                "executor_adapter_id": release.executor_adapter_id,
                "transport_kind": release.transport_kind,
                "provider_id": release.provider_id,
                "model_id": release.model_id,
                "reasoning_profile": release.reasoning_profile,
                "output_constraint_mode": release.output_constraint_mode,
            }
            for release in snapshot.execution_profiles
        ],
        "modules": [
            {
                "module_id": release.module_id,
                "version": release.module_version,
                "release_ref": release.release_ref,
                "release_sha256": release.release_sha256,
                "module_kind": release.module_kind.value,
                "owner_contract_ref": release.owner_contract_ref,
                "entry_policy": release.entry_policy.value,
                "declared_operation_ids": list(
                    release.declared_operation_ids
                ),
                "active": snapshot.active_release_refs.get(
                    f"runtime_module:{release.module_id}"
                ) == release.release_ref,
            }
            for release in snapshot.modules
        ],
        "workflows": [
            {
                "workflow_id": release.workflow_id,
                "version": release.workflow_version,
                "contract_version": release.workflow_contract_version,
                "release_ref": release.release_ref,
                "release_sha256": release.release_sha256,
                "graph_ref": release.graph_ref,
                "graph_sha256": release.graph_sha256,
                "nodes": [node.as_dict() for node in release.nodes],
                "edges": [edge.as_dict() for edge in release.edges],
                "parallel_groups": [
                    group.as_dict() for group in release.parallel_groups
                ],
                "active": snapshot.active_release_refs.get(
                    f"workflow:{release.workflow_id}"
                ) == release.release_ref,
            }
            for release in snapshot.workflows
        ],
        "active_release_refs": dict(snapshot.active_release_refs),
    }


def render_runtime_surface_markdown() -> str:
    """Compatibility wrapper for the target architecture renderer."""

    return render_runtime_architecture_markdown()


def render_runtime_release_markdown(
    release_registry: RuntimeReleaseRegistry,
) -> str:
    """Render a content-free human projection of one Release Registry."""

    inventory = build_runtime_release_inventory(release_registry)
    lines = [
        "# Agent Runtime Release Inventory",
        "",
        (
            "> Generated from the host-composed Runtime Release Registry. "
            "It contains release metadata only and does not admit a candidate."
        ),
        "",
        "## Summary",
        "",
        "| Release kind | Count |",
        "| --- | ---: |",
        f"| Schema Asset | `{len(inventory['schema_assets'])}` |",
        f"| Prompt Component | `{len(inventory['prompt_components'])}` |",
        f"| Prompt Bundle | `{len(inventory['prompt_bundles'])}` |",
        f"| Behavior Policy | `{len(inventory['behavior_policies'])}` |",
        f"| Evaluation Policy | `{len(inventory['evaluation_policies'])}` |",
        f"| Retry Policy | `{len(inventory['retry_policies'])}` |",
        (
            "| Execution Variant Policy | "
            f"`{len(inventory['execution_variant_policies'])}` |"
        ),
        f"| Execution Profile | `{len(inventory['execution_profiles'])}` |",
        f"| Runtime Module | `{len(inventory['modules'])}` |",
        f"| Workflow | `{len(inventory['workflows'])}` |",
        f"| Active release pointer | `{len(inventory['active_release_refs'])}` |",
        "",
        "## Workflows",
        "",
        (
            "| Workflow | Release version | Contract version | Nodes | Edges | "
            "Parallel groups | Active | Release |"
        ),
        "| --- | --- | --- | ---: | ---: | ---: | --- | --- |",
    ]
    for workflow in inventory["workflows"]:
        lines.append(
            f"| `{workflow['workflow_id']}` | `{workflow['version']}` | "
            f"`{workflow['contract_version']}` | `{len(workflow['nodes'])}` | "
            f"`{len(workflow['edges'])}` | "
            f"`{len(workflow['parallel_groups'])}` | "
            f"`{workflow['active']}` | "
            f"`{workflow['release_ref']}` |"
        )
    lines.extend(
        [
            "",
            "## Runtime Modules",
            "",
            (
                "| Module | Version | Kind | Entry policy | Owner contract | "
                "Active |"
            ),
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for module in inventory["modules"]:
        lines.append(
            f"| `{module['module_id']}` | `{module['version']}` | "
            f"`{module['module_kind']}` | `{module['entry_policy']}` | "
            f"`{module['owner_contract_ref']}` | "
            f"`{module['active']}` |"
        )
    lines.extend(
        [
            "",
            "## Schema Assets",
            "",
            "| Schema asset | Version | Schema ref | Schema hash |",
            "| --- | --- | --- | --- |",
        ]
    )
    for schema_asset in inventory["schema_assets"]:
        lines.append(
            f"| `{schema_asset['schema_asset_id']}` | "
            f"`{schema_asset['version']}` | "
            f"`{schema_asset['release_ref']}` | "
            f"`{schema_asset['schema_sha256']}` |"
        )
    lines.extend(
        [
            "",
            "## Prompt Components",
            "",
            (
                "| Component | Version | Kind | Formatter | Content hash | "
                "Release |"
            ),
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for component in inventory["prompt_components"]:
        lines.append(
            f"| `{component['prompt_component_id']}` | "
            f"`{component['version']}` | `{component['component_kind']}` | "
            f"`{component['formatter_id']}@{component['formatter_version']}` | "
            f"`{component['formatted_content_sha256']}` | "
            f"`{component['release_ref']}` |"
        )
    lines.extend(
        [
            "",
            "## Policies",
            "",
            "| Family | Policy | Version | Schema | Release |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for family, inventory_key in (
        ("Behavior", "behavior_policies"),
        ("Evaluation", "evaluation_policies"),
        ("Retry", "retry_policies"),
        ("Execution Variant", "execution_variant_policies"),
    ):
        for policy in inventory[inventory_key]:
            lines.append(
                f"| {family} | `{policy['policy_id']}` | "
                f"`{policy['version']}` | `{policy['policy_schema_ref']}` | "
                f"`{policy['release_ref']}` |"
            )
    lines.extend(
        [
            "",
            "## Execution Profiles",
            "",
            (
                "| Profile | Version | Adapter | Transport | Provider | Model | "
                "Reasoning | Output constraint | Release |"
            ),
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for profile in inventory["execution_profiles"]:
        lines.append(
            f"| `{profile['execution_profile_id']}` | "
            f"`{profile['version']}` | `{profile['executor_adapter_id']}` | "
            f"`{profile['transport_kind']}` | `{profile['provider_id']}` | "
            f"`{profile['model_id']}` | `{profile['reasoning_profile']}` | "
            f"`{profile['output_constraint_mode']}` | "
            f"`{profile['release_ref']}` |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            (
                "Prompt bodies, task input, Artifact bodies, tenant content, "
                "credentials, and provider transcripts are intentionally absent."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Print a deterministic Runtime inventory or surface projection."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("inventory-json", "surface-json", "surface-markdown"),
        default="inventory-json",
        help="inspection output format",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="indent JSON for human inspection",
    )
    args = parser.parse_args(argv)
    if args.format == "surface-markdown":
        print(render_runtime_surface_markdown(), end="")
        return 0
    inventory = (
        build_runtime_surface_inventory()
        if args.format == "surface-json"
        else build_runtime_inventory()
    )
    print(
        json.dumps(
            inventory,
            ensure_ascii=False,
            indent=2 if args.pretty else None,
            sort_keys=True,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return 0


__all__ = [
    "INVENTORY_SCHEMA_VERSION",
    "RELEASE_INVENTORY_SCHEMA_VERSION",
    "build_runtime_inventory",
    "build_runtime_release_inventory",
    "build_runtime_surface_inventory",
    "main",
    "render_runtime_release_markdown",
    "render_runtime_surface_markdown",
]


if __name__ == "__main__":
    raise SystemExit(main())
