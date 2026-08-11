"""Canonical Workflow Execution input, derived-output, and Outcome recording."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..contracts.ledger_record_definition import (
    CheckpointRecord,
    ExecutionInputRef,
    ExecutionOutputRef,
    OutcomeCommitBatch,
    RuntimeRecordBatch,
    WorkflowExecutionRecord,
    stable_runtime_id,
)
from ..contracts.registry_workflow_definition import (
    ExecutionOutputRegistrationRequest,
    ExecutionOutputRegistrationResult,
    ModuleOutcome,
    ModuleOutcomeDisposition,
)
from .ledger_execution_content_recording import (
    RuntimeExecutionContentStore,
    record_execution_content,
)
from .ledger_record_persistence import RuntimeExecutionRecordStore


class WorkflowExecutionArtifactHost(Protocol):
    """Cell-local deterministic-output surface required by the recorder."""

    def record_execution_output(
        self,
        request: ExecutionOutputRegistrationRequest,
        content: bytes,
    ) -> ExecutionOutputRegistrationResult:
        """Persist immutable bytes and return their Runtime-facing handle."""

    def read_bytes(self, content_ref: str, content_sha256: str) -> bytes:
        """Return exact bytes after verifying the declared hash."""


@dataclass(frozen=True)
class WorkflowExecutionLedgerBinding:
    """Execution-pinned formal store and Cell-local artifact boundary."""

    record_store: RuntimeExecutionRecordStore
    artifact_host: WorkflowExecutionArtifactHost
    content_store: RuntimeExecutionContentStore | None = None

    def validate(self) -> None:
        """Validate the exact persistence and artifact operations used here."""

        for method_name in (
            "commit",
            "commit_outcome",
            "get_committed_outcome",
            "load_trace",
        ):
            if not callable(getattr(self.record_store, method_name, None)):
                raise ValueError(
                    f"record_store must implement {method_name}"
                )
        if not callable(
            getattr(self.artifact_host, "record_execution_output", None)
        ):
            raise ValueError(
                "artifact_host must implement record_execution_output"
            )
        if self.content_store is not None:
            for method_name in ("stage_content", "commit_content"):
                if not callable(
                    getattr(self.content_store, method_name, None)
                ):
                    raise ValueError(
                        f"content_store must implement {method_name}"
                    )
            if not callable(getattr(self.artifact_host, "read_bytes", None)):
                raise ValueError("artifact_host must implement read_bytes")


class WorkflowExecutionLedgerRecorder:
    """Record workflow inputs, deterministic outputs, and dispatch outcomes."""

    def __init__(self, binding: WorkflowExecutionLedgerBinding) -> None:
        binding.validate()
        self._binding = binding

    @property
    def record_store(self) -> RuntimeExecutionRecordStore:
        """Return the execution-pinned canonical record store."""

        return self._binding.record_store

    def record_execution_start(
        self,
        *,
        execution: WorkflowExecutionRecord,
        inputs: tuple[ExecutionInputRef, ...],
    ) -> None:
        """Atomically commit one Workflow Execution and frozen input package."""

        execution.validate()
        for row in inputs:
            row.validate()
            if row.workflow_execution_id != execution.workflow_execution_id:
                raise ValueError("execution input crossed Workflow Execution")
        if {row.input_ref for row in inputs} != set(
            execution.execution_input_package_refs
        ):
            raise ValueError(
                "execution inputs differ from the frozen package membership"
            )
        self.record_store.commit(
            RuntimeRecordBatch(
                workflow_execution_id=execution.workflow_execution_id,
                transaction_id=stable_runtime_id(
                    "transaction",
                    execution.workflow_execution_id,
                    "execution_start",
                ),
                records=(execution, *inputs),
            )
        )
        if self._binding.content_store is not None:
            for row in inputs:
                record_execution_content(
                    content_store=self._binding.content_store,
                    content_reader=self._binding.artifact_host,
                    workflow_execution_id=execution.workflow_execution_id,
                    content_ref=row.input_ref,
                    content_sha256=row.input_sha256,
                    media_type=row.media_type,
                    recorded_at_utc=row.recorded_at_utc,
                    reference_is_committed=True,
                )

    def record_execution_output(
        self,
        *,
        workflow_execution_id: str,
        request: ExecutionOutputRegistrationRequest,
        content: bytes,
        recorded_at_utc: str,
    ) -> ExecutionOutputRegistrationResult:
        """Stage one derived artifact and append its formal Runtime handle."""

        request.validate()
        if type(content) is not bytes:
            raise ValueError("execution output content must be bytes")
        result = self._binding.artifact_host.record_execution_output(
            request,
            content,
        )
        result.validate()
        if result.output_resolution_ref is not None:
            raise ValueError(
                "deterministic execution output cannot invent resolution authority"
            )
        row = ExecutionOutputRef(
            execution_output_id=result.execution_output_id,
            workflow_execution_id=workflow_execution_id,
            output_type_id=request.output_type_id,
            schema_version=request.schema_version,
            output_ref=result.execution_output_ref,
            output_sha256=result.execution_output_sha256,
            byte_size=len(content),
            media_type=request.media_type,
            recorded_at_utc=recorded_at_utc,
            module_run_id=request.module_run_id,
            variant_id=request.variant_id,
            attempt_id=request.attempt_id,
            logical_name=request.logical_name,
            source_artifact_refs=request.source_artifact_refs,
        )
        row.validate()
        if self._binding.content_store is not None:
            record_execution_content(
                content_store=self._binding.content_store,
                content_reader=self._binding.artifact_host,
                workflow_execution_id=workflow_execution_id,
                content_ref=row.output_ref,
                content_sha256=row.output_sha256,
                media_type=row.media_type,
                recorded_at_utc=row.recorded_at_utc,
                reference_is_committed=False,
            )
        prior = _one_output(
            self.record_store.load_trace(workflow_execution_id),
            row.execution_output_id,
        )
        if prior is not None:
            _require_same_without_time(prior, row)
            return result
        self.record_store.commit(
            RuntimeRecordBatch(
                workflow_execution_id=workflow_execution_id,
                transaction_id=stable_runtime_id(
                    "transaction",
                    workflow_execution_id,
                    row.execution_output_id,
                ),
                records=(row,),
            )
        )
        return result

    def commit_outcome(
        self,
        *,
        execution: WorkflowExecutionRecord,
        outcome: ModuleOutcome,
        recorded_at_utc: str,
    ) -> ModuleOutcome:
        """Atomically append one Domain Outcome and local recovery checkpoint."""

        execution.validate()
        outcome.validate()
        if outcome.workflow_execution_id != execution.workflow_execution_id:
            raise ValueError("Module Outcome crossed Workflow Execution")
        prior = self.record_store.get_committed_outcome(
            execution.workflow_execution_id,
            outcome.dispatch_id,
        )
        if prior is not None:
            if prior != outcome:
                raise ValueError("Module Outcome identity conflict")
            return prior
        current_state_id = (
            outcome.target_state_id
            if outcome.disposition is ModuleOutcomeDisposition.TRANSITION
            else outcome.expected_state_id
        )
        assert current_state_id is not None
        checkpoint = CheckpointRecord(
            checkpoint_id=stable_runtime_id(
                "checkpoint",
                execution.workflow_execution_id,
                outcome.dispatch_id,
            ),
            workflow_execution_id=execution.workflow_execution_id,
            dispatch_id=outcome.dispatch_id,
            execution_release_ref=execution.execution_release_ref,
            graph_sha256=execution.graph_sha256,
            entitlement_snapshot_hash=execution.entitlement_snapshot_hash,
            current_state_id=current_state_id,
            runtime_status_id=f"{outcome.disposition.value}_committed",
            committed_outcome_ref=outcome.outcome_ref,
            committed_outcome_sha256=outcome.outcome_sha256,
            recorded_at_utc=recorded_at_utc,
        )
        self.record_store.commit_outcome(
            OutcomeCommitBatch(
                workflow_execution_id=execution.workflow_execution_id,
                transaction_id=stable_runtime_id(
                    "transaction",
                    execution.workflow_execution_id,
                    outcome.dispatch_id,
                    "outcome",
                ),
                outcome=outcome,
                checkpoint=checkpoint,
            )
        )
        return outcome


def _one_output(trace, execution_output_id: str) -> ExecutionOutputRef | None:
    matches = tuple(
        row
        for row in trace.records_of_type(ExecutionOutputRef)
        if row.execution_output_id == execution_output_id
    )
    if len(matches) > 1:
        raise ValueError(
            f"duplicate Runtime execution output: {execution_output_id}"
        )
    return matches[0] if matches else None


def _require_same_without_time(
    left: ExecutionOutputRef,
    right: ExecutionOutputRef,
) -> None:
    left_payload = left.as_dict()
    right_payload = right.as_dict()
    left_payload.pop("recorded_at_utc")
    right_payload.pop("recorded_at_utc")
    if left_payload != right_payload:
        raise ValueError("execution output replay changed committed identity")


__all__ = [
    "WorkflowExecutionArtifactHost",
    "WorkflowExecutionLedgerBinding",
    "WorkflowExecutionLedgerRecorder",
]
