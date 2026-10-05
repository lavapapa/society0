"""完整发布链的历史读取与共享记忆提交视图回归。"""
from pathlib import Path
from types import SimpleNamespace

import pytest

from society0.agent.memory import Memory
from society0.core_data import World
from society0.incremental_checkpoint import PersistenceSchema, SealedTickDelta, V4CheckpointStore
from society0.persistence import PersistenceManager


def configured(tmp_path, checkpoint_every=1):
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["type"] = "plain"
    world.environment_data["state"] = {}
    manager = PersistenceManager(str(tmp_path / "run"))
    manager.configure_v4(world, PersistenceSchema.compile(
        {"type": "object", "properties": {}, "additionalProperties": False},
        root_path=("environment", "state")),
        checkpoint_every=checkpoint_every)
    return world, manager


@pytest.mark.asyncio
async def test_complete_publication_does_not_read_historical_manifests(tmp_path, monkeypatch):
    world, manager = configured(tmp_path)
    reads = []
    original = Path.read_bytes
    try:
        await manager.publish_root(world, object())
        def record(path):
            if path.parent.name == "manifests":
                reads.append(path)
            return original(path)
        monkeypatch.setattr(Path, "read_bytes", record)
        for step in range(1, 5):
            await manager.publish_delta(SealedTickDelta(
                step=step, replacements=(), appends=(),
                write_epoch_ids=(f"epoch:{step}",)), object())
        assert reads == [], f"完整发布重新读取了 {len(reads)} 份历史清单"
        assert set(world._committed_memory_epoch_ids) == {f"epoch:{s}" for s in range(1, 5)}
    finally:
        manager.close()
        world.event_logger.close()


def test_agent_memories_share_readonly_world_commit_view(tmp_path):
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    memories = [Memory.__new__(Memory) for _ in range(3)]
    for index, memory in enumerate(memories):
        world._agent_cache[str(index)] = SimpleNamespace(_memory=memory)
    supplied = {f"epoch:{s}" for s in range(1000)}
    try:
        world.set_memory_checkpoint_view(
            target_step=1000, branch_id="main", branch_lineage=[],
            committed_write_epoch_ids=supplied)
        view = world._committed_memory_epoch_ids
        assert all(memory._committed_write_epoch_ids is view for memory in memories)
        supplied.add("uncommitted")
        assert "uncommitted" not in view
        assert not hasattr(view, "add")
        assert len(view) == 1000
    finally:
        world.event_logger.close()


@pytest.mark.asyncio
async def test_unpublished_epochs_are_visible_only_until_discard(tmp_path, monkeypatch):
    world, manager = configured(tmp_path, checkpoint_every=2)
    try:
        await manager.publish_root(world, object())
        first = SealedTickDelta(step=1, replacements=(), appends=(), write_epoch_ids=("pending:1",))
        assert await manager.publish_delta(first, object()) is None
        assert "pending:1" in world._committed_memory_epoch_ids
        def fail(*args, **kwargs):
            raise OSError("发布中断")
        monkeypatch.setattr(manager._v4_store, "publish", fail)
        with pytest.raises(OSError):
            await manager.publish_delta(SealedTickDelta(
                step=2, replacements=(), appends=(), write_epoch_ids=("failed:2",)), object())
        manager.discard_unpublished_epoch()
        assert set(world._committed_memory_epoch_ids) == set()
        assert V4CheckpointStore(tmp_path / "run").available_steps() == [0]
    finally:
        manager.close()
        world.event_logger.close()

@pytest.mark.asyncio
async def test_publication_does_not_iterate_accumulated_epoch_ids(tmp_path):
    class MembershipOnlyIndex(dict):
        def __iter__(self):
            raise AssertionError("发布正在复制累计 epoch 集合")
    world, manager = configured(tmp_path)
    try:
        await manager.publish_root(world, object())
        manager._v4_committed_memory_epoch_ids = MembershipOnlyIndex.fromkeys(
            (f"past:{step}" for step in range(1000)), 0)
        await manager.publish_delta(SealedTickDelta(
            step=1, replacements=(), appends=(), write_epoch_ids=("new:1",)), object())
        assert "past:999" in world._committed_memory_epoch_ids
        assert "new:1" in world._committed_memory_epoch_ids
        assert len(world._committed_memory_epoch_ids) == 1001
    finally:
        manager.close()
        world.event_logger.close()

@pytest.mark.asyncio
async def test_retained_memory_view_keeps_its_complete_boundary(tmp_path):
    world, manager = configured(tmp_path)
    historical = Memory.__new__(Memory)
    historical._active_write_epoch_id = None
    try:
        await manager.publish_root(world, object())
        root_view = world._committed_memory_epoch_ids
        historical.set_memory_view(0, [], root_view)
        await manager.publish_delta(SealedTickDelta(
            step=1, replacements=(), appends=(), write_epoch_ids=("later:1",)), object())
        assert not historical._is_epoch_visible({"write_epoch_id": "later:1", "created_step": 0})
        assert len(root_view) == 0 and list(root_view) == []
        assert "later:1" in world._committed_memory_epoch_ids
        assert world._committed_memory_epoch_ids | {"extra"} == {"later:1", "extra"}
    finally:
        manager.close()
        world.event_logger.close()

@pytest.mark.asyncio
async def test_pending_view_remains_stable_after_publication(tmp_path):
    world, manager = configured(tmp_path, checkpoint_every=2)
    try:
        await manager.publish_root(world, object())
        await manager.publish_delta(SealedTickDelta(
            step=1, replacements=(), appends=(), write_epoch_ids=("pending:1",)), object())
        pending_view = world._committed_memory_epoch_ids
        await manager.publish_delta(SealedTickDelta(
            step=2, replacements=(), appends=(), write_epoch_ids=("next:2",)), object())
        assert set(pending_view) == {"pending:1"}
        assert len(pending_view) == 1
        assert set(world._committed_memory_epoch_ids) == {"pending:1", "next:2"}
    finally:
        manager.close()
        world.event_logger.close()
