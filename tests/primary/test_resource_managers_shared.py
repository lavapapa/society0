"""从旧综合测试提取的有效共享资源断言，保持原测试正文。

来源：96b1f3b 基线之后经本分支提供方字段/时长修复的同名测试。
旧入口退役后继续验证真实共享 LLMManager/EmbeddingManager。
"""
import asyncio
import json
from pathlib import Path
import httpx
import pytest
from openai import AsyncOpenAI
from tenacity import wait_fixed
from society0 import resource_managers
from society0.resource_managers import EmbeddingManager, LLMManager
from society0.function_registry import normalize_strict_function_parameters
from society0.logging import ExperimentLogContext

def test_strict_normalization_keeps_optional_enum_nullable():
    normalized = normalize_strict_function_parameters(
        {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": ["buyer", "seller"],
                }
            },
            "required": [],
        }
    )

    assert normalized["properties"]["role"]["type"] == ["string", "null"]
    assert normalized["properties"]["role"]["enum"] == [
        "buyer",
        "seller",
        None,
    ]


@pytest.mark.asyncio
async def test_managers_route_mixed_trust_env_endpoints_to_distinct_pools():
    endpoint_configs = [
        {
            "id": "direct",
            "api_key": "test",
            "base_url": "http://127.0.0.1:9/v1",
            "model": "test-model",
            "concurrency": 1,
            "trust_env": False,
        },
        {
            "id": "proxied",
            "api_key": "test",
            "base_url": "http://127.0.0.1:10/v1",
            "model": "test-model",
            "concurrency": 1,
            "trust_env": True,
        },
    ]
    llm_manager = LLMManager(endpoint_configs)
    embed_manager = EmbeddingManager(endpoint_configs)
    managers = [llm_manager, embed_manager]
    close_counts = {id(manager): {False: 0, True: 0} for manager in managers}

    def track_pool_close(manager, trust_env, pool):
        original_close = pool.aclose

        async def close_once():
            close_counts[id(manager)][trust_env] += 1
            await original_close()

        pool.aclose = close_once

    try:
        for manager in managers:
            assert set(manager._http_clients) == {False, True}
            assert manager.clients["direct"]._client is manager._http_clients[False]
            assert manager.clients["proxied"]._client is manager._http_clients[True]
            assert manager._http_clients[False]._trust_env is False
            assert manager._http_clients[True]._trust_env is True
            for trust_env, pool in manager._http_clients.items():
                track_pool_close(manager, trust_env, pool)
    finally:
        for manager in managers:
            await manager.close()

    for manager in managers:
        assert close_counts[id(manager)] == {False: 1, True: 1}
        assert all(pool.is_closed for pool in manager._http_clients.values())


@pytest.mark.asyncio
async def test_openai_azure_and_embedding_clients_disable_sdk_retries():
    llm_manager = LLMManager(
        [
            {
                "id": "openai",
                "api_key": "test",
                "base_url": "http://127.0.0.1:9/v1",
                "model": "chat-test",
                "concurrency": 1,
            },
            {
                "id": "azure",
                "api_key": "test",
                "base_url": "http://127.0.0.1:10",
                "model": "chat-test",
                "concurrency": 1,
                "provider_type": "azure",
                "api_version": "2024-02-15-preview",
            },
        ]
    )
    embed_manager = EmbeddingManager(
        [
            {
                "id": "embed",
                "api_key": "test",
                "base_url": "http://127.0.0.1:11/v1",
                "model": "embed-test",
                "concurrency": 1,
            }
        ]
    )

    try:
        assert llm_manager.clients["openai"].max_retries == 0
        assert llm_manager.clients["azure"].max_retries == 0
        assert embed_manager.clients["embed"].max_retries == 0
    finally:
        await llm_manager.close()
        await embed_manager.close()


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
async def test_embedding_microbatch_coalesces_distinct_agent_threads(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MICROBATCH_MAX_TEXTS", "20")
    monkeypatch.setenv("EMBEDDING_MICROBATCH_MAX_WAIT_MS", "5")

    manager = EmbeddingManager(
        [
            {
                "id": "default_embed",
                "api_key": "test",
                "base_url": "http://localhost:9999/v1",
                "model": "embed-test",
                "concurrency": 10,
                "provider_type": "openai",
            }
        ]
    )
    physical_calls = []

    async def fake_execute_request(endpoint, texts, dimensions, metadata=None):
        physical_calls.append(
            {
                "texts": list(texts),
                "metadata": dict(metadata or {}),
            }
        )
        return {
            "result": [
                [float(index), float(len(text))]
                for index, text in enumerate(texts)
            ],
            "model": endpoint.model,
            "dimensions": dimensions,
        }

    manager._execute_request = fake_execute_request  # type: ignore[method-assign]

    try:
        results = await asyncio.gather(
            *(
                manager.request(
                    [f"actor {index} memory"],
                    dimensions=2,
                    metadata={
                        "step": 7,
                        "thread_id": f"thread-{index}",
                        "agent_id": f"actor-{index}",
                        "memory_ids": [f"memory-{index}"],
                        "interaction_type": "memory_write",
                    },
                )
                for index in range(20)
            )
        )
    finally:
        await manager.close()

    assert len(physical_calls) == 1
    assert physical_calls[0]["texts"] == [
        f"actor {index} memory" for index in range(20)
    ]
    assert physical_calls[0]["metadata"] == {
        "step": 7,
        "thread_ids": [f"thread-{index}" for index in range(20)],
        "agent_ids": [f"actor-{index}" for index in range(20)],
        "memory_ids": [f"memory-{index}" for index in range(20)],
        "interaction_type": "memory_write",
        "interaction_types": ["memory_write"],
    }
    assert [result["result"][0] for result in results] == [
        [float(index), float(len(f"actor {index} memory"))]
        for index in range(20)
    ]


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
async def test_llm_manager_retries_after_first_connection_failure():
    attempts = 0

    class FakeMessage:
        role = "assistant"
        content = "recovered"
        tool_calls = []

    class FakeChoice:
        message = FakeMessage()

    class FakeResponse:
        choices = [FakeChoice()]
        usage = None

    class FlakyCompletions:
        async def create(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ConnectionError("temporary connection failure")
            return FakeResponse()

    class FlakyChat:
        completions = FlakyCompletions()

    class FlakyClient:
        chat = FlakyChat()

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
        ]
    )
    manager.clients["default"] = FlakyClient()
    manager._max_retries = 2

    try:
        result = await manager.request(
            {"messages": [{"role": "user", "content": "retry"}]}
        )
    finally:
        await manager.close()

    assert attempts == 2
    assert result["content"] == "recovered"
    assert manager.endpoint_stats["default"]["errors"] == 1
    assert manager.endpoint_stats["default"]["successes"] == 1


@pytest.mark.asyncio
async def test_llm_manager_http_transport_preserves_tool_contract_flags():
    captured_requests = []

    async def capture(request: httpx.Request) -> httpx.Response:
        captured_requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            request=request,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-test",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    mock_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(capture),
        base_url="https://mock.test/v1",
    )
    sdk_client = AsyncOpenAI(
        api_key="test",
        base_url="https://mock.test/v1",
        http_client=mock_http_client,
        max_retries=0,
    )
    manager = LLMManager(
        [
            {
                "id": "default",
                "api_key": "test",
                "base_url": "https://unused.test/v1",
                "model": "gpt-test",
                "concurrency": 1,
                "timeout": 30,
            }
        ]
    )
    original_client = manager.clients["default"]
    await original_client.close()
    manager.clients["default"] = sdk_client

    try:
        result = await manager.request(
            {
                "messages": [{"role": "user", "content": "probe"}],
                "parallel_tool_calls": False,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "probe_action",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "required": [],
                                "additionalProperties": False,
                            },
                            "strict": True,
                        },
                    }
                ],
            }
        )
    finally:
        await manager.close()

    assert result["content"] == "ok"
    assert len(captured_requests) == 1
    request_body = captured_requests[0]
    assert request_body["parallel_tool_calls"] is False
    assert request_body["tools"][0]["function"]["strict"] is True


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
