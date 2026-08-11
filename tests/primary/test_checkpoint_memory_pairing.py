import asyncio
from collections import UserDict
import gzip
import json
import threading
import time

import pytest

from tests import read_gzip_json

from society0 import EmbedModel, LLMModel, Society0
from society0.core_data import World
from society0.decorators import action, env_type
from society0.environment import Environment
from society0.persistence import PersistenceManager
from society0.schedule import CodeSchedule
from society0.sim_engine import SimEngine


@pytest.mark.asyncio
async def test_checkpoint_step_inputs_reject_bool_float_and_string(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    invalid_steps = (True, False, 1.0, "1")
    for invalid in invalid_steps:
        with pytest.raises(ValueError, match="step"):
            manager.resolve_checkpoint(invalid)
        with pytest.raises(ValueError, match="step"):
            PersistenceManager.resolve_checkpoint_from(tmp_path, invalid)
        with pytest.raises(ValueError, match="step"):
            manager._complete_marker_path(invalid)
        with pytest.raises(ValueError, match="step"):
            manager._checkpoint_file_path(invalid)
        with pytest.raises(ValueError, match="step"):
            manager._chroma_backup_path(invalid)
        with pytest.raises(ValueError, match="step"):
            await manager._restore_chroma_store(
                invalid,
                checkpoint_id="checkpoint-test",
                memory_required=False,
                backup_dir=None,
                use_default_backup=False,
            )
    manager.close()


@pytest.mark.asyncio
async def test_v2_checkpoint_marker_is_rejected_after_v3_schema_cutover(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    await manager.save_checkpoint(world, CodeSchedule())

    marker_path = tmp_path / "checkpoints" / "complete" / "step_000000.json"
    marker = _read(marker_path)
    marker["checkpoint_version"] = "complete_step_v2"
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported checkpoint version"):
        manager.resolve_checkpoint(0)
    manager.close()


def _config():
    return {
        "agent_types": [
            {
                "id": "participant",
                "archetype": "rule",
                "persona": "type persona",
            }
        ],
        "agents": [
            {
                "id": "participant_0",
                "type": "participant",
                "persona": "instance persona",
                "model": "research-model",
                "state": {"count": 0},
                "properties": {"cohort": "A"},
            }
        ],
        "environment": {
            "type": "plain",
            "config": {"label": "checkpoint-test"},
            "state": {"round": 1},
            "state_schema": {"round": {"type": "integer"}},
        },
        "globals": {"seed": 7},
    }


def _read(path):
    if path.name.endswith(".json.gz"):
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, payload):
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if path.name.endswith(".json.gz"):
        path.write_bytes(gzip.compress(encoded, compresslevel=6, mtime=0))
        return
    path.write_bytes(encoded)


def _read_text(path):
    if path.name.endswith(".json.gz"):
        return gzip.decompress(path.read_bytes()).decode("utf-8")
    return path.read_text(encoding="utf-8")


def _checkpoint_components(run_dir, step):
    """Resolve immutable component paths through the published marker."""
    marker_path = run_dir / "checkpoints" / "complete" / f"step_{step:06d}.json"
    marker = _read(marker_path)
    world_path = run_dir / "checkpoints" / marker["world_file"]
    backup_name = marker.get("chroma_backup")
    backup_path = (
        run_dir / "chroma_backups" / backup_name
        if backup_name is not None
        else None
    )
    return marker_path, world_path, backup_path, marker


@pytest.mark.asyncio
async def test_checkpoint_persists_derived_annotations(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    run_dir = tmp_path / "annotations"
    engine = Society0(
        save_dir=str(run_dir),
        base_config=_config(),
        checkpoint_every=1,
    )

    @engine.step(name="annotate")
    async def annotate(ctx):
        ctx.world.set_checkpoint_annotation(
            "industry_tick_memory",
            {"status": "partial", "memory_records_written": 1},
        )
        return ctx.result()

    await engine.run(steps=1)

    _, checkpoint_path, _, _ = _checkpoint_components(run_dir, 1)
    checkpoint = _read(checkpoint_path)
    assert checkpoint["world_metadata"]["annotations"] == {
        "industry_tick_memory": {
            "status": "partial",
            "memory_records_written": 1,
        }
    }

    restored, _ = await engine.persistence_manager.load_checkpoint(
        1,
        restore_chroma=True,
    )
    assert restored.checkpoint_annotations() == checkpoint["world_metadata"][
        "annotations"
    ]


@env_type(type_name="resume_identity_env", config_schema={}, state_schema={})
class _ResumeIdentityEnvironmentA(Environment):
    pass


@env_type(type_name="resume_identity_env", config_schema={}, state_schema={})
class _ResumeIdentityEnvironmentB(Environment):
    @action(description="A capability added by changed runtime code.")
    def changed_action(self):
        return {"ok": True}


@pytest.mark.asyncio
async def test_complete_checkpoint_pairs_world_chroma_and_marker_and_restores_latest(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source"
    engine = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        checkpoint_every=1,
    )

    @engine.step(name="increment")
    async def increment(ctx):
        ctx.world.agents_data["participant_0"]["state"]["count"] += 1
        return ctx.result()

    await engine.run(steps=1)

    marker_path, checkpoint_path, chroma_path, marker = _checkpoint_components(
        source_run,
        1,
    )
    checkpoint = _read(checkpoint_path)
    assert chroma_path is not None
    chroma_manifest = _read(chroma_path / "_checkpoint.json")
    final_snapshot = read_gzip_json(
        source_run / "checkpoints" / "checkpoint_final.json.gz"
    )

    assert marker["complete"] is True
    assert marker["recoverable"] is True
    assert marker["checkpoint_version"] == "complete_step_v3"
    assert marker["world_encoding"] == "gzip-json"
    assert checkpoint_path.name.endswith(".json.gz")
    assert checkpoint_path.read_bytes().startswith(b"\x1f\x8b")
    assert not list((source_run / "checkpoints").glob("checkpoint_[0-9]*.json"))
    assert marker["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert marker["checkpoint_id"] == chroma_manifest["checkpoint_id"]
    assert marker["step"] == checkpoint["step"] == chroma_manifest["step"] == 1
    assert marker["world_sha256"] == PersistenceManager._file_sha256(
        checkpoint_path
    )
    assert marker["chroma_sha256"] == chroma_manifest["content_sha256"]
    assert checkpoint["agents_data"]["participant_0"] == {
        "id": "participant_0",
        "type": "participant",
        "archetype": "rule",
        "state": {"count": 1},
        "properties": {"cohort": "A"},
        "reminders": [],
        "persona": "instance persona",
        "persona_instance": "instance persona",
        "persona_type": "type persona",
        "model": "research-model",
    }
    assert checkpoint["environment_data"]["config"] == {"label": "checkpoint-test"}
    assert checkpoint["environment_data"]["schema"] == {"round": {"type": "integer"}}
    assert checkpoint["environment_data"]["globals"] == {"seed": 7}
    assert final_snapshot["recoverable"] is False
    assert final_snapshot["diagnostic"] is True

    destination_run = tmp_path / "destination"
    resumed = Society0(
        save_dir=str(destination_run),
        base_config=_config(),
        source_run=source_run,
        checkpoint_every=1,
    )

    @resumed.step(name="continue")
    async def continue_step(ctx):
        ctx.world.agents_data["participant_0"]["state"]["count"] += 10
        return ctx.result()

    await resumed.run(steps=1)

    assert resumed.restored_checkpoint["step"] == 1
    _, continued_path, _, _ = _checkpoint_components(destination_run, 2)
    continued = _read(continued_path)
    assert continued["agents_data"]["participant_0"]["state"]["count"] == 11

    explicit = Society0(
        save_dir=str(tmp_path / "explicit"),
        base_config=_config(),
        source_run=source_run,
        source_step=0,
    )
    restored_step = await explicit.restore(source_run, step=0)
    assert restored_step == 0
    assert explicit.current_world_state.agents_data["participant_0"]["state"]["count"] == 0
    explicit.event_logger.close()
    explicit.persistence_manager.close()


@pytest.mark.asyncio
async def test_world_checkpoint_has_single_authority_and_compresses_off_event_loop(
    tmp_path,
    monkeypatch,
):
    """World/environment/agents are not copied into derived observations."""
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    worker_threads = []
    original_writer = manager._write_world_checkpoint_temp

    def record_writer(*args, **kwargs):
        worker_threads.append(threading.current_thread().name)
        return original_writer(*args, **kwargs)

    monkeypatch.setattr(manager, "_write_world_checkpoint_temp", record_writer)
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    await manager.save_checkpoint(
        world,
        CodeSchedule(),
        step_metrics={"step_number": 0, "nodes_executed": 1},
    )

    record = manager.resolve_checkpoint(0)
    payload = record["checkpoint_data"]
    observation = payload["observation_data"]
    assert "agents_data" in payload
    assert "environment_data" in payload
    assert "agents_data" not in observation
    assert "environment_data" not in observation
    assert "metrics" not in payload
    assert payload["step_metrics"] == {
        "step_number": 0,
        "nodes_executed": 1,
    }
    assert worker_threads
    assert all(name != threading.current_thread().name for name in worker_threads)
    manager.close()


@pytest.mark.asyncio
async def test_save_uses_worker_hash_and_resolve_rehashes_published_world(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    calls = []
    real_file_sha256 = PersistenceManager._file_sha256

    def record_file_sha256(path):
        calls.append(path)
        return real_file_sha256(path)

    monkeypatch.setattr(
        PersistenceManager,
        "_file_sha256",
        staticmethod(record_file_sha256),
    )
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    marker = await manager.save_checkpoint(world, CodeSchedule())

    assert calls == []
    assert marker["world_sha256"]
    manager.resolve_checkpoint(0)
    assert calls
    manager.close()


@pytest.mark.asyncio
async def test_checkpoint_does_not_traverse_environment_state_for_derived_snapshot(
    tmp_path,
    monkeypatch,
):
    """The base Environment snapshot must not build a discarded state copy."""
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")

    class ReadOnceState(UserDict):
        def __init__(self, *args, **kwargs):
            self.reads = 0
            super().__init__(*args, **kwargs)

        def keys(self):
            self.reads += 1
            if self.reads > 1:
                raise AssertionError("environment state was traversed more than once")
            return super().keys()

    manager = PersistenceManager(str(tmp_path))
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.environment_data["state"] = ReadOnceState({"large": {"value": 1}})
    await manager.save_checkpoint(world, CodeSchedule())

    assert world.environment_data["state"].reads == 1
    checkpoint = manager.resolve_checkpoint(0)["checkpoint_data"]
    assert "state" not in checkpoint["environment_data"]["snapshot"]
    manager.close()


@pytest.mark.asyncio
async def test_checkpoint_compression_cancellation_waits_for_worker_cleanup(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    started = threading.Event()
    original_writer = manager._write_world_checkpoint_temp

    def slow_writer(*args, **kwargs):
        started.set()
        time.sleep(0.05)
        return original_writer(*args, **kwargs)

    monkeypatch.setattr(manager, "_write_world_checkpoint_temp", slow_writer)
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    save_task = asyncio.create_task(manager.save_checkpoint(world, CodeSchedule()))
    await asyncio.to_thread(started.wait, 2)
    save_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await save_task

    assert not list(manager.checkpoints_dir.glob(".*.tmp.json.gz"))
    assert await manager.get_available_checkpoints() == []
    manager.close()


@pytest.mark.asyncio
async def test_cancel_after_world_rename_cleans_only_unpublished_replacement(
    tmp_path,
    monkeypatch,
):
    """Cancellation between component rename and marker keeps old pair intact."""

    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    first = await manager.save_checkpoint(world, CodeSchedule())
    marker_path, old_world, old_backup, _ = _checkpoint_components(tmp_path, 0)
    old_marker_bytes = marker_path.read_bytes()

    reached_backup = asyncio.Event()

    async def block_replacement_backup(step, *, checkpoint_id, memory_required):
        del memory_required
        orphan = manager._chroma_backup_path(step, checkpoint_id=checkpoint_id)
        orphan.mkdir(parents=True, exist_ok=True)
        (orphan / "_checkpoint.json").write_text("{}", encoding="utf-8")
        reached_backup.set()
        await asyncio.sleep(3600)
        return orphan

    monkeypatch.setattr(manager, "_backup_chroma_store", block_replacement_backup)
    task = asyncio.create_task(manager.save_checkpoint(world, CodeSchedule()))
    await reached_backup.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert marker_path.read_bytes() == old_marker_bytes
    assert old_world.is_file()
    assert old_backup is not None and old_backup.is_dir()
    assert await manager.get_available_checkpoints() == [0]
    assert sorted(path.name for path in manager.checkpoints_dir.glob("checkpoint_*.json.gz")) == [
        old_world.name
    ]
    assert sorted(path.name for path in manager.chroma_backup_dir.glob("step_*") if path.is_dir()) == [
        old_backup.name
    ]
    assert first["checkpoint_id"] == _read(marker_path)["checkpoint_id"]
    manager.close()


@pytest.mark.asyncio
async def test_missing_memory_backup_is_not_recoverable(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    run_dir = tmp_path / "run"
    manager = PersistenceManager(str(run_dir))
    world = World(step=0)
    world.add_agent_data("llm_0", "participant", archetype="llm")
    world.set_environment_type("plain")
    await manager.save_checkpoint(world, CodeSchedule())
    _, _, backup_dir, _ = _checkpoint_components(run_dir, 0)
    assert backup_dir is not None
    for path in backup_dir.iterdir():
        path.unlink()
    backup_dir.rmdir()

    with pytest.raises(FileNotFoundError, match="(?:Memory|Chroma) backup missing"):
        await manager.load_checkpoint(0)
    assert await manager.get_available_checkpoints() == []
    manager.close()


@pytest.mark.asyncio
async def test_populated_chroma_backup_restores_with_matching_checkpoint_id(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source = PersistenceManager(str(tmp_path / "source-memory"))
    client = source.get_chroma_client()
    collection = client.get_or_create_collection("agent_memory")
    collection.add(
        ids=["memory-1"],
        documents=["remember this"],
        embeddings=[[0.1, 0.2]],
    )

    world = World(step=3)
    world.add_agent_data("llm_0", "participant", archetype="llm")
    world.set_environment_type("plain")
    marker = await source.save_checkpoint(world, CodeSchedule())
    record = source.resolve_checkpoint(3)

    destination = PersistenceManager(str(tmp_path / "destination-memory"))
    await destination._restore_chroma_store(
        3,
        checkpoint_id=marker["checkpoint_id"],
        memory_required=True,
        backup_dir=record["chroma_backup_dir"],
    )
    restored = destination.get_chroma_client().get_collection("agent_memory").get()

    assert restored["ids"] == ["memory-1"]
    assert record["marker"]["memory_required"] is True
    source.close()
    destination.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_point",
    ["threads", "writer", "backup", "marker"],
)
async def test_failure_before_complete_marker_never_publishes_recoverable_checkpoint(
    tmp_path,
    monkeypatch,
    failure_point,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    engine = Society0(save_dir=str(tmp_path), base_config=_config())
    await engine._initialize()
    manager = engine.persistence_manager

    if failure_point == "threads":
        def fail_threads(*args, **kwargs):
            raise OSError("injected thread manifest failure")

        monkeypatch.setattr(
            manager.agent_thread_store,
            "publish_checkpoint_manifest",
            fail_threads,
        )
        expected = "injected thread manifest failure"
    elif failure_point == "writer":
        def fail_world_writer(*args, **kwargs):
            raise OSError("injected world writer failure")

        monkeypatch.setattr(manager, "_write_world_checkpoint_temp", fail_world_writer)
        expected = "injected world writer failure"
    elif failure_point == "backup":
        async def fail_backup(*args, **kwargs):
            raise OSError("injected backup failure")

        monkeypatch.setattr(manager, "_backup_chroma_store", fail_backup)
        expected = "injected backup failure"
    else:
        real_atomic_write = manager._atomic_write_json

        def fail_marker(path, payload, **kwargs):
            if path.parent == manager.complete_checkpoints_dir:
                raise OSError("injected marker failure")
            return real_atomic_write(path, payload, **kwargs)

        monkeypatch.setattr(manager, "_atomic_write_json", fail_marker)
        expected = "injected marker failure"

    with pytest.raises(OSError, match=expected):
        await manager.save_checkpoint(engine.current_world_state, engine.schedule)

    assert await manager.get_available_checkpoints() == []
    assert not list(manager.complete_checkpoints_dir.glob("step_*.json"))
    assert not list(manager.checkpoints_dir.glob("checkpoint_*.json.gz"))
    assert not list(manager.checkpoints_dir.glob(".*.tmp.json.gz"))
    with pytest.raises(FileNotFoundError, match="No complete checkpoints"):
        await manager.load_checkpoint(None)
    engine.event_logger.close()
    manager.close()


@pytest.mark.asyncio
async def test_checkpoint_final_alone_is_diagnostic_and_cannot_be_loaded(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    engine = Society0(save_dir=str(tmp_path), base_config=_config())
    await engine._initialize()
    await engine.persistence_manager.save_diagnostic_checkpoint(engine.current_world_state)

    diagnostic_path = tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    assert diagnostic_path.is_file()
    assert diagnostic_path.read_bytes().startswith(b"\x1f\x8b")
    diagnostic = read_gzip_json(diagnostic_path)
    assert diagnostic["diagnostic"] is True
    assert diagnostic["recoverable"] is False
    assert diagnostic["world_encoding"] == "gzip-json"
    assert await engine.persistence_manager.get_available_checkpoints() == []
    with pytest.raises(FileNotFoundError, match="No complete checkpoints"):
        await engine.persistence_manager.load_checkpoint(None)
    engine.event_logger.close()
    engine.persistence_manager.close()


@pytest.mark.asyncio
async def test_diagnostic_checkpoint_failure_does_not_publish_partial_gzip(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    engine = Society0(save_dir=str(tmp_path), base_config=_config())
    await engine._initialize()
    import society0.persistence as persistence_module

    def fail_dump(_payload, handle, **_kwargs):
        handle.write("{\"partial\":")
        handle.flush()
        raise OSError("injected diagnostic write failure")

    with monkeypatch.context() as patcher:
        patcher.setattr(persistence_module.json, "dump", fail_dump)
        with pytest.raises(OSError, match="injected diagnostic write failure"):
            await engine.persistence_manager.save_diagnostic_checkpoint(
                engine.current_world_state
            )

    diagnostic_path = tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    assert not diagnostic_path.exists()
    assert not list((tmp_path / "checkpoints").glob(".*.tmp.json.gz"))
    assert await engine.persistence_manager.get_available_checkpoints() == []
    engine.event_logger.close()
    engine.persistence_manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["encoding", "compressed_bytes", "rename"])
async def test_world_component_rejects_encoding_corruption_and_rename(
    tmp_path,
    monkeypatch,
    tamper,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    world = World(step=4)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    await manager.save_checkpoint(world, CodeSchedule())
    marker_path, checkpoint_path, _, marker = _checkpoint_components(tmp_path, 4)

    if tamper == "encoding":
        marker["world_encoding"] = "plain-json"
        expected = "Unsupported world checkpoint encoding"
    elif tamper == "compressed_bytes":
        checkpoint_path.write_bytes(b"not a gzip stream")
        marker["world_sha256"] = PersistenceManager._file_sha256(checkpoint_path)
        expected = "gzip JSON is invalid"
    else:
        renamed = checkpoint_path.with_name(f"renamed-{checkpoint_path.name}")
        checkpoint_path.rename(renamed)
        marker["world_file"] = renamed.name
        expected = "filename does not match marker"
    _write(marker_path, marker)

    with pytest.raises(ValueError, match=expected):
        manager.resolve_checkpoint(4)
    manager.close()


@pytest.mark.asyncio
async def test_streaming_broadcast_uses_resolved_checkpoint_data(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    engine = SimEngine(save_dir=str(tmp_path), base_config={})
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    await engine.persistence_manager.save_checkpoint(
        world,
        CodeSchedule(),
    )
    resolved = engine.persistence_manager.resolve_checkpoint(0)

    class RecordingBridge:
        payload = None

        def publish_snapshot(self, **payload):
            self.payload = payload

    bridge = RecordingBridge()
    engine.streaming_bridge = bridge
    await engine._broadcast_latest_snapshot(0)

    assert bridge.payload is not None
    assert bridge.payload["snapshot"] == resolved["checkpoint_data"]
    assert bridge.payload["checkpoint_path"].endswith(".json.gz")
    engine.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["backup", "marker"])
async def test_failed_replacement_preserves_previous_complete_checkpoint(
    tmp_path,
    monkeypatch,
    failure_point,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    engine = Society0(save_dir=str(tmp_path), base_config=_config())
    await engine._initialize()
    manager = engine.persistence_manager
    first = await manager.save_checkpoint(engine.current_world_state, engine.schedule)

    if failure_point == "backup":
        async def fail_backup(*args, **kwargs):
            raise OSError("replacement backup failure")

        monkeypatch.setattr(manager, "_backup_chroma_store", fail_backup)
        expected = "replacement backup failure"
    else:
        real_atomic_write = manager._atomic_write_json

        def fail_marker(path, payload, **kwargs):
            if path.parent == manager.complete_checkpoints_dir:
                raise OSError("replacement marker failure")
            return real_atomic_write(path, payload, **kwargs)

        monkeypatch.setattr(manager, "_atomic_write_json", fail_marker)
        expected = "replacement marker failure"

    with pytest.raises(OSError, match=expected):
        await manager.save_checkpoint(engine.current_world_state, engine.schedule)

    restored = manager.resolve_checkpoint(0)
    assert restored["checkpoint_id"] == first["checkpoint_id"]
    assert await manager.get_available_checkpoints() == [0]
    engine.event_logger.close()
    manager.close()


@pytest.mark.asyncio
async def test_cross_run_restore_reads_source_without_initializing_or_writing_it(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-read-only"
    source = Society0(save_dir=str(source_run), base_config=_config())

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)

    def source_snapshot():
        return {
            path.relative_to(source_run).as_posix(): (
                path.stat().st_ino,
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in source_run.rglob("*")
            if path.is_file()
        }

    before = source_snapshot()
    destination = Society0(
        save_dir=str(tmp_path / "destination"),
        base_config=_config(),
        source_run=source_run,
    )

    def reject_manager_construction(*args, **kwargs):
        raise AssertionError("source restore must not construct another PersistenceManager")

    monkeypatch.setattr(PersistenceManager, "__init__", reject_manager_construction)
    restored_step = await destination.restore(source_run)

    assert restored_step == 0
    assert destination.restored_checkpoint["marker"]["complete"] is True
    assert source_snapshot() == before
    assert not any(path.name.endswith(".tmp") for path in source_run.rglob("*"))
    destination.event_logger.close()
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_memory_requirement_is_recomputed_from_world_agents(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    await manager.save_checkpoint(world, CodeSchedule())

    marker_path, checkpoint_path, _, _ = _checkpoint_components(tmp_path, 0)
    checkpoint = _read(checkpoint_path)
    checkpoint["agents_data"]["rule_0"]["archetype"] = "llm"
    _write(checkpoint_path, checkpoint)
    marker = _read(marker_path)
    marker["world_sha256"] = PersistenceManager._file_sha256(checkpoint_path)
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(ValueError, match="metadata does not match world data"):
        manager.resolve_checkpoint(0)
    manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["world_metadata", "manifest", "backup_content"])
async def test_checkpoint_components_and_hashes_must_match(tmp_path, monkeypatch, tamper):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    await manager.save_checkpoint(world, CodeSchedule())

    marker_path, checkpoint_path, backup_dir, _ = _checkpoint_components(tmp_path, 0)
    assert backup_dir is not None
    if tamper == "world_metadata":
        checkpoint = _read(checkpoint_path)
        checkpoint["world_metadata"]["checkpoint_id"] = "foreign"
        _write(checkpoint_path, checkpoint)
        marker = _read(marker_path)
        marker["world_sha256"] = PersistenceManager._file_sha256(checkpoint_path)
        marker_path.write_text(json.dumps(marker), encoding="utf-8")
        expected = "metadata does not match world data"
    elif tamper == "manifest":
        manifest_path = backup_dir / "_checkpoint.json"
        manifest = _read(manifest_path)
        manifest["memory_required"] = True
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        expected = "Chroma backup does not match"
    else:
        (backup_dir / "injected.bin").write_bytes(b"tampered")
        expected = "Chroma backup does not match"

    with pytest.raises(ValueError, match=expected):
        manager.resolve_checkpoint(0)
    manager.close()


@pytest.mark.asyncio
async def test_destination_chroma_restore_failure_rolls_back_world_and_store(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source"
    source = Society0(save_dir=str(source_run), base_config=_config())
    await source._initialize()
    source.current_world_state.agents_data["participant_0"]["archetype"] = "llm"
    source.current_world_state.step = 2
    (source.persistence_manager.chroma_store_path / "source.bin").write_bytes(b"source")
    await source.persistence_manager.save_checkpoint(source.current_world_state, source.schedule)
    source.event_logger.close()
    source.persistence_manager.close()

    destination = Society0(save_dir=str(tmp_path / "destination"), base_config=_config())
    await destination._initialize()
    old_world = destination.current_world_state
    old_store = destination.persistence_manager.chroma_store_path
    (old_store / "old.bin").write_bytes(b"old")

    def fail_client_creation():
        raise RuntimeError("injected client open failure")

    monkeypatch.setattr(destination.persistence_manager, "_create_chroma_client", fail_client_creation)
    record = PersistenceManager.resolve_checkpoint_from(source_run, 2)
    with pytest.raises(RuntimeError, match="injected client open failure"):
        await destination.persistence_manager._restore_chroma_store(
            2,
            checkpoint_id=record["checkpoint_id"],
            memory_required=record["memory_required"],
            backup_dir=record["chroma_backup_dir"],
            use_default_backup=False,
        )

    assert destination.current_world_state is old_world
    assert (old_store / "old.bin").read_bytes() == b"old"
    assert not (old_store / "source.bin").exists()
    assert destination.persistence_manager._restore_failed is False
    destination.event_logger.close()
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_memoryless_checkpoint_without_versioned_backup_is_not_recoverable(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    engine = Society0(save_dir=str(tmp_path), base_config=_config())
    await engine._initialize()
    manager = engine.persistence_manager
    await manager.save_checkpoint(engine.current_world_state, engine.schedule)

    _, _, backup_dir, _ = _checkpoint_components(tmp_path, 0)
    assert backup_dir is not None
    for path in backup_dir.iterdir():
        path.unlink()
    backup_dir.rmdir()
    marker_path, _, _, _ = _checkpoint_components(tmp_path, 0)
    marker = _read(marker_path)
    marker["chroma_backup"] = None
    marker["chroma_sha256"] = None
    marker_path.write_text(json.dumps(marker), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="Chroma backup missing"):
        manager.resolve_checkpoint(0)
    assert await manager.get_available_checkpoints() == []
    assert not backup_dir.exists()
    engine.event_logger.close()
    manager.close()


@pytest.mark.asyncio
async def test_memoryless_checkpoint_drops_stale_chroma_and_restore_stays_lazy(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source = PersistenceManager(str(tmp_path / "source-memoryless"))
    (source.chroma_store_path / "stale.bin").write_bytes(b"stale source data")

    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    marker = await source.save_checkpoint(world, CodeSchedule())
    record = source.resolve_checkpoint(0)
    backup_dir = record["chroma_backup_dir"]
    assert backup_dir is not None
    assert [path.name for path in backup_dir.iterdir()] == ["_checkpoint.json"]
    assert marker["memory_required"] is False

    destination = PersistenceManager(str(tmp_path / "destination-memoryless"))
    (destination.chroma_store_path / "stale.bin").write_bytes(b"stale destination data")

    def fail_client_creation():
        raise AssertionError("memoryless restore must not initialize Chroma")

    monkeypatch.setattr(destination, "_create_chroma_client", fail_client_creation)
    await destination.load_checkpoint_from(source.save_dir, step=0)

    assert destination._chroma_client is None
    assert list(destination.chroma_store_path.iterdir()) == []
    source.close()
    destination.close()


@pytest.mark.asyncio
async def test_same_step_replacement_publishes_new_pair_without_moving_old_pair(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    world = World(step=0)
    world.add_agent_data("rule_0", "participant", archetype="rule")
    world.set_environment_type("plain")
    first = await manager.save_checkpoint(world, CodeSchedule())
    old_record = manager.resolve_checkpoint(0)
    old_world = old_record["checkpoint_file"]
    old_backup = old_record["chroma_backup_dir"]

    entered_backup = asyncio.Event()
    release_backup = asyncio.Event()
    real_backup = manager._backup_chroma_store

    async def delayed_backup(*args, **kwargs):
        entered_backup.set()
        await release_backup.wait()
        return await real_backup(*args, **kwargs)

    monkeypatch.setattr(manager, "_backup_chroma_store", delayed_backup)
    replacement = asyncio.create_task(manager.save_checkpoint(world, CodeSchedule()))
    await asyncio.wait_for(entered_backup.wait(), timeout=2)

    during = manager.resolve_checkpoint(0)
    assert during["checkpoint_id"] == first["checkpoint_id"]
    assert old_world.is_file()
    assert old_backup is not None and old_backup.is_dir()

    release_backup.set()
    second = await replacement
    after = manager.resolve_checkpoint(0)
    assert after["checkpoint_id"] == second["checkpoint_id"]
    assert second["checkpoint_id"] != first["checkpoint_id"]
    assert after["checkpoint_file"] != old_world
    assert after["chroma_backup_dir"] != old_backup
    # Versioned components are retained for readers that opened the old marker
    # before the atomic marker replacement; garbage collection is separate.
    assert old_world.is_file()
    assert old_backup is not None and old_backup.is_dir()
    manager.close()


def test_chroma_backup_parent_is_fsynced_after_component_publish(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    manager = PersistenceManager(str(tmp_path))
    calls = []
    real_fsync_directory = manager._fsync_directory

    def record_fsync(path):
        calls.append(path)
        return real_fsync_directory(path)

    monkeypatch.setattr(manager, "_fsync_directory", record_fsync)

    async def save():
        world = World(step=0)
        world.add_agent_data("rule_0", "participant", archetype="rule")
        world.set_environment_type("plain")
        await manager.save_checkpoint(world, CodeSchedule())

    asyncio.run(save())
    assert manager.chroma_backup_dir in calls
    manager.close()


@pytest.mark.asyncio
async def test_post_restore_initialization_failure_disables_engine_and_persistence(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-post-init"
    source = Society0(save_dir=str(source_run), base_config=_config())
    await source._initialize()
    source.current_world_state.step = 4
    await source.persistence_manager.save_checkpoint(source.current_world_state, source.schedule)
    source.event_logger.close()
    source.persistence_manager.close()

    destination = Society0(
        save_dir=str(tmp_path / "destination-post-init"),
        base_config=_config(),
        source_run=source_run,
        source_step=4,
    )

    def fail_environment_init(world):
        raise RuntimeError("injected environment initialization failure")

    monkeypatch.setattr(destination, "_prepare_world_environment", fail_environment_init)
    with pytest.raises(RuntimeError, match="injected environment initialization failure"):
        await destination.restore(source_run, step=4)

    assert destination.current_world_state is None
    assert destination.restored_checkpoint["marker"]["checkpoint_id"] == (
        destination.restored_checkpoint["checkpoint_id"]
    )
    with pytest.raises(RuntimeError, match="Society0 is unusable"):
        await destination.run(steps=0)
    with pytest.raises(RuntimeError, match="PersistenceManager is unusable"):
        destination.persistence_manager.get_chroma_client()
    destination.event_logger.close()
    destination.persistence_manager.close()


@pytest.mark.parametrize("relationship", ["same", "child", "parent"])
def test_resume_path_overlap_is_rejected_before_source_tree_changes(
    tmp_path,
    relationship,
):
    parent = tmp_path / "parent"
    source = parent / "source"
    source.mkdir(parents=True)
    (source / "sentinel.txt").write_text("unchanged", encoding="utf-8")
    before = {
        path.relative_to(parent).as_posix(): path.read_bytes()
        for path in parent.rglob("*")
        if path.is_file()
    }
    destination = {
        "same": source,
        "child": source / "destination",
        "parent": parent,
    }[relationship]

    with pytest.raises(ValueError, match="disjoint directory trees"):
        Society0(
            save_dir=str(destination),
            source_run=source,
            base_config=_config(),
        )

    after = {
        path.relative_to(parent).as_posix(): path.read_bytes()
        for path in parent.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert not (source / "destination").exists()


@pytest.mark.asyncio
async def test_restore_rejects_source_not_declared_before_destination_creation(
    tmp_path,
):
    source = tmp_path / "source-late"
    source.mkdir()
    destination_path = tmp_path / "destination-created"
    destination = Society0(save_dir=str(destination_path), base_config=_config())

    assert (destination_path / "checkpoints").is_dir()
    with pytest.raises(ValueError, match="must be declared when Society0 is constructed"):
        await destination.restore(source)
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_resume_identity_rejects_changed_embedding_before_chroma_restore(
    tmp_path,
    monkeypatch,
):
    pytest.importorskip("chromadb")
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-identity"
    source = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        embed=EmbedModel.openai_compatible(
            model="embedding-a",
            base_url="http://user:source-secret@embed.invalid/v1?token=hidden",
            api_key="test-secret-key",
            dimensions=8,
        ),
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)
    _, checkpoint_path, _, _ = _checkpoint_components(source_run, 0)
    checkpoint_text = _read_text(checkpoint_path)
    assert "api_key" not in checkpoint_text
    assert "embedding-a" in checkpoint_text
    assert "source-secret" not in checkpoint_text
    assert "token=hidden" not in checkpoint_text
    assert "test-secret-key" not in checkpoint_text

    destination = Society0(
        save_dir=str(tmp_path / "destination-identity"),
        base_config=_config(),
        source_run=source_run,
        embed=EmbedModel.openai_compatible(
            model="embedding-a",
            base_url="http://other-embed.invalid/v1",
            api_key="test-secret-key",
            dimensions=8,
        ),
    )
    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_run)
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_resume_identity_rejects_changed_llm_endpoint_without_leaking_url(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-llm-identity"
    source = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        llm=LLMModel.openai_compatible(
            model="decision-model",
            base_url="http://user:llm-secret@llm.invalid/v1?token=hidden",
            api_key="test-secret-key",
        ),
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)
    _, checkpoint_path, _, _ = _checkpoint_components(source_run, 0)
    checkpoint_text = _read_text(checkpoint_path)
    assert "decision-model" in checkpoint_text
    assert "llm-secret" not in checkpoint_text
    assert "token=hidden" not in checkpoint_text
    assert "api_key" not in checkpoint_text
    assert "test-secret-key" not in checkpoint_text

    destination = Society0(
        save_dir=str(tmp_path / "destination-llm-identity"),
        base_config=_config(),
        source_run=source_run,
        llm=LLMModel.openai_compatible(
            model="decision-model",
            base_url="http://other-llm.invalid/v1",
            api_key="test-secret-key",
        ),
    )
    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_run)
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_resume_identity_rejects_changed_llm_request_options(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-llm-options"
    source = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        llm=LLMModel.openai_compatible(
            model="decision-model",
            base_url="http://llm.invalid/v1",
            api_key="test-secret-key",
            request_options={
                "max_tokens": 128,
                "temperature": 0.1,
                "extra_body": {
                    "thinking": {"type": "disabled"},
                    "api_key": "source-secret",
                },
            },
        ),
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)
    _, checkpoint_path, _, _ = _checkpoint_components(source_run, 0)
    checkpoint_text = _read_text(checkpoint_path)
    assert "source-secret" not in checkpoint_text
    assert "max_tokens" in checkpoint_text

    destination = Society0(
        save_dir=str(tmp_path / "destination-llm-options"),
        base_config=_config(),
        source_run=source_run,
        llm=LLMModel.openai_compatible(
            model="decision-model",
            base_url="http://llm.invalid/v1",
            api_key="test-secret-key",
            request_options={
                "max_tokens": 256,
                "temperature": 0.1,
                "extra_body": {
                    "thinking": {"type": "disabled"},
                    "api_key": "destination-secret",
                },
            },
        ),
    )
    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_run)
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_resume_identity_rejects_changed_tool_choice_policy(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-tool-choice-policy"
    source = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        llm=LLMModel.openai_compatible(
            model="decision-model",
            base_url="http://llm.invalid/v1",
            api_key="test-secret-key",
            tool_choice_policy="auto_restrict",
        ),
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)

    destination = Society0(
        save_dir=str(tmp_path / "destination-tool-choice-policy"),
        base_config=_config(),
        source_run=source_run,
        llm=LLMModel.openai_compatible(
            model="decision-model",
            base_url="http://llm.invalid/v1",
            api_key="test-secret-key",
            tool_choice_policy="native",
        ),
    )
    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_run)
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_resume_identity_rejects_changed_agent_concurrency(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-agent-concurrency"
    model = LLMModel.openai_compatible(
        model="decision-model",
        base_url="http://llm.invalid/v1",
        api_key="test-secret-key",
        concurrency=2,
    )
    source = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        llm=model,
        agent_concurrency=1,
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)
    destination = Society0(
        save_dir=str(tmp_path / "destination-agent-concurrency"),
        base_config=_config(),
        source_run=source_run,
        llm=LLMModel.openai_compatible(
            model="decision-model",
            base_url="http://llm.invalid/v1",
            api_key="test-secret-key",
            concurrency=2,
        ),
        agent_concurrency=2,
    )
    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_run)
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_resume_identity_rejects_changed_llm_retry_policy(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-llm-retry-policy"
    model_kwargs = {
        "model": "decision-model",
        "base_url": "http://llm.invalid/v1",
        "api_key": "test-secret-key",
    }
    source = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        llm=LLMModel.openai_compatible(**model_kwargs),
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)
    destination = Society0(
        save_dir=str(tmp_path / "destination-llm-retry-policy"),
        base_config=_config(),
        source_run=source_run,
        llm=LLMModel.openai_compatible(**model_kwargs),
    )
    original_initialize_models = destination._initialize_models

    def initialize_models_with_changed_retry_policy():
        original_initialize_models()
        destination._llm_manager._max_retries = 3

    monkeypatch.setattr(
        destination,
        "_initialize_models",
        initialize_models_with_changed_retry_policy,
    )
    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_run)
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_resume_identity_rejects_changed_llm_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-llm-timeout"
    source = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        llm=LLMModel.openai_compatible(
            model="decision-model",
            base_url="http://llm.invalid/v1",
            api_key="test-secret-key",
            timeout=30.0,
        ),
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)
    destination = Society0(
        save_dir=str(tmp_path / "destination-llm-timeout"),
        base_config=_config(),
        source_run=source_run,
        llm=LLMModel.openai_compatible(
            model="decision-model",
            base_url="http://llm.invalid/v1",
            api_key="test-secret-key",
            timeout=45.0,
        ),
    )
    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_run)
    destination.persistence_manager.close()


def test_resume_contract_rejects_nested_credentials_before_writing(tmp_path):
    destination = tmp_path / "destination-secret-contract"
    with pytest.raises(ValueError, match="must not contain credentials"):
        Society0(
            save_dir=str(destination),
            base_config=_config(),
            resume_contract={"provider": {"access_token": "sensitive"}},
        )
    assert not destination.exists()


@pytest.mark.asyncio
async def test_resume_identity_binds_application_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-contract"
    source = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        resume_contract={"industrial_model_sha256": "a" * 64},
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)
    destination = Society0(
        save_dir=str(tmp_path / "destination-contract"),
        base_config=_config(),
        source_run=source_run,
        resume_contract={"industrial_model_sha256": "b" * 64},
    )
    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_run)
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_resume_identity_rejects_changed_capability_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-capabilities"
    source = Society0(
        save_dir=str(source_run),
        base_config=_config(),
        environment_factory=_ResumeIdentityEnvironmentA,
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)
    destination = Society0(
        save_dir=str(tmp_path / "destination-capabilities"),
        base_config=_config(),
        source_run=source_run,
        environment_factory=_ResumeIdentityEnvironmentB,
    )
    with pytest.raises(ValueError, match="resume identity does not match"):
        await destination.restore(source_run)
    destination.persistence_manager.close()


@pytest.mark.asyncio
async def test_external_environment_factory_is_not_rebound_after_restore(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    source_run = tmp_path / "source-factory"
    config = _config()
    config["environment"]["state"]["nested"] = {"actors": {"actor-1": {"active": True}}}
    source = Society0(
        save_dir=str(source_run),
        base_config=config,
        environment_factory=Environment,
    )

    @source.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    await source.run(steps=0)
    _, checkpoint_path, _, _ = _checkpoint_components(source_run, 0)
    checkpoint = _read(checkpoint_path)
    snapshot_text = json.dumps(checkpoint["environment_data"]["snapshot"])
    assert "_target_dict" not in snapshot_text
    assert "state" not in checkpoint["environment_data"]["snapshot"]
    destination = Society0(
        save_dir=str(tmp_path / "destination-factory"),
        base_config=config,
        environment_factory=Environment,
        source_run=source_run,
    )
    restored_step = await destination.restore(source_run)

    assert restored_step == 0
    assert isinstance(destination.current_world_state.get_environment(), Environment)
    assert destination.current_world_state.environment_data["state"]["nested"] == {
        "actors": {"actor-1": {"active": True}}
    }
    destination.event_logger.close()
    destination.persistence_manager.close()
