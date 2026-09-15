"""One process-local Workflow using the real Module kernel and shared evidence.

The host supplies exact registered definitions and bounded node callbacks. This
module does not select models, load roots, implement a graph loop or call a CLI.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import tempfile
from threading import Event, RLock
import uuid

from ..contracts.execution_module_definition import ModuleInputBinding, WorkflowModuleExecutionRequest
from ..contracts.invocation_adapter_definition import SelfTestResourceUnavailableError
from ..contracts.registry_release_definition import ModuleExecutionPurpose, WorkflowNodeKind, WorkflowRelease, ExecutionVariantPolicyRelease
from ..contracts.registry_workflow_definition import ModuleDispatchRequest, ModuleOutcome, ModuleOutcomeDisposition
from ..durability.durability_local_coordination import LocalWorkflowBackend
from ..durability.durability_workflow_coordination import DurableExecutionCoordinator
from ..ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger
from ..registry.registry_graph_projection import project_workflow_release_graph, RUNTIME_TERMINAL_STATE_ID
from ..foundation.foundation_contract_validation import validate_id, validate_opaque_ref
from .execution_content_staging import InMemoryCellArtifactStore
from .execution_local_invocation import _run_prepared_workflow_node


def _identity(prefix, *parts):
    return prefix + '_' + hashlib.sha256('\0'.join(parts).encode()).hexdigest()[:24]


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _validated_wait_policies(workflow, rows):
    """Freeze explicitly supplied wait bindings; never discover them in task JSON."""
    if type(rows) is not tuple:
        raise TypeError('wait_policies must be an explicit tuple of policy bindings')
    result = {}
    for original in rows:
        if type(original) is not dict or set(original) != {'policy_ref', 'expected_state', 'event_type', 'target_state'}:
            raise ValueError('wait policy requires policy_ref, expected_state, event_type and target_state')
        row = deepcopy(original)
        validate_opaque_ref('wait policy_ref', row['policy_ref'])
        for key in ('expected_state', 'event_type', 'target_state'):
            validate_id('wait ' + key, row[key])
        if row['policy_ref'] in result:
            raise ValueError('wait policy_ref must be unique')
        if not any(node.node_id == row['expected_state'] and node.node_kind is WorkflowNodeKind.MODULE for node in workflow.nodes):
            raise ValueError('wait expected_state must be an exact Module node')
        if row['event_type'] == 'module_outcome' or not any(
            edge.source_node_id == row['expected_state'] and edge.outcome_id == row['event_type']
            and (RUNTIME_TERMINAL_STATE_ID if edge.terminal else edge.target_node_id) == row['target_state']
            for edge in workflow.edges
        ):
            raise ValueError('wait condition must identify one declared host-event graph edge')
        result[row['policy_ref']] = row
    return tuple(result[key] for key in sorted(result))


def _local_start_arguments(*, registry, workflow, selection, input_bindings, workflow_execution_id, wait_policies=()):
    """Execution resolves definitions; Durability receives only validated facts."""
    if type(workflow) is not WorkflowRelease or type(selection) is not ExecutionVariantPolicyRelease:
        raise TypeError('local start requires exact Workflow and Variant releases')
    if registry.get_workflow(workflow.release_ref, workflow.release_sha256) != workflow:
        raise ValueError('local start Workflow differs from registered definition')
    if registry.get_execution_variant_policy(selection.release_ref, selection.release_sha256) != selection:
        raise ValueError('local start selection differs from registered definition')
    policy = selection.policy_document()
    if (policy['origin_kind'], policy['origin_release_ref'], policy['origin_release_sha256']) != (
            'workflow', workflow.release_ref, workflow.release_sha256):
        raise ValueError('local start selection belongs to another Workflow')
    expected = {node.node_id for node in workflow.nodes if node.node_kind is WorkflowNodeKind.MODULE}
    if {row['position_id'] for row in policy['bindings']} != expected:
        raise ValueError('local start selection must cover every Module node exactly')
    if type(input_bindings) is not tuple or not input_bindings or any(type(item) is not ModuleInputBinding for item in input_bindings):
        raise ValueError('local start requires nonempty frozen input bindings')
    for item in input_bindings:
        item.validate()
    if len({item.logical_name for item in input_bindings}) != len(input_bindings):
        raise ValueError('local start repeats an input name')
    conditions = _validated_wait_policies(workflow, wait_policies)
    bound_inputs = [item.as_dict() for item in input_bindings]
    if conditions:
        bound_inputs = {'inputs': bound_inputs, 'wait_policies': conditions}
    return dict(graph=project_workflow_release_graph(workflow), workflow_release_ref=workflow.release_ref,
        workflow_release_sha256=workflow.release_sha256, selection_ref=selection.release_ref,
        selection_sha256=selection.release_sha256,
        input_closure_sha256=hashlib.sha256(_json_bytes(bound_inputs)).hexdigest(),
        workflow_execution_id=workflow_execution_id)


def _validate_node_outcome(ledger, workflow_execution_id, dispatch_id, outcome):
    """Execution checks existing Outcome meaning against actual stored node facts.

    The caller holds ledger.guarded across this check and any immutable write.
    Ledger itself does not import Registry's Outcome schema or infer its meaning.
    """
    if type(outcome) is not ModuleOutcome:
        raise TypeError('local outcome must be ModuleOutcome')
    outcome.validate()
    if (outcome.workflow_execution_id, outcome.dispatch_id) != (workflow_execution_id, dispatch_id):
        raise PermissionError('Outcome crossed the requested execution/dispatch')
    observed = ledger.read_node_execution(workflow_execution_id, dispatch_id)
    if observed is None:
        raise ValueError('Outcome lacks its exact Workflow Module request')
    request, run, recorded_attempts, result = observed
    if (request.module_run_id, request.workflow_node_id) != (outcome.module_run_id, outcome.expected_state_id):
        raise ValueError('Outcome lacks its exact Workflow Module request')
    if result is None:
        raise ValueError('Outcome cannot precede the committed Module result')
    attempts = {item.attempt_id: item for item in recorded_attempts}
    if result.module_run != run or any(attempts.get(item.attempt_id) != item for item in result.attempts):
        raise ValueError('Outcome result differs from the recorded Run/Attempt facts')
    if tuple(outcome.attempt_ids) != tuple(item.attempt_id for item in result.attempts):
        raise ValueError('Outcome Attempt references differ from its actual result')
    failures = tuple(item.failure_class for item in result.attempts if item.status != 'completed')
    if outcome.failure_class != (failures[-1] if failures else None):
        raise ValueError('Outcome changes the actual Module failure')
    if any(item.status == 'cancelled' for item in result.attempts):
        raise ValueError('Cancelled Module cannot produce a continuing graph outcome')
    if outcome.output_resolution_refs:
        resolution = result.resolution
        if resolution is None or (outcome.output_resolution_refs, outcome.output_resolution_sha256s) != (
            (resolution.module_output_resolution_id,), (resolution.resolution_sha256,),
        ):
            raise ValueError('Outcome references an uncommitted output resolution')
    return outcome


def _read_node_outcome(ledger, workflow_execution_id, dispatch_id):
    def read():
        outcome = ledger.get_committed_outcome(workflow_execution_id, dispatch_id)
        return None if outcome is None else _validate_node_outcome(ledger, workflow_execution_id, dispatch_id, outcome)
    return ledger.guarded(read)


class WorkflowSelfTestResources:
    """Live graph resources, not a serialized authorization or persistent session.

    Input bindings must resolve to actual bytes in the supplied memory store.
    The caller owns the containing directory; this class owns only its new
    temporary child. Export execution_record before close when evidence is needed.
    wait_policies is an explicit tuple of host bindings with policy_ref,
    expected_state, event_type and target_state. They are validated, copied and
    bound at start; no policy is inferred from input JSON. No-WAIT graphs use ().

    One live instance keeps one workflow_execution_id. Revisiting a graph node
    creates a new dispatch and Module Run; replaying a committed dispatch reads
    its result without another Provider call. An ID or saved JSON cannot restore
    destroyed memory or a Provider session. drive() and execution_record() expose
    technical progress separately from Module output and business acceptance.
    """

    def __init__(self, *, registry, workflow, selection, input_bindings,
                 artifact_host, ledger, workspace_root, workflow_execution_id=None, wait_policies=()):
        if type(artifact_host) is not InMemoryCellArtifactStore or type(ledger) is not InMemoryModuleExecutionLedger:
            raise TypeError("local Workflow requires its actual memory artifact store and Ledger")
        if type(input_bindings) is not tuple or not input_bindings:
            raise ValueError("local Workflow requires frozen input bindings")
        for item in input_bindings:
            if type(item) is not ModuleInputBinding:
                raise TypeError("Workflow input requires ModuleInputBinding")
            item.validate()
            content = artifact_host.read_bytes(item.input_ref, item.input_sha256)
            if hashlib.sha256(content).hexdigest() != item.input_sha256:
                raise ValueError("Workflow initial input content mismatch")
        registry.assert_workflow_execution_allowed(workflow, ModuleExecutionPurpose.EVALUATION)
        self.registry, self.workflow, self.selection = registry, workflow, selection
        self.artifact_host, self.ledger = artifact_host, ledger
        self.input_bindings = input_bindings
        self._lock = RLock()
        self._active = True
        self._cancel = Event()
        self._resource_lost = Event()
        self._driving = False
        self._scopes = {}
        self._records = {}
        self._preparation_failures = {}
        conditions = _validated_wait_policies(workflow, wait_policies)
        self._wait_policies = {row['policy_ref']: row for row in conditions}
        start = _local_start_arguments(registry=registry, workflow=workflow, selection=selection, input_bindings=input_bindings,
                                       workflow_execution_id=workflow_execution_id or 'execution_' + uuid.uuid4().hex,
                                       wait_policies=conditions)
        self.cursor = LocalWorkflowBackend(transition_guard=self._guard_progress)
        self.execution = self.cursor.start_local(**start)
        self.graph = start['graph']
        root = Path(workspace_root).resolve(strict=True)
        if not root.is_dir():
            raise ValueError("Workflow workspace parent must be an existing directory")
        self._temporary = tempfile.TemporaryDirectory(prefix='workflow-self-test-', dir=root)
        self.workspace_root = Path(self._temporary.name).resolve()

    def require_active(self):
        """Return None while this graph's actual temporary resources are usable.

        Raises SelfTestResourceUnavailableError after resource invalidation,
        final close or workspace loss. A user cancellation is a separate signal.
        """
        with self._lock:
            if not self._active or self._resource_lost.is_set() or not self.workspace_root.is_dir():
                raise SelfTestResourceUnavailableError("local Workflow resources are closed or unavailable")

    def user_cancel_requested(self):
        """Return whether the trusted host requested this Workflow's cancellation.

        Reads the shared thread-safe signal only; it does not stop or clean up
        a process itself and does not interpret a resource failure as a user act.
        """
        return self._cancel.is_set()

    def resource_cancel_requested(self):
        """Actual resource loss is distinct from a user's Workflow cancellation."""
        with self._lock:
            return not self._active or self._resource_lost.is_set() or not self.workspace_root.is_dir()

    def _guard_progress(self, operation):
        """Order result/cursor acceptance with resource loss, using existing locks."""
        def guarded():
            with self._lock:
                self.require_active()
                if self.user_cancel_requested():
                    raise asyncio.CancelledError('Workflow cancelled before accepting progress')
                return operation()
        return self.ledger.guarded(guarded)

    def _validate_wait_outcome(self, outcome):
        if outcome.disposition is not ModuleOutcomeDisposition.WAIT:
            return None
        policy = self._wait_policies.get(outcome.wait_policy_ref)
        if policy is None or policy['expected_state'] != outcome.expected_state_id:
            raise ValueError('WAIT lacks its exact explicitly bound condition')
        return policy

    def _committed_outcome(self, dispatch_id):
        def read():
            outcome = _read_node_outcome(self.ledger, self.execution.workflow_execution_id, dispatch_id)
            if outcome is not None:
                self._validate_wait_outcome(outcome)
            return outcome
        return self.ledger.guarded(read)

    def request_cancel(self, reason='user requested Workflow cancellation'):
        """Signal local cancellation and return the resulting cursor snapshot.

        reason is the trusted caller's nonblank explanation. Active node helpers
        observe the same signal; callers must await drive() before final cleanup.
        An already-completed graph stays completed. Invalid or closed resources
        retain the existing ValueError/SelfTestResourceUnavailableError boundary.
        No production cancellation decision, database or remote request is made.
        """
        with self._lock:
            self.require_active()
            if self.cursor.snapshot(self.execution).runtime_status_id == 'completed':
                return self.cursor.snapshot(self.execution)
            self._cancel.set()
            return self.cursor.request_cancel(self.execution, reason=reason)

    def _admit_dispatch(self, dispatch, input_payload):
        if type(dispatch) is not ModuleDispatchRequest:
            raise TypeError("node admission requires an exact ModuleDispatchRequest")
        dispatch.validate()
        if type(input_payload) is not dict:
            raise TypeError("node input callback must return one JSON object")
        data = _json_bytes(input_payload)
        with self._lock:
            self.require_active()
            if self.user_cancel_requested():
                raise asyncio.CancelledError("Workflow cancelled before node admission")
            snapshot = self.cursor.snapshot(self.execution)
            parallel = [group for group in self.workflow.parallel_groups
                        if group.control_node_id == snapshot.current_state
                        and dispatch.current_state_id in group.branch_node_ids]
            expected = DurableExecutionCoordinator._build_dispatch(
                self.workflow, snapshot, self.selection.release_ref, self.selection.release_sha256,
                node_id=dispatch.current_state_id if parallel else None,
                retry_sequence=dispatch.retry_sequence,
            )
            if expected != dispatch:
                raise PermissionError("node dispatch differs from the live graph cursor")
            if dispatch.dispatch_id in self._scopes:
                raise RuntimeError("node dispatch is already claimed; use its committed outcome")
            workspace = self.workspace_root / dispatch.dispatch_id
            workspace.mkdir()
            scope = {'dispatch': dispatch, 'input_bytes': data, 'workspace': workspace,
                     'run_id': _identity('module_run', self.execution.workflow_execution_id, dispatch.dispatch_id),
                     'key': _identity('node', self.execution.workflow_execution_id, dispatch.dispatch_id),
                     'request': None, 'active': True}
            self._scopes[dispatch.dispatch_id] = scope
            return scope

    def check_node_scope(self, *, request, workflow, variant, registry, artifact_host, ledger, workspace_root):
        """Verify the actual request and shared objects on every child-resource use."""
        if type(request) is not WorkflowModuleExecutionRequest:
            raise TypeError("node scope requires WorkflowModuleExecutionRequest")
        request.validate()
        with self._lock:
            self.require_active()
            if (workflow is not self.workflow or variant is not self.selection or registry is not self.registry
                    or artifact_host is not self.artifact_host or ledger is not self.ledger):
                raise PermissionError("node resources belong to another Workflow")
            scope = self._scopes.get(request.dispatch_id)
            if scope is None or not scope['active']:
                raise PermissionError("node has no live admitted dispatch")
            dispatch = scope['dispatch']
            if (request.workflow_execution_id != self.execution.workflow_execution_id
                    or request.workflow_node_id != dispatch.current_state_id
                    or request.module_run_id != scope['run_id'] or request.idempotency_key != scope['key']
                    or (request.module_release_ref, request.module_release_sha256) !=
                       (dispatch.module_release_ref, dispatch.module_release_sha256)
                    or Path(workspace_root).resolve(strict=True) != scope['workspace']):
                raise PermissionError("node request crossed its admitted execution/dispatch/workspace")
            if request.attempt_ordinal != 1 or request.parent_attempt_id is not None:
                raise ValueError("each local graph dispatch begins a separate initial Attempt")
            task = tuple(item for item in request.inputs if item.logical_name == 'task_input')
            if len(task) != 1 or self.artifact_host.read_bytes(task[0].input_ref, task[0].input_sha256) != scope['input_bytes']:
                raise PermissionError("node input differs from the admitted callback result")
            if scope['request'] is None:
                scope['request'] = request
            elif scope['request'] != request:
                raise PermissionError("node request changed after resource binding")

    def node_records(self, *, committed_only=False):
        """Read actual node facts in graph-visit order, without reparsing Provider logs."""
        with self._lock:
            snapshot = deepcopy(tuple(self._records.items()))
        # Kernel guards take the Ledger lock before checking parent resources.
        # Never hold the parent lock while entering the Ledger in the other direction.
        rows = [record for identity, record in snapshot
                if not committed_only or self._committed_outcome(identity) is not None]
        return tuple(sorted(rows, key=lambda row: (
            row['dispatch']['transition_sequence'], row['dispatch']['current_state_id'], row['dispatch']['retry_sequence'])))

    def execution_record(self):
        """Return local node logs, initial/wait bindings and committed Outcomes.

        Capture this view before final cleanup. Each Outcome is read through
        Execution's original identity/result validation and serialized with its
        existing as_dict(), including wait/failure routing and its content hash.
        Missing Outcomes stay absent, not synthesized from cursor events.
        The parent snapshot lock is released before any Ledger read. Invalid
        stored facts propagate their validation error. This is a non-persistent
        evidence export, not a production Ledger or a durable recovery handle.

        nodes includes committed kernel results even if their Outcome callback
        failed; outcomes includes only committed routing decisions. After resource
        loss, evidence remains readable until close, but cannot authorize dispatch.
        A last snapshot may still say running; inspect drive's result or exception
        as well. This method does not repair the snapshot or invent an Outcome.
        """
        with self._lock:
            if not self._active:
                raise RuntimeError("Workflow evidence must be captured before final resource cleanup")
            record = {'workflow_execution_id': self.execution.workflow_execution_id,
                      'workflow_release_ref': self.workflow.release_ref, 'workflow_release_sha256': self.workflow.release_sha256,
                      'execution_variant_ref': self.selection.release_ref, 'execution_variant_sha256': self.selection.release_sha256,
                      'persistence': 'not_requested', 'snapshot': asdict(self.cursor.snapshot(self.execution)),
                      'input_bindings': [item.as_dict() for item in self.input_bindings],
                      'wait_policies': deepcopy(list(self._wait_policies.values())),
                      'nodes': self.node_records(), 'preparation_failures': deepcopy(self._preparation_failures)}
        outcomes = []
        for node in record['nodes']:
            dispatch = node['dispatch']
            if (node['workflow_execution_id'] != record['workflow_execution_id']
                    or dispatch['workflow_execution_id'] != record['workflow_execution_id']):
                raise PermissionError('node evidence crossed Workflow execution')
            outcome = self._committed_outcome(dispatch['dispatch_id'])
            if outcome is not None:
                outcomes.append(outcome.as_dict())
        record['outcomes'] = outcomes
        return record

    async def drive(self, bridge, *, max_dispatches=100):
        """Execute or continue this live graph through the existing Coordinator.

        Returns:
            DurableExecutionProgress. wait requires a matching resume_event;
            another drive without that event makes no new node call. terminal
            means graph completion, not business acceptance. Other stop reasons
            report where this bounded drive stopped, not automatic retry permission.
        Raises:
            RuntimeError: Another drive is active on this instance.
            PermissionError: The bridge belongs to another resource instance.
            SelfTestResourceUnavailableError: Resources closed or disappeared;
                the instance cannot resume even if its last snapshot says running.
            Exception: Native preparation, validation or callback failure. A
                committed kernel result may be reused after a pure Outcome
                callback failure, while the same resources remain live.
        Effects:
            May call Providers for new dispatches. Cancellation stops dispatch
            and drains active work; it is not a resumable WAIT. After failure,
            retain available execution_record evidence before closing resources.
        """
        with self._lock:
            self.require_active()
            if self._driving:
                raise RuntimeError("this Workflow already has an active drive")
            if not isinstance(bridge, LocalWorkflowModuleBridge) or bridge.resources is not self:
                raise PermissionError("Workflow requires its own real node bridge")
            self._driving = True
        coordinator = DurableExecutionCoordinator(cursor=self.cursor, release_registry=self.registry, activity_bridge=bridge)
        task = asyncio.create_task(coordinator.drive_started(execution=self.execution, workflow=self.workflow,
            selection=self.selection, expected_start_sha256=self.cursor.start_sha256, max_dispatches=max_dispatches))
        try:
            while True:
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    self.request_cancel()
                    if task.done():
                        return task.result()
                    # Do not abandon threads/Provider processes when the caller is cancelled.
                    continue
        finally:
            with self._lock:
                self._driving = False

    async def resume_event(self, event):
        """Resume an acknowledged wait with one exact, host-provided ExternalEvent.

        Execution resolves the cursor's actual committed WAIT and its explicitly
        bound policy, not merely any legal Workflow edge. The cursor enforces
        the same wait ref/hash, execution/state identity and idempotency. Returns an
        ExecutionSnapshot. Unknown edges and non-wait transitions are ValueError;
        crossed executions are PermissionError. An active drive is RuntimeError.
        This only resumes the same living process; closed resources cannot resume.

        A valid new event clears the wait and moves the cursor to target_state;
        this call never invokes a Provider. Call drive() to execute the target.
        Same event_id and identical content returns the existing snapshot without
        another transition. A valid target in the graph is insufficient: the
        actual WAIT must bind this expected_state/event_type/target_state triple.

        Missing WAIT, mismatched conditions or changed content under the same
        event_id raises ValueError without advancing. Closed resources raise
        SelfTestResourceUnavailableError. These exceptions have no dedicated
        error_code; their message text is diagnostic, not a machine enum.
        """
        self.require_active()
        if self._driving:
            raise RuntimeError("wait for the current drive before resuming a local wait")
        from ..contracts.durability_topology_definition import ExternalEvent
        if type(event) is not ExternalEvent:
            raise TypeError('local wait requires an exact ExternalEvent')
        event.validate()
        if event.event_type == 'module_outcome':
            raise ValueError('host wait events cannot impersonate Module outcomes')
        snapshot = self.cursor.snapshot(self.execution)
        prior = next((item for item in snapshot.applied_events if item.event_id == event.event_id), None)
        if prior is not None:
            if prior != event:
                raise ValueError('event identity was reused with different content')
            return await self.cursor.resume_event(self.execution, event)
        binding = self.cursor.waiting_outcome(self.execution)
        if binding is None:
            raise ValueError('local Workflow has no acknowledged wait')
        outcome = self._committed_outcome(binding['dispatch_id'])
        if (outcome is None or outcome.disposition is not ModuleOutcomeDisposition.WAIT
                or (outcome.outcome_ref, outcome.outcome_sha256, outcome.expected_state_id) !=
                   (binding['outcome_ref'], binding['outcome_sha256'], binding['expected_state'])):
            raise ValueError('cursor WAIT does not match its committed Outcome')
        policy = self._validate_wait_outcome(outcome)
        if (event.expected_state, event.event_type, event.target_state) != (
                policy['expected_state'], policy['event_type'], policy['target_state']):
            raise ValueError('event does not match the current WAIT condition')
        return await self.cursor.resume_event(self.execution, event,
            expected_wait_ref=outcome.outcome_ref, expected_wait_sha256=outcome.outcome_sha256)

    def close(self):
        """Clean only this instance's temporary directory after node finalization.

        Returns None, idempotently after close. If nodes are still active, revoke
        resources and raise RuntimeError: await drive() to preserve their actual
        failures, export execution_record(), then close again. This is resource
        loss, not user cancellation. Native cleanup errors propagate; no durable
        recovery or deletion of any host directory is provided.

        Rejection due to active nodes is not a no-op: the resources are already
        invalidated. Repeated close after completed cleanup is harmless.
        """
        with self._lock:
            if not self._active:
                return
            if self._driving or any(scope['active'] for scope in self._scopes.values()):
                self._resource_lost.set()
                raise RuntimeError("Workflow has active nodes; await drive finalization before closing resources")
            self._active = False
            self.cursor.close()
        self._temporary.cleanup()

    def __enter__(self):
        self.require_active()
        return self

    def __exit__(self, *_):
        self.close()


class LocalWorkflowModuleBridge:
    """The existing Activity bridge protocol backed by real Runtime Module calls.

    input_for_node(dispatch, committed_records) returns one JSON object.
    outcome_for_node(dispatch, result, record) returns an exact ModuleOutcome.
    adapter_for_node(node_id, profile, artifact_host, workspace_root) supplies the
    already-selected Adapter, not a model selection or alternative runner.
    tool_session_factory_for_node has the same arguments and returns only this
    node's trusted factory, or None. It never inherits a sibling/parent factory.
    """

    def __init__(self, *, resources, input_for_node, outcome_for_node, adapter_for_node,
                 local_resources_by_node=None, tool_session_factory_for_node=None):
        if type(resources) is not WorkflowSelfTestResources:
            raise TypeError("node bridge requires live WorkflowSelfTestResources")
        if not all(callable(item) for item in (input_for_node, outcome_for_node, adapter_for_node)):
            raise TypeError("node bridge requires explicit host callbacks")
        if tool_session_factory_for_node is not None and not callable(tool_session_factory_for_node):
            raise TypeError("node tool factory callback must be callable or None")
        self.resources = resources
        self.input_for_node, self.outcome_for_node, self.adapter_for_node = input_for_node, outcome_for_node, adapter_for_node
        self.tool_session_factory_for_node = tool_session_factory_for_node
        self._local_resources = dict(local_resources_by_node or {})
        module_nodes = {node.node_id for node in resources.workflow.nodes if node.node_kind is WorkflowNodeKind.MODULE}
        if not set(self._local_resources) <= module_nodes or any(type(data) is not bytes for data in self._local_resources.values()):
            raise ValueError("local node resources must name exact Module nodes and frozen bytes")

    def get_committed_outcome(self, workflow_execution_id, dispatch_id):
        """Return a validated committed node Outcome, or None for an absent key.

        Both IDs must refer to this parent Workflow. Execution rechecks stored
        Outcome identity and recorded request/Run/Attempt facts under the Ledger
        lock, including on replay; malformed or crossed records raise the existing
        TypeError/ValueError/PermissionError. This read never invokes an Adapter.
        """
        if workflow_execution_id != self.resources.execution.workflow_execution_id:
            raise PermissionError("bridge crossed Workflow execution")
        return self.resources._committed_outcome(dispatch_id)

    def dispatch(self, request):
        """Execute/replay one exact ModuleDispatchRequest through the Module kernel.

        Returns the host callback's validated, committed ModuleOutcome. Exact
        prior outcomes replay without Provider entry; a committed kernel result
        can resume a failed pure outcome callback without rerunning the node.
        Inputs, Profile, workspace and shared objects must match the live graph.
        Native preparation/execution/validation exceptions propagate with stored
        evidence retained. Whole-graph cancellation raises asyncio.CancelledError
        only after available node facts are captured; no evaluation follows it.
        Effects are bounded to this graph's temporary resources and declared tools.
        """
        resources = self.resources
        resources.require_active()
        if type(request) is not ModuleDispatchRequest:
            raise TypeError("bridge requires an exact ModuleDispatchRequest")
        request.validate()
        if resources.user_cancel_requested():
            raise asyncio.CancelledError("Workflow cancelled before dispatch")
        with resources._lock:
            previous_scope = resources._scopes.get(request.dispatch_id)
            previous_record = deepcopy(resources._records.get(request.dispatch_id))
            if previous_scope is not None and previous_scope['dispatch'] != request:
                raise PermissionError("dispatch identity was reused with different content")
        prior = self.get_committed_outcome(request.workflow_execution_id, request.dispatch_id)
        if prior is not None:
            return prior
        if previous_record is not None:
            if previous_scope['active']:
                raise RuntimeError("node dispatch is still finalizing")
            # A host callback may fail after the kernel has committed its result.
            # Resume the pure outcome step, never invoke the Adapter again.
            result, = (item for item in resources.ledger.results_for_execution(request.workflow_execution_id)
                       if item.module_run.module_run_id == previous_scope['run_id'])
            record = {key: value for key, value in previous_record.items() if key != 'dispatch'}
            return self._commit_node_outcome(request, result, record)
        payload = self.input_for_node(request, resources.node_records(committed_only=True))
        scope = resources._admit_dispatch(request, payload)
        try:
            policy = resources.selection.policy_document()
            binding, = (row for row in policy['bindings'] if row['position_id'] == request.current_state_id)
            profile = resources.registry.get_execution_profile(binding['execution_profile_release_ref'],
                                                                binding['execution_profile_release_sha256'])
            adapter = self.adapter_for_node(request.current_state_id, profile, resources.artifact_host, scope['workspace'])
            factory = (None if self.tool_session_factory_for_node is None else
                       self.tool_session_factory_for_node(request.current_state_id, profile,
                                                          resources.artifact_host, scope['workspace']))
            result, record = _run_prepared_workflow_node(
                registry=resources.registry, workflow=resources.workflow, selection=resources.selection,
                node_id=request.current_state_id, input_payload=payload, idempotency_key=scope['key'],
                workflow_execution_id=request.workflow_execution_id, dispatch_id=request.dispatch_id,
                module_run_id=scope['run_id'], artifact_host=resources.artifact_host, ledger=resources.ledger,
                workspace_root=scope['workspace'], adapter=adapter, workflow_resources=resources,
                local_resources=self._local_resources.get(request.current_state_id),
                tool_session_factory=factory, user_cancel_requested=resources.user_cancel_requested,
                resource_cancel_requested=resources.resource_cancel_requested)
            with resources._lock:
                resources._records[request.dispatch_id] = {'dispatch': asdict(request), **deepcopy(record)}
            return self._commit_node_outcome(request, result, record)
        except Exception as exc:
            with resources._lock:
                if request.dispatch_id not in resources._records:
                    resources._preparation_failures[request.dispatch_id] = {'error_type': type(exc).__name__, 'detail': str(exc)}
            raise
        finally:
            with resources._lock:
                scope['active'] = False

    def _commit_node_outcome(self, request, result, record):
        resources = self.resources
        if resources.user_cancel_requested() or any(item.status == 'cancelled' for item in result.attempts):
            resources.request_cancel('Workflow node received user cancellation')
            raise asyncio.CancelledError("Workflow cancelled after node finalization")
        if resources.resource_cancel_requested():
            raise SelfTestResourceUnavailableError("Workflow resources became unavailable after node finalization")
        outcome = self.outcome_for_node(request, result, deepcopy(record))
        def commit():
            _validate_node_outcome(resources.ledger, request.workflow_execution_id, request.dispatch_id, outcome)
            resources._validate_wait_outcome(outcome)
            if outcome.disposition is ModuleOutcomeDisposition.TRANSITION and outcome.target_state_id not in resources.graph.allowed_targets(request.current_state_id):
                raise ValueError("node callback selected an undeclared graph target")
            return resources.ledger.commit_outcome(request.workflow_execution_id, request.dispatch_id, outcome)
        return resources._guard_progress(commit)


__all__ = ['WorkflowSelfTestResources', 'LocalWorkflowModuleBridge']
