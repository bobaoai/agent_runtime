"""Project one immutable Runtime ledger trace into the Inspector read model.

The projection is deliberately mechanical.  It does not persist a second
ledger, interpret domain output, or make an authorization decision.  Callers
must authorize the execution and any content dereference before using it.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable

from ..contracts.ledger_lineage_definition import ModuleOutputResolutionRecord
from ..contracts.ledger_record_definition import (
    CheckpointRecord,
    ContextEvent,
    EvaluationResult,
    EvaluationRun,
    ExecutionInputRef,
    ExecutionOutputRef,
    ModelCallRecord,
    RuntimeExecutionTrace,
    Selection,
    ToolCallRecord,
    UsageEvent,
    WorkflowAttemptRecord,
    WorkflowExecutionRecord,
    WorkflowModuleExecutionVariantRecord,
    WorkflowModuleRunRecord,
    runtime_record_as_dict,
)
from ..contracts.registry_release_definition import WorkflowRelease
from ..contracts.registry_workflow_definition import ModuleOutcome


def _one(rows: tuple[Any, ...], label: str) -> Any:
    if len(rows) != 1:
        raise ValueError(f"Runtime inspection requires exactly one {label}")
    return rows[0]


def _sum_optional_int(rows: Iterable[UsageEvent], field: str) -> int | None:
    values = [getattr(row, field) for row in rows]
    if not values or any(value is None for value in values):
        return None
    return sum(int(value) for value in values)


def _sum_optional_usd(rows: Iterable[UsageEvent], field: str) -> str | None:
    values = [getattr(row, field) for row in rows]
    if not values or any(value is None for value in values):
        return None
    return format(sum(Decimal(str(value)) for value in values), "f")


def _usage_view(rows: tuple[UsageEvent, ...]) -> dict[str, int | str | None]:
    return {
        "input_tokens": _sum_optional_int(rows, "input_tokens"),
        "output_tokens": _sum_optional_int(rows, "output_tokens"),
        "cache_read_tokens": _sum_optional_int(rows, "cache_read_tokens"),
        "cache_creation_tokens": _sum_optional_int(
            rows, "cache_creation_tokens"
        ),
        "estimated_cost_usd": _sum_optional_usd(rows, "estimated_cost_usd"),
        "provider_charge_usd": _sum_optional_usd(rows, "provider_charge_usd"),
    }


def _record_position(trace: RuntimeExecutionTrace) -> dict[int, int]:
    return {id(record): index for index, record in enumerate(trace.records, start=1)}


def _module_status(
    module_run_id: str,
    attempts: tuple[WorkflowAttemptRecord, ...],
    outcomes: tuple[ModuleOutcome, ...],
) -> str:
    matching_outcomes = tuple(
        row for row in outcomes if row.module_run_id == module_run_id
    )
    if matching_outcomes:
        disposition = str(matching_outcomes[-1].disposition)
        return {
            "transition": "completed",
            "wait": "waiting",
            "retryable_failure": "failed",
        }.get(disposition, disposition)
    matching_attempts = tuple(
        row for row in attempts if row.module_run_id == module_run_id
    )
    if not matching_attempts:
        return "registered"
    if any(row.status == "failed" for row in matching_attempts):
        return "failed"
    if any(row.status == "cancelled" for row in matching_attempts):
        return "cancelled"
    if all(row.status == "completed" for row in matching_attempts):
        return "completed"
    return "running"


def _workflow_status(
    module_runs: tuple[WorkflowModuleRunRecord, ...],
    attempts: tuple[WorkflowAttemptRecord, ...],
    outcomes: tuple[ModuleOutcome, ...],
    checkpoints: tuple[CheckpointRecord, ...],
) -> str:
    if checkpoints:
        return str(checkpoints[-1].runtime_status_id)
    if not module_runs:
        return "admitted"
    statuses = {
        _module_status(row.module_run_id, attempts, outcomes)
        for row in module_runs
    }
    if "failed" in statuses:
        return "failed"
    if "cancelled" in statuses:
        return "cancelled"
    if "waiting" in statuses:
        return "waiting"
    return "running"


def _workflow_node_release_refs(
    workflow_release: WorkflowRelease | None,
) -> dict[str, str]:
    if workflow_release is None:
        return {}
    workflow_release.validate()
    return {
        node.node_id: str(node.module_release_ref)
        for node in workflow_release.nodes
        if node.module_release_ref is not None
    }


def build_runtime_execution_inspection(
    trace: RuntimeExecutionTrace,
    *,
    workflow_release: WorkflowRelease | None = None,
) -> dict[str, Any]:
    """Build the canonical denormalized view used by live and offline Inspector.

    The input trace remains the only execution authority.  Ledger positions in
    this read model are the one-based append order of records in ``trace``.
    """

    if not isinstance(trace, RuntimeExecutionTrace):
        raise TypeError("trace must be a RuntimeExecutionTrace")
    executions = trace.records_of_type(WorkflowExecutionRecord)
    execution = _one(executions, "WorkflowExecutionRecord")
    if execution.workflow_execution_id != trace.workflow_execution_id:
        raise ValueError("Runtime trace and execution identity disagree")

    for record in trace.records:
        workflow_execution_id = getattr(record, "workflow_execution_id", None)
        if (
            workflow_execution_id is not None
            and workflow_execution_id != trace.workflow_execution_id
        ):
            raise ValueError("Runtime inspection rejects cross-execution records")
        runtime_record_as_dict(record)

    positions = _record_position(trace)
    module_runs = trace.records_of_type(WorkflowModuleRunRecord)
    variants = trace.records_of_type(WorkflowModuleExecutionVariantRecord)
    attempts = trace.records_of_type(WorkflowAttemptRecord)
    inputs = trace.records_of_type(ExecutionInputRef)
    outputs = trace.records_of_type(ExecutionOutputRef)
    usage_events = trace.records_of_type(UsageEvent)
    model_calls = trace.records_of_type(ModelCallRecord)
    tool_calls = trace.records_of_type(ToolCallRecord)
    evaluation_runs = trace.records_of_type(EvaluationRun)
    evaluation_results = trace.records_of_type(EvaluationResult)
    selections = trace.records_of_type(Selection)
    resolutions = trace.records_of_type(ModuleOutputResolutionRecord)
    context_events = trace.records_of_type(ContextEvent)
    outcomes = trace.records_of_type(ModuleOutcome)
    checkpoints = trace.records_of_type(CheckpointRecord)

    module_ids = {row.module_run_id for row in module_runs}
    if len(module_ids) != len(module_runs):
        raise ValueError("Runtime inspection requires unique module_run_id values")
    variant_ids = {row.variant_id for row in variants}
    if len(variant_ids) != len(variants):
        raise ValueError("Runtime inspection requires unique variant_id values")
    attempt_ids = {row.attempt_id for row in attempts}
    if len(attempt_ids) != len(attempts):
        raise ValueError("Runtime inspection requires unique attempt_id values")
    if any(row.module_run_id not in module_ids for row in variants):
        raise ValueError("Runtime inspection found an orphan Variant")
    if any(
        row.module_run_id not in module_ids or row.variant_id not in variant_ids
        for row in attempts
    ):
        raise ValueError("Runtime inspection found an orphan Attempt")

    usage_by_attempt: dict[str, list[UsageEvent]] = defaultdict(list)
    for row in usage_events:
        usage_by_attempt[row.attempt_id].append(row)
    calls_by_attempt: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in (*model_calls, *tool_calls):
        calls_by_attempt[row.attempt_id].append(
            {
                **row.as_dict(),
                "call_kind": "model" if isinstance(row, ModelCallRecord) else "tool",
                "source_ledger_position": positions[id(row)],
            }
        )
    for rows in calls_by_attempt.values():
        rows.sort(key=lambda row: int(row["source_ledger_position"]))

    release_ref_by_node = _workflow_node_release_refs(workflow_release)
    node_occurrences: dict[str, int] = defaultdict(int)
    modules: list[dict[str, Any]] = []
    for module in module_runs:
        node_occurrences[module.state_id] += 1
        module_variants = tuple(
            row for row in variants if row.module_run_id == module.module_run_id
        )
        module_variant_ids = {row.variant_id for row in module_variants}
        module_attempts = tuple(
            row for row in attempts if row.variant_id in module_variant_ids
        )
        module_attempt_ids = {row.attempt_id for row in module_attempts}
        module_outputs = tuple(
            row for row in outputs if row.module_run_id == module.module_run_id
        )
        module_input_refs = set(module.input_refs)
        module_inputs = tuple(
            row for row in inputs if row.input_ref in module_input_refs
        )
        module_release_ref = release_ref_by_node.get(
            module.state_id,
        )
        variant_views = []
        for variant in module_variants:
            variant_views.append(
                {
                    **variant.as_dict(),
                    "execution_profile_ref": (
                        f"execution-profile:{variant.execution_profile_id}"
                    ),
                    "execution_profile_sha256": variant.execution_profile_sha256,
                    "prompt_envelope_ref": None,
                    "prompt_envelope_sha256": variant.prompt_sha256,
                    "execution_profile": {
                        "provider_id": None,
                        "agent_execution_adapter_id": (
                            variant.agent_execution_adapter_id
                        ),
                        "model_id": variant.model_id,
                        "reasoning_profile": variant.reasoning_profile,
                        "context_mode": variant.context_mode,
                        "tool_policy": list(variant.tool_policy),
                        "timeout_seconds": variant.timeout_seconds,
                        "max_attempts": variant.max_attempts,
                    },
                    "source_ledger_position": positions[id(variant)],
                }
            )
        attempt_views = []
        for attempt in module_attempts:
            attempt_usage = tuple(usage_by_attempt.get(attempt.attempt_id, ()))
            attempt_views.append(
                {
                    **attempt.as_dict(),
                    **_usage_view(attempt_usage),
                    "tool_calls": calls_by_attempt.get(attempt.attempt_id, []),
                    "source_ledger_position": positions[id(attempt)],
                }
            )
        artifact_views = [
            {
                "artifact_ref": row.input_ref,
                "artifact_sha256": row.input_sha256,
                "logical_name": row.logical_name or row.input_type_id,
                "media_type": row.media_type,
                "direction": "input",
                "module_run_id": module.module_run_id,
                "source_ledger_position": positions[id(row)],
            }
            for row in module_inputs
        ]
        artifact_views.extend(
            {
                "artifact_ref": row.output_ref,
                "artifact_sha256": row.output_sha256,
                "logical_name": row.logical_name or row.output_type_id,
                "media_type": row.media_type,
                "direction": "output",
                "module_run_id": module.module_run_id,
                "variant_id": row.variant_id,
                "attempt_id": row.attempt_id,
                "source_ledger_position": positions[id(row)],
            }
            for row in module_outputs
        )
        modules.append(
            {
                "module_run": {
                    **module.as_dict(),
                    "workflow_node_id": module.state_id,
                    "module_release_ref": module_release_ref,
                    "status": _module_status(
                        module.module_run_id, attempts, outcomes
                    ),
                    "source_ledger_position": positions[id(module)],
                },
                "node_occurrence_index": node_occurrences[module.state_id],
                "module_release": {
                    "module_id": module.module_id,
                    "release_ref": module_release_ref,
                },
                "variants": variant_views,
                "attempts": attempt_views,
                "artifacts": artifact_views,
                "evaluations": [
                    row.as_dict()
                    for row in evaluation_runs
                    if row.source_module_run_id == module.module_run_id
                ]
                + [
                    row.as_dict()
                    for row in evaluation_results
                    if any(
                        run.evaluation_run_id == row.evaluation_run_id
                        and run.source_module_run_id == module.module_run_id
                        for run in evaluation_runs
                    )
                ],
                "selections": [
                    row.as_dict()
                    for row in selections
                    if row.source_module_run_id == module.module_run_id
                ],
                "resolutions": [
                    row.as_dict()
                    for row in resolutions
                    if row.source_module_run_id == module.module_run_id
                ],
                "context_events": [
                    row.as_dict()
                    for row in context_events
                    if row.module_run_id == module.module_run_id
                    and (
                        row.attempt_id is None
                        or row.attempt_id in module_attempt_ids
                    )
                ],
            }
        )

    workflow_release_view = (
        None if workflow_release is None else workflow_release.as_dict()
    )
    return {
        "trace": {
            "workflow": {
                **execution.as_dict(),
                "status": _workflow_status(
                    module_runs, attempts, outcomes, checkpoints
                ),
                "source_ledger_position": positions[id(execution)],
            },
            "usage": _usage_view(usage_events),
            "record_count": len(trace.records),
            "commit_receipt_count": len(trace.commit_receipts),
        },
        "workflow_release": workflow_release_view,
        "modules": modules,
        "projection_boundary": {
            "authority": "agent_runtime_execution_ledger",
            "projection_kind": "rebuildable_read_model",
            "content_included": False,
            "authorization_decision_made": False,
        },
        "records": [runtime_record_as_dict(row) for row in trace.records],
    }


__all__ = ["build_runtime_execution_inspection"]
