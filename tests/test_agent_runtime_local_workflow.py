"""Real local cursor/Coordinator tests; node adapters are explicit test doubles."""
import asyncio
from dataclasses import replace, fields
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Barrier, Event, Lock, Thread
from types import SimpleNamespace

import pytest

from agent_runtime.contracts.durability_backend_definition import DurableBackendAdapter, WorkflowCursor
from agent_runtime.contracts.durability_topology_definition import ExternalEvent
from agent_runtime.contracts.execution_module_definition import ModuleInputBinding
from agent_runtime.contracts.invocation_adapter_definition import SelfTestResourceUnavailableError
from agent_runtime.contracts.registry_release_definition import (
    WorkflowEdge, WorkflowRelease, WorkflowNodeBinding, WorkflowNodeKind,
    WorkflowParallelGroupBinding, WorkflowParallelJoinPolicy,
)
from agent_runtime.contracts.registry_workflow_definition import ModuleDispatchRequest, ModuleOutcome, ModuleOutcomeDisposition
from agent_runtime.durability.durability_local_coordination import LocalWorkflowBackend
from agent_runtime.durability.durability_workflow_coordination import DurableExecutionCoordinator
from agent_runtime.registry import RuntimeReleaseBundle, compile_execution_variant_policy_release
from agent_runtime.registry.registry_release_compilation import ExecutionVariantPolicyReleaseCandidate, ExecutionVariantProfileBindingCandidate
from test_agent_runtime_provider_tools import nodes
from test_agent_runtime_native_structured_output import _StubInlineAdapter
from agent_runtime.execution.execution_workflow_evaluation import (
    WorkflowSelfTestResources, LocalWorkflowModuleBridge, _local_start_arguments, _validate_node_outcome,
)


def start(nodes):
    payload = b'{"value":"initial"}'
    ref = nodes.artifacts.put_bytes(artifact_kind_id='module_input', schema_version='v1',
        schema_ref=nodes.module.input_schema_ref, schema_sha256=nodes.module.input_schema_sha256,
        media_type='application/json', content=payload, idempotency_key='initial')
    binding = ModuleInputBinding('task_input', ref.artifact_ref, ref.artifact_sha256,
                                 nodes.module.input_schema_ref, nodes.module.input_schema_sha256, 'application/json')
    cursor = LocalWorkflowBackend()
    execution = cursor.start_local(**start_arguments(nodes, binding))
    return cursor, execution, binding


def start_arguments(nodes, binding, **changes):
    return _local_start_arguments(**{**dict(registry=nodes.registry, workflow=nodes.workflow, selection=nodes.selection,
        input_bindings=(binding,), workflow_execution_id='one_execution'), **changes})


class RecordedBridge:
    """Outcome-only test double, never claimed to run the Module kernel."""
    def __init__(self, callback):
        self.callback, self.outcomes, self.calls = callback, {}, []

    def dispatch(self, request):
        if request.dispatch_id not in self.outcomes:
            self.calls.append(request)
            self.outcomes[request.dispatch_id] = self.callback(request)
        return self.outcomes[request.dispatch_id]

    def get_committed_outcome(self, execution, dispatch):
        return self.outcomes.get(dispatch)


def transition(request, target):
    return ModuleOutcome.build(dispatch_id=request.dispatch_id, workflow_execution_id=request.workflow_execution_id,
        expected_state_id=request.current_state_id, disposition=ModuleOutcomeDisposition.TRANSITION,
        target_state_id=target, outcome_ref='outcome:' + request.dispatch_id)


def drive(nodes, cursor, execution, bridge, **kwargs):
    return asyncio.run(DurableExecutionCoordinator(cursor=cursor, release_registry=nodes.registry,
        activity_bridge=bridge).drive_started(execution=execution, workflow=nodes.workflow,
        selection=nodes.selection, expected_start_sha256=cursor.start_sha256, **kwargs))


def test_local_start_has_no_production_authority_and_reentry_is_exact(nodes):
    cursor, execution, binding = start(nodes)
    assert isinstance(cursor, WorkflowCursor) and not isinstance(cursor, DurableBackendAdapter)
    assert cursor.start_local(**start_arguments(nodes, binding)) == execution
    with pytest.raises(ValueError, match='another start'):
        cursor.start_local(**{**start_arguments(nodes, binding), 'workflow_execution_id': 'another_execution'})
    snapshot = cursor.snapshot(execution)
    assert snapshot.start_request_sha256 != '0' * 64 and not snapshot.terminal
    assert not hasattr(cursor, 'tenant_id') and not hasattr(cursor, 'start')


def test_local_start_rejects_incomplete_node_selection_before_starting(nodes):
    _, _, binding = start(nodes)
    partial = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id='partial_selection', policy_version='v1', origin_kind='workflow',
        origin_release_ref=nodes.workflow.release_ref, origin_release_sha256=nodes.workflow.release_sha256,
        bindings=(ExecutionVariantProfileBindingCandidate(nodes.workflow.nodes[0].node_id,
            nodes.profile.release_ref, nodes.profile.release_sha256),)))
    nodes.registry.register_bundle(RuntimeReleaseBundle(execution_variant_policies=(partial,)))
    cursor = LocalWorkflowBackend()
    with pytest.raises(ValueError, match='every Module node'):
        _local_start_arguments(registry=nodes.registry, workflow=nodes.workflow, selection=partial,
                               input_bindings=(binding,), workflow_execution_id='one_execution')
    assert cursor._execution is None
    assert cursor.start_local(**start_arguments(nodes, binding)).workflow_execution_id == 'one_execution'


def test_started_graph_uses_one_coordinator_loop_and_does_not_replay_finished_nodes(nodes):
    cursor, execution, _ = start(nodes)
    bridge = RecordedBridge(lambda request: transition(request, 'second' if request.current_state_id == 'produce' else 'completed'))
    first = drive(nodes, cursor, execution, bridge, max_dispatches=1)
    assert first.stop_reason.value == 'dispatch_limit' and first.snapshot.current_state == 'second'
    second = drive(nodes, cursor, execution, bridge)
    assert second.stop_reason.value == 'terminal'
    assert drive(nodes, cursor, execution, bridge).dispatch_count == 0
    assert [item.current_state_id for item in bridge.calls] == ['produce', 'second']
    assert {item.workflow_execution_id for item in bridge.calls} == {'one_execution'}


def test_wrong_started_binding_and_cross_execution_are_rejected(nodes):
    cursor, execution, _ = start(nodes)
    bridge = RecordedBridge(lambda request: transition(request, 'second'))
    coordinator = DurableExecutionCoordinator(cursor=cursor, release_registry=nodes.registry, activity_bridge=bridge)
    with pytest.raises(PermissionError, match='start binding'):
        asyncio.run(coordinator.drive_started(execution=execution, workflow=nodes.workflow, selection=nodes.selection,
                                              expected_start_sha256='0' * 64))
    with pytest.raises(PermissionError, match='identity'):
        cursor.snapshot(replace(execution, workflow_execution_id='foreign'))
    assert bridge.calls == []


def test_started_graph_rejects_unproved_or_replaced_selection_before_dispatch(nodes):
    cursor, execution, _ = start(nodes)
    bridge = RecordedBridge(lambda request: transition(request, 'second'))
    unproved = SimpleNamespace(query=cursor.query, apply_external_event=cursor.apply_external_event,
                              recover=cursor.recover, list_events=cursor.list_events)
    coordinator = DurableExecutionCoordinator(cursor=unproved, release_registry=nodes.registry, activity_bridge=bridge)
    with pytest.raises(TypeError, match='verifies its actual start'):
        asyncio.run(coordinator.drive_started(execution=execution, workflow=nodes.workflow,
            selection=nodes.selection, expected_start_sha256=cursor.start_sha256))
    bindings = nodes.selection.policy_document()['bindings']
    changed = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id='replacement_selection', policy_version='v1', origin_kind='workflow',
        origin_release_ref=nodes.workflow.release_ref, origin_release_sha256=nodes.workflow.release_sha256,
        bindings=tuple(ExecutionVariantProfileBindingCandidate(item['position_id'], item['execution_profile_release_ref'],
            item['execution_profile_release_sha256']) for item in bindings)))
    nodes.registry.register_bundle(RuntimeReleaseBundle(execution_variant_policies=(changed,)))
    coordinator = DurableExecutionCoordinator(cursor=cursor, release_registry=nodes.registry, activity_bridge=bridge)
    with pytest.raises(PermissionError, match='selection binding'):
        asyncio.run(coordinator.drive_started(execution=execution, workflow=nodes.workflow,
            selection=changed, expected_start_sha256=cursor.start_sha256))
    assert bridge.calls == []


def test_local_wait_requires_matching_event_and_repeats_idempotently(nodes):
    cursor, execution, _ = start(nodes)
    def wait(request):
        if request.current_state_id == 'produce':
            return ModuleOutcome.build(dispatch_id=request.dispatch_id, workflow_execution_id=request.workflow_execution_id,
                expected_state_id='produce', disposition=ModuleOutcomeDisposition.WAIT,
                wait_policy_ref='wait:fixture', outcome_ref='outcome:' + request.dispatch_id)
        return transition(request, 'completed')
    bridge = RecordedBridge(wait)
    assert drive(nodes, cursor, execution, bridge).stop_reason.value == 'wait'
    assert drive(nodes, cursor, execution, bridge).stop_reason.value == 'wait'
    assert len(bridge.calls) == 1
    event = ExternalEvent('resume_1', 'completed', execution.workflow_execution_id, 'produce', 'second', 'fixture:ready')
    waiting = cursor.waiting_outcome(execution)
    wait_args = dict(expected_wait_ref=waiting['outcome_ref'], expected_wait_sha256=waiting['outcome_sha256'])
    with pytest.raises(ValueError, match='expected_state'):
        asyncio.run(cursor.resume_event(execution, replace(event, expected_state='second'), **wait_args))
    resumed = asyncio.run(cursor.resume_event(execution, event, **wait_args))
    assert asyncio.run(cursor.resume_event(execution, event)) == resumed
    assert drive(nodes, cursor, execution, bridge).stop_reason.value == 'terminal'
    assert len(bridge.calls) == 2


def test_local_cancellation_stops_before_next_node_and_closed_cursor_cannot_recover(nodes):
    cursor, execution, _ = start(nodes)
    def cancel(request):
        cursor.request_cancel(execution, reason='user requested stop')
        return transition(request, 'second')
    bridge = RecordedBridge(cancel)
    result = drive(nodes, cursor, execution, bridge)
    assert result.stop_reason.value == 'cancelled' and len(bridge.calls) == 1
    assert drive(nodes, cursor, execution, bridge).dispatch_count == 0
    assert asyncio.run(cursor.list_events(execution))[-1].event_type == 'runtime_cancellation'
    cursor.close()
    with pytest.raises(RuntimeError, match='closed'):
        asyncio.run(cursor.recover(execution))


def resources_for(nodes, *, wait_policies=()):
    cursor, _, binding = start(nodes)
    cursor.close()
    return WorkflowSelfTestResources(registry=nodes.registry, workflow=nodes.workflow, selection=nodes.selection,
        input_bindings=(binding,), artifact_host=nodes.artifacts, ledger=nodes.ledger,
        workspace_root=nodes.root, workflow_execution_id='one_execution', wait_policies=wait_policies)


def actual_outcome(dispatch, result, target, **changes):
    values = dict(dispatch_id=dispatch.dispatch_id, workflow_execution_id=dispatch.workflow_execution_id,
        expected_state_id=dispatch.current_state_id, disposition=ModuleOutcomeDisposition.TRANSITION,
        target_state_id=target, module_run_id=result.module_run.module_run_id,
        attempt_ids=tuple(attempt.attempt_id for attempt in result.attempts),
        failure_class=result.attempts[-1].failure_class, outcome_ref='outcome:' + dispatch.dispatch_id)
    values.update(changes)
    return ModuleOutcome.build(**values)


def test_real_bridge_runs_shared_kernel_and_records_two_nodes_without_authority(nodes):
    seen, adapters, outputs = [], [], []
    with resources_for(nodes) as resources:
        def inputs(dispatch, previous):
            seen.append((dispatch, previous))
            return {'value': 'start' if not previous else previous[-1]['output']['value']}
        def adapter(node_id, profile, artifacts, workspace):
            assert workspace.parent == resources.workspace_root
            result = _StubInlineAdapter(release_registry=nodes.registry, artifact_host=artifacts)
            adapters.append((workspace, result))
            return result
        def outcomes(dispatch, result, record):
            outputs.append((dispatch, result, record))
            return actual_outcome(dispatch, result, 'second' if dispatch.current_state_id == 'produce' else 'completed')
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=inputs,
                                           outcome_for_node=outcomes, adapter_for_node=adapter)
        final = asyncio.run(resources.drive(bridge))
        assert final.stop_reason.value == 'terminal'
        record = resources.execution_record()
        assert len(record['nodes']) == 2 and all(item['status'] == 'completed' for item in record['nodes'])
        assert record['input_bindings'] == [item.as_dict() for item in resources.input_bindings]
        assert len(record['outcomes']) == 2
        for row in record['outcomes']:
            assert row == bridge.get_committed_outcome('one_execution', row['dispatch_id']).as_dict()
        assert len(nodes.ledger.results_for_execution('one_execution')) == 2
        assert seen[0][1] == () and len(seen[1][1]) == 1
        assert {item['workflow_execution_id'] for item in record['nodes']} == {'one_execution'}
        assert len({item['attempt_id'] for item in record['nodes']}) == 2
        assert len({item[0] for item in adapters}) == 2 and all(item.calls == 1 for _, item in adapters)
        assert asyncio.run(resources.drive(bridge)).dispatch_count == 0
        assert len(record['nodes'][0]['execution_log']['attempts']) == 1
        assert record['persistence'] == 'not_requested'
        assert 'entitlement' not in json.dumps(record)
        for dispatch, result, _ in outputs:
            prior = nodes.ledger.get_committed_outcome('one_execution', dispatch.dispatch_id)
            assert nodes.ledger.commit_outcome('one_execution', dispatch.dispatch_id, prior) == prior
            with pytest.raises(ValueError, match='Attempt references'):
                nodes.ledger.guarded(lambda: _validate_node_outcome(nodes.ledger, 'one_execution', dispatch.dispatch_id,
                    actual_outcome(dispatch, result, prior.target_state_id, attempt_ids=('other_attempt',))))
        workspace = resources.workspace_root
    assert not workspace.exists()
    assert len(json.loads(json.dumps(record))['outcomes']) == 2
    with pytest.raises(SelfTestResourceUnavailableError, match='closed'):
        asyncio.run(resources.drive(bridge))


def test_real_failed_node_reaches_next_node_with_actual_failure_evidence(nodes):
    inputs_seen = []
    with resources_for(nodes) as resources:
        def inputs(dispatch, previous):
            inputs_seen.append(previous)
            return {'value': 'initial'}
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=inputs,
            outcome_for_node=lambda d, r, _: actual_outcome(d, r, 'second' if d.current_state_id == 'produce' else 'completed'),
            adapter_for_node=lambda node, profile, artifacts, workspace: _StubInlineAdapter(
                release_registry=nodes.registry, artifact_host=artifacts,
                terminal_status='failed' if node == 'produce' else 'completed'))
        assert asyncio.run(resources.drive(bridge)).stop_reason.value == 'terminal'
        assert inputs_seen[1][0]['status'] == 'failed' and inputs_seen[1][0]['output'] is None
        assert inputs_seen[1][0]['failure_class'] == 'provider'
        exported = resources.execution_record()
        assert exported['nodes'][1]['status'] == 'completed'
        assert exported['outcomes'][0]['failure_class'] == 'provider'
        assert exported['outcomes'][0]['target_state_id'] == 'second'


def test_real_parent_rejects_input_mutation_before_adapter(nodes):
    payload, adapters = {'value': 'admitted'}, []
    with resources_for(nodes) as resources:
        def adapter(node, profile, artifacts, workspace):
            payload['value'] = 'changed after admission'
            result = _StubInlineAdapter(release_registry=nodes.registry, artifact_host=artifacts)
            adapters.append(result)
            return result
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: payload,
            outcome_for_node=lambda *_: pytest.fail('invalid node must not produce an outcome'), adapter_for_node=adapter)
        with pytest.raises(PermissionError, match='input differs'):
            asyncio.run(resources.drive(bridge))
        assert adapters[0].calls == 0 and resources.node_records() == ()
        assert resources.execution_record()['preparation_failures']


def test_async_cancellation_keeps_loop_responsive_and_waits_for_node_finalization(nodes):
    started, finished = Event(), Event()
    with resources_for(nodes) as resources:
        def during(request, host):
            started.set()
            assert resources._cancel.wait(3), 'event loop was blocked by synchronous dispatch'
            finished.set()
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {},
            outcome_for_node=lambda *_: pytest.fail('cancelled Workflow must not enter outcome/evaluation callback'),
            adapter_for_node=lambda node, profile, artifacts, workspace: _StubInlineAdapter(
                release_registry=nodes.registry, artifact_host=artifacts, on_execute=during))
        async def exercise():
            task = asyncio.create_task(resources.drive(bridge))
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            progress = await task
            assert finished.is_set()
            return progress
        progress = asyncio.run(exercise())
        assert progress.stop_reason.value == 'cancelled'
        assert len(resources.node_records()) == 1
        # The in-process adapter finished before noticing the flag; preserve its
        # real result rather than rewriting it as a fabricated cancelled Attempt.
        assert resources.node_records()[0]['status'] == 'completed'


def replace_graph(nodes, *, node_rows, edges, initial, parallel_groups=()):
    values = {field.name: getattr(nodes.workflow, field.name) for field in fields(nodes.workflow)
              if field.name != 'release_sha256'}
    values.update(workflow_id='local_graph', release_ref='runtime-workflow:local_graph@v1',
                  nodes=node_rows, edges=edges, initial_node_id=initial, parallel_groups=parallel_groups)
    workflow = WorkflowRelease.build(**values)
    selection = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id='local_graph_selection', policy_version='v1', origin_kind='workflow',
        origin_release_ref=workflow.release_ref, origin_release_sha256=workflow.release_sha256,
        bindings=tuple(ExecutionVariantProfileBindingCandidate(node.node_id, nodes.profile.release_ref,
                       nodes.profile.release_sha256) for node in node_rows if node.node_kind is WorkflowNodeKind.MODULE)))
    nodes.registry.register_bundle(RuntimeReleaseBundle(workflows=(workflow,), execution_variant_policies=(selection,)))
    nodes.workflow, nodes.selection = workflow, selection


def test_real_parallel_nodes_reach_kernel_concurrently_and_join_once(nodes):
    first, second = nodes.workflow.nodes
    join = replace(first, node_id='join')
    control = WorkflowNodeBinding('parallel', WorkflowNodeKind.CONTROL, None, None, None, None)
    replace_graph(nodes, node_rows=(control, first, second, join), initial='parallel', edges=(
        WorkflowEdge('parallel', 'all_completed', 'join', False),
        WorkflowEdge('produce', 'completed', 'join', False), WorkflowEdge('second', 'completed', 'join', False),
        WorkflowEdge('join', 'completed', None, True)), parallel_groups=(WorkflowParallelGroupBinding(
            'pair', 'parallel', ('produce', 'second'), 'join', 'all_completed', WorkflowParallelJoinPolicy.ALL_REQUIRED),))
    barrier, lock = Barrier(2), Lock()
    calls, join_inputs = [], []
    with resources_for(nodes) as resources:
        def adapter(node_id, profile, artifacts, workspace):
            def enter(request, host):
                with lock:
                    calls.append(node_id)
                if node_id != 'join':
                    barrier.wait(timeout=3)
            return _StubInlineAdapter(release_registry=nodes.registry, artifact_host=artifacts, on_execute=enter)
        def inputs(dispatch, previous):
            if dispatch.current_state_id == 'join':
                join_inputs.append(previous)
            return {}
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=inputs,
            outcome_for_node=lambda d, r, _: actual_outcome(d, r, 'completed' if d.current_state_id == 'join' else 'join'),
            adapter_for_node=adapter)
        assert asyncio.run(resources.drive(bridge)).stop_reason.value == 'terminal'
        assert sorted(calls) == ['join', 'produce', 'second']
        assert len(join_inputs) == 1 and {item['dispatch']['current_state_id'] for item in join_inputs[0]} == {'produce', 'second'}
        assert len(resources.node_records()) == 3
        assert asyncio.run(resources.drive(bridge)).dispatch_count == 0


def test_parallel_cancellation_waits_for_both_real_kernel_calls_and_never_joins(nodes):
    first, second = nodes.workflow.nodes
    join = replace(first, node_id='join')
    control = WorkflowNodeBinding('parallel', WorkflowNodeKind.CONTROL, None, None, None, None)
    replace_graph(nodes, node_rows=(control, first, second, join), initial='parallel', edges=(
        WorkflowEdge('parallel', 'all_completed', 'join', False),
        WorkflowEdge('produce', 'completed', 'join', False), WorkflowEdge('second', 'completed', 'join', False),
        WorkflowEdge('join', 'completed', None, True)), parallel_groups=(WorkflowParallelGroupBinding(
            'pair', 'parallel', ('produce', 'second'), 'join', 'all_completed', WorkflowParallelJoinPolicy.ALL_REQUIRED),))
    lock, both_started = Lock(), Event()
    starts, finishes = [], []
    with resources_for(nodes) as resources:
        def adapter(node, profile, artifacts, workspace):
            assert node != 'join'
            def enter(request, host):
                with lock:
                    starts.append(node)
                    if len(starts) == 2:
                        both_started.set()
                assert resources._cancel.wait(3)
                with lock:
                    finishes.append(node)
            return _StubInlineAdapter(release_registry=nodes.registry, artifact_host=artifacts, on_execute=enter)
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {},
            outcome_for_node=lambda *_: pytest.fail('cancelled branches must not join'), adapter_for_node=adapter)
        async def exercise():
            task = asyncio.create_task(resources.drive(bridge))
            assert await asyncio.to_thread(both_started.wait, 3)
            task.cancel()
            return await task
        progress = asyncio.run(exercise())
        assert progress.stop_reason.value == 'cancelled'
        assert sorted(starts) == sorted(finishes) == ['produce', 'second']
        assert len(resources.node_records()) == 2
        assert progress.dispatch_count == 2


def test_real_revision_and_wait_keep_one_execution_with_new_dispatches(nodes):
    replace_graph(nodes, node_rows=nodes.workflow.nodes, initial='produce', edges=(
        WorkflowEdge('produce', 'completed', 'second', False),
        WorkflowEdge('second', 'revision_required', 'produce', False),
        WorkflowEdge('second', 'material_ready', 'produce', False),
        WorkflowEdge('second', 'accepted', None, True)))
    decisions = []
    policy = {'policy_ref': 'wait:material', 'expected_state': 'second', 'event_type': 'material_ready', 'target_state': 'produce'}
    with resources_for(nodes, wait_policies=(policy,)) as resources:
        # The caller's later mutation cannot replace the actual bound condition.
        policy['event_type'] = 'accepted'
        def outcome(dispatch, result, record):
            if dispatch.current_state_id == 'produce':
                return actual_outcome(dispatch, result, 'second')
            decisions.append(dispatch.dispatch_id)
            if len(decisions) == 1:
                return actual_outcome(dispatch, result, 'produce')
            if len(decisions) == 2:
                return actual_outcome(dispatch, result, None, disposition=ModuleOutcomeDisposition.WAIT,
                                      wait_policy_ref='wait:material')
            return actual_outcome(dispatch, result, 'completed')
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {}, outcome_for_node=outcome,
            adapter_for_node=lambda node, profile, artifacts, workspace: _StubInlineAdapter(
                release_registry=nodes.registry, artifact_host=artifacts))
        progress = asyncio.run(resources.drive(bridge))
        assert progress.stop_reason.value == 'wait' and len(resources.node_records()) == 4
        waiting_record = resources.execution_record()
        assert waiting_record['wait_policies'][0]['event_type'] == 'material_ready'
        wait_outcome, = (row for row in waiting_record['outcomes'] if row['disposition'] == 'wait')
        assert wait_outcome['wait_policy_ref'] == 'wait:material'
        assert wait_outcome['outcome_sha256'] == progress.last_outcome.outcome_sha256
        assert asyncio.run(resources.drive(bridge)).stop_reason.value == 'wait'
        assert len(resources.node_records()) == 4
        event = ExternalEvent('material_1', 'material_ready', 'one_execution', 'second', 'produce', 'fixture:material')
        with pytest.raises(ValueError, match='WAIT condition'):
            asyncio.run(resources.resume_event(replace(event, event_type='wrong')))
        before = resources.cursor.snapshot(resources.execution)
        for wrong in (replace(event, event_type='accepted', target_state='completed'),
                      replace(event, event_type='revision_required')):
            with pytest.raises(ValueError, match='WAIT condition'):
                asyncio.run(resources.resume_event(wrong))
            assert resources.cursor.snapshot(resources.execution) == before
        with pytest.raises(ValueError):
            asyncio.run(resources.resume_event(replace(event, expected_state='produce')))
        asyncio.run(resources.resume_event(event))
        assert asyncio.run(resources.resume_event(event)) == resources.cursor.snapshot(resources.execution)
        assert asyncio.run(resources.drive(bridge)).stop_reason.value == 'terminal'
        records = resources.node_records()
        assert len(records) == 6 and len({item['dispatch']['dispatch_id'] for item in records}) == 6
        assert len({item['module_run_id'] for item in records}) == 6
        assert {item['workflow_execution_id'] for item in records} == {'one_execution'}


def test_wait_without_explicit_condition_cannot_commit(nodes):
    with resources_for(nodes) as resources:
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {},
            outcome_for_node=lambda d, r, _: actual_outcome(d, r, None, disposition=ModuleOutcomeDisposition.WAIT,
                                                           wait_policy_ref='wait:not_bound'),
            adapter_for_node=lambda node, profile, artifacts, workspace: _StubInlineAdapter(
                release_registry=nodes.registry, artifact_host=artifacts))
        with pytest.raises(ValueError, match='WAIT lacks its exact'):
            asyncio.run(resources.drive(bridge))
        exported = resources.execution_record()
        assert len(exported['nodes']) == 1 and exported['nodes'][0]['status'] == 'completed'
        assert exported['outcomes'] == []
        assert resources.cursor.waiting_outcome(resources.execution) is None


@pytest.mark.parametrize('invalid', ['duplicate', 'missing_field', 'unknown_edge'])
def test_explicit_wait_policy_binding_is_checked_before_graph_execution(nodes, invalid):
    policy = {'policy_ref': 'wait:fixture', 'expected_state': 'produce', 'event_type': 'completed', 'target_state': 'second'}
    rows = (policy,)
    if invalid == 'duplicate':
        rows = (policy, dict(policy))
    elif invalid == 'missing_field':
        policy.pop('event_type')
    else:
        policy['event_type'] = 'not_a_declared_edge'
    with pytest.raises(ValueError):
        resources_for(nodes, wait_policies=rows)
    assert nodes.ledger.results_for_execution('one_execution') == ()


@pytest.mark.parametrize('user_cancel', [False, True])
def test_close_during_final_outcome_callback_cannot_commit_or_complete(nodes, user_cancel):
    entered, release = Event(), Event()
    resources = resources_for(nodes)
    try:
        def outcome(dispatch, result, record):
            if dispatch.current_state_id == 'second':
                entered.set()
                assert release.wait(3)
            return actual_outcome(dispatch, result, 'second' if dispatch.current_state_id == 'produce' else 'completed')
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {}, outcome_for_node=outcome,
            adapter_for_node=lambda node, profile, artifacts, workspace: _StubInlineAdapter(
                release_registry=nodes.registry, artifact_host=artifacts))
        async def exercise():
            task = asyncio.create_task(resources.drive(bridge))
            assert await asyncio.to_thread(entered.wait, 3)
            if user_cancel:
                resources.request_cancel('user stopped final callback')
            else:
                with pytest.raises(RuntimeError, match='active nodes'):
                    resources.close()
            release.set()
            if user_cancel:
                assert (await task).stop_reason.value == 'cancelled'
            else:
                with pytest.raises(SelfTestResourceUnavailableError):
                    await task
        asyncio.run(exercise())
        exported = resources.execution_record()
        assert len(exported['nodes']) == 2 and all(row['status'] == 'completed' for row in exported['nodes'])
        assert len(exported['outcomes']) == 1
        assert exported['snapshot']['current_state'] == 'second'
        assert exported['snapshot']['runtime_status_id'] == ('cancelled' if user_cancel else 'running')
        assert resources.user_cancel_requested() is user_cancel
    finally:
        release.set()
        resources.close()


@pytest.mark.parametrize('user_cancel', [False, True])
def test_stop_after_final_outcome_commit_still_prevents_cursor_completion(nodes, user_cancel):
    resources = resources_for(nodes)
    try:
        class ClosingBridge(LocalWorkflowModuleBridge):
            def dispatch(self, request):
                outcome = super().dispatch(request)
                if request.current_state_id == 'second':
                    if user_cancel:
                        resources.request_cancel('user stopped before terminal advance')
                    else:
                        with pytest.raises(RuntimeError, match='active nodes'):
                            resources.close()
                return outcome
        bridge = ClosingBridge(resources=resources, input_for_node=lambda *_: {},
            outcome_for_node=lambda d, r, _: actual_outcome(d, r, 'second' if d.current_state_id == 'produce' else 'completed'),
            adapter_for_node=lambda node, profile, artifacts, workspace: _StubInlineAdapter(
                release_registry=nodes.registry, artifact_host=artifacts))
        if user_cancel:
            assert asyncio.run(resources.drive(bridge)).stop_reason.value == 'cancelled'
        else:
            with pytest.raises(SelfTestResourceUnavailableError):
                asyncio.run(resources.drive(bridge))
        record = resources.execution_record()
        assert len(record['nodes']) == len(record['outcomes']) == 2  # These commits preceded invalidation.
        assert record['snapshot']['runtime_status_id'] == ('cancelled' if user_cancel else 'running')
        assert record['snapshot']['current_state'] == 'second'
        assert len(record['snapshot']['applied_events']) == 1
        assert resources.user_cancel_requested() is user_cancel
    finally:
        resources.close()


@pytest.mark.parametrize('reader', ['committed_nodes', 'execution_record'])
def test_committed_record_read_never_holds_parent_lock_while_entering_ledger(nodes, monkeypatch, reader):
    with resources_for(nodes) as resources:
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {},
            outcome_for_node=lambda d, r, _: actual_outcome(d, r, 'second' if d.current_state_id == 'produce' else 'completed'),
            adapter_for_node=lambda node, profile, artifacts, workspace: _StubInlineAdapter(
                release_registry=nodes.registry, artifact_host=artifacts))
        asyncio.run(resources.drive(bridge, max_dispatches=1))
        held, reading = Event(), Event()
        observed = []
        original = nodes.ledger.guarded
        def read(*args):
            reading.set()
            return original(*args)
        monkeypatch.setattr(nodes.ledger, 'guarded', read)
        def holder():
            def guarded():
                held.set()
                assert reading.wait(2)
                acquired = resources._lock.acquire(timeout=1)
                observed.append(acquired)
                if acquired:
                    resources._lock.release()
            original(guarded)
        left = Thread(target=holder)
        read_records = (lambda: resources.node_records(committed_only=True)) if reader == 'committed_nodes' else resources.execution_record
        right = Thread(target=read_records)
        left.start()
        assert held.wait(2)
        right.start()
        left.join(timeout=3); right.join(timeout=3)
        assert not left.is_alive() and not right.is_alive() and observed == [True]


def test_reentry_after_outcome_callback_failure_reuses_actual_module_result(nodes):
    provider_calls, inputs, outcome_calls = [], [], []
    with resources_for(nodes) as resources:
        def input_for_node(dispatch, previous):
            inputs.append(dispatch.current_state_id)
            return {}
        def adapter(node, profile, artifacts, workspace):
            return _StubInlineAdapter(release_registry=nodes.registry, artifact_host=artifacts,
                on_execute=lambda *_: provider_calls.append(node))
        def outcome(dispatch, result, record):
            outcome_calls.append(dispatch.current_state_id)
            if len(outcome_calls) == 1:
                raise RuntimeError('host outcome callback stopped after kernel commit')
            return actual_outcome(dispatch, result, 'second' if dispatch.current_state_id == 'produce' else 'completed')
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=input_for_node,
            outcome_for_node=outcome, adapter_for_node=adapter)
        with pytest.raises(RuntimeError, match='callback stopped'):
            asyncio.run(resources.drive(bridge))
        first_record = resources.node_records()[0]
        assert first_record['status'] == 'completed'
        assert asyncio.run(resources.drive(bridge)).stop_reason.value == 'terminal'
        assert provider_calls == inputs == ['produce', 'second']
        assert outcome_calls == ['produce', 'produce', 'second']
        assert resources.node_records()[0] == first_record


def agent_graph_resources(tmp_path, *, callback=False):
    from agent_runtime import Module, Workflow, RuntimeModulePlugin, register_runtime_module_plugin
    from agent_runtime.execution.execution_local_invocation import prepare_local_workflow
    from agent_runtime.execution import InMemoryCellArtifactStore
    from agent_runtime.ledger import InMemoryModuleExecutionLedger
    from agent_runtime.registry import RuntimeReleaseRegistry
    from agent_runtime.registry.registry_module_loading import ModuleRegistrationSource
    from test_agent_runtime_workflow_release_compilation import _candidate
    from test_agent_runtime_module_authoring import _requirements
    exports = []
    for index, node in enumerate(('produce', 'second')):
        identity = 'local_agent_' + node
        input_ref, output_ref = 'schema:' + identity + '_input@v1', 'schema:' + identity + '_output@v1'
        source = ModuleRegistrationSource(skill_id='local-agent-fixture', module_id=identity,
            owner_contract_ref='test-owner:local-agent@v1', owner_contract_content='# Local graph fixture',
            input_schema_ref=input_ref, input_schema_document=json.dumps({'$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': input_ref, 'type': 'object'}),
            output_schema_ref=output_ref, output_schema_document=json.dumps({'$schema': 'https://json-schema.org/draft/2020-12/schema', '$id': output_ref, 'type': 'object',
                'properties': {'summary': {'type': 'string'}}, 'required': ['summary'], 'additionalProperties': False}),
            instruction_text='Return the actual fixture result.', schema_version='runtime_module_registration_v4')
        tools = ('inspect_note',) if callback and index == 0 else ()
        exports.append(Module(source, execution_requirements=_requirements(
            execution_mode='agent' if tools else 'tool_free', tool_policy=tools,
            attempt_workspace_policy='none', timeout_seconds=30, max_attempts=1)).export(module_version='v1'))
    base = _candidate()
    nodes_ = tuple(replace(base.nodes[0], node_id=node, module_release_ref=export.module_release.release_ref,
        module_release_sha256=export.module_release.release_sha256) for node, export in zip(('produce', 'second'), exports))
    workflow = Workflow.from_graph(replace(base, nodes=nodes_, edges=(WorkflowEdge('produce', 'completed', 'second', False),
        WorkflowEdge('second', 'completed', None, True)), authorization_manifest_document={'required_operation_ids': ['model_execute']}),
        module_exports=tuple(exports)).export()
    root = tmp_path / 'registered_fixture'
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin('graph_fixture', 'v1', workflow.origin_bundle), root=root)
    saved, selection = prepare_local_workflow(root, workflow.workflow_release.workflow_id)
    artifacts, ledger = InMemoryCellArtifactStore(), InMemoryModuleExecutionLedger()
    module = exports[0].module_release
    initial = artifacts.put_bytes(artifact_kind_id='module_input', schema_version='v1', schema_ref=module.input_schema_ref,
        schema_sha256=module.input_schema_sha256, media_type='application/json', content=b'{}', idempotency_key='initial')
    binding = ModuleInputBinding('task_input', initial.artifact_ref, initial.artifact_sha256,
                                module.input_schema_ref, module.input_schema_sha256, 'application/json')
    return WorkflowSelfTestResources(registry=saved.registry, workflow=saved.release, selection=selection,
        input_bindings=(binding,), artifact_host=artifacts, ledger=ledger, workspace_root=tmp_path)


def test_graph_factory_callback_uses_real_ipc_and_is_not_inherited_by_next_node(tmp_path):
    from agent_runtime.invocation.invocation_claude_cli_execution import ClaudeAdapter
    from agent_runtime.invocation.invocation_local_command_mcp import exchange
    from test_agent_runtime_provider_tools import CallbackFactory
    from test_agent_runtime_claude_native_tools import _fake_cli, _init, _result, _tool_use, _tool_result
    factory, requested = CallbackFactory(), []
    def process(**kwargs):
        kwargs['launch_guard'](lambda: None)
        argv = kwargs['argv']
        settings = json.loads(argv[argv.index('--mcp-config') + 1])
        if settings['mcpServers']:
            endpoint = Path(settings['mcpServers']['runtime_tools']['args'][-2])
            response = exchange(endpoint, {'method': 'invoke', 'tool_name': 'inspect_note', 'payload': {'value': 41}})
            init = _init(())
            tool = 'mcp__runtime_tools__inspect_note'
            init['tools'].append(tool)
            init['mcp_servers'] = [{'name': 'runtime_tools', 'status': 'connected'}]
            events = [init, _tool_use('graph_call', tool, value=41),
                      _tool_result('graph_call', content=json.dumps(response)), _result(structured_output={'summary': '42'})]
        else:
            assert 'inspect_note' not in kwargs['prompt']
            events = [_init(()), _result(structured_output={'summary': 'independent'})]
        raw = '\n'.join(json.dumps(event) for event in events)
        for line in raw.splitlines():
            assert kwargs['on_stdout_line'](line)
        result = subprocess.CompletedProcess(argv, 0, raw, '')
        result.stdout_bytes, result.stderr_bytes = raw.encode(), b''
        return result
    with agent_graph_resources(tmp_path, callback=True) as resources:
        def tool_factory(node, profile, artifacts, workspace):
            requested.append(node)
            return factory if node == 'produce' else None
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {},
            outcome_for_node=lambda d, r, _: actual_outcome(d, r, 'second' if d.current_state_id == 'produce' else 'completed'),
            adapter_for_node=lambda node, profile, artifacts, workspace: ClaudeAdapter(
                release_registry=resources.registry, artifact_host=artifacts, workspace_root=workspace,
                cli_path=_fake_cli(tmp_path), process_runner=process), tool_session_factory_for_node=tool_factory)
        assert asyncio.run(resources.drive(bridge)).stop_reason.value == 'terminal'
        records = resources.node_records()
        assert requested == ['produce', 'second'] and factory.closed
        assert factory.calls == [('inspect_note', {'value': 41})]
        from agent_runtime import read_execution_log
        results = {item.module_run.module_run_id: item
                   for item in resources.ledger.results_for_execution(resources.execution.workflow_execution_id)}
        assert all(row['execution_log']['complete'] is None for row in records)
        logs = [read_execution_log(results[row['module_run_id']].module_run,
                                  attempts=results[row['module_run_id']].attempts,
                                  read_content=resources.artifact_host.read_bytes, include_private_content=True)
                for row in records]
        call, = logs[0]['tool_calls']
        assert call['provider_tool_call_id'] == 'graph_call'
        assert call['response']['result'] == {'answer': 42}
        assert logs[0]['complete'] and logs[1]['complete']
        assert logs[1]['tool_calls'] == []


@pytest.mark.parametrize('user_cancel', [True, False])
def test_graph_callbacks_stop_actual_process_and_keep_cancel_reason(tmp_path, user_cancel):
    from agent_runtime.invocation.invocation_claude_cli_execution import ClaudeAdapter
    from test_agent_runtime_claude_native_tools import _init
    marker = tmp_path / 'node_started'
    executable = tmp_path / 'local_cli'
    executable.write_text(f'#!{sys.executable}\nimport os,sys,time\n'
        "if '--help' in sys.argv or '--version' in sys.argv:\n"
        " print('fixture --safe-mode --restricted --tools --settings --effort --json-schema --strict-mcp-config --add-dir')\n"
        'else:\n' + f' open({str(marker)!r},"w").write(str(os.getpid()))\n'
        + f' print({json.dumps(_init(()))!r},flush=True)\n time.sleep(30)\n')
    executable.chmod(0o700)
    resources = agent_graph_resources(tmp_path)
    try:
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {},
            outcome_for_node=lambda *_: pytest.fail('cancel/resource loss must not enter evaluator'),
            adapter_for_node=lambda node, profile, artifacts, workspace: ClaudeAdapter(
                release_registry=resources.registry, artifact_host=artifacts, workspace_root=workspace, cli_path=executable))
        async def exercise():
            task = asyncio.create_task(resources.drive(bridge))
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)
            assert marker.exists(), 'actual node process did not start'
            if user_cancel:
                task.cancel()
                return await asyncio.wait_for(task, 5)
            with pytest.raises(RuntimeError, match='active nodes'):
                resources.close()
            with pytest.raises(SelfTestResourceUnavailableError, match='resources became unavailable'):
                await asyncio.wait_for(task, 5)
        progress = asyncio.run(exercise())
        record, = resources.node_records()
        assert record['status'] == ('cancelled' if user_cancel else 'failed')
        assert record['provider_trace']['stop_reason'] == ('cancelled' if user_cancel else 'resource_closed')
        assert record['provider_trace']['raw_streams']['stdout']['data']
        assert resources.user_cancel_requested() is user_cancel
        if user_cancel:
            assert progress.stop_reason.value == 'cancelled'
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
    finally:
        resources.close()


def test_parent_workspace_loss_uses_existing_self_test_resource_failure(nodes):
    resources = resources_for(nodes)
    resources.workspace_root.rmdir()  # This exact empty fixture directory is owned by this test.
    try:
        with pytest.raises(SelfTestResourceUnavailableError, match='closed or unavailable'):
            resources.require_active()
        assert resources.resource_cancel_requested() is True
        assert resources.user_cancel_requested() is False
    finally:
        resources.close()


@pytest.mark.parametrize('corruption', ['execution', 'dispatch', 'module_run', 'attempt', 'failure', 'shape'])
def test_execution_validates_opaque_stored_outcome_on_replay_before_adapter(nodes, corruption):
    captured, calls = [], []
    with resources_for(nodes) as resources:
        def interrupt_outcome(dispatch, result, record):
            captured.append((dispatch, result))
            raise RuntimeError('pause after real kernel result, before outcome commit')
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {},
            outcome_for_node=interrupt_outcome,
            adapter_for_node=lambda node, profile, artifacts, workspace: _StubInlineAdapter(
                release_registry=nodes.registry, artifact_host=artifacts, on_execute=lambda *_: calls.append(node)))
        with pytest.raises(RuntimeError, match='pause after real'):
            asyncio.run(resources.drive(bridge))
        dispatch, result = captured[0]
        changes = {'execution': {'workflow_execution_id': 'foreign_execution'},
                   'dispatch': {'dispatch_id': 'foreign_dispatch'}, 'module_run': {'module_run_id': 'foreign_run'},
                   'attempt': {'attempt_ids': ('foreign_attempt',)}, 'failure': {'failure_class': 'provider'}}
        bad = ({'not': 'an Outcome'} if corruption == 'shape'
               else actual_outcome(dispatch, result, 'second', **changes[corruption]))
        # Storage accepts only immutable bytes/values; it does not own Registry's
        # Outcome schema. Execution must reject this injected wrong meaning.
        nodes.ledger.commit_outcome('one_execution', dispatch.dispatch_id, bad)
        with pytest.raises((ValueError, PermissionError, TypeError)):
            bridge.get_committed_outcome('one_execution', dispatch.dispatch_id)
        with pytest.raises((ValueError, PermissionError, TypeError)):
            asyncio.run(resources.drive(bridge))
        assert calls == ['produce']


def test_execution_rejects_outcome_without_a_committed_node_and_cancelled_result(nodes):
    with resources_for(nodes) as resources:
        calls = []
        bridge = LocalWorkflowModuleBridge(resources=resources, input_for_node=lambda *_: {},
            outcome_for_node=lambda *_: pytest.fail('cancelled node cannot make an Outcome'),
            adapter_for_node=lambda node, profile, artifacts, workspace: _StubInlineAdapter(
                release_registry=nodes.registry, artifact_host=artifacts, terminal_status='cancelled',
                on_execute=lambda *_: calls.append(node)))
        absent = ModuleOutcome.build(dispatch_id='absent', workflow_execution_id='one_execution', expected_state_id='produce',
            disposition=ModuleOutcomeDisposition.TRANSITION, target_state_id='second', module_run_id='absent',
            attempt_ids=('absent',), outcome_ref='outcome:absent')
        nodes.ledger.commit_outcome('one_execution', 'absent', absent)
        with pytest.raises(ValueError, match='exact Workflow Module request'):
            bridge.get_committed_outcome('one_execution', 'absent')
        assert calls == []
        progress = asyncio.run(resources.drive(bridge))
        assert progress.stop_reason.value == 'cancelled'
        record, = resources.node_records()
        dispatch = ModuleDispatchRequest(**record['dispatch'])
        result, = nodes.ledger.results_for_execution('one_execution')
        cancelled = actual_outcome(dispatch, result, 'second')
        nodes.ledger.commit_outcome('one_execution', dispatch.dispatch_id, cancelled)
        with pytest.raises(ValueError, match='Cancelled Module'):
            bridge.get_committed_outcome('one_execution', dispatch.dispatch_id)
        assert calls == ['produce']
