"""Read complete private tool logs from existing Ledger facts and content."""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Callable

from ..contracts.ledger_lineage_definition import ModuleRunRecord, ModuleAttemptRecord
from ..contracts.ledger_record_definition import RuntimeExecutionTrace, WorkflowAttemptRecord, ToolCallRecord


def read_execution_log(
    source: ModuleRunRecord | RuntimeExecutionTrace,
    *,
    attempts: tuple[ModuleAttemptRecord, ...] = (),
    read_content: Callable[[str, str], bytes],
    include_private_content: bool = False,
) -> dict:
    """Read one authorized Runtime result/trace without executing or writing.

    Args:
        source: Exact ModuleRunRecord from the kernel, or RuntimeExecutionTrace
            from an authorized Ledger query. Their existing identities bind the
            log; a model-authored dictionary is not accepted as execution truth.
        attempts: All exact terminal Attempts for a ModuleRunRecord. A durable
            RuntimeExecutionTrace already carries these; leave attempts empty.
        read_content: The corresponding authorized private content reader,
            called with the exact ref and SHA-256. Returned bytes are verified.
            For a Workflow trace it must be bound to that execution's store.
        include_private_content: False returns metadata without reading content.
            True returns the full saved provider trace, original encoded streams,
            normalized per-call data and explicit completeness issues. This flag
            expresses the caller's already-authorized disclosure choice.
    Returns:
        runtime_execution_log_v1 with workflow_execution_id, all attempts and
        complete. Each attempt retains its Module/Variant/Attempt identity,
        terminal status, private provider_log, tool_calls, issues and complete.
        Native calls retain original event indices and never acquire a grant;
        Gateway calls retain their exact request/response refs and bytes.
        Missing/legacy logs are explicitly incomplete, not proof of zero calls.
        Unknown shell exit codes and tool-level times are not inferred. Reading
        an in-memory source does not make it durably saved or recoverable later.
    Raises:
        ValueError: Invalid source, crossed identities, duplicate attempts or
            a content hash mismatch. Native reader/storage failures propagate;
            no empty successful log is substituted for missing required content.
    Effects:
        Reads only requested private content, with no store mutation, Provider
        call, grant, credential discovery or filesystem traversal. Snapshot/UI
        consumers may abbreviate presentation without changing returned bodies.
    """
    if type(include_private_content) is not bool:
        raise ValueError("include_private_content must be a boolean")
    if type(source) is ModuleRunRecord:
        source.validate()
        if type(attempts) is not tuple or any(type(row) is not ModuleAttemptRecord for row in attempts):
            raise ValueError("Module log requires exact ModuleAttemptRecord values")
        execution_id = source.workflow_execution_id
        module_ids = {source.module_run_id}
        gateways = {row.attempt_id: row.tool_calls for row in attempts}
        variants = {row.variant_id for row in attempts}
    elif type(source) is RuntimeExecutionTrace:
        if attempts:
            raise ValueError("Workflow trace supplies its own Attempts")
        attempts = source.records_of_type(WorkflowAttemptRecord)
        execution_id = source.workflow_execution_id
        module_ids = {row.module_run_id for row in attempts}
        variants = {row.variant_id for row in attempts}
        gateways = {row.attempt_id: [] for row in attempts}
        for row in source.records_of_type(ToolCallRecord):
            row.validate()
            if row.workflow_execution_id != execution_id or row.attempt_id not in gateways:
                raise ValueError("Tool log lies outside the requested execution/Attempt")
            gateways[row.attempt_id].append(row)
    else:
        raise ValueError("source must be a Runtime result or execution trace")

    def content(ref, digest):
        body = read_content(ref, digest)
        if not isinstance(body, bytes) or hashlib.sha256(body).hexdigest() != digest:
            raise ValueError("Execution log content hash mismatch")
        return body

    def value(body):
        try:
            return json.loads(body)
        except (UnicodeError, ValueError):
            return {"encoding": "base64", "data": base64.b64encode(body).decode("ascii")}

    rows, seen = [], set()
    for attempt in attempts:
        attempt.validate()
        if (attempt.attempt_id in seen or attempt.module_run_id not in module_ids
                or attempt.variant_id not in variants
                or getattr(attempt, "workflow_execution_id", execution_id) != execution_id):
            raise ValueError("Execution log has duplicate or crossed Attempt identities")
        seen.add(attempt.attempt_id)
        row = {"module_run_id": attempt.module_run_id, "variant_id": attempt.variant_id,
            "attempt_id": attempt.attempt_id, "status": attempt.status,
            "provider_trace_ref": attempt.provider_trace_ref,
            "provider_trace_sha256": attempt.provider_trace_sha256,
            "provider_log": None, "tool_calls": None,
            "complete": None, "issues": ["private_content_not_requested"]}
        if include_private_content:
            row.update(tool_calls=[], complete=False, issues=[])
            if attempt.provider_trace_ref is None:
                row["issues"].append("provider_log_not_recorded")
            else:
                trace = json.loads(content(attempt.provider_trace_ref, attempt.provider_trace_sha256))
                if not isinstance(trace, dict):
                    raise ValueError("Provider trace must be an object")
                for identity in ("module_run_id", "variant_id", "attempt_id"):
                    if identity in trace and trace[identity] != row[identity]:
                        raise ValueError("Provider log crossed its recorded Attempt identity")
                row["provider_log"] = trace
                parsed = trace.get("tool_log")
                if parsed is None:
                    row["issues"].append("normalized_provider_log_not_recorded")
                else:
                    if (not isinstance(parsed, dict) or parsed.get("schema_version") != "runtime_cli_log_v1"
                            or type(parsed.get("complete")) is not bool
                            or not isinstance(parsed.get("issues"), list)
                            or not isinstance(parsed.get("tool_calls"), (list, type(None)))):
                        raise ValueError("Invalid stored Provider log view")
                    row["issues"].extend(parsed["issues"])
                    if not parsed["complete"] and not parsed["issues"]:
                        row["issues"].append("provider_log_incomplete")
                    row["tool_calls"].extend(parsed["tool_calls"] or [])
            for call in gateways[attempt.attempt_id]:
                call.validate()
                if call.request_ref is None or call.response_ref is None:
                    row["issues"].append("gateway_content_not_recorded:" + call.tool_call_id)
                    continue
                request = content(call.request_ref, call.request_sha256)
                response = content(call.response_ref, call.response_sha256)
                row["tool_calls"].append({"tool_call_id": call.tool_call_id,
                    "tool_name": getattr(call, "tool_name", None) or call.tool_id,
                    "source_kind": "gateway", "request": value(request), "response": value(response),
                    "request_ref": call.request_ref, "request_sha256": call.request_sha256,
                    "response_ref": call.response_ref, "response_sha256": call.response_sha256,
                    "request_bytes_base64": base64.b64encode(request).decode("ascii"),
                    "response_bytes_base64": base64.b64encode(response).decode("ascii"),
                    "status": getattr(call, "status_id", "completed")})
            ids = [call["tool_call_id"] for call in row["tool_calls"]]
            if len(ids) != len(set(ids)):
                row["issues"].append("overlapping_tool_call_ids")
            row["complete"] = not row["issues"]
        rows.append(row)
    tool_calls = [{**call, "module_run_id": row["module_run_id"], "variant_id": row["variant_id"],
                   "attempt_id": row["attempt_id"]} for row in rows for call in (row["tool_calls"] or [])]
    return {"schema_version": "runtime_execution_log_v1", "workflow_execution_id": execution_id,
            "tool_calls": tool_calls if include_private_content else None,
            "attempts": rows, "complete": (bool(rows) and all(row["complete"] is True for row in rows))
            if include_private_content else None}


__all__ = ["read_execution_log"]
