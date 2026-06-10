"""
记忆系统Agent行动集

统一管理所有记忆相关的Agent行动，提供标准的OpenAI函数调用接口。
这些行动将被Memory实例暴露，并由LLMAgent收集到其行动注册器中。
所有记忆相关行动都带有['memory', 'basic']标签。
"""

from typing import List, Dict, Any, Optional, TYPE_CHECKING
import asyncio
import logging

if TYPE_CHECKING:
    from .memory import Memory
    from .agent_loop import ActionSet

logger = logging.getLogger(__name__)

def create_memory_actions(memory: 'Memory', llm_call: Optional[callable] = None, current_step_provider: Optional[callable] = None) -> List[Dict[str, Any]]:
    """
    为Memory实例创建所有相关的Agent行动

    Args:
        memory: Memory实例
        llm_call: LLM调用函数，用于反思行动
        current_step_provider: 用于获取当前时间步的函数

    Returns:
        符合OpenAI schema的行动定义列表
    """

    def _get_current_step() -> int:
        """获取当前时间步"""
        if current_step_provider and callable(current_step_provider):
            try:
                return current_step_provider()
            except Exception:
                pass
        return 0  # 默认时间步

    async def search_memory(query: str) -> List[str]:
        """
        搜索记忆 - 根据关键词精确搜索Agent的记忆库

        Args:
            query: 搜索查询词

        Returns:
            相关记忆内容列表
        """
        try:
            results = await memory.retrieve(query=query, top_k=10)
            return results
        except Exception as e:
            logger.error(f"Memory search failed: {e}")
            return [f"记忆搜索失败: {str(e)}"]

    async def reflect_on_topic(topic: str) -> str:
        """
        针对主题反思 - 触发反思流程，生成关于特定主题的语义记忆

        Args:
            topic: 反思主题

        Returns:
            反思结果和生成的语义记忆
        """
        if not llm_call:
            return "反思功能需要LLM支持，当前未配置"

        try:
            # 首先搜索相关记忆作为背景
            relevant_memories = await memory.retrieve(query=topic, top_k=5)
            memory_context = "\n".join(relevant_memories) if relevant_memories else "没有找到相关记忆"

            # 构建反思prompt
            reflection_prompt = f"""基于以下相关记忆，请对"{topic}"这个主题进行深入反思：

相关记忆背景：
{memory_context}

请进行反思分析，提供洞见和知识点。反思应该包括：
1. 对现有记忆的综合分析
2. 发现的模式或趋势
3. 重要的洞见或结论
4. 对未来的启示

请提供简洁而深刻的反思结果。"""

            # 调用LLM进行反思
            response = await llm_call({
                "messages": [
                    {"role": "system", "content": "你是一个深度反思专家，能够从记忆中提取洞见和知识。"},
                    {"role": "user", "content": reflection_prompt}
                ]
            })

            reflection_result = response.get("content", "反思失败")

            # 将反思结果作为语义记忆存储
            current_step = _get_current_step()
            memory_id = await memory.add_semantic_memory(
                content=f"关于'{topic}'的反思: {reflection_result}",
                timestamp=current_step,
                importance=4.0  # 反思产生的记忆通常比较重要
            )

            return f"反思完成。生成的洞见: {reflection_result}\\n\\n已将此反思存储为语义记忆 (ID: {memory_id})"

        except Exception as e:
            logger.error(f"Reflection failed: {e}")
            return f"反思失败: {str(e)}"

    async def memorize_this(fact: str, importance: float) -> str:
        """
        强制记忆 - 将一条信息作为高重要度的语义记忆直接存入

        Args:
            fact: 要记忆的事实或信息
            importance: 重要性评分 (0-5)

        Returns:
            记忆存储结果
        """
        try:
            # 验证重要性评分范围
            importance = max(0.0, min(5.0, importance))

            # 存储为语义记忆
            current_step = _get_current_step()
            memory_id = await memory.add_semantic_memory(
                content=fact,
                timestamp=current_step,
                importance=importance
            )

            return f"已成功记忆: {fact}\\n记忆ID: {memory_id}，重要性: {importance}"

        except Exception as e:
            logger.error(f"Failed to memorize fact: {e}")
            return f"记忆存储失败: {str(e)}"

    # 返回行动定义，符合OpenAI schema，包含tags
    actions = [
        {
            "type": "function",
            "function": {
                "name": "search_memory",
                "description": "搜索Agent的记忆库，根据关键词找到相关的记忆内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索查询词或关键短语"
                        }
                    },
                    "required": ["query"]
                },
                "implementation": search_memory,
                "tags": ["memory", "basic"]  # 基础记忆行动
            }
        },
        {
            "type": "function",
            "function": {
                "name": "reflect_on_topic",
                "description": "对特定主题进行深度反思，生成洞见并存储为语义记忆",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "要反思的主题或话题"
                        }
                    },
                    "required": ["topic"]
                },
                "implementation": reflect_on_topic,
                "tags": ["memory", "reflection"]
            }
        },
        {
            "type": "function",
            "function": {
                "name": "memorize_this",
                "description": "强制记忆一条重要信息，直接存储为语义记忆",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "fact": {
                            "type": "string",
                            "description": "要记忆的事实或信息内容"
                        },
                        "importance": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 5,
                            "description": "重要性评分，0-5之间的数值，3表示中等重要性"
                        }
                    },
                    "required": ["fact", "importance"]
                },
                "implementation": memorize_this,
                "tags": ["memory", "storage"]
            }
        }
    ]

    return actions

def register_memory_actions_to_actionset(actionset: 'ActionSet', memory_actions: List[Dict[str, Any]]):
    """
    将记忆行动注册到ActionSet中

    Args:
        actionset: Agent的行动集
        memory_actions: 从create_memory_actions获得的行动列表
    """
    for action in memory_actions:
        func_def = action["function"]
        actionset.add_action(
            name=func_def["name"],
            func=func_def["implementation"],
            description=func_def["description"],
            parameters=func_def["parameters"],
            tags=func_def.get("tags", [])
        )

# Backward compatibility aliases
# create_memory_tools = create_memory_actions
# register_memory_tools_to_toolset = register_memory_actions_to_actionset
