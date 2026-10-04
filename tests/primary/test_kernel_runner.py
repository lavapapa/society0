"""正式入口复用唯一运行调度与完整点，诊断故障不改发布事实。"""
import json
import pytest
from society0.kernel.runner import RunContract, RunPlan, run_plan
from society0.kernel.interaction import interaction_plugin
from society0.kernel.runtime import Phase, runtime_plugin
from society0.kernel.schedule import schedule_plugin
from society0.kernel.results import StepResult, results_plugin


def plan(seen,*,fail=False):
    def phase(ctx):
        seen.append(ctx.moment.time)
        if fail and len(seen)==2:raise ValueError('domain failure')
        return StepResult(metrics={'time':ctx.moment.time})
    return RunPlan(plugins=[interaction_plugin(lambda *args:True),results_plugin(),
        runtime_plugin(information=('interaction','information'),actions=('interaction','actions'),store=('storage','store'),results=('results','results')),
        schedule_plugin([Phase('environment',phase)])],moments=(10,20),contract=RunContract(
        release={'commit':'explicit-test-release'},dependencies={'python':'test'},
        configuration={'plugins':{},'actors':{},'models':{}},time={'start':10,'end':20},budgets={'max_activations':0}))


@pytest.mark.asyncio
async def test_runner_freezes_manifest_and_timings_and_restores(tmp_path):
    seen=[]
    result=await run_plan(tmp_path/'run',plan(seen))
    assert result['complete_step']==2 and result['status']=='completed' and seen==[10,20]
    manifest=json.loads((tmp_path/'run'/'runner.json').read_text())
    assert manifest['run_id']==result['run_id']
    assert manifest['contract']['release']=={'commit':'explicit-test-release'}
    assert manifest['effective_schedule']['capacity']==1
    assert manifest['effective_schedule']['phases'][0]['name']=='environment'
    assert [p['name'] for p in manifest['plugins']]==['interaction','results','runtime','schedule']
    timings=[json.loads(line) for line in (tmp_path/'run'/'timings.jsonl').read_text().splitlines()]
    assert [row['step'] for row in timings]==[1,2] and all(row['complete_step']==row['step'] for row in timings)
    resumed=await run_plan(tmp_path/'resumed',plan([]),source=tmp_path/'run',step=1)
    assert resumed['run_id']!=result['run_id'] and resumed['complete_step']==3


@pytest.mark.asyncio
async def test_manifest_failure_precedes_first_simulation_step(tmp_path,monkeypatch):
    import society0.kernel.runner as module
    def fail(*args):raise OSError('manifest unavailable')
    monkeypatch.setattr(module,'_write_manifest',fail)
    seen=[]
    with pytest.raises(OSError,match='manifest unavailable'):await run_plan(tmp_path/'run',plan(seen))
    assert seen==[]


@pytest.mark.asyncio
async def test_timing_failure_keeps_complete_and_exposes_diagnostic(tmp_path,monkeypatch):
    import society0.kernel.runner as module
    def fail(*args):raise OSError('timing unavailable')
    monkeypatch.setattr(module,'_append_timing',fail)
    result=await run_plan(tmp_path/'run',plan([]))
    assert result['status']=='completed' and result['complete_step']==2
    assert result['diagnostic_errors']==2
    saved=json.loads((tmp_path/'run'/'runner-status.json').read_text())
    assert saved.pop('updated_at')>0 and saved==result


@pytest.mark.asyncio
async def test_failed_step_keeps_last_complete_and_timing(tmp_path):
    from society0.kernel.observation import Observation
    with pytest.raises(ValueError,match='domain failure'):await run_plan(tmp_path/'run',plan([],fail=True))
    result=json.loads((tmp_path/'run'/'runner-status.json').read_text())
    assert result['status']=='failed' and result['complete_step']==1
    assert result['error']=='ValueError'
    assert Observation(tmp_path/'run').status()['complete']['step']==1
    timings=[json.loads(line) for line in (tmp_path/'run'/'timings.jsonl').read_text().splitlines()]
    assert timings[-1]['failed'] and timings[-1]['complete_step']==1


def test_cli_runs_rule_plan_without_optional_dependencies(tmp_path):
    import os, subprocess, sys
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'release':{'commit':'explicit-cli-release'},'start':1,'end':2}))
    result=subprocess.run([sys.executable,'-m','society0.kernel.runner','--factory','examples.core_next.rule_run:build',
        '--config',str(config),'--output',str(tmp_path/'run')],env={**os.environ,'PYTHONPATH':'src:.'},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['complete_step']==2 and report['status']=='completed'
    assert (tmp_path/'run'/'runner.json').exists()
