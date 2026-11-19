"""Read-only inspection of a saved local Test Run record.

A local Test Run returns its facts and archives a copy in each Attempt folder;
it persists nothing else (persistence is not_requested). This reader starts
from the record code saved (agent-runtime-test-run --record-out or
write_test_run_record) and shows its outcome, executed identity, selection,
output or failure, execution log and Attempt folders, so no execution ID,
content ref or internal path has to be retyped. It never registers, reruns a
Provider, queries PostgreSQL or turns a local record into durable history.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat


TEST_RUN_INSPECTION_SCHEMA_VERSION = "runtime_test_run_inspection_v1"
TEST_RUN_INSPECTION_VIEWS = ("summary", "output", "log", "files")
_IDENTITY = ("workflow_release_ref", "workflow_release_sha256", "module_release_ref", "module_release_sha256",
             "execution_profile_ref", "execution_profile_sha256", "workflow_execution_id", "module_run_id",
             "attempt_id", "model", "effort", "runtime_version")


def _load(record) -> tuple[dict, str | None]:
    if isinstance(record, dict):
        return record, None
    if not isinstance(record, (str, os.PathLike)):
        raise ValueError("record must be a Test Run record object or the path of a saved record")
    path = Path(record)
    data = path.read_bytes()
    try:
        if data.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = data.decode("utf-16")
        else:
            text = data.decode("utf-8-sig")
        document = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"saved Test Run record is not readable JSON: {path}") from exc
    if type(document) is not dict:
        raise ValueError(f"saved Test Run record is not a JSON object: {path}")
    return document, str(path.resolve())


def _attempts(record: dict) -> list[dict]:
    rows = record.get("attempt_workspaces") or []
    if type(rows) is not list or any(type(row) is not dict for row in rows):
        raise ValueError("record attempt_workspaces is not a list of Attempt rows")
    return [{"attempt_id": row.get("attempt_id"), "workspace_path": row.get("workspace_path"),
             "workspace_exists": isinstance(row.get("workspace_path"), str) and Path(row["workspace_path"]).is_dir(),
             "archive_path": row.get("archive_path"),
             "archive_exists": isinstance(row.get("archive_path"), str) and Path(row["archive_path"]).is_file(),
             "sealed": row.get("sealed"), "seal_skipped_reason": row.get("seal_skipped_reason"),
             "project_directory": row.get("project_directory")} for row in rows]


def _files(folder: Path) -> list[dict]:
    """Ordinary files below folder with size and SHA-256; links are listed, never followed."""
    rows = []

    def visit(current: Path, prefix: str) -> None:
        with os.scandir(current) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                relative = prefix + entry.name
                info = entry.stat(follow_symlinks=False)
                if entry.is_symlink() or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                    rows.append({"path": relative, "kind": "link"})
                elif stat.S_ISDIR(info.st_mode):
                    visit(Path(entry.path), relative + "/")
                elif stat.S_ISREG(info.st_mode):
                    digest = hashlib.sha256()
                    with open(entry.path, "rb") as stream:
                        for block in iter(lambda: stream.read(1 << 20), b""):
                            digest.update(block)
                    rows.append({"path": relative, "kind": "file", "size": info.st_size, "sha256": digest.hexdigest()})
                else:
                    rows.append({"path": relative, "kind": "special"})

    visit(folder, "")
    return rows


def inspect_test_run_record(record, *, view: str = "summary") -> dict:
    """Show a saved local Test Run record without retyping anything from it.

    Args:
        record: The record returned by run_local_workflow_test, or the path of
            the file agent-runtime-test-run --record-out (or write_test_run_record)
            saved; captured stdout in UTF-8 with or without BOM, or UTF-16, is
            also read.
        view: summary (outcome, identity, selection, Attempt folders), output
            (adds output and failure_detail), log (adds the execution_log
            captured before temporary content closed) or files (adds the
            ordinary files of each existing Attempt folder with size and
            SHA-256; links are listed, not followed).
    Returns:
        A runtime_test_run_inspection_v1 object: view, record_path, outcome
        (the record's status, or no_record for the error document of a run
        that returned no record, which then carries error), persistence,
        durable_history (not_available: a local record is evidence, not a
        recoverable execution), identity, definition_selection and attempts
        (each with workspace_exists and archive_exists as found on disk).
    Raises:
        FileNotFoundError: The saved record file is missing.
        ValueError: Unknown view, unreadable JSON, or an object that is neither
            a local Test Run record nor its error document.
    Effects:
        Reads the record and, for files, the Attempt folders. Writes nothing;
        registers, runs, retries and connects to nothing.
    """
    if view not in TEST_RUN_INSPECTION_VIEWS:
        raise ValueError(f"view must be one of {', '.join(TEST_RUN_INSPECTION_VIEWS)}")
    document, path = _load(record)
    base = {"schema_version": TEST_RUN_INSPECTION_SCHEMA_VERSION, "view": view, "record_path": path,
            "durable_history": "not_available"}
    if "status" not in document:
        if "error_type" not in document:
            raise ValueError("not a Test Run record: it has neither status nor error_type")
        return {**base, "outcome": "no_record", "persistence": "not_requested", "identity": None,
                "definition_selection": None, "attempts": [],
                "error": {key: document.get(key) for key in ("error_type", "error_code", "detail")}}
    persistence = document.get("persistence")
    if persistence != "not_requested":
        raise ValueError(f"this reader covers local Test Run records (persistence not_requested), not {persistence!r}; "
                         "read persistent executions through their store's inspection repository")
    attempts = _attempts(document)
    result = {**base, "outcome": document.get("status"), "persistence": persistence,
              "identity": {name: document.get(name) for name in _IDENTITY},
              "definition_selection": document.get("definition_selection"), "attempts": attempts}
    if view == "output":
        result["output"] = document.get("output")
        result["failure_detail"] = document.get("failure_detail")
    elif view == "log":
        result["execution_log"] = document.get("execution_log")
    elif view == "files":
        for row in attempts:
            row["files"] = _files(Path(row["workspace_path"])) if row["workspace_exists"] else None
    return result


__all__ = ["TEST_RUN_INSPECTION_SCHEMA_VERSION", "TEST_RUN_INSPECTION_VIEWS", "inspect_test_run_record"]
