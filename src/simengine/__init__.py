"""
SimEngine V2: Clean, principle-driven simulation engine.

核心理念：
- 单一事实来源（不可变 WorldState）
- 数据与逻辑分离（数据容器与无状态函数）
- 集中调度控制（Schedule 驱动执行）
- 依赖反转（通过接口注入协作者）
- 简单优先（单一职责）
"""
from __future__ import annotations

from importlib import import_module
from typing import Any, Dict, Tuple, TYPE_CHECKING

__version__ = "2.0.0"
__all__ = [
    "SimEngine",
    "World",
    "Environment",
    "FunctionRegistry",
    "Schedule",
]

# 延迟导入映射，避免在包初始化阶段触发重量级依赖
_LAZY_IMPORTS: Dict[str, Tuple[str, str]] = {
    "SimEngine": ("simengine.sim_engine", "SimEngine"),
    "World": ("simengine.core_data", "World"),
    "Environment": ("simengine.environment", "Environment"),
    "FunctionRegistry": ("simengine.function_registry", "FunctionRegistry"),
    "Schedule": ("simengine.schedule", "Schedule"),
}


def __getattr__(name: str) -> Any:
    """惰性加载关键类，降低初始化失败风险。"""
    if name in _LAZY_IMPORTS:
        module_name, attr_name = _LAZY_IMPORTS[name]
        module = import_module(module_name)
        value = getattr(module, attr_name)
        globals()[name] = value  # 缓存结果，避免重复导入
        return value
    raise AttributeError(f"module 'simengine' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)


if TYPE_CHECKING:  # 类型检查阶段仍提供静态导入
    from .sim_engine import SimEngine  # noqa: F401
    from .core_data import World  # noqa: F401
    from .environment import Environment  # noqa: F401
    from .function_registry import FunctionRegistry  # noqa: F401
    from .schedule import Schedule  # noqa: F401
