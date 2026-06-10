"""
NodeDiffDispatcher: 节点级状态增量调度器

负责将 Step/Node 执行过程中计算得到的差异数据，统一写入持久化目录并通过
StreamingBridge 推送给监听者。保持单一职责：调度器本身不参与 diff 计算，
而是接收外部构造好的结构化差异数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol
import threading


class DiffStreamingBridge(Protocol):
    """StreamingBridge 的最小接口声明，便于依赖注入。"""

    def publish_diff(self, *, step_id: int, node_id: str, changes: List[Dict[str, Any]], context_stack: Optional[List[Dict[str, Any]]] = None) -> None:
        ...


class DiffPersistence(Protocol):
    """PersistenceManager 的最小接口声明。"""

    def append_node_diff(
        self,
        *,
        step_id: int,
        node_id: str,
        changes: List[Dict[str, Any]],
        context_stack: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        ...


@dataclass
class NodeDiffPayload:
    """节点差异数据载体。"""

    step_id: int
    node_id: str
    changes: List[Dict[str, Any]]
    context_stack: Optional[List[Dict[str, Any]]]


class NodeDiffDispatcher:
    """
    节点差异调度器。

    通过构造函数注入 StreamingBridge 与 PersistenceManager，确保依赖集中在组合根，
    并提供线程安全的 `dispatch` 方法。
    """

    def __init__(
        self,
        *,
        bridge: Optional[DiffStreamingBridge],
        persistence: Optional[DiffPersistence],
    ) -> None:
        self._bridge = bridge
        self._persistence = persistence
        self._lock = threading.Lock()

    def dispatch(self, payload: NodeDiffPayload) -> None:
        """持久化并广播节点差异。"""
        if not payload.changes:
            return

        with self._lock:
            if self._persistence:
                self._persistence.append_node_diff(
                    step_id=payload.step_id,
                    node_id=payload.node_id,
                    changes=payload.changes,
                    context_stack=payload.context_stack,
                )

            if self._bridge:
                self._bridge.publish_diff(
                    step_id=payload.step_id,
                    node_id=payload.node_id,
                    changes=payload.changes,
                    context_stack=payload.context_stack,
                )
