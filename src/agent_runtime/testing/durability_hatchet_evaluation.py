"""Hatchet metadata retained only as a rejected product-decision record.

The product decision selected Temporal as the sole production candidate. No
Hatchet runtime, SDK dependency, or production operations surface is shipped by
Agent Runtime.
"""

from __future__ import annotations

from ..contracts.durability_execution_definition import (
    BackendAdmissionState,
    BackendDescriptor,
    BackendEvaluationRole,
)


HATCHET_DESCRIPTOR = BackendDescriptor(
    backend_id="hatchet",
    adapter_contract_version="agent_runtime_backend_v1",
    sdk_package="not-installed",
    admission_state=BackendAdmissionState.REJECTED,
    evaluation_role=BackendEvaluationRole.REJECTED_CANDIDATE,
    implementation_ref=None,
    supports_isolated_namespace=True,
    supports_shared_namespace=True,
    requires_external_service=True,
)
