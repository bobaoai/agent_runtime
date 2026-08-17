from __future__ import annotations

import hashlib
import io
import json
from typing import Any, Mapping

import pytest

from agent_runtime.contracts.ledger_record_definition import RuntimeExecutionTrace
from agent_runtime.inspection import LiveWorkflowInspectorApplication
from agent_runtime.ledger import (
    RuntimeExecutionContent,
    RuntimeExecutionDescriptor,
    RuntimeExecutionPageCursor,
)


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

    def list_executions(
        self,
        *,
        limit: int = 100,
        before: RuntimeExecutionPageCursor | None = None,
    ):
        rows = (EXECUTION, DENIED_EXECUTION)
        if before is not None:
            index = next(
                index
                for index, row in enumerate(rows)
                if row.workflow_execution_id == before.workflow_execution_id
            )
            rows = rows[index + 1 :]
        return rows[:limit]

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
    assert 'src="/assets/live-inspector.js"' in html
    assert "<script>" not in html
    assert "<style>" not in html
    assert "AGENT RUNTIME · READ ONLY" in html
    assert "start execution" not in html.lower()
    assert headers["Cache-Control"] == "no-store"


def test_live_inspector_graph_renders_registered_parallel_groups() -> None:
    status, headers, body = _request(
        _application(_Repository()),
        "/assets/live-inspector.js",
    )
    script = body.decode()

    assert status == "200 OK"
    assert headers["Content-Type"].startswith("text/javascript")
    assert "release.parallel_groups??[]" in script
    assert "parallel_groups:" in script


def test_live_inspector_api_carries_parallel_group_release_projection() -> None:
    group = {
        "group_id": "overview_review_group",
        "control_node_id": "review_parallel",
        "branch_node_ids": ["fidelity_review", "reader_gain_review"],
        "join_node_id": "review_aggregate",
        "completion_outcome_id": "all_completed",
        "join_policy": "all_required",
    }

    class _Release:
        def as_dict(self):
            return {"nodes": [], "edges": [], "parallel_groups": [group]}

    class _ParallelRepository(_Repository):
        def load_workflow_release(self, trace: RuntimeExecutionTrace):
            return _Release()

    status, _, body = _request(
        _application(_ParallelRepository()),
        f"/api/executions/{EXECUTION.workflow_execution_id}",
    )
    payload = json.loads(body)

    assert status == "200 OK"
    assert payload["workflow_release"]["parallel_groups"] == [group]


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
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["Content-Disposition"].startswith("attachment;")
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"].startswith("sandbox;")
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


def test_live_inspector_fails_closed_when_authorizer_errors() -> None:
    class ExplodingAuthorizer:
        def can_read_execution(self, request_context: Any, execution: Any) -> bool:
            raise RuntimeError("authorization service unavailable")

        def can_read_content(
            self,
            request_context: Any,
            execution: Any,
            content: Any,
        ) -> bool:
            raise RuntimeError("authorization service unavailable")

    application = LiveWorkflowInspectorApplication(
        repository=_Repository(),
        authorizer=ExplodingAuthorizer(),
        request_context_resolver=lambda environ: environ.get("reviewer"),
    )

    list_status, _, list_body = _request(application, "/api/executions")
    trace_status, _, _ = _request(
        application,
        f"/api/executions/{EXECUTION.workflow_execution_id}",
    )

    assert list_status == "200 OK"
    assert json.loads(list_body) == {"executions": [], "next_cursor": None}
    assert trace_status == "404 Not Found"


def test_live_inspector_execution_list_uses_opaque_pagination_cursor() -> None:
    application = _application(_Repository())

    first_status, _, first_body = _request(
        application,
        "/api/executions",
        query="limit=1",
    )
    first_page = json.loads(first_body)
    second_status, _, second_body = _request(
        application,
        "/api/executions",
        query=f"limit=1&cursor={first_page['next_cursor']}",
    )
    invalid_status, _, _ = _request(
        application,
        "/api/executions",
        query="cursor=not-a-valid-cursor",
    )

    assert first_status == "200 OK"
    assert first_page["executions"][0]["workflow_execution_id"] == (
        EXECUTION.workflow_execution_id
    )
    assert first_page["next_cursor"] is not None
    assert EXECUTION.workflow_execution_id not in first_page["next_cursor"]
    assert second_status == "200 OK"
    assert json.loads(second_body) == {"executions": [], "next_cursor": None}
    assert invalid_status == "400 Bad Request"


def test_live_inspector_keyset_cursor_ignores_newer_concurrent_insert() -> None:
    older = RuntimeExecutionDescriptor(
        workflow_execution_id="execution_live_older_001",
        workflow_id="agent_workflow_live",
        tenant_id="tenant_allowed",
        cell_id="cell_live",
        principal_id="principal_live",
        execution_release_ref="workflow-release:agent-live@v1",
        recorded_at_utc="2026-08-08T10:00:00Z",
    )
    newer_insert = RuntimeExecutionDescriptor(
        workflow_execution_id="execution_live_new_insert_001",
        workflow_id="agent_workflow_live",
        tenant_id="tenant_allowed",
        cell_id="cell_live",
        principal_id="principal_live",
        execution_release_ref="workflow-release:agent-live@v1",
        recorded_at_utc="2026-08-08T13:00:00Z",
    )

    class ConcurrentRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.rows = [EXECUTION, older]

        def list_executions(self, *, limit=100, before=None):
            rows = sorted(
                self.rows,
                key=lambda row: (row.recorded_at_utc, row.workflow_execution_id),
                reverse=True,
            )
            if before is not None:
                rows = [
                    row
                    for row in rows
                    if (row.recorded_at_utc, row.workflow_execution_id)
                    < (before.recorded_at_utc, before.workflow_execution_id)
                ]
            return tuple(rows[:limit])

    repository = ConcurrentRepository()
    application = _application(repository)
    _, _, first_body = _request(
        application,
        "/api/executions",
        query="limit=1",
    )
    first_page = json.loads(first_body)
    repository.rows.insert(0, newer_insert)
    _, _, second_body = _request(
        application,
        "/api/executions",
        query=f"limit=1&cursor={first_page['next_cursor']}",
    )

    assert json.loads(second_body)["executions"][0][
        "workflow_execution_id"
    ] == older.workflow_execution_id


def test_live_inspector_cursor_advances_past_a_full_unauthorized_scan() -> None:
    allowed_older = RuntimeExecutionDescriptor(
        workflow_execution_id="execution_allowed_after_scan_001",
        workflow_id="agent_workflow_live",
        tenant_id="tenant_allowed",
        cell_id="cell_live",
        principal_id="principal_live",
        execution_release_ref="workflow-release:agent-live@v1",
        recorded_at_utc="2026-08-08T10:00:00Z",
    )
    denied_rows = tuple(
        RuntimeExecutionDescriptor(
            workflow_execution_id=f"execution_denied_scan_{index:04d}",
            workflow_id="agent_workflow_denied",
            tenant_id="tenant_denied",
            cell_id="cell_live",
            principal_id="principal_live",
            execution_release_ref="workflow-release:agent-denied@v1",
            recorded_at_utc="2026-08-08T11:00:00Z",
        )
        for index in range(4999, -1, -1)
    )

    class SparseAuthorizedRepository(_Repository):
        def __init__(self) -> None:
            super().__init__()
            self.rows = denied_rows + (allowed_older,)

        def list_executions(self, *, limit=100, before=None):
            rows = self.rows
            if before is not None:
                index = next(
                    index
                    for index, row in enumerate(rows)
                    if row.workflow_execution_id == before.workflow_execution_id
                )
                rows = rows[index + 1 :]
            return rows[:limit]

    application = _application(SparseAuthorizedRepository())
    _, _, first_body = _request(application, "/api/executions", query="limit=1")
    first_page = json.loads(first_body)
    _, _, second_body = _request(
        application,
        "/api/executions",
        query=f"limit=1&cursor={first_page['next_cursor']}",
    )

    assert first_page["executions"] == []
    assert first_page["next_cursor"] is not None
    assert json.loads(second_body)["executions"][0][
        "workflow_execution_id"
    ] == allowed_older.workflow_execution_id


def test_live_inspector_embedding_requires_explicit_trusted_origin() -> None:
    application = LiveWorkflowInspectorApplication(
        repository=_Repository(),
        authorizer=_Authorizer(),
        request_context_resolver=lambda environ: environ.get("reviewer"),
        frame_ancestors=("https://review.example.com",),
    )

    status, headers, _ = _request(application, "/")

    assert status == "200 OK"
    assert "frame-ancestors https://review.example.com" in (
        headers["Content-Security-Policy"]
    )
    assert "X-Frame-Options" not in headers
    with pytest.raises(ValueError, match="invalid origin"):
        LiveWorkflowInspectorApplication(
            repository=_Repository(),
            authorizer=_Authorizer(),
            request_context_resolver=lambda environ: environ.get("reviewer"),
            frame_ancestors=("https://review.example.com\r\nInjected: true",),
        )


def test_live_inspector_observes_lease_expiry_at_the_request_instant() -> None:
    from agent_runtime.contracts.ledger_record_definition import (
        ExecutionInputRef,
        WorkflowAttemptStartedRecord,
        WorkflowExecutionRecord,
        WorkflowModuleExecutionVariantRecord,
        WorkflowModuleRunRecord,
        sha256_json,
    )

    recorded_at = EXECUTION.recorded_at_utc
    execution_id = EXECUTION.workflow_execution_id
    input_ref = "artifact-ref:live-lease-input"
    input_record = ExecutionInputRef(
        execution_input_id="execution_input_live_001",
        workflow_execution_id=execution_id,
        input_type_id="agent_input",
        schema_version="v1",
        input_ref=input_ref,
        input_sha256="1" * 64,
        byte_size=10,
        media_type="application/json",
        recorded_at_utc=recorded_at,
    )
    module = WorkflowModuleRunRecord(
        workflow_execution_id=execution_id,
        module_run_id="module_run_live_001",
        state_id="state_live",
        module_id="module_live",
        input_refs=(input_ref,),
        input_closure_sha256=sha256_json([input_ref]),
        recorded_at_utc=recorded_at,
    )
    variant = WorkflowModuleExecutionVariantRecord(
        workflow_execution_id=execution_id,
        module_run_id=module.module_run_id,
        variant_id="variant_live_001",
        module_id=module.module_id,
        agent_execution_adapter_id="adapter_live",
        execution_profile_id="profile_live",
        model_id="model_live",
        reasoning_profile="effort_live",
        prompt_sha256="2" * 64,
        static_module_sha256="3" * 64,
        input_closure_sha256=module.input_closure_sha256,
        entitlement_snapshot_hash="e" * 64,
        agent_execution_adapter_revision="adapter_revision_v1",
        runtime_version="runtime_v1",
        tool_policy=("no_tools",),
        context_mode="stateless",
        output_schema_sha256="f" * 64,
        timeout_seconds=300,
        max_attempts=1,
        execution_profile_sha256="5" * 64,
        recorded_at_utc=recorded_at,
    )
    records = (
        WorkflowExecutionRecord(
            workflow_execution_id=execution_id,
            workflow_id=EXECUTION.workflow_id,
            workflow_contract_version="v1",
            tenant_id=EXECUTION.tenant_id,
            cell_id=EXECUTION.cell_id,
            principal_id=EXECUTION.principal_id,
            execution_release_ref=EXECUTION.execution_release_ref,
            graph_sha256="a" * 64,
            runtime_execution_binding_ref="runtime-binding:live@v1",
            runtime_execution_binding_sha256="b" * 64,
            authorization_decision_ref="authorization-decision:live@v1",
            authorization_decision_sha256="c" * 64,
            execution_principal_delegation_ref="delegation-ref:live@v1",
            execution_principal_delegation_sha256="d" * 64,
            entitlement_snapshot_ref="entitlement-ref:live@v1",
            entitlement_snapshot_hash="e" * 64,
            execution_input_package_refs=(input_ref,),
            execution_input_package_sha256="f" * 64,
            recorded_at_utc=recorded_at,
        ),
        input_record,
        module,
        variant,
        WorkflowAttemptStartedRecord(
            workflow_execution_id=execution_id,
            dispatch_id="dispatch_live_001",
            module_run_id=module.module_run_id,
            variant_id=variant.variant_id,
            attempt_id="attempt_live_001",
            parent_attempt_id=None,
            attempt_ordinal=1,
            trace_id="trace_live_001",
            request_sha256="8" * 64,
            claim_token_hash="9" * 64,
            input_closure_sha256=module.input_closure_sha256,
            execution_profile_sha256=variant.execution_profile_sha256,
            entitlement_snapshot_hash=variant.entitlement_snapshot_hash,
            timeout_seconds=variant.timeout_seconds,
            recorded_at_utc=recorded_at,
        ),
    )

    class _ExpiredLeaseRepository(_Repository):
        def load_trace(self, workflow_execution_id: str):
            return RuntimeExecutionTrace(
                workflow_execution_id=workflow_execution_id,
                records=records,
                commit_receipts=(),
            )

    application = LiveWorkflowInspectorApplication(
        repository=_ExpiredLeaseRepository(),
        authorizer=_Authorizer(),
        request_context_resolver=lambda environ: environ.get("reviewer"),
        # Fixed observation instant past the 300s lease deadline.
        observation_clock=lambda: "2026-08-08T12:10:00Z",
    )
    status, _, body = _request(
        application,
        f"/api/executions/{EXECUTION.workflow_execution_id}",
    )

    assert status == "200 OK"
    payload = json.loads(body)
    inspection = payload["inspection"]
    assert inspection["projection_boundary"]["observed_at_utc"] == (
        "2026-08-08T12:10:00Z"
    )
    workflow = inspection["trace"]["workflow"]
    assert workflow["recovery_required"] is True
    assert workflow["modules_requiring_recovery"] == ["module_run_live_001"]
    assert inspection["modules"][0]["attempt_starts"][0]["lease_state"] == (
        "expired"
    )


def test_default_observation_clock_emits_canonical_utc() -> None:
    from agent_runtime.foundation.foundation_contract_validation import (
        parse_utc_timestamp,
    )
    from agent_runtime.inspection.inspection_http_serving import (
        observed_now_utc,
    )

    instant = observed_now_utc()
    parsed = parse_utc_timestamp("observed_at_utc", instant)
    assert parsed.utcoffset().total_seconds() == 0
