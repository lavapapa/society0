"""推荐公开值对象与无存储权限的排序策略。"""
from dataclasses import dataclass, field
from collections.abc import Mapping
import math
from functools import cached_property
from types import MappingProxyType
from ..kernel.plugins import Plugin
from .social_models import RecommendationConfig


@dataclass(frozen=True)
class Candidate:
    post_id: str
    author_id: str
    created_tick: int
    like_count: int = 0
    reply_count: int = 0
    repost_count: int = 0
    view_count: int = 0
    engagement_score: float = 0.0


@dataclass(frozen=True)
class RankingInput:
    actor: str
    tick: int
    candidates: tuple[Candidate, ...]
    following: frozenset[str]
    similarities: Mapping[str, float]
    revision: int

    def __post_init__(self):
        object.__setattr__(self,'candidates',tuple(self.candidates))
        object.__setattr__(self,'following',frozenset(self.following))
        object.__setattr__(self,'similarities',MappingProxyType(dict(self.similarities)))

    @cached_property
    def by_id(self):
        return MappingProxyType({item.post_id:item for item in self.candidates})


@dataclass(frozen=True)
class RankedPost:
    post_id: str
    score: float
    components: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self,'components',MappingProxyType(dict(self.components)))


@dataclass(frozen=True)
class RankingOutput:
    items: tuple[RankedPost, ...]
    total: int
    revision: int

    def __post_init__(self):
        object.__setattr__(self,'items',tuple(self.items))


class WeightedRanker:
    def __init__(self, config=None):
        self.config=RecommendationConfig.model_validate(config or {})

    def rank(self, source: RankingInput) -> RankingOutput:
        cfg=self.config; ranked=[]
        for item in source.candidates:
            if item.author_id==source.actor:continue
            time=math.exp(-max(source.tick-item.created_tick,0)/(cfg.time_decay_hours if cfg.time_decay_hours>0 else 1.0))
            engagement=item.engagement_score
            network=cfg.follow_bonus if item.author_id in source.following else 0.0
            semantic=source.similarities.get(item.post_id,0.0)
            parts={'time_score':time,'time_contribution':cfg.chronological_weight*time,
                   'engagement_score':engagement,'engagement_contribution':cfg.engagement_weight*engagement,
                   'network_score':network,'network_contribution':cfg.network_weight*network,
                   'semantic_score':semantic,'semantic_contribution':cfg.similarity_weight*semantic}
            score=sum(parts[key] for key in ('time_contribution','engagement_contribution','network_contribution','semantic_contribution'))
            parts['total_score']=score
            ranked.append((score,item.created_tick,RankedPost(item.post_id,score,{key:round(float(value),6) for key,value in parts.items()})))
        ranked.sort(key=lambda item:(item[0],item[1]),reverse=True)
        return RankingOutput(tuple(item[2] for item in ranked),len(ranked),source.revision)


class ChronologicalRanker:
    def rank(self, source: RankingInput) -> RankingOutput:
        ordered=sorted((p for p in source.candidates if p.author_id!=source.actor),key=lambda p:(p.created_tick,p.post_id),reverse=True)
        items=tuple(RankedPost(p.post_id,float(p.created_tick),{'total_score':float(p.created_tick)}) for p in ordered)
        return RankingOutput(items,len(items),source.revision)


def weighted_ranking_plugin(*,name='social.weighted',config=None,data=None):
    def install(ctx):
        cfg=ctx.require(data,'data').config.social_media.recommendation if data else config
        ctx.provide('rank',WeightedRanker(cfg))
    return Plugin(name,requires=(data,) if data else (),install=install)


def chronological_ranking_plugin(*,name='social.chronological'):
    return Plugin(name,install=lambda ctx:ctx.provide('rank',ChronologicalRanker()))
