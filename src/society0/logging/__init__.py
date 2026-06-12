"""
SimEngine 日志系统基础设施。

该包包含用于实验运行期间结构化日志的上下文、记录器与扩展接口。
"""

from .context import ExperimentLogContext, LogHook, StructuredLogger
from .spec import (
    AGENT_CHANNEL_PREFIX,
    AGENT_EVENT_SPECS,
    AgentEvent,
    ENVIRONMENT_EVENT_SPECS,
    EnvironmentEvent,
    EventSpecification,
    LogChannel,
    LogField,
    RESOURCE_EVENT_SPECS,
    ResourceEvent,
    RUNTIME_EVENT_SPECS,
    RuntimeEvent,
    SCHEDULE_EVENT_SPECS,
    ScheduleEvent,
    SYSTEM_EVENT_SPECS,
    SystemEvent,
    get_event_spec,
)

__all__ = [
    "ExperimentLogContext",
    "StructuredLogger",
    "LogHook",
    "LogChannel",
    "LogField",
    "RuntimeEvent",
    "ScheduleEvent",
    "AgentEvent",
    "EnvironmentEvent",
    "ResourceEvent",
    "SystemEvent",
    "EventSpecification",
    "RUNTIME_EVENT_SPECS",
    "SCHEDULE_EVENT_SPECS",
    "AGENT_EVENT_SPECS",
    "ENVIRONMENT_EVENT_SPECS",
    "RESOURCE_EVENT_SPECS",
    "SYSTEM_EVENT_SPECS",
    "AGENT_CHANNEL_PREFIX",
    "get_event_spec",
]

from .utils import summarize_text, sample_items

__all__ += ["summarize_text", "sample_items"]
