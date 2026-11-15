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
