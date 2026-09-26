"""Four-layer execution parameters: this call, Workflow file, workspace file, Runtime default."""
import hashlib
import json
import os

import pytest

from agent_runtime import (
    RuntimeModulePlugin, load_runtime_registration, prepare_local_workflow, prepare_local_workflow_module,
    register_runtime_module_plugin, run_local_workflow_test, setup_runtime,
)
from agent_runtime.execution import execution_local_invocation as local
from agent_runtime.execution.execution_parameter_resolution import (
    PARAMETER_FILE_FORMAT, SOURCE_CALL, SOURCE_RUNTIME_DEFAULT, SOURCE_WORKFLOW_FILE, SOURCE_WORKSPACE_FILE,
    resolve_execution_parameters,
)
from agent_runtime.registry import RuntimeReleaseRegistry

from test_agent_runtime_claude_native_tools import _fake_cli
from test_agent_runtime_local_model_preparation import _observe_resource_evaluation
from test_agent_runtime_reviewer_registration_cli import _files


WORKFLOW = "summarize_note"
WORKSPACE = ".runtime/execution_parameters/workspace.json"
WORKFLOW_FILE = f".runtime/execution_parameters/workflows/{WORKFLOW}.json"


def _write(root, relative, **values):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"schema_version": PARAMETER_FILE_FORMAT, **values}).encode()
    path.write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def _module_root(tmp_path, *, version="v1", root=None):
    """Register a tool-free single-node Workflow named summarize_note."""
    from agent_runtime import Module
    from test_agent_runtime_module_authoring import _task_project, _requirements, SKILL_ID as TASK_SKILL
    source = _task_project(tmp_path / ("source_" + version), module_id=WORKFLOW)
    module = Module.from_registration(source, skill_id=TASK_SKILL, module_id=WORKFLOW,
        execution_requirements=_requirements(execution_mode="tool_free", tool_policy=(),
            attempt_workspace_policy="none", timeout_seconds=30, max_attempts=1))
    root = tmp_path / "host" if root is None else root
    workflow = module.to_workflow(module.export(module_version=version)).export()
    register_runtime_module_plugin(RuntimeReleaseRegistry(), RuntimeModulePlugin(
        "parameter_example", version, workflow.origin_bundle), root=root)
    return root


def _profile(saved, variant):
    binding = variant.policy_document()["bindings"][0]
    return saved.registry.get_execution_profile(
        binding["execution_profile_release_ref"], binding["execution_profile_release_sha256"])


@pytest.mark.deterministic
def test_no_parameter_files_leave_every_value_to_the_runtime_default(tmp_path):
    resolved = resolve_execution_parameters(tmp_path, WORKFLOW)
    assert (resolved.transport_kind, resolved.model_id, resolved.reasoning_profile) == (None, None, None)
    assert resolved.as_record() == {name: {"layer": SOURCE_RUNTIME_DEFAULT, "file": None, "file_sha256": None}
                                    for name in ("transport_kind", "model_id", "reasoning_profile")}


@pytest.mark.deterministic
@pytest.mark.parametrize("layer", [SOURCE_CALL, SOURCE_WORKFLOW_FILE, SOURCE_WORKSPACE_FILE])
def test_each_layer_alone_supplies_its_value(tmp_path, layer):
    explicit = {}
    digest = None
    if layer == SOURCE_CALL:
        explicit = {"model_id": "model-from-call"}
    else:
        digest = _write(tmp_path, WORKFLOW_FILE if layer == SOURCE_WORKFLOW_FILE else WORKSPACE, model_id="model-from-call")
    resolved = resolve_execution_parameters(tmp_path, WORKFLOW, **explicit)
    assert resolved.model_id == "model-from-call"
    source = resolved.source("model_id")
    assert source.layer == layer and source.file_sha256 == digest
    assert source.file == (None if layer == SOURCE_CALL else
                           WORKFLOW_FILE if layer == SOURCE_WORKFLOW_FILE else WORKSPACE)


@pytest.mark.deterministic
def test_higher_layers_override_lower_layers_parameter_by_parameter(tmp_path):
    workspace = _write(tmp_path, WORKSPACE, model_id="workspace-model", reasoning_profile="low")
    workflow = _write(tmp_path, WORKFLOW_FILE, model_id="workflow-model")
    resolved = resolve_execution_parameters(tmp_path, WORKFLOW, reasoning_profile="high")
    assert (resolved.transport_kind, resolved.model_id, resolved.reasoning_profile) == (None, "workflow-model", "high")
    record = resolved.as_record()
    assert record["transport_kind"]["layer"] == SOURCE_RUNTIME_DEFAULT
    assert record["model_id"] == {"layer": SOURCE_WORKFLOW_FILE, "file": WORKFLOW_FILE, "file_sha256": workflow}
    assert record["reasoning_profile"] == {"layer": SOURCE_CALL, "file": None, "file_sha256": None}
    other = resolve_execution_parameters(tmp_path, "another_workflow")
    assert (other.model_id, other.reasoning_profile) == ("workspace-model", "low")
    assert other.source("model_id").file_sha256 == workspace


@pytest.mark.deterministic
def test_a_model_written_for_another_transport_is_rejected(tmp_path):
    _write(tmp_path, WORKSPACE, model_id="claude-opus-5", reasoning_profile="high")
    _write(tmp_path, WORKFLOW_FILE, transport_kind="codex_cli")
    with pytest.raises(ValueError, match="mix transports.*model_id comes from workspace_file whose transport is claude_cli"):
        resolve_execution_parameters(tmp_path, WORKFLOW)


@pytest.mark.deterministic
def test_a_call_repeating_the_default_transport_keeps_the_workspace_model(tmp_path):
    _write(tmp_path, WORKSPACE, model_id="claude-opus-5")
    resolved = resolve_execution_parameters(tmp_path, WORKFLOW, transport_kind="claude_cli")
    assert (resolved.transport_kind, resolved.model_id) == ("claude_cli", "claude-opus-5")


@pytest.mark.deterministic
def test_a_file_naming_only_claude_keeps_the_claude_default_model(tmp_path):
    _write(tmp_path, WORKSPACE, transport_kind="claude_cli")
    resolved = resolve_execution_parameters(tmp_path, WORKFLOW)
    assert resolved.model_id is None and resolved.source("model_id").layer == SOURCE_RUNTIME_DEFAULT


@pytest.mark.deterministic
def test_a_codex_layer_carries_its_own_model_and_effort(tmp_path):
    _write(tmp_path, WORKSPACE, transport_kind="codex_cli", model_id="gpt-6-sol", reasoning_profile="high")
    _write(tmp_path, WORKFLOW_FILE, model_id="gpt-6-astra")
    resolved = resolve_execution_parameters(tmp_path, WORKFLOW)
    assert (resolved.transport_kind, resolved.model_id, resolved.reasoning_profile) == ("codex_cli", "gpt-6-astra", "high")


def _invalid(root, relative, case):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if case == "symlink":
        target = root / "elsewhere.json"
        target.write_text(json.dumps({"schema_version": PARAMETER_FILE_FORMAT}))
        path.symlink_to(target)
    elif case == "directory":
        path.mkdir()
    elif case == "invalid_json":
        path.write_text("{not json")
    elif case == "duplicate_key":
        path.write_text('{"schema_version": "%s", "model_id": "a", "model_id": "b"}' % PARAMETER_FILE_FORMAT)
    else:
        document = {
            "missing_schema_version": {"model_id": "a"},
            "other_schema_version": {"schema_version": "runtime_execution_parameters_v0", "model_id": "a"},
            "version_key": {"schema_version": PARAMETER_FILE_FORMAT, "version": "v2"},
            "definition_target_key": {"schema_version": PARAMETER_FILE_FORMAT, "workflow_release_ref": "runtime-workflow:x@v1"},
            "unknown_key": {"schema_version": PARAMETER_FILE_FORMAT, "provider_id": "anthropic"},
            "empty_value": {"schema_version": PARAMETER_FILE_FORMAT, "model_id": " "},
            "non_string_value": {"schema_version": PARAMETER_FILE_FORMAT, "reasoning_profile": 3},
        }[case]
        path.write_text(json.dumps(document))


@pytest.mark.deterministic
@pytest.mark.parametrize("case", ["symlink", "directory", "invalid_json", "duplicate_key", "missing_schema_version",
                                  "other_schema_version", "version_key", "definition_target_key", "unknown_key",
                                  "empty_value", "non_string_value"])
def test_an_invalid_workflow_file_fails_instead_of_falling_through(tmp_path, case):
    _write(tmp_path, WORKSPACE, model_id="workspace-model")
    _invalid(tmp_path, WORKFLOW_FILE, case)
    with pytest.raises(ValueError, match="Execution parameter"):
        resolve_execution_parameters(tmp_path, WORKFLOW)


@pytest.mark.deterministic
def test_public_prepare_functions_share_resolution_and_keep_their_pair_shape(tmp_path):
    root = _module_root(tmp_path)
    digest = _write(root, WORKSPACE, model_id="claude-opus-5", reasoning_profile="high")
    module_pair = prepare_local_workflow_module(root, WORKFLOW)
    graph_pair = prepare_local_workflow(root, WORKFLOW)
    assert len(module_pair) == len(graph_pair) == 2
    module_triple = local._prepare_local_workflow_module_with_sources(root, WORKFLOW)
    graph_triple = local._prepare_local_workflow_with_sources(root, WORKFLOW)
    assert module_triple[2].as_record() == graph_triple[2].as_record()
    assert module_triple[2].source("model_id").file_sha256 == digest
    for saved, variant in (module_pair, graph_pair, module_triple[:2], graph_triple[:2]):
        profile = _profile(saved, variant)
        assert (profile.transport_kind, profile.model_id, profile.reasoning_profile) == ("claude_cli", "claude-opus-5", "high")


@pytest.mark.deterministic
def test_parameter_files_never_select_a_version_or_change_registrations(tmp_path):
    root = _module_root(tmp_path)
    _write(root, WORKSPACE, model_id="claude-opus-5")
    _write(root, WORKFLOW_FILE, reasoning_profile="high")
    parameters = {path: data for path, data in _files(root).items() if "execution_parameters" in path}
    _module_root(tmp_path, version="v2", root=root)
    assert {path: data for path, data in _files(root).items() if "execution_parameters" in path} == parameters
    assert load_runtime_registration(root, "workflow", WORKFLOW).release.workflow_version == "v2"
    saved, _ = prepare_local_workflow_module(root, WORKFLOW)
    assert saved.release.workflow_version == "v2"
    old, _ = prepare_local_workflow_module(root, WORKFLOW, version="v1")
    assert old.release.workflow_version == "v1"


@pytest.mark.deterministic
def test_parameter_values_pass_the_same_profile_checks_as_explicit_values(tmp_path):
    root = _module_root(tmp_path)
    _write(root, WORKSPACE, transport_kind="codex_cli")
    with pytest.raises(ValueError, match="codex_cli requires explicit model_id"):
        prepare_local_workflow_module(root, WORKFLOW)
    _write(root, WORKSPACE, transport_kind="unsupported_cli", model_id="m", reasoning_profile="high")
    with pytest.raises(ValueError, match="Unsupported model transport"):
        prepare_local_workflow_module(root, WORKFLOW)


def _test_run_root(tmp_path):
    root = _module_root(tmp_path)
    setup_runtime(root)
    return root


@pytest.mark.fake_run
def test_test_run_sends_file_parameters_to_the_provider_and_records_their_sources(tmp_path, monkeypatch):
    """Substitutes: FakeCLI executable and an in-process Claude process runner."""
    root = _test_run_root(tmp_path)
    # The provider double reports claude-opus-5[1m]; effort is the value that distinguishes layers.
    digest = _write(root, WORKSPACE, model_id="claude-opus-5[1m]", reasoning_profile="high")
    calls = _observe_resource_evaluation(monkeypatch, lambda fields: None, tools=())
    before = _files(root)
    record = run_local_workflow_test(root, WORKFLOW, input_payload={}, cli_path=_fake_cli(tmp_path))
    assert record["status"] == "completed", record["failure_detail"]
    argv = calls[0]["argv"]
    assert argv[argv.index("--model") + 1] == "claude-opus-5[1m]" and argv[argv.index("--effort") + 1] == "high"
    assert (record["model"], record["effort"]) == ("claude-opus-5[1m]", "high")
    for name in ("model_id", "reasoning_profile"):
        assert record["execution_parameter_sources"][name] == {
            "layer": SOURCE_WORKSPACE_FILE, "file": WORKSPACE, "file_sha256": digest}
    assert record["execution_parameter_sources"]["transport_kind"]["layer"] == SOURCE_RUNTIME_DEFAULT
    assert _files(root) == before
    overridden = run_local_workflow_test(root, WORKFLOW, input_payload={}, reasoning_profile="low",
                                         cli_path=_fake_cli(tmp_path))
    argv = calls[1]["argv"]
    assert argv[argv.index("--effort") + 1] == "low"
    assert overridden["execution_parameter_sources"]["reasoning_profile"]["layer"] == SOURCE_CALL
    assert overridden["execution_profile_sha256"] != record["execution_profile_sha256"]


@pytest.mark.fake_run
def test_test_run_cli_uses_the_parameter_files_and_call_arguments(tmp_path, monkeypatch, capsys):
    """Substitutes: FakeCLI executable and an in-process Claude process runner."""
    from agent_runtime.testing.conformance_local_test_run import main
    root = _test_run_root(tmp_path)
    _write(root, WORKFLOW_FILE, reasoning_profile="high")
    calls = _observe_resource_evaluation(monkeypatch, lambda fields: None, tools=())
    payload = tmp_path / "input.json"
    payload.write_text("{}")
    assert main(["--root", str(root), "--workflow", WORKFLOW, "--input", str(payload), "--model", "claude-opus-5[1m]",
                 "--cli-path", str(_fake_cli(tmp_path))]) == 0
    record = json.loads(capsys.readouterr().out)
    argv = calls[0]["argv"]
    assert argv[argv.index("--effort") + 1] == "high"
    assert record["execution_parameter_sources"]["reasoning_profile"]["layer"] == SOURCE_WORKFLOW_FILE
    assert record["execution_parameter_sources"]["model_id"]["layer"] == SOURCE_CALL


@pytest.mark.fake_run
def test_example_parent_and_child_both_receive_the_workspace_parameters(tmp_path, monkeypatch):
    """Substitutes: FakeCLI executable and the packaged example's provider double."""
    from agent_runtime.testing.conformance_agent_execution import run_agent_example
    from test_agent_runtime_capability_examples import _fake_models
    root = tmp_path / "root"
    digest = _write(root, WORKSPACE, model_id="claude-opus-5[1m]", reasoning_profile="high")
    seen = []
    from agent_runtime.invocation import invocation_claude_cli_execution as claude
    calls = _fake_models(monkeypatch)
    double = claude.ClaudeAdapter
    class Recording(double):
        def _process(self, **fields):
            argv = fields["argv"]
            seen.append((argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1]))
            return super()._process(**fields)
    monkeypatch.setattr(claude, "ClaudeAdapter", Recording)
    result = run_agent_example(root, "agent_evaluation_example", cli_path=_fake_cli(tmp_path))
    assert result["status"] == "completed", result
    assert "capability_task_reviewer" in calls and len(seen) == len(calls)
    assert set(seen) == {("claude-opus-5[1m]", "high")}
    sources = result["execution_parameter_sources"]
    for name in ("workflow", "child_review"):
        assert sources[name]["model_id"] == {"layer": SOURCE_WORKSPACE_FILE, "file": WORKSPACE, "file_sha256": digest}


@pytest.mark.deterministic
def test_example_rejects_different_parent_and_child_transports_before_the_provider(tmp_path, monkeypatch):
    from agent_runtime.testing.conformance_agent_execution import run_agent_example
    from agent_runtime.invocation import invocation_claude_cli_execution as claude
    root = tmp_path / "root"
    _write(root, ".runtime/execution_parameters/workflows/capability_task_reviewer.json",
           transport_kind="codex_cli", model_id="gpt-6-sol", reasoning_profile="high")
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kw: pytest.fail("no Provider before the transport check"))
    with pytest.raises(ValueError, match="different transports"):
        run_agent_example(root, "agent_evaluation_example", cli_path=_fake_cli(tmp_path))


@pytest.mark.real_run
@pytest.mark.skipif(os.environ.get("AGENT_RUNTIME_REAL_RUN") != "1",
                    reason="unverified: set AGENT_RUNTIME_REAL_RUN=1 with an authenticated Claude CLI")
def test_real_test_run_uses_the_workspace_parameter_file(tmp_path):
    """Real entry: installed Claude CLI on PATH, local registration and this package's Test Run API."""
    root = _test_run_root(tmp_path)
    _write(root, WORKSPACE, model_id="claude-opus-5[1m]", reasoning_profile="low")
    record = run_local_workflow_test(root, WORKFLOW, input_payload={})
    assert record["status"] == "completed", record["failure_detail"]
    assert (record["model"], record["effort"]) == ("claude-opus-5[1m]", "low")
    assert record["execution_parameter_sources"]["reasoning_profile"]["layer"] == SOURCE_WORKSPACE_FILE
