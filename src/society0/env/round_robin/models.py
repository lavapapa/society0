"""
Round-robin 对话环境的 Pydantic 模型定义。

包含：
- 配置模型（RoundRobinConfig）
- 状态相关的数据结构（PairingStatus、AgentConversationState 等）
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ConfigDict


class PairingStrategy(str, Enum):
    """配对算法类型。"""

    STANDARD = "standard"
    TOURNAMENT = "tournament"
    CUSTOM = "custom"


class RoundRobinConfig(BaseModel):
    """Round-robin 环境的核心配置。"""

    model_config = ConfigDict(extra="forbid")

    group_size: int = Field(..., ge=2, le=20, description="每个小组包含的 Agent 数量。")
    session_duration_minutes: int = Field(
        10, ge=1, le=120, description="每个配对会话建议持续时长（分钟）。"
    )
    pairing_strategy: PairingStrategy = Field(
        PairingStrategy.STANDARD, description="配对策略类型。"
    )
    message_persistence: bool = Field(
        False, description="是否跨轮次保留历史消息。"
    )


class ConversationMessage(BaseModel):
    """环境内部存储的对话消息。"""

    model_config = ConfigDict(extra="forbid")

    sender: str = Field(..., description="消息发送者 Agent ID。")
    receiver: str = Field(..., description="消息接收者 Agent ID。")
    content: str = Field(..., description="消息内容。")
    round: int = Field(..., ge=0, description="消息所属轮次。")
    timestamp: float = Field(..., description="消息生成时间（秒）。")


class AgentConversationState(BaseModel):
    """每个 Agent 的对话状态镜像。"""

    model_config = ConfigDict(extra="forbid")

    current_partner: Optional[str] = Field(
        None, description="当前配对伙伴的 Agent ID。"
    )
    partner_history: List[str] = Field(
        default_factory=list, description="历史配对伙伴列表（按轮次排序）。"
    )
    current_round: int = Field(0, ge=0, description="当前轮次编号（从 0 开始）。")
    can_converse: bool = Field(
        False, description="是否允许发送消息（当前轮是否激活）。"
    )
    is_conversation_active: bool = Field(
        False, description="当前配对对话是否处于激活状态。"
    )


class PairingStatus(BaseModel):
    """Round-robin 配对执行状态。"""

    model_config = ConfigDict(extra="forbid")

    current_round: int = Field(0, ge=0, description="当前轮次编号（从 0 开始）。")
    total_rounds: int = Field(0, ge=0, description="计划的总轮次数。")
    agent_partner: Dict[str, Optional[str]] = Field(
        default_factory=dict, description="当前轮次中每个 Agent 的配对伙伴。"
    )
    completed_pairs: List[Tuple[str, str]] = Field(
        default_factory=list, description="已完成的配对列表。"
    )
    pairing_schedule: List[List[Tuple[str, str]]] = Field(
        default_factory=list, description="所有轮次的配对计划。"
    )
    round_active_pairs: Dict[int, List[Tuple[str, str]]] = Field(
        default_factory=dict, description="每轮激活的配对列表。"
    )
