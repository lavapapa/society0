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
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Sequence


class PersistenceKind(str, Enum):
    REPLACEABLE = "replaceable"
    APPEND_ONLY_MAP = "append_only_map"
    APPEND_ONLY_LIST = "append_only_list"
    TRANSIENT = "transient"


_WILDCARD = object()


@dataclass(frozen=True, slots=True)
class PersistenceRule:
    """编译后的单个持久化声明。"""

    path: tuple[Any, ...]
    kind: PersistenceKind
    granularity: str | None = None
    default: Any = None
    has_default: bool = False


def _json_copy(value: Any) -> Any:
    """复制并检查持久化值，拒绝只能到保存线程才暴露的坏值。"""

    try:
        copied = copy.deepcopy(value)
        json.dumps(copied, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TypeError(f"persistence value is not JSON-compatible: {value!r}") from exc
    return copied


def _freeze_json(value: Any) -> Any:
    """将 JSON 值转换成递归不可变结构。"""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """把 sealed delta 转成 json.dumps 可处理的普通容器。"""

    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    return value


class PersistenceSchema:
    """JSON Schema 上的 fail-closed 持久化声明编译结果。

    Schema 只负责描述写入语义；它不改变 canonical state。动态 entry 通过
    ``additionalProperties`` 与 ``granularity=entry`` 生成内部 wildcard 规则。
    """

    def __init__(
        self,
        schema: Mapping[str, Any],
        root_path: Iterable[Any],
        rules: Mapping[tuple[Any, ...], PersistenceRule],
        *,
        source_schemas: Sequence["PersistenceSchema"] | None = None,
    ):
        self.schema = copy.deepcopy(dict(schema))
        self.root_path = tuple(root_path)
        self.rules = MappingProxyType(dict(rules))
        # A runtime World may contain more than one declared state root (the
        # environment state plus one or more Agent state schemas).  Keep the
        # original compiled schemas so callers can validate each root and
        # serialize the declarations without inventing a second schema DSL.
        self._source_schemas = tuple(source_schemas or (self,))
        self._wildcard_rules = tuple(
            rule for path, rule in self.rules.items() if any(part is _WILDCARD for part in path)
        )

    @classmethod
    def compile(cls, schema: Mapping[str, Any], *, root_path: Iterable[Any] = ()) -> "PersistenceSchema":
        if not isinstance(schema, Mapping):
            raise TypeError("persistence schema must be an object")
        root = tuple(root_path)
        if schema.get("type", "object") != "object":
            raise ValueError("persistence schema root must have type object")
        rules: dict[tuple[Any, ...], PersistenceRule] = {}

        def has_nested_declaration(node: Mapping[str, Any]) -> bool:
            for child in (node.get("properties") or {}).values():
                if isinstance(child, Mapping) and (
                    isinstance(child.get("persistence"), Mapping) or has_nested_declaration(child)
                ):
                    return True
            additional = node.get("additionalProperties")
            return isinstance(additional, Mapping) and (
                isinstance(additional.get("persistence"), Mapping) or has_nested_declaration(additional)
            )

        def add_rule(path: tuple[Any, ...], node: Mapping[str, Any]) -> PersistenceRule | None:
            declaration = node.get("persistence")
            if declaration is None:
                return None
            if not isinstance(declaration, Mapping):
                raise TypeError(f"invalid persistence declaration at {path!r}")
            raw_kind = declaration.get("kind")
            try:
                kind = PersistenceKind(raw_kind)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"unknown persistence kind at {path!r}: {raw_kind!r}") from exc
            granularity = declaration.get("granularity")
            if granularity is not None and granularity != "entry":
                raise ValueError(f"unknown persistence granularity at {path!r}: {granularity!r}")
            if kind is not PersistenceKind.REPLACEABLE and granularity is not None:
                raise ValueError(f"granularity only applies to replaceable declarations at {path!r}")
            if (
                kind is PersistenceKind.REPLACEABLE
                and node.get("type") == "object"
                and (
                    isinstance(node.get("additionalProperties"), Mapping)
                    or node.get("additionalProperties") is True
                )
                and granularity != "entry"
            ):
                raise ValueError(
                    f"unbounded replaceable map at {path!r} requires granularity='entry'"
                )
            has_default = "default" in node
            default = _json_copy(node["default"]) if has_default else None
            rule = PersistenceRule(path, kind, granularity, default, has_default)
            if path in rules:
                raise ValueError(f"duplicate persistence declaration at {path!r}")
            rules[path] = rule
            return rule

        def walk(node: Mapping[str, Any], path: tuple[Any, ...], *, inside_container: bool = False) -> None:
            if not isinstance(node, Mapping):
                raise TypeError(f"schema node at {path!r} must be an object")
            rule = add_rule(path, node)
            if rule is not None:
                if rule.kind in (PersistenceKind.REPLACEABLE, PersistenceKind.TRANSIENT):
                    if has_nested_declaration(node):
                        raise ValueError(f"persistence parent/child conflict at {path!r}")
                    if rule.granularity == "entry":
                        if node.get("type") != "object":
                            raise ValueError(f"entry granularity requires object at {path!r}")
                        # The dynamic child has the scalar/item schema. It intentionally
                        # carries no second persistence declaration.
                        rules[path + (_WILDCARD,)] = PersistenceRule(
                            path + (_WILDCARD,), rule.kind, "entry", None, False
                        )
                elif rule.kind in (PersistenceKind.APPEND_ONLY_MAP, PersistenceKind.APPEND_ONLY_LIST):
                    if has_nested_declaration(node):
                        raise ValueError(f"persistence parent/child conflict at {path!r}")
                return

            schema_type = node.get("type")
            if schema_type in (None, "object"):
                properties = node.get("properties") or {}
                if not isinstance(properties, Mapping):
                    raise TypeError(f"properties at {path!r} must be an object")
                for name, child in properties.items():
                    if not isinstance(name, str):
                        raise TypeError(f"schema property name at {path!r} must be a string")
                    walk(child, path + (name,), inside_container=inside_container)
                additional = node.get("additionalProperties", False)
                if isinstance(additional, Mapping):
                    walk(additional, path + (_WILDCARD,), inside_container=inside_container)
                elif additional not in (False, True):
                    raise TypeError(f"additionalProperties at {path!r} must be boolean or schema")
            elif schema_type == "array":
                items = node.get("items")
                if isinstance(items, Mapping):
                    walk(items, path + (_WILDCARD,), inside_container=inside_container)
                elif items is not None:
                    raise TypeError(f"items at {path!r} must be a schema")
            else:
                raise ValueError(f"missing persistence declaration at {path!r}")

        # The root object is a structural node and does not itself need a declaration.
        walk(schema, root)
        return cls(schema, root, rules)

    @classmethod
    def merge(cls, *schemas: "PersistenceSchema") -> "PersistenceSchema":
        """Merge declarations that describe separate canonical state roots.

        Each input schema is already compiled with its canonical root path,
        for example ``("environment", "state")`` or
        ``("agents", _WILDCARD, "state")``.  Rules are combined verbatim so
        ``StateDeltaJournal`` resolves concrete Agent IDs through the wildcard
        rule without enumerating the full World during a checkpoint publish.
        The first schema remains the default validation schema for backwards
        compatibility; ``source_schemas`` exposes every root to callers that
        need to validate or restore all state trees.
        """

        if not schemas:
            raise ValueError("at least one persistence schema is required")
        for schema in schemas:
            if not isinstance(schema, cls):
                raise TypeError("PersistenceSchema.merge expects compiled schemas")

        rules: dict[tuple[Any, ...], PersistenceRule] = {}
        for schema in schemas:
            for path, rule in schema.rules.items():
                if path in rules and rules[path] != rule:
                    raise ValueError(f"conflicting persistence declaration at {path!r}")
                rules[path] = rule
        return cls(
            schemas[0].schema,
            schemas[0].root_path,
            rules,
            source_schemas=schemas,
        )

    @property
    def source_schemas(self) -> tuple["PersistenceSchema", ...]:
        """Return the individual root declarations used to build this schema."""

        return self._source_schemas

    def declaration_payloads(self) -> tuple[dict[str, Any], ...]:
        """Return serializable ``root_path``/schema pairs for checkpoint metadata."""

        return tuple(
            {
                # ``_WILDCARD`` is an in-memory matcher object and cannot be
                # JSON encoded.  ``*`` is reserved for this metadata format
                # and converted back by the v4 resolver.
                "root_path": [
                    "*" if part is _WILDCARD else part for part in schema.root_path
                ],
                "schema": copy.deepcopy(schema.schema),
            }
            for schema in self._source_schemas
        )

    @staticmethod
    def _matches(pattern: tuple[Any, ...], path: tuple[Any, ...]) -> bool:
        return len(pattern) == len(path) and all(
            expected is _WILDCARD or expected == actual for expected, actual in zip(pattern, path)
        )

    def resolve(self, path: Iterable[Any]) -> PersistenceRule | None:
        concrete = tuple(path)
        exact = self.rules.get(concrete)
        if exact is not None:
            return exact
        for pattern, rule in self.rules.items():
            if any(part is _WILDCARD for part in pattern) and self._matches(pattern, concrete):
                return rule
        return None

    def _schema_node(self, path: tuple[Any, ...]) -> Mapping[str, Any] | None:
        if path[: len(self.root_path)] != self.root_path:
            return None
        node: Mapping[str, Any] = self.schema
        for part in path[len(self.root_path) :]:
            if not isinstance(node, Mapping):
                return None
            properties = node.get("properties") or {}
            if part in properties:
                node = properties[part]
                continue
            additional = node.get("additionalProperties")
            if isinstance(additional, Mapping):
                node = additional
                continue
            return None
        return node

    @staticmethod
    def _validate_type(value: Any, node: Mapping[str, Any], path: tuple[Any, ...]) -> None:
        expected = node.get("type")
        if expected is None:
            return
        expected_types = set(expected) if isinstance(expected, list) else {expected}
        valid = any(
            (kind == "object" and isinstance(value, Mapping))
            or (kind == "array" and isinstance(value, list))
            or (kind == "string" and isinstance(value, str))
            or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (kind == "boolean" and isinstance(value, bool))
            or (kind == "null" and value is None)
            for kind in expected_types
        )
        if not valid:
            raise TypeError(f"state value at {path!r} does not match schema type {expected!r}")

    def validate_initial_state(self, state: Mapping[str, Any]) -> None:
        if not isinstance(state, Mapping):
            raise TypeError("initial state must be an object")

        def validate(node: Mapping[str, Any], value: Any, path: tuple[Any, ...]) -> None:
            self._validate_type(value, node, path)
            _json_copy(value)
            rule = self.resolve(path)
            if rule is not None:
                if rule.kind is PersistenceKind.APPEND_ONLY_MAP:
                    if not isinstance(value, Mapping):
                        raise TypeError(f"append-only map at {path!r} must be an object")
                    return
                if rule.kind is PersistenceKind.APPEND_ONLY_LIST:
                    if not isinstance(value, list):
                        raise TypeError(f"append-only list at {path!r} must be an array")
                    return
                if (
                    rule.kind is PersistenceKind.REPLACEABLE
                    and rule.granularity == "entry"
                    and node.get("type") == "object"
                ):
                    if not isinstance(value, Mapping):
                        raise TypeError(f"entry-granularity map at {path!r} must be an object")
                    item_schema = node.get("additionalProperties")
                    for key, item in value.items():
                        if isinstance(item_schema, Mapping):
                            validate(item_schema, item, path + (key,))
                    return
                return
            if not isinstance(value, Mapping):
                return
            properties = node.get("properties") or {}
            additional = node.get("additionalProperties", False)
            for key, item in value.items():
                child_path = path + (key,)
                if key in properties:
                    validate(properties[key], item, child_path)
                elif isinstance(additional, Mapping):
                    validate(additional, item, child_path)
                else:
                    raise ValueError(f"undeclared initial state field at {child_path!r}")

        validate(self.schema, state, self.root_path)

    @property
    def append_only_map_paths(self) -> tuple[tuple[Any, ...], ...]:
        return tuple(path for path, rule in self.rules.items() if rule.kind is PersistenceKind.APPEND_ONLY_MAP)


@dataclass(frozen=True, slots=True)
class SealedTickDelta:
    step: int
    replacements: tuple[Mapping[str, Any], ...]
    appends: tuple[Mapping[str, Any], ...]
    write_epoch_ids: tuple[str, ...] = ()
    annotations: Mapping[str, Any] | None = None


class StateDeltaJournal:
    """在 canonical writer 的调用栈内捕获一个 Tick 的持久化变化。"""

    def __init__(self, declarations: PersistenceSchema | Mapping[tuple[Any, ...], PersistenceKind | str]):
        self._schema = declarations if isinstance(declarations, PersistenceSchema) else None
        self._declarations = (
            {}
            if self._schema is not None
            else {tuple(path): PersistenceKind(kind) for path, kind in declarations.items()}
        )
        self._active_step: int | None = None
        self._sequence = 0
        self._replacements: dict[tuple[str, ...], dict[str, Any]] = {}
        self._appends: list[dict[str, Any]] = []
        self._pending_map_ids: set[tuple[tuple[str, ...], str]] = set()
        self._canonical_lookup: Callable[[tuple[Any, ...]], Any] | None = None
        self._write_epoch_id: str | None = None

    @property
    def active_step(self) -> int | None:
        """当前正在捕获的 Tick；未激活时返回 ``None``。"""

        return self._active_step

    def bind_canonical_state(self, state_or_lookup: Mapping[str, Any] | Callable[[tuple[Any, ...]], Any]) -> None:
        """绑定 canonical 容器，用实际 membership 检查 append-only map。"""

        if callable(state_or_lookup):
            self._canonical_lookup = state_or_lookup
        else:
            def lookup(path: tuple[Any, ...]) -> Any:
                current: Any = state_or_lookup
                for part in path:
                    if isinstance(current, Mapping) and part in current:
                        current = current[part]
                    else:
                        return None
                return current
            self._canonical_lookup = lookup

    def begin_tick(self, step: int, *, write_epoch_id: str | None = None) -> None:
        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            raise ValueError("step must be a non-negative integer")
        if self._active_step is not None:
            raise RuntimeError("a tick delta is already active")
        self._active_step = step
        self._write_epoch_id = str(write_epoch_id) if write_epoch_id is not None else None
        self._sequence = 0
        self._replacements = {}
        self._appends = []
        self._pending_map_ids = set()

    def _kind(self, path: Iterable[Any]) -> tuple[tuple[Any, ...], PersistenceKind]:
        self._require_active()
        normalized = tuple(path)
        if self._schema is not None:
            rule = self._schema.resolve(normalized)
            if rule is None:
                raise ValueError(f"undeclared persistence path: {normalized!r}")
            return normalized, rule.kind
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
        value = _json_copy(value)
        if kind is PersistenceKind.TRANSIENT:
            return
        if kind is not PersistenceKind.REPLACEABLE:
            raise ValueError(f"set is not allowed for {kind.value}: {normalized!r}")
        self._replacements[normalized] = {
            "path": list(normalized),
            "operation": "set",
            "value": value,
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
        normalized_id = fact_id
        _json_copy(value)
        identity = (normalized, normalized_id)
        target = self._canonical_lookup(normalized) if self._canonical_lookup else None
        if (
            identity in self._pending_map_ids
            or (isinstance(target, Mapping) and normalized_id in target)
        ):
            raise ValueError(f"duplicate append-only map id: {normalized_id}")
        self._pending_map_ids.add(identity)
        self._appends.append(
            {
                "path": list(normalized),
                "operation": "map_create",
                "id": normalized_id,
                "value": _json_copy(value),
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
                "value": _json_copy(value),
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

        container = tuple(container_path)
        child = container + (key,) if key is not None else container
        if self._schema is not None:
            container_rule = self._schema.resolve(container)
            child_rule = self._schema.resolve(child) if key is not None else None
            container_kind = container_rule.kind if container_rule is not None else None
            child_kind = child_rule.kind if child_rule is not None else None
        else:
            container_kind = self._declarations.get(container)
            child_kind = self._declarations.get(child)

        if container_kind is PersistenceKind.APPEND_ONLY_MAP:
            if operation != "set" or key is None:
                raise ValueError("append-only map only accepts new keys")
            self.record_map_create(container, key, value)
            return
        if container_kind is PersistenceKind.APPEND_ONLY_LIST:
            if operation != "append":
                raise ValueError("append-only list only accepts append")
            self.record_append(container, value)
            return
        if container_kind is PersistenceKind.TRANSIENT:
            if operation in ("set", "append", "insert"):
                _json_copy(value)
            return
        if child_kind in (PersistenceKind.REPLACEABLE, PersistenceKind.TRANSIENT):
            if operation == "set":
                self.record_set(child, value)
                return
            if operation == "delete":
                self.record_delete(child)
                return
        raise ValueError(f"undeclared or unsupported persistence write: {child!r}")

    def validate_proxy_operations(self, operations: Sequence[tuple[Iterable[Any], str, Any, Any]]) -> None:
        """在批量代理操作落到底层前完整预检，避免 extend 部分成功。"""

        pending = set(self._pending_map_ids)
        for container_path, operation, key, value in operations:
            container = tuple(container_path)
            if self._schema is not None:
                container_rule = self._schema.resolve(container)
                child_rule = self._schema.resolve(container + (key,)) if key is not None else None
                container_kind = container_rule.kind if container_rule else None
                child_kind = child_rule.kind if child_rule else None
            else:
                container_kind = self._declarations.get(container)
                child_kind = self._declarations.get(container + (key,)) if key is not None else None
            if container_kind is PersistenceKind.APPEND_ONLY_LIST and operation == "append":
                _json_copy(value)
                continue
            if container_kind is PersistenceKind.TRANSIENT:
                if operation in ("set", "append", "insert"):
                    _json_copy(value)
                continue
            if container_kind is PersistenceKind.APPEND_ONLY_MAP and operation == "set" and key is not None:
                _json_copy(value)
                identity = (container, key)
                target = self._canonical_lookup(container) if self._canonical_lookup else None
                if identity in pending or (isinstance(target, Mapping) and key in target):
                    raise ValueError(f"duplicate append-only map id: {key}")
                pending.add(identity)
                continue
            if child_kind in (PersistenceKind.REPLACEABLE, PersistenceKind.TRANSIENT) and operation in ("set", "delete"):
                if operation == "set":
                    _json_copy(value)
                continue
            raise ValueError(f"undeclared or unsupported persistence write: {container + (key,)!r}")

    def seal_tick(self) -> SealedTickDelta:
        self._require_active()
        assert self._active_step is not None
        result = SealedTickDelta(
            step=self._active_step,
            replacements=tuple(_freeze_json(item) for item in sorted(self._replacements.values(), key=lambda item: item["sequence"])),
            appends=tuple(_freeze_json(item) for item in sorted(self._appends, key=lambda item: item["sequence"])),
            write_epoch_ids=(self._write_epoch_id,) if self._write_epoch_id else (),
        )
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
        self._write_epoch_id = None

    def _require_active(self) -> None:
        if self._active_step is None:
            raise RuntimeError("no active tick delta")


class V4CheckpointStore:
    """发布和恢复 v4 增量链；complete marker 是唯一提交点。"""

    VERSION = "complete_step_v4"

    def __init__(self, root: str | Path, *, branch_id: str = "main"):
        if not isinstance(branch_id, str) or not branch_id or any(
            part in branch_id for part in ("/", "\\", "..")
        ):
            raise ValueError("branch_id must be a non-empty path-safe name")
        self.root = Path(root)
        self.branch_id = branch_id
        self.base = self.root / "checkpoints" / "v4"
        self.segments_dir = self.base / "segments"
        self.replacements_dir = self.base / "replacements"
        self.manifests_dir = self.base / "manifests"
        self.branches_dir = self.base / "branches"
        self.complete_dir = (
            self.base / "complete"
            if branch_id == "main"
            else self.branches_dir / branch_id / "complete"
        )
        for directory in (
            self.segments_dir,
            self.replacements_dir,
            self.manifests_dir,
            self.complete_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.metrics = {"history_entries_read_while_publishing": 0}
        self._publishing = False
        # A newer marker can be present but damaged.  Do not let one bad
        # marker prevent the store from opening: the manager's ``latest``
        # resolver deliberately walks back to the newest valid marker.
        self._latest_step = None
        self._latest_checkpoint_id = None
        self._latest_state_sha256 = "0" * 64
        self._run_id: str | None = None
        for candidate in reversed(self.available_steps()):
            try:
                latest = self._read_marker(candidate)
                # Validate the manifest hash before using it as the publish
                # parent.  A malformed newest marker must never become the
                # parent of a newly published chain.
                manifest_path = self.root / latest["manifest_file"]
                raw_manifest = manifest_path.read_bytes()
                if self._sha256(raw_manifest) != latest.get("manifest_sha256"):
                    continue
                self._latest_step = candidate
                self._latest_checkpoint_id = latest["checkpoint_id"]
                self._latest_state_sha256 = latest["state_sha256"]
                manifest = json.loads(raw_manifest)
                run_id = manifest.get("run_id")
                self._run_id = str(run_id) if run_id else None
                break
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue

    @staticmethod
    def _canonical_bytes(value: Any) -> bytes:
        return json.dumps(
            _thaw_json(value),
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

    def publish(
        self,
        delta: SealedTickDelta,
        *,
        checkpoint_id: str | None = None,
        thread_manifest: Mapping[str, Any] | None = None,
        memory_view: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._publish_delta(
            delta,
            checkpoint_id=checkpoint_id,
            thread_manifest=thread_manifest,
            memory_view=memory_view,
        )

    def publish_root(
        self,
        entries: Sequence[Mapping[str, Any]],
        *,
        metadata: Mapping[str, Any] | None = None,
        step: int = 0,
        checkpoint_id: str | None = None,
        thread_manifest: Mapping[str, Any] | None = None,
        memory_view: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """发布 step 0 根基点。

        ``entries`` 是由 manager 根据声明筛出的持久化初始字段操作。根基点
        走同一 replacement/manifest/marker 提交流程，transient 字段不会进入
        文件；metadata 只保存固定 World 身份信息，不参与增量状态应用。
        """

        if isinstance(step, bool) or not isinstance(step, int) or step != 0:
            raise ValueError("root checkpoint step must be 0")
        delta = SealedTickDelta(
            step=0,
            replacements=tuple(_freeze_json(dict(entry)) for entry in entries),
            appends=(),
        )
        return self._publish_delta(
            delta,
            root_metadata=metadata,
            checkpoint_id=checkpoint_id,
            thread_manifest=thread_manifest,
            memory_view=memory_view,
        )

    def fork(self, branch_id: str, *, step: int) -> "V4CheckpointStore":
        """从完整 checkpoint 建立同一 lineage 内的独立分支。

        分支 marker 直接引用已经验证的 immutable manifest；segments、
        replacements 与 manifests 都留在共享 v4 组件目录，因此创建分支不
        复制历史字节，也不改写源分支 marker。
        """

        if branch_id == self.branch_id:
            raise ValueError("fork branch_id must differ from its source")
        source = self.resolve(step)
        branch = type(self)(self.root, branch_id=branch_id)
        if branch.available_steps():
            raise ValueError(f"branch already exists: {branch_id}")
        marker = dict(source["marker"])
        marker["branch_id"] = branch_id
        marker["forked_from"] = {
            "branch_id": self.branch_id,
            "step": step,
            "checkpoint_id": source["checkpoint_id"],
        }
        marker_path = branch.complete_dir / f"step_{step:06d}.json"
        branch._atomic_write(marker_path, branch._canonical_bytes(marker))
        branch._latest_step = step
        branch._latest_checkpoint_id = source["checkpoint_id"]
        branch._latest_state_sha256 = marker["state_sha256"]
        branch._run_id = (source["manifest"].get("run_id") or self._run_id)
        return branch

    def _publish_delta(
        self,
        delta: SealedTickDelta,
        *,
        root_metadata: Mapping[str, Any] | None = None,
        checkpoint_id: str | None = None,
        thread_manifest: Mapping[str, Any] | None = None,
        memory_view: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._publishing:
            raise RuntimeError("only one unfinished checkpoint is allowed")
        is_root = root_metadata is not None
        if is_root and delta.step != 0:
            raise ValueError("root checkpoint step must be 0")
        if self._latest_step is not None and delta.step <= self._latest_step:
            raise ValueError("checkpoint steps must increase")

        self._publishing = True
        checkpoint_id = str(checkpoint_id or uuid.uuid4().hex)
        if not checkpoint_id or Path(checkpoint_id).name != checkpoint_id:
            raise ValueError("invalid checkpoint_id")
        bytes_written = 0
        try:
            parent_manifest = None if is_root else self._latest_checkpoint_id
            parent_state_sha256 = "0" * 64 if is_root else self._latest_state_sha256
            run_id = (
                str((root_metadata or {}).get("run_id") or uuid.uuid4().hex)
                if is_root
                else (self._run_id or uuid.uuid4().hex)
            )

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
                "branch_id": self.branch_id,
                "thread_manifest_sha256": (
                    str(thread_manifest.get("sha256")) if thread_manifest else None
                ),
                "memory_view": _thaw_json(_freeze_json(dict(memory_view or {}))),
                "annotations": _thaw_json(
                    _freeze_json(dict(delta.annotations or {}))
                ),
            }
            state_sha256 = self._sha256(self._canonical_bytes(state_material))
            manifest = {
                "checkpoint_version": self.VERSION,
                "checkpoint_id": checkpoint_id,
                "step": delta.step,
                "run_id": run_id,
                "branch_id": self.branch_id,
                "parent_checkpoint_id": parent_manifest,
                "replacement_file": replacement["path"],
                "replacement_sha256": replacement["sha256"],
                "new_segments": segments,
                "state_sha256": state_sha256,
                "created_at": time.time(),
                "thread_manifest": (
                    _thaw_json(_freeze_json(dict(thread_manifest)))
                    if thread_manifest is not None
                    else None
                ),
                "memory_view": _thaw_json(_freeze_json(dict(memory_view or {}))),
                "annotations": _thaw_json(
                    _freeze_json(dict(delta.annotations or {}))
                ),
            }
            if root_metadata is not None:
                # Keep the fixed World metadata on the root only.  It is
                # immutable and is never copied into subsequent delta files.
                manifest["root_metadata"] = _thaw_json(_freeze_json(dict(root_metadata)))
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
                "run_id": run_id,
                "branch_id": self.branch_id,
                "manifest_file": self._relative(manifest_path),
                "manifest_sha256": manifest_sha256,
                "state_sha256": state_sha256,
            }
            marker_path = self.complete_dir / f"step_{delta.step:06d}.json"
            marker_bytes = self._canonical_bytes(marker)
            try:
                bytes_written += self._atomic_write(marker_path, marker_bytes)
            except BaseException:
                # ``replace`` 是提交点。若故障发生在 rename 之后（例如目录
                # fsync/通知失败），marker 已经完整可见，调用方必须把该 Tick
                # 当成已提交，避免随后重试产生两条历史。
                if not marker_path.is_file() or marker_path.read_bytes() != marker_bytes:
                    raise
                bytes_written += len(marker_bytes)
            self._latest_step = delta.step
            self._latest_checkpoint_id = checkpoint_id
            self._latest_state_sha256 = state_sha256
            self._run_id = run_id
            return {**marker, "bytes_written": bytes_written}
        finally:
            self._publishing = False

    def resolve(self, step: int | None = None) -> dict[str, Any]:
        """解析一个 v4 marker，并校验 manifest 链。

        ``step=None`` 按降序跳过损坏 marker；显式 step 则把损坏原因直接
        抛给调用方，便于区分“没有 checkpoint”和“checkpoint 已损坏”。
        """

        if step is not None and (isinstance(step, bool) or not isinstance(step, int) or step < 0):
            raise ValueError("step must be a non-negative integer")
        candidates = [step] if step is not None else list(reversed(self.available_steps()))
        if not candidates:
            raise FileNotFoundError("No complete v4 checkpoints found")
        last_error: Exception | None = None
        for candidate in candidates:
            try:
                marker = self._read_marker(candidate)
                chain = self._manifest_chain(candidate)
                manifest_path = self.root / marker["manifest_file"]
                manifest_bytes = manifest_path.read_bytes()
                if self._sha256(manifest_bytes) != marker.get("manifest_sha256"):
                    raise ValueError("manifest content hash mismatch")
                manifest = chain[-1]
                # Resolver validation includes component hashes and the state
                # hash chain.  This keeps ``latest`` from selecting a marker
                # whose manifest exists but whose replacement/segment is
                # already damaged.
                self.restore(candidate)
                return {
                    "step": candidate,
                    "checkpoint_id": marker["checkpoint_id"],
                    "marker": marker,
                    "manifest": manifest,
                    "marker_file": self.complete_dir / f"step_{candidate:06d}.json",
                    "manifest_file": manifest_path,
                }
            except (FileNotFoundError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                if step is not None:
                    raise
                last_error = exc
        raise FileNotFoundError("No complete v4 checkpoints found") from last_error

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
        if marker.get("checkpoint_version") != self.VERSION:
            raise ValueError(
                f"Unsupported checkpoint version: {marker.get('checkpoint_version')!r}"
            )
        if (
            marker.get("complete") is not True
            or marker.get("recoverable") is not True
            or marker.get("step") != step
            or marker.get("branch_id") != self.branch_id
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
        seen: set[str] = set()
        expected_id: str | None = marker["checkpoint_id"]
        expected_state = marker["state_sha256"]
        child_step: int | None = None
        while expected_id is not None:
            if expected_id in seen:
                raise ValueError("checkpoint manifest cycle detected")
            seen.add(expected_id)
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
            manifest_step = manifest.get("step")
            if isinstance(manifest_step, bool) or not isinstance(manifest_step, int):
                raise ValueError("checkpoint manifest step is invalid")
            if child_step is not None and manifest_step >= child_step:
                raise ValueError("checkpoint manifest parent step must decrease")
            child_step = manifest_step
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
        chain = self._manifest_chain(step)
        for manifest in chain:
            thread_manifest = manifest.get("thread_manifest")
            if isinstance(thread_manifest, Mapping):
                from .agent.thread_store import AgentThreadStore

                AgentThreadStore.validate_tick_manifest_from(
                    self.root,
                    thread_manifest,
                    expected_checkpoint_id=manifest["checkpoint_id"],
                    expected_step=manifest["step"],
                )
            replacement = self._read_json_component(
                manifest["replacement_file"],
                manifest["replacement_sha256"],
                "replacement",
            )
            operations = list(replacement["entries"])
            segment_hashes = []
            for segment in manifest["new_segments"]:
                payload = self._read_json_component(
                    segment["path"], segment["sha256"], "segment"
                )
                if len(payload["entries"]) != segment["entry_count"]:
                    raise ValueError("segment entry count mismatch")
                operations.extend(payload["entries"])
                segment_hashes.append(segment["sha256"])
            # Delta entries share one monotonic sequence domain, even though
            # replacements and append segments are stored separately.  Apply
            # them in that original order when operations from a tick are
            # interleaved.
            operations.sort(key=lambda operation: operation.get("sequence", 0))
            for operation in operations:
                self._apply(state, operation)
            expected_state = self._sha256(
                self._canonical_bytes(
                    {
                        "parent_state_sha256": parent_state,
                        "replacement_sha256": manifest["replacement_sha256"],
                        "segment_sha256": segment_hashes,
                        "branch_id": manifest.get("branch_id", "main"),
                        "thread_manifest_sha256": (
                            str(manifest["thread_manifest"].get("sha256"))
                            if isinstance(manifest.get("thread_manifest"), Mapping)
                            else None
                        ),
                        "memory_view": manifest.get("memory_view") or {},
                        "annotations": manifest.get("annotations") or {},
                    }
                )
            )
            if expected_state != manifest["state_sha256"]:
                raise ValueError("state hash chain mismatch")
            parent_state = expected_state

        # Transient values are absent from replacement/segment files.  The
        # root manifest carries their schema defaults so the low-level store
        # restore remains useful on its own (before PersistenceManager builds a
        # World and reapplies defaults there as well).
        if chain and isinstance(chain[0].get("root_metadata"), Mapping):
            metadata = chain[0]["root_metadata"]
            payloads = metadata.get("persistence_schemas") or []
            if not payloads and isinstance(metadata.get("persistence_schema"), Mapping):
                payloads = [
                    {
                        "root_path": metadata.get("persistence_root_path")
                        or ["environment", "state"],
                        "schema": metadata["persistence_schema"],
                    }
                ]

            def set_default(path: tuple[Any, ...], default: Any) -> None:
                current: Any = state
                for part in path[:-1]:
                    if not isinstance(current, dict):
                        return
                    current = current.setdefault(part, {})
                if isinstance(current, dict):
                    current.setdefault(path[-1], copy.deepcopy(default))

            for payload in payloads:
                if not isinstance(payload, Mapping) or not isinstance(payload.get("schema"), Mapping):
                    continue
                raw_root = payload.get("root_path") or ["environment", "state"]
                root = tuple(_WILDCARD if part == "*" else part for part in raw_root)
                try:
                    source = PersistenceSchema.compile(payload["schema"], root_path=root)
                except (TypeError, ValueError):
                    continue
                for path, rule in source.rules.items():
                    if rule.kind is not PersistenceKind.TRANSIENT or not rule.has_default:
                        continue
                    if any(part is _WILDCARD for part in path):
                        index = path.index(_WILDCARD)
                        prefix = path[:index]
                        container = state
                        for part in prefix:
                            if not isinstance(container, Mapping):
                                container = None
                                break
                            container = container.get(part)
                        if not isinstance(container, Mapping):
                            continue
                        suffix = path[index + 1 :]
                        for key in container:
                            set_default(prefix + (key,) + suffix, rule.default)
                    else:
                        set_default(path, rule.default)
        return state

    def committed_memory_epoch_ids(self, step: int) -> set[str]:
        """返回目标 marker 链中已提交的记忆写入 epoch。"""

        committed: set[str] = set()
        for manifest in self._manifest_chain(step):
            view = manifest.get("memory_view") or {}
            for epoch_id in view.get("write_epoch_ids") or ():
                committed.add(str(epoch_id))
        return committed

    def checkpoint_annotations(self, step: int) -> dict[str, Any]:
        """合并目标 marker 链上的有界审计注释。"""

        annotations: dict[str, Any] = {}
        for manifest in self._manifest_chain(step):
            annotations.update(copy.deepcopy(manifest.get("annotations") or {}))
        return annotations

    def cleanup_orphans(self) -> list[str]:
        """删除没有被任何完整 marker 引用的 v4 组件。"""

        if self._publishing:
            raise RuntimeError("cannot collect orphans while publishing")
        gc_started_ns = time.time_ns()
        referenced_manifests: set[Path] = set()
        referenced_components: set[Path] = set()
        branch_complete_dirs = [self.base / "complete"] + list(
            self.branches_dir.glob("*/complete")
        )
        seen_checkpoint_ids: set[str] = set()
        for complete_dir in branch_complete_dirs:
            branch_id = "main" if complete_dir == self.base / "complete" else complete_dir.parent.name
            branch_store = type(self)(self.root, branch_id=branch_id)
            for step in branch_store.available_steps():
                marker = branch_store._read_marker(step)
                for manifest in branch_store._manifest_chain(step):
                    checkpoint_id = manifest["checkpoint_id"]
                    if checkpoint_id in seen_checkpoint_ids:
                        continue
                    seen_checkpoint_ids.add(checkpoint_id)
                    manifest_path = self.manifests_dir / f"{checkpoint_id}.json"
                    referenced_manifests.add(manifest_path)
                    referenced_components.add(self.root / manifest["replacement_file"])
                    referenced_components.update(
                        self.root / segment["path"]
                        for segment in manifest["new_segments"]
                    )
                    thread_manifest = manifest.get("thread_manifest")
                    if isinstance(thread_manifest, Mapping):
                        relative_path = thread_manifest.get("relative_path") or thread_manifest.get(
                            "path"
                        )
                        if isinstance(relative_path, str):
                            referenced_components.add(self.root / relative_path)
                referenced_manifests.add(self.root / marker["manifest_file"])

        removed: list[str] = []
        candidates = (
            list(self.manifests_dir.glob("*.json"))
            + list(self.replacements_dir.glob("*.json.gz"))
            + list(self.segments_dir.glob("*.json.gz"))
            + list((self.root / "agent_threads" / "manifests").glob("*.json"))
        )
        reachable = referenced_manifests | referenced_components
        for path in candidates:
            if path not in reachable and path.stat().st_mtime_ns < gc_started_ns:
                path.unlink(missing_ok=True)
                removed.append(self._relative(path))
        return sorted(removed)
