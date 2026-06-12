"""
Plain环境包 - 提供最简单的空白环境实现

该环境不提供任何状态、动作或视野，用于：
- Agent行为基准测试
- 教学和演示
- 快速原型开发
- 调试和测试
"""

from .env import PlainEnvironment

__all__ = ["PlainEnvironment"]