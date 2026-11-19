"""Closed host-mode configuration; real process coverage is separately recorded."""
from pathlib import Path

import pytest

from agent_runtime import module_requirements_preset, run_local_workflow_test
from agent_runtime.execution.execution_local_invocation import _execution_profile_for_requirements
from agent_runtime.invocation import invocation_codex_module_invocation as codex
from agent_runtime.testing.execution_local_test_run import build_parser
from dataclasses import replace

pytestmark = pytest.mark.deterministic


def profile(**changes):
    requirements = replace(module_requirements_preset("code_change"), **changes)
    return _execution_profile_for_requirements(requirements, transport_kind="codex_cli",
        model_id="gpt-6-astra", reasoning_profile="xhigh")


def test_host_native_command_keeps_model_tools_cwd_and_omits_permission_profile():
    actual = codex.build_workspace_command(profile=profile(), main_folder=Path("/project"),
        codex_bin="/codex", permission_profile=None, codex_execution_mode="host")
    assert actual[actual.index("-s") + 1] == "danger-full-access"
    assert actual[actual.index("-C") + 1] == str(Path("/project"))
    assert actual[actual.index("-m") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="xhigh"' in actual
    assert 'approval_policy="never"' in actual
    assert "features.shell_tool=true" in actual and "allow_login_shell=false" in actual
    assert "mcp_servers={}" in actual
    assert not any(value.startswith(("default_permissions=", "permissions.")) for value in actual)


def test_default_still_uses_configured_permissions():
    permission = codex.codex_permission_profile(write_folders=(Path("/project"),), network_policy="provider_managed")
    kwargs = dict(profile=profile(), main_folder=Path("/project"), codex_bin="/codex", permission_profile=permission)
    default = codex.build_workspace_command(**kwargs)
    assert default == codex.build_workspace_command(**kwargs, codex_execution_mode="configured")
    assert "-s" not in default and 'default_permissions="runtime_attempt"' in default


@pytest.mark.parametrize("mode", [None, "", "full", True, 1, [], {}])
def test_invalid_mode_fails_before_setup(tmp_path, mode):
    root = tmp_path / "must_not_create"
    with pytest.raises(ValueError, match="codex_execution_mode"):
        run_local_workflow_test(root, "missing", input_payload={}, codex_execution_mode=mode)
    assert not root.exists()


@pytest.mark.parametrize("changes", [
    {"network_policy": "denied"},
    {"attempt_workspace_policy": "own_draft_read_write"},
    {"tool_policy": ("read", "shell")},
    {"tool_policy": ("edit", "mcp__data", "read", "search", "shell")},
])
def test_host_refuses_incompatible_requirements(changes):
    with pytest.raises(codex.CodexCapabilityUnsupportedError) as failure:
        codex.validate_codex_execution_mode("host", profile=profile(**changes))
    assert failure.value.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED"


def test_host_refuses_another_transport_and_tool_free():
    claude = _execution_profile_for_requirements(module_requirements_preset("code_change"))
    no_tools = _execution_profile_for_requirements(module_requirements_preset("structured_output"),
        transport_kind="codex_cli", model_id="gpt-6-astra", reasoning_profile="xhigh")
    for item in (claude, no_tools):
        with pytest.raises(codex.CodexCapabilityUnsupportedError):
            codex.validate_codex_execution_mode("host", profile=item)


def test_host_rejects_extra_configuration_and_cli_is_closed():
    with pytest.raises(codex.CodexCapabilityUnsupportedError):
        codex.build_workspace_command(profile=profile(), main_folder=Path("/project"), codex_bin="/codex",
            permission_profile={"extends": ":read-only"}, codex_execution_mode="host")
    parser = build_parser()
    args = ["--root", "/root", "--workflow", "example", "--input", "/input.json"]
    assert parser.parse_args(args).codex_execution_mode == "configured"
    assert parser.parse_args([*args, "--codex-execution-mode", "host"]).codex_execution_mode == "host"
    with pytest.raises(SystemExit):
        parser.parse_args([*args, "--codex-execution-mode", "arbitrary"])
