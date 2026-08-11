"""
PlainEnvironment: 完全空白的基准环境

这是一个零状态、零能力、零配置的环境，适用于Agent行为基准测试、
教学演示和从零开始构建模拟实验。
"""

from typing import Dict, Any, List, TYPE_CHECKING
import logging

from ...environment import Environment
from ...decorators import env_type

if TYPE_CHECKING:
    from ...core_data import World
    from ...agent.core import Agent

logger = logging.getLogger(__name__)

# Plain环境的Schema定义
PLAIN_CONFIG_SCHEMA = {
    "type": "object",
    "title": "PlainEnvironmentConfig",
    "properties": {},
    "additionalProperties": False
}

PLAIN_STATE_SCHEMA = {
    "type": "object",
    "title": "PlainEnvironmentState",
    "properties": {},
    "additionalProperties": False
}

@env_type(
    type_name="plain",
    config_schema=PLAIN_CONFIG_SCHEMA,
    state_schema=PLAIN_STATE_SCHEMA,
    agent_managed_fields_schema={
        "type": "object",
        "properties": {}
    },
    builtin_state_fields=[],
    display_name="Plain Environment",
    description="空白基准环境，用于学习和从零构建模拟。适用于Agent行为研究、教学演示和无复杂交互的模拟场景。"
)
class PlainEnvironment(Environment):
    """
    完全空白的环境实现

    特性：
    - 零状态：不维护任何环境状态
    - 零能力：不提供任何环境动作、视野或规则
    - 零配置：不需要任何配置参数

    适用于：
    - Agent行为基准测试
    - 教学和演示
    - 从零构建模拟
    - 无需复杂环境和交互的实验
    """

    def __init__(self, world: 'World'):
        """初始化空白环境"""
        super().__init__(world)
        logger.debug("PlainEnvironment initialized")

    def initialize(self, agents: List['Agent'], world: 'World') -> None:
        """
        初始化环境

        空白环境不需要任何特殊初始化，只记录基本信息
        """
        agent_count = len(agents)
        logger.info(f"PlainEnvironment initialized with {agent_count} agents at step {world.step}")

    def snapshot(self, *, include_state: bool = True) -> Dict[str, Any]:
        """
        创建环境快照

        空白环境只需要标识类型，无需存储状态数据
        """
        return {
            "environment_type": "plain",
            "created_at": logger.handlers[0].stream.name if logger.handlers else "unknown"
        }

    def restore_from_snapshot(self, snapshot_data: Dict[str, Any]) -> None:
        """
        从快照恢复环境

        空白环境没有状态需要恢复，只验证快照类型
        """
        if snapshot_data.get("environment_type") != "plain":
            logger.warning(f"Snapshot type mismatch: expected 'plain', got '{snapshot_data.get('environment_type')}'")

        logger.info("PlainEnvironment restored from snapshot")
