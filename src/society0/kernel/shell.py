"""可选 Bashkit 适配器：共享信息按需读取，私有工作区与结果分别保存。"""
from __future__ import annotations

import asyncio
import base64
import codecs
import json
import os
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path

from bashkit import Bash, BuiltinResult

from .interaction import Actions, Information, InteractionScope, Query, Ref, ScopeClosed, Unavailable


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=lambda obj: asdict(obj) if is_dataclass(obj) else _unsupported(obj))


def _unsupported(value):
    raise TypeError(f'cannot encode {type(value).__name__}')


def _range(data, *, offset, total, encoding):
    if encoding == 'base64':
        consumed = len(data)
        content = base64.b64encode(data).decode('ascii')
    elif encoding == 'utf-8':
        decoder = codecs.getincrementaldecoder('utf-8')()
        content = decoder.decode(data, final=offset + len(data) == total)
        consumed = len(data) - len(decoder.getstate()[0])
    else:
        raise ValueError('encoding must be utf-8 or base64')
    end = offset + consumed
    return {'data': content, 'encoding': encoding, 'total_bytes': total,
            'next_offset': end if end < total else None}


@dataclass(frozen=True)
class ShellResult:
    session_id: str
    command_id: str
    stdout: str
    stderr: str
    exit_code: int
    stdout_truncated: bool
    stderr_truncated: bool
    stdout_ref: str
    stderr_ref: str
    receipts: tuple[str, ...]
    stdout_total_bytes: int
    stderr_total_bytes: int
    stdout_next_offset: int | None
    stderr_next_offset: int | None
    error: str | None = None


class ShellSession:
    def __init__(self, scope: InteractionScope, information: Information, actions: Actions | None = None,
                 *, bound_actions=None, result_dir, workspace_snapshot=None, preview_bytes=65536, result_reader=None, workspace=None):
        scope.check_active()
        if preview_bytes < 4:
            raise ValueError('preview_bytes must be at least 4')
        self.scope = scope
        if workspace is not None and workspace_snapshot is not None:
            raise ValueError('workspace service and standalone snapshot are mutually exclusive')
        self._workspace = workspace.open(scope) if workspace is not None else None
        if self._workspace is not None:
            workspace_snapshot=self._workspace.state
        self.information = information.bound(scope)
        if (actions is None) == (bound_actions is None):
            raise ValueError('provide actions or bound_actions')
        self.actions = bound_actions if bound_actions is not None else actions.bound(scope)
        self.preview_bytes = preview_bytes
        self._result_reader = result_reader
        root = Path(result_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.result_dir = Path(tempfile.mkdtemp(prefix='shell-', dir=root)).resolve()
        self.session_id = self.result_dir.name
        self._output_dir = self.result_dir / 'output'
        self._output_dir.mkdir()
        self._sequence = 0
        self._receipts = []
        self._fault = None
        self._callbacks = set()
        self._closed = False
        self._lock = asyncio.Lock()
        self._running = None
        options = dict(
            mounts=[{'host_path': str(self._output_dir), 'vfs_path': '/__output', 'writable': True}],
            allowed_mount_paths=[str(self._output_dir)],
            custom_builtins={name: self._builtin(name) for name in ('data', 'action', 'result')},
        )
        if self._workspace is not None and workspace_snapshot is None:
            options['cwd']='/workspace'
        self._bash = (Bash(**options) if workspace_snapshot is None
                      else Bash.from_snapshot(workspace_snapshot, **options))
        from bashkit import FileSystem
        from society0_filesystem import Overlay, callback_filesystem
        from .information_fs import InformationFiles
        self._world_files=InformationFiles(self.information,scope)
        self._bash.mount('/world',FileSystem.from_capsule(callback_filesystem(self._file_callback(self._world_files.callback))),read_only=True)
        if self._workspace is not None:
            self._overlay=Overlay(callback_filesystem(self._file_callback(self._workspace.callback)),*self._workspace.root)
            self._bash.mount('/workspace',FileSystem.from_capsule(self._overlay.capsule()))

    def _file_callback(self,callback):
        async def call(operation,path):
            try:
                self._check()
                if self._fault is not None:raise self._fault
                return await callback(operation,path)
            except (FileNotFoundError,IsADirectoryError,ValueError,Unavailable):raise
            except asyncio.CancelledError:raise
            except BaseException as exc:
                self._fault=exc
                raise
        return call

    def _check(self):
        if self._closed:
            raise ScopeClosed('shell session is closed')
        self.scope.check_active()

    def _save_receipt(self, value):
        name = f'receipt-{self._sequence}-{len(self._receipts)}.json'
        path = self.result_dir / name
        with path.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, default=lambda obj: asdict(obj))
            stream.flush()
            os.fsync(stream.fileno())
        reference = self.session_id + '/' + name
        self._receipts.append(reference)
        return reference

    def _builtin(self, kind):
        async def call(ctx):
            task = asyncio.current_task()
            self._callbacks.add(task)
            try:
                self._check()
                if self._fault is not None:
                    raise self._fault
                result = await self._dispatch(kind, list(ctx.argv))
                return _json(result) + '\n'
            except (Unavailable, ValueError, TypeError, KeyError) as exc:
                return BuiltinResult(stderr=str(exc) + '\n', exit_code=2)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self._fault = exc
                return BuiltinResult(stderr='interaction failed\n', exit_code=1)
            finally:
                self._callbacks.discard(task)
        return call

    async def _dispatch(self, kind, argv):
        if kind == 'action':
            operation, raw = argv
            request = json.loads(raw)
            target = Ref(**request.pop('target'))
            if operation == 'find':
                return await self.actions.find(target, **request)
            if operation == 'describe':
                return await self.actions.describe(target=target, **request)
            if operation == 'invoke':
                invocation = self.actions.invoke(target=target, **request)
                try:
                    result = await invocation
                    self._save_receipt(result)
                except BaseException as exc:
                    if not isinstance(exc, asyncio.CancelledError):
                        self._fault = exc
                    raise
                return result
            raise ValueError('unknown action operation')
        if kind == 'result':
            operation, ref, *tail = argv
            if operation != 'read':
                raise ValueError('unknown result operation')
            options = json.loads(tail[0]) if tail else {}
            if ref.startswith('shell-') and ref.split('/', 1)[0] != self.session_id and self._result_reader is not None:
                self._check()
                return self._result_reader(ref, **options)
            return self.read_result(ref, **options)
        operation, path, *tail = argv
        options = json.loads(tail[0]) if tail else {}
        if operation == 'list':
            return await self.information.list(path, **options)
        if operation == 'query':
            return await self.information.query(path, Query(**options))
        if operation == 'read':
            encoding = options.pop('encoding', 'utf-8')
            offset = options.get('offset', 0)
            size = options.get('size', 65536)
            if encoding == 'utf-8' and size < 4:
                raise ValueError('utf-8 read size must be at least 4 bytes')
            chunk = await self.information.read(path, **options)
            return {**_range(chunk.data, offset=offset, total=chunk.total_bytes, encoding=encoding),
                    'revision': chunk.revision, 'source': chunk.source}
        raise ValueError('unknown data operation')

    def read_result(self, reference, *, offset=0, size=65536, encoding='utf-8'):
        self._check()
        if offset < 0 or size < 1 or (encoding == 'utf-8' and size < 4):
            raise ValueError('invalid result byte range')
        relative = Path(reference)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid result reference')
        if relative.parts and relative.parts[0].startswith('shell-'):
            if relative.parts[0] != self.session_id:
                raise ValueError('result reference belongs to another session')
            relative = Path(*relative.parts[1:])
        path = self.result_dir / relative
        with path.open('rb') as stream:
            total = os.fstat(stream.fileno()).st_size
            stream.seek(offset)
            data = stream.read(size)
        return _range(data, offset=offset, total=total, encoding=encoding)

    async def execute(self, script):
        async with self._lock:
            self._check()
            self._running = asyncio.current_task()
            self._sequence += 1
            command_id = str(self._sequence)
            stdout_ref, stderr_ref = f'output/{command_id}.stdout', f'output/{command_id}.stderr'
            first_receipt = len(self._receipts)
            # 同一命令组保留变量与 cwd；完整输出写到本会话拥有的磁盘目录。
            wrapped = '{\n' + script + '\n} > /__output/' + command_id + '.stdout 2> /__output/' + command_id + '.stderr'
            try:
                result = await self._bash.execute(wrapped)
                if self._fault is not None:
                    raise self._fault
                self._check()
                output = []
                truncated = []
                totals = []
                offsets = []
                for reference, fallback, native_truncated in (
                    (stdout_ref, result.stdout, result.stdout_truncated),
                    (stderr_ref, result.stderr, result.stderr_truncated),
                ):
                    path = self.result_dir / reference
                    # 语法错误可能发生在重定向建立前。
                    if not path.exists():
                        path.write_text(fallback, encoding='utf-8')
                    chunk = self.read_result(reference, size=self.preview_bytes)
                    output.append(chunk['data'])
                    totals.append(chunk['total_bytes'])
                    offsets.append(chunk['next_offset'])
                    truncated.append(native_truncated or chunk['next_offset'] is not None)
                return ShellResult(self.session_id, command_id, *output, result.exit_code, *truncated,
                                   self.session_id + '/' + stdout_ref, self.session_id + '/' + stderr_ref, tuple(self._receipts[first_receipt:]),
                                   *totals, *offsets, result.error)
            except asyncio.CancelledError:
                self._closed = True
                await self._cancel_callbacks()
                await self._world_files.close()
                if self._workspace is not None:await self._workspace.close()
                raise
            finally:
                self._running = None

    async def _cancel_callbacks(self):
        callbacks = tuple(self._callbacks)
        for task in callbacks:
            task.cancel()
        if callbacks:
            await asyncio.gather(*callbacks, return_exceptions=True)

    def snapshot(self):
        self._check()
        if self._running is not None:
            raise RuntimeError('cannot snapshot a running shell')
        if self._workspace is not None:raise ValueError('persistent workspace uses save_workspace')
        return self._bash.snapshot()

    @property
    def has_workspace(self):
        return self._workspace is not None

    def save_workspace(self):
        self._check()
        if self._running is not None:raise RuntimeError('cannot save a running shell')
        if self._workspace is None:raise ValueError('shell has no persistent workspace')
        self._workspace.save(self._bash.snapshot(exclude_filesystem=True),self._overlay.changes())

    async def aclose(self):
        self._closed = True
        if self._running is not None and self._running is not asyncio.current_task():
            self._running.cancel()
            await asyncio.gather(self._running, return_exceptions=True)
        await self._cancel_callbacks()
        await self._world_files.close()
        if self._workspace is not None:await self._workspace.close()
        self._bash = None
        if self._workspace is not None:self._overlay=None
