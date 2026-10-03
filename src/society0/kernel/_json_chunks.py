"""JSON 值的有界 UTF8 编码与独立压缩块，供权威正文存储复用。"""
import json
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


def encode_chunks(value):
    buffer = bytearray()
    for part in _json_parts(value):
        for offset in range(0, len(part), CHUNK_BYTES):
            piece = memoryview(part)[offset:offset + CHUNK_BYTES]
            while piece:
                count = min(CHUNK_BYTES - len(buffer), len(piece))
                buffer.extend(piece[:count])
                piece = piece[count:]
                if len(buffer) == CHUNK_BYTES:
                    yield len(buffer), zlib.compress(buffer, 3)
                    buffer.clear()
    if buffer:
        yield len(buffer), zlib.compress(buffer, 3)


def decode_chunks(chunks):
    raw = bytearray()
    for payload in chunks:
        raw.extend(zlib.decompress(payload))
    return json.loads(raw)
