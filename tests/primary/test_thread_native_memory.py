import asyncio
import json

import pytest

from society0.agent.agent_loop import ActionSet
from society0.agent.core import LLMAgent
from society0.agent.memory_extraction import extract_memories_from_thread
from society0.logging import ExperimentLogContext
from society0.schedule import AgentGroup


pytestmark = pytest.mark.primary


def _extract_response(memories, *, call_id="memory_call"):
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "extract_memories",
                    "arguments": json.dumps(
                        {"memories": memories},
                        ensure_ascii=False,
                    ),
                },
            }
        ],
    }


@pytest.mark.asyncio
async def test_memory_extraction_appends_to_original_agent_thread():
    original_messages = [
        {"role": "system", "content": "你负责经营企业。"},
        {"role": "user", "content": "检查库存并调拨。"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "需要先查库存。",
            "tool_calls": [
                {
                    "id": "query_1",
                    "type": "function",
                    "function": {
                        "name": "query_inventory",
                        "arguments": '{"product_id":"pork"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "query_1",
            "content": '{"quantity": 5, "location_id": "plant-a"}',
        },
        {"role": "assistant", "content": "我已完成检查。"},
    ]
    requests = []

    async def llm_call(payload):
        requests.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        return _extract_response(
            [{"content": "我确认了 plant-a 有 5 kg pork。", "importance": 3}]
        )

    result = await extract_memories_from_thread(
        conversation_messages=original_messages,
        llm_call=llm_call,
        thread_id="thread-12-a",
    )

    assert requests[0]["messages"][:-1] == original_messages
    assert requests[0]["messages"][-1]["role"] == "user"
    assert "记忆" in requests[0]["messages"][-1]["content"]
    assert [tool["function"]["name"] for tool in requests[0]["tools"]] == [
        "extract_memories"
    ]
    assert requests[0]["tool_choice"] == {
        "type": "function",
        "function": {"name": "extract_memories"},
    }
    assert requests[0]["metadata"]["thread_id"] == "thread-12-a"
    schema = requests[0]["tools"][0]["function"]["parameters"]
    assert schema["properties"]["memories"]["maxItems"] == 5
    assert (
        schema["properties"]["memories"]["items"]["properties"]["content"][
            "maxLength"
        ]
        == 500
    )
    assert result["conversation_messages"][: len(original_messages)] == original_messages
    assert result["conversation_messages"][-1]["tool_calls"][0]["id"] == "memory_call"
    assert result["full_history"][0]["request"] == requests[0]


@pytest.mark.asyncio
async def test_memory_extraction_retry_continues_the_same_thread():
    original_messages = [
        {"role": "system", "content": "你负责经营企业。"},
        {"role": "user", "content": "处理本 Tick 经营事项。"},
        {"role": "assistant", "content": "本期经营完成。"},
    ]
    requests = []

    async def llm_call(payload):
        requests.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        if len(requests) == 1:
            return {"role": "assistant", "content": "我会继续处理经营任务。"}
        return _extract_response(
            [{"content": "我本期确认了供应中断风险。", "importance": 4}],
            call_id="memory_retry",
        )

    result = await extract_memories_from_thread(
        conversation_messages=original_messages,
        llm_call=llm_call,
        thread_id="thread-9-a",
    )

    assert len(requests) == 2
    retry_messages = requests[1]["messages"]
    assert retry_messages[: len(original_messages)] == original_messages
    assert retry_messages[-1]["role"] == "user"
    assert "extract_memories" in retry_messages[-1]["content"]
    assert "JSON 数组" in retry_messages[-1]["content"]
    assert all(
        message.get("content") != "我会继续处理经营任务。"
        for message in retry_messages[len(original_messages) :]
    )
    assert requests[1]["metadata"]["thread_id"] == "thread-9-a"
    assert len(result["full_history"]) == 2
    assert result["full_history"][0]["response"] == {
        "role": "assistant",
        "content": "我会继续处理经营任务。",
    }
    assert result["success"] is True


@pytest.mark.asyncio
async def test_memory_extraction_retry_does_not_replay_truncated_tool_arguments():
    original_messages = [
        {"role": "system", "content": "你负责经营企业。"},
        {"role": "user", "content": "处理本 Tick 经营事项。"},
        {"role": "assistant", "content": "本期经营完成。"},
    ]
    requests = []
    malformed_arguments = '{"memories": "[{\\"content\\": \\"政策变化' + " " * 8000

    async def llm_call(payload):
        requests.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        if len(requests) == 1:
            return {
                "role": "assistant",
                "content": "",
                "finish_reason": "length",
                "tool_calls": [
                    {
                        "id": "broken_memory",
                        "type": "function",
                        "function": {
                            "name": "extract_memories",
                            "arguments": malformed_arguments,
                        },
                    }
                ],
            }
        return _extract_response(
            [{"content": "我需要记住消费税政策的生效时间。", "importance": 5}],
            call_id="recovered_memory",
        )

    result = await extract_memories_from_thread(
        conversation_messages=original_messages,
        llm_call=llm_call,
        thread_id="thread-truncated-memory",
    )

    retry_messages = requests[1]["messages"]
    assert result["success"] is True
    assert len(result["full_history"]) == 2
    assert not any(
        malformed_arguments
        == ((call.get("function") or {}).get("arguments"))
        for message in retry_messages
        for call in message.get("tool_calls", [])
    )
    assert "不能把数组再次编码成字符串" in retry_messages[-1]["content"]


@pytest.mark.asyncio
async def test_empty_memory_selection_is_success_and_writes_nothing():
    thread_messages = [
        {"role": "system", "content": "你负责经营企业。"},
        {"role": "user", "content": "经营。"},
        {"role": "assistant", "content": "无需调整。"},
    ]

    class FakeMemory:
        def __init__(self):
            self.write_calls = []

        async def add_memories_batch(self, entries, **kwargs):
            self.write_calls.append((entries, kwargs))
            return []

    class LogContext:
        def read_agent_thread_messages(self, thread_id):
            assert thread_id == "thread-empty"
            return thread_messages

    class FakeWorld:
        event_logger = None
        agents_data = {
            "a": {
                "id": "a",
                "type": "participant",
                "archetype": "llm",
                "persona": "你负责经营企业。",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }

        def get_log_context(self):
            return LogContext()

    async def llm_call(payload):
        return _extract_response([])

    memory = FakeMemory()
    agent = LLMAgent("a", FakeWorld())
    agent.initialize_cognitive_system(
        persona="你负责经营企业。",
        memory=memory,
        llm_call=llm_call,
        actionset=ActionSet(),
    )

    result = await agent.extract_memories_from_thread(
        thread_id="thread-empty",
        timestamp=3,
    )

    assert result["memories"] == []
    assert result["memory_ids"] == []
    assert memory.write_calls == []


@pytest.mark.asyncio
async def test_failed_thread_memory_extraction_never_writes_fallback():
    thread_messages = [
        {"role": "system", "content": "你负责经营企业。"},
        {"role": "user", "content": "经营。"},
        {"role": "assistant", "content": "本期经营完成。"},
    ]

    class FakeMemory:
        def __init__(self):
            self.write_calls = []

        async def add_memories_batch(self, entries, **kwargs):
            self.write_calls.append((entries, kwargs))
            return []

    class LogContext:
        def read_agent_thread_messages(self, thread_id):
            assert thread_id == "thread-failed"
            return thread_messages

    class FakeWorld:
        event_logger = None
        agents_data = {
            "a": {
                "id": "a",
                "type": "participant",
                "archetype": "llm",
                "persona": "你负责经营企业。",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }

        def get_log_context(self):
            return LogContext()

    async def llm_call(payload):
        return {"role": "assistant", "content": "没有调用工具。"}

    memory = FakeMemory()
    agent = LLMAgent("a", FakeWorld())
    agent.initialize_cognitive_system(
        persona="你负责经营企业。",
        memory=memory,
        llm_call=llm_call,
        actionset=ActionSet(),
    )

    with pytest.raises(RuntimeError, match="no_tool_call"):
        await agent.extract_memories_from_thread(
            thread_id="thread-failed",
            timestamp=3,
        )

    assert memory.write_calls == []


@pytest.mark.asyncio
async def test_agent_group_instruct_accepts_thread_ids_by_agent():
    class FakeWorld:
        step = 7
        event_logger = None
        _default_agent_concurrency = 1
        _default_agent_concurrency_source = "test"

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            assert kwargs["thread_id"] == "thread-7-a"
            return {
                "status": "success",
                "agent_id": agent_id,
                "performative_output": "ok",
                "raw_output": {
                    "conversation_messages": [
                        {"role": "system", "content": "system"},
                        {"role": "user", "content": instruction},
                        {"role": "assistant", "content": "ok"},
                    ],
                    "full_history": [],
                },
                "thread_id": "thread-7-a",
                "thread_ref": {"thread_id": "thread-7-a", "closed": False},
                "actions": [],
                "phase_timings": {},
            }

    result = await AgentGroup(FakeWorld(), ["a"]).instruct(
        "act",
        retrieve_memory=False,
        thread_ids_by_agent={"a": "thread-7-a"},
    )

    assert result.by_agent("a").raw["thread_id"] == "thread-7-a"


@pytest.mark.asyncio
async def test_all_structured_output_provider_calls_enter_full_history():
    calls = []

    class FakeWorld:
        event_logger = None
        agents_data = {
            "a": {
                "id": "a",
                "type": "participant",
                "archetype": "llm",
                "persona": "Return structured results.",
                "state": {},
                "properties": {},
                "reminders": [],
            }
        }

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return type("Stack", (), {})()

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def llm_call(payload):
        calls.append(json.loads(json.dumps(payload, ensure_ascii=False)))
        name = (payload.get("metadata") or {}).get("interaction_name")
        if name == "submit_result_enforcement":
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "submit_bad_1",
                        "type": "function",
                        "function": {
                            "name": "submit_result",
                            "arguments": '{"result":{"score":"bad"}}',
                        },
                    }
                ],
            }
        if name == "submit_result_schema_correction":
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "submit_bad_2",
                        "type": "function",
                        "function": {
                            "name": "submit_result",
                            "arguments": '{"result":{"score":"still bad"}}',
                        },
                    }
                ],
            }
        if name == "structured_json_fallback":
            return {"role": "assistant", "content": '"score":4.0}'}
        return {"role": "assistant", "content": "I forgot the tool."}

    agent = LLMAgent("a", FakeWorld())
    agent.initialize_cognitive_system(
        persona="Return structured results.",
        memory=None,
        llm_call=llm_call,
        actionset=ActionSet(),
    )
    result = await agent.instruct(
        "Return a score.",
        output_schema={
            "type": "object",
            "properties": {"score": {"type": "number"}},
            "required": ["score"],
            "additionalProperties": False,
        },
        retrieve_memory=False,
        max_turns=1,
        thread_id="thread-structured-a",
    )

    history = result["raw_output"]["full_history"]
    assert len(calls) == len(history) == 4
    assert [item.get("interaction_name") for item in history[1:]] == [
        "submit_result_enforcement",
        "submit_result_schema_correction",
        "structured_json_fallback",
    ]
    assert result["structured_output"] == {"score": 4.0}
    assert result["raw_output"]["conversation_messages"][-1]["content"] == (
        '"score":4.0}'
    )
    assert all(call["metadata"]["thread_id"] == "thread-structured-a" for call in calls)


@pytest.mark.asyncio
async def test_agent_group_extracts_from_threads_and_closes_them():
    extraction_calls = []
    close_calls = []

    class Agent:
        async def extract_memories_from_thread(self, **kwargs):
            extraction_calls.append(kwargs)
            return {
                "memory_ids": [],
                "memories": [],
                "conversation_messages": [],
                "full_history": [],
                "thread_id": kwargs["thread_id"],
                "thread_ref": {"thread_id": kwargs["thread_id"], "closed": False},
            }

    class LogContext:
        def close_agent_thread(self, thread_id, *, metadata=None):
            close_calls.append((thread_id, metadata))
            return {"thread_id": thread_id, "closed": True}

    class World:
        step = 4
        event_logger = None
        _default_agent_concurrency = 1
        _default_agent_concurrency_source = "test"

        def get_agent(self, agent_id):
            assert agent_id == "a"
            return Agent()

        def get_log_context(self):
            return LogContext()

    result = await AgentGroup(World(), ["a"]).extract_thread_memories(
        {"a": "thread-4-a"},
        timestamp=4,
        idempotency_key="tick:4",
    )

    assert result.success_count == 1
    assert extraction_calls[0]["thread_id"] == "thread-4-a"
    assert extraction_calls[0]["idempotency_key"] == "tick:4"
    assert close_calls == [
        (
            "thread-4-a",
            {"memory_extraction_status": "success"},
        )
    ]
    assert result.by_agent("a").value["thread_ref"]["closed"] is True


@pytest.mark.asyncio
async def test_group_extraction_rejects_non_durable_close_result():
    class Agent:
        async def extract_memories_from_thread(self, **kwargs):
            return {
                "memory_ids": [],
                "memories": [],
                "thread_id": kwargs["thread_id"],
            }

    class LogContext:
        def close_agent_thread(self, thread_id, *, metadata=None):
            return {"thread_id": thread_id, "closed": False}

    class World:
        step = 4
        event_logger = None
        _default_agent_concurrency = 1
        _default_agent_concurrency_source = "test"

        def get_agent(self, agent_id):
            return Agent()

        def get_log_context(self):
            return LogContext()

    result = await AgentGroup(World(), ["a"]).extract_thread_memories(
        {"a": "thread-4-a"},
        timestamp=4,
        idempotency_key="tick:4",
    )

    assert result.error_count == 1
    record = result.by_agent("a")
    assert record.value["thread_ref"]["closed"] is False
    assert "same idempotency key" in record.error


@pytest.mark.asyncio
async def test_cancelled_group_extraction_closes_the_original_thread():
    close_calls = []

    class Agent:
        async def extract_memories_from_thread(self, **kwargs):
            raise asyncio.CancelledError

    class LogContext:
        def close_agent_thread(self, thread_id, *, metadata=None):
            close_calls.append((thread_id, metadata))
            return {"thread_id": thread_id, "closed": True}

    class World:
        step = 5
        event_logger = None
        _default_agent_concurrency = 1
        _default_agent_concurrency_source = "test"

        def get_agent(self, agent_id):
            assert agent_id == "a"
            return Agent()

        def get_log_context(self):
            return LogContext()

    with pytest.raises(asyncio.CancelledError):
        await AgentGroup(World(), ["a"]).extract_thread_memories(
            {"a": "thread-5-a"},
            timestamp=5,
        )

    assert close_calls == [
        (
            "thread-5-a",
            {
                "memory_extraction_status": "cancelled",
                "memory_extraction_error": "CancelledError",
            },
        )
    ]


@pytest.mark.asyncio
async def test_memory_commit_receipt_failure_recovers_without_second_llm_or_memory(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    thread_id = context.open_agent_thread(
        agent_id="a",
        checkpoint_step=1,
        scope={"kind": "test", "id": "1"},
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

    class FakeMemory:
        def __init__(self):
            self.write_calls = []

        def stable_memory_id(self, key, *, memory_type="episodic"):
            return f"mem_{memory_type}_{key.replace(':', '_')}"

        async def add_memories_batch(self, entries, **kwargs):
            self.write_calls.append((entries, kwargs))
            return [entry["memory_id"] for entry in entries]

    class World:
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

        def get_log_context(self):
            return context

    llm_calls = []

    async def llm_call(payload):
        llm_calls.append(payload)
        return _extract_response(
            [{"content": "我确认库存为 5。", "importance": 3}]
        )

    memory = FakeMemory()
    agent = LLMAgent("a", World())
    agent.initialize_cognitive_system(
        persona="你负责经营企业。",
        memory=memory,
        llm_call=llm_call,
        actionset=ActionSet(),
    )

    original_append = context.append_agent_thread_event
    failed = False

    def append_with_receipt_failure(thread, event_type, **kwargs):
        nonlocal failed
        event = original_append(thread, event_type, **kwargs)
        if event_type == "memory_extraction_receipt" and not failed:
            failed = True
            raise OSError("receipt fsync failed after durable write")
        return event

    context.append_agent_thread_event = append_with_receipt_failure
    with pytest.raises(OSError, match="receipt fsync failed"):
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
    assert len(memory.write_calls) == 1
    assert recovered["memory_ids"] == [memory.write_calls[0][0][0]["memory_id"]]
    events = context.read_agent_thread_events(thread_id)
    assert [event["event_type"] for event in events].count(
        "memory_extraction_pending"
    ) == 1
    assert [event["event_type"] for event in events].count(
        "memory_extraction_receipt"
    ) == 1
    assert [event["event_type"] for event in events].count("conversation_message") == 3
    context.close_agent_thread(thread_id)
    context.close()


@pytest.mark.asyncio
async def test_concurrent_memory_extraction_same_key_is_single_flight(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    thread_id = context.open_agent_thread(
        agent_id="a",
        checkpoint_step=1,
        scope={"kind": "test", "id": "1"},
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

    class FakeMemory:
        def __init__(self):
            self.write_calls = []

        def stable_memory_id(self, key, *, memory_type="episodic"):
            return f"mem_{memory_type}_{key.replace(':', '_')}"

        async def add_memories_batch(self, entries, **kwargs):
            self.write_calls.append((entries, kwargs))
            await asyncio.sleep(0)
            return [entry["memory_id"] for entry in entries]

    class World:
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

        def get_log_context(self):
            return context

    llm_started = asyncio.Event()
    release_llm = asyncio.Event()
    llm_calls = []

    async def llm_call(payload):
        llm_calls.append(payload)
        llm_started.set()
        await release_llm.wait()
        return _extract_response(
            [{"content": "我确认库存为 5。", "importance": 3}]
        )

    memory = FakeMemory()
    agent = LLMAgent("a", World())
    agent.initialize_cognitive_system(
        persona="你负责经营企业。",
        memory=memory,
        llm_call=llm_call,
        actionset=ActionSet(),
    )

    first = asyncio.create_task(
        agent.extract_memories_from_thread(
            thread_id=thread_id,
            timestamp=1,
            idempotency_key="tick:1:a",
        )
    )
    await llm_started.wait()
    second = asyncio.create_task(
        agent.extract_memories_from_thread(
            thread_id=thread_id,
            timestamp=1,
            idempotency_key="tick:1:a",
        )
    )
    await asyncio.sleep(0)
    release_llm.set()
    first_result, second_result = await asyncio.gather(first, second)

    sequential_result = await agent.extract_memories_from_thread(
        thread_id=thread_id,
        timestamp=1,
        idempotency_key="tick:1:a",
    )

    assert len(llm_calls) == 1
    assert len(memory.write_calls) == 1
    assert first_result == second_result
    assert sequential_result["memory_ids"] == first_result["memory_ids"]
    assert sequential_result["memories"] == first_result["memories"]
    assert sequential_result["thread_id"] == first_result["thread_id"]
    events = context.read_agent_thread_events(thread_id)
    assert [event["event_type"] for event in events].count(
        "memory_extraction_pending"
    ) == 1
    assert [event["event_type"] for event in events].count(
        "memory_extraction_receipt"
    ) == 1
    assert [event["event_type"] for event in events].count("conversation_message") == 3
    context.close_agent_thread(thread_id)
    context.close()
