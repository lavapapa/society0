"""公开字段直接验证领域消费者和真实认知输入构建器，全程无模型请求。"""
import json
import math

import pytest

from society0.kernel.composition import compose
from society0.kernel.interaction import InteractionScope, Moment, Query, Ref
from society0.plugins.social import social_plugin
from tests.primary.test_plugin_social import plan


def test_public_social_fields_match_reviewed_consumer_scope():
    from society0.plugins.social_models import RecommendationConfig,TrendingConfig,SocialMediaFeatures,SocialNetworkConfig
    # 新增公开字段时，补齐本文件/相邻测试的真实消费者断言及 social-field-matrix。
    assert set(RecommendationConfig.model_fields)==set('''chronological_weight engagement_weight similarity_weight network_weight
        use_embedding_similarity like_score reply_score repost_score time_decay_hours post_count candidate_count
        full_scan_until recent_keep_count top_engagement_keep_count min_lifetime_ticks
        include_recent_posts_in_query include_following_in_query recent_post_limit interaction_limit
        recall_multiplier follow_bonus feed_content_preview_chars feed_max_chars'''.split())
    assert set(TrendingConfig.model_fields)=={'enabled','calculation_window_ticks','inject_into_feed','injection_count'}
    assert set(SocialMediaFeatures.model_fields)=={'enabled','recommendation','trending','content_length_limit'}
    assert set(SocialNetworkConfig.model_fields)=={'distribution','is_directed','social_media'}
    from society0.plugins import social_models as models
    expected={
        'CVTargetedParams':{'target_cv_mean','base_algorithm','base_params','max_iterations','convergence_threshold'},
        'RandomParams':{'connection_probability'},
        'SmallWorldParams':{'k_neighbors','rewiring_probability'},
        'ScaleFreeParams':{'m_edges'},'CompleteParams':set(),
        **{name:{'type','params'} for name in ('RandomDistribution','SmallWorldDistribution','ScaleFreeDistribution','CompleteDistribution','CVTargetedDistribution')},
    }
    for name,fields in expected.items():assert set(getattr(models,name).model_fields)==fields


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled,window,expected', [(True, 5, ['post_3', 'post_2']), (True, 2, ['post_3']), (False, 5, [])])
async def test_trending_enabled_and_window_reach_domain_and_action(tmp_path, enabled, window, expected):
    cfg={'social_media':{'trending':{'enabled':enabled,'calculation_window_ticks':window}}}
    async with compose(tmp_path/'run',plan(social_plugin('abc',edges=[],config=cfg))) as host:
        social=host.service('social','mechanism')
        for tick in (0, 5, 8):social.execute('publish_post','b','b',{'content':str(tick)},tick)
        for tick in (4,10):social.execute('publish_post','b','b',{'content':'boundary'},tick)
        for actor in 'ac':social.execute('like_post',actor,'post_1',{},8)
        assert [p['post_id'] for p in social.trending(9)]==expected
        assert social.update_trending_topics(9)==expected
        assert social.trending_ids()==expected
        result=await host.service('interaction','actions').invoke(InteractionScope('a',Moment(9,'read')),
            'social.get_trending_posts',Ref('social','participants','a'),{})
        assert [p['post_id'] for p in result.value['posts']]==expected


@pytest.mark.asyncio
@pytest.mark.parametrize('inject,count,expected', [(False,2,['post_4','post_3']), (True,0,['post_4','post_3']), (True,1,['post_1','post_4']), (True,2,['post_1','post_2'])])
async def test_injection_reaches_full_cognitive_input_and_feed_without_model(tmp_path, inject, count, expected):
    from society0.plugins.social_cognition import social_source_plugin
    from society0.plugins.cognition import SourceSpec, cognition_plugin
    from society0.plugins import thread_plugin
    from tests.primary.test_cognition_composition import session
    cfg={'social_media':{'content_length_limit':-1,
        'recommendation':{'post_count':2,'full_scan_until':1,'recent_keep_count':2,
            'top_engagement_keep_count':1,'min_lifetime_ticks':0},
        'trending':{'inject_into_feed':inject,'injection_count':count}}}
    from society0.plugins.social_ranking import chronological_ranking_plugin
    async with compose(tmp_path/'run',plan(thread_plugin(), chronological_ranking_plugin(),
        social_plugin('abc',edges=[],config=cfg,ranking=('social.chronological','rank')),
        social_source_plugin(),cognition_plugin(sources=(SourceSpec('social',('social.source','source')),)))) as host:
        social=host.service('social','mechanism')
        body='完整原文🙂'*1500
        for tick in range(4):social.execute('publish_post','b','b',{'content':body+str(tick)},tick)
        for actor in 'ac':social.execute('like_post',actor,'post_1',{},4)
        social.execute('like_post','a','post_2',{},4)
        # 热门注入仍可取得池外的 post_2。
        from dataclasses import replace
        current=replace(session(None),scope=InteractionScope('a',Moment(4,'read')))
        current.cursors['thread_id']=host.service('threads','threads').open('a',current.moment,'decision')
        batch=await host.service('cognition','input_builder')(current)
        payload=json.loads(next(m['content'].split('\n',1)[1] for m in batch.messages if m['content'].startswith('推荐动态\n')))
        assert payload['recommended_ids']==expected
        assert [p['post_id'] for p in payload['new_posts']]==expected
        assert all(p['content']==body+str(int(p['post_id'].split('_')[1])-1) for p in payload['new_posts'])
        assert [p['post_id'] for p in await social.recommended_feed('a',4,record_impressions=False)]==expected
        for post in payload['new_posts']:
            original=await host.service('interaction','information').read(InteractionScope('a',Moment(4,'read')),
                '/social/content/'+post['post_id'],size=100000)
            assert original.data.decode()==post['content']


@pytest.mark.asyncio
async def test_social_media_disabled_preserves_graph_and_hides_media_consumers(tmp_path):
    from society0.plugins.social_cognition import social_source_plugin
    from tests.primary.test_cognition_composition import session
    cfg={'social_media':{'enabled':False}}
    async with compose(tmp_path/'run',plan(social_plugin('abc',edges=[('a','b')],config=cfg),social_source_plugin())) as host:
        social=host.service('social','mechanism')
        assert social.profile('a')['following']==['b']
        assert social.execute('follow','a','c',{},0).status=='completed'
        assert social.execute('publish_post','a','a',{'content':'disabled'},0).status=='rejected'
        assert await social.recommended_feed('a',1)==[]
        assert social.trending(1)==[]
        assert (await host.service('social.source','source')(session(None),None)).messages==[]
        actions=host.service('interaction','actions');scope=InteractionScope('a',Moment(1,'read'))
        assert (await actions.invoke(scope,'social.publish_post',Ref('social','participants','a'),{'content':'x'})).status=='rejected'
        info=host.service('interaction','information')
        from society0.kernel.interaction import Unavailable
        with pytest.raises(Unavailable):await info.query(scope,'/social/posts',Query())


@pytest.mark.asyncio
@pytest.mark.parametrize('per_post,total,expected', [(None,None,[7,7,7]), (3,None,[3,3,3]), (None,10,[7,3,0]), (4,6,[4,2,0]), (0,0,[0,0,0]), (7,21,[7,7,7])])
async def test_preview_limits_new_bodies_in_actual_input_preserving_originals(tmp_path,per_post,total,expected):
    from society0.plugins import thread_plugin
    from society0.plugins.cognition import SourceSpec,cognition_plugin
    from society0.plugins.social_cognition import social_source_plugin
    from tests.primary.test_cognition_composition import session
    recommendation={'post_count':3}
    if per_post is not None:recommendation['feed_content_preview_chars']=per_post
    if total is not None:recommendation['feed_max_chars']=total
    cfg={'social_media':{'recommendation':recommendation,'trending':{'inject_into_feed':False}}}
    async with compose(tmp_path/'run',plan(thread_plugin(),social_plugin('abc',edges=[],config=cfg),social_source_plugin(),
        cognition_plugin(sources=(SourceSpec('social',('social.source','source')),)))) as host:
        social=host.service('social','mechanism');body='中文🙂甲乙丙丁'
        for i in range(3):social.execute('publish_post','b','b',{'content':body},0)
        current=session(None);threads=host.service('threads','threads')
        tid=threads.open('a',current.moment,'decision');current.cursors['thread_id']=tid
        builder=host.service('cognition','input_builder');batch=await builder(current)
        payload=json.loads(next(m['content'].split('\n',1)[1] for m in batch.messages if m['content'].startswith('推荐动态\n')))
        assert len(payload['recommended_ids'])==3
        assert [len(p['content']) for p in payload['new_posts']]==expected
        for post,length in zip(payload['new_posts'],expected):
            assert post['content']==body[:length] and post['content_length']==7
            assert post['content_truncated']==(length<7)
            original=await host.service('interaction','information').read(current.scope,post['content_path'].removeprefix('/world'))
            assert original.data.decode()==body
        # 保存输入后才消费位置和曝光；再读同一 Moment 保留旧消息并无重复正文。
        threads.append_input(tid,batch.messages,batch.consumer,batch.cursor,context=batch.context)
        for effect in batch.effects:effect(type('Context',(),{'session':current})())
        again=await builder(current)
        assert not any(m['content'].startswith('推荐动态\n') for m in again.messages)
        assert threads.read_messages(tid)[-len(batch.messages):]==batch.messages
        social.exposure.flush()
        assert all(social.post_details(identifier)['view_count']==1 for identifier in payload['recommended_ids'])


@pytest.mark.asyncio
async def test_scoring_fields_reach_transaction_projection_and_pure_ranker(tmp_path):
    cfg={'social_media':{'recommendation':{'like_score':2,'reply_score':3,'repost_score':4,
        'chronological_weight':0.1,'engagement_weight':0.2,'similarity_weight':0.3,'network_weight':0.4,'follow_bonus':5}}}
    async with compose(tmp_path/'run',plan(social_plugin('abc',edges=[('a','b')],config=cfg))) as host:
        social=host.service('social','mechanism')
        identifier=social.execute('publish_post','b','b',{'content':'source'},1).value['post_id']
        social.execute('like_post','a',identifier,{},1)
        social.execute('comment','a',identifier,{'content':'reply'},1)
        social.execute('repost','c',identifier,{'commentary':'shared'},1)
        source=await social.recommendation_input('a',1)
        assert source.by_id[identifier].engagement_score==9
        from dataclasses import replace
        output=social.recommendations.rank(replace(source,similarities={identifier:0.5}))
        post=next(item for item in output.items if item.post_id==identifier)
        assert dict(post.components)=={'time_score':1,'time_contribution':0.1,'engagement_score':9,
            'engagement_contribution':1.8,'network_score':5,'network_contribution':2,
            'semantic_score':0.5,'semantic_contribution':0.15,'total_score':4.05}


@pytest.mark.asyncio
@pytest.mark.parametrize('recent,following,post_limit,interaction_limit,expected', [
    (False,False,2,2,'Social feed preference'),
    (True,False,1,0,'Recent posts:\n[post_2] own latest\nTags: 无标签'),
    (True,False,0,1,'Recent interactions:\nLike post_3: other latest'),
    (False,True,1,2,'Following:\nb'),
])
async def test_query_fields_reach_actual_preference_text(tmp_path,recent,following,post_limit,interaction_limit,expected):
    cfg={'social_media':{'recommendation':{'include_recent_posts_in_query':recent,'include_following_in_query':following,
        'recent_post_limit':post_limit,'interaction_limit':interaction_limit}}}
    async with compose(tmp_path/'run',plan(social_plugin('abc',edges=[('a','b'),('a','c')],config=cfg))) as host:
        social=host.service('social','mechanism')
        for actor,body in [('a','own old'),('a','own latest'),('b','other latest')]:
            social.execute('publish_post',actor,actor,{'content':body},0)
        social.execute('like_post','a','post_3',{},1)
        assert social.preference_text('a')==expected


def test_time_decay_hours_documents_actual_exponential_tick_formula():
    from society0.plugins.social_models import RecommendationConfig
    from society0.plugins.social_ranking import Candidate, RankingInput, WeightedRanker
    for scale in (0, -1, 2, 12):
        ranked=WeightedRanker({'time_decay_hours':scale}).rank(RankingInput('a',12,(Candidate('p','b',0),),frozenset(),{},1))
        assert ranked.items[0].components['time_score']==round(math.exp(-12/(scale if scale>0 else 1)),6)
    description=RecommendationConfig.model_fields['time_decay_hours'].description
    assert 'exp(' in description and 'tick' in description and '半衰期' not in description


@pytest.mark.asyncio
@pytest.mark.parametrize('candidate_count,multiplier,post_count,threshold,expected', [(2,3,1,1,6),(4,2,1,1,8),(1,1,7,1,7),(1,9,1,10,3)])
async def test_candidate_count_controls_actual_semantic_vector_request(tmp_path,candidate_count,multiplier,post_count,threshold,expected):
    from society0.kernel.plugins import Plugin
    from tests.primary.test_kernel_memory import Embed, Client
    from types import SimpleNamespace
    embed=Embed();client=Client()
    def resources(ctx):
        ctx.provide('embeddings',{'default':SimpleNamespace(embed=embed)})
        ctx.provide('client',client)
    cfg={'social_media':{'recommendation':{'use_embedding_similarity':True,'candidate_count':candidate_count,
        'recall_multiplier':multiplier,'post_count':post_count,'full_scan_until':threshold,
        'recent_keep_count':3,'top_engagement_keep_count':3}}}
    async with compose(tmp_path/'run',plan(Plugin('vectors',install=resources),social_plugin('abc',edges=[],config=cfg,
        embedding=('vectors','default'),vector_client=('vectors','client')))) as host:
        social=host.service('social','mechanism')
        for i in range(3):social.execute('publish_post','b','b',{'content':str(i)},i)
        await social.flush_embeddings()
        collection=social.semantic._collection;queries=[];original=collection.query
        def query(**kwargs):
            queries.append(kwargs);return original(**kwargs)
        collection.query=query
        await social.recommendation('a',4,query='preference')
        assert queries[0]['n_results']==expected
