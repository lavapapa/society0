"""JSON 值的有界 UTF8 编码与独立压缩块，供权威正文存储复用。"""
import json
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait
import zlib

CHUNK_BYTES = 65536


def write_json(value, emit):
    """原生同步编码；首个写入错误后停止副作用，完成遍历后保留原异常。"""
    import rapidjson
    class Sink:
        error = None
        def write(self, raw):
            if self.error is None:
                try:
                    emit(raw)
                except BaseException as error:
                    self.error = error
    sink = Sink()
    try:
        rapidjson.dump(value, sink, ensure_ascii=False, allow_nan=False,
                      number_mode=rapidjson.NM_NONE, bytes_mode=rapidjson.BM_NONE,
                      iterable_mode=rapidjson.IM_ONLY_LISTS,
                      mapping_mode=rapidjson.MM_ONLY_DICTS, chunk_size=CHUNK_BYTES)
    finally:
        if sink.error is not None:
            raise sink.error


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

    def write(self, value, emit):
        if self.closed:
            raise RuntimeError('compression encoder closed')
        prefix = []
        pending = deque()
        retained = 0
        parallel = False
        def take():
            nonlocal retained
            size, future = pending[0]
            body = future.result()
            pending.popleft()
            retained -= size
            emit(size, body)
        def submit(raw):
            nonlocal retained
            while pending and retained + len(raw) > self.inflight_bytes:
                take()
            pending.append((len(raw), self.pool.submit(zlib.compress, raw, 3)))
            retained += len(raw)
        def accept(raw):
            nonlocal parallel
            if not raw:
                return
            if self.workers == 1:
                emit(len(raw), zlib.compress(raw, 3))
            elif parallel:
                submit(raw)
            else:
                prefix.append(raw)
                if sum(map(len, prefix)) >= 4 * CHUNK_BYTES:
                    if self.pool is None:
                        self.pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix='society0-compress')
                    parallel = True
                    for item in prefix:
                        submit(item)
                    prefix.clear()
        try:
            write_json(value, accept)
            for raw in prefix:
                emit(len(raw), zlib.compress(raw, 3))
            while pending:
                take()
        finally:
            for _, future in pending:
                future.cancel()
            wait([future for _, future in pending])

    def close(self):
        self.closed = True
        if self.pool is not None:
            self.pool.shutdown(wait=True,cancel_futures=True)


def decode_chunks(chunks):
    raw = bytearray()
    for payload in chunks:
        raw.extend(zlib.decompress(payload))
    return json.loads(raw)
