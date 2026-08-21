from __future__ import annotations

import asyncio
from dataclasses import replace
from threading import Lock
import time

import pytest

from agent_runtime.contracts.durability_execution_definition import (
    BackendEvent,
    BackendExecutionRef,
    ExecutionSnapshot,
    ExternalEvent,
)
from agent_runtime.contracts.execution_host_definition import (
    RuntimeCancellationRequest,
    RuntimeWorkflowStartRequest,
)
from agent_runtime.contracts.registry_release_definition import (
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    ModuleEntryPolicy,
    ModuleKind,
    OutputResolutionPolicy,
    RuntimeModuleRelease,
    RetryPolicyRelease,
    SchemaAssetRelease,
    WorkflowEdge,
    WorkflowNodeBinding,
    WorkflowNodeKind,
    WorkflowParallelGroupBinding,
    WorkflowParallelJoinPolicy,
    WorkflowRelease,
)
from agent_runtime.contracts.registry_workflow_definition import (
    ModuleDispatchRequest,
    ModuleOutcome,
    ModuleOutcomeDisposition,
)
from agent_runtime.durability.durability_workflow_coordination import (
    DurableExecutionCoordinator,
    DurableExecutionStopReason,
)
from agent_runtime.registry.registry_graph_projection import (
    RUNTIME_TERMINAL_STATE_ID,
    project_workflow_release_graph,
)
from agent_runtime.registry.registry_release_compilation import (
    runtime_owned_policy_schema_assets,
)
from agent_runtime.registry.registry_release_registration import (
    RuntimeReleaseBundle,
    RuntimeReleaseRegistry,
)


def _policy_releases() -> tuple[
    tuple[object, ...],
    BehaviorPolicyRelease,
    EvaluationPolicyRelease,
    RetryPolicyRelease,
]:
    schemas = runtime_owned_policy_schema_assets()
    schema_by_ref = {schema.release_ref: schema for schema in schemas}
    behavior_schema = schema_by_ref["schema:runtime_behavior_policy@v1"]
    evaluation_schema = schema_by_ref["schema:runtime_evaluation_policy@v1"]
    retry_schema = schema_by_ref["schema:runtime_retry_policy@v1"]
    behavior = BehaviorPolicyRelease.build(
        policy_id="isolated",
        policy_version="1",
        release_ref="behavior-policy:isolated@1",
        policy_schema_ref=behavior_schema.release_ref,
        policy_schema_sha256=behavior_schema.schema_sha256,
        policy_document={
            "context_isolation": "workflow_execution_isolated",
        },
    )
    evaluation = EvaluationPolicyRelease.build(
        policy_id="none",
        policy_version="1",
        release_ref="evaluation-policy:none@1",
        policy_schema_ref=evaluation_schema.release_ref,
        policy_schema_sha256=evaluation_schema.schema_sha256,
        policy_document={"evaluation_mode": "none"},
    )
    retry = RetryPolicyRelease.build(
        policy_id="bounded",
        policy_version="1",
        release_ref="retry-policy:bounded@1",
        policy_schema_ref=retry_schema.release_ref,
        policy_schema_sha256=retry_schema.schema_sha256,
        policy_document={"max_attempts": 3},
    )
    return schemas, behavior, evaluation, retry


def _module_schema_assets() -> tuple[SchemaAssetRelease, SchemaAssetRelease]:
    def build(schema_id: str) -> SchemaAssetRelease:
        release_ref = f"schema:{schema_id}@1"
        return SchemaAssetRelease.build(
            schema_asset_id=schema_id.replace("-", "_"),
            schema_asset_version="1",
            release_ref=release_ref,
            schema_document={
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": release_ref,
                "type": "object",
                "additionalProperties": True,
            },
        )

    return build("parallel_input"), build("parallel_output")


def _register_release_closure(
    registry: RuntimeReleaseRegistry,
    module: RuntimeModuleRelease,
    workflow: WorkflowRelease,
) -> None:
    schemas, behavior, evaluation, retry = _policy_releases()
    input_schema, output_schema = _module_schema_assets()
    registry.register_bundle(
        RuntimeReleaseBundle(
            schema_assets=(*schemas, input_schema, output_schema),
            behavior_policies=(behavior,),
            evaluation_policies=(evaluation,),
            retry_policies=(retry,),
            modules=(module,),
            workflows=(workflow,),
        )
    )


def _module(
    *,
    behavior_policy_sha256: str | None = None,
    evaluation_policy_sha256: str | None = None,
    retry_policy_sha256: str | None = None,
    input_schema_sha256: str | None = None,
    output_schema_sha256: str | None = None,
) -> RuntimeModuleRelease:
    if (
        behavior_policy_sha256 is None
        or evaluation_policy_sha256 is None
        or retry_policy_sha256 is None
    ):
        _, behavior, evaluation, retry = _policy_releases()
        behavior_policy_sha256 = behavior.release_sha256
        evaluation_policy_sha256 = evaluation.release_sha256
        retry_policy_sha256 = retry.release_sha256
    if input_schema_sha256 is None or output_schema_sha256 is None:
        input_schema, output_schema = _module_schema_assets()
        input_schema_sha256 = input_schema.schema_sha256
        output_schema_sha256 = output_schema.schema_sha256
    return RuntimeModuleRelease.build(
        module_id="parallel_test_module",
        module_version="1.0.0",
        release_ref="runtime-module:parallel_test_module@1",
        module_kind=ModuleKind.DETERMINISTIC,
        owner_contract_ref="design-doc:parallel-test@1",
        owner_contract_sha256="1" * 64,
        executable_ref="python:tests.parallel_test_module",
        executable_sha256="2" * 64,
        input_schema_ref="schema:parallel_input@1",
        input_schema_sha256=input_schema_sha256,
        output_schema_ref="schema:parallel_output@1",
        output_schema_sha256=output_schema_sha256,
        prompt_bundle_ref=None,
        prompt_bundle_sha256=None,
        declared_operation_ids=(),
        behavior_policy_ref="behavior-policy:isolated@1",
        behavior_policy_sha256=behavior_policy_sha256,
        evaluation_policy_ref="evaluation-policy:none@1",
        evaluation_policy_sha256=evaluation_policy_sha256,
        retry_policy_ref="retry-policy:bounded@1",
        retry_policy_sha256=retry_policy_sha256,
        compatible_transport_kinds=("in_process_test",),
        entry_policy=ModuleEntryPolicy.WORKFLOW_BOUND,
        output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
    )


def _module_node(node_id: str, module: RuntimeModuleRelease) -> WorkflowNodeBinding:
    return WorkflowNodeBinding(
        node_id=node_id,
        node_kind=WorkflowNodeKind.MODULE,
        module_release_ref=module.release_ref,
        module_release_sha256=module.release_sha256,
        input_mapping_ref=f"input-map:{node_id}@1",
        input_mapping_sha256="8" * 64,
    )


def _workflow(module: RuntimeModuleRelease) -> WorkflowRelease:
    return WorkflowRelease.build(
        workflow_id="parallel_review",
        workflow_version="1.0.0",
        workflow_contract_version="contract_v1",
        release_ref="runtime-workflow:parallel_review@1",
        owner_contract_ref="design-doc:parallel-test@1",
        owner_contract_sha256="9" * 64,
        graph_ref="python:tests.parallel_review_graph",
        graph_sha256="a" * 64,
        initial_node_id="review_parallel",
        nodes=(
            WorkflowNodeBinding(
                node_id="review_parallel",
                node_kind=WorkflowNodeKind.CONTROL,
                module_release_ref=None,
                module_release_sha256=None,
                input_mapping_ref=None,
                input_mapping_sha256=None,
            ),
            _module_node("fidelity_review", module),
            _module_node("reader_gain_review", module),
            _module_node("review_aggregate", module),
        ),
        edges=(
            WorkflowEdge(
                source_node_id="review_parallel",
                outcome_id="all_completed",
                target_node_id="review_aggregate",
                terminal=False,
            ),
            WorkflowEdge(
                source_node_id="fidelity_review",
                outcome_id="completed",
                target_node_id="review_aggregate",
                terminal=False,
            ),
            WorkflowEdge(
                source_node_id="reader_gain_review",
                outcome_id="completed",
                target_node_id="review_aggregate",
                terminal=False,
            ),
            WorkflowEdge(
                source_node_id="review_aggregate",
                outcome_id="completed",
                target_node_id=None,
                terminal=True,
            ),
        ),
        parallel_groups=(
            WorkflowParallelGroupBinding(
                group_id="overview_review_group",
                control_node_id="review_parallel",
                branch_node_ids=("fidelity_review", "reader_gain_review"),
                join_node_id="review_aggregate",
                completion_outcome_id="all_completed",
                join_policy=WorkflowParallelJoinPolicy.ALL_REQUIRED,
            ),
        ),
        authorization_manifest_ref="authorization-manifest:parallel-test@1",
        authorization_manifest_sha256="b" * 64,
        execution_release_ref="execution-release:parallel-test@1",
        execution_release_sha256="c" * 64,
    )


def _workflow_with_prelude(module: RuntimeModuleRelease) -> WorkflowRelease:
    base = _workflow(module)
    return WorkflowRelease.build(
        workflow_id="parallel_review_with_prelude",
        workflow_version="1.0.0",
        workflow_contract_version=base.workflow_contract_version,
        release_ref="runtime-workflow:parallel-review-with-prelude@1",
        owner_contract_ref=base.owner_contract_ref,
        owner_contract_sha256=base.owner_contract_sha256,
        graph_ref="python:tests.parallel_review_with_prelude_graph",
        graph_sha256="d" * 64,
        initial_node_id="prelude",
        nodes=(_module_node("prelude", module), *base.nodes),
        edges=(
            WorkflowEdge(
                source_node_id="prelude",
                outcome_id="completed",
                target_node_id="review_parallel",
                terminal=False,
            ),
            *base.edges,
        ),
        parallel_groups=base.parallel_groups,
        authorization_manifest_ref=base.authorization_manifest_ref,
        authorization_manifest_sha256=base.authorization_manifest_sha256,
        execution_release_ref=base.execution_release_ref,
        execution_release_sha256=base.execution_release_sha256,
    )


def _request(workflow: WorkflowRelease) -> RuntimeWorkflowStartRequest:
    return RuntimeWorkflowStartRequest(
        workflow_execution_id="parallel_execution_001",
        tenant_id="tenant_001",
        cell_id="cell_001",
        workflow_release_ref=workflow.release_ref,
        workflow_release_sha256=workflow.release_sha256,
        execution_release_ref=workflow.execution_release_ref,
        execution_release_sha256=workflow.execution_release_sha256,
        execution_profile_selection_ref="profile-selection:parallel-test@1",
        execution_profile_selection_sha256="d" * 64,
        runtime_execution_binding_ref="runtime-binding:parallel-test@1",
        runtime_execution_binding_sha256="e" * 64,
        execution_authorization_binding_ref="authorization:parallel-test@1",
        execution_authorization_binding_sha256="f" * 64,
        execution_start_admission_ref="start-admission:parallel-test@1",
        execution_start_admission_sha256="0" * 64,
        execution_input_package_refs=("input-package:parallel-test@1",),
        execution_input_package_sha256="1" * 64,
        idempotency_key="parallel_execution_001",
        recorded_at_utc="2026-08-10T12:00:00Z",
    )


class _Cursor:
    def __init__(self, workflow: WorkflowRelease) -> None:
        self.graph = project_workflow_release_graph(workflow)
        self.execution = BackendExecutionRef(
            backend_id="test_backend",
            backend_namespace="test",
            backend_execution_id="parallel-execution-001",
            workflow_execution_id="parallel_execution_001",
        )
        self.current_state = self.graph.initial_state
        self.events: list[ExternalEvent] = []

    def _snapshot(self) -> ExecutionSnapshot:
        terminal = self.current_state in self.graph.terminal_states
        return ExecutionSnapshot(
            backend_id=self.execution.backend_id,
            backend_execution_id=self.execution.backend_execution_id,
            workflow_execution_id=self.execution.workflow_execution_id,
            workflow_id=self.graph.workflow_id,
            graph_sha256=self.graph.graph_sha256,
            start_request_sha256="2" * 64,
            current_state=self.current_state,
            terminal=terminal,
            applied_events=tuple(self.events),
            runtime_status_id="completed" if terminal else "running",
        )

    async def start(self, request: RuntimeWorkflowStartRequest) -> BackendExecutionRef:
        request.validate()
        return self.execution

    async def apply_external_event(
        self, execution: BackendExecutionRef, event: ExternalEvent
    ) -> ExecutionSnapshot:
        assert execution == self.execution
        event.validate()
        prior = next((row for row in self.events if row.event_id == event.event_id), None)
        if prior is not None:
            assert prior == event
            return self._snapshot()
        assert event.expected_state == self.current_state
        assert event.target_state in self.graph.allowed_targets(self.current_state)
        self.events.append(event)
        self.current_state = event.target_state
        return self._snapshot()

    async def query(self, execution: BackendExecutionRef) -> ExecutionSnapshot:
        assert execution == self.execution
        return self._snapshot()

    async def request_cancellation(
        self,
        execution: BackendExecutionRef,
        request: RuntimeCancellationRequest,
    ) -> ExecutionSnapshot:
        raise NotImplementedError

    async def recover(self, execution: BackendExecutionRef) -> ExecutionSnapshot:
        return await self.query(execution)

    async def list_events(self, execution: BackendExecutionRef) -> tuple[BackendEvent, ...]:
        assert execution == self.execution
        return ()


class _Bridge:
    def __init__(self, *, fail_fidelity_once: bool = False) -> None:
        self.fail_fidelity_once = fail_fidelity_once
        self.outcomes: dict[str, ModuleOutcome] = {}
        self.call_count_by_node: dict[str, int] = {}
        self.active_branches = 0
        self.max_active_branches = 0
        self._lock = Lock()

    def dispatch(self, request: ModuleDispatchRequest) -> ModuleOutcome:
        request.validate()
        with self._lock:
            self.call_count_by_node[request.current_state_id] = (
                self.call_count_by_node.get(request.current_state_id, 0) + 1
            )
            is_branch = request.current_state_id in {
                "fidelity_review",
                "reader_gain_review",
            }
            if is_branch:
                self.active_branches += 1
                self.max_active_branches = max(
                    self.max_active_branches,
                    self.active_branches,
                )
        if is_branch:
            time.sleep(0.04)
        try:
            retryable = (
                self.fail_fidelity_once
                and request.current_state_id == "fidelity_review"
                and request.retry_sequence == 0
            )
            outcome = ModuleOutcome.build(
                dispatch_id=request.dispatch_id,
                workflow_execution_id=request.workflow_execution_id,
                expected_state_id=request.current_state_id,
                disposition=(
                    ModuleOutcomeDisposition.RETRYABLE_FAILURE
                    if retryable
                    else ModuleOutcomeDisposition.TRANSITION
                ),
                target_state_id=(
                    None
                    if retryable
                    else (
                        RUNTIME_TERMINAL_STATE_ID
                        if request.current_state_id == "review_aggregate"
                        else (
                            "review_parallel"
                            if request.current_state_id == "prelude"
                            else "review_aggregate"
                        )
                    )
                ),
                failure_class="provider_timeout" if retryable else None,
                outcome_ref=f"module-outcome:{request.dispatch_id}",
            )
            with self._lock:
                self.outcomes[request.dispatch_id] = outcome
            return outcome
        finally:
            if is_branch:
                with self._lock:
                    self.active_branches -= 1

    def get_committed_outcome(
        self, workflow_execution_id: str, dispatch_id: str
    ) -> ModuleOutcome | None:
        assert workflow_execution_id == "parallel_execution_001"
        with self._lock:
            return self.outcomes.get(dispatch_id)


def _coordinator(
    *,
    fail_fidelity_once: bool = False,
    max_parallel_dispatches: int = 16,
) -> tuple[DurableExecutionCoordinator, RuntimeWorkflowStartRequest, _Cursor, _Bridge]:
    module = _module()
    workflow = _workflow(module)
    registry = RuntimeReleaseRegistry()
    _register_release_closure(registry, module, workflow)
    cursor = _Cursor(workflow)
    bridge = _Bridge(fail_fidelity_once=fail_fidelity_once)
    return (
        DurableExecutionCoordinator(
            cursor=cursor,
            release_registry=registry,
            activity_bridge=bridge,
            max_parallel_dispatches=max_parallel_dispatches,
        ),
        _request(workflow),
        cursor,
        bridge,
    )


def test_parallel_group_release_round_trips_and_rejects_ordinary_branch_entry() -> None:
    module = _module()
    workflow = _workflow(module)

    assert WorkflowRelease.from_dict(workflow.as_dict()) == workflow
    invalid = replace(
        workflow,
        edges=(
            *workflow.edges,
            WorkflowEdge(
                source_node_id="review_aggregate",
                outcome_id="illegal_branch_entry",
                target_node_id="fidelity_review",
                terminal=False,
            ),
        ),
    )
    invalid = replace(invalid, release_sha256="0" * 64)
    with pytest.raises(ValueError, match="ordinary graph edge"):
        invalid.validate()

    bypass = replace(
        workflow,
        edges=(
            *workflow.edges,
            WorkflowEdge(
                source_node_id="review_parallel",
                outcome_id="skip_branches",
                target_node_id="review_aggregate",
                terminal=False,
            ),
        ),
        release_sha256="0" * 64,
    )
    with pytest.raises(ValueError, match="only its declared join route"):
        bypass.validate()


def test_parallel_group_dispatches_branches_concurrently_and_joins_once() -> None:
    coordinator, request, cursor, bridge = _coordinator()

    progress = asyncio.run(coordinator.drive(request, max_dispatches=3))

    assert progress.stop_reason is DurableExecutionStopReason.TERMINAL
    assert progress.dispatch_count == 3
    assert bridge.max_active_branches == 2
    assert bridge.call_count_by_node == {
        "fidelity_review": 1,
        "reader_gain_review": 1,
        "review_aggregate": 1,
    }
    assert [event.event_type for event in cursor.events] == [
        "all_completed",
        "module_outcome",
    ]


def test_parallel_group_rejects_call_budget_that_can_never_admit_fanout() -> None:
    coordinator, request, _cursor, bridge = _coordinator()

    with pytest.raises(ValueError, match="at least 2 dispatches are required"):
        asyncio.run(coordinator.drive(request, max_dispatches=1))
    assert bridge.call_count_by_node == {}


def test_parallel_group_waits_for_next_drive_instead_of_partial_dispatch() -> None:
    module = _module()
    workflow = _workflow_with_prelude(module)
    registry = RuntimeReleaseRegistry()
    _register_release_closure(registry, module, workflow)
    cursor = _Cursor(workflow)
    bridge = _Bridge()
    coordinator = DurableExecutionCoordinator(
        cursor=cursor,
        release_registry=registry,
        activity_bridge=bridge,
    )
    request = _request(workflow)

    first = asyncio.run(coordinator.drive(request, max_dispatches=2))

    assert first.stop_reason is DurableExecutionStopReason.DISPATCH_LIMIT
    assert first.dispatch_count == 1
    assert first.snapshot.current_state == "review_parallel"
    assert bridge.call_count_by_node == {"prelude": 1}

    second = asyncio.run(coordinator.drive(request, max_dispatches=3))

    assert bridge.call_count_by_node == {
        "prelude": 1,
        "fidelity_review": 1,
        "reader_gain_review": 1,
        "review_aggregate": 1,
    }
    assert second.stop_reason is DurableExecutionStopReason.TERMINAL


def test_parallel_group_obeys_explicit_concurrency_limit() -> None:
    coordinator, request, _cursor, bridge = _coordinator(
        max_parallel_dispatches=1
    )

    progress = asyncio.run(coordinator.drive(request, max_dispatches=3))

    assert progress.stop_reason is DurableExecutionStopReason.TERMINAL
    assert bridge.max_active_branches == 1


def test_committed_parallel_wait_blocks_persistent_sibling_failure_on_replay() -> None:
    module = _module()
    workflow = _workflow(module)
    registry = RuntimeReleaseRegistry()
    _register_release_closure(registry, module, workflow)
    cursor = _Cursor(workflow)

    class _WaitBridge(_Bridge):
        def dispatch(self, request: ModuleDispatchRequest) -> ModuleOutcome:
            if request.current_state_id == "reader_gain_review":
                request.validate()
                with self._lock:
                    self.call_count_by_node[request.current_state_id] = (
                        self.call_count_by_node.get(request.current_state_id, 0) + 1
                    )
                raise RuntimeError("persistent sibling failure")
            if request.current_state_id != "fidelity_review":
                return super().dispatch(request)
            request.validate()
            with self._lock:
                self.call_count_by_node[request.current_state_id] = (
                    self.call_count_by_node.get(request.current_state_id, 0) + 1
                )
            outcome = ModuleOutcome.build(
                dispatch_id=request.dispatch_id,
                workflow_execution_id=request.workflow_execution_id,
                expected_state_id=request.current_state_id,
                disposition=ModuleOutcomeDisposition.WAIT,
                wait_policy_ref="wait-policy:external-review@1",
                outcome_ref=f"module-outcome:{request.dispatch_id}",
            )
            with self._lock:
                self.outcomes[request.dispatch_id] = outcome
            return outcome

    bridge = _WaitBridge()
    coordinator = DurableExecutionCoordinator(
        cursor=cursor,
        release_registry=registry,
        activity_bridge=bridge,
    )
    request = _request(workflow)

    with pytest.raises(RuntimeError, match="parallel Module dispatch failed"):
        asyncio.run(coordinator.drive(request, max_dispatches=3))
    second = asyncio.run(coordinator.drive(request, max_dispatches=3))

    assert second.stop_reason is DurableExecutionStopReason.BLOCKED
    assert second.dispatch_count == 0
    assert second.last_outcome is not None
    assert second.last_outcome.disposition is ModuleOutcomeDisposition.WAIT
    assert second.snapshot.current_state == "review_parallel"
    assert bridge.call_count_by_node == {
        "fidelity_review": 1,
        "reader_gain_review": 1,
    }


def test_committed_parallel_wait_precedes_sibling_retry_scan_exhaustion() -> None:
    module = _module()
    workflow = _workflow(module)
    registry = RuntimeReleaseRegistry()
    _register_release_closure(registry, module, workflow)
    cursor = _Cursor(workflow)

    class _RetryAndWaitBridge(_Bridge):
        def __init__(self) -> None:
            super().__init__(fail_fidelity_once=True)

        def dispatch(self, request: ModuleDispatchRequest) -> ModuleOutcome:
            if request.current_state_id != "reader_gain_review":
                return super().dispatch(request)
            request.validate()
            with self._lock:
                self.call_count_by_node[request.current_state_id] = (
                    self.call_count_by_node.get(request.current_state_id, 0) + 1
                )
            outcome = ModuleOutcome.build(
                dispatch_id=request.dispatch_id,
                workflow_execution_id=request.workflow_execution_id,
                expected_state_id=request.current_state_id,
                disposition=ModuleOutcomeDisposition.WAIT,
                wait_policy_ref="wait-policy:external-review@1",
                outcome_ref=f"module-outcome:{request.dispatch_id}",
            )
            with self._lock:
                self.outcomes[request.dispatch_id] = outcome
            return outcome

    bridge = _RetryAndWaitBridge()
    coordinator = DurableExecutionCoordinator(
        cursor=cursor,
        release_registry=registry,
        activity_bridge=bridge,
    )
    request = _request(workflow)

    first = asyncio.run(coordinator.drive(request, max_dispatches=3))
    second = asyncio.run(
        coordinator.drive(
            request,
            max_dispatches=3,
            max_committed_retry_scan=1,
        )
    )

    assert first.stop_reason is DurableExecutionStopReason.BLOCKED
    assert second.stop_reason is DurableExecutionStopReason.BLOCKED
    assert second.dispatch_count == 0
    assert second.last_outcome is not None
    assert second.last_outcome.expected_state_id == "reader_gain_review"
    assert bridge.call_count_by_node == {
        "fidelity_review": 1,
        "reader_gain_review": 1,
    }


def test_parallel_retry_reuses_successful_sibling_and_advances_only_failed_branch() -> None:
    coordinator, request, _cursor, bridge = _coordinator(fail_fidelity_once=True)

    first = asyncio.run(coordinator.drive(request, max_dispatches=3))
    second = asyncio.run(coordinator.drive(request, max_dispatches=3))

    assert first.stop_reason is DurableExecutionStopReason.RETRYABLE_FAILURE
    assert first.snapshot.current_state == "review_parallel"
    assert second.stop_reason is DurableExecutionStopReason.TERMINAL
    assert bridge.call_count_by_node == {
        "fidelity_review": 2,
        "reader_gain_review": 1,
        "review_aggregate": 1,
    }
    fidelity_retries = sorted(
        outcome.dispatch_id
        for outcome in bridge.outcomes.values()
        if outcome.expected_state_id == "fidelity_review"
    )
    assert len(fidelity_retries) == 2
    assert fidelity_retries[0] != fidelity_retries[1]


def test_parallel_retry_history_scan_is_bounded() -> None:
    coordinator, request, _cursor, bridge = _coordinator(fail_fidelity_once=True)

    first = asyncio.run(
        coordinator.drive(
            request,
            max_dispatches=3,
            max_committed_retry_scan=1,
        )
    )

    assert first.stop_reason is DurableExecutionStopReason.RETRYABLE_FAILURE
    with pytest.raises(RuntimeError, match="retry scan exceeded safety bound"):
        asyncio.run(
            coordinator.drive(
                request,
                max_dispatches=3,
                max_committed_retry_scan=1,
            )
        )
    assert bridge.call_count_by_node == {
        "fidelity_review": 1,
        "reader_gain_review": 1,
    }


def test_parallel_recovery_reuses_branch_committed_before_sibling_crash() -> None:
    coordinator, request, _cursor, original_bridge = _coordinator()

    class _CrashOnceBridge(_Bridge):
        def __init__(self) -> None:
            super().__init__()
            self.crashed = False

        def dispatch(self, request: ModuleDispatchRequest) -> ModuleOutcome:
            if request.current_state_id == "reader_gain_review" and not self.crashed:
                self.crashed = True
                time.sleep(0.02)
                raise RuntimeError("simulated worker crash")
            return super().dispatch(request)

    bridge = _CrashOnceBridge()
    coordinator._activity_bridge = bridge  # noqa: SLF001 - recovery fixture swap

    with pytest.raises(RuntimeError, match="parallel Module dispatch failed"):
        asyncio.run(coordinator.drive(request, max_dispatches=3))

    recovered = asyncio.run(coordinator.drive(request, max_dispatches=3))

    assert recovered.stop_reason is DurableExecutionStopReason.TERMINAL
    assert bridge.call_count_by_node == {
        "fidelity_review": 1,
        "reader_gain_review": 1,
        "review_aggregate": 1,
    }
    assert original_bridge.call_count_by_node == {}


def test_parallel_join_acknowledgement_loss_does_not_repeat_branches() -> None:
    coordinator, request, _original_cursor, bridge = _coordinator()
    workflow = coordinator._release_registry.get_workflow(  # noqa: SLF001
        request.workflow_release_ref,
        request.workflow_release_sha256,
    )

    class _AckLossCursor(_Cursor):
        def __init__(self, release: WorkflowRelease) -> None:
            super().__init__(release)
            self.lost_acknowledgement = False

        async def apply_external_event(
            self, execution: BackendExecutionRef, event: ExternalEvent
        ) -> ExecutionSnapshot:
            snapshot = await super().apply_external_event(execution, event)
            if event.event_type == "all_completed" and not self.lost_acknowledgement:
                self.lost_acknowledgement = True
                raise RuntimeError("simulated join acknowledgement loss")
            return snapshot

    cursor = _AckLossCursor(workflow)
    coordinator._cursor = cursor  # noqa: SLF001 - acknowledgement-gap fixture

    with pytest.raises(RuntimeError, match="join acknowledgement loss"):
        asyncio.run(coordinator.drive(request, max_dispatches=3))

    recovered = asyncio.run(coordinator.drive(request, max_dispatches=3))

    assert recovered.stop_reason is DurableExecutionStopReason.TERMINAL
    assert bridge.call_count_by_node == {
        "fidelity_review": 1,
        "reader_gain_review": 1,
        "review_aggregate": 1,
    }
    assert [event.event_type for event in cursor.events] == [
        "all_completed",
        "module_outcome",
    ]
