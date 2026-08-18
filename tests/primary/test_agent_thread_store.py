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
    assert "previous_event_sha256" not in response
    assert "event_sha256" not in request
    assert response["payload"] is None
    assert "payload_sha256" not in response
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


def test_v4_tick_manifest_references_only_closed_threads_for_that_tick(tmp_path):
    store = AgentThreadStore(tmp_path)
    open_thread = store.open_thread(
        agent_id="a",
        checkpoint_step=4,
        scope={"kind": "tick", "id": "4"},
    )
    with pytest.raises(ValueError, match="open agent thread"):
        store.publish_tick_manifest(checkpoint_id="checkpoint-4-open", step=4)
    assert not (tmp_path / "agent_threads" / "manifests" / "checkpoint-4-open.json").exists()

    store.append_event(open_thread, "conversation_message", payload={"role": "user"})
    closed_ref = store.close_thread(open_thread)
    descriptor = store.publish_tick_manifest(checkpoint_id="checkpoint-4", step=4)

    assert descriptor["relative_path"] == descriptor["path"]
    assert descriptor["count"] == descriptor["thread_count"] == 1
    assert descriptor["threads"][open_thread] == closed_ref
    manifest_text = (tmp_path / descriptor["relative_path"]).read_text(encoding="utf-8")
    assert "conversation_message" not in manifest_text
    assert '"payload"' not in manifest_text
    assert store.validate_tick_manifest(descriptor)["checkpoint_id"] == "checkpoint-4"


def test_v4_thread_append_and_manifest_use_incremental_tail_index(tmp_path):
    store = AgentThreadStore(tmp_path)
    thread_id = store.open_thread(
        agent_id="a",
        checkpoint_step=5,
        scope={"kind": "tick", "id": "5"},
    )
    for index in range(20):
        store.append_event(thread_id, "conversation_message", payload={"index": index})

    store.reset_metrics()
    store.append_event(thread_id, "conversation_message", payload={"index": 20})
    store.close_thread(thread_id)
    descriptor = store.publish_tick_manifest(checkpoint_id="checkpoint-5", step=5)

    assert store.metrics["jsonl_full_reads"] == 0
    assert store.metrics["jsonl_bytes_read"] == 0
    assert store.metrics["jsonl_append_bytes"] > 0
    assert descriptor["count"] == 1


def test_v4_epoch_manifest_collects_closed_threads_across_ticks_atomically(tmp_path):
    store = AgentThreadStore(tmp_path)
    first = store.open_thread(
        agent_id="a",
        checkpoint_step=8,
        scope={"kind": "tick", "id": "8"},
    )
    second = store.open_thread(
        agent_id="a",
        checkpoint_step=9,
        scope={"kind": "tick", "id": "9"},
    )
    store.close_thread(first)
    store.close_thread(second)
    store.reset_metrics()
    descriptor = store.publish_epoch_manifest(
        checkpoint_id="checkpoint-epoch-9",
        steps=[8, 9],
    )
    assert descriptor["step"] == 9
    assert descriptor["steps"] == [8, 9]
    assert descriptor["count"] == 2
    assert descriptor["threads"][first]["checkpoint_step"] == 8
    assert descriptor["threads"][second]["checkpoint_step"] == 9
    assert store.metrics["jsonl_full_reads"] == 0
    assert store.metrics["jsonl_full_hashes"] == 0
    store.validate_tick_manifest(
        descriptor,
        expected_checkpoint_id="checkpoint-epoch-9",
        expected_step=9,
    )

    third = store.open_thread(
        agent_id="a",
        checkpoint_step=10,
        scope={"kind": "tick", "id": "10"},
    )
    with pytest.raises(ValueError, match="open agent thread"):
        store.publish_epoch_manifest(
            checkpoint_id="checkpoint-epoch-10-open",
            steps=[8, 9, 10],
        )
    assert not (
        tmp_path / "agent_threads" / "manifests" / "checkpoint-epoch-10-open.json"
    ).exists()
    store.close_thread(third)


def test_v4_tick_manifest_validation_rejects_missing_blob_and_changed_thread(tmp_path):
    store = AgentThreadStore(tmp_path, inline_payload_max_bytes=1)
    thread_id = store.open_thread(
        agent_id="a",
        checkpoint_step=6,
        scope={"kind": "tick", "id": "6"},
    )
    store.append_event(thread_id, "provider_response", payload={"body": "large"})
    store.close_thread(thread_id)
    descriptor = store.publish_tick_manifest(checkpoint_id="checkpoint-6", step=6)

    thread_path = tmp_path / descriptor["threads"][thread_id]["path"]
    events = [json.loads(line) for line in thread_path.read_text(encoding="utf-8").splitlines()]
    blob_path = tmp_path / events[1]["payload_ref"]["path"]
    blob_path.unlink()
    with pytest.raises(FileNotFoundError, match="blob"):
        store.validate_tick_manifest(descriptor)

    # 恢复 blob 后修改完整 Thread。检查点只保留一次文件级校验，已经足以
    # 拒绝被修改的不可变工件，不再给每条 JSONL 记录重复加哈希链。
    blob_path.parent.mkdir(parents=True, exist_ok=True)
    blob_path.write_text('{"body":"large"}', encoding="utf-8")
    events[1]["event_type"] = "changed_after_publish"
    thread_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, separators=(",", ":")) for event in events)
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="content hash"):
        store.validate_tick_manifest(descriptor)


def test_v4_fork_tick_manifest_reuses_immutable_thread_references(tmp_path):
    store = AgentThreadStore(tmp_path)
    thread_id = store.open_thread(
        agent_id="a",
        checkpoint_step=7,
        scope={"kind": "tick", "id": "7"},
    )
    store.append_event(thread_id, "conversation_message", payload={"role": "user"})
    store.close_thread(thread_id)
    source = store.publish_tick_manifest(checkpoint_id="checkpoint-7", step=7)
    thread_files_before = sorted((tmp_path / "agent_threads" / "threads").rglob("*.jsonl"))

    fork = store.fork_tick_manifest(
        source,
        checkpoint_id="fork-root",
        step=0,
        branch_id="branch-b",
    )
    thread_files_after = sorted((tmp_path / "agent_threads" / "threads").rglob("*.jsonl"))

    assert thread_files_after == thread_files_before
    assert fork["threads"] == source["threads"]
    assert fork["forked_from"]["checkpoint_id"] == "checkpoint-7"
    assert store.validate_tick_manifest(fork)["forked_from"]["branch_id"] == "branch-b"


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

    marker = _read_json(tmp_path / "checkpoints" / "v4" / "complete" / "step_000001.json")
    manifest = _read_json(tmp_path / marker["manifest_file"])
    refs = manifest["thread_manifest"]
    assert refs["thread_count"] == 1
    assert refs["by_agent"] == {
        "enterprise-a": [next(iter(refs["threads"]))]
    }
    resolved = engine.persistence_manager.resolve_checkpoint(1)
    assert resolved["manifest"]["thread_manifest"]["by_agent"] == refs["by_agent"]
    validated = AgentThreadStore.validate_tick_manifest_from(
        tmp_path,
        refs,
        expected_checkpoint_id=marker["checkpoint_id"],
        expected_step=1,
    )
    assert validated["by_agent"] == refs["by_agent"]


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
async def test_llm_manager_persists_compact_provider_request_and_available_response(tmp_path):
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
    assert result["finish_reason"] == "stop"
    events = context.read_agent_thread_events(thread_id, materialize_payloads=True)
    requests = [event for event in events if event["event_type"] == "provider_request"]
    responses = [event for event in events if event["event_type"] == "provider_response"]
    assert len(requests) == len(responses) == 1
    actual_request = requests[0]["payload"]["request"]
    assert actual_request["model"] == "qwen-test"
    assert actual_request["messages_count"] == 2
    assert actual_request["tool_names"] == ["extract_memories"]
    assert "messages" not in actual_request
    assert "tools" not in actual_request
    assert actual_request["tool_choice"]["function"]["name"] == "extract_memories"
    assert "metadata" not in actual_request
    assert "agent_id" not in actual_request
    serialized = json.dumps(events, ensure_ascii=False)
    assert "must-not-be-written" not in serialized
    response_payload = responses[0]["payload"]
    assert response_payload["finish_reason"] == "stop"
    assert response_payload["message"]["reasoning_content"] == "先检查再行动"
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
        ("provider_response", 2),
    ]

    await manager.close()
    context.close()
