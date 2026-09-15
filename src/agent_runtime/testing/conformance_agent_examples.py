"""Target-independent definitions for the Runtime's bounded Agent examples.

These ordinary Modules own example tasks and schemas, not provider assembly or
business governance. Building an example performs no IO, registration, model
selection, or execution. The execution host supplies fixture tools and exact
per-node Profiles separately.
"""
from __future__ import annotations

import hashlib
import json

from ..contracts.registry_release_definition import (
    ModuleEntryPolicy, ModuleExecutionRequirements, OutputResolutionPolicy,
    WorkflowEdge, WorkflowNodeKind, WorkflowParallelGroupBinding,
    WorkflowParallelJoinPolicy,
)
from ..registry.registry_module_authoring import Module, ModuleExport
from ..registry.registry_module_loading import ModuleRegistrationSource
from ..registry.registry_release_compilation import (
    WorkflowNodeReleaseCandidate, WorkflowReleaseCandidate,
)
from ..registry.registry_workflow_authoring import Workflow, WorkflowExport


EXAMPLE_NAMES = ("agent_capability_example", "agent_evaluation_example")
_VERSION = "v1"
_OWNER = """# Runtime Agent capability examples

Use only the provided test input, explicit fixture tools and private workspace.
Return the declared structured output. A model response does not prove a tool
was called: the execution host collects actual Runtime logs. These examples do
not authorize production data access. Task-internal review is requested by the
tested Agent itself; independent evaluation consumes separately captured facts.
"""
_OWNER_REF = "owner-contract-sha256:" + hashlib.sha256(_OWNER.encode()).hexdigest()


def _object(**properties):
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


_TEXT = {"type": "string"}
_TEXTS = {"type": "array", "items": _TEXT}
_REVIEW_INPUT = _object(draft=_TEXT, required_facts=_TEXTS)
_REVIEW_OUTPUT = _object(accepted={"type": "boolean"}, reason=_TEXT)


def _module(name, instruction, input_schema, output_schema, *, tools=(), draft=False):
    module_id = "capability_" + name
    input_ref, output_ref = f"schema:{module_id}_input@v1", f"schema:{module_id}_output@v1"
    def schema(ref, body):
        return json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema",
                           "$id": ref, **body}, sort_keys=True, separators=(",", ":"))
    source = ModuleRegistrationSource(
        skill_id="runtime-capability-examples", module_id=module_id,
        owner_contract_ref=_OWNER_REF, owner_contract_content=_OWNER,
        instruction_text=instruction,
        input_schema_ref=input_ref, input_schema_document=schema(input_ref, input_schema),
        output_schema_ref=output_ref, output_schema_document=schema(output_ref, output_schema),
        schema_version="runtime_module_registration_v4",
    )
    requirements = ModuleExecutionRequirements(
        context_isolation="workflow_execution_isolated",
        execution_mode="agent" if tools or draft else "tool_free",
        semantic_input_delivery_mode="inline",
        attempt_workspace_policy="own_draft_read_write" if draft else "none",
        tool_policy=tuple(tools), gateway_access_reasons=(), network_policy="denied",
        output_constraint_mode="native_structured_output", timeout_seconds=300,
        max_attempts=1,
    )
    return Module(source, execution_requirements=requirements,
                  entry_policy=ModuleEntryPolicy.STANDALONE_ALLOWED,
                  output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE).export(module_version=_VERSION)


def build_example_reviewer() -> ModuleExport:
    """Build the fixed task-internal review target, independently of a model.

    Its ordinary output schema reports content acceptance. It is not a Portable
    governance Reviewer or a production approval, and has no callback tools.
    A host may bind this exact export to the tested Agent's review_candidate
    tool. The Agent can submit content but cannot select another release.
    """
    return _module("task_reviewer",
        "Review the submitted draft against every required fact. Accept only if all facts are accurately present. "
        "Return accepted and a concise reason. Do not write a replacement draft.",
        _REVIEW_INPUT, _REVIEW_OUTPUT)


def _workflow(name, modules, edges, *, parallel_groups=()):
    nodes = tuple(WorkflowNodeReleaseCandidate(
        node_id=node_id, node_kind=WorkflowNodeKind.MODULE,
        module_release_ref=exported.module_release.release_ref,
        module_release_sha256=exported.module_release.release_sha256,
        input_mapping_ref=f"input-map:{name}/{node_id}@v1",
        input_mapping_document={"example": name, "node": node_id, "version": _VERSION},
    ) for node_id, exported in modules.items())
    if parallel_groups:
        nodes += tuple(WorkflowNodeReleaseCandidate(
            node_id=group.control_node_id, node_kind=WorkflowNodeKind.CONTROL,
            module_release_ref=None, module_release_sha256=None,
            input_mapping_ref=None, input_mapping_document=None,
        ) for group in parallel_groups)
    candidate = WorkflowReleaseCandidate(
        workflow_id=name, workflow_version=_VERSION, workflow_contract_version="contract_v1",
        owner_contract_ref=_OWNER_REF, owner_contract_content=_OWNER,
        graph_ref=f"workflow-graph:{name}@v1", initial_node_id=next(iter(modules)),
        nodes=nodes, edges=tuple(WorkflowEdge(
            source_node_id=source, outcome_id=outcome, target_node_id=target,
            terminal=target is None) for source, outcome, target in edges),
        parallel_groups=parallel_groups,
        authorization_manifest_ref=f"authorization-manifest:{name}@v1",
        authorization_manifest_document={"required_operation_ids": ["model_execute"]},
        execution_binding_ref=f"execution-binding:{name}@v1",
        execution_binding_document={"schema_version": "workflow_execution_binding_v1",
                                    "workflow_id": name, "variant_policy_family": "execution_variant_policy"},
    )
    return Workflow.from_graph(candidate, module_exports=tuple(modules.values())).export()


def build_agent_capability_example() -> WorkflowExport:
    """Build the fixed Context/Query/Writer/parallel-Review/Selector graph.

    Returns:
        An immutable WorkflowExport containing all six Module definitions and
        their schemas, prompts and policies, without Profiles or target roots.
        The selector can finish, revisit the writer, or wait at its current
        state for the host's matching material_ready event before revisiting it.
    Effects:
        Pure construction only. The fixture_read capability requires a trusted
        host-supplied tool; the graph does not enable production Gateway access.
    """
    modules = {
        "context_reader": _module("context_reader",
            "Read the exact task and return a concise summary, preserving the required facts.",
            _object(task=_TEXT, required_facts=_TEXTS), _object(summary=_TEXT, required_facts=_TEXTS)),
        "gateway_researcher": _module("gateway_researcher",
            "Use fixture_read with the provided fixture_key to obtain the actual fixture facts. "
            "Return only facts obtained from that tool. Do not substitute assumptions or file reads.",
            _object(summary=_TEXT, fixture_key=_TEXT), _object(facts=_TEXTS), tools=("fixture_read",)),
        "draft_writer": _module("draft_writer",
            "Write a concise note containing every supplied fact. Use your permitted private scratch workspace "
            "to write and check a draft, then return its complete text in draft. Apply supplied review feedback.",
            _object(facts=_TEXTS, feedback=_TEXTS), _object(draft=_TEXT),
            tools=("read", "search", "shell"), draft=True),
        "reviewer_a": _module("reviewer_a",
            "Independently check every required fact against the draft. Return accepted and the reason. "
            "Do not assume another reviewer has checked it.", _REVIEW_INPUT, _REVIEW_OUTPUT),
        "reviewer_b": _module("reviewer_b",
            "Independently check the draft for contradictions, missing facts and unsupported additions. "
            "Return accepted and the reason. Do not rewrite the draft.", _REVIEW_INPUT, _REVIEW_OUTPUT),
        "selector": _module("selector",
            "Consume both actual reviews. When requested_action is wait, request wait_for_external_event. "
            "When requested_action is revise, request revision_required with feedback. Otherwise accept only "
            "if both reviewers accepted; if either rejected, request revision_required. Explain the decision.",
            _object(draft=_TEXT, review_a=_REVIEW_OUTPUT, review_b=_REVIEW_OUTPUT,
                    requested_action={"type": "string", "enum": ["decide", "revise", "wait"]}),
            _object(decision={"type": "string", "enum": ["accepted", "revision_required", "wait_for_external_event"]},
                    reason=_TEXT)),
    }
    return _workflow(EXAMPLE_NAMES[0], modules, (
        ("context_reader", "completed", "gateway_researcher"),
        ("gateway_researcher", "completed", "draft_writer"),
        ("draft_writer", "completed", "parallel_reviews"),
        ("parallel_reviews", "all_completed", "selector"),
        ("reviewer_a", "completed", "selector"), ("reviewer_b", "completed", "selector"),
        ("selector", "accepted", None), ("selector", "revision_required", "draft_writer"),
        ("selector", "material_ready", "draft_writer"),
    ), parallel_groups=(WorkflowParallelGroupBinding(
        group_id="independent_reviews", control_node_id="parallel_reviews",
        branch_node_ids=("reviewer_a", "reviewer_b"), join_node_id="selector",
        completion_outcome_id="all_completed", join_policy=WorkflowParallelJoinPolicy.ALL_REQUIRED),))


def build_agent_evaluation_example() -> WorkflowExport:
    """Build tested-Agent then independent-evaluator definitions without IO.

    The host mechanically constructs the second node's evidence input from
    committed Runtime facts. Both normal completion and actual task failure
    route to evaluation; whole-Workflow cancellation stops before that node.
    The separate build_example_reviewer export is a host-bound tool target,
    deliberately not a graph node the outer coordinator could call for the Agent.
    """
    return _workflow(EXAMPLE_NAMES[1], {
        "tested_agent": _module("tested_agent",
            "Follow the supplied task and method. Write a concise candidate containing the required facts. "
            "You must call review_candidate yourself with input_payload containing draft and required_facts. "
            "Inspect its actual response. If accepted, return the reviewed candidate unchanged; otherwise "
            "report the feedback and task_completed false. Never claim a tool was called if it was not.",
            _object(task=_TEXT, method=_TEXT, required_facts=_TEXTS),
            _object(candidate=_TEXT, review_feedback=_TEXT, task_completed={"type": "boolean"}),
            tools=("read", "search", "shell", "review_candidate"), draft=True),
        "evaluation_agent": _module("evaluation_agent",
            "Evaluate the tested Agent against the predeclared criteria using only supplied Runtime evidence. "
            "Distinguish technical failure from business rejection and identify missing facts. Agent self-report "
            "does not prove tool use. Mark reviewer_called only when actual recorded child execution exists; "
            "task_completed requires the final candidate to match the accepted reviewed content.",
            _object(criteria=_TEXTS, evidence={"type": "object"}),
            _object(task_completed={"type": "boolean"}, evidence_sufficient={"type": "boolean"},
                    reviewer_called={"type": "boolean"}, assessment=_TEXT)),
    }, (("tested_agent", "completed", "evaluation_agent"),
        ("tested_agent", "failed", "evaluation_agent"), ("evaluation_agent", "completed", None)))
