"""Project one immutable Runtime ledger trace into the Inspector read model.

The projection is deliberately mechanical.  It does not persist a second
ledger, interpret domain output, or make an authorization decision.  Callers
must authorize the execution and any content dereference before using it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable

from ..contracts.ledger_lineage_definition import ModuleOutputResolutionRecord
from ..contracts.ledger_record_definition import (
    AttemptOrphanedRecord,
    CheckpointRecord,
    ContextEvent,
    EvaluationResult,
    EvaluationRun,
    ExecutionInputRef,
    ExecutionOutputRef,
    LegacyAuthorizationLedgerRecord,
    ModelCallRecord,
    RuntimeExecutionTrace,
    Selection,
    ToolCallRecord,
    UsageEvent,
    WorkflowAttemptRecord,
    WorkflowAttemptStartedRecord,
    WorkflowExecutionRecord,
    WorkflowModuleExecutionVariantRecord,
    WorkflowModuleRunRecord,
    legacy_authorization_record_as_dict,
    runtime_record_as_dict,
)
from ..foundation.foundation_contract_validation import (
    format_utc_timestamp,
    parse_utc_timestamp,
)
from ..contracts.registry_release_definition import WorkflowRelease
from ..contracts.registry_workflow_definition import ModuleOutcome


def _one(rows: tuple[Any, ...], label: str) -> Any:
    if len(rows) != 1:
        raise ValueError(f"Runtime inspection requires exactly one {label}")
    return rows[0]


def _inspection_record_as_dict(record: Any) -> dict[str, Any]:
    """Serialize canonical records and explicitly labelled legacy facts."""

    if isinstance(record, LegacyAuthorizationLedgerRecord):
        return legacy_authorization_record_as_dict(record)
    return runtime_record_as_dict(record)


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
    attempt_start_views: list[dict[str, Any]],
    module_attempts: tuple[WorkflowAttemptRecord, ...],
    module_outcomes: tuple[ModuleOutcome, ...],
) -> str:
    if module_outcomes:
        disposition = str(module_outcomes[-1].disposition)
        return {
            "transition": "completed",
            "wait": "waiting",
            "retryable_failure": "failed",
        }.get(disposition, disposition)
    # Attempt chains are per Variant: one Variant's expired lease must never
    # be masked by another Variant's active start, and a dangling start on
    # any Variant keeps the Module unresolved (fan-out semantics) — only a
    # committed Outcome resolves the Module across Variants.
    in_flight = [
        view for view in attempt_start_views if view["terminal_status"] is None
    ]
    if any(view["lease_state"] == "expired" for view in in_flight):
        return "recovery_required"
    if in_flight:
        return "running"
    if not module_attempts:
        return "registered"
    # Without a committed Outcome, the latest committed Attempt is the current
    # state: a retry that succeeds supersedes its failed predecessors instead
    # of leaving the Module marked failed forever.
    last = module_attempts[-1]
    if last.status in {"completed", "failed", "cancelled"}:
        return last.status
    return "running"


def _workflow_status(
    module_statuses: tuple[str, ...],
    checkpoints: tuple[CheckpointRecord, ...],
) -> str:
    if checkpoints:
        return str(checkpoints[-1].runtime_status_id)
    if not module_statuses:
        return "admitted"
    if "recovery_required" in module_statuses:
        return "recovery_required"
    if "failed" in module_statuses:
        return "failed"
    if "cancelled" in module_statuses:
        return "cancelled"
    if "waiting" in module_statuses:
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
    observed_at_utc: str | None = None,
) -> dict[str, Any]:
    """Build the canonical denormalized view used by live and offline Inspector.

    The input trace remains the only execution authority.  Ledger positions in
    this read model are the one-based append order of records in ``trace``.
    """

    if not isinstance(trace, RuntimeExecutionTrace):
        raise TypeError("trace must be a RuntimeExecutionTrace")
    observed_at = (
        None
        if observed_at_utc is None
        else parse_utc_timestamp("observed_at_utc", observed_at_utc)
    )
    executions = trace.records_of_type(WorkflowExecutionRecord)
    execution = _one(executions, "WorkflowExecutionRecord")
    if execution.workflow_execution_id != trace.workflow_execution_id:
        raise ValueError("Runtime trace and execution identity disagree")
    if workflow_release is not None and (
        workflow_release.workflow_id != execution.workflow_id
        or workflow_release.workflow_contract_version
        != execution.workflow_contract_version
    ):
        raise ValueError(
            "Runtime inspection workflow release does not match the execution"
        )

    for record in trace.records:
        workflow_execution_id = getattr(record, "workflow_execution_id", None)
        if (
            workflow_execution_id is not None
            and workflow_execution_id != trace.workflow_execution_id
        ):
            raise ValueError("Runtime inspection rejects cross-execution records")
        _inspection_record_as_dict(record)

    positions = _record_position(trace)
    module_runs = trace.records_of_type(WorkflowModuleRunRecord)
    variants = trace.records_of_type(WorkflowModuleExecutionVariantRecord)
    attempt_starts = trace.records_of_type(WorkflowAttemptStartedRecord)
    attempts = trace.records_of_type(WorkflowAttemptRecord)
    orphaned_attempts = trace.records_of_type(AttemptOrphanedRecord)
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
    variant_by_id = {row.variant_id: row for row in variants}
    if len(variant_by_id) != len(variants):
        raise ValueError("Runtime inspection requires unique variant_id values")
    attempt_by_id = {row.attempt_id: row for row in attempts}
    if len(attempt_by_id) != len(attempts):
        raise ValueError("Runtime inspection requires unique attempt_id values")
    attempt_start_by_id = {row.attempt_id: row for row in attempt_starts}
    if len(attempt_start_by_id) != len(attempt_starts):
        raise ValueError("Runtime inspection requires unique Attempt starts")
    orphaned_by_attempt_id = {row.attempt_id: row for row in orphaned_attempts}
    if len(orphaned_by_attempt_id) != len(orphaned_attempts):
        raise ValueError("Runtime inspection requires one orphan disposition per Attempt")

    # Existence alone is not lineage: every child record must sit inside the
    # exact chain of the parents it names, or a mismatched record would be
    # displayed under the wrong Module as if the ledger had committed it there.
    if any(row.module_run_id not in module_ids for row in variants):
        raise ValueError("Runtime inspection found an orphan Variant")
    for row in attempt_starts:
        start_variant = variant_by_id.get(row.variant_id)
        if (
            start_variant is None
            or start_variant.module_run_id != row.module_run_id
        ):
            raise ValueError(
                "Runtime inspection found an Attempt start outside its Variant lineage"
            )
    for row in attempts:
        attempt_variant = variant_by_id.get(row.variant_id)
        if (
            attempt_variant is None
            or attempt_variant.module_run_id != row.module_run_id
        ):
            raise ValueError(
                "Runtime inspection found an Attempt outside its Variant lineage"
            )
        start = attempt_start_by_id.get(row.attempt_id)
        if start is not None and (
            start.module_run_id != row.module_run_id
            or start.variant_id != row.variant_id
            or start.attempt_ordinal != row.attempt_ordinal
        ):
            raise ValueError(
                "Runtime inspection found a terminal Attempt outside its start lineage"
            )
    for row in orphaned_attempts:
        start = attempt_start_by_id.get(row.attempt_id)
        # The attempts loop above already proved every terminal matches its
        # start, so the orphan row only needs its start and a terminal to
        # exist and to match the start's lineage.
        if (
            start is None
            or attempt_by_id.get(row.attempt_id) is None
            or start.module_run_id != row.module_run_id
            or start.variant_id != row.variant_id
        ):
            raise ValueError(
                "Runtime inspection found an orphan disposition outside its Attempt lineage"
            )
    operation_ids = {row.model_call_id for row in model_calls} | {
        row.tool_call_id for row in tool_calls
    }
    for row in (*model_calls, *tool_calls, *usage_events):
        operation_attempt = attempt_by_id.get(row.attempt_id)
        if (
            operation_attempt is None
            or operation_attempt.variant_id != row.variant_id
            or operation_attempt.module_run_id != row.module_run_id
        ):
            raise ValueError(
                "Runtime inspection found an operation record outside its "
                "Attempt lineage"
            )
    if any(row.operation_id not in operation_ids for row in usage_events):
        raise ValueError(
            "Runtime inspection found a UsageEvent without its call record"
        )
    for row in outputs:
        if row.attempt_id is not None:
            output_attempt = attempt_by_id.get(row.attempt_id)
            if (
                output_attempt is None
                or (
                    row.variant_id is not None
                    and output_attempt.variant_id != row.variant_id
                )
                or (
                    row.module_run_id is not None
                    and output_attempt.module_run_id != row.module_run_id
                )
            ):
                raise ValueError(
                    "Runtime inspection found an output outside its Attempt "
                    "lineage"
                )
        elif row.module_run_id is not None and row.module_run_id not in module_ids:
            raise ValueError("Runtime inspection found an orphan output")
    for row in context_events:
        if row.module_run_id not in module_ids:
            raise ValueError("Runtime inspection found an orphan context event")
        if row.attempt_id is not None:
            context_attempt = attempt_by_id.get(row.attempt_id)
            if (
                context_attempt is None
                or context_attempt.module_run_id != row.module_run_id
            ):
                raise ValueError(
                    "Runtime inspection found a context event outside its "
                    "Attempt lineage"
                )
    if any(
        row.module_run_id is not None and row.module_run_id not in module_ids
        for row in outcomes
    ):
        raise ValueError("Runtime inspection found an orphan ModuleOutcome")
    evaluation_run_ids = {row.evaluation_run_id for row in evaluation_runs}
    if any(row.source_module_run_id not in module_ids for row in evaluation_runs):
        raise ValueError("Runtime inspection found an orphan EvaluationRun")
    if any(
        row.evaluation_run_id not in evaluation_run_ids
        for row in evaluation_results
    ):
        raise ValueError("Runtime inspection found an orphan EvaluationResult")
    if any(row.source_module_run_id not in module_ids for row in selections):
        raise ValueError("Runtime inspection found an orphan Selection")
    if any(row.source_module_run_id not in module_ids for row in resolutions):
        raise ValueError(
            "Runtime inspection found an orphan output resolution"
        )

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
    # Records are grouped by Module once; the validated lineage closure above
    # guarantees the module_run_id groupings agree with Variant membership.
    variants_by_module: dict[str, list] = defaultdict(list)
    for row in variants:
        variants_by_module[row.module_run_id].append(row)
    attempts_by_module: dict[str, list] = defaultdict(list)
    for row in attempts:
        attempts_by_module[row.module_run_id].append(row)
    starts_by_module: dict[str, list] = defaultdict(list)
    for row in attempt_starts:
        starts_by_module[row.module_run_id].append(row)
    outcomes_by_module: dict[str, list] = defaultdict(list)
    for row in outcomes:
        outcomes_by_module[row.module_run_id].append(row)
    node_occurrences: dict[str, int] = defaultdict(int)
    modules: list[dict[str, Any]] = []
    module_statuses: list[str] = []
    modules_requiring_recovery: list[str] = []
    for module in module_runs:
        node_occurrences[module.state_id] += 1
        module_variants = tuple(variants_by_module.get(module.module_run_id, ()))
        module_attempts = tuple(attempts_by_module.get(module.module_run_id, ()))
        module_attempt_starts = tuple(
            starts_by_module.get(module.module_run_id, ())
        )
        module_outcomes = tuple(outcomes_by_module.get(module.module_run_id, ()))
        module_attempt_ids = {row.attempt_id for row in module_attempts}
        module_outputs = tuple(
            row for row in outputs if row.module_run_id == module.module_run_id
        )
        module_input_refs = set(module.input_refs)
        module_inputs = tuple(
            row for row in inputs if row.input_ref in module_input_refs
        )
        module_derived_inputs = tuple(
            row for row in outputs if row.output_ref in module_input_refs
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
                    "prompt_envelope_ref": variant.prompt_envelope_ref,
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
        attempt_start_views = []
        for start in module_attempt_starts:
            terminal = attempt_by_id.get(start.attempt_id)
            orphaned = orphaned_by_attempt_id.get(start.attempt_id)
            deadline = start.deadline_at()
            if terminal is not None:
                lease_state = "terminalized"
            elif observed_at is None:
                lease_state = "unresolved"
            elif start.lease_expired_at(observed_at):
                lease_state = "expired"
            else:
                lease_state = "active"
            attempt_start_views.append(
                {
                    **start.as_dict(),
                    "deadline_at_utc": format_utc_timestamp(deadline),
                    "lease_state": lease_state,
                    "terminal_status": (
                        None if terminal is None else terminal.status
                    ),
                    "orphan_reason_code": (
                        None if orphaned is None else orphaned.reason_code
                    ),
                    "source_ledger_position": positions[id(start)],
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
                "direction": "input",
                "module_run_id": module.module_run_id,
                "source_ledger_position": positions[id(row)],
            }
            for row in module_derived_inputs
        )
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
        module_status = _module_status(
            attempt_start_views,
            module_attempts,
            module_outcomes,
        )
        module_statuses.append(module_status)
        if module_status == "recovery_required":
            modules_requiring_recovery.append(module.module_run_id)
        modules.append(
            {
                "module_run": {
                    **module.as_dict(),
                    "workflow_node_id": module.state_id,
                    "module_release_ref": module_release_ref,
                    "status": module_status,
                    "source_ledger_position": positions[id(module)],
                },
                "node_occurrence_index": node_occurrences[module.state_id],
                "module_release": {
                    "module_id": module.module_id,
                    "release_ref": module_release_ref,
                },
                "variants": variant_views,
                "attempt_starts": attempt_start_views,
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
                    tuple(module_statuses),
                    checkpoints,
                ),
                # Recovery is an orthogonal lease fact carried beside the
                # checkpoint-owned status, so a committed checkpoint never
                # masks an expired in-flight lease.
                "recovery_required": bool(modules_requiring_recovery),
                "modules_requiring_recovery": modules_requiring_recovery,
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
            "observed_at_utc": observed_at_utc,
        },
        "records": [_inspection_record_as_dict(row) for row in trace.records],
    }


__all__ = ["build_runtime_execution_inspection"]
