"""Codex CLI adapters for registered Agent Runtime Modules.

The adapters implement the canonical ``AuthorizedAgentExecutionAdapter``
protocol. Both send one complete, precommitted Prompt Envelope assembled from
authorized inputs. The tool-free adapter (v5) consumes the final response
directly. The workspace adapter (v5) runs agent Modules: with a recorded
``shell`` the Codex shell tool works in the declared task directory using the
configured permission profile or explicitly selected host mode; an MCP-only Module runs with the
shell tool off. Codex uses the host's own login and configuration (CODEX_HOME,
config.toml, execpolicy rules); Runtime adds only explicit ``-c`` overrides for
context, approval, the declared MCP connections and the declared directory
scope. Declared local commands arrive through the Runtime command proxy only
when explicitly selected. Expected provider failures return a typed failed
result; exceptions are adapter conformance failures. Outputs are staged
through the host and become authoritative only through Runtime finalization.
"""

from __future__ import annotations

from .invocation_host_platform_selection import HOST_PLATFORM, private_temporary_directory

from .invocation_process_execution import invocation_deadline, invocation_budget_expired, preflight_timeout

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import json
import inspect
import os
from pathlib import Path
import re
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
    CliProcessError, CliProcessInterrupted, CliProcessTimeout, _capture_cli_interrupts,
    cli_process_stop_confirmed, run_cli_process,
)
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
    validate_project_directory,
)
from .invocation_tool_definition import declared_mcp_connections


_APP_BUNDLE_BIN = "/Applications/Codex.app/Contents/Resources/codex"
# Host variables passed to every Codex process. CODEX_HOME passes through when the
# host sets it, so Codex reads the host's own login and configuration.
_ENVIRONMENT_KEYS = frozenset({
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TZ",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "CODEX_CA_CERTIFICATE", "CODEX_HOME",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
})


@dataclass(frozen=True)
class CodexCliInvocationResult:
    """Captured process facts; three-field injected results remain text-only.

    Byte fields contain the observed streams, not a redacted display. A normal
    return can report incomplete capture but cannot thereby authorize output.
    It is returned only after the invoker confirms owned process cleanup. Capture
    exceptions retain their partial streams, stop reason and cleanup uncertainty.
    """

    returncode: int
    stdout: str
    stderr: str
    stdout_bytes: bytes | None = None
    stderr_bytes: bytes | None = None
    process_output_complete: bool = True
    cli_version: str | None = None
    host_mcp_disabled: tuple[str, ...] = ()
    host_mcp_overlaid: tuple[str, ...] = ()


CodexCliInvoker = Callable[..., CodexCliInvocationResult]


def _resolve_codex_bin() -> str:
    explicit = os.environ.get("CODEX_CLI_BIN", "").strip()
    if explicit and Path(explicit).is_file():
        return explicit
    discovered = HOST_PLATFORM.resolve_cli_program("codex", os.environ)
    if discovered:
        return discovered
    if Path(_APP_BUNDLE_BIN).is_file():
        return _APP_BUNDLE_BIN
    raise RuntimeError("Codex CLI is not installed or visible on PATH")


# Host Codex MCP server names that a -c dotted key can address without quoting.
_CODEX_MCP_KEY = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MCP_LIST_OUTPUT_LIMIT = 1 << 20


def _codex_config_overrides(argv: list[str]) -> list[str]:
    """The -c pairs of a codex exec argv, in order."""
    return [part for index, value in enumerate(argv[:-1]) if value == "-c" for part in ("-c", argv[index + 1])]


def _declared_server_name(option: str) -> str | None:
    """The name a -c mcp_servers.<name>=... value supplies, else None."""
    if option.startswith("mcp_servers.") and "=" in option:
        key = option.split("=", 1)[0].split(".")
        if len(key) == 2:
            return key[1]
    return None


def _declared_codex_mcp_names(argv: list[str]) -> set[str]:
    """Names this argv adds through -c mcp_servers.<name>=...: the call's declared servers."""
    return {name for index, value in enumerate(argv[:-1]) if value == "-c"
            and (name := _declared_server_name(argv[index + 1])) is not None}


def _without_declared_servers(overrides: list[str]) -> list[str]:
    """The -c pairs without Runtime's own server entries: the host's configuration as the call sees it."""
    kept = []
    for index in range(0, len(overrides), 2):
        if _declared_server_name(overrides[index + 1]) is None:
            kept.extend(overrides[index:index + 2])
    return kept


MCP_LISTING_PROBE = "codex mcp list"


def _without_probe_content(failure: BaseException, codex_bin: str) -> None:
    """Remove the listing's captured bytes and -c values from a runner failure, in place.

    The failure keeps its own type, exit code, stop reason and cleanup facts, so
    the call is classified and its stop certainty handled exactly as before. Its
    streams (transport details, possibly credentials) and the probe command line
    (declared connection tables) are dropped, so nothing of the listing reaches
    the Attempt trace or is parsed as provider output.
    """
    for name, empty in (("output", ""), ("stderr", ""), ("stdout_bytes", b""), ("stderr_bytes", b"")):
        if hasattr(failure, name):
            setattr(failure, name, empty)
    if isinstance(failure, KeyboardInterrupt) and hasattr(failure, "stdout"):
        failure.stdout = ""  # CliProcessInterrupted keeps its own stdout attribute.
    if hasattr(failure, "cmd"):
        failure.cmd = [codex_bin, "mcp", "list", "--json"]


def _listed_codex_mcp_servers(codex_bin: str, overrides: list[str], *, cwd: Path, environment: dict[str, str],
                              timeout_seconds: int, launch_guard, cancellation: dict) -> dict[str, bool]:
    """Names and enabled flags from the selected binary's own ``codex mcp list --json``.

    Same cwd, environment and -c overrides as the exec call, so the listing is the
    configuration that call would load. Only name and enabled are kept; the rest
    of the listing (transport details) is discarded and never recorded, including
    when the listing process fails (see _without_probe_content).
    """
    try:
        process = run_cli_process(argv=[codex_bin, *overrides, "mcp", "list", "--json"], prompt="", cwd=cwd,
            timeout_seconds=min(timeout_seconds, 30), environment=environment,
            max_output_bytes=_MCP_LIST_OUTPUT_LIMIT, launch_guard=launch_guard, **cancellation)
    except BaseException as failure:
        _without_probe_content(failure, codex_bin)
        raise
    if process.returncode != 0:
        raise OSError(f"Codex CLI could not list its configured MCP servers (exit {process.returncode})")
    try:
        listed = json.loads(process.stdout or "")
    except ValueError:
        # Not chained: the decode error holds the listing text.
        raise OSError("Codex CLI MCP server list is not JSON") from None
    if type(listed) is not list or any(type(item) is not dict or type(item.get("name")) is not str
                                       or type(item.get("enabled")) is not bool for item in listed):
        raise OSError("Codex CLI MCP server list has an unexpected shape")
    return {item["name"]: item["enabled"] for item in listed}


def select_codex_mcp_servers(argv: list[str], *, cwd: Path, environment: dict[str, str], timeout_seconds: int,
                             launch_guard: Callable | None = None,
                             **cancellation) -> tuple[list[str], tuple[str, ...], tuple[str, ...]]:
    """Keep exactly the declared MCP servers enabled for one codex exec call.

    The host configuration loads normally, and ``-c mcp_servers={}`` does not
    remove servers inherited from config.toml (codex-cli 0.166.0). The selected
    binary therefore lists the servers the host configuration enables for this
    call (same cwd, environment and -c values, without Runtime's own server
    entries); each enabled server the call does not declare gets
    ``-c mcp_servers.<name>.enabled=false``. A declared name that the host
    configuration also defines is overlaid by Codex (host fields plus the
    supplied ones); it is reported, not resolved. A second listing with every
    override must show exactly the declared servers enabled, or the call is
    refused before exec. Only names and enabled flags are used; host files are
    not read or written, and Runtime starts or queries no MCP server itself.

    Returns:
        The argv to execute, the sorted names disabled for this call, and the
        sorted declared names overlaid on a host-configured server.
    Raises:
        OSError: The listing exited nonzero or was not understood, a name cannot
            be addressed by a -c key, or the CLI would enable a different server
            set. The executor records this pre-exec refusal as
            dependency_unavailable / ADAPTER_BINDING_UNAVAILABLE.
        Exception: A failure of a listing process itself (timeout, output
            limit, stream or stop failure, interruption, start failure) keeps the
            runner's own type and stop facts, without the listing's bytes.
    """
    declared = _declared_codex_mcp_names(argv)
    overrides = _codex_config_overrides(argv)
    probe = dict(cwd=cwd, environment=environment, timeout_seconds=timeout_seconds,
                 launch_guard=launch_guard, cancellation=cancellation)
    listed = _listed_codex_mcp_servers(argv[0], _without_declared_servers(overrides), **probe)
    overlaid = tuple(sorted(declared & set(listed)))
    undeclared = sorted(name for name, enabled in listed.items() if enabled and name not in declared)
    unaddressable = [name for name in undeclared if not _CODEX_MCP_KEY.fullmatch(name)]
    if unaddressable:
        raise OSError(f"Codex host MCP server {unaddressable[0]!r} cannot be disabled by a -c override; "
                      "rename it in the host configuration")
    disables = [part for name in undeclared for part in ("-c", f"mcp_servers.{name}.enabled=false")]
    if disables or declared:
        verified = _listed_codex_mcp_servers(argv[0], overrides + disables, **probe)
        enabled = {name for name, value in verified.items() if value}
        if enabled != declared:
            raise OSError("Codex CLI would enable MCP servers " + json.dumps(sorted(enabled))
                          + " for a call that declares " + json.dumps(sorted(declared)))
    position = argv.index("-C") if "-C" in argv else len(argv) - 1
    return argv[:position] + disables + argv[position:], tuple(undeclared), overlaid


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
    """Run Codex with the filtered host environment and a launch-time resource guard.

    The same executable supplies its version without a model/authentication call.
    For a ``codex exec`` argv it then lists its configured MCP servers and disables
    every one the call does not declare (see select_codex_mcp_servers).
    Injected invokers must accept environment and optional launch_guard; there is
    no fallback to the historical four-argument execution contract. The result's
    process_output_complete is the runner's output_streams_closed (EOF on both
    pipes); capture failures still raise.
    """
    cancellation = {name: value for name, value in (("cancel_requested", cancel_requested),
        ("user_cancel_requested", user_cancel_requested), ("deadline_monotonic", deadline_monotonic)) if value is not None}
    version = run_cli_process(argv=[argv[0], "--version"], prompt="", cwd=cwd,
        timeout_seconds=min(timeout_seconds, 10), environment=environment,
        max_output_bytes=4096, launch_guard=launch_guard, **cancellation)
    if version.returncode != 0 or not version.stdout.strip():
        raise OSError("Codex CLI version check failed")
    disabled, overlaid = (), ()
    try:
        if len(argv) > 1 and argv[1] == "exec":
            try:
                argv, disabled, overlaid = select_codex_mcp_servers(argv, cwd=cwd, environment=environment,
                    timeout_seconds=timeout_seconds, launch_guard=launch_guard, **cancellation)
            except BaseException as failure:
                # Before exec: the trace names the probe; no provider output exists.
                failure.cli_probe = MCP_LISTING_PROBE
                raise
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
        process_output_complete=getattr(process, "output_streams_closed", True) is True,
        cli_version=version.stdout.strip(),
        host_mcp_disabled=disabled,
        host_mcp_overlaid=overlaid,
    )


def _execution_expectation(profile: ExecutionProfileRelease) -> InvocationExecutionExpectation:
    """Validate the currently executable Codex v4 combination without resources.

    Historical releases remain readable but must be explicitly prepared with the
    new binding before execution. This check never reads credentials or programs.
    """
    profile.validate()
    expected = ("codex_cli_agent_executor", "v5", "codex_cli", "openai", "tool_free",
                "inline", "none", (), "denied", ())
    actual = (profile.executor_adapter_id, profile.executor_adapter_revision, profile.transport_kind,
              profile.provider_id, profile.execution_mode, profile.semantic_input_delivery_mode,
              profile.attempt_workspace_policy, profile.tool_policy, profile.network_policy,
              profile.gateway_access_reasons)
    if actual != expected:
        raise ValueError("Codex v5 supports only tool_free, inline, workspace none, empty tools and denied network; prepare an explicit compatible binding")
    return InvocationExecutionExpectation("codex_cli_agent_executor", "v5", "codex_cli",
                                         "tool_free", "inline", "none", "denied", ())


_WORKSPACE_POLICIES = ("none", "own_draft_read_write", "explicit_project_read_write")


def _workspace_execution_expectation(profile: ExecutionProfileRelease) -> InvocationExecutionExpectation:
    """Validate an agent combination for workspace v5 without opening resources.

    Codex serves read, search and edit only through its shell tool on the
    bindings observed so far, so a policy with any of them also needs ``shell``.
    A policy of only declared MCP connections (``mcp__<name>``) runs with the
    shell tool off. Shell needs a writable folder (private draft or the caller's
    project directory); workspace ``none`` is accepted only without Shell.
    """
    from .invocation_tool_definition import MCP_TOOL_PREFIX
    profile.validate()
    if (profile.executor_adapter_id, profile.executor_adapter_revision, profile.transport_kind,
            profile.provider_id) != ("codex_cli_agent_workspace_executor", "v5", "codex_cli", "openai"):
        raise ValueError("Codex workspace v5 requires its exact binding; prepare an explicit compatible Profile")
    if (profile.execution_mode, profile.semantic_input_delivery_mode, profile.gateway_access_reasons) != (
            "agent", "inline", ()) or profile.network_policy not in {"denied", "provider_managed"} \
            or profile.attempt_workspace_policy not in _WORKSPACE_POLICIES:
        raise ValueError("Codex workspace v5 supports agent with inline input, a none, private-draft or "
                         "explicit project workspace, and denied or provider_managed network")
    tools = set(profile.tool_policy)
    if not tools or any(name not in {"read", "search", "edit", "shell"} and not name.startswith(MCP_TOOL_PREFIX)
                        for name in tools):
        raise CodexCapabilityUnsupportedError("Codex workspace v5 supports read, search, edit, shell and declared MCP connections")
    if tools & {"read", "search", "edit"} and "shell" not in tools:
        raise CodexCapabilityUnsupportedError("Codex provides read, search and edit only through its shell tool; allow shell as well")
    if "shell" in tools and profile.attempt_workspace_policy == "none":
        raise CodexCapabilityUnsupportedError("Codex shell needs a writable folder; select a private draft or explicit project workspace")
    return InvocationExecutionExpectation("codex_cli_agent_workspace_executor", "v5", "codex_cli",
                                         "agent", "inline", profile.attempt_workspace_policy,
                                         profile.network_policy, profile.tool_policy)


PERMISSION_PROFILE_NAME = "runtime_attempt"


class CodexCapabilityUnsupportedError(ValueError):
    """The requested execution cannot be expressed by a Codex implementation."""

    error_code = "ADAPTER_CAPABILITY_UNSUPPORTED"


def validate_codex_execution_mode(mode: str, *, profile=None) -> None:
    """Validate the closed, trusted host choice without opening resources.

    configured preserves the generated CLI permission configuration. host uses
    the host's permissions for the exact code_change capability combination;
    the project directory is task scope, not an OS-enforced filesystem boundary.
    """
    if type(mode) is not str or mode not in {"configured", "host"}:
        raise ValueError("codex_execution_mode must be configured or host")
    if mode == "host" and profile is not None:
        if (profile.transport_kind != "codex_cli" or profile.execution_mode != "agent"
                or profile.attempt_workspace_policy != "explicit_project_read_write"
                or profile.network_policy != "provider_managed"
                or set(profile.tool_policy) != {"read", "search", "edit", "shell"}):
            raise CodexCapabilityUnsupportedError(
                "host execution requires Codex agent, explicit_project_read_write, "
                "provider_managed network and exactly read/search/edit/shell")


def codex_home_locations(environment: dict | None = None) -> tuple[Path, ...]:
    """The host Codex home: CODEX_HOME when set, else the standard ``~/.codex``.

    Used only by the explicit local-command extension, whose own sandbox read
    roots must not include the Provider's login location. Ordinary calls do not
    consult it: Codex reads its own login normally.
    """
    source = os.environ if environment is None else environment
    value = (source.get("CODEX_HOME") or "").strip()
    home = Path(value) if value else Path.home() / ".codex"
    return tuple(dict.fromkeys((home.absolute(), home.resolve())))


def _overlaps(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def check_codex_external_grants(grants: tuple[Path, ...], *, workspace_root: Path) -> None:
    """Keep Runtime-owned folders separate from host-supplied dependency roots.

    A dependency that is a filesystem root or overlaps the Attempt workspace
    root (other Attempts, their archives, leases and schema files) is refused.
    This is Runtime's own control/material separation, not a credential scan.
    """
    workspace_root = Path(workspace_root).resolve()
    for grant in grants:
        grant = Path(grant).resolve()
        if grant == Path(grant.anchor):
            raise PermissionError(f"Codex read grant {grant} would open a filesystem root")
        if _overlaps(grant, workspace_root):
            raise PermissionError(f"Codex read grant {grant} overlaps the Runtime Attempt workspace root")


def codex_permission_profile(*, write_folders: tuple[Path, ...], network_policy: str,
                             read_folders: tuple[Path, ...] = ()) -> dict:
    """Return the Codex permission profile expressing this call's declared directory scope.

    The profile extends Codex's own read-only base, declares the Runtime-selected
    read folders (the Attempt's read-only materials) as ``read`` and grants write
    access to exactly the declared writable folders (the Attempt's private draft
    or the caller's project directory, and its temporary folder). The ``read``
    entry states the intended input; it is not claimed as enforcement. Observed
    on Windows with codex-cli 0.166.0: the sandbox runs commands under its own
    identity, Codex adds access entries only for write folders, and reading
    elsewhere relies on the access the host set up on the parent tree (Attempt
    task folders therefore inherit it). Tool network is switched off only when
    the Profile explicitly records ``denied``; with ``provider_managed`` Codex's
    own configuration applies. Other paths and command policy stay Codex's and
    the host's.
    """
    filesystem = {str(Path(folder).resolve()): "read" for folder in read_folders}
    filesystem.update({str(Path(folder).resolve()): "write" for folder in write_folders})
    profile = {"extends": ":read-only", "filesystem": filesystem}
    if network_policy == "denied":
        profile["network"] = {"enabled": False}
    return profile


def command_sandbox_conflicts(*, protected: tuple[Path, ...], command_readable_roots: tuple[Path, ...]) -> tuple[Path, ...]:
    """Protected locations a declared command could read through its own sandbox.

    Explicit local-command extension only: declared commands run in the Runtime
    command sandbox, whose read roots are separate from the Codex profile.
    """
    roots = tuple(Path(root).resolve() for root in command_readable_roots)
    return tuple(location for location in protected
                 if any(Path(location).resolve().is_relative_to(root) for root in roots))


# Codex mcp_servers fields accepted from a host connection entry. Credentials are
# accepted only by environment reference; literal-value credential fields are refused.
CODEX_MCP_FIELDS = frozenset({
    "command", "args", "cwd", "url", "startup_timeout_sec", "tool_timeout_sec", "enabled",
    "enabled_tools", "disabled_tools", "default_tools_approval_mode",
    "env_vars", "bearer_token_env_var", "env_http_headers",
})
CODEX_MCP_LITERAL_CREDENTIAL_FIELDS = frozenset({"env", "http_headers", "bearer_token"})


def validate_codex_mcp_connections(connections) -> dict[str, dict]:
    """Validate host-selected Codex MCP connections; return a private copy.

    Each value is ``{"config": {...}, "environment_keys": [...]}``. The config
    may use only CODEX_MCP_FIELDS; ``env``, ``http_headers`` and
    ``bearer_token`` (literal credential values) and unknown keys are refused,
    naming the field. Every variable referenced by ``env_vars``,
    ``bearer_token_env_var`` or ``env_http_headers`` must be listed in
    ``environment_keys``. Values are never read here; ``args`` and ``url`` are
    not credential channels by contract and are passed as given.
    """
    from ..foundation.foundation_environment_setup import validate_mcp_connection_entries
    entries = validate_mcp_connection_entries(connections)
    for name, entry in entries.items():
        config = entry["config"]
        for field in config:
            if field in CODEX_MCP_LITERAL_CREDENTIAL_FIELDS:
                raise ValueError(f"Codex MCP connection {name}: field {field} carries literal credentials; "
                                 "use env_vars, bearer_token_env_var or env_http_headers")
            if field not in CODEX_MCP_FIELDS:
                raise ValueError(f"Codex MCP connection {name}: field {field} is not accepted")
        referenced = []
        if "env_vars" in config:
            if type(config["env_vars"]) is not list or any(type(item) is not str for item in config["env_vars"]):
                raise ValueError(f"Codex MCP connection {name}: env_vars must be a list of variable names")
            referenced.extend(config["env_vars"])
        if "bearer_token_env_var" in config:
            if type(config["bearer_token_env_var"]) is not str:
                raise ValueError(f"Codex MCP connection {name}: bearer_token_env_var must be a variable name")
            referenced.append(config["bearer_token_env_var"])
        if "env_http_headers" in config:
            headers = config["env_http_headers"]
            if type(headers) is not dict or any(type(value) is not str for value in headers.values()):
                raise ValueError(f"Codex MCP connection {name}: env_http_headers must map headers to variable names")
            referenced.extend(headers.values())
        missing = sorted(set(referenced) - set(entry["environment_keys"]))
        if missing:
            raise ValueError(f"Codex MCP connection {name}: referenced variables {missing} are not in environment_keys")
    return entries


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


def _command(*, profile, workspace, codex_bin, schema_path, mcp_servers: tuple[str, ...], shell_tool: bool,
             permission_options: tuple[str, ...], sandbox: tuple[str, ...]) -> list[str]:
    # The shell lives in the functions namespace; excluding it from code mode
    # removes the shell tool itself (codex-cli 0.153.4), so only Shell calls keep it.
    # Host login, config.toml and execpolicy rules load normally; these overrides
    # only select context, approval, declared MCP connections and directory scope.
    excluded = '["collaboration","clock"]' if shell_tool else '["functions","collaboration","clock"]'
    options = ["project_doc_max_bytes=0", 'approval_policy="never"',
        "agents.enabled=false", "skills.include_instructions=false",
        "features.code_mode.excluded_tool_namespaces=" + excluded,
        *mcp_servers, "features.skip_host_skill_discovery=true", 'web_search="disabled"',
        "features.shell_tool=" + ("true" if shell_tool else "false")]
    options.extend("features." + name + "=false" for name in _FEATURES_OFF)
    options.extend(permission_options)
    argv = [str(codex_bin), "exec", "-m", profile.model_id,
            "-c", "model_reasoning_effort=" + json.dumps(profile.reasoning_profile)]
    for option in options:
        argv.extend(("-c", option))
    argv.extend(("-C", str(workspace), *sandbox, "--skip-git-repo-check",
                 "--strict-config", "--ephemeral", "--json"))
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
    """Render the actual tool-free v5 command for execution and context probes, without I/O.

    Model and effort come only from Profile. Task data cannot supply raw flags,
    settings, credentials or permission overrides. The executable locator is a
    host input; program existence and authentication are checked only at execution.
    The host's own Codex login and configuration apply; no MCP server is passed.

    Args:
        profile: Exact v5 Profile; supplies the concrete model and reasoning effort.
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
                    mcp_servers=("mcp_servers={}",), shell_tool=False, permission_options=(),
                    sandbox=("-s", "read-only"))


def render_codex_mcp_servers(connections: dict, local_commands: LocalCommandSession | None = None) -> tuple[str, ...]:
    """Render ``-c`` values: an empty table, then only the declared servers.

    ``connections`` maps a declared name to its validated host entry; only its
    closed, credential-free config fields are rendered. The explicit command
    extension adds its own proxy server when selected. The empty table does not
    remove servers inherited from the host configuration; the invoker disables
    those per call (select_codex_mcp_servers).
    """
    rendered = ["mcp_servers={}"]
    for name, entry in connections.items():
        rendered.append(f"mcp_servers.{name}=" + _toml(entry["config"]))
    if local_commands is not None:
        server = local_commands.mcp_config["mcpServers"][LOCAL_COMMAND_SERVER_NAME]
        rendered.append(f"mcp_servers.{LOCAL_COMMAND_SERVER_NAME}=" + _toml({
            "command": server["command"], "args": server["args"], "enabled_tools": [LOCAL_COMMAND_TOOL_NAME],
            "default_tools_approval_mode": "approve", "tool_timeout_sec": server["timeout"] // 1000}))
    return tuple(rendered)


def build_workspace_command(*, profile: ExecutionProfileRelease, main_folder: Path, codex_bin: str,
                            permission_profile: dict | None, local_commands: LocalCommandSession | None = None,
                            schema_path: Path | None = None, mcp_connections: dict | None = None,
                            codex_execution_mode: str = "configured") -> list[str]:
    """Render the workspace v5 command for one prepared Attempt, without I/O.

    With a recorded ``shell`` the shell tool is on as a non-login shell and the
    generated permission profile replaces -s, which Codex does not accept
    together with a permission profile. Without ``shell`` (MCP-only) the shell
    tool and its namespace are off; a writable folder still gets its profile,
    workspace ``none`` runs with ``-s read-only``. Only the declared MCP
    connections, and the command proxy when explicitly selected, are passed.

    Args:
        profile: Exact workspace v5 Profile; supplies model, effort and tools.
        main_folder: The Attempt's cwd (draft scratch, project directory or work).
        codex_bin: Host-selected CLI executable locator.
        permission_profile: Result of codex_permission_profile, or None for none.
        local_commands: This Attempt's LocalCommandSession, or None.
        schema_path: Optional prepared native schema file.
        mcp_connections: Validated declared connections by name, or None.
        codex_execution_mode: configured retains the generated permission
            profile; host selects native host execution for code_change.
    Returns:
        A fresh argv list for one codex exec invocation.
    Raises:
        ValueError: Profile targets another binding or capability set.
        TypeError: local_commands is not the exact Runtime session.
    """
    _workspace_execution_expectation(profile)
    validate_codex_execution_mode(codex_execution_mode, profile=profile)
    if codex_execution_mode == "host":
        if permission_profile is not None or local_commands is not None or mcp_connections:
            raise CodexCapabilityUnsupportedError("host execution takes no permission profile, command extension or MCP")
        return _command(profile=profile, workspace=main_folder, codex_bin=codex_bin, schema_path=schema_path,
                        mcp_servers=("mcp_servers={}",), shell_tool=True,
                        permission_options=("allow_login_shell=false",), sandbox=("-s", "danger-full-access"))
    if local_commands is not None and type(local_commands) is not LocalCommandSession:
        raise TypeError("local_commands must be the exact Runtime LocalCommandSession")
    shell_tool = "shell" in profile.tool_policy
    mcp_servers = render_codex_mcp_servers(mcp_connections or {}, local_commands)
    permission_options, sandbox = (), ("-s", "read-only")
    if permission_profile is not None:
        name = PERMISSION_PROFILE_NAME
        # A non-login shell does not source login profiles for each command.
        permission_options = (*(("allow_login_shell=false",) if shell_tool else ()),
            f"default_permissions={json.dumps(name)}",
            f"permissions.{name}.extends={_toml(permission_profile['extends'])}",
            f"permissions.{name}.filesystem={_toml(permission_profile['filesystem'])}",
            *((f"permissions.{name}.network.enabled={_toml(permission_profile['network']['enabled'])}",)
              if "network" in permission_profile else ()))
        sandbox = ()
    return _command(profile=profile, workspace=main_folder, codex_bin=codex_bin, schema_path=schema_path,
                    mcp_servers=mcp_servers, shell_tool=shell_tool, permission_options=permission_options,
                    sandbox=sandbox)


def redact_codex_argv(argv: list[str]) -> list[str]:
    """Recorded argv: each declared host MCP value replaced by its config hash."""
    recorded = []
    for value in argv:
        if value.startswith("mcp_servers.") and "=" in value and not value.startswith(
                f"mcp_servers.{LOCAL_COMMAND_SERVER_NAME}="):
            key, rendered = value.split("=", 1)
            digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
            value = f"{key}=<host MCP config sha256:{digest}>"
        recorded.append(value)
    return recorded


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
    supported_network_policies = ("denied",)
    # Actual Shell capability: False for tool-free; None means it follows the
    # Profile's recorded tool_policy (workspace calls, Shell or MCP-only).
    shell_tool_enabled: bool | None = False
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
        read_only_dependencies: tuple[Path, ...] = (),
        mcp_servers: dict | None = None,
        project_directory: Path | None = None,
        command_permits: tuple[str, ...] = (),
        codex_execution_mode: str = "configured",
    ) -> None:
        """Configure the admitted Codex implementation without preparing resources.

        Args:
            release_registry: Exact registered definitions used by invocation checks.
            artifact_host: Request-owned content/trace store implementing read_bytes,
                commit_failure_detail and commit_attempt_trace. This does not select
                or connect to a persistent database.
            workspace_root: Host-owned root of the retained Attempt folders.
            invoker: Trusted callable accepting keyword argv, prompt, cwd,
                timeout_seconds, environment and optional launch_guard. It must pass
                the explicit environment and launch guard to actual process creation.
                An active run budget additionally requires deadline_monotonic;
                it is omitted when the host has no enclosing deadline. Optional
                cancellation ports are passed only when supplied by the host.
                The default uses run_cli_process. An old four-argument callable is
                rejected before resource preparation; no unguarded fallback is tried.
            codex_bin: Optional explicit installed CLI. None uses CODEX_CLI_BIN, then
                the host's native ``codex`` on PATH, at invocation.
            read_only_dependencies: Workspace v5 only. Explicit trusted existing
                directories a Shell-capable Agent may use; never task-supplied.
                Tool-free v5 accepts none.
            mcp_servers: Host connections for codex_cli by name, each
                ``{"config": {...}, "environment_keys": [...]}``. Only the connections
                a Profile declares as ``mcp__<name>`` are passed. Configs accept a
                closed field set with credentials only by environment reference
                (validate_codex_mcp_connections); invalid entries raise here.
            project_directory: The caller's existing writable directory for an
                ``explicit_project_read_write`` Profile. Runtime never deletes it.
            command_permits: Per-call Shell command permits. Codex has no per-call
                command allow rule, so a non-empty value is refused at execution.
            codex_execution_mode: Trusted per-call choice, configured (default)
                or host. host requires the exact code_change capability set and
                no additional dependencies, materials or command extension.
                The project directory states task scope, not an OS boundary.
        Raises:
            ValueError: Invalid artifact host, MCP connection or directory locator.
            OSError: A locator cannot be resolved by the host filesystem.
        Effects:
            Stores resource locators and builds the descriptor. Does not create a
            directory, open credentials, call a CLI/model, or register any release.
            Codex reads the host's own login and configuration during execution.
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
        if read_only_dependencies and not self.workspace_execution:
            raise ValueError("Tool-free Codex execution takes no read-only dependencies")
        self._dependencies = tuple(Path(item).resolve(strict=True) for item in read_only_dependencies)
        self._mcp_servers = validate_codex_mcp_connections(mcp_servers or {})
        self._project_directory = (None if project_directory is None
                                   else validate_project_directory(project_directory, workspace_root=self._workspace_root))
        if type(command_permits) is not tuple:
            raise ValueError("command_permits must be a tuple")
        self._command_permits = command_permits
        validate_codex_execution_mode(codex_execution_mode)
        if codex_execution_mode == "host" and (not self.workspace_execution or self._dependencies):
            raise CodexCapabilityUnsupportedError("host execution requires the workspace adapter and no extra dependencies")
        self._codex_execution_mode = codex_execution_mode
        self._descriptor = provider_adapter_descriptor(
            adapter_id=self.executor_adapter_id,
            adapter_revision=self.executor_adapter_revision,
            provider_id="openai",
            transport_family="cli",
            transport_kind="codex_cli",
            execution_mode=self.expected_execution_mode,
            input_delivery_mode=self.expected_semantic_input_delivery_mode,
            network_policies=self.supported_network_policies,
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
        """Execute one invocation with live resources and the host's own Codex login.

        A self-test requires the kernel's live host validator and actual launch
        guard. Missing hooks or an incompatible injected invoker fail before CLI
        effects. Credentials never enter the prompt, Profile, arguments or trace.

        Args:
            request: Exact registered request with frozen inputs and either external
                operation evidence or the existing live self-test resource binding.
            host: Trusted request-bound Runtime host for staging outputs and, for
                self-tests, live binding validation and launch-time resource checking.
        Returns:
            AgentExecutionResult with a schema-valid output or typed failed/cancelled
            status. Business verdicts remain Module-owned. Actual CLI version, captured
            streams, terminal metadata and the effective configuration stay in the
            private Attempt trace. A valid terminal result is accepted even when the
            raw capture is incomplete; the gap is recorded as process_output_complete.
            CLI error notifications may be transient; process exit and the unique
            turn terminal are checked before validating the final output.
        Raises:
            ValueError: Invalid request/Profile or incompatible injected invoker.
            PermissionError: Missing live resource/operation evidence, a dependency
                overlapping Runtime folders, or (explicit command extension only)
                declared commands that could read a protected location. Provider is
                not started on these failures.
            Exception: Native content/record-store integrity failures retain their owner.
        Effects:
            Prepares one retained Attempt folder, launches the selected CLI with the
            host's login and configuration plus Runtime's explicit overrides, and
            stages output and trace through the supplied ports. Workspace v5 also
            stages materials and records the main folder, permission profile and
            effective configuration. No login, registration, model fallback or
            global environment modification is performed.
        """
        if type(request) is not AuthorizedAgentExecutionRequest:
            raise ValueError("request must be an exact AuthorizedAgentExecutionRequest")
        request.validate()
        profile = self._release_registry.get_execution_profile(request.execution_profile_ref,
                                                              request.execution_profile_sha256)
        expectation = self.execution_expectation(profile)
        validate_codex_execution_mode(self._codex_execution_mode, profile=profile)
        if self.workspace_execution:
            check_codex_external_grants((*_runtime_python_read_roots(), *self._dependencies),
                                        workspace_root=self._workspace_root)
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
            raise ValueError("Codex invoker must accept explicit environment, optional launch_guard "
                "and the active host controls: " + ", ".join(invocation_controls)) from exc
        prepared = prepare_registered_invocation_context(
            request=request,
            release_registry=self._release_registry,
            artifact_host=self._artifact_host,
            expectation=expectation, self_test_validator=validate_self_test,
        )
        if self.shell_tool_enabled is not None and self.shell_tool_enabled != ("shell" in prepared.profile.tool_policy):
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
        """Resource arguments the kernel host checks; v5 keeps its original call."""
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
            "codex_execution_mode": self._codex_execution_mode,
        }
        parsed = _parse_codex_stream(b"")
        # Set when a CLI process this call started was not confirmed stopped; the
        # private schema directory is then retained and reported, not disposed.
        stop_unconfirmed = False

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

        if self._command_permits:
            trace["stage"] = "command_permits"
            fail("dependency_unavailable", "ADAPTER_CAPABILITY_UNSUPPORTED",
                 "Codex cannot express per-call command permits; its own execpolicy rules still apply")
        policy = profile.attempt_workspace_policy
        if (policy == "explicit_project_read_write") != (self._project_directory is not None):
            trace["stage"] = "project_directory"
            fail("schema", "ADAPTER_REQUEST_INVALID",
                 "project_directory is required exactly for an explicit_project_read_write Profile")
        declared = declared_mcp_connections(profile.tool_policy)
        missing = [name for name in declared if name not in self._mcp_servers]
        if missing:
            trace["stage"] = "mcp_connection"
            fail("dependency_unavailable", "ADAPTER_BINDING_UNAVAILABLE",
                 f"Module declares MCP connection {missing[0]}; the host has not configured it for codex_cli")
        connections = {name: self._mcp_servers[name] for name in declared}
        shell_tool = "shell" in profile.tool_policy

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
        temporary, write_folders = None, ()
        material_hashes, policy_refusal = {}, None
        try:
            cleanup.enter_context(lease_attempt_workspace(workspace))
            schema_path = None
            if profile.output_constraint_mode == NATIVE_STRUCTURED_OUTPUT:
                stage = "native_schema_file"
                schema_directory = Path(cleanup.enter_context(private_temporary_directory(
                    prefix=f".{request.attempt_id}_schema_", parent=self._workspace_root,
                    retain=lambda: stop_unconfirmed,
                )))
                schema_path = schema_directory / "output_schema.json"
                schema_path.write_text(json.dumps(projected_output_schema, ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")), encoding="utf-8")
            if self.workspace_execution:
                # The cwd is the declared writable folder (private draft scratch or the
                # caller's project directory); materials stay read-only copies.
                stage = "material_preparation"
                work = workspace / "work"
                materials = work / "materials"
                if policy == "explicit_project_read_write":
                    cwd, temporary = self._project_directory, work / "tmp"
                    directories, write_folders = (work, materials, temporary), (self._project_directory, temporary)
                elif policy == "own_draft_read_write":
                    cwd = work / "scratch"
                    temporary = cwd / ".tmp"
                    directories, write_folders = (work, materials, cwd, temporary), (cwd,)
                else:
                    cwd, directories = work, (work, materials)
                for directory in directories:
                    if directory.is_symlink():
                        policy_refusal = "Codex workspace directory is a symlink"
                        raise AttemptWorkspaceConflictError(policy_refusal)
                    HOST_PLATFORM.create_task_directory(directory)
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
                if self._codex_execution_mode == "host" and resources_body is not None:
                    fail("dependency_unavailable", "ADAPTER_CAPABILITY_UNSUPPORTED",
                         "host execution takes no material package, extra dependencies or command extension")
            else:
                argv = build_command(profile=profile, workspace=workspace, codex_bin=executable,
                                     schema_path=schema_path)
            stage = "provider_environment"
            environment = HOST_PLATFORM.provider_environment(os.environ, _ENVIRONMENT_KEYS)
            forwarded = sorted({key for entry in connections.values() for key in entry["environment_keys"]})
            environment.update({key: os.environ[key] for key in forwarded if key in os.environ})
            permission = None
            if self.workspace_execution:
                stage = "isolation_preparation"
                if write_folders and self._codex_execution_mode == "configured":
                    permission = codex_permission_profile(write_folders=write_folders,
                                                          network_policy=profile.network_policy,
                                                          read_folders=(materials,))
                if resources_body is not None and parse_local_resources(resources_body)["commands"]:
                    # Explicit local-command extension only: its own sandbox read roots
                    # must not include the Runtime temp root or the Codex login location.
                    protected = (Path(tempfile.gettempdir()).resolve(), *codex_home_locations(environment))
                    exposed = command_sandbox_conflicts(protected=protected, command_readable_roots=(
                        *map(Path, _SYSTEM_READ_ROOTS), *_runtime_python_read_roots(), *self._dependencies,
                        materials / "source", cwd))
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
                    permission_profile=permission, local_commands=local_commands, schema_path=schema_path,
                    mcp_connections=connections, codex_execution_mode=self._codex_execution_mode)
                if shell_tool:
                    python = _runtime_python_executable()
                    bins = [str(dep / "bin") for dep in self._dependencies if (dep / "bin").is_dir()]
                    host_path = [entry for entry in environment.get("PATH", "").split(os.pathsep) if entry]
                    environment.update(
                        PATH=os.pathsep.join(dict.fromkeys([str(python.parent), *bins,
                            *_developer_tool_bins(invocation_deadline(host, request)), *host_path])),
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", PYTHONDONTWRITEBYTECODE="1")
                if temporary is not None:
                    environment["TMPDIR"] = str(temporary)
                trace.update(main_folder=str(cwd), codex_permission_profile=permission,
                             material_sha256=material_hashes)
            trace.update(argv=redact_codex_argv(list(argv)), cwd=str(cwd),
                         prompt_envelope_sha256=request.prompt_envelope_sha256,
                         environment_keys=sorted(environment))
            trace["effective_configuration"] = {
                "codex_execution_mode": self._codex_execution_mode,
                "tools": (["shell"] if shell_tool else []) + ["mcp__" + name for name in declared],
                "allowed_rules": [],
                "permission_mode": 'approval_policy="never"',
                "read_directories": [str(path) for path in ((materials,) if materials is not None else ())
                                     + self._dependencies],
                "write_directories": [str(Path(folder)) for folder in write_folders],
                "mcp_connections": [{"name": name, "config_sha256": _canonical_sha256(entry["config"]),
                                     "environment_keys": list(entry["environment_keys"])}
                                    for name, entry in connections.items()],
                "context_suppressed": ["project_docs", "skills", "host_skill_discovery", "agents", "web_search",
                                       *("feature:" + name for name in _FEATURES_OFF)],
                "cli_program": str(Path(executable).absolute()),
                "cli_version": None,
                "command_scope": ("host" if self._codex_execution_mode == "host" else
                                  "provider_policy" if shell_tool else "none"),
                "filesystem_scope": ("task_directed" if self._codex_execution_mode == "host"
                                     else "provider_configuration"),
                "file_io": ("via_shell" if shell_tool else
                            "unavailable_without_shell" if self.workspace_execution else "none"),
            }
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
                trace["effective_configuration"]["cli_version"] = result.cli_version
                if any(type(value) is not tuple or any(type(name) is not str for name in value)
                       for value in (result.host_mcp_disabled, result.host_mcp_overlaid)):
                    raise TypeError("Codex CLI invoker returned invalid MCP selection facts")
                # Names only. mcp_connections' config_sha256 covers only what Runtime
                # supplied; an overlaid name also carries host-configured fields.
                trace["effective_configuration"]["host_mcp_disabled"] = list(result.host_mcp_disabled)
                trace["effective_configuration"]["host_mcp_overlaid"] = list(result.host_mcp_overlaid)
                if type(result.returncode) is not int or type(result.process_output_complete) is not bool:
                    raise TypeError("Codex CLI invoker returned invalid process metadata")
            except BaseException as process_error:
                # Process errors, startup errors and guard failures raised after creation
                # state whether the started process stopped; other failures keep False.
                if cli_process_stop_confirmed(process_error) is False:
                    stop_unconfirmed = True
                raise
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
        except TerminalAdapterFailure:
            # The terminal trace is already committed. Preserve its failure
            # classification and single content-store submission.
            raise
        except (Exception, KeyboardInterrupt) as exc:
            trace.update(stage=stage, error=str(exc))
            if stage == "isolation_preparation" and isinstance(exc, PermissionError):
                raise  # Before any Provider or command process; the caller sees the refusal.
            if getattr(exc, "cli_version", None) is not None:
                trace["cli_version"] = exc.cli_version
            if getattr(exc, "cli_probe", None) is not None:
                # The failure happened in a pre-exec probe: codex exec never started.
                trace["cli_probe"] = exc.cli_probe
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
        # An incomplete raw capture alone is recorded, not a failure: the parsed
        # events decide whether a valid final result exists.
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
    """Execute an isolated tool-free Module with the host's own Codex login.

    v5 adds no task tools and passes no MCP server. v3/v4 releases are
    readable historical definitions, not executable aliases.
    """

    executor_adapter_id = "codex_cli_agent_executor"
    executor_adapter_revision = "v5"


class CodexCliAgentWorkspaceModuleExecutor(_CodexCliExecutorBase):
    """Execute an agent Module through Codex with the recorded tools only.

    A recorded ``shell`` turns on the Codex shell tool, which on the observed
    bindings is also how Codex reads, searches and edits files; it works in the
    declared writable folder (the Attempt's private scratch or the caller's
    project directory) under the selected configured or native host mode. Without ``shell``
    (for example a Module of only declared MCP connections) the shell tool is off
    and the effective configuration records that file I/O is unavailable.
    Declared MCP connections are the only MCP servers passed; the command proxy
    is added only when local commands are explicitly selected. Historical v2/v3/v4
    Profiles stay readable and are refused by binding.
    """

    executor_adapter_id = "codex_cli_agent_workspace_executor"
    executor_adapter_revision = "v5"
    expected_execution_mode = "agent"
    supported_network_policies = ("denied", "provider_managed")
    shell_tool_enabled = None
    workspace_execution = True

    @staticmethod
    def execution_expectation(profile: ExecutionProfileRelease) -> InvocationExecutionExpectation:
        """Check a Profile against workspace v5 without opening resources."""
        return _workspace_execution_expectation(profile)


def _canonical_sha256(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


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
    "select_codex_mcp_servers",
]
