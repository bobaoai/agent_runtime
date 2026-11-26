"""Translate normalized Runtime execution requests into Claude CLI calls with native controls.

Runtime assembles the task, directories, recorded tools and the CLI's own
permission configuration before the call, receives the process streams while
it runs, and validates and hands over the result at the end. Claude performs
all file I/O, commands and MCP calls itself under that configuration.
"""

from __future__ import annotations

from .invocation_host_platform_selection import HOST_PLATFORM, private_temporary_directory

import hashlib
from .invocation_process_execution import invocation_deadline, invocation_budget_expired

from contextlib import ExitStack
import json
import locale
import os
from pathlib import Path
import re
import subprocess
from typing import Callable

from jsonschema import Draft202012Validator, ValidationError

from ..contracts.invocation_adapter_definition import (
    AuthorizedAgentExecutionHost, AuthorizedAgentExecutionRequest, AgentExecutionResult, OutputSubmission,
    SelfTestResourceUnavailableError, AgentExecutionAdapterDescriptor,
)
from ..foundation.foundation_json_encoding import decode_cli_event
from ..registry.registry_release_registration import RuntimeReleaseRegistry
from .invocation_context_preparation import InvocationExecutionExpectation, prepare_registered_invocation_context
from .invocation_process_execution import (
    run_cli_process, CliEventSummary, CliProcessInterrupted, CliProcessProgress, CliProcessStartupError,
    CliProgressChannel, _capture_cli_interrupts, _runtime_python_executable, _runtime_python_read_roots,
    cli_process_stop_confirmed,
)
from .invocation_cli_logging import captured_cli_streams
from .invocation_prompt_assembly import NATIVE_STRUCTURED_OUTPUT
from .invocation_result_assembly import (
    TerminalAdapterFailure, commit_attempt_trace_json, completed_adapter_result,
    raise_terminal_failure, finalize_adapter_result,
)
from .invocation_schema_projection import NativeOutputSchemaProjectionError, claude_native_output_schema
from .invocation_tool_definition import (
    MCP_TOOL_PREFIX, ModuleArtifactHost, callback_tool_names, declared_mcp_connections, runtime_package_version,
    validate_native_project,
)
from ..foundation.foundation_environment_setup import validate_mcp_connection_entries
from .invocation_provider_tool_execution import ProviderToolSessionBridge
from .invocation_local_resource_preparation import (
    parse_local_resources, assert_local_materials_unchanged, LocalResourceError, stage_attempt_materials,
    compose_invocation_prompt, invocation_locations,
)
from .invocation_local_command_execution import (
    LocalCommandSession, LOCAL_COMMAND_CLI_TOOL_NAME, provider_private_locations,
)
from .invocation_workspace_preparation import (
    AttemptWorkspaceConflictError, lease_attempt_workspace, prepare_attempt_workspace, validate_project_directory,
)


# Recorded logical tools and the Claude native tools each maps to; nothing else is added.
NATIVE_TOOLS = {"read": ("Read",), "search": ("Grep", "Glob"), "edit": ("Edit", "Write"), "shell": ("Bash",)}
_CURRENT_BINDING = ("claude_cli_adapter", "v4")
# The CLI's native structured-output tool; an output mechanism, not a task tool.
_STRUCTURED_OUTPUT_TOOL = "StructuredOutput"
_PENDING_TOOL_LIMIT = 64
_NATIVE_TOOL_CATEGORIES = {tool: category for category, tools in NATIVE_TOOLS.items() for tool in tools}
_WRITABLE_WORKSPACES = ("own_draft_read_write", "explicit_project_read_write")
_PERMIT = re.compile(r"^[^\r\n)]+$")
_PREFLIGHT_LIMIT_SECONDS = 30
# Native project execution: Claude's own Skill tool, PowerShell beside Bash (Claude offers it where its
# PowerShell tool exists), and, with declared MCP connections, Claude's server-resource and deferred
# tool-schema surfaces. Settings keep each call independent; nothing else is switched off.
_NATIVE_PROJECT_SHELL = ("Bash", "PowerShell")
_NATIVE_PROJECT_MCP_SUPPORT = ("ListMcpResourcesTool", "ReadMcpResourceTool", "ToolSearch")
_NATIVE_PROJECT_SETTINGS = {"autoMemoryEnabled": False, "disableAllHooks": True}


def _native_project_tools(tool_policy, declared_mcp) -> list[str]:
    """Claude built-in tools offered in native project mode, in recorded tool order."""
    tools = [tool for name in tool_policy
             for tool in (_NATIVE_PROJECT_SHELL if name == "shell" else NATIVE_TOOLS.get(name, ()))]
    return [*tools, "Skill", *(_NATIVE_PROJECT_MCP_SUPPORT if declared_mcp else ())]


def _run_cli_preflight(argv: list[str], *, environment: dict[str, str], deadline: float | None) -> str:
    """Run one selected-CLI probe in the owned process runner and return its stdout text.

    The probe keeps its argv, filtered environment and the Runtime's current
    directory. Its 30 s limit is combined with the live deadline, which is never
    extended. Output is decoded as text-mode subprocess.run did (locale encoding,
    universal newlines) and a nonzero exit raises CalledProcessError. Like the main
    invocation, its whole process tree is stopped and confirmed; unconfirmed
    cleanup or a start failure raises the process module's structured error, so
    the main Provider process never starts after an uncertain probe.
    """
    result = run_cli_process(argv=argv, prompt="", cwd=Path.cwd(), timeout_seconds=_PREFLIGHT_LIMIT_SECONDS,
                             environment=environment, deadline_monotonic=deadline)
    encoding = locale.getpreferredencoding(False)
    stdout, stderr = (raw.decode(encoding).replace("\r\n", "\n").replace("\r", "\n")
                      for raw in (result.stdout_bytes, result.stderr_bytes))
    if result.returncode != 0:
        raise subprocess.CalledProcessError(result.returncode, argv, output=stdout, stderr=stderr)
    return stdout


class _ClaudeEventProgress:
    """Per-Attempt safe summary of Claude stream-json activity for live display.

    Reads only event and block types, tool_use id and name, and tool_result
    tool_use_id; never content, arguments, text, thinking or token values. A
    bounded map of at most 64 pending requests correlates a result with its
    request's category inside this call; uncorrelated results are unknown.
    Malformed nesting is handled by explicit checks. Any unexpected fault stops
    event summaries through the channel and is never raised to the caller.
    """

    def __init__(self, channel: CliProgressChannel, *, native_structured_output: bool) -> None:
        self._channel = channel
        self._native_structured_output = native_structured_output
        self._callback_tools: frozenset[str] = frozenset()
        self._pending: dict[str, str] = {}
        self._model_active = False
        self._stopped = False

    def bind_callback_tools(self, names) -> None:
        """Accept the frozen callback bridge's CLI tool names before the CLI starts."""
        self._callback_tools = frozenset(name for name in names if isinstance(name, str))

    def record(self, event: dict) -> None:
        """Publish the recognized transition in one decoded event; never raises."""
        if self._stopped:
            return
        try:
            summary = self._summarize(event)
            if summary is not None:
                self._channel.event(summary)
        except Exception:
            self._stop()

    def declared_command(self, command_id: str, finished: bool) -> None:
        """Publish a declared command bound by LocalCommandSession; never raises."""
        if self._stopped:
            return
        try:
            phase = "declared_command_finished" if finished else "declared_command_started"
            self._channel.event(CliEventSummary(phase, "declared_command", command_id))
        except Exception:
            self._stop()

    def _stop(self) -> None:
        self._stopped = True
        self._channel.stop_events()

    def _category(self, name) -> str:
        if not isinstance(name, str):
            return "other_tool"
        if name in _NATIVE_TOOL_CATEGORIES:
            return _NATIVE_TOOL_CATEGORIES[name]
        if name == LOCAL_COMMAND_CLI_TOOL_NAME:
            return "declared_command"
        if name.startswith(MCP_TOOL_PREFIX) and name not in self._callback_tools:
            return "mcp_tool"
        if name in self._callback_tools:
            return "declared_callback"
        if self._native_structured_output and name == _STRUCTURED_OUTPUT_TOOL:
            return "structured_output"
        return "other_tool"

    def _transition(self, phase: str, category: str | None = None) -> CliEventSummary:
        self._model_active = False
        return CliEventSummary(phase, category)

    def _activity(self) -> CliEventSummary | None:
        if self._model_active:
            return None
        self._model_active = True
        return CliEventSummary("model_activity_observed")

    def _summarize(self, event: dict) -> CliEventSummary | None:
        kind, subtype = event.get("type"), event.get("subtype")
        if kind == "system" and subtype == "init":
            return self._transition("init_observed")
        if kind == "system" and subtype == "thinking_tokens":
            return self._activity()
        if kind == "result":
            return self._transition("final_result_observed")
        message = event.get("message")
        blocks = message.get("content") if isinstance(message, dict) else None
        blocks = [block for block in blocks if isinstance(block, dict)] if isinstance(blocks, list) else []
        if kind == "assistant":
            uses = [block for block in blocks if block.get("type") == "tool_use"]
            if not uses:
                return self._activity()
            for use in uses:
                identity = use.get("id")
                if isinstance(identity, str) and identity:
                    if identity not in self._pending and len(self._pending) >= _PENDING_TOOL_LIMIT:
                        del self._pending[next(iter(self._pending))]
                    self._pending[identity] = self._category(use.get("name"))
            return self._transition("tool_requested", self._category(uses[-1].get("name")))
        if kind == "user":
            results = [block for block in blocks if block.get("type") == "tool_result"]
            if results:
                categories = []
                for item in results:
                    identity = item.get("tool_use_id")
                    valid = isinstance(identity, str) and identity
                    categories.append(self._pending.pop(identity, "unknown") if valid else "unknown")
                return self._transition("tool_result_observed", categories[-1])
        return None


def _validate_binding(binding: tuple[str, str]) -> None:
    if type(binding) is not tuple or binding != _CURRENT_BINDING:
        raise ValueError("ClaudeAdapter requires claude_cli_adapter@v4; historical records need an explicit new execution Profile")


def _execution_expectation(profile, adapter_binding=_CURRENT_BINDING) -> InvocationExecutionExpectation:
    """Validate supported field combinations without opening executable/resources."""
    _validate_binding(adapter_binding)
    profile.validate()
    if (profile.executor_adapter_id, profile.executor_adapter_revision) != adapter_binding:
        raise ValueError("Execution Profile targets another ClaudeAdapter binding")
    if profile.transport_kind != "claude_cli" or profile.provider_id != "anthropic":
        raise ValueError("ClaudeAdapter requires the anthropic claude_cli transport")
    if profile.gateway_access_reasons or profile.semantic_input_delivery_mode in {"gateway_read", "hybrid"}:
        raise ValueError("ClaudeAdapter does not support production Gateway access or gateway_read/hybrid input")
    if profile.execution_mode not in {"agent", "tool_free"}:
        raise ValueError("ClaudeAdapter supports agent and tool_free execution")
    if profile.semantic_input_delivery_mode != "inline" or profile.network_policy not in {"denied", "provider_managed"}:
        raise ValueError("ClaudeAdapter requires inline input and denied or provider_managed tool network")
    if profile.attempt_workspace_policy not in {"none", *_WRITABLE_WORKSPACES}:
        raise ValueError("ClaudeAdapter requests an unsupported workspace policy")
    if "edit" in profile.tool_policy and profile.attempt_workspace_policy not in _WRITABLE_WORKSPACES:
        raise ValueError("ClaudeAdapter edit needs a writable workspace (private draft or explicit project)")
    if profile.reasoning_profile not in {"low", "medium", "high", "xhigh", "max"}:
        raise ValueError("ClaudeAdapter requests an unsupported CLI effort")
    return InvocationExecutionExpectation(
        executor_adapter_id=adapter_binding[0], executor_adapter_revision=adapter_binding[1],
        transport_kind="claude_cli", execution_mode=profile.execution_mode,
        semantic_input_delivery_mode="inline", attempt_workspace_policy=profile.attempt_workspace_policy,
        network_policy=profile.network_policy, tool_policy=profile.tool_policy,
    )


# Host variables passed through to every Claude CLI process. Secret-bearing
# variables (for example a definition-store DSN) stay out by this allowlist.
_ENVIRONMENT_KEYS = frozenset({
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "CLAUDE_CODE_GIT_BASH_PATH",
})


def _native_settings(*, profile, cwd: Path, materials: Path, read_dependencies, write_rule: str | None,
                     cli_temporary: Path, draft_layout: bool) -> dict:
    """Claude settings for an ordinary call: native file scope, plus a sandbox only when explicitly required.

    File tools are confined to the working directories (cwd plus additional
    read directories) by Claude's own blockReads/blockWritesOutsideWorkingDirectories.
    In the draft layout cwd holds materials and scratch, so Edit on materials is
    denied while Edit(./scratch/**) is the only write allow. The OS sandbox is
    configured only when the Profile explicitly records denied network together
    with Shell; Claude then reports for itself whether it can enforce that.
    """
    tools = set(profile.tool_policy)
    if not tools & {"read", "search", "edit", "shell"}:
        return {}
    additional = ([] if draft_layout or cwd == materials.parent else [str(materials)]) + [str(path) for path in read_dependencies]
    permissions = {"blockReadsOutsideWorkingDirectories": True, "blockWritesOutsideWorkingDirectories": True,
                   "additionalDirectories": additional}
    if write_rule is not None and draft_layout:
        permissions["deny"] = ["Edit(./materials/**)"]
    settings = {"permissions": permissions}
    if "shell" in tools and profile.network_policy == "denied":
        write_directories = ([] if write_rule is None else
                             [str(cwd)] if write_rule == "./**" else [str(cwd / write_rule[2:].removesuffix("/**"))])
        settings["sandbox"] = {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
            "autoAllowBashIfSandboxed": False,
            "filesystem": {"allowWrite": [*write_directories, str(cli_temporary)]},
            "network": {"allowedDomains": [], "strictAllowlist": True,
                        "allowAllUnixSockets": False, "allowLocalBinding": False}}
    return settings


def _extension_settings(*, cwd: Path, materials: Path, cli_temporary: Path, read_dependencies,
                        writable_draft: bool, private_roots) -> dict:
    """The unchanged settings of the explicit command/callback extensions (macOS/Unix)."""
    settings = {
        "permissions": {"blockReadsOutsideWorkingDirectories": True,
            "additionalDirectories": [str(materials), *map(str, read_dependencies)]},
        "sandbox": {"enabled": True, "autoAllowBashIfSandboxed": True, "failIfUnavailable": True,
            "allowUnsandboxedCommands": False,
            "filesystem": {"denyRead": ["/"],
                "allowRead": [str(cwd), str(materials), str(cli_temporary), "/bin", "/usr/bin", "/usr/lib",
                              "/System", "/Library", "/dev", *map(str, read_dependencies)],
                "denyWrite": [str(materials), *map(str, read_dependencies),
                    "/tmp/claude", "/private/tmp/claude",
                    str(Path.home() / ".npm/_logs"), str(Path.home() / ".claude")]
                    if writable_draft else ["/"],
                "allowWrite": [str(cwd), str(cli_temporary)] if writable_draft else []},
            "network": {"allowedDomains": [], "strictAllowlist": True,
                        "allowAllUnixSockets": False, "allowLocalBinding": False}},
    }
    settings.update(claudeMdExcludes=["**"], autoMemoryEnabled=False, disableAllHooks=True, enabledPlugins={})
    settings["sandbox"]["filesystem"]["denyRead"].extend(str(root) for root in private_roots)
    return settings


def _call_settings(*, profile, cwd: Path, materials: Path, read_dependencies, write_rule: str | None,
                   cli_temporary: Path, writable_draft: bool, native_project: bool, mcp_declared: bool,
                   extension_roots=None) -> dict:
    """The exact --settings object of one call; execute renders it unchanged. Pure; no IO.

    extension_roots (private roots of the command/callback extensions, or None)
    keeps the extensions' unchanged settings. A native project call gets only
    autoMemoryEnabled=false and disableAllHooks=true, with or without MCP. Every
    other call keeps _native_settings, and declared MCP adds the existing
    context exclusions (claudeMdExcludes, auto-memory, hooks, enabledPlugins={}).
    """
    if extension_roots is not None:
        return _extension_settings(cwd=cwd, materials=materials, cli_temporary=cli_temporary,
                                   read_dependencies=read_dependencies, writable_draft=writable_draft,
                                   private_roots=extension_roots)
    if native_project:
        return dict(_NATIVE_PROJECT_SETTINGS)
    settings = _native_settings(profile=profile, cwd=cwd, materials=materials, read_dependencies=read_dependencies,
                                write_rule=write_rule, cli_temporary=cli_temporary, draft_layout=writable_draft)
    if mcp_declared:
        settings.update(claudeMdExcludes=["**"], autoMemoryEnabled=False, disableAllHooks=True, enabledPlugins={})
    return settings


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
    prompt_only_json or native_structured_output. Agent mode also supports
    explicitly supplied self-test callbacks through its trusted tool-session
    factory; that local bridge does not provide production Gateway access.
    Frozen permissions are never
    inferred from task text, model choice or old saved Profile records. Unsupported
    combinations, including Gateway requests without a CLI bridge, fail before
    provider invocation. The canonical output schema is always validated.

    Tool events and initialization capability descriptions remain in the raw
    streams. Inspection interprets them on demand; explicit Evaluation judges
    behavior. This adapter validates the final response and actual Runtime
    resources without deriving failures or retry decisions from tool logs.
    Protected-material changes and actual resource failures still invalidate
    the Attempt.

    Concrete model IDs and the CLI [1m] selector are supported. Initialization
    and response-model evidence must agree with the Profile; terminal usage for
    that exact model may supply evidence when assistant messages are absent.
    Auxiliary CLI models never stand in for the requested response model.
    Unresolved aliases, missing evidence and mismatches cannot report success.

    Default SIGINT capture spans provider execution, output validation, raw
    archival and resource cleanup. One check after cleanup selects the handoff
    result using the already committed trace. Diagnostic persistence performs
    no further cancellation checks. Custom handlers are not overridden.
    """

    def __init__(self, *, release_registry: RuntimeReleaseRegistry, artifact_host: ModuleArtifactHost,
                 workspace_root: Path, cli_path: Path | str, read_only_dependencies: tuple[Path, ...] = (),
                 process_runner: Callable = run_cli_process,
                 adapter_binding: tuple[str, str] = _CURRENT_BINDING,
                 progress_observer: Callable[[CliProcessProgress], None] | None = None,
                 mcp_servers: dict | None = None, project_directory: Path | None = None,
                 command_permits: tuple[str, ...] = (), native_project: bool = False) -> None:
        """Bind trusted resources and the current executable Adapter identity.

        adapter_binding is claude_cli_adapter@v4. Old Profile records remain
        readable and committed replay is independent of this Adapter, but new
        execution needs an explicit current Profile; no historical pair is
        silently reinterpreted. Recorded tools map exactly (read: Read; search:
        Grep, Glob; edit: Edit, Write; shell: Bash) under --permission-mode
        dontAsk with the user's own settings loaded. A private draft runs in the
        Attempt work folder with scratch writable and materials denied to Edit;
        an explicit project runs in the caller's project_directory. The Prompt Envelope is frozen
        before execution, including any Runtime resource description, and is sent unchanged.
        An agent call's stdin is a short code-generated "Runtime Invocation Locations" section
        (working and writable directories, the Attempt materials folder, these
        read_only_dependencies and, with shell, the Runtime Python), a blank line, then
        that envelope; a tool-free call sends the envelope alone. The trace's
        actual_prompt keeps the exact stdin text, which Inspection returns; the envelope
        ref and hash identify only the envelope. TMPDIR, TEMP, TMP and CLAUDE_CODE_TMPDIR
        all name the call's private temporary folder in the CLI's environment only.
        read_only_dependencies are trusted existing paths, never task-supplied
        strings; temporary self-tests require these roots in their exact live binding.
        Shell-capable calls use the Python environment running Runtime: its bin
        directory leads PATH and its installation roots are read-only. These
        fixed runtime resources are separate from optional additional dependencies;
        no Python selector or fallback is exposed. Tool-free calls gain no such
        additional directories or task tools.
        This constructor resolves paths only. It neither logs in nor calls Claude.

        Args:
            release_registry: Exact definitions and execution Profiles.
            artifact_host: Trusted input/output byte storage.
            workspace_root: Runtime-owned directory for isolated Attempts.
            cli_path: Existing installed Claude executable.
            read_only_dependencies: Explicit trusted read roots; no task discovery.
            process_runner: Runtime process executor or a test-owned double.
            adapter_binding: Exact currently implemented pair.
            progress_observer: Optional callable receiving volatile
                CliProcessProgress snapshots while each CLI process runs: process
                facts plus one safe current-event summary (init, model activity,
                tool request or result with a safe category, final result, or a
                declared command bound by Runtime). Never content, arguments,
                output or history. Delivery is in order through a bounded
                buffer, may drop updates for a slow observer (reported as
                updates_dropped) and never changes execution, trace or result.
                None keeps the original arguments to process_runner and
                LocalCommandSession.
            mcp_servers: Host connections for claude_cli by name, each
                {"config": {...Claude mcpServers entry...}, "environment_keys": [...]}.
                Only connections a Profile declares as mcp__<name> are passed, via a
                private mcp.json; the config never appears in argv or the trace.
            project_directory: The caller's existing writable directory for an
                explicit_project_read_write Profile; never deleted by Runtime.
            command_permits: Host-prepared Bash permits (Claude rule bodies such as
                "python -m pytest:*"), rendered as Bash(<permit>) allow rules. They
                pre-approve those commands; Claude's own read-only commands and the
                user's allow/deny rules keep their native effect. No permit means no
                Runtime Bash allow rule.
            native_project: Per-call choice, False by default (every rendering
                above is unchanged). True runs one independent call in the
                caller's project_directory with the project's native context:
                no --safe-mode or --disable-slash-commands, so CLAUDE.md and
                user/project skills load by Claude's own rules; tools add Skill
                and, for shell, PowerShell beside Bash, plus ListMcpResourcesTool,
                ReadMcpResourceTool and ToolSearch when MCP is declared; allow
                rules under dontAsk cover those tools, Edit(./**) and each
                declared mcp__<name>, while user, project and managed deny/ask
                rules keep their precedence. Settings are only
                autoMemoryEnabled=false and disableAllHooks=true (no file-scope
                block, sandbox or --add-dir); MCP stays the strict private
                mcp.json of declared connections. Shell runs with host
                permissions: the directory is task scope, not an OS boundary.
                Requires an explicit_project_read_write Profile accepted by
                validate_native_project; refuses command_permits and additional
                read_only_dependencies. Recorded as native_project in the trace
                and effective_configuration.
        Raises:
            TypeError: progress_observer is not callable.
            ValueError: Unknown adapter identity or invalid descriptor, a
                non-bool native_project, or native_project with command_permits
                or additional read_only_dependencies.
            OSError: Executable or a dependency path cannot be resolved.
        Effects:
            Resolves paths only; no provider invocation, registration or login.
        """
        _validate_binding(adapter_binding)
        if progress_observer is not None and not callable(progress_observer):
            raise TypeError("progress_observer must be callable")
        self._progress_observer = progress_observer
        self.executor_adapter_id, self.executor_adapter_revision = adapter_binding
        self._registry = release_registry
        self._artifacts = artifact_host
        self._workspace_root = Path(workspace_root).resolve()
        self._cli_path = Path(cli_path).resolve(strict=True)
        self._dependencies = tuple(Path(item).resolve(strict=True) for item in read_only_dependencies)
        self._run = process_runner
        self._tool_factory = None
        self._tool_definitions = []
        self._mcp_servers = validate_mcp_connection_entries(mcp_servers or {})
        self._project_directory = (None if project_directory is None
                                   else validate_project_directory(project_directory, workspace_root=self._workspace_root))
        if type(command_permits) is not tuple or any(type(item) is not str or not _PERMIT.fullmatch(item.strip()) or item != item.strip()
                                                      for item in command_permits):
            raise ValueError("command_permits must be non-empty single-line Bash rule bodies without ')'")
        self._command_permits = command_permits
        validate_native_project(native_project)
        if native_project and (command_permits or self._dependencies):
            raise ValueError("native_project runs Shell with host permissions and takes no command_permits "
                             "or additional read_only_dependencies")
        self._native_project = native_project
        self.descriptor = AgentExecutionAdapterDescriptor(
            adapter_contract_version="v1",
            adapter_id=self.executor_adapter_id, adapter_revision=self.executor_adapter_revision,
            provider_id="anthropic", transport_family="cli", transport_kind="claude_cli",
            runtime_package_id="agent_runtime_core", runtime_package_version=runtime_package_version(),
            supported_context_modes=("stateless",), supported_read_isolation_modes=("entitled_refs",),
            supported_output_constraint_modes=("prompt_only_json", "native_structured_output"),
            supported_execution_modes=("agent", "tool_free"),
            supported_input_delivery_modes=("inline",), supported_network_policies=("denied", "provider_managed"),
            supports_dynamic_operation_authorization=False, admission_state="integration_tested",
        )
        self.descriptor.validate()

    def bind_tool_session_factory(self, factory, definitions):
        """Attach the self-test callback factory selected during preparation.

        factory supplies definitions and open_session(request); definitions is
        the exact JSON tool table already frozen for this request. Preparation
        calls this method, never model-authored task data. It retains the factory
        and a private copy of the table, but opens no session or CLI process.

        Returns None. A different factory on this Adapter raises PermissionError;
        a table that differs from factory.definitions raises ValueError. Execution
        separately validates the exact Profile tools and live resource binding
        before opening a session. Native tools need no callback factory, and this
        binding neither issues a domain grant nor enables production Gateway input.
        """
        from .invocation_tool_definition import tool_definition_records
        if self._tool_factory is not None and self._tool_factory is not factory:
            raise PermissionError("Adapter already belongs to another callback factory")
        if tool_definition_records(factory.definitions) != definitions:
            raise ValueError("factory differs from frozen tool definitions")
        self._tool_factory = factory
        self._tool_definitions = json.loads(json.dumps(definitions, allow_nan=False))

    def build_command(self, *, profile, settings: dict, output_schema: dict | None, local_commands=None,
                      provider_tools=None, mcp_config_path: Path | None = None,
                      declared_mcp: tuple[str, ...] = (), write_rule: str | None = None) -> list[str]:
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
            provider_tools: The exact live callback bridge, or None.
            mcp_config_path: Private mcp.json holding only the declared host
                connections, or None. Only its path appears in argv.
            declared_mcp: Host connection names declared as mcp__<name>.
            write_rule: Edit rule path (for example ./scratch/**) when the Profile
                records edit, else None. Edit(path) also governs Write.
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
        validate_native_project(self._native_project, profile=profile)
        native = [tool for name in profile.tool_policy if name in NATIVE_TOOLS for tool in NATIVE_TOOLS[name]]
        if self._native_project:
            native = _native_project_tools(profile.tool_policy, declared_mcp)
        tools = ",".join(native)
        if local_commands is not None and type(local_commands) is not LocalCommandSession:
            raise TypeError("local_commands must be the exact Runtime LocalCommandSession")
        extension = local_commands is not None or provider_tools is not None
        if extension and self._native_project:
            raise ValueError("native_project takes no command or callback extension")
        mcp_config = {"mcpServers": {}} if local_commands is None else local_commands.mcp_config
        if extension:
            # Explicit extension calls keep their existing allow list unchanged.
            allowed = list(native) + ([LOCAL_COMMAND_CLI_TOOL_NAME] if local_commands is not None else [])
        elif self._native_project:
            # Whole-tool allow rules under dontAsk; native deny/ask rules still take precedence.
            allowed = [tool for tool in native if tool not in {"Edit", "Write"}]
            if write_rule is not None:
                allowed.append(f"Edit({write_rule})")
        else:
            allowed = [tool for tool in native if tool in {"Read", "Grep", "Glob"}]
            if write_rule is not None:
                allowed.append(f"Edit({write_rule})")
            if "Bash" in native:
                allowed.extend(f"Bash({permit})" for permit in self._command_permits)
        callback_names = callback_tool_names(profile.tool_policy)
        if callback_names:
            if type(provider_tools) is not ProviderToolSessionBridge or provider_tools.cli_tools != {
                    "mcp__runtime_tools__" + name for name in callback_names}:
                raise PermissionError("Profile callbacks require their exact live tool bridge")
            mcp_config = {"mcpServers": {**mcp_config["mcpServers"], **provider_tools.mcp_config["mcpServers"]}}
            allowed.extend(sorted(provider_tools.cli_tools))
        elif provider_tools is not None:
            raise ValueError("unused provider tool bridge")
        if bool(declared_mcp) != (mcp_config_path is not None):
            raise ValueError("declared MCP connections need exactly one private mcp.json")
        allowed.extend(MCP_TOOL_PREFIX + name for name in declared_mcp)
        mcp_values = [json.dumps(mcp_config, separators=(",", ":"))]
        if mcp_config_path is not None:
            mcp_values.append(str(mcp_config_path))
        any_mcp = extension or bool(declared_mcp)
        # Native project calls keep CLAUDE.md and skills: no safe-mode and slash commands (skills) stay on.
        argv = [str(self._cli_path), "-p", *([] if any_mcp or self._native_project else ["--safe-mode"]),
                *([] if self._native_project else ["--disable-slash-commands"]),
                "--strict-mcp-config", "--mcp-config", *mcp_values,
                "--no-chrome", "--no-session-persistence", "--model", profile.model_id,
                "--effort", profile.reasoning_profile, "--permission-mode", "dontAsk",
                "--output-format", "stream-json", "--verbose", "--tools", tools,
                "--allowedTools", *(allowed or [""]),
                "--settings", json.dumps(settings, separators=(",", ":"))]
        if output_schema is not None:
            argv.extend(("--json-schema", json.dumps(output_schema, separators=(",", ":"))))
        for directory in settings.get("permissions", {}).get("additionalDirectories", ()):
            argv.extend(("--add-dir", directory))
        return argv

    def execute(self, request: AuthorizedAgentExecutionRequest,
                host: AuthorizedAgentExecutionHost) -> AgentExecutionResult:
        """Execute with exact resources and archive raw streams and final metadata.

        Args:
            request: AuthorizedAgentExecutionRequest with exact Module/Profile,
                Prompt, schema and authorized input references, not raw CLI flags.
            host: Trusted operation authorization or live self-test resource host.
        Returns:
            AgentExecutionResult with validated output or an actual typed failure;
            original streams, observed model and actual Runtime resource records
            stay in its trace. Detailed log parsing belongs to Inspection;
            execution completion is not a business approval or behavior verdict.
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
        validate_native_project(self._native_project, profile=profile)
        callback_names = callback_tool_names(profile.tool_policy)
        if callback_names:
            if request.self_test_binding_ref is None or self._tool_factory is None:
                raise PermissionError("callbacks require a trusted self-test Factory")
            if callback_names != {item["name"] for item in self._tool_definitions} or "sandbox_command_execute" in callback_names:
                raise PermissionError("callback table differs from Profile or conflicts with commands")
            host.validate_provider_tool_session(request, factory=self._tool_factory, definitions=self._tool_definitions)
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
            # One handoff for both success and failure, after their trace commits
            # and resource cleanup. An interrupted timeout must not allow retry.
            return finalize_adapter_result(artifact_host=self._artifacts, request=request, result=result,
                pending_failure_detail=pending_detail, interruption_requested=lambda: interrupted.requested or (
                    bool(cancellation) and cancellation["user_cancel_requested"]()),
                cleanup_error=cleanup_error, interruption_code="claude_cli_interrupted",
                cleanup_failure_code="claude_cli_cleanup_failed")

    def _execute(self, request, host, prepared, cleanup, *, cancellation) -> AgentExecutionResult:
        profile = prepared.profile
        result: dict = {}
        trace: dict = {"transport": "claude_cli",
                       "model": profile.model_id, "effort": profile.reasoning_profile,
                       "module_run_id": request.module_run_id, "variant_id": request.variant_id,
                       "attempt_id": request.attempt_id}
        policy_refusal: str | None = None
        event_error: str | None = None
        local_commands = None
        provider_tools = None
        resources_body = None
        channel = events = None

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
            # Claude reports ordinary input apart from the two cache counts. The Runtime total includes
            # cache, so it is known only when all three are; ordinary input is kept as reported either way.
            ordinary, outgoing, read, created = values
            total = ordinary + read + created if all(value is not None for value in (ordinary, read, created)) else None
            return {"input_tokens": total, "output_tokens": outgoing, "cache_read_tokens": read,
                    "cache_creation_tokens": created, "ordinary_input_tokens": ordinary}, invalid

        def fail(failure_class, failure_code, message, *, retry="retry_denied", cause=None, terminal_status="failed"):
            trace["adapter_failure"] = {"failure_class": failure_class, "failure_code": failure_code,
                "message": message, "retry_disposition_id": retry, "terminal_status": terminal_status}
            usage, _ = usage_fields()
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
        def observe(line: str) -> bool:
            nonlocal result, event_error
            if not line.strip():
                return True
            try:
                event = decode_cli_event(line)
            except ValueError:
                # Raw non-result diagnostics are archived by the process runner.
                return True
            kind, subtype = event.get("type"), event.get("subtype")
            if kind == "system" and subtype == "init":
                trace["initialization"] = {key: event.get(key) for key in ("model", "claude_code_version")}
            elif kind == "result":
                if result:
                    event_error = "CLI returned multiple terminal results"
                result = event
                trace["result"] = event
            message = event.get("message")
            if kind == "assistant" and isinstance(message, dict) and message.get("model"):
                models = trace.setdefault("response_models", [])
                if message["model"] not in models:
                    models.append(message["model"])
            if events is not None:
                events.record(event)
            return event_error is None

        policy = profile.attempt_workspace_policy
        if (policy == "explicit_project_read_write") != (self._project_directory is not None):
            trace["stage"] = "project_directory"
            fail("schema", "ADAPTER_REQUEST_INVALID",
                 "project_directory is required exactly for an explicit_project_read_write Profile")
        if self._command_permits and "shell" not in profile.tool_policy:
            trace["stage"] = "command_permits"
            fail("schema", "ADAPTER_REQUEST_INVALID", "command_permits require a recorded shell capability")
        declared = declared_mcp_connections(profile.tool_policy)
        missing = [name for name in declared if name not in self._mcp_servers]
        if missing:
            trace["stage"] = "mcp_connection"
            fail("dependency_unavailable", "ADAPTER_BINDING_UNAVAILABLE",
                 f"Module declares MCP connection {missing[0]}; the host has not configured it for claude_cli")
        connections = {name: self._mcp_servers[name] for name in declared}
        # Set when the CLI process this call started was not confirmed stopped; the
        # private crt- directory is then retained and its location reported.
        stop_unconfirmed = False
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
                temporary = cleanup.enter_context(private_temporary_directory(
                    prefix="crt-", short_path=True, retain=lambda: stop_unconfirmed))
                work = attempt / "work"
                materials = work / "materials"
                scratch = work / "scratch"
                extension_call = bool(self._tool_factory is not None)
                writable_draft = policy == "own_draft_read_write"
                for directory in (work, materials, *((scratch,) if writable_draft else ())):
                    if directory.is_symlink():
                        policy_refusal = "CLI workspace directory is a symlink"
                        raise AttemptWorkspaceConflictError(policy_refusal)
                    HOST_PLATFORM.create_task_directory(directory)
                cli_temporary = Path(temporary).resolve()
                material_hashes = {}
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
                commands_declared = resources_body is not None and bool(parse_local_resources(resources_body)["commands"])
                extension_call = extension_call or commands_declared
                # Workspace layout: explicit extensions keep their existing scratch cwd;
                # ordinary drafts run in work/ (materials and scratch side by side, as
                # verified natively); explicit projects run in the caller's directory.
                if policy == "explicit_project_read_write":
                    cwd, write_rule = self._project_directory, "./**"
                elif writable_draft:
                    cwd, write_rule = (scratch, None) if extension_call else (work, "./scratch/**")
                else:
                    cwd, write_rule = work, None
                if "edit" not in profile.tool_policy:
                    write_rule = None
                python = _runtime_python_executable() if "shell" in profile.tool_policy else None
                python_roots = _runtime_python_read_roots() if python is not None else ()
                read_dependencies = tuple(dict.fromkeys((*python_roots, *self._dependencies)))
                private_paths = (cli_temporary, self._workspace_root,
                                 *(provider_private_locations() if extension_call else ()))
                if any(Path(private).resolve().is_relative_to(dep) or dep.is_relative_to(Path(private).resolve())
                       for private in private_paths for dep in read_dependencies):
                    raise PermissionError("Read-only dependencies overlap Runtime-owned folders or private Provider state")
                if self._progress_observer is not None:
                    channel = CliProgressChannel(self._progress_observer)
                    events = _ClaudeEventProgress(channel, native_structured_output=native_schema is not None)
                if commands_declared:
                    stage = "local_command_preparation"
                    observation = {} if events is None else {"on_declared_command": events.declared_command}
                    local_commands = cleanup.enter_context(LocalCommandSession(request=request, profile=profile, host=host, adapter=self,
                        artifact_host=self._artifacts, workspace_root=self._workspace_root, resources_body=resources_body,
                        source_root=materials / "source", scratch_root=scratch, read_only_dependencies=self._dependencies,
                        **observation))
                if self._tool_factory is not None:
                    stage = "provider_tool_preparation"
                    provider_tools = cleanup.enter_context(ProviderToolSessionBridge(request=request, host=host,
                        factory=self._tool_factory, definitions=self._tool_definitions, timeout_seconds=profile.timeout_seconds))
                    if events is not None:
                        events.bind_callback_tools(provider_tools.cli_tools)
                settings = _call_settings(profile=profile, cwd=cwd, materials=materials,
                    read_dependencies=read_dependencies, write_rule=write_rule, cli_temporary=cli_temporary,
                    writable_draft=writable_draft, native_project=self._native_project,
                    mcp_declared=bool(connections),
                    extension_roots=None if local_commands is None and provider_tools is None else
                    [item.private_root for item in (local_commands, provider_tools) if item is not None])
                mcp_config_path = None
                if connections:
                    mcp_config_path = cli_temporary / "mcp.json"
                    mcp_config_path.write_text(json.dumps({"mcpServers": {name: entry["config"]
                        for name, entry in connections.items()}}, ensure_ascii=False), encoding="utf-8")
                argv = self.build_command(profile=profile, settings=settings, output_schema=native_schema,
                                          local_commands=local_commands, provider_tools=provider_tools,
                                          mcp_config_path=mcp_config_path, declared_mcp=declared,
                                          write_rule=write_rule if not extension_call else None)
                # One filtered environment serves the preflight and the real call, so host
                # secrets such as a definition-store DSN never reach any Claude CLI process.
                environment = HOST_PLATFORM.provider_environment(os.environ, _ENVIRONMENT_KEYS)
                forwarded = sorted({key for entry in connections.values() for key in entry["environment_keys"]})
                environment.update({key: os.environ[key] for key in forwarded if key in os.environ})
                bins = [str(dep / "bin") for dep in self._dependencies if (dep / "bin").is_dir()]
                python_bins = [str(python.parent)] if python is not None else []
                host_path = [entry for entry in environment.get("PATH", "").split(os.pathsep) if entry]
                # One per-call temporary folder for every convention: TMPDIR (POSIX tools,
                # Python), TEMP/TMP (Windows native and .NET tools) and Claude's own variable.
                environment.update(PATH=os.pathsep.join(dict.fromkeys([*python_bins, *bins, *host_path])),
                    TMPDIR=str(cli_temporary), TEMP=str(cli_temporary), TMP=str(cli_temporary),
                    CLAUDE_CODE_TMPDIR=str(cli_temporary), PYTHONDONTWRITEBYTECODE="1",
                    GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
                stage = "cli_preflight"
                version = _run_cli_preflight([str(self._cli_path), "--version"], environment=environment,
                                             deadline=invocation_deadline(host, request)).strip()
                help_text = _run_cli_preflight([str(self._cli_path), "--help"], environment=environment,
                                               deadline=invocation_deadline(host, request))
                required = ("--safe-mode", "--tools", "--allowedTools", "--permission-mode", "--settings", "--effort",
                            "--strict-mcp-config", "--add-dir")
                if any(flag not in help_text for flag in required) or (native_schema is not None and "--json-schema" not in help_text):
                    raise ValueError("configured Claude CLI lacks a required option")
                write_directories = ([str(cwd / write_rule[2:].removesuffix("/**")) if write_rule != "./**" else str(cwd)]
                                     if write_rule is not None else [])
                # Agent calls get the code-owned locations of this Attempt ahead of the unchanged
                # frozen envelope; tool-free calls send the envelope alone.
                locations = None if profile.execution_mode == "tool_free" else invocation_locations(
                    working_directory=cwd, writable_directories=write_directories, materials_directory=materials,
                    read_only_dependencies=self._dependencies, runtime_python=python)
                prompt = compose_invocation_prompt(locations, prepared.prompt)
                recorded_environment = {key: ("<redacted>" if key in forwarded else value) for key, value in environment.items()}
                allowed_index = argv.index("--allowedTools") + 1
                allowed_rules = argv[allowed_index:argv.index("--settings")]
                trace.update(argv=argv, environment=recorded_environment, cwd=str(cwd), cli_version=version, settings=settings,
                             actual_prompt=prompt, material_sha256=material_hashes, timeout_seconds=profile.timeout_seconds,
                             executor_adapter_id=self.executor_adapter_id, executor_adapter_revision=self.executor_adapter_revision,
                             prompt_envelope_sha256=request.prompt_envelope_sha256,
                             execution_profile_ref=profile.release_ref, execution_profile_sha256=profile.release_sha256)
                context_suppressed = (["claude_md", "auto_memory", "hooks", "plugins"] if any(
                    key in settings for key in ("claudeMdExcludes", "disableAllHooks")) else ["safe_mode"])
                trace["effective_configuration"] = {
                    "tools": [tool for tool in argv[argv.index("--tools") + 1].split(",") if tool]
                             + [MCP_TOOL_PREFIX + name for name in declared],
                    "allowed_rules": [rule for rule in allowed_rules if rule],
                    "permission_mode": "dontAsk",
                    "read_directories": [str(cwd), *settings.get("permissions", {}).get("additionalDirectories", [])],
                    "write_directories": write_directories,
                    "invocation_locations": locations,
                    "mcp_connections": [{"name": name, "config_sha256": hashlib.sha256(json.dumps(
                        entry["config"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
                        "environment_keys": list(entry["environment_keys"])} for name, entry in connections.items()],
                    "context_suppressed": context_suppressed,
                    "cli_program": str(self._cli_path),
                    "cli_version": version,
                    "command_scope": ("none" if "shell" not in profile.tool_policy else "explicit_extension"
                                      if extension_call else "native_read_only_plus_permits"),
                    "command_permits": list(self._command_permits) if "shell" in profile.tool_policy else [],
                    "file_io": "native_tools" if set(profile.tool_policy) & {"read", "search", "edit"} else "none",
                }
                if self._native_project:
                    # Configuration intent only; what Claude actually discovered and used is in the
                    # raw init event (tools, skills, plugins, mcp_servers) and the tool events.
                    trace["native_project"] = True
                    trace["effective_configuration"].update(
                        native_project=True, context_enabled=["claude_md", "skills"],
                        context_suppressed=["auto_memory", "hooks"], command_scope="host",
                        filesystem_scope="task_directed")
                stage = "provider_invocation"
                command_cleanup_error = None
                try:
                    launch_options = {}
                    if request.self_test_binding_ref is not None:
                        launch_options["launch_guard"] = lambda launch: host.guard_self_test_launch(
                            request, launch, adapter=self, artifact_host=self._artifacts,
                            workspace_root=self._workspace_root, read_only_dependencies=self._dependencies)
                        launch_options.update(cancellation)
                    if channel is not None:
                        launch_options["progress_channel"] = channel
                    process = self._run(argv=argv, prompt=prompt, cwd=cwd, environment=environment,
                                        timeout_seconds=profile.timeout_seconds, on_stdout_line=observe,
                                        deadline_monotonic=invocation_deadline(host, request),
                                        **launch_options)
                    trace.update(exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr,
                                 process_output_complete=getattr(process, "output_streams_closed", True) is True,
                                 **captured_cli_streams(process))
                    if isinstance(result, dict) and isinstance(result.get("permission_denials"), list):
                        trace["permission_denials"] = result["permission_denials"]
                finally:
                    if provider_tools is not None:
                        try:
                            provider_tools.close()
                        except Exception as exc:
                            command_cleanup_error = exc
                            trace["provider_tool_cleanup_error"] = {"error_type": type(exc).__name__, "message": str(exc)}
                        finally:
                            trace["local_callback_calls"] = provider_tools.records
                            trace["local_callback_cli_tools"] = sorted(provider_tools.cli_tools)
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
                if local_commands is not None or provider_tools is not None:
                    stage = "local_command_validation"
                    if command_cleanup_error is not None:
                        raise command_cleanup_error
                    if local_commands is not None:
                        local_commands.validate_completion()
                    if provider_tools is not None:
                        provider_tools.validate_completion()
                    stage = "provider_invocation"
        except (Exception, CliProcessInterrupted) as exc:
            if cli_process_stop_confirmed(exc) is False:
                stop_unconfirmed = True
            trace.update(stage=stage, error=str(exc))
            # Command validation propagates another process's failure. Its bytes
            # are in local_command_calls; keep the Provider's captured log intact.
            if stage != "local_command_validation" and isinstance(exc, (
                    subprocess.CalledProcessError, subprocess.TimeoutExpired, CliProcessInterrupted)):
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
            if isinstance(exc, CliProcessStartupError):
                # No capture exists; keep whether a created child was conclusively stopped.
                trace["process_created"] = exc.process_created
                if exc.cleanup_error is not None:
                    trace["cleanup_error"] = exc.cleanup_error
            if event_error:
                trace["event_error"] = event_error
            if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) and invocation_budget_expired(host, request):
                fail("timeout", "claude_cli_timeout", "Runtime work deadline or Provider timeout expired", cause=exc)
            if isinstance(exc, SelfTestResourceUnavailableError):
                fail("authorization", "self_test_resources_unavailable", str(exc), cause=exc)
            if policy_refusal:
                trace["policy_refusal_reason"] = policy_refusal
                fail("policy_violation", "ADAPTER_POLICY_VIOLATION", policy_refusal, cause=exc)
            if isinstance(exc, LocalResourceError):
                fail("dependency_unavailable" if exc.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED" else
                     "policy_violation" if exc.error_code == "ADAPTER_POLICY_VIOLATION" else "schema",
                     exc.error_code, str(exc), cause=exc)
            if stage in {"local_command_preparation", "provider_tool_preparation"} and isinstance(exc, NotImplementedError):
                fail("dependency_unavailable", "ADAPTER_CAPABILITY_UNSUPPORTED", str(exc), cause=exc)
            if stage == "local_command_validation":
                fail("unknown", "ADAPTER_CONFORMANCE_FAILED", str(exc), cause=exc)
            if stage == "authorized_input_read":
                raise
            if isinstance(exc, CliProcessInterrupted):
                # The owned CLI preflight now stops on user interruption too; still a cancellation.
                fail("cancelled", "claude_cli_interrupted", "Claude CLI interrupted by user",
                     terminal_status="cancelled", cause=exc)
            if stage != "provider_invocation":
                cls = "schema" if stage == "material_preparation" and isinstance(exc, ValueError) else "dependency_unavailable"
                fail(cls, "ADAPTER_REQUEST_INVALID" if cls == "schema" else "ADAPTER_BINDING_UNAVAILABLE", str(exc), cause=exc)
            if isinstance(exc, subprocess.TimeoutExpired):
                fail("timeout", "claude_cli_timeout", "Claude CLI exceeded the configured timeout", retry="retry_allowed", cause=exc)
            if event_error and trace.get("stop_reason") in (None, "observer_stopped"):
                fail("provider", "claude_cli_result_missing_or_invalid", event_error, cause=exc)
            fail("transport", "claude_cli_process_failed", str(exc), cause=exc)
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
            message = event_error or "CLI initialization or terminal result is missing"
            reason = result.get("startup_failure_reason") if result and not event_error else None
            if isinstance(reason, str) and reason:
                # Claude's own structured startup failure explains the missing init event.
                errors = result.get("errors")
                native = "; ".join(map(str, errors)) if isinstance(errors, list) else ""
                message = f"CLI initialization is missing: Claude reported startup failure {reason}" + (
                    f": {native}" if native else "")
            fail("provider", "claude_cli_result_missing_or_invalid", message)
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
        trace_ref, trace_sha256 = commit_attempt_trace_json(self._artifacts, request, trace)
        return completed_adapter_result(profile=profile, request=request, outputs=(submission,),
            tool_operation_ref_ids=(), trace_ref=trace_ref, trace_sha256=trace_sha256,
            **usage)


__all__ = ["ClaudeAdapter"]
