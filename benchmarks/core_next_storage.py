"""标准库小试验；固定领域表，无正式产品依赖或通用存储接口。"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import gzip
from itertools import zip_longest
import json
import os
from pathlib import Path
import platform
import random
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time


def rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Store:
    """单写者 SQLite 当前投影 + 先耐久 JSONL 分段 + SQL 完成记录。"""
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path / 'current.sqlite', isolation_level=None)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('PRAGMA wal_autocheckpoint=0')
        self.db.execute('PRAGMA cache_size=-2048')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS records(id INTEGER PRIMARY KEY,owner TEXT,active INTEGER,value INTEGER);
            CREATE INDEX IF NOT EXISTS active_owner ON records(owner,id) WHERE active=1;
            CREATE TABLE IF NOT EXISTS counts(owner TEXT PRIMARY KEY,total INTEGER);
            CREATE TABLE IF NOT EXISTS complete(revision INTEGER PRIMARY KEY,segment TEXT NOT NULL);
        ''')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()

    def seed(self, n):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.executemany('INSERT INTO records VALUES(?,?,?,?)',
                                ((i, 'alice' if i < 32 or i >= 64 and i % 2 == 0 else 'bob', int(i < 64), i) for i in range(n)))
            self.db.executemany('INSERT INTO counts VALUES(?,32)', [('alice',), ('bob',)])
            with open(self.path / 'seed.jsonl', 'w') as f:
                for i in range(n):
                    f.write(json.dumps({'id': i, 'kind': 'created', 'value': i}, separators=(',', ':')) + '\n')
                f.flush()
                os.fsync(f.fileno())
            sync_directory(self.path)
            self.db.execute('INSERT INTO complete VALUES(0,?)', ('seed.jsonl',))
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')

    def complete(self):
        return self.db.execute('SELECT revision,segment FROM complete ORDER BY revision DESC LIMIT 1').fetchone()

    def facts(self):
        """读取最新完整步骤的事实段；不隐式重放历史。"""
        with open(self.path / self.complete()[1]) as f:
            for line in f:
                yield json.loads(line)

    def stage(self, updates, facts, fail_at=None, active_changes=()):
        def checkpoint(name):
            if fail_at == 'exit_' + name:
                os._exit(73)
            if fail_at == name:
                raise RuntimeError(name)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            revision = self.complete()[0] + 1
            self.db.execute('SAVEPOINT action')
            self.db.executemany('UPDATE records SET value=? WHERE id=?', ((value, key) for key, value in updates))
            for key, active in active_changes:
                owner, previous = self.db.execute('SELECT owner,active FROM records WHERE id=?', (key,)).fetchone()
                self.db.execute('UPDATE records SET active=? WHERE id=?', (int(active), key))
                self.db.execute('UPDATE counts SET total=total+? WHERE owner=?', (int(active) - previous, owner))
            checkpoint('after_update')
            self.db.execute('RELEASE action')
            # 失败文件保留为未引用工件，重试不覆盖它们。
            with tempfile.NamedTemporaryFile(dir=self.path, suffix='.pending', mode='w', delete=False) as f:
                for fact in facts:
                    f.write(json.dumps(fact, separators=(',', ':')) + '\n')
                f.flush()
                os.fsync(f.fileno())
                pending = Path(f.name)
            target = pending.with_suffix('.jsonl')
            os.replace(pending, target)
            sync_directory(self.path)
            checkpoint('after_file')
            self.db.execute('INSERT INTO complete VALUES(?,?)', (revision, target.name))
            checkpoint('before_commit')
            self.db.execute('COMMIT')
            checkpoint('after_commit')
            return target.stat().st_size
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    @contextmanager
    def view(self, owner):
        db = sqlite3.connect(f'file:{self.path / "current.sqlite"}?mode=ro', uri=True, isolation_level=None)
        db.execute('PRAGMA cache_size=-512')
        db.execute('BEGIN')
        revision = db.execute('SELECT max(revision) FROM complete').fetchone()[0]
        try:
            yield View(db, owner, revision, str(self.path.resolve()))
        finally:
            db.execute('ROLLBACK')
            db.close()

    def measure_activity(self):
        counters = {'query_vm_steps': 0, 'stage_vm_steps': 0}
        with self.view('alice') as view:
            def query_count():
                counters['query_vm_steps'] += 1
                return 0
            view.db.set_progress_handler(query_count, 1)
            page = view.page(limit=16)
            view.db.set_progress_handler(None, 0)
        def stage_count():
            counters['stage_vm_steps'] += 1
            return 0
        self.db.set_progress_handler(stage_count, 1)
        before = self.db.total_changes
        delta_bytes = self.stage([(i, i + 1) for i in range(8)], [{'id': i, 'value': i + 1} for i in range(8)])
        self.db.set_progress_handler(None, 0)
        return dict(counters, rows_returned=len(page['rows']), sql_changed_rows=self.db.total_changes - before, delta_bytes=delta_bytes)


class View:
    def __init__(self, db, owner, revision, identity):
        self.db, self.owner, self.revision, self.identity = db, owner, revision, identity

    def page(self, limit=16, cursor=None):
        last = -1
        if cursor is not None:
            if cursor[:3] != (self.identity, self.owner, self.revision):
                raise ValueError('cursor scope/revision mismatch')
            last = cursor[3]
        rows = self.db.execute('SELECT id,value FROM records INDEXED BY active_owner WHERE owner=? AND active=1 AND id>? ORDER BY id LIMIT ?', (self.owner, last, limit + 1)).fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        count = self.db.execute('SELECT total FROM counts WHERE owner=?', (self.owner,)).fetchone()
        return {'revision': self.revision, 'scope': self.owner, 'total': count[0] if count else 0,
                'rows': [{'id': key, 'value': value} for key, value in rows],
                'next': (self.identity, self.owner, self.revision, rows[-1][0]) if more else None}


def make_blocks(path, blocks=32, block_bytes=1048576):
    rng = random.Random(71)
    with open(path, 'wb') as f:
        for block in range(blocks):
            raw = bytearray()
            while len(raw) < block_bytes:
                row = {'id': rng.randrange(10**9), 'owner': rng.randrange(1000), 'amount': rng.random(),
                       'kind': ['trade', 'production', 'payment'][rng.randrange(3)],
                       'memo': ''.join(rng.choices('abcdef0123456789', k=64))}
                raw.extend(json.dumps(row, separators=(',', ':')).encode() + b'\n')
            f.write(raw[:block_bytes])


def compressed_blocks(path, workers, block_bytes, window, stats):
    with open(path, 'rb') as f:
        if not workers:
            while raw := f.read(block_bytes):
                stats['max_pending_raw_bytes'] = max(stats['max_pending_raw_bytes'], len(raw))
                stats['max_pending_tasks'] = 1
                yield raw, gzip.compress(raw, compresslevel=3, mtime=0)
            return
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = deque()
            finished = False
            while pending or not finished:
                while len(pending) < window and not finished:
                    raw = f.read(block_bytes)
                    if not raw:
                        finished = True
                        break
                    pending.append((raw, pool.submit(gzip.compress, raw, compresslevel=3, mtime=0)))
                stats['max_pending_raw_bytes'] = max(stats['max_pending_raw_bytes'], sum(len(item[0]) for item in pending))
                stats['max_pending_tasks'] = max(stats['max_pending_tasks'], len(pending))
                if pending:
                    raw, future = pending.popleft()
                    yield raw, future.result()


def compress_probe(path, workers=0, block_bytes=1048576, window=8):
    stats = {'max_pending_raw_bytes': 0, 'max_pending_tasks': 0}
    before = rss()
    wall, cpu = time.perf_counter(), time.process_time()
    output = sum(len(encoded) for _, encoded in compressed_blocks(path, workers, block_bytes, window, stats))
    result = dict(stats, workers=workers, compressed_bytes=output, raw_bytes=Path(path).stat().st_size,
                  wall_seconds=time.perf_counter() - wall, cpu_seconds=time.process_time() - cpu,
                  peak_rss_bytes=rss(), peak_above_prior_highwater_bytes=max(0, rss() - before))
    count = 0
    for raw, encoded in compressed_blocks(path, workers, block_bytes, window, stats):
        assert gzip.decompress(encoded) == raw
        count += 1
    result['verified_blocks'] = count
    return result


def storage_probe(n):
    with tempfile.TemporaryDirectory() as path, Store(path) as store:
        wall, cpu = time.perf_counter(), time.process_time()
        store.seed(n)
        seed = {'wall_seconds': time.perf_counter() - wall, 'cpu_seconds': time.process_time() - cpu}
        metrics = store.measure_activity()
        wall, cpu = time.perf_counter(), time.process_time()
        for _ in range(100):
            with store.view('alice') as view:
                view.page()
        metrics['query_100_wall_seconds'] = time.perf_counter() - wall
        metrics['query_100_cpu_seconds'] = time.process_time() - cpu
        timings = []
        for step in range(20):
            wall, cpu = time.perf_counter(), time.process_time()
            store.stage([(i, i + 100 + step) for i in range(8)], [{'id': i, 'value': i + 100 + step} for i in range(8)])
            timings.append({'wall_seconds': time.perf_counter() - wall, 'cpu_seconds': time.process_time() - cpu})
        metrics['query_plan'] = store.db.execute("EXPLAIN QUERY PLAN SELECT id,value FROM records INDEXED BY active_owner WHERE owner='alice' AND active=1 AND id>0 ORDER BY id LIMIT 16").fetchall()
        return dict(history_records=n, active_records=64, seed=seed, activity=metrics, stages=timings,
                    disk_bytes={p.name: p.stat().st_size for p in Path(path).iterdir() if p.name.startswith('current.') or p.name == 'seed.jsonl'},
                    segment_bytes=sum(p.stat().st_size for p in Path(path).glob('*.jsonl') if p.name != 'seed.jsonl'), peak_rss_bytes=rss())


def transaction_probe():
    """原生事务边界对照；明确展示水位无法还原已覆盖 current。"""
    with tempfile.TemporaryDirectory() as directory, Store(directory) as store:
        store.seed(1000)
        result = {}
        store.db.execute('BEGIN IMMEDIATE')
        wall, cpu = time.perf_counter(), time.process_time()
        for value in range(1, 21):
            store.db.execute('SAVEPOINT action')
            store.db.execute('UPDATE records SET value=? WHERE id=0', (value,))
            store.db.execute('RELEASE action')
        result['twenty_savepoints_wall_seconds'] = time.perf_counter() - wall
        result['twenty_savepoints_cpu_seconds'] = time.process_time() - cpu
        result['writer_value_before_rollback'] = store.db.execute('SELECT value FROM records WHERE id=0').fetchone()[0]
        with store.view('alice') as view:
            result['uncommitted_reader_value'] = view.page(limit=1)['rows'][0]['value']
        store.db.execute('ROLLBACK')
        result['value_after_rollback'] = store.db.execute('SELECT value FROM records WHERE id=0').fetchone()[0]
        # 反例：已经 COMMIT 的 current 不能靠旧 complete 水位恢复。
        store.db.execute('BEGIN IMMEDIATE')
        store.db.execute('UPDATE records SET value=21 WHERE id=0')
        store.db.execute('COMMIT')
        result['current_after_stage_commit'] = store.db.execute('SELECT value FROM records WHERE id=0').fetchone()[0]
        result['recoverable_value_not_in_current'] = store.complete()[0] == 0 and result['current_after_stage_commit'] != 0
        with store.view('alice') as pinned:
            pinned.page(limit=1)
            for value in range(30, 50):
                store.stage([(0, value)], [{'value': value}])
            result['pinned_checkpoint'] = store.db.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone()
            result['pinned_wal_bytes'] = (store.path / 'current.sqlite-wal').stat().st_size
            result['pinned_reader_value'] = pinned.page(limit=1)['rows'][0]['value']
        store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        result['released_wal_bytes'] = (store.path / 'current.sqlite-wal').stat().st_size
        return result


def session_probe(layout, body_bytes=1048576, rows=16, touched=1):
    """APSW 可选原生实验，依赖仅安装在独立临时虚拟环境。"""
    import apsw
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        db = apsw.Connection(str(path / 'current.sqlite'))
        target = apsw.Connection(str(path / 'root.sqlite'))
        db.execute('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA cache_size=-2048')
        if layout == 'wide':
            schema = 'CREATE TABLE account(id INTEGER PRIMARY KEY,balance INTEGER,body TEXT)'
        elif layout == 'split':
            schema = 'CREATE TABLE account(id INTEGER PRIMARY KEY,balance INTEGER); CREATE TABLE detail(id INTEGER PRIMARY KEY,body TEXT)'
        else:
            schema = 'CREATE TABLE account(id INTEGER PRIMARY KEY,body TEXT)'
        db.execute(schema)
        body = 'x' * body_bytes
        db.execute('BEGIN')
        for key in range(rows):
            if layout == 'wide':
                db.execute('INSERT INTO account VALUES(?,0,?)', (key, body))
            elif layout == 'split':
                db.execute('INSERT INTO account VALUES(?,0)', (key,))
                db.execute('INSERT INTO detail VALUES(?,?)', (key, body))
            else:
                db.execute('INSERT INTO account VALUES(?,?)', (key, json.dumps({'balance': 0, 'body': body})))
        db.execute('COMMIT')
        backup_wall, backup_cpu = time.perf_counter(), time.process_time()
        with target.backup('main', db, 'main') as backup:
            backup.step(-1)
        backup_metrics = {'wall_seconds': time.perf_counter() - backup_wall, 'cpu_seconds': time.process_time() - backup_cpu, 'bytes': (path / 'root.sqlite').stat().st_size}
        session = apsw.Session(db, 'main')
        session.attach()
        before = rss()
        wall, cpu = time.perf_counter(), time.process_time()
        def update(key, value):
            if layout in ('wide', 'split'):
                db.execute('UPDATE account SET balance=? WHERE id=?', (value, key))
            else:
                db.execute("UPDATE account SET body=json_set(body,'$.balance',?) WHERE id=?", (value, key))
        for value in (1, 2):
            db.execute('BEGIN')
            if layout == 'append':
                for key in range(touched):
                    db.execute('INSERT INTO account VALUES(?,?)', (rows + (value - 1) * touched + key, body))
            else:
                for key in range(touched):
                    update(key, value)
            db.execute('COMMIT')
        db.execute('BEGIN')
        update(rows - 1, 999)
        db.execute('ROLLBACK')
        capture_memory = session.memory_used
        captured_cpu, captured_wall = time.process_time() - cpu, time.perf_counter() - wall
        block_sizes = []
        wall, cpu = time.perf_counter(), time.process_time()
        with open(path / 'step1.changeset', 'wb') as f:
            def output(data):
                block_sizes.append(len(data))
                f.write(data)
            session.changeset_stream(output)
            f.flush()
            os.fsync(f.fileno())
        result = {'layout': layout, 'body_bytes': body_bytes, 'seed_rows': rows, 'touched_rows': touched,
                  'apsw': apsw.apswversion(), 'sqlite': apsw.sqlitelibversion(),
                  'root_backup': backup_metrics, 'capture_memory_bytes': capture_memory, 'capture_wall_seconds': captured_wall,
                  'capture_cpu_seconds': captured_cpu, 'export_wall_seconds': time.perf_counter() - wall,
                  'export_cpu_seconds': time.process_time() - cpu,
                  'changeset_bytes': (path / 'step1.changeset').stat().st_size,
                  'stream_max_block_bytes': max(block_sizes, default=0), 'stream_blocks': len(block_sizes),
                  'peak_rss_bytes': rss(), 'peak_above_prior_highwater_bytes': max(0, rss() - before),
                  'session_survives_transactions': True, 'committed_updates': 2}
        with open(path / 'step1.changeset', 'rb') as f:
            result['changeset_rows'] = sum(1 for _ in apsw.Changeset.iter(f.read))
        session.close()
        wall, cpu = time.perf_counter(), time.process_time()
        with open(path / 'step1.changeset', 'rb') as f:
            apsw.Changeset.apply(f.read, target)
        result['apply_wall_seconds'] = time.perf_counter() - wall
        result['apply_cpu_seconds'] = time.process_time() - cpu
        def equal():
            names = ['account', 'detail'] if layout == 'split' else ['account']
            for name in names:
                if any(left != right for left, right in zip_longest(db.execute(f'SELECT * FROM {name} ORDER BY id'), target.execute(f'SELECT * FROM {name} ORDER BY id'))):
                    return False
            return True
        result['equal_after_apply'] = equal()
        column = 'balance' if layout in ('wide', 'split') else "json_extract(body,'$.balance')"
        result['rolled_back_row_value'] = db.execute(f'SELECT {column} FROM account WHERE id=?', (rows - 1,)).fetchone()[0]
        next_session = apsw.Session(db, 'main')
        next_session.attach()
        with db:
            update(0, 3)
        with open(path / 'step2.changeset', 'wb') as f:
            def output_next(data):
                f.write(data)
            next_session.changeset_stream(output_next)
        next_session.close()
        with open(path / 'step2.changeset', 'rb') as f:
            apsw.Changeset.apply(f.read, target)
        result['second_step_equal'] = equal()
        result['peak_after_apply_and_verify_bytes'] = rss()
        result['disk_bytes_before_close'] = {p.name: p.stat().st_size for p in path.iterdir()}
        target.close()
        db.close()
        return result


def session_child(directory, phase):
    """故障子进程：current 先短事务可见，完整发布另由 native changeset 清单决定。"""
    import apsw
    path = Path(directory)
    db = apsw.Connection(str(path / 'current.sqlite'))
    db.execute('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE account(id INTEGER PRIMARY KEY,balance INTEGER); INSERT INTO account VALUES(0,0)')
    root = apsw.Connection(str(path / 'root.sqlite'))
    with root.backup('main', db, 'main') as backup:
        backup.step(-1)
    root.close()
    with open(path / 'root.sqlite', 'rb') as f:
        os.fsync(f.fileno())
    def publish(step, session):
        with open(path / f'step{step}.changeset', 'wb') as f:
            def output(data):
                f.write(data)
            session.changeset_stream(output)
            f.flush()
            os.fsync(f.fileno())
        sync_directory(path)
        if step == 2 and phase == 'after_export':
            os._exit(73)
        with open(path / 'complete.pending', 'w') as f:
            json.dump({'complete_step': step}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(path / 'complete.pending', path / 'complete.json')
        sync_directory(path)
    first = apsw.Session(db, 'main')
    first.attach()
    for value in (1, 2):
        with db:
            db.execute('UPDATE account SET balance=? WHERE id=0', (value,))
    publish(1, first)
    first.close()
    second = apsw.Session(db, 'main')
    second.attach()
    with db:
        db.execute('UPDATE account SET balance=3 WHERE id=0')
    if phase == 'before_export':
        os._exit(73)
    publish(2, second)
    os._exit(73)


def session_recovery_probe(phase):
    import apsw
    with tempfile.TemporaryDirectory() as directory:
        code = "import importlib.util,sys; s=importlib.util.spec_from_file_location('probe',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); m.session_child(sys.argv[2],sys.argv[3])"
        child = subprocess.run([sys.executable, '-c', code, __file__, directory, phase], capture_output=True)
        if child.returncode != 73:
            raise RuntimeError(child.stderr.decode())
        path = Path(directory)
        current = apsw.Connection(str(path / 'current.sqlite'))
        dirty = current.execute('SELECT balance FROM account').fetchone()[0]
        fresh = apsw.Session(current, 'main')
        fresh.attach()
        empty = fresh.changeset() == b''
        fresh.close()
        current.close()
        root = apsw.Connection(str(path / 'root.sqlite'))
        restored = apsw.Connection(str(path / 'restored.sqlite'))
        with restored.backup('main', root, 'main') as backup:
            backup.step(-1)
        root.close()
        complete = json.loads((path / 'complete.json').read_text())['complete_step']
        for step in range(1, complete + 1):
            with open(path / f'step{step}.changeset', 'rb') as f:
                apsw.Changeset.apply(f.read, restored)
        result = {'phase': phase, 'child_exit': child.returncode, 'dirty_current': dirty,
                  'complete_step': complete, 'restored': restored.execute('SELECT balance FROM account').fetchone()[0],
                  'fresh_session_empty': empty}
        restored.close()
        return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', choices=['storage', 'compression', 'session'])
    parser.add_argument('--session', action='store_true')
    parser.add_argument('--layout', default='wide')
    parser.add_argument('--body-bytes', type=int, default=1048576)
    parser.add_argument('--touched', type=int, default=1)
    parser.add_argument('--n', type=int, default=1000)
    parser.add_argument('--workers', type=int, default=0)
    parser.add_argument('--input')
    parser.add_argument('--output', default='research/core-next/storage-experiment-results-20261004.json')
    args = parser.parse_args()
    if args.case:
        result = session_probe(args.layout, args.body_bytes, touched=args.touched) if args.case == 'session' else storage_probe(args.n) if args.case == 'storage' else compress_probe(args.input, args.workers)
        print(json.dumps(result))
        return
    def run(*options):
        return json.loads(subprocess.check_output([sys.executable, __file__, *options], text=True))
    if args.session:
        result = {'layouts': [run('--case', 'session', '--layout', layout, '--body-bytes', str(size), '--touched', str(touched)) for layout in ('wide', 'split', 'json', 'append') for size in (1024, 1048576) for touched in (1, 8)], 'recovery': [session_recovery_probe(phase) for phase in ('before_export', 'after_export', 'after_marker')]}
        Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
        return
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'blocks'
        make_blocks(path)
        result = {'environment': {'python': sys.version, 'platform': platform.platform(), 'sqlite': sqlite3.sqlite_version,
                                  'cpu_count': os.cpu_count(), 'temp_directory': tempfile.gettempdir(), 'rss_units': 'bytes; ru_maxrss normalized'},
                  'transaction': transaction_probe(),
                  'storage': [run('--case', 'storage', '--n', str(n)) for n in (1000, 10000, 100000)],
                  'compression': [dict(run('--case', 'compression', '--input', str(path), '--workers', str(w)), repetition=r)
                                  for r in range(3) for w in ((0, 2, 4, 8) if r % 2 == 0 else (8, 4, 2, 0))]}
    Path(args.output).write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
