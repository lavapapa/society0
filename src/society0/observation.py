"""运行中和离线共用的只读查询；索引保存在独立本地目录。"""
from __future__ import annotations

import argparse
import base64
import json
import heapq
import zlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from typing import Any


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _pack_part(value):
    return _pack_json(_json(value))


def _pack_json(value):
    raw = value.encode()
    if len(raw) > 96:
        packed = zlib.compress(raw, 1)
        if len(packed) < len(raw):
            return b'z' + packed
    return b'j' + raw


def _unpack_part(value):
    return json.loads(zlib.decompress(value[1:]) if value[:1] == b'z' else value[1:])


def _same_identity(left, right):
    return json.dumps(left, sort_keys=True, ensure_ascii=False) == json.dumps(right, sort_keys=True, ensure_ascii=False)


def _record_content(source, sequence, offset, max_bytes):
    from .checkpoint_records import RecordReader
    if type(sequence) is not int:
        raise ValueError("invalid_content_ref")
    with RecordReader(source) as reader:
        try:
            total = reader.raw_bytes(sequence)
        except KeyError:
            raise ValueError("unknown_content_ref") from None
        if offset > total:
            raise ValueError("invalid content range")
        data = b"".join(reader.iter_bytes(sequence, offset=offset, max_bytes=max_bytes))
    return {"data": data, "total_bytes": total, "next_offset": offset + len(data) if offset + len(data) < total else None}


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

    def __init__(self, run_dir, *, index_dir=None, branch_id="main", rebuild=False, readonly_index=False):
        if readonly_index and rebuild:
            raise ValueError("readonly index cannot be rebuilt")
        self.run_dir = Path(run_dir).resolve()
        if not self.run_dir.is_dir():
            raise FileNotFoundError(self.run_dir)
        self.branch_id = branch_id
        self.base = self.run_dir / "checkpoints" / "v4"
        self.latest_path = (self.base / "latest.json" if branch_id == "main" else
                            self.base / "branches" / branch_id / "latest.json")
        self._temporary = tempfile.TemporaryDirectory(prefix="society0-observation-") if index_dir is None else None
        directory = Path(self._temporary.name if self._temporary else index_dir)
        self._readonly_index = readonly_index
        if not readonly_index:
            directory.mkdir(parents=True, exist_ok=True)
        self.index_dir = directory
        if rebuild:
            (directory / "observation.sqlite").unlink(missing_ok=True)
        self.db = sqlite3.connect((directory / "observation.sqlite").resolve().as_uri() + "?mode=ro", uri=True) if readonly_index else sqlite3.connect(directory / "observation.sqlite")
        self.db.row_factory = sqlite3.Row
        existing = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='entries'").fetchone()
        if existing and self.db.execute("PRAGMA user_version").fetchone()[0] != 3:
            self.db.close()
            raise ValueError("index_format_mismatch: rebuild the derived index with --rebuild-index")
        if readonly_index:
            self.db.execute("PRAGMA cache_size=-4096")
            source = self.db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
            codec = self.db.execute("SELECT value FROM meta WHERE key='key_codec'").fetchone()
            if not source or source[0] != _json([str(self.run_dir), branch_id]):
                self.db.close()
                raise ValueError("index_source_mismatch")
            if not codec or codec[0] != "zlib-1:" + zlib.ZLIB_RUNTIME_VERSION:
                self.db.close()
                raise ValueError("index_format_mismatch: rebuild with --rebuild-index")
            return
        self.db.execute("PRAGMA user_version=3")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA cache_size=-4096")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, version INTEGER UNIQUE, step INTEGER, marker TEXT);
            CREATE TABLE IF NOT EXISTS entries(path_id INTEGER,start INTEGER,end INTEGER,
                source INTEGER,sequence INTEGER,raw_bytes INTEGER,ordinal INTEGER,structural INTEGER,
                PRIMARY KEY(path_id,start));
            CREATE TABLE IF NOT EXISTS sources(id INTEGER PRIMARY KEY,path TEXT UNIQUE);
            CREATE TABLE IF NOT EXISTS parts(id INTEGER PRIMARY KEY,value BLOB UNIQUE);
            CREATE TABLE IF NOT EXISTS paths(id INTEGER PRIMARY KEY,parent INTEGER,part INTEGER,UNIQUE(parent,part));
            INSERT OR IGNORE INTO paths VALUES (0,-1,0);
            CREATE INDEX IF NOT EXISTS entries_current ON entries(path_id) WHERE end IS NULL;
            CREATE INDEX IF NOT EXISTS entries_ordinal ON entries(ordinal);
            CREATE INDEX IF NOT EXISTS entries_current_ordinal ON entries(ordinal) WHERE end IS NULL;
            CREATE TABLE IF NOT EXISTS scope_ancestors(ancestor INTEGER,scope INTEGER,PRIMARY KEY(ancestor,scope)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS current_scope_ancestors(ancestor INTEGER,scope INTEGER,PRIMARY KEY(ancestor,scope)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS current_scope_members ON current_scope_ancestors(scope,ancestor);
            CREATE TABLE IF NOT EXISTS scopes(scope INTEGER,ordinal INTEGER,start INTEGER,end INTEGER,entry_id INTEGER,
                PRIMARY KEY(scope,ordinal,start)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS scopes_current ON scopes(scope,ordinal) WHERE end IS NULL;
            CREATE TABLE IF NOT EXISTS prepared_rows(ordinal INTEGER PRIMARY KEY,entry_id INTEGER);
            CREATE TABLE IF NOT EXISTS counts(scope INTEGER,version INTEGER,total INTEGER,
                PRIMARY KEY(scope,version)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS datasets(path TEXT PRIMARY KEY, reference TEXT, checkpoint_id TEXT, root TEXT);
            CREATE TABLE IF NOT EXISTS threads(id TEXT, version INTEGER, root TEXT, reference TEXT, PRIMARY KEY(id,version));
            CREATE TABLE IF NOT EXISTS lengths(path_key TEXT PRIMARY KEY, count INTEGER);
        """)
        codec = "zlib-1:" + zlib.ZLIB_RUNTIME_VERSION
        saved_codec = self.db.execute("SELECT value FROM meta WHERE key='key_codec'").fetchone()
        if saved_codec and saved_codec[0] != codec:
            self.db.close()
            raise ValueError("index_format_mismatch: key codec changed; rebuild with --rebuild-index")
        self.db.execute("INSERT OR IGNORE INTO meta VALUES ('key_codec',?)", (codec,))
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
        if saved is None and not self._readonly_index:
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
        if cursor and not _same_identity(cursor.get("run_id"), position["run_id"]):
            raise ValueError("cursor_mismatch")
        item = {key: status.get(key) for key in ("run_id", "phase", "executing_step", "last_completed_step", "observed_at")}
        item.update(committed_checkpoint=position["committed"], indexed_checkpoint=position["indexed"])
        return {"items": [] if _same_identity(cursor, position) else [item], "next_cursor": position}

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
                path_cache = {(): (0,)}
                ancestor_cache = {}
                active_scope_cache = set()
                lengths_exist = self.db.execute("SELECT 1 FROM lengths LIMIT 1").fetchone() is not None
                self.db.execute("CREATE TEMP TABLE IF NOT EXISTS count_changes(scope INTEGER PRIMARY KEY,change INTEGER)")
                self.db.execute("DELETE FROM count_changes")
                count_changes = {}
                def flush_counts():
                    self.db.executemany("INSERT INTO count_changes VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET change=change+excluded.change", count_changes.items())
                    count_changes.clear()
                def count_change(scope, amount):
                    count_changes[scope] = count_changes.get(scope,0)+amount
                    if len(count_changes) >= 256:
                        flush_counts()
                next_ordinal = self.db.execute("SELECT coalesce(max(ordinal),-1)+1 FROM entries").fetchone()[0]
                def identify(path, identity=None):
                    if not path:
                        return (0,)
                    if identity is None:
                        identity = tuple(_json(part) for part in path)
                    known = path_cache.get(identity)
                    if known is not None:
                        return known
                    parent_ids = identify(path[:-1], identity[:-1])
                    parent = parent_ids[-1]
                    part = _pack_json(identity[-1])
                    part_row = self.db.execute("INSERT OR IGNORE INTO parts(value) VALUES (?) RETURNING id", (part,)).fetchone()
                    part_id = part_row[0] if part_row else self.db.execute("SELECT id FROM parts WHERE value=?", (part,)).fetchone()[0]
                    node_row = self.db.execute("INSERT OR IGNORE INTO paths(parent,part) VALUES (?,?) RETURNING id", (parent,part_id)).fetchone()
                    node = node_row[0] if node_row else self.db.execute("SELECT id FROM paths WHERE parent=? AND part=?", (parent,part_id)).fetchone()[0]
                    result = (*parent_ids, node)
                    cache_bytes = sum(4*len(part)+64 for part in identity)+64*len(result)
                    if cache_bytes <= 4096:
                        if len(path_cache) >= 256:
                            path_cache.pop(next(iter(path_cache)))
                        path_cache[identity] = result
                    return result
                for meta in iter_metadata(component):
                    path = meta["path"]
                    operation = meta["operation"]
                    if operation == "map_create":
                        path = [*path, meta["id"]]
                    elif operation == "append":
                        lengths_exist = True
                        key = _key(path)
                        row = self.db.execute("SELECT count FROM lengths WHERE path_key=?", (key,)).fetchone()
                        offset = row[0] if row else 0
                        self.db.execute("INSERT INTO lengths VALUES (?,?) ON CONFLICT(path_key) DO UPDATE SET count=excluded.count", (key, offset + 1))
                        path = [*path, offset]
                    ids = identify(path)
                    node = ids[-1]
                    for ancestor_id in ids[1:-1]:
                        if ancestor_id not in ancestor_cache:
                            if len(ancestor_cache) >= 256:
                                ancestor_cache.pop(next(iter(ancestor_cache)))
                            ancestor_cache[ancestor_id] = self.db.execute("SELECT structural FROM entries INDEXED BY entries_current WHERE path_id=? AND end IS NULL", (ancestor_id,)).fetchone()
                        ancestor = ancestor_cache[ancestor_id]
                        if ancestor and not ancestor[0]:
                            raise ValueError("unsupported_operation: nested write inside atomic entry " + _json(path))
                    previous = self.db.execute("SELECT ordinal FROM entries INDEXED BY entries_current WHERE path_id=? AND end IS NULL", (node,)).fetchone()
                    ordinal = previous[0] if previous else next_ordinal
                    if previous is None:
                        next_ordinal += 1
                    # 当前 scope 的索引直接给出被替换的活跃后代，不扫历史版本。
                    expired_query = "SELECT e.rowid,e.path_id,e.ordinal,e.start FROM current_scope_ancestors a JOIN scopes s INDEXED BY scopes_current ON s.scope=a.scope JOIN entries e ON e.rowid=s.entry_id WHERE a.ancestor=? AND s.end IS NULL UNION ALL SELECT rowid,path_id,ordinal,start FROM entries INDEXED BY entries_current WHERE path_id=? AND end IS NULL"
                    cursor = self.db.execute(expired_query, (node,node))
                    expired = cursor.fetchmany(256)
                    cursor.close()
                    if len(expired) == 256:
                        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS expired_entries(rowid INTEGER PRIMARY KEY,path_id INTEGER,ordinal INTEGER,start INTEGER)")
                        self.db.execute("DELETE FROM expired_entries")
                        self.db.execute("INSERT INTO expired_entries " + expired_query, (node,node))
                        expired = self.db.execute("SELECT * FROM expired_entries")
                    if expired:
                        self.db.execute("CREATE TEMP TABLE IF NOT EXISTS expired_scopes(scope INTEGER PRIMARY KEY)")
                        self.db.execute("DELETE FROM expired_scopes")
                        for old in expired:
                            self.db.execute("UPDATE entries SET end=? WHERE rowid=?", (version,old['rowid']))
                            ancestor_cache.pop(old['path_id'], None)
                            old_ids = ids if old['path_id'] == node else self._path_ids(old['path_id'])
                            direct = old_ids[-2]
                            active_scope_cache.discard(direct)
                            self.db.execute("UPDATE scopes SET end=? WHERE scope=? AND ordinal=? AND start=?", (version,direct,old['ordinal'],old['start']))
                            self.db.execute("INSERT OR IGNORE INTO expired_scopes VALUES (?)", (direct,))
                            for scope in old_ids:
                                count_change(scope,-1)
                        self.db.execute("DELETE FROM current_scope_ancestors WHERE scope IN (SELECT scope FROM expired_scopes x WHERE NOT EXISTS (SELECT 1 FROM scopes s INDEXED BY scopes_current WHERE s.scope=x.scope AND s.end IS NULL))")
                    if lengths_exist and operation in {"delete", "set"}:
                        key = _key(path)
                        self.db.execute("DELETE FROM lengths WHERE path_key>=? AND path_key<?", (key, key + "\U0010ffff"))
                    if operation == "delete":
                        continue
                    structural = int(meta.get("value_kind") in {"map", "list"} and meta.get("value_empty", False))
                    self.db.execute("INSERT INTO entries VALUES (?,?,NULL,?,?,?,?,?) ON CONFLICT(path_id,start) DO UPDATE SET source=excluded.source,sequence=excluded.sequence,raw_bytes=excluded.raw_bytes,structural=excluded.structural,ordinal=excluded.ordinal,end=NULL", (node,version,source_id,meta['sequence'],meta['raw_bytes'],ordinal,structural))
                    ancestor_cache.pop(node, None)
                    entry_id = self.db.execute("SELECT rowid FROM entries WHERE path_id=? AND start=?", (node,version)).fetchone()[0]
                    direct = ids[-2]
                    if direct not in active_scope_cache:
                        active = self.db.execute("SELECT 1 FROM scopes INDEXED BY scopes_current WHERE scope=? AND end IS NULL LIMIT 1", (direct,)).fetchone()
                        if active is None:
                            self.db.executemany("INSERT OR IGNORE INTO scope_ancestors VALUES (?,?)", ((ancestor,direct) for ancestor in ids[:-1]))
                            self.db.executemany("INSERT OR IGNORE INTO current_scope_ancestors VALUES (?,?)", ((ancestor,direct) for ancestor in ids[:-1]))
                        if len(active_scope_cache) >= 256:
                            active_scope_cache.pop()
                        active_scope_cache.add(direct)
                    self.db.execute("INSERT INTO scopes VALUES (?,?,?,NULL,?) ON CONFLICT(scope,ordinal,start) DO UPDATE SET end=NULL,entry_id=excluded.entry_id", (direct,ordinal,version,entry_id))
                    for scope in ids:
                        count_change(scope,1)
                # 每个 scope 每个 epoch 只登记一次总数；历史页按版本 seek。
                flush_counts()
                self.db.execute("INSERT INTO counts SELECT scope,?,coalesce((SELECT total FROM counts WHERE counts.scope=c.scope ORDER BY version DESC LIMIT 1),0)+change FROM count_changes c WHERE change!=0", (version,))
                for name, reference in (manifest.get("annotations") or {}).items():
                    if name.startswith("dataset:"):
                        self.db.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?,?)", (reference["path"], _json(reference), checkpoint_id, str(root)))
                for tid, reference in (manifest.get("thread_manifest") or {}).get("threads", {}).items():
                    self.db.execute("INSERT OR REPLACE INTO threads VALUES (?,?,?,?)", (tid, version, str(root), _json(reference)))
                marker = latest if checkpoint_id == latest["checkpoint_id"] else {
                    "checkpoint_id": checkpoint_id, "step": manifest["step"], "run_id": manifest.get("run_id"), "branch_id": self.branch_id}
                self.db.execute("INSERT INTO checkpoints VALUES (?,?,?,?)", (checkpoint_id, version, manifest["step"], _json(marker)))
        return self.status()

    def _path_ids(self, node):
        ids = [node]
        while node:
            node = self.db.execute("SELECT parent FROM paths WHERE id=?", (node,)).fetchone()[0]
            ids.append(node)
        return tuple(reversed(ids))

    def _path(self, node):
        parts = []
        while node:
            row = self.db.execute("SELECT p.parent,v.value AS part FROM paths p JOIN parts v ON v.id=p.part WHERE p.id=?", (node,)).fetchone()
            parts.append(_unpack_part(row['part']))
            node = row['parent']
        return list(reversed(parts))

    def prepared_state(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='prepared_state'").fetchone()
        result = json.loads(row[0]) if row else {"state": "absent"}
        attempt = getattr(self, "_preparation_status", None)
        return {**result, "request": attempt} if attempt else result

    def clear_prepared_state(self):
        with self.db:
            self.db.execute("DELETE FROM prepared_rows")
            self.db.execute("DELETE FROM meta WHERE key='prepared_state'")
        return {"state": "absent"}

    def prepare_state(self, checkpoint_id, path=()):
        """显式一次构建旧版 scope 的磁盘投影；原子替换唯一一个准备结果。"""
        self.status()
        checkpoint = self.db.execute("SELECT version FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
        if checkpoint is None:
            raise ValueError("index_pending: checkpoint must be indexed before prepare_state")
        version = checkpoint[0]
        node = self._find_path(path)
        count = self.db.execute("SELECT total FROM counts WHERE scope=? AND version<=? ORDER BY version DESC LIMIT 1", (node,version)).fetchone()
        total = count[0] if count else 0
        result = {"state":"ready", "checkpoint_id":checkpoint_id,"path":list(path),"version":version,"scope":node,"total":total}
        with self.db:
            self.db.execute("DELETE FROM prepared_rows")
            rows = self.db.execute("SELECT s.ordinal,s.entry_id FROM scope_ancestors a JOIN scopes s ON s.scope=a.scope WHERE a.ancestor=? AND s.start<=? AND (s.end IS NULL OR s.end>?) UNION ALL SELECT ordinal,rowid FROM entries WHERE path_id=? AND start<=? AND (end IS NULL OR end>?)", (node,version,version,node,version,version))
            processed = 0
            for row in rows:
                self.db.execute("INSERT INTO prepared_rows VALUES (?,?)", tuple(row))
                processed += 1
                callback = getattr(self, "_prepare_progress", None)
                if callback and processed % 1000 == 0:
                    callback({**result,"state":"preparing","prepared_entries":processed})
            self.db.execute("INSERT OR REPLACE INTO meta VALUES ('prepared_state',?)", (_json(result),))
        return result

    def _page_rows(self, node, version, after, limit, current, checkpoint_id, path):
        if current and node == 0:
            yield from self.db.execute("SELECT e.*,f.path AS source_path FROM entries e INDEXED BY entries_current_ordinal JOIN sources f ON f.id=e.source WHERE e.end IS NULL AND e.ordinal>? ORDER BY e.ordinal LIMIT ?", (after,limit+1))
            return
        table = "current_scope_ancestors" if current else "scope_ancestors"
        scopes = self.db.execute(f"SELECT scope FROM {table} WHERE ancestor=? LIMIT 257", (node,)).fetchall()
        def pending():
            raise ValueError(_json({"code":"index_pending", "method":"prepare_state", "checkpoint_id":checkpoint_id,"path":list(path)}))
        if len(scopes) > 128:
            pending()
        probes = len(scopes)
        def historical(scope):
            nonlocal probes
            position = after
            while True:
                if probes >= 256:
                    pending()
                probes += 1
                found = self.db.execute("SELECT ordinal FROM scopes WHERE scope=? AND ordinal>? ORDER BY ordinal LIMIT 1", (scope,position)).fetchone()
                if found is None:
                    return
                position = found[0]
                member = self.db.execute("SELECT entry_id,end FROM scopes WHERE scope=? AND ordinal=? AND start<=? ORDER BY start DESC LIMIT 1", (scope,position,version)).fetchone()
                if member and (member['end'] is None or member['end'] > version):
                    yield position, member['entry_id']
        streams = []
        for (scope,) in scopes:
            if current:
                streams.append(self.db.execute("SELECT ordinal,entry_id FROM scopes INDEXED BY scopes_current WHERE scope=? AND end IS NULL AND ordinal>? ORDER BY ordinal LIMIT ?", (scope,after,limit+1)))
            else:
                streams.append(historical(scope))
        own = self.db.execute("SELECT ordinal,rowid,end FROM entries WHERE path_id=? AND start<=? ORDER BY start DESC LIMIT 1", (node,version)).fetchone()
        if own and own['ordinal'] > after and (own['end'] is None or own['end'] > version):
            streams.append(iter([(own['ordinal'],own['rowid'])]))
        for i, (_,entry_id) in enumerate(heapq.merge(*streams, key=lambda item:item[0])):
            if i >= limit+1:
                break
            yield self.db.execute("SELECT e.*,f.path AS source_path FROM entries e JOIN sources f ON f.id=e.source WHERE e.rowid=?", (entry_id,)).fetchone()

    def _child(self, parent, part):
        return self.db.execute("SELECT id FROM paths WHERE parent=? AND part=(SELECT id FROM parts WHERE value=?)", (parent, _pack_part(part))).fetchone()

    def _find_path(self, path):
        node = 0
        for part in path:
            found = self._child(node, part)
            if found is None:
                return -1
            node = found[0]
        return node

    def state_page(self, checkpoint_id=None, path=(), *, cursor=None, limit=100, max_bytes=1048576):
        # 水位、版本计数与行引用共用一个 WAL 读快照。
        self.db.execute("BEGIN")
        try:
            return self._state_page(checkpoint_id,path,cursor=cursor,limit=limit,max_bytes=max_bytes)
        finally:
            self.db.rollback()

    def _state_page(self, checkpoint_id=None, path=(), *, cursor=None, limit=100, max_bytes=1048576):
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
        if cursor and not _same_identity(cursor.get("identity"), identity):
            raise ValueError("cursor_mismatch")
        node = 0
        for depth,part in enumerate(path):
            found = self._child(node, part)
            if found is None:
                node = -1
                break
            node = found[0]
            if depth < len(path)-1:
                ancestor = self.db.execute("SELECT structural,end FROM entries WHERE path_id=? AND start<=? ORDER BY start DESC LIMIT 1", (node,version)).fetchone()
                if ancestor and (ancestor['end'] is None or ancestor['end'] > version) and not ancestor['structural']:
                    raise ValueError("unsupported_path: nearest readable ancestor " + _json(path[:depth+1]))
        count = self.db.execute("SELECT total FROM counts WHERE scope=? AND version<=? ORDER BY version DESC LIMIT 1", (node,version)).fetchone()
        total = count[0] if count else 0
        after = cursor["after"] if cursor else -1
        if _same_identity([(prepared := self.prepared_state()).get('checkpoint_id'), prepared.get('path')], [checkpoint_id, list(path)]):
            rows = self.db.execute("SELECT e.*,f.path AS source_path FROM prepared_rows p JOIN entries e ON e.rowid=p.entry_id JOIN sources f ON f.id=e.source WHERE p.ordinal>? ORDER BY p.ordinal LIMIT ?", (after,limit+1))
        else:
            current = checkpoint_id == (status["indexed_checkpoint"] or {}).get("checkpoint_id")
            rows = self._page_rows(node, version, after, limit, current, checkpoint_id, path)
        items, used, more = [], 2, False
        content_reader, content_source, verified_source = None, None, None
        try:
            for record in rows:
                if len(items) >= limit:
                    more = True
                    break
                ref = {"kind": "checkpoint_record", "source": record["source_path"], "sequence": record["sequence"], "run_id": status["run_id"]}
                item = {"path": self._path(record["path_id"]), "operation": "set", "content_ref": ref, "raw_bytes": record["raw_bytes"]}
                if verified_source != record["source_path"]:
                    self._require_source(record["source_path"])
                    verified_source = record["source_path"]
                if record["raw_bytes"] < min(65536, max_bytes // 2):
                    from .checkpoint_records import RecordReader
                    if content_source != record["source_path"]:
                        if content_reader:
                            content_reader.close()
                        content_reader = None
                        content_reader = RecordReader(record["source_path"])
                        content_source = record["source_path"]
                    body = json.loads(b"".join(content_reader.iter_bytes(record["sequence"])))
                    item["value"] = body.get("value")
                cost = len(_json(item).encode()) + (1 if items else 0)
                if used + cost > max_bytes:
                    if not items:
                        raise ValueError("max_bytes_too_small_for_reference")
                    more = True
                    break
                items.append(item)
                used += cost
                after = record["ordinal"]
            return {"items": items, "total": total, "next_cursor": {"identity": identity, "after": after} if more else None,
                    "checkpoint_id": checkpoint_id, "committed_checkpoint": status["committed_checkpoint"], "indexed_checkpoint": status["indexed_checkpoint"], "bytes": used}
        finally:
            if content_reader:
                content_reader.close()

    def thread_page(self, thread_id, *, checkpoint_id=None, cursor=None, limit=100, max_bytes=1048576):
        from .agent.thread_store import AgentThreadStore
        root, boundary = self.run_dir, None
        identity = [self.status()["run_id"], thread_id, checkpoint_id]
        if cursor and not _same_identity(cursor.get("identity"), identity):
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
            if registered is None or not _same_identity(json.loads(registered[0]), reference):
                raise ValueError("dataset_not_committed")
            root = Path(registered[1])
        self._require_source(root / reference["path"])
        return root

    def dataset_page(self, reference, *, cursor=None, limit=100, max_bytes=1048576):
        from .result_datasets import read_dataset_page
        status = self.status()
        root = self._dataset_root(reference)
        identity = [status["run_id"], reference["path"], reference.get("record_path")]
        if cursor and not _same_identity(cursor.get("identity"), identity):
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
            if not _same_identity(ref.get("run_id"), self.status()["run_id"]):
                raise ValueError("cursor_mismatch")
            source = _dataset_path(self._dataset_root(ref["dataset"]), ref["dataset"])
            return _record_content(source, ref["sequence"], offset, max_bytes)
        if ref.get("kind") != "checkpoint_record":
            from .agent.thread_store import AgentThreadStore
            root = Path(ref.get("run_dir", self.run_dir)).resolve()
            if root != self.run_dir and not self.db.execute("SELECT 1 FROM threads WHERE root=? LIMIT 1", (str(root),)).fetchone():
                raise ValueError("unknown_content_ref")
            return AgentThreadStore(root, create=False).read_content(ref, offset=offset, max_bytes=max_bytes)
        if not _same_identity(ref.get("run_id"), self.status()["run_id"]):
            raise ValueError("cursor_mismatch")
        source, sequence = ref["source"], ref["sequence"]
        self._require_source(source)
        if self.db.execute("SELECT 1 FROM sources WHERE path=?", (source,)).fetchone() is None:
            raise ValueError("unknown_content_ref")
        return _record_content(source, sequence, offset, max_bytes)


def _request(reader, method, params):
    allowed = {"status", "watch", "sync", "state_page", "thread_page", "dataset_page", "read_content", "prepare_state", "prepared_state", "clear_prepared_state"}
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
        from socketserver import ThreadingMixIn
        import threading
        import queue
        preparation = queue.Queue(maxsize=1)
        stop = threading.Event()
        preparation_lock = threading.Lock()
        def preparation_status(value):
            with preparation_lock:
                reader._preparation_status = value
        def index_loop():
            with ObservationReader(reader.run_dir, index_dir=reader.index_dir, branch_id=reader.branch_id) as indexer:
                indexer._prepare_progress = preparation_status
                while not stop.is_set():
                    try:
                        indexer.sync()
                        reader.index_error = None
                    except (OSError, ValueError, sqlite3.Error) as exc:
                        reader.index_error = str(exc)
                    try:
                        method, params = preparation.get_nowait()
                    except queue.Empty:
                        pass
                    else:
                        try:
                            preparation_status({"state":"preparing","method":method,**params})
                            result = getattr(indexer, method)(**params)
                            preparation_status({**result,"method":method})
                        except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
                            preparation_status({"state":"failed","method":method,**params,"error":str(exc)})
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
                    params = request.get("params", {})
                    if method in {"prepare_state", "clear_prepared_state"}:
                        import inspect
                        try:
                            inspect.signature(getattr(reader, method)).bind(**params)
                        except TypeError as exc:
                            raise ValueError(str(exc)) from exc
                        with preparation_lock:
                            attempt = getattr(reader, '_preparation_status', {})
                            if attempt.get('state') in {'queued','preparing'}:
                                raise ValueError("preparation_busy")
                            try:
                                preparation.put_nowait((method,params))
                            except queue.Full:
                                raise ValueError("preparation_busy") from None
                            reader._preparation_status = {"state":"queued","method":method,**params}
                            result = {"result":reader._preparation_status}
                    else:
                        with ObservationReader(reader.run_dir, index_dir=reader.index_dir, branch_id=reader.branch_id, readonly_index=True) as query:
                            query.index_error = getattr(reader, 'index_error', None)
                            with preparation_lock:
                                query._preparation_status = getattr(reader, '_preparation_status', None)
                            result = {"result": query.status() if method == "sync" else _request(query, method, params)}
                    code = 200
                except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
                    detail = str(exc)
                    if detail.startswith('{'):
                        detail = json.loads(detail)
                    result, code = {"error": detail}, 400
                payload = _json(result).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
            def log_message(self, *args):
                pass
        class BoundedServer(ThreadingMixIn, HTTPServer):
            daemon_threads = True
            slots = threading.BoundedSemaphore(4)
            def process_request(self, request, address):
                if not self.slots.acquire(blocking=False):
                    try:
                        request.settimeout(.1)
                        payload = b'{"error":"server_busy"}'
                        request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\nContent-Length: ' + str(len(payload)).encode() + b'\r\n\r\n' + payload)
                    except OSError:
                        pass
                    finally:
                        self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, address)
                except BaseException:
                    self.slots.release()
                    raise
            def process_request_thread(self, request, address):
                try:
                    super().process_request_thread(request, address)
                finally:
                    self.slots.release()
        server = BoundedServer(("127.0.0.1", args.serve), Handler)
        try:
            server.serve_forever()
        finally:
            stop.set()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    main()
