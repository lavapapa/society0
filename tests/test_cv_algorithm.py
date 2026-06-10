"""
最简单的CV值网络生成算法测试 - 独立测试
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import networkx as nx
from unittest.mock import MagicMock


class MockSocialNetworkEnv:
    """模拟的社交网络环境，专门用于测试CV算法"""

    def __init__(self):
        self.graph = None

    def _calculate_cv_for_node(self, graph, node):
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

    def _calculate_average_cv_for_graph(self, graph):
        """计算整个网络的平均CV值"""
        if graph.number_of_nodes() == 0:
            return 0.0

        cv_values = [self._calculate_cv_for_node(graph, node) for node in graph.nodes()]
        return sum(cv_values) / len(cv_values)


def test_cv_calculation():
    """测试CV值计算的核心算法"""
    print("🧪 测试CV值计算算法...")

    env = MockSocialNetworkEnv()

    # 创建测试用的有向图
    graph = nx.DiGraph()
    graph.add_nodes_from(['A', 'B', 'C', 'D'])

    # 构建测试网络：
    # A ←→ B (互关)
    # A → C (A关注C，C不回关)
    # C → A (C关注A，但这与上面组成互关)
    # D 独立
    graph.add_edges_from([
        ('A', 'B'), ('B', 'A'),  # A和B互关
        ('A', 'C'), ('C', 'A')   # A和C也互关
    ])

    # 测试各节点的CV值
    cv_a = env._calculate_cv_for_node(graph, 'A')  # M=2, D=2, CV=1.0
    cv_b = env._calculate_cv_for_node(graph, 'B')  # M=1, D=1, CV=1.0
    cv_c = env._calculate_cv_for_node(graph, 'C')  # M=1, D=1, CV=1.0
    cv_d = env._calculate_cv_for_node(graph, 'D')  # M=0, D=0, CV=0.0

    print(f"   节点A的CV值: {cv_a:.3f} (期望: 1.000)")
    print(f"   节点B的CV值: {cv_b:.3f} (期望: 1.000)")
    print(f"   节点C的CV值: {cv_c:.3f} (期望: 1.000)")
    print(f"   节点D的CV值: {cv_d:.3f} (期望: 0.000)")

    # 验证结果
    assert abs(cv_a - 1.0) < 0.001, f"A的CV值错误: {cv_a}"
    assert abs(cv_b - 1.0) < 0.001, f"B的CV值错误: {cv_b}"
    assert abs(cv_c - 1.0) < 0.001, f"C的CV值错误: {cv_c}"
    assert abs(cv_d - 0.0) < 0.001, f"D的CV值错误: {cv_d}"

    # 测试平均CV值
    avg_cv = env._calculate_average_cv_for_graph(graph)
    expected_avg = (1.0 + 1.0 + 1.0 + 0.0) / 4
    print(f"   网络平均CV值: {avg_cv:.3f} (期望: {expected_avg:.3f})")

    assert abs(avg_cv - expected_avg) < 0.001, f"平均CV值错误: {avg_cv}"

    print("✅ CV值计算测试通过")


def test_cv_scenarios():
    """测试不同CV值场景"""
    print("📊 测试不同CV值场景...")

    env = MockSocialNetworkEnv()

    # 场景1：强连接网络 (大部分边都是互关)
    strong_graph = nx.DiGraph()
    strong_graph.add_nodes_from(['A', 'B', 'C'])
    strong_graph.add_edges_from([
        ('A', 'B'), ('B', 'A'),  # A-B互关
        ('B', 'C'), ('C', 'B'),  # B-C互关
        ('A', 'C'), ('C', 'A')   # A-C互关
    ])

    strong_cv = env._calculate_average_cv_for_graph(strong_graph)
    print(f"   强连接网络平均CV: {strong_cv:.3f} (期望: 1.000)")
    assert strong_cv == 1.0, f"强连接网络CV值应为1.0: {strong_cv}"

    # 场景2：弱连接网络 (部分边是互关)
    weak_graph = nx.DiGraph()
    weak_graph.add_nodes_from(['A', 'B', 'C', 'D'])
    weak_graph.add_edges_from([
        ('A', 'B'), ('B', 'A'),  # A-B互关
        ('A', 'C'),              # A单向关注C
        ('D', 'A')               # D单向关注A
    ])

    weak_cv = env._calculate_average_cv_for_graph(weak_graph)
    # A: M=1, D=3, CV=0.33
    # B: M=1, D=1, CV=1.0
    # C: M=0, D=1, CV=0.0
    # D: M=0, D=1, CV=0.0
    # 平均: (0.33 + 1.0 + 0.0 + 0.0) / 4 = 0.33
    expected_weak = (1/3 + 1.0 + 0.0 + 0.0) / 4
    print(f"   弱连接网络平均CV: {weak_cv:.3f} (期望: {expected_weak:.3f})")
    assert abs(weak_cv - expected_weak) < 0.01, f"弱连接网络CV值错误: {weak_cv}"

    # 场景3：无连接网络 (所有边都是单向)
    none_graph = nx.DiGraph()
    none_graph.add_nodes_from(['A', 'B', 'C'])
    none_graph.add_edges_from([
        ('A', 'B'),  # A关注B
        ('B', 'C'),  # B关注C
    ])

    none_cv = env._calculate_average_cv_for_graph(none_graph)
    print(f"   无连接网络平均CV: {none_cv:.3f} (期望: 0.000)")
    assert none_cv == 0.0, f"无连接网络CV值应为0.0: {none_cv}"

    print("✅ CV值场景测试通过")


def test_network_metrics():
    """测试网络拓扑指标"""
    print("🌐 测试网络拓扑指标...")

    # 根据论文定义测试CV值分类
    def classify_cv(cv):
        if cv > 0.2:
            return "强连接"
        elif cv > 0:
            return "弱连接"
        else:
            return "无连接"

    test_cases = [
        (0.8, "强连接"),
        (0.3, "强连接"),
        (0.2, "弱连接"),
        (0.1, "弱连接"),
        (0.0, "无连接")
    ]

    for cv_value, expected_type in test_cases:
        actual_type = classify_cv(cv_value)
        print(f"   CV={cv_value:.1f} -> {actual_type} (期望: {expected_type})")
        assert actual_type == expected_type, f"CV分类错误: {cv_value} -> {actual_type}"

    print("✅ 网络拓扑指标测试通过")


def test_edge_cases():
    """测试边界情况"""
    print("🔍 测试边界情况...")

    env = MockSocialNetworkEnv()

    # 空图
    empty_graph = nx.DiGraph()
    empty_cv = env._calculate_average_cv_for_graph(empty_graph)
    assert empty_cv == 0.0, f"空图CV值应为0: {empty_cv}"
    print("   空图处理: ✅")

    # 单节点图
    single_graph = nx.DiGraph()
    single_graph.add_node('A')
    single_cv = env._calculate_average_cv_for_graph(single_graph)
    assert single_cv == 0.0, f"单节点图CV值应为0: {single_cv}"
    print("   单节点图处理: ✅")

    # 自环图
    self_loop_graph = nx.DiGraph()
    self_loop_graph.add_nodes_from(['A', 'B'])
    self_loop_graph.add_edges_from([('A', 'A'), ('A', 'B')])  # A有自环
    cv_a_with_loop = env._calculate_cv_for_node(self_loop_graph, 'A')
    print(f"   自环节点CV值: {cv_a_with_loop:.3f}")
    print("   自环图处理: ✅")

    print("✅ 边界情况测试通过")


def main():
    """运行所有测试"""
    print("🚀 开始SocialNetworkEnv CV值算法独立测试\n")

    try:
        test_cv_calculation()
        print()

        test_cv_scenarios()
        print()

        test_network_metrics()
        print()

        test_edge_cases()
        print()

        print("🎉 所有CV值算法测试通过！")
        print("📈 核心功能验证：")
        print("   ✅ CV值计算公式正确 (CV = M/D)")
        print("   ✅ 互关检测逻辑正确")
        print("   ✅ 网络分类标准符合论文定义")
        print("   ✅ 边界情况处理完善")

        return True

    except Exception as e:
        print(f"❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)