"""Real managed child: a parent deadline stops the Runtime-managed child invocation.

The contract is Runtime's outer one: the child Attempt receives at most the parent's remaining
budget, the Runtime-started child CLI process is stopped and confirmed when that budget expires,
and the parent records the timeout with its own cause. Processes the CLI may start internally
are the CLI's responsibility and are not inspected here.
"""

import json
import os
from pathlib import Path
import shutil
import time

import pytest

from test_agent_runtime_provider_tools import UNIX_BRIDGE


@pytest.mark.real_run
@UNIX_BRIDGE
@pytest.mark.skipif(os.environ.get("AGENT_RUNTIME_REAL_RUN") != "1",
                    reason="unverified: set AGENT_RUNTIME_REAL_RUN=1 with an authenticated Claude CLI")
def test_parent_expiry_stops_the_runtime_managed_child_invocation(tmp_path, monkeypatch):
    claude_cli = os.environ.get("AGENT_RUNTIME_CLAUDE_BIN") or shutil.which("claude")
    if not claude_cli:
        pytest.skip("unverified: an installed Claude CLI is required")
    evidence = Path(os.environ.get("AGENT_RUNTIME_BUDGET_EVIDENCE_DIR", str(tmp_path))) / "nested_expiry_record.json"
    if evidence.exists():
        raise FileExistsError("provide a fresh evidence directory; existing runs are immutable")
    from agent_runtime.testing import conformance_agent_execution as execution
    from agent_runtime.testing.conformance_agent_examples import _module, _REVIEW_INPUT, _REVIEW_OUTPUT

    original_factory = execution._ExampleToolFactory
    callback_timing = {}

    def waiting_factory(definition, invoke):
        if definition.tool_name != "review_candidate":
            return original_factory(definition, invoke)

        def invoke_near_parent_deadline(session, payload):
            parent = session.parent_run
            assert parent is not None and parent.attempt_id == session.request.attempt_id
            callback_timing["remaining_on_entry_seconds"] = parent._budget.remaining()
            while parent._budget.remaining() > 8:
                time.sleep(0.05)
            callback_timing["remaining_before_child_seconds"] = parent._budget.remaining()
            return invoke(session, payload)

        return original_factory(definition, invoke_near_parent_deadline)

    def slow_child():
        # A long tool-free generation keeps the child call active past the parent's deadline.
        return _module("task_reviewer",
            "Before answering, write a careful 4000-word review of every required fact in the draft, "
            "then return accepted/reason JSON.", _REVIEW_INPUT, _REVIEW_OUTPUT, tools=(), draft=False)

    monkeypatch.setattr(execution, "build_example_reviewer", slow_child)
    monkeypatch.setattr(execution, "_ExampleToolFactory", waiting_factory)
    root = tmp_path / "root"
    parameters = root / ".runtime/execution_parameters"
    (parameters / "workflows").mkdir(parents=True)
    (parameters / "workspace.json").write_text(json.dumps({
        "schema_version": "runtime_execution_parameters_v2", "transport_kind": "claude_cli",
        "model_id": "claude-sonnet-5", "reasoning_profile": "low", "run_timeout_seconds": 60,
    }), encoding="utf-8")
    (parameters / "workflows/capability_task_reviewer.json").write_text(json.dumps({
        "schema_version": "runtime_execution_parameters_v2", "run_timeout_seconds": 3600,
    }), encoding="utf-8")

    try:
        result = execution.run_agent_example(root, "agent_evaluation_example", cli_path=claude_cli)
    except BaseException as exc:
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps({"execution_error": {"type": type(exc).__name__, "message": str(exc)},
            "callback_timing": callback_timing}, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"runtime_record": result, "callback_timing": callback_timing},
                                   ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")

    assert 0 < callback_timing["remaining_before_child_seconds"] <= 8
    assert result["child_executions"], "No recorded managed child execution"
    child = result["child_executions"][0]["record"]
    parent = next(row for row in result["execution"]["nodes"]
                  if row["dispatch"]["current_state_id"] == "tested_agent")
    # The child ran on the parent's remaining budget, not its own one-hour selection.
    assert child["execution_budget"]["limiting_scope"] == "parent"
    assert child["execution_budget"]["parent_attempt_id"] == parent["attempt_id"]
    assert child["execution_budget"]["effective_timeout_seconds"] <= 8
    # The Runtime-started child process was stopped at that deadline and its stop confirmed.
    assert child["status"] != "completed" and child["failure_class"] == "timeout", child.get("failure_detail")
    assert child["failure_detail"]["failure_code"] == "claude_cli_timeout"
    assert child["provider_trace"]["stop_reason"] == "timeout"
    assert child["provider_trace"].get("cleanup_error") is None
    # The parent keeps its own timeout cause and the example releases its resources.
    assert parent["failure_class"] == "timeout"
    assert parent["failure_detail"]["failure_code"] == "claude_cli_timeout"
    assert result["status"] != "completed" and result["resources_cleaned"], result.get("failure")
    assert result["failure"]["error_type"] == "TimeoutError"
    assert result["execution_budget"]["elapsed_seconds"] >= 60
