"""社交插件的事实、投影与共享环境消费者。"""
import pytest
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin,InteractionScope,Moment,Ref,Query
from society0.plugins.social import social_plugin


def plan(*mechanisms):
    return [actor_plugin({'rule':lambda record:None},records=[ActorRecord(i,'rule') for i in 'abc']),
            interaction_plugin(lambda *args:True),*mechanisms]


@pytest.mark.asyncio
async def test_social_actions_facts_projection_notifications_restore(tmp_path):
    plugins=plan(social_plugin('abc',name='public',edges=[('a','b')],content_length_limit=-1))
    async with compose(tmp_path/'run',plugins) as host:
        social=host.service('public','mechanism'); actions=host.service('interaction','actions')
        a=InteractionScope('a',Moment(1,'acting')); b=InteractionScope('b',Moment(1,'acting'))
        published=await actions.invoke(a,'public.publish_post',Ref('public','participants','a'),{'content':'完整帖子🙂','tags':['news']})
        assert published.status=='completed'
        key=published.value['post_id']; target=Ref('public','posts',key)
        assert (await actions.invoke(b,'public.like_post',target,{})).status=='completed'
        assert (await actions.invoke(b,'public.like_post',target,{})).value['changed'] is False
        assert (await actions.invoke(b,'public.comment',target,{'content':'完整评论'})).status=='completed'
        repost=await actions.invoke(b,'public.repost',target,{'commentary':'读后意见'})
        assert repost.status=='completed'
        assert (await actions.invoke(b,'public.follow',Ref('public','participants','a'),{})).status=='completed'
        detail=social.post_details(key)
        assert detail['likes']==['b'] and detail['reply_count']==1 and detail['repost_count']==1
        assert detail['replies'][0]['content']=='完整评论'
        assert social.profile('a')['followers']==['b']
        notices=social.notifications('a')
        assert [n['type'] for n in notices]==['post_like','post_comment','post_repost','new_follower']
        assert social.notifications('a',consume=True)==notices
        assert social.notifications('a')==[]
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restore',plugins,source=tmp_path/'run') as host:
        social=host.service('public','mechanism')
        assert social.post_details(key)==detail
        assert social.notifications('a')==[]
        assert social.notifications('a',include_consumed=True)==notices
        assert social.post_details(repost.value['post_id'])['content']=='读后意见\n\n--- 原帖 '+key+' ---\n完整帖子🙂'


@pytest.mark.asyncio
async def test_social_two_instances_original_body_and_rejections(tmp_path):
    async with compose(tmp_path/'run',plan(social_plugin('abc',name='one'),social_plugin('abc',name='two'))) as host:
        actions=host.service('interaction','actions'); info=host.service('interaction','information')
        scope=InteractionScope('a',Moment(1,'acting'))
        own=Ref('one','participants','a')
        assert (await actions.invoke(scope,'one.publish_post',own,{'content':'x'*251})).status=='rejected'
        assert (await actions.invoke(scope,'one.follow',own,{})).status=='rejected'
        result=await actions.invoke(scope,'one.publish_post',own,{'content':'原文🙂','tags':[]})
        page=await info.query(scope,'/one/posts',Query())
        assert page.total==1 and page.items[0]['ref'].key==result.value['post_id']
        assert (await info.query(scope,'/two/posts',Query())).total==0
        read=await info.read(scope,'/one/content/'+result.value['post_id'],offset=0,size=100)
        assert read.data.decode()=='原文🙂'
        assert (await actions.invoke(scope,'one.publish_post',Ref('one','participants','b'),{'content':'fake'})).status=='rejected'


@pytest.mark.parametrize('distribution',[
    {'type':'random','params':{'connection_probability':0.3}},
    {'type':'small_world','params':{'k_neighbors':4,'rewiring_probability':0.2}},
    {'type':'scale_free','params':{'m_edges':2}},
    {'type':'complete','params':{}},
    {'type':'cv_targeted','params':{'target_cv_mean':0.6,'max_iterations':25}},
])
@pytest.mark.parametrize('directed',[True,False])
def test_social_topology_matches_existing_algorithm(distribution,directed):
    import random
    from society0.plugins.social_models import SocialNetworkConfig
    from tests.reference.builtin_algorithms import SocialNetworkEnv
    from society0.plugins.social_topology import generate_topology
    from types import SimpleNamespace
    config=SocialNetworkConfig(distribution=distribution,is_directed=directed)
    members=[str(i) for i in range(12)]
    proxy=SimpleNamespace(_config=config)
    for name in ('_create_traditional_graph','_create_cv_targeted_graph','_create_base_graph_for_cv','_get_base_distribution','_optimize_cv_distribution','_increase_mutual_connections','_decrease_mutual_connections','_calculate_cv_for_node','_calculate_average_cv_for_graph'):
        setattr(proxy,name,getattr(SocialNetworkEnv,name).__get__(proxy))
    saved=random.getstate()
    try:
        random.seed(913)
        original=SocialNetworkEnv._generate_topology(proxy,members)
    finally:random.setstate(saved)
    actual=generate_topology(members,config,seed=913)
    assert list(actual.nodes)==list(original.nodes)
    assert list(actual.edges)==list(original.edges)


@pytest.mark.asyncio
async def test_social_hot_like_does_not_capture_cold_post_body(tmp_path):
    async with compose(tmp_path/'run',plan(social_plugin('abc',content_length_limit=-1))) as host:
        social=host.service('social','mechanism'); store=host.service('storage','store')
        result=social.execute('publish_post','a','a',{'content':'冷正文🙂'*300000},0)
        store.complete(1)
        social.execute('like_post','b',result.value['post_id'],{},1)
        assert store._session.memory_used<65536


@pytest.mark.asyncio
async def test_social_topology_configuration_is_persisted_and_not_regenerated_on_restore(tmp_path):
    from society0.plugins.social_models import SocialNetworkConfig
    config=SocialNetworkConfig(distribution={'type':'complete'},is_directed=False)
    async with compose(tmp_path/'run',plan(social_plugin('abc',config=config,seed=13))) as host:
        social=host.service('social','mechanism')
        assert social.profile('a')['following']==['b','c']
        social.execute('unfollow','a','b',{},1)
        host.service('storage','store').complete(1)
    changed=SocialNetworkConfig(distribution={'type':'random','params':{'connection_probability':0}},is_directed=True)
    async with compose(tmp_path/'restored',plan(social_plugin('abc',config=changed,seed=99)),source=tmp_path/'run') as host:
        social=host.service('social','mechanism')
        assert social.profile('a')['following']==['c']
        assert social.config==config


@pytest.mark.asyncio
async def test_social_active_pool_and_scores_match_old_semantics_without_body_scan(tmp_path):
    from society0.plugins.social_models import SocialNetworkConfig
    from tests.reference.builtin_algorithms import SocialNetworkEnv
    from types import SimpleNamespace
    config=SocialNetworkConfig(social_media={'recommendation':{'full_scan_until':5,'recent_keep_count':3,'top_engagement_keep_count':2,'min_lifetime_ticks':2,'use_embedding_similarity':False}})
    async with compose(tmp_path/'run',plan(social_plugin('abc',edges=[('a','b')],config=config))) as host:
        social=host.service('social','mechanism'); posts={}
        for index in range(12):
            actor='b' if index%2 else 'c'
            key=social.execute('publish_post',actor,actor,{'content':str(index)},index).value['post_id']
            if index%3==0:social.execute('like_post','a',key,{},index)
            posts[key]=social.post_details(key)
        features={key:{'created_tick':p['created_tick'],'engagement_score':float(len(p['likes']))} for key,p in posts.items()}
        expected=SocialNetworkEnv._build_active_pool_ids(SimpleNamespace(_config=config),posts,features,12)
        pool=social.active_pool(12)
        assert [p['post_id'] for p in pool]==expected
        graph=__import__('networkx').DiGraph([('a','b')])
        import math
        for key,feature in features.items():feature['time_score']=math.exp(-max(0,12-feature['created_tick'])/config.social_media.recommendation.time_decay_hours)
        proxy=SimpleNamespace(_config=config,graph=graph,_get_recommendation_cache=lambda:{'post_features':features})
        original=SocialNetworkEnv._score_posts(proxy,SimpleNamespace(id='a'),[posts[k] for k in expected],{})
        ranked=social.rank('a',12)
        assert [(p['post_id'],p['_recommendation_score']) for p in ranked]==[(p['post_id'],p['_recommendation_score']) for p in original]
        assert all('content' not in p for p in pool)


@pytest.mark.asyncio
async def test_social_embedding_batch_original_vectors_restore_and_exposure_boundary(tmp_path):
    from society0.kernel.plugins import Plugin
    from tests.primary.test_kernel_memory import Embed,Client
    from society0.plugins.social_models import SocialNetworkConfig
    embed=Embed();client=Client()
    def resources(ctx):
        from types import SimpleNamespace
        ctx.provide('embeddings',{'default':SimpleNamespace(embed=embed)});ctx.provide('vector_client',client)
    config=SocialNetworkConfig(social_media={'recommendation':{'post_count':2,'use_embedding_similarity':True}})
    plugins=plan(Plugin('vectors',install=resources),social_plugin('abc',edges=[],config=config,embedding=('vectors','default'),vector_client=('vectors','vector_client')))
    async with compose(tmp_path/'run',plugins) as host:
        social=host.service('social','mechanism')
        first=social.execute('publish_post','b','b',{'content':'first','tags':['x']},0).value['post_id']
        second=social.execute('publish_post','c','c',{'content':'second'},0).value['post_id']
        assert embed.calls==[]
        await social.after_tick()
        assert embed.calls==[['first\nTags: #x','second']]
        preview=await social.recommended_feed('a',1,record_impressions=False,query='my preference')
        assert len(preview)==2
        assert social.post_details(first)['view_count']==0
        shown=await social.recommended_feed('a',1,query='my preference')
        assert [p['post_id'] for p in shown]==[p['post_id'] for p in preview]
        assert social.post_details(first)['view_count']==0
        await social.after_tick()
        assert social.post_details(first)['view_count']==1
        assert social.post_details(second)['view_count']==1
        assert social.recommended_ids('a')==[p['post_id'] for p in shown]
        host.service('storage','store').complete(1)
    embed.calls.clear();client.collections.clear()
    async with compose(tmp_path/'restore',plugins,source=tmp_path/'run') as host:
        social=host.service('social','mechanism')
        result=await social.recommended_feed('a',1,record_impressions=False,query='my preference')
        assert embed.calls==[['my preference']]
        assert [(p['post_id'],p['_recommendation_score']) for p in result]==[(p['post_id'],p['_recommendation_score']) for p in preview]


@pytest.mark.asyncio
async def test_social_preference_preserves_recent_interaction_ties_and_read_actions(tmp_path):
    from society0.plugins.social_models import SocialNetworkConfig
    from tests.reference.builtin_algorithms import SocialNetworkEnv
    from types import SimpleNamespace
    cfg=SocialNetworkConfig(social_media={'recommendation':{'use_embedding_similarity':False,'interaction_limit':4,'include_following_in_query':True}})
    async with compose(tmp_path/'run',plan(social_plugin('abc',config=cfg,edges=[('a','b')]))) as host:
        social=host.service('social','mechanism')
        for actor in 'bca':social.execute('publish_post',actor,actor,{'content':actor+' body','tags':['x']},1)
        social.execute('comment','a','post_2',{'content':'comment on two'},2)
        social.execute('like_post','a','post_1',{},2)
        social.execute('comment','a','post_1',{'content':'comment on one'},2)
        social.execute('like_post','a','post_2',{},2)
        posts={key:social.post_details(key) for key in ('post_1','post_2','post_3')}
        for key,post in posts.items():
            post['like_events']=[{'agent_id':x,'created_tick':2} for x in post['likes']]
        proxy=SimpleNamespace(_config=cfg,_posts_view=lambda:posts,
                              _collect_recent_posts_for_agent=lambda actor,limit:[posts['post_3']],
                              graph=__import__('networkx').DiGraph([('a','b')]))
        proxy._collect_recent_interactions_for_agent=SocialNetworkEnv._collect_recent_interactions_for_agent.__get__(proxy)
        expected=SocialNetworkEnv._build_agent_preference_text(proxy,SimpleNamespace(id='a',get_raw_data=lambda:{'persona':''}))
        assert social.preference_text('a')==expected
        actions=host.service('interaction','actions');scope=InteractionScope('a',Moment(2,'read'))
        info=host.service('interaction','information')
        import json
        result=json.loads((await info.read(scope,'/social/post_details/post_1')).data)
        assert result['content']=='b body' and result==social.post_details('post_1')
        profile=json.loads((await info.read(scope,'/social/profiles/b')).data)
        assert profile['posts']==['post_1'] and profile==social.profile('b')
        assert (await actions.invoke(scope,'social.get_post_details',Ref('social','posts','post_1'),{})).status=='rejected'
        assert (await actions.invoke(scope,'social.get_agent_profile',Ref('social','participants','b'),{})).status=='rejected'
        trending=await actions.invoke(scope,'social.get_trending_posts',Ref('social','participants','a'),{})
        assert len(trending.value['posts'])==2
        assert social.post_details('post_1')['view_count']==0
        await social.after_tick()
        assert social.post_details('post_1')['view_count']==1


@pytest.mark.asyncio
async def test_social_actual_chroma_rebuild_preserves_ranking_without_embedding_posts(tmp_path):
    import chromadb
    from society0.kernel.plugins import Plugin
    from tests.primary.test_kernel_memory import Embed
    from society0.plugins.social_models import SocialNetworkConfig
    embed=Embed();client=chromadb.PersistentClient(path=str(tmp_path/'vectors'))
    def resources(ctx):
        from types import SimpleNamespace
        ctx.provide('embeddings',{'default':SimpleNamespace(embed=embed)});ctx.provide('vector_client',client)
    cfg=SocialNetworkConfig(social_media={'recommendation':{'post_count':2,'use_embedding_similarity':True}})
    plugins=plan(Plugin('vectors',install=resources),social_plugin('abc',edges=[],config=cfg,embedding=('vectors','default'),vector_client=('vectors','vector_client')))
    async with compose(tmp_path/'run',plugins) as host:
        social=host.service('social','mechanism')
        for actor,body in [('b','abc'),('c','abcde')]:social.execute('publish_post',actor,actor,{'content':body},0)
        before=await social.recommended_feed('a',1,record_impressions=False,query='abcd')
        host.service('storage','store').complete(1)
    embed.calls.clear()
    async with compose(tmp_path/'restored',plugins,source=tmp_path/'run') as host:
        after=await host.service('social','mechanism').recommended_feed('a',1,record_impressions=False,query='abcd')
        assert embed.calls==[['abcd']]
        assert before==after


@pytest.mark.asyncio
async def test_social_intervention_trending_and_full_notification_content_survive_restore(tmp_path):
    from society0.plugins.social_models import SocialNetworkConfig
    import random
    cfg=SocialNetworkConfig(social_media={'recommendation':{'use_embedding_similarity':False},'content_length_limit':-1})
    plugins=plan(social_plugin('abc',edges=[],config=cfg))
    async with compose(tmp_path/'run',plugins) as host:
        social=host.service('social','mechanism')
        first=social.execute('publish_post','a','a',{'content':'#policy source \n\n--- 原贴 post_0 ---\nold'},1).value['post_id']
        second=social.execute('repost','b',first,{'commentary':'my comment'},1).value['post_id']
        assert social.post_details(second)['content']=='my comment\n\n--- 原帖 '+first+' ---\n#policy source'
        social.execute('comment','b',first,{'content':'完整回复内容'},1)
        notes=social.notifications('a')
        assert notes[0]['data']['commentary_preview']=='my comment'
        assert notes[1]['data']['comment_preview']=='完整回复内容'
        result=social.intervene(1,target_hashtag='#policy',draw=random.Random(8).random,intervention_rate=1,tag_to_apply='checked')
        assert result['flagged_post_ids']==[first,second]
        assert social.intervene(1,target_hashtag='#policy',draw=random.Random(8).random,intervention_rate=1,tag_to_apply='checked')['posts_flagged']==0
        assert social.post_details(first)['special_tags']==['checked']
        assert social.update_trending_topics()==[first,second]
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restore',plugins,source=tmp_path/'run') as host:
        social=host.service('social','mechanism')
        assert social.post_details(first)['special_tags']==['checked']
        assert social.trending_ids()==[first,second]

@pytest.mark.asyncio
async def test_social_feed_pages_expose_every_candidate_and_bind_business_version(tmp_path):
    import json
    config={'social_media':{'recommendation':{'use_embedding_similarity':False,'post_count':1}}}
    async with compose(tmp_path/'run',plan(social_plugin('abc',config=config,edges=[]))) as host:
        social=host.service('social','mechanism');info=host.service('interaction','information')
        store=host.service('storage','store')
        for i in range(3):social.execute('publish_post','b','b',{'content':'原文'+str(i)},i)
        scope=InteractionScope('a',Moment(3,'acting'))
        page=await info.query(scope,'/social/feed',Query(limit=1))
        assert page.total==3 and len(page.items)==1
        assert page.items[0]['ref'].kind=='posts' and 'content' not in page.items[0]
        cursor=json.loads(json.dumps(page.next_cursor))
        store.transaction(lambda w:w.execute("UPDATE actor_counts SET total=total"))
        seen=[page.items[0]['post_id']]
        while cursor:
            page=await info.query(scope,'/social/feed',Query(limit=1,cursor=cursor))
            seen.extend(item['post_id'] for item in page.items);cursor=page.next_cursor
        assert seen==[item['post_id'] for item in social.rank('a',3)]
        first=await info.query(scope,'/social/feed',Query(limit=1))
        social.execute('like_post','c',seen[0],{},3)
        with pytest.raises(ValueError,match='cursor'):
            await info.query(scope,'/social/feed',Query(limit=1,cursor=first.next_cursor))
        body=await info.read(scope,first.items[0]['content_path'],size=100)
        assert body.data.decode().startswith('原文')
        assert (await info.query(scope,'/social/feed',Query(limit=1))).total==3

@pytest.mark.asyncio
async def test_social_notification_original_document_is_recipient_scoped(tmp_path):
    import json
    async with compose(tmp_path/'run',plan(social_plugin('abc',edges=[]))) as host:
        social=host.service('social','mechanism');info=host.service('interaction','information')
        actions=host.service('interaction','actions')
        key=social.execute('publish_post','a','a',{'content':'完整帖子'},1).value['post_id']
        social.execute('comment','b',key,{'content':'回复原文🙂'},1)
        a=InteractionScope('a',Moment(1,'p'));b=InteractionScope('b',Moment(1,'p'))
        page=await info.query(a,'/social/notifications',Query())
        identifier=page.items[0]['id']
        body=await info.read(a,'/social/notification_data/'+str(identifier),size=1000)
        assert json.loads(body.data)==social.notifications('a')[0]['data']
        from society0.kernel.interaction import Unavailable
        with pytest.raises(Unavailable):await info.read(b,'/social/notification_data/'+str(identifier))
        result=await actions.invoke(a,'social.get_notifications',Ref('social','participants','a'),{})
        assert result.status=='completed' and len(result.value['notifications'])==1
        assert (await info.query(a,'/social/notifications',Query())).total==0
        assert json.loads((await info.read(a,'/social/notification_data/'+str(identifier))).data)==result.value['notifications'][0]['data']

@pytest.mark.asyncio
async def test_social_public_profile_has_subjective_public_fields_and_recent_original_preview(tmp_path):
    actors=actor_plugin({'rule':lambda record:None},records=[
        ActorRecord('a','rule',persona='私有画像',state={'interests':['经济','技术'],'mood':'平静'},config={'type':'researcher','archetype':'llm'}),
        ActorRecord('b','rule')])
    async with compose(tmp_path/'run',[actors,interaction_plugin(lambda *args:True),social_plugin('ab',edges=[],content_length_limit=-1)]) as host:
        social=host.service('social','mechanism')
        text='🙂汉字'*1000
        social.execute('publish_post','a','a',{'content':text},1)
        result=social.profile('a')
        assert result['type']=='researcher' and result['archetype']=='llm'
        assert result['interests']==['经济','技术'] and result['mood']=='平静'
        assert 'persona' not in result
        assert result['recent_posts'][0]['content_preview']==text[:80]+'...'
        assert result['recent_posts'][0]['content_path']=='/social/content/post_1'

@pytest.mark.asyncio
async def test_social_runtime_complete_preserves_exposure_and_failed_step_restores_prior_world(tmp_path):
    from types import SimpleNamespace
    from society0.kernel.runtime import Runtime,Actor,DriverResult,Phase
    config={'social_media':{'recommendation':{'use_embedding_similarity':False}}}
    plugins=plan(social_plugin('abc',config=config,edges=[]))
    async with compose(tmp_path/'run',plugins) as host:
        social=host.service('social','mechanism');store=host.service('storage','store')
        async def publish(session):
            await session.actions.invoke('social.publish_post',Ref('social','participants','b'),{'content':'step '+str(session.moment.time)})
            return DriverResult('completed')
        async def read(session):
            page=await session.information.query('/social/feed',Query())
            assert page.total==int(session.moment.time)
            await social.recommended_feed('a',session.moment.time)
            return DriverResult('completed')
        runtime=Runtime([Actor('b',SimpleNamespace(run=publish)),Actor('a',SimpleNamespace(run=read))],
            information=host.service('interaction','information'),actions=host.service('interaction','actions'),store=store)
        async def phase(ctx):
            ctx.activate('b');await ctx.drain();ctx.activate('a')
        async def finish(ctx):await social.after_tick()
        await runtime.run_step(1,1,[Phase('decision',phase),Phase('social_finish',finish)])
        expected=social.post_details('post_1');assert expected['view_count']==1
        async def fail(ctx):raise OSError('output unavailable')
        with pytest.raises(OSError):await runtime.run_step(2,2,[Phase('decision',phase),Phase('social_finish',finish),Phase('fail',fail)])
        assert store.complete_step==1
    async with compose(tmp_path/'restored',plugins,source=tmp_path/'run') as host:
        social=host.service('social','mechanism')
        assert social.post_details('post_1')==expected
        assert len(social.active_pool(1))==1
        assert social.recommended_ids('a')==['post_1']

@pytest.mark.asyncio
async def test_social_vector_watermark_failure_reuses_persisted_original_vectors(tmp_path):
    from types import SimpleNamespace
    from society0.kernel.plugins import Plugin
    from tests.primary.test_kernel_memory import Embed,Client,Collection
    class FailingCollection(Collection):
        def __init__(self,metadata):super().__init__(metadata);self.fail_watermark=True
        def modify(self,metadata):
            if self.fail_watermark:
                self.fail_watermark=False
                raise OSError('watermark write failed')
            super().modify(metadata)
    class FailingClient(Client):
        def get_or_create_collection(self,name,metadata=None,embedding_function=None):
            return self.collections.setdefault(name,FailingCollection(metadata))
    embed=Embed();client=FailingClient()
    def install(ctx):
        ctx.provide('embeddings',{'default':SimpleNamespace(embed=embed)});ctx.provide('client',client)
    plugins=plan(Plugin('vectors',install=install),social_plugin('abc',edges=[],embedding=('vectors','default'),vector_client=('vectors','client')))
    async with compose(tmp_path/'run',plugins) as host:
        social=host.service('social','mechanism')
        social.execute('publish_post','b','b',{'content':'original complete body'},1)
        with pytest.raises(OSError,match='watermark'):await social.after_tick()
        assert embed.calls==[['original complete body']]
        await social.after_tick()
        assert embed.calls==[['original complete body']]
        assert list(social._collection.rows)==['post_1']
        assert social._collection.metadata['through']==1
        host.service('storage','store').complete(1)

@pytest.mark.asyncio
async def test_social_consecutive_feed_pages_reuse_same_semantic_query(tmp_path):
    from types import SimpleNamespace
    from society0.kernel.plugins import Plugin
    from tests.primary.test_kernel_memory import Embed,Client
    embed=Embed();client=Client()
    def install(ctx):
        ctx.provide('embeddings',{'default':SimpleNamespace(embed=embed)});ctx.provide('client',client)
    plugins=plan(Plugin('vectors',install=install),social_plugin('abc',edges=[],embedding=('vectors','default'),vector_client=('vectors','client')))
    async with compose(tmp_path/'run',plugins) as host:
        social=host.service('social','mechanism');info=host.service('interaction','information')
        for text in ('one','two','three'):social.execute('publish_post','b','b',{'content':text},1)
        scope=InteractionScope('a',Moment(1,'p'))
        first=await info.query(scope,'/social/feed',Query(limit=1))
        await info.query(scope,'/social/feed',Query(limit=1,cursor=first.next_cursor))
        assert len(embed.calls)==2  # 一次帖子批量，一次完整偏好查询。
        social.execute('publish_post','b','b',{'content':'four'},1)
        await info.query(scope,'/social/feed',Query(limit=1))
        assert len(embed.calls)==4

@pytest.mark.asyncio
async def test_social_fixed_active_pool_queries_do_not_replay_long_history(tmp_path):
    measured=[]
    config={'social_media':{'recommendation':{'use_embedding_similarity':False,'full_scan_until':10,
        'recent_keep_count':5,'top_engagement_keep_count':5,'min_lifetime_ticks':1}}}
    for count in (100,10000):
        async with compose(tmp_path/str(count),plan(social_plugin('abc',config=config,edges=[]))) as host:
            social=host.service('social','mechanism');store=host.service('storage','store')
            def seed(w):
                w.executemany('INSERT INTO social_posts VALUES(?,?,?,0,NULL,0,0,0,0,0)',
                    ((f'post_{i:08d}',i,'b') for i in range(1,count+1)))
                w.execute('UPDATE social_head SET post_count=? WHERE id=1',(count,))
            store.transaction(seed)
            original=store.read;vm=[0]
            def read(callback,**kwargs):
                def run(view):
                    view._connection.set_progress_handler(lambda:vm.__setitem__(0,vm[0]+1) or False,1)
                    return callback(view)
                return original(run,**kwargs)
            store.read=read
            assert len(social.active_pool(100))==5
            measured.append(vm[0])
    print({'history':[100,10000],'active_pool_sql_vm':measured})
    assert measured[1]<measured[0]*1.2+30


@pytest.mark.asyncio
async def test_social_read_views_range_revision_authorization_and_restore(tmp_path):
    import json
    from society0.kernel.interaction import Unavailable
    plugins=plan(social_plugin('abc',content_length_limit=-1))
    async with compose(tmp_path/'run',plugins) as host:
        social=host.service('social','mechanism');info=host.service('interaction','information')
        social.execute('publish_post','b','b',{'content':'完整正文🙂'*20000,'tags':['x']},1)
        social.execute('comment','a','post_1',{'content':'完整评论'},2)
        social.execute('like_post','a','post_1',{},2)
        scope=InteractionScope('a',Moment(2,'read'))
        directory=await info.list_files(scope,'/social')
        assert {'/social/post_details','/social/profiles'} <= {item['path'] for item in directory.items}
        path='/social/post_details/post_1';first=await info.read(scope,path,size=1024)
        provider=info._mounts['/social'];source=provider._record_source[1]
        parts=[first.data];offset=first.next_offset
        while offset is not None:
            chunk=await info.read(scope,path,offset=offset,size=1024,expected_revision=first.revision)
            assert provider._record_source[1] is source
            parts.append(chunk.data);offset=chunk.next_offset
        result=json.loads(b''.join(parts));assert result==social.post_details('post_1')
        with pytest.raises(Unavailable):await info.read(InteractionScope('outsider',Moment(2,'read')),path)
        social.execute('comment','c','post_1',{'content':'新评论'},2)
        with pytest.raises(ValueError):await info.read(scope,path,offset=1024,expected_revision=first.revision)
        saved=social.post_details('post_1');host.service('storage','store').complete(1)
    async with compose(tmp_path/'restore',plugins,source=tmp_path/'run') as host:
        assert json.loads((await host.service('interaction','information').read(InteractionScope('a',Moment(3,'read')),path,size=1000000)).data)==saved
