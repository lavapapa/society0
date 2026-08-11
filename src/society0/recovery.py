"""Society0 step 失败分类与恢复边界。"""

from __future__ import annotations

import errno
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any


_RETRYABLE_OS_ERRORS = {
    errno.ECONNABORTED,
    errno.ECONNREFUSED,
    errno.ECONNRESET,
    errno.EHOSTUNREACH,
    errno.ENETDOWN,
    errno.ENETUNREACH,
    errno.ETIMEDOUT,
}

_RETRYABLE_MESSAGE_MARKERS = (
    "empty_model_response",
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "connection aborted",
    "temporarily unavailable",
    "rate limit",
    "http 429",
    "http 502",
    "http 503",
    "http 504",
)

_SCHEMA_MESSAGE_MARKERS = (
    "tool schema error",
    "tool_schema_error",
    "invalid action arguments",
    "invalid arguments for action",
)

_WORLD_FAILURE_MARKERS = (
    "world writer",
    "state invariant",
    "state corruption",
    "checkpoint",
    "persistence",
    "failed to persist",
    "thread event",
)


@dataclass(frozen=True, slots=True)
class StepFailure:
    """一次未发布 step 的可审计失败摘要。"""

    failed_step: int
    last_complete_step: int | None
    error_type: str
    error: str
    error_fingerprint: str
    retryable: bool
    failure_class: str = "unknown"
    retry_scope: str = "step"

    @property
    def recoverable(self) -> bool:
        return self.last_complete_step is not None

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["recoverable"] = self.recoverable
        return result


def classify_step_failure(
    exc: BaseException,
    *,
    failed_step: int,
    last_complete_step: int | None,
) -> StepFailure:
    """把异常整理成 runner 可消费的默认失败分类。

    返回失败类型和重试粒度：provider 传输/空响应只属于当前 Agent 激活，
    工具参数 schema 错误由同一 Agent 收到结构化工具错误，World writer、
    checkpoint 和状态不变量错误才属于整步恢复边界。
    """

    message = str(exc) or repr(exc)
    retryable = False
    messages: list[str] = []
    cursor: BaseException | None = exc
    visited: set[int] = set()
    while cursor is not None and id(cursor) not in visited:
        visited.add(id(cursor))
        messages.append(str(cursor) or repr(cursor))
        retryable = retryable or isinstance(cursor, (TimeoutError, ConnectionError))
        if isinstance(cursor, OSError):
            retryable = retryable or cursor.errno in _RETRYABLE_OS_ERRORS
        cursor = cursor.__cause__ or cursor.__context__
    lowered = "\n".join(messages).lower()
    retryable = retryable or any(
        marker in lowered for marker in _RETRYABLE_MESSAGE_MARKERS
    )
    explicit_failure_class = str(getattr(exc, "failure_class", "") or "")
    explicit_retry_scope = str(getattr(exc, "retry_scope", "") or "")
    is_world_failure = any(marker in lowered for marker in _WORLD_FAILURE_MARKERS)
    is_schema_error = explicit_failure_class == "tool_schema_error" or any(
        marker in lowered for marker in _SCHEMA_MESSAGE_MARKERS
    )
    if not is_schema_error and any(
        marker in lowered for marker in ("additional properties", "required property")
    ):
        # These JSON-schema phrases are only useful when the diagnostic also
        # identifies the Action/tool boundary; bare schema diagnostics remain
        # fail-closed at the step boundary.
        is_schema_error = "tool" in lowered or "action" in lowered
    is_empty_response = "empty_model_response" in lowered
    is_timeout = (
        retryable
        and not is_empty_response
        and (
            isinstance(exc, (TimeoutError, ConnectionError))
            or "timeout" in lowered
            or "timed out" in lowered
        )
    )
    if is_world_failure:
        # Persistence/checkpoint/state failures are step boundaries even when
        # an implementation happens to mention a schema in its diagnostics.
        failure_class = "world_writer_error"
        retry_scope = "step"
        retryable = False
    elif explicit_failure_class in {
        "provider_timeout",
        "provider_transport_error",
        "provider_empty_response",
    }:
        failure_class = explicit_failure_class
        retry_scope = explicit_retry_scope or "agent_activation"
    elif is_schema_error:
        failure_class = "tool_schema_error"
        retry_scope = "agent_activation"
        # The ActionLoop sends this back to the same Agent as a structured
        # tool error. It must never trigger a step replay.
        retryable = False
    elif is_empty_response:
        failure_class = "provider_empty_response"
        retry_scope = "agent_activation"
    elif is_timeout:
        failure_class = "provider_timeout"
        retry_scope = "agent_activation"
    elif retryable:
        failure_class = "provider_transport_error"
        retry_scope = "agent_activation"
    else:
        failure_class = "unclassified"
        retry_scope = "step"
    payload = {
        "error_type": type(exc).__name__,
        "error": message,
        "failed_step": int(failed_step),
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return StepFailure(
        failed_step=int(failed_step),
        last_complete_step=last_complete_step,
        error_type=type(exc).__name__,
        error=message,
        error_fingerprint=fingerprint,
        retryable=retryable,
        failure_class=failure_class,
        retry_scope=retry_scope,
    )
