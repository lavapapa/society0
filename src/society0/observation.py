"""运行中和离线共用的只读查询；索引保存在独立本地目录。"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def write_runtime_status(run_dir, **fields):
    """原子替换小型进度；不包含业务载荷，也不承担检查点提交职责。"""
    path = Path(run_dir) / "runtime-status.json"
    fields.update(observed_at=time.time(), producer_pid=os.getpid())
    fd, temporary = tempfile.mkstemp(prefix=".status-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(fields, handle, ensure_ascii=False)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def _key(path):
    # 每段带终止符，使子路径成为可索引的前缀，保留键类型。
    return "".join(_json(part) + "\x1f" for part in path)


class ObservationReader:
    """状态页返回持久化声明的当前条目，正文可通过引用完整读取。"""

    def __init__(self, run_dir, *, index_dir=None, branch_id="main", rebuild=False):
        self.run_dir = Path(run_dir).resolve()
        if not self.run_dir.is_dir():
            raise FileNotFoundError(self.run_dir)
        self.branch_id = branch_id
        self.base = self.run_dir / "checkpoints" / "v4"
        self.latest_path = (self.base / "latest.json" if branch_id == "main" else
                            self.base / "branches" / branch_id / "latest.json")
        self._temporary = tempfile.TemporaryDirectory(prefix="society0-observation-") if index_dir is None else None
        directory = Path(self._temporary.name if self._temporary else index_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.index_dir = directory
        if rebuild:
            (directory / "observation.sqlite").unlink(missing_ok=True)
        self.db = sqlite3.connect(directory / "observation.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA cache_size=-4096")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, version INTEGER UNIQUE, step INTEGER, marker TEXT);
            CREATE TABLE IF NOT EXISTS entries(path_key TEXT, path TEXT, start INTEGER, end INTEGER,
                source INTEGER, sequence INTEGER, raw_bytes INTEGER, operation TEXT, ordinal INTEGER, structural INTEGER,
                PRIMARY KEY(path_key,start));
            CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY,path TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS scope_names(id INTEGER PRIMARY KEY,path TEXT UNIQUE);
            CREATE INDEX IF NOT EXISTS entries_current ON entries(path_key) WHERE end IS NULL;
            CREATE INDEX IF NOT EXISTS entries_ordinal ON entries(ordinal);
            CREATE INDEX IF NOT EXISTS entries_source ON entries(source,sequence);
            CREATE TABLE IF NOT EXISTS scopes(scope INTEGER,entry_id INTEGER,start INTEGER,end INTEGER,ordinal INTEGER,
                PRIMARY KEY(scope,entry_id,start)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS scopes_current ON scopes(scope,ordinal) WHERE end IS NULL;
            CREATE INDEX IF NOT EXISTS scopes_history ON scopes(scope,ordinal,start,end);
            CREATE INDEX IF NOT EXISTS scopes_current_entries ON scopes(entry_id) WHERE end IS NULL;
            CREATE TABLE IF NOT EXISTS datasets(path TEXT PRIMARY KEY, reference TEXT, checkpoint_id TEXT, root TEXT);
            CREATE TABLE IF NOT EXISTS threads(id TEXT, version INTEGER, root TEXT, reference TEXT, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS lengths(path_key TEXT PRIMARY KEY, count INTEGER);
        """)
        identity = _json([str(self.run_dir), branch_id])
        saved = self.db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
        if saved and saved[0] != identity:
            self.db.close()
            raise ValueError("index_source_mismatch")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES ('source',?)", (identity,))
        self._bind_run_identity()

    def close(self):
        self.db.close()
        if self._temporary:
            self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _latest_marker(self):
        candidate = _read_json(self.latest_path)
        complete = self.latest_path.parent / "complete"
        if not complete.is_dir():
            return None
        directory_time = complete.stat().st_mtime_ns
        pointer_time = self.latest_path.stat().st_mtime_ns if self.latest_path.exists() else 0
        if candidate is not None:
            marker = _read_json(complete / f"step_{candidate['step']:06d}.json")
            if marker and marker.get("complete") is True and marker.get("checkpoint_id") == candidate.get("checkpoint_id") and pointer_time >= directory_time:
                return marker
        cached = getattr(self, "_marker_scan", None)
        if cached and cached[0] == directory_time:
            return cached[1]
        # 仅目录发布位置改变或指针缺失时扫描，并缓存观察结果。
        found = None
        for path in complete.glob("step_*.json"):
            marker = _read_json(path)
            if marker and marker.get("complete") is True and (found is None or marker["step"] > found["step"]):
                found = marker
        self._marker_scan = (directory_time, found)
        return found

    def _bind_run_identity(self, marker=None):
        marker = marker or self._latest_marker()
        run_id = (marker or {}).get("run_id") or (_read_json(self.run_dir / "runtime-status.json", {}) or {}).get("run_id")
        if run_id is None:
            return
        saved = self.db.execute("SELECT value FROM meta WHERE key='run_id'").fetchone()
        if saved and saved[0] != run_id:
            raise ValueError("index_source_mismatch: run_id changed; rebuild the local index")
        if saved is None:
            with self.db:
                self.db.execute("INSERT INTO meta VALUES ('run_id',?)", (run_id,))

    @staticmethod
    def _require_source(path):
        if not Path(path).is_file():
            raise FileNotFoundError(f"source_missing: {path}")

    def status(self):
        status = _read_json(self.run_dir / "runtime-status.json", {})
        marker = self._latest_marker()
        self._bind_run_identity(marker)
        row = self.db.execute("SELECT marker FROM checkpoints ORDER BY version DESC LIMIT 1").fetchone()
        indexed = json.loads(row[0]) if row else None
        return {**status, "run_id": status.get("run_id") or (marker or {}).get("run_id") or str(self.run_dir),
                "committed_checkpoint": marker, "indexed_checkpoint": indexed,
                "index_lag_steps": max(0, (marker or {}).get("step", 0) - (indexed or {}).get("step", 0)),
                "index_error": getattr(self, "index_error", None),
                "status_age_seconds": max(0., time.time() - status["observed_at"]) if status.get("observed_at") else None}

    def watch(self, *, cursor=None, limit=100, max_bytes=65536):
        if limit < 1 or max_bytes < 512:
            raise ValueError("positive limit and max_bytes >= 512 required")
        status = self.status()
        # 轮询只比较生产者发布的位置，无连接队列或载荷副本。
        position = {"run_id": status["run_id"], "observed_at": status.get("observed_at"),
                    "committed": (status["committed_checkpoint"] or {}).get("checkpoint_id"),
                    "indexed": (status["indexed_checkpoint"] or {}).get("checkpoint_id")}
        if cursor and cursor.get("run_id") != position["run_id"]:
            raise ValueError("cursor_mismatch")
        item = {key: status.get(key) for key in ("run_id", "phase", "executing_step", "last_completed_step", "observed_at")}
        item.update(committed_checkpoint=position["committed"], indexed_checkpoint=position["indexed"])
        return {"items": [] if cursor == position else [item], "next_cursor": position}

    def sync(self):
        """追赶完整 marker；每个检查点由一个本地事务处理。"""
        from .checkpoint_records import iter_metadata
        latest = self._latest_marker()
        self._bind_run_identity(latest)
        if not latest:
            return self.status()
        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS pending_checkpoints(position INTEGER PRIMARY KEY,root TEXT,checkpoint_id TEXT UNIQUE)")
        self.db.execute("DELETE FROM pending_checkpoints")
        root, checkpoint_id, position = self.run_dir, latest["checkpoint_id"], 0
        while not self.db.execute("SELECT 1 FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone():
            manifest = _read_json(root / "checkpoints" / "v4" / "manifests" / f"{checkpoint_id}.json")
            if manifest is None:
                raise FileNotFoundError(f"source_missing: manifest {checkpoint_id}")
            self.db.execute("INSERT INTO pending_checkpoints VALUES (?,?,?)", (position, str(root), checkpoint_id))
            position += 1
            parent = manifest.get("parent_checkpoint_id")
            if parent:
                checkpoint_id = parent
                continue
            base = (manifest.get("root_metadata") or {}).get("base_checkpoint")
            if not base:
                break
            root, checkpoint_id = (root / base["source_root"]).resolve(), base["checkpoint_id"]
        self.db.commit()
        for pending in self.db.execute("SELECT root,checkpoint_id FROM pending_checkpoints ORDER BY position DESC"):
            root = Path(pending["root"])
            manifest = _read_json(root / "checkpoints" / "v4" / "manifests" / f"{pending['checkpoint_id']}.json")
            component = root / manifest["replacement_file"]
            checkpoint_id = manifest["checkpoint_id"]
            self._require_source(component)
            with self.db:
                version = self.db.execute("SELECT coalesce(max(version),0)+1 FROM checkpoints").fetchone()[0]
                self.db.execute("INSERT OR IGNORE INTO sources(path) VALUES (?)", (str(component),))
                source_id = self.db.execute("SELECT id FROM sources WHERE path=?", (str(component),)).fetchone()[0]
                scope_cache = {}
                for meta in iter_metadata(component):
                    path = meta["path"]
                    operation = meta["operation"]
                    if operation == "map_create":
                        path = [*path, meta["id"]]
                    elif operation == "append":
                        key = _key(path)
                        row = self.db.execute("SELECT count FROM lengths WHERE path_key=?", (key,)).fetchone()
                        offset = row[0] if row else 0
                        self.db.execute("INSERT INTO lengths VALUES (?,?) ON CONFLICT(path_key) DO UPDATE SET count=excluded.count", (key, offset + 1))
                        path = [*path, offset]
                    key = _key(path)
                    for depth in range(1, len(path)):
                        ancestor = self.db.execute("SELECT structural FROM entries WHERE path_key=? AND end IS NULL", (_key(path[:depth]),)).fetchone()
                        if ancestor and not ancestor[0]:
                            raise ValueError("unsupported_operation: nested write inside atomic entry " + _json(path[:depth]))
                    previous = self.db.execute("SELECT ordinal FROM entries WHERE path_key=? AND end IS NULL", (key,)).fetchone()
                    ordinal = previous[0] if previous else self.db.execute("SELECT coalesce(max(ordinal),-1)+1 FROM entries").fetchone()[0]
                    # 替换父节点时结束旧子项版本，旧检查点仍保留。
                    expired = self.db.execute("UPDATE entries INDEXED BY entries_current SET end=? WHERE path_key>=? AND path_key<? AND end IS NULL RETURNING rowid", (version, key, key + "\U0010ffff"))
                    for old in expired:
                        self.db.execute("UPDATE scopes INDEXED BY scopes_current_entries SET end=? WHERE entry_id=? AND end IS NULL", (version, old[0]))
                    if operation in {"delete", "set"}:
                        self.db.execute("DELETE FROM lengths WHERE path_key>=? AND path_key<?", (key, key + "\U0010ffff"))
                    if operation == "delete":
                        continue
                    structural = int(meta.get("value_kind") in {"map", "list"} and meta.get("value_empty", False))
                    self.db.execute("INSERT INTO entries VALUES (?,?,?,NULL,?,?,?,?,?,?) ON CONFLICT(path_key,start) DO UPDATE SET source=excluded.source,sequence=excluded.sequence,raw_bytes=excluded.raw_bytes,operation=excluded.operation,structural=excluded.structural,ordinal=excluded.ordinal,end=NULL", (key, _json(path), version, source_id, meta["sequence"], meta["raw_bytes"], operation, ordinal, structural))
                    entry_id = self.db.execute("SELECT rowid FROM entries WHERE path_key=? AND start=?", (key, version)).fetchone()[0]
                    for depth in range(len(path) + 1):
                        scope_key = _key(path[:depth])
                        scope_id = scope_cache.get(scope_key)
                        if scope_id is None:
                            self.db.execute("INSERT OR IGNORE INTO scope_names(path) VALUES (?)", (scope_key,))
                            scope_id = self.db.execute("SELECT id FROM scope_names WHERE path=?", (scope_key,)).fetchone()[0]
                            if len(scope_cache) >= 256:
                                scope_cache.pop(next(iter(scope_cache)))
                            scope_cache[scope_key] = scope_id
                        self.db.execute("INSERT INTO scopes VALUES (?,?,?,NULL,?) ON CONFLICT(scope,entry_id,start) DO UPDATE SET end=NULL,ordinal=excluded.ordinal", (scope_id, entry_id, version, ordinal))
                for name, reference in (manifest.get("annotations") or {}).items():
                    if name.startswith("dataset:"):
                        self.db.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?)", (reference["path"], _json(reference), checkpoint_id, str(root)))
                for tid, reference in (manifest.get("thread_manifest") or {}).get("threads", {}).items():
                    self.db.execute("INSERT OR REPLACE INTO threads VALUES (?,?,?,?)", (tid, version, str(root), _json(reference)))
                marker = latest if checkpoint_id == latest["checkpoint_id"] else {
                    "checkpoint_id": checkpoint_id, "step": manifest["step"], "run_id": manifest.get("run_id"), "branch_id": self.branch_id}
                self.db.execute("INSERT INTO checkpoints VALUES (?,?,?,?)", (checkpoint_id, version, manifest["step"], _json(marker)))
        return self.status()

    def state_page(self, checkpoint_id=None, path=(), *, cursor=None, limit=100, max_bytes=1048576):
        if limit < 1 or max_bytes < 512:
            raise ValueError("positive limit and max_bytes >= 512 required")
        status = self.status()
        checkpoint_id = checkpoint_id or (cursor["identity"][2] if cursor else None) or (status["indexed_checkpoint"] or {}).get("checkpoint_id")
        row = self.db.execute("SELECT version FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        if row is None:
            manifest = _read_json(self.base / "manifests" / f"{checkpoint_id}.json") if checkpoint_id else None
            marker = _read_json(self.latest_path.parent / "complete" / f"step_{manifest['step']:06d}.json") if manifest else None
            pending = marker and marker.get("complete") is True and marker.get("checkpoint_id") == checkpoint_id
            raise ValueError("index_pending" if checkpoint_id is None or pending else "checkpoint_not_found")
        version = row[0]
        identity = [status["run_id"], self.branch_id, checkpoint_id, list(path)]
        if cursor and cursor.get("identity") != identity:
            raise ValueError("cursor_mismatch")
        for depth in range(1, len(path)):
            ancestor = self.db.execute("SELECT structural FROM entries WHERE path_key=? AND start<=? AND (end IS NULL OR end>?)", (_key(path[:depth]), version, version)).fetchone()
            if ancestor and not ancestor[0]:
                raise ValueError("unsupported_path: nearest readable ancestor " + _json(path[:depth]))
        prefix = _key(path)
        scope = self.db.execute("SELECT id FROM scope_names WHERE path=?", (prefix,)).fetchone()
        scope_id = scope[0] if scope else -1
        where = "s.scope=? AND s.start<=? AND (s.end IS NULL OR s.end>?)"
        args = (scope_id, version, version)
        if checkpoint_id == (status["indexed_checkpoint"] or {}).get("checkpoint_id"):
            where = "s.scope=? AND s.end IS NULL"
            args = (scope_id,)
        total = self.db.execute("SELECT count(*) FROM scopes s WHERE " + where, args).fetchone()[0]
        after = cursor["after"] if cursor else -1
        rows = self.db.execute("SELECT e.*,f.path AS source_path FROM scopes s JOIN entries e ON e.rowid=s.entry_id JOIN sources f ON f.id=e.source WHERE " + where + " AND s.ordinal>? ORDER BY s.ordinal LIMIT ?", (*args, after, limit + 1)).fetchall()
        items, used = [], 2
        for record in rows[:limit]:
            ref = {"kind": "checkpoint_record", "source": record["source_path"], "sequence": record["sequence"], "run_id": status["run_id"]}
            item = {"path": json.loads(record["path"]), "operation": "set", "content_ref": ref, "raw_bytes": record["raw_bytes"]}
            self._require_source(record["source_path"])
            if record["raw_bytes"] < min(65536, max_bytes // 2):
                from .checkpoint_records import iter_record_bytes
                body = json.loads(b"".join(iter_record_bytes(record["source_path"], record["sequence"])))
                item["value"] = body.get("value")
            cost = len(_json(item).encode()) + (1 if items else 0)
            if used + cost > max_bytes:
                if not items:
                    raise ValueError("max_bytes_too_small_for_reference")
                break
            items.append(item)
            used += cost
            after = record["ordinal"]
        more = bool(rows and rows[-1]["ordinal"] > after)
        return {"items": items, "total": total, "next_cursor": {"identity": identity, "after": after} if more else None,
                "checkpoint_id": checkpoint_id, "committed_checkpoint": status["committed_checkpoint"], "indexed_checkpoint": status["indexed_checkpoint"], "bytes": used}

    def thread_page(self, thread_id, *, checkpoint_id=None, cursor=None, limit=100, max_bytes=1048576):
        from .agent.thread_store import AgentThreadStore
        root, boundary = self.run_dir, None
        identity = [self.status()["run_id"], thread_id, checkpoint_id]
        if cursor and cursor.get("identity") != identity:
            raise ValueError("cursor_mismatch")
        inner = cursor["position"] if cursor else None
        if checkpoint_id is not None:
            checkpoint = self.db.execute("SELECT version FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
            if checkpoint is None:
                raise ValueError("index_pending")
            row = self.db.execute("SELECT root,reference FROM threads WHERE id=? AND version<=? ORDER BY version DESC LIMIT 1", (thread_id, checkpoint[0])).fetchone()
            if row is None:
                raise ValueError("thread_not_committed")
            root, boundary = Path(row[0]), json.loads(row[1])
            if inner and (inner["boundary"]["end_offset"] != boundary["cursor"]["byte_offset"]):
                raise ValueError("cursor_mismatch")
        result = AgentThreadStore(root, create=False).read_event_page(thread_id, cursor=inner, max_records=limit, max_bytes=max_bytes, boundary=boundary if inner is None else None)
        if checkpoint_id is None:
            result["durable_through"] = None
        for event in result["events"]:
            for key in ("event_ref", "payload_ref"):
                if isinstance(event.get(key), dict):
                    event[key]["run_dir"] = str(root)
        bounded, used = [], 2
        for event in result["events"]:
            size = len(_json(event).encode()) + (1 if bounded else 0)
            if used + size > max_bytes:
                if not bounded:
                    raise ValueError("max_bytes_too_small_for_reference")
                result["next_cursor"] = {"version": 1, "run_dir": str(root), "thread_id": thread_id,
                    "path": result["boundary"]["path"], "sequence": bounded[-1]["sequence"], "boundary": result["boundary"]}
                break
            bounded.append(event)
            used += size
        result["events"] = bounded
        result["bytes"] = used
        result["checkpoint_id"] = checkpoint_id
        if result["next_cursor"]:
            result["next_cursor"] = {"identity": identity, "position": result["next_cursor"]}
        return result

    def _dataset_root(self, reference):
        root = self.run_dir
        if reference.get("publication") != "run_diagnostics":
            registered = self.db.execute("SELECT reference,root FROM datasets WHERE path=?", (reference["path"],)).fetchone()
            if registered is None or json.loads(registered[0]) != reference:
                raise ValueError("dataset_not_committed")
            root = Path(registered[1])
        self._require_source(root / reference["path"])
        return root

    def dataset_page(self, reference, *, cursor=None, limit=100, max_bytes=1048576):
        from .result_datasets import read_dataset_page
        status = self.status()
        root = self._dataset_root(reference)
        identity = [status["run_id"], reference["path"]]
        if cursor and cursor.get("identity") != identity:
            raise ValueError("cursor_mismatch")
        self._require_source(root / reference["path"])
        page = read_dataset_page(root, reference, after_sequence=cursor["sequence"] if cursor else -1, limit=limit, max_bytes=max_bytes)
        for record in page["records"]:
            if "record_ref" in record:
                record["record_ref"].update(kind="dataset_record", dataset=reference, run_id=status["run_id"])
        bounded, used = [], 2
        for record in page["records"]:
            size = len(_json(record).encode()) + (1 if bounded else 0)
            if used + size > max_bytes:
                if not bounded:
                    raise ValueError("max_bytes_too_small_for_reference")
                page["next_sequence"] = bounded[-1]["sequence"]
                break
            bounded.append(record)
            used += size
        page["records"] = bounded
        page["payload_bytes"] = used
        page["next_cursor"] = {"identity": identity, "sequence": page["next_sequence"]} if page["next_sequence"] is not None else None
        return page

    def read_content(self, ref, *, offset=0, max_bytes=1048576):
        if offset < 0 or max_bytes < 1:
            raise ValueError("invalid content range")
        if ref.get("kind") == "dataset_record":
            from .result_datasets import _dataset_path
            from .checkpoint_records import iter_record_bytes
            if ref.get("run_id") != self.status()["run_id"]:
                raise ValueError("cursor_mismatch")
            source = _dataset_path(self._dataset_root(ref["dataset"]), ref["dataset"])
            from .checkpoint_records import iter_metadata
            row = next(iter_metadata(source, after_sequence=ref["sequence"]-1))
            data = b"".join(iter_record_bytes(source, ref["sequence"], offset=offset, max_bytes=max_bytes))
            total = row["raw_bytes"]
            return {"data": data, "total_bytes": total, "next_offset": offset + len(data) if offset + len(data) < total else None}
        if ref.get("kind") != "checkpoint_record":
            from .agent.thread_store import AgentThreadStore
            root = Path(ref.get("run_dir", self.run_dir)).resolve()
            if root != self.run_dir and not self.db.execute("SELECT 1 FROM threads WHERE root=? LIMIT 1", (str(root),)).fetchone():
                raise ValueError("unknown_content_ref")
            return AgentThreadStore(root, create=False).read_content(ref, offset=offset, max_bytes=max_bytes)
        if ref.get("run_id") != self.status()["run_id"]:
            raise ValueError("cursor_mismatch")
        source, sequence = ref["source"], ref["sequence"]
        self._require_source(source)
        row = self.db.execute("SELECT e.raw_bytes FROM entries e JOIN sources f ON f.id=e.source WHERE f.path=? AND e.sequence=? LIMIT 1", (source, sequence)).fetchone()
        if row is None:
            raise ValueError("unknown_content_ref")
        if offset > row[0]:
            raise ValueError("invalid content range")
        from .checkpoint_records import iter_record_bytes
        data = b"".join(iter_record_bytes(source, sequence, offset=offset, max_bytes=max_bytes))
        return {"data": data, "total_bytes": row[0], "next_offset": offset + len(data) if offset + len(data) < row[0] else None}


def _request(reader, method, params):
    allowed = {"status", "watch", "sync", "state_page", "thread_page", "dataset_page", "read_content"}
    if method not in allowed:
        raise ValueError("method_not_found")
    result = getattr(reader, method)(**params)
    if isinstance(result.get("data"), bytes):
        result = {**result, "data": base64.b64encode(result["data"]).decode(), "encoding": "base64"}
    return result


def main():
    parser = argparse.ArgumentParser(description="Read a Society0 run online or offline")
    parser.add_argument("run_dir")
    parser.add_argument("--index-dir")
    parser.add_argument("--method", default="status")
    parser.add_argument("--rebuild-index", action="store_true")
    parser.add_argument("--params", default="{}")
    parser.add_argument("--serve", type=int, metavar="PORT")
    args = parser.parse_args()
    with ObservationReader(args.run_dir, index_dir=args.index_dir, rebuild=args.rebuild_index) as reader:
        if args.serve is None:
            print(_json(_request(reader, args.method, json.loads(args.params))))
            return
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading
        stop = threading.Event()
        def index_loop():
            with ObservationReader(reader.run_dir, index_dir=reader.index_dir, branch_id=reader.branch_id) as indexer:
                while not stop.is_set():
                    try:
                        indexer.sync()
                        reader.index_error = None
                    except (OSError, ValueError, sqlite3.Error) as exc:
                        reader.index_error = str(exc)
                    stop.wait(.5)
        worker = threading.Thread(target=index_loop, daemon=True)
        worker.start()
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1048576:
                        raise ValueError("invalid_request_size")
                    request = json.loads(self.rfile.read(length))
                    method = request["method"]
                    result = {"result": reader.status() if method == "sync" else _request(reader, method, request.get("params", {}))}
                    code = 200
                except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
                    result, code = {"error": str(exc)}, 400
                payload = _json(result).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args):
                pass
        server = HTTPServer(("127.0.0.1", args.serve), Handler)
        try:
            server.serve_forever()
        finally:
            stop.set()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    main()
