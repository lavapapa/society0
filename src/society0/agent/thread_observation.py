"""Thread 的只读观察接口；分页成本由本页记录和返回字节决定。"""
from __future__ import annotations

import json
import os
import struct
from typing import Any, Mapping


class ThreadObservation:
    def _require_writable(self) -> None:
        if self.read_only:
            raise PermissionError("agent thread store is read-only")

    def _cache_put(self, cache, key, value) -> None:
        # 单项 opened payload 的缓存上限；超大内容仍保留在原始 Thread 中。
        if getattr(value, 'opened_payload_bytes', 0) > 65536:
            value.opened_payload = None
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self.cache_max_entries:
            cache.popitem(last=False)

    def read_event_page(
        self, thread_id: str, *, cursor: Mapping[str, Any] | None = None,
        max_records: int = 100, max_bytes: int = 65536,
        boundary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """读取固定边界内的一页。bytes 是 events 的紧凑 JSON 字节数。

        过大事件以 event_ref 返回，正文可通过 read_content 分段读取。
        默认边界捕获已写出 offsets 的记录；它不声明检查点已经发布。
        已发布边界应传入检查点中的 Thread reference。
        """
        tid = self._validate_thread_id(thread_id)
        if max_records < 1 or max_bytes < 512:
            raise ValueError("max_records must be positive and max_bytes at least 512")
        if cursor is not None and boundary is not None:
            raise ValueError("pass cursor or boundary, not both")
        path = self._resolve_thread_path(tid)
        relative = self._relative_path(path)
        with path.with_suffix('.offsets').open('rb') as offsets:
            if cursor is not None:
                if cursor.get('version') != 1 or cursor.get('run_dir') != str(self.run_dir) or cursor.get('thread_id') != tid or cursor.get('path') != relative:
                    raise ValueError('invalid Thread page cursor')
                captured = dict(cursor['boundary'])
                sequence = int(cursor['sequence'])
            else:
                sequence = 0
                if boundary is not None:
                    if boundary.get('thread_id') != tid or boundary.get('path') != relative:
                        raise ValueError('Thread boundary identity mismatch')
                    if 'cursor' in boundary:
                        total = int(boundary['cursor']['sequence'])
                        end = int(boundary['cursor']['byte_offset'])
                        kind = 'published_reference'
                    else:
                        total = int(boundary['total'])
                        end = int(boundary['end_offset'])
                        kind = boundary['kind']
                else:
                    offsets.seek(0, os.SEEK_END)
                    total = offsets.tell() // 8
                    end = 0
                    if total:
                        offsets.seek((total - 1) * 8)
                        end = struct.unpack('<Q', offsets.read(8))[0]
                    kind = 'captured'
                captured = {'thread_id': tid, 'path': relative, 'total': total, 'end_offset': end, 'kind': kind}
            total = int(captured['total'])
            end = int(captured['end_offset'])
            if sequence < 0 or sequence > total or end > path.stat().st_size:
                raise ValueError('Thread boundary unavailable')
            offsets.seek(max(0, sequence - 1) * 8)
            start = struct.unpack('<Q', offsets.read(8))[0] if sequence else 0
            events = []
            used = 2  # JSON 数组的方括号。
            with path.open('rb') as source:
                source.seek(start)
                while sequence < total and len(events) < max_records:
                    packed = offsets.read(8)
                    if len(packed) != 8:
                        raise ValueError('Thread offset index is incomplete')
                    stop = struct.unpack('<Q', packed)[0]
                    if stop <= start or stop > end:
                        raise ValueError('Thread offset lies outside boundary')
                    size = stop - start
                    reference = {'run_dir': str(self.run_dir), 'path': relative, 'offset': start, 'bytes': size}
                    # 单条超过整页预算时始终返回引用，避免正文物化。
                    if size > max_bytes - 2:
                        event = {'sequence': sequence + 1, 'event_ref': reference}
                    else:
                        raw = source.read(size)
                        self.metrics['jsonl_bytes_read'] += len(raw)
                        event = json.loads(raw)
                        if event.get('thread_id') != tid or event.get('sequence') != sequence + 1:
                            raise ValueError('Thread event identity mismatch')
                    encoded_size = len(json.dumps(event, ensure_ascii=False, separators=(',', ':')).encode())
                    cost = encoded_size + bool(events)
                    if used + cost > max_bytes:
                        if not events:
                            raise ValueError("max_bytes cannot hold the content reference")
                        break
                    events.append(event)
                    used += cost
                    sequence += 1
                    start = stop
                    source.seek(stop)
            next_cursor = None if sequence == total else {
                'version': 1, 'run_dir': str(self.run_dir), 'thread_id': tid, 'path': relative,
                'sequence': sequence, 'boundary': captured,
            }
            return {'events': events, 'next_cursor': next_cursor, 'boundary': captured,
                    'total': total, 'bytes': used,
                    'readable_through': end,
                    'durable_through': end if captured['kind'] == 'published_reference' else None}

    def read_content(self, reference: Mapping[str, Any], *, offset: int = 0,
                     max_bytes: int = 65536) -> dict[str, Any]:
        """按字节读取引用正文；调用者拼接 bytes 后再解码 UTF-8。"""
        if reference.get('run_dir', str(self.run_dir)) != str(self.run_dir):
            raise ValueError('Thread content belongs to another run')
        size = int(reference['bytes'])
        if offset < 0 or offset > size or max_bytes < 1:
            raise ValueError('invalid content range')
        path = self._safe_relative_file(str(reference['path']), component='Thread content')
        with path.open('rb') as source:
            source.seek(int(reference.get('offset', 0)) + offset)
            data = source.read(min(max_bytes, size - offset))
        if len(data) != min(max_bytes, size - offset):
            raise ValueError('Thread content is incomplete')
        following = offset + len(data)
        return {'data': data, 'total_bytes': size,
                'next_offset': following if following < size else None}

    def read_event(self, reference: Mapping[str, Any]) -> dict[str, Any]:
        return json.loads(self.read_content(reference, max_bytes=int(reference['bytes']))['data'])

    def read_payload(self, reference: Mapping[str, Any]) -> Any:
        return self._materialize_payload({'payload': None, 'payload_ref': reference})
