import json

import pytest

from society0 import AgentGroup, Society0
from society0.decorators import env_type, fov
from society0.environment import Environment
from society0.env import BUILTIN_ENVS


pytestmark = pytest.mark.primary


def test_agent_group_is_public_for_environment_level_activation():
    assert AgentGroup.__module__ == "society0.schedule"


@env_type(
    type_name="external_test",
    config_schema={"type": "object", "properties": {}},
    state_schema={"type": "object", "properties": {}},
)
class ExternalTestEnvironment(Environment):
    def initialize(self, agents, world):
        self.state.setdefault("ticks", [])

    def before_tick(self, ctx):
        self.state["ticks"].append(ctx.step)

    @fov(description="Return the externally injected environment state.")
    def external_state(self):
        return {"ticks": list(self.state["ticks"])}


@pytest.mark.asyncio
async def test_society0_accepts_external_environment_factory_without_global_registration(tmp_path):
    config = {
        "agent_types": [{"id": "enterprise", "archetype": "rule"}],
        "agents": [{"id": "enterprise_a", "type": "enterprise", "state": {}}],
        "environment": {"type": "external_test", "state": {}},
    }
    assert "external_test" not in BUILTIN_ENVS

    engine = Society0(
        save_dir=str(tmp_path),
        base_config=config,
        environment_factory=ExternalTestEnvironment,
    )

    @engine.step(name="observe_external_environment")
    async def observe_external_environment(ctx):
        assert isinstance(ctx.env, ExternalTestEnvironment)
        assert ctx.capabilities.has("fov", "external_state", source="environment")
        return ctx.result(observations={"ticks": list(ctx.env.state["ticks"])})

    await engine.run(steps=1)

    checkpoint = json.loads(
        (tmp_path / "checkpoints" / "checkpoint_final.json").read_text(encoding="utf-8")
    )
    assert checkpoint["environment_data"]["state"]["ticks"] == [0]
    assert "external_test" not in BUILTIN_ENVS
