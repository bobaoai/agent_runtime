"""Bounded stdio MCP proxy; command execution remains in the Runtime parent.

This leaf can be launched by its exact installed/source file path under -I. It
imports no alternative Runtime installation and never evaluates command argv.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import socket
import sys


def exchange(endpoint: Path, message: dict) -> object:
    raw = json.dumps(message, ensure_ascii=False, allow_nan=False).encode("utf-8") + b"\n"
    if len(raw) > 65536:
        raise ValueError("Local command request is too large")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.connect(str(endpoint))
        channel.sendall(raw)
        with channel.makefile("rb") as stream:
            response = stream.readline(64 * 1024 * 1024 + 1)
    if len(response) > 64 * 1024 * 1024 or not response.endswith(b"\n"):
        raise RuntimeError("Local command response is missing or exceeds its frame")
    result = json.loads(response)
    if "error" in result:
        raise RuntimeError(result["error"]["type"] + ": " + result["error"]["message"])
    return result["result"]


async def serve(endpoint: Path):
    # Optional cli_tools dependency is loaded only by this explicitly selected
    # proxy. Core import, registration and no-command execution stay independent.
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp import types

    server = Server("runtime_commands")
    @server.list_tools()
    async def definitions():
        values = await asyncio.to_thread(exchange, endpoint, {"method": "definitions"})
        return [types.Tool(**item) for item in values]
    @server.call_tool()
    async def invoke(name: str, arguments: dict):
        if name != "sandbox_command_execute" or set(arguments) != {"command_id"}:
            raise ValueError("Only the declared command_id tool is available")
        response = await asyncio.to_thread(exchange, endpoint, {"method": "invoke", "command_id": arguments["command_id"]})
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False))],
            structuredContent=response, isError=response["failure"] is not None or response["returncode"] != 0)
    async with stdio_server() as (input_stream, output_stream):
        await server.run(input_stream, output_stream, server.create_initialization_options())


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: invocation_local_command_mcp.py PRIVATE_ENDPOINT")
    asyncio.run(serve(Path(sys.argv[1])))


if __name__ == "__main__":
    main()
