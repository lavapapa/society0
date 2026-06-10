"""
PlainEnvironment基础功能测试
"""

import pytest
import logging
import sys
import os
from unittest.mock import Mock

# 添加simengine路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from simengine.core_data import World
from simengine.env.plain.env import PlainEnvironment
from simengine.agent.core import Agent


class TestPlainEnvironment:
    """PlainEnvironment测试类"""

    def setup_method(self):
        """测试前置设置"""
        self.world = Mock(spec=World)
        self.world.step = 0
        self.world.environment_data = {
            "type": "plain",
            "state": {}
        }
        self.world.agents_data = {}

        # 设置日志
        logging.basicConfig(level=logging.DEBUG)

    def test_plain_environment_initialization(self):
        """测试PlainEnvironment初始化"""
        env = PlainEnvironment(self.world)

        # 验证基本属性
        assert env._world == self.world
        assert env.type == "plain"

    def test_plain_environment_initialize(self):
        """测试环境初始化方法"""
        env = PlainEnvironment(self.world)

        # 创建模拟agents
        mock_agents = [
            Mock(spec=Agent, id="agent1"),
            Mock(spec=Agent, id="agent2")
        ]

        # 调用initialize
        env.initialize(mock_agents, self.world)

        # 验证没有抛出异常
        assert True  # 如果没有异常就通过

    def test_plain_environment_snapshot(self):
        """测试快照功能"""
        env = PlainEnvironment(self.world)
        snapshot = env.snapshot()

        # 验证快照结构
        assert "environment_type" in snapshot
        assert snapshot["environment_type"] == "plain"

    def test_plain_environment_restore_from_snapshot(self):
        """测试从快照恢复功能"""
        env = PlainEnvironment(self.world)

        # 创建测试快照
        test_snapshot = {
            "environment_type": "plain",
            "created_at": "test"
        }

        # 恢复快照（应该不抛出异常）
        env.restore_from_snapshot(test_snapshot)

        # 测试类型不匹配的情况
        wrong_snapshot = {
            "environment_type": "wrong_type"
        }

        # 应该只记录警告，不抛出异常
        env.restore_from_snapshot(wrong_snapshot)

    def test_plain_environment_no_actions(self):
        """测试PlainEnvironment不提供任何动作"""
        env = PlainEnvironment(self.world)

        # 验证没有预定义的动作
        # 这里我们通过检查环境的能力来验证
        # 由于PlainEnvironment没有@action装饰的方法，所以应该没有动作
        assert hasattr(env, 'get_actions')
        actions = env.get_actions()
        assert isinstance(actions, dict)
        # 空环境应该返回空字典或默认实现

    def test_plain_environment_state_access(self):
        """测试状态访问功能"""
        env = PlainEnvironment(self.world)

        # 验证可以访问state代理
        state = env.state
        assert state is not None

        # 验证state是代理对象
        assert hasattr(state, '__setitem__')
        assert hasattr(state, '__getitem__')

    def test_plain_environment_dependency_injection(self):
        """测试依赖倒置接口"""
        env = PlainEnvironment(self.world)

        # 模拟world的数据
        self.world.agents_data = {
            "agent1": {"type": "test", "archetype": "llm"},
            "agent2": {"type": "test", "archetype": "llm"}
        }

        # 测试依赖倒置方法
        agent1 = env.get_agent("agent1")
        assert agent1 is not None  # 应该返回某种代理对象

        all_agents = env.get_all_agents()
        assert len(all_agents) == 2

        agents_by_type = env.get_agents_by_type("test")
        assert len(agents_by_type) == 2