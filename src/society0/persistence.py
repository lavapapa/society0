"""Society0 v4 persistence and live Chroma resource management."""

from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING, Mapping, Sequence
from pathlib import Path
import asyncio
import copy
import gzip
import io
import json
import time
import shutil
import logging
import os
import uuid
import hashlib
from datetime import datetime
import threading

from .state_proxy import DictProxy, ListProxy
from .incremental_checkpoint import (
    PersistenceSchema,
    PersistenceKind,
    SealedTickDelta,
    V4CheckpointStore,
    _WILDCARD,
    _thaw_json,
)

if TYPE_CHECKING:
    from .core_data import World
    from typing import Any as Schedule

logger = logging.getLogger(__name__)


class PersistenceManager:
    """
    Unified persistence manager for the new World-based architecture.

    Handles v4 replacement/append checkpoint components and one live Chroma
    database for the run.  Threads and vector records are referenced by v4
    manifests; neither is copied into a checkpoint.

    按照resource_management_design.md，新增向量存储客户端管理职责。
    """

    CHECKPOINT_VERSION = V4CheckpointStore.VERSION
    DIAGNOSTIC_ENCODING = "gzip-json"
    DIAGNOSTIC_COMPRESSION_LEVEL = 6

    def __init__(self, save_dir: str):
        """
        Initialize persistence manager with save directory.

        Args:
            save_dir: Directory to save all simulation data
        """
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

        # Agent Thread evidence is a separate append-only component.  The
        # checkpoint stores only immutable references to closed threads.
        from .agent.thread_store import AgentThreadStore

        self.agent_thread_store = AgentThreadStore(self.save_dir)

        # 立即初始化_chroma_client属性为None，确保属性存在
        self._chroma_client = None
        self._restore_failed = False
        self._close_completed = False

        # Create subdirectories according to new design
        self.checkpoints_dir = self.save_dir / "checkpoints"
        self.metadata_dir = self.save_dir / "metadata"
        self.events_dir = self.save_dir / "events"
        self.diffs_dir = self.save_dir / "diffs"
        self.interviews_dir = self.save_dir / "interviews"

        for dir_path in [
            self.checkpoints_dir,
            self.metadata_dir,
            self.events_dir,
            self.diffs_dir,
            self.interviews_dir,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)

        self._diff_lock = threading.Lock()
        self._chroma_init_lock = threading.Lock()

        # v4 state is consumed only from sealed deltas after the World has been
        # configured.  The manager never snapshots a World during publish.
        self._v4_enabled = False
        self._v4_world = None
        self._v4_schema: Optional[PersistenceSchema] = None
        self._v4_store: Optional[V4CheckpointStore] = None
        self._v4_checkpoint_every = 1
        self._v4_epoch: list[SealedTickDelta] = []
        self._v4_publish_lock: Optional[asyncio.Lock] = None
        self._v4_root_published = False
        self._v4_run_id = uuid.uuid4().hex
        self._v4_branch_id = "main"
        self._v4_branch_lineage: list[tuple[str, int]] = []
        self._v4_committed_memory_epoch_ids: set[str] = set()
        self._v4_pending_memory_epoch_ids: set[str] = set()

        # Paths for Chroma persistence
        self.chroma_store_path = self.save_dir / "chroma_store"
        self.chroma_store_path.mkdir(parents=True, exist_ok=True)
        self._runtime_mode = self._load_runtime_mode()
        self._tmpfs_root = self._load_tmpfs_root()
        self._cleanup_tmpfs_on_close = self._load_tmpfs_cleanup_on_close()
        self.chroma_runtime_path = self._resolve_chroma_runtime_path()
        self._using_fallback_runtime = self.chroma_runtime_path != self.chroma_store_path
        if self._using_fallback_runtime:
            self._prepare_chroma_runtime_store()

        # Initialize metadata
        default_metadata = {
            "save_dir": str(self.save_dir),
            "created_at": datetime.now().isoformat(),
            "event_counter": 0,
            "total_steps": 0,
            "last_checkpoint_step": -1,
            "architecture_version": "unified_state_v4",
            "experiment_name": "simulation_experiment",
        }
        metadata_file = self.metadata_dir / "metadata.json"
        loaded_metadata = self._load_metadata() if metadata_file.exists() else {}
        default_metadata.update(loaded_metadata)
        self.experiment_metadata = default_metadata

        # Chroma 只在 memory/vector store 被实际请求时创建。
        # 纯规则或无 LLM 的 CodeSchedule 不应在初始化阶段承担 Chroma 依赖成本。

    @staticmethod
    def _load_runtime_mode() -> str:
        raw = (os.getenv("CHROMA_RUNTIME_MODE") or "tmpfs").strip().lower()
        if raw not in {"tmpfs", "disk"}:
            return "tmpfs"
        return raw

    @staticmethod
    def _load_tmpfs_root() -> Path:
        raw = (os.getenv("CHROMA_TMPFS_ROOT") or "/dev/shm/society0_chroma").strip()
        return Path(raw) if raw else Path("/dev/shm/society0_chroma")

    @staticmethod
    def _load_tmpfs_cleanup_on_close() -> bool:
        raw = (os.getenv("CHROMA_TMPFS_CLEANUP_ON_CLOSE") or "1").strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def _resolve_chroma_runtime_path(self) -> Path:
        if self._runtime_mode != "tmpfs":
            return self.chroma_store_path

        try:
            self._tmpfs_root.mkdir(parents=True, exist_ok=True)
            runtime_id = hashlib.sha1(str(self.save_dir).encode("utf-8")).hexdigest()[:16]
            candidate = self._tmpfs_root / f"exp_{runtime_id}"
            if candidate.resolve() == self.chroma_store_path.resolve():
                return self.chroma_store_path
            return candidate
        except Exception as exc:
            logger.warning("Failed to setup tmpfs runtime path, fallback to disk store: %s", exc)
            return self.chroma_store_path

    def _prepare_chroma_runtime_store(self) -> None:
        """
        准备 tmpfs 运行目录。
        - 若磁盘 store 有历史数据：复制到 runtime（支持 resume）
        - 否则创建空 runtime
        """
        runtime_path = self.chroma_runtime_path
        if runtime_path == self.chroma_store_path:
            return

        try:
            if runtime_path.exists():
                shutil.rmtree(runtime_path, ignore_errors=True)

            has_store_data = any(self.chroma_store_path.iterdir())
            if has_store_data:
                self._copy_directory(self.chroma_store_path, runtime_path)
                logger.info(
                    "Prepared tmpfs Chroma runtime from store: %s <- %s",
                    runtime_path,
                    self.chroma_store_path,
                )
            else:
                runtime_path.mkdir(parents=True, exist_ok=True)
                logger.info("Prepared empty tmpfs Chroma runtime: %s", runtime_path)

            if not os.getenv("SQLITE_TMPDIR"):
                os.environ["SQLITE_TMPDIR"] = str(runtime_path)
        except Exception as exc:
            logger.warning("Failed to prepare tmpfs runtime store, fallback to disk mode: %s", exc)
            self.chroma_runtime_path = self.chroma_store_path
            self._using_fallback_runtime = False

    def save_interview_record(self, step: int, node_id: str, operator_id: str, record: Dict[str, Any]) -> None:
        """
        追加写入单条访谈记录（JSONL）。

        Args:
            step: 当前步骤
            node_id: 所属 node
            operator_id: 所属 operator
            record: 要写入的记录（字典）
        """
        try:
            file_path = self.interviews_dir / f"step_{step:06d}__node_{node_id}__op_{operator_id}.jsonl"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with file_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning(
                f"Failed to save interview record (step={step}, node={node_id}, op={operator_id}): {exc}"
            )

    def _ensure_chroma_client(self):
        """初始化（或返回已存在的）Chroma PersistentClient，保证仅创建一次。"""
        self._assert_usable()
        with self._chroma_init_lock:
            if self._chroma_client is not None:
                return self._chroma_client

            self._chroma_client = self._create_chroma_client()
            self._sync_chroma_to_store()
            logger.info(
                "PersistenceManager initialized with dedicated Chroma client: runtime=%s store=%s",
                self.chroma_runtime_path,
                self.chroma_store_path,
            )

            return self._chroma_client

    def _create_chroma_client(self):
        """
        创建并返回专属于此次仿真的 Chroma PersistentClient 实例。

        Returns:
            chromadb.PersistentClient 实例
        """
        try:
            import chromadb
        except ImportError:
            logger.error("chromadb not available. Please install: pip install chromadb")
            raise ImportError("Please install chromadb: pip install chromadb")

        runtime_path = self.chroma_runtime_path if self._using_fallback_runtime else self.chroma_store_path
        runtime_path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(runtime_path))
        logger.debug("Chroma client created at runtime path: %s", runtime_path)
        return client

    def get_chroma_client(self):
        """显式获取 Chroma PersistentClient。"""
        self._assert_usable()
        return self._ensure_chroma_client()

    def _assert_usable(self) -> None:
        if self._restore_failed:
            raise RuntimeError(
                "PersistenceManager is unusable after a failed Chroma restore rollback"
            )

    def disable_after_restore_failure(self) -> None:
        """Prevent further persistence use after its World/Chroma pair is uncertain."""
        self._chroma_client = None
        self._restore_failed = True

    def _sync_chroma_to_store(self) -> None:
        """如果使用临时运行目录，确保向量存储同步回实验目录。"""
        runtime_path = self.chroma_runtime_path
        if not runtime_path:
            return
        if not runtime_path.exists():
            return
        if not self._using_fallback_runtime:
            return

        try:
            self._copy_directory(runtime_path, self.chroma_store_path)
            logger.debug(
                "Synced Chroma runtime store -> primary store: %s -> %s",
                runtime_path,
                self.chroma_store_path,
            )
        except Exception as exc:
            logger.warning("Failed to sync Chroma store to primary path: %s", exc)
            raise

    @staticmethod
    def _copy_directory(source: Path, target: Path) -> None:
        """复制目录（覆盖目标）。"""
        if not source.exists():
            raise FileNotFoundError(f"Source directory does not exist: {source}")
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)

    def seed_chroma_store_from(self, source_run: str | Path) -> bool:
        """用来源运行的逻辑记忆库初始化一个新的恢复目录。

        这是跨目录恢复时的一次性分支初始化，不属于逐 Tick checkpoint。
        后续可见性仍由 v4 的目标 Tick、分支谱系和已提交写入 epoch 限定。
        """

        self._assert_usable()
        with self._chroma_init_lock:
            if self._chroma_client is not None:
                raise RuntimeError("Chroma client 已初始化，不能再导入来源记忆库")
            source_store = Path(source_run).resolve() / "chroma_store"
            if not source_store.is_dir() or not any(source_store.iterdir()):
                return False
            if any(self.chroma_store_path.iterdir()):
                raise ValueError("恢复目标的 Chroma store 必须为空")
            if source_store.resolve() == self.chroma_store_path.resolve():
                raise ValueError("恢复来源与目标不能使用同一个 Chroma store")

            self._copy_directory(source_store, self.chroma_store_path)
            if self._using_fallback_runtime:
                self._copy_directory(
                    self.chroma_store_path,
                    self.chroma_runtime_path,
                )
            return True
    
    def _load_metadata(self) -> Dict[str, Any]:
        """Load experiment metadata from disk."""
        metadata_file = self.metadata_dir / "metadata.json"
        
        if metadata_file.exists():
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)
                logger.info("Loaded existing experiment metadata")
                return metadata
            except Exception as e:
                logger.warning(f"Failed to load metadata: {e}")
        
        # Return default metadata
        return {
            "created_at": time.time(),
            "experiment_name": "simulation_experiment",
            "total_steps": 0,
            "last_checkpoint_step": -1,
            "architecture_version": "unified_state_v4"
        }
    
    def _save_metadata(self) -> None:
        """Save experiment metadata to disk."""
        metadata_file = self.metadata_dir / "metadata.json"
        
        self.experiment_metadata["last_updated"] = time.time()
        
        try:
            self._atomic_write_json(metadata_file, self.experiment_metadata, indent=2)
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
            raise

    @staticmethod
    def _atomic_write_json(path: Path, payload: Dict[str, Any], *, indent: Optional[int] = None) -> None:
        """Write JSON through a sibling temporary file and publish it with rename."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    indent=indent,
                    separators=None if indent is not None else (",", ":"),
                    default=str,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(path)
            PersistenceManager._fsync_directory(path.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    @classmethod
    def _atomic_write_diagnostic_gzip_json(
        cls,
        path: Path,
        payload: Dict[str, Any],
    ) -> None:
        """Atomically write the explicitly non-recoverable diagnostic gzip."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(
            f".{path.name}.{uuid.uuid4().hex}.tmp.json.gz"
        )
        try:
            with temp_path.open("xb") as raw_handle:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=cls.DIAGNOSTIC_COMPRESSION_LEVEL,
                    fileobj=raw_handle,
                    mtime=0,
                ) as gzip_handle:
                    with io.TextIOWrapper(gzip_handle, encoding="utf-8") as handle:
                        json.dump(
                            payload,
                            handle,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            default=str,
                        )
                        handle.write("\n")
                        handle.flush()
                raw_handle.flush()
                os.fsync(raw_handle.fileno())
            temp_path.replace(path)
            cls._fsync_directory(path.parent)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        """Durably publish a newly renamed checkpoint component."""

        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _normalize_step(step: Any) -> int:
        """Validate a checkpoint step without coercing caller input."""

        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            raise ValueError("step must be a non-negative integer")
        return step

    @staticmethod
    def canonical_sha256(value: Any) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @classmethod
    def _plain_snapshot_value(cls, value: Any) -> Any:
        if isinstance(value, DictProxy):
            value = value._target_dict
        elif isinstance(value, ListProxy):
            value = value._target_list
        if isinstance(value, dict):
            return {
                key: cls._plain_snapshot_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [cls._plain_snapshot_value(item) for item in value]
        return value

    @classmethod
    def _derived_environment_snapshot(cls, snapshot: Any) -> Dict[str, Any]:
        """Return environment-only snapshot data.

        ``World.environment_data['state']`` is the canonical state store.  The
        default ``Environment.snapshot()`` also exposes that mapping under a
        top-level ``state`` key, which would write the same state twice and
        make two fields appear authoritative.  Diagnostics retain only custom
        environment data alongside a read-only state summary.
        """
        plain = cls._plain_snapshot_value(snapshot)
        if not isinstance(plain, dict):
            raise ValueError("Environment snapshot must be a mapping")
        plain.pop("state", None)
        return plain

    # ------------------------------------------------------------------
    # v4 incremental checkpoint path
    # ------------------------------------------------------------------

    @staticmethod
    def _v4_path_value(state: Mapping[str, Any], path: Sequence[Any]) -> tuple[bool, Any]:
        current: Any = state
        for part in path:
            if not isinstance(current, Mapping) or part not in current:
                return False, None
            current = current[part]
        return True, current

    def _v4_root_entries(self, state: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Build step-0 operations from declared state once.

        The root is the only place where the complete initial state may be
        inspected.  Every later publish consumes only ``SealedTickDelta``.
        Transient declarations are intentionally omitted; defaults are applied
        when a v4 World is restored.
        """

        assert self._v4_schema is not None
        entries: list[dict[str, Any]] = []
        sequence = 0
        # A merged schema contains one source declaration per canonical root.
        # Expand only the Agent-id wildcard against the already-captured root
        # state.  This is bootstrap-only work; later publishes consume sealed
        # journal entries and never inspect the complete World.
        source_schemas = getattr(self._v4_schema, "source_schemas", (self._v4_schema,))
        for source in source_schemas:
            root = tuple(source.root_path)
            concrete_roots: list[tuple[tuple[Any, ...], tuple[Any, ...]]] = []
            if _WILDCARD not in root:
                concrete_roots.append((root, root))
            else:
                wildcard_index = root.index(_WILDCARD)
                prefix = root[:wildcard_index]
                suffix = root[wildcard_index + 1 :]
                found, container = self._v4_path_value(state, prefix)
                if not found or not isinstance(container, Mapping):
                    continue
                for key in sorted(container, key=lambda item: str(item)):
                    concrete = prefix + (key,) + suffix
                    concrete_roots.append((concrete, root))

            for concrete_root, _ in concrete_roots:
                for path, rule in source.rules.items():
                    # The Agent-id wildcard belongs to this source root and
                    # has already been expanded above.  Only skip wildcards
                    # in the relative path (nested dynamic containers cannot
                    # be materialized without concrete keys).
                    relative_start = len(root)
                    if any(part is _WILDCARD for part in path[relative_start:]):
                        continue
                    if path[: len(root)] != root:
                        continue
                    relative = path[len(root) :]
                    concrete_path = concrete_root + relative
                    if rule.kind is PersistenceKind.TRANSIENT:
                        continue
                    found, value = self._v4_path_value(state, concrete_path)
                    if not found:
                        continue
                    if rule.granularity == "entry":
                        if not isinstance(value, Mapping):
                            raise TypeError(
                                f"entry-granularity state must be a mapping: {concrete_path!r}"
                            )
                        if not value:
                            # Empty entity maps still belong to the canonical
                            # root shape. Without this structural operation a
                            # restored Env would try to recreate the container
                            # after the journal was attached but before a Tick
                            # delta existed.
                            entries.append(
                                {
                                    "path": list(concrete_path),
                                    "operation": "set",
                                    "value": {},
                                    "sequence": sequence,
                                }
                            )
                            sequence += 1
                            continue
                        for key, item in value.items():
                            entries.append(
                                {
                                    "path": list(concrete_path + (key,)),
                                    "operation": "set",
                                    "value": item,
                                    "sequence": sequence,
                                }
                            )
                            sequence += 1
                        continue
                    # Root bootstrap is allowed to materialize an append-only
                    # container once. Tick writes still enforce append-only
                    # operations through StateDeltaJournal.
                    entries.append(
                        {
                            "path": list(concrete_path),
                            "operation": "set",
                            "value": value,
                            "sequence": sequence,
                        }
                    )
                    sequence += 1
        return entries

    def configure_v4(
        self,
        world: 'World',
        declarations: PersistenceSchema | Sequence[PersistenceSchema] | Mapping[str, Any],
        *,
        checkpoint_every: int = 1,
    ) -> PersistenceSchema:
        """Enable v4 persistence for a World and compile its declarations."""

        if isinstance(checkpoint_every, bool) or not isinstance(checkpoint_every, int) or checkpoint_every < 1:
            raise ValueError("checkpoint_every must be a positive integer")
        if isinstance(declarations, (list, tuple)):
            declarations = PersistenceSchema.merge(*declarations)
        compiled = world.configure_persistence(declarations)
        if not isinstance(compiled, PersistenceSchema):
            raise TypeError("World.configure_persistence must return PersistenceSchema")
        self._v4_enabled = True
        self._v4_world = world
        self._v4_schema = compiled
        self._v4_store = V4CheckpointStore(self.save_dir, branch_id=self._v4_branch_id)
        self._v4_checkpoint_every = checkpoint_every
        self._v4_epoch = []
        self._v4_publish_lock = asyncio.Lock()
        self._v4_root_published = 0 in self._v4_store.available_steps()
        if self._v4_root_published:
            manifest = self._v4_store.resolve(0)["manifest"]
            self._v4_run_id = str(manifest["run_id"])
            latest_step = self._v4_store.resolve()["step"]
            self._v4_committed_memory_epoch_ids = (
                self._v4_store.committed_memory_epoch_ids(latest_step)
            )
        else:
            # 跨目录恢复会把来源 checkpoint 的记忆视图挂在新 World 上。
            # 新运行的 root 必须继承该提交边界，不能把已恢复记忆重置为空。
            self._v4_branch_id = str(
                getattr(world, "_memory_branch_id", self._v4_branch_id)
            )
            self._v4_branch_lineage = list(
                getattr(world, "_memory_branch_lineage", ()) or ()
            )
            self._v4_committed_memory_epoch_ids = set(
                getattr(world, "_committed_memory_epoch_ids", set()) or set()
            )
        return compiled

    def _v4_root_metadata(
        self,
        environment_data: Mapping[str, Any],
        agents_data: Mapping[str, Any],
        agent_types: Mapping[str, Any],
        *,
        resume_identity: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        environment_data = dict(environment_data)
        environment_data.pop("state", None)
        metadata = {
            "schema_version": 1,
            "run_id": self._v4_run_id,
            "branch_id": self._v4_branch_id,
            "agents_data": dict(agents_data),
            "agent_types": dict(agent_types),
            "environment_data": environment_data,
            "persistence_schema": self._v4_schema.schema
            if self._v4_schema is not None
            else None,
            "persistence_root_path": list(self._v4_schema.root_path)
            if self._v4_schema is not None
            else ["environment", "state"],
            "persistence_schemas": list(self._v4_schema.declaration_payloads())
            if self._v4_schema is not None
            else [],
        }
        if resume_identity is not None:
            metadata["resume_identity"] = dict(resume_identity)
        return metadata

    @staticmethod
    def _v4_merge_epoch(deltas: Sequence[SealedTickDelta]) -> SealedTickDelta:
        if not deltas:
            raise ValueError("cannot publish an empty checkpoint epoch")
        if len(deltas) == 1:
            return deltas[0]
        replacements: list[Mapping[str, Any]] = []
        appends: list[Mapping[str, Any]] = []
        sequence = 0
        for delta in deltas:
            operations = [
                ("replacement", dict(item)) for item in delta.replacements
            ] + [("append", dict(item)) for item in delta.appends]
            operations.sort(key=lambda item: item[1].get("sequence", 0))
            for kind, operation in operations:
                operation["sequence"] = sequence
                (replacements if kind == "replacement" else appends).append(operation)
                sequence += 1
        return SealedTickDelta(
            step=deltas[-1].step,
            replacements=tuple(replacements),
            appends=tuple(appends),
            write_epoch_ids=tuple(
                epoch_id
                for delta in deltas
                for epoch_id in delta.write_epoch_ids
            ),
            annotations={
                key: value
                for delta in deltas
                for key, value in (delta.annotations or {}).items()
            },
        )

    @staticmethod
    async def _await_v4_publication(callable_: Any, /, *args: Any, **kwargs: Any) -> Any:
        """等待发布线程越过唯一提交点，避免取消后后台偷偷发布 marker。"""

        worker = asyncio.create_task(asyncio.to_thread(callable_, *args, **kwargs))
        try:
            return await asyncio.shield(worker)
        except asyncio.CancelledError:
            # marker rename 前取消：worker 会完成清理并抛错；marker rename 后
            # 取消：发布已经提交，必须把成功结果交给调用方完成 step 推进。
            return await worker

    async def publish_root(self, world: 'World', schedule: 'Schedule') -> Dict[str, Any]:
        """Publish the immutable step-0 v4 root through a worker thread."""

        del schedule  # schedule progress is not part of v4 state deltas
        if not self._v4_enabled or self._v4_store is None or self._v4_schema is None:
            raise RuntimeError("v4 persistence is not configured")
        if world is not self._v4_world:
            raise ValueError("publish_root World does not match configure_v4 World")
        if self._v4_publish_lock is None:
            self._v4_publish_lock = asyncio.Lock()
        async with self._v4_publish_lock:
            if self._v4_root_published:
                raise RuntimeError("v4 root checkpoint is already published")
            # Capture canonical World data once, before entering the worker.
            # No subsequent delta publish has access to this World reference.
            environment_data = self._plain_snapshot_value(world.environment_data)
            agents_data = self._plain_snapshot_value(world.agents_data)
            agent_types = self._plain_snapshot_value(getattr(world, "_agent_types", {}) or {})
            # Agent identity/type metadata is immutable bootstrap metadata; the
            # declared Agent state itself is represented by root operations so
            # subsequent deltas can replace it without copying the World.
            metadata_agents_data = {
                str(agent_id): {
                    key: value
                    for key, value in agent_data.items()
                    if key not in {"state", "properties", "reminders"}
                }
                for agent_id, agent_data in agents_data.items()
                if isinstance(agent_data, Mapping)
            }
            canonical_state = {
                "environment": {
                    "state": environment_data.get("state") or {},
                },
                "agents": {
                    str(agent_id): {
                        "state": (raw_data or {}).get("state") or {},
                        "properties": (raw_data or {}).get("properties") or {},
                        "reminders": (raw_data or {}).get("reminders") or [],
                    }
                    for agent_id, raw_data in agents_data.items()
                    if isinstance(raw_data, Mapping)
                },
            }
            entries = self._v4_root_entries(canonical_state)
            metadata = self._v4_root_metadata(
                environment_data,
                metadata_agents_data,
                agent_types,
                resume_identity=getattr(world, "_resume_identity", None),
            )
            checkpoint_id = uuid.uuid4().hex
            memory_target_step = int(world.step)
            inherited_memory_epochs = set(
                self._v4_committed_memory_epoch_ids
            )

            def publish_root_transaction() -> Dict[str, Any]:
                thread_manifest = self.agent_thread_store.publish_epoch_manifest(
                    checkpoint_id,
                    [0],
                )
                return self._v4_store.publish_root(
                    entries,
                    metadata=metadata,
                    step=0,
                    checkpoint_id=checkpoint_id,
                    thread_manifest=thread_manifest,
                    memory_view={
                        "branch_id": self._v4_branch_id,
                        "target_step": memory_target_step,
                        "write_epoch_ids": sorted(inherited_memory_epochs),
                    },
                )

            marker = await self._await_v4_publication(publish_root_transaction)
            self._v4_root_published = True
            world.set_memory_checkpoint_view(
                target_step=memory_target_step,
                branch_id=self._v4_branch_id,
                branch_lineage=self._v4_branch_lineage,
                committed_write_epoch_ids=inherited_memory_epochs,
            )
            return marker

    async def publish_delta(
        self,
        delta: SealedTickDelta,
        schedule: 'Schedule',
        *,
        force: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Queue a sealed delta and publish complete epochs with backpressure."""

        del schedule
        if not self._v4_enabled or self._v4_store is None:
            raise RuntimeError("v4 persistence is not configured")
        if not isinstance(delta, SealedTickDelta):
            raise TypeError("publish_delta expects a SealedTickDelta")
        if not self._v4_root_published:
            raise RuntimeError("publish_root must complete before publish_delta")
        if self._v4_publish_lock is None:
            self._v4_publish_lock = asyncio.Lock()
        async with self._v4_publish_lock:
            self._v4_epoch.append(delta)
            self._v4_pending_memory_epoch_ids.update(delta.write_epoch_ids)
            if len(self._v4_epoch) < self._v4_checkpoint_every and not force:
                if self._v4_world is not None:
                    self._v4_world.set_memory_checkpoint_view(
                        target_step=delta.step,
                        branch_id=self._v4_branch_id,
                        branch_lineage=self._v4_branch_lineage,
                        committed_write_epoch_ids=(
                            self._v4_committed_memory_epoch_ids
                            | self._v4_pending_memory_epoch_ids
                        ),
                    )
                return None
            epoch = tuple(self._v4_epoch)
            combined = self._v4_merge_epoch(epoch)
            checkpoint_id = uuid.uuid4().hex

            def publish_epoch_transaction() -> Dict[str, Any]:
                thread_manifest = self.agent_thread_store.publish_epoch_manifest(
                    checkpoint_id,
                    [item.step for item in epoch],
                )
                return self._v4_store.publish(
                    combined,
                    checkpoint_id=checkpoint_id,
                    thread_manifest=thread_manifest,
                    memory_view={
                        "branch_id": self._v4_branch_id,
                        "target_step": combined.step,
                        "write_epoch_ids": list(combined.write_epoch_ids),
                    },
                )
            try:
                marker = await self._await_v4_publication(publish_epoch_transaction)
            except BaseException:
                # A failed epoch is never recoverable.  The caller may continue
                # only after explicitly starting a new epoch.
                self._v4_epoch.clear()
                self._v4_pending_memory_epoch_ids.clear()
                raise
            self._v4_epoch.clear()
            committed = self._v4_store.committed_memory_epoch_ids(combined.step)
            self._v4_committed_memory_epoch_ids = set(committed)
            self._v4_pending_memory_epoch_ids.clear()
            if self._v4_world is not None:
                self._v4_world._checkpoint_annotations.update(
                    _thaw_json(combined.annotations or {})
                )
                self._v4_world.set_memory_checkpoint_view(
                    target_step=combined.step,
                    branch_id=self._v4_branch_id,
                    branch_lineage=self._v4_branch_lineage,
                    committed_write_epoch_ids=committed,
                )
            return marker

    def discard_unpublished_epoch(self) -> None:
        """Drop all sealed deltas since the previous v4 marker."""

        if self._v4_publish_lock is not None and self._v4_publish_lock.locked():
            raise RuntimeError("cannot discard an epoch while v4 writer is active")
        self._v4_epoch.clear()
        self._v4_pending_memory_epoch_ids.clear()
        if self._v4_store is not None and self._v4_world is not None:
            latest = self._v4_store.resolve()
            self._v4_committed_memory_epoch_ids = (
                self._v4_store.committed_memory_epoch_ids(latest["step"])
            )
            self._v4_world.set_memory_checkpoint_view(
                target_step=int(latest["step"]),
                branch_id=self._v4_branch_id,
                branch_lineage=self._v4_branch_lineage,
                committed_write_epoch_ids=self._v4_committed_memory_epoch_ids,
            )

    @classmethod
    def _resolve_v4_checkpoint_from(
        cls,
        source_run: str | Path,
        step: Optional[int] = None,
        *,
        include_restored_state: bool = False,
    ) -> Dict[str, Any]:
        root = Path(source_run).resolve()
        complete_dir = root / "checkpoints" / "v4" / "complete"
        if not complete_dir.is_dir():
            raise FileNotFoundError(f"No v4 complete checkpoints found in {root}")
        store = V4CheckpointStore(root)
        return store.resolve(
            step,
            include_restored_state=include_restored_state,
        )

    @staticmethod
    def _v4_root_manifest(
        root: Path,
        step: int,
        *,
        branch_id: str = "main",
    ) -> dict[str, Any]:
        store = V4CheckpointStore(root, branch_id=branch_id)
        chain = store._manifest_chain(step)
        if not chain or not isinstance(chain[0].get("root_metadata"), dict):
            raise ValueError(f"v4 root metadata missing for step {step}")
        return chain[0]["root_metadata"]

    @staticmethod
    def _v4_apply_transient_defaults(
        state: dict[str, Any],
        schema: Optional[PersistenceSchema],
    ) -> None:
        if schema is None:
            return
        source_schemas = getattr(schema, "source_schemas", (schema,))

        def apply_at(path: tuple[Any, ...], rule: Any) -> None:
            current: Any = state
            for part in path[:-1]:
                if not isinstance(current, dict):
                    return
                child = current.get(part)
                if child is None:
                    child = {}
                    current[part] = child
                current = child
            if isinstance(current, dict):
                current.setdefault(path[-1], copy.deepcopy(rule.default))

        for source in source_schemas:
            root = tuple(source.root_path)
            for path, rule in source.rules.items():
                if rule.kind is not PersistenceKind.TRANSIENT or not rule.has_default:
                    continue
                # Rules generated for dynamic entry maps are not transient
                # defaults; their concrete keys are runtime data.
                if any(part is _WILDCARD for part in path):
                    wildcard_index = path.index(_WILDCARD)
                    prefix = path[:wildcard_index]
                    found, container = PersistenceManager._v4_path_value(state, prefix)
                    if not found or not isinstance(container, Mapping):
                        continue
                    suffix = path[wildcard_index + 1 :]
                    for key in container:
                        # A transient wildcard child is only meaningful when a
                        # concrete entry already exists; do not invent IDs.
                        apply_at(prefix + (key,) + suffix, rule)
                    continue
                if path[: len(root)] != root or not path[len(root) :]:
                    continue
                apply_at(path, rule)

    async def _load_v4_checkpoint_record(
        self,
        record: Dict[str, Any],
        *,
        event_logger: Optional[Any],
        event_log_path: Optional[str],
        environment_factory: Optional[Any],
    ) -> Tuple['World', 'Schedule']:
        marker_path = Path(record["marker_file"]).resolve()
        v4_base = next(
            (
                parent
                for parent in marker_path.parents
                if parent.name == "v4" and parent.parent.name == "checkpoints"
            ),
            None,
        )
        if v4_base is None:
            raise ValueError("v4 marker is outside a checkpoint store")
        root = v4_base.parent.parent
        branch_id = str((record.get("marker") or {}).get("branch_id") or "main")
        store = V4CheckpointStore(root, branch_id=branch_id)
        state_payload = record.get("_restored_state")
        if not isinstance(state_payload, dict):
            state_payload = store.restore(record["step"])
        root_metadata = self._v4_root_manifest(
            root,
            record["step"],
            branch_id=branch_id,
        )
        schema = self._v4_schema
        raw_schemas = root_metadata.get("persistence_schemas")
        if schema is None and isinstance(raw_schemas, list) and raw_schemas:
            compiled_sources = []
            for payload in raw_schemas:
                if not isinstance(payload, Mapping) or not isinstance(payload.get("schema"), Mapping):
                    raise ValueError("invalid v4 persistence schema metadata")
                root_path_raw = payload.get("root_path") or ("environment", "state")
                root_path = tuple(
                    _WILDCARD if part == "*" else part for part in root_path_raw
                )
                compiled_sources.append(
                    PersistenceSchema.compile(
                        payload["schema"],
                        root_path=root_path,
                    )
                )
            schema = (
                PersistenceSchema.merge(*compiled_sources)
                if len(compiled_sources) > 1
                else compiled_sources[0]
            )
        if schema is None and isinstance(root_metadata.get("persistence_schema"), dict):
            root_path = tuple(root_metadata.get("persistence_root_path") or ("environment", "state"))
            schema = PersistenceSchema.compile(root_metadata["persistence_schema"], root_path=root_path)

        from .core_data import World

        events_file = event_log_path or str(self.events_dir / f"events_from_step_{record['step']}.jsonl")
        world = World(step=record["step"], event_log_path=events_file, event_logger=event_logger)
        if environment_factory is not None:
            world.set_environment_factory(environment_factory)
        world.agents_data = copy.deepcopy(root_metadata.get("agents_data") or {})
        world._agent_types = copy.deepcopy(root_metadata.get("agent_types") or {})
        world.environment_data = copy.deepcopy(root_metadata.get("environment_data") or {})
        resume_identity = root_metadata.get("resume_identity")
        if isinstance(resume_identity, Mapping):
            world._resume_identity = copy.deepcopy(dict(resume_identity))
        world.environment_data.setdefault("type", "base")
        restored_state_payload = state_payload
        self._v4_apply_transient_defaults(restored_state_payload, schema)
        restored_state = (
            (restored_state_payload.get("environment") or {}).get("state") or {}
        )
        world.environment_data["state"] = restored_state
        restored_agents = restored_state_payload.get("agents") or {}
        if isinstance(restored_agents, Mapping):
            for agent_id, agent_data in world.agents_data.items():
                dynamic_data = restored_agents.get(agent_id, {})
                if isinstance(dynamic_data, Mapping):
                    agent_data["state"] = dynamic_data.get("state") or {}
                    agent_data["properties"] = dynamic_data.get("properties") or {}
                    agent_data["reminders"] = dynamic_data.get("reminders") or []
                else:
                    agent_data["state"] = {}
                    agent_data["properties"] = {}
                    agent_data["reminders"] = []
        if schema is not None:
            world.configure_persistence(schema)
        committed_epochs = store.committed_memory_epoch_ids(int(record["step"]))
        branch_id = str((record.get("marker") or {}).get("branch_id") or "main")
        forked_from = (record.get("marker") or {}).get("forked_from") or {}
        branch_lineage = []
        if isinstance(forked_from, Mapping) and forked_from.get("branch_id") is not None:
            branch_lineage.append(
                (str(forked_from["branch_id"]), int(forked_from.get("step", record["step"])))
            )
        world.set_memory_checkpoint_view(
            target_step=int(record["step"]),
            branch_id=branch_id,
            branch_lineage=branch_lineage,
            committed_write_epoch_ids=committed_epochs,
        )
        world._checkpoint_annotations = store.checkpoint_annotations(int(record["step"]))
        return world, None
    
    async def save_diagnostic_checkpoint(
        self,
        world: 'World',
        *,
        filename: str = "checkpoint_final.json.gz",
        failure: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Write a non-recoverable final snapshot for inspection.

        This intentionally does not create a Chroma checkpoint copy or a
        recoverable marker.
        """
        if Path(filename).name != filename:
            raise ValueError("Diagnostic checkpoint filename must be a basename")
        if not filename.endswith(".json.gz"):
            raise ValueError("Diagnostic checkpoint filename must end with .json.gz")
        step = self._normalize_step(world.step)
        environment = world.get_environment()
        environment_payload = dict(world.environment_data)
        environment_payload["state"] = dict(world.environment_data.get("state") or {})
        environment_payload["snapshot"] = self._derived_environment_snapshot(
            environment.snapshot(include_state=False)
        )
        # A diagnostic snapshot can be taken while an activation is still
        # running.  Keep immutable cursors to every open/closed Agent Thread
        # so the failure evidence can be resumed by inspection.
        agent_threads = self.agent_thread_store.snapshot_thread_references()
        path = self.checkpoints_dir / filename
        payload = {
                "step": step,
                "timestamp": time.time(),
                "recoverable": False,
                "diagnostic": True,
                "encoding": self.DIAGNOSTIC_ENCODING,
                "agent_threads": agent_threads,
                "agents_data": self._serialize_agents_data(world.agents_data),
                "environment_data": environment_payload,
                "world_state_summary": world.get_state_summary(),
        }
        if failure is not None:
            payload["failure"] = dict(failure)
        self._atomic_write_diagnostic_gzip_json(path, payload)
        return path

    def append_node_diff(
        self,
        *,
        step_id: int,
        node_id: str,
        changes: List[Dict[str, Any]],
        context_stack: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """追加节点差异记录到 diffs 目录。"""
        record: Dict[str, Any] = {
            "step_number": step_id,
            "node_id": node_id,
            "changes": changes,
        }
        if context_stack is not None:
            record["context_stack"] = context_stack

        diff_file = self.diffs_dir / f"diffs_from_step_{step_id:06d}.jsonl"
        serialized = json.dumps(record, ensure_ascii=False, default=self._json_serializer)
        with self._diff_lock, diff_file.open("a", encoding="utf-8") as fp:
            fp.write(serialized + "\n")
    
    @classmethod
    def resolve_checkpoint_from(
        cls,
        source_run: str | Path,
        step: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read and validate a v4 checkpoint without opening its Chroma store."""
        return cls._resolve_v4_checkpoint_from(Path(source_run).resolve(), step)


    @classmethod
    def resolve_last_complete_from(cls, source_run: str | Path) -> Dict[str, Any]:
        """返回来源运行中最新且完整可恢复的 checkpoint。"""

        return cls.resolve_checkpoint_from(source_run, step=None)

    def resolve_checkpoint(self, step: Optional[int] = None) -> Dict[str, Any]:
        """Resolve a checkpoint in this run through the read-only resolver."""
        self._assert_usable()
        store = self._v4_store or V4CheckpointStore(self.save_dir)
        return store.resolve(step)

    async def create_branch(self, from_step: int, branch_name: str) -> "PersistenceManager":
        """从任意完整 v4 checkpoint 创建共享 immutable 组件的逻辑分支。"""

        if not self._v4_enabled or self._v4_store is None:
            raise RuntimeError("v4 persistence must be configured before forking")
        branch_store = self._v4_store.fork(branch_name, step=from_step)
        branch = type(self)(str(self.save_dir))
        branch._v4_enabled = True
        branch._v4_store = branch_store
        branch._v4_schema = self._v4_schema
        branch._v4_checkpoint_every = self._v4_checkpoint_every
        branch._v4_epoch = []
        branch._v4_publish_lock = asyncio.Lock()
        branch._v4_root_published = True
        branch._v4_run_id = self._v4_run_id
        branch._v4_branch_id = str(branch_name)
        branch._v4_branch_lineage = [(self._v4_branch_id, int(from_step))]
        branch._v4_committed_memory_epoch_ids = (
            branch_store.committed_memory_epoch_ids(from_step)
        )
        branch._v4_pending_memory_epoch_ids = set()
        record = branch_store.resolve(
            from_step,
            include_restored_state=True,
        )
        world, _ = await branch._load_v4_checkpoint_record(
            record,
            event_logger=None,
            event_log_path=str(branch.events_dir / f"branch_{branch_name}.jsonl"),
            environment_factory=None,
        )
        world.set_persistence_manager(branch)
        branch._v4_world = world
        return branch

    async def load_checkpoint(
        self,
        step: Optional[int] = None,
        *,
        memory_required: Optional[bool] = None,
        restore_chroma: bool = False,
        event_logger: Optional[Any] = None,
        event_log_path: Optional[str] = None,
        environment_factory: Optional[Any] = None,
    ) -> Tuple['World', 'Schedule']:
        """Restore a v4 checkpoint and switch the Chroma visibility view.

        ``memory_required`` and ``restore_chroma`` are accepted only as an
        explicit v4 call-site guard; v4 never copies or restores a database.
        """
        del memory_required
        if restore_chroma:
            raise ValueError("v4 checkpoints use the live Chroma view; no copy is restored")
        store = self._v4_store or V4CheckpointStore(self.save_dir)
        record = store.resolve(step, include_restored_state=True)
        return await self._load_v4_checkpoint_record(
            record,
            event_logger=event_logger,
            event_log_path=event_log_path,
            environment_factory=environment_factory,
        )

    async def load_checkpoint_from(
        self,
        source_run: str | Path,
        step: Optional[int] = None,
        *,
        memory_required: Optional[bool] = None,
        restore_chroma: bool = False,
        event_logger: Optional[Any] = None,
        event_log_path: Optional[str] = None,
        environment_factory: Optional[Any] = None,
    ) -> Tuple['World', 'Schedule']:
        """Restore a v4 checkpoint from another run without mutating it."""
        del memory_required
        if restore_chroma:
            raise ValueError("v4 checkpoints use the live Chroma view; no copy is restored")
        self._assert_usable()
        record = self._resolve_v4_checkpoint_from(
            Path(source_run).resolve(),
            step,
            include_restored_state=True,
        )
        return await self._load_v4_checkpoint_record(
            record,
            event_logger=event_logger,
            event_log_path=event_log_path,
            environment_factory=environment_factory,
        )

    @staticmethod
    def _serialize_agent_entry(agent_info: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize all persistent Agent fields, including identity and model selection."""
        serialized = dict(agent_info)
        serialized["id"] = str(agent_info.get("id") or "")
        serialized["type"] = agent_info.get("type", "unknown")
        serialized["archetype"] = agent_info.get("archetype", "rule")
        serialized["state"] = dict(agent_info.get("state") or {})
        serialized["properties"] = dict(agent_info.get("properties") or {})
        serialized["reminders"] = list(agent_info.get("reminders") or [])
        for key in ("persona", "persona_instance", "persona_type"):
            serialized[key] = agent_info.get(key, "")
        if agent_info.get("model") is not None:
            serialized["model"] = agent_info["model"]
        return serialized

    def _serialize_agents_data(self, agents_data: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize agents data for checkpointing."""
        serialized = {}
        for agent_id, agent_info in agents_data.items():
            entry = self._serialize_agent_entry(agent_info)
            entry["id"] = str(entry.get("id") or agent_id)
            serialized[agent_id] = entry
        return serialized
    
    async def get_available_checkpoints(self) -> List[int]:
        """Get complete, recoverable checkpoint step numbers."""
        return self._get_available_checkpoints_sync()

    def _get_available_checkpoints_sync(self) -> List[int]:
        steps: list[int] = []
        store = self._v4_store or V4CheckpointStore(self.save_dir)
        for step in store.available_steps():
            try:
                store.resolve(step)
                steps.append(step)
            except (IndexError, ValueError, OSError, json.JSONDecodeError) as exc:
                logger.warning("Ignoring invalid v4 checkpoint step %s: %s", step, exc)
                continue
        return sorted(steps)
    
    def get_experiment_info(self) -> Dict[str, Any]:
        """Get experiment information and statistics."""
        # Use sync version for checkpoints
        available_checkpoints = self._get_available_checkpoints_sync()
        
        return {
            "save_dir": str(self.save_dir),
            "metadata": self.experiment_metadata.copy(),
            "available_checkpoints": available_checkpoints,
            "total_checkpoints": len(available_checkpoints),
            "disk_usage_mb": self._calculate_disk_usage(),
            "architecture_version": "unified_state_v4"
        }
    
    def _calculate_disk_usage(self) -> float:
        """Calculate total disk usage in MB."""
        total_size = 0
        
        for file_path in self.save_dir.rglob("*"):
            if file_path.is_file():
                try:
                    total_size += file_path.stat().st_size
                except OSError:
                    continue
        
        return total_size / (1024 * 1024)  # Convert to MB
    
    def _json_serializer(self, obj):
        """Custom JSON serializer for complex objects."""
        if isinstance(obj, Path):
            return str(obj)
        elif hasattr(obj, '__dict__'):
            return obj.__dict__
        else:
            return str(obj)

    def close(self) -> None:
        """
        关闭持久化管理器。
        - 若使用 tmpfs runtime，先同步回磁盘 store
        - 按配置清理 tmpfs 目录
        """
        with self._chroma_init_lock:
            if self._close_completed:
                return

            if self._restore_failed:
                # The runtime/store pair is already known to be uncertain.
                # Keep the runtime for forensic inspection or an explicit
                # recovery attempt; closing must not turn that uncertainty
                # into data loss.
                logger.warning(
                    "PersistenceManager remains open after failed Chroma restore; "
                    "runtime path retained: %s",
                    self.chroma_runtime_path,
                )
                self._close_completed = True
                return

            try:
                self._sync_chroma_to_store()
            except Exception as exc:
                # A failed writeback is observable to the caller and leaves
                # both the client and runtime attached so close() can be
                # retried after the underlying storage issue is fixed.
                logger.warning("Failed to sync Chroma store during close: %s", exc)
                raise

            self._chroma_client = None

            if (
                self._using_fallback_runtime
                and self._cleanup_tmpfs_on_close
                and self.chroma_runtime_path
                and self.chroma_runtime_path != self.chroma_store_path
            ):
                try:
                    shutil.rmtree(self.chroma_runtime_path, ignore_errors=True)
                    logger.debug("Cleaned tmpfs Chroma runtime path: %s", self.chroma_runtime_path)
                except Exception as exc:
                    logger.warning("Failed to cleanup tmpfs runtime path: %s", exc)

            self._close_completed = True
