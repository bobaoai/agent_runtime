"""Immutable execution-content contract shared by ledger adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import re
from typing import Any


_MEDIA_TYPE_PATTERN = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$"
)


@dataclass(frozen=True)
class RuntimeExecutionContent:
    """One immutable content body referenced by an execution fact."""

    workflow_execution_id: str
    content_ref: str
    content_sha256: str
    media_type: str
    body: bytes
    recorded_at_utc: str

    def validate(self) -> None:
        """Validate content identity, bytes, media type, and record time."""

        if (
            type(self.workflow_execution_id) is not str
            or not self.workflow_execution_id
        ):
            raise ValueError("workflow_execution_id is required")
        if type(self.content_ref) is not str or not self.content_ref:
            raise ValueError("content_ref is required")
        if type(self.content_sha256) is not str or not re.fullmatch(
            r"[0-9a-f]{64}", self.content_sha256
        ):
            raise ValueError("content_sha256 must be lowercase SHA-256")
        if type(self.media_type) is not str or not _MEDIA_TYPE_PATTERN.fullmatch(
            self.media_type
        ):
            raise ValueError("media_type must be a normalized MIME type")
        if type(self.body) is not bytes:
            raise ValueError("body must be bytes")
        if hashlib.sha256(self.body).hexdigest() != self.content_sha256:
            raise ValueError("content body hash mismatch")
        try:
            timestamp = datetime.fromisoformat(
                self.recorded_at_utc.replace("Z", "+00:00")
            )
        except (AttributeError, ValueError) as exc:
            raise ValueError(
                "recorded_at_utc must be an ISO-8601 timestamp"
            ) from exc
        if timestamp.tzinfo is None:
            raise ValueError("recorded_at_utc must include a timezone")

    def metadata_dict(self) -> dict[str, Any]:
        """Return the content-free inspection projection."""

        self.validate()
        return {
            "workflow_execution_id": self.workflow_execution_id,
            "content_ref": self.content_ref,
            "content_sha256": self.content_sha256,
            "media_type": self.media_type,
            "byte_size": len(self.body),
            "recorded_at_utc": self.recorded_at_utc,
        }


__all__ = ["RuntimeExecutionContent"]
