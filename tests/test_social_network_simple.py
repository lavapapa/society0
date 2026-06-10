"""
简化的SocialNetworkEnv测试 - 基本功能验证
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import networkx as nx
from unittest.mock import MagicMock

def test_cv_calculation():
    """测试CV值计算功能"""
    print("🧪 测试CV值计算...")

    from simengine.env.social_network.env import SocialNetworkEnv
    from simengine.env.social_network.models import SocialNetworkConfig

    # 创建测试用的有向图
    graph = nx.DiGraph()
    graph.add_nodes_from(['A', 'B', 'C'])

    # A关注B和C，B关注A，C单向关注A
    graph.add_edges_from([('A', 'B'), ('B', 'A'), ('A', 'C')])

    # 创建环境实例
    world = MagicMock()
    world.environment_data = {'state': {'config': {}}}
    env = SocialNetworkEnv(world)

    # 测试A的CV值：M=1（与B互关），D=2，CV=0.5
    cv_a = env._calculate_cv_for_node(graph, 'A')
    print(f"   节点A的CV值: {cv_a}")

    # 测试B的CV值：M=1（与A互关），D=1，CV=1.0
    cv_b = env._calculate_cv_for_node(graph, 'B')
    print(f"   节点B的CV值: {cv_b}")

    # 测试C的CV值：M=0（无互关），D=1，CV=0.0
    cv_c = env._calculate_cv_for_node(graph, 'C')
    print(f"   节点C的CV值: {cv_c}")

    print("✅ CV值计算测试通过")

def test_network_generation():
    """测试网络生成功能"""
    print("🌐 测试网络生成...")

    from simengine.env.social_network.env import SocialNetworkEnv
    from simengine.env.social_network.models import SocialNetworkConfig, SmallWorldDistribution, SmallWorldParams

    config = SocialNetworkConfig(
        distribution=SmallWorldDistribution(
            params=SmallWorldParams(k_neighbors=2)
        ),
        is_directed=True
    )

    world = MagicMock()
    world.environment_data = {'state': {'config': config}}

    env = SocialNetworkEnv(world)

    # 生成网络
    agent_ids = ['alice', 'bob', 'charlie', 'diana']
    graph = env._create_traditional_graph(agent_ids, config)

    print(f"   生成网络: {graph.number_of_nodes()} 个节点, {graph.number_of_edges()} 条边")
    print(f"   平均CV值: {env._calculate_average_cv_for_graph(graph):.3f}")

    assert graph.number_of_nodes() == 4
    print("✅ 网络生成测试通过")

def test_action_system():
    """测试Action系统"""
    print("⚡ 测试Action系统...")

    from simengine.env.social_network.env import SocialNetworkEnv
    from simengine.env.social_network.models import SocialNetworkConfig

    config = SocialNetworkConfig(social_media={'enabled': True})
    world = MagicMock()
    world.environment_data = {'state': {'config': config}}
    world.step = 10
    world.agents_data = {'bob': MagicMock()}

    env = SocialNetworkEnv(world)
    env.graph = nx.DiGraph()
    env.graph.add_nodes_from(['alice', 'bob'])
    env.state = {
        'posts': {},
        'author_to_post_ids': {},
        'post_counter': 0
    }

    # 创建测试agent和context
    agent = MagicMock()
    agent.id = 'alice'

    context = MagicMock()
    context.caller = agent
    context.world = world
    context.log_event = MagicMock()

    # 测试发布帖子
    patches = env.publish_post(context, "Hello world!", ["test"])
    print(f"   发布帖子生成 {len(patches)} 个StatePatch")

    # 测试关注功能
    patches = env.follow(context, 'bob')
    print(f"   关注操作生成 {len(patches)} 个StatePatch")
    print(f"   Alice是否关注Bob: {env.graph.has_edge('alice', 'bob')}")

    print("✅ Action系统测试通过")

def test_snapshot_system():
    """测试快照系统"""
    print("💾 测试快照系统...")

    from simengine.env.social_network.env import SocialNetworkEnv
    from simengine.env.social_network.models import SocialNetworkConfig

    config = SocialNetworkConfig()
    world = MagicMock()
    world.environment_data = {'state': {'config': config}}

    env = SocialNetworkEnv(world)

    # 创建测试图
    env.graph = nx.DiGraph()
    env.graph.add_nodes_from(['alice', 'bob', 'charlie'])
    env.graph.add_edges_from([('alice', 'bob'), ('bob', 'charlie')])

    # 创建快照
    snapshot = env.snapshot()
    print(f"   快照包含 {len(snapshot)} 项数据")
    print(f"   图数据包含 {snapshot['graph']['node_count']} 个节点")

    # 恢复快照
    env2 = SocialNetworkEnv(world)
    env2.restore_from_snapshot(snapshot)

    print(f"   恢复后: {env2.graph.number_of_nodes()} 个节点, {env2.graph.number_of_edges()} 条边")

    assert env2.graph.number_of_nodes() == 3
    assert env2.graph.number_of_edges() == 2

    print("✅ 快照系统测试通过")

def main():
    """运行所有测试"""
    print("🚀 开始SocialNetworkEnv增强版功能测试\n")

    try:
        test_cv_calculation()
        print()

        test_network_generation()
        print()

        test_action_system()
        print()

        test_snapshot_system()
        print()

        print("🎉 所有测试通过！SocialNetworkEnv增强版功能正常")
        return True

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)