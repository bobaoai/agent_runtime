"""Codex tool-free v5 configuration, host-login reuse and launch-guard tests."""
from dataclasses import asdict
import subprocess

import pytest

from agent_runtime.contracts.invocation_adapter_definition import (
    AuthorizedAgentExecutionRequest, SelfTestResourceUnavailableError,
)
from agent_runtime.invocation import invocation_codex_module_invocation as codex
import test_agent_runtime_codex_execution_outcomes as cases


@pytest.mark.deterministic
def test_configuration_is_pure_and_contains_no_host_auth_or_model_defaults(tmp_path, monkeypatch):
    compiled = cases.native._compile_native_module(tmp_path, executor_adapter_revision="v5",
                                                   model_id="explicit-model", reasoning_profile="xhigh")
    monkeypatch.setattr(codex.HOST_PLATFORM, "create_private_directory", lambda **kw: pytest.fail("pure check created resources"))
    profile = compiled.execution_profile
    expected = codex._execution_expectation(profile)
    assert expected.executor_adapter_revision == "v5"
    argv = codex.build_command(profile=profile, workspace=tmp_path, codex_bin="host-codex")
    assert argv[:4] == ["host-codex", "exec", "-m", "explicit-model"]
    assert argv[-1] == "-" and "--output-schema" not in argv
    settings = [argv[index+1] for index, value in enumerate(argv) if value == "-c"]
    for required in ["agents.enabled=false", "skills.include_instructions=false", "mcp_servers={}",
        "features.skip_host_skill_discovery=true", 'approval_policy="never"',
        'features.code_mode.excluded_tool_namespaces=["functions","collaboration","clock"]',
        "features.skill_search=false", "features.memories=false", "features.hooks=false", "features.view_image=false"]:
        assert required in settings
    assert not any("auth.json" in value or "gpt-6-astra" in value or "cli_auth_credentials_store" in value
                   for value in argv)
    # Host login, config.toml and execpolicy rules load normally (no ignore flags).
    assert "--ignore-user-config" not in argv and "--ignore-rules" not in argv
    assert {"--strict-config", "--ephemeral", "--json", "--skip-git-repo-check"} <= set(argv)


@pytest.mark.deterministic
def test_codex_environment_passes_host_codex_home_only_when_set():
    assert "CODEX_HOME" in codex._ENVIRONMENT_KEYS
    filtered = codex.HOST_PLATFORM.provider_environment({"CODEX_HOME": "C:/host/codex", "PATH": "p", "SECRET_DSN": "x"},
                                                        codex._ENVIRONMENT_KEYS)
    assert filtered["CODEX_HOME"] == "C:/host/codex" and "SECRET_DSN" not in filtered
    assert "CODEX_HOME" not in codex.HOST_PLATFORM.provider_environment({"PATH": "p"}, codex._ENVIRONMENT_KEYS)


@pytest.mark.deterministic
def test_v4_is_readable_but_cannot_execute_as_v5(tmp_path):
    profile = cases.native._compile_native_module(tmp_path, executor_adapter_revision="v4").execution_profile
    profile.validate()
    with pytest.raises(ValueError, match="v5"):
        codex._execution_expectation(profile)


def test_old_invoker_signature_rejected_before_resources_or_call(tmp_path, monkeypatch):
    calls = []
    env = cases.environment(tmp_path, lambda _: cases.process())
    def old(*, argv, prompt, cwd, timeout_seconds):
        calls.append(argv)
        return cases.process()
    env.executor._invoker = old
    monkeypatch.setattr(codex, "prepare_attempt_workspace", lambda **kw: pytest.fail("resources before contract check"))
    with pytest.raises(ValueError, match="environment"):
        cases.execute(env)
    assert calls == []


def self_test_request(original):
    fields = asdict(original)
    fields.pop("request_sha256")
    fields["authorized_inputs"] = original.authorized_inputs
    for name in ("execution_authorization_binding", "protected_operation_intent", "product_operation_decision",
                 "gateway_authorization_observation", "operation_grant"):
        fields[name+"_ref"] = fields[name+"_sha256"] = None
    fields["grant_disposition_ref"] = None
    fields["self_test_binding_ref"] = "self-test:synthetic"
    fields["self_test_binding_sha256"] = "8" * 64
    fields["workflow_execution_id"] = "self_test_execution"
    fields["isolated_scope_ref"] = fields["isolated_scope_sha256"] = None
    return AuthorizedAgentExecutionRequest.build(**fields)


@pytest.mark.parametrize("closed", [False, True])
def test_self_test_guard_reaches_the_actual_process_runner(tmp_path, monkeypatch, closed):
    effects, validations = [], []
    env = cases.environment(tmp_path, lambda _: cases.process())
    env.request = self_test_request(env.request)
    env.executor._invoker = codex._default_invoke

    class Host(cases.native._RecordingHost):
        def validate_self_test_binding(self, request, **ports):
            assert request == env.request and ports["adapter"] is env.executor
            validations.append(request)
        def guard_self_test_launch(self, request, launch, **ports):
            self.validate_self_test_binding(request, **ports)
            if closed:
                raise SelfTestResourceUnavailableError("closed before Popen")
            return launch()

    def runner(**fields):
        def popen():
            effects.append(fields["argv"])
            result = subprocess.CompletedProcess(fields["argv"], 0,
                "codex 0.test" if "--version" in fields["argv"] else "[]" if fields["argv"][-3:] == ["mcp", "list", "--json"]
                else cases.raw_events([cases.message(), cases.terminal()]).decode(), "")
            result.stdout_bytes, result.stderr_bytes = result.stdout.encode(), b""
            return result
        assert fields["launch_guard"] is not None
        return fields["launch_guard"](popen)

    monkeypatch.setattr(codex, "run_cli_process", runner)
    env.host = Host()
    result, trace = cases.execute(env)
    assert validations
    if closed:
        assert not effects and result.failure.failure_class == "authorization"
        assert cases.detail(env, result)["failure_code"] == "self_test_resources_unavailable"
    else:
        # Version, the host MCP listing (nothing configured, nothing declared), then exec.
        assert len(effects) == 3 and result.terminal_status == "completed"
        assert effects[1][-3:] == ["mcp", "list", "--json"] and "mcp_servers={}" in effects[1]
        assert trace["cli_version"] == "codex 0.test"
        assert trace["effective_configuration"]["host_mcp_disabled"] == []


def test_version_capture_survives_later_timeout(tmp_path, monkeypatch):
    env = cases.environment(tmp_path, lambda _: cases.process())
    env.executor._invoker = codex._default_invoke
    def runner(**fields):
        if "--version" in fields["argv"]:
            return subprocess.CompletedProcess(fields["argv"], 0, "codex fixture-v4\n", "")
        if fields["argv"][-3:] == ["mcp", "list", "--json"]:
            return subprocess.CompletedProcess(fields["argv"], 0, "[]", "")
        raise subprocess.TimeoutExpired(fields["argv"], 1, output=cases.raw_events([cases.terminal()]), stderr=b"partial")
    monkeypatch.setattr(codex, "run_cli_process", runner)
    result, trace = cases.execute(env)
    assert result.failure.failure_class == "timeout"
    assert trace["cli_version"] == "codex fixture-v4"
    assert result.input_tokens == 12


def test_missing_live_host_rejected_before_private_state_preparation(tmp_path, monkeypatch):
    env = cases.environment(tmp_path, lambda _: cases.process())
    env.request = self_test_request(env.request)
    monkeypatch.setattr(codex, "prepare_attempt_workspace", lambda **kw: pytest.fail("workspace prepared before live host"))
    with pytest.raises(SelfTestResourceUnavailableError):
        env.executor.execute(env.request, env.host)
    assert not env.calls


@pytest.mark.fake_run
@pytest.mark.parametrize("closed", [False, True])
def test_default_invoker_real_process_launch_is_guarded(tmp_path, monkeypatch, closed):
    from agent_runtime.invocation import invocation_process_execution as processes
    from test_agent_runtime_claude_native_tools import _scripted_cli
    executable = _scripted_cli(tmp_path / "synthetic_codex",
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('codex synthetic-local-process')\n"
        "elif sys.argv[-3:] == ['mcp', 'list', '--json']:\n"
        "    print('[]')\n"
        "elif sys.argv[1] == 'exec':\n"
        "    sys.stdin.read()\n"
        f"    sys.stdout.buffer.write({cases.raw_events([cases.message(), cases.terminal()])!r})\n"
        "else:\n"
        "    raise SystemExit(3)\n", monkeypatch)
    env = cases.environment(tmp_path, lambda _: pytest.fail("injected path must not execute"))
    env.request = self_test_request(env.request)
    env.executor._invoker = codex._default_invoke
    env.executor._codex_bin = str(executable)
    order = []

    class Host(cases.native._RecordingHost):
        def validate_self_test_binding(self, request, **ports):
            assert request == env.request and ports["adapter"] is env.executor
        def guard_self_test_launch(self, request, launch, **ports):
            self.validate_self_test_binding(request, **ports)
            order.append("guard")
            if closed:
                raise SelfTestResourceUnavailableError("closed before actual Popen")
            return launch()

    create = processes.HOST_PLATFORM.create_cli_process
    def recorded_create(argv, **kwargs):
        if str(argv[0]) == str(executable):
            order.append("version" if "--version" in argv else "list" if "mcp" in argv else "exec")
        return create(argv, **kwargs)  # The shared launch seam starts the script on Windows.
    monkeypatch.setattr(processes.HOST_PLATFORM, "create_cli_process", recorded_create)
    env.host = Host()
    result, trace = cases.execute(env)
    if closed:
        assert order == ["guard"]
        assert result.failure.failure_class == "authorization"
        assert cases.detail(env, result)["failure_code"] == "self_test_resources_unavailable"
    else:
        assert order == ["guard", "version", "guard", "list", "guard", "exec"]
        assert result.terminal_status == "completed" and trace["byte_capture_exact"]
        assert trace["cli_version"] == "codex synthetic-local-process"
        assert cases.cli_stream_bytes(trace, "stdout") == cases.raw_events([cases.message(), cases.terminal()])


@pytest.mark.parametrize("kind", ["not_callable", "getter_failure"])
def test_declared_broken_optional_control_is_not_treated_as_absent(tmp_path, kind):
    env = cases.environment(tmp_path, lambda _: pytest.fail("bad control reached Provider"))
    env.request = self_test_request(env.request)
    class Host(cases.native._RecordingHost):
        def validate_self_test_binding(self, request, **ports):
            assert request == env.request
        def guard_self_test_launch(self, request, launch, **ports):
            return launch()
        @property
        def self_test_cancel_requested(self):
            if kind == "getter_failure":
                raise AttributeError("declared control getter failed")
            return None
    env.host = Host()
    with pytest.raises(AttributeError if kind == "getter_failure" else TypeError):
        cases.execute(env)
    assert not env.calls


def test_optional_controls_are_forwarded_without_replacing_the_guard(tmp_path):
    effects, queries = [], []
    def invocation(fields):
        assert fields["cancel_requested"]() is False
        assert fields["user_cancel_requested"]() is False
        fields["launch_guard"](lambda: effects.append("guarded"))
        return cases.process()
    env = cases.environment(tmp_path, invocation)
    env.request = self_test_request(env.request)
    class Host(cases.native._RecordingHost):
        def validate_self_test_binding(self, request, **ports):
            assert request == env.request
        def guard_self_test_launch(self, request, launch, **ports):
            self.validate_self_test_binding(request, **ports)
            return launch()
        def self_test_cancel_requested(self, request, *, user=False):
            assert request == env.request
            queries.append(user)
            return False
    env.host = Host()
    result, trace = cases.execute(env)
    assert result.terminal_status == "completed" and effects == ["guarded"]
    assert False in queries and True in queries
    assert result.input_tokens == 12 and trace["byte_capture_exact"]


def test_optional_control_does_not_replace_a_missing_required_launch_guard(tmp_path):
    env = cases.environment(tmp_path, lambda _: pytest.fail("missing guard reached Provider"))
    env.request = self_test_request(env.request)
    class Host(cases.native._RecordingHost):
        def validate_self_test_binding(self, request, **ports):
            pass
        def self_test_cancel_requested(self, request, *, user=False):
            return False
    env.host = Host()
    with pytest.raises(SelfTestResourceUnavailableError, match="launch guard"):
        cases.execute(env)
    assert not env.calls


@pytest.mark.parametrize("failure_on", [1, 2])
def test_codex_handoff_checks_control_once_and_keeps_observed_result(tmp_path, failure_on):
    env = cases.environment(tmp_path, lambda _: cases.process())
    env.request = self_test_request(env.request)
    queries = []
    class Host(cases.native._RecordingHost):
        def validate_self_test_binding(self, request, **ports):
            assert request == env.request
        def guard_self_test_launch(self, request, launch, **ports):
            return launch()
        def self_test_cancel_requested(self, request, *, user=False):
            queries.append(user)
            if len(queries) == failure_on:
                raise RuntimeError("trusted cancellation control unavailable")
            return False
    env.host = Host()
    result, trace = cases.execute(env)
    if failure_on == 1:
        assert result.terminal_status == "failed" and not result.outputs
        assert result.failure.failure_class == "dependency_unavailable"
        assert cases.detail(env, result)["failure_code"] == "ADAPTER_BINDING_UNAVAILABLE"
        assert "trusted cancellation control unavailable" in cases.detail(env, result)["provider_error_message"]
    else:
        assert result.terminal_status == "completed" and result.failure is None
    assert result.input_tokens == 12 and result.output_tokens == 5
    assert cases.cli_stream_bytes(trace, "stdout") == cases.raw_events([cases.message(), cases.terminal()])
    assert len(env.calls) == len(queries) == 1
