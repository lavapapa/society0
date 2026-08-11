"""PersistenceManager 的 v4 恢复合同。

旧测试围绕 ``world_file``、``complete/`` 和每 checkpoint 的 Chroma 目录，
这些路径属于已经删除的 v3 实现。这里仅验证 v4 manifest/segment 链、明确
拒绝旧格式以及诊断快照与可恢复 checkpoint 的边界。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from society0.core_data import World
from society0.incremental_checkpoint import PersistenceSchema, V4CheckpointStore
from society0.persistence import PersistenceManager
from society0.schedule import CodeSchedule


def _schema() -> PersistenceSchema:
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
    manager.configure_v4(world, _schema(), checkpoint_every=checkpoint_every)
    return CodeSchedule()


def _seal_delta(world: World, step: int, *, price: int, fact_id: str) -> Any:
    world.begin_persistence_tick(step)
    state = world.create_environment_state_proxy()
    state["price"] = price
    state["facts_by_id"][fact_id] = {"step": step}
    state["facts"].append({"id": fact_id})
    state["cursor"] = step * 100
    return world.seal_persistence_tick()


def _state(world: World) -> dict[str, Any]:
    return world.environment_data["state"]


def _close(manager: PersistenceManager, *worlds: World) -> None:
    for world in worlds:
        world.event_logger.close()
    manager.close()


@pytest.mark.asyncio
async def test_checkpoint_step_inputs_reject_bool_float_and_string(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    try:
        invalid_steps = (True, False, 1.0, "1")
        for invalid in invalid_steps:
            with pytest.raises(ValueError, match="step"):
                manager.resolve_checkpoint(invalid)
            with pytest.raises(ValueError, match="step"):
                PersistenceManager.resolve_checkpoint_from(tmp_path, invalid)
    finally:
        manager.close()


def test_v3_recovery_symbols_and_directory_contract_are_removed(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    try:
        assert manager.CHECKPOINT_VERSION == V4CheckpointStore.VERSION
        assert not (tmp_path / "chroma_backups").exists()
        for obsolete in (
            "_complete_marker_path",
            "_checkpoint_file_path",
            "_chroma_backup_path",
            "_backup_chroma_store",
            "_restore_chroma_store",
            "replay_events_from_checkpoint",
            "_apply_event_to_world",
        ):
            assert not hasattr(manager, obsolete), obsolete
    finally:
        manager.close()


def test_save_checkpoint_world_snapshot_api_is_removed(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    try:
        assert not hasattr(manager, "save_checkpoint")
        assert not (tmp_path / "checkpoints" / "complete").exists()
        assert not (tmp_path / "chroma_backups").exists()
    finally:
        manager.close()


@pytest.mark.asyncio
async def test_v4_root_and_deltas_restore_any_complete_tick_without_v3_components(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        root_marker = await manager.publish_root(world, schedule)
        marker_one = await manager.publish_delta(
            _seal_delta(world, 1, price=11, fact_id="f1"), schedule
        )
        marker_two = await manager.publish_delta(
            _seal_delta(world, 2, price=12, fact_id="f2"), schedule
        )

        assert root_marker["checkpoint_version"] == V4CheckpointStore.VERSION
        assert marker_one["step"] == 1
        assert marker_two["step"] == 2
        assert not (tmp_path / "checkpoints" / "complete").exists()
        assert not (tmp_path / "chroma_backups").exists()

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
async def test_v4_record_has_manifest_and_segments_only(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        await manager.publish_delta(
            _seal_delta(world, 1, price=11, fact_id="f1"), schedule
        )
        record = manager.resolve_checkpoint(1)
        assert set(record) == {
            "step",
            "checkpoint_id",
            "marker",
            "manifest",
            "marker_file",
            "manifest_file",
        }
        assert record["marker"]["checkpoint_version"] == V4CheckpointStore.VERSION
        assert record["manifest"]["checkpoint_version"] == V4CheckpointStore.VERSION
        assert record["manifest"]["replacement_file"].startswith(
            "checkpoints/v4/replacements/"
        )
        assert isinstance(record["manifest"]["new_segments"], list)
        for obsolete in ("world_file", "chroma_backup", "world_encoding"):
            assert obsolete not in record["marker"]
            assert obsolete not in record["manifest"]
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_v3_marker_under_v4_tree_is_explicitly_rejected(tmp_path):
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
async def test_latest_skips_corrupt_newest_v4_marker_but_explicit_step_rejects_it(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    schedule = _configure(manager, world)
    try:
        await manager.publish_root(world, schedule)
        await manager.publish_delta(
            _seal_delta(world, 1, price=11, fact_id="f1"), schedule
        )
        await manager.publish_delta(
            _seal_delta(world, 2, price=12, fact_id="f2"), schedule
        )
        newest = tmp_path / "checkpoints" / "v4" / "complete" / "step_000002.json"
        payload = json.loads(newest.read_text(encoding="utf-8"))
        payload["manifest_sha256"] = "0" * 64
        newest.write_text(json.dumps(payload), encoding="utf-8")

        assert manager.resolve_checkpoint()["step"] == 1
        with pytest.raises((ValueError, FileNotFoundError), match="manifest|hash|mismatch"):
            manager.resolve_checkpoint(2)
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_diagnostic_snapshot_is_nonrecoverable_and_does_not_publish_v4_marker(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    try:
        path = await manager.save_diagnostic_checkpoint(world)
        assert path.is_file()
        payload = json.loads(__import__("gzip").decompress(path.read_bytes()))
        assert payload["diagnostic"] is True
        assert payload["recoverable"] is False
        assert not (tmp_path / "checkpoints" / "v4" / "complete").exists()
        assert not (tmp_path / "chroma_backups").exists()
        with pytest.raises(FileNotFoundError, match="No complete v4 checkpoints"):
            await manager.load_checkpoint(None)
    finally:
        _close(manager, world)


@pytest.mark.asyncio
async def test_publish_delta_signature_has_no_world_snapshot_parameter(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    try:
        assert "world" not in inspect.signature(manager.publish_delta).parameters
    finally:
        manager.close()


@pytest.mark.asyncio
async def test_source_restore_reads_only_v4_source_tree(tmp_path):
    source_dir = tmp_path / "source"
    source = PersistenceManager(str(source_dir))
    source_world = _world(tmp_path, name="source")
    source_schedule = _configure(source, source_world)
    destination = PersistenceManager(str(tmp_path / "destination"))
    try:
        await source.publish_root(source_world, source_schedule)
        await source.publish_delta(
            _seal_delta(source_world, 1, price=11, fact_id="f1"), source_schedule
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
            source_dir, step=1, restore_chroma=False
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
        _close(source, source_world)
        destination.close()
