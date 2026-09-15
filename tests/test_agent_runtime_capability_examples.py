"""Definition checks do not stand in for the examples' real execution gates."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from agent_runtime.registry import RuntimeReleaseRegistry
from agent_runtime.testing.conformance_agent_examples import (
    build_agent_capability_example, build_agent_evaluation_example, build_example_reviewer,
)


def test_canonical_example_is_target_independent_and_has_real_graph_closure():
    exported = build_agent_capability_example()
    assert exported == build_agent_capability_example()
    assert len(exported.origin_bundle.modules) == 6
    assert exported.origin_bundle.execution_profiles == ()
    assert exported.origin_bundle.execution_variant_policies == ()
    assert exported.workflow_release.parallel_groups[0].branch_node_ids == ("reviewer_a", "reviewer_b")
    registries = [RuntimeReleaseRegistry(), RuntimeReleaseRegistry()]
    for registry in registries:
        registry.register_bundle(exported.origin_bundle)
        assert registry.get_workflow(exported.workflow_release.release_ref,
                                     exported.workflow_release.release_sha256) == exported.workflow_release
    assert all(module.reviewer_defaults is None for module in exported.origin_bundle.modules)
    query = next(module for module in exported.origin_bundle.modules if module.module_id == "capability_gateway_researcher")
    assert query.declared_operation_ids == ("model_execute",)
    assert query.execution_requirements.tool_policy == ("fixture_read",)
    assert query.execution_requirements.network_policy == "denied"


def test_task_reviewer_is_a_separate_fixed_tool_target_not_an_outer_graph_node():
    exported = build_agent_evaluation_example()
    reviewer = build_example_reviewer()
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(exported.origin_bundle)
    registry.register_bundle(reviewer.origin_bundle)
    assert len(exported.workflow_release.nodes) == 2
    assert reviewer.module_release.release_ref not in {
        node.module_release_ref for node in exported.workflow_release.nodes}
    tested = next(module for module in exported.origin_bundle.modules if module.module_id == "capability_tested_agent")
    assert "review_candidate" in tested.execution_requirements.tool_policy
    assert reviewer.module_release.execution_requirements.tool_policy == ()
    assert {edge.outcome_id for edge in exported.workflow_release.edges
            if edge.source_node_id == "tested_agent"} == {"completed", "failed"}


def _fake_models(monkeypatch, *, skip_review=False, fail_task=False):
    """Real Runtime/IPC with a Provider double, explicitly not live-model evidence."""
    from agent_runtime.invocation import invocation_claude_cli_execution as claude
    from agent_runtime.invocation.invocation_local_command_mcp import exchange
    from test_agent_runtime_claude_native_tools import _init, _result, _tool_use, _tool_result
    base = claude.ClaudeAdapter
    calls = []
    class Adapter(base):
        def __init__(self, **kwargs):
            super().__init__(**kwargs, process_runner=self._process)
        def execute(self, request, host):
            self.request = request
            return super().execute(request, host)
        def _process(self, **fields):
            module = self._registry.get_module(self.request.module_release_ref, self.request.module_release_sha256)
            fields["launch_guard"](lambda: calls.append(module.module_id))
            item, = [item for item in self.request.authorized_inputs if item.logical_name == "task_input"]
            payload = json.loads(self._artifacts.read_bytes(item.input_ref, item.input_sha256))
            args = fields["argv"]
            tools = args[args.index("--tools") + 1].split(",")
            config = json.loads(args[args.index("--mcp-config") + 1])["mcpServers"]
            init = _init(tuple(name for name in tools if name))
            endpoint = None
            if "runtime_tools" in config:
                endpoint = Path(config["runtime_tools"]["args"][-2])
                definitions = exchange(endpoint, {"method": "definitions"})
                init["tools"] += ["mcp__runtime_tools__" + row["name"] for row in definitions]
                init["mcp_servers"] = [{"name": "runtime_tools", "status": "connected"}]
            lines = []
            def emit(event):
                line = json.dumps(event)
                assert fields["on_stdout_line"](line)
                lines.append(line)
            emit(init)
            def invoke(name, value):
                identity = "provider_example_call"
                emit(_tool_use(identity, "mcp__runtime_tools__" + name, **value))
                response = exchange(endpoint, {"method": "invoke", "tool_name": name, "payload": value})
                emit(_tool_result(identity, failed=response["status"] != "completed", content=json.dumps(response)))
                assert response["status"] == "completed", response
                return response["result"]
            node = module.module_id.removeprefix("capability_")
            if node == "context_reader":
                output = {"summary": payload["task"], "required_facts": payload["required_facts"]}
            elif node == "gateway_researcher":
                output = invoke("fixture_read", {"key": payload["fixture_key"]})
            elif node == "draft_writer":
                output = {"draft": "\n".join(payload["facts"])}
            elif node in {"reviewer_a", "reviewer_b", "task_reviewer"}:
                output = {"accepted": all(fact in payload["draft"] for fact in payload["required_facts"]), "reason": "facts checked"}
            elif node == "selector":
                output = {"decision": {"decide": "accepted", "revise": "revision_required", "wait": "wait_for_external_event"}[payload["requested_action"]],
                          "reason": "both reviews consumed"}
            elif node == "tested_agent":
                draft = "\n".join(payload["required_facts"])
                if fail_task:
                    output = {"invalid_output": True}
                else:
                    if not skip_review:
                        invoke("review_candidate", {"input_payload": {"draft": draft, "required_facts": payload["required_facts"]}})
                    output = {"candidate": draft, "review_feedback": "self-report alone is not evidence", "task_completed": True}
            elif node == "evaluation_agent":
                evidence = payload["evidence"]
                reviewed = bool(evidence["child_executions"])
                complete = reviewed and evidence["tested_agent"]["status"] == "completed"
                output = {"task_completed": complete, "evidence_sufficient": True, "reviewer_called": reviewed,
                          "assessment": "checked supplied execution facts"}
            else:
                pytest.fail("unhandled example Module " + node)
            emit(_result(structured_output=output))
            raw = "\n".join(lines).encode()
            completed = subprocess.CompletedProcess(args, 0, raw.decode(), "")
            completed.stdout_bytes, completed.stderr_bytes = raw, b""
            return completed
    monkeypatch.setattr(claude, "ClaudeAdapter", Adapter)
    return calls


@pytest.mark.parametrize("scenario,visits", [("accepted", 6), ("revision", 10), ("wait", 10)])
def test_full_example_uses_real_graph_and_runtime_tools_with_provider_double(tmp_path, monkeypatch, scenario, visits):
    from agent_runtime.testing.conformance_agent_execution import run_agent_example
    from test_agent_runtime_claude_native_tools import _fake_cli
    calls = _fake_models(monkeypatch)
    result = run_agent_example(tmp_path / "root", "agent_capability_example", scenario=scenario, cli_path=_fake_cli(tmp_path))
    assert result["status"] == "completed", result
    assert result["output"]["decision"] == "accepted"
    assert len(calls) == len(result["execution"]["nodes"]) == visits
    assert len({row["workflow_execution_id"] for row in result["execution"]["nodes"]}) == 1
    assert all(row["execution_log"]["complete"] for row in result["execution"]["nodes"])
    assert result["resources_cleaned"] and result["full_runtime_completed"] is False
    assert bool(result["wait_snapshots"]) == (scenario == "wait")


@pytest.mark.parametrize("skip_review,fail_task", [(False, False), (True, False), (False, True)])
def test_independent_evaluation_uses_child_facts_never_outer_substitution(tmp_path, monkeypatch, skip_review, fail_task):
    from agent_runtime.testing.conformance_agent_execution import run_agent_example
    from test_agent_runtime_claude_native_tools import _fake_cli
    calls = _fake_models(monkeypatch, skip_review=skip_review, fail_task=fail_task)
    result = run_agent_example(tmp_path / "root", "agent_evaluation_example", cli_path=_fake_cli(tmp_path))
    assert result["status"] == "completed", result
    expected = not (skip_review or fail_task)
    assert result["output"]["reviewer_called"] is expected
    assert result["output"]["task_completed"] is expected
    assert len(result["child_executions"]) == int(expected)
    assert calls.count("capability_task_reviewer") == int(expected)
    assert calls.count("capability_evaluation_agent") == 1
    assert len(result["execution"]["nodes"]) == 2
    if expected:
        child = result["child_executions"][0]
        parent = result["execution"]["nodes"][0]
        assert child["input_payload"]["draft"] == parent["output"]["candidate"]
        assert child["parent_attempt_id"] == parent["attempt_id"]
        assert child["record"]["workflow_execution_id"] != parent["workflow_execution_id"]
        assert child["record"]["execution_log"]["complete"]
    if fail_task:
        assert result["execution"]["nodes"][0]["status"] == "failed"


def test_example_cleanup_failure_retains_captured_records():
    from agent_runtime.testing.conformance_agent_execution import _retain_cleanup_result
    result = {}
    with _retain_cleanup_result(result):
        result.update(status="completed", execution={"nodes": [{"actual": "record"}]})
        raise OSError("owned cleanup failed")
    assert result["status"] == "failed" and result["resources_cleaned"] is False
    assert result["execution"]["nodes"] == [{"actual": "record"}]


@pytest.mark.parametrize("arguments", [
    [], ["--workflow", "some_workflow"], ["--example", "agent_capability_example", "--version", "v1"],
    ["--example", "agent_capability_example", "--resources", "resources.json"],
    ["--workflow", "some_workflow", "--example", "agent_capability_example"],
    ["--example", "agent_evaluation_example", "--scenario", "wait"],
])
def test_example_cli_invalid_arguments_stop_before_root_or_model(tmp_path, arguments):
    from agent_runtime.testing.conformance_local_evaluation import main
    root = tmp_path / "unused_root"
    with pytest.raises(SystemExit) as error:
        main(["--root", str(root), *arguments])
    assert error.value.code == 2
    assert not root.exists()


def test_installed_cli_routes_examples_and_preserves_returned_facts(tmp_path, monkeypatch, capsys):
    from agent_runtime.testing import conformance_local_evaluation as cli
    seen = []
    def run(root, example, **arguments):
        seen.append((root, example, arguments))
        return {"status": "cancelled", "execution": {"actual": "records"}}
    monkeypatch.setattr(cli, "run_agent_example", run)
    assert cli.main(["--root", str(tmp_path), "--example", "agent_evaluation_example"]) == 130
    assert json.loads(capsys.readouterr().out)["execution"] == {"actual": "records"}
    assert seen[0][1] == "agent_evaluation_example" and seen[0][2]["input_payload"] is None


def test_installed_cli_keeps_the_existing_registered_execution_entry(tmp_path, monkeypatch):
    from agent_runtime.testing import conformance_local_evaluation as cli
    arguments = ["--root", str(tmp_path), "--workflow", "one_module", "--input", "input.json"]
    seen = []
    monkeypatch.setattr(cli, "_execute_registered", lambda argv: seen.append(argv) or 0)
    assert cli.main(arguments) == 0
    assert seen == [arguments]


def test_example_help_preserves_inherited_execution_guidance(monkeypatch, capsys):
    from agent_runtime.testing import conformance_local_evaluation as cli
    from agent_runtime.testing.execution_local_evaluation import build_parser
    inherited = build_parser(require_workflow=False, require_input=False)
    combined = cli.build_parser()
    assert combined.epilog.startswith(inherited.epilog)
    assert len(combined.epilog) > len(inherited.epilog)
    assert inherited.epilog in combined.format_help()
    def forbidden(*args, **kwargs):
        pytest.fail("help must not enter setup, registration or execution")
    monkeypatch.setattr(cli, "run_agent_example", forbidden)
    monkeypatch.setattr(cli, "_execute_registered", forbidden)
    with pytest.raises(SystemExit) as outcome:
        cli.main(["--help"])
    assert outcome.value.code == 0
    assert inherited.epilog in capsys.readouterr().out


@pytest.mark.skipif(os.environ.get("RUN_PROVIDER_INTEGRATION") != "1", reason="explicit live Agent example gate")
@pytest.mark.parametrize("example", ["agent_capability_example", "agent_evaluation_example"])
def test_live_claude_agent_examples(tmp_path, example):
    from agent_runtime.testing.conformance_agent_execution import run_agent_example
    result = run_agent_example(tmp_path / "root", example)
    (tmp_path / "execution.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    assert result["status"] == "completed", result["failure"]
    assert result["resources_cleaned"] and not result["full_runtime_completed"]
    nodes = result["execution"]["nodes"]
    assert all(row["execution_log"]["complete"] for row in nodes)
    if example == "agent_capability_example":
        assert result["output"]["decision"] == "accepted"
        query = next(row for row in nodes if row["dispatch"]["current_state_id"] == "gateway_researcher")
        assert any(call["tool_name"] == "fixture_read" and call["status"] == "completed"
                   for call in query["execution_log"]["tool_calls"])
        assert {row["dispatch"]["current_state_id"] for row in nodes} >= {
            "context_reader", "gateway_researcher", "draft_writer", "reviewer_a", "reviewer_b", "selector"}
    else:
        assert result["output"]["task_completed"] and result["output"]["evidence_sufficient"]
        assert result["output"]["reviewer_called"]
        assert result["child_executions"]
        parent = next(row for row in nodes if row["dispatch"]["current_state_id"] == "tested_agent")
        child = result["child_executions"][-1]
        assert child["parent_attempt_id"] == parent["attempt_id"]
        assert child["input_payload"]["draft"] == parent["output"]["candidate"]
        assert child["record"]["status"] == "completed" and child["record"]["output"]["accepted"]
        assert child["record"]["execution_log"]["complete"]
        assert any(call["tool_name"] == "review_candidate" and call["status"] == "completed"
                   for call in parent["execution_log"]["tool_calls"])
