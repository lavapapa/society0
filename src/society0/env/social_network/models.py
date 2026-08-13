"""
社交网络环境的 Pydantic 模型。

该文件定义了以下数据结构:
- 社交网络的配置 (拓扑结构, 功能特性)。
- 社交媒体实体的状态 (帖子, 回复, 投票)。
"""
from __future__ import annotations
from typing import List, Dict, Any, Union, Literal, Optional
from pydantic import BaseModel, Field
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
    target_cv_std: float = Field(0.05, description="目标CV值标准差。", ge=0, le=0.5)
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
    """内容推荐系统的配置。"""
    chronological_weight: float = Field(0.3, description="按时间排序的权重。", ge=0, le=1)
    engagement_weight: float = Field(0.3, description="帖子互动（点赞、回复）的权重。", ge=0, le=1)
    similarity_weight: float = Field(0.2, description="内容相似度（标签、嵌入向量）的权重。", ge=0, le=1)
    network_weight: float = Field(0.2, description="社交网络邻近度的权重。", ge=0, le=1)
    use_embedding_similarity: bool = Field(True, description="是否使用嵌入向量进行相似度计算。")
    like_score: float = Field(1.0, description="单次点赞的分值。")
    reply_score: float = Field(1.5, description="单次回复的分值。")
    repost_score: float = Field(2.0, description="单次转发的分值。")
    time_decay_hours: float = Field(12.0, description="内容热度的半衰期（小时）。")
    post_count: int = Field(5, description="在信息流中返回的帖子数量。", ge=1)
    candidate_count: int = Field(15, description="旧版候选数量参数；默认推荐池不再受它限制。", ge=1)
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
    feed_content_preview_chars: int = Field(
        160,
        description="推荐流中单条帖子正文展示的最大字符数，避免 FoV prompt 过大。",
        ge=40,
    )
    feed_max_chars: int = Field(
        3000,
        description="推荐流 FoV 文本最大字符数，超过时截断并提示。",
        ge=500,
    )

class TrendingConfig(BaseModel):
    """热门帖子功能的配置。"""
    enabled: bool = Field(True, description="是否启用热门功能。")
    calculation_window_ticks: int = Field(100, description="计算热门趋势的时间窗口（以 tick 为单位）。", ge=1)
    inject_into_feed: bool = Field(True, description="是否将热门帖子注入用户的信息流。")
    injection_count: int = Field(1, description="注入信息流的热门帖子数量。", ge=0)

class SocialMediaFeatures(BaseModel):
    """所有与社交媒体相关功能配置的容器。"""
    enabled: bool = Field(True, description="全局启用或禁用社交媒体功能。")
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

# --- 社交媒体状态模型 ---

class Reply(BaseModel):
    """代表对一个帖子的回复。"""
    reply_id: str
    author_id: str
    original_post_id: str
    content: str
    created_tick: int
    likes: List[str] = Field(default_factory=list)

class Vote(BaseModel):
    """代表对一个帖子的投票。"""
    vote_id: str
    voter_id: str
    original_post_id: str
    vote: float = Field(description="投票值，通常在 -1.0 到 1.0 之间。")
    created_tick: int

class LikeEvent(BaseModel):
    """代表一次点赞事件，用于记录时间信息。"""
    agent_id: str
    created_tick: int

class Post(BaseModel):
    """代表社交网络中的单个帖子。"""
    post_id: str
    author_id: str
    content: str
    tags: List[str] = Field(default_factory=list)
    special_tags: List[str] = Field(default_factory=list, description="干预系统添加的特殊标签")
    view_count: int = Field(0, description="帖子被查看的次数")
    created_tick: int
    likes: List[str] = Field(default_factory=list)
    like_events: List[LikeEvent] = Field(default_factory=list, description="带时间戳的点赞记录")
    replies: List[Reply] = Field(default_factory=list)
    votes: List[Vote] = Field(default_factory=list)
    embedding: Optional[List[float]] = None
    embedding_ref: Optional[str] = Field(None, description="向量库中的帖子 embedding 引用")
    embedding_model: Optional[str] = Field(None, description="生成帖子 embedding 的模型")
    embedding_dimensions: Optional[int] = Field(None, description="帖子 embedding 维度")
    embedding_indexed: bool = Field(False, description="帖子 embedding 是否已写入向量库")
    reply_to: Optional[str] = Field(None, description="被回复/转发的帖子ID")
