import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from society0.agent.thread_store import AgentThreadStore
from society0 import Society0
from society0.logging import ExperimentLogContext
from society0.resource_managers import LLMManager


pytestmark = pytest.mark.primary


def _read_json(path: Path) -> dict:
    if path.name.endswith(".json.gz"):
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def _rule_config() -> dict:
    return {
        "agent_types": [{"id": "enterprise", "archetype": "rule"}],
        "agents": [{"id": "enterprise-a", "type": "enterprise", "state": {}}],
        "environment": {"type": "plain", "state": {}},
    }


def test_agent_thread_store_hash_chain_cursor_and_large_blob(tmp_path):
    store = AgentThreadStore(tmp_path, inline_payload_max_bytes=64)
    thread_id = store.open_thread(
        agent_id="enterprise-a",
        checkpoint_step=7,
        scope={"kind": "industry_tick", "id": "7", "tick": 7},
        metadata={"simulation_date": "2026-08-09"},
    )

    request = store.append_event(
        thread_id,
        "provider_request",
        payload={
            "messages": [
                {"role": "system", "content": "你是企业经营者"},
                {"role": "user", "content": "请处理本轮经营任务"},
            ],
            "tools": [{"type": "function", "function": {"name": "act"}}],
            "tool_choice": {"type": "function", "function": {"name": "act"}},
        },
        interaction_id="activation-1",
        interaction_type="instruct",
        turn_id="turn-1",
    )
    response = store.append_event(
        thread_id,
        "provider_response",
        payload={
            "content": "",
            "reasoning_content": "当前需要调整库存。",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {"name": "act", "arguments": '{"quantity":5}'},
                }
            ],
            "provider_raw": {"large": "x" * 512},
        },
        interaction_id="activation-1",
        interaction_type="instruct",
        turn_id="turn-1",
    )
    reference = store.close_thread(thread_id, metadata={"memory_status": "empty"})

    assert request["sequence"] == 2
    assert response["sequence"] == 3
    assert response["previous_event_sha256"] == request["event_sha256"]
    assert response["payload"] is None
    assert response["payload_ref"]["sha256"] == response["payload_sha256"]
    blob_path = tmp_path / response["payload_ref"]["path"]
    assert json.loads(blob_path.read_text(encoding="utf-8"))["provider_raw"]["large"] == "x" * 512
    assert reference["closed"] is True
    assert reference["cursor"]["sequence"] == 4
    assert reference["cursor"]["byte_offset"] == (tmp_path / reference["path"]).stat().st_size
    assert store.get_thread_reference(thread_id, require_closed=True) == reference

    events = store.read_events(thread_id, materialize_payloads=True)
    assert [event["event_type"] for event in events] == [
        "thread_opened",
        "provider_request",
        "provider_response",
        "thread_closed",
    ]
    assert events[2]["payload"]["reasoning_content"] == "当前需要调整库存。"


def test_checkpoint_manifest_names_agents_and_rejects_tampering(tmp_path):
    store = AgentThreadStore(tmp_path)
    thread_id = store.open_thread(
        agent_id="enterprise-a",
        checkpoint_step=3,
        scope={"kind": "industry_tick", "id": "3", "tick": 3},
    )
    store.append_event(thread_id, "provider_request", payload={"messages": []})
    reference = store.close_thread(thread_id)

    published = store.publish_checkpoint_manifest(
        checkpoint_id="checkpoint-3",
        step=3,
    )
    manifest_path = tmp_path / published["path"]
    manifest = _read_json(manifest_path)

    assert manifest["checkpoint_id"] == "checkpoint-3"
    assert manifest["step"] == 3
    assert manifest["by_agent"] == {"enterprise-a": [thread_id]}
    assert manifest["threads"][thread_id] == reference
    validated = AgentThreadStore.validate_checkpoint_manifest(
        tmp_path,
        published["path"],
        expected_sha256=published["sha256"],
        checkpoint_id="checkpoint-3",
        step=3,
    )
    assert validated == manifest

    thread_path = tmp_path / reference["path"]
    with thread_path.open("a", encoding="utf-8") as handle:
        handle.write("{}\n")
    with pytest.raises(ValueError, match="thread content hash mismatch"):
        AgentThreadStore.validate_checkpoint_manifest(
            tmp_path,
            published["path"],
            expected_sha256=published["sha256"],
            checkpoint_id="checkpoint-3",
            step=3,
        )


def test_read_messages_replays_latest_request_response_and_runtime_messages(tmp_path):
    store = AgentThreadStore(tmp_path)
    thread_id = store.open_thread(
        agent_id="a",
        checkpoint_step=2,
        scope={"kind": "tick", "id": "2"},
    )
    initial = [
        {"role": "system", "content": "persona"},
        {"role": "user", "content": "act"},
    ]
    store.append_event(
        thread_id,
        "provider_request",
        payload={"request": {"messages": initial, "tools": []}},
    )
    store.append_event(
        thread_id,
        "provider_response",
        payload={
            "response": {"choices": []},
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "act", "arguments": "{}"},
                    }
                ],
            },
        },
    )
    store.append_event(
        thread_id,
        "conversation_message",
        payload={
            "message": {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "full result",
            }
        },
    )
    store.append_event(
        thread_id,
        "conversation_message",
        payload={"role": "user", "content": "extract memories"},
    )

    assert store.read_messages(thread_id) == [
        *initial,
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "act", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": "full result",
        },
        {"role": "user", "content": "extract memories"},
    ]


def test_experiment_log_context_exposes_agent_thread_api(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    thread_id = context.open_agent_thread(
        agent_id="a",
        checkpoint_step=1,
        scope={"kind": "test", "id": "1"},
    )
    context.append_agent_thread_event(
        thread_id,
        "tool_execution_completed",
        payload={"raw_arguments": "{}", "parsed_arguments": {}, "result": {"ok": True}},
    )
    reference = context.close_agent_thread(thread_id)
    assert context.get_agent_thread_reference(thread_id, require_closed=True) == reference
    assert context.read_agent_thread_events(thread_id)[1]["payload"]["result"] == {"ok": True}
    context.close()


@pytest.mark.asyncio
async def test_complete_checkpoint_contains_named_thread_references(tmp_path, monkeypatch):
    monkeypatch.setenv("CHROMA_RUNTIME_MODE", "disk")
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_rule_config(),
        checkpoint_every=1,
    )

    @engine.step(name="thread")
    async def thread_step(ctx):
        thread_id = engine.log_context.open_agent_thread(
            agent_id="enterprise-a",
            checkpoint_step=1,
            scope={"kind": "industry_tick", "id": "1", "tick": 1},
        )
        engine.log_context.append_agent_thread_event(
            thread_id,
            "conversation_message",
            payload={"role": "user", "content": "remember everything"},
        )
        engine.log_context.close_agent_thread(thread_id)
        return ctx.result(artifacts={"thread_id": thread_id})

    await engine.run(steps=1)

    marker = _read_json(tmp_path / "checkpoints" / "complete" / "step_000001.json")
    checkpoint = _read_json(tmp_path / "checkpoints" / marker["world_file"])
    refs = checkpoint["world_metadata"]["agent_threads"]
    assert refs["thread_count"] == 1
    assert refs["by_agent"] == {
        "enterprise-a": [next(iter(refs["threads"]))]
    }
    assert marker["agent_threads_manifest"] == refs["manifest"]
    assert marker["agent_threads_sha256"] == refs["manifest_sha256"]
    resolved = engine.persistence_manager.resolve_checkpoint(1)
    assert resolved["agent_thread_manifest"]["by_agent"] == refs["by_agent"]


class _FakeResponse:
    def __init__(self):
        message = SimpleNamespace(
            role="assistant",
            content="已完成",
            reasoning_content="先检查再行动",
            tool_calls=None,
        )
        self.choices = [SimpleNamespace(message=message, finish_reason="stop", index=0)]
        self.usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)

    def model_dump(self, **_kwargs):
        return {
            "id": "chatcmpl-1",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "已完成",
                        "reasoning_content": "先检查再行动",
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
        }


class _FakeCompletions:
    def __init__(self):
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return _FakeResponse()


class _RetryingFakeCompletions(_FakeCompletions):
    async def create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            raise RuntimeError("provider temporarily unavailable")
        return _FakeResponse()


@pytest.mark.asyncio
async def test_llm_manager_persists_exact_provider_request_and_available_response(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    thread_id = context.open_agent_thread(
        agent_id="enterprise-a",
        checkpoint_step=9,
        scope={"kind": "industry_tick", "id": "9", "tick": 9},
    )
    manager = LLMManager(
        [
            {
                "id": "fake",
                "api_key": "must-not-be-written",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "qwen-test",
                "concurrency": 1,
                "timeout": 10,
            }
        ],
        log_context=context,
    )
    completions = _FakeCompletions()
    manager.clients["fake"] = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=lambda: None,
    )

    result = await manager.request(
        {
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user prompt"},
            ],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "extract_memories",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            "tool_choice": {
                "type": "function",
                "function": {"name": "extract_memories"},
            },
            "max_tokens": 4096,
            "metadata": {
                "thread_id": thread_id,
                "agent_id": "enterprise-a",
                "step": 9,
                "interaction_id": "memory-1",
                "interaction_type": "memory_extract",
                "interaction_name": "memory_extract",
                "turn_id": "memory-turn-1",
            },
        }
    )
    context.close_agent_thread(thread_id)

    assert result["reasoning_content"] == "先检查再行动"
    events = context.read_agent_thread_events(thread_id, materialize_payloads=True)
    requests = [event for event in events if event["event_type"] == "provider_request"]
    responses = [event for event in events if event["event_type"] == "provider_response"]
    assert len(requests) == len(responses) == 1
    actual_request = requests[0]["payload"]["request"]
    assert actual_request["model"] == "qwen-test"
    assert actual_request["messages"][0]["content"] == "system prompt"
    assert actual_request["tool_choice"]["function"]["name"] == "extract_memories"
    assert "metadata" not in actual_request
    assert "agent_id" not in actual_request
    serialized = json.dumps(events, ensure_ascii=False)
    assert "must-not-be-written" not in serialized
    raw_response = responses[0]["payload"]["response"]
    assert raw_response["choices"][0]["finish_reason"] == "stop"
    assert raw_response["choices"][0]["message"]["reasoning_content"] == "先检查再行动"
    assert raw_response["usage"]["total_tokens"] == 18
    assert requests[0]["interaction_id"] == responses[0]["interaction_id"] == "memory-1"
    assert requests[0]["metadata"]["provider_request_id"] == responses[0]["metadata"]["provider_request_id"]

    await manager.close()
    context.close()


@pytest.mark.asyncio
async def test_llm_manager_records_every_physical_retry_attempt(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    thread_id = context.open_agent_thread(
        agent_id="a",
        checkpoint_step=1,
        scope={"kind": "test", "id": "1"},
    )
    manager = LLMManager(
        [
            {
                "id": "fake",
                "api_key": "secret",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "qwen-test",
                "concurrency": 1,
                "timeout": 10,
            }
        ],
        log_context=context,
    )
    completions = _RetryingFakeCompletions()
    manager.clients["fake"] = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
        close=lambda: None,
    )

    await manager.request(
        {
            "messages": [{"role": "user", "content": "retry"}],
            "metadata": {
                "thread_id": thread_id,
                "agent_id": "a",
                "interaction_id": "retry-1",
                "turn_id": "turn-1",
            },
        }
    )
    context.close_agent_thread(thread_id)

    events = context.read_agent_thread_events(thread_id)
    attempts = [
        (
            event["event_type"],
            event.get("metadata", {}).get("attempt_number"),
        )
        for event in events
        if event["event_type"].startswith("provider_")
    ]
    assert attempts == [
        ("provider_request", 1),
        ("provider_error", 1),
        ("provider_request", 2),
        ("provider_response_raw", 2),
        ("provider_response", 2),
    ]

    await manager.close()
    context.close()
