"""
Environment类定义

实现了统一状态架构下的Environment类，支持代理机制。
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional, TYPE_CHECKING
import copy
import logging
import inspect

# Import proxy system for state management
from .state_proxy import DictProxy
from .decorators import CAPABILITY_META_ATTR

if TYPE_CHECKING:
    from .core_data import World
    from .runtime_scope import StepRuntimeScope

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EnvironmentTickContext:
    """Context passed to environment lifecycle hooks around each simulation tick."""

    step: int
    world: "World"
    log: Any = None
    runtime_scope: "StepRuntimeScope | None" = None


class Environment:
    """
    Environment类：支持代理机制的环境容器
    
    在统一架构下，Environment通过World获取代理对象来访问状态，
    确保所有状态修改都被自动记录和追踪。
    
    Environment还提供依赖倒置的接口，让下层组件能够访问其他Agent
    和环境能力，而不直接依赖World对象。
    """
    
    def __init__(self, world: 'World'):
        """
        初始化Environment

        Args:
            world: World对象引用，用于获取真实数据和创建代理
        """
        self._world = world
        # 可选的资源句柄（由引擎注入，子类按需使用）
        self._embed_call = None
        self._vector_client = None
        # 仅在 ``async with ctx.activation_pool()`` 会话内由运行时注入。
        self.activation_pool = None
        logger.debug("Created Environment proxy")

    # 资源注入接口：引擎在环境实例化后调用，子类可复用
    def set_resource_handles(self, *, embed_call=None, vector_client=None) -> None:
        """
        注入可选的 embedding 调用函数与向量存储客户端。

        Args:
            embed_call: 异步 embedding 请求函数，签名与 EmbeddingManager.request 一致
            vector_client: 持久化的向量存储客户端（如 Chroma PersistentClient）
        """
        self._embed_call = embed_call
        self._vector_client = vector_client

    def initialize(self, agents: List[Any], world: "World") -> None:
        """Initialize environment state. Subclasses can override."""
        return None

    def before_tick(self, ctx: EnvironmentTickContext) -> None:
        """Hook called before all code steps in a simulation tick."""
        return None

    def after_tick(self, ctx: EnvironmentTickContext) -> None:
        """Hook called after all code steps succeed, before the world advances."""
        return None

    @property
    def agent_instruction(self) -> str:
        """环境可注入到 Agent system prompt 的指引文本，默认留空。"""
        return ""

    @property
    def type(self) -> str:
        """环境类型"""
        return self._world.environment_data["type"]
    
    @property
    def state(self) -> DictProxy:
        """
        获取环境状态的代理对象
        
        Returns:
            DictProxy对象，所有修改都会被自动记录
        """
        return self._world.create_environment_state_proxy()

    def write_transaction(self):
        """在 explicit_transactions 模式下开启环境状态写事务。"""

        return self._world.write_environment_transaction()

    @state.setter
    def state(self, value: Dict[str, Any]) -> None:
        """仅允许在初始化或恢复阶段整体替换环境状态。"""
        if not isinstance(value, dict):
            raise TypeError("Environment.state must be assigned a dict")
        journal = getattr(self._world, "_state_delta_journal", None)
        if journal is not None and getattr(journal, "active_step", None) is not None:
            raise RuntimeError(
                "Environment.state cannot be replaced during an active persistence Tick; "
                "write declared fields through the state proxy"
            )
        self._world.environment_data["state"] = value
        if hasattr(self._world, "_environment_state_proxy"):
            self._world._environment_state_proxy = None

    @property
    def step_runtime(self) -> "StepRuntimeScope":
        """返回当前 step 的临时状态作用域。"""

        return self._world.require_step_runtime_scope()
    
    def get_raw_data(self) -> Dict[str, Any]:
        """
        获取用于调试的脱离副本。
        
        Returns:
            Environment 数据的深拷贝；修改它不会写回 canonical state。
        """
        return copy.deepcopy(self._world.environment_data)
    
    # 依赖倒置接口：提供访问其他能力的方法
    
    def get_agent(self, agent_id: str):
        """
        通过依赖倒置获取其他agent
        
        Args:
            agent_id: Agent ID
            
        Returns:
            Agent对象
        """
        return self._world.get_agent(agent_id)
    
    def get_agents_by_type(self, agent_type: str):
        """
        获取特定类型的agents
        
        Args:
            agent_type: Agent类型
            
        Returns:
            Agent对象列表
        """
        return self._world.get_agents_by_type(agent_type)
    
    def get_agents_by_archetype(self, archetype: str):
        """
        获取特定架构的agents
        
        Args:
            archetype: Agent架构类型
            
        Returns:
            Agent对象列表
        """
        return self._world.get_agents_by_archetype(archetype)
    
    def get_all_agents(self):
        """
        获取所有agents
        
        Returns:
            Agent对象列表
        """
        return self._world.get_all_agents()
    
    # Default implementation methods
    
    def snapshot(self, *, include_state: bool = True) -> Dict[str, Any]:
        """
        Create a snapshot of environment state for persistence
        
        Default implementation returns the basic state data.
        Subclasses should override this to include complex objects
        like NetworkX graphs, spatial data structures, etc.
        
        Returns:
            Dictionary containing environment snapshot data

        Args:
            include_state: Include the canonical World environment state.  Set
                to ``False`` when the caller already persists
                ``World.environment_data["state"]`` separately.
        """
        snapshot_data = {"type": self.type}
        if include_state:
            # World.environment_data["state"] is the canonical state store.
            # Callers creating a World checkpoint can request only derived
            # environment data to avoid a second full state traversal.
            snapshot_data["state"] = dict(self.state)  # Convert proxy to regular dict
        
        # Add any additional default snapshot data
        snapshot_data["snapshot_metadata"] = {
            "created_by": "default_snapshot",
            "environment_class": self.__class__.__name__
        }
        
        logger.debug(f"Created default snapshot for environment {self.type}")
        return snapshot_data
    
    def restore_from_snapshot(self, snapshot_data: Dict[str, Any]):
        """
        Restore environment state from snapshot data
        
        Default implementation restores basic state.
        Subclasses should override this to handle complex objects.
        
        Args:
            snapshot_data: Dictionary containing snapshot data
        """
        if "state" in snapshot_data:
            # 恢复阶段直接整体替换 canonical state，避免 explicit 模式的只读视图
            # 走业务写路径；active Tick 仍由 state setter 拒绝。
            self.state = copy.deepcopy(snapshot_data["state"])
        
        if "type" in snapshot_data:
            self._world.set_environment_type(snapshot_data["type"])
        
        logger.debug(f"Restored environment from snapshot: {snapshot_data.get('type', 'unknown')}")
    
    def get_capabilities(self, kind: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """
        Get capabilities provided by this environment.

        Args:
            kind: Optional capability kind filter: action, fov, rule, or behavior.

        Returns:
            Dictionary of capability name -> capability metadata.
        """
        capabilities: Dict[str, Dict[str, Any]] = {}
        for attr_name, class_method in inspect.getmembers(self.__class__, predicate=inspect.isfunction):
            meta = getattr(class_method, CAPABILITY_META_ATTR, None)
            if meta is None:
                continue
            cap_kind = getattr(meta, "kind", None)
            if kind is not None and cap_kind != kind:
                continue
            method = getattr(self, attr_name)
            capability_name = getattr(meta, "name", None) or attr_name
            capabilities[capability_name] = {
                "kind": cap_kind,
                "function": method,
                "description": getattr(meta, "description", "") or "",
                "parameters": getattr(meta, "parameters_schema", {}) or {},
                "tags": list(getattr(meta, "tags", []) or []),
                "strict": bool(getattr(meta, "strict", False)),
                "source": "environment",
                "func_name": attr_name,
            }
        return capabilities

    def get_actions(self) -> Dict[str, Dict[str, Any]]:
        """
        Get actions that this environment provides to agents

        Default implementation discovers @action capabilities.

        Returns:
            Dictionary of action_name -> action_info mappings
        """
        return self.get_capabilities(kind="action")
    
    def __repr__(self) -> str:
        return f"Environment(type='{self.type}')"
    
    def __str__(self) -> str:
        return f"Environment({self.type})"
