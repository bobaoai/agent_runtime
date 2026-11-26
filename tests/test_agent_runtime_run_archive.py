"""Run archive: save a finished Test Run, read it back on a new connection, clean only its own Attempt.

Substitutes: the FakeCLI Test Run fixture from test_agent_runtime_self_test_cli (real
preparation, kernel, Adapter, record and sealed Attempt folder) and FakeDatabase, a DB-API
stand-in whose committed rows are visible only to later connections. The real_run test at
the end uses PostgreSQL through AGENT_RUNTIME_TEST_DATABASE_URL.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid

import pytest

from agent_runtime import (
    PostgresTestRunArchiveStore, cleanup_test_run_archive, load_runtime_config, read_test_run_archive,
    run_archive_ddl, save_test_run_archive, write_test_run_record,
)
from agent_runtime.invocation import invocation_workspace_preparation as workspaces
from agent_runtime.testing import conformance_test_run_archiving as archiving
from test_agent_runtime_self_test_cli import environment, invoke  # noqa: F401 - FakeCLI Test Run fixture


_CONTROL = {".agent_runtime_attempt.json", ".agent_runtime_attempt_seal.json"}


class FakeDatabaseError(Exception):
    """Shaped like a psycopg error: its message must never be echoed."""

    sqlstate = "23514"


FakeDatabaseError.__module__ = "psycopg.errors"


class FakeDatabase:
    """Committed rows only; each connect() is a new connection; business rows sit beside the archive."""

    def __init__(self):
        self.archives, self.contents, self.connections = {}, {}, []
        self.business = {"supply.orders": [{"order_id": "o-1", "units": 3}]}
        self.fail_insert = None
        self.corrupt = None

    def connect(self):
        connection = FakeConnection(self)
        self.connections.append(connection)
        return connection

    def statements(self):
        return [statement for connection in self.connections for statement in connection.statements]


class FakeConnection:
    def __init__(self, database):
        self.database, self.pending, self.statements = database, [], []
        self.read_only = self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        for table, key, row in self.pending:
            getattr(self.database, table)[key] = row
        self.pending = []

    def rollback(self):
        self.pending = []

    def close(self):
        self.closed = True


class FakeCursor:
    def __init__(self, connection):
        self.connection, self.rows = connection, []

    def execute(self, statement, parameters=()):
        connection, database = self.connection, self.connection.database
        text = " ".join(statement.split())
        connection.statements.append(text)
        if text == "SET TRANSACTION READ ONLY":
            connection.read_only = True
        elif text.startswith("INSERT INTO runtime_archive.run_archive "):
            assert not connection.read_only and parameters[0] not in database.archives
            connection.pending.append(("archives", parameters[0],
                                       (*parameters[:2], "2026-11-26T00:00:00.000000Z", *parameters[2:])))
        elif text.startswith("INSERT INTO runtime_archive.archive_content "):
            archive_id, path, size, digest, body = parameters
            if database.fail_insert:
                raise FakeDatabaseError(database.fail_insert)
            assert len(body) == size and hashlib.sha256(body).hexdigest() == digest
            connection.pending.append(("contents", (archive_id, path), (path, size, digest, body)))
        elif text.startswith("SELECT archive_id,"):
            row = database.archives.get(parameters[0])
            self.rows = [] if row is None else [row]
        elif text.startswith("SELECT content_path,"):
            self.rows = [(path, size, digest, body + b"!" if path == database.corrupt else body)
                         for (archive_id, _), (path, size, digest, body) in sorted(database.contents.items())
                         if archive_id == parameters[0]]
        else:
            raise AssertionError(f"unexpected statement: {text}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def close(self):
        pass


def _files(folder):
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in Path(folder).rglob("*") if path.is_file()}


def _attempt_files(folder):
    return {name: body for name, body in _files(folder).items() if name not in _CONTROL}


@pytest.fixture
def run(environment, tmp_path):
    """One finished FakeCLI Test Run saved with --record-out semantics, plus an empty FakeDatabase."""
    root = environment[0]
    record = invoke(environment)
    path = write_test_run_record(tmp_path / "record.json", record)
    (row,) = record["attempt_workspaces"]
    assert row["sealed"] is True
    database = FakeDatabase()
    return root, record, path, Path(row["workspace_path"]), database, PostgresTestRunArchiveStore(database.connect)


def _save(run, tmp_path, name="receipt.json"):
    root, _, path, _, _, store = run
    return save_test_run_archive(root, path, receipt_out=tmp_path / name, store=store)


# --- Save, independent readback, retrieval ----------------------------------------------------

@pytest.mark.fake_run
def test_save_reads_back_on_a_new_connection_and_read_returns_record_logs_and_files(run, tmp_path):
    root, record, path, folder, database, store = run
    receipt = _save(run, tmp_path)
    assert receipt["archive_status"] == "verified" and receipt["error"] is None
    assert receipt["run_status"] == "completed" and receipt["scene_complete"] is True
    assert receipt["attempt_ids"] == [record["attempt_id"]]
    assert json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8")) == receipt
    writer, reader = database.connections
    assert writer is not reader and writer.closed and reader.closed
    assert reader.read_only and not writer.read_only and reader.statements[0] == "SET TRANSACTION READ ONLY"
    # The record bytes and every ordinary file of the Attempt folder, structure kept; control files skipped.
    stored = {content_path: body for (_, content_path), (_, _, _, body) in database.contents.items()}
    attempt = f"attempts/{record['attempt_id']}/"
    assert stored == {"record/execution_record.json": path.read_bytes(),
                      **{attempt + name: body for name, body in _attempt_files(folder).items()}}
    assert attempt + "archive/execution_record.json" in stored
    manifest = json.loads(database.archives[receipt["archive_id"]][5])
    assert manifest["run"]["runtime_version"] == record["runtime_version"]  # From the record, not this host.
    assert set(manifest["archive_host"]) == {"observed_at_utc", "runtime_package_version", "python_version",
                                             "python_implementation", "python_executable", "os_platform", "machine"}

    restored = tmp_path / "restored"
    output = read_test_run_archive(root, tmp_path / "receipt.json", view="output", extract_to=restored, store=store)
    assert output["verified"] and output["run_status"] == "completed"
    assert output["record_inspection"]["output"] == record["output"]
    assert output["record_inspection"]["identity"]["workflow_execution_id"] == record["workflow_execution_id"]
    assert [item["relative_path"] for item in output["archive"]["files"]] == sorted(_attempt_files(folder))
    assert _files(restored) == {"record/execution_record.json": path.read_bytes(),
                                **{attempt + name: body for name, body in _attempt_files(folder).items()}}
    log = read_test_run_archive(root, receipt, view="log", store=store)["record_inspection"]["execution_log"]
    assert log == json.loads(path.read_text(encoding="utf-8"))["execution_log"] and log["complete"]
    assert len(database.connections) == 4  # Each read is its own connection.


# --- Save failures leave the scene and the run outcome ----------------------------------------

@pytest.mark.fake_run
@pytest.mark.parametrize("failure,status", [("insert", "write_failed"), ("corrupt", "readback_failed")])
def test_failed_save_leaves_the_scene_and_the_run_outcome(run, tmp_path, failure, status):
    root, record, path, folder, database, store = run
    record_bytes, scene = path.read_bytes(), _files(folder)
    if failure == "insert":
        database.fail_insert = "value violates check constraint password=never-echoed"
    else:
        database.corrupt = f"attempts/{record['attempt_id']}/archive/execution_record.json"
    receipt = _save(run, tmp_path)
    assert receipt["archive_status"] == status and receipt["run_status"] == "completed"
    assert "never-echoed" not in json.dumps(receipt)
    assert _files(folder) == scene and path.read_bytes() == record_bytes
    if failure == "insert":
        assert database.archives == {} and receipt["error"] == {
            "error_type": "FakeDatabaseError", "error_code": None, "sqlstate": "23514", "detail": None}
    cleaned = cleanup_test_run_archive(root, receipt, store=store)
    assert cleaned["archive_verified"] is False and cleaned["complete"] is False
    assert [row["retained_reasons"] for row in cleaned["attempts"]] == [["archive_unavailable"]]
    assert _files(folder) == scene


@pytest.mark.fake_run
def test_a_failed_model_run_is_archived_without_becoming_a_success(environment, tmp_path):
    environment[4]["response_model"] = "claude-sonnet-4-6"
    record = invoke(environment)
    assert record["status"] == "failed"
    store = PostgresTestRunArchiveStore(FakeDatabase().connect)
    receipt = save_test_run_archive(environment[0], record, receipt_out=tmp_path / "receipt.json", store=store)
    assert (receipt["archive_status"], receipt["run_status"]) == ("verified", "failed")
    read = read_test_run_archive(environment[0], receipt, view="output", store=store)
    assert read["run_status"] == read["record_inspection"]["outcome"] == "failed"
    assert read["archive"]["run"]["failure_code"] == "claude_cli_model_identity_mismatch"


# --- Retained: live lease, caller hold, changed or unsupported scene ---------------------------

@pytest.mark.fake_run
@pytest.mark.parametrize("case,reason", [("lease", "lease_active"), ("hold", "retention_hold"),
                                         ("late_file", "scene_changed_since_archive")])
def test_cleanup_retains_a_leased_held_or_changed_scene(run, tmp_path, case, reason):
    root, _, _, folder, _, store = run
    receipt = _save(run, tmp_path)
    if case == "late_file":
        (folder / "scratch_after_save.txt").write_text("not archived", encoding="utf-8")
    if case == "lease":
        with workspaces._hold_lease(folder):
            cleaned = cleanup_test_run_archive(root, receipt, store=store)
    else:
        cleaned = cleanup_test_run_archive(root, receipt, store=store, retention_hold=case == "hold")
    (row,) = cleaned["attempts"]
    assert cleaned["archive_verified"] and not cleaned["complete"]
    assert (row["outcome"], row["retained_reasons"]) == ("retained", [reason]) and folder.is_dir()


@pytest.mark.fake_run
def test_a_link_in_the_scene_is_not_followed_and_keeps_the_scene(run, tmp_path):
    root, record, _, folder, database, store = run
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "original.txt").write_bytes(b"caller original")
    if os.name == "nt":
        from windows_junction_helpers import create_junction
        create_junction(folder / "linked", outside)
    else:
        (folder / "linked").symlink_to(outside, target_is_directory=True)
    receipt = _save(run, tmp_path)
    assert receipt["archive_status"] == "verified" and receipt["scene_complete"] is False
    manifest = json.loads(database.archives[receipt["archive_id"]][5])
    (attempt,) = manifest["attempts"]
    assert attempt["scene"] == "incomplete"
    assert attempt["unarchived_entries"] == [{"relative_path": "linked", "kind": "link"}]
    assert not any("original" in item["relative_path"] for item in manifest["files"])
    cleaned = cleanup_test_run_archive(root, receipt, store=store)
    assert cleaned["attempts"][0]["retained_reasons"] == ["scene_not_fully_archived"] and folder.is_dir()
    assert (outside / "original.txt").read_bytes() == b"caller original"


# --- Cleanup removes only this run's Attempt ---------------------------------------------------

@pytest.mark.fake_run
def test_cleanup_removes_only_this_runs_attempt_and_the_archive_stays_readable(environment, run, tmp_path):
    root, record, path, folder, database, store = run
    other = invoke(environment)  # Another run's Attempt in the same root.
    other_folder = Path(other["attempt_workspaces"][0]["workspace_path"])
    other_scene = _files(other_folder)
    project = tmp_path / "caller_project"
    (project / "reports").mkdir(parents=True)
    (project / "reports" / "summary.md").write_text("caller deliverable", encoding="utf-8")
    project_files, record_bytes = _files(project), path.read_bytes()
    business = json.loads(json.dumps(database.business))
    receipt = _save(run, tmp_path)
    assert cleanup_test_run_archive(root, receipt, store=store, query=True)["attempts"][0]["outcome"] == "cleanable"
    assert folder.is_dir()
    cleaned = cleanup_test_run_archive(root, receipt, store=store)
    assert cleaned["complete"] is True and [row["outcome"] for row in cleaned["attempts"]] == ["deleted"]
    assert not folder.exists()
    assert _files(other_folder) == other_scene and _files(project) == project_files
    assert path.read_bytes() == record_bytes and database.business == business
    assert all("runtime_archive." in text for text in database.statements() if text.startswith(("INSERT", "SELECT")))
    again = cleanup_test_run_archive(root, receipt, store=store)
    assert again["complete"] is True and again["attempts"][0]["outcome"] == "absent"
    read = read_test_run_archive(root, receipt, view="output", extract_to=tmp_path / "after_cleanup", store=store)
    assert read["record_inspection"]["output"] == record["output"]
    assert read["record_inspection"]["attempts"][0]["workspace_exists"] is False
    assert (tmp_path / "after_cleanup" / "record" / "execution_record.json").read_bytes() == record_bytes


@pytest.mark.fake_run
def test_save_refuses_a_record_whose_attempts_belong_to_another_root(run, tmp_path):
    root, record, _, _, database, store = run
    moved = json.loads(json.dumps(record))
    moved["attempt_workspaces"][0]["workspace_path"] = str(tmp_path / "elsewhere" / record["attempt_id"])
    with pytest.raises(ValueError, match="attempt_workspace_root"):
        save_test_run_archive(root, moved, receipt_out=tmp_path / "receipt.json", store=store)
    with pytest.raises(ValueError, match="owns no Attempt"):
        save_test_run_archive(root, {"error_type": "ValueError", "detail": "x"},
                              receipt_out=tmp_path / "receipt.json", store=store)
    assert database.connections == [] and not (tmp_path / "receipt.json").exists()


# --- Configured binding: private credential, code-written receipt, installed CLI loop -----------

_CREDENTIAL = "project001.postgresql.comms_app"


def _configure(root, monkeypatch, tmp_path, *, reply):
    config = Path(root) / ".runtime" / "config.json"
    document = json.loads(config.read_text(encoding="utf-8")) if config.is_file() else {}
    document["run_archive"] = {"host": "localhost", "port": 5432, "database": "the_workspace",
                               "user": "comms_app", "schema": "runtime_archive", "password_credential": _CREDENTIAL}
    config.write_text(json.dumps(document), encoding="utf-8")
    client = tmp_path / "local_app_data" / Path(*archiving._CREDENTIAL_CLIENT)
    client.parent.mkdir(parents=True)
    client.write_bytes(b"")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local_app_data"))
    calls, connections, database = [], [], FakeDatabase()

    def credential_client(argv, **kwargs):
        calls.append((argv, kwargs))
        return reply(argv)

    class Psycopg:
        @staticmethod
        def connect(**kwargs):
            connections.append(kwargs)
            return database.connect()

    monkeypatch.setattr(archiving.subprocess, "run", credential_client)
    monkeypatch.setattr(archiving, "_psycopg", lambda: Psycopg)
    return client, calls, connections, database


@pytest.mark.fake_run
def test_configured_cli_loop_reads_the_password_privately_and_never_records_it(run, tmp_path, monkeypatch, capsys):
    root, record, path, folder, _, _ = run
    secret = "sentinel-" + uuid.uuid4().hex
    client, calls, connections, database = _configure(
        root, monkeypatch, tmp_path, reply=lambda argv: subprocess.CompletedProcess(argv, 0, json.dumps(secret), ""))
    assert load_runtime_config(root)["run_archive"]["password_credential"] == _CREDENTIAL
    receipt_path = tmp_path / "receipt.json"
    texts = []

    def cli(*argv):
        status = archiving.main([*argv, "--root", str(root)])
        captured = capsys.readouterr()
        texts.extend((captured.out, captured.err))
        return status, captured.out

    status, out = cli("save", "--record", str(path), "--receipt-out", str(receipt_path))
    receipt = json.loads(out)
    assert status == 0 and receipt["archive_status"] == "verified"
    assert re.fullmatch(r"run_archive_[0-9a-f]{32}", receipt["archive_id"])
    assert receipt["record_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert receipt["archive_binding"] == {"host": "localhost", "port": 5432, "database": "the_workspace",
                                          "user": "comms_app", "schema": "runtime_archive",
                                          "password_credential": _CREDENTIAL}
    status, out = cli("cleanup", "--receipt", str(receipt_path))
    assert status == 0 and json.loads(out)["attempts"][0]["outcome"] == "deleted" and not folder.exists()
    status, out = cli("read", "--receipt", str(receipt_path), "--view", "output")
    assert status == 0 and json.loads(out)["record_inspection"]["output"] == record["output"]
    # Write, readback, cleanup readback and read: four connections, each with a fresh credential read.
    assert [argv for argv, _ in calls] == [[str(client), "get", _CREDENTIAL]] * 4
    assert all(kwargs["stdin"] is subprocess.DEVNULL and kwargs["capture_output"] for _, kwargs in calls)
    assert [connection["password"] for connection in connections] == [secret] * 4
    assert {key for key in connections[0] if key != "password"} == {
        "host", "port", "dbname", "user", "connect_timeout", "options"}
    texts += [receipt_path.read_text(encoding="utf-8"), *(row[5] for row in database.archives.values())]
    assert not any(secret in text for text in texts) and secret not in os.environ.values()


@pytest.mark.fake_run
def test_a_credential_failure_is_an_archive_failure_with_only_its_code(run, tmp_path, monkeypatch, capsys):
    root, _, path, folder, _, _ = run
    scene = _files(folder)
    _, _, connections, database = _configure(root, monkeypatch, tmp_path, reply=lambda argv: subprocess.CompletedProcess(
        argv, 1, "", "LOGIN_REQUIRED: open the existing login window"))
    status = archiving.main(["save", "--root", str(root), "--record", str(path),
                             "--receipt-out", str(tmp_path / "receipt.json")])
    receipt = json.loads(capsys.readouterr().out)
    assert status == 1 and receipt["archive_status"] == "write_failed"
    assert receipt["error"]["error_code"] == "LOGIN_REQUIRED" and connections == []
    assert _files(folder) == scene and database.archives == {}
    status = archiving.main(["cleanup", "--root", str(root), "--receipt", str(tmp_path / "receipt.json")])
    assert status == 1 and json.loads(capsys.readouterr().out)["attempts"][0]["outcome"] == "retained"
    assert _files(folder) == scene


@pytest.mark.deterministic
def test_binding_and_ddl_shapes(tmp_path):
    config = tmp_path / ".runtime" / "config.json"
    config.parent.mkdir(parents=True)
    binding = {"host": "localhost", "port": 5432, "database": "the_workspace", "user": "comms_app",
               "schema": "runtime_archive", "password_credential": _CREDENTIAL}
    for change in ({"port": "5432"}, {"schema": "Runtime"}, {"password_credential": "Bad Name"},
                   {"password": "x"}, {"database_url_env": "DSN"}):
        config.write_text(json.dumps({"run_archive": {**binding, **change}}), encoding="utf-8")
        with pytest.raises(ValueError, match="run_archive"):
            load_runtime_config(tmp_path)
    config.write_text(json.dumps({"release_stores": {}}), encoding="utf-8")
    with pytest.raises(ValueError, match="no run_archive binding"):
        archiving.open_configured_run_archive(tmp_path)
    statements = run_archive_ddl(grant_role="comms_app")
    assert statements[0] == "CREATE SCHEMA runtime_archive"
    assert statements[-1] == ("GRANT SELECT, INSERT ON runtime_archive.run_archive, runtime_archive.archive_content "
                              "TO comms_app")
    assert not any(word in " ".join(statements) for word in ("UPDATE", "DELETE", "TRUNCATE"))


# --- Real PostgreSQL --------------------------------------------------------------------------

# Read at import: the shared `environment` fixture removes this variable before the test body runs.
_TEST_DATABASE_URL = os.environ.get("AGENT_RUNTIME_TEST_DATABASE_URL")


@pytest.mark.real_run
@pytest.mark.skipif(not _TEST_DATABASE_URL, reason="requires AGENT_RUNTIME_TEST_DATABASE_URL")
def test_postgres_archive_round_trip_and_cleanup(environment, tmp_path):
    import psycopg
    database_url = _TEST_DATABASE_URL
    schema = f"runtime_archive_test_{uuid.uuid4().hex[:16]}"
    record = invoke(environment)
    folder = Path(record["attempt_workspaces"][0]["workspace_path"])
    path = write_test_run_record(tmp_path / "record.json", record)
    try:
        with psycopg.connect(database_url, autocommit=True) as connection:
            for statement in run_archive_ddl(schema):
                connection.execute(statement)
        store = PostgresTestRunArchiveStore.from_dsn(database_url, schema=schema)
        receipt = save_test_run_archive(environment[0], path, receipt_out=tmp_path / "receipt.json", store=store)
        assert receipt["archive_status"] == "verified", receipt["error"]
        cleaned = cleanup_test_run_archive(environment[0], receipt, store=store)
        assert cleaned["complete"] and not folder.exists()
        read = read_test_run_archive(environment[0], receipt, view="output", extract_to=tmp_path / "restored",
                                     store=PostgresTestRunArchiveStore.from_dsn(database_url, schema=schema))
        assert read["record_inspection"]["output"] == record["output"]
        assert (tmp_path / "restored" / "record" / "execution_record.json").read_bytes() == path.read_bytes()
    finally:
        with psycopg.connect(database_url, autocommit=True) as connection:
            connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
