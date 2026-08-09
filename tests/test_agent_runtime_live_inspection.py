from __future__ import annotations

import hashlib
import io
import json
from typing import Any, Mapping

from agent_runtime.contracts.ledger_record_definition import RuntimeExecutionTrace
from agent_runtime.inspection import LiveWorkflowInspectorApplication
from agent_runtime.ledger import RuntimeExecutionContent, RuntimeExecutionDescriptor


EXECUTION = RuntimeExecutionDescriptor(
    workflow_execution_id="execution_live_001",
    workflow_id="agent_workflow_live",
    tenant_id="tenant_allowed",
    cell_id="cell_live",
    principal_id="principal_live",
    execution_release_ref="workflow-release:agent-live@v1",
    recorded_at_utc="2026-08-08T12:00:00Z",
)
DENIED_EXECUTION = RuntimeExecutionDescriptor(
    workflow_execution_id="execution_denied_001",
    workflow_id="agent_workflow_denied",
    tenant_id="tenant_denied",
    cell_id="cell_live",
    principal_id="principal_live",
    execution_release_ref="workflow-release:agent-denied@v1",
    recorded_at_utc="2026-08-08T11:00:00Z",
)
BODY = b"authorized prompt body"
CONTENT = RuntimeExecutionContent(
    workflow_execution_id=EXECUTION.workflow_execution_id,
    content_ref="artifact-ref:live-prompt-001",
    content_sha256=hashlib.sha256(BODY).hexdigest(),
    media_type="text/plain",
    body=BODY,
    recorded_at_utc=EXECUTION.recorded_at_utc,
)


class _Repository:
    def __init__(self) -> None:
        self.trace_reads = 0

    def list_executions(self, *, limit: int = 100):
        return (EXECUTION, DENIED_EXECUTION)[:limit]

    def get_execution_descriptor(self, workflow_execution_id: str):
        return next(
            (
                row
                for row in (EXECUTION, DENIED_EXECUTION)
                if row.workflow_execution_id == workflow_execution_id
            ),
            None,
        )

    def load_trace(self, workflow_execution_id: str):
        self.trace_reads += 1
        return RuntimeExecutionTrace(
            workflow_execution_id=workflow_execution_id,
            records=(),
            commit_receipts=(),
        )

    def list_content_metadata(self, workflow_execution_id: str):
        if workflow_execution_id != EXECUTION.workflow_execution_id:
            return ()
        return (CONTENT.metadata_dict(),)

    def load_content(self, workflow_execution_id: str, content_ref: str):
        if (
            workflow_execution_id == EXECUTION.workflow_execution_id
            and content_ref == CONTENT.content_ref
        ):
            return CONTENT
        return None

    def load_workflow_release(self, trace: RuntimeExecutionTrace):
        return None


class _Authorizer:
    def can_read_execution(self, request_context: Any, execution: RuntimeExecutionDescriptor):
        return (
            request_context == "authenticated-reviewer"
            and execution.tenant_id == "tenant_allowed"
        )

    def can_read_content(
        self,
        request_context: Any,
        execution: RuntimeExecutionDescriptor,
        content: Mapping[str, Any],
    ):
        return (
            request_context == "authenticated-reviewer"
            and execution == EXECUTION
            and content["content_ref"] == CONTENT.content_ref
        )


def _application(repository: _Repository) -> LiveWorkflowInspectorApplication:
    return LiveWorkflowInspectorApplication(
        repository=repository,
        authorizer=_Authorizer(),
        request_context_resolver=lambda environ: environ.get("reviewer"),
    )


def _request(
    application: LiveWorkflowInspectorApplication,
    path: str,
    *,
    method: str = "GET",
    query: str = "",
    authenticated: bool = True,
) -> tuple[str, dict[str, str], bytes]:
    result: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        result["status"] = status
        result["headers"] = dict(headers)

    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "wsgi.input": io.BytesIO(),
    }
    if authenticated:
        environ["reviewer"] = "authenticated-reviewer"
    body = b"".join(application(environ, start_response))
    return result["status"], result["headers"], body


def test_live_inspector_shell_contains_no_execution_data_or_write_controls() -> None:
    status, headers, body = _request(_application(_Repository()), "/")
    html = body.decode()

    assert status == "200 OK"
    assert EXECUTION.workflow_execution_id not in html
    assert "fetch(" in html
    assert "AGENT RUNTIME · READ ONLY" in html
    assert "start execution" not in html.lower()
    assert headers["Cache-Control"] == "no-store"


def test_live_inspector_filters_list_and_denies_trace_before_loading_records() -> None:
    repository = _Repository()
    application = _application(repository)

    status, _, body = _request(application, "/api/executions")
    payload = json.loads(body)
    denied_status, _, _ = _request(
        application,
        f"/api/executions/{DENIED_EXECUTION.workflow_execution_id}",
    )

    assert status == "200 OK"
    assert [row["workflow_execution_id"] for row in payload["executions"]] == [
        EXECUTION.workflow_execution_id
    ]
    assert denied_status == "404 Not Found"
    assert repository.trace_reads == 0


def test_live_inspector_requires_authentication_and_exact_content_authorization() -> None:
    application = _application(_Repository())

    unauthorized, _, _ = _request(
        application,
        "/api/executions",
        authenticated=False,
    )
    status, headers, body = _request(
        application,
        f"/api/executions/{EXECUTION.workflow_execution_id}",
        query=f"content_ref={CONTENT.content_ref}",
    )
    missing, _, _ = _request(
        application,
        f"/api/executions/{EXECUTION.workflow_execution_id}",
        query="content_ref=artifact-ref:other",
    )

    assert unauthorized == "401 Unauthorized"
    assert status == "200 OK"
    assert body == BODY
    assert headers["X-Content-SHA256"] == CONTENT.content_sha256
    assert missing == "404 Not Found"


def test_live_inspector_has_no_write_route_and_head_returns_no_body() -> None:
    application = _application(_Repository())

    write_status, write_headers, _ = _request(
        application,
        "/api/executions",
        method="POST",
    )
    head_status, _, head_body = _request(
        application,
        "/api/executions",
        method="HEAD",
    )

    assert write_status == "405 Method Not Allowed"
    assert write_headers["Allow"] == "GET, HEAD"
    assert head_status == "200 OK"
    assert head_body == b""
