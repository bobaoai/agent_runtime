"""Pure JSON and encoded-stream decoding shared by Runtime readers."""
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


__all__ = ["decode_cli_event", "cli_stream_bytes"]
