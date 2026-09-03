"""Build the canonical Engineering Reviewer input from one exact Git commit."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
from typing import Mapping, Sequence

from jsonschema import Draft202012Validator, ValidationError


ENGINEERING_REVIEW_SUBJECT_INVALID = "ENGINEERING_REVIEW_SUBJECT_INVALID"
ENGINEERING_REVIEW_INPUT_CLOSURE_INCOMPLETE = (
    "ENGINEERING_REVIEW_INPUT_CLOSURE_INCOMPLETE"
)
INPUT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[5]
    / "09_soul/governance/skills/engineering-change-review/runtime_modules"
    / "engineering_change_reviewer/schemas/input.schema.json"
)


class EngineeringReviewInputError(ValueError):
    """One deterministic Engineering Review input failure."""

    def __init__(self, error_code: str, detail: str) -> None:
        self.error_code = error_code
        super().__init__(f"{error_code}: {detail}")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run_git(root: Path, *args: str) -> bytes:
    try:
        result = subprocess.run(
            ("git", *args),
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = (
            exc.stderr.decode("utf-8", errors="replace").strip()
            if isinstance(exc, subprocess.CalledProcessError)
            else str(exc)
        )
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_SUBJECT_INVALID,
            detail or f"git {' '.join(args)} failed",
        ) from exc
    return result.stdout


def _full_commit_oid(root: Path, commit_ref: str) -> str:
    if not isinstance(commit_ref, str) or not commit_ref.strip():
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_SUBJECT_INVALID,
            "commit_ref must be non-empty",
        )
    oid = _run_git(root, "rev-parse", "--verify", f"{commit_ref}^{{commit}}").decode(
        "ascii"
    ).strip()
    if len(oid) not in {40, 64} or any(char not in "0123456789abcdef" for char in oid):
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_SUBJECT_INVALID,
            "resolved commit object ID is invalid",
        )
    return oid


def _single_parent(root: Path, commit_oid: str) -> str:
    row = _run_git(root, "rev-list", "--parents", "-n", "1", commit_oid).decode(
        "ascii"
    ).strip().split()
    if len(row) != 2:
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_SUBJECT_INVALID,
            "Engineering Review requires one commit with exactly one parent",
        )
    return row[1]


def _changed_path_rows(root: Path, parent_oid: str, commit_oid: str) -> list[dict[str, str]]:
    raw = _run_git(
        root,
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        parent_oid,
        commit_oid,
        "--",
    )
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_SUBJECT_INVALID,
            "Git name-status output is incomplete",
        )
    state_by_status = {b"A": "added", b"M": "modified", b"D": "deleted"}
    rows: list[dict[str, str]] = []
    for index in range(0, len(fields), 2):
        status = fields[index]
        path_bytes = fields[index + 1]
        state = state_by_status.get(status)
        if state is None:
            raise EngineeringReviewInputError(
                ENGINEERING_REVIEW_SUBJECT_INVALID,
                f"unsupported Git path state: {status!r}",
            )
        try:
            path = path_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise EngineeringReviewInputError(
                ENGINEERING_REVIEW_SUBJECT_INVALID,
                "changed path is not UTF-8",
            ) from exc
        source_oid = parent_oid if state == "deleted" else commit_oid
        content = _run_git(root, "show", f"{source_oid}:{path}")
        rows.append(
            {
                "path": path,
                "state": state,
                "content_sha256": _sha256_bytes(content),
            }
        )
    if not rows:
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_SUBJECT_INVALID,
            "exact commit has no changed path relative to its parent",
        )
    return sorted(rows, key=lambda row: row["path"].encode("utf-8"))


def _require_hashed_body(value: Mapping[str, object], name: str) -> dict[str, str]:
    if set(value) != {"ref", "sha256", "body"}:
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_INPUT_CLOSURE_INCOMPLETE,
            f"{name} must contain ref, sha256 and body",
        )
    ref, declared_sha256, body = value["ref"], value["sha256"], value["body"]
    if not all(isinstance(item, str) and item for item in (ref, declared_sha256, body)):
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_INPUT_CLOSURE_INCOMPLETE,
            f"{name} fields must be non-empty strings",
        )
    actual_sha256 = _sha256_bytes(body.encode("utf-8"))
    if declared_sha256 != actual_sha256:
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_INPUT_CLOSURE_INCOMPLETE,
            f"{name} body hash mismatch",
        )
    return {"ref": ref, "sha256": declared_sha256, "body": body}


def build_engineering_review_input(
    *,
    repository_root: Path,
    commit_ref: str,
    system_change_plan_step: Mapping[str, object],
    code_design_basis: Mapping[str, object],
    sandbox_command_plan: Mapping[str, object],
    acceptance_criteria: Sequence[str],
    prior_findings: Sequence[Mapping[str, object]] = (),
    schema_path: Path = INPUT_SCHEMA_PATH,
) -> dict[str, object]:
    """Return one schema-valid successor Reviewer input object."""

    root = repository_root.resolve()
    commit_oid = _full_commit_oid(root, commit_ref)
    parent_oid = _single_parent(root, commit_oid)
    paths = _changed_path_rows(root, parent_oid, commit_oid)
    diff = _run_git(
        root,
        "diff",
        "--binary",
        "--no-ext-diff",
        "--no-renames",
        parent_oid,
        commit_oid,
        "--",
    )
    diff_sha256 = _sha256_bytes(diff)
    subject_identity = json.dumps(
        {
            "commit_ref": commit_oid,
            "parent_ref": parent_oid,
            "diff_sha256": diff_sha256,
            "paths": paths,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    payload: dict[str, object] = {
        "schema_version": "engineering_change_reviewer_input_v4",
        "module_id": "engineering_change_reviewer",
        "system_change_plan_step": _require_hashed_body(
            system_change_plan_step, "system_change_plan_step"
        ),
        "subject": {
            "commit_ref": commit_oid,
            "parent_ref": parent_oid,
            "subject_sha256": _sha256_bytes(subject_identity),
            "diff_sha256": diff_sha256,
            "paths": paths,
        },
        "code_design_basis": _require_hashed_body(
            code_design_basis, "code_design_basis"
        ),
        "sandbox_command_plan": dict(sandbox_command_plan),
        "acceptance_criteria": list(acceptance_criteria),
        "prior_findings": [dict(item) for item in prior_findings],
    }
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema).validate(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
        raise EngineeringReviewInputError(
            ENGINEERING_REVIEW_INPUT_CLOSURE_INCOMPLETE,
            str(exc),
        ) from exc
    return payload


__all__ = [
    "ENGINEERING_REVIEW_INPUT_CLOSURE_INCOMPLETE",
    "ENGINEERING_REVIEW_SUBJECT_INVALID",
    "EngineeringReviewInputError",
    "INPUT_SCHEMA_PATH",
    "build_engineering_review_input",
]
