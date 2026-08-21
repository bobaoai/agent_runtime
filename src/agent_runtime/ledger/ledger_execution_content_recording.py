"""Content staging port used by formal Workflow and Module recorders."""

from __future__ import annotations

from typing import Protocol

from ..contracts.ledger_content_definition import RuntimeExecutionContent


class RuntimeExecutionContentReader(Protocol):
    """Read exact Cell-local bytes behind one admitted content reference."""

    def read_bytes(self, content_ref: str, content_sha256: str) -> bytes:
        """Return exact bytes after verifying the declared hash."""


class RuntimeExecutionContentStore(Protocol):
    """Persist immutable execution content without owning ledger authority."""

    def stage_content(
        self,
        content: RuntimeExecutionContent,
    ) -> RuntimeExecutionContent:
        """Stage bytes before their output reference becomes authoritative."""

    def commit_content(
        self,
        content: RuntimeExecutionContent,
    ) -> RuntimeExecutionContent:
        """Persist bytes whose input or output reference is already committed."""


def record_execution_content(
    *,
    content_store: RuntimeExecutionContentStore,
    content_reader: RuntimeExecutionContentReader,
    workflow_execution_id: str,
    content_ref: str,
    content_sha256: str,
    media_type: str,
    recorded_at_utc: str,
    reference_is_committed: bool,
) -> RuntimeExecutionContent:
    """Copy exact Cell bytes into the Runtime content store idempotently."""

    content = RuntimeExecutionContent(
        workflow_execution_id=workflow_execution_id,
        content_ref=content_ref,
        content_sha256=content_sha256,
        media_type=media_type,
        body=content_reader.read_bytes(content_ref, content_sha256),
        recorded_at_utc=recorded_at_utc,
    )
    content.validate()
    if reference_is_committed:
        return content_store.commit_content(content)
    return content_store.stage_content(content)


__all__ = [
    "RuntimeExecutionContentReader",
    "RuntimeExecutionContentStore",
    "record_execution_content",
]
