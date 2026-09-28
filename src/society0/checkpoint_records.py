"""不可变检查点记录：逐条、分块编码，查询连接始终只读。"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import shutil
import gzip
from collections.abc import Mapping, MutableSequence
from contextlib import closing
from pathlib import Path

CODEC = 'sqlite_records_v1'
CHUNK = 1048576


def _json_parts(value):
    # 字符串也分块转义，避免 JSONEncoder 对单个巨大字符串产生整段副本。
    if isinstance(value, str):
        yield b'"'
        for start in range(0, len(value), 8192):
            yield json.dumps(value[start:start + 8192], ensure_ascii=False)[1:-1].encode('utf-8')
        yield b'"'
    elif isinstance(value, Mapping):
        yield b'{'
        for index, (key, item) in enumerate(value.items()):
            if index:
                yield b','
            if not isinstance(key, str):
                if key is None:
                    key = "null"
                elif isinstance(key, bool):
                    key = "true" if key else "false"
                elif isinstance(key, (int, float)):
                    key = json.dumps(key, allow_nan=False)
                else:
                    raise TypeError("JSON object key is not a scalar")
            yield from _json_parts(key)
            yield b':'
            yield from _json_parts(item)
        yield b'}'
    elif isinstance(value, (list, tuple, MutableSequence)):
        yield b'['
        for index, item in enumerate(value):
            if index:
                yield b','
            yield from _json_parts(item)
        yield b']'
    else:
        yield json.dumps(value, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _open(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)


def write_records(path: Path, records, *, sequence_offset=0) -> int:
    """在同一事务逐条写入，关闭并 fsync 后原子发布文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    count = 0
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute('PRAGMA journal_mode=DELETE')
            db.execute('PRAGMA synchronous=FULL')
            db.execute('PRAGMA cache_size=-2048')
            db.executescript('''
                CREATE TABLE prefixes(id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL);
                CREATE TABLE paths(id INTEGER PRIMARY KEY, prefix_id INTEGER NOT NULL,
                    leaf TEXT NOT NULL, record_count INTEGER NOT NULL, UNIQUE(prefix_id, leaf));
                CREATE TABLE records(sequence INTEGER PRIMARY KEY, path_id INTEGER NOT NULL,
                    operation TEXT NOT NULL, raw_bytes INTEGER NOT NULL, record_id TEXT,
                    value_kind TEXT NOT NULL, value_empty INTEGER NOT NULL, raw_start INTEGER NOT NULL);
                CREATE INDEX records_path ON records(path_id, sequence);
                CREATE TABLE chunks(raw_start INTEGER PRIMARY KEY, payload BLOB NOT NULL);
                CREATE TABLE totals(record_count INTEGER NOT NULL, raw_bytes INTEGER NOT NULL);
            ''')
            db.execute('BEGIN')
            position = 0
            pending = bytearray()
            prefix_cache = {}
            def emit(raw):
                nonlocal position
                db.execute('INSERT INTO chunks VALUES (?, ?)',
                           (position, gzip.compress(raw, compresslevel=6, mtime=0)))
                position += len(raw)
            for record in records:
                sequence = int(record.get('sequence', count)) + sequence_offset
                record = {**record, 'sequence': sequence}
                start = position + len(pending)
                size = 0
                for raw in _json_parts(record):
                    size += len(raw)
                    pending.extend(raw)
                    while len(pending) >= CHUNK:
                        emit(pending[:CHUNK])
                        del pending[:CHUNK]
                record_path = record['path']
                prefix = json.dumps(record_path[:-1], ensure_ascii=False, separators=(',', ':'))
                leaf = json.dumps(record_path[-1], ensure_ascii=False)
                prefix_id = prefix_cache.get(prefix)
                if prefix_id is None:
                    db.execute('INSERT OR IGNORE INTO prefixes(path) VALUES (?)', (prefix,))
                    prefix_id = db.execute('SELECT id FROM prefixes WHERE path=?', (prefix,)).fetchone()[0]
                    if len(prefix_cache) >= 256:
                        prefix_cache.clear()
                    prefix_cache[prefix] = prefix_id
                path_id = db.execute('''INSERT INTO paths(prefix_id,leaf,record_count) VALUES (?,?,1)
                    ON CONFLICT(prefix_id,leaf) DO UPDATE SET record_count=record_count+1 RETURNING id''',
                    (prefix_id, leaf)).fetchone()[0]
                value = record.get('value')
                value_kind = 'map' if isinstance(value, Mapping) else 'list' if isinstance(value, (list, tuple, MutableSequence)) else 'scalar'
                db.execute('INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                           (sequence, path_id, record['operation'], size,
                            json.dumps(record['id']) if 'id' in record else None,
                            value_kind, value_kind != 'scalar' and len(value) == 0, start))
                count += 1
            if pending:
                emit(pending)
            db.execute('INSERT INTO totals VALUES (?,?)', (count, position))
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return count
    finally:
        temporary.unlink(missing_ok=True)


def _record_bytes(db, sequence, *, offset=0, max_bytes=None, cache=None):
    if offset < 0 or (max_bytes is not None and max_bytes < 0):
        raise ValueError('offset and max_bytes must be non-negative')
    row = db.execute('SELECT raw_bytes, raw_start FROM records WHERE sequence=?', (sequence,)).fetchone()
    if row is None:
        raise KeyError(sequence)
    end = row[1] + (row[0] if max_bytes is None else min(row[0], offset + max_bytes))
    offset += row[1]
    if offset >= end:
        return
    if cache:
        cached_start, cached_raw = next(iter(cache.items()))
        if cached_start <= offset < cached_start + len(cached_raw):
            yield cached_raw[offset-cached_start:min(len(cached_raw), end-cached_start)]
            offset = cached_start + len(cached_raw)
            if offset >= end:
                return
    first = db.execute('SELECT raw_start FROM chunks WHERE raw_start<=? ORDER BY raw_start DESC LIMIT 1',
                       (offset,)).fetchone()
    if first is None:
        raise ValueError('incomplete checkpoint record')
    for start, compressed in db.execute('SELECT raw_start, payload FROM chunks WHERE raw_start>=? AND raw_start<? ORDER BY raw_start',
                                        (first[0], end)):
        raw = cache.get(start) if cache is not None else None
        if raw is None:
            raw = gzip.decompress(compressed)
            if cache is not None:
                cache.clear()
                cache[start] = raw
        yield raw[max(0, offset - start):min(len(raw), end - start)]


def iter_record_bytes(path, sequence, *, offset=0, max_bytes=None):
    with closing(_open(path)) as db:
        yield from _record_bytes(db, sequence, offset=offset, max_bytes=max_bytes)


def iter_records(path):
    with closing(_open(path)) as db:
        cache = {}
        for (sequence,) in db.execute('SELECT sequence FROM records ORDER BY sequence'):
            yield json.loads(b''.join(_record_bytes(db, sequence, cache=cache)))


def read_page(path, *, after_sequence=-1, limit=100, max_bytes=1048576, path_filter=None):
    # path 参数为文件路径；上层 API 使用 path_filter 表示精确业务路径。
    if limit < 1 or max_bytes < 1:
        raise ValueError('limit and max_bytes must be positive')
    with closing(_open(path)) as db:
        args = []
        where = ''
        if path_filter is not None:
            prefix = json.dumps(path_filter[:-1], ensure_ascii=False, separators=(',', ':'))
            leaf = json.dumps(path_filter[-1], ensure_ascii=False)
            found = db.execute('''SELECT paths.id, paths.record_count FROM paths JOIN prefixes
                ON prefixes.id=paths.prefix_id WHERE prefixes.path=? AND paths.leaf=?''', (prefix, leaf)).fetchone()
            if found is None:
                return {'records': [], 'total': 0, 'next_sequence': None, 'payload_bytes': 0}
            args = [found[0]]
            total = found[1]
            where = 'WHERE records.path_id=?'
        else:
            total = db.execute('SELECT record_count FROM totals').fetchone()[0]
        tail = where + (' AND ' if where else ' WHERE ') + 'sequence>? ORDER BY sequence LIMIT ?'
        rows = db.execute('''SELECT sequence,prefixes.path,paths.leaf,operation,raw_bytes FROM records
            JOIN paths ON records.path_id=paths.id JOIN prefixes ON paths.prefix_id=prefixes.id ''' + tail,
            (*args, after_sequence, limit + 1)).fetchall()
        cache = {}
        records = []
        used = 0
        for sequence, prefix, leaf, operation, size in rows[:limit]:
            if size > max_bytes:
                item = {'sequence': sequence, 'path': json.loads(prefix) + [json.loads(leaf)], 'operation': operation,
                        'raw_bytes': size, 'record_ref': {'sequence': sequence}}
                item_bytes = len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
            else:
                item_bytes = size
                item = None
            if used + item_bytes > max_bytes:
                if not records:
                    raise ValueError('max_bytes is too small for the record reference')
                break
            if item is None:
                item = json.loads(b''.join(_record_bytes(db, sequence, cache=cache)))
            records.append(item)
            used += item_bytes
        last = records[-1]['sequence'] if records else after_sequence
        more = bool(rows and rows[-1][0] > last)
        return {'records': records, 'total': total, 'next_sequence': last if more else None,
                'payload_bytes': used}


def iter_metadata(path, *, after_sequence=-1):
    """仅读取索引列，不解压任何记录正文。"""
    with closing(_open(path)) as db:
        for sequence, prefix, leaf, operation, size, record_id, value_kind, value_empty in db.execute(
            '''SELECT sequence,prefixes.path,paths.leaf,operation,raw_bytes,record_id,value_kind,value_empty
            FROM records JOIN paths ON records.path_id=paths.id JOIN prefixes ON paths.prefix_id=prefixes.id
            WHERE sequence>? ORDER BY sequence''', (after_sequence,)):
            record = {'sequence': sequence, 'path': json.loads(prefix) + [json.loads(leaf)],
                      'operation': operation, 'raw_bytes': size,
                      'value_kind': value_kind, 'value_empty': bool(value_empty)}
            if record_id is not None:
                record['id'] = json.loads(record_id)
            yield record


def next_sequence(path):
    with closing(_open(path)) as db:
        return db.execute('SELECT coalesce(max(sequence), -1) + 1 FROM records').fetchone()[0]


def merge_records(path, sources):
    """复制已经压缩的记录页，不解析正文；目标完成前始终为临时文件。"""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        shutil.copyfile(sources[0], temporary)
        with closing(sqlite3.connect(temporary)) as db:
            db.execute('PRAGMA cache_size=-2048')
            for source in sources[1:]:
                db.execute('ATTACH DATABASE ? AS pending', (str(source),))
                with db:
                    offset = db.execute('SELECT raw_bytes FROM totals').fetchone()[0]
                    db.execute('INSERT OR IGNORE INTO prefixes(path) SELECT path FROM pending.prefixes')
                    db.execute('''INSERT INTO paths(prefix_id,leaf,record_count)
                        SELECT main_prefix.id,p.leaf,p.record_count FROM pending.paths p
                        JOIN pending.prefixes pp ON p.prefix_id=pp.id
                        JOIN prefixes main_prefix ON main_prefix.path=pp.path WHERE 1
                        ON CONFLICT(prefix_id,leaf) DO UPDATE SET record_count=record_count+excluded.record_count''')
                    db.execute('''INSERT INTO records
                        SELECT r.sequence,mp.id,r.operation,r.raw_bytes,r.record_id,r.value_kind,r.value_empty,r.raw_start+?
                        FROM pending.records r JOIN pending.paths p ON p.id=r.path_id
                        JOIN pending.prefixes pp ON pp.id=p.prefix_id
                        JOIN prefixes pref ON pref.path=pp.path JOIN paths mp ON mp.prefix_id=pref.id AND mp.leaf=p.leaf''', (offset,))
                    db.execute('INSERT INTO chunks SELECT raw_start+?,payload FROM pending.chunks', (offset,))
                    db.execute('''UPDATE totals SET record_count=record_count+(SELECT record_count FROM pending.totals),
                        raw_bytes=raw_bytes+(SELECT raw_bytes FROM pending.totals)''')
                db.execute('DETACH DATABASE pending')
            count = db.execute('SELECT record_count FROM totals').fetchone()[0]
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return count
    finally:
        temporary.unlink(missing_ok=True)
