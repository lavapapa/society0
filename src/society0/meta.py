# libs/society0/src/society0/meta.py
"""
元数据结构定义：实现资产依赖关系的全面显式化

本模块定义了所有资产的元数据 dataclass，是整个改造的"信息基石"。

核心设计原则：
1. 所有元数据都是 frozen dataclass（不可变）
2. 函数引用（_func_ref, _class_ref）与序列化字段分离
3. Schema 增强：支持 agent_visible, agent_editable, env_managed
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type


# =============================================================================
# Logic 元数据结构
# =============================================================================

@dataclass(frozen=True)
class LogicMeta:
    """Logic 的完整元数据（v3.0 - 统一 State 容器版本）

    这份元数据完整、无歧义地描述一个 Logic 函数的所有特征，
    包括其基本信息、接口契约、依赖关系和运行时定位信息。
    """
    # --- 基本信息 ---
    name: str
    kind: str  # 'rule', 'behavior', 'action', 'fov'
    description: str
    module_path: str  # 声明该 Logic 的文件路径, e.g., "assets.logics.movement"
    func_name: str    # 原始函数名, e.g., "find_path"

    tags: List[str] = field(default_factory=list)

    # --- 接口契约 ---
    # 约定参数 (如 behavior 的 agent, env 或 rule 的 env) 由代码在运行时注入，不在此声明。

    # 上下文注入参数: 声明函数是否需要注入一个特殊的上下文对象。
    # e.g., "society0.core_data.ExecutionContext"
    context_parameter_type: Optional[str] = None
    context_parameter_name: Optional[str] = None

    # 可变关键字参数 (JSON Schema 格式): 描述了函数签名中，除了约定参数和
    # 上下文注入参数之外的所有其他参数。这是暴露给 LLM 和 Schedule 编辑器的部分。
    parameters_schema: Dict[str, Any] = field(default_factory=dict)

    # 🔑 新增：**kwargs 参数支持检测（v3.1）
    # accepts_var_keyword: 函数是否接受 **kwargs 参数（bool）
    # extra_parameters: 显式声明的参数名列表（List[str]），不包括约定参数和 context
    # 用途：
    # 1. Schedule 在调用时判断是否需要打包未映射参数
    # 2. 文档生成时标注函数的参数接收能力
    # 3. LLM 提示时提供参数信息
    accepts_var_keyword: bool = False
    extra_parameters: List[str] = field(default_factory=list)

    # 返回值: 描述函数返回值的 JSON Schema，用于下游节点的 input_mapping。
    return_value_schema: Dict[str, Any] = field(default_factory=dict)

    # --- 依赖声明 ---
    target_env_type: Optional[str] = None
    target_agent_types: List[str] = field(default_factory=list)

    # --- 状态契约 (描述性，供参考) ---
    # 🔑 新增：描述 Logic 对 Agent state 的读写需求
    # 格式：{"reads": ["mood", "energy"], "writes": ["mood"]}
    # 用途：
    # 1. 文档生成（告诉开发者这个 Logic 会访问哪些字段）
    # 2. IDE 提示（编写 Logic 时提示可用字段）
    # 3. LLM 参考（帮助 LLM 理解 Logic 的作用）
    state_access_declaration: Optional[Dict[str, List[str]]] = None

    # 🔑 运行时函数引用（不参与序列化）
    _func_ref: Optional[Callable] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（排除 _func_ref）"""
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "tags": self.tags,
            "context_parameter_type": self.context_parameter_type,
            "context_parameter_name": self.context_parameter_name,
            "parameters_schema": self.parameters_schema,
            "accepts_var_keyword": self.accepts_var_keyword,
            "extra_parameters": self.extra_parameters,
            "return_value_schema": self.return_value_schema,
            "target_env_type": self.target_env_type,
            "target_agent_types": self.target_agent_types,
            "state_access_declaration": self.state_access_declaration,
            "module_path": self.module_path,
            "func_name": self.func_name,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], func_ref: Optional[Callable] = None) -> 'LogicMeta':
        """从字典反序列化（可选地附加函数引用）"""
        data_copy = dict(data)
        data_copy['_func_ref'] = func_ref
        return cls(**data_copy)


# =============================================================================
# Environment 元数据结构
# =============================================================================

@dataclass(frozen=True)
class CapabilityMeta:
    """描述一个由环境提供的内置能力 (Action/FoV/Rule/Behavior)

    我们将其统一抽象为"能力"，以便上层服务统一处理。
    """
    name: str
    kind: str  # 'action', 'fov', 'rule', 'behavior'
    description: str
    parameters_schema: Dict[str, Any]  # 复用 Logic 的参数 Schema 结构
    return_value_schema: Dict[str, Any]
    func_name: str  # 绑定的原始方法名

    tags: List[str] = field(default_factory=list)

    # 🔑 状态访问声明（同 LogicMeta）
    state_access_declaration: Optional[Dict[str, List[str]]] = None

    # 🔑 FoV 缓存策略：可按 step 或按 agent 缓存计算结果
    cache_on_step: bool = False
    cache_on_agent: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "parameters_schema": self.parameters_schema,
            "return_value_schema": self.return_value_schema,
            "tags": self.tags,
            "func_name": self.func_name,
            "state_access_declaration": self.state_access_declaration,
            "cache_on_step": self.cache_on_step,
            "cache_on_agent": self.cache_on_agent,
        }


@dataclass(frozen=True)
class EnvironmentMeta:
    """Environment 的完整元数据（v3.0 - 统一 State 容器版本）

    这是 Environment 的"说明书"，描述了它的一切。
    """
    # --- 基本信息 ---
    type_name: str
    display_name: str
    description: str
    class_path: str  # 用于动态实例化, e.g., "society0.env.grid_world.GridWorldEnv"

    # --- 接口契约 ---
    config_schema: Dict[str, Any]  # 实例化环境所需的配置
    state_schema: Dict[str, Any]   # 环境自身的运行时状态结构

    # 🔑 Agent 状态字段管理（v3.0 核心改进）
    # Env 声明它会为 Agent 添加哪些状态字段
    #
    # 字段格式（扩展 JSON Schema）：
    # {
    #   "type": "object",
    #   "properties": {
    #     "position": {
    #       "type": "object",
    #       "properties": {"x": "integer", "y": "integer"},
    #       "default": {"x": 0, "y": 0},
    #       "agent_visible": false,    # LLM 推理时是否可见
    #       "agent_editable": false,   # Agent 的 behavior/action 是否可修改
    #       "env_managed": true,       # 标记这是 Env 提供的字段
    #       "description": "Agent 在网格中的位置"
    #     }
    #   }
    # }
    #
    # 默认值（Env 提供的字段）：agent_visible=False, agent_editable=False
    agent_managed_fields_schema: Dict[str, Any] = field(default_factory=dict)
    builtin_state_fields: List[Dict[str, Any]] = field(default_factory=list)

    # --- 内置能力 ---
    capabilities: List[CapabilityMeta] = field(default_factory=list)

    # 🔑 运行时类引用（不参与序列化）
    _env_class: Optional[Type] = field(default=None, repr=False, compare=False)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（排除 _env_class）"""
        return {
            "type_name": self.type_name,
            "display_name": self.display_name,
            "description": self.description,
            "class_path": self.class_path,
            "config_schema": self.config_schema,
            "state_schema": self.state_schema,
            "agent_managed_fields_schema": self.agent_managed_fields_schema,
            "builtin_state_fields": self.builtin_state_fields,
            "capabilities": [cap.to_dict() for cap in self.capabilities],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any], env_class: Optional[Type] = None) -> 'EnvironmentMeta':
        """从字典反序列化（可选地附加类引用）"""
        data_copy = dict(data)

        # 反序列化 capabilities
        if 'capabilities' in data_copy:
            caps_data = data_copy.pop('capabilities')
            capabilities = [CapabilityMeta(**cap) for cap in caps_data]
            data_copy['capabilities'] = capabilities

        data_copy['_env_class'] = env_class
        return cls(**data_copy)


# =============================================================================
# AgentSet 元数据结构（可选，未来扩展）
# =============================================================================

@dataclass(frozen=True)
class AgentSetMeta:
    """AgentSet 的元数据（未来可能需要）

    当前 AgentSet 主要通过 JSON 定义，这里预留结构。
    """
    type_name: str
    display_name: str
    description: str
    archetype: str  # 'llm', 'rule', 'hybrid'

    # 🔑 Agent 自身的 state schema
    # 默认值（Agent 自己定义的字段）：agent_visible=True, agent_editable=True
    state_schema: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            "type_name": self.type_name,
            "display_name": self.display_name,
            "description": self.description,
            "archetype": self.archetype,
            "state_schema": self.state_schema,
        }


# =============================================================================
# 辅助函数
# =============================================================================

def enrich_state_schema_with_defaults(
    schema: Dict[str, Any],
    is_agent_owned: bool = True
) -> Dict[str, Any]:
    """
    为 state schema 的每个字段添加默认的访问控制元数据

    Args:
        schema: JSON Schema 格式的 state 定义
        is_agent_owned: True=Agent 自己定义的字段，False=Env 提供的字段

    Returns:
        增强后的 schema
    """
    if not schema or 'properties' not in schema:
        return schema

    # 确定默认值
    if is_agent_owned:
        # Agent 自己的字段：默认可见、可编辑
        default_visible = True
        default_editable = True
        default_env_managed = False
    else:
        # Env 提供的字段：默认不可见、不可编辑
        default_visible = False
        default_editable = False
        default_env_managed = True

    enriched_schema = {
        "type": schema.get("type", "object"),
        "properties": {}
    }

    for field_name, field_def in schema.get("properties", {}).items():
        enriched_field = dict(field_def)

        # 添加默认值（如果未声明）
        if "agent_visible" not in enriched_field:
            enriched_field["agent_visible"] = default_visible
        if "agent_editable" not in enriched_field:
            enriched_field["agent_editable"] = default_editable
        if "env_managed" not in enriched_field:
            enriched_field["env_managed"] = default_env_managed

        enriched_schema["properties"][field_name] = enriched_field

    return enriched_schema
