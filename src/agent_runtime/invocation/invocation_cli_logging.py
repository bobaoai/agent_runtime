"""Capture and interpret public CLI logs without creating operation grants."""
from __future__ import annotations

import base64
import copy
import json
from typing import Any, Mapping


def decode_cli_event(line: str) -> dict:
    """Decode one JSON object that remains serializable as strict UTF-8 JSON."""
    try:
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("CLI event must be an object")
        json.dumps(event, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except RecursionError as exc:
        raise ValueError("CLI event exceeds supported JSON nesting") from exc
    return event


def captured_cli_streams(process) -> dict:
    """Encode the captured bytes; keep whether the transport actually supplied bytes.

    Text-only injected/legacy process results remain readable, but are not proof
    of byte-exact capture. This function neither reads files nor strips content.
    Its result belongs only in the request-bound private Attempt trace.
    """
    streams = {}
    exact = True
    for name in ("stdout", "stderr"):
        raw = getattr(process, name + "_bytes", None)
        if raw is None:
            raw = getattr(process, name, None) or b""
            if isinstance(raw, str):
                exact = False
                raw = raw.encode("utf-8")
        if not isinstance(raw, bytes):
            raise ValueError("CLI stream must contain bytes or text")
        streams[name] = {"encoding": "base64", "data": base64.b64encode(raw).decode("ascii")}
    return {"raw_streams": streams, "byte_capture_exact": exact}


def cli_stream_bytes(trace: Mapping[str, Any], stream: str) -> bytes:
    """Read one exact captured stream, or the explicitly legacy text representation."""
    if stream not in {"stdout", "stderr"}:
        raise ValueError("unknown CLI stream")
    if "raw_streams" in trace:
        value = trace["raw_streams"][stream]
        if value["encoding"] != "base64":
            raise ValueError("unsupported CLI stream encoding")
        return base64.b64decode(value["data"], validate=True)
    value = trace.get(stream, "")
    if not isinstance(value, str):
        raise ValueError("legacy CLI stream must be text")
    return value.encode("utf-8")


def _with_local_commands(view: dict, trace: Mapping[str, Any], *, callbacks=False) -> dict:
    """Correlate real parent-process facts with the response the CLI observed.

    This runs while Invocation creates its stored view, never while Ledger
    reinterprets historical logs. Neither matching command names nor order is
    evidence of identity: the actual returned local_call_id must pair uniquely,
    and the exact request/response JSON must agree with the parent record.
    """
    record_key = "local_callback_calls" if callbacks else "local_command_calls"
    if record_key not in trace:
        return view
    native = view["tool_calls"]
    view.setdefault("provider_tool_calls", copy.deepcopy(native))
    issues = view["issues"]
    records = trace[record_key]
    if type(records) is not list:
        issues.append("invalid_local_command_records")
        view["complete"] = False
        return view
    tool_name = trace.get("local_command_cli_tool_name")
    callback_tools = trace.get("local_callback_cli_tools", []) if callbacks else []
    if (callbacks and (type(callback_tools) is not list or any(type(name) is not str for name in callback_tools))) or (
            not callbacks and (not isinstance(tool_name, str) or not tool_name)):
        issues.append("local_command_tool_identity_unavailable")
        callback_tools = []
    by_id, bad_ids = {}, set()
    for position, supplied in enumerate(records):
        row = copy.deepcopy(supplied)
        identity = row.get("tool_call_id") if isinstance(row, dict) else None
        if not isinstance(identity, str) or not identity:
            issues.append(f"invalid_local_command_identity:{position}")
            continue
        if identity in by_id:
            issues.append(f"duplicate_local_command_identity:{identity}")
            bad_ids.add(identity)
            continue
        by_id[identity] = row
        response, request = row.get("response"), row.get("request")
        if callbacks:
            valid = (row.get("source_kind") == "runtime_local" and type(row.get("tool_name")) is str
                and "mcp__runtime_tools__" + row["tool_name"] in callback_tools
                and row.get("status") in {"completed", "failed"} and type(request) is dict
                and type(response) is dict and response.get("local_call_id") == identity
                and response.get("tool_name") == row["tool_name"] and response.get("status") == row["status"]
                and type(response.get("allowed")) is bool
                and (row["status"] != "completed" or (response["allowed"] and response.get("error") is None)))
            if not valid:
                issues.append(f"invalid_local_callback_record:{identity}")
                bad_ids.add(identity)
            continue
        valid = (row.get("source_kind") == "runtime_local" and row.get("tool_name") == "sandbox_command_execute"
            and row.get("status") in {"completed", "failed"} and type(request) is dict
            and set(request) == {"command_id"} and type(request["command_id"]) is str
            and type(response) is dict and response.get("local_call_id") == identity
            and response.get("command_id") == request["command_id"]
            and type(response.get("allowed")) is bool
            and (response.get("returncode") is None or type(response["returncode"]) is int)
            and type(response.get("stdout")) is str and type(response.get("stderr")) is str
            and type(response.get("process_output_complete")) is bool)
        if valid and row["status"] == "completed":
            valid = (response["allowed"] and response.get("returncode") == 0
                and response["process_output_complete"] and response.get("failure") is None)
        if valid and response["allowed"]:
            valid = (type(response.get("argv")) is list and bool(response["argv"])
                and all(type(value) is str for value in response["argv"])
                and type(response.get("cwd")) is str and bool(response["cwd"]))
        if not valid:
            issues.append(f"invalid_local_command_record:{identity}")
            bad_ids.add(identity)
            continue
        if response["allowed"]:
            if response.get("byte_capture_exact") is not True:
                issues.append(f"local_command_byte_capture_unavailable:{identity}")
            if not response["process_output_complete"]:
                issues.append(f"local_command_output_incomplete:{identity}")
        if "raw_streams" in response:
            try:
                for stream in ("stdout", "stderr"):
                    if cli_stream_bytes(response, stream).decode("utf-8", errors="replace") != response[stream]:
                        raise ValueError("command text differs from captured bytes")
            except (ValueError, TypeError, KeyError):
                issues.append(f"invalid_local_command_streams:{identity}")
                bad_ids.add(identity)

    observed, incomplete_native = {}, set()
    for index, call in enumerate(native):
        if (call.get("tool_name") not in callback_tools if callbacks else call.get("tool_name") != tool_name):
            continue
        response = call.get("response")
        block = response.get("tool_result") if isinstance(response, dict) else None
        content = block.get("content") if isinstance(block, dict) else None
        if isinstance(content, list) and all(isinstance(item, dict) and item.get("type") == "text"
                                            and isinstance(item.get("text"), str) for item in content):
            content = "\n".join(item["text"] for item in content)
        try:
            returned = decode_cli_event(content) if isinstance(content, str) else None
        except (ValueError, UnicodeError):
            returned = None
        identity = returned.get("local_call_id") if isinstance(returned, dict) else None
        if not isinstance(identity, str) or not identity:
            if call.get("status") == "completed":
                issues.append(f"local_command_response_identity_missing:{call['tool_call_id']}")
                incomplete_native.add(index)
            continue
        if identity not in by_id:
            issues.append(f"local_command_parent_record_missing:{identity}")
            incomplete_native.add(index)
            continue
        observed.setdefault(identity, []).append((index, returned))

    replacements, paired = {}, set()
    for identity, row in by_id.items():
        candidates = observed.get(identity, [])
        if len(candidates) != 1:
            issues.append(f"local_command_correlation_{'missing' if not candidates else 'ambiguous'}:{identity}")
            bad_ids.add(identity)
            incomplete_native.update(index for index, _ in candidates)
            continue
        index, returned = candidates[0]
        call = native[index]
        same_json = lambda left, right: json.dumps(left, sort_keys=True, ensure_ascii=False, allow_nan=False) == json.dumps(right, sort_keys=True, ensure_ascii=False, allow_nan=False)
        if (identity in bad_ids or call.get("status") != row.get("status")
                or (callbacks and call.get("tool_name") != "mcp__runtime_tools__" + row["tool_name"])
                or len(call.get("request_event_indices", [])) != 1
                or not same_json(call.get("request"), row.get("request"))
                or not same_json(returned, row.get("response"))):
            issues.append(f"local_command_correlation_conflict:{identity}")
            bad_ids.add(identity)
            incomplete_native.add(index)
            continue
        row.update(provider_tool_call_id=call["tool_call_id"], provider_tool_name=call["tool_name"],
            request_event_indices=list(call["request_event_indices"]),
            response_event_indices=list(call["response_event_indices"]))
        replacements[index] = row
        paired.add(identity)
    unified = [replacements.get(index, {**call, "status": "incomplete"} if index in incomplete_native else call)
               for index, call in enumerate(native)]
    for identity, row in by_id.items():
        if identity not in paired:
            row["status"] = "incomplete"
            unified.append(row)
    view.update(tool_calls=unified, complete=not issues)
    return view


def parse_cli_log(trace: Mapping[str, Any]) -> dict:
    """Project observed Claude/Codex CLI tool calls from a trusted private trace.

    Runtime Adapters and trusted execution-log readers supply this trace, never
    the model's final answer. Returned data describes observations, not grants
    or proof that this was a managed Runtime invocation. All source events remain
    in events and raw bytes remain in the supplied trace. Event indices are
    zero-based stdout line positions. Unknown/duplicate/missing events make the
    log incomplete; unknown exit codes or tool times are never invented.

    Claude permission events and terminal denial summaries can complement one
    real tool_result; they neither create another call nor fabricate that block.
    Ambiguous or contradictory calls have status=incomplete, never trustworthy
    completed. Codex native completion snapshots retain their public fields;
    absent patch bodies and search results are explicit content limitations.
    A complete failed turn is a recorded terminal, not a missing terminal.
    When this invocation supplies local_command_calls, exact returned IDs and
    matching request/result JSON correlate its own process facts with the CLI.
    A paired call appears once in tool_calls with both real identities; the
    untouched CLI observations remain in provider_tool_calls and events. Missing,
    contradictory or duplicate correlations are incomplete, never inferred.
    """
    if not isinstance(trace, Mapping):
        raise ValueError("CLI trace must be an object")
    transport = trace.get("transport")
    issues = []
    if trace.get("byte_capture_exact") is not True:
        issues.append("byte_exact_capture_unavailable")
    if trace.get("process_output_complete") is not True:
        issues.append("process_output_incomplete")
    if transport not in {"claude_cli", "codex_cli"}:
        return {"schema_version": "runtime_cli_log_v1", "complete": False, "issues": [*issues, "unsupported_transport"],
                "events": [], "tool_calls": None}
    events, calls = [], {}
    ambiguous, response_kinds, failed_results = set(), {}, {}
    denials, native_started = set(), set()
    terminal_count = 0

    def issue(message, identity=None):
        issues.append(message)
        if isinstance(identity, str):
            ambiguous.add(identity)

    def call(identity, position):
        if not isinstance(identity, str) or not identity:
            issue(f"missing_tool_call_id:{position}")
            return None
        if identity not in calls:
            calls[identity] = {"tool_call_id": identity, "tool_name": None,
                "source_kind": "provider_native", "request": None, "response": None,
                "status": "incomplete", "request_event_indices": [], "response_event_indices": []}
        return calls[identity]

    def request(identity, name, arguments, position, *, native_update=False):
        row = call(identity, position)
        if row is None:
            return
        if row["request_event_indices"] and not native_update:
            issue(f"duplicate_tool_request:{identity}", identity)
        else:
            row.update(tool_name=name, request=arguments)
        row["request_event_indices"].append(position)
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            issue(f"invalid_tool_request:{identity}", identity)

    def response(identity, body, failed, position, *, origin="tool_result"):
        row = call(identity, position)
        if row is None:
            return
        kinds = response_kinds.setdefault(identity, set())
        if origin in kinds:
            issue(f"duplicate_tool_response:{identity}", identity)
        else:
            kinds.add(origin)
            if transport == "claude_cli":
                if row["response"] is None:
                    row["response"] = {}
                row["response"].update(body)
            else:
                row["response"] = body
            if origin == "tool_result":
                failed_results[identity] = failed
            else:
                denials.add(identity)
        if position not in row["response_event_indices"]:
            row["response_event_indices"].append(position)

    def permission_denial(value, position, *, summary=False):
        if not isinstance(value, dict):
            issue(f"invalid_permission_denial:{position}")
            return
        identity = value.get("tool_use_id")
        origin = "permission_denials" if summary else "permission_denied"
        response(identity, {origin: value}, True, position, origin=origin)
        row = calls.get(identity) if isinstance(identity, str) else None
        if row and value.get("tool_name") is not None and row["tool_name"] not in (None, value["tool_name"]):
            issue(f"permission_denial_tool_mismatch:{identity}", identity)

    def progress(identity, name, position):
        # A progress snapshot proves a tool was observed, not that its request
        # or result was captured. Register the ID so an orphan stays unpaired.
        # The exact snapshot remains in events, never in invented start/result fields.
        row = call(identity, position)
        if row is not None and row["tool_name"] is None:
            row["tool_name"] = name

    def native_codex_item(event, item, position):
        kind, identity = item["type"], item.get("id")
        stage = event.get("type")
        if stage not in {"item.started", "item.updated", "item.completed"}:
            return
        if stage == "item.updated":
            progress(identity, kind, position)
            return
        fields = ("command",) if kind == "command_execution" else (
            ("changes",) if kind == "file_change" else ("query", "action"))
        arguments = {key: item[key] for key in fields if key in item}
        update = stage == "item.completed" and isinstance(identity, str) and identity in native_started
        previous = calls.get(identity) if isinstance(identity, str) else None
        if update and previous and isinstance(previous["request"], dict):
            if previous["tool_name"] != kind:
                issue(f"conflicting_tool_request:{identity}", identity)
            stable_field = fields[0]
            old, new = previous["request"].get(stable_field), arguments.get(stable_field)
            if old and new and old != new:
                issue(f"conflicting_tool_request:{identity}", identity)
        request(identity, kind, arguments, position, native_update=update)
        row = calls.get(identity) if isinstance(identity, str) else None
        if row is None:
            return
        if stage == "item.started":
            native_started.add(identity)
            return
        native_started.discard(identity)
        if kind == "command_execution":
            status, exit_code = item.get("status"), item.get("exit_code")
            if not isinstance(item.get("command"), str) or not item["command"]:
                issue(f"invalid_tool_request:{identity}", identity)
            if exit_code is not None and type(exit_code) is not int:
                issue(f"invalid_tool_exit_code:{identity}", identity)
            if status not in {"completed", "failed", "declined"}:
                issue(f"invalid_tool_status:{identity}", identity)
            if status == "declined" and exit_code is not None:
                issue(f"conflicting_tool_results:{identity}", identity)
            failed = status in {"failed", "declined"} or (type(exit_code) is int and exit_code != 0)
        elif kind == "file_change":
            if not isinstance(item.get("changes"), list):
                issue(f"invalid_tool_request:{identity}", identity)
            if item.get("status") not in {"completed", "failed"}:
                issue(f"invalid_tool_status:{identity}", identity)
            failed = item.get("status") == "failed"
            # Public CLI events report changed paths/kinds, not the patch body.
            issue(f"provider_request_content_unavailable:{identity}")
        else:
            failed = False  # The public item.completed event completed the search.
            if not isinstance(item.get("query"), str) or not item["query"]:
                issue(f"invalid_tool_request:{identity}", identity)
            # Query/action are public; actual retrieved pages are not supplied.
            issue(f"provider_result_content_unavailable:{identity}")
        # Keep the exact completed item. Aggregated command output is not
        # split into fictitious stdout/stderr; file events do not prove adoption.
        response(identity, item, failed, position)

    for position, line in enumerate(cli_stream_bytes(trace, "stdout").splitlines()):
        if not line.strip():
            continue
        try:
            event = decode_cli_event(line.decode("utf-8"))
        except (UnicodeError, ValueError):
            issues.append(f"invalid_event:{position}")
            continue
        events.append({"index": position, "event": event})
        if event.get("type") in (("result",) if transport == "claude_cli" else ("turn.completed", "turn.failed")):
            terminal_count += 1
        if transport == "claude_cli":
            if event.get("type") == "system" and event.get("subtype") == "permission_denied":
                permission_denial(event, position)
            if event.get("type") == "result" and "permission_denials" in event:
                summary = event["permission_denials"]
                if not isinstance(summary, list):
                    issue(f"invalid_permission_denials:{position}")
                else:
                    for denial in summary:
                        permission_denial(denial, position, summary=True)
            message = event.get("message", {})
            blocks = message.get("content", []) if isinstance(message, dict) else []
            if not isinstance(blocks, list):
                issues.append(f"invalid_message_content:{position}")
                continue
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                if event.get("type") == "assistant" and block.get("type") == "tool_use":
                    request(block.get("id"), block.get("name"), block.get("input"), position)
                elif event.get("type") == "user" and block.get("type") == "tool_result":
                    # Keep both the complete block and optional CLI-owned result
                    # details. A successful text result is not a shell exit code.
                    body = {"tool_result": block}
                    if "tool_use_result" in event:
                        body["tool_use_result"] = event["tool_use_result"]
                    if "is_error" in block and type(block["is_error"]) is not bool:
                        issue(f"invalid_tool_error_flag:{position}", block.get("tool_use_id"))
                    response(block.get("tool_use_id"), body, block.get("is_error") is True, position)
        else:
            item = event.get("item", {})
            if isinstance(item, dict) and item.get("type") in {"command_execution", "file_change", "web_search"}:
                native_codex_item(event, item, position)
                continue
            if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
                if isinstance(item, dict) and (str(item.get("type", "")).endswith("tool_call")
                        or item.get("type") in {"command_execution", "file_change", "web_search"}):
                    issues.append(f"unsupported_tool_event:{position}")
                continue
            if event.get("type") == "item.started":
                request(item.get("id"), item.get("tool"), item.get("arguments"), position)
            elif event.get("type") == "item.updated":
                progress(item.get("id"), item.get("tool"), position)
            elif event.get("type") == "item.completed":
                raw = item.get("result")
                body = raw
                if isinstance(raw, dict):
                    if isinstance(raw.get("structured_content"), dict):
                        body = raw["structured_content"]
                    elif isinstance(raw.get("content"), list) and len(raw["content"]) == 1:
                        block = raw["content"][0]
                        if isinstance(block, dict) and block.get("type") == "text":
                            try:
                                decoded = json.loads(block["text"])
                                json.dumps(decoded, ensure_ascii=False, allow_nan=False).encode("utf-8")
                                body = decoded
                            except (TypeError, ValueError, KeyError, UnicodeError, RecursionError):
                                pass
                if item.get("error") is not None:
                    body = {"result": raw, "error": item["error"]}
                response(item.get("id"), body,
                    item.get("status") != "completed" or item.get("error") is not None, position)
    for identity, row in calls.items():
        if not row["request_event_indices"] or not row["response_event_indices"]:
            issue(f"unpaired_tool_call:{identity}", identity)
        if identity in denials and failed_results.get(identity) is False:
            issue(f"conflicting_tool_results:{identity}", identity)
        if isinstance(row["response"], dict) and transport == "claude_cli":
            for key in ("permission_denied", "permission_denials"):
                denied = row["response"].get(key)
                if (denied and row["tool_name"] is not None and denied.get("tool_name") is not None
                        and denied["tool_name"] != row["tool_name"]):
                    issue(f"permission_denial_tool_mismatch:{identity}", identity)
        row["status"] = ("incomplete" if identity in ambiguous else
                         "failed" if identity in denials or failed_results.get(identity) else "completed")
    if terminal_count != 1:
        issues.append("terminal_event_missing_or_duplicated")
    view = _with_local_commands({"schema_version": "runtime_cli_log_v1", "complete": not issues,
        "issues": issues, "events": events, "tool_calls": list(calls.values())}, trace)
    return _with_local_commands(view, trace, callbacks=True)


__all__ = ["captured_cli_streams", "cli_stream_bytes", "parse_cli_log"]
