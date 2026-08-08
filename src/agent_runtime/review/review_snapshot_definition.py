"""Build the portable read model consumed by Workflow review clients.

The builder accepts an already-authorized inspection projection. It performs
no database read and makes no authorization decision. Runtime services remain
responsible for producing the inspection from registered releases and
committed execution records before this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Mapping, Sequence


WORKFLOW_REVIEW_BUNDLE_SCHEMA_VERSION = "workflow_review_bundle_v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REDACTION_STATES = frozenset(
    {"content_read_granted", "metadata_only", "redacted", "unavailable"}
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _json_copy(value: Any) -> Any:
    return json.loads(_canonical_json(value))


@dataclass(frozen=True)
class ReviewContent:
    """One authorized body or explicit redaction in a review bundle."""

    content_ref: str
    content_sha256: str
    media_type: str
    redaction_state: str
    body: Any | None = None
    redaction_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the exact JSON member after validating disclosure state."""

        if type(self.content_ref) is not str or ":" not in self.content_ref:
            raise ValueError("Review content requires an opaque content_ref")
        if not _SHA256.fullmatch(self.content_sha256):
            raise ValueError("Review content requires a lowercase SHA-256")
        if type(self.media_type) is not str or "/" not in self.media_type:
            raise ValueError("Review content requires a media_type")
        if self.redaction_state not in _REDACTION_STATES:
            raise ValueError("invalid review-content redaction_state")
        if self.redaction_state == "content_read_granted":
            if self.body is None:
                raise ValueError("granted review content requires a body")
            if self.redaction_reason is not None:
                raise ValueError("granted review content cannot carry a redaction reason")
        elif self.body is not None:
            raise ValueError("non-granted review content cannot include a body")
        elif not self.redaction_reason:
            raise ValueError("non-granted review content requires a redaction reason")
        return {
            "content_ref": self.content_ref,
            "content_sha256": self.content_sha256,
            "media_type": self.media_type,
            "redaction_state": self.redaction_state,
            "body": _json_copy(self.body) if self.body is not None else None,
            "redaction_reason": self.redaction_reason,
        }


def _inspection_identity(inspection: Mapping[str, Any]) -> tuple[str, str]:
    trace = inspection.get("trace")
    if not isinstance(trace, Mapping):
        raise ValueError("Workflow review inspection requires trace")
    workflow = trace.get("workflow")
    if not isinstance(workflow, Mapping):
        raise ValueError("Workflow review inspection requires trace.workflow")
    workflow_id = workflow.get("workflow_id")
    workflow_execution_id = workflow.get("workflow_execution_id")
    if type(workflow_id) is not str or not workflow_id:
        raise ValueError("Workflow review inspection requires workflow_id")
    if type(workflow_execution_id) is not str or not workflow_execution_id:
        raise ValueError("Workflow review inspection requires workflow_execution_id")
    modules = inspection.get("modules")
    if type(modules) is not list:
        raise ValueError("Workflow review inspection requires a modules list")
    module_run_ids: list[str] = []
    variant_ids: list[str] = []
    attempt_ids: list[str] = []
    for module in modules:
        if not isinstance(module, Mapping):
            raise ValueError("Workflow review module entries must be objects")
        module_run = module.get("module_run")
        if not isinstance(module_run, Mapping):
            raise ValueError("Workflow review module requires module_run")
        module_run_ids.append(str(module_run.get("module_run_id", "")))
        for variant in module.get("variants", []):
            variant_ids.append(str(variant.get("variant_id", "")))
        for attempt in module.get("attempts", []):
            attempt_ids.append(str(attempt.get("attempt_id", "")))
    for label, values in (
        ("module_run_id", module_run_ids),
        ("variant_id", variant_ids),
        ("attempt_id", attempt_ids),
    ):
        if any(not value for value in values) or len(values) != len(set(values)):
            raise ValueError(f"Workflow review inspection has invalid {label} values")
    return workflow_id, workflow_execution_id


def build_workflow_review_bundle(
    inspection: Mapping[str, Any],
    *,
    contents: Sequence[ReviewContent] = (),
) -> dict[str, Any]:
    """Build one content-addressed, offline-readable Workflow review bundle."""

    if not isinstance(inspection, Mapping):
        raise ValueError("inspection must be a mapping")
    workflow_id, workflow_execution_id = _inspection_identity(inspection)
    content_rows = [content.as_dict() for content in contents]
    content_refs = [row["content_ref"] for row in content_rows]
    if len(content_refs) != len(set(content_refs)):
        raise ValueError("Workflow review content refs must be unique")
    payload = {
        "schema_version": WORKFLOW_REVIEW_BUNDLE_SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "workflow_execution_id": workflow_execution_id,
        "inspection": _json_copy(inspection),
        "contents": content_rows,
    }
    bundle_sha256 = _sha256_json(payload)
    bundle = {
        **payload,
        "bundle_id": f"workflow-review-bundle:{workflow_execution_id}:{bundle_sha256[:16]}",
        "bundle_sha256": bundle_sha256,
    }
    validate_workflow_review_bundle(bundle)
    return bundle


def validate_workflow_review_bundle(bundle: Mapping[str, Any]) -> None:
    """Validate top-level identity, inspection closure, content, and hash."""

    if not isinstance(bundle, Mapping):
        raise ValueError("Workflow review bundle must be a mapping")
    expected_keys = {
        "schema_version",
        "workflow_id",
        "workflow_execution_id",
        "inspection",
        "contents",
        "bundle_id",
        "bundle_sha256",
    }
    if set(bundle) != expected_keys:
        raise ValueError("Workflow review bundle has an invalid top-level shape")
    if bundle["schema_version"] != WORKFLOW_REVIEW_BUNDLE_SCHEMA_VERSION:
        raise ValueError("unsupported Workflow review bundle schema")
    workflow_id, workflow_execution_id = _inspection_identity(bundle["inspection"])
    if bundle["workflow_id"] != workflow_id:
        raise ValueError("Workflow review bundle workflow_id mismatch")
    if bundle["workflow_execution_id"] != workflow_execution_id:
        raise ValueError("Workflow review bundle execution identity mismatch")
    rows = bundle["contents"]
    if type(rows) is not list:
        raise ValueError("Workflow review bundle contents must be a list")
    normalized_rows = [ReviewContent(**row).as_dict() for row in rows]
    refs = [row["content_ref"] for row in normalized_rows]
    if len(refs) != len(set(refs)):
        raise ValueError("Workflow review bundle content refs must be unique")
    payload = {
        "schema_version": bundle["schema_version"],
        "workflow_id": bundle["workflow_id"],
        "workflow_execution_id": bundle["workflow_execution_id"],
        "inspection": bundle["inspection"],
        "contents": normalized_rows,
    }
    expected_sha256 = _sha256_json(payload)
    if bundle["bundle_sha256"] != expected_sha256:
        raise ValueError("Workflow review bundle hash mismatch")
    expected_id = f"workflow-review-bundle:{workflow_execution_id}:{expected_sha256[:16]}"
    if bundle["bundle_id"] != expected_id:
        raise ValueError("Workflow review bundle identity mismatch")


__all__ = [
    "WORKFLOW_REVIEW_BUNDLE_SCHEMA_VERSION",
    "ReviewContent",
    "build_workflow_review_bundle",
    "validate_workflow_review_bundle",
]
