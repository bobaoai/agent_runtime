"""Capture original CLI stream bytes without interpreting Provider events."""
from __future__ import annotations

import base64


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


__all__ = ["captured_cli_streams"]
