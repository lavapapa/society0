"""推荐经完整认知输入呈现后登记曝光，同 Moment 与完整点恢复共用游标。"""
import json
from dataclasses import replace

import pytest

from society0 import ActorRecord, Plugin, Phase, compose
from society0.plugins import actor_plugin, interaction_plugin, llm_driver_plugin, runtime_plugin, social_plugin, thread_plugin
from tests.primary.test_kernel_llm import FakeProvider, reply


SOCIAL_SOURCE_ID = 'social.main.v1'
NOTICE_SOURCE_ID = 'notice.main.v1'


def plugins(providers, *, malformed=False, notices=True, capture_typed=False):
    from society0.plugins.cognition import SourceSpec, cognition_plugin
    from society0.plugins.social_cognition import social_source_plugin
    from examples.core_next.composition_support import notification_data_plugin, notification_source_plugin
    def install(ctx):
        if capture_typed:
            from tests.primary.test_cognition_composition import CapturingProvider
            provider=CapturingProvider(ctx.require('threads','threads'))
        else:
            provider=FakeProvider(ctx.require('threads','threads'),[reply(text='读取完成') for _ in range(4)])
        providers.append(provider)
        ctx.provide('provider',provider)
    configured=[
        actor_plugin({'llm':('driver','factory')},records=[ActorRecord(actor,'llm') for actor in 'abc']),
        interaction_plugin(lambda *args:True),thread_plugin(),
        social_plugin('abc',edges=(),content_length_limit=-1,config={'social_media':{'recommendation':{'use_embedding_similarity':False}}}),
        social_source_plugin(),Plugin('provider',('threads',),install),
    ]
    sources = [SourceSpec(SOCIAL_SOURCE_ID, ('social.source', 'source'))]
    if notices:
        configured.extend([notification_data_plugin(), notification_source_plugin()])
        sources.append(SourceSpec(NOTICE_SOURCE_ID, ('notice.source', 'source')))
    configured.append(cognition_plugin(sources=tuple(sources), environment='共享社交环境'))
    builder=('cognition','input_builder')
    if malformed:
        def broken(ctx):
            original=ctx.require(*builder)
            async def build(session):
                batch=await original(session)
                return replace(batch,messages=[*batch.messages,{'role':'user','content':object()}])
            ctx.provide('builder',build)
        configured.append(Plugin('broken',('cognition',),broken))
    configured.extend([
        llm_driver_plugin(provider=('provider','provider'),input_builder=('broken','builder') if malformed else builder,name='driver'),
        runtime_plugin(actor_service=('actors','actors'),information=('interaction','information'),
            actions=('interaction','actions'),store=('storage','store')),
    ])
    return configured


@pytest.mark.asyncio
async def test_actual_input_exposure_reactivation_and_complete_restore(tmp_path):
    providers=[]
    async with compose(tmp_path/'run',plugins(providers)) as host:
        social=host.service('social','mechanism')
        post=social.execute('publish_post','b','b',{'content':'推荐帖子完整原文🙂'*500},0).value['post_id']
        async def read_twice(ctx):
            ctx.activate('a');await ctx.drain()
            ctx.activate('a');await ctx.drain()
            assert social.post_details(post)['view_count']==0
        await host.service('runtime','runtime').run_step(1,1,[Phase('read',read_twice)])
        assert social.post_details(post)['view_count']==1
        assert social.recommended_ids('a')==[post]
        threads=host.service('threads','threads')
        from society0.kernel.interaction import Moment
        tid=threads.find('a',Moment(1,'read'))
        first_history=threads.read_messages(tid)
        first_messages=[m['content'] for m in first_history if m['role']=='user']
        assert sum('推荐帖子完整原文🙂' in message for message in first_messages)==1
        assert len(providers[0].requests)==2
    async with compose(tmp_path/'restored',plugins(providers),source=tmp_path/'run') as host:
        social=host.service('social','mechanism')
        new=social.execute('publish_post','c','c',{'content':'恢复后的新动态'},1).value['post_id']
        await host.service('runtime','runtime').run_step(2,1,[Phase('read',lambda ctx:ctx.activate('a'))])
        assert social.post_details(post)['view_count']==1
        assert social.post_details(new)['view_count']==1
        current=host.service('threads','threads').read_messages(tid)
        assert current[:len(first_history)]==first_history
        texts=[m['content'] for m in current if m['role']=='user']
        assert sum('推荐帖子完整原文🙂' in message for message in texts)==1
        assert sum('恢复后的新动态' in message for message in texts)==1
        assert set(social.recommended_ids('a'))=={post,new}


@pytest.mark.asyncio
async def test_input_serialization_failure_has_no_exposure_or_complete_point(tmp_path,monkeypatch):
    providers=[]
    async with compose(tmp_path/'bad',plugins(providers,malformed=True)) as host:
        social=host.service('social','mechanism')
        post=social.execute('publish_post','b','b',{'content':'不能产生曝光'},0).value['post_id']
        runtime=host.service('runtime','runtime')
        await runtime.run_step(1,0,[])
        recorded=[]
        monkeypatch.setattr(host.service('social.exposure','exposure'),'record',lambda *args,**kwargs:recorded.append(args))
        with pytest.raises(TypeError):
            await runtime.run_step(2,1,[Phase('read',lambda ctx:ctx.activate('a'))])
        assert host.service('storage','store').complete_step==1
        assert providers[0].requests==[] and recorded==[]
    async with compose(tmp_path/'restore-after-failure',plugins(providers),source=tmp_path/'bad') as host:
        social=host.service('social','mechanism')
        assert social.post_details(post)['view_count']==0
        assert social.recommended_ids('a')==[]


@pytest.mark.asyncio
async def test_social_perception_returns_source_batch_without_confirming_preview(tmp_path):
    from society0.plugins.cognition import SourceBatch, SourcePosition
    from society0.plugins.social_cognition import SocialPerception
    from tests.primary.test_plugin_social import plan
    from tests.primary.test_cognition_composition import session
    async with compose(tmp_path/'preview', plan(social_plugin('abc', edges=(), content_length_limit=-1))) as host:
        social = host.service('social', 'mechanism')
        post = social.execute('publish_post', 'b', 'b', {'content': '来源完整正文🙂'*1000}, 0).value['post_id']
        source = SocialPerception(host.service('social.recommendation', 'recommendation'),
            host.service('social.content', 'content'), host.service('social.exposure', 'exposure'), post_count=1, domain='social')
        first = await source(session(None), None)
        assert isinstance(first, SourceBatch)
        assert first.position['seen'] == [post] and len(first.effects) == 1
        assert '来源完整正文🙂'*1000 in first.messages[0]['content']
        second = await source(session(None), SourcePosition(first.position))
        assert second.messages == [] and second.effects == () and second.position == first.position
        await social.after_tick()
        assert social.post_details(post)['view_count'] == 0 and social.recommended_ids('a') == []


@pytest.mark.asyncio
async def test_social_source_exports_only_source_and_does_not_borrow_threads(tmp_path):
    configured = plugins([], notices=False)
    source = next(plugin for plugin in configured if plugin.name == 'social.source')
    assert 'threads' not in source.requires
    async with compose(tmp_path/'source', configured) as host:
        assert callable(host.service('social.source', 'source'))
        with pytest.raises(KeyError):
            host.service('social.source', 'input_builder')
        description = next(item for item in host.describe_plugins() if item.name == 'cognition')
        assert description.requires == ('threads', 'social.source')


@pytest.mark.asyncio
@pytest.mark.parametrize('order', [('social', 'notice'), ('notice', 'social')])
async def test_cog_01_02_dual_sources_keep_independent_watermarks_full_sdk_input_and_phase_identity(tmp_path, monkeypatch, order):
    from society0.kernel.interaction import Moment
    from society0.plugins.cognition import SourceSpec, cognition_plugin
    from tests.primary.test_cognition_composition import request_texts
    providers = []
    configured = plugins(providers, capture_typed=True)
    actor_index = next(index for index, plugin in enumerate(configured) if plugin.name == 'actors')
    persona = '本人的完整背景🙂'*500
    configured[actor_index] = actor_plugin({'llm': ('driver', 'factory')}, records=[ActorRecord(actor, 'llm', persona=persona if actor == 'a' else None) for actor in 'abc'])
    cognition_index = next(index for index, plugin in enumerate(configured) if plugin.name == 'cognition')
    specs = {'social': SourceSpec(SOCIAL_SOURCE_ID, ('social.source', 'source')), 'notice': SourceSpec(NOTICE_SOURCE_ID, ('notice.source', 'source'))}
    configured[cognition_index] = cognition_plugin(sources=tuple(specs[name] for name in order), environment='共同环境完整说明')
    originals = ['社交首篇完整原文🙂'*1000, '通知首条完整原文🙂'*1000, '仅社交新增正文', '仅通知新增正文']
    async with compose(tmp_path/'run', configured) as host:
        social = host.service('social', 'mechanism')
        notes = host.service('notice.data', 'notifications')
        threads = host.service('threads', 'threads')
        runtime = host.service('runtime', 'runtime')
        first = social.execute('publish_post', 'b', 'b', {'content': originals[0]}, 0).value['post_id']
        notes.publish('a', originals[1])
        effects = []
        exposure = host.service('social.exposure', 'exposure')
        original_record, original_ack = exposure.record, notes.acknowledge
        def record(*args, **kwargs):
            effects.append('social')
            return original_record(*args, **kwargs)
        def acknowledge(*args, **kwargs):
            effects.append('notice')
            return original_ack(*args, **kwargs)
        monkeypatch.setattr(exposure, 'record', record)
        monkeypatch.setattr(notes, 'acknowledge', acknowledge)
        async def three(context):
            context.activate('a'); await context.drain()
            tid = threads.find('a', Moment(1, 'read'))
            saved = threads.input_cursor(tid, 'operating_context')['position']
            assert saved[SOCIAL_SOURCE_ID]['seen'] == [first] and saved[NOTICE_SOURCE_ID] == 1
            assert effects == list(order)
            social.execute('publish_post', 'c', 'c', {'content': originals[2]}, 1)
            context.activate('a'); await context.drain()
            saved = threads.input_cursor(tid, 'operating_context')['position']
            assert len(saved[SOCIAL_SOURCE_ID]['seen']) == 2 and saved[NOTICE_SOURCE_ID] == 1
            notes.publish('a', originals[3])
            context.activate('a'); await context.drain()
            saved = threads.input_cursor(tid, 'operating_context')['position']
            assert len(saved[SOCIAL_SOURCE_ID]['seen']) == 2 and saved[NOTICE_SOURCE_ID] == 2
        await runtime.run_step(1, 1, [Phase('read', three)])
        tid = threads.find('a', Moment(1, 'read'))
        prefix = threads.read_messages(tid)
        texts = request_texts(providers[0].requests[0])
        assert sum(persona in text for text in texts) == 1
        assert sum('当前主观状态' in text for text in texts) == 1
        assert any('共同环境完整说明' in text for text in texts)
        indices = {'social': next(i for i, text in enumerate(texts) if originals[0] in text), 'notice': next(i for i, text in enumerate(texts) if originals[1] in text)}
        assert indices[order[0]] < indices[order[1]]
        assert notes.receipts('a') == [(tid, 1), (tid, 2)]
        assert [social.post_details(identifier)['view_count'] for identifier in ('post_1', 'post_2')] == [1, 1]
        await runtime.run_step(2, 1, [Phase('different', lambda context: context.activate('a'))])
        b_tid = threads.find('a', Moment(1, 'different'))
        assert b_tid != tid and (b_tid, 1) in notes.receipts('a') and (b_tid, 2) in notes.receipts('a')
        await runtime.run_step(3, 1, [Phase('read', lambda context: context.activate('a'))])
        assert threads.find('a', Moment(1, 'read')) == tid
        assert threads.read_messages(tid)[:len(prefix)] == prefix
        texts = request_texts(providers[0].requests[-1])
        for original in originals:
            assert sum(original in text for text in texts) == 1
        assert [social.post_details(identifier)['view_count'] for identifier in ('post_1', 'post_2')] == [2, 2]
        assert [row for row in notes.receipts('a') if row[0] == tid] == [(tid, 1), (tid, 2)]


@pytest.mark.asyncio
async def test_cog_12_interleaved_dual_sources_acknowledge_each_actors_actual_thread_and_ids(tmp_path):
    import asyncio
    from society0.kernel.interaction import Moment
    from society0.plugins.cognition import SourceSpec, cognition_plugin
    from tests.primary.test_cognition_composition import request_texts
    providers = []
    entered = {actor: asyncio.Event() for actor in 'ab'}
    release = asyncio.Event()
    def install(ctx):
        source = ctx.require('notice.source', 'source')
        async def read(current, previous):
            batch = source(current, previous)
            entered[current.actor.id].set()
            await release.wait()
            return batch
        ctx.provide('source', read)
    configured = plugins(providers, capture_typed=True)
    index = next(index for index, plugin in enumerate(configured) if plugin.name == 'cognition')
    configured[index] = cognition_plugin(sources=(SourceSpec(SOCIAL_SOURCE_ID, ('social.source', 'source')), SourceSpec(NOTICE_SOURCE_ID, ('blocked_notice', 'source'))))
    configured.append(Plugin('blocked_notice', ('notice.source',), install))
    async with compose(tmp_path/'run', configured) as host:
        social = host.service('social', 'mechanism')
        post = social.execute('publish_post', 'c', 'c', {'content': '两主体共享完整社交正文'}, 0).value['post_id']
        notes = host.service('notice.data', 'notifications')
        notes.publish('a', '仅a的第一通知')
        notes.publish('b', '仅b的第一通知')
        notes.publish('a', '仅a的第二通知')
        async def concurrent(context):
            try:
                context.activate('a'); context.activate('b')
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 10)
                release.set()
                await context.drain()
            finally:
                release.set()
        await host.service('runtime', 'runtime').run_step(1, 1, [Phase('read', concurrent, execution='independent', capacity=2)])
        threads = host.service('threads', 'threads')
        for actor, identifiers in [('a', [1, 3]), ('b', [2])]:
            tid = threads.find(actor, Moment(1, 'read'))
            positions = threads.input_cursor(tid, 'operating_context')['position']
            assert positions[SOCIAL_SOURCE_ID]['seen'] == [post]
            assert positions[NOTICE_SOURCE_ID] == identifiers[-1]
            assert notes.receipts(actor) == [(tid, identifier) for identifier in identifiers]
        assert social.post_details(post)['view_count'] == 2
        assert len(providers[0].requests) == 2
        for request in providers[0].requests:
            texts = request_texts(request)
            owns_a = any('仅a的第一通知' in text for text in texts)
            owns_b = any('仅b的第一通知' in text for text in texts)
            assert owns_a != owns_b
            assert any('仅a的第二通知' in text for text in texts) == owns_a
            assert any('两主体共享完整社交正文' in text for text in texts)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['extension', 'provider', 'before_publish'])
async def test_cog_10_runtime_discards_unpublished_dual_source_input_and_effects_on_restore(tmp_path, failure):
    from contextlib import asynccontextmanager
    from society0.kernel.interaction import Moment
    providers = []
    configured = plugins(providers, capture_typed=True)
    inspected = []
    def checked(ctx):
        original = ctx.require('cognition', 'input_builder')
        notes, threads = ctx.require('notice.data', 'notifications'), ctx.require('threads', 'threads')
        async def builder(current):
            batch = await original(current)
            def saved(context):
                inspected.append((context.thread_id, notes.receipts(current.actor.id), threads.input_cursor(context.thread_id, batch.consumer)))
            return replace(batch, effects=(*batch.effects, saved))
        ctx.provide('builder', builder)
    configured.append(Plugin('checked_input', ('cognition', 'notice.data', 'threads'), checked))
    index = next(index for index, plugin in enumerate(configured) if plugin.name == 'driver')
    configured[index] = llm_driver_plugin(provider=('provider', 'provider'), input_builder=('checked_input', 'builder'), name='driver')
    if failure == 'extension':
        @asynccontextmanager
        async def bad_extension(context):
            context.messages.append({'role': 'user', 'content': object()})
            yield
        def install(ctx):
            ctx.provide('extension', bad_extension)
        configured.append(Plugin('bad_extension', install=install))
        index = next(index for index, plugin in enumerate(configured) if plugin.name == 'driver')
        configured[index] = llm_driver_plugin(provider=('provider', 'provider'), input_builder=('checked_input', 'builder'),
            extensions=(('bad_extension', 'extension'),), name='driver')
    async with compose(tmp_path/'run', configured) as host:
        social = host.service('social', 'mechanism')
        post = social.execute('publish_post', 'b', 'b', {'content': '完整点中的社交正文'}, 0).value['post_id']
        notes = host.service('notice.data', 'notifications')
        notes.publish('a', '完整点中的通知正文')
        runtime = host.service('runtime', 'runtime')
        await runtime.run_step(1, 0, [])
        if failure == 'provider':
            async def unavailable(*args, **kwargs):
                raise RuntimeError('provider failed')
            providers[0].request_model = unavailable
        async def read(context):
            context.activate('a'); await context.drain()
            if failure == 'before_publish':
                raise RuntimeError('stopped before publish')
        with pytest.raises(TypeError if failure == 'extension' else RuntimeError):
            await runtime.run_step(2, 1, [Phase('read', read)])
        assert host.service('storage', 'store').complete_step == 1
        assert len(inspected) == 1
        tid, receipts, cursor = inspected[0]
        assert receipts == [(tid, 1)]
        assert cursor['position'][NOTICE_SOURCE_ID] == 1
        assert cursor['position'][SOCIAL_SOURCE_ID]['seen'] == [post]
        from society0.kernel.storage import StorageError
        with pytest.raises(StorageError, match='restore required'):
            host.service('threads', 'threads').find('a', Moment(1, 'read'))
        assert len(providers[0].requests) == (1 if failure == 'before_publish' else 0)
    async with compose(tmp_path/'restored', plugins([], capture_typed=True), source=tmp_path/'run', step=1) as host:
        assert host.service('notice.data', 'notifications').receipts('a') == []
        assert host.service('social', 'mechanism').post_details(post)['view_count'] == 0
        assert host.service('social', 'mechanism').recommended_ids('a') == []
        assert host.service('threads', 'threads').find('a', Moment(1, 'read')) is None
