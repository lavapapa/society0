"""面向 Env 作者的 checkpoint v4 声明式 API 与普通 Python 写入合同。"""

from __future__ import annotations

import copy

import pytest

from society0 import (
    append_only_list,
    append_only_map,
    persistent_state_schema,
    replaceable,
    replaceable_map,
    transient,
)
from society0.core_data import World
from society0.incremental_checkpoint import PersistenceKind, PersistenceSchema, StateDeltaJournal


ROOT = ("environment", "state")


def _world(tmp_path, state: dict, schema: dict) -> World:
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["state"] = copy.deepcopy(state)
    world.configure_persistence(PersistenceSchema.compile(schema, root_path=ROOT))
    world.begin_persistence_tick(1)
    return world


def test_public_builder_keeps_persistence_declaration_compact_and_strict():
    schema = persistent_state_schema(
        clock=replaceable(),
        actors=replaceable_map(),
        trades=append_only_map(),
        audit=append_only_list(),
        runtime_index=transient(default={}),
    )
    compiled = PersistenceSchema.compile(schema, root_path=ROOT)

    assert schema["additionalProperties"] is False
    assert compiled.resolve((*ROOT, "clock")).kind is PersistenceKind.REPLACEABLE
    assert compiled.resolve((*ROOT, "actors", "actor-1")).granularity == "entry"
    assert compiled.resolve((*ROOT, "trades")).kind is PersistenceKind.APPEND_ONLY_MAP
    assert compiled.resolve((*ROOT, "audit")).kind is PersistenceKind.APPEND_ONLY_LIST
    runtime = compiled.resolve((*ROOT, "runtime_index"))
    assert runtime.kind is PersistenceKind.TRANSIENT
    assert runtime.has_default and runtime.default == {}


def test_replaceable_bounded_object_captures_normal_nested_python_writes(tmp_path):
    schema = persistent_state_schema(clock=replaceable())
    world = _world(tmp_path, {"clock": {"date": "2026-01-01", "week": 1}}, schema)
    try:
        state = world.create_environment_state_proxy()
        state["clock"]["date"] = "2026-01-08"
        state["clock"]["week"] += 1

        delta = world.seal_persistence_tick()

        assert len(delta.replacements) == 1
        assert tuple(delta.replacements[0]["path"]) == (*ROOT, "clock")
        assert dict(delta.replacements[0]["value"]) == {
            "date": "2026-01-08",
            "week": 2,
        }
    finally:
        world.event_logger.close()


def test_replaceable_map_captures_only_changed_entry_after_deep_dict_and_list_writes(tmp_path):
    schema = persistent_state_schema(inventories=replaceable_map())
    initial = {
        "inventories": {
            "inv-1": {"status": "open", "lots": [{"id": "lot-1", "qty": 2}]},
            "inv-2": {"status": "open", "lots": [{"id": "lot-2", "qty": 9}]},
        }
    }
    world = _world(tmp_path, initial, schema)
    try:
        state = world.create_environment_state_proxy()
        inventory = state["inventories"]["inv-1"]
        inventory["status"] = "reserved"
        inventory["lots"][0]["qty"] = 1
        inventory["lots"].append({"id": "lot-3", "qty": 1})

        delta = world.seal_persistence_tick()

        assert len(delta.replacements) == 1
        entry = delta.replacements[0]
        assert tuple(entry["path"]) == (*ROOT, "inventories", "inv-1")
        assert dict(entry["value"])["status"] == "reserved"
        assert [dict(item) for item in entry["value"]["lots"]] == [
            {"id": "lot-1", "qty": 1},
            {"id": "lot-3", "qty": 1},
        ]
        assert "inv-2" not in repr(entry)
    finally:
        world.event_logger.close()


def test_replaceable_map_compacts_each_changed_id_independently(tmp_path):
    schema = persistent_state_schema(actors=replaceable_map())
    world = _world(
        tmp_path,
        {"actors": {"a": {"cash": 1}, "b": {"cash": 2}, "c": {"cash": 3}}},
        schema,
    )
    try:
        actors = world.create_environment_state_proxy()["actors"]
        actors["a"]["cash"] = 10
        actors["a"]["cash"] = 11
        actors["c"]["cash"] = 30

        delta = world.seal_persistence_tick()

        assert [tuple(item["path"]) for item in delta.replacements] == [
            (*ROOT, "actors", "a"),
            (*ROOT, "actors", "c"),
        ]
        assert [item["value"]["cash"] for item in delta.replacements] == [11, 30]
    finally:
        world.event_logger.close()


def test_replaceable_map_supports_entry_create_replace_and_delete(tmp_path):
    schema = persistent_state_schema(plans=replaceable_map())
    world = _world(tmp_path, {"plans": {"old": {"status": "draft"}}}, schema)
    try:
        plans = world.create_environment_state_proxy()["plans"]
        plans["new"] = {"status": "draft"}
        plans["new"]["status"] = "approved"
        del plans["old"]

        delta = world.seal_persistence_tick()

        by_path = {tuple(item["path"]): item for item in delta.replacements}
        assert by_path[(*ROOT, "plans", "new")]["value"]["status"] == "approved"
        assert by_path[(*ROOT, "plans", "old")]["operation"] == "delete"
    finally:
        world.event_logger.close()


def test_append_only_fact_entry_is_deeply_immutable_after_creation(tmp_path):
    schema = persistent_state_schema(trades=append_only_map())
    world = _world(tmp_path, {"trades": {"t-1": {"qty": 1}}}, schema)
    try:
        trades = world.create_environment_state_proxy()["trades"]
        before = copy.deepcopy(world.environment_data["state"])

        with pytest.raises(ValueError, match="append-only|immutable|trades"):
            trades["t-1"]["qty"] = 2

        assert world.environment_data["state"] == before
    finally:
        world.abort_persistence_tick()
        world.event_logger.close()


def test_transient_subtree_accepts_nested_writes_but_never_enters_delta(tmp_path):
    schema = persistent_state_schema(runtime=transient(default={"queue": []}))
    world = _world(tmp_path, {"runtime": {"queue": []}}, schema)
    try:
        runtime = world.create_environment_state_proxy()["runtime"]
        runtime["queue"].append("job-1")
        runtime["cursor"] = 4

        delta = world.seal_persistence_tick()

        assert delta.replacements == ()
        assert delta.appends == ()
    finally:
        world.event_logger.close()


def test_replaceable_entry_list_mutators_capture_final_entry_once(tmp_path):
    schema = persistent_state_schema(queues=replaceable_map())
    world = _world(tmp_path, {"queues": {"q": {"items": [3, 1, 2]}}}, schema)
    try:
        items = world.create_environment_state_proxy()["queues"]["q"]["items"]
        items.sort()
        items.pop()
        items.extend([5, 4])

        delta = world.seal_persistence_tick()

        assert len(delta.replacements) == 1
        assert list(delta.replacements[0]["value"]["items"]) == [1, 2, 5, 4]
    finally:
        world.event_logger.close()


def test_replaceable_map_write_does_not_iterate_unchanged_entries(tmp_path):
    class NoScanMap(dict):
        def __iter__(self):
            raise AssertionError("must not scan unchanged entries")

        def items(self):
            raise AssertionError("must not scan unchanged entries")

        def values(self):
            raise AssertionError("must not scan unchanged entries")

    schema = persistent_state_schema(entities=replaceable_map())
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["state"] = {
        "entities": NoScanMap({"changed": {"value": 1}, "untouched": {"value": 2}})
    }
    # 此测试直接绑定已经验证过的声明，隔离 Tick 热路径是否扫描历史。
    compiled = PersistenceSchema.compile(schema, root_path=ROOT)
    world.set_state_delta_journal(StateDeltaJournal(compiled))
    try:
        world.begin_persistence_tick(1)
        world.create_environment_state_proxy()["entities"]["changed"]["value"] = 9
        delta = world.seal_persistence_tick()
        assert len(delta.replacements) == 1
        assert tuple(delta.replacements[0]["path"]) == (*ROOT, "entities", "changed")
    finally:
        world.event_logger.close()
