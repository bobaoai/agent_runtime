"""Request-scoped callback tools served by Runtime, not a sandboxed command."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import threading

from jsonschema import Draft202012Validator, ValidationError

from .invocation_process_execution import _runtime_python_executable
from .invocation_tool_definition import tool_definition_records
from .invocation_tool_transport import start_tool_server


class ProviderToolSessionBridge:
    """Own the IPC for one trusted self-test session and its exact tool table."""
    server_name = "runtime_tools"

    def __init__(self, *, request, host, factory, definitions, timeout_seconds):
        if request.self_test_binding_ref is None or request.has_operation_evidence:
            raise PermissionError("callback bridge accepts only actual self-test resources")
        self._request, self._host, self._factory = request, host, factory
        self._definitions = copy.deepcopy(definitions)
        self._records, self._fatal = [], None
        self._lock = threading.RLock()
        self._closed = False
        self._server = self._thread = self._session = None
        self._private = None
        self._validate_factory()
        self._session = factory.open_session(request)
        try:
            self._validate()
            from importlib.util import find_spec
            if find_spec("mcp") is None:
                raise ImportError("callback tools require agent-runtime-core[cli_tools]")
            self._private = Path(tempfile.mkdtemp(prefix="runtime-tools-")).resolve()
            self._endpoint = self._private / "tools.sock"
            self._server, self._thread = start_tool_server(self._endpoint, self._dispatch, max_request_bytes=4 * 1024 * 1024)
            proxy = Path(__file__).with_name("invocation_local_command_mcp.py")
            self.mcp_config = {"mcpServers": {self.server_name: {"type": "stdio",
                "command": str(_runtime_python_executable()), "args": ["-I", "-B", str(proxy), str(self._endpoint), "--callbacks"],
                "alwaysLoad": True, "timeout": (timeout_seconds + 15) * 1000}}}
        except BaseException:
            self.close()
            raise

    @property
    def private_root(self):
        return self._private

    @property
    def cli_tools(self):
        return {"mcp__runtime_tools__" + item["name"] for item in self._definitions}

    @property
    def records(self):
        with self._lock:
            return copy.deepcopy(self._records)

    def _validate_factory(self):
        self._host.validate_provider_tool_session(self._request, factory=self._factory, definitions=self._definitions)
        if tool_definition_records(self._factory.definitions) != self._definitions:
            raise PermissionError("factory tool definitions changed after request preparation")

    def _validate(self):
        if self._closed:
            raise PermissionError("callback tool session is closed")
        self._validate_factory()
        if self._session is not None and tool_definition_records(self._session.definitions) != self._definitions:
            raise PermissionError("session tools differ from the frozen prompt definitions")
        if self._session is not None and getattr(self._session, "request", None) != self._request:
            raise PermissionError("callback session belongs to another Attempt")

    def _dispatch(self, message):
        if message == {"method": "definitions"}:
            self._validate()
            return copy.deepcopy(self._definitions)
        if type(message) is not dict or set(message) != {"method", "tool_name", "payload"} or message["method"] != "invoke":
            raise ValueError("callback protocol accepts only an exact tool name and payload")
        return self.invoke(message["tool_name"], message["payload"])

    def invoke(self, name, payload):
        with self._lock:
            self._validate()
            identity = "runtime_tool_" + str(len(self._records) + 1)
            copied = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
            response = {"local_call_id": identity, "tool_name": name, "status": "failed", "allowed": False,
                        "result": None, "error": None}
            row = {"source_kind": "runtime_local", "tool_call_id": identity, "tool_name": name,
                   "request": copied, "response": None, "status": "incomplete"}
            self._records.append(row)
        def report(exc, *, fatal=False):
            if fatal and self._fatal is None:
                self._fatal = exc
            response["status"] = "failed"
            response["error"] = {"type": type(exc).__name__, "message": str(exc)}

        try:
            definition = next((item for item in self._definitions if item["name"] == name), None)
            if definition is None:
                raise ValueError("tool is not in the frozen callback set")
            Draft202012Validator(definition["inputSchema"]).validate(copied)
        except (ValidationError, ValueError) as exc:
            report(exc)
        else:
            try:
                self._validate()
            except Exception as exc:
                report(exc, fatal=True)
            else:
                response["allowed"] = True
                try:
                    result = self._session.invoke(name, copied, authorization=None)
                    response["result"] = json.loads(json.dumps(dict(result), ensure_ascii=False, allow_nan=False))
                    if len(json.dumps(response, ensure_ascii=False, allow_nan=False).encode()) > 64 * 1024 * 1024 - 1024:
                        response["result"] = None
                        raise ValueError("callback result exceeds its bounded response frame")
                    response["status"] = "completed"
                except Exception as exc:
                    # A callback refusal/failure is a tool result. Only Runtime's
                    # independent resource checks may invalidate the Attempt.
                    report(exc)
                try:
                    self._validate()
                except Exception as exc:
                    report(exc, fatal=True)
        with self._lock:
            row.update(response=copy.deepcopy(response), status=response["status"])
        return response

    def validate_completion(self):
        if self._fatal is not None:
            raise self._fatal
        self._validate_factory()
        if any(item["status"] == "incomplete" for item in self._records):
            raise RuntimeError("callback invocation has no recorded result")
        self._session.validate_completion()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
        error = None
        try:
            if self._session is not None:
                self._session.close()
        except BaseException as exc:
            error = exc
        finally:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                self._thread.join()
            if self._private is not None:
                shutil.rmtree(self._private)
        if error is not None:
            self._fatal = error
            raise error

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
