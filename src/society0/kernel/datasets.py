"""不可变导入批次：热引用在规范库，正文在按块索引的 SQLite 工件。"""
from contextlib import contextmanager
import json
import uuid
from functools import lru_cache

import apsw

from ._json_chunks import CHUNK_BYTES, ChunkWriter, write_json, decode_chunk

DATASET_SCHEMA = (
    'CREATE TABLE datasets(id TEXT PRIMARY KEY NOT NULL,name TEXT NOT NULL,artifact TEXT NOT NULL,count INTEGER NOT NULL)',
)


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')


class Datasets:
    def __init__(self, store):
        self.store = store

    def import_rows(self, name, rows, *, attach=None):
        """一个批次封存记录目录和原生 zstd 帧，文件与 attach 同事务登记。"""
        count = 0
        def build(path):
            nonlocal count
            connection = apsw.Connection(str(path))
            try:
                connection.execute('PRAGMA journal_mode=OFF')
                connection.execute('PRAGMA synchronous=OFF')
                connection.execute('PRAGMA cache_size=-2048')
                connection.execute('CREATE TABLE records(ordinal INTEGER PRIMARY KEY,raw_start INTEGER NOT NULL,raw_bytes INTEGER NOT NULL)')
                connection.execute('CREATE TABLE blocks(id INTEGER PRIMARY KEY,payload BLOB NOT NULL)')
                number = 0
                def emit(size, body):
                    nonlocal number
                    connection.execute('INSERT INTO blocks VALUES(?,?)', (number, body))
                    number += 1
                sink = ChunkWriter(emit)
                with connection:
                    for ordinal, value in enumerate(rows):
                        start = sink.position
                        write_json(value, sink.write)
                        connection.execute('INSERT INTO records VALUES(?,?,?)',
                                           (ordinal, start, sink.position-start))
                        count += 1
                    sink.finish()
                connection.execute('PRAGMA user_version=4')
            finally:
                connection.close()
        artifact = self.store.prepare_artifact_file(build)
        reference = {'kind': 'dataset', 'id': uuid.uuid4().hex, 'artifact': artifact}
        def publish(writer):
            writer.execute('INSERT INTO datasets VALUES(?,?,?,?)',
                           (reference['id'], name, artifact, count))
            writer.include_artifact(artifact)
            if attach is not None:
                result = attach(writer, reference)
                import inspect
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result): result.close()
                    raise TypeError('dataset attach must be synchronous')
        self.store.transaction(publish)
        return reference

    @staticmethod
    def _registration(view, reference):
        rows = view.query('SELECT name,artifact,count FROM datasets WHERE id=?',
                          (reference['id'],), max_rows=1)
        if (reference.get('kind') != 'dataset' or not rows
                or rows[0][1] != reference['artifact']):
            raise ValueError('dataset reference unavailable')
        return rows[0]

    def describe(self, reference):
        """读取规范登记的轻元数据，不打开冷正文文件。"""
        def read(view):
            name, _, count = self._registration(view, reference)
            return {'name': name, 'count': count}
        return self.store.read(read)

    @contextmanager
    def _open(self, reference):
        def lookup(view):
            _, artifact, count = self._registration(view, reference)
            return view.run_id, artifact, count
        run_id, artifact, count = self.store.read(lookup)
        uri = (self.store.path / artifact).as_uri() + '?immutable=1'
        connection = apsw.Connection(uri, flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI)
        @lru_cache(maxsize=1)
        def decode(number):
            body = connection.execute('SELECT payload FROM blocks WHERE id=?', (number,)).fetchone()[0]
            return decode_chunk(body)
        try:
            if connection.execute('PRAGMA user_version').get != 4:
                raise ValueError('unsupported dataset codec')
            yield connection, run_id, count, decode
        finally:
            decode.cache_clear()
            connection.close()

    @staticmethod
    def _location(connection, ordinal):
        if type(ordinal) is not int or ordinal < 0:
            raise ValueError('invalid dataset ordinal')
        row = connection.execute('SELECT raw_start,raw_bytes FROM records WHERE ordinal=?', (ordinal,)).fetchone()
        if row is None: raise KeyError(ordinal)
        return row

    @staticmethod
    def _range(start, total, offset, size, decode):
        end = start + min(total, offset + size)
        offset += start
        if end <= offset:
            return b''
        output = bytearray()
        first, last = offset // CHUNK_BYTES, (end - 1) // CHUNK_BYTES
        for number in range(first, last+1):
            raw = decode(number)
            block_start = number * CHUNK_BYTES
            output.extend(raw[max(0, offset-block_start):min(len(raw), end-block_start)])
        if len(output) != end-offset:
            raise ValueError('dataset body shorter than registered record')
        return bytes(output)

    def read_payload(self, reference, ordinal, *, offset=0, size=65536):
        if type(offset) is not int or offset < 0 or type(size) is not int or size < 0:
            raise ValueError('invalid dataset byte range')
        with self._open(reference) as (connection, _, _, decode):
            start, total = self._location(connection, ordinal)
            data = self._range(start, total, offset, size, decode)
            return {'data': data, 'total_bytes': total,
                    'next_offset': offset+len(data) if offset+len(data)<total else None}

    def get(self, reference, ordinal):
        """调用方明确请求一条完整 JSON 值，内存随该条值增长。"""
        with self._open(reference) as (connection, _, _, decode):
            start, total = self._location(connection, ordinal)
            return json.loads(self._range(start, total, 0, total, decode))

    def page(self, reference, *, cursor=None, limit=100, max_bytes=65536):
        if type(limit) is not int or limit < 1 or type(max_bytes) is not int or max_bytes < 512:
            raise ValueError('positive limit and at least 512 page bytes required')
        with self._open(reference) as (connection, run_id, total, decode):
            identity = [run_id, reference['id']]
            if cursor is not None and cursor['identity'] != identity:
                raise ValueError('dataset cursor mismatch')
            after = -1 if cursor is None else cursor['after']
            if type(after) is not int or after < -1 or after >= max(total, 1):
                raise ValueError('invalid dataset cursor')
            def envelope(items, last):
                return {'items':items, 'total':total, 'next_cursor':
                        {'identity':identity,'after':last} if last+1<total else None}
            items=[];last=after;item_bytes=0
            for ordinal, start, size in connection.execute('SELECT ordinal,raw_start,raw_bytes FROM records WHERE ordinal>? ORDER BY ordinal LIMIT ?', (after,limit)):
                item={'ordinal':ordinal,'raw_bytes':size,'payload_ref':{'dataset':reference,'ordinal':ordinal,'total_bytes':size}}
                if size <= max_bytes//2:
                    item={'ordinal':ordinal,'raw_bytes':size,'value':json.loads(self._range(start,size,0,size,decode))}
                encoded_size = len(_bytes(item))
                if len(_bytes(envelope([],ordinal))) + item_bytes + encoded_size + len(items) > max_bytes:
                    if not items: raise ValueError('dataset page budget too small for reference')
                    break
                items.append(item);last=ordinal;item_bytes+=encoded_size
            return envelope(items,last)


def dataset_plugin(*,storage=('storage','store'),name='datasets'):
    """多个机制共享同一份规范数据集登记和工件服务。"""
    from .plugins import Plugin
    def install(context):context.provide('datasets',Datasets(context.require(*storage)))
    return Plugin(name,(storage[0],),install,schema=DATASET_SCHEMA)
