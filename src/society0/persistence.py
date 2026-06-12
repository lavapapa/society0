"""
SimEngine V2: PersistenceManager - Unified persistence for the new architecture.

Handles checkpointing of World state, Chroma vector store data,
and event replay according to the final integration design document.
"""

from typing import Dict, Any, List, Optional, Tuple, TYPE_CHECKING, Iterable
from pathlib import Path
import json
import traceback
import time
import shutil
import asyncio
import logging
import os
import uuid
import hashlib
from datetime import datetime
import threading

from .logging import LogField, SystemEvent
from .jmespath_context import NodeSnapshot, OperatorSnapshot

if TYPE_CHECKING:
    from .core_data import World
    from typing import Any as Schedule
    from .event_logger import Event

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

    def __init__(self, save_dir: str):
        """
        Initialize persistence manager with save directory.

        Args:
            save_dir: Directory to save all simulation data
        """
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

        # 立即初始化_chroma_client属性为None，确保属性存在
        self._chroma_client = None

        # Create subdirectories according to new design
        self.checkpoints_dir = self.save_dir / "checkpoints"
        self.chroma_backup_dir = self.save_dir / "chroma_backups"
        self.metadata_dir = self.save_dir / "metadata"
        self.events_dir = self.save_dir / "events"
        self.diffs_dir = self.save_dir / "diffs"
        self.interviews_dir = self.save_dir / "interviews"

        for dir_path in [
            self.checkpoints_dir,
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

        # 尽早创建 Chroma 客户端；失败时允许后续懒加载重试
        self._ensure_chroma_client()

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
        return self._ensure_chroma_client()

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
            with open(metadata_file, 'w') as f:
                json.dump(self.experiment_metadata, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save metadata: {e}")
    
    async def save_checkpoint(
        self,
        world: 'World',
        schedule: 'Schedule',
        *,
        step_metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Save a complete checkpoint with World state and 向量存储（Chroma）备份。
        
        According to the design document:
        1. Get current step number
        2. Call world.environment.snapshot() for custom environment data
        3. Serialize world data to checkpoint_{step}.json
        4. Backup Chroma vector store
        5. Update metadata.json
        
        Args:
            world: The World instance to checkpoint
            schedule: The Schedule instance for progress tracking
        """
        step = world.step
        checkpoint_file = self.checkpoints_dir / f"checkpoint_{step:06d}.json"
        checkpoint_start = time.time()
        
        try:
            logger.info(f"Creating checkpoint for step {step}")

            environment = world.get_environment()
            env_snapshot = environment.snapshot()

            environment_payload = {
                "type": world.environment_data["type"],
                "state": dict(world.environment_data["state"]),
                "snapshot": env_snapshot,
            }
            observation_payload = self._build_observation_data(
                world,
                schedule,
                step_metrics,
                include_agents=False,
            )

            world_metadata = {
                "checkpoint_version": "unified_state_v2",
                "created_by": "PersistenceManager.save_checkpoint",
            }

            temp_path = checkpoint_file.with_suffix(".json.tmp")
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

            with temp_path.open("w", encoding="utf-8") as fp:
                fp.write("{\n")
                top_level_fields: List[Tuple[str, str]] = [
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
                    top_level_fields.extend([("metrics", "value"), ("step_metrics", "value")])

                for idx, (field_key, field_type) in enumerate(top_level_fields):
                    last_field = idx == len(top_level_fields) - 1
                    if field_type == "value":
                        value_mapping = {
                            "step": step,
                            "timestamp": current_time,
                            "environment_data": environment_payload,
                            "world_metadata": world_metadata,
                            "source_step": step,
                        }
                        if include_metrics and field_key in {"metrics", "step_metrics"}:
                            value_mapping[field_key] = step_metrics  # type: ignore[assignment]
                        _write_value_field(
                            fp,
                            2,
                            field_key,
                            value_mapping[field_key],
                            last=last_field,
                        )
                    elif field_type == "agents":
                        _write_agents_field(fp, 2, field_key, world.agents_data, last=last_field)
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
                os.fsync(fp.fileno())

            temp_path.replace(checkpoint_file)

            checkpoint_size = checkpoint_file.stat().st_size if checkpoint_file.exists() else None

            backup_start = time.time()
            await self._backup_chroma_store(step)
            backup_duration = time.time() - backup_start
            
            # 5. Update metadata
            self.experiment_metadata.setdefault("total_steps", 0)
            self.experiment_metadata.setdefault("last_checkpoint_step", -1)
            self.experiment_metadata["last_checkpoint_step"] = step
            self.experiment_metadata["total_steps"] = max(
                self.experiment_metadata["total_steps"], step)
            self._save_metadata()
            
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

        except Exception as e:
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
    
    async def load_checkpoint(self, step: int) -> Tuple['World', 'Schedule']:
        """
        Load checkpoint and reconstruct World and Schedule objects.
        
        According to the design document:
        1. Find checkpoint files and Chroma backup for step
        2. Restore Chroma vector store
        3. Deserialize checkpoint data and rebuild World
        4. Create Schedule object with correct internal state
        5. Return reconstructed objects
        
        Args:
            step: Step number to load
            
        Returns:
            Tuple of (World, Schedule) objects
        """
        checkpoint_file = self.checkpoints_dir / f"checkpoint_{step:06d}.json"
        
        if not checkpoint_file.exists():
            raise FileNotFoundError(f"Checkpoint not found for step {step}")
        
        try:
            # 1. Load checkpoint data
            with open(checkpoint_file, 'r') as f:
                checkpoint_data = json.load(f)
            
            # 2. Restore Chroma vector store
            await self._restore_chroma_store(step)
            
            # 3. Reconstruct World object
            from .core_data import World
            
            # Create World with same event log path pattern
            events_file = self.events_dir / f"events_from_step_{step}.jsonl"
            world = World(step=step, event_log_path=str(events_file))
            
            # Restore agents data
            world.agents_data = self._deserialize_agents_data(checkpoint_data["agents_data"])
            
            # Restore environment data
            env_data = checkpoint_data["environment_data"]
            world.environment_data = {
                "type": env_data["type"],
                "state": env_data["state"]
            }
            
            # Restore environment from snapshot if available
            if "snapshot" in env_data:
                environment = world.get_environment()
                environment.restore_from_snapshot(env_data["snapshot"])
            
            # 4. Create Schedule object
            # Note: Schedule creation requires configuration, which we don't have here
            # The caller needs to provide the schedule configuration
            # For now, return None for schedule - this should be handled by SimEngine
            schedule = None
            
            logger.info(f"Loaded checkpoint for step {step}")
            return world, schedule
            
        except Exception as e:
            logger.error(f"Failed to load checkpoint for step {step}: {e}")
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
    
    async def _backup_chroma_store(self, step: int) -> None:
        """Backup Chroma vector store directory for the given step."""
        try:
            self._sync_chroma_to_store()

            source_dir = (
                self.chroma_runtime_path
                if self.chroma_runtime_path and self.chroma_runtime_path.exists()
                else self.chroma_store_path
            )

            if not source_dir.exists():
                logger.warning(f"Chroma store directory not found at {source_dir}")
                return

            backup_dir = self.chroma_backup_dir / f"step_{step:06d}"
            self._copy_directory(source_dir, backup_dir)
            logger.debug("Backed up Chroma store for step %s -> %s", step, backup_dir)

        except Exception as e:
            logger.error(f"Failed to backup Chroma store for step {step}: {e}")
            raise

    async def _restore_chroma_store(self, step: int) -> None:
        """Restore Chroma vector store directory from backup."""
        try:
            backup_dir = self.chroma_backup_dir / f"step_{step:06d}"
            if not backup_dir.exists():
                logger.warning(f"Chroma backup not found for step {step}")
                return

            # 先同步并释放现有客户端（Chroma 无显式 close，因此仅同步）
            self._sync_chroma_to_store()
            self._chroma_client = None

            target_dir = (
                self.chroma_runtime_path
                if self._using_fallback_runtime
                else self.chroma_store_path
            )

            target_dir.mkdir(parents=True, exist_ok=True)
            self._copy_directory(backup_dir, target_dir)

            # 重新创建客户端并同步目录状态
            self._chroma_client = None
            self._ensure_chroma_client()

            logger.debug("Restored Chroma store from step %s", step)

        except Exception as e:
            logger.error(f"Failed to restore Chroma store for step {step}: {e}")
            raise
    
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
    
    @staticmethod
    def _serialize_agent_entry(agent_info: Dict[str, Any]) -> Dict[str, Any]:
        """序列化单个 Agent 数据。"""
        return {
            "type": agent_info["type"],
            "archetype": agent_info["archetype"],
            "state": dict(agent_info["state"]),
            "properties": dict(agent_info["properties"]),
            "reminders": list(agent_info["reminders"]),
        }

    def _serialize_agents_data(self, agents_data: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize agents data for checkpointing."""
        serialized = {}
        for agent_id, agent_info in agents_data.items():
            serialized[agent_id] = self._serialize_agent_entry(agent_info)
        return serialized
    
    def _deserialize_agents_data(self, serialized_data: Dict[str, Any]) -> Dict[str, Any]:
        """Deserialize agents data from checkpoint."""
        deserialized = {}
        for agent_id, agent_info in serialized_data.items():
            deserialized[agent_id] = {
                "type": agent_info["type"],
                "archetype": agent_info["archetype"], 
                "state": agent_info["state"],
                "properties": agent_info["properties"],
                "reminders": agent_info["reminders"]
            }
        return deserialized
    
    async def get_available_checkpoints(self) -> List[int]:
        """Get list of available checkpoint step numbers."""
        steps = []
        
        for checkpoint_file in self.checkpoints_dir.glob("checkpoint_*.json"):
            try:
                # Extract step number from filename
                step_str = checkpoint_file.stem.split('_')[1]
                steps.append(int(step_str))
            except (IndexError, ValueError):
                logger.warning(f"Invalid checkpoint filename: {checkpoint_file.name}")
                continue
        
        return sorted(steps)
    
    def get_experiment_info(self) -> Dict[str, Any]:
        """Get experiment information and statistics."""
        # Use sync version for checkpoints
        available_checkpoints = []
        for checkpoint_file in self.checkpoints_dir.glob("checkpoint_*.json"):
            try:
                step_str = checkpoint_file.stem.split('_')[1]
                available_checkpoints.append(int(step_str))
            except (IndexError, ValueError):
                continue
        available_checkpoints.sort()
        
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
        backup_steps: List[int] = []

        for backup_dir in self.chroma_backup_dir.glob("step_*"):
            if not backup_dir.is_dir():
                continue
            parts = backup_dir.name.split("_")
            if len(parts) != 2:
                continue
            try:
                backup_steps.append(int(parts[1]))
            except ValueError:
                continue

        backup_steps.sort()
        return backup_steps

    def close(self) -> None:
        """
        关闭持久化管理器。
        - 若使用 tmpfs runtime，先同步回磁盘 store
        - 按配置清理 tmpfs 目录
        """
        with self._chroma_init_lock:
            try:
                self._sync_chroma_to_store()
            except Exception as exc:
                logger.warning("Failed to sync Chroma store during close: %s", exc)
            finally:
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
