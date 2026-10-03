"""真实 SQLite Session 存储的非作者验收。"""
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from society0.kernel.storage import StageReader, StageStore, StorageError


def create(path):
    return StageStore.create(path, ['CREATE TABLE facts(id INTEGER PRIMARY KEY,value TEXT NOT NULL)'],
                             initialize=lambda w: w.execute('INSERT INTO facts VALUES(1,?)', ('root',)))


def values(store):
    return store.read(lambda r: r.query('SELECT id,value FROM facts ORDER BY id'))


def test_review_writer_lease_rejects_other_thread_even_inside_callback(tmp_path):
    with create(tmp_path / 'run') as store:
        errors = []
        def callback(writer):
            def cross_thread():
                try: writer.execute("UPDATE facts SET value='wrong' WHERE id=1")
                except BaseException as error: errors.append(error)
            thread = threading.Thread(target=cross_thread)
            thread.start()
            thread.join()
        store.transaction(callback)
        assert len(errors) == 1 and isinstance(errors[0], StorageError)
        assert values(store) == [(1, 'root')]


def test_review_writer_scope_expiration_and_failed_returning_rollback(tmp_path):
    with create(tmp_path / 'run') as store:
        writer = store.transaction(lambda w: w)
        with pytest.raises(StorageError, match='expired'):
            writer.execute("UPDATE facts SET value='expired'")
        with pytest.raises(StorageError, match='query'):
            store.transaction(lambda w: w.execute("UPDATE facts SET value='changed' RETURNING id"))
        assert values(store) == [(1, 'root')]
        store.transaction(lambda w: w.execute("UPDATE facts SET value='valid'"))
        assert store.complete(1)['step'] == 1


def test_review_complete_receipt_failure_reopens_at_new_complete_marker(tmp_path):
    path = tmp_path / 'run'
    store = create(path)
    store.transaction(lambda w: w.execute("UPDATE facts SET value='one'"))
    def fault(phase):
        if phase == 'after_publish': raise OSError('receipt failed')
    store._fault = fault
    with pytest.raises(OSError, match='receipt'):
        store.complete(1)
    assert store.complete_step == 1
    store.close()
    with StageStore.open(path) as reopened:
        assert reopened.complete_step == 1 and values(reopened) == [(1, 'one')]


def test_review_readonly_process_current_and_complete_watermarks(tmp_path):
    path = tmp_path / 'run'
    with create(path) as store:
        store.transaction(lambda w: w.execute("UPDATE facts SET value='active'"))
        script = '''import json,sys
from society0.kernel.storage import StageReader
print(json.dumps(StageReader(sys.argv[1]).read(lambda r: [r.live_revision,r.complete_step,r.query('SELECT value FROM facts')])))
'''
        # 外部 reader 使用独立 SQLite 连接，当前事务已提交而完整步尚未发布。
        result = subprocess.run([sys.executable, '-c', script, str(path)], capture_output=True, text=True, check=True)
        assert json.loads(result.stdout) == [1, 0, [['active']]]
        with pytest.raises(StorageError, match='revision'):
            StageReader(path).read(lambda r: r.query('SELECT value FROM facts'), expected_revision=0)


def test_review_artifact_fork_copies_complete_chain_and_survives_source_removal(tmp_path):
    source = tmp_path / 'source'
    with create(source) as store:
        first = store.prepare_artifact([b'a', b'\x00\xff'])
        store.transaction(lambda w: w.execute("UPDATE facts SET value='one'"))
        store.complete(1, artifacts=[first])
        second = store.prepare_artifact([b'b'])
        store.transaction(lambda w: w.execute("INSERT INTO facts VALUES(2,'two')"))
        store.complete(2, artifacts=[second])
        with StageStore.fork(source, tmp_path / 'fork', step=2) as fork:
            assert values(fork) == [(1, 'one'), (2, 'two')]
            assert (fork.path / first).read_bytes() == b'a\x00\xff'
            assert (fork.path / second).read_bytes() == b'b'
    source.rename(tmp_path / 'moved-source')
    with StageStore.open(tmp_path / 'fork') as fork:
        assert values(fork) == [(1, 'one'), (2, 'two')]


def test_review_hot_read_and_complete_do_not_discover_history(tmp_path, monkeypatch):
    with create(tmp_path / 'run') as store:
        for step in range(1, 5):
            store.transaction(lambda w: w.execute('UPDATE facts SET value=?', (str(step),)))
            store.complete(step)
        def forbidden(*args, **kwargs): raise AssertionError('hot path scanned history')
        monkeypatch.setattr(StageStore, '_latest', forbidden)
        monkeypatch.setattr(StageStore, '_validate_references', forbidden)
        monkeypatch.setattr(Path, 'glob', forbidden)
        assert values(store) == [(1, '4')]
        store.transaction(lambda w: w.execute("UPDATE facts SET value='5'"))
        assert store.complete(5)['step'] == 5


@pytest.mark.parametrize('table', ['astage_records', 'sqliteFacts'])
def test_review_schema_filter_preserves_ordinary_table_names(tmp_path, table):
    path = tmp_path / 'run'
    with StageStore.create(path, [f'CREATE TABLE {table}(id INTEGER PRIMARY KEY,value TEXT NOT NULL)']) as store:
        store.transaction(lambda w: w.execute(f'INSERT INTO {table} VALUES(1,?)', ('must persist',)))
        store.complete(1)
    with StageStore.restore(path, tmp_path / 'restored', step=1) as restored:
        assert restored.read(lambda r: r.query(f'SELECT value FROM {table}')) == [('must persist',)]
