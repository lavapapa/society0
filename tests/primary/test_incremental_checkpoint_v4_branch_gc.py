from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from society0.incremental_checkpoint import (
    PersistenceKind,
    StateDeltaJournal,
    V4CheckpointStore,
)


PRICE = ("environment", "state", "price")
FACTS = ("environment", "state", "facts")


def _journal() -> StateDeltaJournal:
    return StateDeltaJournal(
        {
            PRICE: PersistenceKind.REPLACEABLE,
            FACTS: PersistenceKind.APPEND_ONLY_LIST,
        }
    )


def _delta(journal: StateDeltaJournal, step: int, price: int, fact: str):
    journal.begin_tick(step)
    journal.record_set(PRICE, price)
    journal.record_append(FACTS, {"id": fact})
    return journal.seal_tick()


def test_fork_reuses_immutable_history_and_branches_evolve_independently(tmp_path):
    source = V4CheckpointStore(tmp_path, branch_id="main")
    journal = _journal()
    source.publish_root(
        (
            {"path": list(PRICE), "operation": "set", "value": 0, "sequence": 0},
            {"path": list(FACTS), "operation": "set", "value": [], "sequence": 1},
        ),
        metadata={"run_id": "run-a"},
    )
    source.publish(_delta(journal, 1, 10, "shared"))

    source_marker_path = source.complete_dir / "step_000001.json"
    source_marker_before = source_marker_path.read_bytes()
    source_manifest_paths = set(source.manifests_dir.glob("*.json"))
    source_segment_paths = set(source.segments_dir.glob("*.json.gz"))

    branch = source.fork("policy", step=1)

    assert source_marker_path.read_bytes() == source_marker_before
    assert set(branch.manifests_dir.glob("*.json")) == source_manifest_paths
    assert set(branch.segments_dir.glob("*.json.gz")) == source_segment_paths
    assert branch.resolve(1)["marker"]["branch_id"] == "policy"

    branch.publish(_delta(_journal(), 2, 20, "branch-only"))
    source.publish(_delta(journal, 2, 11, "source-only"))

    assert source.restore(2)["environment"]["state"] == {
        "price": 11,
        "facts": [{"id": "shared"}, {"id": "source-only"}],
    }
    assert branch.restore(2)["environment"]["state"] == {
        "price": 20,
        "facts": [{"id": "shared"}, {"id": "branch-only"}],
    }


def test_gc_keeps_components_reachable_only_from_a_branch(tmp_path):
    source = V4CheckpointStore(tmp_path)
    source.publish_root(
        ({"path": list(PRICE), "operation": "set", "value": 0, "sequence": 0},),
        metadata={"run_id": "run-a"},
    )
    source.publish(_delta(_journal(), 1, 10, "shared"))
    branch = source.fork("retained", step=1)
    branch.publish(_delta(_journal(), 2, 20, "branch-only"))

    branch_manifest = Path(branch.resolve(2)["manifest_file"])
    branch_payload = json.loads(branch_manifest.read_text(encoding="utf-8"))
    branch_replacement = tmp_path / branch_payload["replacement_file"]
    branch_segment = tmp_path / branch_payload["new_segments"][0]["path"]

    orphan_manifest = source.manifests_dir / "orphan.json"
    orphan_replacement = source.replacements_dir / "orphan.json.gz"
    orphan_segment = source.segments_dir / "orphan.json.gz"
    orphan_manifest.write_text("{}", encoding="utf-8")
    orphan_replacement.write_bytes(gzip.compress(b"{}", mtime=0))
    orphan_segment.write_bytes(gzip.compress(b"{}", mtime=0))

    removed = source.cleanup_orphans()

    assert "checkpoints/v4/manifests/orphan.json" in removed
    assert "checkpoints/v4/replacements/orphan.json.gz" in removed
    assert "checkpoints/v4/segments/orphan.json.gz" in removed
    assert branch_manifest.exists()
    assert branch_replacement.exists()
    assert branch_segment.exists()


def test_manifest_cycle_and_parent_step_regression_fail_closed(tmp_path):
    store = V4CheckpointStore(tmp_path)
    store.publish_root(
        ({"path": list(PRICE), "operation": "set", "value": 0, "sequence": 0},),
        metadata={"run_id": "run-a"},
    )
    marker = store.publish(_delta(_journal(), 1, 10, "one"))
    manifest_path = tmp_path / marker["manifest_file"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["parent_checkpoint_id"] = manifest["checkpoint_id"]
    raw = store._canonical_bytes(manifest)
    manifest_path.write_bytes(raw)
    marker_path = store.complete_dir / "step_000001.json"
    marker_payload = json.loads(marker_path.read_text(encoding="utf-8"))
    marker_payload["manifest_sha256"] = store._sha256(raw)
    marker_path.write_bytes(store._canonical_bytes(marker_payload))

    with pytest.raises(ValueError, match="cycle|step|chain"):
        store.restore(1)


def test_publish_reads_no_historical_components_and_writes_only_fixed_delta(tmp_path, monkeypatch):
    store = V4CheckpointStore(tmp_path)
    store.publish_root(
        ({"path": list(PRICE), "operation": "set", "value": 0, "sequence": 0},),
        metadata={"run_id": "run-a"},
    )
    journal = _journal()
    for step in range(1, 201):
        store.publish(_delta(journal, step, step, f"fact-{step}"))

    historical_reads = []
    original_read_bytes = Path.read_bytes

    def guarded_read(path: Path):
        if path.parent in {store.segments_dir, store.replacements_dir}:
            historical_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    marker = store.publish(_delta(journal, 201, 201, "fixed"))

    assert historical_reads == []
    assert marker["bytes_written"] < 4096
    assert store.metrics["history_entries_read_while_publishing"] == 0
