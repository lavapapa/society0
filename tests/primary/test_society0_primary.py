import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from society0 import EmbedModel, LLMModel, Society0
from society0.agent.core import LLMAgent, _parse_structured_json_from_model_text
from society0.agent.agent_loop import ActionSet, execute_action_loop
from society0.agent.memory import Memory
from society0.core_data import World
from society0.function_registry import FunctionRegistry
from society0.logging import ExperimentLogContext
from society0.models import LLMModel as PublicLLMModel
from society0.resource_managers import EmbeddingManager, LLMManager
from society0.context_stack import ContextStack
from society0.events import StateChangeEvent
from society0.schedule import AgentBatchResult, AgentCallRecord, AgentSelector, StepResult
from society0.transaction import EventLogger

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


def _social_network_recommendation_config():
    return {
        "agent_types": [{"id": "social_user", "archetype": "rule"}],
        "agents": [
            {"id": "viewer", "type": "social_user", "state": {}},
            {"id": "author_old", "type": "social_user", "state": {}},
            {"id": "author_recent", "type": "social_user", "state": {}},
            {"id": "reposter", "type": "social_user", "state": {}},
        ],
        "environment": {
            "type": "social_network",
            "config": {
                "social_media": {
                    "recommendation": {
                        "post_count": 8,
                        "candidate_count": 20,
                        "use_embedding_similarity": False,
                    }
                }
            },
            "state": {},
        },
    }


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_event_logger_compacts_large_state_change_values_but_not_listener_batch(tmp_path):
    events_path = tmp_path / "events.jsonl"
    captured_batches = []

    class Listener:
        def handle(self, batch):
            captured_batches.append(batch)

    event_logger = EventLogger(str(events_path), listeners=[Listener()])
    large_value = {
        "post_id": "post_1",
        "content": "full content should stay out of monitoring events " * 30,
        "likes": [f"user_{idx}" for idx in range(20)],
    }
    event = StateChangeEvent(
        target_type="environment",
        target_id="global",
        path=["posts", "post_1"],
        operation="set",
        value=large_value,
        old_value={"previous": "value"},
    )

    event_logger.write_event(event)
    event_logger.close()

    records = _read_jsonl(events_path)
    assert len(records) == 1
    record = records[0]
    assert "value" not in record
    assert record["value_omitted"] is True
    assert record["value_summary"]["type"] == "dict"
    assert record["value_summary"]["length"] == 3
    assert "content" in record["value_summary"]["keys_sample"]
    assert "full content should stay out" not in json.dumps(record, ensure_ascii=False)
    assert "old_value" not in record
    assert record["old_value_summary"]["type"] == "dict"
    assert captured_batches[0].events[0].value is large_value


def test_event_logger_keeps_small_scalar_state_change_values(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))
    event_logger.write_event(
        StateChangeEvent(
            target_type="agent",
            target_id="alice",
            path=["state", "trust"],
            operation="set",
            value=0.75,
            old_value=0.5,
        )
    )
    event_logger.close()

    [record] = _read_jsonl(events_path)
    assert record["value"] == 0.75
    assert record["old_value"] == 0.5
    assert "value_summary" not in record


def test_event_logger_omits_empty_context_stack_and_null_old_value(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))
    event_logger.write_event(
        StateChangeEvent(
            target_type="environment",
            target_id="state",
            path=["post_counter"],
            operation="set",
            value=1,
            old_value=None,
        )
    )
    event_logger.close()

    [record] = _read_jsonl(events_path)
    assert record["value"] == 1
    assert "old_value" not in record
    assert "context_stack" not in record
    assert "context" not in record


def test_event_logger_compacts_context_stack_in_main_log_only(tmp_path):
    events_path = tmp_path / "events.jsonl"
    captured_batches = []

    class Listener:
        def handle(self, batch):
            captured_batches.append(batch)

    event_logger = EventLogger(str(events_path), listeners=[Listener()])
    context_stack = ContextStack().push_step("step_3").push_node("node_a").to_list()
    event_logger.write_event(
        StateChangeEvent(
            target_type="agent",
            target_id="alice",
            path=["state", "trust"],
            operation="set",
            value=1,
            context_stack=context_stack,
        )
    )
    event_logger.close()

    [record] = _read_jsonl(events_path)
    assert "context_stack" not in record
    assert record["context"] == {
        "step_id": "step_3",
        "node_id": "node_a",
        "operator_id": None,
        "depth": 2,
    }
    assert captured_batches[0].events[0].context_stack == context_stack


def test_event_logger_can_suppress_state_changes_in_main_log_but_notify_listeners(tmp_path):
    events_path = tmp_path / "events.jsonl"
    captured_batches = []

    class Listener:
        def handle(self, batch):
            captured_batches.append(batch)

    event_logger = EventLogger(str(events_path), listeners=[Listener()], write_state_changes=False)
    event = StateChangeEvent(
        target_type="environment",
        target_id="state",
        path=["posts", "post_1"],
        operation="set",
        value={"content": "still available to listeners"},
    )

    event_logger.write_event(event)
    event_logger.close()

    assert events_path.read_text(encoding="utf-8") == ""
    assert len(captured_batches) == 1
    assert captured_batches[0].events == (event,)
    assert captured_batches[0].event_offsets == (-1,)


def test_llm_agent_action_trace_compacts_arguments(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "participant",
                "archetype": "llm",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }

        def __init__(self):
            self.event_logger = event_logger

        def get_context_stack(self):
            return (
                ContextStack()
                .push_step("step_0")
                .push_operator(
                    "stress_publish",
                    interaction_type="instruct",
                    step_name="publish_slice",
                )
            )

    agent = LLMAgent("alice", FakeWorld())
    full_content = "this long generated post body should not be fully duplicated in events " * 8
    agent._record_action_trace(
        "publish_post",
        {"content": full_content, "tags": ["campus", "daily"]},
        "Successfully published post post_1",
        "success",
    )
    event_logger.close()

    [record] = _read_jsonl(events_path)
    event_data = record["event_data"]
    assert event_data["action"] == "publish_post"
    assert event_data["step_id"] == "step_0"
    assert event_data["step_name"] == "publish_slice"
    assert event_data["interaction_name"] == "stress_publish"
    assert event_data["interaction_type"] == "instruct"
    assert event_data["arguments"]["content"].endswith("...")
    assert event_data["arguments"]["content_length"] == len(full_content.strip())
    assert event_data["arguments"]["content_truncated"] is True
    assert event_data["arguments"]["tags"] == ["campus", "daily"]
    assert full_content not in json.dumps(record, ensure_ascii=False)


class TrustOutput(BaseModel):
    trust_score: float


def test_public_api_imports():
    assert Society0 is not None
    assert LLMModel.openai_compatible(id="llm", model="m", base_url="http://localhost/v1", api_key="x")
    assert EmbedModel.ollama(id="embed", model="nomic-embed-text")
    assert PublicLLMModel is LLMModel


@pytest.mark.asyncio
async def test_society0_llm_agents_require_embedding_model(tmp_path):
    config = _base_config()
    config["agent_types"] = [{"id": "social_user", "archetype": "llm"}]
    config["agents"] = [{"id": "alice", "type": "social_user", "state": {}}]
    llm = LLMModel.openai_compatible(
        id="default",
        model="fake-model",
        base_url="http://127.0.0.1:9/v1",
        api_key="test-key",
    )
    engine = Society0(save_dir=str(tmp_path), base_config=config, llm=llm)

    @engine.step(name="noop")
    async def noop(ctx):
        return ctx.result()

    with pytest.raises(ValueError, match="embed=EmbedModel"):
        await engine.run(steps=1)


def test_world_strict_cognitive_initialization_requires_memory():
    async def fake_llm_call(payload):
        return {"role": "assistant", "content": "ok"}

    world = World()
    world.add_agent_data(agent_id="alice", agent_type="social_user", archetype="llm")

    with pytest.raises(RuntimeError, match="Failed to initialize LLM agent 'alice'"):
        world.initialize_all_cognitive_systems(
            llm_call=fake_llm_call,
            strict=True,
            require_memory=True,
        )


def test_agent_batch_result_exposes_action_summaries():
    long_result = "热门动态 " + ("very long post body " * 40)
    result = AgentBatchResult(
        [
            AgentCallRecord(
                agent_id="alice",
                status="success",
                value={
                    "actions": [
                        {
                            "type": "action_call",
                            "action_name": "get_trending_posts",
                            "arguments": {},
                            "result": "hot posts",
                            "status": "success",
                            "tags": ["get_trending_posts", "social_read"],
                        },
                        {
                            "type": "action_call",
                            "action_name": "get_agent_profile",
                            "arguments": {"agent_id": "bob"},
                            "result": "profile",
                            "status": "success",
                            "tags": ["get_agent_profile", "profile_read"],
                        },
                        {
                            "type": "action_call",
                            "action_name": "get_trending_posts",
                            "arguments": {"query": "campus", "note": "n" * 300},
                            "result": long_result,
                            "status": "success",
                            "tags": ["get_trending_posts", "social_read"],
                        },
                        {
                            "type": "action_call",
                            "action_name": "comment",
                            "arguments": {"post_id": "missing"},
                            "result": "Post not found",
                            "status": "error",
                            "tags": ["comment", "social_write"],
                        },
                    ],
                    "memory_retrieved": True,
                    "memory_top_k": 7,
                    "memory_saved": True,
                    "memory_extraction_enabled": True,
                    "memory_extraction_success": True,
                    "extracted_memories": [{"content": "Alice learned that the post was missing."}],
                    "termination_reason": "completion_action_tag",
                },
            ),
            AgentCallRecord(
                agent_id="bob",
                status="success",
                raw={
                    "actions": [
                        {
                            "type": "action_call",
                            "action_name": "get_trending_posts",
                            "arguments": {},
                            "result": "hot posts",
                            "status": "success",
                            "tags": ["get_trending_posts", "social_read"],
                        }
                    ],
                    "memory_retrieved": True,
                    "memory_top_k": 5,
                    "memory_saved": False,
                    "memory_extraction_enabled": False,
                    "memory_extraction_success": False,
                    "extracted_memories": [],
                    "termination_reason": "no_action_calls",
                },
            ),
            AgentCallRecord(
                agent_id="carol",
                status="error",
                value={"reason": "required action missing"},
                error="Missing required actions for carol: publish_post",
            ),
        ]
    )

    assert result.action_counts() == {"get_trending_posts": 3, "get_agent_profile": 1, "comment": 1}
    assert result.successful_action_counts() == {"get_trending_posts": 3, "get_agent_profile": 1}
    assert result.failed_action_counts() == {"comment": 1}
    assert result.action_error_samples() == [
        {
            "agent_id": "alice",
            "action_name": "comment",
            "status": "error",
            "error": "Post not found",
            "arguments": {"post_id": "missing"},
        }
    ]
    assert result.action_tag_counts() == {
        "get_trending_posts": 3,
        "social_read": 3,
        "get_agent_profile": 1,
        "profile_read": 1,
    }
    assert result.termination_reason_counts() == {
        "completion_action_tag": 1,
        "no_action_calls": 1,
    }
    assert result.memory_summary() == {
        "record_count": 2,
        "retrieve_enabled_count": 2,
        "save_enabled_count": 1,
        "extraction_enabled_count": 1,
        "extraction_success_count": 1,
        "extraction_error_count": 0,
        "extracted_memory_count": 1,
        "top_k_values": [5, 7],
    }
    assert [action["action_name"] for action in result.actions_by_agent("alice")] == [
        "get_trending_posts",
        "get_agent_profile",
        "get_trending_posts",
        "comment",
    ]
    assert result.actions_by_agent("missing") == []
    compact_long = result.actions_by_agent("alice")[-2]
    assert compact_long["result"].endswith("...")
    assert compact_long["result_length"] == len(long_result)
    assert compact_long["result_truncated"] is True
    assert compact_long["arguments"]["note"].endswith("...")
    assert compact_long["arguments"]["note_length"] == 300
    assert compact_long["arguments"]["note_truncated"] is True
    assert long_result not in json.dumps(result.actions(), ensure_ascii=False)
    assert result.error_samples() == [
        {
            "agent_id": "carol",
            "status": "error",
            "error": "Missing required actions for carol: publish_post",
        }
    ]
    assert result.to_dict()["action_counts"] == result.action_counts()
    assert result.to_dict()["successful_action_counts"] == result.successful_action_counts()
    assert result.to_dict()["failed_action_counts"] == result.failed_action_counts()
    assert result.to_dict()["action_error_samples"] == result.action_error_samples()
    assert result.to_dict()["action_tag_counts"] == result.action_tag_counts()
    assert result.to_dict()["termination_reason_counts"] == result.termination_reason_counts()
    assert result.to_dict()["memory_summary"] == result.memory_summary()
    assert result.to_dict()["error_samples"] == result.error_samples()


def test_society0_summary_aggregates_agent_operations_from_steps(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())
    (tmp_path / "steps.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "step": 0,
                        "step_name": "browse_round",
                        "duration_sec": 12.5,
                        "result": {
                            "tables": {
                                "browse": [
                                    {
                                        "agent_id": "alice",
                                        "status": "success",
                                        "total_turns": 2,
                                        "memory_retrieved": True,
                                        "memory_top_k": 3,
                                        "memory_saved": True,
                                        "memory_extraction_enabled": True,
                                        "memory_extraction_success": True,
                                        "extracted_memories": [{"content": "Alice commented."}],
                                    },
                                    {
                                        "agent_id": "bob",
                                        "status": "error",
                                        "total_turns": 3,
                                        "error": "bad action",
                                        "memory_retrieved": True,
                                        "memory_top_k": 3,
                                        "memory_saved": True,
                                        "memory_extraction_enabled": True,
                                        "memory_extraction_success": False,
                                        "memory_extraction_error": "bad action",
                                        "extracted_memories": [],
                                    },
                                ],
                                "browse_actions": [
                                    {
                                        "agent_id": "alice",
                                        "status": "success",
                                        "action_name": "get_trending_posts",
                                        "tags": ["get_trending_posts", "social_read"],
                                    },
                                    {
                                        "agent_id": "alice",
                                        "status": "success",
                                        "action_name": "comment",
                                        "tags": ["comment", "social_write"],
                                    },
                                    {
                                        "agent_id": "bob",
                                        "status": "error",
                                        "action_name": "comment",
                                        "tags": ["comment", "social_write"],
                                        "error": "bad action",
                                    },
                                ],
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "step": 0,
                        "step_name": "measure",
                        "duration_sec": 1.0,
                        "result": {
                            "tables": {
                                "survey": [
                                    {
                                        "agent_id": "alice",
                                        "status": "success",
                                        "trust": 3,
                                        "total_turns": 2,
                                    }
                                ]
                            }
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "resource_calls.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "resource_type": "llm",
                        "status": "success",
                        "step": 0,
                        "step_name": "browse_round",
                        "interaction_type": "instruct",
                        "interaction_name": "feed",
                        "agent_id": "alice",
                        "duration_sec": 4.0,
                        "provider_duration_sec": 3.5,
                        "queue_duration_sec": 0.2,
                        "input_characters": 1200,
                        "messages_count": 4,
                        "prompt_tokens": 800,
                        "completion_tokens": 120,
                        "total_tokens": 920,
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "embedding",
                        "status": "success",
                        "step": 0,
                        "step_names": ["browse_round"],
                        "interaction_types": ["semantic_recommendation"],
                        "interaction_names": ["recommended_feed"],
                        "agent_ids": ["alice", "bob"],
                        "duration_sec": 0.3,
                        "provider_duration_sec": 0.25,
                        "queue_duration_sec": 0.01,
                        "input_characters": 350,
                        "texts_count": 2,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = engine._summarize_agent_operations()

    assert summary["browse_round"]["agent_count"] == 2
    assert summary["browse_round"]["unique_agent_count"] == 2
    assert summary["browse_round"]["success_count"] == 1
    assert summary["browse_round"]["error_count"] == 1
    assert summary["browse_round"]["turns_avg"] == 2.5
    assert summary["browse_round"]["turns_max"] == 3
    assert summary["browse_round"]["slowest_agents_by_turns"] == [
        {"agent_id": "bob", "total_turns": 3, "status": "error", "error": "bad action"},
        {"agent_id": "alice", "total_turns": 2, "status": "success"},
    ]
    assert summary["browse_round"]["action_counts"] == {"get_trending_posts": 1, "comment": 2}
    assert summary["browse_round"]["successful_action_counts"] == {"comment": 1, "get_trending_posts": 1}
    assert summary["browse_round"]["failed_action_counts"] == {"comment": 1}
    assert summary["browse_round"]["action_tag_counts"] == {
        "comment": 1,
        "get_trending_posts": 1,
        "social_read": 1,
        "social_write": 1,
    }
    assert summary["browse_round"]["action_error_count"] == 1
    assert summary["browse_round"]["memory_summary"] == {
        "record_count": 2,
        "retrieve_enabled_count": 2,
        "save_enabled_count": 2,
        "extraction_enabled_count": 2,
        "extraction_success_count": 1,
        "extraction_error_count": 1,
        "extracted_memory_count": 1,
        "top_k_values": [3],
        "error_samples": [{"agent_id": "bob", "error": "bad action"}],
    }
    assert summary["browse_round"]["error_samples"][0]["agent_id"] == "bob"
    assert summary["browse_round"]["by_tick"]["0"]["agent_count"] == 2
    assert summary["browse_round"]["by_tick"]["0"]["success_count"] == 1
    assert summary["browse_round"]["by_tick"]["0"]["error_count"] == 1
    assert summary["browse_round"]["by_tick"]["0"]["action_counts"] == {
        "comment": 2,
        "get_trending_posts": 1,
    }
    assert summary["browse_round"]["by_tick"]["0"]["successful_action_counts"] == {
        "comment": 1,
        "get_trending_posts": 1,
    }
    assert summary["browse_round"]["by_tick"]["0"]["failed_action_counts"] == {"comment": 1}
    assert summary["browse_round"]["by_tick"]["0"]["memory_summary"] == summary["browse_round"]["memory_summary"]
    assert summary["browse_round"]["by_tick"]["0"]["action_tag_counts"] == {
        "comment": 1,
        "get_trending_posts": 1,
        "social_read": 1,
        "social_write": 1,
    }
    assert summary["browse_round"]["by_tick"]["0"]["turns_avg"] == 2.5
    assert summary["browse_round"]["resources"]["llm"]["call_count"] == 1
    assert summary["browse_round"]["resources"]["llm"]["total_tokens"] == 920
    assert summary["browse_round"]["resources"]["llm"]["input_characters"] == 1200
    assert summary["browse_round"]["resources"]["llm"]["messages_count_max"] == 4
    assert summary["browse_round"]["resources"]["llm"]["duration_sec_total"] == 4.0
    assert summary["browse_round"]["resources"]["llm"]["total_duration_sec"] == 4.0
    assert summary["browse_round"]["resources"]["llm"]["by_interaction_type"]["instruct"]["call_count"] == 1
    assert summary["browse_round"]["resources"]["llm"]["fidelity"]["agent_loop"]["call_count"] == 1
    assert summary["browse_round"]["resources"]["embedding"]["call_count"] == 1
    assert summary["browse_round"]["resources"]["embedding"]["texts_count"] == 2
    assert (
        summary["browse_round"]["resources"]["embedding"]["by_interaction_type"]["semantic_recommendation"][
            "call_count"
        ]
        == 1
    )
    assert summary["browse_round"]["resources"]["embedding"]["fidelity"]["environment"]["call_count"] == 1
    assert summary["browse_round"]["by_tick"]["0"]["resources"]["llm"]["call_count"] == 1
    assert (
        summary["browse_round"]["by_tick"]["0"]["resources"]["llm"]["by_interaction_type"]["instruct"][
            "call_count"
        ]
        == 1
    )
    assert summary["browse_round"]["by_tick"]["0"]["resources"]["embedding"]["input_characters"] == 350
    assert summary["measure"]["agent_count"] == 1
    assert summary["measure"]["unique_agent_count"] == 1
    assert summary["measure"]["turns_avg"] == 2.0
    assert summary["measure"]["turns_max"] == 2


def test_society0_summary_counts_nested_agent_actions_without_double_counting(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())
    nested_action = {
        "agent_id": "alice",
        "status": "success",
        "action_name": "publish_post",
        "call_id": "call_publish_1",
        "arguments": {"content": "hello"},
    }
    (tmp_path / "steps.jsonl").write_text(
        json.dumps(
            {
                "step": 0,
                "step_name": "publish_once",
                "duration_sec": 3.0,
                "result": {
                    "tables": {
                        "published": [
                            {
                                "agent_id": "alice",
                                "status": "success",
                                "total_turns": 1,
                                "actions": [nested_action],
                            }
                        ],
                        # Users may also store the normalized action table for
                        # analysis. Summary should dedupe the same call_id.
                        "published_actions": [nested_action],
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    summary = engine._summarize_agent_operations()["publish_once"]

    assert summary["agent_count"] == 1
    assert summary["unique_agent_count"] == 1
    assert summary["success_count"] == 1
    assert summary["action_counts"] == {"publish_post": 1}
    assert summary["successful_action_counts"] == {"publish_post": 1}
    assert summary["failed_action_counts"] == {}
    assert summary["action_error_count"] == 0


def test_society0_summary_counts_repeated_agent_operations_across_ticks(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())
    (tmp_path / "steps.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "step": 0,
                        "step_name": "browse_round",
                        "duration_sec": 1.0,
                        "result": {
                            "tables": {
                                "browse": [
                                    {"agent_id": "alice", "status": "success", "total_turns": 1},
                                    {"agent_id": "bob", "status": "success", "total_turns": 1},
                                ],
                                "browse_actions": [
                                    {"agent_id": "alice", "status": "success", "action_name": "like_post"},
                                    {"agent_id": "bob", "status": "success", "action_name": "comment"},
                                ],
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "step": 1,
                        "step_name": "browse_round",
                        "duration_sec": 1.0,
                        "result": {
                            "tables": {
                                "browse": [
                                    {"agent_id": "alice", "status": "success", "total_turns": 2},
                                    {"agent_id": "bob", "status": "error", "total_turns": 3, "error": "bad id"},
                                ],
                                "browse_actions": [
                                    {"agent_id": "alice", "status": "success", "action_name": "repost"},
                                    {"agent_id": "bob", "status": "error", "action_name": "like_post"},
                                ],
                            }
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = engine._summarize_agent_operations()["browse_round"]

    assert summary["agent_count"] == 4
    assert summary["unique_agent_count"] == 2
    assert summary["success_count"] == 3
    assert summary["error_count"] == 1
    assert summary["turns_avg"] == 1.75
    assert summary["turns_max"] == 3
    assert summary["action_counts"] == {"comment": 1, "like_post": 2, "repost": 1}
    assert summary["successful_action_counts"] == {"comment": 1, "like_post": 1, "repost": 1}
    assert summary["failed_action_counts"] == {"like_post": 1}
    assert summary["action_error_count"] == 1
    assert summary["by_tick"]["0"]["agent_count"] == 2
    assert summary["by_tick"]["0"]["action_counts"] == {"comment": 1, "like_post": 1}
    assert summary["by_tick"]["0"]["successful_action_counts"] == {"comment": 1, "like_post": 1}
    assert summary["by_tick"]["0"]["failed_action_counts"] == {}
    assert summary["by_tick"]["1"]["agent_count"] == 2
    assert summary["by_tick"]["1"]["error_count"] == 1
    assert summary["by_tick"]["1"]["action_counts"] == {"like_post": 1, "repost": 1}
    assert summary["by_tick"]["1"]["successful_action_counts"] == {"repost": 1}
    assert summary["by_tick"]["1"]["failed_action_counts"] == {"like_post": 1}


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
    events = _read_jsonl(tmp_path / "events.jsonl")
    assert len(steps) == 3
    assert metrics[0]["metrics"] == {"avg_trust": 0.6000000000000001}
    assert [event["event"] for event in events if event.get("event", "").startswith("code_step_")] == [
        "code_step_started",
        "code_step_completed",
        "code_step_started",
        "code_step_completed",
        "code_step_started",
        "code_step_completed",
    ]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["final_step"] == 3
    assert summary["total_time"] >= 0
    assert summary["total_execution_time"] == summary["total_time"]
    assert summary["agent_operations"] == {}
    assert summary["outputs"]["files"]["events.jsonl"]["line_count"] >= 1
    assert summary["outputs"]["files"]["steps.jsonl"]["line_count"] == 3
    assert summary["outputs"]["checkpoints"]["count"] == 2
    assert "env_hooks" not in summary["events"]
    assert summary["outputs"]["checkpoints"]["files"]["checkpoint_final.json"]["bytes"] > 0
    checkpoints = sorted(path.name for path in (tmp_path / "checkpoints").glob("checkpoint*.json"))
    assert checkpoints == ["checkpoint_000000.json", "checkpoint_final.json"]

    checkpoint_text = (tmp_path / "checkpoints" / "checkpoint_final.json").read_text(encoding="utf-8")
    checkpoint_payload = json.loads(checkpoint_text)
    pretty_checkpoint = json.dumps(checkpoint_payload, ensure_ascii=False, indent=2, default=str) + "\n"
    assert checkpoint_payload["step"] == 3
    assert checkpoint_text.count("\n") == 1
    assert len(checkpoint_text) < len(pretty_checkpoint)


def test_event_summary_preserves_agent_batch_fidelity_options(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())
    (tmp_path / "events.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "agent_batch_started",
                        "event_data": {
                            "step": 0,
                            "step_name": "shared_step",
                            "interaction_type": "instruct",
                            "interaction_name": "shared",
                            "agent_count": 2,
                            "concurrency": 2,
                            "model_id": "default",
                            "fovs": ["recommended_feed"],
                            "actions": ["publish_post"],
                            "execution_options": {
                                "max_turns": 4,
                                "memory": {"retrieve": True, "save": True, "extract": True, "top_k": 7},
                                "completion_action_tags": ["social_write"],
                                "required_actions": ["publish_post"],
                                "required_action_tags": ["social_write"],
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_type": "agent_batch_heartbeat",
                        "event_data": {
                            "step": 0,
                            "step_name": "shared_step",
                            "interaction_type": "instruct",
                            "interaction_name": "shared",
                            "agent_count": 2,
                            "concurrency": 2,
                            "success_count": 1,
                            "error_count": 0,
                            "completed_count": 1,
                            "started_count": 2,
                            "in_flight_count": 1,
                            "pending_count": 0,
                            "duration_sec": 0.75,
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_type": "agent_batch_progress",
                        "event_data": {
                            "step": 0,
                            "step_name": "shared_step",
                            "interaction_type": "instruct",
                            "interaction_name": "shared",
                            "agent_count": 2,
                            "concurrency": 2,
                            "success_count": 2,
                            "error_count": 0,
                            "completed_count": 2,
                            "duration_sec": 1.2,
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_type": "agent_batch_completed",
                        "event_data": {
                            "step": 0,
                            "step_name": "shared_step",
                            "interaction_type": "instruct",
                            "interaction_name": "shared",
                            "agent_count": 2,
                            "concurrency": 2,
                            "success_count": 2,
                            "error_count": 0,
                            "completed_count": 2,
                            "duration_sec": 1.5,
                            "action_counts": {"publish_post": 2},
                            "successful_action_counts": {"publish_post": 2},
                            "failed_action_counts": {},
                            "action_tag_counts": {"publish_post": 2, "social_write": 2},
                            "termination_reason_counts": {"terminal_action": 2},
                            "execution_options": {
                                "max_turns": 4,
                                "memory": {"retrieve": True, "save": True, "extract": True, "top_k": 7},
                                "completion_action_tags": ["social_write"],
                                "required_actions": ["publish_post"],
                                "required_action_tags": ["social_write"],
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_type": "agent_batch_started",
                        "event_data": {
                            "step": 1,
                            "step_name": "shared_step",
                            "interaction_type": "instruct",
                            "interaction_name": "shared",
                            "agent_count": 2,
                            "concurrency": 2,
                            "model_id": "default",
                            "fovs": ["recommended_feed"],
                            "actions": ["comment"],
                            "execution_options": {
                                "max_turns": 4,
                                "memory": {"retrieve": True, "save": True, "extract": True, "top_k": 7},
                                "completion_action_tags": ["social_write"],
                                "required_actions": ["comment"],
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_type": "agent_batch_completed",
                        "event_data": {
                            "step": 1,
                            "step_name": "shared_step",
                            "interaction_type": "instruct",
                            "interaction_name": "shared",
                            "agent_count": 2,
                            "concurrency": 2,
                            "success_count": 1,
                            "error_count": 1,
                            "completed_count": 2,
                            "duration_sec": 2.5,
                            "action_counts": {"comment": 2},
                            "successful_action_counts": {"comment": 1},
                            "failed_action_counts": {"comment": 1},
                            "action_tag_counts": {"comment": 1, "social_write": 1},
                            "termination_reason_counts": {"completion_action_tag": 1},
                            "execution_options": {
                                "max_turns": 4,
                                "memory": {"retrieve": True, "save": True, "extract": True, "top_k": 7},
                                "completion_action_tags": ["social_write"],
                                "required_actions": ["comment"],
                            },
                        },
                    }
                ),
                json.dumps(
                    {
                        "event_type": "agent_batch_started",
                        "event_data": {
                            "step": 0,
                            "step_name": "shared_step",
                            "interaction_type": "interview",
                            "interaction_name": "shared",
                            "agent_count": 2,
                            "concurrency": 1,
                            "fovs": ["recent_posts"],
                            "actions": [],
                            "execution_options": {
                                "max_turns": 2,
                                "memory": {"retrieve": True, "save": False, "extract": False, "top_k": 5},
                            },
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "resource_calls.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "resource_type": "llm",
                        "status": "success",
                        "step": 0,
                        "step_name": "shared_step",
                        "interaction_type": "instruct",
                        "interaction_name": "shared",
                        "agent_id": "alice",
                        "duration_sec": 1.1,
                        "provider_duration_sec": 0.9,
                        "queue_duration_sec": 0.1,
                        "input_characters": 1000,
                        "tools_characters": 300,
                        "payload_characters": 1500,
                        "messages_count": 2,
                        "tools_count": 3,
                        "total_tokens": 120,
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "llm",
                        "status": "success",
                        "step": 1,
                        "step_name": "shared_step",
                        "interaction_type": "instruct",
                        "interaction_name": "shared",
                        "agent_id": "bob",
                        "duration_sec": 2.2,
                        "provider_duration_sec": 2.0,
                        "queue_duration_sec": 0.15,
                        "input_characters": 1200,
                        "tools_characters": 300,
                        "payload_characters": 1700,
                        "messages_count": 4,
                        "tools_count": 3,
                        "total_tokens": 180,
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "llm",
                        "status": "success",
                        "step": 1,
                        "step_name": "shared_step",
                        "interaction_type": "memory_extract",
                        "interaction_name": "memory_extract",
                        "agent_id": "bob",
                        "duration_sec": 3.0,
                        "input_characters": 900,
                        "tools_characters": 120,
                        "payload_characters": 1100,
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "embedding",
                        "status": "success",
                        "step": 0,
                        "step_names": ["shared_step"],
                        "interaction_types": ["interview"],
                        "interaction_names": ["shared"],
                        "agent_ids": ["alice", "bob"],
                        "duration_sec": 0.4,
                        "provider_duration_sec": 0.3,
                        "queue_duration_sec": 0.05,
                        "input_characters": 600,
                        "texts_count": 2,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = engine._summarize_events()

    assert summary["by_event"]["agent_batch_started"] == 3
    assert summary["by_event"]["agent_batch_completed"] == 2
    assert set(summary["agent_batches"]) == {"instruct / shared", "interview / shared"}
    instruct = summary["agent_batches"]["instruct / shared"]
    interview = summary["agent_batches"]["interview / shared"]
    assert instruct["latest_event"] == "agent_batch_completed"
    assert instruct["interaction_type"] == "instruct"
    assert instruct["step_name"] == "shared_step"
    assert instruct["agent_count"] == 2
    assert instruct["concurrency"] == 2
    assert instruct["model_id"] == "default"
    assert instruct["fovs"] == ["recommended_feed"]
    assert instruct["actions"] == ["publish_post"]
    assert instruct["batch_started_count"] == 2
    assert instruct["batch_completed_count"] == 2
    assert instruct["success_count"] == 1
    assert instruct["success_count_total"] == 3
    assert instruct["error_count_total"] == 1
    assert instruct["completed_count"] == 2
    assert instruct["completed_count_total"] == 4
    assert instruct["action_counts"] == {"comment": 2, "publish_post": 2}
    assert instruct["successful_action_counts"] == {"comment": 1, "publish_post": 2}
    assert instruct["failed_action_counts"] == {"comment": 1}
    assert instruct["action_tag_counts"] == {"comment": 1, "publish_post": 2, "social_write": 3}
    assert instruct["termination_reason_counts"] == {
        "completion_action_tag": 1,
        "terminal_action": 2,
    }
    assert instruct["duration_sec"] == 2.5
    assert instruct["duration_sec_total"] == 4.0
    assert instruct["by_tick"]["0"]["batch_started_count"] == 1
    assert instruct["by_tick"]["0"]["batch_completed_count"] == 1
    assert instruct["by_tick"]["0"]["success_count_total"] == 2
    assert instruct["by_tick"]["0"]["error_count_total"] == 0
    assert instruct["by_tick"]["0"]["completed_count_total"] == 2
    assert instruct["by_tick"]["0"]["duration_sec_total"] == 1.5
    assert instruct["by_tick"]["0"]["action_counts"] == {"publish_post": 2}
    assert instruct["by_tick"]["0"]["action_tag_counts"] == {"publish_post": 2, "social_write": 2}
    assert instruct["by_tick"]["0"]["termination_reason_counts"] == {"terminal_action": 2}
    assert instruct["by_tick"]["1"]["batch_started_count"] == 1
    assert instruct["by_tick"]["1"]["batch_completed_count"] == 1
    assert instruct["by_tick"]["1"]["success_count_total"] == 1
    assert instruct["by_tick"]["1"]["error_count_total"] == 1
    assert instruct["by_tick"]["1"]["completed_count_total"] == 2
    assert instruct["by_tick"]["1"]["duration_sec_total"] == 2.5
    assert instruct["by_tick"]["1"]["action_counts"] == {"comment": 2}
    assert instruct["by_tick"]["1"]["successful_action_counts"] == {"comment": 1}
    assert instruct["by_tick"]["1"]["failed_action_counts"] == {"comment": 1}
    assert instruct["by_tick"]["1"]["action_tag_counts"] == {"comment": 1, "social_write": 1}
    assert instruct["by_tick"]["1"]["termination_reason_counts"] == {"completion_action_tag": 1}
    assert instruct["progress_event_count"] == 1
    assert instruct["heartbeat_event_count"] == 1
    assert instruct["max_in_flight_count"] == 1
    assert instruct["max_pending_count"] == 0
    assert instruct["max_started_count"] == 2
    assert instruct["resources"]["llm"]["call_count"] == 2
    assert instruct["resources"]["llm"]["duration_sec_total"] == 3.3
    assert instruct["resources"]["llm"]["total_input_characters"] == 2200
    assert instruct["resources"]["llm"]["total_tools_characters"] == 600
    assert instruct["resources"]["llm"]["total_payload_characters"] == 3200
    assert instruct["resources"]["llm"]["messages_count_max"] == 4
    assert instruct["resources"]["llm"]["tools_count_max"] == 3
    assert instruct["resources"]["llm"]["fidelity"]["agent_loop"]["call_count"] == 2
    assert "memory_extraction" not in instruct["resources"]["llm"]["fidelity"]
    assert instruct["by_tick"]["0"]["resources"]["llm"]["call_count"] == 1
    assert instruct["by_tick"]["0"]["resources"]["llm"]["total_payload_characters"] == 1500
    assert instruct["by_tick"]["1"]["resources"]["llm"]["call_count"] == 1
    assert instruct["by_tick"]["1"]["resources"]["llm"]["total_duration_sec"] == 2.2
    assert instruct["execution_options"]["memory"]["extract"] is True
    assert instruct["execution_options"]["completion_action_tags"] == ["social_write"]
    assert instruct["action_semantics"] == {
        "completion_action_tags": {
            "configured": ["social_write"],
            "observed_counts": {"social_write": 3},
        },
        "required_action_tags": {
            "configured": ["social_write"],
            "observed_counts": {"social_write": 2},
        },
        "required_actions": {
            "configured": ["publish_post", "comment"],
            "observed_counts": {"comment": 1, "publish_post": 2},
        },
    }
    assert instruct["by_tick"]["0"]["action_semantics"] == {
        "completion_action_tags": {
            "configured": ["social_write"],
            "observed_counts": {"social_write": 2},
        },
        "required_action_tags": {
            "configured": ["social_write"],
            "observed_counts": {"social_write": 2},
        },
        "required_actions": {
            "configured": ["publish_post"],
            "observed_counts": {"publish_post": 2},
        },
    }
    assert instruct["by_tick"]["1"]["action_semantics"] == {
        "completion_action_tags": {
            "configured": ["social_write"],
            "observed_counts": {"social_write": 1},
        },
        "required_actions": {
            "configured": ["comment"],
            "observed_counts": {"comment": 1},
        },
    }
    assert interview["interaction_type"] == "interview"
    assert interview["concurrency"] == 1
    assert interview["fovs"] == ["recent_posts"]
    assert interview["execution_options"]["memory"] == {
        "retrieve": True,
        "save": False,
        "extract": False,
        "top_k": 5,
    }
    assert interview["resources"]["embedding"]["call_count"] == 1
    assert interview["resources"]["embedding"]["texts_count"] == 2
    assert interview["resources"]["embedding"]["by_interaction_type"]["interview"]["call_count"] == 1
    assert interview["resources"]["embedding"]["fidelity"]["other"]["call_count"] == 1


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
async def test_social_network_recommendation_recalls_old_high_engagement_posts(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_network_recommendation_config())
    observed = {}

    @engine.step(name="recommend")
    async def recommend(ctx):
        posts = ctx.env.state["posts"]
        posts["post_old"] = {
            "post_id": "post_old",
            "author_id": "author_old",
            "content": "Older but heavily discussed public claim.",
            "tags": [],
            "created_tick": 0,
            "likes": [],
            "replies": [
                {"reply_id": f"reply_{idx}", "author_id": f"commenter_{idx}", "content": "reply", "created_tick": 1}
                for idx in range(4)
            ],
        }
        posts["post_old_repost"] = {
            "post_id": "post_old_repost",
            "author_id": "reposter",
            "content": "Reposting old claim",
            "tags": [],
            "created_tick": 1,
            "likes": [],
            "replies": [],
            "reply_to": "post_old",
        }
        for idx in range(25):
            posts[f"post_recent_{idx}"] = {
                "post_id": f"post_recent_{idx}",
                "author_id": "author_recent",
                "content": f"Recent low-engagement post {idx}",
                "tags": [],
                "created_tick": 100 + idx,
                "likes": [],
                "replies": [],
            }

        viewer = ctx.world.get_agent("viewer")
        candidates = ctx.env._get_real_posts_only(viewer)
        ranked = await ctx.env._rank_posts_with_similarity(viewer, candidates)
        repost_counts = ctx.env._build_repost_counts(posts)
        observed["candidate_ids"] = [post["post_id"] for post in candidates]
        observed["ranked_ids"] = [post["post_id"] for post in ranked]
        observed["old_engagement_score"] = ctx.env._post_engagement_score(posts["post_old"], repost_counts)
        return ctx.result(metrics={"candidate_count": len(candidates)})

    await engine.run(steps=1)

    assert "post_old" in observed["candidate_ids"]
    assert observed["old_engagement_score"] == 8.0
    assert observed["ranked_ids"][0] == "post_old"
    assert len(observed["ranked_ids"]) == 8


def test_society0_skill_requires_visible_experiment_todo_list():
    skill_text = Path(__file__).resolve().parents[2].joinpath("skill", "SKILL.md").read_text(encoding="utf-8")
    assert "Start and maintain a visible todo list" in skill_text
    assert "Keep the todo list visible" in skill_text


@pytest.mark.asyncio
async def test_instruct_and_interview_wrappers_pass_expected_options():
    class FakeWorld:
        step = 7
        agents_data = {
            "alice": {"id": "alice", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}}
        }

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            self.instruct_call = (agent_id, instruction, kwargs)
            return {"structured_output": {"trust_score": 0.5}, "total_turns": 2, "llm_calls": 2}

        async def interview_agent(self, agent_id, question, **kwargs):
            self.interview_call = (agent_id, question, kwargs)
            return {"structured_output": {"trust_score": 0.75}, "total_turns": 3, "llm_calls": 3}

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
        terminal_actions=["submit_final_decision"],
        completion_action_tags=["social_write"],
        max_action_calls=2,
        action_call_limits={"publish_post": 1},
        required_actions=["publish_post"],
        required_action_tags=["social_write"],
        memory_top_k=3,
        max_tokens=80,
        temperature=0.2,
        top_p=0.9,
        timeout=45,
        llm_options={"max_tokens": 120, "metadata": {"bad": True}},
    )
    assert instruct.mean("trust_score") == 0.5
    assert instruct.table()[0]["total_turns"] == 2
    assert world.instruct_call[2]["fovs"] == ["feed"]
    assert world.instruct_call[2]["action_tags"] == ["social"]
    assert world.instruct_call[2]["retrieve_memory"] is False
    assert world.instruct_call[2]["save_memory"] is False
    assert world.instruct_call[2]["extract_memory"] is False
    assert world.instruct_call[2]["model_id"] == "fast"
    assert world.instruct_call[2]["name"] == "feed_interaction"
    assert world.instruct_call[2]["reasoning_stages"] == [{"name": "think", "desc": "think first"}]
    assert world.instruct_call[2]["terminal_action_names"] == ["submit_final_decision"]
    assert world.instruct_call[2]["completion_action_tags"] == ["social_write"]
    assert world.instruct_call[2]["required_action_names"] == ["publish_post"]
    assert world.instruct_call[2]["required_action_tags"] == ["social_write"]
    assert world.instruct_call[2]["max_action_calls"] == 2
    assert world.instruct_call[2]["action_call_limits"] == {"publish_post": 1}
    assert world.instruct_call[2]["memory_top_k"] == 3
    assert world.instruct_call[2]["llm_request_options"] == {
        "max_tokens": 80,
        "temperature": 0.2,
        "top_p": 0.9,
        "timeout": 45.0,
    }

    interview = await group.interview(
        "rate trust",
        fovs=["recent_posts"],
        output=TrustOutput,
        retrieve_memory=True,
        save_memory=False,
        model="careful",
        name="trust_survey",
        reasoning_stages=[{"name": "answer", "desc": "answer directly"}],
        memory_top_k=2,
        max_tokens=48,
        temperature=0,
        llm_options={"top_p": 0.8, "tools": []},
    )
    assert interview.mean("trust_score") == 0.75
    assert interview.table()[0]["total_turns"] == 3
    assert world.interview_call[2]["fovs"] == ["recent_posts"]
    assert world.interview_call[2]["save_memory"] is False
    assert world.interview_call[2]["name"] == "trust_survey"
    assert world.interview_call[2]["reasoning_stages"] == [{"name": "answer", "desc": "answer directly"}]
    assert world.interview_call[2]["memory_top_k"] == 2
    assert world.interview_call[2]["llm_request_options"] == {
        "max_tokens": 48,
        "temperature": 0.0,
        "top_p": 0.8,
    }
    assert world.interview_call[2]["output_schema"]["type"] == "object"
    assert "trust_score" in world.interview_call[2]["output_schema"]["properties"]
    assert "action_tags" not in world.interview_call[2]


@pytest.mark.asyncio
async def test_agent_group_instruct_defaults_to_extractive_memory_write():
    class FakeWorld:
        step = 1
        agents_data = {
            "alice": {"id": "alice", "type": "participant", "archetype": "llm", "state": {}, "properties": {}}
        }

        def __init__(self):
            self.calls = []

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            self.calls.append(kwargs)
            return {"structured_output": {"trust_score": 1.0}}

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    world = FakeWorld()
    group = AgentSelector(world).all()

    await group.instruct("remember this", memory=True)
    await group.instruct("lightweight pilot", memory=True, extract_memory=False)
    await group.instruct("no managed memory", memory=False)

    assert world.calls[0]["retrieve_memory"] is True
    assert world.calls[0]["save_memory"] is True
    assert world.calls[0]["extract_memory"] is True
    assert world.calls[1]["extract_memory"] is False
    assert world.calls[2]["retrieve_memory"] is False
    assert world.calls[2]["save_memory"] is False
    assert world.calls[2]["extract_memory"] is False


@pytest.mark.asyncio
async def test_agent_group_instruct_required_actions_turn_missing_action_into_error(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))

    class FakeWorld:
        step = 1
        _current_code_step_name = "publish_check"
        _default_agent_concurrency = 2
        _model_provider = None
        agents_data = {
            "alice": {"id": "alice", "type": "participant", "archetype": "llm", "state": {}, "properties": {}},
            "bob": {"id": "bob", "type": "participant", "archetype": "llm", "state": {}, "properties": {}},
        }

        def __init__(self, event_logger):
            self.event_logger = event_logger

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            if agent_id == "alice":
                return {
                    "actions": [
                        {
                            "type": "action_call",
                            "action_name": "publish_post",
                            "status": "success",
                            "result": "published",
                        }
                    ],
                    "total_turns": 1,
                    "llm_calls": 1,
                }
            return {"actions": [], "total_turns": 1, "llm_calls": 1}

        def get_context_stack(self):
            return ContextStack().push_step("step_1")

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    result = await AgentSelector(FakeWorld(event_logger)).all().instruct(
        "publish once",
        actions=["publish_post"],
        memory=False,
        name="publish_round",
        required_actions=["publish_post"],
    )
    event_logger.close()
    events = _read_jsonl(events_path)
    started = next(event for event in events if event.get("event_type") == "agent_batch_started")
    summary = Society0(save_dir=str(tmp_path), base_config=_base_config())._summarize_events()
    batch = summary["agent_batches"]["instruct / publish_round"]

    assert result.success_count == 1
    assert result.error_count == 1
    assert result.by_agent("alice").status == "success"
    assert result.by_agent("bob").status == "error"
    assert result.by_agent("bob").error == "missing required action(s): publish_post"
    assert result.action_counts() == {"publish_post": 1}
    assert started["event_data"]["execution_options"]["required_actions"] == ["publish_post"]
    assert batch["error_samples"] == [
        {
            "agent_id": "bob",
            "status": "error",
            "error": "missing required action(s): publish_post",
        }
    ]


@pytest.mark.asyncio
async def test_agent_group_instruct_required_action_tags_turn_missing_tag_into_error(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))

    class FakeWorld:
        step = 1
        _current_code_step_name = "social_write_check"
        _default_agent_concurrency = 2
        _model_provider = None
        agents_data = {
            "alice": {"id": "alice", "type": "participant", "archetype": "llm", "state": {}, "properties": {}},
            "bob": {"id": "bob", "type": "participant", "archetype": "llm", "state": {}, "properties": {}},
        }

        def __init__(self, event_logger):
            self.event_logger = event_logger

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            if agent_id == "alice":
                return {
                    "actions": [
                        {
                            "type": "action_call",
                            "action_name": "comment",
                            "tags": ["comment", "social_write", "engagement"],
                            "status": "success",
                            "result": "commented",
                        }
                    ],
                    "total_turns": 1,
                    "llm_calls": 1,
                }
            return {
                "actions": [
                    {
                        "type": "action_call",
                        "action_name": "get_trending_posts",
                        "tags": ["get_trending_posts", "social_read", "lookup"],
                        "status": "success",
                        "result": "hot posts",
                    }
                ],
                "total_turns": 1,
                "llm_calls": 1,
            }

        def get_context_stack(self):
            return ContextStack().push_step("step_1")

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    result = await AgentSelector(FakeWorld(event_logger)).all().instruct(
        "make one real social interaction",
        actions=["get_trending_posts", "comment"],
        memory=False,
        name="social_round",
        required_action_tags=["social_write"],
    )
    event_logger.close()
    events = _read_jsonl(events_path)
    started = next(event for event in events if event.get("event_type") == "agent_batch_started")
    summary = Society0(save_dir=str(tmp_path), base_config=_base_config())._summarize_events()
    batch = summary["agent_batches"]["instruct / social_round"]

    assert result.success_count == 1
    assert result.error_count == 1
    assert result.by_agent("alice").status == "success"
    assert result.by_agent("bob").status == "error"
    assert result.by_agent("bob").error == "missing required action tag(s): social_write"
    assert result.action_counts() == {"comment": 1, "get_trending_posts": 1}
    assert result.action_tag_counts() == {
        "comment": 1,
        "engagement": 1,
        "get_trending_posts": 1,
        "lookup": 1,
        "social_read": 1,
        "social_write": 1,
    }
    assert started["event_data"]["execution_options"]["required_action_tags"] == ["social_write"]
    completed = next(event for event in events if event.get("event_type") == "agent_batch_completed")
    assert completed["event_data"]["action_counts"] == {"comment": 1, "get_trending_posts": 1}
    assert completed["event_data"]["action_tag_counts"] == result.action_tag_counts()
    assert batch["action_counts"] == {"comment": 1, "get_trending_posts": 1}
    assert batch["action_tag_counts"] == result.action_tag_counts()
    assert batch["error_samples"] == [
        {
            "agent_id": "bob",
            "status": "error",
            "error": "missing required action tag(s): social_write",
        }
    ]


@pytest.mark.asyncio
async def test_terminal_action_stops_agent_loop_after_tool_call():
    action_set = ActionSet()
    called_actions = []
    llm_calls = []

    async def submit_final_decision(decision: str):
        called_actions.append(decision)
        return {"accepted": True, "decision": decision}

    action_set.add_action(
        name="submit_final_decision",
        func=submit_final_decision,
        description="Submit the final decision and end this task",
        parameters={
            "type": "object",
            "properties": {"decision": {"type": "string"}},
            "required": ["decision"],
        },
        tags=["submit_final_decision", "environment"],
    )

    async def fake_llm_call(payload):
        llm_calls.append(payload)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "submit_final_decision",
                        "arguments": '{"decision": "approve"}',
                    },
                }
            ],
        }

    result = await execute_action_loop(
        instruction="Submit your final decision for this round.",
        action_set=action_set,
        system_prompt="You are a test agent.",
        stages=[{"name": "回答", "desc": "act"}],
        llm_call=fake_llm_call,
        max_turns=3,
        terminal_action_names=["submit_final_decision"],
    )

    assert called_actions == ["approve"]
    assert len(llm_calls) == 1
    assert llm_calls[0]["tool_choice"] == "auto"
    assert result.total_turns == 1
    assert [call["action_name"] for call in result.action_calls] == ["submit_final_decision"]
    assert result.termination_reason == "terminal_action"
    assert result.termination_action == "submit_final_decision"


@pytest.mark.asyncio
async def test_terminal_action_requires_success_before_ending_loop():
    action_set = ActionSet()
    called_actions = []
    llm_calls = []

    async def submit_final_decision(decision: str):
        called_actions.append(decision)
        if decision == "bad":
            return {"ok": False, "error": "decision payload was invalid"}
        return {"ok": True, "decision": decision}

    action_set.add_action(
        name="submit_final_decision",
        func=submit_final_decision,
        description="Submit the final decision and end this task",
        parameters={
            "type": "object",
            "properties": {"decision": {"type": "string"}},
            "required": ["decision"],
        },
        tags=["submit_final_decision", "environment"],
    )

    async def fake_llm_call(payload):
        llm_calls.append(payload)
        if len(llm_calls) == 1:
            decision = "bad"
            call_id = "call_bad"
        else:
            decision = "approve"
            call_id = "call_good"
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "submit_final_decision",
                        "arguments": f'{{"decision": "{decision}"}}',
                    },
                }
            ],
        }

    result = await execute_action_loop(
        instruction="Submit your final decision for this round.",
        action_set=action_set,
        system_prompt="You are a test agent.",
        stages=[{"name": "回答", "desc": "act"}],
        llm_call=fake_llm_call,
        max_turns=3,
        terminal_action_names=["submit_final_decision"],
    )

    assert called_actions == ["bad", "approve"]
    assert len(llm_calls) == 2
    assert result.total_turns == 2
    assert [call["status"] for call in result.action_calls] == ["error", "success"]
    assert result.termination_reason == "terminal_action"
    assert result.termination_action == "submit_final_decision"


@pytest.mark.asyncio
async def test_required_action_gets_correction_turn_when_model_stops_early():
    action_set = ActionSet()
    called = []
    llm_payloads = []

    async def publish_post(content: str):
        called.append(content)
        return "Successfully published post post_1"

    action_set.add_action(
        name="publish_post",
        func=publish_post,
        description="Publish a post",
        parameters={
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
        tags=["publish_post", "social_write"],
    )

    async def fake_llm_call(payload):
        llm_payloads.append(
            {"messages": [dict(message) for message in payload["messages"]]}
        )
        if len(llm_payloads) == 1:
            return {"role": "assistant", "content": "I will publish a post.", "tool_calls": []}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_publish",
                    "type": "function",
                    "function": {
                        "name": "publish_post",
                        "arguments": '{"content": "Campus is lively today."}',
                    },
                }
            ],
        }

    result = await execute_action_loop(
        instruction="Publish exactly one post.",
        action_set=action_set,
        system_prompt="You are a test agent.",
        stages=[{"name": "回答", "desc": "act"}],
        llm_call=fake_llm_call,
        max_turns=3,
        action_call_limits={"publish_post": 1},
        required_action_names=["publish_post"],
    )

    assert called == ["Campus is lively today."]
    assert len(llm_payloads) == 2
    assert "Required action name(s): publish_post" in llm_payloads[1]["messages"][-1]["content"]
    assert [call["action_name"] for call in result.action_calls] == ["publish_post"]
    assert result.termination_reason == "action_budget_exhausted"


@pytest.mark.asyncio
async def test_completion_action_tag_stops_after_successful_matching_action():
    action_set = ActionSet()
    called = []
    llm_calls = []

    async def get_trending_posts():
        called.append(("get_trending_posts", None))
        return "hot posts"

    async def comment(post_id: str, content: str):
        called.append(("comment", post_id))
        return f"commented:{post_id}"

    action_set.add_action(
        name="get_trending_posts",
        func=get_trending_posts,
        description="Read trending posts",
        parameters={"type": "object", "properties": {}},
        tags=["social_read", "lookup"],
    )
    action_set.add_action(
        name="comment",
        func=comment,
        description="Comment on a post",
        parameters={
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["post_id", "content"],
        },
        tags=["social_write", "engagement"],
    )

    async def fake_llm_call(payload):
        llm_calls.append(payload)
        if len(llm_calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_read",
                        "type": "function",
                        "function": {
                            "name": "get_trending_posts",
                            "arguments": "{}",
                        },
                    }
                ],
            }
        if len(llm_calls) == 2:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_write",
                        "type": "function",
                        "name": "comment",
                        "function": {
                            "name": "comment",
                            "arguments": '{"post_id": "post_1", "content": "same here"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "extra turn should not happen", "tool_calls": []}

    result = await execute_action_loop(
        instruction="Browse, optionally read, then make one real interaction if useful.",
        action_set=action_set,
        system_prompt="You are a test agent.",
        stages=[{"name": "回答", "desc": "act"}],
        llm_call=fake_llm_call,
        max_turns=4,
        completion_action_tags=["social_write"],
    )

    assert called == [("get_trending_posts", None), ("comment", "post_1")]
    assert len(llm_calls) == 2
    assert result.total_turns == 2
    assert [call["action_name"] for call in result.action_calls] == ["get_trending_posts", "comment"]
    assert result.action_calls[0]["tags"] == ["get_trending_posts", "social_read", "lookup"]
    assert result.action_calls[1]["tags"] == ["comment", "social_write", "engagement"]
    assert result.termination_reason == "completion_action_tag"
    assert result.termination_action == "comment"


@pytest.mark.asyncio
async def test_completion_action_tag_ignores_semantic_action_failure():
    action_set = ActionSet()
    called = []
    llm_calls = []

    async def comment(post_id: str, content: str):
        called.append(post_id)
        if post_id == "missing_post":
            return "Post missing_post not found"
        return f"Successfully commented on post {post_id}"

    action_set.add_action(
        name="comment",
        func=comment,
        description="Comment on a post",
        parameters={
            "type": "object",
            "properties": {
                "post_id": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["post_id", "content"],
        },
        tags=["social_write", "engagement"],
    )

    async def fake_llm_call(payload):
        llm_calls.append(payload)
        if len(llm_calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_missing",
                        "type": "function",
                        "function": {
                            "name": "comment",
                            "arguments": '{"post_id": "missing_post", "content": "bad id"}',
                        },
                    }
                ],
            }
        if len(llm_calls) == 2:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_valid",
                        "type": "function",
                        "function": {
                            "name": "comment",
                            "arguments": '{"post_id": "post_1", "content": "valid id"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "extra turn should not happen", "tool_calls": []}

    result = await execute_action_loop(
        instruction="Comment on a visible post.",
        action_set=action_set,
        system_prompt="You are a test agent.",
        stages=[{"name": "回答", "desc": "act"}],
        llm_call=fake_llm_call,
        max_turns=3,
        completion_action_tags=["social_write"],
    )

    assert called == ["missing_post", "post_1"]
    assert len(llm_calls) == 2
    assert [call["status"] for call in result.action_calls] == ["error", "success"]
    assert result.action_calls[0]["error"] == "Post missing_post not found"


def test_empty_action_tags_filter_exposes_no_actions():
    action_set = ActionSet()

    async def recall(query: str):
        return {"memories": []}

    action_set.add_action(
        name="recall",
        func=recall,
        description="Recall memory",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        tags=["memory"],
    )

    assert list(action_set.filter_by_tags(None).actions) == ["recall"]
    assert list(action_set.filter_by_tags([]).actions) == []


def test_exact_action_filter_does_not_expand_same_named_tags():
    action_set = ActionSet()

    async def follow(target_agent_id: str):
        return f"followed:{target_agent_id}"

    async def unfollow(target_agent_id: str):
        return f"unfollowed:{target_agent_id}"

    async def get_trending_posts():
        return "trending"

    action_set.add_action(
        name="follow",
        func=follow,
        description="Follow a user",
        parameters={
            "type": "object",
            "properties": {"target_agent_id": {"type": "string"}},
            "required": ["target_agent_id"],
        },
        tags=["social", "follow"],
    )
    action_set.add_action(
        name="unfollow",
        func=unfollow,
        description="Unfollow a user",
        parameters={
            "type": "object",
            "properties": {"target_agent_id": {"type": "string"}},
            "required": ["target_agent_id"],
        },
        tags=["social", "follow"],
    )
    action_set.add_action(
        name="get_trending_posts",
        func=get_trending_posts,
        description="Read trending posts",
        parameters={"type": "object", "properties": {}},
        tags=["social_read"],
    )

    filtered = action_set.filter_by_tags(["social_read", "follow"])

    assert list(filtered.actions) == ["follow", "get_trending_posts"]


@pytest.mark.asyncio
async def test_submit_result_only_action_loop_uses_compact_prompt():
    action_set = ActionSet()
    llm_calls = []

    async def submit_result(result: dict):
        return {"accepted": True, "result": result}

    action_set.add_action(
        name="submit_result",
        func=submit_result,
        description="Submit structured result",
        parameters={
            "type": "object",
            "properties": {
                "result": {
                    "type": "object",
                    "properties": {"trust_score": {"type": "number"}},
                    "required": ["trust_score"],
                }
            },
            "required": ["result"],
        },
        tags=["system", "output"],
    )

    async def fake_llm_call(payload):
        llm_calls.append(payload)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "submit_result",
                        "arguments": '{"result": {"trust_score": 4.0}}',
                    },
                }
            ],
        }

    result = await execute_action_loop(
        instruction="Return a trust score.",
        action_set=action_set,
        system_prompt="You are a test agent.",
        stages=[{"name": "思考", "desc": "think"}, {"name": "回答", "desc": "answer"}],
        llm_call=fake_llm_call,
        max_turns=3,
    )

    system_content = llm_calls[0]["messages"][0]["content"]
    assert "直接调用 submit_result 工具" in system_content
    assert "阶段标记的格式" not in system_content
    assert "-> STAGE_BEGIN" not in system_content
    assert llm_calls[0]["tool_choice"] == {"type": "function", "function": {"name": "submit_result"}}
    assert result.total_turns == 1
    assert result.action_calls[0]["action_name"] == "submit_result"


@pytest.mark.asyncio
async def test_non_terminal_tool_call_continues_agent_loop():
    action_set = ActionSet()
    llm_calls = []

    async def publish_post(content: str):
        return {"post_id": "post_1", "content": content}

    action_set.add_action(
        name="publish_post",
        func=publish_post,
        description="Publish one post",
        parameters={
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
        tags=["publish_post", "environment"],
    )

    async def fake_llm_call(payload):
        llm_calls.append(payload)
        if len(llm_calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "publish_post",
                            "arguments": '{"content": "hello"}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "done", "tool_calls": []}

    result = await execute_action_loop(
        instruction="Publish one post.",
        action_set=action_set,
        system_prompt="You are a test agent.",
        stages=[{"name": "回答", "desc": "act"}],
        llm_call=fake_llm_call,
        max_turns=3,
    )

    assert len(llm_calls) == 2
    system_content = llm_calls[0]["messages"][0]["content"]
    assert "按任务需要简要思考并行动" in system_content
    assert "阶段标记的格式" not in system_content
    assert "-> STAGE_BEGIN" not in system_content
    assert result.total_turns == 2
    assert [call["action_name"] for call in result.action_calls] == ["publish_post"]


@pytest.mark.asyncio
async def test_action_call_limits_stop_when_all_available_actions_exhausted_without_terminal_action():
    action_set = ActionSet()
    published = []
    llm_calls = []

    async def publish_post(content: str):
        published.append(content)
        return {"post_id": f"post_{len(published)}", "content": content}

    action_set.add_action(
        name="publish_post",
        func=publish_post,
        description="Publish one post",
        parameters={
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
        tags=["publish_post", "environment"],
    )

    async def fake_llm_call(payload):
        llm_calls.append(payload)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": f"call_{len(llm_calls)}",
                    "type": "function",
                    "function": {
                        "name": "publish_post",
                        "arguments": f'{{"content": "post {len(llm_calls)}"}}',
                    },
                }
            ],
        }

    result = await execute_action_loop(
        instruction="Publish once, then continue if needed.",
        action_set=action_set,
        system_prompt="You are a test agent.",
        stages=[{"name": "回答", "desc": "act"}],
        llm_call=fake_llm_call,
        max_turns=2,
        action_call_limits={"publish_post": 1},
    )

    assert published == ["post 1"]
    assert len(llm_calls) == 1
    assert llm_calls[0]["tool_choice"] == "auto"
    assert result.total_turns == 1
    assert [call["action_name"] for call in result.action_calls] == ["publish_post"]


@pytest.mark.asyncio
async def test_plain_action_instruction_omits_redundant_output_requirements_when_memory_disabled():
    calls = []

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "social_user",
                "archetype": "llm",
                "persona": "Act concisely.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack()

        def set_context_stack(self, stack):
            self.context_stack = stack

    action_set = ActionSet()

    async def publish_post(content: str):
        return f"published:{content}"

    action_set.add_action(
        name="publish_post",
        func=publish_post,
        description="Publish one post",
        parameters={
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
        tags=["publish_post", "environment"],
    )

    async def fake_llm_call(payload):
        calls.append(payload)
        return {"role": "assistant", "content": "done", "tool_calls": []}

    agent = LLMAgent("alice", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Act concisely.",
        memory=None,
        llm_call=fake_llm_call,
        actionset=action_set,
    )

    await agent.instruct(
        "Call publish_post once.",
        action_tags=["publish_post"],
        retrieve_memory=False,
        save_memory=False,
        max_turns=1,
    )

    user_prompt = calls[0]["messages"][1]["content"]
    assert "[输出要求]" not in user_prompt
    assert "结构化输出" not in user_prompt
    assert "记忆策略" not in user_prompt
    assert "[任务]" in user_prompt


@pytest.mark.asyncio
async def test_action_call_limits_continue_when_other_actions_remain_available():
    action_set = ActionSet()
    called = []
    llm_calls = []

    async def publish_post(content: str):
        called.append(("publish_post", content))
        return {"post_id": "post_1"}

    async def like_post(post_id: str):
        called.append(("like_post", post_id))
        return "liked"

    action_set.add_action(
        name="publish_post",
        func=publish_post,
        description="Publish one post",
        parameters={
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
        tags=["publish_post", "environment"],
    )
    action_set.add_action(
        name="like_post",
        func=like_post,
        description="Like one post",
        parameters={
            "type": "object",
            "properties": {"post_id": {"type": "string"}},
            "required": ["post_id"],
        },
        tags=["like_post", "environment"],
    )

    async def fake_llm_call(payload):
        llm_calls.append(payload)
        if len(llm_calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "publish_post",
                            "arguments": '{"content": "post 1"}',
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_2",
                    "type": "function",
                    "function": {
                        "name": "like_post",
                        "arguments": '{"post_id": "post_1"}',
                    },
                }
            ],
        }

    result = await execute_action_loop(
        instruction="Publish and then like if useful.",
        action_set=action_set,
        system_prompt="You are a test agent.",
        stages=[{"name": "回答", "desc": "act"}],
        llm_call=fake_llm_call,
        max_turns=3,
        action_call_limits={"publish_post": 1, "like_post": 1},
    )

    assert called == [("publish_post", "post 1"), ("like_post", "post_1")]
    assert len(llm_calls) == 2
    assert result.total_turns == 2
    assert [call["action_name"] for call in result.action_calls] == ["publish_post", "like_post"]


@pytest.mark.asyncio
async def test_concurrent_action_context_is_task_local():
    world = World()
    world.set_context_stack(ContextStack().push_step("step_0"))
    action_set = ActionSet()
    slow_started = asyncio.Event()
    release_slow = asyncio.Event()
    observed = {}

    async def slow_action():
        slow_started.set()
        await release_slow.wait()
        return "slow_done"

    async def fast_action():
        observed["fast_stack"] = world.get_context_stack().to_list()
        return "fast_done"

    action_set.add_action("slow_action", slow_action, "slow", {"type": "object", "properties": {}})
    action_set.add_action("fast_action", fast_action, "fast", {"type": "object", "properties": {}})

    def context_provider():
        return world.get_context_stack(), world.set_context_stack

    slow_task = asyncio.create_task(action_set.call_action("slow_action", context_provider=context_provider))
    await slow_started.wait()
    await action_set.call_action("fast_action", context_provider=context_provider)
    release_slow.set()
    await slow_task

    assert [frame["id"] for frame in observed["fast_stack"]] == ["step_0", "fast_action"]


@pytest.mark.asyncio
async def test_action_context_provider_records_read_only_actions():
    world = World()
    world.set_context_stack(ContextStack().push_step("step_0"))
    action_set = ActionSet()
    traces = []

    async def read_profile(agent_id: str):
        return f"profile:{agent_id}"

    action_set.add_action(
        "get_agent_profile",
        read_profile,
        "read profile",
        {
            "type": "object",
            "properties": {"agent_id": {"type": "string"}},
            "required": ["agent_id"],
        },
    )

    def context_provider():
        def record_action(action_name, arguments, result, status):
            traces.append(
                {
                    "action_name": action_name,
                    "arguments": arguments,
                    "result": result,
                    "status": status,
                    "stack": world.get_context_stack().to_list(),
                }
            )

        return world.get_context_stack(), world.set_context_stack, record_action

    result = await action_set.call_action("get_agent_profile", context_provider=context_provider, agent_id="alice")

    assert result == "profile:alice"
    assert traces == [
        {
            "action_name": "get_agent_profile",
            "arguments": {"agent_id": "alice"},
            "result": "profile:alice",
            "status": "success",
            "stack": [
                {"type": "step", "id": "step_0", "params": {}, "metadata": {}},
                {"type": "action", "id": "get_agent_profile", "params": {"agent_id": "alice"}, "metadata": {}},
            ],
        }
    ]


@pytest.mark.asyncio
async def test_action_context_provider_records_action_failures():
    world = World()
    world.set_context_stack(ContextStack().push_step("step_0"))
    action_set = ActionSet()
    traces = []

    async def fail_action(item_id: str):
        raise RuntimeError(f"missing:{item_id}")

    action_set.add_action(
        "fail_action",
        fail_action,
        "fail",
        {
            "type": "object",
            "properties": {"item_id": {"type": "string"}},
            "required": ["item_id"],
        },
    )

    def context_provider():
        def record_action(action_name, arguments, result, status):
            traces.append(
                {
                    "action_name": action_name,
                    "arguments": arguments,
                    "result": result,
                    "status": status,
                    "stack": world.get_context_stack().to_list(),
                }
            )

        return world.get_context_stack(), world.set_context_stack, record_action

    with pytest.raises(RuntimeError, match="missing:post_1"):
        await action_set.call_action("fail_action", context_provider=context_provider, item_id="post_1")

    assert traces == [
        {
            "action_name": "fail_action",
            "arguments": {"item_id": "post_1"},
            "result": "missing:post_1",
            "status": "error",
            "stack": [
                {"type": "step", "id": "step_0", "params": {}, "metadata": {}},
                {"type": "action", "id": "fail_action", "params": {"item_id": "post_1"}, "metadata": {}},
            ],
        }
    ]


@pytest.mark.asyncio
async def test_execute_action_loop_records_budget_blocked_actions():
    world = World()
    world.set_context_stack(ContextStack().push_step("step_0"))
    action_set = ActionSet()
    traces = []

    async def limited_action():
        return "should not run"

    action_set.add_action("limited_action", limited_action, "limited", {"type": "object", "properties": {}})

    async def fake_llm(_payload):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "limited_action", "arguments": "{}"},
                }
            ],
        }

    def context_provider():
        def record_action(action_name, arguments, result, status):
            traces.append(
                {
                    "action_name": action_name,
                    "arguments": arguments,
                    "result": result,
                    "status": status,
                    "stack": world.get_context_stack().to_list(),
                }
            )

        return world.get_context_stack(), world.set_context_stack, record_action

    result = await execute_action_loop(
        instruction="try action",
        action_set=action_set,
        system_prompt="system",
        stages=["Reflection"],
        llm_call=fake_llm,
        max_turns=1,
        context_provider=context_provider,
        max_action_calls=0,
    )

    assert result.action_calls[0]["action_name"] == "limited_action"
    assert result.action_calls[0]["result"] == "Action budget exhausted: max_action_calls=0"
    assert traces == [
        {
            "action_name": "limited_action",
            "arguments": {},
            "result": "Action budget exhausted: max_action_calls=0",
            "status": "blocked",
            "stack": [{"type": "step", "id": "step_0", "params": {}, "metadata": {}}],
        }
    ]


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
async def test_agent_group_instruct_writes_progress_events(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))

    class FakeWorld:
        step = 3
        _current_code_step_name = "measure"
        _default_agent_concurrency = 2
        _model_provider = None
        agents_data = {
            "alice": {"type": "participant"},
            "bob": {"type": "participant"},
            "carol": {"type": "participant"},
        }

        def __init__(self, event_logger):
            self.event_logger = event_logger

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            return {"structured_output": {"ok": True, "agent_id": agent_id}}

        def get_context_stack(self):
            return ContextStack().push_step("step_3")

    result = await AgentSelector(FakeWorld(event_logger)).all().instruct(
        "answer",
        fovs=["feed"],
        actions=["environment"],
        memory=False,
        name="measure_round",
    )

    event_logger.close()
    events = _read_jsonl(events_path)
    batch_events = [event for event in events if event.get("event_type", "").startswith("agent_batch_")]
    progress_events = [event for event in batch_events if event.get("event_type") == "agent_batch_progress"]
    lifecycle_events = [
        event
        for event in batch_events
        if event.get("event_type") in {"agent_batch_started", "agent_batch_completed"}
    ]

    assert result.success_count == 3
    assert [event["event_type"] for event in lifecycle_events] == [
        "agent_batch_started",
        "agent_batch_completed",
    ]
    assert lifecycle_events[0]["event_data"] == {
        "step": 3,
        "step_name": "measure",
        "interaction_type": "instruct",
        "interaction_name": "measure_round",
        "agent_count": 3,
        "concurrency": 2,
        "concurrency_source": "world_default",
        "model_id": None,
        "fovs": ["feed"],
        "actions": ["environment"],
        "target_ids_sample": ["alice", "bob", "carol"],
        "execution_options": {
            "max_turns": 3,
            "output_schema": False,
            "reasoning_stage_count": 0,
            "reasoning_stages": [],
            "memory": {"retrieve": False, "save": False, "extract": False, "top_k": 10},
            "llm_request_options": {},
        },
    }
    assert [event["event_data"]["completed_count"] for event in progress_events] == [1, 2, 3]
    assert progress_events[-1]["event_data"]["success_count"] == 3
    assert progress_events[-1]["event_data"]["error_count"] == 0
    assert progress_events[-1]["event_data"]["latest_agent_id"] in {"alice", "bob", "carol"}
    assert lifecycle_events[1]["event_data"]["success_count"] == 3
    assert lifecycle_events[1]["event_data"]["error_count"] == 0
    assert lifecycle_events[1]["event_data"]["duration_sec"] >= 0


@pytest.mark.asyncio
async def test_short_agent_batch_progress_records_in_flight_without_heartbeat(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))

    class SlowWorld:
        step = 6
        _current_code_step_name = "short_parallel"
        _default_agent_concurrency = 2
        _agent_batch_heartbeat_interval_sec = 999
        _model_provider = None
        agents_data = {
            "alice": {"type": "participant"},
            "bob": {"type": "participant"},
            "carol": {"type": "participant"},
            "dave": {"type": "participant"},
        }

        def __init__(self, event_logger):
            self.event_logger = event_logger

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            await asyncio.sleep(0.02)
            return {"structured_output": {"ok": True, "agent_id": agent_id}}

        def get_context_stack(self):
            return ContextStack().push_step("step_6")

    result = await AgentSelector(SlowWorld(event_logger)).all().instruct(
        "answer briefly",
        memory=False,
        name="short_round",
    )

    event_logger.close()
    events = _read_jsonl(events_path)
    progress_events = [event for event in events if event.get("event_type") == "agent_batch_progress"]
    heartbeat_events = [event for event in events if event.get("event_type") == "agent_batch_heartbeat"]
    summary = Society0(save_dir=str(tmp_path), base_config=_base_config())._summarize_events()
    batch = summary["agent_batches"]["instruct / short_round"]

    assert result.success_count == 4
    assert not heartbeat_events
    assert len(progress_events) == 4
    assert max(event["event_data"]["in_flight_count"] for event in progress_events) == 2
    assert max(event["event_data"]["started_count"] for event in progress_events) == 4
    assert progress_events[-1]["event_data"]["pending_count"] == 0
    assert batch["progress_event_count"] == 4
    assert batch["heartbeat_event_count"] == 0
    assert batch["max_in_flight_count"] == 2
    assert batch["max_started_count"] == 4
    assert batch["concurrency_source"] == "world_default"
    assert batch["concurrency_source_counts"] == {"world_default": 1}
    assert batch["by_tick"]["6"]["concurrency_source"] == "world_default"


@pytest.mark.asyncio
async def test_agent_batch_events_record_fidelity_execution_options(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))

    class FakeWorld:
        step = 5
        _current_code_step_name = "fidelity_round"
        _default_agent_concurrency = 1
        _model_provider = None
        agents_data = {"alice": {"type": "participant"}}

        def __init__(self, event_logger):
            self.event_logger = event_logger

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            return {
                "structured_output": {"ok": True, "agent_id": agent_id},
                "memory_retrieved": kwargs.get("retrieve_memory"),
                "memory_top_k": kwargs.get("memory_top_k"),
                "memory_saved": kwargs.get("save_memory"),
                "memory_extraction_enabled": kwargs.get("extract_memory"),
                "memory_extraction_success": True,
                "extracted_memories": [{"content": "remembered action"}],
            }

        async def interview_agent(self, agent_id, question, **kwargs):
            return {
                "structured_output": {"trust_score": 4},
                "memory_retrieved": kwargs.get("retrieve_memory"),
                "memory_top_k": kwargs.get("memory_top_k"),
                "memory_saved": kwargs.get("save_memory"),
                "memory_extraction_enabled": False,
                "memory_extraction_success": False,
                "extracted_memories": [],
            }

        def get_context_stack(self):
            return ContextStack().push_step("step_5")

    group = AgentSelector(FakeWorld(event_logger)).all()
    await group.instruct(
        "act with tools",
        fovs=["recommended_feed"],
        actions=["publish_post", "comment"],
        output={"type": "object"},
        memory=True,
        extract_memory=True,
        max_turns=4,
        name="tool_round",
        reasoning_stages=[{"name": "plan", "desc": "Plan before acting."}],
        terminal_actions=["submit_final_decision"],
        completion_action_tags=["social_write"],
        max_action_calls=3,
        action_call_limits={"publish_post": 1},
        memory_top_k=7,
        max_tokens=90,
        temperature=0.1,
        llm_options={"vendor_hint": "do-not-log-this-value"},
    )
    await group.interview(
        "measure memory",
        fovs=["recent_posts"],
        output=TrustOutput,
        retrieve_memory=True,
        save_memory=False,
        max_turns=2,
        name="survey_round",
        reasoning_stages=[{"name": "answer", "description": "Answer directly."}],
        memory_top_k=5,
        top_p=0.8,
    )

    event_logger.close()
    events = _read_jsonl(events_path)
    started_events = [event for event in events if event.get("event_type") == "agent_batch_started"]
    completed_events = [event for event in events if event.get("event_type") == "agent_batch_completed"]
    instruct_options = started_events[0]["event_data"]["execution_options"]
    interview_options = started_events[1]["event_data"]["execution_options"]

    assert instruct_options["max_turns"] == 4
    assert instruct_options["output_schema"] is True
    assert instruct_options["memory"] == {"retrieve": True, "save": True, "extract": True, "top_k": 7}
    assert instruct_options["reasoning_stage_count"] == 1
    assert instruct_options["reasoning_stages"][0]["name"] == "plan"
    assert instruct_options["reasoning_stages"][0]["description_length"] == len("Plan before acting.")
    assert instruct_options["terminal_actions"] == ["submit_final_decision"]
    assert instruct_options["completion_action_tags"] == ["social_write"]
    assert instruct_options["max_action_calls"] == 3
    assert instruct_options["action_call_limits"] == {"publish_post": 1}
    assert instruct_options["llm_request_options"] == {
        "max_tokens": 90,
        "temperature": 0.1,
        "custom_option_keys": ["vendor_hint"],
    }
    assert "do-not-log-this-value" not in json.dumps(events, ensure_ascii=False)

    assert interview_options["max_turns"] == 2
    assert interview_options["output_schema"] is True
    assert interview_options["memory"] == {"retrieve": True, "save": False, "extract": False, "top_k": 5}
    assert interview_options["reasoning_stage_count"] == 1
    assert interview_options["reasoning_stages"][0]["name"] == "answer"
    assert interview_options["llm_request_options"] == {"top_p": 0.8}
    assert "terminal_actions" not in interview_options
    assert completed_events[0]["event_data"]["memory_summary"] == {
        "record_count": 1,
        "retrieve_enabled_count": 1,
        "save_enabled_count": 1,
        "extraction_enabled_count": 1,
        "extraction_success_count": 1,
        "extraction_error_count": 0,
        "extracted_memory_count": 1,
        "top_k_values": [7],
    }
    assert completed_events[1]["event_data"]["memory_summary"] == {
        "record_count": 1,
        "retrieve_enabled_count": 1,
        "save_enabled_count": 0,
        "extraction_enabled_count": 0,
        "extraction_success_count": 0,
        "extraction_error_count": 0,
        "extracted_memory_count": 0,
        "top_k_values": [5],
    }
    summary = Society0(save_dir=str(tmp_path), base_config=_base_config())._summarize_events()
    assert summary["agent_batches"]["instruct / tool_round"]["memory_summary"]["extraction_success_count"] == 1
    assert summary["agent_batches"]["interview / survey_round"]["memory_summary"]["save_enabled_count"] == 0


@pytest.mark.asyncio
async def test_agent_group_instruct_writes_heartbeat_events_while_in_flight(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))

    class SlowWorld:
        step = 4
        _current_code_step_name = "long_measure"
        _default_agent_concurrency = 2
        _agent_batch_heartbeat_interval_sec = 0.01
        _model_provider = None
        agents_data = {
            "alice": {"type": "participant"},
            "bob": {"type": "participant"},
            "carol": {"type": "participant"},
        }

        def __init__(self, event_logger):
            self.event_logger = event_logger

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            await asyncio.sleep(0.05)
            return {"structured_output": {"ok": True, "agent_id": agent_id}}

        def get_context_stack(self):
            return ContextStack().push_step("step_4")

    result = await AgentSelector(SlowWorld(event_logger)).all().instruct(
        "answer slowly",
        memory=False,
        name="slow_round",
    )

    event_logger.close()
    events = _read_jsonl(events_path)
    heartbeat_events = [
        event
        for event in events
        if event.get("event_type") == "agent_batch_heartbeat"
    ]

    assert result.success_count == 3
    assert heartbeat_events
    first_heartbeat = heartbeat_events[0]["event_data"]
    assert first_heartbeat["step"] == 4
    assert first_heartbeat["step_name"] == "long_measure"
    assert first_heartbeat["interaction_type"] == "instruct"
    assert first_heartbeat["interaction_name"] == "slow_round"
    assert first_heartbeat["agent_count"] == 3
    assert first_heartbeat["concurrency"] == 2
    assert first_heartbeat["concurrency_source"] == "world_default"
    assert first_heartbeat["started_count"] == 2
    assert first_heartbeat["in_flight_count"] == 2
    assert first_heartbeat["pending_count"] == 1
    assert set(first_heartbeat["running_agent_ids_sample"]).issubset({"alice", "bob", "carol"})
    assert first_heartbeat["completed_count"] == 0


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
async def test_agent_batch_events_record_concurrency_source(tmp_path):
    events_path = tmp_path / "events.jsonl"
    event_logger = EventLogger(str(events_path))

    class FakeWorld:
        step = 8
        _current_code_step_name = "source_probe"
        _default_agent_concurrency = 2
        _default_agent_concurrency_source = "llm_model"
        _model_provider = None
        agents_data = {
            "alice": {"id": "alice", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}},
            "bob": {"id": "bob", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}},
        }

        def __init__(self, event_logger):
            self.event_logger = event_logger

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            return {"structured_output": {"trust_score": 1.0}}

        async def interview_agent(self, agent_id, question, **kwargs):
            return {"structured_output": {"trust_score": 1.0}}

        def get_context_stack(self):
            return ContextStack().push_step("step_8")

    world = FakeWorld(event_logger)
    group = AgentSelector(world).all()

    await group.instruct("act", name="model_source")
    await group.interview("rate", output=TrustOutput, name="explicit_source", concurrency=1)

    event_logger.close()
    events = _read_jsonl(events_path)
    completed_events = [event for event in events if event.get("event_type") == "agent_batch_completed"]
    assert completed_events[0]["event_data"]["concurrency"] == 2
    assert completed_events[0]["event_data"]["concurrency_source"] == "llm_model"
    assert completed_events[1]["event_data"]["concurrency"] == 1
    assert completed_events[1]["event_data"]["concurrency_source"] == "explicit"

    summary = Society0(save_dir=str(tmp_path), base_config=_base_config())._summarize_events()
    assert summary["agent_batches"]["instruct / model_source"]["concurrency_source"] == "llm_model"
    assert summary["agent_batches"]["instruct / model_source"]["concurrency_source_counts"] == {
        "llm_model": 1
    }
    assert summary["agent_batches"]["interview / explicit_source"]["concurrency_source"] == "explicit"
    assert summary["agent_batches"]["interview / explicit_source"]["by_tick"]["8"][
        "concurrency_source_counts"
    ] == {"explicit": 1}


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


@pytest.mark.asyncio
async def test_agent_group_interview_passes_runtime_options_to_world():
    class FakeWorld:
        step = 1
        _default_agent_concurrency = 1
        agents_data = {
            "alice": {"id": "alice", "type": "social_user", "archetype": "llm", "state": {}, "properties": {}}
        }

        def __init__(self):
            self.kwargs = None

        async def interview_agent(self, agent_id, question, **kwargs):
            self.kwargs = kwargs
            return {"structured_output": {"trust_score": 1.0}}

        def get_agent(self, agent_id):
            return type("Agent", (), {"id": agent_id})()

    world = FakeWorld()

    await AgentSelector(world).all().interview(
        "rate",
        output=TrustOutput,
        retrieve_memory=False,
        save_memory=True,
        max_turns=1,
        reasoning_stages=[{"name": "Answer", "desc": "answer directly"}],
        name="survey",
        memory_top_k=4,
    )

    assert world.kwargs["retrieve_memory"] is False
    assert world.kwargs["save_memory"] is True
    assert world.kwargs["max_turns"] == 1
    assert world.kwargs["name"] == "survey"
    assert world.kwargs["reasoning_stages"] == [{"name": "Answer", "desc": "answer directly"}]
    assert world.kwargs["memory_top_k"] == 4


@pytest.mark.asyncio
async def test_world_interview_agent_passes_runtime_options_to_llm_agent(tmp_path):
    class FakeAgent:
        async def interview(self, **kwargs):
            self.kwargs = kwargs
            return {"status": "success", "structured_output": {"trust_score": 2.0}}

    fake_agent = FakeAgent()
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.agents_data["alice"] = {
        "id": "alice",
        "type": "social_user",
        "archetype": "llm",
        "state": {},
        "properties": {},
        "reminders": [],
    }
    world.get_agent = lambda agent_id: fake_agent
    world.get_environment = lambda: type("Env", (), {})()

    await world.interview_agent(
        "alice",
        "rate",
        retrieve_memory=False,
        save_memory=True,
        max_turns=1,
        memory_top_k=4,
        output_schema={"type": "object"},
        name="survey",
        llm_request_options={"max_tokens": 32, "temperature": 0.1},
    )

    assert fake_agent.kwargs["retrieve_memory"] is False
    assert fake_agent.kwargs["save_memory"] is True
    assert fake_agent.kwargs["max_turns"] == 1
    assert fake_agent.kwargs["memory_top_k"] == 4
    assert fake_agent.kwargs["llm_request_options"] == {"max_tokens": 32, "temperature": 0.1}


@pytest.mark.asyncio
async def test_world_instruct_logs_fov_preview_without_full_result_by_default(tmp_path):
    class FakeAgent:
        id = "alice"

        async def instruct(self, **kwargs):
            self.kwargs = kwargs
            return {
                "status": "success",
                "performative_output": "done",
                "total_turns": 0,
                "actions": [],
                "llm_calls": 0,
            }

    async def long_feed(agent, env):
        return "visible-feed-" + ("x" * 500)

    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    log_context = ExperimentLogContext(tmp_path / "logs")
    registry = FunctionRegistry()
    registry.env.fov(name="long_feed")(long_feed)
    fake_agent = FakeAgent()
    world.set_log_context(log_context)
    world.set_function_registry(registry)
    world.agents_data["alice"] = {
        "id": "alice",
        "type": "social_user",
        "archetype": "llm",
        "state": {},
        "properties": {},
        "reminders": [],
    }
    world.get_agent = lambda agent_id: fake_agent
    world.get_environment = lambda: object()

    try:
        await world.instruct_agent("alice", "inspect feed", fovs=["long_feed"])
    finally:
        log_context.close()

    records = _read_jsonl(tmp_path / "logs" / "agents" / "alice.jsonl")
    events = [record["event"] for record in records]
    assert "fov_executed" in events
    assert "fov_full_result" not in events
    fov_record = next(record for record in records if record["event"] == "fov_executed")
    assert fov_record["fov_result_length"] > len(fov_record["fov_result_preview"])


@pytest.mark.asyncio
async def test_world_interview_logs_fov_failure(tmp_path):
    class FakeAgent:
        id = "alice"

        async def interview(self, **kwargs):
            self.kwargs = kwargs
            return {
                "status": "success",
                "structured_output": {"trust_score": 3.0},
                "total_turns": 0,
                "actions": [],
                "llm_calls": 0,
            }

    fake_agent = FakeAgent()
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    log_context = ExperimentLogContext(tmp_path / "logs")
    world.set_log_context(log_context)
    world.set_function_registry(FunctionRegistry())
    world.agents_data["alice"] = {
        "id": "alice",
        "type": "social_user",
        "archetype": "llm",
        "state": {},
        "properties": {},
        "reminders": [],
    }
    world.get_agent = lambda agent_id: fake_agent
    world.get_environment = lambda: object()

    try:
        await world.interview_agent("alice", "rate", fovs=["missing_feed"])
    finally:
        log_context.close()

    records = _read_jsonl(tmp_path / "logs" / "agents" / "alice.jsonl")
    fov_failed = next(record for record in records if record["event"] == "fov_failed")
    assert fov_failed["fov_name"] == "missing_feed"
    assert "FoV function 'missing_feed' not found" in fov_failed["error"]
    assert fake_agent.kwargs["context"]["fov_results"]["missing_feed"]["error"] == fov_failed["error"]


@pytest.mark.asyncio
async def test_submit_result_terminates_structured_instruct_without_extra_llm_turn():
    calls = []

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "participant",
                "archetype": "llm",
                "persona": "Answer directly.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack()

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def fake_llm_call(payload):
        calls.append(payload)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "submit_result",
                        "arguments": '{"result": {"trust_score": 4.0}}',
                    },
                }
            ],
        }

    agent = LLMAgent("alice", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Answer directly.",
        memory=None,
        llm_call=fake_llm_call,
        actionset=ActionSet(),
    )

    result = await agent.instruct(
        "Return a trust score.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
        retrieve_memory=False,
        save_memory=False,
        max_turns=3,
        llm_request_options={
            "max_tokens": 64,
            "temperature": 0.0,
            "top_p": 0.8,
            "metadata": {"must_not": "pass"},
        },
    )

    assert result["structured_output"] == {"trust_score": 4.0}
    assert result["total_turns"] == 1
    assert calls[0]["max_tokens"] == 64
    assert calls[0]["temperature"] == 0.0
    assert calls[0]["top_p"] == 0.8
    assert calls[0]["metadata"]["agent_id"] == "alice"
    assert calls[0]["metadata"].get("must_not") is None
    assert len(calls) == 1
    assert calls[0]["tool_choice"] == {"type": "function", "function": {"name": "submit_result"}}


@pytest.mark.asyncio
async def test_forced_submit_result_turn_is_counted_in_structured_interview():
    calls = []

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "participant",
                "archetype": "llm",
                "persona": "Answer directly.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack()

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def fake_llm_call(payload):
        calls.append(payload)
        if len(calls) == 1:
            return {"role": "assistant", "content": "可信度是4分。", "tool_calls": []}
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_forced",
                    "type": "function",
                    "function": {
                        "name": "submit_result",
                        "arguments": '{"result": {"trust_score": 4.0}}',
                    },
                }
            ],
        }

    agent = LLMAgent("alice", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Answer directly.",
        memory=None,
        llm_call=fake_llm_call,
        actionset=ActionSet(),
    )

    result = await agent.interview(
        "Return a trust score.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
        retrieve_memory=False,
        save_memory=False,
        max_turns=2,
    )

    assert result["structured_output"] == {"trust_score": 4.0}
    assert result["total_turns"] == 2
    assert result["llm_calls"] == 2
    assert len(calls) == 2
    assert calls[1]["tool_choice"] == {"type": "function", "function": {"name": "submit_result"}}


@pytest.mark.asyncio
async def test_structured_interview_direct_json_fast_path_uses_one_llm_call_when_enabled():
    calls = []

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "participant",
                "archetype": "llm",
                "persona": "Answer directly.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack()

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def fake_llm_call(payload):
        calls.append(payload)
        return {
            "role": "assistant",
            "content": '{"trust_score": 4.0}',
            "tool_calls": [],
        }

    agent = LLMAgent("alice", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Answer directly.",
        memory=None,
        llm_call=fake_llm_call,
        actionset=ActionSet(),
    )

    result = await agent.interview(
        "Return a trust score.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
        retrieve_memory=False,
        save_memory=False,
        max_turns=2,
        prefer_direct_json_output=True,
    )

    assert result["structured_output"] == {"trust_score": 4.0}
    assert result["total_turns"] == 1
    assert result["llm_calls"] == 1
    assert len(calls) == 1
    assert calls[0]["tools"] is None
    assert calls[0]["tool_choice"] is None


@pytest.mark.asyncio
async def test_world_interview_defaults_to_submit_result_loop_not_direct_json(tmp_path):
    captured = []
    world = World(event_log_path=str(tmp_path / "events.jsonl"))
    world.agents_data = {
        "alice": {
            "id": "alice",
            "type": "participant",
            "archetype": "llm",
            "persona": "Answer directly.",
            "state": {},
            "properties": {},
            "reminders": [],
        }
    }

    class FakeAgent:
        async def interview(self, question, **kwargs):
            captured.append(kwargs)
            return {
                "structured_output": {"trust_score": 4.0},
                "total_turns": 1,
                "llm_calls": 1,
                "actions": [],
            }

    world.get_agent = lambda agent_id: FakeAgent()  # type: ignore[method-assign]

    await world.interview_agent(
        "alice",
        "Return a trust score.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
    )
    await world.interview_agent(
        "alice",
        "Return a trust score quickly.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
        prefer_direct_json_output=True,
    )
    world.event_logger.close()

    assert captured[0]["prefer_direct_json_output"] is False
    assert captured[1]["prefer_direct_json_output"] is True


@pytest.mark.asyncio
async def test_llm_agent_instruct_uses_configured_memory_top_k():
    retrieve_calls = []

    class FakeMemory:
        async def retrieve(self, **kwargs):
            retrieve_calls.append(kwargs)
            return ["remembered context"]

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "participant",
                "archetype": "llm",
                "persona": "Answer directly.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack()

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def fake_llm_call(payload):
        return {
            "role": "assistant",
            "content": '{"trust_score": 4.0}',
            "tool_calls": [],
        }

    agent = LLMAgent("alice", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Answer directly.",
        memory=FakeMemory(),
        llm_call=fake_llm_call,
        actionset=ActionSet(),
    )

    await agent.instruct(
        "Return a trust score.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
        retrieve_memory=True,
        save_memory=False,
        memory_top_k=2,
        prefer_direct_json_output=True,
    )

    assert retrieve_calls
    assert retrieve_calls[0]["top_k"] == 2


@pytest.mark.asyncio
async def test_structured_interview_exposes_submit_result_only_even_with_memory_actions():
    calls = []
    action_set = ActionSet()

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "participant",
                "archetype": "llm",
                "persona": "Answer directly.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack()

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def recall(query: str):
        return {"memories": ["cobalt moon"]}

    action_set.add_action(
        name="recall",
        func=recall,
        description="Recall memory",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        tags=["memory"],
    )

    async def fake_llm_call(payload):
        calls.append(payload)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "submit_result",
                        "arguments": '{"result": {"trust_score": 4.0}}',
                    },
                }
            ],
        }

    agent = LLMAgent("alice", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Answer directly.",
        memory=None,
        llm_call=fake_llm_call,
        actionset=action_set,
    )

    result = await agent.interview(
        "Return a trust score.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
        retrieve_memory=False,
        save_memory=False,
        max_turns=2,
    )

    assert result["structured_output"] == {"trust_score": 4.0}
    assert result["total_turns"] == 1
    assert len(calls) == 1
    tool_names = [tool["function"]["name"] for tool in calls[0]["tools"]]
    assert tool_names == ["submit_result"]
    assert calls[0]["tool_choice"] == {"type": "function", "function": {"name": "submit_result"}}


@pytest.mark.asyncio
async def test_default_structured_instruct_keeps_memory_actions_opt_in():
    calls = []
    action_set = ActionSet()

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "participant",
                "archetype": "llm",
                "persona": "Answer directly.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack()

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def recall(query: str):
        return {"memories": ["cobalt moon"]}

    action_set.add_action(
        name="recall",
        func=recall,
        description="Recall memory",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        tags=["memory"],
    )

    async def fake_llm_call(payload):
        calls.append(payload)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "submit_result",
                        "arguments": '{"result": {"trust_score": 4.0}}',
                    },
                }
            ],
        }

    agent = LLMAgent("alice", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Answer directly.",
        memory=None,
        llm_call=fake_llm_call,
        actionset=action_set,
    )

    result = await agent.instruct(
        "Return a trust score.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
        retrieve_memory=False,
        save_memory=False,
        max_turns=2,
    )

    assert result["structured_output"] == {"trust_score": 4.0}
    assert result["total_turns"] == 1
    assert len(calls) == 1
    assert [tool["function"]["name"] for tool in calls[0]["tools"]] == ["submit_result"]
    assert calls[0]["tool_choice"] == {"type": "function", "function": {"name": "submit_result"}}
    assert list(action_set.filter_by_tags(["memory"]).actions) == ["recall"]


@pytest.mark.asyncio
async def test_structured_instruct_awaits_framework_memory_write():
    calls = []

    class FakeMemory:
        def __init__(self):
            self.write_calls = []

        async def add_memories_batch(self, entries, *, fire_and_forget=False, trace=None):
            await asyncio.sleep(0)
            self.write_calls.append(
                {
                    "entries": entries,
                    "fire_and_forget": fire_and_forget,
                    "trace": trace,
                }
            )
            return ["mem_1"]

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "participant",
                "archetype": "llm",
                "persona": "Answer directly.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack().push_step("step_0")

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def fake_llm_call(payload):
        calls.append(payload)
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "submit_result",
                        "arguments": '{"result": {"trust_score": 4.0}}',
                    },
                }
            ],
        }

    memory = FakeMemory()
    agent = LLMAgent("alice", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Answer directly.",
        memory=memory,
        llm_call=fake_llm_call,
        actionset=ActionSet(),
    )

    result = await agent.instruct(
        "Remember this and return a trust score.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
        retrieve_memory=False,
        save_memory=True,
        extract_memory=False,
        max_turns=3,
        trace={
            "step": 0,
            "step_name": "seed",
            "interaction_type": "instruct",
            "interaction_name": "seed_round",
        },
    )

    assert result["structured_output"] == {"trust_score": 4.0}
    assert len(memory.write_calls) == 1
    assert memory.write_calls[0]["fire_and_forget"] is False
    assert memory.write_calls[0]["trace"]["interaction_type"] == "memory_write"
    assert memory.write_calls[0]["trace"]["parent_interaction_type"] == "instruct"
    assert memory.write_calls[0]["trace"]["interaction_name"] == "seed_round"


@pytest.mark.asyncio
async def test_extractive_memory_llm_call_is_traced_as_memory_extract():
    calls = []

    class FakeMemory:
        def __init__(self):
            self.write_calls = []

        async def add_memories_batch(self, entries, *, fire_and_forget=False, trace=None):
            self.write_calls.append({"entries": entries, "trace": trace})
            return ["mem_1"]

    class FakeWorld:
        agents_data = {
            "alice": {
                "id": "alice",
                "type": "participant",
                "archetype": "llm",
                "persona": "Answer directly.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack().push_step("step_0")

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def fake_llm_call(payload):
        calls.append(payload)
        tool_names = [tool["function"]["name"] for tool in payload.get("tools", [])]
        if "extract_memories" in tool_names:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "extract_1",
                        "type": "function",
                        "function": {
                            "name": "extract_memories",
                            "arguments": '{"memories": [{"content": "我记住了 cobalt moon。", "importance": 4}]}',
                        },
                    }
                ],
            }
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "submit_1",
                    "type": "function",
                    "function": {
                        "name": "submit_result",
                        "arguments": '{"result": {"trust_score": 4.0}}',
                    },
                }
            ],
        }

    memory = FakeMemory()
    agent = LLMAgent("alice", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Answer directly.",
        memory=memory,
        llm_call=fake_llm_call,
        actionset=ActionSet(),
    )

    result = await agent.instruct(
        "Remember cobalt moon and return a trust score.",
        output_schema={
            "type": "object",
            "properties": {"trust_score": {"type": "number"}},
            "required": ["trust_score"],
            "additionalProperties": False,
        },
        retrieve_memory=False,
        save_memory=True,
        extract_memory=True,
        max_turns=3,
        trace={
            "step": 0,
            "step_name": "seed",
            "interaction_type": "instruct",
            "interaction_name": "seed_round",
        },
    )

    assert result["memory_extraction_enabled"] is True
    assert result["memory_extraction_success"] is True
    assert len(calls) == 2
    assert calls[0]["metadata"]["interaction_type"] == "instruct"
    assert calls[0]["metadata"]["interaction_name"] == "seed_round"
    assert calls[1]["metadata"]["interaction_type"] == "memory_extract"
    assert calls[1]["metadata"]["interaction_name"] == "memory_extract"
    assert memory.write_calls[0]["trace"]["interaction_type"] == "memory_write"
    assert memory.write_calls[0]["entries"][0]["metadata"]["extraction_method"] == "structured_extract"


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

    await engine.run(steps=2)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")
    assert metrics[0]["metrics"] == {
        "pressure": 0.7,
        "behavior_success": 2,
        "ctx_behavior_success": 1,
    }
    assert metrics[1]["step"] == 1
    assert metrics[1]["metrics"] == metrics[0]["metrics"]
    final_checkpoint = json.loads((tmp_path / "checkpoints" / "checkpoint_final.json").read_text(encoding="utf-8"))
    assert final_checkpoint["agents_data"]["alice"]["state"]["trust"] == 0.6
    assert final_checkpoint["agents_data"]["bob"]["state"]["trust"] == 1.0
    assert final_checkpoint["agents_data"]["carol"]["state"]["trust"] == 0.6
    events = _read_jsonl(tmp_path / "events.jsonl")
    logic_events = [event for event in events if event.get("event_type", "").startswith("logic_execution_")]
    assert [event["event_type"] for event in logic_events[:6]] == [
        "logic_execution_started",
        "logic_execution_completed",
        "logic_execution_started",
        "logic_execution_completed",
        "logic_execution_started",
        "logic_execution_completed",
    ]
    assert len(logic_events) == 12
    assert logic_events[0]["event_data"]["logic_kind"] == "rule"
    assert logic_events[0]["event_data"]["logic_name"] == "set_pressure"
    assert logic_events[0]["event_data"]["param_keys"] == ["amount"]
    assert logic_events[2]["event_data"]["logic_kind"] == "behavior"
    assert logic_events[2]["event_data"]["logic_name"] == "adjust_trust"
    assert logic_events[2]["event_data"]["agent_count"] == 2
    assert logic_events[2]["event_data"]["concurrency"] == 1
    assert logic_events[2]["event_data"]["param_keys"] == ["delta"]
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    logic_summary = summary["events"]["logic_executions"]
    assert logic_summary["rule / set_pressure"]["started_count"] == 2
    assert logic_summary["rule / set_pressure"]["completed_count"] == 2
    assert logic_summary["rule / set_pressure"]["success_count"] == 2
    assert logic_summary["rule / set_pressure"]["param_keys"] == ["amount"]
    assert logic_summary["rule / set_pressure"]["by_tick"]["0"]["completed_count"] == 1
    assert logic_summary["rule / set_pressure"]["by_tick"]["0"]["success_count"] == 1
    assert logic_summary["rule / set_pressure"]["by_tick"]["1"]["completed_count"] == 1
    assert logic_summary["rule / set_pressure"]["by_tick"]["1"]["success_count"] == 1
    assert logic_summary["behavior / adjust_trust"]["started_count"] == 4
    assert logic_summary["behavior / adjust_trust"]["completed_count"] == 4
    assert logic_summary["behavior / adjust_trust"]["success_count"] == 6
    assert logic_summary["behavior / adjust_trust"]["error_count"] == 0
    assert logic_summary["behavior / adjust_trust"]["agent_count_total"] == 6
    assert logic_summary["behavior / adjust_trust"]["param_keys"] == ["delta"]
    assert logic_summary["behavior / adjust_trust"]["by_tick"]["0"]["started_count"] == 2
    assert logic_summary["behavior / adjust_trust"]["by_tick"]["0"]["completed_count"] == 2
    assert logic_summary["behavior / adjust_trust"]["by_tick"]["0"]["success_count"] == 3
    assert logic_summary["behavior / adjust_trust"]["by_tick"]["0"]["agent_count_total"] == 3
    assert logic_summary["behavior / adjust_trust"]["by_tick"]["1"]["started_count"] == 2
    assert logic_summary["behavior / adjust_trust"]["by_tick"]["1"]["completed_count"] == 2
    assert logic_summary["behavior / adjust_trust"]["by_tick"]["1"]["success_count"] == 3
    assert logic_summary["behavior / adjust_trust"]["by_tick"]["1"]["agent_count_total"] == 3


@pytest.mark.asyncio
async def test_behavior_error_samples_are_summarized_without_failing_run(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())

    @engine.registry.sched.behavior(name="maybe_fail")
    async def maybe_fail(agent, env):
        if agent.id == "bob":
            raise RuntimeError("bob rejected the deterministic behavior")
        agent.state["checked"] = True
        return {"checked": True}

    @engine.step(name="behavior_errors")
    async def behavior_errors(ctx):
        result = await ctx.agents.where(type="social_user").behavior("maybe_fail", concurrency=2)
        return ctx.result(
            metrics={"success": result.success_count, "errors": result.error_count},
            tables={"behavior": result.table()},
        )

    await engine.run(steps=1)

    metrics = _read_jsonl(tmp_path / "metrics.jsonl")[0]["metrics"]
    assert metrics == {"success": 1, "errors": 1}
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    execution = summary["events"]["logic_executions"]["behavior / maybe_fail"]
    assert execution["completed_count"] == 1
    assert execution["failed_count"] == 0
    assert execution["success_count"] == 1
    assert execution["error_count"] == 1
    assert execution["agent_count_total"] == 2
    assert execution["error_samples"] == [
        {
            "agent_id": "bob",
            "status": "error",
            "error": "bob rejected the deterministic behavior",
        }
    ]


@pytest.mark.asyncio
async def test_rule_failure_is_summarized_with_logic_error_sample(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())

    @engine.registry.env.rule(name="explode_rule")
    async def explode_rule(env, severity: str):
        raise RuntimeError(f"rule failed at {severity}")

    @engine.step(name="failing_rule")
    async def failing_rule(ctx):
        await ctx.rule("explode_rule", severity="high")
        return ctx.result()

    with pytest.raises(RuntimeError, match="rule failed at high"):
        await engine.run(steps=1)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert summary["failed"] is True
    execution = summary["events"]["logic_executions"]["rule / explode_rule"]
    assert execution["started_count"] == 1
    assert execution["completed_count"] == 0
    assert execution["failed_count"] == 1
    assert execution["param_keys"] == ["severity"]
    assert execution["error_samples"] == [
        {
            "error": "rule failed at high",
            "error_type": "RuntimeError",
        }
    ]
    assert summary["events"]["error_samples"][0]["logic_kind"] == "rule"
    assert summary["events"]["error_samples"][0]["logic_name"] == "explode_rule"


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
        assert ctx.capabilities.has("rule", "set_pressure", source="experiment")
        assert not ctx.capabilities.has("rule", "set_pressure", source="environment")
        assert ctx.capabilities.has("behavior", "adjust_trust")
        assert "set_pressure" in ctx.capabilities.names("rule")
        assert ctx.capabilities.names("rule", source="experiment") == ["set_pressure"]
        assert ctx.capabilities.names("rule", source="environment") == []
        assert "adjust_trust" in ctx.capabilities.names("behavior")
        experiment_capabilities = ctx.capabilities.by_source("experiment")
        assert "set_pressure" in {entry["name"] for entry in experiment_capabilities["rules"]}
        assert "adjust_trust" in {entry["name"] for entry in experiment_capabilities["behaviors"]}
        assert ctx.capabilities.by_source("environment", kind="rule") == []
        with pytest.raises(ValueError, match="Rule 'missing_rule' not found"):
            await ctx.rule("missing_rule")
        with pytest.raises(ValueError, match="Behavior 'missing_behavior' not found"):
            await ctx.agents.all().behavior("missing_behavior")
        return ctx.result(metrics={"rules": len(ctx.capabilities.rules())})

    await engine.run(steps=1)

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    capabilities = summary["capabilities"]
    assert capabilities["environment_type"] == "plain"
    assert capabilities["counts"]["rules"] >= 1
    assert capabilities["counts"]["behaviors"] >= 1
    assert capabilities["by_source"]["experiment"]["rules"] >= 1
    assert capabilities["by_source"]["experiment"]["behaviors"] >= 1
    rule_names = {entry["name"] for entry in capabilities["by_kind"]["rules"]}
    behavior_names = {entry["name"] for entry in capabilities["by_kind"]["behaviors"]}
    assert "set_pressure" in rule_names
    assert "adjust_trust" in behavior_names


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
async def test_embedding_microbatch_flushes_same_bucket_batches_in_parallel(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MICROBATCH_MAX_TEXTS", "2")
    monkeypatch.setenv("EMBEDDING_MICROBATCH_MAX_WAIT_MS", "5")

    manager = EmbeddingManager(
        [
            {
                "id": "default_embed",
                "api_key": "test",
                "base_url": "http://localhost:9999/v1",
                "model": "embed-test",
                "concurrency": 3,
                "provider_type": "openai",
            }
        ]
    )
    in_flight = 0
    max_in_flight = 0
    physical_calls = 0

    async def fake_execute_request(endpoint, texts, dimensions, metadata=None):
        nonlocal in_flight, max_in_flight, physical_calls
        async with manager.semaphores[endpoint.id]:
            physical_calls += 1
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                await asyncio.sleep(0.03)
                return {
                    "result": [[float(len(text)), float(index)] for index, text in enumerate(texts)],
                    "model": endpoint.model,
                    "dimensions": dimensions,
                }
            finally:
                in_flight -= 1

    manager._execute_request = fake_execute_request  # type: ignore[method-assign]

    try:
        results = await asyncio.gather(
            *(manager.request([f"unique embedding text {idx}"], dimensions=2) for idx in range(8))
        )
        stats = manager.get_stats()
    finally:
        await manager.close()

    assert len(results) == 8
    assert all(len(result["result"]) == 1 for result in results)
    assert physical_calls == 4
    assert max_in_flight > 1
    assert max_in_flight <= 3
    assert stats["microbatch"]["batches"] == 4
    assert stats["microbatch"]["max_parallel_flushes"] == 3
    assert stats["cache"]["current_items"] == 8


@pytest.mark.asyncio
async def test_embedding_microbatch_preserves_plural_trace_metadata(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MICROBATCH_MAX_TEXTS", "10")
    monkeypatch.setenv("EMBEDDING_MICROBATCH_MAX_WAIT_MS", "1")

    manager = EmbeddingManager(
        [
            {
                "id": "default_embed",
                "api_key": "test",
                "base_url": "http://localhost:9999/v1",
                "model": "embed-test",
                "concurrency": 1,
                "provider_type": "openai",
            }
        ]
    )
    metadata_seen = []

    async def fake_execute_request(endpoint, texts, dimensions, metadata=None):
        metadata_seen.append(dict(metadata or {}))
        return {
            "result": [[float(index), 0.0] for index, _ in enumerate(texts)],
            "model": endpoint.model,
            "dimensions": dimensions,
        }

    manager._execute_request = fake_execute_request  # type: ignore[method-assign]

    try:
        result = await manager.request(
            ["post one", "post two"],
            dimensions=2,
            metadata={
                "step": 0,
                "step_name": "publish_once",
                "interaction_type": "env_post_embedding",
                "interaction_name": "publish_post",
                "agent_ids": ["alice", "bob"],
                "post_ids": ["post_1", "post_2"],
            },
        )
    finally:
        await manager.close()

    assert len(result["result"]) == 2
    assert metadata_seen == [
        {
            "step": 0,
            "step_name": "publish_once",
            "step_names": ["publish_once"],
            "interaction_type": "env_post_embedding",
            "interaction_types": ["env_post_embedding"],
            "interaction_name": "publish_post",
            "interaction_names": ["publish_post"],
            "agent_ids": ["alice", "bob"],
            "post_ids": ["post_1", "post_2"],
        }
    ]


@pytest.mark.asyncio
async def test_society0_injects_model_concurrency_into_runtime(tmp_path):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_base_config(),
        llm=LLMModel.openai_compatible(
            id="default",
            model="gpt-test",
            base_url="https://private-llm.example.test/v1",
            api_key="llm-secret-token",
            concurrency=7,
            timeout=45,
        ),
        embed=EmbedModel.openai_compatible(
            id="embed",
            model="embed-test",
            base_url="https://private-embed.example.test/v1",
            api_key="embed-secret-token",
            concurrency=3,
            dimensions=1024,
            timeout=60,
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
    assert summary["models"]["llm"] == {
        "id": "default",
        "model": "gpt-test",
        "provider_type": "openai",
        "concurrency": 7,
        "timeout": 45,
    }
    assert summary["models"]["embedding"] == {
        "id": "embed",
        "model": "embed-test",
        "provider_type": "openai",
        "concurrency": 3,
        "timeout": 60,
        "dimensions": 1024,
    }
    summary_text = json.dumps(summary, ensure_ascii=False)
    assert "private-llm.example.test" not in summary_text
    assert "private-embed.example.test" not in summary_text
    assert "llm-secret-token" not in summary_text
    assert "embed-secret-token" not in summary_text
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


def test_society0_resource_summary_aggregates_resource_calls(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())
    calls_path = tmp_path / "resource_calls.jsonl"
    calls_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "resource_type": "llm",
                        "status": "started",
                        "step": 0,
                        "duration_sec": None,
                        "step_name": "survey",
                        "interaction_type": "interview",
                        "interaction_name": "trust",
                        "agent_id": "alice",
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "llm",
                        "status": "success",
                        "step": 0,
                        "duration_sec": 1.25,
                        "queue_duration_sec": 0.2,
                        "provider_duration_sec": 1.0,
                        "step_name": "survey",
                        "interaction_type": "interview",
                        "interaction_name": "trust",
                        "agent_id": "alice",
                        "input_characters": 700,
                        "messages_count": 2,
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 14,
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "llm",
                        "status": "error",
                        "step": 1,
                        "duration_sec": 2.0,
                        "queue_duration_sec": 0.5,
                        "provider_duration_sec": 1.4,
                        "step_name": "survey",
                        "interaction_type": "interview",
                        "interaction_name": "trust",
                        "agent_id": "bob",
                        "input_characters": 900,
                        "messages_count": 4,
                        "prompt_tokens": 3,
                        "error_type": "TimeoutError",
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "embedding",
                        "status": "success",
                        "step": 0,
                        "duration_sec": 0.5,
                        "queue_duration_sec": 0.1,
                        "provider_duration_sec": 0.35,
                        "step_names": ["seed", "recall"],
                        "interaction_types": ["memory_write", "memory_retrieve"],
                        "interaction_names": ["seed_round", "recall_round"],
                        "agent_ids": ["alice", "bob"],
                        "input_characters": 1200,
                        "texts_count": 6,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = engine._summarize_resource_calls()

    summary_keys = {
        "started_count",
        "call_count",
        "terminal_count",
        "incomplete_count",
        "error_count",
        "duration_sec_total",
        "duration_sec_max",
        "duration_sec_avg",
        "duration_sec_p50",
        "duration_sec_p90",
        "duration_sec_p99",
        "queue_duration_sec_total",
        "queue_duration_sec_max",
        "queue_duration_sec_avg",
        "provider_duration_sec_total",
        "provider_duration_sec_max",
        "provider_duration_sec_avg",
        "input_characters",
        "total_input_characters",
        "messages_count_total",
        "messages_count_max",
        "messages_count_avg",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "texts_count",
        "total_duration_sec",
        "total_provider_duration_sec",
        "total_queue_duration_sec",
        "slowest_calls",
        "by_interaction",
        "by_interaction_type",
        "fidelity",
        "by_tick",
        "error_samples",
    }
    assert summary_keys.issubset(summary["llm"].keys())
    assert {
        key: summary["llm"][key]
        for key in (
            "started_count",
            "call_count",
            "terminal_count",
            "incomplete_count",
            "error_count",
            "duration_sec_total",
            "duration_sec_max",
            "duration_sec_avg",
            "duration_sec_p50",
            "duration_sec_p90",
            "duration_sec_p99",
            "queue_duration_sec_total",
            "queue_duration_sec_max",
            "queue_duration_sec_avg",
            "provider_duration_sec_total",
            "provider_duration_sec_max",
            "provider_duration_sec_avg",
            "input_characters",
            "total_input_characters",
            "messages_count_total",
            "messages_count_max",
            "messages_count_avg",
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "texts_count",
            "total_duration_sec",
            "total_provider_duration_sec",
            "total_queue_duration_sec",
        )
    } == {
        "started_count": 1,
        "call_count": 2,
        "terminal_count": 2,
        "incomplete_count": 0,
        "error_count": 1,
        "duration_sec_total": 3.25,
        "duration_sec_max": 2.0,
        "duration_sec_avg": 1.625,
        "duration_sec_p50": 1.25,
        "duration_sec_p90": 2.0,
        "duration_sec_p99": 2.0,
        "queue_duration_sec_total": 0.7,
        "queue_duration_sec_max": 0.5,
        "queue_duration_sec_avg": 0.35,
        "provider_duration_sec_total": 2.4,
        "provider_duration_sec_max": 1.4,
        "provider_duration_sec_avg": 1.2,
        "input_characters": 1600,
        "total_input_characters": 1600,
        "messages_count_total": 6,
        "messages_count_max": 4,
        "messages_count_avg": 3.0,
        "prompt_tokens": 13,
        "completion_tokens": 4,
        "total_tokens": 14,
        "texts_count": 0,
        "total_duration_sec": 3.25,
        "total_provider_duration_sec": 2.4,
        "total_queue_duration_sec": 0.7,
    }
    assert summary["llm"]["slowest_calls"][0]["agent_id"] == "bob"
    assert summary["llm"]["slowest_calls"][0]["error_type"] == "TimeoutError"
    assert summary["llm"]["error_samples"][0]["agent_id"] == "bob"
    assert summary["llm"]["by_tick"]["0"]["started_count"] == 1
    assert summary["llm"]["by_tick"]["0"]["call_count"] == 1
    assert summary["llm"]["by_tick"]["0"]["total_tokens"] == 14
    assert summary["llm"]["by_tick"]["0"]["input_characters"] == 700
    assert summary["llm"]["by_tick"]["0"]["messages_count_avg"] == 2.0
    assert summary["llm"]["by_tick"]["0"]["total_input_characters"] == 700
    assert summary["llm"]["by_tick"]["1"]["call_count"] == 1
    assert summary["llm"]["by_tick"]["1"]["error_count"] == 1
    assert summary["llm"]["by_interaction"]["survey / interview / trust"]["call_count"] == 2
    assert summary["llm"]["by_interaction"]["survey / interview / trust"]["error_count"] == 1
    assert summary["llm"]["by_interaction"]["survey / interview / trust"]["queue_duration_sec_total"] == 0.7
    assert summary["llm"]["by_interaction"]["survey / interview / trust"]["provider_duration_sec_total"] == 2.4
    assert summary["llm"]["by_interaction"]["survey / interview / trust"]["input_characters"] == 1600
    assert summary["llm"]["by_interaction"]["survey / interview / trust"]["messages_count_max"] == 4
    assert summary["llm"]["by_interaction"]["survey / interview / trust"]["total_duration_sec"] == 3.25
    assert summary["llm"]["by_interaction_type"]["interview"]["call_count"] == 2
    assert summary["llm"]["by_interaction_type"]["interview"]["error_count"] == 1
    assert summary["llm"]["by_interaction_type"]["interview"]["total_tokens"] == 14
    assert summary["llm"]["fidelity"]["agent_loop"]["call_count"] == 2
    assert summary["llm"]["fidelity"]["agent_loop"]["error_count"] == 1
    assert summary["llm"]["fidelity"]["agent_loop"]["duration_sec_total"] == 3.25
    assert summary["embedding"]["call_count"] == 1
    assert summary["embedding"]["started_count"] == 0
    assert summary["embedding"]["terminal_count"] == 1
    assert summary["embedding"]["incomplete_count"] == 0
    assert summary["embedding"]["texts_count"] == 6
    assert summary["embedding"]["input_characters"] == 1200
    assert summary["embedding"]["total_input_characters"] == 1200
    assert summary["embedding"]["queue_duration_sec_total"] == 0.1
    assert summary["embedding"]["provider_duration_sec_total"] == 0.35
    assert summary["embedding"]["by_tick"]["0"]["texts_count"] == 6
    embedding_key = "seed,recall / memory_write,memory_retrieve / seed_round,recall_round"
    assert summary["embedding"]["by_interaction"][embedding_key]["texts_count"] == 6
    assert summary["embedding"]["by_interaction_type"]["memory_write"]["call_count"] == 1
    assert summary["embedding"]["by_interaction_type"]["memory_retrieve"]["call_count"] == 1
    assert summary["embedding"]["fidelity"]["memory_io"]["call_count"] == 1
    assert summary["embedding"]["fidelity"]["memory_io"]["texts_count"] == 6


def test_society0_resource_summary_exposes_fidelity_diagnostics(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_base_config())
    (tmp_path / "resource_calls.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "resource_type": "llm",
                        "status": "success",
                        "step": 0,
                        "step_name": "browse",
                        "interaction_type": "instruct",
                        "interaction_name": "feed",
                        "duration_sec": 3.0,
                        "provider_duration_sec": 2.6,
                        "queue_duration_sec": 0.2,
                        "tools_count": 4,
                        "tools_characters": 1200,
                        "input_characters": 5000,
                        "total_tokens": 900,
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "llm",
                        "status": "success",
                        "step": 0,
                        "step_name": "browse",
                        "interaction_type": "memory_extract",
                        "interaction_name": "memory_extract",
                        "duration_sec": 1.1,
                        "provider_duration_sec": 1.0,
                        "queue_duration_sec": 0.05,
                        "input_characters": 900,
                        "total_tokens": 150,
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "embedding",
                        "status": "success",
                        "step": 0,
                        "step_name": "browse",
                        "interaction_type": "memory_write",
                        "interaction_name": "feed",
                        "duration_sec": 0.4,
                        "provider_duration_sec": 0.35,
                        "queue_duration_sec": 0.02,
                        "input_characters": 300,
                        "texts_count": 1,
                    }
                ),
                json.dumps(
                    {
                        "resource_type": "embedding",
                        "status": "success",
                        "step": 0,
                        "step_name": "browse",
                        "interaction_type": "semantic_recommendation",
                        "interaction_name": "recommended_feed",
                        "duration_sec": 0.6,
                        "provider_duration_sec": 0.55,
                        "queue_duration_sec": 0.03,
                        "input_characters": 600,
                        "texts_count": 2,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = engine._summarize_resource_calls()

    assert summary["llm"]["by_interaction_type"]["instruct"]["call_count"] == 1
    assert summary["llm"]["by_interaction_type"]["memory_extract"]["call_count"] == 1
    assert summary["llm"]["fidelity"]["agent_loop"]["call_count"] == 1
    assert summary["llm"]["fidelity"]["agent_loop"]["tools_count_total"] == 4
    assert summary["llm"]["fidelity"]["agent_loop"]["tools_characters"] == 1200
    assert summary["llm"]["fidelity"]["agent_loop"]["total_tokens"] == 900
    assert summary["llm"]["fidelity"]["memory_extraction"]["call_count"] == 1
    assert summary["llm"]["fidelity"]["memory_extraction"]["total_tokens"] == 150
    assert summary["embedding"]["fidelity"]["memory_io"]["call_count"] == 1
    assert summary["embedding"]["fidelity"]["memory_io"]["texts_count"] == 1
    assert summary["embedding"]["fidelity"]["environment"]["call_count"] == 1
    assert summary["embedding"]["fidelity"]["environment"]["texts_count"] == 2


@pytest.mark.asyncio
async def test_memory_embedding_calls_include_trace_metadata():
    captured_calls = []

    class FakeCollection:
        def __init__(self):
            self.ids = []
            self.documents = []
            self.metadatas = []

        def add(self, *, ids, documents, embeddings, metadatas):
            self.ids.extend(ids)
            self.documents.extend(documents)
            self.metadatas.extend(metadatas)

        def count(self):
            return len(self.ids)

        def query(self, *, query_embeddings, n_results, include, where):
            return {
                "ids": [self.ids[:n_results]],
                "documents": [self.documents[:n_results]],
                "metadatas": [self.metadatas[:n_results]],
                "distances": [[0.1 for _ in self.ids[:n_results]]],
            }

    class FakeVectorClient:
        def __init__(self):
            self.collection = FakeCollection()

        def get_or_create_collection(self, **kwargs):
            return self.collection

    async def fake_embed(texts, dimensions, metadata=None):
        captured_calls.append({"texts": list(texts), "dimensions": dimensions, "metadata": dict(metadata or {})})
        return {"result": [[1.0, 0.0, 0.0] for _ in texts], "dimensions": dimensions, "model": "fake"}

    memory = Memory(
        "alice",
        FakeVectorClient(),
        embed_call=fake_embed,
        embedding_dim=3,
    )

    await memory.add_episodic_memory(
        "cobalt moon",
        timestamp=0,
        importance=3.0,
        trace={
            "step": 4,
            "step_name": "seed_memory",
            "interaction_type": "memory_write",
            "interaction_name": "seed_round",
        },
    )
    recalled = await memory.retrieve(
        "what signal?",
        current_step=4,
        trace={
            "step": 4,
            "step_name": "recall_memory",
            "interaction_type": "memory_retrieve",
            "interaction_name": "recall_round",
        },
    )

    assert recalled == ["cobalt moon"]
    assert captured_calls[0]["metadata"] == {
        "agent_id": "alice",
        "step": 4,
        "step_name": "seed_memory",
        "interaction_type": "memory_write",
        "interaction_name": "seed_round",
    }
    assert captured_calls[1]["metadata"] == {
        "agent_id": "alice",
        "step": 4,
        "step_name": "recall_memory",
        "interaction_type": "memory_retrieve",
        "interaction_name": "recall_round",
    }


@pytest.mark.asyncio
async def test_memory_retrieve_skips_embedding_when_collection_is_empty():
    embed_calls = []

    class EmptyCollection:
        def count(self):
            return 0

    class FakeVectorClient:
        def get_or_create_collection(self, **kwargs):
            return EmptyCollection()

    async def fake_embed(texts, dimensions, metadata=None):
        embed_calls.append(list(texts))
        return {"result": [[1.0, 0.0, 0.0] for _ in texts], "dimensions": dimensions}

    memory = Memory("alice", FakeVectorClient(), embed_call=fake_embed, embedding_dim=3)

    recalled = await memory.retrieve("anything", current_step=0)

    assert recalled == []
    assert embed_calls == []


@pytest.mark.asyncio
async def test_memory_retrieve_skips_embedding_when_current_agent_has_no_memories():
    embed_calls = []

    class SharedCollection:
        def count(self):
            return 1

        def get(self, *, where, limit):
            assert where == {
                "$and": [
                    {"agent_id": {"$eq": "alice"}},
                    {"branch_id": {"$eq": "main"}},
                ]
            }
            assert limit == 1
            return {"ids": []}

    class FakeVectorClient:
        def get_or_create_collection(self, **kwargs):
            return SharedCollection()

    async def fake_embed(texts, dimensions, metadata=None):
        embed_calls.append(list(texts))
        return {"result": [[1.0, 0.0, 0.0] for _ in texts], "dimensions": dimensions}

    memory = Memory("alice", FakeVectorClient(), embed_call=fake_embed, embedding_dim=3)

    recalled = await memory.retrieve("anything", current_step=0)

    assert recalled == []
    assert embed_calls == []


@pytest.mark.asyncio
async def test_llm_manager_enforces_hard_timeout_and_logs_failure(tmp_path):
    class SlowCompletions:
        async def create(self, **kwargs):
            await asyncio.sleep(0.05)
            raise AssertionError("slow fake client should be cancelled by wait_for")

    class SlowChat:
        completions = SlowCompletions()

    class SlowClient:
        chat = SlowChat()

    log_context = ExperimentLogContext(tmp_path / "logs")
    manager = LLMManager(
        [
            {
                "id": "default",
                "api_key": "test",
                "base_url": "http://localhost:9999/v1",
                "model": "gpt-test",
                "concurrency": 1,
                "timeout": 0.01,
            }
        ],
        log_context=log_context,
    )
    manager.clients["default"] = SlowClient()
    manager._max_retries = 1

    try:
        with pytest.raises(asyncio.TimeoutError):
            await manager.request(
                {
                    "messages": [{"role": "user", "content": "time out"}],
                    "metadata": {"step": 3, "step_name": "timeout_probe"},
                }
            )
    finally:
        await manager.close()
        log_context.close()

    llm_events = _read_jsonl(tmp_path / "logs" / "resources" / "llm.jsonl")
    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")

    assert [event["event"] for event in llm_events] == [
        "llm_request_started",
        "llm_request_failed",
    ]
    assert llm_events[-1]["error_type"] == "TimeoutError"
    assert llm_events[-1]["error"]
    assert llm_events[-1]["step_name"] == "timeout_probe"
    assert resource_calls[0]["resource_type"] == "llm"
    assert resource_calls[0]["status"] == "started"
    assert resource_calls[0]["step_name"] == "timeout_probe"
    assert resource_calls[-1]["resource_type"] == "llm"
    assert resource_calls[-1]["status"] == "failed"
    assert resource_calls[-1]["error_type"] == "TimeoutError"
    assert resource_calls[-1]["error_preview"]


@pytest.mark.asyncio
async def test_llm_manager_logs_tool_and_payload_size_for_generation_options(tmp_path):
    captured_payloads = []

    class FakeUsage:
        prompt_tokens = 12
        completion_tokens = 4
        total_tokens = 16

    class FakeFunction:
        name = "publish_post"
        arguments = '{"content": "hello"}'

    class FakeToolCall:
        id = "call_1"
        type = "function"
        function = FakeFunction()

    class FakeMessage:
        role = "assistant"
        content = ""
        tool_calls = [FakeToolCall()]

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()

    class FakeCompletions:
        async def create(self, **kwargs):
            captured_payloads.append(kwargs)
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    log_context = ExperimentLogContext(tmp_path / "logs")
    manager = LLMManager(
        [
            {
                "id": "default",
                "api_key": "test",
                "base_url": "http://localhost:9999/v1",
                "model": "gpt-test",
                "concurrency": 1,
                "timeout": 30,
            }
        ],
        log_context=log_context,
    )
    manager.clients["default"] = FakeClient()

    try:
        result = await manager.request(
            {
                "messages": [{"role": "user", "content": "publish now"}],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "publish_post",
                            "description": "Publish a short social post.",
                            "parameters": {
                                "type": "object",
                                "properties": {"content": {"type": "string"}},
                                "required": ["content"],
                            },
                        },
                    }
                ],
                "tool_choice": "auto",
                "max_tokens": 80,
                "temperature": 0.2,
                "top_p": 0.9,
                "metadata": {
                    "agent_id": "alice",
                    "step": 2,
                    "step_name": "publish",
                    "interaction_type": "instruct",
                    "interaction_name": "round",
                },
            }
        )
    finally:
        await manager.close()
        log_context.close()

    assert result["tool_calls"][0]["function"]["name"] == "publish_post"
    assert captured_payloads[0]["max_tokens"] == 80
    assert captured_payloads[0]["temperature"] == 0.2
    assert captured_payloads[0]["top_p"] == 0.9
    assert "metadata" not in captured_payloads[0]
    assert captured_payloads[0]["model"] == "gpt-test"

    resource_calls = _read_jsonl(tmp_path / "resource_calls.jsonl")
    success_record = next(record for record in resource_calls if record.get("status") == "success")
    assert success_record["messages_count"] == 1
    assert success_record["input_characters"] == len("publish now")
    assert success_record["tools_count"] == 1
    assert success_record["tools_characters"] > 100
    assert success_record["payload_characters"] > success_record["tools_characters"]
    assert success_record["max_tokens"] == 80
    assert success_record["temperature"] == 0.2
    assert success_record["top_p"] == 0.9

    summary = Society0(save_dir=str(tmp_path), base_config=_base_config())._summarize_resource_calls()
    assert summary["llm"]["tools_count_total"] == 1
    assert summary["llm"]["tools_count_max"] == 1
    assert summary["llm"]["tools_count_avg"] == 1.0
    assert summary["llm"]["tools_characters"] == success_record["tools_characters"]
    assert summary["llm"]["payload_characters"] == success_record["payload_characters"]
    assert summary["llm"]["total_tools_characters"] == success_record["tools_characters"]
    assert summary["llm"]["total_payload_characters"] == success_record["payload_characters"]
    assert summary["llm"]["slowest_calls"][0]["payload_characters"] == success_record["payload_characters"]


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
