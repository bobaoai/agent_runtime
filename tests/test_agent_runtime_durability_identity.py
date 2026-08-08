from __future__ import annotations

from agent_runtime.contracts.durability_topology_definition import (
    BackendExecutionRef,
)


def test_backend_execution_ref_accepts_public_host_id_grammar() -> None:
    reference = BackendExecutionRef(
        backend_id="temporal",
        backend_namespace="runtime.production",
        backend_execution_id="wf-exec-001",
        workflow_execution_id="wf-exec-001",
    )

    reference.validate()
