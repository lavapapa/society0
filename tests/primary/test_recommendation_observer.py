"""独立插件消费社交推荐输入输出，并保存自己的实验状态。"""
import pytest

from society0 import ActorRecord, compose
from society0.plugins import actor_data_plugin, interaction_plugin, social_plugin
from society0.plugins.social_ranking import weighted_ranking_plugin, chronological_ranking_plugin


def plugins():
    from examples.core_next.recommendation_observer import recommendation_observer_plugin
    config={'social_media':{'recommendation':{'use_embedding_similarity':False,'chronological_weight':0.,'engagement_weight':1.,'network_weight':0.}}}
    return [
        actor_data_plugin(records=[ActorRecord(actor,'unused') for actor in ('a','b','c')]),
        interaction_plugin(lambda *args:True),
        social_plugin(('a','b','c'),edges=(),config=config,ranking=('recent','rank')),
        weighted_ranking_plugin(name='weighted',config=config['social_media']['recommendation']),
        chronological_ranking_plugin(name='recent'),
        recommendation_observer_plugin(source=('social.recommendation','recommendation'),
            strategies={'weighted':('weighted','rank'),'recent':('recent','rank')}),
    ]


@pytest.mark.asyncio
async def test_observer_reuses_inputs_outputs_and_restores_without_domain_mutations(tmp_path):
    async with compose(tmp_path/'run',plugins()) as host:
        social=host.service('social','mechanism')
        first=social.execute('publish_post','b','b',{'content':'旧帖完整原文🙂'},0).value['post_id']
        latest=social.execute('publish_post','c','c',{'content':'新帖完整原文🙂'},1).value['post_id']
        social.execute('like_post','a',first,{},1)
        source=await social.recommendation_input('a',2)
        observer=host.service('recommendation.observer','observer')
        result=await observer.observe('a',2)
        assert result['candidate_ids']==[candidate.post_id for candidate in source.candidates]
        assert [item['post_id'] for item in result['strategies']['weighted']]==[first,latest]
        assert [item['post_id'] for item in result['strategies']['recent']]==[latest,first]
        feed=await social.recommended_feed('a',2,record_impressions=False)
        assert [item['post_id'] for item in feed]==[latest,first]
        assert [social.post_details(post)['view_count'] for post in (first,latest)]==[0,0]
        assert social.recommended_ids('a')==[]
        store=host.service('storage','store')
        store.complete(1)
    async with compose(tmp_path/'restored',plugins(),source=tmp_path/'run') as host:
        assert host.service('recommendation.observer','observer').records()==[result]
        social=host.service('social','mechanism')
        assert social.post_details(first)['like_count']==1
        assert social.post_details(first)['content']=='旧帖完整原文🙂'
        host.service('storage','store').complete(2)

    from examples.core_next.recommendation_observer import recommendation_observer_plugin
    from society0.plugins.social import (social_data_plugin, social_notifications_plugin,
        social_relations_plugin, social_candidates_plugin, social_recommendation_plugin)
    headless=[actor_data_plugin(),social_data_plugin('abc',edges=()),social_notifications_plugin(),
        social_relations_plugin(),social_candidates_plugin(),
        weighted_ranking_plugin(name='weighted',data='social.data'),chronological_ranking_plugin(name='recent'),
        social_recommendation_plugin(ranking=('recent','rank')),
        recommendation_observer_plugin(source=('social.recommendation','recommendation'),
            strategies={'weighted':('weighted','rank'),'recent':('recent','rank')})]
    async with compose(tmp_path/'analysis-only',headless,source=tmp_path/'run') as host:
        observed=await host.service('recommendation.observer','observer').observe('a',2)
        assert observed['strategies']==result['strategies']
        for name,service in (('interaction','information'),('social.content','content'),
                              ('social.exposure','exposure'),('social.presentation','presentation')):
            with pytest.raises(KeyError):host.service(name,service)
        host.service('storage','store').complete(2)
