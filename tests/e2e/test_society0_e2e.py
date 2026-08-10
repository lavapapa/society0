import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pytest

from society0 import EmbedModel, LLMModel, Society0
from society0.core_data import ExecutionContext
from society0.resource_managers import EmbeddingManager
from tests import read_gzip_json

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


def _fake_embed_model(*, dimensions=512):
    return EmbedModel.openai_compatible(
        id="default_embed",
        model="fake-embed",
        base_url="http://127.0.0.1:9/v1",
        api_key="test-key",
        dimensions=dimensions,
    )


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


def _social_network_config():
    return {
        "agent_types": [{"id": "social_user", "archetype": "rule"}],
        "agents": [
            {"id": "author", "type": "social_user", "state": {}},
            {"id": "commenter", "type": "social_user", "state": {}},
            {"id": "reposter", "type": "social_user", "state": {}},
            {"id": "viewer", "type": "social_user", "state": {}},
        ],
        "environment": {
            "type": "social_network",
            "config": {
                "social_media": {
                    "recommendation": {
                        "post_count": 2,
                        "candidate_count": 1,
                        "use_embedding_similarity": False,
                        "chronological_weight": 0.1,
                        "engagement_weight": 1.0,
                        "similarity_weight": 0.0,
                        "network_weight": 0.0,
                    },
                    "content_length_limit": -1,
                }
            },
            "state": {},
        },
    }


def _jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _action_context(ctx, agent_id: str) -> ExecutionContext:
    return ExecutionContext(
        world=ctx.world,
        step=None,
        node=None,
        caller=ctx.world.get_agent(agent_id),
        event_logger=ctx.world.event_logger,
        log_context=ctx.log,
    )


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
    checkpoint_names = sorted(
        path.name for path in (tmp_path / "checkpoints").glob("checkpoint*.json*")
    )

    assert len(steps) == 24
    assert len(metrics) == 12
    assert events[0]["event"] == "run_started"
    assert events[-1]["event"] == "run_completed"
    assert all(event.get("event") for event in events)
    assert summary["final_step"] == 12
    assert summary["code_steps"] == ["expose", "measure"]
    assert summary["events"]["by_event"]["run_started"] == 1
    assert summary["events"]["by_event"]["run_completed"] == 1
    versioned_worlds = []
    for step in (0, 10):
        marker = json.loads(
            (tmp_path / "checkpoints" / "complete" / f"step_{step:06d}.json").read_text(
                encoding="utf-8"
            )
        )
        versioned_worlds.append(marker["world_file"])
    assert checkpoint_names == sorted([*versioned_worlds, "checkpoint_final.json.gz"])

    final_checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
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
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_config(agent_count=1, archetype="llm"),
        llm=llm,
        embed=_fake_embed_model(),
    )

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
    assert (tmp_path / "checkpoints" / "checkpoint_final.json.gz").is_file()


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
    assert (tmp_path / "checkpoints" / "checkpoint_final.json.gz").is_file()
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["steps_requested"] == 2
    assert summary["steps_run"] == 0
    assert summary["steps_completed"] == 0
    assert summary["final_step"] == 0
    assert summary["failed"] is True
    assert summary["failure"]["failed_step"] == 0
    assert summary["failure"]["error_type"] == "RuntimeError"
    assert summary["outputs"]["files"]["diagnostics.md"]["bytes"] == (tmp_path / "diagnostics.md").stat().st_size
    diagnostics = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    assert "Status: failed" in diagnostics
    assert "The run failed with `RuntimeError` at step 0: intentional e2e failure" in diagnostics


@pytest.mark.asyncio
async def test_e2e_builtin_round_robin_rule_behavior_and_capabilities(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_round_robin_config())

    @engine.step(name="round_robin_logic")
    async def round_robin_logic(ctx):
        assert ctx.capabilities.has("rule", "advance_round_robin_with_pairing")
        assert ctx.capabilities.has("rule", "env.advance_round_robin_with_pairing")
        assert ctx.capabilities.has("behavior", "mark_conversation_participant")
        assert ctx.capabilities.has("behavior", "env.mark_conversation_participant")
        assert ctx.capabilities.has("fov", "get_conversation_fov")
        assert ctx.capabilities.has(
            "fov",
            "environments.round_robin_conversation.fovs.get_conversation_fov",
        )
        assert ctx.capabilities.has("action", "send_message_to_partner")
        assert ctx.capabilities.has("action", "env.send_message_to_partner")
        rule_entry = ctx.capabilities.get("rule", "env.advance_round_robin_with_pairing")
        behavior_entry = ctx.capabilities.get("behavior", "mark_conversation_participant")
        action_entry = ctx.capabilities.get("action", "env.send_message_to_partner")
        assert rule_entry is not None
        assert rule_entry["source"] == "environment"
        assert rule_entry["kind"] == "rule"
        assert rule_entry["func_name"] == "advance_round_robin_with_pairing"
        assert "env.advance_round_robin_with_pairing" in rule_entry["aliases"]
        assert "round_number" in rule_entry["parameters"]["properties"]
        assert behavior_entry is not None
        assert behavior_entry["source"] == "environment"
        assert "marker" in behavior_entry["parameters"]["properties"]
        assert action_entry is not None
        assert action_entry["source"] == "environment"
        assert "content" in action_entry["parameters"]["properties"]
        with pytest.raises(ValueError) as wrong_kind:
            await ctx.agents.all().behavior("send_message_to_partner")
        wrong_kind_message = str(wrong_kind.value)
        assert "'send_message_to_partner' is registered as action, not behavior" in wrong_kind_message
        assert "actions with instruct(..., actions=[...])" in wrong_kind_message
        with pytest.raises(ValueError) as wrong_alias_kind:
            await ctx.agents.all().behavior("env.send_message_to_partner")
        wrong_alias_message = str(wrong_alias_kind.value)
        assert "'env.send_message_to_partner' is registered as action, not behavior" in wrong_alias_message
        assert "actions with instruct(..., actions=[...])" in wrong_alias_message

        pairing = await ctx.rule("advance_round_robin_with_pairing", round_number=1)
        sender_actionset = ctx.world.assemble_agent_actionset(ctx.world.get_agent("participant_0"))
        social_actionset = sender_actionset.filter_by_tags(["send_message_to_partner"])
        assert "send_message_to_partner" in social_actionset.actions
        sent = await social_actionset.call_action(
            "send_message_to_partner",
            content="Hello from participant_0.",
        )
        assert sent["status"] == "success"
        partner_id = sent["sent_to"]
        partner_fov = await ctx.env.get_conversation_fov(ctx.world.get_agent(partner_id), ctx.env)
        assert "Hello from participant_0." in partner_fov

        broadcast_actionset = sender_actionset.filter_by_tags(["broadcast_to_group"])
        assert "broadcast_to_group" in broadcast_actionset.actions
        broadcast = await broadcast_actionset.call_action(
            "broadcast_to_group",
            content="Round 1 group update.",
        )
        assert broadcast["status"] == "success"
        assert len(broadcast["delivered"]) == 3
        group_peer_fov = await ctx.env.get_conversation_fov(ctx.world.get_agent("participant_1"), ctx.env)
        assert "Round 1 group update." in group_peer_fov

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
                "messages_sent": ctx.env.state["message_counter"],
            },
            tables={"marked": marked.table()},
        )

    await engine.run(steps=1)

    metrics = _jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"] == {
        "successful_pairs": 2,
        "marked": 2,
        "behavior_errors": 0,
        "messages_sent": 4,
    }
    final_checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    assert final_checkpoint["agents_data"]["participant_0"]["state"]["conversation_marker"] == "baseline-ready"
    assert final_checkpoint["environment_data"]["state"]["pairing_status"]["current_round"] == 1
    assert len(final_checkpoint["environment_data"]["state"]["pairing_status"]["completed_pairs"]) == 2
    assert final_checkpoint["environment_data"]["state"]["message_counter"] == 4
    round_messages = final_checkpoint["environment_data"]["state"]["round_messages"]["1"]
    assert any(
        message["content"] == "Hello from participant_0."
        for message in round_messages["participant_3"]
    )
    assert any(
        message["content"] == "Round 1 group update."
        for message in round_messages["participant_1"]
    )
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    rule_entries = summary["capabilities"]["by_kind"]["rules"]
    action_entries = summary["capabilities"]["by_kind"]["actions"]
    round_rule = next(entry for entry in rule_entries if entry["name"] == "advance_round_robin_with_pairing")
    send_action = next(entry for entry in action_entries if entry["name"] == "send_message_to_partner")
    assert "env.advance_round_robin_with_pairing" in round_rule["aliases"]
    assert round_rule["func_name"] == "advance_round_robin_with_pairing"
    assert "round_number" in round_rule["parameters"]["properties"]
    assert "env.send_message_to_partner" in send_action["aliases"]
    assert "content" in send_action["parameters"]["properties"]


@pytest.mark.asyncio
async def test_e2e_social_network_recommendation_flushes_impressions_after_tick(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_network_config())
    observed = {}

    @engine.step(name="social_round")
    async def social_round(ctx):
        await ctx.env.publish_post(
            context=_action_context(ctx, "author"),
            content="A public claim that will receive engagement.",
            tags=["claim"],
        )
        await ctx.env.comment(
            context=_action_context(ctx, "commenter"),
            post_id="post_1",
            content="I have a question about this claim.",
        )
        await ctx.env.repost(
            context=_action_context(ctx, "reposter"),
            post_id="post_1",
            commentary="Sharing for discussion.",
        )
        feed = await ctx.env.recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        observed["recommended"] = list(ctx.env._pending_recommended_posts["viewer"])
        observed["state_recommended_during_step"] = dict(ctx.env.state.get("recommended_posts", {}))
        observed["view_count_during_step"] = ctx.env.state["posts"]["post_1"].get("view_count", 0)
        return ctx.result(
            metrics={"recommended_count": len(observed["recommended"])},
            observations={"feed_has_post_1": "post_1" in feed},
        )

    await engine.run(steps=1)

    assert observed["recommended"][0] == "post_1"
    assert observed["state_recommended_during_step"] == {}
    assert observed["view_count_during_step"] == 0
    final_checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    posts = final_checkpoint["environment_data"]["state"]["posts"]
    assert posts["post_1"]["view_count"] == 1
    assert final_checkpoint["environment_data"]["state"]["recommended_posts"]["viewer"][0] == "post_1"
    assert len(posts["post_1"]["replies"]) == 1
    assert posts["post_2"]["reply_to"] == "post_1"


@pytest.mark.asyncio
async def test_e2e_social_browse_completion_action_tags_stop_after_write_action(tmp_path, monkeypatch):
    calls_by_agent_interaction = defaultdict(int)

    class FakeLLMManager:
        async def request(self, payload):
            metadata = payload.get("metadata") or {}
            agent_id = metadata.get("agent_id")
            interaction_name = metadata.get("interaction_name")
            key = (agent_id, interaction_name)
            calls_by_agent_interaction[key] += 1
            call_index = calls_by_agent_interaction[key]

            if interaction_name == "publish_seed":
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"publish_{agent_id}",
                            "type": "function",
                            "function": {
                                "name": "publish_post",
                                "arguments": json.dumps(
                                    {
                                        "content": f"{agent_id} says campus life is stressful but manageable.",
                                        "tags": ["campus", "stress"],
                                    }
                                ),
                            },
                        }
                    ],
                }

            if interaction_name == "browse_round" and call_index == 1:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"read_{agent_id}",
                            "type": "function",
                            "function": {
                                "name": "get_trending_posts",
                                "arguments": "{}",
                            },
                        }
                    ],
                }

            if interaction_name == "browse_round" and call_index == 2:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": f"comment_{agent_id}",
                            "type": "function",
                            "function": {
                                "name": "comment",
                                "arguments": json.dumps(
                                    {
                                        "post_id": "post_1",
                                        "content": f"{agent_id} agrees with this concern.",
                                    }
                                ),
                            },
                        }
                    ],
                }

            return {"role": "assistant", "content": "done", "tool_calls": []}

        async def close(self):
            return None

    monkeypatch.setattr(LLMModel, "build_manager", lambda self, *, log_context=None: FakeLLMManager())
    llm = LLMModel.openai_compatible(
        id="default",
        model="fake-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="test-key",
        concurrency=3,
    )
    config = {
        "agent_types": [{"id": "social_user", "archetype": "llm"}],
        "agents": [
            {
                "id": f"user_{idx}",
                "type": "social_user",
                "persona": "A concise campus social media user.",
                "state": {},
            }
            for idx in range(3)
        ],
        "environment": {
            "type": "social_network",
            "config": {
                "social_media": {
                    "recommendation": {
                        "post_count": 2,
                        "use_embedding_similarity": False,
                    },
                    "content_length_limit": -1,
                }
            },
            "state": {},
        },
    }
    engine = Society0(save_dir=str(tmp_path), base_config=config, llm=llm, embed=_fake_embed_model())

    @engine.step(name="publish_seed")
    async def publish_seed(ctx):
        result = await ctx.agents.all().instruct(
            "Publish one seed post.",
            actions=["publish_post"],
            retrieve_memory=False,
            max_turns=3,
            action_call_limits={"publish_post": 1},
            name="publish_seed",
        )
        return ctx.result(metrics={"publish_errors": result.error_count}, tables={"publish": result.table()})

    @engine.step(name="browse_round")
    async def browse_round(ctx):
        result = await ctx.agents.all().instruct(
            "Browse the recommended feed. You may read trending posts first; make one real interaction if useful.",
            fovs=["recommended_feed"],
            actions=["get_trending_posts", "comment"],
            retrieve_memory=False,
            max_turns=4,
            completion_action_tags=["social_write"],
            required_action_tags=["social_write"],
            name="browse_round",
        )
        return ctx.result(
            metrics={
                "browse_errors": result.error_count,
                "browse_success": result.success_count,
                "browse_action_total": len(result.actions()),
                "max_browse_turns": max(row["total_turns"] for row in result.table()),
            },
            tables={"browse": result.table(), "browse_actions": result.actions()},
            observations={"action_counts": result.action_counts()},
        )

    await engine.run(steps=1)

    steps = _jsonl(tmp_path / "steps.jsonl")
    browse_step = next(item for item in steps if item["step_name"] == "browse_round")
    browse_metrics = browse_step["result"]["metrics"]
    browse_actions = browse_step["result"]["tables"]["browse_actions"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    final_checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )
    posts = final_checkpoint["environment_data"]["state"]["posts"]

    assert browse_metrics["browse_errors"] == 0
    assert browse_metrics["browse_success"] == 3
    assert browse_metrics["max_browse_turns"] == 2
    assert browse_step["result"]["observations"]["action_counts"] == {
        "get_trending_posts": 3,
        "comment": 3,
    }
    assert [calls_by_agent_interaction[(f"user_{idx}", "browse_round")] for idx in range(3)] == [2, 2, 2]
    assert [action["action_name"] for action in browse_actions] == [
        "get_trending_posts",
        "comment",
        "get_trending_posts",
        "comment",
        "get_trending_posts",
        "comment",
    ]
    assert summary["agent_operations"]["browse_round"]["turns_max"] == 2
    assert summary["agent_operations"]["browse_round"]["action_counts"] == {
        "comment": 3,
        "get_trending_posts": 3,
    }
    assert summary["events"]["agent_batches"]["instruct / browse_round"]["execution_options"][
        "required_action_tags"
    ] == ["social_write"]
    assert len(posts["post_1"]["replies"]) == 3


@pytest.mark.asyncio
async def test_e2e_social_browse_records_recoverable_action_failure(tmp_path, monkeypatch):
    llm_calls = []

    class FakeLLMManager:
        async def request(self, payload):
            llm_calls.append(payload)
            if len(llm_calls) == 1:
                return {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "comment_bad",
                            "type": "function",
                            "function": {
                                "name": "comment",
                                "arguments": json.dumps(
                                    {
                                        "post_id": "user_0",
                                        "content": "I used the author id by mistake.",
                                    }
                                ),
                            },
                        }
                    ],
                }
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "comment_good",
                        "type": "function",
                        "function": {
                            "name": "comment",
                            "arguments": json.dumps(
                                {
                                    "post_id": "post_1",
                                    "content": "Now I am using the real post id.",
                                }
                            ),
                        },
                    }
                ],
            }

        async def close(self):
            return None

    monkeypatch.setattr(LLMModel, "build_manager", lambda self, *, log_context=None: FakeLLMManager())
    llm = LLMModel.openai_compatible(
        id="default",
        model="fake-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="test-key",
        concurrency=1,
    )
    config = {
        "agent_types": [{"id": "social_user", "archetype": "llm"}],
        "agents": [
            {
                "id": "user_0",
                "type": "social_user",
                "persona": "A concise social media user.",
                "state": {},
            }
        ],
        "environment": {
            "type": "social_network",
            "config": {"social_media": {"recommendation": {"use_embedding_similarity": False}}},
            "state": {
                "posts": {
                    "post_1": {
                        "post_id": "post_1",
                        "author_id": "author_1",
                        "content": "Visible seed post.",
                        "tags": [],
                        "created_tick": 0,
                        "likes": [],
                        "like_events": [],
                        "replies": [],
                        "view_count": 0,
                    }
                }
            },
        },
    }
    engine = Society0(save_dir=str(tmp_path), base_config=config, llm=llm, embed=_fake_embed_model())

    @engine.step(name="browse_round")
    async def browse_round(ctx):
        result = await ctx.agents.all().instruct(
            "Comment on the visible post. If an action result says the id is invalid, correct it.",
            actions=["comment"],
            retrieve_memory=False,
            max_turns=3,
            completion_action_tags=["social_write"],
            required_action_tags=["social_write"],
            name="browse_round",
        )
        return ctx.result(tables={"browse": result.table(), "browse_actions": result.actions()})

    await engine.run(steps=1)

    steps = _jsonl(tmp_path / "steps.jsonl")
    browse_actions = steps[0]["result"]["tables"]["browse_actions"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    diagnostics = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    final_checkpoint = read_gzip_json(
        tmp_path / "checkpoints" / "checkpoint_final.json.gz"
    )

    assert len(llm_calls) == 2
    assert [action["status"] for action in browse_actions] == ["error", "success"]
    assert browse_actions[0]["error"] == "Post user_0 not found"
    assert summary["agent_operations"]["browse_round"]["action_error_count"] == 1
    assert summary["agent_operations"]["browse_round"]["turns_max"] == 2
    batch = summary["events"]["agent_batches"]["instruct / browse_round"]
    assert "error_samples" not in batch
    assert batch["failed_action_counts"] == {"comment": 1}
    assert batch["successful_action_counts"] == {"comment": 1}
    assert batch["action_error_samples"] == [
        {
            "agent_id": "user_0",
            "action_name": "comment",
            "status": "error",
            "error": "Post user_0 not found",
            "arguments": {"content": "I used the author id by mistake.", "post_id": "user_0"},
        }
    ]
    assert "Action error samples: 1; inspect tool arguments before weakening actions." in diagnostics
    assert "Sample: agent_id=user_0, action_name=comment, status=error; error=Post user_0 not found." in diagnostics
    assert "Arguments: content=I used the author id by mistake., post_id=user_0." in diagnostics
    assert len(final_checkpoint["environment_data"]["state"]["posts"]["post_1"]["replies"]) == 1


@pytest.mark.asyncio
async def test_e2e_instruct_rejects_fov_name_in_actions_before_llm_call(tmp_path, monkeypatch):
    llm_calls = []

    class FakeLLMManager:
        async def request(self, payload):
            llm_calls.append(payload)
            return {"role": "assistant", "content": "should not be called"}

        async def close(self):
            return None

    monkeypatch.setattr(LLMModel, "build_manager", lambda self, *, log_context=None: FakeLLMManager())
    llm = LLMModel.openai_compatible(
        id="default",
        model="fake-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="test-key",
        concurrency=1,
    )
    config = {
        "agent_types": [{"id": "social_user", "archetype": "llm"}],
        "agents": [
            {
                "id": "viewer",
                "type": "social_user",
                "persona": "A concise social media user.",
                "state": {},
            }
        ],
        "environment": {
            "type": "social_network",
            "config": {"social_media": {"recommendation": {"use_embedding_similarity": False}}},
            "state": {},
        },
    }
    engine = Society0(save_dir=str(tmp_path), base_config=config, llm=llm, embed=_fake_embed_model())

    @engine.step(name="bad_action_filter")
    async def bad_action_filter(ctx):
        result = await ctx.agents.all().instruct(
            "Try to browse the feed.",
            actions=["recommended_feed"],
            retrieve_memory=False,
            max_turns=1,
            name="bad_action_filter",
        )
        return ctx.result(
            metrics={"errors": result.error_count, "success": result.success_count},
            tables={"result": result.table()},
        )

    await engine.run(steps=1)

    assert llm_calls == []
    steps = _jsonl(tmp_path / "steps.jsonl")
    row = steps[0]["result"]["tables"]["result"][0]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    batch = summary["events"]["agent_batches"]["instruct / bad_action_filter"]

    assert row["status"] == "error"
    assert "Action filter ['recommended_feed'] matched no available actions" in row["error"]
    assert "'recommended_feed' is a FoV, not an action" in row["error"]
    assert "Use fovs=['recommended_feed'] for context" in row["error"]
    assert batch["error_samples"][0]["error"] == row["error"]
    assert batch["agent_count"] == 1
    assert batch["success_count_total"] == 0
    assert batch["error_count_total"] == 1


@pytest.mark.parametrize(
    "actions,required_actions,required_action_tags,expected_error",
    [
        (
            ["comment"],
            ["publish_post"],
            None,
            "Required action(s) 'publish_post' are not available after applying actions=['comment'].",
        ),
        (
            ["get_trending_posts"],
            None,
            ["social_write"],
            "Required action tag(s) 'social_write' are not available after applying actions=['get_trending_posts'].",
        ),
    ],
)
@pytest.mark.asyncio
async def test_e2e_instruct_rejects_unsatisfiable_required_actions_before_llm_call(
    tmp_path,
    monkeypatch,
    actions,
    required_actions,
    required_action_tags,
    expected_error,
):
    llm_calls = []

    class FakeLLMManager:
        async def request(self, payload):
            llm_calls.append(payload)
            return {"role": "assistant", "content": "should not be called"}

        async def close(self):
            return None

    monkeypatch.setattr(LLMModel, "build_manager", lambda self, *, log_context=None: FakeLLMManager())
    llm = LLMModel.openai_compatible(
        id="default",
        model="fake-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="test-key",
        concurrency=1,
    )
    config = {
        "agent_types": [{"id": "social_user", "archetype": "llm"}],
        "agents": [
            {
                "id": "viewer",
                "type": "social_user",
                "persona": "A concise social media user.",
                "state": {},
            }
        ],
        "environment": {
            "type": "social_network",
            "config": {"social_media": {"recommendation": {"use_embedding_similarity": False}}},
            "state": {},
        },
    }
    engine = Society0(save_dir=str(tmp_path), base_config=config, llm=llm, embed=_fake_embed_model())

    @engine.step(name="unsatisfiable_required_action")
    async def unsatisfiable_required_action(ctx):
        result = await ctx.agents.all().instruct(
            "Try to complete a required social action.",
            actions=actions,
            retrieve_memory=False,
            max_turns=1,
            required_actions=required_actions,
            required_action_tags=required_action_tags,
            name="unsatisfiable_required_action",
        )
        return ctx.result(tables={"result": result.table()})

    await engine.run(steps=1)

    assert llm_calls == []
    row = _jsonl(tmp_path / "steps.jsonl")[0]["result"]["tables"]["result"][0]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    batch = summary["events"]["agent_batches"]["instruct / unsatisfiable_required_action"]

    assert row["status"] == "error"
    assert expected_error in row["error"]
    assert "Align required_actions/required_action_tags with the actions exposed to the LLM tool loop." in row["error"]
    assert batch["error_samples"][0]["error"] == row["error"]
    assert batch["success_count_total"] == 0
    assert batch["error_count_total"] == 1


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
    assert (run_dir / "checkpoints" / "checkpoint_final.json.gz").is_file()
