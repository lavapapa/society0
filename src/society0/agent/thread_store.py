"""Durable, append-only Agent Thread evidence.

The thread store is deliberately separate from checkpoints and ordinary logs.
Checkpoints only reference immutable, closed thread files by cursor and hash.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
from threading import RLock
from typing import Any, Mapping
import uuid


_THREAD_ID_PATTERN = re.compile(r"^thr_[0-9a-f]{32}$")
_THREAD_SCHEMA_VERSION = 1
# v4 manifests are per-Tick immutable references.  They intentionally do not
# carry Thread bodies or a cumulative list from earlier checkpoints.
_MANIFEST_SCHEMA_VERSION = 2
_REDACTED_CREDENTIAL = "[REDACTED]"
_CREDENTIAL_KEY_NAMES = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credentials",
    "password",
    "proxy_authorization",
    "secret",
    "set_cookie",
    "token",
    "x_api_key",
}
_CREDENTIAL_VALUE_PATTERN = re.compile(
    r"(?ix)"
    r"(\b(?:api[_ -]?key|authorization|cookie|credentials|password|"
    r"proxy[_ -]?authorization|secret|token|x[_ -]?api[_ -]?key)\b"
    r"\s*(?:[:=]\s*|\bis\s+))"
    r"([^\s,;\]}\[]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)(\bBearer\s+)([^\s,;\]}\[]+)")


@dataclass
class _ThreadIndex:
    """Incremental state for one append-only JSONL Thread.

    The index is deliberately an in-process cache.  A process that resumes an
    existing run may pay one validation scan when it first appends to a Thread;
    subsequent appends and checkpoint manifests consume only this tail state.
    Recovery validation still reads the complete file and verifies every event.
    """

    path: Path
    event_count: int
    next_sequence: int
    tail_event_sha256: str | None
    file_digest: Any
    byte_offset: int
    closed: bool
    last_event_type: str | None
    opened_payload: dict[str, Any]


def _credential_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
    return normalized in _CREDENTIAL_KEY_NAMES or normalized.endswith(
        ("_api_key", "_password", "_secret", "_token")
    )


def _redact_text(value: str) -> str:
    """Remove credential values from free-form SDK/tool/error text."""

    redacted = _CREDENTIAL_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)}{_REDACTED_CREDENTIAL}",
        str(value),
    )
    return _BEARER_PATTERN.sub(
        lambda match: f"{match.group(1)}{_REDACTED_CREDENTIAL}",
        redacted,
    )


def _json_value(value: Any) -> Any:
    """Convert an SDK/runtime value without silently dropping exposed fields."""

    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"__society0_type__": "non_finite_float", "text": str(value)}
    if isinstance(value, bytes):
        return {
            "__society0_type__": "bytes",
            "base64": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _REDACTED_CREDENTIAL
            if _credential_key(key)
            else _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if is_dataclass(value):
        return _json_value(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    model_dump_error: BaseException | None = None
    if callable(model_dump):
        try:
            return _json_value(model_dump(mode="json", exclude_none=False))
        except BaseException as exc:
            model_dump_error = exc
            try:
                return _json_value(model_dump())
            except BaseException as fallback_exc:
                model_dump_error = fallback_exc
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            return _json_value(to_dict())
        except BaseException as exc:
            model_dump_error = model_dump_error or exc
    try:
        attributes = vars(value)
    except BaseException:
        attributes = None
    if isinstance(attributes, Mapping):
        return _json_value(attributes)
    text = _redact_text(str(value))
    payload = {
        "__society0_type__": f"{type(value).__module__}.{type(value).__qualname__}",
        "text": text,
    }
    if model_dump_error is not None:
        payload["serialization_error"] = _redact_text(
            f"{type(model_dump_error).__name__}: {model_dump_error}"
        )
    return payload


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class AgentThreadStore:
    """Persist complete Agent Thread events under one simulation run directory."""

    _locks_guard = RLock()
    _run_locks: dict[str, RLock] = {}

    def __init__(
        self,
        run_dir: str | Path,
        *,
        inline_payload_max_bytes: int = 1024 * 1024,
        create: bool = True,
    ) -> None:
        self.run_dir = Path(run_dir).resolve()
        self.root = self.run_dir / "agent_threads"
        self.threads_dir = self.root / "threads"
        self.manifests_dir = self.root / "manifests"
        self.blobs_dir = self.root / "blobs" / "sha256"
        self.inline_payload_max_bytes = int(inline_payload_max_bytes)
        if self.inline_payload_max_bytes < 0:
            raise ValueError("inline_payload_max_bytes must be non-negative")
        if create:
            for directory in (
                self.root,
                self.threads_dir,
                self.manifests_dir,
                self.blobs_dir,
            ):
                directory.mkdir(parents=True, exist_ok=True)
                try:
                    directory.chmod(0o700)
                except OSError:
                    pass
        with self._locks_guard:
            self._lock = self._run_locks.setdefault(str(self.run_dir), RLock())
        self._path_cache: dict[str, Path] = {}
        # ``_thread_indexes`` is the append/checkpoint hot-path index.  It is
        # never treated as recovery evidence: validation deliberately reads
        # and hashes the immutable JSONL/blob components again.
        self._thread_indexes: dict[str, _ThreadIndex] = {}
        self.metrics: dict[str, int] = {
            "jsonl_full_reads": 0,
            "jsonl_full_hashes": 0,
            "jsonl_bytes_read": 0,
            "jsonl_append_bytes": 0,
            "manifest_reference_count": 0,
        }

    @staticmethod
    def _validate_thread_id(thread_id: str) -> str:
        normalized = str(thread_id)
        if _THREAD_ID_PATTERN.fullmatch(normalized) is None:
            raise ValueError("invalid agent thread id")
        return normalized

    def _relative_path(self, path: Path) -> str:
        resolved = path.resolve()
        if self.run_dir not in resolved.parents:
            raise ValueError("agent thread path escapes run directory")
        return resolved.relative_to(self.run_dir).as_posix()

    @staticmethod
    def _normalize_checkpoint_step(checkpoint_step: int | None) -> int | None:
        """Validate a checkpoint step without coercing caller input.

        Thread paths are part of the durable identity.  Accepting values such
        as ``"3"`` or ``3.5`` here would silently place evidence under a
        different checkpoint than the caller requested.
        """

        if checkpoint_step is None:
            return None
        if isinstance(checkpoint_step, bool) or not isinstance(checkpoint_step, int):
            raise ValueError("checkpoint_step must be a non-negative integer or None")
        if checkpoint_step < 0:
            raise ValueError("checkpoint_step must be a non-negative integer or None")
        return checkpoint_step

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        """Durably publish a directory entry after create/rename operations."""

        try:
            descriptor = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _safe_relative_file(self, relative: str, *, component: str) -> Path:
        raw_candidate = self.run_dir / str(relative)
        try:
            relative_parts = raw_candidate.relative_to(self.run_dir).parts
        except ValueError as exc:
            raise ValueError(f"{component} path escapes run directory") from exc
        cursor = self.run_dir
        for part in relative_parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError(f"{component} path must not contain symlinks")
        candidate = raw_candidate.resolve()
        if self.run_dir not in candidate.parents:
            raise ValueError(f"{component} path escapes run directory")
        return candidate

    def _thread_path_for_new(
        self,
        thread_id: str,
        checkpoint_step: int | None,
    ) -> Path:
        checkpoint_step = self._normalize_checkpoint_step(checkpoint_step)
        if checkpoint_step is None:
            parent = self.threads_dir / "unbound"
        else:
            parent = self.threads_dir / f"step_{checkpoint_step:06d}"
        parent.mkdir(parents=True, exist_ok=True)
        try:
            parent.chmod(0o700)
        except OSError:
            pass
        return parent / f"{thread_id}.jsonl"

    def _resolve_thread_path(self, thread_id: str) -> Path:
        normalized = self._validate_thread_id(thread_id)
        cached = self._path_cache.get(normalized)
        if cached is not None and cached.is_file() and not cached.is_symlink():
            return cached
        candidates = list(self.threads_dir.glob(f"*/{normalized}.jsonl"))
        if len(candidates) != 1:
            if not candidates:
                raise FileNotFoundError(f"agent thread not found: {normalized}")
            raise ValueError(f"duplicate agent thread id: {normalized}")
        path = candidates[0]
        if path.is_symlink() or not path.is_file():
            raise ValueError("agent thread must be a regular file")
        self._path_cache[normalized] = path
        return path

    def reset_metrics(self) -> None:
        """Reset observable Thread persistence counters for a benchmark."""

        with self._lock:
            for key in self.metrics:
                self.metrics[key] = 0

    def _index_from_events(
        self,
        thread_id: str,
        path: Path,
        raw: bytes,
        events: list[dict[str, Any]],
    ) -> _ThreadIndex:
        digest = hashlib.sha256()
        digest.update(raw)
        self.metrics["jsonl_full_hashes"] += 1
        opened_payload: dict[str, Any] = {}
        if events:
            opened = self._materialize_payload(events[0])
            if isinstance(opened, Mapping):
                opened_payload = dict(opened)
        tail_event_sha256 = (
            str(events[-1].get("event_sha256")) if events else None
        )
        index = _ThreadIndex(
            path=path,
            event_count=len(events),
            next_sequence=len(events) + 1,
            tail_event_sha256=tail_event_sha256,
            file_digest=digest,
            byte_offset=len(raw),
            closed=bool(events and events[-1].get("event_type") == "thread_closed"),
            last_event_type=(
                str(events[-1].get("event_type")) if events else None
            ),
            opened_payload=opened_payload,
        )
        self._thread_indexes[thread_id] = index
        return index

    def _load_thread_index(self, thread_id: str, path: Path) -> _ThreadIndex:
        """Load or refresh the incremental append index.

        A size change invalidates the cache and causes one full JSONL scan.  A
        normal append through this class updates the digest/cursor directly and
        therefore does not re-read historical records.
        """

        cached = self._thread_indexes.get(thread_id)
        try:
            current_size = path.stat().st_size
        except OSError:
            current_size = -1
        if cached is not None and cached.path == path and cached.byte_offset == current_size:
            return cached
        raw = self._read_thread_bytes_with_tail_recovery(path)
        self.metrics["jsonl_full_reads"] += 1
        self.metrics["jsonl_bytes_read"] += len(raw)
        events = self._read_events_path(
            path,
            materialize_payloads=False,
            expected_thread_id=thread_id,
            raw=raw,
        )
        # ``_read_events_path`` performs validation but does not hash the whole
        # file.  The incremental digest below is the one full hash needed to
        # resume an append stream after process restart.
        return self._index_from_events(thread_id, path, raw, events)

    def _reference_from_index(
        self,
        thread_id: str,
        index: _ThreadIndex,
        *,
        require_closed: bool,
    ) -> dict[str, Any]:
        if index.event_count == 0:
            raise ValueError("agent thread is empty")
        if require_closed and not index.closed:
            raise ValueError("agent thread is not closed")
        opened = index.opened_payload
        return {
            "thread_id": str(thread_id),
            "agent_id": str(opened.get("agent_id") or ""),
            "checkpoint_step": opened.get("checkpoint_step"),
            "scope": _json_value(opened.get("scope") or {}),
            "path": self._relative_path(index.path),
            "cursor": {
                "sequence": int(index.event_count),
                "byte_offset": int(index.byte_offset),
            },
            "tail_event_sha256": str(index.tail_event_sha256),
            "file_sha256": index.file_digest.copy().hexdigest(),
            "closed": bool(index.closed),
        }

    def _write_blob(self, payload_bytes: bytes) -> dict[str, Any]:
        digest = _sha256_bytes(payload_bytes)
        parent = self.blobs_dir / digest[:2]
        parent.mkdir(parents=True, exist_ok=True)
        try:
            parent.chmod(0o700)
        except OSError:
            pass
        path = parent / f"{digest}.json"
        if not path.exists():
            temp = parent / f".{digest}.{uuid.uuid4().hex}.tmp"
            try:
                with temp.open("xb") as handle:
                    handle.write(payload_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    temp.chmod(0o600)
                except OSError:
                    pass
                try:
                    temp.replace(path)
                    self._fsync_directory(parent)
                except FileExistsError:
                    pass
            finally:
                temp.unlink(missing_ok=True)
        if _sha256_file(path) != digest:
            raise ValueError("agent thread blob content hash mismatch")
        return {
            "path": self._relative_path(path),
            "sha256": digest,
            "bytes": len(payload_bytes),
            "media_type": "application/json",
        }

    def open_thread(
        self,
        *,
        agent_id: str,
        checkpoint_step: int | None,
        scope: Mapping[str, Any],
        thread_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        normalized_agent_id = str(agent_id).strip()
        if not normalized_agent_id:
            raise ValueError("agent_id must be non-empty")
        if not isinstance(scope, Mapping) or not scope:
            raise ValueError("scope must be a non-empty mapping")
        normalized_thread_id = (
            self._validate_thread_id(thread_id)
            if thread_id is not None
            else f"thr_{uuid.uuid4().hex}"
        )
        with self._lock:
            existing = list(self.threads_dir.glob(f"*/{normalized_thread_id}.jsonl"))
            if existing:
                raise ValueError(f"agent thread already exists: {normalized_thread_id}")
            path = self._thread_path_for_new(normalized_thread_id, checkpoint_step)
            if path.exists():
                raise ValueError(f"agent thread already exists: {normalized_thread_id}")
            path.touch(mode=0o600, exist_ok=False)
            self._fsync_directory(path.parent)
            self._path_cache[normalized_thread_id] = path
            self._thread_indexes[normalized_thread_id] = _ThreadIndex(
                path=path,
                event_count=0,
                next_sequence=1,
                tail_event_sha256=None,
                file_digest=hashlib.sha256(),
                byte_offset=0,
                closed=False,
                last_event_type=None,
                opened_payload={},
            )
            self._append_event_locked(
                normalized_thread_id,
                "thread_opened",
                payload={
                    "agent_id": normalized_agent_id,
                    "checkpoint_step": checkpoint_step,
                    "scope": dict(scope),
                    "metadata": dict(metadata or {}),
                },
            )
        return normalized_thread_id

    def append_event(
        self,
        thread_id: str,
        event_type: str,
        *,
        payload: Any = None,
        interaction_id: str | None = None,
        interaction_type: str | None = None,
        interaction_name: str | None = None,
        turn_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._append_event_locked(
                thread_id,
                event_type,
                payload=payload,
                interaction_id=interaction_id,
                interaction_type=interaction_type,
                interaction_name=interaction_name,
                turn_id=turn_id,
                metadata=metadata,
            )

    def _append_event_locked(
        self,
        thread_id: str,
        event_type: str,
        *,
        payload: Any = None,
        interaction_id: str | None = None,
        interaction_type: str | None = None,
        interaction_name: str | None = None,
        turn_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_thread_id = self._validate_thread_id(thread_id)
        normalized_event_type = str(event_type).strip()
        if not normalized_event_type:
            raise ValueError("event_type must be non-empty")
        path = self._resolve_thread_path(normalized_thread_id)
        index = self._load_thread_index(normalized_thread_id, path)
        if index.closed:
            raise RuntimeError("cannot append to a closed agent thread")
        opened_payload = index.opened_payload
        had_events = index.event_count > 0
        if not had_events and normalized_event_type != "thread_opened":
            raise ValueError("agent thread must start with thread_opened")
        payload_value = _json_value(payload)
        if not had_events and normalized_event_type == "thread_opened" and isinstance(payload_value, Mapping):
            opened_payload = payload_value
        payload_bytes = _canonical_bytes(payload_value)
        payload_sha256 = _sha256_bytes(payload_bytes)
        if len(payload_bytes) > self.inline_payload_max_bytes:
            inline_payload = None
            payload_ref = self._write_blob(payload_bytes)
        else:
            inline_payload = payload_value
            payload_ref = None
        event: dict[str, Any] = {
            "schema_version": _THREAD_SCHEMA_VERSION,
            "event_id": f"evt_{uuid.uuid4().hex}",
            "thread_id": normalized_thread_id,
            "sequence": index.next_sequence,
            "run_id": (opened_payload.get("metadata") or {}).get("run_id"),
            "experiment_id": (opened_payload.get("metadata") or {}).get(
                "experiment_id"
            ),
            "agent_id": opened_payload.get("agent_id"),
            "checkpoint_step": opened_payload.get("checkpoint_step"),
            "scope": opened_payload.get("scope") or {},
            "event_type": normalized_event_type,
            "recorded_at": datetime.now().astimezone().isoformat(),
            "payload": inline_payload,
            "payload_ref": payload_ref,
            "payload_sha256": payload_sha256,
            "previous_event_sha256": index.tail_event_sha256,
        }
        optional = {
            "interaction_id": interaction_id,
            "interaction_type": interaction_type,
            "interaction_name": interaction_name,
            "turn_id": turn_id,
            "metadata": dict(metadata or {}) or None,
        }
        event.update({key: _json_value(value) for key, value in optional.items() if value is not None})
        event["event_sha256"] = _sha256_bytes(_canonical_bytes(event))
        serialized = json.dumps(
            event,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        # Existing interrupted writers may leave a complete JSON object
        # without its final newline.  Account for the one-byte repair in the
        # incremental digest before appending the new immutable record.
        before_size = path.stat().st_size
        self._ensure_final_newline(path)
        after_size = path.stat().st_size
        if after_size > before_size:
            index.file_digest.update(b"\n")
            index.byte_offset += after_size - before_size
            self.metrics["jsonl_append_bytes"] += after_size - before_size
        serialized_bytes = (serialized + "\n").encode("utf-8")
        with path.open("ab") as handle:
            handle.write(serialized_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        self._fsync_directory(path.parent)
        index.file_digest.update(serialized_bytes)
        index.byte_offset += len(serialized_bytes)
        index.event_count += 1
        index.next_sequence += 1
        index.tail_event_sha256 = str(event["event_sha256"])
        index.last_event_type = normalized_event_type
        index.closed = normalized_event_type == "thread_closed"
        if not had_events and isinstance(opened_payload, Mapping):
            index.opened_payload = dict(opened_payload)
        self.metrics["jsonl_append_bytes"] += len(serialized_bytes)
        return event

    def _materialize_payload(self, event: Mapping[str, Any]) -> Any:
        inline = event.get("payload")
        reference = event.get("payload_ref")
        if reference is None:
            payload = inline
            payload_bytes = _canonical_bytes(payload)
        else:
            if inline is not None or not isinstance(reference, Mapping):
                raise ValueError("invalid agent thread payload storage")
            path = self._safe_relative_file(
                str(reference.get("path") or ""),
                component="agent thread blob",
            )
            if not path.is_file():
                raise FileNotFoundError("agent thread blob missing")
            payload_bytes = path.read_bytes()
            expected = str(reference.get("sha256") or "")
            if _sha256_bytes(payload_bytes) != expected:
                raise ValueError("agent thread blob content hash mismatch")
            if reference.get("bytes") != len(payload_bytes):
                raise ValueError("agent thread blob byte count mismatch")
            payload = json.loads(payload_bytes.decode("utf-8"))
        if _sha256_bytes(payload_bytes) != event.get("payload_sha256"):
            raise ValueError("agent thread payload hash mismatch")
        return payload

    def _read_thread_bytes_with_tail_recovery(self, path: Path) -> bytes:
        """Read a JSONL thread, dropping only an incomplete final line.

        A process can be interrupted after writing part of the final JSONL
        record.  That byte suffix has no trustworthy sequence/hash identity,
        so it is safe to discard up to the previous newline.  Any complete
        JSON object (including one with a bad hash) is retained and validated
        by :meth:`_read_events_path`; corruption is never silently repaired.
        """

        raw = path.read_bytes()
        if not raw or raw.endswith(b"\n"):
            return raw

        separator = raw.rfind(b"\n")
        tail = raw[separator + 1 :]
        try:
            tail.decode("utf-8")
            json.loads(tail)
        except (UnicodeDecodeError, json.JSONDecodeError):
            keep_size = separator + 1
            with path.open("r+b") as handle:
                handle.truncate(keep_size)
                handle.flush()
                os.fsync(handle.fileno())
            self._fsync_directory(path.parent)
            return raw[:keep_size]
        return raw

    @staticmethod
    def _ensure_final_newline(path: Path) -> None:
        """Make an otherwise valid final JSON record append-safe."""

        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                if size == 0:
                    return
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) == b"\n":
                    return
        except OSError:
            raise
        with path.open("ab") as handle:
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _read_events_path(
        self,
        path: Path,
        *,
        materialize_payloads: bool,
        expected_thread_id: str | None = None,
        raw: bytes | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        previous_hash: str | None = None
        normalized_expected_thread_id = self._validate_thread_id(
            expected_thread_id or path.stem
        )
        if raw is None:
            raw = self._read_thread_bytes_with_tail_recovery(path)
            self.metrics["jsonl_full_reads"] += 1
            self.metrics["jsonl_bytes_read"] += len(raw)
        for line_number, raw_line in enumerate(raw.splitlines(), start=1):
            if not raw_line.strip():
                continue
            try:
                line = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError(
                    f"agent thread invalid UTF-8 at line {line_number}"
                ) from exc
            event = json.loads(line)
            if event.get("schema_version") != _THREAD_SCHEMA_VERSION:
                raise ValueError("unsupported agent thread schema")
            if event.get("thread_id") != normalized_expected_thread_id:
                raise ValueError("agent thread embedded thread id mismatch")
            if (
                not isinstance(event.get("sequence"), int)
                or isinstance(event.get("sequence"), bool)
                or event.get("sequence") != len(events) + 1
            ):
                raise ValueError("agent thread sequence mismatch")
            if event.get("previous_event_sha256") != previous_hash:
                raise ValueError("agent thread hash chain mismatch")
            if events and events[-1].get("event_type") == "thread_closed":
                raise ValueError("agent thread has events after close")
            expected_event_hash = event.get("event_sha256")
            unsigned = dict(event)
            unsigned.pop("event_sha256", None)
            if _sha256_bytes(_canonical_bytes(unsigned)) != expected_event_hash:
                raise ValueError(
                    f"agent thread event hash mismatch at line {line_number}"
                )
            self._materialize_payload(event)
            checkpoint_step = event.get("checkpoint_step")
            self._normalize_checkpoint_step(checkpoint_step)
            previous_hash = str(expected_event_hash)
            if materialize_payloads:
                event["payload"] = self._materialize_payload(event)
                event["payload_ref"] = None
            events.append(event)
        if events and events[0].get("event_type") != "thread_opened":
            raise ValueError("agent thread does not start with thread_opened")
        return events

    def read_events(
        self,
        thread_id: str,
        *,
        materialize_payloads: bool = True,
    ) -> list[dict[str, Any]]:
        with self._lock:
            path = self._resolve_thread_path(thread_id)
            return self._read_events_path(
                path,
                materialize_payloads=materialize_payloads,
                expected_thread_id=thread_id,
            )

    def read_messages(self, thread_id: str) -> list[dict[str, Any]]:
        """Rebuild the provider-compatible conversation at the thread tail."""

        events = self.read_events(thread_id, materialize_payloads=True)
        last_request_index: int | None = None
        messages: list[dict[str, Any]] = []
        for index, event in enumerate(events):
            if event.get("event_type") != "provider_request":
                continue
            payload = event.get("payload") or {}
            request = payload.get("request") if isinstance(payload, Mapping) else None
            request_messages = (
                request.get("messages") if isinstance(request, Mapping) else None
            )
            if isinstance(request_messages, list):
                messages = _json_value(request_messages)
                last_request_index = index
        start = (last_request_index + 1) if last_request_index is not None else 0
        for event in events[start:]:
            payload = event.get("payload")
            if event.get("event_type") == "provider_response" and isinstance(
                payload, Mapping
            ):
                message = payload.get("message")
                if isinstance(message, Mapping):
                    messages.append(_json_value(message))
            elif event.get("event_type") == "conversation_message" and isinstance(
                payload, Mapping
            ):
                message = payload.get("message", payload)
                if isinstance(message, Mapping):
                    messages.append(_json_value(message))
        return messages

    def close_thread(
        self,
        thread_id: str,
        *,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            path = self._resolve_thread_path(thread_id)
            index = self._load_thread_index(str(thread_id), path)
            if index.closed:
                return self.get_thread_reference(thread_id, require_closed=True)
            self._append_event_locked(
                thread_id,
                "thread_closed",
                payload={"metadata": dict(metadata or {})},
            )
            return self.get_thread_reference(thread_id, require_closed=True)

    def get_thread_reference(
        self,
        thread_id: str,
        *,
        require_closed: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            path = self._resolve_thread_path(thread_id)
            index = self._load_thread_index(str(thread_id), path)
            return self._reference_from_index(
                str(thread_id),
                index,
                require_closed=require_closed,
            )

    def snapshot_thread_references(self) -> dict[str, Any]:
        """Return point-in-time references, including still-open threads.

        Diagnostic checkpoints are intentionally non-recoverable and may be
        emitted while an activation is still running.  They therefore retain
        cursors for open files instead of requiring the closed-thread manifest
        used by recoverable checkpoints.
        """

        with self._lock:
            references: dict[str, dict[str, Any]] = {}
            by_agent: dict[str, list[str]] = {}
            for path in sorted(self.threads_dir.glob("*/thr_*.jsonl")):
                if path.is_symlink() or not path.is_file():
                    if path.is_symlink():
                        raise ValueError("agent thread must not be a symlink")
                    continue
                thread_id = path.stem
                reference = self.get_thread_reference(thread_id, require_closed=False)
                references[thread_id] = reference
                by_agent.setdefault(reference["agent_id"], []).append(thread_id)
            return {
                "schema_version": _MANIFEST_SCHEMA_VERSION,
                "thread_count": len(references),
                "threads": references,
                "by_agent": {
                    agent_id: sorted(thread_ids)
                    for agent_id, thread_ids in sorted(by_agent.items())
                },
            }

    @staticmethod
    def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("x", encoding="utf-8") as handle:
                json.dump(
                    payload,
                    handle,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                temp.chmod(0o600)
            except OSError:
                pass
            temp.replace(path)
            AgentThreadStore._fsync_directory(path.parent)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _normalize_checkpoint_id(checkpoint_id: str) -> str:
        normalized = str(checkpoint_id).strip()
        if not normalized or Path(normalized).name != normalized:
            raise ValueError("invalid checkpoint_id for agent thread manifest")
        return normalized

    @staticmethod
    def _manifest_bytes(payload: Mapping[str, Any]) -> bytes:
        return (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            + b"\n"
        )

    def _write_immutable_manifest(
        self,
        path: Path,
        manifest: Mapping[str, Any],
    ) -> tuple[str, int]:
        """Write a manifest once; never mutate one referenced by a marker."""

        raw = self._manifest_bytes(manifest)
        digest = _sha256_bytes(raw)
        if path.is_symlink():
            raise ValueError("agent thread manifest must not be a symlink")
        if path.exists():
            existing = path.read_bytes()
            if existing != raw:
                raise ValueError("agent thread manifest is already published")
            return digest, len(raw)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                temp.chmod(0o600)
            except OSError:
                pass
            temp.replace(path)
            self._fsync_directory(path.parent)
        finally:
            temp.unlink(missing_ok=True)
        return digest, len(raw)

    def _manifest_descriptor(
        self,
        path: Path,
        manifest: Mapping[str, Any],
        digest: str,
    ) -> dict[str, Any]:
        references = dict(manifest.get("threads") or {})
        by_agent = dict(manifest.get("by_agent") or {})
        refs = [references[thread_id] for thread_id in sorted(references)]
        descriptor = {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "checkpoint_id": manifest["checkpoint_id"],
            "step": manifest["step"],
            "steps": list(manifest.get("steps") or [manifest["step"]]),
            "relative_path": self._relative_path(path),
            # ``path`` remains an explicit alias for callers that consume the
            # pre-v4 descriptor shape; both values are identical and immutable.
            "path": self._relative_path(path),
            "sha256": digest,
            "refs": refs,
            "count": len(refs),
            "thread_count": len(refs),
            "threads": references,
            "by_agent": by_agent,
        }
        if manifest.get("forked_from") is not None:
            descriptor["forked_from"] = _json_value(manifest["forked_from"])
        return descriptor

    @staticmethod
    def _normalize_epoch_steps(steps: Any) -> list[int]:
        if isinstance(steps, (str, bytes, Mapping)):
            raise ValueError("epoch steps must be a non-empty iterable of integers")
        try:
            values = list(steps)
        except TypeError as exc:
            raise ValueError("epoch steps must be a non-empty iterable of integers") from exc
        if not values:
            raise ValueError("epoch steps must be a non-empty iterable of integers")
        normalized: set[int] = set()
        for step in values:
            if isinstance(step, bool) or not isinstance(step, int) or step < 0:
                raise ValueError("epoch steps must contain non-negative integers")
            normalized.add(step)
        return sorted(normalized)

    def _collect_epoch_manifest(
        self,
        *,
        checkpoint_id: str,
        steps: Any,
        forked_from: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_checkpoint_id = self._normalize_checkpoint_id(checkpoint_id)
        normalized_steps = self._normalize_epoch_steps(steps)
        target_step = normalized_steps[-1]
        references: dict[str, dict[str, Any]] = {}
        by_agent: dict[str, list[str]] = {}
        # Preflight every Thread before creating a manifest.  An open Thread
        # is diagnostic-only and must never make the whole epoch recoverable.
        for step in normalized_steps:
            step_dir = self.threads_dir / f"step_{step:06d}"
            if not step_dir.is_dir():
                continue
            for path in sorted(step_dir.glob("thr_*.jsonl")):
                if path.is_symlink():
                    raise ValueError("agent thread must not be a symlink")
                if not path.is_file():
                    continue
                thread_id = self._validate_thread_id(path.stem)
                reference = self.get_thread_reference(thread_id, require_closed=False)
                if reference.get("checkpoint_step") != step:
                    raise ValueError("agent thread checkpoint step mismatch")
                if reference.get("closed") is not True:
                    raise ValueError(
                        "open agent thread cannot enter a recoverable checkpoint"
                    )
                references[thread_id] = reference
                by_agent.setdefault(reference["agent_id"], []).append(thread_id)
        manifest: dict[str, Any] = {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "checkpoint_id": normalized_checkpoint_id,
            "step": target_step,
            "steps": normalized_steps,
            "threads": references,
            "by_agent": {
                agent_id: sorted(thread_ids)
                for agent_id, thread_ids in sorted(by_agent.items())
            },
        }
        if forked_from is not None:
            manifest["forked_from"] = _json_value(forked_from)
        path = self.manifests_dir / f"{normalized_checkpoint_id}.json"
        digest, _ = self._write_immutable_manifest(path, manifest)
        self.metrics["manifest_reference_count"] += len(references)
        return self._manifest_descriptor(path, manifest, digest)

    def publish_epoch_manifest(
        self,
        checkpoint_id: str,
        steps: Any,
    ) -> dict[str, Any]:
        """Publish one immutable Thread manifest for a checkpoint epoch.

        ``steps`` contains the successfully sealed Tick steps since the prior
        complete marker.  The manifest target ``step`` is the final step, but
        each reference retains its own ``checkpoint_step`` so recovery can
        audit the epoch without copying Thread bodies.
        """

        with self._lock:
            return self._collect_epoch_manifest(
                checkpoint_id=checkpoint_id,
                steps=steps,
            )

    def publish_tick_manifest(
        self,
        checkpoint_id: str,
        step: int,
    ) -> dict[str, Any]:
        """Publish only the newly closed Thread references for one Tick.

        Thread bodies remain in their immutable JSONL/blob files.  The
        descriptor is suitable for embedding in a v4 checkpoint manifest and
        contains only relative paths, cursors and hashes.
        """

        return self.publish_epoch_manifest(
            checkpoint_id=checkpoint_id,
            steps=[step],
        )

    def publish_checkpoint_manifest(
        self,
        *,
        checkpoint_id: str,
        step: int,
    ) -> dict[str, Any]:
        """Deprecated spelling retained for internal callers during cutover."""

        return self.publish_tick_manifest(checkpoint_id=checkpoint_id, step=step)

    def fork_tick_manifest(
        self,
        source: Mapping[str, Any],
        *,
        checkpoint_id: str,
        step: int,
        branch_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a branch manifest that references source Thread files.

        The source manifest is fully validated first.  No Thread JSONL/blob is
        copied; the new manifest reuses the exact immutable references.
        """

        with self._lock:
            source_manifest = self._validate_tick_manifest_locked(source)
            source_step = source_manifest["step"]
            source_id = source_manifest["checkpoint_id"]
            forked_from = {
                "checkpoint_id": source_id,
                "step": source_step,
                "steps": list(source_manifest.get("steps") or [source_step]),
                "manifest_sha256": str(source.get("sha256") or ""),
            }
            if branch_id is not None:
                forked_from["branch_id"] = str(branch_id)
            normalized_checkpoint_id = self._normalize_checkpoint_id(checkpoint_id)
            normalized_step = self._normalize_checkpoint_step(step)
            if normalized_step is None:
                raise ValueError("checkpoint step must be a non-negative integer")
            # A fork has no newly closed Thread references at its root; it
            # carries the source list as immutable lineage metadata.  Keeping
            # the refs in the manifest lets recovery validate the same files
            # without copying them into a branch directory.
            manifest = {
                "schema_version": _MANIFEST_SCHEMA_VERSION,
                "checkpoint_id": normalized_checkpoint_id,
                "step": normalized_step,
                "steps": [normalized_step],
                "threads": dict(source_manifest.get("threads") or {}),
                "by_agent": dict(source_manifest.get("by_agent") or {}),
                "forked_from": forked_from,
            }
            path = self.manifests_dir / f"{normalized_checkpoint_id}.json"
            digest, _ = self._write_immutable_manifest(path, manifest)
            self.metrics["manifest_reference_count"] += len(manifest["threads"])
            return self._manifest_descriptor(path, manifest, digest)

    def _validate_tick_manifest_locked(
        self,
        descriptor: Mapping[str, Any],
        *,
        expected_checkpoint_id: str | None = None,
        expected_step: int | None = None,
    ) -> dict[str, Any]:
        if not isinstance(descriptor, Mapping):
            raise TypeError("agent thread manifest descriptor must be a mapping")
        relative_path = descriptor.get("relative_path") or descriptor.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("agent thread manifest path is missing")
        expected_sha256 = descriptor.get("sha256")
        if not isinstance(expected_sha256, str) or not expected_sha256:
            raise ValueError("agent thread manifest hash is missing")
        path = self._safe_relative_file(relative_path, component="agent thread manifest")
        if not path.is_file():
            raise FileNotFoundError("agent thread manifest missing")
        raw_manifest = path.read_bytes()
        if _sha256_bytes(raw_manifest) != expected_sha256:
            raise ValueError("agent thread manifest content hash mismatch")
        try:
            manifest = json.loads(raw_manifest.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("agent thread manifest JSON is invalid") from exc
        descriptor_path = descriptor.get("path")
        if descriptor_path is not None and descriptor_path != relative_path:
            raise ValueError("agent thread manifest path aliases disagree")
        descriptor_id = descriptor.get("checkpoint_id")
        descriptor_step = descriptor.get("step")
        descriptor_steps = descriptor.get("steps")
        if descriptor_id is not None and descriptor_id != manifest.get("checkpoint_id"):
            raise ValueError("agent thread manifest descriptor identity mismatch")
        if descriptor_step is not None and descriptor_step != manifest.get("step"):
            raise ValueError("agent thread manifest descriptor step mismatch")
        manifest_steps = manifest.get("steps")
        if manifest_steps is None:
            manifest_steps = [manifest.get("step")]
        if (
            not isinstance(manifest_steps, list)
            or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0
                for item in manifest_steps
            )
            or sorted(set(manifest_steps)) != manifest_steps
            or not manifest_steps
            or manifest_steps[-1] != manifest.get("step")
        ):
            raise ValueError("agent thread manifest epoch steps are invalid")
        if descriptor_steps is not None and descriptor_steps != manifest_steps:
            raise ValueError("agent thread manifest descriptor steps disagree")
        normalized_step = self._normalize_checkpoint_step(
            expected_step if expected_step is not None else manifest.get("step")
        )
        if normalized_step is None:
            raise ValueError("checkpoint step must be a non-negative integer")
        normalized_checkpoint_id = self._normalize_checkpoint_id(
            expected_checkpoint_id
            if expected_checkpoint_id is not None
            else str(manifest.get("checkpoint_id") or "")
        )
        if (
            manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION
            or manifest.get("checkpoint_id") != normalized_checkpoint_id
            or not isinstance(manifest.get("step"), int)
            or isinstance(manifest.get("step"), bool)
            or manifest.get("step") != normalized_step
        ):
            raise ValueError("agent thread manifest identity mismatch")
        references = manifest.get("threads")
        by_agent = manifest.get("by_agent")
        if not isinstance(references, dict) or not isinstance(by_agent, dict):
            raise ValueError("agent thread manifest schema is invalid")
        descriptor_threads = descriptor.get("threads")
        if descriptor_threads is not None and descriptor_threads != references:
            raise ValueError("agent thread manifest descriptor references disagree")
        descriptor_by_agent = descriptor.get("by_agent")
        if descriptor_by_agent is not None and descriptor_by_agent != by_agent:
            raise ValueError("agent thread manifest descriptor actor index disagrees")
        descriptor_refs = descriptor.get("refs")
        if descriptor_refs is not None:
            if not isinstance(descriptor_refs, list):
                raise ValueError("agent thread manifest descriptor refs are invalid")
            expected_refs = [references[thread_id] for thread_id in sorted(references)]
            if descriptor_refs != expected_refs:
                raise ValueError("agent thread manifest descriptor refs disagree")
        reconstructed: dict[str, list[str]] = {}
        for thread_id, expected in references.items():
            normalized_thread_id = self._validate_thread_id(thread_id)
            if not isinstance(expected, dict):
                raise ValueError("agent thread reference is invalid")
            path_ref = self._safe_relative_file(
                str(expected.get("path") or ""),
                component="agent thread",
            )
            if not path_ref.is_file():
                raise FileNotFoundError("referenced agent thread missing")
            raw_thread = path_ref.read_bytes()
            self.metrics["jsonl_full_hashes"] += 1
            self.metrics["jsonl_bytes_read"] += len(raw_thread)
            if _sha256_bytes(raw_thread) != expected.get("file_sha256"):
                raise ValueError("agent thread content hash mismatch")
            # Recovery is stricter than the append path: a complete immutable
            # Thread must contain every event, payload/blob and hash-chain link.
            if not raw_thread.endswith(b"\n"):
                raise ValueError("closed agent thread is missing final newline")
            events = self._read_events_path(
                path_ref,
                materialize_payloads=False,
                expected_thread_id=normalized_thread_id,
                raw=raw_thread,
            )
            if not events or events[-1].get("event_type") != "thread_closed":
                raise ValueError("recoverable manifest references an open agent thread")
            actual = self._reference_from_raw_events(
                normalized_thread_id,
                path_ref,
                raw_thread,
                events,
            )
            if actual != expected:
                raise ValueError("agent thread reference mismatch")
            if actual.get("checkpoint_step") not in manifest_steps:
                # A fork may carry an earlier Thread set; its lineage metadata
                # is validated against the source step instead of the fork's
                # publication step.
                if not isinstance(manifest.get("forked_from"), Mapping):
                    raise ValueError("agent thread checkpoint step mismatch")
            reconstructed.setdefault(actual["agent_id"], []).append(normalized_thread_id)
        normalized_reconstructed = {
            key: sorted(value) for key, value in sorted(reconstructed.items())
        }
        if normalized_reconstructed != by_agent:
            raise ValueError("agent thread manifest actor index mismatch")
        if descriptor.get("count") is not None and descriptor.get("count") != len(references):
            raise ValueError("agent thread manifest count mismatch")
        if descriptor.get("thread_count") is not None and descriptor.get("thread_count") != len(references):
            raise ValueError("agent thread manifest thread count mismatch")
        return manifest

    def _reference_from_raw_events(
        self,
        thread_id: str,
        path: Path,
        raw: bytes,
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        opened = self._materialize_payload(events[0])
        if not isinstance(opened, Mapping):
            raise ValueError("agent thread opened payload is invalid")
        return {
            "thread_id": str(thread_id),
            "agent_id": str(opened.get("agent_id") or ""),
            "checkpoint_step": opened.get("checkpoint_step"),
            "scope": _json_value(opened.get("scope") or {}),
            "path": self._relative_path(path),
            "cursor": {
                "sequence": int(events[-1]["sequence"]),
                "byte_offset": len(raw),
            },
            "tail_event_sha256": str(events[-1]["event_sha256"]),
            "file_sha256": _sha256_bytes(raw),
            "closed": True,
        }

    def validate_tick_manifest(
        self,
        descriptor: Mapping[str, Any],
        *,
        expected_checkpoint_id: str | None = None,
        expected_step: int | None = None,
        checkpoint_id: str | None = None,
        step: int | None = None,
    ) -> dict[str, Any]:
        """Validate a published Tick manifest and every immutable Thread/blob."""

        if checkpoint_id is not None:
            if expected_checkpoint_id is not None and expected_checkpoint_id != checkpoint_id:
                raise ValueError("conflicting expected checkpoint IDs")
            expected_checkpoint_id = checkpoint_id
        if step is not None:
            if expected_step is not None and expected_step != step:
                raise ValueError("conflicting expected checkpoint steps")
            expected_step = step
        with self._lock:
            return self._validate_tick_manifest_locked(
                descriptor,
                expected_checkpoint_id=expected_checkpoint_id,
                expected_step=expected_step,
            )

    @classmethod
    def validate_tick_manifest_from(
        cls,
        run_dir: str | Path,
        descriptor: Mapping[str, Any],
        *,
        expected_checkpoint_id: str | None = None,
        expected_step: int | None = None,
        checkpoint_id: str | None = None,
        step: int | None = None,
    ) -> dict[str, Any]:
        store = cls(run_dir, create=False)
        return store.validate_tick_manifest(
            descriptor,
            expected_checkpoint_id=expected_checkpoint_id,
            expected_step=expected_step,
            checkpoint_id=checkpoint_id,
            step=step,
        )

    @classmethod
    def validate_checkpoint_manifest(
        cls,
        run_dir: str | Path,
        relative_path: str,
        *,
        expected_sha256: str,
        checkpoint_id: str,
        step: int,
    ) -> dict[str, Any]:
        """Validate the pre-v4 call shape through the strict Tick validator."""

        descriptor = {
            "relative_path": relative_path,
            "sha256": expected_sha256,
            "checkpoint_id": checkpoint_id,
            "step": step,
        }
        return cls.validate_tick_manifest_from(
            run_dir,
            descriptor,
            expected_checkpoint_id=checkpoint_id,
            expected_step=step,
        )


__all__ = ["AgentThreadStore"]
