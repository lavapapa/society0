"""策略、叶服务和实际呈现的组合合同。"""
import pytest
from dataclasses import fields
from society0.kernel.composition import compose
from society0.kernel.interaction import InteractionScope, Moment, Query
from society0.plugins.social import social_plugin, chronological_ranking_plugin, weighted_ranking_plugin
from society0.plugins.social_ranking import Candidate, RankingInput, WeightedRanker, ChronologicalRanker
from tests.primary.test_social_composition import plan

CFG={'social_media':{'recommendation':{'use_embedding_similarity':False,'chronological_weight':0,'engagement_weight':1,'network_weight':0,'similarity_weight':0}}}


def test_public_ranking_dto_has_no_service_permissions_and_keeps_all_candidates():
    source=RankingInput('a',9,(Candidate('old','b',1,engagement_score=5),Candidate('new','c',8),Candidate('own','a',9)),frozenset({'b'}),{},12)
    weighted=WeightedRanker(CFG['social_media']['recommendation']).rank(source)
    chronological=ChronologicalRanker().rank(source)
    assert [p.post_id for p in weighted.items]==['old','new']
    assert [p.post_id for p in chronological.items]==['new','old']
    assert weighted.total==chronological.total==2
    assert weighted.revision==chronological.revision==12
    assert weighted.items[0].score==5
    assert {item.name for item in fields(source)}=={'actor','tick','candidates','following','similarities','revision'}
    assert not hasattr(source,'store') and not hasattr(source,'ctx')


@pytest.mark.asyncio
async def test_explicit_strategy_binding_restore_and_exact_presentation(tmp_path):
    def plugins(strategy):
        rank=weighted_ranking_plugin(name='chosen',config=CFG['social_media']['recommendation']) if strategy=='weighted' else chronological_ranking_plugin(name='chosen')
        return plan(rank,social_plugin('abc',edges=[],config=CFG,ranking=('chosen','rank')))
    async with compose(tmp_path/'run',plugins('weighted')) as host:
        s=host.service('social.presentation','presentation'); info=host.service('interaction','information')
        s.execute('publish_post','b','b',{'content':'old'},1)
        s.execute('publish_post','c','c',{'content':'new'},2)
        s.execute('like_post','a','post_1',{},3)
        page=await info.query(InteractionScope('a',Moment(3,'read')),'/social/feed',Query())
        assert [p['post_id'] for p in page.items]==['post_1','post_2']
        await s.after_tick()
        assert [s.post_details(p)['view_count'] for p in ('post_1','post_2')]==[0,0]
        s.present('a',['post_2'])
        await s.after_tick()
        assert [s.post_details(p)['view_count'] for p in ('post_1','post_2')]==[0,1]
        assert s.recommended_ids('a')==['post_2']
        host.service('storage','store').complete(1)
    async with compose(tmp_path/'restore',plugins('chronological'),source=tmp_path/'run') as host:
        s=host.service('social','mechanism')
        source=await s.recommendation_input('a',4)
        output=host.service('chosen','rank').rank(source)
        assert [p.post_id for p in output.items]==['post_2','post_1']
        assert s.post_details('post_1')['like_count']==1
        assert s.post_details('post_2')['view_count']==1
        assert not hasattr(s,'_operations')
        assert host.service('social.content','content') is s.content
        assert host.service('social.candidates','candidates') is s.candidates


@pytest.mark.asyncio
async def test_cross_domain_writer_failure_rolls_back_every_fact(tmp_path,monkeypatch):
    async with compose(tmp_path/'run',plan(social_plugin('abc',edges=[],config=CFG))) as host:
        s=host.service('social','mechanism');store=host.service('storage','store')
        s.execute('publish_post','b','b',{'content':'original'},1)
        def fail(*args):raise OSError('notification failure')
        monkeypatch.setattr(host.service('social.notifications','notifications'),'notify_in',fail)
        with pytest.raises(OSError,match='notification'):
            s.execute('like_post','a','post_1',{},2)
        assert s.post_details('post_1')['like_count']==0
        assert s.post_details('post_1')['likes']==[]
        assert store.read(lambda r:r.query('SELECT engagement FROM social_posts'))==[(0.0,)]
        assert store.read(lambda r:r.query('SELECT count(*) FROM social_recent_interactions'))==[(0,)]
        assert s.notifications('b')==[]
        with pytest.raises(OSError):s.execute('follow','a','b',{},2)
        assert s.profile('a')['following']==[]
        assert s.profile('b')['followers']==[]


@pytest.mark.asyncio
async def test_leaf_domain_assembly_without_facade_or_interaction(tmp_path):
    from society0.kernel.actors import actor_data_plugin,ActorRecord
    from society0.plugins.social import social_data_plugin,social_notifications_plugin,social_engagement_plugin,social_content_plugin,social_candidates_plugin
    plugins=[actor_data_plugin(records=[ActorRecord(i,'rule') for i in 'ab']),social_data_plugin('ab',edges=[],config=CFG),social_notifications_plugin(),social_engagement_plugin(),social_content_plugin(),social_candidates_plugin()]
    async with compose(tmp_path/'run',plugins) as host:
        content=host.service('social.content','content');store=host.service('storage','store')
        result=store.transaction(lambda w:content.publish_in(w,'b','leaf body',[],1))
        assert result.value=={'post_id':'post_1'}
        assert host.service('social.candidates','candidates').active_pool(2)[0]['post_id']=='post_1'
        assert content.post_details('post_1')['content']=='leaf body'
        with pytest.raises(KeyError):host.service('social','mechanism')


@pytest.mark.asyncio
async def test_global_trending_index_cost_does_not_grow_with_history(tmp_path):
    measured=[]
    for count in (100,10000):
        async with compose(tmp_path/str(count),plan(social_plugin('abc',edges=[],config=CFG))) as host:
            store=host.service('storage','store');candidates=host.service('social.candidates','candidates')
            def seed(w):
                w.executemany('INSERT INTO social_posts VALUES(?,?,?,0,NULL,0,0,0,0,?)',((f'post_{i:08d}',i,'b',float(i)) for i in range(1,count+1)))
                w.execute('UPDATE social_head SET post_count=? WHERE id=1',(count,))
            store.transaction(seed)
            plan_rows=store.read(lambda r:r.query('EXPLAIN QUERY PLAN SELECT id FROM social_posts ORDER BY engagement DESC,created_tick DESC,id DESC LIMIT 2'))
            assert any('pool_engagement' in str(row) for row in plan_rows)
            original=store.read;vm=[0]
            def read(callback,**kwargs):
                def run(view):
                    view._connection.set_progress_handler(lambda:vm.__setitem__(0,vm[0]+1) or False,1)
                    try:return callback(view)
                    finally:view._connection.set_progress_handler(None,0)
                return original(run,**kwargs)
            store.read=read
            assert candidates.trending_ids()==[f'post_{count:08d}',f'post_{count-1:08d}']
            measured.append(vm[0])
    print({'history':[100,10000],'trending_top2_sql_vm':measured})
    assert measured[1]<=measured[0]+10


@pytest.mark.asyncio
async def test_recommendation_leaf_without_presentation_or_content(tmp_path):
    from society0.kernel.actors import ActorRecord,actor_data_plugin
    from society0.plugins.social import social_data_plugin,social_relations_plugin,social_notifications_plugin,social_candidates_plugin,social_recommendation_plugin
    plugins=[actor_data_plugin(records=[ActorRecord(i,'unused') for i in 'ab']),social_data_plugin('ab',edges=[]),social_notifications_plugin(),social_relations_plugin(),social_candidates_plugin(),chronological_ranking_plugin(),social_recommendation_plugin(ranking=('social.chronological','rank'))]
    async with compose(tmp_path/'run',plugins) as host:
        store=host.service('storage','store')
        def seed(w):
            w.execute("INSERT INTO social_posts VALUES('p',1,'b',1,NULL,0,0,0,0,0)")
            w.execute('UPDATE social_head SET post_count=1')
        store.transaction(seed)
        source,output=await host.service('social.recommendation','recommendation').recommendation('a',2)
        assert source.candidates[0].post_id=='p'
        assert [p.post_id for p in output.items]==['p']
        for name,service in [('social.presentation','presentation'),('social.content','content'),('social.exposure','exposure'),('interaction','information')]:
            with pytest.raises(KeyError):host.service(name,service)


@pytest.mark.asyncio
async def test_bounded_recommendation_cache_eviction_preserves_results(tmp_path):
    from society0.kernel.plugins import Plugin
    class Counting(ChronologicalRanker):
        calls=0
        def rank(self,source):
            self.calls+=1
            return super().rank(source)
    ranker=Counting()
    plugins=plan(Plugin('chosen',install=lambda ctx:ctx.provide('rank',ranker)),social_plugin('abc',edges=[],ranking=('chosen','rank'),cache_capacity=1))
    async with compose(tmp_path/'run',plugins) as host:
        s=host.service('social','mechanism');recs=host.service('social.recommendation','recommendation')
        for tick in range(3):s.execute('publish_post','b','b',{'content':str(tick)},tick)
        first=await recs.recommendation('a',3)
        assert (await recs.recommendation('a',3))==first
        await recs.recommendation('c',3)
        assert (await recs.recommendation('a',3))==first
        assert ranker.calls==3
        s.present('a',['post_1'],recommended=False)
        s.set_recommended('a',['post_3','post_1'])
        await s.after_tick()
        assert s.recommended_ids('a')==['post_3','post_1']
        assert s.post_details('post_1')['view_count']==1
        assert s.post_details('post_3')['view_count']==0
        await recs.recommendation('a',3)
        assert ranker.calls==4


@pytest.mark.asyncio
async def test_feed_pages_only_materialize_selected_metadata(tmp_path,monkeypatch):
    import society0.plugins.social as module
    original=module.asdict;counts=[]
    def observed(value):
        if isinstance(value,Candidate):counts.append(value.post_id)
        return original(value)
    monkeypatch.setattr(module,'asdict',observed)
    async with compose(tmp_path/'run',plan(social_plugin('abc',edges=[]))) as host:
        s=host.service('social','mechanism');info=host.service('interaction','information')
        for tick in range(6):s.execute('publish_post','b','b',{'content':str(tick)},tick)
        scope=InteractionScope('a',Moment(7,'read'))
        first=await info.query(scope,'/social/feed',Query(limit=1))
        second=await info.query(scope,'/social/feed',Query(limit=1,cursor=first.next_cursor))
        assert counts==[first.items[0]['post_id'],second.items[0]['post_id']]
        assert first.total==second.total==6


@pytest.mark.asyncio
async def test_external_strategy_and_observer_cannot_mutate_shared_dto(tmp_path):
    from society0.kernel.plugins import Plugin
    class AttemptedMutation(ChronologicalRanker):
        def rank(self,source):
            with pytest.raises(TypeError):source.similarities['post_1']=99
            with pytest.raises(TypeError):source.by_id['post_1']=source.candidates[0]
            return super().rank(source)
    plugins=plan(Plugin('chosen',install=lambda ctx:ctx.provide('rank',AttemptedMutation())),social_plugin('abc',edges=[],ranking=('chosen','rank')))
    async with compose(tmp_path/'run',plugins) as host:
        s=host.service('social','mechanism');info=host.service('interaction','information')
        for tick in range(3):s.execute('publish_post','b','b',{'content':str(tick)},tick)
        scope=InteractionScope('a',Moment(4,'read'))
        first=await info.query(scope,'/social/feed',Query(limit=1))
        source,output=await host.service('social.recommendation','recommendation').recommendation('a',4)
        with pytest.raises(TypeError):output.items[1].components['total_score']=999
        second=await info.query(scope,'/social/feed',Query(limit=1,cursor=first.next_cursor))
        assert first.items[0]['post_id']=='post_3'
        assert second.items[0]['post_id']=='post_2'
        assert second.items[0]['_recommendation_score']['total_score']==1.0
        assert source.similarities=={}


def test_ranking_dto_copies_mapping_boundary_without_mutable_aliases():
    from society0.plugins.social_ranking import RankedPost
    similarities={'p':0.5};parts={'total_score':1.0}
    source=RankingInput('a',1,(Candidate('p','b',0),),frozenset(),similarities,3)
    output=RankedPost('p',1.0,parts)
    similarities['p']=9;parts['total_score']=99
    assert source.similarities['p']==0.5
    assert output.components['total_score']==1.0
