"""
Agent核心类定义

实现了统一状态架构下的Agent类层次结构，包括：
- Agent基类：支持代理机制的智能Agent容器
- 集成了代理状态访问、依赖注入和事务管理

v3.0 新增功能：
- get_llm_visible_state: 获取 LLM 推理时可见的状态字段
- 支持基于 schema 的权限控制
"""

from typing import Dict, Any, List, Optional, Union, TYPE_CHECKING, Callable, Awaitable
import logging
import copy
import json

# Import proxy system for state management
from ..state_proxy import DictProxy, AccessContext
from ..async_utils import invoke_maybe_async
from ..logging import AgentEvent, LogField, summarize_text
from .memory_extraction import perform_memory_extraction, build_interaction_summary

if TYPE_CHECKING:
    from .memory import Memory
    from .agent_loop import ActionSet, execute_action_loop
    from ..core_data import World

logger = logging.getLogger(__name__)


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
    def reminders(self) -> List[str]:
        """
        获取Agent提醒列表

        注意：目前返回原始列表，未来可能需要代理化
        """
        return self._world.agents_data[self._id]["reminders"]

    def add_reminder(self, reminder: str):
        """添加提醒"""
        self._world.agents_data[self._id]["reminders"].append(reminder)

    def clear_reminders(self):
        """清空提醒"""
        self._world.agents_data[self._id]["reminders"].clear()

    def get_raw_data(self) -> Dict[str, Any]:
        """
        获取原始数据（仅用于调试和特殊情况）

        Returns:
            Agent的原始数据字典
        """
        return self._world.agents_data[self._id]

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

        # 创建带权限控制的 DictProxy
        return DictProxy(
            target_dict=self._world.agents_data[self._id]["state"],
            event_recorder=self._world._create_event_recorder(),
            context_provider=self._world._create_context_provider(),
            path=("agents", self._id, "state"),
            access_context=access_context
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
        """
        尝试 strict 模式，失败时简单重试

        Args:
            effective_llm_call: LLM 调用函数
            request: LLM 请求参数

        Returns:
            LLM 响应结果
        """
        # 第一次尝试：启用 strict
        try:
            enhanced_request = copy.deepcopy(request)
            # 为 finish_instruction 工具添加 strict: true
            for tool in enhanced_request.get("tools", []):
                if tool.get("function", {}).get("name") == "finish_instruction":
                    tool["function"]["parameters"]["strict"] = True
                    break

            return await effective_llm_call(enhanced_request)
        except Exception as e:
            # 简单重试：不使用 strict
            logger.info(f"Strict mode failed, retrying without strict: {e}")
            return await effective_llm_call(request)

    async def instruct(self,
                      instruction: str,
                      context: Optional[Dict[str, Any]] = None,
                      current_step: Optional[int] = None,
                      action_tags: Optional[List[str]] = None,
                      retrieve_memory: bool = True,
                      save_memory: bool = True,
                      output_schema: Optional[Dict[str, Any]] = None,
                      reasoning_stages: Optional[List[Dict[str, Any]]] = None,
                      llm_call_override: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None,
                      *,
                      override_actionset: Optional[Any] = None,
                      max_turns: int = 3,
                      extract_memory: bool = True,
                      turn_remain_hint: bool = True,
                      hint_on_remain_turn: int = 1,
                      terminal_action_names: Optional[List[str]] = None) -> Dict[str, Any]:
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
            save_memory: 是否写入/存储记忆
            output_schema: 强制结构化输出的schema
            reasoning_stages: 覆盖默认的推理阶段配置
            terminal_action_names: 命中后立即结束本次 agent loop 的动作名列表

        Returns:
            指令执行结果
        """
        effective_llm_call = llm_call_override or self._llm_call
        if not effective_llm_call:
            raise RuntimeError(f"LLM call function not initialized for Agent '{self.id}'")

        context = context or {}
        current_step = current_step or 0

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
            try:
                memory_results = await self._memory.retrieve(
                    query=instruction,
                    top_k=10,
                    current_step=current_step
                ) or []
                if memory_results:
                    memory_text = "\n".join([f"- {mem}" for mem in memory_results])
                    # 记忆系统监控
                    print(f"🧠 Agent {self.id} Memory Retrieval:")
                    print(f"   Query: {instruction[:50]}...")
                    print(f"   Retrieved {len(memory_results)} memories")
                    for i, mem in enumerate(memory_results[:3], 1):  # 显示前3条
                        print(f"   {i}. {mem[:80]}...")
                    logger.debug(f"Agent {self.id} retrieved {len(memory_results)} memories")

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

        # 3. 组装提示词
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

        requirements_text = self._build_output_requirements_text(output_schema, save_memory, extract_memory)
        if requirements_text:
            user_sections.append(self._format_prompt_section("输出要求", requirements_text))

        if isinstance(context, dict) and context.get("extra_notes"):
            notes = context.get("extra_notes") or []
            notes_text = "\n".join(f"- {n}" for n in notes if n)
            if notes_text:
                user_sections.append(self._format_prompt_section("附加说明", notes_text))

        user_sections.append(self._format_prompt_section("任务", instruction))

        user_prompt = "\n\n".join(section for section in user_sections if section)

        # 4. 筛选ActionSet
        available_actionset = None
        if override_actionset is not None:
            available_actionset = override_actionset
        elif self._actionset and action_tags:
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
            available_actionset = self._actionset
            # print(f"--- [DEBUG] No action_tags provided, using full ActionSet: {list(available_actionset.actions.keys())}")

        # 注释掉详细的调试信息
        # print(f"--- [DEBUG] Agent {self.id} ---")
        # if available_actionset:
        #     print(f"ActionSet passed to loop: {list(available_actionset.actions.keys())}")
        #     print(f"OpenAI Schema: {available_actionset.get_openai_actions_schema()}")
        # else:
        #     print("ActionSet is None!")

        # 5. 调用推理引擎 (execute_action_loop)
        try:
            from .agent_loop import execute_action_loop, ActionSet, DEFAULT_REASONING_STAGES

            # 决定使用哪个推理阶段配置：优先级：参数传入 > Agent默认配置 > 全局默认
            active_stages = reasoning_stages or self._default_reasoning_stages or DEFAULT_REASONING_STAGES

            # 如果没有actionset，创建一个空的
            if available_actionset is None:
                available_actionset = ActionSet()

            # 5.1. 实现结构化输出机制：动态创建 submit_result Action（对外保持 finish_instruction 语义字段）
            finish_instruction_added = False
            original_system_prompt = system_prompt

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
                return current_stack, update_func

            # 首次执行action loop
            loop_result = await execute_action_loop(
                instruction=user_prompt,
                action_set=available_actionset,
                system_prompt=system_prompt,
                stages=active_stages,
                llm_call=effective_llm_call,
                # 将 instruct 的最大回合数从4降为3（可通过参数覆盖）
                max_turns=max_turns,
                context_provider=context_provider,
                terminal_action_names=terminal_action_names,
                turn_remain_hint=turn_remain_hint,
                hint_on_remain_turn=hint_on_remain_turn
            )

            # 5.2. 结构化输出验证和强制执行轮次
            finish_instruction_called = False  # 保持对外语义：是否调用过提交工具
            structured_output = None

            if finish_instruction_added:
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

                    # 从loop_result的full_history重建messages
                    if loop_result.full_history:
                        # 获取最后一次LLM调用的messages
                        last_turn = loop_result.full_history[-1]
                        messages = last_turn["request"]["messages"].copy()

                        # 添加强制提示
                        messages.append({
                            "role": "user",
                            "content": "请注意：你必须调用 submit_result 工具来完成任务，并在其 result 参数中提交你的结构化JSON结果。"
                        })

                        # 最后一次LLM调用（使用 strict 重试机制）
                        try:
                            final_response = await self._call_with_strict_retry(effective_llm_call, {
                                "messages": messages,
                                "tools": available_actionset.get_openai_actions_schema(),
                                "tool_choice": {"type": "function", "function": {"name": "submit_result"}},
                            })

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

                        except Exception as e:
                            logger.error(f"Error in enforcement round for agent {self.id}: {e}")

                # 5.3. 结构化输出后校验与单次纠错（基于内层schema）
                def _validate_against_schema(data: Any, schema: Dict[str, Any]) -> bool:
                    """对结构化输出进行JSON Schema校验（严格模式）。"""
                    try:
                        from jsonschema import validate, Draft202012Validator
                        Draft202012Validator.check_schema(schema)
                        validate(instance=data, schema=schema)
                        return True
                    except Exception:
                        return False

                if output_schema and structured_output is not None:
                    inner_schema = enhanced_inner_schema  # 与提交时保持一致
                    is_valid = _validate_against_schema(structured_output, inner_schema)
                    if not is_valid:
                        # 纠错轮次：向LLM说明校验失败，请重新提交（仍使用 submit_result 工具）
                        try:
                            # 基于最后一轮消息重建
                            if loop_result.full_history:
                                last_turn = loop_result.full_history[-1]
                                messages = last_turn["request"]["messages"].copy()
                            else:
                                messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

                            messages.append({
                                "role": "user",
                                "content": "你提交的结果与JSON Schema不一致，请重新调用 submit_result 并在 result 中提供完全符合Schema的JSON。只提交工具，不要额外文本。",
                            })

                            retry_response = await self._call_with_strict_retry(
                                effective_llm_call,
                                {
                                    "messages": messages,
                                    "tools": available_actionset.get_openai_actions_schema(),
                                    "tool_choice": {"type": "function", "function": {"name": "submit_result"}},
                                },
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
                        except Exception as e:
                            logger.error(f"Error in correction round for agent {self.id}: {e}")

                # 5.4. 若仍无有效结构化结果，进行第二种强制方式（JSON前缀fallback）
                if output_schema and (structured_output is None or not _validate_against_schema(structured_output, enhanced_inner_schema)):
                    try:
                        # 不保留第一种强制轮次的历史，只基于现有对话追加两条消息：user+assistant（assistant为JSON前缀）
                        base_messages = []
                        if loop_result.full_history:
                            last_turn = loop_result.full_history[-1]
                            base_messages = last_turn["request"]["messages"].copy()
                        else:
                            base_messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

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

                        fallback_response = await effective_llm_call({
                            "messages": base_messages,
                            "tools": None,
                            "tool_choice": None,
                        })

                        # 从响应中截取第一个'{'到最后一个'}'，用json_repair解析
                        content = fallback_response.get("content") or ""
                        if isinstance(content, str) and "{" in content and "}" in content:
                            start = content.find("{")
                            end = content.rfind("}")
                            json_str = content[start : end + 1]
                            import json_repair
                            candidate = json_repair.loads(json_str)
                            if _validate_against_schema(candidate, enhanced_inner_schema):
                                structured_output = candidate
                    except Exception as e:
                        logger.error(f"Error in JSON-prefix fallback for agent {self.id}: {e}")

            # 6. 处理结果与写入记忆
            performative_output = loop_result.phases.get("Reflection", "")
            raw_loop_output = {
                "phases": loop_result.phases,
                "phases_unknown": loop_result.phases_unknown,
                "full_history": loop_result.full_history,
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

            has_output = bool(performative_output) or any(bool(v) for v in loop_result.phases.values())

            # 写入记忆（如果启用且可用）
            if save_memory and self._memory:
                try:

                    extraction_result = {"success": False, "memories": [], "error": None}
                    if extract_memory and has_output:
                        extraction_result = await perform_memory_extraction(loop_result, effective_llm_call)
                        loop_result.memory_extraction_enabled = True
                        loop_result.memory_extraction_success = extraction_result.get("success", False)
                        loop_result.extracted_memories = extraction_result.get("memories", [])
                        loop_result.memory_extraction_error = extraction_result.get("error")

                    if extraction_result.get("success") and extraction_result.get("memories"):
                        mem_entries = []
                        for mem in extraction_result["memories"]:
                            mem_entries.append(
                                {
                                    "memory_type": "episodic",
                                    "content": mem.get("content", ""),
                                    "timestamp": current_step,
                                    "importance": mem.get("importance"),
                                    "metadata": {
                                        "instruction": instruction,
                                        "extraction_method": "structured_extract",
                                        "agent_id": self.id,
                                        "model_type": loop_result.model_type,
                                        "has_reasoning": loop_result.has_reasoning,
                                    },
                                }
                            )

                        memory_ids = await self._memory.add_memories_batch(mem_entries, fire_and_forget=True)

                        for mem_id, mem in zip(memory_ids, mem_entries):
                            memory_summary = summarize_text(mem.get("content", ""))
                            self._log(
                                "INFO",
                                AgentEvent.MEMORY_WRITTEN,
                                **{
                                    LogField.STEP.value: current_step,
                                    LogField.MEMORY_ID.value: mem_id,
                                    LogField.MEMORY_CONTENT_PREVIEW.value: memory_summary["preview"],
                                    LogField.MEMORY_CONTENT_LENGTH.value: memory_summary["length"],
                                },
                            )
                    elif has_output:
                        # 改进的fallback：第一人称+过程描述，长度上限1000字符
                        def _build_fallback_memory() -> str:
                            interaction_summary = build_interaction_summary(loop_result)
                            parts = [
                                f"我接到的指令：{instruction}",
                                f"我的过程：{interaction_summary}",
                                f"最终结果：{performative_output or '无结果'}",
                            ]
                            fallback_text = "\n".join([p for p in parts if p])
                            return fallback_text[:1000]

                        fallback_content = _build_fallback_memory()

                        mem_entries = [
                            {
                                "memory_type": "episodic",
                                "content": fallback_content,
                                "timestamp": current_step,
                                "importance": 3.0,
                                "metadata": {
                                    "instruction": instruction,
                                    "extraction_method": "fallback_first_person",
                                    "agent_id": self.id,
                                    "model_type": loop_result.model_type,
                                    "has_reasoning": loop_result.has_reasoning,
                                },
                            }
                        ]

                        memory_ids = await self._memory.add_memories_batch(mem_entries, fire_and_forget=True)

                        memory_summary = summarize_text(fallback_content)
                        if memory_ids:
                            self._log(
                                "INFO",
                                AgentEvent.MEMORY_WRITTEN,
                                **{
                                    LogField.STEP.value: current_step,
                                    LogField.MEMORY_ID.value: memory_ids[0],
                                    LogField.MEMORY_CONTENT_PREVIEW.value: memory_summary["preview"],
                                    LogField.MEMORY_CONTENT_LENGTH.value: memory_summary["length"],
                                },
                            )
                    # 若无输出则跳过记忆写入
                except Exception as e:
                    logger.warning(f"Failed to save memory for agent {self.id}: {e}")
                    instruction_preview = summarize_text(instruction, limit=120)
                    self._log(
                        "WARNING",
                        AgentEvent.ACTION_FAILED,
                        **{
                            LogField.STEP.value: current_step,
                            LogField.ACTION.value: "memory_write",
                            LogField.ERROR.value: str(e),
                            LogField.ACTION_PARAMS.value: {
                                "instruction_preview": instruction_preview["preview"],
                            },
                        },
                    )

            # 情绪模型更新（可选实现）
            # TODO: 将performative_output传递给情绪模型，更新self.state['emotion']

            return {
                "status": "success",
                "agent_id": self.id,
                "instruction": instruction,
                "performative_output": performative_output,
                "structured_output": structured_output,
                "raw_output": raw_loop_output,
                "total_turns": loop_result.total_turns,
                "finish_instruction_called": finish_instruction_called,  # 现在正确实现了
                "stages_executed": list(loop_result.phases.keys()),
                "memory_retrieved": retrieve_memory,
                "memory_saved": save_memory,
                "actions_available": len(available_actionset.actions) if available_actionset else 0,
                "has_structured_output": bool(structured_output),
                "output_schema_provided": output_schema is not None,
                # 新增推理相关信息
                "reasoning_content": loop_result.reasoning_content,
                "thinking_process": loop_result.thinking_process,
                "has_reasoning": loop_result.has_reasoning,
                "model_type": loop_result.model_type,
                # 记忆提取信息
                "memory_extraction_enabled": extract_memory and save_memory and has_output,
                "memory_extraction_success": getattr(loop_result, "memory_extraction_success", False),
                "extracted_memories": getattr(loop_result, "extracted_memories", []),
                "memory_extraction_error": getattr(loop_result, "memory_extraction_error", None),
            }

        except Exception as e:
            logger.error(f"Error in instruction execution for agent {self.id}: {e}")
            return {
                "status": "error",
                "agent_id": self.id,
                "instruction": instruction,
                "error": str(e),
                "performative_output": f"执行指令时发生错误: {str(e)}",
                "structured_output": None,
                "raw_output": None,
                "total_turns": 0,
                "finish_instruction_called": False,
                "has_structured_output": False,
                "output_schema_provided": output_schema is not None,
                # 推理相关信息 - 错误情况下为空
                "reasoning_content": None,
                "thinking_process": [],
                "has_reasoning": False,
                "model_type": None
            }

    async def interview(self,
                       question: str,
                       context: Optional[Dict[str, Any]] = None,
                       current_step: Optional[int] = None,
                       output_schema: Optional[Dict[str, Any]] = None,
                       reasoning_stages: Optional[List[Dict[str, Any]]] = None,
                       llm_call_override: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None) -> Dict[str, Any]:
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
            retrieve_memory=True,  # 允许读取记忆
            save_memory=False,     # 禁止写入记忆
            output_schema=output_schema,
            reasoning_stages=reasoning_stages,
            terminal_action_names=["submit_result"],
            llm_call_override=llm_call_override
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
        save_memory: bool,
        extract_memory: bool,
    ) -> str:
        """根据输出需求与记忆策略生成文本"""
        requirements: List[str] = []

        if output_schema:
            try:
                schema_text = json.dumps(output_schema, ensure_ascii=False, indent=2)
            except Exception:
                schema_text = str(output_schema)
            requirements.append(
                "- 结构化输出：调用 finish_instruction/submit_result，并遵循以下 Schema：\n"
                f"{schema_text}"
            )
        else:
            requirements.append("- 结构化输出：可使用自然语言给出完整答案。")

        if save_memory:
            if extract_memory:
                requirements.append("- 记忆策略：系统会从你的回答中提取记忆，请使用第一人称，描述具体行为与感受。")
            else:
                requirements.append("- 记忆策略：回答将直接写入记忆，请使用第一人称并提供可回溯的细节。")
        else:
            requirements.append("- 记忆策略：本次回答不会写入记忆。")

        return "\n".join(requirements)

    def _format_prompt_section(self, title: str, content: str) -> str:
        """统一的用户提示块渲染"""
        clean_content = (content or "").strip()
        if not clean_content:
            return ""
        clean_title = title.strip() if title and title.strip() else "信息"
        return f"[{clean_title}]\n{clean_content}"

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
