"""用真实 Runtime/Semantic 与 Event 验证 runtime→worker→资源关闭。"""
import asyncio
import pytest

from society0.kernel.actors import ActorRecord, actor_data_plugin, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import Query, interaction_plugin
from society0.kernel.plugins import Plugin, PluginHost
from society0.kernel.runtime import Actor, DriverResult, Phase, Runtime, runtime_plugin
from society0.kernel.storage import StageStore
from society0.plugins.social import social_plugin
from society0.plugins.social_semantic import Semantic


@pytest.mark.asyncio
@pytest.mark.parametrize('runtime_order', ['early', 'late'])
@pytest.mark.parametrize('request_from', ['activation', 'after_step'])
async def test_qui_01_02_runtime_idle_precedes_semantic_shutdown_in_both_graphs(tmp_path, monkeypatch, runtime_order, request_from):
    entered = asyncio.Event()
    never = asyncio.Event()
    events = []
    resources = {'closed': False}
    state = {}

    class Embedding:
        async def embed(self, texts, **kwargs):
            entered.set()
            try:
                await never.wait()
                return [[1.0, 0.0] for text in texts]
            finally:
                assert not resources['closed']
                assert state['store'].read(lambda view: view.query('SELECT count(*) FROM social_posts')[0][0]) == 1
                events.append('request.settled')

    def install_resources(ctx):
        def close():
            resources['closed'] = True
            events.append('provider.closed')
        ctx.on_close(close)
        ctx.provide('embeddings', {'main': Embedding()})
        ctx.provide('client', object())

    class Driver:
        async def run(self, session):
            try:
                if request_from == 'activation':
                    await session.information.query('/social/feed', Query(limit=1))
                return DriverResult('completed')
            finally:
                session.scope.check_active()
                state['store'].read(lambda view: view.query('SELECT 1'))
                events.append('driver.cleaned')

    old_runtime_close = Runtime.close
    async def runtime_close(self):
        events.append('runtime.stop')
        await old_runtime_close(self)
        events.append('runtime.idle')
    monkeypatch.setattr(Runtime, 'close', runtime_close)
    old_semantic_close = Semantic.close
    async def semantic_close(self):
        events.append('worker.stop')
        await old_semantic_close(self)
        events.append('worker.idle')
    monkeypatch.setattr(Semantic, 'close', semantic_close)
    old_store_close = StageStore.close
    def store_close(self):
        old_store_close(self)
        events.append('store.closed')
    monkeypatch.setattr(StageStore, 'close', store_close)

    data = actor_data_plugin(records=[ActorRecord(actor, 'rule') for actor in 'ab'])
    driver = Driver()
    optional = []
    if runtime_order == 'late':
        def install_driver(ctx):
            ctx.require('social.semantic', 'semantic')
            ctx.provide('factory', lambda view: driver)
        optional = [Plugin('driver', requires=('social.semantic',), install=install_driver),
                    actor_plugin({'rule': ('driver', 'factory')}, data=('actors.data', 'directory'))]
    runtime = runtime_plugin([Actor('a', driver)], information=('interaction', 'information'),
                             actions=('interaction', 'actions'), store=('storage', 'store'),
                             actor_service=('actors', 'actors') if optional else None)
    plugins = [data, interaction_plugin(lambda *args: True), runtime,
               Plugin('resources', install=install_resources),
               social_plugin('ab', edges=[], embedding=('resources', 'main'), vector_client=('resources', 'client'),
                             config={'social_media': {'recommendation': {'use_embedding_similarity': True}}}), *optional]
    async with compose(tmp_path/'run', plugins) as host:
        state['store'] = host.service('storage', 'store')
        host.service('social', 'mechanism').execute('publish_post', 'b', 'b', {'content': '完整待嵌入原文'}, 1)
        async def phase(ctx):
            ctx.activate('a')
            await ctx.drain()
        running = asyncio.create_task(host.service('runtime', 'runtime').run_step(1, 1, [Phase('decision', phase)]))
        await asyncio.wait_for(entered.wait(), timeout=10)
    with pytest.raises(asyncio.CancelledError):
        await running
    assert events.index('runtime.idle') < events.index('worker.stop')
    assert events.index('request.settled') < events.index('runtime.idle')
    assert events.index('driver.cleaned') < events.index('runtime.idle')
    assert events.index('worker.idle') < events.index('provider.closed') < events.index('store.closed')
    assert resources['closed']


@pytest.mark.asyncio
@pytest.mark.parametrize('broken_stage', ['runtime', 'worker', 'resource'])
async def test_qui_03_stage_failure_keeps_remaining_stops_and_resources(broken_stage):
    events = []
    def append(name):
        events.append(name)
        if name == broken_stage + '.last':
            raise OSError('stage failure: ' + broken_stage)
    def install(ctx):
        ctx.on_close(append, 'resource.first')
        ctx.on_close(append, 'resource.last')
        ctx.on_quiesce(append, 'runtime.first', stage='runtime')
        ctx.on_quiesce(append, 'runtime.last', stage='runtime')
        ctx.on_quiesce(append, 'worker.first', stage='worker')
        ctx.on_quiesce(append, 'worker.last', stage='worker')
    with pytest.raises(OSError, match=broken_stage):
        async with PluginHost([Plugin('stages', install=install)]):
            pass
    assert events == ['runtime.last', 'runtime.first', 'worker.last', 'worker.first', 'resource.last', 'resource.first']


@pytest.mark.asyncio
async def test_qui_03_partial_install_and_invalid_stage_use_same_cleanup_order():
    events = []
    def install(ctx):
        ctx.on_close(events.append, 'resource')
        ctx.on_quiesce(events.append, 'worker')
        ctx.on_quiesce(events.append, 'runtime', stage='runtime')
        ctx.on_quiesce(events.append, 'invalid', stage='unexpected')
    with pytest.raises(ValueError, match='quiesce stage'):
        async with PluginHost([Plugin('partial', install=install)]):
            pass
    assert events == ['runtime', 'worker', 'resource']
