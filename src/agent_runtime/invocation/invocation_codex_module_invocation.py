"""Codex CLI adapters for registered Agent Runtime Modules.

The adapters implement the canonical ``AuthorizedAgentExecutionAdapter``
protocol. Both send one complete, precommitted Prompt Envelope assembled from
authorized inputs. The tool-free adapter (v4) consumes the final response
directly. The workspace adapter (v3) runs the agent workspace environment:
the Codex shell tool works in one main folder per Attempt under a permission
profile generated for that call, and declared local commands arrive through
the Runtime command proxy. Neither adapter gives the provider a database
connection or network-enabled tool execution. Expected provider failures
return a typed failed result; exceptions are adapter conformance failures.
Outputs are staged through the host and become authoritative only through
Runtime finalization.
"""

from __future__ import annotations

from .invocation_process_execution import invocation_deadline, invocation_budget_expired, preflight_timeout

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
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
from ..foundation.foundation_json_encoding import cli_stream_bytes, decode_cli_event
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
from .invocation_cli_logging import captured_cli_streams
from .invocation_process_execution import (
    CliProcessError, CliProcessInterrupted, _capture_cli_interrupts, run_cli_process,
)
from .invocation_codex_environment import prepare_codex_environment
from .invocation_process_execution import _runtime_python_executable, _runtime_python_read_roots
from .invocation_local_resource_preparation import (
    LocalResourceError, assert_local_materials_unchanged, parse_local_resources, stage_attempt_materials,
)
from .invocation_local_command_execution import (
    LocalCommandSession, LOCAL_COMMAND_SERVER_NAME, LOCAL_COMMAND_TOOL_NAME, _SYSTEM_READ_ROOTS,
)
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
    cancel_requested: Callable | None = None,
    user_cancel_requested: Callable | None = None,
    deadline_monotonic: float | None = None,
) -> CodexCliInvocationResult:
    """Run v4 with explicit private environment and a launch-time resource guard.

    The same executable supplies its version without a model/authentication call.
    Injected invokers must accept environment and optional launch_guard; there is
    no fallback to the historical four-argument execution contract.
    """
    cancellation = {name: value for name, value in (("cancel_requested", cancel_requested),
        ("user_cancel_requested", user_cancel_requested), ("deadline_monotonic", deadline_monotonic)) if value is not None}
    version = run_cli_process(argv=[argv[0], "--version"], prompt="", cwd=cwd,
        timeout_seconds=min(timeout_seconds, 10), environment=environment,
        max_output_bytes=4096, launch_guard=launch_guard, **cancellation)
    if version.returncode != 0 or not version.stdout.strip():
        raise OSError("Codex CLI version check failed")
    try:
        process = run_cli_process(
            argv=argv, prompt=prompt, cwd=cwd, timeout_seconds=timeout_seconds,
            environment=environment, launch_guard=launch_guard, **cancellation,
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


def _workspace_execution_expectation(profile: ExecutionProfileRelease) -> InvocationExecutionExpectation:
    """Validate the agent workspace combination (tools including shell) for v3.

    Codex has no Read or Grep tools of its own; its shell covers read, search
    and shell, so only a tool policy containing shell maps. A policy with read
    or search alone is reported as unsupported before any resource is opened.
    """
    profile.validate()
    if (profile.executor_adapter_id, profile.executor_adapter_revision, profile.transport_kind,
            profile.provider_id) != ("codex_cli_agent_workspace_executor", "v3", "codex_cli", "openai"):
        raise ValueError("Codex workspace v3 requires its exact binding; prepare an explicit compatible Profile")
    if (profile.execution_mode, profile.semantic_input_delivery_mode, profile.attempt_workspace_policy,
            profile.network_policy, profile.gateway_access_reasons) != ("agent", "inline", "own_draft_read_write", "denied", ()):
        raise ValueError("Codex workspace v3 supports only agent, inline input, a private draft workspace and denied network")
    if not profile.tool_policy or not set(profile.tool_policy) <= {"read", "search", "shell"}:
        raise CodexCapabilityUnsupportedError("Codex workspace v3 supports only read, search and shell tools")
    if "shell" not in profile.tool_policy:
        raise CodexCapabilityUnsupportedError("Codex provides read and search only through its shell tool; allow shell as well")
    return InvocationExecutionExpectation("codex_cli_agent_workspace_executor", "v3", "codex_cli",
                                         "agent", "inline", "own_draft_read_write", "denied", profile.tool_policy)


PERMISSION_PROFILE_NAME = "runtime_attempt"
# Fixed roots no workspace Attempt may read; resolved protected locations are added per call.
_DENIED_ROOTS = (Path("/Users"), Path("/Volumes"), Path("/tmp"), Path("/private/tmp"),
                 Path("/private/var/folders"), Path("/var/folders"))
# Codex permission profiles start from the built-in read-only base; readable
# system directories outside the denied roots are this recorded isolation gap.
ISOLATION_GAPS = ("system_directories_readable",)


class CodexCapabilityUnsupportedError(ValueError):
    """The requested execution cannot be expressed by a Codex implementation."""

    error_code = "ADAPTER_CAPABILITY_UNSUPPORTED"


def codex_program_files(codex_bin: str | Path) -> tuple[Path, ...]:
    """The invoked entry and its resolved file; Codex must read both to start its sandbox helper."""
    entry = Path(codex_bin).absolute()
    return tuple(dict.fromkeys((entry, entry.resolve(strict=True))))


def codex_auth_directories(auth_reference: Path) -> tuple[Path, ...]:
    """Directories of the selected authentication file and of its link target.

    auth_reference is the private state entry that points at the selected
    source; both its target's directory and that directory's resolved form
    are protected, so a relocated or linked credential stays covered.
    """
    source = Path(auth_reference).readlink()
    return tuple(dict.fromkeys((source.parent.absolute(), source.resolve().parent)))


def _credential_locations() -> tuple[Path, ...]:
    home = Path.home()
    return tuple(path.resolve() for path in (home / ".codex", home / ".claude", home / ".claude.json",
                                             home / "Library/Keychains"))


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def check_codex_external_grants(grants: tuple[Path, ...], *, protected: tuple[Path, ...],
                                host_temp_root: Path, workspace_root: Path) -> None:
    """Refuse a read grant that would reopen something the profile denies.

    A more specific grant overrides a parent deny, so a grant that overlaps a
    protected, credential or workspace location, that is a denied or broad
    root, or that contains a denied root or the host temporary root, is
    refused. A grant inside the host temporary root is ordinary (for example a
    test fixture); only its root and ancestors are refused.
    """
    host_temp_root = Path(host_temp_root).resolve()
    closed = (*protected, *_credential_locations(), Path(workspace_root).resolve())
    for grant in grants:
        grant = Path(grant).resolve()
        if any(_overlaps(grant, location) for location in closed if location != host_temp_root):
            raise PermissionError(f"Codex read grant {grant} overlaps private execution state or credentials")
        if grant == Path(grant.anchor) or host_temp_root.is_relative_to(grant) or any(
                root.is_relative_to(grant) for root in _DENIED_ROOTS):
            raise PermissionError(f"Codex read grant {grant} would open a denied root")


def codex_permission_profile(*, main_folder: Path, materials: Path, readable: tuple[Path, ...],
                             program_files: tuple[Path, ...], protected: tuple[Path, ...],
                             host_temp_root: Path, workspace_root: Path) -> dict:
    """Return the Codex permission profile for one workspace Attempt, or raise PermissionError.

    main_folder is the only writable location; materials is this Attempt's
    read-only input tree. Both must lie inside workspace_root, which is denied
    as a whole so other Attempts stay closed. readable holds read-only
    dependencies and Runtime Python roots; program_files are granted as files,
    never as their directories. protected holds the resolved host temporary
    root, Provider private state and authentication directories.
    """
    workspace_root = Path(workspace_root).resolve()
    main_folder, materials = Path(main_folder).resolve(), Path(materials).resolve()
    for own in (main_folder, materials):
        if own == workspace_root or not own.is_relative_to(workspace_root) or any(
                _overlaps(own, location) for location in (*protected, *_credential_locations())
                if location != Path(host_temp_root).resolve()):
            raise PermissionError(f"Codex Attempt folder {own} is outside its workspace or overlaps private state")
    check_codex_external_grants((*readable, *program_files), protected=protected,
                                host_temp_root=host_temp_root, workspace_root=workspace_root)
    filesystem = {str(path): "deny" for path in (*_DENIED_ROOTS, *protected, workspace_root)}
    filesystem.update({str(Path(path).resolve()): "read" for path in (materials, *readable)})
    filesystem.update({str(path): "read" for path in program_files})
    filesystem[str(main_folder)] = "write"
    return {"extends": ":read-only", "filesystem": filesystem, "network": {"enabled": False}}


def command_sandbox_conflicts(*, protected: tuple[Path, ...], command_readable_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Protected locations a declared command could read through its own sandbox.

    Declared commands run in the Runtime command sandbox, whose read roots are
    separate from the Codex profile; a protected location inside one of them
    is not closed by the Codex deny.
    """
    roots = tuple(Path(root).resolve() for root in command_readable_roots)
    return tuple(location for location in protected
                 if any(Path(location).resolve().is_relative_to(root) for root in roots))


def _toml_string(text: str) -> str:
    """Render a TOML basic string that parses back to exactly text.

    Quotation mark, backslash and control characters other than tab are
    escaped; every other character, including non-BMP ones, stays literal.
    A string holding a lone surrogate (a path that is not valid Unicode) has
    no TOML form and is refused rather than altered.
    """
    parts = ['"']
    for character in text:
        code = ord(character)
        if 0xD800 <= code <= 0xDFFF:
            raise ValueError("a path that is not valid Unicode cannot be written to the Codex configuration")
        if character in '"\\':
            parts.append("\\" + character)
        elif (code < 0x20 and character != "\t") or code == 0x7F:
            parts.append(f"\\u{code:04X}")
        else:
            parts.append(character)
    parts.append('"')
    return "".join(parts)


def _toml(value) -> str:
    """Render a JSON-compatible value as a TOML inline value for -c overrides."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_toml_string(str(key))}={_toml(item)}" for key, item in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    return _toml_string(str(value))


_FEATURES_OFF = ("multi_agent", "plugins", "plugin_sharing", "apps", "browser_use", "computer_use",
                 "skill_search", "tool_suggest", "memories", "view_image", "image_generation", "hooks",
                 "workspace_dependencies")


def _command(*, profile, workspace, codex_bin, schema_path, mcp_servers: str, shell_tool: bool,
             permission_options: tuple[str, ...], sandbox: tuple[str, ...]) -> list[str]:
    # The shell lives in the functions namespace; excluding it from code mode
    # removes the shell tool itself (codex-cli 0.153.4), so only v3 keeps it.
    excluded = '["collaboration","clock"]' if shell_tool else '["functions","collaboration","clock"]'
    options = ["project_doc_max_bytes=0", 'approval_policy="never"',
        'cli_auth_credentials_store="file"', "agents.enabled=false", "skills.include_instructions=false",
        "features.code_mode.excluded_tool_namespaces=" + excluded,
        mcp_servers, "features.skip_host_skill_discovery=true", 'web_search="disabled"',
        "features.shell_tool=" + ("true" if shell_tool else "false")]
    options.extend("features." + name + "=false" for name in _FEATURES_OFF)
    options.extend(permission_options)
    argv = [str(codex_bin), "exec", "-m", profile.model_id,
            "-c", "model_reasoning_effort=" + json.dumps(profile.reasoning_profile)]
    for option in options:
        argv.extend(("-c", option))
    argv.extend(("-C", str(workspace), *sandbox, "--skip-git-repo-check",
                 "--ignore-user-config", "--ignore-rules", "--strict-config", "--ephemeral", "--json"))
    if schema_path is not None:
        argv.extend(("--output-schema", str(schema_path)))
    argv.append("-")
    return argv


def _developer_tool_bins(deadline=None) -> list[str]:
    """The Xcode developer tool bin from xcode-select, avoiding the xcrun shims in /usr/bin.

    Returns nothing when unresolved; the other PATH entries still apply.
    """
    try:
        selected = subprocess.run(["/usr/bin/xcode-select", "-p"], capture_output=True, text=True,
                                  timeout=preflight_timeout(10, deadline, "xcode-select"), check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return []
    path = Path(selected) / "usr" / "bin"
    return [str(path)] if selected and path.is_dir() else []


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
    return _command(profile=profile, workspace=workspace, codex_bin=codex_bin, schema_path=schema_path,
                    mcp_servers="mcp_servers={}", shell_tool=False, permission_options=(),
                    sandbox=("-s", "read-only"))


def build_workspace_command(*, profile: ExecutionProfileRelease, main_folder: Path, codex_bin: str,
                            permission_profile: dict, local_commands: LocalCommandSession | None = None,
                            schema_path: Path | None = None) -> list[str]:
    """Render the workspace v3 command for one prepared Attempt, without I/O.

    The shell tool is on as a non-login shell and every other optional
    feature stays off. The generated permission profile replaces -s, which
    Codex does not accept together with a permission profile. With declared
    commands, the Runtime command proxy is the only MCP server and exposes
    only its command tool.

    Args:
        profile: Exact workspace v3 Profile; supplies model and effort.
        main_folder: The Attempt's only writable folder, used as cwd.
        codex_bin: Host-selected CLI executable locator.
        permission_profile: Result of codex_permission_profile for this Attempt.
        local_commands: This Attempt's LocalCommandSession, or None.
        schema_path: Optional prepared native schema file.
    Returns:
        A fresh argv list for one codex exec invocation.
    Raises:
        ValueError: Profile targets another binding or capability set.
        TypeError: local_commands is not the exact Runtime session.
    """
    _workspace_execution_expectation(profile)
    if local_commands is not None and type(local_commands) is not LocalCommandSession:
        raise TypeError("local_commands must be the exact Runtime LocalCommandSession")
    mcp_servers = "mcp_servers={}"
    if local_commands is not None:
        server = local_commands.mcp_config["mcpServers"][LOCAL_COMMAND_SERVER_NAME]
        mcp_servers = f"mcp_servers.{LOCAL_COMMAND_SERVER_NAME}=" + _toml({
            "command": server["command"], "args": server["args"], "enabled_tools": [LOCAL_COMMAND_TOOL_NAME],
            "default_tools_approval_mode": "approve", "tool_timeout_sec": server["timeout"] // 1000})
    name = PERMISSION_PROFILE_NAME
    # A non-login shell does not source login profiles for each command.
    permission_options = ("allow_login_shell=false", f"default_permissions={json.dumps(name)}",
        f"permissions.{name}.extends={_toml(permission_profile['extends'])}",
        f"permissions.{name}.filesystem={_toml(permission_profile['filesystem'])}",
        f"permissions.{name}.network.enabled={_toml(permission_profile['network']['enabled'])}")
    return _command(profile=profile, workspace=main_folder, codex_bin=codex_bin, schema_path=schema_path,
                    mcp_servers=mcp_servers, shell_tool=True, permission_options=permission_options, sandbox=())


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
    """Read one turn's terminal, final message and reported usage.

    thread/turn start markers may be absent in legacy injected streams. When
    present they must unambiguously precede this turn's response and terminal.
    Tool items and unrelated malformed lines do not determine the run result;
    the raw stream remains available for explicit Inspection.
    Top-level error events can report retries and remain in that raw stream.
    Process exit, the unique turn terminal and final output determine success.
    """
    issues, terminals = [], []
    thread_started = turn_started = seen_response = False
    final_text = None
    for index, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        try:
            event = decode_cli_event(line.decode("utf-8"))
        except (UnicodeError, ValueError):
            continue
        kind = event.get("type")
        if kind == "thread.started":
            if thread_started or turn_started or seen_response or terminals:
                issues.append(f"unexpected_thread_start:{index}")
            thread_started = True
        elif kind == "turn.started":
            if turn_started or seen_response or terminals:
                issues.append(f"unexpected_turn_start:{index}")
            turn_started = True
        elif kind in {"turn.completed", "turn.failed"}:
            terminals.append(event)
            if kind == "turn.failed" and not isinstance(event.get("error"), dict):
                issues.append(f"invalid_failure_terminal:{index}")
        elif kind == "item.completed":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                seen_response = True
                if terminals:
                    issues.append(f"response_after_terminal:{index}")
                final_text = item.get("text")
    if len(terminals) != 1:
        issues.append("terminal_event_missing_or_duplicated")
    usage, usage_issues = _parse_usage(terminals)
    return {"terminal": terminals[0] if len(terminals) == 1 else None,
            "issues": issues, "final_text": final_text,
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


class _CodexCliExecutorBase:
    """Shared exact-envelope execution for one admitted Codex CLI mode."""

    executor_adapter_id = ""
    executor_adapter_revision = ""
    expected_execution_mode = "tool_free"
    expected_semantic_input_delivery_mode = "inline"
    expected_attempt_workspace_policy = "none"
    expected_network_policy = "denied"
    shell_tool_enabled = False
    workspace_execution = False
    descriptor_admission_state = "integration_tested"

    @staticmethod
    def execution_expectation(profile: ExecutionProfileRelease) -> InvocationExecutionExpectation:
        """Check a Profile against this implementation without opening resources."""
        return _execution_expectation(profile)

    def __init__(
        self,
        *,
        release_registry: RuntimeReleaseRegistry,
        artifact_host: ModuleArtifactHost,
        workspace_root: Path,
        invoker: CodexCliInvoker = _default_invoke,
        codex_bin: str | None = None,
        auth_file: Path | None = None,
        read_only_dependencies: tuple[Path, ...] = (),
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
                An active run budget additionally requires deadline_monotonic;
                it is omitted when the host has no enclosing deadline. Optional
                cancellation ports are passed only when supplied by the host.
                The default uses run_cli_process. An old four-argument callable is
                rejected before resource preparation; no unguarded fallback is tried.
            codex_bin: Optional explicit installed CLI. None uses the existing host
                executable resolver at invocation; this constructor probes no program.
            auth_file: Optional single regular file authentication source. None uses
                the host's original CODEX_HOME/auth.json, or standard .codex/auth.json
                when CODEX_HOME is unset. Resolution occurs only during invocation.
                Missing file authentication does not fall back to keychain/API keys.
            read_only_dependencies: Workspace v3 only. Explicit trusted existing
                directories the Agent's shell may read; never task-supplied.
                Tool-free v4 accepts none.
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
        if read_only_dependencies and not self.workspace_execution:
            raise ValueError("Tool-free Codex execution takes no read-only dependencies")
        self._dependencies = tuple(Path(item).resolve(strict=True) for item in read_only_dependencies)
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
        """Execute one invocation with live resources and private Provider state.

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
            streams and terminal metadata stay in the private Attempt trace.
            Inspection derives detailed tool views on request; those views do not
            determine ordinary execution failure or retry.
            CLI error notifications may be transient; process exit and the unique
            turn terminal are checked before validating the final output.
        Raises:
            ValueError: Invalid request/Profile or incompatible injected invoker.
            PermissionError: Missing live resource/operation evidence, or (workspace
                v3) a read grant that would reopen a denied, credential or private
                location, or declared commands that could read a protected
                location. Provider is not started on these failures.
            Exception: Native content/record-store integrity failures retain their owner.
        Effects:
            Resolves one authentication file, prepares private state and one Attempt,
            launches the selected CLI with its fixed configuration, and stages output
            and trace through the supplied ports. Workspace v3 also stages materials,
            records main_folder, codex_permission_profile and isolation_gaps, and
            runs declared commands in the Runtime command session. No login,
            registration, model fallback, credential copy-back or global
            environment modification is performed.
        """
        if type(request) is not AuthorizedAgentExecutionRequest:
            raise ValueError("request must be an exact AuthorizedAgentExecutionRequest")
        request.validate()
        profile = self._release_registry.get_execution_profile(request.execution_profile_ref,
                                                              request.execution_profile_sha256)
        expectation = self.execution_expectation(profile)
        if self.workspace_execution:
            check_codex_external_grants((*_runtime_python_read_roots(), *self._dependencies), protected=(),
                host_temp_root=Path(tempfile.gettempdir()), workspace_root=self._workspace_root)
        def validate_self_test(value):
            validator = getattr(host, "validate_self_test_binding", None)
            guard = getattr(host, "guard_self_test_launch", None)
            if not callable(validator) or not callable(guard):
                raise SelfTestResourceUnavailableError("Codex self-test requires live Runtime resource validation and launch guard")
            validator(value, adapter=self, artifact_host=self._artifact_host, workspace_root=self._workspace_root,
                      **self._host_resources())
        from .invocation_tool_definition import self_test_cancellation_callbacks
        invocation_controls = self_test_cancellation_callbacks(host, request)
        deadline = invocation_deadline(host, request)
        if deadline is not None:
            invocation_controls["deadline_monotonic"] = deadline
        try:
            inspect.signature(self._invoker).bind(argv=[], prompt="", cwd=self._workspace_root,
                timeout_seconds=profile.timeout_seconds, environment={}, launch_guard=None,
                **invocation_controls)
        except (TypeError, ValueError) as exc:
            raise ValueError("Codex v4 invoker must accept explicit environment, optional launch_guard "
                "and the active host controls: " + ", ".join(invocation_controls)) from exc
        prepared = prepare_registered_invocation_context(
            request=request,
            release_registry=self._release_registry,
            artifact_host=self._artifact_host,
            expectation=expectation, self_test_validator=validate_self_test,
        )
        if self.shell_tool_enabled != ("shell" in prepared.profile.tool_policy):
            raise PermissionError("Codex actual Shell capability differs from the explicit Profile tool_policy")
        with _capture_cli_interrupts() as interrupted:
            result, pending_detail, cleanup_error = None, None, None
            try:
                with ExitStack() as cleanup:
                    try:
                        result = self._execute_prepared(request, host, prepared, cleanup, invocation_controls=invocation_controls)
                    except TerminalAdapterFailure as failure:
                        result, pending_detail = failure.result, failure.pending_failure_detail
            except Exception as exc:
                if result is None:
                    raise
                cleanup_error = exc
            return finalize_adapter_result(
                artifact_host=self._artifact_host, request=request, result=result,
                pending_failure_detail=pending_detail,
                interruption_requested=lambda: interrupted.requested or (
                    "user_cancel_requested" in invocation_controls and invocation_controls["user_cancel_requested"]()),
                cleanup_error=cleanup_error, interruption_code="codex_cli_interrupted",
                cleanup_failure_code="codex_cli_cleanup_failed",
            )

    def _host_resources(self) -> dict:
        """Resource arguments the kernel host checks; v4 keeps its original call."""
        return {"read_only_dependencies": self._dependencies} if self.workspace_execution else {}

    def _execute_prepared(
        self,
        request: AuthorizedAgentExecutionRequest,
        host: AuthorizedAgentExecutionHost,
        prepared,
        cleanup: ExitStack,
        *, invocation_controls: dict,
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
            trace["usage_issues"] = parsed["usage_issues"]

        def fail(failure_class, failure_code, message, *, retry="retry_denied", cause=None,
                 terminal_status="failed"):
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
        cwd, materials, local_commands, resources_body = workspace, None, None, None
        material_hashes, policy_refusal = {}, None
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
            if self.workspace_execution:
                # One main folder per Attempt: scratch is the cwd, the only
                # writable location, and holds TMPDIR; materials stay read-only.
                stage = "material_preparation"
                work = workspace / "work"
                materials, cwd = work / "materials", work / "scratch"
                for directory in (work, materials, cwd, cwd / ".tmp"):
                    if directory.is_symlink():
                        policy_refusal = "Codex workspace directory is a symlink"
                        raise AttemptWorkspaceConflictError(policy_refusal)
                    directory.mkdir(mode=0o700, exist_ok=True)
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
            else:
                argv = build_command(profile=profile, workspace=workspace, codex_bin=executable,
                                     schema_path=schema_path)
            stage = "provider_environment"
            host_temp_root = Path(tempfile.gettempdir()).resolve()
            environment = cleanup.enter_context(prepare_codex_environment(
                workspace_root=self._workspace_root, auth_file=self._auth_file,
                source_environment=dict(os.environ)))
            if self.workspace_execution:
                stage = "isolation_preparation"
                state = Path(environment["CODEX_HOME"]).resolve()
                protected = tuple(dict.fromkeys((host_temp_root, state, *codex_auth_directories(state / "auth.json"))))
                python = _runtime_python_executable()
                python_roots = _runtime_python_read_roots()
                permission = codex_permission_profile(
                    main_folder=cwd, materials=materials,
                    readable=tuple(dict.fromkeys((*python_roots, *self._dependencies))),
                    program_files=codex_program_files(executable), protected=protected,
                    host_temp_root=host_temp_root, workspace_root=self._workspace_root)
                if resources_body is not None and parse_local_resources(resources_body)["commands"]:
                    exposed = command_sandbox_conflicts(protected=protected, command_readable_roots=(
                        *map(Path, _SYSTEM_READ_ROOTS), *python_roots, *self._dependencies, materials / "source", cwd))
                    if exposed:
                        raise PermissionError("Declared commands could read protected locations: "
                                              + ", ".join(map(str, exposed)))
                    stage = "local_command_preparation"
                    local_commands = cleanup.enter_context(LocalCommandSession(
                        request=request, profile=profile, host=host, adapter=self,
                        artifact_host=self._artifact_host, workspace_root=self._workspace_root,
                        resources_body=resources_body, source_root=materials / "source", scratch_root=cwd,
                        read_only_dependencies=self._dependencies))
                argv = build_workspace_command(profile=profile, main_folder=cwd, codex_bin=executable,
                    permission_profile=permission, local_commands=local_commands, schema_path=schema_path)
                bins = [str(dep / "bin") for dep in self._dependencies if (dep / "bin").is_dir()]
                environment.update(
                    TMPDIR=str(cwd / ".tmp"),
                    PATH=os.pathsep.join(dict.fromkeys([str(python.parent), *bins, *_developer_tool_bins(invocation_deadline(host, request)),
                                                        "/usr/bin", "/bin"])),
                    GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")
                trace.update(main_folder=str(cwd), codex_permission_profile=permission,
                             isolation_gaps=list(ISOLATION_GAPS), material_sha256=material_hashes)
            trace.update(argv=list(argv), cwd=str(cwd),
                         prompt_envelope_sha256=request.prompt_envelope_sha256,
                         environment_keys=sorted(environment), provider_state_isolated=True)
            launch_guard = None
            if request.self_test_binding_ref is not None:
                launch_guard = lambda launch: host.guard_self_test_launch(
                    request, launch, adapter=self, artifact_host=self._artifact_host,
                    workspace_root=self._workspace_root, **self._host_resources())
            stage = "provider_invocation"
            command_cleanup_error = None
            try:
                result = self._invoker(argv=argv, prompt=prompt, cwd=cwd,
                                       timeout_seconds=profile.timeout_seconds, environment=environment,
                                       launch_guard=launch_guard, **invocation_controls)
                if type(result) is not CodexCliInvocationResult:
                    raise TypeError("Codex CLI invoker returned an invalid result")
                capture(result, complete=result.process_output_complete is True)
                trace["cli_version"] = result.cli_version
                if type(result.returncode) is not int or type(result.process_output_complete) is not bool:
                    raise TypeError("Codex CLI invoker returned invalid process metadata")
            finally:
                if local_commands is not None:
                    try:
                        local_commands.close()
                    except Exception as exc:
                        command_cleanup_error = exc
                        trace["local_command_cleanup_error"] = {"error_type": type(exc).__name__, "message": str(exc)}
                    finally:
                        trace["local_command_calls"] = local_commands.records
                        trace["local_command_cli_tool_name"] = LOCAL_COMMAND_TOOL_NAME
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
        except (Exception, KeyboardInterrupt) as exc:
            trace.update(stage=stage, error=str(exc))
            if stage == "isolation_preparation" and isinstance(exc, PermissionError):
                raise  # Before any Provider or command process; the caller sees the refusal.
            if getattr(exc, "cli_version", None) is not None:
                trace["cli_version"] = exc.cli_version
            if stage != "local_command_validation" and (
                    isinstance(exc, (subprocess.CalledProcessError, subprocess.TimeoutExpired, CliProcessInterrupted))
                    or any(getattr(exc, name, None) is not None
                           for name in ("stdout", "stderr", "stdout_bytes", "stderr_bytes"))):
                capture(exc, complete=False)
            if isinstance(exc, KeyboardInterrupt):
                fail("cancelled", "codex_cli_interrupted", "Codex CLI interrupted",
                     terminal_status="cancelled", cause=exc)
            if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) and invocation_budget_expired(host, request):
                fail("timeout", "codex_cli_timeout", "Runtime work deadline or Provider timeout expired", cause=exc)
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
            if stage == "material_preparation" and isinstance(exc, ValueError):
                fail("schema", "ADAPTER_REQUEST_INVALID", str(exc), cause=exc)
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
        if policy_refusal:
            trace["policy_refusal_reason"] = policy_refusal
            fail("policy_violation", "ADAPTER_POLICY_VIOLATION", policy_refusal)
        if not result.process_output_complete:
            fail("transport", "codex_cli_process_failed", "Codex process output capture is incomplete")
        if result.returncode != 0:
            fail("provider", "codex_cli_nonzero_exit", f"Codex CLI failed with return code {result.returncode}",
                 retry="retry_allowed")
        if parsed["issues"]:
            fail("provider", "codex_cli_result_missing_or_invalid", "; ".join(parsed["issues"]))
        if parsed["terminal"]["type"] == "turn.failed":
            fail("provider", "codex_cli_error_result", "Codex reported a failed turn", retry="retry_allowed")
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
    """Execute an agent workspace Module (tools including shell) with the Codex shell tool.

    Each Attempt has one main folder, its private scratch directory: the cwd,
    the only writable location and the home of TMPDIR. A permission profile
    generated per call keeps materials, read-only dependencies, Runtime Python
    and the Codex program readable, closes user trees, external volumes, shared
    and host temporary directories, and disables tool network. Other system
    directories remain readable (recorded in isolation_gaps). Declared local
    commands run through the Runtime command proxy, the only MCP server.
    The historical v2 Profile stays readable and is refused by binding.
    """

    executor_adapter_id = "codex_cli_agent_workspace_executor"
    executor_adapter_revision = "v3"
    expected_execution_mode = "agent"
    expected_attempt_workspace_policy = "own_draft_read_write"
    shell_tool_enabled = True
    workspace_execution = True

    @staticmethod
    def execution_expectation(profile: ExecutionProfileRelease) -> InvocationExecutionExpectation:
        """Check a Profile against workspace v3 without opening resources."""
        return _workspace_execution_expectation(profile)


__all__ = [
    "build_command",
    "build_workspace_command",
    "codex_permission_profile",
    "CodexCapabilityUnsupportedError",
    "CodexCliInvocationResult",
    "CodexCliInvoker",
    "CodexCliAgentWorkspaceModuleExecutor",
    "CodexCliModuleExecutor",
    "ModuleArtifactHost",
]
