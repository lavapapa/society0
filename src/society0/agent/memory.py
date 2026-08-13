"""
模块化记忆系统架构 (Memory as a Service)

这个模块实现了完全独立的记忆系统，封装了存储、召回和遗忘的复杂逻辑，
对外提供简洁的接口，支持情景记忆和语义记忆的管理。

按照resource_management_design.md重构：移除全局状态，采用依赖注入。
"""

import uuid
import hashlib
import math
import asyncio
import os
from typing import Iterable, List, Dict, Any, Optional, Callable, Awaitable, Tuple, Union, Set
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

    # Chroma metadata 不能保存 None；使用统一的最大整数表达开放区间。
    # 查询始终使用 ``visible_until_step > target_step``，因此该值不会与
    # 真实 Tick 混淆，也不需要在读取时扫描整库补全缺省值。
    OPEN_VISIBLE_UNTIL = 2**63 - 1

    def __init__(
        self,
        agent_id: str,
        vector_client,  # 注入的向量存储客户端实例（Chroma PersistentClient）
        branch_id: str = "main",
        embed_call: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
        llm_call: Optional[Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]] = None,
        decay_rate: float = 0.1,
        embedding_dim: int = 512,
        branch_lineage: Optional[List[Tuple[str, int]]] = None,
        source_branch_id: Optional[str] = None,
        write_epoch_id: Optional[str] = None,
        epoch_seq: int = 0,
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
        self.source_branch_id = str(source_branch_id or branch_id)
        self.branch_lineage = [
            (str(source_branch), int(fork_step))
            for source_branch, fork_step in (branch_lineage or [])
        ]
        if write_epoch_id is not None and not str(write_epoch_id).strip():
            raise ValueError("write_epoch_id must be a non-empty string")
        self.write_epoch_id = str(write_epoch_id) if write_epoch_id is not None else f"legacy:{branch_id}"
        self.epoch_seq = int(epoch_seq)
        if self.epoch_seq < 0:
            raise ValueError("epoch_seq must be non-negative")
        # 未显式配置 v4 epoch 的旧调用仍沿用原有 where 形状；一旦携带
        # write_epoch/source 或切换 view，就启用单库多 Tick 可见性约束。
        self._v4_visibility_enabled = write_epoch_id is not None or source_branch_id is not None
        self._memory_view_step: Optional[int] = None
        self._committed_write_epoch_ids: Optional[Set[str]] = None
        self._active_write_epoch_id: Optional[str] = (
            str(write_epoch_id) if write_epoch_id is not None else None
        )
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

    def set_memory_view(
        self,
        target_step: Optional[int] = None,
        branch_lineage: Optional[List[Tuple[str, int]]] = None,
        committed_write_epoch_ids: Optional[Iterable[str]] = None,
        *,
        visible_step: Optional[int] = None,
    ) -> "Memory":
        """切换单库查询视图，不复制、回滚或重建 Chroma 数据库。

        ``committed_write_epoch_ids`` 来自已发布 marker；未发布/失败 epoch
        即使残留在物理库中，也会在返回结果上被过滤。该过滤刻意不放进
        Chroma ``where``，避免把 marker 的提交点伪装成可变 metadata。
        """

        if target_step is None and visible_step is not None:
            target_step = visible_step
        self._v4_visibility_enabled = True
        self._memory_view_step = None if target_step is None else int(target_step)
        if branch_lineage is not None:
            self.branch_lineage = [
                (str(source_branch), int(fork_step))
                for source_branch, fork_step in branch_lineage
            ]
        if committed_write_epoch_ids is None:
            self._committed_write_epoch_ids = None
        else:
            self._committed_write_epoch_ids = {
                str(epoch_id) for epoch_id in committed_write_epoch_ids
            }
        return self

    # 显式别名便于 checkpoint resolver 传递 marker 的 memory_view。
    set_view = set_memory_view

    def set_write_epoch(self, write_epoch_id: str, epoch_seq: int = 0) -> "Memory":
        """切换当前 Tick 的写入 epoch；运行中该 epoch 可被当前视图读取。"""

        normalized = str(write_epoch_id).strip()
        if not normalized:
            raise ValueError("write_epoch_id must be a non-empty string")
        sequence = int(epoch_seq)
        if sequence < 0:
            raise ValueError("epoch_seq must be non-negative")
        if sequence < int(getattr(self, "epoch_seq", 0)):
            raise ValueError("epoch_seq must be monotonic")
        self.write_epoch_id = normalized
        self.epoch_seq = sequence
        self._active_write_epoch_id = normalized
        self._v4_visibility_enabled = True
        return self

    def clear_write_epoch(self) -> "Memory":
        """结束当前未发布写入 epoch；视图仅保留 marker 已提交 epoch。"""

        self._active_write_epoch_id = None
        return self

    def discard_write_epoch(self, write_epoch_id: Optional[str] = None) -> None:
        """清理当前 Agent/分支的未发布 epoch；视图正确性不依赖该清理。"""

        epoch = str(write_epoch_id or getattr(self, "write_epoch_id", ""))
        if not epoch:
            return
        collection = self._get_collection()
        delete = getattr(collection, "delete", None)
        if not callable(delete):
            return
        delete(
            where={
                "$and": [
                    {"agent_id": {"$eq": self.agent_id}},
                    {"branch_id": {"$eq": self.branch_id}},
                    {"write_epoch_id": {"$eq": epoch}},
                ]
            }
        )

    discard_unpublished_epoch = discard_write_epoch

    def _is_epoch_visible(self, metadata: Optional[Dict[str, Any]]) -> bool:
        committed = getattr(self, "_committed_write_epoch_ids", None)
        if committed is None:
            return True
        epoch_id = str((metadata or {}).get("write_epoch_id", ""))
        active = getattr(self, "_active_write_epoch_id", None)
        return epoch_id in committed or (active is not None and epoch_id == active)

    @staticmethod
    def _result_value(records: Any, key: str, default: Any = None) -> Any:
        if isinstance(records, dict):
            return records.get(key, default)
        return getattr(records, key, default)

    def _filter_epoch_records(self, records: Any, *, nested: bool = False) -> Any:
        """按 marker 的 epoch 白名单过滤 Chroma get/query 返回值。"""

        committed = getattr(self, "_committed_write_epoch_ids", None)
        if committed is None:
            return records

        if nested:
            metadatas = self._result_value(records, "metadatas", []) or []
            if not metadatas:
                return records
            first = metadatas[0] if isinstance(metadatas[0], list) else metadatas
            keep = [
                index
                for index, metadata in enumerate(first)
                if self._is_epoch_visible(metadata)
            ]
            if isinstance(records, dict):
                filtered = dict(records)
                for key in ("ids", "documents", "metadatas", "distances", "embeddings"):
                    values = filtered.get(key)
                    if not values:
                        continue
                    if isinstance(values[0], list):
                        filtered[key] = [[values[0][index] for index in keep]]
                    else:
                        filtered[key] = [values[index] for index in keep]
                return filtered
            return records

        metadatas = self._result_value(records, "metadatas", []) or []
        keep = [
            index
            for index, metadata in enumerate(metadatas)
            if self._is_epoch_visible(metadata)
        ]
        if isinstance(records, dict):
            filtered = dict(records)
            for key in ("ids", "documents", "metadatas", "embeddings", "distances"):
                values = filtered.get(key)
                if values is not None:
                    filtered[key] = [values[index] for index in keep]
            return filtered
        return records

    def _branch_rank(self, branch_id: Any) -> int:
        branch = str(branch_id or "")
        if branch == str(getattr(self, "branch_id", "")):
            return 0
        for index, (source_branch, _fork_step) in enumerate(
            getattr(self, "branch_lineage", [])
        ):
            if branch == str(source_branch):
                return index + 1
        return len(getattr(self, "branch_lineage", [])) + 1

    def _filter_branch_records(self, records: Any, *, nested: bool = False) -> Any:
        """按当前分支优先级对同一 logical memory 做 shadowing。

        fork 分支的 update/delete 追加自己的版本并保持源记录不可变；查询
        需要让当前分支版本（含 tombstone）遮蔽祖先版本，避免恢复视图同时
        返回“旧值+新值”或在 fork delete 后重新看到源记忆。
        """

        if nested:
            metadatas = self._result_value(records, "metadatas", []) or []
            if not metadatas:
                return records
            first = metadatas[0] if isinstance(metadatas[0], list) else metadatas
            keep = self._branch_shadow_indices(first)
            if isinstance(records, dict):
                filtered = dict(records)
                for key in ("ids", "documents", "metadatas", "distances", "embeddings"):
                    values = filtered.get(key)
                    if not values:
                        continue
                    if isinstance(values[0], list):
                        filtered[key] = [[values[0][index] for index in keep]]
                    else:
                        filtered[key] = [values[index] for index in keep]
                return filtered
            return records

        metadatas = self._result_value(records, "metadatas", []) or []
        keep = self._branch_shadow_indices(metadatas)
        if isinstance(records, dict):
            filtered = dict(records)
            for key in ("ids", "documents", "metadatas", "embeddings", "distances"):
                values = filtered.get(key)
                if values is not None:
                    filtered[key] = [values[index] for index in keep]
            return filtered
        return records

    def _branch_shadow_indices(self, metadatas: List[Dict[str, Any]]) -> List[int]:
        chosen: Dict[str, Tuple[int, int, int, int]] = {}
        active_epoch = getattr(self, "_active_write_epoch_id", None)
        for index, metadata in enumerate(metadatas):
            logical_id = metadata.get("logical_memory_id")
            if logical_id is None:
                # Legacy records without a logical ID cannot shadow each other.
                chosen[f"__physical__:{index}"] = (
                    self._branch_rank(metadata.get("branch_id")),
                    0,
                    0,
                    index,
                )
                continue
            key = str(logical_id)
            candidate = (
                self._branch_rank(metadata.get("branch_id")),
                0
                if active_epoch is not None
                and str(metadata.get("write_epoch_id", "")) == str(active_epoch)
                else 1,
                -int(metadata.get("created_step", metadata.get("timestamp", 0)) or 0),
                index,
            )
            previous = chosen.get(key)
            if previous is None or candidate < previous:
                chosen[key] = candidate
        return sorted(item[3] for item in chosen.values())

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

    def _memory_where_filter(self, visible_step: Optional[int] = None) -> Dict[str, Any]:
        """构造 Agent、分支谱系和 Tick 可见性过滤条件。"""

        v4_visibility = bool(getattr(self, "_v4_visibility_enabled", False))
        if visible_step is None and v4_visibility:
            visible_step = getattr(self, "_memory_view_step", None)

        branches: List[Dict[str, Any]] = []
        current_branch = [{"branch_id": {"$eq": self.branch_id}}]
        if visible_step is not None:
            current_branch.append({"created_step": {"$lte": int(visible_step)}})
            if v4_visibility:
                current_branch.append(
                    {"visible_until_step": {"$gt": int(visible_step)}}
                )
        branches.append({"$and": current_branch})
        for source_branch, fork_step in self.branch_lineage:
            cutoff = fork_step if visible_step is None else min(fork_step, int(visible_step))
            source_conditions: List[Dict[str, Any]] = [
                {"branch_id": {"$eq": source_branch}},
                {"created_step": {"$lte": cutoff}},
            ]
            if v4_visibility:
                source_conditions.append(
                    {"visible_until_step": {"$gt": cutoff}}
                )
            branches.append(
                {
                    "$and": source_conditions
                }
            )
        branch_filter = branches[0] if len(branches) == 1 else {"$or": branches}
        return {"$and": [{"agent_id": {"$eq": self.agent_id}}, branch_filter]}

    async def _has_retrievable_memories(
        self, visible_step: Optional[int] = None
    ) -> bool:
        """Return False when a cheap collection check proves recall would be empty."""
        async with self._io_lock:
            collection = self._get_collection()
            count = getattr(collection, "count", None)
            if callable(count):
                try:
                    if count() == 0:
                        logger.debug("Collection %s empty, skip recall", self.collection_name)
                        return False
                except Exception as exc:
                    logger.debug("Memory collection count check unavailable for agent %s: %s", self.agent_id, exc)

            get = getattr(collection, "get", None)
            if callable(get):
                try:
                    existing = get(
                        where=self._memory_where_filter(visible_step),
                        limit=None if getattr(self, "_committed_write_epoch_ids", None) is not None else 1,
                    )
                    existing = self._filter_branch_records(
                        self._filter_epoch_records(existing)
                    )
                    ids = existing.get("ids") if isinstance(existing, dict) else getattr(existing, "ids", None)
                    if ids is not None:
                        return bool(ids)
                except Exception as exc:
                    logger.debug("Memory existence check unavailable for agent %s: %s", self.agent_id, exc)

        # If the vector store cannot answer cheaply, preserve prior behavior.
        return True

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

    def _versioned_memory_id(self, logical_id: str, write_epoch_id: str) -> str:
        """返回跨 epoch 稳定、且不覆盖旧版本的物理 ID。"""

        digest = hashlib.sha256(
            (
                f"{self.agent_id}\0{self.branch_id}\0"
                f"{logical_id}\0{write_epoch_id}"
            ).encode("utf-8")
        ).hexdigest()[:16]
        return f"{logical_id}__epoch_{digest}"

    @staticmethod
    def _stored_payload_matches(
        *,
        stored_document: Any,
        stored_metadata: Dict[str, Any],
        document: str,
        metadata: Dict[str, Any],
    ) -> bool:
        return stored_document == document and dict(stored_metadata or {}) == dict(metadata)

    def _resolve_physical_memory_ids(
        self,
        collection: Any,
        logical_ids: List[str],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
    ) -> List[str]:
        """为 v4 epoch 选择稳定物理 ID，并拒绝同 epoch payload 冲突。"""

        if not getattr(self, "_v4_visibility_enabled", False):
            return list(logical_ids)
        get = getattr(collection, "get", None)
        if not callable(get):
            current_epoch = str(getattr(self, "write_epoch_id", f"legacy:{self.branch_id}"))
            return [
                self._versioned_memory_id(logical_id, current_epoch)
                for logical_id in logical_ids
            ]

        storage_where = {
            "$and": [
                {"agent_id": {"$eq": self.agent_id}},
                {"branch_id": {"$eq": self.branch_id}},
            ]
        }

        try:
            existing = get(
                ids=logical_ids,
                include=["documents", "metadatas"],
                where=storage_where,
            )
        except Exception:
            # 旧/极简 fake collection 可能不支持读取；仍保留首写 ID。
            current_epoch = str(getattr(self, "write_epoch_id", f"legacy:{self.branch_id}"))
            return [
                self._versioned_memory_id(logical_id, current_epoch)
                for logical_id in logical_ids
            ]

        existing_ids = self._result_value(existing, "ids", []) or []
        existing_documents = self._result_value(existing, "documents", []) or []
        existing_metadatas = self._result_value(existing, "metadatas", []) or []
        existing_by_id = {
            str(record_id): {
                "document": existing_documents[index] if index < len(existing_documents) else "",
                "metadata": dict(existing_metadatas[index] or {})
                if index < len(existing_metadatas)
                else {},
            }
            for index, record_id in enumerate(existing_ids)
        }

        physical_ids = [None for _ in logical_ids]
        current_epoch = str(getattr(self, "write_epoch_id", f"legacy:{self.branch_id}"))
        for index, logical_id in enumerate(logical_ids):
            stored = existing_by_id.get(str(logical_id))
            physical_id = self._versioned_memory_id(logical_id, current_epoch)
            # 优先读取当前 epoch 的稳定物理版本；不同分支即使逻辑 ID
            # 相同，也不会共用 Chroma 的全局 ID。
            try:
                versioned = get(
                    ids=[physical_id],
                    include=["documents", "metadatas"],
                    where=storage_where,
                )
                versioned_ids = self._result_value(versioned, "ids", []) or []
                if versioned_ids:
                    versioned_documents = self._result_value(versioned, "documents", []) or []
                    versioned_metadatas = self._result_value(versioned, "metadatas", []) or []
                    if not self._stored_payload_matches(
                        stored_document=versioned_documents[0] if versioned_documents else "",
                        stored_metadata=versioned_metadatas[0] if versioned_metadatas else {},
                        document=documents[index],
                        metadata=metadatas[index],
                    ):
                        raise ValueError(
                            f"memory payload conflict for id {logical_id!r} in write epoch {current_epoch!r}"
                        )
                    physical_ids[index] = physical_id
                    continue
            except ValueError:
                raise
            except Exception:
                pass

            if stored is None:
                physical_ids[index] = physical_id
                continue
            stored_epoch = str(stored["metadata"].get("write_epoch_id", ""))
            if stored_epoch == current_epoch:
                if not self._stored_payload_matches(
                    stored_document=stored["document"],
                    stored_metadata=stored["metadata"],
                    document=documents[index],
                    metadata=metadatas[index],
                ):
                    raise ValueError(
                        f"memory payload conflict for id {logical_id!r} in write epoch {current_epoch!r}"
                    )
                physical_ids[index] = logical_id
                continue
            physical_ids[index] = physical_id

        return [str(memory_id) for memory_id in physical_ids]

    def stable_memory_id(
        self,
        idempotency_key: str,
        *,
        memory_type: str = "episodic",
    ) -> str:
        """Build an idempotent ID inside this agent and branch namespace."""

        normalized_key = str(idempotency_key).strip()
        normalized_type = str(memory_type).strip()
        if not normalized_key:
            raise ValueError("idempotency_key must be a non-empty string")
        if not normalized_type:
            raise ValueError("memory_type must be a non-empty string")
        digest = hashlib.sha256(
            (
                f"{self.agent_id}\0{self.branch_id}\0"
                f"{normalized_type}\0{normalized_key}"
            ).encode("utf-8")
        ).hexdigest()
        return self._normalize_memory_id(f"{normalized_type}_{digest}")
        
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

    async def _generate_embeddings(
        self,
        texts: List[str],
        *,
        trace: Optional[Dict[str, Any]] = None,
    ) -> List[List[float]]:
        """生成向量；禁用默认回退，主通路失败将抛出异常。"""
        if self.embed_call:
            try:
                metadata = self._embedding_trace_metadata(trace)
                try:
                    result = await self.embed_call(texts, self.embedding_dim, metadata=metadata)
                except TypeError as exc:
                    if "metadata" not in str(exc):
                        raise
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

    def _embedding_trace_metadata(self, trace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        metadata = {key: value for key, value in dict(trace or {}).items() if value is not None}
        metadata.setdefault("agent_id", self.agent_id)
        return metadata

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

        # Chroma 不接受 None。开放可见区间统一编码为最大整数，where
        # 只需比较 ``visible_until_step > target_step``，不会混合类型。
        visible_until = sanitized_metadata.pop("visible_until_step", self.OPEN_VISIBLE_UNTIL)
        if visible_until is None:
            visible_until = self.OPEN_VISIBLE_UNTIL
        try:
            visible_until = int(visible_until)
        except (TypeError, ValueError) as exc:
            raise ValueError("visible_until_step must be an integer or None") from exc

        source_branch = sanitized_metadata.pop(
            "source_branch_id", getattr(self, "source_branch_id", self.branch_id)
        )
        source_branch = str(source_branch or self.branch_id)
        write_epoch = sanitized_metadata.pop(
            "write_epoch_id", getattr(self, "write_epoch_id", f"legacy:{self.branch_id}")
        )
        write_epoch = str(write_epoch)
        epoch_seq = sanitized_metadata.pop("epoch_seq", getattr(self, "epoch_seq", 0))
        try:
            epoch_seq = int(epoch_seq)
        except (TypeError, ValueError) as exc:
            raise ValueError("epoch_seq must be an integer") from exc

        # 添加系统字段；系统字段覆盖同名用户值，避免伪造 Agent/分支归属。
        sanitized_metadata.update(
            {
                "memory_type": memory_type,
                "timestamp": timestamp,
                "created_step": int(timestamp),
                "visible_until_step": visible_until,
                "base_importance": importance,
                "branch_id": self.branch_id,
                "source_branch_id": source_branch,
                "write_epoch_id": write_epoch,
                "epoch_seq": epoch_seq,
                "agent_id": self.agent_id,
            }
        )
        return sanitized_metadata

    async def inspect_memory_ids(
        self,
        memory_ids: List[str],
        *,
        entries: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, List[str]]:
        """按 ID 检查记忆是否已持久化，并可核对其 payload。

        ``add_memories_batch`` 使用稳定 ID 做 upsert，但 Thread 的 receipt
        与向量库不共享事务。重试前必须先读回这些 ID；只要已存在的记录与
        pending entry 的正文和元数据一致，就可以跳过 embedding/upsert。

        Args:
            memory_ids: 需要确认的稳定记忆 ID，顺序会保留在返回值中。
            entries: 可选的 pending entries。提供时会严格核对 content 及
                持久化 metadata，并将不一致的 ID 放入 ``mismatched_ids``。

        Returns:
            ``existing_ids``、``missing_ids`` 和 ``mismatched_ids`` 三个列表。
            ``mismatched_ids`` 只在提供 entries 时计算；调用方应将其视为
            不可安全覆盖的冲突。
        """

        requested_ids = [str(memory_id).strip() for memory_id in memory_ids]
        if any(not memory_id for memory_id in requested_ids):
            raise ValueError("memory_ids must contain non-empty strings")
        if len(set(requested_ids)) != len(requested_ids):
            raise ValueError("memory_ids must be unique")

        expected_by_id: Dict[str, Dict[str, Any]] = {}
        if entries is not None:
            if not isinstance(entries, list):
                raise ValueError("entries must be a list")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("entries must contain dictionaries")
                raw_id = entry.get("memory_id")
                if raw_id is None or not str(raw_id).strip():
                    raise ValueError("entries must include non-empty memory_id")
                entry_id = str(raw_id).strip()
                if entry_id not in requested_ids:
                    raise ValueError("entries memory_id must be listed in memory_ids")
                if entry_id in expected_by_id:
                    raise ValueError("entries memory_id values must be unique")
                expected_by_id[entry_id] = entry
            if set(expected_by_id) != set(requested_ids):
                raise ValueError("entries must cover every requested memory_id")

        if not requested_ids:
            return {"existing_ids": [], "missing_ids": [], "mismatched_ids": []}

        async with self._io_lock:
            collection = self._get_collection()
            records = collection.get(
                ids=requested_ids,
                include=["documents", "metadatas"],
                where=self._memory_where_filter(),
            )
            records = self._filter_branch_records(
                self._filter_epoch_records(records)
            )

            # v4 跨 epoch 的第二个版本拥有物理 versioned ID；receipt 仍只持有
            # 逻辑 ID。首个按逻辑 ID 的 get 可能命中失败 epoch，必要时再按
            # 当前 view 读取候选记录并以 logical_memory_id 建索引。
            if getattr(self, "_v4_visibility_enabled", False):
                visible_ids = self._result_value(records, "ids", []) or []
                if len(visible_ids) < len(requested_ids):
                    try:
                        candidates = collection.get(
                            include=["documents", "metadatas"],
                            where=self._memory_where_filter(),
                        )
                        candidates = self._filter_branch_records(
                            self._filter_epoch_records(candidates)
                        )
                        candidate_ids = self._result_value(candidates, "ids", []) or []
                        candidate_documents = self._result_value(candidates, "documents", []) or []
                        candidate_metadatas = self._result_value(candidates, "metadatas", []) or []
                        visible_records: Dict[str, Tuple[str, Dict[str, Any]]] = {}
                        current_epoch = str(
                            getattr(self, "write_epoch_id", f"legacy:{self.branch_id}")
                        )
                        for index, (record_id, metadata) in enumerate(
                            zip(candidate_ids, candidate_metadatas)
                        ):
                            logical_id = str(metadata.get("logical_memory_id", record_id))
                            candidate = (
                                candidate_documents[index]
                                if index < len(candidate_documents)
                                else "",
                                dict(metadata or {}),
                            )
                            previous = visible_records.get(logical_id)
                            if previous is None or (
                                candidate[1].get("write_epoch_id") == current_epoch
                                and previous[1].get("write_epoch_id") != current_epoch
                            ):
                                visible_records[logical_id] = candidate
                        ids_list = list(self._result_value(records, "ids", []) or [])
                        docs_list = list(self._result_value(records, "documents", []) or [])
                        metadata_list = list(self._result_value(records, "metadatas", []) or [])
                        for logical_id in requested_ids:
                            if logical_id in visible_records and logical_id not in ids_list:
                                document, metadata = visible_records[logical_id]
                                ids_list.append(logical_id)
                                docs_list.append(document)
                                metadata_list.append(metadata)
                        records = {
                            "ids": ids_list,
                            "documents": docs_list,
                            "metadatas": metadata_list,
                        }
                    except Exception as exc:
                        logger.debug("Memory logical ID fallback unavailable for agent %s: %s", self.agent_id, exc)

        if isinstance(records, dict):
            stored_ids = records.get("ids") or []
            documents = records.get("documents") or []
            metadatas = records.get("metadatas") or []
        else:
            stored_ids = getattr(records, "ids", None) or []
            documents = getattr(records, "documents", None) or []
            metadatas = getattr(records, "metadatas", None) or []

        stored_by_id: Dict[str, Dict[str, Any]] = {}
        for index, raw_id in enumerate(stored_ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            record_id = str(metadata.get("logical_memory_id", raw_id))
            if record_id not in requested_ids:
                continue
            stored_by_id[record_id] = {
                "content": documents[index] if index < len(documents) else "",
                "metadata": dict(metadata or {}),
            }

        existing_ids = [memory_id for memory_id in requested_ids if memory_id in stored_by_id]
        missing_ids = [memory_id for memory_id in requested_ids if memory_id not in stored_by_id]
        mismatched_ids: List[str] = []

        for memory_id in existing_ids:
            expected = expected_by_id.get(memory_id)
            if expected is None:
                continue
            expected_importance = expected.get("importance")
            if expected_importance is None:
                expected_importance = 3.0
            expected_metadata = self._build_metadata(
                memory_type=expected.get("memory_type", "episodic"),
                timestamp=expected.get("timestamp", 0),
                importance=expected_importance,
                metadata=expected.get("metadata"),
            )
            if getattr(self, "_v4_visibility_enabled", False):
                expected_metadata["logical_memory_id"] = memory_id
            stored = stored_by_id[memory_id]
            if (
                stored["content"] != expected.get("content", "")
                or stored["metadata"] != expected_metadata
            ):
                mismatched_ids.append(memory_id)

        return {
            "existing_ids": existing_ids,
            "missing_ids": missing_ids,
            "mismatched_ids": mismatched_ids,
        }

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
        metadata: Optional[Dict[str, Any]] = None,
        trace: Optional[Dict[str, Any]] = None,
        memory_id: Optional[str] = None,
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
        return await self._add_memory(
            "episodic",
            content,
            timestamp,
            importance,
            metadata,
            trace=trace,
            memory_id=memory_id,
        )
        
    async def add_semantic_memory(
        self,
        content: str,
        timestamp: int,
        importance: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace: Optional[Dict[str, Any]] = None,
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
        return await self._add_memory("semantic", content, timestamp, importance, metadata, trace=trace)

    async def _add_memory(
        self,
        memory_type: str,
        content: str,
        timestamp: int,
        importance: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace: Optional[Dict[str, Any]] = None,
        memory_id: Optional[str] = None,
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
                    "memory_id": memory_id,
                }
            ],
            fire_and_forget=False,
            trace=trace,
        )
        return ids[0] if ids else ""

    async def add_memories_batch(
        self,
        entries: List[Dict[str, Any]],
        *,
        fire_and_forget: bool = False,
        return_task: bool = False,
        trace: Optional[Dict[str, Any]] = None,
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

        memory_ids = [
            str(item.get("memory_id") or self._new_memory_id())
            for item in entries
        ]
        if any(not memory_id.strip() for memory_id in memory_ids):
            raise ValueError("memory_id must be a non-empty string")
        if len(set(memory_ids)) != len(memory_ids):
            raise ValueError("memory_id values must be unique within one batch")
        use_upsert = any(item.get("memory_id") is not None for item in entries)

        async def _persist_batch():
            try:
                texts = [item.get("content", "") for item in entries]
                embedding_trace = dict(trace or {})
                if embedding_trace.get("thread_id") is not None:
                    embedding_trace["memory_ids"] = list(memory_ids)
                    if len(memory_ids) == 1:
                        embedding_trace["memory_id"] = memory_ids[0]
                embeddings = await self._generate_embeddings(
                    texts,
                    trace=embedding_trace,
                )
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
                    if getattr(self, "_v4_visibility_enabled", False):
                        record_metadata["logical_memory_id"] = memory_ids[idx]
                    metadatas.append(record_metadata)
                    docs.append(content)
                    entries[idx]["_embedding"] = embeddings[idx]
                    entries[idx]["_importance"] = importance

                async with self._io_lock:
                    collection = self._get_collection()
                    physical_ids = self._resolve_physical_memory_ids(
                        collection,
                        memory_ids,
                        docs,
                        metadatas,
                    )
                    write = collection.upsert if use_upsert else collection.add
                    write(
                        ids=physical_ids,
                        documents=docs,
                        embeddings=[item["_embedding"] for item in entries],
                        metadatas=metadatas,
                    )

                logger.debug(
                    "Agent %s added %s memories to %s",
                    self.agent_id,
                    len(memory_ids),
                    self.collection_name,
                )

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

    def _logical_memory_where(
        self, logical_id: str, visible_step: Optional[int] = None
    ) -> Dict[str, Any]:
        # 写入版本只允许关闭当前分支记录；祖先谱系必须保持不可变，不能
        # 因为 fork 分支的 update/delete 改写源分支。
        branch_conditions: List[Dict[str, Any]] = [
            {"agent_id": {"$eq": self.agent_id}},
            {"branch_id": {"$eq": self.branch_id}},
        ]
        if visible_step is not None:
            branch_conditions.extend(
                [
                    {"created_step": {"$lte": int(visible_step)}},
                    {"visible_until_step": {"$gt": int(visible_step)}},
                ]
            )
        return {
            "$and": [
                {"$and": branch_conditions},
                {"logical_memory_id": {"$eq": str(logical_id)}},
            ]
        }

    def _close_logical_versions(
        self,
        collection: Any,
        logical_id: str,
        visible_until_step: int,
    ) -> None:
        get = getattr(collection, "get", None)
        update = getattr(collection, "update", None)
        if not callable(get) or not callable(update):
            return
        records = get(
            include=["metadatas"],
            # 新版本与旧版本可以拥有同一 created_step；只关闭严格早于
            # 生效 Tick 的记录，避免刚追加的版本立即被截断。
            where=self._logical_memory_where(logical_id, max(visible_until_step - 1, 0)),
        )
        ids = self._result_value(records, "ids", []) or []
        metadatas = self._result_value(records, "metadatas", []) or []
        for index, record_id in enumerate(ids):
            metadata = dict(metadatas[index] or {}) if index < len(metadatas) else {}
            current_until = metadata.get("visible_until_step", self.OPEN_VISIBLE_UNTIL)
            try:
                current_until = int(current_until)
            except (TypeError, ValueError):
                current_until = self.OPEN_VISIBLE_UNTIL
            if current_until <= visible_until_step:
                continue
            metadata["visible_until_step"] = int(visible_until_step)
            update(ids=[record_id], metadatas=[metadata])

    def _version_record_id(
        self,
        logical_id: str,
        *,
        operation: str,
        timestamp: int,
        content: str,
    ) -> str:
        digest = hashlib.sha256(
            (
                f"{self.agent_id}\0{self.branch_id}\0{logical_id}\0"
                f"{self.write_epoch_id}\0{operation}\0{timestamp}\0{content}"
            ).encode("utf-8")
        ).hexdigest()[:20]
        return f"{logical_id}__{operation}_{digest}"

    async def _append_version(
        self,
        logical_id: str,
        *,
        content: str,
        timestamp: int,
        importance: float,
        metadata: Optional[Dict[str, Any]],
        operation: str,
        deleted: bool = False,
        trace: Optional[Dict[str, Any]] = None,
    ) -> str:
        self._v4_visibility_enabled = True
        embeddings = await self._generate_embeddings([content], trace=trace)
        if not embeddings:
            raise RuntimeError("Failed to generate embedding for memory version")
        record_metadata = self._build_metadata(
            memory_type=(metadata or {}).get("memory_type", "episodic"),
            timestamp=int(timestamp),
            importance=float(importance),
            metadata=metadata,
        )
        record_metadata["logical_memory_id"] = str(logical_id)
        record_metadata["deleted"] = bool(deleted)
        physical_id = self._version_record_id(
            str(logical_id),
            operation=operation,
            timestamp=int(timestamp),
            content=content,
        )
        async with self._io_lock:
            collection = self._get_collection()
            writer = getattr(collection, "upsert", None) or getattr(collection, "add", None)
            if not callable(writer):
                raise ValueError("Injected collection must provide add or upsert")
            writer(
                ids=[physical_id],
                documents=[content],
                embeddings=[embeddings[0]],
                metadatas=[record_metadata],
            )
            self._close_logical_versions(collection, str(logical_id), int(timestamp))
        return str(logical_id)

    async def update_memory(
        self,
        memory_id: str,
        *,
        content: str,
        timestamp: int,
        importance: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        visible_step: Optional[int] = None,
        trace: Optional[Dict[str, Any]] = None,
    ) -> str:
        """以追加版本方式更新记忆，保留旧 Tick 的可见记录。"""

        step = int(timestamp if visible_step is None else visible_step)
        score = 3.0 if importance is None else float(importance)
        return await self._append_version(
            str(memory_id),
            content=content,
            timestamp=step,
            importance=score,
            metadata=metadata,
            operation="update",
            trace=trace,
        )

    async def delete_memory(
        self,
        memory_id: str,
        *,
        visible_step: int,
        trace: Optional[Dict[str, Any]] = None,
    ) -> str:
        """追加 tombstone，并关闭旧版本的可见区间。"""

        return await self._append_version(
            str(memory_id),
            content="",
            timestamp=int(visible_step),
            importance=0.0,
            metadata=None,
            operation="delete",
            deleted=True,
            trace=trace,
        )

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
        visible_step: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                async with self._io_lock:
                    collection = self._get_collection()
                    if hasattr(collection, "count") and collection.count() == 0:
                        logger.debug("Collection %s empty, skip recall", self.collection_name)
                        return None

                    result = collection.query(
                        query_embeddings=[query_embedding],
                        n_results=max(1, top_k * 2),
                        include=["documents", "metadatas", "distances"],
                        where=self._memory_where_filter(visible_step),
                    )
                    return self._filter_branch_records(
                        self._filter_epoch_records(result, nested=True),
                        nested=True,
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
        current_step: Optional[int] = None,
        trace: Optional[Dict[str, Any]] = None,
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

            if not await self._has_retrievable_memories(current_step):
                return []

            # 生成查询向量
            query_embeddings = await self._generate_embeddings([query], trace=trace)
            if not query_embeddings:
                return []
            query_embedding = query_embeddings[0]

            # 执行向量搜索（带重试和降级）
            search_results = await self._query_collection_with_retry(
                query_embedding=query_embedding,
                top_k=top_k,
                visible_step=current_step,
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
                if metadata.get("deleted") is True:
                    continue
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

            logger.debug(
                "Agent %s retrieved %s memories from %s",
                self.agent_id,
                len(results),
                self.collection_name,
            )

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
            records = self._filter_branch_records(
                self._filter_epoch_records(records)
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
