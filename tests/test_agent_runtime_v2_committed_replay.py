"""Upgrade replay of records actually committed by Runtime 86b5892.

The frozen fixture is produced by fixtures/claude_v2_replay/generate.py under
the unmodified predecessor source. Replay uses the current public entry and
normal record/content validation, without any registered Provider Adapter.
"""
from dataclasses import asdict
import base64
import json
import sys
from pathlib import Path

import pytest

from agent_runtime import run_registered_workflow_module
from agent_runtime.contracts.ledger_content_definition import RuntimeExecutionContent
from agent_runtime.contracts.ledger_record_definition import LegacyRuntimeRecordBatch
from agent_runtime.execution import AgentExecutionAdapterRegistry, InMemoryCellArtifactStore
from agent_runtime.ledger import InMemoryRuntimeExecutionRecordStore
from agent_runtime.ledger.ledger_postgres_persistence import deserialize_runtime_record
from agent_runtime.registry import RuntimeReleaseBundle, RuntimeReleaseRegistry
from test_agent_runtime_registered_module_execution import _Contents

FIXTURE = Path(__file__).parent / "fixtures/claude_v2_replay/committed.json"


def restore(case):
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(RuntimeReleaseBundle.from_dict(case["bundle"]))
    cell, contents = InMemoryCellArtifactStore(), _Contents()
    for value in case["artifacts"]:
        expected = value["resolved"]
        actual = cell.put_bytes(artifact_kind_id=expected["artifact_kind_id"], schema_version=expected["schema_version"],
            schema_ref=value["schema_ref"], schema_sha256=value["schema_sha256"], media_type=value["media_type"],
            content=base64.b64decode(value["content_base64"]), idempotency_key=value["idempotency_key"],
            logical_name=expected["logical_name"])
        assert asdict(actual) == expected
    for value in case["contents"]:
        contents.stage_content(RuntimeExecutionContent(**{**value, "body": base64.b64decode(value["body"])}))
    store = InMemoryRuntimeExecutionRecordStore(execution_output_integrity_check=contents.contains)
    execution_id = case["original_result"]["module_run"]["workflow_execution_id"]
    store.commit(LegacyRuntimeRecordBatch(execution_id, "restore_predecessor_history",
        tuple(deserialize_runtime_record(item) for item in case["records"])))
    return registry, cell, contents, store, execution_id


@pytest.mark.parametrize("workspace", ["none", "own_draft_read_write"])
@pytest.mark.parametrize("fresh_staging", [False, True])
def test_predecessor_v2_committed_request_replays_without_an_adapter(tmp_path, workspace, fresh_staging):
    data = json.loads(FIXTURE.read_text())
    assert data["source_commit"] == "86b58929035daec2787ce0d37e4a74856764de99"
    case = next(item for item in data["cases"] if item["workspace"] == workspace)
    assert case["provider_calls"] == case["host_calls"] == 1
    registry, original_cell, contents, store, execution_id = restore(case)
    records_before = store.load_trace(execution_id)
    registry_before = registry.snapshot()
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("committed replay must not request authorization or invoke a Provider")
    adapters = AgentExecutionAdapterRegistry()
    staging = InMemoryCellArtifactStore() if fresh_staging else original_cell
    kwargs = dict(module_id=case["module_id"], input_payload=case["input_payload"], idempotency_key=case["idempotency_key"],
        release_registry=registry, workflow=registry.get_workflow(case["workflow_ref"], case["workflow_sha256"]),
        variant_policy=registry.get_execution_variant_policy(case["selection_ref"], case["selection_sha256"]),
        authorize=forbidden, context_client=object(), operation_client=object(),
        enforcing_gateway_id="agent_runtime_module_kernel", environment_id="development", adapters=adapters,
        artifact_host=staging, record_store=store, content_store=contents,
        claim_token_secret=b"host-claim-material-never-model-visible")
    replay = run_registered_workflow_module(**kwargs)
    assert json.loads(json.dumps(asdict(replay))) == case["original_result"]
    assert store.load_trace(execution_id) == records_before
    assert registry.snapshot() == registry_before and calls == []
    assert run_registered_workflow_module(**kwargs) == replay
    # A new key is not replay and must still refuse the retired execution pair.
    from agent_runtime.invocation.invocation_claude_cli_execution import ClaudeAdapter
    adapters.register(ClaudeAdapter(release_registry=registry, artifact_host=staging,
        workspace_root=tmp_path / "attempts", cli_path=Path(sys.executable), process_runner=forbidden))
    with pytest.raises(KeyError, match="claude_cli_adapter@v2"):
        run_registered_workflow_module(**{**kwargs, "idempotency_key": "new_v2_request"})
    assert calls == [] and store.load_trace(execution_id) == records_before
