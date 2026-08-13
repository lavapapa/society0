import asyncio
import json
from types import SimpleNamespace

import pytest

from society0.agent.memory import Memory
from society0.logging import ExperimentLogContext
from society0.resource_managers import EmbeddingManager


pytestmark = pytest.mark.primary


class _EmbeddingResponse:
    def __init__(self, vectors):
        self.data = [
            SimpleNamespace(index=index, object="embedding", embedding=vector)
            for index, vector in enumerate(vectors)
        ]

    def model_dump(self, **_kwargs):
        return {
            "data": [
                {
                    "index": item.index,
                    "object": item.object,
                    "embedding": list(item.embedding),
                }
                for item in self.data
            ],
            "model": "embed-test",
            "usage": {"prompt_tokens": 7, "total_tokens": 7},
        }


class _EmbeddingClient:
    def __init__(self, *, response=None, error=None):
        self.requests = []
        self.response = response
        self.error = error
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        self.started.set()
        if self.error is not None:
            raise self.error
        if self.response is None:
            await self.release.wait()
            return _EmbeddingResponse([[0.1, 0.2, 0.3]])
        return self.response


class _MemoryCollection:
    def __init__(self):
        self.ids = []

    def add(self, *, ids, documents, embeddings, metadatas):
        self.ids.extend(ids)

    def count(self):
        return len(self.ids)


class _MemoryVectorClient:
    def __init__(self):
        self.collection = _MemoryCollection()

    def get_or_create_collection(self, **_kwargs):
        return self.collection


def _manager(context, *, send_dimensions: bool = True):
    manager = EmbeddingManager(
        [
            {
                "id": "fake",
                "api_key": "embedding-secret",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "embed-test",
                "concurrency": 1,
                "provider_type": "openai",
                "send_dimensions": send_dimensions,
            }
        ],
        log_context=context,
    )
    manager._embedding_timeout_schedule = [30.0]
    return manager


@pytest.mark.asyncio
async def test_embedding_provider_calls_are_persisted_on_agent_thread(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    context.agent_thread_store.inline_payload_max_bytes = 32
    thread_id = context.open_agent_thread(
        agent_id="enterprise-a",
        checkpoint_step=4,
        scope={"kind": "industry_tick", "id": "4"},
    )
    manager = _manager(context)
    client = _EmbeddingClient(response=_EmbeddingResponse([[0.1, 0.2, 0.3]]))
    manager.clients["fake"] = SimpleNamespace(embeddings=client, close=lambda: None)

    result = await manager.request(
        ["需要长期保留的经营记忆"],
        dimensions=3,
        metadata={
            "thread_id": thread_id,
            "interaction_id": "memory-write-4",
            "interaction_type": "memory_write",
            "interaction_name": "tick_memory_write",
            "memory_ids": ["mem_enterprise_a_1"],
        },
    )
    context.close_agent_thread(thread_id)

    events = context.read_agent_thread_events(thread_id, materialize_payloads=True)
    provider_events = [
        event for event in events if event["event_type"].startswith("embedding_provider_")
    ]
    assert [event["event_type"] for event in provider_events] == [
        "embedding_provider_request",
        "embedding_provider_response",
    ]
    request_event, response_event = provider_events
    request = request_event["payload"]["request"]
    assert request["model"] == "embed-test"
    assert request["input"] == ["需要长期保留的经营记忆"]
    assert request["dimensions"] == 3
    assert client.requests == [request]
    assert response_event["payload"]["response"]["data"][0]["embedding"] == [
        0.1,
        0.2,
        0.3,
    ]
    for event in provider_events:
        assert event["interaction_id"] == "memory-write-4"
        assert event["interaction_type"] == "memory_write"
        assert event["interaction_name"] == "tick_memory_write"
        assert event["metadata"]["memory_ids"] == ["mem_enterprise_a_1"]
        assert event["metadata"]["provider_request_id"].startswith("emb_")
    serialized = json.dumps(events, ensure_ascii=False)
    assert "embedding-secret" not in serialized
    assert result["result"] == [[0.1, 0.2, 0.3]]

    await manager.close()
    context.close()


@pytest.mark.asyncio
async def test_openai_compatible_endpoint_can_omit_dimensions_parameter(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    manager = _manager(context, send_dimensions=False)
    client = _EmbeddingClient(response=_EmbeddingResponse([[0.1, 0.2, 0.3]]))
    manager.clients["fake"] = SimpleNamespace(embeddings=client, close=lambda: None)

    result = await manager.request(["固定维度模型"], dimensions=3)

    assert client.requests == [
        {
            "model": "embed-test",
            "input": ["固定维度模型"],
            "timeout": 30.0,
        }
    ]
    assert result["dimensions"] == 3
    assert result["result"] == [[0.1, 0.2, 0.3]]

    await manager.close()
    context.close()


@pytest.mark.asyncio
async def test_embedding_provider_error_is_persisted_with_memory_trace(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    thread_id = context.open_agent_thread(
        agent_id="enterprise-a",
        checkpoint_step=5,
        scope={"kind": "industry_tick", "id": "5"},
    )
    manager = _manager(context)
    client = _EmbeddingClient(error=RuntimeError("embedding unavailable"))
    manager.clients["fake"] = SimpleNamespace(embeddings=client, close=lambda: None)

    with pytest.raises(RuntimeError, match="embedding unavailable"):
        await manager.request(
            ["retryable memory"],
            dimensions=3,
            metadata={
                "thread_id": thread_id,
                "interaction_id": "memory-write-5",
                "interaction_type": "memory_write",
                "interaction_name": "tick_memory_write",
                "memory_ids": ["mem-enterprise-a-2"],
            },
        )
    context.close_agent_thread(thread_id)

    events = context.read_agent_thread_events(thread_id)
    provider_events = [
        event for event in events if event["event_type"].startswith("embedding_provider_")
    ]
    assert [event["event_type"] for event in provider_events] == [
        "embedding_provider_request",
        "embedding_provider_error",
    ]
    assert provider_events[-1]["payload"]["error_type"] == "RuntimeError"
    assert provider_events[-1]["payload"]["error"] == "embedding unavailable"
    assert provider_events[-1]["metadata"]["memory_ids"] == ["mem-enterprise-a-2"]

    await manager.close()
    context.close()


@pytest.mark.asyncio
async def test_embedding_provider_cancellation_is_persisted(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    thread_id = context.open_agent_thread(
        agent_id="enterprise-a",
        checkpoint_step=6,
        scope={"kind": "industry_tick", "id": "6"},
    )
    manager = _manager(context)
    client = _EmbeddingClient()
    manager.clients["fake"] = SimpleNamespace(embeddings=client, close=lambda: None)

    task = asyncio.create_task(
        manager._execute_request(
            manager.endpoints[0],
            ["cancel this embedding"],
            3,
            metadata={
                "thread_id": thread_id,
                "interaction_id": "memory-write-6",
                "interaction_type": "memory_write",
                "interaction_name": "tick_memory_write",
                "memory_ids": ["mem-enterprise-a-3"],
            },
        )
    )
    await client.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    context.close_agent_thread(thread_id)

    events = context.read_agent_thread_events(thread_id)
    provider_events = [
        event for event in events if event["event_type"].startswith("embedding_provider_")
    ]
    assert [event["event_type"] for event in provider_events] == [
        "embedding_provider_request",
        "embedding_provider_cancelled",
    ]
    assert provider_events[-1]["payload"]["error_type"] == "CancelledError"
    assert provider_events[-1]["metadata"]["memory_ids"] == ["mem-enterprise-a-3"]

    await manager.close()
    context.close()


@pytest.mark.asyncio
async def test_memory_write_trace_carries_generated_memory_ids_to_embedding_call():
    captured = []

    async def fake_embed(texts, dimensions, metadata=None):
        captured.append(
            {
                "texts": list(texts),
                "dimensions": dimensions,
                "metadata": dict(metadata or {}),
            }
        )
        return {
            "result": [[0.1, 0.2, 0.3] for _ in texts],
            "model": "embed-test",
            "dimensions": dimensions,
        }

    memory = Memory(
        "enterprise-a",
        _MemoryVectorClient(),
        embed_call=fake_embed,
        embedding_dim=3,
    )
    memory_ids = await memory.add_memories_batch(
        [
            {
                "memory_type": "episodic",
                "content": "经营记忆",
                "timestamp": 4,
                "importance": 3,
            }
        ],
        trace={
            "thread_id": "thr_00000000000000000000000000000004",
            "interaction_id": "memory-write-4",
            "interaction_type": "memory_write",
            "interaction_name": "tick_memory_write",
        },
    )

    assert captured[0]["metadata"]["memory_ids"] == memory_ids
    assert captured[0]["metadata"]["memory_id"] == memory_ids[0]
    assert captured[0]["metadata"]["thread_id"].startswith("thr_")
