"""Society0: code-driven social simulation core."""
from __future__ import annotations

from importlib import import_module
from typing import Any, Dict, Tuple, TYPE_CHECKING

__version__ = "4.0.1"
__all__ = [
    "Society0",
    "LLMModel",
    "EmbedModel",
    "CodeSchedule",
    "StepContext",
    "StepResult",
    "AgentBatchResult",
    "AgentGroup",
    "CapabilityCatalog",
    "ActivationBatch",
    "ActivationPool",
    "ActivationPoolError",
    "ActivationLimitError",
    "ActivationResult",
    "ActivationSignal",
    "ActivationSubmission",
    "ActivationPoolSession",
    "World",
    "Environment",
    "EnvironmentTickContext",
    "StepRuntimeScope",
    "StepFailure",
    "FunctionRegistry",
    "load_run_summary",
    "render_runtime_diagnostic_report",
    "persistent_state_schema",
    "replaceable",
    "replaceable_map",
    "append_only_map",
    "append_only_list",
    "transient",
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
    "AgentGroup": ("society0.schedule", "AgentGroup"),
    "CapabilityCatalog": ("society0.schedule", "CapabilityCatalog"),
    "ActivationBatch": ("society0.activation_pool", "ActivationBatch"),
    "ActivationPool": ("society0.activation_pool", "ActivationPool"),
    "ActivationPoolError": ("society0.activation_pool", "ActivationPoolError"),
    "ActivationLimitError": ("society0.activation_pool", "ActivationLimitError"),
    "ActivationResult": ("society0.activation_pool", "ActivationResult"),
    "ActivationSignal": ("society0.activation_pool", "ActivationSignal"),
    "ActivationSubmission": ("society0.activation_pool", "ActivationSubmission"),
    "ActivationPoolSession": ("society0.activation_pool", "ActivationPoolSession"),
    "World": ("society0.core_data", "World"),
    "Environment": ("society0.environment", "Environment"),
    "EnvironmentTickContext": ("society0.environment", "EnvironmentTickContext"),
    "StepRuntimeScope": ("society0.runtime_scope", "StepRuntimeScope"),
    "StepFailure": ("society0.recovery", "StepFailure"),
    "FunctionRegistry": ("society0.function_registry", "FunctionRegistry"),
    "load_run_summary": ("society0.diagnostics", "load_run_summary"),
    "render_runtime_diagnostic_report": ("society0.diagnostics", "render_runtime_diagnostic_report"),
    "persistent_state_schema": ("society0.state_persistence", "persistent_state_schema"),
    "replaceable": ("society0.state_persistence", "replaceable"),
    "replaceable_map": ("society0.state_persistence", "replaceable_map"),
    "append_only_map": ("society0.state_persistence", "append_only_map"),
    "append_only_list": ("society0.state_persistence", "append_only_list"),
    "transient": ("society0.state_persistence", "transient"),
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
    from .schedule import AgentBatchResult, AgentGroup, CapabilityCatalog, CodeSchedule, StepContext, StepResult  # noqa: F401
    from .activation_pool import (  # noqa: F401
        ActivationBatch,
        ActivationPool,
        ActivationPoolError,
        ActivationLimitError,
        ActivationPoolSession,
        ActivationResult,
        ActivationSignal,
        ActivationSubmission,
    )
    from .core_data import World  # noqa: F401
    from .environment import Environment, EnvironmentTickContext  # noqa: F401
    from .runtime_scope import StepRuntimeScope  # noqa: F401
    from .recovery import StepFailure  # noqa: F401
    from .function_registry import FunctionRegistry  # noqa: F401
    from .diagnostics import load_run_summary, render_runtime_diagnostic_report  # noqa: F401
