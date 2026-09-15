"""Model-independent registration and exact per-invocation preparation."""
from dataclasses import fields, replace
import hashlib
import json
import os
import subprocess
import tempfile
from unittest.mock import patch

import pytest

from agent_runtime import evaluate_local_workflow_module
from agent_runtime import (
    ModuleReviewer, RuntimeModulePlugin, load_runtime_registration,
    prepare_local_workflow_module, register_runtime_module_plugin, run_registered_workflow_module,
)
from agent_runtime.execution import AgentExecutionAdapterRegistry, InMemoryCellArtifactStore
from agent_runtime.execution import execution_local_invocation as local
from agent_runtime.registry import RuntimeReleaseBundle, RuntimeReleaseRegistry, PostgresRuntimeReleaseStore
from agent_runtime.registry import registry_module_authoring as authoring
from agent_runtime.ledger import (
    InMemoryRuntimeExecutionRecordStore, PostgresRuntimeExecutionRecordStore, PostgresRuntimeExecutionQueryStore,
)
from agent_runtime.contracts.ledger_record_definition import WorkflowModuleExecutionVariantRecord, WorkflowExecutionRecord
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from test_agent_runtime_claude_native_tools import _fake_cli, _init, _result
from test_agent_runtime_registered_module_execution import _Host, _Contents, _TEST_TIME
from test_agent_runtime_reviewer_registration_cli import (
    _source, _register, _files, _cli, _prepared_bundle, SKILL_ID, MODULE_ID,
)
from test_agent_runtime_postgres_release_store import postgres_release_test_schema
from test_agent_runtime_postgres_execution_ledger import postgres_test_schema


def test_plain_export_and_registration_do_not_resolve_any_model(tmp_path, monkeypatch):
    source, _ = _source(tmp_path / "source")
    monkeypatch.setattr(local, "_execution_profile_for_requirements", lambda *a, **k: pytest.fail("No model selection"))
    reviewer = ModuleReviewer.from_registration(source, skill_id=SKILL_ID, module_id=MODULE_ID)
    exported = reviewer.export(module_version="v1")
    assert exported.module_release.reviewer_defaults is None
    assert exported.module_release.get_execution_requirements() is not None
    assert exported.origin_bundle.execution_profiles == exported.origin_bundle.execution_variant_policies == ()
    registered = _register(tmp_path / "host", source)
    assert registered.submitted_bundle.execution_profiles == registered.submitted_bundle.execution_variant_policies == ()


@pytest.mark.parametrize("name", ["model_id", "reasoning_profile"])
def test_registration_model_parameters_fail_before_writes(tmp_path, name):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    with pytest.raises(TypeError, match="unexpected keyword"):
        _register(root, source, **{name: "explicit"})
    assert not root.exists()
    result = _cli(root, source, "v1", "--" + name.replace("_", "-"), "explicit")
    assert result.returncode == 2 and not root.exists()


@pytest.mark.parametrize("mode,tools,workspace", [
    ("tool_free", (), "none"), ("agent", (), "none"),
    ("agent", (), "own_draft_read_write"), ("agent", ("read",), "none"),
    ("agent", ("search", "read"), "own_draft_read_write"),
    ("agent", ("shell",), "none"), ("agent", ("read", "search", "shell"), "own_draft_read_write"),
])
@pytest.mark.parametrize("output_mode", ["prompt_only_json", "native_structured_output"])
def test_ordinary_module_register_prepare_and_evaluate(tmp_path, monkeypatch, mode, tools, workspace, output_mode):
    from agent_runtime import Module, evaluate_local_workflow_module
    from agent_runtime.registry import registry_reviewer_defaults
    from test_agent_runtime_module_authoring import _task_project, _requirements, SKILL_ID as TASK_SKILL

    source = _task_project(tmp_path / "source", module_id="summarize_note")
    requirements = _requirements(execution_mode=mode, tool_policy=tools,
        attempt_workspace_policy=workspace, output_constraint_mode=output_mode,
        timeout_seconds=70, max_attempts=2)
    monkeypatch.setattr(registry_reviewer_defaults, "_validate_reviewer_output_schema",
                        lambda *a: pytest.fail("ordinary Module cannot use Reviewer-specific validation"))
    module = Module.from_registration(source, skill_id=TASK_SKILL, module_id="summarize_note",
                                      execution_requirements=requirements)
    exported = module.export(module_version="v1")
    workflow = Module.to_workflow(exported).export()
    root = tmp_path / "host"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        "ordinary_example", "v1", workflow.origin_bundle), root=root)
    before = _files(root)
    saved, variant = prepare_local_workflow_module(root, "summarize_note")
    assert saved.release.workflow_id == "summarize_note"
    selected = saved.registry.snapshot().execution_profiles[0]
    requirements.assert_profile(selected)
    assert (selected.executor_adapter_id, selected.executor_adapter_revision) == ("claude_cli_adapter", "v3")
    assert selected.model_id == "claude-opus-5[1m]" and selected.reasoning_profile == "xhigh"
    assert exported.module_release.reviewer_defaults is None
    assert not exported.origin_bundle.execution_profiles
    assert variant.policy_document()["bindings"][0]["position_id"] == saved.release.nodes[0].node_id
    calls = []
    adapter_type = claude.ClaudeAdapter
    native = output_mode == "native_structured_output"
    def process(**kwargs):
        kwargs["launch_guard"](lambda: calls.append(kwargs))
        argv = kwargs["argv"]
        assert argv[argv.index("--tools") + 1] == ",".join(claude.NATIVE_TOOLS[tool] for tool in tools)
        assert kwargs["timeout_seconds"] == 70
        init = _init(tuple(claude.NATIVE_TOOLS[tool] for tool in tools))
        if not native:
            init["tools"] = [tool for tool in init["tools"] if tool != "StructuredOutput"]
        terminal = _result(structured_output={"summary": "the actual ordinary output"})
        if not native:
            terminal["result"] = json.dumps(terminal.pop("structured_output"))
        lines = [json.dumps(event) for event in (init, terminal)]
        for line in lines:
            assert kwargs["on_stdout_line"](line)
        completed = subprocess.CompletedProcess(argv, 0, "\n".join(lines), "")
        completed.stdout_bytes, completed.stderr_bytes = completed.stdout.encode(), b""
        return completed
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kw: adapter_type(**kw, process_runner=process))
    record = evaluate_local_workflow_module(root, "summarize_note", input_payload={}, cli_path=_fake_cli(tmp_path))
    assert record["status"] == "completed", record["failure_detail"]
    assert record["output"] == {"summary": "the actual ordinary output"}
    assert record["execution_log"]["complete"] and len(calls) == 1
    assert hashlib.sha256(calls[0]["prompt"].encode()).hexdigest() == record["execution_trace"]["attempts"][0]["prompt_envelope_sha256"]
    assert record["provider_trace"]["actual_prompt"] == calls[0]["prompt"]
    assert _files(root) == before


@pytest.mark.parametrize("field,value", [("model_id", ""), ("reasoning_profile", ""),
                                         ("reasoning_profile", "none"), ("reasoning_profile", "ultra")])
def test_explicit_invalid_model_fields_do_not_become_defaults(field, value):
    from test_agent_runtime_module_authoring import _requirements
    with pytest.raises(ValueError):
        local._execution_profile_for_requirements(_requirements(), **{field: value})


def test_requested_gateway_requires_cli_bridge_before_resources_or_provider():
    from test_agent_runtime_module_authoring import _requirements
    requirements = _requirements(semantic_input_delivery_mode="gateway_read", attempt_workspace_policy="none",
        tool_policy=("read_source",), network_policy="gateway_only",
        gateway_access_reasons=("external_fact_verification",))
    with pytest.raises(ValueError, match="production Gateway access"):
        local._execution_profile_for_requirements(requirements)


def _resource_test_module(tmp_path, *, tools=("read", "search", "shell")):
    from agent_runtime import Module
    from test_agent_runtime_module_authoring import _task_project, _requirements, SKILL_ID as TASK_SKILL
    source = _task_project(tmp_path / "source", module_id="summarize_note")
    module = Module.from_registration(source, skill_id=TASK_SKILL, module_id="summarize_note",
        execution_requirements=_requirements(execution_mode="agent" if tools else "tool_free", tool_policy=tools,
            attempt_workspace_policy="own_draft_read_write" if tools else "none", timeout_seconds=30, max_attempts=1))
    root = tmp_path / "host"
    workflow = module.to_workflow(module.export(module_version="v1")).export()
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        "resource_example", "v1", workflow.origin_bundle), root=root)
    return root


def _observe_resource_evaluation(monkeypatch, inspect, *, tools=("read", "search", "shell")):
    adapter_type = claude.ClaudeAdapter
    calls = []
    def process(**kwargs):
        kwargs["launch_guard"](lambda: calls.append(kwargs))
        inspect(kwargs)
        events = [_init(tuple(claude.NATIVE_TOOLS[name] for name in tools)),
                  _result(structured_output={"summary": "resource input observed"})]
        lines = [json.dumps(event) for event in events]
        for line in lines:
            assert kwargs["on_stdout_line"](line)
        result = subprocess.CompletedProcess(kwargs["argv"], 0, "\n".join(lines), "")
        result.stdout_bytes, result.stderr_bytes = result.stdout.encode(), b""
        return result
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kwargs: adapter_type(**kwargs, process_runner=process))
    return calls


def test_public_resources_freeze_tree_and_exact_prompt_with_host_defaults(tmp_path, monkeypatch):
    from pathlib import Path
    root = _resource_test_module(tmp_path)
    tree = tmp_path / "tree"
    (tree / "pkg").mkdir(parents=True)
    body = b"VALUE = 41\n"
    (tree / "pkg/value.py").write_bytes(body)
    dependency = tmp_path / "library"
    dependency.mkdir()
    executable = _fake_cli(tmp_path)
    (root / ".runtime/config.json").write_text(json.dumps({
        "provider_cli_paths": {"claude_cli": str(executable), "codex_cli": "unused/missing"},
        "read_only_dependencies": [str(dependency)]}))
    before = _files(root)
    def inspect(fields):
        materials = fields["cwd"].parent / "materials"
        assert (materials / "source/pkg/value.py").read_bytes() == body
        assert not (materials / "local_resources").exists()
        assert str(tree) not in fields["prompt"] and str(dependency) not in fields["prompt"]
        assert "content_base64" not in fields["prompt"] and "../materials/source" in fields["prompt"]
        assert Path(fields["argv"][0]) == executable.resolve()
        (tree / "pkg/value.py").write_text("changed only after capture\n")
        assert (materials / "source/pkg/value.py").read_bytes() == body
    calls = _observe_resource_evaluation(monkeypatch, inspect)
    record = evaluate_local_workflow_module(root, "summarize_note", input_payload={}, material_root=tree,
        material_files=({"relative_path": "pkg/value.py", "sha256": hashlib.sha256(body).hexdigest(), "executable": False},))
    assert record["status"] == "completed", record["failure_detail"]
    assert len(calls) == 1 and len(record["input_bindings"]) == 2 and _files(root) == before
    assert record["provider_trace"]["actual_prompt"] == calls[0]["prompt"]
    assert hashlib.sha256(calls[0]["prompt"].encode()).hexdigest() == record["execution_trace"]["attempts"][0]["prompt_envelope_sha256"]
    assert not calls[0]["cwd"].exists()


@pytest.mark.parametrize("tools,explicit_dependencies", [((), None), (("read", "search", "shell"), ())])
def test_unused_or_cleared_host_dependencies_do_not_open_resources(tmp_path, monkeypatch, tools, explicit_dependencies):
    root = _resource_test_module(tmp_path, tools=tools)
    (root / ".runtime/config.json").write_text(json.dumps({
        "provider_cli_paths": {"claude_cli": "unused/missing"},
        "read_only_dependencies": ["unused/library"]}))
    calls = _observe_resource_evaluation(monkeypatch, lambda fields: None, tools=tools)
    result = evaluate_local_workflow_module(root, "summarize_note", input_payload={},
        cli_path=_fake_cli(tmp_path), read_only_dependencies=explicit_dependencies)
    assert result["status"] == "completed", result["failure_detail"]
    assert len(calls) == 1 and len(result["input_bindings"]) == 1


def test_invalid_material_hash_is_rejected_before_attempt_resources(tmp_path, monkeypatch):
    root = _resource_test_module(tmp_path)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "value.txt").write_text("current data")
    monkeypatch.setattr(tempfile, "TemporaryDirectory", lambda *a, **kw: pytest.fail("no Attempt for invalid input"))
    with pytest.raises(ValueError):
        evaluate_local_workflow_module(root, "summarize_note", input_payload={}, material_root=tree,
            material_files=({"relative_path": "value.txt", "sha256": "a"*64, "executable": False},))


def test_staged_tree_mutation_is_rejected_and_keeps_provider_log(tmp_path, monkeypatch):
    root = _resource_test_module(tmp_path)
    tree = tmp_path / "tree"
    tree.mkdir()
    body = b"fixed data"
    (tree / "value.txt").write_bytes(body)
    def mutate(fields):
        target = fields["cwd"].parent / "materials/source/value.txt"
        target.chmod(0o600)
        target.write_text("real changed bytes")
    calls = _observe_resource_evaluation(monkeypatch, mutate)
    record = evaluate_local_workflow_module(root, "summarize_note", input_payload={},
        cli_path=_fake_cli(tmp_path), material_root=tree,
        material_files=({"relative_path": "value.txt", "sha256": hashlib.sha256(body).hexdigest(), "executable": False},))
    assert record["status"] == "failed" and record["output"] is None
    assert len(calls) == 1 and record["provider_trace"]["raw_streams"]


def test_public_command_execution_and_cli_observations_share_one_verified_log(tmp_path, monkeypatch):
    from pathlib import Path
    from agent_runtime.invocation.invocation_local_command_mcp import exchange
    from agent_runtime.invocation.invocation_local_command_execution import LOCAL_COMMAND_CLI_TOOL_NAME
    from test_agent_runtime_execution_logging import use, local_reply
    root = _resource_test_module(tmp_path)
    adapter_type = claude.ClaudeAdapter
    calls, command_responses = [], []
    def process(**fields):
        fields["launch_guard"](lambda: calls.append(fields))
        argv = fields["argv"]
        server = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["runtime_commands"]
        endpoint = Path(server["args"][-1])
        assert str(endpoint) not in fields["prompt"]
        init = _init()
        init["tools"].append(LOCAL_COMMAND_CLI_TOOL_NAME)
        init["mcp_servers"] = [{"name": "runtime_commands", "status": "connected"}]
        events = [init]
        for index, command_id in enumerate(("ok", "bad"), 1):
            provider_id = f"provider_{index}"
            events.append(use(provider_id, LOCAL_COMMAND_CLI_TOOL_NAME, command_id=command_id))
            response = exchange(endpoint, {"method": "invoke", "command_id": command_id})
            command_responses.append(response)
            events.append(local_reply(provider_id, {"response": response,
                "status": "completed" if response["returncode"] == 0 else "failed"}))
        events.append(_result(structured_output={"summary": "both command results observed"}))
        lines = [json.dumps(event) for event in events]
        for line in lines:
            assert fields["on_stdout_line"](line)
        result = subprocess.CompletedProcess(argv, 0, "\n".join(lines), "")
        result.stdout_bytes, result.stderr_bytes = result.stdout.encode(), b""
        return result
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kwargs: adapter_type(**kwargs, process_runner=process))
    record = evaluate_local_workflow_module(root, "summarize_note", input_payload={}, cli_path=_fake_cli(tmp_path),
        commands=({"command_id": "ok", "argv": ["/usr/bin/printf", "real command stdout"], "cwd": "scratch/run", "timeout_seconds": 5},
                  {"command_id": "bad", "argv": ["/usr/bin/false"], "cwd": "source", "timeout_seconds": 5}))
    assert record["status"] == "completed", record["failure_detail"]
    assert len(calls) == 1 and [response["returncode"] for response in command_responses] == [0, 1]
    log = record["execution_log"]
    assert log["complete"], log["attempts"][0]["issues"]
    assert len(log["tool_calls"]) == len(log["attempts"][0]["provider_tool_calls"]) == 2
    for index, (row, response) in enumerate(zip(log["tool_calls"], command_responses), 1):
        assert row["source_kind"] == "runtime_local" and row["response"] == response
        assert row["tool_call_id"] == response["local_call_id"] and row["provider_tool_call_id"] == f"provider_{index}"
        assert row["status"] == ("completed" if index == 1 else "failed")
    assert command_responses[0]["stdout"] == "real command stdout"
    assert hashlib.sha256(calls[0]["prompt"].encode()).hexdigest() == record["execution_trace"]["attempts"][0]["prompt_envelope_sha256"]
    assert not calls[0]["cwd"].exists()


@pytest.mark.parametrize("saved_variants", [0, 1, 2])
def test_missing_requirements_never_infer_defaults_from_historical_bindings(tmp_path, saved_variants):
    from test_agent_runtime_registered_module_execution import _environment as legacy_environment, _selection
    env = legacy_environment(tmp_path)
    assert env.module.get_execution_requirements() is None
    if saved_variants == 2:
        _selection(env)
    variants = env.registry.snapshot().execution_variant_policies if saved_variants else ()
    assert len(variants) == saved_variants
    bundle = local._closure(env.registry, env.kwargs["workflow"], supplied_variants=variants)
    root = tmp_path / "saved"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin("historical", "v1", bundle), root=root)
    before = _files(root)
    with pytest.raises(ValueError, match="requires frozen Module execution requirements"):
        prepare_local_workflow_module(root, env.kwargs["workflow"].workflow_id)
    assert _files(root) == before
    assert env.host.calls == len(env.calls) == 0


def test_explicit_historical_binding_still_executes_and_replays(tmp_path):
    from test_agent_runtime_registered_module_execution import _environment as legacy_environment
    env = legacy_environment(tmp_path)
    root = tmp_path / "saved"
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        "historical", "v1", local._closure(env.registry, env.kwargs["workflow"],
            supplied_variants=(env.kwargs["variant_policy"],))), root=root)
    before = _files(root)
    host = {key: value for key, value in env.kwargs.items()
            if key not in {"release_registry", "workflow", "variant_policy"}}
    first = local.run_local_workflow_module(root, env.kwargs["workflow"].workflow_id,
        input_payload={"value": "example"}, idempotency_key="old_explicit_request", **host)
    repeat = local.run_local_workflow_module(root, env.kwargs["workflow"].workflow_id,
        input_payload={"value": "example"}, idempotency_key="old_explicit_request", **host)
    assert first.attempts[0].status == "completed"
    assert repeat.attempts == first.attempts and env.host.calls == len(env.calls) == 1
    assert _files(root) == before


def test_old_saved_model_is_preserved_but_does_not_select_new_invocations(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    legacy = _prepared_bundle(root, model_id="saved-model-c")
    register_runtime_module_plugin(RuntimeReleaseRegistry(),
        RuntimeModulePlugin("historical_binding", "v1", legacy), root=root)
    before = _files(root)
    _register(root, source)
    assert _files(root) == before
    fresh = _prepared_bundle(root)
    assert fresh.execution_profiles[0].model_id == "claude-opus-5[1m]"
    assert len(fresh.execution_variant_policies) == len(fresh.execution_profiles) == 1
    assert load_runtime_registration(root, "workflow", MODULE_ID).registry.snapshot().execution_profiles[0].model_id == "saved-model-c"
    assert _files(root) == before


def test_external_policy_lookup_does_not_copy_model_configuration_to_new_root(tmp_path):
    source, _ = _source(tmp_path / "source")
    _register(tmp_path / "old", source)
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(_prepared_bundle(tmp_path / "old", model_id="not-a-registration-default"))
    with pytest.raises(TypeError, match="unexpected keyword"):
        _register(tmp_path / "new", source, release_registry=registry)
    assert not (tmp_path / "new").exists()


def test_new_default_affects_only_new_preparations_not_fixed_files(tmp_path, monkeypatch):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    original = local._execution_profile_for_requirements
    old, _ = prepare_local_workflow_module(root, MODULE_ID)
    monkeypatch.setattr(local, "_execution_profile_for_requirements",
        lambda defaults, **kw: original(defaults, **{**kw, "model_id": kw.get("model_id") or "model-b"}))
    _register(root, source)
    new, _ = prepare_local_workflow_module(root, MODULE_ID)
    assert old.registry.snapshot().execution_profiles[0].model_id == "claude-opus-5[1m]"
    assert new.registry.snapshot().execution_profiles[0].model_id == "model-b"
    assert old.release == new.release and _files(root) == before


@pytest.mark.parametrize("transport", ["claude_agent_sdk", "", "unknown"])
def test_unsupported_transport_has_no_fallback_or_store_write(tmp_path, transport):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    class Store:
        def register_bundle(self, bundle):
            pytest.fail("No writes for an unsupported model transport")
        def load_release_registry(self):
            pytest.fail("No reads for an unsupported model transport")
    with pytest.raises(ValueError, match="Unsupported model transport"):
        prepare_local_workflow_module(root, MODULE_ID, transport_kind=transport, release_store=Store())
    assert _files(root) == before


def _invoke(preparation, tmp_path, calls, *, key, verdict="passed", records=None, contents=None, payload=None):
    saved, selection = preparation
    cell = InMemoryCellArtifactStore()
    host = _Host(cell, saved.release)
    profile = saved.registry.snapshot().execution_profiles[0]
    output = {"verdict": verdict, "check_results": [], "findings": [], "safe_next_step": "Review completed."}
    if verdict == "non_pass":
        output["findings"] = [{"finding_id": "fix_one", "severity": "fix",
            "evidence": {"source_ref": "candidate", "locator": "section_one", "observation": "Missing requirement"},
            "requirement": "Include the requirement", "impact": "Incomplete candidate",
            "accountable_owner_ref": "subject_owner", "required_change": "Revise the candidate"}]
    def process(**kwargs):
        calls.append(kwargs)
        init = {**_init(), "model": profile.model_id}
        terminal = _result(structured_output=output, is_error=verdict == "provider_failure",
            modelUsage={profile.model_id: {"canonicalModel": profile.model_id.removesuffix("[1m]"), "outputTokens": 3}})
        for event in (init, terminal):
            assert kwargs["on_stdout_line"](json.dumps(event))
        return subprocess.CompletedProcess(kwargs["argv"], 0, "", "")
    tmp_path.mkdir(parents=True, exist_ok=True)
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(claude.ClaudeAdapter(
        release_registry=saved.registry, artifact_host=cell, workspace_root=tmp_path / "attempts",
        cli_path=_fake_cli(tmp_path), process_runner=process))
    contents = contents if contents is not None else _Contents()
    records = records if records is not None else InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=contents.contains)
    # The provider is a stub; keep its ancillary temporary files inside the
    # test's declared writable root, including under the independent review sandbox.
    temporary_directory = claude.tempfile.TemporaryDirectory
    def test_temporary_directory(*args, **kwargs):
        return temporary_directory(*args, **{**kwargs, "dir": tmp_path})
    with patch.object(claude.tempfile, "TemporaryDirectory", test_temporary_directory):
        run = run_registered_workflow_module(module_id=MODULE_ID, input_payload={} if payload is None else payload,
            idempotency_key=key, release_registry=saved.registry, workflow=saved.release, variant_policy=selection,
            authorize=host.authorize, context_client=host, operation_client=host,
            enforcing_gateway_id="agent_runtime_module_kernel", environment_id="analyst_billie",
            adapters=adapters, artifact_host=cell, record_store=records, content_store=contents,
            claim_token_secret=b"test_only_claim_secret_value_123456", clock=lambda: _TEST_TIME)
    return run, records, contents

def _diagnostics(run, contents):
    attempt = run.attempts[-1]
    detail = next((value.body.decode() for value in contents.values.values()
                   if value.content_ref == attempt.failure_detail_ref), None)
    return {"status": attempt.status, "failure_class": attempt.failure_class, "failure_detail": detail}


@pytest.mark.parametrize("verdict", ["passed", "non_pass", "provider_failure"])
def test_prepared_selection_runs_once_and_preserves_ledger_identity(tmp_path, monkeypatch, verdict):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    prepared = prepare_local_workflow_module(root, MODULE_ID, model_id="model-a")
    _register(root, source, "v2")
    source.rename(source.with_name("unavailable_source"))
    monkeypatch.setattr(local, "load_runtime_registration", lambda *a, **k: pytest.fail("No reload at dispatch"))
    monkeypatch.setattr(local, "_execution_profile_for_requirements", lambda *a, **k: pytest.fail("No model reselection"))
    calls = []
    result, records, contents = _invoke(prepared, tmp_path / "run", calls, key="new_request", verdict=verdict)
    assert len(calls) == 1, _diagnostics(result, contents)
    assert result.attempts[0].status == ("failed" if verdict == "provider_failure" else "completed")
    trace = records.load_trace(result.module_run.workflow_execution_id)
    assert trace.records_of_type(WorkflowModuleExecutionVariantRecord)[0].model_id == "model-a"
    assert trace.records_of_type(WorkflowExecutionRecord)[0].workflow_release_ref == prepared[0].release.release_ref
    repeat, _, _ = _invoke(prepared, tmp_path / "repeat", calls, key="new_request",
                          records=records, contents=contents, verdict=verdict)
    assert repeat.attempts == result.attempts and len(calls) == 1


def test_changed_model_requires_new_key_and_invalid_input_never_calls_provider(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    a = prepare_local_workflow_module(root, MODULE_ID, model_id="model-a")
    b = prepare_local_workflow_module(root, MODULE_ID, model_id="model-b")
    calls = []
    first, records, contents = _invoke(a, tmp_path / "a", calls, key="same_request")
    with pytest.raises(ValueError, match="already bound"):
        _invoke(b, tmp_path / "conflict", calls, key="same_request", records=records, contents=contents)
    assert len(calls) == 1, _diagnostics(first, contents)
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):
        _invoke(b, tmp_path / "invalid", calls, key="invalid_request", payload={"unexpected": True},
                records=records, contents=contents)
    assert len(calls) == 1
    second, _, _ = _invoke(b, tmp_path / "b", calls, key="new_request", records=records, contents=contents)
    assert len(calls) == 2 and first.module_run.workflow_execution_id != second.module_run.workflow_execution_id


def test_prepare_store_failure_and_wrong_readback_do_not_update_local_files(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    class Store:
        def register_bundle(self, bundle):
            raise RuntimeError("store write unavailable")
        def load_release_registry(self):
            return RuntimeReleaseRegistry()
    with pytest.raises(RuntimeError, match="store write unavailable"):
        prepare_local_workflow_module(root, MODULE_ID, release_store=Store())
    class MissingReadback(Store):
        def register_bundle(self, bundle):
            self.submitted = bundle
    with pytest.raises(KeyError, match="unknown Workflow"):
        prepare_local_workflow_module(root, MODULE_ID, release_store=MissingReadback())
    assert _files(root) == before


def test_pg_new_model_and_fresh_log_query_keep_original_configuration(
    tmp_path, postgres_release_test_schema, postgres_test_schema,
):
    dsn = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    releases = PostgresRuntimeReleaseStore.from_dsn(dsn, schema=postgres_release_test_schema)
    releases.create_schema(installed_at_utc=_TEST_TIME)
    ledger = PostgresRuntimeExecutionRecordStore.from_dsn(dsn, schema=postgres_test_schema)
    ledger.initialize_schema()
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    a = prepare_local_workflow_module(root, MODULE_ID, model_id="model-a", release_store=releases)
    calls = []
    first, _, _ = _invoke(a, tmp_path / "a", calls, key="pg_request_a", records=ledger, contents=ledger)
    b = prepare_local_workflow_module(root, MODULE_ID, model_id="model-b", release_store=releases)
    _invoke(b, tmp_path / "b", calls, key="pg_request_b", records=ledger, contents=ledger)
    fresh = PostgresRuntimeReleaseStore.from_dsn(dsn, schema=postgres_release_test_schema).load_release_registry()
    query = PostgresRuntimeExecutionQueryStore.from_dsn(dsn, schema=postgres_test_schema)
    trace = query.load_trace(first.module_run.workflow_execution_id)
    start = trace.records_of_type(WorkflowExecutionRecord)[0]
    selection = fresh.get_execution_variant_policy(start.execution_profile_selection_ref, start.execution_profile_selection_sha256)
    assert selection == a[1]
    binding = selection.policy_document()["bindings"][0]
    original = fresh.get_execution_profile(binding["execution_profile_release_ref"], binding["execution_profile_release_sha256"])
    assert original.model_id == "model-a"
    assert trace.records_of_type(WorkflowModuleExecutionVariantRecord)[0].model_id == "model-a"
    item = first.outputs[0]
    assert json.loads(query.load_content(first.module_run.workflow_execution_id, item.output_ref).body)["verdict"] == "passed"
    assert len(calls) == 2 and _files(root) == before
