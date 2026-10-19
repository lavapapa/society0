"""原生只读锁等待：可控独占锁、等待耗尽及完成 WAL 的多进程读。"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import apsw
import pytest

from society0.kernel.observation import Observation
from society0.kernel.storage import StageReader, StageStore


def completed_run(path):
    with StageStore.create(path, ['CREATE TABLE facts(id INTEGER PRIMARY KEY,value TEXT NOT NULL)']) as store:
        store.transaction(lambda writer: writer.execute("INSERT INTO facts VALUES(1,'complete')"))
        store.complete(1)
    return path


def child_environment():
    return {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src'),
            'PYTHONDONTWRITEBYTECODE': '1'}


@contextmanager
def exclusive_lock(path):
    script = '''import apsw,sys
connection=apsw.Connection(sys.argv[1])
connection.execute('PRAGMA locking_mode=EXCLUSIVE')
connection.execute('BEGIN EXCLUSIVE')
print('locked',flush=True)
input()
connection.execute('ROLLBACK')
connection.close()
'''
    process = subprocess.Popen([sys.executable, '-B', '-c', script, str(path / 'current.sqlite')],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        yield process
    finally:
        if process.poll() is None:
            process.communicate('\n', timeout=5)
        else:
            process.communicate(timeout=5)
        assert process.returncode == 0


def test_default_reader_waits_for_short_native_lock(tmp_path):
    path = completed_run(tmp_path / 'run')
    with exclusive_lock(path) as locker:
        def release():
            locker.stdin.write('\n')
            locker.stdin.flush()
        timer = threading.Timer(.15, release)
        timer.start()
        started = time.monotonic()
        try:
            with Observation(path) as observer:
                assert observer.status()['complete']['step'] == 1
                assert observer.reader.read(lambda view: view.query('SELECT value FROM facts')) == [('complete',)]
        finally:
            timer.join()
        assert .08 <= time.monotonic() - started < 2


@pytest.mark.parametrize('timeout_ms', [0, 60])
def test_native_wait_is_bounded_and_retained_reader_recovers(tmp_path, timeout_ms):
    path = completed_run(tmp_path / 'run')
    with StageReader(path, busy_timeout_ms=timeout_ms) as reader:
        with exclusive_lock(path):
            started = time.monotonic()
            with pytest.raises(apsw.BusyError):
                reader.read(lambda view: view.complete_step)
            elapsed = time.monotonic() - started
            assert elapsed < 1
            if timeout_ms:
                assert elapsed >= .04
        assert reader.read(lambda view: view.complete_step) == 1
        assert reader.read(lambda view: view.query('SELECT value FROM facts')) == [('complete',)]


@pytest.mark.parametrize('timeout_ms', [-1, True, 1.5, None])
def test_reader_rejects_invalid_busy_timeout(tmp_path, timeout_ms):
    with pytest.raises(ValueError, match='busy_timeout_ms'):
        StageReader(tmp_path, busy_timeout_ms=timeout_ms)


def test_completed_wal_many_processes_open_and_close_readers(tmp_path):
    path = completed_run(tmp_path / 'run')
    script = '''import json,sys
from society0.kernel.observation import Observation
from society0.kernel.storage import StageReader
print('ready',flush=True)
input()
for _ in range(16):
 with Observation(sys.argv[1]) as observer:
  assert observer.status()['complete']['step']==1
 with StageReader(sys.argv[1]) as reader:
  assert reader.read(lambda view:view.query('SELECT value FROM facts'))==[('complete',)]
print(json.dumps({'reads':32}))
'''
    processes = [subprocess.Popen([sys.executable, '-B', '-c', script, str(path)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=child_environment()) for _ in range(12)]
    try:
        for process in processes:
            assert process.stdout.readline().strip() == 'ready'
        for process in processes:
            process.stdin.write('\n')
            process.stdin.flush()
        for process in processes:
            output, error = process.communicate(timeout=15)
            assert process.returncode == 0, error
            assert json.loads(output)['reads'] == 32
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
