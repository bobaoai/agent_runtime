"""Conformance tests for the generic agentic workflow package contract."""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_runtime.contracts import (
    AgenticWorkflowConformancePackage,
    ModuleInputProjection,
    ModuleInputProjectionContract,
    ConformanceContractBinding,
    ConformanceContractKind,
    DynamicInputBinding,
    WorkflowManagementLifecycle,
)


_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _binding(kind: ConformanceContractKind) -> ConformanceContractBinding:
    return ConformanceContractBinding(
        contract_kind=kind,
        artifact_ref=f"artifact://contracts/{kind.value}",
        artifact_sha256=_HASH_A,
    )


def _package() -> AgenticWorkflowConformancePackage:
    projection = ModuleInputProjectionContract(
        module_id="worker",
        module_release_ref="artifact://modules/worker_v1",
        module_release_sha256=_HASH_B,
        projected_contract_kinds=(
            ConformanceContractKind.INTENT,
            ConformanceContractKind.ARTIFACT_SCHEMA_INDEX,
            ConformanceContractKind.CONTEXT_AND_DATA_BOUNDARY,
        ),
        input_schema_ref="schema://worker/input_v1",
        input_schema_sha256=_HASH_A,
        output_schema_ref="schema://worker/output_v1",
        output_schema_sha256=_HASH_B,
        declared_operation_ids=("model_invoke", "knowledge_read"),
        allows_dynamic_materials=True,
    )
    package = AgenticWorkflowConformancePackage(
        package_id="opaque_workflow_package",
        package_version="v1",
        workflow_id="opaque_workflow",
        workflow_contract_version="v1",
        lifecycle=WorkflowManagementLifecycle.MIGRATION_PLANNED,
        contract_bindings=tuple(
            _binding(kind) for kind in ConformanceContractKind
        ),
        module_input_projections=(projection,),
        package_sha256="0" * 64,
    )
    return replace(package, package_sha256=package.calculate_sha256())


def _module_input(
    package: AgenticWorkflowConformancePackage,
) -> ModuleInputProjection:
    module = package.module("worker")
    by_kind = {
        binding.contract_kind: binding for binding in package.contract_bindings
    }
    projection = ModuleInputProjection(
        workflow_execution_id="execution_1",
        module_run_id="module_1",
        variant_id="variant_1",
        conformance_package_id=package.package_id,
        conformance_package_sha256=package.package_sha256,
        module_id=module.module_id,
        module_release_ref=module.module_release_ref,
        module_release_sha256=module.module_release_sha256,
        static_contract_bindings=tuple(
            by_kind[kind] for kind in module.projected_contract_kinds
        ),
        dynamic_input_bindings=(
            DynamicInputBinding(
                logical_name="source_package",
                artifact_ref="artifact://inputs/source_1",
                artifact_sha256=_HASH_A,
                schema_ref="schema://source/read_v1",
                schema_sha256=_HASH_B,
            ),
        ),
        authorized_operation_ids=("model_invoke",),
        input_closure_sha256="0" * 64,
    )
    return replace(
        projection,
        input_closure_sha256=projection.calculate_sha256(),
    )


def test_complete_package_and_least_privilege_projection_validate() -> None:
    package = _package()
    projection = _module_input(package)

    package.validate()
    projection.validate_against(package)


def test_package_rejects_missing_required_contract_family() -> None:
    package = _package()
    incomplete = replace(
        package,
        contract_bindings=package.contract_bindings[:-1],
    )
    incomplete = replace(
        incomplete,
        package_sha256=incomplete.calculate_sha256(),
    )

    with pytest.raises(ValueError, match="incomplete conformance contract closure"):
        incomplete.validate()


def test_module_projection_rejects_extra_control_plane_material() -> None:
    package = _package()
    projection = _module_input(package)
    extra = next(
        binding
        for binding in package.contract_bindings
        if binding.contract_kind is ConformanceContractKind.GRAPH
    )
    polluted = replace(
        projection,
        static_contract_bindings=(*projection.static_contract_bindings, extra),
    )
    polluted = replace(
        polluted,
        input_closure_sha256=polluted.calculate_sha256(),
    )

    with pytest.raises(ValueError, match="missing, extra, or reordered"):
        polluted.validate_against(package)


def test_module_projection_rejects_undeclared_operation() -> None:
    package = _package()
    projection = replace(
        _module_input(package),
        authorized_operation_ids=("publication_write",),
    )
    projection = replace(
        projection,
        input_closure_sha256=projection.calculate_sha256(),
    )

    with pytest.raises(ValueError, match="undeclared operations"):
        projection.validate_against(package)


def test_module_projection_rejects_cross_package_hash() -> None:
    package = _package()
    projection = replace(
        _module_input(package),
        conformance_package_sha256="f" * 64,
    )
    projection = replace(
        projection,
        input_closure_sha256=projection.calculate_sha256(),
    )

    with pytest.raises(ValueError, match="crossed conformance package"):
        projection.validate_against(package)
