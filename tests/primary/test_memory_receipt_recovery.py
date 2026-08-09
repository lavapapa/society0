import json

import pytest

from society0.agent.agent_loop import ActionSet
from society0.agent.core import LLMAgent
from society0.agent.memory import Memory
from society0.logging import ExperimentLogContext


pytestmark = pytest.mark.primary


def _extract_response(memories):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "memory_call",
                "type": "function",
                "function": {
                    "name": "extract_memories",
                    "arguments": json.dumps({"memories": memories}, ensure_ascii=False),
                },
            }
        ],
    }


class _Collection:
    def __init__(self):
        self.records = {}
        self.upsert_calls = []

    def upsert(self, *, ids, documents, embeddings, metadatas):
        self.upsert_calls.append(list(ids))
        for index, memory_id in enumerate(ids):
            self.records[memory_id] = {
                "content": documents[index],
                "metadata": metadatas[index],
            }

    def get(self, *, ids, include, where):
        selected = [memory_id for memory_id in ids if memory_id in self.records]
        return {
            "ids": selected,
            "documents": [self.records[memory_id]["content"] for memory_id in selected],
            "metadatas": [self.records[memory_id]["metadata"] for memory_id in selected],
        }


class _VectorClient:
    def __init__(self):
        self.collection = _Collection()

    def get_or_create_collection(self, **_kwargs):
        return self.collection


class _World:
    agents_data = {
        "a": {
            "id": "a",
            "type": "participant",
            "archetype": "llm",
            "state": {},
            "properties": {},
            "reminders": [],
        }
    }

    def __init__(self, context):
        self.context = context

    def get_log_context(self):
        return self.context


@pytest.mark.asyncio
async def test_receipt_pre_durable_retry_reuses_memory_payload(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    thread_id = context.open_agent_thread(
        agent_id="a",
        checkpoint_step=1,
        scope={"kind": "test", "id": "receipt-pre-durable"},
    )
    context.append_agent_thread_event(
        thread_id,
        "conversation_message",
        payload={"role": "system", "content": "你负责经营企业。"},
    )
    context.append_agent_thread_event(
        thread_id,
        "conversation_message",
        payload={"role": "user", "content": "请检查库存。"},
    )

    vector_client = _VectorClient()
    embedding_calls = []

    async def embed_call(texts, dimensions, metadata=None):
        embedding_calls.append((list(texts), dict(metadata or {})))
        return {"result": [[0.1, 0.2, 0.3] for _ in texts], "dimensions": dimensions}

    memory = Memory("a", vector_client, embed_call=embed_call, embedding_dim=3)
    llm_calls = []

    async def llm_call(payload):
        llm_calls.append(payload)
        return _extract_response([{"content": "库存为 5。", "importance": 3}])

    agent = LLMAgent("a", _World(context))
    agent.initialize_cognitive_system(
        persona="你负责经营企业。",
        memory=memory,
        llm_call=llm_call,
        actionset=ActionSet(),
    )

    original_append = context.append_agent_thread_event
    failed = False

    def append_with_pre_durable_failure(thread, event_type, **kwargs):
        nonlocal failed
        if event_type == "memory_extraction_receipt" and not failed:
            failed = True
            raise OSError("receipt fsync failed before durable write")
        return original_append(thread, event_type, **kwargs)

    context.append_agent_thread_event = append_with_pre_durable_failure
    with pytest.raises(OSError, match="receipt fsync failed before durable write"):
        await agent.extract_memories_from_thread(
            thread_id=thread_id,
            timestamp=1,
            idempotency_key="tick:1:a",
        )

    recovered = await agent.extract_memories_from_thread(
        thread_id=thread_id,
        timestamp=1,
        idempotency_key="tick:1:a",
    )

    assert len(llm_calls) == 1
    assert len(embedding_calls) == 1
    assert len(vector_client.collection.upsert_calls) == 1
    assert recovered["memory_ids"] == vector_client.collection.upsert_calls[0]

    events = context.read_agent_thread_events(thread_id)
    assert [event["event_type"] for event in events].count(
        "memory_extraction_receipt"
    ) == 1
    tool_messages = [
        event
        for event in events
        if event["event_type"] == "conversation_message"
        and event["payload"].get("role") == "tool"
    ]
    assert len(tool_messages) == 1
    context.close_agent_thread(thread_id)
    context.close()


@pytest.mark.asyncio
async def test_memory_inspect_ids_distinguishes_missing_and_payload_mismatch():
    vector_client = _VectorClient()
    calls = []

    async def embed_call(texts, dimensions, metadata=None):
        calls.append(list(texts))
        return {"result": [[0.1, 0.2, 0.3] for _ in texts], "dimensions": dimensions}

    memory = Memory("a", vector_client, embed_call=embed_call, embedding_dim=3)
    existing = {
        "memory_id": "mem_a_existing",
        "memory_type": "episodic",
        "content": "原始内容",
        "timestamp": 1,
        "importance": 3,
        "metadata": {"idempotency_key": "tick:1:a"},
    }
    await memory.add_memories_batch([existing], fire_and_forget=False)

    matching = await memory.inspect_memory_ids(
        ["mem_a_existing", "mem_a_missing"],
        entries=[
            existing,
            {
                **existing,
                "memory_id": "mem_a_missing",
                "content": "缺失内容",
            },
        ],
    )
    assert matching == {
        "existing_ids": ["mem_a_existing"],
        "missing_ids": ["mem_a_missing"],
        "mismatched_ids": [],
    }

    mismatch = await memory.inspect_memory_ids(
        ["mem_a_existing"],
        entries=[{**existing, "content": "被篡改内容"}],
    )
    assert mismatch == {
        "existing_ids": ["mem_a_existing"],
        "missing_ids": [],
        "mismatched_ids": ["mem_a_existing"],
    }
    assert len(calls) == 1
