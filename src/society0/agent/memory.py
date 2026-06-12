"""
模块化记忆系统架构 (Memory as a Service)

这个模块实现了完全独立的记忆系统，封装了存储、召回和遗忘的复杂逻辑，
对外提供简洁的接口，支持情景记忆和语义记忆的管理。

按照resource_management_design.md重构：移除全局状态，采用依赖注入。
"""

import uuid
import math
import asyncio
import os
from typing import List, Dict, Any, Optional, Callable, Awaitable, Tuple, Union, Set
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


def _debug_print(*values: Any, sep: str = " ", end: str = "\n", file: Any = None, flush: bool = False) -> None:
    """Route legacy debug prints through logging without writing to stdout."""
    if file is not None:
        import builtins

        builtins.print(*values, sep=sep, end=end, file=file, flush=flush)
        return

    message = sep.join(str(value) for value in values)
    if end and end != "\n":
        message += end
    logger.debug(message)


print = _debug_print

# 全局状态已被移除 - 不再使用全局向量存储客户端

@dataclass
class MemoryEntry:
    """记忆片段数据结构"""
    id: str
    type: str  # "episodic" or "semantic"
    content: str
    embedding: List[float]
    timestamp: int  # 记忆创建时的时间步 (step)
    base_importance: float  # 基础重要性评分 0-5
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典格式用于存储"""
        return {
            "id": self.id,
            "type": self.type,
            "content": self.content,
            "embedding": self.embedding,
            "timestamp": self.timestamp,
            "base_importance": self.base_importance,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryEntry":
        """从字典创建记忆片段"""
        return cls(
            id=data["id"],
            type=data["type"],
            content=data["content"],
            embedding=data["embedding"],
            timestamp=data["timestamp"],
            base_importance=data["base_importance"],
            metadata=data.get("metadata", {})
        )

# 删除了 init_global_milvus 和 get_global_milvus 全局状态函数

async def ollama_embed(texts: List[str], dimensions: int = 512) -> Dict[str, Any]:
    """
    使用Ollama SDK进行文本向量化
    
    Args:
        texts: 待向量化的文本列表
        dimensions: 向量维度，默认512
        
    Returns:
        {"result": List[List[float]], "model": str, "dimensions": int}
    """
    try:
        from ollama import AsyncClient

        # 优先级：OLLAMA_HOST > EMBEDDING_BASE_URL > 本机默认端点
        host = (
            os.getenv("OLLAMA_HOST")
            or os.getenv("EMBEDDING_BASE_URL")
            or "http://localhost:11434"
        ).strip()
        if host.endswith("/v1"):
            host = host[:-3].rstrip("/")

        model = (os.getenv("EMBEDDING_MODEL") or "nomic-embed-text").strip()
        client = AsyncClient(host=host)
        try:
            response = await client.embed(
                model=model,
                input=texts,
                dimensions=dimensions,
            )
        except TypeError:
            # Some Ollama SDK versions don't accept `dimensions`.
            response = await client.embed(
                model=model,
                input=texts,
            )

        embeddings = []
        if isinstance(response, dict):
            embeddings = response.get("embeddings") or []
        else:
            embeddings = getattr(response, "embeddings", None) or []
            
        return {
            "result": embeddings,
            "model": model,
            "dimensions": dimensions
        }
    except ImportError:
        raise ImportError("Please install ollama: pip install ollama")
    except Exception as e:
        logger.error(f"Failed to generate embeddings with Ollama: {e}")
        raise

class Memory:
    """
    记忆系统核心类 - Memory as a Service

    封装所有记忆的存储、召回和遗忘逻辑，提供简洁的对外接口。
    每个Memory实例绑定到特定的Agent，使用共享Collection+metadata过滤实现数据隔离。

    按照resource_management_design.md重构：
    - 接收注入的向量存储客户端（Chroma PersistentClient），移除对全局状态的依赖
    - 支持注入的embed_call和llm_call函数
    """

    def __init__(
        self,
        agent_id: str,
        vector_client,  # 注入的向量存储客户端实例（Chroma PersistentClient）
        branch_id: str = "main",
        embed_call: Optional[Callable[[List[str], int], Awaitable[Dict[str, Any]]]] = None,
        llm_call: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None,
        decay_rate: float = 0.1,
        embedding_dim: int = 512
    ):
        """
        初始化Memory实例

        Args:
            agent_id: Agent唯一标识，用于元数据过滤
            vector_client: 注入的Chroma PersistentClient实例 (替代全局状态)
            branch_id: 分支标识，用于支持未来的平行时空能力
            embed_call: 异步向量化函数 (可注入，替代硬编码ollama调用)
            llm_call: LLM调用函数，用于重要性评估
            decay_rate: 遗忘曲线衰减率
            embedding_dim: 向量维度
        """
        self.agent_id = agent_id
        self.vector_client = vector_client  # 使用注入的客户端（Chroma）
        self.branch_id = branch_id
        self.embed_call = embed_call or ollama_embed  # 可注入embed函数
        self.llm_call = llm_call
        self.decay_rate = decay_rate
        self.embedding_dim = embedding_dim
        self.collection_name = self._load_collection_name()
        self._collection = None
        self._fallback_embedding_fn = None
        self._pending_write_tasks: Set[asyncio.Task] = set()
        self._retrieve_pending_wait_sec = self._load_retrieve_pending_wait_sec()
        self._io_lock = asyncio.Lock()

        # 确保客户端已注入
        if self.vector_client is None:
            raise ValueError("vector_client must be provided - global state is no longer supported")

        # 初始化Collection
        self._ensure_collection()

    @staticmethod
    def _load_collection_name() -> str:
        """读取共享记忆集合名。"""
        raw = (os.getenv("CHROMA_MEMORY_COLLECTION_NAME") or "agent_memories").strip()
        return raw or "agent_memories"

    @staticmethod
    def _load_retrieve_pending_wait_sec() -> float:
        """读取 retrieve 前等待 pending 写任务的时间窗口（秒）。"""
        raw = (os.getenv("MEMORY_RETRIEVE_PENDING_WAIT_SEC") or "1.0").strip()
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 1.0
        return max(0.0, value)

    async def _await_pending_writes_before_retrieve(self) -> None:
        """
        在同一 agent 的 retrieve 前，短暂等待后台 memory 写入任务。

        目标是减少 fire-and-forget 写入与下一次读之间的竞态窗口；
        超时后继续，不阻塞主流程。
        """
        if self._retrieve_pending_wait_sec <= 0:
            return
        if not self._pending_write_tasks:
            return

        pending_tasks = [task for task in self._pending_write_tasks if not task.done()]
        if not pending_tasks:
            return

        try:
            done, pending = await asyncio.wait(
                pending_tasks,
                timeout=self._retrieve_pending_wait_sec,
                return_when=asyncio.ALL_COMPLETED,
            )
            # 主动检查已完成任务异常（done callback 也会记录）
            for task in done:
                try:
                    _ = task.exception()
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            if pending:
                logger.warning(
                    "Agent %s: %s pending memory write task(s) not finished within %.2fs before retrieve",
                    self.agent_id,
                    len(pending),
                    self._retrieve_pending_wait_sec,
                )
        except Exception as exc:
            logger.debug(
                "Agent %s: pending memory write wait failed (ignored): %s",
                self.agent_id,
                exc,
            )

    def _memory_where_filter(self) -> Dict[str, Any]:
        """当前 agent 在共享 collection 下的过滤条件。"""
        return {
            "$and": [
                {"agent_id": {"$eq": self.agent_id}},
                {"branch_id": {"$eq": self.branch_id}},
            ]
        }

    def _normalize_memory_id(self, raw_id: str) -> str:
        """
        将 memory_id 规范为带 agent 前缀，避免共享 collection 下 ID 冲突。
        """
        rid = (raw_id or "").strip()
        if not rid:
            rid = uuid.uuid4().hex[:12]
        prefix = f"mem_{self.agent_id}_"
        if rid.startswith(prefix):
            return rid
        if rid.startswith("mem_"):
            return f"{prefix}{rid[4:]}"
        return f"{prefix}{rid}"

    def _new_memory_id(self) -> str:
        return self._normalize_memory_id(uuid.uuid4().hex[:12])
        
    def _ensure_collection(self):
        """确保共享记忆 Collection 可用。"""
        if self._collection is not None:
            return

        client = self.vector_client
        if not hasattr(client, "get_or_create_collection"):
            raise ValueError(
                "Injected vector store client必须提供get_or_create_collection接口"
            )

        collection = client.get_or_create_collection(
            name=self.collection_name,
            metadata={
                "source": "agent_memory",
                "scope": "shared_multi_agent",
            },
        )
        self._collection = collection
        logger.debug("Shared memory collection ready for agent %s: %s", self.agent_id, self.collection_name)

    def _get_collection(self):
        """访问缓存的 Chroma Collection。"""
        if self._collection is None:
            self._ensure_collection()
        return self._collection

    async def _generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """生成向量；禁用默认回退，主通路失败将抛出异常。"""
        if self.embed_call:
            try:
                result = await self.embed_call(texts, self.embedding_dim)
                embeddings = result.get("result") if result else None
                if embeddings:
                    self._update_embedding_dim(embeddings[0])
                    return embeddings
            except Exception as exc:
                logger.error("Primary embedding generation failed and fallback is disabled: %s", exc)
                raise

        # 如果没有可用的 embed_call，则直接抛错（禁用默认回退）
        raise RuntimeError("Embedding generation unavailable: no primary embed_call")

    def _generate_embeddings_via_fallback(self, texts: List[str]) -> List[List[float]]:
        """使用 Chroma 默认 ONNX 模型生成嵌入。"""
        if not texts:
            return []

        if self._fallback_embedding_fn is None:
            try:
                from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

                self._fallback_embedding_fn = DefaultEmbeddingFunction()
                logger.info("Initialized Chroma default embedding function as fallback")
            except ImportError as exc:
                logger.error("chromadb embedding functions unavailable: %s", exc)
                raise

        embeddings = self._fallback_embedding_fn(texts)
        if embeddings:
            self._update_embedding_dim(embeddings[0])
        return embeddings

    def _update_embedding_dim(self, vector: List[float]) -> None:
        """根据实际向量长度动态调整 embedding 维度。"""
        if vector is None:
            return
        try:
            new_dim = len(vector)
        except TypeError:
            return
        if new_dim == 0:
            return
        if new_dim != self.embedding_dim:
            logger.debug(
                "Adjusting embedding dimension from %s to %s for agent %s",
                self.embedding_dim,
                new_dim,
                self.agent_id,
            )
            self.embedding_dim = new_dim

    def _build_metadata(
        self,
        memory_type: str,
        timestamp: int,
        importance: float,
        metadata: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        构建存储到向量数据库的元数据。

        ChromaDB metadata 限制：只支持 str, int, float, bool, None 类型
        复杂类型（list, dict）会被自动序列化为 JSON 字符串
        """
        import json

        # 序列化用户提供的 metadata，确保符合 ChromaDB 类型限制
        sanitized_metadata = {}
        if metadata:
            for key, value in metadata.items():
                if value is None or isinstance(value, (str, int, float, bool)):
                    # 简单类型直接保留
                    sanitized_metadata[key] = value
                else:
                    # 复杂类型（list, dict, 等）序列化为 JSON 字符串
                    try:
                        sanitized_metadata[key] = json.dumps(value, ensure_ascii=False)
                        logger.debug(f"Serialized metadata field '{key}' from {type(value).__name__} to JSON string")
                    except (TypeError, ValueError) as e:
                        # 序列化失败时，转为字符串
                        sanitized_metadata[key] = str(value)
                        logger.warning(f"Failed to serialize metadata field '{key}', used str() instead: {e}")

        # 添加系统字段
        sanitized_metadata.update(
            {
                "memory_type": memory_type,
                "timestamp": timestamp,
                "base_importance": importance,
                "branch_id": self.branch_id,
                "agent_id": self.agent_id,
            }
        )
        return sanitized_metadata

    @staticmethod
    def _distance_to_similarity(distance: Optional[float]) -> float:
        """将 Chroma 返回的距离值转换为相似度分数（越大越相似）。"""
        if distance is None:
            return 0.0
        similarity = 1.0 - float(distance)
        return max(-1.0, min(1.0, similarity))

    async def add_episodic_memory(
        self,
        content: str,
        timestamp: int,
        importance: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        添加情景记忆
        
        Args:
            content: 记忆内容
            timestamp: 时间步
            importance: 重要性评分，如果为None则自动评估
            metadata: 元数据
            
        Returns:
            记忆条目的ID
        """
        return await self._add_memory("episodic", content, timestamp, importance, metadata)
        
    async def add_semantic_memory(
        self,
        content: str,
        timestamp: int,
        importance: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        添加语义记忆
        
        Args:
            content: 记忆内容
            timestamp: 时间步  
            importance: 重要性评分，如果为None则自动评估
            metadata: 元数据
            
        Returns:
            记忆条目的ID
        """
        return await self._add_memory("semantic", content, timestamp, importance, metadata)

    async def _add_memory(
        self,
        memory_type: str,
        content: str,
        timestamp: int,
        importance: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> str:
        """内部方法：添加记忆（单条封装到批量通道）"""
        ids = await self.add_memories_batch(
            [
                {
                    "memory_type": memory_type,
                    "content": content,
                    "timestamp": timestamp,
                    "importance": importance,
                    "metadata": metadata,
                }
            ],
            fire_and_forget=False,
        )
        return ids[0] if ids else ""

    async def add_memories_batch(
        self,
        entries: List[Dict[str, Any]],
        *,
        fire_and_forget: bool = False,
        return_task: bool = False,
    ) -> Union[List[str], Tuple[List[str], asyncio.Task]]:
        """
        批量添加记忆，支持 fire-and-forget 模式以减少主流程等待。

        Args:
            entries: 每项包含 memory_type/content/timestamp/importance/metadata
            fire_and_forget: True 时在后台任务中完成，立即返回预生成的 memory_id 列表
            return_task: True 时（仅 fire_and_forget 模式）一并返回后台任务句柄，便于上层监控
        """
        if not entries:
            return []

        memory_ids = [self._new_memory_id() for _ in entries]

        async def _persist_batch():
            try:
                texts = [item.get("content", "") for item in entries]
                embeddings = await self._generate_embeddings(texts)
                if not embeddings or len(embeddings) < len(entries):
                    raise RuntimeError("Failed to generate embeddings for memories batch")

                metadatas = []
                docs = []
                for idx, item in enumerate(entries):
                    memory_type = item.get("memory_type", "episodic")
                    content = item.get("content", "")
                    timestamp = item.get("timestamp", 0)
                    importance = item.get("importance")
                    if importance is None:
                        importance = await self._evaluate_importance(content, memory_type)
                    record_metadata = self._build_metadata(
                        memory_type=memory_type,
                        timestamp=timestamp,
                        importance=importance,
                        metadata=item.get("metadata"),
                    )
                    metadatas.append(record_metadata)
                    docs.append(content)
                    entries[idx]["_embedding"] = embeddings[idx]
                    entries[idx]["_importance"] = importance

                async with self._io_lock:
                    collection = self._get_collection()
                    collection.add(
                        ids=memory_ids,
                        documents=docs,
                        embeddings=[item["_embedding"] for item in entries],
                        metadatas=metadatas,
                    )

                # 调试输出
                for mem_id, item in zip(memory_ids, entries):
                    content = item.get("content", "")
                    print(f"💾 [MEMORY] Agent {self.agent_id}: Added {item.get('memory_type','episodic')} memory")
                    print(f"   Memory ID: {mem_id}")
                    print(f"   Content: {content[:60]}..." if len(content) > 60 else f"   Content: {content}")
                    print(f"   Importance: {item.get('_importance', 0):.2f}")
                    print(f"   Timestamp: {item.get('timestamp', 0)}")

            except Exception as exc:
                logger.error("Failed to add memories batch for agent %s: %s", self.agent_id, exc)
                raise

        if fire_and_forget:
            task = asyncio.create_task(_persist_batch())
            self._pending_write_tasks.add(task)

            def _log_task_error(t: asyncio.Task):
                self._pending_write_tasks.discard(t)
                try:
                    exc = t.exception()
                    if exc:
                        logger.error("Background memory batch failed for agent %s: %s", self.agent_id, exc)
                except asyncio.CancelledError:
                    logger.warning("Background memory batch cancelled for agent %s", self.agent_id)
                except Exception:
                    logger.exception("Error handling memory batch task result for agent %s", self.agent_id)

            task.add_done_callback(_log_task_error)
            return (memory_ids, task) if return_task else memory_ids

        await _persist_batch()
        return memory_ids

    @staticmethod
    def _is_retryable_collection_error(exc: Exception) -> bool:
        message = str(exc).lower()
        markers = (
            "hnsw segment reader",
            "nothing found on disk",
            "segment reader",
            "error executing plan",
            "internal error",
        )
        return any(marker in message for marker in markers)

    async def _query_collection_with_retry(
        self,
        *,
        query_embedding: List[float],
        top_k: int,
    ) -> Optional[Dict[str, Any]]:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                async with self._io_lock:
                    collection = self._get_collection()
                    if hasattr(collection, "count") and collection.count() == 0:
                        logger.debug("Collection %s empty, skip recall", self.collection_name)
                        return None

                    return collection.query(
                        query_embeddings=[query_embedding],
                        n_results=max(1, top_k * 2),
                        include=["documents", "metadatas", "distances"],
                        where=self._memory_where_filter(),
                    )
            except Exception as exc:
                retryable = self._is_retryable_collection_error(exc)
                if retryable and attempt < max_attempts:
                    logger.warning(
                        "Agent %s collection query failed (attempt %s/%s), rebuilding collection cache: %s",
                        self.agent_id,
                        attempt,
                        max_attempts,
                        exc,
                    )
                    async with self._io_lock:
                        self._collection = None
                        self._ensure_collection()
                    await asyncio.sleep(0.05 * (2 ** (attempt - 1)))
                    continue

                if retryable:
                    logger.warning(
                        "Agent %s collection query degraded to empty memories after %s attempts: %s",
                        self.agent_id,
                        max_attempts,
                        exc,
                    )
                    return None

                raise

        return None

    async def retrieve(
        self,
        query: str,
        top_k: int = 10,
        current_step: Optional[int] = None
    ) -> List[str]:
        """
        记忆召回 - 核心检索功能
        
        Args:
            query: 查询文本
            top_k: 返回结果数量
            current_step: 当前时间步，用于计算当前相关性
            
        Returns:
            最相关的记忆内容列表
        """
        try:
            await self._await_pending_writes_before_retrieve()

            # 生成查询向量
            query_embeddings = await self._generate_embeddings([query])
            if not query_embeddings:
                return []
            query_embedding = query_embeddings[0]

            # 执行向量搜索（带重试和降级）
            search_results = await self._query_collection_with_retry(
                query_embedding=query_embedding,
                top_k=top_k,
            )
            if not search_results:
                return []

            ids = (search_results.get("ids") or [[]])[0]
            if not ids:
                return []

            documents = (search_results.get("documents") or [[]])[0]
            metadatas = (search_results.get("metadatas") or [[]])[0]
            distances = (search_results.get("distances") or [[]])[0]

            # 构建候选集，同时用 (memory_type, content) 做一次去重（避免重复记忆被多次返回）
            candidates = {}
            for idx, content in enumerate(documents):
                metadata = metadatas[idx] if idx < len(metadatas) else {}
                distance = distances[idx] if idx < len(distances) else None
                similarity_score = self._distance_to_similarity(distance)
                base_importance = float(metadata.get("base_importance", 0.0))
                timestamp = int(metadata.get("timestamp", 0))
                memory_type = str(metadata.get("memory_type", "episodic"))

                if current_step is not None:
                    time_decay = math.exp(-self.decay_rate * max(0, current_step - timestamp))
                    current_relevance = base_importance * time_decay
                else:
                    current_relevance = base_importance

                final_score = similarity_score + current_relevance * 0.1
                key = (memory_type, content)
                # 保留得分更高的版本
                existing = candidates.get(key)
                if not existing or final_score > existing["score"]:
                    candidates[key] = {"content": content, "score": final_score}

            sorted_candidates = sorted(candidates.values(), key=lambda x: x["score"], reverse=True)
            results = [item["content"] for item in sorted_candidates[:top_k]]

            # 🔧 DEBUG: Memory retrieval monitoring
            print(f"🧠 [MEMORY] Agent {self.agent_id}: Retrieved memories")
            print(f"   Query: {query[:50]}..." if len(query) > 50 else f"   Query: {query}")
            print(f"   Found: {len(results)} memories")
            for i, content in enumerate(results[:3], 1):
                content_preview = content[:40] + "..." if len(content) > 40 else content
                print(f"   {i}. {content_preview}")

            return results

        except Exception as e:
            logger.error(f"Memory retrieval failed: {e}")
            raise RuntimeError(f"Memory retrieval failed: {e}")
    
    async def _evaluate_importance(self, content: str, memory_type: str) -> float:
        """使用LLM评估记忆重要性"""
        if not self.llm_call:
            # 如果没有LLM调用函数，使用启发式规则
            return 3.0  # 默认moderate重要性

        # ⚠️ 临时调整：高并发实验中关闭记忆重要性评估的 LLM 调用，固定返回默认分值
        # try:
        #     prompt = f"""请评估以下{memory_type}记忆的重要性，使用0-5的评分标准，其中3表示中等重要性。
        #
        # 记忆内容: {content}
        #
        # 评估标准:
        # - 0-1: 非常不重要，日常琐事
        # - 2-3: 一般重要性，常规信息  
        # - 4-5: 非常重要，关键信息或深刻洞见
        #
        # 请只返回一个0-5之间的数字评分。"""
        #     response = await self.llm_call({
        #         "messages": [
        #             {"role": "system", "content": "你是一个记忆重要性评估专家。"},
        #             {"role": "user", "content": prompt}
        #         ]
        #     })
        #     content_text = response.get("content", "3.0")
        #     score = float(content_text.strip())
        #     return max(0.0, min(5.0, score))
        # except Exception as e:
        #     logger.error(f"Error evaluating importance: {e}")
        #     return 3.0

        return 3.0  # 默认重要性

    def export_memories(self) -> List[Dict[str, Any]]:
        """导出所有记忆数据"""
        collection = self._get_collection()
        try:
            records = collection.get(
                include=["documents", "metadatas", "embeddings"],
                where=self._memory_where_filter(),
            )
        except Exception as exc:
            logger.error("Failed to export memories for agent %s: %s", self.agent_id, exc)
            raise

        ids = records.get("ids")
        documents = records.get("documents")
        metadatas = records.get("metadatas")
        embeddings = records.get("embeddings")

        ids = ids if ids is not None else []
        documents = documents if documents is not None else []
        metadatas = metadatas if metadatas is not None else []
        embeddings = embeddings if embeddings is not None else []

        exported: List[Dict[str, Any]] = []
        for idx, memory_id in enumerate(ids):
            content = documents[idx] if idx < len(documents) else ""
            metadata = metadatas[idx] if idx < len(metadatas) else {}
            embedding = embeddings[idx] if idx < len(embeddings) else None

            if embedding is not None:
                self._update_embedding_dim(embedding)

            exported.append(
                {
                    "id": memory_id,
                    "type": metadata.get("memory_type", metadata.get("type", "")),
                    "content": content,
                    "embedding": embedding,
                    "timestamp": metadata.get("timestamp", 0),
                    "base_importance": metadata.get("base_importance", 0.0),
                    "metadata": metadata,
                }
            )

        return exported

    def import_memories(self, memories: List[Dict[str, Any]]):
        """导入记忆数据"""
        if not memories:
            return

        collection = self._get_collection()
        ids: List[str] = []
        documents: List[str] = []
        embeddings: List[Optional[List[float]]] = []
        metadatas: List[Dict[str, Any]] = []

        for memory in memories:
            memory_id = self._normalize_memory_id(memory.get("id") or uuid.uuid4().hex[:12])
            content = memory.get("content", "")
            embedding = memory.get("embedding")
            timestamp = int(memory.get("timestamp", 0))
            base_importance = float(memory.get("base_importance", 0.0))
            memory_type = memory.get("type", "episodic")
            metadata = self._build_metadata(
                memory_type=memory_type,
                timestamp=timestamp,
                importance=base_importance,
                metadata=memory.get("metadata"),
            )

            ids.append(memory_id)
            documents.append(content)
            embeddings.append(embedding)
            metadatas.append(metadata)

            if embedding is not None:
                self._update_embedding_dim(embedding)

        has_all_embeddings = all(embed is not None for embed in embeddings)
        add_kwargs: Dict[str, Any] = {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
        }
        if has_all_embeddings:
            add_kwargs["embeddings"] = embeddings

        try:
            collection.add(**add_kwargs)
        except Exception as exc:
            logger.error("Failed to import memories for agent %s: %s", self.agent_id, exc)
            raise

    async def apply_event(self, event):
        """
        Apply a MemoryChangeEvent during event replay to restore memory state.

        This method handles memory-specific events during event sourcing replay,
        ensuring that the memory system can be accurately restored from event logs.

        Args:
            event: MemoryChangeEvent instance to apply

        Note:
            This method is called by World._apply_memory_change during event replay
        """
        try:
            from ..events import MemoryChangeEvent

            if not isinstance(event, MemoryChangeEvent):
                logger.warning(f"Memory.apply_event received non-memory event: {type(event)}")
                return

            operation = getattr(event, 'operation', 'unknown')
            memory_id = getattr(event, 'memory_id', None)
            memory_type = getattr(event, 'memory_type', '')
            content = getattr(event, 'content', None)

            logger.debug(f"Applying memory event for agent {self.agent_id}: {operation}")

            if operation == "add":
                # Restore a memory entry
                if not memory_id or not content:
                    logger.warning("Memory add event missing required fields (memory_id, content)")
                    return
                memory_id = self._normalize_memory_id(str(memory_id))

                # Extract memory data from event
                memory_data = content if isinstance(content, dict) else {"content": str(content)}

                # Create memory entry for restoration
                if "embedding" not in memory_data:
                    logger.warning("Memory event missing embedding data, skipping...")
                    return

                memory_entry = MemoryEntry(
                    id=memory_id,
                    type=memory_type or "episodic",
                    content=memory_data.get("content", ""),
                    embedding=memory_data.get("embedding", []),
                    timestamp=memory_data.get("timestamp", 0),
                    base_importance=memory_data.get("base_importance", 3.0),
                    metadata=memory_data.get("metadata", {})
                )

                # Insert directly into Chroma（避免重新嵌入）
                try:
                    collection = self._get_collection()
                    record_metadata = self._build_metadata(
                        memory_type=memory_entry.type,
                        timestamp=memory_entry.timestamp,
                        importance=memory_entry.base_importance,
                        metadata=memory_entry.metadata,
                    )

                    if memory_entry.embedding:
                        self._update_embedding_dim(memory_entry.embedding)

                    collection.add(
                        ids=[memory_entry.id],
                        documents=[memory_entry.content],
                        embeddings=[memory_entry.embedding],
                        metadatas=[record_metadata],
                    )

                    logger.debug(f"Restored memory entry {memory_id} for agent {self.agent_id}")

                except Exception as e:
                    logger.error(f"Failed to restore memory entry {memory_id}: {e}")

            elif operation == "delete":
                # Remove a memory entry
                if not memory_id:
                    logger.warning("Memory delete event missing memory_id")
                    return
                memory_id = self._normalize_memory_id(str(memory_id))

                try:
                    collection = self._get_collection()
                    delete_kwargs: Dict[str, Any] = {"ids": [memory_id]}
                    delete_kwargs["where"] = self._memory_where_filter()
                    collection.delete(**delete_kwargs)

                    logger.debug(f"Deleted memory entry {memory_id} during replay for agent {self.agent_id}")

                except Exception as e:
                    logger.error(f"Failed to delete memory entry {memory_id} during replay: {e}")

            elif operation == "update":
                # Update a memory entry (if supported)
                logger.debug(f"Memory update operation not fully implemented yet for agent {self.agent_id}")

            else:
                logger.warning(f"Unknown memory operation during replay: {operation}")

        except Exception as e:
            logger.error(f"Error applying memory event for agent {self.agent_id}: {e}")
            # Don't raise to avoid breaking the entire replay process
