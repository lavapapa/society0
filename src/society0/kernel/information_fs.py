"""共享信息的逻辑文件投影；原文保持文件身份，传输分片显式访问。"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import PurePosixPath
from urllib.parse import unquote

from .interaction import Unavailable


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()


def _token(value):
    return base64.urlsafe_b64encode(_json(value)).decode().rstrip('=')


def _untoken(value):
    return json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))


def _name(value):
    # Provider 公布的路径已经承担组件编码，shell保持同一逻辑身份。
    return value


class InformationFiles:
    def __init__(self, information, scope, *, max_file_bytes=65536, page_size=100):
        if type(max_file_bytes) is not int or max_file_bytes < 1024:
            raise ValueError('projection file budget must be at least 1024 bytes')
        if type(page_size) is not int or not 2 <= page_size <= 100:
            raise ValueError('projection page size must be 2..100')
        self.information = information
        self.scope = scope
        self.max_file_bytes = max_file_bytes
        self.page_size = page_size
        self.part_bytes = max_file_bytes // 4 * 3
        self._closed = False
        self._tasks = set()

    def _check(self):
        if self._closed:
            raise RuntimeError('information filesystem is closed')
        self.scope.check_active()

    def _parse(self, path):
        source = []
        cursor = None
        parts = PurePosixPath(path).parts[1:]
        for index, part in enumerate(parts):
            if part.startswith('@page-'):
                cursor = _untoken(part[6:])
            elif part in ('@manifest.json','@schema.json') or part.startswith(('@parts-', '@text-')):
                return '/' + '/'.join(source), cursor, parts[index:]
            else:
                source.append(part)
                cursor = None
        return '/' + '/'.join(source), cursor, ()

    @staticmethod
    def _entry(name, kind, size=0):
        return name, kind, size, 0o555 if kind == 'directory' else 0o444, 0, 0

    async def _stat(self, source):
        stat = await self.information.stat(source)
        if stat.kind == 'file' and stat.total_bytes is None:
            chunk = await self.information.read(source, size=1)
            return stat.kind, chunk.total_bytes, chunk.revision
        return stat.kind, stat.total_bytes, stat.revision

    async def _part(self, source, revision, total, number, text):
        offset = number * self.part_bytes
        if offset >= total or number < 0:
            raise FileNotFoundError(source)
        size = min(self.part_bytes, total - offset)
        # 文本片在两端最多多看一个UTF-8字符；完整字节片独立base64编码。
        chunk = await self.information.read(source, offset=offset,
            size=size + (3 if text else 0), expected_revision=revision)
        if chunk.total_bytes != total:
            raise ValueError('resource changed during projection read')
        data = chunk.data
        if len(data) < size:
            raise ValueError('provider returned an incomplete projection range')
        if not text:
            return base64.b64encode(data[:size])
        start = 0
        while start < min(3, len(data)) and 0x80 <= data[start] <= 0xbf:
            start += 1
        end = size
        while end < len(data) and 0x80 <= data[end] <= 0xbf:
            end += 1
        raw = data[start:end]
        raw.decode('utf-8')
        return raw

    def _parts_node(self, suffix, total):
        count = (total + self.part_bytes - 1) // self.part_bytes
        start, end = 0, count
        for index, component in enumerate(suffix):
            if '-' in component:
                left, right = map(int, component.split('-', 1))
                if not start <= left < right <= end:
                    raise FileNotFoundError(component)
                start, end = left, right
            else:
                number, extension = component.rsplit('.', 1)
                number = int(number)
                if index != len(suffix)-1 or not start <= number < end or extension not in ('txt', 'b64'):
                    raise FileNotFoundError(component)
                return start, end, number, extension
        return start, end, None, None

    async def _projected(self, operation, source, suffix, total, revision):
        count = (total + self.part_bytes - 1) // self.part_bytes
        identity = _token({'revision': revision, 'total': total, 'part_bytes': self.part_bytes})
        roots = ('@parts-' + identity, '@text-' + identity)
        if not suffix:
            if operation == 'list':
                return [self._entry('@manifest.json', 'file'), *(self._entry(name, 'directory') for name in roots)]
            if operation == 'stat':
                return self._entry('', 'directory')[1:]
            if operation == 'exists':
                return True
            raise IsADirectoryError(source)
        if suffix == ('@manifest.json',):
            data = _json({'format': 'society0-parts-v1', 'source': source, 'revision': revision,
                'total_bytes': total, 'part_count': count, 'part_raw_bytes': self.part_bytes,
                'max_file_bytes': self.max_file_bytes, 'bytes_directory': roots[0],
                'text_directory': roots[1], 'order': 'ascending numeric filename',
                'bytes_read': 'Decode each .b64 file separately, then concatenate in order.',
                'text_read': 'For UTF-8 text concatenate .txt files in order; invalid UTF-8 is an error.'})
            if len(data) > self.max_file_bytes:
                raise ValueError('projection manifest exceeds file budget')
            if operation == 'read':return data
            if operation == 'stat':return self._entry('', 'file', len(data))[1:]
            if operation == 'exists':return True
            raise NotADirectoryError(source)
        marker = suffix[0]
        if not marker.startswith(('@parts-', '@text-')):
            raise FileNotFoundError(marker)
        text = marker.startswith('@text-')
        bound = _untoken(marker.split('-', 1)[1])
        if _json(bound) != _json({'revision': revision, 'total': total, 'part_bytes': self.part_bytes}):
            raise ValueError('projection version changed; reopen the manifest')
        start, end, number, extension = self._parts_node(suffix[1:], total)
        if number is not None:
            if extension != ('txt' if text else 'b64'):
                raise FileNotFoundError(suffix[-1])
            data = await self._part(source, revision, total, number, text)
            if operation == 'read':return data
            if operation == 'stat':return self._entry('', 'file', len(data))[1:]
            if operation == 'exists':return True
            raise NotADirectoryError(source)
        if operation == 'list':
            span = 1
            while (end-start + span-1)//span > self.page_size:
                span *= max(2, self.page_size)
            if span == 1:
                return [self._entry(f'{index:012d}.{"txt" if text else "b64"}', 'file') for index in range(start,end)]
            return [self._entry(f'{index:012d}-{min(end,index+span):012d}', 'directory')
                    for index in range(start,end,span)]
        if operation == 'stat':return self._entry('', 'directory')[1:]
        if operation == 'exists':return True
        raise IsADirectoryError(source)

    async def callback(self, operation, path):
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            self._check()
            source, cursor, suffix = self._parse(path)
            kind, total, revision = await self._stat(source)
            if kind == 'file' and suffix:
                result = await self._projected(operation,source,suffix,total,revision)
            elif kind=='directory' and suffix==('@schema.json',):
                data=_json(await self.information.metadata(source))
                if operation=='read':result=data
                elif operation=='stat':result=self._entry('','file',len(data))[1:]
                elif operation=='exists':result=True
                else:raise NotADirectoryError(path)
            elif kind == 'directory' and suffix == ('@manifest.json',):
                page = await self.information.list_files(source,limit=self.page_size,cursor=cursor)
                data = _json({'format':'society0-directory-v1','source':source,
                    'total':page.total,'revision':page.revision,'page_size':self.page_size,
                    'entries_on_page':len(page.items),
                    'next_directory':None if page.next_cursor is None else '@page-' + _token(page.next_cursor),
                    'names':'Percent escapes preserve original provider path components.'})
                if len(data)>self.max_file_bytes:raise ValueError('directory manifest exceeds file budget')
                if operation=='read':result=data
                elif operation=='stat':result=self._entry('', 'file', len(data))[1:]
                elif operation=='exists':result=True
                else:raise NotADirectoryError(path)
            elif suffix:
                raise FileNotFoundError(path)
            elif operation == 'list':
                if kind != 'directory':raise NotADirectoryError(path)
                page = await self.information.list_files(source,limit=self.page_size,cursor=cursor)
                result = [self._entry('@manifest.json','file')]
                try:await self.information.metadata(source)
                except Unavailable:pass
                else:result.append(self._entry('@schema.json','file'))
                for item in page.items:
                    child_kind = item['kind']
                    result.append(self._entry(_name(PurePosixPath(item['path']).name),child_kind))
                if page.next_cursor is not None:
                    result.append(self._entry('@page-' + _token(page.next_cursor),'directory'))
            elif operation == 'read':
                if kind != 'file':raise IsADirectoryError(path)
                # Bashkit read_file 合同整读原文；专用 read 承担真正范围读取。
                chunks = []
                offset = 0
                while offset < total:
                    chunk = await self.information.read(source, offset=offset,
                        size=min(65536, total-offset), expected_revision=revision)
                    if not chunk.data or chunk.total_bytes != total:
                        raise ValueError('provider returned an incomplete original range')
                    chunks.append(chunk.data)
                    offset += len(chunk.data)
                result = b''.join(chunks)
            elif operation == 'stat':
                result = self._entry('',kind,total or 0)[1:]
            elif operation == 'exists':result=True
            else:raise ValueError('information views do not contain symbolic links')
            self._check()
            return result
        except (Unavailable, FileNotFoundError) as exc:
            if operation == 'exists':return False
            raise FileNotFoundError(path) from exc
        finally:
            self._tasks.discard(task)

    async def close(self):
        self._closed = True
        # 生命周期由Shell执行任务拥有；关闭之前已收束原生回调。
        if self._tasks:
            raise RuntimeError('information callbacks must finish before closing the filesystem')


async def search_reader(read,sink,pattern,*,literal=False,ignore_case=False):
    """取消时收束 Python 回调与原生阻塞工作，工件关闭发生在返回之后。"""
    from society0_filesystem import search_reader as native_search
    callbacks=set()
    stopped=False
    def tracked(callback):
        async def call(*args):
            task=asyncio.current_task()
            callbacks.add(task)
            try:
                if stopped:raise asyncio.CancelledError()
                return await callback(*args)
            finally:callbacks.discard(task)
        return call
    async def sink_batch(items):
        for line,offset,data in items:await sink(line,offset,data)
    worker=asyncio.ensure_future(native_search(tracked(read),tracked(sink_batch),pattern,literal,ignore_case))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        stopped=True
        for task in tuple(callbacks):task.cancel()
        await asyncio.gather(*tuple(callbacks),return_exceptions=True)
        # 原生 Reader 的等待获取消错误后结束；仍等待工作线程确认退出。
        await asyncio.gather(worker,return_exceptions=True)
        raise
