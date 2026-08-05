# libs/society0/src/society0/decorators.py
"""
装饰器系统（v3.0 - 完全重写）

本模块提供两套装饰器：
1. 内置装饰器（用于 society0 内部）：@env_type, @capability
2. 外部装饰器（用于 logics 脚本）：@logic.behavior, @logic.rule

核心设计原则：
- 装饰器在 import 时执行，利用 inspect 模块获取函数签名
- 元数据附着在函数/类上（`__logic_meta__`, `__env_meta__`, `__capability__`）
- 支持自动 schema 生成（基于 pydantic）和手动 schema 覆盖
- 模块级注册表用于快速发现
"""
from __future__ import annotations

import inspect
import sys
from dataclasses import replace
from typing import Any, Callable, Dict, List, Optional, Type

# 导入元数据结构
from .meta import LogicMeta, EnvironmentMeta, CapabilityMeta

# 尝试导入 pydantic（用于自动 schema 生成）
try:
    from pydantic import BaseModel, create_model
    from pydantic.json_schema import model_json_schema
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False


# =============================================================================
# 常量
# =============================================================================

ENV_META_ATTR = "__env_meta__"  # 附着在环境类上的元数据属性名
CAPABILITY_META_ATTR = "__capability__"  # 附着在方法上的能力元数据属性名
LOGIC_META_ATTR = "__logic_meta__"  # 附着在 logic 函数上的元数据属性名
MODULE_LOGICS_ATTR = "__logic_functions__"  # 模块级 logic 函数列表


def _normalize_action_agent_types(
    *,
    role: Optional[str] = None,
    roles: Optional[List[str]] = None,
) -> List[str]:
    """校验并归一化 action 的 Agent.type 限制。"""
    if role is not None and roles is not None:
        raise ValueError("action: role 与 roles 不能同时指定")

    if role is not None:
        if not isinstance(role, str) or not role.strip():
            raise ValueError("action: role 必须是非空字符串")
        return [role.strip()]

    if roles is None:
        return []
    if not isinstance(roles, list):
        raise ValueError("action: roles 必须是非空 list[str]")
    if not roles:
        raise ValueError("action: roles 必须是非空 list[str]")

    normalized: List[str] = []
    for item in roles:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("action: roles 的每一项都必须是非空字符串")
        value = item.strip()
        if value not in normalized:
            normalized.append(value)
    return normalized


# =============================================================================
# Schema 自动生成（基于 pydantic）
# =============================================================================

def _generate_schema_from_signature(
    func: Callable,
    kind: Optional[str] = None
) -> tuple[Dict[str, Any], Optional[Dict[str, str]], bool, List[str]]:
    """
    从函数签名自动生成 parameters_schema 和参数列表信息

    Args:
        func: 目标函数
        kind: logic 类型（'behavior', 'rule', 'action', 'fov'），用于识别约定参数

    Returns:
        (parameters_schema, context_parameter_info, accepts_var_keyword, extra_parameters)
        - parameters_schema: JSON Schema 格式的参数定义
        - context_parameter_info: 上下文注入参数的信息 {'type': ..., 'name': ...}
        - accepts_var_keyword: 函数是否接受 **kwargs (bool)
        - extra_parameters: 显式声明的参数名列表（不包括约定参数和 context）
    """
    if not HAS_PYDANTIC:
        # 回退到基础版本
        return _generate_schema_fallback(func, kind)

    sig = inspect.signature(func)
    params = sig.parameters

    # 识别约定参数（不包含在 schema 中）
    convention_params = {'self', 'cls'}
    context_param = None
    accepts_var_keyword = False  # 🔑 新增：检测 **kwargs
    extra_parameters = []  # 🔑 新增：收集显式参数列表

    if kind in ['behavior', 'action']:
        # behavior: agent, env, **kwargs
        # action: agent, env, **kwargs (统一接口)
        convention_params.update({'agent', 'env'})
        # 检查是否有 ExecutionContext 类型的参数
        for param_name, param in params.items():
            if param_name in convention_params:
                continue
            annotation = param.annotation
            if annotation != inspect.Parameter.empty:
                annotation_str = str(annotation)
                if 'ExecutionContext' in annotation_str or param_name == 'context':
                    context_param = {
                        'type': annotation_str if 'ExecutionContext' in annotation_str else 'society0.core_data.ExecutionContext',
                        'name': param_name
                    }
                    convention_params.add(param_name)
                    break

    elif kind in ['rule', 'fov']:
        # rule: env, **kwargs (修改：不再使用 world)
        # fov: agent, env, **kwargs
        if kind == 'rule':
            convention_params.update({'env'})
        else:  # fov
            convention_params.update({'agent', 'env'})

        for param_name, param in params.items():
            if param_name in convention_params:
                continue
            annotation = param.annotation
            if annotation != inspect.Parameter.empty:
                annotation_str = str(annotation)
                if 'ExecutionContext' in annotation_str or param_name == 'context':
                    context_param = {
                        'type': annotation_str if 'ExecutionContext' in annotation_str else 'society0.core_data.ExecutionContext',
                        'name': param_name
                    }
                    convention_params.add(param_name)
                    break

    elif kind == 'selector':
        # selector: agents, env, **kwargs（context 可选）
        convention_params.update({'agents', 'env'})
        for param_name, param in params.items():
            if param_name in convention_params:
                continue
            annotation = param.annotation
            if annotation != inspect.Parameter.empty:
                annotation_str = str(annotation)
                if 'ExecutionContext' in annotation_str or param_name == 'context':
                    context_param = {
                        'type': annotation_str if 'ExecutionContext' in annotation_str else 'society0.core_data.ExecutionContext',
                        'name': param_name
                    }
                    convention_params.add(param_name)
                    break

    # 🔑 新增：检测 **kwargs 和收集显式参数
    pydantic_fields = {}
    for param_name, param in params.items():
        if param_name in convention_params:
            continue

        # 检测 **kwargs
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            accepts_var_keyword = True
            continue  # 不加入 pydantic_fields

        # 跳过 *args
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue

        # 收集显式参数名
        extra_parameters.append(param_name)

        # 获取类型注解
        annotation = param.annotation
        if annotation == inspect.Parameter.empty:
            annotation = Any

        # 获取默认值
        if param.default == inspect.Parameter.empty:
            # 必需参数
            pydantic_fields[param_name] = (annotation, ...)
        else:
            # 可选参数
            pydantic_fields[param_name] = (annotation, param.default)

    # 如果没有可变参数，返回空 schema（但仍返回 accepts_var_keyword 和 extra_parameters）
    if not pydantic_fields:
        return {
            "type": "object",
            "properties": {},
            "additionalProperties": False
        }, context_param, accepts_var_keyword, extra_parameters

    # 创建 pydantic 模型
    try:
        DynamicModel = create_model(
            f'{func.__name__}_Params',
            **pydantic_fields
        )

        # 生成 JSON Schema
        schema = model_json_schema(DynamicModel, mode='serialization')

        # 清理 schema（移除 pydantic 特定字段）
        cleaned_schema = {
            "type": "object",
            "properties": schema.get("properties", {}),
            "required": schema.get("required", []),
            "additionalProperties": False
        }

        return cleaned_schema, context_param, accepts_var_keyword, extra_parameters

    except Exception as e:
        # pydantic 生成失败，回退
        fallback_schema, fallback_context, fallback_kwargs, fallback_params = _generate_schema_fallback(func, kind)
        # 使用我们已经检测到的值，而不是回退的值（更准确）
        return fallback_schema, fallback_context or context_param, accepts_var_keyword, extra_parameters


def _generate_schema_fallback(
    func: Callable,
    kind: Optional[str] = None
) -> tuple[Dict[str, Any], Optional[Dict[str, str]], bool, List[str]]:
    """
    基础版本的 schema 生成（不依赖 pydantic）

    只支持基础类型推断：str, int, float, bool, list, dict

    Returns:
        (parameters_schema, context_parameter_info, accepts_var_keyword, extra_parameters)
    """
    sig = inspect.signature(func)
    params = sig.parameters

    # 识别约定参数（统一接口）
    convention_params = {'agent', 'env', 'context', 'self', 'cls'}
    if kind == 'selector':
        convention_params.add('agents')
    accepts_var_keyword = False
    extra_parameters = []

    properties = {}
    required = []

    for param_name, param in params.items():
        if param_name in convention_params:
            continue

        # 检测 **kwargs
        if param.kind == inspect.Parameter.VAR_KEYWORD:
            accepts_var_keyword = True
            continue

        # 跳过 *args
        if param.kind == inspect.Parameter.VAR_POSITIONAL:
            continue

        # 收集显式参数名
        extra_parameters.append(param_name)

        # 基础类型推断
        annotation = param.annotation
        if annotation == inspect.Parameter.empty:
            json_type = "string"
        else:
            annotation_str = str(annotation).lower()
            if 'int' in annotation_str:
                json_type = "integer"
            elif 'float' in annotation_str or 'number' in annotation_str:
                json_type = "number"
            elif 'bool' in annotation_str:
                json_type = "boolean"
            elif 'list' in annotation_str:
                json_type = "array"
            elif 'dict' in annotation_str:
                json_type = "object"
            else:
                json_type = "string"

        properties[param_name] = {"type": json_type}

        # 判断是否必需
        if param.default == inspect.Parameter.empty:
            required.append(param_name)
        else:
            properties[param_name]["default"] = param.default

    schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False
    }

    # Note: fallback不检测 context_parameter，因为那需要类型注解
    return schema, None, accepts_var_keyword, extra_parameters


# =============================================================================
# 内置装饰器（用于 society0 内部）
# =============================================================================

def env_type(
    *,
    type_name: str,
    config_schema: Dict[str, Any],
    state_schema: Dict[str, Any],
    agent_managed_fields_schema: Dict[str, Any] = None,
    builtin_state_fields: Optional[List[Dict[str, Any]]] = None,
    display_name: Optional[str] = None,
    description: str = ""
):
    """
    环境类型装饰器（v3.0）

    用法：
    @env_type(
        type_name="grid_world",
        config_schema={...},
        state_schema={...},
        agent_managed_fields_schema={...}
    )
    class GridWorldEnv(Environment):
        ...
    """
    if not type_name or not isinstance(type_name, str):
        raise ValueError("env_type: type_name 必须为非空字符串")

    if not isinstance(config_schema, dict) or not isinstance(state_schema, dict):
        raise ValueError("env_type: config_schema 与 state_schema 必须为 dict")

    def decorator(cls: Type[Any]):
        # 生成类路径
        class_path = f"{cls.__module__}.{cls.__name__}"

        # 收集该类中所有被 @capability 标记的方法
        capabilities = []
        for method_name in dir(cls):
            try:
                method = getattr(cls, method_name)
                if hasattr(method, CAPABILITY_META_ATTR):
                    cap_meta = getattr(method, CAPABILITY_META_ATTR)
                    capabilities.append(cap_meta)
            except AttributeError:
                continue

        # 创建元数据
        meta = EnvironmentMeta(
            type_name=type_name,
            class_path=class_path,
            display_name=display_name or type_name.replace("_", " ").title(),
            description=description or inspect.getdoc(cls) or "",
            config_schema=config_schema,
            state_schema=state_schema,
            agent_managed_fields_schema=agent_managed_fields_schema or {},
            builtin_state_fields=builtin_state_fields or [],
            capabilities=capabilities,
            _env_class=cls  # 🔑 附加类引用
        )

        # 附着元数据到类
        setattr(cls, ENV_META_ATTR, meta)

        return cls

    return decorator


def capability(
    kind: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    parameters_schema: Optional[Dict[str, Any]] = None,
    return_value_schema: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    target_agent_types: Optional[List[str]] = None,
    state_access: Optional[Dict[str, List[str]]] = None,
    cache_on_step: bool = False,
    cache_on_agent: bool = False,
):
    """
    能力装饰器（v3.0）

    用于标记 Environment 类中的方法为可暴露的能力（action/fov/rule）

    用法：
    @capability(kind='action', description="移动到指定方向")
    def move(self, context: ExecutionContext, direction: str):
        ...
    """
    def decorator(func: Callable):
        func_name = name or func.__name__

        # 自动生成或使用手动提供的 schema
        if parameters_schema is None:
            params_schema, _, _, _ = _generate_schema_from_signature(func, kind=kind)
        else:
            params_schema = parameters_schema

        # 创建元数据
        meta = CapabilityMeta(
            name=func_name,
            kind=kind,
            description=description or inspect.getdoc(func) or "",
            parameters_schema=params_schema,
            return_value_schema=return_value_schema or {},
            tags=tags or [],
            target_agent_types=target_agent_types or [],
            func_name=func.__name__,
            state_access_declaration=state_access,
            cache_on_step=cache_on_step,
            cache_on_agent=cache_on_agent,
        )

        # 附着到函数
        setattr(func, CAPABILITY_META_ATTR, meta)

        return func

    return decorator


# 快捷装饰器
def fov(
    name: Optional[str] = None,
    description: Optional[str] = None,
    **kwargs
):
    """@capability(kind='fov', ...) 的快捷方式"""
    return capability(kind='fov', name=name, description=description, **kwargs)


def action(
    name: Optional[str] = None,
    description: Optional[str] = None,
    *,
    role: Optional[str] = None,
    roles: Optional[List[str]] = None,
    **kwargs
):
    """@capability(kind='action', ...) 的快捷方式。

    ``role`` 和 ``roles`` 按 ``Agent.type`` 限制动作的使用者；两者互斥。
    未指定时，该动作对所有 Agent 可用。
    """
    if "target_agent_types" in kwargs:
        if role is not None or roles is not None:
            raise ValueError(
                "action: role/roles 不能与 target_agent_types 同时指定"
            )
    else:
        kwargs["target_agent_types"] = _normalize_action_agent_types(
            role=role,
            roles=roles,
        )
    return capability(kind='action', name=name, description=description, **kwargs)


def rule(
    name: Optional[str] = None,
    description: Optional[str] = None,
    **kwargs
):
    """@capability(kind='rule', ...) 的快捷方式"""
    return capability(kind='rule', name=name, description=description, **kwargs)


def behavior(
    name: Optional[str] = None,
    description: Optional[str] = None,
    **kwargs
):
    """@capability(kind='behavior', ...) 的快捷方式"""
    return capability(kind='behavior', name=name, description=description, **kwargs)


# =============================================================================
# 外部装饰器（用于 logics 脚本）
# =============================================================================

class _LogicDecoratorFactory:
    """
    Logic 装饰器工厂（v3.0）

    提供 @logic.behavior, @logic.rule 等装饰器
    """

    def _create_decorator(
        self,
        kind: str,
        name: str,
        description: str = "",
        **kwargs
    ):
        """创建 logic 装饰器的通用逻辑"""
        def decorator(func: Callable):
            meta_kwargs = dict(kwargs)

            # 兼容更易理解的 state_access 写法
            state_access = meta_kwargs.pop('state_access', None)
            if state_access is not None and 'state_access_declaration' not in meta_kwargs:
                meta_kwargs['state_access_declaration'] = state_access

            # 自动生成或使用手动提供的 schema
            if 'parameters_schema' in meta_kwargs:
                params_schema = meta_kwargs.pop('parameters_schema')
                context_param = None
                accepts_var_keyword = False
                extra_parameters = []
            else:
                params_schema, context_param, accepts_var_keyword, extra_parameters = _generate_schema_from_signature(func, kind)

            # 创建元数据
            meta = LogicMeta(
                kind=kind,
                name=name,
                description=description or inspect.getdoc(func) or "",
                parameters_schema=params_schema,
                context_parameter_type=context_param['type'] if context_param else None,
                context_parameter_name=context_param['name'] if context_param else None,
                accepts_var_keyword=accepts_var_keyword,  # 🔑 新增
                extra_parameters=extra_parameters,  # 🔑 新增
                module_path=func.__module__,
                func_name=func.__name__,
                _func_ref=func,  # 🔑 附加函数引用
                **meta_kwargs
            )

            # 附着到函数
            setattr(func, LOGIC_META_ATTR, meta)

            # 🔑 注册到模块级列表
            _register_to_module(func.__module__, meta)

            return func

        return decorator

    def behavior(
        self,
        name: str,
        description: str = "",
        **kwargs
    ):
        """@logic.behavior 装饰器"""
        return self._create_decorator('behavior', name, description, **kwargs)

    def rule(
        self,
        name: str,
        description: str = "",
        **kwargs
    ):
        """@logic.rule 装饰器"""
        return self._create_decorator('rule', name, description, **kwargs)

    def action(
        self,
        name: str,
        description: str = "",
        *,
        role: Optional[str] = None,
        roles: Optional[List[str]] = None,
        **kwargs
    ):
        """@logic.action 装饰器"""
        if "target_agent_types" in kwargs:
            if role is not None or roles is not None:
                raise ValueError(
                    "action: role/roles 不能与 target_agent_types 同时指定"
                )
        else:
            kwargs["target_agent_types"] = _normalize_action_agent_types(
                role=role,
                roles=roles,
            )
        return self._create_decorator('action', name, description, **kwargs)

    def fov(
        self,
        name: str,
        description: str = "",
        **kwargs
    ):
        """@logic.fov 装饰器"""
        return self._create_decorator('fov', name, description, **kwargs)

    def selector(
        self,
        name: str,
        description: str = "",
        **kwargs
    ):
        """@logic.selector 装饰器"""
        return self._create_decorator('selector', name, description, **kwargs)


# 单例
logic = _LogicDecoratorFactory()


# =============================================================================
# 辅助函数
# =============================================================================

def _register_to_module(module_name: str, meta: LogicMeta):
    """将 LogicMeta 注册到模块级列表"""
    if module_name not in sys.modules:
        return

    module = sys.modules[module_name]

    if not hasattr(module, MODULE_LOGICS_ATTR):
        setattr(module, MODULE_LOGICS_ATTR, [])

    logic_list = getattr(module, MODULE_LOGICS_ATTR)
    logic_list.append(meta)
