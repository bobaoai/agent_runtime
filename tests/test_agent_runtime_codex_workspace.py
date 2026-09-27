"""Codex workspace v3: the Reviewer default environment executed by Codex.

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
# Tool-free Profile and argv produced before workspace v3 existed (Runtime 0952002).
V4_PROFILE_SHA256 = "e35072ea7f3170b37cf08c437389bee6407646da2c82a76b3476e5f8d112cdb4"
V4_ARGV = ["/bin/codex", "exec", "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="xhigh"',
    "-c", "project_doc_max_bytes=0", "-c", 'approval_policy="never"', "-c", 'cli_auth_credentials_store="file"',
    "-c", "agents.enabled=false", "-c", "skills.include_instructions=false",
    "-c", 'features.code_mode.excluded_tool_namespaces=["functions","collaboration","clock"]',
    "-c", "mcp_servers={}", "-c", "features.skip_host_skill_discovery=true", "-c", 'web_search="disabled"',
    *[part for name in ("shell_tool", "multi_agent", "plugins", "plugin_sharing", "apps", "browser_use", "computer_use",
                        "skill_search", "tool_suggest", "memories", "view_image", "image_generation", "hooks",
                        "workspace_dependencies") for part in ("-c", f"features.{name}=false")],
    "-C", "/attempt/cwd", "-s", "read-only", "--skip-git-repo-check", "--ignore-user-config", "--ignore-rules",
    "--strict-config", "--ephemeral", "--json", "--output-schema", "/schema.json", "-"]


def _codex_profile(**changes):
    return _execution_profile_for_requirements(_requirements(**changes), transport_kind="codex_cli",
                                               model_id=REAL_MODEL, reasoning_profile="xhigh")


def _option(argv, prefix):
    values = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "-c" and argv[index + 1].startswith(prefix)]
    assert len(values) == 1, (prefix, values)
    return values[0]


@pytest.mark.deterministic
def test_codex_binding_follows_the_module_execution_mode():
    agent = _codex_profile()
    assert (agent.executor_adapter_id, agent.executor_adapter_revision) == ("codex_cli_agent_workspace_executor", "v3")
    assert agent.tool_policy == ("read", "search", "shell")
    tool_free = _codex_profile(execution_mode="tool_free", tool_policy=(), attempt_workspace_policy="none",
                               timeout_seconds=30, max_attempts=1)
    assert (tool_free.executor_adapter_id, tool_free.executor_adapter_revision) == ("codex_cli_agent_executor", "v4")
    assert tool_free.release_sha256 == V4_PROFILE_SHA256
    assert codex.build_command(profile=tool_free, workspace=Path("/attempt/cwd"), codex_bin="/bin/codex",
                               schema_path=Path("/schema.json")) == V4_ARGV


@pytest.mark.deterministic
@pytest.mark.parametrize("tools", [("read",), ("search",), ("read", "search")])
def test_codex_refuses_read_or_search_without_shell_as_unsupported(tools):
    with pytest.raises(codex.CodexCapabilityUnsupportedError, match="through its shell tool") as refused:
        _codex_profile(tool_policy=tools)
    assert refused.value.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED"


@pytest.mark.deterministic
@pytest.mark.parametrize("changes", [
    dict(attempt_workspace_policy="none"),
    dict(network_policy="direct_sandboxed"),
    dict(semantic_input_delivery_mode="gateway_read", network_policy="gateway_only", gateway_access_reasons=("semantic_input",)),
])
def test_codex_refuses_missing_draft_open_network_or_gateway(changes):
    with pytest.raises(ValueError):
        _codex_profile(**changes)


@pytest.mark.deterministic
def test_permission_profile_maps_locations_and_grants_program_files_only(tmp_path):
    host_temp = (tmp_path / "host_tmp").resolve()
    workspace = host_temp / "workspace"
    attempt = workspace / "module_attempt_1" / "work"
    main, materials = attempt / "scratch", attempt / "materials"
    state = host_temp / "agent-runtime-codex-state-1"
    auth = (tmp_path / "login").resolve()
    dependency = (tmp_path / "dependency").resolve()
    program = Path("/opt/codex/bin/codex")
    profile = codex.codex_permission_profile(main_folder=main, materials=materials, readable=(dependency,),
        program_files=(program,), protected=(host_temp, state, auth), host_temp_root=host_temp,
        workspace_root=workspace)
    filesystem = profile["filesystem"]
    assert (profile["extends"], profile["network"]) == (":read-only", {"enabled": False})
    assert [path for path, access in filesystem.items() if access == "write"] == [str(main)]
    assert {path for path, access in filesystem.items() if access == "read"} == {str(materials), str(dependency), str(program)}
    assert str(program.parent) not in filesystem
    for denied in ("/Users", "/Volumes", "/tmp", "/private/tmp", "/private/var/folders", "/var/folders",
                   str(host_temp), str(state), str(auth), str(workspace)):
        assert filesystem[denied] == "deny", denied
    assert ":tmpdir" not in filesystem


@pytest.mark.deterministic
@pytest.mark.parametrize("grant", ["home", "codex_home", "root", "users", "host_temp", "host_temp_parent",
                                   "provider_state", "auth_directory", "inside_workspace", "workspace_parent"])
def test_permission_profile_refuses_grants_that_reopen_denied_locations(tmp_path, grant):
    host_temp = (tmp_path / "host_tmp").resolve()
    workspace = host_temp / "workspace"
    state, auth = host_temp / "state", (tmp_path / "login").resolve()
    attempt = workspace / "module_attempt_1" / "work"
    value = {"home": Path.home(), "codex_home": Path.home() / ".codex" / "packages", "root": Path("/"),
             "users": Path("/Users"), "host_temp": host_temp, "host_temp_parent": host_temp.parent,
             "provider_state": state / "nested", "auth_directory": auth, "inside_workspace": workspace / "other",
             "workspace_parent": workspace.parent}[grant]
    with pytest.raises(PermissionError):
        codex.codex_permission_profile(main_folder=attempt / "scratch", materials=attempt / "materials",
            readable=(value,), program_files=(), protected=(host_temp, state, auth), host_temp_root=host_temp,
            workspace_root=workspace)


@pytest.mark.deterministic
def test_permission_profile_accepts_a_dependency_inside_the_host_temporary_root(tmp_path):
    host_temp = (tmp_path / "host_tmp").resolve()
    workspace, dependency = host_temp / "workspace", host_temp / "fixture_dependency"
    attempt = workspace / "module_attempt_1" / "work"
    profile = codex.codex_permission_profile(main_folder=attempt / "scratch", materials=attempt / "materials",
        readable=(dependency,), program_files=(), protected=(host_temp,), host_temp_root=host_temp,
        workspace_root=workspace)
    assert profile["filesystem"][str(dependency)] == "read"
    with pytest.raises(PermissionError, match="outside its workspace"):
        codex.codex_permission_profile(main_folder=tmp_path / "elsewhere", materials=attempt / "materials",
            readable=(), program_files=(), protected=(host_temp,), host_temp_root=host_temp, workspace_root=workspace)


@pytest.mark.deterministic
def test_command_sandbox_containment_refuses_protected_locations_under_its_read_roots():
    roots = tuple(map(Path, _SYSTEM_READ_ROOTS))
    exposed = codex.command_sandbox_conflicts(
        protected=(Path("/Library/Application Support/codex-login"), Path("/usr/local/var/tmp")),
        command_readable_roots=roots)
    assert exposed == (Path("/Library/Application Support/codex-login"), Path("/usr/local/var/tmp"))
    default_layout = (Path(tempfile.gettempdir()).resolve(), Path(tempfile.gettempdir()).resolve() / "state",
                      Path.home() / ".codex")
    assert codex.command_sandbox_conflicts(protected=default_layout, command_readable_roots=roots) == ()


@pytest.mark.deterministic
def test_workspace_command_turns_on_only_the_shell_and_carries_the_permission_profile():
    profile = _codex_profile()
    permission = {"extends": ":read-only", "filesystem": {"/Users": "deny", "/w/scratch": "write"},
                  "network": {"enabled": False}}
    argv = codex.build_workspace_command(profile=profile, main_folder=Path("/w/scratch"), codex_bin="/bin/codex",
                                         permission_profile=permission)
    assert "-s" not in argv and argv[argv.index("-C") + 1] == "/w/scratch"
    assert _option(argv, "features.shell_tool=") == "features.shell_tool=true"
    assert _option(argv, "features.code_mode.excluded_tool_namespaces=").endswith('["collaboration","clock"]')
    assert _option(argv, "mcp_servers") == "mcp_servers={}"
    assert _option(argv, "allow_login_shell=") == "allow_login_shell=false"
    assert _option(argv, "default_permissions=") == 'default_permissions="runtime_attempt"'
    assert _option(argv, "permissions.runtime_attempt.filesystem=") == (
        'permissions.runtime_attempt.filesystem={"/Users"="deny", "/w/scratch"="write"}')
    assert _option(argv, "permissions.runtime_attempt.network.enabled=").endswith("=false")
    assert all(_option(argv, f"features.{name}=").endswith("=false") for name in codex._FEATURES_OFF)
    with pytest.raises(ValueError):
        codex.build_workspace_command(profile=_codex_profile(execution_mode="tool_free", tool_policy=(),
            attempt_workspace_policy="none"), main_folder=Path("/w"), codex_bin="/bin/codex", permission_profile=permission)


@pytest.mark.deterministic
@pytest.mark.parametrize("path", ["/private/var/tmp/dependency-\U0001F600", "/tmp/\u4e2d\u6587/\u8def\u5f84",
                                  '/tmp/quote"and\\backslash', "/tmp/tab\tnewline\nnul\x00del\x7f",
                                  "/tmp/\U0010FFFF"])
def test_workspace_command_encodes_every_valid_path_losslessly(path):
    profile = _codex_profile()
    permission = {"extends": ":read-only", "filesystem": {path: "read", "/w/scratch": "write"},
                  "network": {"enabled": False}}
    argv = codex.build_workspace_command(profile=profile, main_folder=Path("/w/scratch"), codex_bin="/bin/codex",
                                         permission_profile=permission)
    rendered = _option(argv, "permissions.runtime_attempt.filesystem=").split("=", 1)[1]
    assert tomllib.loads("filesystem = " + rendered)["filesystem"] == permission["filesystem"]
    assert "\\ud8" not in rendered.lower() and "\\udd" not in rendered.lower()
    assert tomllib.loads("value = " + codex._toml([path]))["value"] == [path]


@pytest.mark.deterministic
def test_workspace_command_refuses_a_path_that_is_not_valid_unicode():
    permission = {"extends": ":read-only", "filesystem": {"/tmp/undecodable-\udcff": "read", "/w/scratch": "write"},
                  "network": {"enabled": False}}
    with pytest.raises(ValueError, match="not valid Unicode"):
        codex.build_workspace_command(profile=_codex_profile(), main_folder=Path("/w/scratch"),
                                      codex_bin="/bin/codex", permission_profile=permission)


@pytest.mark.deterministic
def test_codex_layout_text_states_the_main_folder_with_relative_paths_only(tmp_path):
    profile = _codex_profile()
    source = tmp_path / "materials"
    source.mkdir()
    (source / "note.txt").write_text("note\n")
    body = capture_local_resources(profile=profile, material_root=source, material_files=(
        {"relative_path": "note.txt", "sha256": hashlib.sha256(b"note\n").hexdigest(), "executable": False},),
        read_only_dependencies=(), commands=({"command_id": "check", "argv": ["/bin/cat", "note.txt"],
                                              "cwd": "source", "timeout_seconds": 5},))
    text = describe_local_resources(profile=profile, body=body)
    assert "current working directory is the only location you may modify" in text
    assert "TMPDIR" in text and "../materials/task_input" in text and "../materials/source" in text
    assert "sandbox_command_execute" in text and '"command_id": "check"' in text
    assert str(tmp_path) not in text
    assert describe_local_resources(profile=profile, body=None).count("only location you may modify") == 1


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


def _sandbox(argv, environment, cwd, command):
    """Run one shell command in Codex's sandbox with the exact permission options the executor rendered."""
    options = [argv[index + 1] for index, value in enumerate(argv[:-1])
               if value == "-c" and argv[index + 1].startswith(("default_permissions=", "permissions."))]
    sandboxed = [argv[0], "sandbox", *[part for option in options for part in ("-c", option)],
                 "-P", codex.PERMISSION_PROFILE_NAME, "-C", str(cwd), "--", "/bin/sh", "-c", command]
    return subprocess.run(sandboxed, env=environment, capture_output=True, text=True, timeout=60)


class _Listener:
    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        received = self.received = []
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.path)
                self.send_response(200)
                self.end_headers()
            def log_message(self, *_):
                pass
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def _boundary_run(tmp_path, monkeypatch, *, then_provider):
    """Probe the boundary of one real Attempt through Test Run, then optionally start the Provider."""
    # A non-BMP character makes the real Codex parse and apply a path outside the Basic Multilingual Plane.
    dependency = tmp_path / "dependency-\U0001F600"
    dependency.mkdir()
    (dependency / "fixture.txt").write_text("dependency original\n")
    sentinel = Path(tempfile.mkdtemp(prefix="codex-boundary-sentinel-")).resolve()
    (sentinel / "secret.txt").write_text("host temporary sentinel\n")
    listener = _Listener()
    probes, observed = {}, {}
    marker = f".runtime_codex_probe_{uuid.uuid4().hex}"

    class Probing(codex.CodexCliAgentWorkspaceModuleExecutor):
        def __init__(self, **fields):
            def invoke(**call):
                cwd, environment, argv = call["cwd"], call["environment"], call["argv"]
                state = Path(environment["CODEX_HOME"])
                observed.update(cwd=cwd, environment=dict(environment), argv=list(argv), state=state)
                program_dir = Path(argv[0]).resolve().parent
                checks = {
                    "main_write": ("printf main > probe_main.txt && cat probe_main.txt", True),
                    "tmpdir_write": ('printf tmp > "$TMPDIR/probe_tmp.txt" && cat "$TMPDIR/probe_tmp.txt"', True),
                    "materials_read": ("cat ../materials/task_input", True),
                    "materials_write": ("printf x >> ../materials/task_input", False),
                    "dependency_read": (f"cat {dependency / 'fixture.txt'}", True),
                    "dependency_write": (f"printf x >> {dependency / 'fixture.txt'}", False),
                    "host_temp_other": (f"cat {sentinel / 'secret.txt'}", False),
                    "provider_state": (f"ls {state}", False),
                    "slash_tmp_read": ("ls /tmp/", False),
                    "slash_tmp_write": (f"printf x > /private/tmp/{marker}", False),
                    "users": ("ls /Users", False),
                    "codex_home": (f"ls {Path.home() / '.codex'}", False),
                    "program_dir": (f"ls {program_dir}", False),
                    "network": (f"/usr/bin/curl -sS -m 5 http://127.0.0.1:{listener.server.server_port}/probe", False),
                    "system_read": ("ls /Library >/dev/null", True),
                }
                if os.access("/opt/homebrew", os.W_OK):
                    checks["system_write"] = (f"touch /opt/homebrew/{marker}", False)
                for name, (command, allowed) in checks.items():
                    done = _sandbox(argv, environment, cwd, command)
                    probes[name] = (done.returncode, done.stdout + done.stderr, allowed)
                if then_provider:
                    return codex._default_invoke(**call)
                raise RuntimeError("boundary probes complete; this run does not start the Provider")
            super().__init__(**fields, invoker=invoke)

    monkeypatch.setattr(codex, "CodexCliAgentWorkspaceModuleExecutor", Probing)
    root = _register(tmp_path, "This is a Runtime capability test with no task steps.\n" + _PASSING)
    try:
        record = run_local_workflow_test(root, MODULE_ID, input_payload={}, transport_kind="codex_cli",
            model_id=REAL_MODEL, reasoning_profile=REAL_EFFORT,
            resources_path=_resources(tmp_path, dependencies=(dependency,)))
    finally:
        listener.close()
        shutil.rmtree(sentinel)
        for leftover in (Path("/private/tmp") / marker, Path("/opt/homebrew") / marker):
            leftover.unlink(missing_ok=True)
    return record, probes, observed, listener.received, dependency


def _assert_probes(probes, received, dependency):
    for name, (returncode, output, allowed) in probes.items():
        assert (returncode == 0) is allowed, (name, returncode, output)
    assert "host temporary sentinel" not in probes["host_temp_other"][1]
    assert received == []
    assert (dependency / "fixture.txt").read_text() == "dependency original\n"


@pytest.mark.real_run
@REAL_GATE
def test_real_attempt_boundary_holds_in_codex_sandbox_and_the_provider_completes(tmp_path, monkeypatch):
    """Real entry: Test Run prepares an Attempt; installed Codex sandbox runs each probe; then the real Provider runs."""
    record, probes, observed, received, dependency = _boundary_run(tmp_path, monkeypatch, then_provider=True)
    _assert_probes(probes, received, dependency)
    assert record["status"] == "completed", record["failure_detail"]
    trace = record["provider_trace"]
    assert trace["main_folder"] == trace["cwd"] == str(observed["cwd"])
    assert observed["environment"]["TMPDIR"] == str(Path(trace["main_folder"]) / ".tmp")
    assert trace["isolation_gaps"] == ["system_directories_readable"]
    filesystem = trace["codex_permission_profile"]["filesystem"]
    assert filesystem[str(observed["state"].resolve())] == "deny"
    assert filesystem[str(Path(tempfile.gettempdir()).resolve())] == "deny"
    rendered = _option(trace["argv"], "permissions.runtime_attempt.filesystem=").split("=", 1)[1]
    assert tomllib.loads("filesystem = " + rendered)["filesystem"] == filesystem


@pytest.mark.real_run
@REAL_GATE
def test_real_attempt_boundary_covers_nondefault_temporary_and_credential_locations(tmp_path, monkeypatch):
    """Real entry: Runtime TMPDIR and the credential file both live under /private/var/tmp; Codex sandbox probes only."""
    base = Path(tempfile.mkdtemp(prefix="codex-nondefault-", dir="/private/var/tmp")).resolve()
    try:
        host_temp, login = base / "host_tmp", base / "login"
        host_temp.mkdir()
        login.mkdir()
        (login / "auth.json").write_text('{"synthetic": "location test only"}')
        monkeypatch.setattr(tempfile, "tempdir", str(host_temp))
        monkeypatch.setenv("CODEX_HOME", str(login))
        record, probes, observed, received, dependency = _boundary_run(tmp_path, monkeypatch, then_provider=False)
        _assert_probes(probes, received, dependency)
        filesystem = record["provider_trace"]["codex_permission_profile"]["filesystem"]
        assert filesystem[str(host_temp)] == filesystem[str(login)] == "deny"
        for target in (login / "auth.json", observed["state"]):
            done = _sandbox(observed["argv"], observed["environment"], observed["cwd"], f"ls {target}")
            assert done.returncode != 0 and "synthetic" not in done.stdout, target
        assert record["status"] == "failed" and record["output"] is None
    finally:
        shutil.rmtree(base)


@pytest.mark.real_run
@REAL_GATE
@pytest.mark.parametrize("dependency", ["home", "credential_directory", "host_temp_root"])
def test_real_test_run_refuses_overlapping_dependencies_before_the_provider(tmp_path, monkeypatch, dependency):
    """Real entry: Test Run with the installed Codex CLI; the refusal comes before any Provider or command process."""
    host_temp = tmp_path / "host_tmp"
    host_temp.mkdir()
    login = tmp_path / "login"
    login.mkdir()
    (login / "auth.json").write_text('{"synthetic": "never used"}')
    monkeypatch.setattr(tempfile, "tempdir", str(host_temp))
    monkeypatch.setenv("CODEX_HOME", str(login))
    launched, real_process = [], codex.run_cli_process
    def observed_process(**fields):
        launched.append(fields["argv"])
        return real_process(**fields)
    monkeypatch.setattr(codex, "run_cli_process", observed_process)
    root = _register(tmp_path, "Capability refusal test.\n" + _PASSING)
    value = {"home": Path.home(), "credential_directory": login, "host_temp_root": host_temp}[dependency]
    record = run_local_workflow_test(root, MODULE_ID, input_payload={}, transport_kind="codex_cli", model_id=REAL_MODEL,
        reasoning_profile=REAL_EFFORT, resources_path=_resources(tmp_path, dependencies=(value,)))
    # The kernel records the adapter's PermissionError as the failed Attempt; nothing ran.
    assert record["status"] == "failed" and record["output"] is None and record["provider_trace"] is None
    assert record["failure_detail"]["exception_type"] == "PermissionError", record["failure_detail"]
    assert "overlaps" in record["failure_detail"]["message"] or "denied root" in record["failure_detail"]["message"]
    assert launched == []
    assert not list(host_temp.glob("agent-runtime-codex-state-*"))


@pytest.mark.real_run
@REAL_GATE
def test_real_test_run_refuses_the_filesystem_root_as_a_dependency(tmp_path):
    root = _register(tmp_path, "Capability refusal test.\n" + _PASSING)
    with pytest.raises(LocalResourceError, match="filesystem root"):
        run_local_workflow_test(root, MODULE_ID, input_payload={}, transport_kind="codex_cli", model_id=REAL_MODEL,
            reasoning_profile=REAL_EFFORT, resources_path=_resources(tmp_path, dependencies=(Path("/"),)))


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
    assert trace["isolation_gaps"] == ["system_directories_readable"]
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
def test_real_resource_close_during_a_command_fails_the_attempt_and_keeps_its_records(tmp_path, monkeypatch):
    """Real entry: Test Run with the installed Codex CLI; the public resource_cancel_requested closes resources mid-command."""
    host_temp = tmp_path / "host_tmp"
    host_temp.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(host_temp))
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
        while not list(host_temp.glob("agent-runtime-self-test-*/*/work/scratch/started")) and time.monotonic() < deadline:
            time.sleep(0.2)
        assert list(host_temp.glob("agent-runtime-self-test-*/*/work/scratch/started")), "declared command did not start"
        closed.set()
        record = pending.result(timeout=120)
    assert record["status"] == "failed" and record["output"] is None, record["status"]
    assert record["failure_detail"]["failure_code"] != "codex_cli_interrupted"
    trace = record["provider_trace"]
    assert trace["local_command_calls"], "command records are kept"
    assert trace["local_command_calls"][0]["request"] == {"command_id": "wait"}
    assert trace["raw_streams"]["stdout"]["data"]
