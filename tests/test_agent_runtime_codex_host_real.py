"""Actual Runtime refusal paths. No Provider/process substitute and no model call."""
import hashlib
import os
from pathlib import Path

import pytest

from agent_runtime import (
    Module, RuntimeModulePlugin, RuntimeReleaseRegistry, module_requirements_preset,
    prepare_local_workflow_module, register_runtime_module_plugin, run_local_workflow_test,
)
from agent_runtime.execution import execution_local_invocation as local
from agent_runtime.execution.execution_content_staging import InMemoryCellArtifactStore
from agent_runtime.ledger.ledger_lineage_recording import InMemoryModuleExecutionLedger
from agent_runtime.invocation import invocation_codex_module_invocation as codex
from agent_runtime.invocation.invocation_local_resource_preparation import capture_local_resources
from test_agent_runtime_module_authoring import _task_project, SKILL_ID

pytestmark = [pytest.mark.real_run, pytest.mark.skipif(
    os.environ.get("AGENT_RUNTIME_REAL_RUN") != "1" or not os.environ.get("AGENT_RUNTIME_CODEX_BIN"),
    reason="unverified: require AGENT_RUNTIME_REAL_RUN=1 and the actual AGENT_RUNTIME_CODEX_BIN locator")]


@pytest.fixture
def actual_host(tmp_path):
    source = _task_project(tmp_path / "s", module_id="summarize_note")
    module = Module.from_registration(source, skill_id=SKILL_ID, module_id="summarize_note",
                                     execution_requirements=module_requirements_preset("code_change"))
    workflow = Module.to_workflow(module.export(module_version="v1")).export()
    root, project = tmp_path / "r", tmp_path / "p"
    project.mkdir()
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        "host_refusal", "v1", workflow.origin_bundle), root=root)
    return root, project, os.environ["AGENT_RUNTIME_CODEX_BIN"]


@pytest.mark.parametrize("resource", ["materials", "dependencies", "commands"])
def test_host_resource_refused_before_provider(actual_host, tmp_path, resource):
    root, project, executable = actual_host
    folder = tmp_path / "resource"
    folder.mkdir()
    arguments = {"materials": {"material_root": folder},
                 "dependencies": {"read_only_dependencies": (folder,)},
                 "commands": {"commands": ({"command_id": "check", "argv": ["python", "-V"],
                                               "cwd": "scratch", "timeout_seconds": 10},)}}[resource]
    with pytest.raises(codex.CodexCapabilityUnsupportedError) as failure:
        run_local_workflow_test(root, "summarize_note", version="v1", input_payload={},
            transport_kind="codex_cli", model_id="gpt-6-astra", reasoning_profile="xhigh",
            cli_path=executable, project_directory=project, codex_execution_mode="host", **arguments)
    assert failure.value.error_code == "ADAPTER_CAPABILITY_UNSUPPORTED"
    assert not (root / ".runtime/attempt_workspaces").exists()


def test_host_adapter_material_terminal(actual_host, tmp_path):
    root, project, executable = actual_host
    saved, selection = prepare_local_workflow_module(root, "summarize_note", version="v1",
        transport_kind="codex_cli", model_id="gpt-6-astra", reasoning_profile="xhigh")
    binding = selection.policy_document()["bindings"][0]
    profile = saved.registry.get_execution_profile(binding["execution_profile_release_ref"], binding["execution_profile_release_sha256"])
    materials = tmp_path / "m"
    materials.mkdir()
    (materials / "note.txt").write_bytes(b"fixed material")
    body = capture_local_resources(profile=profile, material_root=materials, material_files=(
        {"relative_path": "note.txt", "sha256": hashlib.sha256(b"fixed material").hexdigest(), "executable": False},))
    artifacts = InMemoryCellArtifactStore()
    ledger = InMemoryModuleExecutionLedger()
    workspace = tmp_path / "attempts"
    workspace.mkdir()
    adapter = codex.CodexCliAgentWorkspaceModuleExecutor(release_registry=saved.registry,
        artifact_host=artifacts, workspace_root=workspace, codex_bin=executable,
        project_directory=project, codex_execution_mode="host")
    _, record = local._run_prepared_workflow_node(registry=saved.registry, workflow=saved.release,
        selection=selection, input_payload={}, idempotency_key="host_material_refusal", artifact_host=artifacts,
        ledger=ledger, workspace_root=workspace, adapter=adapter, local_resources=body)
    assert record["status"] == "failed"
    assert record["failure_detail"].get("failure_code") == "ADAPTER_CAPABILITY_UNSUPPORTED", record["failure_detail"]
    trace = record["provider_trace"]
    assert trace["adapter_failure"]["failure_code"] == "ADAPTER_CAPABILITY_UNSUPPORTED"
    assert "argv" not in trace and "raw_streams" not in trace and "returncode" not in trace
