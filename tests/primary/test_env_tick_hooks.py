import json
from pathlib import Path

import pytest

from society0 import Society0
from society0.decorators import env_type
from society0.env import BUILTIN_ENVS
from society0.environment import Environment
from tests import read_gzip_json

pytestmark = pytest.mark.primary


def _hook_config(env_type: str):
    return {
        "agent_types": [{"id": "user", "archetype": "rule"}],
        "agents": [{"id": "alice", "type": "user", "state": {}}],
        "environment": {"type": env_type, "state": {"events": []}},
    }


def _jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


_HOOK_CONFIG_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}
_HOOK_STATE_SCHEMA = {
    "type": "object",
    "properties": {
        "events": {
            "type": "array",
            "items": {"type": "string"},
            "persistence": {"kind": "append_only_list"},
        }
    },
    "additionalProperties": False,
}


@env_type(
    type_name="hook_order",
    config_schema=_HOOK_CONFIG_SCHEMA,
    state_schema=_HOOK_STATE_SCHEMA,
)
class HookOrderEnv(Environment):
    def initialize(self, agents, world):
        self.state.setdefault("events", [])

    def before_tick(self, ctx):
        self.state["events"].append(f"before:{ctx.step}")

    async def after_tick(self, ctx):
        self.state["events"].append(f"after:{ctx.step}")


@env_type(
    type_name="async_before_sync_after",
    config_schema=_HOOK_CONFIG_SCHEMA,
    state_schema=_HOOK_STATE_SCHEMA,
)
class AsyncBeforeSyncAfterEnv(Environment):
    def initialize(self, agents, world):
        self.state.setdefault("events", [])

    async def before_tick(self, ctx):
        self.state["events"].append(f"async_before:{ctx.step}")

    def after_tick(self, ctx):
        self.state["events"].append(f"sync_after:{ctx.step}")


@env_type(
    type_name="failing_before",
    config_schema=_HOOK_CONFIG_SCHEMA,
    state_schema=_HOOK_STATE_SCHEMA,
)
class FailingBeforeEnv(Environment):
    def initialize(self, agents, world):
        self.state.setdefault("events", [])

    def before_tick(self, ctx):
        self.state["events"].append(f"before:{ctx.step}")
        raise RuntimeError("before hook failed")


@env_type(
    type_name="runtime_scope",
    config_schema=_HOOK_CONFIG_SCHEMA,
    state_schema=_HOOK_STATE_SCHEMA,
)
class RuntimeScopeEnv(Environment):
    def initialize(self, agents, world):
        self.state.setdefault("events", [])
        self.last_scope = None

    def before_tick(self, ctx):
        assert ctx.runtime_scope is self.step_runtime
        self.last_scope = ctx.runtime_scope
        ctx.runtime_scope.namespace("test.env")["ephemeral"] = "not-checkpointed"

    def after_tick(self, ctx):
        namespace = ctx.runtime_scope.namespace("test.env")
        assert namespace["ephemeral"] == "not-checkpointed"


@pytest.fixture
def hook_envs(monkeypatch):
    monkeypatch.setitem(BUILTIN_ENVS, "hook_order", HookOrderEnv)
    monkeypatch.setitem(BUILTIN_ENVS, "async_before_sync_after", AsyncBeforeSyncAfterEnv)
    monkeypatch.setitem(BUILTIN_ENVS, "failing_before", FailingBeforeEnv)
    monkeypatch.setitem(BUILTIN_ENVS, "runtime_scope", RuntimeScopeEnv)


@pytest.mark.asyncio
async def test_env_tick_hooks_order(tmp_path, hook_envs):
    engine = Society0(save_dir=str(tmp_path), base_config=_hook_config("hook_order"))

    @engine.step(name="record_step")
    async def record_step(ctx):
        assert ctx.step == 0
        ctx.env.state["events"].append(f"step:{ctx.step}")
        return ctx.result(metrics={"events": len(ctx.env.state["events"])})

    await engine.run(steps=1)

    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert checkpoint["environment_data"]["state"]["events"] == ["before:0", "step:0", "after:0"]
    assert checkpoint["step"] == 1
    events = _jsonl(tmp_path / "events.jsonl")
    hook_events = [event for event in events if event.get("event", "").startswith("env_hook_")]
    assert [(event["event"], event["hook_name"]) for event in hook_events] == [
        ("env_hook_started", "before_tick"),
        ("env_hook_completed", "before_tick"),
        ("env_hook_started", "after_tick"),
        ("env_hook_completed", "after_tick"),
    ]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["events"]["env_hooks"]["before_tick"]["started_count"] == 1
    assert summary["events"]["env_hooks"]["before_tick"]["completed_count"] == 1
    assert summary["events"]["env_hooks"]["before_tick"]["failed_count"] == 0
    assert summary["events"]["env_hooks"]["after_tick"]["started_count"] == 1
    assert summary["events"]["env_hooks"]["after_tick"]["completed_count"] == 1


@pytest.mark.asyncio
async def test_env_tick_hooks_support_async_and_sync(tmp_path, hook_envs):
    engine = Society0(save_dir=str(tmp_path), base_config=_hook_config("async_before_sync_after"))

    @engine.step(name="record_step")
    async def record_step(ctx):
        ctx.env.state["events"].append(f"step:{ctx.step}")
        return None

    await engine.run(steps=1)

    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert checkpoint["environment_data"]["state"]["events"] == [
        "async_before:0",
        "step:0",
        "sync_after:0",
    ]


@pytest.mark.asyncio
async def test_env_tick_hook_summary_splits_repeated_hooks_by_tick(tmp_path, hook_envs):
    engine = Society0(save_dir=str(tmp_path), base_config=_hook_config("hook_order"))

    @engine.step(name="record_step")
    async def record_step(ctx):
        ctx.env.state["events"].append(f"step:{ctx.step}")
        return None

    await engine.run(steps=3)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    before_hook = summary["events"]["env_hooks"]["before_tick"]
    after_hook = summary["events"]["env_hooks"]["after_tick"]

    assert before_hook["started_count"] == 3
    assert before_hook["completed_count"] == 3
    assert before_hook["failed_count"] == 0
    assert set(before_hook["by_tick"]) == {"0", "1", "2"}
    assert before_hook["by_tick"]["0"]["started_count"] == 1
    assert before_hook["by_tick"]["1"]["completed_count"] == 1
    assert before_hook["by_tick"]["2"]["failed_count"] == 0

    assert after_hook["started_count"] == 3
    assert after_hook["completed_count"] == 3
    assert set(after_hook["by_tick"]) == {"0", "1", "2"}
    assert after_hook["by_tick"]["2"]["completed_count"] == 1
    assert "error_samples" not in before_hook["by_tick"]["0"]
    assert "error_samples" not in after_hook["by_tick"]["2"]


@pytest.mark.asyncio
async def test_after_tick_not_called_when_step_fails(tmp_path, hook_envs):
    engine = Society0(save_dir=str(tmp_path), base_config=_hook_config("hook_order"))

    @engine.step(name="boom")
    async def boom(ctx):
        ctx.env.state["events"].append(f"step:{ctx.step}")
        raise RuntimeError("step failed")

    with pytest.raises(RuntimeError, match="step failed"):
        await engine.run(steps=1)

    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert checkpoint["environment_data"]["state"]["events"] == ["before:0", "step:0"]
    events = _jsonl(tmp_path / "events.jsonl")
    assert events[-1]["event"] == "run_failed"
    assert events[-1]["failed_step"] == 0
    assert events[-1]["last_complete_step"] == 0
    assert events[-1]["recoverable"] is True
    assert events[-1]["retryable"] is False


@pytest.mark.asyncio
async def test_hook_failure_fails_run_and_saves_final_checkpoint(tmp_path, hook_envs):
    engine = Society0(save_dir=str(tmp_path), base_config=_hook_config("failing_before"))

    @engine.step(name="should_not_run")
    async def should_not_run(ctx):
        ctx.env.state["events"].append("step")
        return None

    with pytest.raises(RuntimeError, match="before hook failed"):
        await engine.run(steps=1)

    checkpoint_path = tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    assert checkpoint_path.is_file()
    checkpoint = read_gzip_json(checkpoint_path)
    assert checkpoint["environment_data"]["state"]["events"] == ["before:0"]
    assert checkpoint["step"] == 0
    assert checkpoint["failure"]["last_complete_step"] == 0
    assert checkpoint["failure"]["recoverable"] is True
    events = _jsonl(tmp_path / "events.jsonl")
    assert events[-1]["event"] == "run_failed"
    assert events[-1]["error_type"] == "RuntimeError"
    hook_events = [event for event in events if event.get("event", "").startswith("env_hook_")]
    assert [(event["event"], event["hook_name"]) for event in hook_events] == [
        ("env_hook_started", "before_tick"),
        ("env_hook_failed", "before_tick"),
    ]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    before_hook = summary["events"]["env_hooks"]["before_tick"]
    assert before_hook["started_count"] == 1
    assert before_hook["completed_count"] == 0
    assert before_hook["failed_count"] == 1
    assert before_hook["by_tick"]["0"]["started_count"] == 1
    assert before_hook["by_tick"]["0"]["completed_count"] == 0
    assert before_hook["by_tick"]["0"]["failed_count"] == 1
    diagnostics = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    assert "Status: failed" in diagnostics
    assert "### before_tick" in diagnostics
    assert "started/completed/failed 1/0/1" in diagnostics
    assert "RuntimeError: before hook failed" in diagnostics
    assert before_hook["error_samples"] == [
        {
            "step": 0,
            "error": "before hook failed",
            "error_type": "RuntimeError",
        }
    ]
    assert before_hook["by_tick"]["0"]["error_samples"] == before_hook["error_samples"]


@pytest.mark.asyncio
async def test_step_runtime_scope_is_shared_during_step_and_not_checkpointed(
    tmp_path,
    hook_envs,
):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_hook_config("runtime_scope"),
    )

    @engine.step(name="read_runtime_scope")
    async def read_runtime_scope(ctx):
        namespace = ctx.runtime_scope.namespace("test.env")
        assert namespace["ephemeral"] == "not-checkpointed"
        return None

    await engine.run(steps=1)

    world = engine.current_world_state
    assert world is not None
    env = world.get_environment()
    assert isinstance(env, RuntimeScopeEnv)
    assert world.get_step_runtime_scope() is None
    with pytest.raises(RuntimeError, match="已失效"):
        env.last_scope.namespace("test.env")

    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert "not-checkpointed" not in json.dumps(checkpoint, ensure_ascii=False)
