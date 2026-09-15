"""Private bounded Unix transport shared by Runtime-owned MCP tools."""
import json
import socketserver
import threading


def start_tool_server(path, dispatch, *, max_request_bytes=65536):
    """Start one owned endpoint; the caller closes resources before joining it."""
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(5)
            try:
                line = self.rfile.readline(max_request_bytes + 1)
                if len(line) > max_request_bytes or not line.endswith(b"\n"):
                    raise ValueError("Local tool request exceeds its bounded frame")
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("Duplicate local tool protocol field")
                        result[key] = value
                    return result
                def invalid_constant(value):
                    raise ValueError("Non-finite local tool protocol value")
                message = json.loads(line, object_pairs_hook=unique, parse_constant=invalid_constant)
                response = {"result": dispatch(message)}
            except Exception as exc:
                response = {"error": {"type": type(exc).__name__, "message": str(exc)}}
            raw = json.dumps(response, ensure_ascii=False, allow_nan=False).encode() + b"\n"
            if len(raw) > 64 * 1024 * 1024:
                raw = b'{"error":{"type":"ValueError","message":"Local tool response exceeds its bounded frame"}}\n'
            self.wfile.write(raw)
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = False
        block_on_close = True
    server = Server(str(path), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    return server, thread
