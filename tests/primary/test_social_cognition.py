"""推荐经完整认知输入呈现后登记曝光，同 Moment 与完整点恢复共用游标。"""
import json
from dataclasses import replace

import pytest

from society0 import ActorRecord, Plugin, Phase, compose
from society0.plugins import actor_plugin, interaction_plugin, llm_driver_plugin, runtime_plugin, social_plugin, thread_plugin
from tests.primary.test_kernel_llm import FakeProvider, reply


def plugins(providers, *, malformed=False):
    from society0.plugins.social_cognition import social_cognition_plugin
    def install(ctx):
        provider=FakeProvider(ctx.require('threads','threads'),[reply(text='读取完成') for _ in range(4)])
        providers.append(provider)
        ctx.provide('provider',provider)
    configured=[
        actor_plugin({'llm':('driver','factory')},records=[ActorRecord(actor,'llm') for actor in 'abc']),
        interaction_plugin(lambda *args:True),thread_plugin(),
        social_plugin('abc',edges=(),content_length_limit=-1,config={'social_media':{'recommendation':{'use_embedding_similarity':False}}}),
        social_cognition_plugin(),Plugin('provider',('threads',),install),
    ]
    builder=('social.cognition','input_builder')
    if malformed:
        def broken(ctx):
            original=ctx.require(*builder)
            async def build(session):
                batch=await original(session)
                return replace(batch,messages=[*batch.messages,{'role':'user','content':object()}])
            ctx.provide('builder',build)
        configured.append(Plugin('broken',('social.cognition',),broken))
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
