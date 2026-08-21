"""Compile domain-owned graph authorities into immutable backend projections.

The compiler is the only place that translates a registered domain adjacency
map into the generic state rows persisted by a durable backend.  Backends may
validate and follow the projection; they do not define or repair domain edges.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from enum import Enum
from typing import Any

from ..contracts import (
    WorkflowRuntimeRegistration,
    validate_domain_runtime_manifest,
)
from ..contracts.registry_release_definition import WorkflowRelease
from .registry_workflow_registration import resolve_registration_reference
from ..contracts.durability_execution_definition import (
    GraphStateProjection,
    WorkflowGraphProjection,
)


GRAPH_PROJECTION_VERSION = "agent_runtime_graph_projection_v1"
WORKFLOW_RELEASE_GRAPH_PROJECTION_VERSION = (
    "agent_runtime_workflow_release_graph_projection_v1"
)
RUNTIME_TERMINAL_STATE_ID = "completed"


def _state_id(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def project_graph_authority(
    registration: WorkflowRuntimeRegistration,
    graph_authority: Mapping[Any, Any],
) -> WorkflowGraphProjection:
    """Normalize one explicitly supplied authority for tests or compilation."""

    registration.validate()
    if not registration.executable or registration.initial_state is None:
        raise ValueError("graph projection requires an executable registration")
    if not isinstance(graph_authority, Mapping) or not graph_authority:
        raise ValueError("graph authority must be a non-empty mapping")

    normalized: dict[str, tuple[str, ...]] = {}
    for raw_state, raw_targets in graph_authority.items():
        state_id = _state_id(raw_state)
        if state_id in normalized:
            raise ValueError(f"duplicate normalized graph state: {state_id}")
        if isinstance(raw_targets, str) or not isinstance(
            raw_targets,
            (set, frozenset, tuple, list),
        ):
            raise ValueError(f"graph targets must be a finite sequence: {state_id}")
        normalized[state_id] = tuple(
            sorted(_state_id(target) for target in raw_targets)
        )

    states = tuple(
        GraphStateProjection(
            state_id=state_id,
            allowed_next_state_ids=targets,
        )
        for state_id, targets in sorted(normalized.items())
    )
    terminal_states = tuple(
        state.state_id for state in states if not state.allowed_next_state_ids
    )
    unhashed = WorkflowGraphProjection(
        projection_version=GRAPH_PROJECTION_VERSION,
        workflow_id=registration.workflow_id,
        workflow_contract_version=registration.contract_version,
        graph_authority_ref=registration.graph_authority_ref,
        initial_state=registration.initial_state,
        terminal_states=terminal_states,
        states=states,
        graph_sha256="0" * 64,
    )
    projection = replace(
        unhashed,
        graph_sha256=unhashed.computed_sha256(),
    )
    projection.validate()
    return projection


def compile_registered_graph(
    registration: WorkflowRuntimeRegistration,
) -> WorkflowGraphProjection:
    """Resolve and compile a graph whose states match the domain manifest."""

    authority = resolve_registration_reference(registration.graph_authority_ref)
    if not isinstance(authority, Mapping):
        raise TypeError("registered graph authority must resolve to a mapping")
    projection = project_graph_authority(registration, authority)
    if registration.domain_manifest_ref is None:
        raise ValueError("executable graph compilation requires a domain manifest")
    raw_manifest = resolve_registration_reference(registration.domain_manifest_ref)
    if not isinstance(raw_manifest, Mapping):
        raise TypeError("registered domain manifest must resolve to a mapping")
    manifest = validate_domain_runtime_manifest(
        raw_manifest,
        expected_contract_version=registration.contract_version,
        expected_initial_state=registration.initial_state,
    )
    declared_states = set(manifest["state_ids"])
    projected_states = {state.state_id for state in projection.states}
    if declared_states != projected_states:
        raise ValueError("domain manifest state_ids do not match graph projection")
    return projection


def project_workflow_release_graph(
    release: WorkflowRelease,
) -> WorkflowGraphProjection:
    """Project the target Workflow Release into a backend cursor graph.

    Terminal outcome edges converge on one Runtime control state.  The state is
    not a Module and therefore does not create a fake Module registration.
    Outcome semantics remain in the exact Workflow Release and domain trace;
    the durable backend receives only legal state adjacency.
    """

    if type(release) is not WorkflowRelease:
        raise ValueError("release must be an exact WorkflowRelease")
    release.validate()
    node_ids = {node.node_id for node in release.nodes}
    if RUNTIME_TERMINAL_STATE_ID in node_ids:
        raise ValueError("Workflow Release collides with Runtime terminal state")
    targets_by_state: dict[str, set[str]] = {
        node_id: set() for node_id in node_ids
    }
    targets_by_state[RUNTIME_TERMINAL_STATE_ID] = set()
    for edge in release.edges:
        targets_by_state[edge.source_node_id].add(
            RUNTIME_TERMINAL_STATE_ID
            if edge.terminal
            else str(edge.target_node_id)
        )
    states = tuple(
        GraphStateProjection(
            state_id=state_id,
            allowed_next_state_ids=tuple(sorted(targets)),
        )
        for state_id, targets in sorted(targets_by_state.items())
    )
    provisional = WorkflowGraphProjection(
        projection_version=WORKFLOW_RELEASE_GRAPH_PROJECTION_VERSION,
        workflow_id=release.workflow_id,
        workflow_contract_version=release.workflow_contract_version,
        graph_authority_ref=release.graph_ref,
        initial_state=release.initial_node_id,
        terminal_states=(RUNTIME_TERMINAL_STATE_ID,),
        states=states,
        graph_sha256="0" * 64,
    )
    projection = replace(
        provisional,
        graph_sha256=provisional.computed_sha256(),
    )
    projection.validate()
    return projection


__all__ = [
    "GRAPH_PROJECTION_VERSION",
    "RUNTIME_TERMINAL_STATE_ID",
    "WORKFLOW_RELEASE_GRAPH_PROJECTION_VERSION",
    "compile_registered_graph",
    "project_graph_authority",
    "project_workflow_release_graph",
]
