import json
import subprocess
import sys
from pathlib import Path

import pytest

from society0 import EmbedModel, LLMModel, Society0
from society0.resource_managers import EmbeddingManager

pytestmark = pytest.mark.e2e


def _config(agent_count=4, *, archetype="rule"):
    agents = []
    for idx in range(agent_count):
        agents.append(
            {
                "id": f"user_{idx}",
                "type": "social_user",
                "state": {"trust": 0.4 + idx * 0.1, "exposure": 0},
            }
        )
    return {
        "agent_types": [{"id": "social_user", "archetype": archetype}],
        "agents": agents,
        "environment": {
            "type": "plain",
            "state": {"misinformation_pressure": 0.1, "correction_strength": 0.03},
        },
    }


def _round_robin_config():
    return {
        "agent_types": [{"id": "participant", "archetype": "rule"}],
        "agents": [
            {"id": f"participant_{idx}", "type": "participant", "state": {}}
            for idx in range(4)
        ],
        "environment": {
            "type": "round_robin_conversation",
            "config": {"group_size": 4, "session_duration_minutes": 5},
            "state": {},
        },
    }


def _jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_e2e_default_run_writes_expected_artifacts_and_state(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_config(), checkpoint_every=10)

    @engine.step(name="expose")
    async def expose(ctx):
        pressure = ctx.world.environment_data["state"]["misinformation_pressure"]
        correction = ctx.world.environment_data["state"]["correction_strength"]
        for agent_id in ctx.agents.where(type="social_user").ids():
            state = ctx.world.agents_data[agent_id]["state"]
            state["exposure"] += 1
            state["trust"] = max(0.0, min(1.0, state["trust"] + pressure - correction))
        return ctx.result(observations={"pressure": pressure})

    @engine.step(name="measure")
    async def measure(ctx):
        rows = [
            {
                "agent_id": agent_id,
                "trust": round(ctx.world.agents_data[agent_id]["state"]["trust"], 4),
                "exposure": ctx.world.agents_data[agent_id]["state"]["exposure"],
            }
            for agent_id in ctx.agents.where(type="social_user").ids()
        ]
        avg_trust = sum(row["trust"] for row in rows) / len(rows)
        return ctx.result(metrics={"avg_trust": round(avg_trust, 4)}, tables={"trust": rows})

    await engine.run(steps=12)

    assert (tmp_path / "steps.jsonl").is_file()
    assert (tmp_path / "metrics.jsonl").is_file()
    assert (tmp_path / "events.jsonl").is_file()
    assert (tmp_path / "summary.json").is_file()
    assert (tmp_path / "chroma_store").is_dir()
    assert not (tmp_path / "events.index.jsonl").exists()
    assert not list((tmp_path / "diffs").glob("*.json*"))

    steps = _jsonl(tmp_path / "steps.jsonl")
    metrics = _jsonl(tmp_path / "metrics.jsonl")
    events = _jsonl(tmp_path / "events.jsonl")
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    checkpoint_names = sorted(path.name for path in (tmp_path / "checkpoints").glob("checkpoint*.json"))

    assert len(steps) == 24
    assert len(metrics) == 12
    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "run_completed"
    assert summary["final_step"] == 12
    assert summary["code_steps"] == ["expose", "measure"]
    assert checkpoint_names == ["checkpoint_000000.json", "checkpoint_000010.json", "checkpoint_final.json"]

    final_checkpoint = json.loads((tmp_path / "checkpoints" / "checkpoint_final.json").read_text(encoding="utf-8"))
    assert final_checkpoint["step"] == 12
    assert final_checkpoint["agents_data"]["user_0"]["state"]["exposure"] == 12


@pytest.mark.asyncio
async def test_e2e_llm_model_declaration_initializes_llm_agents_without_network_call(tmp_path):
    llm = LLMModel.openai_compatible(
        id="default",
        model="fake-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="test-key",
        concurrency=2,
    )
    engine = Society0(save_dir=str(tmp_path), base_config=_config(agent_count=1, archetype="llm"), llm=llm)

    @engine.step(name="inspect_llm_agent")
    async def inspect_llm_agent(ctx):
        agent = ctx.world.get_agent("user_0")
        return ctx.result(
            metrics={
                "has_memory": int(getattr(agent, "_memory", None) is not None),
                "has_llm_call": int(getattr(agent, "_llm_call", None) is not None),
            }
        )

    await engine.run(steps=1)

    metrics = _jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"] == {"has_memory": 1, "has_llm_call": 1}
    assert (tmp_path / "checkpoints" / "checkpoint_final.json").is_file()


@pytest.mark.asyncio
async def test_e2e_embed_model_dimensions_reach_memory_and_manager_closes(tmp_path, monkeypatch):
    closed_dimensions = []
    original_close = EmbeddingManager.close

    async def tracking_close(self):
        closed_dimensions.append(self.default_dimensions)
        await original_close(self)

    monkeypatch.setattr(EmbeddingManager, "close", tracking_close)

    llm = LLMModel.openai_compatible(
        id="default",
        model="fake-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="test-key",
    )
    embed = EmbedModel.openai_compatible(
        id="default_embed",
        model="fake-embed",
        base_url="http://127.0.0.1:9/v1",
        api_key="test-key",
        dimensions=1536,
    )
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_config(agent_count=1, archetype="llm"),
        llm=llm,
        embed=embed,
    )

    @engine.step(name="inspect_memory")
    async def inspect_memory(ctx):
        agent = ctx.world.get_agent("user_0")
        memory = getattr(agent, "_memory", None)
        return ctx.result(
            metrics={
                "world_embedding_dim": int(getattr(ctx.world, "_embedding_dim", 0)),
                "memory_embedding_dim": int(getattr(memory, "embedding_dim", 0)),
            }
        )

    await engine.run(steps=1)

    metrics = _jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"] == {"world_embedding_dim": 1536, "memory_embedding_dim": 1536}
    assert closed_dimensions == [1536]


@pytest.mark.asyncio
async def test_e2e_failed_step_records_failed_event_and_final_checkpoint(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_config(agent_count=1))

    @engine.step(name="boom")
    async def boom(ctx):
        raise RuntimeError("intentional e2e failure")

    with pytest.raises(RuntimeError, match="intentional e2e failure"):
        await engine.run(steps=2)

    events = _jsonl(tmp_path / "events.jsonl")
    assert events[-1]["event"] == "run_failed"
    assert events[-1]["failed_step"] == 0
    assert not any(event["event"] == "run_completed" for event in events)
    assert (tmp_path / "checkpoints" / "checkpoint_final.json").is_file()
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["final_step"] == 0


@pytest.mark.asyncio
async def test_e2e_builtin_round_robin_rule_behavior_and_capabilities(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_round_robin_config())

    @engine.step(name="round_robin_logic")
    async def round_robin_logic(ctx):
        assert ctx.capabilities.has("rule", "advance_round_robin_with_pairing")
        assert ctx.capabilities.has("behavior", "mark_conversation_participant")
        assert ctx.capabilities.has("fov", "get_conversation_fov")
        assert ctx.capabilities.has("action", "send_message_to_partner")

        pairing = await ctx.rule("advance_round_robin_with_pairing", round_number=1)
        marked = await ctx.agents.ids(["participant_0", "participant_1"]).behavior(
            "mark_conversation_participant",
            marker="baseline-ready",
            concurrency=1,
        )
        return ctx.result(
            metrics={
                "successful_pairs": pairing["successful_pairs"],
                "marked": marked.success_count,
                "behavior_errors": marked.error_count,
            },
            tables={"marked": marked.table()},
        )

    await engine.run(steps=1)

    metrics = _jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"] == {
        "successful_pairs": 2,
        "marked": 2,
        "behavior_errors": 0,
    }
    final_checkpoint = json.loads((tmp_path / "checkpoints" / "checkpoint_final.json").read_text(encoding="utf-8"))
    assert final_checkpoint["agents_data"]["participant_0"]["state"]["conversation_marker"] == "baseline-ready"
    assert final_checkpoint["environment_data"]["state"]["pairing_status"]["current_round"] == 1
    assert len(final_checkpoint["environment_data"]["state"]["pairing_status"]["completed_pairs"]) == 2


@pytest.mark.parametrize(
    "script_name,expected_run_dir",
    [
        ("example_usage.py", Path("runs/basic_example")),
        ("misinformation_trust_demo.py", Path("runs/misinformation_trust_demo")),
    ],
)
def test_e2e_public_example_script_runs_from_user_perspective(tmp_path, script_name, expected_run_dir):
    repo = Path(__file__).resolve().parents[2]
    script = repo / "examples" / script_name

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "complete" in result.stdout.lower()
    run_dir = tmp_path / expected_run_dir
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["final_step"] > 0
    assert (run_dir / "steps.jsonl").is_file()
    assert (run_dir / "metrics.jsonl").is_file()
    assert (run_dir / "checkpoints" / "checkpoint_final.json").is_file()
