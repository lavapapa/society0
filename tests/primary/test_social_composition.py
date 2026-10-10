import pytest
from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin
from society0.plugins.social import social_plugin


def plan(*plugins):
    return [actor_plugin({'rule':lambda record:None},records=[ActorRecord(i,'rule') for i in 'abc']),interaction_plugin(lambda *args:True),*plugins]


def test_semantic_resources_are_checked_when_recipe_is_built():
    with pytest.raises(ValueError,match='semantic.*resources'):
        social_plugin('abc',edges=[],config={'social_media':{'recommendation':{'use_embedding_similarity':True}}})


def test_recipe_has_independent_owners():
    plugin=social_plugin('abc',edges=[],config={'social_media':{'recommendation':{'use_embedding_similarity':False}}})
    assert {p.name for p in plugin.includes} >= {'social.data','social.content','social.relations','social.notifications','social.exposure','social.candidates','social.weighted','social.presentation'}
    assert 'social.semantic' not in {p.name for p in plugin.includes}


@pytest.mark.asyncio
async def test_trending_global_top_two_outside_active_pool(tmp_path):
    cfg={'social_media':{'recommendation':{'use_embedding_similarity':False,'full_scan_until':1,'recent_keep_count':1,'top_engagement_keep_count':1,'min_lifetime_ticks':0}}}
    async with compose(tmp_path/'run',plan(social_plugin('abc',edges=[],config=cfg))) as host:
        s=host.service('social','mechanism')
        for tick in range(3):s.execute('publish_post','b','b',{'content':str(tick)},tick)
        s.execute('comment','a','post_1',{'content':'one'},3)
        s.execute('like_post','a','post_2',{},3)
        assert [p['post_id'] for p in s.trending(10)]==['post_1','post_2']
        assert len(s.active_pool(10))==2
        assert s.post_details('post_1')['view_count']==0


@pytest.mark.asyncio
async def test_plain_social_does_not_import_optional_graph_or_vector_dependencies(tmp_path,monkeypatch):
    import builtins
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name.split('.')[0] in {'networkx','chromadb'} or name.endswith('social_semantic'):
            raise AssertionError('optional dependency imported: '+name)
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    cfg={'social_media':{'recommendation':{'use_embedding_similarity':False}}}
    async with compose(tmp_path/'run',plan(social_plugin('abc',config=cfg))) as host:
        s=host.service('social','mechanism')
        s.execute('publish_post','b','b',{'content':'plain'},1)
        assert (await s.recommended_feed('a',1,record_impressions=False))[0]['post_id']=='post_1'


@pytest.mark.asyncio
@pytest.mark.parametrize('directed',[False,True])
@pytest.mark.parametrize('distribution',[{'type':'random','params':{'connection_probability':0.6}},{'type':'complete'}])
async def test_lightweight_topology_preserves_seeded_edges(tmp_path,directed,distribution):
    from society0.plugins.social_topology import generate_topology
    from society0.plugins.social_models import SocialNetworkConfig
    cfg=SocialNetworkConfig(distribution=distribution,is_directed=directed,social_media={'recommendation':{'use_embedding_similarity':False}})
    expected=list(generate_topology('abc',cfg,seed=19).edges)
    async with compose(tmp_path/'run',plan(social_plugin('abc',config=cfg,seed=19))) as host:
        store=host.service('storage','store')
        actual=store.read(lambda r:r.query('SELECT follower,followee FROM social_edges ORDER BY id'))
        assert actual==expected
