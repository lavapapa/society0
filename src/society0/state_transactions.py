"""Explicit state access for the Society0 World.

The default ``transparent_proxy`` mode lives in :mod:`society0.state_proxy`.
This module contains the deliberately small companion used by
``explicit_transactions``: read-only, zero-copy views and a synchronous
copy-on-write transaction.  It is intentionally independent from the
checkpoint writer; a transaction only prepares a deterministic list of state
operations and hands those operations to the existing ``StateDeltaJournal``
at commit time.
"""

from __future__ import annotations

import copy
import contextvars
import threading
from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Iterator, Optional


class StateAccessMode(str, Enum):
    """World state access contract selected by the simulation engine."""

    TRANSPARENT_PROXY = "transparent_proxy"
    EXPLICIT_TRANSACTIONS = "explicit_transactions"

    @classmethod
    def coerce(cls, value: "StateAccessMode | str | None") -> "StateAccessMode":
        if value is None:
            return cls.TRANSPARENT_PROXY
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(
                "state_access_mode must be 'transparent_proxy' or "
                "'explicit_transactions'"
            ) from exc


class _ViewLease:
    """Generation-based lease for ordinary explicit-mode read views."""

    __slots__ = ("generation", "active")

    def __init__(self, generation: int) -> None:
        self.generation = int(generation)
        self.active = True

    def invalidate(self) -> None:
        self.active = False

    def ensure_live(self) -> None:
        if not self.active:
            raise RuntimeError("state view has expired")


def _plain(value: Any) -> Any:
    """Detach a transaction value without retaining proxy/view objects."""

    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [_plain(item) for item in value]
    return copy.deepcopy(value)


def _staged_value(value: Any) -> Any:
    """保留普通容器赋值后的引用语义，同时拆掉事务/只读视图。"""

    if isinstance(value, (_ReadViewBase, _TxBase)):
        return _plain(value)
    return value


class _ReadViewBase:
    __slots__ = ("_target", "_lease", "_child_cache")

    def __init__(
        self,
        target: Any,
        lease: Any,
    ) -> None:
        self._target = target
        self._lease = lease
        self._child_cache: dict[Any, Any] = {}

    def _ensure_live(self) -> None:
        lease = self._lease
        if lease is not None and not lease.active:
            raise RuntimeError("state view has expired")

    def _value(self) -> Any:
        self._ensure_live()
        return self._target

    def _child(self, key: Any, value: Any) -> Any:
        if isinstance(value, dict):
            cached = self._child_cache.get(key)
            if isinstance(cached, ReadOnlyDict) and cached._target is value:
                return cached
            child = ReadOnlyDict(value, self._lease)
            self._child_cache[key] = child
            return child
        if isinstance(value, list):
            cached = self._child_cache.get(key)
            if isinstance(cached, ReadOnlyList) and cached._target is value:
                return cached
            child = ReadOnlyList(value, self._lease)
            self._child_cache[key] = child
            return child
        return value

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        self._ensure_live()
        existing = memo.get(id(self))
        if existing is not None:
            return existing
        value = _plain(self._value())
        memo[id(self)] = value
        return value


class ReadOnlyDict(_ReadViewBase, Mapping):
    """Zero-copy recursive read view over a canonical or transactional map."""

    def __getitem__(self, key: Any) -> Any:
        self._ensure_live()
        value = self._target[key]
        return self._child(key, value)

    def __iter__(self) -> Iterator[Any]:
        self._ensure_live()
        return iter(self._target)

    def __len__(self) -> int:
        self._ensure_live()
        return len(self._target)

    def keys(self):
        self._ensure_live()
        return self._target.keys()

    def values(self):
        self._ensure_live()
        return (self[key] for key in self._target)

    def items(self):
        self._ensure_live()
        return ((key, self[key]) for key in self._target)

    def __setitem__(self, key: Any, value: Any) -> None:
        raise TypeError("explicit state views are read-only; use write_transaction()")

    def __delitem__(self, key: Any) -> None:
        raise TypeError("explicit state views are read-only; use write_transaction()")

    def __repr__(self) -> str:
        return f"ReadOnlyDict({dict(self.items())!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return dict(self.items()) == dict(other.items())
        return NotImplemented


class ReadOnlyList(_ReadViewBase, Sequence):
    """Zero-copy recursive read view over a canonical or transactional list."""

    def __getitem__(self, index: Any) -> Any:
        self._ensure_live()
        value = self._target[index]
        if isinstance(index, slice):
            return [self._child(offset, item) for offset, item in zip(
                range(*index.indices(len(self._target))), value
            )]
        return self._child(index, value)

    def __len__(self) -> int:
        self._ensure_live()
        return len(self._target)

    def __iter__(self) -> Iterator[Any]:
        self._ensure_live()
        return (self[index] for index in range(len(self._target)))

    def __setitem__(self, index: Any, value: Any) -> None:
        raise TypeError("explicit state views are read-only; use write_transaction()")

    def __delitem__(self, index: Any) -> None:
        raise TypeError("explicit state views are read-only; use write_transaction()")

    def append(self, value: Any) -> None:
        raise TypeError("explicit state views are read-only; use write_transaction()")

    def extend(self, values: Iterable[Any]) -> None:
        raise TypeError("explicit state views are read-only; use write_transaction()")

    def __repr__(self) -> str:
        return f"ReadOnlyList({list(self)!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes, bytearray)):
            return list(self) == list(other)
        return NotImplemented


@dataclass(frozen=True)
class _StagedOperation:
    container: tuple[Any, ...]
    operation: str
    key: Any
    value: Any
    anchor: tuple[Any, ...]
    kind: Any
    affected: tuple[Any, ...]
    conflict_key: Any


@dataclass(frozen=True)
class _PreparedStateCommit:
    """提交前一次性冻结的 canonical/journal 操作。"""

    operations: tuple[tuple[tuple[Any, ...], str, Any, Any], ...]
    tokens: tuple[Any, ...]
    conflict_keys: tuple[Any, ...]
    final_values: tuple[tuple[tuple[Any, ...], Any], ...]


class StateTransactionConflict(RuntimeError):
    """Canonical record changed after this transaction staged its first write."""


class StateTransaction:
    """Synchronous copy-on-write transaction for one World state root."""

    def __init__(
        self,
        world: Any,
        root_path: tuple[Any, ...],
        *,
        access_context: Any = None,
    ) -> None:
        self.world = world
        self.root_path = tuple(root_path)
        self.access_context = access_context
        self._active = False
        self._entered = False
        self._lease = _ViewLease(0)
        self._state_view: Any = None
        self._overlays: dict[tuple[Any, ...], Any] = {}
        self._overlay_children: dict[tuple[Any, ...], set[Any]] = {}
        self._deleted_anchors: set[tuple[Any, ...]] = set()
        self._deleted_children: dict[tuple[Any, ...], set[Any]] = {}
        self._append_maps: dict[tuple[Any, ...], dict[Any, Any]] = {}
        self._append_lists: dict[tuple[Any, ...], list[Any]] = {}
        self._operations: list[_StagedOperation] = []
        self._base_versions: dict[tuple[Any, ...], int] = {}
        self._append_ids: set[tuple[tuple[Any, ...], Any]] = set()
        self._start_tick = None

    # ------------------------------------------------------------------
    # lifecycle / public API
    # ------------------------------------------------------------------
    def __enter__(self) -> "StateTransaction":
        if self._entered:
            raise RuntimeError("state transaction cannot be entered twice")
        self.world._register_state_transaction(self)
        self._entered = True
        self._active = True
        journal = getattr(self.world, "_state_delta_journal", None)
        self._start_tick = getattr(journal, "active_step", None)
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if not self._active:
            return False
        if exc_type is not None:
            self.rollback()
            return False
        try:
            self.commit()
        except Exception:
            self.invalidate("state transaction commit failed")
            raise
        return False

    @property
    def active(self) -> bool:
        return self._active

    @property
    def state(self) -> Any:
        self._ensure_live()
        if self._state_view is None:
            self._state_view = self._wrap(self.root_path)
        return self._state_view

    def commit(self) -> None:
        self._ensure_live()
        try:
            self.world._commit_state_transaction(self)
        except Exception:
            self.invalidate("state transaction commit failed")
            raise
        self._finish()

    def rollback(self) -> None:
        if not self._active:
            return
        self._finish()

    def invalidate(self, reason: str = "state transaction has expired") -> None:
        if not self._active:
            return
        self._active = False
        self._lease.invalidate()
        self.world._unregister_state_transaction(self)
        self._invalid_reason = str(reason)

    def _finish(self) -> None:
        self._active = False
        self._lease.invalidate()
        self.world._unregister_state_transaction(self)

    def _ensure_live(self) -> None:
        if not self._active:
            reason = getattr(self, "_invalid_reason", "state transaction has expired")
            raise RuntimeError(reason)
        journal = getattr(self.world, "_state_delta_journal", None)
        active_tick = getattr(journal, "active_step", None)
        if active_tick != self._start_tick:
            raise RuntimeError("state transaction cannot cross persistence Tick boundaries")

    # ------------------------------------------------------------------
    # zero-copy transactional reads
    # ------------------------------------------------------------------
    def _wrap(self, path: tuple[Any, ...]) -> Any:
        value = self._lookup(path)
        if isinstance(value, (_OverlayMap, _OverlayList)):
            return value
        if isinstance(value, Mapping):
            return _TxDict(self, path)
        if isinstance(value, list):
            return _TxList(self, path)
        return value

    def _lookup(self, path: tuple[Any, ...]) -> Any:
        self._ensure_live()
        anchor = self._longest_ancestor(path, self._deleted_anchors)
        if anchor is not None:
            raise KeyError(path[len(anchor) - 1] if anchor else path)

        # 新增事实缓冲优先于可能包含它的父记录 overlay。新建 mixed entity
        # 后继续追加其 journal 时，父记录候选只保留空历史容器，新增事实仍
        # 必须能在事务内立即读到。
        anchor = self._longest_ancestor(path, self._append_maps)
        if anchor is not None:
            remainder = path[len(anchor) :]
            if not remainder:
                return _OverlayMap(self, anchor)
            key = remainder[0]
            buffered = self._append_maps[anchor]
            if key in buffered:
                return _walk(buffered[key], remainder[1:])

        anchor = self._longest_ancestor(path, self._append_lists)
        if anchor is not None:
            remainder = path[len(anchor) :]
            if not remainder:
                return _OverlayList(self, anchor)
            index = remainder[0]
            if isinstance(index, int):
                try:
                    canonical = self._canonical_value(anchor)
                except KeyError:
                    canonical = _walk(self._overlays[self._covering_overlay(anchor)], anchor[len(self._covering_overlay(anchor)) :])
                if index < 0:
                    index += len(canonical) + len(self._append_lists[anchor])
                if index >= len(canonical):
                    value = self._append_lists[anchor][index - len(canonical)]
                    return _walk(value, remainder[1:])

        # Longest staged replacement wins.
        anchor = self._longest_ancestor(path, self._overlays)
        if anchor is not None:
            if anchor in self._deleted_anchors:
                raise KeyError(path[-1] if path else anchor)
            value = self._overlays[anchor]
            return _walk(value, path[len(anchor) :])

        return self._canonical_value(path)

    def _covering_overlay(self, path: tuple[Any, ...]) -> tuple[Any, ...]:
        anchor = self._longest_ancestor(path, self._overlays)
        if anchor is None:
            raise KeyError(path)
        return anchor

    def _longest_ancestor(self, path: tuple[Any, ...], keys: Any) -> tuple[Any, ...] | None:
        """按路径深度查找 overlay；成本只随状态路径深度增长。"""

        for size in range(len(path), len(self.root_path) - 1, -1):
            candidate = path[:size]
            if candidate in keys:
                return candidate
        return None

    def _base_append_list(self, path: tuple[Any, ...]) -> list[Any]:
        """返回 append list 的既有部分；新建父记录时从父 overlay 读取。"""

        try:
            value = self._canonical_value(path)
        except KeyError:
            covering = self._covering_overlay(path)
            value = _walk(self._overlays[covering], path[len(covering) :])
        if not isinstance(value, list):
            raise TypeError(f"append-only state at {path!r} must be a list")
        return value

    def _base_append_map(self, path: tuple[Any, ...]) -> Mapping[Any, Any]:
        """返回 append map 的既有部分；新建父记录时从父 overlay 读取。"""

        try:
            value = self._canonical_value(path)
        except KeyError:
            covering = self._covering_overlay(path)
            value = _walk(self._overlays[covering], path[len(covering) :])
        if not isinstance(value, Mapping):
            raise TypeError(f"append-only state at {path!r} must be a mapping")
        return value

    def _canonical_value(self, path: tuple[Any, ...]) -> Any:
        """读取 canonical 路径，并保留合法的 ``None`` 值。"""

        if not path:
            raise KeyError(path)
        if path[0] == "environment":
            current: Any = self.world.environment_data
        elif path[0] == "agents":
            current = self.world.agents_data
        else:
            raise KeyError(path[0])
        for part in path[1:]:
            try:
                current = current[part]
            except (KeyError, IndexError, TypeError) as exc:
                raise KeyError(part) from exc
        return current

    def _mapping_keys(self, path: tuple[Any, ...]) -> list[Any]:
        """返回包含直接 child overlay/deletion 的虚拟 map 键集合。"""

        self._ensure_live()
        value = self._lookup(path)
        if not isinstance(value, Mapping):
            raise TypeError(f"state value at {path!r} is not a mapping")
        keys = list(value.keys())
        seen = set(keys)
        for key in self._overlay_children.get(path, ()):
            anchor = path + (key,)
            if key not in seen and anchor not in self._deleted_anchors:
                keys.append(key)
                seen.add(key)
        for key in self._deleted_children.get(path, ()):
            if key in seen:
                keys.remove(key)
                seen.remove(key)
        return keys

    def _plain_at(self, path: tuple[Any, ...]) -> Any:
        return _plain(self._lookup(path))

    # ------------------------------------------------------------------
    # staging writes
    # ------------------------------------------------------------------
    def _stage(self, container: tuple[Any, ...], operation: str, key: Any, value: Any = None) -> Any:
        lock = getattr(self.world, "_state_transaction_lock", None)
        if lock is None:
            return self._stage_locked(container, operation, key, value)
        with lock:
            return self._stage_locked(container, operation, key, value)

    def _stage_locked(
        self,
        container: tuple[Any, ...],
        operation: str,
        key: Any,
        value: Any = None,
    ) -> Any:
        self._ensure_live()
        self._check_access(container, key)
        token = self._dry_prepare(container, operation, key, value)
        anchor = tuple(getattr(token, "anchor", self.root_path))
        kind = getattr(token, "kind", None)
        affected = container + (key,) if key is not None else container

        if operation == "clear":
            schema = getattr(self.world, "_persistence_schema", None)
            resolution = schema.resolve_write(container) if schema is not None else None
            if (
                resolution is not None
                and resolution.anchor == container
                and schema.has_append_only_descendant(resolution.rule)
            ):
                raise ValueError(
                    "mixed entity with nested append-only history requires "
                    f"per-field writes and cannot be cleared: {container!r}"
                )

        # The existing journal is the authority for persistence declarations.
        # When no journal is configured, a transaction still works over the
        # selected root, but has no durable delta to publish.
        if kind is None:
            anchor = self.root_path

        kind_value = getattr(kind, "value", kind)
        conflict_key = _conflict_key(anchor, kind_value, key)
        # 先取得版本，再读取或复制 canonical。若并发提交发生在两者之间，
        # commit 会检测到版本变化并拒绝旧事务；反向顺序会让旧副本错误地
        # 绑定到新版本，造成丢失更新。
        self._remember_version(conflict_key)
        if kind_value == "append_only_map":
            identity = (anchor, key)
            if identity in self._append_ids:
                raise ValueError(f"duplicate append-only map id: {key}")
            if key in self._base_append_map(anchor):
                raise ValueError(f"duplicate append-only map id: {key}")
            self._append_ids.add(identity)
            self._append_maps.setdefault(anchor, {})[key] = _staged_value(value)
        elif kind_value == "append_only_list":
            if operation != "append":
                raise ValueError(f"append-only list only accepts append: {affected!r}")
            self._append_lists.setdefault(anchor, []).append(_staged_value(value))
        else:
            self._stage_overlay_write(anchor, container, operation, key, value, affected)

        self._operations.append(
            _StagedOperation(
                container=container,
                operation=operation,
                key=key,
                value=_staged_value(value),
                anchor=anchor,
                kind=kind,
                affected=affected,
                conflict_key=conflict_key,
            )
        )
        return value

    def _dry_prepare(self, container: tuple[Any, ...], operation: str, key: Any, value: Any) -> Any:
        journal = getattr(self.world, "_state_delta_journal", None)
        if journal is not None and getattr(journal, "active_step", None) is not None:
            # 显式事务允许一次业务记录内先写入暂态值，再在提交前形成合法
            # 终态。此处只解析持久化种类和操作权限，值校验在整条记录冻结后
            # 统一完成；透明代理仍保持逐操作即时校验。
            return journal.prepare_proxy_operation(
                container,
                operation,
                key,
                value,
                validate_value=False,
                allow_mixed_field_anchor=True,
            )
        schema = getattr(self.world, "_persistence_schema", None)
        if schema is None:
            return None
        resolution = schema.resolve_write(container + (key,) if key is not None else container)
        if resolution is None:
            raise ValueError(f"undeclared persistence path: {container + (key,) if key is not None else container!r}")
        if resolution.rule.kind.value == "append_only_map" and operation != "set":
            raise ValueError("append-only map entry is immutable")
        if resolution.rule.kind.value == "append_only_list" and operation != "append":
            raise ValueError("append-only list only accepts append")
        if operation in {"set", "append", "insert"}:
            schema.validate_write_value(container + (key,) if key is not None else container, value)
        return resolution

    def _remember_version(self, conflict_key: Any) -> None:
        if conflict_key is None:
            return
        if conflict_key not in self._base_versions:
            versions = getattr(self.world, "_state_record_versions", {})
            self._base_versions[conflict_key] = int(versions.get(conflict_key, 0))

    def _cancel_buffered_map_create(
        self,
        path: tuple[Any, ...],
        key: Any,
    ) -> None:
        """取消当前事务中新建、尚未提交的 append-only map 记录。"""

        lock = getattr(self.world, "_state_transaction_lock", None)
        if lock is None:
            self._cancel_buffered_map_create_locked(path, key)
            return
        with lock:
            self._cancel_buffered_map_create_locked(path, key)

    def _cancel_buffered_map_create_locked(
        self,
        path: tuple[Any, ...],
        key: Any,
    ) -> None:
        self._ensure_live()
        self._check_access(path, key)
        buffered = self._append_maps.get(path)
        if buffered is None or key not in buffered:
            if key in self._base_append_map(path):
                raise ValueError("append-only map entry is immutable")
            raise KeyError(key)

        del buffered[key]
        if not buffered:
            self._append_maps.pop(path, None)
        self._append_ids.discard((path, key))
        conflict_key = _conflict_key(path, "append_only_map", key)
        self._operations = [
            operation
            for operation in self._operations
            if not (
                getattr(operation.kind, "value", operation.kind)
                == "append_only_map"
                and operation.anchor == path
                and operation.key == key
            )
        ]
        if not any(
            operation.conflict_key == conflict_key
            for operation in self._operations
        ):
            self._base_versions.pop(conflict_key, None)

    def _stage_overlay_write(
        self,
        anchor: tuple[Any, ...],
        container: tuple[Any, ...],
        operation: str,
        key: Any,
        value: Any,
        affected: tuple[Any, ...],
    ) -> None:
        # Direct replacement of a dynamic entry has no canonical anchor yet.
        if operation == "set" and anchor == affected:
            self._overlays[anchor] = _staged_value(value)
            self._overlay_children.setdefault(anchor[:-1], set()).add(anchor[-1])
            self._deleted_anchors.discard(anchor)
            self._deleted_children.get(anchor[:-1], set()).discard(anchor[-1])
            return
        if operation == "delete" and anchor == affected:
            # 与普通 dict 保持一致：删除不存在的 key 立即报错；同一事务内
            # 新建的记录仍可被正常删除并在提交时折叠为无操作。
            self._lookup(affected)
            self._deleted_anchors.add(anchor)
            self._overlays.pop(anchor, None)
            self._overlay_children.get(anchor[:-1], set()).discard(anchor[-1])
            self._deleted_children.setdefault(anchor[:-1], set()).add(anchor[-1])
            return
        if anchor not in self._overlays:
            current = self._lookup(anchor)
            self._overlays[anchor] = copy.deepcopy(current)
            self._overlay_children.setdefault(anchor[:-1], set()).add(anchor[-1])
        target = self._lookup_from_overlay(anchor, container)
        self._apply_operation(target, operation, key, value)

    def _lookup_from_overlay(self, anchor: tuple[Any, ...], path: tuple[Any, ...]) -> Any:
        value = self._overlays[anchor]
        return _walk(value, path[len(anchor) :])

    @staticmethod
    def _apply_operation(target: Any, operation: str, key: Any, value: Any) -> Any:
        if operation == "set":
            target[key] = _staged_value(value)
        elif operation == "delete":
            del target[key]
        elif operation == "append":
            target.append(_staged_value(value))
        elif operation == "insert":
            target.insert(key, _staged_value(value))
        elif operation in ("remove",):
            target.remove(value)
        elif operation in ("pop",):
            return target.pop(key)
        elif operation == "clear":
            target.clear()
        elif operation == "reverse":
            target.reverse()
        elif operation == "sort":
            target.sort(**(value or {}))
        else:
            raise ValueError(f"unsupported state transaction operation: {operation}")
        return None

    # Public mutator helpers used by _TxDict/_TxList.
    def dict_set(self, path: tuple[Any, ...], key: Any, value: Any) -> None:
        self._stage(path, "set", key, value)

    def dict_delete(self, path: tuple[Any, ...], key: Any) -> None:
        if key in self._append_maps.get(path, {}):
            self._cancel_buffered_map_create(path, key)
            return
        self._stage(path, "delete", key)

    def dict_clear(self, path: tuple[Any, ...]) -> None:
        self._stage(path, "clear", None)

    def list_set(self, path: tuple[Any, ...], index: Any, value: Any) -> None:
        self._stage(path, "set", index, value)

    def list_delete(self, path: tuple[Any, ...], index: Any) -> Any:
        value = self._lookup(path)[index]
        self._stage(path, "delete", index)
        return value

    def list_append(self, path: tuple[Any, ...], value: Any) -> None:
        self._stage(path, "append", len(self._lookup(path)), value)

    def list_insert(self, path: tuple[Any, ...], index: int, value: Any) -> None:
        self._stage(path, "insert", index, value)

    def list_remove(self, path: tuple[Any, ...], value: Any) -> None:
        self._stage(path, "remove", self._lookup(path).index(value), value)

    def list_pop(self, path: tuple[Any, ...], index: int = -1) -> Any:
        value = self._lookup(path)[index]
        self._stage(path, "pop", index)
        return value

    def list_clear(self, path: tuple[Any, ...]) -> None:
        self._stage(path, "clear", None)

    def list_reverse(self, path: tuple[Any, ...]) -> None:
        self._stage(path, "reverse", None)

    def list_sort(self, path: tuple[Any, ...], *, key: Any = None, reverse: bool = False) -> None:
        self._stage(path, "sort", None, {"key": key, "reverse": reverse})

    # ------------------------------------------------------------------
    # commit preparation helpers (called by World under its commit lock)
    # ------------------------------------------------------------------
    def _check_access(self, container: tuple[Any, ...], key: Any) -> None:
        context = self.access_context
        if context is None:
            return
        root_len = len(self.root_path)
        relative = container[root_len:]
        field = relative[0] if relative else key
        if field is not None and not context.can_write(field):
            raise PermissionError(
                f"权限不足：{context.caller_type} '{context.caller_id}' 无法修改字段 '{field}'。"
            )

    def prepare_for_commit(self) -> _PreparedStateCommit:
        """Freeze all canonical and journal work before the first mutation."""

        self._ensure_live()
        journal = getattr(self.world, "_state_delta_journal", None)
        final_by_anchor: dict[tuple[Any, ...], Any] = {}
        last_index: dict[tuple[Any, ...], int] = {}
        append_operations: list[tuple[int, tuple[tuple[Any, ...], str, Any, Any]]] = []
        for index, op in enumerate(self._operations):
            kind_value = getattr(op.kind, "value", op.kind)
            if kind_value in {"append_only_map", "append_only_list"}:
                append_operations.append(
                    (
                        index,
                        (
                            op.container,
                            op.operation,
                            op.key,
                            copy.deepcopy(self._commit_value(op)),
                        ),
                    )
                )
                continue
            last_index[op.anchor] = index

        bounded_operations: list[
            tuple[int, tuple[tuple[Any, ...], str, Any, Any]]
        ] = []
        anchor_set = set(last_index)
        top_level_anchors = {
            anchor
            for anchor in anchor_set
            if not any(
                anchor[:size] in anchor_set
                for size in range(len(self.root_path), len(anchor))
            )
        }
        overlay_descendants: dict[tuple[Any, ...], list[tuple[Any, ...]]] = {
            anchor: [] for anchor in top_level_anchors
        }
        deleted_descendants: dict[tuple[Any, ...], list[tuple[Any, ...]]] = {
            anchor: [] for anchor in top_level_anchors
        }
        for child in self._overlays:
            parent = self._longest_ancestor(child[:-1], top_level_anchors)
            if parent is not None:
                overlay_descendants[parent].append(child)
        for child in self._deleted_anchors:
            parent = self._longest_ancestor(child[:-1], top_level_anchors)
            if parent is not None:
                deleted_descendants[parent].append(child)
        for anchor, index in last_index.items():
            if anchor not in top_level_anchors:
                continue
            if anchor in self._deleted_anchors:
                try:
                    self._canonical_value(anchor)
                except KeyError:
                    # 同一事务内创建后删除的可替换记录最终没有 canonical
                    # 变化，也不应生成 delete delta。
                    continue
                bounded_operations.append(
                    (index, (anchor[:-1], "delete", anchor[-1], None))
                )
                continue
            value = self._bounded_final_value(
                anchor,
                overlay_descendants=overlay_descendants[anchor],
                deleted_descendants=deleted_descendants[anchor],
            )
            final_by_anchor[anchor] = value
            bounded_operations.append(
                (index, (anchor[:-1], "set", anchor[-1], copy.deepcopy(value)))
            )

        deleted_roots = tuple(
            anchor for anchor in top_level_anchors if anchor in self._deleted_anchors
        )
        append_operations = [
            item
            for item in append_operations
            if not any(_is_prefix(deleted, item[1][0]) for deleted in deleted_roots)
        ]
        operations = tuple(
            operation
            for _, operation in sorted(
                [*bounded_operations, *append_operations], key=lambda item: item[0]
            )
        )
        created_anchors: set[tuple[Any, ...]] = set()
        deleted_without_canonical: set[tuple[Any, ...]] = set()
        for anchor in self._deleted_anchors:
            try:
                self._canonical_value(anchor)
            except KeyError:
                deleted_without_canonical.add(anchor)
        creation_candidates = {
            op.anchor
            for op in self._operations
            if op.operation == "set" and op.anchor == op.affected
        }
        raw_created_anchors: set[tuple[Any, ...]] = set()
        for anchor in creation_candidates:
            if anchor in self._deleted_anchors:
                continue
            try:
                self._canonical_value(anchor)
            except KeyError:
                raw_created_anchors.add(anchor)
        created_anchors = {
            anchor
            for anchor in raw_created_anchors
            if not any(
                anchor[:size] in raw_created_anchors
                for size in range(len(self.root_path), len(anchor))
            )
        }
        created_final_values: dict[tuple[Any, ...], Any] = {}
        for anchor in created_anchors:
            value = final_by_anchor.get(anchor)
            if value is None:
                value = self._bounded_final_value(anchor)
            created_final_values[anchor] = copy.deepcopy(value)
        journal_operations_list: list[tuple[tuple[Any, ...], str, Any, Any]] = []
        for op in self._operations:
            kind_value = getattr(op.kind, "value", op.kind)
            created_parent = None
            if op.anchor in deleted_without_canonical:
                continue
            if kind_value == "replaceable":
                created_parent = self._longest_ancestor(
                    op.affected, created_anchors
                )
                if created_parent is not None and not (
                    op.operation == "set"
                    and op.anchor == created_parent
                    and op.affected == created_parent
                ):
                    continue
            value = (
                copy.deepcopy(created_final_values[created_parent])
                if created_parent is not None and op.operation == "set"
                else self._commit_value(op)
            )
            journal_operations_list.append(
                (op.container, op.operation, op.key, value)
            )
        journal_operations = tuple(journal_operations_list)
        schema = getattr(self.world, "_persistence_schema", None)
        if schema is not None:
            for anchor, value in final_by_anchor.items():
                schema.validate_write_value(anchor, value, require_complete=True)
        tokens: tuple[Any, ...] = ()
        if journal is not None and getattr(journal, "active_step", None) is not None:
            # Journal 保留原调用路径，以区分“修改 mixed entity 的普通字段”
            # 和“整体替换含只追加历史的 entity”。Canonical patch 可以按锚点
            # 合并，journal token 不能把深层写降格为受禁止的整条替换。
            tokens = tuple(
                journal.prepare_proxy_operations(
                    journal_operations,
                    allow_mixed_field_anchor=True,
                )
            )
        return _PreparedStateCommit(
            operations=operations,
            tokens=tokens,
            conflict_keys=tuple(self._base_versions),
            final_values=tuple(final_by_anchor.items()),
        )

    def _bounded_final_value(
        self,
        anchor: tuple[Any, ...],
        *,
        overlay_descendants: Iterable[tuple[Any, ...]] | None = None,
        deleted_descendants: Iterable[tuple[Any, ...]] | None = None,
    ) -> Any:
        """合成一个有界锚点终态，不把其只追加后代历史复制进来。"""

        if anchor in self._overlays:
            value = copy.deepcopy(self._overlays[anchor])
        else:
            value = copy.deepcopy(self._canonical_value(anchor))
        descendants = sorted(
            overlay_descendants
            if overlay_descendants is not None
            else (
                child
                for child in self._overlays
                if child != anchor and _is_prefix(anchor, child)
            ),
            key=len,
        )
        for child in descendants:
            relative = child[len(anchor) :]
            if child in self._deleted_anchors:
                _delete_relative(value, relative)
            else:
                _set_relative(value, relative, copy.deepcopy(self._overlays[child]))
        deleted = (
            deleted_descendants
            if deleted_descendants is not None
            else (
                child
                for child in self._deleted_anchors
                if child != anchor and _is_prefix(anchor, child)
            )
        )
        for child in sorted(deleted, key=len):
            _delete_relative(value, child[len(anchor) :])
        return value

    def _commit_value(self, op: _StagedOperation) -> Any:
        if op.kind is not None and getattr(op.kind, "value", op.kind) in {
            "append_only_map",
            "append_only_list",
        }:
            if getattr(op.kind, "value", op.kind) == "append_only_map":
                return self._append_maps[op.anchor][op.key]
            return op.value
        if op.operation in {"set", "insert", "append"}:
            try:
                return self._plain_at(op.affected)
            except KeyError:
                return op.value
        return op.value

    def apply_canonical(self, plan: _PreparedStateCommit | None = None) -> list[tuple[Any, ...]]:
        """Apply deterministic staged operations; journal remains untouched."""
        if plan is None:
            plan = self.prepare_for_commit()
        touched: list[tuple[Any, ...]] = []
        for container, operation, key, value in plan.operations:
            target = self.world._persistence_lookup(container)
            if target is None:
                raise KeyError(container)
            self._apply_operation(target, operation, key, value)
            affected = container + (key,) if key is not None else container
            if affected not in touched:
                touched.append(affected)
        return touched

    def operations_for_commit(self) -> list[tuple[tuple[Any, ...], str, Any, Any]]:
        return [
            (op.container, op.operation, op.key, self._commit_value(op))
            for op in self._operations
        ]


class _TxBase:
    __slots__ = ("_tx", "_path")

    def __init__(self, tx: StateTransaction, path: tuple[Any, ...]) -> None:
        self._tx = tx
        self._path = tuple(path)

    def _ensure_live(self) -> None:
        self._tx._ensure_live()

    def _wrap(self, path: tuple[Any, ...], value: Any) -> Any:
        if isinstance(value, (_OverlayMap, _OverlayList)):
            return value
        if isinstance(value, Mapping):
            return _TxDict(self._tx, path)
        if isinstance(value, list):
            return _TxList(self._tx, path)
        return value

    def __deepcopy__(self, memo: dict[int, Any]) -> Any:
        existing = memo.get(id(self))
        if existing is not None:
            return existing
        value = self._tx._plain_at(self._path)
        memo[id(self)] = value
        return value


class _TxDict(_TxBase, MutableMapping):
    def __getitem__(self, key: Any) -> Any:
        value = self._tx._lookup(self._path + (key,))
        return self._wrap(self._path + (key,), value)

    def __setitem__(self, key: Any, value: Any) -> None:
        self._tx.dict_set(self._path, key, value)

    def __delitem__(self, key: Any) -> None:
        self._tx.dict_delete(self._path, key)

    def __iter__(self) -> Iterator[Any]:
        self._ensure_live()
        return iter(self._tx._mapping_keys(self._path))

    def __len__(self) -> int:
        return len(self._tx._mapping_keys(self._path))

    def keys(self):
        self._ensure_live()
        return self._tx._mapping_keys(self._path)

    def values(self):
        self._ensure_live()
        return (self[key] for key in self._tx._mapping_keys(self._path))

    def items(self):
        self._ensure_live()
        return ((key, self[key]) for key in self._tx._mapping_keys(self._path))

    def clear(self) -> None:
        self._tx.dict_clear(self._path)

    def pop(self, key: Any, *default: Any) -> Any:
        if len(default) > 1:
            raise TypeError(f"pop expected at most 2 arguments, got {len(default) + 1}")
        try:
            value = _plain(self[key])
        except KeyError:
            if default:
                return default[0]
            raise
        del self[key]
        return value

    def setdefault(self, key: Any, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            self[key] = default
            return self[key]

    def __repr__(self) -> str:
        return f"TransactionDict({dict(self.items())!r})"


class _TxList(_TxBase, MutableSequence):
    def __getitem__(self, index: Any) -> Any:
        value = self._tx._lookup(self._path)
        if isinstance(index, slice):
            return [self._wrap(self._path + (offset,), item) for offset, item in zip(
                range(*index.indices(len(value))), value[index]
            )]
        item = value[index]
        actual = index if index >= 0 else len(value) + index
        return self._wrap(self._path + (actual,), item)

    def __setitem__(self, index: Any, value: Any) -> None:
        if isinstance(index, slice):
            values = list(value)
            current = list(self)
            current[index] = values
            self._tx._stage(self._path[:-1], "set", self._path[-1], current)
            return
        self._tx.list_set(self._path, index, value)

    def __delitem__(self, index: Any) -> None:
        if isinstance(index, slice):
            current = list(self)
            del current[index]
            self._tx._stage(self._path[:-1], "set", self._path[-1], current)
            return
        self._tx.list_delete(self._path, index)

    def __len__(self) -> int:
        return len(self._tx._lookup(self._path))

    def append(self, value: Any) -> None:
        self._tx.list_append(self._path, value)

    def extend(self, values: Iterable[Any]) -> None:
        for value in list(values):
            self.append(value)

    def insert(self, index: int, value: Any) -> None:
        self._tx.list_insert(self._path, index, value)

    def remove(self, value: Any) -> None:
        self._tx.list_remove(self._path, value)

    def pop(self, index: int = -1) -> Any:
        return self._tx.list_pop(self._path, index)

    def clear(self) -> None:
        self._tx.list_clear(self._path)

    def reverse(self) -> None:
        self._tx.list_reverse(self._path)

    def sort(self, *, key: Any = None, reverse: bool = False) -> None:
        self._tx.list_sort(self._path, key=key, reverse=reverse)

    def __repr__(self) -> str:
        return f"TransactionList({list(self)!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Sequence) and not isinstance(other, (str, bytes, bytearray)):
            return list(self) == list(other)
        return NotImplemented


class _OverlayMap(_TxDict):
    """Virtual append-only map view (canonical history + buffered entries)."""

    def __iter__(self) -> Iterator[Any]:
        self._ensure_live()
        canonical = self._tx._base_append_map(self._path)
        buffered = self._tx._append_maps.get(self._path, {})
        return iter(list(canonical.keys()) + [key for key in buffered if key not in canonical])

    def __len__(self) -> int:
        return len(list(iter(self)))

    def __getitem__(self, key: Any) -> Any:
        self._ensure_live()
        buffered = self._tx._append_maps.get(self._path, {})
        if key in buffered:
            value = buffered[key]
        else:
            value = self._tx._base_append_map(self._path)[key]
        return self._wrap(self._path + (key,), value)

    def keys(self):
        self._ensure_live()
        return list(iter(self))

    def values(self):
        self._ensure_live()
        return (self[key] for key in self)

    def items(self):
        self._ensure_live()
        return ((key, self[key]) for key in self)

    def __setitem__(self, key: Any, value: Any) -> None:
        self._tx.dict_set(self._path, key, value)


class _OverlayList(_TxList):
    """Virtual append-only list view (canonical history + buffered entries)."""

    def __len__(self) -> int:
        canonical = self._tx._base_append_list(self._path)
        return len(canonical) + len(self._tx._append_lists.get(self._path, []))

    def __getitem__(self, index: Any) -> Any:
        self._ensure_live()
        canonical = self._tx._base_append_list(self._path)
        buffered = self._tx._append_lists.get(self._path, [])
        if isinstance(index, slice):
            values = list(canonical) + list(buffered)
            selected = values[index]
            start, _, step = index.indices(len(values))
            return [self._wrap(self._path + (start + offset * step,), value) for offset, value in enumerate(selected)]
        normalized = index if index >= 0 else len(canonical) + len(buffered) + index
        value = canonical[normalized] if normalized < len(canonical) else buffered[normalized - len(canonical)]
        return self._wrap(self._path + (normalized,), value)

    def append(self, value: Any) -> None:
        self._tx.list_append(self._path, value)


def _is_prefix(prefix: tuple[Any, ...], path: tuple[Any, ...]) -> bool:
    return len(prefix) <= len(path) and prefix == path[: len(prefix)]


def _conflict_key(anchor: tuple[Any, ...], kind: Any, key: Any = None) -> Any:
    """Return the smallest optimistic-conflict unit for a persistence kind."""

    if kind == "append_only_map":
        return (anchor, "__append_only_map_id__", key)
    if kind == "append_only_list":
        # 无 ID 事实按持锁提交顺序追加；彼此不覆盖，因此无需把同时开始的
        # 事务判为冲突。
        return None
    return anchor


def _walk(value: Any, path: tuple[Any, ...]) -> Any:
    current = value
    for part in path:
        current = current[part]
    return current


def _set_relative(root: Any, path: tuple[Any, ...], value: Any) -> None:
    if not path:
        raise ValueError("relative state path must not be empty")
    target = _walk(root, path[:-1])
    target[path[-1]] = value


def _delete_relative(root: Any, path: tuple[Any, ...]) -> None:
    if not path:
        raise ValueError("relative state path must not be empty")
    target = _walk(root, path[:-1])
    del target[path[-1]]


__all__ = [
    "StateAccessMode",
    "StateTransactionConflict",
    "ReadOnlyDict",
    "ReadOnlyList",
    "StateTransaction",
]
