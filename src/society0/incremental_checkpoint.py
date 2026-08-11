"""Checkpoint v4 的增量状态日志与不可变段存储。"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping


class PersistenceKind(str, Enum):
    REPLACEABLE = "replaceable"
    APPEND_ONLY_MAP = "append_only_map"
    APPEND_ONLY_LIST = "append_only_list"
    TRANSIENT = "transient"


@dataclass(frozen=True, slots=True)
class SealedTickDelta:
    step: int
    replacements: tuple[dict[str, Any], ...]
    appends: tuple[dict[str, Any], ...]


class StateDeltaJournal:
    """在 canonical writer 的调用栈内捕获一个 Tick 的持久化变化。"""

    def __init__(self, declarations: Mapping[tuple[str, ...], PersistenceKind | str]):
        self._declarations = {
            tuple(path): PersistenceKind(kind) for path, kind in declarations.items()
        }
        self._active_step: int | None = None
        self._sequence = 0
        self._replacements: dict[tuple[str, ...], dict[str, Any]] = {}
        self._appends: list[dict[str, Any]] = []
        self._pending_map_ids: set[tuple[tuple[str, ...], str]] = set()
        self._committed_map_ids: set[tuple[tuple[str, ...], str]] = set()

    def begin_tick(self, step: int) -> None:
        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            raise ValueError("step must be a non-negative integer")
        if self._active_step is not None:
            raise RuntimeError("a tick delta is already active")
        self._active_step = step
        self._sequence = 0
        self._replacements = {}
        self._appends = []
        self._pending_map_ids = set()

    def _kind(self, path: Iterable[str]) -> tuple[tuple[str, ...], PersistenceKind]:
        self._require_active()
        normalized = tuple(str(part) for part in path)
        try:
            return normalized, self._declarations[normalized]
        except KeyError as exc:
            raise ValueError(f"undeclared persistence path: {normalized!r}") from exc

    def _next_sequence(self) -> int:
        sequence = self._sequence
        self._sequence += 1
        return sequence

    def record_set(self, path: Iterable[str], value: Any) -> None:
        normalized, kind = self._kind(path)
        if kind is PersistenceKind.TRANSIENT:
            return
        if kind is not PersistenceKind.REPLACEABLE:
            raise ValueError(f"set is not allowed for {kind.value}: {normalized!r}")
        self._replacements[normalized] = {
            "path": list(normalized),
            "operation": "set",
            "value": copy.deepcopy(value),
            "sequence": self._next_sequence(),
        }

    def record_delete(self, path: Iterable[str]) -> None:
        normalized, kind = self._kind(path)
        if kind is PersistenceKind.TRANSIENT:
            return
        if kind is not PersistenceKind.REPLACEABLE:
            raise ValueError(f"delete is not allowed for {kind.value}: {normalized!r}")
        self._replacements[normalized] = {
            "path": list(normalized),
            "operation": "delete",
            "sequence": self._next_sequence(),
        }

    def record_map_create(self, path: Iterable[str], fact_id: str, value: Any) -> None:
        normalized, kind = self._kind(path)
        if kind is not PersistenceKind.APPEND_ONLY_MAP:
            raise ValueError(f"map create is not allowed for {kind.value}: {normalized!r}")
        normalized_id = str(fact_id)
        identity = (normalized, normalized_id)
        if identity in self._pending_map_ids or identity in self._committed_map_ids:
            raise ValueError(f"duplicate append-only map id: {normalized_id}")
        self._pending_map_ids.add(identity)
        self._appends.append(
            {
                "path": list(normalized),
                "operation": "map_create",
                "id": normalized_id,
                "value": copy.deepcopy(value),
                "sequence": self._next_sequence(),
            }
        )

    def record_append(self, path: Iterable[str], value: Any) -> None:
        normalized, kind = self._kind(path)
        if kind is not PersistenceKind.APPEND_ONLY_LIST:
            raise ValueError(f"append is not allowed for {kind.value}: {normalized!r}")
        self._appends.append(
            {
                "path": list(normalized),
                "operation": "append",
                "value": copy.deepcopy(value),
                "sequence": self._next_sequence(),
            }
        )

    def record_proxy_operation(
        self,
        container_path: Iterable[str],
        operation: str,
        key: str | int | None,
        value: Any = None,
    ) -> None:
        """接收代理在修改底层容器之前发出的规范化写入。"""

        container = tuple(str(part) for part in container_path)
        container_kind = self._declarations.get(container)
        child = container + (str(key),) if key is not None else container
        child_kind = self._declarations.get(child)

        if container_kind is PersistenceKind.APPEND_ONLY_MAP:
            if operation != "set" or key is None:
                raise ValueError("append-only map only accepts new keys")
            self.record_map_create(container, str(key), value)
            return
        if container_kind is PersistenceKind.APPEND_ONLY_LIST:
            if operation != "append":
                raise ValueError("append-only list only accepts append")
            self.record_append(container, value)
            return
        if child_kind in (PersistenceKind.REPLACEABLE, PersistenceKind.TRANSIENT):
            if operation == "set":
                self.record_set(child, value)
                return
            if operation == "delete":
                self.record_delete(child)
                return
        raise ValueError(f"undeclared or unsupported persistence write: {child!r}")

    def seal_tick(self) -> SealedTickDelta:
        self._require_active()
        assert self._active_step is not None
        result = SealedTickDelta(
            step=self._active_step,
            replacements=tuple(
                sorted(self._replacements.values(), key=lambda item: item["sequence"])
            ),
            appends=tuple(sorted(self._appends, key=lambda item: item["sequence"])),
        )
        self._committed_map_ids.update(self._pending_map_ids)
        self._clear_active()
        return result

    def abort_tick(self) -> None:
        self._require_active()
        self._clear_active()

    def _clear_active(self) -> None:
        self._active_step = None
        self._sequence = 0
        self._replacements = {}
        self._appends = []
        self._pending_map_ids = set()

    def _require_active(self) -> None:
        if self._active_step is None:
            raise RuntimeError("no active tick delta")


class V4CheckpointStore:
    """发布和恢复 v4 增量链；complete marker 是唯一提交点。"""

    VERSION = "complete_step_v4"

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.base = self.root / "checkpoints" / "v4"
        self.segments_dir = self.base / "segments"
        self.replacements_dir = self.base / "replacements"
        self.manifests_dir = self.base / "manifests"
        self.complete_dir = self.base / "complete"
        for directory in (
            self.segments_dir,
            self.replacements_dir,
            self.manifests_dir,
            self.complete_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.metrics = {"history_entries_read_while_publishing": 0}
        self._publishing = False
        existing_steps = self.available_steps()
        self._latest_step = existing_steps[-1] if existing_steps else None
        if self._latest_step is None:
            self._latest_checkpoint_id = None
            self._latest_state_sha256 = "0" * 64
        else:
            latest = self._read_marker(self._latest_step)
            self._latest_checkpoint_id = latest["checkpoint_id"]
            self._latest_state_sha256 = latest["state_sha256"]

    @staticmethod
    def _canonical_bytes(value: Any) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    @staticmethod
    def _sha256(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> int:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            temporary.replace(path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temporary.unlink(missing_ok=True)
        return len(data)

    def _write_gzip_component(self, directory: Path, payload: Any, name: str) -> dict[str, Any]:
        canonical = self._canonical_bytes(payload)
        compressed = gzip.compress(canonical, compresslevel=6, mtime=0)
        digest = self._sha256(compressed)
        path = directory / name
        written = self._atomic_write(path, compressed)
        return {"path": self._relative(path), "sha256": digest, "bytes": written}

    def publish(self, delta: SealedTickDelta) -> dict[str, Any]:
        if self._publishing:
            raise RuntimeError("only one unfinished checkpoint is allowed")
        if self._latest_step is not None and delta.step <= self._latest_step:
            raise ValueError("checkpoint steps must increase")

        self._publishing = True
        checkpoint_id = uuid.uuid4().hex
        bytes_written = 0
        try:
            parent_manifest = self._latest_checkpoint_id
            parent_state_sha256 = self._latest_state_sha256

            replacement_payload = {
                "checkpoint_id": checkpoint_id,
                "step": delta.step,
                "entries": list(delta.replacements),
            }
            replacement = self._write_gzip_component(
                self.replacements_dir,
                replacement_payload,
                f"{checkpoint_id}.json.gz",
            )
            bytes_written += replacement["bytes"]

            segments = []
            if delta.appends:
                segment_payload = {
                    "checkpoint_id": checkpoint_id,
                    "step": delta.step,
                    "entries": list(delta.appends),
                }
                segment_bytes = gzip.compress(
                    self._canonical_bytes(segment_payload), compresslevel=6, mtime=0
                )
                segment_hash = self._sha256(segment_bytes)
                segment_path = self.segments_dir / f"{segment_hash}.json.gz"
                if not segment_path.exists():
                    bytes_written += self._atomic_write(segment_path, segment_bytes)
                segments.append(
                    {
                        "path": self._relative(segment_path),
                        "sha256": segment_hash,
                        "entry_count": len(delta.appends),
                    }
                )

            state_material = {
                "parent_state_sha256": parent_state_sha256,
                "replacement_sha256": replacement["sha256"],
                "segment_sha256": [item["sha256"] for item in segments],
            }
            state_sha256 = self._sha256(self._canonical_bytes(state_material))
            manifest = {
                "checkpoint_version": self.VERSION,
                "checkpoint_id": checkpoint_id,
                "step": delta.step,
                "parent_checkpoint_id": parent_manifest,
                "replacement_file": replacement["path"],
                "replacement_sha256": replacement["sha256"],
                "new_segments": segments,
                "state_sha256": state_sha256,
                "created_at": time.time(),
            }
            manifest_path = self.manifests_dir / f"{checkpoint_id}.json"
            manifest_bytes = self._canonical_bytes(manifest)
            bytes_written += self._atomic_write(manifest_path, manifest_bytes)
            manifest_sha256 = self._sha256(manifest_bytes)

            marker = {
                "complete": True,
                "recoverable": True,
                "checkpoint_version": self.VERSION,
                "checkpoint_id": checkpoint_id,
                "step": delta.step,
                "manifest_file": self._relative(manifest_path),
                "manifest_sha256": manifest_sha256,
                "state_sha256": state_sha256,
            }
            marker_path = self.complete_dir / f"step_{delta.step:06d}.json"
            marker_bytes = self._canonical_bytes(marker)
            bytes_written += self._atomic_write(marker_path, marker_bytes)
            self._latest_step = delta.step
            self._latest_checkpoint_id = checkpoint_id
            self._latest_state_sha256 = state_sha256
            return {**marker, "bytes_written": bytes_written}
        finally:
            self._publishing = False

    def available_steps(self) -> list[int]:
        result = []
        for path in self.complete_dir.glob("step_*.json"):
            try:
                result.append(int(path.stem.removeprefix("step_")))
            except ValueError:
                continue
        return sorted(result)

    def _read_marker(self, step: int) -> dict[str, Any]:
        path = self.complete_dir / f"step_{step:06d}.json"
        if not path.is_file():
            raise FileNotFoundError(f"complete checkpoint not found for step {step}")
        marker = json.loads(path.read_text(encoding="utf-8"))
        if (
            marker.get("complete") is not True
            or marker.get("recoverable") is not True
            or marker.get("checkpoint_version") != self.VERSION
            or marker.get("step") != step
        ):
            raise ValueError(f"invalid complete marker for step {step}")
        return marker

    def _read_json_component(self, relative: str, expected_sha256: str, component: str) -> Any:
        path = self.root / relative
        if not path.is_file():
            raise FileNotFoundError(f"{component} missing: {relative}")
        raw = path.read_bytes()
        if self._sha256(raw) != expected_sha256:
            raise ValueError(f"{component} content hash mismatch: {relative}")
        if path.suffix == ".gz":
            try:
                raw = gzip.decompress(raw)
            except OSError as exc:
                raise ValueError(f"{component} gzip is invalid: {relative}") from exc
        return json.loads(raw)

    def _manifest_chain(self, step: int) -> list[dict[str, Any]]:
        marker = self._read_marker(step)
        chain = []
        expected_id: str | None = marker["checkpoint_id"]
        expected_state = marker["state_sha256"]
        while expected_id is not None:
            manifest_path = self.manifests_dir / f"{expected_id}.json"
            if not manifest_path.is_file():
                raise FileNotFoundError(f"manifest missing: {expected_id}")
            raw = manifest_path.read_bytes()
            if not chain and self._sha256(raw) != marker["manifest_sha256"]:
                raise ValueError("manifest content hash mismatch")
            manifest = json.loads(raw)
            if (
                manifest.get("checkpoint_id") != expected_id
                or manifest.get("checkpoint_version") != self.VERSION
                or manifest.get("state_sha256") != expected_state
            ):
                raise ValueError("checkpoint manifest chain mismatch")
            chain.append(manifest)
            expected_id = manifest.get("parent_checkpoint_id")
            if expected_id is not None:
                parent_path = self.manifests_dir / f"{expected_id}.json"
                if not parent_path.is_file():
                    raise FileNotFoundError(f"parent manifest missing: {expected_id}")
                expected_state = json.loads(parent_path.read_bytes())["state_sha256"]
        chain.reverse()
        return chain

    @staticmethod
    def _parent(root: dict[str, Any], path: list[str], *, create: bool) -> tuple[dict[str, Any], str]:
        if not path:
            raise ValueError("state operation path must not be empty")
        current = root
        for part in path[:-1]:
            child = current.get(part)
            if child is None and create:
                child = {}
                current[part] = child
            if not isinstance(child, dict):
                raise ValueError(f"state path crosses a non-map value: {path!r}")
            current = child
        return current, path[-1]

    @classmethod
    def _apply(cls, state: dict[str, Any], operation: dict[str, Any]) -> None:
        parent, key = cls._parent(state, operation["path"], create=True)
        kind = operation["operation"]
        if kind == "set":
            parent[key] = copy.deepcopy(operation["value"])
        elif kind == "delete":
            parent.pop(key, None)
        elif kind == "map_create":
            target = parent.setdefault(key, {})
            if not isinstance(target, dict) or operation["id"] in target:
                raise ValueError("duplicate or invalid append-only map entry during restore")
            target[operation["id"]] = copy.deepcopy(operation["value"])
        elif kind == "append":
            target = parent.setdefault(key, [])
            if not isinstance(target, list):
                raise ValueError("invalid append-only list during restore")
            target.append(copy.deepcopy(operation["value"]))
        else:
            raise ValueError(f"unsupported state operation: {kind}")

    def restore(self, step: int) -> dict[str, Any]:
        state: dict[str, Any] = {}
        parent_state = "0" * 64
        for manifest in self._manifest_chain(step):
            replacement = self._read_json_component(
                manifest["replacement_file"],
                manifest["replacement_sha256"],
                "replacement",
            )
            for operation in replacement["entries"]:
                self._apply(state, operation)
            segment_hashes = []
            for segment in manifest["new_segments"]:
                payload = self._read_json_component(
                    segment["path"], segment["sha256"], "segment"
                )
                if len(payload["entries"]) != segment["entry_count"]:
                    raise ValueError("segment entry count mismatch")
                for operation in payload["entries"]:
                    self._apply(state, operation)
                segment_hashes.append(segment["sha256"])
            expected_state = self._sha256(
                self._canonical_bytes(
                    {
                        "parent_state_sha256": parent_state,
                        "replacement_sha256": manifest["replacement_sha256"],
                        "segment_sha256": segment_hashes,
                    }
                )
            )
            if expected_state != manifest["state_sha256"]:
                raise ValueError("state hash chain mismatch")
            parent_state = expected_state
        return state

    def cleanup_orphans(self) -> list[str]:
        """删除没有被任何完整 marker 引用的 v4 组件。"""

        if self._publishing:
            raise RuntimeError("cannot collect orphans while publishing")
        referenced_manifests: set[Path] = set()
        referenced_components: set[Path] = set()
        for step in self.available_steps():
            marker = self._read_marker(step)
            for manifest in self._manifest_chain(step):
                manifest_path = self.manifests_dir / f"{manifest['checkpoint_id']}.json"
                referenced_manifests.add(manifest_path)
                referenced_components.add(self.root / manifest["replacement_file"])
                referenced_components.update(
                    self.root / segment["path"]
                    for segment in manifest["new_segments"]
                )
            referenced_manifests.add(self.root / marker["manifest_file"])

        removed: list[str] = []
        candidates = (
            list(self.manifests_dir.glob("*.json"))
            + list(self.replacements_dir.glob("*.json.gz"))
            + list(self.segments_dir.glob("*.json.gz"))
        )
        reachable = referenced_manifests | referenced_components
        for path in candidates:
            if path not in reachable:
                path.unlink(missing_ok=True)
                removed.append(self._relative(path))
        return sorted(removed)
