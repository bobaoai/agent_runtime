"""Terminal failure facts and provider diagnostics survive durable execution."""

from dataclasses import fields, replace
from datetime import timedelta

import pytest

from agent_runtime.contracts.ledger_record_definition import WorkflowAttemptRecord
from agent_runtime.foundation.foundation_contract_validation import (
    parse_utc_timestamp, format_utc_timestamp,
)
from test_agent_runtime_registered_module_execution import _environment, _run, pg_stores
from test_agent_runtime_native_structured_output import _TEST_TIME


def _timed_module_setup(tmp_path, durations):
    import test_agent_runtime_native_structured_output as native
    compiled = native._compile_native_module(tmp_path, timeout_seconds=10,
        executor_adapter_id="stub_inline_executor", executor_adapter_revision="v1",
        transport_kind="in_process_test", provider_id="provider_stub")
    registry = native._register_compiled_for_evaluation(compiled)
    cell = native.InMemoryCellArtifactStore()
    prompt = native._evaluation_prompt(cell, compiled, suffix="timing")
    original = native._evaluation_request(compiled, prompt, suffix="timing")
    request = native.ModuleExecutionRequest.build(**{
        item.name: getattr(original, item.name) for item in fields(original)
        if item.name not in {"variants", "input_closure_sha256", "request_sha256"}
    }, variants=tuple(replace(original.variants[0], arm_key=f"arm_{i}") for i in range(len(durations))))
    now = [parse_utc_timestamp("fixture", _TEST_TIME)]
    ledger = native.InMemoryModuleExecutionLedger()
    starts_before_call = []
    def advance(request, host):
        starts_before_call.append(tuple(ledger._attempt_starts.values()))
        now[0] += timedelta(seconds=durations[len(starts_before_call)-1])
    adapter = native._StubInlineAdapter(release_registry=registry, artifact_host=cell, on_execute=advance)
    adapters = native.AgentExecutionAdapterRegistry()
    adapters.register(adapter)
    authority, _ = native._evaluation_authority(registry, request)
    options = dict(request=request, release_registry=registry, adapters=adapters, artifact_host=cell,
                   ledger=ledger, authority=authority, clock=lambda: format_utc_timestamp(now[0]))
    return native, options, adapter, starts_before_call


@pytest.mark.parametrize("durations,statuses", [((6, 6), ("completed", "completed")),
                                                ((6, 11), ("completed", "failed"))])
def test_sequential_variants_use_their_own_attempt_start(tmp_path, durations, statuses):
    native, options, adapter, starts = _timed_module_setup(tmp_path, durations)
    run = native.run_module(**options)
    assert tuple(a.status for a in run.attempts) == statuses
    base = parse_utc_timestamp("fixture", _TEST_TIME)
    assert [a.period_start_at_utc for a in run.attempts] == [format_utc_timestamp(base), format_utc_timestamp(base + timedelta(seconds=6))]
    assert [len(rows) for rows in starts] == [1, 2]
    for index, attempt in enumerate(run.attempts):
        assert starts[index][-1].attempt_id == attempt.attempt_id
        assert starts[index][-1].recorded_at_utc == attempt.period_start_at_utc
    if statuses[-1] == "failed":
        assert run.attempts[-1].failure_class == "timeout"
        assert not run.attempts[-1].output_refs
    assert native.run_module(**options) == run and adapter.calls == 2


def test_attempt_start_is_immutable_and_requires_a_known_run(tmp_path):
    native, options, _, _ = _timed_module_setup(tmp_path, (1,))
    run = native.run_module(**options)
    ledger = options["ledger"]
    start = ledger._attempt_starts[run.attempts[0].attempt_id]
    ledger.record_attempt_start(start)
    with pytest.raises(ValueError):
        ledger.record_attempt_start(replace(start, recorded_at_utc="2026-08-09T12:00:01.000000Z"))
    empty = native.InMemoryModuleExecutionLedger()
    with pytest.raises(ValueError):
        empty.record_attempt_start(start)
    assert empty._attempt_starts == {}
    with pytest.raises(ValueError):
        ledger.record_attempt_start(replace(start, attempt_id="attempt_after_finished_run"))


@pytest.mark.parametrize("inherit_protocol", [False, True])
def test_missing_attempt_start_interface_stops_before_run_claim(tmp_path, inherit_protocol):
    from agent_runtime.contracts.execution_module_definition import ModuleExecutionLedger
    native, options, adapter, _ = _timed_module_setup(tmp_path, (1,))
    calls = []
    inner = native.InMemoryModuleExecutionLedger()
    class OldLedger(ModuleExecutionLedger if inherit_protocol else object):
        def existing_result(self, request):
            return inner.existing_result(request)
        def begin(self, *args):
            calls.append("begin")
            return inner.begin(*args)
        def commit_attempt(self, attempt):
            return inner.commit_attempt(attempt)
        def commit_result(self, request_id, result):
            return inner.commit_result(request_id, result)
    with pytest.raises(TypeError, match="record_attempt_start"):
        options["ledger"] = OldLedger()
        native.run_module(**options)
    assert calls == [] and adapter.calls == 0


@pytest.mark.parametrize("over_budget", [False, True])
def test_postgres_attempt_clock_excludes_run_preparation(tmp_path, monkeypatch, pg_stores, over_budget):
    from agent_runtime.ledger.ledger_workflow_module_recording import WorkflowModuleLedgerRecorder
    from agent_runtime.contracts.ledger_record_definition import WorkflowAttemptStartedRecord
    env = _environment(tmp_path, pg=pg_stores)
    base = parse_utc_timestamp("fixture", _TEST_TIME)
    now = [base]
    duration = env.profile.timeout_seconds + 1 if over_budget else 6
    prepare = WorkflowModuleLedgerRecorder.record_module_start
    def delayed_prepare(self, **kwargs):
        prepare(self, **kwargs)
        now[0] += timedelta(seconds=env.profile.timeout_seconds + 5)
    monkeypatch.setattr(WorkflowModuleLedgerRecorder, "record_module_start", delayed_prepare)
    adapter = env.kwargs["adapters"].resolve(env.profile.executor_adapter_id, env.profile.executor_adapter_revision)
    invoke = adapter._invoker
    def delayed_invoke(**kwargs):
        result = invoke(**kwargs)
        now[0] += timedelta(seconds=duration)
        return result
    monkeypatch.setattr(adapter, "_invoker", delayed_invoke)
    run = _run(env, clock=lambda: format_utc_timestamp(now[0]))
    assert len(env.calls) == 1
    attempt = run.attempts[0]
    assert attempt.status == ("failed" if over_budget else "completed")
    if over_budget:
        assert attempt.failure_class == "timeout" and not run.outputs
    query = pg_stores[2]()
    trace = query.load_trace(run.module_run.workflow_execution_id)
    start = trace.records_of_type(WorkflowAttemptStartedRecord)[0]
    assert start.recorded_at_utc == format_utc_timestamp(base + timedelta(seconds=env.profile.timeout_seconds + 5))
    assert attempt.period_start_at_utc == start.recorded_at_utc
    assert start.deadline_at() == parse_utc_timestamp("start", start.recorded_at_utc) + timedelta(seconds=env.profile.timeout_seconds)
    assert trace.records_of_type(WorkflowAttemptRecord)[0].period_start_at_utc == start.recorded_at_utc
    assert query.load_content(run.module_run.workflow_execution_id, attempt.provider_trace_ref) is not None
    assert _run(env, clock=lambda: format_utc_timestamp(now[0] + timedelta(days=1))) == run
    assert len(env.calls) == 1


@pytest.mark.parametrize("resume_after_seconds", [5, 120])
def test_postgres_existing_attempt_start_never_refreshes_budget(tmp_path, monkeypatch, pg_stores, resume_after_seconds):
    import test_agent_runtime_native_structured_output as native
    from agent_runtime.contracts.ledger_record_definition import WorkflowAttemptStartedRecord
    options, binding, adapter = native._workflow_gateway_setup(tmp_path,
        record_store=pg_stores[1], content_store=pg_stores[1])
    class InterruptedBeforeAuthorization(BaseException):
        pass
    recorder = native.WorkflowModuleLedgerRecorder(binding)
    begin = recorder.begin_attempt
    def stop_after_begin(**kwargs):
        begin(**kwargs)
        raise InterruptedBeforeAuthorization
    monkeypatch.setattr(recorder, "begin_attempt", stop_after_begin)
    with pytest.raises(InterruptedBeforeAuthorization):
        native.run_workflow_module(**options, ledger=native.InMemoryModuleExecutionLedger(),
            workflow_ledger=recorder, clock=lambda: _TEST_TIME)
    query = pg_stores[2]()
    execution_id = options["request"].workflow_execution_id
    original = query.load_trace(execution_id).records_of_type(WorkflowAttemptStartedRecord)[0]
    assert adapter.calls == 0
    later = format_utc_timestamp(parse_utc_timestamp("fixture", _TEST_TIME) + timedelta(seconds=resume_after_seconds))
    call = dict(**options, ledger=native.InMemoryModuleExecutionLedger(),
                workflow_ledger=native.WorkflowModuleLedgerRecorder(binding), clock=lambda: later)
    if resume_after_seconds > original.timeout_seconds:
        with pytest.raises(PermissionError, match="after Attempt deadline"):
            native.run_workflow_module(**call)
        assert adapter.calls == 0
    else:
        result = native.run_workflow_module(**call)
        assert result.attempts[0].status == "completed"
        assert result.attempts[0].period_start_at_utc == original.recorded_at_utc
        assert result.attempts[0].period_end_at_utc == later
        replay = native.run_workflow_module(**options, ledger=native.InMemoryModuleExecutionLedger(),
            workflow_ledger=native.WorkflowModuleLedgerRecorder(binding), clock=lambda: "2026-08-10T12:00:00Z")
        assert replay == result and adapter.calls == 1
    assert query.load_trace(execution_id).records_of_type(WorkflowAttemptStartedRecord)[0] == original


def _run_rejected_gateway(tmp_path, monkeypatch, rejection, *, store=None):
    import test_agent_runtime_native_structured_output as native
    from agent_runtime.contracts.ledger_record_definition import LegacyModuleCapabilityGrant, ToolCallRecord
    options, binding, adapter = native._workflow_gateway_setup(tmp_path, record_store=store, content_store=store)
    execute = adapter.execute
    def reject_result(request, host):
        if rejection in {"duplicate_observation", "missing_first_observation"}:
            def two_calls(request, host):
                first = native._gateway_tool_callback(options["artifact_host"], [],
                    resource_id="resource_a", tool_call_id="call_a")(request, host)
                second = native._gateway_tool_callback(options["artifact_host"], [],
                    resource_id="resource_b", tool_call_id="call_a" if rejection == "duplicate_observation" else "call_b")(request, host)
                return second if rejection == "missing_first_observation" else (*first, *second)
            adapter._on_execute = two_calls
        if rejection == "output_schema":
            adapter._payload = b'{"value":44}'
        result = execute(request, host)
        return replace(result, **{
            "identity": {"provider_id": "wrong_provider"},
            "output_schema": {},
            "missing_observation": {"tool_observations": (), "tool_operation_ref_ids": ()},
            "unresolvable_trace": {"cell_local_trace_ref": "missing:trace"},
            "invalid_usage": {"input_tokens": True},
            "duplicate_observation": {},
            "missing_first_observation": {},
            "invalid_tool_id": {"tool_observations": (replace(result.tool_observations[0], tool_call_id=[]),)},
        }[rejection])
    monkeypatch.setattr(adapter, "execute", reject_result)
    ledger = native.InMemoryModuleExecutionLedger()
    run = native.run_workflow_module(**options, ledger=ledger,
        workflow_ledger=native.WorkflowModuleLedgerRecorder(binding), clock=lambda: _TEST_TIME)
    attempt = run.attempts[0]
    assert attempt.status == "failed" and not run.outputs
    uncertain_calls = rejection in {"missing_observation", "duplicate_observation", "missing_first_observation", "invalid_tool_id"}
    assert len(attempt.tool_calls) == (0 if uncertain_calls else 1)
    assert attempt.usage.input_tokens == (None if rejection == "invalid_usage" else 3)
    assert (attempt.provider_trace_ref is None) == (rejection == "unresolvable_trace")
    trace = binding.record_store.load_trace(run.module_run.workflow_execution_id)
    assert len(trace.records_of_type(LegacyModuleCapabilityGrant)) == (3 if rejection in {"duplicate_observation", "missing_first_observation"} else 2)
    assert len(trace.records_of_type(ToolCallRecord)) == len(attempt.tool_calls)
    assert trace.records_of_type(WorkflowAttemptRecord)[0].status == "failed"
    replay = native.run_workflow_module(**options, ledger=native.InMemoryModuleExecutionLedger(),
        workflow_ledger=native.WorkflowModuleLedgerRecorder(binding), clock=lambda: _TEST_TIME)
    assert replay == run and adapter.calls == 1
    return run, options["artifact_host"]


@pytest.mark.parametrize("rejection", ["identity", "output_schema", "missing_observation", "unresolvable_trace", "invalid_usage", "duplicate_observation", "missing_first_observation", "invalid_tool_id"])
def test_kernel_rejection_preserves_verified_facts_and_finalizes(tmp_path, monkeypatch, rejection):
    _run_rejected_gateway(tmp_path, monkeypatch, rejection)


@pytest.mark.parametrize("rejection", ["identity", "missing_observation", "missing_first_observation"])
def test_postgres_rejected_gateway_closes_and_round_trips(tmp_path, monkeypatch, pg_stores, rejection):
    run, cell = _run_rejected_gateway(tmp_path, monkeypatch, rejection, store=pg_stores[1])
    query = pg_stores[2]()
    execution_id = run.module_run.workflow_execution_id
    terminal = query.load_trace(execution_id).records_of_type(WorkflowAttemptRecord)[0]
    assert terminal.status == "failed"
    for prefix in ("provider_trace", "failure_detail"):
        ref, digest = getattr(terminal, prefix + "_ref"), getattr(terminal, prefix + "_sha256")
        body = query.load_content(execution_id, ref)
        assert body.content_sha256 == digest
        assert body.body == cell.read_bytes(ref, digest)


@pytest.mark.parametrize("provider_error", [False, True])
def test_after_deadline_is_recorded_as_failure_without_output(tmp_path, provider_error):
    env = _environment(tmp_path)
    env.state["provider_error"] = provider_error
    late = format_utc_timestamp(parse_utc_timestamp("start", _TEST_TIME)
                                + timedelta(seconds=env.profile.timeout_seconds + 1))
    result = _run(env, clock=lambda: late if env.calls else _TEST_TIME)
    attempt = result.attempts[0]
    assert attempt.status == "failed"
    assert result.outputs == ()
    assert attempt.period_end_at_utc == late
    trace = env.store.load_trace(result.module_run.workflow_execution_id)
    terminal = trace.records_of_type(WorkflowAttemptRecord)[0]
    assert terminal.status == "failed"
    assert terminal.provider_trace_ref == attempt.provider_trace_ref
    assert terminal.failure_detail_ref == attempt.failure_detail_ref
    assert (trace.workflow_execution_id, terminal.provider_trace_ref) in env.contents.values
    assert (trace.workflow_execution_id, terminal.failure_detail_ref) in env.contents.values
    replay = _run(env, clock=lambda: late)
    assert replay.attempts[0] == attempt
    assert len(env.calls) == 1


def test_old_attempt_payload_does_not_gain_empty_evidence_fields(tmp_path):
    env = _environment(tmp_path)
    result = _run(env)
    record = env.store.load_trace(result.module_run.workflow_execution_id).records_of_type(WorkflowAttemptRecord)[0]
    old = replace(record, provider_trace_ref=None, provider_trace_sha256=None,
                  failure_detail_ref=None, failure_detail_sha256=None)
    payload = old.as_dict()
    assert not {"provider_trace_ref", "provider_trace_sha256", "failure_detail_ref",
                "failure_detail_sha256"}.intersection(payload)
    assert WorkflowAttemptRecord(**payload).as_dict() == payload


@pytest.mark.parametrize("provider_error", [False, True])
def test_postgres_terminal_evidence_is_readable_from_fresh_query(tmp_path, pg_stores, provider_error):
    env = _environment(tmp_path, pg=pg_stores)
    env.state["provider_error"] = provider_error
    result = _run(env)
    execution_id = result.module_run.workflow_execution_id
    query = env.query()
    terminal = query.load_trace(execution_id).records_of_type(WorkflowAttemptRecord)[0]
    evidence = query.load_content(execution_id, terminal.provider_trace_ref)
    assert evidence is not None
    assert evidence.content_sha256 == terminal.provider_trace_sha256
    assert evidence.body == env.cell.read_bytes(terminal.provider_trace_ref, terminal.provider_trace_sha256)
    if provider_error:
        failure = query.load_content(execution_id, terminal.failure_detail_ref)
        assert failure is not None
        assert failure.content_sha256 == terminal.failure_detail_sha256
    assert _run(env).attempts[0].provider_trace_ref == terminal.provider_trace_ref
    assert len(env.calls) == 1
