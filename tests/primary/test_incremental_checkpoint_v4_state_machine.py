"""Checkpoint v4 的确定性属性/状态机测试。

参考模型只消费状态机实际接受并封存的操作。它不通过保存前后扫描
World 来推导 expected delta，因此可以检验 journal 是唯一变化来源。
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterable

import pytest

from society0.incremental_checkpoint import (
    PersistenceKind,
    StateDeltaJournal,
    V4CheckpointStore,
)


PRICE = ("environment", "state", "price")
FACTS_BY_ID = ("environment", "state", "facts_by_id")
FACTS = ("environment", "state", "facts")
CURSOR = ("environment", "state", "cursor")

DECLARATIONS = {
    PRICE: PersistenceKind.REPLACEABLE,
    FACTS_BY_ID: PersistenceKind.APPEND_ONLY_MAP,
    FACTS: PersistenceKind.APPEND_ONLY_LIST,
    CURSOR: PersistenceKind.TRANSIENT,
}


def _root_entries() -> tuple[dict[str, Any], ...]:
    # 根基点由 bootstrap writer 提供。append-only 容器在根上是空值；后续
    # Tick 只能通过 journal 的 map_create/append 写入。
    return (
        {"path": list(PRICE), "operation": "set", "value": 0, "sequence": 0},
        {"path": list(FACTS_BY_ID), "operation": "set", "value": {}, "sequence": 1},
        {"path": list(FACTS), "operation": "set", "value": [], "sequence": 2},
    )


def _parent(state: dict[str, Any], path: Iterable[str]) -> tuple[dict[str, Any], str]:
    parts = list(path)
    if not parts:
        raise AssertionError("reference operation path cannot be empty")
    current = state
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise AssertionError(f"reference path crosses a non-map: {parts!r}")
        current = child
    return current, parts[-1]


def _apply_reference(state: dict[str, Any], operation: dict[str, Any]) -> None:
    """独立参考模型；不调用 V4CheckpointStore._apply。"""

    parent, key = _parent(state, operation["path"])
    kind = operation["operation"]
    if kind == "set":
        parent[key] = copy.deepcopy(operation["value"])
    elif kind == "delete":
        parent.pop(key, None)
    elif kind == "map_create":
        target = parent.setdefault(key, {})
        if not isinstance(target, dict) or operation["id"] in target:
            raise AssertionError("reference model saw an invalid map append")
        target[operation["id"]] = copy.deepcopy(operation["value"])
    elif kind == "append":
        target = parent.setdefault(key, [])
        if not isinstance(target, list):
            raise AssertionError("reference model saw an invalid list append")
        target.append(copy.deepcopy(operation["value"]))
    else:
        raise AssertionError(f"unknown reference operation: {kind!r}")


def _apply_delta(state: dict[str, Any], delta: Any) -> None:
    operations = [*delta.replacements, *delta.appends]
    for operation in sorted(operations, key=lambda item: item["sequence"]):
        _apply_reference(state, _plain(dict(operation)))


def _plain(value: Any) -> Any:
    """将 sealed delta 的递归只读容器转成参考模型可复制的 JSON 值。"""

    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _journal_for(state: dict[str, Any]) -> StateDeltaJournal:
    journal = StateDeltaJournal(DECLARATIONS)
    # 绑定的是参考状态本身。它只用于 append-only map 的 membership 校验，
    # 不会在 publish 时被读取来发现变化。
    journal.bind_canonical_state(state)
    return journal


def test_deterministic_multitick_state_machine_restores_every_complete_tick(tmp_path):
    """固定随机种子覆盖 set/delete/map_create/list append/seal/abort。"""

    import random

    rng = random.Random(20260811)
    store = V4CheckpointStore(tmp_path)
    reference: dict[str, Any] = {
        "environment": {"state": {"price": 0, "facts_by_id": {}, "facts": []}}
    }
    store.publish_root(_root_entries(), metadata={"run_id": "state-machine"})
    journal = _journal_for(reference)
    snapshots: dict[int, dict[str, Any]] = {0: copy.deepcopy(reference)}
    complete_steps = [0]
    known_ids: list[str] = []
    duplicate_errors = 0
    aborted_steps: list[int] = []

    # 64 个 Tick 足以让父链和 append-only 历史增长，同时保持 primary suite
    # 的运行时间稳定。每个 Tick 的操作顺序由固定种子决定。
    for step in range(1, 65):
        journal.begin_tick(step)

        # 明确覆盖跨 Tick 重复 ID；错误发生在 canonical state 改动之前。
        if known_ids and step % 5 == 0:
            with pytest.raises(ValueError, match="duplicate append-only map id"):
                journal.record_map_create(
                    FACTS_BY_ID,
                    known_ids[0],
                    {"step": step, "duplicate": True},
                )
            duplicate_errors += 1

        for index in range(7):
            choice = rng.randrange(5)
            if choice == 0:
                journal.record_set(PRICE, step * 100 + index)
            elif choice == 1:
                journal.record_delete(PRICE)
            elif choice == 2:
                fact_id = f"fact-{step:03d}-{index}"
                journal.record_map_create(
                    FACTS_BY_ID,
                    fact_id,
                    {"step": step, "index": index, "payload": "v" * (index + 1)},
                )
                known_ids.append(fact_id)
            elif choice == 3:
                journal.record_append(
                    FACTS,
                    {"step": step, "index": index, "token": rng.randrange(1_000_000)},
                )
            else:
                # transient 写入可以发生，但永远不进入 delta 或恢复结果。
                journal.record_set(CURSOR, step * 10 + index)

        # 每 7 个 Tick 取消一次；取消前已记录的 runtime delta 必须完全丢弃。
        if step % 7 == 0:
            journal.abort_tick()
            aborted_steps.append(step)
            assert store.available_steps() == complete_steps
            continue

        delta = journal.seal_tick()
        _apply_delta(reference, delta)
        store.publish(delta)
        complete_steps.append(step)
        snapshots[step] = copy.deepcopy(reference)

        # 每个已发布 Tick 都直接恢复并与独立模型比较；这也覆盖了
        # replacement 与 append 在同一 sequence 域内交错的情况。
        assert store.restore(step) == reference
        assert "cursor" not in reference["environment"]["state"]

        # 随机抽查较早的完整 Tick，确保后续 replacement 没有回写历史视图。
        earlier = rng.choice(complete_steps)
        assert store.restore(earlier) == snapshots[earlier]

    assert duplicate_errors >= 10
    assert aborted_steps
    assert store.available_steps() == complete_steps
    for step in complete_steps:
        assert store.restore(step) == snapshots[step]
    for step in aborted_steps:
        with pytest.raises(FileNotFoundError):
            store.restore(step)


def test_duplicate_id_and_failed_marker_are_not_visible(tmp_path, monkeypatch):
    """重复事实写入前失败；marker 原子发布前失败时目标 Tick 不可见。"""

    store = V4CheckpointStore(tmp_path)
    store.publish_root(_root_entries(), metadata={"run_id": "failure-marker"})
    journal = _journal_for(
        {"environment": {"state": {"price": 0, "facts_by_id": {}, "facts": []}}}
    )

    journal.begin_tick(1)
    journal.record_map_create(FACTS_BY_ID, "once", {"value": 1})
    with pytest.raises(ValueError, match="duplicate append-only map id"):
        journal.record_map_create(FACTS_BY_ID, "once", {"value": 2})
    delta_one = journal.seal_tick()
    store.publish(delta_one)
    visible_before_failure = store.restore(1)

    journal.begin_tick(2)
    journal.record_set(PRICE, 99)
    delta_two = journal.seal_tick()
    original_write = store._atomic_write

    def fail_complete_marker(path: Path, data: bytes) -> int:
        if path.parent == store.complete_dir:
            raise OSError("injected marker failure")
        return original_write(path, data)

    monkeypatch.setattr(store, "_atomic_write", fail_complete_marker)
    with pytest.raises(OSError, match="marker failure"):
        store.publish(delta_two)

    assert store.available_steps() == [0, 1]
    assert store.restore(1) == visible_before_failure
    assert not (store.complete_dir / "step_000002.json").exists()
    with pytest.raises(FileNotFoundError):
        store.restore(2)
