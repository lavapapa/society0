"""将已有主体信息视图映射为动态只读 Bashkit 文件路径。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import PurePosixPath
from urllib.parse import quote

from .interaction import Unavailable


class InformationFiles:
    def __init__(self,information,scope):
        self.information=information;self.scope=scope;self._closed=False;self._tasks=set()

    def _check(self):
        if self._closed:raise RuntimeError('information filesystem is closed')
        self.scope.check_active()

    async def callback(self,operation,path):
        task=asyncio.current_task();self._tasks.add(task)
        try:
            self._check()
            if operation=='list':
                items=[];cursor=None
                while True:
                    page=await self.information.list_files(path,limit=100,cursor=cursor)
                    self._check()
                    for item in page.items:
                        name=PurePosixPath(item['path']).name;kind=item['kind']
                        items.append((name,kind,0,0o555 if kind=='directory' else 0o444,0,0))
                    cursor=page.next_cursor
                    if cursor is None:return items
            if operation=='read':
                # 原生 read_file 明确请求整文件；正文由同一授权入口获取。
                chunk=await self.information.read(path,size=sys.maxsize)
                revision=chunk.revision;parts=[chunk.data]
                while chunk.next_offset is not None:
                    chunk=await self.information.read(path,offset=chunk.next_offset,size=sys.maxsize)
                    if chunk.revision!=revision:raise ValueError('resource changed during file read')
                    parts.append(chunk.data)
                self._check();return b''.join(parts)
            try:stat=await self.information.stat(path)
            except Unavailable:
                if operation=='exists':return False
                raise
            self._check()
            if operation=='exists':return True
            if operation=='stat':return stat.kind,stat.total_bytes or 0,0o555 if stat.kind=='directory' else 0o444,0,0
            raise ValueError('information views do not contain symbolic links')
        except Unavailable as exc:raise FileNotFoundError(path) from exc
        finally:self._tasks.discard(task)

    async def close(self):
        self._closed=True
        for task in tuple(self._tasks):task.cancel()
        await asyncio.gather(*tuple(self._tasks),return_exceptions=True)
