"""社交机制的领域配置，独立于旧 World 与环境生命周期。"""
from __future__ import annotations
from typing import List, Dict, Any, Union, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field
from enum import Enum

# --- 网络拓扑配置 ---

class NetworkDistributionType(str, Enum):
    """可能的网络生成算法枚举。"""
    RANDOM = "random"
    SMALL_WORLD = "small_world"
    SCALE_FREE = "scale_free"
    COMPLETE = "complete"
    CV_TARGETED = "cv_targeted"  # 新增：基于CV值的网络生成

class CVTargetedParams(BaseModel):
    """基于CV值的网络生成参数。"""
    target_cv_mean: float = Field(0.1, description="目标CV值均值。", ge=0, le=1)
    base_algorithm: NetworkDistributionType = Field(NetworkDistributionType.SMALL_WORLD,
                                                   description="基础网络生成算法。")
    base_params: Optional[Dict[str, Any]] = Field(default_factory=dict,
                                                 description="基础算法的参数。")
    max_iterations: int = Field(1000, description="最大调整迭代次数。", ge=1)
    convergence_threshold: float = Field(0.01, description="收敛阈值。", ge=0.001, le=0.1)

class SmallWorldParams(BaseModel):
    """Watts-Strogatz 小世界网络的参数。"""
    k_neighbors: int = Field(4, description="每个节点的最近邻居数。", ge=1)
    rewiring_probability: float = Field(0.1, description="边的重新连接概率。", ge=0, le=1)

class ScaleFreeParams(BaseModel):
    """Barabasi-Albert 无标度网络的参数。"""
    m_edges: int = Field(2, description="从一个新节点连接到现有节点的边数。", ge=1)

class RandomParams(BaseModel):
    """Erdos-Renyi (随机) 网络的参数。"""
    connection_probability: float = Field(0.1, description="在两个节点之间创建一条边的概率。", ge=0, le=1)

class CompleteParams(BaseModel):
    """完全图的参数，其中所有节点都相互连接。"""
    pass

class RandomDistribution(BaseModel):
    type: Literal[NetworkDistributionType.RANDOM] = NetworkDistributionType.RANDOM
    params: RandomParams = Field(default_factory=RandomParams)

class SmallWorldDistribution(BaseModel):
    type: Literal[NetworkDistributionType.SMALL_WORLD] = NetworkDistributionType.SMALL_WORLD
    params: SmallWorldParams = Field(default_factory=SmallWorldParams)

class ScaleFreeDistribution(BaseModel):
    type: Literal[NetworkDistributionType.SCALE_FREE] = NetworkDistributionType.SCALE_FREE
    params: ScaleFreeParams = Field(default_factory=ScaleFreeParams)

class CompleteDistribution(BaseModel):
    type: Literal[NetworkDistributionType.COMPLETE] = NetworkDistributionType.COMPLETE
    params: CompleteParams = Field(default_factory=CompleteParams)

class CVTargetedDistribution(BaseModel):
    type: Literal[NetworkDistributionType.CV_TARGETED] = NetworkDistributionType.CV_TARGETED
    params: CVTargetedParams = Field(default_factory=CVTargetedParams)

AnyDistribution = Union[
    RandomDistribution,
    SmallWorldDistribution,
    ScaleFreeDistribution,
    CompleteDistribution,
    CVTargetedDistribution
]

# --- 社交媒体功能配置 ---

class RecommendationConfig(BaseModel):
    """单次运行内固定的推荐行为配置。"""
    model_config = ConfigDict(frozen=True)
    chronological_weight: float = Field(0.3, description="按时间排序的权重。", ge=0, le=1)
    engagement_weight: float = Field(0.3, description="帖子互动（点赞、回复）的权重。", ge=0, le=1)
    similarity_weight: float = Field(0.2, description="内容相似度（标签、嵌入向量）的权重。", ge=0, le=1)
    network_weight: float = Field(0.2, description="社交网络邻近度的权重。", ge=0, le=1)
    use_embedding_similarity: bool = Field(False, description="显式启用语义索引；启用时必须绑定嵌入与向量资源。")
    like_score: float = Field(1.0, description="单次点赞的分值。")
    reply_score: float = Field(1.5, description="单次回复的分值。")
    repost_score: float = Field(2.0, description="单次转发的分值。")
    time_decay_hours: float = Field(12.0, description="时间评分的指数衰减尺度：exp(-max(当前tick-创建tick,0)/尺度)。沿用字段名；单位为tick，非正值按1计算。")
    post_count: int = Field(5, description="在信息流中返回的帖子数量。", ge=1)
    candidate_count: int = Field(15, description="语义大池召回基数；向量请求数为max(int(candidate_count*recall_multiplier),post_count)。候选池不受此值截断。", ge=1)
    full_scan_until: int = Field(5000, description="活跃池帖子数不超过该值时默认全量评分。", ge=1)
    recent_keep_count: int = Field(1000, description="超过全量阈值后保留的最新帖子数量。", ge=1)
    top_engagement_keep_count: int = Field(500, description="超过全量阈值后保留的高互动帖子数量。", ge=1)
    min_lifetime_ticks: int = Field(50, description="帖子进入推荐池后的最小生命周期 tick 数。", ge=0)
    include_recent_posts_in_query: bool = Field(
        True, description="构建召回查询时是否包含近期帖子与互动。"
    )
    include_following_in_query: bool = Field(
        False,
        description=(
            "构建语义召回查询时是否包含关注列表。默认关闭；网络邻近度已由 "
            "network_weight/follow_bonus 单独评分，关闭可避免大规模仿真中按 agent 重复 embedding。"
        ),
    )
    recent_post_limit: int = Field(3, description="纳入召回文本的近期帖子数量。", ge=0)
    interaction_limit: int = Field(3, description="纳入召回文本的近期互动数量。", ge=0)
    recall_multiplier: float = Field(
        2.0, description="向量召回数量与候选数量的倍数。", ge=1.0
    )
    follow_bonus: float = Field(0.2, description="关注作者时的额外得分。", ge=0)
    feed_content_preview_chars: int | None = Field(
        None,
        description="认知来源中每条新帖正文的可选字符上限；默认完整正文，原文路径及总长度始终保留。",
        ge=0,
    )
    feed_max_chars: int | None = Field(
        None,
        description="认知来源单批新帖正文的可选总字符预算；默认完整。元信息、原文入口、既有Thread和其他输入保持完整。",
        ge=0,
    )

class TrendingConfig(BaseModel):
    """热门帖子功能的配置。"""
    enabled: bool = Field(True, description="是否启用热门功能。")
    calculation_window_ticks: int = Field(100, description="热门帖创建时间窗口：(当前tick-窗口, 当前tick]；窗口内按累计互动分排序。", ge=1)
    inject_into_feed: bool = Field(True, description="是否在认知来源和recommended_feed中优先呈现热门帖，保持post_count上限。")
    injection_count: int = Field(1, description="优先呈现的热门帖数，排除自己并去重；剩余位置沿用排名。", ge=0)

class SocialMediaFeatures(BaseModel):
    """所有与社交媒体相关功能配置的容器。"""
    enabled: bool = Field(True, description="启用发帖、互动、推荐和热门呈现；关闭时保留网络关系、主体资料与通知。")
    recommendation: RecommendationConfig = Field(default_factory=RecommendationConfig)
    trending: TrendingConfig = Field(default_factory=TrendingConfig)
    content_length_limit: int = Field(
        250,
        description="内容长度限制，-1 表示不限制。",
        ge=-1,
        le=10000,
    )

# --- 主要环境配置 ---

class SocialNetworkConfig(BaseModel):
    """SocialNetworkEnv 的主要配置模型。"""
    distribution: AnyDistribution = Field(description="网络拓扑生成算法。", default_factory=RandomDistribution)
    is_directed: bool = Field(False, description="图是否有向（例如，Twitter 风格的关注）？")
    social_media: SocialMediaFeatures = Field(default_factory=SocialMediaFeatures)
