"""不可变导入批次：热引用在规范库，正文在已封存的原生 SQLite 工件。"""
from contextlib import contextmanager
import json
import uuid
import zlib

import apsw

from ._json_chunks import CHUNK_BYTES, encode_chunks

DATASET_SCHEMA = (
    'CREATE TABLE datasets(id TEXT PRIMARY KEY NOT NULL,name TEXT NOT NULL,artifact TEXT NOT NULL,count INTEGER NOT NULL)',
)


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf8')


class Datasets:
    def __init__(self, store):
        self.store = store

    def import_rows(self, name, rows, *, attach=None):
        """一个显式批次对应一个文件，attach 与批次头、文件依赖同事务。"""
        count = 0
        def build(path):
            nonlocal count
            connection = apsw.Connection(str(path))
            try:
                # 私有构建文件，最终关闭和 fsync 后才发布。
                connection.execute('PRAGMA journal_mode=OFF')
                connection.execute('PRAGMA synchronous=OFF')
                connection.execute('PRAGMA cache_size=-2048')
                connection.execute('CREATE TABLE records(ordinal INTEGER PRIMARY KEY,raw_bytes INTEGER NOT NULL)')
                connection.execute('CREATE TABLE chunks(ordinal INTEGER NOT NULL,chunk INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(ordinal,chunk)) WITHOUT ROWID')
                with connection:
                    for ordinal, value in enumerate(rows):
                        total = 0
                        def chunks():
                            nonlocal total
                            for number, (size, body) in enumerate(encode_chunks(value)):
                                total += size
                                yield ordinal, number, body
                        connection.executemany('INSERT INTO chunks VALUES(?,?,?)', chunks())
                        connection.execute('INSERT INTO records VALUES(?,?)', (ordinal, total))
                        count += 1
                connection.execute('PRAGMA user_version=1')
            finally:
                connection.close()
        artifact = self.store.prepare_artifact_file(build)
        reference = {'kind': 'dataset', 'id': uuid.uuid4().hex, 'artifact': artifact}
        def publish(writer):
            writer.execute('INSERT INTO datasets VALUES(?,?,?,?)', (reference['id'], name, artifact, count))
            writer.include_artifact(artifact)
            if attach is not None:
                result = attach(writer, reference)
                import inspect
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result): result.close()
                    raise TypeError('dataset attach must be synchronous')
        self.store.transaction(publish)
        return reference

    @contextmanager
    def _open(self, reference):
        def lookup(view):
            rows = view.query('SELECT artifact,count FROM datasets WHERE id=?', (reference['id'],), max_rows=1)
            if reference.get('kind') != 'dataset' or not rows or rows[0][0] != reference['artifact']:
                raise ValueError('dataset reference unavailable')
            return view.run_id, *rows[0]
        run_id, artifact, count = self.store.read(lookup)
        connection = apsw.Connection(str(self.store.path / artifact), flags=apsw.SQLITE_OPEN_READONLY)
        try:
            yield connection, run_id, count
        finally:
            connection.close()

    @staticmethod
    def _size(connection, ordinal):
        if type(ordinal) is not int or ordinal < 0:
            raise ValueError('invalid dataset ordinal')
        row = connection.execute('SELECT raw_bytes FROM records WHERE ordinal=?', (ordinal,)).fetchone()
        if row is None: raise KeyError(ordinal)
        return row[0]

    @staticmethod
    def _range(connection, ordinal, total, offset, size):
        end = min(total, offset + size)
        if end <= offset: return b''
        output = bytearray()
        for number, body in connection.execute(
            'SELECT chunk,payload FROM chunks WHERE ordinal=? AND chunk>=? AND chunk<=? ORDER BY chunk',
            (ordinal, offset // CHUNK_BYTES, (end - 1) // CHUNK_BYTES),
        ):
            raw = zlib.decompress(body)
            start = number * CHUNK_BYTES
            output.extend(raw[max(0, offset-start):min(len(raw), end-start)])
        return bytes(output)

    def read_payload(self, reference, ordinal, *, offset=0, size=65536):
        if type(offset) is not int or offset < 0 or type(size) is not int or size < 0:
            raise ValueError('invalid dataset byte range')
        with self._open(reference) as (connection, _, _):
            total = self._size(connection, ordinal)
            data = self._range(connection, ordinal, total, offset, size)
            return {'data': data, 'total_bytes': total,
                    'next_offset': offset+len(data) if offset+len(data)<total else None}

    def get(self, reference, ordinal):
        """调用方明确请求一条完整 JSON 值，内存随该条值增长。"""
        with self._open(reference) as (connection, _, _):
            total = self._size(connection, ordinal)
            return json.loads(self._range(connection, ordinal, total, 0, total))

    def page(self, reference, *, cursor=None, limit=100, max_bytes=65536):
        if type(limit) is not int or limit < 1 or type(max_bytes) is not int or max_bytes < 512:
            raise ValueError('positive limit and at least 512 page bytes required')
        with self._open(reference) as (connection, run_id, total):
            identity = [run_id, reference['id'], reference['artifact']]
            if cursor is not None and cursor['identity'] != identity:
                raise ValueError('dataset cursor mismatch')
            after = -1 if cursor is None else cursor['after']
            if type(after) is not int or after < -1 or after >= max(total, 1):
                raise ValueError('invalid dataset cursor')
            def envelope(items, last):
                return {'items':items, 'total':total, 'next_cursor':
                        {'identity':identity,'after':last} if last+1<total else None}
            items=[];last=after;item_bytes=0
            for ordinal, size in connection.execute('SELECT ordinal,raw_bytes FROM records WHERE ordinal>? ORDER BY ordinal LIMIT ?', (after,limit)):
                item={'ordinal':ordinal,'raw_bytes':size,'payload_ref':{'dataset':reference,'ordinal':ordinal,'total_bytes':size}}
                if size <= max_bytes//2:
                    item={'ordinal':ordinal,'raw_bytes':size,'value':json.loads(self._range(connection,ordinal,size,0,size))}
                encoded_size = len(_bytes(item))
                if len(_bytes(envelope([],ordinal))) + item_bytes + encoded_size + len(items) > max_bytes:
                    if not items: raise ValueError('dataset page budget too small for reference')
                    break
                items.append(item);last=ordinal;item_bytes+=encoded_size
            return envelope(items,last)
