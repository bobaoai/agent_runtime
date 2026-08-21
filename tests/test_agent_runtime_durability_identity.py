from __future__ import annotations

from dataclasses import fields

from agent_runtime.contracts import durability_execution_definition
from agent_runtime.contracts.durability_execution_definition import (
    BackendExecutionRef,
    DurableExecutionBinding,
)


def test_backend_execution_ref_accepts_public_host_id_grammar() -> None:
    reference = BackendExecutionRef(
        backend_id="temporal",
        backend_namespace="runtime.production",
        backend_execution_id="wf-exec-001",
        workflow_execution_id="wf-exec-001",
    )

    reference.validate()


def test_durable_execution_binding_contains_only_runtime_scope_and_backend_refs(
) -> None:
    assert tuple(field.name for field in fields(DurableExecutionBinding)) == (
        "tenant_id",
        "cell_id",
        "backend_id",
        "backend_namespace",
        "backend_endpoint_ref",
        "backend_persistence_ref",
    )
    binding = DurableExecutionBinding(
        tenant_id="tenant-alpha",
        cell_id="cell-alpha",
        backend_id="temporal",
        backend_namespace="runtime.production",
        backend_endpoint_ref="temporal-endpoint:alpha",
        backend_persistence_ref="temporal-persistence:alpha",
    )

    binding.validate()


def test_runtime_durability_contract_excludes_host_topology_authority() -> None:
    for forbidden_name in (
        "Region",
        "LlmSupply",
        "DeploymentMode",
        "CellRuntimeBinding",
        "PrincipalContext",
        "TenantRouter",
        "assert_dedicated_cell_isolation",
    ):
        assert not hasattr(durability_execution_definition, forbidden_name)
