"""CLI test host: load saved definitions and run the real evaluation kernel.

Only the provider response and live host ports are controlled test doubles.
No release compiler, source loader or persistent registration is called here.
"""

import argparse
import json
from pathlib import Path

from agent_runtime import load_runtime_registration, run_local_workflow_module
from agent_runtime.execution import AgentExecutionAdapterRegistry, InMemoryCellArtifactStore
from agent_runtime.invocation.invocation_codex_module_invocation import CodexCliInvocationResult, CodexCliModuleExecutor
from agent_runtime.ledger import InMemoryRuntimeExecutionRecordStore
from test_agent_runtime_registered_module_execution import _Contents, _Host, _TEST_TIME


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--version")
    args = parser.parse_args()
    from agent_runtime.registry import registry_release_compilation, registry_module_loading
    def reject_authoring(*args, **kwargs):
        raise AssertionError("Cold execution must not compile or load authoring source")
    for name in vars(registry_release_compilation):
        if name.startswith("compile_"):
            setattr(registry_release_compilation, name, reject_authoring)
    registry_module_loading.load_module_registration = reject_authoring
    loaded = load_runtime_registration(args.root, "workflow", args.workflow, args.version)
    cell, contents = InMemoryCellArtifactStore(), _Contents()
    store = InMemoryRuntimeExecutionRecordStore(execution_output_integrity_check=contents.contains)
    host = _Host(cell, loaded.release)
    calls = []

    def invoke(**request):
        calls.append(request)
        event = {"type": "item.completed", "item": {"type": "agent_message",
                 "text": json.dumps({"value": "loaded_" + loaded.release.workflow_version})}}
        usage = {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 2}}
        return CodexCliInvocationResult(0, json.dumps(event) + "\n" + json.dumps(usage), "")

    adapters = AgentExecutionAdapterRegistry()
    adapters.register(CodexCliModuleExecutor(release_registry=loaded.registry, artifact_host=cell,
        workspace_root=args.root / "test_attempts", invoker=invoke, codex_bin="controlled-codex"))
    result = run_local_workflow_module(args.root, args.workflow, version=args.version,
        input_payload={"value": "CLI cold start"}, idempotency_key="cli_" + (args.version or "latest"),
        adapters=adapters, artifact_host=cell, authorize=host.authorize,
        context_client=host, operation_client=host, enforcing_gateway_id="agent_runtime_module_kernel",
        environment_id="development", record_store=store, content_store=contents,
        claim_token_secret=b"test-host-claim-material-never-model-visible", clock=lambda: _TEST_TIME)
    output = result.outputs[0]
    print(json.dumps({"workflow_version": loaded.release.workflow_version,
        "release_ref": loaded.release.release_ref, "execution_id": result.module_run.workflow_execution_id,
        "provider_calls": len(calls), "output": json.loads(cell.read_bytes(output.output_ref, output.output_sha256)),
        "execution": "real_runtime_kernel_with_controlled_provider"}))


if __name__ == "__main__":
    main()
