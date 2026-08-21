from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from agent_runtime.contracts import (
    WorkflowAdmissionState,
    WorkflowRuntimeRegistration,
    validate_domain_runtime_manifest,
)
from agent_runtime.registry.registry_graph_projection import compile_registered_graph
from agent_runtime.registry.registry_plugin_registration import (
    DomainRuntimePlugin,
    register_runtime_plugin,
)
from agent_runtime.registry.registry_workflow_registration import WorkflowRuntimeRegistry


OPAQUE_GRAPH = MappingProxyType(
    {
        "state_alpha": frozenset({"state_omega"}),
        "state_omega": frozenset(),
    }
)
OPAQUE_MANIFEST = MappingProxyType(
    {
        "manifest_version": "opaque_manifest_v1",
        "contract_version": "opaque_contract_v1",
        "state_ids": ("state_alpha", "state_omega"),
        "module_ids": ("module_delta",),
        "artifact_kind_ids": ("artifact_sigma",),
        "evaluation_binding_ids": ("evaluation_tau",),
        "execution_profile_ids": ("profile_kappa",),
    }
)
MISMATCHED_OPAQUE_MANIFEST = MappingProxyType(
    {
        **OPAQUE_MANIFEST,
        "state_ids": ("state_alpha", "state_extra", "state_omega"),
    }
)


class OpaqueStore:
    """Import-resolvable store sentinel for the synthetic plugin fixture."""


def opaque_driver(**_: Any) -> dict[str, str]:
    """Return a content-free sentinel from the synthetic plugin fixture."""

    return {"status": "synthetic"}


def _opaque_registration(*, workflow_id: str) -> WorkflowRuntimeRegistration:
    module_ref = __name__
    return WorkflowRuntimeRegistration(
        workflow_id=workflow_id,
        domain="domain_zeta",
        registration_version="registration_v1",
        contract_version="opaque_contract_v1",
        intent_ref="contract-ref:opaque_intent_v1",
        graph_authority_ref=f"{module_ref}:OPAQUE_GRAPH",
        admission_state=WorkflowAdmissionState.SHADOW_EXECUTABLE,
        capabilities=frozenset({"artifact_lineage", "cyclic_graph"}),
        entitlement_mode="frozen_for_execution",
        domain_manifest_ref=f"{module_ref}:OPAQUE_MANIFEST",
        initial_state="state_alpha",
        driver_ref=f"{module_ref}:opaque_driver",
        store_ref=f"{module_ref}:OpaqueStore",
        default_backend_id="backend_zeta",
        allowed_backend_ids=("backend_zeta",),
    )


def test_opaque_plugin_registers_and_projects_graph() -> None:
    registration = _opaque_registration(workflow_id="workflow_zeta")
    plugin = DomainRuntimePlugin(
        plugin_id="plugin_zeta",
        plugin_version="plugin_v1",
        registrations=(registration,),
    )
    registry = WorkflowRuntimeRegistry()

    register_runtime_plugin(registry, plugin)
    manifest = registry.resolve_domain_manifest(registration.workflow_id)
    projection = compile_registered_graph(registration)
    assert tuple(registry.all()) == ("workflow_zeta",)
    assert manifest["state_ids"] == ("state_alpha", "state_omega")
    assert tuple(state.state_id for state in projection.states) == manifest["state_ids"]
    assert projection.allowed_targets("state_alpha") == ("state_omega",)


def test_plugin_registration_is_atomic_when_one_workflow_collides() -> None:
    registry = WorkflowRuntimeRegistry()
    registry.register(_opaque_registration(workflow_id="workflow_zeta"))
    plugin = DomainRuntimePlugin(
        plugin_id="plugin_zeta",
        plugin_version="plugin_v2",
        registrations=(
            _opaque_registration(workflow_id="workflow_eta"),
            _opaque_registration(workflow_id="workflow_zeta"),
        ),
    )

    with pytest.raises(ValueError, match="already registered"):
        register_runtime_plugin(registry, plugin)

    assert tuple(registry.all()) == ("workflow_zeta",)


def test_manifest_validation_rejects_missing_initial_state_and_duplicate_ids() -> None:
    missing_initial = dict(OPAQUE_MANIFEST)
    missing_initial["state_ids"] = ("state_omega",)
    with pytest.raises(ValueError, match="initial_state"):
        validate_domain_runtime_manifest(
            missing_initial,
            expected_contract_version="opaque_contract_v1",
            expected_initial_state="state_alpha",
        )

    duplicate_modules = dict(OPAQUE_MANIFEST)
    duplicate_modules["module_ids"] = (
        "module_delta",
        "module_delta",
    )
    with pytest.raises(ValueError, match="module_ids must be unique"):
        validate_domain_runtime_manifest(
            duplicate_modules,
            expected_contract_version="opaque_contract_v1",
        )


def test_graph_compilation_rejects_manifest_state_drift() -> None:
    registration = replace(
        _opaque_registration(workflow_id="workflow_zeta"),
        domain_manifest_ref=f"{__name__}:MISMATCHED_OPAQUE_MANIFEST",
    )

    with pytest.raises(ValueError, match="state_ids do not match"):
        compile_registered_graph(registration)


def test_registration_accepts_dotted_capabilities_and_rejects_bad_module_refs() -> None:
    registration = _opaque_registration(workflow_id="workflow_zeta")
    dotted = replace(
        registration,
        capabilities=frozenset({"research.theme", "digestion.source_extraction"}),
    )
    dotted.validate()

    for malformed_ref in (
        "package..module:GRAPH",
        "package.module:GRAPH..value",
        "package.module.:GRAPH",
        "package.module:GRAPH.",
    ):
        malformed = replace(
            registration,
            graph_authority_ref=malformed_ref,
        )
        with pytest.raises(ValueError, match="graph_authority_ref"):
            malformed.validate()


def test_runtime_core_imports_only_runtime_modules_from_project_namespace() -> None:
    runtime_root = Path(__file__).resolve().parents[1] / "src" / "agent_runtime"
    violations: list[str] = []

    for path in sorted(runtime_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported_modules: tuple[str, ...] = ()
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules = (node.module,)
            elif isinstance(node, ast.Import):
                imported_modules = tuple(alias.name for alias in node.names)
            for module_name in imported_modules:
                if module_name == "src" or module_name.startswith("src."):
                    violations.append(
                        f"{path.relative_to(runtime_root)}:{module_name}"
                    )

    assert violations == []


def test_standalone_workflow_registry_has_no_builtin_domain_workflows() -> None:
    registry = WorkflowRuntimeRegistry()

    assert registry.all() == {}
