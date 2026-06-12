"""
SimEngine V2: Resource Managers - LLM和Embedding的统一管理器

按照resource_management_design.md设计文档实现的资源管理器，
解决硬编码依赖和全局状态滥用问题。

主要组件：
- LLMManager: 统一管理多个LLM端点，支持负载均衡和并发控制
- EmbeddingManager: 统一管理多个Embedding端点
"""

import asyncio
import logging
import time
import uuid
import hashlib
from typing import Dict, List, Any, Optional, Callable, Union, Set
from dataclasses import dataclass
import random
import os
from pathlib import Path
from collections import OrderedDict

from pydantic import BaseModel, Field
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_random_exponential,
)

from .logging import ExperimentLogContext, LogField, ResourceEvent, summarize_text

logger = logging.getLogger(__name__)


@dataclass
class EndpointConfig:
    """端点配置数据结构"""
    id: str
    api_key: str
    base_url: str
    model: str
    concurrency: int
    weight: float = 1.0  # 负载均衡权重
    timeout: float = 30.0  # 请求超时时间
    provider_type: str = "openai"  # 提供商类型: openai, azure, other
    api_version: Optional[str] = None  # Azure API版本
    deployment_name: Optional[str] = None  # Azure部署名称


@dataclass
class _EmbeddingBatchItem:
    cache_key: str
    text: str
    future: asyncio.Future
    model: str
    dimensions: int
    enqueued_at: float


class LLMLogExtras(BaseModel):
    request_id: str
    endpoint_id: str
    model: str
    agent_id: Optional[str] = None
    messages_count: Optional[int] = None
    input_characters: Optional[int] = None
    node_id: Optional[str] = None
    retry_count: int = 0
    cache_hit: bool = False
    duration_sec: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    error: Optional[str] = None
    error_type: Optional[str] = None


class EmbeddingLogExtras(BaseModel):
    request_id: str
    endpoint_id: str
    model: str
    texts_count: int
    dimensions: int
    input_characters: int
    duration_sec: Optional[float] = None
    vectors_returned: Optional[int] = None
    retry_count: int = 0
    cache_hit: bool = False
    cost_usd: Optional[float] = None
    error: Optional[str] = None
    error_type: Optional[str] = None


class LLMManager:
    """
    LLM管理器 - 统一管理所有大语言模型API调用

    设计特性：
    - 支持多个端点配置，每个端点独立的并发控制
    - 轮询等策略的负载均衡
    - 统一的OpenAI兼容接口
    - 总并发能力反馈给Schedule
    """

    def __init__(self, endpoints: List[Dict[str, Any]], *, log_context: Optional[ExperimentLogContext] = None):
        """
        初始化LLM管理器

        Args:
            endpoints: 端点配置列表，每个配置包含：
                - id: 端点标识
                - api_key: API密钥
                - base_url: API地址
                - model: 模型名称
                - concurrency: 并发限制
                - weight: 负载均衡权重（可选，默认1.0）
                - timeout: 请求超时（可选，默认30秒）
        """
        self.endpoints = []
        self.clients = {}
        self.semaphores = {}
        self.endpoint_stats = {}
        self._log_context: Optional[ExperimentLogContext] = log_context
        self._retry_hooks: List[Callable[[str, Dict[str, Any]], None]] = []
        # 内部不强制设置请求超时（允许长时间生成），仅保留重试次数与退避策略
        self._llm_timeout_schedule = [15.0, 30.0, 60.0]  # 仅用于兼容旧逻辑的占位，不再驱动timeout
        self._max_retries = 2

        # 全局并发整形：为所有端点统一限制总并发，避免瞬时连接洪峰
        self._global_semaphore = asyncio.Semaphore(200)

        # 共享 HTTPX 连接池：限制最大连接数并启用 keep-alive/HTTP2
        self._http_client = None
        try:
            import httpx

            limits = httpx.Limits(
                max_connections=200,
                max_keepalive_connections=200,
                keepalive_expiry=30.0,
            )
            # 不设置整体超时，由调用方 payload 决定是否传入 timeout
            self._http_client = httpx.AsyncClient(
                limits=limits,
                timeout=None,
                http2=True,
                follow_redirects=True,
            )
            logger.info(
                "LLMManager: initialized shared HTTPX client (max_conn=200, keepalive=200, http2=True)"
            )
        except Exception as e:
            logger.warning("LLMManager: failed to init shared HTTPX client, fallback to SDK defaults: %s", e)
            self._http_client = None

        # 初始化端点
        for endpoint_config in endpoints:
            self._add_endpoint(endpoint_config)

        self.current_endpoint_index = 0  # 轮询负载均衡索引

        logger.info(f"LLMManager initialized with {len(self.endpoints)} endpoints")

    def set_log_context(self, log_context: Optional[ExperimentLogContext]) -> None:
        """注入或更新日志上下文。"""
        self._log_context = log_context

    def _add_endpoint(self, config: Dict[str, Any]):
        """添加单个端点配置"""
        endpoint = EndpointConfig(
            id=config["id"],
            api_key=config["api_key"],
            base_url=config["base_url"],
            model=config["model"],
            concurrency=config["concurrency"],
            weight=config.get("weight", 1.0),
            timeout=config.get("timeout", 30.0),
            provider_type=config.get("provider_type", "openai"),
            api_version=config.get("api_version"),
            deployment_name=config.get("deployment_name"),
        )

        # 创建异步客户端（注入共享 HTTP 客户端以复用连接池）
        try:
            import openai

            # 根据提供商类型创建不同的客户端
            if endpoint.provider_type == "azure":
                # Azure OpenAI客户端
                client = openai.AsyncAzureOpenAI(
                    api_key=endpoint.api_key,
                    azure_endpoint=endpoint.base_url,
                    api_version=endpoint.api_version or "2024-02-15-preview",
                    timeout=endpoint.timeout,
                    http_client=self._http_client,
                )
                logger.debug(f"Created Azure OpenAI client for endpoint: {endpoint.id}")
            else:
                # 标准OpenAI兼容客户端
                client = openai.AsyncOpenAI(
                    api_key=endpoint.api_key,
                    base_url=endpoint.base_url,
                    timeout=endpoint.timeout,
                    http_client=self._http_client,
                )
                logger.debug(f"Created OpenAI client for endpoint: {endpoint.id}")

            # 创建并发信号量
            semaphore = asyncio.Semaphore(endpoint.concurrency)

            self.endpoints.append(endpoint)
            self.clients[endpoint.id] = client
            self.semaphores[endpoint.id] = semaphore
            self.endpoint_stats[endpoint.id] = {
                "requests": 0,
                "successes": 0,
                "errors": 0,
                "total_time": 0.0,
                "avg_time": 0.0
            }

            logger.debug(f"Added LLM endpoint: {endpoint.id} (concurrency: {endpoint.concurrency}, provider: {endpoint.provider_type})")

        except ImportError:
            raise ImportError("Please install openai: pip install openai")
        except Exception as e:
            logger.error(f"Failed to create client for endpoint {config['id']}: {e}")
            raise

    async def request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        统一的LLM请求接口

        Args:
            payload: OpenAI兼容的请求参数，包含messages, tools等

        Returns:
            OpenAI兼容的响应格式
        """
        # 选择端点
        endpoint = self._select_endpoint()
        if not endpoint:
            raise RuntimeError("No available LLM endpoints")

        return await self._execute_request(endpoint, payload)

    def _select_endpoint(self) -> Optional[EndpointConfig]:
        """使用轮询策略选择端点"""
        if not self.endpoints:
            return None

        # 简单轮询策略
        endpoint = self.endpoints[self.current_endpoint_index]
        self.current_endpoint_index = (self.current_endpoint_index + 1) % len(self.endpoints)

        return endpoint

    async def _execute_request(self, endpoint: EndpointConfig, payload: Dict[str, Any]) -> Dict[str, Any]:
        """在指定端点上执行请求"""
        start_time = time.time()
        request_id = f"llm_{uuid.uuid4().hex[:8]}"
        agent_id: Optional[str] = None
        messages_count: Optional[int] = None
        input_characters: Optional[int] = None
        cache_hit = False
        if isinstance(payload, dict):
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                agent_id = metadata.get("agent_id") or agent_id
                cache_hit = bool(metadata.get("cache_hit", cache_hit))
            agent_id = agent_id or payload.get("agent_id")
            maybe_messages = payload.get("messages")
            if isinstance(maybe_messages, list):
                messages_count = len(maybe_messages)
                input_characters = 0
                for message in maybe_messages:
                    if not isinstance(message, dict):
                        continue
                    content = message.get("content")
                    if isinstance(content, str):
                        input_characters += len(content)
                        continue
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, str):
                                input_characters += len(item)
                            elif isinstance(item, dict):
                                if isinstance(item.get("text"), str):
                                    input_characters += len(item["text"])
                                elif isinstance(item.get("input_text"), str):
                                    input_characters += len(item["input_text"])
                                elif isinstance(item.get("content"), str):
                                    input_characters += len(item["content"])
                                else:
                                    input_characters += len(str(item))
                            elif item is not None:
                                input_characters += len(str(item))
                        continue
                    if content is not None:
                        input_characters += len(str(content))

        if self._log_context:
            start_payload: Dict[str, Any] = {
                LogField.REQUEST_ID.value: request_id,
                LogField.ENDPOINT_ID.value: endpoint.id,
                LogField.MODEL.value: endpoint.model,
            }
            if agent_id:
                start_payload[LogField.AGENT_ID.value] = agent_id
            if messages_count is not None:
                start_payload[LogField.MESSAGES_COUNT.value] = messages_count
            if input_characters is not None:
                start_payload[LogField.INPUT_CHARACTERS.value] = input_characters
            if cache_hit:
                start_payload[LogField.CACHE_HIT.value] = cache_hit
            self._log_context.log_resource(
                "llm",
                "INFO",
                ResourceEvent.LLM_REQUEST_STARTED.value,
                **start_payload,
            )

        # 获取并发许可
        extras = LLMLogExtras(
            request_id=request_id,
            endpoint_id=endpoint.id,
            model=endpoint.model,
            agent_id=agent_id,
            messages_count=messages_count,
            input_characters=input_characters,
            cache_hit=cache_hit,
        )

        # 全局并发整形 + 端点级并发限制
        async with self._global_semaphore:
            async with self.semaphores[endpoint.id]:
                # 抖动：降低瞬时突发（5ms~50ms）
                try:
                    await asyncio.sleep(random.uniform(0.005, 0.05))
                except Exception:
                    pass

                stats_entry = self.endpoint_stats[endpoint.id]

                retrying = AsyncRetrying(
                    stop=stop_after_attempt(self._max_retries),
                    retry=retry_if_exception_type(Exception),
                    wait=wait_random_exponential(multiplier=0.1, max=1.5),
                    reraise=True,
                )

                try:
                    async for attempt in retrying:
                        attempt_number = attempt.retry_state.attempt_number
                        try:
                            extras.error = None
                            extras.error_type = None
                            extras.retry_count = attempt_number - 1

                            stats_entry["requests"] += 1

                            client = self.clients[endpoint.id]
                            request_params = payload.copy()

                            # Azure使用deployment_name，其他使用model
                            if endpoint.provider_type == "azure" and endpoint.deployment_name:
                                request_params["model"] = endpoint.deployment_name
                            else:
                                request_params["model"] = endpoint.model

                            response = await client.chat.completions.create(**request_params)
                            result = self._convert_response(response)

                            execution_time = time.time() - start_time
                            extras.duration_sec = execution_time

                            usage = getattr(response, "usage", None)

                            def _extract_usage_value(value: Any, key: str) -> Optional[int]:
                                if value is None:
                                    return None
                                if isinstance(value, dict):
                                    maybe = value.get(key)
                                    return int(maybe) if isinstance(maybe, (int, float)) else maybe
                                maybe_attr = getattr(value, key, None)
                                return int(maybe_attr) if isinstance(maybe_attr, (int, float)) else maybe_attr

                            extras.prompt_tokens = _extract_usage_value(usage, "prompt_tokens")
                            extras.completion_tokens = _extract_usage_value(usage, "completion_tokens")
                            extras.total_tokens = _extract_usage_value(usage, "total_tokens")

                            if self._log_context:
                                self._log_context.log_resource(
                                    "llm",
                                    "INFO",
                                    ResourceEvent.LLM_REQUEST_COMPLETED.value,
                                    **extras.model_dump(exclude_none=True),
                                )

                            stats_entry["successes"] += 1
                            stats_entry["total_time"] += execution_time
                            stats_entry["avg_time"] = stats_entry["total_time"] / stats_entry["requests"]

                            logger.debug(
                                "LLM request completed on %s in %.3fs",
                                endpoint.id,
                                execution_time,
                            )
                            return result

                        except Exception as exc:
                            extras.error_type = type(exc).__name__
                            extras.error = str(exc)
                            extras.duration_sec = time.time() - start_time
                            stats_entry["errors"] += 1

                            level = (
                                "WARNING"
                                if attempt.retry_state.attempt_number < self._max_retries
                                else "ERROR"
                            )
                            if self._log_context:
                                self._log_context.log_resource(
                                    "llm",
                                    level,
                                    ResourceEvent.LLM_REQUEST_FAILED.value,
                                    **extras.model_dump(exclude_none=True),
                                )

                            if level == "WARNING":
                                logger.warning(
                                    "LLM request retry %s/%s failed on endpoint %s: %s",
                                    attempt.retry_state.attempt_number,
                                    self._max_retries,
                                    endpoint.id,
                                    exc,
                                )
                            else:
                                logger.error(
                                    "LLM request failed on endpoint %s after %s attempts: %s",
                                    endpoint.id,
                                    self._max_retries,
                                    exc,
                                )
                            raise
                except RetryError as retry_error:
                    final_exc = retry_error.last_attempt.exception()
                    if final_exc is None:
                        final_exc = RuntimeError("LLM request failed without exception detail")
                    raise final_exc

    def _convert_response(self, response) -> Dict[str, Any]:
        """将OpenAI响应转换为标准格式"""
        try:
            choice = response.choices[0]
            message = choice.message

            result = {
                "role": message.role,
                "content": message.content or "",
            }

            # 处理reasoning_content（推理模型）
            if hasattr(message, 'reasoning_content') and message.reasoning_content:
                result["reasoning_content"] = message.reasoning_content

            # 处理tool_calls
            if hasattr(message, 'tool_calls') and message.tool_calls:
                result["tool_calls"] = []
                for tool_call in message.tool_calls:
                    result["tool_calls"].append({
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments
                        }
                    })

            return result

        except Exception as e:
            logger.error(f"Failed to convert LLM response: {e}")
            raise

    def get_total_concurrency(self) -> int:
        """获取所有端点的总并发能力"""
        return sum(endpoint.concurrency for endpoint in self.endpoints)

    def get_stats(self) -> Dict[str, Any]:
        """获取管理器统计信息"""
        return {
            "total_endpoints": len(self.endpoints),
            "total_concurrency": self.get_total_concurrency(),
            "endpoints": self.endpoint_stats.copy()
        }

    def get_available_endpoints(self) -> List[str]:
        """获取可用端点列表"""
        return [endpoint.id for endpoint in self.endpoints]

    async def close(self) -> None:
        """关闭管理器内部资源（共享HTTP客户端与SDK客户端）。"""
        # 关闭 OpenAI/兼容 客户端
        for client in list(self.clients.values()):
            if client is None:
                continue
            try:
                closer = getattr(client, "close", None)
                if callable(closer):
                    res = closer()
                    if asyncio.iscoroutine(res):
                        await res
            except Exception:
                pass
        # 关闭共享 HTTP 客户端
        http_client = getattr(self, "_http_client", None)
        if http_client is not None:
            try:
                await http_client.aclose()
            except Exception:
                pass


class EmbeddingManager:
    """
    Embedding管理器 - 统一管理所有文本向量化API调用

    与LLMManager设计完全相同，但专门处理embedding请求
    """

    def __init__(self, endpoints: List[Dict[str, Any]], *, log_context: Optional[ExperimentLogContext] = None):
        """
        初始化Embedding管理器

        Args:
            endpoints: 端点配置列表，格式与LLMManager相同
        """
        self.endpoints = []
        self.clients = {}
        self.semaphores = {}
        self.endpoint_stats = {}
        self._log_context: Optional[ExperimentLogContext] = log_context
        self._retry_hooks: List[Callable[[str, Dict[str, Any]], None]] = []
        self._embedding_timeout_schedule = [30.0, 60.0, 120.0]  # 秒：Embedding 请求同步超时阶梯
        self._cache_lock = asyncio.Lock()
        self._embedding_cache: "OrderedDict[str, List[float]]" = OrderedDict()
        self._pending_embeddings: Dict[str, asyncio.Future] = {}
        self._cache_max_items = self._load_cache_max_items()
        self._cache_hits = 0
        self._cache_misses = 0
        self._microbatch_max_batch_texts = self._load_microbatch_max_batch_texts()
        self._microbatch_max_wait_ms = self._load_microbatch_max_wait_ms()
        self._microbatch_max_batch_chars = self._load_microbatch_max_batch_chars()
        self._microbatch_lock = asyncio.Lock()
        self._microbatch_queues: Dict[str, List[_EmbeddingBatchItem]] = {}
        self._microbatch_queue_chars: Dict[str, int] = {}
        self._microbatch_timers: Dict[str, asyncio.Task] = {}
        self._microbatch_flushing_buckets: Set[str] = set()
        self._microbatch_flush_tasks: Set[asyncio.Task] = set()
        self._microbatch_stats: Dict[str, float] = {
            "batches": 0.0,
            "items": 0.0,
            "coalesced_items": 0.0,
            "wait_ms_total": 0.0,
            "split_count": 0.0,
            "failed_items": 0.0,
        }
        self.default_dimensions = self._load_default_dimensions(endpoints)

        # 初始化端点
        for endpoint_config in endpoints:
            self._add_endpoint(endpoint_config)

        self.current_endpoint_index = 0

        logger.info(f"EmbeddingManager initialized with {len(self.endpoints)} endpoints")

    def set_log_context(self, log_context: Optional[ExperimentLogContext]) -> None:
        """注入或更新日志上下文。"""
        self._log_context = log_context

    @staticmethod
    def _load_default_dimensions(endpoints: List[Dict[str, Any]]) -> int:
        for endpoint in endpoints:
            raw = endpoint.get("dimensions")
            if raw is None:
                continue
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                return value
        return 512

    @staticmethod
    def _load_cache_max_items() -> int:
        raw = (os.getenv("EMBEDDING_CACHE_MAX_ITEMS") or "5000").strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 5000
        return max(0, value)

    @staticmethod
    def _make_cache_key(model: str, dimensions: int, text: str) -> str:
        digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
        return f"{model}|{dimensions}|{digest}"

    @staticmethod
    def _make_microbatch_bucket_key(model: str, dimensions: int) -> str:
        return f"{model}|{dimensions}"

    @staticmethod
    def _load_microbatch_max_batch_texts() -> int:
        raw = (os.getenv("EMBEDDING_MICROBATCH_MAX_TEXTS") or "20").strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 20
        return max(1, value)

    @staticmethod
    def _load_microbatch_max_wait_ms() -> int:
        raw = (os.getenv("EMBEDDING_MICROBATCH_MAX_WAIT_MS") or "15").strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 15
        return max(0, value)

    @staticmethod
    def _load_microbatch_max_batch_chars() -> int:
        raw = (os.getenv("EMBEDDING_MICROBATCH_MAX_CHARS") or "20000").strip()
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 20000
        return max(1, value)

    def _cache_put_unlocked(self, cache_key: str, embedding: List[float]) -> None:
        if self._cache_max_items <= 0:
            return
        self._embedding_cache[cache_key] = embedding
        self._embedding_cache.move_to_end(cache_key)
        while len(self._embedding_cache) > self._cache_max_items:
            self._embedding_cache.popitem(last=False)

    def _register_flush_task_unlocked(self, bucket_key: str) -> None:
        if bucket_key in self._microbatch_flushing_buckets:
            return
        self._microbatch_flushing_buckets.add(bucket_key)
        task = asyncio.create_task(self._flush_microbatch_bucket(bucket_key))
        self._microbatch_flush_tasks.add(task)

        def _cleanup(done_task: asyncio.Task) -> None:
            self._microbatch_flush_tasks.discard(done_task)
            try:
                done_task.result()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Embedding microbatch flush task crashed for bucket %s", bucket_key)

        task.add_done_callback(_cleanup)

    async def _enqueue_microbatch_item(self, bucket_key: str, item: _EmbeddingBatchItem) -> None:
        async with self._microbatch_lock:
            queue = self._microbatch_queues.setdefault(bucket_key, [])
            queue.append(item)
            self._microbatch_queue_chars[bucket_key] = self._microbatch_queue_chars.get(bucket_key, 0) + len(item.text)

            queue_size = len(queue)
            queue_chars = self._microbatch_queue_chars.get(bucket_key, 0)
            immediate_flush = (
                queue_size >= self._microbatch_max_batch_texts
                or queue_chars >= self._microbatch_max_batch_chars
                or self._microbatch_max_wait_ms <= 0
            )

            if immediate_flush:
                timer = self._microbatch_timers.pop(bucket_key, None)
                if timer is not None:
                    timer.cancel()
                self._register_flush_task_unlocked(bucket_key)
                return

            if bucket_key in self._microbatch_timers:
                return

            timer = asyncio.create_task(self._delayed_flush(bucket_key))
            self._microbatch_timers[bucket_key] = timer

    async def _delayed_flush(self, bucket_key: str) -> None:
        wait_seconds = self._microbatch_max_wait_ms / 1000.0
        if wait_seconds > 0:
            try:
                await asyncio.sleep(wait_seconds)
            except asyncio.CancelledError:
                return

        async with self._microbatch_lock:
            timer = self._microbatch_timers.get(bucket_key)
            if timer is not asyncio.current_task():
                return
            self._microbatch_timers.pop(bucket_key, None)
            self._register_flush_task_unlocked(bucket_key)

    def _pop_microbatch_unlocked(self, bucket_key: str) -> List[_EmbeddingBatchItem]:
        queue = self._microbatch_queues.get(bucket_key)
        if not queue:
            return []

        max_items = max(1, self._microbatch_max_batch_texts)
        max_chars = max(1, self._microbatch_max_batch_chars)

        batch: List[_EmbeddingBatchItem] = []
        batch_chars = 0
        take_count = 0

        for item in queue:
            text_chars = len(item.text)
            if take_count >= max_items:
                break
            if batch and (batch_chars + text_chars) > max_chars:
                break
            batch.append(item)
            batch_chars += text_chars
            take_count += 1
            if take_count >= max_items:
                break

        if not batch and queue:
            # 单条文本超长时仍允许单条出队，避免永久阻塞。
            first_item = queue[0]
            batch = [first_item]
            batch_chars = len(first_item.text)
            take_count = 1

        if take_count > 0:
            del queue[:take_count]
            self._microbatch_queue_chars[bucket_key] = max(
                0,
                self._microbatch_queue_chars.get(bucket_key, 0) - batch_chars,
            )

        if not queue:
            self._microbatch_queues.pop(bucket_key, None)
            self._microbatch_queue_chars.pop(bucket_key, None)

        return batch

    async def _resolve_batch_success(self, batch: List[_EmbeddingBatchItem], embeddings: List[List[float]]) -> None:
        now = time.time()
        batch_size = len(batch)
        self._microbatch_stats["batches"] += 1
        self._microbatch_stats["items"] += batch_size
        if batch_size > 1:
            self._microbatch_stats["coalesced_items"] += batch_size

        async with self._cache_lock:
            for item, embedding in zip(batch, embeddings):
                wait_ms = max(0.0, (now - item.enqueued_at) * 1000.0)
                self._microbatch_stats["wait_ms_total"] += wait_ms

                self._cache_put_unlocked(item.cache_key, embedding)
                future = self._pending_embeddings.pop(item.cache_key, None)
                if future is None or future.done():
                    continue
                future.set_result(embedding)

    async def _resolve_batch_failure(self, batch: List[_EmbeddingBatchItem], exc: Exception) -> None:
        self._microbatch_stats["failed_items"] += len(batch)
        async with self._cache_lock:
            for item in batch:
                future = self._pending_embeddings.pop(item.cache_key, None)
                if future is None or future.done():
                    continue
                future.set_exception(exc)
                try:
                    _ = future.exception()
                except Exception:
                    pass

    async def _execute_microbatch_with_split(self, bucket_key: str, batch: List[_EmbeddingBatchItem]) -> None:
        if not batch:
            return

        model = batch[0].model
        dimensions = batch[0].dimensions
        endpoint = self._select_endpoint(model=model)
        if endpoint is None:
            await self._resolve_batch_failure(batch, RuntimeError(f"No available Embedding endpoints for model '{model}'"))
            return

        texts = [item.text for item in batch]
        try:
            result = await self._execute_request(endpoint, texts, dimensions)
            embeddings = result.get("result") or []
            if len(embeddings) != len(batch):
                raise RuntimeError(f"Microbatch result mismatch: expected {len(batch)}, got {len(embeddings)}")
            await self._resolve_batch_success(batch, embeddings)
        except Exception as exc:
            if len(batch) > 1:
                self._microbatch_stats["split_count"] += 1
                mid = len(batch) // 2
                left = batch[:mid]
                right = batch[mid:]
                logger.warning(
                    "Embedding microbatch failed for bucket %s (size=%s), split into %s and %s: %s",
                    bucket_key,
                    len(batch),
                    len(left),
                    len(right),
                    exc,
                )
                await self._execute_microbatch_with_split(bucket_key, left)
                await self._execute_microbatch_with_split(bucket_key, right)
                return

            await self._resolve_batch_failure(batch, exc)

    async def _flush_microbatch_bucket(self, bucket_key: str) -> None:
        try:
            while True:
                async with self._microbatch_lock:
                    batch = self._pop_microbatch_unlocked(bucket_key)
                if not batch:
                    return
                await self._execute_microbatch_with_split(bucket_key, batch)
        finally:
            async with self._microbatch_lock:
                self._microbatch_flushing_buckets.discard(bucket_key)
                # flush 期间可能有新任务入队；若有残留，继续拉起下一轮 flush。
                if self._microbatch_queues.get(bucket_key):
                    self._register_flush_task_unlocked(bucket_key)

    def _add_endpoint(self, config: Dict[str, Any]):
        """添加单个端点配置"""
        # Ollama Python SDK expects the host root (e.g. http://host:11434), not the OpenAI-compatible /v1.
        # Accept both to keep env/config backwards compatible.
        raw_base_url = str(config.get("base_url") or "").strip()
        provider_type = str(config.get("provider_type") or "openai").strip() or "openai"
        base_url = raw_base_url
        if provider_type.lower() == "ollama" and base_url.endswith("/v1"):
            base_url = base_url[:-3].rstrip("/")

        endpoint = EndpointConfig(
            id=config["id"],
            api_key=config["api_key"],
            base_url=base_url,
            model=config["model"],
            concurrency=config["concurrency"],
            weight=config.get("weight", 1.0),
            timeout=config.get("timeout", 30.0),
            provider_type=provider_type,
            api_version=config.get("api_version"),
            deployment_name=config.get("deployment_name"),
        )

        # 创建异步客户端
        try:
            if endpoint.provider_type.lower() == "ollama":
                try:
                    from ollama import AsyncClient  # type: ignore
                except ImportError:
                    raise ImportError("Please install ollama: pip install ollama")

                client = AsyncClient(host=endpoint.base_url)
            else:
                import openai
                client = openai.AsyncOpenAI(
                    api_key=endpoint.api_key,
                    base_url=endpoint.base_url,
                    timeout=endpoint.timeout
                )

            # 创建并发信号量
            semaphore = asyncio.Semaphore(endpoint.concurrency)

            self.endpoints.append(endpoint)
            self.clients[endpoint.id] = client
            self.semaphores[endpoint.id] = semaphore
            self.endpoint_stats[endpoint.id] = {
                "requests": 0,
                "successes": 0,
                "errors": 0,
                "total_time": 0.0,
                "avg_time": 0.0
            }

            logger.debug(
                "Added Embedding endpoint: %s (concurrency: %s, provider: %s)",
                endpoint.id,
                endpoint.concurrency,
                endpoint.provider_type,
            )

        except Exception as e:
            logger.error(f"Failed to create client for endpoint {config['id']}: {e}")
            raise

    async def request(self, texts: List[str], dimensions: Optional[int] = None) -> Dict[str, Any]:
        """
        统一的Embedding请求接口

        Args:
            texts: 待向量化的文本列表
            dimensions: 向量维度

        Returns:
            {"result": List[List[float]], "model": str, "dimensions": int}
        """
        # 选择端点，主要用于确定当前模型名；实际请求在 flush 时再次按负载选择端点。
        endpoint = self._select_endpoint()
        if not endpoint:
            raise RuntimeError("No available Embedding endpoints")
        requested_dimensions = int(dimensions or self.default_dimensions)

        if not texts:
            return {"result": [], "model": endpoint.model, "dimensions": requested_dimensions}

        model = endpoint.model
        bucket_key = self._make_microbatch_bucket_key(model, requested_dimensions)
        loop = asyncio.get_running_loop()
        results: List[Optional[List[float]]] = [None] * len(texts)
        cache_keys: List[str] = []
        wait_futures: Dict[str, asyncio.Future] = {}
        new_batch_items: List[_EmbeddingBatchItem] = []

        async with self._cache_lock:
            for idx, text in enumerate(texts):
                cache_key = self._make_cache_key(model, requested_dimensions, text)
                cache_keys.append(cache_key)
                cached = self._embedding_cache.get(cache_key)
                if cached is not None:
                    self._embedding_cache.move_to_end(cache_key)
                    results[idx] = cached
                    self._cache_hits += 1
                    continue

                self._cache_misses += 1
                pending = self._pending_embeddings.get(cache_key)
                if pending is None:
                    pending = loop.create_future()
                    self._pending_embeddings[cache_key] = pending
                    new_batch_items.append(
                        _EmbeddingBatchItem(
                            cache_key=cache_key,
                            text=text,
                            future=pending,
                            model=model,
                            dimensions=requested_dimensions,
                            enqueued_at=time.time(),
                        )
                    )
                wait_futures[cache_key] = pending

        for item in new_batch_items:
            await self._enqueue_microbatch_item(bucket_key, item)

        for idx, embedding in enumerate(results):
            if embedding is not None:
                continue
            key = cache_keys[idx]
            future = wait_futures.get(key)
            if future is None:
                raise RuntimeError(f"Missing embedding future for cache key {key}")
            results[idx] = await future

        final_results: List[List[float]] = []
        for idx, embedding in enumerate(results):
            if embedding is None:
                raise RuntimeError(f"Missing embedding at index {idx}")
            final_results.append(embedding)

        return {"result": final_results, "model": model, "dimensions": requested_dimensions}

    def _select_endpoint(self, model: Optional[str] = None) -> Optional[EndpointConfig]:
        """按当前负载选择端点（最小负载优先，轮询作为平局打散）。"""
        if not self.endpoints:
            return None

        if model:
            candidates = [ep for ep in self.endpoints if ep.model == model]
        else:
            candidates = list(self.endpoints)
        if not candidates:
            return None

        total = len(candidates)
        best_idx = 0
        best_score = None

        for offset in range(total):
            idx = (self.current_endpoint_index + offset) % total
            endpoint = candidates[idx]
            semaphore = self.semaphores.get(endpoint.id)
            if semaphore is None:
                continue

            available = max(0, int(getattr(semaphore, "_value", 0)))
            capacity = max(1, int(endpoint.concurrency))
            inflight = max(0, capacity - available)
            load_ratio = inflight / capacity
            # 优先最小负载，其次最小绝对 in-flight，最后保持轮询顺序
            score = (load_ratio, inflight, offset)

            if best_score is None or score < best_score:
                best_score = score
                best_idx = idx

        endpoint = candidates[best_idx]
        self.current_endpoint_index = (best_idx + 1) % total
        return endpoint

    async def _execute_request(self, endpoint: EndpointConfig, texts: List[str], dimensions: int) -> Dict[str, Any]:
        """在指定端点上执行embedding请求"""
        start_time = time.time()
        request_id = f"emb_{uuid.uuid4().hex[:8]}"
        texts_count = len(texts)
        total_characters = sum(len(text) for text in texts)
        if self._log_context:
            start_payload = {
                LogField.REQUEST_ID.value: request_id,
                LogField.ENDPOINT_ID.value: endpoint.id,
                LogField.MODEL.value: endpoint.model,
                LogField.TEXTS_COUNT.value: texts_count,
                LogField.DIMENSIONS.value: dimensions,
                LogField.INPUT_CHARACTERS.value: total_characters,
            }
            self._log_context.log_resource(
                "embedding",
                "INFO",
                ResourceEvent.EMBEDDING_REQUEST_STARTED.value,
                **start_payload,
            )

        # 获取并发许可
        extras = EmbeddingLogExtras(
            request_id=request_id,
            endpoint_id=endpoint.id,
            model=endpoint.model,
            texts_count=texts_count,
            dimensions=dimensions,
            input_characters=total_characters,
        )

        async with self.semaphores[endpoint.id]:
            stats_entry = self.endpoint_stats[endpoint.id]
            timeout_schedule = self._embedding_timeout_schedule or [120.0]
            max_attempts = len(timeout_schedule)

            for attempt_number, request_deadline in enumerate(timeout_schedule, start=1):
                try:
                    extras.error = None
                    extras.error_type = None
                    extras.retry_count = attempt_number - 1

                    stats_entry["requests"] += 1
                    client = self.clients[endpoint.id]
                    embeddings: List[List[float]] = []

                    if endpoint.provider_type.lower() == "ollama":
                        # Ollama 支持批量 embed 调用
                        response = await asyncio.wait_for(
                            client.embed(
                                model=endpoint.model,
                                input=texts,
                            ),
                            timeout=request_deadline,
                        )
                        embedding_list = None
                        if isinstance(response, dict):
                            embedding_list = response.get("embeddings")
                        else:
                            embedding_list = getattr(response, "embeddings", None)
                        if embedding_list:
                            embeddings.extend(embedding_list)
                    else:
                        batch_size = 50
                        for i in range(0, len(texts), batch_size):
                            batch_texts = texts[i:i + batch_size]
                            response = await client.embeddings.create(
                                model=endpoint.model,
                                input=batch_texts,
                                dimensions=dimensions,
                                timeout=request_deadline,
                            )
                            for embedding_obj in response.data:
                                embeddings.append(embedding_obj.embedding)

                    if len(embeddings) != len(texts):
                        raise RuntimeError(
                            f"Embedding endpoint returned {len(embeddings)} vectors for {len(texts)} texts"
                        )

                    execution_time = time.time() - start_time
                    extras.duration_sec = execution_time
                    extras.vectors_returned = len(embeddings)

                    result = {
                        "result": embeddings,
                        "model": endpoint.model,
                        "dimensions": dimensions
                    }

                    if self._log_context:
                        self._log_context.log_resource(
                            "embedding",
                            "INFO",
                            ResourceEvent.EMBEDDING_REQUEST_COMPLETED.value,
                            **extras.model_dump(exclude_none=True),
                        )

                    stats_entry["successes"] += 1
                    stats_entry["total_time"] += execution_time
                    stats_entry["avg_time"] = stats_entry["total_time"] / stats_entry["requests"]

                    logger.debug(
                        "Embedding request completed on %s in %.3fs (attempt %s/%s, timeout %.0fs)",
                        endpoint.id,
                        execution_time,
                        attempt_number,
                        max_attempts,
                        request_deadline,
                    )
                    return result

                except Exception as exc:
                    extras.error = str(exc)
                    extras.error_type = type(exc).__name__
                    extras.duration_sec = time.time() - start_time
                    stats_entry["errors"] += 1

                    is_last_attempt = attempt_number >= max_attempts
                    level = "ERROR" if is_last_attempt else "WARNING"
                    if self._log_context:
                        self._log_context.log_resource(
                            "embedding",
                            level,
                            ResourceEvent.EMBEDDING_REQUEST_FAILED.value,
                            **extras.model_dump(exclude_none=True),
                            )

                    retry_payload = {
                        "attempt": attempt_number,
                        "max_attempts": max_attempts,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "dimensions": dimensions,
                        "texts_count": texts_count,
                    }
                    for hook in self._retry_hooks:
                        try:
                            hook(endpoint.id, retry_payload)
                        except Exception:
                            logger.debug("Embedding retry hook failed for endpoint %s", endpoint.id, exc_info=True)

                    if is_last_attempt:
                        logger.error(
                            "Embedding request failed on endpoint %s after %s attempts: %s",
                            endpoint.id,
                            max_attempts,
                            exc,
                        )
                        raise

                    logger.warning(
                        "Embedding request retry %s/%s failed on endpoint %s: %s",
                        attempt_number,
                        max_attempts,
                        endpoint.id,
                        exc,
                    )
                    await asyncio.sleep(min(0.2 * (2 ** (attempt_number - 1)), 2.0))

            raise RuntimeError("Embedding request exhausted retries without error details")

    def get_total_concurrency(self) -> int:
        """获取所有端点的总并发能力"""
        return sum(endpoint.concurrency for endpoint in self.endpoints)

    def get_stats(self) -> Dict[str, Any]:
        """获取管理器统计信息"""
        total_lookups = self._cache_hits + self._cache_misses
        total_batches = int(self._microbatch_stats["batches"])
        total_items = int(self._microbatch_stats["items"])
        coalesced_items = int(self._microbatch_stats["coalesced_items"])
        avg_batch_size = (total_items / total_batches) if total_batches else 0.0
        avg_wait_ms = (self._microbatch_stats["wait_ms_total"] / total_items) if total_items else 0.0
        batch_hit_ratio = (coalesced_items / total_items) if total_items else 0.0
        return {
            "total_endpoints": len(self.endpoints),
            "total_concurrency": self.get_total_concurrency(),
            "endpoints": self.endpoint_stats.copy(),
            "cache": {
                "enabled": self._cache_max_items > 0,
                "max_items": self._cache_max_items,
                "current_items": len(self._embedding_cache),
                "hits": self._cache_hits,
                "misses": self._cache_misses,
                "hit_ratio": (self._cache_hits / total_lookups) if total_lookups else 0.0,
                "pending_keys": len(self._pending_embeddings),
            },
            "microbatch": {
                "max_batch_texts": self._microbatch_max_batch_texts,
                "max_wait_ms": self._microbatch_max_wait_ms,
                "max_batch_chars": self._microbatch_max_batch_chars,
                "batches": total_batches,
                "items": total_items,
                "avg_batch_size": avg_batch_size,
                "avg_wait_ms": avg_wait_ms,
                "coalesced_items": coalesced_items,
                "batch_hit_ratio": batch_hit_ratio,
                "split_count": int(self._microbatch_stats["split_count"]),
                "failed_items": int(self._microbatch_stats["failed_items"]),
                "queued_items": sum(len(queue) for queue in self._microbatch_queues.values()),
            },
        }

    def get_available_endpoints(self) -> List[str]:
        """获取可用端点列表"""
        return [endpoint.id for endpoint in self.endpoints]

    async def close(self) -> None:
        """Close SDK clients and stop pending embedding microbatch tasks."""
        async with self._microbatch_lock:
            timers = list(self._microbatch_timers.values())
            self._microbatch_timers.clear()
            flush_tasks = list(self._microbatch_flush_tasks)
            self._microbatch_flush_tasks.clear()
            pending_futures = list(self._pending_embeddings.values())
            self._pending_embeddings.clear()
            self._microbatch_queues.clear()
            self._microbatch_queue_chars.clear()
            self._microbatch_flushing_buckets.clear()

        for task in timers + flush_tasks:
            task.cancel()
        if timers or flush_tasks:
            await asyncio.gather(*timers, *flush_tasks, return_exceptions=True)

        for future in pending_futures:
            if future.done():
                continue
            future.set_exception(RuntimeError("EmbeddingManager closed"))
            try:
                _ = future.exception()
            except Exception:
                pass

        for client in list(self.clients.values()):
            if client is None:
                continue
            for closer_name in ("aclose", "close"):
                closer = getattr(client, closer_name, None)
                if not callable(closer):
                    continue
                try:
                    result = closer()
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
                break
