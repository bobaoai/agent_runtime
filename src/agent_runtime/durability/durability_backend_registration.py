"""Temporal backend metadata for the selected durable-runtime candidate.

The legacy two-Cell workflow passed real integration and recovery tests, but
the generic acknowledged-Update adapter has not yet passed the full two-Cell
conformance suite. The backend therefore remains integration-tested until that
evidence exists.
"""

from __future__ import annotations

from importlib.util import find_spec

from ..contracts.durability_execution_definition import (
    BackendAdmissionState,
    BackendDescriptor,
    BackendEvaluationRole,
)


TEMPORAL_DESCRIPTOR = BackendDescriptor(
    backend_id="temporal",
    adapter_contract_version="agent_runtime_backend_v1",
    sdk_package="temporalio>=1.31",
    admission_state=BackendAdmissionState.INTEGRATION_TESTED,
    evaluation_role=BackendEvaluationRole.SELECTED_CANDIDATE,
    implementation_ref=(
        "agent_runtime.durability.durability_backend_registration:"
        "load_temporal_workflow_release_adapter"
    ),
    supports_isolated_namespace=True,
    supports_shared_namespace=True,
    requires_external_service=True,
)


def load_temporal_workflow_release_adapter():
    """Load the selected adapter only after the optional SDK is available."""

    if not temporal_sdk_available():
        raise RuntimeError(
            "Temporal adapter requires the agent-runtime-core[temporal] extra"
        )
    from .durability_temporal_coordination import (
        TemporalWorkflowReleaseBackendAdapter,
    )

    return TemporalWorkflowReleaseBackendAdapter


def temporal_sdk_available() -> bool:
    """Return whether the Temporal SDK dependency is importable."""

    return find_spec("temporalio") is not None
