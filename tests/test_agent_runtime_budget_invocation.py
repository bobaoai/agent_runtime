"""Actual process and public-kernel regressions for run-budget admission.

The custom invoker delegates to the installed Codex CLI. It does not synthesize
Provider output. The process guard orders a real parent exit before observation.
"""
import json
import os
from pathlib import Path
import sys
import time

import psutil
import pytest

from agent_runtime.invocation.invocation_process_execution import run_cli_process

_REAL = pytest.mark.skipif(os.environ.get("AGENT_RUNTIME_REAL_RUN") != "1",
                           reason="unverified: set AGENT_RUNTIME_REAL_RUN=1 for actual processes")


@pytest.mark.real_run
@_REAL
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_exited_parent_group_is_cleaned_before_descendant_observation(tmp_path):
    marker = tmp_path / "child.json"
    # Actual child inherits the newly created group but not any captured pipe.
    program = ("import json,subprocess,sys;from pathlib import Path;import psutil;"
        "child=subprocess.Popen([sys.executable,'-I','-c','import time;time.sleep(60)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
        f"Path({str(marker)!r}).write_text(json.dumps([child.pid,psutil.Process(child.pid).create_time()]))")
    def after_parent_exit(launch):
        process = launch()
        assert process.wait(timeout=10) == 0
        return process
    survivor = None
    try:
        result = run_cli_process(argv=[sys.executable,"-c",program], prompt="", cwd=tmp_path,
            timeout_seconds=20, environment=dict(os.environ), launch_guard=after_parent_exit)
        assert result.returncode == 0
        pid, created = json.loads(marker.read_text())
        try:
            child = psutil.Process(pid)
            if child.create_time() != created:
                return  # Original child no longer exists; never signal a reused PID.
            _, alive = psutil.wait_procs([child], timeout=3)
            survivor = next((p for p in alive if p.status() != psutil.STATUS_ZOMBIE), None)
        except psutil.NoSuchProcess:
            pass
        assert survivor is None, "same-group child survived Runtime cleanup"
    finally:
        # Test hygiene after a failed regression; never counted as Runtime cleanup.
        if survivor is not None and survivor.is_running():
            survivor.kill()
            psutil.wait_procs([survivor], timeout=3)


@pytest.mark.real_run
@_REAL
@pytest.mark.skipif(not os.environ.get("AGENT_RUNTIME_CODEX_BIN"),
                    reason="unverified: AGENT_RUNTIME_CODEX_BIN must identify an authenticated Codex CLI")
def test_exact_profile_invoker_without_budget_remains_compatible(tmp_path):
    from agent_runtime import prepare_local_workflow_module
    from agent_runtime.execution import execution_local_invocation as local
    from agent_runtime.execution.execution_run_budget import RunBudget
    from agent_runtime.invocation import invocation_codex_module_invocation as codex
    from test_agent_runtime_execution_parameters import _module_root

    root = _module_root(tmp_path)
    saved, variant = prepare_local_workflow_module(root, "summarize_note", transport_kind="codex_cli",
        model_id="gpt-6-astra", reasoning_profile="xhigh")
    calls = []
    def existing_invoker(*, argv, prompt, cwd, timeout_seconds, environment, launch_guard=None,
                         cancel_requested=None, user_cancel_requested=None):
        calls.append(cwd)
        return codex._default_invoke(argv=argv, prompt=prompt, cwd=cwd, timeout_seconds=timeout_seconds,
            environment=environment, launch_guard=launch_guard, cancel_requested=cancel_requested,
            user_cancel_requested=user_cancel_requested)

    artifacts, ledger = local.InMemoryCellArtifactStore(), local.InMemoryModuleExecutionLedger()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    adapter = codex.CodexCliModuleExecutor(release_registry=saved.registry, artifact_host=artifacts,
        workspace_root=workspace, invoker=existing_invoker, codex_bin=os.environ["AGENT_RUNTIME_CODEX_BIN"])
    common = dict(registry=saved.registry, workflow=saved.release, selection=variant, input_payload={},
        artifact_host=artifacts, ledger=ledger, workspace_root=workspace, adapter=adapter)
    result, record = local._run_prepared_workflow_node(**common, idempotency_key="exact-profile")
    assert record["status"] == "completed", record.get("failure_detail")
    assert len(calls) == 1
    assert "execution_budget" not in record
    # Same real binding, but this invocation explicitly activates a budget.
    # An invoker unable to enforce it must be rejected before any private state
    # or Attempt directory is made; no unsupported Provider run is attempted.
    budget = RunBudget(1200, started=time.monotonic())
    created = list(workspace.rglob("*"))
    _, rejected = local._run_prepared_workflow_node(**common, idempotency_key="budget-incompatible", run_budget=budget)
    assert rejected["status"] == "failed"
    assert "deadline_monotonic" in json.dumps(rejected["failure_detail"])
    assert rejected["provider_trace"] is None
    budget.close()
    assert len(calls) == 1
    assert list(workspace.rglob("*")) == created
    evidence = os.environ.get("AGENT_RUNTIME_BUDGET_EVIDENCE_DIR")
    if evidence:
        with (Path(evidence) / "exact_profile_invoker_record.json").open("x") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
        with (Path(evidence) / "budget_invoker_rejection_record.json").open("x") as stream:
            json.dump(rejected, stream, ensure_ascii=False, indent=2, allow_nan=False)
