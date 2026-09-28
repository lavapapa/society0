"""不可变检查点记录：逐条、分块编码，查询连接始终只读。"""
from __future__ import annotations

import json
import bisect
import struct
import sys
import os
import sqlite3
import tempfile
import gzip
from collections.abc import Mapping, MutableSequence
from contextlib import closing
from pathlib import Path

CODEC = 'sqlite_records_v3'
KEY_BLOCK_BYTES = 65536
KEY_CACHE_BYTES = 262144
CHUNK = 1048576
PAGE_SIZE = 4096
SMALL_PAGE_SIZE = 1024
LARGE_RECORD_COUNT = 4096
COMPRESSION_LEVEL = 3
_OPERATIONS = ('set', 'delete', 'map_create', 'append', 'row')
_OPERATION_CODES = {name: index for index, name in enumerate(_OPERATIONS)}
_VALUE_KINDS = ('scalar', 'map', 'list')


def _operation(value):
    return _OPERATIONS[value] if isinstance(value, int) else value


def _finish_index(db):
    # 大键与排序工作区位于 TEMP 磁盘表；正文已写入压缩块。
    db.execute('CREATE INDEX temp.staged_paths ON staged_records(prefix_id,leaf)')
    db.execute('CREATE TEMP TABLE keymap(prefix_id INTEGER,leaf TEXT,path_id INTEGER,PRIMARY KEY(prefix_id,leaf)) WITHOUT ROWID')
    buffer = bytearray()
    prefix = first = None
    start_id = path_id = block_count = 0
    mappings = []
    mapping_bytes = 0
    def emit():
        if buffer:
            db.execute('INSERT INTO key_blocks VALUES (?,?,?,?)',
                       (start_id, prefix, first, gzip.compress(buffer, compresslevel=3, mtime=0)))
            buffer.clear()
    def flush_mappings():
        nonlocal mapping_bytes
        db.executemany('INSERT INTO keymap VALUES (?,?,?)', mappings)
        mappings.clear()
        mapping_bytes = 0
    for prefix_id, leaf, count in db.execute('SELECT prefix_id,leaf,count(*) FROM staged_records GROUP BY prefix_id,leaf ORDER BY prefix_id,leaf'):
        raw = leaf.encode('utf-8')
        if prefix_id != prefix or block_count >= 1024 or len(buffer) + len(raw) + 9 > KEY_BLOCK_BYTES:
            emit()
            prefix, first, start_id, block_count = prefix_id, leaf, path_id, 0
        buffer.extend(struct.pack('<Q', count))
        buffer.extend(raw)
        buffer.append(0)
        mapping_size = 128 + 4 * len(leaf)
        if mappings and mapping_bytes + mapping_size > 65536:
            flush_mappings()
        mappings.append((prefix_id, leaf, path_id))
        mapping_bytes += mapping_size
        if len(mappings) >= 512 or mapping_bytes >= 65536:
            flush_mappings()
        path_id += 1
        block_count += 1
    emit()
    if mappings:
        flush_mappings()
    db.execute('''INSERT INTO records
        SELECT r.sequence,k.path_id,r.operation,r.raw_bytes,r.record_id,r.value_flags,r.raw_start
        FROM staged_records r CROSS JOIN keymap k ON k.prefix_id=r.prefix_id AND k.leaf=r.leaf''')
    _write_metadata_keys(db)
    db.execute('CREATE INDEX records_path ON records(path_id,sequence)')
    db.execute('CREATE INDEX key_fence ON key_blocks(prefix_id,first_leaf)')


def _write_metadata_keys(db):
    # 顺序流避免随机业务顺序反复解压排序键字典；每块可独立定位。
    buffer = bytearray()
    first = last = None
    count = 0
    def emit():
        if buffer:
            db.execute('INSERT INTO metadata_keys VALUES (?,?,?)',
                       (first, last, gzip.compress(buffer, compresslevel=3, mtime=0)))
            buffer.clear()
    for sequence, prefix_id, leaf in db.execute('SELECT sequence,prefix_id,leaf FROM staged_records ORDER BY sequence'):
        raw = leaf.encode('utf-8')
        if buffer and (len(buffer) + len(raw) + 17 > KEY_BLOCK_BYTES or count >= 1024):
            emit()
            count = 0
        if not buffer:
            first = sequence
        last = sequence
        buffer.extend(struct.pack('<QQ', sequence, prefix_id))
        buffer.extend(raw)
        buffer.append(0)
        count += 1
    emit()


def _iter_metadata_keys(db, after_sequence):
    # 每次只持有一块和一个前缀；极长单键作为不可分割的元数据值处理。
    prefix_id = prefix = None
    first = db.execute('SELECT first_sequence FROM metadata_keys WHERE first_sequence<=? ORDER BY first_sequence DESC LIMIT 1',
                       (after_sequence + 1,)).fetchone()
    for (payload,) in db.execute('SELECT payload FROM metadata_keys WHERE first_sequence>=? AND last_sequence>? ORDER BY first_sequence',
                                 (first[0] if first else 0, after_sequence)):
        raw = gzip.decompress(payload)
        position = 0
        while position < len(raw):
            sequence, current_prefix = struct.unpack_from('<QQ', raw, position)
            position += 16
            end = raw.index(0, position)
            if sequence > after_sequence:
                if current_prefix != prefix_id:
                    prefix = json.loads(db.execute('SELECT path FROM prefixes WHERE id=?', (current_prefix,)).fetchone()[0])
                    prefix_id = current_prefix
                yield sequence, prefix + [json.loads(raw[position:end])]
            position = end + 1


def _decode_keys(payload):
    raw = gzip.decompress(payload)
    leaves, counts = [], []
    position = 0
    while position < len(raw):
        counts.append(struct.unpack_from('<Q', raw, position)[0])
        position += 8
        end = raw.index(0, position)
        leaves.append(raw[position:end].decode('utf-8'))
        position = end + 1
    return leaves, counts


def _key_block(db, path_id, cache):
    if cache:
        start, prefix, leaves, counts = cache['block']
        if start <= path_id < start + len(leaves):
            return start, prefix, leaves, counts
    row = db.execute('''SELECT k.id,p.path,k.payload FROM key_blocks k JOIN prefixes p ON p.id=k.prefix_id
        WHERE k.id<=? ORDER BY k.id DESC LIMIT 1''', (path_id,)).fetchone()
    start, prefix, payload = row
    leaves, counts = _decode_keys(payload)
    prefix = json.loads(prefix)
    result = (start, prefix, leaves, counts)
    cost = sum(sys.getsizeof(item) for item in (*leaves, *counts, *prefix)) + sys.getsizeof(leaves) + sys.getsizeof(counts) + sys.getsizeof(prefix)
    cache.clear()
    if cost <= KEY_CACHE_BYTES:
        cache['block'] = result
    return result


def _find_key(db, path):
    prefix = json.dumps(path[:-1], ensure_ascii=False, separators=(',', ':'))
    leaf = json.dumps(path[-1], ensure_ascii=False)
    row = db.execute('''SELECT k.id,k.payload FROM key_blocks k JOIN prefixes p ON p.id=k.prefix_id
        WHERE p.path=? AND k.first_leaf<=? ORDER BY k.first_leaf DESC LIMIT 1''', (prefix, leaf)).fetchone()
    if row is None:
        return None
    leaves, counts = _decode_keys(row[1])
    slot = bisect.bisect_left(leaves, leaf)
    if slot == len(leaves) or leaves[slot] != leaf:
        return None
    return row[0] + slot, counts[slot]


def _fits_c_encoding(value, budget=65536):
    """保守估算；超预算立即停止，不先构造待判定的 JSON。"""
    def consume(item, remaining):
        kind = type(item)
        if kind is str:
            return remaining - (6 * len(item) + 2)
        if item is None or kind is bool:
            return remaining - 5
        if kind is int:
            return remaining - (item.bit_length() // 3 + 3)
        if kind is float:
            return remaining - 32
        if kind in (dict, list, tuple):
            remaining -= 2
            values = item.items() if kind is dict else ((None, value) for value in item)
            for key, child in values:
                if kind is dict:
                    remaining = consume(key, remaining - 2)
                if remaining < 0:
                    return remaining
                remaining = consume(child, remaining - 1)
                if remaining < 0:
                    return remaining
            return remaining
        return -1
    return consume(value, budget) >= 0


def _json_parts(value):
    if _fits_c_encoding(value):
        yield json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
        return
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


def _create_tables(db, *, pending=False):
    db.executescript('''CREATE TABLE prefixes(id INTEGER PRIMARY KEY,path TEXT UNIQUE NOT NULL);
        CREATE TABLE records(sequence INTEGER PRIMARY KEY,path_id INTEGER NOT NULL,operation NOT NULL,
            raw_bytes INTEGER NOT NULL,record_id TEXT,value_flags INTEGER NOT NULL,raw_start INTEGER NOT NULL);
        CREATE TABLE key_blocks(id INTEGER PRIMARY KEY,prefix_id INTEGER,first_leaf TEXT,payload BLOB);
        CREATE TABLE chunks(raw_start INTEGER PRIMARY KEY,payload BLOB NOT NULL);
        CREATE TABLE metadata_keys(first_sequence INTEGER PRIMARY KEY,last_sequence INTEGER,payload BLOB);
        CREATE TABLE totals(record_count INTEGER,raw_bytes INTEGER,next_sequence INTEGER,pending INTEGER);
    ''')
    db.execute(('CREATE TABLE ' if pending else 'CREATE TEMP TABLE ') + '''staged_records(
        sequence INTEGER PRIMARY KEY,prefix_id INTEGER,leaf TEXT,operation,raw_bytes INTEGER,
        record_id TEXT,value_flags INTEGER,raw_start INTEGER)''')
    db.execute('PRAGMA temp.cache_size=-2048')


def _page_size(record_count):
    return SMALL_PAGE_SIZE if record_count is not None and record_count < LARGE_RECORD_COUNT else PAGE_SIZE


def write_records(path: Path, records, *, sequence_offset=0, pending=False, record_count=None) -> int:
    """在同一事务逐条写入，关闭并 fsync 后原子发布文件。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    count = 0
    try:
        with closing(sqlite3.connect(temporary)) as db, db:
            db.execute(f'PRAGMA page_size={_page_size(record_count)}')
            db.execute('PRAGMA temp_store=FILE')
            db.execute('PRAGMA journal_mode=OFF')
            db.execute('PRAGMA synchronous=OFF')
            db.execute('PRAGMA cache_size=-2048')
            _create_tables(db, pending=pending)
            db.execute('BEGIN')
            position = 0
            next_id = 0
            buffer = bytearray()
            prefix_cache = {}
            prefix_bytes = 0
            rows = []
            row_bytes = 0
            def flush_rows():
                nonlocal row_bytes
                db.executemany('INSERT INTO staged_records VALUES (?,?,?,?,?,?,?,?)', rows)
                rows.clear()
                row_bytes = 0
            def emit(raw):
                nonlocal position
                db.execute('INSERT INTO chunks VALUES (?, ?)',
                           (position, gzip.compress(raw, compresslevel=COMPRESSION_LEVEL, mtime=0)))
                position += len(raw)
            for record in records:
                sequence = int(record.get('sequence', count)) + sequence_offset
                next_id = max(next_id, sequence + 1)
                record = {**record, 'sequence': sequence}
                start = position + len(buffer)
                size = 0
                for raw in _json_parts(record):
                    size += len(raw)
                    buffer.extend(raw)
                    while len(buffer) >= CHUNK:
                        emit(buffer[:CHUNK])
                        del buffer[:CHUNK]
                record_path = record['path']
                prefix_key = tuple(record_path[:-1])
                if not all(isinstance(part, str) for part in prefix_key):
                    prefix_key = json.dumps(record_path[:-1], ensure_ascii=False, separators=(',', ':'))
                leaf = json.dumps(record_path[-1], ensure_ascii=False)
                prefix_id = prefix_cache.get(prefix_key)
                if prefix_id is None:
                    prefix = json.dumps(record_path[:-1], ensure_ascii=False, separators=(',', ':'))
                    db.execute('INSERT OR IGNORE INTO prefixes(path) VALUES (?)', (prefix,))
                    prefix_id = db.execute('SELECT id FROM prefixes WHERE path=?', (prefix,)).fetchone()[0]
                    prefix_size = 64 + 4 * len(prefix)
                    if len(prefix_cache) >= 256 or prefix_bytes + prefix_size > 65536:
                        prefix_cache.clear()
                        prefix_bytes = 0
                    if prefix_size <= 65536:
                        prefix_cache[prefix_key] = prefix_id
                        prefix_bytes += prefix_size
                value = record.get('value')
                kind = 1 if isinstance(value, Mapping) else 2 if isinstance(value, (list, tuple, MutableSequence)) else 0
                flags = kind | (4 if kind and not value else 0)
                record_id = json.dumps(record['id']) if 'id' in record else None
                # 字符串采用最多四字节/字符的预算；单条超预算立即写出。
                metadata_bytes = 128 + 4 * (len(leaf) + len(record_id or ''))
                if rows and row_bytes + metadata_bytes > 65536:
                    flush_rows()
                rows.append((sequence, prefix_id, leaf, _OPERATION_CODES.get(record['operation'], record['operation']),
                             size, record_id, flags, start))
                row_bytes += metadata_bytes
                if len(rows) == 512 or row_bytes >= 65536:
                    flush_rows()
                count += 1
            if buffer:
                emit(buffer)
            if rows:
                flush_rows()
            if not pending:
                _finish_index(db)
            db.execute('INSERT INTO totals VALUES (?,?,?,?)', (count, position, next_id, int(pending)))
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


class RecordReader:
    """单页读取会话：一个只读连接、最多一个 CHUNK 正文缓存。"""
    def __init__(self, path):
        self.db = _open(path)
        self.cache = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        self.cache.clear()
        self.db.close()

    @property
    def cached_bytes(self):
        return sum(map(len, self.cache.values()))

    def raw_bytes(self, sequence):
        row = self.db.execute('SELECT raw_bytes FROM records WHERE sequence=?', (sequence,)).fetchone()
        if row is None:
            raise KeyError(sequence)
        return row[0]

    def iter_bytes(self, sequence, *, offset=0, max_bytes=None):
        yield from _record_bytes(self.db, sequence, offset=offset, max_bytes=max_bytes, cache=self.cache)


def iter_record_bytes(path, sequence, *, offset=0, max_bytes=None):
    with closing(_open(path)) as db:
        yield from _record_bytes(db, sequence, offset=offset, max_bytes=max_bytes)


def iter_records(path):
    with closing(_open(path)) as db:
        cache = {}
        for (sequence,) in db.execute('SELECT sequence FROM records ORDER BY sequence'):
            yield json.loads(b''.join(_record_bytes(db, sequence, cache=cache)))


def read_page(path, *, after_sequence=-1, limit=100, max_bytes=1048576, path_filter=None):
    if limit < 1 or max_bytes < 1:
        raise ValueError('limit and max_bytes must be positive')
    with closing(_open(path)) as db:
        args = []
        where = ''
        if path_filter is not None:
            found = _find_key(db, path_filter)
            if found is None:
                return {'records': [], 'total': 0, 'next_sequence': None, 'payload_bytes': 0}
            args = [found[0]]
            total = found[1]
            where = 'WHERE path_id=?'
        else:
            total = db.execute('SELECT record_count FROM totals').fetchone()[0]
        tail = where + (' AND ' if where else ' WHERE ') + 'sequence>? ORDER BY sequence LIMIT ?'
        rows = db.execute('SELECT sequence,path_id,operation,raw_bytes FROM records ' + tail,
                          (*args, after_sequence, limit + 1))
        cache, key_cache = {}, {}
        records, used, more = [], 0, False
        for sequence, path_id, operation, size in rows:
            if len(records) >= limit:
                more = True
                break
            if size > max_bytes:
                start, prefix, leaves, _ = _key_block(db, path_id, key_cache)
                item = {'sequence': sequence, 'path': prefix + [json.loads(leaves[path_id-start])],
                        'operation': _operation(operation), 'raw_bytes': size, 'record_ref': {'sequence': sequence}}
                item_bytes = len(json.dumps(item, ensure_ascii=False).encode('utf-8'))
            else:
                item_bytes, item = size, None
            if used + item_bytes > max_bytes:
                if not records:
                    raise ValueError('max_bytes is too small for the record reference')
                more = True
                break
            if item is None:
                item = json.loads(b''.join(_record_bytes(db, sequence, cache=cache)))
            records.append(item)
            used += item_bytes
        last = records[-1]['sequence'] if records else after_sequence
        return {'records': records, 'total': total, 'next_sequence': last if more else None, 'payload_bytes': used}


def iter_metadata(path, *, after_sequence=-1):
    """按业务顺序读取有界元数据流，始终不解压记录正文。"""
    with closing(_open(path)) as db:
        keys = _iter_metadata_keys(db, after_sequence)
        for sequence, operation, size, record_id, flags in db.execute(
            'SELECT sequence,operation,raw_bytes,record_id,value_flags FROM records WHERE sequence>? ORDER BY sequence',
            (after_sequence,)):
            key_sequence, record_path = next(keys)
            if key_sequence != sequence:
                raise ValueError('incomplete checkpoint metadata')
            record = {'sequence': sequence, 'path': record_path,
                      'operation': _operation(operation), 'raw_bytes': size,
                      'value_kind': _VALUE_KINDS[flags & 3], 'value_empty': bool(flags & 4)}
            if record_id is not None:
                record['id'] = json.loads(record_id)
            yield record


def record_count(path):
    with closing(_open(path)) as db:
        return db.execute('SELECT count(*) FROM records').fetchone()[0]


def next_sequence(path):
    with closing(_open(path)) as db:
        return db.execute('SELECT next_sequence FROM totals').fetchone()[0]


def merge_records(path, sources):
    """正文压缩块只复制一次；键元数据在磁盘暂存后建立统一字典。"""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        total_count = 0
        for source in sources:
            with closing(_open(source)) as source_db:
                total_count += source_db.execute('SELECT record_count FROM totals').fetchone()[0]
        with closing(sqlite3.connect(temporary)) as db:
            db.execute(f'PRAGMA page_size={_page_size(total_count)}')
            db.execute('PRAGMA journal_mode=OFF')
            db.execute('PRAGMA synchronous=OFF')
            db.execute('PRAGMA cache_size=-2048')
            db.execute('PRAGMA temp_store=FILE')
            _create_tables(db)
            position = next_id = 0
            for source in sources:
                db.execute('ATTACH DATABASE ? AS source', (str(source),))
                with db:
                    source_count, source_bytes, source_next, pending = db.execute('SELECT * FROM source.totals').fetchone()
                    db.execute('INSERT OR IGNORE INTO prefixes(path) SELECT path FROM source.prefixes')
                    if pending:
                        db.execute('''INSERT INTO staged_records
                            SELECT r.sequence,pref.id,r.leaf,r.operation,r.raw_bytes,r.record_id,r.value_flags,r.raw_start+?
                            FROM source.staged_records r JOIN source.prefixes pp ON pp.id=r.prefix_id
                            JOIN prefixes pref ON pref.path=pp.path''', (position,))
                    else:
                        db.execute('CREATE TEMP TABLE source_keys(id INTEGER PRIMARY KEY,prefix_id INTEGER,leaf TEXT)')
                        def keys():
                            for start, prefix, payload in db.execute('SELECT id,prefix_id,payload FROM source.key_blocks ORDER BY id'):
                                leaves, _ = _decode_keys(payload)
                                for slot, leaf in enumerate(leaves):
                                    yield start + slot, prefix, leaf
                        db.executemany('INSERT INTO source_keys VALUES (?,?,?)', keys())
                        db.execute('''INSERT INTO staged_records
                            SELECT r.sequence,pref.id,k.leaf,r.operation,r.raw_bytes,r.record_id,r.value_flags,r.raw_start+?
                            FROM source.records r JOIN source_keys k ON k.id=r.path_id
                            JOIN source.prefixes pp ON pp.id=k.prefix_id JOIN prefixes pref ON pref.path=pp.path''', (position,))
                        db.execute('DROP TABLE source_keys')
                    db.execute('INSERT INTO chunks SELECT raw_start+?,payload FROM source.chunks', (position,))
                    position += source_bytes
                    next_id = max(next_id, source_next)
                db.execute('DETACH DATABASE source')
            with db:
                _finish_index(db)
                db.execute('INSERT INTO totals VALUES (?,?,?,0)', (total_count, position, next_id))
        with temporary.open('rb') as handle:
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return total_count
    finally:
        temporary.unlink(missing_ok=True)
