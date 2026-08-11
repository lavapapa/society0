from __future__ import annotations

from pathlib import Path

import pytest

from society0.agent.core import Agent
from society0.core_data import World
from society0.incremental_checkpoint import PersistenceKind
from society0.persistence import PersistenceManager
from society0.schedule import CodeSchedule


def _agent_schema() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "score": {
                "type": "integer",
                "persistence": {"kind": "replaceable"},
            }
        },
    }


def _world(tmp_path: Path) -> World:
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.environment_data["type"] = "plain"
    world.environment_data["schema"] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {},
    }
    world._agent_types = {"worker": {"state_schema": _agent_schema()}}
    world.add_agent_data("a", "worker", "rule")
    world.agents_data["a"]["state"] = {"score": 1}
    world.agents_data["a"]["properties"] = {"cash": 10}
    world.agents_data["a"]["reminders"] = ["bootstrap-only"]
    return world


def test_runtime_schema_declares_agent_properties_and_transient_reminders(tmp_path):
    world = _world(tmp_path)
    schema = world.compile_runtime_persistence_schema()

    assert schema.resolve(("agents", "a", "properties", "cash")).kind is PersistenceKind.REPLACEABLE
    reminders = schema.resolve(("agents", "a", "reminders"))
    assert reminders.kind is PersistenceKind.TRANSIENT
    assert reminders.has_default and reminders.default == []


@pytest.mark.asyncio
async def test_agent_properties_restore_and_reminders_reset_to_default(tmp_path):
    manager = PersistenceManager(str(tmp_path))
    world = _world(tmp_path)
    manager.configure_v4(world, world.compile_runtime_persistence_schema())
    schedule = CodeSchedule()
    try:
        await manager.publish_root(world, schedule)
        world.begin_persistence_tick(1)
        agent = Agent("a", world)
        agent.properties["cash"] = 12
        agent.add_reminder("tick-local")
        assert list(agent.reminders) == ["bootstrap-only", "tick-local"]
        agent.clear_reminders()
        marker = await manager.publish_delta(world.seal_persistence_tick(), schedule)
        assert marker is not None

        restored, _ = await manager.load_checkpoint(1, restore_chroma=False)
        restored_agent = Agent("a", restored)
        assert dict(restored_agent.properties) == {"cash": 12}
        assert list(restored_agent.reminders) == []
    finally:
        world.event_logger.close()
        manager.close()


def test_context_and_collection_iteration_cannot_bypass_tick_lease(tmp_path):
    world = _world(tmp_path)
    world.configure_persistence(world.compile_runtime_persistence_schema())
    world.begin_persistence_tick(1)
    agent = Agent("a", world)
    context_state = agent.get_state_for_context("agent_behavior", "test")
    context_state["score"] = 2
    properties = agent.properties
    nested_values = list(properties.values())
    assert nested_values == [10]
    world.seal_persistence_tick()

    with pytest.raises(RuntimeError, match="expired|sealed|lease"):
        context_state["score"] = 3
    with pytest.raises(RuntimeError, match="expired|sealed|lease"):
        properties["cash"] = 13
