"""Outer CLI boundary (R6): native configuration, selected MCP, project folders and retention.

Deterministic tests cover the exact recorded-tool mapping, Claude rules and
settings per layout, host command permits, MCP admission and rendering, the
Codex host-MCP selection, project directory validation, material operations on
the current host, private-directory retention, and Attempt seal and maintenance.
fake_run tests drive the real Adapters with a named substitute for the Provider
process (canned events or an injected invoker) to check redaction, layouts and
refusals end to end. One real_run test checks the Codex config merge against the
host-named binary with a synthetic CODEX_HOME (no model call).
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_runtime.contracts.registry_release_definition import OutputResolutionPolicy
from agent_runtime.execution.execution_local_invocation import _execution_profile_for_requirements
from agent_runtime.execution.execution_module_invocation import _assert_admitted_test_evaluation_profile
from agent_runtime.foundation.foundation_environment_setup import load_runtime_config
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.invocation import invocation_codex_module_invocation as codex
from agent_runtime.invocation import invocation_provider_tool_execution as provider_tools
from agent_runtime.invocation import invocation_workspace_preparation as workspaces
from agent_runtime.invocation.invocation_host_platform_selection import HOST_PLATFORM, private_temporary_directory
from agent_runtime.invocation.invocation_tool_definition import callback_tool_names, declared_mcp_connections
from agent_runtime.registry.registry_module_authoring import ModuleReviewer
import test_agent_runtime_codex_execution_outcomes as codex_cases
import test_agent_runtime_native_structured_output as native
from test_agent_runtime_claude_native_tools import SYNTHETIC_CLAUDE_HELP
from test_agent_runtime_module_authoring import _requirements


WINDOWS = os.name == "nt"
SENTINEL = "SENTINEL-VALUE-7f3a9c"
_HELP = SYNTHETIC_CLAUDE_HELP  # One synthetic help text for every Claude test.


def _claude_profile(**changes):
    return _execution_profile_for_requirements(_requirements(**changes), transport_kind="claude_cli",
                                               model_id="claude-opus-5", reasoning_profile="high",
                                               run_timeout_seconds=600)


def _adapter(tmp_path, **options):
    executable = tmp_path / "claude.exe"
    executable.write_bytes(b"")
    return claude.ClaudeAdapter(release_registry=None, artifact_host=None, workspace_root=tmp_path / "attempts",
                                cli_path=executable, **options)


def _allowed(argv):
    return argv[argv.index("--allowedTools") + 1:argv.index("--settings")]


# --- Claude argv, tools and permits -------------------------------------------------------------

@pytest.mark.deterministic
@pytest.mark.parametrize("tools,expected_tools,expected_allowed", [
    (("read",), "Read", ["Read"]),
    (("search",), "Grep,Glob", ["Grep", "Glob"]),
    (("read", "search", "shell"), "Read,Grep,Glob,Bash", ["Read", "Grep", "Glob"]),
    (("edit", "read", "search", "shell"), "Edit,Write,Read,Grep,Glob,Bash",
     ["Read", "Grep", "Glob", "Edit(./scratch/**)"]),
])
def test_claude_maps_exactly_the_recorded_tools_without_injection(tmp_path, tools, expected_tools, expected_allowed):
    profile = _claude_profile(tool_policy=tools, network_policy="provider_managed")
    argv = _adapter(tmp_path).build_command(profile=profile, settings={}, output_schema={"type": "object"},
                                            write_rule="./scratch/**" if "edit" in tools else None)
    assert argv[argv.index("--tools") + 1] == expected_tools
    assert _allowed(argv) == expected_allowed
    assert "Bash" not in _allowed(argv)  # Plain Bash is never pre-approved.
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert "--setting-sources" not in argv and "--restricted" not in argv
    assert "--safe-mode" in argv and "--strict-mcp-config" in argv
    if "edit" not in tools:
        assert "Edit" not in argv[argv.index("--tools") + 1] and "Write" not in argv[argv.index("--tools") + 1]


@pytest.mark.deterministic
def test_claude_command_permits_render_as_bash_rules_only(tmp_path):
    profile = _claude_profile(network_policy="provider_managed")
    permitted = _adapter(tmp_path, command_permits=("python -m pytest:*", "git diff:*"))
    argv = permitted.build_command(profile=profile, settings={}, output_schema={"type": "object"})
    assert _allowed(argv) == ["Read", "Grep", "Glob", "Bash(python -m pytest:*)", "Bash(git diff:*)"]
    shell_only = _claude_profile(tool_policy=("shell",), network_policy="provider_managed")
    argv = _adapter(tmp_path).build_command(profile=shell_only, settings={}, output_schema={"type": "object"})
    assert argv[argv.index("--tools") + 1] == "Bash" and _allowed(argv) == [""]


@pytest.mark.deterministic
@pytest.mark.parametrize("permits", [("",), ("a\nb",), ("Bash(x)",), (" padded",), ["python:*"]])
def test_claude_refuses_invalid_command_permits(tmp_path, permits):
    with pytest.raises(ValueError, match="command_permits"):
        _adapter(tmp_path, command_permits=permits)


@pytest.mark.deterministic
def test_claude_edit_needs_a_writable_workspace_and_project_policy_needs_agent():
    with pytest.raises(ValueError):
        _claude_profile(tool_policy=("edit", "read"), attempt_workspace_policy="none")
    with pytest.raises(ValueError):
        _claude_profile(execution_mode="tool_free", tool_policy=(), attempt_workspace_policy="explicit_project_read_write")
    project = _claude_profile(tool_policy=("edit", "read"), attempt_workspace_policy="explicit_project_read_write",
                              network_policy="provider_managed")
    assert (project.executor_adapter_id, project.executor_adapter_revision) == ("claude_cli_adapter", "v4")


@pytest.mark.deterministic
def test_claude_native_settings_per_layout(tmp_path):
    work, temporary = tmp_path / "attempt" / "work", tmp_path / "crt"
    materials = work / "materials"
    managed = claude._native_settings(profile=_claude_profile(network_policy="provider_managed"), cwd=work,
        materials=materials, read_dependencies=(), write_rule="./scratch/**", cli_temporary=temporary, draft_layout=True)
    assert managed == {"permissions": {"blockReadsOutsideWorkingDirectories": True,
        "blockWritesOutsideWorkingDirectories": True, "additionalDirectories": [], "deny": ["Edit(./materials/**)"]}}
    denied_no_shell = claude._native_settings(profile=_claude_profile(tool_policy=("read", "search")), cwd=work,
        materials=materials, read_dependencies=(), write_rule=None, cli_temporary=temporary, draft_layout=True)
    assert "sandbox" not in denied_no_shell and "deny" not in denied_no_shell["permissions"]
    denied_shell = claude._native_settings(profile=_claude_profile(tool_policy=("edit", "shell")), cwd=work,
        materials=materials, read_dependencies=(), write_rule="./scratch/**", cli_temporary=temporary, draft_layout=True)
    sandbox = denied_shell["sandbox"]
    assert sandbox["filesystem"] == {"allowWrite": [str(work / "scratch"), str(temporary)]}
    assert sandbox["network"]["allowedDomains"] == [] and sandbox["autoAllowBashIfSandboxed"] is False
    assert set(sandbox) == {"enabled", "failIfUnavailable", "allowUnsandboxedCommands", "autoAllowBashIfSandboxed",
                            "filesystem", "network"}
    project = tmp_path / "project"
    explicit = claude._native_settings(profile=_claude_profile(tool_policy=("edit", "read"),
        attempt_workspace_policy="explicit_project_read_write", network_policy="provider_managed"), cwd=project,
        materials=materials, read_dependencies=(), write_rule="./**", cli_temporary=temporary, draft_layout=False)
    assert explicit["permissions"]["additionalDirectories"] == [str(materials)] and "deny" not in explicit["permissions"]
    assert claude._native_settings(profile=_claude_profile(execution_mode="tool_free", tool_policy=(),
        attempt_workspace_policy="none"), cwd=work, materials=materials, read_dependencies=(), write_rule=None,
        cli_temporary=temporary, draft_layout=False) == {}


@pytest.mark.deterministic
def test_claude_environment_keeps_host_path_and_git_bash_but_not_secrets():
    assert {"PATH", "CLAUDE_CODE_GIT_BASH_PATH", "HTTPS_PROXY"} <= claude._ENVIRONMENT_KEYS
    filtered = HOST_PLATFORM.provider_environment({"PATH": "p", "CLAUDE_CODE_GIT_BASH_PATH": "g",
                                                   "RUNTIME_DATABASE_URL": "postgres://secret"}, claude._ENVIRONMENT_KEYS)
    assert filtered["PATH"] == "p" and filtered["CLAUDE_CODE_GIT_BASH_PATH"] == "g"
    assert "RUNTIME_DATABASE_URL" not in filtered


# --- Claude execution through the adapter (fake process runner) --------------------------------

def _claude_compiled(tmp_path, *, tools, workspace="none", network="provider_managed"):
    return native._compile_native_module(tmp_path, output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="claude_boundary", executor_adapter_id="claude_cli_adapter", executor_adapter_revision="v4",
        transport_kind="claude_cli", provider_id="anthropic", model_id="claude-opus-5[1m]", reasoning_profile="xhigh",
        execution_mode="agent", attempt_workspace_policy=workspace, tool_policy=tools, network_policy=network)


def _claude_call(tmp_path, monkeypatch, compiled, *, suffix, **options):
    import test_agent_runtime_claude_native_tools as claude_cases
    registry = native._register_compiled_for_evaluation(compiled)
    cell = native.InMemoryCellArtifactStore()
    prompt = native._evaluation_prompt(cell, compiled, suffix=suffix)
    request = native._direct_adapter_request(compiled, prompt, suffix=suffix)
    monkeypatch.setattr(claude, "_run_cli_preflight",
                        lambda argv, **kw: "2.1.316 (Claude Code)\n" if argv[-1] == "--version" else _HELP)
    seen = {}

    def runner(**fields):
        argv = fields["argv"]
        seen.update(fields)
        position = argv.index("--mcp-config")
        if not argv[position + 2].startswith("--"):
            seen["mcp_json_path"] = Path(argv[position + 2])
            seen["mcp_json"] = json.loads(seen["mcp_json_path"].read_text(encoding="utf-8"))
        lines = [json.dumps(event) for event in (claude_cases._init(()), claude_cases._result())]
        for line in lines:
            assert fields["on_stdout_line"](line)
        result = subprocess.CompletedProcess(argv, 0, "\n".join(lines), "")
        result.stdout_bytes, result.stderr_bytes = result.stdout.encode(), b""
        return result

    executable = tmp_path / "claude.exe"
    executable.write_bytes(b"")
    adapter = claude.ClaudeAdapter(release_registry=registry, artifact_host=cell, workspace_root=tmp_path / "attempts",
                                   cli_path=executable, process_runner=runner, **options)
    result = adapter.execute(request, native._RecordingHost())
    trace = json.loads(cell.read_bytes(result.cell_local_trace_ref, result.cell_local_trace_sha256))
    return result, trace, seen, cell


_FIXTURE = {"config": {"command": "python", "args": ["server.py"], "env": {"FIXTURE_TOKEN": "${FIXTURE_TOKEN}"}},
            "environment_keys": ["FIXTURE_TOKEN"]}


@pytest.mark.fake_run
def test_claude_declared_mcp_reaches_the_cli_only_through_a_private_mcp_json(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_TOKEN", SENTINEL)
    monkeypatch.setenv("RUNTIME_DATABASE_URL", "postgres://never-forwarded")
    mcp_loaded = "mcp" in sys.modules
    servers = {"fixture": _FIXTURE, "other": {"config": {"command": "other"}, "environment_keys": []}}
    result, trace, seen, _ = _claude_call(tmp_path, monkeypatch, _claude_compiled(tmp_path, tools=("mcp__fixture",)),
                                          suffix="mcp", mcp_servers=servers)
    assert result.terminal_status == "completed", trace.get("adapter_failure")
    argv = seen["argv"]
    assert "--safe-mode" not in argv and "--strict-mcp-config" in argv and "mcp__fixture" in _allowed(argv)
    assert seen["mcp_json"] == {"mcpServers": {"fixture": _FIXTURE["config"]}}  # Undeclared "other" is not passed.
    assert seen["mcp_json_path"].parent.name.startswith("crt-") and not seen["mcp_json_path"].exists()
    assert seen["environment"]["FIXTURE_TOKEN"] == SENTINEL and "RUNTIME_DATABASE_URL" not in seen["environment"]
    assert SENTINEL not in json.dumps(argv) and SENTINEL not in json.dumps(trace)
    assert trace["environment"]["FIXTURE_TOKEN"] == "<redacted>"
    effective = trace["effective_configuration"]
    digest = hashlib.sha256(json.dumps(_FIXTURE["config"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert effective["mcp_connections"] == [{"name": "fixture", "config_sha256": digest,
                                             "environment_keys": ["FIXTURE_TOKEN"]}]
    assert effective["tools"] == ["mcp__fixture"] and effective["command_scope"] == "none"
    assert "server.py" not in json.dumps(trace["argv"])  # Only the private mcp.json path is in argv.
    assert "isolation_gaps" not in trace and "provider_state_isolated" not in trace
    assert mcp_loaded or "mcp" not in sys.modules  # An ordinary call imports no MCP library.


@pytest.mark.fake_run
def test_claude_unconfigured_mcp_is_refused_and_undeclared_is_not_passed(tmp_path, monkeypatch):
    result, trace, seen, cell = _claude_call(tmp_path, monkeypatch,
        _claude_compiled(tmp_path, tools=("mcp__missing",)), suffix="missing", mcp_servers={"fixture": _FIXTURE})
    assert result.terminal_status == "failed" and not seen
    assert native._direct_failure_detail(result, cell)["failure_code"] == "ADAPTER_BINDING_UNAVAILABLE"
    assert trace["stage"] == "mcp_connection"
    (tmp_path / "plain").mkdir()
    result, trace, seen, _ = _claude_call(tmp_path / "plain", monkeypatch, _claude_compiled(tmp_path / "plain",
        tools=("read", "search"), workspace="own_draft_read_write"), suffix="plain", mcp_servers={"fixture": _FIXTURE})
    assert result.terminal_status == "completed"
    argv = seen["argv"]
    assert "--safe-mode" in argv and "mcp_json" not in seen and not any("mcp__" in value for value in argv)
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'


@pytest.mark.fake_run
def test_claude_project_directory_is_the_cwd_and_only_write_grant(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    compiled = _claude_compiled(tmp_path, tools=("edit", "read", "search", "shell"), workspace="explicit_project_read_write")
    result, trace, seen, _ = _claude_call(tmp_path, monkeypatch, compiled, suffix="project", project_directory=project,
                                          command_permits=("python -m pytest:*",))
    assert result.terminal_status == "completed", trace.get("adapter_failure")
    assert Path(seen["cwd"]) == project.resolve()
    assert _allowed(seen["argv"]) == ["Read", "Grep", "Glob", "Edit(./**)", "Bash(python -m pytest:*)"]
    effective = trace["effective_configuration"]
    assert effective["write_directories"] == [str(project.resolve())]
    assert effective["command_scope"] == "native_read_only_plus_permits"
    assert effective["command_permits"] == ["python -m pytest:*"] and effective["file_io"] == "native_tools"
    assert effective["permission_mode"] == "dontAsk" and effective["cli_version"] == "2.1.316 (Claude Code)"
    assert "sandbox" not in trace["settings"]


@pytest.mark.fake_run
def test_claude_draft_layout_runs_in_work_with_scratch_writes_and_materials_denied(tmp_path, monkeypatch):
    compiled = _claude_compiled(tmp_path, tools=("edit", "read", "search"), workspace="own_draft_read_write")
    result, trace, seen, _ = _claude_call(tmp_path, monkeypatch, compiled, suffix="draft")
    assert result.terminal_status == "completed", trace.get("adapter_failure")
    assert Path(seen["cwd"]).name == "work" and (Path(seen["cwd"]) / "scratch").is_dir()
    assert _allowed(seen["argv"]) == ["Read", "Grep", "Glob", "Edit(./scratch/**)"]
    assert trace["settings"]["permissions"]["deny"] == ["Edit(./materials/**)"]
    assert trace["effective_configuration"]["command_scope"] == "none"


@pytest.mark.fake_run
@pytest.mark.parametrize("case", ["missing_project", "unexpected_project", "permits_without_shell"])
def test_claude_refuses_inconsistent_call_resources_before_any_process(tmp_path, monkeypatch, case):
    project = tmp_path / "project"
    project.mkdir()
    workspace = "explicit_project_read_write" if case == "missing_project" else "own_draft_read_write"
    compiled = _claude_compiled(tmp_path, tools=("read", "search"), workspace=workspace)
    options = {"unexpected_project": {"project_directory": project},
               "permits_without_shell": {"command_permits": ("python -m pytest:*",)}}.get(case, {})
    result, trace, seen, cell = _claude_call(tmp_path, monkeypatch, compiled, suffix=case, **options)
    assert result.terminal_status == "failed" and not seen
    assert native._direct_failure_detail(result, cell)["failure_code"] == "ADAPTER_REQUEST_INVALID"


# --- Codex: MCP selection, permits and capture ------------------------------------------------

def _codex_mcp_env(tmp_path, invocation, *, tools=("mcp__fixture",), workspace="none", **executor_options):
    compiled = native._compile_native_module(tmp_path, output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        executor_adapter_id="codex_cli_agent_workspace_executor", executor_adapter_revision="v5",
        execution_mode="agent", attempt_workspace_policy=workspace, tool_policy=tools, network_policy="provider_managed")
    registry = native._register_compiled_for_evaluation(compiled)
    artifacts = native.InMemoryCellArtifactStore()
    prompt = native._evaluation_prompt(artifacts, compiled, suffix="codex_boundary")
    calls = []

    def invoker(**fields):
        calls.append(fields)
        return invocation(fields)

    executor = codex.CodexCliAgentWorkspaceModuleExecutor(release_registry=registry, artifact_host=artifacts,
        workspace_root=tmp_path / "workspaces", invoker=invoker, codex_bin="never-executed-codex", **executor_options)
    request = native._direct_adapter_request(compiled, prompt, suffix="codex_boundary")
    return executor, request, artifacts, calls


def _codex_execute(executor, request, artifacts):
    result = executor.execute(request, native._RecordingHost())
    trace = json.loads(artifacts.read_bytes(result.cell_local_trace_ref, result.cell_local_trace_sha256))
    return result, trace


@pytest.mark.fake_run
def test_codex_sentinel_reaches_only_the_child_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("FIXTURE_TOKEN", SENTINEL)
    servers = {"fixture": {"config": {"command": "python", "args": ["server.py"], "env_vars": ["FIXTURE_TOKEN"]},
                           "environment_keys": ["FIXTURE_TOKEN"]}}
    executor, request, artifacts, calls = _codex_mcp_env(tmp_path, lambda _: codex_cases.process(),
                                                         mcp_servers=servers)
    result, trace = _codex_execute(executor, request, artifacts)
    assert result.terminal_status == "completed", trace.get("adapter_failure")
    (call,) = calls
    assert call["environment"]["FIXTURE_TOKEN"] == SENTINEL
    assert SENTINEL not in json.dumps(call["argv"]) and SENTINEL not in json.dumps(trace)
    assert "FIXTURE_TOKEN" in trace["environment_keys"]
    assert any(value.startswith("mcp_servers.fixture=<host MCP config sha256:") for value in trace["argv"])
    assert not any("server.py" in value for value in trace["argv"])
    effective = trace["effective_configuration"]
    assert [entry["name"] for entry in effective["mcp_connections"]] == ["fixture"]
    assert effective["file_io"] == "unavailable_without_shell" and effective["command_scope"] == "none"
    assert effective["host_mcp_disabled"] == [] and effective["host_mcp_overlaid"] == []


@pytest.mark.fake_run
def test_codex_unconfigured_mcp_and_command_permits_are_refused_before_the_invoker(tmp_path):
    executor, request, artifacts, calls = _codex_mcp_env(tmp_path, lambda _: pytest.fail("invoker reached"))
    result, _ = _codex_execute(executor, request, artifacts)
    assert native._direct_failure_detail(result, artifacts)["failure_code"] == "ADAPTER_BINDING_UNAVAILABLE"
    executor, request, artifacts, calls = _codex_mcp_env(tmp_path / "permits", lambda _: pytest.fail("invoker reached"),
        tools=("read", "search", "shell"), workspace="own_draft_read_write", command_permits=("python -m pytest:*",))
    result, _ = _codex_execute(executor, request, artifacts)
    assert native._direct_failure_detail(result, artifacts)["failure_code"] == "ADAPTER_CAPABILITY_UNSUPPORTED"
    assert calls == []


@pytest.mark.fake_run
def test_codex_valid_result_with_incomplete_capture_is_accepted(tmp_path):
    raw = codex_cases.raw_events([codex_cases.message(), codex_cases.terminal()])
    env = codex_cases.environment(tmp_path, lambda _: codex.CodexCliInvocationResult(
        0, raw.decode(), "", stdout_bytes=raw, stderr_bytes=b"", process_output_complete=False))
    result, trace = codex_cases.execute(env)
    assert result.terminal_status == "completed" and trace["process_output_complete"] is False
    broken = codex_cases.environment(tmp_path / "broken", lambda _: codex.CodexCliInvocationResult(
        0, "not json\n", "", stdout_bytes=b"not json\n", stderr_bytes=b"", process_output_complete=False))
    result, _ = codex_cases.execute(broken)
    assert result.terminal_status == "failed"


def _listing_runner(listings, calls):
    def runner(**fields):
        calls.append(fields["argv"])
        body = listings.pop(0)
        if isinstance(body, int):
            return subprocess.CompletedProcess(fields["argv"], body, "", "failed")
        return subprocess.CompletedProcess(fields["argv"], 0, body if isinstance(body, str) else json.dumps(body), "")
    return runner


_EXEC = ["codex", "exec", "-c", "mcp_servers={}", "-c", "mcp_servers.fixture={command=\"python\"}",
         "-C", "/work", "--json", "-"]


@pytest.mark.deterministic
def test_codex_disables_enabled_host_servers_the_call_does_not_declare(monkeypatch, tmp_path):
    calls = []
    host = [{"name": "host_http", "enabled": True}, {"name": "host_off", "enabled": False}]
    verified = [{"name": "fixture", "enabled": True}, {"name": "host_http", "enabled": False},
                {"name": "host_off", "enabled": False}]
    monkeypatch.setattr(codex, "run_cli_process", _listing_runner([host, verified], calls))
    argv, disabled, overlaid = codex.select_codex_mcp_servers(list(_EXEC), cwd=tmp_path, environment={},
                                                              timeout_seconds=60)
    assert (disabled, overlaid) == (("host_http",), ())
    assert argv == [*_EXEC[:6], "-c", "mcp_servers.host_http.enabled=false", *_EXEC[6:]]
    # The first listing is the host configuration as this call sees it, without Runtime's own entry.
    assert calls[0] == ["codex", "-c", "mcp_servers={}", "mcp", "list", "--json"]
    assert calls[1] == ["codex", "-c", "mcp_servers={}", "-c", 'mcp_servers.fixture={command="python"}',
                        "-c", "mcp_servers.host_http.enabled=false", "mcp", "list", "--json"]


@pytest.mark.deterministic
def test_codex_reports_a_declared_name_overlaid_on_a_host_configured_server(monkeypatch, tmp_path):
    host = [{"name": "fixture", "enabled": True}]
    monkeypatch.setattr(codex, "run_cli_process", _listing_runner([host, host], []))
    argv, disabled, overlaid = codex.select_codex_mcp_servers(list(_EXEC), cwd=tmp_path, environment={},
                                                              timeout_seconds=60)
    assert (argv, disabled, overlaid) == (_EXEC, (), ("fixture",))


@pytest.mark.deterministic
@pytest.mark.parametrize("listings,message", [
    ([[{"name": "host_http", "enabled": True}], [{"name": "host_http", "enabled": True}]], "would enable"),
    ([[{"name": "fixture", "enabled": False}], [{"name": "fixture", "enabled": False}]], "would enable"),
    ([[{"name": "my.server", "enabled": True}]], "cannot be disabled"),
    (["not json"], "not JSON"),
    ([{"servers": []}], "unexpected shape"),
    ([2], "could not list"),
])
def test_codex_mcp_selection_refuses_before_exec_when_unconfirmed(monkeypatch, tmp_path, listings, message):
    monkeypatch.setattr(codex, "run_cli_process", _listing_runner(list(listings), []))
    with pytest.raises(OSError, match=message):
        codex.select_codex_mcp_servers(list(_EXEC), cwd=tmp_path, environment={}, timeout_seconds=60)


@pytest.mark.deterministic
def test_codex_tool_free_call_disables_every_host_server(monkeypatch, tmp_path):
    calls = []
    argv = ["codex", "exec", "-c", "mcp_servers={}", "-C", "/w", "-"]
    monkeypatch.setattr(codex, "run_cli_process", _listing_runner(
        [[{"name": "a", "enabled": True}, {"name": "b", "enabled": True}],
         [{"name": "a", "enabled": False}, {"name": "b", "enabled": False}]], calls))
    effective, disabled, overlaid = codex.select_codex_mcp_servers(argv, cwd=tmp_path, environment={},
                                                                   timeout_seconds=60)
    assert disabled == ("a", "b") and overlaid == () and len(calls) == 2
    assert effective == ["codex", "exec", "-c", "mcp_servers={}", "-c", "mcp_servers.a.enabled=false",
                         "-c", "mcp_servers.b.enabled=false", "-C", "/w", "-"]
    calls.clear()
    monkeypatch.setattr(codex, "run_cli_process", _listing_runner([[]], calls))
    assert codex.select_codex_mcp_servers(argv, cwd=tmp_path, environment={}, timeout_seconds=60) == (argv, (), ())
    assert len(calls) == 1  # Nothing configured and nothing declared: no second listing.


CODEX_REAL = pytest.mark.skipif(
    os.environ.get("AGENT_RUNTIME_REAL_RUN") != "1" or not os.environ.get("AGENT_RUNTIME_CODEX_BIN"),
    reason="unverified: set AGENT_RUNTIME_REAL_RUN=1 and AGENT_RUNTIME_CODEX_BIN to the host-named codex executable")


@pytest.mark.real_run
@CODEX_REAL
def test_real_codex_config_merge_keeps_only_declared_servers(tmp_path):
    """Synthetic CODEX_HOME with two harmless entries; read-only `mcp list`, no model, login or server start."""
    binary = os.environ["AGENT_RUNTIME_CODEX_BIN"]
    home, work = tmp_path / "codex_home", tmp_path / "work"
    home.mkdir()
    work.mkdir()
    missing = (tmp_path / "does_not_exist.exe").as_posix()
    (home / "config.toml").write_text(f'[mcp_servers.selected_fixture]\ncommand = "{missing}"\n\n'
                                      f'[mcp_servers.unselected_fixture]\ncommand = "{missing}"\n', encoding="utf-8")
    environment = HOST_PLATFORM.provider_environment(os.environ, codex._ENVIRONMENT_KEYS)
    environment["CODEX_HOME"] = str(home)

    def listed(overrides):
        completed = subprocess.run([binary, *overrides, "mcp", "list", "--json"], cwd=work, env=environment,
                                   capture_output=True, text=True, timeout=60)
        assert completed.returncode == 0, completed.stderr
        return {item["name"]: item["enabled"] for item in json.loads(completed.stdout)}

    connections = codex.validate_codex_mcp_connections({"selected_fixture": {"config": {"command": missing},
                                                                            "environment_keys": []}})
    profile = _execution_profile_for_requirements(_requirements(tool_policy=("mcp__selected_fixture",),
        attempt_workspace_policy="none", network_policy="provider_managed"), transport_kind="codex_cli",
        model_id="gpt-6-astra", reasoning_profile="low", run_timeout_seconds=600)
    argv = codex.build_workspace_command(profile=profile, main_folder=work, codex_bin=binary, permission_profile=None,
                                         mcp_connections=connections)
    # The regression: the empty table alone keeps the inherited, undeclared server enabled.
    assert listed(codex._codex_config_overrides(argv))["unselected_fixture"] is True
    effective, disabled, overlaid = codex.select_codex_mcp_servers(argv, cwd=work, environment=environment,
                                                                   timeout_seconds=60)
    # selected_fixture is also defined in the host config.toml, so the supplied entry is an overlay.
    assert (disabled, overlaid) == (("unselected_fixture",), ("selected_fixture",))
    assert listed(codex._codex_config_overrides(effective)) == {"selected_fixture": True, "unselected_fixture": False}
    tool_free = _execution_profile_for_requirements(_requirements(execution_mode="tool_free", tool_policy=(),
        attempt_workspace_policy="none", max_attempts=1), transport_kind="codex_cli", model_id="gpt-6-astra",
        reasoning_profile="low", run_timeout_seconds=600)
    argv = codex.build_command(profile=tool_free, workspace=work, codex_bin=binary)
    effective, disabled, overlaid = codex.select_codex_mcp_servers(argv, cwd=work, environment=environment,
                                                                   timeout_seconds=60)
    assert disabled == ("selected_fixture", "unselected_fixture") and overlaid == ()
    assert set(listed(codex._codex_config_overrides(effective)).values()) == {False}


# --- MCP admission and configuration ----------------------------------------------------------

@pytest.mark.deterministic
def test_mcp_names_are_connections_never_callbacks():
    policy = ("mcp__fixture", "read", "search", "shell", "lookup_record")
    assert declared_mcp_connections(policy) == ("fixture",)
    assert callback_tool_names(policy) == {"lookup_record"}


@pytest.mark.deterministic
@pytest.mark.parametrize("entries,match", [
    ({"Bad Name": {"config": {}}}, "not accepted"),
    ({"runtime_tools": {"config": {}}}, "not accepted"),
    ({"data": {"config": [], "environment_keys": []}}, "JSON object"),
    ({"data": {"config": {}, "environment_keys": ["1BAD"]}}, "environment variable names"),
    ({"data": {"config": {}, "extra": 1}}, "needs config"),
])
def test_runtime_config_refuses_malformed_mcp_entries(tmp_path, entries, match):
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime" / "config.json").write_text(json.dumps({"mcp_servers": {"claude_cli": entries}}))
    with pytest.raises(ValueError, match=match):
        load_runtime_config(tmp_path)


@pytest.mark.deterministic
def test_runtime_config_reads_attempt_root_and_mcp_entries(tmp_path):
    assert load_runtime_config(tmp_path)["attempt_workspace_root"] == tmp_path.resolve() / ".runtime" / "attempt_workspaces"
    (tmp_path / ".runtime").mkdir()
    document = {"attempt_workspace_root": "kept", "mcp_servers": {"codex_cli": {"data": {
        "config": {"url": "https://example.invalid/mcp", "bearer_token_env_var": "TOKEN"}, "environment_keys": ["TOKEN"]}}}}
    (tmp_path / ".runtime" / "config.json").write_text(json.dumps(document))
    config = load_runtime_config(tmp_path)
    assert config["attempt_workspace_root"] == tmp_path.resolve() / "kept"
    assert config["mcp_servers"]["codex_cli"]["data"]["environment_keys"] == ["TOKEN"]
    with pytest.raises(ValueError, match="mcp_servers must map"):
        (tmp_path / ".runtime" / "config.json").write_text(json.dumps({"mcp_servers": {"other_cli": {}}}))
        load_runtime_config(tmp_path)


# --- Network values, Reviewer default and admission -------------------------------------------

@pytest.mark.deterministic
def test_new_module_reviewer_requirements_carry_edit_and_provider_managed():
    requirements = ModuleReviewer._default_execution_requirements
    assert requirements.tool_policy == ("edit", "read", "search", "shell")
    assert requirements.network_policy == "provider_managed"
    assert requirements.attempt_workspace_policy == "own_draft_read_write"


@pytest.mark.deterministic
def test_provider_managed_and_denied_are_both_admitted_and_others_keep_their_meaning(tmp_path):
    for network in ("denied", "provider_managed"):
        profile = _claude_profile(network_policy=network)
        assert profile.network_policy == network
        compiled = _claude_compiled(tmp_path, tools=("read",), workspace="own_draft_read_write", network=network)
        _assert_admitted_test_evaluation_profile(compiled.module, compiled.execution_profile)
    with pytest.raises(ValueError):
        _claude_profile(execution_mode="tool_free", tool_policy=(), attempt_workspace_policy="none",
                        network_policy="provider_managed")
    with pytest.raises(ValueError):
        _claude_profile(network_policy="direct_sandboxed")
    assert codex.CodexCliModuleExecutor.supported_network_policies == ("denied",)


@pytest.mark.deterministic
def test_callback_bridge_reports_the_socket_gap_before_opening_a_session(monkeypatch, tmp_path):
    from test_agent_runtime_codex_environment import self_test_request
    compiled = native._compile_native_module(tmp_path)
    prompt = native._evaluation_prompt(native.InMemoryCellArtifactStore(), compiled, suffix="bridge")
    request = self_test_request(native._direct_adapter_request(compiled, prompt, suffix="bridge"))

    class Factory:
        definitions = ()

        def open_session(self, request):
            pytest.fail("a session opened on a host without the socket transport")

    monkeypatch.setattr(provider_tools, "unix_socket_transport_available", lambda: False)
    with pytest.raises(NotImplementedError, match="Unix-domain socket"):
        provider_tools.ProviderToolSessionBridge(request=request, host=None, factory=Factory(), definitions=[],
                                                 timeout_seconds=60)


# --- Project directory --------------------------------------------------------------------------

@pytest.mark.deterministic
def test_project_directory_must_be_an_existing_separate_absolute_folder(tmp_path):
    root = tmp_path / "runtime" / "attempt_workspaces"
    root.mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    assert workspaces.validate_project_directory(project, workspace_root=root) == project.resolve()
    with pytest.raises(ValueError, match="absolute"):
        workspaces.validate_project_directory(Path("relative"), workspace_root=root)
    with pytest.raises(FileNotFoundError):
        workspaces.validate_project_directory(tmp_path / "missing", workspace_root=root)
    with pytest.raises(ValueError, match="filesystem root"):
        workspaces.validate_project_directory(Path(Path.cwd().anchor), workspace_root=root)
    for overlapping in (root, root / "inner", tmp_path / "runtime"):
        overlapping.mkdir(exist_ok=True)
        with pytest.raises(ValueError, match="overlaps"):
            workspaces.validate_project_directory(overlapping, workspace_root=root)
    file_path = tmp_path / "file.txt"
    file_path.write_text("x")
    with pytest.raises(ValueError, match="existing directory"):
        workspaces.validate_project_directory(file_path, workspace_root=root)


# --- Host material operations on the current host ---------------------------------------------

@pytest.mark.deterministic
def test_host_material_operations_capture_copy_verify_and_refuse_links(tmp_path):
    source = tmp_path / "source"
    (source / "sub").mkdir(parents=True)
    (source / "sub" / "a.txt").write_bytes(b"alpha")
    body, executable = HOST_PLATFORM.read_material_file(source, ("sub", "a.txt"))
    assert body == b"alpha" and executable in ({None} if WINDOWS else {False})
    copy = tmp_path / "copy"
    HOST_PLATFORM.write_material_tree(copy, [(("sub", "a.txt"), b"alpha", False), (("b.txt",), b"beta", False)])
    files, directories = HOST_PLATFORM.material_tree_state(copy)
    flag = None if WINDOWS else False
    assert files == {"sub/a.txt": (hashlib.sha256(b"alpha").hexdigest(), flag),
                     "b.txt": (hashlib.sha256(b"beta").hexdigest(), flag)}
    assert directories == {"sub"}
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"outside")
    if WINDOWS:
        from windows_junction_helpers import create_junction
        create_junction(source / "linked", outside)
    else:
        (source / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        HOST_PLATFORM.read_material_file(source, ("linked", "secret.txt"))
    assert (outside / "secret.txt").read_bytes() == b"outside"


# --- Private directories and Attempt maintenance ----------------------------------------------

@pytest.mark.deterministic
def test_private_directory_is_retained_only_when_the_stop_is_unconfirmed(tmp_path):
    with pytest.raises(OSError, match="retained at") as kept:
        with private_temporary_directory(prefix="crt-", parent=tmp_path, retain=lambda: True) as directory:
            retained = Path(directory)
            (retained / "mcp.json").write_text("{}")
    assert retained.is_dir() and str(retained) in str(kept.value)
    HOST_PLATFORM.remove_private_directory(retained, HOST_PLATFORM.private_directory_identity(retained))
    with private_temporary_directory(prefix="crt-", parent=tmp_path, retain=lambda: False) as directory:
        disposed = Path(directory)
    assert not disposed.exists()


def _attempt(root, attempt_id="attempt_a", *, sealed_at="2026-01-01T00:00:00Z"):
    folder = workspaces.prepare_attempt_workspace(workspace_root=root, attempt_identity={"attempt_id": attempt_id})
    if sealed_at is not None:
        workspaces.seal_attempt_workspace(workspace_root=root, attempt_id=attempt_id, effective_at_utc=sealed_at)
    return folder


_READY = workspaces.AttemptWorkspaceRetentionFacts(execution_ended=True, process_stop_confirmed=True,
                                                    archive_complete=True, retention_hold=False)


def _maintain(root, *, facts=None, retention=60, observed="2026-01-01T00:02:00Z", intent="cleanup", ids=("attempt_a",)):
    return workspaces.maintain_attempt_workspaces(workspace_root=root, facts=facts or {name: _READY for name in ids},
        retention_seconds=retention, observed_at_utc=observed, intent=intent)


@pytest.mark.deterministic
def test_seal_is_exclusive_idempotent_and_closes_the_folder_for_execution(tmp_path):
    root = tmp_path / "attempts"
    folder = _attempt(root)
    assert workspaces.seal_attempt_workspace(workspace_root=root, attempt_id="attempt_a",
                                             effective_at_utc="2026-01-01T00:00:00Z") == folder
    with pytest.raises(workspaces.AttemptWorkspaceConflictError, match="different seal"):
        workspaces.seal_attempt_workspace(workspace_root=root, attempt_id="attempt_a",
                                          effective_at_utc="2026-01-02T00:00:00Z")
    with pytest.raises(workspaces.AttemptWorkspaceConflictError, match="sealed"):
        workspaces.prepare_attempt_workspace(workspace_root=root, attempt_identity={"attempt_id": "attempt_a"})
    with pytest.raises(workspaces.AttemptWorkspaceConflictError, match="sealed"):
        with workspaces.lease_attempt_workspace(folder):
            pass
    with pytest.raises(ValueError):
        workspaces.seal_attempt_workspace(workspace_root=root, attempt_id="attempt_a", effective_at_utc="2026-01-01")
    open_folder = _attempt(root, "attempt_b", sealed_at=None)
    with workspaces.lease_attempt_workspace(open_folder):
        with pytest.raises(workspaces.AttemptWorkspaceConflictError):
            workspaces.seal_attempt_workspace(workspace_root=root, attempt_id="attempt_b",
                                              effective_at_utc="2026-01-01T00:00:00Z")


@pytest.mark.deterministic
def test_maintenance_query_never_deletes_and_cleanup_removes_only_listed_folders(tmp_path):
    root = tmp_path / "attempts"
    folder, other = _attempt(root), _attempt(root, "attempt_other")
    (result,) = _maintain(root, intent="query")
    assert (result.outcome, result.retained_reasons) == ("cleanable", ()) and folder.is_dir()
    (result,) = _maintain(root)
    assert result.outcome == "deleted" and not folder.exists()
    assert other.is_dir() and (root / ".agent_runtime_attempt_leases").is_dir()


@pytest.mark.deterministic
@pytest.mark.parametrize("change,reasons", [
    (dict(facts=dict(execution_ended=False)), ("execution_not_ended",)),
    (dict(facts=dict(process_stop_confirmed=False)), ("process_stop_unconfirmed",)),
    (dict(facts=dict(archive_complete=False)), ("archive_incomplete",)),
    (dict(facts=dict(retention_hold=True)), ("retention_hold",)),
    (dict(retention=None), ("retention_policy_absent",)),
    (dict(observed="2025-12-31T23:59:59Z"), ("time_evidence_inconsistent",)),
    (dict(observed="2026-01-01T00:00:30Z"), ("retention_not_expired",)),
])
def test_maintenance_retains_with_each_stated_reason(tmp_path, change, reasons):
    root = tmp_path / "attempts"
    folder = _attempt(root)
    facts = {"attempt_a": workspaces.AttemptWorkspaceRetentionFacts(**{**vars(_READY), **change.get("facts", {})})}
    options = {key: value for key, value in change.items() if key != "facts"}
    (result,) = _maintain(root, facts=facts, **options)
    assert (result.outcome, result.retained_reasons) == ("retained", reasons) and folder.is_dir()


@pytest.mark.deterministic
@pytest.mark.parametrize("case", ["unsealed", "foreign_marker", "missing", "linked", "leased"])
def test_maintenance_retains_unconfirmed_ownership_seal_or_live_lease(tmp_path, case):
    root = tmp_path / "attempts"
    if case == "unsealed":
        _attempt(root, sealed_at=None)
    elif case == "foreign_marker":
        folder = _attempt(root)
        (folder / ".agent_runtime_attempt.json").write_text(json.dumps({"attempt_id": "someone_else"}))
    elif case == "missing":
        root.mkdir()
    elif case == "linked":
        target = _attempt(tmp_path / "elsewhere")
        root.mkdir()
        if WINDOWS:
            from windows_junction_helpers import create_junction
            create_junction(root / "attempt_a", target)
        else:
            (root / "attempt_a").symlink_to(target, target_is_directory=True)
    if case == "leased":
        folder = _attempt(root)
        with workspaces._hold_lease(folder):
            (result,) = _maintain(root)
        assert folder.is_dir()
    else:
        (result,) = _maintain(root)
    expected = {"unsealed": "seal_unconfirmed", "leased": "lease_active"}.get(case, "ownership_unconfirmed")
    assert (result.outcome, result.retained_reasons) == ("retained", (expected,))
    if case == "linked":
        assert target.is_dir() and (target / ".agent_runtime_attempt.json").is_file()


@pytest.mark.deterministic
@pytest.mark.parametrize("change", [dict(intent="delete"), dict(retention=0), dict(observed="2026-01-01 00:02:00"),
                                    dict(facts={"attempt_a": object()}), dict(facts={"../escape": _READY})])
def test_maintenance_validates_every_input_before_touching_a_path(tmp_path, change):
    root = tmp_path / "never_created"
    with pytest.raises(ValueError):
        _maintain(root, **change)
    assert not root.exists()


@pytest.mark.deterministic
def test_maintenance_never_deletes_a_folder_replaced_after_its_identity_was_taken(tmp_path, monkeypatch):
    root = tmp_path / "attempts"
    folder = _attempt(root)
    original = HOST_PLATFORM.private_directory_identity
    moved = tmp_path / "moved_original"

    def identity_then_replace(directory):
        captured = original(directory)
        if Path(directory) == folder:
            folder.rename(moved)
            folder.mkdir()
            for name in (".agent_runtime_attempt.json", ".agent_runtime_attempt_seal.json"):
                (folder / name).write_bytes((moved / name).read_bytes())
            (folder / "replacement.txt").write_text("keep")
        return captured

    monkeypatch.setattr(HOST_PLATFORM, "private_directory_identity", identity_then_replace)
    (result,) = _maintain(root)
    assert result.outcome == "deletion_failed" and str(folder) in result.failure_message
    assert (folder / "replacement.txt").read_text() == "keep" and moved.is_dir()


@pytest.mark.deterministic
def test_cleanup_lifts_read_only_material_copies_inside_the_owned_folder(tmp_path):
    root = tmp_path / "attempts"
    folder = _attempt(root)
    HOST_PLATFORM.write_material_tree(folder / "materials", [(("sub", "a.txt"), b"alpha", False)])
    with pytest.raises(OSError):  # The default keeps refusing read-only entries.
        HOST_PLATFORM.remove_private_directory(folder / "materials",
                                               HOST_PLATFORM.private_directory_identity(folder / "materials"))
    assert (folder / "materials" / "sub" / "a.txt").read_bytes() == b"alpha"
    (result,) = _maintain(root)
    assert result.outcome == "deleted" and not folder.exists()


@pytest.mark.deterministic
@pytest.mark.parametrize("kind", ["hard_link", "directory_link"])
def test_cleanup_never_follows_or_alters_what_a_link_points_to(tmp_path, kind):
    root = tmp_path / "attempts"
    folder = _attempt(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "source.txt").write_bytes(b"source")
    if kind == "hard_link":
        os.link(outside / "source.txt", folder / "linked.txt")
    elif WINDOWS:
        from windows_junction_helpers import create_junction
        create_junction(folder / "linked", outside)
    else:
        (folder / "linked").symlink_to(outside, target_is_directory=True)
    (result,) = _maintain(root)
    if WINDOWS:  # Multiply-linked files and reparse points are refused by the held-handle disposal.
        assert result.outcome == "deletion_failed" and str(folder) in result.failure_message
    assert (outside / "source.txt").read_bytes() == b"source"


# --- Test-run archive and seal --------------------------------------------------------------

@pytest.mark.deterministic
def test_test_run_archives_then_seals_only_confirmed_ended_attempts(tmp_path):
    from agent_runtime.testing.conformance_local_test_run import _archive_and_seal
    root = tmp_path / "attempts"
    for name in ("attempt_a", "attempt_b", "attempt_c"):
        _attempt(root, name, sealed_at=None)
    attempts = [{"attempt_id": "attempt_a", "provider_log": {}},
                {"attempt_id": "attempt_b", "provider_log": {"cleanup_error": "stop not confirmed"}},
                {"attempt_id": "attempt_c", "failure_detail": {"failure_code": "claude_cli_cleanup_failed"}},
                {"attempt_id": "attempt_missing"}]
    project = tmp_path / "project"
    project.mkdir()
    rows = _archive_and_seal({"status": "failed", "execution_log": {"attempts": attempts}}, workspace_root=root,
                             project_directory=project)
    assert [(row["sealed"], row["seal_skipped_reason"]) for row in rows] == [
        (True, None), (False, "process_stop_unconfirmed"), (False, "process_stop_unconfirmed"),
        (False, "archive_not_saved")]
    assert all(row["project_directory"] == str(project.resolve()) for row in rows)
    assert Path(rows[0]["archive_path"]).is_file() and Path(rows[1]["archive_path"]).is_file()
    assert not any(project.iterdir())
    running = _archive_and_seal({"status": "running", "execution_log": {"attempts": [{"attempt_id": "attempt_b"}]}},
                                workspace_root=root, project_directory=None)
    assert running[0]["seal_skipped_reason"] == "execution_not_ended"

# --- Review r1 repairs: listing failures carry no listing content (F1) -------------------------

_LISTING_SECRET = "LISTING-PRIVATE-Authorization-3c1e"


def _listing_failure(kind, argv):
    content = json.dumps([{"name": "host_http", "enabled": True,
                           "http_headers": {"Authorization": _LISTING_SECRET}}])
    from agent_runtime.invocation import invocation_process_execution as processes
    raw = dict(output=content, stderr="stderr " + _LISTING_SECRET, stdout_bytes=content.encode(),
               stderr_bytes=("stderr " + _LISTING_SECRET).encode())
    if kind == "subprocess_timeout":
        raise subprocess.TimeoutExpired(argv, 30, output=content, stderr=raw["stderr"])
    if kind == "runtime_timeout":
        raise processes.CliProcessTimeout(argv, 30, returncode=None, **raw)
    if kind == "output_limit":
        raise processes.CliProcessError(None, argv, stop_reason="output_limit",
                                        message="Runtime process output limit reached; log incomplete", **raw)
    if kind == "stop_unconfirmed":
        raise processes.CliProcessError(None, argv, stop_reason="cleanup_error", message="CLI process cleanup failed",
                                        cleanup_error="synthetic listing stop uncertainty", **raw)
    if kind == "not_json":
        result = subprocess.CompletedProcess(argv, 0, "not json " + _LISTING_SECRET, "")
    else:  # "would_enable": the host server stays enabled after the disable override.
        result = subprocess.CompletedProcess(argv, 0, content, "")
    result.stdout_bytes, result.stderr_bytes = result.stdout.encode(), b""
    return result


@pytest.mark.fake_run
@pytest.mark.parametrize("kind,failure_class,failure_code", [
    ("subprocess_timeout", "timeout", "codex_cli_timeout"),
    ("runtime_timeout", "timeout", "codex_cli_timeout"),
    ("output_limit", "transport", "codex_cli_process_failed"),
    ("stop_unconfirmed", "transport", "codex_cli_cleanup_failed"),
    ("not_json", "dependency_unavailable", "ADAPTER_BINDING_UNAVAILABLE"),
    ("would_enable", "dependency_unavailable", "ADAPTER_BINDING_UNAVAILABLE"),
])
def test_codex_listing_failure_keeps_its_class_and_stop_facts_but_no_listing_content(
        tmp_path, monkeypatch, kind, failure_class, failure_code):
    env = codex_cases.environment(tmp_path, lambda _: pytest.fail("injected invoker unused"))
    env.executor._invoker = codex._default_invoke
    calls = []

    def runner(**fields):
        argv = fields["argv"]
        calls.append(argv)
        if argv[-1] == "--version":
            result = subprocess.CompletedProcess(argv, 0, "codex-cli 0.166.0", "")
            result.stdout_bytes, result.stderr_bytes = b"codex-cli 0.166.0", b""
            return result
        if argv[-3:] == ["mcp", "list", "--json"]:
            return _listing_failure(kind, argv)
        pytest.fail("codex exec started after a failed MCP listing")

    monkeypatch.setattr(codex, "run_cli_process", runner)
    result, trace = codex_cases.execute(env)
    detail = codex_cases.detail(env, result)
    assert result.terminal_status == "failed" and result.outputs == ()
    assert (result.failure.failure_class, detail["failure_code"]) == (failure_class, failure_code)
    assert trace["cli_probe"] == "codex mcp list" and not any("exec" in argv for argv in calls)
    assert _LISTING_SECRET not in json.dumps(trace) and _LISTING_SECRET not in json.dumps(detail)
    if "raw_streams" in trace:  # Runner failures keep their (now empty) capture facts.
        assert codex_cases.cli_stream_bytes(trace, "stdout") == b""
        assert codex_cases.cli_stream_bytes(trace, "stderr") == b""
    if kind == "stop_unconfirmed":  # Stop uncertainty is preserved: the schema folder is retained.
        assert "synthetic listing stop uncertainty" in trace["cleanup_error"]
        assert "retained at" in detail["provider_error_message"]


# --- Review r1 repairs: archive only inside the owned Attempt (F2) ------------------------------

@pytest.mark.deterministic
@pytest.mark.parametrize("redirect", ["directory_link", "plain_file"])
def test_archive_refuses_a_redirected_archive_folder_and_leaves_the_scene(tmp_path, redirect):
    from agent_runtime.testing.conformance_local_test_run import _archive_and_seal
    root = tmp_path / "attempts"
    folder = _attempt(root, sealed_at=None)
    outside = tmp_path / "outside_target"
    outside.mkdir()
    if redirect == "plain_file":
        (folder / "archive").write_bytes(b"not a folder")
    elif WINDOWS:
        from windows_junction_helpers import create_junction
        create_junction(folder / "archive", outside)
    else:
        (folder / "archive").symlink_to(outside, target_is_directory=True)
    (row,) = _archive_and_seal({"status": "completed", "execution_log": {"attempts": [
        {"attempt_id": "attempt_a", "provider_log": {}}]}}, workspace_root=root, project_directory=None)
    assert (row["sealed"], row["seal_skipped_reason"], row["archive_path"]) == (False, "archive_not_saved", None)
    assert "AttemptWorkspaceConflictError" in row["archive_error"]
    assert list(outside.iterdir()) == [] and not (folder / ".agent_runtime_attempt_seal.json").exists()
    if redirect == "plain_file":
        assert (folder / "archive").read_bytes() == b"not a folder"


@pytest.mark.deterministic
def test_archive_writes_once_inside_the_owned_attempt(tmp_path):
    root = tmp_path / "attempts"
    folder = _attempt(root, sealed_at=None)
    written = workspaces.write_attempt_archive(workspace_root=root, attempt_id="attempt_a",
                                               file_name="execution_record.json", content=b"{}")
    assert written == folder / "archive" / "execution_record.json" and written.read_bytes() == b"{}"
    with pytest.raises(OSError):  # Exclusive create: an existing archive is never replaced.
        workspaces.write_attempt_archive(workspace_root=root, attempt_id="attempt_a",
                                         file_name="execution_record.json", content=b"changed")
    assert written.read_bytes() == b"{}"
    with pytest.raises(ValueError):
        workspaces.write_attempt_archive(workspace_root=root, attempt_id="attempt_a",
                                         file_name="../escape.json", content=b"{}")
    (folder / ".agent_runtime_attempt.json").write_text(json.dumps({"attempt_id": "someone_else"}))
    with pytest.raises(workspaces.AttemptWorkspaceConflictError):
        workspaces.write_attempt_archive(workspace_root=root, attempt_id="attempt_a",
                                         file_name="other.json", content=b"{}")


# --- C2 acceptance gap: the selected materials folder is declared to Codex as readable ---------

@pytest.mark.deterministic
def test_codex_profile_declares_the_attempt_materials_as_read_and_writes_only_declared(tmp_path):
    materials, scratch = (tmp_path / "work" / "materials").resolve(), (tmp_path / "work" / "scratch").resolve()
    profile = codex.codex_permission_profile(write_folders=(scratch,), network_policy="provider_managed",
                                             read_folders=(materials,))
    assert profile == {"extends": ":read-only", "filesystem": {str(materials): "read", str(scratch): "write"}}
    argv = codex.build_workspace_command(profile=_execution_profile_for_requirements(_requirements(
        network_policy="provider_managed"), transport_kind="codex_cli", model_id="gpt-6-astra",
        reasoning_profile="low", run_timeout_seconds=600), main_folder=scratch, codex_bin="/bin/codex",
        permission_profile=profile)
    rendered = [value for value in argv if value.startswith("permissions.runtime_attempt.filesystem=")]
    assert rendered == ["permissions.runtime_attempt.filesystem=" + codex._toml(profile["filesystem"])]


@pytest.mark.fake_run
def test_codex_workspace_call_passes_its_materials_folder_as_a_read_entry(tmp_path):
    seen = []

    def invoke(fields):
        seen.append(fields)
        return codex_cases.process()

    executor, request, artifacts, _ = _codex_mcp_env(tmp_path, invoke, tools=("read", "search", "shell"),
                                                     workspace="own_draft_read_write")
    result, trace = _codex_execute(executor, request, artifacts)
    assert result.terminal_status == "completed", trace.get("adapter_failure")
    filesystem = trace["codex_permission_profile"]["filesystem"]
    materials = Path(trace["effective_configuration"]["read_directories"][0])
    assert filesystem[str(materials.resolve())] == "read" and materials.name == "materials"
    assert [path for path, access in filesystem.items() if access == "write"] == [trace["main_folder"]]

# --- Attempt task folders follow the host convention (inherited on Windows, 0o700 on POSIX) -----

def _explicit_access_entries(folder):
    """icacls entries of a folder that are not inherited from its parent (Windows)."""
    lines = subprocess.run(["icacls", str(folder)], capture_output=True, text=True).stdout.splitlines()
    entries = []
    for index, line in enumerate(lines):
        if not line.strip():
            break
        entries.append(line[len(str(folder)):].strip() if index == 0 else line.strip())
    return [entry for entry in entries if "(I)" not in entry], entries


@pytest.mark.fake_run
def test_attempt_task_folders_keep_inherited_host_access_and_host_readback(tmp_path):
    import shutil
    import stat
    import tempfile
    import uuid
    # An ordinary parent (default creation), unlike pytest's owner-only tmp_path.
    base = Path(tempfile.gettempdir()) / f"agent_runtime_task_inherit_{uuid.uuid4().hex[:12]}"
    base.mkdir()
    try:
        executor, request, artifacts, _ = _codex_mcp_env(base, lambda _: codex_cases.process(),
            tools=("read", "search", "shell"), workspace="own_draft_read_write")
        result, trace = _codex_execute(executor, request, artifacts)
        assert result.terminal_status == "completed", trace.get("adapter_failure")
        work = Path(trace["main_folder"]).parent
        for folder in (work, work / "materials", work / "scratch"):
            if WINDOWS:
                explicit, entries = _explicit_access_entries(folder)
                assert explicit == [] and entries, (folder, entries)  # Fully inherited, never owner-only.
                assert any(os.environ["USERNAME"].lower() in entry.lower() for entry in entries)
            else:
                assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        draft = work / "scratch" / "draft.txt"
        draft.write_text("host readback")
        assert draft.read_text() == "host readback"
    finally:
        shutil.rmtree(base, onexc=lambda function, path, _: (os.chmod(path, 0o700), function(path)))
