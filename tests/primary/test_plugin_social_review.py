"""社交插件异步身份和共享资源收尾的非作者消费者。"""
import asyncio
from types import SimpleNamespace
import pytest
from society0.kernel.plugins import Plugin
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin,InteractionScope,Moment,Query
from society0.plugins.social import social_plugin
from tests.primary.test_kernel_memory import Client


def plugins(embed, *, closing=None):
    def install(ctx):
        ctx.provide('embeddings',{'default':SimpleNamespace(embed=embed)})
        ctx.provide('client',Client())
        if closing is not None:ctx.on_close(closing)
    return [actor_plugin({'rule':lambda record:None},records=[ActorRecord(i,'rule',persona='old') for i in 'ab']),
            interaction_plugin(lambda *args:True),Plugin('vectors',install=install),
            social_plugin('ab',edges=[],config={'social_media':{'recommendation':{'use_embedding_similarity':True}}},embedding=('vectors','default'),vector_client=('vectors','client'))]


@pytest.mark.asyncio
async def test_review_query_embedding_wait_cannot_label_old_preference_as_new_revision(tmp_path):
    entered=asyncio.Event();release=asyncio.Event();wait_once=True
    async def embed(texts,*,metadata):
        nonlocal wait_once
        if metadata['purpose']=='social_recommendation' and wait_once:
            wait_once=False;entered.set();await release.wait()
        return [[0.0 if 'old' in text else 1.0,0.0] for text in texts]
    async with compose(tmp_path/'run',plugins(embed)) as host:
        social=host.service('social','mechanism');info=host.service('interaction','information')
        for text in ('old','new'):social.execute('publish_post','b','b',{'content':text},1)
        scope=InteractionScope('a',Moment(1,'acting'))
        task=asyncio.create_task(info.query(scope,'/social/feed',Query(limit=1)))
        await entered.wait()
        host.service('actors','actors').update('a',persona='new')
        release.set()
        try:first=await task
        except ValueError as error:
            assert 'revision' in str(error) or 'changed' in str(error)
            return
        current=await info.query(scope,'/social/feed',Query(limit=1))
        assert first.items[0]['post_id']==current.items[0]['post_id'], 'stale preference ranking published under current revision'


@pytest.mark.asyncio
async def test_review_host_drains_social_consumer_before_shared_resources_close(tmp_path):
    entered=asyncio.Event();at_resource_close=[];task=None
    async def embed(texts,*,metadata):
        entered.set()
        await asyncio.Event().wait()
    async def closing():at_resource_close.append(task.done())
    try:
        async with compose(tmp_path/'run',plugins(embed,closing=closing)) as host:
            social=host.service('social','mechanism')
            social.execute('publish_post','b','b',{'content':'original'},1)
            task=asyncio.create_task(social.recommended_feed('a',1))
            await entered.wait()
        assert at_resource_close==[True], 'shared resource owner closed while Social consumer remained active'
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
