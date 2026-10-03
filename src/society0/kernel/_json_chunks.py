"""JSON 值的有界 UTF8 编码与独立压缩块，供权威正文存储复用。"""
import json
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
from itertools import islice, chain
import zlib

CHUNK_BYTES = 65536


def _json_parts(value, ancestors=None):
    """字符串先切片再转义；不构造巨大 JSON 字符串。"""
    if type(value) is str:
        yield b'"'
        for start in range(0, len(value), 8192):
            yield json.dumps(value[start:start + 8192], ensure_ascii=False)[1:-1].encode('utf8')
        yield b'"'
    elif type(value) in (dict, list):
        ancestors = set() if ancestors is None else ancestors
        if id(value) in ancestors:
            raise ValueError('circular JSON value')
        ancestors.add(id(value))
        try:
            mapping = type(value) is dict
            yield b'{' if mapping else b'['
            for index, pair in enumerate(value.items() if mapping else value):
                if index:
                    yield b','
                if mapping:
                    key, item = pair
                    if type(key) is not str:
                        raise TypeError('JSON object keys must be strings')
                    yield from _json_parts(key, ancestors)
                    yield b':'
                else:
                    item = pair
                yield from _json_parts(item, ancestors)
            yield b'}' if mapping else b']'
        finally:
            ancestors.remove(id(value))
    elif value is None or type(value) in (bool, int, float):
        yield json.dumps(value, allow_nan=False).encode('ascii')
    else:
        raise TypeError('Thread values must be JSON values')


def raw_chunks(value):
    buffer = bytearray()
    for part in _json_parts(value):
        for offset in range(0, len(part), CHUNK_BYTES):
            piece = memoryview(part)[offset:offset + CHUNK_BYTES]
            while piece:
                count = min(CHUNK_BYTES - len(buffer), len(piece))
                buffer.extend(piece[:count])
                piece = piece[count:]
                if len(buffer) == CHUNK_BYTES:
                    yield bytes(buffer)
                    buffer.clear()
    if buffer:
        yield bytes(buffer)


def encode_chunks(value):
    for raw in raw_chunks(value):
        yield len(raw), zlib.compress(raw, 3)


class ChunkEncoder:
    """一个规范 writer 的惰性工作池；工作线程仅接收不可变原始字节。"""
    def __init__(self, workers=4, inflight_bytes=8*CHUNK_BYTES):
        if type(workers) is not int or workers < 1:
            raise ValueError('compression_workers must be a positive integer')
        if type(inflight_bytes) is not int or inflight_bytes < CHUNK_BYTES:
            raise ValueError('compression_inflight_bytes must hold one 64KiB chunk')
        self.workers, self.inflight_bytes = workers, inflight_bytes
        self.pool = None
        self.closed = False

    def encode(self, value):
        if self.closed:
            raise RuntimeError('compression encoder closed')
        source = raw_chunks(value)
        prefix = list(islice(source, 4))
        if self.workers == 1 or sum(map(len,prefix)) < 4*CHUNK_BYTES:
            for raw in chain(prefix,source):
                yield len(raw), zlib.compress(raw,3)
            return
        if self.pool is None:
            self.pool = ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix='society0-compress')
        pending = deque()
        retained = 0
        try:
            for raw in chain(prefix,source):
                while pending and retained+len(raw)>self.inflight_bytes:
                    size,future = pending[0]
                    result = future.result()
                    pending.popleft()
                    retained -= size
                    yield size,result
                pending.append((len(raw),self.pool.submit(zlib.compress,raw,3)))
                retained += len(raw)
            while pending:
                size,future = pending[0]
                result = future.result()
                pending.popleft()
                yield size,result
        finally:
            for _,future in pending:
                future.cancel()
            wait([future for _,future in pending])

    def close(self):
        self.closed = True
        if self.pool is not None:
            self.pool.shutdown(wait=True,cancel_futures=True)


def decode_chunks(chunks):
    raw = bytearray()
    for payload in chunks:
        raw.extend(zlib.decompress(payload))
    return json.loads(raw)
