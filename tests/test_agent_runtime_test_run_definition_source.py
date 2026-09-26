"""Test Run definition sources: local root or read-only exact PostgreSQL release."""
import json
from pathlib import Path

import pytest

from agent_runtime import load_runtime_registration, run_local_workflow_test, setup_runtime
from agent_runtime.foundation import foundation_environment_setup as setup
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.registry import PostgresRuntimeReleaseStore
from agent_runtime.testing import conformance_local_test_run as cli

from test_agent_runtime_claude_native_tools import _fake_cli
from test_agent_runtime_execution_parameters import _module_root, _write, WORKFLOW
from test_agent_runtime_local_model_preparation import _observe_resource_evaluation, _resource_test_module
from test_agent_runtime_reviewer_registration_cli import _files


DSN = "postgresql://runtime_reader:SECRET-DSN-VALUE@db.invalid:5432/runtime"


class _ReadOnlyStore:
    """Fake release store: serves one loaded Registry and fails on any write."""

    def __init__(self, registry):
        self.registry, self.loads = registry, 0

    def load_release_registry(self):
        self.loads += 1
        return self.registry

    def register_bundle(self, bundle):
        pytest.fail("Test Run must not write a PostgreSQL definition store")


def _pg_source(tmp_path):
    """Registry exported from a separate registration root, standing in for PostgreSQL."""
    source_root = _module_root(tmp_path / "published")
    saved = load_runtime_registration(source_root, "workflow", WORKFLOW)
    return saved.release, _ReadOnlyStore(saved.registry)


@pytest.fixture
def no_setup(monkeypatch):
    monkeypatch.setattr(setup, "setup_runtime", lambda root: pytest.fail("rejected before setup"))
    monkeypatch.setattr(PostgresRuntimeReleaseStore, "from_dsn",
                        classmethod(lambda cls, *a, **k: pytest.fail("rejected before a connection")))


@pytest.mark.deterministic
@pytest.mark.parametrize("arguments,message", [
    (dict(workflow_id=WORKFLOW, workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64), "not both"),
    (dict(workflow_id=WORKFLOW, release_database_url_env="DSN_VAR", release_schema="agent_runtime"), "not both"),
    (dict(), "requires workflow_id"),
    (dict(workflow_release_ref="runtime-workflow:x@v1"), "requires workflow_release_sha256"),
    (dict(workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64, version="v1",
          release_database_url_env="DSN_VAR", release_schema="s"), "version selects a local definition"),
    (dict(workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64,
          release_store=object()), "must provide load_release_registry"),
    (dict(workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64,
          release_store=_ReadOnlyStore(None), release_database_url_env="DSN_VAR"), "not both"),
    (dict(workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64,
          release_database_url_env="DSN_VAR"), "requires both"),
    (dict(workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64,
          release_schema="agent_runtime"), "requires both"),
    (dict(workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64,
          release_database_url_env="BAD-NAME", release_schema="agent_runtime"), "environment variable name"),
    (dict(workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64,
          release_database_url_env="MISSING_RUNTIME_DSN", release_schema="agent_runtime"), "missing or empty"),
    (dict(workflow_id=WORKFLOW, expected_module_id=""), "expected_module_id"),
    (dict(workflow_id=WORKFLOW, resources_path=Path("resources.json"), material_root=Path(".")), "not both"),
    (dict(workflow_id=WORKFLOW, resources_path=Path("resources.json"), commands=({"command_id": "c"},)), "not both"),
])
def test_conflicting_or_incomplete_targets_fail_before_setup_or_connection(tmp_path, monkeypatch, no_setup,
                                                                           arguments, message):
    monkeypatch.delenv("MISSING_RUNTIME_DSN", raising=False)
    with pytest.raises(ValueError, match=message):
        run_local_workflow_test(tmp_path, input_payload={}, **arguments)


@pytest.mark.deterministic
def test_an_empty_dsn_variable_fails_before_setup_or_connection(tmp_path, monkeypatch, no_setup):
    monkeypatch.setenv("EMPTY_RUNTIME_DSN", "")
    with pytest.raises(ValueError, match="missing or empty; no DSN fallback"):
        run_local_workflow_test(tmp_path, input_payload={}, workflow_release_ref="runtime-workflow:x@v1",
            workflow_release_sha256="a" * 64, release_database_url_env="EMPTY_RUNTIME_DSN", release_schema="s")


@pytest.mark.fake_run
def test_pg_definition_runs_read_only_with_root_setup_and_parameter_files(tmp_path, monkeypatch):
    """Substitutes: in-memory release store for PostgreSQL, FakeCLI and in-process Claude runner."""
    workflow, store = _pg_source(tmp_path)
    root = tmp_path / "consumer"
    digest = _write(root, f".runtime/execution_parameters/workflows/{WORKFLOW}.json", reasoning_profile="high")
    calls = _observe_resource_evaluation(monkeypatch, lambda fields: None, tools=())
    record = run_local_workflow_test(root, input_payload={}, release_store=store,
        workflow_release_ref=workflow.release_ref, workflow_release_sha256=workflow.release_sha256,
        cli_path=_fake_cli(tmp_path))
    assert record["status"] == "completed", record["failure_detail"]
    assert (record["workflow_release_ref"], record["workflow_release_sha256"]) == (workflow.release_ref, workflow.release_sha256)
    assert store.loads == 1 and len(calls) == 1
    argv = calls[0]["argv"]
    assert argv[argv.index("--effort") + 1] == "high"
    assert record["execution_parameter_sources"]["reasoning_profile"]["file_sha256"] == digest
    assert (root / ".runtime/setup.json").is_file() and not (root / ".runtime/workflow").exists()


@pytest.mark.fake_run
def test_dsn_never_reaches_claude_preflight_provider_or_record(tmp_path, monkeypatch):
    """Substitutes: from_dsn returns an in-memory store; FakeCLI executable and in-process Claude process runner.

    The two CLI preflight calls run the real FakeCLI script through subprocess.run.
    """
    import subprocess
    monkeypatch.setenv("RUNTIME_TEST_RELEASE_DSN", DSN)
    workflow, store = _pg_source(tmp_path)
    opened = []
    monkeypatch.setattr(PostgresRuntimeReleaseStore, "from_dsn",
                        classmethod(lambda cls, dsn, *, schema: opened.append((dsn, schema)) or store))
    preflight = []
    real_run = subprocess.run
    def observed_run(argv, *args, **kwargs):
        preflight.append((list(argv), kwargs.get("env")))
        return real_run(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", observed_run)
    calls = _observe_resource_evaluation(monkeypatch, lambda fields: None, tools=())
    record = run_local_workflow_test(tmp_path / "consumer", input_payload={},
        release_database_url_env="RUNTIME_TEST_RELEASE_DSN", release_schema="agent_runtime",
        workflow_release_ref=workflow.release_ref, workflow_release_sha256=workflow.release_sha256,
        cli_path=_fake_cli(tmp_path))
    assert record["status"] == "completed", record["failure_detail"]
    assert opened == [(DSN, "agent_runtime")]
    checks = [(argv, env) for argv, env in preflight if argv[-1] in ("--version", "--help")]
    assert [argv[-1] for argv, _ in checks] == ["--version", "--help"]
    for _, env in checks:
        assert env is not None and "RUNTIME_TEST_RELEASE_DSN" not in env and "SECRET-DSN-VALUE" not in json.dumps(env)
    assert "SECRET-DSN-VALUE" not in json.dumps(calls[0], default=str)
    assert "SECRET-DSN-VALUE" not in json.dumps(record)
    assert "RUNTIME_TEST_RELEASE_DSN" not in record["provider_trace"]["environment"]


@pytest.mark.deterministic
@pytest.mark.parametrize("name", ["LOGNAME", "HOME", "SSL_CERT_FILE", "HTTPS_PROXY", "CODEX_CA_CERTIFICATE"])
def test_a_dsn_variable_that_an_adapter_forwards_is_rejected(tmp_path, monkeypatch, no_setup, name):
    monkeypatch.setenv(name, DSN)
    with pytest.raises(ValueError, match="forwarded to Provider processes"):
        run_local_workflow_test(tmp_path, input_payload={}, release_database_url_env=name, release_schema="s",
            workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64)


@pytest.mark.deterministic
def test_missing_or_mismatched_pg_release_never_falls_back_to_a_local_definition(tmp_path, monkeypatch):
    workflow, store = _pg_source(tmp_path)
    root = _module_root(tmp_path / "consumer")
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kw: pytest.fail("no Provider for an unresolved definition"))
    with pytest.raises(KeyError, match="unknown Workflow release"):
        run_local_workflow_test(root, input_payload={}, release_store=store,
            workflow_release_ref="runtime-workflow:absent@v1", workflow_release_sha256=workflow.release_sha256)
    with pytest.raises(ValueError, match="hash mismatch"):
        run_local_workflow_test(root, input_payload={}, release_store=store,
            workflow_release_ref=workflow.release_ref, workflow_release_sha256="0" * 64)


@pytest.mark.deterministic
def test_a_multi_node_pg_release_is_rejected(tmp_path, monkeypatch):
    class Graph:
        nodes = (object(), object())
        initial_node_id = "first"
    class Registry:
        def get_workflow(self, ref, digest):
            return Graph()
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kw: pytest.fail("no Provider for a graph"))
    with pytest.raises(ValueError, match="one Workflow Module entry node"):
        run_local_workflow_test(tmp_path, input_payload={}, release_store=_ReadOnlyStore(Registry()),
            workflow_release_ref="runtime-workflow:graph@v1", workflow_release_sha256="a" * 64)


@pytest.mark.deterministic
def test_expected_module_mismatch_stops_before_materials_or_provider(tmp_path, monkeypatch):
    from agent_runtime.invocation import invocation_local_resource_preparation as resources
    root = _module_root(tmp_path)
    monkeypatch.setattr(resources, "capture_local_resources", lambda **kw: pytest.fail("no material capture"))
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kw: pytest.fail("no Provider"))
    with pytest.raises(ValueError, match="not the expected other_module"):
        run_local_workflow_test(root, WORKFLOW, input_payload={}, expected_module_id="other_module")


@pytest.mark.fake_run
def test_resource_file_resolves_the_same_through_api_and_cli(tmp_path, monkeypatch, capsys):
    """Substitutes: FakeCLI and in-process Claude runner; the resource file and materials are real."""
    root = _resource_test_module(tmp_path)
    setup_runtime(root)
    tree = tmp_path / "bundle" / "tree"
    (tree / "pkg").mkdir(parents=True)
    body = b"VALUE = 41\n"
    (tree / "pkg/value.py").write_bytes(body)
    import hashlib
    resources = tmp_path / "bundle" / "resources.json"
    resources.write_text(json.dumps({"material_root": "tree", "read_only_dependencies": [], "material_files": [
        {"relative_path": "pkg/value.py", "sha256": hashlib.sha256(body).hexdigest(), "executable": False}]}))
    calls = _observe_resource_evaluation(monkeypatch, lambda fields: None)
    api = run_local_workflow_test(root, WORKFLOW, input_payload={}, resources_path=resources,
                                  cli_path=_fake_cli(tmp_path))
    payload = tmp_path / "input.json"
    payload.write_text("{}")
    assert cli.main(["--root", str(root), "--workflow", WORKFLOW, "--input", str(payload),
                     "--resources", str(resources), "--cli-path", str(_fake_cli(tmp_path))]) == 0
    command = json.loads(capsys.readouterr().out)
    assert api["status"] == command["status"] == "completed"
    def resource_hashes(record):  # content refs are per call; the frozen bytes must match
        return [row["input_sha256"] for row in record["input_bindings"] if row["logical_name"] == "local_resources"]
    assert len(resource_hashes(api)) == 1 and resource_hashes(api) == resource_hashes(command)
    assert len(calls) == 2


@pytest.mark.deterministic
@pytest.mark.parametrize("arguments", [
    ["--workflow", WORKFLOW, "--workflow-ref", "r", "--workflow-sha256", "s", "--input", "i.json"],
    ["--workflow-ref", "r", "--input", "i.json"],
    ["--workflow-ref", "r", "--workflow-sha256", "s", "--input", "i.json"],
    ["--workflow-ref", "r", "--workflow-sha256", "s", "--release-database-url-env", "V",
     "--release-schema", "x", "--version", "v1", "--input", "i.json"],
    ["--example", "agent_capability_example", "--workflow-ref", "r"],
    ["--example", "agent_capability_example", "--expected-module-id", "m"],
    ["--release-schema", "x", "--input", "i.json"],
])
def test_cli_target_combinations_are_argument_errors(tmp_path, monkeypatch, arguments):
    monkeypatch.setattr(setup, "setup_runtime", lambda root: pytest.fail("argument errors precede setup"))
    monkeypatch.setattr(cli, "run_local_workflow_test", lambda *a, **k: pytest.fail("no Test Run"))
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--root", str(tmp_path), *arguments])
    assert stopped.value.code == 2


@pytest.mark.deterministic
def test_cli_passes_pg_locator_and_constraints_to_the_api_unchanged(tmp_path, monkeypatch, capsys):
    seen = {}
    def capture(root, workflow_id, **kwargs):
        seen.update(root=root, workflow_id=workflow_id, **kwargs)
        return {"status": "completed"}
    monkeypatch.setattr(cli, "run_local_workflow_test", capture)
    payload = tmp_path / "input.json"
    payload.write_text('{"subject": 1}')
    assert cli.main(["--root", str(tmp_path), "--workflow-ref", "runtime-workflow:x@v1", "--workflow-sha256", "a" * 64,
                     "--release-database-url-env", "RUNTIME_RELEASE_DSN", "--release-schema", "agent_runtime",
                     "--expected-module-id", "design_contract_reviewer", "--resources", "resources.json",
                     "--transport", "claude_cli", "--input", str(payload)]) == 0
    assert seen == {"root": tmp_path, "workflow_id": None, "input_payload": {"subject": 1},
                    "expected_module_id": "design_contract_reviewer", "version": None,
                    "release_database_url_env": "RUNTIME_RELEASE_DSN", "release_schema": "agent_runtime",
                    "workflow_release_ref": "runtime-workflow:x@v1", "workflow_release_sha256": "a" * 64,
                    "transport_kind": "claude_cli", "model_id": None, "reasoning_profile": None,
                    "cli_path": None, "resources_path": Path("resources.json")}
