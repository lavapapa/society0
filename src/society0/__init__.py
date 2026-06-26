"""Society0: code-driven social simulation core."""
from __future__ import annotations

from importlib import import_module
from typing import Any, Dict, Tuple, TYPE_CHECKING

__version__ = "2.0.0"
__all__ = [
    "Society0",
    "LLMModel",
    "EmbedModel",
    "CodeSchedule",
    "StepContext",
    "StepResult",
    "AgentBatchResult",
    "CapabilityCatalog",
    "World",
    "Environment",
    "EnvironmentTickContext",
    "FunctionRegistry",
    "load_run_summary",
    "render_runtime_diagnostic_report",
]

# 延迟导入映射，避免在包初始化阶段触发重量级依赖
_LAZY_IMPORTS: Dict[str, Tuple[str, str]] = {
    "Society0": ("society0.society", "Society0"),
    "LLMModel": ("society0.models", "LLMModel"),
    "EmbedModel": ("society0.models", "EmbedModel"),
    "CodeSchedule": ("society0.schedule", "CodeSchedule"),
    "StepContext": ("society0.schedule", "StepContext"),
    "StepResult": ("society0.schedule", "StepResult"),
    "AgentBatchResult": ("society0.schedule", "AgentBatchResult"),
    "CapabilityCatalog": ("society0.schedule", "CapabilityCatalog"),
    "World": ("society0.core_data", "World"),
    "Environment": ("society0.environment", "Environment"),
    "EnvironmentTickContext": ("society0.environment", "EnvironmentTickContext"),
    "FunctionRegistry": ("society0.function_registry", "FunctionRegistry"),
    "load_run_summary": ("society0.diagnostics", "load_run_summary"),
    "render_runtime_diagnostic_report": ("society0.diagnostics", "render_runtime_diagnostic_report"),
}


def __getattr__(name: str) -> Any:
    """惰性加载关键类，降低初始化失败风险。"""
    if name in _LAZY_IMPORTS:
        module_name, attr_name = _LAZY_IMPORTS[name]
        module = import_module(module_name)
        value = getattr(module, attr_name)
        globals()[name] = value  # 缓存结果，避免重复导入
        return value
    raise AttributeError(f"module 'society0' has no attribute '{name}'")


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + __all__)


if TYPE_CHECKING:  # 类型检查阶段仍提供静态导入
    from .society import Society0  # noqa: F401
    from .models import LLMModel, EmbedModel  # noqa: F401
    from .schedule import AgentBatchResult, CapabilityCatalog, CodeSchedule, StepContext, StepResult  # noqa: F401
    from .core_data import World  # noqa: F401
    from .environment import Environment, EnvironmentTickContext  # noqa: F401
    from .function_registry import FunctionRegistry  # noqa: F401
    from .diagnostics import load_run_summary, render_runtime_diagnostic_report  # noqa: F401
