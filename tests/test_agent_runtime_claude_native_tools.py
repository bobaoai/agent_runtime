"""Profile-selected Claude CLI execution and AB integration examples."""
import json
import os
import subprocess
from dataclasses import fields
from itertools import combinations
from pathlib import Path
from unittest.mock import patch

import pytest

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


def _environment(tmp_path, *, tools=("read", "search", "shell"), model="claude-opus-5[1m]", effort="xhigh", material=None, instructions="",
                 mode="agent", workspace="own_draft_read_write", output_mode="native_structured_output",
                 binding=("claude_cli_adapter", "v1"), output_schema_document=None):
    compiled = _compile_native_module(
        tmp_path, output_resolution_policy=OutputResolutionPolicy.DIRECT_SINGLE,
        execution_profile_id="claude_fields", executor_adapter_id=binding[0],
        executor_adapter_revision=binding[1], transport_kind="claude_cli", provider_id="anthropic",
        model_id=model, reasoning_profile=effort, execution_mode=mode,
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


def _fake_cli(tmp_path):
    executable = tmp_path / "claude"
    executable.write_text("#!/bin/sh\nprintf '%s\\n' '2.1.999 --safe-mode --restricted --tools --settings --effort --json-schema --setting-sources --strict-mcp-config --add-dir'\n")
    executable.chmod(0o700)
    return executable


def _result(**extra):
    return {"type": "result", "subtype": "success", "is_error": False,
            "modelUsage": {"claude-opus-5[1m]": {"canonicalModel": "claude-opus-5", "outputTokens": 3}},
            "usage": {"input_tokens": 7, "output_tokens": 3, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
            "structured_output": {"value": "checked"}, **extra}


def _init(tools=("Read", "Grep", "Bash")):
    return {"type": "system", "subtype": "init", "model": "claude-opus-5[1m]",
            "permissionMode": "auto",
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
            "decision_reason": "--restricted: path outside the working directory",
            "message": "The requested path is outside the allowed workspace."}


def _run(env, tmp_path, event_factory):
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
        cli_path=_fake_cli(tmp_path), process_runner=process,
        adapter_binding=(env[0].execution_profile.executor_adapter_id, env[0].execution_profile.executor_adapter_revision),
    )
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    # The fake transport needs no system temporary directory. Keep its owned
    # artifacts inside pytest's writable fixture under independent review.
    temporary_directory = claude.tempfile.TemporaryDirectory
    with patch.object(claude.tempfile, "TemporaryDirectory", lambda *args, **kw:
                      temporary_directory(*args, **{**kw, "dir": tmp_path})):
        run = run_module(request, release_registry=registry, adapters=adapters, artifact_host=cell,
                         ledger=InMemoryModuleExecutionLedger(), authority=authority, clock=lambda: _TEST_TIME)
    return run, cell


@pytest.mark.parametrize("change", [None, "binding", "legacy_marker"])
def test_attempt_workspace_binds_exact_authorization_boundary(tmp_path, monkeypatch, change):
    captured = []
    entered = []
    execute = claude.ClaudeAdapter._execute
    def capture(adapter, request, host, prepared, cleanup):
        captured.append((adapter, request, host))
        return execute(adapter, request, host, prepared, cleanup)
    def events(call):
        entered.append(True)
        yield _init()
        yield _result()
    monkeypatch.setattr(claude.ClaudeAdapter, "_execute", capture)
    run, cell = _run(_environment(tmp_path), tmp_path, events)
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


@pytest.mark.parametrize("target", ["work", "work/materials", "work/scratch", "material_file", "changed_material"])
def test_preinvocation_workspace_policy_refusal_is_recorded(tmp_path, monkeypatch, target):
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
def test_profile_drives_one_cli_command_builder(tmp_path, model, effort, tools):
    env = _environment(tmp_path, model=model, effort=effort, tools=tools)
    expected = [claude.NATIVE_TOOLS[name] for name in tools]
    def events(call):
        argv = call["argv"]
        assert argv[argv.index("--model")+1] == model
        assert argv[argv.index("--effort")+1] == effort
        assert argv[argv.index("--tools")+1].split(",") == expected
        assert argv[argv.index("--allowedTools")+1].split(",") == expected
        assert "--safe-mode" in argv and "--restricted" in argv
        assert argv[argv.index("--permission-mode")+1] == "auto"
        assert call["environment"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert call["environment"]["GIT_CONFIG_NOSYSTEM"] == "1"
        assert "--system-prompt" not in argv and call["prompt"] not in argv
        assert json.loads(argv[argv.index("--mcp-config")+1]) == {"mcpServers": {}}
        yield {**_init(expected), "model": model}
        yield _result(modelUsage={model: {"canonicalModel": model.removesuffix("[1m]"), "outputTokens": 3}})
    run, cell = _run(env, tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}
    profile = env[0].execution_profile
    assert ExecutionProfileRelease.from_dict(profile.as_dict()).as_dict() == profile.as_dict()


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


@pytest.mark.parametrize("binding", [("claude_cli_adapter", "v1"), ("claude_cli_native_tools_executor", "v2")])
def test_actual_cli_settings_keep_resource_boundaries(tmp_path, binding):
    def events(call):
        argv = call["argv"]
        settings = json.loads(argv[argv.index("--settings") + 1])
        assert settings["permissions"]["blockReadsOutsideWorkingDirectories"] is True
        sandbox = settings["sandbox"]
        assert sandbox["enabled"] and sandbox["failIfUnavailable"]
        assert sandbox["allowUnsandboxedCommands"] is False
        fs = sandbox["filesystem"]
        assert fs["denyRead"] == ["/"]
        assert str(call["cwd"]) in fs["allowRead"]
        assert str(tmp_path) not in fs["allowRead"]
        material_root = call["cwd"] if binding[1] == "v2" else call["cwd"].parent
        assert str(material_root / "materials") in fs["denyWrite"]
        assert fs["allowWrite"] == [str(call["cwd"]), call["environment"]["TMPDIR"]]
        assert sandbox["network"] == {"allowedDomains": [], "strictAllowlist": True,
            "allowAllUnixSockets": False, "allowLocalBinding": False}
        yield _init()
        yield _result()
    run, cell = _run(_environment(tmp_path, material=b"protected", binding=binding), tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}


def test_preflight_timeout_keeps_byte_diagnostics(tmp_path, monkeypatch):
    env = _environment(tmp_path)
    def preflight(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 30, output=b"partial version", stderr=b"fixture diagnostic")
    monkeypatch.setattr(claude.subprocess, "run", preflight)
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


@pytest.mark.parametrize("damage", [None, "change", "remove", "symlink"])
@pytest.mark.parametrize("binding", [("claude_cli_adapter", "v1"), ("claude_cli_native_tools_executor", "v2")])
@pytest.mark.parametrize("process_error", [False, True])
def test_materials_checked_on_process_completion_and_exception(tmp_path, damage, binding, process_error):
    env = _environment(tmp_path, material=b"original", binding=binding)
    def events(call):
        yield _init()
        source = (call["cwd"] if binding[1] == "v2" else call["cwd"].parent) / "materials/source"
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


@pytest.mark.parametrize("surface", ["tools", "skills", "plugins", "slash_commands", "mcp_servers", "permissionMode"])
def test_unexpected_init_stops_stream(tmp_path, surface):
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
    run, _ = _run(_environment(tmp_path), tmp_path, events)
    assert run.attempts[0].failure_class == "policy_violation"
    assert reached == []


@pytest.mark.parametrize("source", ["event", "result", "combined"])
@pytest.mark.parametrize("binding", [("claude_cli_adapter", "v1"), ("claude_cli_native_tools_executor", "v2")])
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
    calls = {item["tool_call_id"]: item for item in trace["tool_log"]["tool_calls"]}
    assert len(calls) == 2 and calls["denied"]["status"] == "failed"
    assert calls["allowed"]["status"] == "completed"
    assert trace["tool_log"]["complete"]
    assert bool(trace["public_events"]) is (source != "result")


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
    assert not trace["tool_log"]["complete"] and trace["tool_log"]["tool_calls"] == []
    recorded = [item["event"] for item in trace["tool_log"]["events"]]
    if source == "result":
        assert recorded[-1]["permission_denials"] == [{"tool_name": "Read"}]
    else:
        assert observation in recorded


@pytest.mark.parametrize("ending,expected", [
    ("provider", "provider"), ("nonzero", "transport"), ("timeout", "timeout"),
    ("cancelled", "cancelled"), ("missing", "provider"), ("schema", "schema"),
    ("invalid_event", "provider"), ("material", "policy_violation"),
])
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
            (call["cwd"].parent / "materials/source").write_bytes(b"changed")
        yield _result(**({"subtype": "error_during_execution", "is_error": True} if ending == "provider" else
                         {"structured_output": {"wrong": 1}} if ending == "schema" else {}))
        if ending == "nonzero":
            raise subprocess.CalledProcessError(1, call["argv"])
    run, cell = _run(_environment(tmp_path, material=b"protected"), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.failure_class == expected and not run.outputs
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert trace["public_events"] == [_denial("denied")]
    assert "permission_denied" in trace["stdout"]
    if ending != "material":
        assert "policy_refusal_reason" not in trace


@pytest.mark.parametrize("outcome", ["denied", "error", "completed", "missing", "missing_id",
                                     "wrong_id", "duplicate_request", "conflicting_results"])
def test_undeclared_tool_request_requires_unambiguous_success_evidence(tmp_path, outcome):
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
    assert any(item.get("id") == "extra" for item in trace["native_tool_events"])
    if outcome == "completed":
        assert attempt.failure_class == "policy_violation" and not run.outputs
        assert trace["policy_refusal_reason"] == "CLI completed an undeclared tool"
    else:
        _assert_completed_provider_run(run, cell)
        assert "policy_refusal_reason" not in trace
        if outcome not in {"denied", "error"}:
            assert trace["tool_log"]["complete"] is False


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


def test_command_permission_words_are_not_permission_evidence(tmp_path):
    def events(call):
        yield _init()
        yield {"type": "user", "message": {"content": [{"type": "tool_result", "is_error": True,
            "tool_use_id": "test", "content": "AssertionError: expected 'permission denied'"}]}}
        yield _result()
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}


@pytest.mark.parametrize("failure", ["host_denied", "hash_mismatch", "missing_cli_option", "cli_unavailable"])
def test_preparation_failures_never_enter_cli(tmp_path, monkeypatch, failure):
    from agent_runtime.execution.execution_module_invocation import _AttemptExecutionHost
    env = _environment(tmp_path, material=b"original")
    entered = []
    if failure == "host_denied":
        def denied(*_):
            raise PermissionError("host denied read")
        monkeypatch.setattr(_AttemptExecutionHost, "read_authorized_input", denied)
    elif failure == "hash_mismatch":
        monkeypatch.setattr(_AttemptExecutionHost, "read_authorized_input", lambda *_: b"wrong")
    else:
        real_run = claude.subprocess.run
        def unavailable(args, **kwargs):
            if args[-1] == "--help":
                if failure == "cli_unavailable":
                    raise OSError("CLI unavailable")
                return subprocess.CompletedProcess(args, 0, stdout="--model", stderr="")
            return real_run(args, **kwargs)
        monkeypatch.setattr(claude.subprocess, "run", unavailable)
    def events(call):
        entered.append(True)
        yield _result()
    run, _ = _run(env, tmp_path, events)
    assert not entered and not run.outputs
    assert run.attempts[0].failure_class == {"host_denied": "authorization", "hash_mismatch": "schema"}.get(failure, "dependency_unavailable")


@pytest.mark.parametrize("final", [None, {"type":"result","subtype":"error","is_error":True}, _result(structured_output={"wrong":1})])
def test_missing_error_or_invalid_result_cannot_succeed(tmp_path, final):
    def events(call):
        yield _init()
        if final is not None:
            yield final
    run, _ = _run(_environment(tmp_path), tmp_path, events)
    assert run.attempts[0].status == "failed" and not run.outputs


@pytest.mark.parametrize("with_init", [True, False])
@pytest.mark.parametrize("fields,expected", [({"result":"usage limit reached"}, "quota"),
    ({"api_error_status":401}, "authentication"), ({"api_error_status":403}, "authorization"),
    ({"api_error_status":429}, "rate_limit")])
def test_provider_error_retains_its_category(tmp_path, fields, expected, with_init):
    def events(call):
        if with_init:
            yield _init()
        yield _result(subtype="error_during_execution", is_error=True, **fields)
    run, _ = _run(_environment(tmp_path), tmp_path, events)
    assert run.attempts[0].failure_class == expected


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
    assert trace["process_output_complete"] is False
    assert attempt.usage.input_tokens is None and attempt.usage.output_tokens is None
    if failure == "invalid_event":
        assert "invalid-json-line" in trace["stdout"]


@pytest.mark.parametrize("bad_event", ["invalid-json", _result()])
def test_stream_event_error_keeps_provider_failure_and_usage(tmp_path, bad_event):
    def events(call):
        yield _init()
        yield _result()
        yield bad_event
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.status == "failed" and not run.outputs
    assert attempt.failure_class == "provider"
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    assert detail["failure_code"] == "claude_cli_result_missing_or_invalid"
    assert attempt.usage.input_tokens == 7 and attempt.usage.output_tokens == 3


@pytest.mark.parametrize("reason,exit_code", [
    ("observer_stopped", 0), ("stream_error", 0), ("output_limit", 0),
    ("cleanup_error", 0), ("cleanup_error", None), ("timeout", 0),
])
def test_runtime_capture_failure_preserves_real_exit_and_reason(tmp_path, reason, exit_code):
    def events(call):
        yield _init()
        yield _result()
        if reason == "timeout":
            raise CliProcessTimeout(call["argv"], 1, returncode=exit_code,
                output="captured provider output", stderr="actual stderr", cleanup_error="cleanup diagnostic",
                stream_error="additional stream diagnostic")
        raise CliProcessError(exit_code, call["argv"], stop_reason=reason, message="Runtime capture stopped",
            output="captured provider output", stderr="actual stderr", cleanup_error="cleanup diagnostic",
            stream_error="additional stream diagnostic")
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.status == "failed" and not run.outputs
    assert attempt.failure_class == ("timeout" if reason == "timeout" else "transport")
    assert attempt.usage.input_tokens == 7 and attempt.usage.output_tokens == 3
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert trace["exit_code"] == exit_code and trace["stop_reason"] == reason
    assert trace["process_output_complete"] is False and trace["cleanup_error"] == "cleanup diagnostic"
    assert trace["stream_error"] == "additional stream diagnostic"
    assert trace["stdout"] == "captured provider output" and trace["stderr"] == "actual stderr"
    detail = json.loads(cell.read_bytes(attempt.failure_detail_ref, attempt.failure_detail_sha256))
    assert detail["transport_exit_code"] == exit_code


@pytest.mark.parametrize("primary_reason", ["output_limit", "stream_error", "cleanup_error"])
def test_capture_primary_reason_is_not_replaced_by_late_invalid_event(tmp_path, primary_reason):
    def events(call):
        yield _init()
        yield _result()
        assert call["on_stdout_line"]("late-invalid-json") is False
        raise CliProcessError(0, call["argv"], stop_reason=primary_reason, message="primary capture failure",
            output="late-invalid-json", stderr="provider diagnostic")
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    attempt = run.attempts[0]
    assert attempt.failure_class == "transport" and not run.outputs
    trace = json.loads(cell.read_bytes(attempt.provider_trace_ref, attempt.provider_trace_sha256))
    assert trace["stop_reason"] == primary_reason and trace["event_error"]
    assert trace["exit_code"] == 0 and trace["stderr"] == "provider diagnostic"
    assert attempt.usage.input_tokens == 7


@pytest.mark.parametrize("usage", ["bad", [], {"input_tokens":"7"}, {"output_tokens":-1},
    {"input_tokens":True}, {"cache_read_input_tokens":1.5}])
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


def test_unknown_usage_remains_unknown(tmp_path):
    def events(call):
        yield _init()
        yield _result(usage=None)
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    _assert_completed_provider_run(run, cell)
    assert run.attempts[0].usage.input_tokens is None and run.attempts[0].usage.output_tokens is None


def test_valid_usage_includes_reported_cache_counts(tmp_path):
    def events(call):
        yield _init()
        yield _result(usage={"input_tokens":2, "output_tokens":5,
                             "cache_read_input_tokens":11, "cache_creation_input_tokens":13})
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    _assert_completed_provider_run(run, cell)
    usage = run.attempts[0].usage
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_tokens, usage.cache_creation_tokens) == (26, 5, 11, 13)


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


_TOOL_SUBSETS = tuple(combination for size in range(4)
                      for combination in combinations(("read", "search", "shell"), size))


@pytest.mark.parametrize("tools", _TOOL_SUBSETS)
@pytest.mark.parametrize("workspace", ["none", "own_draft_read_write"])
@pytest.mark.parametrize("output_mode", ["prompt_only_json", "native_structured_output"])
def test_claude_adapter_executes_each_agent_field_combination(tmp_path, tools, workspace, output_mode):
    """Actual kernel -> Adapter -> runner; no command-builder-only success claim."""
    calls = []
    native = output_mode == "native_structured_output"
    def events(call):
        calls.append(call)
        argv = call["argv"]
        expected = ",".join(claude.NATIVE_TOOLS[tool] for tool in tools)
        assert argv[argv.index("--tools")+1] == expected
        assert argv[argv.index("--allowedTools")+1] == expected
        assert ("--json-schema" in argv) is native
        assert json.loads(argv[argv.index("--mcp-config")+1]) == {"mcpServers": {}}
        assert "--max-turns" not in argv and "--max-budget-usd" not in argv
        settings = json.loads(argv[argv.index("--settings")+1])
        fs = settings["sandbox"]["filesystem"]
        if workspace == "none":
            assert fs["denyWrite"] == ["/"] and fs["allowWrite"] == []
            assert not (call["cwd"] / "scratch").exists()
            assert "No model-writable workspace" in call["prompt"]
            material = call["cwd"] / "materials/source"
        else:
            assert call["cwd"].name == "scratch"
            assert fs["allowWrite"] == [str(call["cwd"]), call["environment"]["TMPDIR"]]
            material = call["cwd"].parent / "materials/source"
        assert material.read_bytes() == b"authorized evidence"
        # This double proves staging/translation; OS denial is separately live-tested.
        initialization = _init(tuple(claude.NATIVE_TOOLS[tool] for tool in tools))
        if not native:
            initialization["tools"].remove("StructuredOutput")
        yield initialization
        terminal = _result()
        if not native:
            terminal.pop("structured_output")
            terminal["result"] = '{"value":"checked"}'
        yield terminal
    run, cell = _run(_environment(tmp_path, tools=tools, workspace=workspace, output_mode=output_mode,
                                  material=b"authorized evidence"), tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}
    assert len(calls) == 1
    trace = json.loads(cell.read_bytes(run.attempts[0].provider_trace_ref, run.attempts[0].provider_trace_sha256))
    assert (trace["executor_adapter_id"], trace["executor_adapter_revision"]) == ("claude_cli_adapter", "v1")
    assert trace["execution_profile_ref"] == run.variants[0].execution_profile_ref
    assert trace["tool_log"]["complete"]


@pytest.mark.parametrize("output_mode", ["prompt_only_json", "native_structured_output"])
def test_tool_free_uses_same_adapter_with_explicit_empty_tools(tmp_path, output_mode):
    native = output_mode == "native_structured_output"
    def events(call):
        assert call["argv"][call["argv"].index("--tools") + 1] == ""
        init = _init(())
        if not native:
            init["tools"] = []
        yield init
        yield _result() if native else _result(structured_output=None, result='{"value":"checked"}')
    run, cell = _run(_environment(tmp_path, tools=(), mode="tool_free", workspace="none", output_mode=output_mode),
                     tmp_path, events)
    assert _assert_completed_provider_run(run, cell) == {"value": "checked"}


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_model_and_effort_preserve_permissions_and_actual_selection(tmp_path, effort):
    def events(call):
        argv = call["argv"]
        assert argv[argv.index("--model")+1] == "concrete-other-model"
        assert argv[argv.index("--effort")+1] == effort
        assert argv[argv.index("--tools")+1] == "Grep,Read"
        assert call["timeout_seconds"] == 60
        yield {**_init(("Grep", "Read")), "model": "concrete-other-model"}
        yield {"type": "assistant", "message": {"model": "concrete-other-model", "content": []}}
        yield _result()
    run, cell = _run(_environment(tmp_path, tools=("search", "read"), model="concrete-other-model", effort=effort), tmp_path, events)
    _assert_completed_provider_run(run, cell)


@pytest.mark.parametrize("effort", ["none", "ultra", "--tools", "", "high;echo injected"])
def test_invalid_effort_never_launches_provider(tmp_path, effort):
    def unused(call):
        pytest.fail("invalid effort reached Provider")
        yield
    try:
        env = _environment(tmp_path, effort=effort)
    except ValueError:
        assert effort in {"", "--tools", "high;echo injected"}
        return
    run, cell = _run(env, tmp_path, unused)
    assert run.attempts[0].status == "failed"


@pytest.mark.parametrize("binding", [("claude_cli_adapter", "v2"), ("unknown", "v1"), ["claude_cli_adapter", "v1"]])
def test_unknown_adapter_identity_rejected_before_resource_resolution(tmp_path, binding):
    with pytest.raises(ValueError, match="adapter binding"):
        claude.ClaudeAdapter(release_registry=None, artifact_host=None,
            workspace_root=tmp_path, cli_path=tmp_path / "missing", adapter_binding=binding)


@pytest.mark.parametrize("tools", [("unknown_tool",), ("Read",), ("read", "read"), ("Bash(*)",)])
def test_unsupported_or_malformed_tools_never_reach_provider(tmp_path, tools):
    def unused(call):
        pytest.fail("unrecognized tools reached Provider")
        yield
    if tools in {("Read",), ("read", "read"), ("Bash(*)",)}:
        with pytest.raises(ValueError):
            _environment(tmp_path, tools=tools)
    else:
        run, _ = _run(_environment(tmp_path, tools=tools), tmp_path, unused)
        assert run.attempts[0].status == "failed"


def test_command_renderer_and_execute_share_the_field_validator(tmp_path, monkeypatch):
    seen = []
    validate = claude._execution_expectation
    def observe(profile, binding):
        seen.append((profile.release_ref, binding))
        return validate(profile, binding)
    monkeypatch.setattr(claude, "_execution_expectation", observe)
    def events(call):
        yield _init()
        yield _result()
    run, cell = _run(_environment(tmp_path), tmp_path, events)
    _assert_completed_provider_run(run, cell)
    assert len(seen) == 2 and seen[0] == seen[1]


@pytest.mark.parametrize("detail", ["Exit code 2\ncommand usage error",
                                       "Exit code 1\napplication text: operation not permitted"])
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
    assert trace["tool_log"]["complete"] is True
    call = trace["tool_log"]["tool_calls"][0]
    assert call["status"] == "failed"
    assert call["response"]["tool_result"]["content"] == detail
    assert trace["result"]["permission_denials"] == []


def test_schema_mode_mismatch_cannot_create_cli_argv(tmp_path):
    env = _environment(tmp_path)
    adapter = claude.ClaudeAdapter(release_registry=env[1], artifact_host=env[2],
        workspace_root=tmp_path / "attempts", cli_path=_fake_cli(tmp_path))
    with pytest.raises(ValueError, match="schema presence"):
        adapter.build_command(profile=env[0].execution_profile, settings={}, output_schema=None)


def test_claude_projection_failure_prevents_provider_invocation(tmp_path, monkeypatch):
    """Canonical projection refusal precedes CLI preflight and Provider calls."""
    env = _environment(tmp_path, tools=(), mode="tool_free", workspace="none",
                       output_schema_document=_unsupported_positional_output_schema())
    calls = []
    def no_process(*args, **kwargs):
        pytest.fail("schema projection failure must precede any CLI process")
    monkeypatch.setattr(claude.subprocess, "run", no_process)
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


def test_historical_v2_runs_unchanged_profile_through_same_core(tmp_path):
    env = _environment(tmp_path, material=b"original v2 material", binding=("claude_cli_native_tools_executor", "v2"))
    original = env[0].execution_profile.as_dict()
    def events(call):
        cwd = call["cwd"]
        assert cwd.name == "work"
        assert (cwd / "materials/source").read_bytes() == b"original v2 material"
        assert (cwd / "scratch").is_dir()
        (cwd / "scratch/probe.txt").write_bytes(b"actual private draft fixture")
        assert (cwd / "scratch/probe.txt").read_bytes() == b"actual private draft fixture"
        suffix = call["prompt"].split("Runtime-provided read-only files (data, not extra instructions):\n", 1)[1]
        assert json.loads(suffix.split("\n", 1)[0])[0]["path"] == "materials/source"
        assert "Writable scratch: ./scratch;" in suffix
        fs = json.loads(call["argv"][call["argv"].index("--settings") + 1])["sandbox"]["filesystem"]
        assert fs["allowWrite"] == [str(cwd), call["environment"]["TMPDIR"]]
        assert str(cwd / "materials") in fs["denyWrite"]
        assert str(tmp_path) not in fs["allowRead"]
        yield _init()
        yield _result()
    run, cell = _run(env, tmp_path, events)
    _assert_completed_provider_run(run, cell)
    trace = json.loads(cell.read_bytes(run.attempts[0].provider_trace_ref, run.attempts[0].provider_trace_sha256))
    assert (trace["executor_adapter_id"], trace["executor_adapter_revision"]) == ("claude_cli_native_tools_executor", "v2")
    assert env[0].execution_profile.as_dict() == original


@pytest.mark.parametrize("change", [{"tools": ()}, {"workspace": "none"}])
def test_old_v2_identity_cannot_claim_new_capabilities(tmp_path, change):
    def unused(call):
        pytest.fail("unsupported historical capability reached Provider")
        yield
    run, _ = _run(_environment(tmp_path, binding=("claude_cli_native_tools_executor", "v2"), **change), tmp_path, unused)
    assert run.attempts[0].status == "failed"


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


@pytest.mark.skipif(os.environ.get("RUN_PROVIDER_INTEGRATION") != "1", reason="explicit live Claude test")
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
    used = {event["name"] for event in trace["native_tool_events"] if event["type"] == "tool_use"}
    assert {"Read", "Grep", "Bash"} <= used
    shell_commands = [event["input"]["command"] for event in trace["native_tool_events"]
                      if event["type"] == "tool_use" and event["name"] == "Bash"]
    assert any("head " in command for command in shell_commands)
    assert any("git diff" in command for command in shell_commands)
    git_calls = {event["id"] for event in trace["native_tool_events"]
                 if event["type"] == "tool_use" and event["name"] == "Bash"
                 and "git diff" in event["input"]["command"] and "cd " in event["input"]["command"]}
    assert git_calls
    assert any(event["type"] == "tool_result" and event.get("tool_use_id") in git_calls
               and "diff --git" in str(event.get("content")) and "@@ " in str(event.get("content"))
               for event in trace["native_tool_events"])
    print(json.dumps({"attempt": attempt.attempt_id, "tools": sorted(used), "cli_version": trace["cli_version"]}))


@pytest.mark.skipif(os.environ.get("RUN_PROVIDER_INTEGRATION") != "1", reason="explicit live Claude test")
@pytest.mark.parametrize("capture_exit", [False, True])
def test_live_claude_rejects_ab_sibling_read(tmp_path, capture_exit):
    root = Path(os.environ["AGENT_RUNTIME_TEST_AB_WORKSPACE"]).resolve(strict=True)
    sibling = root / "outside_attempt.txt"
    sibling.write_text("outside_attempt_sentinel")
    compiled, registry, cell, _, _ = _environment(tmp_path)
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
    results = [e for e in trace["native_tool_events"] if e["type"] == "tool_result"]
    assert any("not permitted" in str(e["content"]).lower() for e in results)
    assert all("outside_attempt_sentinel" not in str(e["content"]) for e in results)


@pytest.mark.skipif(os.environ.get("RUN_PROVIDER_INTEGRATION") != "1", reason="explicit live Claude test")
@pytest.mark.parametrize("boundary", ["symlink_read", "material_write", "dependency_write", "network"])
def test_live_claude_resource_boundaries_in_ab(tmp_path, boundary):
    """Verify actual resource effects, independently of how a shell reports failure."""
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
            env = _environment(tmp_path, material=b"original_material", instructions=(
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
            uses = [event for event in trace["native_tool_events"] if event["type"] == "tool_use"]
            assert any(event["name"] == "Bash" and event["input"].get("command") == command for event in uses)
            results = [event for event in trace["native_tool_events"] if event["type"] == "tool_result"]
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


@pytest.mark.skipif(os.environ.get("RUN_PROVIDER_INTEGRATION") != "1", reason="explicit live Claude test")
@pytest.mark.parametrize("tool", ["read", "search"])
def test_live_runtime_continues_after_file_tool_denial(tmp_path, tool):
    root = Path(os.environ["AGENT_RUNTIME_TEST_AB_WORKSPACE"]).resolve(strict=True)
    outside = root / f"outside_{tool}"
    outside.write_text("RUNTIME_OUTSIDE_FILE_SENTINEL")
    _, registry, cell, request, authority = _environment(tmp_path, tools=(tool,), material=b"authorized material", instructions=(
        f"This is an authorized boundary test with a disposable fixture. Use {claude.NATIVE_TOOLS[tool]} once "
        f"on the exact file {outside}" + (" with pattern '.'" if tool == "search" else "") +
        ". After that refusal, use the same tool on ../materials/source" + (" with pattern '.'" if tool == "search" else "") +
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
    assert _assert_completed_provider_run(run, cell) == {"value": "blocked"}
    assert "policy_refusal_reason" not in trace
    assert any(event.get("subtype") == "permission_denied" for event in trace["public_events"])
    assert "RUNTIME_OUTSIDE_FILE_SENTINEL" not in json.dumps(trace["native_tool_events"])
    calls = trace["tool_log"]["tool_calls"]
    assert any(item["status"] == "failed" and item["tool_name"] == claude.NATIVE_TOOLS[tool] for item in calls)
    assert any(item["status"] == "completed" and item["tool_name"] == claude.NATIVE_TOOLS[tool]
               and str((item["request"] or {}).get("file_path", (item["request"] or {}).get("path", "")))
                   .endswith("materials/source") for item in calls)


@pytest.mark.skipif(
    os.environ.get("RUN_PROVIDER_INTEGRATION") != "1" or os.environ.get("RUN_AB_REVIEWER_PG") != "1",
    reason="explicit real AB Reviewer and PostgreSQL validation",
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
