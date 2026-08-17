"""Authorized, read-only HTTP application for live Runtime inspection."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
import re
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from ..contracts.ledger_content_definition import RuntimeExecutionContent
from ..contracts.ledger_record_definition import (
    CommitReceipt,
    LegacyAuthorizationLedgerRecord,
    RuntimeExecutionTrace,
    RuntimeLedgerRecord,
    legacy_authorization_record_as_dict,
    runtime_record_as_dict,
)
from ..foundation.foundation_contract_validation import format_utc_timestamp
from ..contracts.registry_release_definition import WorkflowRelease
from ..ledger.ledger_postgres_persistence import (
    RuntimeExecutionDescriptor,
    RuntimeExecutionPageCursor,
)
from .inspection_execution_projecting import build_runtime_execution_inspection


class WorkflowInspectionRepository(Protocol):
    """Read-only facts required by the live Inspector."""

    def list_executions(
        self,
        *,
        limit: int = 100,
        before: RuntimeExecutionPageCursor | None = None,
    ) -> tuple[RuntimeExecutionDescriptor, ...]: ...

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


def observed_now_utc() -> str:
    """Return the canonical UTC observation instant for lease inspection."""

    return format_utc_timestamp(datetime.now(timezone.utc))


@dataclass(frozen=True)
class LiveInspectionAssembly:
    """Explicit secure assembly inputs for the live application."""

    repository: WorkflowInspectionRepository
    authorizer: LiveInspectionAuthorizer
    request_context_resolver: Callable[[Mapping[str, Any]], Any]
    frame_ancestors: tuple[str, ...] = ("'none'",)
    observation_clock: Callable[[], str] = observed_now_utc

    def build(self) -> "LiveWorkflowInspectorApplication":
        if not callable(self.request_context_resolver):
            raise ValueError("request_context_resolver must be callable")
        return LiveWorkflowInspectorApplication(
            repository=self.repository,
            authorizer=self.authorizer,
            request_context_resolver=self.request_context_resolver,
            frame_ancestors=self.frame_ancestors,
            observation_clock=self.observation_clock,
        )


class LiveWorkflowInspectorApplication:
    """Small WSGI app with no write route and no implicit authorization."""

    def __init__(
        self,
        *,
        repository: WorkflowInspectionRepository,
        authorizer: LiveInspectionAuthorizer,
        request_context_resolver: Callable[[Mapping[str, Any]], Any],
        frame_ancestors: tuple[str, ...] = ("'none'",),
        observation_clock: Callable[[], str] = observed_now_utc,
    ) -> None:
        if repository is None or authorizer is None:
            raise ValueError("repository and authorizer are required")
        if not callable(request_context_resolver):
            raise ValueError("request_context_resolver must be callable")
        if not callable(observation_clock):
            raise ValueError("observation_clock must be callable")
        self._repository = repository
        self._authorizer = authorizer
        self._request_context_resolver = request_context_resolver
        self._frame_ancestors = _validate_frame_ancestors(frame_ancestors)
        self._observation_clock = observation_clock

    def __call__(
        self,
        environ: Mapping[str, Any],
        start_response: Callable[..., Any],
    ) -> Iterable[bytes]:
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
        if path == "/assets/live-inspector.css":
            return self._respond(
                start_response,
                "200 OK",
                _LIVE_INSPECTOR_STYLE.encode("utf-8"),
                "text/css; charset=utf-8",
                method=method,
            )
        if path == "/assets/live-inspector.js":
            return self._respond(
                start_response,
                "200 OK",
                _LIVE_INSPECTOR_SCRIPT.encode("utf-8"),
                "text/javascript; charset=utf-8",
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
        try:
            scan_before = _decode_execution_cursor(query.get("cursor", [None])[0])
        except ValueError:
            return self._json(
                start_response,
                "400 Bad Request",
                {"error": "invalid_cursor"},
                method,
            )
        authorized: list[RuntimeExecutionDescriptor] = []
        examined = 0
        exhausted = False
        while len(authorized) < limit and examined < 5000:
            scan_limit = min(1000, 5000 - examined)
            rows = self._repository.list_executions(
                limit=scan_limit,
                before=scan_before,
            )
            if not rows:
                exhausted = True
                break
            for row in rows:
                examined += 1
                scan_before = RuntimeExecutionPageCursor(
                    recorded_at_utc=row.recorded_at_utc,
                    workflow_execution_id=row.workflow_execution_id,
                )
                if self._can_read_execution(request_context, row):
                    authorized.append(row)
                    if len(authorized) == limit:
                        break
            if len(authorized) == limit:
                break
            if len(rows) < scan_limit:
                exhausted = True
                break
        next_cursor = (
            None
            if exhausted
            else _encode_execution_cursor(scan_before)
        )
        return self._json(
            start_response,
            "200 OK",
            {
                "executions": [row.as_dict() for row in authorized],
                "next_cursor": next_cursor,
            },
            method,
        )

    def _load_execution(
        self,
        workflow_execution_id: str,
        environ: Mapping[str, Any],
        start_response: Callable[..., Any],
        request_context: Any,
        method: str,
    ) -> Iterable[bytes]:
        execution = self._repository.get_execution_descriptor(workflow_execution_id)
        if execution is None or not self._can_read_execution(
            request_context,
            execution,
        ):
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
        inspection = (
            build_runtime_execution_inspection(
                trace,
                workflow_release=release,
                # The request instant is the lease observation: without it a
                # live reader would never see an expired lease surface as
                # recovery_required.
                observed_at_utc=self._observation_clock(),
            )
            if trace.records
            else None
        )
        payload = {
            "execution": execution.as_dict(),
            "workflow_release": None if release is None else release.as_dict(),
            "inspection": inspection,
            "records": [_record_dict(record) for record in trace.records],
            "commit_receipts": [_receipt_dict(receipt) for receipt in trace.commit_receipts],
            "contents": [
                dict(row)
                for row in self._repository.list_content_metadata(
                    workflow_execution_id
                )
            ],
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
        if metadata is None or not self._can_read_content(
            request_context,
            execution,
            metadata,
        ):
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
            "application/octet-stream",
            method=method,
            extra_headers=(
                ("X-Content-SHA256", content.content_sha256),
                ("Content-Disposition", 'attachment; filename="execution-content.bin"'),
            ),
            sandboxed_download=True,
        )

    def _can_read_execution(
        self,
        request_context: Any,
        execution: RuntimeExecutionDescriptor,
    ) -> bool:
        try:
            return (
                self._authorizer.can_read_execution(request_context, execution)
                is True
            )
        except Exception:
            return False

    def _can_read_content(
        self,
        request_context: Any,
        execution: RuntimeExecutionDescriptor,
        content: Mapping[str, Any],
    ) -> bool:
        try:
            return (
                self._authorizer.can_read_content(
                    request_context,
                    execution,
                    content,
                )
                is True
            )
        except Exception:
            return False

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

    def _respond(
        self,
        start_response: Callable[..., Any],
        status: str,
        body: bytes,
        content_type: str,
        *,
        method: str,
        extra_headers: tuple[tuple[str, str], ...] = (),
        sandboxed_download: bool = False,
    ) -> Iterable[bytes]:
        frame_headers = (
            (("X-Frame-Options", "DENY"),)
            if self._frame_ancestors == ("'none'",)
            else ()
        )
        content_security_policy = (
            "sandbox; default-src 'none'; frame-ancestors 'none'"
            if sandboxed_download
            else "default-src 'none'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; "
            f"frame-ancestors {' '.join(self._frame_ancestors)}"
        )
        headers = (
            ("Content-Type", content_type),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            (
                "Content-Security-Policy",
                content_security_policy,
            ),
            ("Cross-Origin-Resource-Policy", "same-origin"),
            *frame_headers,
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


def _encode_execution_cursor(cursor: RuntimeExecutionPageCursor | None) -> str:
    if cursor is None:
        raise ValueError("execution cursor requires an authorized row")
    cursor.validate()
    payload = json.dumps(
        [cursor.recorded_at_utc, cursor.workflow_execution_id],
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_execution_cursor(value: str | None) -> RuntimeExecutionPageCursor | None:
    if value is None:
        return None
    if type(value) is not str or not value or len(value) > 2048:
        raise ValueError("invalid execution cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(
            padded,
            altchars=b"-_",
            validate=True,
        ).decode("ascii")
        payload = json.loads(decoded)
        if (
            type(payload) is not list
            or len(payload) != 2
            or any(type(item) is not str for item in payload)
        ):
            raise ValueError("invalid execution cursor")
        cursor = RuntimeExecutionPageCursor(
            recorded_at_utc=payload[0],
            workflow_execution_id=payload[1],
        )
        cursor.validate()
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid execution cursor") from exc
    return cursor


def _validate_frame_ancestors(value: tuple[str, ...]) -> tuple[str, ...]:
    if type(value) is not tuple or not value:
        raise ValueError("frame_ancestors must be a non-empty tuple")
    valid = re.compile(r"https://[a-z0-9.-]+(?::[0-9]{1,5})?$")
    if any(
        type(item) is not str
        or item not in {"'none'", "'self'"}
        and not valid.fullmatch(item)
        for item in value
    ):
        raise ValueError("frame_ancestors contains an invalid origin")
    if "'none'" in value and len(value) != 1:
        raise ValueError("frame_ancestors 'none' cannot be combined")
    return value


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


_LIVE_INSPECTOR_DOCUMENT = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Agent Runtime Live Inspector</title>
<style>:root{color-scheme:dark;--bg:#070b10;--panel:#101720;--line:#273442;--text:#e8eef5;--muted:#90a0b1;--cyan:#57ded2;--green:#7ee2a9;--amber:#efbd61;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text)}button,input{font:inherit;color:inherit}.top{height:66px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;padding:0 20px;position:sticky;top:0;background:#070b10f2;z-index:4}.brand small,.eyebrow{color:var(--cyan);font-size:10px;font-weight:800;letter-spacing:.13em}.live{color:var(--green);font-size:11px}.shell{display:grid;grid-template-columns:300px minmax(0,1fr);min-height:calc(100vh - 66px)}aside{border-right:1px solid var(--line);padding:15px;overflow:auto}.search{width:100%;border:1px solid var(--line);background:#0b1118;border-radius:7px;padding:9px;margin:10px 0}.execution{width:100%;text-align:left;border:1px solid var(--line);background:#0c1219;border-radius:8px;padding:11px;margin-bottom:8px;cursor:pointer}.execution:hover,.execution.active{border-color:var(--cyan)}main{padding:18px;min-width:0}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:17px;margin-bottom:14px}.muted{color:var(--muted)}.mono{font:11px/1.5 ui-monospace,SFMono-Regular,Consolas,monospace;overflow-wrap:anywhere}h1,h2,h3,p{margin:0}.summary{display:flex;justify-content:space-between;gap:15px}.metrics{display:flex;gap:7px;flex-wrap:wrap}.metric{border:1px solid var(--line);border-radius:7px;padding:8px 10px;min-width:92px}.metric small{display:block;color:var(--muted)}nav{display:flex;gap:4px;border-bottom:1px solid var(--line);overflow:auto;margin-top:15px}nav button{border:0;border-bottom:2px solid transparent;background:none;padding:9px;cursor:pointer;white-space:nowrap}nav button.active{border-color:var(--cyan);color:var(--cyan)}.view{padding-top:14px}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.card,details{border:1px solid var(--line);border-radius:7px;background:#0b1118;padding:11px}.wide{grid-column:1/-1}.card small{display:block;color:var(--muted);text-transform:uppercase}.graph{display:flex;gap:18px;overflow:auto;padding:8px}.node{min-width:165px;position:relative}.node:after{content:'→';position:absolute;right:-15px;top:26px;color:var(--muted)}.node:last-child:after{display:none}details{margin-bottom:8px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow:auto;max-height:62vh;background:#070b10;border:1px solid var(--line);padding:12px;border-radius:7px;color:#cbd8e5}.content{display:flex;justify-content:space-between;gap:8px;align-items:center}.content button{border:1px solid var(--cyan);background:transparent;border-radius:5px;padding:5px 8px;cursor:pointer}.notice{color:var(--amber)}@media(max-width:850px){.shell{grid-template-columns:1fr}aside{border-right:0;border-bottom:1px solid var(--line)}.grid{grid-template-columns:1fr}}</style></head>
<body><header class="top"><div class="brand"><small>AGENT RUNTIME · READ ONLY</small><h1>Live Inspector</h1></div><span class="live" id="live">● LIVE</span></header><div class="shell"><aside><p class="eyebrow">AUTHORIZED EXECUTIONS</p><input class="search" id="search" placeholder="Filter workflow or execution"><div id="executions"></div></aside><main><section class="panel" id="empty"><h2>Select an Agent Workflow execution</h2><p class="muted">Inspect its registered graph, module runs, attempts, model/tool calls, outputs, evaluations and recovery facts.</p></section><section id="workspace" hidden><section class="panel summary"><div><p class="eyebrow" id="workflow"></p><h2 id="execution"></h2><p class="mono muted" id="release"></p></div><div class="metrics" id="metrics"></div></section><section class="panel"><nav id="tabs"><button data-tab="graph" class="active">Agent graph</button><button data-tab="modules">Module runs</button><button data-tab="attempts">Attempts</button><button data-tab="operations">Model &amp; tools</button><button data-tab="decisions">Evaluation</button><button data-tab="recovery">Recovery</button><button data-tab="content">Content</button><button data-tab="records">All records</button></nav><div class="view" id="view"></div></section></section></main></div>
<script>"use strict";let executions=[],selected=null,data=null,tab="graph";const el=id=>document.getElementById(id),esc=v=>String(v??"unknown").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;"),pretty=v=>esc(JSON.stringify(v,null,2)),types=(...names)=>(data?.records??[]).filter(r=>names.includes(r.record_type)),card=(label,value,wide=false)=>`<div class="card ${wide?'wide':''}"><small>${esc(label)}</small><span class="mono">${esc(value)}</span></div>`;
async function json(url){const response=await fetch(url,{credentials:"same-origin",headers:{Accept:"application/json"}});if(!response.ok)throw new Error(`${response.status}`);return response.json()}async function refreshList(){try{let rows=[],cursor=null,pages=0;do{const page=await json(`/api/executions?limit=250${cursor?`&cursor=${encodeURIComponent(cursor)}`:''}`);rows.push(...page.executions);cursor=page.next_cursor;pages+=1}while(cursor&&pages<100);executions=rows;renderList();el('live').textContent=cursor?'● PARTIAL':'● LIVE'}catch(error){el('live').textContent='● DISCONNECTED'}}function renderList(){const q=el('search').value.toLowerCase();el('executions').innerHTML=executions.filter(x=>`${x.workflow_id} ${x.workflow_execution_id}`.toLowerCase().includes(q)).map(x=>`<button class="execution ${selected===x.workflow_execution_id?'active':''}" data-id="${esc(x.workflow_execution_id)}"><strong>${esc(x.workflow_id)}</strong><div class="mono muted">${esc(x.workflow_execution_id)}</div></button>`).join('')||'<p class="muted">No authorized executions.</p>';document.querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>load(b.dataset.id))}
async function load(id){selected=id;data=await json(`/api/executions/${encodeURIComponent(id)}`);renderList();render();el('empty').hidden=true;el('workspace').hidden=false}function render(){const x=data.execution,r=data.records,release=data.workflow_release;el('workflow').textContent=x.workflow_id;el('execution').textContent=x.workflow_execution_id;el('release').textContent=x.execution_release_ref;const counts=[['Module runs',types('WorkflowModuleRunRecord').length],['Attempts',types('WorkflowAttemptRecord').length],['Model calls',types('ModelCallRecord').length],['Tool calls',types('ToolCallRecord').length]];el('metrics').innerHTML=counts.map(([a,b])=>`<div class="metric"><small>${a}</small><strong>${b}</strong></div>`).join('');document.querySelectorAll('[data-tab]').forEach(b=>b.classList.toggle('active',b.dataset.tab===tab));const views={graph:()=>release?`<div class="graph">${release.nodes.map(n=>`<article class="card node"><small>${esc(n.node_kind)}</small><strong>${esc(n.node_id)}</strong><div class="mono muted">${esc(n.module_release_ref??'control')}</div></article>`).join('')}</div><pre>${pretty({parallel_groups:release.parallel_groups??[],edges:release.edges})}</pre>`:'<p class="notice">The frozen Workflow release is not available from the configured release store.</p>',modules:()=>recordView(types('WorkflowModuleRunRecord','WorkflowModuleExecutionVariantRecord')),attempts:()=>recordView(types('WorkflowAttemptStartedRecord','WorkflowAttemptRecord','AttemptOrphanedRecord','StaleOutputRecord')),operations:()=>recordView(types('InvocationCommitRecord','ModelCallRecord','ToolCallRecord','UsageEvent','GatewayOperationEffectRecord','InvocationGatewayEffectBindingRecord')),decisions:()=>recordView(types('EvaluationRun','EvaluationResult','EvaluationSet','Selection','ModuleOutputResolutionRecord','ModuleOutcome')),recovery:()=>recordView(types('CheckpointRecord','BackendAcknowledgementRecord','ExternalEventApplicationRecord')),content:()=>contentView(),records:()=>recordView(r)};el('view').innerHTML=views[tab]()}
function recordView(rows){return rows.map((r,i)=>`<details ${i===0?'open':''}><summary>${esc(r.record_type)}</summary><pre>${pretty(r.record)}</pre></details>`).join('')||'<p class="muted">No committed records in this category.</p>'}function contentView(){return data.contents.map(c=>`<div class="card content"><div><strong>${esc(c.content_ref)}</strong><div class="mono muted">${esc(c.media_type)} · ${esc(c.byte_size)} bytes · ${esc(c.content_sha256)}</div></div><button data-content="${esc(c.content_ref)}">Read authorized body</button></div>`).join('')||'<p class="muted">No content bodies were recorded.</p>'}document.querySelectorAll('[data-tab]').forEach(b=>b.onclick=()=>{tab=b.dataset.tab;render()});el('view').addEventListener('click',async event=>{const ref=event.target.dataset.content;if(!ref)return;const response=await fetch(`/api/executions/${encodeURIComponent(selected)}?content_ref=${encodeURIComponent(ref)}`,{credentials:'same-origin'});if(!response.ok){event.target.textContent='Not authorized';return}const body=await response.text();const pre=document.createElement('pre');pre.textContent=body;event.target.closest('.card').after(pre)});el('search').oninput=renderList;refreshList();setInterval(async()=>{await refreshList();if(selected)try{data=await json(`/api/executions/${encodeURIComponent(selected)}`);render()}catch(error){}},3000);</script></body></html>'''

_HTML_BEFORE_STYLE, _, _HTML_AFTER_STYLE_START = _LIVE_INSPECTOR_DOCUMENT.partition(
    "<style>"
)
_LIVE_INSPECTOR_STYLE, _, _HTML_AFTER_STYLE = _HTML_AFTER_STYLE_START.partition(
    "</style>"
)
_HTML_BEFORE_SCRIPT, _, _HTML_AFTER_SCRIPT_START = _HTML_AFTER_STYLE.partition(
    "<script>"
)
_LIVE_INSPECTOR_SCRIPT, _, _HTML_AFTER_SCRIPT = _HTML_AFTER_SCRIPT_START.partition(
    "</script>"
)
_LIVE_INSPECTOR_HTML = (
    _HTML_BEFORE_STYLE
    + '<link rel="stylesheet" href="/assets/live-inspector.css">'
    + _HTML_BEFORE_SCRIPT
    + '<script src="/assets/live-inspector.js"></script>'
    + _HTML_AFTER_SCRIPT
)


if __name__ == "__main__":  # pragma: no cover - exercised by the console script
    raise SystemExit(main())


__all__ = [
    "LiveInspectionAssembly",
    "LiveInspectionAuthorizer",
    "LiveWorkflowInspectorApplication",
    "WorkflowInspectionRepository",
    "load_live_inspection_application",
    "main",
    "serve_live_inspector",
]
