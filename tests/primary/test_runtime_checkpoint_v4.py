"""Society0/SimEngine v4 运行时生命周期的红灯合同。

这些用例只使用 rule agent、CodeSchedule 和一个最小自定义环境；状态变更通过
Environment/Agent 公开代理发生，预期由运行器自动完成 v4 schema bootstrap、
begin/seal/publish 生命周期。产品实现完成前，本文件保持红灯。
"""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from society0 import Environment, Society0
from society0.decorators import env_type
from society0.env import BUILTIN_ENVS
from society0.persistence import PersistenceManager
from society0.sim_engine import SimEngine
from society0.incremental_checkpoint import V4CheckpointStore


pytestmark = pytest.mark.primary


_CONFIG_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {},
}

_STATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "counter": {
            "type": "integer",
            "persistence": {"kind": "replaceable"},
        },
        "events": {
            "type": "array",
            "items": {"type": "string"},
            "persistence": {"kind": "append_only_list"},
        },
        "cursor": {
            "type": "integer",
            "default": 0,
            "persistence": {"kind": "transient"},
        },
    },
}

_AGENT_STATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "score": {
            "type": "integer",
            "persistence": {"kind": "replaceable"},
        },
    },
}


@env_type(
    type_name="runtime_checkpoint_v4_test",
    config_schema=_CONFIG_SCHEMA,
    state_schema=_STATE_SCHEMA,
    agent_managed_fields_schema={
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    },
)
class RuntimeCheckpointEnvironment(Environment):
    """Minimal environment whose optional after hook can fail deterministically."""

    fail_after = False

    def after_tick(self, ctx):
        self.state["events"].append(f"after:{ctx.step}")
        if self.fail_after:
            raise RuntimeError("after_tick checkpoint v4 failure")


def _failing_after_factory(world):
    environment = RuntimeCheckpointEnvironment(world)
    environment.fail_after = True
    return environment


def _base_config() -> dict[str, Any]:
    return {
        "agent_types": [
            {
                "id": "worker",
                "archetype": "rule",
                "state_schema": _AGENT_STATE_SCHEMA,
            }
        ],
        "agents": [
            {
                "id": "worker-a",
                "type": "worker",
                "state": {"score": 0},
            }
        ],
        "environment": {
            "type": "runtime_checkpoint_v4_test",
            "state": {"counter": 0, "events": [], "cursor": 0},
        },
    }


def _engine(
    save_dir: Path,
    *,
    checkpoint_every: int = 1,
    environment_factory: Callable[[Any], Environment] | None = RuntimeCheckpointEnvironment,
) -> Society0:
    return Society0(
        save_dir=str(save_dir),
        base_config=_base_config(),
        checkpoint_every=checkpoint_every,
        environment_factory=environment_factory,
    )


def _v4_steps(run_dir: Path) -> list[int]:
    complete = run_dir / "checkpoints" / "v4" / "complete"
    return sorted(
        int(path.stem.removeprefix("step_"))
        for path in complete.glob("step_*.json")
    )


def _assert_no_v3_recoverable_outputs(run_dir: Path) -> None:
    """v4 runs must not leave the old recoverable gzip/backup contract behind."""

    assert not list((run_dir / "checkpoints" / "complete").glob("step_*.json"))
    for path in (run_dir / "checkpoints").glob("checkpoint_*.json.gz"):
        # A non-recoverable failure diagnostic may remain for inspection; the
        # v3 recoverable World gzip must not be emitted by a v4 run.
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            assert json.load(handle).get("recoverable") is not True
    assert not (run_dir / "chroma_backups").exists()


async def _restore_v4_world(source_dir: Path, destination_dir: Path, step: int):
    reader = PersistenceManager(str(destination_dir))
    try:
        world, _ = await reader.load_checkpoint_from(
            source_dir,
            step=step,
            restore_chroma=False,
            environment_factory=RuntimeCheckpointEnvironment,
        )
        return world
    finally:
        reader.close()


async def _run_one_tick(engine: Society0) -> None:
    @engine.step(name="mutate_runtime_state")
    async def mutate_runtime_state(ctx):
        ctx.env.state["counter"] = ctx.step + 1
        ctx.env.state["events"].append(f"step:{ctx.step}")
        ctx.env.state["cursor"] = 100 + ctx.step
        ctx.world.get_agent("worker-a").state["score"] = ctx.step + 1
        return ctx.result()

    await engine.run(steps=1)


@pytest.mark.asyncio
async def test_society0_bootstraps_v4_schema_root_and_recovers_env_and_agent_state(tmp_path):
    engine = _engine(tmp_path)
    await _run_one_tick(engine)

    assert _v4_steps(tmp_path) == [0, 1]
    _assert_no_v3_recoverable_outputs(tmp_path)

    restored = await _restore_v4_world(tmp_path, tmp_path / "reader", 1)
    assert restored.environment_data["state"] == {
        "counter": 1,
        "events": ["step:0", "after:0"],
        "cursor": 0,
    }
    assert restored.agents_data["worker-a"]["state"]["score"] == 1


@pytest.mark.asyncio
async def test_society0_tick_lifecycle_calls_begin_seal_publish_delta_without_world(
    tmp_path,
    monkeypatch,
):
    engine = _engine(tmp_path)
    lifecycle: list[str] = []

    # The world is created lazily; wrap the class methods so the observation does
    # not require a private World reference before Society0 initialization.
    from society0.core_data import World

    real_begin = World.begin_persistence_tick
    real_seal = World.seal_persistence_tick

    def begin(world, step):
        lifecycle.append("begin")
        return real_begin(world, step)

    def seal(world):
        lifecycle.append("seal")
        return real_seal(world)

    monkeypatch.setattr(World, "begin_persistence_tick", begin)
    monkeypatch.setattr(World, "seal_persistence_tick", seal)

    async def publish_delta(delta, schedule, *, force=False):
        lifecycle.append("publish")
        assert not hasattr(delta, "environment_data")
        return await real_publish_delta(delta, schedule, force=force)

    real_publish_delta = engine.persistence_manager.publish_delta
    monkeypatch.setattr(engine.persistence_manager, "publish_delta", publish_delta)

    await _run_one_tick(engine)
    assert lifecycle == ["begin", "seal", "publish"]


@pytest.mark.asyncio
async def test_society0_business_failure_aborts_delta_last_complete_and_runtime_scope(
    tmp_path,
):
    engine = _engine(tmp_path)
    scope_holder: list[Any] = []

    @engine.step(name="fail_business")
    async def fail_business(ctx):
        scope_holder.append(ctx.runtime_scope)
        ctx.runtime_scope.namespace("test")["ephemeral"] = "discard-me"
        ctx.env.state["counter"] = 99
        ctx.world.get_agent("worker-a").state["score"] = 99
        raise RuntimeError("business v4 failure")

    with pytest.raises(RuntimeError, match="business v4 failure"):
        await engine.run(steps=1)

    assert _v4_steps(tmp_path) == [0]
    _assert_no_v3_recoverable_outputs(tmp_path)
    root_state = V4CheckpointStore(tmp_path).restore(0)["environment"]["state"]
    assert root_state == {"counter": 0, "events": [], "cursor": 0}
    assert engine.current_world_state is not None
    assert engine.current_world_state.get_step_runtime_scope() is None
    with pytest.raises(RuntimeError, match="失效|scope"):
        scope_holder[0].namespace("test")


@pytest.mark.asyncio
async def test_society0_after_tick_failure_aborts_delta_and_keeps_last_complete(tmp_path):
    engine = _engine(tmp_path, environment_factory=_failing_after_factory)

    @engine.step(name="mutate_before_after_failure")
    async def mutate_before_after_failure(ctx):
        ctx.env.state["counter"] = 7
        ctx.env.state["events"].append("business")
        ctx.world.get_agent("worker-a").state["score"] = 7
        return ctx.result()

    with pytest.raises(RuntimeError, match="after_tick checkpoint v4 failure"):
        await engine.run(steps=1)

    assert _v4_steps(tmp_path) == [0]
    _assert_no_v3_recoverable_outputs(tmp_path)
    root_state = V4CheckpointStore(tmp_path).restore(0)["environment"]["state"]
    assert root_state == {"counter": 0, "events": [], "cursor": 0}
    assert engine.current_world_state is not None
    assert engine.current_world_state.get_step_runtime_scope() is None


@pytest.mark.asyncio
async def test_society0_checkpoint_publish_failure_stops_run_without_new_marker(
    tmp_path,
    monkeypatch,
):
    engine = _engine(tmp_path)

    async def fail_publish(delta, schedule, *, force=False):
        raise OSError("checkpoint publish failed")

    monkeypatch.setattr(engine.persistence_manager, "publish_delta", fail_publish)

    @engine.step(name="mutate_then_publish")
    async def mutate_then_publish(ctx):
        ctx.env.state["counter"] = 1
        ctx.world.get_agent("worker-a").state["score"] = 1
        return ctx.result()

    with pytest.raises(OSError, match="checkpoint publish failed"):
        await engine.run(steps=2)

    assert _v4_steps(tmp_path) == [0]
    _assert_no_v3_recoverable_outputs(tmp_path)


@pytest.mark.asyncio
async def test_society0_checkpoint_every_two_publishes_epoch_and_recovers_agent_state(tmp_path):
    engine = _engine(tmp_path, checkpoint_every=2)

    @engine.step(name="mutate_epoch")
    async def mutate_epoch(ctx):
        ctx.env.state["counter"] = ctx.step + 1
        ctx.env.state["events"].append(f"step:{ctx.step}")
        ctx.world.get_agent("worker-a").state["score"] = ctx.step + 1
        return ctx.result()

    await engine.run(steps=2)

    assert _v4_steps(tmp_path) == [0, 2]
    _assert_no_v3_recoverable_outputs(tmp_path)
    restored = await _restore_v4_world(tmp_path, tmp_path / "reader", 2)
    assert restored.environment_data["state"]["counter"] == 2
    assert restored.environment_data["state"]["events"] == [
        "step:0",
        "after:0",
        "step:1",
        "after:1",
    ]
    assert restored.agents_data["worker-a"]["state"]["score"] == 2


@pytest.mark.asyncio
async def test_society0_checkpoint_every_two_publish_failure_discards_epoch(tmp_path, monkeypatch):
    engine = _engine(tmp_path, checkpoint_every=2)
    real_publish_delta = engine.persistence_manager.publish_delta
    publish_steps: list[int] = []

    async def fail_epoch_publish(delta, schedule, *, force=False):
        publish_steps.append(delta.step)
        if delta.step >= 2:
            raise OSError("epoch publish failed")
        return await real_publish_delta(delta, schedule, force=force)

    monkeypatch.setattr(engine.persistence_manager, "publish_delta", fail_epoch_publish)

    @engine.step(name="mutate_failed_epoch")
    async def mutate_failed_epoch(ctx):
        ctx.env.state["counter"] = ctx.step + 1
        ctx.world.get_agent("worker-a").state["score"] = ctx.step + 1
        return ctx.result()

    with pytest.raises(OSError, match="epoch publish failed"):
        await engine.run(steps=2)

    assert publish_steps
    assert _v4_steps(tmp_path) == [0]
    _assert_no_v3_recoverable_outputs(tmp_path)


@pytest.mark.asyncio
async def test_society0_cancelled_business_tick_does_not_publish_runtime_delta(tmp_path):
    engine = _engine(tmp_path)
    entered = asyncio.Event()
    scope_holder: list[Any] = []

    @engine.step(name="wait_for_cancel")
    async def wait_for_cancel(ctx):
        scope_holder.append(ctx.runtime_scope)
        entered.set()
        await asyncio.Event().wait()

    run_task = asyncio.create_task(engine.run(steps=1))
    await asyncio.wait_for(entered.wait(), timeout=2)
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert _v4_steps(tmp_path) == [0]
    _assert_no_v3_recoverable_outputs(tmp_path)
    assert engine.current_world_state is not None
    assert engine.current_world_state.get_step_runtime_scope() is None
    with pytest.raises(RuntimeError, match="失效|scope"):
        scope_holder[0].namespace("test")


@pytest.mark.asyncio
async def test_simengine_bootstraps_same_v4_layout_for_rule_schedule(tmp_path, monkeypatch):
    monkeypatch.setitem(BUILTIN_ENVS, "runtime_checkpoint_v4_test", RuntimeCheckpointEnvironment)
    experiment_root = tmp_path / "experiment"
    experiment_root.mkdir()
    (experiment_root / "agent_set.json").write_text(
        json.dumps(
            {
                "types": [
                    {
                        "id": "worker",
                        "archetype": "rule",
                        "state_schema": _AGENT_STATE_SCHEMA,
                    }
                ],
                "agents": [
                    {"id": "worker-a", "type": "worker", "state": {"score": 0}}
                ],
            }
        ),
        encoding="utf-8",
    )
    (experiment_root / "environment.json").write_text(
        json.dumps(
            {
                "type": "runtime_checkpoint_v4_test",
                "config": {},
                "state_schema": _STATE_SCHEMA,
                "state": {"counter": 0, "events": [], "cursor": 0},
            }
        ),
        encoding="utf-8",
    )
    schedule = {
        "dependencies": {
            "agent_set": "agent_set",
            "environment": "environment",
            "logics": [],
        },
        "nodes": [],
    }

    run_dir = tmp_path / "simengine-run"
    engine = SimEngine(
        save_dir=str(run_dir),
        base_config={"schedule": schedule},
        experiment_root=experiment_root,
    )

    class _FakeLLMManager:
        async def request(self, _payload):
            return {"role": "assistant", "content": "ok", "tool_calls": []}

        async def close(self):
            return None

    # SimEngine's legacy initialization asks for an LLM callable even for a
    # rule-only schedule; the fake is never invoked by this empty CodeSchedule.
    engine.set_resource_managers(llm_manager=_FakeLLMManager())
    try:
        await engine.run(steps=1)
    finally:
        engine.close()

    assert _v4_steps(run_dir) == [0, 1]
    _assert_no_v3_recoverable_outputs(run_dir)
