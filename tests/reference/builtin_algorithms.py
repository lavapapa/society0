"""从固定基线逐字抽取的测试参考算法；不作为运行时实现。"""
from __future__ import annotations
import logging
import random
import networkx as nx
from typing import Any, Dict, List
from society0.plugins.social_models import (SocialNetworkConfig, NetworkDistributionType, CVTargetedParams, RandomDistribution, RandomParams, ScaleFreeDistribution, ScaleFreeParams, SmallWorldDistribution, SmallWorldParams)
logger = logging.getLogger(__name__)

class SocialNetworkEnv:
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

class RoundRobinConversationEnv:
    def _build_round_robin_schedule(self, group: List[str]) -> List[List[Tuple[str, str]]]:
        if not group:
            return []

        players = list(group)
        count = len(players)

        if count < 2:
            return []
        if count % 2 != 0:
            raise ValueError("Round-robin 小组人数必须为偶数。")

        schedule: List[List[Tuple[str, str]]] = []
        rotation = players[:]

        for _ in range(count - 1):
            round_pairs: List[Tuple[str, str]] = []
            for i in range(count // 2):
                first = rotation[i]
                second = rotation[count - 1 - i]
                if first == second:
                    continue
                round_pairs.append((first, second))
            schedule.append(round_pairs)

            # 旋转（保持首元素不动）
            rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]

        return schedule

