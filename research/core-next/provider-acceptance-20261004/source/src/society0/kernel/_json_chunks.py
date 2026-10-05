"""JSON 值的流式 UTF8 编码与独立 zstd 帧，供权威正文存储复用。"""
import json

from backports import zstd

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


class ChunkWriter:
    """同一64KiB原生帧sink，单JSON和连续不可变批次共用。"""
    def __init__(self, emit):
        self.emit = emit
        self.compressor = zstd.ZstdCompressor(level=3)
        self.pending = bytearray()
        self.position = 0

    def write(self, raw):
        self.position += len(raw)
        offset = 0
        while offset < len(raw):
            size = min(CHUNK_BYTES - len(self.pending), len(raw) - offset)
            self.pending.extend(raw[offset:offset + size])
            offset += size
            if len(self.pending) == CHUNK_BYTES:
                self.emit(len(self.pending), self.compressor.compress(self.pending, self.compressor.FLUSH_FRAME))
                self.pending.clear()

    def finish(self):
        if self.pending:
            self.emit(len(self.pending), self.compressor.compress(self.pending, self.compressor.FLUSH_FRAME))
            self.pending.clear()


def write_chunks(value, emit):
    """规范 writer 同步消费每个独立帧，压缩器不持有后台任务。"""
    sink = ChunkWriter(emit)
    write_json(value, sink.write)
    sink.finish()


def decode_chunk(payload):
    return zstd.decompress(payload)


def decode_chunks(chunks):
    raw = bytearray()
    for payload in chunks:
        raw.extend(decode_chunk(payload))
    return json.loads(raw)
