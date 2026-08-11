"""PersistenceManager v4 的最小垂直切片红灯测试。

这些用例先冻结 manager 层合同，当前实现尚未提供 ``configure_v4``、
``publish_root`` 和 ``publish_delta``，因此在实现前应保持红灯。测试只从
``SealedTickDelta`` 消费增量，不用保存前后 World 的差异推导 expected delta。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from pathlib import Path
from typing import Any

import pytest

from society0.core_data import World
from society0.incremental_checkpoint import PersistenceSchema, V4CheckpointStore
from society0.persistence import PersistenceManager
from society0.schedule import CodeSchedule


def _declarations() -> PersistenceSchema:
    return PersistenceSchema.compile(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "price": {
                    "type": "integer",
                    "persistence": {"kind": "replaceable"},
                },
                "facts_by_id": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                    "persistence": {"kind": "append_only_map"},
                },
                "facts": {
                    "type": "array",
                    "items": {"type": "object"},
                    "persistence": {"kind": "append_only_list"},
                },
                "cursor": {
                    "type": "integer",
                    "default": 0,
                    "persistence": {"kind": "transient"},
                },
            },
        },
        root_path=("environment", "state"),
    )


def _world(tmp_path: Path, *, name: str = "world") -> World:
    world = World(step=0, event_log_path=str(tmp_path / f"{name}.events.jsonl"))
    world.environment_data["type"] = "plain"
    world.environment_data["state"] = {
        "price": 10,
        "facts_by_id": {},
        "facts": [],
        "cursor": 0,
    }
    return world


def _configure(
    manager: PersistenceManager,
    world: World,
    *,
    checkpoint_every: int = 1,
) -> CodeSchedule:
    manager.configure_v4(
        world,
        _declarations(),
        checkpoint_every=checkpoint_every,
    )
    return CodeSchedule()


def _seal_delta(world: World, step: int, *, price: int, fact_id: str) -> Any:
    world.begin_persistence_tick(step)
    state = world.create_environment_state_proxy()
    state["price"] = price
    state["facts_by_id"][fact_id] = {"step": step}
    state["facts"].append({"id": fact_id})
    # transient 字段可以运行时变化，但不应进入 replacement/segment。
    state["cursor"] = step * 100
    return world.seal_persistence_tick()


def _state(world: World) -> dict[str, Any]:
    return world.environment_data["state"]


def _close(manager: PersistenceManager, *worlds: World) -> None:
    for world in worlds:
        world.event_logger.close()
    manager.close()


@pytest.mark.asyncio
async def test_v4_root_then_two_deltas_restore_any_complete_tick(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        root_marker = await manager.publish_root(world, schedule)
        delta_one = _seal_delta(world, 1, price=11, fact_id="f1")
        marker_one = await manager.publish_delta(delta_one, schedule)
        delta_two = _seal_delta(world, 2, price=12, fact_id="f2")
        marker_two = await manager.publish_delta(delta_two, schedule)

        assert root_marker["checkpoint_version"] == "complete_step_v4"
        assert marker_one["step"] == 1
        assert marker_two["step"] == 2

        expected = {
            0: {"price": 10, "facts_by_id": {}, "facts": [], "cursor": 0},
            1: {
                "price": 11,
                "facts_by_id": {"f1": {"step": 1}},
                "facts": [{"id": "f1"}],
                "cursor": 0,
            },
            2: {
                "price": 12,
                "facts_by_id": {
                    "f1": {"step": 1},
                    "f2": {"step": 2},
                },
                "facts": [{"id": "f1"}, {"id": "f2"}],
                "cursor": 0,
            },
        }
        for step, expected_state in expected.items():
            restored, _ = await manager.load_checkpoint(step, restore_chroma=False)
            assert restored.step == step
            assert _state(restored) == expected_state
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_v4_publication_is_marker_manifest_replacement_segments_without_v3_components(
    tmp_path,
):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        delta = _seal_delta(world, 1, price=11, fact_id="f1")
        await manager.publish_delta(delta, schedule)

        record = manager.resolve_checkpoint(1)
        assert set(record) == {
            "step",
            "checkpoint_id",
            "marker",
            "manifest",
            "marker_file",
            "manifest_file",
        }
        marker = record["marker"]
        manifest = record["manifest"]
        assert marker["checkpoint_version"] == "complete_step_v4"
        assert manifest["checkpoint_version"] == "complete_step_v4"
        assert marker["manifest_file"].startswith("checkpoints/v4/manifests/")
        assert manifest["replacement_file"].startswith("checkpoints/v4/replacements/")
        assert isinstance(manifest["new_segments"], list)
        assert "world_file" not in marker
        assert "chroma_backup" not in marker
        assert "world_file" not in manifest
        assert "chroma_backup" not in manifest
        assert not list((tmp_path / "checkpoints" / "complete").glob("step_*.json"))
        assert not any(
            path.is_file() for path in (tmp_path / "chroma_backups").rglob("*")
        )
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_publish_delta_has_no_world_parameter_and_does_not_read_world_after_root(
    tmp_path,
):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        delta = _seal_delta(world, 1, price=11, fact_id="f1")

        assert "world" not in inspect.signature(manager.publish_delta).parameters

        class ExplodingMap(dict):
            def _explode(self, *_args, **_kwargs):
                raise AssertionError("publish_delta read the complete World")

            __getitem__ = _explode
            get = _explode
            items = _explode
            keys = _explode
            values = _explode
            __iter__ = _explode
            __len__ = _explode

        # 这两个映射只在 root 之后替换；delta 已经封存，后续发布不得再
        # 访问 World 的完整状态来发现变化。
        world.environment_data = ExplodingMap()
        world.agents_data = ExplodingMap()
        await manager.publish_delta(delta, schedule)
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_latest_skips_corrupt_newest_marker_but_explicit_step_rejects_it(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        await manager.publish_delta(_seal_delta(world, 1, price=11, fact_id="f1"), schedule)
        await manager.publish_delta(_seal_delta(world, 2, price=12, fact_id="f2"), schedule)

        newest = tmp_path / "checkpoints" / "v4" / "complete" / "step_000002.json"
        payload = json.loads(newest.read_text(encoding="utf-8"))
        payload["manifest_sha256"] = "0" * 64
        newest.write_text(json.dumps(payload), encoding="utf-8")

        latest = manager.resolve_checkpoint()
        assert latest["step"] == 1
        with pytest.raises((ValueError, FileNotFoundError), match="manifest|hash|mismatch"):
            manager.resolve_checkpoint(2)
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_v3_marker_is_explicitly_rejected_after_v4_cutover(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        marker_path = tmp_path / "checkpoints" / "v4" / "complete" / "step_000000.json"
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        marker["checkpoint_version"] = "complete_step_v3"
        marker_path.write_text(json.dumps(marker), encoding="utf-8")

        with pytest.raises(ValueError, match="Unsupported|version|v3"):
            manager.resolve_checkpoint(0)
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_marker_failure_keeps_previous_checkpoint_and_does_not_publish_new_step(
    tmp_path,
    monkeypatch,
):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        old_record = manager.resolve_checkpoint(0)
        old_marker_bytes = Path(old_record["marker_file"]).read_bytes()

        real_atomic_write = V4CheckpointStore._atomic_write

        def fail_new_marker(path: Path, data: bytes) -> int:
            if path.parent.name == "complete" and path.name == "step_000001.json":
                raise OSError("injected v4 marker failure")
            return real_atomic_write(path, data)

        monkeypatch.setattr(
            V4CheckpointStore,
            "_atomic_write",
            staticmethod(fail_new_marker),
        )
        delta = _seal_delta(world, 1, price=11, fact_id="f1")
        with pytest.raises(OSError, match="marker failure"):
            await manager.publish_delta(delta, schedule)

        assert manager.resolve_checkpoint()["step"] == 0
        assert Path(old_record["marker_file"]).read_bytes() == old_marker_bytes
        assert not (
            tmp_path / "checkpoints" / "v4" / "complete" / "step_000001.json"
        ).exists()
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_error_after_marker_rename_is_reported_as_committed_success(tmp_path, monkeypatch):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        real_atomic_write = V4CheckpointStore._atomic_write

        def raise_after_marker_rename(path: Path, data: bytes) -> int:
            written = real_atomic_write(path, data)
            if path.parent.name == "complete" and path.name == "step_000001.json":
                raise OSError("post-rename directory notification failed")
            return written

        monkeypatch.setattr(
            V4CheckpointStore,
            "_atomic_write",
            staticmethod(raise_after_marker_rename),
        )
        marker = await manager.publish_delta(
            _seal_delta(world, 1, price=11, fact_id="f1"),
            schedule,
        )

        assert marker is not None and marker["step"] == 1
        assert manager.resolve_checkpoint()["step"] == 1
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_async_writer_applies_backpressure_to_second_publish(tmp_path, monkeypatch):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        delta_one = _seal_delta(world, 1, price=11, fact_id="f1")
        delta_two = _seal_delta(world, 2, price=12, fact_id="f2")

        entered = threading.Event()
        release = threading.Event()
        real_publish = V4CheckpointStore.publish

        def delayed_publish(
            store: V4CheckpointStore,
            delta: Any,
            **components: Any,
        ) -> dict[str, Any]:
            if not entered.is_set():
                entered.set()
                # If an implementation incorrectly runs the synchronous writer
                # on the event loop, the bounded timeout fails the test quickly
                # rather than hanging the whole primary suite.
                release.wait(timeout=0.5)
            return real_publish(store, delta, **components)

        monkeypatch.setattr(V4CheckpointStore, "publish", delayed_publish)
        first = asyncio.create_task(manager.publish_delta(delta_one, schedule))
        await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), timeout=1.5)
        second = asyncio.create_task(manager.publish_delta(delta_two, schedule))
        await asyncio.sleep(0.05)
        assert not second.done(), "a concurrent save must wait behind the active writer"
        release.set()
        await asyncio.wait_for(asyncio.gather(first, second), timeout=3)
        assert manager.resolve_checkpoint()["step"] == 2
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_checkpoint_every_epoch_accumulates_ticks_and_discard_drops_unpublished_epoch(
    tmp_path,
):
    manager = PersistenceManager(str(tmp_path / "discard"))
    world = _world(tmp_path / "discard", name="discard")
    schedule = _configure(manager, world, checkpoint_every=2)
    try:
        await manager.publish_root(world, schedule)
        first_delta = _seal_delta(world, 1, price=11, fact_id="f1")
        assert await manager.publish_delta(first_delta, schedule) is None
        manager.discard_unpublished_epoch()
        assert manager.resolve_checkpoint()["step"] == 0
        assert not (
            Path(manager.save_dir) / "checkpoints" / "v4" / "complete" / "step_000001.json"
        ).exists()
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_manager_fork_reuses_history_and_publishes_independent_future(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        await manager.publish_delta(
            _seal_delta(world, 1, price=11, fact_id="shared"),
            schedule,
        )
        source_manifest_count = len(list((tmp_path / "checkpoints" / "v4" / "manifests").glob("*.json")))

        branch = await manager.create_branch(1, "policy")
        branch_world = branch._v4_world
        assert branch_world is not None
        assert len(list((tmp_path / "checkpoints" / "v4" / "manifests").glob("*.json"))) == source_manifest_count
        branch_delta = _seal_delta(branch_world, 2, price=20, fact_id="branch-only")
        await branch.publish_delta(branch_delta, schedule)

        source_delta = _seal_delta(world, 2, price=12, fact_id="source-only")
        await manager.publish_delta(source_delta, schedule)
        source_restored, _ = await manager.load_checkpoint(2, restore_chroma=False)
        branch_restored, _ = await branch.load_checkpoint(2, restore_chroma=False)
        assert _state(source_restored)["price"] == 12
        assert _state(branch_restored)["price"] == 20
        assert set(_state(source_restored)["facts_by_id"]) == {"shared", "source-only"}
        assert set(_state(branch_restored)["facts_by_id"]) == {"shared", "branch-only"}
    finally:
        if "branch" in locals():
            branch.close()
        _close(manager, world)

    manager = PersistenceManager(str(tmp_path / "commit"))
    world = _world(tmp_path / "commit", name="commit")
    schedule = _configure(manager, world, checkpoint_every=2)
    try:
        await manager.publish_root(world, schedule)
        first_delta = _seal_delta(world, 1, price=11, fact_id="f1")
        second_delta = _seal_delta(world, 2, price=12, fact_id="f2")
        assert await manager.publish_delta(first_delta, schedule) is None
        marker = await manager.publish_delta(second_delta, schedule)
        assert marker is not None and marker["step"] == 2
        restored, _ = await manager.load_checkpoint(2, restore_chroma=False)
        assert _state(restored) == {
            "price": 12,
            "facts_by_id": {"f1": {"step": 1}, "f2": {"step": 2}},
            "facts": [{"id": "f1"}, {"id": "f2"}],
            "cursor": 0,
        }
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_source_restore_only_reads_source_tree(tmp_path):
    source_dir = tmp_path / "source"
    source = PersistenceManager(str(source_dir))
    source_world = _world(tmp_path, name="source")
    source_schedule = _configure(source, source_world)
    destination = PersistenceManager(str(tmp_path / "destination"))
    try:
        await source.publish_root(source_world, source_schedule)
        await source.publish_delta(
            _seal_delta(source_world, 1, price=11, fact_id="f1"),
            source_schedule,
        )
        before = {
            path.relative_to(source_dir).as_posix(): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in source_dir.rglob("*")
            if path.is_file()
        }

        restored, _ = await destination.load_checkpoint_from(
            source_dir,
            step=1,
            restore_chroma=False,
        )
        assert restored.step == 1
        assert _state(restored)["price"] == 11
        after = {
            path.relative_to(source_dir).as_posix(): (
                path.read_bytes(),
                path.stat().st_mtime_ns,
            )
            for path in source_dir.rglob("*")
            if path.is_file()
        }
        assert after == before
    finally:
        source_world.event_logger.close()
        source.close()
        destination.close()
