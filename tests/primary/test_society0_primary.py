import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from society0 import EmbedModel, LLMModel, Society0
from society0.agent.core import _parse_structured_json_from_model_text
from society0.models import LLMModel as PublicLLMModel
from society0.schedule import AgentSelector, StepResult

pytestmark = pytest.mark.primary


def _base_config():
    return {
        "agent_types": [
            {"id": "social_user", "archetype": "rule"},
            {"id": "researcher", "archetype": "rule"},
        ],
        "agents": [
            {"id": "alice", "type": "social_user", "state": {"trust": 0.4}},
            {"id": "bob", "type": "social_user", "state": {"trust": 0.8}},
            {"id": "carol", "type": "researcher", "state": {"trust": 1.0}},
        ],
        "environment": {"type": "plain", "state": {"topic": "misinformation"}},
    }


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class TrustOutput(BaseModel):
    trust_score: float


def test_public_api_imports():
    assert Society0 is not None
    assert LLMModel.openai_compatible(id="llm", model="m", base_url="http://localhost/v1", api_key="x")
    assert EmbedModel.ollama(id="embed", model="nomic-embed-text")
    assert PublicLLMModel is LLMModel


@pytest.mark.asyncio
async def test_code_schedule_smoke_outputs_and_checkpoints(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())
    seen = []

    @engine.step(name="measure_trust")
    async def measure_trust(ctx):
        seen.append((ctx.step, ctx.step_name))
        users = ctx.agents.where(type="social_user")
        avg = sum(ctx.world.agents_data[agent_id]["state"]["trust"] for agent_id in users.ids()) / len(users)
        return ctx.result(metrics={"avg_trust": avg}, notes="measured")

    await engine.run(steps=3)

    assert seen == [(0, "measure_trust"), (1, "measure_trust"), (2, "measure_trust")]
    steps = _read_jsonl(tmp_path / "steps.jsonl")
    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    assert len(steps) == 3
    assert metrics[0]["metrics"] == {"avg_trust": 0.6000000000000001}
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["final_step"] == 3
    checkpoints = sorted(path.name for path in (tmp_path / "checkpoints").glob("checkpoint*.json"))
    assert checkpoints == ["checkpoint_000000.json", "checkpoint_final.json"]


@pytest.mark.asyncio
async def test_checkpoint_policy(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())

    @engine.step(name="noop")
    async def noop(ctx):
        return None

    await engine.run(steps=25)

    checkpoints = sorted(path.name for path in (tmp_path / "checkpoints").glob("checkpoint*.json"))
    assert checkpoints == [
        "checkpoint_000000.json",
        "checkpoint_000010.json",
        "checkpoint_000020.json",
        "checkpoint_final.json",
    ]


@pytest.mark.asyncio
async def test_agent_group_selection(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())

    @engine.step(name="select")
    async def select(ctx):
        selector = ctx.agents
        assert selector.all().ids() == ["alice", "bob", "carol"]
        assert selector.ids(["bob", "missing"]).ids() == ["bob"]
        assert selector.where(type="social_user").ids() == ["alice", "bob"]
        assert selector.where(trust=0.8).ids() == ["bob"]
        assert selector.sample(1, seed=1, where={"type": "social_user"}).ids() == ["alice"]
        assert selector.filter(lambda agent: agent.id.endswith("l")).ids() == ["carol"]
        return ctx.result()

    await engine.run(steps=1)


@pytest.mark.asyncio
async def test_instruct_and_interview_wrappers_pass_expected_options():
    class FakeWorld:
        step = 7
        agents_data = {
            "alice": {"id": "alice", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}}
        }

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            self.instruct_call = (agent_id, instruction, kwargs)
            return {"structured_output": {"trust_score": 0.5}}

        async def interview_agent(self, agent_id, question, **kwargs):
            self.interview_call = (agent_id, question, kwargs)
            return {"structured_output": {"trust_score": 0.75}}

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    world = FakeWorld()
    group = AgentSelector(world).ids(["alice"])

    instruct = await group.instruct(
        "act",
        fovs=["feed"],
        actions=["social"],
        output={"type": "object"},
        memory=False,
        model="fast",
        max_turns=1,
        concurrency=2,
        name="feed_interaction",
        reasoning_stages=[{"name": "think", "desc": "think first"}],
    )
    assert instruct.mean("trust_score") == 0.5
    assert world.instruct_call[2]["fovs"] == ["feed"]
    assert world.instruct_call[2]["action_tags"] == ["social"]
    assert world.instruct_call[2]["retrieve_memory"] is False
    assert world.instruct_call[2]["save_memory"] is False
    assert world.instruct_call[2]["model_id"] == "fast"
    assert world.instruct_call[2]["name"] == "feed_interaction"
    assert world.instruct_call[2]["reasoning_stages"] == [{"name": "think", "desc": "think first"}]

    interview = await group.interview(
        "rate trust",
        fovs=["recent_posts"],
        output=TrustOutput,
        retrieve_memory=True,
        save_memory=False,
        model="careful",
        name="trust_survey",
        reasoning_stages=[{"name": "answer", "desc": "answer directly"}],
    )
    assert interview.mean("trust_score") == 0.75
    assert world.interview_call[2]["fovs"] == ["recent_posts"]
    assert world.interview_call[2]["save_memory"] is False
    assert world.interview_call[2]["name"] == "trust_survey"
    assert world.interview_call[2]["reasoning_stages"] == [{"name": "answer", "desc": "answer directly"}]
    assert world.interview_call[2]["output_schema"]["type"] == "object"
    assert "trust_score" in world.interview_call[2]["output_schema"]["properties"]
    assert "action_tags" not in world.interview_call[2]


@pytest.mark.asyncio
async def test_instruct_and_interview_use_world_default_concurrency():
    class SlowWorld:
        step = 1
        _default_agent_concurrency = 2
        agents_data = {
            f"agent_{idx}": {"id": f"agent_{idx}", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}}
            for idx in range(6)
        }

        def __init__(self):
            self.in_flight = 0
            self.max_in_flight = 0

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.01)
            self.in_flight -= 1
            return {"structured_output": {"trust_score": 1.0}}

        async def interview_agent(self, agent_id, question, **kwargs):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.01)
            self.in_flight -= 1
            return {"structured_output": {"trust_score": 1.0}}

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    world = SlowWorld()
    group = AgentSelector(world).all()

    await group.instruct("act")
    assert world.max_in_flight == 2

    world.max_in_flight = 0
    await group.interview("rate", output=TrustOutput)
    assert world.max_in_flight == 2


@pytest.mark.asyncio
async def test_agent_group_without_runtime_falls_back_to_five_not_unbounded():
    class SlowWorld:
        step = 1
        agents_data = {
            f"agent_{idx}": {"id": f"agent_{idx}", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}}
            for idx in range(9)
        }

        def __init__(self):
            self.in_flight = 0
            self.max_in_flight = 0

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.01)
            self.in_flight -= 1
            return {"structured_output": {"trust_score": 1.0}}

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    world = SlowWorld()

    await AgentSelector(world).all().instruct("act")

    assert world.max_in_flight == 5


@pytest.mark.asyncio
async def test_explicit_concurrency_overrides_world_default():
    class SlowWorld:
        step = 1
        _default_agent_concurrency = 1
        agents_data = {
            f"agent_{idx}": {"id": f"agent_{idx}", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}}
            for idx in range(5)
        }

        def __init__(self):
            self.in_flight = 0
            self.max_in_flight = 0

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            await asyncio.sleep(0.01)
            self.in_flight -= 1
            return {"structured_output": {"trust_score": 1.0}}

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    world = SlowWorld()

    await AgentSelector(world).all().instruct("act", concurrency=3)

    assert world.max_in_flight == 3


@pytest.mark.asyncio
async def test_invalid_agent_group_concurrency_rejected():
    class FakeWorld:
        step = 1
        agents_data = {
            "alice": {"id": "alice", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}}
        }

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            return {"structured_output": {"trust_score": 1.0}}

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    with pytest.raises(ValueError, match="concurrency must be a positive integer"):
        await AgentSelector(FakeWorld()).all().instruct("act", concurrency=0)


@pytest.mark.asyncio
async def test_agent_group_treats_agent_status_error_as_batch_error():
    class FakeWorld:
        step = 1
        agents_data = {
            "alice": {"id": "alice", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}}
        }

        async def interview_agent(self, agent_id, question, **kwargs):
            return {"status": "error", "error": "bad schema", "structured_output": None}

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    result = await AgentSelector(FakeWorld()).all().interview("rate", output=TrustOutput)

    assert result.success_count == 0
    assert result.error_count == 1
    assert result.by_agent("alice").error == "bad schema"


@pytest.mark.asyncio
async def test_agent_group_treats_missing_structured_output_as_batch_error_when_schema_required():
    class FakeWorld:
        step = 1
        agents_data = {
            "alice": {"id": "alice", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}}
        }

        async def interview_agent(self, agent_id, question, **kwargs):
            return {
                "status": "success",
                "structured_output": None,
                "raw_output": {"content": "I answered but did not submit JSON."},
            }

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    result = await AgentSelector(FakeWorld()).all().interview("rate", output=TrustOutput)

    assert result.success_count == 0
    assert result.error_count == 1
    assert result.by_agent("alice").error == "missing structured_output"


def test_json_prefix_fallback_parses_prefilled_object_continuation():
    parsed = _parse_structured_json_from_model_text(
        '"trust_score": 3, "reason": "Plausible but underspecified."}\\n```',
        assume_prefilled_object=True,
    )

    assert parsed == {"trust_score": 3, "reason": "Plausible but underspecified."}


@pytest.mark.asyncio
async def test_code_step_rule_and_behavior_helpers(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())

    @engine.registry.env.rule(name="set_pressure")
    async def set_pressure(env, amount: float, context=None):
        env.state["pressure"] = amount
        return {"pressure": env.state["pressure"], "step": context.step_number}

    @engine.registry.sched.behavior(name="adjust_trust")
    async def adjust_trust(agent, env, delta: float, context=None):
        agent.state["trust"] = round(agent.state.get("trust", 0) + delta, 3)
        return {"trust": agent.state["trust"], "pressure": env.state.get("pressure"), "step": context.step_number}

    @engine.step(name="apply_registered_logic")
    async def apply_registered_logic(ctx):
        rule_result = await ctx.rule("set_pressure", amount=0.7)
        group = ctx.agents.where(type="social_user")
        behavior_result = await group.behavior("adjust_trust", delta=0.1, concurrency=1)
        via_ctx = await ctx.behavior("adjust_trust", agents=["carol"], delta=-0.2)
        return ctx.result(
            metrics={
                "pressure": rule_result["pressure"],
                "behavior_success": behavior_result.success_count,
                "ctx_behavior_success": via_ctx.success_count,
            },
            tables={"behavior": behavior_result.table(), "ctx_behavior": via_ctx.table()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"] == {
        "pressure": 0.7,
        "behavior_success": 2,
        "ctx_behavior_success": 1,
    }
    final_checkpoint = json.loads((tmp_path / "checkpoints" / "checkpoint_final.json").read_text(encoding="utf-8"))
    assert final_checkpoint["agents_data"]["alice"]["state"]["trust"] == 0.5
    assert final_checkpoint["agents_data"]["bob"]["state"]["trust"] == 0.9
    assert final_checkpoint["agents_data"]["carol"]["state"]["trust"] == 0.8


@pytest.mark.asyncio
async def test_capability_catalog_and_missing_logic_errors(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())

    @engine.registry.env.rule(name="set_pressure")
    async def set_pressure(env, amount: float = 0.1):
        env.state["pressure"] = amount
        return {"pressure": amount}

    @engine.registry.sched.behavior(name="adjust_trust")
    async def adjust_trust(agent, env, delta: float = 0.0):
        agent.state["trust"] += delta
        return {"trust": agent.state["trust"]}

    @engine.step(name="inspect_capabilities")
    async def inspect_capabilities(ctx):
        assert ctx.capabilities.has("rule", "set_pressure")
        assert ctx.capabilities.has("behavior", "adjust_trust")
        assert "set_pressure" in ctx.capabilities.names("rule")
        assert "adjust_trust" in ctx.capabilities.names("behavior")
        with pytest.raises(ValueError, match="Rule 'missing_rule' not found"):
            await ctx.rule("missing_rule")
        with pytest.raises(ValueError, match="Behavior 'missing_behavior' not found"):
            await ctx.agents.all().behavior("missing_behavior")
        return ctx.result(metrics={"rules": len(ctx.capabilities.rules())})

    await engine.run(steps=1)


def test_model_declaration_builds_endpoint_configs():
    llm = LLMModel.openai_compatible(
        id="default",
        model="gpt-test",
        base_url="http://localhost:9999/v1",
        api_key="test",
        concurrency=3,
    )
    embed = EmbedModel.ollama(id="embed", model="nomic-embed-text", base_url="http://localhost:11434")
    assert llm.endpoint_config()["concurrency"] == 3
    assert LLMModel.openai_compatible(model="gpt-test", base_url="http://localhost:9999/v1").endpoint_config()[
        "concurrency"
    ] == 5
    assert EmbedModel.ollama(model="nomic-embed-text").endpoint_config()["concurrency"] == 5
    assert embed.endpoint_config()["provider_type"] == "ollama"
    assert EmbedModel.openai(id="embed", model="text-embedding-3-small", dimensions=1536).endpoint_config()[
        "dimensions"
    ] == 1536


def test_model_declaration_rejects_invalid_concurrency():
    with pytest.raises(ValueError, match="concurrency must be a positive integer"):
        LLMModel.openai_compatible(model="gpt-test", base_url="http://localhost:9999/v1", concurrency=0)
    with pytest.raises(ValueError, match="concurrency must be a positive integer"):
        EmbedModel.ollama(model="nomic-embed-text", concurrency=0)


@pytest.mark.asyncio
async def test_society0_injects_model_concurrency_into_runtime(tmp_path):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_base_config(),
        llm=LLMModel.openai_compatible(
            id="default",
            model="gpt-test",
            base_url="http://localhost:9999/v1",
            api_key="test",
            concurrency=7,
        ),
        embed=EmbedModel.openai_compatible(
            id="embed",
            model="embed-test",
            base_url="http://localhost:9999/v1",
            api_key="test",
            concurrency=3,
        ),
    )

    @engine.step(name="inspect_runtime")
    async def inspect_runtime(ctx):
        return ctx.result(
            metrics={
                "agent_concurrency": ctx.world._default_agent_concurrency,
                "source_is_llm_model": int(ctx.world._default_agent_concurrency_source == "llm_model"),
            }
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"] == {"agent_concurrency": 7, "source_is_llm_model": 1}
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["runtime"]["agent_concurrency"] == 7
    assert summary["runtime"]["agent_concurrency_source"] == "llm_model"
    run_started = _read_jsonl(tmp_path / "events.jsonl")[0]
    assert run_started["event"] == "run_started"
    assert run_started["agent_concurrency"] == 7
    assert run_started["llm_concurrency"] == 7
    assert run_started["embed_concurrency"] == 3


@pytest.mark.asyncio
async def test_society0_global_agent_concurrency_overrides_model(tmp_path):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_base_config(),
        llm=LLMModel.openai_compatible(
            id="default",
            model="gpt-test",
            base_url="http://localhost:9999/v1",
            api_key="test",
            concurrency=9,
        ),
        agent_concurrency=4,
    )

    @engine.step(name="inspect_runtime")
    async def inspect_runtime(ctx):
        return ctx.result(
            metrics={
                "agent_concurrency": ctx.world._default_agent_concurrency,
                "source_is_society0": int(ctx.world._default_agent_concurrency_source == "society0"),
            }
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"] == {"agent_concurrency": 4, "source_is_society0": 1}
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["runtime"]["agent_concurrency"] == 4
    assert summary["runtime"]["agent_concurrency_source"] == "society0"


def test_legacy_schedule_importable():
    from society0.legacy.schedule import Schedule

    assert Schedule is not None


@pytest.mark.asyncio
async def test_no_debug_stdout(tmp_path, capsys):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())

    @engine.step(name="noop")
    async def noop(ctx):
        return StepResult()

    await engine.run(steps=1)
    captured = capsys.readouterr()
    assert "[DEBUG]" not in captured.out
    assert "[COMPILE DEBUG]" not in captured.out
    assert "[MEMORY]" not in captured.out
