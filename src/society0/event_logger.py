"""
SimEngine V2: EventLogger - Event tracking and auditing component (已停用)。

旧版事件日志组件已被事务系统与新的 ExperimentLogContext 取代。
此文件保留仅为兼容性占位，防止误用。
"""

from __future__ import annotations

from typing import Any


class Event:  # pragma: no cover
    """占位类型，防止旧引用导致 AttributeError。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "society0.event_logger.Event 已停用，请迁移到 society0.events 中的事件模型。"
        )


class EventLogger:  # pragma: no cover
    """占位类，旧版事件日志记录器已停用。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise RuntimeError(
            "society0.event_logger.EventLogger 已停用，请使用 society0.transaction.EventLogger "
            "或 society0.logging.ExperimentLogContext。"
        )
