import pytest

from society0 import AgentGroup, Society0
from society0.agent.agent_loop import execute_action_loop
from society0.core_data import ExecutionContext
from society0.decorators import action, env_type, fov
from society0.environment import Environment
from society0.env import BUILTIN_ENVS
from tests import read_gzip_json


pytestmark = pytest.mark.primary


def test_class_strict_action_schema_is_validated_during_decoration():
    with pytest.raises(ValueError, match="invalid JSON Schema"):

        @action(
            strict=True,
            parameters_schema={
                "type": "object",
                "properties": {"quantity": {"type": "not-a-json-type"}},
                "required": ["quantity"],
            },
        )
        def invalid_action(agent, quantity):
            return quantity

    with pytest.raises(ValueError, match="free-form object"):

        @action(
            strict=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "object",
                        "additionalProperties": True,
                    }
                },
                "required": ["content"],
            },
        )
        def free_form_action(agent, content):
            return content

    with pytest.raises(ValueError, match="free-form object"):

        @action(
            strict=True,
            parameters_schema={
                "type": "object",
                "properties": {"content": {"type": "object"}},
                "required": ["content"],
            },
        )
        def implicit_free_form_action(agent, content):
            return content


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
        self.state.setdefault("action_call_ids", [])

    def before_tick(self, ctx):
        self.state["ticks"].append(ctx.step)

    @fov(description="Return the externally injected environment state.")
    def external_state(self):
        return {"ticks": list(self.state["ticks"])}

    @action(description="Record the runtime call id without model input.")
    def record_action_call(
        self,
        agent,
        context: ExecutionContext,
    ):
        self.state["action_call_ids"].append(context.action_call_id)
        return {"ok": True}


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

    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert checkpoint["environment_data"]["state"]["ticks"] == [0]
    assert "external_test" not in BUILTIN_ENVS


@pytest.mark.asyncio
async def test_external_environment_action_receives_runtime_call_id(tmp_path):
    config = {
        "agent_types": [{"id": "enterprise", "archetype": "rule"}],
        "agents": [{"id": "enterprise_a", "type": "enterprise", "state": {}}],
        "environment": {"type": "external_test", "state": {}},
    }
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=config,
        environment_factory=ExternalTestEnvironment,
    )

    @engine.step(name="call_external_environment_action")
    async def call_external_environment_action(ctx):
        agent = ctx.world.get_agent("enterprise_a")
        actionset = ctx.world.assemble_agent_actionset(agent)
        result = await actionset.call_action(
            "record_action_call",
            _society0_call_id="call_external_1",
        )
        assert result == {"ok": True}
        return ctx.result()

    await engine.run(steps=1)

    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert checkpoint["environment_data"]["state"]["action_call_ids"] == [
        "call_external_1"
    ]


@pytest.mark.asyncio
async def test_class_environment_strict_action_reaches_provider_payload(tmp_path):
    @env_type(
        type_name="strict_external_test",
        config_schema={"type": "object", "properties": {}},
        state_schema={"type": "object", "properties": {}},
    )
    class StrictExternalEnvironment(Environment):
        @action(
            description="Submit a quantity.",
            strict=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "quantity": {"type": "integer"},
                    "note": {"type": "string", "default": "scheduled"},
                },
                "required": ["quantity"],
            },
        )
        def submit_quantity(
            self,
            agent,
            quantity: int,
            note: str = "scheduled",
        ):
            return {"agent_id": agent.id, "quantity": quantity, "note": note}

    engine = Society0(
        save_dir=str(tmp_path),
        base_config={
            "agent_types": [{"id": "enterprise", "archetype": "rule"}],
            "agents": [{"id": "enterprise_a", "type": "enterprise", "state": {}}],
            "environment": {"type": "strict_external_test", "state": {}},
        },
        environment_factory=StrictExternalEnvironment,
    )
    requests = []

    @engine.step(name="inspect_strict_tool_payload")
    async def inspect_strict_tool_payload(ctx):
        actionset = ctx.world.assemble_agent_actionset(
            ctx.world.get_agent("enterprise_a")
        )

        async def fake_llm_call(request):
            requests.append(request)
            return {"role": "assistant", "content": "done", "tool_calls": []}

        await execute_action_loop(
            instruction="Inspect the available action.",
            action_set=actionset,
            system_prompt="Test agent",
            stages=[{"name": "act", "desc": "act"}],
            llm_call=fake_llm_call,
            max_turns=1,
        )
        return ctx.result()

    await engine.run(steps=1)

    assert engine.registry.env_agent_tools["submit_quantity"]["strict"] is True
    submit_tool = next(
        tool
        for tool in requests[0]["tools"]
        if tool["function"]["name"] == "submit_quantity"
    )
    assert submit_tool["function"]["strict"] is True
    assert submit_tool["function"]["parameters"]["additionalProperties"] is False
    assert submit_tool["function"]["parameters"]["required"] == ["quantity", "note"]
    assert submit_tool["function"]["parameters"]["properties"]["note"]["type"] == [
        "string",
        "null",
    ]


@pytest.mark.asyncio
async def test_strict_environment_action_rejects_missing_argument_without_execution(tmp_path):
    @env_type(
        type_name="strict_validation_test",
        config_schema={"type": "object", "properties": {}},
        state_schema={"type": "object", "properties": {}},
    )
    class StrictValidationEnvironment(Environment):
        def initialize(self, agents, world):
            self.state["calls"] = []

        @action(
            description="Submit a required quantity.",
            strict=True,
            parameters_schema={
                "type": "object",
                "properties": {"quantity": {"type": "integer"}},
                "required": ["quantity"],
            },
        )
        def submit_quantity(self, agent, quantity: int = 99):
            self.state["calls"].append(quantity)
            return {"quantity": quantity}

    engine = Society0(
        save_dir=str(tmp_path),
        base_config={
            "agent_types": [{"id": "enterprise", "archetype": "rule"}],
            "agents": [{"id": "enterprise_a", "type": "enterprise", "state": {}}],
            "environment": {"type": "strict_validation_test", "state": {}},
        },
        environment_factory=StrictValidationEnvironment,
    )
    loop_results = []

    @engine.step(name="reject_invalid_strict_action")
    async def reject_invalid_strict_action(ctx):
        responses = iter(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "missing_quantity",
                            "type": "function",
                            "function": {
                                "name": "submit_quantity",
                                "arguments": "{}",
                            },
                        }
                    ],
                },
                {"role": "assistant", "content": "done", "tool_calls": []},
            ]
        )

        async def fake_llm_call(_request):
            return next(responses)

        loop_results.append(
            await execute_action_loop(
                instruction="Submit a quantity.",
                action_set=ctx.world.assemble_agent_actionset(
                    ctx.world.get_agent("enterprise_a")
                ),
                system_prompt="Test agent",
                stages=[{"name": "act", "desc": "act"}],
                llm_call=fake_llm_call,
                max_turns=2,
            )
        )
        return ctx.result()

    await engine.run(steps=1)

    checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert checkpoint["environment_data"]["state"]["calls"] == []
    assert loop_results[0].action_calls[0]["status"] == "error"
    assert "quantity" in loop_results[0].action_calls[0]["error"]


@pytest.mark.asyncio
async def test_strict_nullable_argument_recovers_environment_method_default(tmp_path):
    @env_type(
        type_name="strict_default_test",
        config_schema={"type": "object", "properties": {}},
        state_schema={"type": "object", "properties": {}},
    )
    class StrictDefaultEnvironment(Environment):
        @action(
            description="Submit an optional note.",
            strict=True,
            parameters_schema={
                "type": "object",
                "properties": {"note": {"type": "string", "default": "scheduled"}},
                "required": [],
            },
        )
        def submit_note(self, agent, note: str = "scheduled"):
            return {"note": note}

    engine = Society0(
        save_dir=str(tmp_path),
        base_config={
            "agent_types": [{"id": "enterprise", "archetype": "rule"}],
            "agents": [{"id": "enterprise_a", "type": "enterprise", "state": {}}],
            "environment": {"type": "strict_default_test", "state": {}},
        },
        environment_factory=StrictDefaultEnvironment,
    )
    action_results = []

    @engine.step(name="recover_strict_default")
    async def recover_strict_default(ctx):
        action_results.append(
            await ctx.world.assemble_agent_actionset(
                ctx.world.get_agent("enterprise_a")
            ).call_action("submit_note", note=None)
        )
        return ctx.result()

    await engine.run(steps=1)

    assert action_results == [{"note": "scheduled"}]


@pytest.mark.asyncio
async def test_strict_nested_optional_null_is_omitted_before_environment_call(tmp_path):
    @env_type(
        type_name="strict_nested_optional_test",
        config_schema={"type": "object", "properties": {}},
        state_schema={"type": "object", "properties": {}},
    )
    class StrictNestedOptionalEnvironment(Environment):
        @action(
            description="Submit a plan with an optional reason.",
            strict=True,
            parameters_schema={
                "type": "object",
                "properties": {
                    "plan": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "reason": {"type": "string"},
                        },
                        "required": ["content"],
                    },
                    "note": {"type": "string"},
                },
                "required": ["plan"],
            },
        )
        def submit_plan(self, agent, plan: dict, note: str = "scheduled"):
            return {"plan": plan, "note": note}

    engine = Society0(
        save_dir=str(tmp_path),
        base_config={
            "agent_types": [{"id": "enterprise", "archetype": "rule"}],
            "agents": [{"id": "enterprise_a", "type": "enterprise", "state": {}}],
            "environment": {"type": "strict_nested_optional_test", "state": {}},
        },
        environment_factory=StrictNestedOptionalEnvironment,
    )
    action_results = []

    @engine.step(name="strip_strict_nested_null")
    async def strip_strict_nested_null(ctx):
        action_results.append(
            await ctx.world.assemble_agent_actionset(
                ctx.world.get_agent("enterprise_a")
            ).call_action(
                "submit_plan",
                plan={"content": "hold", "reason": None},
                note=None,
            )
        )
        return ctx.result()

    await engine.run(steps=1)

    assert action_results == [
        {"plan": {"content": "hold"}, "note": "scheduled"}
    ]
