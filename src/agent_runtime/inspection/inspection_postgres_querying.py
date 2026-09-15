"""Read-only PostgreSQL composition for live Workflow inspection."""

from __future__ import annotations

from typing import Any, Mapping

from ..contracts.ledger_content_definition import RuntimeExecutionContent
from ..contracts.ledger_record_definition import (
    RuntimeExecutionTrace,
    WorkflowExecutionRecord,
)
from ..contracts.registry_release_definition import WorkflowRelease
from ..ledger.ledger_postgres_persistence import (
    PostgresRuntimeExecutionQueryStore,
    RuntimeExecutionDescriptor,
    RuntimeExecutionPageCursor,
)
from ..registry.registry_postgres_persistence import PostgresRuntimeReleaseQueryStore


class PostgresWorkflowInspectionRepository:
    """Join authorized read-only execution and optional release query ports.

    Construct with execution_queries and optional release_queries supplied by
    the host. This object neither selects credentials nor grants access. Normal
    snapshots use load_trace; read_execution_log adds the same complete log view
    used by in-memory self-tests, with private bodies explicitly requested.
    """

    def __init__(
        self,
        execution_queries: PostgresRuntimeExecutionQueryStore,
        *,
        release_queries: PostgresRuntimeReleaseQueryStore | None = None,
    ) -> None:
        if not isinstance(execution_queries, PostgresRuntimeExecutionQueryStore):
            raise TypeError(
                "execution_queries must be PostgresRuntimeExecutionQueryStore"
            )
        if release_queries is not None and not isinstance(
            release_queries,
            PostgresRuntimeReleaseQueryStore,
        ):
            raise TypeError("release_queries must be PostgresRuntimeReleaseQueryStore")
        self._execution_queries = execution_queries
        self._release_queries = release_queries

    def list_executions(
        self,
        *,
        limit: int = 100,
        before: RuntimeExecutionPageCursor | None = None,
    ) -> tuple[RuntimeExecutionDescriptor, ...]:
        """Return the existing authorized execution page, without content bodies."""
        return self._execution_queries.list_executions(
            limit=limit,
            before=before,
        )

    def get_execution_descriptor(
        self,
        workflow_execution_id: str,
    ) -> RuntimeExecutionDescriptor | None:
        """Return the descriptor for one exact execution, or None if absent."""
        return self._execution_queries.get_execution_descriptor(workflow_execution_id)

    def load_trace(self, workflow_execution_id: str) -> RuntimeExecutionTrace:
        """Read the exact execution's committed content-free Ledger records."""
        return self._execution_queries.load_trace(workflow_execution_id)

    def list_content_metadata(
        self,
        workflow_execution_id: str,
    ) -> tuple[Mapping[str, Any], ...]:
        """Read metadata for content bound to one authorized execution."""
        return self._execution_queries.list_content_metadata(workflow_execution_id)

    def load_content(
        self,
        workflow_execution_id: str,
        content_ref: str,
    ) -> RuntimeExecutionContent | None:
        """Read one private body through the supplied execution-scoped query port."""
        return self._execution_queries.load_content(workflow_execution_id, content_ref)

    def load_workflow_release(
        self,
        trace: RuntimeExecutionTrace,
    ) -> WorkflowRelease | None:
        """Resolve the trace's Workflow through the optional read-only Registry port."""
        if self._release_queries is None:
            return None
        executions = trace.records_of_type(WorkflowExecutionRecord)
        if not executions:
            return None
        execution = executions[0]
        release_ref = execution.workflow_release_ref or execution.execution_release_ref
        return self._release_queries.load_workflow_release(release_ref)

    def read_execution_log(self, workflow_execution_id: str, *, include_private_content: bool = False) -> dict:
        """Read the same Runtime log view used by non-persistent self-tests.

        The repository is already authorized for the requested execution. Private
        bodies are opt-in and checked against both execution identity and hash.
        This method never invokes a Provider or writes any database state.
        """
        from .inspection_execution_logging import read_execution_log
        trace = self.load_trace(workflow_execution_id)
        if trace.workflow_execution_id != workflow_execution_id:
            raise ValueError("Execution log query returned another execution")

        def read(ref, digest):
            item = self.load_content(workflow_execution_id, ref)
            if item is None:
                raise FileNotFoundError("Execution log content is unavailable: " + ref)
            item.validate()
            if (item.workflow_execution_id != workflow_execution_id or item.content_ref != ref
                    or item.content_sha256 != digest):
                raise ValueError("Execution log content crossed its requested identity")
            return item.body

        return read_execution_log(trace, read_content=read, include_private_content=include_private_content)


__all__ = ["PostgresWorkflowInspectionRepository"]
