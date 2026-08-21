"""Generic Native/Postgres backend extension point.

Runtime core exposes only a dependency-probe descriptor. A domain may publish
its own reference executor descriptor from its plugin, but that domain-local
implementation is not a reusable Runtime backend.
"""

from __future__ import annotations

from ..contracts.durability_execution_definition import (
    BackendAdmissionState,
    BackendDescriptor,
    BackendEvaluationRole,
)


NATIVE_POSTGRES_DESCRIPTOR = BackendDescriptor(
    backend_id="native_postgres",
    adapter_contract_version="agent_runtime_backend_v1",
    sdk_package="stdlib",
    admission_state=BackendAdmissionState.DEPENDENCY_PROBE,
    evaluation_role=BackendEvaluationRole.EVALUATION_CANDIDATE,
    implementation_ref=None,
    supports_isolated_namespace=True,
    supports_shared_namespace=False,
    requires_external_service=False,
)
