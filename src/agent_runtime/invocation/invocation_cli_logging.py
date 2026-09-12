"""Capture and interpret public CLI logs without creating operation grants."""
from __future__ import annotations

import base64
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


def parse_cli_log(trace: Mapping[str, Any]) -> dict:
    """Project observed Claude/Codex CLI tool calls from a trusted private trace.

    Runtime Adapters and trusted execution-log readers supply this trace, never
    the model's final answer. Returned data describes observations, not grants
    or proof that this was a managed Runtime invocation. All source events remain
    in events and raw bytes remain in the supplied trace. Event indices are
    zero-based stdout line positions. Unknown/duplicate/missing events make the
    log incomplete; unknown exit codes or tool times are never invented.
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
    terminal_count = 0

    def call(identity, position):
        if not isinstance(identity, str) or not identity:
            issues.append(f"missing_tool_call_id:{position}")
            return None
        if identity not in calls:
            calls[identity] = {"tool_call_id": identity, "tool_name": None,
                "source_kind": "provider_native", "request": None, "response": None,
                "status": "incomplete", "request_event_indices": [], "response_event_indices": []}
        return calls[identity]

    def request(identity, name, arguments, position):
        row = call(identity, position)
        if row is None:
            return
        if row["request_event_indices"]:
            issues.append(f"duplicate_tool_request:{identity}")
        else:
            row.update(tool_name=name, request=arguments)
        row["request_event_indices"].append(position)
        if not isinstance(name, str) or not name or not isinstance(arguments, dict):
            issues.append(f"invalid_tool_request:{identity}")

    def response(identity, body, failed, position):
        row = call(identity, position)
        if row is None:
            return
        if row["response_event_indices"]:
            issues.append(f"duplicate_tool_response:{identity}")
        else:
            row.update(response=body, status="failed" if failed else "completed")
        row["response_event_indices"].append(position)

    for position, line in enumerate(cli_stream_bytes(trace, "stdout").splitlines()):
        if not line.strip():
            continue
        try:
            event = decode_cli_event(line.decode("utf-8"))
        except (UnicodeError, ValueError):
            issues.append(f"invalid_event:{position}")
            continue
        events.append({"index": position, "event": event})
        if event.get("type") == ("result" if transport == "claude_cli" else "turn.completed"):
            terminal_count += 1
        if transport == "claude_cli":
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
                    response(block.get("tool_use_id"), body, block.get("is_error") is True, position)
        else:
            item = event.get("item", {})
            if not isinstance(item, dict) or item.get("type") != "mcp_tool_call":
                if isinstance(item, dict) and (str(item.get("type", "")).endswith("tool_call")
                        or item.get("type") in {"command_execution", "file_change", "web_search"}):
                    issues.append(f"unsupported_tool_event:{position}")
                continue
            if event.get("type") == "item.started":
                request(item.get("id"), item.get("tool"), item.get("arguments"), position)
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
            row["status"] = "incomplete"
            issues.append(f"unpaired_tool_call:{identity}")
    if terminal_count != 1:
        issues.append("terminal_event_missing_or_duplicated")
    return {"schema_version": "runtime_cli_log_v1", "complete": not issues, "issues": issues, "events": events,
            "tool_calls": list(calls.values())}


__all__ = ["captured_cli_streams", "cli_stream_bytes", "parse_cli_log"]
