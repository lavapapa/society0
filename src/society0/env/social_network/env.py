"""
SocialNetworkEnv 类,代表一个社交网络环境。

v3.0 更新：
- 使用新的 @capability 装饰器替代 get_actions() 方法
- agent_managed_fields_schema 声明环境提供的 Agent 状态字段
- @action, @fov, @rule 装饰器自动收集元数据
"""
from __future__ import annotations
from typing import List, Dict, Any, TYPE_CHECKING, Optional
from collections.abc import Mapping
import uuid
import math
import time
import random
import logging

import networkx as nx

from ...environment import Environment, EnvironmentTickContext
from ...decorators import env_type, action, fov, rule
from ...core_data import ExecutionContext
from ...agent.core import Agent
from ...logging import AgentEvent, EnvironmentEvent, summarize_text
from .models import (
    SocialNetworkConfig,
    Post,
    Reply,
    NetworkDistributionType,
    CVTargetedParams,
)

if TYPE_CHECKING:
    from ...core_data import World

logger = logging.getLogger(__name__)

_POST_OPEN_VISIBLE_UNTIL = 2**63 - 1


def _mapping_like(value: Any) -> bool:
    return hasattr(value, "get") and hasattr(value, "items")


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

_RECOMMENDATION_TRACE_SCORE_LIMIT = 20

# --- 社交网络环境 ---

SOCIAL_NETWORK_STATE_SCHEMA = {
    "type": "object",
    "title": "SocialNetworkEnvExtraState",
    "description": "社交网络的声明状态；事实、投影与运行时缓存分层保存。",
    "properties": {
        "post_creation_facts": {
            "type": "object",
            "additionalProperties": {"type": "object"},
            "persistence": {"kind": "append_only_map"},
            "description": "不可变帖子创建事实。",
        },
        "post_interaction_facts": {
            "type": "array",
            "items": {"type": "object"},
            "persistence": {"kind": "append_only_list"},
            "description": "点赞、评论、投票等不可变互动事实。",
        },
        "post_projection": {
            "type": "object",
            "additionalProperties": {"type": "object"},
            "persistence": {"kind": "replaceable", "granularity": "entry"},
            "description": "每个帖子当前计数与向量元数据投影。",
        },
        "author_post_facts": {
            "type": "array",
            "items": {"type": "object"},
            "persistence": {"kind": "append_only_list"},
            "description": "作者到帖子关系事实。",
        },
        "post_counter": {
            "type": "integer",
            "persistence": {"kind": "replaceable"},
            "default": 0,
            "description": "帖子 ID 生成计数器。",
        },
        "notification_facts": {
            "type": "array",
            "items": {"type": "object"},
            "persistence": {"kind": "append_only_list"},
            "description": "不可变通知事实。",
        },
        "notification_state": {
            "type": "object",
            "additionalProperties": {"type": "object"},
            "persistence": {"kind": "replaceable", "granularity": "entry"},
            "description": "通知 consumed/read 等当前投影。",
        },
        "notification_counter": {
            "type": "integer",
            "persistence": {"kind": "replaceable"},
            "default": 0,
            "description": "通知 ID 生成计数器。",
        },
        "recommended_posts": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
            "persistence": {"kind": "replaceable", "granularity": "entry"},
            "description": "按 Agent 保存的推荐结果投影。",
        },
        "trending_post_ids": {
            "type": "array",
            "items": {"type": "string"},
            "persistence": {"kind": "transient"},
            "description": "可由帖子事实重算的热门缓存。",
        },
    },
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
                        "repost_score": {"type": "number"},
                        "time_decay_hours": {"type": "number"},
                        "post_count": {"type": "integer"},
                        "candidate_count": {"type": "integer", "minimum": 1},
                        "full_scan_until": {"type": "integer", "minimum": 1},
                        "recent_keep_count": {"type": "integer", "minimum": 1},
                        "top_engagement_keep_count": {"type": "integer", "minimum": 1},
                        "min_lifetime_ticks": {"type": "integer", "minimum": 0},
                        "include_recent_posts_in_query": {"type": "boolean"},
                        "include_following_in_query": {"type": "boolean"},
                        "recent_post_limit": {"type": "integer", "minimum": 0},
                        "interaction_limit": {"type": "integer", "minimum": 0},
                        "recall_multiplier": {"type": "number", "minimum": 1},
                        "follow_bonus": {"type": "number", "minimum": 0},
                        "feed_content_preview_chars": {"type": "integer", "minimum": 40},
                        "feed_max_chars": {"type": "integer", "minimum": 500},
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
            "name": "post_creation_facts",
            "type": "Dict[str, Dict[str, Any]]",
            "description": "不可变帖子创建事实。",
        },
        {
            "name": "post_interaction_facts",
            "type": "List[Dict[str, Any]]",
            "description": "点赞、评论、投票等不可变互动事实。",
        },
        {
            "name": "post_projection",
            "type": "Dict[str, Dict[str, Any]]",
            "description": "帖子当前计数与向量元数据投影。",
        },
        {
            "name": "author_post_facts",
            "type": "List[Dict[str, Any]]",
            "description": "作者到帖子关系事实。",
        },
        {
            "name": "notification_facts",
            "type": "List[Dict[str, Any]]",
            "description": "不可变通知事实。",
        },
        {
            "name": "notification_state",
            "type": "Dict[str, Dict[str, Any]]",
            "description": "通知消费状态投影。",
        },
        {
            "name": "recommended_posts",
            "type": "Dict[str, List[str]]",
            "description": "按 Agent 保存的推荐结果投影。",
        },
        {
            "name": "trending_post_ids",
            "type": "List[str]",
            "description": "运行时热门缓存。",
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
        raw_config = world.environment_data.get("config") or {}

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
        # Runtime-only recommendation caches. They are derived from state and never checkpointed.
        self._recommendation_cache_key: Optional[tuple] = None
        self._recommendation_cache: Dict[str, Any] = {}
        self._recommendation_cache_rebuild_count = 0
        self._semantic_score_cache: Dict[tuple, Dict[str, float]] = {}
        self._pending_impressions: Dict[str, int] = {}
        self._pending_recommended_posts: Dict[str, List[str]] = {}
        self._recommended_posts_by_agent: Dict[str, List[str]] = {}
        self._pending_post_embeddings: Dict[str, Dict[str, Any]] = {}

        logger.debug(f"SocialNetworkEnv配置已验证: {self._config.distribution.type}")

    def set_resource_handles(self, *, embed_call=None, vector_client=None) -> None:
        """接收引擎注入的 embedding 调用与向量存储客户端"""
        self._embed_call = embed_call
        self._vector_client = vector_client
        # collection 缓存在 vector_client 变更时失效
        self._post_collection = None
        self._notifications_cap = None  # 保留以便后续需要全局句柄时使用

    def before_tick(self, ctx: EnvironmentTickContext) -> None:
        """Clear per-tick transient recommendation state."""
        self._semantic_score_cache.clear()
        self._pending_impressions.clear()
        self._pending_recommended_posts.clear()

    # --- v4 canonical state helpers ---

    @staticmethod
    def _plain(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: SocialNetworkEnv._plain(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [SocialNetworkEnv._plain(item) for item in value]
        return value

    def _ensure_social_state(self) -> None:
        """建立 v4 分层状态容器；每个容器都有明确声明。"""
        state = self._state_store()
        defaults = {
            "post_creation_facts": {},
            "post_interaction_facts": [],
            "post_projection": {},
            "author_post_facts": [],
            "post_counter": 0,
            "notification_facts": [],
            "notification_state": {},
            "notification_counter": 0,
            "recommended_posts": {},
            "trending_post_ids": [],
        }
        for key, value in defaults.items():
            if key not in state:
                state[key] = value

    def _state_store(self):
        """返回当前 Tick 的 canonical proxy，或初始化阶段的原始状态。"""
        journal = getattr(self._world, "_state_delta_journal", None)
        active_tick = getattr(journal, "_active_step", None) is not None
        return self.state if active_tick else self._world.environment_data.setdefault("state", {})

    def _posts_view(self) -> Dict[str, Dict[str, Any]]:
        """由不可变帖子/互动事实和有界投影构建只读业务视图。"""
        self._ensure_social_state()
        facts = self.state.get("post_creation_facts", {})
        projections = self.state.get("post_projection", {})
        result: Dict[str, Dict[str, Any]] = {}
        for fallback_id, raw_fact in facts.items():
            fact = self._plain(raw_fact)
            if not isinstance(fact, dict):
                continue
            post_id = str(fact.get("post_id") or fallback_id)
            post = dict(fact)
            post.setdefault("tags", [])
            post.setdefault("special_tags", [])
            post.setdefault("likes", [])
            post.setdefault("like_events", [])
            post.setdefault("replies", [])
            post.setdefault("votes", [])
            post.setdefault("view_count", 0)
            post.setdefault("embedding_ref", None)
            post.setdefault("embedding_model", None)
            post.setdefault("embedding_dimensions", None)
            post.setdefault("embedding_indexed", False)
            projection = projections.get(post_id, {}) if isinstance(projections, Mapping) else {}
            if isinstance(projection, Mapping):
                post.update(self._plain(projection))
            result[post_id] = post

        for raw_event in self.state.get("post_interaction_facts", []) or []:
            event = self._plain(raw_event)
            if not isinstance(event, Mapping):
                continue
            post_id = str(event.get("post_id") or event.get("original_post_id") or "")
            post = result.get(post_id)
            if post is None:
                continue
            kind = event.get("kind")
            if kind == "like":
                agent_id = event.get("agent_id")
                if agent_id not in post["likes"]:
                    post["likes"].append(agent_id)
                post["like_events"].append(
                    {"agent_id": agent_id, "created_tick": event.get("created_tick", 0)}
                )
            elif kind == "comment":
                reply = event.get("reply") or {
                    key: event.get(key)
                    for key in ("reply_id", "author_id", "original_post_id", "content", "created_tick")
                    if key in event
                }
                post["replies"].append(reply)
            elif kind == "vote":
                post["votes"].append(
                    {
                        key: event.get(key)
                        for key in ("vote_id", "voter_id", "original_post_id", "vote", "created_tick")
                        if key in event
                    }
                )
        return result

    def _append_post_creation(self, post: Mapping[str, Any]) -> None:
        self._ensure_social_state()
        post_id = str(post.get("post_id"))
        fact = {
            key: self._plain(post.get(key))
            for key in ("post_id", "author_id", "content", "tags", "created_tick", "reply_to")
            if key in post
        }
        self.state["post_creation_facts"][post_id] = fact
        self.state["post_projection"][post_id] = {
            "view_count": int(post.get("view_count", 0) or 0),
            "embedding_ref": post.get("embedding_ref"),
            "embedding_model": post.get("embedding_model"),
            "embedding_dimensions": post.get("embedding_dimensions"),
            "embedding_indexed": bool(post.get("embedding_indexed", False)),
            "special_tags": list(post.get("special_tags", []) or []),
        }
        self.state["author_post_facts"].append(
            {"author_id": str(post.get("author_id") or ""), "post_id": post_id}
        )

    def _append_post_interaction(self, event: Mapping[str, Any]) -> None:
        self._ensure_social_state()
        self.state["post_interaction_facts"].append(self._plain(dict(event)))

    def _set_post_projection(self, post_id: str, **updates: Any) -> None:
        self._ensure_social_state()
        projections = self.state["post_projection"]
        current = self._plain(projections.get(post_id, {}))
        if not isinstance(current, dict):
            current = {}
        current.update({key: self._plain(value) for key, value in updates.items()})
        projections[post_id] = current

    def _author_post_index(self) -> Dict[str, List[str]]:
        index: Dict[str, List[str]] = {}
        for raw_fact in self.state.get("author_post_facts", []) or []:
            fact = self._plain(raw_fact)
            if not isinstance(fact, Mapping):
                continue
            index.setdefault(str(fact.get("author_id") or ""), []).append(str(fact.get("post_id")))
        return index

    def _notification_view(self) -> Dict[str, Dict[str, Any]]:
        self._ensure_social_state()
        states = self.state.get("notification_state", {})
        result: Dict[str, Dict[str, Any]] = {}
        for raw_fact in self.state.get("notification_facts", []) or []:
            fact = self._plain(raw_fact)
            if not isinstance(fact, Mapping):
                continue
            item = dict(fact)
            item.update(self._plain(states.get(item.get("id"), {})) if isinstance(states, Mapping) else {})
            item.setdefault("consumed", False)
            target = str(item.get("target_agent_id") or "")
            result.setdefault(target, {}).setdefault("notifications", []).append(item)
        return result

    async def after_tick(self, ctx: EnvironmentTickContext) -> None:
        """Flush deferred recommendation side effects after all code steps succeed."""
        await self._flush_pending_post_embeddings()
        if not self._pending_impressions and not self._pending_recommended_posts:
            return
        self._ensure_social_state()
        posts = self._posts_view()
        if not isinstance(posts, Mapping):
            self._pending_impressions.clear()
            self._pending_recommended_posts.clear()
            return
        applied_impressions: Dict[str, int] = {}
        for post_id, delta in list(self._pending_impressions.items()):
            if delta <= 0 or post_id not in posts:
                continue
            post_state = posts[post_id]
            self._set_post_projection(
                post_id,
                view_count=int(post_state.get("view_count", 0) or 0) + delta,
            )
            applied_impressions[post_id] = delta
        recommended_updates = {
            agent_id: list(post_ids)
            for agent_id, post_ids in self._pending_recommended_posts.items()
        }
        if applied_impressions or recommended_updates:
            state_patches: List[Dict[str, Any]] = []
            if recommended_updates:
                recommended_state = self.state.setdefault("recommended_posts", {})
                for agent_id, post_ids in recommended_updates.items():
                    recommended_state[agent_id] = list(post_ids)
                    state_patches.append(
                        {
                            "target_type": "environment",
                            "operation": "set",
                            "path": ["recommended_posts", agent_id],
                            "value": list(post_ids),
                        }
                    )
            for post_id, delta in applied_impressions.items():
                state_patches.append(
                        {
                            "target_type": "environment",
                            "operation": "increment",
                            "path": ["post_projection", post_id, "view_count"],
                            "value": delta,
                        }
                )
            if hasattr(self._world, "_bump_state_version"):
                self._world._bump_state_version()
            event_logger = getattr(self._world, "event_logger", None)
            if event_logger is not None:
                event_logger.log(
                    "social_recommendation_state_flushed",
                    source="environment",
                    data={
                        "step": getattr(self._world, "step", None),
                        "impression_deltas": applied_impressions,
                        "recommended_posts": recommended_updates,
                        "state_patches": state_patches,
                    },
                )
        self._pending_impressions.clear()
        self._pending_recommended_posts.clear()
        self._invalidate_recommendation_cache()

    def _log_recommendation_trace(self, **data: Any) -> None:
        """Record a compact recommendation trace into the main event log."""
        event_logger = getattr(self._world, "event_logger", None)
        if event_logger is None:
            return
        try:
            event_logger.log(
                "social_recommendation_trace",
                source="environment",
                data={
                    "step": getattr(self._world, "step", None),
                    "step_name": getattr(self._world, "_current_code_step_name", None),
                    **data,
                },
            )
        except Exception:
            logger.debug("Failed to write social recommendation trace", exc_info=True)

    def _derive_post_counter(self) -> int:
        """Derive the numeric post counter from existing post ids."""
        posts = self.state.get("post_creation_facts", {})
        max_counter = 0
        if not _mapping_like(posts):
            return max_counter
        for post_id in posts.keys():
            text = str(post_id)
            if not text.startswith("post_"):
                continue
            suffix = text.rsplit("_", maxsplit=1)[-1]
            if suffix.isdigit():
                max_counter = max(max_counter, int(suffix))
        return max_counter

    def _rebuild_author_post_index_if_empty(self) -> None:
        """保留显式事实索引；读取时通过 ``_author_post_index`` 重建。"""
        self._ensure_social_state()

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
            state = self._state_store()
            self._ensure_social_state()
            if not state.get("post_counter"):
                state["post_counter"] = self._derive_post_counter()
            self._ensure_notifications_state()
            self._ensure_recommended_posts_state()

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

    @action(
        description="在社交网络上发布新帖子，支持标签和转发/回复功能。当视野没有帖子时应优先使用本动作。",
        tags=["social", "social_write", "publish"],
    )
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

        # 创建事实、作者索引事实和当前投影分别写入各自的 canonical 容器。
        self._append_post_creation(new_post.model_dump())

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

        logger.debug(
            "Agent %s published post %s (tags=%s, total_posts=%s)",
            agent.id,
            new_post_id,
            tags,
            post_counter + 1,
        )

        self._queue_post_embedding(
            post_id=new_post_id,
            content=content,
            tags=tags,
            created_tick=context.world.step,
            author_id=agent.id,
            step_name=getattr(context.world, "_current_code_step_name", None),
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

    def _post_memory_view(self) -> Dict[str, Any]:
        """读取 World 当前的 Tick/分支视图，供帖子向量读写共用。"""
        world = self._world
        branch_id = str(getattr(world, "_memory_branch_id", "main") or "main")
        lineage = []
        for source_branch, fork_step in getattr(world, "_memory_branch_lineage", []) or []:
            lineage.append((str(source_branch), int(fork_step)))
        target_step = int(getattr(world, "step", 0) or 0)
        active_epoch = getattr(world, "_active_memory_epoch_id", None)
        active_epoch = str(active_epoch) if active_epoch else None
        committed = getattr(world, "_committed_memory_epoch_ids", None)
        committed_epochs = None if committed is None else {str(epoch) for epoch in committed}
        source_branch = getattr(world, "_memory_source_branch_id", None)
        if source_branch is None:
            source_branch = lineage[0][0] if lineage else branch_id
        return {
            "branch_id": branch_id,
            "branch_lineage": lineage,
            "source_branch_id": str(source_branch),
            "target_step": target_step,
            "active_epoch": active_epoch,
            "committed_epochs": committed_epochs,
        }

    def _post_vector_metadata(self, entry: Mapping[str, Any]) -> Dict[str, Any]:
        """生成帖子向量记录的完整 v4 可见性元数据。"""
        view = self._post_memory_view()
        return {
            "author_id": str(entry.get("author_id") or ""),
            "tags": ", ".join(list(entry.get("tags") or [])),
            "created_tick": int(entry.get("created_tick", 0) or 0),
            "env": "social_network",
            "post_id": str(entry.get("post_id") or ""),
            "logical_post_id": str(entry.get("post_id") or ""),
            "created_step": view["target_step"],
            "visible_until_step": _POST_OPEN_VISIBLE_UNTIL,
            "branch_id": view["branch_id"],
            "source_branch_id": view["source_branch_id"],
            # Metadata cannot contain None; use a deterministic legacy marker
            # only for direct non-Tick callers that have no active epoch.
            "write_epoch_id": view["active_epoch"] or f"legacy:{view['branch_id']}",
        }

    def _post_vector_where(self) -> Dict[str, Any]:
        """构造与 Memory 相同的 branch/created/visible Chroma 过滤器。"""
        view = self._post_memory_view()
        target_step = view["target_step"]

        def branch_clause(branch_id: str, cutoff: int) -> Dict[str, Any]:
            return {
                "$and": [
                    {"branch_id": {"$eq": branch_id}},
                    {"created_step": {"$lte": int(cutoff)}},
                    {"visible_until_step": {"$gt": int(cutoff)}},
                ]
            }

        branches = [branch_clause(view["branch_id"], target_step)]
        for source_branch, fork_step in view["branch_lineage"]:
            branches.append(branch_clause(source_branch, min(int(fork_step), target_step)))
        branch_filter = branches[0] if len(branches) == 1 else {"$or": branches}
        return {"$and": [branch_filter]}

    @staticmethod
    def _filter_query_payload(payload: Dict[str, Any], keep: List[int]) -> Dict[str, Any]:
        filtered = dict(payload)
        for key in ("ids", "documents", "metadatas", "distances", "embeddings"):
            values = filtered.get(key)
            if values is None:
                continue
            if isinstance(values, list) and values and isinstance(values[0], list):
                filtered[key] = [[values[0][index] for index in keep]]
            elif isinstance(values, list):
                filtered[key] = [values[index] for index in keep]
        return filtered

    def _filter_post_query_results(self, payload: Any) -> Any:
        """按已提交/当前 epoch 过滤并执行分支 shadow。"""
        if not isinstance(payload, dict):
            return payload
        raw_metadatas = payload.get("metadatas")
        raw_ids = payload.get("ids")
        if not isinstance(raw_metadatas, list) or not raw_metadatas:
            # Some lightweight test/fake collections omit metadata entirely.
            # The server-side where clause still applies; without metadata there
            # is no safe client-side epoch/shadow decision to make.
            return payload
        metadatas = raw_metadatas[0] if isinstance(raw_metadatas[0], list) else raw_metadatas
        ids = raw_ids[0] if isinstance(raw_ids, list) and raw_ids and isinstance(raw_ids[0], list) else raw_ids or []
        view = self._post_memory_view()
        committed = view["committed_epochs"]
        active = view["active_epoch"]
        epoch_filter_enabled = committed is not None
        lineage = view["branch_lineage"]
        branch_ranks = {view["branch_id"]: 0}
        branch_ranks.update({source: index + 1 for index, (source, _step) in enumerate(lineage)})
        chosen: Dict[str, tuple[int, int, int, int]] = {}
        for index, metadata in enumerate(metadatas):
            if not isinstance(metadata, Mapping):
                continue
            if epoch_filter_enabled:
                epoch = str(metadata.get("write_epoch_id", ""))
                if epoch not in (committed or set()) and epoch != active:
                    continue
            branch = str(metadata.get("branch_id", ""))
            if branch not in branch_ranks:
                continue
            logical_id = str(
                metadata.get("logical_post_id")
                or metadata.get("post_id")
                or (ids[index] if index < len(ids) else index)
            )
            candidate = (
                branch_ranks[branch],
                0 if active and str(metadata.get("write_epoch_id", "")) == active else 1,
                -int(metadata.get("created_step", 0) or 0),
                index,
            )
            previous = chosen.get(logical_id)
            if previous is None or candidate < previous:
                chosen[logical_id] = candidate
        keep = sorted(item[3] for item in chosen.values())
        return self._filter_query_payload(payload, keep)

    def _build_post_text(self, content: str, tags: List[str]) -> str:
        """组合内容和标签用于向量化"""
        if not tags:
            return content
        tags_text = " ".join(f"#{t}" for t in tags)
        return f"{content}\nTags: {tags_text}"

    async def _request_embedding(
        self,
        texts: List[str],
        *,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Call the injected embedding function with optional trace metadata."""
        if not self._embed_call:
            return {}
        try:
            return await self._embed_call(texts, metadata=metadata)
        except TypeError as exc:
            # Tests and third-party envs may inject simple callables that do not
            # yet accept trace metadata.
            if "metadata" not in str(exc):
                raise
            return await self._embed_call(texts)

    def _invalidate_recommendation_cache(self) -> None:
        """Invalidate runtime-only recommendation cache."""
        self._recommendation_cache_key = None
        self._recommendation_cache = {}
        self._semantic_score_cache.clear()

    def _recommendation_cache_signature(self) -> tuple:
        """Build a cache key from post-derived recommendation inputs."""
        posts = self._posts_view()
        post_parts = []
        for post_id, post in posts.items():
            if not _mapping_like(post):
                continue
            post_parts.append(
                (
                    post_id,
                    post.get("post_id", post_id),
                    post.get("author_id"),
                    post.get("created_tick", 0),
                    post.get("reply_to"),
                    post.get("content", ""),
                    tuple(post.get("tags", []) or []),
                    len(post.get("likes", []) or []),
                    len(post.get("replies", []) or []),
                    post.get("embedding_ref") or post.get("embedding_indexed") or (post.get("embedding") is not None),
                )
            )
        cfg = self._config.social_media.recommendation
        return (
            getattr(self._world, "step", 0),
            tuple(sorted(post_parts)),
            str(cfg.model_dump()),
        )

    def _get_recommendation_cache(self) -> Dict[str, Any]:
        """Return post features and active pool derived from current state."""
        cache_key = self._recommendation_cache_signature()
        if self._recommendation_cache_key == cache_key and self._recommendation_cache:
            return self._recommendation_cache

        cfg = self._config.social_media.recommendation
        current_tick = int(getattr(self._world, "step", 0))
        state_posts = self._posts_view()
        posts_by_id: Dict[str, Dict[str, Any]] = {}
        for fallback_id, post in state_posts.items():
            if not _mapping_like(post):
                continue
            post_copy = dict(post)
            post_id = str(post_copy.get("post_id") or fallback_id)
            post_copy["post_id"] = post_id
            posts_by_id[post_id] = post_copy

        repost_counts = self._build_repost_counts(posts_by_id)
        decay_constant = cfg.time_decay_hours if cfg.time_decay_hours > 0 else 1.0
        post_features: Dict[str, Dict[str, Any]] = {}
        for post_id, post in posts_by_id.items():
            created_tick = int(post.get("created_tick", 0) or 0)
            age = max(current_tick - created_tick, 0)
            time_score = math.exp(-age / decay_constant)
            likes = len(post.get("likes", []) or [])
            replies = len(post.get("replies", []) or [])
            reposts = repost_counts.get(post_id, 0)
            engagement_score = self._post_engagement_score(post, repost_counts)
            post_features[post_id] = {
                "created_tick": created_tick,
                "likes": likes,
                "replies": replies,
                "reposts": reposts,
                "views": int(post.get("view_count", 0) or 0),
                "time_score": time_score,
                "engagement_score": engagement_score,
                "base_score": (
                    cfg.chronological_weight * time_score
                    + cfg.engagement_weight * engagement_score
                ),
            }

        active_pool_ids = self._build_active_pool_ids(posts_by_id, post_features, current_tick)
        self._recommendation_cache_key = cache_key
        self._recommendation_cache = {
            "posts": posts_by_id,
            "post_features": post_features,
            "active_pool_ids": active_pool_ids,
            "repost_counts": repost_counts,
        }
        self._recommendation_cache_rebuild_count += 1
        return self._recommendation_cache

    def _build_active_pool_ids(
        self,
        posts_by_id: Dict[str, Dict[str, Any]],
        post_features: Dict[str, Dict[str, Any]],
        current_tick: int,
    ) -> List[str]:
        """Build the recommendation active pool without deleting source posts."""
        cfg = self._config.social_media.recommendation
        all_ids = list(posts_by_id.keys())
        if len(all_ids) <= cfg.full_scan_until:
            active_ids = set(all_ids)
        else:
            recent_ids = sorted(
                all_ids,
                key=lambda pid: (post_features[pid]["created_tick"], pid),
                reverse=True,
            )[: cfg.recent_keep_count]
            top_engagement_ids = sorted(
                all_ids,
                key=lambda pid: (
                    post_features[pid]["engagement_score"],
                    post_features[pid]["created_tick"],
                    pid,
                ),
                reverse=True,
            )[: cfg.top_engagement_keep_count]
            young_ids = [
                pid
                for pid in all_ids
                if current_tick - post_features[pid]["created_tick"] < cfg.min_lifetime_ticks
            ]
            active_ids = set(recent_ids) | set(top_engagement_ids) | set(young_ids)

        return sorted(
            active_ids,
            key=lambda pid: (
                post_features[pid]["created_tick"],
                post_features[pid]["engagement_score"],
                pid,
            ),
            reverse=True,
        )

    # --- 通知与摘要工具 ---

    def _ensure_notifications_state(self) -> None:
        """确保通知事实和消费投影容器存在。"""
        self._ensure_social_state()

    def _ensure_recommended_posts_state(self) -> None:
        """Ensure per-agent recommendation trace state exists without resetting proxies."""
        self._ensure_social_state()

    def _push_notification(self, target_agent_id: str, notification_type: str, data: Dict[str, Any], created_tick: int) -> None:
        """向指定用户推送一条通知（不做去重，消费时聚合）"""
        self._ensure_notifications_state()
        counter = int(self.state.get("notification_counter", 0) or 0) + 1
        self.state["notification_counter"] = counter
        notification = {
            "id": f"notif_{counter}",
            "target_agent_id": target_agent_id,
            "type": notification_type,
            "created_tick": created_tick,
            "data": data,
        }
        self.state["notification_facts"].append(notification)
        self.state["notification_state"][notification["id"]] = {"consumed": False}

    @staticmethod
    def _short_content(text: str, limit: int = 80) -> str:
        """生成短内容预览"""
        if not text:
            return ""
        return text if len(text) <= limit else text[:limit] + "..."

    def _count_reposts(self, post_id: str) -> int:
        """统计被转发次数（通过 reply_to 追踪）"""
        posts = self._posts_view()
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
        author_posts = self._author_post_index().get(agent_id, [])
        posts = self._posts_view()
        recent = []
        for pid in author_posts[-limit:]:
            if pid in posts:
                recent.append(posts[pid])
        recent.sort(key=lambda x: x.get("created_tick", 0), reverse=True)
        return recent[:limit]

    def _post_needs_embedding(self, post_id: str) -> bool:
        """Return whether a post still needs vector indexing."""
        try:
            existing = self._posts_view().get(post_id, {})
            if not _mapping_like(existing):
                return False
            return not (
                existing.get("embedding_indexed")
                or existing.get("embedding_ref")
            )
        except Exception:
            return True

    def _queue_post_embedding(
        self,
        *,
        post_id: str,
        content: str,
        tags: List[str],
        created_tick: int,
        author_id: str,
        step_name: Optional[str] = None,
    ) -> None:
        """Defer post embedding so many posts in one tick become one request."""
        if not post_id or not self._post_needs_embedding(post_id):
            return
        self._pending_post_embeddings[post_id] = {
            "post_id": post_id,
            "content": content,
            "tags": list(tags or []),
            "created_tick": created_tick,
            "author_id": author_id,
            "step_name": step_name,
        }

    async def _flush_pending_post_embeddings(self) -> None:
        """Flush queued post embeddings in one batch request."""
        if not self._pending_post_embeddings:
            return
        entries = list(self._pending_post_embeddings.values())
        self._pending_post_embeddings.clear()
        await self._embed_and_store_posts_batch(entries)

    async def _embed_and_store_post(self, post_id: str, content: str, tags: List[str],
                                    created_tick: int, author_id: str) -> None:
        """为帖子生成 embedding 并写入向量库"""
        await self._embed_and_store_posts_batch(
            [
                {
                    "post_id": post_id,
                    "content": content,
                    "tags": list(tags or []),
                    "created_tick": created_tick,
                    "author_id": author_id,
                    "step_name": getattr(self._world, "_current_code_step_name", None),
                }
            ]
        )

    async def _embed_and_store_posts_batch(self, entries: List[Dict[str, Any]]) -> None:
        """Generate embeddings for multiple posts and upsert them together."""
        entries = [
            dict(entry)
            for entry in entries
            if entry.get("post_id") and self._post_needs_embedding(str(entry.get("post_id")))
        ]
        if not entries:
            return
        if not self._embed_call:
            return
        texts = [
            self._build_post_text(str(entry.get("content") or ""), list(entry.get("tags") or []))
            for entry in entries
        ]
        post_ids = [str(entry["post_id"]) for entry in entries]
        author_ids = [str(entry.get("author_id") or "") for entry in entries]
        step_names = [
            str(entry.get("step_name"))
            for entry in entries
            if entry.get("step_name") is not None
        ]
        metadata: Dict[str, Any] = {
            "step": getattr(self._world, "step", None),
            "interaction_type": "env_post_embedding",
            "interaction_name": "publish_post",
        }
        unique_step_names = list(dict.fromkeys(step_names))
        if len(unique_step_names) == 1:
            metadata["step_name"] = unique_step_names[0]
        elif unique_step_names:
            metadata["step_names"] = unique_step_names
        if len(entries) == 1:
            metadata["agent_id"] = author_ids[0]
            metadata["post_id"] = post_ids[0]
        else:
            metadata["agent_ids"] = author_ids
            metadata["post_ids"] = post_ids
        try:
            result = await self._request_embedding(
                texts,
                metadata=metadata,
            )
            embeddings = result.get("result") if result else None
            if not embeddings or len(embeddings) < len(entries):
                return
        except Exception as exc:
            logger.warning(f"Embedding generation failed for posts {post_ids}: {exc}")
            return

        # 写回轻量索引元数据；大向量只放在 Chroma，避免 checkpoint 膨胀。
        for entry, embedding in zip(entries, embeddings):
            post_id = str(entry["post_id"])
            try:
                if post_id in self._posts_view():
                    self._set_post_projection(
                        post_id,
                        embedding_ref=post_id,
                        embedding_model=result.get("model"),
                        embedding_dimensions=result.get("dimensions") or len(embedding),
                        embedding_indexed=True,
                    )
            except Exception as exc:
                logger.warning(f"Failed to store embedding metadata in state for post {post_id}: {exc}")

        # 向量库 upsert
        collection = self._ensure_post_collection()
        if collection is None:
            return
        try:
            collection.upsert(
                ids=post_ids,
                embeddings=embeddings,
                metadatas=[self._post_vector_metadata(entry) for entry in entries],
                documents=texts,
            )
            logger.debug("Upserted %s posts into vector store", len(entries))
        except Exception as exc:
            logger.warning(f"Failed to upsert posts {post_ids} into vector store: {exc}")
        self._invalidate_recommendation_cache()

    async def _rank_posts_with_similarity(self, agent: Agent, posts_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """召回候选帖子并根据多重信号排序"""
        cfg = self._config.social_media.recommendation
        if not posts_data:
            return []

        similarity_scores = await self._semantic_similarity_scores(agent, posts_data)
        ranked = self._score_posts(agent, posts_data, similarity_scores)
        limit = cfg.post_count if cfg.post_count > 0 else len(ranked)
        return ranked[:limit]

    async def _semantic_similarity_scores(
        self,
        agent: Agent,
        posts_data: List[Dict[str, Any]],
    ) -> Dict[str, float]:
        """Compute semantic scores against the active pool, not a latest-post sample."""
        cfg = self._config.social_media.recommendation
        active_post_ids = {post.get("post_id") for post in posts_data if post.get("post_id")}
        if not active_post_ids or not cfg.use_embedding_similarity or not self._embed_call:
            return {}

        preference_text = self._build_agent_preference_text(agent)
        cache_key = (preference_text, self._recommendation_cache_key, tuple(sorted(active_post_ids)))
        if cache_key in self._semantic_score_cache:
            return dict(self._semantic_score_cache[cache_key])

        await self._flush_pending_post_embeddings()
        collection = self._ensure_post_collection()
        if not collection:
            return {}

        try:
            result = await self._request_embedding(
                [preference_text],
                metadata={
                    "step": getattr(self._world, "step", None),
                    "step_name": getattr(self._world, "_current_code_step_name", None),
                    "interaction_type": "semantic_recommendation",
                    "interaction_name": "recommended_feed",
                    "agent_id": agent.id,
                },
            )
            q_embeddings = result.get("result") if result else None
            if not q_embeddings:
                return {}
            q_emb = q_embeddings[0]
            active_size = len(active_post_ids)
            if active_size <= cfg.full_scan_until:
                n_results = max(active_size, cfg.post_count)
            else:
                n_results = max(int(cfg.candidate_count * cfg.recall_multiplier), cfg.post_count)
            query_res = collection.query(
                query_embeddings=[q_emb],
                n_results=n_results,
                include=["distances", "metadatas"],
                where=self._post_vector_where(),
            )
            query_res = self._filter_post_query_results(query_res)
        except Exception as exc:
            logger.warning(f"Similarity query failed for agent {agent.id}: {exc}")
            return {}

        ids_list = query_res.get("ids") or []
        distances = query_res.get("distances") or []
        ordered_ids = ids_list[0] if ids_list else []
        ordered_distances = distances[0] if distances else []
        similarity_scores: Dict[str, float] = {}
        for idx, pid in enumerate(ordered_ids):
            if pid not in active_post_ids:
                continue
            distance = ordered_distances[idx] if idx < len(ordered_distances) else None
            similarity = 1 - distance if distance is not None else 0.0
            similarity_scores[pid] = max(similarity, 0.0)

        self._semantic_score_cache[cache_key] = dict(similarity_scores)
        return similarity_scores

    def _score_posts(
        self,
        agent: Agent,
        posts_data: List[Dict[str, Any]],
        similarity_scores: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """按照多重信号为帖子打分"""
        cfg = self._config.social_media.recommendation
        cache = self._get_recommendation_cache()
        features_by_id = cache.get("post_features", {})

        scored_posts = []
        for post in posts_data:
            pid = post.get("post_id")
            if not pid:
                continue
            features = features_by_id.get(pid)
            if features is None:
                repost_counts = cache.get("repost_counts", {})
                engagement_score = self._post_engagement_score(post, repost_counts)
                created_tick = int(post.get("created_tick", 0) or 0)
                features = {
                    "time_score": 0.0,
                    "engagement_score": engagement_score,
                    "base_score": cfg.engagement_weight * engagement_score,
                    "created_tick": created_tick,
                }
            network_score = 0.0
            if self.graph and post.get("author_id") and self.graph.has_edge(agent.id, post["author_id"]):
                network_score = cfg.follow_bonus
            similarity_score = similarity_scores.get(pid, 0.0)
            time_score = float(features.get("time_score", 0.0) or 0.0)
            engagement_score = float(features.get("engagement_score", 0.0) or 0.0)
            time_contribution = cfg.chronological_weight * time_score
            engagement_contribution = cfg.engagement_weight * engagement_score
            network_contribution = cfg.network_weight * network_score
            semantic_contribution = cfg.similarity_weight * similarity_score

            total_score = (
                time_contribution
                + engagement_contribution
                + network_contribution
                + semantic_contribution
            )
            scored_post = dict(post)
            scored_post["_recommendation_score"] = {
                "time_score": round(float(time_score), 6),
                "time_contribution": round(float(time_contribution), 6),
                "engagement_score": round(float(engagement_score), 6),
                "engagement_contribution": round(float(engagement_contribution), 6),
                "network_score": round(float(network_score), 6),
                "network_contribution": round(float(network_contribution), 6),
                "semantic_score": round(float(similarity_score), 6),
                "semantic_contribution": round(float(semantic_contribution), 6),
                "total_score": round(float(total_score), 6),
            }

            scored_posts.append(
                {
                    "post": scored_post,
                    "score": total_score,
                    "created_tick": post.get("created_tick", 0),
                }
            )

        scored_posts.sort(key=lambda item: (item["score"], item["created_tick"]), reverse=True)
        return [item["post"] for item in scored_posts]

    @staticmethod
    def _recommendation_score_trace(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build compact score diagnostics for returned feed items."""
        traces: List[Dict[str, Any]] = []
        for rank, post in enumerate(posts[:_RECOMMENDATION_TRACE_SCORE_LIMIT], start=1):
            score = post.get("_recommendation_score")
            if not isinstance(score, dict):
                continue
            traces.append(
                {
                    "rank": rank,
                    "post_id": post.get("post_id"),
                    "author_id": post.get("author_id"),
                    **score,
                }
            )
        return traces

    def _collect_recent_posts_for_agent(self, agent: Agent, limit: int) -> List[Dict[str, Any]]:
        """获取Agent最近发布的帖子"""
        if limit <= 0:
            return []
        author_index = self._author_post_index()
        post_ids = author_index.get(agent.id, [])
        posts = self._posts_view()

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
        posts = self._posts_view()
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

        if cfg.include_following_in_query and self.graph and agent.id in self.graph:
            follows = list(self.graph.successors(agent.id))
            if follows:
                limit = cfg.recent_post_limit or 3
                sections.append("Following:\n" + ", ".join(follows[:limit]))

        return "\n\n".join(sections) if sections else "Social feed preference"

    # --- 通知聚合与视野 ---

    def _format_notifications(self, agent: Agent) -> str:
        """将未读通知聚合并格式化输出，消费后标记为已读"""
        self._ensure_notifications_state()
        user_notifs = self._notification_view().get(agent.id, {})
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

        posts = self._posts_view()

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
            self.state["notification_state"][notif["id"]] = {"consumed": True}

        # 如果超过行数上限，补充省略提示
        if len(lines) > max_lines:
            lines = lines[:max_lines - 1] + [f"... 还有 {len(unread)} 条通知已被合并展示"]

        return "\n".join(lines)

    @fov(description="获取与你相关的互动通知（点赞、评论、转发、关注）")
    async def get_notifications(self, agent: Agent, env) -> str:
        """消费未读通知并返回聚合摘要"""
        return self._format_notifications(agent)

    @action(
        description="点赞指定的帖子（post_id 必须来自 FoV 或系统明确提示）",
        tags=["social", "social_write", "engagement"],
    )
    def like_post(self, context: ExecutionContext, post_id: str) -> str:
        """点赞帖子Action（适配新架构）"""
        agent: Agent = context.caller
        log_ctx = context.log_context or context.world.get_log_context()

        posts = self._posts_view()
        post = posts.get(post_id)

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

        # 确保特定帖子仍存在于 canonical 创建事实中。
        if post_id not in self.state.get("post_creation_facts", {}):
            logger.debug("Post %s disappeared before like mutation", post_id)
            return f"Post {post_id} not found in state"

        self._append_post_interaction(
            {
                "kind": "like",
                "post_id": post_id,
                "agent_id": agent.id,
                "created_tick": context.world.step,
            }
        )

        current_likes = len(posts[post_id].get("likes", [])) + 1
        logger.debug("Agent %s liked post %s (total_likes=%s)", agent.id, post_id, current_likes)

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

    @action(
        description="评论指定的帖子",
        tags=["social", "social_write", "engagement"],
    )
    async def comment(self, context: ExecutionContext, post_id: str, content: str) -> str:
        """发表评论的Action"""
        is_valid, msg = self._validate_content_length(content, "comment", context.caller.id)
        if not is_valid:
            return msg

        agent: Agent = context.caller
        log_ctx = context.log_context or context.world.get_log_context()
        posts = self._posts_view()
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
        self._append_post_interaction(
            {
                "kind": "comment",
                "post_id": post_id,
                "reply": reply.model_dump(),
            }
        )

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

    @action(
        description="转发指定的帖子并添加评论",
        tags=["social", "social_write", "engagement", "publish"],
    )
    async def repost(self, context: ExecutionContext, post_id: str, commentary: str = "") -> str:
        """转发帖子Action"""
        posts = self._posts_view()
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

    @action(
        description="关注另一个Agent，建立社交连接",
        tags=["social", "social_write", "follow"],
    )
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

    @action(
        description="取消关注指定的Agent",
        tags=["social", "social_write", "follow"],
    )
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

    @fov(name="recommended_feed", description="获取个性化推荐动态，包含推荐帖子和可关注用户信息")
    async def recommended_feed(self, agent: Agent, env) -> str:
        """
        获取推荐动态并格式化为人类可读的字符串，同时记录曝光。

        Returns:
            str: 格式化的推荐动态字符串，便于LLM理解
        """
        return await self._render_recommended_feed(
            agent,
            env,
            record_impression=True,
            record_recommended_state=True,
            title="个性化推荐动态",
        )

    async def get_recommended_feed(self, agent: Agent, env) -> str:
        """Backward-compatible Python alias for the `recommended_feed` FoV."""
        return await self.recommended_feed(agent, env)

    @fov(name="recommended_feed_preview", description="预览个性化推荐动态，不记录曝光或更新推荐状态，适合访谈测量")
    async def preview_recommended_feed(self, agent: Agent, env) -> str:
        """返回不产生曝光副作用的推荐动态预览。"""
        return await self._render_recommended_feed(
            agent,
            env,
            record_impression=False,
            record_recommended_state=False,
            title="个性化推荐动态预览",
        )

    async def _render_recommended_feed(
        self,
        agent: Agent,
        env,
        *,
        record_impression: bool,
        record_recommended_state: bool,
        title: str,
    ) -> str:
        """Render a recommended feed, optionally committing exposure side effects."""
        started = time.perf_counter()
        rebuild_count_before = self._recommendation_cache_rebuild_count
        # 获取推荐帖子数据
        posts_data = self._get_real_posts_only(agent)
        raw_candidate_count = len(posts_data)

        logger.debug(
            "Rendering recommended feed for agent %s with %s raw candidate posts",
            agent.id,
            len(posts_data),
        )

        # 可选：基于向量的相似度重排
        rank_started = time.perf_counter()
        posts_data = await self._rank_posts_with_similarity(agent, posts_data)
        rank_duration = time.perf_counter() - rank_started

        if not posts_data:
            # 如果没有帖子，至少显示网络中的其他Agent供关注
            other_agents = self._get_other_agents_in_network(agent)
            if other_agents:
                # 只显示前5个用户，如果超过5个则添加省略号
                display_agents = other_agents[:5]
                agents_list = ', '.join(display_agents)
                if len(other_agents) > 5:
                    agents_list += '...'

                feed_text = "📭 暂无推荐内容,你可以等待，或者发表你的帖子 "
            else:
                feed_text = "📭 暂无推荐内容"
            self._log_recommendation_trace(
                agent_id=agent.id,
                raw_candidate_count=raw_candidate_count,
                returned_count=0,
                active_pool_count=0,
                record_impression=record_impression,
                record_recommended_state=record_recommended_state,
                rank_duration_sec=rank_duration,
                duration_sec=time.perf_counter() - started,
                output_characters=len(feed_text),
                cache_rebuilds_delta=self._recommendation_cache_rebuild_count - rebuild_count_before,
                score_breakdown=[],
            )
            return feed_text

        # 格式化为人类可读的字符串
        feed_sections = []
        recommendation_cfg = self._config.social_media.recommendation
        current_tick = int(getattr(self._world, "step", 0))
        feed_sections.append(f"{title} | tick={current_tick}")

        for i, post in enumerate(posts_data, 1):
            post_id = post.get('post_id', 'N/A')

            author = post.get("author_id", "Unknown")
            created_tick = post.get("created_tick", 0)
            content = post.get("content", "")
            content = self._short_content(content, recommendation_cfg.feed_content_preview_chars)
            special_tags = post.get("special_tags", [])
            if special_tags:
                content += f"\n[系统标记: {', '.join(special_tags)}]"

            tags = post.get("tags", [])
            tags_text = ", ".join(tags) if tags else "无"

            likes_count = len(post.get("likes", []))
            replies_count = len(post.get("replies", []))
            view_count = post.get("view_count", 0)
            repost_count = self._count_reposts(post_id)
            reply_to = post.get("reply_to")

            time_diff = current_tick - created_tick
            time_text = f"第{created_tick}步 (本步发布)" if time_diff == 0 else f"第{created_tick}步 ({time_diff}步前)"

            block_lines = [
                f"{i}. 帖子 ID: {post_id} | 作者用户 ID: {author} | 发布: {time_text}",
                f"内容: {content}",
                f"标签: {tags_text} | 互动: 👍{likes_count} 💬{replies_count} 🔁{repost_count} 👁️{view_count}",
            ]
            if reply_to:
                block_lines[-1] += f" | 引用/回复: {reply_to}"

            feed_sections.append("\n".join(block_lines))
            if record_impression:
                self._record_impression(post_id)

        feed_sections.append("提示: 互动请使用帖子 ID；不要把作者用户 ID 当作 post_id。")

        # 记录本次推荐的帖子ID（按 agent 隔离，不重置代理状态）
        recommended_ids = [p.get("post_id") for p in posts_data if p.get("post_id")]
        if record_recommended_state:
            self._recommended_posts_by_agent[agent.id] = list(recommended_ids)
            self._pending_recommended_posts[agent.id] = list(recommended_ids)

        formatted_feed = "\n".join(feed_sections)
        if len(formatted_feed) > recommendation_cfg.feed_max_chars:
            formatted_feed = (
                formatted_feed[: recommendation_cfg.feed_max_chars].rstrip()
                + "\n... 推荐流已按 feed_max_chars 截断，请调高配置以展示更多内容。"
            )

        logger.debug(
            "Recommended feed rendered for agent %s (posts=%s, chars=%s)",
            agent.id,
            len(posts_data),
            len(formatted_feed),
        )
        cache = self._get_recommendation_cache()
        self._log_recommendation_trace(
            agent_id=agent.id,
            raw_candidate_count=raw_candidate_count,
            returned_count=len(posts_data),
            active_pool_count=len(cache.get("active_pool_ids", []) or []),
            record_impression=record_impression,
            record_recommended_state=record_recommended_state,
            rank_duration_sec=rank_duration,
            duration_sec=time.perf_counter() - started,
            output_characters=len(formatted_feed),
            cache_rebuilds_delta=self._recommendation_cache_rebuild_count - rebuild_count_before,
            score_breakdown=self._recommendation_score_trace(posts_data),
        )

        return formatted_feed

    @fov(description="获取热门帖子的视野（可配置启用）", cache_on_step=True)
    async def get_trending_feed(self, agent: Agent, env) -> str:
        """返回热门帖子视图（仅作为 FoV，不作为 agent 主动 action）"""
        trending_cfg = self._config.social_media.trending
        if not trending_cfg.enabled:
            return "🔥 热门视图已关闭"

        posts_data = self._get_top_engagement_posts(None, limit=2)
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

    def _record_impression(self, post_id: str) -> None:
        """Record a view for after_tick batch flush."""
        if post_id and post_id in self._posts_view():
            self._pending_impressions[post_id] = self._pending_impressions.get(post_id, 0) + 1

    def _increment_view_count(self, post_id: str) -> None:
        """Backward-compatible private helper; now defers to after_tick."""
        self._record_impression(post_id)

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
        获取 active pool 中的真实帖子候选（不生成任何示例数据）。

        默认推荐池不再受 candidate_count 限制。帖子数不超过
        full_scan_until 时全量进入 active pool；超过阈值后才按近期、
        高互动和最小生命周期规则剪枝。

        Args:
            agent: 当前Agent，用于过滤自己的帖子，为None时不过滤

        Returns:
            List[Dict]: 真实帖子数据列表，如果没有则返回空列表
        """
        cache = self._get_recommendation_cache()
        posts_by_id = cache.get("posts", {})
        active_pool_ids = cache.get("active_pool_ids", [])
        filtered_posts: List[Dict[str, Any]] = []
        for post_id in active_pool_ids:
            post_data = posts_by_id.get(post_id)
            if not post_data:
                continue
            author_id = post_data.get("author_id", "")
            if agent and author_id == agent.id:
                continue
            filtered_posts.append(dict(post_data))
        return filtered_posts

    @staticmethod
    def _dedupe_posts(posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """按 post_id 去重，同时保留召回通道顺序。"""
        seen = set()
        deduped: List[Dict[str, Any]] = []
        for post in posts:
            pid = post.get("post_id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            deduped.append(post)
        return deduped

    @staticmethod
    def _build_repost_counts(posts: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
        """一次性统计每个帖子被转发的次数，避免推荐阶段反复全量扫描。"""
        counts: Dict[str, int] = {}
        for post in posts.values():
            parent_id = post.get("reply_to")
            if parent_id:
                counts[parent_id] = counts.get(parent_id, 0) + 1
        return counts

    def _post_engagement_score(
        self,
        post: Dict[str, Any],
        repost_counts: Optional[Dict[str, int]] = None,
    ) -> float:
        """计算帖子互动分，供候选召回、推荐排序和热门计算复用。"""
        cfg = self._config.social_media.recommendation
        post_id = post.get("post_id")
        if not post_id:
            repost_count = 0
        elif repost_counts is not None:
            repost_count = repost_counts.get(post_id, 0)
        else:
            repost_count = self._count_reposts(post_id)
        return (
            cfg.like_score * len(post.get("likes", []))
            + cfg.reply_score * len(post.get("replies", []))
            + cfg.repost_score * repost_count
        )

    def _get_top_engagement_posts(self, agent: Agent | None = None, limit: int = 2) -> List[Dict[str, Any]]:
        """获取互动分最高的真实帖子。"""
        if limit <= 0:
            return []
        cache = self._get_recommendation_cache()
        posts_by_id = cache.get("posts", {})
        features_by_id = cache.get("post_features", {})
        posts = []
        for post_id, post_data in posts_by_id.items():
            author_id = post_data.get("author_id", "")
            if agent and author_id == agent.id:
                continue
            posts.append(dict(post_data))
        ranked = sorted(
            posts,
            key=lambda x: (
                features_by_id.get(x.get("post_id"), {}).get("engagement_score", 0.0),
                x.get("created_tick", 0),
                x.get("post_id", ""),
            ),
            reverse=True,
        )
        return ranked[:limit]

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

    @action(
        description="获取当前网络上热门帖子的动态信息，并记录这些热门帖子的一次曝光",
        tags=["social_read", "lookup", "trending"],
    )
    async def get_trending_posts(self) -> str:
        """
        返回当前网络上热门帖子的格式化字符串

        Returns:
            str: 格式化的热门帖子字符串
        """
        # 获取真实的热门帖子
        posts_data = self._get_top_engagement_posts(None, limit=2)

        if not posts_data:
            return "🔥 暂无热门内容"

        # 格式化热门帖子
        recommendation_cfg = self._config.social_media.recommendation
        trending_sections = []
        trending_sections.append("热门动态（本动作会记录曝光）")

        # 只显示前2条作为热门
        for i, post in enumerate(posts_data[:2], 1):
            # 🔧 增加view_count统计 - 查看热门帖子时也增加计数
            post_id = post.get('post_id', 'N/A')
            self._increment_view_count(post_id)

            likes_count = len(post.get("likes", []))
            replies_count = len(post.get("replies", []))
            view_count = post.get("view_count", 0)

            # 🔧 处理special_tags - 如果有干预标签，追加到内容后面
            content = self._short_content(
                post.get('content', ''),
                min(recommendation_cfg.feed_content_preview_chars, 120),
            )
            special_tags = post.get("special_tags", [])
            if special_tags:
                content += f" [系统标记: {', '.join(special_tags)}]"

            post_info = [
                f"{i}. 帖子 ID: {post_id} | 作者用户 ID: {post.get('author_id', 'Unknown')}",
                f"内容: {content}",
                f"互动: 👍{likes_count} 💬{replies_count} 👁️{view_count}",
            ]

            trending_sections.append("\n".join(post_info))

        trending_sections.append("提示: 评论、点赞或转发时使用帖子 ID；不要使用作者用户 ID。")

        formatted_trending = "\n".join(trending_sections)

        logger.debug("Generated trending posts view")

        return formatted_trending

    @action(
        description="查看指定帖子的详细信息（内容、点赞、评论、转发）",
        tags=["social_read", "lookup", "post_detail"],
    )
    async def get_post_details(self, agent, post_id: str) -> str:
        """返回帖子的完整详情（无权限限制，聚合显示）"""
        posts = self._posts_view()
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

    @action(
        description="获取指定Agent的个人资料和社交统计信息",
        tags=["social_read", "lookup", "profile"],
    )
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
        author_posts = self._author_post_index().get(agent_id, [])
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

        logger.debug("Generated agent profile for %s", agent_id)

        return formatted_profile

    # --- 4. 框架集成接口 ---

    # 🔑 v3.0: get_actions() 方法已废弃
    # 所有 Action 现在通过 @action 装饰器自动收集
    # World._get_environment_actions() 会自动从 EnvironmentMeta.capabilities 中获取 action

    def snapshot(self, *, include_state: bool = True) -> Dict[str, Any]:
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
        posts = self._posts_view()
        if not posts:
            context.log_event("update_trending_topics", source="environment", data={"trending_ids": []})
            return "No real posts available for trending calculation"

        # 简单实现：按统一互动分排序获取热门帖子
        repost_counts = self._build_repost_counts(posts)
        post_items = list(posts.items())
        sorted_posts = sorted(
            post_items,
            key=lambda x: (
                self._post_engagement_score(x[1], repost_counts),
                x[1].get("created_tick", 0),
                x[0],
            ),
            reverse=True,
        )
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
        logger.debug("Simple rule executed with message: %s", message)

        # 获取当前状态信息
        posts_count = len(self._posts_view())
        agents_count = len(self._world.agents_data)

        result = {
            "rule_type": "simple_test",
            "message": message,
            "posts_count": posts_count,
            "agents_count": agents_count,
            "timestamp": world.step
        }

        logger.debug("Simple rule result: %s", result)
        return result

    @rule(description="复杂的测试规则 - 测试智能参数映射和内容干预功能")
    async def test_intervention_rule(self, world,
                                   target_hashtag: str,
                                   intervention_rate: float = 0.5,
                                   tag_to_apply: str = "flagged") -> dict:
        """复杂的测试规则 - 测试智能参数映射和实际干预"""
        logger.debug(
            "Intervention rule: hashtag=%s, rate=%s, tag=%s",
            target_hashtag,
            intervention_rate,
            tag_to_apply,
        )

        posts_to_flag = []
        total_posts = 0

        # 遍历所有帖子，查找包含目标hashtag的帖子
        all_posts = self._posts_view()
        for post_id, post_data in all_posts.items():
            total_posts += 1
            content = post_data.get("content", "")

            if target_hashtag in content:
                # 根据干预率决定是否标记
                import random
                if random.random() < intervention_rate:
                    # 确保特定帖子存在于 canonical 创建事实中。
                    if post_id not in self.state.get("post_creation_facts", {}):
                        logger.debug("Post %s disappeared before intervention mutation", post_id)
                        continue

                    # 特殊标记属于有界当前投影，逐帖子替换。
                    current_tags = list(all_posts[post_id].get("special_tags", []) or [])
                    if tag_to_apply not in current_tags:
                        current_tags.append(tag_to_apply)
                        self._set_post_projection(post_id, special_tags=current_tags)
                        posts_to_flag.append(post_id)
                        logger.debug("Tagged post %s with special tag %s", post_id, tag_to_apply)

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

        logger.debug("Intervention result: %s", result)
        return result

    @rule(description="测试必需参数验证的规则")
    async def test_required_params_rule(self, world, required_param: str, optional_param: str = "default") -> dict:
        """测试必需参数验证的规则"""
        logger.debug(
            "Required params rule: required=%s, optional=%s",
            required_param,
            optional_param,
        )

        result = {
            "rule_type": "required_params_test",
            "required_param": required_param,
            "optional_param": optional_param,
            "validation": "success"
        }

        logger.debug("Required params result: %s", result)
        return result
