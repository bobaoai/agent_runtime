"""Run capability-verification examples through Runtime's graph and Module ports."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from threading import Event, Lock

from ..contracts.durability_topology_definition import ExternalEvent
from ..contracts.execution_module_definition import ModuleInputBinding
from ..contracts.invocation_adapter_definition import SelfTestResourceUnavailableError
from ..contracts.registry_workflow_definition import ModuleOutcome, ModuleOutcomeDisposition
from ..execution.execution_content_staging import InMemoryCellArtifactStore
from ..ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger
from ..registry.registry_graph_projection import RUNTIME_TERMINAL_STATE_ID
from ..registry.registry_module_authoring import Module
from ..registry.registry_plugin_registration import RuntimeModulePlugin, register_runtime_module_plugin
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from ..invocation.invocation_tool_definition import ProviderToolDefinition
from .conformance_agent_examples import (
    EXAMPLE_NAMES, build_agent_capability_example, build_agent_evaluation_example, build_example_reviewer,
)

__all__ = ["run_agent_example"]


_FACTS = ("Alpha has 2 units.", "Beta has 3 units.", "The combined total is 5 units.")
_TASK = "Create a concise note containing all supplied facts without adding unsupported claims."
_METHOD = "Write a candidate, call review_candidate yourself, inspect the actual response, and return the exact reviewed candidate."
_WAIT_POLICY = {
    "policy_ref": "wait-policy:agent_capability_example/material_ready@v1",
    "expected_state": "selector", "event_type": "material_ready", "target_state": "draft_writer",
}
_CRITERIA = (
    "The final candidate satisfies the task and preserves the supplied facts.",
    "The tested Agent itself made an actual review_candidate tool call; an outer call or self-report is insufficient.",
    "The recorded child completed and accepted the exact final candidate, with no missing required evidence.",
    "A technical failure or incomplete evidence cannot be reported as task completion.",
)
_GRAPH_CRITERIA = (
    "The query node actually reads the explicitly supplied fixture through its tool.",
    "The draft preserves the facts and both independent review branches complete before selection.",
    "The selector follows the supplied first-visit scenario and the graph's declared edges.",
    "Waiting resumes only after the host supplies a matching event in the same live execution.",
)


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@contextmanager
def _retain_cleanup_result(record):
    """Preserve already captured execution facts when owned cleanup fails."""
    try:
        yield
    except Exception as exc:
        if not record:
            raise
        record.update(status="failed", resources_cleaned=False,
                      cleanup_failure={"error_type": type(exc).__name__, "detail": str(exc)})
    else:
        record["resources_cleaned"] = True


def _latest(records, node):
    matches = [row for row in records if row["dispatch"]["current_state_id"] == node]
    if not matches:
        raise ValueError(f"example input lacks committed predecessor {node}")
    return matches[-1]


def _evidence(record):
    """Select actual Runtime facts for evaluation; full raw records remain returned."""
    return {
        "workflow_execution_id": record["workflow_execution_id"],
        "module_run_id": record["module_run_id"],
        "module_release_ref": record["module_release_ref"],
        "module_release_sha256": record["module_release_sha256"],
        "execution_profile_ref": record["execution_profile_ref"],
        "execution_profile_sha256": record["execution_profile_sha256"],
        "input_closure_sha256": record["input_closure_sha256"],
        "status": record["status"], "output": deepcopy(record["output"]),
        "failure_detail": deepcopy(record["failure_detail"]),
        "log_complete": record["execution_log"]["complete"],
        "attempt_ids": [row["attempt_id"] for row in record["execution_trace"]["attempts"]],
        "tool_calls": deepcopy(record["execution_log"]["tool_calls"]),
    }


class _ExampleToolFactory:
    """One fixed fixture/child callback with an independently closed Attempt scope."""

    def __init__(self, definition, invoke):
        self.definitions = (definition,)
        self._invoke = invoke

    def open_session(self, request):
        factory = self
        class Session:
            definitions = factory.definitions
            observations = ()
            def __init__(self):
                self.request = request
                self.closed = Event()
            def operation_intent(self, *_):
                raise PermissionError("example tools have no production operation authority")
            def invoke(self, name, payload, authorization):
                if authorization is not None:
                    raise PermissionError("example tool accepts only its actual test-resource boundary")
                if self.closed.is_set():
                    raise SelfTestResourceUnavailableError("example tool scope is closed")
                if name != self.definitions[0].tool_name:
                    raise ValueError("tool differs from the fixed example target")
                return factory._invoke(self, deepcopy(payload))
            def validate_completion(self):
                # Runtime checks completed IPC records after closing the server.
                # This synchronous fixture has no additional required operation;
                # choosing whether review was performed belongs to evaluation.
                pass
            def close(self):
                self.closed.set()
        return Session()


def _fixture_factory(facts):
    definition = ProviderToolDefinition("fixture_read", "Read the facts in this explicitly supplied test fixture.", {
        "type": "object", "properties": {"key": {"type": "string", "enum": ["source_facts"]}},
        "required": ["key"], "additionalProperties": False})
    def invoke(session, payload):
        if payload != {"key": "source_facts"}:
            raise PermissionError("fixture key is outside this test resource")
        return {"facts": list(facts)}
    return _ExampleToolFactory(definition, invoke)


def run_agent_example(root: Path, example_name: str, *, scenario="accepted", input_payload=None,
                      transport_kind=None, model_id=None, reasoning_profile=None, cli_path=None) -> dict:
    """Register and run one packaged, non-persistent multi-Agent example.

    Args:
        root: Explicit Runtime host root. Setup and exact example definitions
            use this root; it is not permission to read project files.
        example_name: agent_capability_example or agent_evaluation_example.
        scenario: accepted, revision or wait for the capability graph. Revision
            and wait are explicit test inputs, applied on the first selector
            visit. The wait scenario supplies one matching fixture event after
            recording WAIT, in this same process. Evaluation uses accepted only.
        input_payload: Optional object with exactly task and required_facts.
            None uses the packaged finite facts. No runtime flags, credentials,
            factory locators or model overrides are accepted in task JSON.
        transport_kind: Independent model transport passed to common preparation.
            Current tool-capable examples require the admitted Claude CLI path.
        model_id: Independent explicit model, or Runtime's public default.
        reasoning_profile: Independent effort, or Runtime's public default.
        cli_path: Explicit executable; otherwise the root's configured executable
            or the same transport's PATH lookup is used, without provider fallback.
    Returns:
        Actual Workflow/node records, child execution records, final output,
        resource cleanup and observed stop reason. Full raw logs are retained in
        the returned Runtime records. This explicit example requests Inspection
        for node and child logs before cleanup; evaluation receives selected
        actual facts. Ordinary graph dispatch does not parse native tool logs.
        Business acceptance is separate from status=completed. A focused example
        never declares full Runtime conformance or cross-process recovery.

        Read output as the task decision, execution.nodes/child_executions as
        actual calls, and stop_reason as graph progress. A caught graph exception
        returns failed/execution_error and failure={error_type, detail}; the last
        snapshot may still say running. resources_cleaned reports cleanup only.
    Raises:
        ValueError: Unknown example/scenario, invalid input or unsupported binding.
        Exception: Actual setup, registration, preparation or cleanup failures.
            No synthetic Reviewer verdict is returned for such failures.
    Effects:
        Registers only the fixed example definitions through the public Registry,
        prepares models in memory, invokes real Runtime Modules and returns logs.
        No PG, production authorization, Digestion data or ambient project access.
        Each run owns temporary workspaces, removed after capturing its records.
        Python remains the interpreter running Runtime. No additional environment.
        Every invocation creates a fresh Workflow execution. Saved JSON preserves
        evidence but does not resume a closed graph or a Provider CLI session.
    """
    from jsonschema import Draft202012Validator
    from ..execution.execution_local_invocation import (
        prepare_local_workflow, prepare_local_workflow_module, _run_prepared_workflow_node,
    )
    from ..execution.execution_workflow_evaluation import WorkflowSelfTestResources, LocalWorkflowModuleBridge
    from ..inspection.inspection_execution_logging import read_execution_log
    from ..foundation.foundation_environment_setup import setup_runtime, load_runtime_config
    from ..invocation.invocation_claude_cli_execution import ClaudeAdapter
    from ..invocation.invocation_codex_module_invocation import CodexCliModuleExecutor

    if example_name not in EXAMPLE_NAMES or scenario not in {"accepted", "revision", "wait"}:
        raise ValueError("unknown packaged example or scenario")
    if example_name == EXAMPLE_NAMES[1] and scenario != "accepted":
        raise ValueError("evaluation example has no selector scenario")
    task = {"task": _TASK, "required_facts": list(_FACTS)} if input_payload is None else deepcopy(input_payload)
    input_schema = {"type": "object", "additionalProperties": False, "required": ["task", "required_facts"],
        "properties": {"task": {"type": "string", "minLength": 1},
                       "required_facts": {"type": "array", "minItems": 1,
                                          "items": {"type": "string", "minLength": 1}}}}
    Draft202012Validator(input_schema).validate(task)
    setup_runtime(root)
    definition = (build_agent_capability_example if example_name == EXAMPLE_NAMES[0]
                  else build_agent_evaluation_example)()
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        example_name, "v1", definition.origin_bundle), root=root)
    choice = dict(transport_kind=transport_kind, model_id=model_id, reasoning_profile=reasoning_profile)
    saved, selection = prepare_local_workflow(root, example_name, version="v1", **choice)
    child = child_selection = None
    if example_name == EXAMPLE_NAMES[1]:
        exported = Module.to_workflow(build_example_reviewer()).export()
        register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
            "agent_example_task_review", "v1", exported.origin_bundle), root=root)
        child, child_selection = prepare_local_workflow_module(root, exported.workflow_release.workflow_id,
                                                               version="v1", **choice)
    config = load_runtime_config(root)
    transport = "claude_cli" if transport_kind is None else transport_kind
    executable = cli_path if cli_path is not None else config["provider_cli_paths"].get(transport)
    if executable is None:
        executable = shutil.which({"claude_cli": "claude", "codex_cli": "codex"}[transport])
    if executable is None:
        raise FileNotFoundError(f"{transport} executable is unavailable")
    executable = Path(executable).resolve(strict=True)
    artifacts, ledger = InMemoryCellArtifactStore(), InMemoryModuleExecutionLedger()
    initial = {**task, "scenario": scenario,
               "criteria": list(_GRAPH_CRITERIA if example_name == EXAMPLE_NAMES[0] else _CRITERIA)}
    if example_name == EXAMPLE_NAMES[0]:
        initial["wait_policy"] = deepcopy(_WAIT_POLICY)
    initial_schema = {"type": "object"}
    schema_sha = hashlib.sha256(_bytes(initial_schema)).hexdigest()
    content = artifacts.put_bytes(artifact_kind_id="module_input", schema_version="v1",
        schema_ref="schema:agent_example_input@v1", schema_sha256=schema_sha, media_type="application/json",
        content=_bytes(initial), idempotency_key="example_input", logical_name="example_input")
    initial_binding = ModuleInputBinding("example_input", content.artifact_ref, content.artifact_sha256,
        "schema:agent_example_input@v1", schema_sha, "application/json")
    children, child_lock = [], Lock()
    event_snapshots = []

    def inspected(record):
        result, = (
            item for item in ledger.results_for_execution(record["workflow_execution_id"])
            if item.module_run.module_run_id == record["module_run_id"]
        )
        return {**record, "execution_log": read_execution_log(
            result.module_run, attempts=result.attempts,
            read_content=artifacts.read_bytes, include_private_content=True,
        )}

    response = {}
    with _retain_cleanup_result(response), tempfile.TemporaryDirectory(prefix="agent-runtime-example-") as directory:
        with WorkflowSelfTestResources(registry=saved.registry, workflow=saved.release, selection=selection,
                input_bindings=(initial_binding,), artifact_host=artifacts, ledger=ledger,
                workspace_root=Path(directory),
                wait_policies=(initial["wait_policy"],) if "wait_policy" in initial else ()) as resources:
            def adapter(registry, profile, artifact_host, workspace):
                if profile.transport_kind == "claude_cli":
                    return ClaudeAdapter(release_registry=registry, artifact_host=artifact_host, workspace_root=workspace,
                        cli_path=executable, adapter_binding=(profile.executor_adapter_id, profile.executor_adapter_revision))
                return CodexCliModuleExecutor(release_registry=registry, artifact_host=artifact_host,
                                               workspace_root=workspace, codex_bin=str(executable))

            def factory_for_node(node_id, profile, artifact_host, workspace):
                if node_id == "gateway_researcher":
                    return _fixture_factory(task["required_facts"])
                if node_id != "tested_agent":
                    return None
                child_node = child.release.nodes[0]
                child_module = child.registry.get_module(child_node.module_release_ref, child_node.module_release_sha256)
                child_schema = child.registry.get_schema_asset(child_module.input_schema_ref, child_module.input_schema_sha256)
                tool_schema = {"type": "object", "properties": {"input_payload": child_schema.schema_document()},
                               "required": ["input_payload"], "additionalProperties": False}
                counter = 0
                def invoke(session, payload):
                    nonlocal counter
                    with child_lock:
                        counter += 1
                        number = counter
                    with tempfile.TemporaryDirectory(prefix="child-review-", dir=directory) as child_directory:
                        binding = child_selection.policy_document()["bindings"][0]
                        chosen = child.registry.get_execution_profile(binding["execution_profile_release_ref"],
                                                                      binding["execution_profile_release_sha256"])
                        _, record = _run_prepared_workflow_node(
                            registry=child.registry, workflow=child.release, selection=child_selection,
                            input_payload=payload["input_payload"], idempotency_key=session.request.attempt_id + "_child_" + str(number),
                            artifact_host=artifacts, ledger=ledger, workspace_root=Path(child_directory),
                            adapter=adapter(child.registry, chosen, artifacts, Path(child_directory)),
                            user_cancel_requested=resources.user_cancel_requested,
                            resource_cancel_requested=session.closed.is_set)
                        record = inspected(record)
                    with child_lock:
                        children.append({"parent_attempt_id": session.request.attempt_id,
                                         "input_payload": deepcopy(payload["input_payload"]), "record": record})
                    return {"child_execution_id": record["workflow_execution_id"], "module_run_id": record["module_run_id"],
                            "attempt_id": record["attempt_id"], "status": record["status"],
                            "output": record["output"], "failure_detail": record["failure_detail"]}
                return _ExampleToolFactory(ProviderToolDefinition("review_candidate",
                    "Submit the candidate to the fixed independently executed example reviewer.", tool_schema), invoke)

            def input_for_node(dispatch, records):
                node = dispatch.current_state_id
                if node == "context_reader":
                    return deepcopy(task)
                if node == "gateway_researcher":
                    return {"summary": _latest(records, "context_reader")["output"]["summary"], "fixture_key": "source_facts"}
                if node == "draft_writer":
                    feedback = [row["output"]["reason"] for row in records
                                if row["dispatch"]["current_state_id"] == "selector" and row["output"] is not None]
                    return {"facts": _latest(records, "gateway_researcher")["output"]["facts"], "feedback": feedback}
                if node in {"reviewer_a", "reviewer_b"}:
                    return {"draft": _latest(records, "draft_writer")["output"]["draft"], "required_facts": task["required_facts"]}
                if node == "selector":
                    first = not any(row["dispatch"]["current_state_id"] == "selector" for row in records)
                    action = {"accepted": "decide", "revision": "revise", "wait": "wait"}[scenario] if first else "decide"
                    return {"draft": _latest(records, "draft_writer")["output"]["draft"],
                            "review_a": _latest(records, "reviewer_a")["output"],
                            "review_b": _latest(records, "reviewer_b")["output"], "requested_action": action}
                if node == "tested_agent":
                    return {**deepcopy(task), "method": _METHOD}
                if node == "evaluation_agent":
                    with child_lock:
                        child_evidence = [{"parent_attempt_id": row["parent_attempt_id"],
                            "input_payload": deepcopy(row["input_payload"]), "execution": _evidence(row["record"])} for row in children]
                    return {"criteria": list(_CRITERIA), "evidence": {
                        "task": deepcopy(task), "tested_agent": _evidence(inspected(_latest(records, "tested_agent"))),
                        "child_executions": child_evidence}}
                raise ValueError("example input mapping has no such node")

            def outcome_for_node(dispatch, result, record):
                node, output = dispatch.current_state_id, record["output"]
                if record["status"] != "completed" and node != "tested_agent":
                    raise RuntimeError(f"example node {node} did not complete: {record['status']}")
                wait_ref = None
                target = {"context_reader": "gateway_researcher", "gateway_researcher": "draft_writer",
                          "draft_writer": "parallel_reviews", "reviewer_a": "selector", "reviewer_b": "selector",
                          "tested_agent": "evaluation_agent", "evaluation_agent": RUNTIME_TERMINAL_STATE_ID}.get(node)
                if node == "selector":
                    decision = output["decision"]
                    if decision == "wait_for_external_event":
                        target, wait_ref = None, initial["wait_policy"]["policy_ref"]
                    else:
                        target = RUNTIME_TERMINAL_STATE_ID if decision == "accepted" else "draft_writer"
                failures = [attempt.failure_class for attempt in result.attempts if attempt.status != "completed"]
                return ModuleOutcome.build(dispatch_id=dispatch.dispatch_id, workflow_execution_id=dispatch.workflow_execution_id,
                    expected_state_id=node, disposition=ModuleOutcomeDisposition.WAIT if wait_ref else ModuleOutcomeDisposition.TRANSITION,
                    target_state_id=target, wait_policy_ref=wait_ref, module_run_id=result.module_run.module_run_id,
                    attempt_ids=tuple(attempt.attempt_id for attempt in result.attempts), failure_class=failures[-1] if failures else None,
                    evidence_artifact_refs=tuple(item.output_ref for item in result.outputs),
                    outcome_ref="module-outcome:" + dispatch.dispatch_id)

            bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=input_for_node,
                outcome_for_node=outcome_for_node,
                adapter_for_node=lambda node, profile, store, workspace: adapter(saved.registry, profile, store, workspace),
                tool_session_factory_for_node=factory_for_node)
            async def execute():
                progress = await resources.drive(bridge, max_dispatches=18)
                if progress.stop_reason.value == "wait" and scenario == "wait":
                    event_snapshots.append(resources.execution_record()["snapshot"])
                    policy = initial["wait_policy"]
                    event = ExternalEvent(event_id="example_material_ready", event_type=policy["event_type"],
                        workflow_execution_id=resources.execution.workflow_execution_id, expected_state=policy["expected_state"],
                        target_state=policy["target_state"], evidence_ref=content.artifact_ref)
                    await resources.resume_event(event)
                    progress = await resources.drive(bridge, max_dispatches=12)
                return progress
            failure = None
            try:
                progress = asyncio.run(execute())
            except Exception as exc:
                progress = None
                failure = {"error_type": type(exc).__name__, "detail": str(exc)}
            record = resources.execution_record()
            record["nodes"] = tuple(inspected(node) for node in record["nodes"])
            status = record["snapshot"]["runtime_status_id"] if failure is None else "failed"
            final_node = "selector" if example_name == EXAMPLE_NAMES[0] else "evaluation_agent"
            final = [row for row in record["nodes"] if row["dispatch"]["current_state_id"] == final_node]
            response.update({"example": example_name, "scenario": scenario, "status": status,
                "input": deepcopy(initial), "input_binding": asdict(initial_binding),
                "stop_reason": progress.stop_reason.value if progress else "execution_error", "failure": failure,
                "output": final[-1]["output"] if final else None,
                "execution": record, "child_executions": deepcopy(children), "wait_snapshots": event_snapshots,
                "persistence": "not_requested", "full_runtime_completed": False})
    return response
