"""
实验日志上下文与结构化写入器。

主要职责：
1. 为不同功能域提供独立的 JSONL 写入器（runtime/schedule/env 等）
2. 支持 Agent 级日志的懒加载与 LRU 关闭策略，降低文件句柄占用
3. 统一记录字段规范，便于前后端与审计系统消费
4. 引入 Hook 扩展点，可用于转发日志到额外的处理管道
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Dict, Iterable, Optional, Protocol


class LogHook(Protocol):
    """日志 Hook 接口，允许在记录写入后执行额外逻辑。"""

    def __call__(self, channel: str, record: Dict[str, Any]) -> None:
        ...


def _ensure_directory(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    """返回 UTC 时间戳（ISO8601）。"""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class LoggerConfig:
    """结构化写入器的配置项。"""

    channel: str
    file_path: Path


class StructuredLogger:
    """
    针对单个 JSONL 文件的结构化写入器。

    - 线程安全
    - 行缓冲写入，确保实时可读
    - 写入后可通知 Hook
    """

    def __init__(
        self,
        config: LoggerConfig,
        *,
        notify: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ):
        self._config = config
        self._lock = Lock()
        self._file = None
        self._notify = notify
        _ensure_directory(self._config.file_path)

    @property
    def channel(self) -> str:
        return self._config.channel

    @property
    def file_path(self) -> Path:
        return self._config.file_path

    def log(self, level: str, event: str, **payload: Any) -> Dict[str, Any]:
        """写入结构化日志，返回最终记录内容。"""
        record = {
            "timestamp": _now_iso(),
            "level": level.upper(),
            "event": event,
            **payload,
        }
        self._write_record(record)
        return record

    def _write_record(self, record: Dict[str, Any]) -> None:
        with self._lock:
            if self._file is None or self._file.closed:
                self._file = open(
                    self._config.file_path,
                    "a",
                    encoding="utf-8",
                    buffering=1,  # 行缓冲
                )
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._file.flush()

        if self._notify is not None:
            try:
                self._notify(self.channel, record)
            except Exception:
                # Hook 失败不影响主流程
                pass

    def close(self) -> None:
        with self._lock:
            if self._file and not self._file.closed:
                self._file.close()


class _AgentLoggerCache:
    """Agent 日志懒加载与 LRU 管理。"""

    def __init__(
        self,
        base_dir: Path,
        *,
        max_open_files: int,
        notify: Optional[Callable[[str, Dict[str, Any]], None]],
    ):
        self._base_dir = base_dir
        self._max_open_files = max_open_files
        self._notify = notify
        self._cache: OrderedDict[str, StructuredLogger] = OrderedDict()

    def get(self, agent_id: str) -> StructuredLogger:
        agent_id = str(agent_id)
        logger = self._cache.get(agent_id)
        if logger is not None:
            self._cache.move_to_end(agent_id)
            return logger

        if len(self._cache) >= self._max_open_files:
            _, stale_logger = self._cache.popitem(last=False)
            stale_logger.close()

        file_path = self._base_dir / f"{agent_id}.jsonl"
        config = LoggerConfig(channel=f"agents/{agent_id}", file_path=file_path)
        logger = StructuredLogger(config, notify=self._notify)
        self._cache[agent_id] = logger
        return logger

    def clear(self) -> None:
        while self._cache:
            _, logger = self._cache.popitem(last=False)
            logger.close()


class ExperimentLogContext:
    """
    实验级日志上下文，集中管理所有日志写入器。

    负责：
    - 创建并持有各功能域的 StructuredLogger
    - 统一附加实验元数据字段
    - 提供 Hook 注册能力
    - 对 Agent 日志进行懒加载与 LRU 管理
    """

    DEFAULT_AGENT_CACHE_SIZE = 32

    def __init__(
        self,
        logs_dir: Path,
        *,
        experiment_id: Optional[str] = None,
        run_id: Optional[str] = None,
        hooks: Optional[Iterable[LogHook]] = None,
        agent_cache_size: Optional[int] = None,
    ):
        self._logs_dir = Path(logs_dir)
        self._logs_dir.mkdir(parents=True, exist_ok=True)

        self._experiment_id = experiment_id or self._infer_experiment_id(self._logs_dir)
        self._run_id = run_id
        self._hooks = list(hooks or [])
        self._agent_cache = _AgentLoggerCache(
            self._logs_dir / "agents",
            max_open_files=agent_cache_size or self.DEFAULT_AGENT_CACHE_SIZE,
            notify=self._notify_hooks,
        )

        self.runtime = self._build_logger("runtime", "runtime.jsonl")
        self.schedule = self._build_logger("schedule", "schedule.jsonl")
        self.env = self._build_logger("env", "env.jsonl")
        self.system = self._build_logger("system", "system.jsonl")
        self.resources = {
            "llm": self._build_logger("resources/llm", "resources/llm.jsonl"),
            "embedding": self._build_logger("resources/embedding", "resources/embedding.jsonl"),
        }
        # 统一的资源调用追踪日志（与 log.txt 同级，便于快速排查性能瓶颈）
        self.resource_calls = StructuredLogger(
            LoggerConfig(
                channel="resource_calls",
                file_path=self._logs_dir.parent / "resource_calls.jsonl",
            ),
            notify=self._notify_hooks,
        )
        self._resource_call_lock = Lock()
        self._resource_call_starts: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _infer_experiment_id(logs_dir: Path) -> Optional[str]:
        try:
            return logs_dir.resolve().parent.parent.name
        except Exception:
            return None

    @property
    def experiment_id(self) -> Optional[str]:
        return self._experiment_id

    @property
    def run_id(self) -> Optional[str]:
        return self._run_id

    def attach_hook(self, hook: LogHook) -> None:
        self._hooks.append(hook)

    def get_agent_logger(self, agent_id: str) -> StructuredLogger:
        return self._agent_cache.get(agent_id)

    def log_runtime(self, level: str, event: str, **payload: Any) -> Dict[str, Any]:
        return self._log_with(self.runtime, level, event, payload)

    def log_schedule(self, level: str, event: str, **payload: Any) -> Dict[str, Any]:
        return self._log_with(self.schedule, level, event, payload)

    def log_env(self, level: str, event: str, **payload: Any) -> Dict[str, Any]:
        return self._log_with(self.env, level, event, payload)

    def log_system(self, level: str, event: str, **payload: Any) -> Dict[str, Any]:
        return self._log_with(self.system, level, event, payload)

    def log_resource(self, resource_type: str, level: str, event: str, **payload: Any) -> Dict[str, Any]:
        logger = self.resources.get(resource_type)
        if logger is None:
            raise ValueError(f"Unsupported resource logger: {resource_type}")
        record = self._log_with(logger, level, event, payload)
        self._log_resource_call_trace(resource_type, record)
        return record

    def log_agent(self, agent_id: str, level: str, event: str, **payload: Any) -> Dict[str, Any]:
        logger = self.get_agent_logger(agent_id)
        payload.setdefault("agent_id", agent_id)
        return self._log_with(logger, level, event, payload)

    def _log_with(
        self,
        logger: StructuredLogger,
        level: str,
        event: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        enriched = self._enrich_payload(payload)
        return logger.log(level, event, **enriched)

    def _enrich_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(payload)
        if self._experiment_id and "experiment_id" not in result:
            result["experiment_id"] = self._experiment_id
        if self._run_id and "run_id" not in result:
            result["run_id"] = self._run_id
        return result

    def _build_logger(self, channel: str, relative_path: str) -> StructuredLogger:
        file_path = self._logs_dir / relative_path
        return StructuredLogger(
            LoggerConfig(channel=channel, file_path=file_path),
            notify=self._notify_hooks,
        )

    def _log_resource_call_trace(self, resource_type: str, record: Dict[str, Any]) -> None:
        event = str(record.get("event") or "")
        request_id = record.get("request_id")
        if not request_id:
            return

        started_events = {"llm_request_started", "embedding_request_started"}
        completed_events = {"llm_request_completed", "embedding_request_completed"}
        failed_events = {"llm_request_failed", "embedding_request_failed"}
        terminal_events = completed_events | failed_events

        if event in started_events:
            start_payload = {
                "resource_type": resource_type,
                "request_id": request_id,
                "status": "started",
                "started_at": record.get("timestamp"),
                "endpoint_id": record.get("endpoint_id"),
                "model": record.get("model"),
                "agent_id": record.get("agent_id"),
                "agent_ids": record.get("agent_ids"),
                "post_id": record.get("post_id"),
                "post_ids": record.get("post_ids"),
                "step_names": record.get("step_names"),
                "interaction_types": record.get("interaction_types"),
                "interaction_names": record.get("interaction_names"),
                "messages_count": record.get("messages_count"),
                "tools_count": record.get("tools_count"),
                "tools_characters": record.get("tools_characters"),
                "payload_characters": record.get("payload_characters"),
                "max_tokens": record.get("max_tokens"),
                "temperature": record.get("temperature"),
                "top_p": record.get("top_p"),
                "texts_count": record.get("texts_count"),
                "dimensions": record.get("dimensions"),
                "input_characters": record.get("input_characters"),
                "cache_hit": record.get("cache_hit"),
                "step": record.get("step"),
                "step_name": record.get("step_name"),
                "interaction_type": record.get("interaction_type"),
                "interaction_name": record.get("interaction_name"),
            }
            with self._resource_call_lock:
                self._resource_call_starts[request_id] = start_payload
            compact_start_payload = {k: v for k, v in start_payload.items() if v is not None}
            self.resource_calls.log("INFO", "resource_call_trace", **compact_start_payload)
            return

        if event not in terminal_events:
            return

        with self._resource_call_lock:
            started = self._resource_call_starts.pop(request_id, None)

        status = "success" if event in completed_events else "failed"
        error_value = record.get("error")
        if error_value is None:
            error_preview = None
        else:
            error_preview = str(error_value).strip()
            if len(error_preview) > 240:
                error_preview = f"{error_preview[:240].rstrip()}..."

        trace_payload: Dict[str, Any] = {
            "resource_type": resource_type,
            "request_id": request_id,
            "status": status,
            "started_at": (started or {}).get("started_at"),
            "completed_at": record.get("timestamp"),
            "duration_sec": record.get("duration_sec"),
            "endpoint_id": record.get("endpoint_id") or (started or {}).get("endpoint_id"),
            "model": record.get("model") or (started or {}).get("model"),
            "agent_id": record.get("agent_id") or (started or {}).get("agent_id"),
            "agent_ids": record.get("agent_ids") or (started or {}).get("agent_ids"),
            "post_id": record.get("post_id") or (started or {}).get("post_id"),
            "post_ids": record.get("post_ids") or (started or {}).get("post_ids"),
            "step_names": record.get("step_names") or (started or {}).get("step_names"),
            "interaction_types": record.get("interaction_types") or (started or {}).get("interaction_types"),
            "interaction_names": record.get("interaction_names") or (started or {}).get("interaction_names"),
            "messages_count": record.get("messages_count") or (started or {}).get("messages_count"),
            "tools_count": record.get("tools_count") or (started or {}).get("tools_count"),
            "tools_characters": record.get("tools_characters") or (started or {}).get("tools_characters"),
            "payload_characters": record.get("payload_characters") or (started or {}).get("payload_characters"),
            "max_tokens": record.get("max_tokens") or (started or {}).get("max_tokens"),
            "temperature": record.get("temperature") if "temperature" in record else (started or {}).get("temperature"),
            "top_p": record.get("top_p") if "top_p" in record else (started or {}).get("top_p"),
            "texts_count": record.get("texts_count") or (started or {}).get("texts_count"),
            "dimensions": record.get("dimensions") or (started or {}).get("dimensions"),
            "input_characters": record.get("input_characters") or (started or {}).get("input_characters"),
            "queue_duration_sec": record.get("queue_duration_sec"),
            "provider_duration_sec": record.get("provider_duration_sec"),
            "step": record.get("step") or (started or {}).get("step"),
            "step_name": record.get("step_name") or (started or {}).get("step_name"),
            "interaction_type": record.get("interaction_type") or (started or {}).get("interaction_type"),
            "interaction_name": record.get("interaction_name") or (started or {}).get("interaction_name"),
            "cache_hit": record.get("cache_hit") if "cache_hit" in record else (started or {}).get("cache_hit"),
            "retry_count": record.get("retry_count"),
            "prompt_tokens": record.get("prompt_tokens"),
            "completion_tokens": record.get("completion_tokens"),
            "total_tokens": record.get("total_tokens"),
            "vectors_returned": record.get("vectors_returned"),
            "error_type": record.get("error_type"),
            "error_preview": error_preview,
        }

        # 保持输出简洁，仅保留有值字段
        compact_payload = {k: v for k, v in trace_payload.items() if v is not None}
        self.resource_calls.log("INFO", "resource_call_trace", **compact_payload)

    def _notify_hooks(self, channel: str, record: Dict[str, Any]) -> None:
        if not self._hooks:
            return
        for hook in list(self._hooks):
            try:
                hook(channel, record)
            except Exception:
                # Hook 失败不影响主流程
                continue

    def close(self) -> None:
        self.runtime.close()
        self.schedule.close()
        self.env.close()
        self.system.close()
        for logger in self.resources.values():
            logger.close()
        self.resource_calls.close()
        self._agent_cache.clear()
