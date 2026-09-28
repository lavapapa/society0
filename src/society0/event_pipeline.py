"""
事务日志的事件批次与监听接口。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence

from .events import BaseEvent


@dataclass(frozen=True)
class EventBatch:
    """事件批次数据结构，用于监听器之间传递上下文信息。"""

    log_file_path: str
    events: Sequence[BaseEvent]
    step_id: Optional[str]
    node_id: Optional[str]
    start_offset: int
    end_offset: int
    event_offsets: Sequence[int]

    @property
    def event_count(self) -> int:
        return len(self.events)


class EventBatchListener(Protocol):
    """事件批次监听器接口，监听器实现必须保持无副作用输入。"""

    def handle(self, batch: EventBatch) -> None:
        ...
