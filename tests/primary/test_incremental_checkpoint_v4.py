import json

import pytest

from society0.incremental_checkpoint import (
    PersistenceKind,
    StateDeltaJournal,
    V4CheckpointStore,
)


def _declarations():
    return {
        ("environment", "state", "price"): PersistenceKind.REPLACEABLE,
        ("environment", "state", "facts_by_id"): PersistenceKind.APPEND_ONLY_MAP,
        ("environment", "state", "facts"): PersistenceKind.APPEND_ONLY_LIST,
        ("environment", "state", "cursor"): PersistenceKind.TRANSIENT,
    }


def test_replaceable_and_append_only_restore_each_tick_without_copying_old_facts(tmp_path):
    journal = StateDeltaJournal(_declarations())
    store = V4CheckpointStore(tmp_path)

    journal.begin_tick(1)
    journal.record_set(("environment", "state", "price"), 10)
    journal.record_map_create(("environment", "state", "facts_by_id"), "f1", {"v": 1})
    journal.record_append(("environment", "state", "facts"), {"id": "f1"})
    store.publish(journal.seal_tick())

    journal.begin_tick(2)
    journal.record_set(("environment", "state", "price"), 12)
    journal.record_map_create(("environment", "state", "facts_by_id"), "f2", {"v": 2})
    journal.record_append(("environment", "state", "facts"), {"id": "f2"})
    marker = store.publish(journal.seal_tick())

    assert store.restore(1) == {
        "environment": {"state": {"price": 10, "facts_by_id": {"f1": {"v": 1}}, "facts": [{"id": "f1"}]}}
    }
    assert store.restore(2) == {
        "environment": {
            "state": {
                "price": 12,
                "facts_by_id": {"f1": {"v": 1}, "f2": {"v": 2}},
                "facts": [{"id": "f1"}, {"id": "f2"}],
            }
        }
    }
    manifest = json.loads((tmp_path / marker["manifest_file"]).read_text())
    assert manifest["new_segments"][0]["entry_count"] == 2
    assert "f1" not in (tmp_path / manifest["new_segments"][0]["path"]).read_text()


def test_same_tick_order_duplicate_id_and_abort_are_strict(tmp_path):
    journal = StateDeltaJournal(_declarations())
    store = V4CheckpointStore(tmp_path)

    journal.begin_tick(1)
    journal.record_set(("environment", "state", "price"), 1)
    journal.record_delete(("environment", "state", "price"))
    journal.record_set(("environment", "state", "price"), 3)
    journal.record_map_create(("environment", "state", "facts_by_id"), "same", {"v": 1})
    with pytest.raises(ValueError, match="duplicate append-only map id"):
        journal.record_map_create(("environment", "state", "facts_by_id"), "same", {"v": 2})
    store.publish(journal.seal_tick())
    assert store.restore(1)["environment"]["state"]["price"] == 3

    journal.begin_tick(2)
    journal.record_set(("environment", "state", "price"), 99)
    journal.abort_tick()
    assert store.available_steps() == [1]
    assert store.restore(1)["environment"]["state"]["price"] == 3


def test_transient_is_never_serialized_and_missing_segment_is_detected(tmp_path):
    journal = StateDeltaJournal(_declarations())
    store = V4CheckpointStore(tmp_path)
    journal.begin_tick(1)
    journal.record_set(("environment", "state", "cursor"), 8)
    journal.record_append(("environment", "state", "facts"), {"id": "x"})
    marker = store.publish(journal.seal_tick())
    restored = store.restore(1)
    assert "cursor" not in restored.get("environment", {}).get("state", {})

    manifest = json.loads((tmp_path / marker["manifest_file"]).read_text())
    segment = tmp_path / manifest["new_segments"][0]["path"]
    segment.unlink()
    with pytest.raises(FileNotFoundError, match="segment"):
        store.restore(1)


def test_fixed_delta_write_volume_does_not_depend_on_prior_history(tmp_path):
    journal = StateDeltaJournal(_declarations())
    store = V4CheckpointStore(tmp_path)
    written = []
    for step in range(1, 101):
        journal.begin_tick(step)
        journal.record_map_create(
            ("environment", "state", "facts_by_id"),
            f"fact-{step}",
            {"payload": "x" * 128},
        )
        marker = store.publish(journal.seal_tick())
        written.append(marker["bytes_written"])

    assert max(written[1:]) <= min(written[1:]) + 256
    assert store.metrics["history_entries_read_while_publishing"] == 0
