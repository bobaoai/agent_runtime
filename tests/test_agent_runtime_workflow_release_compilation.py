from __future__ import annotations

from dataclasses import fields, replace

import pytest

from agent_runtime.contracts.registry_release_definition import (
    WorkflowEdge,
    WorkflowNodeKind,
)
from agent_runtime.registry.registry_release_compilation import (
    WorkflowNodeReleaseCandidate,
    WorkflowReleaseCandidate,
    compile_workflow_release,
)


def _candidate() -> WorkflowReleaseCandidate:
    return WorkflowReleaseCandidate(
        workflow_id="synthetic_workflow",
        workflow_version="v1",
        workflow_contract_version="contract_v1",
        owner_contract_ref="host-source:design/synthetic_workflow@v1",
        owner_contract_content="# Synthetic Workflow\n",
        graph_ref="host-content:workflow/synthetic_workflow/graph@v1",
        initial_node_id="produce",
        nodes=(
            WorkflowNodeReleaseCandidate(
                node_id="produce",
                node_kind=WorkflowNodeKind.MODULE,
                module_release_ref="runtime-module:synthetic_module@v1",
                module_release_sha256="a" * 64,
                input_mapping_ref="input-map:synthetic_workflow/produce@v1",
                input_mapping_document={"source": "workflow_input"},
            ),
        ),
        edges=(
            WorkflowEdge(
                source_node_id="produce",
                outcome_id="completed",
                target_node_id=None,
                terminal=True,
            ),
        ),
        authorization_manifest_ref=(
            "authorization-manifest:synthetic_workflow@v1"
        ),
        authorization_manifest_document={
            "required_operation_ids": ["invoke_model"]
        },
        execution_binding_ref=(
            "execution-binding:synthetic_workflow@v1"
        ),
        execution_binding_document={
            "variant_policy_ref": "execution-variant-policy:synthetic@v1"
        },
    )


def test_workflow_candidate_is_path_free_and_compiles_exact_content() -> None:
    forbidden_fragments = ("path", "root", "skill")
    assert not any(
        fragment in field.name
        for field in fields(WorkflowReleaseCandidate)
        for fragment in forbidden_fragments
    )

    release = compile_workflow_release(_candidate())

    assert release.release_ref == "runtime-workflow:synthetic_workflow@v1"
    assert release.nodes[0].input_mapping_sha256 is not None
    assert release.owner_contract_sha256 != "0" * 64
    release.validate()


def test_workflow_candidate_replay_is_byte_identical() -> None:
    first = compile_workflow_release(_candidate())
    second = compile_workflow_release(_candidate())

    assert second.as_dict() == first.as_dict()


def test_workflow_hash_domains_follow_exact_supplied_content() -> None:
    candidate = _candidate()
    original = compile_workflow_release(candidate)
    revised_mapping = compile_workflow_release(
        replace(
            candidate,
            nodes=(
                replace(
                    candidate.nodes[0],
                    input_mapping_document={"source": "revised_input"},
                ),
            ),
        )
    )
    revised_authorization = compile_workflow_release(
        replace(
            candidate,
            authorization_manifest_document={
                "required_operation_ids": ["invoke_model", "query_data"]
            },
        )
    )

    assert revised_mapping.graph_sha256 != original.graph_sha256
    assert revised_mapping.release_sha256 != original.release_sha256
    assert revised_authorization.graph_sha256 == original.graph_sha256
    assert revised_authorization.release_sha256 != original.release_sha256


def test_control_node_cannot_smuggle_input_mapping_content() -> None:
    candidate = _candidate()
    invalid_node = replace(
        candidate.nodes[0],
        node_kind=WorkflowNodeKind.CONTROL,
        module_release_ref=None,
        module_release_sha256=None,
    )

    with pytest.raises(ValueError, match="control node"):
        compile_workflow_release(replace(candidate, nodes=(invalid_node,)))
