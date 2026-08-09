"""Authorized, read-only HTTP application for live Runtime inspection."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import importlib
import json
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from ..contracts.ledger_record_definition import (
    CommitReceipt,
    LegacyAuthorizationLedgerRecord,
    RuntimeExecutionTrace,
    RuntimeLedgerRecord,
    WorkflowExecutionRecord,
    legacy_authorization_record_as_dict,
    runtime_record_as_dict,
)
from ..contracts.registry_release_definition import WorkflowRelease
from ..ledger.ledger_postgres_persistence import (
    PostgresRuntimeExecutionRecordStore,
    RuntimeExecutionContent,
    RuntimeExecutionDescriptor,
)
from ..registry.registry_postgres_persistence import PostgresRuntimeReleaseStore


class WorkflowInspectionRepository(Protocol):
    """Read-only facts required by the live Inspector."""

    def list_executions(self, *, limit: int = 100) -> tuple[RuntimeExecutionDescriptor, ...]: ...

    def get_execution_descriptor(
        self, workflow_execution_id: str
    ) -> RuntimeExecutionDescriptor | None: ...

    def load_trace(self, workflow_execution_id: str) -> RuntimeExecutionTrace: ...

    def list_content_metadata(
        self, workflow_execution_id: str
    ) -> tuple[Mapping[str, Any], ...]: ...

    def load_content(
        self, workflow_execution_id: str, content_ref: str
    ) -> RuntimeExecutionContent | None: ...

    def load_workflow_release(
        self, trace: RuntimeExecutionTrace
    ) -> WorkflowRelease | None: ...


class LiveInspectionAuthorizer(Protocol):
    """Product-supplied current authorization checks for inspection reads."""

    def can_read_execution(
        self,
        request_context: Any,
        execution: RuntimeExecutionDescriptor,
    ) -> bool: ...

    def can_read_content(
        self,
        request_context: Any,
        execution: RuntimeExecutionDescriptor,
        content: Mapping[str, Any],
    ) -> bool: ...


@dataclass(frozen=True)
class LiveInspectionAssembly:
    """Explicit secure assembly inputs for the live application."""

    repository: WorkflowInspectionRepository
    authorizer: LiveInspectionAuthorizer
    request_context_resolver: Callable[[Mapping[str, Any]], Any]

    def build(self) -> "LiveWorkflowInspectorApplication":
        if not callable(self.request_context_resolver):
            raise ValueError("request_context_resolver must be callable")
        return LiveWorkflowInspectorApplication(
            repository=self.repository,
            authorizer=self.authorizer,
            request_context_resolver=self.request_context_resolver,
        )


class PostgresWorkflowInspectionRepository:
    """Compose PostgreSQL execution and release stores for read-only queries."""

    def __init__(
        self,
        execution_store: PostgresRuntimeExecutionRecordStore,
        *,
        release_store: PostgresRuntimeReleaseStore | None = None,
    ) -> None:
        self._execution_store = execution_store
        self._release_store = release_store

    def list_executions(self, *, limit: int = 100) -> tuple[RuntimeExecutionDescriptor, ...]:
        return self._execution_store.list_executions(limit=limit)

    def get_execution_descriptor(
        self, workflow_execution_id: str
    ) -> RuntimeExecutionDescriptor | None:
        return self._execution_store.get_execution_descriptor(workflow_execution_id)

    def load_trace(self, workflow_execution_id: str) -> RuntimeExecutionTrace:
        return self._execution_store.load_trace(workflow_execution_id)

    def list_content_metadata(
        self, workflow_execution_id: str
    ) -> tuple[Mapping[str, Any], ...]:
        return self._execution_store.list_content_metadata(workflow_execution_id)

    def load_content(
        self, workflow_execution_id: str, content_ref: str
    ) -> RuntimeExecutionContent | None:
        return self._execution_store.load_content(workflow_execution_id, content_ref)

    def load_workflow_release(
        self, trace: RuntimeExecutionTrace
    ) -> WorkflowRelease | None:
        if self._release_store is None:
            return None
        execution_records = trace.records_of_type(WorkflowExecutionRecord)
        if not execution_records:
            return None
        execution = execution_records[0]
        release_ref = execution.workflow_release_ref or execution.execution_release_ref
        registry = self._release_store.load_release_registry()
        return next(
            (
                release
                for release in registry.snapshot().workflows
                if release.release_ref == release_ref
            ),
            None,
        )


class LiveWorkflowInspectorApplication:
    """Small WSGI app with no write route and no implicit authorization."""

    def __init__(
        self,
        *,
        repository: WorkflowInspectionRepository,
        authorizer: LiveInspectionAuthorizer,
        request_context_resolver: Callable[[Mapping[str, Any]], Any],
    ) -> None:
        if repository is None or authorizer is None:
            raise ValueError("repository and authorizer are required")
        if not callable(request_context_resolver):
            raise ValueError("request_context_resolver must be callable")
        self._repository = repository
        self._authorizer = authorizer
        self._request_context_resolver = request_context_resolver

    def __call__(self, environ: Mapping[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        if method not in {"GET", "HEAD"}:
            return self._respond(
                start_response,
                "405 Method Not Allowed",
                b'{"error":"read_only"}',
                "application/json; charset=utf-8",
                method=method,
                extra_headers=(("Allow", "GET, HEAD"),),
            )
        path = str(environ.get("PATH_INFO", "/"))
        if path == "/":
            return self._respond(
                start_response,
                "200 OK",
                _LIVE_INSPECTOR_HTML.encode("utf-8"),
                "text/html; charset=utf-8",
                method=method,
            )
        if not path.startswith("/api/"):
            return self._json(start_response, "404 Not Found", {"error": "not_found"}, method)
        try:
            request_context = self._request_context_resolver(environ)
        except Exception:
            return self._json(
                start_response,
                "401 Unauthorized",
                {"error": "authentication_required"},
                method,
            )
        if request_context is None:
            return self._json(
                start_response,
                "401 Unauthorized",
                {"error": "authentication_required"},
                method,
            )
        if path == "/api/executions":
            return self._list_executions(environ, start_response, request_context, method)
        prefix = "/api/executions/"
        if not path.startswith(prefix):
            return self._json(start_response, "404 Not Found", {"error": "not_found"}, method)
        suffix = path[len(prefix) :]
        if not suffix or "/" in suffix:
            return self._json(start_response, "404 Not Found", {"error": "not_found"}, method)
        return self._load_execution(
            suffix,
            environ,
            start_response,
            request_context,
            method,
        )

    def _list_executions(
        self,
        environ: Mapping[str, Any],
        start_response: Callable[..., Any],
        request_context: Any,
        method: str,
    ) -> Iterable[bytes]:
        query = parse_qs(str(environ.get("QUERY_STRING", "")))
        try:
            requested_limit = int(query.get("limit", ["100"])[0])
        except ValueError:
            return self._json(start_response, "400 Bad Request", {"error": "invalid_limit"}, method)
        limit = min(max(requested_limit, 1), 1000)
        rows = (
            row.as_dict()
            for row in self._repository.list_executions(limit=limit)
            if self._authorizer.can_read_execution(request_context, row) is True
        )
        return self._json(start_response, "200 OK", {"executions": list(rows)}, method)

    def _load_execution(
        self,
        workflow_execution_id: str,
        environ: Mapping[str, Any],
        start_response: Callable[..., Any],
        request_context: Any,
        method: str,
    ) -> Iterable[bytes]:
        execution = self._repository.get_execution_descriptor(workflow_execution_id)
        if execution is None or self._authorizer.can_read_execution(
            request_context, execution
        ) is not True:
            return self._json(start_response, "404 Not Found", {"error": "not_found"}, method)
        query = parse_qs(str(environ.get("QUERY_STRING", "")), keep_blank_values=True)
        content_ref = query.get("content_ref", [None])[0]
        if content_ref is not None:
            return self._load_content(
                execution,
                content_ref,
                start_response,
                request_context,
                method,
            )
        trace = self._repository.load_trace(workflow_execution_id)
        release = self._repository.load_workflow_release(trace)
        payload = {
            "execution": execution.as_dict(),
            "workflow_release": None if release is None else release.as_dict(),
            "records": [_record_dict(record) for record in trace.records],
            "commit_receipts": [_receipt_dict(receipt) for receipt in trace.commit_receipts],
            "contents": [dict(row) for row in self._repository.list_content_metadata(workflow_execution_id)],
        }
        return self._json(start_response, "200 OK", payload, method)

    def _load_content(
        self,
        execution: RuntimeExecutionDescriptor,
        content_ref: str,
        start_response: Callable[..., Any],
        request_context: Any,
        method: str,
    ) -> Iterable[bytes]:
        metadata = next(
            (
                item
                for item in self._repository.list_content_metadata(
                    execution.workflow_execution_id
                )
                if item.get("content_ref") == content_ref
            ),
            None,
        )
        if metadata is None or self._authorizer.can_read_content(
            request_context,
            execution,
            metadata,
        ) is not True:
            return self._json(start_response, "404 Not Found", {"error": "not_found"}, method)
        content = self._repository.load_content(
            execution.workflow_execution_id,
            content_ref,
        )
        if content is None:
            return self._json(start_response, "404 Not Found", {"error": "not_found"}, method)
        return self._respond(
            start_response,
            "200 OK",
            content.body,
            content.media_type,
            method=method,
            extra_headers=(
                ("X-Content-SHA256", content.content_sha256),
                ("Content-Disposition", "inline"),
            ),
        )

    def _json(
        self,
        start_response: Callable[..., Any],
        status: str,
        payload: Mapping[str, Any],
        method: str,
    ) -> Iterable[bytes]:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._respond(
            start_response,
            status,
            body,
            "application/json; charset=utf-8",
            method=method,
        )

    @staticmethod
    def _respond(
        start_response: Callable[..., Any],
        status: str,
        body: bytes,
        content_type: str,
        *,
        method: str,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> Iterable[bytes]:
        headers = (
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            (
                "Content-Security-Policy",
                "default-src 'none'; style-src 'unsafe-inline'; "
                "script-src 'unsafe-inline'; connect-src 'self'",
            ),
            *extra_headers,
        )
        start_response(status, list(headers))
        return (b"" if method == "HEAD" else body,)


def _record_dict(record: Any) -> dict[str, Any]:
    if isinstance(record, RuntimeLedgerRecord):
        return runtime_record_as_dict(record)
    if isinstance(record, LegacyAuthorizationLedgerRecord):
        return legacy_authorization_record_as_dict(record)
    raise TypeError(f"unsupported execution record: {type(record).__name__}")


def _receipt_dict(receipt: CommitReceipt) -> dict[str, Any]:
    return {
        "workflow_execution_id": receipt.workflow_execution_id,
        "transaction_id": receipt.transaction_id,
        "transaction_sha256": receipt.transaction_sha256,
        "record_count": receipt.record_count,
        "committed_outcome_refs": list(receipt.committed_outcome_refs),
        "replayed": receipt.replayed,
    }


def load_live_inspection_application(factory_ref: str) -> LiveWorkflowInspectorApplication:
    """Load an explicitly configured ``module:factory`` secure assembly."""

    module_name, separator, attribute_name = factory_ref.partition(":")
    if not separator or not module_name or not attribute_name:
        raise ValueError("application factory must use module:attribute syntax")
    factory = getattr(importlib.import_module(module_name), attribute_name)
    value = factory()
    if isinstance(value, LiveInspectionAssembly):
        return value.build()
    if isinstance(value, LiveWorkflowInspectorApplication):
        return value
    raise TypeError(
        "application factory must return LiveInspectionAssembly or "
        "LiveWorkflowInspectorApplication"
    )


def serve_live_inspector(
    application: LiveWorkflowInspectorApplication,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    """Serve the configured read-only application until interrupted."""

    if not isinstance(application, LiveWorkflowInspectorApplication):
        raise TypeError("application must be LiveWorkflowInspectorApplication")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    with make_server(host, port, application) as server:
        server.serve_forever()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve an authorized, read-only Agent Runtime Live Inspector."
    )
    parser.add_argument(
        "--application-factory",
        required=True,
        help="Trusted module:factory returning a LiveInspectionAssembly",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    arguments = parser.parse_args(argv)
    application = load_live_inspection_application(arguments.application_factory)
    serve_live_inspector(application, host=arguments.host, port=arguments.port)
    return 0


_LIVE_INSPECTOR_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent Runtime Live Inspector</title>
<style>:root{color-scheme:dark;--bg:#070b10;--panel:#101720;--line:#273442;--text:#e8eef5;--muted:#90a0b1;--cyan:#57ded2;--green:#7ee2a9;--amber:#efbd61;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}button,input{font:inherit;color:inherit}.top{height:66px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 20px;position:sticky;top:0;background:#070b10f2;z-index:4}.brand small,.eyebrow{color:var(--cyan);font-size:10px;font-weight:800;letter-spacing:.13em}.live{color:var(--green);font-size:11px}.shell{display:grid;grid-template-columns:300px minmax(0,1fr);min-height:calc(100vh - 66px)}aside{border-right:1px solid var(--line);padding:15px;overflow:auto}.search{width:100%;border:1px solid var(--line);background:#0b1118;border-radius:7px;padding:9px;margin:10px 0}.execution{width:100%;text-align:left;border:1px solid var(--line);background:#0c1219;border-radius:8px;padding:11px;margin-bottom:8px;cursor:pointer}.execution:hover,.execution.active{border-color:var(--cyan)}main{padding:18px;min-width:0}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:17px;margin-bottom:14px}.muted{color:var(--muted)}.mono{font:11px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}h1,h2,h3,p{margin:0}.summary{display:flex;justify-content:space-between;gap:15px}.metrics{display:flex;gap:7px;flex-wrap:wrap}.metric{border:1px solid var(--line);border-radius:7px;padding:8px 10px;min-width:92px}.metric small{display:block;color:var(--muted)}nav{display:flex;gap:4px;border-bottom:1px solid var(--line);overflow:auto;margin-top:15px}nav button{border:0;border-bottom:2px solid transparent;background:none;padding:9px;cursor:pointer;white-space:nowrap}nav button.active{border-color:var(--cyan);color:var(--cyan)}.view{padding-top:14px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.card,details{border:1px solid var(--line);border-radius:7px;background:#0b1118;padding:11px}.wide{grid-column:1/-1}.card small{display:block;color:var(--muted);text-transform:uppercase}.graph{display:flex;gap:18px;overflow:auto;padding:8px}.node{min-width:165px;position:relative}.node:after{content:'→';position:absolute;right:-15px;top:26px;color:var(--muted)}.node:last-child:after{display:none}details{margin-bottom:8px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow:auto;max-height:62vh;background:#070b10;border:1px solid var(--line);padding:12px;border-radius:7px;color:#cbd8e5}.content{display:flex;justify-content:space-between;gap:8px;align-items:center}.content button{border:1px solid var(--cyan);background:transparent;border-radius:5px;padding:5px 8px;cursor:pointer}.notice{color:var(--amber)}@media(max-width:850px){.shell{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--line)}.grid{grid-template-columns:1fr}}</style></head>
<body><header class="top"><div class="brand"><small>AGENT RUNTIME · READ ONLY</small><h1>Live Inspector</h1></div><span class="live" id="live">● LIVE</span></header><div class="shell"><aside><p class="eyebrow">AUTHORIZED EXECUTIONS</p><input class="search" id="search" placeholder="Filter workflow or execution"><div id="executions"></div></aside><main><section class="panel" id="empty"><h2>Select an Agent Workflow execution</h2><p class="muted">Inspect its registered graph, module runs, attempts, model/tool calls, outputs, evaluations and recovery facts.</p></section><section id="workspace" hidden><section class="panel summary"><div><p class="eyebrow" id="workflow"></p><h2 id="execution"></h2><p class="mono muted" id="release"></p></div><div class="metrics" id="metrics"></div></section><section class="panel"><nav id="tabs"><button data-tab="graph" class="active">Agent graph</button><button data-tab="modules">Module runs</button><button data-tab="attempts">Attempts</button><button data-tab="operations">Model &amp; tools</button><button data-tab="decisions">Evaluation</button><button data-tab="recovery">Recovery</button><button data-tab="content">Content</button><button data-tab="records">All records</button></nav><div class="view" id="view"></div></section></section></main></div>
<script>"use strict";let executions=[],selected=null,data=null,tab="graph";const el=id=>document.getElementById(id),esc=v=>String(v??"unknown").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;"),pretty=v=>esc(JSON.stringify(v,null,2)),types=(...names)=>(data?.records??[]).filter(r=>names.includes(r.record_type)),card=(label,value,wide=false)=>`<div class="card ${wide?'wide':''}"><small>${esc(label)}</small><span class="mono">${esc(value)}</span></div>`;
async function json(url){const response=await fetch(url,{credentials:"same-origin",headers:{Accept:"application/json"}});if(!response.ok)throw new Error(`${response.status}`);return response.json()}async function refreshList(){try{executions=(await json('/api/executions')).executions;renderList();el('live').textContent='● LIVE'}catch(error){el('live').textContent='● DISCONNECTED'}}function renderList(){const q=el('search').value.toLowerCase();el('executions').innerHTML=executions.filter(x=>`${x.workflow_id} ${x.workflow_execution_id}`.toLowerCase().includes(q)).map(x=>`<button class="execution ${selected===x.workflow_execution_id?'active':''}" data-id="${esc(x.workflow_execution_id)}"><strong>${esc(x.workflow_id)}</strong><div class="mono muted">${esc(x.workflow_execution_id)}</div></button>`).join('')||'<p class="muted">No authorized executions.</p>';document.querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>load(b.dataset.id))}
async function load(id){selected=id;data=await json(`/api/executions/${encodeURIComponent(id)}`);renderList();render();el('empty').hidden=true;el('workspace').hidden=false}function render(){const x=data.execution,r=data.records,release=data.workflow_release;el('workflow').textContent=x.workflow_id;el('execution').textContent=x.workflow_execution_id;el('release').textContent=x.execution_release_ref;const counts=[['Module runs',types('WorkflowModuleRunRecord').length],['Attempts',types('WorkflowAttemptRecord').length],['Model calls',types('ModelCallRecord').length],['Tool calls',types('ToolCallRecord').length]];el('metrics').innerHTML=counts.map(([a,b])=>`<div class="metric"><small>${a}</small><strong>${b}</strong></div>`).join('');document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));const views={graph:()=>release?`<div class="graph">${release.nodes.map(n=>`<article class="card node"><small>${esc(n.node_kind)}</small><strong>${esc(n.node_id)}</strong><div class="mono muted">${esc(n.module_release_ref??'control')}</div></article>`).join('')}</div><pre>${pretty(release.edges)}</pre>`:'<p class="notice">The frozen Workflow release is not available from the configured release store.</p>',modules:()=>recordView(types('WorkflowModuleRunRecord','WorkflowModuleExecutionVariantRecord')),attempts:()=>recordView(types('WorkflowAttemptStartedRecord','WorkflowAttemptRecord','AttemptOrphanedRecord','StaleOutputRecord')),operations:()=>recordView(types('InvocationCommitRecord','ModelCallRecord','ToolCallRecord','UsageEvent','GatewayOperationEffectRecord','InvocationGatewayEffectBindingRecord')),decisions:()=>recordView(types('EvaluationRun','EvaluationResult','EvaluationSet','Selection','ModuleOutputResolutionRecord','ModuleOutcome')),recovery:()=>recordView(types('CheckpointRecord','BackendAcknowledgementRecord','ExternalEventApplicationRecord')),content:()=>contentView(),records:()=>recordView(r)};el('view').innerHTML=views[tab]()}
function recordView(rows){return rows.map((r,i)=>`<details ${i===0?'open':''}><summary>${esc(r.record_type)}</summary><pre>${pretty(r.record)}</pre></details>`).join('')||'<p class="muted">No committed records in this category.</p>'}function contentView(){return data.contents.map(c=>`<div class="card content"><div><strong>${esc(c.content_ref)}</strong><div class="mono muted">${esc(c.media_type)} · ${esc(c.byte_size)} bytes · ${esc(c.content_sha256)}</div></div><button data-content="${esc(c.content_ref)}">Read authorized body</button></div>`).join('')||'<p class="muted">No content bodies were recorded.</p>'}document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;render()});el('view').addEventListener('click',async event=>{const ref=event.target.dataset.content;if(!ref)return;const response=await fetch(`/api/executions/${encodeURIComponent(selected)}?content_ref=${encodeURIComponent(ref)}`,{credentials:'same-origin'});if(!response.ok){event.target.textContent='Not authorized';return}const body=await response.text();const pre=document.createElement('pre');pre.textContent=body;event.target.closest('.card').after(pre)});el('search').oninput=renderList;refreshList();setInterval(async()=>{await refreshList();if(selected)try{data=await json(`/api/executions/${encodeURIComponent(selected)}`);render()}catch(error){}},3000);</script></body></html>'''


if __name__ == "__main__":  # pragma: no cover - exercised by the console script
    raise SystemExit(main())


__all__ = [
    "LiveInspectionAssembly",
    "LiveInspectionAuthorizer",
    "LiveWorkflowInspectorApplication",
    "PostgresWorkflowInspectionRepository",
    "WorkflowInspectionRepository",
    "load_live_inspection_application",
    "main",
    "serve_live_inspector",
]
