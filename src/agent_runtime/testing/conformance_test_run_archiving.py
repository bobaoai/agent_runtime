"""Save a finished Test Run to the central Runtime archive, read it back, and clean only its own Attempts.

agent-runtime-archive is an explicit host step after one ordinary Test Run
(persistence not_requested). save copies the run's record and the ordinary
files of its own Attempt folders into the Runtime archive schema, commits, and
reads everything back through a new connection before writing a code-generated
receipt. read verifies the archive again from the database and returns the
archived record, logs and file index, optionally restoring the files. cleanup
verifies the archive again, checks that each Attempt folder still holds exactly
what was saved, and passes only those folders to the existing
maintain_attempt_workspaces.

The archive is the Runtime run-record layer. It is not a Workflow Ledger and
does not turn a Test Run into production history; the run's own outcome and
the archive outcome stay separate facts. The caller's project directory and
any business database or schema are recorded only as the references the run
record already holds: they are never copied, cleaned or written. Archive rows
are insert-only and kept; nothing here deletes them.
"""

from __future__ import annotations

import argparse
from datetime import timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping
import uuid

from ..foundation.foundation_contract_validation import validate_id
from ..foundation.foundation_environment_setup import load_runtime_config
from ..inspection.inspection_test_run_reading import _IDENTITY, inspect_test_run_record
from ..invocation.invocation_workspace_preparation import (
    _MARKER_NAME, _SEAL_NAME, AttemptWorkspaceRetentionFacts, _ordinary_directory, _read_record,
    maintain_attempt_workspaces, utc_now_instant,
)
from .conformance_local_test_run import _TERMINAL_STATUSES, _process_stop_confirmed


RUN_ARCHIVE_FORMAT = "runtime_run_archive_v1"
RUN_ARCHIVE_RECEIPT_SCHEMA_VERSION = "runtime_run_archive_receipt_v1"
RUN_ARCHIVE_READING_SCHEMA_VERSION = "runtime_run_archive_reading_v1"
RUN_ARCHIVE_CLEANUP_SCHEMA_VERSION = "runtime_run_archive_cleanup_v1"
RUN_ARCHIVE_DEFAULT_SCHEMA = "runtime_archive"
RUN_ARCHIVE_VIEWS = ("summary", "output", "log")

_SCHEMA = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_ARCHIVE_ID = re.compile(r"^run_archive_[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_RECORD_CONTENT_PATH = "record/execution_record.json"
_CONTROL_FILES = (_MARKER_NAME, _SEAL_NAME)
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_CONNECTION_OPTIONS = "-c client_encoding=UTF8 -c timezone=UTC"
# The installed central credential client, per the credential-access Skill binding.
_CREDENTIAL_CLIENT = ("the_workspace", "credentials", "app", "venv", "Scripts", "wscred.exe")
_CREDENTIAL_CODES = frozenset({"LOGIN_REQUIRED", "SERVICE_UNAVAILABLE", "NOT_FOUND", "INVALID_REQUEST",
                               "STORAGE_ERROR"})


class RunArchiveVerificationError(RuntimeError):
    """The archive read from the database is absent or differs from its own recorded sizes and hashes."""


class RunArchiveCredentialError(RuntimeError):
    """The central credential client did not return the configured password; carries only its error code."""

    def __init__(self, error_code: str):
        self.error_code = error_code
        super().__init__(f"central credential client failed: {error_code}")


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _validate_schema(schema: str) -> str:
    if type(schema) is not str or not _SCHEMA.fullmatch(schema):
        raise ValueError("run archive schema must be a lowercase PostgreSQL identifier")
    return schema


def run_archive_ddl(schema: str = RUN_ARCHIVE_DEFAULT_SCHEMA, *, grant_role: str | None = None) -> tuple[str, ...]:
    """Return the statements that create the Runtime run-archive schema.

    Args:
        schema: New schema to create, default runtime_archive.
        grant_role: Optional existing application role that receives USAGE on
            the schema and only SELECT and INSERT on its two tables, so archive
            rows cannot be changed or deleted through that role.
    Returns:
        CREATE SCHEMA, the run_archive table (one row per saved run: its
        manifest bytes and SHA-256, the run's own status and execution id) and
        the archive_content table (one row per saved file: path, size, SHA-256
        and bytes), then the grants. The database owner applies them once with
        its own administrative connection.
    Raises:
        ValueError: schema or grant_role is not a lowercase identifier.
    Effects:
        None. Saving never runs DDL.
    """
    schema = _validate_schema(schema)
    statements = [
        f"CREATE SCHEMA {schema}",
        f"""CREATE TABLE {schema}.run_archive (
            archive_id TEXT PRIMARY KEY CHECK (archive_id ~ '^run_archive_[0-9a-f]{{32}}$'),
            archive_format TEXT NOT NULL,
            archived_at_utc TIMESTAMPTZ NOT NULL DEFAULT now(),
            run_status TEXT NOT NULL,
            workflow_execution_id TEXT,
            manifest_json TEXT NOT NULL,
            manifest_sha256 CHAR(64) NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{{64}}$')
        )""",
        f"""CREATE TABLE {schema}.archive_content (
            archive_id TEXT NOT NULL REFERENCES {schema}.run_archive(archive_id),
            content_path TEXT NOT NULL,
            size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
            sha256 CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            body BYTEA NOT NULL,
            PRIMARY KEY (archive_id, content_path),
            CHECK (octet_length(body) = size_bytes)
        )""",
    ]
    if grant_role is not None:
        if type(grant_role) is not str or not _SCHEMA.fullmatch(grant_role):
            raise ValueError("grant_role must be a lowercase PostgreSQL role name")
        statements += [f"GRANT USAGE ON SCHEMA {schema} TO {grant_role}",
                       f"GRANT SELECT, INSERT ON {schema}.run_archive, {schema}.archive_content TO {grant_role}"]
    return tuple(statements)


class PostgresTestRunArchiveStore:
    """PostgreSQL Runtime run archive: one manifest row and its content rows per saved Test Run.

    Every call opens its own connection from connection_factory, so a readback
    never reuses the connection that wrote. Rows are only inserted. The
    database client is imported only by from_dsn or the configured binding.
    """

    def __init__(self, connection_factory: Callable[[], Any], *, schema: str = RUN_ARCHIVE_DEFAULT_SCHEMA) -> None:
        if not callable(connection_factory):
            raise ValueError("connection_factory must be callable")
        self._connection_factory = connection_factory
        self.schema = _validate_schema(schema)

    @classmethod
    def from_dsn(cls, database_url: str, *, schema: str = RUN_ARCHIVE_DEFAULT_SCHEMA,
                 connect_timeout: int = 8) -> "PostgresTestRunArchiveStore":
        """Open the archive through a DSN, for an isolated test database or a host that holds its own DSN.

        Args:
            database_url: libpq connection string; kept only in the connection factory.
            schema: Archive schema created by run_archive_ddl.
            connect_timeout: Seconds for each new connection.
        Returns:
            A store whose connections use psycopg with UTF-8 and UTC.
        Raises:
            ValueError: Empty DSN or invalid timeout.
            RuntimeError: psycopg is not installed (agent-runtime-core[postgres]).
        """
        if type(database_url) is not str or not database_url:
            raise ValueError("database_url is required")
        if type(connect_timeout) is not int or connect_timeout < 1:
            raise ValueError("connect_timeout must be a positive integer")
        psycopg = _psycopg()
        return cls(lambda: psycopg.connect(database_url, connect_timeout=connect_timeout,
                                           options=_CONNECTION_OPTIONS), schema=schema)

    def write_archive(self, *, archive_id: str, run_status: str, workflow_execution_id: str | None,
                      manifest_json: str, contents: Mapping[str, bytes]) -> None:
        """Insert one archive row and all its content rows in one transaction, then commit.

        Args:
            archive_id: Code-generated run_archive_ identifier.
            run_status: The archived run's own status, stored as given.
            workflow_execution_id: The run's execution id, or None.
            manifest_json: Manifest text; its UTF-8 SHA-256 is stored beside it.
            contents: Content path to exact bytes; size and SHA-256 are computed here.
        Raises:
            ValueError: Invalid archive id or content.
            Exception: The database error; nothing is committed.
        """
        if type(archive_id) is not str or not _ARCHIVE_ID.fullmatch(archive_id):
            raise ValueError("archive_id must be a code-generated run_archive_ identifier")
        if type(manifest_json) is not str or any(type(body) is not bytes for body in contents.values()):
            raise ValueError("manifest_json must be text and every content body bytes")

        def write(cursor: Any) -> None:
            cursor.execute(
                f"INSERT INTO {self.schema}.run_archive (archive_id, archive_format, run_status, "
                "workflow_execution_id, manifest_json, manifest_sha256) VALUES (%s, %s, %s, %s, %s, %s)",
                (archive_id, RUN_ARCHIVE_FORMAT, run_status, workflow_execution_id, manifest_json,
                 _sha256(manifest_json.encode("utf-8"))))
            for content_path, body in contents.items():
                cursor.execute(
                    f"INSERT INTO {self.schema}.archive_content (archive_id, content_path, size_bytes, sha256, body) "
                    "VALUES (%s, %s, %s, %s, %s)", (archive_id, content_path, len(body), _sha256(body), body))

        self._transaction(write)

    def read_archive(self, archive_id: str) -> dict:
        """Read one archive and every content row through a new read-only transaction.

        Returns:
            archive_id, archive_format, archived_at_utc, run_status,
            workflow_execution_id, manifest_json, manifest_sha256 and contents
            (content path to size_bytes, sha256 and body bytes), as stored.
            The caller verifies them; this method does not.
        Raises:
            KeyError: No archive with this id.
            Exception: The database error.
        """
        if type(archive_id) is not str or not _ARCHIVE_ID.fullmatch(archive_id):
            raise ValueError("archive_id must be a run_archive_ identifier")

        def read(cursor: Any) -> dict:
            cursor.execute(
                f"SELECT archive_id, archive_format, archived_at_utc, run_status, workflow_execution_id, "
                f"manifest_json, manifest_sha256 FROM {self.schema}.run_archive WHERE archive_id = %s", (archive_id,))
            row = cursor.fetchone()
            if row is None:
                raise KeyError(f"run archive {archive_id} is not in {self.schema}")
            cursor.execute(
                f"SELECT content_path, size_bytes, sha256, body FROM {self.schema}.archive_content "
                "WHERE archive_id = %s ORDER BY content_path", (archive_id,))
            contents = {path: {"size_bytes": int(size), "sha256": str(digest).strip(), "body": bytes(body)}
                        for path, size, digest, body in cursor.fetchall()}
            archived_at = row[2]
            if hasattr(archived_at, "astimezone"):
                archived_at = archived_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            return {"archive_id": row[0], "archive_format": row[1], "archived_at_utc": archived_at,
                    "run_status": row[3], "workflow_execution_id": row[4], "manifest_json": row[5],
                    "manifest_sha256": str(row[6]).strip(), "contents": contents}

        return self._transaction(read, read_only=True)

    def _transaction(self, operation: Callable[[Any], Any], *, read_only: bool = False) -> Any:
        connection = self._connection_factory()
        try:
            cursor = connection.cursor()
            try:
                if read_only:
                    cursor.execute("SET TRANSACTION READ ONLY")
                result = operation(cursor)
            finally:
                cursor.close()
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _psycopg():
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on optional install
        raise RuntimeError("the run archive requires agent-runtime-core[postgres]") from exc
    return psycopg


def _read_password_credential(name: str) -> str:
    """The configured password, read privately through the installed central credential client.

    Only the credential name is passed as an argument; the value is parsed in
    memory and returned to the caller, never printed, logged or stored. A client
    failure raises RunArchiveCredentialError with the client's error code only.
    """
    local = os.environ.get("LOCALAPPDATA")
    client = None if not local else Path(local).joinpath(*_CREDENTIAL_CLIENT)
    if client is None or not client.is_file():
        raise RunArchiveCredentialError("CLIENT_NOT_INSTALLED")
    try:
        completed = subprocess.run(
            [str(client), "get", name], stdin=subprocess.DEVNULL, capture_output=True, text=True,
            encoding="utf-8", check=False, timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise RunArchiveCredentialError("CLIENT_FAILED") from None
    if completed.returncode:
        match = re.match(r"([A-Z_]+)(?=[:\s]|$)", (completed.stderr or "").strip())
        raise RunArchiveCredentialError(match[1] if match and match[1] in _CREDENTIAL_CODES
                                        else "CLIENT_FAILED")
    try:
        value = json.loads(completed.stdout)
    except (ValueError, RecursionError):
        raise RunArchiveCredentialError("INVALID_VALUE") from None
    if type(value) is not str or not value or "\x00" in value:
        raise RunArchiveCredentialError("INVALID_VALUE")
    return value


def _binding_locator(binding: dict) -> dict:
    return {name: binding[name] for name in ("host", "port", "database", "user", "schema", "password_credential")}


def open_configured_run_archive(root: Path) -> PostgresTestRunArchiveStore:
    """Open the run archive named by root/.runtime/config.json run_archive.

    Each new connection reads the binding's password_credential through the
    installed central credential client and passes the value only to the
    database connection. Raises ValueError when no run_archive is configured.
    Opening performs no query.
    """
    binding = load_runtime_config(Path(root))["run_archive"]
    if binding is None:
        raise ValueError("root/.runtime/config.json has no run_archive binding; add the central archive's host, "
                         "port, database, user, schema and password_credential")

    def connect():
        psycopg = _psycopg()
        password = _read_password_credential(binding["password_credential"])
        return psycopg.connect(host=binding["host"], port=binding["port"], dbname=binding["database"],
                               user=binding["user"], password=password, connect_timeout=8,
                               options=_CONNECTION_OPTIONS)

    store = PostgresTestRunArchiveStore(connect, schema=binding["schema"])
    store.binding_locator = _binding_locator(binding)
    return store


def _store_for(root: Path, store) -> PostgresTestRunArchiveStore:
    if store is None:
        return open_configured_run_archive(root)
    if not (callable(getattr(store, "write_archive", None)) and callable(getattr(store, "read_archive", None))):
        raise ValueError("store must provide write_archive and read_archive")
    return store


def _locator(store) -> dict:
    locator = getattr(store, "binding_locator", None)
    return dict(locator) if isinstance(locator, dict) else {"schema": getattr(store, "schema", None)}


def _safe_error(exc: BaseException) -> dict:
    """Error facts without database messages, which may echo connection details."""
    database = (type(exc).__module__ or "").split(".")[0] == "psycopg"
    return {"error_type": type(exc).__name__, "error_code": getattr(exc, "error_code", None),
            "sqlstate": getattr(exc, "sqlstate", None) if database else None,
            "detail": None if database else str(exc)}


def _same(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _scene(folder: Path) -> tuple[list[tuple[str, Path]], list[str], list[dict]]:
    """Ordinary files, directories and entries that are not ordinary files, without following links.

    The Attempt's own Runtime marker and seal at its top level are control
    metadata and are skipped. Links, reparse points and special files are
    returned as unarchived entries.
    """
    files, directories, unarchived = [], [], []

    def visit(current: str, prefix: str) -> None:
        with os.scandir(current) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                relative = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & _REPARSE:
                    unarchived.append({"relative_path": relative, "kind": "link"})
                elif stat.S_ISDIR(info.st_mode):
                    directories.append(relative)
                    visit(entry.path, relative + "/")
                elif stat.S_ISREG(info.st_mode):
                    if not (prefix == "" and entry.name in _CONTROL_FILES):
                        files.append((relative, Path(entry.path)))
                else:
                    unarchived.append({"relative_path": relative, "kind": "special"})

    visit(str(folder), "")
    return files, directories, unarchived


def _owned(folder: Path, attempt_id: str) -> bool:
    marker = _read_record(folder / _MARKER_NAME) if _ordinary_directory(folder) else None
    return marker is not None and marker.get("attempt_id") == attempt_id


def _record_document(data: bytes):
    """A saved record's JSON: UTF-8 with or without BOM, or captured UTF-16, as inspect_test_run_record reads."""
    return json.loads(data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8-sig"))


def _record_source(record) -> tuple[bytes, dict, str | None]:
    if isinstance(record, dict):
        return (json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"), record, None
    if not isinstance(record, (str, os.PathLike)):
        raise ValueError("record must be a Test Run record object or the path of a saved record")
    path = Path(record).resolve()
    data = path.read_bytes()
    try:
        document = _record_document(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"saved Test Run record is not readable JSON: {path}") from exc
    if type(document) is not dict:
        raise ValueError(f"saved Test Run record is not a JSON object: {path}")
    return data, document, str(path)


def _new_file(path: Path, label: str) -> Path:
    path = Path(path)
    if os.path.lexists(path) or not path.parent.is_dir():
        raise ValueError(f"{label} must name a new file in an existing folder: {path}")
    return path


def _capture_attempts(document: dict, workspace_root: Path) -> tuple[list[dict], list[dict], dict[str, bytes]]:
    rows = document.get("attempt_workspaces")
    if type(rows) is not list or not rows or any(type(row) is not dict for row in rows):
        raise ValueError("the record lists no Attempt folders (attempt_workspaces); nothing of this run to archive")
    logged = {attempt.get("attempt_id"): attempt
              for attempt in ((document.get("execution_log") or {}).get("attempts") or ())
              if isinstance(attempt, dict)}
    ended = document.get("status") in _TERMINAL_STATUSES
    attempts, files, contents = [], [], {}
    for row in rows:
        attempt_id = row.get("attempt_id")
        validate_id("attempt_id", attempt_id)
        if any(entry["attempt_id"] == attempt_id for entry in attempts):
            raise ValueError(f"the record lists Attempt {attempt_id} twice")
        folder = workspace_root / attempt_id
        recorded = row.get("workspace_path")
        if type(recorded) is not str or _same(recorded) != _same(folder):
            raise ValueError(f"Attempt {attempt_id} was retained at {recorded}, not in this root's "
                             f"attempt_workspace_root {workspace_root}; archive it with the root that ran it")
        log = logged.get(attempt_id)
        entry = {"attempt_id": attempt_id, "workspace_path": str(folder),
                 "project_directory": row.get("project_directory"), "sealed": row.get("sealed") is True,
                 "seal_skipped_reason": row.get("seal_skipped_reason"), "execution_ended": ended,
                 "process_stop_confirmed": log is not None and _process_stop_confirmed(log),
                 "control_files_excluded": list(_CONTROL_FILES)}
        if not _owned(folder, attempt_id):
            entry.update(scene="not_captured", directories=[], unarchived_entries=[])
        else:
            ordinary, directories, unarchived = _scene(folder)
            for relative, path in ordinary:
                body = path.read_bytes()
                content_path = f"attempts/{attempt_id}/{relative}"
                contents[content_path] = body
                files.append({"content_path": content_path, "attempt_id": attempt_id, "relative_path": relative,
                              "size_bytes": len(body), "sha256": _sha256(body)})
            entry.update(scene="incomplete" if unarchived else "complete", directories=directories,
                         unarchived_entries=unarchived)
        attempts.append(entry)
    return attempts, files, contents


def _archive_host() -> dict:
    """Facts observed on this host while saving; they are not the original run's environment."""
    try:
        version = importlib.metadata.version("agent-runtime-core")
    except importlib.metadata.PackageNotFoundError:
        version = None
    return {"observed_at_utc": utc_now_instant(), "runtime_package_version": version,
            "python_version": platform.python_version(), "python_implementation": platform.python_implementation(),
            "python_executable": sys.executable, "os_platform": platform.platform(), "machine": platform.machine()}


def _verified(read: dict, archive_id: str, *, manifest_sha256: str | None) -> tuple[dict, dict[str, bytes]]:
    """Check what the database returned against its own manifest; return the manifest and verified bodies."""
    manifest_json = read.get("manifest_json")
    if read.get("archive_id") != archive_id or type(manifest_json) is not str:
        raise RunArchiveVerificationError(f"the database did not return archive {archive_id}")
    digest = _sha256(manifest_json.encode("utf-8"))
    if digest != read.get("manifest_sha256") or (manifest_sha256 is not None and digest != manifest_sha256):
        raise RunArchiveVerificationError("the archived manifest differs from its recorded SHA-256")
    try:
        manifest = json.loads(manifest_json)
        record = manifest["record"]
        expected = {record["content_path"]: (record["size_bytes"], record["sha256"]),
                    **{item["content_path"]: (item["size_bytes"], item["sha256"]) for item in manifest["files"]}}
        describes = (manifest["archive_format"] == RUN_ARCHIVE_FORMAT and manifest["archive_id"] == archive_id
                     and read.get("archive_format") == RUN_ARCHIVE_FORMAT
                     and read.get("run_status") == manifest["run"]["status"]
                     and record["content_path"] == _RECORD_CONTENT_PATH and type(manifest["attempts"]) is list)
    except (ValueError, KeyError, TypeError) as exc:
        raise RunArchiveVerificationError("the archived manifest is not a run archive manifest") from exc
    if not describes:
        raise RunArchiveVerificationError("the archived manifest does not describe this archive")
    stored = read.get("contents") or {}
    if set(stored) != set(expected):
        missing, extra = sorted(set(expected) - set(stored)), sorted(set(stored) - set(expected))
        raise RunArchiveVerificationError(f"archived content differs from the manifest: missing {missing}, "
                                          f"unexpected {extra}")
    bodies = {}
    for content_path, (size, sha256) in expected.items():
        row = stored[content_path]
        body = row["body"]
        if (type(body) is not bytes or len(body) != size or row["size_bytes"] != size
                or _sha256(body) != sha256 or row["sha256"] != sha256):
            raise RunArchiveVerificationError(f"archived content {content_path} differs from its size or SHA-256")
        bodies[content_path] = body
    return manifest, bodies


def save_test_run_archive(root: Path, record, *, receipt_out: Path, store=None) -> dict:
    """Save one finished Test Run to the central Runtime archive and verify it through a new connection.

    Args:
        root: Host root that ran the Test Run; its root/.runtime/config.json
            gives attempt_workspace_root and the run_archive binding.
        record: The record run_local_workflow_test returned, or the file
            agent-runtime-test-run --record-out saved. An error document
            without status owns no Attempt and is refused.
        receipt_out: New receipt file in an existing folder; written by code on
            success and on failure.
        store: Optional trusted store with write_archive and read_archive
            (such as PostgresTestRunArchiveStore.from_dsn for an isolated
            database); None opens the configured run_archive binding.
    Returns:
        The receipt: archive_id, archive_status (verified, write_failed or
        readback_failed) and error, beside the run's own run_status, which is
        never changed; root, record_path, record_sha256, attempt_ids,
        scene_complete, the binding locator (names only), manifest_sha256,
        content_count, content_bytes and saved_at_utc.
    Raises:
        ValueError: Not a Test Run record, an Attempt folder outside this
            root's attempt_workspace_root, no run_archive binding, or an
            existing receipt. Nothing is written to the database.
    Effects:
        Reads the record and the ordinary files of each listed Attempt folder
        (links are not followed; Runtime marker and seal files are skipped;
        links and special files are listed as unarchived and leave the scene
        incomplete). Never reads the project directory, leases or credentials
        beyond the binding's own password. Inserts one archive in one
        transaction, then reads it back on a new connection and checks the
        manifest, the exact file set, and every body's size and SHA-256. Writes
        the receipt. Never changes the record or any Attempt folder.
    """
    root = Path(root).resolve()
    receipt_out = _new_file(receipt_out, "receipt_out")
    record_bytes, document, record_path = _record_source(record)
    if "status" not in document:
        raise ValueError("this is the error document of a Test Run that returned no record; it owns no Attempt "
                         "folder to archive")
    if document.get("persistence") != "not_requested" or type(document["status"]) is not str:
        raise ValueError("the run archive covers ordinary Test Run records (persistence not_requested)")
    workspace_root = Path(load_runtime_config(root)["attempt_workspace_root"]).resolve()
    attempts, files, contents = _capture_attempts(document, workspace_root)
    store = _store_for(root, store)
    archive_id = "run_archive_" + uuid.uuid4().hex
    failure = document.get("failure_detail")
    run = {"status": document["status"], "failure_class": document.get("failure_class"),
           "failure_code": failure.get("failure_code") if isinstance(failure, dict) else None,
           "persistence": document.get("persistence"),
           **{name: document.get(name) for name in _IDENTITY}}
    manifest = {"archive_format": RUN_ARCHIVE_FORMAT, "archive_id": archive_id, "root": str(root),
                "attempt_workspace_root": str(workspace_root), "run": run,
                "record": {"content_path": _RECORD_CONTENT_PATH, "source_path": record_path,
                           "size_bytes": len(record_bytes), "sha256": _sha256(record_bytes)},
                "attempts": attempts, "files": files, "archive_host": _archive_host(),
                "archive_binding": _locator(store)}
    manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    contents = {_RECORD_CONTENT_PATH: record_bytes, **contents}
    receipt = {"schema_version": RUN_ARCHIVE_RECEIPT_SCHEMA_VERSION, "archive_id": archive_id,
               "archive_status": None, "error": None, "run_status": document["status"],
               "workflow_execution_id": document.get("workflow_execution_id"), "root": str(root),
               "record_path": record_path, "record_sha256": _sha256(record_bytes),
               "attempt_ids": [entry["attempt_id"] for entry in attempts],
               "scene_complete": all(entry["scene"] == "complete" for entry in attempts),
               "archive_binding": manifest["archive_binding"],
               "manifest_sha256": _sha256(manifest_json.encode("utf-8")),
               "content_count": len(contents), "content_bytes": sum(map(len, contents.values()))}
    try:
        store.write_archive(archive_id=archive_id, run_status=document["status"],
                            workflow_execution_id=document.get("workflow_execution_id"),
                            manifest_json=manifest_json, contents=contents)
    except Exception as exc:
        receipt.update(archive_status="write_failed", error=_safe_error(exc))
    else:
        try:
            _verified(store.read_archive(archive_id), archive_id, manifest_sha256=receipt["manifest_sha256"])
            receipt["archive_status"] = "verified"
        except Exception as exc:
            receipt.update(archive_status="readback_failed", error=_safe_error(exc))
    receipt["saved_at_utc"] = utc_now_instant()
    with open(receipt_out, "x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(receipt, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    return receipt


def _read_receipt(receipt) -> dict:
    if isinstance(receipt, dict):
        document = receipt
    else:
        try:
            document = json.loads(Path(receipt).read_bytes().decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"run archive receipt is not readable JSON: {receipt}") from exc
    if (type(document) is not dict or document.get("schema_version") != RUN_ARCHIVE_RECEIPT_SCHEMA_VERSION
            or type(document.get("archive_id")) is not str or not _ARCHIVE_ID.fullmatch(document["archive_id"])
            or type(document.get("manifest_sha256")) is not str or not _SHA256.fullmatch(document["manifest_sha256"])
            or type(document.get("attempt_ids")) is not list):
        raise ValueError(f"not a {RUN_ARCHIVE_RECEIPT_SCHEMA_VERSION} document written by agent-runtime-archive save")
    return document


def _relative_parts(content_path: str) -> tuple[str, ...]:
    parts = tuple(content_path.split("/"))
    if any(part in ("", ".", "..") or any(char in part for char in ":\\\x00") for part in parts):
        raise RunArchiveVerificationError(f"archived content path is not a plain relative path: {content_path!r}")
    return parts


def _extract(target: Path, manifest: dict, bodies: dict[str, bytes]) -> str:
    """Write every verified archived file under a new folder, keeping its archived structure."""
    target.mkdir()
    for entry in manifest["attempts"]:
        for relative in entry["directories"]:
            target.joinpath(*_relative_parts(f"attempts/{entry['attempt_id']}/{relative}")).mkdir(
                parents=True, exist_ok=True)
    for content_path in sorted(bodies):
        path = target.joinpath(*_relative_parts(content_path))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "xb") as stream:
            stream.write(bodies[content_path])
    return str(target.resolve())


def read_test_run_archive(root: Path, receipt, *, view: str = "summary", extract_to: Path | None = None,
                          store=None) -> dict:
    """Read a saved Test Run back from the central archive, verifying every byte again.

    Args:
        root: Host root whose run_archive binding locates the archive.
        receipt: The receipt file (or object) save_test_run_archive wrote. Only
            its archive_id and manifest_sha256 are used; its archive_status is
            not trusted.
        view: summary, output or log, as inspect_test_run_record applies to the
            archived record.
        extract_to: Optional new folder; every archived file is written there
            under record/ and attempts/<attempt_id>/ with its saved structure.
        store: Optional trusted store; None opens the configured binding.
    Returns:
        runtime_run_archive_reading_v1: archive_id, verified (True),
        archived_at_utc, run_status (the run's own), archive (run facts, record
        entry, Attempt rows, file index, archive_host facts observed when it was
        saved, binding locator), record_inspection (the archived record through
        inspect_test_run_record: outcome, identity, output and failure, or the
        complete execution_log) and extracted_to.
    Raises:
        ValueError: Unknown view, invalid receipt, or an existing extract_to.
        KeyError: The archive is not in the database.
        RunArchiveVerificationError: The database content differs from its
            manifest, sizes or hashes.
    Effects:
        Reads the database on a new read-only connection. Writes only the new
        extract_to folder. Works after the Attempt folders were cleaned.
    """
    if view not in RUN_ARCHIVE_VIEWS:
        raise ValueError(f"view must be one of {', '.join(RUN_ARCHIVE_VIEWS)}")
    receipt = _read_receipt(receipt)
    if extract_to is not None:
        extract_to = _new_file(extract_to, "extract_to")
    store = _store_for(Path(root).resolve(), store)
    read = store.read_archive(receipt["archive_id"])
    manifest, bodies = _verified(read, receipt["archive_id"], manifest_sha256=receipt["manifest_sha256"])
    record = _record_document(bodies[_RECORD_CONTENT_PATH])
    archive ={name: manifest[name] for name in ("run", "record", "attempts", "files", "archive_host",
                                                 "archive_binding")}
    return {"schema_version": RUN_ARCHIVE_READING_SCHEMA_VERSION, "archive_id": receipt["archive_id"],
            "verified": True, "archived_at_utc": read["archived_at_utc"], "run_status": read["run_status"],
            "archive": archive, "record_inspection": inspect_test_run_record(record, view=view),
            "extracted_to": None if extract_to is None else _extract(extract_to, manifest, bodies)}


def _scene_index(folder: Path) -> tuple[dict[str, tuple[int, str]], list[dict]]:
    files, _, unarchived = _scene(folder)
    index = {}
    for relative, path in files:
        digest, size = hashlib.sha256(), 0
        with open(path, "rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
                size += len(block)
        index[relative] = (size, digest.hexdigest())
    return index, unarchived


def _overlaps(target: Path, project_directory: str | None) -> bool:
    if not project_directory:
        return False
    first, second = _same(target), _same(project_directory)
    return first == second or first.startswith(second.rstrip("\\/") + os.sep) or second.startswith(
        first.rstrip("\\/") + os.sep)


def cleanup_test_run_archive(root: Path, receipt, *, query: bool = False, retention_hold: bool = False,
                             store=None) -> dict:
    """Remove only this run's own Attempt folders after reading its archive back from the database again.

    Args:
        root: The root that ran and saved the run.
        receipt: The receipt save_test_run_archive wrote; its archive_status is
            not trusted, the database is read again.
        query: True reports what cleanup would do and deletes nothing.
        retention_hold: The caller's hold (a downstream owner still uses the
            folders); every Attempt is retained with retention_hold.
        store: Optional trusted store; None opens the configured binding.
    Returns:
        runtime_run_archive_cleanup_v1: archive_id, intent, archive_verified,
        run_status, attempts (attempt_id, workspace_path, outcome deleted,
        cleanable, retained, deletion_failed or absent, retained_reasons,
        failure_message) and complete (every Attempt deleted, cleanable for a
        query, or already absent). An archive that cannot be read back and
        verified retains every Attempt with archive_unavailable and reports
        error.
    Raises:
        ValueError: Invalid receipt or flags, no run_archive binding, or an
            archive saved for another root or attempt_workspace_root.
    Effects:
        Reads the archive on a new connection. Only the Attempt ids in the
        archived manifest are considered, each exactly
        <attempt_workspace_root>/<attempt_id>. An Attempt whose saved scene was
        incomplete, that overlaps the recorded project directory, or whose
        current files differ from the archived index is retained. The rest go to
        maintain_attempt_workspaces with the archived execution facts,
        archive_complete from this readback, the caller's hold, the host's
        current UTC instant and retention_seconds=0; its ownership, marker,
        seal, live-lease and identity-bound removal checks apply unchanged.
        Never deletes archive rows, the record file, the project directory or
        business data.
    """
    if type(query) is not bool or type(retention_hold) is not bool:
        raise ValueError("query and retention_hold must be booleans")
    root = Path(root).resolve()
    receipt = _read_receipt(receipt)
    workspace_root = Path(load_runtime_config(root)["attempt_workspace_root"]).resolve()
    store = _store_for(root, store)
    intent = "query" if query else "cleanup"
    result = {"schema_version": RUN_ARCHIVE_CLEANUP_SCHEMA_VERSION, "archive_id": receipt["archive_id"],
              "intent": intent, "archive_verified": False, "run_status": receipt.get("run_status")}
    try:
        manifest, _ = _verified(store.read_archive(receipt["archive_id"]), receipt["archive_id"],
                                manifest_sha256=receipt["manifest_sha256"])
    except Exception as exc:
        rows = [{"attempt_id": attempt_id, "workspace_path": None, "outcome": "retained",
                 "retained_reasons": ["archive_unavailable"], "failure_message": None}
                for attempt_id in receipt["attempt_ids"]]
        return {**result, "error": _safe_error(exc), "attempts": rows, "complete": False}
    if _same(manifest["root"]) != _same(root) or _same(manifest["attempt_workspace_root"]) != _same(workspace_root):
        raise ValueError(f"archive {receipt['archive_id']} was saved for root {manifest['root']} with Attempt folders "
                         f"in {manifest['attempt_workspace_root']}; clean it with that root")
    outcomes, facts = {}, {}
    for entry in manifest["attempts"]:
        attempt_id = entry["attempt_id"]
        validate_id("attempt_id", attempt_id)
        target = workspace_root / attempt_id
        if not os.path.lexists(target):
            outcomes[attempt_id] = ("absent", [])
            continue
        reasons = []
        if entry["scene"] != "complete":
            reasons.append("scene_not_fully_archived")
        if _overlaps(target, entry.get("project_directory")):
            reasons.append("project_directory_overlap")
        if not reasons and _ordinary_directory(target):
            current, unarchived = _scene_index(target)
            archived = {item["relative_path"]: (item["size_bytes"], item["sha256"])
                        for item in manifest["files"] if item["attempt_id"] == attempt_id}
            if unarchived or current != archived:
                reasons.append("scene_changed_since_archive")
        if reasons:
            outcomes[attempt_id] = ("retained", reasons)
        else:
            facts[attempt_id] = AttemptWorkspaceRetentionFacts(
                execution_ended=entry["execution_ended"] is True,
                process_stop_confirmed=entry["process_stop_confirmed"] is True,
                archive_complete=True, retention_hold=retention_hold)
    failures = {}
    if facts:
        for item in maintain_attempt_workspaces(workspace_root=workspace_root, facts=facts, retention_seconds=0,
                                                observed_at_utc=utc_now_instant(), intent=intent):
            outcomes[item.attempt_id] = (item.outcome, list(item.retained_reasons))
            failures[item.attempt_id] = item.failure_message
    rows = [{"attempt_id": entry["attempt_id"], "workspace_path": str(workspace_root / entry["attempt_id"]),
             "outcome": outcomes[entry["attempt_id"]][0], "retained_reasons": outcomes[entry["attempt_id"]][1],
             "failure_message": failures.get(entry["attempt_id"])} for entry in manifest["attempts"]]
    finished = {"cleanable" if query else "deleted", "absent"}
    return {**result, "archive_verified": True, "run_status": manifest["run"]["status"], "attempts": rows,
            "complete": bool(rows) and all(row["outcome"] in finished for row in rows)}


def build_parser() -> argparse.ArgumentParser:
    """The agent-runtime-archive grammar: save, read and cleanup."""
    parser = argparse.ArgumentParser(
        prog="agent-runtime-archive",
        description=("Save one finished Test Run record and its own Attempt folders to the central Runtime archive "
                     "configured as run_archive in root/.runtime/config.json, read it back from the database, and "
                     "clean only that run's Attempt folders after a fresh database readback."))
    commands = parser.add_subparsers(dest="command", required=True)
    save = commands.add_parser("save", help="Archive a record written by agent-runtime-test-run --record-out.")
    save.add_argument("--root", type=Path, required=True, help="Root that ran the Test Run.")
    save.add_argument("--record", type=Path, required=True, help="The saved Test Run record file.")
    save.add_argument("--receipt-out", type=Path, required=True, help="New receipt file written by code.")
    save.add_argument("--cleanup", action="store_true",
                      help="After a verified save, run cleanup for the same receipt.")
    read = commands.add_parser("read", help="Read and verify a saved archive from the database.")
    read.add_argument("--root", type=Path, required=True)
    read.add_argument("--receipt", type=Path, required=True)
    read.add_argument("--view", choices=RUN_ARCHIVE_VIEWS, default="summary",
                      help="summary, output (adds output and failure_detail) or log (adds the execution_log).")
    read.add_argument("--extract-to", type=Path, help="New folder to restore every archived file into.")
    cleanup = commands.add_parser("cleanup", help="Clean this run's own Attempt folders after a fresh readback.")
    cleanup.add_argument("--root", type=Path, required=True)
    cleanup.add_argument("--receipt", type=Path, required=True)
    cleanup.add_argument("--query", action="store_true", help="Report what would be cleaned; delete nothing.")
    cleanup.add_argument("--hold", action="store_true", help="A downstream owner still needs the folders; keep them.")
    return parser


def main(argv=None) -> int:
    """Run agent-runtime-archive and print its JSON result on stdout.

    save prints the receipt (with --cleanup: {receipt, cleanup}); exit 0 only
    when the archive was verified (and, with --cleanup, every Attempt was
    removed). The receipt's run_status is the Test Run's own outcome; a failed
    run archived successfully still exits 0 here. read prints the verified
    archive; exit 0. cleanup prints the per-Attempt outcomes; exit 0 only when
    complete. Exit 1: archive or cleanup not complete, or an error printed on
    stderr as error_type, error_code, sqlstate and detail (database messages are
    not echoed). Exit 2: invalid arguments.
    """
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "save" and (os.path.lexists(args.receipt_out) or not args.receipt_out.parent.is_dir()):
        parser.error(f"--receipt-out must name a new file in an existing folder: {args.receipt_out}")
    if args.command == "read" and args.extract_to is not None and (
            os.path.lexists(args.extract_to) or not args.extract_to.parent.is_dir()):
        parser.error(f"--extract-to must name a new folder in an existing folder: {args.extract_to}")
    try:
        if args.command == "save":
            receipt = save_test_run_archive(args.root, args.record, receipt_out=args.receipt_out)
            status = 0 if receipt["archive_status"] == "verified" else 1
            document = receipt
            if args.cleanup and status == 0:
                cleaned = cleanup_test_run_archive(args.root, args.receipt_out)
                document = {"receipt": receipt, "cleanup": cleaned}
                status = 0 if cleaned["complete"] else 1
        elif args.command == "read":
            document = read_test_run_archive(args.root, args.receipt, view=args.view, extract_to=args.extract_to)
            status = 0
        else:
            document = cleanup_test_run_archive(args.root, args.receipt, query=args.query, retention_hold=args.hold)
            status = 0 if document["complete"] else 1
    except KeyboardInterrupt:
        print(json.dumps({"error_type": "KeyboardInterrupt", "detail": "interrupted"}), file=sys.stderr)
        return 130
    except Exception as exc:
        print(json.dumps(_safe_error(exc), ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(document, ensure_ascii=False, allow_nan=False))
    return status


__all__ = [
    "PostgresTestRunArchiveStore",
    "RUN_ARCHIVE_FORMAT",
    "RunArchiveCredentialError",
    "RunArchiveVerificationError",
    "cleanup_test_run_archive",
    "open_configured_run_archive",
    "read_test_run_archive",
    "run_archive_ddl",
    "save_test_run_archive",
]


if __name__ == "__main__":
    raise SystemExit(main())
