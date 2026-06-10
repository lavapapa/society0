"""
Behavior行动：列出和执行Agent可用的behaviors

这个行动允许Agent查看和执行其可用的behaviors。
Behaviors代表Agent的条件反射或自动行为。
"""

from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

def create_behavior_action() -> Dict[str, Any]:
    """
    创建behavior行动，用于列出Agent可用的behaviors
    
    Returns:
        符合OpenAI schema的行动定义
    """
    
    async def list_behaviors() -> List[str]:
        """
        列出当前Agent可用的behaviors
        
        这个函数需要与Function Registry集成来获取
        注册为此Agent类型可用的behaviors。
        
        Returns:
            可用behavior的名称和描述列表
        """
        # TODO: 与Function Registry集成
        # 当前返回示例behaviors
        available_behaviors = [
            "daily_routine: 执行日常例行公事",
            "respond_to_fatigue: 对疲劳状态做出反应",
            "social_interaction: 执行社交互动行为",
            "work_task: 执行工作相关任务"
        ]
        
        return available_behaviors
    
    # 返回行动定义
    action_def = {
        "type": "function",
        "function": {
            "name": "list_behaviors",
            "description": "列出当前Agent可用的所有behaviors（条件反射和自动行为）",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            },
            "implementation": list_behaviors,
            "tags": ["behavior", "basic"]
        }
    }
    
    return action_def

def register_behavior_action_to_actionset(actionset, agent_type: str = None):
    """
    将behavior行动注册到ActionSet中
    
    Args:
        actionset: Agent的行动集
        agent_type: Agent类型，用于筛选可用的behaviors
    """
    behavior_action = create_behavior_action()
    func_def = behavior_action["function"]
    
    actionset.add_action(
        name=func_def["name"],
        func=func_def["implementation"],
        description=func_def["description"],
        parameters=func_def["parameters"],
        tags=func_def.get("tags", [])
    )