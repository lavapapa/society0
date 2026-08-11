import gzip
import json

import pytest

from society0.incremental_checkpoint import (
    PersistenceKind,
    StateDeltaJournal,
    V4CheckpointStore,
)
from society0.core_data import World
from society0.agent.memory import Memory


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
    with gzip.open(tmp_path / manifest["new_segments"][0]["path"], "rt") as handle:
        segment = json.load(handle)
    assert [entry.get("id") for entry in segment["entries"]] == ["f2", None]


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


def test_world_state_proxy_records_before_mutation_and_rejects_duplicate_fact(tmp_path):
    journal = StateDeltaJournal(_declarations())
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["state"] = {
        "price": 0,
        "facts_by_id": {},
        "facts": [],
        "cursor": 0,
    }
    world.set_state_delta_journal(journal)
    journal.begin_tick(1)

    state = world.create_environment_state_proxy()
    state["price"] = 7
    state["facts_by_id"]["f1"] = {"v": 1}
    state["facts"].append({"id": "f1"})
    state["cursor"] = 4
    with pytest.raises(ValueError, match="duplicate append-only map id"):
        state["facts_by_id"]["f1"] = {"v": 2}

    assert state["facts_by_id"]["f1"]["v"] == 1
    restored_store = V4CheckpointStore(tmp_path / "v4")
    restored_store.publish(journal.seal_tick())
    restored = restored_store.restore(1)
    assert restored["environment"]["state"] == {
        "price": 7,
        "facts_by_id": {"f1": {"v": 1}},
        "facts": [{"id": "f1"}],
    }


def test_memory_visibility_filter_hides_future_and_keeps_fork_isolated():
    memory = Memory.__new__(Memory)
    memory.agent_id = "agent-1"
    memory.branch_id = "fork-b"
    memory.branch_lineage = [("main", 4)]

    assert memory._memory_where_filter(6) == {
        "$and": [
            {"agent_id": {"$eq": "agent-1"}},
            {
                "$or": [
                    {
                        "$and": [
                            {"branch_id": {"$eq": "fork-b"}},
                            {"created_step": {"$lte": 6}},
                        ]
                    },
                    {
                        "$and": [
                            {"branch_id": {"$eq": "main"}},
                            {"created_step": {"$lte": 4}},
                        ]
                    },
                ]
            },
        ]
    }


def test_failure_before_complete_marker_is_invisible_and_orphans_are_collectable(
    tmp_path, monkeypatch
):
    journal = StateDeltaJournal(_declarations())
    store = V4CheckpointStore(tmp_path)
    journal.begin_tick(1)
    journal.record_set(("environment", "state", "price"), 1)
    delta = journal.seal_tick()
    original_write = store._atomic_write

    def fail_marker(path, data):
        if path.parent == store.complete_dir:
            raise OSError("injected marker failure")
        return original_write(path, data)

    monkeypatch.setattr(store, "_atomic_write", fail_marker)
    with pytest.raises(OSError, match="marker failure"):
        store.publish(delta)
    assert store.available_steps() == []
    monkeypatch.setattr(store, "_atomic_write", original_write)
    assert store.cleanup_orphans()
    assert not list(store.manifests_dir.iterdir())
    assert not list(store.replacements_dir.iterdir())


def test_replacement_corruption_is_detected(tmp_path):
    journal = StateDeltaJournal(_declarations())
    store = V4CheckpointStore(tmp_path)
    journal.begin_tick(1)
    journal.record_set(("environment", "state", "price"), 1)
    marker = store.publish(journal.seal_tick())
    manifest = json.loads((tmp_path / marker["manifest_file"]).read_text())
    replacement = tmp_path / manifest["replacement_file"]
    replacement.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="replacement content hash mismatch"):
        store.restore(1)
