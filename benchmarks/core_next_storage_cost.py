"""独立的 StageStore 成本归属实验；timing 无 patch，profile 使用进程级同步仪器。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import time
from unittest.mock import patch

import apsw
from society0.kernel import storage
from benchmarks.core_next_checkpoint_baseline import checkpoint_baseline
from benchmarks.core_next_persistence_probe import SyncVFS


class CostProbe:
    """仅用于独占 worker。作用域内不可 await；ExitStack 释放所有 patch 与默认 VFS。"""
    def __init__(self):
        self.costs = {}
        self.active = None
        self.bookkeeping_seconds = 0.0

    def add(self, name, value=1):
        start = time.perf_counter()
        if self.active is not None:
            row = self.costs.setdefault(self.active, {})
            row[name] = row.get(name, 0) + value
        self.bookkeeping_seconds += time.perf_counter() - start

    def maximum(self, name, value):
        if self.active is not None:
            row = self.costs.setdefault(self.active, {})
            row[name] = max(row.get(name, 0), value)

    @contextmanager
    def segment(self, name):
        start = time.perf_counter()
        self.add(name + '_count')
        try:
            yield
        finally:
            self.add(name + '_seconds', time.perf_counter() - start)

    @contextmanager
    def stage(self, name):
        previous = self.active
        self.active = name
        before = dict(self.vfs.stats)
        try:
            yield
        finally:
            self.add('sqlite_sync_count', self.vfs.stats['count'] - before['count'])
            self.add('sqlite_sync_seconds', self.vfs.stats['seconds'] - before['seconds'])
            self.active = previous

    def __enter__(self):
        probe = self
        self.stack = ExitStack()
        self.vfs = SyncVFS()
        self.stack.callback(self.vfs.unregister)
        self.stack.callback(apsw.set_default_vfs, self.vfs.base)
        native_session, native_changeset = apsw.Session, apsw.Changeset
        native_connection, native_fsync = apsw.Connection, os.fsync
        native_zstd = storage.zstd.ZstdFile
        native_read_text, native_json = Path.read_text, storage._json

        def read_text(path, *args, **kwargs):
            if path.parent.name == 'steps' and path.suffix == '.json':
                with probe.segment('descriptor_read'):
                    result = native_read_text(path, *args, **kwargs)
                # 完整读取不可变描述符；字节取文件长度，避免为计数再次编码正文。
                probe.add('descriptor_read_bytes', path.stat().st_size)
                return result
            return native_read_text(path, *args, **kwargs)

        def publish_json(path, *args, **kwargs):
            name = 'descriptor_publish' if path.parent.name == 'steps' else 'manifest_publish'
            with probe.segment(name):
                return native_json(path, *args, **kwargs)

        class Session:
            def __init__(self, *args, **kwargs):
                with probe.segment('session_create'):
                    self.native = native_session(*args, **kwargs)
            def __getattr__(self, name):
                return getattr(self.native, name)
            def attach(self, table):
                with probe.segment('session_attach'):
                    return self.native.attach(table)
            def changeset_stream(self, sink):
                # APSW 暴露原生 sqlite3session_memory_used；读取发生在导出之前。
                start = time.perf_counter()
                memory = self.native.memory_used
                probe.add('session_memory_samples')
                probe.maximum('session_memory_max_bytes', memory)
                probe.add('memory_sampling_seconds', time.perf_counter() - start)
                def emit(data):
                    probe.add('export_write_count')
                    probe.add('export_bytes', len(data))
                    return sink(data)
                with probe.segment('export'):
                    return self.native.changeset_stream(emit)

        class Changeset:
            @staticmethod
            def apply(reader, *args, **kwargs):
                def read(size):
                    with probe.segment('apply_read'):
                        data = reader(size)
                    probe.add('apply_read_bytes', len(data))
                    return data
                with probe.segment('apply'):
                    return native_changeset.apply(read, *args, **kwargs)

        def connect(*args, **kwargs):
            probe.add('connection_count')
            connection = native_connection(*args, **kwargs)
            def trace(cursor, sql, bindings):
                if sql.startswith('PRAGMA table_info('):
                    probe.add('table_info_count')
                if 'FROM sqlite_schema' in sql:
                    probe.add('schema_query_count')
                return True
            connection.set_exec_trace(trace)
            return connection

        def fsync(fd):
            directory = stat.S_ISDIR(os.fstat(fd).st_mode)
            with probe.segment('python_fsync'):
                if directory:
                    with probe.segment('directory_sync'):
                        return native_fsync(fd)
                return native_fsync(fd)

        class CompressedReader:
            def __init__(self, stream):
                self.stream = stream
            def read(self, size=-1):
                with probe.segment('compressed_read'):
                    data = self.stream.read(size)
                probe.add('compressed_read_bytes', len(data))
                return data
            def __getattr__(self, name):
                return getattr(self.stream, name)

        class CompressedWriter:
            def __init__(self, stream):
                self.stream = stream
            def write(self, data):
                with probe.segment('compression_write'):
                    return self.stream.write(data)

        @contextmanager
        def zstd_file(file, mode='rb', **kwargs):
            if mode == 'rb' and isinstance(file, Path):
                with file.open('rb') as stream:
                    with native_zstd(CompressedReader(stream), mode, **kwargs) as compressed:
                        yield compressed
            else:
                compressed = native_zstd(file, mode, **kwargs)
                try:
                    yield CompressedWriter(compressed)
                finally:
                    with probe.segment('compression_finish'):
                        compressed.close()

        def wrap(name, original):
            def call(*args, **kwargs):
                with probe.segment(name):
                    result = original(*args, **kwargs)
                if name == 'tables':
                    probe.add('tables_returned', len(result))
                elif name == 'artifact_sync':
                    probe.add('artifact_references', len(result))
                elif name == 'backup':
                    probe.add('backup_bytes', Path(args[1]).stat().st_size)
                return result
            return call

        try:
            for target, replacement in (
                ('apsw.Session', Session), ('apsw.Changeset', Changeset),
                ('apsw.Connection', connect), ('os.fsync', fsync),
                ('society0.kernel.storage.zstd.ZstdFile', zstd_file),
                ('pathlib.Path.read_text', read_text),
                ('society0.kernel.storage._json', publish_json),
            ):
                self.stack.enter_context(patch(target, replacement))
            for symbol, name in (('_schema', 'schema'), ('_tables', 'tables'),
                                 ('_backup', 'backup')):
                self.stack.enter_context(patch.object(storage, symbol, wrap(name, getattr(storage, symbol))))
            for symbol, name in (('_start_session', 'session_rebuild'),
                                 ('_artifacts', 'artifact_sync'),
                                 ('_validate_references', 'validate_references')):
                original = getattr(storage.StageStore, symbol)
                replacement = wrap(name, original)
                if symbol == '_validate_references':
                    replacement = staticmethod(replacement)
                self.stack.enter_context(patch.object(storage.StageStore, symbol, replacement))
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


def storage_cost(path, *, mode='timing', **workload):
    if mode not in ('timing', 'profile'):
        raise ValueError('mode must be timing or profile')
    workload.setdefault('artifacts_per_step', 0)
    if mode == 'profile':
        with CostProbe() as probe:
            result = checkpoint_baseline(path, observer=probe.stage, **workload)
        result['costs'] = probe.costs
        result['instrumentation'] = dict(
            enabled=True, bookkeeping_seconds=probe.bookkeeping_seconds,
            accounting='bookkeeping is a measured lower bound; wrapper/trace/VFS overhead requires paired timing workers',
            segments='inclusive and nested; do not sum; export includes compression, apply includes reads/decompression',
            session_memory='native Session.memory_used sampled before each export; max is across samples, not process RSS')
    else:
        result = checkpoint_baseline(path, **workload)
        result['costs'] = {}
        result['instrumentation'] = dict(enabled=False)
    result['mode'] = mode
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='新目录，保留所有原始运行工件')
    parser.add_argument('--mode', choices=('timing', 'profile'), default='timing')
    parser.add_argument('--history-rows', type=int, default=1000)
    parser.add_argument('--active-rows', type=int, default=8)
    parser.add_argument('--steps', type=int, default=8)
    parser.add_argument('--delta-rows', type=int, default=8)
    parser.add_argument('--body-bytes', type=int, default=128)
    parser.add_argument('--artifacts-per-step', type=int, default=0)
    parser.add_argument('--extra-tables', type=int, default=0)
    parser.add_argument('--position-body-bytes', type=int, default=0)
    parser.add_argument('--position-body-layout', choices=('inline', 'detached'), default='inline')
    args = vars(parser.parse_args())
    output = args.pop('output')
    result = storage_cost(output, **args)
    root = Path(__file__).resolve().parents[1]
    result['source'] = dict(
        commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        dirty=bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip()),
        executable=sys.executable, module=storage.__file__)
    result['environment'] = dict(python=platform.python_version(), platform=platform.platform(),
                                 apsw=apsw.apswversion(), sqlite=apsw.sqlitelibversion())
    result['limitations'] = [
        'synthetic environment-only; no model or memory endpoint',
        'filesystem cache is not cleared; cold_open means new writer, not OS-cold disk',
        'position body uses deterministic pseudo-random bytes shared across actors; fact body remains repeated x',
        'python fsync and SQLite VFS xSync are separate, inclusive measurements',
        'profile uses process-global synchronous patches; run in an exclusive worker; never await inside CostProbe',
        'timing/profile have identical semantic verification outside measured complete/open/restore stages',
        'no cProfile results are used as speed evidence',
    ]
    report = output / 'report.json'
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(report=str(report))))


if __name__ == '__main__':
    main()
