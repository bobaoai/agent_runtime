"""Model-independent registration and exact per-invocation preparation."""
from dataclasses import fields, replace
import json
import os
import subprocess
from unittest.mock import patch

import pytest

from agent_runtime import (
    ModuleReviewer, RuntimeModulePlugin, load_runtime_registration,
    prepare_local_workflow_module, register_runtime_module_plugin, run_registered_workflow_module,
)
from agent_runtime.execution import AgentExecutionAdapterRegistry, InMemoryCellArtifactStore
from agent_runtime.execution import execution_local_invocation as local
from agent_runtime.registry import RuntimeReleaseBundle, RuntimeReleaseRegistry, PostgresRuntimeReleaseStore
from agent_runtime.registry import registry_module_authoring as authoring
from agent_runtime.ledger import (
    InMemoryRuntimeExecutionRecordStore, PostgresRuntimeExecutionRecordStore, PostgresRuntimeExecutionQueryStore,
)
from agent_runtime.contracts.ledger_record_definition import WorkflowModuleExecutionVariantRecord, WorkflowExecutionRecord
from agent_runtime.invocation import invocation_claude_cli_execution as claude
from test_agent_runtime_claude_native_tools import _fake_cli, _init, _result
from test_agent_runtime_registered_module_execution import _Host, _Contents, _TEST_TIME
from test_agent_runtime_reviewer_registration_cli import (
    _source, _register, _files, _cli, _prepared_bundle, SKILL_ID, MODULE_ID,
)
from test_agent_runtime_postgres_release_store import postgres_release_test_schema
from test_agent_runtime_postgres_execution_ledger import postgres_test_schema


def test_plain_export_and_registration_do_not_resolve_any_model(tmp_path, monkeypatch):
    source, _ = _source(tmp_path / "source", compatible=False)
    monkeypatch.setattr(authoring, "reviewer_execution_profile", lambda *a, **k: pytest.fail("No model selection"))
    reviewer = ModuleReviewer.from_registration(source, skill_id=SKILL_ID, module_id=MODULE_ID)
    exported = reviewer.export(module_version="v1")
    assert exported.module_release.reviewer_defaults is not None
    assert exported.execution_profile is exported.execution_variant is exported.execution_blocker_code is None
    registered = _register(tmp_path / "host", source)
    assert registered.submitted_bundle.execution_profiles == registered.submitted_bundle.execution_variant_policies == ()


@pytest.mark.parametrize("name", ["model_id", "reasoning_profile"])
def test_registration_model_parameters_fail_before_writes(tmp_path, name):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    with pytest.raises(ValueError, match="prepare_local_workflow_module"):
        _register(root, source, **{name: "explicit"})
    assert not root.exists()
    result = _cli(root, source, "v1", "--" + name.replace("_", "-"), "explicit")
    assert result.returncode == 2 and not root.exists()


def test_old_saved_model_is_preserved_but_does_not_select_new_invocations(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    legacy = _prepared_bundle(root, model_id="saved-model-c")
    register_runtime_module_plugin(RuntimeReleaseRegistry(),
        RuntimeModulePlugin("historical_binding", "v1", legacy), root=root)
    before = _files(root)
    _register(root, source)
    assert _files(root) == before
    fresh = _prepared_bundle(root)
    assert fresh.execution_profiles[0].model_id == "claude-opus-5[1m]"
    assert len(fresh.execution_variant_policies) == len(fresh.execution_profiles) == 1
    assert load_runtime_registration(root, "workflow", MODULE_ID + "_review").registry.snapshot().execution_profiles[0].model_id == "saved-model-c"
    assert _files(root) == before


def test_external_policy_lookup_does_not_copy_model_configuration_to_new_root(tmp_path):
    source, _ = _source(tmp_path / "source")
    _register(tmp_path / "old", source)
    registry = RuntimeReleaseRegistry()
    registry.register_bundle(_prepared_bundle(tmp_path / "old", model_id="not-a-registration-default"))
    result = _register(tmp_path / "new", source, release_registry=registry)
    assert not result.submitted_bundle.execution_profiles
    assert not load_runtime_registration(tmp_path / "new", "workflow", MODULE_ID + "_review").registry.snapshot().execution_profiles


def test_new_default_affects_only_new_preparations_not_fixed_files(tmp_path, monkeypatch):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    original = local.reviewer_execution_profile
    old, _ = prepare_local_workflow_module(root, MODULE_ID + "_review")
    monkeypatch.setattr(local, "reviewer_execution_profile",
        lambda defaults, **kw: original(defaults, **{**kw, "model_id": kw.get("model_id") or "model-b"}))
    _register(root, source)
    new, _ = prepare_local_workflow_module(root, MODULE_ID + "_review")
    assert old.registry.snapshot().execution_profiles[0].model_id == "claude-opus-5[1m]"
    assert new.registry.snapshot().execution_profiles[0].model_id == "model-b"
    assert old.release == new.release and _files(root) == before


@pytest.mark.parametrize("transport", ["codex_cli", "claude_agent_sdk", "", "unknown"])
def test_unsupported_transport_has_no_fallback_or_store_write(tmp_path, transport):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    class Store:
        def register_bundle(self, bundle):
            pytest.fail("No writes for an unsupported model transport")
        def load_release_registry(self):
            pytest.fail("No reads for an unsupported model transport")
    with pytest.raises(ValueError, match="Unsupported Reviewer model transport"):
        prepare_local_workflow_module(root, MODULE_ID + "_review", transport_kind=transport, release_store=Store())
    assert _files(root) == before


def _invoke(preparation, tmp_path, calls, *, key, verdict="passed", records=None, contents=None, payload=None):
    saved, selection = preparation
    cell = InMemoryCellArtifactStore()
    host = _Host(cell, saved.release)
    profile = saved.registry.snapshot().execution_profiles[0]
    output = {"verdict": verdict, "check_results": [], "findings": [], "safe_next_step": "Review completed."}
    if verdict == "non_pass":
        output["findings"] = [{"finding_id": "fix_one", "severity": "fix",
            "evidence": {"source_ref": "candidate", "locator": "section_one", "observation": "Missing requirement"},
            "requirement": "Include the requirement", "impact": "Incomplete candidate",
            "accountable_owner_ref": "subject_owner", "required_change": "Revise the candidate"}]
    def process(**kwargs):
        calls.append(kwargs)
        init = {**_init(), "model": profile.model_id}
        terminal = _result(structured_output=output, is_error=verdict == "provider_failure",
            modelUsage={profile.model_id: {"canonicalModel": profile.model_id.removesuffix("[1m]"), "outputTokens": 3}})
        for event in (init, terminal):
            assert kwargs["on_stdout_line"](json.dumps(event))
        return subprocess.CompletedProcess(kwargs["argv"], 0, "", "")
    tmp_path.mkdir(parents=True, exist_ok=True)
    adapters = AgentExecutionAdapterRegistry()
    adapters.register(claude.ClaudeCliNativeToolsModuleExecutor(
        release_registry=saved.registry, artifact_host=cell, workspace_root=tmp_path / "attempts",
        cli_path=_fake_cli(tmp_path), process_runner=process))
    contents = contents if contents is not None else _Contents()
    records = records if records is not None else InMemoryRuntimeExecutionRecordStore(
        execution_output_integrity_check=contents.contains)
    # The provider is a stub; keep its ancillary temporary files inside the
    # test's declared writable root, including under the independent review sandbox.
    temporary_directory = claude.tempfile.TemporaryDirectory
    def test_temporary_directory(*args, **kwargs):
        return temporary_directory(*args, **{**kwargs, "dir": tmp_path})
    with patch.object(claude.tempfile, "TemporaryDirectory", test_temporary_directory):
        run = run_registered_workflow_module(module_id=MODULE_ID, input_payload={} if payload is None else payload,
            idempotency_key=key, release_registry=saved.registry, workflow=saved.release, variant_policy=selection,
            authorize=host.authorize, context_client=host, operation_client=host,
            enforcing_gateway_id="agent_runtime_module_kernel", environment_id="analyst_billie",
            adapters=adapters, artifact_host=cell, record_store=records, content_store=contents,
            claim_token_secret=b"test_only_claim_secret_value_123456", clock=lambda: _TEST_TIME)
    return run, records, contents

def _diagnostics(run, contents):
    attempt = run.attempts[-1]
    detail = next((value.body.decode() for value in contents.values.values()
                   if value.content_ref == attempt.failure_detail_ref), None)
    return {"status": attempt.status, "failure_class": attempt.failure_class, "failure_detail": detail}


@pytest.mark.parametrize("verdict", ["passed", "non_pass", "provider_failure"])
def test_prepared_selection_runs_once_and_preserves_ledger_identity(tmp_path, monkeypatch, verdict):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    prepared = prepare_local_workflow_module(root, MODULE_ID + "_review", model_id="model-a")
    _register(root, source, "v2")
    source.rename(source.with_name("unavailable_source"))
    monkeypatch.setattr(local, "load_runtime_registration", lambda *a, **k: pytest.fail("No reload at dispatch"))
    monkeypatch.setattr(local, "reviewer_execution_profile", lambda *a, **k: pytest.fail("No model reselection"))
    calls = []
    result, records, contents = _invoke(prepared, tmp_path / "run", calls, key="new_request", verdict=verdict)
    assert len(calls) == 1, _diagnostics(result, contents)
    assert result.attempts[0].status == ("failed" if verdict == "provider_failure" else "completed")
    trace = records.load_trace(result.module_run.workflow_execution_id)
    assert trace.records_of_type(WorkflowModuleExecutionVariantRecord)[0].model_id == "model-a"
    assert trace.records_of_type(WorkflowExecutionRecord)[0].workflow_release_ref == prepared[0].release.release_ref
    repeat, _, _ = _invoke(prepared, tmp_path / "repeat", calls, key="new_request",
                          records=records, contents=contents, verdict=verdict)
    assert repeat.attempts == result.attempts and len(calls) == 1


def test_changed_model_requires_new_key_and_invalid_input_never_calls_provider(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    a = prepare_local_workflow_module(root, MODULE_ID + "_review", model_id="model-a")
    b = prepare_local_workflow_module(root, MODULE_ID + "_review", model_id="model-b")
    calls = []
    first, records, contents = _invoke(a, tmp_path / "a", calls, key="same_request")
    with pytest.raises(ValueError, match="already bound"):
        _invoke(b, tmp_path / "conflict", calls, key="same_request", records=records, contents=contents)
    assert len(calls) == 1, _diagnostics(first, contents)
    from jsonschema import ValidationError
    with pytest.raises(ValidationError):
        _invoke(b, tmp_path / "invalid", calls, key="invalid_request", payload={"unexpected": True},
                records=records, contents=contents)
    assert len(calls) == 1
    second, _, _ = _invoke(b, tmp_path / "b", calls, key="new_request", records=records, contents=contents)
    assert len(calls) == 2 and first.module_run.workflow_execution_id != second.module_run.workflow_execution_id


def test_prepare_store_failure_and_wrong_readback_do_not_update_local_files(tmp_path):
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    class Store:
        def register_bundle(self, bundle):
            raise RuntimeError("store write unavailable")
        def load_release_registry(self):
            return RuntimeReleaseRegistry()
    with pytest.raises(RuntimeError, match="store write unavailable"):
        prepare_local_workflow_module(root, MODULE_ID + "_review", release_store=Store())
    class MissingReadback(Store):
        def register_bundle(self, bundle):
            self.submitted = bundle
    with pytest.raises(KeyError, match="unknown Workflow"):
        prepare_local_workflow_module(root, MODULE_ID + "_review", release_store=MissingReadback())
    assert _files(root) == before


def test_pg_new_model_and_fresh_log_query_keep_original_configuration(
    tmp_path, postgres_release_test_schema, postgres_test_schema,
):
    dsn = os.environ["AGENT_RUNTIME_TEST_DATABASE_URL"]
    releases = PostgresRuntimeReleaseStore.from_dsn(dsn, schema=postgres_release_test_schema)
    releases.create_schema(installed_at_utc=_TEST_TIME)
    ledger = PostgresRuntimeExecutionRecordStore.from_dsn(dsn, schema=postgres_test_schema)
    ledger.initialize_schema()
    source, _ = _source(tmp_path / "source")
    root = tmp_path / "host"
    _register(root, source)
    before = _files(root)
    a = prepare_local_workflow_module(root, MODULE_ID + "_review", model_id="model-a", release_store=releases)
    calls = []
    first, _, _ = _invoke(a, tmp_path / "a", calls, key="pg_request_a", records=ledger, contents=ledger)
    b = prepare_local_workflow_module(root, MODULE_ID + "_review", model_id="model-b", release_store=releases)
    _invoke(b, tmp_path / "b", calls, key="pg_request_b", records=ledger, contents=ledger)
    fresh = PostgresRuntimeReleaseStore.from_dsn(dsn, schema=postgres_release_test_schema).load_release_registry()
    query = PostgresRuntimeExecutionQueryStore.from_dsn(dsn, schema=postgres_test_schema)
    trace = query.load_trace(first.module_run.workflow_execution_id)
    start = trace.records_of_type(WorkflowExecutionRecord)[0]
    selection = fresh.get_execution_variant_policy(start.execution_profile_selection_ref, start.execution_profile_selection_sha256)
    assert selection == a[1]
    binding = selection.policy_document()["bindings"][0]
    original = fresh.get_execution_profile(binding["execution_profile_release_ref"], binding["execution_profile_release_sha256"])
    assert original.model_id == "model-a"
    assert trace.records_of_type(WorkflowModuleExecutionVariantRecord)[0].model_id == "model-a"
    item = first.outputs[0]
    assert json.loads(query.load_content(first.module_run.workflow_execution_id, item.output_ref).body)["verdict"] == "passed"
    assert len(calls) == 2 and _files(root) == before
