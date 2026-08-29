"""
Agent核心类定义

实现了统一状态架构下的Agent类层次结构，包括：
- Agent基类：支持代理机制的智能Agent容器
- 集成了代理状态访问、依赖注入和事务管理

v3.0 新增功能：
- get_llm_visible_state: 获取 LLM 推理时可见的状态字段
- 支持基于 schema 的权限控制
"""

from typing import Dict, Any, List, Optional, TYPE_CHECKING, Callable, Awaitable, Union
import logging
import copy
import asyncio
import json
import threading
import time

# Import proxy system for state management
from ..state_proxy import DictProxy, ListProxy, AccessContext
from ..async_utils import invoke_maybe_async
from ..logging import AgentEvent, LogField, summarize_text
from .memory_extraction import (
    extract_memories_from_thread,
)

if TYPE_CHECKING:
    from .memory import Memory
    from .agent_loop import ActionSet
    from ..core_data import World

logger = logging.getLogger(__name__)


def _debug_print(*values: Any, sep: str = " ", end: str = "\n", file: Any = None, flush: bool = False) -> None:
    """Route legacy debug prints through logging without writing to stdout."""
    if file is not None:
        import builtins

        builtins.print(*values, sep=sep, end=end, file=file, flush=flush)
        return

    message = sep.join(str(value) for value in values)
    if end and end != "\n":
        message += end
    logger.debug(message)


print = _debug_print


def _summarize_action_arguments(arguments: Dict[str, Any], *, text_limit: int = 120, sample_limit: int = 10) -> Dict[str, Any]:
    """Return monitor-friendly action arguments without storing large payloads."""
    compact: Dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        if isinstance(value, str):
            summary = summarize_text(value, limit=text_limit)
            compact[key] = summary["preview"]
            compact[f"{key}_length"] = summary["length"]
            compact[f"{key}_truncated"] = summary["truncated"]
        elif isinstance(value, (list, tuple, set)):
            values = list(value)
            compact[key] = values[:sample_limit]
            compact[f"{key}_count"] = len(values)
            compact[f"{key}_sampled"] = len(values) > sample_limit
        elif isinstance(value, dict):
            compact[key] = {
                "type": "dict",
                "length": len(value),
                "keys_sample": [str(item_key) for item_key in list(value.keys())[:sample_limit]],
            }
        else:
            compact[key] = copy.deepcopy(value)
    return compact


def _sanitize_llm_request_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep user-facing generation options from overriding runtime-owned fields."""
    if not options:
        return {}
    forbidden = {"messages", "tools", "tool_choice", "metadata", "agent_id", "model"}
    return {
        str(key): value
        for key, value in dict(options).items()
        if key not in forbidden and value is not None
    }


def _with_thread_session_id(options: Dict[str, Any], thread_id: Optional[str]) -> Dict[str, Any]:
    """Attach the provider KV-cache session to one Agent Thread's action loop."""
    if thread_id is None:
        return options
    request_options = dict(options)
    extra_body = dict(request_options.get("extra_body") or {})
    metadata = dict(extra_body.get("metadata") or {})
    metadata["session_id"] = thread_id
    extra_body["metadata"] = metadata
    request_options["extra_body"] = extra_body
    return request_options


def _format_agent_exception(exc: BaseException) -> str:
    """Return a non-empty, audit-friendly description of an agent error.

    Some timeout implementations have an empty ``str(exc)``.  Keep the
    exception type/repr in that case and include lightweight request/timeout
    context when the provider attached those attributes.
    """

    exception_type = type(exc).__name__
    detail = str(exc).strip()
    message = f"{exception_type}: {detail}" if detail else repr(exc)
    if not message:
        message = exception_type

    context_parts = []
    for attribute in ("request", "timeout"):
        try:
            value = getattr(exc, attribute)
        except Exception:
            continue
        if value is None:
            continue
        if attribute == "request":
            if isinstance(value, dict):
                method = value.get("method")
                url = value.get("url")
            else:
                method = getattr(value, "method", None)
                url = getattr(value, "url", None)
            if method is not None or url is not None:
                value_text = " ".join(
                    str(part) for part in (method, url) if part is not None
                )
            elif isinstance(value, dict):
                keys = sorted(str(key) for key in value.keys())[:10]
                value_text = f"dict(keys={keys})"
            else:
                value_text = f"<{type(value).__name__}>"
        else:
            value_text = summarize_text(repr(value), limit=120)["preview"]
        context_parts.append(f"{attribute}={value_text}")
    if context_parts:
        message = f"{message} ({', '.join(context_parts)})"
    return message


def _parse_structured_json_from_model_text(content: str, *, assume_prefilled_object: bool = False) -> Any:
    """Parse JSON from model text, including continuations after a prefilled ``{``."""
    import json_repair

    text = (content or "").strip()
    if not text:
        return None

    def strip_fence(value: str) -> str:
        stripped = value.strip()
        if stripped.startswith("```"):
            stripped = stripped[3:].lstrip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].lstrip()
        if stripped.endswith("```"):
            stripped = stripped[:-3].rstrip()
        return stripped.strip()

    normalized = strip_fence(text)
    candidates: List[str] = []

    if "{" in normalized and "}" in normalized:
        candidates.append(normalized[normalized.find("{") : normalized.rfind("}") + 1])

    if assume_prefilled_object and "}" in normalized:
        completed = "{" + normalized.lstrip()
        candidates.append(completed[completed.find("{") : completed.rfind("}") + 1])

    candidates.append(normalized)

    seen = set()
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            parsed = json_repair.loads(candidate)
        except Exception:
            continue
        if parsed is not None:
            return parsed
    return None


def _validate_structured_output_against_schema(data: Any, schema: Dict[str, Any]) -> bool:
    """Validate structured model output against a JSON Schema."""
    try:
        from jsonschema import Draft202012Validator, validate

        Draft202012Validator.check_schema(schema)
        validate(instance=data, schema=schema)
        return True
    except Exception:
        return False


class Agent:
    """
    Agent基类：支持代理机制的智能Agent容器

    新的统一架构下，Agent不再直接持有状态数据，而是通过World获取
    代理对象来访问状态。这样做的好处：
    1. 所有状态修改都被自动记录
    2. 支持事务机制和回滚
    3. 实现完整的状态变更追踪

    Agent提供的接口：
    - state: 返回状态代理对象（DictProxy）
    - properties: 返回属性代理对象（DictProxy）
    - reminders: 返回提醒列表（暂时不代理）
    - memory: 返回记忆系统对象
    """

    def __init__(self, agent_id: str, world: 'World'):
        """
        初始化Agent

        Args:
            agent_id: Agent的唯一标识符
            world: World对象引用，用于获取真实数据和创建代理
        """
        self._id = agent_id
        self._world = world

        # 验证agent数据存在
        if agent_id not in world.agents_data:
            raise KeyError(f"Agent '{agent_id}' not found in world data")

        logger.debug(f"Created Agent proxy for '{agent_id}'")

    @property
    def id(self) -> str:
        """Agent ID（只读）"""
        return self._id

    @property
    def type(self) -> str:
        """Agent的社会类型"""
        return self._world.agents_data[self._id]["type"]

    @property
    def archetype(self) -> str:
        """Agent的执行架构类型"""
        return self._world.agents_data[self._id]["archetype"]

    @property
    def state(self) -> DictProxy:
        """
        获取Agent状态的代理对象

        Returns:
            DictProxy对象，所有修改都会被自动记录
        """
        return self._world.create_agent_state_proxy(self._id, "state")

    @property
    def properties(self) -> DictProxy:
        """
        获取Agent属性的代理对象

        Returns:
            DictProxy对象，所有修改都会被自动记录
        """
        return self._world.create_agent_state_proxy(self._id, "properties")

    @property
    def reminders(self) -> Union[ListProxy, List[str]]:
        """返回 Tick 临时提醒队列的受控代理。"""

        creator = getattr(self._world, "create_agent_state_proxy", None)
        if creator is None:
            # 轻量单元测试会注入只读 FakeWorld；生产 World 始终提供代理工厂。
            return self._world.agents_data[self._id]["reminders"]
        proxy = creator(self._id, "reminders")
        if not isinstance(proxy, (ListProxy, list, tuple)) and not hasattr(proxy, "append"):
            raise TypeError("Agent reminders must be a list")
        return proxy

    def add_reminder(self, reminder: str):
        """添加提醒"""
        mode = getattr(self._world, "state_access_mode", None)
        if getattr(mode, "value", mode) == "explicit_transactions":
            with self._world.write_agent_transaction(self._id, "reminders") as tx:
                tx.state.append(reminder)
            return
        self.reminders.append(reminder)

    def clear_reminders(self):
        """清空提醒"""
        mode = getattr(self._world, "state_access_mode", None)
        if getattr(mode, "value", mode) == "explicit_transactions":
            with self._world.write_agent_transaction(self._id, "reminders") as tx:
                tx.state.clear()
            return
        self.reminders.clear()

    def write_transaction(
        self,
        state_key: str = "state",
        *,
        caller_type: str = "system",
        caller_id: Optional[str] = None,
    ):
        """开启 Agent 状态写事务，可携带行为/动作字段权限上下文。"""

        access_context = None
        if caller_type != "system":
            merged_schema = self._world.get_merged_agent_state_schema(self.type)
            access_context = AccessContext(
                caller_type=caller_type,
                caller_id=caller_id or caller_type,
                state_schema=merged_schema,
            )
        return self._world.write_agent_transaction(
            self._id,
            state_key,
            access_context=access_context,
        )

    def get_raw_data(self) -> Dict[str, Any]:
        """
        获取原始数据（仅用于调试和特殊情况）

        Returns:
            Agent的原始数据字典
        """
        return copy.deepcopy(self._world.agents_data[self._id])

    # =========================================================================
    # v3.0: 新增方法 - 权限控制和可见性过滤
    # =========================================================================

    def get_state_for_context(self, caller_type: str, caller_id: str) -> DictProxy:
        """
        为特定执行上下文提供受限的 state 访问

        这个方法由 World 或 SimEngine 在执行 behavior/action 时调用，
        创建一个带权限控制的 DictProxy。

        Args:
            caller_type: 调用者类型 ('agent_behavior', 'agent_action', 'env_rule', etc.)
            caller_id: 调用者标识符（behavior/action/rule 的名称）

        Returns:
            带权限控制的 DictProxy
        """
        # 获取合并后的 state schema
        merged_schema = self._world.get_merged_agent_state_schema(self.type)

        # 创建访问上下文
        access_context = AccessContext(
            caller_type=caller_type,
            caller_id=caller_id,
            state_schema=merged_schema
        )

        if getattr(getattr(self._world, "state_access_mode", None), "value", None) == "explicit_transactions":
            return self._world.create_agent_state_view(self._id, "state")

        # 创建带权限控制的 DictProxy
        return DictProxy(
            target_dict=self._world.agents_data[self._id]["state"],
            event_recorder=self._world._create_event_recorder(),
            context_provider=self._world._create_context_provider(),
            path=("agents", self._id, "state"),
            access_context=access_context,
            # Context-restricted Agent views must use the same journal and
            # lease as the ordinary ``Agent.state`` proxy.  Otherwise an
            # action/behavior can mutate canonical state without entering the
            # v4 delta, or keep writing after the Tick has been sealed.
            persistence_journal=self._world._state_delta_journal,
            lease=self._world._persistence_proxy_lease,
        )

    def get_llm_visible_state(self) -> Dict[str, Any]:
        """
        获取 LLM 推理时可见的状态字段

        根据 state schema 中的 agent_visible 字段过滤状态，
        只返回那些 agent_visible=True 的字段。

        这个方法用于构建 LLM 的提示词。

        Returns:
            过滤后的状态字典（只包含可见字段）
        """
        # 获取合并后的 state schema
        merged_schema = self._world.get_merged_agent_state_schema(self.type)

        # 过滤出 agent_visible=True 的字段
        visible_state = {}
        properties = merged_schema.get('properties', {})
        current_state = self._world.agents_data[self._id].get("state", {})

        for field_name, field_schema in properties.items():
            # 默认可见（向后兼容）
            is_visible = field_schema.get('agent_visible', True)

            if is_visible and field_name in current_state:
                visible_state[field_name] = current_state[field_name]

        return visible_state

    def get_state_schema_info(self) -> Dict[str, Any]:
        """
        获取当前 Agent 的 state schema 信息（用于调试和文档生成）

        Returns:
            包含 schema 信息的字典，包括每个字段的访问控制元数据
        """
        merged_schema = self._world.get_merged_agent_state_schema(self.type)

        # 格式化为更友好的结构
        fields_info = {}
        properties = merged_schema.get('properties', {})

        for field_name, field_schema in properties.items():
            fields_info[field_name] = {
                "type": field_schema.get("type", "unknown"),
                "description": field_schema.get("description", ""),
                "agent_visible": field_schema.get("agent_visible", True),
                "agent_editable": field_schema.get("agent_editable", True),
                "env_managed": field_schema.get("env_managed", False),
                "current_value": self._world.agents_data[self._id].get("state", {}).get(field_name)
            }

        return {
            "agent_type": self.type,
            "fields": fields_info,
            "total_fields": len(fields_info),
            "visible_fields": sum(1 for f in fields_info.values() if f["agent_visible"]),
            "editable_fields": sum(1 for f in fields_info.values() if f["agent_editable"]),
            "env_managed_fields": sum(1 for f in fields_info.values() if f["env_managed"])
        }

    async def call_behavior(self, behavior_name: str, **kwargs) -> Any:
        """在当前 Agent 的上下文中，调用一个注册在案的 behavior。

        这为实现 Agent 行为的连锁反应提供了基础。
        注意：为避免无限递归，需在 SimEngine 或调用层设计熔断机制。

        Args:
            behavior_name: 要调用的 behavior 名称
            **kwargs: 传递给 behavior 的额外参数

        Returns:
            behavior 执行的返回值

        Raises:
            ValueError: 如果 behavior 未找到
            RuntimeError: 如果 logic provider 未初始化
        """
        # 1. 通过 self._world 获取 LogicProvider (FunctionRegistry)
        logic_provider = self._world.get_logic_provider()

        # 2. 查找 behavior 函数
        if behavior_name not in logic_provider.behaviors:
            raise ValueError(f"Behavior '{behavior_name}' not found in registry")

        behavior_info = logic_provider.behaviors[behavior_name]
        behavior_func = behavior_info['function']

        # 3. 准备参数并调用
        # 核心：将 self (Agent 实例) 和当前 env 作为约定参数传入
        current_env = self._world.get_environment()

        logger.debug(f"Agent {self.id} calling behavior '{behavior_name}' with kwargs: {list(kwargs.keys())}")

        return await invoke_maybe_async(behavior_func, self, current_env, **kwargs)

    async def call_action(self, action_name: str, **kwargs) -> Any:
        """在当前 Agent 的上下文中，调用一个注册在案的 action。

        Args:
            action_name: 要调用的 action 名称
            **kwargs: 传递给 action 的额外参数

        Returns:
            action 执行的返回值

        Raises:
            ValueError: 如果 action 未找到
            RuntimeError: 如果 logic provider 未初始化
        """
        # 1. 通过 self._world 获取 LogicProvider (FunctionRegistry)
        logic_provider = self._world.get_logic_provider()

        # 2. 查找 action 函数
        # 注意: actions 可能在 agent_actions 或 env_actions 中
        action_func = None
        action_info = None

        if action_name in logic_provider.agent_actions:
            action_info = logic_provider.agent_actions[action_name]
            action_func = action_info['function']
        elif hasattr(logic_provider, 'env_actions') and action_name in logic_provider.env_actions:
            action_info = logic_provider.env_actions[action_name]
            action_func = action_info['function']

        if not action_func:
            raise ValueError(f"Action '{action_name}' not found in registry")

        # 3. 准备参数并调用
        # 核心：将 self (Agent 实例) 和当前 env 作为约定参数传入
        current_env = self._world.get_environment()

        logger.debug(f"Agent {self.id} calling action '{action_name}' with kwargs: {list(kwargs.keys())}")

        return await invoke_maybe_async(action_func, self, current_env, **kwargs)

    def __repr__(self) -> str:
        return f"Agent(id='{self.id}', type='{self.type}', archetype='{self.archetype}')"

    def __str__(self) -> str:
        return f"Agent({self.id})"


class LLMAgent(Agent):
    """
    基于LLM的智能Agent，具有完整认知架构

    扩展基础Agent以支持：
    - 记忆系统
    - 指令执行
    - 动作循环
    - 认知架构集成
    """

    # 同一个运行世界中的同一 Thread/key 只允许一个提炼提交进入
    # pending -> vector upsert -> receipt 流程。Future 让并发调用者复用
    # 首次调用的最终结果，而不同 Thread 或 key 仍可并行执行。
    _memory_extraction_flights: Dict[
        tuple[int, int, str, str, str], asyncio.Future
    ] = {}
    _memory_extraction_flights_guard = threading.Lock()

    def __init__(self, agent_id: str, world: 'World'):
        """
        初始化LLMAgent

        Args:
            agent_id: Agent的唯一标识符
            world: World对象引用
        """
        super().__init__(agent_id, world)

        # 验证这是一个LLM agent
        if self.archetype != "llm":
            logger.warning(f"Creating LLMAgent for non-LLM archetype: {self.archetype}")

        # 认知组件（延迟初始化）
        self._memory: Optional['Memory'] = None
        self._persona: Optional[str] = None
        self._persona_type: str = ""
        self._llm_call: Optional[Callable] = None
        self._actionset: Optional['ActionSet'] = None
        self._default_reasoning_stages: Optional[List[Dict[str, Any]]] = None

        logger.debug(f"Created LLMAgent for '{agent_id}'")

    @property
    def memory(self) -> 'Memory':
        """
        获取记忆系统（延迟初始化）

        Returns:
            Memory对象
        """
        if self._memory is None:
            # 通过World获取或创建Memory
            # 这里需要依赖注入机制
            raise NotImplementedError("Memory system integration pending")

        return self._memory

    def initialize_cognitive_system(self,
                                  persona: str,
                                  memory: 'Memory',
                                  llm_call: Callable,
                                  actionset: Optional['ActionSet'] = None,
                                  reasoning_stages: Optional[List[Dict[str, Any]]] = None,
                                  *,
                                  type_persona: str = ""):
        """
        初始化认知系统

        Args:
            persona: Agent的人格设定字符串
            memory: 记忆系统对象
            llm_call: LLM调用函数
            actionset: 动作集合（可选）
            reasoning_stages: 默认推理阶段配置（可选）
        """
        self._persona = persona  # 实例级 persona
        self._persona_type = type_persona  # 类型级 persona
        self._memory = memory
        self._llm_call = llm_call
        self._actionset = actionset
        self._default_reasoning_stages = reasoning_stages

        logger.debug(f"Initialized cognitive system for Agent '{self.id}'")

    def _agent_thread_log_context(self):
        get_log_context = getattr(self._world, "get_log_context", None)
        return get_log_context() if callable(get_log_context) else None

    def _read_agent_thread_messages(self, thread_id: str) -> List[Dict[str, Any]]:
        log_context = self._agent_thread_log_context()
        read_messages = getattr(log_context, "read_agent_thread_messages", None)
        if not callable(read_messages):
            return []
        messages = read_messages(thread_id)
        if not isinstance(messages, list):
            raise RuntimeError("read_agent_thread_messages() must return a list")
        return messages

    def _read_agent_thread_events(self, thread_id: str) -> List[Dict[str, Any]]:
        """读取 Thread 事件；旧的测试替身没有事件接口时返回空列表。"""

        log_context = self._agent_thread_log_context()
        read_events = getattr(log_context, "read_agent_thread_events", None)
        if not callable(read_events):
            return []
        try:
            events = read_events(thread_id, materialize_payloads=True)
        except TypeError:
            # 兼容只接受 thread_id 的轻量测试替身。
            events = read_events(thread_id)
        if not isinstance(events, list):
            raise RuntimeError("read_agent_thread_events() must return a list")
        return events

    @staticmethod
    def _thread_memory_commit_state(
        events: List[Dict[str, Any]],
        idempotency_key: str,
    ) -> tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
        """返回同一 key 的 pending intent 和最终 receipt。"""

        pending: Dict[str, Any] | None = None
        receipt: Dict[str, Any] | None = None
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("event_type") not in {
                "memory_extraction_pending",
                "memory_extraction_receipt",
            }:
                continue
            payload = event.get("payload")
            if not isinstance(payload, dict):
                continue
            if str(payload.get("idempotency_key") or "") != idempotency_key:
                continue
            if event.get("event_type") == "memory_extraction_pending":
                pending = payload
            else:
                receipt = payload
        return pending, receipt

    @staticmethod
    def _memory_commit_key(thread_id: str, idempotency_key: str | None) -> str:
        """没有显式 key 时仍为该 Thread 生成可重试的稳定 key。"""

        if idempotency_key is None:
            return f"thread:{thread_id}:memory_extract"
        normalized = str(idempotency_key).strip()
        if not normalized:
            raise ValueError("idempotency_key must be a non-empty string")
        return normalized

    def _append_agent_thread_message(
        self,
        thread_id: str | None,
        message: Dict[str, Any],
        *,
        interaction_type: str,
        interaction_name: str,
    ) -> None:
        if not thread_id:
            return
        self._append_agent_thread_event(
            thread_id,
            "conversation_message",
            message,
            interaction_type=interaction_type,
            interaction_name=interaction_name,
        )

    def _append_agent_thread_event(
        self,
        thread_id: str | None,
        event_type: str,
        payload: Dict[str, Any],
        *,
        interaction_type: str,
        interaction_name: str,
    ) -> None:
        """Append one complete structured event to the current Agent Thread."""

        if not thread_id:
            return
        log_context = self._agent_thread_log_context()
        append_event = getattr(log_context, "append_agent_thread_event", None)
        if callable(append_event):
            append_event(
                thread_id,
                event_type,
                payload=copy.deepcopy(payload),
                interaction_type=interaction_type,
                interaction_name=interaction_name,
            )

    def _agent_thread_reference(self, thread_id: str | None) -> Dict[str, Any] | None:
        if not thread_id:
            return None
        log_context = self._agent_thread_log_context()
        get_reference = getattr(log_context, "get_agent_thread_reference", None)
        if not callable(get_reference):
            return {"thread_id": thread_id, "closed": False}
        reference = get_reference(thread_id, require_closed=False)
        return copy.deepcopy(reference) if isinstance(reference, dict) else reference

    async def extract_memories_from_thread(
        self,
        *,
        thread_id: str,
        timestamp: int,
        idempotency_key: str | None = None,
        metadata: Dict[str, Any] | None = None,
        interaction_name: str = "memory_extract",
    ) -> Dict[str, Any]:
        """并发安全地提交一次 Thread 记忆提炼。"""

        normalized_key = self._memory_commit_key(thread_id, idempotency_key)
        # Future 只在创建它的事件循环内复用；跨 loop 的调用各自读取
        # durable pending/receipt，避免触发 asyncio 的跨 loop 绑定错误。
        loop = asyncio.get_running_loop()
        flight_key = (
            id(loop),
            id(self._world),
            self.id,
            str(thread_id),
            normalized_key,
        )
        with self._memory_extraction_flights_guard:
            flight = self._memory_extraction_flights.get(flight_key)
            if flight is None:
                flight = loop.create_future()
                self._memory_extraction_flights[flight_key] = flight
                owner = True
            else:
                owner = False

        if not owner:
            # shield 防止某个等待者取消时连带取消正在进行的提交。
            result = await asyncio.shield(flight)
            return copy.deepcopy(result)

        try:
            result = await self._extract_memories_from_thread_once(
                thread_id=thread_id,
                timestamp=timestamp,
                idempotency_key=normalized_key,
                metadata=metadata,
                interaction_name=interaction_name,
            )
        except asyncio.CancelledError:
            if not flight.done():
                flight.cancel()
            raise
        except BaseException as exc:
            if not flight.done():
                flight.set_exception(exc)
                # 无等待者时也消费异常，避免事件循环报
                # ``Future exception was never retrieved``。
                flight.exception()
            raise
        else:
            if not flight.done():
                flight.set_result(copy.deepcopy(result))
            return result
        finally:
            with self._memory_extraction_flights_guard:
                if self._memory_extraction_flights.get(flight_key) is flight:
                    self._memory_extraction_flights.pop(flight_key, None)

    async def _extract_memories_from_thread_once(
        self,
        *,
        thread_id: str,
        timestamp: int,
        idempotency_key: str | None = None,
        metadata: Dict[str, Any] | None = None,
        interaction_name: str = "memory_extract",
    ) -> Dict[str, Any]:
        """在 Agent 自己的完整 Thread 末尾追加记忆提炼回合。

        记忆向量库与 Thread 文件不是同一个事务资源，因此这里采用一个
        可恢复的两阶段协议：先把带稳定 key 的 pending intent 写入 Thread，
        再用稳定 memory id 做 upsert，最后写入 receipt。进程若在任一步骤
        中断，下一次同 key 调用会从 Thread 事件恢复，不会重新调用模型或
        产生第二组记忆。
        """

        if self._llm_call is None:
            raise RuntimeError(f"Agent {self.id} 未初始化 LLM 调用")
        if self._memory is None:
            raise RuntimeError(f"Agent {self.id} 未初始化 Memory")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int) or timestamp < 0:
            raise ValueError("timestamp must be a non-negative integer")

        normalized_key = self._memory_commit_key(thread_id, idempotency_key)
        thread_events = self._read_agent_thread_events(thread_id)
        pending_payload, receipt_payload = self._thread_memory_commit_state(
            thread_events,
            normalized_key,
        )
        if receipt_payload is not None:
            if pending_payload is None:
                raise RuntimeError("memory extraction receipt is missing its pending intent")
            receipt_payload = {
                **pending_payload,
                **receipt_payload,
            }

        thread_ref = self._agent_thread_reference(thread_id)
        if isinstance(thread_ref, dict):
            owner = thread_ref.get("agent_id")
            if owner is not None and str(owner) != self.id:
                raise ValueError(
                    f"Agent Thread {thread_id} belongs to {owner}, not {self.id}"
                )
            # 已有 receipt 代表记忆提交已经完成；即使 close 在上一次调用
            # 中已经成功但调用方未拿到返回值，也必须允许同 key 重试确认。
            if thread_ref.get("closed") is True and receipt_payload is None:
                raise ValueError(f"Agent Thread {thread_id} is already closed")

        async def finish_from_commit(
            *,
            payload: Dict[str, Any],
            write_memory: bool,
        ) -> Dict[str, Any]:
            """从 pending/receipt 恢复一次提交，不重新走 LLM 提炼。"""

            entries = payload.get("entries") or []
            memories = payload.get("memories") or []
            memory_ids = [str(item) for item in (payload.get("memory_ids") or [])]
            if not isinstance(entries, list) or not isinstance(memories, list):
                raise RuntimeError("memory extraction commit payload is invalid")
            if write_memory and entries:
                entry_ids = [str(item.get("memory_id") or "") for item in entries]
                if any(not memory_id for memory_id in entry_ids):
                    raise RuntimeError("memory extraction commit entries missing memory_id")
                if len(set(entry_ids)) != len(entry_ids):
                    raise RuntimeError("memory extraction commit entries contain duplicate memory_id")
                if memory_ids and memory_ids != entry_ids:
                    raise RuntimeError("memory extraction commit memory_ids do not match entries")
                memory_ids = entry_ids

                # Receipt 可能在写入前失败；pending 中的稳定 ID 不能直接
                # 再次 upsert。Memory 层读回真实 payload 后，只补缺失项，
                # 对同 ID 的内容冲突采取硬失败，避免覆盖未知事实。
                inspect_memory_ids = getattr(self._memory, "inspect_memory_ids", None)
                if callable(inspect_memory_ids):
                    state = await inspect_memory_ids(
                        memory_ids,
                        entries=copy.deepcopy(entries),
                    )
                    if not isinstance(state, dict):
                        raise RuntimeError("memory ID inspection returned invalid state")
                    mismatched_ids = [
                        str(item) for item in (state.get("mismatched_ids") or [])
                    ]
                    if mismatched_ids:
                        raise RuntimeError(
                            "memory extraction commit payload mismatch for IDs: "
                            + ", ".join(mismatched_ids)
                        )
                    missing_ids = {
                        str(item) for item in (state.get("missing_ids") or [])
                    }
                    unknown_missing_ids = missing_ids.difference(memory_ids)
                    if unknown_missing_ids:
                        raise RuntimeError("memory ID inspection returned unknown missing IDs")
                    entries_to_write = [
                        entry for entry in entries if str(entry["memory_id"]) in missing_ids
                    ]
                else:
                    # 轻量测试替身或旧扩展没有检查 API 时保持原有写入
                    # 语义；生产 Memory 始终提供 inspect_memory_ids。
                    entries_to_write = copy.deepcopy(entries)

                if entries_to_write:
                    added_ids = await self._memory.add_memories_batch(
                        copy.deepcopy(entries_to_write),
                        fire_and_forget=False,
                        trace={
                            "step": int(payload.get("timestamp", timestamp)),
                            "step_name": getattr(
                                self._world, "_current_code_step_name", None
                            ),
                            "interaction_type": "memory_write",
                            "interaction_name": interaction_name,
                            "thread_id": thread_id,
                            "idempotency_key": normalized_key,
                        },
                    )
                    expected_added_ids = [str(entry["memory_id"]) for entry in entries_to_write]
                    if [str(item) for item in (added_ids or [])] != expected_added_ids:
                        raise RuntimeError("memory write returned IDs inconsistent with pending entries")

                    if callable(inspect_memory_ids):
                        final_state = await inspect_memory_ids(
                            memory_ids,
                            entries=copy.deepcopy(entries),
                        )
                        if (
                            final_state.get("missing_ids")
                            or final_state.get("mismatched_ids")
                        ):
                            raise RuntimeError("memory write did not durably match pending entries")

            tool_message = payload.get("tool_message")
            if not isinstance(tool_message, dict):
                tool_message = {
                    "role": "tool",
                    "tool_call_id": str(payload.get("tool_call_id") or "memory_extract"),
                    "content": json.dumps(
                        {"status": "success", "memory_ids": memory_ids},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }

            if write_memory:
                receipt_payload = {
                    "idempotency_key": normalized_key,
                    "memory_ids": list(memory_ids),
                    "tool_message": copy.deepcopy(tool_message),
                    "status": "success",
                }
                self._append_agent_thread_event(
                    thread_id,
                    "memory_extraction_receipt",
                    receipt_payload,
                    interaction_type="memory_extract",
                    interaction_name=interaction_name,
                )

            # receipt 可能已经落盘但 conversation_message 尚未落盘；
            # 追加前先检查事件，遇到 fsync 后重试也不会产生重复消息。
            existing_messages = [
                event
                for event in self._read_agent_thread_events(thread_id)
                if isinstance(event, dict)
                and event.get("event_type") == "conversation_message"
                and isinstance(event.get("payload"), dict)
                and event["payload"].get("message", event["payload"]) == tool_message
            ]
            if not existing_messages:
                self._append_agent_thread_message(
                    thread_id,
                    tool_message,
                    interaction_type="memory_extract",
                    interaction_name=interaction_name,
                )

            conversation_messages = payload.get("conversation_messages")
            if not isinstance(conversation_messages, list):
                conversation_messages = self._read_agent_thread_messages(thread_id)
            conversation_messages = copy.deepcopy(conversation_messages)
            if not conversation_messages or conversation_messages[-1] != tool_message:
                conversation_messages.append(copy.deepcopy(tool_message))
            return {
                "memory_ids": memory_ids,
                "memories": copy.deepcopy(memories),
                "conversation_messages": conversation_messages,
                "full_history": copy.deepcopy(payload.get("full_history") or []),
                "thread_id": thread_id,
                "thread_ref": self._agent_thread_reference(thread_id),
            }

        if receipt_payload is not None:
            return await finish_from_commit(payload=receipt_payload, write_memory=False)
        if pending_payload is not None:
            # pending intent 中保存的是首次 LLM 提炼结果。恢复时只重做
            # idempotent upsert，禁止再次构造 caller 摘要或启动新 Thread。
            return await finish_from_commit(payload=pending_payload, write_memory=True)

        messages = [
            copy.deepcopy(event["payload"].get("message", event["payload"]))
            for event in thread_events
            if isinstance(event, dict)
            and event.get("event_type") == "conversation_message"
            and isinstance(event.get("payload"), dict)
        ]
        if not messages:
            messages = self._read_agent_thread_messages(thread_id)
        if not messages:
            raise RuntimeError(f"Agent {self.id} Thread {thread_id} 没有可提炼的对话")

        # 经营回合通过 World 的模型运行时发出请求，那里会合并正式模型的
        # request_options。记忆提炼也属于同一 Thread 的工具调用，不能绕过
        # 这条路径，否则 Qwen 会重新开启 thinking，且会丢失同一 Thread 的
        # KV-cache 会话标识。
        llm_call = self._llm_call
        resolve_model = getattr(self._world, "_resolve_model_selection", None)
        if callable(resolve_model):
            selection = resolve_model(self.id, None)
            runtime_call = getattr(getattr(selection, "runtime", None), "llm_call", None)
            if callable(runtime_call):
                llm_call = runtime_call

        async def extract_with_thread_session(payload: Dict[str, Any]) -> Dict[str, Any]:
            request_payload = _with_thread_session_id(dict(payload), thread_id)
            return await llm_call(request_payload)

        result = await extract_memories_from_thread(
            conversation_messages=messages,
            llm_call=extract_with_thread_session,
            thread_id=thread_id,
            metadata={
                "agent_id": self.id,
                "step": timestamp,
                "interaction_name": interaction_name,
            },
        )
        if not result.get("success"):
            raise RuntimeError(
                f"Agent {self.id} 记忆提炼失败："
                f"{result.get('error') or 'unknown_error'}"
            )

        memories = result.get("memories") or []
        common_metadata = dict(metadata or {})
        common_metadata.update(
            {
                "extraction_method": "structured_extract",
                "agent_id": self.id,
            }
        )
        entries = []
        for index, memory in enumerate(memories):
            entry = {
                "memory_type": "episodic",
                "content": memory["content"],
                "timestamp": timestamp,
                "importance": memory["importance"],
                "metadata": dict(common_metadata),
            }
            entry["memory_id"] = self._memory.stable_memory_id(
                f"{normalized_key}:memory:{index}",
                memory_type="episodic",
            )
            entry["metadata"]["idempotency_key"] = normalized_key
            entries.append(entry)

        tool_message = {
            "role": "tool",
            "tool_call_id": result.get("tool_call_id") or "memory_extract",
            "content": "",
        }
        pending_payload = {
            "idempotency_key": normalized_key,
            "entries": copy.deepcopy(entries),
            "memory_ids": [str(entry["memory_id"]) for entry in entries],
            "memories": copy.deepcopy(memories),
            "timestamp": timestamp,
            "tool_call_id": tool_message["tool_call_id"],
        }
        # Pending intent 是向量写入前的 durable fence。写失败时不会写
        # vector；若 fsync 在写后报错，下一次仍可从该事件恢复。
        self._append_agent_thread_event(
            thread_id,
            "memory_extraction_pending",
            pending_payload,
            interaction_type="memory_extract",
            interaction_name=interaction_name,
        )

        memory_ids = []
        if entries:
            memory_ids = await self._memory.add_memories_batch(
                copy.deepcopy(entries),
                fire_and_forget=False,
                trace={
                    "step": timestamp,
                    "step_name": getattr(
                        self._world, "_current_code_step_name", None
                    ),
                    "interaction_type": "memory_write",
                    "interaction_name": interaction_name,
                    "thread_id": thread_id,
                    "idempotency_key": normalized_key,
                },
            )
        tool_message["content"] = json.dumps(
            {"status": "success", "memory_ids": memory_ids},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        receipt_payload = {
            "idempotency_key": normalized_key,
            "memory_ids": list(memory_ids),
            "tool_message": copy.deepcopy(tool_message),
            "status": "success",
        }
        # Receipt 也是 Thread 证据的一部分；vector upsert 成功但 receipt
        # fsync 失败时，pending intent 仍然存在，调用方可用同 key 重试。
        self._append_agent_thread_event(
            thread_id,
            "memory_extraction_receipt",
            receipt_payload,
            interaction_type="memory_extract",
            interaction_name=interaction_name,
        )
        result["conversation_messages"].append(tool_message)
        self._append_agent_thread_message(
            thread_id,
            tool_message,
            interaction_type="memory_extract",
            interaction_name=interaction_name,
        )
        return {
            "memory_ids": memory_ids,
            "memories": memories,
            "conversation_messages": result["conversation_messages"],
            "full_history": result["full_history"],
            "thread_id": thread_id,
            "thread_ref": self._agent_thread_reference(thread_id),
        }

    def _enhance_output_schema(self, original_schema: Dict[str, Any]) -> Dict[str, Any]:
        """
        增强 output_schema，自动添加 required 字段

        Args:
            original_schema: 原始的 output_schema

        Returns:
            增强后的 schema，自动添加了 required 字段（仅顶层）
        """
        enhanced_schema = copy.deepcopy(original_schema)

        # 自动添加 required 字段（仅顶层）
        if "properties" in enhanced_schema and "required" not in enhanced_schema:
            properties = enhanced_schema["properties"]
            if isinstance(properties, dict) and properties:
                enhanced_schema["required"] = list(properties.keys())

        return enhanced_schema

    async def _call_with_strict_retry(self, effective_llm_call: Callable, request: Dict[str, Any]) -> Dict[str, Any]:
        """Send the structured-output enforcement request in strict mode.

        Args:
            effective_llm_call: LLM 调用函数
            request: LLM 请求参数

        Returns:
            LLM 响应结果
        """
        enhanced_request = copy.deepcopy(request)
        for tool in enhanced_request.get("tools", []):
            function = tool.get("function", {})
            if function.get("name") in {"finish_instruction", "submit_result"}:
                function["strict"] = True
        return await effective_llm_call(enhanced_request)

    async def instruct(self,
                      instruction: str,
                      context: Optional[Dict[str, Any]] = None,
                      current_step: Optional[int] = None,
                      action_tags: Optional[List[str]] = None,
                      retrieve_memory: bool = True,
                      output_schema: Optional[Dict[str, Any]] = None,
                      reasoning_stages: Optional[List[Dict[str, Any]]] = None,
                      llm_call_override: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None,
                      *,
                      override_actionset: Optional[Any] = None,
                      max_turns: int = 3,
                      memory_top_k: int = 10,
                      turn_remain_hint: bool = True,
                      hint_on_remain_turn: int = 1,
                      terminal_action_names: Optional[List[str]] = None,
                      completion_action_tags: Optional[List[str]] = None,
                      required_action_names: Optional[List[str]] = None,
                      required_action_tags: Optional[List[str]] = None,
                      max_action_calls: Optional[int] = None,
                      max_request_messages: Optional[int] = None,
                      action_call_limits: Optional[Dict[str, int]] = None,
                      prefer_direct_json_output: bool = False,
                      llm_request_options: Optional[Dict[str, Any]] = None,
                      prior_messages: Optional[List[Dict[str, Any]]] = None,
                      thread_id: Optional[str] = None,
                      thread_ref: Optional[Dict[str, Any]] = None,
                      trace: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        完整的指令执行方法 - LLMAgent的"大脑中枢"

        实现完整的认知流程：
        1. 处理提醒
        2. 召回记忆
        3. 组装提示词
        4. 筛选ActionSet
        5. 调用推理引擎
        6. 处理结果并写入记忆

        Args:
            instruction: 指令文本
            context: 上下文信息（包含fov_results等）
            current_step: 当前步骤
            action_tags: 用于筛选可用动作的标签
            retrieve_memory: 是否读取/检索记忆
            output_schema: 强制结构化输出的schema
            reasoning_stages: 覆盖默认的推理阶段配置
            terminal_action_names: 命中后立即结束本次 agent loop 的动作名列表
            completion_action_tags: 命中这些标签的动作成功执行后结束本次 agent loop

        Returns:
            指令执行结果
        """
        effective_llm_call = llm_call_override or self._llm_call
        if not effective_llm_call:
            raise RuntimeError(f"LLM call function not initialized for Agent '{self.id}'")
        safe_llm_request_options = _sanitize_llm_request_options(llm_request_options)
        normalized_thread_id = str(thread_id or "").strip() or None
        safe_llm_request_options = _with_thread_session_id(
            safe_llm_request_options,
            normalized_thread_id,
        )
        if prior_messages is not None and normalized_thread_id is not None:
            raise ValueError("prior_messages and thread_id cannot be used together")
        resolved_thread_ref = (
            self._agent_thread_reference(normalized_thread_id)
            if normalized_thread_id is not None
            else None
        )
        if isinstance(resolved_thread_ref, dict):
            owner = resolved_thread_ref.get("agent_id")
            if owner is not None and str(owner) != self.id:
                raise ValueError(
                    f"Agent Thread {normalized_thread_id} belongs to {owner}, not {self.id}"
                )
            if resolved_thread_ref.get("closed") is True:
                raise ValueError(f"Agent Thread {normalized_thread_id} is already closed")
        thread_messages = (
            self._read_agent_thread_messages(normalized_thread_id)
            if normalized_thread_id is not None
            else None
        )

        context = context or {}
        current_step = current_step or 0
        try:
            effective_memory_top_k = int(memory_top_k)
        except (TypeError, ValueError):
            raise ValueError("memory_top_k must be a positive integer")
        if effective_memory_top_k <= 0:
            raise ValueError("memory_top_k must be a positive integer")

        instruct_started = time.perf_counter()
        phase_timings: Dict[str, float] = {}

        def record_phase(phase_name: str, started_at: float) -> None:
            duration = max(time.perf_counter() - started_at, 0.0)
            phase_timings[phase_name] = round(
                phase_timings.get(phase_name, 0.0) + duration,
                6,
            )

        def build_memory_trace(operation: str) -> Dict[str, Any]:
            metadata = {key: value for key, value in dict(trace or {}).items() if value is not None}
            if metadata.get("interaction_type") is not None:
                metadata.setdefault("parent_interaction_type", metadata.get("interaction_type"))
            metadata["interaction_type"] = operation
            metadata.setdefault("interaction_name", operation)
            metadata.setdefault("agent_id", self.id)
            metadata.setdefault("step", current_step)
            if normalized_thread_id is not None:
                metadata.setdefault("thread_id", normalized_thread_id)
            return metadata

        # 1. 处理提醒 - 检查并清空提醒列表
        reminders_text = ""
        if self.reminders:
            reminders_text = "\n".join([f"- {reminder}" for reminder in self.reminders])
            self.clear_reminders()
            logger.debug(f"Agent {self.id} processed {len(self.reminders)} reminders")

        # 2. 调用记忆系统（检索）
        memory_text = ""
        memory_results: List[str] = []
        if retrieve_memory and self._memory:
            memory_retrieve_started = time.perf_counter()
            try:
                memory_results = await self._memory.retrieve(
                    query=instruction,
                    top_k=effective_memory_top_k,
                    current_step=current_step,
                    trace=build_memory_trace("memory_retrieve"),
                ) or []
                if memory_results:
                    memory_text = "\n".join([f"- {mem}" for mem in memory_results])
                    logger.debug(
                        "Agent %s retrieved %s memories for instruction prefix=%r",
                        self.id,
                        len(memory_results),
                        instruction[:50],
                    )

                summary = summarize_text(memory_text)
                self._log(
                    "INFO",
                    AgentEvent.MEMORY_READ,
                    **{
                        LogField.STEP.value: current_step,
                        LogField.MEMORY_QUERY.value: instruction,
                        LogField.MEMORY_RESULTS_COUNT.value: len(memory_results),
                        LogField.MEMORY_RESULT_PREVIEW.value: summary["preview"],
                    },
                )
                if summary["truncated"]:
                    self._log(
                        "DEBUG",
                        AgentEvent.MEMORY_READ,
                        **{
                            LogField.STEP.value: current_step,
                            LogField.MEMORY_QUERY.value: instruction,
                            LogField.MEMORY_RESULT_FULL.value: summary["full"],
                        },
                    )
            except Exception as e:
                logger.warning(f"Memory retrieval failed for agent {self.id}: {e}")
                self._log(
                    "WARNING",
                    AgentEvent.MEMORY_READ,
                    **{
                        LogField.STEP.value: current_step,
                        LogField.MEMORY_QUERY.value: instruction,
                        LogField.ERROR.value: str(e),
                    },
                )
            finally:
                record_phase("memory_retrieve", memory_retrieve_started)

        # 3. 组装提示词
        prompt_build_started = time.perf_counter()
        # System Prompt: 基于persona和state
        system_prompt = self._build_system_prompt()

        # User Prompt: 结构化呈现上下文与任务
        user_sections: List[str] = []
        if reminders_text:
            user_sections.append(self._format_prompt_section("提醒", reminders_text))

        if memory_text:
            user_sections.append(self._format_prompt_section("相关记忆", memory_text))

        if context.get("fov_results"):
            fov_text = self._format_fov_results(context["fov_results"])
            user_sections.append(self._format_prompt_section("视野信息", fov_text))

        requirements_text = self._build_output_requirements_text(output_schema)
        if requirements_text:
            user_sections.append(self._format_prompt_section("输出要求", requirements_text))

        if isinstance(context, dict) and context.get("extra_notes"):
            notes = context.get("extra_notes") or []
            notes_text = "\n".join(f"- {n}" for n in notes if n)
            if notes_text:
                user_sections.append(self._format_prompt_section("附加说明", notes_text))

        user_sections.append(self._format_prompt_section("任务", instruction))

        user_prompt = "\n\n".join(section for section in user_sections if section)
        record_phase("prompt_build", prompt_build_started)

        # 4. 筛选ActionSet
        actionset_build_started = time.perf_counter()
        available_actionset = None
        if override_actionset is not None:
            available_actionset = override_actionset
        elif self._actionset and action_tags is not None:
            # 注释掉详细的调试信息
            # print(f"--- [DEBUG] Filtering ActionSet. Full list: {list(self._actionset.actions.keys())}")
            # print(f"Filtering with tags: {action_tags}")
            # for action_name, action_info in self._actionset.actions.items():
            #     print(f"  Action '{action_name}': tags={action_info.get('tags', [])}")

            available_actionset = self._actionset.filter_by_tags(action_tags=action_tags)

            # 注释掉筛选后的调试信息
            # print(f"After filtering: {list(available_actionset.actions.keys()) if available_actionset else 'None'}")

            logger.debug(f"Agent {self.id} filtered actions: {len(available_actionset.actions)} available")
        elif self._actionset:
            # By default expose ordinary environment actions, but keep memory
            # tools opt-in. Framework-managed retrieval is explicit; exposing
            # memory tools by default causes
            # redundant LLM turns in structured tasks.
            available_actionset = self._actionset.filter_by_tags(exclude_tags=["memory"])
            # print(f"--- [DEBUG] No action_tags provided, using full ActionSet: {list(available_actionset.actions.keys())}")

        # 注释掉详细的调试信息
        # print(f"--- [DEBUG] Agent {self.id} ---")
        # if available_actionset:
        #     print(f"ActionSet passed to loop: {list(available_actionset.actions.keys())}")
        #     print(f"OpenAI Schema: {available_actionset.get_openai_actions_schema()}")
        # else:
        #     print("ActionSet is None!")
        record_phase("actionset_build", actionset_build_started)

        # 5. 调用推理引擎 (execute_action_loop)
        agent_loop_started: Optional[float] = None
        try:
            agent_loop_started = time.perf_counter()
            from .agent_loop import (
                ActionSet,
                DEFAULT_REASONING_STAGES,
                LoopResult,
                build_assistant_turn_trace,
                execute_action_loop,
            )

            # 决定使用哪个推理阶段配置：优先级：参数传入 > Agent默认配置 > 全局默认
            active_stages = reasoning_stages or self._default_reasoning_stages or DEFAULT_REASONING_STAGES

            # 如果没有actionset，创建一个空的
            if available_actionset is None:
                available_actionset = ActionSet()

            # 5.1. 实现结构化输出机制：动态创建 submit_result Action（对外保持 finish_instruction 语义字段）
            finish_instruction_added = False
            original_system_prompt = system_prompt
            enhanced_inner_schema = None

            if output_schema:
                # 增强 output_schema（内层）：补齐顶层 required，默认严格 additionalProperties=false
                enhanced_inner_schema = self._enhance_output_schema(output_schema)
                if isinstance(enhanced_inner_schema, dict):
                    enhanced_inner_schema.setdefault("additionalProperties", False)

                # 外层单一工具submit_result的schema封装：{"result": <inner>}
                submit_result_schema = {
                    "type": "object",
                    "properties": {
                        "result": enhanced_inner_schema,
                    },
                    "required": ["result"],
                    "additionalProperties": False,
                }
                from ..function_registry import normalize_strict_function_parameters

                submit_result_schema = normalize_strict_function_parameters(
                    submit_result_schema
                )

                # 创建 submit_result Action（注意：action_tags 过滤已在之前进行，动态注入不受其影响）
                async def _submit_result_action(**kwargs):
                    """
                    内部占位动作：用于OpenAI函数调用契约，真正的结构化结果由LLM以工具参数提交。
                    """
                    # 仅返回简要确认，避免噪音（结果解析在下游执行）
                    return f"submit_result received. keys={list(kwargs.keys())}"

                available_actionset.add_action(
                    name="submit_result",
                    func=_submit_result_action,
                    description="【必须调用】提交结构化结果以完成本次指令。请在 result 字段中提供完整的结构化JSON。",
                    parameters=submit_result_schema,
                    tags=["system", "output", "required"],
                    strict=True,
                )
                finish_instruction_added = True

                # 修改system prompt以明确提交方式（首轮也注入并提示，允许首轮直接提交）
                system_prompt = (
                    original_system_prompt
                    + "\n\n🚨 重要：本次任务需要结构化输出。请在完成推理与行动后，调用 submit_result 工具，在其 result 参数中提交最终JSON结果（仅JSON，不要额外文本）。"
                )

            # 创建上下文提供者函数
            def context_provider():
                """Provides current context stack and update function for action execution."""
                current_stack = self._world.get_context_stack()
                update_func = self._world.set_context_stack
                return current_stack, update_func, self._record_action_trace

            async def traced_llm_call(payload: Dict[str, Any]) -> Dict[str, Any]:
                request_payload = dict(payload)
                agent_loop_control_options = {
                    "provider_request_retry_max",
                    "empty_response_retry_max",
                    "empty_response_retry_temperature_delta",
                    "empty_response_retry_temperature_max",
                    "repeated_read_temperature_delta",
                    "repeated_read_temperature_max",
                }
                for key, value in safe_llm_request_options.items():
                    if key in agent_loop_control_options:
                        continue
                    request_payload.setdefault(key, value)
                payload_metadata = dict(request_payload.get("metadata") or {})
                metadata = {
                    "agent_id": self.id,
                    "step": current_step,
                }
                if normalized_thread_id is not None:
                    metadata["thread_id"] = normalized_thread_id
                if trace:
                    metadata.update({k: v for k, v in trace.items() if v is not None})
                metadata.update({k: v for k, v in payload_metadata.items() if v is not None})
                request_payload["metadata"] = metadata
                return await effective_llm_call(request_payload)

            def _has_ordinary_actions() -> bool:
                for action_name, action_info in available_actionset.actions.items():
                    tags = {str(tag).lower() for tag in (action_info.get("tags", []) or [])}
                    if action_name == "submit_result" or "system" in tags:
                        continue
                    return True
                return False

            direct_structured_output = None
            direct_loop_result = None
            preloop_llm_calls = 0
            preloop_history: List[Dict[str, Any]] = []
            if (
                prefer_direct_json_output
                and output_schema
                and isinstance(enhanced_inner_schema, dict)
                and not _has_ordinary_actions()
                and normalized_thread_id is None
            ):
                try:
                    schema_text = json.dumps(enhanced_inner_schema, ensure_ascii=False, separators=(",", ":"))
                    direct_messages = [
                        {
                            "role": "system",
                            "content": (
                                system_prompt
                                + "\n\n[输出流程]\n"
                                "只输出一个符合 JSON Schema 的 JSON 对象。不要使用 Markdown，不要解释，不要调用工具。"
                            ),
                        },
                        {
                            "role": "user",
                            "content": (
                                user_prompt
                                + "\n\n请直接输出 JSON 对象；如果前文提到 submit_result，本轮以此处要求为准，不调用工具。\n"
                                f"JSON Schema:\n{schema_text}"
                            ),
                        },
                    ]
                    direct_request = {
                        "messages": direct_messages,
                        "tools": None,
                        "tool_choice": None,
                    }
                    try:
                        direct_response = await traced_llm_call(direct_request)
                    except Exception as exc:
                        preloop_history.append(
                            {
                                "turn": 1,
                                "request": copy.deepcopy(direct_request),
                                "response": None,
                                "error": str(exc),
                                "interaction_name": "direct_structured_output",
                            }
                        )
                        preloop_llm_calls = 1
                        raise
                    preloop_history.append(
                        {
                            "turn": 1,
                            "request": copy.deepcopy(direct_request),
                            "response": copy.deepcopy(direct_response),
                            "interaction_name": "direct_structured_output",
                        }
                    )
                    preloop_llm_calls = 1
                    candidate = _parse_structured_json_from_model_text(direct_response.get("content") or "")
                    if candidate is not None and _validate_structured_output_against_schema(candidate, enhanced_inner_schema):
                        direct_structured_output = candidate
                        direct_loop_result = LoopResult(
                            status="success",
                            phases={"default": direct_response.get("content") or ""},
                            phases_unknown={},
                            full_history=copy.deepcopy(preloop_history),
                            parsing_errors=[],
                            total_turns=1,
                            default_stage_name="default",
                            action_calls=[],
                            termination_reason="direct_structured_output",
                            model_type="standard",
                            conversation_messages=[
                                *copy.deepcopy(direct_messages),
                                copy.deepcopy(direct_response),
                            ],
                        )
                        preloop_llm_calls = 0
                except Exception:
                    logger.debug("Direct JSON structured output path failed for agent %s", self.id, exc_info=True)

            # 首次执行action loop
            effective_terminal_action_names = list(terminal_action_names or [])
            if finish_instruction_added and not any(
                str(name).strip().lower() == "submit_result"
                for name in effective_terminal_action_names
            ):
                effective_terminal_action_names.append("submit_result")

            previous_action_trace = getattr(self, "_current_action_trace", None)
            self._current_action_trace = dict(trace or {})
            try:
                if direct_loop_result is not None:
                    loop_result = direct_loop_result
                else:
                    loop_result = await execute_action_loop(
                        instruction=user_prompt,
                        action_set=available_actionset,
                        system_prompt=system_prompt,
                        stages=active_stages,
                        llm_call=traced_llm_call,
                        # 将 instruct 的最大回合数从4降为3（可通过参数覆盖）
                        max_turns=max_turns,
                        context_provider=context_provider,
                        terminal_action_names=effective_terminal_action_names,
                        completion_action_tags=completion_action_tags,
                        required_action_names=required_action_names,
                        required_action_tags=required_action_tags,
                        turn_remain_hint=turn_remain_hint,
                        hint_on_remain_turn=hint_on_remain_turn,
                        max_action_calls=max_action_calls,
                        max_request_messages=max_request_messages,
                        action_call_limits=action_call_limits,
                        llm_request_options=safe_llm_request_options,
                        prior_messages=(
                            thread_messages
                            if normalized_thread_id is not None
                            else prior_messages
                        ),
                        thread_message_recorder=(
                            (
                                lambda message: self._append_agent_thread_message(
                                    normalized_thread_id,
                                    message,
                                    interaction_type="instruct",
                                    interaction_name=str(
                                        (trace or {}).get("interaction_name")
                                        or "instruction"
                                    ),
                                )
                            )
                            if normalized_thread_id is not None
                            else None
                        ),
                        thread_event_recorder=(
                            (
                                lambda event_type, payload: self._append_agent_thread_event(
                                    normalized_thread_id,
                                    event_type,
                                    payload,
                                    interaction_type="instruct",
                                    interaction_name=str(
                                        (trace or {}).get("interaction_name")
                                        or "instruction"
                                    ),
                                )
                            )
                            if normalized_thread_id is not None
                            else None
                        ),
                    )
                    if preloop_history:
                        combined_history = [
                            *copy.deepcopy(preloop_history),
                            *copy.deepcopy(loop_result.full_history),
                        ]
                        for turn_number, item in enumerate(
                            combined_history,
                            start=1,
                        ):
                            item["turn"] = turn_number
                        loop_result.full_history = combined_history
            finally:
                if previous_action_trace is None:
                    try:
                        delattr(self, "_current_action_trace")
                    except AttributeError:
                        pass
                else:
                    self._current_action_trace = previous_action_trace

            # 5.2. 结构化输出验证和强制执行轮次
            finish_instruction_called = False  # 保持对外语义：是否调用过提交工具
            structured_output = direct_structured_output
            extra_llm_calls = preloop_llm_calls
            post_loop_messages = copy.deepcopy(loop_result.conversation_messages)

            async def call_post_loop_model(
                *,
                messages: List[Dict[str, Any]],
                tools: Any,
                tool_choice: Any,
                interaction_name: str,
                strict: bool = False,
            ) -> Dict[str, Any]:
                nonlocal extra_llm_calls, post_loop_messages
                request = {
                    "messages": copy.deepcopy(messages),
                    "tools": copy.deepcopy(tools),
                    "tool_choice": copy.deepcopy(tool_choice),
                    "metadata": {
                        "interaction_type": "instruct",
                        "interaction_name": interaction_name,
                    },
                }
                turn_number = len(loop_result.full_history) + 1
                try:
                    response = await (
                        self._call_with_strict_retry(traced_llm_call, request)
                        if strict
                        else traced_llm_call(request)
                    )
                except Exception as exc:
                    loop_result.full_history.append(
                        {
                            "turn": turn_number,
                            "request": request,
                            "response": None,
                            "error": str(exc),
                            "interaction_name": interaction_name,
                        }
                    )
                    raise
                extra_llm_calls += 1
                loop_result.full_history.append(
                    {
                        "turn": turn_number,
                        "request": request,
                        "response": copy.deepcopy(response),
                        "interaction_name": interaction_name,
                    }
                )
                post_loop_messages = [
                    *copy.deepcopy(messages),
                    copy.deepcopy(response),
                ]
                loop_result.conversation_messages = copy.deepcopy(post_loop_messages)
                return response

            def append_post_loop_tool_receipt(
                response: Dict[str, Any],
                *,
                content: str,
            ) -> None:
                nonlocal post_loop_messages
                tool_call = next(
                    (
                        item
                        for item in (response.get("tool_calls") or [])
                        if isinstance(item, dict)
                        and isinstance(item.get("function"), dict)
                        and item["function"].get("name") == "submit_result"
                    ),
                    None,
                )
                if tool_call is None:
                    return
                message = {
                    "role": "tool",
                    "tool_call_id": str(tool_call.get("id") or "submit_result"),
                    "content": content,
                }
                post_loop_messages.append(message)
                loop_result.conversation_messages = copy.deepcopy(post_loop_messages)
                self._append_agent_thread_message(
                    normalized_thread_id,
                    message,
                    interaction_type="instruct",
                    interaction_name="submit_result_receipt",
                )

            if (
                finish_instruction_added
                and structured_output is None
                and loop_result.status != "error"
            ):
                # 检查是否调用了 submit_result（对外仍呈现 finish_instruction_called 语义）
                # 优先从 loop_result.action_calls 提取；回退到 phases 中的 action_call 列表。
                action_items: List[Dict[str, Any]] = []
                seen_call_ids = set()
                for item in getattr(loop_result, "action_calls", []) or []:
                    if isinstance(item, dict):
                        action_items.append(item)
                        call_id = item.get("call_id")
                        if call_id:
                            seen_call_ids.add(call_id)
                for phase_value in (loop_result.phases or {}).values():
                    if not isinstance(phase_value, list):
                        continue
                    for item in phase_value:
                        if not isinstance(item, dict) or item.get("type") != "action_call":
                            continue
                        call_id = item.get("call_id")
                        if call_id and call_id in seen_call_ids:
                            continue
                        action_items.append(item)
                        if call_id:
                            seen_call_ids.add(call_id)

                for action_item in action_items:
                    if action_item.get("action_name") != "submit_result":
                        continue
                    # 提取 submit_result.arguments.result 作为结构化输出
                    args_dict = action_item.get("arguments", {}) or {}
                    if isinstance(args_dict, dict):
                        structured_output = args_dict.get("result", None)
                    finish_instruction_called = True

                # 如果没有调用 submit_result，启动强制执行轮次（第一阶段：tool_choice 精确指向 submit_result）
                if not finish_instruction_called:
                    # 强制执行轮次 - 静默处理，不输出警告

                    if post_loop_messages:
                        messages = copy.deepcopy(post_loop_messages)
                        # 添加强制提示
                        messages.append({
                            "role": "user",
                            "content": "请注意：你必须调用 submit_result 工具来完成任务，并在其 result 参数中提交你的结构化JSON结果。"
                        })

                        # 最后一次LLM调用（使用 strict 重试机制）
                        try:
                            final_response = await call_post_loop_model(
                                messages=messages,
                                tools=available_actionset.get_openai_actions_schema(),
                                tool_choice={"type": "function", "function": {"name": "submit_result"}},
                                interaction_name="submit_result_enforcement",
                                strict=True,
                            )

                            # 解析强制调用的结果
                            if final_response.get("tool_calls"):
                                for tool_call in final_response["tool_calls"]:
                                    if tool_call["function"]["name"] == "submit_result":
                                        import json_repair
                                        args = json_repair.loads(tool_call["function"]["arguments"] or "{}")
                                        if isinstance(args, dict):
                                            structured_output = args.get("result", None)
                                        finish_instruction_called = True
                                        break
                            append_post_loop_tool_receipt(
                                final_response,
                                content="submit_result received for local schema validation.",
                            )

                        except Exception as e:
                            logger.error(f"Error in enforcement round for agent {self.id}: {e}")

                # 5.3. 结构化输出后校验与单次纠错（基于内层schema）
                def _validate_against_schema(data: Any, schema: Dict[str, Any]) -> bool:
                    """对结构化输出进行JSON Schema校验（严格模式）。"""
                    return _validate_structured_output_against_schema(data, schema)

                if output_schema and structured_output is not None:
                    inner_schema = enhanced_inner_schema  # 与提交时保持一致
                    is_valid = _validate_against_schema(structured_output, inner_schema)
                    if not is_valid:
                        # 纠错轮次：向LLM说明校验失败，请重新提交（仍使用 submit_result 工具）
                        try:
                            messages = copy.deepcopy(post_loop_messages) or [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ]

                            messages.append({
                                "role": "user",
                                "content": "你提交的结果与JSON Schema不一致，请重新调用 submit_result 并在 result 中提供完全符合Schema的JSON。只提交工具，不要额外文本。",
                            })

                            retry_response = await call_post_loop_model(
                                messages=messages,
                                tools=available_actionset.get_openai_actions_schema(),
                                tool_choice={"type": "function", "function": {"name": "submit_result"}},
                                interaction_name="submit_result_schema_correction",
                                strict=True,
                            )
                            if retry_response.get("tool_calls"):
                                for tool_call in retry_response["tool_calls"]:
                                    if tool_call["function"]["name"] == "submit_result":
                                        import json_repair
                                        args = json_repair.loads(tool_call["function"]["arguments"] or "{}")
                                        candidate = args.get("result") if isinstance(args, dict) else None
                                        if candidate is not None and _validate_against_schema(candidate, inner_schema):
                                            structured_output = candidate
                                        break
                            append_post_loop_tool_receipt(
                                retry_response,
                                content="submit_result correction received for local schema validation.",
                            )
                        except Exception as e:
                            logger.error(f"Error in correction round for agent {self.id}: {e}")

                # 5.4. 若仍无有效结构化结果，进行第二种强制方式（JSON前缀fallback）
                if output_schema and (structured_output is None or not _validate_against_schema(structured_output, enhanced_inner_schema)):
                    try:
                        base_messages = copy.deepcopy(post_loop_messages) or [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ]

                        # 向用户明确只输出JSON，并附上Schema文本
                        try:
                            import json as _json
                            schema_text = _json.dumps(enhanced_inner_schema, ensure_ascii=False, indent=2)
                        except Exception:
                            schema_text = str(enhanced_inner_schema)
                        base_messages.append({
                            "role": "user",
                            "content": f"现在请严格按照以下JSON Schema输出最终答案，仅输出JSON，不要任何额外文本。\n\nSchema:\n{schema_text}",
                        })
                        # 为了提示结构，附上schema简要（可选：仅键）
                        # 直接追加一个assistant起始，促使模型续写JSON
                        base_messages.append({
                            "role": "assistant",
                            "content": "```json\n{",
                        })

                        fallback_response = await call_post_loop_model(
                            messages=base_messages,
                            tools=None,
                            tool_choice=None,
                            interaction_name="structured_json_fallback",
                        )

                        # 从响应中提取 JSON。这里要兼容 assistant 已预填 "{" 后，
                        # 模型只返回 '"field": value}' 这种对象续写的情况。
                        content = fallback_response.get("content") or ""
                        if isinstance(content, str):
                            candidate = _parse_structured_json_from_model_text(
                                content,
                                assume_prefilled_object=True,
                            )
                            if candidate is not None and _validate_against_schema(candidate, enhanced_inner_schema):
                                structured_output = candidate
                    except Exception as e:
                        logger.error(f"Error in JSON-prefix fallback for agent {self.id}: {e}")

            record_phase("agent_loop", agent_loop_started)

            # 6. 处理结果。长期记忆只允许通过完整 Thread 的显式提炼流程写入。
            assistant_turn_trace = build_assistant_turn_trace(loop_result.full_history)
            visible_assistant_text = "\n\n".join(
                turn["assistant_text"]
                for turn in assistant_turn_trace
                if turn["has_visible_text"]
            )
            performative_output = visible_assistant_text
            raw_loop_output = {
                "phases": loop_result.phases,
                "phases_unknown": loop_result.phases_unknown,
                "full_history": loop_result.full_history,
                "conversation_messages": loop_result.conversation_messages,
                "assistant_turn_trace": assistant_turn_trace,
                "parsing_errors": loop_result.parsing_errors,
                "default_stage_name": loop_result.default_stage_name,
            }

            # 如果没有使用结构化输出机制，从传统Actions phase提取
            if not finish_instruction_added and structured_output is None:
                if "Actions" in loop_result.phases:
                    actions_phase = loop_result.phases["Actions"]
                    if isinstance(actions_phase, list):
                        # 找到action_call类型的项目
                        action_calls = [item for item in actions_phase if item.get("type") == "action_call"]
                        if action_calls:
                            structured_output = action_calls

            raw_loop_output["full_history"] = loop_result.full_history
            raw_loop_output["conversation_messages"] = (
                loop_result.conversation_messages
            )

            # 情绪模型更新（可选实现）
            # TODO: 将performative_output传递给情绪模型，更新self.state['emotion']

            total_llm_calls = loop_result.total_turns + extra_llm_calls
            record_phase("total", instruct_started)
            instruction_status = (
                "error" if loop_result.status == "error" else "success"
            )
            loop_error = loop_result.error or loop_result.termination_reason

            return {
                "status": instruction_status,
                "agent_id": self.id,
                "instruction": instruction,
                "performative_output": (
                    performative_output
                    if instruction_status != "error"
                    else f"执行指令时发生错误: {loop_error or 'agent_loop_error'}"
                ),
                "visible_assistant_text": visible_assistant_text,
                "assistant_turn_trace": assistant_turn_trace,
                "structured_output": structured_output,
                "raw_output": raw_loop_output,
                "total_turns": total_llm_calls,
                "actions": loop_result.action_calls,
                "termination_reason": loop_result.termination_reason,
                "termination_action": loop_result.termination_action,
                "activation_status": loop_result.activation_status,
                **(
                    {"error": loop_error or "agent_loop_error"}
                    if instruction_status == "error"
                    else {}
                ),
                **(
                    {
                        "failure_class": loop_result.failure_class,
                        "retry_scope": loop_result.retry_scope,
                        "retry_attempts": loop_result.retry_attempts,
                    }
                    if loop_result.failure_class is not None
                    else {}
                ),
                "llm_calls": total_llm_calls,
                "finish_instruction_called": finish_instruction_called,  # 现在正确实现了
                "stages_executed": list(loop_result.phases.keys()),
                "memory_retrieved": retrieve_memory,
                "memory_top_k": effective_memory_top_k if retrieve_memory else 0,
                "actions_available": len(available_actionset.actions) if available_actionset else 0,
                "has_structured_output": bool(structured_output),
                "output_schema_provided": output_schema is not None,
                # 新增推理相关信息
                "reasoning_content": loop_result.reasoning_content,
                "thinking_process": loop_result.thinking_process,
                "has_reasoning": loop_result.has_reasoning,
                "model_type": loop_result.model_type,
                "thread_id": normalized_thread_id,
                "thread_ref": (
                    self._agent_thread_reference(normalized_thread_id)
                    if normalized_thread_id is not None
                    else thread_ref
                ),
                "phase_timings": dict(sorted(phase_timings.items())),
            }

        except Exception as e:
            if agent_loop_started is not None and "agent_loop" not in phase_timings:
                record_phase("agent_loop", agent_loop_started)
            record_phase("total", instruct_started)
            error_message = _format_agent_exception(e)
            logger.error(
                "Error in instruction execution for agent %s: %s",
                self.id,
                error_message,
                exc_info=True,
            )
            return {
                "status": "error",
                "agent_id": self.id,
                "instruction": instruction,
                "error": error_message,
                "performative_output": f"执行指令时发生错误: {error_message}",
                "visible_assistant_text": "",
                "assistant_turn_trace": [],
                "structured_output": None,
                "raw_output": None,
                "total_turns": 0,
                "finish_instruction_called": False,
                "has_structured_output": False,
                "output_schema_provided": output_schema is not None,
                **(
                    {
                        "failure_class": str(getattr(e, "failure_class")),
                        "retry_scope": str(getattr(e, "retry_scope")),
                        "retry_attempts": int(getattr(e, "retry_attempts")),
                    }
                    if getattr(e, "failure_class", None) is not None
                    else {}
                ),
                # 推理相关信息 - 错误情况下为空
                "reasoning_content": None,
                "thinking_process": [],
                "has_reasoning": False,
                "model_type": None,
                "phase_timings": dict(sorted(phase_timings.items())),
            }

    async def interview(self,
                       question: str,
                       context: Optional[Dict[str, Any]] = None,
                       current_step: Optional[int] = None,
                       output_schema: Optional[Dict[str, Any]] = None,
                       reasoning_stages: Optional[List[Dict[str, Any]]] = None,
                       llm_call_override: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None,
                       trace: Optional[Dict[str, Any]] = None,
                       *,
                       retrieve_memory: bool = True,
                       prefer_direct_json_output: bool = False,
                       memory_top_k: int = 10,
                       llm_request_options: Optional[Dict[str, Any]] = None,
                       max_turns: int = 2) -> Dict[str, Any]:
        """
        专用的访谈方法 - 用于获取Agent信息而不污染其记忆或行为

        特点：
        1. 可以访问Agent的现有记忆（读取）
        2. 不能执行任何改变世界状态的动作（除了finish_instruction）
        3. 访谈事件本身不会被存储为记忆（不写入）

        Args:
            question: 访谈问题
            context: 上下文信息
            current_step: 当前步骤
            output_schema: 强制结构化输出的schema
            reasoning_stages: 可选推理阶段配置（覆盖 agent 默认设置）

        Returns:
            访谈结果
        """
        # 调用instruct方法，但限制参数以确保访谈纯净性
        return await self.instruct(
            instruction=question,
            context=context,
            current_step=current_step,
            action_tags=[],  # 禁止所有动作（除了finish_instruction）
            retrieve_memory=retrieve_memory,
            memory_top_k=memory_top_k,
            output_schema=output_schema,
            reasoning_stages=reasoning_stages,
            terminal_action_names=["submit_result"],
            max_turns=max_turns,
            llm_call_override=llm_call_override,
            trace=trace,
            prefer_direct_json_output=prefer_direct_json_output,
            llm_request_options=llm_request_options,
        )

    def _build_system_prompt(self) -> str:
        persona_lines = [f"你（{self.id}）是一个{self.type}。"]
        if self._persona_type:
            persona_lines.append(f"{self.type}的定义：{self._persona_type.strip()}")

        if self._persona:
            persona_lines.append(f"你的个人设定：{self._persona.strip()}")
        elif not self._persona_type:
            persona_lines.append("你的个人设定：请根据指令完成任务。")

        persona_text = "\n".join(persona_lines)
        role_section = f"[扮演角色]\n{persona_text}"

        env_instruction = ""
        try:
            environment = self._world.get_environment()
            env_instruction = (environment.agent_instruction or "").strip()
        except Exception:
            env_instruction = ""
        env_text = env_instruction if env_instruction else "无额外环境指引，遵循通用规则。"
        env_section = f"[环境情况]\n{env_text}"

        current_state = self._world.agents_data[self._id].get("state", {})
        if current_state:
            state_lines = [f"- {key}: {value}" for key, value in current_state.items()]
            state_text = "\n".join(state_lines)
        else:
            state_text = f"当前类型: {self.type}"
        state_section = f"[当前状态]\n{state_text}"

        guidance_section = "[说明]\n请根据你的角色、环境和当前状态来执行指令。"

        return "\n\n".join([role_section, env_section, state_section, guidance_section])

    def _format_fov_results(self, fov_results: Dict[str, Any]) -> str:
        """格式化FoV结果为文本"""
        if not fov_results:
            return "无视野信息"

        fov_parts = []
        for fov_name, fov_data in fov_results.items():
            if "error" in fov_data:
                fov_parts.append(f"- {fov_name}: 错误 - {fov_data['error']}")
            else:
                fov_parts.append(f"- {fov_name}: {str(fov_data)}")

        return "\n".join(fov_parts)

    def _build_output_requirements_text(
        self,
        output_schema: Optional[Dict[str, Any]],
    ) -> str:
        """根据输出需求生成文本。记忆提炼不属于 instruct 回合。"""
        requirements: List[str] = []

        if output_schema:
            try:
                schema_text = json.dumps(output_schema, ensure_ascii=False, separators=(",", ":"))
            except Exception:
                schema_text = str(output_schema)
            requirements.append(
                "- 结构化输出：调用 finish_instruction/submit_result，并遵循以下 Schema：\n"
                f"{schema_text}"
            )

        return "\n".join(requirements)

    def _format_prompt_section(self, title: str, content: str) -> str:
        """统一的用户提示块渲染"""
        clean_content = (content or "").strip()
        if not clean_content:
            return ""
        clean_title = title.strip() if title and title.strip() else "信息"
        return f"[{clean_title}]\n{clean_content}"

    def _record_action_trace(self, action_name: str, arguments: Dict[str, Any], result: Any, status: str) -> None:
        """Record every agent action, including read-only actions, to the event stream."""
        event_logger = getattr(self._world, "event_logger", None)
        if event_logger is None:
            return
        try:
            from ..events import BaseEvent

            class AgentActionEvent(BaseEvent):
                def __init__(self, *, context_stack: List[Dict[str, Any]]):
                    super().__init__(event_type="agent_action", context_stack=context_stack)
                    self.source = self_agent_id
                    self.event_data = event_data

                def to_dict(self) -> Dict[str, Any]:
                    payload = super().to_dict()
                    payload.update({"source": self.source, "event_data": self.event_data})
                    return payload

            self_agent_id = self.id
            result_summary = summarize_text(str(result), limit=200)
            context_stack = self._world.get_context_stack()
            step_frame = context_stack.find_frame_by_type("step")
            operator_frame = context_stack.find_frame_by_type("operator")
            action_frame = context_stack.find_frame_by_type("action")
            operator_metadata = dict(operator_frame.metadata) if operator_frame else {}
            action_trace = dict(getattr(self, "_current_action_trace", None) or {})
            event_data = {
                "agent_id": self.id,
                "action": action_name,
                "status": status,
                "arguments": _summarize_action_arguments(arguments),
                "result_preview": result_summary["preview"],
                "result_length": result_summary["length"],
            }
            if step_frame is not None:
                event_data["step_id"] = step_frame.frame_id
            if operator_frame is not None:
                event_data["interaction_name"] = operator_frame.frame_id
            if action_frame is not None:
                event_data["action_context_name"] = action_frame.frame_id
            for key in ("step_name", "interaction_type", "interaction_name"):
                value = operator_metadata.get(key)
                if value is not None:
                    event_data[key] = value
            for key in ("step", "step_name", "interaction_type", "interaction_name"):
                value = action_trace.get(key)
                if value is not None:
                    event_data[key] = value
            current_code_step_name = getattr(self._world, "_current_code_step_name", None)
            if current_code_step_name is not None:
                event_data.setdefault("step_name", current_code_step_name)
            event = AgentActionEvent(context_stack=context_stack.to_list())
            event_logger.write_event(event)
        except Exception:
            logger.debug("Failed to record action trace for %s", action_name, exc_info=True)

    def _log(self, level: str, event: AgentEvent, **payload: Any) -> None:
        """统一的 Agent 日志入口。"""
        log_context = self._world.get_log_context()
        if not log_context:
            return
        log_context.log_agent(self.id, level, str(event), **payload)

    @staticmethod
    def _extract_actions_preview(result: Dict[str, Any], limit: int = 5) -> List[str]:
        """提取动作名称用于摘要展示。"""
        previews: List[str] = []

        structured_output = result.get("structured_output")
        if isinstance(structured_output, dict):
            name = structured_output.get("action_name") or structured_output.get("name")
            if name:
                previews.append(str(name))
        elif isinstance(structured_output, list):
            for item in structured_output:
                if not isinstance(item, dict):
                    continue
                name = (
                    item.get("action_name")
                    or item.get("name")
                    or item.get("action")
                    or item.get("function")
                )
                if name:
                    previews.append(str(name))

        actions_field = result.get("actions")
        if isinstance(actions_field, list):
            for item in actions_field:
                if isinstance(item, dict):
                    name = item.get("name") or item.get("action")
                else:
                    name = str(item)
                if name:
                    previews.append(str(name))

        unique_previews: List[str] = []
        for name in previews:
            if name not in unique_previews:
                unique_previews.append(name)

        return unique_previews[:limit]

    @staticmethod
    def _collect_structured_keys(structured_output: Any) -> List[str]:
        """收集结构化输出的键集合。"""
        if isinstance(structured_output, dict):
            return sorted(structured_output.keys())
        if isinstance(structured_output, list):
            keys = set()
            for item in structured_output:
                if isinstance(item, dict):
                    keys.update(item.keys())
            return sorted(keys)
        return []


class RuleAgent(Agent):
    """
    基于规则的Agent

    用于执行简单的、确定性的规则逻辑
    """

    def __init__(self, agent_id: str, world: 'World'):
        """
        初始化RuleAgent

        Args:
            agent_id: Agent的唯一标识符
            world: World对象引用
        """
        super().__init__(agent_id, world)

        # 验证这是一个rule agent
        if self.archetype != "rule":
            logger.warning(f"Creating RuleAgent for non-rule archetype: {self.archetype}")

        logger.debug(f"Created RuleAgent for '{agent_id}'")

    def execute_rule(self, rule_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行指定的规则

        Args:
            rule_name: 规则名称
            params: 规则参数

        Returns:
            规则执行结果
        """
        return {
            "rule": rule_name,
            "agent_id": self.id,
            "executed": True,
            "params": params
        }
