from __future__ import annotations

from agent_runtime.contracts.ledger_record_definition import (
    CheckpointRecord,
    ExecutionInputRef,
    ExecutionOutputRef,
    WorkflowExecutionRecord,
)
from agent_runtime.contracts.registry_workflow_definition import (
    ExecutionOutputRegistrationRequest,
    ModuleOutcome,
    ModuleOutcomeDisposition,
)
from agent_runtime.execution.execution_content_staging import (
    InMemoryCellArtifactStore,
)
from agent_runtime.ledger.ledger_record_persistence import (
    InMemoryRuntimeExecutionRecordStore,
)
from agent_runtime.ledger.ledger_workflow_execution_recording import (
    WorkflowExecutionLedgerBinding,
    WorkflowExecutionLedgerRecorder,
)


_TIME = "2026-08-11T12:00:00Z"
_EXECUTION_ID = "execution_workflow_artifact_test"


class _RecordingContentStore:
    def __init__(self) -> None:
        self.staged = []
        self.committed = []

    def stage_content(self, content):
        content.validate()
        self.staged.append(content)
        return content

    def commit_content(self, content):
        content.validate()
        self.committed.append(content)
        return content


def _execution(input_ref: str, input_sha256: str) -> WorkflowExecutionRecord:
    return WorkflowExecutionRecord(
        workflow_execution_id=_EXECUTION_ID,
        workflow_id="workflow_artifact_test",
        workflow_contract_version="v1",
        tenant_id="tenant_test",
        cell_id="cell_test",
        principal_id="principal_test",
        execution_release_ref="execution-release:artifact-test@v1",
        graph_sha256="a" * 64,
        runtime_execution_binding_ref="runtime-binding:artifact-test@v1",
        runtime_execution_binding_sha256="b" * 64,
        authorization_decision_ref="authorization-decision:artifact-test@v1",
        authorization_decision_sha256="c" * 64,
        execution_principal_delegation_ref="delegation:artifact-test@v1",
        execution_principal_delegation_sha256="d" * 64,
        entitlement_snapshot_ref="entitlement:artifact-test@v1",
        entitlement_snapshot_hash="e" * 64,
        execution_input_package_refs=(input_ref,),
        execution_input_package_sha256=input_sha256,
        recorded_at_utc=_TIME,
    )


def test_execution_recorder_commits_inputs_derived_outputs_and_outcomes() -> None:
    artifact_host = InMemoryCellArtifactStore()
    content_store = _RecordingContentStore()
    input_content = b'{"source":"canonical"}'
    staged_input = artifact_host.put_bytes(
        artifact_kind_id="canonical_source",
        schema_version="v1",
        schema_ref="schema:canonical_source@v1",
        schema_sha256="1" * 64,
        media_type="application/json",
        content=input_content,
        idempotency_key="workflow_artifact_input",
        logical_name="source",
    )
    execution = _execution(
        staged_input.artifact_ref,
        staged_input.artifact_sha256,
    )
    input_row = ExecutionInputRef(
        execution_input_id="execution_input_artifact_test",
        workflow_execution_id=_EXECUTION_ID,
        input_type_id="canonical_source",
        schema_version="v1",
        input_ref=staged_input.artifact_ref,
        input_sha256=staged_input.artifact_sha256,
        byte_size=len(input_content),
        media_type="application/json",
        recorded_at_utc=_TIME,
        logical_name="source",
    )
    store = InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=lambda row: (
            artifact_host.read_bytes(row.output_ref, row.output_sha256)
            is not None
        )
    )
    recorder = WorkflowExecutionLedgerRecorder(
        WorkflowExecutionLedgerBinding(
            record_store=store,
            artifact_host=artifact_host,
            content_store=content_store,
        )
    )
    recorder.record_execution_start(
        execution=execution,
        inputs=(input_row,),
    )

    output_request = ExecutionOutputRegistrationRequest(
        output_type_id="routing_decision",
        schema_version="v1",
        media_type="application/json",
        idempotency_key="routing_decision_artifact_test",
        logical_name="routing_decision",
        source_artifact_refs=(staged_input.artifact_ref,),
    )
    first = recorder.record_execution_output(
        workflow_execution_id=_EXECUTION_ID,
        request=output_request,
        content=b'{"route":"optical_devices"}',
        recorded_at_utc=_TIME,
    )
    replay = recorder.record_execution_output(
        workflow_execution_id=_EXECUTION_ID,
        request=output_request,
        content=b'{"route":"optical_devices"}',
        recorded_at_utc="2026-08-11T12:01:00Z",
    )
    assert replay == first

    outcome = ModuleOutcome.build(
        dispatch_id="dispatch_artifact_test",
        workflow_execution_id=_EXECUTION_ID,
        expected_state_id="routing",
        disposition=ModuleOutcomeDisposition.TRANSITION,
        target_state_id="production",
        evidence_artifact_refs=(first.execution_output_ref,),
        outcome_ref="module-outcome:artifact-test",
    )
    assert recorder.commit_outcome(
        execution=execution,
        outcome=outcome,
        recorded_at_utc="2026-08-11T12:02:00Z",
    ) == outcome
    assert recorder.commit_outcome(
        execution=execution,
        outcome=outcome,
        recorded_at_utc="2026-08-11T12:03:00Z",
    ) == outcome

    trace = store.load_trace(_EXECUTION_ID)
    outputs = trace.records_of_type(ExecutionOutputRef)
    checkpoints = trace.records_of_type(CheckpointRecord)
    assert len(outputs) == 1
    assert outputs[0].source_artifact_refs == (staged_input.artifact_ref,)
    assert len(checkpoints) == 1
    assert checkpoints[0].current_state_id == "production"
    assert checkpoints[0].runtime_status_id == "transition_committed"
    assert [row.content_ref for row in content_store.committed] == [
        staged_input.artifact_ref
    ]
    assert [row.content_ref for row in content_store.staged] == [
        first.execution_output_ref,
        first.execution_output_ref,
    ]
