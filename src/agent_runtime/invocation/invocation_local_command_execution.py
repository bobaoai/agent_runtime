"""Execute declared local commands using live Runtime resources, not domain grants."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time

from ..contracts.invocation_adapter_definition import SelfTestResourceUnavailableError
from .invocation_cli_logging import captured_cli_streams
from .invocation_local_resource_preparation import (
    validate_local_resources, assert_local_materials_unchanged,
    LOCAL_RESOURCES_SCHEMA_REF, LOCAL_RESOURCES_SCHEMA_SHA256, LOCAL_RESOURCES_MEDIA_TYPE, LOCAL_RESOURCES_LOGICAL_NAME,
)
from .invocation_process_execution import run_cli_process, CliProcessInterrupted
from .invocation_tool_definition import ProviderToolDefinition


LOCAL_COMMAND_TOOL_NAME = "sandbox_command_execute"
LOCAL_COMMAND_SERVER_NAME = "runtime_commands"
LOCAL_COMMAND_CLI_TOOL_NAME = "mcp__runtime_commands__sandbox_command_execute"
_SYSTEM_READ_ROOTS = ("/bin", "/sbin", "/usr", "/System", "/Library", "/private/etc", "/private/var/db/dyld", "/dev")


class LocalCommandSession:
    """One Attempt's fixed commands and private CLI bridge.

    The real request-bound host validates resources and orders each Popen with
    close. Native Bash remains separate; only supplied command_id values run
    here. No Product authority, Gateway receipt or model result is manufactured.
    MCP is imported only by the optional stdio proxy, after dependency preflight.

    records are trusted local observations, with an Attempt-local local_call_id
    also returned in every accepted tool-call response. They are not Provider
    tool_use_id values. Ordinary command failures remain per-call results;
    validate_completion rethrows resource/integrity/recording failures.
    """

    def __init__(self, *, request, profile, host, adapter, artifact_host, workspace_root: Path,
                 resources_body: bytes, source_root: Path, scratch_root: Path,
                 read_only_dependencies: tuple[Path, ...] = ()) -> None:
        """Bind one exact Profile/control input to the existing live self-test host.

        source_root and scratch_root must belong to workspace_root. The private
        IPC/control directory is created separately and is never a task resource.
        Creates no model/command process; missing cli_tools or sandbox capability,
        mismatched content or overlapping private roots fail before CLI launch.
        """
        if sys.platform != "darwin" or not Path("/usr/bin/sandbox-exec").is_file():
            raise NotImplementedError("Local command isolation requires macOS sandbox-exec")
        if importlib.util.find_spec("mcp") is None:
            raise ImportError("Declared commands require the Runtime cli_tools extra")
        if (profile.release_ref, profile.release_sha256) != (request.execution_profile_ref, request.execution_profile_sha256):
            raise SelfTestResourceUnavailableError("Local command Profile differs from its exact request")
        resource = validate_local_resources(profile=profile, body=resources_body)
        if resource is None:
            raise ValueError("Local command session requires an explicit resource input")
        self._dependencies = tuple(Path(path).resolve(strict=True) for path in read_only_dependencies)
        if [str(path) for path in self._dependencies] != resource["read_only_dependencies"]:
            raise SelfTestResourceUnavailableError("Local command dependencies differ from the frozen resource input")
        self._request, self._host, self._adapter = request, host, adapter
        self._profile = profile
        self._artifacts, self._workspace = artifact_host, Path(workspace_root).resolve(strict=True)
        self._body = resources_body
        self._source = Path(source_root).resolve()
        self._scratch = Path(scratch_root).resolve(strict=True)
        if not self._source.is_relative_to(self._workspace) or not self._scratch.is_relative_to(self._workspace):
            raise SelfTestResourceUnavailableError("Local command directories differ from the Attempt workspace")
        self._commands = {item["command_id"]: item for item in resource["commands"]}
        if not self._commands:
            raise ValueError("Local command session requires an explicit nonempty command set")
        self._condition = threading.Condition(threading.RLock())
        self._closed = threading.Event()
        self._records, self._running, self._ordinal, self._fatal = [], 0, 0, None
        self._server = self._thread = self._private = None
        self._validate_host()
        control = [item for item in request.authorized_inputs if item.schema_ref == LOCAL_RESOURCES_SCHEMA_REF]
        if (len(control) != 1 or control[0].schema_sha256 != LOCAL_RESOURCES_SCHEMA_SHA256
                or control[0].logical_name != LOCAL_RESOURCES_LOGICAL_NAME or control[0].media_type != LOCAL_RESOURCES_MEDIA_TYPE
                or hashlib.sha256(resources_body).hexdigest() != control[0].input_sha256
                or host.read_authorized_input(control[0].local_handle) != resources_body):
            raise SelfTestResourceUnavailableError("Local commands are not the exact authorized resource input")
        self._verify_materials()
        self._private = Path(tempfile.mkdtemp(prefix="rc-", dir="/tmp")).resolve()
        try:
            readable = (self._source, self._scratch, *self._dependencies)
            if any(self._private.is_relative_to(path) or path.is_relative_to(self._private) for path in readable):
                raise PermissionError("Command control directory overlaps model-visible resources")
            protected = (self._workspace, Path.home() / ".codex", Path.home() / ".claude",
                         Path.home() / ".claude.json", Path.home() / "Library/Keychains")
            if any(path.resolve().is_relative_to(dep) or dep.is_relative_to(path.resolve())
                   for path in protected for dep in self._dependencies):
                raise PermissionError("Command dependencies overlap private execution or credential resources")
            self._socket_path = self._private / "commands.sock"
            self._profile_path = self._private / "sandbox.sb"
            self._profile_path.write_text(self._sandbox_profile(), encoding="utf-8")
            bins = [str(path / "bin") for path in self._dependencies if (path / "bin").is_dir()]
            self._environment = {"PATH": os.pathsep.join([*bins, "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
                "HOME": str(self._scratch), "TMPDIR": str(self._scratch), "LANG": "en_US.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_OPTIONAL_LOCKS": "0"}
            for command in self._commands.values():
                self._resolve_command(command)
            session = self
            class Handler(socketserver.StreamRequestHandler):
                def handle(self):
                    self.connection.settimeout(5)
                    try:
                        line = self.rfile.readline(65537)
                        if len(line) > 65536 or not line.endswith(b"\n"):
                            raise ValueError("Local command protocol request exceeds its bounded frame")
                        def unique(pairs):
                            value = {}
                            for key, item in pairs:
                                if key in value:
                                    raise ValueError("Duplicate local command protocol field")
                                value[key] = item
                            return value
                        def invalid_constant(value):
                            raise ValueError("Non-finite local command protocol value")
                        message = json.loads(line, object_pairs_hook=unique, parse_constant=invalid_constant)
                        if message == {"method": "definitions"}:
                            session._validate_host()
                            result = [{"name": item.tool_name, "description": item.description,
                                       "inputSchema": dict(item.input_schema)} for item in session.definitions]
                        elif isinstance(message, dict) and set(message) == {"method", "command_id"} and message["method"] == "invoke":
                            result = session.invoke(message["command_id"])
                        else:
                            raise ValueError("Local command protocol accepts only definitions or an exact command_id")
                        response = {"result": result}
                    except Exception as exc:
                        response = {"error": {"type": type(exc).__name__, "message": str(exc)}}
                    self.wfile.write(json.dumps(response, ensure_ascii=False, allow_nan=False).encode() + b"\n")
            class Server(socketserver.ThreadingUnixStreamServer):
                daemon_threads = True
            self._server = Server(str(self._socket_path), Handler)
            self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            self._thread.start()
        except BaseException:
            self.close()
            raise

    @property
    def definitions(self) -> tuple[ProviderToolDefinition, ...]:
        tool = ProviderToolDefinition(LOCAL_COMMAND_TOOL_NAME,
            "Execute one exact Runtime-provided command_id and return its actual process facts. "
            "Use native Bash separately for other allowed scratch work.",
            {"type": "object", "additionalProperties": False, "properties": {"command_id": {"type": "string"}},
             "required": ["command_id"]})
        tool.validate()
        return (tool,)

    @property
    def mcp_config(self) -> dict:
        """Return this installation's explicit stdio proxy and private endpoint."""
        proxy = Path(__file__).with_name("invocation_local_command_mcp.py").resolve(strict=True)
        return {"mcpServers": {LOCAL_COMMAND_SERVER_NAME: {"type": "stdio", "command": sys.executable,
            "args": ["-I", "-B", str(proxy), str(self._socket_path)],
            "alwaysLoad": True,
            "timeout": (max(item["timeout_seconds"] for item in self._commands.values()) + 15) * 1000}}}

    @property
    def records(self) -> list[dict]:
        """Return an independent snapshot of only this Session's actual calls."""
        with self._condition:
            return copy.deepcopy(self._records)

    @property
    def private_root(self) -> Path:
        """Locator used only to keep Provider/native tools outside control files."""
        return self._private

    def _set_fatal(self, exc):
        with self._condition:
            if self._fatal is None:
                self._fatal = exc

    def _validate_host(self):
        if self._closed.is_set():
            raise SelfTestResourceUnavailableError("Local command session is closed")
        method = getattr(self._host, "validate_self_test_binding", None)
        if not callable(method):
            raise SelfTestResourceUnavailableError("Local commands require a live Runtime self-test host")
        method(self._request, adapter=self._adapter, artifact_host=self._artifacts,
               workspace_root=self._workspace, read_only_dependencies=self._dependencies)

    def _verify_materials(self):
        assert_local_materials_unchanged(self._body, materials_root=self._source.parent)

    def _sandbox_profile(self):
        reads = {str(path.resolve()) for path in (*map(Path, _SYSTEM_READ_ROOTS), self._source, self._scratch, *self._dependencies)}
        permitted = " ".join(f"(subpath {json.dumps(path)})" for path in sorted(reads))
        # Private state and credential homes are not in the read set. An explicit
        # dependency cannot override the control/credential deny rules.
        protected = (self._private, Path.home() / ".codex", Path.home() / ".claude")
        denies = "\n".join(f"(deny file-read-data (subpath {json.dumps(str(path))}))" for path in protected)
        return "\n".join(("(version 1)", "(allow default)", "(deny network*)", "(allow file-read-metadata)",
            f"(deny file-read-data (require-not (require-any {permitted})))",
            f"(deny file-map-executable (require-not (require-any {permitted})))",
            f'(deny file-write* (require-not (require-any (subpath {json.dumps(str(self._scratch))}) (subpath "/dev"))))',
            '(allow file-read-data (literal "/"))', denies, ""))

    def _resolve_command(self, command):
        relative = PurePosixPath(command["cwd"])
        root = self._source if relative.parts[0] == "source" else self._scratch
        path = root.joinpath(*relative.parts[1:])
        cursor = root
        for part in relative.parts[1:]:
            cursor = cursor / part
            if cursor.is_symlink():
                raise PermissionError("Command cwd must not traverse a symlink")
            if root == self._scratch:
                cursor.mkdir(mode=0o700, exist_ok=True)
        cwd = path.resolve(strict=True)
        if not cwd.is_dir() or not cwd.is_relative_to(root):
            raise PermissionError("Command cwd escaped its declared local root")
        argv = list(command["argv"])
        if Path(argv[0]).is_absolute():
            executable = argv[0]
        elif os.sep in argv[0]:
            executable = cwd / argv[0]
        else:
            executable = shutil.which(argv[0], path=self._environment["PATH"])
        if not executable:
            raise FileNotFoundError("Declared command executable is unavailable")
        executable = Path(executable).resolve(strict=True)
        roots = (*map(Path, _SYSTEM_READ_ROOTS), self._source, self._scratch, *self._dependencies)
        if not executable.is_file() or not os.access(executable, os.X_OK) or not any(executable.is_relative_to(path.resolve()) for path in roots):
            raise PermissionError("Declared command executable is outside its readable executable resources")
        argv[0] = str(executable)
        return argv, cwd

    def invoke(self, command_id: str) -> dict:
        """Run one declared command and retain actual process/byte-level evidence.

        Unknown IDs, nonzero exits and timeouts return per-call failures with a
        local_call_id; they are not domain decisions. Resource/integrity faults
        also remain visible here and invalidate validate_completion even if the
        caller handles the tool error. No invocation of a missing command is
        invented, and no unknown exit code is replaced by zero.
        """
        with self._condition:
            self._ordinal += 1
            identity = f"local_command_{self._ordinal}"
            self._running += 1
        response = {"local_call_id": identity, "command_id": command_id, "argv": None, "cwd": None,
                    "allowed": False, "returncode": None, "stdout": "", "stderr": "",
                    "process_output_complete": False, "failure": None}
        process = None
        try:
            try:
                self._validate_host()
                self._verify_materials()
            except Exception as exc:
                self._set_fatal(exc)
                raise
            if not isinstance(command_id, str) or command_id not in self._commands:
                raise PermissionError("Command is outside this Attempt's declared command set")
            command = self._commands[command_id]
            argv, cwd = self._resolve_command(command)
            response.update(argv=argv, cwd=str(cwd), declared_argv=list(command["argv"]), declared_cwd=command["cwd"])
            def launch(create):
                def checked():
                    self._verify_materials()
                    # Order Session close with process creation using the same
                    # short lock. Never hold it while the command is running.
                    with self._condition:
                        self._validate_host()
                        response["allowed"] = True
                        return create()
                try:
                    return self._host.guard_self_test_launch(self._request, checked, adapter=self._adapter,
                        artifact_host=self._artifacts, workspace_root=self._workspace,
                        read_only_dependencies=self._dependencies)
                except Exception as exc:
                    self._set_fatal(exc)
                    raise
            def cancelled():
                try:
                    self._validate_host()
                    return False
                except Exception as exc:
                    self._set_fatal(exc)
                    return True
            process = run_cli_process(argv=["/usr/bin/sandbox-exec", "-f", str(self._profile_path), *argv],
                prompt="", cwd=cwd, environment=self._environment, timeout_seconds=command["timeout_seconds"],
                launch_guard=launch, cancel_requested=cancelled)
            response.update(returncode=process.returncode, stdout=process.stdout, stderr=process.stderr,
                            process_output_complete=True, **captured_cli_streams(process))
        except (Exception, CliProcessInterrupted) as exc:
            if isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired, CliProcessInterrupted)):
                response.update(returncode=getattr(exc, "returncode", None), **captured_cli_streams(exc))
                for stream in ("stdout", "stderr"):
                    value = getattr(exc, stream, "") or ""
                    response[stream] = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
            response["failure"] = {"error_type": type(exc).__name__, "message": str(exc),
                                   "stop_reason": getattr(exc, "stop_reason", None)}
        finally:
            try:
                self._validate_host()
                self._verify_materials()
            except Exception as exc:
                self._set_fatal(exc)
                response["failure"] = {"error_type": type(exc).__name__, "message": str(exc), "stop_reason": "resource_invalid"}
            row = {"tool_call_id": identity, "tool_name": LOCAL_COMMAND_TOOL_NAME, "source_kind": "runtime_local",
                   "request": {"command_id": command_id}, "response": response,
                   "status": "completed" if response["failure"] is None and response["returncode"] == 0 else "failed"}
            with self._condition:
                try:
                    json.dumps(row, ensure_ascii=False, allow_nan=False).encode("utf-8")
                    self._records.append(copy.deepcopy(row))
                except Exception as exc:
                    self._set_fatal(exc)
                    response["failure"] = {"error_type": type(exc).__name__, "message": str(exc), "stop_reason": "record_failure"}
                finally:
                    self._running -= 1
                    self._condition.notify_all()
        return copy.deepcopy(response)

    def validate_completion(self):
        """Re-raise a real resource/integrity failure; never require every command."""
        with self._condition:
            if self._running:
                raise RuntimeError("Local command result is still in flight")
            if self._fatal is not None:
                raise self._fatal

    def close(self):
        """Stop accepting calls, cancel live processes and remove owned controls.

        A bounded cleanup failure keeps diagnostic state and raises; it never
        signals successful completion or silently deletes another Session.
        """
        with self._condition:
            self._closed.set()
        if self._server is not None:
            if self._thread is not None and self._thread.is_alive():
                self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
        deadline = time.monotonic() + 6
        with self._condition:
            while self._running and time.monotonic() < deadline:
                self._condition.wait(timeout=0.05)
            if self._running:
                raise RuntimeError("Local command processes did not close; control files retained")
        if self._private is not None and self._private.exists():
            shutil.rmtree(self._private)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


__all__ = ["LocalCommandSession"]
