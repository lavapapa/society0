"""
通用异步辅助工具。

提供 `invoke_maybe_async`，在不知道目标函数是否为协程实现时，
安全地执行并返回结果，避免错误的 `await` 调用。
"""

from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, TypeVar

T = TypeVar("T")


async def invoke_maybe_async(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """
    安全调用可能为同步或异步的函数。

    Args:
        func: 目标函数
        *args: 位置参数
        **kwargs: 关键字参数

    Returns:
        函数返回值。如函数返回协程，则自动 await 后返回其结果。
    """
    result = func(*args, **kwargs)

    if inspect.isawaitable(result):
        return await result

    return result
