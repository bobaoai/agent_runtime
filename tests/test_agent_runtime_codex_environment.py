"""v4 isolation and credential-reference tests using synthetic local resources."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

import pytest

from agent_runtime.contracts.invocation_adapter_definition import (
    AuthorizedAgentExecutionRequest, SelfTestResourceUnavailableError,
)
from agent_runtime.invocation import invocation_codex_environment as private
from agent_runtime.invocation import invocation_codex_module_invocation as codex
import test_agent_runtime_codex_execution_outcomes as cases


@pytest.fixture
def credential(tmp_path):
    source = tmp_path / "provider" / "auth.json"
    source.parent.mkdir()
    source.write_text("SYNTHETIC_PRIVATE_CREDENTIAL")
    return source


def test_private_state_has_only_credential_reference_and_filtered_child_environment(tmp_path, credential):
    parent = {"HOME": "/synthetic/home", "CODEX_HOME": str(credential.parent), "PATH": "/bin",
        "CODEX_THREAD_ID": "exclude", "PLATFORM_DATABASE_URL": "secret", "OPENAI_API_KEY": "secret",
        "OTHER_PASSWORD": "secret", "HTTP_PROXY": "http://proxy.example", "LANG": "C"}
    before = dict(parent)
    with private.prepare_codex_environment(workspace_root=tmp_path / "workspace", source_environment=parent) as child:
        state = Path(child["CODEX_HOME"])
        assert stat.S_IMODE(state.stat().st_mode) == 0o700
        assert sorted(p.name for p in state.iterdir()) == ["auth.json"]
        assert (state / "auth.json").is_symlink() and (state / "auth.json").readlink() == credential
        assert set(child) == {"HOME", "CODEX_HOME", "PATH", "HTTP_PROXY", "LANG"}
        assert child["CODEX_HOME"] != parent["CODEX_HOME"]
    assert parent == before and not state.exists()
    assert credential.read_text() == "SYNTHETIC_PRIVATE_CREDENTIAL"


def test_standard_home_and_explicit_source_are_single_choices(tmp_path, credential):
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    auth = home / ".codex" / "auth.json"
    auth.write_text("HOME_CREDENTIAL")
    for explicit, env, expected in [
        (None, {"HOME": str(home)}, auth),
        (credential, {"HOME": str(home), "CODEX_HOME": "/missing"}, credential),
    ]:
        with private.prepare_codex_environment(workspace_root=tmp_path / "workspace", auth_file=explicit,
                                               source_environment=env) as child:
            assert (Path(child["CODEX_HOME"]) / "auth.json").readlink() == expected
    with pytest.raises(FileNotFoundError):
        with private.prepare_codex_environment(workspace_root=tmp_path / "workspace",
                source_environment={"HOME": str(home), "CODEX_HOME": str(tmp_path / "missing")}):
            pytest.fail("must not fall back to HOME credentials")


@pytest.mark.parametrize("bad_source", ["missing", "directory", "symlink"])
def test_unsupported_credential_does_not_create_state(tmp_path, credential, monkeypatch, bad_source):
    path = tmp_path / "source"
    if bad_source == "directory":
        path.mkdir()
    elif bad_source == "symlink":
        path.symlink_to(credential)
    monkeypatch.setattr(private.tempfile, "mkdtemp", lambda **kwargs: pytest.fail("no state for bad credentials"))
    with pytest.raises(FileNotFoundError):
        with private.prepare_codex_environment(workspace_root=tmp_path / "workspace", auth_file=path,
                                               source_environment={}):
            pytest.fail("bad credential accepted")


def test_state_inside_workspace_cleanup_tree_is_rejected_before_linking(tmp_path, credential, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = private.tempfile.mkdtemp
    created = []

    def create(**kwargs):
        state = original(**kwargs, dir=workspace)
        created.append(Path(state))
        return state

    monkeypatch.setattr(private.tempfile, "mkdtemp", create)
    with pytest.raises(ValueError, match="cleanup tree"):
        with private.prepare_codex_environment(workspace_root=workspace, auth_file=credential,
                                               source_environment={}):
            pytest.fail("unsafe state placement")
    assert len(created) == 1 and not created[0].exists()
    assert credential.read_text() == "SYNTHETIC_PRIVATE_CREDENTIAL"


@pytest.mark.parametrize("replacement", ["file", "other_link", "directory"])
def test_replaced_auth_survives_outer_workspace_cleanup(tmp_path, credential, replacement):
    retained = None
    other = tmp_path / "other.json"
    other.write_text("OTHER_CREDENTIAL")
    try:
        with tempfile.TemporaryDirectory(dir=tmp_path) as workspace:
            with pytest.raises(private.CodexEnvironmentCleanupError) as error:
                with private.prepare_codex_environment(workspace_root=Path(workspace), auth_file=credential,
                                                       source_environment={}) as child:
                    retained = Path(child["CODEX_HOME"])
                    auth = retained / "auth.json"
                    auth.unlink()
                    if replacement == "file":
                        auth.write_text("REFRESHED_PRIVATE_CREDENTIAL")
                    elif replacement == "other_link":
                        auth.symlink_to(other)
                    else:
                        auth.mkdir()
            assert error.value.recovery_path == retained
        assert not Path(workspace).exists() and retained.is_dir()
        assert stat.S_IMODE(retained.stat().st_mode) == 0o700
        assert credential.read_text() == "SYNTHETIC_PRIVATE_CREDENTIAL"
        assert other.read_text() == "OTHER_CREDENTIAL"
        if replacement == "file":
            assert (retained / "auth.json").read_text() == "REFRESHED_PRIVATE_CREDENTIAL"
    finally:
        if retained is not None:
            shutil.rmtree(retained)


@pytest.mark.parametrize("failure", [RuntimeError("failed"), KeyboardInterrupt()])
def test_normal_reference_cleanup_also_happens_on_failure(tmp_path, credential, failure):
    with pytest.raises(type(failure)):
        with private.prepare_codex_environment(workspace_root=tmp_path / "workspace", auth_file=credential,
                                               source_environment={}) as child:
            state = Path(child["CODEX_HOME"])
            raise failure
    assert not state.exists() and credential.is_file()


@pytest.mark.parametrize("method", ["unlink", "readlink"])
def test_reference_cleanup_oserror_always_reports_retained_state(tmp_path, credential, monkeypatch, method):
    retained = None
    original = getattr(Path, method)
    try:
        with monkeypatch.context() as patch:
            def refuse(path, *args, **kwargs):
                if retained is not None and path == retained / "auth.json":
                    raise OSError("synthetic reference cleanup failure")
                return original(path, *args, **kwargs)
            patch.setattr(Path, method, refuse)
            with pytest.raises(private.CodexEnvironmentCleanupError) as failure:
                with private.prepare_codex_environment(workspace_root=tmp_path / "workspace", auth_file=credential,
                                                       source_environment={}) as child:
                    retained = Path(child["CODEX_HOME"])
            assert failure.value.recovery_path == retained
        assert retained.is_dir() and (retained / "auth.json").is_symlink()
        assert credential.read_text() == "SYNTHETIC_PRIVATE_CREDENTIAL"
    finally:
        if retained is not None:
            shutil.rmtree(retained)


def test_configuration_is_pure_and_contains_no_host_auth_or_model_defaults(tmp_path, monkeypatch):
    compiled = cases.native._compile_native_module(tmp_path, executor_adapter_revision="v4",
                                                   model_id="explicit-model", reasoning_profile="xhigh")
    monkeypatch.setattr(private.tempfile, "mkdtemp", lambda **kw: pytest.fail("pure check created resources"))
    profile = compiled.execution_profile
    expected = codex._execution_expectation(profile)
    assert expected.executor_adapter_revision == "v4"
    argv = codex.build_command(profile=profile, workspace=tmp_path, codex_bin="host-codex")
    assert argv[:4] == ["host-codex", "exec", "-m", "explicit-model"]
    assert argv[-1] == "-" and "--output-schema" not in argv
    settings = [argv[index+1] for index, value in enumerate(argv) if value == "-c"]
    for required in ["agents.enabled=false", "skills.include_instructions=false", "mcp_servers={}",
        "features.skip_host_skill_discovery=true", 'cli_auth_credentials_store="file"',
        'features.code_mode.excluded_tool_namespaces=["functions","collaboration","clock"]',
        "features.skill_search=false", "features.memories=false", "features.hooks=false", "features.view_image=false"]:
        assert required in settings
    assert not any("auth.json" in value or "gpt-6-astra" in value for value in argv)


def test_v3_is_readable_but_cannot_execute_as_v4(tmp_path):
    profile = cases.native._compile_native_module(tmp_path, executor_adapter_revision="v3").execution_profile
    profile.validate()
    with pytest.raises(ValueError, match="v4"):
        codex._execution_expectation(profile)


def test_old_invoker_signature_rejected_before_resources_or_call(tmp_path, monkeypatch):
    calls = []
    env = cases.environment(tmp_path, lambda _: cases.process())
    def old(*, argv, prompt, cwd, timeout_seconds):
        calls.append(argv)
        return cases.process()
    env.executor._invoker = old
    monkeypatch.setattr(codex, "prepare_codex_environment", lambda **kw: pytest.fail("resources before contract check"))
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
                "codex 0.test" if "--version" in fields["argv"] else cases.raw_events([cases.message(), cases.terminal()]).decode(), "")
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
        assert len(effects) == 2 and result.terminal_status == "completed"
        assert trace["cli_version"] == "codex 0.test"


def test_refresh_cleanup_returns_private_diagnostic_and_preserves_state(tmp_path):
    retained = None
    def invocation(fields):
        nonlocal retained
        retained = Path(fields["environment"]["CODEX_HOME"])
        auth = retained / "auth.json"
        auth.unlink()
        auth.write_text("REFRESHED_PRIVATE_CREDENTIAL")
        return cases.process()
    env = cases.environment(tmp_path, invocation)
    try:
        result, trace = cases.execute(env)
        assert result.terminal_status == "failed" and not result.outputs
        diagnostic = cases.detail(env, result)
        assert diagnostic["failure_code"] == "codex_cli_cleanup_failed"
        assert str(retained) in diagnostic["provider_error_message"]
        assert "REFRESHED_PRIVATE_CREDENTIAL" not in json.dumps(trace) + json.dumps(diagnostic)
        assert retained.is_dir() and (retained / "auth.json").read_text() == "REFRESHED_PRIVATE_CREDENTIAL"
        assert result.input_tokens == 12
    finally:
        if retained is not None:
            shutil.rmtree(retained)


def test_runtime_does_not_open_credential_contents(tmp_path, credential, monkeypatch):
    original = Path.open
    def checked_open(path, *args, **kwargs):
        if Path(path) == credential or Path(path).name == "auth.json":
            pytest.fail("Runtime must not open credential contents")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", checked_open)
    with private.prepare_codex_environment(workspace_root=tmp_path / "workspace", auth_file=credential,
                                           source_environment={}) as child:
        assert (Path(child["CODEX_HOME"]) / "auth.json").is_symlink()


def test_each_call_gets_fresh_state_and_never_copies_ambient_files(tmp_path, credential):
    (credential.parent / "AGENTS.md").write_text("UNWANTED_INSTRUCTIONS")
    (credential.parent / "config.toml").write_text("UNWANTED_CONFIG")
    states = []
    for _ in range(2):
        with private.prepare_codex_environment(workspace_root=tmp_path / "workspace", auth_file=credential,
                                               source_environment={}) as child:
            state = Path(child["CODEX_HOME"])
            states.append(state)
            assert [entry.name for entry in state.iterdir()] == ["auth.json"]
    assert states[0] != states[1] and all(not state.exists() for state in states)


def test_auth_removed_by_cli_does_not_delete_original_source(tmp_path, credential):
    with private.prepare_codex_environment(workspace_root=tmp_path / "workspace", auth_file=credential,
                                           source_environment={}) as child:
        state = Path(child["CODEX_HOME"])
        (state / "auth.json").unlink()
    assert not state.exists() and credential.is_file()


def test_preserved_refresh_is_not_lost_on_provider_timeout(tmp_path):
    retained = None
    def invoke(fields):
        nonlocal retained
        retained = Path(fields["environment"]["CODEX_HOME"])
        reference = retained / "auth.json"
        reference.unlink()
        reference.write_text("REFRESH_AFTER_TIMEOUT")
        raise subprocess.TimeoutExpired(fields["argv"], 1, output=cases.raw_events([cases.terminal()]), stderr=b"prefix")
    env = cases.environment(tmp_path, invoke)
    try:
        result, trace = cases.execute(env)
        assert result.terminal_status == "failed" and result.failure.retry_disposition_id == "retry_denied"
        assert trace["adapter_failure"]["failure_class"] == "timeout"
        assert cases.detail(env, result)["failure_code"] == "codex_cli_cleanup_failed"
        assert result.input_tokens == 12 and (retained / "auth.json").read_text() == "REFRESH_AFTER_TIMEOUT"
    finally:
        if retained is not None:
            shutil.rmtree(retained)


def test_version_capture_survives_later_timeout(tmp_path, monkeypatch):
    env = cases.environment(tmp_path, lambda _: cases.process())
    env.executor._invoker = codex._default_invoke
    def runner(**fields):
        if "--version" in fields["argv"]:
            return subprocess.CompletedProcess(fields["argv"], 0, "codex fixture-v4\n", "")
        raise subprocess.TimeoutExpired(fields["argv"], 1, output=cases.raw_events([cases.terminal()]), stderr=b"partial")
    monkeypatch.setattr(codex, "run_cli_process", runner)
    result, trace = cases.execute(env)
    assert result.failure.failure_class == "timeout"
    assert trace["cli_version"] == "codex fixture-v4"
    assert result.input_tokens == 12


def test_missing_live_host_rejected_before_private_state_preparation(tmp_path, monkeypatch):
    env = cases.environment(tmp_path, lambda _: cases.process())
    env.request = self_test_request(env.request)
    monkeypatch.setattr(codex, "prepare_codex_environment", lambda **kw: pytest.fail("state prepared before live host"))
    with pytest.raises(SelfTestResourceUnavailableError):
        env.executor.execute(env.request, env.host)
    assert not env.calls


@pytest.mark.parametrize("closed", [False, True])
def test_default_invoker_real_process_launch_is_guarded(tmp_path, monkeypatch, closed):
    import sys
    from agent_runtime.invocation import invocation_process_execution as processes
    executable = tmp_path / "synthetic_codex"
    executable.write_text(f"#!{sys.executable}\n" +
        "import sys\n"
        "if sys.argv[1:] == ['--version']:\n"
        "    print('codex synthetic-local-process')\n"
        "elif sys.argv[1] == 'exec':\n"
        "    sys.stdin.read()\n"
        f"    print({cases.raw_events([cases.message(), cases.terminal()]).decode()!r}, end='')\n"
        "else:\n"
        "    raise SystemExit(3)\n")
    executable.chmod(0o700)
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

    popen = processes.subprocess.Popen
    def recorded_popen(argv, **kwargs):
        order.append("version" if "--version" in argv else "exec")
        return popen(argv, **kwargs)
    monkeypatch.setattr(processes.subprocess, "Popen", recorded_popen)
    env.host = Host()
    result, trace = cases.execute(env)
    if closed:
        assert order == ["guard"]
        assert result.failure.failure_class == "authorization"
        assert cases.detail(env, result)["failure_code"] == "self_test_resources_unavailable"
    else:
        assert order == ["guard", "version", "guard", "exec"]
        assert result.terminal_status == "completed" and trace["byte_capture_exact"]
        assert trace["cli_version"] == "codex synthetic-local-process"
        assert cases.cli_stream_bytes(trace, "stdout") == cases.raw_events([cases.message(), cases.terminal()])
