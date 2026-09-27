"""Test Run definition sources: local root or read-only exact PostgreSQL release.

Argument rules that stop a call before setup, a connection or a Provider are
deterministic. Every path that loads a definition, reads PostgreSQL or reaches a
Provider runs for real: the PostgreSQL test database named by
AGENT_RUNTIME_TEST_DATABASE_URL, the installed Claude or Codex CLI and this
package's Test Run. Test Run reads PostgreSQL as a temporary role that holds
only USAGE and SELECT on the test schema, so any registration, DDL or row write
during a Test Run fails the test.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlsplit, urlunsplit
import uuid

import pytest

from agent_runtime import load_runtime_registration, run_local_workflow_test, setup_runtime
from agent_runtime.foundation import foundation_environment_setup as setup
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from agent_runtime.registry import PostgresRuntimeReleaseStore
from agent_runtime.registry.registry_local_persistence import _closure
from agent_runtime.testing import conformance_local_test_run as cli

from test_agent_runtime_execution_parameters import (
    _module_root, _write, REAL_EFFORT, REAL_GATE, REAL_MODEL, REAL_TIMEOUT_SECONDS, WORKFLOW,
)
from test_agent_runtime_local_model_preparation import _resource_test_module


DSN_VARIABLE = "AGENT_RUNTIME_TEST_DATABASE_URL"
READER_DSN_VARIABLE = "AGENT_RUNTIME_TEST_READER_DATABASE_URL"
REAL_CODEX_MODEL = "gpt-6-astra"
DSN = "postgresql://runtime_reader:dummy@db.invalid:5432/runtime"


class _StoreShape:
    """Argument-shape stand-in: rejected before it is ever read."""

    def load_release_registry(self):
        pytest.fail("rejected before the store is read")


@pytest.fixture
def no_setup(monkeypatch):
    monkeypatch.setattr(setup, "setup_runtime", lambda root: pytest.fail("rejected before setup"))
    monkeypatch.setattr(PostgresRuntimeReleaseStore, "from_dsn",
                        classmethod(lambda cls, *a, **k: pytest.fail("rejected before a connection")))


@pytest.fixture
def pg_schema(monkeypatch):
    """A throwaway schema and a SELECT-only reader role in the real test database, both dropped afterwards.

    Yields the writing store and the schema; READER_DSN_VARIABLE names the
    reader's DSN, which is what Test Run receives.
    """
    dsn = os.environ.get(DSN_VARIABLE)
    if not dsn:
        pytest.skip(f"unverified: requires {DSN_VARIABLE} naming a PostgreSQL test database")
    import psycopg
    from psycopg import sql
    suffix = uuid.uuid4().hex[:16]
    schema, reader = f"agent_runtime_test_run_{suffix}", f"agent_runtime_test_reader_{suffix}"
    store = PostgresRuntimeReleaseStore.from_dsn(dsn, schema=schema)
    store.create_schema(installed_at_utc="2026-09-26T00:00:00Z")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(reader)))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(reader)))
        connection.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(
            sql.Identifier(schema), sql.Identifier(reader)))
    parts = urlsplit(dsn)
    monkeypatch.setenv(READER_DSN_VARIABLE, urlunsplit(parts._replace(netloc=f"{reader}@{parts.hostname}"
                                                                    + (f":{parts.port}" if parts.port else ""))))
    try:
        yield store, schema
    finally:
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(reader)))
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(reader)))


def _registration(directory):
    """The tool-free summarize_note Workflow registered locally: its Registry and release."""
    if not (directory / "host").exists():
        _module_root(directory)
    saved = load_runtime_registration(directory / "host", "workflow", WORKFLOW)
    return saved.registry, saved.release


def _publish(store, tmp_path):
    """Register the tool-free summarize_note Workflow into the real PostgreSQL store with the writing role."""
    registry, release = _registration(tmp_path / "published")
    store.register_bundle(_closure(registry, release))
    return release


def _row_counts(schema):
    """Row count of every table in the schema, read directly from PostgreSQL."""
    import psycopg
    with psycopg.connect(os.environ[DSN_VARIABLE]) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s ORDER BY table_name", (schema,))]
        return {table: connection.execute(f'SELECT count(*) FROM "{schema}"."{table}"').fetchone()[0]
                for table in tables}


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
          release_store=_StoreShape(), release_database_url_env="DSN_VAR"), "not both"),
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


@pytest.mark.deterministic
@pytest.mark.parametrize("name", ["LOGNAME", "HOME", "TZ", "SSL_CERT_FILE", "HTTPS_PROXY", "CODEX_CA_CERTIFICATE"])
def test_a_dsn_variable_that_an_adapter_forwards_is_rejected(tmp_path, monkeypatch, no_setup, name):
    monkeypatch.setenv(name, DSN)
    with pytest.raises(ValueError, match="forwarded to Provider processes"):
        run_local_workflow_test(tmp_path, input_payload={}, release_database_url_env=name, release_schema="s",
            workflow_release_ref="runtime-workflow:x@v1", workflow_release_sha256="a" * 64)


@pytest.mark.deterministic
def test_expected_module_mismatch_stops_before_materials_or_provider(tmp_path, monkeypatch):
    from agent_runtime.invocation import invocation_local_resource_preparation as resources
    root = _module_root(tmp_path)
    monkeypatch.setattr(resources, "capture_local_resources", lambda **kw: pytest.fail("no material capture"))
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kw: pytest.fail("no Provider"))
    with pytest.raises(ValueError, match="not the expected other_module"):
        run_local_workflow_test(root, WORKFLOW, input_payload={}, expected_module_id="other_module")


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
def test_cli_passes_pg_locator_and_constraints_to_the_api_unchanged(tmp_path, monkeypatch):
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
                    "transport_kind": "claude_cli", "model_id": None, "reasoning_profile": None, "run_timeout_seconds": None,
                    "cli_path": None, "resources_path": Path("resources.json")}


@pytest.mark.real_run
def test_real_missing_or_mismatched_pg_release_never_falls_back_to_a_local_definition(tmp_path, pg_schema, monkeypatch):
    """Real entry: the PostgreSQL test database; the Provider guard proves nothing is launched."""
    store, schema = pg_schema
    workflow = _publish(store, tmp_path)
    root = _module_root(tmp_path / "consumer")  # the same Workflow ID is registered locally
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kw: pytest.fail("no Provider for an unresolved definition"))
    target = dict(release_database_url_env=READER_DSN_VARIABLE, release_schema=schema)
    with pytest.raises(KeyError, match="unknown Workflow release"):
        run_local_workflow_test(root, input_payload={}, workflow_release_ref="runtime-workflow:absent@v1",
                                workflow_release_sha256=workflow.release_sha256, **target)
    with pytest.raises(ValueError, match="hash mismatch"):
        run_local_workflow_test(root, input_payload={}, workflow_release_ref=workflow.release_ref,
                                workflow_release_sha256="0" * 64, **target)


@pytest.mark.real_run
def test_real_multi_node_pg_release_is_rejected(tmp_path, pg_schema, monkeypatch):
    """Real entry: the packaged capability graph registered in the PostgreSQL test database, read through a store."""
    from agent_runtime.testing.conformance_agent_examples import build_agent_capability_example
    store, schema = pg_schema
    bundle = build_agent_capability_example().origin_bundle
    store.register_bundle(bundle)
    graph = next(item for item in bundle.workflows if len(item.nodes) > 1)
    monkeypatch.setattr(claude, "ClaudeAdapter", lambda **kw: pytest.fail("no Provider for a graph"))
    with pytest.raises(ValueError, match="one Workflow Module entry node"):
        run_local_workflow_test(tmp_path / "consumer", input_payload={},
            release_store=PostgresRuntimeReleaseStore.from_dsn(os.environ[READER_DSN_VARIABLE], schema=schema),
            workflow_release_ref=graph.release_ref, workflow_release_sha256=graph.release_sha256)


@pytest.mark.real_run
@REAL_GATE
def test_real_pg_definition_runs_read_only_with_root_setup_and_parameter_files(tmp_path, pg_schema):
    """Real entry: the PostgreSQL test database, the installed Claude CLI and this package's Test Run."""
    store, schema = pg_schema
    workflow = _publish(store, tmp_path)
    root = tmp_path / "consumer"
    digest = _write(root, f".runtime/execution_parameters/workflows/{WORKFLOW}.json",
                    model_id=REAL_MODEL, reasoning_profile=REAL_EFFORT)
    before = _row_counts(schema)
    record = run_local_workflow_test(root, input_payload={}, release_database_url_env=READER_DSN_VARIABLE,
        release_schema=schema, workflow_release_ref=workflow.release_ref, workflow_release_sha256=workflow.release_sha256,
        run_timeout_seconds=3600)
    assert record["status"] == "completed", record["failure_detail"]
    assert record["execution_budget"]["requested_timeout_seconds"] == 3600
    assert record["execution_parameter_sources"]["run_timeout_seconds"]["layer"] == "call"
    assert record["provider_trace"]["timeout_seconds"] == 3600
    assert (record["workflow_release_ref"], record["workflow_release_sha256"]) == (workflow.release_ref, workflow.release_sha256)
    argv = record["provider_trace"]["argv"]
    assert (argv[argv.index("--model") + 1], argv[argv.index("--effort") + 1]) == (REAL_MODEL, REAL_EFFORT)
    assert record["execution_parameter_sources"]["reasoning_profile"]["file_sha256"] == digest
    evidence_dir = os.environ.get("AGENT_RUNTIME_BUDGET_EVIDENCE_DIR")
    if evidence_dir:
        with (Path(evidence_dir) / "pg_budget_record.json").open("x") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
    assert _row_counts(schema) == before
    assert (root / ".runtime/setup.json").is_file() and not (root / ".runtime/workflow").exists()
    # Control: the identity Test Run used cannot register, so a write during the run would have failed it.
    import psycopg
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        PostgresRuntimeReleaseStore.from_dsn(os.environ[READER_DSN_VARIABLE], schema=schema).register_bundle(
            _closure(*_registration(tmp_path / "published")))


@pytest.mark.real_run
@REAL_GATE
@pytest.mark.parametrize("transport,model", [("claude_cli", REAL_MODEL), ("codex_cli", REAL_CODEX_MODEL)])
def test_real_dsn_never_reaches_provider_processes_or_record(tmp_path, pg_schema, monkeypatch, transport, model):
    """Real entry: the PostgreSQL test database and the installed Provider CLI.

    Process launches are observed, not replaced: each call still starts the real
    process with the environment Runtime built.
    """
    from agent_runtime.invocation import invocation_codex_module_invocation as codex
    store, schema = pg_schema
    workflow = _publish(store, tmp_path)
    dsn, reader_dsn = os.environ[DSN_VARIABLE], os.environ[READER_DSN_VARIABLE]
    launched = []
    real_run = subprocess.run
    def observed_run(argv, *args, **kwargs):
        launched.append(("subprocess", [str(item) for item in argv], kwargs.get("env")))
        return real_run(argv, *args, **kwargs)
    monkeypatch.setattr(subprocess, "run", observed_run)
    real_process = codex.run_cli_process
    def observed_process(**fields):
        launched.append(("codex", [str(item) for item in fields["argv"]], dict(fields["environment"])))
        return real_process(**fields)
    monkeypatch.setattr(codex, "run_cli_process", observed_process)
    record = run_local_workflow_test(tmp_path / "consumer", input_payload={}, release_database_url_env=READER_DSN_VARIABLE,
        release_schema=schema, workflow_release_ref=workflow.release_ref, workflow_release_sha256=workflow.release_sha256,
        transport_kind=transport, model_id=model, reasoning_profile=REAL_EFFORT)
    assert record["status"] == "completed", record["failure_detail"]
    if transport == "claude_cli":
        checks = [env for kind, argv, env in launched if kind == "subprocess" and argv[-1] in ("--version", "--help")]
        assert len(checks) == 2, "both Claude CLI preflight checks ran"
        checks.append(record["provider_trace"]["environment"])
    else:
        checks = [env for kind, _, env in launched if kind == "codex"]
        assert len(checks) == 2, "the Codex version check and the Codex run both started"
    for env in checks:
        assert env is not None and not {DSN_VARIABLE, READER_DSN_VARIABLE} & set(env)
        assert dsn not in json.dumps(env) and reader_dsn not in json.dumps(env)
    assert dsn not in json.dumps(record) and reader_dsn not in json.dumps(record)


@pytest.mark.real_run
@REAL_GATE
def test_real_resource_file_resolves_the_same_through_api_and_cli(tmp_path, capsys):
    """Real entry: the installed Claude CLI with frozen materials declared in a resource file."""
    root = _resource_test_module(tmp_path, timeout_seconds=REAL_TIMEOUT_SECONDS)
    setup_runtime(root)
    _write(root, ".runtime/execution_parameters/workspace.json", model_id=REAL_MODEL, reasoning_profile=REAL_EFFORT)
    tree = tmp_path / "bundle" / "tree"
    (tree / "pkg").mkdir(parents=True)
    body = b"VALUE = 41\n"
    (tree / "pkg/value.py").write_bytes(body)
    resources = tmp_path / "bundle" / "resources.json"
    resources.write_text(json.dumps({"material_root": "tree", "read_only_dependencies": [], "material_files": [
        {"relative_path": "pkg/value.py", "sha256": hashlib.sha256(body).hexdigest(), "executable": False}]}))
    api = run_local_workflow_test(root, WORKFLOW, input_payload={}, resources_path=resources)
    payload = tmp_path / "input.json"
    payload.write_text("{}")
    assert cli.main(["--root", str(root), "--workflow", WORKFLOW, "--input", str(payload),
                     "--resources", str(resources)]) == 0
    command = json.loads(capsys.readouterr().out)
    assert api["status"] == command["status"] == "completed", (api["failure_detail"], command["failure_detail"])
    def resource_hashes(record):  # content refs are per call; the frozen bytes must match
        return [row["input_sha256"] for row in record["input_bindings"] if row["logical_name"] == "local_resources"]
    assert len(resource_hashes(api)) == 1 and resource_hashes(api) == resource_hashes(command)
