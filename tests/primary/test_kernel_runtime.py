import asyncio
from types import SimpleNamespace

import pytest

from society0.kernel import Plugin, PluginHost
from society0.kernel.interaction import Actions, Action, ActionResult, Information, Moment, Ref, ScopeClosed
from society0.kernel.runtime import Actor, DriverResult, Phase, Runtime, runtime_plugin


class Store:
    def __init__(self):
        self.events = []
        self.complete_step = 0

    def complete(self, step):
        self.events.append(('complete',))
        self.complete_step = step
        return 'published'

    def abort_step(self):
        self.events.append(('abort',))


def runtime(actors, **options):
    store = Store()
    return Runtime(actors, information=Information(lambda *args: True),
                   actions=Actions(lambda *args: True), store=store, **options), store


def actor(name, run):
    return Actor(name, SimpleNamespace(run=run), state_ref=Ref('subjective', 'state', name), config={'name': name})


def test_plugin_composition_two_mechanisms_serial_live_and_closed_scopes():
    async def main():
        actions = Actions(lambda *args: True)
        info = Information(lambda *args: True)
        store = Store()
        values, sessions = [], []

        def mechanism(name):
            def install(ctx):
                def effect(scope, target, args):
                    values.append((name, scope.actor, len(values)))
                    return ActionResult('completed')
                ctx.require('interaction', 'actions').register(Action(name, (name, 'entity'), '', {}, effect))
            return Plugin(name, ('interaction',), install)

        async def run(session):
            sessions.append(session)
            await session.actions.invoke(session.actor.id, Ref(session.actor.id, 'entity', 'one'), {})
            return DriverResult('completed')

        async def phase(ctx):
            ctx.activate('left')
            ctx.activate('right')

        def install_interaction(ctx):
            ctx.provide('information', info)
            ctx.provide('actions', actions)

        async with PluginHost([
            Plugin('interaction', install=install_interaction),
            Plugin('storage', install=lambda ctx: ctx.provide('store', store)),
            mechanism('left'), mechanism('right'),
            runtime_plugin([actor('left', run), actor('right', run)],
                           information=('interaction', 'information'), actions=('interaction', 'actions'),
                           store=('storage', 'store')),
        ]) as host:
            rt = host.service('runtime', 'runtime')
            assert await rt.run_step(1, 12, [Phase('decide', phase)]) == 'published'
            assert rt.last_completed == 1
        assert values == [('left', 'left', 0), ('right', 'right', 1)]
        assert store.events == [('complete',)]
        with pytest.raises(ScopeClosed):
            await sessions[0].actions.find(Ref('left', 'entity', 'one'))
        with pytest.raises(RuntimeError):
            await rt.run_step(2, 13, [])
    asyncio.run(main())


def test_signal_merge_followup_mutual_exclusion_and_cursor_retention():
    async def main():
        seen, scopes = [], []
        active = 0
        async def run(session):
            nonlocal active
            active += 1
            assert active == 1
            scopes.append(session.scope)
            seen.append((session.signals, session.cursors.get('position', 0), session.moment))
            session.cursors['position'] = len(seen)
            if len(seen) == 1:
                session.activate('a', 'next1')
                session.activate('a', 'next2')
            await asyncio.sleep(0)
            active -= 1
            return DriverResult('waiting' if len(seen) == 1 else 'completed')
        rt, store = runtime([actor('a', run)], capacity=3)
        def phase(ctx):
            ctx.activate('a', 'first')
            ctx.activate('a', 'second')
        await rt.run_step(1, 4, [Phase('decide', phase, execution='independent')])
        assert [(v[0], v[1]) for v in seen] == [(('first', 'second'), 0), (('next1', 'next2'), 1)]
        assert all(v[2] == Moment(4, 'decide') for v in seen)
        assert scopes[0] is not scopes[1]
        assert store.events[-1] == ('complete',)
    asyncio.run(main())


def test_independent_refills_capacity_and_shares_one_prepared_reference():
    async def main():
        slow = asyncio.Event()
        third = asyncio.Event()
        shared = ('immutable', 42)
        prepared_calls, seen = [], []
        async def run(session):
            assert session.prepared is shared
            seen.append(session.actor.id)
            if session.actor.id == 'a':
                await slow.wait()
            if session.actor.id == 'c':
                third.set()
            return DriverResult('completed')
        rt, _ = runtime([actor(n, run) for n in 'abc'], capacity=2)
        def prepare(ctx):
            prepared_calls.append(ctx.moment)
            return shared
        async def phase(ctx):
            for name in 'abc':
                ctx.activate(name)
            await asyncio.wait_for(third.wait(), 1)
            assert not slow.is_set()
            slow.set()
        await rt.run_step(1, 'today', [Phase('decision', phase, prepare=prepare, execution='independent')])
        assert len(prepared_calls) == 1
        assert seen == ['a', 'b', 'c']
    asyncio.run(main())


@pytest.mark.parametrize('status', ['incomplete', 'exception'])
def test_failed_driver_aborts_and_does_not_retry_successful_action(status):
    async def main():
        calls = []
        async def run(session):
            calls.append('successful effect')
            if status == 'exception':
                raise ValueError('broken')
            return DriverResult('incomplete', reason='budget')
        rt, store = runtime([actor('a', run)])
        with pytest.raises(Exception):
            await rt.run_step(1, 0, [Phase('phase', lambda ctx: ctx.activate('a'))])
        assert store.events == [('abort',)]
        with pytest.raises(RuntimeError):
            await rt.run_step(1, 0, [Phase('phase', lambda ctx: ctx.activate('a'))])
        assert calls == ['successful effect']
        assert rt.last_completed == 0
    asyncio.run(main())


def test_cancellation_waits_for_driver_cleanup_before_abort():
    async def main():
        started = asyncio.Event()
        order, sessions = [], []
        async def run(session):
            sessions.append(session)
            started.set()
            try:
                await asyncio.Future()
            finally:
                await asyncio.sleep(0)
                order.append('driver closed')
        rt, store = runtime([actor('a', run)])
        store.abort_step = lambda: order.append('abort')
        task = asyncio.create_task(rt.run_step(1, 1, [Phase('p', lambda ctx: ctx.activate('a'))]))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert order == ['driver closed', 'abort']
        with pytest.raises(ScopeClosed):
            sessions[0].scope.check_active()
    asyncio.run(main())


def test_activation_limit_prevents_publish_and_invalidates_phase():
    async def main():
        contexts = []
        async def run(session):
            session.activate('a')
            return DriverResult('completed')
        rt, store = runtime([actor('a', run)], max_activations=2)
        def phase(ctx):
            contexts.append(ctx)
            ctx.activate('a')
        with pytest.raises(Exception, match='maximum|max_activations|上限'):
            await rt.run_step(1, 1, [Phase('p', phase)])
        assert store.events[-1] == ('abort',)
        with pytest.raises(RuntimeError):
            contexts[0].activate('a')
    asyncio.run(main())


def test_phase_order_prepare_before_drivers_and_clock_does_not_advance_on_await():
    async def main():
        order = []
        async def run(session):
            order.append(('actor', session.moment))
            await asyncio.sleep(0)
            assert session.moment == Moment(9, 'second')
            return DriverResult('waiting')
        rt, store = runtime([actor('a', run)])
        def prepare(ctx):
            order.append('prepare')
        await rt.run_step(1, 9, [Phase('first', lambda ctx: order.append('first')),
                                  Phase('second', lambda ctx: ctx.activate('a'), prepare=prepare)])
        assert order == ['first', 'prepare', ('actor', Moment(9, 'second'))]
        assert store.events[-1] == ('complete',)
    asyncio.run(main())


def test_failure_cancels_independent_peer_before_abort_and_skips_next_phase():
    async def main():
        started = asyncio.Event()
        events = []
        async def run(session):
            if session.actor.id == 'slow':
                started.set()
                try:
                    await asyncio.Future()
                finally:
                    events.append('slow stopped')
            await started.wait()
            raise ValueError('driver failure')
        rt, store = runtime([actor('slow', run), actor('broken', run)], capacity=2)
        store.abort_step = lambda: events.append('abort')
        def phase(ctx):
            ctx.activate('slow')
            ctx.activate('broken')
        with pytest.raises(ValueError, match='driver failure'):
            await rt.run_step(1, 0, [Phase('p', phase, execution='independent'),
                                     Phase('never', lambda ctx: events.append('wrong'))])
        assert events == ['slow stopped', 'abort']
    asyncio.run(main())


def test_step_budget_is_shared_across_phases():
    async def main():
        seen = []
        async def run(session):
            seen.append(session.moment.phase)
            return DriverResult('completed')
        rt, store = runtime([actor('a', run)], max_activations=1)
        with pytest.raises(Exception, match='maximum|max_activations|上限'):
            await rt.run_step(1, 0, [Phase('one', lambda ctx: ctx.activate('a')),
                                    Phase('two', lambda ctx: ctx.activate('a'))])
        assert seen == ['one']
        assert store.events == [('abort',)]
    asyncio.run(main())


def test_same_moment_state_continues_and_new_moment_resets_without_copy():
    async def main():
        values = []
        async def run(session):
            values.append(session.cursors.get('seen', 0))
            session.cursors['seen'] = values[-1] + 1
            return DriverResult('completed')
        rt, _ = runtime([actor('a', run)])
        phases = [Phase('p', lambda ctx: ctx.activate('a'))]
        await rt.run_step(1, 7, phases)
        await rt.run_step(2, 7, phases)
        await rt.run_step(3, 8, phases)
        assert values == [0, 1, 0]
    asyncio.run(main())


def test_publish_failure_and_phase_hook_failure_abort_without_advancing_watermark():
    async def main():
        for failure in ('publish', 'hook'):
            rt, store = runtime([])
            def broken(*args):
                raise OSError('failed')
            if failure == 'publish':
                store.complete = broken
            phases = [] if failure == 'publish' else [Phase('p', broken)]
            with pytest.raises(OSError):
                await rt.run_step(1, 0, phases)
            assert rt.last_completed == 0
            assert store.events == [('abort',)]
    asyncio.run(main())


def test_independent_namespaces_apply_without_shared_order_assumption():
    async def main():
        gate = asyncio.Event()
        values = {'left': 0, 'right': 0}
        async def run(session):
            if session.actor.id == 'left':
                await gate.wait()
            result = await session.actions.invoke(session.actor.id, Ref(session.actor.id, 'account', '1'), {})
            assert result.status == 'completed'
            if session.actor.id == 'right':
                gate.set()
            return DriverResult('completed')
        rt, _ = runtime([actor(n, run) for n in values], capacity=2)
        for name in values:
            def effect(scope, target, arguments):
                values[target.namespace] += 1
                return ActionResult('completed')
            rt.actions.register(Action(name, (name, 'account'), '', {}, effect))
        def phase(ctx):
            ctx.activate('left')
            ctx.activate('right')
        await asyncio.wait_for(rt.run_step(1, 0, [Phase('p', phase, execution='independent')]), 1)
        assert values == {'left': 1, 'right': 1}
    asyncio.run(main())


def test_spent_budget_allows_remaining_pure_mechanism_hooks():
    async def main():
        hooks = []
        async def run(session):
            return DriverResult('completed')
        rt, _ = runtime([actor('a', run)], max_activations=1)
        await rt.run_step(1, 0, [Phase('one', lambda ctx: ctx.activate('a')),
                                Phase('two', lambda ctx: hooks.append('done'))])
        assert hooks == ['done']
    asyncio.run(main())


def test_real_store_two_mechanisms_complete_restore_and_failed_step(tmp_path):
    from society0.kernel.storage import StageStore

    async def main():
        store = StageStore.create(tmp_path / 'run', [
            'CREATE TABLE ledger(namespace TEXT PRIMARY KEY NOT NULL, value INTEGER NOT NULL)',
        ], initialize=lambda w: w.executemany('INSERT INTO ledger VALUES(?,?)', [('left', 0), ('right', 0)]))
        try:
            async def run(session):
                await session.actions.invoke(session.actor.id, Ref(session.actor.id, 'account', '1'), {})
                return DriverResult('completed' if session.moment.time == 0 else 'incomplete')
            rt = Runtime([actor(n, run) for n in ('left', 'right')],
                         information=Information(lambda *a: True), actions=Actions(lambda *a: True), store=store)
            for name in ('left', 'right'):
                def effect(scope, target, args):
                    store.transaction(lambda w: w.execute('UPDATE ledger SET value=value+1 WHERE namespace=?', (target.namespace,)))
                    return ActionResult('completed')
                rt.actions.register(Action(name, (name, 'account'), '', {}, effect))
            def phase(ctx):
                ctx.activate('left')
                ctx.activate('right')
            assert (await rt.run_step(1, 0, [Phase('p', phase)]))['step'] == 1
            with pytest.raises(Exception):
                await rt.run_step(2, 1, [Phase('p', phase)])
            assert rt.last_completed == 1
        finally:
            store.close()
        with StageStore.restore(tmp_path / 'run', tmp_path / 'restored', step=1) as restored:
            assert restored.read(lambda r: r.query('SELECT namespace,value FROM ledger ORDER BY namespace')) == [('left', 1), ('right', 1)]
    asyncio.run(main())
