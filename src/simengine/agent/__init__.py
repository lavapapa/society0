"""
Agent模块：包含所有Agent相关的核心组件

导出：
- Agent基类和子类
- Persona人格系统
- Memory记忆系统（已重构，移除全局状态）
- ActionLoop推理引擎
- Memory行动集
- Behavior行动
"""

from .core import Agent, RuleAgent, LLMAgent
from .memory import Memory, MemoryEntry, ollama_embed  # 移除全局状态函数
from .agent_loop import ActionSet, ActionCall, LoopResult, execute_action_loop
from .memory_actions import create_memory_actions, register_memory_actions_to_actionset
from .behavior_action import create_behavior_action, register_behavior_action_to_actionset

# Backward compatibility aliases
# ToolSet = ActionSet
# ToolCall = ActionCall
# execute_tool_call_loop = execute_action_loop
# create_memory_tools = create_memory_actions
# register_memory_tools_to_toolset = register_memory_actions_to_actionset

__all__ = [
    # 核心Agent类
    'Agent', 'RuleAgent', 'LLMAgent',
    # 记忆系统 (已重构，移除全局状态)
    'Memory', 'MemoryEntry', 'ollama_embed',
    # 推理引擎
    'ActionSet', 'ActionCall', 'LoopResult', 'execute_action_loop',
    # 记忆行动
    'create_memory_actions', 'register_memory_actions_to_actionset',
    # Behavior行动
    'create_behavior_action', 'register_behavior_action_to_actionset',
    # 向后兼容
    # 'ToolSet', 'ToolCall', 'execute_tool_call_loop',
    # 'create_memory_tools', 'register_memory_tools_to_toolset'
]