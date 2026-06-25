import json
from pathlib import Path

import pytest

from society0 import Society0
from society0.env import BUILTIN_ENVS
from society0.environment import Environment

pytestmark = pytest.mark.primary


def _hook_config(env_type: str):
    return {
        "agent_types": [{"id": "user", "archetype": "rule"}],
        "agents": [{"id": "alice", "type": "user", "state": {}}],
        "environment": {"type": env_type, "state": {"events": []}},
    }


def _jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class HookOrderEnv(Environment):
    def initialize(self, agents, world):
        self.state.setdefault("events", [])

    def before_tick(self, ctx):
        self.state["events"].append(f"before:{ctx.step}")

    async def after_tick(self, ctx):
        self.state["events"].append(f"after:{ctx.step}")


class AsyncBeforeSyncAfterEnv(Environment):
    def initialize(self, agents, world):
        self.state.setdefault("events", [])

    async def before_tick(self, ctx):
        self.state["events"].append(f"async_before:{ctx.step}")

    def after_tick(self, ctx):
        self.state["events"].append(f"sync_after:{ctx.step}")


class FailingBeforeEnv(Environment):
    def initialize(self, agents, world):
        self.state.setdefault("events", [])

    def before_tick(self, ctx):
        self.state["events"].append(f"before:{ctx.step}")
        raise RuntimeError("before hook failed")


@pytest.fixture
def hook_envs(monkeypatch):
    monkeypatch.setitem(BUILTIN_ENVS, "hook_order", HookOrderEnv)
    monkeypatch.setitem(BUILTIN_ENVS, "async_before_sync_after", AsyncBeforeSyncAfterEnv)
    monkeypatch.setitem(BUILTIN_ENVS, "failing_before", FailingBeforeEnv)


@pytest.mark.asyncio
async def test_env_tick_hooks_order(tmp_path, hook_envs):
    engine = Society0(save_dir=str(tmp_path), base_config=_hook_config("hook_order"))

    @engine.step(name="record_step")
    async def record_step(ctx):
        assert ctx.step == 0
        ctx.env.state["events"].append(f"step:{ctx.step}")
        return ctx.result(metrics={"events": len(ctx.env.state["events"])})

    await engine.run(steps=1)

    checkpoint = json.loads((tmp_path / "checkpoints" / "checkpoint_final.json").read_text(encoding="utf-8"))
    assert checkpoint["environment_data"]["state"]["events"] == ["before:0", "step:0", "after:0"]
    assert checkpoint["step"] == 1


@pytest.mark.asyncio
async def test_env_tick_hooks_support_async_and_sync(tmp_path, hook_envs):
    engine = Society0(save_dir=str(tmp_path), base_config=_hook_config("async_before_sync_after"))

    @engine.step(name="record_step")
    async def record_step(ctx):
        ctx.env.state["events"].append(f"step:{ctx.step}")
        return None

    await engine.run(steps=1)

    checkpoint = json.loads((tmp_path / "checkpoints" / "checkpoint_final.json").read_text(encoding="utf-8"))
    assert checkpoint["environment_data"]["state"]["events"] == [
        "async_before:0",
        "step:0",
        "sync_after:0",
    ]


@pytest.mark.asyncio
async def test_after_tick_not_called_when_step_fails(tmp_path, hook_envs):
    engine = Society0(save_dir=str(tmp_path), base_config=_hook_config("hook_order"))

    @engine.step(name="boom")
    async def boom(ctx):
        ctx.env.state["events"].append(f"step:{ctx.step}")
        raise RuntimeError("step failed")

    with pytest.raises(RuntimeError, match="step failed"):
        await engine.run(steps=1)

    checkpoint = json.loads((tmp_path / "checkpoints" / "checkpoint_final.json").read_text(encoding="utf-8"))
    assert checkpoint["environment_data"]["state"]["events"] == ["before:0", "step:0"]
    events = _jsonl(tmp_path / "events.jsonl")
    assert events[-1]["event"] == "run_failed"
    assert events[-1]["failed_step"] == 0


@pytest.mark.asyncio
async def test_hook_failure_fails_run_and_saves_final_checkpoint(tmp_path, hook_envs):
    engine = Society0(save_dir=str(tmp_path), base_config=_hook_config("failing_before"))

    @engine.step(name="should_not_run")
    async def should_not_run(ctx):
        ctx.env.state["events"].append("step")
        return None

    with pytest.raises(RuntimeError, match="before hook failed"):
        await engine.run(steps=1)

    checkpoint_path = tmp_path / "checkpoints" / "checkpoint_final.json"
    assert checkpoint_path.is_file()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["environment_data"]["state"]["events"] == ["before:0"]
    assert checkpoint["step"] == 0
    events = _jsonl(tmp_path / "events.jsonl")
    assert events[-1]["event"] == "run_failed"
    assert events[-1]["error_type"] == "RuntimeError"
