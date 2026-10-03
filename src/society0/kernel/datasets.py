"""不可变导入批次：热引用在规范库，正文在已封存的原生 SQLite 工件。"""
from contextlib import contextmanager
import json
import uuid
import zstandard

import apsw

from ._json_chunks import CHUNK_BYTES, raw_chunks

DATASET_SCHEMA = (
    'CREATE TABLE datasets(id TEXT PRIMARY KEY NOT NULL,name TEXT NOT NULL,artifact TEXT NOT NULL,count INTEGER NOT NULL)',
)


def _decompress(decoder, body):
    return decoder.decompress(body, max_output_size=CHUNK_BYTES)


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
                connection.execute('CREATE TABLE records(ordinal INTEGER PRIMARY KEY,raw_start INTEGER NOT NULL,raw_bytes INTEGER NOT NULL)')
                connection.execute('CREATE TABLE blocks(id INTEGER PRIMARY KEY,payload BLOB NOT NULL)')
                compressor = zstandard.ZstdCompressor(level=3)
                buffer = bytearray()
                position = 0
                block = 0
                def flush():
                    nonlocal block
                    if buffer:
                        connection.execute('INSERT INTO blocks VALUES(?,?)', (block,compressor.compress(bytes(buffer))))
                        block += 1
                        buffer.clear()
                with connection:
                    for ordinal, value in enumerate(rows):
                        start = position
                        for raw in raw_chunks(value):
                            position += len(raw)
                            piece = memoryview(raw)
                            while piece:
                                size = min(CHUNK_BYTES-len(buffer),len(piece))
                                buffer.extend(piece[:size]);piece=piece[size:]
                                if len(buffer)==CHUNK_BYTES:flush()
                        connection.execute('INSERT INTO records VALUES(?,?,?)', (ordinal,start,position-start))
                        count += 1
                    flush()
                connection.execute('PRAGMA user_version=2')
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
            if connection.execute('PRAGMA user_version').get != 2:
                raise ValueError('unsupported dataset codec')
            yield connection, run_id, count, {'decoder': zstandard.ZstdDecompressor(), 'number': None, 'raw': b''}
        finally:
            connection.close()

    @staticmethod
    def _location(connection, ordinal):
        if type(ordinal) is not int or ordinal < 0:
            raise ValueError('invalid dataset ordinal')
        row = connection.execute('SELECT raw_start,raw_bytes FROM records WHERE ordinal=?', (ordinal,)).fetchone()
        if row is None: raise KeyError(ordinal)
        return row

    @staticmethod
    def _range(connection, start, total, offset, size, decoder):
        end = start + min(total, offset + size)
        offset += start
        if end <= offset: return b''
        output = bytearray()
        for number, body in connection.execute(
            'SELECT id,payload FROM blocks WHERE id>=? AND id<=? ORDER BY id',
            (offset // CHUNK_BYTES, (end - 1) // CHUNK_BYTES),
        ):
            if decoder['number'] != number:
                decoder['raw'] = _decompress(decoder['decoder'], body)
                decoder['number'] = number
            raw = decoder['raw']
            block_start = number * CHUNK_BYTES
            output.extend(raw[max(0, offset-block_start):min(len(raw), end-block_start)])
        return bytes(output)

    def read_payload(self, reference, ordinal, *, offset=0, size=65536):
        if type(offset) is not int or offset < 0 or type(size) is not int or size < 0:
            raise ValueError('invalid dataset byte range')
        with self._open(reference) as (connection, _, _, decoder):
            start, total = self._location(connection, ordinal)
            data = self._range(connection, start, total, offset, size, decoder)
            return {'data': data, 'total_bytes': total,
                    'next_offset': offset+len(data) if offset+len(data)<total else None}

    def get(self, reference, ordinal):
        """调用方明确请求一条完整 JSON 值，内存随该条值增长。"""
        with self._open(reference) as (connection, _, _, decoder):
            start, total = self._location(connection, ordinal)
            return json.loads(self._range(connection, start, total, 0, total, decoder))

    def page(self, reference, *, cursor=None, limit=100, max_bytes=65536):
        if type(limit) is not int or limit < 1 or type(max_bytes) is not int or max_bytes < 512:
            raise ValueError('positive limit and at least 512 page bytes required')
        with self._open(reference) as (connection, run_id, total, decoder):
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
            for ordinal, start, size in connection.execute('SELECT ordinal,raw_start,raw_bytes FROM records WHERE ordinal>? ORDER BY ordinal LIMIT ?', (after,limit)):
                item={'ordinal':ordinal,'raw_bytes':size,'payload_ref':{'dataset':reference,'ordinal':ordinal,'total_bytes':size}}
                if size <= max_bytes//2:
                    item={'ordinal':ordinal,'raw_bytes':size,'value':json.loads(self._range(connection,start,size,0,size,decoder))}
                encoded_size = len(_bytes(item))
                if len(_bytes(envelope([],ordinal))) + item_bytes + encoded_size + len(items) > max_bytes:
                    if not items: raise ValueError('dataset page budget too small for reference')
                    break
                items.append(item);last=ordinal;item_bytes+=encoded_size
            return envelope(items,last)
