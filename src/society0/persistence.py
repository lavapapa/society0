"""
SimEngine V2: PersistenceManager - Unified persistence for the new architecture.

Handles checkpointing of World state, Chroma vector store data,
and event replay according to the final integration design document.
"""

from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING, Iterable
from pathlib import Path
import copy
import gzip
import io
import json
import traceback
import time
import shutil
import logging
import os
import uuid
import hashlib
from datetime import datetime
import threading

from .logging import LogField, SystemEvent
from .jmespath_context import NodeSnapshot, OperatorSnapshot
from .state_proxy import DictProxy, ListProxy

if TYPE_CHECKING:
    from .core_data import World
    from typing import Any as Schedule

logger = logging.getLogger(__name__)


class PersistenceManager:
    """
    Unified persistence manager for the new World-based architecture.

    Handles:
    1. World state snapshots (agents_data, environment_data)
    2. Chroma vector store backup/restore and client management
    3. Event log persistence and replay
    4. Schedule progress tracking

    按照resource_management_design.md，新增向量存储客户端管理职责。
    """

    CHECKPOINT_VERSION = "complete_step_v2"
    WORLD_ENCODING = "gzip-json"
    WORLD_COMPRESSION_LEVEL = 6

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
        self.complete_checkpoints_dir = self.checkpoints_dir / "complete"
        self.chroma_backup_dir = self.save_dir / "chroma_backups"
        self.metadata_dir = self.save_dir / "metadata"
        self.events_dir = self.save_dir / "events"
        self.diffs_dir = self.save_dir / "diffs"
        self.interviews_dir = self.save_dir / "interviews"

        for dir_path in [
            self.checkpoints_dir,
            self.complete_checkpoints_dir,
            self.chroma_backup_dir,
            self.metadata_dir,
            self.events_dir,
            self.diffs_dir,
            self.interviews_dir,
        ]:
            dir_path.mkdir(parents=True, exist_ok=True)

        self._diff_lock = threading.Lock()
        self._chroma_init_lock = threading.Lock()

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
            "architecture_version": "unified_state_v2",
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
            "architecture_version": "unified_state_v2"
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

    @classmethod
    def _fsync_tree(cls, root: Path) -> None:
        """Flush copied component files and directory entries to stable storage."""

        if root.is_symlink() or not root.is_dir():
            raise ValueError(f"Checkpoint component must be a regular directory: {root}")

        directories: List[Path] = []
        for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink():
                raise ValueError(f"Checkpoint component must not contain symlinks: {path}")
            if path.is_dir():
                directories.append(path)
                continue
            if not path.is_file():
                raise ValueError(f"Checkpoint component contains unsupported entry: {path}")
            with path.open("rb") as handle:
                os.fsync(handle.fileno())

        for directory in sorted(directories, key=lambda item: item.as_posix(), reverse=True):
            cls._fsync_directory(directory)
        cls._fsync_directory(root)

    @staticmethod
    def _normalize_step(step: Any) -> int:
        """Validate a checkpoint step without coercing caller input."""

        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            raise ValueError("step must be a non-negative integer")
        return step

    def _complete_marker_path(self, step: int) -> Path:
        step = self._normalize_step(step)
        return self.complete_checkpoints_dir / f"step_{step:06d}.json"

    def _checkpoint_file_path(
        self,
        step: int,
        *,
        checkpoint_id: Optional[str] = None,
    ) -> Path:
        """Return the immutable world component path for a checkpoint.

        Recoverable checkpoints are published through the step marker.  The
        component name therefore includes the checkpoint id so a replacement
        can be built without touching the pair referenced by the currently
        published marker.
        """
        step = self._normalize_step(step)
        suffix = f".{checkpoint_id}" if checkpoint_id else ""
        return self.checkpoints_dir / f"checkpoint_{step:06d}{suffix}.json.gz"

    @classmethod
    def _read_world_checkpoint(cls, path: Path, *, encoding: Any) -> Dict[str, Any]:
        """Decode one validated world component using its declared encoding."""
        if encoding != cls.WORLD_ENCODING:
            raise ValueError(f"Unsupported world checkpoint encoding: {encoding!r}")
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, EOFError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("World checkpoint gzip JSON is invalid") from exc
        if not isinstance(payload, dict):
            raise ValueError("World checkpoint payload must be a mapping")
        return payload

    def _chroma_backup_path(
        self,
        step: int,
        *,
        checkpoint_id: Optional[str] = None,
    ) -> Path:
        """Return the immutable Chroma component path for a checkpoint."""
        step = self._normalize_step(step)
        suffix = f".{checkpoint_id}" if checkpoint_id else ""
        return self.chroma_backup_dir / f"step_{step:06d}{suffix}"

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

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
    def _directory_content_sha256(cls, directory: Path) -> str:
        """Hash file names and bytes, excluding the checkpoint manifest itself."""
        digest = hashlib.sha256()
        for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
            if path.is_symlink():
                raise ValueError(f"Checkpoint backup must not contain symlinks: {path}")
            if not path.is_file() or path == directory / "_checkpoint.json":
                continue
            relative = path.relative_to(directory).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _validate_memoryless_chroma_backup(directory: Path) -> None:
        """Ensure a rule-only Chroma component contains metadata only.

        Rule-only checkpoints deliberately do not carry vector data.  Keeping
        the versioned backup directory (and its manifest) preserves the same
        marker/hash publication protocol as memory-backed checkpoints, while
        this validation prevents stale files or directories from crossing the
        checkpoint boundary.
        """
        for entry in directory.iterdir():
            if entry.name == "_checkpoint.json":
                if entry.is_symlink() or not entry.is_file():
                    raise ValueError(
                        "Memoryless Chroma backup metadata must be a regular file"
                    )
                continue
            raise ValueError(
                "Chroma backup does not match: memoryless backup must contain metadata only"
            )

    @staticmethod
    def _world_requires_memory(world: 'World') -> bool:
        return any(
            isinstance(agent_data, dict) and agent_data.get("archetype") == "llm"
            for agent_data in world.agents_data.values()
        )
    
    async def save_checkpoint(
        self,
        world: 'World',
        schedule: 'Schedule',
        *,
        step_metrics: Optional[Dict[str, Any]] = None,
        memory_required: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        Save a complete checkpoint with World state and 向量存储（Chroma）备份。
        
        According to the design document:
        1. Get current step number
        2. Call world.environment.snapshot() for custom environment data
        3. Stream world data directly into checkpoint_{step}.{id}.json.gz
        4. Backup Chroma vector store
        5. Update metadata.json
        
        Args:
            world: The World instance to checkpoint
            schedule: The Schedule instance for progress tracking
        """
        self._assert_usable()
        step = self._normalize_step(world.step)
        checkpoint_id = uuid.uuid4().hex
        checkpoint_file = self._checkpoint_file_path(step, checkpoint_id=checkpoint_id)
        marker_file = self._complete_marker_path(step)
        requires_memory = self._world_requires_memory(world)
        if memory_required is not None and bool(memory_required) is not requires_memory:
            raise ValueError("memory_required must match the LLM agents present in World")
        checkpoint_start = time.time()
        checkpoint_published = False
        chroma_backup: Optional[Path] = None
        temp_path = checkpoint_file.with_name(
            f".{checkpoint_file.name}.{uuid.uuid4().hex}.tmp.json.gz"
        )

        try:
            logger.info(f"Creating checkpoint for step {step}")

            agent_threads = self.agent_thread_store.publish_checkpoint_manifest(
                checkpoint_id=checkpoint_id,
                step=step,
            )

            environment = world.get_environment()
            env_snapshot = self._plain_snapshot_value(environment.snapshot())

            environment_payload = dict(world.environment_data)
            environment_payload["state"] = dict(world.environment_data.get("state") or {})
            environment_payload["snapshot"] = env_snapshot
            observation_payload = self._build_observation_data(
                world,
                schedule,
                step_metrics,
                include_agents=False,
            )

            world_metadata = {
                "checkpoint_version": self.CHECKPOINT_VERSION,
                "world_encoding": self.WORLD_ENCODING,
                "created_by": "PersistenceManager.save_checkpoint",
                "checkpoint_id": checkpoint_id,
                "step": step,
                "memory_required": requires_memory,
                "agent_types": dict(getattr(world, "_agent_types", {}) or {}),
                "agent_threads": {
                    "schema_version": 1,
                    "manifest": agent_threads["path"],
                    "manifest_sha256": agent_threads["sha256"],
                    "thread_count": agent_threads["thread_count"],
                    "by_agent": agent_threads["by_agent"],
                    "threads": agent_threads["threads"],
                },
            }
            resume_identity = getattr(world, "_resume_identity", None)
            if resume_identity is not None:
                world_metadata["resume_identity"] = dict(resume_identity)
            checkpoint_annotations = world.checkpoint_annotations()
            if checkpoint_annotations:
                world_metadata["annotations"] = checkpoint_annotations

            current_time = time.time()

            def _write_value_field(fp, indent: int, key: str, value: Any, *, last: bool) -> None:
                fp.write(" " * indent)
                fp.write(json.dumps(key, ensure_ascii=False))
                fp.write(": ")
                json.dump(value, fp, ensure_ascii=False, default=self._json_serializer)
                if not last:
                    fp.write(",")
                fp.write("\n")

            def _write_agents_map(fp, indent: int, agents_data: Dict[str, Any]) -> None:
                fp.write("{")
                if agents_data:
                    fp.write("\n")
                    for idx, (agent_id, agent_info) in enumerate(agents_data.items()):
                        if idx:
                            fp.write(",\n")
                        fp.write(" " * (indent + 2))
                        fp.write(json.dumps(agent_id, ensure_ascii=False))
                        fp.write(": ")
                        json.dump(
                            self._serialize_agent_entry(agent_info),
                            fp,
                            ensure_ascii=False,
                            default=self._json_serializer,
                        )
                    fp.write("\n")
                    fp.write(" " * indent)
                fp.write("}")

            def _write_agents_field(fp, indent: int, key: str, agents_data: Dict[str, Any], *, last: bool) -> None:
                fp.write(" " * indent)
                fp.write(json.dumps(key, ensure_ascii=False))
                fp.write(": ")
                _write_agents_map(fp, indent, agents_data)
                if not last:
                    fp.write(",")
                fp.write("\n")

            def _write_observation_field(fp, indent: int, key: str, payload: Dict[str, Any], agents_data: Dict[str, Any], *, last: bool) -> None:
                fp.write(" " * indent)
                fp.write(json.dumps(key, ensure_ascii=False))
                fp.write(": {\n")
                inner_fields: List[Tuple[str, str]] = [
                    ("step", "value"),
                    ("environment_data", "value"),
                    ("agents_data", "agents"),
                    ("step_flow", "value"),
                ]
                if payload.get("metrics") is not None:
                    inner_fields.append(("metrics", "value"))

                for idx, (inner_key, inner_type) in enumerate(inner_fields):
                    inner_last = idx == len(inner_fields) - 1
                    fp.write(" " * (indent + 2))
                    fp.write(json.dumps(inner_key, ensure_ascii=False))
                    fp.write(": ")
                    if inner_type == "agents":
                        _write_agents_map(fp, indent + 2, agents_data)
                    else:
                        json.dump(
                            payload[inner_key],
                            fp,
                            ensure_ascii=False,
                            default=self._json_serializer,
                        )
                    if not inner_last:
                        fp.write(",")
                    fp.write("\n")

                fp.write(" " * indent)
                fp.write("}")
                if not last:
                    fp.write(",")
                fp.write("\n")

            with temp_path.open("xb") as raw_fp:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    compresslevel=self.WORLD_COMPRESSION_LEVEL,
                    fileobj=raw_fp,
                    mtime=0,
                ) as gzip_fp:
                    with io.TextIOWrapper(gzip_fp, encoding="utf-8") as fp:
                        fp.write("{\n")
                        top_level_fields: List[Tuple[str, str]] = [
                            ("checkpoint_id", "value"),
                            ("step", "value"),
                            ("timestamp", "value"),
                            ("agents_data", "agents"),
                            ("environment_data", "value"),
                            ("world_metadata", "value"),
                            ("observation_data", "observation"),
                            ("source_step", "value"),
                        ]
                        include_metrics = step_metrics is not None
                        if include_metrics:
                            top_level_fields.extend(
                                [("metrics", "value"), ("step_metrics", "value")]
                            )

                        for idx, (field_key, field_type) in enumerate(top_level_fields):
                            last_field = idx == len(top_level_fields) - 1
                            if field_type == "value":
                                value_mapping = {
                                    "checkpoint_id": checkpoint_id,
                                    "step": step,
                                    "timestamp": current_time,
                                    "environment_data": environment_payload,
                                    "world_metadata": world_metadata,
                                    "source_step": step,
                                }
                                if include_metrics and field_key in {
                                    "metrics",
                                    "step_metrics",
                                }:
                                    value_mapping[field_key] = step_metrics  # type: ignore[assignment]
                                _write_value_field(
                                    fp,
                                    2,
                                    field_key,
                                    value_mapping[field_key],
                                    last=last_field,
                                )
                            elif field_type == "agents":
                                _write_agents_field(
                                    fp,
                                    2,
                                    field_key,
                                    world.agents_data,
                                    last=last_field,
                                )
                            elif field_type == "observation":
                                _write_observation_field(
                                    fp,
                                    2,
                                    field_key,
                                    observation_payload,
                                    world.agents_data,
                                    last=last_field,
                                )
                        fp.write("}\n")
                        fp.flush()
                raw_fp.flush()
                os.fsync(raw_fp.fileno())

            temp_path.replace(checkpoint_file)
            self._fsync_directory(checkpoint_file.parent)
            world_sha256 = self._file_sha256(checkpoint_file)

            # Validate the immutable world component before publishing the
            # marker.  A marker must never expose a truncated or foreign file.
            persisted_world = self._read_world_checkpoint(
                checkpoint_file,
                encoding=self.WORLD_ENCODING,
            )
            if (
                persisted_world.get("checkpoint_id") != checkpoint_id
                or persisted_world.get("step") != step
                or persisted_world.get("world_metadata", {}).get("checkpoint_id")
                != checkpoint_id
            ):
                raise ValueError("World checkpoint component failed identity validation")

            checkpoint_size = checkpoint_file.stat().st_size if checkpoint_file.exists() else None

            backup_start = time.time()
            chroma_backup = await self._backup_chroma_store(
                step,
                checkpoint_id=checkpoint_id,
                memory_required=requires_memory,
            )
            backup_duration = time.time() - backup_start
            chroma_sha256 = self._directory_content_sha256(chroma_backup)
            chroma_manifest_path = chroma_backup / "_checkpoint.json"
            with chroma_manifest_path.open("r", encoding="utf-8") as fp:
                chroma_manifest = json.load(fp)
            if (
                chroma_manifest.get("checkpoint_id") != checkpoint_id
                or chroma_manifest.get("step") != step
                or chroma_manifest.get("content_sha256") != chroma_sha256
            ):
                raise ValueError("Chroma backup component failed identity validation")

            marker_payload = {
                "complete": True,
                "recoverable": True,
                "checkpoint_id": checkpoint_id,
                "step": step,
                "world_file": checkpoint_file.name,
                "world_encoding": self.WORLD_ENCODING,
                "chroma_backup": chroma_backup.name if chroma_backup is not None else None,
                "memory_required": requires_memory,
                "published_at": time.time(),
                "checkpoint_version": self.CHECKPOINT_VERSION,
                "world_sha256": world_sha256,
                "chroma_sha256": chroma_sha256,
                "agent_threads_manifest": agent_threads["path"],
                "agent_threads_sha256": agent_threads["sha256"],
            }
            # Validate the exact immutable Thread manifest before publishing
            # the complete marker.  A marker is the commit point; publishing
            # it with a missing/mismatched Thread reference would make a
            # checkpoint appear recoverable while its evidence is not.
            from .agent.thread_store import AgentThreadStore

            AgentThreadStore.validate_checkpoint_manifest(
                self.save_dir,
                agent_threads["path"],
                expected_sha256=agent_threads["sha256"],
                checkpoint_id=checkpoint_id,
                step=step,
            )
            # 5. Update metadata
            self.experiment_metadata.setdefault("total_steps", 0)
            self.experiment_metadata.setdefault("last_checkpoint_step", -1)
            self.experiment_metadata["last_checkpoint_step"] = step
            self.experiment_metadata["total_steps"] = max(
                self.experiment_metadata["total_steps"], step)
            self._save_metadata()

            # This is the commit point.  The old marker and its immutable pair
            # remain untouched until this single atomic replacement succeeds.
            # Versioned old components stay available for readers that opened
            # the previous marker; any garbage collection is a separate task.
            marker_payload["published_at"] = time.time()
            self._atomic_write_json(marker_file, marker_payload)
            checkpoint_published = True

            logger.info(f"Successfully saved checkpoint for step {step}")

            log_context = getattr(world, "_log_context", None)
            if log_context:
                duration = time.time() - checkpoint_start
                payload = {
                    LogField.CHECKPOINT_STEP.value: step,
                    LogField.FILE_PATH.value: str(checkpoint_file),
                    LogField.DURATION_SEC.value: duration,
                }
                if checkpoint_size is not None:
                    payload[LogField.CHECKPOINT_SIZE_BYTES.value] = checkpoint_size
                payload[LogField.BACKUP_DURATION_SEC.value] = backup_duration
                payload[LogField.BACKUP_PATH.value] = str(self.chroma_store_path)
                log_context.log_system(
                    "INFO",
                    SystemEvent.CHECKPOINT_SAVED.value,
                    **payload,
                )

            return marker_payload

        except Exception as e:
            # A failed pre-commit build may leave only an unpublished orphan.
            # Never touch the component named by the previous marker.  If the
            # marker replacement itself succeeded but a later durability check
            # raised, preserve the newly published pair as well.
            if not checkpoint_published:
                try:
                    with marker_file.open("r", encoding="utf-8") as fp:
                        published_marker = json.load(fp)
                    checkpoint_published = (
                        published_marker.get("checkpoint_id") == checkpoint_id
                    )
                except (FileNotFoundError, OSError, json.JSONDecodeError):
                    pass
            if not checkpoint_published:
                checkpoint_file.unlink(missing_ok=True)
                if chroma_backup is not None:
                    shutil.rmtree(chroma_backup, ignore_errors=True)
            logger.error(f"Failed to save checkpoint for step {step}: {e}")
            log_context = getattr(world, "_log_context", None)
            if log_context:
                log_context.log_system(
                    "ERROR",
                    SystemEvent.CHECKPOINT_SAVED.value,
                    **{
                        LogField.CHECKPOINT_STEP.value: step,
                        LogField.ERROR.value: str(e),
                        LogField.TRACEBACK.value: traceback.format_exc(),
                    },
                )
            raise
        finally:
            temp_path.unlink(missing_ok=True)

    def _build_observation_data(
        self,
        world: 'World',
        schedule: 'Schedule',
        step_metrics: Optional[Dict[str, Any]] = None,
        *,
        include_agents: bool = True,
    ) -> Dict[str, Any]:
        """构建用于快照的观察数据。

        Args:
            world: 当前世界状态
            schedule: 调度实例
            step_metrics: 当步指标
            include_agents: 是否包含 agents_data（流式写入时可禁用以减少内存峰值）
        """

        # Extract metrics from last node's converter if available
        metrics = {}
        if hasattr(schedule, 'step_context') and schedule.step_context:
            # Get last node in execution order
            node_ids = list(schedule.step_context.keys())
            if node_ids:
                last_node_id = node_ids[-1]
                last_node_context = schedule.step_context[last_node_id]

                # Extract metrics from converter output
                if isinstance(last_node_context, dict) and 'converter_output' in last_node_context:
                    converter_output = last_node_context['converter_output']
                    if isinstance(converter_output, dict):
                        # Extract only top-level string->number fields
                        for key, value in converter_output.items():
                            if isinstance(key, str) and isinstance(value, (int, float)):
                                metrics[key] = value

        # Build step flow data with preserved order
        step_flow_nodes: List[Dict[str, Any]] = []
        step_execution_summary = {
            "total_nodes": 0,
            "successful_nodes": 0,
            "step_start_time": getattr(schedule, 'step_start_time', time.time()),
            "step_end_time": time.time(),
            "world_step": world.step
        }

        step_flow_nodes_source = (
            (step_metrics or {}).get("step_flow_nodes") if isinstance(step_metrics, dict) else None
        )

        if isinstance(step_flow_nodes_source, dict) and step_flow_nodes_source:
            step_execution_summary["total_nodes"] = len(step_flow_nodes_source)
            step_execution_summary["successful_nodes"] = len(step_flow_nodes_source)
            for node_id, node_payload in step_flow_nodes_source.items():
                if isinstance(node_payload, dict):
                    entry = {"id": node_id}
                    entry.update(node_payload)
                    step_flow_nodes.append(entry)
        elif hasattr(schedule, '_context_builder') and schedule._context_builder._nodes:
            step_execution_summary["total_nodes"] = len(schedule._context_builder._nodes)

            # Preserve execution order using the order in JmespathContextBuilder._nodes
            for node_id, node_snapshot in schedule._context_builder._nodes.items():
                if isinstance(node_snapshot, NodeSnapshot):
                    step_execution_summary["successful_nodes"] += 1
                    step_node = self._build_step_node_from_jmespath_snapshot(node_id, node_snapshot)
                    step_flow_nodes.append(step_node)

        # Build observation data structure
        observation_data: Dict[str, Any] = {
            "step": {
                "number": world.step,
                "timestamp": time.time(),
            },
            "environment_data": {
                "type": world.environment_data["type"],
                "state": dict(world.environment_data["state"]),
            },
            "step_flow": {
                "nodes": step_flow_nodes,
                "execution_summary": step_execution_summary
            },
        }

        if include_agents:
            observation_data["agents_data"] = self._serialize_agents_data(world.agents_data)

        # Add metrics if available
        if metrics:
            observation_data["metrics"] = metrics

        return observation_data

    async def save_diagnostic_checkpoint(
        self,
        world: 'World',
        *,
        filename: str = "checkpoint_final.json",
    ) -> Path:
        """Write a non-recoverable final snapshot for inspection.

        This intentionally does not create a Chroma backup or a complete marker.
        """
        if Path(filename).name != filename:
            raise ValueError("Diagnostic checkpoint filename must be a basename")
        step = self._normalize_step(world.step)
        environment = world.get_environment()
        environment_payload = dict(world.environment_data)
        environment_payload["state"] = dict(world.environment_data.get("state") or {})
        environment_payload["snapshot"] = self._plain_snapshot_value(
            environment.snapshot()
        )
        # A diagnostic snapshot can be taken while an activation is still
        # running.  Keep immutable cursors to every open/closed Agent Thread
        # so the failure evidence can be resumed by inspection.
        agent_threads = self.agent_thread_store.snapshot_thread_references()
        path = self.checkpoints_dir / filename
        self._atomic_write_json(
            path,
            {
                "step": step,
                "timestamp": time.time(),
                "recoverable": False,
                "diagnostic": True,
                "agent_threads": agent_threads,
                "agents_data": self._serialize_agents_data(world.agents_data),
                "environment_data": environment_payload,
                "world_state_summary": world.get_state_summary(),
            },
        )
        return path

    def _build_step_node_from_jmespath_snapshot(self, node_id: str, node_snapshot: NodeSnapshot) -> Dict[str, Any]:
        """从 JmespathContextBuilder 的 NodeSnapshot 构建步骤节点数据"""
        step_node = {
            "id": node_id,
            "inputs": getattr(node_snapshot, 'inputs', {}),
        }

        # Selector 信息
        if hasattr(node_snapshot, 'selector') and node_snapshot.selector:
            selector = node_snapshot.selector
            step_node["selector"] = {
                "params": getattr(selector, 'params', {}),
                "matched_ids": getattr(selector, 'matched_ids', []),
                "match_count": getattr(selector, 'match_count', 0)
            }

        # Operator 执行信息
        if hasattr(node_snapshot, 'operators') and node_snapshot.operators:
            operators = []
            for operator_id, operator_snapshot in node_snapshot.operators.items():
                operator_data = self._build_operator_from_jmespath_snapshot(operator_id, operator_snapshot)
                operators.append(operator_data)
            step_node["operators"] = operators

        # Converter 输出
        if hasattr(node_snapshot, 'converter_output') and node_snapshot.converter_output is not None:
            step_node["converter"] = {
                "output": node_snapshot.converter_output,
                # Note: converter expression is not stored in NodeSnapshot, so we'll omit it
            }

        return step_node

    def _build_operator_from_jmespath_snapshot(self, operator_id: str, operator_snapshot: OperatorSnapshot) -> Dict[str, Any]:
        """从 JmespathContextBuilder 的 OperatorSnapshot 构建操作员数据"""
        executions = []
        if hasattr(operator_snapshot, 'executions') and operator_snapshot.executions:
            for execution in operator_snapshot.executions:
                execution_data = {
                    "agent_id": execution.agent_id,
                    "status": execution.status,
                    "output": execution.output,
                    "structured_output": execution.structured_output,
                    "execution_time": execution.execution_time,
                    "inputs": execution.inputs,
                    "error_message": execution.error_message
                }
                executions.append(execution_data)

        return {
            "id": operator_id,
            "type": operator_snapshot.type,
            "executions": executions
        }

    def _build_step_node_snapshot(self, node_id: str, node_context: Dict[str, Any]) -> Dict[str, Any]:
        """Build a snapshot for a single step node."""
        node_snapshot = {
            "id": node_id,
            "inputs": node_context.get("inputs", {}),
        }

        # Add selector information if available
        if "selector_result" in node_context:
            selector_result = node_context["selector_result"]
            if isinstance(selector_result, dict):
                node_snapshot["selector"] = {
                    "params": node_context.get("selector_params", {}),
                    "matched_ids": selector_result.get("matched_ids", []),
                    "match_count": len(selector_result.get("matched_ids", []))
                }

        # Add operator executions
        if "operators" in node_context:
            operators = []
            operators_data = node_context["operators"]

            if isinstance(operators_data, dict):
                for operator_id, operator_data in operators_data.items():
                    if isinstance(operator_data, dict):
                        operator_snapshot = self._build_operator_snapshot(operator_id, operator_data)
                        operators.append(operator_snapshot)

            node_snapshot["operators"] = operators

        # Add converter output if available
        if "converter_output" in node_context:
            node_snapshot["converter"] = {
                "output": node_context["converter_output"],
                "expression": node_context.get("converter_expression")
            }

        return node_snapshot

    def _build_operator_snapshot(self, operator_id: str, operator_data: Dict[str, Any]) -> Dict[str, Any]:
        """Build a snapshot for a single operator."""
        operator_snapshot = {
            "id": operator_id,
            "type": operator_data.get("type", "unknown"),
            "executions": []
        }

        # Add executions if available
        if "executions" in operator_data:
            executions = operator_data["executions"]
            if isinstance(executions, list):
                for execution in executions:
                    if isinstance(execution, dict):
                        execution_snapshot = {
                            "agent_id": execution.get("agent_id", "unknown"),
                            "status": execution.get("status", "unknown"),
                            "output": execution.get("output", {}),
                            "structured_output": execution.get("structured_output", {}),
                            "execution_time": execution.get("execution_time"),
                            "inputs": execution.get("inputs", {}),
                            "error_message": execution.get("error_message")
                        }
                        operator_snapshot["executions"].append(execution_snapshot)

        return operator_snapshot

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
    
    @staticmethod
    def _safe_child(directory: Path, name: Any, *, component: str) -> Path:
        """Resolve one named checkpoint component without permitting path escape."""
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError(f"Invalid {component} name")
        directory = directory.resolve()
        candidate = (directory / name).resolve()
        if candidate.parent != directory:
            raise ValueError(f"Invalid {component} path")
        return candidate

    @classmethod
    def resolve_checkpoint_from(
        cls,
        source_run: str | Path,
        step: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Read and validate a checkpoint without initializing its source run.

        This method deliberately performs path-only reads: it creates no source
        directories, tmpfs runtime, Chroma client, or close-time writeback.
        """
        save_dir = Path(source_run).resolve()
        checkpoints_dir = save_dir / "checkpoints"
        complete_dir = checkpoints_dir / "complete"
        backup_root = save_dir / "chroma_backups"

        if step is None:
            candidates: List[int] = []
            for marker_path in complete_dir.glob("step_*.json"):
                try:
                    candidates.append(int(marker_path.stem.split("_", 1)[1]))
                except (IndexError, ValueError):
                    continue
            for candidate_step in sorted(set(candidates), reverse=True):
                try:
                    return cls.resolve_checkpoint_from(save_dir, candidate_step)
                except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
                    continue
            raise FileNotFoundError(f"No complete checkpoints found in {save_dir}")

        step = cls._normalize_step(step)
        marker_file = complete_dir / f"step_{step:06d}.json"
        if not marker_file.is_file() or marker_file.is_symlink():
            raise FileNotFoundError(f"Complete checkpoint not found for step {step}")
        with marker_file.open("r", encoding="utf-8") as handle:
            marker = json.load(handle)
        if marker.get("complete") is not True or marker.get("recoverable") is not True:
            raise ValueError(f"Checkpoint marker is not complete for step {step}")
        checkpoint_id = marker.get("checkpoint_id")
        marker_step = marker.get("step")
        if (
            isinstance(marker_step, bool)
            or not isinstance(marker_step, int)
            or marker_step != step
            or not isinstance(checkpoint_id, str)
            or not checkpoint_id
        ):
            raise ValueError(f"Invalid checkpoint marker for step {step}")
        if marker.get("checkpoint_version") != cls.CHECKPOINT_VERSION:
            raise ValueError(f"Unsupported checkpoint version for step {step}")

        checkpoint_file = cls._safe_child(
            checkpoints_dir,
            marker.get("world_file"),
            component="world checkpoint",
        )
        expected_world_name = f"checkpoint_{step:06d}.{checkpoint_id}.json.gz"
        if marker.get("world_file") != expected_world_name:
            raise ValueError(f"World checkpoint filename does not match marker for step {step}")
        world_encoding = marker.get("world_encoding")
        if world_encoding != cls.WORLD_ENCODING:
            raise ValueError(f"Unsupported world checkpoint encoding for step {step}")
        if not checkpoint_file.is_file() or checkpoint_file.is_symlink():
            raise FileNotFoundError(f"World checkpoint component missing for step {step}")
        expected_world_hash = marker.get("world_sha256")
        if not isinstance(expected_world_hash, str) or cls._file_sha256(checkpoint_file) != expected_world_hash:
            raise ValueError(f"World checkpoint content hash mismatch for step {step}")
        checkpoint_data = cls._read_world_checkpoint(
            checkpoint_file,
            encoding=world_encoding,
        )
        checkpoint_step = checkpoint_data.get("step")
        if (
            checkpoint_data.get("checkpoint_id") != checkpoint_id
            or isinstance(checkpoint_step, bool)
            or not isinstance(checkpoint_step, int)
            or checkpoint_step != step
        ):
            raise ValueError(f"World checkpoint does not match complete marker for step {step}")
        agents_data = checkpoint_data.get("agents_data")
        if not isinstance(agents_data, dict):
            raise ValueError(f"World checkpoint agents_data is invalid for step {step}")
        derived_memory_required = any(
            isinstance(agent, dict) and agent.get("archetype") == "llm"
            for agent in agents_data.values()
        )
        world_metadata = checkpoint_data.get("world_metadata")
        if not isinstance(world_metadata, dict):
            raise ValueError(f"World metadata missing for step {step}")
        if (
            world_metadata.get("checkpoint_id") != checkpoint_id
            or isinstance(world_metadata.get("step"), bool)
            or not isinstance(world_metadata.get("step"), int)
            or world_metadata.get("step") != step
            or world_metadata.get("checkpoint_version") != marker.get("checkpoint_version")
            or world_metadata.get("world_encoding") != world_encoding
            or world_metadata.get("memory_required") is not derived_memory_required
            or marker.get("memory_required") is not derived_memory_required
        ):
            raise ValueError(f"Checkpoint metadata does not match world data for step {step}")
        resume_identity = world_metadata.get("resume_identity")
        if resume_identity is not None:
            if not isinstance(resume_identity, dict):
                raise ValueError(f"Checkpoint resume identity is invalid for step {step}")
            unsigned_identity = dict(resume_identity)
            identity_sha256 = unsigned_identity.pop("identity_sha256", None)
            if (
                unsigned_identity.get("schema_version") != 1
                or not isinstance(identity_sha256, str)
                or cls.canonical_sha256(unsigned_identity) != identity_sha256
            ):
                raise ValueError(f"Checkpoint resume identity hash mismatch for step {step}")
        checkpoint_annotations = world_metadata.get("annotations")
        if checkpoint_annotations is not None and not isinstance(
            checkpoint_annotations,
            dict,
        ):
            raise ValueError(f"Checkpoint annotations are invalid for step {step}")

        agent_threads = world_metadata.get("agent_threads")
        if not isinstance(agent_threads, dict):
            raise ValueError(f"Agent Thread references missing for step {step}")
        manifest_name = marker.get("agent_threads_manifest")
        manifest_sha256 = marker.get("agent_threads_sha256")
        if (
            agent_threads.get("schema_version") != 1
            or agent_threads.get("manifest") != manifest_name
            or agent_threads.get("manifest_sha256") != manifest_sha256
            or not isinstance(manifest_name, str)
            or not isinstance(manifest_sha256, str)
        ):
            raise ValueError(
                f"Agent Thread references do not match checkpoint marker for step {step}"
            )
        from .agent.thread_store import AgentThreadStore

        agent_thread_manifest = AgentThreadStore.validate_checkpoint_manifest(
            save_dir,
            manifest_name,
            expected_sha256=manifest_sha256,
            checkpoint_id=checkpoint_id,
            step=step,
        )
        if (
            agent_threads.get("thread_count")
            != len(agent_thread_manifest["threads"])
            or agent_threads.get("by_agent") != agent_thread_manifest["by_agent"]
            or agent_threads.get("threads") != agent_thread_manifest["threads"]
        ):
            raise ValueError(
                f"Agent Thread checkpoint references are inconsistent for step {step}"
            )

        backup_name = marker.get("chroma_backup")
        if backup_name is None:
            raise FileNotFoundError(
                f"Chroma backup missing for complete checkpoint step {step}"
            )
        backup_dir = cls._safe_child(
            backup_root,
            backup_name,
            component="Chroma backup",
        )
        if not backup_dir.is_dir() or backup_dir.is_symlink():
            raise FileNotFoundError(f"Chroma backup missing for complete checkpoint step {step}")
        backup_manifest = backup_dir / "_checkpoint.json"
        if not backup_manifest.is_file() or backup_manifest.is_symlink():
            raise FileNotFoundError(f"Chroma backup manifest missing for step {step}")
        if not derived_memory_required:
            cls._validate_memoryless_chroma_backup(backup_dir)
        with backup_manifest.open("r", encoding="utf-8") as handle:
            chroma_metadata = json.load(handle)
        expected_chroma_hash = marker.get("chroma_sha256")
        actual_chroma_hash = cls._directory_content_sha256(backup_dir)
        if (
            chroma_metadata.get("checkpoint_id") != checkpoint_id
            or isinstance(chroma_metadata.get("step"), bool)
            or not isinstance(chroma_metadata.get("step"), int)
            or chroma_metadata.get("step") != step
            or chroma_metadata.get("checkpoint_version") != marker.get("checkpoint_version")
            or chroma_metadata.get("memory_required") is not derived_memory_required
            or not isinstance(expected_chroma_hash, str)
            or chroma_metadata.get("content_sha256") != expected_chroma_hash
            or actual_chroma_hash != expected_chroma_hash
        ):
            raise ValueError(f"Chroma backup does not match complete marker for step {step}")

        return {
            "step": step,
            "checkpoint_id": checkpoint_id,
            "marker": marker,
            "marker_file": marker_file,
            "checkpoint_file": checkpoint_file,
            "checkpoint_data": checkpoint_data,
            "chroma_backup_dir": backup_dir,
            "agent_thread_manifest": agent_thread_manifest,
            "memory_required": derived_memory_required,
        }

    def resolve_checkpoint(self, step: Optional[int] = None) -> Dict[str, Any]:
        """Resolve a checkpoint in this run through the read-only resolver."""
        self._assert_usable()
        return self.resolve_checkpoint_from(self.save_dir, step)

    async def load_checkpoint(
        self,
        step: Optional[int] = None,
        *,
        memory_required: Optional[bool] = None,
        restore_chroma: bool = True,
        event_logger: Optional[Any] = None,
        event_log_path: Optional[str] = None,
        environment_factory: Optional[Any] = None,
    ) -> Tuple['World', 'Schedule']:
        """
        Load checkpoint and reconstruct World and Schedule objects.
        
        According to the design document:
        1. Find checkpoint files and Chroma backup for step
        2. Restore Chroma vector store
        3. Deserialize checkpoint data and rebuild World
        4. Create Schedule object with correct internal state
        5. Return reconstructed objects
        
        Args:
            step: Step number to load. ``None`` selects the latest complete step.
            
        Returns:
            Tuple of (World, Schedule) objects
        """
        record = self.resolve_checkpoint(step)
        return await self._load_checkpoint_record(
            record,
            memory_required=memory_required,
            restore_chroma=restore_chroma,
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
        restore_chroma: bool = True,
        event_logger: Optional[Any] = None,
        event_log_path: Optional[str] = None,
        environment_factory: Optional[Any] = None,
    ) -> Tuple['World', 'Schedule']:
        """Load a source checkpoint into this manager without mutating source_run."""
        self._assert_usable()
        record = self.resolve_checkpoint_from(source_run, step)
        return await self._load_checkpoint_record(
            record,
            memory_required=memory_required,
            restore_chroma=restore_chroma,
            event_logger=event_logger,
            event_log_path=event_log_path,
            environment_factory=environment_factory,
        )

    async def _load_checkpoint_record(
        self,
        record: Dict[str, Any],
        *,
        memory_required: Optional[bool],
        restore_chroma: bool,
        event_logger: Optional[Any],
        event_log_path: Optional[str],
        environment_factory: Optional[Any],
    ) -> Tuple['World', 'Schedule']:
        chroma_restored = False
        try:
            resolved_step = self._normalize_step(record["step"])
            checkpoint_data = record["checkpoint_data"]
            checkpoint_requires_memory = bool(record["memory_required"])
            if memory_required is True and record["chroma_backup_dir"] is None:
                raise FileNotFoundError(
                    f"Memory backup missing for complete checkpoint step {resolved_step}"
                )

            if restore_chroma:
                await self._restore_chroma_store(
                    resolved_step,
                    checkpoint_id=record["checkpoint_id"],
                    memory_required=checkpoint_requires_memory,
                    backup_dir=record["chroma_backup_dir"],
                    use_default_backup=False,
                )
                chroma_restored = True
            
            # 3. Reconstruct World object
            from .core_data import World
            
            # Create World with same event log path pattern
            events_file = event_log_path or str(self.events_dir / f"events_from_step_{resolved_step}.jsonl")
            world = World(step=resolved_step, event_log_path=events_file, event_logger=event_logger)
            if environment_factory is not None:
                world.set_environment_factory(environment_factory)
            
            # Restore agents data
            world.agents_data = self._deserialize_agents_data(checkpoint_data["agents_data"])
            world._agent_types = dict(
                (checkpoint_data.get("world_metadata") or {}).get("agent_types") or {}
            )
            resume_identity = (checkpoint_data.get("world_metadata") or {}).get(
                "resume_identity"
            )
            if isinstance(resume_identity, dict):
                world._resume_identity = dict(resume_identity)
            checkpoint_annotations = (checkpoint_data.get("world_metadata") or {}).get(
                "annotations"
            )
            if checkpoint_annotations is not None:
                if not isinstance(checkpoint_annotations, dict):
                    raise ValueError("Checkpoint annotations must be a mapping")
                world._checkpoint_annotations = copy.deepcopy(checkpoint_annotations)
            
            # Restore environment data
            env_data = checkpoint_data["environment_data"]
            world.environment_data = {
                key: value
                for key, value in env_data.items()
                if key != "snapshot"
            }
            world.environment_data.setdefault("type", "base")
            world.environment_data.setdefault("state", {})
            
            # Restore environment from snapshot if available
            if "snapshot" in env_data:
                environment = world.get_environment()
                environment.restore_from_snapshot(env_data["snapshot"])
            
            # 4. Create Schedule object
            # Note: Schedule creation requires configuration, which we don't have here
            # The caller needs to provide the schedule configuration
            # For now, return None for schedule - this should be handled by SimEngine
            schedule = None
            
            logger.info(f"Loaded checkpoint for step {resolved_step}")
            return world, schedule
            
        except Exception as e:
            if chroma_restored:
                self.disable_after_restore_failure()
            logger.error(f"Failed to load checkpoint for step {record.get('step')}: {e}")
            raise
    
    async def replay_events_from_checkpoint(self, world: 'World', checkpoint_step: int) -> 'World':
        """
        Replay events from the last checkpoint to bring World to latest state.
        
        Args:
            world: World object loaded from checkpoint
            checkpoint_step: Step number of the checkpoint
            
        Returns:
            Updated World object with events replayed
        """
        checkpoint_step = self._normalize_step(checkpoint_step)
        events_file = self.events_dir / f"events_from_step_{checkpoint_step}.jsonl"
        
        if not events_file.exists():
            logger.info(f"No event log found for replay from step {checkpoint_step}")
            return world
        
        try:
            events_replayed = 0
            with open(events_file, 'r') as f:
                for line in f:
                    if line.strip():
                        event_data = json.loads(line)
                        # Apply event to world state
                        await self._apply_event_to_world(world, event_data)
                        events_replayed += 1
            
            logger.info(f"Replayed {events_replayed} events from checkpoint step {checkpoint_step}")
            return world
            
        except Exception as e:
            logger.error(f"Failed to replay events from step {checkpoint_step}: {e}")
            raise
    
    async def _backup_chroma_store(
        self,
        step: int,
        *,
        checkpoint_id: str,
        memory_required: bool,
    ) -> Path:
        """Build the Chroma component before the complete marker is published."""
        step = self._normalize_step(step)
        temp_dir = self.chroma_backup_dir / f".step_{step:06d}.{checkpoint_id}.tmp"
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
            if memory_required:
                self._sync_chroma_to_store()

                source_dir = (
                    self.chroma_runtime_path
                    if self.chroma_runtime_path and self.chroma_runtime_path.exists()
                    else self.chroma_store_path
                )

                if not source_dir.exists():
                    raise FileNotFoundError(f"Chroma store directory not found at {source_dir}")

                self._copy_directory(source_dir, temp_dir)
            else:
                # Rule-only checkpoints retain only the versioned metadata
                # container.  Never copy or sync a pre-existing runtime store.
                temp_dir.mkdir(parents=True, exist_ok=True)
            content_sha256 = self._directory_content_sha256(temp_dir)
            self._atomic_write_json(
                temp_dir / "_checkpoint.json",
                {
                    "checkpoint_id": checkpoint_id,
                    "step": step,
                    "checkpoint_version": self.CHECKPOINT_VERSION,
                    "memory_required": memory_required,
                    "content_sha256": content_sha256,
                    "created_at": time.time(),
                },
            )

            # Flush every copied file and directory entry before exposing the
            # component name.  The marker is the only publication point for a
            # complete pair; its parent directory is synced after replacement.
            self._fsync_tree(temp_dir)
            backup_dir = self._chroma_backup_path(step, checkpoint_id=checkpoint_id)
            temp_dir.replace(backup_dir)
            self._fsync_directory(self.chroma_backup_dir)
            logger.debug("Backed up Chroma store for step %s -> %s", step, backup_dir)
            return backup_dir

        except Exception as e:
            logger.error(f"Failed to backup Chroma store for step {step}: {e}")
            raise
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    async def _restore_chroma_store(
        self,
        step: int,
        *,
        checkpoint_id: str,
        memory_required: bool,
        backup_dir: Optional[Path] = None,
        use_default_backup: bool = True,
    ) -> None:
        """Restore Chroma transactionally, rolling back every destination path."""
        self._assert_usable()
        step = self._normalize_step(step)
        rollback_paths: Dict[Path, Path] = {}
        staged_paths: Dict[Path, Path] = {}
        replaced_targets: List[Path] = []
        existing_targets: set[Path] = set()
        try:
            if backup_dir is None and not use_default_backup:
                raise FileNotFoundError(f"Chroma backup not found for step {step}")
            backup_dir = backup_dir or self._chroma_backup_path(step)
            if not backup_dir.exists():
                raise FileNotFoundError(f"Chroma backup not found for step {step}")

            manifest_path = backup_dir / "_checkpoint.json"
            if not manifest_path.is_file():
                raise FileNotFoundError(f"Chroma backup manifest not found for step {step}")
            with manifest_path.open("r", encoding="utf-8") as handle:
                manifest = json.load(handle)
            if not memory_required:
                self._validate_memoryless_chroma_backup(backup_dir)
            content_sha256 = self._directory_content_sha256(backup_dir)
            if (
                manifest.get("checkpoint_id") != checkpoint_id
                or isinstance(manifest.get("step"), bool)
                or not isinstance(manifest.get("step"), int)
                or manifest.get("step") != step
                or manifest.get("checkpoint_version") != self.CHECKPOINT_VERSION
                or manifest.get("memory_required") is not memory_required
                or manifest.get("content_sha256") != content_sha256
            ):
                raise ValueError(f"Chroma backup does not match checkpoint {checkpoint_id}")

            # Chroma has no explicit close API. Persistent writes already land
            # in its active directory; detach the handle before path swaps.
            if memory_required:
                self._sync_chroma_to_store()
            self._chroma_client = None

            targets = [self.chroma_store_path]
            if self._using_fallback_runtime and self.chroma_runtime_path != self.chroma_store_path:
                targets.insert(0, self.chroma_runtime_path)
            unique_targets = list(dict.fromkeys(targets))

            token = uuid.uuid4().hex
            for target_dir in unique_targets:
                target_dir.parent.mkdir(parents=True, exist_ok=True)
                staged = target_dir.parent / f".{target_dir.name}.{token}.restore"
                rollback = target_dir.parent / f".{target_dir.name}.{token}.rollback"
                shutil.rmtree(staged, ignore_errors=True)
                shutil.rmtree(rollback, ignore_errors=True)
                self._copy_directory(backup_dir, staged)
                (staged / "_checkpoint.json").unlink(missing_ok=True)
                staged_paths[target_dir] = staged
                rollback_paths[target_dir] = rollback

            for target_dir in unique_targets:
                rollback = rollback_paths[target_dir]
                staged = staged_paths[target_dir]
                if target_dir.exists():
                    existing_targets.add(target_dir)
                    target_dir.replace(rollback)
                    replaced_targets.append(target_dir)
                else:
                    replaced_targets.append(target_dir)
                staged.replace(target_dir)

            if memory_required:
                # Avoid _ensure_chroma_client here: it syncs during the open and
                # would make rollback span another destructive copy.
                self._chroma_client = self._create_chroma_client()

            for rollback in rollback_paths.values():
                shutil.rmtree(rollback, ignore_errors=True)

            logger.debug("Restored Chroma store from step %s", step)

        except Exception as e:
            self._chroma_client = None
            rollback_errors: List[Exception] = []
            for target_dir in reversed(replaced_targets):
                try:
                    rollback = rollback_paths[target_dir]
                    shutil.rmtree(target_dir, ignore_errors=True)
                    if target_dir in existing_targets and rollback.exists():
                        rollback.replace(target_dir)
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
            if rollback_errors:
                self._restore_failed = True
                logger.critical(
                    "Chroma restore rollback failed; persistence manager disabled: %s",
                    rollback_errors,
                )
            logger.error(f"Failed to restore Chroma store for step {step}: {e}")
            raise
        finally:
            for staged in staged_paths.values():
                shutil.rmtree(staged, ignore_errors=True)
    
    async def _apply_event_to_world(self, world: 'World', event_data: Dict[str, Any]) -> None:
        """Apply a single event to world state (event replay)."""
        event_type = (event_data.get("event_type") or "").upper()

        if event_type == "STATE_CHANGE":
            target_type = event_data.get("target_type")
            target_id = event_data.get("target_id")
            path = event_data.get("path") or []
            value = event_data.get("value")

            if target_type is not None:
                self._apply_state_change_event(world, target_type, target_id, path, value)
                logger.debug("Applied STATE_CHANGE event to %s:%s path=%s", target_type, target_id, path)
                return

            # legacy fallback
            agent_id = event_data.get("agent_id")
            if agent_id and agent_id in world.agents_data:
                changes = event_data.get("changes", {})
                for key, change_value in changes.items():
                    agent_state = world.agents_data[agent_id].get("state", {})
                    if isinstance(change_value, dict):
                        agent_state.setdefault(key, {}).update(change_value)
                    else:
                        agent_state[key] = change_value
                logger.debug("Applied legacy STATE_CHANGE for agent %s", agent_id)
                return

        elif event_type == "MEMORY_CHANGE":
            # 记忆事件暂不重放（向量存储在快照与备份中恢复）
            logger.debug("Skipping MEMORY_CHANGE replay (handled via Chroma restore)")

        payload = event_data.get("event_data") or {}
        state_patches = payload.get("state_patches")
        if isinstance(state_patches, list):
            self._apply_state_patches(world, state_patches)
            logger.debug("Applied %s generic state patch(es)", len(state_patches))

    def _apply_state_change_event(
        self,
        world: 'World',
        target_type: Optional[str],
        target_id: Optional[str],
        path: Iterable[str],
        value: Any
    ) -> None:
        if target_type == "agent":
            if not target_id or target_id not in world.agents_data:
                return
            agent_entry = world.agents_data[target_id]
            container = agent_entry.setdefault("state", {})
        elif target_type == "environment":
            container = world.environment_data.setdefault("state", {})
        else:
            return

        path_segments = [segment for segment in path if segment is not None]
        self._set_nested_value(container, path_segments, value)

    @staticmethod
    def _set_nested_value(target: Dict[str, Any], path: Iterable[str], value: Any) -> None:
        current = target
        segments = list(path)
        if not segments:
            if isinstance(current, dict):
                current.clear()
                if isinstance(value, dict):
                    current.update(value)
            return

        for segment in segments[:-1]:
            current = current.setdefault(segment, {})
        current[segments[-1]] = value

    def _apply_state_patches(self, world: 'World', patches: Iterable[Dict[str, Any]]) -> None:
        """Apply generic state patches carried by an event payload."""
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            target_type = patch.get("target_type")
            target_id = patch.get("target_id")
            operation = str(patch.get("operation") or "set").lower()
            path = patch.get("path") or []
            value = patch.get("value")

            if operation == "set":
                self._apply_state_change_event(world, target_type, target_id, path, value)
            elif operation == "increment":
                self._apply_increment_patch(world, target_type, target_id, path, value)
            elif operation == "merge":
                self._apply_merge_patch(world, target_type, target_id, path, value)
            else:
                logger.debug("Skipping unsupported state patch operation: %s", operation)

    def _get_patch_root(
        self,
        world: 'World',
        target_type: Optional[str],
        target_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if target_type == "agent":
            if not target_id or target_id not in world.agents_data:
                return None
            return world.agents_data[target_id].setdefault("state", {})
        if target_type == "environment":
            return world.environment_data.setdefault("state", {})
        return None

    def _get_patch_parent(
        self,
        world: 'World',
        target_type: Optional[str],
        target_id: Optional[str],
        path: Iterable[str],
    ) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        root = self._get_patch_root(world, target_type, target_id)
        if root is None:
            return None, None
        segments = [segment for segment in path if segment is not None]
        if not segments:
            return None, None
        parent = root
        for segment in segments[:-1]:
            current = parent.setdefault(segment, {})
            if not isinstance(current, dict):
                current = {}
                parent[segment] = current
            parent = current
        return parent, segments[-1]

    def _apply_increment_patch(
        self,
        world: 'World',
        target_type: Optional[str],
        target_id: Optional[str],
        path: Iterable[str],
        value: Any,
    ) -> None:
        parent, key = self._get_patch_parent(world, target_type, target_id, path)
        if parent is None or key is None:
            return
        try:
            delta = int(value)
        except (TypeError, ValueError):
            return
        parent[key] = int(parent.get(key, 0) or 0) + delta

    def _apply_merge_patch(
        self,
        world: 'World',
        target_type: Optional[str],
        target_id: Optional[str],
        path: Iterable[str],
        value: Any,
    ) -> None:
        if not isinstance(value, dict):
            return
        parent, key = self._get_patch_parent(world, target_type, target_id, path)
        if parent is None or key is None:
            return
        target = parent.setdefault(key, {})
        if isinstance(target, dict):
            target.update(value)
    
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
    
    def _deserialize_agents_data(self, serialized_data: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize agents data from checkpoint."""
        deserialized = {}
        for agent_id, agent_info in serialized_data.items():
            entry = dict(agent_info)
            entry["id"] = str(agent_info.get("id") or agent_id)
            entry["type"] = agent_info.get("type", "unknown")
            entry["archetype"] = agent_info.get("archetype", "rule")
            entry["state"] = dict(agent_info.get("state") or {})
            entry["properties"] = dict(agent_info.get("properties") or {})
            entry["reminders"] = list(agent_info.get("reminders") or [])
            entry.setdefault("persona", "")
            entry.setdefault("persona_instance", "")
            entry.setdefault("persona_type", "")
            deserialized[agent_id] = entry
        return deserialized
    
    async def get_available_checkpoints(self) -> List[int]:
        """Get complete, recoverable checkpoint step numbers."""
        return self._get_available_checkpoints_sync()

    def _get_available_checkpoints_sync(self) -> List[int]:
        steps = []

        for marker_file in self.complete_checkpoints_dir.glob("step_*.json"):
            try:
                step_str = marker_file.stem.split('_')[1]
                step = int(step_str)
                self.resolve_checkpoint(step)
                steps.append(step)
            except (IndexError, ValueError, OSError, json.JSONDecodeError) as exc:
                logger.warning("Ignoring incomplete checkpoint marker %s: %s", marker_file.name, exc)
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
            "architecture_version": "unified_state_v2"
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

    def get_available_chroma_backups(self) -> List[int]:
        """Return list of available Chroma backup steps."""
        backup_steps: set[int] = set()

        for backup_dir in self.chroma_backup_dir.glob("step_*"):
            if not backup_dir.is_dir():
                continue
            step_token = backup_dir.name.split("_", 1)[1].split(".", 1)[0]
            if not step_token:
                continue
            try:
                backup_steps.add(int(step_token))
            except ValueError:
                continue

        return sorted(backup_steps)

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
