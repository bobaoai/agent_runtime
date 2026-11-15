"""Synthetic, credential-free stdio MCP server for the mcp_data usage-pattern example.

The selected CLI starts this file from the example's ordinary MCP connection
entry in root/.runtime/config.json, exactly as it would start a host's own
server:

    python conformance_fixture_mcp_serving.py --audit AUDIT.jsonl

It serves one tool, get_stock_snapshot, whose result is synthetic stock data
with a fresh response_id for every call. Each call is appended to the audit
file before its result is returned, so the example check can compare the files
the model saved with what the server actually returned. The server reads no
credentials, contacts no network service and is not a Runtime callback, IPC
channel or tool supervisor. It uses the standard mcp package (FastMCP); install
the cli_tools extra or mcp in the interpreter that runs the example.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
from typing import Any

SERVER_NAME = "example_data"
TOOL_NAME = "get_stock_snapshot"
SKUS = ("BOLT-M6", "NUT-M6", "WASHER-M6")


def stock_snapshot(warehouse: str, request_tag: str) -> dict:
    """Return one synthetic snapshot with a fresh response_id and random unit counts."""
    return {"response_id": "resp-" + secrets.token_hex(6), "warehouse": warehouse, "request_tag": request_tag,
            "items": [{"sku": sku, "units": 10 + secrets.randbelow(90)} for sku in SKUS],
            "source": "synthetic example fixture"}


def append_audit(path: Path, *, tool: str, arguments: dict, result: dict) -> None:
    """Append one tools/call line (arguments and the exact result) and flush it to disk."""
    line = {"event": "tools/call", "tool": tool, "arguments": arguments, "result": result, "pid": os.getpid(),
            "received_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")}
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def build_server(audit: Path):
    """Build the FastMCP application; importing mcp happens only here."""
    from mcp.server.fastmcp import FastMCP

    app = FastMCP(SERVER_NAME)

    @app.tool(name=TOOL_NAME, structured_output=True)
    def get_stock_snapshot(warehouse: str, request_tag: str) -> dict[str, Any]:
        """Return a synthetic stock snapshot for one warehouse.

        The result holds response_id, warehouse, request_tag (echoed), items
        (sku and units) and source. Every call returns new numbers.
        """
        result = stock_snapshot(warehouse, request_tag)
        append_audit(audit, tool=TOOL_NAME, arguments={"warehouse": warehouse, "request_tag": request_tag},
                     result=result)
        return result

    return app


def main(argv=None) -> int:
    """Serve the fixture over stdio until the client closes it."""
    parser = argparse.ArgumentParser(description="Synthetic stdio MCP fixture for the mcp_data example")
    parser.add_argument("--audit", type=Path, required=True, help="Append-only JSON Lines file for received calls")
    args = parser.parse_args(argv)
    build_server(args.audit).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
