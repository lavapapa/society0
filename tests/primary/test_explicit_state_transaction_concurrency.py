"""显式状态事务的 asyncio 并发、重试和提交故障语义。"""

from __future__ import annotations

import asyncio
import contextvars
import copy
from pathlib import Path
from typing import Any

import pytest

from society0 import (
    StateAccessMode,
    StateTransactionConflict,
    append_only_map,
    persistent_state_schema,
    replaceable,
    replaceable_map,
)
from society0.core_data import World
from society0.incremental_checkpoint import PersistenceSchema


ROOT = ("environment", "state")


def _record_schema() -> dict[str, Any]:
    return persistent_state_schema(
        entities=replaceable_map(
            entry_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "value": replaceable(schema={"type": "integer"}),
                },
            }
        )
    )


def _append_schema() -> dict[str, Any]:
    return persistent_state_schema(
        facts=append_only_map(
            entry_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"value": {"type": "integer"}},
            }
        )
    )


def _scalar_schema() -> dict[str, Any]:
    return persistent_state_schema(
        value=replaceable(schema={"type": "integer"}),
    )


def _two_scalar_schema() -> dict[str, Any]:
    return persistent_state_schema(
        left=replaceable(schema={"type": "integer"}),
        right=replaceable(schema={"type": "integer"}),
    )


def _world(tmp_path: Path, schema: dict[str, Any], state: dict[str, Any]) -> World:
    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    world.environment_data["state"] = copy.deepcopy(state)
    world.configure_persistence(PersistenceSchema.compile(schema, root_path=ROOT))
    world.begin_persistence_tick(1)
    return world


def _close_world(world: World) -> None:
    journal = getattr(world, "_state_delta_journal", None)
    if journal is not None and getattr(journal, "active_step", None) is not None:
        world.abort_persistence_tick()
    world.event_logger.close()


@pytest.mark.asyncio
async def test_async_tasks_conflict_on_one_replaceable_record(tmp_path: Path) -> None:
    """两个真实 asyncio task 可并发准备，但同一记录只有一个提交者。"""

    world = _world(
        tmp_path,
        _record_schema(),
        {"entities": {"a": {"value": 0}}},
    )
    barrier = asyncio.Barrier(2)

    async def action(value: int) -> str:
        try:
            with world.write_environment_transaction() as tx:
                tx.state["entities"]["a"]["value"] = value
                await barrier.wait()
        except StateTransactionConflict:
            return "conflict"
        return "committed"

    try:
        results = await asyncio.gather(action(1), action(2))
        assert sorted(results) == ["committed", "conflict"]
        winner = world.environment_data["state"]["entities"]["a"]["value"]
        assert winner in {1, 2}
        delta = world.seal_persistence_tick()
        assert len(delta.replacements) == 1
        assert delta.replacements[0]["path"] == [
            *ROOT,
            "entities",
            "a",
            "value",
        ]
        assert delta.replacements[0]["value"] == winner
    finally:
        _close_world(world)


@pytest.mark.asyncio
async def test_async_tasks_with_distinct_append_ids_both_commit(tmp_path: Path) -> None:
    """append-only map 的冲突单位是 (anchor, id)，不同 ID 不应互相阻塞。"""

    world = _world(tmp_path, _append_schema(), {"facts": {}})
    barrier = asyncio.Barrier(2)

    async def action(fact_id: str, value: int) -> str:
        with world.write_environment_transaction() as tx:
            tx.state["facts"][fact_id] = {"value": value}
            await barrier.wait()
        return fact_id

    try:
        results = await asyncio.gather(
            action("fact-a", 1),
            action("fact-b", 2),
        )
        assert set(results) == {"fact-a", "fact-b"}
        assert world.environment_data["state"]["facts"] == {
            "fact-a": {"value": 1},
            "fact-b": {"value": 2},
        }
        delta = world.seal_persistence_tick()
        assert {item["id"] for item in delta.appends} == {"fact-a", "fact-b"}
    finally:
        _close_world(world)


@pytest.mark.asyncio
async def test_action_style_retry_uses_a_fresh_transaction_after_conflict(
    tmp_path: Path,
) -> None:
    """冲突后的 action 重试必须丢弃旧事务，再从新 canonical 版本开始。"""

    world = _world(tmp_path, _scalar_schema(), {"value": 0})
    first_attempt_staged = asyncio.Event()
    allow_first_attempt_commit = asyncio.Event()

    async def winner_action() -> None:
        await first_attempt_staged.wait()
        with world.write_environment_transaction() as tx:
            tx.state["value"] = 10
        allow_first_attempt_commit.set()

    async def retrying_action() -> int:
        attempts = 0
        while True:
            attempts += 1
            try:
                with world.write_environment_transaction() as tx:
                    tx.state["value"] = 20 if attempts == 1 else 30
                    if attempts == 1:
                        first_attempt_staged.set()
                        await allow_first_attempt_commit.wait()
                return attempts
            except StateTransactionConflict:
                if attempts >= 2:
                    raise

    try:
        attempts, _ = await asyncio.gather(
            retrying_action(),
            winner_action(),
        )
        assert attempts == 2
        assert world.environment_data["state"]["value"] == 30
        delta = world.seal_persistence_tick()
        assert len(delta.replacements) == 1
        assert delta.replacements[0]["value"] == 30
    finally:
        _close_world(world)


def test_partial_journal_commit_rolls_back_canonical_and_journal_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """注入 journal 半提交故障，canonical 与 journal 都应保持原状。"""

    world = _world(tmp_path, _two_scalar_schema(), {"left": 1, "right": 2})
    journal = world._state_delta_journal
    assert journal is not None
    original_write = journal._commit_proxy_write
    write_count = 0

    def fail_on_second_token(token: Any) -> None:
        nonlocal write_count
        write_count += 1
        if write_count == 2:
            raise RuntimeError("injected partial journal commit failure")
        original_write(token)

    monkeypatch.setattr(journal, "_commit_proxy_write", fail_on_second_token)
    try:
        with pytest.raises(RuntimeError, match="partial journal"):
            with world.write_environment_transaction() as tx:
                tx.state["left"] = 3
                tx.state["right"] = 4
        assert world.environment_data["state"] == {"left": 1, "right": 2}
        assert journal._replacements == {}
        assert journal._appends == []
    finally:
        _close_world(world)


def test_cross_context_commit_does_not_poison_origin_context(tmp_path: Path) -> None:
    """在另一个 context 提交后，原 context 应可开启下一笔事务。"""

    world = _world(tmp_path, _scalar_schema(), {"value": 1})
    origin = contextvars.copy_context()
    tx = world.write_environment_transaction()
    try:
        origin.run(tx.__enter__)
        tx.state["value"] = 2
        tx.commit()

        next_tx = world.write_environment_transaction()
        origin.run(next_tx.__enter__)
        next_tx.state["value"] = 3
        next_tx.commit()
        assert world.environment_data["state"]["value"] == 3
    finally:
        _close_world(world)


def test_abort_invalidates_transaction_and_allows_a_fresh_next_tick(
    tmp_path: Path,
) -> None:
    """abort 既丢弃未提交事务，也必须允许下一 Tick 重新写入。"""

    world = _world(tmp_path, _scalar_schema(), {"value": 1})
    tx = world.write_environment_transaction()
    tx.__enter__()
    try:
        tx.state["value"] = 2
        with pytest.raises(RuntimeError, match="open state transactions"):
            world.seal_persistence_tick()

        world.abort_persistence_tick()
        assert world.environment_data["state"] == {"value": 1}
        assert not tx.active
        with pytest.raises(RuntimeError, match="invalidated|expired"):
            _ = tx.state

        world.begin_persistence_tick(2)
        with world.write_environment_transaction() as fresh:
            fresh.state["value"] = 3
        delta = world.seal_persistence_tick()
        assert world.environment_data["state"] == {"value": 3}
        assert [item["value"] for item in delta.replacements] == [3]
    finally:
        _close_world(world)


def test_existing_tick_journal_survives_a_later_partial_commit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """后续事务的 journal 半提交失败不能清掉本 Tick 已有增量。"""

    world = _world(tmp_path, _two_scalar_schema(), {"left": 1, "right": 2})
    journal = world._state_delta_journal
    assert journal is not None
    try:
        with world.write_environment_transaction() as tx:
            tx.state["left"] = 10
        previous = copy.deepcopy(journal._replacements)
        original_write = journal._commit_proxy_write
        write_count = 0

        def fail_on_second_token(token: Any) -> None:
            nonlocal write_count
            write_count += 1
            if write_count == 2:
                raise RuntimeError("injected later partial journal failure")
            original_write(token)

        monkeypatch.setattr(journal, "_commit_proxy_write", fail_on_second_token)
        with pytest.raises(RuntimeError, match="later partial"):
            with world.write_environment_transaction() as tx:
                tx.state["left"] = 11
                tx.state["right"] = 22

        assert world.environment_data["state"] == {"left": 10, "right": 2}
        assert journal._replacements == previous
        delta = world.seal_persistence_tick()
        assert [item["value"] for item in delta.replacements] == [10]
    finally:
        _close_world(world)


@pytest.mark.asyncio
async def test_async_append_id_conflict_retries_with_a_new_id(tmp_path: Path) -> None:
    """真实 asyncio action 的 append ID 冲突重试必须新开事务和新 ID。"""

    world = _world(tmp_path, _append_schema(), {"facts": {}})
    loser_staged = asyncio.Event()
    winner_done = asyncio.Event()

    async def loser_action() -> str:
        try:
            with world.write_environment_transaction() as tx:
                tx.state["facts"]["same"] = {"value": 2}
                loser_staged.set()
                await winner_done.wait()
        except StateTransactionConflict:
            with world.write_environment_transaction() as retry:
                retry.state["facts"]["retry"] = {"value": 3}
            return "retried"
        return "unexpectedly-committed"

    async def winner_action() -> None:
        await loser_staged.wait()
        with world.write_environment_transaction() as tx:
            tx.state["facts"]["same"] = {"value": 1}
        winner_done.set()

    try:
        result, _ = await asyncio.gather(loser_action(), winner_action())
        assert result == "retried"
        assert world.environment_data["state"]["facts"] == {
            "same": {"value": 1},
            "retry": {"value": 3},
        }
        delta = world.seal_persistence_tick()
        assert [item["id"] for item in delta.appends] == ["same", "retry"]
    finally:
        _close_world(world)


def test_buffered_append_map_create_delete_is_invisible_and_recreatable(
    tmp_path: Path,
) -> None:
    """同一 map 视图上 create→delete 后可再次用同 ID 创建。"""

    world = _world(tmp_path, _append_schema(), {"facts": {}})
    try:
        with world.write_environment_transaction() as tx:
            facts = tx.state["facts"]
            facts["pending"] = {"value": 1}
            assert facts["pending"]["value"] == 1
            del facts["pending"]

            assert "pending" not in facts
            with pytest.raises(KeyError):
                _ = facts["pending"]
            assert tx._operations == []
            assert tx._base_versions == {}
            assert tx._append_ids == set()
            assert tx._append_maps == {}

            facts["pending"] = {"value": 2}
            assert facts["pending"]["value"] == 2

        assert world.environment_data["state"] == {
            "facts": {"pending": {"value": 2}},
        }
        delta = world.seal_persistence_tick()
        assert len(delta.appends) == 1
        assert delta.appends[0]["id"] == "pending"
        assert delta.appends[0]["value"] == {"value": 2}
    finally:
        _close_world(world)


def test_buffered_append_map_create_pop_is_invisible_and_leaves_empty_delta(
    tmp_path: Path,
) -> None:
    """同一 map 视图上 create→pop 后 RYW 不可见且不产生 append。"""

    world = _world(tmp_path, _append_schema(), {"facts": {}})
    try:
        with world.write_environment_transaction() as tx:
            facts = tx.state["facts"]
            facts["pending"] = {"value": 1}
            popped = facts.pop("pending")
            assert popped == {"value": 1}
            assert "pending" not in facts
            with pytest.raises(KeyError):
                _ = facts["pending"]
            assert tx._operations == []
            assert tx._base_versions == {}

        assert world.environment_data["state"] == {"facts": {}}
        delta = world.seal_persistence_tick()
        assert delta.appends == ()
        assert delta.replacements == ()
    finally:
        _close_world(world)


def test_existing_append_map_entry_delete_remains_rejected(tmp_path: Path) -> None:
    """canonical 已有 append-only ID 仍不可删除。"""

    world = _world(
        tmp_path,
        _append_schema(),
        {"facts": {"existing": {"value": 1}}},
    )
    try:
        with world.write_environment_transaction() as tx:
            facts = tx.state["facts"]
            with pytest.raises(ValueError, match="immutable"):
                del facts["existing"]
            with pytest.raises(ValueError, match="immutable"):
                facts.pop("existing")
            assert facts["existing"]["value"] == 1

        assert world.environment_data["state"] == {
            "facts": {"existing": {"value": 1}},
        }
        delta = world.seal_persistence_tick()
        assert delta.appends == ()
        assert delta.replacements == ()
    finally:
        _close_world(world)


def test_transparent_proxy_mode_rejects_explicit_transaction_same_tick(
    tmp_path: Path,
) -> None:
    """旧 DictProxy 合同与显式事务入口不能在同一 World 混用。"""

    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["state"] = {"value": 1}
    world.configure_persistence(
        PersistenceSchema.compile(_scalar_schema(), root_path=ROOT)
    )
    world.begin_persistence_tick(1)
    try:
        proxy = world.create_environment_state_proxy()
        with pytest.raises(RuntimeError, match="explicit_transactions"):
            world.write_environment_transaction()
        proxy["value"] = 2
        delta = world.seal_persistence_tick()
        assert world.environment_data["state"] == {"value": 2}
        assert [item["value"] for item in delta.replacements] == [2]
    finally:
        _close_world(world)
