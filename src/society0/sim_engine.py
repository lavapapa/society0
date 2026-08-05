"""
SimEngine V2: Main SimEngine class - Clean single-responsibility entry point.

The SimEngine is the user-facing entry point that coordinates all components
without becoming a "god object". Each component has clear responsibilities.
"""

from typing import Dict, Any, Optional, Union, List, Callable, Set
from collections.abc import Iterable
from pathlib import Path
import asyncio
import logging
import json
import yaml
import time
import traceback
import inspect

from .core_data import World
from .environment import Environment
from .decorators import ENV_META_ATTR, CAPABILITY_META_ATTR
from .function_registry import FunctionRegistry, register_environment_capabilities
from .legacy.schedule import Schedule
from .persistence import PersistenceManager
from .transaction import EventLogger
from .event_pipeline import EventBatchListener, JsonlIndexWriter, ReplayLogWriter
from .streaming import StreamingBridge
from .meta import CapabilityMeta
from .diff_dispatcher import NodeDiffDispatcher
from .logging import ExperimentLogContext

logger = logging.getLogger(__name__)

_SELECTOR_MATCH_LIMIT = 100
_TEXT_TRUNCATE_LIMIT = 2000
_TEXT_TRUNCATE_KEYS = {
    "thinking_process",
    "inner_monologue",
    "raw_response",
    "raw_output",
    "performative_output",
    "content",
    "message",
}


class SimEngine:
    """
    Clean, single-responsibility entry point for the simulation engine.
    Coordinates components without becoming a god object.
    """

    def __init__(
        self,
        save_dir: str,
        base_config: Union[str, dict] = None,
        *,
        experiment_log_context: Optional[ExperimentLogContext] = None,
        log_hooks: Optional[Iterable[Callable[[str, Dict[str, Any]], None]]] = None,
        **kwargs_configs,
    ):
        """
        Initialize the simulation engine with flexible configuration support.

        Args:
            save_dir: Directory to save experiment data
            base_config: Base configuration (file path or dict)
            **kwargs_configs: Additional configuration sources (file paths or dicts)
        """
        self.save_dir = save_dir

        event_listeners_config = kwargs_configs.pop('event_listeners', None)
        streaming_bridge_factory = kwargs_configs.pop("streaming_bridge_factory", None)
        enable_streaming_bridge = kwargs_configs.pop("enable_streaming_bridge", None)

        extra_configs = dict(kwargs_configs)
        self._schedule_path = self._pop_optional_path(extra_configs, "schedule_path")
        self._agent_set_path = self._pop_optional_path(extra_configs, "agent_set_path")
        self._environment_path = self._pop_optional_path(extra_configs, "environment_path")

        raw_experiment_root = extra_configs.pop("experiment_root", None)
        if raw_experiment_root is not None:
            self.experiment_root = Path(raw_experiment_root).resolve()
        elif self._schedule_path is not None:
            self.experiment_root = self._schedule_path.parent
        elif self._agent_set_path is not None:
            self.experiment_root = self._agent_set_path.parent
        else:
            self.experiment_root = Path(save_dir).resolve()

        # Load and merge configurations
        self.config = self._load_and_merge_configs(base_config, extra_configs)

        # Initialize components with clear responsibilities
        self.registries: List[FunctionRegistry] = []
        self.persistence_manager = PersistenceManager(save_dir)
        self._node_diff_dispatcher: Optional[NodeDiffDispatcher] = None
        self._model_provider = None

        default_listeners: List[EventBatchListener] = [
            JsonlIndexWriter(Path(save_dir) / "events.index.jsonl"),
            ReplayLogWriter(self.persistence_manager.events_dir)
        ]
        if event_listeners_config:
            if isinstance(event_listeners_config, Iterable):
                default_listeners.extend(event_listeners_config)
            else:
                default_listeners.append(event_listeners_config)

        self.streaming_bridge: Optional[StreamingBridge] = None
        requested_streaming_flag = self._resolve_streaming_flag(enable_streaming_bridge)
        if streaming_bridge_factory is not None:
            self.streaming_bridge = streaming_bridge_factory(Path(save_dir))
            self.streaming_enabled = True
        elif requested_streaming_flag:
            self.streaming_bridge = StreamingBridge(Path(save_dir))
            self.streaming_enabled = True
        else:
            self.streaming_enabled = False

        diff_bridge = self.streaming_bridge if self.streaming_enabled else None
        self._node_diff_dispatcher = NodeDiffDispatcher(
            bridge=diff_bridge,
            persistence=self.persistence_manager,
        )

        if self.streaming_bridge:
            default_listeners.append(self.streaming_bridge)

        self.event_logger = EventLogger(
            f"{save_dir}/events.jsonl",
            listeners=default_listeners
        )
        logs_dir = Path(save_dir) / "logs"
        hooks_iterable = list(log_hooks or [])
        self.log_context = experiment_log_context or ExperimentLogContext(
            logs_dir,
            hooks=hooks_iterable,
        )
        self.schedule: Optional[Schedule] = None
        self.current_world_state: Optional[World] = None
        self.agent_set_data: Optional[Dict[str, Any]] = None
        self.environment_data: Optional[Dict[str, Any]] = None
        self._schedule_dependencies: Dict[str, Any] = {}

        # Runtime state
        self.is_initialized = False

        logger.info(f"SimEngine initialized with save_dir: {save_dir}")

    def add_registry(self, registry: FunctionRegistry) -> None:
        """Add an external function registry to the engine.

        Args:
            registry: FunctionRegistry instance to add
        """
        from function_registry import get_registry_stats
        stats = get_registry_stats(registry)
        self.registries.append(registry)
        logger.info(f"Added registry with {stats['total_functions']} functions")

    @property
    def register(self) -> FunctionRegistry:
        """Get the primary function registry for decorator-based registration."""
        if not self.registries:
            # Create default registry if none added
            default_registry = FunctionRegistry()
            self.registries.append(default_registry)
        return self.registries[0]

    def _load_and_merge_configs(self, base_config: Union[str, dict], kwargs_configs: Dict[str, Any]) -> Dict[str, Any]:
        """Load and deeply merge multiple configuration sources.

        Args:
            base_config: Base configuration (file path or dict)
            kwargs_configs: Additional configuration sources
                - If config_name matches a top-level config key (like 'schedule', 'agents'),
                  it will be nested under that key
                - Otherwise it will be merged at top level

        Returns:
            Merged configuration dictionary
        """
        # Start with base config
        if base_config is None:
            merged_config = {}
        elif isinstance(base_config, dict):
            merged_config = base_config.copy()
        elif isinstance(base_config, str):
            merged_config = self._load_config_file(base_config)
        else:
            raise ValueError("base_config must be a file path (str), dict, or None")

        # Merge additional configs
        for config_name, config_source in kwargs_configs.items():
            if isinstance(config_source, dict):
                config_data = config_source
            elif isinstance(config_source, str):
                config_data = self._load_config_file(config_source)
            else:
                logger.warning(f"Skipping invalid config source: {config_name}")
                continue

            # 🔧 FIX: Smart nesting - wrap config_data under config_name key
            # This makes SimEngine(schedule=schedule_data) work intuitively
            wrapped_config = {config_name: config_data}

            # Deep merge
            merged_config = self._deep_merge(merged_config, wrapped_config)
            logger.debug(f"Merged config from: {config_name}")

        return merged_config

    def _deep_merge(self, base: Dict[str, Any], update: Dict[str, Any]) -> Dict[str, Any]:
        """Deeply merge two dictionaries.

        Args:
            base: Base dictionary
            update: Dictionary to merge into base

        Returns:
            Merged dictionary
        """
        result = base.copy()

        for key, value in update.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = value

        return result

    def _resolve_streaming_flag(self, explicit_flag: Optional[bool]) -> bool:
        """解析 StreamingBridge 是否应启用的布尔值。"""
        if explicit_flag is not None:
            return bool(explicit_flag)

        candidate = self.config.get("streaming_enabled")
        if isinstance(candidate, dict):
            candidate = candidate.get("enabled")

        if candidate is None:
            streaming_section = self.config.get("streaming")
            if isinstance(streaming_section, dict):
                candidate = streaming_section.get("enabled")

        if candidate is None:
            return True
        return bool(candidate)

    def _pop_optional_path(self, payload: Dict[str, Any], key: str) -> Optional[Path]:
        value = payload.pop(key, None)
        if value is None:
            return None
        return Path(value).resolve()

    def _load_config_file(self, config_path: str) -> Dict[str, Any]:
        """Load configuration from a single file.

        Args:
            config_path: Path to configuration file

        Returns:
            Configuration dictionary
        """
        config_file = Path(config_path)

        if not config_file.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        try:
            if config_file.suffix.lower() in ['.yaml', '.yml']:
                with open(config_file, 'r') as f:
                    return yaml.safe_load(f)
            elif config_file.suffix.lower() == '.json':
                with open(config_file, 'r') as f:
                    return json.load(f)
            else:
                raise ValueError(f"Unsupported config file format: {config_file.suffix}")
        except Exception as e:
            logger.error(f"Failed to load config file {config_path}: {e}")
            raise

    def set_resource_managers(self, llm_manager=None, embedding_manager=None):
        """
        Set resource managers for the simulation engine.
        This should be called before _initialize() to ensure proper dependency injection.

        Args:
            llm_manager: LLMManager instance for LLM API calls
            embedding_manager: EmbeddingManager instance for embedding calls
        """
        self._llm_manager = llm_manager
        self._embedding_manager = embedding_manager
        if self._llm_manager and hasattr(self._llm_manager, "set_log_context"):
            self._llm_manager.set_log_context(self.log_context)
        if self._embedding_manager and hasattr(self._embedding_manager, "set_log_context"):
            self._embedding_manager.set_log_context(self.log_context)
        logger.info("Resource managers set for SimEngine")

    def set_model_provider(self, model_provider) -> None:
        """注入模型提供者，用于按需解析模型配置。"""
        self._model_provider = model_provider
        # 兼容 runtime_models 里为每个模型独立创建的 LLMManager，
        # 确保其资源日志也绑定到当前实验日志上下文。
        if self._model_provider is not None and self.log_context is not None:
            models_map = getattr(self._model_provider, "_models", None)
            if isinstance(models_map, dict):
                for runtime in models_map.values():
                    llm_call = getattr(runtime, "llm_call", None)
                    owner = getattr(llm_call, "__self__", None)
                    if owner is not None and hasattr(owner, "set_log_context"):
                        try:
                            owner.set_log_context(self.log_context)
                        except Exception:
                            logger.debug("Failed to bind log context for model provider runtime", exc_info=True)
        logger.info("Model provider set for SimEngine")

    async def _initialize(self) -> None:
        """Initialize the simulation components."""
        self._ensure_streaming_bridge_started(status="initializing")

        if self.is_initialized:
            return

        logger.info("Initializing simulation components...")

        # Compile schedule from configuration
        schedule_config = self.config.get('schedule', {})
        if not schedule_config:
            raise ValueError("No schedule configuration found")

        self._prepare_assets(schedule_config)

        # Create initial world state
        self.current_world_state = self._create_initial_world_state()
        if self._node_diff_dispatcher:
            self.current_world_state.set_node_diff_dispatcher(self._node_diff_dispatcher)

        # Open the EventLogger for writing
        self.event_logger.open()

        # Merge all registries for schedule creation
        primary_registry = self.register  # This ensures at least one registry exists

        # Merge functions from all registries into primary registry
        for registry in self.registries[1:]:  # Skip first (primary) registry
            # Merge environment functions
            primary_registry.env_fovs.update(registry.env_fovs)
            primary_registry.env_empowers.update(registry.env_empowers)

            # Merge environment rules
            primary_registry.rules.update(registry.rules)

            # Merge agent functions
            primary_registry.agent_actions.update(registry.agent_actions)

            # Merge behavior functions
            primary_registry.behaviors.update(registry.behaviors)

            # Merge schedule functions
            primary_registry.selectors.update(registry.selectors)
            primary_registry.operators.update(registry.operators)
            primary_registry.converters.update(registry.converters)

        if len(self.registries) > 1:
            from .function_registry import get_registry_stats
            stats = get_registry_stats(primary_registry)
            logger.info(f"Merged {len(self.registries)} registries with {stats['total_functions']} total functions")

        # Set function registry to world for FoV execution and ActionSet assembly
        self.current_world_state.set_function_registry(primary_registry)

        # 🔧 FIX: 自动发现并注册环境提供的 FoV 函数
        try:
            environment = self.current_world_state.get_environment()
            self._register_environment_functions(environment, primary_registry)
            logger.info(f"Successfully registered environment functions from {environment.__class__.__name__}")
        except Exception as e:
            logger.warning(f"Failed to register environment functions: {e}")

        # Set persistence manager to world for memory operations
        self.current_world_state.set_persistence_manager(self.persistence_manager)

        # Inject resource managers if available
        if hasattr(self, '_llm_manager') or hasattr(self, '_embedding_manager'):
            self.current_world_state.set_resource_managers(
                llm_manager=getattr(self, '_llm_manager', None),
                embedding_manager=getattr(self, '_embedding_manager', None)
            )

        # 将资源句柄注入环境实例（保持向后兼容，若环境未实现 set_resource_handles 则忽略）
        try:
            env_instance = self.current_world_state.get_environment()
            if hasattr(env_instance, "set_resource_handles"):
                embed_call = None
                vector_client = None
                if getattr(self, "_embedding_manager", None):
                    embed_call = self._embedding_manager.request
                if getattr(self, "persistence_manager", None):
                    try:
                        vector_client = self.persistence_manager.get_chroma_client()
                    except Exception:
                        vector_client = None
                env_instance.set_resource_handles(embed_call=embed_call, vector_client=vector_client)
        except Exception as e:
            logger.warning(f"Failed to inject resource handles into environment: {e}")

        if getattr(self, '_model_provider', None):
            self.current_world_state.set_model_provider(self._model_provider)

        # Initialize cognitive systems for all LLM agents
        await self._initialize_cognitive_systems()

        # Create schedule with merged registry
        self.schedule = Schedule(
            schedule_config,
            primary_registry,
            log_context=self.log_context,
        )

        self.is_initialized = True
        logger.info("Simulation initialization completed")

    def _create_initial_world_state(self) -> World:
        """Create the initial world state from configuration."""
        # Create World with new unified architecture,使用共享的 EventLogger
        world = World(
            step=0,
            event_log_path=self.event_logger.log_file_path,
            event_logger=self.event_logger
        )
        world.set_log_context(self.log_context)

        agent_set_data = self.agent_set_data or {}
        agent_types = agent_set_data.get('types', []) or []
        type_index = {
            item.get('id'): item for item in agent_types if isinstance(item, dict)
        }

        # Store agent types in World for later reference (e.g., reasoning_stages)
        world._agent_types = type_index

        # Add agents from configuration
        agents_config = agent_set_data.get('agents', []) or []
        for agent_config in agents_config:
            if not isinstance(agent_config, dict):
                logger.warning("Agent configuration is not a dict, skipping...")
                continue
            agent_id = agent_config.get('id')
            if not agent_id:
                logger.warning("Agent configuration missing 'id', skipping...")
                continue

            type_ref = agent_config.get('type')
            type_meta = type_index.get(type_ref or "")
            archetype = 'rule'
            if isinstance(type_meta, dict):
                archetype = type_meta.get('archetype') or archetype

            # Add agent data to World
            world.add_agent_data(
                agent_id=agent_id,
                agent_type=type_ref or agent_config.get('agent_type', 'unknown'),
                archetype=archetype or 'rule'
            )

            # 🔧 PERSONA 重构: 读取并存储每个 Agent 的 persona 字段
            persona_instance = agent_config.get('persona') or ''
            type_persona = ""
            if isinstance(type_meta, dict):
                type_persona = type_meta.get('persona', '') or ''
            persona_value = persona_instance or type_persona
            world.agents_data[agent_id]['persona'] = persona_value or ''
            world.agents_data[agent_id]['persona_instance'] = persona_instance
            world.agents_data[agent_id]['persona_type'] = type_persona

            # Set initial agent state
            agent_state = agent_config.get('state', {})
            world.agents_data[agent_id]["state"].update(agent_state)

            model_override = agent_config.get('model')
            if isinstance(model_override, str) and model_override.strip():
                world.agents_data[agent_id]['model'] = model_override.strip()
            elif 'model' in world.agents_data[agent_id]:
                world.agents_data[agent_id].pop('model', None)

        # Set environment configuration
        env_config = self.environment_data or {}
        env_type = env_config.get('type', 'base')
        world.set_environment_type(env_type)

        # Populate environment config & state
        world.environment_data["config"] = env_config.get('config', {})
        initial_state = env_config.get('state') or env_config.get('initial_state') or {}
        if isinstance(initial_state, dict):
            world.environment_data["state"].update(initial_state)
        world.environment_data["schema"] = env_config.get('state_schema', {})

        # Store globals in environment data for now
        globals_config = self.config.get('globals', env_config.get('globals', {}))
        world.environment_data["globals"] = globals_config

        logger.info(f"Created initial world state with {len(world.agents_data)} agents")
        return world

    def _prepare_assets(self, schedule_config: Dict[str, Any]) -> None:
        dependencies = schedule_config.get('dependencies') or {}
        if not isinstance(dependencies, dict):
            raise ValueError("Schedule configuration缺少 dependencies 块，无法解析实验依赖。")

        agent_set_name = dependencies.get('agent_set')
        environment_name = dependencies.get('environment')

        if not isinstance(agent_set_name, str) or not agent_set_name.strip():
            raise ValueError("Schedule dependencies.agent_set 必须为非空字符串")
        if not isinstance(environment_name, str) or not environment_name.strip():
            raise ValueError("Schedule dependencies.environment 必须为非空字符串")

        agent_set_name = agent_set_name.strip()
        environment_name = environment_name.strip()

        try:
            self.agent_set_data = self._load_agent_set_payload(agent_set_name)
        except FileNotFoundError as exc:
            raise ValueError(f"Agent Set 资产 '{agent_set_name}' 不存在于实验包中") from exc

        try:
            self.environment_data = self._load_environment_payload(environment_name)
        except FileNotFoundError as exc:
            raise ValueError(f"Environment 资产 '{environment_name}' 不存在于实验包中") from exc

        self._schedule_dependencies = {
            "agent_set": agent_set_name,
            "environment": environment_name,
            "logics": dependencies.get("logics", []),
        }

        # 将解析出的数据合并回 config，便于后续组件复用
        agent_set_agents = (self.agent_set_data or {}).get('agents', [])
        agent_types = (self.agent_set_data or {}).get('types', [])
        self.config['agents'] = agent_set_agents
        self.config['agent_types'] = agent_types

        env_payload = self.environment_data or {}
        self.config['environment'] = {
            "type": env_payload.get('type'),
            "config": env_payload.get('config', {}),
            "state_schema": env_payload.get('state_schema', {}),
            "globals": env_payload.get('globals', {}),
        }

        logger.info(
            "Loaded schedule dependencies: agent_set=%s, environment=%s",
            agent_set_name,
            environment_name,
        )

    async def _initialize_cognitive_systems(self) -> None:
        """🔧 PERSONA 重构: Initialize cognitive systems for all LLM agents following unified architecture."""
        logger.info("Initializing cognitive systems for LLM agents...")

        # Get LLM call function - prefer resource manager over config
        llm_call_function = None
        if hasattr(self, '_llm_manager') and self._llm_manager:
            llm_call_function = self._llm_manager.request
            logger.info("Using LLMManager.request for LLM calls")
        else:
            # Fallback to configuration-based LLM call
            llm_call_function = self._get_llm_call_function()

        # Get memory URI from configuration
        memory_uri = self.config.get('memory_uri', './chroma_store')

        # 🔧 PERSONA 重构: 直接在这里将 per-agent persona 传递下去
        # 不再需要传递全局的 persona_config
        self.current_world_state.initialize_all_cognitive_systems(
            llm_call=llm_call_function,
            memory_uri=memory_uri,
            model_provider=getattr(self, '_model_provider', None),
        )

        logger.info("Cognitive systems initialization completed")

    def _get_llm_call_function(self) -> Callable:
        """Get or create LLM call function from configuration."""
        # Check if LLM configuration exists
        llm_config = self.config.get('llm', {})

        if 'api_key' in llm_config and 'model' in llm_config:
            # Create real LLM call function
            return self._create_real_llm_call(llm_config)
        else:
            # 🚨 CRITICAL: No mock LLM - require real configuration
            raise ValueError(
                "No LLM configuration found. Please provide api_key and model in config. "
                "Mock LLM is disabled to prevent fake data issues."
            )

    def _create_real_llm_call(self, llm_config: Dict[str, Any]) -> Callable:
        """Create real LLM call function based on configuration."""
        api_key = llm_config['api_key']
        model = llm_config['model']
        base_url = llm_config.get('base_url')

        async def llm_call(payload: Dict[str, Any]) -> Dict[str, Any]:
            """Real LLM call implementation."""
            try:
                # Here would be actual LLM API call implementation
                logger.info(f"LLM call to {model} with {len(payload.get('messages', []))} messages")

                # TODO: Implement actual API call
                raise NotImplementedError(
                    "Real LLM API call not implemented yet. Please provide implementation or use LLMManager."
                )
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                raise  # Don't return mock response, let it fail

        return llm_call


    def _load_agent_set_payload(self, identifier: str) -> Dict[str, Any]:
        candidates = self._candidate_agent_set_paths(identifier)
        for path in self._unique_paths(candidates):
            if path.is_file():
                logger.debug("Loading agent set from %s", path)
                with path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
        raise FileNotFoundError(identifier)

    def _load_environment_payload(self, identifier: str) -> Dict[str, Any]:
        candidates = self._candidate_environment_paths(identifier)
        for path in self._unique_paths(candidates):
            if path.is_file():
                logger.debug("Loading environment from %s", path)
                with path.open("r", encoding="utf-8") as handle:
                    if path.suffix.lower() == ".json":
                        return json.load(handle)
                    return yaml.safe_load(handle) or {}
        raise FileNotFoundError(identifier)

    def _candidate_agent_set_paths(self, identifier: str) -> List[Path]:
        slug = self._slugify(identifier)
        candidates: List[Path] = []
        if self._agent_set_path:
            candidates.append(self._agent_set_path)
        candidates.append(self.experiment_root / "agent_set.json")
        candidates.append(self.experiment_root / f"agent_set_{slug}.json")
        candidates.append(self.experiment_root / f"{slug}.json")
        candidates.append(self.experiment_root / "agent_sets" / f"{slug}.json")
        return candidates

    def _candidate_environment_paths(self, identifier: str) -> List[Path]:
        slug = self._slugify(identifier)
        candidates: List[Path] = []
        if self._environment_path:
            candidates.append(self._environment_path)
        candidates.append(self.experiment_root / "environment.yaml")
        candidates.append(self.experiment_root / "environment.yml")
        candidates.append(self.experiment_root / f"environment_{slug}.yaml")
        candidates.append(self.experiment_root / f"environment_{slug}.yml")
        candidates.append(self.experiment_root / f"{slug}.yaml")
        candidates.append(self.experiment_root / f"{slug}.yml")
        candidates.append(self.experiment_root / "environments" / f"{slug}.yaml")
        candidates.append(self.experiment_root / "environments" / f"{slug}.yml")
        candidates.append(self.experiment_root / "environment.json")
        candidates.append(self.experiment_root / f"environment_{slug}.json")
        candidates.append(self.experiment_root / f"{slug}.json")
        return candidates

    def _unique_paths(self, candidates: List[Path]) -> List[Path]:
        unique: List[Path] = []
        seen: Set[str] = set()
        for path in candidates:
            if path is None:
                continue
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    @staticmethod
    def _slugify(value: str) -> str:
        if not value:
            return "item"
        allowed = set("-_.")
        chars = []
        for ch in value:
            if ch.isascii() and (ch.isalnum() or ch in allowed):
                chars.append(ch)
            else:
                chars.append("_")
        slug = "".join(chars).strip("_")
        return slug or "item"

    def _ensure_streaming_bridge_started(self, *, status: Optional[str] = None) -> None:
        """确保 StreamingBridge 已经启动。"""
        if not self.streaming_bridge:
            return
        try:
            self.streaming_bridge.start()
            if status:
                self.streaming_bridge.update_status(status=status)
        except Exception as exc:
            logger.warning("Failed to start StreamingBridge: %s", exc)

    def _update_streaming_status(self, **kwargs) -> None:
        """安全地更新 StreamingBridge 状态。"""
        if not self.streaming_bridge:
            return
        try:
            self.streaming_bridge.update_status(**kwargs)
        except Exception as exc:
            logger.warning("Failed to update StreamingBridge status: %s", exc)

    def _update_streaming_progress(
        self,
        current_step: int,
        total_steps: int,
        *,
        average_step_duration_ms: Optional[float] = None,
        estimated_remaining_seconds: Optional[float] = None,
    ) -> None:
        """安全地广播进度信息。"""
        if not self.streaming_bridge:
            return
        try:
            self.streaming_bridge.update_progress(
                current_step=current_step,
                total_steps=total_steps,
                average_step_duration_ms=average_step_duration_ms,
                estimated_remaining_seconds=estimated_remaining_seconds,
            )
        except Exception as exc:
            logger.warning("Failed to update StreamingBridge progress: %s", exc)

    def _stop_streaming_bridge(self, *, final_status: Optional[str] = None) -> None:
        """安全地停止 StreamingBridge，并记录最终状态。

        Args:
            final_status: 实验的最终状态（例如 "completed", "failed"）
        """
        if not self.streaming_bridge:
            return
        try:
            # 将 final_status 传递给 stop 方法
            self.streaming_bridge.stop(final_status=final_status)
        except Exception as exc:
            logger.warning("Failed to stop StreamingBridge: %s", exc)

    def _get_real_agent_ids(self) -> List[str]:
        """Get list of real agent IDs from current world state."""
        if self.current_world_state and hasattr(self.current_world_state, 'agents_data'):
            return list(self.current_world_state.agents_data.keys())
        else:
            # Fallback to config-based agent IDs
            agents_config = self.config.get('agents', [])
            return [agent.get('id') for agent in agents_config if agent.get('id')]

    def get_log_context(self) -> ExperimentLogContext:
        """获取实验日志上下文，便于外部模块注入。"""
        return self.log_context

    async def run(self, steps: int) -> None:
        """
        Run the simulation for the specified number of steps.
        Uses direct modification model - no patch collection/application.

        Args:
            steps: Number of simulation steps to execute
        """
        # 初始化最终状态标记
        final_status = "pending"

        try:
            await self._initialize()
        except Exception as exc:
            # 初始化失败，记录错误信息但不更新状态（在 finally 块统一处理）
            logger.error(f"Simulation initialization failed: {exc}")
            final_status = "failed"
            self.log_context.log_runtime(
                "ERROR",
                "experiment_failed",
                phase="initialize",
                error=str(exc),
                traceback=traceback.format_exc(),
            )
            self._update_streaming_status(error={"message": f"Initialization failed: {str(exc)}"})
            raise

        # 标记为运行中
        self._update_streaming_status(status="running")
        self._update_streaming_progress(
            current_step=self.current_world_state.step if self.current_world_state else 0,
            total_steps=steps,
            average_step_duration_ms=None,
            estimated_remaining_seconds=None,
        )

        await self._ensure_initial_snapshot()

        if not self.schedule:
            final_status = "failed"
            self._update_streaming_status(error={"message": "Schedule not initialized"})
            self.log_context.log_runtime(
                "ERROR",
                "experiment_failed",
                phase="post_initialize",
                error="Schedule not initialized",
            )
            raise RuntimeError("Schedule not initialized")

        total_agents = len(self._get_real_agent_ids())
        self.log_context.log_runtime(
            "INFO",
            "experiment_started",
            total_steps=steps,
            agent_count=total_agents,
        )
        logger.info("Starting simulation run for %s steps", steps)
        start_time = time.time()

        try:
            for step in range(steps):
                current_world_step = self.current_world_state.step if self.current_world_state else step
                self.log_context.log_runtime(
                    "INFO",
                    "step_started",
                    step=step,
                    world_step=current_world_step,
                )
                logger.info("--- Executing Step %s ---", self.current_world_state.step + 1)

                # Set up context stack for this step
                from .context_stack import ContextStack
                step_context = ContextStack().push_step(f"step_{self.current_world_state.step}")
                self.current_world_state.set_context_stack(step_context)

                # Execute step within transaction for atomicity
                try:
                    with self.current_world_state.transaction_manager.transaction(
                        f"step_{self.current_world_state.step}",
                        "simulation_run",
                        step_context
                    ) as tx:
                        step_metrics_payload: Optional[Dict[str, Any]] = None
                        # Update event logger context
                        self.event_logger.set_context(step=self.current_world_state.step)

                        # Execute one step through Schedule with event logging
                        step_start_time = time.time()
                        step_result = await self.schedule.execute_step(self.current_world_state, self.event_logger)
                        step_execution_time = time.time() - step_start_time

                        # Extract step_context for analysis
                        step_context = step_result.get("step_context", {}) or {}
                        jmespath_root = step_result.get("jmespath_root")
                        nodes_executed = step_result.get("nodes_executed", 0)
                        step_flow_nodes_snapshot = self._build_step_flow_snapshot(
                            schedule=self.schedule,
                            raw_step_context=step_context,
                        )
                        pruned_jmespath_root = None
                        if jmespath_root is not None:
                            pruned_jmespath_root = self._prune_jmespath_root(
                                raw_root=jmespath_root,
                                step_flow_nodes=step_flow_nodes_snapshot,
                            )
                            jmespath_root = None

                        # Create and record StepSummaryEvent with full context
                        from .events import create_step_summary_event

                        # Create summary with complete context and aggregation capabilities
                        summary_event = create_step_summary_event(
                            step_id=f"step_{self.current_world_state.step}",
                            step_number=self.current_world_state.step,
                            step_context=step_context,
                            context_stack=self.current_world_state.get_context_stack().to_list(),
                            execution_time=step_execution_time,
                            nodes_executed=nodes_executed,
                            execution_summary={
                                "total_nodes": len(step_context),
                                "successful_nodes": len([k for k, v in step_context.items() if v is not None]),
                                "step_start_time": step_start_time,
                                "step_end_time": time.time(),
                                "world_step": self.current_world_state.step
                            }
                        )

                        # 可选：添加一些基础的聚合指标示例
                        # 这里可以根据具体需求添加特定的指标计算逻辑
                        if step_context:
                            # 示例：统计不同类型的节点输出
                            output_types = {}
                            for node_id, output in step_context.items():
                                if output:
                                    output_type = type(output).__name__
                                    output_types[output_type] = output_types.get(output_type, 0) + 1

                            summary_event.add_aggregate_metric(
                                "node_output_types",
                                output_types,
                                calculation_method="type_counting",
                                source_nodes=list(step_context.keys())
                            )

                        executed_step_number = self.current_world_state.step
                        checkpoint_step_number = executed_step_number + 1
                        step_metrics_payload = {
                            # Keep metrics.step_number aligned with checkpoint_{step}.json step.
                            "step_number": checkpoint_step_number,
                            "nodes_executed": nodes_executed,
                            "step_context": step_context,
                            "step_flow_nodes": step_flow_nodes_snapshot,
                            "aggregate_metrics": summary_event.aggregate_metrics,
                            "execution_summary": summary_event.execution_summary,
                        }
                        if pruned_jmespath_root is not None:
                            step_metrics_payload["jmespath_root"] = pruned_jmespath_root

                        # 记录StepSummaryEvent
                        self.current_world_state.get_event_logger().write_event(
                            summary_event,
                            step_id=f"step_{self.current_world_state.step}",
                            node_id="step_summary"
                        )

                        # Advance step counter directly (within transaction)
                        self.current_world_state.advance_step()

                        # Transaction commits automatically here

                    # Save checkpoint after successful step execution
                    await self.persistence_manager.save_checkpoint(
                        self.current_world_state,
                        self.schedule,
                        step_metrics=step_metrics_payload,
                    )
                    await self._broadcast_latest_snapshot(self.current_world_state.step)

                    logger.info(f"Step {self.current_world_state.step} completed successfully")
                    self.log_context.log_runtime(
                        "INFO",
                        "step_completed",
                        step=step,
                        world_step=self.current_world_state.step,
                        duration_sec=step_execution_time,
                        nodes_executed=nodes_executed,
                    )

                    steps_completed = self.current_world_state.step
                    elapsed = time.time() - start_time
                    average_duration_ms = (elapsed / steps_completed) * 1000 if steps_completed else None
                    remaining_steps = max(steps - steps_completed, 0)
                    estimated_remaining = (elapsed / steps_completed) * remaining_steps if steps_completed else None

                    self._update_streaming_progress(
                        current_step=steps_completed,
                        total_steps=steps,
                        average_step_duration_ms=average_duration_ms,
                        estimated_remaining_seconds=estimated_remaining,
                    )

                except Exception as e:
                    logger.error(f"Error in step {self.current_world_state.step}: {e}")
                    # 只更新错误信息，不更新状态（在 finally 块统一处理）
                    self.log_context.log_runtime(
                        "ERROR",
                        "step_failed",
                        step=step,
                        world_step=self.current_world_state.step,
                        error=str(e),
                        traceback=traceback.format_exc(),
                    )
                    self._update_streaming_status(error={"message": str(e)})
                    raise  # Re-raise to stop simulation on error

        except Exception as exc:
            # 执行过程中发生异常，标记为失败
            logger.error("Simulation run failed: %s", exc)
            final_status = "failed"
            # 更新错误信息（状态在 finally 块处理）
            self._update_streaming_status(error={"message": str(exc)})
            self.log_context.log_runtime(
                "ERROR",
                "experiment_failed",
                phase="execution",
                error=str(exc),
                traceback=traceback.format_exc(),
            )
            raise
        else:
            # 成功完成所有步骤
            total_time = time.time() - start_time
            logger.info(f"Simulation completed in {total_time:.2f} seconds")
            self.log_context.log_runtime(
                "INFO",
                "experiment_completed",
                total_steps=steps,
                total_duration_sec=total_time,
                steps_completed=self.current_world_state.step if self.current_world_state else steps,
            )

            # Save final summary
            await self._save_experiment_summary(steps, total_time)
            final_step = self.current_world_state.step if self.current_world_state else steps
            average_duration_ms = (total_time / steps) * 1000 if steps else None
            self._update_streaming_progress(
                current_step=final_step,
                total_steps=steps,
                average_step_duration_ms=average_duration_ms,
                estimated_remaining_seconds=0.0,
            )
            # 标记最终状态为成功
            final_status = "completed"
        finally:
            # 【核心修复】在 finally 块中，统一记录最终状态并停止 StreamingBridge
            # 无论成功、失败还是异常退出，都会执行到这里
            self._stop_streaming_bridge(final_status=final_status)

    @staticmethod
    def _truncate_string(value: Any, *, limit: int = _TEXT_TRUNCATE_LIMIT) -> Any:
        if isinstance(value, str) and len(value) > limit:
            omitted = len(value) - limit
            return f"{value[:limit]}...(truncated {omitted} chars)"
        return value

    def _truncate_structure(self, value: Any) -> Any:
        if isinstance(value, dict):
            trimmed: Dict[str, Any] = {}
            for key, val in value.items():
                if key in _TEXT_TRUNCATE_KEYS:
                    trimmed[key] = (
                        self._truncate_string(val)
                        if isinstance(val, str)
                        else self._truncate_structure(val)
                    )
                else:
                    trimmed[key] = self._truncate_structure(val)
            return trimmed
        if isinstance(value, list):
            return [self._truncate_structure(item) for item in value]
        return self._truncate_string(value)

    def _prune_selector_snapshot(
        self,
        selector_snapshot: Any,
        selector_type: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if selector_snapshot is None:
            return None
        params = dict(getattr(selector_snapshot, "params", {}) or {})
        match_count = getattr(selector_snapshot, "match_count", 0)
        matched_ids = list(getattr(selector_snapshot, "matched_ids", []) or [])
        info: Dict[str, Any] = {
            "type": selector_type or params.get("type") or "unknown",
            "match_count": match_count,
        }
        if params:
            info["params"] = params
        selector_key = (selector_type or "").lower()
        if selector_key in {"custom", "by_id"}:
            if len(matched_ids) > _SELECTOR_MATCH_LIMIT:
                info["matched_ids"] = matched_ids[:_SELECTOR_MATCH_LIMIT]
                info["matched_ids_omitted"] = len(matched_ids) - _SELECTOR_MATCH_LIMIT
            else:
                info["matched_ids"] = matched_ids
        return info

    def _prune_operator_execution(self, execution_view: Dict[str, Any]) -> Dict[str, Any]:
        inputs_value = execution_view.get("inputs")
        entry: Dict[str, Any] = {
            "agent_id": execution_view.get("agent_id"),
            "status": execution_view.get("status"),
            "output": execution_view.get("output"),
            "raw_output": execution_view.get("value"),
            "structured_output": execution_view.get("structured_output"),
            "error_message": self._truncate_string(execution_view.get("error_message")),
            "execution_time": execution_view.get("execution_time"),
        }
        if isinstance(inputs_value, dict) and inputs_value:
            entry["inputs"] = self._truncate_structure(inputs_value)
        elif inputs_value is not None:
            entry["inputs"] = inputs_value
        metadata = execution_view.get("metadata")
        if isinstance(metadata, dict) and metadata:
            entry["metadata"] = self._truncate_structure(metadata)
        performative = execution_view.get("performative_output")
        if performative is not None:
            entry["performative_output"] = self._truncate_string(performative)
        actions_taken = execution_view.get("actions_taken")
        if actions_taken:
            entry["actions_taken"] = self._truncate_structure(actions_taken)
        phases = execution_view.get("phases")
        if phases:
            entry["phases"] = self._truncate_structure(phases)
        fovs_used = execution_view.get("fovs_used")
        if fovs_used:
            entry["fovs_used"] = fovs_used
        total_turns = execution_view.get("total_turns")
        if total_turns is not None:
            entry["total_turns"] = total_turns
        return {k: v for k, v in entry.items() if v not in (None, "", [], {})}

    def _prune_operator_snapshot(self, operator_snapshot: Any) -> Dict[str, Any]:
        pruned: Dict[str, Any] = {
            "type": getattr(operator_snapshot, "type", "unknown"),
        }
        name = getattr(operator_snapshot, "name", None)
        if name:
            pruned["name"] = name
        description = getattr(operator_snapshot, "description", None)
        if description:
            pruned["description"] = description

        outputs: List[Dict[str, Any]] = []
        status_summary: Dict[str, int] = {}
        for record in getattr(operator_snapshot, "executions", []) or []:
            execution_view = record.to_dict()
            outputs.append(self._prune_operator_execution(execution_view))
            status = execution_view.get("status")
            if status:
                status_summary[status] = status_summary.get(status, 0) + 1

        pruned["outputs"] = outputs
        if status_summary:
            pruned["status_summary"] = status_summary
        return pruned

    def _prune_node_snapshot(
        self,
        *,
        node_id: str,
        snapshot: Any,
        node_config: Optional[Any],
        raw_step_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        selector_info = self._prune_selector_snapshot(
            getattr(snapshot, "selector", None),
            getattr(node_config, "selector_type", None) if node_config else None,
        )
        if selector_info:
            data["selector"] = selector_info

        operator_snapshots = getattr(snapshot, "operators", {}) or {}
        if operator_snapshots:
            operators: Dict[str, Any] = {}
            for operator_id, operator_snapshot in operator_snapshots.items():
                operators[operator_id] = self._prune_operator_snapshot(operator_snapshot)
            data["operators"] = operators

        if node_id in raw_step_context:
            converter_payload = raw_step_context[node_id]
            converter_entry: Dict[str, Any] = {"output_ref": f"metrics.step_context.{node_id}"}
            if isinstance(converter_payload, dict):
                converter_entry["preview_keys"] = list(converter_payload.keys())
            data["converter"] = converter_entry

        return data

    def _build_step_flow_snapshot(
        self,
        *,
        schedule: "Schedule",
        raw_step_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        step_nodes: Dict[str, Any] = {}
        for flow in getattr(schedule, "step_flows", []) or []:
            for node in getattr(flow, "step_nodes", []) or []:
                step_nodes[node.id] = node
        snapshot: Dict[str, Any] = {}
        for flow in getattr(schedule, "step_flows", []) or []:
            builder = getattr(flow, "_context_builder", None)
            if builder and getattr(builder, "_nodes", None):
                for node_id, node_snapshot in builder._nodes.items():
                    snapshot[node_id] = self._prune_node_snapshot(
                        node_id=node_id,
                        snapshot=node_snapshot,
                        node_config=step_nodes.get(node_id),
                        raw_step_context=raw_step_context,
                    )

        for node_id in raw_step_context.keys():
            snapshot.setdefault(
                node_id,
                {"converter": {"output_ref": f"metrics.step_context.{node_id}"}},
            )

        return snapshot

    def _prune_jmespath_root(
        self,
        *,
        raw_root: Any,
        step_flow_nodes: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(raw_root, dict):
            return {"status": "omitted"}

        pruned: Dict[str, Any] = {}
        step_info = raw_root.get("step")
        if isinstance(step_info, dict):
            pruned["step"] = {"number": step_info.get("number")}

        if step_flow_nodes:
            pruned["nodes"] = {
                node_id: {
                    "selector": node_data.get("selector"),
                    "operators": {
                        op_id: {
                            "type": op_info.get("type"),
                            "status_summary": op_info.get("status_summary"),
                            "outputs_count": len(op_info.get("outputs", [])),
                        }
                        for op_id, op_info in (node_data.get("operators") or {}).items()
                    },
                    "converter": node_data.get("converter"),
                }
                for node_id, node_data in step_flow_nodes.items()
            }

        operators_section = raw_root.get("operators")
        if isinstance(operators_section, dict):
            pruned["operators"] = {
                op_id: {
                    "status_summary": (
                        operator.get("status_summary") if isinstance(operator, dict) else None
                    )
                }
                for op_id, operator in operators_section.items()
            }

        world_section = raw_root.get("world")
        if isinstance(world_section, dict):
            pruned["world"] = {
                "step": world_section.get("step"),
                "agent_count": len(world_section.get("agents_data", {}))
                if isinstance(world_section.get("agents_data"), dict)
                else None,
                "environment_keys": list(
                    world_section.get("environment_data", {}).keys()
                    if isinstance(world_section.get("environment_data"), dict)
                    else []
                ),
            }

        debug_section = raw_root.get("debug")
        if debug_section:
            pruned["debug"] = debug_section

        pruned["metadata"] = {
            "omitted_sections": ["world.agents_data", "world.environment_data", "step_context"]
        }
        return pruned

    async def _ensure_initial_snapshot(self) -> None:
        """确保在执行前保存并广播初始快照。"""
        if not self.current_world_state:
            return

        initial_step = self.current_world_state.step
        try:
            self.persistence_manager.resolve_checkpoint(initial_step)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            await self.persistence_manager.save_checkpoint(
                self.current_world_state,
                self.schedule,
                step_metrics=None,
            )

        await self._broadcast_latest_snapshot(initial_step)

    async def _broadcast_latest_snapshot(self, step_number: int) -> None:
        """在生成新快照后，将其通过 StreamingBridge 推送给监听者。"""
        if not self.streaming_bridge:
            return

        try:
            checkpoint_record = self.persistence_manager.resolve_checkpoint(step_number)
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            return
        checkpoint_path = checkpoint_record["checkpoint_file"]

        def _load_snapshot() -> Dict[str, Any]:
            with checkpoint_path.open("r", encoding="utf-8") as fp:
                return json.load(fp)

        try:
            snapshot_data = await asyncio.to_thread(_load_snapshot)
        except Exception as exc:  # pragma: no cover - IO 错误仅记录日志
            logger.warning("Failed to load checkpoint for streaming broadcast: %s", exc)
            return

        diff_log_path = self.persistence_manager.diffs_dir / f"diffs_from_step_{step_number:06d}.jsonl"
        diff_exists = diff_log_path.exists()
        if not diff_exists:
            legacy_diff_path = self.persistence_manager.events_dir / f"events_from_step_{step_number:06d}.jsonl"
            if legacy_diff_path.exists():
                diff_log_path = legacy_diff_path
                diff_exists = True

        snapshot_metrics = snapshot_data.get("metrics") or snapshot_data.get("step_metrics")
        if snapshot_metrics is not None and "metrics" not in snapshot_data:
            snapshot_data["metrics"] = snapshot_metrics

        self.streaming_bridge.publish_snapshot(
            step_id=step_number,
            snapshot=snapshot_data,
            checkpoint_path=str(checkpoint_path),
            diff_log_file=str(diff_log_path),
            diff_log_exists=diff_exists,
            metrics=snapshot_metrics,
        )

    async def resume(self, from_step: int = -1) -> None:
        """
        Resume simulation from a saved checkpoint.

        Args:
            from_step: Step to resume from. If -1, resumes from latest.
        """
        await self._initialize()

        # Find step to resume from
        available_steps = await self.persistence_manager.get_available_checkpoints()

        if not available_steps:
            logger.warning("No checkpoints found, cannot resume")
            return

        if from_step == -1:
            resume_step = max(available_steps)
        else:
            if from_step not in available_steps:
                raise ValueError(f"Step {from_step} checkpoint not found")
            resume_step = from_step

        logger.info(f"Resuming simulation from step {resume_step}")

        # Load world state from checkpoint
        world, schedule = await self.persistence_manager.load_checkpoint(
            resume_step,
            event_logger=self.event_logger,
        )
        self.current_world_state = world
        if self.current_world_state:
            self.current_world_state.set_log_context(self.log_context)
            self.current_world_state.set_function_registry(self.register)
            self.current_world_state.set_persistence_manager(self.persistence_manager)
            self.current_world_state.set_resource_managers(
                llm_manager=self._llm_manager,
                embedding_manager=self._embedding_manager,
            )
            if self._model_provider is not None:
                self.current_world_state.set_model_provider(self._model_provider)
        if self._node_diff_dispatcher:
            self.current_world_state.set_node_diff_dispatcher(self._node_diff_dispatcher)

        # Reinitialize schedule with correct registry (since load_checkpoint returns None for schedule)
        if self.schedule is None:
            schedule_config = self.config.get('schedule', {})
            primary_registry = self.register
            self.schedule = Schedule(
                schedule_config,
                primary_registry,
                log_context=self.log_context,
            )

        # Reinitialize cognitive systems for the restored world
        await self._initialize_cognitive_systems()

        logger.info(f"Successfully resumed from step {resume_step}")

    async def create_parallel_world(self, from_step: int, branch_name: str) -> 'SimEngine':
        """
        Create a parallel world branch from a specific step.

        Args:
            from_step: Step to branch from
            branch_name: Name for the new branch

        Returns:
            New SimEngine instance for the parallel world
        """
        # Create branch using persistence manager
        branch_persistence = await self.persistence_manager.create_branch(from_step, branch_name)

        # Create new SimEngine for the branch
        branch_engine = SimEngine.__new__(SimEngine)
        branch_engine.save_dir = str(branch_persistence.save_dir)
        branch_engine.config = self.config.copy()
        branch_engine.registries = self.registries  # Share the same registries
        branch_engine.persistence_manager = branch_persistence
        branch_engine.schedule = None
        branch_engine.current_world_state = None
        branch_engine.is_initialized = False
        branch_engine.streaming_bridge = None
        branch_engine.streaming_enabled = False
        branch_engine._node_diff_dispatcher = NodeDiffDispatcher(
            bridge=None,
            persistence=branch_persistence,
        )

        logger.info(f"Created parallel world branch '{branch_name}' from step {from_step}")
        return branch_engine

    async def _save_experiment_summary(self, steps_run: int, total_time: float) -> None:
        """Save experiment execution summary."""
        if not self.current_world_state:
            return

        summary = {
            'experiment_config': self.config,
            'execution_summary': {
                'steps_run': steps_run,
                'total_execution_time': total_time,
                'average_step_time': total_time / steps_run if steps_run > 0 else 0,
                'final_step': self.current_world_state.step
            },
            'world_state_summary': self.current_world_state.get_state_summary(),
            'function_registry_stats': self._get_registry_stats(),
            'completed_at': time.time()
        }

        # Save summary to metadata
        self.persistence_manager.experiment_metadata.update(summary)
        self.persistence_manager._save_metadata()

        logger.info("Experiment summary saved")
        self.log_context.log_system(
            "INFO",
            "summary_saved",
            steps_run=steps_run,
            total_duration_sec=total_time,
            final_step=self.current_world_state.step,
        )

    def _get_registry_stats(self) -> Dict[str, Any]:
        """Get function registry statistics."""
        from .function_registry import get_registry_stats
        return get_registry_stats(self.register)

    def _register_environment_functions(self, environment: 'Environment', registry: 'FunctionRegistry') -> None:
        """🔧 FIX: 自动发现并注册环境提供的 FoV 函数

        Args:
            environment: Environment 实例（如 SocialNetworkEnv）
            registry: FunctionRegistry 实例用于注册函数
        """
        import inspect

        cls = environment.__class__
        env_meta = getattr(cls, ENV_META_ATTR, None)

        # 首选：使用 v3 能力注册器，确保 env.<func_name> / canonical ID 均被填充
        if env_meta:
            registered = register_environment_capabilities(registry, env_meta, environment)
            logger.debug(
                "Registered %s capabilities from environment %s via EnvironmentMeta",
                registered,
                env_meta.type_name,
            )
            if registered > 0:
                return

        # 兼容旧环境：保留原始的基于装饰器扫描的逻辑
        env_type_name = env_meta.type_name if env_meta else cls.__name__

        for name, func in inspect.getmembers(cls, predicate=inspect.isfunction):
            capability_meta = getattr(func, CAPABILITY_META_ATTR, None)
            if isinstance(capability_meta, CapabilityMeta) and capability_meta.kind == "fov":
                canonical_id = f"environments.{env_type_name}.fovs.{capability_meta.name}"
                bound_method = getattr(environment, name)
                entry = {
                    'function': bound_method,
                    'description': capability_meta.description,
                    'display_name': capability_meta.name,
                    'logical_name': capability_meta.name,
                    'canonical_id': canonical_id,
                    'environment_type': env_type_name,
                    'signature': inspect.signature(func),
                }
                registry.env_fovs[canonical_id] = entry
                # 追加短ID，行为与 register_environment_capabilities 对齐
                registry.env_fovs[f"env.{capability_meta.func_name}"] = entry
                registry.env_fovs[capability_meta.name] = entry
                logger.debug(
                    "Registered FoV function: %s (canonical=%s) from %s (legacy path)",
                    name,
                    canonical_id,
                    cls.__name__,
                )

        for name, method in inspect.getmembers(environment, predicate=inspect.ismethod):
            if hasattr(method, '_is_rule') and method._is_rule:
                entry = {
                    'function': method,
                    'description': f"Rule function from {environment.__class__.__name__}",
                    'signature': inspect.signature(method)
                }
                registry.rules[name] = entry
                registry.rules[f"env.{name}"] = entry
                logger.debug(
                    "Registered rule function: %s from %s (legacy path)",
                    name,
                    environment.__class__.__name__,
                )

    def get_experiment_info(self) -> Dict[str, Any]:
        """Get current experiment information."""
        info = {
            'save_dir': self.save_dir,
            'is_initialized': self.is_initialized,
            'registry_stats': self._get_registry_stats(),
            'num_registries': len(self.registries)
        }

        if self.current_world_state:
            info['current_world_state'] = self.current_world_state.get_state_summary()

        # Add persistence info
        info['persistence'] = self.persistence_manager.get_experiment_info()

        return info

    async def validate_configuration(self) -> List[str]:
        """
        Validate the loaded configuration.

        Returns:
            List of validation warnings/errors
        """
        issues = []

        # Check required sections
        required_sections = ['agents', 'environment', 'schedule']
        for section in required_sections:
            if section not in self.config:
                issues.append(f"Missing required section: '{section}'")

        # Validate agents
        agents_config = self.config.get('agents', [])
        agent_ids = set()

        for i, agent_config in enumerate(agents_config):
            if 'id' not in agent_config:
                issues.append(f"Agent {i} missing required 'id' field")
            else:
                agent_id = agent_config['id']
                if agent_id in agent_ids:
                    issues.append(f"Duplicate agent ID: '{agent_id}'")
                agent_ids.add(agent_id)

        # Validate schedule
        schedule_config = self.config.get('schedule', {})
        if 'nodes' not in schedule_config:
            issues.append("Schedule missing required 'nodes' field")
        else:
            nodes = schedule_config['nodes']
            node_ids = set()

            for node in nodes:
                if 'id' not in node:
                    issues.append("Schedule node missing required 'id' field")
                else:
                    node_id = node['id']
                    if node_id in node_ids:
                        issues.append(f"Duplicate schedule node ID: '{node_id}'")
                    node_ids.add(node_id)

                    # Check dependencies reference valid nodes
                    for dep in node.get('dependencies', []):
                        if dep not in node_ids and dep not in [n.get('id') for n in nodes]:
                            issues.append(f"Node '{node_id}' depends on undefined node: '{dep}'")

        return issues

    def close(self) -> None:
        """Clean up resources and close connections."""
        try:
            # Close EventLogger
            if hasattr(self, 'event_logger') and self.event_logger:
                self.event_logger.close()

            if hasattr(self, "log_context") and self.log_context:
                self.log_context.close()

            # Close World if it exists
            if self.current_world_state:
                self.current_world_state.close()

            if hasattr(self, "persistence_manager") and self.persistence_manager:
                try:
                    self.persistence_manager.close()
                except Exception as exc:
                    logger.warning("Failed to close persistence manager: %s", exc)

            logger.info("SimEngine resources cleaned up")
        except Exception as e:
            logger.error(f"Error during SimEngine cleanup: {e}")

    def __del__(self):
        """Ensure cleanup on deletion."""
        try:
            self.close()
        except:
            pass  # Avoid errors during garbage collection

    def __repr__(self) -> str:
        return f"SimEngine(save_dir='{self.save_dir}', initialized={self.is_initialized})"
