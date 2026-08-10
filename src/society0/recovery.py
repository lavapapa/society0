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


@dataclass(frozen=True, slots=True)
class StepFailure:
    """一次未发布 step 的可审计失败摘要。"""

    failed_step: int
    last_complete_step: int | None
    error_type: str
    error: str
    error_fingerprint: str
    retryable: bool

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

    默认只把连接和超时类故障视为可重试候选。schema、领域不变量、
    checkpoint 校验和磁盘错误保持 fail-closed，由调用方修复后再恢复。
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
    )
