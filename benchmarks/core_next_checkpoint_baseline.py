"""StageStore 公开路径的检查点基线；运行目录与计时/计数报告保留在指定输出目录。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

import apsw
import society0
from society0.kernel.storage import StageStore

SCHEMA = (
    'CREATE TABLE cold_history(id INTEGER PRIMARY KEY, body BLOB NOT NULL)',
    'CREATE TABLE current_positions(id INTEGER PRIMARY KEY, value INTEGER NOT NULL)',
    'CREATE TABLE step_facts(id INTEGER PRIMARY KEY, actor INTEGER NOT NULL, amount INTEGER NOT NULL, body BLOB NOT NULL)',
)


def max_rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(value if sys.platform == 'darwin' else value * 1024)


@contextmanager
def timed(target, name):
    wall, cpu = time.perf_counter(), time.process_time()
    try:
        yield
    finally:
        target[name] = dict(wall_seconds=time.perf_counter() - wall,
                            cpu_seconds=time.process_time() - cpu)


def disk_usage(path):
    """逻辑字节与分配字节分别记录；同一分类中硬链接只计一次物理分配。"""
    result = {}
    for name in ('root', 'current', 'changesets', 'artifacts', 'steps'):
        files = ([path / 'root.sqlite'] if name == 'root' else
                 [path / 'current.sqlite', path / 'current.sqlite-wal', path / 'current.sqlite-shm']
                 if name == 'current' else (path / name).rglob('*'))
        logical, allocated, count, seen = 0, 0, 0, set()
        for file in files:
            if not file.is_file():
                continue
            stat = file.stat()
            logical += stat.st_size
            count += 1
            identity = (stat.st_dev, stat.st_ino)
            if identity not in seen:
                seen.add(identity)
                allocated += stat.st_blocks * 512
        result[name] = dict(files=count, logical_bytes=logical, allocated_bytes=allocated)
    return result


def expected_positions(active_rows, steps, delta_rows):
    values = [0] * active_rows
    for number in range(steps * delta_rows):
        values[number % active_rows] += 1
    return values


def verify(reader, history_rows, active_rows, steps, delta_rows, body, *, continued=False):
    """正文逐行核对，在测量阶段之外执行；不构造完整世界副本。"""
    def check(view):
        history_count = 0
        for identifier, actual_body in view.iter_query('SELECT id,body FROM cold_history ORDER BY id'):
            assert identifier == history_count and actual_body == body
            history_count += 1
        assert history_count == history_rows
        positions = view.query('SELECT id,value FROM current_positions ORDER BY id',
                               max_rows=active_rows)
        expected = expected_positions(active_rows, steps, delta_rows)
        if continued:
            expected[0] += 1
        assert positions == list(enumerate(expected))
        count = 0
        for identifier, actor, amount, actual_body in view.iter_query(
                'SELECT id,actor,amount,body FROM step_facts ORDER BY id'):
            assert identifier == count + 1
            assert actor == count % active_rows and amount == 1
            assert actual_body == body
            count += 1
        assert count == steps * delta_rows
        assert view.complete_step == steps + int(continued)
    reader.read(check)


def checkpoint_baseline(path, *, history_rows=1000, active_rows=8, steps=4,
                        delta_rows=3, body_bytes=128, artifacts_per_step=1):
    workload = dict(history_rows=history_rows, active_rows=active_rows, steps=steps,
                    delta_rows=delta_rows, body_bytes=body_bytes,
                    artifacts_per_step=artifacts_per_step)
    for name, value in workload.items():
        minimum = 0 if name in ('history_rows', 'artifacts_per_step') else 1
        if type(value) is not int or value < minimum:
            raise ValueError(f'{name} must be an integer >= {minimum}')
    path = Path(path).resolve()
    path.mkdir(parents=True, exist_ok=False)
    body = b'x' * body_bytes
    stages, samples = {}, []

    def initialize(writer):
        writer.executemany('INSERT INTO cold_history VALUES(?,?)',
                           ((i, body) for i in range(history_rows)))
        writer.executemany('INSERT INTO current_positions VALUES(?,0)',
                           ((i,) for i in range(active_rows)))

    with timed(stages, 'create'):
        store = StageStore.create(path / 'source', SCHEMA, initialize=initialize)
    try:
        for step in range(1, steps + 1):
            sample = dict(step=step, facts_appended=delta_rows, projection_updates=delta_rows)

            def write(writer):
                start = (step - 1) * delta_rows
                for number in range(start, start + delta_rows):
                    actor = number % active_rows
                    writer.execute('UPDATE current_positions SET value=value+1 WHERE id=?', (actor,))
                    writer.execute('INSERT INTO step_facts VALUES(?,?,?,?)',
                                   (number + 1, actor, 1, body))

            with timed(sample, 'transaction'):
                store.transaction(write)
            with timed(sample, 'artifact_prepare'):
                artifacts = [store.prepare_artifact((body,)) for _ in range(artifacts_per_step)]
            with timed(sample, 'complete'):
                descriptor = store.complete(step, artifacts=artifacts)
            sample['changeset_bytes'] = (store.path / descriptor['changeset']).stat().st_size
            sample['artifacts'] = len(artifacts)
            samples.append(sample)
        with timed(stages, 'source_verification'):
            verify(store, history_rows, active_rows, steps, delta_rows, body)
    finally:
        with timed(stages, 'close'):
            store.close()
    source_disk = disk_usage(path / 'source')

    with timed(stages, 'cold_open'):
        reopened = StageStore.open(path / 'source')
    try:
        with timed(stages, 'open_verification'):
            verify(reopened, history_rows, active_rows, steps, delta_rows, body)
    finally:
        reopened.close()

    with timed(stages, 'readonly'):
        readonly = StageStore.prepare_readonly(path / 'source', path / 'readonly')
    try:
        with timed(stages, 'readonly_verification'):
            verify(readonly, history_rows, active_rows, steps, delta_rows, body)
    finally:
        readonly.close()

    with timed(stages, 'restore'):
        restored = StageStore.restore(path / 'source', path / 'restored')
    try:
        with timed(stages, 'restore_verification'):
            verify(restored, history_rows, active_rows, steps, delta_rows, body)
            refs = restored.read(lambda view: view.query('SELECT path FROM _stage_artifacts',
                                max_rows=max(1, steps * artifacts_per_step)))
            assert len(refs) == steps * artifacts_per_step
            for (reference,) in refs:
                data, total = restored.read_artifact(reference, size=body_bytes)
                assert total == body_bytes and data == body
        with timed(stages, 'continue'):
            restored.transaction(lambda writer: writer.execute(
                'UPDATE current_positions SET value=value+1 WHERE id=0'))
            restored.complete(steps + 1)
        assert restored.complete_step == steps + 1
        assert restored.read(lambda view: view.query(
            'SELECT value FROM current_positions WHERE id=0'))[0][0] == expected_positions(
                active_rows, steps, delta_rows)[0] + 1
    finally:
        restored.close()
    # 继续点再次重建，以实际恢复消费者检查最后一次提交可用。
    with timed(stages, 'continued_readonly'):
        continued = StageStore.prepare_readonly(path / 'restored', path / 'continued')
    try:
        with timed(stages, 'continued_verification'):
            verify(continued, history_rows, active_rows, steps, delta_rows, body, continued=True)
    finally:
        continued.close()

    return dict(workload=workload, stages=stages, steps=samples,
                source_disk=source_disk, restored_disk=disk_usage(path / 'restored'),
                readonly_disk=disk_usage(path / 'readonly'), max_rss_bytes=max_rss_bytes(),
                rss_scope='whole worker process high-water mark; not per-stage allocation',
                verification=dict(history_rows=history_rows, fact_rows=steps * delta_rows,
                                  active_rows=active_rows, complete_step=steps,
                                  artifacts=steps * artifacts_per_step,
                                  readonly_matches=True, restore_matches=True,
                                  continued_complete_step=steps + 1))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='新的输出目录，已有目录拒绝覆盖')
    parser.add_argument('--history', type=int, nargs='+', default=[1000, 100000])
    parser.add_argument('--active-rows', type=int, default=8)
    parser.add_argument('--steps', type=int, default=8)
    parser.add_argument('--delta-rows', type=int, default=8)
    parser.add_argument('--body-bytes', type=int, default=128)
    parser.add_argument('--artifacts-per-step', type=int, default=1)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = dict(active_rows=args.active_rows, steps=args.steps, delta_rows=args.delta_rows,
                  body_bytes=args.body_bytes, artifacts_per_step=args.artifacts_per_step)
    if args.worker:
        if len(args.history) != 1:
            parser.error('worker requires exactly one history size')
        print(json.dumps(checkpoint_baseline(args.output, history_rows=args.history[0], **config)))
        return
    args.output.mkdir(parents=True, exist_ok=False)
    samples = []
    for index, history in enumerate(args.history):
        command = [sys.executable, '-m', 'benchmarks.core_next_checkpoint_baseline',
                   '--worker', '--output', str(args.output / f'sample-{index}'),
                   '--history', str(history)]
        for name, value in config.items():
            command += ['--' + name.replace('_', '-'), str(value)]
        process = subprocess.run(command, capture_output=True, text=True)
        (args.output / f'sample-{index}.stderr.txt').write_text(process.stderr)
        if process.returncode:
            raise RuntimeError(f'baseline worker failed ({process.returncode}): {process.stderr}')
        sample = json.loads(process.stdout)
        sample['command'] = command
        samples.append(sample)
    root = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip())
    report = dict(format=1, python=platform.python_version(), platform=platform.platform(),
                  sqlite=apsw.sqlitelibversion(), samples=samples,
                  source=dict(commit=commit, dirty=dirty, module=society0.__file__, executable=sys.executable),
                  limitations=['separate process RSS high-water marks include imports and verification',
                               'OS filesystem cache is not cleared; repeated runs may be warm',
                               'synthetic environment-only workload; no model or memory endpoint',
                               'repeated x payload is highly compressible; not a real-corpus compression estimate',
                               'timing is descriptive; semantic tests do not use machine-dependent thresholds'])
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'report': str(args.output / 'report.json'), 'samples': len(samples)}))


if __name__ == '__main__':
    main()
