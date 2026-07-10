"""
SimEngine V2: FunctionRegistry - Clean registry without dependency injection chaos.

Simple registry that holds function references without being injected everywhere.
Functions are registered via decorators and retrieved by Schedule during compilation.

v3.0 新增：
- 支持从新的元数据结构（LogicMeta, CapabilityMeta）自动注册函数
- register_from_logic_module() 方法用于批量注册 Logic 函数
- register_from_environment_meta() 方法用于批量注册 Environment capabilities
"""

from typing import Dict, Callable, Any, List, Optional, Set
import copy
import inspect
import logging

from .async_utils import invoke_maybe_async

logger = logging.getLogger(__name__)


def validate_strict_function_parameters(schema: Dict[str, Any]) -> None:
    """Validate the JSON Schema subset required by strict function tools.

    Strict schemas must close every object, require every declared property,
    and define the item schema for arrays. Optional values should therefore be
    represented as required nullable properties.
    """

    def includes_type(value: Any, expected: str) -> bool:
        if isinstance(value, str):
            return value == expected
        if isinstance(value, list):
            return expected in value
        return False

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            raise ValueError(f"strict function schema at {path} must be an object")
        unsupported_reference_keywords = {"$ref", "$defs", "definitions"} & set(node)
        if unsupported_reference_keywords:
            keywords = ", ".join(sorted(unsupported_reference_keywords))
            raise ValueError(
                f"strict function schema at {path} uses unsupported reference keyword(s): {keywords}"
            )

        if includes_type(node.get("type"), "object"):
            properties = node.get("properties")
            if not isinstance(properties, dict):
                raise ValueError(f"strict function schema object at {path} must define properties")
            if node.get("additionalProperties") is not False:
                raise ValueError(
                    f"strict function schema object at {path} must set additionalProperties=false"
                )
            required = node.get("required")
            if not isinstance(required, list) or set(required) != set(properties):
                raise ValueError(
                    f"strict function schema object at {path} must require every declared property; "
                    "represent optional values as nullable"
                )
            for property_name, property_schema in properties.items():
                visit(property_schema, f"{path}.{property_name}")

        if includes_type(node.get("type"), "array"):
            if "items" not in node:
                raise ValueError(f"strict function schema array at {path} must define items")
            visit(node["items"], f"{path}[]")

        for keyword in ("anyOf", "oneOf", "allOf"):
            alternatives = node.get(keyword)
            if alternatives is None:
                continue
            if not isinstance(alternatives, list) or not alternatives:
                raise ValueError(f"strict function schema {keyword} at {path} must be a non-empty array")
            for index, alternative in enumerate(alternatives):
                visit(alternative, f"{path}.{keyword}[{index}]")

    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise ValueError("strict function parameters must use a root object schema")
    visit(schema, "$")


def _json_type_for_annotation(annotation: Any) -> str:
    if annotation in {str, "str"}:
        return "string"
    if annotation in {int, "int"}:
        return "integer"
    if annotation in {float, "float"}:
        return "number"
    if annotation in {bool, "bool"}:
        return "boolean"
    if annotation in {list, List, "list"}:
        return "array"
    if annotation in {dict, Dict, "dict"}:
        return "object"
    return "string"


def _parameters_schema_from_signature(
    signature: inspect.Signature,
    *,
    injected_names: Set[str],
) -> Dict[str, Any]:
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for param_name, param in signature.parameters.items():
        if param_name in injected_names or param_name in {"self", "cls"}:
            continue
        if param.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        schema: Dict[str, Any] = {"type": _json_type_for_annotation(param.annotation)}
        if param.default is not inspect.Parameter.empty:
            schema["default"] = param.default
        else:
            required.append(param_name)
        properties[param_name] = schema
    result: Dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


class FunctionRegistry:
    """
    Clean function registry without dependency injection chaos.
    Stores function references to be retrieved during schedule compilation.
    """
    
    def __init__(self):
        # Environment functions
        self.env_fovs: Dict[str, Dict[str, Any]] = {}
        self.env_empowers: Dict[str, Dict[str, Any]] = {}  # Missing empower functions
        self.env_agent_tools: Dict[str, Dict[str, Any]] = {} # <-- ADDED
        self.env_rules: Dict[str, Dict[str, Any]] = {}

        # Environment rules - 新增专用的 rules 字典
        self.rules: Dict[str, Dict[str, Any]] = {}

        # Agent functions
        self.agent_actions: Dict[str, Dict[str, Any]] = {}
        self.agent_rules: Dict[str, Dict[str, Any]] = {}

        # Behavior functions (NEW for Schedule integration)
        self.behaviors: Dict[str, Dict[str, Any]] = {}

        # Schedule functions
        self.selectors: Dict[str, Dict[str, Any]] = {}
        self.operators: Dict[str, Dict[str, Any]] = {}
        self.converters: Dict[str, Dict[str, Any]] = {}
    
    @property
    def env(self):
        """Environment function registrar."""
        return EnvironmentRegistry(self)
    
    @property
    def agent(self):
        """Agent function registrar."""
        return AgentRegistry(self)
    
    @property
    def sched(self):
        """Schedule function registrar."""
        return ScheduleRegistry(self)


class EnvironmentRegistry:
    """Registry for environment-related functions."""
    
    def __init__(self, main_registry: FunctionRegistry):
        self._registry = main_registry
    
    def fov(self, desc: str = "", name: str = None):
        """Register a field of view function.
        
        Function signature: def func(agent, env) -> str
        - agent: Agent object
        - env: Environment object
        - Returns: Serializable string result
        """
        def decorator(func: Callable):
            func_name = name or func.__name__

            # Validate function signature
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            if len(params) != 2:
                raise ValueError(f"FoV function {func_name} must have exactly 2 parameters (agent, env)")

            # Store function with metadata（将 func_name 视为 canonical id 占位符）
            entry = {
                'function': func,
                'description': desc,
                'display_name': func_name,
                'logical_name': func_name,
                'canonical_id': func_name,
                'environment_type': None,
                'signature': sig,
                'source': 'experiment',
                'kind': 'fov',
                'func_name': func_name,
                'parameters': _parameters_schema_from_signature(
                    sig,
                    injected_names={"agent", "env", "environment", "world", "context", "params"},
                ),
            }
            self._registry.env_fovs[func_name] = entry
            self._registry.env_fovs[f"env.{func_name}"] = entry

            logger.debug(f"Registered environment FoV function: {func_name}")
            return func
        
        return decorator

    def rule(self, desc: str = "", name: str = None):
        """Register an environment rule function.

        Function signature: async def func(environment, world, params) -> Any
        """
        def decorator(func: Callable):
            func_name = name or func.__name__
            canonical_id = f"env.{func_name}"
            entry = {
                'function': func,
                'description': desc,
                'signature': inspect.signature(func),
                'source': 'experiment',
                'kind': 'rule',
                'canonical_id': canonical_id,
                'display_name': func_name,
                'func_name': func_name,
            }
            entry['parameters'] = _parameters_schema_from_signature(
                entry['signature'],
                injected_names={"env", "environment", "world", "context", "params"},
            )
            self._registry.env_rules[canonical_id] = entry
            self._registry.env_rules[func_name] = entry  # 兼容早期依赖
            # 规则需要被 schedule 调度器看到，因此也写入统一 rules 字典
            self._registry.rules[canonical_id] = entry
            self._registry.rules[func_name] = entry

            logger.debug(f"Registered environment rule function: {canonical_id}")
            return func

        return decorator

    def action(
        self,
        desc: str = "",
        name: str = None,
        tags: Optional[List[str]] = None,
        strict: bool = False,
        parameters_schema: Optional[Dict[str, Any]] = None,
    ):
        """Register an experiment-specific environment action.

        These actions are exposed as LLM tools through ``instruct(..., actions=[...])``
        and should represent capabilities provided by the environment for this
        experiment, not shortcuts around the agent loop.

        Supported injected parameter names include ``agent``, ``env``/
        ``environment``, ``world``, ``context``, ``agent_ids``, and ``params``.
        Other parameters are exposed to the model as action arguments.

        ``strict=True`` requires an explicit, strict-compatible
        ``parameters_schema``. Provider support is also required; unsupported
        providers fail normally rather than silently downgrading validation.
        """

        def decorator(func: Callable):
            func_name = name or func.__name__
            canonical_id = f"env.{func_name}"
            sig = inspect.signature(func)
            if strict and parameters_schema is None:
                raise ValueError(
                    f"strict experiment action {canonical_id} requires an explicit parameters_schema"
                )
            exposed_parameters = (
                copy.deepcopy(parameters_schema)
                if parameters_schema is not None
                else _parameters_schema_from_signature(
                    sig,
                    injected_names={
                        "agent",
                        "agents",
                        "agent_ids",
                        "env",
                        "environment",
                        "world",
                        "context",
                        "params",
                    },
                )
            )
            if strict:
                validate_strict_function_parameters(exposed_parameters)
            exposed_signature_names: Set[str] = set()
            required_signature_names: Set[str] = set()
            accepts_schema_mapping = False
            injected_names = {
                "agent",
                "agents",
                "agent_ids",
                "env",
                "environment",
                "world",
                "context",
                "params",
            }
            for param_name, param in sig.parameters.items():
                if param_name in {"self", "cls"}:
                    continue
                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    continue
                if param.kind == inspect.Parameter.VAR_KEYWORD or param_name == "params":
                    accepts_schema_mapping = True
                    continue
                if param_name in injected_names:
                    continue
                exposed_signature_names.add(param_name)
                if param.default is inspect.Parameter.empty:
                    required_signature_names.add(param_name)
            schema_property_names = set((exposed_parameters.get("properties") or {}).keys())
            missing_required = required_signature_names - schema_property_names
            if missing_required:
                raise ValueError(
                    f"experiment action {canonical_id} parameters_schema omits required function "
                    f"parameter(s): {', '.join(sorted(missing_required))}"
                )
            unknown_properties = schema_property_names - exposed_signature_names
            if unknown_properties and not accepts_schema_mapping:
                raise ValueError(
                    f"experiment action {canonical_id} parameters_schema declares unknown "
                    f"property/properties: {', '.join(sorted(unknown_properties))}"
                )
            action_tags = list(
                dict.fromkeys(
                    [
                        "environment",
                        "experiment",
                        canonical_id,
                        func_name,
                        *(tags or []),
                    ]
                )
            )
            entry = {
                'function': func,
                'description': desc,
                'signature': sig,
                'source': 'experiment',
                'kind': 'action',
                'canonical_id': canonical_id,
                'display_name': func_name,
                'func_name': func_name,
                'environment_type': None,
                'tags': action_tags,
                'parameters': exposed_parameters,
            }
            if strict:
                entry['strict'] = True
            self._registry.env_agent_tools[canonical_id] = entry
            self._registry.env_agent_tools[func_name] = entry

            logger.debug(f"Registered experiment environment action function: {canonical_id}")
            return func

        return decorator
    
    def empower(self, desc: str = "", name: str = None):
        """Register an environment empowerment function.
        
        Function signature: async def func(environment, world, agent_ids, params) -> Any
        - environment: Environment object (proxy-enabled)
        - world: World object (unified state container)
        - agent_ids: List of agent IDs to empower
        - params: Empowerment parameters
        - Returns: Any result (will be wrapped in proper result format)
        """
        def decorator(func: Callable):
            func_name = name or func.__name__
            
            self._registry.env_empowers[func_name] = {
                'function': func,
                'description': desc,
                'signature': inspect.signature(func)
            }
            
            logger.debug(f"Registered environment empower function: {func_name}")
            return func
        
        return decorator


class AgentRegistry:
    """Registry for agent-related functions."""
    
    def __init__(self, main_registry: FunctionRegistry):
        self._registry = main_registry
    
    def action(self, desc: str = "", name: str = None):
        """Register an agent action function.
        
        Function signature: async def func(agent_ids, world, params) -> Any
        - agent_ids: List of agent IDs to perform action on
        - world: World object (unified state container)
        - params: Action parameters
        - Returns: Any result (will be wrapped in proper result format)
        """
        def decorator(func: Callable):
            func_name = name or func.__name__
            
            self._registry.agent_actions[func_name] = {
                'function': func,
                'description': desc,
                'signature': inspect.signature(func),
                'source': 'experiment',
                'kind': 'action',
                'canonical_id': func_name,
                'display_name': func_name,
                'func_name': func_name,
                'parameters': _parameters_schema_from_signature(
                    inspect.signature(func),
                    injected_names={"agent", "agents", "agent_ids", "env", "environment", "world", "context", "params"},
                ),
            }
            
            logger.debug(f"Registered agent action function: {func_name}")
            return func
        
        return decorator

    def rule(self, desc: str = "", name: str = None):
        """Register an agent-scoped rule function (legacy compatibility)."""
        def decorator(func: Callable):
            func_name = name or func.__name__
            entry = {
                'function': func,
                'description': desc,
                'signature': inspect.signature(func),
                'source': 'experiment',
                'kind': 'rule',
                'canonical_id': func_name,
                'display_name': func_name,
                'func_name': func_name,
            }
            entry['parameters'] = _parameters_schema_from_signature(
                entry['signature'],
                injected_names={"agent", "agents", "agent_ids", "env", "environment", "world", "context", "params"},
            )
            self._registry.agent_rules[func_name] = entry
            self._registry.rules[func_name] = entry

            logger.debug(f"Registered agent rule function: {func_name}")
            return func

        return decorator


class ScheduleRegistry:
    """Registry for schedule-related functions."""
    
    def __init__(self, main_registry: FunctionRegistry):
        self._registry = main_registry
    
    def selector(self, desc: str = "", name: str = None):
        """Register a selector function.
        
        Function signature: async def func(params, context) -> List[Agent]
        - params: Selector parameters from configuration
        - context: ExecutionContext with world, step, node references
        - Returns: List of Agent objects
        
        Note: Legacy signature (world_state, params) -> List[str] is also supported
        """
        def decorator(func: Callable):
            func_name = name or func.__name__
            
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            if len(params) != 2:
                raise ValueError(f"Selector {func_name} must have exactly 2 parameters (params, context) or legacy (world_state, params)")
            
            self._registry.selectors[func_name] = {
                'function': func,
                'description': desc,
                'signature': sig
            }
            
            logger.debug(f"Registered selector function: {func_name}")
            return func
        
        return decorator
    
    def operator(self, desc: str = "", name: str = None):
        """Register an operator function.
        
        Function signature: async def func(agents, params, context) -> BaseOperatorResult
        - agents: List of Agent objects
        - params: Operator parameters from configuration
        - context: ExecutionContext with world, step, node references
        - Returns: BaseOperatorResult instance
        
        Note: Legacy signature (agent_ids, world_state, params) -> List[StatePatch] is also supported
        """
        def decorator(func: Callable):
            func_name = name or func.__name__
            
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            if len(params) != 3:
                raise ValueError(f"Operator {func_name} must have exactly 3 parameters (agents, params, context) or legacy (agent_ids, world_state, params)")
            
            self._registry.operators[func_name] = {
                'function': func,
                'description': desc,
                'signature': sig
            }
            
            logger.debug(f"Registered operator function: {func_name}")
            return func
        
        return decorator
    
    def converter(self, desc: str = "", name: str = None):
        """Register a converter function.
        
        Function signature: async def func(operator_results, params, context) -> Dict[str, Any]
        - operator_results: List of BaseOperatorResult instances
        - params: Converter parameters from configuration  
        - context: ExecutionContext with world, step, node references
        - Returns: Dict of converted output data
        """
        def decorator(func: Callable):
            func_name = name or func.__name__
            
            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            if len(params) not in [2, 3]:  # Allow both old (2) and new (3) signatures
                raise ValueError(f"Converter {func_name} must have 2 parameters (results, params) or 3 parameters (results, params, context)")
            
            self._registry.converters[func_name] = {
                'function': func,
                'description': desc,
                'signature': sig
            }
            
            logger.debug(f"Registered converter function: {func_name}")
            return func

        return decorator

    def behavior(self, desc: str = "", name: str = None):
        """Register a behavior function for Schedule integration.

        Function signature: async def func(agent, env, **kwargs) -> Dict[str, Any] | BaseOperatorResult
        - agent: Agent object (the target agent)
        - env: Environment proxy (created from world.get_environment())
        - **kwargs: Behavior parameters (from schedule config + input_mapping results)
        - Returns: Dict with execution info OR BaseOperatorResult instance

        Example:
        @registry.sched.behavior("Update agent's digital trust level")
        async def update_digital_trust(agent, env, trust_delta=0.1, source="unknown"):
            # Business logic here
            agent.state["digital_trust"] = agent.state.get("digital_trust", 0.5) + trust_delta

            return {
                "agent_id": agent.id,
                "trust_before": agent.state.get("digital_trust", 0.5) - trust_delta,
                "trust_after": agent.state["digital_trust"],
                "delta_applied": trust_delta,
                "source": source
            }
        """
        def decorator(func: Callable):
            func_name = name or func.__name__

            sig = inspect.signature(func)
            params = list(sig.parameters.keys())
            if len(params) < 2:
                raise ValueError(f"Behavior {func_name} must have at least 2 parameters (agent, env, ...)")

            # Validate parameter names (updated for unified interface)
            if params[0] != "agent":
                logger.warning(f"Behavior {func_name} first parameter should be 'agent', got '{params[0]}'")
            if params[1] != "env":
                logger.warning(f"Behavior {func_name} second parameter should be 'env', got '{params[1]}'")

            self._registry.behaviors[func_name] = {
                'function': func,
                'description': desc,
                'signature': sig,
                'source': 'experiment',
                'kind': 'behavior',
                'canonical_id': func_name,
                'display_name': func_name,
                'func_name': func_name,
                'parameters': _parameters_schema_from_signature(
                    sig,
                    injected_names={"agent", "env", "environment", "world", "context", "params"},
                ),
            }

            logger.debug(f"Registered behavior function: {func_name}")
            return func

        return decorator


def get_registry_stats(registry: FunctionRegistry) -> Dict[str, Any]:
    """Get statistics about registered functions."""
    return {
        'environment': {
            'fovs': len(registry.env_fovs),
            'agent_tools': len(registry.env_agent_tools),
        },
        'agent': {
            'actions': len(registry.agent_actions)
        },
        'behaviors': len(registry.behaviors),
        'schedule': {
            'selectors': len(registry.selectors),
            'operators': len(registry.operators),
            'converters': len(registry.converters)
        },
        'total_functions': (
            len(registry.env_fovs) + len(registry.env_agent_tools) +
            len(registry.agent_actions) + len(registry.behaviors) +
            len(registry.selectors) + len(registry.operators) + len(registry.converters)
        )
    }


# =============================================================================
# v3.0: 新增批量注册方法
# =============================================================================

def _extract_logic_metas_from_module(module) -> List[Any]:
    """从模块函数对象提取 LogicMeta，作为 __logic_functions__ 缺失时的兜底。"""
    from .decorators import LOGIC_META_ATTR  # 局部导入避免循环

    metas: List[Any] = []
    seen: set[tuple[Any, Any]] = set()
    for attr in module.__dict__.values():
        if not callable(attr) or not hasattr(attr, LOGIC_META_ATTR):
            continue
        meta = getattr(attr, LOGIC_META_ATTR)
        key = (getattr(meta, "kind", None), getattr(meta, "func_name", None))
        if key in seen:
            continue
        seen.add(key)
        metas.append(meta)
    return metas

def register_logic_module(registry: FunctionRegistry, module) -> int:
    """
    从 Logic 模块中批量注册函数

    读取模块的 __logic_functions__ 属性，将所有 LogicMeta 注册到 FunctionRegistry。

    Args:
        registry: FunctionRegistry 实例
        module: Python 模块对象（需要有 __logic_functions__ 属性）

    Returns:
        注册的函数数量
    """
    from .decorators import MODULE_LOGICS_ATTR

    if hasattr(module, MODULE_LOGICS_ATTR):
        logic_metas = list(getattr(module, MODULE_LOGICS_ATTR) or [])
    else:
        logger.debug(f"Module {module.__name__} has no {MODULE_LOGICS_ATTR} attribute, fallback to function scan")
        logic_metas = _extract_logic_metas_from_module(module)
        if not logic_metas:
            logger.debug(f"Module {module.__name__} contains no logic-decorated functions")
            return 0

    registered_count = 0

    for meta in logic_metas:
        # meta 是 LogicMeta 实例
        if not hasattr(meta, 'kind') or not hasattr(meta, '_func_ref'):
            logger.warning(f"Invalid LogicMeta in module {module.__name__}")
            continue

        func = meta._func_ref
        if func is None:
            logger.warning(f"LogicMeta {meta.name} has no function reference")
            continue

        module_name = getattr(meta, 'module_path', None) or getattr(module, "__name__", "")
        func_name = getattr(meta, 'func_name', None)
        if not module_name or not func_name:
            logger.warning(f"LogicMeta {meta.name} 缺少 module_name/func_name，跳过注册")
            continue
        canonical_id = f"{module_name}.{func_name}"

        # 根据 kind 注册到不同的字典
        if meta.kind == 'behavior':
            registry.behaviors[canonical_id] = {
                'function': func,
                'description': meta.description,
                'signature': inspect.signature(func),
                'meta': meta,  # 保留完整元数据
                'display_name': meta.name,
                'module': module_name,
                'func_name': func_name,
                'canonical_id': canonical_id,
                'source': 'experiment',
                'kind': 'behavior',
            }
            registered_count += 1
            logger.debug(f"Registered behavior: {canonical_id} from {module.__name__}")

        elif meta.kind == 'rule':
            registry.rules[canonical_id] = {
                'function': func,
                'description': meta.description,
                'signature': inspect.signature(func),
                'meta': meta,
                'display_name': meta.name,
                'module': module_name,
                'func_name': func_name,
                'canonical_id': canonical_id,
                'source': 'experiment',
                'kind': 'rule',
            }
            registered_count += 1
            logger.debug(f"Registered rule: {canonical_id} from {module.__name__}")

        elif meta.kind == 'action':
            registry.agent_actions[canonical_id] = {
                'function': func,
                'description': meta.description,
                'signature': inspect.signature(func),
                'meta': meta,
                'display_name': meta.name,
                'module': module_name,
                'func_name': func_name,
                'canonical_id': canonical_id,
                'source': 'experiment',
                'kind': 'action',
            }
            registered_count += 1
            logger.debug(f"Registered action: {canonical_id} from {module.__name__}")

        elif meta.kind == 'selector':
            selector_sig = inspect.signature(func)
            parameters = [
                param for param in selector_sig.parameters.values()
                if param.name not in {'self', 'cls'}
            ]

            if not parameters or parameters[0].name != 'agents':
                logger.error(
                    "Selector %s.%s 的第一个参数必须命名为 'agents'，当前签名：%s",
                    module_name,
                    func_name,
                    selector_sig,
                )
                continue

            expects_env = False
            if len(parameters) >= 2:
                second_param = parameters[1]
                if second_param.name != 'env':
                    logger.error(
                        "Selector %s.%s 的第二个参数必须命名为 'env'，当前签名：%s",
                        module_name,
                        func_name,
                        selector_sig,
                    )
                    continue
                expects_env = True

            schema_properties = {}
            if isinstance(meta.parameters_schema, dict):
                schema_properties = meta.parameters_schema.get("properties", {}) or {}

            allowed_parameter_names: Set[str] = set(schema_properties.keys())
            allowed_parameter_names.update(meta.extra_parameters or [])
            if meta.context_parameter_name:
                allowed_parameter_names.discard(meta.context_parameter_name)

            required_names: Set[str] = set()
            if isinstance(meta.parameters_schema, dict):
                required_names.update(meta.parameters_schema.get("required", []) or [])

            reserved_keys = {"function", "type", "desc", "description", "name"}

            async def selector_wrapper(params: Dict[str, Any], context: Any, *, _func=func,
                                       _meta=meta, _expects_env=expects_env,
                                       _allowed=allowed_parameter_names,
                                       _required=required_names) -> List[Any]:
                if context is None or getattr(context, "world", None) is None:
                    raise ValueError(f"Selector '{canonical_id}' 需要有效的 ExecutionContext（缺少 world）")

                provided = dict(params or {})
                provided.pop("input_mapping", None)  # 预留未来支持
                base_params = {
                    key: value for key, value in provided.items()
                    if key not in reserved_keys
                }

                if not _meta.accepts_var_keyword:
                    unknown_keys = set(base_params.keys()) - _allowed
                    if unknown_keys:
                        raise ValueError(
                            f"Selector '{canonical_id}' 收到了未声明的参数：{sorted(unknown_keys)}"
                        )

                missing_required = {
                    key for key in _required
                    if key not in base_params
                }
                if missing_required:
                    raise ValueError(
                        f"Selector '{canonical_id}' 缺少必填参数：{sorted(missing_required)}"
                    )

                call_kwargs = dict(base_params)
                if _meta.context_parameter_name:
                    call_kwargs[_meta.context_parameter_name] = context

                world = context.world
                agents = world.get_all_agents()
                environment = world.get_environment() if _expects_env else None

                try:
                    if _expects_env:
                        result = await invoke_maybe_async(_func, agents, environment, **call_kwargs)
                    else:
                        result = await invoke_maybe_async(_func, agents, **call_kwargs)
                except TypeError as exc:
                    raise ValueError(
                        f"Selector '{canonical_id}' 参数绑定失败，请检查函数签名与 Schedule 配置是否匹配：{exc}"
                    ) from exc

                if result is None:
                    return []
                if not isinstance(result, list):
                    raise TypeError(
                        f"Selector '{canonical_id}' 必须返回 list[Agent]，当前返回类型为 {type(result).__name__}"
                    )
                return result

            registry.selectors[canonical_id] = {
                'function': selector_wrapper,
                'description': meta.description,
                'signature': inspect.signature(selector_wrapper),
                'meta': meta,
                'display_name': meta.name,
                'module': module_name,
                'func_name': func_name,
                'canonical_id': canonical_id,
                'original_function': func,
                'original_signature': selector_sig,
            }
            registered_count += 1
            logger.debug(f"Registered selector: {canonical_id} from {module.__name__}")

        elif meta.kind == 'fov':
            registry.env_fovs[canonical_id] = {
                'function': func,
                'description': meta.description,
                'display_name': meta.name,
                'logical_name': meta.name,
                'canonical_id': canonical_id,
                'environment_type': meta.target_env_type,
                'signature': inspect.signature(func),
                'meta': meta,
                'module': module_name,
                'func_name': func_name
            }
            registered_count += 1
            logger.debug(f"Registered fov: {canonical_id} from {module.__name__}")

    logger.info(f"Registered {registered_count} functions from module {module.__name__}")
    return registered_count


def register_environment_capabilities(registry: FunctionRegistry, env_meta, env_instance) -> int:
    """
    从 EnvironmentMeta 批量注册 capabilities

    读取 EnvironmentMeta.capabilities，将所有 CapabilityMeta 的函数注册到 FunctionRegistry。
    🔑 注意：这里需要从环境**实例**绑定方法，而不是从类定义。

    Args:
        registry: FunctionRegistry 实例
        env_meta: EnvironmentMeta 实例（from decorators.py）
        env_instance: Environment 实例（用于获取绑定的方法）

    Returns:
        注册的函数数量
    """
    if not env_meta or not hasattr(env_meta, 'capabilities'):
        return 0

    registered_count = 0

    for cap_meta in env_meta.capabilities:
        # cap_meta 是 CapabilityMeta 实例
        if not hasattr(cap_meta, 'kind') or not hasattr(cap_meta, 'func_name'):
            logger.warning(f"Invalid CapabilityMeta in {env_meta.type_name}")
            continue

        # 🔑 从环境实例获取绑定的方法（而不是从类）
        if not hasattr(env_instance, cap_meta.func_name):
            logger.warning(f"Environment instance has no method {cap_meta.func_name}")
            continue

        bound_method = getattr(env_instance, cap_meta.func_name)

        # 根据 kind 注册到不同的字典
        if cap_meta.kind == 'action':
            canonical_id = f"env.{cap_meta.func_name}"
            action_tags = list(dict.fromkeys([*(cap_meta.tags or []), canonical_id, cap_meta.name, "environment"]))
            entry = {
                'function': bound_method,
                'description': cap_meta.description,
                'parameters': cap_meta.parameters_schema,
                'signature': inspect.signature(bound_method),
                'tags': action_tags,
                'meta': cap_meta,
                'source': 'environment',
                'kind': 'action',
                'environment_type': env_meta.type_name,
                'func_name': cap_meta.func_name,
                'display_name': cap_meta.name,
                'canonical_id': canonical_id,
            }
            registry.env_agent_tools[canonical_id] = entry
            # 兼容旧引用（使用装饰器名称）
            registry.env_agent_tools[cap_meta.name] = entry
            registered_count += 1
            logger.debug(f"Registered environment action: {canonical_id}")

        elif cap_meta.kind == 'fov':
            fov_signature = inspect.signature(bound_method)
            fov_param_count = len(fov_signature.parameters)
            if fov_param_count != 2:
                logger.warning(
                    "Environment FoV '%s' in '%s' has non-standard signature %s "
                    "(expected 2 params: agent, env).",
                    cap_meta.func_name,
                    env_meta.type_name,
                    fov_signature,
                )
            canonical_id = f"environments.{env_meta.type_name}.fovs.{cap_meta.name}"
            entry = {
                'function': bound_method,
                'description': cap_meta.description,
                'display_name': cap_meta.name,
                'logical_name': cap_meta.name,
                'canonical_id': canonical_id,
                'environment_type': env_meta.type_name,
                'signature': fov_signature,
                'meta': cap_meta,
                'source': 'environment',
                'kind': 'fov',
                'parameters': cap_meta.parameters_schema,
                'tags': cap_meta.tags,
                'func_name': cap_meta.func_name,
            }
            registry.env_fovs[canonical_id] = entry
            # 注册 env.<func_name> 短 ID，便于 schedule 引用
            env_short_id = f"env.{cap_meta.func_name}"
            registry.env_fovs[env_short_id] = entry
            # 同时注册短名
            registry.env_fovs[cap_meta.name] = entry
            registered_count += 1
            logger.debug(f"Registered environment fov: {env_short_id} (canonical: {canonical_id})")

        elif cap_meta.kind == 'rule':
            canonical_id = f"env.{cap_meta.func_name}"
            entry = {
                'function': bound_method,
                'description': cap_meta.description,
                'signature': inspect.signature(bound_method),
                'meta': cap_meta,
                'source': 'environment',
                'kind': 'rule',
                'environment_type': env_meta.type_name,
                'func_name': cap_meta.func_name,
                'display_name': cap_meta.name,
                'canonical_id': canonical_id,
                'parameters': cap_meta.parameters_schema,
                'tags': cap_meta.tags,
            }
            registry.env_rules[canonical_id] = entry
            registry.env_rules[cap_meta.name] = entry
            registry.rules[canonical_id] = entry
            # 兼容旧名称写法
            registry.rules[cap_meta.name] = entry
            registered_count += 1
            logger.debug(f"Registered environment rule: {canonical_id}")

        elif cap_meta.kind == 'behavior':
            canonical_id = f"env.{cap_meta.func_name}"
            entry = {
                'function': bound_method,
                'description': cap_meta.description,
                'signature': inspect.signature(bound_method),
                'meta': cap_meta,
                'source': 'environment',
                'kind': 'behavior',
                'environment_type': env_meta.type_name,
                'func_name': cap_meta.func_name,
                'display_name': cap_meta.name,
                'canonical_id': canonical_id,
                'parameters': cap_meta.parameters_schema,
                'tags': cap_meta.tags,
            }
            registry.behaviors[canonical_id] = entry
            registry.behaviors[cap_meta.name] = entry
            registered_count += 1
            logger.debug(f"Registered environment behavior: {canonical_id}")

    logger.info(f"Registered {registered_count} capabilities from environment {env_meta.type_name}")
    return registered_count
