"""Chroma 单库多 Tick/分支视图的 v4 低层契约。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pytest

from society0.agent.memory import Memory


pytestmark = pytest.mark.primary


def _matches(metadata: Dict[str, Any], where: Dict[str, Any] | None) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_matches(metadata, item) for item in where["$and"])
    if "$or" in where:
        return any(_matches(metadata, item) for item in where["$or"])
    for key, expression in where.items():
        value = metadata.get(key)
        for operator, expected in expression.items():
            if operator == "$eq" and value != expected:
                return False
            if operator == "$lte" and not (value <= expected):
                return False
            if operator == "$lt" and not (value < expected):
                return False
            if operator == "$gte" and not (value >= expected):
                return False
            if operator == "$gt" and not (value > expected):
                return False
    return True


class _FakeCollection:
    def __init__(self):
        self.records: Dict[str, Dict[str, Any]] = {}
        self.where_calls: List[Dict[str, Any] | None] = []

    def count(self):
        return len(self.records)

    def add(self, *, ids, documents, embeddings, metadatas):
        for index, record_id in enumerate(ids):
            if record_id in self.records:
                raise ValueError(f"duplicate id {record_id}")
            self.records[record_id] = {
                "document": documents[index],
                "embedding": embeddings[index],
                "metadata": dict(metadatas[index]),
            }

    def upsert(self, *, ids, documents, embeddings, metadatas):
        for index, record_id in enumerate(ids):
            self.records[record_id] = {
                "document": documents[index],
                "embedding": embeddings[index],
                "metadata": dict(metadatas[index]),
            }

    def update(self, *, ids, metadatas=None, documents=None, embeddings=None):
        for index, record_id in enumerate(ids):
            record = self.records[record_id]
            if metadatas is not None:
                record["metadata"].update(metadatas[index])
            if documents is not None:
                record["document"] = documents[index]
            if embeddings is not None:
                record["embedding"] = embeddings[index]

    def get(self, *, ids=None, include=None, where=None, limit=None):
        self.where_calls.append(where)
        selected = []
        for record_id, record in self.records.items():
            if ids is not None and record_id not in ids:
                continue
            if not _matches(record["metadata"], where):
                continue
            selected.append(record_id)
            if limit is not None and len(selected) >= limit:
                break
        return {
            "ids": selected,
            "documents": [self.records[item]["document"] for item in selected],
            "metadatas": [self.records[item]["metadata"] for item in selected],
            "embeddings": [self.records[item]["embedding"] for item in selected],
        }

    def query(self, *, query_embeddings, n_results, include, where=None):
        result = self.get(include=include, where=where, limit=n_results)
        return {
            "ids": [result["ids"]],
            "documents": [result["documents"]],
            "metadatas": [result["metadatas"]],
            "distances": [[0.0 for _ in result["ids"]]],
        }

    def delete(self, *, ids=None, where=None):
        for record_id in list(self.records):
            if ids is not None and record_id not in ids:
                continue
            if where is not None and not _matches(self.records[record_id]["metadata"], where):
                continue
            del self.records[record_id]


class _FakeClient:
    def __init__(self):
        self.collection = _FakeCollection()

    def get_or_create_collection(self, **_kwargs):
        return self.collection


async def _embed(texts: Iterable[str], dimensions: int, metadata=None):
    del metadata
    return {"result": [[0.1, 0.2, 0.3] for _ in texts], "dimensions": dimensions}


def _memory(client, *, branch="main", source=None, epoch="epoch-1", lineage=None):
    return Memory(
        "agent-a",
        client,
        branch_id=branch,
        source_branch_id=source,
        branch_lineage=lineage,
        write_epoch_id=epoch,
        embed_call=_embed,
        embedding_dim=3,
    )


@pytest.mark.asyncio
async def test_memory_records_carry_v4_visibility_metadata():
    client = _FakeClient()
    memory = _memory(client, epoch="epoch-success")

    ids = await memory.add_memories_batch(
        [
            {
                "memory_id": "mem_agent-a_fact",
                "memory_type": "episodic",
                "content": "step 4 fact",
                "timestamp": 4,
                "importance": 3,
            }
        ]
    )

    metadata = next(
        record["metadata"]
        for record in client.collection.records.values()
        if record["metadata"].get("logical_memory_id") == ids[0]
    )
    assert metadata["created_step"] == 4
    assert metadata["visible_until_step"] == Memory.OPEN_VISIBLE_UNTIL
    assert metadata["branch_id"] == "main"
    assert metadata["source_branch_id"] == "main"
    assert metadata["write_epoch_id"] == "epoch-success"


@pytest.mark.asyncio
async def test_view_filters_branch_tick_and_unpublished_epoch_before_retrieval():
    client = _FakeClient()
    failed = _memory(client, branch="fork", source="main", epoch="epoch-failed", lineage=[("main", 4)])
    success = _memory(client, branch="fork", source="main", epoch="epoch-success", lineage=[("main", 4)])
    source = _memory(client, branch="main", epoch="epoch-main")

    await failed.add_memories_batch(
        [{"memory_id": "failed", "content": "failed tick", "timestamp": 5, "importance": 3}]
    )
    await success.add_memories_batch(
        [{"memory_id": "success", "content": "successful tick", "timestamp": 5, "importance": 3}]
    )
    await source.add_memories_batch(
        [{"memory_id": "ancestor", "content": "ancestor fact", "timestamp": 4, "importance": 3}]
    )

    success.set_memory_view(
        target_step=5,
        branch_lineage=[("main", 4)],
        committed_write_epoch_ids={"epoch-success", "epoch-main"},
    )
    results = await success.retrieve("fact", top_k=10, current_step=5)

    assert "successful tick" in results
    assert "ancestor fact" in results
    assert "failed tick" not in results
    assert success._memory_where_filter(5) != {
        "$and": [{"agent_id": {"$eq": "agent-a"}}]
    }
    assert any("visible_until_step" in str(where) for where in client.collection.where_calls)


@pytest.mark.asyncio
async def test_unpublished_epoch_is_excluded_from_export_and_inspection():
    client = _FakeClient()
    failed = _memory(client, epoch="epoch-failed")
    await failed.add_memories_batch(
        [{"memory_id": "failed", "content": "orphan", "timestamp": 2, "importance": 3}]
    )

    failed.set_memory_view(target_step=2, committed_write_epoch_ids=set())
    failed.clear_write_epoch()
    assert failed.export_memories() == []
    assert await failed.inspect_memory_ids(["failed"]) == {
        "existing_ids": [],
        "missing_ids": ["failed"],
        "mismatched_ids": [],
    }


@pytest.mark.asyncio
async def test_active_write_epoch_is_visible_until_cleared():
    client = _FakeClient()
    memory = _memory(client, epoch="epoch-live")
    await memory.add_memories_batch(
        [{"memory_id": "live", "content": "current tick", "timestamp": 2, "importance": 3}]
    )
    memory.set_memory_view(target_step=2, committed_write_epoch_ids=set())
    assert await memory.retrieve("tick", top_k=2, current_step=2) == ["current tick"]

    memory.clear_write_epoch()
    assert await memory.retrieve("tick", top_k=2, current_step=2) == []


@pytest.mark.asyncio
async def test_retry_epoch_uses_stable_versioned_id_and_rejects_payload_conflict():
    client = _FakeClient()
    failed = _memory(client, epoch="epoch-failed")
    success = _memory(client, epoch="epoch-success")
    entry = {"memory_id": "receipt", "content": "stable", "timestamp": 2, "importance": 3}

    await failed.add_memories_batch([entry])
    await success.add_memories_batch([entry])
    assert len(client.collection.records) == 2

    # Retry in the same epoch is idempotent and does not append another record.
    await success.add_memories_batch([entry])
    assert len(client.collection.records) == 2

    success.set_memory_view(target_step=2, committed_write_epoch_ids={"epoch-success"})
    assert [item["content"] for item in success.export_memories()] == ["stable"]
    assert await success.inspect_memory_ids(["receipt"]) == {
        "existing_ids": ["receipt"],
        "missing_ids": [],
        "mismatched_ids": [],
    }

    with pytest.raises(ValueError, match="conflict"):
        await success.add_memories_batch(
            [{**entry, "content": "changed"}], fire_and_forget=False
        )


@pytest.mark.asyncio
async def test_v4_receipt_inspection_resolves_physical_version_without_marker_view():
    client = _FakeClient()
    memory = _memory(client, epoch="epoch-retry")
    entry = {"memory_id": "receipt", "content": "stable", "timestamp": 2, "importance": 3}
    await memory.add_memories_batch([entry])
    assert await memory.inspect_memory_ids(["receipt"], entries=[entry]) == {
        "existing_ids": ["receipt"],
        "missing_ids": [],
        "mismatched_ids": [],
    }


@pytest.mark.asyncio
async def test_update_and_delete_close_visible_interval_without_rewriting_old_tick():
    client = _FakeClient()
    memory = _memory(client, epoch="epoch-main")
    logical_id = await memory.add_episodic_memory("old", timestamp=1, importance=3)

    await memory.update_memory(
        logical_id,
        content="new",
        timestamp=2,
        importance=3,
        visible_step=2,
    )
    memory.set_memory_view(target_step=1, committed_write_epoch_ids={"epoch-main"})
    assert await memory.retrieve("old", top_k=5, current_step=1) == ["old"]
    memory.set_memory_view(target_step=2, committed_write_epoch_ids={"epoch-main"})
    assert await memory.retrieve("new", top_k=5, current_step=2) == ["new"]

    await memory.delete_memory(logical_id, visible_step=3)
    memory.set_memory_view(target_step=2, committed_write_epoch_ids={"epoch-main"})
    assert await memory.retrieve("new", top_k=5, current_step=2) == ["new"]
    memory.set_memory_view(target_step=3, committed_write_epoch_ids={"epoch-main"})
    assert await memory.retrieve("new", top_k=5, current_step=3) == []


@pytest.mark.asyncio
async def test_fork_update_does_not_close_ancestor_record():
    client = _FakeClient()
    source = _memory(client, branch="main", epoch="epoch-main")
    fork = _memory(client, branch="fork", source="main", epoch="epoch-fork", lineage=[("main", 1)])
    logical_id = await source.add_episodic_memory("source", timestamp=1, importance=3)

    await fork.update_memory(logical_id, content="forked", timestamp=2, importance=3, visible_step=2)
    source.set_memory_view(target_step=2, committed_write_epoch_ids={"epoch-main"})
    assert await source.retrieve("source", top_k=5, current_step=2) == ["source"]
    fork.set_memory_view(
        target_step=2,
        branch_lineage=[("main", 1)],
        committed_write_epoch_ids={"epoch-main", "epoch-fork"},
    )
    fork_results = await fork.retrieve("forked", top_k=5, current_step=2)
    assert "forked" in fork_results
    assert "source" not in fork_results

    await fork.delete_memory(logical_id, visible_step=3)
    fork.set_memory_view(
        target_step=3,
        branch_lineage=[("main", 1)],
        committed_write_epoch_ids={"epoch-main", "epoch-fork"},
    )
    assert await fork.retrieve("forked", top_k=5, current_step=3) == []


@pytest.mark.asyncio
async def test_same_logical_id_on_fork_gets_separate_physical_record():
    client = _FakeClient()
    source = _memory(client, branch="main", epoch="epoch-main")
    fork = _memory(client, branch="fork", source="main", epoch="epoch-fork")
    await source.add_memories_batch(
        [{"memory_id": "same", "content": "source", "timestamp": 1, "importance": 3}]
    )
    await fork.add_memories_batch(
        [{"memory_id": "same", "content": "fork", "timestamp": 1, "importance": 3}]
    )
    assert len(client.collection.records) == 2
    assert {record["metadata"]["branch_id"] for record in client.collection.records.values()} == {
        "main",
        "fork",
    }


@pytest.mark.asyncio
async def test_real_chroma_uses_single_collection_and_epoch_view(tmp_path: Path):
    chromadb = pytest.importorskip("chromadb")
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    memory = _memory(client, epoch="epoch-success")
    await memory.add_memories_batch(
        [{"memory_id": "real", "content": "real chroma", "timestamp": 1, "importance": 3}]
    )
    memory.set_memory_view(target_step=1, committed_write_epoch_ids={"epoch-success"})

    assert await memory.retrieve("real", top_k=1, current_step=1) == ["real chroma"]
    assert len(list((tmp_path / "chroma").iterdir())) > 0
