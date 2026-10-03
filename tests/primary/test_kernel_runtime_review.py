"""运行时独立生命周期验收；与作者测试分开留证。"""
import asyncio
from types import SimpleNamespace

import pytest

from society0.kernel.interaction import Actions, Information, ScopeClosed
from society0.kernel.runtime import Actor, DriverResult, Phase, Runtime


class Store:
    complete_step = 0
    def __init__(self): self.events = []
    def complete(self, step):
        self.complete_step = step
        self.events.append(('complete', step))
    def abort_step(self): self.events.append(('abort', self.complete_step))


def make(run=None, **kwargs):
    store = Store()
    actors = [] if run is None else [Actor('a', SimpleNamespace(run=run))]
    return Runtime(actors, information=Information(lambda *args: True),
                   actions=Actions(lambda *args: True), store=store, **kwargs), store


@pytest.mark.asyncio
async def test_review_concurrent_run_does_not_abort_existing_step():
    gate, started = asyncio.Event(), asyncio.Event()
    async def run(session):
        started.set()
        await gate.wait()
        return DriverResult('waiting')
    runtime, store = make(run)
    task = asyncio.create_task(runtime.run_step(1, 0, [Phase('p', lambda ctx: ctx.activate('a'))]))
    await started.wait()
    with pytest.raises(RuntimeError, match='ready'):
        await runtime.run_step(1, 0, [])
    assert store.events == []
    gate.set()
    await task
    assert store.events == [('complete', 1)]


@pytest.mark.asyncio
async def test_review_complete_receipt_failure_preserves_authoritative_watermark():
    runtime, store = make()
    def publish_then_disconnect(step):
        store.complete_step = step
        raise OSError('receipt lost after durable completion')
    store.complete = publish_then_disconnect
    with pytest.raises(OSError, match='receipt lost'):
        await runtime.run_step(1, 0, [])
    assert runtime.last_completed == store.complete_step == 1
    with pytest.raises(RuntimeError, match='ready'):
        await runtime.run_step(2, 1, [])


@pytest.mark.asyncio
async def test_review_close_waits_async_worker_finally_before_abort():
    started = asyncio.Event()
    order, scopes = [], []
    async def run(session):
        scopes.append(session.scope)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            order.append('worker-drained')
    runtime, store = make(run)
    store.abort_step = lambda: order.append('abort')
    task = asyncio.create_task(runtime.run_step(1, 0, [Phase('p', lambda ctx: ctx.activate('a'))]))
    await started.wait()
    await runtime.close()
    assert task.cancelled() and order == ['worker-drained', 'abort']
    with pytest.raises(ScopeClosed): scopes[0].check_active()


@pytest.mark.asyncio
async def test_review_late_activation_from_driver_finally_is_drained_before_publish():
    calls, contexts = [], []
    async def run(session):
        calls.append(len(calls))
        try:
            return DriverResult('waiting')
        finally:
            await asyncio.sleep(0)
            if len(calls) == 1:
                session.activate('a', 'late')
    runtime, store = make(run, max_activations=2)
    def phase(ctx):
        contexts.append(ctx)
        ctx.activate('a')
    await runtime.run_step(1, 0, [Phase('p', phase)])
    assert calls == [0, 1] and store.events == [('complete', 1)]
    with pytest.raises(RuntimeError): contexts[0].activate('a')


@pytest.mark.asyncio
async def test_review_total_budget_allows_empty_later_phase_but_no_third_activation():
    calls = []
    async def run(session):
        calls.append(session.moment.phase)
        return DriverResult('completed')
    runtime, store = make(run, max_activations=2)
    with pytest.raises(Exception, match='maximum|max_activations|上限'):
        await runtime.run_step(1, 0, [Phase('first', lambda ctx: ctx.activate('a')),
                                     Phase('second', lambda ctx: ctx.activate('a')),
                                     Phase('empty', lambda ctx: None),
                                     Phase('third', lambda ctx: ctx.activate('a'))])
    assert calls == ['first', 'second']
    assert store.events == [('abort', 0)]


@pytest.mark.asyncio
async def test_review_pluginhost_two_mechanisms_real_store_serial_then_restore(tmp_path):
    from society0.kernel.plugins import Plugin, PluginHost
    from society0.kernel.runtime import runtime_plugin
    from society0.kernel.storage import StageStore
    from society0.kernel.interaction import Action, ActionResult, Ref

    store = StageStore.create(tmp_path / 'run', [
        'CREATE TABLE facts(name TEXT PRIMARY KEY NOT NULL,value INTEGER NOT NULL)'],
        initialize=lambda writer: writer.executemany('INSERT INTO facts VALUES (?,?)', [('orders', 0), ('messages', 0)]))
    actions = Actions(lambda *args: True)
    information = Information(lambda *args: True)
    order = []
    active = 0
    async def run(session):
        nonlocal active
        active += 1
        assert active == 1
        await asyncio.sleep(0)
        await session.actions.invoke(session.actor.id, Ref(session.actor.id, 'fact', 'one'), {})
        active -= 1
        return DriverResult('waiting')
    def install_api(ctx):
        ctx.provide('actions', actions)
        ctx.provide('information', information)
    def install_store(ctx):
        ctx.provide('store', store)
        ctx.on_close(store.close)
    def mechanism(name):
        def install(ctx):
            owned_store = ctx.require('storage', 'store')
            def apply(scope, target, arguments):
                before = owned_store.read(lambda r: r.query('SELECT name,value FROM facts ORDER BY name'))
                order.append((name, before))
                owned_store.transaction(lambda w: w.execute('UPDATE facts SET value=value+1 WHERE name=?', (name,)))
                return ActionResult('completed')
            ctx.require('api', 'actions').register(Action(name, (name, 'fact'), name, {}, apply))
        return Plugin(name, ('api', 'storage'), install)
    async with PluginHost([
        Plugin('api', install=install_api), Plugin('storage', install=install_store),
        mechanism('orders'), mechanism('messages'),
        runtime_plugin([Actor(name, SimpleNamespace(run=run)) for name in ('orders', 'messages')],
                       information=('api', 'information'), actions=('api', 'actions'),
                       store=('storage', 'store'), capacity=8),
    ]) as host:
        runtime = host.service('runtime', 'runtime')
        def phase(ctx):
            ctx.activate('orders')
            ctx.activate('messages')
        await runtime.run_step(1, 0, [Phase('serial', phase)])
    assert order == [('orders', [('messages', 0), ('orders', 0)]),
                     ('messages', [('messages', 0), ('orders', 1)])]
    with StageStore.restore(tmp_path / 'run', tmp_path / 'restored', step=1) as restored:
        assert restored.read(lambda r: r.query('SELECT name,value FROM facts ORDER BY name')) == [('messages', 1), ('orders', 1)]
