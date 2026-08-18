"""Checkpoint v4 声明与受控写入的第一批 TDD 用例。

这些用例当前是预期红灯：声明编译器、声明驱动的 World 生命周期以及
跨 Tick 代理失效语义尚未在产品代码中落地。测试先冻结最小公开合同，
实现完成后应在不放宽断言的情况下转绿。
"""

from __future__ import annotations

import copy

import pytest

import society0.incremental_checkpoint as checkpoint
from society0.core_data import World


PersistenceKind = checkpoint.PersistenceKind
StateDeltaJournal = checkpoint.StateDeltaJournal
V4CheckpointStore = checkpoint.V4CheckpointStore


_STATE_ROOT = ("environment", "state")


def _persistence(kind: str, **kwargs):
    """返回 scope 约定的 ``persistence.kind`` 声明。"""

    return {"kind": kind, **kwargs}


def _state_schema(properties: dict, *, additional_properties: bool = False) -> dict:
    return {
        "type": "object",
        "additionalProperties": additional_properties,
        "properties": properties,
    }


def _compile(schema: dict):
    """调用拟议的最小公开声明编译 API。"""

    return checkpoint.PersistenceSchema.compile(schema, root_path=_STATE_ROOT)


def _world_with_state(tmp_path, schema, state: dict) -> World:
    tmp_path.mkdir(parents=True, exist_ok=True)
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    # bootstrap 阶段允许 canonical writer 写入初始 state；配置时应校验声明。
    world.environment_data["state"] = copy.deepcopy(state)
    world.configure_persistence(schema)
    return world


def test_recursive_schema_compile_emits_rules_for_nested_properties():
    schema = _compile(
        _state_schema(
            {
                "account": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "cash": {
                            "type": "number",
                            "persistence": _persistence("replaceable"),
                        },
                        "cursor": {
                            "type": "integer",
                            "default": 0,
                            "persistence": _persistence("transient"),
                        },
                    },
                },
                "trades": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                    "persistence": _persistence("append_only_map"),
                },
                "audit": {
                    "type": "array",
                    "items": {"type": "object"},
                    "persistence": _persistence("append_only_list"),
                },
            }
        )
    )

    cash = schema.resolve((*_STATE_ROOT, "account", "cash"))
    cursor = schema.resolve((*_STATE_ROOT, "account", "cursor"))
    trades = schema.resolve((*_STATE_ROOT, "trades"))
    audit = schema.resolve((*_STATE_ROOT, "audit"))

    assert cash is not None and cash.kind is PersistenceKind.REPLACEABLE
    assert cursor is not None and cursor.kind is PersistenceKind.TRANSIENT
    assert trades is not None and trades.kind is PersistenceKind.APPEND_ONLY_MAP
    assert audit is not None and audit.kind is PersistenceKind.APPEND_ONLY_LIST


def test_restored_world_can_bind_persistence_without_revalidating_full_state(
    tmp_path,
    monkeypatch,
):
    schema = _compile(
        _state_schema(
            {
                "counter": {
                    "type": "integer",
                    "persistence": _persistence("replaceable"),
                }
            }
        )
    )
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["state"] = {"counter": 1}

    def unexpected_validation(_self, _state):
        raise AssertionError("恢复检查点不应再次序列化并校验完整世界")

    monkeypatch.setattr(
        checkpoint.PersistenceSchema,
        "validate_initial_state",
        unexpected_validation,
    )

    world.configure_persistence(schema, validate_initial_state=False)
    world.begin_persistence_tick(1)
    world.create_environment_state_proxy()["counter"] = 2
    delta = world.seal_persistence_tick()

    assert len(delta.replacements) == 1
    world.event_logger.close()


def test_persistence_schema_reuses_concrete_write_resolution():
    schema = _compile(
        _state_schema(
            {
                "inventories": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "persistence": _persistence(
                        "replaceable",
                        granularity="entry",
                    ),
                }
            }
        )
    )
    path = (*_STATE_ROOT, "inventories", "lot-1", "quantity")

    first = schema.resolve_write(path)
    second = schema.resolve_write(path)

    assert first is second
    assert first is not None
    assert first.anchor == (*_STATE_ROOT, "inventories", "lot-1")


def test_schema_compile_resolves_wildcard_dynamic_entry_map():
    schema = _compile(
        _state_schema(
            {
                # 动态 map 的每个 entry 都是独立的 replaceable 投影；编译
                # schema 时不能枚举任意 entry ID。
                "post_view_count_by_id": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                    "persistence": _persistence("replaceable", granularity="entry"),
                }
            }
        )
    )

    rule = schema.resolve((*_STATE_ROOT, "post_view_count_by_id", "post-001"))

    assert rule is not None
    assert rule.kind is PersistenceKind.REPLACEABLE
    assert rule.granularity == "entry"


def test_schema_compile_rejects_unbounded_replaceable_map_without_entry_granularity():
    with pytest.raises((TypeError, ValueError), match="granularity|entry|post_view_count"):
        _compile(
            _state_schema(
                {
                    "post_view_count_by_id": {
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                        "persistence": _persistence("replaceable"),
                    }
                }
            )
        )


def test_schema_compile_rejects_missing_persistence_kind_fail_closed():
    with pytest.raises((TypeError, ValueError), match="cash"):
        _compile(
            _state_schema(
                {
                    "cash": {"type": "number"},
                }
            )
        )


def test_schema_compile_rejects_unknown_persistence_kind_fail_closed():
    with pytest.raises((TypeError, ValueError), match="future_kind|unknown|kind"):
        _compile(
            _state_schema(
                {
                    "cash": {
                        "type": "number",
                        "persistence": _persistence("future_kind"),
                    }
                }
            )
        )


def test_schema_compile_rejects_replaceable_parent_child_conflict():
    with pytest.raises((TypeError, ValueError), match="account|cash|conflict"):
        _compile(
            _state_schema(
                {
                    "account": {
                        "type": "object",
                        "persistence": _persistence("replaceable"),
                        "properties": {
                            "cash": {
                                "type": "number",
                                "persistence": _persistence("replaceable"),
                            }
                        },
                    }
                }
            )
        )


def test_schema_compile_rejects_append_map_nested_mutable_child_conflict():
    with pytest.raises((TypeError, ValueError), match="post_facts|view_count|conflict"):
        _compile(
            _state_schema(
                {
                    "post_facts": {
                        "type": "object",
                        "persistence": _persistence("append_only_map"),
                        "additionalProperties": {
                            "type": "object",
                            "properties": {
                                "view_count": {
                                    "type": "integer",
                                    "persistence": _persistence("replaceable"),
                                }
                            },
                        },
                    }
                }
            )
        )


def test_transient_rule_exposes_schema_default():
    schema = _compile(
        _state_schema(
            {
                "tick_cursor": {
                    "type": "integer",
                    "default": 0,
                    "persistence": _persistence("transient"),
                }
            }
        )
    )

    rule = schema.resolve((*_STATE_ROOT, "tick_cursor"))

    assert rule is not None and rule.kind is PersistenceKind.TRANSIENT
    assert rule.has_default is True
    assert rule.default == 0


def test_plain_initial_state_with_undeclared_nonempty_field_is_rejected():
    schema = _compile(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {},
        }
    )

    with pytest.raises((TypeError, ValueError), match="price"):
        schema.validate_initial_state({"price": 123})


def test_append_only_map_rejects_duplicate_id_present_in_initial_state_before_mutation(tmp_path):
    schema = _compile(
        _state_schema(
            {
                "facts": {
                    "type": "object",
                    "persistence": _persistence("append_only_map"),
                    "additionalProperties": {"type": "object"},
                }
            }
        )
    )
    world = _world_with_state(tmp_path, schema, {"facts": {"known": {"v": 1}}})
    try:
        world.begin_persistence_tick(1)
        facts = world.create_environment_state_proxy()["facts"]
        before = copy.deepcopy(world.environment_data["state"])

        with pytest.raises(ValueError, match="duplicate|append-only map|known"):
            facts["known"] = {"v": 2}

        assert world.environment_data["state"] == before
    finally:
        world.event_logger.close()


def test_append_only_map_rejects_duplicate_id_after_restore_before_mutation(tmp_path):
    schema = _compile(
        _state_schema(
            {
                "facts": {
                    "type": "object",
                    "persistence": _persistence("append_only_map"),
                    "additionalProperties": {"type": "object"},
                }
            }
        )
    )
    journal = StateDeltaJournal(schema)
    journal.begin_tick(1)
    journal.record_map_create((*_STATE_ROOT, "facts"), "restored", {"v": 1})
    store = V4CheckpointStore(tmp_path / "seed")
    store.publish(journal.seal_tick())
    restored = store.restore(1)

    world = _world_with_state(tmp_path / "restored", schema, restored["environment"]["state"])
    try:
        world.begin_persistence_tick(2)
        facts = world.create_environment_state_proxy()["facts"]
        before = copy.deepcopy(world.environment_data["state"])

        with pytest.raises(ValueError, match="duplicate|append-only map|restored"):
            facts["restored"] = {"v": 2}

        assert world.environment_data["state"] == before
    finally:
        world.event_logger.close()


def test_binding_append_only_map_does_not_enumerate_existing_history():
    class HistoryMap(dict):
        def __iter__(self):
            raise AssertionError("binding must not scan append-only history")

        def keys(self):
            raise AssertionError("binding must not scan append-only history")

        def items(self):
            raise AssertionError("binding must not scan append-only history")

    schema = _compile(
        _state_schema(
            {
                "facts": {
                    "type": "object",
                    "persistence": _persistence("append_only_map"),
                    "additionalProperties": {"type": "object"},
                }
            }
        )
    )
    facts = HistoryMap({"known": {"v": 1}})
    canonical = {"environment": {"state": {"facts": facts}}}
    journal = StateDeltaJournal(schema)
    journal.bind_canonical_state(canonical)
    journal.begin_tick(1)

    with pytest.raises(ValueError, match="duplicate append-only map id"):
        journal.record_map_create((*_STATE_ROOT, "facts"), "known", {"v": 2})


def test_sealed_delta_is_detached_from_recorded_value():
    schema = _compile(
        _state_schema(
            {
                "payload": {
                    "type": "object",
                    "persistence": _persistence("replaceable"),
                }
            }
        )
    )
    journal = StateDeltaJournal(schema)
    journal.begin_tick(1)
    recorded = {"nested": {"value": 1}}
    journal.record_set((*_STATE_ROOT, "payload"), recorded)
    delta = journal.seal_tick()

    recorded["nested"]["value"] = 2

    assert delta.replacements[0]["value"]["nested"]["value"] == 1
    assert tuple(delta.replacements[0]["path"]) == (*_STATE_ROOT, "payload")


def test_nested_replaceable_record_is_materialized_once_at_tick_seal(tmp_path):
    schema = _compile(
        _state_schema(
            {
                "inventories": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "persistence": _persistence(
                        "replaceable",
                        granularity="entry",
                    ),
                }
            }
        )
    )
    world = _world_with_state(
        tmp_path,
        schema,
        {"inventories": {"lot-1": {"quantity": 10, "reserved": 0}}},
    )
    try:
        world.begin_persistence_tick(1)
        lot = world.create_environment_state_proxy()["inventories"]["lot-1"]
        lot["quantity"] = 8
        lot["reserved"] = 2
        lot["quantity"] = 7

        delta = world.seal_persistence_tick()

        assert len(delta.replacements) == 1
        replacement = delta.replacements[0]
        assert replacement["operation"] == "set"
        assert tuple(replacement["path"]) == (
            *_STATE_ROOT,
            "inventories",
            "lot-1",
        )
        assert dict(replacement["value"]) == {"quantity": 7, "reserved": 2}
    finally:
        world.event_logger.close()


def test_disabled_state_change_log_keeps_version_without_building_events(tmp_path):
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["state"] = {"counter": 0}
    world.set_state_change_event_recording(False)
    version_before = world.get_state_version()

    world.create_environment_state_proxy()["counter"] = 1
    world.event_logger.close()

    assert world.get_state_version() == version_before + 1
    assert not (tmp_path / "events.jsonl").exists()


def test_nested_proxy_from_previous_tick_is_invalidated_before_new_mutation(tmp_path):
    schema = _compile(
        _state_schema(
            {
                "account": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "cash": {
                            "type": "number",
                            "persistence": _persistence("replaceable"),
                        }
                    },
                }
            }
        )
    )
    world = _world_with_state(tmp_path, schema, {"account": {"cash": 1}})
    try:
        world.begin_persistence_tick(1)
        nested = world.create_environment_state_proxy()["account"]
        nested["cash"] = 2
        first_delta = world.seal_persistence_tick()
        assert first_delta.step == 1

        world.begin_persistence_tick(2)
        before = copy.deepcopy(world.environment_data["state"])
        with pytest.raises((RuntimeError, ReferenceError), match="stale|expired|tick|proxy"):
            nested["cash"] = 3

        assert world.environment_data["state"] == before
        second_delta = world.seal_persistence_tick()
        assert second_delta.replacements == ()
    finally:
        world.event_logger.close()
