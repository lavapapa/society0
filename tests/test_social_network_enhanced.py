"""
SocialNetworkEnv增强版测试套件

测试覆盖：
1. CV值网络生成算法
2. 配置系统和初始化
3. Action技能系统
4. 增强推荐算法
5. 图序列化接口
6. 端到端集成测试
"""

import pytest
import asyncio
import networkx as nx
from typing import List, Dict, Any
from unittest.mock import MagicMock, patch

# 导入测试目标
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from simengine.env.social_network.env import SocialNetworkEnv
from simengine.env.social_network.models import (
    SocialNetworkConfig, CVTargetedDistribution, CVTargetedParams,
    SmallWorldDistribution, SmallWorldParams, Post
)
from simengine.environment import Environment
from simengine.core_data import Agent, ExecutionContext


class TestCVNetworkGeneration:
    """CV值网络生成算法测试"""

    def test_cv_calculation_for_node(self):
        """测试单个节点的CV值计算"""
        # 创建测试用的有向图
        graph = nx.DiGraph()
        graph.add_nodes_from(['A', 'B', 'C', 'D'])

        # A关注B和C，B关注A，C单向关注A
        graph.add_edges_from([('A', 'B'), ('B', 'A'), ('A', 'C'), ('C', 'A')])

        # 创建环境实例
        world = MagicMock()
        world.environment_data = {'state': {'config': {}}}
        env = SocialNetworkEnv(world)

        # 测试A的CV值：M=2（互关B和C），D=2，CV=1.0
        cv_a = env._calculate_cv_for_node(graph, 'A')
        assert cv_a == 1.0, f"A的CV值应为1.0，实际为{cv_a}"

        # 测试B的CV值：M=1（互关A），D=1，CV=1.0
        cv_b = env._calculate_cv_for_node(graph, 'B')
        assert cv_b == 1.0, f"B的CV值应为1.0，实际为{cv_b}"

        # 测试D的CV值：M=0，D=0，CV=0.0
        cv_d = env._calculate_cv_for_node(graph, 'D')
        assert cv_d == 0.0, f"D的CV值应为0.0，实际为{cv_d}"

    def test_cv_targeted_network_generation(self):
        """测试CV值目标网络生成"""
        # 配置CV目标网络
        config = SocialNetworkConfig(
            distribution=CVTargetedDistribution(
                params=CVTargetedParams(
                    target_cv_mean=0.3,
                    target_cv_std=0.05,
                    max_iterations=100,
                    convergence_threshold=0.05
                )
            ),
            is_directed=True,
            social_media={'enabled': True}
        )

        world = MagicMock()
        world.environment_data = {'state': {'config': config}}
        env = SocialNetworkEnv(world)

        # 生成网络
        agent_ids = ['alice', 'bob', 'charlie', 'diana', 'eve']
        graph = env._create_cv_targeted_graph(agent_ids, config.distribution.params)

        # 验证网络基本属性
        assert graph.number_of_nodes() == 5
        assert graph.number_of_edges() > 0
        assert isinstance(graph, nx.DiGraph)

        # 验证CV值接近目标
        actual_cv = env._calculate_average_cv_for_graph(graph)
        target_cv = config.distribution.params.target_cv_mean
        assert abs(actual_cv - target_cv) <= 0.1, f"CV偏差过大：{actual_cv} vs {target_cv}"

    def test_network_generation_convergence(self):
        """测试网络生成的收敛性"""
        config_strong = SocialNetworkConfig(
            distribution=CVTargetedDistribution(
                params=CVTargetedParams(target_cv_mean=0.8, max_iterations=50)
            ),
            is_directed=True
        )

        world = MagicMock()
        world.environment_data = {'state': {'config': config_strong}}
        env = SocialNetworkEnv(world)

        agent_ids = ['a', 'b', 'c', 'd']
        graph = env._create_cv_targeted_graph(agent_ids, config_strong.distribution.params)

        # 强连接网络应该有较高的CV值
        cv = env._calculate_average_cv_for_graph(graph)
        assert cv > 0.5, f"强连接网络CV值过低：{cv}"


class TestConfigurationAndInitialization:
    """配置系统和初始化测试"""

    def test_config_validation_and_separation(self):
        """测试配置验证和分离"""
        # 测试有效配置
        valid_config = {
            'distribution': {'type': 'small_world', 'params': {'k_neighbors': 4}},
            'is_directed': True,
            'social_media': {'enabled': True}
        }

        world = MagicMock()
        world.environment_data = {'state': {'config': valid_config}}

        env = SocialNetworkEnv(world)

        # 验证配置被正确解析和存储
        assert env._config is not None
        assert env._config.distribution.type == 'small_world'
        assert env._config.is_directed == True
        assert env.graph is None  # 初始化时graph为None

    def test_initialization_creates_graph_and_state(self):
        """测试initialize方法创建图和状态"""
        config = SocialNetworkConfig(
            distribution=SmallWorldDistribution(
                params=SmallWorldParams(k_neighbors=2)
            ),
            is_directed=True,
            social_media={'enabled': True}
        )

        world = MagicMock()
        world.environment_data = {'state': {'config': config}}

        env = SocialNetworkEnv(world)

        # 模拟代理列表
        agents = [MagicMock(id='alice'), MagicMock(id='bob'), MagicMock(id='charlie')]
        world_state = MagicMock(step=0)

        # 模拟state属性
        env.state = {}

        # 调用initialize
        env.initialize(agents, world_state)

        # 验证图被创建
        assert env.graph is not None
        assert env.graph.number_of_nodes() == 3

        # 验证社交媒体状态被初始化
        assert 'posts' in env.state
        assert 'author_to_post_ids' in env.state
        assert 'post_counter' in env.state


class TestActionSystem:
    """Action技能系统测试"""

    def setup_method(self):
        """设置测试环境"""
        config = SocialNetworkConfig(
            distribution=SmallWorldDistribution(),
            is_directed=True,
            social_media={'enabled': True}
        )

        self.world = MagicMock()
        self.world.environment_data = {'state': {'config': config}}
        self.world.step = 10

        self.env = SocialNetworkEnv(self.world)

        # 初始化图和状态
        self.env.graph = nx.DiGraph()
        self.env.graph.add_nodes_from(['alice', 'bob'])

        self.env.state = {
            'posts': {},
            'author_to_post_ids': {},
            'post_counter': 0,
            'reports': []
        }

    def test_publish_post_action(self):
        """测试发布帖子Action"""
        # 创建测试agent和context
        agent = MagicMock()
        agent.id = 'alice'

        context = MagicMock()
        context.caller = agent
        context.world = self.world
        context.log_event = MagicMock()

        # 调用publish_post
        patches = self.env.publish_post(context, "Hello world!", ["greeting", "test"])

        # 验证返回的StatePatch
        assert len(patches) == 3
        assert any("posts.post_1" in patch.path for patch in patches)
        assert any("author_to_post_ids.alice" in patch.path for patch in patches)
        assert any("post_counter" in patch.path for patch in patches)

        # 验证日志事件被记录
        context.log_event.assert_called_once()

    def test_follow_action_direct_graph_modification(self):
        """测试关注Action直接修改图结构"""
        agent = MagicMock()
        agent.id = 'alice'

        context = MagicMock()
        context.caller = agent
        context.world = self.world
        context.world.agents_data = {'bob': MagicMock()}
        context.log_event = MagicMock()

        # 验证初始状态
        assert not self.env.graph.has_edge('alice', 'bob')

        # 调用follow
        patches = self.env.follow(context, 'bob')

        # 验证图被直接修改
        assert self.env.graph.has_edge('alice', 'bob')

        # 验证不返回StatePatch（因为直接修改了graph）
        assert len(patches) == 0

        # 验证事件被记录
        context.log_event.assert_called_once()

    def test_like_post_action(self):
        """测试点赞Action"""
        # 先添加一个帖子到状态
        self.env.state['posts']['post_1'] = {
            'post_id': 'post_1',
            'author_id': 'bob',
            'content': 'Test post',
            'likes': []
        }

        agent = MagicMock()
        agent.id = 'alice'

        context = MagicMock()
        context.caller = agent
        context.log_event = MagicMock()

        # 调用like_post
        patches = self.env.like_post(context, 'post_1')

        # 验证返回正确的StatePatch
        assert len(patches) == 1
        assert "posts.post_1.likes" in patches[0].path

        # 验证日志
        context.log_event.assert_called_once()

    def test_report_post_action(self):
        """测试举报帖子Action"""
        # 添加测试帖子
        self.env.state['posts']['post_1'] = {
            'post_id': 'post_1',
            'author_id': 'bob',
            'content': 'Inappropriate content'
        }

        agent = MagicMock()
        agent.id = 'alice'

        context = MagicMock()
        context.caller = agent
        context.world = self.world
        context.log_event = MagicMock()

        # 调用report_post
        patches = self.env.report_post(context, 'post_1', 'spam')

        # 验证返回StatePatch更新reports
        assert len(patches) == 1
        assert "reports" in patches[0].path

        # 验证事件记录
        context.log_event.assert_called_once()


class TestEnhancedRecommendation:
    """增强推荐算法测试"""

    def setup_method(self):
        """设置测试环境"""
        config = SocialNetworkConfig(
            social_media={'enabled': True, 'recommendation': {'post_count': 5}}
        )

        world = MagicMock()
        world.environment_data = {'state': {'config': config}}

        self.env = SocialNetworkEnv(world)

        # 创建测试图结构
        self.env.graph = nx.DiGraph()
        nodes = ['alice', 'bob', 'charlie', 'diana']
        self.env.graph.add_nodes_from(nodes)
        self.env.graph.add_edges_from([
            ('alice', 'bob'),    # alice关注bob
            ('bob', 'charlie'),  # bob关注charlie
            ('alice', 'diana')   # alice关注diana
        ])

        # 创建测试帖子
        self.env.state = {
            'posts': {
                'post_1': {'post_id': 'post_1', 'author_id': 'bob', 'content': 'Hello',
                          'tags': ['greeting'], 'created_tick': 5, 'likes': ['alice'], 'replies': []},
                'post_2': {'post_id': 'post_2', 'author_id': 'charlie', 'content': 'Tech news',
                          'tags': ['tech'], 'created_tick': 8, 'likes': [], 'replies': []},
                'post_3': {'post_id': 'post_3', 'author_id': 'diana', 'content': 'Art piece',
                          'tags': ['art'], 'created_tick': 9, 'likes': ['bob'], 'replies': []}
            },
            'author_to_post_ids': {
                'bob': ['post_1'],
                'charlie': ['post_2'],
                'diana': ['post_3']
            }
        }

    def test_multi_layer_candidate_collection(self):
        """测试多层社交网络候选收集"""
        world_state = MagicMock()
        world_state.step = 10

        candidates = self.env._collect_enhanced_candidates('alice', world_state)

        # 应该包含直接关注者的帖子
        direct_sources = [c for c in candidates if c['source'] == 'direct_follow']
        assert len(direct_sources) >= 2  # bob和diana的帖子

        # 应该包含二级连接的帖子
        second_degree = [c for c in candidates if c['source'] == 'second_degree']
        assert len(second_degree) >= 1  # charlie的帖子（通过bob）

    def test_second_degree_connections(self):
        """测试二级社交连接发现"""
        second_degree = self.env._get_second_degree_connections('alice')

        # alice -> bob -> charlie，所以charlie是alice的二级连接
        assert 'charlie' in second_degree
        assert 'bob' not in second_degree  # bob是直接连接
        assert 'alice' not in second_degree  # 不包含自己

    def test_enhanced_scoring_algorithm(self):
        """测试增强评分算法"""
        agent = MagicMock()
        agent.id = 'alice'
        agent.state = {'interests': ['greeting', 'tech']}

        world_state = MagicMock()
        world_state.step = 10

        candidate_posts = [
            {'post': self.env.state['posts']['post_1'], 'source': 'direct_follow',
             'social_distance': 1, 'author_id': 'bob'},
            {'post': self.env.state['posts']['post_2'], 'source': 'second_degree',
             'social_distance': 2, 'author_id': 'charlie'}
        ]

        scored_posts = self.env._score_posts_enhanced(agent, candidate_posts, world_state)

        # 验证评分结果
        assert len(scored_posts) == 2

        # 直接关注的帖子应该得分更高（因为社交距离更近）
        direct_score = next(p['score'] for p in scored_posts if p['source'] == 'direct_follow')
        second_score = next(p['score'] for p in scored_posts if p['source'] == 'second_degree')

        # 直接关注的帖子分数应该更高
        assert direct_score > second_score

    def test_diversity_filter(self):
        """测试多样性过滤"""
        agent = MagicMock()
        agent.id = 'alice'

        # 创建测试评分帖子（模拟同一作者的多个帖子）
        scored_posts = [
            {'post': {'author_id': 'bob', 'post_id': 'post_1'}, 'score': 0.9, 'source': 'direct_follow'},
            {'post': {'author_id': 'bob', 'post_id': 'post_2'}, 'score': 0.8, 'source': 'direct_follow'},
            {'post': {'author_id': 'charlie', 'post_id': 'post_3'}, 'score': 0.7, 'source': 'second_degree'},
            {'post': {'author_id': 'bob', 'post_id': 'post_4'}, 'score': 0.6, 'source': 'direct_follow'}
        ]

        diversified = self.env._apply_diversity_filter(scored_posts, agent)

        # 验证单个作者最多2条帖子
        bob_posts = [p for p in diversified if p['post']['author_id'] == 'bob']
        assert len(bob_posts) <= 2

        # 验证包含不同来源的帖子
        sources = set(p['source'] for p in diversified)
        assert len(sources) > 1


class TestGraphSerialization:
    """图序列化接口测试"""

    def test_snapshot_and_restore_cycle(self):
        """测试完整的快照-恢复循环"""
        # 创建环境
        config = SocialNetworkConfig()
        world = MagicMock()
        world.environment_data = {'state': {'config': config}}

        env = SocialNetworkEnv(world)

        # 创建测试图
        env.graph = nx.DiGraph()
        env.graph.add_nodes_from(['alice', 'bob', 'charlie'])
        env.graph.add_edges_from([('alice', 'bob'), ('bob', 'alice'), ('alice', 'charlie')])

        # 创建快照
        snapshot = env.snapshot()

        # 验证快照包含必要信息
        assert 'graph' in snapshot
        assert 'environment_type' in snapshot
        assert snapshot['environment_type'] == 'social_network'

        graph_data = snapshot['graph']
        assert graph_data['directed'] == True
        assert len(graph_data['nodes']) == 3
        assert len(graph_data['edges']) == 3
        assert 'cv_stats' in graph_data

        # 创建新环境并恢复
        env2 = SocialNetworkEnv(world)
        env2.restore_from_snapshot(snapshot)

        # 验证恢复结果
        assert env2.graph is not None
        assert env2.graph.number_of_nodes() == 3
        assert env2.graph.number_of_edges() == 3
        assert env2.graph.has_edge('alice', 'bob')
        assert env2.graph.has_edge('bob', 'alice')

        # 验证CV值一致性
        original_cv = env._calculate_average_cv()
        restored_cv = env2._calculate_average_cv()
        assert abs(original_cv - restored_cv) < 0.001

    def test_get_actions_interface(self):
        """测试获取Actions接口"""
        config = SocialNetworkConfig()
        world = MagicMock()
        world.environment_data = {'state': {'config': config}}

        env = SocialNetworkEnv(world)
        actions = env.get_actions()

        # 验证所有Action都被返回
        expected_actions = ['publish_post', 'like_post', 'follow', 'unfollow', 'reply_to_post', 'report_post']
        for action_name in expected_actions:
            assert action_name in actions
            assert 'function' in actions[action_name]
            assert 'schema' in actions[action_name]

            # 验证schema格式
            schema = actions[action_name]['schema']
            assert 'name' in schema
            assert 'description' in schema
            assert 'parameters' in schema


class TestIntegrationAndPerformance:
    """集成测试和性能验证"""

    def test_end_to_end_workflow(self):
        """端到端工作流测试"""
        # 1. 创建配置
        config = SocialNetworkConfig(
            distribution=CVTargetedDistribution(
                params=CVTargetedParams(target_cv_mean=0.2, max_iterations=50)
            ),
            is_directed=True,
            social_media={'enabled': True}
        )

        world = MagicMock()
        world.environment_data = {'state': {'config': config}}
        world.step = 0
        world.agents_data = {'alice': MagicMock(), 'bob': MagicMock(), 'charlie': MagicMock()}

        # 2. 初始化环境
        env = SocialNetworkEnv(world)

        agents = [MagicMock(id=aid) for aid in ['alice', 'bob', 'charlie']]
        world_state = MagicMock(step=0)

        env.state = {}
        env.initialize(agents, world_state)

        # 3. 验证网络生成
        assert env.graph is not None
        assert env.graph.number_of_nodes() == 3
        cv = env._calculate_average_cv()
        assert cv >= 0  # CV值应该合理

        # 4. 测试Actions
        alice = MagicMock()
        alice.id = 'alice'
        context = MagicMock()
        context.caller = alice
        context.world = world
        context.log_event = MagicMock()

        # 发布帖子
        patches = env.publish_post(context, "Hello world!", ["test"])
        assert len(patches) > 0

        # 关注用户
        patches = env.follow(context, 'bob')
        assert env.graph.has_edge('alice', 'bob')

        # 5. 测试推荐
        alice_agent = MagicMock()
        alice_agent.id = 'alice'
        alice_agent.state = {'interests': ['test']}

        recommendations = env.get_recommended_feed(alice_agent, world_state)
        assert isinstance(recommendations, list)

        # 6. 测试序列化
        snapshot = env.snapshot()
        assert snapshot is not None

        env2 = SocialNetworkEnv(world)
        env2.restore_from_snapshot(snapshot)
        assert env2.graph.number_of_nodes() == env.graph.number_of_nodes()

    def test_large_network_performance(self):
        """大规模网络性能测试"""
        config = SocialNetworkConfig(
            distribution=CVTargetedDistribution(
                params=CVTargetedParams(target_cv_mean=0.1, max_iterations=100)
            ),
            is_directed=True
        )

        world = MagicMock()
        world.environment_data = {'state': {'config': config}}

        env = SocialNetworkEnv(world)

        # 创建100个节点的网络
        agent_ids = [f'agent_{i}' for i in range(100)]

        import time
        start_time = time.time()
        graph = env._create_cv_targeted_graph(agent_ids, config.distribution.params)
        generation_time = time.time() - start_time

        # 验证网络属性
        assert graph.number_of_nodes() == 100
        assert graph.number_of_edges() > 0

        # 性能要求：100节点网络生成时间应在10秒内
        assert generation_time < 10, f"网络生成时间过长：{generation_time}秒"

        # 验证CV值
        cv = env._calculate_average_cv_for_graph(graph)
        assert abs(cv - 0.1) < 0.1, f"大规模网络CV值偏差过大：{cv}"


if __name__ == "__main__":
    # 运行测试
    pytest.main([__file__, "-v", "--tb=short"])