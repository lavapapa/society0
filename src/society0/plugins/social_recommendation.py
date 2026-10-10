"""候选与策略装配；消费者无需安装内容行动或认知呈现。"""
from collections import OrderedDict
from ..kernel.plugins import Plugin
from ..kernel.interaction import Unavailable
from .social_ranking import Candidate, RankingInput


class Recommendations:
    def __init__(self,data,candidates,relations,ranker,semantic=None,*,cache_capacity=128):
        self.data,self.candidates,self.relations=data,candidates,relations
        self.ranker,self.semantic=ranker,semantic
        if type(cache_capacity) is not int or cache_capacity<1:raise ValueError('cache_capacity must be a positive integer')
        self.cache_capacity=cache_capacity
        self._snapshots=OrderedDict()

    def ranking_dependencies(self):
        suffixes=('posts','bodies','edges','recent_interactions','members')
        if self.semantic is not None:suffixes+=('vectors',)
        return tuple(self.data.name+'_'+key for key in suffixes)+('actor_personas',)

    def ranking_input(self,actor,tick,*,similarity_scores=None,pool=None):
        def read(r):
            if not self.data.member_in(r,actor):raise Unavailable('resource unavailable')
            return r.revision_for(self.ranking_dependencies())
        revision=self.data.store.read(read)
        candidates=self.candidates.active_pool(tick) if pool is None else pool
        return RankingInput(actor,tick,tuple(Candidate(**item) for item in candidates),self.relations.following(actor),dict(similarity_scores or {}),revision)

    async def recommendation_input(self,actor,tick,*,query=None):
        scores=await self.semantic.similarities(actor,tick,query=query) if self.semantic is not None else {}
        return self.ranking_input(actor,tick,similarity_scores=scores)

    def rank(self,source):
        result=self.ranker.rank(source)
        expected={item.post_id for item in source.candidates if item.author_id!=source.actor}
        actual=[item.post_id for item in result.items]
        if result.revision!=source.revision or result.total!=len(actual) or len(actual)!=len(expected) or set(actual)!=expected:
            raise ValueError('ranking output must preserve revision and all eligible candidate identities')
        return result

    async def recommendation(self,actor,tick,*,query=None):
        revision=self.data.store.read(lambda r:r.revision_for(self.ranking_dependencies()))
        previous=self._snapshots.get(actor)
        if previous is not None and previous[:3]==(tick,query,revision):
            self._snapshots.move_to_end(actor)
            return previous[3],previous[4]
        source=await self.recommendation_input(actor,tick,query=query)
        output=self.rank(source)
        self._snapshots[actor]=(tick,query,source.revision,source,output)
        self._snapshots.move_to_end(actor)
        if len(self._snapshots)>self.cache_capacity:self._snapshots.popitem(last=False)
        return source,output


def social_recommendation_plugin(*,name='social.recommendation',data='social.data',candidates='social.candidates',relations='social.relations',ranking=('social.weighted','rank'),semantic=None,cache_capacity=128):
    def install(ctx):
        service=Recommendations(ctx.require(data,'data'),ctx.require(candidates,'candidates'),ctx.require(relations,'relations'),ctx.require(*ranking),ctx.require(semantic,'semantic') if semantic else None,cache_capacity=cache_capacity)
        ctx.provide('recommendation',service)
    requires=tuple(dict.fromkeys((data,candidates,relations,ranking[0])+((semantic,) if semantic else ())))
    return Plugin(name,requires=requires,install=install)
