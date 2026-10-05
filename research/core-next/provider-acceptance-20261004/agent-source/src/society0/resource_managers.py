"""请求资源与留证脱敏；提供方协议由低层模型 SDK 实现。"""
from __future__ import annotations
import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager, AsyncExitStack
import random
import re
import time
from typing import Any, Iterable

_REDACTED_CREDENTIAL = "[REDACTED]"
_CREDENTIAL_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credentials",
    "password",
    "proxy_authorization",
    "secret",
    "set_cookie",
    "token",
    "x_api_key",
}
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(?ix)"
    r"(\b(?:api[_ -]?key|authorization|cookie|credentials|password|"
    r"proxy[_ -]?authorization|secret|set[_ -]?cookie|token|x[_ -]?api[_ -]?key)\b"
    r"\s*(?:[:=]\s*|\bis\s+))"
    r"([^\s,;\]}\[]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)(\bBearer\s+)([^\s,;\]}\[]+)")


def _is_credential_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
    return normalized in _CREDENTIAL_KEY_NAMES or normalized.endswith(
        ("_api_key", "_password", "_secret", "_token")
    )


def _redact_text(value: str, *, secrets: Iterable[Any] = ()) -> str:
    """Remove credentials embedded in exception, tool, or provider text."""

    redacted = _CREDENTIAL_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)}{_REDACTED_CREDENTIAL}",
        str(value),
    )
    redacted = _BEARER_PATTERN.sub(
        lambda match: f"{match.group(1)}{_REDACTED_CREDENTIAL}",
        redacted,
    )
    for secret in secrets:
        if secret is None:
            continue
        secret_text = str(secret)
        if secret_text:
            redacted = redacted.replace(secret_text, _REDACTED_CREDENTIAL)
    return redacted


def redact_credentials(value: Any, *, secrets: Iterable[Any] = ()) -> Any:
    """Recursively redact credential-bearing mapping fields.

    Provider request/exception objects frequently nest headers and transport
    options several levels deep.  Redacting only the top-level request leaves
    credentials in durable Thread evidence, so every mapping/list branch is
    traversed before it is serialized.
    """

    if isinstance(value, str):
        return _redact_text(value, secrets=secrets)
    if isinstance(value, Mapping):
        return {
            key: _REDACTED_CREDENTIAL
            if _is_credential_key(key)
            else redact_credentials(item, secrets=secrets)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_credentials(item, secrets=secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_credentials(item, secrets=secrets) for item in value)
    return value



class RequestResources:
    """端点许可先于运行许可；等待期间不持完整模型输入。"""
    def __init__(self, concurrency, *, shared=None, rpm=None, jitter=0):
        if type(concurrency) is not int or concurrency < 1:
            raise ValueError('concurrency must be positive')
        if jitter < 0: raise ValueError('jitter must be nonnegative')
        self.endpoint = asyncio.Semaphore(concurrency)
        self.shared, self.jitter = shared, jitter
        self.rate = None
        if rpm is not None:
            if rpm <= 0: raise ValueError('rpm must be positive')
            from aiolimiter import AsyncLimiter
            self.rate = AsyncLimiter(1, 60 / rpm)

    @asynccontextmanager
    async def acquire(self):
        started = time.perf_counter()
        jitter = random.uniform(0, self.jitter) if self.jitter else 0
        if jitter: await asyncio.sleep(jitter)
        async with AsyncExitStack() as stack:
            if self.rate is not None: await stack.enter_async_context(self.rate)
            await stack.enter_async_context(self.endpoint)
            if self.shared is not None: await stack.enter_async_context(self.shared)
            yield {'queue_s': time.perf_counter() - started - jitter, 'jitter_s': jitter}
