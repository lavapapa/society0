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
    append_only_list,
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


def _mixed_dynamic_schema() -> dict[str, Any]:
    account_schema = {
        "type": "object",
        "properties": {
            "journal": append_only_map(),
        },
        "additionalProperties": True,
    }
    return persistent_state_schema(
        accounts=replaceable_map(entry_schema=account_schema)
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


def test_mutation_anchor_fast_path_preserves_canonical_and_overlay_reads(
    tmp_path: Path,
) -> None:
    """修改一个顶层记录不应遮蔽另一个记录，根 overlay 仍须支持 RYW。"""

    world = _world(
        tmp_path,
        _two_scalar_schema(),
        {"left": 1, "right": 2},
    )
    try:
        with world.write_environment_transaction() as tx:
            tx.state["left"] = 3
            assert tx.state["right"] == 2
            tx.state["right"] = 4
            assert tx.state["right"] == 4
        assert world.environment_data["state"] == {"left": 3, "right": 4}
    finally:
        _close_world(world)

    no_journal_world = World(
        event_log_path=str(tmp_path / "no-journal-events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    no_journal_world.environment_data["state"] = {
        "left": {"value": 1},
        "right": {"value": 2},
    }
    try:
        with no_journal_world.write_environment_transaction() as tx:
            tx.state["left"] = {"value": 3}
            assert tx.state["left"]["value"] == 3
            assert tx.state["right"]["value"] == 2
            tx.state["right"] = {"value": 4}
            assert tx.state["right"]["value"] == 4
    finally:
        no_journal_world.event_logger.close()


def test_mixed_dynamic_field_delete_preserves_nested_history(tmp_path: Path) -> None:
    """开放 mixed entity 的动态字段可删除，历史事实仍保持原样。"""

    world = _world(
        tmp_path,
        _mixed_dynamic_schema(),
        {
            "accounts": {
                "a": {
                    "journal": {"old": {"amount": 1}},
                    "label": "before",
                }
            }
        },
    )
    try:
        with world.write_environment_transaction() as tx:
            del tx.state["accounts"]["a"]["label"]
        assert world.environment_data["state"] == {
            "accounts": {"a": {"journal": {"old": {"amount": 1}}}}
        }
        delta = world.seal_persistence_tick()
        assert delta.replacements == (
            {
                "path": [*ROOT, "accounts", "a", "label"],
                "operation": "delete",
                "sequence": 0,
            },
        )
        assert delta.appends == ()
    finally:
        _close_world(world)


def test_mixed_dynamic_field_delete_exception_leaves_canonical_and_journal_clean(
    tmp_path: Path,
) -> None:
    """动态字段删除在事务体异常时不应留下 canonical/journal 改动。"""

    original = {
        "accounts": {
            "a": {
                "journal": {"old": {"amount": 1}},
                "label": "before",
            }
        }
    }
    world = _world(tmp_path, _mixed_dynamic_schema(), original)
    journal = world._state_delta_journal
    assert journal is not None
    try:
        with pytest.raises(RuntimeError, match="business failure"):
            with world.write_environment_transaction() as tx:
                del tx.state["accounts"]["a"]["label"]
                raise RuntimeError("business failure")
        assert world.environment_data["state"] == original
        assert journal._replacements == {}
        assert journal._appends == []
        assert world.seal_persistence_tick().replacements == ()
    finally:
        _close_world(world)


def test_mixed_dynamic_field_delete_conflict_does_not_append_journal_delta(
    tmp_path: Path,
) -> None:
    """动态字段删除冲突时，失败事务不能污染已有 journal。"""

    world = _world(
        tmp_path,
        _mixed_dynamic_schema(),
        {
            "accounts": {
                "a": {
                    "journal": {"old": {"amount": 1}},
                    "label": "before",
                }
            }
        },
    )
    first = world.write_environment_transaction()
    second = world.write_environment_transaction()
    first_context = contextvars.Context()
    second_context = contextvars.Context()
    first_context.run(first.__enter__)
    second_context.run(second.__enter__)
    try:
        first_context.run(
            lambda: first.state["accounts"]["a"].__delitem__("label")
        )
        second_context.run(
            lambda: second.state["accounts"]["a"].__delitem__("label")
        )
        first.commit()
        with pytest.raises(StateTransactionConflict):
            second.commit()
        assert world.environment_data["state"] == {
            "accounts": {"a": {"journal": {"old": {"amount": 1}}}}
        }
        delta = world.seal_persistence_tick()
        assert delta.replacements == (
            {
                "path": [*ROOT, "accounts", "a", "label"],
                "operation": "delete",
                "sequence": 0,
            },
        )
        assert delta.appends == ()
    finally:
        _close_world(world)


def test_transaction_deepcopy_reflects_mixed_append_and_delete(tmp_path: Path) -> None:
    """根/子树 deepcopy 必须合成事务 overlay、追加缓冲和删除结果。"""

    account_schema = {
        "type": "object",
        "properties": {
            "balance": replaceable(schema={"type": "number"}),
            "journal": append_only_map(),
            "history": append_only_list(),
        },
        "additionalProperties": True,
    }
    schema = persistent_state_schema(
        accounts=replaceable_map(entry_schema=account_schema)
    )
    world = _world(
        tmp_path,
        schema,
        {
            "accounts": {
                "a": {
                    "balance": 10,
                    "journal": {"old": {"amount": 1}},
                    "history": ["old"],
                }
            }
        },
    )
    try:
        with world.write_environment_transaction() as tx:
            account = tx.state["accounts"]["a"]
            del account["balance"]
            account["journal"]["new"] = {"amount": 2}
            account["history"].append("new")

            expected = {
                "accounts": {
                    "a": {
                        "journal": {
                            "old": {"amount": 1},
                            "new": {"amount": 2},
                        },
                        "history": ["old", "new"],
                    }
                }
            }
            assert copy.deepcopy(tx.state) == expected
            assert copy.deepcopy(tx.state["accounts"]) == expected["accounts"]

        assert world.environment_data["state"] == expected
        delta = world.seal_persistence_tick()
        assert [tuple(item["path"]) for item in delta.replacements] == [
            (*ROOT, "accounts", "a", "balance"),
        ]
        assert delta.replacements[0]["operation"] == "delete"
        assert [tuple(item["path"]) for item in delta.appends] == [
            (*ROOT, "accounts", "a", "journal"),
            (*ROOT, "accounts", "a", "history"),
        ]
    finally:
        _close_world(world)


def test_explicit_transaction_without_journal_commits_and_blocks_root_replace(
    tmp_path: Path,
) -> None:
    """无 journal 的显式事务仍可提交，活动事务期间不能整体替换根状态。"""

    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    world.environment_data["state"] = {"value": 1}
    environment = world.get_environment()
    try:
        with world.write_environment_transaction() as tx:
            tx.state["value"] = 2
            with pytest.raises(RuntimeError, match="active .*transaction"):
                environment.state = {"value": 100}
        assert world.environment_data["state"] == {"value": 2}

        environment.state = {"value": 3}
        assert world.environment_data["state"] == {"value": 3}
    finally:
        world.event_logger.close()


def test_no_journal_root_replace_sees_transaction_from_other_context(
    tmp_path: Path,
) -> None:
    """根替换检查必须覆盖不在当前 context 中的活动事务。"""

    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    world.environment_data["state"] = {"value": 1}
    environment = world.get_environment()
    transaction = world.write_environment_transaction()
    owner_context = contextvars.Context()
    owner_context.run(transaction.__enter__)
    try:
        with pytest.raises(RuntimeError, match="active .*transaction"):
            environment.state = {"value": 100}
        owner_context.run(transaction.rollback)
        assert world.environment_data["state"] == {"value": 1}
    finally:
        if transaction.active:
            owner_context.run(transaction.rollback)
        world.event_logger.close()


def test_unvalidated_dry_token_cannot_be_committed_directly(tmp_path: Path) -> None:
    """事务内部的 dry token 不得绕过 journal 的最终值校验。"""

    world = _world(tmp_path, _scalar_schema(), {"value": 1})
    journal = world._state_delta_journal
    assert journal is not None
    try:
        token = journal.prepare_proxy_operation(
            ROOT,
            "set",
            "value",
            "invalid",
            validate_value=False,
        )
        with pytest.raises(RuntimeError, match="unvalidated"):
            journal.commit_proxy_operation(token)
        assert world.environment_data["state"] == {"value": 1}
        assert journal._replacements == {}
        assert journal._appends == []
        delta = world.seal_persistence_tick()
        assert delta.replacements == ()
        assert delta.appends == ()
    finally:
        _close_world(world)


def _list_pop_schema() -> dict[str, Any]:
    return persistent_state_schema(
        items=replaceable(
            schema={
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"value": {"type": "integer"}},
                },
            }
        )
    )


def test_list_pop_detaches_result_before_rollback(tmp_path: Path) -> None:
    """pop 返回值脱离 canonical 后，事务异常不能泄漏原始可变对象。"""

    world = _world(
        tmp_path,
        _list_pop_schema(),
        {"items": [{"value": 1}, {"value": 2}]},
    )
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with world.write_environment_transaction() as tx:
                popped = tx.state["items"].pop(0)
                assert popped == {"value": 1}
                popped["value"] = 99
                raise RuntimeError("rollback")
        assert world.environment_data["state"] == {
            "items": [{"value": 1}, {"value": 2}]
        }
        delta = world.seal_persistence_tick()
        assert delta.replacements == ()
    finally:
        _close_world(world)


def test_list_pop_commit_does_not_retain_mutated_return_alias(tmp_path: Path) -> None:
    """pop 提交后返回值的后续修改不能改写已提交列表。"""

    world = _world(
        tmp_path,
        _list_pop_schema(),
        {"items": [{"value": 1}, {"value": 2}]},
    )
    try:
        with world.write_environment_transaction() as tx:
            popped = tx.state["items"].pop(0)
            popped["value"] = 99

        assert world.environment_data["state"] == {
            "items": [{"value": 2}]
        }
        delta = world.seal_persistence_tick()
        assert delta.replacements == (
            {
                "path": [*ROOT, "items"],
                "operation": "set",
                "value": [{"value": 2}],
                "sequence": 0,
            },
        )
    finally:
        _close_world(world)


def test_list_pop_detaches_nested_list_result(tmp_path: Path) -> None:
    """pop 返回列表值时，列表本身及其后续修改也不能越过事务隔离。"""

    schema = persistent_state_schema(
        items=replaceable(
            schema={
                "type": "array",
                "items": {"type": "array", "items": {"type": "integer"}},
            }
        )
    )
    world = _world(tmp_path, schema, {"items": [[1, 2], [3]]})
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with world.write_environment_transaction() as tx:
                popped = tx.state["items"].pop(0)
                assert popped == [1, 2]
                popped.append(99)
                assert popped == [1, 2, 99]
                raise RuntimeError("rollback")
        assert world.environment_data["state"] == {"items": [[1, 2], [3]]}
        assert world.seal_persistence_tick().replacements == ()
    finally:
        _close_world(world)


def test_popitem_returns_detached_value_after_delete_and_rollback(tmp_path: Path) -> None:
    """popitem 返回值在删除后仍可读写，回滚不污染 canonical。"""

    schema = persistent_state_schema(entities=replaceable_map())
    world = _world(
        tmp_path,
        schema,
        {"entities": {"a": {"value": 1}, "b": {"value": 2}}},
    )
    try:
        with pytest.raises(RuntimeError, match="rollback"):
            with world.write_environment_transaction() as tx:
                key, popped = tx.state["entities"].popitem()
                assert key == "b"
                assert popped == {"value": 2}
                popped["value"] = 99
                assert popped == {"value": 99}
                raise RuntimeError("rollback")
        assert world.environment_data["state"] == {
            "entities": {"a": {"value": 1}, "b": {"value": 2}}
        }
        assert world.seal_persistence_tick().replacements == ()
    finally:
        _close_world(world)


def test_popitem_commit_does_not_retain_mutated_return_alias(tmp_path: Path) -> None:
    """popitem 提交后返回值的后续修改不能恢复已删除记录。"""

    schema = persistent_state_schema(entities=replaceable_map())
    world = _world(
        tmp_path,
        schema,
        {"entities": {"a": {"value": 1}, "b": {"value": 2}}},
    )
    try:
        with world.write_environment_transaction() as tx:
            key, popped = tx.state["entities"].popitem()
            assert key == "b"
            popped["value"] = 99

        assert world.environment_data["state"] == {
            "entities": {"a": {"value": 1}}
        }
        delta = world.seal_persistence_tick()
        assert delta.replacements == (
            {
                "path": [*ROOT, "entities", "b"],
                "operation": "delete",
                "sequence": 0,
            },
        )
    finally:
        _close_world(world)


def test_append_only_map_popitem_rejects_canonical_entry_delete(tmp_path: Path) -> None:
    """append-only map 的 canonical ID 不能通过 popitem 删除。"""

    world = _world(
        tmp_path,
        _append_schema(),
        {"facts": {"existing": {"value": 1}}},
    )
    try:
        with world.write_environment_transaction() as tx:
            with pytest.raises(ValueError, match="immutable"):
                tx.state["facts"].popitem()
        assert world.environment_data["state"] == {
            "facts": {"existing": {"value": 1}}
        }
        delta = world.seal_persistence_tick()
        assert delta.replacements == ()
        assert delta.appends == ()
    finally:
        _close_world(world)
