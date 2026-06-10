import os
import sys
from copy import deepcopy
from typing import List
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

from simengine.core_data import World
from simengine.events import StateChangeEvent
from simengine.schedule import StepFlow


def _create_world(tmp_path) -> World:
    """创建用于测试的 World，避免写入默认事件日志。"""
    return World(event_log_path=str(tmp_path / "events.jsonl"))


def test_build_node_diff_includes_environment_and_agent_changes(tmp_path) -> None:
    world = _create_world(tmp_path)
    step_flow = StepFlow(step_number=0, step_config={"nodes": []}, function_registry=MagicMock())

    before_agents = {
        "alice": {
            "id": "alice",
            "type": "test",
            "archetype": "rule",
            "state": {"mood": "neutral"},
            "properties": {},
            "reminders": [],
        }
    }
    before_environment = {
        "type": "base",
        "state": {"temperature": 20},
    }

    world.agents_data = deepcopy(before_agents)
    world.agents_data["alice"]["state"]["mood"] = "happy"

    world.environment_data = deepcopy(before_environment)
    world.environment_data["state"]["temperature"] = 25

    pending_events: List[StateChangeEvent] = [
        StateChangeEvent(
            target_type="agent",
            target_id="alice",
            path=["state", "mood"],
            operation="set",
            value="happy",
            old_value="neutral",
            context_stack=[],
        ),
        StateChangeEvent(
            target_type="environment",
            target_id="state",
            path=["temperature"],
            operation="set",
            value=25,
            old_value=20,
            context_stack=[],
        ),
    ]

    diffs = step_flow._build_node_diff(
        before_agents,
        before_environment,
        world,
        pending_events,
    )

    agent_diff = next(change for change in diffs if change["target"]["type"] == "agent")
    assert agent_diff["target"]["id"] == "alice"
    assert agent_diff["path"] == ["state", "mood"]
    assert agent_diff["old_value"] == "neutral"
    assert agent_diff["new_value"] == "happy"

    env_diff = next(change for change in diffs if change["target"]["type"] == "environment")
    assert env_diff["path"] == ["temperature"]
    assert env_diff["old_value"] == 20
    assert env_diff["new_value"] == 25
