"""Codex CLI adapters for registered Agent Runtime Modules.

The adapters implement the canonical ``AuthorizedAgentExecutionAdapter``
protocol. Both send one complete, precommitted Prompt Envelope assembled from
authorized inputs. The tool-free adapter consumes the final response directly.
The Agent-workspace adapter gives Codex its standard workspace-write and shell
permissions so it can draft, reread, revise, and validate from an Attempt-local
cwd. That adapter remains a conformance candidate rather than a public-kernel
admission because Codex workspace-write does not confine ambient filesystem
reads to that cwd. Neither adapter gives the provider a database connection or
network-enabled tool execution. Expected provider failures return a typed
failed result; exceptions are adapter conformance failures. Outputs are staged
through the host and become authoritative only through Runtime finalization.
"""

from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import json
import inspect
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Callable

from jsonschema import Draft202012Validator, ValidationError

from ..contracts.invocation_adapter_definition import (
    AgentExecutionAdapterDescriptor,
    AgentExecutionResult,
    AuthorizedAgentExecutionHost,
    AuthorizedAgentExecutionRequest,
    OutputSubmission,
    SelfTestResourceUnavailableError,
)
from ..contracts.registry_release_definition import ExecutionProfileRelease
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from .invocation_tool_definition import ModuleArtifactHost
from .invocation_prompt_assembly import (
    NATIVE_STRUCTURED_OUTPUT,
    normalize_codex_native_output,
)
from .invocation_schema_projection import (
    NativeOutputSchemaProjectionError,
    codex_native_output_schema,
)
from .invocation_context_preparation import (
    InvocationExecutionExpectation,
    prepare_registered_invocation_context,
)
from .invocation_result_assembly import (
    TerminalAdapterFailure,
    bounded_trace_text,
    commit_attempt_trace_json,
    completed_adapter_result,
    finalize_adapter_result,
    provider_adapter_descriptor,
    raise_terminal_failure,
)
from .invocation_cli_logging import (
    captured_cli_streams, cli_stream_bytes, decode_cli_event, parse_cli_log,
)
from .invocation_process_execution import (
    CliProcessError, CliProcessInterrupted, _capture_cli_interrupts, run_cli_process,
)
from .invocation_codex_environment import prepare_codex_environment
from .invocation_workspace_preparation import (
    AttemptWorkspaceConflictError,
    lease_attempt_workspace,
    prepare_attempt_workspace,
)


_APP_BUNDLE_BIN = "/Applications/Codex.app/Contents/Resources/codex"


@dataclass(frozen=True)
class CodexCliInvocationResult:
    """Captured process facts; three-field injected results remain text-only.

    Byte fields contain the observed streams, not a redacted display. A normal
    return can report incomplete capture but cannot thereby authorize output.
    Capture exceptions retain their own partial streams and stop reason.
    """

    returncode: int
    stdout: str
    stderr: str
    stdout_bytes: bytes | None = None
    stderr_bytes: bytes | None = None
    process_output_complete: bool = True
    cli_version: str | None = None


CodexCliInvoker = Callable[..., CodexCliInvocationResult]


def _resolve_codex_bin() -> str:
    explicit = os.environ.get("CODEX_CLI_BIN", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    if Path(_APP_BUNDLE_BIN).is_file():
        return _APP_BUNDLE_BIN
    raise RuntimeError("Codex CLI is not installed or visible on PATH")


def _default_invoke(
    *,
    argv: list[str],
    prompt: str,
    cwd: Path,
    timeout_seconds: int,
    environment: dict[str, str],
    launch_guard: Callable | None = None,
) -> CodexCliInvocationResult:
    """Run v4 with explicit private environment and a launch-time resource guard.

    The same executable supplies its version without a model/authentication call.
    Injected invokers must accept environment and optional launch_guard; there is
    no fallback to the historical four-argument execution contract.
    """
    version = run_cli_process(argv=[argv[0], "--version"], prompt="", cwd=cwd,
        timeout_seconds=min(timeout_seconds, 10), environment=environment,
        max_output_bytes=4096, launch_guard=launch_guard)
    if version.returncode != 0 or not version.stdout.strip():
        raise OSError("Codex CLI version check failed")
    try:
        process = run_cli_process(
            argv=argv, prompt=prompt, cwd=cwd, timeout_seconds=timeout_seconds,
            environment=environment, launch_guard=launch_guard,
        )
    except (Exception, CliProcessInterrupted) as exc:
        exc.cli_version = version.stdout.strip()
        raise
    return CodexCliInvocationResult(
        returncode=process.returncode,
        stdout=process.stdout or "",
        stderr=process.stderr or "",
        stdout_bytes=process.stdout_bytes,
        stderr_bytes=process.stderr_bytes,
        cli_version=version.stdout.strip(),
    )


def _execution_expectation(profile: ExecutionProfileRelease) -> InvocationExecutionExpectation:
    """Validate the currently executable Codex v4 combination without resources.

    Historical releases remain readable but must be explicitly prepared with the
    new binding before execution. This check never reads credentials or programs.
    """
    profile.validate()
    expected = ("codex_cli_agent_executor", "v4", "codex_cli", "openai", "tool_free",
                "inline", "none", (), "denied", ())
    actual = (profile.executor_adapter_id, profile.executor_adapter_revision, profile.transport_kind,
              profile.provider_id, profile.execution_mode, profile.semantic_input_delivery_mode,
              profile.attempt_workspace_policy, profile.tool_policy, profile.network_policy,
              profile.gateway_access_reasons)
    if actual != expected:
        raise ValueError("Codex v4 supports only tool_free, inline, workspace none, empty tools and denied network; prepare an explicit compatible binding")
    return InvocationExecutionExpectation("codex_cli_agent_executor", "v4", "codex_cli",
                                         "tool_free", "inline", "none", "denied", ())


def build_command(*, profile: ExecutionProfileRelease, workspace: Path, codex_bin: str,
                  schema_path: Path | None = None) -> list[str]:
    """Render the actual v4 command for execution and context probes, without I/O.

    Model and effort come only from Profile. Task data cannot supply raw flags,
    settings, credentials or permission overrides. The executable locator is a
    host input; program existence and authentication are checked only at execution.

    Args:
        profile: Exact v4 Profile; supplies the concrete model and reasoning effort.
        workspace: Prepared Attempt cwd, not an additional filesystem read grant.
        codex_bin: Host-selected CLI executable locator.
        schema_path: Optional prepared native schema file; execute supplies it
            when native structured output is selected. Context probes may omit it.
    Returns:
        A fresh argv list used directly for one codex exec invocation.
    Raises:
        ValueError: Profile targets another binding or unsupported capability set.
    Effects:
        None. No file reads, credential resolution, process or environment changes.
    """
    _execution_expectation(profile)
    options = ["project_doc_max_bytes=0", 'approval_policy="never"',
        'cli_auth_credentials_store="file"', "agents.enabled=false", "skills.include_instructions=false",
        'features.code_mode.excluded_tool_namespaces=["functions","collaboration","clock"]',
        "mcp_servers={}", "features.skip_host_skill_discovery=true", 'web_search="disabled"']
    options.extend("features." + name + "=false" for name in (
        "shell_tool", "multi_agent", "plugins", "plugin_sharing", "apps", "browser_use",
        "computer_use", "skill_search", "tool_suggest", "memories", "view_image",
        "image_generation", "hooks", "workspace_dependencies",
    ))
    argv = [str(codex_bin), "exec", "-m", profile.model_id,
            "-c", "model_reasoning_effort=" + json.dumps(profile.reasoning_profile)]
    for option in options:
        argv.extend(("-c", option))
    argv.extend(("-C", str(workspace), "-s", "read-only", "--skip-git-repo-check",
                 "--ignore-user-config", "--ignore-rules", "--strict-config", "--ephemeral", "--json"))
    if schema_path is not None:
        argv.extend(("--output-schema", str(schema_path)))
    argv.append("-")
    return argv


def _parse_usage(events: list[dict]) -> tuple[dict, list[str]]:
    """Read one reported turn's counters without coercion or inferred totals."""
    aliases = {
        "input_tokens": ("input_tokens",), "output_tokens": ("output_tokens",),
        "cache_read_tokens": ("cached_input_tokens", "cache_read_tokens"),
        "cache_creation_tokens": ("cache_write_input_tokens", "cache_creation_tokens"),
    }
    values = dict.fromkeys(aliases)
    rows = [event["usage"] for event in events
            if event.get("type") in {"turn.completed", "turn.failed"} and event.get("usage") is not None]
    if not rows:
        return values, []
    if len(rows) != 1 or type(rows[0]) is not dict:
        return values, ["ambiguous_or_invalid_usage"]
    issues = []
    for target, names in aliases.items():
        supplied = [rows[0][name] for name in names if rows[0].get(name) is not None]
        if any(type(value) is not int or value < 0 for value in supplied) or len(set(supplied)) > 1:
            issues.append("invalid_usage:" + target)
        elif supplied:
            values[target] = supplied[0]
    return values, issues


def _parse_codex_stream(raw: bytes) -> dict:
    """Interpret the public one-shot turn; item errors are not stream errors.

    thread/turn start markers may be absent in legacy injected streams. When
    present they must unambiguously precede this turn's items and terminal.
    All malformed bytes and contradictory events remain in the private trace.
    """
    events, issues, terminals, fatal = [], [], [], []
    thread_started = turn_started = seen_item = False
    final_text = None
    for index, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        try:
            event = decode_cli_event(line.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            issues.append(f"invalid_event:{index}:{type(exc).__name__}")
            continue
        events.append(event)
        kind = event.get("type")
        if kind == "thread.started":
            if thread_started or turn_started or seen_item or terminals:
                issues.append(f"unexpected_thread_start:{index}")
            thread_started = True
        elif kind == "turn.started":
            if turn_started or seen_item or terminals:
                issues.append(f"unexpected_turn_start:{index}")
            turn_started = True
        elif kind in {"turn.completed", "turn.failed"}:
            terminals.append(event)
            if kind == "turn.failed" and not isinstance(event.get("error"), dict):
                issues.append(f"invalid_failure_terminal:{index}")
        elif kind == "error":
            # This is a fatal stream event, unlike item.details.type == error.
            fatal.append(event)
        elif isinstance(kind, str) and kind.startswith("item."):
            item = event.get("item")
            if not isinstance(item, dict):
                issues.append(f"invalid_item:{index}")
            elif item.get("type") != "error":
                # Public nonfatal diagnostic items can precede turn.started.
                # They are not task activity and cannot establish a turn's
                # execution order; retain them without interpreting their prose.
                seen_item = True
                if terminals:
                    issues.append(f"item_after_terminal:{index}")
                if kind == "item.completed" and item.get("type") == "agent_message":
                    final_text = item.get("text")
    if len(terminals) != 1:
        issues.append("terminal_event_missing_or_duplicated")
    usage, usage_issues = _parse_usage(events)
    return {"terminal": terminals[0] if len(terminals) == 1 else None,
            "fatal_errors": fatal, "issues": issues, "final_text": final_text,
            "usage": usage, "usage_issues": usage_issues}


def _public_stdout(text: str) -> str:
    """Exclude reasoning events before persisting public CLI diagnostics.

    JSONL is delimited by LF, not Unicode line separators inside JSON strings.
    Non-JSON process diagnostics are preserved, not guessed to be reasoning.
    """
    rows = []
    for line in text.split("\n"):
        try:
            event = json.loads(line)
        except (ValueError, TypeError, RecursionError):
            rows.append(line)
            continue
        if isinstance(event, dict):
            item = event.get("item")
            if "reasoning" in str(event.get("type", "")) or (
                isinstance(item, dict) and "reasoning" in str(item.get("type", ""))
            ):
                continue
        rows.append(line)
    return "\n".join(rows)


def _undeclared_tool_effects(tool_log: dict) -> list[dict]:
    """Find positive execution evidence in unambiguous native tool records.

    Used only for the admitted empty-tools configuration. A denied request or
    an unknown result is not evidence of execution. These observations never
    create Runtime operation grants or infer resource effects from prose.
    """
    events = {row["index"]: row["event"] for row in tool_log.get("events", [])}
    effects = []
    for call in tool_log.get("tool_calls") or []:
        if call.get("source_kind") != "provider_native" or call.get("status") == "incomplete":
            continue
        for index in call.get("response_event_indices", []):
            event = events.get(index, {})
            item = event.get("item", {})
            if event.get("type") != "item.completed" or not isinstance(item, dict):
                continue
            kind = item.get("type")
            if item.get("id") != call.get("tool_call_id"):
                continue
            if kind == "command_execution":
                executed = item.get("status") != "declined" and type(item.get("exit_code")) is int
            elif kind == "mcp_tool_call":
                executed = (call.get("status") == "completed" and item.get("status") == "completed"
                            and item.get("error") is None and item.get("tool") == call.get("tool_name"))
            elif kind == "file_change":
                executed = item.get("status") == "completed"
            elif kind == "web_search":
                executed = call.get("status") == "completed"
            else:
                executed = False
            if executed:
                effects.append({"tool_call_id": call["tool_call_id"], "tool_kind": kind,
                                "response_event_index": index, "failure_code": "ADAPTER_POLICY_VIOLATION"})
    return effects


class _CodexCliExecutorBase:
    """Shared exact-envelope execution for one admitted Codex CLI mode."""

    executor_adapter_id = ""
    executor_adapter_revision = ""
    expected_execution_mode = "tool_free"
    expected_semantic_input_delivery_mode = "inline"
    expected_attempt_workspace_policy = "none"
    expected_tool_policy: tuple[str, ...] = ()
    expected_network_policy = "denied"
    shell_tool_enabled = False
    sandbox_mode = "read-only"
    descriptor_admission_state = "integration_tested"

    def __init__(
        self,
        *,
        release_registry: RuntimeReleaseRegistry,
        artifact_host: ModuleArtifactHost,
        workspace_root: Path,
        invoker: CodexCliInvoker = _default_invoke,
        codex_bin: str | None = None,
        auth_file: Path | None = None,
    ) -> None:
        """Configure the admitted Codex implementation without preparing resources.

        Args:
            release_registry: Exact registered definitions used by invocation checks.
            artifact_host: Request-owned content/trace store implementing read_bytes,
                commit_failure_detail and commit_attempt_trace. This does not select
                or connect to a persistent database.
            workspace_root: Host-owned Attempt workspace and cleanup tree. Provider
                private state is created separately, outside this tree, at execution.
            invoker: Trusted v4 callable accepting keyword argv, prompt, cwd,
                timeout_seconds, environment and optional launch_guard. It must pass
                the explicit environment and launch guard to actual process creation.
                The default uses run_cli_process. An old four-argument callable is
                rejected before resource preparation; no unguarded fallback is tried.
            codex_bin: Optional explicit installed CLI. None uses the existing host
                executable resolver at invocation; this constructor probes no program.
            auth_file: Optional single regular file authentication source. None uses
                the host's original CODEX_HOME/auth.json, or standard .codex/auth.json
                when CODEX_HOME is unset. Resolution occurs only during invocation.
                Missing file authentication does not fall back to keychain/API keys.
        Raises:
            ValueError: Required artifact methods or descriptor fields are invalid.
            OSError: The workspace locator cannot be resolved by the host filesystem.
        Effects:
            Stores resource locators and builds the descriptor. Does not create a
            directory, open credentials, call a CLI/model, or register any release.
            During execution only CLI reads or refreshes credentials. If it replaces
            the private authentication reference, state is retained for host recovery
            and the invocation returns a trace-bound cleanup failure, not approval.
        """
        required_artifact_methods = (
            "read_bytes",
            "commit_failure_detail",
            "commit_attempt_trace",
        )
        if any(
            not callable(getattr(artifact_host, method_name, None))
            for method_name in required_artifact_methods
        ):
            raise ValueError("artifact_host must implement the Module artifact boundary")
        self._release_registry = release_registry
        self._artifact_host = artifact_host
        self._workspace_root = workspace_root.resolve()
        self._invoker = invoker
        self._codex_bin = codex_bin
        self._auth_file = auth_file
        self._descriptor = provider_adapter_descriptor(
            adapter_id=self.executor_adapter_id,
            adapter_revision=self.executor_adapter_revision,
            provider_id="openai",
            transport_family="cli",
            transport_kind="codex_cli",
            execution_mode=self.expected_execution_mode,
            input_delivery_mode=self.expected_semantic_input_delivery_mode,
            network_policy=self.expected_network_policy,
            admission_state=self.descriptor_admission_state,
        )

    @property
    def descriptor(self) -> AgentExecutionAdapterDescriptor:
        """Return immutable canonical adapter admission metadata."""

        return self._descriptor

    def execute(
        self,
        request: AuthorizedAgentExecutionRequest,
        host: AuthorizedAgentExecutionHost,
    ) -> AgentExecutionResult:
        """Execute one v4 invocation with live resources and private Provider state.

        A self-test requires the kernel's live host validator and actual launch
        guard. Missing hooks or an incompatible injected invoker fail before CLI
        effects. Credential replacement retains private state and reports cleanup
        failure; no credentials enter the prompt, Profile or command arguments.

        Args:
            request: Exact registered request with frozen inputs and either external
                operation evidence or the existing live self-test resource binding.
            host: Trusted request-bound Runtime host for staging outputs and, for
                self-tests, live binding validation and launch-time resource checking.
        Returns:
            AgentExecutionResult with a schema-valid output or typed failed/cancelled
            status. Business verdicts remain Module-owned. Actual CLI version, captured
            streams and normalized tool observations stay in the private Attempt trace.
        Raises:
            ValueError: Invalid request/Profile or incompatible injected invoker.
            PermissionError: Missing live resource/operation evidence or unsupported
                workspace execution. Provider is not started on these entry failures.
            Exception: Native content/record-store integrity failures retain their owner.
        Effects:
            Resolves one authentication file, prepares private state and one Attempt,
            launches the selected CLI with fixed v4 configuration, and stages output
            and trace through the supplied ports. No login, registration, model fallback,
            credential copy-back or global environment modification is performed.
        """
        if self.descriptor.admission_state == "conformance_candidate":
            raise PermissionError("Codex workspace candidate lacks required ambient-read isolation")
        if type(request) is not AuthorizedAgentExecutionRequest:
            raise ValueError("request must be an exact AuthorizedAgentExecutionRequest")
        request.validate()
        profile = self._release_registry.get_execution_profile(request.execution_profile_ref,
                                                              request.execution_profile_sha256)
        expectation = _execution_expectation(profile)
        def validate_self_test(value):
            validator = getattr(host, "validate_self_test_binding", None)
            guard = getattr(host, "guard_self_test_launch", None)
            if not callable(validator) or not callable(guard):
                raise SelfTestResourceUnavailableError("Codex self-test requires live Runtime resource validation and launch guard")
            validator(value, adapter=self, artifact_host=self._artifact_host, workspace_root=self._workspace_root)
        prepared = prepare_registered_invocation_context(
            request=request,
            release_registry=self._release_registry,
            artifact_host=self._artifact_host,
            expectation=expectation, self_test_validator=validate_self_test,
        )
        if self.shell_tool_enabled != ("shell" in prepared.profile.tool_policy):
            raise PermissionError("Codex actual Shell capability differs from the explicit Profile tool_policy")
        try:
            inspect.signature(self._invoker).bind(argv=[], prompt="", cwd=self._workspace_root,
                timeout_seconds=profile.timeout_seconds, environment={}, launch_guard=None)
        except (TypeError, ValueError) as exc:
            raise ValueError("Codex v4 invoker must accept explicit environment and optional launch_guard") from exc
        with _capture_cli_interrupts() as interrupted:
            result, pending_detail, cleanup_error = None, None, None
            try:
                with ExitStack() as cleanup:
                    try:
                        result = self._execute_prepared(request, host, prepared, cleanup)
                    except TerminalAdapterFailure as failure:
                        result, pending_detail = failure.result, failure.pending_failure_detail
            except Exception as exc:
                if result is None:
                    raise
                cleanup_error = exc
            return finalize_adapter_result(
                artifact_host=self._artifact_host, request=request, result=result,
                pending_failure_detail=pending_detail,
                interruption_requested=lambda: interrupted.requested,
                cleanup_error=cleanup_error, interruption_code="codex_cli_interrupted",
                cleanup_failure_code="codex_cli_cleanup_failed",
            )

    def _execute_prepared(
        self,
        request: AuthorizedAgentExecutionRequest,
        host: AuthorizedAgentExecutionHost,
        prepared,
        cleanup: ExitStack,
    ) -> AgentExecutionResult:
        module = prepared.module
        profile = prepared.profile
        registered_output_schema = prepared.registered_output_schema
        trace = {
            "transport": "codex_cli", "module_run_id": request.module_run_id,
            "variant_id": request.variant_id, "attempt_id": request.attempt_id,
            "model": profile.model_id, "effort": profile.reasoning_profile,
            "executor_adapter_id": self.executor_adapter_id,
            "executor_adapter_revision": self.executor_adapter_revision,
            "execution_profile_ref": profile.release_ref,
            "execution_profile_sha256": profile.release_sha256,
            "timeout_seconds": profile.timeout_seconds,
        }
        parsed = _parse_codex_stream(b"")

        def retain_tool_log():
            if "tool_log" in trace:
                return
            try:
                trace["tool_log"] = parse_cli_log(trace)
                if profile.execution_mode == "tool_free" and not profile.tool_policy:
                    trace["undeclared_tool_effects"] = _undeclared_tool_effects(trace["tool_log"])
            except Exception as exc:
                trace["tool_log"] = {
                    "schema_version": "runtime_cli_log_v1", "complete": False,
                    "issues": ["normalization_failed:" + type(exc).__name__],
                    "events": [], "tool_calls": None,
                }

        def capture(process, *, complete):
            nonlocal parsed
            trace.update(captured_cli_streams(process))
            trace["process_output_complete"] = complete
            code = getattr(process, "returncode", None)
            trace["returncode"] = code if type(code) is int else None
            for name in ("stop_reason", "prior_stop_reason", "cleanup_error", "stream_error", "cli_version"):
                if getattr(process, name, None) is not None:
                    trace[name] = getattr(process, name)
            for stream in ("stdout", "stderr"):
                value = cli_stream_bytes(trace, stream).decode("utf-8", errors="replace")
                trace[stream] = bounded_trace_text(_public_stdout(value))
            trace["display_redacted"] = True
            parsed = _parse_codex_stream(cli_stream_bytes(trace, "stdout"))
            trace["provider_terminal"] = parsed["terminal"]
            trace["provider_stream_issues"] = parsed["issues"]
            trace["provider_fatal_errors"] = parsed["fatal_errors"]
            trace["usage_issues"] = parsed["usage_issues"]
            retain_tool_log()

        def fail(failure_class, failure_code, message, *, retry="retry_denied", cause=None,
                 terminal_status="failed"):
            retain_tool_log()
            if trace.get("undeclared_tool_effects"):
                retry = "retry_denied"
            trace["adapter_failure"] = {
                "failure_class": failure_class, "failure_code": failure_code,
                "message": message, "retry_disposition_id": retry,
                "terminal_status": terminal_status,
            }
            raise_terminal_failure(
                artifact_host=self._artifact_host, request=request, profile=profile,
                failure_class=failure_class, failure_code=failure_code, message=message,
                provider_response="stdout:\n" + trace.get("stdout", "") + "\nstderr:\n" + trace.get("stderr", ""),
                retry_disposition_id=retry, trace=trace, cause=cause,
                transport_exit_code=trace.get("returncode"), terminal_status=terminal_status,
                defer_failure_detail=True, **parsed["usage"],
            )

        projected_output_schema = None
        if profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT:
            try:
                projected_output_schema = codex_native_output_schema(
                    registered_output_schema
                )
            except NativeOutputSchemaProjectionError as exc:
                trace["stage"] = "native_output_schema_projection"
                fail("schema", "native_output_schema_projection_unsupported", str(exc), cause=exc)

        try:
            workspace = prepare_attempt_workspace(
                workspace_root=self._workspace_root,
                attempt_identity={
                    "attempt_id": request.attempt_id,
                    "module_run_id": request.module_run_id,
                    "variant_id": request.variant_id,
                    "module_release_sha256": module.release_sha256,
                    "execution_profile_sha256": profile.release_sha256,
                    "prompt_envelope_sha256": request.prompt_envelope_sha256,
                },
            )
        except (OSError, AttemptWorkspaceConflictError) as exc:
            trace["stage"] = "workspace_preparation"
            fail("dependency_unavailable", "codex_attempt_workspace_unavailable",
                 "Codex Attempt workspace could not be prepared", cause=exc)
        prompt = prepared.prompt
        try:
            executable = self._codex_bin or _resolve_codex_bin()
        except (OSError, RuntimeError) as exc:
            trace["stage"] = "executable_resolution"
            fail("dependency_unavailable", "ADAPTER_BINDING_UNAVAILABLE", str(exc), cause=exc)
        stage = "workspace_lease"
        try:
            cleanup.enter_context(lease_attempt_workspace(workspace))
            schema_path = None
            if profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT:
                stage = "native_schema_file"
                schema_directory = Path(cleanup.enter_context(tempfile.TemporaryDirectory(
                    prefix=f".{request.attempt_id}_schema_", dir=self._workspace_root,
                )))
                schema_path = schema_directory / "output_schema.json"
                schema_path.write_text(json.dumps(projected_output_schema, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")), encoding="utf-8")
            argv = build_command(profile=profile, workspace=workspace, codex_bin=executable,
                                 schema_path=schema_path)
            stage = "provider_environment"
            environment = cleanup.enter_context(prepare_codex_environment(
                workspace_root=self._workspace_root, auth_file=self._auth_file,
                source_environment=dict(os.environ)))
            trace.update(argv=list(argv), cwd=str(workspace),
                         prompt_envelope_sha256=request.prompt_envelope_sha256,
                         environment_keys=sorted(environment), provider_state_isolated=True)
            launch_guard = None
            if request.self_test_binding_ref is not None:
                launch_guard = lambda launch: host.guard_self_test_launch(
                    request, launch, adapter=self, artifact_host=self._artifact_host,
                    workspace_root=self._workspace_root)
            stage = "provider_invocation"
            result = self._invoker(argv=argv, prompt=prompt, cwd=workspace,
                                   timeout_seconds=profile.timeout_seconds, environment=environment,
                                   launch_guard=launch_guard)
            if type(result) is not CodexCliInvocationResult:
                raise TypeError("Codex CLI invoker returned an invalid result")
            capture(result, complete=result.process_output_complete is True)
            trace["cli_version"] = result.cli_version
            if type(result.returncode) is not int or type(result.process_output_complete) is not bool:
                raise TypeError("Codex CLI invoker returned invalid process metadata")
        except (Exception, KeyboardInterrupt) as exc:
            trace.update(stage=stage, error=str(exc))
            if getattr(exc, "cli_version", None) is not None:
                trace["cli_version"] = exc.cli_version
            if isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired, CliProcessInterrupted)) or any(
                getattr(exc, name, None) is not None for name in ("stdout", "stderr", "stdout_bytes", "stderr_bytes")
            ):
                capture(exc, complete=False)
            if isinstance(exc, KeyboardInterrupt):
                fail("cancelled", "codex_cli_interrupted", "Codex CLI interrupted",
                     terminal_status="cancelled", cause=exc)
            if isinstance(exc, SelfTestResourceUnavailableError):
                fail("authorization", "self_test_resources_unavailable", str(exc), cause=exc)
            if isinstance(exc, subprocess.TimeoutExpired):
                fail("timeout", "codex_cli_timeout", "Codex CLI invocation timed out",
                     retry="retry_allowed", cause=exc)
            if isinstance(exc, CliProcessError):
                fail("transport", "codex_cli_process_failed", str(exc), cause=exc)
            if isinstance(exc, subprocess.CalledProcessError):
                fail("provider", "codex_cli_nonzero_exit", "Codex CLI process reported a nonzero exit",
                     retry="retry_allowed", cause=exc)
            if isinstance(exc, AttemptWorkspaceConflictError):
                fail("dependency_unavailable", "codex_attempt_workspace_unavailable",
                     "Codex Attempt workspace is already leased", cause=exc)
            if stage != "provider_invocation" or isinstance(exc, OSError):
                fail("dependency_unavailable", "ADAPTER_BINDING_UNAVAILABLE", str(exc), cause=exc)
            fail("unknown", "ADAPTER_CONFORMANCE_FAILED", "Codex invoker violated its result contract", cause=exc)
        if not result.process_output_complete:
            fail("transport", "codex_cli_process_failed", "Codex process output capture is incomplete")
        if result.returncode != 0:
            fail("provider", "codex_cli_nonzero_exit", f"Codex CLI failed with return code {result.returncode}",
                 retry="retry_allowed")
        if parsed["fatal_errors"]:
            fail("provider", "codex_cli_error_result", "Codex emitted a fatal stream error", retry="retry_allowed")
        if parsed["issues"]:
            fail("provider", "codex_cli_result_missing_or_invalid", "; ".join(parsed["issues"]))
        if parsed["terminal"]["type"] == "turn.failed":
            fail("provider", "codex_cli_error_result", "Codex reported a failed turn", retry="retry_allowed")
        if trace.get("undeclared_tool_effects"):
            fail("policy_violation", "ADAPTER_POLICY_VIOLATION", "Codex executed a tool under an empty-tools Profile")
        if parsed["usage_issues"]:
            fail("schema", "codex_cli_usage_invalid", "; ".join(parsed["usage_issues"]))
        try:
            canonical_payload = decode_cli_event(parsed["final_text"])
        except (ValueError, TypeError, UnicodeError) as exc:
            fail("schema", "codex_cli_output_json_invalid", "Codex final message must be one valid JSON object",
                 retry="retry_allowed", cause=exc)
        try:
            if profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT:
                canonical_payload = normalize_codex_native_output(
                    payload=canonical_payload, canonical_schema=registered_output_schema,
                )
            Draft202012Validator(registered_output_schema).validate(canonical_payload)
            canonical_output = json.dumps(
                canonical_payload, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        except ValidationError as exc:
            fail("schema", "codex_cli_output_schema_violation", str(exc), retry="retry_allowed", cause=exc)
        except Exception as exc:
            fail("unknown", "ADAPTER_CONFORMANCE_FAILED", "Codex output normalization failed", cause=exc)
        submission = OutputSubmission(
            output_slot_id="result",
            local_handle="output/result.json",
        )
        try:
            host.stage_output_bytes(submission, canonical_output)
        except PermissionError as exc:
            fail("authorization", "self_test_resources_unavailable" if isinstance(exc, SelfTestResourceUnavailableError)
                 else "output_authorization_refused", str(exc), cause=exc)
        except Exception as exc:
            fail("unknown", "ADAPTER_CONFORMANCE_FAILED", "Codex output could not be staged", cause=exc)
        retain_tool_log()
        trace_ref, trace_sha256 = commit_attempt_trace_json(
            self._artifact_host, request, trace
        )
        return completed_adapter_result(
            profile=profile,
            request=request,
            outputs=(submission,),
            tool_operation_ref_ids=(),
            trace_ref=trace_ref,
            trace_sha256=trace_sha256,
            **parsed["usage"],
        )


class CodexCliModuleExecutor(_CodexCliExecutorBase):
    """Execute an isolated tool-free Module using Codex CLI file authentication.

    v4 has explicit private-state/environment preparation and a launch guard.
    v3 releases are readable historical definitions, not executable aliases.
    """

    executor_adapter_id = "codex_cli_agent_executor"
    executor_adapter_revision = "v4"


class CodexCliAgentWorkspaceModuleExecutor(_CodexCliExecutorBase):
    """Unadmitted workspace candidate; refuses execution until reads are confined.

    The historical v2 Profile and descriptor identity remain readable. This
    implementation cannot limit ambient reads and does not grant a usable public
    execution path. Both direct execute and the kernel fail before workspace or
    provider effects; selecting an empty tool_policy cannot implicitly enable Shell.
    """

    executor_adapter_id = "codex_cli_agent_workspace_executor"
    executor_adapter_revision = "v2"
    descriptor_admission_state = "conformance_candidate"
    expected_execution_mode = "agent"
    expected_attempt_workspace_policy = "own_draft_read_write"
    expected_tool_policy = ()
    shell_tool_enabled = True
    sandbox_mode = "workspace-write"


__all__ = [
    "build_command",
    "CodexCliInvocationResult",
    "CodexCliInvoker",
    "CodexCliAgentWorkspaceModuleExecutor",
    "CodexCliModuleExecutor",
    "ModuleArtifactHost",
]
