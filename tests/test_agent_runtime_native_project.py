"""Native project execution: validation, refusals before setup, default argv/settings, native rendering,
CLI forwarding and the example check's evidence rules.

Execution records (trace and effective_configuration) are verified through real runs of the packaged
native_project example (tests/test_agent_runtime_pattern_examples.py, test_real_cli_native_project_example,
gated by AGENT_RUNTIME_REAL_RUN=1); no Provider substitute stands in for them here.
"""
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from agent_runtime import module_requirements_preset, run_local_workflow_test
from agent_runtime.execution.execution_local_invocation import _execution_profile_for_requirements
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.invocation import invocation_codex_module_invocation as codex
from agent_runtime.invocation.invocation_tool_definition import (
    NativeProjectUnsupportedError, validate_native_project,
)
from agent_runtime.testing import conformance_local_test_run as local_test_run
from agent_runtime.testing.execution_local_test_run import build_parser


def _requirements(preset="code_change", **changes):
    options = {"mcp_connections": ("team_data",)} if preset == "mcp_data" else {}
    return replace(module_requirements_preset(preset, **options), **changes)


def _profile(transport, preset="code_change", **changes):
    choice = {} if transport == "claude_cli" else {"model_id": "gpt-6-astra", "reasoning_profile": "xhigh"}
    return _execution_profile_for_requirements(_requirements(preset, **changes), transport_kind=transport, **choice)


def _claude_adapter(tmp_path, *, native_project, mcp=None, permits=()):
    cli = tmp_path / "claude.exe"
    cli.write_bytes(b"")
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return claude.ClaudeAdapter(release_registry=None, artifact_host=None, workspace_root=tmp_path / "attempts",
        cli_path=cli, project_directory=project, mcp_servers=mcp, command_permits=permits,
        native_project=native_project)


def _settings(base, profile, *, native_project, declared=()):
    """The settings the execute path would send, from the same _call_settings it uses."""
    return claude._call_settings(profile=profile, cwd=base / "project", materials=base / "work" / "materials",
        read_dependencies=(), write_rule="./**", cli_temporary=base / "tmp", writable_draft=False,
        native_project=native_project, mcp_declared=bool(declared))


def _render(adapter, profile, *, declared=()):
    base = adapter._cli_path.parent
    settings = _settings(base, profile, native_project=adapter._native_project, declared=declared)
    return adapter.build_command(profile=profile, settings=settings, output_schema={"type": "object"},
        mcp_config_path=Path("mcp.json") if declared else None, declared_mcp=declared, write_rule="./**")


def _allowed(argv):
    return argv[argv.index("--allowedTools") + 1:argv.index("--settings")]


# --- Validation -------------------------------------------------------------------------------

@pytest.mark.deterministic
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
@pytest.mark.parametrize("preset", ["code_change", "mcp_data"])
def test_native_project_accepts_the_existing_project_shapes(transport, preset):
    host = "host" if transport == "codex_cli" else "configured"
    validate_native_project(True, profile=_profile(transport, preset), codex_execution_mode=host)


@pytest.mark.deterministic
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
@pytest.mark.parametrize("preset,changes", [
    ("structured_output", {}),
    ("file_review", {}),
    ("code_change", {"network_policy": "denied"}),
    ("code_change", {"tool_policy": ("read", "search", "shell")}),
    ("code_change", {"tool_policy": ("edit", "read", "search")}),
    ("code_change", {"tool_policy": ("edit", "read", "search", "shell", "lookup_case")}),
])
def test_native_project_refuses_other_shapes_without_downgrade(transport, preset, changes):
    host = "host" if transport == "codex_cli" else "configured"
    try:
        profile = _profile(transport, preset, **changes)
    except ValueError:
        # Codex cannot even form this Profile (no shell, or a callback tool): refused earlier, never downgraded.
        assert transport == "codex_cli"
        return
    with pytest.raises(NativeProjectUnsupportedError) as failure:
        validate_native_project(True, profile=profile, codex_execution_mode=host)
    assert failure.value.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED"


@pytest.mark.deterministic
def test_codex_native_project_requires_the_explicit_host_choice():
    with pytest.raises(NativeProjectUnsupportedError, match="codex_execution_mode"):
        validate_native_project(True, profile=_profile("codex_cli"), codex_execution_mode="configured")


@pytest.mark.deterministic
def test_antigravity_is_refused():
    profile = _execution_profile_for_requirements(module_requirements_preset("file_review"),
        transport_kind="antigravity_cli", model_id="m", reasoning_profile="high")
    with pytest.raises(NativeProjectUnsupportedError):
        validate_native_project(True, profile=profile)


@pytest.mark.deterministic
@pytest.mark.parametrize("value", [None, 1, "true", [], {}])
def test_option_must_be_a_bool_and_is_checked_before_setup(tmp_path, value):
    root = tmp_path / "must_not_create"
    with pytest.raises(ValueError, match="native_project"):
        run_local_workflow_test(root, "missing", input_payload={}, native_project=value)
    assert not root.exists()


@pytest.mark.deterministic
@pytest.mark.parametrize("arguments,message", [
    ({}, "project_directory"),
    ({"project_directory": Path("."), "command_permits": ("python:*",)}, "command_permits"),
    ({"project_directory": Path("."), "tool_session_factory": object()}, "callback"),
])
def test_run_refuses_incompatible_native_input_before_setup(tmp_path, arguments, message):
    root = tmp_path / "must_not_create"
    with pytest.raises(NativeProjectUnsupportedError, match=message):
        run_local_workflow_test(root, "missing", input_payload={}, native_project=True, **arguments)
    assert not root.exists()


@pytest.mark.deterministic
@pytest.mark.parametrize("arguments", [
    {"material_root": Path("materials"), "material_files": ({"relative_path": "a.md", "sha256": "0" * 64,
                                                              "executable": False},)},
    {"commands": ({"command_id": "c", "argv": ["x"], "cwd": "scratch", "timeout_seconds": 5},)},
    {"read_only_dependencies": (Path("dependency"),)},
    {"resources_path": Path("resources.json")},
])
def test_run_refuses_native_resources_before_setup(tmp_path, arguments):
    root = tmp_path / "must_not_create"
    with pytest.raises(NativeProjectUnsupportedError, match="native_project"):
        run_local_workflow_test(root, "missing", input_payload={}, native_project=True,
                                project_directory=tmp_path, **arguments)
    assert not root.exists()


class _SetupReached(Exception):
    pass


def _registered(short_root, preset, monkeypatch):
    """A locally registered example Module and its selection; setup_runtime then raises if it is reached."""
    pytest.importorskip("mcp")
    from agent_runtime.foundation import foundation_environment_setup as foundation
    from agent_runtime.registry.registry_definition_selection import selection_from_registration
    from agent_runtime.registry.registry_plugin_registration import register_module
    from agent_runtime.testing import conformance_pattern_examples as examples
    host = short_root / "host"
    source = examples.write_pattern_source(short_root / "source", preset)
    result = register_module(host, source=source, module_version="v1",
                             execution_requirements=examples.pattern_requirements(preset))
    project = short_root / "project"
    project.mkdir()

    def reached(root):
        raise _SetupReached(str(root))
    monkeypatch.setattr(foundation, "setup_runtime", reached)
    return host, selection_from_registration(result, root=host), project


@pytest.mark.deterministic
def test_native_profile_mismatch_is_refused_before_setup(short_root, monkeypatch):
    host, selection, project = _registered(short_root, "file_review", monkeypatch)
    with pytest.raises(NativeProjectUnsupportedError, match="explicit_project_read_write"):
        run_local_workflow_test(host, selection=selection, input_payload={}, native_project=True,
                                project_directory=project, transport_kind="claude_cli")


@pytest.mark.deterministic
def test_host_default_dependencies_are_refused_before_setup_and_cleared_explicitly(short_root, monkeypatch):
    host, selection, project = _registered(short_root, "code_change", monkeypatch)
    dependency = short_root / "dependency"
    dependency.mkdir()
    (host / ".runtime" / "config.json").write_text(json.dumps({"read_only_dependencies": [str(dependency)]}),
                                                   encoding="utf-8")
    with pytest.raises(NativeProjectUnsupportedError, match="read_only_dependencies"):
        run_local_workflow_test(host, selection=selection, input_payload={}, native_project=True,
                                project_directory=project, transport_kind="claude_cli")
    with pytest.raises(_SetupReached):
        run_local_workflow_test(host, selection=selection, input_payload={}, native_project=True,
                                project_directory=project, transport_kind="claude_cli", read_only_dependencies=())


@pytest.mark.deterministic
@pytest.mark.parametrize("kind", ["missing", "file", "relative", "inside_attempt_root"])
def test_invalid_project_directory_is_refused_before_setup(short_root, monkeypatch, kind):
    host, selection, project = _registered(short_root, "code_change", monkeypatch)
    if kind == "missing":
        project = short_root / "no such project"
    elif kind == "file":
        project = short_root / "project.txt"
        project.write_text("not a folder", encoding="utf-8")
    elif kind == "relative":
        project = Path("project")
    else:
        project = host / ".runtime" / "attempt_workspaces" / "inside"
        project.mkdir(parents=True)
    with pytest.raises((ValueError, OSError)):
        run_local_workflow_test(host, selection=selection, input_payload={}, native_project=True,
                                project_directory=project, transport_kind="claude_cli", read_only_dependencies=())


@pytest.mark.deterministic
def test_default_calls_still_run_setup_first(short_root, monkeypatch):
    host, selection, project = _registered(short_root, "file_review", monkeypatch)
    with pytest.raises(_SetupReached):
        run_local_workflow_test(host, selection=selection, input_payload={}, transport_kind="claude_cli")


@pytest.mark.deterministic
def test_codex_host_without_native_project_keeps_its_exact_shape():
    with pytest.raises(codex.CodexCapabilityUnsupportedError):
        codex.validate_codex_execution_mode("host", profile=_profile("codex_cli", "mcp_data"))
    codex.validate_codex_execution_mode("host", profile=_profile("codex_cli", "mcp_data"), native_project=True)


# --- Claude rendering -------------------------------------------------------------------------

DEV19_DEFAULT_ARGV = {
 "claude:code_change:0": [
  "<BASE>\\claude.exe",
  "-p",
  "--safe-mode",
  "--disable-slash-commands",
  "--strict-mcp-config",
  "--mcp-config",
  "{\"mcpServers\":{}}",
  "--no-chrome",
  "--no-session-persistence",
  "--model",
  "claude-opus-5[1m]",
  "--effort",
  "xhigh",
  "--permission-mode",
  "dontAsk",
  "--output-format",
  "stream-json",
  "--verbose",
  "--tools",
  "Edit,Write,Read,Grep,Glob,Bash",
  "--allowedTools",
  "Read",
  "Grep",
  "Glob",
  "Edit(./**)",
  "--settings",
  "{\"permissions\":{\"blockReadsOutsideWorkingDirectories\":true,\"blockWritesOutsideWorkingDirectories\":true,\"additionalDirectories\":[\"<BASE_JSON>\\\\work\\\\materials\"]}}",
  "--json-schema",
  "{\"type\":\"object\"}",
  "--add-dir",
  "<BASE>\\work\\materials"
 ],
 "claude:code_change:1": [
  "<BASE>\\claude.exe",
  "-p",
  "--safe-mode",
  "--disable-slash-commands",
  "--strict-mcp-config",
  "--mcp-config",
  "{\"mcpServers\":{}}",
  "--no-chrome",
  "--no-session-persistence",
  "--model",
  "claude-opus-5[1m]",
  "--effort",
  "xhigh",
  "--permission-mode",
  "dontAsk",
  "--output-format",
  "stream-json",
  "--verbose",
  "--tools",
  "Edit,Write,Read,Grep,Glob,Bash",
  "--allowedTools",
  "Read",
  "Grep",
  "Glob",
  "Edit(./**)",
  "Bash(python -m pytest:*)",
  "--settings",
  "{\"permissions\":{\"blockReadsOutsideWorkingDirectories\":true,\"blockWritesOutsideWorkingDirectories\":true,\"additionalDirectories\":[\"<BASE_JSON>\\\\work\\\\materials\"]}}",
  "--json-schema",
  "{\"type\":\"object\"}",
  "--add-dir",
  "<BASE>\\work\\materials"
 ],
 "claude:mcp_data:0": [
  "<BASE>\\claude.exe",
  "-p",
  "--disable-slash-commands",
  "--strict-mcp-config",
  "--mcp-config",
  "{\"mcpServers\":{}}",
  "<BASE>\\mcp.json",
  "--no-chrome",
  "--no-session-persistence",
  "--model",
  "claude-opus-5[1m]",
  "--effort",
  "xhigh",
  "--permission-mode",
  "dontAsk",
  "--output-format",
  "stream-json",
  "--verbose",
  "--tools",
  "Edit,Write,Read,Bash",
  "--allowedTools",
  "Read",
  "Edit(./**)",
  "mcp__team_data",
  "--settings",
  "{\"permissions\":{\"blockReadsOutsideWorkingDirectories\":true,\"blockWritesOutsideWorkingDirectories\":true,\"additionalDirectories\":[\"<BASE_JSON>\\\\work\\\\materials\"]}}",
  "--json-schema",
  "{\"type\":\"object\"}",
  "--add-dir",
  "<BASE>\\work\\materials"
 ],
 "codex:code_change:configured": [
  "codex.exe",
  "exec",
  "-m",
  "gpt-6-astra",
  "-c",
  "model_reasoning_effort=\"xhigh\"",
  "-c",
  "project_doc_max_bytes=0",
  "-c",
  "approval_policy=\"never\"",
  "-c",
  "agents.enabled=false",
  "-c",
  "skills.include_instructions=false",
  "-c",
  "features.code_mode.excluded_tool_namespaces=[\"collaboration\",\"clock\"]",
  "-c",
  "mcp_servers={}",
  "-c",
  "features.skip_host_skill_discovery=true",
  "-c",
  "web_search=\"disabled\"",
  "-c",
  "features.shell_tool=true",
  "-c",
  "features.multi_agent=false",
  "-c",
  "features.plugins=false",
  "-c",
  "features.plugin_sharing=false",
  "-c",
  "features.apps=false",
  "-c",
  "features.browser_use=false",
  "-c",
  "features.computer_use=false",
  "-c",
  "features.skill_search=false",
  "-c",
  "features.tool_suggest=false",
  "-c",
  "features.memories=false",
  "-c",
  "features.view_image=false",
  "-c",
  "features.image_generation=false",
  "-c",
  "features.hooks=false",
  "-c",
  "features.workspace_dependencies=false",
  "-c",
  "allow_login_shell=false",
  "-c",
  "default_permissions=\"runtime_attempt\"",
  "-c",
  "permissions.runtime_attempt.extends=\":read-only\"",
  "-c",
  "permissions.runtime_attempt.filesystem={\"<BASE_JSON>\\\\work\\\\materials\"=\"read\", \"<BASE_JSON>\\\\project\"=\"write\"}",
  "-C",
  "<BASE>\\project",
  "--skip-git-repo-check",
  "--strict-config",
  "--ephemeral",
  "--json",
  "-"
 ],
 "codex:code_change:host": [
  "codex.exe",
  "exec",
  "-m",
  "gpt-6-astra",
  "-c",
  "model_reasoning_effort=\"xhigh\"",
  "-c",
  "project_doc_max_bytes=0",
  "-c",
  "approval_policy=\"never\"",
  "-c",
  "agents.enabled=false",
  "-c",
  "skills.include_instructions=false",
  "-c",
  "features.code_mode.excluded_tool_namespaces=[\"collaboration\",\"clock\"]",
  "-c",
  "mcp_servers={}",
  "-c",
  "features.skip_host_skill_discovery=true",
  "-c",
  "web_search=\"disabled\"",
  "-c",
  "features.shell_tool=true",
  "-c",
  "features.multi_agent=false",
  "-c",
  "features.plugins=false",
  "-c",
  "features.plugin_sharing=false",
  "-c",
  "features.apps=false",
  "-c",
  "features.browser_use=false",
  "-c",
  "features.computer_use=false",
  "-c",
  "features.skill_search=false",
  "-c",
  "features.tool_suggest=false",
  "-c",
  "features.memories=false",
  "-c",
  "features.view_image=false",
  "-c",
  "features.image_generation=false",
  "-c",
  "features.hooks=false",
  "-c",
  "features.workspace_dependencies=false",
  "-c",
  "allow_login_shell=false",
  "-C",
  "<BASE>\\project",
  "-s",
  "danger-full-access",
  "--skip-git-repo-check",
  "--strict-config",
  "--ephemeral",
  "--json",
  "-"
 ],
 "codex:mcp_data:configured": [
  "codex.exe",
  "exec",
  "-m",
  "gpt-6-astra",
  "-c",
  "model_reasoning_effort=\"xhigh\"",
  "-c",
  "project_doc_max_bytes=0",
  "-c",
  "approval_policy=\"never\"",
  "-c",
  "agents.enabled=false",
  "-c",
  "skills.include_instructions=false",
  "-c",
  "features.code_mode.excluded_tool_namespaces=[\"collaboration\",\"clock\"]",
  "-c",
  "mcp_servers={}",
  "-c",
  "mcp_servers.team_data={\"command\"=\"server.exe\", \"args\"=[\"--x\"], \"default_tools_approval_mode\"=\"approve\"}",
  "-c",
  "features.skip_host_skill_discovery=true",
  "-c",
  "web_search=\"disabled\"",
  "-c",
  "features.shell_tool=true",
  "-c",
  "features.multi_agent=false",
  "-c",
  "features.plugins=false",
  "-c",
  "features.plugin_sharing=false",
  "-c",
  "features.apps=false",
  "-c",
  "features.browser_use=false",
  "-c",
  "features.computer_use=false",
  "-c",
  "features.skill_search=false",
  "-c",
  "features.tool_suggest=false",
  "-c",
  "features.memories=false",
  "-c",
  "features.view_image=false",
  "-c",
  "features.image_generation=false",
  "-c",
  "features.hooks=false",
  "-c",
  "features.workspace_dependencies=false",
  "-c",
  "allow_login_shell=false",
  "-c",
  "default_permissions=\"runtime_attempt\"",
  "-c",
  "permissions.runtime_attempt.extends=\":read-only\"",
  "-c",
  "permissions.runtime_attempt.filesystem={\"<BASE_JSON>\\\\work\\\\materials\"=\"read\", \"<BASE_JSON>\\\\project\"=\"write\"}",
  "-C",
  "<BASE>\\project",
  "--skip-git-repo-check",
  "--strict-config",
  "--ephemeral",
  "--json",
  "-"
 ]
}


def _baseline(key, base):
    """Golden argv as the installed 0.2.0.dev19 package rendered it, rebased onto this test's folder."""
    base_text = str(base)
    base_json = json.dumps(base_text)[1:-1]
    return [item.replace("<BASE_JSON>", base_json).replace("<BASE>", base_text) for item in DEV19_DEFAULT_ARGV[key]]


def _baseline_fixture(tmp_path):
    base = tmp_path / "fixture"
    (base / "project").mkdir(parents=True)
    (base / "claude.exe").write_bytes(b"")
    return base


@pytest.mark.deterministic
@pytest.mark.skipif(os.name != "nt", reason="the golden argv was rendered on Windows")
@pytest.mark.parametrize("key", ["claude:code_change:0", "claude:code_change:1", "claude:mcp_data:0"])
def test_claude_default_argv_equals_the_dev19_rendering(tmp_path, key):
    _, preset, permits = key.split(":")
    base = _baseline_fixture(tmp_path)
    profile = _execution_profile_for_requirements(_requirements(preset))
    adapter = claude.ClaudeAdapter(release_registry=None, artifact_host=None, workspace_root=base / "attempts",
        cli_path=base / "claude.exe", project_directory=base / "project",
        mcp_servers={"team_data": {"config": {"command": "server.exe", "args": ["--x"]}, "environment_keys": []}},
        command_permits=("python -m pytest:*",) if permits == "1" else ())
    declared = ("team_data",) if preset == "mcp_data" else ()
    # The dev19 rendering used _native_settings alone; _call_settings' MCP exclusions are checked separately.
    settings = claude._native_settings(profile=profile, cwd=base / "project", materials=base / "work" / "materials",
        read_dependencies=(), write_rule="./**", cli_temporary=base / "tmp", draft_layout=False)
    argv = adapter.build_command(profile=profile, settings=settings, output_schema={"type": "object"},
        mcp_config_path=base / "mcp.json" if declared else None, declared_mcp=declared, write_rule="./**")
    assert argv == _baseline(key, base)


@pytest.mark.deterministic
@pytest.mark.skipif(os.name != "nt", reason="the golden argv was rendered on Windows")
@pytest.mark.parametrize("key", ["codex:code_change:configured", "codex:code_change:host", "codex:mcp_data:configured"])
def test_codex_default_argv_equals_the_dev19_rendering(tmp_path, key):
    _, preset, mode = key.split(":")
    base = _baseline_fixture(tmp_path)
    folder = base / "project"
    permission = (None if mode == "host" else codex.codex_permission_profile(write_folders=(folder,),
        network_policy="provider_managed", read_folders=(base / "work" / "materials",)))
    connections = ({"team_data": {"config": {"command": "server.exe", "args": ["--x"],
                                             "default_tools_approval_mode": "approve"}, "environment_keys": []}}
                   if preset == "mcp_data" else None)
    argv = codex.build_workspace_command(profile=_profile("codex_cli", preset), main_folder=folder,
        codex_bin="codex.exe", permission_profile=permission, mcp_connections=connections, codex_execution_mode=mode)
    assert argv == _baseline(key, base)


@pytest.mark.deterministic
def test_call_settings_keep_defaults_and_give_native_calls_only_the_two_switches(tmp_path):
    profile = _profile("claude_cli", "mcp_data")
    default = _settings(tmp_path, profile, native_project=False)
    assert set(default) == {"permissions"} and default["permissions"]["blockReadsOutsideWorkingDirectories"] is True
    with_mcp = _settings(tmp_path, profile, native_project=False, declared=("team_data",))
    assert with_mcp == {**default, "claudeMdExcludes": ["**"], "autoMemoryEnabled": False, "disableAllHooks": True,
                        "enabledPlugins": {}}
    for declared in ((), ("team_data",)):
        assert _settings(tmp_path, profile, native_project=True, declared=declared) == {
            "autoMemoryEnabled": False, "disableAllHooks": True}


@pytest.mark.deterministic
def test_claude_native_rendering_loads_project_context_and_allows_routine_shell(tmp_path):
    argv = _render(_claude_adapter(tmp_path, native_project=True), _profile("claude_cli"))
    assert "--safe-mode" not in argv and "--disable-slash-commands" not in argv
    assert "--no-session-persistence" in argv and "--strict-mcp-config" in argv
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"
    assert not any("bypass" in value.lower() or "dangerously" in value.lower() for value in argv)
    tools = argv[argv.index("--tools") + 1].split(",")
    assert {"Read", "Grep", "Glob", "Edit", "Write", "Bash", "PowerShell", "Skill"} == set(tools)
    assert set(_allowed(argv)) == {"Read", "Grep", "Glob", "Edit(./**)", "Bash", "PowerShell", "Skill"}
    assert json.loads(argv[argv.index("--settings") + 1]) == {"autoMemoryEnabled": False, "disableAllHooks": True}
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}} and "--add-dir" not in argv


@pytest.mark.deterministic
def test_claude_native_rendering_passes_only_declared_mcp_with_its_native_surfaces(tmp_path):
    mcp = {"team_data": {"config": {"type": "http", "url": "https://mcp.example.invalid/mcp"}, "environment_keys": []}}
    argv = _render(_claude_adapter(tmp_path, native_project=True, mcp=mcp), _profile("claude_cli", "mcp_data"),
                   declared=("team_data",))
    tools = argv[argv.index("--tools") + 1].split(",")
    assert {"ListMcpResourcesTool", "ReadMcpResourceTool", "ToolSearch", "Skill"} <= set(tools)
    assert "mcp__team_data" in _allowed(argv) and "--strict-mcp-config" in argv
    assert argv[argv.index("--mcp-config") + 2] == "mcp.json"


@pytest.mark.deterministic
def test_claude_native_adapter_refuses_permits_and_extra_dependencies(tmp_path):
    with pytest.raises(ValueError, match="native_project"):
        _claude_adapter(tmp_path, native_project=True, permits=("python:*",))
    with pytest.raises(ValueError, match="native_project"):
        claude.ClaudeAdapter(release_registry=None, artifact_host=None, workspace_root=tmp_path / "w",
            cli_path=_claude_adapter(tmp_path, native_project=False)._cli_path, read_only_dependencies=(tmp_path,),
            native_project=True)
    with pytest.raises(ValueError, match="native_project"):
        _claude_adapter(tmp_path, native_project="yes")


# --- Codex rendering --------------------------------------------------------------------------

_CONTEXT_OFF = ("project_doc_max_bytes=0", "skills.include_instructions=false",
                "features.skip_host_skill_discovery=true")


@pytest.mark.deterministic
def test_codex_native_rendering_enables_agents_md_and_skills_under_host_permissions():
    connections = {"team_data": {"config": {"url": "https://mcp.example.invalid/mcp",
                                            "default_tools_approval_mode": "approve"}, "environment_keys": []}}
    host = codex.build_workspace_command(profile=_profile("codex_cli"), main_folder=Path("/project"),
        codex_bin="/codex", permission_profile=None, codex_execution_mode="host")
    native = codex.build_workspace_command(profile=_profile("codex_cli", "mcp_data"), main_folder=Path("/project"),
        codex_bin="/codex", permission_profile=None, codex_execution_mode="host", native_project=True,
        mcp_connections=connections)
    assert all(option in host for option in _CONTEXT_OFF)
    assert not any(option in native for option in _CONTEXT_OFF)
    assert native[native.index("-s") + 1] == "danger-full-access" and 'approval_policy="never"' in native
    assert "agents.enabled=false" in native and 'web_search="disabled"' in native
    assert "features.memories=false" in native and "features.hooks=false" in native
    assert any(value.startswith("mcp_servers.team_data=") for value in native)
    assert not any("dangerously" in value for value in native)


@pytest.mark.deterministic
def test_codex_default_and_host_rendering_are_unchanged():
    permission = codex.codex_permission_profile(write_folders=(Path("/project"),), network_policy="provider_managed")
    configured = dict(profile=_profile("codex_cli"), main_folder=Path("/project"), codex_bin="/codex",
                      permission_profile=permission)
    assert codex.build_workspace_command(**configured) == codex.build_workspace_command(**configured,
                                                                                         native_project=False)
    host = dict(configured, permission_profile=None, codex_execution_mode="host")
    assert codex.build_workspace_command(**host) == codex.build_workspace_command(**host, native_project=False)
    with pytest.raises(NativeProjectUnsupportedError):
        codex.build_workspace_command(**configured, native_project=True)


# --- CLI forwarding ---------------------------------------------------------------------------

@pytest.mark.deterministic
def test_cli_flag_defaults_off_and_forwards_true(tmp_path, monkeypatch):
    parser = build_parser()
    args = ["--root", str(tmp_path), "--workflow", "w", "--input", str(tmp_path / "input.json")]
    assert parser.parse_args(args).native_project is False
    assert parser.parse_args([*args, "--native-project"]).native_project is True
    (tmp_path / "input.json").write_text("{}", encoding="utf-8")
    seen = {}

    def capture(*positional, **keywords):
        seen.update(keywords)
        return {"status": "completed"}
    monkeypatch.setattr(local_test_run, "run_local_workflow_test", capture)
    assert local_test_run.main([*args, "--native-project", "--project-directory", str(tmp_path)]) == 0
    assert seen["native_project"] is True
    seen.clear()
    assert local_test_run.main(args) == 0
    assert seen["native_project"] is False


@pytest.mark.deterministic
def test_packaged_examples_refuse_the_per_call_option(tmp_path):
    with pytest.raises(SystemExit):
        local_test_run.main(["--root", str(tmp_path), "--example", "agent_capability_example", "--native-project"])


# --- Runnable example -------------------------------------------------------------------------

@pytest.fixture
def short_root():
    """A short root with a space: pytest's tmp_path is too deep for Windows' 260-character paths."""
    import shutil
    import tempfile
    import uuid
    root = Path(tempfile.gettempdir()) / f"ar np {uuid.uuid4().hex[:6]}"
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.mark.deterministic
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
def test_native_project_example_prepares_a_project_without_putting_its_facts_in_the_task(short_root, transport):
    pytest.importorskip("mcp")
    from agent_runtime.testing import conformance_pattern_examples as examples
    manifest = examples.prepare_pattern_example(short_root, examples.NATIVE_PROJECT_PATTERN, transport,
                                                model_id="m", reasoning_profile="high")
    project = Path(manifest["project_directory"])
    for path in ("CLAUDE.md", "AGENTS.md", ".claude/skills/stock-report/SKILL.md", ".claude/skills/meeting-notes/SKILL.md",
                 ".agents/skills/stock-report/SKILL.md", ".agents/skills/meeting-notes/SKILL.md",
                 "scripts/summarize_stock.py"):
        assert (project / path).is_file(), path
    marker = manifest["expected"]["marker"]
    assert marker in (project / "CLAUDE.md").read_text(encoding="utf-8")
    task = (Path(manifest["input_path"])).read_text(encoding="utf-8")
    assert marker not in task and "stock-report" not in task and "SKILL" not in task
    argv = manifest["test_run_argv"]
    assert "--native-project" in argv
    assert ("--codex-execution-mode" in argv) is (transport == "codex_cli")
    assert manifest["registered"]["module_release_ref"] == "runtime-module:example_native_project@v1"
    with pytest.raises(ValueError):
        examples.prepare_pattern_example(short_root / "other", examples.NATIVE_PROJECT_PATTERN, "antigravity_cli")


def _native_run(short_root, transport):
    """A prepared example folder plus the files and log a passing run leaves; tests remove one piece at a time."""
    pytest.importorskip("mcp")
    from agent_runtime.testing import conformance_pattern_examples as examples
    manifest = examples.prepare_pattern_example(short_root, examples.NATIVE_PROJECT_PATTERN, transport,
                                                model_id="m", reasoning_profile="high")
    expected, project = manifest["expected"], Path(manifest["project_directory"])
    result = {"response_id": "resp-1", "warehouse": expected["warehouse"], "request_tag": expected["request_tag"],
              "items": [{"sku": "A", "units": 3}, {"sku": "B", "units": 4}], "source": "fixture"}
    audit = {"event": "tools/call", "tool": examples.MCP_TOOL, "result": result,
             "arguments": {"warehouse": expected["warehouse"], "request_tag": expected["request_tag"]}}
    Path(expected["audit_path"]).write_text(json.dumps(audit) + "\n", encoding="utf-8")
    (project / "data").mkdir()
    (project / "data" / "snapshot.json").write_text(json.dumps(result), encoding="utf-8")
    summary = {"response_id": "resp-1", "warehouse": expected["warehouse"], "total_units": 7, "sku_count": 2,
               "python_executable": expected["interpreter"], "script_nonce": expected["script_nonce"]}
    (project / "reports").mkdir()
    (project / "reports" / "stock_summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (project / "reports" / "stock_report.md").write_text(f"Project-Marker: {expected['marker']}\n", encoding="utf-8")
    shell = "Bash" if transport == "claude_cli" else "command_execution"
    if transport == "claude_cli":
        skill = {"tool_name": "Skill", "status": "completed", "request": {"skill": "stock-report"}, "response": {}}
    else:
        skill = {"tool_name": shell, "status": "completed",
                 "request": {"command": "Get-Content .agents/skills/stock-report/SKILL.md"},
                 "response": {"exit_code": 0, "output": "# Stock report"}}
    command = f"{expected['interpreter']} scripts/summarize_stock.py data/snapshot.json reports/stock_summary.json"
    script = {"tool_name": shell, "status": "completed", "request": {"command": command},
              "response": {"exit_code": 0, "output": json.dumps(summary)}}
    enabled = ["claude_md", "skills"] if transport == "claude_cli" else ["agents_md", "skills"]
    configuration = {"native_project": True, "context_enabled": enabled, "command_scope": "host",
                     "filesystem_scope": "task_directed", "context_suppressed": ["auto_memory", "hooks"],
                     "allowed_rules": ["Skill", "Bash", "PowerShell"]}
    init = json.dumps({"type": "system", "subtype": "init", "skills": ["stock-report", "meeting-notes"]})
    record = {"input_payload": {"request": "stock report"}, "execution_log": {"attempts": [{
        "provider_log": {"effective_configuration": configuration, "stdout": init, "actual_prompt": "task"},
        "tool_calls": [skill, script]}]}}
    output = {"report_file": "reports/stock_report.md", "response_id": "resp-1", "total_units": 7, "sku_count": 2}
    return examples, manifest, record, output


def _native_checks(examples, manifest, record, output):
    checks = examples._Checks()
    calls = record["execution_log"]["attempts"][0]["tool_calls"]
    examples._check_native_project(checks, manifest, record, calls, output)
    return {row["check"]: row["passed"] for row in checks.rows}


@pytest.mark.deterministic
@pytest.mark.parametrize("transport", ["claude_cli", "codex_cli"])
def test_native_example_check_passes_only_with_the_required_evidence(short_root, transport):
    examples, manifest, record, output = _native_run(short_root, transport)
    assert all(_native_checks(examples, manifest, record, output).values())
    calls = record["execution_log"]["attempts"][0]["tool_calls"]
    skill, script = list(calls)
    if transport == "claude_cli":
        # Reading SKILL.md is not a Skill tool use for Claude.
        calls[0] = {"tool_name": "Read", "status": "completed",
                    "request": {"file_path": ".claude/skills/stock-report/SKILL.md"}, "response": {}}
    else:
        calls[0] = {**skill, "request": {"command": "Get-ChildItem .agents/skills"}}
        assert _native_checks(examples, manifest, record, output)["relevant_skill_used"] is False
        # Naming SKILL.md is not a read unless the step's own output shows the skill body.
        calls[0] = {**skill, "status": "failed", "response": {"exit_code": 1, "output": "Cannot find path"}}
        assert _native_checks(examples, manifest, record, output)["relevant_skill_used"] is False
        # A read followed by a failing command on the same Shell line still read the file.
        calls[0] = {**skill, "status": "failed", "response": {"exit_code": 1, "output": "# Stock report\nrg: not found"}}
        assert _native_checks(examples, manifest, record, output)["relevant_skill_used"] is True
        calls[0] = {**skill, "request": {"command": "Get-ChildItem .agents/skills"}}
    assert _native_checks(examples, manifest, record, output)["relevant_skill_used"] is False
    calls[0] = skill
    # The artifact alone does not prove the project script ran: the log must show the run and its printed JSON.
    nonce = manifest["expected"]["script_nonce"]
    calls[1] = {**script, "request": {"command": "cat scripts/summarize_stock.py"},
                "response": {"exit_code": 0, "output": f'SCRIPT_NONCE = "{nonce}"'}}
    assert _native_checks(examples, manifest, record, output)["project_python_script_ran"] is False
    calls[1] = {**script, "status": "failed"}
    assert _native_checks(examples, manifest, record, output)["project_python_script_ran"] is False
    calls[1] = script
    assert all(_native_checks(examples, manifest, record, output).values())

