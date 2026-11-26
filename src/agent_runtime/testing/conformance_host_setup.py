"""Installed setup CLI: prepare one project root for Runtime use with Claude, Codex or Antigravity.

The command is agent-runtime-setup. It composes existing pieces only:
setup_runtime (operator Skills), load_runtime_config (the one host config),
the Runtime process runner (native program start, short status probes) and the
existing MCP connection validators. It writes nothing but setup_runtime's files
and the selected fields of root/.runtime/config.json, and keeps no other state.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys

from ..foundation.foundation_environment_setup import (
    _MCP_NAME, _RESERVED_MCP_NAMES, _parse_runtime_config, _read, _target, _write,
    load_runtime_config, setup_runtime, validate_mcp_connection_entries,
)
from ..invocation.invocation_antigravity_cli_execution import installed_agy_program
from ..invocation.invocation_codex_module_invocation import validate_codex_mcp_connections
from ..invocation.invocation_host_platform_selection import HOST_PLATFORM
from ..invocation.invocation_process_execution import run_cli_process


# "both" keeps meaning Claude plus Codex; Antigravity is selected only by name.
_TRANSPORTS = {"claude": ("claude_cli",), "codex": ("codex_cli",), "both": ("claude_cli", "codex_cli"),
               "antigravity": ("antigravity_cli",)}
_PROGRAM_NAMES = {"claude_cli": "claude", "codex_cli": "codex", "antigravity_cli": "agy"}
_PATH_FLAGS = {"claude_cli": "--claude-path", "codex_cli": "--codex-path", "antigravity_cli": "--antigravity-path"}
_LOGIN_COMMANDS = {"claude_cli": "claude auth login", "codex_cli": "codex login",
                   "antigravity_cli": "agy, completing its sign-in when it asks"}
_PROBE_SECONDS = 15
_PROBE_OUTPUT_BYTES = 1 << 20
# Codex `mcp get --json` keys that describe the listing, not the connection.
_CODEX_LISTING_METADATA = frozenset({"name", "enabled", "disabled_reason", "auth_status"})
_CODEX_SERVER_FIELDS = frozenset({"enabled_tools", "disabled_tools", "startup_timeout_sec", "tool_timeout_sec"})
_CODEX_TRANSPORT_FIELDS = {"stdio": frozenset({"command", "args", "cwd", "env_vars"}),
                           "streamable_http": frozenset({"url", "bearer_token_env_var", "env_http_headers"})}
# Choices a project entry already made; a refresh from the native listing keeps them.
_CODEX_PROJECT_CHOICES = ("enabled", "enabled_tools", "disabled_tools", "default_tools_approval_mode")
_CLAUDE_FIELDS = frozenset({"type", "command", "args", "env", "url", "headers"})
_CLAUDE_TYPES = frozenset({"stdio", "http", "sse"})
_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]{0,127})\}")
_SOURCE_ROUTE = ("the connection's owner can supply a credential-free entry through --mcp-config FILE "
                 "(an existing Runtime config with mcp_servers.<transport>.<name>) or change the native "
                 "entry to environment-variable references")


def _npm_package_program(name: str) -> str | None:
    """The native program inside the npm package beside a PATH launcher, or None.

    Only directories on PATH that hold the npm launcher (<name>.cmd or .ps1) are
    considered, and in each only these fixed package-relative files: no launcher
    content is read and nothing is searched recursively.
    """
    arm = platform.machine().lower() in ("arm64", "aarch64")
    arch, triple = ("arm64", "aarch64-pc-windows-msvc") if arm else ("x64", "x86_64-pc-windows-msvc")
    vendor = ("vendor", triple, "bin", "codex.exe")
    layouts = {
        "claude": (("node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe"),),
        "codex": (("node_modules", "@openai", "codex", "node_modules", "@openai", f"codex-win32-{arch}", *vendor),
                  ("node_modules", "@openai", f"codex-win32-{arch}", *vendor),
                  ("node_modules", "@openai", "codex", *vendor)),
    }[name]
    search = next((value for key, value in os.environ.items() if key.upper() == "PATH"), "")
    for directory in filter(None, search.split(os.pathsep)):
        folder = Path(directory)
        if not any((folder / (name + suffix)).is_file() for suffix in (".cmd", ".ps1")):
            continue
        for parts in layouts:
            candidate = folder.joinpath(*parts)
            if candidate.is_file():
                return os.path.abspath(candidate)
    return None


def _probe(program: str, arguments: list[str], root: Path) -> tuple[int | None, str]:
    """Run one short non-interactive native command; (exit code, stdout) or (None, reason)."""
    try:
        completed = run_cli_process(argv=[program, *arguments], prompt="", cwd=root,
                                    timeout_seconds=_PROBE_SECONDS, environment=dict(os.environ),
                                    max_output_bytes=_PROBE_OUTPUT_BYTES)
    except OSError as exc:
        return None, "cannot be started: " + (exc.strerror or type(exc).__name__)
    except subprocess.SubprocessError as exc:
        return None, f"did not finish ({type(exc).__name__})"
    return completed.returncode, completed.stdout or ""


def _login(transport: str, program: str, root: Path) -> dict:
    """Login status from the CLI's own status command; only a summary is kept.

    Antigravity has no documented login-status command. Setup neither reads its
    authentication files nor treats a model listing as sign-in evidence, so the
    status is not_verified until a real call succeeds.
    """
    if transport == "antigravity_cli":
        return {"login": "not_verified"}
    if transport == "claude_cli":
        code, output = _probe(program, ["auth", "status", "--json"], root)
        try:
            status = json.loads(output) if code is not None else None
        except ValueError:
            status = None
        if type(status) is dict and type(status.get("loggedIn")) is bool:
            method = status.get("authMethod")
            return {"login": "logged_in" if status["loggedIn"] else "not_logged_in",
                    "auth_method": method if type(method) is str and len(method) <= 64 else None}
        return {"login": "unknown"}
    code, _ = _probe(program, ["login", "status"], root)
    return {"login": "unknown" if code is None else "logged_in" if code == 0 else "not_logged_in"}


def _prepare_program(transport: str, explicit: Path | None, configured: Path | None, root: Path,
                     missing: list[str]) -> tuple[dict, str | None]:
    """Choose, start-check and probe one provider program; return its report and a path to write."""
    name = _PROGRAM_NAMES[transport]
    flag = _PATH_FLAGS[transport]
    if explicit is not None:
        candidates = [(os.path.abspath(explicit), "explicit")]
    elif configured is not None:
        candidates = [(str(configured), "configured")]
    else:
        fallback = ((installed_agy_program(os.environ), "installed_path") if transport == "antigravity_cli"
                    else (_npm_package_program(name), "npm_package"))
        candidates = [(program, source) for program, source in (
            (HOST_PLATFORM.resolve_cli_program(name, os.environ), "path"), fallback) if program is not None]
    if not candidates:
        where = ("on PATH or at %LOCALAPPDATA%\\agy\\bin\\agy.exe" if transport == "antigravity_cli"
                 else "on PATH or in its npm package")
        missing.append(f"{transport}: no native {name} program {where}; install the CLI or pass {flag} EXE")
        return {"program": None, "source": None, "started": False}, None
    failures = []
    for program, source in candidates:
        code, output = _probe(program, ["--version"], root)
        if code == 0:
            report = {"program": program, "source": source, "started": True,
                      "version": (output.strip().splitlines() or [""])[0][:200],
                      **_login(transport, program, root)}
            if report["login"] not in ("logged_in", "not_verified"):
                missing.append(f"{transport}: login status is {report['login']}; the human signs in natively "
                               f"({_LOGIN_COMMANDS[transport]}) and then reruns this setup")
            return report, (None if source == "configured" else program)
        failures.append(f"{program} ({source}) {output if code is None else f'--version exited {code}'}")
    if candidates[0][1] == "configured":
        missing.append(f"{transport}: configured provider_cli_paths program {failures[0]}; it is kept; "
                       f"pass {flag} EXE to replace it")
    else:
        missing.append(f"{transport}: program " + "; ".join(failures))
    return {"program": candidates[0][0], "source": candidates[0][1], "started": False}, None


def _claude_refusals(config: dict) -> tuple[list[str], list[str]]:
    """Field paths that are not credential-free Claude configuration, and the referenced variables."""
    refused, referenced = [], []

    def scan(value) -> bool:
        found = _REFERENCE.findall(value) if type(value) is str else []
        referenced.extend(found)
        return bool(found)

    for field, value in config.items():
        if field not in _CLAUDE_FIELDS:
            refused.append(field)
        elif field == "type":
            if value not in _CLAUDE_TYPES:
                refused.append(field)
        elif field in ("env", "headers"):
            if type(value) is not dict:
                refused.append(field)
                continue
            # A literal value may be a credential; only ${VAR} references are handed through.
            refused.extend(f"{field}.{key}" for key, item in value.items() if not scan(item))
        elif field == "args":
            if type(value) is not list or any(type(item) is not str for item in value):
                refused.append(field)
            else:
                for item in value:
                    scan(item)
        elif type(value) is not str:
            refused.append(field)
        else:
            scan(value)
    return refused, referenced


def _source_entry(transport: str, name: str, source: dict, source_path: Path) -> tuple[dict | None, str]:
    """The selected entry from an explicit --mcp-config document, or (None, why it is absent)."""
    where = f"--mcp-config {source_path}"
    if "mcp_servers" in source:
        servers = source["mcp_servers"]
        entries = servers.get(transport) if type(servers) is dict else None
        entry = entries.get(name) if type(entries) is dict else None
        return (entry, "") if entry is not None else (None, f"{where} has no mcp_servers.{transport}.{name}")
    if "mcpServers" in source:
        if transport != "claude_cli":
            return None, f"{where} is a Claude mcpServers file; it has no mcp_servers.{transport}.{name}"
        servers = source["mcpServers"]
        config = servers.get(name) if type(servers) is dict else None
        if config is None:
            return None, f"{where} has no mcpServers.{name}"
        return {"config": config, "environment_keys": []}, ""
    return None, f"{where} has neither mcp_servers nor mcpServers"


def _codex_listing_entry(listing: dict) -> tuple[dict | None, list[str]]:
    """A Codex project entry from one `codex mcp get --json` result, or the fields it cannot express."""
    config, refused = {}, []

    def meaningful(value) -> bool:
        return value is not None and value != [] and value != {} and value != ""

    for field, value in listing.items():
        if field in _CODEX_LISTING_METADATA or field == "transport" or not meaningful(value):
            continue
        if field in _CODEX_SERVER_FIELDS:
            config[field] = value
        else:
            refused.append(field)
    transport = listing.get("transport")
    kind = transport.get("type") if type(transport) is dict else None
    if kind not in _CODEX_TRANSPORT_FIELDS:
        refused.append("transport.type")
    else:
        for field, value in transport.items():
            if field == "type" or not meaningful(value):
                continue
            if field in _CODEX_TRANSPORT_FIELDS[kind]:
                config[field] = value
            else:
                refused.append("transport." + field)
    if refused:
        return None, refused
    referenced = [*config.get("env_vars", []), config.get("bearer_token_env_var"),
                  *(config.get("env_http_headers") or {}).values()]
    return {"config": config, "environment_keys": [item for item in referenced if type(item) is str]}, []


def _codex_native_entry(name: str, program: str | None, root: Path,
                        existing: dict | None) -> tuple[dict | None, str, str, bool]:
    """Read one selected server from the Codex program; return (entry, kind, reason, disabled)."""
    if program is None:
        return None, "missing", "the Codex program is unavailable, so its MCP configuration cannot be read", False
    code, output = _probe(program, ["mcp", "get", name, "--json"], root)
    if code != 0:
        return None, "missing", (f"codex mcp get {name} {output}" if code is None else
                                 f"Codex has no MCP server named {name} (codex mcp get exited {code})"), False
    try:
        listing = json.loads(output)
    except ValueError:
        listing = None
    if type(listing) is not dict:
        return None, "missing", f"codex mcp get {name} --json did not return a JSON object", False
    entry, refused = _codex_listing_entry(listing)
    if entry is None:
        return None, "refused", (f"native Codex fields {refused} cannot be handed through as credential-free "
                                 "configuration (values are not shown); " + _SOURCE_ROUTE), False
    if existing is not None:
        # A refresh keeps the tool scope and approval the project entry already chose.
        for field in _CODEX_PROJECT_CHOICES:
            entry["config"].pop(field, None)
            if field in existing["config"]:
                entry["config"][field] = existing["config"][field]
        entry["environment_keys"] = list(dict.fromkeys([*entry["environment_keys"], *existing["environment_keys"]]))
    return entry, "", "", listing.get("enabled") is False


def _prepare_connection(transport: str, name: str, source: dict | None, source_path: Path | None,
                        existing: dict | None, program: str | None, root: Path,
                        missing: list[str]) -> tuple[dict, dict | None]:
    """Select one MCP connection; return its report and a new entry to write (None: leave it as it is)."""
    disabled = False
    if source is not None:
        entry, reason = _source_entry(transport, name, source, source_path)
        kind = "missing"
    elif transport == "codex_cli":
        entry, kind, reason, disabled = _codex_native_entry(name, program, root, existing)
        if kind == "missing" and existing is not None:
            report = {"action": "kept", "detail": reason + "; the existing project entry is kept"}
            return _with_enabled_state(transport, name, existing, False, report, missing), None
    elif existing is not None:
        return {"action": "kept"}, None
    else:
        entry, kind, reason = None, "missing", ("Claude has no machine-readable MCP listing; pass --mcp-config FILE "
                                                f"(a Runtime config with mcp_servers.claude_cli.{name} or a Claude "
                                                "mcpServers file)")
    if entry is not None:
        try:
            if transport == "codex_cli":
                entry = validate_codex_mcp_connections({name: entry})[name]
            else:
                entry = validate_mcp_connection_entries({name: entry})[name]
                refused, referenced = _claude_refusals(entry["config"])
                if refused:
                    raise ValueError(f"Claude MCP connection {name}: fields {refused} are not credential-free "
                                     "configuration (values are not shown); " + _SOURCE_ROUTE)
                entry["environment_keys"] = list(dict.fromkeys([*entry["environment_keys"], *referenced]))
        except ValueError as exc:
            entry, kind, reason = None, "refused", str(exc)
    if entry is None:
        if existing is not None:
            reason += "; the existing project entry is kept unchanged"
        missing.append(f"{transport} MCP {name}: {reason}")
        return {"action": kind, "detail": reason}, None
    report = {"action": "unchanged" if entry == existing else "imported"}
    return _with_enabled_state(transport, name, entry, disabled, report, missing), (None if entry == existing else entry)


def _with_enabled_state(transport: str, name: str, entry: dict, native_disabled: bool, report: dict,
                        missing: list[str]) -> dict:
    """Add a Codex entry's approval (informational) and a disabled state from any source to its report."""
    if transport == "codex_cli":
        # Informational only: native policy for this server may already allow its tools.
        report["approval"] = entry["config"].get("default_tools_approval_mode", "not_set_in_project_entry")
        if native_disabled or entry["config"].get("enabled") is False:
            report["disabled"] = True
            where = "the native Codex configuration" if native_disabled else "the selected entry (enabled=false)"
            missing.append(f"{transport} MCP {name}: disabled by {where}; a run that declares it is refused until "
                           "its owner enables it")
    return report


def _next_steps(root: Path, providers: dict, connections: dict) -> list[str]:
    steps = []
    for transport, report in providers.items():
        if report["started"] and report.get("login") == "not_verified":
            steps.append(f"Human: if {transport} is not signed in yet, sign in natively ({_LOGIN_COMMANDS[transport]}) "
                         "in a terminal. The CLI has no documented login-status command, so setup cannot check it; "
                         "the verification call below shows whether the sign-in works. Setup never starts a login.")
        elif report["started"] and report.get("login") != "logged_in":
            steps.append(f"Human: sign in to {transport} natively ({_LOGIN_COMMANDS[transport]}) in a terminal, "
                         "then rerun this setup command to refresh the status. Setup never starts a login.")
    for transport, entries in connections.items():
        tool = _PROGRAM_NAMES[transport]
        for name, report in entries.items():
            if report["action"] in ("imported", "unchanged", "kept"):
                steps.append(f"Human: if {transport} MCP {name} needs sign-in or consent, complete it in the CLI "
                             f"({tool} mcp login {name}); setup did not check its authorization or permission.")
            if report.get("approval") == "not_set_in_project_entry":
                steps.append(f"Owner choice (not verified, not required by setup): Codex runs with "
                             f"approval_policy=\"never\", so {name} tools that still ask for local confirmation are "
                             "refused. The owner may set default_tools_approval_mode=\"approve\" for this server in "
                             "the native Codex config or the project entry; setup never sets it.")
    for transport, report in providers.items():
        if not report["started"]:
            continue
        extra = ("--model", "<model>", "--effort", "<effort>") if transport in ("codex_cli", "antigravity_cli") else ()
        steps.append(f"Agent (after any human steps above): verify {transport} with one real model call using the "
                     "packaged check: " + _shell_command(
                         sys.executable, "-m", "agent_runtime.testing.conformance_pattern_examples", "run",
                         "--pattern", "structured_output", "--transport", transport, "--cli-path", report["program"],
                         *extra, "--root", "<new examples folder>"))
    steps.append("Agent: run the project's registered task (see the agent-runtime-test-run Skill): " + _shell_command(
        "agent-runtime-test-run", "--root", str(root), "--selection", "<selection file>", "--input", "<input file>")
        + "; a task that declares an MCP connection is the check that its data calls work.")
    return steps


def _shell_command(program: str, *arguments: str) -> str:
    """One copyable command line for this host's ordinary shell (PowerShell on Windows)."""
    if os.name != "nt":
        return shlex.join((program, *arguments))

    def quote(value: str) -> str:
        return value if re.fullmatch(r"[A-Za-z0-9_.:=-]+", value) else "'" + value.replace("'", "''") + "'"

    return "& " + " ".join(quote(item) for item in (program, *arguments))


def _mcp_name(value: str) -> str:
    if not _MCP_NAME.fullmatch(value) or value in _RESERVED_MCP_NAMES:
        raise argparse.ArgumentTypeError(f"MCP connection names use [a-z0-9_-]{{1,64}}: {value!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-runtime-setup", description=(
        "Prepare a project root for Runtime: install the operator Skills, record the native Claude/Codex/Antigravity "
        "programs and selected MCP connections in root/.runtime/config.json, and report what is still missing. "
        "It never logs in, approves tools or calls a model."))
    parser.add_argument("--root", required=True, type=Path, help="Project root to prepare (created when absent).")
    parser.add_argument("--provider", required=True, choices=sorted(_TRANSPORTS), help="CLI(s) to prepare: claude, codex, both (Claude and Codex) or antigravity (agy; structured_output and file_review only, no MCP).")
    parser.add_argument("--claude-path", type=Path, help="Native claude.exe to record; replaces the configured program. Default: the configured program, else PATH, else the npm package.")
    parser.add_argument("--codex-path", type=Path, help="Native codex.exe to record; replaces the configured program. Default: the configured program, else PATH, else the npm package.")
    parser.add_argument("--antigravity-path", type=Path, help="Native agy.exe to record; replaces the configured program. Default: the configured program, else PATH, else %%LOCALAPPDATA%%\\agy\\bin\\agy.exe.")
    parser.add_argument("--mcp", action="append", default=[], type=_mcp_name, metavar="NAME", help="Existing MCP connection to add for each selected provider (repeatable). Unselected connections are never read or changed.")
    parser.add_argument("--mcp-config", type=Path, metavar="FILE", help="Explicit source for --mcp: an existing Runtime config (mcp_servers.<transport>.<name>, copied with its tool, approval and environment-name choices) or a Claude mcpServers file. Without it Codex reads codex mcp get NAME --json.")
    return parser


def main(argv=None):
    """Prepare one project root and print a JSON report.

    Runs setup_runtime, then for each selected provider chooses the native
    program (explicit --claude-path/--codex-path, else the configured
    provider_cli_paths value, else a native program on PATH, else the CLI's npm
    package beside its PATH launcher) and starts it only for --version, its own
    login-status command and, for a selected Codex connection without
    --mcp-config, codex mcp get NAME --json. A configured program that cannot be started is
    kept and reported; only an explicit path replaces it. Selected --mcp
    connections come from --mcp-config FILE when given (no fallback), else
    from codex mcp get NAME --json for Codex; a Claude connection without FILE
    keeps an existing project entry. Literal credentials and fields that
    cannot be expressed as credential-free configuration refuse that connection
    and keep its existing entry; values are never printed. Only
    provider_cli_paths and the selected mcp_servers entries change; other
    config content stays as written. Exit 0 means prepared with nothing
    missing, 1 means items are missing or setup failed (changed_paths lists
    what was already written), 2 means invalid arguments, 130 interruption.
    Preparation is not verification: login status, a found program or a
    configured MCP connection do not prove a model or data call works.
    """
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.mcp_config is not None and not args.mcp_config.is_file():
        parser.error(f"--mcp-config must name an existing file: {args.mcp_config}")
    if args.mcp_config is not None and not args.mcp:
        parser.error("--mcp-config is a source for the connections selected with --mcp NAME")
    transports = _TRANSPORTS[args.provider]
    explicit = {"claude_cli": args.claude_path, "codex_cli": args.codex_path, "antigravity_cli": args.antigravity_path}
    for transport, value in explicit.items():
        if value is not None and transport not in transports:
            choices = "antigravity" if transport == "antigravity_cli" else f"{_PROGRAM_NAMES[transport]} or both"
            parser.error(f"{_PATH_FLAGS[transport]} needs --provider {choices}")
    if args.mcp and "antigravity_cli" in transports:
        parser.error("--mcp is not available for --provider antigravity: MCP is outside the first Antigravity binding")
    root = Path(args.root).resolve()
    report = {"root": str(root), "status": "failed", "changed_paths": [], "providers": {}, "mcp_servers": {},
              "missing": [], "next_steps": [],
              "note": ("Preparation only: a found program, a login status or a configured MCP connection is not "
                       "proof that a model call or an MCP data call succeeds; the next steps verify that.")}
    try:
        report["changed_paths"] = [str(path) for path in setup_runtime(root)]
        config_path = _target(root, ".runtime", "config.json")
        before = _read(config_path)
        config = _parse_runtime_config(root, before)
        document = {} if before is None else json.loads(before)
        source = None
        if args.mcp_config is not None:
            source = json.loads(args.mcp_config.read_text(encoding="utf-8-sig"))
            if type(source) is not dict:
                raise ValueError(f"--mcp-config {args.mcp_config} is not a JSON object")
        missing = report["missing"]
        paths = dict(document.get("provider_cli_paths", {}))
        servers = {transport: dict(entries) for transport, entries in document.get("mcp_servers", {}).items()}
        for transport in transports:
            program, write = _prepare_program(transport, explicit[transport],
                                              config["provider_cli_paths"].get(transport), root, missing)
            report["providers"][transport] = program
            if write is not None:
                paths[transport] = write
        for transport in transports:
            program = report["providers"][transport]
            started = program["program"] if program["started"] else None
            for name in dict.fromkeys(args.mcp):
                existing = config["mcp_servers"].get(transport, {}).get(name)
                connection, entry = _prepare_connection(transport, name, source, args.mcp_config, existing,
                                                        started, root, missing)
                report["mcp_servers"].setdefault(transport, {})[name] = connection
                if entry is not None:
                    servers.setdefault(transport, {})[name] = entry
        updated = dict(document)
        if paths:
            updated["provider_cli_paths"] = paths
        if servers:
            updated["mcp_servers"] = servers
        if updated != document:
            body = (json.dumps(updated, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            _parse_runtime_config(root, body)  # Same validation as every later load.
            if _read(config_path) != before:
                raise ValueError(f"Runtime config changed during setup: {config_path}")
            _write(config_path, body)
            report["changed_paths"].append(str(config_path))
        report["next_steps"] = _next_steps(root, report["providers"], report["mcp_servers"])
        report["status"] = "incomplete" if missing else "prepared"
    except KeyboardInterrupt:
        report["error"] = {"error_type": "KeyboardInterrupt", "detail": "setup interrupted"}
        print(json.dumps(report, indent=2))
        return 130
    except (OSError, ValueError) as exc:
        report["error"] = {"error_type": type(exc).__name__, "detail": str(exc)}
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "prepared" else 1


if __name__ == "__main__":
    raise SystemExit(main())
