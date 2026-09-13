"""Translate normalized Runtime execution requests into isolated Claude CLI calls."""

from __future__ import annotations

import hashlib
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Callable

from jsonschema import Draft202012Validator, ValidationError

from ..contracts.invocation_adapter_definition import (
    AuthorizedAgentExecutionHost, AuthorizedAgentExecutionRequest, AgentExecutionResult, OutputSubmission,
    SelfTestResourceUnavailableError, AgentExecutionAdapterDescriptor,
)
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from .invocation_context_preparation import InvocationExecutionExpectation, prepare_registered_invocation_context
from .invocation_process_execution import run_cli_process, CliProcessInterrupted, _capture_cli_interrupts
from .invocation_cli_logging import captured_cli_streams, parse_cli_log, decode_cli_event
from .invocation_prompt_assembly import NATIVE_STRUCTURED_OUTPUT
from .invocation_result_assembly import (
    TerminalAdapterFailure, commit_attempt_trace_json, completed_adapter_result,
    raise_terminal_failure, finalize_adapter_result,
)
from .invocation_schema_projection import NativeOutputSchemaProjectionError, claude_native_output_schema
from .invocation_tool_definition import ModuleArtifactHost, runtime_package_version
from .invocation_local_resource_preparation import (
    LOCAL_RESOURCES_SCHEMA_REF, LOCAL_RESOURCES_SCHEMA_SHA256, LOCAL_RESOURCES_MEDIA_TYPE,
    LOCAL_RESOURCES_LOGICAL_NAME, parse_local_resources, materialize_local_resources,
    assert_local_materials_unchanged, validate_local_resources, LocalResourceError,
)
from .invocation_local_command_execution import LocalCommandSession, LOCAL_COMMAND_CLI_TOOL_NAME, LOCAL_COMMAND_SERVER_NAME
from .invocation_workspace_preparation import (
    AttemptWorkspaceConflictError, lease_attempt_workspace, prepare_attempt_workspace,
)


NATIVE_TOOLS = {"read": "Read", "search": "Grep", "shell": "Bash"}
_CURRENT_BINDING = ("claude_cli_adapter", "v2")


def _validate_binding(binding: tuple[str, str]) -> None:
    if type(binding) is not tuple or binding != _CURRENT_BINDING:
        raise ValueError("ClaudeAdapter requires claude_cli_adapter@v2; historical records need an explicit new execution Profile")


def _execution_expectation(profile, adapter_binding=_CURRENT_BINDING) -> InvocationExecutionExpectation:
    """Validate supported field combinations without opening executable/resources."""
    _validate_binding(adapter_binding)
    profile.validate()
    if (profile.executor_adapter_id, profile.executor_adapter_revision) != adapter_binding:
        raise ValueError("Execution Profile targets another ClaudeAdapter binding")
    if profile.transport_kind != "claude_cli" or profile.provider_id != "anthropic":
        raise ValueError("ClaudeAdapter requires the anthropic claude_cli transport")
    if profile.gateway_access_reasons or profile.semantic_input_delivery_mode in {"gateway_read", "hybrid"}:
        raise ValueError("ClaudeAdapter lacks a trusted CLI Gateway/MCP bridge; no automatic SDK fallback")
    if profile.execution_mode not in {"agent", "tool_free"}:
        raise ValueError("ClaudeAdapter supports agent and tool_free execution")
    if profile.semantic_input_delivery_mode != "inline" or profile.network_policy != "denied":
        raise ValueError("ClaudeAdapter requires inline input and denied tool network")
    if profile.attempt_workspace_policy not in {"none", "own_draft_read_write"}:
        raise ValueError("ClaudeAdapter requests an unsupported workspace policy")
    if set(profile.tool_policy) - NATIVE_TOOLS.keys():
        raise ValueError("Claude Profile requests unsupported native tools")
    if profile.reasoning_profile not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("ClaudeAdapter requests an unsupported CLI effort")
    return InvocationExecutionExpectation(
        executor_adapter_id=adapter_binding[0], executor_adapter_revision=adapter_binding[1],
        transport_kind="claude_cli", execution_mode=profile.execution_mode,
        semantic_input_delivery_mode="inline", attempt_workspace_policy=profile.attempt_workspace_policy,
        network_policy="denied", tool_policy=profile.tool_policy,
    )


def _model_identity(value):
    """Accept exact model IDs and the known CLI context-window selector only.

    The response stream reports claude-opus-5 for claude-opus-5[1m]. Do not infer
    a model family from aliases, prefixes, or unrelated auxiliary model usage.
    """
    return value.removesuffix("[1m]") if isinstance(value, str) else None


class ClaudeAdapter:
    """Execute a normalized request through one Claude CLI translation path.

    Agent and tool_free requests independently select an empty tool set or a
    subset of read/search/shell, no model write area or a private draft, and
    prompt_only_json or native_structured_output. Frozen permissions are never
    inferred from task text, model choice or old saved Profile records. Unsupported
    combinations, including Gateway requests without a CLI bridge, fail before
    provider invocation. The canonical output schema is always validated.

    A tool error or permission denial remains a per-call observation. The Agent
    can continue within the same configured capabilities and return a valid final
    response. Initialization outside those capabilities, accurately paired proof
    of an undeclared tool completing, and protected-material changes still
    invalidate the Attempt. Missing or ambiguous tool results remain unknown;
    Shell error text and model summaries never supply an invented policy cause.

    Concrete model IDs and the CLI [1m] selector are supported. Initialization
    and response-model evidence must agree with the Profile; terminal usage for
    that exact model may supply evidence when assistant messages are absent.
    Auxiliary CLI models never stand in for the requested response model.
    Unresolved aliases, missing evidence and mismatches cannot report success.

    Default SIGINT capture spans provider execution, output validation and log
    preparation, serialization and resource cleanup. Cancellation before handoff
    returns the captured Attempt as cancelled, reusing an already committed trace
    unchanged. Logs are not replaced by a bare interruption error. A signal after
    final handoff does not undo that result. Custom handlers are not overridden.
    """

    def __init__(self, *, release_registry: RuntimeReleaseRegistry, artifact_host: ModuleArtifactHost,
                 workspace_root: Path, cli_path: Path | str, read_only_dependencies: tuple[Path, ...] = (),
                 process_runner: Callable = run_cli_process,
                 adapter_binding: tuple[str, str] = _CURRENT_BINDING) -> None:
        """Bind trusted resources and the current executable Adapter identity.

        adapter_binding is claude_cli_adapter@v2. Old Profile records remain
        readable and committed replay is independent of this Adapter, but new
        execution needs an explicit current Profile; no historical pair is
        silently reinterpreted. Agent drafts use scratch as cwd and the write
        root, with ../materials/<name> read-only. The complete prompt is frozen
        before execution, including any Runtime resource description.
        read_only_dependencies are trusted existing paths, never task-supplied
        strings; temporary self-tests require these roots in their exact live binding.
        This constructor resolves paths only. It neither logs in nor calls Claude.

        Args:
            release_registry: Exact definitions and execution Profiles.
            artifact_host: Trusted input/output byte storage.
            workspace_root: Runtime-owned directory for isolated Attempts.
            cli_path: Existing installed Claude executable.
            read_only_dependencies: Explicit trusted read roots; no task discovery.
            process_runner: Runtime process executor or a test-owned double.
            adapter_binding: Exact currently implemented pair.
        Raises:
            ValueError: Unknown adapter identity or invalid descriptor.
            OSError: Executable or a dependency path cannot be resolved.
        Effects:
            Resolves paths only; no provider invocation, registration or login.
        """
        _validate_binding(adapter_binding)
        self.executor_adapter_id, self.executor_adapter_revision = adapter_binding
        self._registry = release_registry
        self._artifacts = artifact_host
        self._workspace_root = Path(workspace_root).resolve()
        self._cli_path = Path(cli_path).resolve(strict=True)
        self._dependencies = tuple(Path(item).resolve(strict=True) for item in read_only_dependencies)
        self._run = process_runner
        self.descriptor = AgentExecutionAdapterDescriptor(
            adapter_contract_version="v1",
            adapter_id=self.executor_adapter_id, adapter_revision=self.executor_adapter_revision,
            provider_id="anthropic", transport_family="cli", transport_kind="claude_cli",
            runtime_package_id="agent_runtime_core", runtime_package_version=runtime_package_version(),
            supported_context_modes=("stateless",), supported_read_isolation_modes=("entitled_refs",),
            supported_output_constraint_modes=("prompt_only_json", "native_structured_output"),
            supported_execution_modes=("agent", "tool_free"),
            supported_input_delivery_modes=("inline",), supported_network_policies=("denied",),
            supports_dynamic_operation_authorization=False, admission_state="integration_tested",
        )
        self.descriptor.validate()

    def build_command(self, *, profile, settings: dict, output_schema: dict | None, local_commands=None) -> list[str]:
        """Render validated fields using Runtime-generated settings and schema.

        Used internally by execute; it is not an execution or permission port.
        Callers execute normalized requests, not arbitrary settings or raw flags.
        Prompt bytes are supplied separately on stdin. Empty tools are explicit.

        Args:
            profile: Exact Profile already chosen for this invocation.
            settings: Internal settings generated from trusted resources by execute.
            output_schema: Mechanically projected native schema, or None for
                prompt_only_json. The full canonical schema remains authoritative.
            local_commands: Trusted LocalCommandSession, or None. It supplies
                one private MCP endpoint; task data cannot supply raw servers.
        Returns:
            Actual argv as separate strings; never a shell command string.
        Raises:
            ValueError: Unsupported fields, exact binding mismatch, or a schema
                inconsistent with the requested output mode.
        Effects:
            Pure rendering; does not authorize tools, run a process or write files.
        """
        _execution_expectation(profile, (self.executor_adapter_id, self.executor_adapter_revision))
        if (output_schema is not None) != (profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT):
            raise ValueError("CLI schema presence differs from Profile output constraint mode")
        tools = ",".join(NATIVE_TOOLS[name] for name in profile.tool_policy)
        if local_commands is not None and type(local_commands) is not LocalCommandSession:
            raise TypeError("local_commands must be the exact Runtime LocalCommandSession")
        mcp_config = {"mcpServers": {}} if local_commands is None else local_commands.mcp_config
        allowed = tools if local_commands is None else tools + "," + LOCAL_COMMAND_CLI_TOOL_NAME
        argv = [str(self._cli_path), "-p", *(["--safe-mode"] if local_commands is None else []), "--restricted", "--disable-slash-commands",
                "--strict-mcp-config", "--mcp-config", json.dumps(mcp_config, separators=(",", ":")), "--setting-sources", "",
                "--no-chrome", "--no-session-persistence", "--model", profile.model_id,
                "--effort", profile.reasoning_profile, "--permission-mode", "auto",
                "--output-format", "stream-json", "--verbose", "--tools", tools, "--allowedTools", allowed,
                "--settings", json.dumps(settings, separators=(",", ":"))]
        if output_schema is not None:
            argv.extend(("--json-schema", json.dumps(output_schema, separators=(",", ":"))))
        for directory in settings.get("permissions", {}).get("additionalDirectories", ()):
            argv.extend(("--add-dir", directory))
        return argv

    def execute(self, request: AuthorizedAgentExecutionRequest,
                host: AuthorizedAgentExecutionHost) -> AgentExecutionResult:
        """Resolve exact releases, enforce fields/resources, execute and retain logs.

        Args:
            request: AuthorizedAgentExecutionRequest with exact Module/Profile,
                Prompt, schema and authorized input references, not raw CLI flags.
            host: Trusted operation authorization or live self-test resource host.
        Returns:
            AgentExecutionResult with validated output or an actual typed failure;
            original streams, observed model, tool facts and limits stay in its
            trace. Execution completion is not a business approval or verdict.
        Raises:
            ValueError: Invalid request, unsupported configuration or identity.
            PermissionError: Missing/expired authorization or live resources.
            Exception: Existing integrity/resource faults before transport begins.
        Effects:
            Stages exact authorized bytes, launches one isolated CLI process and
            commits output/trace via existing ports. Temporary resources are
            cleaned on completion, failure or interruption. No model reselection,
            raw settings passthrough, registration or automatic SDK fallback.
        """
        if type(request) is not AuthorizedAgentExecutionRequest:
            raise ValueError("request must be an exact AuthorizedAgentExecutionRequest")
        request.validate()
        profile = self._registry.get_execution_profile(request.execution_profile_ref, request.execution_profile_sha256)
        expectation = _execution_expectation(profile, (self.executor_adapter_id, self.executor_adapter_revision))
        def validate_self_test(value):
            validator = getattr(host, "validate_self_test_binding", None)
            if not callable(validator):
                raise PermissionError("self-test requires bounded Runtime resources")
            validator(value, adapter=self, artifact_host=self._artifacts, workspace_root=self._workspace_root,
                      read_only_dependencies=self._dependencies)
        prepared = prepare_registered_invocation_context(
            request=request, release_registry=self._registry, artifact_host=self._artifacts,
            expectation=expectation,
            self_test_validator=validate_self_test,
        )
        with _capture_cli_interrupts() as interrupted:
            result, pending_detail, cleanup_error = None, None, None
            try:
                with ExitStack() as cleanup:
                    try:
                        result = self._execute(request, host, prepared, cleanup)
                    except TerminalAdapterFailure as failure:
                        result, pending_detail = failure.result, failure.pending_failure_detail
            except Exception as exc:
                if result is None:
                    raise
                cleanup_error = exc
            # One handoff for both success and failure, after their trace commits
            # and resource cleanup. An interrupted timeout must not allow retry.
            return finalize_adapter_result(artifact_host=self._artifacts, request=request, result=result,
                pending_failure_detail=pending_detail, interruption_requested=lambda: interrupted.requested,
                cleanup_error=cleanup_error, interruption_code="claude_cli_interrupted",
                cleanup_failure_code="claude_cli_cleanup_failed")

    def _execute(self, request, host, prepared, cleanup) -> AgentExecutionResult:
        profile = prepared.profile
        tools = [NATIVE_TOOLS[name] for name in profile.tool_policy]
        result: dict = {}
        trace: dict = {"transport": "claude_cli", "native_tool_events": [], "public_events": [],
                       "model": profile.model_id, "effort": profile.reasoning_profile,
                       "module_run_id": request.module_run_id, "variant_id": request.variant_id,
                       "attempt_id": request.attempt_id}
        policy_refusal: str | None = None
        event_error: str | None = None
        local_commands = None
        resources_body = None

        def usage_fields():
            raw = result.get("usage")
            invalid = raw is not None and type(raw) is not dict
            raw = raw if type(raw) is dict else {}
            values = []
            for key in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                value = raw.get(key)
                if value is not None and (type(value) is not int or value < 0):
                    invalid = True
                    value = None
                values.append(value)
            incoming, outgoing, read, created = values
            total = incoming + read + created if all(value is not None for value in (incoming, read, created)) else None
            return {"input_tokens": total, "output_tokens": outgoing,
                    "cache_read_tokens": read, "cache_creation_tokens": created}, invalid

        def retain_tool_log():
            if "tool_log" in trace:
                return
            try:
                trace["tool_log"] = parse_cli_log(trace)
            except Exception as exc:
                # A failed derived view must never prevent the original streams
                # from reaching the existing private trace commit.
                trace["tool_log"] = {"schema_version": "runtime_cli_log_v1", "complete": False,
                    "issues": ["normalization_failed:" + type(exc).__name__], "tool_calls": None, "events": []}

        def inspect_tool_boundary():
            nonlocal policy_refusal
            retain_tool_log()
            for call in trace["tool_log"].get("provider_tool_calls", trace["tool_log"].get("tool_calls")) or ():
                response = call.get("response")
                if (call.get("source_kind") == "provider_native" and call.get("status") == "completed"
                        and call.get("tool_name") not in expected_tools
                        and len(call.get("request_event_indices", ())) == 1
                        and isinstance(response, dict) and isinstance(response.get("tool_result"), dict)):
                    policy_refusal = policy_refusal or "CLI completed an undeclared tool"

        def fail(failure_class, failure_code, message, *, retry="retry_denied", cause=None, terminal_status="failed"):
            trace["adapter_failure"] = {"failure_class": failure_class, "failure_code": failure_code,
                "message": message, "retry_disposition_id": retry, "terminal_status": terminal_status}
            usage, _ = usage_fields()
            retain_tool_log()
            raise_terminal_failure(
                artifact_host=self._artifacts, request=request, profile=profile,
                failure_class=failure_class, failure_code=failure_code, message=message,
                provider_response=str(result.get("result", "")), retry_disposition_id=retry,
                trace=trace, cause=cause, **usage,
                transport_exit_code=trace.get("exit_code"),
                terminal_status=terminal_status,
                defer_failure_detail=True,
            )

        try:
            native_schema = (claude_native_output_schema(prepared.registered_output_schema)
                             if profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT else None)
        except NativeOutputSchemaProjectionError as exc:
            fail("schema", "native_output_schema_projection_unsupported", str(exc), cause=exc)
        expected_tools = set(tools) | ({"StructuredOutput"} if native_schema is not None else set())

        def observe(line: str) -> bool:
            nonlocal result, policy_refusal, event_error
            if not line.strip():
                return True
            try:
                event = decode_cli_event(line)
            except ValueError as exc:
                event_error = str(exc)
                return False
            kind, subtype = event.get("type"), event.get("subtype")
            if kind == "system" and subtype == "init":
                trace["initialization"] = {key: event.get(key) for key in
                    ("model", "tools", "permissionMode", "skills", "plugins", "slash_commands", "mcp_servers", "claude_code_version")}
                servers = event.get("mcp_servers", [])
                servers_valid = (not servers if local_commands is None else
                    isinstance(servers, list) and len(servers) == 1 and isinstance(servers[0], dict)
                    and servers[0].get("name") == LOCAL_COMMAND_SERVER_NAME and servers[0].get("status") == "connected")
                if set(event.get("tools", [])) != expected_tools or not servers_valid or any(event.get(key) for key in
                    ("skills", "plugins", "slash_commands")) or (
                    event.get("permissionMode") != argv[argv.index("--permission-mode") + 1]
                ):
                    policy_refusal = "CLI initialized capabilities outside the Profile"
            elif kind == "system" and subtype == "permission_denied":
                trace["public_events"].append(event)
            elif kind == "result":
                if result:
                    event_error = "CLI returned multiple terminal results"
                result = event
                trace["result"] = event
            message = event.get("message")
            if isinstance(message, dict):
                if kind == "assistant" and message.get("model"):
                    models = trace.setdefault("response_models", [])
                    if message["model"] not in models:
                        models.append(message["model"])
                for block in message.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        trace["native_tool_events"].append({"type": "tool_use", "id": block.get("id"),
                            "name": block.get("name"), "input": block.get("input")})
                    elif block.get("type") == "tool_result":
                        trace["native_tool_events"].append(block)
            return policy_refusal is None and event_error is None

        stage = "workspace_preparation"
        try:
            attempt = prepare_attempt_workspace(workspace_root=self._workspace_root, attempt_identity={
                "attempt_id": request.attempt_id, "module_run_id": request.module_run_id,
                "variant_id": request.variant_id, "module_release_sha256": prepared.module.release_sha256,
                "execution_profile_sha256": profile.release_sha256,
                "prompt_envelope_sha256": request.prompt_envelope_sha256,
                "execution_authorization_binding_ref": request.execution_authorization_binding_ref,
                "execution_authorization_binding_sha256": request.execution_authorization_binding_sha256,
                **({"self_test_binding_ref": request.self_test_binding_ref,
                    "self_test_binding_sha256": request.self_test_binding_sha256}
                   if request.self_test_binding_ref is not None else {}),
            })
            with lease_attempt_workspace(attempt):
                temporary = cleanup.enter_context(tempfile.TemporaryDirectory(prefix="crt-", dir="/tmp"))
                work = attempt / "work"
                materials = work / "materials"
                scratch = work / "scratch"
                writable_draft = profile.attempt_workspace_policy == "own_draft_read_write"
                for directory in (work, materials, *((scratch,) if writable_draft else ())):
                    if directory.is_symlink():
                        policy_refusal = "CLI workspace directory is a symlink"
                        raise AttemptWorkspaceConflictError(policy_refusal)
                    directory.mkdir(mode=0o700, exist_ok=True)
                cwd = scratch if writable_draft else work
                cli_temporary = Path(temporary).resolve()
                material_hashes = {}
                stage = "material_preparation"
                names = [item.logical_name for item in request.authorized_inputs]
                if len(names) != len(set(names)):
                    raise ValueError("material names must be unique")
                for item in request.authorized_inputs:
                    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}", item.logical_name):
                        raise ValueError("logical_name is not a safe material filename")
                    target = materials / item.logical_name
                    if target.is_symlink():
                        policy_refusal = "material file is a symlink"
                        raise AttemptWorkspaceConflictError(policy_refusal)
                    stage = "authorized_input_read"
                    body = host.read_authorized_input(item.local_handle)
                    stage = "material_preparation"
                    if hashlib.sha256(body).hexdigest() != item.input_sha256:
                        raise ValueError("authorized input hash mismatch")
                    if item.schema_ref == LOCAL_RESOURCES_SCHEMA_REF:
                        if (resources_body is not None or item.schema_sha256 != LOCAL_RESOURCES_SCHEMA_SHA256
                                or item.media_type != LOCAL_RESOURCES_MEDIA_TYPE or item.logical_name != LOCAL_RESOURCES_LOGICAL_NAME):
                            raise ValueError("Local resource control input has invalid metadata")
                        if request.self_test_binding_ref is None:
                            raise PermissionError("Local resources require a live Runtime self-test binding")
                        resource = validate_local_resources(profile=profile, body=body)
                        if tuple(resource["read_only_dependencies"]) != tuple(map(str, self._dependencies)):
                            raise SelfTestResourceUnavailableError("Adapter dependencies differ from the bound local resources")
                        resources_body = body
                        materialize_local_resources(body, materials_root=materials)
                        continue
                    if target.exists() and target.read_bytes() != body:
                        policy_refusal = "existing material content differs"
                        raise AttemptWorkspaceConflictError(policy_refusal)
                    if not target.exists():
                        target.write_bytes(body)
                    material_hashes[str(target)] = item.input_sha256
                private_paths = (cli_temporary, self._workspace_root, Path.home() / ".codex", Path.home() / ".claude",
                                 Path.home() / ".claude.json", Path.home() / "Library/Keychains")
                if any(private.resolve().is_relative_to(dep) or dep.is_relative_to(private.resolve())
                       for private in private_paths for dep in self._dependencies):
                    raise PermissionError("Read-only dependencies overlap private Provider state or credentials")
                if resources_body is not None and parse_local_resources(resources_body)["commands"]:
                    stage = "local_command_preparation"
                    local_commands = cleanup.enter_context(LocalCommandSession(request=request, profile=profile, host=host, adapter=self,
                        artifact_host=self._artifacts, workspace_root=self._workspace_root, resources_body=resources_body,
                        source_root=materials / "source", scratch_root=scratch, read_only_dependencies=self._dependencies))
                    expected_tools.add(LOCAL_COMMAND_CLI_TOOL_NAME)
                settings = {
                    "permissions": {"blockReadsOutsideWorkingDirectories": True,
                        "additionalDirectories": [str(materials), *map(str, self._dependencies)]},
                    "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True, "failIfUnavailable": True,
                        "allowUnsandboxedCommands": False,
                        "filesystem": {"denyRead": ["/"],
                            "allowRead": [str(cwd), str(materials), str(cli_temporary), "/bin", "/usr/bin", "/usr/lib",
                                          "/System", "/Library", "/dev", *map(str, self._dependencies)],
                            "denyWrite": [str(materials), *map(str, self._dependencies),
                                "/tmp/claude", "/private/tmp/claude",
                                str(Path.home() / ".npm/_logs"), str(Path.home() / ".claude")]
                                if writable_draft else ["/"],
                            "allowWrite": [str(cwd), str(cli_temporary)] if writable_draft else []},
                        "network": {"allowedDomains": [], "strictAllowlist": True,
                                    "allowAllUnixSockets": False, "allowLocalBinding": False}},
                }
                if local_commands is not None:
                    settings.update(claudeMdExcludes=["**"], autoMemoryEnabled=False, disableAllHooks=True, enabledPlugins={})
                    settings["sandbox"]["filesystem"]["denyRead"].append(str(local_commands.private_root))
                argv = self.build_command(profile=profile, settings=settings, output_schema=native_schema, local_commands=local_commands)
                stage = "cli_preflight"
                version = subprocess.run([str(self._cli_path), "--version"], capture_output=True,
                                         text=True, check=True, timeout=30).stdout.strip()
                help_text = subprocess.run([str(self._cli_path), "--help"], capture_output=True,
                                           text=True, check=True, timeout=30).stdout
                required = ("--safe-mode", "--restricted", "--tools", "--settings", "--effort", "--strict-mcp-config", "--add-dir")
                if any(flag not in help_text for flag in required) or (native_schema is not None and "--json-schema" not in help_text):
                    raise ValueError("configured Claude CLI lacks a required option")
                environment = {key: value for key, value in os.environ.items() if key in
                    {"HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
                bins = [str(dep / "bin") for dep in self._dependencies if (dep / "bin").is_dir()]
                environment.update(PATH=os.pathsep.join([*bins, "/usr/bin", "/bin", "/usr/sbin", "/sbin"]),
                    TMPDIR=str(cli_temporary), CLAUDE_CODE_TMPDIR=str(cli_temporary),
                    PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                    GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL="/dev/null")
                prompt = prepared.prompt
                trace.update(argv=argv, environment=environment, cwd=str(cwd), cli_version=version, settings=settings,
                             actual_prompt=prompt, material_sha256=material_hashes, timeout_seconds=profile.timeout_seconds,
                             executor_adapter_id=self.executor_adapter_id, executor_adapter_revision=self.executor_adapter_revision,
                             prompt_envelope_sha256=request.prompt_envelope_sha256,
                             execution_profile_ref=profile.release_ref, execution_profile_sha256=profile.release_sha256)
                stage = "provider_invocation"
                command_cleanup_error = None
                try:
                    launch_options = {}
                    if request.self_test_binding_ref is not None:
                        launch_options["launch_guard"] = lambda launch: host.guard_self_test_launch(
                            request, launch, adapter=self, artifact_host=self._artifacts,
                            workspace_root=self._workspace_root, read_only_dependencies=self._dependencies)
                    process = self._run(argv=argv, prompt=prompt, cwd=cwd, environment=environment,
                                        timeout_seconds=profile.timeout_seconds, on_stdout_line=observe,
                                        **launch_options)
                    trace.update(exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr,
                                 process_output_complete=True, **captured_cli_streams(process))
                finally:
                    if local_commands is not None:
                        try:
                            local_commands.close()
                        except Exception as exc:
                            command_cleanup_error = exc
                            trace["local_command_cleanup_error"] = {"error_type": type(exc).__name__, "message": str(exc)}
                        finally:
                            trace["local_command_calls"] = local_commands.records
                            trace["local_command_cli_tool_name"] = LOCAL_COMMAND_CLI_TOOL_NAME
                    if resources_body is not None:
                        try:
                            assert_local_materials_unchanged(resources_body, materials_root=materials)
                        except Exception as exc:
                            policy_refusal = "read-only material tree changed or unavailable"
                            trace["material_integrity_error"] = str(exc)
                    for name, digest in material_hashes.items():
                        target = Path(name)
                        try:
                            intact = (target.resolve() == target and target.is_file()
                                      and hashlib.sha256(target.read_bytes()).hexdigest() == digest)
                        except (OSError, RuntimeError):
                            intact = False
                        if not intact:
                            policy_refusal = "read-only material changed or unavailable"
                if local_commands is not None:
                    stage = "local_command_validation"
                    if command_cleanup_error is not None:
                        raise command_cleanup_error
                    local_commands.validate_completion()
                    stage = "provider_invocation"
        except (Exception, CliProcessInterrupted) as exc:
            trace.update(stage=stage, error=str(exc))
            if isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired, CliProcessInterrupted)):
                trace.update(captured_cli_streams(exc))
                for stream in ("stdout", "stderr"):
                    value = getattr(exc, stream) or ""
                    trace[stream] = value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
                trace["process_output_complete"] = False
                trace["exit_code"] = getattr(exc, "returncode", None)
                if getattr(exc, "stop_reason", None) is not None:
                    trace["stop_reason"] = exc.stop_reason
                if getattr(exc, "prior_stop_reason", None) is not None:
                    trace["prior_stop_reason"] = exc.prior_stop_reason
                if getattr(exc, "cleanup_error", None) is not None:
                    trace["cleanup_error"] = exc.cleanup_error
                if getattr(exc, "stream_error", None) is not None:
                    trace["stream_error"] = exc.stream_error
            if event_error:
                trace["event_error"] = event_error
            inspect_tool_boundary()
            if isinstance(exc, SelfTestResourceUnavailableError):
                fail("authorization", "self_test_resources_unavailable", str(exc), cause=exc)
            if policy_refusal:
                trace["policy_refusal_reason"] = policy_refusal
                fail("policy_violation", "ADAPTER_POLICY_VIOLATION", policy_refusal, cause=exc)
            if isinstance(exc, LocalResourceError):
                fail("dependency_unavailable" if exc.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED" else
                     "policy_violation" if exc.error_code == "ADAPTER_POLICY_VIOLATION" else "schema",
                     exc.error_code, str(exc), cause=exc)
            if stage == "local_command_preparation" and isinstance(exc, NotImplementedError):
                fail("dependency_unavailable", "ADAPTER_CAPABILITY_UNSUPPORTED", str(exc), cause=exc)
            if stage == "local_command_validation":
                fail("unknown", "ADAPTER_CONFORMANCE_FAILED", str(exc), cause=exc)
            if stage == "authorized_input_read":
                raise
            if stage != "provider_invocation":
                cls = "schema" if stage == "material_preparation" and isinstance(exc, ValueError) else "dependency_unavailable"
                fail(cls, "ADAPTER_REQUEST_INVALID" if cls == "schema" else "ADAPTER_BINDING_UNAVAILABLE", str(exc), cause=exc)
            if isinstance(exc, CliProcessInterrupted):
                fail("cancelled", "claude_cli_interrupted", "Claude CLI interrupted by user",
                     terminal_status="cancelled", cause=exc)
            if isinstance(exc, subprocess.TimeoutExpired):
                fail("timeout", "claude_cli_timeout", "Claude CLI exceeded the configured timeout", retry="retry_allowed", cause=exc)
            if event_error and trace.get("stop_reason") in (None, "observer_stopped"):
                fail("provider", "claude_cli_result_missing_or_invalid", event_error, cause=exc)
            fail("transport", "claude_cli_process_failed", str(exc), cause=exc)
        inspect_tool_boundary()
        if policy_refusal:
            trace["policy_refusal_reason"] = policy_refusal
            fail("policy_violation", "ADAPTER_POLICY_VIOLATION", policy_refusal)
        if result and (trace.get("exit_code") != 0 or result.get("is_error") is True):
            diagnostic = str(result.get("result", "")) + " " + str(result.get("errors", []))
            if any(token in diagnostic.lower() for token in ("session limit", "usage limit", "quota exceeded", "credit balance")):
                fail("quota", "provider_quota_exhausted", "Claude reported an exhausted provider quota")
            if result.get("api_error_status") == 401:
                fail("authentication", "claude_cli_authentication_failed", "Claude authentication failed")
            if result.get("api_error_status") == 403:
                fail("authorization", "claude_cli_authorization_failed", "Claude Provider refused this request")
            if result.get("api_error_status") == 429:
                fail("rate_limit", "claude_cli_rate_limited", "Claude reported a rate limit", retry="retry_allowed")
        if "initialization" not in trace or event_error or not result:
            fail("provider", "claude_cli_result_missing_or_invalid", event_error or "CLI initialization or terminal result is missing")
        if trace["exit_code"] != 0 or result.get("is_error") is not False or result.get("subtype") != "success":
            fail("provider", "claude_cli_error_result", "Claude CLI did not complete successfully", retry="retry_allowed")
        usage, invalid_usage = usage_fields()
        if invalid_usage:
            fail("schema", "ADAPTER_OUTPUT_INVALID", "Claude CLI returned invalid usage metadata")
        expected_model = _model_identity(profile.model_id)
        observed_models = trace.get("response_models", [])
        if not observed_models:
            # Some structured-output streams omit assistant messages. Only the
            # exact requested model's terminal usage can supply that evidence;
            # CLI housekeeping models are not the review's response model.
            reported = result.get("modelUsage", {})
            reported = reported.get(profile.model_id) if isinstance(reported, dict) else None
            if (isinstance(reported, dict) and type(reported.get("outputTokens")) is int
                    and reported["outputTokens"] > 0 and isinstance(reported.get("canonicalModel"), str)):
                observed_models = [reported["canonicalModel"]]
        trace["observed_response_models"] = observed_models
        if not observed_models:
            fail("provider", "claude_cli_model_identity_unavailable", "CLI returned no verifiable response model")
        if (_model_identity(trace["initialization"].get("model")) != expected_model
                or any(_model_identity(model) != expected_model for model in observed_models)):
            fail("provider", "claude_cli_model_identity_mismatch",
                 "Observed CLI model differs from the requested Profile")
        try:
            payload = result.get("structured_output")
            if payload is None:
                payload = decode_cli_event(result.get("result", ""))
            if not isinstance(payload, dict):
                raise ValueError("Claude final output must be a JSON object")
            Draft202012Validator(prepared.registered_output_schema).validate(payload)
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (ValueError, TypeError, ValidationError, RecursionError) as exc:
            fail("schema", "ADAPTER_OUTPUT_INVALID", str(exc), cause=exc)
        submission = OutputSubmission(output_slot_id="result", local_handle="output/result.json")
        try:
            host.stage_output_bytes(submission, canonical)
        except PermissionError as exc:
            # Usage and trace have already been observed. Retain them even when
            # the resource boundary prevents the output becoming consumable.
            fail("authorization", "self_test_resources_unavailable" if request.self_test_binding_ref is not None
                 else "output_authorization_refused", str(exc), cause=exc)
        retain_tool_log()
        trace_ref, trace_sha256 = commit_attempt_trace_json(self._artifacts, request, trace)
        return completed_adapter_result(profile=profile, request=request, outputs=(submission,),
            tool_operation_ref_ids=(), trace_ref=trace_ref, trace_sha256=trace_sha256,
            **usage)


__all__ = ["ClaudeAdapter"]
