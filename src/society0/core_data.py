"""
SimEngine V2: Core data structures following the unified state architecture.

This module defines the fundamental data structures:
- World: Single source of truth for simulation state (renamed from WorldState)
- Environment: Pure data container with proxy support
- Agent: Agent data structure with proxy support
- ExecutionContext: Standardized execution context
- BaseOperatorResult: Standardized operator return contract
"""

from typing import Dict, Any, List, Optional, Union, TYPE_CHECKING, Callable, Set, Tuple
import importlib
import contextvars
from dataclasses import dataclass, field
import copy
import logging
import time
from abc import ABC

# Import new unified state architecture components
from .state_proxy import DictProxy
from .context_stack import ContextStack
from .transaction import TransactionManager, EventLogger
from .async_utils import invoke_maybe_async
from .logging import AgentEvent, LogField, summarize_text

if TYPE_CHECKING:
    from .legacy.schedule import Schedule, StepFlow, StepNode
    from .agent import Agent
    from .environment import Environment
    from .logging import ExperimentLogContext

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


def _capability_table_entry_matches(key: Any, entry: Dict[str, Any], name: str) -> bool:
    """Match capability registry entries by key, canonical id, display name, or function name."""
    target = str(name)
    meta = entry.get("meta")
    aliases: Set[str] = set()
    for value in (
        key,
        entry.get("canonical_id"),
        entry.get("display_name"),
        entry.get("func_name"),
        entry.get("name"),
    ):
        if value is None:
            continue
        text = str(value)
        aliases.add(text)
        if "." in text:
            aliases.add(text.rsplit(".", maxsplit=1)[-1])
    if meta is not None:
        for attr in ("canonical_id", "name", "func_name"):
            value = getattr(meta, attr, None)
            if value is None:
                continue
            text = str(value)
            aliases.add(text)
            if "." in text:
                aliases.add(text.rsplit(".", maxsplit=1)[-1])
    if target in aliases:
        return True
    lowered = target.lower()
    return lowered in {alias.lower() for alias in aliases}


@dataclass
class BaseOperatorResult:
    """
    Base class for all operator return values, establishing a clear data contract.

    This standardizes the interface between operators and converters, ensuring
    reliable data flow throughout the schedule execution pipeline.
    """
    agent_id: str
    status: str  # "success", "error", "partial"
    value: Any  # Core return value with flexible type
    error_message: Optional[str] = None
    execution_time: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class InstructOperatorResult(BaseOperatorResult):
    """
    Specialized result for instruct operator execution.

    Extends BaseOperatorResult with instruct-specific fields while maintaining
    the standard contract.
    """
    performative_output: str = ""
    structured_output: Optional[Dict] = None
    phases: Dict = field(default_factory=dict)
    total_turns: int = 0
    fovs_used: List[str] = field(default_factory=list)
    actions_taken: List[Dict] = field(default_factory=list)
    finish_instruction_called: bool = False

    def __init__(self, agent_id: str, status: str, value: Any = None, **kwargs):
        """Initialize with automatic value generation if not provided."""
        # Extract additional fields from kwargs
        performative_output = kwargs.pop('performative_output', "")
        structured_output = kwargs.pop('structured_output', None)
        phases = kwargs.pop('phases', {})
        total_turns = kwargs.pop('total_turns', 0)
        fovs_used = kwargs.pop('fovs_used', [])
        actions_taken = kwargs.pop('actions_taken', [])
        finish_instruction_called = kwargs.pop('finish_instruction_called', False)

        # Set instruct-specific fields first
        self.performative_output = performative_output
        self.structured_output = structured_output
        self.phases = phases
        self.total_turns = total_turns
        self.fovs_used = fovs_used
        self.actions_taken = actions_taken
        self.finish_instruction_called = finish_instruction_called

        # Generate value if not provided, otherwise use provided value
        if value is None:
            value = {
                "performative_output": self.performative_output,
                "structured_output": self.structured_output,
                "phases": self.phases,
                "total_turns": self.total_turns,
                "fovs_used": self.fovs_used,
                "actions_taken": self.actions_taken,
                "finish_instruction_called": self.finish_instruction_called
            }

        # Initialize parent class with the value (either provided or generated)
        super().__init__(agent_id=agent_id, status=status, value=value, **kwargs)


@dataclass(frozen=True)
class ExecutionContext:
    """Standardized execution context for all registered functions.

    Provides comprehensive context information while maintaining clear separation
    between static parameters and dynamic runtime context.
    """
    world: 'World'                   # Reference to current world state (renamed from WorldState)
    step: Optional['StepFlow']       # Reference to current StepFlow instance (None for direct calls)
    node: Optional['StepNode']       # Reference to current StepNode instance (None for direct calls)
    caller: Union['Schedule', 'Agent', 'Environment', str]  # Entity that initiated this function call
    event_logger: Optional['EventLogger'] = None  # Reference to event logger for traceability
    log_context: Optional['ExperimentLogContext'] = None  # Structured logging context
    operator_id: Optional[str] = None  # 当前正在执行的 operator 标识（若有）
    action_call_id: Optional[str] = None  # 当前 LLM 工具调用的内部编号

    @property
    def step_number(self) -> int:
        """Get current step number from world state."""
        return self.world.step

    @property
    def node_inputs(self) -> Dict[str, Any]:
        """Get inputs from upstream nodes (empty dict if no node context)."""
        if self.node is None:
            return {}
        return getattr(self.node, 'inputs', {})

    def log_event(self, event_type: str, source: str, data: Dict[str, Any]) -> None:
        """Log an event if event logger is available.

        Args:
            event_type: Type of event
            source: Source of the event
            data: Event data
        """
        if self.event_logger:
            # Create a simple action event for logging
            from .events import BaseEvent

            # Create an ActionEvent that extends BaseEvent
            class ActionEvent(BaseEvent):
                def __init__(self, event_type: str, source: str, data: Dict[str, Any], context_stack: List[Dict[str, Any]] = None):
                    super().__init__(event_type=event_type, context_stack=context_stack or [])
                    self.source = source
                    self.event_data = data

                def to_dict(self) -> Dict[str, Any]:
                    result = super().to_dict()
                    result.update({
                        'source': self.source,
                        'event_data': self.event_data
                    })
                    return result

            # Get context stack if available
            context_stack = []
            if hasattr(self.world, 'get_context_stack'):
                try:
                    context_stack = self.world.get_context_stack().to_list()
                except:
                    context_stack = []

            event = ActionEvent(event_type, source, data, context_stack)
            self.event_logger.write_event(event)

    def log_schedule(self, level: str, event: str, **payload: Any) -> None:
        """Convenience wrapper for记录调度日志。"""
        if self.log_context:
            self.log_context.log_schedule(level, event, **payload)

    def log_agent(self, agent_id: str, level: str, event: str, **payload: Any) -> None:
        """Convenience wrapper for记录 Agent 日志。"""
        if self.log_context:
            self.log_context.log_agent(agent_id, level, event, **payload)

    def log_env(self, level: str, event: str, **payload: Any) -> None:
        """Convenience wrapper for记录环境日志。"""
        if self.log_context:
            self.log_context.log_env(level, event, **payload)

    def log_resource(self, resource_type: str, level: str, event: str, **payload: Any) -> None:
        """Convenience wrapper for记录资源调用日志。"""
        if self.log_context:
            self.log_context.log_resource(resource_type, level, event, **payload)


class World:
    """
    The single source of truth for all simulation state in the unified architecture.

    This class replaces the old WorldState and serves as the central data repository.
    It manages all agents, environment data, and provides proxy-based access for
    transparent state change tracking.

    Key features:
    - Holds all real data (agents_data, environment_data)
    - Provides proxy-based access through Agent and Environment objects
    - Integrates with transaction system for automatic event recording
    - Supports dependency injection for agent/environment creation
    """

    def __init__(
        self,
        step: int = 0,
        event_log_path: str = "events.jsonl",
        event_logger: Optional[EventLogger] = None,
        transaction_manager: Optional[TransactionManager] = None
    ):
        """
        Initialize the World with empty state

        Args:
            step: Initial step number
            event_log_path: Path for event logging
            event_logger: 外部注入的事件记录器
            transaction_manager: 外部注入的事务管理器
        """
        self.step = step

        # Real data storage - the source of truth
        self.agents_data: Dict[str, Dict[str, Any]] = {}
        self.environment_data: Dict[str, Any] = {
            "type": "base",
            "state": {}
        }

        # Agent and Environment object caches
        self._agent_cache: Dict[str, 'Agent'] = {}
        self._environment_cache: Optional['Environment'] = None

        # 🔑 方案 A: Environment state 单例代理（确保所有修改都被记录）
        self._environment_state_proxy: Optional[DictProxy] = None

        # Transaction and event management
        self.event_logger = event_logger or EventLogger(event_log_path)
        self.transaction_manager = transaction_manager or TransactionManager(self.event_logger)

        # Current context stack (will be set by Schedule). The context var is
        # task-local, preventing concurrent agent actions from inheriting each
        # other's action frames while preserving a synchronous fallback stack.
        self._current_context_stack: Optional[ContextStack] = None
        self._context_stack_var: contextvars.ContextVar[Optional[ContextStack]] = contextvars.ContextVar(
            f"society0_context_stack_{id(self)}",
            default=None,
        )
        self._log_context: Optional['ExperimentLogContext'] = None

        # Dependency injection - these will be set by SimEngine
        self._persistence_manager: Optional[Any] = None
        self._function_registry: Optional[Any] = None
        self._llm_manager: Optional[Any] = None
        self._embedding_manager: Optional[Any] = None
        self._model_provider: Optional[Any] = None
        self._environment_factory: Optional[Callable[["World"], "Environment"]] = None
        # 内部缓存：已发现的环境类型元数据（仅首次访问时构建）
        self._env_meta_cache: Optional[Dict[str, Any]] = None
        # 节点差异调度器，由 SimEngine 在组合根注入
        self._node_diff_dispatcher: Optional[Any] = None
        # 状态版本号：每次状态写入都会递增，用于缓存控制
        self._state_version: int = 0
        # FoV 结果缓存：按 step / 按 agent 的临时缓存，避免重复调用
        self._fov_cache_step: Dict[str, Any] = {}
        self._fov_cache_agent: Dict[tuple[str, str], Any] = {}

    def set_context_stack(self, context_stack: ContextStack):
        """Set the current context stack (called by Schedule/StepFlow)"""
        self._current_context_stack = context_stack
        self._context_stack_var.set(context_stack)

    def get_context_stack(self) -> ContextStack:
        """Get the current context stack"""
        return self._context_stack_var.get() or self._current_context_stack or ContextStack()

    def _create_event_recorder(self):
        """Create event recorder callback for proxies"""
        def record_event(event_dict):
            # Convert simple event dict to proper event object
            from .events import StateChangeEvent

            change = event_dict.get("change", {})
            event = StateChangeEvent(
                target_type="agent" if change.get("path", [None])[0] == "agents" else "environment",
                target_id=change.get("path", [None, None])[1] if len(change.get("path", [])) > 1 else "global",
                path=change.get("path", [])[2:] if len(change.get("path", [])) > 2 else change.get("path", []),
                operation=change.get("operation", "unknown"),
                value=change.get("value"),
                old_value=change.get("old_value"),
                context_stack=self.get_context_stack().to_list()
            )
            new_version = self._bump_state_version()
            event.metadata["state_version"] = new_version

            # Record in current transaction if available
            if self.transaction_manager.has_active_transaction():
                self.transaction_manager.record_event(event)
            else:
                # Direct logging if no transaction (should be rare)
                self.event_logger.write_event(event)

        return record_event

    def _create_context_provider(self):
        """Create context provider callback for proxies"""
        return lambda: self.get_context_stack().to_list()

    def get_state_version(self) -> int:
        """获取当前世界状态版本号。"""
        return self._state_version

    def _bump_state_version(self) -> int:
        """状态变更后调用，递增版本号并返回新值。"""
        self._state_version += 1
        return self._state_version

    def _build_execution_context(
        self,
        caller: Union['Schedule', 'Agent', 'Environment', str],
        *,
        step: Optional['StepFlow'] = None,
        node: Optional['StepNode'] = None,
    ) -> ExecutionContext:
        """构造标准 ExecutionContext，统一包含当前 world 引用。"""
        from .agent.agent_loop import current_action_call_id

        return ExecutionContext(
            world=self,
            step=step,
            node=node,
            caller=caller,
            event_logger=self.event_logger,
            log_context=self._log_context,
            action_call_id=current_action_call_id(),
        )

    # Agent management methods

    def add_agent_data(self, agent_id: str, agent_type: str, archetype: str = "llm"):
        """
        Add raw agent data to the world

        Args:
            agent_id: Unique agent identifier
            agent_type: Agent's social type
            archetype: Agent's execution archetype (llm, rule)
        """
        if agent_id in self.agents_data:
            raise ValueError(f"Agent with ID '{agent_id}' already exists")

        self.agents_data[agent_id] = {
            "id": agent_id,
            "type": agent_type,
            "archetype": archetype,
            "state": {},
            "properties": {},
            "reminders": []
        }

        logger.debug(f"Added agent data for '{agent_id}' (type: {agent_type}, archetype: {archetype})")

    def set_log_context(self, log_context: Optional['ExperimentLogContext']) -> None:
        """注入实验级日志上下文。"""
        self._log_context = log_context
        if hasattr(self, "transaction_manager") and self.transaction_manager:
            try:
                self.transaction_manager.set_log_context(log_context)
            except AttributeError:
                pass

    def get_log_context(self) -> Optional['ExperimentLogContext']:
        """获取当前实验日志上下文。"""
        return self._log_context

    def _log_agent(self, agent_id: str, level: str, event: str, **payload: Any) -> None:
        if self._log_context:
            self._log_context.log_agent(agent_id, level, event, **payload)

    def _log_env(self, level: str, event: str, **payload: Any) -> None:
        if self._log_context:
            self._log_context.log_env(level, event, **payload)

    def _log_resource(self, resource_type: str, level: str, event: str, **payload: Any) -> None:
        if self._log_context:
            self._log_context.log_resource(resource_type, level, event, **payload)

    @staticmethod
    def _extract_actions_preview_from_result(result: Dict[str, Any], limit: int = 5) -> List[str]:
        """从指令执行结果中提取动作名称摘要。"""
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
    def _collect_structured_output_keys(structured_output: Any) -> List[str]:
        """收集结构化输出中的键集合。"""
        if isinstance(structured_output, dict):
            return sorted(structured_output.keys())
        if isinstance(structured_output, list):
            keys = set()
            for item in structured_output:
                if isinstance(item, dict):
                    keys.update(item.keys())
            return sorted(keys)
        return []

    @staticmethod
    def _extract_instruction_metrics(result: Dict[str, Any]) -> Tuple[Optional[int], Optional[int], Optional[int]]:
        """提取指令执行的关键统计信息。"""
        actions_value = result.get("actions")
        actions_count = len(actions_value) if isinstance(actions_value, list) else None

        usage_info = result.get("usage") or result.get("token_usage") or {}
        total_tokens = result.get("total_tokens")
        if total_tokens is None and isinstance(usage_info, dict):
            total_tokens = usage_info.get("total_tokens") or usage_info.get("total")

        llm_calls = result.get("llm_calls")
        if llm_calls is None:
            stats_block = result.get("stats") or {}
            if isinstance(stats_block, dict):
                llm_calls = stats_block.get("llm_calls")

        return actions_count, llm_calls, total_tokens

    def _log_instruction_start(
        self,
        agent_id: str,
        step_number: int,
        instruction_text: str,
        *,
        fovs: Optional[List[str]],
        action_tags: Optional[List[str]],
        model_id: Optional[str],
        option_keys: Optional[List[str]],
        interaction_type: str,
    ) -> None:
        """记录指令/访谈开始日志。"""
        payload: Dict[str, Any] = {
            LogField.STEP.value: step_number,
            LogField.INSTRUCTION.value: instruction_text,
            LogField.INSTRUCTION_LENGTH.value: len(instruction_text or ""),
            LogField.FOVS.value: fovs or [],
            LogField.ACTION_TAGS.value: action_tags or [],
            LogField.INTERACTION_TYPE.value: interaction_type,
        }
        if model_id:
            payload[LogField.MODEL.value] = model_id
        if option_keys:
            payload[LogField.OPTIONS_KEYS.value] = option_keys
        self._log_agent(agent_id, "INFO", AgentEvent.AGENT_INSTRUCTED.value, **payload)

    def _log_instruction_completion(
        self,
        agent_id: str,
        step_number: int,
        *,
        execution_duration: float,
        result: Dict[str, Any],
        actions_count: Optional[int],
        llm_calls: Optional[int],
        total_tokens: Optional[int],
        interaction_type: str,
    ) -> None:
        """记录指令/访谈完成日志。"""
        payload: Dict[str, Any] = {
            LogField.STEP.value: step_number,
            LogField.DURATION_SEC.value: execution_duration,
            LogField.STATUS.value: result.get("status", "success"),
            LogField.INTERACTION_TYPE.value: interaction_type,
        }
        if actions_count is not None:
            payload[LogField.ACTIONS_TAKEN.value] = actions_count
        if llm_calls is not None:
            payload[LogField.LLM_CALLS.value] = llm_calls
        if total_tokens is not None:
            payload[LogField.TOTAL_TOKENS.value] = total_tokens
        self._log_agent(agent_id, "INFO", AgentEvent.AGENT_TURN_COMPLETED.value, **payload)

    def _log_instruction_decision(
        self,
        agent_id: str,
        step_number: int,
        instruction_text: str,
        *,
        result: Dict[str, Any],
        total_tokens: Optional[int],
        llm_calls: Optional[int],
        interaction_type: str,
        action_name: str,
    ) -> None:
        """记录指令/访谈决策与详情。"""
        decision_summary = summarize_text(result.get("performative_output", ""))
        structured_keys = self._collect_structured_output_keys(result.get("structured_output"))
        actions_preview = self._extract_actions_preview_from_result(result)

        payload: Dict[str, Any] = {
            LogField.STEP.value: step_number,
            LogField.STATUS.value: result.get("status", "success"),
            LogField.DECISION_PREVIEW.value: decision_summary["preview"],
            LogField.DECISION_LENGTH.value: decision_summary["length"],
            LogField.INTERACTION_TYPE.value: interaction_type,
        }
        if actions_preview:
            payload[LogField.ACTIONS_PREVIEW.value] = actions_preview
        if structured_keys:
            payload[LogField.STRUCTURED_OUTPUT_KEYS.value] = structured_keys
        stages_executed = result.get("stages_executed")
        if isinstance(stages_executed, list) and stages_executed:
            payload[LogField.STAGES_EXECUTED.value] = stages_executed
        if total_tokens is not None:
            payload[LogField.TOTAL_TOKENS.value] = total_tokens
        if llm_calls is not None:
            payload[LogField.LLM_CALLS.value] = llm_calls
        if result.get("error"):
            payload[LogField.ERROR.value] = result["error"]

        structured_output = result.get("structured_output")
        if structured_output is not None:
            payload[LogField.STRUCTURED_OUTPUT.value] = structured_output

        self._log_agent(agent_id, "INFO", AgentEvent.AGENT_DECISION.value, **payload)

        if decision_summary["truncated"]:
            debug_payload = {
                LogField.STEP.value: step_number,
                LogField.DECISION_FULL.value: decision_summary["full"],
                LogField.INTERACTION_TYPE.value: interaction_type,
            }
            self._log_agent(
                agent_id,
                "DEBUG",
                AgentEvent.AGENT_DECISION.value,
                **debug_payload,
            )

        if result.get("status") == "error":
            instruction_preview = summarize_text(instruction_text, limit=120)
            error_payload = {
                LogField.STEP.value: step_number,
                LogField.ACTION.value: action_name,
                LogField.INTERACTION_TYPE.value: interaction_type,
                LogField.ACTION_PARAMS.value: {
                    "instruction_preview": instruction_preview["preview"],
                },
                LogField.ERROR.value: result.get("error", "unknown_error"),
            }
            self._log_agent(
                agent_id,
                "ERROR",
                AgentEvent.ACTION_FAILED.value,
                **error_payload,
            )

    def get_agent(self, agent_id: str) -> 'Agent':
        """
        Get an Agent object (with proxy support) for the given ID

        Args:
            agent_id: Agent identifier

        Returns:
            Agent object with proxy-enabled state access

        Raises:
            KeyError: If agent doesn't exist
        """
        if agent_id not in self.agents_data:
            raise KeyError(f"Agent '{agent_id}' not found")

        # Use cache to avoid recreating Agent objects
        if agent_id not in self._agent_cache:
            # Get archetype to determine agent class
            archetype = self.agents_data[agent_id]["archetype"]

            # Import agent classes here to avoid circular imports
            from .agent.core import Agent, LLMAgent, RuleAgent

            if archetype == "llm":
                self._agent_cache[agent_id] = LLMAgent(agent_id, self)
            elif archetype == "rule":
                self._agent_cache[agent_id] = RuleAgent(agent_id, self)
            else:
                # Default to base Agent class
                self._agent_cache[agent_id] = Agent(agent_id, self)

        return self._agent_cache[agent_id]

    def get_all_agents(self) -> List['Agent']:
        """Get all Agent objects"""
        return [self.get_agent(agent_id) for agent_id in self.agents_data.keys()]

    def get_agents_by_type(self, agent_type: str) -> List['Agent']:
        """Get all agents of a specific social type"""
        return [
            self.get_agent(agent_id)
            for agent_id, data in self.agents_data.items()
            if data["type"] == agent_type
        ]

    def get_agents_by_archetype(self, archetype: str) -> List['Agent']:
        """Get all agents of a specific archetype"""
        return [
            self.get_agent(agent_id)
            for agent_id, data in self.agents_data.items()
            if data["archetype"] == archetype
        ]

    def remove_agent(self, agent_id: str):
        """Remove an agent from the world"""
        if agent_id not in self.agents_data:
            raise KeyError(f"Agent '{agent_id}' not found")

        # Remove from data and cache
        del self.agents_data[agent_id]
        if agent_id in self._agent_cache:
            del self._agent_cache[agent_id]

        logger.debug(f"Removed agent '{agent_id}'")

    # Environment management methods

    def set_environment_factory(
        self,
        factory: Callable[["World"], "Environment"],
    ) -> None:
        """Inject an external Environment factory before the environment is created."""
        if self._environment_cache is not None:
            raise RuntimeError("Environment factory must be set before the environment is created")
        if not callable(factory):
            raise TypeError("Environment factory must be callable")
        self._environment_factory = factory

    def get_environment(self) -> 'Environment':
        """
        Get the Environment object (with proxy support)

        Returns:
            Environment object with proxy-enabled state access
        """
        if self._environment_cache is None:
            if self._environment_factory is not None:
                from .environment import Environment

                environment = self._environment_factory(self)
                if not isinstance(environment, Environment):
                    raise TypeError("Environment factory must return an Environment instance")
                self._environment_cache = environment
                agents = self.get_all_agents()
                self._environment_cache.initialize(agents, self)
                self.initialize_env_provided_fields()
                logger.info(
                    "Created and initialized externally injected %s environment",
                    environment.__class__.__name__,
                )
                return self._environment_cache

            # Get environment type from configuration
            env_type = self.environment_data.get("type", "base")

            # 1) 使用内置发现机制解析环境类
            env_class = None
            try:
                from .env_discovery import discover_builtins  # type: ignore
                if self._env_meta_cache is None:
                    self._env_meta_cache = discover_builtins()
                meta = self._env_meta_cache.get(env_type) if self._env_meta_cache else None
                if meta is not None:
                    module_name, _, class_name = meta.class_path.rpartition(".")
                    if module_name:
                        module = importlib.import_module(module_name)
                        env_class = getattr(module, class_name, None)
            except Exception:
                env_class = None

            # 2) 回退到旧的内置注册表
            if env_class is None:
                try:
                    from .env import BUILTIN_ENVS  # type: ignore
                    env_class = BUILTIN_ENVS.get(env_type)
                except Exception:
                    env_class = None

            # 3) 最终回退：基础 Environment
            if env_class is None:
                from .environment import Environment  # Import here to avoid circular imports
                self._environment_cache = Environment(self)
                logger.warning(f"Environment type '{env_type}' not registered, using base Environment")
            else:
                self._environment_cache = env_class(self)
                agents = self.get_all_agents()
                self._environment_cache.initialize(agents, self)
                # 🔑 v3.0: 初始化 Environment 提供的字段
                self.initialize_env_provided_fields()
                logger.info(f"Created and initialized {env_class.__name__} environment for type '{env_type}'")

        return self._environment_cache

    def set_environment_type(self, env_type: str):
        """Set the environment type"""
        self.environment_data["type"] = env_type

    # Step management

    def advance_step(self):
        """Advance to the next step"""
        self.step += 1
        # 切换步骤时清理 step 级 FoV 缓存
        self._fov_cache_step = {}
        logger.debug(f"Advanced to step {self.step}")

    # Utility methods

    def get_state_summary(self) -> Dict[str, Any]:
        """Get a summary of current world state"""
        return {
            "step": self.step,
            "agent_count": len(self.agents_data),
            "agent_types": list(set(data["type"] for data in self.agents_data.values())),
            "agent_archetypes": list(set(data["archetype"] for data in self.agents_data.values())),
            "environment_type": self.environment_data["type"]
        }

    def create_agent_state_proxy(self, agent_id: str, state_key: str) -> DictProxy:
        """
        Create a state proxy for a specific agent and state key

        Args:
            agent_id: Agent identifier
            state_key: State key ('state', 'properties', 'reminders')

        Returns:
            DictProxy for the agent's state
        """
        if agent_id not in self.agents_data:
            raise KeyError(f"Agent '{agent_id}' not found")

        target_dict = self.agents_data[agent_id][state_key]
        if not isinstance(target_dict, dict):
            raise ValueError(f"Agent {agent_id}.{state_key} is not a dictionary")

        return DictProxy(
            target_dict=target_dict,
            event_recorder=self._create_event_recorder(),
            context_provider=self._create_context_provider(),
            path=("agents", agent_id, state_key)
        )

    def create_environment_state_proxy(self) -> DictProxy:
        """
        Create a state proxy for environment state (Singleton pattern)

        🔑 方案 A: 返回单例代理实例，确保所有 environment state 修改都被同一个
        代理拦截和记录。这样可以防止直接访问绕过事件记录系统。

        Returns:
            DictProxy for environment state (singleton instance)
        """
        if self._environment_state_proxy is None:
            self._environment_state_proxy = DictProxy(
                target_dict=self.environment_data["state"],
                event_recorder=self._create_event_recorder(),
                context_provider=self._create_context_provider(),
                path=("environment", "state")
            )
            logger.debug("Created singleton environment state proxy")
        return self._environment_state_proxy

    # Transaction integration

    def get_transaction_manager(self) -> TransactionManager:
        """Get the transaction manager"""
        return self.transaction_manager

    def get_event_logger(self) -> EventLogger:
        """Get the event logger"""
        return self.event_logger

    # Diff dispatcher 注入/访问
    def set_node_diff_dispatcher(self, dispatcher: Optional[Any]) -> None:
        """注入节点差异调度器。"""
        self._node_diff_dispatcher = dispatcher

    def get_node_diff_dispatcher(self) -> Optional[Any]:
        """获取节点差异调度器。"""
        return self._node_diff_dispatcher

    # =========================================================================
    # v3.0: Schema 管理和初始化
    # =========================================================================

    def get_agent_type_schema(self, agent_type: str) -> Optional[Dict[str, Any]]:
        """
        获取指定 Agent 类型的 state schema

        这个 schema 是从外部配置（agent_set）中读取的。
        SimEngine 在初始化时会将 agent_types 存储到 world._agent_types 中。

        Args:
            agent_type: Agent 类型名称

        Returns:
            Agent 的 state_schema，如果未找到返回 None
        """
        # 从外部提供的 agent_types 配置中读取
        if not hasattr(self, '_agent_types') or not self._agent_types:
            # 如果没有外部配置，返回空 schema（默认所有字段可见可编辑）
            return {
                "type": "object",
                "properties": {}
            }

        type_meta = self._agent_types.get(agent_type)
        if not isinstance(type_meta, dict):
            return {
                "type": "object",
                "properties": {}
            }

        # 返回 state_schema（如果存在）
        state_schema = type_meta.get('state_schema')
        if isinstance(state_schema, dict):
            return state_schema

        # 回退到空 schema
        return {
            "type": "object",
            "properties": {}
        }

    def get_environment_managed_fields_schema(self) -> Dict[str, Any]:
        """
        获取当前 Environment 管理的字段 schema

        从 Environment 的元数据中获取 agent_managed_fields_schema。

        Returns:
            Environment 提供的字段 schema
        """
        if not self._environment_cache:
            return {"type": "object", "properties": {}}

        # 从已发现的元数据中获取
        env_type = self.environment_data.get("type")
        if self._env_meta_cache and env_type in self._env_meta_cache:
            env_meta = self._env_meta_cache[env_type]
            return env_meta.agent_managed_fields_schema

        return {"type": "object", "properties": {}}

    def get_merged_agent_state_schema(self, agent_type: str) -> Dict[str, Any]:
        """
        获取合并后的 Agent state schema

        合并规则：
        1. 先获取 Agent 自己的 state_schema
        2. 再获取 Environment 提供的 agent_managed_fields_schema
        3. Env 的字段会覆盖 Agent 的同名字段

        Args:
            agent_type: Agent 类型名称

        Returns:
            合并后的 schema
        """
        merged = {
            "type": "object",
            "properties": {}
        }

        # 1. Agent 自己的 schema
        agent_schema = self.get_agent_type_schema(agent_type)
        if agent_schema:
            agent_properties = agent_schema.get('state_schema', {}).get('properties', {})
            # 应用默认值增强
            from .meta import enrich_state_schema_with_defaults
            agent_enriched = enrich_state_schema_with_defaults(
                {"type": "object", "properties": agent_properties},
                is_agent_owned=True
            )
            merged['properties'].update(agent_enriched.get('properties', {}))

        # 2. Environment 提供的 schema
        env_schema = self.get_environment_managed_fields_schema()
        if env_schema:
            env_properties = env_schema.get('properties', {})
            # 应用默认值增强
            from .meta import enrich_state_schema_with_defaults
            env_enriched = enrich_state_schema_with_defaults(
                {"type": "object", "properties": env_properties},
                is_agent_owned=False
            )
            # 🔑 Env 的字段覆盖 Agent 的同名字段
            merged['properties'].update(env_enriched.get('properties', {}))

        return merged

    def initialize_env_provided_fields(self):
        """
        为所有 Agent 初始化 Environment 提供的状态字段

        在 Environment 创建后调用，根据 env_meta.agent_managed_fields_schema
        为每个 Agent 的 state 添加字段并填充默认值。
        """
        env_schema = self.get_environment_managed_fields_schema()
        if not env_schema or not env_schema.get('properties'):
            logger.debug("No environment-managed fields to initialize")
            return

        properties = env_schema.get('properties', {})
        initialized_count = 0

        for agent_id, agent_data in self.agents_data.items():
            if 'state' not in agent_data:
                agent_data['state'] = {}

            state = agent_data['state']

            for field_name, field_def in properties.items():
                # 🔑 覆盖策略：Env 的字段直接覆盖
                # 填充默认值
                default_value = self._get_default_from_schema(field_def)
                state[field_name] = default_value
                initialized_count += 1

        logger.info(f"Initialized {initialized_count} environment-managed fields "
                   f"across {len(self.agents_data)} agents "
                   f"({len(properties)} fields per agent)")

    def _get_default_from_schema(self, field_schema: Dict[str, Any]) -> Any:
        """从 JSON Schema 中提取默认值"""
        if 'default' in field_schema:
            return field_schema['default']

        # 根据类型生成默认值
        field_type = field_schema.get('type', 'string')
        type_defaults = {
            'string': '',
            'number': 0,
            'integer': 0,
            'boolean': False,
            'array': [],
            'object': {}
        }
        return type_defaults.get(field_type, None)

    def _resolve_fov_entry(self, name: str) -> Optional[Dict[str, Any]]:
        """根据 canonical id 或短名解析 FoV 函数元数据。"""

        if not hasattr(self, "_function_registry") or not self._function_registry:
            return None

        entry = self._function_registry.env_fovs.get(name)
        if entry is not None:
            return entry

        if isinstance(name, str) and name:
            short_name = name.rsplit(".", maxsplit=1)[-1]
            if short_name != name:
                return self._function_registry.env_fovs.get(short_name)

        return None

    def _fov_not_found_message(self, name: str) -> str:
        """Build a researcher-friendly error when a requested FoV is unavailable."""
        base_message = f"FoV function '{name}' not found"
        registry = getattr(self, "_function_registry", None)
        if registry is None:
            return base_message

        action_registry = getattr(registry, "env_agent_tools", {}) or {}
        candidate_names = [name]
        if isinstance(name, str) and name:
            short_name = name.rsplit(".", maxsplit=1)[-1]
            if short_name not in candidate_names:
                candidate_names.append(short_name)

        for candidate in candidate_names:
            if candidate in action_registry:
                return (
                    f"'{name}' is an environment action, not a FoV. "
                    f"Use actions=['{candidate}'] in instruct(...), or let the agent call it as a tool; "
                    "do not put it in fovs=[...]."
                )

        available_fovs = sorted(
            key
            for key in getattr(registry, "env_fovs", {}).keys()
            if isinstance(key, str) and not key.startswith("environments.")
        )
        if available_fovs:
            return f"{base_message}. Available FoVs include: {', '.join(available_fovs[:10])}"
        return base_message

    def _validate_action_filter(
        self,
        agent: Any,
        action_tags: Optional[List[str]],
        *,
        required_action_names: Optional[List[str]] = None,
        required_action_tags: Optional[List[str]] = None,
    ) -> None:
        """Fail early when action filters make required tool behavior impossible."""
        actionset = getattr(agent, "_actionset", None)
        if actionset is None:
            actionset = self.assemble_agent_actionset(agent)
        if action_tags is None:
            filtered_actionset = actionset.filter_by_tags(exclude_tags=["memory"])
        else:
            filtered_actionset = actionset.filter_by_tags(action_tags=action_tags)

        if action_tags is not None and len(action_tags) > 0 and not filtered_actionset.actions:
            raise ValueError(self._action_filter_not_found_message(action_tags, actionset))

        missing_required_actions = [
            name
            for name in (required_action_names or [])
            if not self._actionset_has_action_name(filtered_actionset, str(name))
        ]
        missing_required_tags = [
            tag
            for tag in (required_action_tags or [])
            if not self._actionset_has_action_tag(filtered_actionset, str(tag))
        ]
        if missing_required_actions or missing_required_tags:
            raise ValueError(
                self._required_action_constraints_not_available_message(
                    filtered_actionset,
                    action_tags=action_tags,
                    missing_required_actions=missing_required_actions,
                    missing_required_tags=missing_required_tags,
                )
            )

    def _action_filter_not_found_message(self, action_tags: List[str], actionset: Any) -> str:
        requested = [str(tag) for tag in action_tags]
        requested_text = "[" + ", ".join(repr(tag) for tag in requested) + "]"
        parts = [f"Action filter {requested_text} matched no available actions."]
        registry = getattr(self, "_function_registry", None)
        if registry is not None:
            for token in requested:
                matching_kinds = self._capability_kinds_matching_name(token, exclude_kind="action")
                if not matching_kinds:
                    continue
                for kind in matching_kinds:
                    if kind == "fov":
                        parts.append(
                            f"'{token}' is a FoV, not an action. Use fovs=['{token}'] for context."
                        )
                    elif kind == "rule":
                        parts.append(f"'{token}' is a rule, not an action. Use ctx.rule('{token}', ...).")
                    elif kind == "behavior":
                        parts.append(
                            f"'{token}' is a behavior, not an action. Use ctx.behavior(...) or AgentGroup.behavior(...)."
                        )
        available = self._available_action_filter_tokens(actionset)
        if available:
            parts.append(f"Available action names/tags include: {', '.join(available[:16])}.")
        parts.append("Use actions=[...] only for actions or action tags exposed to the LLM tool loop.")
        return " ".join(parts)

    def _required_action_constraints_not_available_message(
        self,
        filtered_actionset: Any,
        *,
        action_tags: Optional[List[str]],
        missing_required_actions: List[str],
        missing_required_tags: List[str],
    ) -> str:
        parts = []
        filter_text = "None" if action_tags is None else "[" + ", ".join(repr(str(tag)) for tag in action_tags) + "]"
        if missing_required_actions:
            parts.append(
                "Required action(s) "
                + ", ".join(repr(str(name)) for name in missing_required_actions)
                + f" are not available after applying actions={filter_text}."
            )
        if missing_required_tags:
            parts.append(
                "Required action tag(s) "
                + ", ".join(repr(str(tag)) for tag in missing_required_tags)
                + f" are not available after applying actions={filter_text}."
            )
        for token in [*missing_required_actions, *missing_required_tags]:
            matching_kinds = self._capability_kinds_matching_name(str(token), exclude_kind="action")
            for kind in matching_kinds:
                if kind == "fov":
                    parts.append(f"'{token}' is a FoV, not an action; use fovs=['{token}'].")
                elif kind == "rule":
                    parts.append(f"'{token}' is a rule, not an action; use ctx.rule('{token}', ...).")
                elif kind == "behavior":
                    parts.append(
                        f"'{token}' is a behavior, not an action; use ctx.behavior(...) or AgentGroup.behavior(...)."
                    )
        available = self._available_action_filter_tokens(filtered_actionset)
        if available:
            parts.append(f"Available filtered action names/tags include: {', '.join(available[:16])}.")
        else:
            parts.append("The filtered action set is empty.")
        parts.append(
            "Align required_actions/required_action_tags with the actions exposed to the LLM tool loop."
        )
        return " ".join(parts)

    @staticmethod
    def _actionset_has_action_name(actionset: Any, name: str) -> bool:
        target = name.lower()
        for action_name in getattr(actionset, "actions", {}):
            aliases = {str(action_name).lower()}
            if "." in str(action_name):
                aliases.add(str(action_name).rsplit(".", maxsplit=1)[-1].lower())
            if target in aliases:
                return True
        return False

    @staticmethod
    def _actionset_has_action_tag(actionset: Any, tag: str) -> bool:
        target = tag.lower()
        for action_name, action_info in getattr(actionset, "actions", {}).items():
            tokens = {str(action_name).lower()}
            if "." in str(action_name):
                tokens.add(str(action_name).rsplit(".", maxsplit=1)[-1].lower())
            tokens.update(str(item).lower() for item in (action_info.get("tags", []) or []))
            if target in tokens:
                return True
        return False

    def _capability_kinds_matching_name(self, name: str, *, exclude_kind: Optional[str] = None) -> List[str]:
        registry = getattr(self, "_function_registry", None)
        if registry is None:
            return []
        tables = (
            ("fov", getattr(registry, "env_fovs", {}) or {}),
            ("action", getattr(registry, "env_agent_tools", {}) or {}),
            ("rule", getattr(registry, "rules", {}) or {}),
            ("behavior", getattr(registry, "behaviors", {}) or {}),
        )
        matches: List[str] = []
        for kind, table in tables:
            if kind == exclude_kind:
                continue
            if any(_capability_table_entry_matches(key, entry, name) for key, entry in table.items()):
                matches.append(kind)
        return matches

    @staticmethod
    def _available_action_filter_tokens(actionset: Any) -> List[str]:
        tokens: List[str] = []
        for action_name, action_info in getattr(actionset, "actions", {}).items():
            tokens.append(str(action_name))
            if "." in str(action_name):
                tokens.append(str(action_name).rsplit(".", maxsplit=1)[-1])
            for tag in action_info.get("tags", []) or []:
                tokens.append(str(tag))
        return sorted(dict.fromkeys(token for token in tokens if token))

    def _build_fov_cache_key(self, fov_name: str, params: Dict[str, Any]) -> str:
        """Build a cache key for a FoV invocation, excluding injected context."""
        if not params:
            return fov_name
        try:
            filtered = {key: value for key, value in params.items() if key != "context"}
            return f"{fov_name}|{sorted(filtered.items(), key=lambda item: item[0])}"
        except Exception:
            return fov_name

    async def _collect_fov_results(
        self,
        *,
        agent_id: str,
        agent: Any,
        fovs: Optional[List[str]],
        step_number: int,
    ) -> Dict[str, Any]:
        """Execute FoVs for an agent and record consistent success/failure logs."""
        if not fovs:
            return {}

        import asyncio

        environment = self.get_environment()

        async def _run_fov(fov_name: str):
            try:
                if not (hasattr(self, "_function_registry") and self._function_registry):
                    raise RuntimeError("Function registry not available")

                fov_func = self._resolve_fov_entry(fov_name)
                if not fov_func:
                    raise RuntimeError(self._fov_not_found_message(fov_name))

                call_kwargs: Dict[str, Any] = {}
                meta = fov_func.get("meta")
                if meta and getattr(meta, "context_parameter_name", None):
                    call_kwargs.setdefault(
                        meta.context_parameter_name,
                        self._build_execution_context(caller=agent),
                    )

                cache_key = self._build_fov_cache_key(fov_name, call_kwargs)
                if meta and getattr(meta, "cache_on_step", False):
                    if cache_key in self._fov_cache_step:
                        return fov_name, self._fov_cache_step[cache_key], True
                if meta and getattr(meta, "cache_on_agent", False):
                    agent_key = (agent_id, cache_key)
                    if agent_key in self._fov_cache_agent:
                        return fov_name, self._fov_cache_agent[agent_key], True

                state_version_before = self.get_state_version()
                fov_result = await invoke_maybe_async(
                    fov_func["function"],
                    agent,
                    environment,
                    **call_kwargs,
                )
                state_version_after = self.get_state_version()

                if (
                    meta
                    and (
                        getattr(meta, "cache_on_step", False)
                        or getattr(meta, "cache_on_agent", False)
                    )
                    and state_version_after != state_version_before
                ):
                    logger.warning(
                        "[FoV CACHE WARNING] FoV '%s' for agent %s has cache enabled "
                        "but mutated state (state_version %s -> %s). "
                        "Consider disabling FoV cache or removing side effects.",
                        fov_name,
                        agent_id,
                        state_version_before,
                        state_version_after,
                    )

                if meta and getattr(meta, "cache_on_step", False):
                    self._fov_cache_step[cache_key] = fov_result
                if meta and getattr(meta, "cache_on_agent", False):
                    self._fov_cache_agent[(agent_id, cache_key)] = fov_result

                return fov_name, fov_result, False

            except Exception as exc:
                error_message = str(exc)
                logger.error("Error executing FoV '%s' for agent %s: %s", fov_name, agent_id, exc)
                return fov_name, {"error": error_message}, False

        fov_results: Dict[str, Any] = {}
        fov_pairs = await asyncio.gather(*[_run_fov(fov_name) for fov_name in fovs])
        for fov_name, fov_result, from_cache in fov_pairs:
            fov_results[fov_name] = fov_result
            if isinstance(fov_result, dict) and "error" in fov_result:
                self._log_agent(
                    agent_id,
                    "ERROR",
                    "fov_failed",
                    step=step_number,
                    fov_name=fov_name,
                    error=fov_result["error"],
                )
                continue

            serialized = ""
            if fov_result is not None:
                serialized = fov_result if isinstance(fov_result, str) else str(fov_result)
            preview = serialized[:200] + ("..." if len(serialized) > 200 else "")
            self._log_agent(
                agent_id,
                "INFO",
                "fov_executed",
                step=step_number,
                fov_name=fov_name,
                fov_result_preview=preview,
                fov_result_length=len(serialized),
                from_cache=from_cache,
            )
            if serialized and getattr(self, "_log_full_fov_results", False):
                self._log_agent(
                    agent_id,
                    "DEBUG",
                    "fov_full_result",
                    step=step_number,
                    fov_name=fov_name,
                    fov_result=serialized,
                    from_cache=from_cache,
                )

        return fov_results

    # Agent instruction delegation

    async def instruct_agent(self,
                            agent_id: str,
                            instruction: str,
                            fovs: Optional[List[str]] = None,
                            action_tags: Optional[List[str]] = None,
                            current_step: Optional[int] = None,
                            reasoning_stages: Optional[List[Dict[str, Any]]] = None,
                            extra_notes: Optional[List[str]] = None,
                            model_id: Optional[str] = None,
                            **kwargs) -> Dict[str, Any]:
        """
        Instruct a specific agent with FoV data collection and action filtering

        This method serves as the bridge between _instruct_operator and agent.instruct().
        It handles FoV data collection, then delegates the actual instruction execution
        to the agent's instruct method.

        Args:
            agent_id: Agent identifier
            instruction: Instruction text
            fovs: List of FoV function names to execute
            action_tags: Action tags for filtering available actions
            current_step: Current step number for memory retrieval
            reasoning_stages: Optional reasoning stages configuration to override agent's default
            **kwargs: Additional parameters passed to agent.instruct()

        Returns:
            Dictionary with instruction execution results
        """
        # Get agent instance
        agent = self.get_agent(agent_id)
        step_number = current_step if current_step is not None else self.step
        self._validate_action_filter(
            agent,
            action_tags,
            required_action_names=kwargs.get("required_action_names"),
            required_action_tags=kwargs.get("required_action_tags"),
        )

        fov_collection_started = time.time()
        fov_results = await self._collect_fov_results(
            agent_id=agent_id,
            agent=agent,
            fovs=fovs,
            step_number=step_number,
        )
        fov_collection_duration = time.time() - fov_collection_started

        # Prepare context for agent instruction
        context = {"fov_results": fov_results} if fov_results else {}
        if extra_notes:
            context["extra_notes"] = list(extra_notes)
        context.update(kwargs.get("context", {}))
        call_name = kwargs.get("name")

        llm_override_call = None
        resolved_model_id = None
        if self._model_provider is not None:
            selection = self._resolve_model_selection(agent_id, model_id)
            if selection is not None:
                resolved_model_id = selection.model_id
                llm_override_call = selection.runtime.llm_call

        interaction_type = "instruct"
        option_keys = sorted(kwargs.keys()) if kwargs else []
        selected_model_id = resolved_model_id or model_id
        self._log_instruction_start(
            agent_id,
            step_number,
            instruction,
            fovs=fovs or [],
            action_tags=action_tags or [],
            model_id=selected_model_id,
            option_keys=option_keys,
            interaction_type=interaction_type,
        )

        execution_start = time.time()

        result = await agent.instruct(
            instruction=instruction,
            context=context,
            current_step=current_step or self.step,
            action_tags=action_tags,
            reasoning_stages=reasoning_stages,
            llm_call_override=llm_override_call,
            trace={
                "step": step_number,
                "step_name": getattr(self, "_current_code_step_name", None),
                "interaction_type": interaction_type,
                "interaction_name": call_name or "instruction",
            },
            **{k: v for k, v in kwargs.items() if k not in {"context", "name"}}
        )
        execution_duration = time.time() - execution_start

        if resolved_model_id is not None:
            result.setdefault("model_id", resolved_model_id)
        if fovs:
            phase_timings = result.setdefault("phase_timings", {})
            if isinstance(phase_timings, dict):
                phase_timings["fov_collection"] = round(fov_collection_duration, 6)

        actions_count, llm_calls, total_tokens = self._extract_instruction_metrics(result)
        self._log_instruction_completion(
            agent_id,
            step_number,
            execution_duration=execution_duration,
            result=result,
            actions_count=actions_count,
            llm_calls=llm_calls,
            total_tokens=total_tokens,
            interaction_type=interaction_type,
        )

        self._log_instruction_decision(
            agent_id,
            step_number,
            instruction,
            result=result,
            total_tokens=total_tokens,
            llm_calls=llm_calls,
            interaction_type=interaction_type,
            action_name=call_name or "instruction",
        )

        return result

    async def interview_agent(self,
                            agent_id: str,
                            question: str,
                            fovs: Optional[List[str]] = None,
                            current_step: Optional[int] = None,
                            reasoning_stages: Optional[List[Dict[str, Any]]] = None,
                            extra_notes: Optional[List[str]] = None,
                            model_id: Optional[str] = None,
                            **kwargs) -> Dict[str, Any]:
        """
        专用访谈方法 - 从架构层面保证访谈纯净性

        特点：
        1. 硬编码action_tags=[]，禁止所有动作（除了finish_instruction）
        2. 硬编码save_memory=False，禁止写入记忆
        3. 允许retrieve_memory=True，可以读取现有记忆
        4. 支持FoV数据收集，以便提供充分的上下文信息

        Args:
            agent_id: Agent identifier
            question: 访谈问题
            fovs: List of FoV function names to execute
            current_step: Current step number for memory retrieval
            reasoning_stages: Optional reasoning stages configuration to override agent's default
            **kwargs: Additional parameters (context, output_schema等)

        Returns:
            Dictionary with interview results
        """
        # Get agent instance
        agent = self.get_agent(agent_id)
        step_number = current_step if current_step is not None else self.step

        fov_collection_started = time.time()
        fov_results = await self._collect_fov_results(
            agent_id=agent_id,
            agent=agent,
            fovs=fovs,
            step_number=step_number,
        )
        fov_collection_duration = time.time() - fov_collection_started

        # Prepare context for agent interview
        context = {"fov_results": fov_results} if fov_results else {}
        if extra_notes:
            context["extra_notes"] = list(extra_notes)
        context.update(kwargs.get("context", {}))
        call_name = kwargs.get("name")

        # 🔧 CRITICAL: 硬编码安全参数以确保访谈纯净性
        llm_override_call = None
        resolved_model_id = None
        if self._model_provider is not None:
            selection = self._resolve_model_selection(agent_id, model_id)
            if selection is not None:
                resolved_model_id = selection.model_id
                llm_override_call = selection.runtime.llm_call
        interaction_type = "interview"
        option_keys = sorted(kwargs.keys()) if kwargs else []
        selected_model_id = resolved_model_id or model_id
        self._log_instruction_start(
            agent_id,
            step_number,
            question,
            fovs=fovs or [],
            action_tags=[],
            model_id=selected_model_id,
            option_keys=option_keys,
            interaction_type=interaction_type,
        )

        execution_start = time.time()

        result = await agent.interview(
            question=question,
            context=context,
            current_step=current_step or self.step,
            output_schema=kwargs.get("output_schema"),
            reasoning_stages=reasoning_stages,
            llm_call_override=llm_override_call,
            trace={
                "step": step_number,
                "step_name": getattr(self, "_current_code_step_name", None),
                "interaction_type": interaction_type,
                "interaction_name": call_name or "interview",
            },
            retrieve_memory=kwargs.get("retrieve_memory", True),
            save_memory=kwargs.get("save_memory", False),
            memory_top_k=kwargs.get("memory_top_k", 10),
            prefer_direct_json_output=kwargs.get("prefer_direct_json_output", False),
            max_turns=kwargs.get("max_turns", 2),
            llm_request_options=kwargs.get("llm_request_options"),
        )
        execution_duration = time.time() - execution_start
        if resolved_model_id is not None:
            result.setdefault("model_id", resolved_model_id)
        if fovs:
            phase_timings = result.setdefault("phase_timings", {})
            if isinstance(phase_timings, dict):
                phase_timings["fov_collection"] = round(fov_collection_duration, 6)

        actions_count, llm_calls, total_tokens = self._extract_instruction_metrics(result)
        self._log_instruction_completion(
            agent_id,
            step_number,
            execution_duration=execution_duration,
            result=result,
            actions_count=actions_count,
            llm_calls=llm_calls,
            total_tokens=total_tokens,
            interaction_type=interaction_type,
        )
        self._log_instruction_decision(
            agent_id,
            step_number,
            question,
            result=result,
            total_tokens=total_tokens,
            llm_calls=llm_calls,
            interaction_type=interaction_type,
            action_name=call_name or "interview",
        )

        return result

    def set_function_registry(self, function_registry):
        """Set function registry for FoV function lookup"""
        self._function_registry = function_registry

    def get_logic_provider(self):
        """
        Get the logic provider (function registry) for accessing registered logics.

        This method is used by Agent.call_behavior() and Agent.call_action() to
        lookup and execute registered logic functions.

        Returns:
            FunctionRegistry instance containing all registered behaviors, actions, rules, and fovs

        Raises:
            RuntimeError: If function registry has not been initialized
        """
        if self._function_registry is None:
            raise RuntimeError(
                "Function registry not initialized. "
                "Ensure SimEngine has properly initialized World with set_function_registry()."
            )
        return self._function_registry

    def set_persistence_manager(self, persistence_manager):
        """Set persistence manager for vector store client access"""
        self._persistence_manager = persistence_manager

    def set_resource_managers(self, llm_manager=None, embedding_manager=None):
        """
        Set resource managers for LLM and Embedding calls

        Args:
            llm_manager: LLMManager instance for LLM API calls
            embedding_manager: EmbeddingManager instance for embedding calls
        """
        if llm_manager:
            self._llm_call = llm_manager.request
            if hasattr(llm_manager, "set_log_context"):
                llm_manager.set_log_context(self._log_context)
            logger.debug("Injected LLMManager.request as _llm_call")

        if embedding_manager:
            self._embed_call = embedding_manager.request
            self._embedding_dim = int(getattr(embedding_manager, "default_dimensions", 512) or 512)
            if hasattr(embedding_manager, "set_log_context"):
                embedding_manager.set_log_context(self._log_context)
            logger.debug("Injected EmbeddingManager.request as _embed_call")

    def set_model_provider(self, model_provider):
        """注入模型提供者以支持按模型选择 LLM 调用。"""
        self._model_provider = model_provider
        # 兼容 runtime_models 下每模型独立 LLMManager 的调用路径，
        # 统一挂接实验日志上下文，确保 llm/embedding trace 完整。
        if self._model_provider is not None and self._log_context is not None:
            models_map = getattr(self._model_provider, "_models", None)
            if isinstance(models_map, dict):
                for runtime in models_map.values():
                    llm_call = getattr(runtime, "llm_call", None)
                    owner = getattr(llm_call, "__self__", None)
                    if owner is not None and hasattr(owner, "set_log_context"):
                        try:
                            owner.set_log_context(self._log_context)
                        except Exception:
                            logger.debug("Failed to bind log context for model provider runtime", exc_info=True)
        logger.debug("Model provider injected into World")

    def _resolve_model_selection(self, agent_id: str, operator_model_id: Optional[str]) -> Optional[Any]:
        if self._model_provider is None:
            return None

        provider = self._model_provider
        agent_record = self.agents_data.get(agent_id, {})
        candidate_ids: List[Optional[str]] = []

        if isinstance(operator_model_id, str) and operator_model_id.strip():
            candidate_ids.append(operator_model_id.strip())

        agent_model = agent_record.get("model")
        if isinstance(agent_model, str) and agent_model.strip():
            candidate_ids.append(agent_model.strip())

        type_id = agent_record.get("type")
        if type_id:
            type_meta = getattr(self, "_agent_types", {}).get(type_id, {})
            type_model = type_meta.get("model")
            if isinstance(type_model, str) and type_model.strip():
                candidate_ids.append(type_model.strip())

        default_model_id = None
        if hasattr(provider, "get_default_model_id"):
            try:
                default_model_id = provider.get_default_model_id()
            except Exception:  # pragma: no cover - 防御
                default_model_id = None
        candidate_ids.append(default_model_id)

        for candidate in candidate_ids:
            if not candidate:
                continue
            try:
                if provider.has_model(candidate):
                    return provider.get(candidate)
            except Exception as exc:  # pragma: no cover - 防御
                logger.debug("Failed to resolve model '%s' for agent %s: %s", candidate, agent_id, exc)

        try:
            return provider.get(None)
        except Exception as exc:  # pragma: no cover - 防御
            logger.warning("Falling back to default model failed for agent %s: %s", agent_id, exc)
            return None

    def assemble_agent_actionset(self, agent):
        """
        Assemble ActionSet for a specific LLMAgent

        This method collects Actions from various sources and returns a complete ActionSet.
        It serves as the central point for Action/Behavior assembly.

        Args:
            agent: LLMAgent instance to assemble actions for

        Returns:
            ActionSet: Assembled ActionSet with all available actions
        """
        from .agent.agent_loop import ActionSet

        # Create empty ActionSet
        actionset = ActionSet()

        # Add memory system actions (if available)
        try:
            if hasattr(agent, '_memory') and agent._memory:
                memory_actions = self._get_memory_actions(agent._memory)

                # 注释掉详细的调试信息
                # print(f"--- [DEBUG] Actions from Memory system ---")
                # print(f"Found {len(memory_actions)} memory actions: {list(memory_actions.keys())}")
                # for action_name, action_info in memory_actions.items():
                #     print(f"  Memory Action '{action_name}': tags={action_info.get('tags', [])}")

                for action_name, action_info in memory_actions.items():
                    actionset.add_action(
                        name=action_name,
                        func=action_info["function"],
                        description=action_info["description"],
                        parameters=action_info["parameters"],
                        tags=action_info.get("tags", ["memory"])
                    )
                logger.debug(f"Added {len(memory_actions)} memory actions for agent {agent.id}")
            else:
                # print(f"--- [DEBUG] No memory system available for agent {agent.id} ---")
                logger.debug(f"No memory system available for agent {agent.id}, skipping memory actions")
        except Exception as e:
            # print(f"--- [DEBUG] Error loading memory actions: {e} ---")
            logger.warning(f"Failed to load memory actions for agent {agent.id}: {e}")

        # Add environment actions (from current environment)
        try:
            environment = self.get_environment()
            env_actions = self._get_environment_actions(environment, agent)  # Pass agent parameter

            # 注释掉详细的调试信息
            # print(f"--- [DEBUG] Actions from Env '{getattr(environment, 'type', 'unknown')}' ---")
            # print(f"Found {len(env_actions)} actions: {list(env_actions.keys())}")
            # for action_name, action_info in env_actions.items():
            #     print(f"  Action '{action_name}': tags={action_info.get('tags', [])}")

            for action_name, action_info in env_actions.items():
                # 自动补全标签：包含显式标签、动作名称及环境标识，便于 action_tags 匹配
                base_tags = action_info.get("tags", [])
                implicit_tags = {action_name, "environment"}
                implicit_tags.add(f"env.{action_name}")
                if "." in action_name:
                    implicit_tags.add(action_name.split(".")[-1])
                env_tags = list({*base_tags, *implicit_tags})

                actionset.add_action(
                    name=action_name,
                    func=action_info["function"],
                    description=action_info["description"],
                    parameters=action_info["parameters"],
                    tags=env_tags
                )
            logger.debug(f"Added {len(env_actions)} environment actions for agent {agent.id}")
        except Exception as e:
            logger.warning(f"Failed to load environment actions for agent {agent.id}: {e}")

        # Add registry-based actions (from function registry)
        try:
            if hasattr(self, '_function_registry') and self._function_registry:
                registry_env_actions = self._get_registry_env_actions(agent)
                for action_name, action_info in registry_env_actions.items():
                    actionset.add_action(
                        name=action_name,
                        func=action_info["function"],
                        description=action_info["description"],
                        parameters=action_info["parameters"],
                        tags=action_info.get("tags", ["environment", "experiment"]),
                    )
                registry_actions = self._get_registry_actions(agent)
                for action_name, action_info in registry_actions.items():
                    actionset.add_action(
                        name=action_name,
                        func=action_info["function"],
                        description=action_info["description"],
                        parameters=action_info["parameters"],
                        tags=action_info.get("tags", ["registry"])
                    )
                logger.debug(f"Added {len(registry_actions)} registry actions for agent {agent.id}")
        except Exception as e:
            logger.warning(f"Failed to load registry actions for agent {agent.id}: {e}")

        logger.info(f"Assembled ActionSet for agent {agent.id}: {len(actionset.actions)} total actions")
        return actionset

    def _get_memory_actions(self, memory):
        """Get actions provided by the memory system"""
        memory_actions = {}

        # Create wrapped async functions for memory operations
        async def remember_action(**kwargs):
            content = kwargs.get("content", "")
            timestamp = kwargs.get("timestamp", self.step)
            importance = kwargs.get("importance", 3.0)
            metadata = kwargs.get("metadata", {})

            memory_id = await memory.add_episodic_memory(
                content=content,
                timestamp=timestamp,
                importance=importance,
                metadata=metadata
            )
            return f"Stored memory with ID: {memory_id}"

        async def recall_action(**kwargs):
            query = kwargs.get("query", "")
            top_k = kwargs.get("top_k", 5)
            current_step = kwargs.get("current_step", self.step)

            memories = await memory.retrieve(
                query=query,
                top_k=top_k,
                current_step=current_step
            )
            return {"memories": memories, "count": len(memories)}

        memory_actions["remember"] = {
            "function": remember_action,
            "description": "Store new information in episodic memory",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Content to remember"},
                    "importance": {"type": "number", "description": "Importance score 0-5", "default": 3.0},
                    "metadata": {"type": "object", "description": "Additional metadata"}
                },
                "required": ["content"]
            },
            "tags": ["memory", "storage"]
        }

        memory_actions["recall"] = {
            "function": recall_action,
            "description": "Retrieve relevant memories",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query"},
                    "top_k": {"type": "integer", "description": "Number of results", "default": 5}
                },
                "required": ["query"]
            },
            "tags": ["memory", "retrieval"]
        }

        return memory_actions

    def _get_environment_actions(self, environment, agent):
        """
        从环境实例获取 actions（v3.0: 从 capabilities 获取）

        通过 inspect 分析函数签名，自动区分系统参数和用户参数：
        - 系统参数（如 ExecutionContext）会被自动注入
        - 用户参数会被暴露给 LLM 的 schema 中
        - 创建智能闭包函数处理参数映射

        Args:
            environment: Environment 实例
            agent: Agent 实例 that will use these actions
        """
        import inspect
        from .decorators import ENV_META_ATTR

        env_actions = {}

        # 🔑 v3.0: 从环境类的元数据中获取 capabilities
        env_meta = getattr(environment.__class__, ENV_META_ATTR, None)

        if env_meta is None:
            logger.warning(f"Environment {environment.__class__.__name__} has no {ENV_META_ATTR}")
            return env_actions

        if not hasattr(env_meta, 'capabilities'):
            logger.warning(f"EnvironmentMeta for {environment.__class__.__name__} has no capabilities")
            return env_actions

        # 遍历 capabilities，筛选出 kind='action' 的
        for cap_meta in env_meta.capabilities:
            if cap_meta.kind != 'action':
                continue

            # 🔑 从环境**实例**获取绑定的方法
            if not hasattr(environment, cap_meta.func_name):
                logger.warning(f"Environment instance missing method {cap_meta.func_name}")
                continue

            bound_method = getattr(environment, cap_meta.func_name)

            # 使用 cap_meta.parameters_schema（已经去除了约定参数）
            action_schema = cap_meta.parameters_schema

            # 🔧 智能绑定：分析函数签名识别系统参数
            sig = inspect.signature(bound_method)
            system_params = {}
            user_params = {}

            for param_name, param in sig.parameters.items():
                if param_name == 'self':
                    continue  # Skip self parameter
                elif param_name == 'context' or param.annotation == ExecutionContext:
                    # This is a system parameter - needs auto-injection
                    system_params[param_name] = ExecutionContext
                elif param_name == 'agent':
                    # 框架自动注入当前 Agent
                    system_params[param_name] = "agent"
                else:
                    # This is a user parameter - expose to LLM
                    user_params[param_name] = param

            # 创建智能 wrapper 函数
            def create_intelligent_wrapper(env_func, action_name_local, sys_params, usr_params, schema):
                async def intelligent_action(**kwargs):
                    # 🔧 Step 1: Prepare system parameters
                    injected_params = {}
                    for sys_param_name, sys_param_type in sys_params.items():
                        if sys_param_type == ExecutionContext:
                            injected_params[sys_param_name] = ExecutionContext(
                                world=self,
                                step=None,
                                node=None,
                                caller=agent,
                                event_logger=self.event_logger,
                                log_context=self._log_context
                            )
                        elif sys_param_type == "agent":
                            injected_params[sys_param_name] = agent

                    # 🔧 Step 2: Validate and filter user parameters
                    schema_props = schema.get('properties', {})
                    filtered_user_params = {}

                    for param_name in usr_params.keys():
                        if param_name in kwargs:
                            filtered_user_params[param_name] = kwargs[param_name]
                        elif param_name in schema_props and 'default' in schema_props[param_name]:
                            # Use default value from schema
                            filtered_user_params[param_name] = schema_props[param_name]['default']

                    logger.debug(f"🔧 Action '{action_name_local}': injected {list(injected_params.keys())}, user params {list(filtered_user_params.keys())}")

                    # 🔧 Step 3: Combine parameters and call original function
                    all_params = {**injected_params, **filtered_user_params}

                    import asyncio
                    result = env_func(**all_params)
                    if asyncio.iscoroutine(result):
                        return await result
                    else:
                        return result

                return intelligent_action

            env_actions[cap_meta.name] = {
                "function": create_intelligent_wrapper(
                    bound_method,
                    cap_meta.name,
                    system_params,
                    user_params,
                    action_schema
                ),
                "description": cap_meta.description,
                "parameters": action_schema,
                "tags": ["environment"] + cap_meta.tags
            }

            logger.debug(f"🔧 Loaded action '{cap_meta.name}' from capabilities: system={list(system_params.keys())}, user={list(user_params.keys())}")

        logger.info(f"Loaded {len(env_actions)} environment actions from capabilities")
        return env_actions

    def _get_registry_actions(self, agent: 'Agent') -> Dict[str, Dict[str, Any]]:
        """Get actions from the function registry."""
        registry_actions: Dict[str, Dict[str, Any]] = {}

        if not hasattr(self._function_registry, "agent_actions"):
            return registry_actions

        for action_name, action_info in self._function_registry.agent_actions.items():
            original_func = action_info["function"]
            meta = action_info.get("meta")

            if meta is not None:
                def create_logic_action_wrapper(func, logic_meta):
                    async def action_wrapper(**kwargs):
                        call_kwargs = dict(kwargs)
                        context_param = getattr(logic_meta, "context_parameter_name", None)
                        if context_param:
                            call_kwargs.setdefault(
                                context_param,
                                self._build_execution_context(caller=agent),
                            )
                        environment = self.get_environment()
                        return await invoke_maybe_async(func, agent, environment, **call_kwargs)

                    return action_wrapper

                parameters_schema = copy.deepcopy(getattr(meta, "parameters_schema", None)) or {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
                tags: Set[str] = set(getattr(meta, "tags", []) or [])
                tags.update({"registry", "logic"})
                tags.add(action_name)
                short_name = action_name.rsplit(".", maxsplit=1)[-1]
                if short_name:
                    tags.add(short_name)

                registry_actions[action_name] = {
                    "function": create_logic_action_wrapper(original_func, meta),
                    "description": getattr(meta, "description", "") or action_info.get(
                        "description",
                        f"Logic action: {action_name}",
                    ),
                    "parameters": parameters_schema,
                    "tags": sorted(tags),
                }
                continue

            def create_legacy_action_wrapper(func):
                async def action_wrapper(**kwargs):
                    agent_ids = kwargs.get("agent_ids", [])
                    params = {k: v for k, v in kwargs.items() if k != "agent_ids"}
                    return await invoke_maybe_async(func, agent_ids, self, params)

                return action_wrapper

            registry_actions[action_name] = {
                "function": create_legacy_action_wrapper(original_func),
                "description": action_info.get("description", f"Registry action: {action_name}"),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "agent_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of agent IDs to perform action on",
                        }
                    },
                    "required": [],
                    "additionalProperties": True,
                },
                "tags": ["registry", "agent_action"],
            }

        return registry_actions

    def _get_registry_env_actions(self, agent: 'Agent') -> Dict[str, Dict[str, Any]]:
        """Get experiment-specific environment actions from the function registry."""
        registry_actions: Dict[str, Dict[str, Any]] = {}

        if not hasattr(self._function_registry, "env_agent_tools"):
            return registry_actions

        seen: Set[str] = set()
        for action_name, action_info in self._function_registry.env_agent_tools.items():
            if action_info.get("source") != "experiment":
                continue
            canonical_id = str(action_info.get("canonical_id") or action_name)
            if canonical_id in seen:
                continue
            seen.add(canonical_id)

            original_func = action_info["function"]
            signature = action_info.get("signature")

            def create_experiment_env_action_wrapper(func, sig):
                async def action_wrapper(**kwargs):
                    environment = self.get_environment()
                    context = self._build_execution_context(caller=agent)
                    call_kwargs: Dict[str, Any] = {}
                    consumed_user_kwargs: Set[str] = set()
                    var_keyword_name: Optional[str] = None

                    parameters = getattr(sig, "parameters", {}) if sig is not None else {}
                    for param_name, param in parameters.items():
                        if param_name in {"self", "cls"}:
                            continue
                        if param.kind == param.VAR_POSITIONAL:
                            continue
                        if param.kind == param.VAR_KEYWORD:
                            var_keyword_name = param_name
                            continue

                        if param_name == "agent":
                            call_kwargs[param_name] = agent
                        elif param_name == "agents":
                            call_kwargs[param_name] = [agent]
                        elif param_name == "agent_ids":
                            call_kwargs[param_name] = [getattr(agent, "id", None)]
                        elif param_name in {"env", "environment"}:
                            call_kwargs[param_name] = environment
                        elif param_name == "world":
                            call_kwargs[param_name] = self
                        elif param_name == "context":
                            call_kwargs[param_name] = context
                        elif param_name == "params":
                            call_kwargs[param_name] = dict(kwargs)
                        elif param_name in kwargs:
                            call_kwargs[param_name] = kwargs[param_name]
                            consumed_user_kwargs.add(param_name)

                    if var_keyword_name is not None:
                        for key, value in kwargs.items():
                            if key not in consumed_user_kwargs:
                                call_kwargs[key] = value

                    return await invoke_maybe_async(func, **call_kwargs)

                return action_wrapper

            tags: Set[str] = set(action_info.get("tags", []) or [])
            tags.update({
                "environment",
                "experiment",
                canonical_id,
                str(action_info.get("display_name") or action_name),
            })
            if "." in canonical_id:
                tags.add(canonical_id.rsplit(".", maxsplit=1)[-1])

            registry_actions[action_info.get("display_name") or action_name] = {
                "function": create_experiment_env_action_wrapper(original_func, signature),
                "description": action_info.get("description", f"Environment action: {action_name}"),
                "parameters": copy.deepcopy(action_info.get("parameters") or {
                    "type": "object",
                    "properties": {},
                }),
                "tags": sorted(tag for tag in tags if tag),
            }

        return registry_actions

    def initialize_all_cognitive_systems(self,
                                       llm_call: Callable = None,
                                       memory_uri: str = "./chroma_store",
                                       model_provider: Optional[Any] = None,
                                       embedding_dim: Optional[int] = None,
                                       *,
                                       strict: bool = False,
                                       require_memory: bool = False):
        """
        🔧 PERSONA 重构: Initialize cognitive systems for all LLMAgents

        This method should be called by SimEngine after World creation or restoration.
        It handles the complete initialization of all cognitive components for LLM agents.

        按照resource_management_design.md，使用注入的依赖而不是硬编码。

        Args:
            llm_call: LLM calling函数（来自LLMManager）
            memory_uri: 旧版内存存储路径（保留参数）
            model_provider: 模型提供者，用于动态解析模型选择
            embedding_dim: 记忆向量维度，默认来自注入的 EmbeddingManager
            strict: True 时任何 LLM agent 初始化失败都直接抛错
            require_memory: True 时每个 LLM agent 必须成功初始化 memory
        """
        logger.info("Initializing cognitive systems for all LLM agents")
        if embedding_dim is not None:
            self._embedding_dim = int(embedding_dim)

        # Find all LLM agents
        llm_agent_ids = [
            agent_id for agent_id, data in self.agents_data.items()
            if data.get("archetype") == "llm"
        ]

        logger.info(f"Found {len(llm_agent_ids)} LLM agents to initialize: {llm_agent_ids}")

        if model_provider is not None:
            self.set_model_provider(model_provider)

        # 使用注入的LLM函数，如果没有则使用World或模型提供者的默认设置
        effective_llm_call = llm_call or getattr(self, "_llm_call", None)
        if effective_llm_call is None and self._model_provider is not None:
            try:
                default_model_id = self._model_provider.get_default_model_id()
                selection = self._model_provider.get(default_model_id)
                effective_llm_call = selection.runtime.llm_call
            except Exception as exc:  # pragma: no cover - 防御
                logger.warning("Failed to acquire default model from provider: %s", exc)

        if not effective_llm_call:
            message = "No LLM call function available - agents cannot run"
            if strict:
                raise RuntimeError(message)
            logger.warning("%s", message)

        # Initialize each LLM agent
        for agent_id in llm_agent_ids:
            try:
                # Get agent instance (this will return LLMAgent due to archetype)
                llm_agent = self.get_agent(agent_id)

                # Initialize Memory system with injected dependencies FIRST
                memory = self._create_memory_for_agent(
                    agent_id,
                    memory_uri,
                    strict=strict or require_memory,
                )
                if require_memory and memory is None:
                    raise RuntimeError(f"Memory initialization failed for LLM agent '{agent_id}'")

                # 🔧 PERSONA 重构: 获取类型级 persona 与实例级 persona
                type_persona, persona = self._get_persona_for_agent(agent_id)

                # 获取 reasoning_stages 配置（如果存在）
                reasoning_stages = self._get_reasoning_stages_for_agent(agent_id)

                # Initialize the cognitive system with memory and persona FIRST
                llm_agent.initialize_cognitive_system(
                    persona=persona,  # 现在传递字符串而不是字典
                    memory=memory,
                    llm_call=effective_llm_call,
                    actionset=None,  # ActionSet will be set separately
                    reasoning_stages=reasoning_stages,
                    type_persona=type_persona
                )

                # NOW assemble ActionSet (memory is available)
                actionset = self.assemble_agent_actionset(llm_agent)

                # Update the ActionSet in the already initialized agent
                llm_agent._actionset = actionset

                logger.info(f"Successfully initialized cognitive system for agent {agent_id}")

            except Exception as e:
                logger.error(f"Failed to initialize cognitive system for agent {agent_id}: {e}")
                if strict:
                    raise RuntimeError(f"Failed to initialize LLM agent '{agent_id}'") from e
                logger.warning(f"Continuing with other agents despite failure for {agent_id}")
                continue

        logger.info("Completed cognitive system initialization for all LLM agents")

    def _create_memory_for_agent(self, agent_id: str, memory_uri: str, *, strict: bool = False):
        """Create Memory instance for a specific agent"""
        try:
            # Skip memory creation if URI indicates no memory wanted
            if memory_uri == ":memory:" or memory_uri == "none":
                if strict:
                    raise RuntimeError(
                        f"Memory is required for LLM agent '{agent_id}', but memory_uri={memory_uri!r}"
                    )
                logger.debug(f"Skipping memory creation for agent {agent_id} (memory_uri: {memory_uri})")
                return None

            from .agent.memory import Memory

            # 获取注入的向量存储客户端实例
            vector_client = None
            if hasattr(self, '_persistence_manager') and self._persistence_manager:
                try:
                    vector_client = self._persistence_manager.get_chroma_client()
                except Exception as exc:
                    logger.error("Failed to obtain Chroma client from persistence manager: %s", exc)
            else:
                message = f"No PersistenceManager available for agent {agent_id} memory creation"
                if strict:
                    raise RuntimeError(message)
                logger.warning(message)
                return None

            if vector_client is None:
                message = f"No Chroma client available for agent {agent_id} memory creation"
                if strict:
                    raise RuntimeError(message)
                logger.warning("%s; skipping memory creation", message)
                return None

            # Create memory with agent-specific configuration and injected client
            memory = Memory(
                agent_id=agent_id,
                vector_client=vector_client,  # 注入客户端实例
                branch_id="main",  # Default branch
                embedding_dim=int(getattr(self, "_embedding_dim", 512) or 512),
                embed_call=getattr(self, '_embed_call', None),  # 注入embed函数
                llm_call=getattr(self, '_llm_call', None)  # 注入llm函数
            )

            logger.debug(f"Created memory system for agent {agent_id} with injected dependencies")
            return memory

        except Exception as e:
            logger.error(f"Failed to create memory for agent {agent_id}: {e}")
            if strict:
                raise RuntimeError(f"Failed to create memory for LLM agent '{agent_id}'") from e
            logger.warning(f"Continuing without memory for agent {agent_id}")
            return None

    def _get_persona_for_agent(self, agent_id: str) -> Tuple[str, str]:
        """🔧 PERSONA 重构: 分别获取类型级和实例级 persona 字符串"""

        agent_data = self.agents_data.get(agent_id, {})
        type_persona = (agent_data.get('persona_type') or '').strip()
        instance_persona = (agent_data.get('persona_instance') or '').strip()
        stored_persona = (agent_data.get('persona') or '').strip()

        final_instance = instance_persona or stored_persona
        if not final_instance and not type_persona:
            agent_type = agent_data.get('type', 'agent')
            final_instance = f"你是一个 {agent_type} 类型的智能体 {agent_id}，请根据你的角色特点来行动。"

        return type_persona, final_instance

    def _get_reasoning_stages_for_agent(self, agent_id: str) -> Optional[List[Dict[str, Any]]]:
        """
        从 agent 的 type 定义中获取 reasoning_stages 配置。

        查找顺序:
        1. agent 实例级别的覆盖（未来可能支持）
        2. agent type 定义中的 reasoning_stages
        3. 返回 None（使用全局默认值）

        Args:
            agent_id: Agent 标识符

        Returns:
            reasoning_stages 配置列表，如果未定义则返回 None
        """
        # 1. 检查 agent 实例级别的配置（未来可能支持）
        agent_data = self.agents_data.get(agent_id, {})
        if 'reasoning_stages' in agent_data and agent_data['reasoning_stages']:
            logger.debug(f"Using instance-level reasoning_stages for agent {agent_id}")
            return agent_data['reasoning_stages']

        # 2. 从 agent type 定义中获取
        agent_type_id = agent_data.get('type')
        if agent_type_id and hasattr(self, '_agent_types'):
            type_def = self._agent_types.get(agent_type_id)
            if type_def and 'reasoning_stages' in type_def and type_def['reasoning_stages']:
                logger.debug(f"Using type-level reasoning_stages for agent {agent_id} (type: {agent_type_id})")
                return type_def['reasoning_stages']

        # 3. 未定义，返回 None（将使用全局默认值）
        logger.debug(f"No reasoning_stages configured for agent {agent_id}, will use global default")
        return None

    # Event replay mechanism for event sourcing

    def apply_event(self, event):
        """
        Apply an event to restore state during event replay.

        This is the core dispatcher for event sourcing, handling different
        event types during restoration from snapshots + event logs.

        Args:
            event: BaseEvent instance to apply
        """
        from .events import StateChangeEvent, MemoryChangeEvent, NodeExecutionEvent, StepSummaryEvent

        if isinstance(event, StateChangeEvent):
            self._apply_state_change(event)
        elif isinstance(event, MemoryChangeEvent):
            self._apply_memory_change(event)
        elif isinstance(event, NodeExecutionEvent):
            # Node execution events are informational and don't need replay
            logger.debug(f"Skipping NodeExecutionEvent during replay: {event.node_id}")
        elif isinstance(event, StepSummaryEvent):
            # Step summary events are analytical and don't need replay
            # They are used for post-hoc analysis and don't affect world state
            logger.debug(f"Skipping StepSummaryEvent during replay: step {event.step_number}")
        else:
            # Handle unknown event types gracefully
            event_type = getattr(event, 'event_type', type(event).__name__)
            logger.warning(f"Unknown event type during replay: {event_type}")

            # Try to extract basic event info for debugging
            event_details = {
                "event_id": getattr(event, 'event_id', 'unknown'),
                "timestamp": getattr(event, 'timestamp', 'unknown'),
                "context_stack_length": len(getattr(event, 'context_stack', []))
            }
            logger.debug(f"Unknown event details: {event_details}")

    def _apply_state_change(self, event):
        """
        Apply a StateChangeEvent by directly modifying the underlying data.

        This method bypasses the proxy system to avoid generating new events
        during replay. It directly manipulates agents_data or environment_data.

        Args:
            event: StateChangeEvent instance
        """
        try:
            # Parse the event change information
            if hasattr(event, 'change') and event.change:
                # Legacy format with change dict
                change = event.change
                path = change.get('path', [])
                operation = change.get('operation')
                value = change.get('value')
            else:
                # Direct format
                path = getattr(event, 'path', [])
                operation = getattr(event, 'operation', None)
                value = getattr(event, 'value', None)

            if not path:
                logger.warning("StateChangeEvent has empty path, skipping")
                return

            # Determine target based on event metadata
            target_type = getattr(event, 'target_type', 'agent')
            target_id = getattr(event, 'target_id', 'unknown')

            if target_type == "agent":
                if target_id not in self.agents_data:
                    logger.warning(f"Agent {target_id} not found during event replay, skipping")
                    return

                # For agent events, path points directly to the field within agent data
                # We need to determine which section (state, properties, reminders) to update
                agent_data = self.agents_data[target_id]

                # If path has only one element, assume it's going to state
                if len(path) == 1:
                    target = agent_data["state"]
                    remaining_path = path
                else:
                    # Check if first path element is a known agent section
                    if path[0] in ["state", "properties", "reminders"]:
                        target = agent_data[path[0]]
                        remaining_path = path[1:]
                    else:
                        # Default to state section
                        target = agent_data["state"]
                        remaining_path = path

            elif target_type == "environment":
                target = self.environment_data["state"]
                remaining_path = path

            else:
                logger.warning(f"Unknown target_type during event replay: {target_type}")
                return

            # Navigate deeper into the structure
            for key in remaining_path[:-1]:
                if key not in target:
                    # Create missing intermediate structures
                    target[key] = {}
                target = target[key]

            # Apply the operation
            if remaining_path:
                final_key = remaining_path[-1]

                if operation == "set":
                    target[final_key] = value
                elif operation == "delete":
                    if final_key in target:
                        del target[final_key]
                elif operation == "append":
                    if final_key not in target:
                        target[final_key] = []
                    if isinstance(target[final_key], list):
                        target[final_key].append(value)
                elif operation == "extend":
                    if final_key not in target:
                        target[final_key] = []
                    if isinstance(target[final_key], list) and isinstance(value, list):
                        target[final_key].extend(value)
                else:
                    logger.warning(f"Unknown operation during event replay: {operation}")
            else:
                # Direct assignment to root level (rare case)
                if operation == "set":
                    target.update(value if isinstance(value, dict) else {})

            logger.debug(f"Applied state change event: {target_type}.{target_id} {path} {operation} {value}")

        except Exception as e:
            logger.error(f"Failed to apply state change event: {e}")
            logger.error(f"Event details: target_type={getattr(event, 'target_type', 'unknown')}, target_id={getattr(event, 'target_id', 'unknown')}, path={getattr(event, 'path', 'unknown')}, operation={getattr(event, 'operation', 'unknown')}")

    def _apply_memory_change(self, event):
        """
        Apply a MemoryChangeEvent by delegating to the agent's memory system.

        Args:
            event: MemoryChangeEvent instance
        """
        try:
            agent_id = event.target_id

            if agent_id not in self.agents_data:
                logger.warning(f"Agent {agent_id} not found for memory event replay, skipping")
                return

            # Get the agent and its memory system
            agent = self.get_agent(agent_id)

            if not hasattr(agent, '_memory') or agent._memory is None:
                logger.warning(f"Agent {agent_id} has no memory system for event replay, skipping")
                return

            # Delegate to the memory system's apply_event method
            # Note: Memory system must implement its own apply_event method
            if hasattr(agent._memory, 'apply_event'):
                agent._memory.apply_event(event)
                logger.debug(f"Applied memory change event for agent {agent_id}")
            else:
                logger.warning(f"Memory system for agent {agent_id} does not support event replay")

        except Exception as e:
            logger.error(f"Failed to apply memory change event: {e}")
            logger.error(f"Event details: agent_id={getattr(event, 'target_id', 'unknown')}")

    def close(self):
        """Clean up resources"""
        if self.event_logger:
            self.event_logger.close()

        logger.debug("World closed")
