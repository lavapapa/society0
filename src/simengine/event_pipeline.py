"""
事件管线工具：提供事件批次监听与索引写入能力

这个模块包含统一的事件批次模型、监听器接口以及若干默认监听器实现，
用于在不侵入核心仿真流程的情况下，为事件系统扩展持久化与实时能力。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Protocol, Sequence
import json
import re
import threading

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


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _extract_step_number(step_id: Optional[str], events: Sequence[BaseEvent]) -> Optional[int]:
    """
    尝试解析步骤编号，优先使用显式 step_id，其次回退到事件上下文栈。
    """
    if step_id is not None:
        parsed = _parse_numeric_suffix(step_id)
        if parsed is not None:
            return parsed
    for event in events:
        context_step = event.get_current_step()
        if context_step:
            parsed = _parse_numeric_suffix(context_step)
            if parsed is not None:
                return parsed
    return None


def _parse_numeric_suffix(identifier: str) -> Optional[int]:
    match = re.search(r"(\d+)$", identifier)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


class JsonlIndexWriter:
    """
    事件索引写入器

    将批次信息写入 JSONL 文件，便于快速定位 step/node 对应的事件范围。
    """

    def __init__(self, index_path: Path):
        self.index_path = Path(index_path)
        _ensure_parent(self.index_path)
        self._lock = threading.Lock()

    def handle(self, batch: EventBatch) -> None:
        if batch.event_count == 0:
            return

        record = {
            "step_id": batch.step_id,
            "node_id": batch.node_id,
            "log_file": batch.log_file_path,
            "offset": batch.start_offset,
            "length": batch.end_offset - batch.start_offset,
            "event_count": batch.event_count,
            "event_offsets": list(batch.event_offsets),
            "event_types": [event.event_type for event in batch.events],
        }

        with self._lock, self.index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


class ReplayLogWriter:
    """
    事件重放写入器

    为每个 step 维护一份 JSONL 日志，供 PersistenceManager 重放增量变更。
    """

    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def handle(self, batch: EventBatch) -> None:
        if batch.event_count == 0:
            return

        step_number = _extract_step_number(batch.step_id, batch.events)
        if step_number is None:
            return

        file_path = self.base_dir / f"events_from_step_{step_number:06d}.jsonl"
        record_stream: Iterable[dict] = self._build_records(batch)

        with self._lock, file_path.open("a", encoding="utf-8") as fh:
            for record in record_stream:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def _build_records(self, batch: EventBatch) -> Iterable[dict]:
        for event, offset in zip(batch.events, batch.event_offsets):
            record = event.to_dict()
            if batch.step_id is not None and "step_id" not in record:
                record["step_id"] = batch.step_id
            if batch.node_id is not None and "node_id" not in record:
                record["node_id"] = batch.node_id
            record["_log_file"] = batch.log_file_path
            record["_log_offset"] = offset
            yield record
