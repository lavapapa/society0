import pytest

from society0.core_data import World
from society0.decorators import (
    CAPABILITY_META_ATTR,
    ENV_META_ATTR,
    LOGIC_META_ATTR,
    action,
    env_type,
    logic,
)
from society0.environment import Environment
from society0.function_registry import FunctionRegistry
from society0.meta import EnvironmentMeta, LogicMeta


pytestmark = pytest.mark.primary


@env_type(
    type_name="role_permission_test",
    config_schema={"type": "object", "properties": {}},
    state_schema={"type": "object", "properties": {}},
)
class RolePermissionEnvironment(Environment):
    def initialize(self, agents, world):
        self.state.setdefault("calls", [])

    @action(
        description="Moderate one item.",
        role="moderator",
        tags=["moderation"],
    )
    def moderate(self, agent, item_id: str):
        self.state["calls"].append({"agent_id": agent.id, "item_id": item_id})
        return {"ok": True}

    @action(
        description="Review one item.",
        roles=["moderator", "administrator"],
        tags=["review"],
    )
    def review(self, agent, item_id: str):
        return {"ok": True, "item_id": item_id}

    @action(description="Read public information.")
    def read_public(self, agent):
        return {"ok": True, "agent_id": agent.id}


@logic.action(
    name="approve_request",
    role="moderator",
    tags=["approval"],
)
async def approve_request(agent, env, request_id: str):
    return {"ok": True, "agent_id": agent.id, "request_id": request_id}


def _world_with_role_agents(tmp_path):
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.add_agent_data("moderator_1", "moderator", archetype="rule")
    world.add_agent_data("reader_1", "reader", archetype="rule")
    world.add_agent_data("administrator_1", "administrator", archetype="rule")
    world.set_environment_factory(RolePermissionEnvironment)
    return world


def test_action_role_metadata_serializes_and_round_trips():
    moderate_meta = getattr(RolePermissionEnvironment.moderate, CAPABILITY_META_ATTR)
    review_meta = getattr(RolePermissionEnvironment.review, CAPABILITY_META_ATTR)
    public_meta = getattr(RolePermissionEnvironment.read_public, CAPABILITY_META_ATTR)

    assert moderate_meta.target_agent_types == ["moderator"]
    assert review_meta.target_agent_types == ["moderator", "administrator"]
    assert public_meta.target_agent_types == []
    assert moderate_meta.to_dict()["target_agent_types"] == ["moderator"]

    env_meta = getattr(RolePermissionEnvironment, ENV_META_ATTR)
    restored = EnvironmentMeta.from_dict(env_meta.to_dict())
    restored_by_name = {cap.name: cap for cap in restored.capabilities}
    assert restored_by_name["moderate"].target_agent_types == ["moderator"]
    assert restored_by_name["review"].target_agent_types == [
        "moderator",
        "administrator",
    ]
    assert restored_by_name["read_public"].target_agent_types == []


def test_logic_action_role_metadata_uses_agent_types_and_round_trips():
    meta = getattr(approve_request, LOGIC_META_ATTR)
    assert meta.target_agent_types == ["moderator"]
    restored = LogicMeta.from_dict(meta.to_dict(), func_ref=approve_request)
    assert restored.target_agent_types == ["moderator"]
    assert restored._func_ref is approve_request


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"role": "moderator", "roles": ["moderator"]}, "不能同时指定"),
        ({"role": ""}, "role 必须是非空字符串"),
        ({"role": 7}, "role 必须是非空字符串"),
        ({"roles": "moderator"}, r"roles 必须是非空 list\[str\]"),
        ({"roles": []}, r"roles 必须是非空 list\[str\]"),
        ({"roles": ["moderator", ""]}, "每一项都必须是非空字符串"),
        ({"roles": ["moderator", 7]}, "每一项都必须是非空字符串"),
    ],
)
def test_action_rejects_invalid_role_parameters(kwargs, message):
    with pytest.raises(ValueError, match=message):
        action(**kwargs)


def test_action_rejects_role_alias_with_internal_target_agent_types():
    with pytest.raises(ValueError, match="target_agent_types"):
        action(role="moderator", target_agent_types=["moderator"])


@pytest.mark.asyncio
async def test_actionset_only_contains_actions_available_to_agent_type(tmp_path):
    world = _world_with_role_agents(tmp_path)

    moderator = world.get_agent("moderator_1")
    moderator_actions = world.assemble_agent_actionset(moderator)
    assert {"moderate", "review", "read_public"} <= set(moderator_actions.actions)

    administrator = world.get_agent("administrator_1")
    administrator_actions = world.assemble_agent_actionset(administrator)
    assert "moderate" not in administrator_actions.actions
    assert {"review", "read_public"} <= set(administrator_actions.actions)

    reader = world.get_agent("reader_1")
    reader_actions = world.assemble_agent_actionset(reader)
    assert set(reader_actions.actions) == {"read_public"}
    assert reader_actions.filter_by_tags(["moderation"]).actions == {}
    assert reader_actions.filter_by_tags(["moderate"]).actions == {}

    with pytest.raises(ValueError, match="matched no available actions"):
        world._validate_action_filter(reader, ["moderation"])
    with pytest.raises(ValueError, match="matched no available actions"):
        world._validate_action_filter(reader, ["moderate"])

    assert await reader_actions.call_action("read_public") == {
        "ok": True,
        "agent_id": "reader_1",
    }


@pytest.mark.asyncio
async def test_action_wrapper_rechecks_agent_type_before_execution(tmp_path):
    world = _world_with_role_agents(tmp_path)
    moderator = world.get_agent("moderator_1")
    stale_actionset = world.assemble_agent_actionset(moderator)
    assert "moderate" in stale_actionset.actions

    # 模拟 ActionSet 组装后 Agent.type 发生变化。wrapper 仍应拒绝执行。
    world.agents_data[moderator.id]["type"] = "reader"
    with pytest.raises(PermissionError, match="not available to agent type 'reader'"):
        await stale_actionset.call_action("moderate", item_id="post_1")

    assert list(world.get_environment().state["calls"]) == []


@pytest.mark.asyncio
async def test_logic_action_role_is_enforced_during_actionset_assembly(tmp_path):
    world = _world_with_role_agents(tmp_path)
    registry = FunctionRegistry()
    meta = getattr(approve_request, LOGIC_META_ATTR)
    registry.agent_actions["tests.approve_request"] = {
        "function": approve_request,
        "description": meta.description,
        "meta": meta,
    }
    world.set_function_registry(registry)

    moderator_actions = world.assemble_agent_actionset(
        world.get_agent("moderator_1")
    )
    reader_actions = world.assemble_agent_actionset(world.get_agent("reader_1"))

    assert "tests.approve_request" in moderator_actions.actions
    assert "tests.approve_request" not in reader_actions.actions
    assert reader_actions.filter_by_tags(["approval"]).actions == {}
    assert await moderator_actions.call_action(
        "tests.approve_request",
        request_id="request_1",
    ) == {
        "ok": True,
        "agent_id": "moderator_1",
        "request_id": "request_1",
    }
