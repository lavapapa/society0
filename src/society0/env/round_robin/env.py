"""
RoundRobinConversationEnv 定义。

提供轮次化对话实验的环境能力，负责：
- 生成并维护 round-robin 配对计划
- 管理对话消息生命周期
- 暴露配对启动、消息发送、轮次推进等能力
- 提供 FoV 视图供 Agent 获取上下文
"""
from __future__ import annotations

from typing import Dict, List, Tuple, Any, TYPE_CHECKING
from collections.abc import Mapping, Sequence
import logging
import time

from ...environment import Environment
from ...decorators import env_type, action, behavior, fov, rule
from ...core_data import ExecutionContext
from ...state_proxy import DictProxy, ListProxy

if TYPE_CHECKING:
    from ...core_data import World
    from ...agent.core import Agent

from .models import (
    RoundRobinConfig,
    PairingStatus,
    AgentConversationState,
    ConversationMessage,
)

logger = logging.getLogger(__name__)


ROUND_ROBIN_CONFIG_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "title": "RoundRobinConversationConfig",
    "properties": {
        "group_size": {
            "type": "integer",
            "minimum": 2,
            "maximum": 20,
            "description": "每个小组包含的 Agent 数量。",
        },
        "session_duration_minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 120,
            "default": 10,
            "description": "每次配对对话的建议时长（分钟）。",
        },
        "pairing_strategy": {
            "type": "string",
            "enum": ["standard", "tournament", "custom"],
            "default": "standard",
            "description": "配对策略类型。",
        },
        "message_persistence": {
            "type": "boolean",
            "default": False,
            "description": "是否跨轮次保留消息历史。",
        },
    },
    "required": ["group_size"],
    "additionalProperties": False,
}

ROUND_ROBIN_STATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "title": "RoundRobinConversationState",
    "properties": {
        "config": {
            "type": "object",
            "persistence": {"kind": "transient"},
            "description": "环境的配置快照（由配置重建）。",
        },
        "groups": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {"type": "string"},
            },
            "persistence": {"kind": "transient"},
            "description": "划分好的 Agent 小组，每组按 group_size 切分（由配置重建）。",
        },
        "pairing_current_round": {
            "type": "integer",
            "persistence": {"kind": "replaceable"},
            "default": 0,
            "description": "当前轮次编号。",
        },
        "pairing_total_rounds": {
            "type": "integer",
            "persistence": {"kind": "replaceable"},
            "default": 0,
            "description": "计划的总轮次数。",
        },
        "pairing_current_partner": {
            "type": "object",
            "additionalProperties": {"type": ["string", "null"]},
            "persistence": {"kind": "replaceable", "granularity": "entry"},
            "description": "每个 Agent 当前配对伙伴的有界投影。",
        },
        "pairing_active_pairs": {
            "type": "object",
            "persistence": {"kind": "transient"},
            "description": "当前轮激活的配对缓存。",
        },
        "pairing_completed_pairs": {
            "type": "array",
            "items": {
                "type": "array",
                "items": {"type": "string"},
            },
            "persistence": {"kind": "append_only_list"},
            "description": "已完成的配对事实。",
        },
        "conversation_current": {
            "type": "object",
            "additionalProperties": {"type": "object"},
            "persistence": {"kind": "replaceable", "granularity": "entry"},
            "description": "每个 Agent 当前对话状态投影。",
        },
        "conversation_partner_history": {
            "type": "array",
            "items": {"type": "object"},
            "persistence": {"kind": "append_only_list"},
            "description": "配对历史事实。",
        },
        "message_facts": {
            "type": "array",
            "items": {"type": "object"},
            "persistence": {"kind": "append_only_list"},
            "description": "不可变消息事实。",
        },
        "active_messages": {
            "type": "object",
            "persistence": {"kind": "transient"},
            "description": "当前轮次快速访问的消息缓存。",
        },
        "message_retention": {
            "type": "object",
            "additionalProperties": {"type": "object"},
            "persistence": {"kind": "replaceable", "granularity": "entry"},
            "description": "消息保留/索引投影。",
        },
        "message_counter": {
            "type": "integer",
            "persistence": {"kind": "replaceable"},
            "description": "全局消息计数器。",
        },
    },
    "additionalProperties": False,
}


@env_type(
    type_name="round_robin_conversation",
    config_schema=ROUND_ROBIN_CONFIG_SCHEMA,
    state_schema=ROUND_ROBIN_STATE_SCHEMA,
    agent_managed_fields_schema={"type": "object", "properties": {}},
    builtin_state_fields=[
        {"name": "pairing_current_round", "type": "int", "description": "当前轮次。"},
        {"name": "pairing_total_rounds", "type": "int", "description": "计划总轮次。"},
        {"name": "pairing_current_partner", "type": "Dict[str, Optional[str]]", "description": "当前伙伴投影。"},
        {"name": "pairing_completed_pairs", "type": "List[List[str]]", "description": "已完成配对事实。"},
        {"name": "conversation_current", "type": "Dict[str, Any]", "description": "当前对话投影。"},
        {"name": "conversation_partner_history", "type": "List[Dict[str, Any]]", "description": "配对历史事实。"},
        {"name": "message_facts", "type": "List[Dict[str, Any]]", "description": "不可变消息事实。"},
        {"name": "active_messages", "type": "Dict[str, Any]", "description": "当前轮次快速访问消息缓存。"},
        {"name": "message_retention", "type": "Dict[str, Any]", "description": "消息保留/索引投影。"},
        {"name": "message_counter", "type": "int", "description": "全局消息计数器。"},
    ],
    display_name="Round-Robin 对话环境",
    description="""支持多人轮转对话的环境，适用于心理学实验、团队协作研究等场景。

核心特性：
- 支持 N 人小组的 Round-Robin 配对算法
- 每轮生成不重复的配对组合
- 提供配对激活、消息传递、状态管理等能力

使用模式（典型工作流）：
1. 使用 advance_round_robin 开始新轮次（清空上一轮消息）
2. 使用 start_pairing_session 激活特定配对
3. 使用 instruct 进行对话交互（一次instruct = 完整对话）
4. 重复步骤2-3完成当前轮次的所有配对
5. 使用 advance_round_robin 进入下一轮

重要约束：
- 每轮对话开始前必须调用 advance_round_robin
- 只有激活的配对才能进行有效的对话交互
- 建议每个配对对话时长约10分钟
""",
)
class RoundRobinConversationEnv(Environment):
    """Round-robin 对话环境实现。"""

    def __init__(self, world: "World"):
        super().__init__(world)

        raw_config: Any = world.environment_data.get("config") or {}

        if isinstance(raw_config, RoundRobinConfig):
            self._config = raw_config
        else:
            self._config = RoundRobinConfig.model_validate(raw_config)

        # 初始化容器
        self._groups: List[List[str]] = []
        self._group_index_by_agent: Dict[str, int] = {}
        self._group_schedules: Dict[int, List[List[Tuple[str, str]]]] = {}
        self._pairing_strategy = self._config.pairing_strategy

        logger.debug("RoundRobinConversationEnv 配置已验证：%s", self._config.model_dump())

    def _state_store(self):
        """返回 Tick 代理；初始化/恢复阶段使用原始 canonical state。"""
        journal = getattr(self._world, "_state_delta_journal", None)
        active_tick = getattr(journal, "_active_step", None) is not None
        return self.state if active_tick else self._world.environment_data.setdefault("state", {})

    # --- 生命周期钩子 ---

    def initialize(self, agents, world):
        """根据配置划分小组并初始化状态。"""
        agent_ids = [agent.id for agent in agents]
        group_size = self._config.group_size
        restored_state = any(
            key in self.state
            for key in ("pairing_current_round", "pairing_completed_pairs", "message_facts")
        )

        if not agent_ids:
            logger.warning("没有可用的 Agent，RoundRobinConversationEnv 初始化为空环境。")
            self._groups = []
            self._group_index_by_agent = {}
            self._group_schedules = {}
            self._ensure_state_initialized()
            return

        if len(agent_ids) % group_size != 0:
            raise ValueError(
                f"Agent 总数 {len(agent_ids)} 无法被 group_size={group_size} 整除。"
            )

        # 均匀切分小组
        self._groups = [
            agent_ids[i : i + group_size]
            for i in range(0, len(agent_ids), group_size)
        ]
        self._group_index_by_agent = {
            agent_id: idx
            for idx, group in enumerate(self._groups)
            for agent_id in group
        }

        # 构建各组的 round-robin 配对计划（按组独立存储）
        self._group_schedules = {}
        for group_idx, group in enumerate(self._groups):
            schedule = self._build_round_robin_schedule(group)
            self._group_schedules[group_idx] = schedule

        # 计算总轮数（各组应一致，取最大值容错）
        total_rounds = max((len(s) for s in self._group_schedules.values()), default=0)
        current_round = 1 if total_rounds > 0 else 0

        self._ensure_state_initialized()
        state = self._state_store()

        if restored_state:
            # 恢复时只重建配置派生结构与 transient 活跃缓存；持久化投影/事实保持不变。
            state["config"] = self._config.model_dump()
            state["groups"] = [list(group) for group in self._groups]
            self._rebuild_active_messages()
            return

        state["config"] = self._config.model_dump()
        state["groups"] = [list(group) for group in self._groups]

        state["pairing_current_round"] = current_round
        state["pairing_total_rounds"] = total_rounds
        state["pairing_current_partner"] = {
            agent_id: None for agent_id in agent_ids
        }
        state["pairing_active_pairs"] = {}
        state["pairing_completed_pairs"] = []

        conversation_state = {
            agent_id: {
                key: value
                for key, value in AgentConversationState(
                    current_partner=None,
                    partner_history=[],
                    current_round=current_round,
                    can_converse=False,
                    is_conversation_active=False,
                ).model_dump().items()
                if key != "partner_history"
            }
            for agent_id in agent_ids
        }
        state["conversation_current"] = conversation_state
        state["conversation_partner_history"] = []

        state["message_facts"] = []
        state["message_retention"] = {}
        if current_round:
            self._clear_round_messages(current_round)
        else:
            state["active_messages"] = {agent_id: [] for agent_id in agent_ids}

        state["message_counter"] = 0

        logger.info(
            "RoundRobinConversationEnv 已初始化：%s 个小组、每组 %s 人，总轮数 %s。",
            len(self._groups),
            group_size,
            total_rounds,
        )

    def _rebuild_active_messages(self) -> None:
        """从不可变消息事实重建当前轮 transient 收件箱。"""
        state = self._state_store()
        current_round = int(state.get("pairing_current_round", 0) or 0)
        active = {agent_id: [] for agent_id in self._group_index_by_agent.keys()}
        if current_round:
            for raw_message in state.get("message_facts", []) or []:
                message = self._to_plain(raw_message)
                if not isinstance(message, Mapping) or message.get("round") != current_round:
                    continue
                receiver = message.get("receiver")
                if receiver in active:
                    active[receiver].append(message)
        state["active_messages"] = active

    # --- 环境内部工具（稍后实现） ---

    def _ensure_state_initialized(self) -> None:
        state = self._state_store()

        if "config" not in state:
            state["config"] = self._config.model_dump()
        if "groups" not in state:
            state["groups"] = [list(group) for group in self._groups]
        if "pairing_current_round" not in state:
            state["pairing_current_round"] = 0
        if "pairing_total_rounds" not in state:
            state["pairing_total_rounds"] = 0
        if "pairing_current_partner" not in state:
            state["pairing_current_partner"] = {}
        if "pairing_active_pairs" not in state:
            state["pairing_active_pairs"] = {}
        if "pairing_completed_pairs" not in state:
            state["pairing_completed_pairs"] = []
        if "conversation_current" not in state:
            state["conversation_current"] = {}
        if "conversation_partner_history" not in state:
            state["conversation_partner_history"] = []
        if "message_facts" not in state:
            state["message_facts"] = []
        if "message_retention" not in state:
            state["message_retention"] = {}
        if "active_messages" not in state:
            state["active_messages"] = {
                agent_id: [] for agent_id in self._group_index_by_agent.keys()
            }
        if "message_counter" not in state:
            state["message_counter"] = 0

    def _build_round_robin_schedule(self, group: List[str]) -> List[List[Tuple[str, str]]]:
        if not group:
            return []

        players = list(group)
        count = len(players)

        if count < 2:
            return []
        if count % 2 != 0:
            raise ValueError("Round-robin 小组人数必须为偶数。")

        schedule: List[List[Tuple[str, str]]] = []
        rotation = players[:]

        for _ in range(count - 1):
            round_pairs: List[Tuple[str, str]] = []
            for i in range(count // 2):
                first = rotation[i]
                second = rotation[count - 1 - i]
                if first == second:
                    continue
                round_pairs.append((first, second))
            schedule.append(round_pairs)

            # 旋转（保持首元素不动）
            rotation = [rotation[0]] + [rotation[-1]] + rotation[1:-1]

        return schedule

    def _get_agent_state(self, agent_id: str) -> AgentConversationState:
        self._ensure_state_initialized()
        state = self._state_store().setdefault("conversation_current", {})
        raw = state.get(agent_id)
        if raw is None:
            raw_state = AgentConversationState().model_dump()
            state[agent_id] = {
                key: value
                for key, value in raw_state.items()
                if key not in {"partner_history"}
            }
            return AgentConversationState.model_validate(raw_state)
        plain_state = self._to_plain(raw)
        history = self._partner_history_for_agent(agent_id)
        plain_state["partner_history"] = history
        return AgentConversationState.model_validate(plain_state)

    def _set_agent_state(self, agent_id: str, agent_state: AgentConversationState) -> None:
        self._ensure_state_initialized()
        conversation_state = self._state_store().setdefault("conversation_current", {})
        current = agent_state.model_dump()
        current.pop("partner_history", None)
        conversation_state[agent_id] = current

    def _partner_history_for_agent(self, agent_id: str) -> List[str]:
        history = []
        for fact in self.state.get("conversation_partner_history", []) or []:
            if isinstance(fact, Mapping) and fact.get("agent_id") == agent_id:
                partner = fact.get("partner_id")
                if partner is not None:
                    history.append(str(partner))
        return history

    def _append_partner_history(self, agent_id: str, partner_id: str, round_number: int) -> None:
        self.state["conversation_partner_history"].append(
            {
                "agent_id": agent_id,
                "partner_id": partner_id,
                "round": round_number,
            }
        )

    def _append_active_message(self, receiver: str, message: Mapping[str, Any]) -> None:
        """更新 transient 收件箱，通过根级替换避免代理深入临时列表。"""
        active = self._to_plain(self.state.get("active_messages", {}))
        if not isinstance(active, dict):
            active = {}
        active.setdefault(receiver, []).append(self._to_plain(message))
        self.state["active_messages"] = active

    def _clear_round_messages(self, round_number: int) -> None:
        self._ensure_state_initialized()
        state = self._state_store()

        state["active_messages"] = {
            agent_id: [] for agent_id in self._group_index_by_agent.keys()
        }
        retention = state.setdefault("message_retention", {})
        retention[round_number] = {
            "round": round_number,
            "message_persistence": bool(self._config.message_persistence),
        }

    def _canonical_pair(self, agent1_id: str, agent2_id: str) -> Tuple[str, str]:
        return tuple(sorted((agent1_id, agent2_id)))

    def _to_plain(self, value: Any) -> Any:
        if isinstance(value, DictProxy):
            return {key: self._to_plain(val) for key, val in value.items()}
        if isinstance(value, ListProxy):
            return [self._to_plain(item) for item in value]
        if isinstance(value, Mapping):
            return {key: self._to_plain(val) for key, val in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self._to_plain(item) for item in value]
        return value

    def _validate_agents_in_same_group(self, agent1_id: str, agent2_id: str) -> None:
        if agent1_id not in self._group_index_by_agent:
            raise ValueError(f"Agent '{agent1_id}' 不属于任何已初始化小组。")
        if agent2_id not in self._group_index_by_agent:
            raise ValueError(f"Agent '{agent2_id}' 不属于任何已初始化小组。")
        if self._group_index_by_agent[agent1_id] != self._group_index_by_agent[agent2_id]:
            raise ValueError("两个 Agent 不同组，无法建立配对。")

    def _get_pairing_status(self) -> PairingStatus:
        self._ensure_state_initialized()
        current_partner = self._to_plain(self.state.get("pairing_current_partner", {}))
        active_pairs = self._to_plain(self.state.get("pairing_active_pairs", {}))
        completed = self._to_plain(self.state.get("pairing_completed_pairs", []))
        return PairingStatus(
            current_round=int(self.state.get("pairing_current_round", 0) or 0),
            total_rounds=int(self.state.get("pairing_total_rounds", 0) or 0),
            agent_partner=current_partner,
            completed_pairs=[tuple(pair) for pair in completed],
            pairing_schedule=[],
            round_active_pairs={int(key): [tuple(pair) for pair in pairs] for key, pairs in active_pairs.items()},
        )

    def _set_pairing_status(self, status: PairingStatus) -> None:
        self._ensure_state_initialized()
        self.state["pairing_current_round"] = status.current_round
        self.state["pairing_total_rounds"] = status.total_rounds
        current_partner = self.state.setdefault("pairing_current_partner", {})
        for agent_id in list(current_partner.keys()):
            if agent_id not in status.agent_partner:
                del current_partner[agent_id]
        for agent_id, partner_id in status.agent_partner.items():
            current_partner[agent_id] = partner_id
        self.state["pairing_active_pairs"] = {
            str(round_number): [list(pair) for pair in pairs]
            for round_number, pairs in status.round_active_pairs.items()
        }

    def _get_group_members(self, agent_id: str) -> List[str]:
        if agent_id not in self._group_index_by_agent:
            raise ValueError(f"Agent '{agent_id}' 不存在于任何小组。")
        group_idx = self._group_index_by_agent[agent_id]
        return self._groups[group_idx]

    # --- 能力定义（将在后续步骤实现） ---

    @rule(description="推进到下一轮并自动为所有组进行配对。\n\n使用说明：\n- 自动推进到指定轮次\n- 自动为所有组按计划进行配对\n- 不需要指定具体的agent参数\n- 返回配对结果摘要")
    async def advance_round_robin_with_pairing(
        self,
        env,  # 框架会自动注入环境实例作为第一个参数
        round_number: int,
    ) -> Dict[str, Any]:
        """推进到下一轮并自动为所有组进行配对

        使用说明：
        - 自动推进到指定轮次
        - 自动为所有组按计划进行配对
        - 不需要指定具体的agent参数
        - 返回配对结果摘要
        """
        self._ensure_state_initialized()

        # 获取配对状态
        pairing_status = self._get_pairing_status()
        if pairing_status.total_rounds == 0:
            return {"status": "error", "message": "尚未生成配对计划。"}

        if round_number <= 0:
            return {"status": "error", "message": "轮次编号必须大于 0。"}

        if round_number > pairing_status.total_rounds:
            return {"status": "error", "message": "指定轮次超出配对计划范围。"}

        # 推进到指定轮次
        old_round = pairing_status.current_round
        pairing_status.current_round = round_number

        # 清理旧的激活配对
        if old_round in pairing_status.round_active_pairs:
            pairing_status.round_active_pairs.pop(old_round, None)

        # 清理旧的 agent_partner 映射
        pairing_status.agent_partner.clear()

        # 自动为所有组进行配对
        pairing_results = []

        # 获取当前轮次的计划配对（按组收集）
        schedule_index = round_number - 1
        scheduled_pairs: List[Tuple[str, str]] = []
        for _group_idx, group_schedule in self._group_schedules.items():
            if schedule_index < len(group_schedule):
                scheduled_pairs.extend(group_schedule[schedule_index])

        for agent1_id, agent2_id in scheduled_pairs:
            try:
                canonical_target = self._canonical_pair(agent1_id, agent2_id)

                # 检查是否已完成
                completed_canonicals = {
                    self._canonical_pair(pair[0], pair[1]) for pair in pairing_status.completed_pairs
                }
                if canonical_target in completed_canonicals:
                    continue  # 跳过已完成的配对

                # 检查是否已激活
                active_pairs = pairing_status.round_active_pairs.get(round_number, [])
                active_canonicals = {
                    self._canonical_pair(pair[0], pair[1]) for pair in active_pairs
                }
                if canonical_target in active_canonicals:
                    continue  # 跳过已激活的配对

                # 更新配对状态
                pairing_status.agent_partner[agent1_id] = agent2_id
                pairing_status.agent_partner[agent2_id] = agent1_id
                pairing_status.round_active_pairs.setdefault(round_number, []).append(
                    (agent1_id, agent2_id)
                )
                pairing_status.completed_pairs.append(canonical_target)
                self.state["pairing_completed_pairs"].append(list(canonical_target))

                # 同步对话状态
                for agent_id, partner_id in ((agent1_id, agent2_id), (agent2_id, agent1_id)):
                    agent_state = self._get_agent_state(agent_id)
                    if partner_id not in agent_state.partner_history:
                        self._append_partner_history(agent_id, partner_id, round_number)
                    agent_state.current_partner = partner_id
                    agent_state.current_round = round_number
                    agent_state.can_converse = True
                    agent_state.is_conversation_active = True
                    self._set_agent_state(agent_id, agent_state)

                pairing_results.append({
                    "pair_id": f"{agent1_id}_{agent2_id}",
                    "agent1": agent1_id,
                    "agent2": agent2_id,
                    "status": "success"
                })

                logger.info(
                    "RoundRobin: 自动配对 (%s, %s) @ round %s",
                    agent1_id,
                    agent2_id,
                    round_number,
                )

            except Exception as e:
                pairing_results.append({
                    "pair_id": f"{agent1_id}_{agent2_id}",
                    "agent1": agent1_id,
                    "agent2": agent2_id,
                    "status": "error",
                    "error": str(e)
                })

        # 保存配对状态
        self._set_pairing_status(pairing_status)

        # 返回配对结果摘要
        successful_pairings = [r for r in pairing_results if r["status"] == "success"]
        failed_pairings = [r for r in pairing_results if r["status"] == "error"]

        return {
            "success": True,
            "message": f"第{round_number}轮配对完成",
            "round": round_number,
            "total_pairs": len(pairing_results),
            "successful_pairs": len(successful_pairings),
            "failed_pairs": len(failed_pairings),
            "pairing_details": pairing_results
        }

    @action(description="向当前配对伙伴发送消息。\n\n使用说明：\n- 只有当前有激活配对才能使用此能力\n- 消息会自动记录到轮次消息缓存中\n- 一次只能发送给当前配对的伙伴")
    async def send_message_to_partner(
        self,
        context: ExecutionContext,
        agent: "Agent",
        content: str,
    ) -> Dict[str, Any]:
        self._ensure_state_initialized()

        # 输入验证
        if not content or not content.strip():
            return {"status": "error", "message": "消息内容不能为空。"}

        # 标准方式：直接通过框架注入的agent参数获取agent_id
        agent_id = agent.id

        agent_state = self._get_agent_state(agent_id)
        if not agent_state.can_converse or not agent_state.current_partner:
            return {"status": "error", "message": "当前没有激活的配对会话。"}

        partner_id = agent_state.current_partner
        pairing_status = self._get_pairing_status()
        current_round = agent_state.current_round or pairing_status.current_round or 1

        timestamp = time.time()
        message_counter = self.state.get("message_counter", 0) + 1
        self.state["message_counter"] = message_counter

        message = ConversationMessage(
            sender=agent_id,
            receiver=partner_id,
            content=content,
            round=current_round,
            timestamp=timestamp,
        ).model_dump()

        self._append_active_message(partner_id, message)
        self.state["message_facts"].append(message)

        logger.debug(
            "RoundRobin: %s -> %s @ round %s | message_id=%s",
            agent_id,
            partner_id,
            current_round,
            message_counter,
        )

        return {
            "status": "success",
            "message_data": message,
            "sent_to": partner_id,
            "message_id": message_counter,
        }

    @action(description="向小组成员广播消息。\n\n使用说明：\n- 可用于需要组内沟通的实验场景\n- 消息会发送给同组的所有成员\n- 不依赖当前的配对状态")
    async def broadcast_to_group(
        self,
        context: ExecutionContext,
        agent: "Agent",
        content: str,
    ) -> Dict[str, Any]:
        self._ensure_state_initialized()

        # 输入验证
        if not content or not content.strip():
            return {"status": "error", "message": "广播消息内容不能为空。"}

        # 标准方式：直接通过框架注入的agent参数获取agent_id
        agent_id = agent.id

        agent_state = self._get_agent_state(agent_id)
        if not agent_state.can_converse:
            return {"status": "error", "message": "当前状态下不允许广播消息。"}

        group_members = self._get_group_members(agent_id)
        receivers = [member for member in group_members if member != agent_id]
        if not receivers:
            return {"status": "ignored", "message": "没有可广播的组员。"}

        pairing_status = self._get_pairing_status()
        current_round = agent_state.current_round or pairing_status.current_round or 1

        timestamp = time.time()
        delivered = []
        # 在循环外获取起始计数器
        message_counter = self.state.get("message_counter", 0)

        for receiver in receivers:
            message_counter += 1
            self.state["message_counter"] = message_counter
            message = ConversationMessage(
                sender=agent_id,
                receiver=receiver,
                content=content,
                round=current_round,
                timestamp=timestamp,
            ).model_dump()

            self._append_active_message(receiver, message)
            self.state["message_facts"].append(message)
            delivered.append({"receiver": receiver, "message_id": message_counter})

        logger.info(
            "RoundRobin: %s 广播消息给 %s 个组员。",
            agent_id,
            len(delivered),
        )

        return {
            "status": "success",
            "round": current_round,
            "delivered": delivered,
        }

    def get_agent_pairing_status(
        self,
        agent_id: str,
    ) -> Dict[str, Any]:
        self._ensure_state_initialized()

        pairing_status = self._get_pairing_status()
        agent_state = self._get_agent_state(agent_id)
        group_members = self._get_group_members(agent_id)

        remaining_partners = [
            member
            for member in group_members
            if member != agent_id
            and member not in agent_state.partner_history
            and member != agent_state.current_partner
        ]

        return {
            "status": "success",
            "agent_id": agent_id,
            "current_round": agent_state.current_round,
            "current_partner": agent_state.current_partner,
            "partner_history": list(agent_state.partner_history),
            "remaining_partners": remaining_partners,
            "can_converse": agent_state.can_converse,
            "is_conversation_active": agent_state.is_conversation_active,
            "round_active_pairs": pairing_status.round_active_pairs.get(
                agent_state.current_round or pairing_status.current_round, []
            ),
        }

    @behavior(description="为对话实验设置单个 Agent 的确定性参与标记。\n\n适合 rule-based baseline、协议测试或在 code step 中准备对话状态。")
    async def mark_conversation_participant(
        self,
        agent: "Agent",
        env,
        marker: str = "ready",
    ) -> Dict[str, Any]:
        self._ensure_state_initialized()
        agent.state["conversation_marker"] = marker
        agent_state = self._get_agent_state(agent.id)
        return {
            "status": "marked",
            "agent_id": agent.id,
            "marker": marker,
            "current_round": agent_state.current_round,
            "current_partner": agent_state.current_partner,
            "can_converse": agent_state.can_converse,
        }

    @rule(description="推进到下一轮 round-robin 对话。\n\n使用说明：\n- 每轮对话开始前必须调用此函数\n- 会清空上一轮的所有消息缓存\n- 重置所有Agent的配对状态")
    async def advance_round_robin(
        self,
        env,  # 框架会自动注入环境实例作为第一个参数
    ) -> Dict[str, Any]:
        self._ensure_state_initialized()

        pairing_status = self._get_pairing_status()
        if pairing_status.total_rounds == 0:
            return {"status": "idle", "message": "没有可推进的轮次。"}

        current_round = pairing_status.current_round
        if current_round >= pairing_status.total_rounds:
            return {"status": "completed", "message": "所有轮次已完成。"}

        new_round = current_round + 1
        pairing_status.current_round = new_round
        pairing_status.round_active_pairs.pop(current_round, None)
        pairing_status.round_active_pairs.setdefault(new_round, [])

        # 重置 agent_partner
        pairing_status.agent_partner = {
            agent_id: None for agent_id in pairing_status.agent_partner.keys()
        }
        self._set_pairing_status(pairing_status)

        # 重置对话状态
        for agent_id in list(self._group_index_by_agent.keys()):
            agent_state = self._get_agent_state(agent_id)
            agent_state.current_partner = None
            agent_state.can_converse = False
            agent_state.is_conversation_active = False
            agent_state.current_round = new_round
            self._set_agent_state(agent_id, agent_state)

        self._clear_round_messages(new_round)

        logger.info("RoundRobin: 推进到第 %s 轮。", new_round)

        # 返回分组形式的当轮配对
        grouped_pairs: Dict[int, List[Tuple[str, str]]] = {}
        idx = new_round - 1
        for group_idx, group_schedule in self._group_schedules.items():
            if idx < len(group_schedule):
                grouped_pairs[group_idx] = group_schedule[idx]

        return {
            "status": "advanced",
            "new_round": new_round,
            "pairings_available": grouped_pairs,
        }

    @rule(description="为指定轮次初始化消息存储。\n\n使用说明：\n- 通常在 advance_round_robin 后自动调用\n- 为新轮次创建消息存储空间\n- 如果轮次已存在则跳过初始化")
    async def initialize_round_messages(
        self,
        env,  # 框架会自动注入环境实例作为第一个参数
        round_number: int,
    ) -> Dict[str, Any]:
        if round_number <= 0:
            return {"status": "error", "message": "轮次编号必须大于 0。"}

        self._clear_round_messages(round_number)
        logger.debug("RoundRobin: 初始化第 %s 轮消息缓存。", round_number)
        return {"status": "initialized", "round": round_number}

    @fov(description="获取当前对话上下文视野。\n\n使用说明：\n- 为Agent提供完整的对话上下文信息\n- 包含当前伙伴、历史消息、轮次进度等\n- 是Agent进行对话决策的主要信息来源")
    async def get_conversation_fov(
        self,
        agent,
        env,
    ) -> str:
        agent_id = agent.id
        self._ensure_state_initialized()

        agent_state = self._get_agent_state(agent_id)
        pairing_status = self._get_pairing_status()
        current_round = agent_state.current_round or pairing_status.current_round or 1

        lines: List[str] = []
        lines.append("=== 对话环境信息 ===")
        lines.append(f"你当前在第 {current_round} 轮对话中。")
        lines.append(f"总共有 {pairing_status.total_rounds} 轮对话。")

        if agent_state.current_partner:
            lines.append(f"当前的对话伙伴：{agent_state.current_partner}")
            if agent_state.partner_history:
                lines.append(
                    "历史对话伙伴：" + ", ".join(agent_state.partner_history)
                )
        else:
            lines.append("当前没有激活的配对，请等待调度启动会话。")

        active_messages = self.state.get("active_messages", {})
        received_messages = active_messages.get(agent_id, [])
        if received_messages:
            lines.append("")
            lines.append("=== 最近收到的消息 ===")
            for idx, msg in enumerate(received_messages, start=1):
                sender = msg.get("sender")
                content = msg.get("content")
                lines.append(f"{idx}. 来自 {sender}: {content}")
        else:
            lines.append("")
            lines.append("=== 消息记录 ===")
            lines.append("在本轮尚未收到任何消息。")

        lines.append("")
        lines.append("=== 可执行的动作 ===")
        if agent_state.can_converse and agent_state.current_partner:
            lines.append("- 使用 send_message_to_partner 向当前伙伴发送消息。")
            lines.append("- 使用 broadcast_to_group 向小组成员广播消息。")
            lines.append(
                f"- 建议对话时长约 {self._config.session_duration_minutes} 分钟。"
            )
        else:
            lines.append("- 等待实验调度开始你的配对会话。")

        lines.append("")
        lines.append("=== 进度信息 ===")
        completed_rounds = max(0, current_round - 1)
        remaining_rounds = max(0, pairing_status.total_rounds - current_round)
        lines.append(f"已完成 {completed_rounds} 轮，对应剩余 {remaining_rounds} 轮。")

        return "\n".join(lines)

    @fov(description="获取小组层面信息。\n\n使用说明：\n- 提供小组整体层面的视角和状态\n- 包含成员列表、配对历史、即将配对的信息\n- 有助于Agent了解在整个小组中的位置")
    async def get_group_fov(
        self,
        agent,
        env,
    ) -> str:
        agent_id = agent.id
        self._ensure_state_initialized()

        agent_state = self._get_agent_state(agent_id)
        pairing_status = self._get_pairing_status()
        group_members = self._get_group_members(agent_id)

        lines: List[str] = []
        lines.append("=== 小组概览 ===")
        lines.append(f"小组成员：{', '.join(group_members)}")
        lines.append(f"当前轮次：{pairing_status.current_round}")

        if agent_state.partner_history:
            lines.append("已配对过的成员：" + ", ".join(agent_state.partner_history))
        else:
            lines.append("尚未与任何成员完成配对。")

        upcoming_partners: List[str] = []
        # 仅基于本组的配对计划推导未来配对
        group_idx = self._group_index_by_agent.get(agent_id)
        group_schedule = self._group_schedules.get(group_idx, []) if group_idx is not None else []
        start_round = agent_state.current_round or pairing_status.current_round or 1
        for idx, pairs in enumerate(group_schedule, start=1):
            if idx < start_round:
                continue
            for a1, a2 in pairs:
                if agent_id == a1 or agent_id == a2:
                    partner = a2 if agent_id == a1 else a1
                    upcoming_partners.append(f"第 {idx} 轮：{partner}")

        if upcoming_partners:
            lines.append("")
            lines.append("=== 即将配对的成员 ===")
            lines.extend(upcoming_partners)

        return "\n".join(lines)
