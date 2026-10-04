"""开放调度协议：完整时间线、恢复游标与纯规划。"""
import json
import pytest
from society0.kernel.schedule import StepPlan, SequenceSchedule, schedule_plugin
from society0.kernel.runner import RunPlan, RunContract, run_plan
from society0.kernel.runtime import Phase, runtime_plugin
from society0.kernel.interaction import interaction_plugin
from society0.kernel.plugins import Plugin


@pytest.mark.asyncio
async def test_sequence_uses_completed_identity_without_advancing():
    phase = Phase('body', lambda context: None)
    schedule = SequenceSchedule(iter((10, 20, 30)), [phase])
    assert await schedule.next_step(0) == await schedule.next_step(0) == StepPlan(10, (phase,))
    assert (await schedule.next_step(1)).time == 20
    assert await schedule.next_step(3) is None


@pytest.mark.asyncio
async def test_third_party_schedule_with_no_runtime_or_phases_restores_full_timeline(tmp_path):
    seen = []
    calls = []
    class ThirdParty:
        async def next_step(self, completed_step):
            calls.append(completed_step)
            if completed_step == 3: return None
            return StepPlan((10, 20, 30)[completed_step], (Phase('third-party', lambda ctx: seen.append(ctx.moment.time)),))
    def build():
        def install(ctx): ctx.provide('schedule', ThirdParty())
        return RunPlan([interaction_plugin(lambda *args: True),
            runtime_plugin(information=('interaction', 'information'), actions=('interaction', 'actions'), store=('storage', 'store')),
            Plugin('third-party', install=install)], RunContract({}, {}, {'schedule': 'third-party'}, {'timeline': [10,20,30]}, {}),
            schedule=('third-party', 'schedule'))
    await run_plan(tmp_path/'run', build())
    assert seen == [10,20,30] and calls == [0,1,2,3]
    seen.clear(); calls.clear()
    report = await run_plan(tmp_path/'resume', build(), source=tmp_path/'run', step=1)
    assert seen == [20,30] and calls == [1,2,3] and report['complete_step'] == 3
    manifest = json.loads((tmp_path/'resume'/'runner.json').read_text())
    assert manifest['schedule'] == ['third-party', 'schedule']
    assert manifest['effective_runtime']['capacity'] == 1
