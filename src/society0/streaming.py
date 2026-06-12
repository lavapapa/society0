"""
StreamingBridge: 事件流实时推送组件

该模块实现了一个 Unix Socket 服务端，用于将仿真事件、进度和状态信息实时推送给
外部观察者（例如前端调试工具）。设计遵循以下原则：

- 作为 EventBatchListener 挂载到 EventLogger，始终被动接收事件
- 仅负责转发数据，不再写入事件日志，避免重复 I/O
- 所有复杂依赖（EventLogger、结果目录）通过构造函数注入
- 线程安全地管理客户端集合与消息队列，任何异常都不会影响仿真主流程
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence, Set
import json
import logging
import queue
import socket
import tempfile
import threading
import uuid

from .event_pipeline import EventBatch, EventBatchListener
from .events import BaseEvent

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StreamingMessage:
    """内部消息模型，统一封装发送到客户端的数据。"""

    message_type: str
    payload: dict

    def to_bytes(self) -> bytes:
        data = {
            "type": self.message_type,
            "payload": self.payload,
            "published_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")


class StreamingBridge(EventBatchListener):
    """实时流式通信桥接组件。"""

    def __init__(
        self,
        results_dir: Path,
        *,
        socket_name: str = "control.sock",
        queue_size: int = 1024,
    ):
        """
        Args:
            results_dir: 实验结果根目录
            socket_name: Unix Socket 文件名
            queue_size: 内部消息队列容量
        """
        self.results_dir = Path(results_dir)
        self._requested_sock_path = self.results_dir / socket_name
        self.sock_path = self._requested_sock_path
        self.status_path = self.results_dir / "status.json"
        self.progress_path = self.results_dir / "progress.json"

        self._running = False
        self._server_socket: Optional[socket.socket] = None
        self._server_thread: Optional[threading.Thread] = None
        self._dispatcher_thread: Optional[threading.Thread] = None

        self._clients: Set[socket.socket] = set()
        self._clients_lock = threading.Lock()
        self._file_lock = threading.Lock()

        self._event_queue: queue.Queue[StreamingMessage] = queue.Queue(maxsize=queue_size)

        self._initialize_files()

    # --------------------------------------------------------------------- #
    # 生命周期管理
    # --------------------------------------------------------------------- #
    def start(self) -> None:
        """启动 StreamingBridge，开启 socket 服务与派发线程。"""
        if self._running:
            logger.debug("StreamingBridge already running")
            return

        self.results_dir.mkdir(parents=True, exist_ok=True)

        # 清理残留的 socket 文件
        try:
            if self.sock_path.exists():
                self.sock_path.unlink()
        except FileNotFoundError:
            pass

        bind_path = self._prepare_socket_path()
        self._server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket.bind(str(bind_path))
        self._server_socket.listen()
        self._server_socket.settimeout(1.0)

        self._running = True
        self._server_thread = threading.Thread(
            target=self._socket_server_loop,
            name="StreamingBridge-Server",
            daemon=True,
        )
        self._dispatcher_thread = threading.Thread(
            target=self._dispatcher_loop,
            name="StreamingBridge-Dispatcher",
            daemon=True,
        )

        self._server_thread.start()
        self._dispatcher_thread.start()
        self.update_status(socket_available=True, connection_state="live")

        logger.info("StreamingBridge started at %s", self.sock_path)

    def stop(self, *, final_status: Optional[str] = None) -> None:
        """停止 StreamingBridge，关闭所有资源，并记录最终状态。

        Args:
            final_status: 实验的最终状态（例如 "completed", "failed"），
                         如果提供，将在关闭前记录到 status.json
        """
        if not self._running:
            return

        self._running = False

        # 唤醒队列，确保 dispatcher 能尽快退出
        try:
            self._event_queue.put_nowait(StreamingMessage("bridge_shutdown", {}))
        except queue.Full:
            pass

        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=5.0)
        if self._dispatcher_thread and self._dispatcher_thread.is_alive():
            self._dispatcher_thread.join(timeout=5.0)

        with self._clients_lock:
            for client in list(self._clients):
                try:
                    client.close()
                finally:
                    self._clients.discard(client)

        if self._server_socket:
            try:
                self._server_socket.close()
            finally:
                self._server_socket = None

        # 【核心修复逻辑】
        # 使用传入的最终状态（如果提供的话）来更新 status.json
        # 这确保实验的最终状态能够被持久化记录
        self.update_status(
            status=final_status,
            socket_available=False,
            connection_state="disconnected"
        )

        try:
            if self.sock_path.exists():
                self.sock_path.unlink()
        except FileNotFoundError:
            pass
        finally:
            self.sock_path = self._requested_sock_path

        logger.info(f"StreamingBridge stopped with final status: {final_status}")

    # --------------------------------------------------------------------- #
    # EventBatchListener 接口
    # --------------------------------------------------------------------- #
    def handle(self, batch: EventBatch) -> None:
        """接收事件批次，将其中每个事件推送到客户端。"""
        if batch.event_count == 0:
            return

        for event, offset in zip(batch.events, batch.event_offsets):
            self.publish_event(
                event,
                step_id=batch.step_id,
                node_id=batch.node_id,
                log_file=batch.log_file_path,
                log_offset=offset,
            )

    # --------------------------------------------------------------------- #
    # 外部接口
    # --------------------------------------------------------------------- #
    def publish_event(
        self,
        event: BaseEvent,
        *,
        step_id: Optional[str] = None,
        node_id: Optional[str] = None,
        log_file: Optional[str] = None,
        log_offset: Optional[int] = None,
    ) -> None:
        """将单个事件推送到队列，等待派发线程广播。"""
        message = self._build_event_message(
            event,
            step_id=step_id,
            node_id=node_id,
            log_file=log_file,
            log_offset=log_offset,
        )
        self._enqueue(message)

    def publish_snapshot(
        self,
        *,
        step_id: int,
        snapshot: dict,
        checkpoint_path: Optional[str] = None,
        diff_log_file: Optional[str] = None,
        diff_log_exists: Optional[bool] = None,
        metrics: Optional[dict] = None,
    ) -> None:
        """
        将最新快照广播给所有监听者。

        Args:
            step_id: 使用 checkpoint 的整数 step 编号
            snapshot: 快照数据内容
            checkpoint_path: 快照文件路径（可选，方便调试与回溯）
            diff_log_file: 对应 diff 日志文件路径（可选）
            diff_log_exists: diff 日志是否已存在（可选，默认按文件是否存在推断）
            metrics: 步骤指标数据（可选）
        """
        # 组合一个可识别的 payload
        payload = {
            "step_number": step_id,
            "step_label": f"step_{step_id:06d}",
            "snapshot": snapshot,
        }
        if checkpoint_path is not None:
            payload["checkpoint_path"] = checkpoint_path
        if diff_log_file is not None:
            payload["diff_log_file"] = diff_log_file
        if diff_log_exists is not None:
            payload["diff_log_exists"] = diff_log_exists
        if metrics is not None:
            payload["metrics"] = metrics
            if isinstance(metrics, dict):
                analysis_context = metrics.get("jmespath_root")
                if analysis_context is not None:
                    payload["analysis"] = analysis_context

        self._enqueue(StreamingMessage("snapshot", payload))

    def publish_diff(
        self,
        *,
        step_id: int,
        node_id: str,
        changes: Sequence[dict],
        context_stack: Optional[Sequence[dict]] = None,
    ) -> None:
        """广播节点级差异数据。"""
        payload = {
            "step_number": step_id,
            "node_id": node_id,
            "changes": list(changes),
        }
        if context_stack is not None:
            payload["context_stack"] = list(context_stack)

        self._enqueue(StreamingMessage("diff", payload))

    def update_progress(
        self,
        current_step: int,
        total_steps: int,
        *,
        average_step_duration_ms: Optional[float] = None,
        estimated_remaining_seconds: Optional[float] = None,
    ) -> None:
        """更新进度快照并广播进度消息。"""
        if total_steps <= 0:
            progress_percent = 0.0
        else:
            progress_percent = (current_step / total_steps) * 100

        timestamp = self._utc_now()
        progress_payload = {
            "current_step": current_step,
            "total_steps": total_steps,
            "progress_percent": progress_percent,
            "last_update": timestamp,
            "last_update_timestamp": timestamp,
        }

        if average_step_duration_ms is not None:
            progress_payload["average_step_duration_ms"] = average_step_duration_ms
        if estimated_remaining_seconds is not None:
            progress_payload["estimated_remaining_seconds"] = estimated_remaining_seconds

        with self._file_lock:
            self.progress_path.write_text(json.dumps(progress_payload, indent=2, ensure_ascii=False))

        self._enqueue(StreamingMessage("progress", progress_payload))

    def update_status(
        self,
        *,
        status: Optional[str] = None,
        socket_available: Optional[bool] = None,
        error: Optional[dict] = None,
        connection_state: Optional[str] = None,
    ) -> None:
        """更新状态快照文件，必要时推送状态消息。"""
        with self._file_lock:
            if self.status_path.exists():
                try:
                    current_status = json.loads(self.status_path.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    current_status = self._default_status()
            else:
                current_status = self._default_status()

            lifecycle = current_status.setdefault("lifecycle", {})

            if status is not None:
                current_status["status"] = status
                lifecycle_key = self._status_to_lifecycle_key(status)
                if lifecycle_key and lifecycle_key not in lifecycle:
                    lifecycle[lifecycle_key] = self._utc_now()

            if socket_available is not None:
                current_status["socket_available"] = socket_available

            if error is not None:
                current_status["error"] = error

            if connection_state is not None:
                current_status["connection_state"] = connection_state

            current_status["socket_path"] = str(self.sock_path)
            current_status["requested_socket_path"] = str(self._requested_sock_path)

            self.status_path.write_text(json.dumps(current_status, indent=2, ensure_ascii=False))

        if (
            status is not None
            or socket_available is not None
            or error is not None
            or connection_state is not None
        ):
            self._enqueue(StreamingMessage("status", current_status))

    # --------------------------------------------------------------------- #
    # 内部实现
    # --------------------------------------------------------------------- #
    def _initialize_files(self) -> None:
        """创建初始状态文件，确保消费者有默认值。"""
        self.results_dir.mkdir(parents=True, exist_ok=True)

        with self._file_lock:
            if not self.status_path.exists():
                self.status_path.write_text(
                    json.dumps(self._default_status(), indent=2, ensure_ascii=False)
                )
            if not self.progress_path.exists():
                self.progress_path.write_text(
                    json.dumps(self._default_progress(), indent=2, ensure_ascii=False)
                )

    def _socket_server_loop(self) -> None:
        """持续接受客户端连接。"""
        assert self._server_socket is not None

        while self._running:
            try:
                conn, _ = self._server_socket.accept()
            except socket.timeout:
                continue
            except OSError as exc:
                if self._running:
                    logger.warning("StreamingBridge socket accept failed: %s", exc)
                break

            conn.setblocking(False)
            with self._clients_lock:
                self._clients.add(conn)
            logger.info("StreamingBridge client connected (total=%d)", self._client_count())

    def _dispatcher_loop(self) -> None:
        """从队列中取出消息并广播给所有客户端。"""
        while self._running:
            try:
                message = self._event_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            if not self._running:
                break

            payload = message.to_bytes()
            self._broadcast_to_clients(payload)

    def _broadcast_to_clients(self, payload: bytes) -> None:
        """将消息广播给所有活跃客户端。"""
        with self._clients_lock:
            clients_snapshot = list(self._clients)

        for client in clients_snapshot:
            try:
                client.sendall(payload)
            except (BrokenPipeError, ConnectionResetError, OSError):
                logger.debug("StreamingBridge client disconnected")
                with self._clients_lock:
                    self._clients.discard(client)
                try:
                    client.close()
                except OSError:
                    pass

    def _enqueue(self, message: StreamingMessage) -> None:
        """将消息放入队列，如队列已满则丢弃最旧消息。"""
        if not self._running:
            # 组件未启动时，避免阻塞；仅更新文件即可
            return

        try:
            self._event_queue.put_nowait(message)
        except queue.Full:
            try:
                # 丢弃最旧消息，优先保证最新数据
                self._event_queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self._event_queue.put_nowait(message)
            except queue.Full:
                logger.warning("StreamingBridge queue full, dropping message")

    def _build_event_message(
        self,
        event: BaseEvent,
        *,
        step_id: Optional[str],
        node_id: Optional[str],
        log_file: Optional[str],
        log_offset: Optional[int],
    ) -> StreamingMessage:
        event_dict = event.to_dict()

        payload = {
            "event": event_dict,
            "step_id": step_id or event.get_current_step(),
            "node_id": node_id or event.get_current_node(),
        }

        if log_file is not None:
            payload["log_file"] = log_file
        if log_offset is not None:
            payload["log_offset"] = log_offset

        return StreamingMessage("event", payload)

    def _client_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _default_status() -> dict:
        return {
            "status": "pending",
            "lifecycle": {"created_at": StreamingBridge._utc_now()},
            "socket_available": False,
            "error": None,
            "connection_state": "disconnected",
        }

    @staticmethod
    def _default_progress() -> dict:
        timestamp = StreamingBridge._utc_now()
        return {
            "current_step": 0,
            "total_steps": 0,
            "progress_percent": 0.0,
            "last_update": timestamp,
            "last_update_timestamp": timestamp,
        }

    @staticmethod
    def _status_to_lifecycle_key(status: str) -> Optional[str]:
        mapping = {
            "running": "started_at",
            "paused": "paused_at",
            "completed": "completed_at",
            "failed": "failed_at",
        }
        return mapping.get(status)

    def _prepare_socket_path(self) -> Path:
        """根据路径长度准备实际绑定的 Unix Socket 路径。"""
        desired = self._requested_sock_path
        path_bytes = str(desired).encode("utf-8")
        max_length = self._max_unix_socket_path()

        def _cleanup_target(target: Path) -> None:
            try:
                if target.exists() or target.is_symlink():
                    target.unlink()
            except FileNotFoundError:
                pass

        if len(path_bytes) <= max_length:
            _cleanup_target(desired)
            self.sock_path = desired
            return desired

        fallback_dir = Path(tempfile.gettempdir())
        fallback_path = fallback_dir / f"simbridge_{uuid.uuid4().hex}.sock"
        _cleanup_target(fallback_path)
        _cleanup_target(desired)

        self.sock_path = fallback_path
        return fallback_path

    @staticmethod
    def _max_unix_socket_path() -> int:
        """简单估算 Unix Socket 路径长度上限。"""
        # Linux 为 108，macOS 为 104，这里取更保守的 100
        return 100
