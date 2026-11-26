"""Run the two admitted Runtime capability shapes through the native Antigravity CLI (agy).

First binding, two shapes only. structured_output: tool-free, inline input, no
model workspace, native structured output. file_review: agent with read, search,
edit and shell in a private draft, provider_managed tool network. Every other
combination (explicit project, MCP, callbacks, recorded commands, prompt-only
JSON, Gateway input) is refused before a model starts.

Runtime prepares the Attempt and three per-call files, then starts agy once:
a custom primary agent in ``<cwd>/.agents/agents/`` that lists the native tools
(``finish`` is the CLI's structured-result mechanism, not a task tool), a
session-only settings file passed with ``--settings`` (never written to the
global settings) and the canonical output schema passed with ``--json-schema``.
stdin carries exactly one stream-json user event, then EOF. agy plans and runs
its own tools under that configuration; Runtime neither executes nor retries
them. The terminal ``result.structured_output`` is the only accepted output and
is validated against the full canonical schema; response text is never parsed.
Login stays native: the CLI uses the host's existing sign-in.
"""

from __future__ import annotations

from .invocation_host_platform_selection import HOST_PLATFORM, private_temporary_directory

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable

from jsonschema import Draft202012Validator, ValidationError

from ..contracts.invocation_adapter_definition import (
    AgentExecutionAdapterDescriptor, AgentExecutionResult, AuthorizedAgentExecutionHost,
    AuthorizedAgentExecutionRequest, OutputSubmission, SelfTestResourceUnavailableError,
)
from ..foundation.foundation_json_encoding import cli_stream_bytes, decode_cli_event
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from .invocation_cli_logging import captured_cli_streams
from .invocation_context_preparation import InvocationExecutionExpectation, prepare_registered_invocation_context
from .invocation_local_resource_preparation import (
    LocalResourceError, assert_local_materials_unchanged, compose_invocation_prompt, invocation_locations,
    parse_local_resources, stage_attempt_materials,
)
from .invocation_process_execution import (
    CliProcessInterrupted, CliProcessStartupError, _capture_cli_interrupts, _runtime_python_executable,
    cli_process_stop_confirmed, invocation_budget_expired, invocation_deadline, run_cli_process,
)
from .invocation_prompt_assembly import NATIVE_STRUCTURED_OUTPUT
from .invocation_result_assembly import (
    TerminalAdapterFailure, bounded_trace_text, commit_attempt_trace_json, completed_adapter_result,
    finalize_adapter_result, raise_terminal_failure,
)
from .invocation_tool_definition import ModuleArtifactHost, runtime_package_version
from .invocation_workspace_preparation import (
    AttemptWorkspaceConflictError, lease_attempt_workspace, prepare_attempt_workspace,
)


TRANSPORT_KIND = "antigravity_cli"
PROVIDER_ID = "google"
CURRENT_BINDING = ("antigravity_cli_adapter", "v1")
# The generated custom primary agent; a fixed descriptive name, not an identity.
AGENT_NAME = "agent-runtime-task"
# Native tools of each admitted shape. finish delivers the schema result.
TOOL_FREE_NATIVE_TOOLS = ("finish",)
FILE_REVIEW_NATIVE_TOOLS = ("view_file", "write_to_file", "replace_file_content", "run_command",
                            "command_status", "finish")
FILE_REVIEW_TOOL_POLICY = frozenset({"edit", "read", "search", "shell"})
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_PERMIT = re.compile(r"^[^\r\n\x00]+$")
_PREFLIGHT_LIMIT_SECONDS = 30
# Host variables passed to every agy process; Windows essentials (USERPROFILE,
# APPDATA, LOCALAPPDATA, SYSTEMROOT, ...) are added by the host platform, so the
# CLI finds the host's own sign-in. Secret-bearing variables stay out.
_ENVIRONMENT_KEYS = frozenset({
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
})
# Session settings shared by both shapes, as live-probed with agy 1.5.14.
_BASE_SETTINGS = {"enableTerminalSandbox": False, "toolPermission": "request-review",
                  "artifactReviewPolicy": "always-proceed"}
_ALWAYS_DENIED = ["mcp(*)", "read_url(*)", "execute_url(*)"]
_AGENT_BODY = {
    "tool_free": ("Complete only the task in the user message, using only the input it supplies. No task tools "
                  "are available. Return the final answer through the native structured output mechanism."),
    "agent": ("Complete the task in the user message with the tools listed for this agent and the locations "
              "the message names. Return the final answer through the native structured output mechanism."),
}


def installed_agy_program(environment) -> str | None:
    """The official Windows installer's fixed location of agy.exe, or None.

    Used only after an explicit path, the configured path and PATH found nothing:
    a process started before the installer updated the user PATH keeps the old
    PATH. Only %LOCALAPPDATA%\\agy\\bin\\agy.exe is checked; nothing is searched.
    """
    if os.name != "nt":
        return None
    base = next((value for key, value in environment.items() if key.upper() == "LOCALAPPDATA"), "")
    if not base:
        return None
    candidate = Path(base) / "agy" / "bin" / "agy.exe"
    return os.path.abspath(candidate) if candidate.is_file() else None


class AntigravityCapabilityUnsupportedError(ValueError):
    """The requested capability combination is outside this first Antigravity binding."""

    error_code = "ADAPTER_CAPABILITY_UNSUPPORTED"


def antigravity_command_permit(command: str) -> str:
    """Return the live-tested agy rule body for exactly this one command string.

    The form is ``regex:`` followed by Python ``re.escape`` of the complete
    command, rendered by the adapter as ``command(<body>)``. Parentheses are
    escaped like every other special character; agy accepted that form for an
    interpreter path with spaces, non-ASCII text and parentheses. Whether agy
    matches the pattern against the whole command or a part of it is native
    behavior that Runtime does not test or claim. A plain-prefix body is not
    validated.

    Only the supplied string is escaped: Runtime does not resolve ``python``
    to an interpreter, normalize path separators or quotes, or combine
    separately permitted commands. Build each canonical command once (for
    example with the selected Runtime Python), put exactly that string in the
    task input, derive its permit from the same value and ask for one tool call
    per command. A model that rewrites the path or joins commands with ``;``
    submits a different string from the one the permit was derived from; agy
    denied such a request in the observed case.

    Raises:
        ValueError: The command is empty, has surrounding spaces, spans lines or
            contains NUL.
    """
    if type(command) is not str or not command or command != command.strip():
        raise ValueError("command must be a non-empty single command without surrounding spaces")
    body = "regex:" + re.escape(command)
    if not _PERMIT.fullmatch(body):
        raise ValueError("this command cannot be expressed as a single-line command permit without NUL")
    return body


def execution_expectation(profile) -> InvocationExecutionExpectation:
    """Admit exactly the structured_output or file_review shape, without opening resources.

    Raises:
        ValueError: The Profile names another binding, transport or effort.
        AntigravityCapabilityUnsupportedError: Any other capability combination,
            refused before a workspace, process or model is used.
    """
    profile.validate()
    if (profile.executor_adapter_id, profile.executor_adapter_revision) != CURRENT_BINDING:
        raise ValueError("Execution Profile targets another binding; antigravity_cli requires antigravity_cli_adapter@v1")
    if profile.transport_kind != TRANSPORT_KIND or profile.provider_id != PROVIDER_ID:
        raise ValueError("Antigravity adapter requires the google antigravity_cli transport")
    if profile.reasoning_profile not in _EFFORTS:
        raise ValueError("antigravity_cli effort must be one of " + ", ".join(_EFFORTS))
    common = (profile.semantic_input_delivery_mode == "inline" and not profile.gateway_access_reasons
              and profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT)
    structured_output = (profile.execution_mode == "tool_free" and profile.attempt_workspace_policy == "none"
                         and profile.tool_policy == () and profile.network_policy == "denied")
    file_review = (profile.execution_mode == "agent" and profile.attempt_workspace_policy == "own_draft_read_write"
                   and set(profile.tool_policy) == FILE_REVIEW_TOOL_POLICY
                   and len(profile.tool_policy) == len(FILE_REVIEW_TOOL_POLICY)
                   and profile.network_policy == "provider_managed")
    if not common or not (structured_output or file_review):
        raise AntigravityCapabilityUnsupportedError(
            "antigravity_cli supports only the structured_output shape (tool_free, inline, workspace none, no "
            "tools, network denied) and the file_review shape (agent, inline, own_draft_read_write, exactly "
            "read/search/edit/shell, provider_managed), both with native structured output; explicit project, "
            "MCP, callbacks, recorded commands and prompt-only JSON are not part of this binding")
    return InvocationExecutionExpectation(
        executor_adapter_id=CURRENT_BINDING[0], executor_adapter_revision=CURRENT_BINDING[1],
        transport_kind=TRANSPORT_KIND, execution_mode=profile.execution_mode,
        semantic_input_delivery_mode="inline", attempt_workspace_policy=profile.attempt_workspace_policy,
        network_policy=profile.network_policy, tool_policy=profile.tool_policy,
    )


def render_agent_definition(execution_mode: str) -> str:
    """The custom primary agent file for one shape (YAML front matter, fixed body). Pure."""
    tools = TOOL_FREE_NATIVE_TOOLS if execution_mode == "tool_free" else FILE_REVIEW_NATIVE_TOOLS
    policy = "off" if execution_mode == "tool_free" else "auto"
    lines = ["---", f"name: {AGENT_NAME}",
             "description: Agent Runtime task with the tools Runtime selected for this call.",
             "mainAgent: true", "subagent: false", "tools:", *(f"  - {tool}" for tool in tools),
             "mcpServers: []", "skills: []", "plugins: []", f'commandExecutionPolicy: "{policy}"', "---",
             _AGENT_BODY[execution_mode], ""]
    return "\n".join(lines)


def _rule_path(path: Path) -> str:
    return Path(path).as_posix()


def session_settings(execution_mode: str, *, working_directory: Path | None = None, materials: Path | None = None,
                     read_only_dependencies: tuple[Path, ...] = (), command_permits: tuple[str, ...] = ()) -> dict:
    """Session-only agy settings for one call. Pure.

    tool_free denies every file, command, MCP and URL operation. file_review
    accepts edits in its working directory (the private scratch), allows reads
    of that directory, the read-only materials and each selected dependency,
    allows exactly the host's command permits, and denies writes to materials
    and to the generated agent folder. No permit means no command allow rule.
    """
    if execution_mode == "tool_free":
        return {"agentMode": "", **_BASE_SETTINGS, "permissions": {"deny": [
            "read_file(*)", "write_file(*)", "command(*)", *_ALWAYS_DENIED]}}
    allow = [f"read_file({_rule_path(working_directory)})", f"read_file({_rule_path(materials)})",
             *(f"read_file({_rule_path(path)})" for path in read_only_dependencies),
             *(f"command({permit})" for permit in command_permits)]
    deny = [f"write_file({_rule_path(materials)})",
            f"write_file({_rule_path(Path(working_directory) / '.agents')})", *_ALWAYS_DENIED]
    return {"agentMode": "accept-edits", **_BASE_SETTINGS, "permissions": {"allow": allow, "deny": deny}}


def build_command(*, cli_path: Path | str, profile, schema_path: Path, settings_path: Path) -> list[str]:
    """The exact agy argv for one call, as the passing native probes ran it. Pure.

    No -p, continue, conversation, project, sandbox or permission-skipping flag;
    the time limit is Runtime's own deadline, so --print-timeout is not passed.
    """
    execution_expectation(profile)
    return [str(cli_path), "--input-format", "stream-json", "--output-format", "stream-json",
            "--json-schema", str(schema_path), "--agent", AGENT_NAME, "--model", profile.model_id,
            "--effort", profile.reasoning_profile, "--settings", str(settings_path), "--disable-slash-commands"]


def stdin_event(prompt: str) -> str:
    """The exact stdin: one stream-json user event and a newline; stdin then closes. Pure."""
    return json.dumps({"event": "user", "message": {"content": prompt}}, ensure_ascii=False) + "\n"


def parse_antigravity_stream(raw: bytes) -> dict:
    """Read the init event, the single terminal result and the observed tool steps.

    Tool steps are recorded by name and final state as observed; their contents
    stay in the raw stream for Inspection. Unparseable lines are not provider
    events and stay in the raw stream. Missing or duplicated init/result events
    and events after the result are reported as issues.
    """
    inits, results, issues, steps = [], [], [], {}
    for index, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        try:
            event = decode_cli_event(line.decode("utf-8"))
        except (UnicodeError, ValueError):
            continue
        kind = event.get("event")
        if results and kind in {"init", "step_update", "result"}:
            issues.append(f"event_after_result:{index}")
        if kind == "init":
            inits.append(event.get("init"))
        elif kind == "result":
            results.append(event.get("result"))
        elif kind == "step_update":
            step = event.get("step_update")
            if isinstance(step, dict) and step.get("step_type") == "tool":
                steps[step.get("step_index")] = {"step_index": step.get("step_index"),
                                                 "tool_name": step.get("tool_name"), "state": step.get("state")}
    if len(inits) != 1 or not isinstance(inits[0], dict):
        issues.append("init_event_missing_or_duplicated")
    if len(results) != 1 or not isinstance(results[0], dict):
        issues.append("terminal_event_missing_or_duplicated")
    return {"init": inits[0] if len(inits) == 1 and isinstance(inits[0], dict) else None,
            "result": results[0] if len(results) == 1 and isinstance(results[0], dict) else None,
            "issues": issues, "tool_steps": list(steps.values())}


def usage_fields(result: dict | None) -> tuple[dict, bool]:
    """Runtime usage from the terminal result's own usage row; (fields, invalid).

    output_tokens is taken as reported; agy's thinking_tokens are already part
    of it and are never added again. Whether agy's input_tokens includes
    cache_read_tokens is not established, so the cache-inclusive input total,
    cache read, cache creation and ordinary input stay unknown. The raw row is
    kept unchanged in the trace.
    """
    fields = {"input_tokens": None, "output_tokens": None, "cache_read_tokens": None,
              "cache_creation_tokens": None, "ordinary_input_tokens": None}
    raw = result.get("usage") if isinstance(result, dict) else None
    if raw is None:
        return fields, False
    if type(raw) is not dict:
        return fields, True
    invalid = False
    for key in ("input_tokens", "output_tokens", "thinking_tokens", "cache_read_tokens", "total_tokens"):
        value = raw.get(key)
        if value is not None and (type(value) is not int or value < 0):
            invalid = True
    if not invalid:
        fields["output_tokens"] = raw.get("output_tokens")
    return fields, invalid


class AntigravityCliAdapter:
    """Execute a normalized request through the native agy CLI for the two admitted shapes.

    The descriptor admits tool_free and agent; execution_expectation narrows
    them to exactly the structured_output and file_review requirement shapes.
    Concrete model and effort come only from the Profile, which requires both
    explicitly (no default). The adapter validates the final structured output,
    the selected agent and the reported model; tool results, soft-denied tool
    requests (reported on stderr while the run continues) and other native
    observations stay in the raw streams and the Inspection view. A denied tool
    request does not by itself fail the Attempt; it is never reported as a
    succeeded tool either. When the result carries no structured_output and
    agy reports result.denied_actions, the unchanged missing-output failure
    names those native denials and the failed tool steps first.
    """

    def __init__(self, *, release_registry: RuntimeReleaseRegistry, artifact_host: ModuleArtifactHost,
                 workspace_root: Path, cli_path: Path | str, read_only_dependencies: tuple[Path, ...] = (),
                 process_runner: Callable = run_cli_process, command_permits: tuple[str, ...] = (),
                 mcp_servers: dict | None = None, project_directory: Path | None = None) -> None:
        """Bind trusted resources; resolves paths only, never logs in or calls agy.

        Args:
            release_registry: Exact definitions and execution Profiles.
            artifact_host: Request-owned content/trace store.
            workspace_root: Runtime-owned directory for retained Attempt folders.
            cli_path: Existing installed native agy executable.
            read_only_dependencies: Explicit trusted directories a file_review
                call may read (rendered as read_file allow rules); none for
                structured_output.
            process_runner: run_cli_process or a test-owned double with its signature.
            command_permits: Host-chosen agy command rule bodies for file_review,
                rendered as command(<permit>) allow rules. The live-tested form is
                antigravity_command_permit(command): regex: plus re.escape of the
                complete command string, unnormalized. Single line, no NUL. No
                permit: no command rule.
            mcp_servers: Accepted only empty; MCP is outside this binding.
            project_directory: Accepted only None; explicit projects are outside
                this binding.
        Raises:
            AntigravityCapabilityUnsupportedError: MCP connections or a project
                directory were supplied.
            ValueError: Invalid command permits.
            OSError: The executable or a dependency path cannot be resolved.
        """
        if mcp_servers:
            raise AntigravityCapabilityUnsupportedError("antigravity_cli takes no MCP connections in this binding")
        if project_directory is not None:
            raise AntigravityCapabilityUnsupportedError("antigravity_cli takes no explicit project directory in this binding")
        if type(command_permits) is not tuple or any(
                type(item) is not str or item != item.strip() or not _PERMIT.fullmatch(item) for item in command_permits):
            raise ValueError("command_permits must be non-empty single-line agy rule bodies without NUL")
        self.executor_adapter_id, self.executor_adapter_revision = CURRENT_BINDING
        self._registry = release_registry
        self._artifacts = artifact_host
        self._workspace_root = Path(workspace_root).resolve()
        self._cli_path = Path(cli_path).resolve(strict=True)
        self._dependencies = tuple(Path(item).resolve(strict=True) for item in read_only_dependencies)
        self._run = process_runner
        self._command_permits = command_permits
        self.descriptor = AgentExecutionAdapterDescriptor(
            adapter_contract_version="v1", adapter_id=CURRENT_BINDING[0], adapter_revision=CURRENT_BINDING[1],
            provider_id=PROVIDER_ID, transport_family="cli", transport_kind=TRANSPORT_KIND,
            runtime_package_id="agent_runtime_core", runtime_package_version=runtime_package_version(),
            supported_context_modes=("stateless",), supported_read_isolation_modes=("entitled_refs",),
            supported_output_constraint_modes=(NATIVE_STRUCTURED_OUTPUT,),
            supported_execution_modes=("agent", "tool_free"), supported_input_delivery_modes=("inline",),
            supported_network_policies=("denied", "provider_managed"),
            supports_dynamic_operation_authorization=False, admission_state="integration_tested",
        )
        self.descriptor.validate()

    def execute(self, request: AuthorizedAgentExecutionRequest,
                host: AuthorizedAgentExecutionHost) -> AgentExecutionResult:
        """Execute one invocation and return a validated output or a typed failure.

        Unsupported shapes, command permits without shell, dependencies for a
        tool-free call and overlapping dependencies are refused before any
        process starts. Effects: prepares one retained Attempt folder and a
        private temporary folder for the settings and schema files, starts agy
        once with the host's own sign-in, and commits output and trace through
        the supplied ports. No login, global setting change or model fallback.
        """
        if type(request) is not AuthorizedAgentExecutionRequest:
            raise ValueError("request must be an exact AuthorizedAgentExecutionRequest")
        request.validate()
        profile = self._registry.get_execution_profile(request.execution_profile_ref, request.execution_profile_sha256)
        expectation = execution_expectation(profile)
        for dependency in self._dependencies:
            # Runtime's own control/material separation, checked before any folder or process.
            if (dependency == Path(dependency.anchor) or dependency.is_relative_to(self._workspace_root)
                    or self._workspace_root.is_relative_to(dependency)):
                raise PermissionError(f"Read-only dependency {dependency} overlaps the Runtime Attempt workspace "
                                      "root or opens a filesystem root")

        def validate_self_test(value):
            validator = getattr(host, "validate_self_test_binding", None)
            if not callable(validator):
                raise SelfTestResourceUnavailableError("self-test requires bounded Runtime resources")
            validator(value, adapter=self, artifact_host=self._artifacts, workspace_root=self._workspace_root,
                      read_only_dependencies=self._dependencies)

        prepared = prepare_registered_invocation_context(
            request=request, release_registry=self._registry, artifact_host=self._artifacts,
            expectation=expectation, self_test_validator=validate_self_test,
        )
        from .invocation_tool_definition import self_test_cancellation_callbacks
        cancellation = self_test_cancellation_callbacks(host, request)
        with _capture_cli_interrupts() as interrupted:
            result, pending_detail, cleanup_error = None, None, None
            try:
                with ExitStack() as cleanup:
                    try:
                        result = self._execute(request, host, prepared, cleanup, cancellation=cancellation)
                    except TerminalAdapterFailure as failure:
                        result, pending_detail = failure.result, failure.pending_failure_detail
            except Exception as exc:
                if result is None:
                    raise
                cleanup_error = exc
            return finalize_adapter_result(
                artifact_host=self._artifacts, request=request, result=result,
                pending_failure_detail=pending_detail, interruption_requested=lambda: interrupted.requested or (
                    bool(cancellation) and cancellation["user_cancel_requested"]()),
                cleanup_error=cleanup_error, interruption_code="antigravity_cli_interrupted",
                cleanup_failure_code="antigravity_cli_cleanup_failed")

    def _execute(self, request, host, prepared, cleanup, *, cancellation) -> AgentExecutionResult:
        profile = prepared.profile
        tool_free = profile.execution_mode == "tool_free"
        trace: dict = {"transport": TRANSPORT_KIND, "model": profile.model_id, "effort": profile.reasoning_profile,
                       "module_run_id": request.module_run_id, "variant_id": request.variant_id,
                       "attempt_id": request.attempt_id, "executor_adapter_id": self.executor_adapter_id,
                       "executor_adapter_revision": self.executor_adapter_revision,
                       "execution_profile_ref": profile.release_ref, "execution_profile_sha256": profile.release_sha256,
                       "timeout_seconds": profile.timeout_seconds,
                       "prompt_envelope_sha256": request.prompt_envelope_sha256}
        parsed = parse_antigravity_stream(b"")
        policy_refusal = None
        resources_body = None

        def fail(failure_class, failure_code, message, *, retry="retry_denied", cause=None, terminal_status="failed"):
            trace["adapter_failure"] = {"failure_class": failure_class, "failure_code": failure_code,
                "message": message, "retry_disposition_id": retry, "terminal_status": terminal_status}
            usage, _ = usage_fields(parsed["result"])
            raise_terminal_failure(
                artifact_host=self._artifacts, request=request, profile=profile,
                failure_class=failure_class, failure_code=failure_code, message=message,
                provider_response="stdout:\n" + trace.get("stdout", "") + "\nstderr:\n" + trace.get("stderr", ""),
                retry_disposition_id=retry, trace=trace, cause=cause, **usage,
                transport_exit_code=trace.get("exit_code"), terminal_status=terminal_status,
                defer_failure_detail=True,
            )

        def capture(process, *, complete):
            nonlocal parsed
            trace.update(captured_cli_streams(process))
            trace["process_output_complete"] = complete
            code = getattr(process, "returncode", None)
            trace["exit_code"] = code if type(code) is int else None
            for name in ("stop_reason", "prior_stop_reason", "cleanup_error", "stream_error"):
                if getattr(process, name, None) is not None:
                    trace[name] = getattr(process, name)
            for stream in ("stdout", "stderr"):
                trace[stream] = bounded_trace_text(cli_stream_bytes(trace, stream).decode("utf-8", errors="replace"))
            parsed = parse_antigravity_stream(cli_stream_bytes(trace, "stdout"))
            trace["provider_stream_issues"] = parsed["issues"]
            if parsed["init"] is not None:
                trace["initialization"] = {key: parsed["init"].get(key) for key in (
                    "model", "agent", "cwd", "permission_mode", "conversation_id")}
                # init.tools lists the general catalog, not the selected agent's tools.
                trace["initialization_tool_catalog"] = parsed["init"].get("tools")
            if parsed["result"] is not None:
                trace["result_status"] = parsed["result"].get("status")
                trace["native_usage"] = parsed["result"].get("usage")
                if "denied_actions" in parsed["result"]:
                    trace["native_denied_actions"] = parsed["result"]["denied_actions"]
            trace["observed_tool_steps"] = parsed["tool_steps"]

        if self._command_permits and tool_free:
            trace["stage"] = "command_permits"
            fail("schema", "ADAPTER_REQUEST_INVALID", "command_permits require the file_review shape with shell")
        if self._dependencies and tool_free:
            trace["stage"] = "read_only_dependencies"
            fail("schema", "ADAPTER_REQUEST_INVALID", "a tool-free call takes no read-only dependencies")
        # Set when the agy process this call started was not confirmed stopped; the
        # private folder is then retained and its location reported.
        stop_unconfirmed = False
        stage = "workspace_preparation"
        try:
            attempt = prepare_attempt_workspace(workspace_root=self._workspace_root, attempt_identity={
                "attempt_id": request.attempt_id, "module_run_id": request.module_run_id,
                "variant_id": request.variant_id, "module_release_sha256": prepared.module.release_sha256,
                "execution_profile_sha256": profile.release_sha256,
                "prompt_envelope_sha256": request.prompt_envelope_sha256,
            })
            with lease_attempt_workspace(attempt):
                temporary = Path(cleanup.enter_context(private_temporary_directory(
                    prefix="agy-", short_path=True, retain=lambda: stop_unconfirmed))).resolve()
                work = attempt / "work"
                materials = work / "materials"
                cwd = work if tool_free else work / "scratch"
                agent_folder = cwd / ".agents" / "agents"
                directories = [work] if tool_free else [work, materials, cwd, cwd / ".tmp"]
                for directory in [*directories, cwd / ".agents", agent_folder]:
                    if directory.is_symlink():
                        policy_refusal = "Antigravity workspace directory is a symlink"
                        raise AttemptWorkspaceConflictError(policy_refusal)
                    HOST_PLATFORM.create_task_directory(directory)
                material_hashes = {}
                if not tool_free:
                    stage = "material_preparation"

                    def set_stage(value):
                        nonlocal stage
                        stage = value
                    try:
                        material_hashes, resources_body = stage_attempt_materials(
                            request=request, host=host, profile=profile, materials=materials,
                            dependencies=self._dependencies, set_stage=set_stage)
                    except AttemptWorkspaceConflictError as exc:
                        policy_refusal = str(exc)
                        raise
                    if resources_body is not None and parse_local_resources(resources_body)["commands"]:
                        raise LocalResourceError("recorded local commands are outside the antigravity_cli binding",
                                                 error_code="ADAPTER_CAPABILITY_UNSUPPORTED")
                stage = "invocation_files"
                agent_path = agent_folder / f"{AGENT_NAME}.md"
                agent_text = render_agent_definition(profile.execution_mode)
                with open(agent_path, "x", encoding="utf-8", newline="\n") as stream:
                    stream.write(agent_text)
                settings = session_settings(profile.execution_mode, working_directory=cwd, materials=materials,
                                            read_only_dependencies=self._dependencies,
                                            command_permits=self._command_permits)
                settings_path = temporary / "settings.json"
                settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
                # The canonical registered schema itself; the result is validated against it again below.
                schema_text = json.dumps(prepared.registered_output_schema, ensure_ascii=False, sort_keys=True,
                                         separators=(",", ":"))
                schema_path = temporary / "output_schema.json"
                schema_path.write_text(schema_text, encoding="utf-8")
                argv = build_command(cli_path=self._cli_path, profile=profile, schema_path=schema_path,
                                     settings_path=settings_path)
                environment = HOST_PLATFORM.provider_environment(os.environ, _ENVIRONMENT_KEYS)
                python = None if tool_free else _runtime_python_executable()
                host_path = [entry for entry in environment.get("PATH", "").split(os.pathsep) if entry]
                bins = [str(dep / "bin") for dep in self._dependencies if (dep / "bin").is_dir()]
                cli_temporary = temporary if tool_free else cwd / ".tmp"
                environment.update(PATH=os.pathsep.join(dict.fromkeys(
                    [*([str(python.parent)] if python is not None else []), *bins, *host_path])),
                    TMPDIR=str(cli_temporary), TEMP=str(cli_temporary), TMP=str(cli_temporary),
                    PYTHONDONTWRITEBYTECODE="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
                stage = "cli_preflight"
                version_process = self._run(argv=[str(self._cli_path), "--version"], prompt="", cwd=cwd,
                                            timeout_seconds=_PREFLIGHT_LIMIT_SECONDS, environment=environment,
                                            deadline_monotonic=invocation_deadline(host, request))
                version = (version_process.stdout or "").strip()
                if version_process.returncode != 0 or not version:
                    raise OSError("agy --version did not report a version")
                locations = None if tool_free else invocation_locations(
                    working_directory=cwd, writable_directories=(cwd,), materials_directory=materials,
                    read_only_dependencies=self._dependencies, runtime_python=python)
                prompt = compose_invocation_prompt(locations, prepared.prompt)
                wire = stdin_event(prompt)
                trace.update(argv=argv, cwd=str(cwd), environment_keys=sorted(environment), cli_version=version,
                             settings=settings, settings_path=str(settings_path), agent_definition=agent_text,
                             agent_definition_path=str(agent_path), output_schema_path=str(schema_path),
                             output_schema_sha256=hashlib.sha256(schema_text.encode("utf-8")).hexdigest(),
                             actual_prompt=prompt, stdin_jsonl=wire, material_sha256=material_hashes)
                trace["effective_configuration"] = {
                    "agent": AGENT_NAME,
                    "agent_definition_path": str(agent_path),
                    "tools": list(TOOL_FREE_NATIVE_TOOLS if tool_free else FILE_REVIEW_NATIVE_TOOLS),
                    "structured_output_mechanism": "finish",
                    "allowed_rules": list(settings["permissions"].get("allow", [])),
                    "denied_rules": list(settings["permissions"]["deny"]),
                    "permission_mode": f"toolPermission=request-review; agentMode={settings['agentMode'] or 'default'}",
                    "terminal_sandbox": False,
                    "settings_scope": "session_only",
                    "read_directories": [] if tool_free else [str(cwd), str(materials), *map(str, self._dependencies)],
                    "write_directories": [] if tool_free else [str(cwd)],
                    "invocation_locations": locations,
                    "mcp_connections": [],
                    "context_suppressed": ["slash_commands", "agent_mcp_servers", "agent_skills", "agent_plugins"],
                    "cli_program": str(self._cli_path),
                    "cli_version": version,
                    "command_scope": "none" if tool_free else "explicit_permits",
                    "command_permits": [] if tool_free else list(self._command_permits),
                    "file_io": "none" if tool_free else "native_tools",
                }
                stage = "provider_invocation"
                try:
                    launch_options = {}
                    if request.self_test_binding_ref is not None:
                        launch_options["launch_guard"] = lambda launch: host.guard_self_test_launch(
                            request, launch, adapter=self, artifact_host=self._artifacts,
                            workspace_root=self._workspace_root, read_only_dependencies=self._dependencies)
                        launch_options.update(cancellation)
                    process = self._run(argv=argv, prompt=wire, cwd=cwd, environment=environment,
                                        timeout_seconds=profile.timeout_seconds,
                                        deadline_monotonic=invocation_deadline(host, request), **launch_options)
                    capture(process, complete=getattr(process, "output_streams_closed", True) is True)
                finally:
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
        except TerminalAdapterFailure:
            raise
        except (Exception, CliProcessInterrupted) as exc:
            if cli_process_stop_confirmed(exc) is False:
                stop_unconfirmed = True
            trace.update(stage=stage, error=str(exc))
            if isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired, CliProcessInterrupted)) or any(
                    getattr(exc, name, None) is not None for name in ("stdout_bytes", "stderr_bytes")):
                capture(exc, complete=False)
            if isinstance(exc, CliProcessStartupError):
                trace["process_created"] = exc.process_created
                if exc.cleanup_error is not None:
                    trace["cleanup_error"] = exc.cleanup_error
            if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) and invocation_budget_expired(host, request):
                fail("timeout", "antigravity_cli_timeout", "Runtime work deadline or Provider timeout expired", cause=exc)
            if isinstance(exc, SelfTestResourceUnavailableError):
                fail("authorization", "self_test_resources_unavailable", str(exc), cause=exc)
            if policy_refusal:
                trace["policy_refusal_reason"] = policy_refusal
                fail("policy_violation", "ADAPTER_POLICY_VIOLATION", policy_refusal, cause=exc)
            if isinstance(exc, LocalResourceError):
                fail("dependency_unavailable" if exc.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED" else
                     "policy_violation" if exc.error_code == "ADAPTER_POLICY_VIOLATION" else "schema",
                     exc.error_code, str(exc), cause=exc)
            if stage == "authorized_input_read":
                raise
            if isinstance(exc, CliProcessInterrupted):
                fail("cancelled", "antigravity_cli_interrupted", "Antigravity CLI interrupted by user",
                     terminal_status="cancelled", cause=exc)
            if stage != "provider_invocation":
                cls = "schema" if stage == "material_preparation" and isinstance(exc, ValueError) else "dependency_unavailable"
                fail(cls, "ADAPTER_REQUEST_INVALID" if cls == "schema" else "ADAPTER_BINDING_UNAVAILABLE", str(exc), cause=exc)
            if isinstance(exc, subprocess.TimeoutExpired):
                fail("timeout", "antigravity_cli_timeout", "Antigravity CLI exceeded the configured timeout",
                     retry="retry_allowed", cause=exc)
            fail("transport", "antigravity_cli_process_failed", str(exc), cause=exc)
        if policy_refusal:
            trace["policy_refusal_reason"] = policy_refusal
            fail("policy_violation", "ADAPTER_POLICY_VIOLATION", policy_refusal)
        terminal = parsed["result"]
        status = terminal.get("status") if terminal is not None else None
        if trace.get("exit_code") != 0:
            fail("provider", "antigravity_cli_nonzero_exit",
                 f"Antigravity CLI exited with {trace.get('exit_code')} (result status {status!r})", retry="retry_allowed")
        if parsed["issues"]:
            fail("provider", "antigravity_cli_result_missing_or_invalid", "; ".join(parsed["issues"]))
        if status != "SUCCESS":
            fail("provider", "antigravity_cli_error_result", f"Antigravity CLI result status is {status!r}, not SUCCESS",
                 retry="retry_allowed")
        initialization = parsed["init"]
        if initialization.get("agent") != AGENT_NAME:
            fail("policy_violation", "antigravity_cli_agent_not_applied",
                 f"Antigravity CLI ran agent {initialization.get('agent')!r}, not the generated {AGENT_NAME!r}")
        if not isinstance(initialization.get("model"), str) or not initialization["model"]:
            fail("provider", "antigravity_cli_model_identity_unavailable", "Antigravity CLI reported no model")
        if initialization["model"] != profile.model_id:
            fail("provider", "antigravity_cli_model_identity_mismatch",
                 f"Antigravity CLI reported model {initialization['model']!r}, not the requested {profile.model_id!r}")
        usage, invalid_usage = usage_fields(terminal)
        if invalid_usage:
            fail("schema", "antigravity_cli_usage_invalid", "Antigravity CLI returned invalid usage metadata")
        payload = terminal.get("structured_output")
        if payload is None:
            message = "Antigravity CLI result carries no structured_output; response text is not parsed"
            denied = terminal.get("denied_actions")
            if isinstance(denied, list) and denied:
                # The native denial is the more direct cause; the missing receipt stays the failure.
                failed_steps = ", ".join(f"{step['tool_name']} #{step['step_index']}"
                                         for step in parsed["tool_steps"] if step["state"] == "ERROR")
                message = (f"Antigravity CLI denied native actions {json.dumps(denied, ensure_ascii=False)}"
                           + (f" (failed tool steps: {failed_steps})" if failed_steps else "")
                           + "; its result carries no structured_output and response text is not parsed")
            fail("schema", "antigravity_cli_structured_output_missing", message)
        try:
            if not isinstance(payload, dict):
                raise ValueError("Antigravity structured_output must be a JSON object")
            Draft202012Validator(prepared.registered_output_schema).validate(payload)
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, ValidationError, RecursionError) as exc:
            fail("schema", "ADAPTER_OUTPUT_INVALID", str(exc), cause=exc)
        submission = OutputSubmission(output_slot_id="result", local_handle="output/result.json")
        try:
            host.stage_output_bytes(submission, canonical)
        except PermissionError as exc:
            fail("authorization", "self_test_resources_unavailable" if request.self_test_binding_ref is not None
                 else "output_authorization_refused", str(exc), cause=exc)
        trace_ref, trace_sha256 = commit_attempt_trace_json(self._artifacts, request, trace)
        return completed_adapter_result(profile=profile, request=request, outputs=(submission,),
            tool_operation_ref_ids=(), trace_ref=trace_ref, trace_sha256=trace_sha256, **usage)


__all__ = [
    "AGENT_NAME", "AntigravityCapabilityUnsupportedError", "AntigravityCliAdapter", "antigravity_command_permit",
    "build_command", "execution_expectation", "installed_agy_program", "parse_antigravity_stream",
    "render_agent_definition",
    "session_settings", "stdin_event", "usage_fields",
]
