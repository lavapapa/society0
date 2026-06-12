"""
SocialNetworkEnv 类,代表一个社交网络环境。

v3.0 更新：
- 使用新的 @capability 装饰器替代 get_actions() 方法
- agent_managed_fields_schema 声明环境提供的 Agent 状态字段
- @action, @fov, @rule 装饰器自动收集元数据
"""
from __future__ import annotations
from typing import List, Dict, Any, TYPE_CHECKING, Optional
import uuid
import math
import time
import random
import logging

import networkx as nx

from ...environment import Environment
from ...decorators import env_type, action, fov, rule
from ...core_data import ExecutionContext
from ...agent.core import Agent
from ...logging import AgentEvent, EnvironmentEvent, summarize_text
from .models import (
    SocialNetworkConfig,
    Post,
    Reply,
    Vote,
    LikeEvent,
    NetworkDistributionType,
    CVTargetedParams,
)

if TYPE_CHECKING:
    from ...core_data import World

logger = logging.getLogger(__name__)


def _debug_print(*values: Any, sep: str = " ", end: str = "\n", file: Any = None, flush: bool = False) -> None:
    """Route legacy debug prints through logging without writing to stdout."""
    if file is not None:
        import builtins

        builtins.print(*values, sep=sep, end=end, file=file, flush=flush)
        return

    message = sep.join(str(value) for value in values)
    if end and end != "\n":
        message += end
    logger.debug(message)


print = _debug_print

# --- 社交网络环境 ---

SOCIAL_NETWORK_STATE_SCHEMA = {
    "type": "object",
    "title": "SocialNetworkEnvExtraState",
    "description": "环境的额外声明状态（不含内置核心状态，如 posts 等）。",
    "properties": {},
    "additionalProperties": False,
}

# 注意：根据约束，config_schema 不允许使用 $defs/$ref/oneOf 等高级特性
# 这里提供一个“扁平化”的、无需引用的简化 JSON Schema，覆盖常用字段
SOCIAL_NETWORK_CONFIG_SCHEMA = {
    "type": "object",
    "title": "SocialNetworkConfig",
    "properties": {
        "distribution": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [
                        "random",
                        "small_world",
                        "scale_free",
                        "complete",
                        "cv_targeted",
                    ],
                },
                "params": {
                    "type": "object",
                    "properties": {
                        # random
                        "connection_probability": {"type": "number", "minimum": 0, "maximum": 1},
                        # small_world
                        "k_neighbors": {"type": "integer", "minimum": 1},
                        "rewiring_probability": {"type": "number", "minimum": 0, "maximum": 1},
                        # scale_free
                        "m_edges": {"type": "integer", "minimum": 1},
                        # cv_targeted
                        "target_cv_mean": {"type": "number", "minimum": 0, "maximum": 1},
                        "target_cv_std": {"type": "number", "minimum": 0, "maximum": 0.5},
                        "base_algorithm": {
                            "type": "string",
                            "enum": [
                                "random",
                                "small_world",
                                "scale_free",
                                "complete",
                                "cv_targeted",
                            ],
                        },
                        "base_params": {"type": "object"},
                        "max_iterations": {"type": "integer", "minimum": 1},
                        "convergence_threshold": {"type": "number", "minimum": 0.001, "maximum": 0.1},
                    },
                    "additionalProperties": False,
                },
            },
            "required": ["type"],
            "additionalProperties": False,
        },
        "is_directed": {"type": "boolean"},
        "social_media": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "recommendation": {
                    "type": "object",
                    "properties": {
                        "chronological_weight": {"type": "number", "minimum": 0, "maximum": 1},
                        "engagement_weight": {"type": "number", "minimum": 0, "maximum": 1},
                        "similarity_weight": {"type": "number", "minimum": 0, "maximum": 1},
                        "network_weight": {"type": "number", "minimum": 0, "maximum": 1},
                        "use_embedding_similarity": {"type": "boolean"},
                        "like_score": {"type": "number"},
                        "reply_score": {"type": "number"},
                        "time_decay_hours": {"type": "number"},
                        "post_count": {"type": "integer"},
                        "candidate_count": {"type": "integer", "minimum": 1},
                        "include_recent_posts_in_query": {"type": "boolean"},
                        "recent_post_limit": {"type": "integer", "minimum": 0},
                        "interaction_limit": {"type": "integer", "minimum": 0},
                        "recall_multiplier": {"type": "number", "minimum": 1},
                        "follow_bonus": {"type": "number", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "trending": {
                    "type": "object",
                    "properties": {
                        "enabled": {"type": "boolean"},
                        "calculation_window_ticks": {"type": "integer", "minimum": 1},
                        "inject_into_feed": {"type": "boolean"},
                        "injection_count": {"type": "integer", "minimum": 0},
                    },
                    "additionalProperties": False,
                },
                "content_length_limit": {
                    "type": "integer",
                    "minimum": -1,
                    "maximum": 10000,
                    "description": "内容长度限制，-1表示不限制",
                    "default": 250
                },
            },
            "additionalProperties": False,
        },
    },
    "required": ["distribution", "is_directed", "social_media"],
    "additionalProperties": False,
}


@env_type(
    type_name="social_network",
    # 使用简化版 JSON Schema，避免 $defs/$ref/oneOf
    config_schema=SOCIAL_NETWORK_CONFIG_SCHEMA,
    state_schema=SOCIAL_NETWORK_STATE_SCHEMA,
    # 🔑 v3.0: 声明环境管理的 Agent 状态字段
    # 这些字段会在 World.initialize_env_provided_fields() 时自动添加到每个 Agent
    agent_managed_fields_schema={
        "type": "object",
        "properties": {
            # 暂时不添加字段，如果需要可以添加如 position, social_score 等
            # 示例：
            # "social_influence": {
            #     "type": "number",
            #     "default": 0.0,
            #     "description": "Agent 在社交网络中的影响力分数",
            #     "agent_visible": True,      # LLM可见
            #     "agent_editable": False,    # Agent不可修改（由环境管理）
            #     "env_managed": True
            # }
        }
    },
    builtin_state_fields=[
        {
            "name": "posts",
            "type": "Dict[str, Dict[str, Any]]",
            "description": "社交网络中的帖子字典，键为 post_id，值为帖子详情（内容、作者、互动数据等）。",
        },
        {
            "name": "author_to_post_ids",
            "type": "Dict[str, List[str]]",
            "description": "按作者聚合的帖子 ID 列表，便于快速查询某个智能体发布的内容。",
        },
        {
            "name": "post_counter",
            "type": "int",
            "description": "累积的帖子计数器，用于生成新的 post_id。",
        },
        {
            "name": "trending_post_ids",
            "type": "List[str]",
            "description": "当前识别出的热门帖子 ID 列表，由环境规则动态计算。",
        },
    ],
    display_name="Social Network",
    description="一个模拟社交关系和信息传播的环境（含社交媒体功能）。",
)
class SocialNetworkEnv(Environment):
    """
    一个模拟社交网络的环境，管理网络图、用户生成内容（帖子）和互动。

    设计原则：
    1. __init__: 只负责配置接收和验证，配置信息存储在self._config中，不污染state
    2. initialize: 负责根据配置创建世界的初始状态
    3. self.graph: 作为独立属性存在，不存放在state中，通过snapshot接口序列化
    """

    def __init__(self, world: 'World'):
        """
        初始化方法：只负责配置接收和验证

        职责：
        1. 从world.environment_data中获取原始配置
        2. 使用pydantic验证配置
        3. 将验证后的配置存储在self._config中
        4. 初始化self.graph属性为None
        """
        super().__init__(world)

        # 从 world.environment_data 中获取配置
        #
        # SimEngine 会把 environment.yaml 的 config 放在 world.environment_data["config"]。
        # 之前错误地读取 world.environment_data["state"]["config"] 会导致始终回退到默认配置，
        # 且 environment.yaml 的推荐参数完全不生效。
        raw_config = world.environment_data.get("config")
        if raw_config is None:
            # 兼容旧数据格式（若有）
            raw_config = world.environment_data.get("state", {}).get("config", {})
        if raw_config is None:
            raw_config = {}

        # 使用pydantic进行严格的配置解析和验证
        if isinstance(raw_config, SocialNetworkConfig):
            self._config = raw_config
        else:
            self._config = SocialNetworkConfig.model_validate(raw_config)

        # 初始化图属性（不存放在state中）
        self.graph: Optional[nx.DiGraph] = None
        # 注入的资源句柄（由 SimEngine 提供）
        self._embed_call = None
        self._vector_client = None
        self._post_collection = None
        # 临时缓存：帖子静态元数据，不写入 state
        self._post_meta_cache: Dict[str, Dict[str, Any]] = {}

        logger.debug(f"SocialNetworkEnv配置已验证: {self._config.distribution.type}")

    def set_resource_handles(self, *, embed_call=None, vector_client=None) -> None:
        """接收引擎注入的 embedding 调用与向量存储客户端"""
        self._embed_call = embed_call
        self._vector_client = vector_client
        # collection 缓存在 vector_client 变更时失效
        self._post_collection = None
        self._notifications_cap = None  # 保留以便后续需要全局句柄时使用

    # --- 1. 初始化 (由框架调用) ---

    def initialize(self, agents: List[Agent], world: 'World'):
        """
        创世方法：根据self._config真正构建世界的初始状态

        职责：
        1. 调用_generate_topology创建网络图
        2. 如果启用社交媒体功能，初始化相关状态
        """
        agent_ids = [agent.id for agent in agents]

        # 1. 生成网络拓扑
        self.graph = self._generate_topology(agent_ids)

        # 2. 初始化社交媒体状态（如果启用）
        if self._config.social_media.enabled:
            self.state["posts"] = {}
            self.state["author_to_post_ids"] = {}
            self.state["post_counter"] = 0
            # 通知中心：按用户存储通知队列与计数器
            self.state["notifications"] = {"user_notifications": {}}
            self.state["recommended_posts"] = {}

        logger.info(f"社交网络已初始化: {self.graph.number_of_nodes()} 个节点, "
                   f"{self.graph.number_of_edges()} 条边, "
                   f"平均CV值: {self._calculate_average_cv():.3f}")

    @property
    def agent_instruction(self) -> str:
        base_instruction = (
            "你处在一个类社交平台的环境。只能依赖视野(FoV)里呈现的真实帖子和用户 ID 进行互动，"
            "不要编造从未出现的内容。如果视野为空，请先发布原创内容或等待新的帖子。"
            "所有操作（点赞、评论、关注、转发）前都需要再次检查可见 ID 是否存在。"
        )

        limit = getattr(self._config.social_media, "content_length_limit", -1)
        if limit is not None and limit >= 0:
            base_instruction += f"\n内容长度上限：帖子、评论、转发的文字内容不能超过 {limit} 字符，超出将被拒绝。"

        return base_instruction

    def _generate_topology(self, agent_ids: List[str]) -> nx.DiGraph:
        """
        生成网络拓扑的核心方法

        根据_config.distribution类型选择对应的生成算法：
        - 对于cv_targeted类型，实现基于CV值的迭代调整算法
        - 对于其他类型，使用传统networkx算法
        """
        if self._config.distribution.type == NetworkDistributionType.CV_TARGETED:
            return self._create_cv_targeted_graph(agent_ids, self._config.distribution.params)
        else:
            return self._create_traditional_graph(agent_ids, self._config)

    def _create_traditional_graph(self, agent_ids: List[str], config: SocialNetworkConfig) -> nx.DiGraph:
        """传统的网络图生成方法（重构后）"""
        graph = nx.DiGraph() if config.is_directed else nx.Graph()
        graph.add_nodes_from(agent_ids)

        dist = config.distribution
        num_nodes = len(agent_ids)

        if num_nodes == 0:
            return graph

        if dist.type == "random":
            edges = nx.gnp_random_graph(num_nodes, dist.params.connection_probability, directed=config.is_directed).edges()
        elif dist.type == "small_world":
            k = min(num_nodes - 1, dist.params.k_neighbors) # k 必须小于 n
            edges = nx.watts_strogatz_graph(num_nodes, k, dist.params.rewiring_probability).edges()
        elif dist.type == "scale_free":
            m = min(num_nodes - 1, dist.params.m_edges) # m 必须小于 n
            edges = nx.barabasi_albert_graph(num_nodes, m).edges()
        elif dist.type == "complete":
            edges = nx.complete_graph(num_nodes).edges()
        else:
            edges = []

        actor_map = {i: agent_id for i, agent_id in enumerate(agent_ids)}
        actual_edges = [(actor_map[u], actor_map[v]) for u, v in edges]

        # 确保返回有向图（统一接口）
        if not config.is_directed and isinstance(graph, nx.Graph):
            directed_graph = nx.DiGraph()
            directed_graph.add_nodes_from(agent_ids)
            # 为无向图的每条边添加双向边
            for u, v in actual_edges:
                directed_graph.add_edge(u, v)
                directed_graph.add_edge(v, u)
            return directed_graph
        else:
            graph.add_edges_from(actual_edges)
            return graph

    # --- 2. Agent 工具 (Agent可用的能力) ---

    @action(description="在社交网络上发布新帖子，支持标签和转发/回复功能。当视野没有帖子时应优先使用本动作。")
    async def publish_post(self, context: ExecutionContext, content: str, tags: List[str] = None,
                     reply_to: Optional[str] = None) -> str:
        """
        发布帖子Action（适配新架构）

        现在直接通过代理机制修改状态，不返回StatePatch
        """
        is_valid, msg = self._validate_content_length(content, "post", context.caller.id)
        if not is_valid:
            return msg

        if tags is None:
            tags = []

        agent = context.caller
        log_ctx = context.log_context or context.world.get_log_context()
        post_counter = self.state.get("post_counter", 0)
        new_post_id = f"post_{post_counter + 1}"

        # 创建帖子对象
        new_post = Post(
            post_id=new_post_id,
            author_id=agent.id,
            content=content,
            tags=tags,
            created_tick=context.world.step,
            reply_to=reply_to
        )

        # 记录事件
        event_data = {
            "post_id": new_post_id,
            "content": content[:50] + "..." if len(content) > 50 else content,
            "tags": tags,
            "is_reply": reply_to is not None
        }
        context.log_event("publish_post", source=agent.id, data=event_data)

        # 直接修改状态（通过代理机制）
        self.state["posts"][new_post_id] = new_post.model_dump()

        # 更新作者帖子索引
        if "author_to_post_ids" not in self.state:
            self.state["author_to_post_ids"] = {}
        if agent.id not in self.state["author_to_post_ids"]:
            self.state["author_to_post_ids"][agent.id] = []
        self.state["author_to_post_ids"][agent.id].append(new_post_id)

        # 更新计数器
        self.state["post_counter"] = post_counter + 1

        content_summary = summarize_text(content)

        if log_ctx:
            log_ctx.log_env(
                "INFO",
                EnvironmentEvent.POST_CREATED.value,
                step=context.world.step,
                post_id=new_post_id,
                author_id=agent.id,
                content_preview=content_summary["preview"],
                content_length=content_summary["length"],
                tags=tags,
                reply_to=reply_to,
            )
            if content_summary["truncated"]:
                log_ctx.log_env(
                    "DEBUG",
                    EnvironmentEvent.POST_CREATED.value,
                    step=context.world.step,
                    post_id=new_post_id,
                    author_id=agent.id,
                    content=content,
                )
            log_ctx.log_agent(
                agent.id,
                "INFO",
                AgentEvent.ACTION_EXECUTED.value,
                step=context.world.step,
                action="publish_post",
                action_params={
                    "content_preview": content_summary["preview"],
                    "tags": tags,
                    "reply_to": reply_to,
                },
                action_result=f"post_created:{new_post_id}",
            )

        # 🔧 DEBUG: Posts system monitoring
        print(f"📝 [POSTS] Agent {agent.id}: Published new post")
        print(f"   Post ID: {new_post_id}")
        print(f"   Content: {content[:50]}..." if len(content) > 50 else f"   Content: {content}")
        print(f"   Tags: {tags}")
        print(f"   Total posts: {post_counter + 1}")

        # 尝试为帖子生成向量并写入向量库
        await self._embed_and_store_post(
            post_id=new_post_id,
            content=content,
            tags=tags,
            created_tick=context.world.step,
            author_id=agent.id,
        )

        return f"Successfully published post {new_post_id}"

    # --- 向量化与检索辅助 ---

    def _ensure_post_collection(self):
        """获取/创建帖子向量集合，失败返回 None"""
        if self._post_collection is not None:
            return self._post_collection
        client = self._vector_client
        if not client:
            return None
        if not hasattr(client, "get_or_create_collection"):
            return None
        try:
            self._post_collection = client.get_or_create_collection(
                name="posts",
                metadata={"source": "social_network_env"}
            )
            return self._post_collection
        except Exception as exc:
            logger.warning(f"Failed to get_or_create_collection for posts: {exc}")
            return None

    def _build_post_text(self, content: str, tags: List[str]) -> str:
        """组合内容和标签用于向量化"""
        if not tags:
            return content
        tags_text = " ".join(f"#{t}" for t in tags)
        return f"{content}\nTags: {tags_text}"

    # --- 通知与摘要工具 ---

    def _ensure_notifications_state(self) -> None:
        """确保通知状态结构存在"""
        if "notifications" not in self.state or not isinstance(self.state.get("notifications"), dict):
            self.state["notifications"] = {"user_notifications": {}}
        if "user_notifications" not in self.state["notifications"]:
            self.state["notifications"]["user_notifications"] = {}

    def _push_notification(self, target_agent_id: str, notification_type: str, data: Dict[str, Any], created_tick: int) -> None:
        """向指定用户推送一条通知（不做去重，消费时聚合）"""
        self._ensure_notifications_state()
        user_notifs = self.state["notifications"]["user_notifications"]
        if target_agent_id not in user_notifs:
            user_notifs[target_agent_id] = {"notifications": []}

        notification = {
            "id": f"notif_{len(user_notifs[target_agent_id]['notifications']) + 1}",
            "type": notification_type,
            "created_tick": created_tick,
            "consumed": False,
            "data": data,
        }
        user_notifs[target_agent_id]["notifications"].append(notification)

    @staticmethod
    def _short_content(text: str, limit: int = 80) -> str:
        """生成短内容预览"""
        if not text:
            return ""
        return text if len(text) <= limit else text[:limit] + "..."

    def _count_reposts(self, post_id: str) -> int:
        """统计被转发次数（通过 reply_to 追踪）"""
        posts = self.state.get("posts", {})
        return sum(1 for p in posts.values() if p.get("reply_to") == post_id)

    @staticmethod
    def _strip_nested_repost_content(content: str) -> str:
        """仅保留帖子正文，剥离历史转发链，避免内容指数膨胀。"""
        if not content:
            return ""
        markers = ("\n\n--- 原帖 ", "\n\n--- 原贴 ")
        cut_positions = [content.find(marker) for marker in markers if content.find(marker) >= 0]
        if not cut_positions:
            return content
        return content[: min(cut_positions)].rstrip()

    def _get_agent_recent_posts(self, agent_id: str, limit: int = 5) -> List[Dict[str, Any]]:
        """获取指定用户最近的帖子（按时间倒序）"""
        author_posts = self.state.get("author_to_post_ids", {}).get(agent_id, [])
        posts = self.state.get("posts", {})
        recent = []
        for pid in author_posts[-limit:]:
            if pid in posts:
                recent.append(posts[pid])
        recent.sort(key=lambda x: x.get("created_tick", 0), reverse=True)
        return recent[:limit]

    async def _embed_and_store_post(self, post_id: str, content: str, tags: List[str],
                                    created_tick: int, author_id: str) -> None:
        """为帖子生成 embedding 并写入向量库"""
        if not self._embed_call:
            return
        text = self._build_post_text(content, tags)
        try:
            result = await self._embed_call([text])
            embeddings = result.get("result") if result else None
            if not embeddings:
                return
            embedding = embeddings[0]
        except Exception as exc:
            logger.warning(f"Embedding generation failed for post {post_id}: {exc}")
            return

        # 写回帖子结构
        try:
            if "posts" in self.state and post_id in self.state["posts"]:
                self.state["posts"][post_id]["embedding"] = embedding
        except Exception as exc:
            logger.warning(f"Failed to store embedding in state for post {post_id}: {exc}")

        # 向量库 upsert
        collection = self._ensure_post_collection()
        if not collection:
            return
        try:
            tags_text = ", ".join(tags) if tags else ""
            collection.upsert(
                ids=[post_id],
                embeddings=[embedding],
                metadatas=[{
                    "author_id": author_id,
                    "tags": tags_text,
                    "created_tick": created_tick,
                    "env": "social_network"
                }],
                documents=[text],
            )
            logger.debug(f"Upserted post {post_id} into vector store")
        except Exception as exc:
            logger.warning(f"Failed to upsert post {post_id} into vector store: {exc}")

    async def _rank_posts_with_similarity(self, agent: Agent, posts_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """召回候选帖子并根据多重信号排序"""
        cfg = self._config.social_media.recommendation
        if not posts_data:
            return []

        similarity_scores: Dict[str, float] = {}
        if cfg.use_embedding_similarity and self._embed_call:
            collection = self._ensure_post_collection()
            if collection:
                preference_text = self._build_agent_preference_text(agent)
                try:
                    result = await self._embed_call([preference_text])
                    q_embeddings = result.get("result") if result else None
                    if q_embeddings:
                        q_emb = q_embeddings[0]
                        n_results = max(
                            int(cfg.candidate_count * cfg.recall_multiplier),
                            len(posts_data),
                            cfg.post_count,
                        )
                        query_res = collection.query(
                            query_embeddings=[q_emb],
                            n_results=n_results,
                            include=["distances"],
                        )
                        ids_list = query_res.get("ids") or []
                        distances = query_res.get("distances") or []
                        ordered_ids = ids_list[0] if ids_list else []
                        ordered_distances = distances[0] if distances else []

                        # 将向量结果加入候选，并保存相似度分数
                        state_posts = self.state.get("posts", {})
                        post_map = {p.get("post_id"): p for p in posts_data if p.get("post_id")}
                        for idx, pid in enumerate(ordered_ids):
                            if pid not in post_map and pid in state_posts:
                                post_copy = dict(state_posts[pid])
                                posts_data.append(post_copy)
                                post_map[pid] = post_copy

                            if pid:
                                distance = ordered_distances[idx] if idx < len(ordered_distances) else None
                                similarity = 1 - distance if distance is not None else 0.0
                                similarity_scores[pid] = max(similarity, 0.0)
                except Exception as exc:
                    logger.warning(f"Similarity query failed for agent {agent.id}: {exc}")

        ranked = self._score_posts(agent, posts_data, similarity_scores)
        limit = cfg.post_count if cfg.post_count > 0 else len(ranked)
        return ranked[:limit]

    def _score_posts(
        self,
        agent: Agent,
        posts_data: List[Dict[str, Any]],
        similarity_scores: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """按照多重信号为帖子打分"""
        cfg = self._config.social_media.recommendation
        current_tick = getattr(self._world, "step", 0)
        decay_constant = cfg.time_decay_hours if cfg.time_decay_hours > 0 else 1.0

        scored_posts = []
        for post in posts_data:
            pid = post.get("post_id")
            if not pid:
                continue
            age = max(current_tick - post.get("created_tick", 0), 0)
            time_score = math.exp(-age / decay_constant)
            engagement_score = (
                cfg.like_score * len(post.get("likes", []))
                + cfg.reply_score * len(post.get("replies", []))
            )
            network_score = 0.0
            if self.graph and post.get("author_id") and self.graph.has_edge(agent.id, post["author_id"]):
                network_score = cfg.follow_bonus
            similarity_score = similarity_scores.get(pid, 0.0)

            total_score = (
                cfg.chronological_weight * time_score
                + cfg.engagement_weight * engagement_score
                + cfg.network_weight * network_score
                + cfg.similarity_weight * similarity_score
            )

            scored_posts.append(
                {
                    "post": post,
                    "score": total_score,
                    "created_tick": post.get("created_tick", 0),
                }
            )

        scored_posts.sort(key=lambda item: (item["score"], item["created_tick"]), reverse=True)
        return [item["post"] for item in scored_posts]

    def _collect_recent_posts_for_agent(self, agent: Agent, limit: int) -> List[Dict[str, Any]]:
        """获取Agent最近发布的帖子"""
        if limit <= 0:
            return []
        author_index = self.state.get("author_to_post_ids", {})
        post_ids = author_index.get(agent.id, [])
        posts = self.state.get("posts", {})

        recent_posts: List[Dict[str, Any]] = []
        for pid in reversed(post_ids):
            if pid in posts:
                recent_posts.append(dict(posts[pid]))
            if len(recent_posts) >= limit:
                break
        return recent_posts

    def _collect_recent_interactions_for_agent(self, agent_id: str, limit: int) -> List[Dict[str, Any]]:
        """收集Agent最近的点赞和评论"""
        if limit <= 0:
            return []
        posts = self.state.get("posts", {})
        interactions: List[Dict[str, Any]] = []
        for post in posts.values():
            for like_event in post.get("like_events", []):
                if like_event.get("agent_id") == agent_id:
                    interactions.append(
                        {
                            "type": "like",
                            "post_id": post.get("post_id"),
                            "content": post.get("content", ""),
                            "created_tick": like_event.get("created_tick", 0),
                        }
                    )
            for reply in post.get("replies", []):
                if reply.get("author_id") == agent_id:
                    interactions.append(
                        {
                            "type": "comment",
                            "post_id": post.get("post_id"),
                            "content": reply.get("content", ""),
                            "created_tick": reply.get("created_tick", 0),
                        }
                    )

        interactions.sort(key=lambda item: item.get("created_tick", 0), reverse=True)
        return interactions[:limit]

    def _build_agent_preference_text(self, agent: Agent) -> str:
        """构建用于召回的偏好文本"""
        cfg = self._config.social_media.recommendation
        sections: List[str] = []

        persona = ""
        try:
            persona = agent.get_raw_data().get("persona", "") or ""
        except Exception:
            persona = ""
        if persona:
            sections.append(f"Persona:\n{persona}")

        if cfg.include_recent_posts_in_query:
            recent_posts = self._collect_recent_posts_for_agent(agent, cfg.recent_post_limit)
            if recent_posts:
                post_lines = []
                for post in recent_posts:
                    tags = ", ".join(post.get("tags", [])) or "无标签"
                    post_lines.append(
                        f"[{post.get('post_id')}] {post.get('content', '')}\nTags: {tags}"
                    )
                sections.append("Recent posts:\n" + "\n---\n".join(post_lines))

            interactions = self._collect_recent_interactions_for_agent(agent.id, cfg.interaction_limit)
            if interactions:
                interaction_lines = []
                for inter in interactions:
                    interaction_lines.append(
                        f"{inter['type'].title()} {inter.get('post_id')}: {inter.get('content', '')}"
                    )
                sections.append("Recent interactions:\n" + "\n".join(interaction_lines))

        if self.graph and agent.id in self.graph:
            follows = list(self.graph.successors(agent.id))
            if follows:
                limit = cfg.recent_post_limit or 3
                sections.append("Following:\n" + ", ".join(follows[:limit]))

        return "\n\n".join(sections) if sections else "Social feed preference"

    # --- 通知聚合与视野 ---

    def _format_notifications(self, agent: Agent) -> str:
        """将未读通知聚合并格式化输出，消费后标记为已读"""
        self._ensure_notifications_state()
        user_notifs = self.state["notifications"]["user_notifications"].get(agent.id, {})
        notifications = user_notifs.get("notifications", [])
        unread = [n for n in notifications if not n.get("consumed")]

        if not unread:
            return "🔔 你暂无新通知"

        max_lines = 20
        lines: List[str] = ["🔔 通知中心", "=" * 18]

        def add_line(text: str) -> None:
            if len(lines) < max_lines:
                lines.append(text)

        # 分类型聚合
        like_groups: Dict[str, Dict[str, Any]] = {}
        comment_groups: Dict[str, Dict[str, Any]] = {}
        repost_groups: Dict[str, Dict[str, Any]] = {}
        followers: List[str] = []

        for notif in unread:
            n_type = notif.get("type")
            data = notif.get("data", {}) or {}
            if n_type == "post_like":
                pid = data.get("post_id")
                if not pid:
                    continue
                grp = like_groups.setdefault(pid, {"new": 0, "actors": []})
                grp["new"] += 1
                actor = data.get("interactor_id")
                if actor:
                    grp["actors"].append(actor)
            elif n_type == "post_comment":
                pid = data.get("post_id")
                if not pid:
                    continue
                grp = comment_groups.setdefault(pid, {"new": 0, "samples": []})
                grp["new"] += 1
                preview = data.get("comment_preview") or self._short_content(data.get("content", ""), 40)
                if preview:
                    grp["samples"].append(preview)
            elif n_type == "post_repost":
                pid = data.get("post_id")
                if not pid:
                    continue
                grp = repost_groups.setdefault(pid, {"new": 0, "samples": []})
                grp["new"] += 1
                preview = data.get("commentary_preview") or self._short_content(data.get("commentary", ""), 40)
                if preview:
                    grp["samples"].append(preview)
            elif n_type == "new_follower":
                follower_id = data.get("follower_id")
                if follower_id:
                    followers.append(follower_id)

        posts = self.state.get("posts", {})

        def _post_preview(pid: str) -> str:
            post = posts.get(pid, {})
            return self._short_content(post.get("content", ""), 60)

        # 点赞聚合
        if like_groups:
            add_line("👍 点赞")
            sorted_likes = sorted(like_groups.items(), key=lambda kv: kv[1]["new"], reverse=True)
            detailed = sorted_likes[:5]
            remaining = sorted_likes[5:]
            for pid, info in detailed:
                total_likes = len(posts.get(pid, {}).get("likes", []))
                new_count = info["new"]
                actors = list(dict.fromkeys(info.get("actors", [])))
                if new_count <= 5 and actors:
                    actor_text = "，".join(actors[:5])
                    text = f"· 帖子 {pid}（{_post_preview(pid)}）新增{new_count}赞（共{total_likes}赞）：{actor_text}"
                else:
                    text = f"· 帖子 {pid}（{_post_preview(pid)}）新增{new_count}赞（共{total_likes}赞）"
                add_line(text)
            if remaining:
                rest_new = sum(info["new"] for _, info in remaining)
                rest_total = sum(len(posts.get(pid, {}).get("likes", [])) for pid, _ in remaining)
                add_line(f"· 其他 {len(remaining)} 条帖子共新增 {rest_new} 赞（累计 {rest_total} 赞）")

        # 评论聚合
        if comment_groups:
            add_line("💬 评论")
            sorted_comments = sorted(comment_groups.items(), key=lambda kv: kv[1]["new"], reverse=True)
            detailed = sorted_comments[:5]
            remaining = sorted_comments[5:]
            for pid, info in detailed:
                total_replies = len(posts.get(pid, {}).get("replies", []))
                new_count = info["new"]
                samples = info.get("samples", [])[:3]
                sample_text = f" 示例：{' | '.join(samples)}" if samples else ""
                add_line(f"· 帖子 {pid}（{_post_preview(pid)}）新增{new_count}条评论（共{total_replies}条）。{sample_text}")
            if remaining:
                rest_new = sum(info["new"] for _, info in remaining)
                rest_total = sum(len(posts.get(pid, {}).get("replies", [])) for pid, _ in remaining)
                add_line(f"· 其他 {len(remaining)} 条帖子共新增 {rest_new} 条评论（累计 {rest_total} 条）")

        # 转发聚合
        if repost_groups:
            add_line("🔁 转发")
            sorted_reposts = sorted(repost_groups.items(), key=lambda kv: kv[1]["new"], reverse=True)
            detailed = sorted_reposts[:5]
            remaining = sorted_reposts[5:]
            for pid, info in detailed:
                total_reposts = self._count_reposts(pid)
                new_count = info["new"]
                samples = info.get("samples", [])[:2]
                sample_text = f" 示例：{' | '.join(samples)}" if samples else ""
                add_line(f"· 帖子 {pid}（{_post_preview(pid)}）新增{new_count}次转发（共{total_reposts}次）。{sample_text}")
            if remaining:
                rest_new = sum(info["new"] for _, info in remaining)
                rest_total = sum(self._count_reposts(pid) for pid, _ in remaining)
                add_line(f"· 其他 {len(remaining)} 条帖子共新增 {rest_new} 次转发（累计 {rest_total} 次）")

        # 新关注
        if followers:
            unique_followers = list(dict.fromkeys(followers))
            if len(unique_followers) <= 5:
                add_line(f"👥 新增关注：{', '.join(unique_followers)}")
            else:
                add_line(f"👥 新增关注 {len(unique_followers)} 人，其中包含：{', '.join(unique_followers[:5])} 等")

        # 标记已读
        for notif in unread:
            notif["consumed"] = True

        # 如果超过行数上限，补充省略提示
        if len(lines) > max_lines:
            lines = lines[:max_lines - 1] + [f"... 还有 {len(unread)} 条通知已被合并展示"]

        return "\n".join(lines)

    @fov(description="获取与你相关的互动通知（点赞、评论、转发、关注）")
    async def get_notifications(self, agent: Agent, env) -> str:
        """消费未读通知并返回聚合摘要"""
        return self._format_notifications(agent)

    @action(description="点赞指定的帖子（post_id 必须来自 FoV 或系统明确提示）")
    def like_post(self, context: ExecutionContext, post_id: str) -> str:
        """点赞帖子Action（适配新架构）"""
        agent: Agent = context.caller
        log_ctx = context.log_context or context.world.get_log_context()

        # 🔧 DEBUG: 添加调试信息
        print(f"🔍 [DEBUG] like_post: agent={agent.id}, post_id={post_id}")
        print(f"🔍 [DEBUG] state type: {type(self.state)}")

        posts = self.state.get("posts", {})
        print(f"🔍 [DEBUG] posts type: {type(posts)}, count: {len(posts)}")

        post = posts.get(post_id)
        print(f"🔍 [DEBUG] post found: {post is not None}")

        if not post:
            logger.warning(f"Agent {agent.id} 尝试点赞不存在的帖子 {post_id}")
            # 获取当前可见的帖子ID作为提示
            visible_posts = self._get_real_posts_only(agent)
            valid_ids = [p.get('post_id') for p in visible_posts]
            valid_ids_hint = ", ".join(valid_ids)
            error_message = (
                f"错误：点赞失败。帖子 '{post_id}' 不存在。请只引用你当前视野中展示的真实帖子ID：{valid_ids_hint}"
            )
            if log_ctx:
                log_ctx.log_env(
                    "WARNING",
                    EnvironmentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    agent_id=agent.id,
                    action="like_post",
                    post_id=post_id,
                    error="post_not_found",
                )
                log_ctx.log_agent(
                    agent.id,
                    "WARNING",
                    AgentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    action="like_post",
                    action_params={"post_id": post_id},
                    error="post_not_found",
                )
            return error_message

        current_likes = post.get("likes", [])
        if agent.id in current_likes:
            logger.debug(f"Agent {agent.id} 已经点赞过帖子 {post_id}")
            return f"Already liked post {post_id}"

        context.log_event("like_post", source=agent.id, data={"post_id": post_id})

        # 🔧 修复：使用正确的状态访问方式
        print(f"🔍 [DEBUG] 准备修改帖子 {post_id} 的likes")

        # 确保posts字典存在
        if "posts" not in self.state:
            self.state["posts"] = {}

        # 确保特定帖子存在
        if post_id not in self.state["posts"]:
            print(f"🔍 [DEBUG] 帖子 {post_id} 在state中不存在!")
            return f"Post {post_id} not found in state"

        # 确保likes字段存在
        if "likes" not in self.state["posts"][post_id]:
            self.state["posts"][post_id]["likes"] = []
        if "like_events" not in self.state["posts"][post_id]:
            self.state["posts"][post_id]["like_events"] = []

        # 添加点赞
        self.state["posts"][post_id]["likes"].append(agent.id)
        like_event = LikeEvent(agent_id=agent.id, created_tick=context.world.step)
        self.state["posts"][post_id]["like_events"].append(like_event.model_dump())

        # 🔧 DEBUG: Posts interaction monitoring
        current_likes = len(self.state["posts"][post_id]["likes"])
        print(f"👍 [POSTS] Agent {agent.id}: Liked post {post_id}")
        print(f"   Total likes now: {current_likes}")

        if log_ctx:
            log_ctx.log_env(
                "INFO",
                EnvironmentEvent.POST_LIKED.value,
                step=context.world.step,
                post_id=post_id,
                agent_id=agent.id,
                total_likes=current_likes,
            )
            log_ctx.log_agent(
                agent.id,
                "INFO",
                AgentEvent.ACTION_EXECUTED.value,
                step=context.world.step,
                action="like_post",
                action_params={"post_id": post_id},
                action_result="liked",
            )

        # 推送通知给帖子作者
        post_author = post.get("author_id")
        if post_author and post_author != agent.id:
            self._push_notification(
                target_agent_id=post_author,
                notification_type="post_like",
                data={"post_id": post_id, "interactor_id": agent.id},
                created_tick=context.world.step,
            )

        return f"Successfully liked post {post_id}"

    @action(description="评论指定的帖子")
    async def comment(self, context: ExecutionContext, post_id: str, content: str) -> str:
        """发表评论的Action"""
        is_valid, msg = self._validate_content_length(content, "comment", context.caller.id)
        if not is_valid:
            return msg

        agent: Agent = context.caller
        log_ctx = context.log_context or context.world.get_log_context()
        posts = self.state.get("posts", {})
        post = posts.get(post_id)

        if not post:
            logger.warning(f"Agent {agent.id} 尝试评论不存在的帖子 {post_id}")
            return f"Post {post_id} not found"

        reply_id = f"{post_id}_reply_{uuid.uuid4().hex[:6]}"
        reply = Reply(
            reply_id=reply_id,
            author_id=agent.id,
            original_post_id=post_id,
            content=content,
            created_tick=context.world.step,
        )
        if "replies" not in self.state["posts"][post_id]:
            self.state["posts"][post_id]["replies"] = []
        self.state["posts"][post_id]["replies"].append(reply.model_dump())

        content_summary = summarize_text(content)
        context.log_event("comment_post", source=agent.id, data={"post_id": post_id, "reply_id": reply_id})
        if log_ctx:
            log_ctx.log_env(
                "INFO",
                EnvironmentEvent.POST_REPLIED.value,
                step=context.world.step,
                post_id=post_id,
                agent_id=agent.id,
                action="comment",
                reply_id=reply_id,
                content_preview=content_summary["preview"],
            )
            log_ctx.log_agent(
                agent.id,
                "INFO",
                AgentEvent.ACTION_EXECUTED.value,
                step=context.world.step,
                action="comment",
                action_params={"post_id": post_id, "content_preview": content_summary["preview"]},
                action_result=f"commented:{post_id}",
            )

        # 推送通知给帖子作者
        post_author = post.get("author_id")
        if post_author and post_author != agent.id:
            self._push_notification(
                target_agent_id=post_author,
                notification_type="post_comment",
                data={"post_id": post_id, "comment_preview": content_summary["preview"], "interactor_id": agent.id},
                created_tick=context.world.step,
            )

        return f"Successfully commented on post {post_id}"

    @action(description="转发指定的帖子并添加评论")
    async def repost(self, context: ExecutionContext, post_id: str, commentary: str = "") -> str:
        """转发帖子Action"""
        posts = self.state.get("posts", {})
        original = posts.get(post_id)
        if not original:
            logger.warning(f"Agent {context.caller.id} 尝试转发不存在的帖子 {post_id}")
            return f"Post {post_id} not found"

        commentary = commentary or "转发"
        is_valid, msg = self._validate_content_length(commentary, "repost", context.caller.id)
        if not is_valid:
            return msg

        parent_content = self._strip_nested_repost_content(str(original.get("content", "") or ""))
        combined_content = f"{commentary}\n\n--- 原帖 {post_id} ---\n{parent_content}"
        original_tags = list(original.get("tags", []))

        result = await self.publish_post(
            context=context,
            content=combined_content,
            tags=original_tags,
            reply_to=post_id,
        )
        content_summary = summarize_text(commentary)
        context.log_event("repost_post", source=context.caller.id, data={"post_id": post_id})
        log_ctx = context.log_context or context.world.get_log_context()
        if log_ctx:
            log_ctx.log_env(
                "INFO",
                EnvironmentEvent.POST_REPOSTED.value,
                step=context.world.step,
                post_id=post_id,
                agent_id=context.caller.id,
                content_preview=content_summary["preview"],
            )
            log_ctx.log_agent(
                context.caller.id,
                "INFO",
                AgentEvent.ACTION_EXECUTED.value,
                step=context.world.step,
                action="repost",
                action_params={"post_id": post_id, "commentary_preview": content_summary["preview"]},
                action_result=f"reposted:{post_id}",
            )

        # 推送通知给原帖作者
        post_author = original.get("author_id")
        if post_author and post_author != context.caller.id:
            self._push_notification(
                target_agent_id=post_author,
                notification_type="post_repost",
                data={"post_id": post_id, "commentary_preview": content_summary["preview"], "interactor_id": context.caller.id},
                created_tick=context.world.step,
            )

        return f"Reposted {post_id}: {result}"

    @action(description="关注另一个Agent，建立社交连接")
    def follow(self, context: ExecutionContext, target_agent_id: str) -> str:
        """关注其他Agent的Action（适配新架构）"""
        agent = context.caller
        log_ctx = context.log_context or context.world.get_log_context()

        # 验证目标Agent存在
        if target_agent_id not in context.world.agents_data:
            logger.warning(f"Agent {agent.id} 尝试关注不存在的Agent {target_agent_id}")
            # 获取当前可见的其他用户ID作为提示
            other_agents = self._get_other_agents_in_network(agent)
            valid_ids_hint = ", ".join(other_agents[:5]) # 最多显示5个作为示例
            error_message = (
                f"错误：关注失败。用户 '{target_agent_id}' 不存在。"
                f"请从你看到的信息中选择一个有效的用户ID，例如：{valid_ids_hint}。"
                f"不要编造用户ID。"
            )
            if log_ctx:
                log_ctx.log_env(
                    "WARNING",
                    EnvironmentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    agent_id=agent.id,
                    action="follow",
                    target_id=target_agent_id,
                    error="agent_not_found",
                )
                log_ctx.log_agent(
                    agent.id,
                    "WARNING",
                    AgentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    action="follow",
                    action_params={"target_agent_id": target_agent_id},
                    error="agent_not_found",
                )
            return error_message

        # 防止自己关注自己
        if agent.id == target_agent_id:
            logger.warning(f"Agent {agent.id} 尝试关注自己")
            if log_ctx:
                log_ctx.log_env(
                    "WARNING",
                    EnvironmentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    agent_id=agent.id,
                    action="follow",
                    target_id=target_agent_id,
                    error="self_follow",
                )
                log_ctx.log_agent(
                    agent.id,
                    "WARNING",
                    AgentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    action="follow",
                    action_params={"target_agent_id": target_agent_id},
                    error="self_follow",
                )
            return "Cannot follow yourself。请选择其他用户。"

        # 检查是否已经关注
        if self.graph and self.graph.has_edge(agent.id, target_agent_id):
            logger.debug(f"Agent {agent.id} 已经关注了 {target_agent_id}")
            return f"Already following {target_agent_id}"

        # 直接修改self.graph（按设计文档要求）
        if self.graph is None:
            logger.error("网络图未初始化")
            if log_ctx:
                log_ctx.log_env(
                    "ERROR",
                    EnvironmentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    agent_id=agent.id,
                    action="follow",
                    target_id=target_agent_id,
                    error="graph_not_initialized",
                )
                log_ctx.log_agent(
                    agent.id,
                    "ERROR",
                    AgentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    action="follow",
                    action_params={"target_agent_id": target_agent_id},
                    error="graph_not_initialized",
                )
            return "Network graph not initialized"

        self.graph.add_edge(agent.id, target_agent_id)

        follower_cv = self._calculate_cv_for_node(self.graph, agent.id)
        followee_cv = self._calculate_cv_for_node(self.graph, target_agent_id)

        # 记录事件
        context.log_event("follow", source=agent.id, data={
            "target": target_agent_id,
            "follower_cv": follower_cv,
            "followee_cv": followee_cv
        })

        logger.info(f"Agent {agent.id} 关注了 {target_agent_id}")
        if log_ctx:
            log_ctx.log_env(
                "INFO",
                EnvironmentEvent.AGENT_FOLLOWED.value,
                step=context.world.step,
                follower_id=agent.id,
                followee_id=target_agent_id,
                follower_cv=follower_cv,
                followee_cv=followee_cv,
            )
            log_ctx.log_agent(
                agent.id,
                "INFO",
                AgentEvent.ACTION_EXECUTED.value,
                step=context.world.step,
                action="follow",
                action_params={"target_agent_id": target_agent_id},
                action_result=f"followed:{target_agent_id}",
            )

        # 推送通知给被关注者
        if target_agent_id != agent.id:
            self._push_notification(
                target_agent_id=target_agent_id,
                notification_type="new_follower",
                data={"follower_id": agent.id},
                created_tick=context.world.step,
            )

        return f"Successfully followed {target_agent_id}"

    @action(description="取消关注指定的Agent")
    def unfollow(self, context: ExecutionContext, target_agent_id: str) -> str:
        """取消关注Action"""
        agent = context.caller
        log_ctx = context.log_context or context.world.get_log_context()

        # 检查是否正在关注
        if not self.graph or not self.graph.has_edge(agent.id, target_agent_id):
            logger.debug(f"Agent {agent.id} 没有关注 {target_agent_id}")
            if log_ctx:
                log_ctx.log_env(
                    "WARNING",
                    EnvironmentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    agent_id=agent.id,
                    action="unfollow",
                    target_id=target_agent_id,
                    error="not_following",
                )
                log_ctx.log_agent(
                    agent.id,
                    "WARNING",
                    AgentEvent.ACTION_FAILED.value,
                    step=context.world.step,
                    action="unfollow",
                    action_params={"target_agent_id": target_agent_id},
                    error="not_following",
                )
            return f"Not following {target_agent_id}"

        # 直接修改self.graph
        self.graph.remove_edge(agent.id, target_agent_id)

        # 记录事件
        context.log_event("unfollow", source=agent.id, data={"target": target_agent_id})

        logger.info(f"Agent {agent.id} 取消关注了 {target_agent_id}")
        if log_ctx:
            log_ctx.log_env(
                "INFO",
                EnvironmentEvent.AGENT_UNFOLLOWED.value,
                step=context.world.step,
                follower_id=agent.id,
                followee_id=target_agent_id,
            )
            log_ctx.log_agent(
                agent.id,
                "INFO",
                AgentEvent.ACTION_EXECUTED.value,
                step=context.world.step,
                action="unfollow",
                action_params={"target_agent_id": target_agent_id},
                action_result=f"unfollowed:{target_agent_id}",
            )
        return f"Successfully unfollowed {target_agent_id}"

    # @agent_tool
    # def reply_to_post(self, context: ExecutionContext, post_id: str, content: str) -> str:
    #     """回复帖子Action（简化版）"""
    #     # 简化实现
    #     return f"Replied to post {post_id}"

    # @agent_tool
    # def report_post(self, context: ExecutionContext, post_id: str, reason: str) -> str:
    #     """举报帖子Action（简化版）"""
    #     # 简化实现
    #     return f"Reported post {post_id} for {reason}"

    # --- 3. 视野 (FoV) 函数 (Agent如何感知世界) ---

    @fov(description="获取个性化推荐动态，包含推荐帖子和可关注用户信息")
    async def get_recommended_feed(self, agent: Agent, env) -> str:
        """
        获取推荐动态并格式化为人类可读的字符串

        Returns:
            str: 格式化的推荐动态字符串，便于LLM理解
        """
        # 获取推荐帖子数据
        posts_data = self._get_real_posts_only(agent)

        # 🔍 DEBUG: 添加FoV调试信息
        print(f"🔍 [FoV DEBUG] Agent {agent.id} - posts_data count: {len(posts_data)}")
        for i, post in enumerate(posts_data):
            post_id = post.get('post_id', 'N/A')
            author_id = post.get('author_id', 'Unknown')
            print(f"🔍 [FoV DEBUG] Post {i+1}: ID={post_id}, Author={author_id}")

        # 可选：基于向量的相似度重排
        posts_data = await self._rank_posts_with_similarity(agent, posts_data)

        if not posts_data:
            # 如果没有帖子，至少显示网络中的其他Agent供关注
            other_agents = self._get_other_agents_in_network(agent)
            if other_agents:
                # 只显示前5个用户，如果超过5个则添加省略号
                display_agents = other_agents[:5]
                agents_list = ', '.join(display_agents)
                if len(other_agents) > 5:
                    agents_list += '...'

                return f"📭 暂无推荐内容,你可以等待，或者发表你的帖子 "
            else:
                return "📭 暂无推荐内容"

        # 格式化为人类可读的字符串
        feed_sections = []
        feed_sections.append("📱 个性化推荐动态")
        current_tick = int(getattr(self._world, "step", 0))
        feed_sections.append(f"⏰ 当前仿真第 {current_tick} 步")
        feed_sections.append("")  # 空行分隔

        for i, post in enumerate(posts_data, 1):
            # 🔧 增加view_count统计 - 每次查看时增加计数
            post_id = post.get('post_id', 'N/A')
            self._increment_view_count(post_id)

            author = post.get("author_id", "Unknown")
            created_tick = post.get("created_tick", 0)
            content = post.get("content", "")
            special_tags = post.get("special_tags", [])
            if special_tags:
                content += f"\n[系统标记: {', '.join(special_tags)}]"

            tags = post.get("tags", [])
            tags_text = ", ".join(tags) if tags else "无"

            likes_count = len(post.get("likes", []))
            replies_count = len(post.get("replies", []))
            # view_count 以 state 中的最新值为准（上面已自增）
            try:
                view_count = self.state["posts"][post_id].get("view_count", 0)
            except Exception:
                view_count = post.get("view_count", 0)
            repost_count = self._count_reposts(post_id)
            reply_to = post.get("reply_to")

            time_diff = current_tick - created_tick
            time_text = f"第{created_tick}步 (本步发布)" if time_diff == 0 else f"第{created_tick}步 ({time_diff}步前)"

            block_lines = [
                f"{i}. 帖子 {post_id}",
                f"作者: {author}",
                f"发布: {time_text}",
                f"内容: {content}",
                f"标签: {tags_text}",
                f"互动: 👍{likes_count} 💬{replies_count} 🔁{repost_count} 👁️{view_count}",
            ]
            if reply_to:
                block_lines.append(f"引用/回复: {reply_to}")

            feed_sections.append("\n".join(block_lines))

        feed_sections.append("\n提示: 可以使用 like_post、comment、repost、follow 等动作与帖子互动")
        feed_sections.append("注意: 引用帖子或用户时请使用视野中呈现的真实 ID")

        # 记录本次推荐的帖子ID（覆盖式存储）
        if "recommended_posts" not in self.state or not isinstance(self.state.get("recommended_posts"), dict):
            self.state["recommended_posts"] = {}
        self.state["recommended_posts"][agent.id] = [p.get("post_id") for p in posts_data if p.get("post_id")]

        formatted_feed = "\n".join(feed_sections)

        # 监控输出
        print(f"📱 Agent {agent.id} FoV - Recommended Feed:")
        print(f"   Generated {len(posts_data)} posts in feed")
        print(f"   Feed length: {len(formatted_feed)} characters")

        # 🔍 DEBUG: 显示完整的FoV内容
        print(f"🔍 [FoV FULL] Agent {agent.id} sees:")
        print(formatted_feed[:500] + "..." if len(formatted_feed) > 500 else formatted_feed)

        return formatted_feed

    @fov(description="获取热门帖子的视野（可配置启用）", cache_on_step=True)
    async def get_trending_feed(self, agent: Agent, env) -> str:
        """返回热门帖子视图（仅作为 FoV，不作为 agent 主动 action）"""
        trending_cfg = self._config.social_media.trending
        if not trending_cfg.enabled:
            return "🔥 热门视图已关闭"

        posts_data = self._get_real_posts_only(None)
        if not posts_data:
            return "🔥 暂无热门内容"

        trending_sections = []
        trending_sections.append("🔥 **热门动态**")
        trending_sections.append("=" * 30)

        for i, post in enumerate(posts_data[:2], 1):
            post_id = post.get('post_id', 'N/A')
            likes_count = len(post.get("likes", []))
            replies_count = len(post.get("replies", []))
            view_count = post.get("view_count", 0)

            content = post.get('content', '')[:60]
            special_tags = post.get("special_tags", [])
            if special_tags:
                content += f" [系统标记: {', '.join(special_tags)}]"

            post_info = [
                f"**🔥 热门 {i}**",
                f"👤 {post.get('author_id', 'Unknown')} (ID: {post.get('author_id', 'Unknown')})",
                f"💬 {content}...",
                f"📊 {likes_count}👍 {replies_count}💬 {view_count}👁️",
                f"🆔 帖子 ID: {post_id}",
            ]

            trending_sections.append("\n".join(post_info))

            if i < 2:
                trending_sections.append("-" * 20)

        return "\n".join(trending_sections)

    def _increment_view_count(self, post_id: str) -> None:
        """增加帖子的查看计数"""
        if "posts" in self.state and post_id in self.state["posts"]:
            # 确保view_count字段存在（向后兼容）
            if "view_count" not in self.state["posts"][post_id]:
                self.state["posts"][post_id]["view_count"] = 0

            self.state["posts"][post_id]["view_count"] += 1

            # 🔧 DEBUG: View count monitoring
            current_count = self.state["posts"][post_id]["view_count"]
            print(f"👁️ [VIEW COUNT] Post {post_id}: view_count increased to {current_count}")

    def _validate_content_length(self, content: str, content_type: str, agent_id: str) -> tuple[bool, str]:
        """
        验证帖子/评论/转发的文本长度。

        Args:
            content: 文本内容
            content_type: 'post' | 'comment' | 'repost'
            agent_id: 发起者ID

        Returns:
            (is_valid, message_or_content)
        """
        limit = getattr(self._config.social_media, "content_length_limit", -1)
        if limit is None or limit < 0:
            return True, content

        length = len(content or "")
        if length > limit:
            type_names = {"post": "帖子", "comment": "评论", "repost": "转发评论"}
            type_name = type_names.get(content_type, "内容")
            return False, f"{type_name}内容过长。最多 {limit} 字符，当前 {length}。请缩短后再发布。"
        return True, content

    def _get_real_posts_only(self, agent: Agent|None = None) -> List[Dict[str, Any]]:
        """
        获取真实帖子数据（不生成任何示例数据）

        Args:
            agent: 当前Agent，用于过滤自己的帖子，为None时不过滤

        Returns:
            List[Dict]: 真实帖子数据列表，如果没有则返回空列表
        """
        real_posts = self.state.get("posts", {})
        if real_posts:
            cfg = self._config.social_media.recommendation
            candidate_limit = max(cfg.candidate_count, cfg.post_count)
            filtered_posts: List[Dict[str, Any]] = []
            for post_id, post_data in real_posts.items():
                author_id = post_data.get("author_id", "")
                if agent and author_id == agent.id:
                    continue
                filtered_posts.append(dict(post_data))

            if not filtered_posts:
                return []

            sorted_posts = sorted(filtered_posts, key=lambda x: x.get("created_tick", 0), reverse=True)
            return sorted_posts[:candidate_limit]

        # 如果没有真实帖子，返回空列表
        return []

    def _get_other_agents_in_network(self, current_agent: Agent) -> List[str]:
        """
        获取网络中的其他Agent ID列表（排除当前Agent）

        Args:
            current_agent: 当前Agent

        Returns:
            List[str]: 其他Agent的ID列表
        """
        # 使用self._world获取agent数据
        if not hasattr(self, '_world') or self._world is None:
            logger.warning("self._world is not available, returning empty list")
            return []

        all_agent_ids = list(self._world.agents_data.keys())
        other_agents = [aid for aid in all_agent_ids if aid != current_agent.id]

        # 🔍 DEBUG: 调试Agent列表
        # print(f"🔍 [FoV DEBUG] All agents: {all_agent_ids}")
        # print(f"🔍 [FoV DEBUG] Other agents for {current_agent.id}: {len(other_agents)}")

        return other_agents

    @fov(description="获取当前网络上热门帖子的动态信息")
    async def get_trending_posts(self) -> str:
        """
        返回当前网络上热门帖子的格式化字符串

        Returns:
            str: 格式化的热门帖子字符串
        """
        # 获取真实的热门帖子
        posts_data = self._get_real_posts_only(None)

        if not posts_data:
            return "🔥 暂无热门内容"

        # 格式化热门帖子
        trending_sections = []
        trending_sections.append("🔥 **热门动态**")
        trending_sections.append("=" * 30)

        # 只显示前2条作为热门
        for i, post in enumerate(posts_data[:2], 1):
            # 🔧 增加view_count统计 - 查看热门帖子时也增加计数
            post_id = post.get('post_id', 'N/A')
            self._increment_view_count(post_id)

            likes_count = len(post.get("likes", []))
            replies_count = len(post.get("replies", []))
            view_count = post.get("view_count", 0)

            # 🔧 处理special_tags - 如果有干预标签，追加到内容后面
            content = post.get('content', '')[:60]
            special_tags = post.get("special_tags", [])
            if special_tags:
                content += f" [系统标记: {', '.join(special_tags)}]"

            post_info = [
                f"**🔥 热门 {i}**",
                f"👤 {post.get('author_id', 'Unknown')}",
                f"💬 {content}...",
                f"📊 {likes_count}👍 {replies_count}💬 {view_count}👁️"
            ]

            trending_sections.append("\n".join(post_info))

            if i < 2:
                trending_sections.append("-" * 20)

        formatted_trending = "\n".join(trending_sections)

        # 监控输出
        print(f"🔥 FoV - Trending Posts: Generated trending feed")

        return formatted_trending

    @action(description="查看指定帖子的详细信息（内容、点赞、评论、转发）")
    async def get_post_details(self, agent, post_id: str) -> str:
        """返回帖子的完整详情（无权限限制，聚合显示）"""
        posts = self.state.get("posts", {})
        post = posts.get(post_id)
        if not post:
            return f"❌ 帖子 {post_id} 不存在"

        lines: List[str] = [
            f"📄 帖子详情: {post_id}",
            "=" * 30,
            f"👤 作者: {post.get('author_id', 'Unknown')}",
            f"⏰ 发布时间: 第{post.get('created_tick', 0)}步",
        ]

        content = post.get("content", "")
        lines.append(f"💬 内容:\n{content}")

        tags = post.get("tags", [])
        if tags:
            lines.append(f"🏷️ 标签: {', '.join(tags)}")

        # 互动统计
        likes_list = post.get("likes", []) or []
        replies_list = post.get("replies", []) or []
        repost_count = self._count_reposts(post_id)
        view_count = post.get("view_count", 0)
        lines.append(f"📊 互动: 👍{len(likes_list)} 💬{len(replies_list)} 🔁{repost_count} 👁️{view_count}")

        # 点赞详情（聚合）
        if likes_list:
            if len(likes_list) <= 5:
                lines.append(f"👍 点赞用户：{', '.join(likes_list)}")
            else:
                like_events = post.get("like_events", [])
                recent = []
                if like_events:
                    recent_sorted = sorted(like_events, key=lambda x: x.get("created_tick", 0), reverse=True)
                    for ev in recent_sorted:
                        aid = ev.get("agent_id")
                        if aid and aid not in recent:
                            recent.append(aid)
                        if len(recent) >= 5:
                            break
                recent_text = ", ".join(recent) if recent else ""
                suffix = f" 最近：{recent_text}" if recent_text else ""
                lines.append(f"👍 共 {len(likes_list)} 赞。{suffix}".strip())

        # 评论详情（聚合展示最近10条）
        if replies_list:
            lines.append("💬 评论（按时间倒序，最多10条）：")
            sorted_replies = sorted(replies_list, key=lambda x: x.get("created_tick", 0), reverse=True)
            show_replies = sorted_replies[:10]
            for reply in show_replies:
                lines.append(
                    f"- {reply.get('author_id', 'Unknown')}（第{reply.get('created_tick', 0)}步）：{reply.get('content', '')}"
                )
            if len(sorted_replies) > 10:
                lines.append(f"... 还有 {len(sorted_replies) - 10} 条更早的评论")

        # 回复来源 / 转发来源
        reply_to = post.get("reply_to")
        if reply_to:
            lines.append(f"↪️ 此帖引用/回复自: {reply_to}")

        return "\n".join(lines)

    @fov(description="获取指定Agent的个人资料和社交统计信息")
    async def get_agent_profile(self, agent_id: str) -> str:
        """
        返回特定agent的格式化个人资料

        Returns:
            str: 格式化的个人资料字符串
        """
        # 尝试获取真实agent信息
        agent_data = self._world.agents_data.get(agent_id)
        if not agent_data:
            return f"❌ 用户 {agent_id} 不存在"

        # 格式化个人资料
        profile_sections = [
            f"👤 **用户资料: {agent_id}**",
            "=" * 30,
            f"🏷️ **类型**: {agent_data.get('type', 'unknown')}",
            f"🤖 **架构**: {agent_data.get('archetype', 'unknown')}",
        ]

        # 获取状态信息
        state = agent_data.get("state", {})
        if state.get("interests"):
            profile_sections.append(f"🎯 **兴趣**: {', '.join(state['interests'])}")

        if state.get("mood"):
            profile_sections.append(f"😊 **心情**: {state['mood']}")

        # 社交统计（从真实数据计算）
        followers_count = 0
        following_count = 0
        posts_count = 0

        # 如果有网络图，计算真实的关注者和关注数量
        if self.graph:
            followers_count = len(list(self.graph.predecessors(agent_id)))
            following_count = len(list(self.graph.successors(agent_id)))

        # 如果有帖子数据，计算真实的帖子数量
        author_posts = self.state.get("author_to_post_ids", {}).get(agent_id, [])
        posts_count = len(author_posts)

        profile_sections.extend([
            "",
            "📊 **社交统计**",
            f"👥 关注者: {followers_count}",
            f"👤 关注中: {following_count}",
            f"📝 帖子: {posts_count}"
        ])

        # 最近帖子预览
        recent_posts = self._get_agent_recent_posts(agent_id, limit=5)
        if recent_posts:
            profile_sections.extend(["", "📝 **最近帖子**"])
            for i, post in enumerate(recent_posts, 1):
                likes_count = len(post.get("likes", []))
                replies_count = len(post.get("replies", []))
                repost_count = self._count_reposts(post.get("post_id"))
                created_tick = post.get("created_tick", 0)
                content_preview = self._short_content(post.get("content", ""), 80)
                profile_sections.append(
                    f"{i}. 第{created_tick}步 · {content_preview} · 👍{likes_count} 💬{replies_count} 🔁{repost_count} (ID: {post.get('post_id')})"
                )

        formatted_profile = "\n".join(profile_sections)

        # 监控输出
        print(f"👤 FoV - Agent Profile: Generated profile for {agent_id}")

        return formatted_profile

    # --- 4. 框架集成接口 ---

    # 🔑 v3.0: get_actions() 方法已废弃
    # 所有 Action 现在通过 @action 装饰器自动收集
    # World._get_environment_actions() 会自动从 EnvironmentMeta.capabilities 中获取 action

    def snapshot(self) -> Dict[str, Any]:
        """
        快照接口：将非状态的复杂对象转换为可序列化字典

        职责：
        - 将self.graph转换为可序列化的字典格式
        - 保留所有网络拓扑信息以便恢复
        """
        snapshot_data = {}

        if self.graph is not None:
            # 使用networkx的标准序列化格式
            snapshot_data["graph"] = {
                "directed": self.graph.is_directed(),
                "nodes": list(self.graph.nodes()),
                "edges": list(self.graph.edges()),
                "node_count": self.graph.number_of_nodes(),
                "edge_count": self.graph.number_of_edges(),
                "cv_stats": {
                    "average_cv": self._calculate_average_cv(),
                    "node_cv_values": {
                        node: self._calculate_cv_for_node(self.graph, node)
                        for node in self.graph.nodes()
                    }
                }
            }
        else:
            snapshot_data["graph"] = None

        # 保存配置信息用于验证
        snapshot_data["config_hash"] = hash(str(self._config.model_dump()))
        snapshot_data["environment_type"] = "social_network"

        logger.debug(f"社交网络快照已创建：{len(snapshot_data)} 项数据")
        return snapshot_data

    def restore_from_snapshot(self, data: Dict[str, Any]) -> None:
        """
        恢复接口：从快照数据重建复杂对象

        职责：
        - 从快照数据重建self.graph对象
        - 验证数据完整性
        """
        if not data:
            logger.warning("快照数据为空，跳过恢复")
            return

        # 验证环境类型
        if data.get("environment_type") != "social_network":
            logger.error(f"快照类型不匹配：期望 social_network，实际 {data.get('environment_type')}")
            return

        # 恢复图结构
        graph_data = data.get("graph")
        if graph_data is None:
            self.graph = None
            logger.info("恢复了空的网络图")
        else:
            try:
                # 创建图对象
                if graph_data.get("directed", True):
                    self.graph = nx.DiGraph()
                else:
                    self.graph = nx.Graph()

                # 恢复节点和边
                nodes = graph_data.get("nodes", [])
                edges = graph_data.get("edges", [])

                self.graph.add_nodes_from(nodes)
                self.graph.add_edges_from(edges)

                # 验证恢复的数据
                restored_nodes = self.graph.number_of_nodes()
                restored_edges = self.graph.number_of_edges()
                expected_nodes = graph_data.get("node_count", 0)
                expected_edges = graph_data.get("edge_count", 0)

                if restored_nodes != expected_nodes or restored_edges != expected_edges:
                    logger.warning(f"快照恢复数据不一致：节点 {restored_nodes}/{expected_nodes}, "
                                 f"边 {restored_edges}/{expected_edges}")

                # 验证CV值（可选）
                current_cv = self._calculate_average_cv()
                expected_cv = graph_data.get("cv_stats", {}).get("average_cv", 0)
                if abs(current_cv - expected_cv) > 0.01:
                    logger.warning(f"恢复后CV值偏差较大：{current_cv:.3f} vs {expected_cv:.3f}")

                logger.info(f"成功恢复社交网络图：{restored_nodes} 节点，{restored_edges} 边，"
                           f"平均CV值 {current_cv:.3f}")

            except Exception as e:
                logger.error(f"快照恢复失败：{e}")
                self.graph = None

    # NOTE:
    # 这里曾存在一套旧版推荐系统的占位实现（_collect_candidate_posts/_score_posts），
    # 会覆盖上方用于真实推荐排序的 _score_posts(posts_data, similarity_scores) 实现，
    # 从而导致推荐流永远为空、view_count 永远不增长。
    # 旧占位函数已移除。

    # --- 4. 环境规则 (世界的自主行为) ---

    @rule(description="基于真实数据更新热门话题")
    def update_trending_topics(self, context: ExecutionContext) -> str:
        """更新热门话题的规则（基于真实数据）"""
        # 基于真实帖子计算热门话题，而不是使用模拟数据
        posts = self.state.get("posts", {})
        if not posts:
            context.log_event("update_trending_topics", source="environment", data={"trending_ids": []})
            return "No real posts available for trending calculation"

        # 简单实现：按点赞数排序获取热门帖子
        post_items = list(posts.items())
        sorted_posts = sorted(post_items, key=lambda x: len(x[1].get("likes", [])), reverse=True)
        trending_post_ids = [post_id for post_id, _ in sorted_posts[:3]]  # 取前3个

        self.state["trending_post_ids"] = trending_post_ids
        context.log_event("update_trending_topics", source="environment", data={"trending_ids": trending_post_ids})
        return f"Updated trending topics from real data: {trending_post_ids}"

    @rule(description="模拟随时间降低旧帖子相关性的规则")
    def decay_post_hotness(self, context: ExecutionContext) -> str:
        """一个随时间降低旧帖子相关性的规则（简化版）"""
        context.log_event("decay_post_hotness", source="environment", data={"message": "正在模拟帖子相关性的衰减。"})
        return "Post hotness decay simulated"

    # --- CV值网络生成算法 ---

    def _create_cv_targeted_graph(self, agent_ids: List[str], params: CVTargetedParams) -> nx.DiGraph:
        """
        基于CV值的网络生成核心算法

        算法流程：
        1. 使用基础算法生成初始有向图
        2. 计算当前网络的CV值分布
        3. 迭代调整边连接，直到达到目标CV值分布
        """
        if len(agent_ids) < 2:
            graph = nx.DiGraph()
            graph.add_nodes_from(agent_ids)
            return graph

        # 第一步：生成基础图作为起点
        base_graph = self._create_base_graph_for_cv(agent_ids, params)

        # 第二步：迭代调整CV值
        optimized_graph = self._optimize_cv_distribution(base_graph, params)

        logger.info(f"CV值网络生成完成: 目标CV={params.target_cv_mean:.3f}, "
                   f"实际CV={self._calculate_average_cv_for_graph(optimized_graph):.3f}")

        return optimized_graph

    def _create_base_graph_for_cv(self, agent_ids: List[str], params: CVTargetedParams) -> nx.DiGraph:
        """为CV值优化创建基础图"""
        # 创建基础图配置
        base_config = SocialNetworkConfig(
            distribution=self._get_base_distribution(params),
            is_directed=True  # 强制使用有向图进行CV计算
        )

        return self._create_traditional_graph(agent_ids, base_config)

    def _get_base_distribution(self, params: CVTargetedParams):
        """获取基础图生成算法配置"""
        if params.base_algorithm == NetworkDistributionType.SMALL_WORLD:
            from .models import SmallWorldDistribution, SmallWorldParams
            return SmallWorldDistribution(params=SmallWorldParams(**params.base_params))
        elif params.base_algorithm == NetworkDistributionType.SCALE_FREE:
            from .models import ScaleFreeDistribution, ScaleFreeParams
            return ScaleFreeDistribution(params=ScaleFreeParams(**params.base_params))
        elif params.base_algorithm == NetworkDistributionType.RANDOM:
            from .models import RandomDistribution, RandomParams
            return RandomDistribution(params=RandomParams(**params.base_params))
        else:
            # 默认使用小世界网络
            from .models import SmallWorldDistribution, SmallWorldParams
            return SmallWorldDistribution()

    def _optimize_cv_distribution(self, graph: nx.DiGraph, params: CVTargetedParams) -> nx.DiGraph:
        """
        通过迭代调整优化CV值分布的核心算法

        策略：
        1. 计算当前CV值与目标的差距
        2. 根据差距选择调整操作：
           - CV值过低：将单向边转换为互关边
           - CV值过高：添加单向边或将互关边改为单向边
        """
        nodes = list(graph.nodes())
        target_cv = params.target_cv_mean
        convergence_threshold = params.convergence_threshold

        for iteration in range(params.max_iterations):
            current_cv = self._calculate_average_cv_for_graph(graph)

            # 检查收敛
            if abs(current_cv - target_cv) < convergence_threshold:
                logger.debug(f"CV优化收敛于第{iteration}次迭代，CV值: {current_cv:.3f}")
                break

            # 根据CV差距选择调整策略
            if current_cv < target_cv:
                # CV值过低，需要增加互关比例
                self._increase_mutual_connections(graph, nodes)
            else:
                # CV值过高，需要降低互关比例
                self._decrease_mutual_connections(graph, nodes)

            # 每100次迭代输出进度
            if iteration % 100 == 0:
                logger.debug(f"CV优化进度: 迭代{iteration}, 当前CV={current_cv:.3f}, 目标CV={target_cv:.3f}")

        return graph

    def _increase_mutual_connections(self, graph: nx.DiGraph, nodes: List[str]):
        """增加互关连接数量的策略"""
        # 策略1: 将现有单向边转换为双向边
        single_edges = []
        for u, v in graph.edges():
            if not graph.has_edge(v, u):  # 找到单向边
                single_edges.append((u, v))

        if single_edges:
            # 随机选择一条单向边变为双向边
            u, v = random.choice(single_edges)
            graph.add_edge(v, u)
        else:
            # 策略2: 在没有连接的节点间添加双向边
            unconnected_pairs = []
            for i, u in enumerate(nodes):
                for v in nodes[i+1:]:
                    if not graph.has_edge(u, v) and not graph.has_edge(v, u):
                        unconnected_pairs.append((u, v))

            if unconnected_pairs:
                u, v = random.choice(unconnected_pairs)
                graph.add_edge(u, v)
                graph.add_edge(v, u)

    def _decrease_mutual_connections(self, graph: nx.DiGraph, nodes: List[str]):
        """降低互关连接比例的策略"""
        # 策略1: 将双向边转换为单向边
        mutual_edges = []
        for u, v in graph.edges():
            if graph.has_edge(v, u):  # 找到互关边
                mutual_edges.append((u, v))

        if mutual_edges:
            # 随机选择一条互关边，移除其中一个方向
            u, v = random.choice(mutual_edges)
            if random.random() < 0.5:
                graph.remove_edge(v, u)
            else:
                graph.remove_edge(u, v)
        else:
            # 策略2: 添加单向连接增加总连接度
            unconnected_pairs = []
            for u in nodes:
                for v in nodes:
                    if u != v and not graph.has_edge(u, v):
                        unconnected_pairs.append((u, v))

            if unconnected_pairs:
                u, v = random.choice(unconnected_pairs)
                graph.add_edge(u, v)

    def _calculate_cv_for_node(self, graph: nx.DiGraph, node: str) -> float:
        """计算单个节点的CV值 (CV = M/D，M=互关数，D=总连接度)"""
        if not graph.has_node(node):
            return 0.0

        # 计算出度和入度
        out_edges = set(graph.successors(node))
        in_edges = set(graph.predecessors(node))

        # 计算互关数 (M)
        mutual_connections = len(out_edges.intersection(in_edges))

        # 计算总连接度 (D)
        total_degree = len(out_edges.union(in_edges))

        # CV = M/D (当D>0时)
        return mutual_connections / total_degree if total_degree > 0 else 0.0

    def _calculate_average_cv_for_graph(self, graph: nx.DiGraph) -> float:
        """计算整个网络的平均CV值"""
        if graph.number_of_nodes() == 0:
            return 0.0

        cv_values = [self._calculate_cv_for_node(graph, node) for node in graph.nodes()]
        return sum(cv_values) / len(cv_values)

    def _calculate_average_cv(self) -> float:
        """计算当前网络的平均CV值（用于日志）"""
        if self.graph is None:
            return 0.0
        return self._calculate_average_cv_for_graph(self.graph)

    # --- 测试用的 Rule 函数 ---

    @rule(description="简单的测试规则 - 测试基本功能和默认参数")
    async def test_simple_rule(self, world, message: str = "Hello from rule!") -> dict:
        """简单的测试规则 - 测试基本功能和默认参数"""
        print(f"🔧 [RULE TEST] Simple rule executed with message: {message}")

        # 获取当前状态信息
        posts_count = len(self.state.get("posts", {}))
        agents_count = len(self._world.agents_data)

        result = {
            "rule_type": "simple_test",
            "message": message,
            "posts_count": posts_count,
            "agents_count": agents_count,
            "timestamp": world.step
        }

        print(f"🔧 [RULE TEST] Simple rule result: {result}")
        return result

    @rule(description="复杂的测试规则 - 测试智能参数映射和内容干预功能")
    async def test_intervention_rule(self, world,
                                   target_hashtag: str,
                                   intervention_rate: float = 0.5,
                                   tag_to_apply: str = "flagged") -> dict:
        """复杂的测试规则 - 测试智能参数映射和实际干预"""
        print(f"🔧 [RULE TEST] Intervention rule: hashtag={target_hashtag}, rate={intervention_rate}, tag={tag_to_apply}")

        posts_to_flag = []
        total_posts = 0

        # 遍历所有帖子，查找包含目标hashtag的帖子
        all_posts = self.state.get("posts", {})
        for post_id, post_data in all_posts.items():
            total_posts += 1
            content = post_data.get("content", "")

            if target_hashtag in content:
                # 根据干预率决定是否标记
                import random
                if random.random() < intervention_rate:
                    # 🔧 修复：使用正确的状态访问方式
                    print(f"🔧 [RULE DEBUG] 准备标记帖子 {post_id}")

                    # 确保posts字典存在
                    if "posts" not in self.state:
                        self.state["posts"] = {}

                    # 确保特定帖子存在
                    if post_id not in self.state["posts"]:
                        print(f"🔧 [RULE DEBUG] 帖子 {post_id} 在state中不存在!")
                        continue

                    # 确保special_tags字段存在
                    if "special_tags" not in self.state["posts"][post_id]:
                        self.state["posts"][post_id]["special_tags"] = []

                    # 添加特殊标记到special_tags而不是普通tags
                    if tag_to_apply not in self.state["posts"][post_id]["special_tags"]:
                        self.state["posts"][post_id]["special_tags"].append(tag_to_apply)
                        posts_to_flag.append(post_id)
                        print(f"🔧 [RULE TEST] Tagged post {post_id} with special tag '{tag_to_apply}'")

        result = {
            "rule_type": "intervention",
            "target_hashtag": target_hashtag,
            "intervention_rate": intervention_rate,
            "tag_applied": tag_to_apply,
            "total_posts": total_posts,
            "posts_flagged": len(posts_to_flag),
            "flagged_post_ids": posts_to_flag,
            "timestamp": world.step
        }

        print(f"🔧 [RULE TEST] Intervention result: {result}")
        return result

    @rule(description="测试必需参数验证的规则")
    async def test_required_params_rule(self, world, required_param: str, optional_param: str = "default") -> dict:
        """测试必需参数验证的规则"""
        print(f"🔧 [RULE TEST] Required params rule: required={required_param}, optional={optional_param}")

        result = {
            "rule_type": "required_params_test",
            "required_param": required_param,
            "optional_param": optional_param,
            "validation": "success"
        }

        print(f"🔧 [RULE TEST] Required params result: {result}")
        return result
