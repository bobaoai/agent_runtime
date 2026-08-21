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
    """Join least-authority execution and release readers for the Inspector."""

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
        return self._execution_queries.list_executions(
            limit=limit,
            before=before,
        )

    def get_execution_descriptor(
        self,
        workflow_execution_id: str,
    ) -> RuntimeExecutionDescriptor | None:
        return self._execution_queries.get_execution_descriptor(workflow_execution_id)

    def load_trace(self, workflow_execution_id: str) -> RuntimeExecutionTrace:
        return self._execution_queries.load_trace(workflow_execution_id)

    def list_content_metadata(
        self,
        workflow_execution_id: str,
    ) -> tuple[Mapping[str, Any], ...]:
        return self._execution_queries.list_content_metadata(workflow_execution_id)

    def load_content(
        self,
        workflow_execution_id: str,
        content_ref: str,
    ) -> RuntimeExecutionContent | None:
        return self._execution_queries.load_content(workflow_execution_id, content_ref)

    def load_workflow_release(
        self,
        trace: RuntimeExecutionTrace,
    ) -> WorkflowRelease | None:
        if self._release_queries is None:
            return None
        executions = trace.records_of_type(WorkflowExecutionRecord)
        if not executions:
            return None
        execution = executions[0]
        release_ref = execution.workflow_release_ref or execution.execution_release_ref
        return self._release_queries.load_workflow_release(release_ref)


__all__ = ["PostgresWorkflowInspectionRepository"]
