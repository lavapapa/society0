"""Durable, append-only Agent Thread evidence.

The thread store is deliberately separate from checkpoints and ordinary logs.
Checkpoints only reference immutable, closed thread files by cursor and hash.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, is_dataclass
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
_MANIFEST_SCHEMA_VERSION = 1
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
        events = self._read_events_path(path, materialize_payloads=False)
        if events and events[-1].get("event_type") == "thread_closed":
            raise RuntimeError("cannot append to a closed agent thread")
        opened_payload = self._materialize_payload(events[0]) if events else {}
        payload_value = _json_value(payload)
        if (
            not events
            and normalized_event_type == "thread_opened"
            and isinstance(payload_value, Mapping)
        ):
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
            "sequence": len(events) + 1,
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
            "previous_event_sha256": (
                events[-1].get("event_sha256") if events else None
            ),
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
        self._ensure_final_newline(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(serialized + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._fsync_directory(path.parent)
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
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        previous_hash: str | None = None
        normalized_expected_thread_id = self._validate_thread_id(
            expected_thread_id or path.stem
        )
        raw = self._read_thread_bytes_with_tail_recovery(path)
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
            events = self.read_events(thread_id, materialize_payloads=False)
            if events and events[-1].get("event_type") == "thread_closed":
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
            events = self._read_events_path(path, materialize_payloads=False)
            if not events:
                raise ValueError("agent thread is empty")
            opened = self._materialize_payload(events[0])
            closed = events[-1].get("event_type") == "thread_closed"
            if require_closed and not closed:
                raise ValueError("agent thread is not closed")
            return {
                "thread_id": str(thread_id),
                "agent_id": str(opened.get("agent_id") or ""),
                "checkpoint_step": opened.get("checkpoint_step"),
                "scope": _json_value(opened.get("scope") or {}),
                "path": self._relative_path(path),
                "cursor": {
                    "sequence": int(events[-1]["sequence"]),
                    "byte_offset": path.stat().st_size,
                },
                "tail_event_sha256": str(events[-1]["event_sha256"]),
                "file_sha256": _sha256_file(path),
                "closed": closed,
            }

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

    def publish_checkpoint_manifest(
        self,
        *,
        checkpoint_id: str,
        step: int,
    ) -> dict[str, Any]:
        normalized_checkpoint_id = str(checkpoint_id).strip()
        if not normalized_checkpoint_id or Path(normalized_checkpoint_id).name != normalized_checkpoint_id:
            raise ValueError("invalid checkpoint_id for agent thread manifest")
        step = self._normalize_checkpoint_step(step)
        if step is None:
            raise ValueError("checkpoint step must be a non-negative integer")
        with self._lock:
            step_dir = self.threads_dir / f"step_{step:06d}"
            references: dict[str, dict[str, Any]] = {}
            by_agent: dict[str, list[str]] = {}
            if step_dir.is_dir():
                for path in sorted(step_dir.glob("thr_*.jsonl")):
                    if path.is_symlink():
                        raise ValueError("agent thread must not be a symlink")
                    thread_id = path.stem
                    reference = self.get_thread_reference(
                        thread_id,
                        require_closed=True,
                    )
                    if reference.get("checkpoint_step") != step:
                        raise ValueError("agent thread checkpoint step mismatch")
                    references[thread_id] = reference
                    by_agent.setdefault(reference["agent_id"], []).append(thread_id)
            manifest = {
                "schema_version": _MANIFEST_SCHEMA_VERSION,
                "checkpoint_id": normalized_checkpoint_id,
                "step": step,
                "threads": references,
                "by_agent": {
                    agent_id: sorted(thread_ids)
                    for agent_id, thread_ids in sorted(by_agent.items())
                },
            }
            path = self.manifests_dir / f"{normalized_checkpoint_id}.json"
            self._atomic_write_json(path, manifest)
            return {
                "path": self._relative_path(path),
                "sha256": _sha256_file(path),
                "thread_count": len(references),
                "by_agent": manifest["by_agent"],
                "threads": references,
            }

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
        store = cls(run_dir, create=False)
        path = store._safe_relative_file(
            relative_path,
            component="agent thread manifest",
        )
        if not path.is_file():
            raise FileNotFoundError("agent thread manifest missing")
        if _sha256_file(path) != expected_sha256:
            raise ValueError("agent thread manifest content hash mismatch")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        normalized_step = cls._normalize_checkpoint_step(step)
        if normalized_step is None:
            raise ValueError("checkpoint step must be a non-negative integer")
        if (
            manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION
            or manifest.get("checkpoint_id") != checkpoint_id
            or not isinstance(manifest.get("step"), int)
            or isinstance(manifest.get("step"), bool)
            or manifest.get("step") != normalized_step
        ):
            raise ValueError("agent thread manifest identity mismatch")
        references = manifest.get("threads")
        by_agent = manifest.get("by_agent")
        if not isinstance(references, dict) or not isinstance(by_agent, dict):
            raise ValueError("agent thread manifest schema is invalid")
        reconstructed: dict[str, list[str]] = {}
        for thread_id, expected in references.items():
            if not isinstance(expected, dict):
                raise ValueError("agent thread reference is invalid")
            path_ref = store._safe_relative_file(
                str(expected.get("path") or ""),
                component="agent thread",
            )
            if not path_ref.is_file():
                raise FileNotFoundError("referenced agent thread missing")
            if _sha256_file(path_ref) != expected.get("file_sha256"):
                raise ValueError("agent thread content hash mismatch")
            actual = store.get_thread_reference(thread_id, require_closed=True)
            if actual != expected:
                raise ValueError("agent thread reference mismatch")
            if actual.get("checkpoint_step") != normalized_step:
                raise ValueError("agent thread checkpoint step mismatch")
            reconstructed.setdefault(actual["agent_id"], []).append(thread_id)
        normalized_reconstructed = {
            key: sorted(value) for key, value in sorted(reconstructed.items())
        }
        if normalized_reconstructed != by_agent:
            raise ValueError("agent thread manifest actor index mismatch")
        return manifest


__all__ = ["AgentThreadStore"]
