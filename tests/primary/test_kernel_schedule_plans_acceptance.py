"""FixedStep 与 PhasedSchedule 经正式 RunPlan 运行及完整点恢复。"""
import json

import pytest

from society0.kernel.interaction import interaction_plugin
from society0.kernel.observation import Observation
from society0.kernel.plugins import Plugin
from society0.kernel.results import StepResult, results_plugin
from society0.kernel.runner import RunContract, RunPlan, run_plan
from society0.kernel.runtime import Phase, runtime_plugin
from society0.kernel.schedule import FixedStep, PhasedSchedule, schedule_plugin
from society0.kernel.storage import StageStore


def build_plan(kind, moments, seen, initialized, *, fail=None):
    services = {}
    def initialize(writer):
        initialized.append('initialized')
        writer.execute('INSERT INTO business VALUES(1,0)')
    def install(context): services['store'] = context.require('storage', 'store')
    business = Plugin('business', ('storage',), install,
                      schema=('CREATE TABLE business(id INTEGER PRIMARY KEY,n INTEGER NOT NULL)',),
                      initialize=initialize)
    def phase(name):
        def prepare(context):
            store = services['store']
            value = store.read(lambda view: view.query('SELECT n FROM business')[0][0])
            seen.append(('prepare', name, context.moment.time, value, store.complete_step))
            return value
        def run(context):
            store = services['store']
            assert context.prepared == store.read(lambda view: view.query('SELECT n FROM business')[0][0])
            seen.append(('run', name, context.moment.time, context.prepared, store.complete_step))
            store.transaction(lambda writer: writer.execute('UPDATE business SET n=n+1'))
            if fail == (name, context.moment.time): raise ValueError('planned phase failure')
            return StepResult(metrics={'previous': context.prepared})
        return Phase(name, run, prepare)
    if kind == 'fixed':
        single = phase('tick')
        schedule = FixedStep(single.run, name=single.name, prepare=single.prepare)
    else: schedule = PhasedSchedule((phase('left'), phase('right')))
    return RunPlan(plugins=[business, interaction_plugin(lambda *args: True), results_plugin(),
        runtime_plugin(information=('interaction', 'information'), actions=('interaction', 'actions'),
                       store=('storage', 'store'), results=('results', 'results')),
        schedule_plugin(schedule)], moments=iter(moments), contract=RunContract(
            release={'commit': 'explicit-schedule-test'}, dependencies={'python': 'test'},
            configuration={'schedule': kind}, time={'moments': list(moments)}, budgets={'max_activations': 0}))


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,names', [('fixed', ('tick',)), ('phased', ('left', 'right'))])
async def test_public_schedule_order_complete_boundaries_and_selected_restore(tmp_path, kind, names):
    seen, initialized = [], []
    result = await run_plan(tmp_path / 'run', build_plan(kind, (10, 20), seen, initialized))
    expected = []
    value = 0
    for complete, moment in enumerate((10, 20)):
        for name in names:
            expected.extend((('prepare', name, moment, value, complete), ('run', name, moment, value, complete)))
            value += 1
    assert seen == expected and result['complete_step'] == 2 and result['status'] == 'completed'
    manifest = json.loads((tmp_path / 'run' / 'runner.json').read_text())
    assert [phase['name'] for phase in manifest['effective_schedule']['phases']] == list(names)
    with Observation(tmp_path / 'run') as observation:
        assert observation.status()['complete']['step'] == 2
    resumed_seen = []
    resumed = await run_plan(tmp_path / 'restored', build_plan(kind, (30,), resumed_seen, initialized),
                             source=tmp_path / 'run', step=1)
    assert resumed['complete_step'] == 2 and resumed['run_id'] != result['run_id']
    assert initialized == ['initialized']
    assert resumed_seen[0] == ('prepare', names[0], 30, len(names), 1)
    with StageStore.open(tmp_path / 'restored') as store:
        assert store.read(lambda view: view.query('SELECT n FROM business')) == [(2 * len(names),)]
    timings = [json.loads(line) for line in (tmp_path / 'restored' / 'timings.jsonl').read_text().splitlines()]
    assert len(timings) == 1 and timings[0]['step'] == timings[0]['complete_step'] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,last', [('fixed', 'tick'), ('phased', 'right')])
async def test_public_schedule_failure_does_not_publish_and_recovery_uses_complete_state(tmp_path, kind, last):
    seen, initialized = [], []
    with pytest.raises(ValueError, match='planned phase failure'):
        await run_plan(tmp_path / 'failed', build_plan(kind, (10, 20), seen, initialized, fail=(last, 20)))
    with Observation(tmp_path / 'failed') as observation:
        status = observation.status()
        assert status['complete']['step'] == 1 and status['failed']
    assert not (tmp_path / 'failed' / 'steps' / f'{2:020d}.json').exists()
    report = json.loads((tmp_path / 'failed' / 'runner-status.json').read_text())
    assert report['complete_step'] == 1 and report['status'] == 'failed' and report['error'] == 'ValueError'
    recovered_seen = []
    recovered = await run_plan(tmp_path / 'recovered', build_plan(kind, (20,), recovered_seen, initialized),
                               source=tmp_path / 'failed')
    count = 1 if kind == 'fixed' else 2
    assert recovered_seen[0] == ('prepare', 'tick' if kind == 'fixed' else 'left', 20, count, 1)
    assert recovered['complete_step'] == 2 and recovered['status'] == 'completed'
    assert initialized == ['initialized']
    with StageStore.open(tmp_path / 'recovered') as store:
        assert store.read(lambda view: view.query('SELECT n FROM business')) == [(2 * count,)]
