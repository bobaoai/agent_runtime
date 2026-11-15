"""Codex workspace v4: agent Modules (Shell-capable or MCP-only) executed by Codex.

Deterministic tests check binding, capability checks, the generated
permission profile, command rendering, layout text and log correlation.
Real-run tests use the installed Codex CLI: its own sandbox checks the
boundary each Attempt receives, and Test Run executes a registered Reviewer.
"""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import uuid

import pytest

from agent_runtime import register_reviewer, run_local_workflow_test
from agent_runtime.execution.execution_local_invocation import _execution_profile_for_requirements
from agent_runtime.inspection.inspection_execution_logging import parse_cli_log
from agent_runtime.invocation import invocation_codex_module_invocation as codex
from agent_runtime.invocation.invocation_local_command_execution import _SYSTEM_READ_ROOTS
from agent_runtime.invocation.invocation_local_resource_preparation import (
    LocalResourceError, capture_local_resources, describe_local_resources,
)
from test_agent_runtime_module_authoring import _requirements, SKILL_ID
from test_agent_runtime_reviewer_registration_cli import _source, MODULE_ID


REAL_GATE = pytest.mark.skipif(os.environ.get("AGENT_RUNTIME_REAL_RUN") != "1",
                               reason="unverified: set AGENT_RUNTIME_REAL_RUN=1 with an authenticated Codex CLI")
REAL_MODEL, REAL_EFFORT = "gpt-6-astra", "low"
# Tool-free v5 argv: host login and configuration load normally (no ignore flags, no auth store override).
V5_ARGV = ["/bin/codex", "exec", "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="xhigh"',
    "-c", "project_doc_max_bytes=0", "-c", 'approval_policy="never"',
    "-c", "agents.enabled=false", "-c", "skills.include_instructions=false",
    "-c", 'features.code_mode.excluded_tool_namespaces=["functions","collaboration","clock"]',
    "-c", "mcp_servers={}", "-c", "features.skip_host_skill_discovery=true", "-c", 'web_search="disabled"',
    *[part for name in ("shell_tool", "multi_agent", "plugins", "plugin_sharing", "apps", "browser_use", "computer_use",
                        "skill_search", "tool_suggest", "memories", "view_image", "image_generation", "hooks",
                        "workspace_dependencies") for part in ("-c", f"features.{name}=false")],
    "-C", str(Path("/attempt/cwd")), "-s", "read-only", "--skip-git-repo-check",
    "--strict-config", "--ephemeral", "--json", "--output-schema", str(Path("/schema.json")), "-"]


def _codex_profile(*, run_timeout_seconds=1200, **changes):
    return _execution_profile_for_requirements(_requirements(**changes), transport_kind="codex_cli",
                                               model_id=REAL_MODEL, reasoning_profile="xhigh", run_timeout_seconds=run_timeout_seconds)


def _option(argv, prefix):
    values = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-c" and argv[index + 1].startswith(prefix)]
    assert len(values) == 1, (prefix, values)
    return values[0]


@pytest.mark.deterministic
def test_codex_binding_follows_the_module_execution_mode():
    agent = _codex_profile()
    assert (agent.executor_adapter_id, agent.executor_adapter_revision) == ("codex_cli_agent_workspace_executor", "v4")
    assert agent.tool_policy == ("read", "search", "shell")
    tool_free = _codex_profile(execution_mode="tool_free", tool_policy=(), attempt_workspace_policy="none",
                               run_timeout_seconds=30, max_attempts=1)
    assert (tool_free.executor_adapter_id, tool_free.executor_adapter_revision) == ("codex_cli_agent_executor", "v5")
    assert codex.build_command(profile=tool_free, workspace=Path("/attempt/cwd"), codex_bin="/bin/codex",
                               schema_path=Path("/schema.json")) == V5_ARGV


@pytest.mark.deterministic
@pytest.mark.parametrize("tools", [("read",), ("search",), ("read", "search"), ("edit",), ("edit", "mcp__data")])
def test_codex_refuses_read_search_or_edit_without_shell_as_unsupported(tools):
    with pytest.raises(codex.CodexCapabilityUnsupportedError, match="through its shell tool") as refused:
        _codex_profile(tool_policy=tools)
    assert refused.value.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED"


@pytest.mark.deterministic
@pytest.mark.parametrize("changes", [
    dict(attempt_workspace_policy="none"),
    dict(network_policy="direct_sandboxed"),
    dict(semantic_input_delivery_mode="gateway_read", network_policy="gateway_only", gateway_access_reasons=("semantic_input",)),
])
def test_codex_refuses_shell_without_folder_open_network_or_gateway(changes):
    with pytest.raises(ValueError):
        _codex_profile(**changes)


@pytest.mark.deterministic
def test_mcp_only_codex_reaches_the_public_preparation_with_shell_off():
    profile = _codex_profile(tool_policy=("mcp__fixture",), attempt_workspace_policy="none",
                             network_policy="provider_managed")
    assert (profile.executor_adapter_id, profile.executor_adapter_revision) == ("codex_cli_agent_workspace_executor", "v4")
    expectation = codex.CodexCliAgentWorkspaceModuleExecutor.execution_expectation(profile)
    assert expectation.network_policy == "provider_managed" and expectation.attempt_workspace_policy == "none"
    connections = codex.validate_codex_mcp_connections({"fixture": {
        "config": {"command": "python", "args": ["server.py"], "env_vars": ["FIXTURE_TOKEN"]},
        "environment_keys": ["FIXTURE_TOKEN"]}})
    argv = codex.build_workspace_command(profile=profile, main_folder=Path("/w/work"), codex_bin="/bin/codex",
                                         permission_profile=None, mcp_connections=connections)
    assert _option(argv, "features.shell_tool=") == "features.shell_tool=false"
    assert _option(argv, "features.code_mode.excluded_tool_namespaces=").endswith('["functions","collaboration","clock"]')
    assert argv[argv.index("-s") + 1] == "read-only" and not any("default_permissions" in value for value in argv)
    assert "mcp_servers={}" in argv and _option(argv, "mcp_servers.fixture=").startswith("mcp_servers.fixture={")
    assert not any("allow_login_shell" in value for value in argv)
    draft = _codex_profile(tool_policy=("mcp__fixture",), network_policy="provider_managed")
    permission = codex.codex_permission_profile(write_folders=(Path("/w/scratch"),), network_policy="provider_managed")
    argv = codex.build_workspace_command(profile=draft, main_folder=Path("/w/scratch"), codex_bin="/bin/codex",
                                         permission_profile=permission, mcp_connections=connections)
    assert "-s" not in argv and _option(argv, "default_permissions=") == 'default_permissions="runtime_attempt"'
    executor_descriptor = codex.CodexCliAgentWorkspaceModuleExecutor(
        release_registry=None, artifact_host=type("H", (), {"read_bytes": lambda *a: b"", "commit_failure_detail": lambda *a: None,
                                                            "commit_attempt_trace": lambda *a: None})(),
        workspace_root=Path("/w")).descriptor
    assert set(executor_descriptor.supported_network_policies) == {"denied", "provider_managed"}


@pytest.mark.deterministic
@pytest.mark.parametrize("field", ["env", "http_headers", "bearer_token", "unknown_field"])
def test_codex_mcp_config_refuses_literal_credentials_and_unknown_fields(field):
    with pytest.raises(ValueError, match=field):
        codex.validate_codex_mcp_connections({"data": {"config": {"command": "server", field: {"X": "literal"}},
                                                       "environment_keys": []}})


@pytest.mark.deterministic
def test_codex_mcp_config_requires_every_referenced_variable_in_environment_keys():
    with pytest.raises(ValueError, match="TOKEN"):
        codex.validate_codex_mcp_connections({"data": {"config": {"url": "https://example.invalid/mcp",
            "bearer_token_env_var": "TOKEN"}, "environment_keys": []}})
    accepted = codex.validate_codex_mcp_connections({"data": {"config": {"url": "https://example.invalid/mcp",
        "bearer_token_env_var": "TOKEN", "env_http_headers": {"X-Key": "KEY"}}, "environment_keys": ["TOKEN", "KEY"]}})
    rendered = codex.render_codex_mcp_servers(accepted)
    assert rendered[0] == "mcp_servers={}" and "TOKEN" in rendered[1] and "KEY" in rendered[1]
    recorded = codex.redact_codex_argv(["-c", rendered[1]])
    assert recorded[1].startswith("mcp_servers.data=<host MCP config sha256:") and "example.invalid" not in recorded[1]


@pytest.mark.deterministic
def test_permission_profile_grants_only_the_declared_write_folders(tmp_path):
    main, temporary = (tmp_path / "project").resolve(), (tmp_path / "attempt" / "tmp").resolve()
    denied = codex.codex_permission_profile(write_folders=(main, temporary), network_policy="denied")
    assert denied == {"extends": ":read-only", "filesystem": {str(main): "write", str(temporary): "write"},
                      "network": {"enabled": False}}
    managed = codex.codex_permission_profile(write_folders=(main,), network_policy="provider_managed")
    assert managed == {"extends": ":read-only", "filesystem": {str(main): "write"}}


@pytest.mark.deterministic
@pytest.mark.parametrize("grant", ["root", "inside_workspace", "workspace_parent", "workspace"])
def test_dependency_grants_stay_separate_from_runtime_folders(tmp_path, grant):
    workspace = (tmp_path / "workspace").resolve()
    value = {"root": Path(Path.cwd().anchor), "inside_workspace": workspace / "other",
             "workspace_parent": workspace.parent, "workspace": workspace}[grant]
    with pytest.raises(PermissionError):
        codex.check_codex_external_grants((value,), workspace_root=workspace)
    codex.check_codex_external_grants((tmp_path / "dependency", tmp_path / "workspace_sibling"), workspace_root=workspace)


@pytest.mark.deterministic
def test_command_sandbox_containment_refuses_protected_locations_under_its_read_roots():
    roots = tuple(map(Path, _SYSTEM_READ_ROOTS))
    exposed = codex.command_sandbox_conflicts(
        protected=(Path("/Library/Application Support/codex-login"), Path("/usr/local/var/tmp")),
        command_readable_roots=roots)
    assert exposed == (Path("/Library/Application Support/codex-login"), Path("/usr/local/var/tmp"))
    default_layout = (Path(tempfile.gettempdir()).resolve(), *codex.codex_home_locations({}))
    assert codex.command_sandbox_conflicts(protected=default_layout, command_readable_roots=roots) == ()


@pytest.mark.deterministic
def test_workspace_command_turns_on_only_the_shell_and_carries_the_permission_profile():
    profile = _codex_profile()
    permission = {"extends": ":read-only", "filesystem": {"/w/scratch": "write"}, "network": {"enabled": False}}
    argv = codex.build_workspace_command(profile=profile, main_folder=Path("/w/scratch"), codex_bin="/bin/codex",
                                         permission_profile=permission)
    assert "-s" not in argv and argv[argv.index("-C") + 1] == str(Path("/w/scratch"))
    assert _option(argv, "features.shell_tool=") == "features.shell_tool=true"
    assert _option(argv, "features.code_mode.excluded_tool_namespaces=").endswith('["collaboration","clock"]')
    assert _option(argv, "mcp_servers") == "mcp_servers={}"
    assert _option(argv, "allow_login_shell=") == "allow_login_shell=false"
    assert _option(argv, "default_permissions=") == 'default_permissions="runtime_attempt"'
    assert _option(argv, "permissions.runtime_attempt.filesystem=") == (
        'permissions.runtime_attempt.filesystem={"/w/scratch"="write"}')
    assert _option(argv, "permissions.runtime_attempt.network.enabled=").endswith("=false")
    assert all(_option(argv, f"features.{name}=").endswith("=false") for name in codex._FEATURES_OFF)
    assert "--ignore-user-config" not in argv and "--ignore-rules" not in argv
    with pytest.raises(ValueError):
        codex.build_workspace_command(profile=_codex_profile(execution_mode="tool_free", tool_policy=(),
            attempt_workspace_policy="none"), main_folder=Path("/w"), codex_bin="/bin/codex", permission_profile=permission)


@pytest.mark.deterministic
@pytest.mark.parametrize("path", ["/private/var/tmp/dependency-\U0001F600", "/tmp/中文/路径",
                                  '/tmp/quote"and\\backslash', "/tmp/tab\tnewline\nnul\x00del\x7f",
                                  "/tmp/\U0010FFFF"])
def test_workspace_command_encodes_every_valid_path_losslessly(path):
    profile = _codex_profile()
    permission = {"extends": ":read-only", "filesystem": {path: "write", "/w/scratch": "write"},
                  "network": {"enabled": False}}
    argv = codex.build_workspace_command(profile=profile, main_folder=Path("/w/scratch"), codex_bin="/bin/codex",
                                         permission_profile=permission)
    rendered = _option(argv, "permissions.runtime_attempt.filesystem=").split("=", 1)[1]
    assert tomllib.loads("filesystem = " + rendered)["filesystem"] == permission["filesystem"]
    assert "\\ud8" not in rendered.lower() and "\\udd" not in rendered.lower()
    assert tomllib.loads("value = " + codex._toml([path]))["value"] == [path]


@pytest.mark.deterministic
def test_workspace_command_refuses_a_path_that_is_not_valid_unicode():
    permission = {"extends": ":read-only", "filesystem": {"/tmp/undecodable-\udcff": "write", "/w/scratch": "write"},
                  "network": {"enabled": False}}
    with pytest.raises(ValueError, match="not valid Unicode"):
        codex.build_workspace_command(profile=_codex_profile(), main_folder=Path("/w/scratch"),
                                      codex_bin="/bin/codex", permission_profile=permission)


@pytest.mark.deterministic
def test_codex_layout_text_states_the_main_folder_with_relative_paths_only(tmp_path):
    profile = _codex_profile()
    source = tmp_path / "materials"
    source.mkdir()
    (source / "note.txt").write_bytes(b"note\n")
    body = capture_local_resources(profile=profile, material_root=source, material_files=(
        {"relative_path": "note.txt", "sha256": hashlib.sha256(b"note\n").hexdigest(), "executable": False},),
        read_only_dependencies=(), commands=())
    text = describe_local_resources(profile=profile, body=body)
    assert "current working directory is your writable scratch area" in text
    assert "TMPDIR" in text and "../materials/task_input" in text and "../materials/source" in text
    assert "Other locations follow the CLI's own permissions" in text
    assert str(tmp_path) not in text
    assert describe_local_resources(profile=profile, body=None).count("writable scratch area") == 1


def _mcp_events(identity, command_id, returned, *, status="completed"):
    text = json.dumps(returned)
    item = {"id": identity, "type": "mcp_tool_call", "server": "runtime_commands", "tool": "sandbox_command_execute",
            "arguments": {"command_id": command_id}, "result": None, "error": None, "status": "in_progress"}
    completed = {**item, "status": status,
                 "result": {"content": [{"type": "text", "text": text}], "structured_content": returned}}
    return [{"type": "item.started", "item": item}, {"type": "item.completed", "item": completed}]


def _command_record(local_id, command_id, returncode=0):
    response = {"local_call_id": local_id, "command_id": command_id, "argv": ["/bin/cat", "note.txt"],
                "cwd": "/attempt/work/materials/source", "allowed": True, "returncode": returncode,
                "stdout": "note\n", "stderr": "", "process_output_complete": True, "failure": None,
                "byte_capture_exact": True}
    return {"tool_call_id": local_id, "tool_name": "sandbox_command_execute", "source_kind": "runtime_local",
            "status": "completed" if returncode == 0 else "failed", "request": {"command_id": command_id},
            "response": response}


def _codex_trace(events, records):
    stdout = "\n".join(json.dumps(event) for event in [
        {"type": "thread.started", "thread_id": "t"}, {"type": "turn.started"}, *events,
        {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]) + "\n"
    return {"transport": "codex_cli", "byte_capture_exact": True, "process_output_complete": True, "stdout": stdout,
            "local_command_calls": records, "local_command_cli_tool_name": "sandbox_command_execute"}


@pytest.mark.deterministic
def test_inspection_pairs_codex_command_results_with_runtime_records():
    record = _command_record("local_command_1", "check")
    view = parse_cli_log(_codex_trace(_mcp_events("item_4", "check", record["response"]), [record]))
    assert view["complete"] is True and view["issues"] == []
    (paired,) = view["tool_calls"]
    assert (paired["source_kind"], paired["provider_tool_call_id"], paired["status"]) == ("runtime_local", "item_4", "completed")
    assert len(view["provider_tool_calls"]) == 1


@pytest.mark.deterministic
@pytest.mark.parametrize("case,issue", [("missing_parent", "local_command_parent_record_missing"),
                                        ("conflict", "local_command_correlation_conflict"),
                                        ("ambiguous", "local_command_correlation_ambiguous")])
def test_inspection_marks_codex_correlation_gaps_incomplete(case, issue):
    record = _command_record("local_command_1", "check")
    events = _mcp_events("item_4", "check", record["response"])
    records = [record]
    if case == "missing_parent":
        records = []
    elif case == "conflict":
        records = [{**record, "response": {**record["response"], "stdout": "different\n"}}]
    elif case == "ambiguous":
        events += _mcp_events("item_5", "check", record["response"])
    view = parse_cli_log(_codex_trace(events, records))
    assert view["complete"] is False
    assert any(item.startswith(issue) for item in view["issues"]), view["issues"]


def _register(tmp_path, prompt):
    source, registration = _source(tmp_path / "source")
    (registration.parent / "prompt.md").write_text(prompt)
    root = tmp_path / "host"
    register_reviewer(root, source_root=source, skill_id=SKILL_ID, module_id=MODULE_ID, module_version="v1")
    return root


def _resources(tmp_path, commands=(), dependencies=()):
    materials = tmp_path / "materials"
    materials.mkdir(exist_ok=True)
    (materials / "note.txt").write_text("probe note\n")
    path = tmp_path / "resources.json"
    path.write_text(json.dumps({"material_root": "materials", "material_files": [
        {"relative_path": "note.txt", "sha256": hashlib.sha256(b"probe note\n").hexdigest(), "executable": False}],
        "read_only_dependencies": [str(item) for item in dependencies], "commands": list(commands)}))
    return path


_PASSING = ("Then return verdict 'passed', one check_result with check_id 'steps', disposition 'passed', a one-sentence "
            "assessment and finding_ids [], findings [] and safe_next_step 'none'.\n")


@pytest.mark.deterministic
def test_real_test_run_refuses_the_filesystem_root_as_a_dependency(tmp_path, monkeypatch):
    """Prelaunch validation through the public Test Run entry; no Provider, CLI or process is involved.

    The placeholder executable only satisfies argument resolution; any process creation fails the test.
    """
    from agent_runtime.invocation.invocation_host_platform_selection import HOST_PLATFORM
    monkeypatch.setattr(HOST_PLATFORM, "create_cli_process",
                        lambda *args, **kwargs: pytest.fail("refused resources must not start any process"))
    root = _register(tmp_path, "Capability refusal test.\n" + _PASSING)
    filesystem_root = Path(Path.cwd().anchor)
    with pytest.raises(LocalResourceError, match="filesystem root"):
        run_local_workflow_test(root, MODULE_ID, input_payload={}, transport_kind="codex_cli", model_id=REAL_MODEL,
            reasoning_profile=REAL_EFFORT, cli_path=Path(sys.executable),
            resources_path=_resources(tmp_path, dependencies=(filesystem_root,)))


_COMMANDS = (
    {"command_id": "read_note", "argv": ["/bin/cat", "note.txt"], "cwd": "source", "timeout_seconds": 60},
    {"command_id": "fails", "argv": ["/bin/sh", "-c", "echo boom >&2; exit 3"], "cwd": "scratch", "timeout_seconds": 60},
)


@pytest.mark.real_run
@REAL_GATE
def test_real_codex_reviewer_uses_shell_draft_and_declared_commands_in_one_log(tmp_path):
    """Real entry: register_reviewer, Test Run with codex_cli and gpt-6-astra, installed Codex CLI and MCP proxy."""
    root = _register(tmp_path, "This is a Runtime capability test. Do these steps in order, each once:\n"
        "1. Use the shell to run: cat ../materials/source/note.txt\n"
        "2. Use the shell to write draft.txt in the current directory containing 'drafted', then cat it.\n"
        "3. Call the MCP tool sandbox_command_execute with command_id 'read_note'.\n"
        "4. Call the MCP tool sandbox_command_execute with command_id 'fails'. A nonzero exit is expected.\n"
        + _PASSING)
    record = run_local_workflow_test(root, MODULE_ID, input_payload={}, transport_kind="codex_cli",
        model_id=REAL_MODEL, reasoning_profile=REAL_EFFORT, resources_path=_resources(tmp_path, commands=_COMMANDS))
    assert record["status"] == "completed", record["failure_detail"]
    trace = record["provider_trace"]
    argv = trace["argv"]
    assert "-s" not in argv and trace["main_folder"] == trace["cwd"] == argv[argv.index("-C") + 1]
    assert [path for path, access in trace["codex_permission_profile"]["filesystem"].items()
            if access == "write"] == [trace["main_folder"]]
    assert "isolation_gaps" not in trace and "--ignore-rules" not in argv
    assert _option(argv, "mcp_servers.runtime_commands=").count("sandbox_command_execute") == 1
    (attempt,) = record["execution_log"]["attempts"]
    assert attempt["complete"] is True and attempt["issues"] == []
    shell = [call for call in attempt["tool_calls"] if call["tool_name"] == "command_execution"]
    assert any("materials/source/note.txt" in call["request"]["command"] and call["status"] == "completed" for call in shell)
    assert any("draft.txt" in call["request"]["command"] and call["status"] == "completed" for call in shell)
    local = {call["request"]["command_id"]: call for call in attempt["tool_calls"] if call.get("source_kind") == "runtime_local"}
    assert set(local) == {"read_note", "fails"}
    assert all(call["provider_tool_call_id"] and call["tool_name"] == "sandbox_command_execute" for call in local.values())
    assert (local["read_note"]["status"], local["read_note"]["response"]["stdout"]) == ("completed", "probe note\n")
    assert (local["fails"]["status"], local["fails"]["response"]["returncode"]) == ("failed", 3)


@pytest.mark.real_run
@REAL_GATE
def test_real_resource_close_during_a_command_fails_the_attempt_and_keeps_its_records(tmp_path):
    """Real entry: Test Run with the installed Codex CLI; the public resource_cancel_requested closes resources mid-command."""
    root = _register(tmp_path, "Call the MCP tool sandbox_command_execute with command_id 'wait' once and wait "
                     "for its result.\n" + _PASSING)
    commands = ({"command_id": "wait", "argv": ["/bin/sh", "-c", "touch started; sleep 120"], "cwd": "scratch",
                 "timeout_seconds": 150},)
    resources = _resources(tmp_path, commands=commands)
    closed = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(run_local_workflow_test, root, MODULE_ID, input_payload={}, transport_kind="codex_cli",
            model_id=REAL_MODEL, reasoning_profile=REAL_EFFORT, resources_path=resources,
            resource_cancel_requested=closed.is_set)
        deadline = time.monotonic() + 240
        started = lambda: list((root / ".runtime" / "attempt_workspaces").glob("*/work/scratch/started"))
        while not started() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert started(), "declared command did not start"
        closed.set()
        record = pending.result(timeout=120)
    assert record["status"] == "failed" and record["output"] is None, record["status"]
    assert record["failure_detail"]["failure_code"] != "codex_cli_interrupted"
    trace = record["provider_trace"]
    assert trace["local_command_calls"], "command records are kept"
    assert trace["local_command_calls"][0]["request"] == {"command_id": "wait"}
    assert trace["raw_streams"]["stdout"]["data"]
