"""Env 状态持久化的简洁声明 API。

业务代码仍然操作普通 ``dict`` / ``list``。这些函数只在 Env 元数据中描述
每个顶层状态容器的持久化语义，不创建运行时包装对象。
"""

from __future__ import annotations

import copy
from typing import Any, Mapping


_MISSING = object()


def _declared_node(
    kind: str,
    *,
    schema: Mapping[str, Any] | None = None,
    default: Any = _MISSING,
    granularity: str | None = None,
) -> dict[str, Any]:
    node = copy.deepcopy(dict(schema or {}))
    declaration: dict[str, Any] = {"kind": kind}
    if granularity is not None:
        declaration["granularity"] = granularity
    node["persistence"] = declaration
    if default is not _MISSING:
        node["default"] = copy.deepcopy(default)
    return node


def replaceable(
    *, schema: Mapping[str, Any] | None = None, default: Any = _MISSING
) -> dict[str, Any]:
    """声明一个有界值或有界对象；深层写入会替换这一整体投影。"""

    return _declared_node("replaceable", schema=schema, default=default)


def replaceable_map(
    *, entry_schema: Mapping[str, Any] | None = None, default: Any = _MISSING
) -> dict[str, Any]:
    """声明动态对象表；每个被修改的 key 是独立的有界替换投影。"""

    return _declared_node(
        "replaceable",
        schema={
            "type": "object",
            "additionalProperties": copy.deepcopy(dict(entry_schema or {})),
        },
        default=default,
        granularity="entry",
    )


def append_only_map(
    *, entry_schema: Mapping[str, Any] | None = None, default: Any = _MISSING
) -> dict[str, Any]:
    """声明按唯一 ID 追加的不可变事实表。"""

    return _declared_node(
        "append_only_map",
        schema={
            "type": "object",
            "additionalProperties": copy.deepcopy(dict(entry_schema or {})),
        },
        default=default,
    )


def append_only_list(
    *, item_schema: Mapping[str, Any] | None = None, default: Any = _MISSING
) -> dict[str, Any]:
    """声明只允许 ``append`` / ``extend`` 的有序不可变事实流。"""

    return _declared_node(
        "append_only_list",
        schema={"type": "array", "items": copy.deepcopy(dict(item_schema or {}))},
        default=default,
    )


def transient(
    *, schema: Mapping[str, Any] | None = None, default: Any = _MISSING
) -> dict[str, Any]:
    """声明 Tick 临时子树；写入正常生效，checkpoint 丢弃并按默认值恢复。"""

    return _declared_node("transient", schema=schema, default=default)


def persistent_state_schema(**fields: Mapping[str, Any]) -> dict[str, Any]:
    """生成严格的 Env ``state_schema``，未知顶层字段一律拒绝。"""

    return {
        "type": "object",
        "properties": copy.deepcopy(dict(fields)),
        "additionalProperties": False,
    }


__all__ = [
    "append_only_list",
    "append_only_map",
    "persistent_state_schema",
    "replaceable",
    "replaceable_map",
    "transient",
]
