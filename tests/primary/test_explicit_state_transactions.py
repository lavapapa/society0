"""显式状态事务的核心语义。"""

from __future__ import annotations

import contextvars
import copy

import pytest

from society0 import (
    Environment,
    Society0,
    StateAccessMode,
    append_only_list,
    append_only_map,
    persistent_state_schema,
    replaceable,
    replaceable_map,
    transient,
)
from society0.decorators import env_type
from society0.core_data import World
from society0.incremental_checkpoint import PersistenceSchema
from society0.state_proxy import DictProxy
from society0.state_transactions import ReadOnlyDict


ROOT = ("environment", "state")


@env_type(
    type_name="explicit_transaction_test",
    config_schema={"type": "object", "additionalProperties": False, "properties": {}},
    state_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "counter": {"type": "integer", "persistence": {"kind": "replaceable"}},
            "events": {
                "type": "array",
                "items": {"type": "string"},
                "persistence": {"kind": "append_only_list"},
            },
        },
    },
    agent_managed_fields_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
)
class ExplicitTransactionEnvironment(Environment):
    def after_tick(self, ctx):
        with self.write_transaction() as tx:
            tx.state["events"].append(f"after:{ctx.step}")


def _schema() -> dict:
    return persistent_state_schema(
        clock=replaceable(),
        entities=replaceable_map(),
        facts=append_only_map(),
        audit=append_only_list(),
        runtime=transient(default={}),
    )


def _state() -> dict:
    return {
        "clock": {"day": 1, "status": "open"},
        "entities": {
            "a": {"qty": 4, "tags": ["old"]},
            "untouched": {"qty": 99, "tags": []},
        },
        "facts": {"old": {"kind": "bootstrap"}},
        "audit": [{"event": "bootstrap"}],
        "runtime": {"cursor": 0},
    }


def _world(tmp_path, *, explicit: bool = True) -> World:
    mode = (
        StateAccessMode.EXPLICIT_TRANSACTIONS
        if explicit
        else StateAccessMode.TRANSPARENT_PROXY
    )
    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=mode,
    )
    world.environment_data["state"] = copy.deepcopy(_state())
    world.configure_persistence(PersistenceSchema.compile(_schema(), root_path=ROOT))
    world.begin_persistence_tick(1)
    return world


def test_existing_proxy_mode_remains_the_default(tmp_path):
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["state"] = {"clock": {"day": 1}}
    world.configure_persistence(
        PersistenceSchema.compile(
            persistent_state_schema(clock=replaceable()), root_path=ROOT
        )
    )
    world.begin_persistence_tick(1)
    try:
        state = world.create_environment_state_proxy()
        assert isinstance(state, DictProxy)
        state["clock"]["day"] = 2
        delta = world.seal_persistence_tick()
        assert delta.replacements[0]["value"]["day"] == 2
    finally:
        world.event_logger.close()


def test_explicit_mode_reads_are_recursive_read_only_views(tmp_path):
    world = _world(tmp_path)
    try:
        state = world.create_environment_state_proxy()
        assert isinstance(state, ReadOnlyDict)
        assert state["entities"]["a"]["qty"] == 4
        assert state["entities"]["a"]["tags"][0] == "old"
        with pytest.raises(TypeError, match="write_transaction"):
            state["clock"]["day"] = 2
        with pytest.raises(TypeError, match="write_transaction"):
            state["audit"].append({"event": "bad"})
        assert world.environment_data["state"] == _state()
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_public_state_resolves_to_the_active_explicit_transaction(tmp_path):
    world = _world(tmp_path)
    environment = world.get_environment()
    try:
        outside = environment.state
        with environment.write_transaction() as tx:
            assert environment.state["clock"]["day"] == 1
            environment.state["clock"]["day"] = 2
            assert tx.state["clock"]["day"] == 2
        assert world.environment_data["state"]["clock"]["day"] == 2
        assert outside["clock"]["day"] == 2
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_transaction_reads_its_writes_and_commits_bounded_deltas(tmp_path):
    world = _world(tmp_path)
    try:
        with world.write_environment_transaction() as tx:
            state = tx.state
            state["clock"]["day"] = 2
            state["entities"]["a"]["qty"] = 3
            state["entities"]["a"]["tags"].append("new")
            state["entities"]["created"] = {"qty": 7, "tags": []}
            del state["entities"]["untouched"]
            state["facts"]["f-1"] = {"kind": "trade", "qty": 1}
            state["audit"].append({"event": "commit"})
            state["runtime"]["cursor"] = 5

            assert state["clock"]["day"] == 2
            assert state["entities"]["a"]["tags"] == ["old", "new"]
            assert set(state["entities"]) == {"a", "created"}
            assert state["facts"]["f-1"]["kind"] == "trade"
            assert state["audit"][-1]["event"] == "commit"
            assert world.environment_data["state"] == _state()

        assert world.environment_data["state"]["clock"]["day"] == 2
        assert world.environment_data["state"]["entities"] == {
            "a": {"qty": 3, "tags": ["old", "new"]},
            "created": {"qty": 7, "tags": []},
        }
        assert world.environment_data["state"]["runtime"]["cursor"] == 5

        delta = world.seal_persistence_tick()
        replacement_paths = {tuple(item["path"]) for item in delta.replacements}
        assert replacement_paths == {
            (*ROOT, "clock"),
            (*ROOT, "entities", "a"),
            (*ROOT, "entities", "created"),
            (*ROOT, "entities", "untouched"),
        }
        assert len(delta.appends) == 2
        assert {item["operation"] for item in delta.appends} == {
            "map_create",
            "append",
        }
        assert "bootstrap" not in repr(delta)
        assert "cursor" not in repr(delta)
    finally:
        world.event_logger.close()


def test_assigned_plain_container_keeps_dict_reference_semantics_until_commit(
    tmp_path,
):
    world = _world(tmp_path)
    fact = {"kind": "trade", "lines": ["debit"]}
    try:
        with world.write_environment_transaction() as tx:
            tx.state["facts"]["f-alias"] = fact
            fact["lines"].append("credit")
            assert tx.state["facts"]["f-alias"]["lines"] == [
                "debit",
                "credit",
            ]
        assert world.environment_data["state"]["facts"]["f-alias"]["lines"] == [
            "debit",
            "credit",
        ]
        fact["lines"].append("outside")
        assert world.environment_data["state"]["facts"]["f-alias"]["lines"] == [
            "debit",
            "credit",
        ]
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_body_failure_discards_all_staged_writes(tmp_path):
    world = _world(tmp_path)
    before = copy.deepcopy(world.environment_data["state"])
    try:
        with pytest.raises(RuntimeError, match="business failure"):
            with world.write_environment_transaction() as tx:
                tx.state["entities"]["a"]["qty"] = 0
                tx.state["facts"]["f-1"] = {"kind": "should-not-exist"}
                raise RuntimeError("business failure")

        assert world.environment_data["state"] == before
        delta = world.seal_persistence_tick()
        assert delta.replacements == ()
        assert delta.appends == ()
    finally:
        world.event_logger.close()


def test_commit_validation_failure_leaves_canonical_and_journal_unchanged(tmp_path):
    schema = persistent_state_schema(
        entities=replaceable_map(
            entry_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["qty"],
                "properties": {"qty": {"type": "integer"}},
            }
        )
    )
    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    world.environment_data["state"] = {"entities": {"a": {"qty": 1}}}
    world.configure_persistence(PersistenceSchema.compile(schema, root_path=ROOT))
    world.begin_persistence_tick(1)
    before = copy.deepcopy(world.environment_data["state"])
    try:
        with pytest.raises(ValueError):
            with world.write_environment_transaction() as tx:
                # 单字段删除暂时合法，提交时按整条业务记录统一校验。
                del tx.state["entities"]["a"]["qty"]
        assert world.environment_data["state"] == before
        delta = world.seal_persistence_tick()
        assert delta.replacements == ()
    finally:
        world.event_logger.close()


def test_record_is_validated_after_all_fields_reach_their_final_values(tmp_path):
    schema = persistent_state_schema(
        entities=replaceable_map(
            entry_schema={
                "type": "object",
                "additionalProperties": False,
                "required": ["qty", "status"],
                "properties": {
                    "qty": {"type": "integer"},
                    "status": {"type": "string"},
                },
            }
        )
    )
    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    world.environment_data["state"] = {"entities": {}}
    world.configure_persistence(PersistenceSchema.compile(schema, root_path=ROOT))
    world.begin_persistence_tick(1)
    try:
        with world.write_environment_transaction() as tx:
            tx.state["entities"]["new"] = {"qty": 0, "status": "draft"}
            tx.state["entities"]["new"]["qty"] = "temporary-invalid"
            tx.state["entities"]["new"]["status"] = "ready"
            tx.state["entities"]["new"]["qty"] = 8
        assert world.environment_data["state"]["entities"]["new"] == {
            "qty": 8,
            "status": "ready",
        }
        delta = world.seal_persistence_tick()
        assert len(delta.replacements) == 1
        assert delta.replacements[0]["value"] == {"qty": 8, "status": "ready"}
    finally:
        world.event_logger.close()


def test_create_then_delete_replaceable_record_folds_to_no_change(tmp_path):
    world = _world(tmp_path)
    try:
        with world.write_environment_transaction() as tx:
            tx.state["entities"]["temporary"] = {"qty": 1, "tags": []}
            del tx.state["entities"]["temporary"]
            assert "temporary" not in tx.state["entities"]
        assert "temporary" not in world.environment_data["state"]["entities"]
        delta = world.seal_persistence_tick()
        assert delta.replacements == ()
        assert delta.appends == ()
    finally:
        world.event_logger.close()


def test_mixed_record_keeps_old_history_out_of_transaction_delta(tmp_path):
    account_schema = {
        "type": "object",
        "properties": {
            "balance": replaceable(schema={"type": "number"}),
            "journal": append_only_map(),
        },
        "additionalProperties": False,
    }
    schema = persistent_state_schema(
        accounts=replaceable_map(entry_schema=account_schema)
    )
    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    world.environment_data["state"] = {
        "accounts": {
            "a": {"balance": 10, "journal": {"old": {"amount": 1}}}
        }
    }
    world.configure_persistence(PersistenceSchema.compile(schema, root_path=ROOT))
    world.begin_persistence_tick(1)
    try:
        with world.write_environment_transaction() as tx:
            account = tx.state["accounts"]["a"]
            account["balance"] = 9
            account["journal"]["new"] = {"amount": 1}
            assert account["journal"]["new"]["amount"] == 1
        delta = world.seal_persistence_tick()
        assert [tuple(item["path"]) for item in delta.replacements] == [
            (*ROOT, "accounts", "a", "balance")
        ]
        assert [tuple(item["path"]) for item in delta.appends] == [
            (*ROOT, "accounts", "a", "journal")
        ]
        assert "old" not in repr(delta)
    finally:
        world.event_logger.close()


def test_mixed_record_cannot_clear_nested_append_only_history(tmp_path):
    account_schema = {
        "type": "object",
        "properties": {
            "balance": replaceable(schema={"type": "number"}),
            "journal": append_only_map(),
        },
        "additionalProperties": False,
    }
    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    original = {"balance": 10, "journal": {"old": {"amount": 1}}}
    world.environment_data["state"] = {"accounts": {"a": copy.deepcopy(original)}}
    world.configure_persistence(
        PersistenceSchema.compile(
            persistent_state_schema(
                accounts=replaceable_map(entry_schema=account_schema)
            ),
            root_path=ROOT,
        )
    )
    world.begin_persistence_tick(1)
    try:
        with pytest.raises(ValueError, match="cannot be cleared"):
            with world.write_environment_transaction() as tx:
                tx.state["accounts"]["a"].clear()
        assert world.environment_data["state"]["accounts"]["a"] == original
        delta = world.seal_persistence_tick()
        assert delta.replacements == ()
        assert delta.appends == ()
    finally:
        world.event_logger.close()


def test_mixed_record_persists_open_field_without_copying_history(
    tmp_path,
):
    schema = persistent_state_schema(
        entities=replaceable_map(
            entry_schema={
                "type": "object",
                "properties": {"journal": append_only_map()},
                "additionalProperties": True,
            }
        )
    )
    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    original = {"journal": {"old": {"amount": 1}}, "label": "before"}
    world.environment_data["state"] = {"entities": {"a": copy.deepcopy(original)}}
    world.configure_persistence(PersistenceSchema.compile(schema, root_path=ROOT))
    world.begin_persistence_tick(1)
    try:
        with world.write_environment_transaction() as tx:
            tx.state["entities"]["a"]["label"] = "after"
        assert world.environment_data["state"]["entities"]["a"] == {
            **original,
            "label": "after",
        }
        delta = world.seal_persistence_tick()
        assert len(delta.replacements) == 1
        assert tuple(delta.replacements[0]["path"]) == (
            *ROOT,
            "entities",
            "a",
            "label",
        )
        assert delta.replacements[0]["value"] == "after"
        assert "old" not in repr(delta.replacements)
        assert delta.appends == ()
    finally:
        world.event_logger.close()


def test_new_mixed_record_combines_child_updates_before_appending_fact(tmp_path):
    account_schema = {
        "type": "object",
        "properties": {
            "balance": replaceable(schema={"type": "number"}),
            "journal": append_only_map(),
        },
        "additionalProperties": False,
    }
    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    world.environment_data["state"] = {"accounts": {}}
    world.configure_persistence(
        PersistenceSchema.compile(
            persistent_state_schema(
                accounts=replaceable_map(entry_schema=account_schema)
            ),
            root_path=ROOT,
        )
    )
    world.begin_persistence_tick(1)
    try:
        with world.write_environment_transaction() as tx:
            tx.state["accounts"]["new"] = {
                "balance": 0,
                "journal": {"old": {"amount": 0}},
            }
            tx.state["accounts"]["new"]["balance"] = 5
            tx.state["accounts"]["new"]["journal"]["j-1"] = {"amount": 5}
            assert tx.state["accounts"]["new"]["journal"]["j-1"]["amount"] == 5
            assert set(tx.state["accounts"]["new"]["journal"]) == {"old", "j-1"}
            with pytest.raises(ValueError, match="duplicate"):
                tx.state["accounts"]["new"]["journal"]["old"] = {"amount": 9}
        assert world.environment_data["state"]["accounts"]["new"] == {
            "balance": 5,
            "journal": {"old": {"amount": 0}, "j-1": {"amount": 5}},
        }
        delta = world.seal_persistence_tick()
        assert len(delta.replacements) == 1
        assert delta.replacements[0]["value"] == {
            "balance": 5,
            "journal": {"old": {"amount": 0}},
        }
        assert len(delta.appends) == 1
        assert delta.appends[0]["id"] == "j-1"
    finally:
        world.event_logger.close()


def test_new_mixed_record_can_append_to_nested_history_list(tmp_path):
    schema = persistent_state_schema(
        entities=replaceable_map(
            entry_schema={
                "type": "object",
                "properties": {"history": append_only_list()},
                "additionalProperties": False,
            }
        )
    )
    world = World(
        event_log_path=str(tmp_path / "events.jsonl"),
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    world.environment_data["state"] = {"entities": {}}
    world.configure_persistence(PersistenceSchema.compile(schema, root_path=ROOT))
    world.begin_persistence_tick(1)
    try:
        with world.write_environment_transaction() as tx:
            tx.state["entities"]["new"] = {"history": []}
            tx.state["entities"]["new"]["history"].append("new")
            assert list(tx.state["entities"]["new"]["history"]) == ["new"]
        assert world.environment_data["state"]["entities"]["new"] == {
            "history": ["new"]
        }
        delta = world.seal_persistence_tick()
        assert delta.replacements[0]["value"] == {"history": []}
        assert delta.appends[0]["value"] == "new"
    finally:
        world.event_logger.close()


def test_deleted_record_cannot_be_read_or_mutated_inside_transaction(tmp_path):
    world = _world(tmp_path)
    try:
        with world.write_environment_transaction() as tx:
            del tx.state["entities"]["a"]
            with pytest.raises(KeyError):
                _ = tx.state["entities"]["a"]
            with pytest.raises(KeyError):
                tx.state["entities"]["a"]["qty"] = 0
        assert "a" not in world.environment_data["state"]["entities"]
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_replaceable_list_slice_assignment_and_delete_are_staged(tmp_path):
    world = _world(tmp_path)
    try:
        with world.write_environment_transaction() as tx:
            tags = tx.state["entities"]["a"]["tags"]
            tags[:] = ["x", "y", "z"]
            del tags[1:2]
            assert list(tags) == ["x", "z"]
        assert world.environment_data["state"]["entities"]["a"]["tags"] == [
            "x",
            "z",
        ]
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_setdefault_returns_a_transactional_child_view(tmp_path):
    world = _world(tmp_path)
    try:
        with world.write_environment_transaction() as tx:
            tags = tx.state["entities"]["a"].setdefault("extra_tags", [])
            tags.append("captured")
            assert tx.state["entities"]["a"]["extra_tags"] == ["captured"]
        assert world.environment_data["state"]["entities"]["a"]["extra_tags"] == [
            "captured"
        ]
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_nested_transaction_views_deepcopy_as_plain_containers(tmp_path):
    world = _world(tmp_path)
    try:
        with world.write_environment_transaction() as tx:
            entity = tx.state["entities"]["a"]
            entity["tags"].append("new")
            detached = copy.deepcopy(dict(entity))
            assert detached == {"qty": 4, "tags": ["old", "new"]}
            assert type(detached["tags"]) is list
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_append_only_transaction_view_deepcopies_as_plain_list(tmp_path):
    world = _world(tmp_path)
    try:
        with world.write_environment_transaction() as tx:
            tx.state["audit"].append({"event": "new"})
            detached = copy.deepcopy(tx.state["audit"])
            assert detached == [
                {"event": "bootstrap"},
                {"event": "new"},
            ]
            assert isinstance(detached, list)
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_concurrent_conflict_is_per_record_and_append_id(tmp_path):
    world = _world(tmp_path)
    try:
        tx1 = world.write_environment_transaction()
        tx2 = world.write_environment_transaction()
        contextvars.Context().run(tx1.__enter__)
        contextvars.Context().run(tx2.__enter__)
        tx1.state["entities"]["a"]["qty"] = 3
        tx2.state["entities"]["a"]["qty"] = 2
        tx1.commit()
        with pytest.raises(RuntimeError, match="conflict"):
            tx2.commit()
        assert world.environment_data["state"]["entities"]["a"]["qty"] == 3

        tx3 = world.write_environment_transaction()
        tx4 = world.write_environment_transaction()
        contextvars.Context().run(tx3.__enter__)
        contextvars.Context().run(tx4.__enter__)
        tx3.state["facts"]["f-1"] = {"kind": "one"}
        tx4.state["facts"]["f-2"] = {"kind": "two"}
        tx3.commit()
        tx4.commit()
        assert set(world.environment_data["state"]["facts"]) == {
            "old",
            "f-1",
            "f-2",
        }

        tx5 = world.write_environment_transaction()
        tx6 = world.write_environment_transaction()
        contextvars.Context().run(tx5.__enter__)
        contextvars.Context().run(tx6.__enter__)
        tx5.state["audit"].append({"event": "five"})
        tx6.state["audit"].append({"event": "six"})
        tx5.commit()
        tx6.commit()
        assert [item["event"] for item in world.environment_data["state"]["audit"]] == [
            "bootstrap",
            "five",
            "six",
        ]
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_open_transaction_cannot_cross_tick_boundary(tmp_path):
    world = _world(tmp_path)
    tx = world.write_environment_transaction()
    tx.__enter__()
    try:
        tx.state["clock"]["day"] = 2
        with pytest.raises(RuntimeError, match="open state transactions"):
            world.seal_persistence_tick()
        world.abort_persistence_tick()
        assert world.environment_data["state"] == _state()
        with pytest.raises(RuntimeError, match="aborted|expired|invalidated"):
            _ = tx.state
    finally:
        world.event_logger.close()


@pytest.mark.asyncio
async def test_society0_explicit_mode_persists_and_restores_selected_contract(tmp_path):
    config = {
        "agent_types": [
            {
                "id": "worker",
                "archetype": "rule",
                "state_schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "score": {
                            "type": "integer",
                            "persistence": {"kind": "replaceable"},
                        }
                    },
                },
            }
        ],
        "agents": [
            {"id": "a", "type": "worker", "state": {"score": 0}}
        ],
        "environment": {
            "type": "explicit_transaction_test",
            "state": {"counter": 0, "events": []},
        },
    }
    engine = Society0(
        save_dir=str(tmp_path / "run"),
        base_config=config,
        environment_factory=ExplicitTransactionEnvironment,
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )

    @engine.step(name="explicit_write")
    async def explicit_write(ctx):
        with ctx.env.write_transaction() as tx:
            tx.state["counter"] += 1
            tx.state["events"].append(f"step:{ctx.step}")
        with ctx.world.get_agent("a").write_transaction() as tx:
            tx.state["score"] += 1
        return ctx.result()

    await engine.run(steps=1)
    assert engine.current_world_state is not None
    assert engine.current_world_state.environment_data["state"] == {
        "counter": 1,
        "events": ["step:0", "after:0"],
    }

    from society0.persistence import PersistenceManager

    reader = PersistenceManager(str(tmp_path / "reader"))
    try:
        restored, _ = await reader.load_checkpoint_from(
            tmp_path / "run",
            step=1,
            restore_chroma=False,
            environment_factory=ExplicitTransactionEnvironment,
        )
        assert restored.state_access_mode is StateAccessMode.EXPLICIT_TRANSACTIONS
        assert restored.environment_data["state"] == {
            "counter": 1,
            "events": ["step:0", "after:0"],
        }
        assert restored.agents_data["a"]["state"] == {"score": 1}
        with pytest.raises(TypeError, match="write_transaction"):
            restored.get_environment().state["counter"] = 2

        branch = await engine.persistence_manager.create_branch(1, "explicit-fork")
        try:
            branch_world = branch._v4_world
            assert branch_world is not None
            assert branch_world.state_access_mode is StateAccessMode.EXPLICIT_TRANSACTIONS
            branch_world.begin_persistence_tick(2)
            with branch_world.write_environment_transaction() as tx:
                tx.state["counter"] = 2
                tx.state["events"].append("fork:2")
            await branch.publish_delta(
                branch_world.seal_persistence_tick(), engine.schedule, force=True
            )
            source_again, _ = await reader.load_checkpoint_from(
                tmp_path / "run",
                step=1,
                restore_chroma=False,
                environment_factory=ExplicitTransactionEnvironment,
            )
            assert source_again.environment_data["state"]["counter"] == 1
            branch_restored, _ = await branch.load_checkpoint(
                2,
                restore_chroma=False,
                environment_factory=ExplicitTransactionEnvironment,
            )
            assert branch_restored.environment_data["state"] == {
                "counter": 2,
                "events": ["step:0", "after:0", "fork:2"],
            }
            assert branch_restored.state_access_mode is StateAccessMode.EXPLICIT_TRANSACTIONS
        finally:
            branch.close()
    finally:
        reader.close()


@pytest.mark.asyncio
async def test_society0_failed_explicit_transaction_leaves_live_world_at_root(tmp_path):
    config = {
        "agent_types": [],
        "agents": [],
        "environment": {
            "type": "explicit_transaction_test",
            "state": {"counter": 0, "events": []},
        },
    }
    engine = Society0(
        save_dir=str(tmp_path / "failed"),
        base_config=config,
        environment_factory=ExplicitTransactionEnvironment,
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )

    @engine.step(name="fail_inside_transaction")
    async def fail_inside_transaction(ctx):
        with ctx.env.write_transaction() as tx:
            tx.state["counter"] = 99
            tx.state["events"].append("uncommitted")
            raise RuntimeError("business failure")

    with pytest.raises(RuntimeError, match="business failure"):
        await engine.run(steps=1)
    assert engine.current_world_state is not None
    assert engine.current_world_state.environment_data["state"] == {
        "counter": 0,
        "events": [],
    }
