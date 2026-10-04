"""最终候选的并行作用域与完整发布独立复审。"""

import asyncio

import pytest

from society0.kernel.interaction import Action, ActionResult, Actions, Information, Ref
from society0.kernel.runtime import Actor, DriverResult, Phase, Runtime
from society0.kernel.storage import StageStore


@pytest.mark.asyncio
async def test_parallel_caught_action_fault_stops_other_actor_and_restore(tmp_path):
    ready = asyncio.Event()
    failed = asyncio.Event()
    target = Ref('world', 'fact', 'one')
    with StageStore.create(tmp_path / 'run', ['CREATE TABLE facts(id INTEGER PRIMARY KEY)']) as store:
        actions = Actions(lambda *args: True)

        def fail(scope, target, arguments):
            store.transaction(lambda writer: writer.execute('INSERT INTO facts VALUES(1)'))
            failed.set()
            raise ValueError('domain fault after write')

        def write(scope, target, arguments):
            store.transaction(lambda writer: writer.execute('INSERT INTO facts VALUES(2)'))
            return ActionResult('completed')

        actions.register(Action('fail', ('world', 'fact'), '', {'type': 'object'}, fail))
        actions.register(Action('write', ('world', 'fact'), '', {'type': 'object'}, write))

        class Driver:
            async def run(self, session):
                if session.actor.id == 'a':
                    await ready.wait()
                    with pytest.raises(ValueError):
                        await session.actions.invoke('fail', target, {})
                else:
                    ready.set()
                    await failed.wait()
                    with pytest.raises(ValueError):
                        await session.actions.invoke('write', target, {})
                return DriverResult('completed')

        runtime = Runtime([Actor(name, Driver()) for name in ('a', 'b')],
                          information=Information(lambda *args: True), actions=actions,
                          store=store, capacity=2)

        async def phase(context):
            context.activate('a')
            context.activate('b')
            await context.drain()

        with pytest.raises(ValueError, match='domain fault'):
            await runtime.run_step(1, 1, [Phase('act', phase, execution='independent')])
        assert store.complete_step == 0

    with StageStore.restore(tmp_path / 'run', tmp_path / 'restored') as restored:
        assert restored.read(lambda view: view.query('SELECT id FROM facts')) == []
