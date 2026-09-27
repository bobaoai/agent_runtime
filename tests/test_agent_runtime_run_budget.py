"""Synchronous budget rules and actual process deadline enforcement."""
import json
import os
from pathlib import Path
import sys
import time

import pytest

from agent_runtime.execution.execution_parameter_resolution import resolve_execution_parameters
from agent_runtime.execution.execution_run_budget import ParentRunContext, RunBudget
from agent_runtime.invocation.invocation_process_execution import CliProcessTimeout, run_cli_process


@pytest.mark.deterministic
@pytest.mark.parametrize('value', [True, False, 0, -1, 86401, 0.5, '3600'])
def test_invalid_budget_cannot_enter_profile_preparation(tmp_path, value):
    with pytest.raises(ValueError):
        resolve_execution_parameters(tmp_path, 'review', run_timeout_seconds=value)


@pytest.mark.deterministic
def test_timeout_resolution_uses_all_four_layers_without_mutating_files(tmp_path):
    workspace = tmp_path/'.runtime/execution_parameters/workspace.json'
    workflow = workspace.parent/'workflows/review.json'
    workflow.parent.mkdir(parents=True)
    assert resolve_execution_parameters(tmp_path, 'review').run_timeout_seconds == 1200
    workspace.write_text(json.dumps({'schema_version':'runtime_execution_parameters_v2','run_timeout_seconds':1800}))
    assert resolve_execution_parameters(tmp_path, 'review').run_timeout_seconds == 1800
    workflow.write_text(json.dumps({'schema_version':'runtime_execution_parameters_v2','run_timeout_seconds':2400}))
    before = (workspace.read_bytes(), workflow.read_bytes())
    assert resolve_execution_parameters(tmp_path, 'review').run_timeout_seconds == 2400
    call = resolve_execution_parameters(tmp_path, 'review', run_timeout_seconds=3600)
    assert call.run_timeout_seconds == 3600
    assert call.source('run_timeout_seconds').layer == 'call'
    assert before == (workspace.read_bytes(), workflow.read_bytes())
    assert resolve_execution_parameters(tmp_path, 'review').run_timeout_seconds == 2400


@pytest.mark.deterministic
@pytest.mark.parametrize('payload', [
    {'schema_version':'runtime_execution_parameters_v1','run_timeout_seconds':3600},
    {'schema_version':'runtime_execution_parameters_v2','run_timeout_seconds':None},
    {'schema_version':'runtime_execution_parameters_v2','run_timeout_seconds':True},
    {'schema_version':'runtime_execution_parameters_v2','run_timeout_seconds':1.5},
    {'schema_version':'runtime_execution_parameters_v2','unknown_budget':20},
])
def test_parameter_file_does_not_ignore_unsupported_or_null_budget(tmp_path, payload):
    file=tmp_path/'.runtime/execution_parameters/workspace.json';file.parent.mkdir(parents=True)
    file.write_text(json.dumps(payload))
    with pytest.raises(ValueError): resolve_execution_parameters(tmp_path,'review')


@pytest.mark.deterministic
def test_parent_remaining_time_bounds_child_and_parallel_siblings():
    now=[10.0];clock=lambda:now[0]
    parent=RunBudget(1200,started=0.0,clock=clock)
    context=parent.bind_parent(attempt_id='parent_attempt',active=lambda:None,cancelled=lambda:False)
    now[0]=1080.25
    first=RunBudget(3600,started=now[0],parent_run=context,clock=clock)
    second=RunBudget(3600,started=now[0],parent_run=context,clock=clock)
    assert first.deadline==second.deadline==1200.0
    assert first.as_record()['effective_timeout_seconds']==119.75
    assert first.as_record()['limiting_scope']=='parent'
    assert first.as_record()['parent_attempt_id']=='parent_attempt'
    first.close(); parent.require_active();second.require_active()
    now[0]=1200.0
    with pytest.raises(TimeoutError):second.require_active()
    with pytest.raises(TimeoutError):RunBudget(3600,started=now[0],parent_run=context,clock=clock)


@pytest.mark.deterministic
def test_child_shorter_budget_does_not_stop_parent_and_no_arbitrary_parent():
    now=[10.0];clock=lambda:now[0]
    parent=RunBudget(1200,started=0.0,clock=clock)
    context=parent.bind_parent(attempt_id='parent',active=lambda:None,cancelled=lambda:False)
    child=RunBudget(10,started=10.0,parent_run=context,clock=clock)
    assert child.as_record()['limiting_scope']=='local'
    now[0]=21.0
    with pytest.raises(TimeoutError):child.require_active()
    parent.require_active()
    with pytest.raises(TypeError):RunBudget(10,started=21,parent_run={'deadline':100})
    parent.close()
    with pytest.raises(PermissionError):context.require_active()


@pytest.mark.deterministic
def test_preparation_consumes_budget_and_observation_does_not_serialize_clock():
    budget=RunBudget(1,started=10.0,clock=lambda:11.5)
    with pytest.raises(TimeoutError):budget.require_active()
    record=budget.as_record()
    assert record['elapsed_seconds']==1.5
    assert set(record)=={'requested_timeout_seconds','effective_timeout_seconds','elapsed_seconds','limiting_scope','parent_attempt_id'}
    json.dumps(record,allow_nan=False)


_REAL=pytest.mark.skipif(os.environ.get('AGENT_RUNTIME_REAL_RUN')!='1',reason='real process budget check requires AGENT_RUNTIME_REAL_RUN=1')


@pytest.mark.real_run
@_REAL
def test_actual_process_is_stopped_by_shorter_enclosing_deadline(tmp_path):
    started=time.monotonic()
    with pytest.raises(CliProcessTimeout) as caught:
        run_cli_process(argv=[sys.executable,'-I','-c','import time;print("started",flush=True);time.sleep(20)'],
            prompt='',cwd=tmp_path,timeout_seconds=20,environment={'PATH':'/usr/bin:/bin'},deadline_monotonic=started+1)
    assert 'started' in caught.value.stdout
    assert caught.value.stop_reason=='timeout'
    assert time.monotonic()-started<10


@pytest.mark.real_run
@_REAL
def test_expired_deadline_never_launches_actual_command(tmp_path):
    marker=tmp_path/'unexpected'
    with pytest.raises(CliProcessTimeout):
        run_cli_process(argv=[sys.executable,'-I','-c',f'from pathlib import Path;Path({str(marker)!r}).write_text("started")'],
            prompt='',cwd=tmp_path,timeout_seconds=20,environment={},deadline_monotonic=time.monotonic()-1)
    assert not marker.exists()

@pytest.mark.real_run
@_REAL
def test_actual_codex_run_uses_one_hour_selection_without_re_registering(tmp_path):
    from agent_runtime import run_local_workflow_test, prepare_local_workflow_module
    from test_agent_runtime_execution_parameters import _module_root, _profile
    root=_module_root(tmp_path)
    selection=dict(transport_kind='codex_cli',model_id='gpt-6-astra',reasoning_profile='xhigh')
    before={str(p.relative_to(root)):p.read_bytes() for p in (root/'.runtime/module').rglob('*.json')}
    saved,variant=prepare_local_workflow_module(root,'summarize_note',run_timeout_seconds=3600,**selection)
    assert _profile(saved,variant).timeout_seconds==3600
    cli=os.environ.get('AGENT_RUNTIME_CODEX_BIN')
    assert cli, 'AGENT_RUNTIME_CODEX_BIN must identify the real installed Codex executable'
    record=run_local_workflow_test(root,'summarize_note',input_payload={},cli_path=cli,run_timeout_seconds=3600,**selection)
    assert record['status']=='completed',record.get('failure_detail')
    assert record['execution_parameter_sources']['run_timeout_seconds']['layer']=='call'
    assert record['execution_budget']['requested_timeout_seconds']==3600
    assert record['execution_budget']['effective_timeout_seconds']==3600
    assert record['provider_trace']['timeout_seconds']==3600
    assert before=={str(p.relative_to(root)):p.read_bytes() for p in (root/'.runtime/module').rglob('*.json')}
    saved,variant=prepare_local_workflow_module(root,'summarize_note',**selection)
    assert _profile(saved,variant).timeout_seconds==1200
    result_dir=os.environ.get('AGENT_RUNTIME_BUDGET_EVIDENCE_DIR')
    if result_dir:
        target=Path(result_dir)/'one_hour_codex_record.json'
        with target.open('x') as stream: json.dump(record,stream,ensure_ascii=False,indent=2,allow_nan=False)


@pytest.mark.real_run
@_REAL
def test_actual_managed_child_uses_parent_remaining_budget(tmp_path):
    from agent_runtime.testing.conformance_agent_execution import run_agent_example
    root=tmp_path/'root'
    base=root/'.runtime/execution_parameters'
    (base/'workflows').mkdir(parents=True)
    (base/'workspace.json').write_text(json.dumps({'schema_version':'runtime_execution_parameters_v2',
        'transport_kind':'claude_cli','model_id':'claude-sonnet-5','reasoning_profile':'low','run_timeout_seconds':180}))
    (base/'workflows/capability_task_reviewer.json').write_text(json.dumps({
        'schema_version':'runtime_execution_parameters_v2','run_timeout_seconds':3600}))
    result=run_agent_example(root,'agent_evaluation_example',cli_path=os.environ.get('AGENT_RUNTIME_CLAUDE_BIN'))
    result_dir=os.environ.get('AGENT_RUNTIME_BUDGET_EVIDENCE_DIR')
    if result_dir:
        with (Path(result_dir)/'managed_child_record.json').open('x') as stream:
            json.dump(result,stream,ensure_ascii=False,indent=2,allow_nan=False)
    assert result['status']=='completed',result.get('failure')
    assert result['child_executions'], 'the actual model must call the registered child Reviewer'
    parent=next(row for row in result['execution']['nodes'] if row['dispatch']['current_state_id']=='tested_agent')
    for row in result['child_executions']:
        budget=row['record']['execution_budget']
        assert row['record']['status']=='completed'
        assert budget['requested_timeout_seconds']==3600
        assert 0<budget['effective_timeout_seconds']<180
        assert budget['limiting_scope']=='parent'
        assert budget['parent_attempt_id']==parent['attempt_id']
    assert result['resources_cleaned']

@pytest.mark.real_run
@_REAL
def test_real_process_is_reaped_when_ownership_discovery_is_denied(tmp_path, monkeypatch):
    """Actual process lifecycle with an explicitly injected metadata-access failure.

    This is failure-injection evidence, not proof that a particular OS sandbox
    denies process inspection. No Provider response or process is substituted.
    """
    import psutil
    from agent_runtime.invocation import invocation_process_execution as processes
    def denied(process):
        raise psutil.AccessDenied(process.pid)
    monkeypatch.setattr(processes, '_remember_descendants', denied)
    with pytest.raises(processes.CliProcessError) as caught:
        run_cli_process(argv=[sys.executable,'-c','import time;time.sleep(30)'],prompt='',cwd=tmp_path,
                        timeout_seconds=30,environment={'PATH':'/usr/bin:/bin'})
    assert caught.value.returncode == -9
    assert caught.value.cleanup_error and 'ownership' in caught.value.cleanup_error


@pytest.mark.deterministic
def test_invalid_parent_is_rejected_before_root_setup(tmp_path):
    from agent_runtime import run_local_workflow_test
    root=tmp_path/'host'
    with pytest.raises(TypeError,match='parent_run'):
        run_local_workflow_test(root,'review',input_payload={},parent_run={'deadline':3600})
    assert not root.exists()
    closed=RunBudget(3600,started=time.monotonic())
    parent=closed.bind_parent(attempt_id='closed_attempt',active=lambda:None,cancelled=lambda:False)
    closed.close()
    with pytest.raises(PermissionError):
        run_local_workflow_test(root,'review',input_payload={},parent_run=parent)
    assert not root.exists()
