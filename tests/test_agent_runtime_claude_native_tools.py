"""Profile-selected Claude CLI execution and AB integration examples."""
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from agent_runtime import parse_cli_log
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.invocation.invocation_process_execution import CliProcessError, CliProcessTimeout
from agent_runtime.contracts.registry_release_definition import ExecutionProfileRelease, OutputResolutionPolicy
from agent_runtime.contracts.execution_module_definition import ModuleInputBinding, ModuleExecutionRequest
from agent_runtime.foundation.foundation_contract_validation import validate_model_id
from agent_runtime.execution import AgentExecutionAdapterRegistry, InMemoryCellArtifactStore
from agent_runtime.ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger
from agent_runtime.execution.execution_module_invocation import run_module
from test_agent_runtime_native_structured_output import (
    _compile_native_module, _register_compiled_for_evaluation, _evaluation_prompt,
    _evaluation_request, _evaluation_authority, _assert_completed_provider_run, _TEST_TIME,
    _unsupported_positional_output_schema,
)


@pytest.mark.fake_run
@pytest.mark.parametrize("closed", [False, True])
def test_legacy_self_test_host_without_optional_cancel_port_keeps_guard(tmp_path, monkeypatch, closed):
    import test_agent_runtime_native_structured_output as native
    from test_agent_runtime_codex_environment import self_test_request
    from agent_runtime.contracts.invocation_adapter_definition import SelfTestResourceUnavailableError
    compiled, registry, cell, _, _ = _environment(tmp_path, tools=(), mode="tool_free", workspace="none")
    prompt = native._evaluation_prompt(cell, compiled, suffix="legacy_host")
    request = self_test_request(native._direct_adapter_request(compiled, prompt, suffix="legacy_host"))
    effects = []
    class Host(native._RecordingHost):
        def validate_self_test_binding(self, actual, **ports):
            assert actual == request
        def guard_self_test_launch(self, actual, launch, **ports):
            self.validate_self_test_binding(actual, **ports)
            if closed:
                raise SelfTestResourceUnavailableError("existing resource guard is closed")
            return launch()
    def process(**fields):
        assert "cancel_requested" not in fields and "user_cancel_requested" not in fields
        fields["launch_guard"](lambda: effects.append("executed"))
        lines = [json.dumps(event) for event in (_init(()), _result())]
        for line in lines:
            assert fields["on_stdout_line"](line)
        result = subprocess.CompletedProcess(fields["argv"], 0, "\n".join(lines), "")
        result.stdout_bytes, result.stderr_bytes = result.stdout.encode(), b""
        return result
    adapter = claude.ClaudeAdapter(release_registry=registry, artifact_host=cell, workspace_root=tmp_path / "attempts",
                                  cli_path=_fake_cli(tmp_path, monkeypatch), process_runner=process)
    result = adapter.execute(request, Host())
    assert result.terminal_status == ("failed" if closed else "completed")
    assert effects == ([] if closed else ["executed"])
    if not closed:
        assert result.input_tokens == 7 and result.cell_local_trace_ref


@pytest.mark.fake_run
@pytest.mark.parametrize("failure_on", [1, 2])
def test_claude_handoff_checks_control_once_and_keeps_observed_result(tmp_path, monkeypatch, failure_on):
    import test_agent_runtime_native_structured_output as native
    from test_agent_runtime_codex_environment import self_test_request
    from agent_runtime.foundation.foundation_json_encoding import cli_stream_bytes
    compiled, registry, cell, _, _ = _environment(tmp_path, tools=(), mode="tool_free", workspace="none")
    prompt = native._evaluation_prompt(cell, compiled, suffix="late_control")
    request = self_test_request(native._direct_adapter_request(compiled, prompt, suffix="late_control"))
    queries, calls = [], []
    class Host(native._RecordingHost):
        def validate_self_test_binding(self, actual, **ports):
            assert actual == request
        def guard_self_test_launch(self, actual, launch, **ports):
            return launch()
        def self_test_cancel_requested(self, actual, *, user=False):
            queries.append(user)
            if len(queries) == failure_on:
                raise RuntimeError("late Claude control failure")
            return False
    raw = "\n".join(json.dumps(event) for event in (_init(()), _result())).encode()
    def process(**fields):
        fields["launch_guard"](lambda: calls.append(fields))
        for line in raw.decode().splitlines():
            assert fields["on_stdout_line"](line)
        result = subprocess.CompletedProcess(fields["argv"], 0, raw.decode(), "")
        result.stdout_bytes, result.stderr_bytes = raw, b""
        return result
    adapter = claude.ClaudeAdapter(release_registry=registry, artifact_host=cell, workspace_root=tmp_path / "attempts",
                                  cli_path=_fake_cli(tmp_path, monkeypatch), process_runner=process)
    result = adapter.execute(request, Host())
    if failure_on == 1:
        assert result.terminal_status == "failed" and not result.outputs
        assert result.failure.failure_class == "dependency_unavailable"
        assert native._direct_failure_detail(result, cell)["failure_code"] == "ADAPTER_BINDING_UNAVAILABLE"
    else:
        assert result.terminal_status == "completed" and result.failure is None
    assert result.input_tokens == 7 and result.output_tokens == 3
    trace = json.loads(cell.read_bytes(result.cell_local_trace_ref, result.cell_local_trace_sha256))
    assert cli_stream_bytes(trace, "stdout") == raw
    assert len(calls) == len(queries) == 1


@pytest.mark.parametrize("during_handoff", ["control_error", "late_control_error", "late_cancel", "cleanup_error"])
def test_failed_result_handoff_saves_one_diagnostic_and_keeps_trace(tmp_path, monkeypatch, during_handoff):
    from agent_runtime.invocation.invocation_result_assembly import (
        TerminalAdapterFailure, finalize_adapter_result, raise_terminal_failure,
    )
    import test_agent_runtime_native_structured_output as native
    compiled, _, cell, _, _ = _environment(tmp_path, tools=(), mode="tool_free", workspace="none")
    prompt = native._evaluation_prompt(cell, compiled, suffix="failure_handoff")
    request = native._direct_adapter_request(compiled, prompt, suffix="failure_handoff")
    trace = {"transport": "claude_cli", "stdout": "original provider diagnostic",
             "adapter_failure": {"failure_class": "dependency_unavailable", "message": "original failure"}}
    with pytest.raises(TerminalAdapterFailure) as pending:
        raise_terminal_failure(artifact_host=cell, request=request, profile=compiled.execution_profile,
            failure_class="dependency_unavailable", failure_code="ADAPTER_BINDING_UNAVAILABLE",
            message="original failure", provider_response="original provider diagnostic",
            retry_disposition_id="retry_denied", trace=trace, input_tokens=7, output_tokens=3,
            defer_failure_detail=True)
    original = pending.value.result
    queries, commits = [], []
    cancelled = False
    def interruption():
        queries.append(True)
        if during_handoff == "control_error" or (during_handoff == "late_control_error" and len(queries) > 1):
            raise RuntimeError("control unavailable")
        return cancelled
    commit = cell.commit_failure_detail
    def save(**fields):
        nonlocal cancelled
        commits.append(fields)
        cancelled = during_handoff == "late_cancel"
        return commit(**fields)
    monkeypatch.setattr(cell, "commit_failure_detail", save)
    result = finalize_adapter_result(artifact_host=cell, request=request, result=original,
        pending_failure_detail=pending.value.pending_failure_detail, interruption_requested=interruption,
        cleanup_error=OSError("cleanup failed") if during_handoff == "cleanup_error" else None,
        interruption_code="claude_cli_interrupted", cleanup_failure_code="claude_cli_cleanup_failed")
    assert len(queries) == len(commits) == 1
    assert result.terminal_status == "failed" and result.outputs == ()
    assert result.failure.failure_class == ("transport" if during_handoff == "cleanup_error" else "dependency_unavailable")
    assert result.cell_local_trace_ref == original.cell_local_trace_ref
    assert result.cell_local_trace_sha256 == original.cell_local_trace_sha256
    assert json.loads(cell.read_bytes(result.cell_local_trace_ref, result.cell_local_trace_sha256)) == trace
    assert (result.input_tokens, result.output_tokens) == (7, 3)
    diagnostic = native._direct_failure_detail(result, cell)
    assert diagnostic["failure_code"] == ("claude_cli_cleanup_failed" if during_handoff == "cleanup_error"
                                          else "ADAPTER_BINDING_UNAVAILABLE")
    if during_handoff in {"late_control_error", "late_cancel"}:
        assert diagnostic["message"] == "original failure"


def _environment(tmp_path, *, tools=("read", "search", "shell"), model="claude-opus-5[1m]", effort="xhigh", material=None, instructions="",
                 mode="agent", workspace="own_draft_read_write", output_mode="native_structured_output",
                 binding=("claude_cli_adapter", "v4"), output_schema_document=None, network=None):
    """Compile one Claude Module and request.

    network defaults to the ordinary agent policy, provider_managed; a tool_free Profile
    must deny network. Pass network="denied" for an explicitly saved strict agent Profile.
    """
    network = network or ("denied" if mode == "tool_free" else "provider_managed")
    compiled = _compile_native_module(
        tmp_path, output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="claude_fields", executor_adapter_id=binding[0],
        executor_adapter_revision=binding[1], transport_kind="claude_cli", provider_id="anthropic",
        model_id=model, reasoning_profile=effort, execution_mode=mode, network_policy=network,
        attempt_workspace_policy=workspace, tool_policy=tools, output_constraint_mode=output_mode,
        **({"output_schema_document": output_schema_document} if output_schema_document is not None else {}),
    )
    registry = _register_compiled_for_evaluation(compiled)
    cell = InMemoryCellArtifactStore()
    prompt = _evaluation_prompt(cell, compiled, suffix="native_tools", execution_specific_instructions=instructions)
    request = _evaluation_request(compiled, prompt, suffix="native_tools")
    if material is not None:
        stored = cell.put_bytes(artifact_kind_id="source", schema_version="v1",
            schema_ref="schema:source@v1", schema_sha256="4" * 64, media_type="text/plain",
            content=material, idempotency_key="native_material")
        request = ModuleExecutionRequest.build(**{
            item.name: getattr(request, item.name) for item in fields(request)
            if item.name not in {"inputs", "input_closure_sha256", "request_sha256"}
        }, inputs=(ModuleInputBinding("source", stored.artifact_ref, stored.artifact_sha256,
                                      "schema:source@v1", "4" * 64, "text/plain"),))
    authority, _ = _evaluation_authority(registry, request)
    return compiled, registry, cell, request, authority


# The options the Claude Adapter's preflight requires from `claude --help` today.
SYNTHETIC_CLAUDE_HELP = ("--safe-mode --tools --allowedTools --permission-mode --settings --effort --json-schema "
                         "--strict-mcp-config --add-dir")
SYNTHETIC_CLAUDE_VERSION = "2.1.999 (synthetic Claude CLI)"


def _launch_through_interpreter(script, monkeypatch):
    """Start the named synthetic script with the base interpreter on Windows; leave every other argv alone.

    Windows starts only native .exe programs (a script's shebang is not honored), so the
    single process-creation entry point is wrapped for this test: an argv whose program is
    exactly this script becomes [sys._base_executable, script, *arguments]. Everything else
    still reaches the product's native-program check unchanged.
    """
    from agent_runtime.invocation import invocation_process_execution as processes
    registered = os.path.normcase(os.path.abspath(script))
    current = processes.HOST_PLATFORM.create_cli_process

    def create(argv, **kwargs):
        program = Path(str(argv[0])) if argv else None
        if program is not None and not program.is_absolute():
            program = Path(kwargs.get("cwd") or ".") / program
        if program is not None and os.path.normcase(os.path.abspath(program)) == registered:
            argv = [sys._base_executable, str(script), *list(argv)[1:]]
        return current(argv, **kwargs)
    monkeypatch.setattr(processes.HOST_PLATFORM, "create_cli_process", create)


def _scripted_cli(path, body, monkeypatch):
    """Named substitute: a stdlib-only Python script standing in for a CLI program.

    POSIX runs its shebang natively; on Windows it is started through the base
    interpreter by the scoped process-creation wrapper above. Returns the script path.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
    path.chmod(0o700)
    if os.name == "nt":
        _launch_through_interpreter(path, monkeypatch)
    return path


def _fake_cli(tmp_path, monkeypatch):
    """Named substitute for the Claude executable's real preflight: --version and --help only.

    The Provider call itself is served by each test's process-runner double; any other
    argv exits 3, so an accidental real Provider launch fails loudly.
    """
    return _scripted_cli(Path(tmp_path) / "claude", (
        "import sys\n"
        f"if sys.argv[1:] == ['--version']:\n    print({SYNTHETIC_CLAUDE_VERSION!r})\n"
        f"elif sys.argv[1:] == ['--help']:\n    print({SYNTHETIC_CLAUDE_HELP!r})\n"
        "else:\n"
        "    sys.stderr.write('synthetic Claude CLI answers only --version and --help\\n')\n"
        "    sys.exit(3)\n"), monkeypatch)


def _process_alive(pid):
    """Whether a process with this ID is still running.

    POSIX probes with signal 0. On Windows os.kill(pid, 0) would send CTRL_C_EVENT instead,
    so the process is opened for a limited query and its exit code compared with STILL_ACTIVE.
    """
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def _require_symlink(tmp_path):
    """Skip with the real prerequisite when this host cannot create a test-owned symbolic link.

    Shared by the link checks of Runtime-owned material, write, archive and cleanup paths. It never
    enables a privilege, and a directory junction is not offered as a substitute.
    """
    probe = Path(tmp_path) / ".symlink_probe"
    try:
        probe.symlink_to(Path(tmp_path), target_is_directory=True)
    except OSError as exc:
        code = getattr(exc, "winerror", None) or exc.errno
        pytest.skip(f"unverified on this host: symbolic-link creation privilege unavailable (error {code})")
    probe.unlink()


def _allowed(argv):
    return argv[argv.index("--allowedTools") + 1:argv.index("--settings")]


def _native(profile):
    """Claude tools for the recorded logical tools, in Profile order (read: Read; search: Grep, Glob; ...)."""
    return [tool for name in profile.tool_policy for tool in claude.NATIVE_TOOLS.get(name, ())]


def _result(**extra):
    return {"type": "result", "subtype": "success", "is_error": False,
            "modelUsage": {"claude-opus-5[1m]": {"canonicalModel": "claude-opus-5", "outputTokens": 3}},
            "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            "structured_output": {"value": "checked"}, **extra}


def _init(tools=("Read", "Grep", "Bash")):
    return {"type": "system", "subtype": "init", "model": "claude-opus-5[1m]",
            "permissionMode": "dontAsk",
            "tools": [*tools, "StructuredOutput"], "skills": [], "plugins": [], "slash_commands": [], "mcp_servers": []}


def _tool_use(identity, name="Read", **arguments):
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": identity, "name": name, "input": arguments}]}}


def _tool_result(identity, *, failed=False, content="actual tool output"):
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": identity, "is_error": failed, "content": content}]}}


def _denial(identity, name="Read"):
    return {"type": "system", "subtype": "permission_denied", "tool_name": name,
            "tool_use_id": identity, "decision_reason_type": "other",
            "decision_reason": "path outside the allowed working directories",
            "message": "The requested path is outside the allowed workspace."}


def _run(env, tmp_path, event_factory, monkeypatch=None):
    """Run the real kernel and ClaudeAdapter with a process-runner double for the Provider call.

    The synthetic CLI's preflight runs as a real process. Pass the test's monkeypatch when
    the adapter is used again after this run; otherwise the launch seam lasts for this run.
    """
    if monkeypatch is None:
        with pytest.MonkeyPatch.context() as scoped:
            return _run(env, tmp_path, event_factory, scoped)
    _, registry, cell, request, authority = env
    def process(**kwargs):
        lines = []
        events = event_factory(kwargs)
        try:
            for event in events:
                line = event if isinstance(event, str) else json.dumps(event)
                lines.append(line)
                if not kwargs["on_stdout_line"](line):
                    raise CliProcessError(0, kwargs["argv"], output="\n".join(lines), stderr="",
                                          stop_reason="observer_stopped", message="Runtime stopped the CLI event stream")
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            if exc.output is None:
                exc.output = "\n".join(lines)
            if exc.stderr is None:
                exc.stderr = ""
            exc.stdout_bytes = exc.stdout.encode("utf-8") if isinstance(exc.stdout, str) else exc.stdout
            exc.stderr_bytes = exc.stderr.encode("utf-8") if isinstance(exc.stderr, str) else exc.stderr
            raise
        finally:
            events.close()
        result = subprocess.CompletedProcess(kwargs["argv"], 0, "\n".join(lines), "")
        result.stdout_bytes, result.stderr_bytes = result.stdout.encode(), b""
        return result
    adapter = claude.ClaudeAdapter(
        release_registry=registry, artifact_host=cell, workspace_root=tmp_path / "attempts",
        cli_path=_fake_cli(tmp_path, monkeypatch), process_runner=process,
        adapter_binding=(env[0].execution_profile.executor_adapter_id, env[0].execution_profile.executor_adapter_revision),
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    run = run_module(request, release_registry=registry, adapters=adapters, artifact_host=cell,
                     ledger=InMemoryModuleExecutionLedger(), authority=authority, clock=lambda: _TEST_TIME)
    return run, cell


@pytest.mark.fake_run
@pytest.mark.parametrize("change", [None, "binding", "legacy_marker"])
def test_attempt_workspace_binds_exact_authorization_boundary(tmp_path, monkeypatch, change):
    captured = []
    entered = []
    execute = claude.ClaudeAdapter._execute
    def capture(adapter, request, host, prepared, cleanup, *, cancellation):
        captured.append((adapter, request, host))
        return execute(adapter, request, host, prepared, cleanup, cancellation=cancellation)
    def events(call):
        entered.append(True)
        yield _init()
        yield _result()
    monkeypatch.setattr(claude.ClaudeAdapter, "_execute", capture)
    run, cell = _run(_environment(tmp_path), tmp_path, events, monkeypatch)  # The adapter runs again below.
    assert run.attempts[0].status == "completed"
    adapter, request, host = captured[0]
    marker = tmp_path / "attempts" / request.attempt_id / ".agent_runtime_attempt.json"
    identity = json.loads(marker.read_text())
    assert identity.get("execution_authorization_binding_ref") == request.execution_authorization_binding_ref
    assert identity.get("execution_authorization_binding_sha256") == request.execution_authorization_binding_sha256
    scratch = marker.parent / "work/scratch/retained.txt"
    scratch.write_text("existing private draft")
    if change == "binding":
        values = {item.name:getattr(request,item.name) for item in fields(request) if item.name != "request_sha256"}
        values.update(execution_authorization_binding_ref="authorization-binding:different",
                      execution_authorization_binding_sha256="f"*64)
        request = type(request).build(**values)
    elif change == "legacy_marker":
        identity.pop("execution_authorization_binding_ref")
        identity.pop("execution_authorization_binding_sha256")
        marker.write_text(json.dumps(identity, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    before = marker.read_bytes()
    # Exercise the Adapter boundary with a valid request. The original host's
    # terminal staging can reject the second result; this is not kernel replay.
    try:
        adapter.execute(request, host)
    except ValueError:
        pass
    assert len(entered) == (2 if change is None else 1)
    assert marker.read_bytes() == before
    assert scratch.read_text() == "existing private draft"


@pytest.mark.fake_run
@pytest.mark.parametrize("target", ["work/scratch", "material_file", "changed_material"])
def test_preinvocation_workspace_policy_refusal_is_recorded(tmp_path, monkeypatch, target):
    """A replaced Runtime-owned work folder or material is refused before the Provider starts.

    One per-directory check covers work, materials and scratch; work/scratch represents it.
    """
    if target != "changed_material":
        _require_symlink(tmp_path)
    entered = []
    outside = tmp_path / "outside"
    outside.mkdir()
    protected = outside / "private.txt"
    protected.write_text("untouched")
    prepare = claude.prepare_attempt_workspace
    def altered_workspace(**kwargs):
        attempt = prepare(**kwargs)
        if target in {"material_file", "changed_material"}:
            materials = attempt / "work/materials"
            materials.mkdir(parents=True)
            path = materials / "source"
            if target == "material_file":
                path.symlink_to(protected)
            else:
                path.write_text("different old bytes")
        else:
            path = attempt / target
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(outside, target_is_directory=True)
        return attempt
    monkeypatch.setattr(claude, "prepare_attempt_workspace", altered_workspace)
    def events(call):
        entered.append(True)
        yield _init()
        yield _result()
    run, cell = _run(_environment(tmp_path, material=b"authorized bytes"), tmp_path, events)
    attempt = run.attempts[0]
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert attempt.status == "failed" and not run.outputs
    assert attempt.failure_class == "policy_violation"
    assert detail["failure_code"] == "ADAPTER_POLICY_VIOLATION"
    assert trace["policy_refusal_reason"]
    assert entered == [] and protected.read_text() == "untouched"


@pytest.mark.fake_run
def test_workspace_dependency_failure_is_not_policy_violation(tmp_path, monkeypatch):
    def unavailable(**kwargs):
        raise OSError("workspace storage unavailable")
    monkeypatch.setattr(claude, "prepare_attempt_workspace", unavailable)
    def events(call):
        pytest.fail("provider must not enter")
        yield _result()
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    assert attempt.failure_class == "dependency_unavailable"
    assert detail["failure_code"] == "ADAPTER_BINDING_UNAVAILABLE"


@pytest.mark.parametrize("model,effort,tools", [
    ("claude-opus-5[1m]", "xhigh", ("read", "search", "shell")),
    ("claude-sonnet-4-6", "high", ("read",)),
    ("claude-opus-5", "low", ("search", "shell")),
])
@pytest.mark.fake_run
def test_profile_drives_one_cli_command_builder(tmp_path, model, effort, tools):
    env = _environment(tmp_path, model=model, effort=effort, tools=tools)
    expected = _native(env[0].execution_profile)
    def events(call):
        argv = call["argv"]
        assert argv[argv.index("--model")+1] == model
        assert argv[argv.index("--effort")+1] == effort
        assert argv[argv.index("--tools")+1].split(",") == expected
        # Only read/search tools are preapproved; Bash needs an explicit host permit.
        assert _allowed(argv) == [tool for tool in expected if tool in {"Read", "Grep", "Glob"}]
        assert "--safe-mode" in argv and "--restricted" not in argv
        assert argv[argv.index("--permission-mode")+1] == "dontAsk"
        assert call["environment"]["GIT_CONFIG_GLOBAL"] == os.devnull
        assert call["environment"]["GIT_CONFIG_NOSYSTEM"] == "1"
        assert "--system-prompt" not in argv and call["prompt"] not in argv
        assert json.loads(argv[argv.index("--mcp-config")+1]) == {"mcpServers": {}}
        yield {**_init(expected), "model": model}
        yield _result(modelUsage={model: {"canonicalModel": model.removesuffix("[1m]"), "outputTokens": 3}})
    run, cell = _run(env, tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}
    profile = env[0].execution_profile
    assert ExecutionProfileRelease.from_dict(profile.as_dict()).as_dict() == profile.as_dict()


@pytest.mark.fake_run
@pytest.mark.parametrize("fault", ["initialization", "response", "missing"])
def test_actual_model_must_match_the_profile(tmp_path, fault):
    def events(call):
        yield {**_init(), **({"model":"claude-sonnet-4-6"} if fault == "initialization" else {})}
        if fault == "response":
            yield {"type":"assistant", "message":{"model":"claude-sonnet-4-6", "content":[]}}
        yield _result(**({"modelUsage":{}} if fault == "missing" else {}))
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.status == "failed" and attempt.usage.input_tokens == 7
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    assert detail["failure_code"] == ("claude_cli_model_identity_unavailable" if fault == "missing"
                                      else "claude_cli_model_identity_mismatch")
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert trace["result"]["structured_output"] == {"value":"checked"}


@pytest.mark.fake_run
def test_preflight_timeout_keeps_byte_diagnostics(tmp_path, monkeypatch):
    env = _environment(tmp_path)
    def preflight(**fields):
        raise subprocess.TimeoutExpired(fields["argv"], 30, output=b"partial version", stderr=b"fixture diagnostic")
    monkeypatch.setattr(claude, "run_cli_process", preflight)  # The owned preflight runner.
    def unused_provider(call):
        raise AssertionError("Provider must not start after failed preflight")
        yield
    run, cell = _run(env, tmp_path, unused_provider)
    attempt = run.attempts[0]
    assert attempt.failure_class == "dependency_unavailable"
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert trace["stdout"] == "partial version"
    assert trace["stderr"] == "fixture diagnostic"
    assert trace["stage"] == "cli_preflight"


@pytest.mark.fake_run
@pytest.mark.parametrize("damage", [None, "change", "remove", "symlink"])
@pytest.mark.parametrize("binding", [("claude_cli_adapter", "v4")])
@pytest.mark.parametrize("process_error", [False, True])
def test_materials_checked_on_process_completion_and_exception(tmp_path, damage, binding, process_error):
    if damage == "symlink":
        _require_symlink(tmp_path)
    env = _environment(tmp_path, material=b"original", binding=binding)
    def events(call):
        yield _init()
        source = call["cwd"] / "materials/source"  # The ordinary Attempt runs in work/, beside materials/.
        assert source.read_bytes() == b"original"
        if damage == "change":
            source.write_bytes(b"changed")
        elif damage in {"remove", "symlink"}:
            source.unlink()
            if damage == "symlink":
                replacement = tmp_path / "replacement"
                replacement.write_bytes(b"original")
                source.symlink_to(replacement)
        yield _result()
        if process_error:
            raise subprocess.CalledProcessError(1, call["argv"], output="", stderr="process failed")
    run, cell = _run(env, tmp_path, events)
    if damage is None and not process_error:
        assert _assert_completed_provider_run(run, cell) == {"value": "checked"}
        return
    assert run.attempts[0].failure_class == ("transport" if damage is None else "policy_violation")
    assert run.attempts[0].usage.input_tokens == 7
    assert not run.outputs


@pytest.mark.fake_run
@pytest.mark.parametrize("surface", ["tools", "skills", "plugins", "slash_commands", "mcp_servers", "permissionMode"])
def test_init_capability_descriptions_do_not_stop_the_run(tmp_path, surface):
    reached = []
    def events(call):
        initial = _init()
        if surface == "permissionMode":
            initial[surface] = "bypassPermissions"
        else:
            initial[surface].append("unexpected")
        yield initial
        reached.append(True)
        yield _result()
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    _assert_completed_provider_run(run, cell)
    assert reached == [True]
    attempt = run.attempts[0]
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert "tool_log" not in trace and "policy_refusal_reason" not in trace
    assert json.loads(trace["stdout"].splitlines()[0])[surface]


@pytest.mark.parametrize("event", ["not-json", {"type": "future_event", "value": [1, 2]},
    {"type": "assistant", "message": {"content": None}},
    {"type": "user", "message": {"content": {"type": "tool_result", "unknown": True}}}])
@pytest.mark.fake_run
def test_non_result_events_are_archived_without_a_tool_parser(tmp_path, monkeypatch, event):
    from agent_runtime.inspection import inspection_execution_logging as inspection
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("ordinary execution must not parse detailed logs")
    monkeypatch.setattr(inspection, "parse_cli_log", forbidden)
    def events(call):
        yield _init()
        yield event
        yield _result()
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    _assert_completed_provider_run(run, cell)
    attempt = run.attempts[0]
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert calls == [] and "tool_log" not in trace
    assert (event if isinstance(event, str) else json.dumps(event)) in trace["stdout"]


@pytest.mark.fake_run
@pytest.mark.parametrize("source", ["event", "result", "combined"])
@pytest.mark.parametrize("binding", [("claude_cli_adapter", "v4")])
def test_cli_permission_denial_allows_later_tools_and_output(tmp_path, source, binding):
    continued = []
    def events(call):
        yield _init()
        yield _tool_use("denied", file_path="outside")
        if source != "result":
            yield _denial("denied")
        if source == "combined":
            yield _tool_result("denied", failed=True, content="Read was refused")
        continued.append(True)
        yield _tool_use("allowed", "Bash", command="printf allowed")
        yield _tool_result("allowed", content="allowed")
        yield _result(permission_denials=[] if source == "event" else [
            {"tool_name": "Read", "tool_use_id": "denied", "tool_input": {"file_path": "outside"}}])
    run, cell = _run(_environment(tmp_path, binding=binding), tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}
    assert continued == [True] and len(run.attempts) == 1
    attempt = run.attempts[0]
    assert attempt.usage.input_tokens == 7 and attempt.usage.output_tokens == 3
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert "policy_refusal_reason" not in trace
    assert "tool_log" not in trace
    inspected = parse_cli_log(trace)
    calls = {item["tool_call_id"]: item for item in inspected["tool_calls"]}
    assert len(calls) == 2 and calls["denied"]["status"] == "failed"
    assert calls["allowed"]["status"] == "completed"
    assert inspected["complete"]
    assert any(row["event"].get("subtype") == "permission_denied" for row in inspected["events"]) is (source != "result")


@pytest.mark.fake_run
@pytest.mark.parametrize("source", ["event", "result", "tool_result"])
def test_unidentified_tool_denial_retains_events_without_inventing_a_call(tmp_path, source):
    observation = _denial(None) if source == "event" else _tool_result(None, failed=True)
    def events(call):
        yield _init()
        if source != "result":
            yield observation
        yield _result(permission_denials=[{"tool_name": "Read"}] if source == "result" else [])
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    _assert_completed_provider_run(run, cell)
    attempt = run.attempts[0]
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    inspected = parse_cli_log(trace)
    assert not inspected["complete"] and inspected["tool_calls"] == []
    recorded = [item["event"] for item in inspected["events"]]
    if source == "result":
        assert recorded[-1]["permission_denials"] == [{"tool_name": "Read"}]
    else:
        assert observation in recorded


@pytest.mark.parametrize("ending,expected", [
    ("provider", "provider"), ("nonzero", "transport"), ("timeout", "timeout"),
    ("cancelled", "cancelled"), ("missing", "provider"), ("schema", "schema"),
    ("invalid_event", "provider"), ("material", "policy_violation"),
])
@pytest.mark.fake_run
def test_normal_denial_does_not_mask_later_execution_failure(tmp_path, ending, expected):
    prefix = [_init(), _tool_use("denied", file_path="outside"), _denial("denied"),
              _tool_result("denied", failed=True)]
    def events(call):
        yield from prefix
        if ending == "timeout":
            raise subprocess.TimeoutExpired(call["argv"], 1)
        if ending == "cancelled":
            raw = "\n".join(json.dumps(item) for item in prefix)
            raise claude.CliProcessInterrupted(returncode=-15, output=raw, stderr="cancelled",
                stdout_bytes=raw.encode(), stderr_bytes=b"cancelled")
        if ending == "missing":
            return
        if ending == "invalid_event":
            yield "invalid-json-line"
            return
        if ending == "material":
            (call["cwd"] / "materials/source").write_bytes(b"changed")
        yield _result(**({"subtype": "error_during_execution", "is_error": True} if ending == "provider" else
                         {"structured_output": {"wrong": 1}} if ending == "schema" else {}))
        if ending == "nonzero":
            raise subprocess.CalledProcessError(1, call["argv"])
    run, cell = _run(_environment(tmp_path, material=b"protected"), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.failure_class == expected and not run.outputs
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert _denial("denied") in [row["event"] for row in parse_cli_log(trace)["events"]]
    assert "permission_denied" in trace["stdout"]
    if ending != "material":
        assert "policy_refusal_reason" not in trace


@pytest.mark.parametrize("outcome", ["denied", "error", "completed", "missing", "missing_id",
                                     "wrong_id", "duplicate_request", "conflicting_results"])
@pytest.mark.fake_run
def test_tool_event_outcomes_do_not_override_a_valid_final_result(tmp_path, outcome):
    continued = []
    def events(call):
        yield _init(("Read",))
        yield _tool_use("extra", "Bash", command="printf unapproved")
        continued.append(True)
        if outcome == "duplicate_request":
            yield _tool_use("extra", "Bash", command="printf another")
        if outcome == "denied":
            yield _denial("extra", "Bash")
        if outcome != "missing":
            yield _tool_result(None if outcome == "missing_id" else "other" if outcome == "wrong_id" else "extra",
                               failed=outcome in {"denied", "error"})
        if outcome == "conflicting_results":
            yield _tool_result("extra", failed=True)
        yield _tool_use("allowed", file_path="material")
        yield _tool_result("allowed")
        yield _result()
    run, cell = _run(_environment(tmp_path, tools=("read",)), tmp_path, events)
    assert continued == [True]
    attempt = run.attempts[0]
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    _assert_completed_provider_run(run, cell)
    assert "tool_log" not in trace and "native_tool_events" not in trace
    assert "policy_refusal_reason" not in trace
    inspected = parse_cli_log(trace)
    assert any(item["tool_call_id"] == "extra" for item in inspected["tool_calls"])
    if outcome not in {"completed", "denied", "error"}:
        assert inspected["complete"] is False


@pytest.mark.fake_run
@pytest.mark.parametrize("verdict", ["non_pass", "blocked"])
def test_valid_business_verdict_after_denial_is_not_a_technical_retry(tmp_path, verdict):
    invocations = []
    def events(call):
        invocations.append(True)
        yield _init()
        yield _tool_use("denied", file_path="outside")
        yield _denial("denied")
        yield _tool_result("denied", failed=True)
        yield _result(structured_output={"value": verdict})
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": verdict}
    assert invocations == [True] and len(run.attempts) == 1


@pytest.mark.fake_run
def test_command_permission_words_are_not_permission_evidence(tmp_path):
    def events(call):
        yield _init()
        yield {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
            "tool_use_id": "test", "content": "AssertionError: expected 'permission denied'"}]}}
        yield _result()
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}


@pytest.mark.fake_run
@pytest.mark.parametrize("failure,failure_class,code,stage,message", [
    ("host_denied", "authorization", None, None, "host denied read"),
    ("hash_mismatch", "schema", "ADAPTER_REQUEST_INVALID", "material_preparation", "authorized input hash mismatch"),
    ("missing_cli_option", "dependency_unavailable", "ADAPTER_BINDING_UNAVAILABLE", "cli_preflight",
     "configured Claude CLI lacks a required option"),
    ("cli_unavailable", "dependency_unavailable", "ADAPTER_BINDING_UNAVAILABLE", "cli_preflight", "CLI unavailable"),
], ids=["host_denied", "hash_mismatch", "missing_cli_option", "cli_unavailable"])
def test_preparation_failures_never_enter_cli(tmp_path, monkeypatch, failure, failure_class, code, stage, message):
    """Each injected preparation fault is reached, attributed to its own stage, and stops before the Provider."""
    from agent_runtime.execution.execution_module_invocation import _AttemptExecutionHost
    env = _environment(tmp_path, material=b"original")
    entered, reached = [], []
    if failure == "host_denied":
        def denied(*_):
            reached.append(failure)
            raise PermissionError("host denied read")
        monkeypatch.setattr(_AttemptExecutionHost, "read_authorized_input", denied)
    elif failure == "hash_mismatch":
        def wrong(*_):
            reached.append(failure)
            return b"wrong"
        monkeypatch.setattr(_AttemptExecutionHost, "read_authorized_input", wrong)
    else:
        real_run = claude.run_cli_process
        def unavailable(**fields):
            if fields["argv"][-1] == "--help":
                reached.append(failure)
                if failure == "cli_unavailable":
                    raise OSError("CLI unavailable")
                result = subprocess.CompletedProcess(fields["argv"], 0, stdout="--model", stderr="")
                result.stdout_bytes, result.stderr_bytes = b"--model", b""
                return result
            return real_run(**fields)
        monkeypatch.setattr(claude, "run_cli_process", unavailable)  # The owned preflight runner.
    def events(call):
        entered.append(True)
        yield _result()
    run, cell = _run(env, tmp_path, events)
    attempt = run.attempts[0]
    assert reached == [failure] and not entered and not run.outputs
    assert attempt.status == "failed" and attempt.failure_class == failure_class
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    assert detail.get("failure_code") == code and message in detail["message"]
    if stage is not None:
        trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
        assert trace["stage"] == stage


@pytest.mark.fake_run
@pytest.mark.parametrize("final,failure_class,code", [
    (None, "provider", "claude_cli_result_missing_or_invalid"),
    ({"type": "result", "subtype": "error", "is_error": True}, "provider", "claude_cli_error_result"),
    (_result(structured_output={"wrong": 1}), "schema", "ADAPTER_OUTPUT_INVALID"),
], ids=["None", "final1", "final2"])
def test_missing_error_or_invalid_result_cannot_succeed(tmp_path, final, failure_class, code):
    """The Provider call is reached and its exit recorded; the final result decides the specific failure."""
    entered = []
    def events(call):
        entered.append(True)
        yield _init()
        if final is not None:
            yield final
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert entered == [True] and attempt.status == "failed" and not run.outputs
    assert attempt.failure_class == failure_class
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    assert detail["failure_code"] == code
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert trace["exit_code"] == 0 and trace["cli_version"] == SYNTHETIC_CLAUDE_VERSION


@pytest.mark.parametrize("with_init", [True, False])
@pytest.mark.parametrize("fields,expected", [({"result":"usage limit reached"}, "quota"),
    ({"api_error_status":401}, "authentication"), ({"api_error_status":403}, "authorization"),
    ({"api_error_status":429}, "rate_limit")])
@pytest.mark.fake_run
def test_provider_error_retains_its_category(tmp_path, fields, expected, with_init):
    def events(call):
        if with_init:
            yield _init()
        yield _result(subtype="error_during_execution", is_error=True, **fields)
    run, _ = _run(_environment(tmp_path), tmp_path, events)
    assert run.attempts[0].failure_class == expected


@pytest.mark.fake_run
@pytest.mark.parametrize("failure", ["timeout", "invalid_event", "output_limit"])
def test_failure_preserves_captured_public_output_and_source_usage(tmp_path, failure):
    source = {"type": "assistant", "message": {"id": "message_partial", "model": "claude-opus-5",
        "usage": {"input_tokens": 19, "output_tokens": 2}, "content": [{"type":"text","text":"partial output"}]}}
    def events(call):
        yield _init()
        yield source
        if failure == "timeout":
            raise subprocess.TimeoutExpired(call["argv"], 1)
        if failure == "output_limit":
            raise subprocess.CalledProcessError(125, call["argv"], stderr="output limit reached")
        yield "invalid-json-line"
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.status == "failed" and not run.outputs
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert json.dumps(source) in trace["stdout"]
    assert trace["process_output_complete"] is (failure == "invalid_event")
    assert attempt.usage.input_tokens is None and attempt.usage.output_tokens is None
    if failure == "invalid_event":
        assert "invalid-json-line" in trace["stdout"]


@pytest.mark.fake_run
@pytest.mark.parametrize("bad_event", ["invalid-json", _result()])
def test_terminal_validation_is_separate_from_unrelated_log_errors(tmp_path, bad_event):
    def events(call):
        yield _init()
        yield _result()
        yield bad_event
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    if isinstance(bad_event, str):
        _assert_completed_provider_run(run, cell)
    else:
        assert attempt.status == "failed" and not run.outputs
        assert attempt.failure_class == "provider"
        detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
        assert detail["failure_code"] == "claude_cli_result_missing_or_invalid"
    assert attempt.usage.input_tokens == 7 and attempt.usage.output_tokens == 3


@pytest.mark.fake_run
@pytest.mark.parametrize("reason,exit_code,cleanup", [
    ("observer_stopped", 0, False), ("stream_error", 0, False), ("output_limit", 0, False), ("timeout", 0, False),
    ("cleanup_error", 0, True), ("cleanup_error", None, True), ("timeout", 0, True),
], ids=["observer_stopped-0", "stream_error-0", "output_limit-0", "timeout-0",
        "cleanup_error-0", "cleanup_error-None", "timeout-0-cleanup_failed"])
def test_runtime_capture_failure_preserves_real_exit_and_reason(tmp_path, reason, exit_code, cleanup):
    """The immutable provider trace keeps the real exit, stop reason and streams in every case.

    An ordinary capture failure is the final diagnostic. When process cleanup itself failed,
    the later handoff diagnostic reports claude_cli_cleanup_failed with no transport exit code
    of its own, while the earlier trace still holds the process facts.
    """
    extra = {"cleanup_error": "cleanup diagnostic"} if cleanup else {}
    def events(call):
        yield _init()
        yield _result()
        if reason == "timeout":
            raise CliProcessTimeout(call["argv"], 1, returncode=exit_code, output="captured provider output",
                stderr="actual stderr", stream_error="additional stream diagnostic", **extra)
        raise CliProcessError(exit_code, call["argv"], stop_reason=reason, message="Runtime capture stopped",
            output="captured provider output", stderr="actual stderr", stream_error="additional stream diagnostic",
            **extra)
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.status == "failed" and not run.outputs
    assert attempt.usage.input_tokens == 7 and attempt.usage.output_tokens == 3
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert trace["stage"] == "provider_invocation"
    assert trace["exit_code"] == exit_code and trace["stop_reason"] == reason
    assert trace["process_output_complete"] is False and trace.get("cleanup_error") == extra.get("cleanup_error")
    assert trace["stream_error"] == "additional stream diagnostic"
    assert trace["stdout"] == "captured provider output" and trace["stderr"] == "actual stderr"
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    if cleanup:
        assert attempt.failure_class == "transport" and detail["failure_code"] == "claude_cli_cleanup_failed"
        assert detail["transport_exit_code"] is None
    elif reason == "timeout":
        assert attempt.failure_class == "timeout" and detail["failure_code"] == "claude_cli_timeout"
        assert detail["transport_exit_code"] == exit_code
    else:
        assert attempt.failure_class == "transport" and detail["failure_code"] == "claude_cli_process_failed"
        assert detail["transport_exit_code"] == exit_code


@pytest.mark.fake_run
@pytest.mark.parametrize("primary_reason", ["output_limit", "stream_error", "cleanup_error"])
def test_capture_primary_reason_is_not_replaced_by_late_invalid_event(tmp_path, primary_reason):
    def events(call):
        yield _init()
        yield _result()
        assert call["on_stdout_line"]("late-invalid-json") is True
        raise CliProcessError(0, call["argv"], stop_reason=primary_reason, message="primary capture failure",
            output="late-invalid-json", stderr="provider diagnostic")
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.failure_class == "transport" and not run.outputs
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert trace["stop_reason"] == primary_reason and "event_error" not in trace
    assert "late-invalid-json" in trace["stdout"]
    assert trace["exit_code"] == 0 and trace["stderr"] == "provider diagnostic"
    assert attempt.usage.input_tokens == 7


@pytest.mark.parametrize("usage", ["bad", [], {"input_tokens":"7"}, {"output_tokens":-1},
    {"input_tokens":True}, {"cache_read_input_tokens":1.5}])
@pytest.mark.fake_run
@pytest.mark.parametrize("status", [None, 401, 403, 429])
def test_malformed_usage_never_overwrites_result_or_loses_evidence(tmp_path, usage, status):
    def events(call):
        yield _init()
        yield _result(usage=usage, api_error_status=status, is_error=status is not None,
                      subtype="success" if status is None else "error_during_execution")
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.failure_class == {None:"schema", 401:"authentication", 403:"authorization", 429:"rate_limit"}[status]
    assert not run.outputs
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert trace["result"]["usage"] == usage


@pytest.mark.fake_run
def test_unknown_usage_remains_unknown(tmp_path):
    def events(call):
        yield _init()
        yield _result(usage=None)
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    _assert_completed_provider_run(run, cell)
    assert run.attempts[0].usage.input_tokens is None and run.attempts[0].usage.output_tokens is None


@pytest.mark.fake_run
@pytest.mark.parametrize("reported, expected", [
    # Claude's input_tokens is ordinary input; Runtime input_tokens is the cache-inclusive total.
    ({"input_tokens": 2, "output_tokens": 5, "cache_read_input_tokens": 11, "cache_creation_input_tokens": 13},
     (26, 2, 11, 13, 5)),
    ({"input_tokens": 2, "output_tokens": 5, "cache_read_input_tokens": 11}, (None, 2, 11, None, 5)),
    ({"input_tokens": 2, "output_tokens": 5}, (None, 2, None, None, 5)),
    ({"output_tokens": 5, "cache_read_input_tokens": 11, "cache_creation_input_tokens": 13}, (None, None, 11, 13, 5)),
    ({"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
     (0, 0, 0, 0, 0)),
], ids=["all_known", "cache_creation_missing", "caches_missing", "ordinary_missing", "zeros"])
def test_valid_usage_keeps_ordinary_input_beside_the_cache_inclusive_total(tmp_path, reported, expected):
    def events(call):
        yield _init()
        yield _result(usage=reported)
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    _assert_completed_provider_run(run, cell)
    usage = run.attempts[0].usage
    assert (usage.input_tokens, usage.ordinary_input_tokens, usage.cache_read_tokens,
            usage.cache_creation_tokens, usage.output_tokens) == expected


@pytest.mark.fake_run
@pytest.mark.parametrize("ordinary", [True, -1, "2", 1.5])
def test_invalid_ordinary_input_is_unknown_and_fails_the_usage_check(tmp_path, ordinary):
    def events(call):
        yield _init()
        yield _result(usage={"input_tokens": ordinary, "output_tokens": 5,
                             "cache_read_input_tokens": 11, "cache_creation_input_tokens": 13})
    run, _ = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.failure_class == "schema" and not run.outputs
    assert (attempt.usage.input_tokens, attempt.usage.ordinary_input_tokens) == (None, None)
    assert (attempt.usage.cache_read_tokens, attempt.usage.cache_creation_tokens) == (11, 13)


@pytest.mark.fake_run
def test_failed_attempt_keeps_the_reported_input_breakdown(tmp_path):
    def events(call):
        yield _init()
        yield _result(usage={"input_tokens": 2, "output_tokens": 5, "cache_read_input_tokens": 11,
                             "cache_creation_input_tokens": 13},
                      api_error_status=429, is_error=True, subtype="error_during_execution")
    run, _ = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.status == "failed" and attempt.failure_class == "rate_limit"
    assert (attempt.usage.input_tokens, attempt.usage.ordinary_input_tokens, attempt.usage.cache_read_tokens,
            attempt.usage.cache_creation_tokens) == (26, 2, 11, 13)


@pytest.mark.parametrize("value", ["--other-option", "model with spaces", "x\nsecret", "", None, 1])
def test_model_selection_rejects_non_model_input(value):
    with pytest.raises(ValueError):
        validate_model_id("model_id", value)


def test_native_module_profile_tools_are_not_gateway_operations(tmp_path):
    from agent_runtime.execution.execution_module_invocation import _assert_admitted_test_evaluation_profile
    env = _environment(tmp_path)
    module, profile = env[0].module, env[0].execution_profile
    assert not set(profile.tool_policy).intersection(module.declared_operation_ids)
    assert _assert_admitted_test_evaluation_profile(module, profile) is None


# One row per distinct current branch of the ordinary (non-extension) Claude call:
# tools, workspace, network and output mode. Empty tools are covered by the tool_free test below.
_FIELD_ROWS = {
    "read_only": (("read",), "none", "provider_managed", "prompt_only_json"),
    "search_only": (("search",), "none", "provider_managed", "native_structured_output"),
    "draft_without_edit": (("read", "search"), "own_draft_read_write", "provider_managed", "prompt_only_json"),
    "draft_with_edit": (("edit", "read", "search"), "own_draft_read_write", "provider_managed", "native_structured_output"),
    "shell_provider_managed": (("read", "search", "shell"), "none", "provider_managed", "native_structured_output"),
    "shell_saved_strict": (("read", "shell"), "own_draft_read_write", "denied", "prompt_only_json"),
}


@pytest.mark.fake_run
@pytest.mark.parametrize("row", sorted(_FIELD_ROWS))
def test_claude_adapter_executes_each_agent_field_combination(tmp_path, row):
    """Actual kernel -> Adapter -> runner for each current field branch; not a command-builder-only claim."""
    tools, workspace, network, output_mode = _FIELD_ROWS[row]
    calls = []
    native = output_mode == "native_structured_output"
    env = _environment(tmp_path, tools=tools, workspace=workspace, network=network, output_mode=output_mode,
                       material=b"authorized evidence")
    expected = _native(env[0].execution_profile)
    shell = "shell" in tools
    python_roots = [str(Path(value).resolve()) for value in dict.fromkeys((sys.prefix, sys.base_prefix))]
    def events(call):
        calls.append(call)
        argv = call["argv"]
        assert argv[argv.index("--tools")+1] == ",".join(expected)
        allowed = [tool for tool in expected if tool in {"Read", "Grep", "Glob"}]
        if "edit" in tools:
            allowed.append("Edit(./scratch/**)")
        assert _allowed(argv) == allowed  # Bash is offered as a tool but never preapproved without a permit.
        assert argv[argv.index("--permission-mode")+1] == "dontAsk" and "--restricted" not in argv
        assert ("--json-schema" in argv) is native
        assert json.loads(argv[argv.index("--mcp-config")+1]) == {"mcpServers": {}}
        assert "--max-turns" not in argv and "--max-budget-usd" not in argv
        assert call["cwd"].name == "work"  # The ordinary Attempt runs in work/, beside materials/.
        assert (call["cwd"] / "scratch").is_dir() is (workspace == "own_draft_read_write")
        assert (call["cwd"] / "materials/source").read_bytes() == b"authorized evidence"
        settings = json.loads(argv[argv.index("--settings")+1])
        permissions = settings["permissions"]
        assert permissions["blockReadsOutsideWorkingDirectories"] and permissions["blockWritesOutsideWorkingDirectories"]
        assert permissions["additionalDirectories"] == (python_roots if shell else [])
        assert permissions.get("deny") == (["Edit(./materials/**)"] if "edit" in tools else None)
        if shell:  # Shell-capable calls use the Python running Runtime first on PATH.
            assert call["environment"]["PATH"].split(os.pathsep)[0] == str(Path(sys.executable).absolute().parent)
        if network == "denied":  # An explicitly saved strict Profile is passed to Claude unchanged.
            sandbox = settings["sandbox"]
            assert sandbox["enabled"] is True and sandbox["failIfUnavailable"] is True
            assert sandbox["filesystem"] == {"allowWrite": [call["environment"]["TMPDIR"]]}
            assert sandbox["network"]["allowedDomains"] == []
        else:
            assert "sandbox" not in settings
        initialization = _init(tuple(expected))
        if not native:
            initialization["tools"].remove("StructuredOutput")
        yield initialization
        terminal = _result()
        if not native:
            terminal.pop("structured_output")
            terminal["result"] = '{"value":"checked"}'
        yield terminal
    run, cell = _run(env, tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}
    assert len(calls) == 1
    trace = json.loads(cell.read_bytes(run.attempts[0].provider_trace_ref, run.attempts[0].provider_trace_sha256))
    assert (trace["executor_adapter_id"], trace["executor_adapter_revision"]) == ("claude_cli_adapter", "v4")
    assert hashlib.sha256(trace["actual_prompt"].encode()).hexdigest() == trace["prompt_envelope_sha256"]
    assert trace["execution_profile_ref"] == run.variants[0].execution_profile_ref
    effective = trace["effective_configuration"]
    assert effective["tools"] == expected and effective["permission_mode"] == "dontAsk"
    assert effective["write_directories"] == ([str(calls[0]["cwd"] / "scratch")] if "edit" in tools else [])
    assert effective["command_scope"] == ("native_read_only_plus_permits" if shell else "none")
    assert "tool_log" not in trace
    assert parse_cli_log(trace)["complete"]


@pytest.mark.fake_run
@pytest.mark.parametrize("output_mode", ["prompt_only_json", "native_structured_output"])
def test_tool_free_uses_same_adapter_with_explicit_empty_tools(tmp_path, output_mode):
    native = output_mode == "native_structured_output"
    def events(call):
        assert call["argv"][call["argv"].index("--tools") + 1] == ""
        assert _allowed(call["argv"]) == [""]
        # A tool-free call gets no file permissions block and no task directories.
        assert json.loads(call["argv"][call["argv"].index("--settings") + 1]) == {}
        assert "--add-dir" not in call["argv"] and ("--json-schema" in call["argv"]) is native
        init = _init(())
        if not native:
            init["tools"] = []
        yield init
        yield _result() if native else _result(structured_output=None, result='{"value":"checked"}')
    run, cell = _run(_environment(tmp_path, tools=(), mode="tool_free", workspace="none", output_mode=output_mode),
                     tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}


@pytest.mark.fake_run
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_model_and_effort_preserve_permissions_and_actual_selection(tmp_path, effort):
    def events(call):
        argv = call["argv"]
        assert argv[argv.index("--model")+1] == "concrete-other-model"
        assert argv[argv.index("--effort")+1] == effort
        # The model and effort never widen the recorded tools or their preapproval.
        assert argv[argv.index("--tools")+1] == "Grep,Glob,Read"
        assert _allowed(argv) == ["Grep", "Glob", "Read"]
        assert call["timeout_seconds"] == 60
        yield {**_init(("Grep", "Glob", "Read")), "model": "concrete-other-model"}
        yield {"type": "assistant", "message": {"model": "concrete-other-model", "content": []}}
        yield _result()
    run, cell = _run(_environment(tmp_path, tools=("search", "read"), model="concrete-other-model", effort=effort), tmp_path, events)
    _assert_completed_provider_run(run, cell)


def _count_processes(monkeypatch):
    """Record every process the Runtime creates (preflight or Provider) during this test."""
    from agent_runtime.invocation import invocation_process_execution as processes
    created = []
    create = processes.HOST_PLATFORM.create_cli_process
    def counting(argv, **kwargs):
        created.append(list(argv))
        return create(argv, **kwargs)
    monkeypatch.setattr(processes.HOST_PLATFORM, "create_cli_process", counting)
    return created


@pytest.mark.fake_run
@pytest.mark.parametrize("effort", ["none", "ultra", "--tools", "", "high;echo injected"])
def test_invalid_effort_never_launches_provider(tmp_path, monkeypatch, effort):
    def unused(call):
        pytest.fail("invalid effort reached Provider")
        yield
    try:
        env = _environment(tmp_path, effort=effort)
    except ValueError:
        assert effort in {"", "--tools", "high;echo injected"}  # Rejected when the Profile is compiled.
        return
    created = _count_processes(monkeypatch)
    run, cell = _run(env, tmp_path, unused, monkeypatch)
    attempt = run.attempts[0]
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    assert attempt.status == "failed" and not run.outputs
    assert "unsupported CLI effort" in detail["message"]
    assert created == [] and not (tmp_path / "attempts").exists()  # No preflight, Provider or workspace.


@pytest.mark.parametrize("binding", [("claude_cli_adapter", "v1"), ("claude_cli_adapter", "v2"), ("claude_cli_native_tools_executor", "v2"),
                                     ("unknown", "v1"), ["claude_cli_adapter", "v2"]])
def test_unknown_adapter_identity_rejected_before_resource_resolution(tmp_path, binding):
    with pytest.raises(ValueError, match="execution Profile"):
        claude.ClaudeAdapter(release_registry=None, artifact_host=None,
            workspace_root=tmp_path, cli_path=tmp_path / "missing", adapter_binding=binding)


@pytest.mark.fake_run
@pytest.mark.parametrize("tools", [("unknown_tool",), ("Read",), ("read", "read"), ("Bash(*)",)])
def test_unsupported_or_malformed_tools_never_reach_provider(tmp_path, monkeypatch, tools):
    def unused(call):
        pytest.fail("unrecognized tools reached Provider")
        yield
    if tools in {("Read",), ("read", "read"), ("Bash(*)",)}:
        with pytest.raises(ValueError):
            _environment(tmp_path, tools=tools)
        return
    # An unknown logical name is a callback tool, never a native one; without a trusted
    # self-test factory it is refused before any process or workspace.
    created = _count_processes(monkeypatch)
    run, cell = _run(_environment(tmp_path, tools=tools), tmp_path, unused, monkeypatch)
    attempt = run.attempts[0]
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    assert attempt.status == "failed" and attempt.failure_class == "authorization"
    assert "callbacks require a trusted self-test Factory" in detail["message"]
    assert created == [] and not (tmp_path / "attempts").exists()


@pytest.mark.parametrize("detail", ["Exit code 2\ncommand usage error",
                                       "Exit code 1\napplication text: operation not permitted"])
@pytest.mark.fake_run
def test_shell_error_text_does_not_invent_a_policy_denial(tmp_path, detail):
    def events(call):
        yield _init()
        yield {"type": "assistant", "message": {"content": [{
            "type": "tool_use", "id": "shell_error", "name": "Bash",
            "input": {"command": "exit 2"}}]}}
        yield {"type": "user", "message": {"content": [{
            "type": "tool_result", "tool_use_id": "shell_error",
            "content": detail, "is_error": True}]}, "tool_use_result": "Error: " + detail}
        yield _result(permission_denials=[])
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    _assert_completed_provider_run(run, cell)
    attempt = run.attempts[0]
    assert attempt.failure_class is None
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    inspected = parse_cli_log(trace)
    assert inspected["complete"] is True
    call = inspected["tool_calls"][0]
    assert call["status"] == "failed"
    assert call["response"]["tool_result"]["content"] == detail
    assert trace["result"]["permission_denials"] == []


def test_schema_mode_mismatch_cannot_create_cli_argv(tmp_path, monkeypatch):
    env = _environment(tmp_path)
    adapter = claude.ClaudeAdapter(release_registry=env[1], artifact_host=env[2],
        workspace_root=tmp_path / "attempts", cli_path=_fake_cli(tmp_path, monkeypatch))
    with pytest.raises(ValueError, match="schema presence"):
        adapter.build_command(profile=env[0].execution_profile, settings={}, output_schema=None)


@pytest.mark.fake_run
def test_claude_projection_failure_prevents_provider_invocation(tmp_path, monkeypatch):
    """Canonical projection refusal precedes CLI preflight and Provider calls."""
    env = _environment(tmp_path, tools=(), mode="tool_free", workspace="none",
                       output_schema_document=_unsupported_positional_output_schema())
    calls = []
    def no_process(*args, **kwargs):
        pytest.fail("schema projection failure must precede any CLI process")
    monkeypatch.setattr(claude.subprocess, "run", no_process)
    monkeypatch.setattr(claude, "run_cli_process", no_process)  # The owned preflight runner.
    def unused_provider(call):
        calls.append(call)
        pytest.fail("unsupported schema reached Provider")
        yield
    run, artifacts = _run(env, tmp_path, unused_provider)
    attempt = run.attempts[0]
    assert calls == [] and run.outputs == ()
    assert attempt.status == "failed" and attempt.failure_class == "schema"
    detail = json.loads(artifacts.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    assert detail["failure_code"] == "native_output_schema_projection_unsupported"
    assert detail["retryable"] is False
    assert not (tmp_path / "attempts").exists()


@pytest.mark.parametrize("binding", [("claude_cli_adapter", "v1"), ("claude_cli_adapter", "v2"), ("claude_cli_native_tools_executor", "v2")])
def test_historical_claude_profile_is_readable_but_not_silently_reexecuted(tmp_path, monkeypatch, binding):
    env = _environment(tmp_path, material=b"historical material", binding=binding)
    original = env[0].execution_profile.as_dict()
    def unused(**_):
        pytest.fail("historical Profile reached a new Provider invocation")
    with pytest.raises(ValueError, match="historical records"):
        claude.ClaudeAdapter(release_registry=env[1], artifact_host=env[2], workspace_root=tmp_path,
                            cli_path=_fake_cli(tmp_path, monkeypatch), process_runner=unused, adapter_binding=binding)
    restored = env[1].get_execution_profile(env[0].execution_profile.release_ref, env[0].execution_profile.release_sha256)
    assert restored.as_dict() == original
    assert env[0].execution_profile.as_dict() == original


@pytest.mark.fake_run
@pytest.mark.parametrize("change", [{"tools": ()}, {"workspace": "none"}])
def test_old_v2_identity_cannot_claim_new_capabilities(tmp_path, change):
    def unused(call):
        pytest.fail("unsupported historical capability reached Provider")
        yield
    with pytest.raises(ValueError, match="historical records"):
        _run(_environment(tmp_path, binding=("claude_cli_native_tools_executor", "v2"), **change), tmp_path, unused)


def test_cli_adapter_import_does_not_require_sdk():
    import sys
    source = str(Path(__file__).resolve().parents[1] / "src")
    code = (
        "import sys; sys.path.insert(0, " + repr(source) + ")\n"
        "class NoSDK:\n"
        " def find_spec(self, fullname, path=None, target=None):\n"
        "  if fullname.startswith('claude_agent_sdk'): raise AssertionError('SDK imported')\n"
        "sys.meta_path.insert(0, NoSDK())\n"
        "from agent_runtime.invocation.invocation_claude_cli_execution import ClaudeAdapter\n"
    )
    subprocess.run([sys.executable, "-I", "-B", "-c", code], check=True, capture_output=True)


# Live Claude runs on the macOS AB host. The strict cases are explicitly saved denied+Shell
# Profiles: Runtime passes that saved choice to Claude, and Claude enforces or reports it.
_AB_LIVE = pytest.mark.skipif(
    os.environ.get("RUN_PROVIDER_INTEGRATION") != "1" or sys.platform != "darwin",
    reason="unverified: explicit live Claude run on the macOS AB host (its commands use macOS paths); set "
           "RUN_PROVIDER_INTEGRATION=1, AGENT_RUNTIME_TEST_AB_WORKSPACE and AGENT_RUNTIME_TEST_CLAUDE_BIN")
_LIVE = pytest.mark.skipif(
    os.environ.get("RUN_PROVIDER_INTEGRATION") != "1",
    reason="unverified: explicit live Claude run; set RUN_PROVIDER_INTEGRATION=1, "
           "AGENT_RUNTIME_TEST_AB_WORKSPACE and AGENT_RUNTIME_TEST_CLAUDE_BIN")


@pytest.mark.real_run
@_AB_LIVE
def test_live_claude_native_tools_in_ab(tmp_path):
    root = Path(os.environ["AGENT_RUNTIME_TEST_AB_WORKSPACE"]).resolve(strict=True)
    cli_path = Path(os.environ["AGENT_RUNTIME_TEST_CLAUDE_BIN"]).resolve(strict=True)
    compiled, registry, cell, request, authority = _environment(tmp_path)
    prompt = _evaluation_prompt(cell, compiled, suffix="ab_tools_live", execution_specific_instructions=(
        "Use Bash to create a scratch subdirectory inside the current writable draft, then create scratch/test_check.py containing def test_ok(): assert 2+2==4. "
        "Create scratch/pytest.ini with [pytest] and addopts=-p no:cacheprovider. "
        "Use Read to inspect the test and Grep to find its assertion. "
        "Use head to view the test, copy it to scratch/changed.py, and append a comment there. "
        "From the initial workspace run the Git check as: "
        "(cd ./scratch && git diff --no-index -- test_check.py changed.py). "
        "This tests a real subdirectory change in a subshell, not a redundant cd back to the current directory; "
        "do not omit cd or substitute git -C. "
        "Exit 1 with a real diff means success, not a failure. Return to the initial workspace for pytest. "
        "Run /opt/miniconda3/bin/python -B -m pytest -q --rootdir=./scratch -c ./scratch/pytest.ini ./scratch/test_check.py. "
        "Then return the registered output with value='tested' only if the test passed. "
        "Use only the current workspace and exact dependency shown; do not access other projects."
    ))
    request = _evaluation_request(compiled, prompt, suffix="ab_tools_live")
    authority, _ = _evaluation_authority(registry, request)
    adapter = claude.ClaudeAdapter(
        release_registry=registry, artifact_host=cell, workspace_root=root,
        cli_path=cli_path, read_only_dependencies=(Path("/opt/miniconda3"), Path("/Library/Developer/CommandLineTools/usr")),
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    run = run_module(request, release_registry=registry, adapters=adapters, artifact_host=cell,
                     ledger=InMemoryModuleExecutionLedger(), authority=authority)
    attempt = run.attempts[0]
    if attempt.provider_trace_ref:
        (root / "observed_trace.json").write_bytes(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    if attempt.failure_detail_ref:
        (root / "failure_detail.json").write_bytes(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    output = _assert_completed_provider_run(run, cell)
    assert output["value"] == "tested"
    attempt = run.attempts[0]
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    calls = parse_cli_log(trace)["tool_calls"]
    used = {call["tool_name"] for call in calls}
    assert {"Read", "Grep", "Bash"} <= used
    shell_commands = [call["request"]["command"] for call in calls if call["tool_name"] == "Bash"]
    assert any("head " in command for command in shell_commands)
    assert any("git diff" in command for command in shell_commands)
    git_calls = [call for call in calls if call["tool_name"] == "Bash"
                 and "git diff" in call["request"]["command"] and "cd " in call["request"]["command"]]
    assert git_calls
    assert any("diff --git" in str(call["response"]) and "@@ " in str(call["response"]) for call in git_calls)
    print(json.dumps({"attempt": attempt.attempt_id, "tools": sorted(used), "cli_version": trace["cli_version"]}))


@pytest.mark.real_run
@_AB_LIVE
@pytest.mark.parametrize("capture_exit", [False, True])
def test_live_claude_rejects_ab_sibling_read(tmp_path, capture_exit):
    """Explicit saved strict case (network denied with Shell) on the macOS AB host, not an ordinary-call default."""
    root = Path(os.environ["AGENT_RUNTIME_TEST_AB_WORKSPACE"]).resolve(strict=True)
    sibling = root / "outside_attempt.txt"
    sibling.write_text("outside_attempt_sentinel")
    compiled, registry, cell, _, _ = _environment(tmp_path, network="denied")  # Explicitly saved strict Profile.
    command = f"/bin/cat {sibling}" + (" || true" if capture_exit else "")
    prompt = _evaluation_prompt(cell, compiled, suffix="ab_denied_live", execution_specific_instructions=(
        f"Use Bash once to run this exact command: {command}\nReport the actual result. "
        "Do not retry, read credentials or request unsandboxed access. Return value='blocked' if refused."
    ))
    request = _evaluation_request(compiled, prompt, suffix="ab_denied_live")
    authority, _ = _evaluation_authority(registry, request)
    adapter = claude.ClaudeAdapter(
        release_registry=registry, artifact_host=cell, workspace_root=root,
        cli_path=os.environ["AGENT_RUNTIME_TEST_CLAUDE_BIN"],
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    run = run_module(request, release_registry=registry, adapters=adapters, artifact_host=cell,
                     ledger=InMemoryModuleExecutionLedger(), authority=authority)
    attempt = run.attempts[0]
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    (root / ("captured_denial_trace.json" if capture_exit else "denied_trace.json")).write_text(json.dumps(trace, ensure_ascii=False, indent=2))
    assert _assert_completed_provider_run(run, cell) == {"value": "blocked"}
    assert "policy_refusal_reason" not in trace
    results = [call["response"]["tool_result"] for call in parse_cli_log(trace)["tool_calls"]
               if isinstance(call["response"], dict) and "tool_result" in call["response"]]
    assert any("not permitted" in str(e["content"]).lower() for e in results)
    assert all("outside_attempt_sentinel" not in str(e["content"]) for e in results)


@pytest.mark.real_run
@_AB_LIVE
@pytest.mark.parametrize("boundary", ["symlink_read", "material_write", "dependency_write", "network"])
def test_live_claude_resource_boundaries_in_ab(tmp_path, boundary):
    """Explicit saved strict case (network denied with Shell) on the macOS AB host.

    Verifies actual resource effects, independently of how a shell reports failure. This is
    not an ordinary-call default: ordinary calls use provider_managed without a sandbox.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from tempfile import TemporaryDirectory
    from threading import Thread

    root = Path(os.environ["AGENT_RUNTIME_TEST_AB_WORKSPACE"]).resolve(strict=True)
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"outside_network_sentinel")
        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with TemporaryDirectory(prefix="native-boundary-", dir=root) as fixture:
            fixture_root = Path(fixture).resolve()
            outside = fixture_root / "outside"
            outside.write_text("outside_link_sentinel")
            dependency = fixture_root / "dependency"
            dependency.mkdir()
            dependency_file = dependency / "fixture"
            dependency_file.write_text("original_dependency")
            commands = {
                "symlink_read": f"/bin/ln -s {outside} link; /bin/cat link",
                "material_write": "printf changed > ../materials/source",
                "dependency_write": f"printf changed > {dependency_file}",
                "network": f"printf 'GET /probe HTTP/1.0\\r\\n\\r\\n' | /usr/bin/nc -v -w 2 127.0.0.1 {server.server_port}",
            }
            command = commands[boundary]
            env = _environment(tmp_path, material=b"original_material", network="denied", instructions=(
                "This is an authorized isolation test using disposable fixtures. Use Bash once to execute "
                f"this exact command, even if the sandbox will deny it: {command}\n"
                "Do not retry or request unsandboxed access. Return value='blocked' if denied, otherwise 'executed'."
            ))
            _, registry, cell, request, authority = env
            adapter = claude.ClaudeAdapter(
                release_registry=registry, artifact_host=cell, workspace_root=root,
                cli_path=os.environ["AGENT_RUNTIME_TEST_CLAUDE_BIN"], read_only_dependencies=(dependency,),
            )
            adapters = AgentExecutionAdapterRegistry()
            adapters.register(adapter)
            run = run_module(request, release_registry=registry, adapters=adapters, artifact_host=cell,
                             ledger=InMemoryModuleExecutionLedger(), authority=authority)
            attempt = run.attempts[0]
            trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
            (root / f"boundary_{boundary}_trace.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2))
            calls = parse_cli_log(trace)["tool_calls"]
            assert any(call["tool_name"] == "Bash" and (call["request"] or {}).get("command") == command for call in calls)
            results = [call["response"]["tool_result"] for call in calls
                       if isinstance(call["response"], dict) and "tool_result" in call["response"]]
            assert results, "an attempted command without its result is not boundary evidence"
            assert all("outside_link_sentinel" not in str(event["content"])
                       and "outside_network_sentinel" not in str(event["content"]) for event in results)
            assert (Path(trace["cwd"]).parent / "materials/source").read_bytes() == b"original_material"
            assert dependency_file.read_text() == "original_dependency"
            assert received == []
            if boundary == "network":
                assert any("connectx to 127.0.0.1" in str(event["content"])
                           and "Operation not permitted" in str(event["content"]) for event in results), "nc must reach the connection attempt"
            print(json.dumps({"boundary": boundary, "attempt": attempt.attempt_id,
                              "status": attempt.status, "cli_version": trace["cli_version"], "resource_check": "passed"}))
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


@pytest.mark.real_run
@_LIVE
@pytest.mark.parametrize("tool", ["read", "search"])
def test_live_runtime_continues_after_file_tool_denial(tmp_path, tool):
    root = Path(os.environ["AGENT_RUNTIME_TEST_AB_WORKSPACE"]).resolve(strict=True)
    outside = root / f"outside_{tool}"
    outside.write_text("RUNTIME_OUTSIDE_FILE_SENTINEL")
    _, registry, cell, request, authority = _environment(tmp_path, tools=(tool,), material=b"authorized material", instructions=(
        f"This is an authorized boundary test with a disposable fixture. Use {claude.NATIVE_TOOLS[tool]} once "
        f"on the exact file {outside}" + (" with pattern '.'" if tool == "search" else "") +
        ". After that refusal, use the same tool on materials/source" + (" with pattern '.'" if tool == "search" else "") +
        ". Do not retry the forbidden path or ask for escalation. Return value='blocked' after the allowed read."
    ))
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(claude.ClaudeAdapter(release_registry=registry, artifact_host=cell,
        workspace_root=root, cli_path=os.environ["AGENT_RUNTIME_TEST_CLAUDE_BIN"]))
    run = run_module(request, release_registry=registry, adapters=adapters, artifact_host=cell,
                     ledger=InMemoryModuleExecutionLedger(), authority=authority)
    attempt = run.attempts[0]
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    (root / f"file_denial_{tool}.json").write_text(json.dumps(trace, ensure_ascii=False, indent=2))
    # The registered schema permits an optional note; verify the required verdict
    # without narrowing the model's valid output to one exact dictionary.
    assert _assert_completed_provider_run(run, cell)["value"] == "blocked"
    assert "policy_refusal_reason" not in trace
    inspected = parse_cli_log(trace)
    assert any(row["event"].get("subtype") == "permission_denied" for row in inspected["events"])
    calls = inspected["tool_calls"]
    assert "RUNTIME_OUTSIDE_FILE_SENTINEL" not in json.dumps([call["response"] for call in calls])
    assert any(item["status"] == "failed" and item["tool_name"] == claude.NATIVE_TOOLS[tool] for item in calls)
    assert any(item["status"] == "completed" and item["tool_name"] == claude.NATIVE_TOOLS[tool]
               and str((item["request"] or {}).get("file_path", (item["request"] or {}).get("path", "")))
                   .endswith("materials/source") for item in calls)


@pytest.mark.real_run
@pytest.mark.skipif(
    os.environ.get("RUN_PROVIDER_INTEGRATION") != "1" or os.environ.get("RUN_AB_REVIEWER_PG") != "1",
    reason="unverified: explicit real AB Reviewer and PostgreSQL run; set RUN_PROVIDER_INTEGRATION=1 and RUN_AB_REVIEWER_PG=1",
)
def test_live_ab_registered_design_reviewer_and_postgres(tmp_path):
    """Use a real registered Reviewer and request; permission ports are test-owned."""
    import importlib.util
    import uuid
    from dataclasses import replace
    from agent_runtime import (run_registered_workflow_module, ModuleReviewer, Workflow, RuntimeModulePlugin,
                               register_runtime_module_plugin)
    from agent_runtime.registry import (
        PostgresRuntimeReleaseStore, RuntimeReleaseBundle, ExecutionProfileReleaseSpec,
        ExecutionVariantPolicyReleaseCandidate, ExecutionVariantProfileBindingCandidate,
        compile_execution_profile_release, compile_execution_variant_policy_release,
        WorkflowReleaseCandidate, WorkflowNodeReleaseCandidate,
    )
    from agent_runtime.contracts.registry_release_definition import WorkflowNodeKind, WorkflowEdge
    from agent_runtime.ledger import PostgresRuntimeExecutionRecordStore, PostgresRuntimeExecutionQueryStore
    from test_agent_runtime_registered_module_execution import _Host

    ab = Path(os.environ["AGENT_RUNTIME_TEST_AB_ROOT"]).resolve(strict=True)
    config = json.loads((ab / "governance_bindings/ddm_runtime.json").read_text())
    data = json.loads(Path(os.environ["AGENT_RUNTIME_TEST_REVIEW_INPUT"]).read_text())
    payload = data["semantic_input"] if "semantic_input" in data else data
    assert payload["module_id"] == "design_contract_reviewer"
    dsn = os.environ[config["database_url_env"]]
    releases = PostgresRuntimeReleaseStore.from_dsn(dsn, schema=config["registry_schema"])
    assert releases.installed_schema_release().state == "ready"
    registry = releases.load_release_registry()
    original_module = registry.get_module(config["module_release_ref"], config["module_release_sha256"])
    from agent_runtime.execution.execution_local_invocation import _execution_profile_for_requirements
    reviewer = ModuleReviewer.from_registration(ab, skill_id="the-design-authoring", module_id="design_contract_reviewer")
    exported = reviewer.export(module_version="cli_sample_v1")
    profile = _execution_profile_for_requirements(exported.module_release.get_execution_requirements())
    module = exported.module_release
    assert module.input_schema_sha256 == original_module.input_schema_sha256
    assert module.output_schema_sha256 == original_module.output_schema_sha256
    assert exported.compiled.prompt_bundle.compiled_static_body == registry.get_prompt_bundle(
        original_module.prompt_bundle_ref, original_module.prompt_bundle_sha256).compiled_static_body
    workflow_export = Workflow.from_graph(WorkflowReleaseCandidate(
        workflow_id="ab_claude_cli_review", workflow_version="v1", workflow_contract_version="v1",
        owner_contract_ref=reviewer.source.owner_contract_ref, owner_contract_content=reviewer.source.owner_contract_content,
        graph_ref="workflow-graph:ab_claude_cli_review@v1", initial_node_id="review",
        nodes=(WorkflowNodeReleaseCandidate(node_id="review", node_kind=WorkflowNodeKind.MODULE,
            module_release_ref=module.release_ref, module_release_sha256=module.release_sha256,
            input_mapping_ref="input-mapping:ab_claude_cli_review@v1", input_mapping_document={"task_input": "payload"}),),
        edges=(WorkflowEdge("review", "complete", None, True),),
        authorization_manifest_ref="authorization-manifest:ab_claude_cli_review@v1",
        authorization_manifest_document={"operations": list(module.declared_operation_ids)},
        execution_binding_ref="execution-binding:ab_claude_cli_review@v1",
        execution_binding_document={"schema_version": "workflow_execution_binding_v1",
            "variant_policy_family": "execution_variant_policy", "workflow_id": "ab_claude_cli_review"},
    ), module_exports=(exported,)).export()
    workflow = workflow_export.workflow_release
    selection = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
        policy_id="ab_claude_cli_reviewer_sample", policy_version="v1", origin_kind="workflow",
        origin_release_ref=workflow.release_ref, origin_release_sha256=workflow.release_sha256,
        bindings=(ExecutionVariantProfileBindingCandidate(workflow.nodes[0].node_id, profile.release_ref, profile.release_sha256),),
    ))
    bundle = replace(workflow_export.origin_bundle, execution_profiles=(profile,), execution_variant_policies=(selection,))
    plugin = RuntimeModulePlugin(plugin_id="ab_claude_cli_review", plugin_version="v1", release_bundle=bundle)
    register_runtime_module_plugin(releases, plugin)
    register_runtime_module_plugin(releases, plugin)
    registry = PostgresRuntimeReleaseStore.from_dsn(dsn, schema=config["registry_schema"]).load_release_registry()
    assert registry.get_execution_profile(profile.release_ref, profile.release_sha256) == profile
    assert registry.get_module(module.release_ref, module.release_sha256) == module
    store = PostgresRuntimeExecutionRecordStore.from_dsn(dsn, schema=config["execution_schema"])
    cell = InMemoryCellArtifactStore()
    host = _Host(cell, workflow)
    adapter = claude.ClaudeAdapter(
        release_registry=registry, artifact_host=cell,
        workspace_root=Path(os.environ["AGENT_RUNTIME_TEST_AB_WORKSPACE"]),
        cli_path=os.environ["AGENT_RUNTIME_TEST_CLAUDE_BIN"],
        read_only_dependencies=(Path("/opt/miniconda3"),),
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    run = run_registered_workflow_module(
        module_id=module.module_id, input_payload=payload, idempotency_key="claude_review_"+uuid.uuid4().hex,
        release_registry=registry, workflow=workflow, variant_policy=selection,
        authorize=host.authorize, context_client=host, operation_client=host,
        enforcing_gateway_id="agent_runtime_module_kernel", environment_id="analyst_billie",
        adapters=adapters, artifact_host=cell, record_store=store, content_store=store,
        claim_token_secret=os.urandom(32),
    )
    execution_id = run.module_run.workflow_execution_id
    query = PostgresRuntimeExecutionQueryStore.from_dsn(dsn, schema=config["execution_schema"])
    attempt = run.attempts[0]
    evidence = query.load_content(execution_id, attempt.provider_trace_ref)
    assert evidence.content_sha256 == attempt.provider_trace_sha256
    root = Path(os.environ["AGENT_RUNTIME_TEST_AB_WORKSPACE"])
    (root / "registered_review_trace.json").write_bytes(evidence.body)
    (root / "registered_review_execution.json").write_text(json.dumps({
        "execution_id": execution_id, "attempt_id": attempt.attempt_id,
        "module_release": module.release_ref, "profile_release": profile.release_ref,
        "status": attempt.status, "failure_class": attempt.failure_class,
        "authorization_ports": "test_owned", "pg_trace_readback": "passed",
    }, ensure_ascii=False, indent=2))
    assert attempt.status == "completed", attempt.failure_class
    output = json.loads(query.load_content(execution_id, run.outputs[0].output_ref).body)
    validator_path = ab / "09_soul/governance/t0/validation/artifact_contracts/design_review_output.py"
    spec = importlib.util.spec_from_file_location("ab_design_output_validation", validator_path)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    validator.validate_design_contract_review_output(output, payload)
    (root / "registered_review_result.json").write_text(json.dumps({
        "execution_id": execution_id, "module_release": module.release_ref,
        "profile_release": profile.release_ref, "attempt_id": attempt.attempt_id,
        "status": attempt.status, "output_validation": "passed", "output": output,
        "pg_records": len(query.load_trace(execution_id).records),
    }, ensure_ascii=False, indent=2))
    print(json.dumps({"execution_id": execution_id, "module": module.release_ref, "verdict": output["verdict"], "pg_round_trip": "passed"}))
