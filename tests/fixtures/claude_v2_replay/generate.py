"""Regenerate committed-history fixtures using the exact predecessor Runtime.

Run with the existing project Python and an explicit clean 86b5892 source root.
Command: python -B tests/fixtures/claude_v2_replay/generate.py /path/to/clean-parent
The canonical JSON is written to stdout; committed.json is its frozen output.
Only in-memory stores and an in-process Provider double are used. No login,
installation, network, PostgreSQL or real model invocation occurs.
"""
from dataclasses import asdict, fields
import base64
import json
from pathlib import Path
import subprocess
import sys
import tempfile

PARENT = "86b58929035daec2787ce0d37e4a74856764de99"
root = Path(sys.argv[1]).resolve(strict=True)
assert subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() == PARENT
subprocess.run(["git", "-C", str(root), "diff", "--quiet", "HEAD", "--", "src", "tests"], check=True)
sys.path[:0] = [str(root / "src"), str(root / "tests")]

import agent_runtime
assert Path(agent_runtime.__file__).resolve() == root / "src/agent_runtime/__init__.py"
from agent_runtime import run_registered_workflow_module
from agent_runtime.execution import AgentExecutionAdapterRegistry
from agent_runtime.contracts.registry_release_definition import ExecutionProfileRelease
from agent_runtime.ledger.ledger_postgres_persistence import _persisted_record_as_dict
from agent_runtime.registry import (
    RuntimeReleaseBundle, compile_execution_variant_policy_release,
    ExecutionVariantPolicyReleaseCandidate, ExecutionVariantProfileBindingCandidate,
)
from test_agent_runtime_registered_module_execution import _environment
from test_agent_runtime_native_structured_output import _StubInlineAdapter

cases = []
with tempfile.TemporaryDirectory(prefix="v2-history-") as temporary:
    for workspace in ("none", "own_draft_read_write"):
        env = _environment(Path(temporary) / workspace)
        profile = ExecutionProfileRelease.build(**{
            **env.profile._payload(), "execution_profile_id": "claude_v2_history",
            "release_ref": "execution-profile:claude_v2_history@v1", "execution_profile_version": "v1",
            "executor_adapter_id": "claude_cli_adapter", "executor_adapter_revision": "v2",
            "transport_kind": "claude_cli", "provider_id": "anthropic", "model_id": "claude-opus-5[1m]",
            "reasoning_profile": "xhigh", "execution_mode": "agent", "attempt_workspace_policy": workspace,
            "tool_policy": (), "gateway_access_reasons": (),
        })
        workflow = env.kwargs["workflow"]
        selection = compile_execution_variant_policy_release(ExecutionVariantPolicyReleaseCandidate(
            policy_id="claude_v2_selection", policy_version="v1", origin_kind="workflow",
            origin_release_ref=workflow.release_ref, origin_release_sha256=workflow.release_sha256,
            bindings=(ExecutionVariantProfileBindingCandidate("run", profile.release_ref, profile.release_sha256),)))
        env.registry.register_bundle(RuntimeReleaseBundle(execution_profiles=(profile,), execution_variant_policies=(selection,)))
        provider = _StubInlineAdapter(release_registry=env.registry, artifact_host=env.cell,
            adapter_id="claude_cli_adapter", adapter_revision="v2", provider_id="anthropic",
            transport_kind="claude_cli", transport_family="cli", supported_execution_modes=("agent",),
            payload=b'{"value":"committed by predecessor"}')
        adapters = AgentExecutionAdapterRegistry()
        adapters.register(provider)
        payload, key = {"value": "frozen predecessor input"}, "v2_existing_request"
        result = run_registered_workflow_module(module_id=env.module.module_id, input_payload=payload,
            idempotency_key=key, **{**env.kwargs, "variant_policy": selection, "adapters": adapters})
        assert result.attempts[0].status == "completed" and provider.calls == 1
        execution_id = result.module_run.workflow_execution_id
        snapshot = env.registry.snapshot()
        bundle = RuntimeReleaseBundle(**{field.name: getattr(snapshot, field.name) for field in fields(RuntimeReleaseBundle)})
        artifacts = []
        for idempotency_key, ref in env.cell._ref_by_idempotency.items():
            stored = env.cell.artifact(ref)
            artifacts.append({"idempotency_key": idempotency_key, "resolved": asdict(stored.resolved),
                "schema_ref": stored.schema_ref, "schema_sha256": stored.schema_sha256,
                "media_type": stored.media_type, "content_base64": base64.b64encode(stored.content).decode()})
        contents = [{**asdict(value), "body": base64.b64encode(value.body).decode()}
                    for value in env.contents.values.values()]
        cases.append({"workspace": workspace, "bundle": bundle.as_dict(), "module_id": env.module.module_id,
            "workflow_ref": workflow.release_ref, "workflow_sha256": workflow.release_sha256,
            "selection_ref": selection.release_ref, "selection_sha256": selection.release_sha256,
            "input_payload": payload, "idempotency_key": key, "original_result": asdict(result),
            "provider_calls": provider.calls, "host_calls": env.host.calls,
            "records": [_persisted_record_as_dict(item) for item in env.store.load_trace(execution_id).records],
            "artifacts": artifacts, "contents": contents})
print(json.dumps({"source_commit": PARENT, "provider_execution": "in_process_double", "cases": cases},
                 ensure_ascii=False, sort_keys=True, separators=(",", ":")))
