"""动态历史 ID 共用声明导航，保持精确字段、数组及多根校验语义。"""
import itertools

import pytest

from society0.incremental_checkpoint import (
    PersistenceKind, PersistenceRule, PersistenceSchema, StateDeltaJournal, _WILDCARD,
)


def test_schema_navigation_handles_dynamic_entries_arrays_and_open_subtrees():
    schema = PersistenceSchema({
        "type": "object", "additionalProperties": False,
        "properties": {
            "rows": {"type": "object", "additionalProperties": {
                "type": "object", "additionalProperties": False,
                "required": ["quantity"], "properties": {
                    "quantity": {"type": "number"},
                    "values": {"type": "array", "items": {"type": "integer"}},
                    "payload": {"persistence": {"kind": "replaceable"}},
                },
            }},
        },
    }, ("environment", "state"), {})
    root = ("environment", "state", "rows")
    quantity = schema._schema_node((*root, "first", "quantity"))
    for index in range(40_000):
        assert schema._schema_node((*root, str(index), "quantity")) is quantity
    assert len(schema._schema_lookup.properties["rows"].properties) == 0
    schema.validate_write_value((*root, "r", "values", 0), 3)
    schema.validate_write_value((*root, "r", "values", slice(0, 2)), [3, 4])
    schema.validate_write_value((*root, "r", "payload", "arbitrary", 0), {"x": 3})
    with pytest.raises(TypeError, match="schema type"):
        schema.validate_write_value((*root, "r", "quantity"), "three")
    with pytest.raises(TypeError, match="schema type"):
        schema.validate_write_value((*root, "r", "values", slice(0, 1)), ["bad"])
    for path in ((*root, "r", "missing"), (*root, "r", "values", "missing")):
        with pytest.raises(ValueError, match="undeclared"):
            schema.validate_write_value(path, 1)
    with pytest.raises(ValueError, match="missing required"):
        schema.validate_write_value((*root, "r"), {}, require_complete=True)


def test_schema_navigation_preserves_merged_wildcard_roots_and_exact_properties():
    first = PersistenceSchema({"type": "object", "additionalProperties": False,
                               "properties": {"balance": {"type": "number"}}},
                              ("environment", "state"), {})
    second = PersistenceSchema({"type": "object", "additionalProperties": {"type": "string"},
                                "properties": {"count": {"type": "integer"}}},
                               ("agents", _WILDCARD, "state"), {})
    merged = PersistenceSchema.merge(first, second)
    merged.validate_write_value(("environment", "state", "balance"), 1.5)
    merged.validate_write_value(("agents", "a", "state", "count"), 2)
    merged.validate_write_value(("agents", "b", "state", "name"), "B")
    with pytest.raises(TypeError):
        merged.validate_write_value(("agents", "a", "state", "count"), "2")
    with pytest.raises(ValueError):
        merged.validate_write_value(("environment", "state", "name"), "B")


def test_write_rule_resolution_keeps_deepest_then_most_exact_precedence():
    patterns = [("root",), ("root", _WILDCARD), ("root", "x"),
                ("root", _WILDCARD, "x"), ("root", "x", _WILDCARD),
                ("root", "x", "x", "y")]
    rules = {p: PersistenceRule(p, PersistenceKind.REPLACEABLE) for p in patterns}
    schema = PersistenceSchema({}, (), rules)
    for length in range(5):
        for tail in itertools.product(("x", "y", "z"), repeat=length):
            path = ("root", *tail)
            matches = [p for p in patterns if schema._prefix_matches(p, path)]
            expected = max(matches, key=lambda p: (
                len(p), sum(x is not _WILDCARD for x in p),
                tuple(x is not _WILDCARD for x in p),
            ))
            result = schema.resolve_write(path)
            assert result.rule is rules[expected]
            assert result.anchor == path[:len(expected)]
    assert schema.resolve_write(("unknown",)) is None


@pytest.mark.parametrize("history_size", [0, 100_000])
def test_batch_preflight_only_checks_membership_of_prior_tick_writes(history_size):
    class PendingIds:
        def __init__(self):
            self.values = {(("facts",), str(i)) for i in range(history_size)}

        def __contains__(self, value):
            return value in self.values

        def __iter__(self):
            raise AssertionError("批量预检不得复制或扫描此前累积的事实编号")

    journal = StateDeltaJournal({("facts",): PersistenceKind.APPEND_ONLY_MAP,
                                 ("price",): PersistenceKind.REPLACEABLE})
    journal.begin_tick(1)
    journal._pending_map_ids = PendingIds()
    tokens = journal.prepare_proxy_operations([
        ((), "set", "price", 3), (("facts",), "set", "new", {"value": 1}),
    ])
    assert len(tokens) == 2
    with pytest.raises(ValueError, match="duplicate append-only map id"):
        journal.prepare_proxy_operations([
            (("facts",), "set", "same", {}), (("facts",), "set", "same", {}),
        ])
    if history_size:
        with pytest.raises(ValueError, match="duplicate append-only map id"):
            journal.prepare_proxy_operations([(("facts",), "set", "0", {})])
    assert journal._appends == []
    assert journal._replacements == {}
