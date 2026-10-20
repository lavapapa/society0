"""声明 SQL 来源依赖，在短视图内取得同版输入并绑定纯值排名策略。"""
from collections import OrderedDict
from dataclasses import replace
from typing import Protocol

from ..kernel.plugins import Plugin
from ..kernel.interaction import Unavailable
from ..kernel.storage import ReadView, StageStore
from .social_ranking import Candidate, RankingInput


class SQLCandidates(Protocol):
    @property
    def store(self) -> StageStore: ...
    @property
    def dependencies(self) -> tuple[str, ...]: ...
    def active_pool_in(self, view: ReadView, tick: int) -> tuple[Candidate, ...]: ...


class SQLRelations(Protocol):
    @property
    def store(self) -> StageStore: ...
    @property
    def dependencies(self) -> tuple[str, ...]: ...
    def following_in(self, view: ReadView, actor: str) -> frozenset[str]: ...


class Recommendations:
    def __init__(self,data,candidates,relations,ranker,semantic=None,*,cache_capacity=128):
        self.data,self.candidates,self.relations=data,candidates,relations
        self._ranker,self.semantic=ranker,semantic
        self.store=data.store
        if type(cache_capacity) is not int or cache_capacity<1:raise ValueError('cache_capacity must be a positive integer')
        self.cache_capacity=cache_capacity
        self._snapshots=OrderedDict()
        sources=[('candidates',candidates,('active_pool_in',)),('relations',relations,('following_in',))]
        if semantic is not None:sources.append(('semantic',semantic,('query_text_in','flush_embeddings','similarities')))
        dependencies=list(data.member_dependencies)
        for label,service,methods in sources:
            for attribute in ('store','dependencies',*methods):
                if not hasattr(service,attribute):raise ValueError(f'{label} source requires {attribute}')
            if service.store is not self.store:raise ValueError(f'{label} source must use the same StageStore')
            for method in methods:
                if not callable(getattr(service,method)):raise ValueError(f'{label} source requires callable {method}')
            declared=tuple(dict.fromkeys(service.dependencies))
            try:self.store.read(lambda view:view.revision_for(declared))
            except ValueError as error:raise ValueError(f'{label} source declares an unknown dependency table') from error
            dependencies.extend(declared)
        self._dependencies=tuple(dict.fromkeys(dependencies))
        self.store.read(lambda view:view.revision_for(self._dependencies))

    @property
    def ranker(self):
        return self._ranker

    def revision_in(self,view,actor):
        if not self.data.member_in(view,actor):raise Unavailable('resource unavailable')
        return view.revision_for(self._dependencies)

    async def _input(self,actor,tick,query):
        if not self.data.config.social_media.enabled:
            return self.store.read(lambda view:RankingInput(actor,tick,(),frozenset(),{},self.revision_in(view,actor)))
        if self.semantic is not None:await self.semantic.flush_embeddings()
        def read(view):
            revision=self.revision_in(view,actor)
            candidates=self.candidates.active_pool_in(view,tick)
            if not isinstance(candidates,tuple):raise TypeError('candidates source must return a tuple of Candidate values')
            identities=[item.post_id for item in candidates]
            if len(set(identities))!=len(identities):raise ValueError('candidates source must return unique post identities')
            source=RankingInput(actor,tick,candidates,self.relations.following_in(view,actor),{},revision)
            text=self.semantic.query_text_in(view,actor,query=query) if self.semantic is not None else None
            return source,text
        source,text=self.store.read(read)
        if self.semantic is not None:
            scores=await self.semantic.similarities(source,text=text,dependencies=self._dependencies)
            source=replace(source,similarities=scores)
        if self.store.read(lambda view:self.revision_in(view,actor))!=source.revision:
            raise ValueError('recommendation inputs changed; request again')
        return source

    async def recommendation_input(self,actor,tick,*,query=None):
        return await self._input(actor,tick,query)

    def rank(self,source):
        result=self.ranker.rank(source)
        expected={item.post_id for item in source.candidates if item.author_id!=source.actor}
        actual=[item.post_id for item in result.items]
        if result.revision!=source.revision or result.total!=len(actual) or len(actual)!=len(expected) or set(actual)!=expected:
            raise ValueError('ranking output must preserve revision and all eligible candidate identities')
        return result

    async def recommendation(self,actor,tick,*,query=None):
        revision=self.store.read(lambda view:self.revision_in(view,actor))
        previous=self._snapshots.get(actor)
        if previous is not None and previous[:3]==(tick,query,revision):
            self._snapshots.move_to_end(actor)
            return previous[3],previous[4]
        source=await self._input(actor,tick,query)
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
