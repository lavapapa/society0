"""StageStore 的完成身份、原生事务和恢复合同。"""
from pathlib import Path
import json
import os
import subprocess
import sys

import pytest

from society0.kernel.storage import StageStore, StorageError

SCHEMA = [
    'CREATE TABLE account(id INTEGER PRIMARY KEY, balance INTEGER NOT NULL)',
    'CREATE TABLE event(id INTEGER PRIMARY KEY, body TEXT NOT NULL)',
]


def make(path):
    return StageStore.create(path, SCHEMA, initialize=lambda w: w.execute('INSERT INTO account VALUES(1,10)'))


def balance(store):
    return store.read(lambda r: r.query('SELECT balance FROM account')[0][0])


def test_short_transactions_live_revision_and_complete(tmp_path):
    with make(tmp_path / 'run') as store:
        assert store.complete_step == 0
        store.transaction(lambda w: w.execute('UPDATE account SET balance=11'))
        assert balance(store) == 11
        assert store.read(lambda r: (r.live_revision, r.complete_step)) == (1, 0)
        first = store.complete(1)
        assert first['step'] == 1
        assert store.complete(1) == first
        assert store.complete(2)['step'] == 2  # 空步骤有完整身份
        assert store.complete_step == 2
    with StageStore.open(tmp_path / 'run') as reopened:
        assert balance(reopened) == 11


def test_callback_failure_and_borrow_expiry(tmp_path):
    with make(tmp_path / 'run') as store:
        escaped = store.transaction(lambda w: w)
        with pytest.raises(StorageError, match='scope'):
            escaped.execute('UPDATE account SET balance=100')
        reader = store.read(lambda r: r)
        with pytest.raises(StorageError, match='scope'):
            reader.query('SELECT * FROM account')
        def fail(w):
            w.execute('UPDATE account SET balance=99')
            raise ValueError('business reject')
        with pytest.raises(ValueError):
            store.transaction(fail)
        assert balance(store) == 10
        async def wrong(w):
            w.execute('UPDATE account SET balance=99')
        with pytest.raises(TypeError, match='await'):
            store.transaction(wrong)
        assert balance(store) == 10
        store.transaction(lambda w: w.execute('UPDATE account SET balance=12'))
        store.abort_step()
        with pytest.raises(StorageError):
            store.transaction(lambda w: None)
        with pytest.raises(StorageError):
            store.complete(1)
    with pytest.raises(StorageError, match='restore'):
        StageStore.open(tmp_path / 'run')


@pytest.mark.parametrize('sql', ['CREATE TABLE undeclared(id INTEGER PRIMARY KEY)', 'COMMIT', 'PRAGMA user_version=9', "ATTACH ':memory:' AS extra"])
def test_writer_cannot_escape_transaction_or_schema(tmp_path, sql):
    with make(tmp_path / 'run') as store:
        with pytest.raises(Exception):
            store.transaction(lambda w: w.execute(sql))
        assert balance(store) == 10
        store.complete(1)


def test_restore_ignores_dirty_current_and_copies_artifacts(tmp_path):
    source = tmp_path / 'source'
    with make(source) as store:
        artifact = source / 'artifacts' / 'thread.jsonl'
        artifact.parent.mkdir()
        artifact.write_text('{"text":"complete"}\n')
        store.transaction(lambda w: w.execute('UPDATE account SET balance=20'))
        complete = store.complete(1, artifacts=['artifacts/thread.jsonl'])
        store.transaction(lambda w: w.execute('UPDATE account SET balance=30'))
        old_id = store.run_id
    with pytest.raises(StorageError, match='restore'):
        StageStore.open(source)
    with StageStore.restore(source, tmp_path / 'restored') as restored:
        assert balance(restored) == 20
        assert restored.run_id != old_id
        assert restored.complete_step == 1
        assert restored.source['run_id'] == old_id
        assert restored.source['step'] == complete['step']
        assert (restored.path / 'artifacts/thread.jsonl').read_text() == artifact.read_text()
        artifact.unlink()
        assert (restored.path / 'artifacts/thread.jsonl').is_file()
        restored.transaction(lambda w: w.execute('UPDATE account SET balance=21'))
        restored.complete(2)


def test_artifact_conflict_missing_and_schema_mismatch(tmp_path):
    source = tmp_path / 'source'
    with make(source) as store:
        with pytest.raises(StorageError):
            store.complete(1, artifacts=['missing.jsonl'])
        assert store.complete_step == 0
    # 封存失败的当前实例不得继续，恢复从root取得新实例。
    with StageStore.restore(source, tmp_path / 'fresh') as store:
        store.complete(1)
        with pytest.raises(StorageError):
            store.complete(1, artifacts=['artifacts/other'])
        store.transaction(lambda w: w.execute('UPDATE account SET balance=1'))
        with pytest.raises(StorageError):
            store.complete(1)
    import apsw
    db = apsw.Connection(str(source / 'root.sqlite'))
    db.execute('DROP TABLE event')
    db.close()
    with pytest.raises(StorageError, match='schema'):
        StageStore.restore(source, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_missing_changeset_is_not_silent_fallback(tmp_path):
    source = tmp_path / 'source'
    with make(source) as store:
        store.transaction(lambda w: w.execute('UPDATE account SET balance=22'))
        descriptor = store.complete(1)
    (source / descriptor['changeset']).unlink()
    with pytest.raises(StorageError):
        StageStore.restore(source, tmp_path / 'bad')
    assert not (tmp_path / 'bad').exists()


def test_read_is_short_and_bound_to_revision(tmp_path):
    with make(tmp_path / 'source') as store:
        def read_old(view):
            assert view.query('SELECT balance FROM account') == [(10,)]
            store.transaction(lambda w: w.execute('UPDATE account SET balance=15'))
            assert view.query('SELECT balance FROM account') == [(10,)]
        store.read(read_old)
        assert balance(store) == 15
        with pytest.raises(StorageError, match='revision'):
            store.read(lambda r: None, expected_revision=0)
        with pytest.raises(StorageError, match='limit'):
            store.read(lambda r: r.query('SELECT 1 UNION ALL SELECT 2', max_rows=1))
        with pytest.raises(Exception):
            store.read(lambda r: r.query('DELETE FROM account'))


@pytest.mark.parametrize('schema', [['CREATE TABLE missing_pk(value TEXT)'], ['CREATE TABLE nullable_pk(id TEXT PRIMARY KEY)']])
def test_schema_requires_explicit_nonnull_primary_key(tmp_path, schema):
    with pytest.raises(StorageError, match='primary key'):
        StageStore.create(tmp_path / 'bad', schema)
    assert not (tmp_path / 'bad').exists()


@pytest.mark.parametrize('phase,expected', [('before_publish', 10), ('after_publish', 44)])
def test_process_crash_publication_boundary(tmp_path, phase, expected):
    source = tmp_path / 'source'
    with make(source):
        pass
    script = '''
import os,sys
from society0.kernel.storage import StageStore
s=StageStore.open(sys.argv[1])
s.transaction(lambda w:w.execute('UPDATE account SET balance=44'))
s._fault=lambda phase: os._exit(73) if phase==sys.argv[2] else None
s.complete(1)
'''
    result = subprocess.run([sys.executable, '-c', script, str(source), phase])
    assert result.returncode == 73
    with StageStore.restore(source, tmp_path / 'restored') as restored:
        assert balance(restored) == expected
    if phase == 'after_publish':
        with StageStore.open(source) as store:
            assert store.complete(1)['step'] == 1
    else:
        with pytest.raises(StorageError, match='restore'):
            StageStore.open(source)


def test_lost_receipt_same_instance_and_cold_missing_reference(tmp_path):
    with make(tmp_path / 'source') as store:
        def fault(phase):
            if phase == 'after_publish':
                raise OSError('lost receipt')
        store._fault = fault
        with pytest.raises(OSError):
            store.complete(1)
        descriptor = store.complete(1)
    (tmp_path / 'source' / descriptor['changeset']).unlink()
    with pytest.raises(StorageError, match='changeset'):
        StageStore.open(tmp_path / 'source')


def test_hot_complete_does_not_scan_history(tmp_path, monkeypatch):
    with make(tmp_path / 'source') as store:
        monkeypatch.setattr(Path, 'glob', lambda *a, **k: (_ for _ in ()).throw(AssertionError('history scan')))
        for step in range(1, 31):
            store.transaction(lambda w: w.execute('UPDATE account SET balance=balance+1'))
            store.complete(step)
            assert store.read(lambda r:r.complete_step) == step


def test_fork_is_independent_after_source_removed(tmp_path):
    import shutil
    with make(tmp_path / 'source') as store:
        store.transaction(lambda w:w.execute('UPDATE account SET balance=55'))
        store.complete(1)
    with StageStore.fork(tmp_path / 'source', tmp_path / 'fork') as fork:
        shutil.rmtree(tmp_path / 'source')
        assert balance(fork) == 55
        fork.transaction(lambda w:w.execute('UPDATE account SET balance=56'))
        fork.complete(2)
    with StageStore.restore(tmp_path / 'fork', tmp_path / 'second') as restored:
        assert balance(restored) == 56


def test_independent_reader_sees_live_and_confirmed_watermarks(tmp_path, monkeypatch):
    from society0.kernel.storage import StageReader
    source = tmp_path / 'source'
    with make(source):
        pass
    script = '''
import sys
from society0.kernel.storage import StageStore
s=StageStore.open(sys.argv[1])
s.transaction(lambda w:w.execute('UPDATE account SET balance=71'))
print('live',flush=True)
input()
s.complete(1)
print('complete',flush=True)
input()
s.close()
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(source)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'live'
        reader = StageReader(source)
        monkeypatch.setattr(Path, 'glob', lambda *a, **k: (_ for _ in ()).throw(AssertionError('history scan')))
        assert reader.read(lambda r:(r.live_revision,r.complete_step,r.query('SELECT balance FROM account'))) == (1,0,[(71,)])
        process.stdin.write('\n'); process.stdin.flush()
        assert process.stdout.readline().strip() == 'complete'
        assert reader.read(lambda r:(r.live_revision,r.complete_step)) == (1,1)
        process.stdin.write('\n'); process.stdin.flush()
        assert process.wait(timeout=10) == 0
    finally:
        if process.poll() is None:
            process.kill(); process.wait()


def test_streaming_prepared_artifact_is_independent_of_input(tmp_path):
    with make(tmp_path / 'source') as store:
        original = tmp_path / 'input'
        original.write_bytes(b'a' * 100_000)
        with original.open('rb') as source:
            ref = store.prepare_artifact(iter(lambda: source.read(4096), b''))
        original.write_bytes(b'changed')
        assert (store.path / ref).read_bytes() == b'a' * 100_000
        store.complete(1, artifacts=[ref])
        with StageStore.restore(store.path, tmp_path / 'restored') as restored:
            assert (restored.path / ref).read_bytes() == b'a' * 100_000


def test_single_writer_lock(tmp_path):
    with make(tmp_path / 'source') as first:
        with pytest.raises(StorageError, match='writer'):
            StageStore.open(first.path)
        assert balance(first) == 10
    with StageStore.open(tmp_path / 'source'):
        pass


def test_failed_artifact_stream_does_not_publish_file(tmp_path):
    with make(tmp_path / 'source') as store:
        def chunks():
            yield b'partial'
            raise ValueError('producer failed')
        with pytest.raises(ValueError):
            store.prepare_artifact(chunks())
        assert list((store.path / 'artifacts').iterdir()) == []
        store.complete(1)


def test_restore_requires_new_identity_and_strict_step(tmp_path):
    with make(tmp_path / 'source') as store:
        original_id = store.run_id
        store.complete(1)
        with pytest.raises(StorageError):
            store.complete(True)
    with pytest.raises(StorageError, match='identity'):
        StageStore.restore(tmp_path / 'source', tmp_path / 'bad', run_id=original_id)
    assert not (tmp_path / 'bad').exists()


def test_read_scan_is_streamed_and_scope_bound(tmp_path):
    with make(tmp_path / 'source') as store:
        store.transaction(lambda w:w.executemany('INSERT INTO event VALUES(?,?)',((i,str(i)) for i in range(2000))))
        assert store.read(lambda r:sum(row[0] for row in r.iter_query('SELECT id FROM event'))) == sum(range(2000))
        def borrow(view):
            iterator = view.iter_query('SELECT id FROM event')
            assert next(iterator) == (0,)
            return iterator
        escaped = store.read(borrow)
        with pytest.raises(StorageError, match='scope'):
            next(escaped)


def test_incremental_blob_read_bounded_and_scope_checked(tmp_path):
    import apsw
    with StageStore.create(tmp_path/'run',['CREATE TABLE docs(id INTEGER PRIMARY KEY,body BLOB NOT NULL)'],
                           initialize=lambda w:w.execute('INSERT INTO docs VALUES(1,zeroblob(?))',(16*1024*1024,))) as store:
        apsw.status(apsw.SQLITE_STATUS_MEMORY_USED,True)
        baseline = apsw.status(apsw.SQLITE_STATUS_MEMORY_USED)[0]
        assert store.read(lambda r:r.read_blob('docs','body',1,offset=100000,size=64)) == (b'\0'*64,16*1024*1024)
        assert apsw.status(apsw.SQLITE_STATUS_MEMORY_USED)[1] - baseline < 4*1024*1024
        escaped = store.read(lambda r:r)
        with pytest.raises(StorageError,match='scope'):
            escaped.read_blob('docs','body',1,offset=0,size=1)
        assert store.read(lambda r:r.read_blob('docs','body',1,offset=20*1024*1024,size=64)) == (b'',16*1024*1024)


@pytest.mark.parametrize('parent,child',[('a_parent','z_child'),('z_parent','a_child')])
def test_foreign_keys_and_native_apply_cross_table_order(tmp_path,parent,child):
    import apsw
    schema = [f'CREATE TABLE {parent}(id INTEGER PRIMARY KEY)',
              f'CREATE TABLE {child}(id INTEGER PRIMARY KEY,parent_id INTEGER NOT NULL REFERENCES {parent}(id))']
    with pytest.raises(apsw.ConstraintError):
        StageStore.create(tmp_path/'bad',schema,initialize=lambda w:w.execute(f'INSERT INTO {child} VALUES(1,999)'))
    assert not (tmp_path/'bad').exists()
    with StageStore.create(tmp_path/'run',schema) as store:
        with pytest.raises(apsw.ConstraintError):
            store.transaction(lambda w:w.execute(f'INSERT INTO {child} VALUES(1,999)'))
        store.transaction(lambda w:(w.execute(f'INSERT INTO {parent} VALUES(1)'),w.execute(f'INSERT INTO {child} VALUES(1,1)')))
        store.complete(1)
    with StageStore.open(tmp_path/'run') as store:
        with pytest.raises(apsw.ConstraintError):
            store.transaction(lambda w:w.execute(f'DELETE FROM {parent}'))
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert restored.read(lambda r:r.query(f'SELECT * FROM {child}')) == [(1,1)]
        with pytest.raises(apsw.ConstraintError):
            restored.transaction(lambda w:w.execute(f'INSERT INTO {child} VALUES(2,999)'))


@pytest.mark.parametrize('parent,child',[('a_parent','z_child'),('z_parent','a_child')])
def test_native_apply_preserves_foreign_key_cascade(tmp_path,parent,child):
    schema = [f'CREATE TABLE {parent}(id INTEGER PRIMARY KEY)',
              f'CREATE TABLE {child}(id INTEGER PRIMARY KEY,parent_id INTEGER NOT NULL REFERENCES {parent}(id) ON DELETE CASCADE)']
    with StageStore.create(tmp_path/'run',schema,initialize=lambda w:(w.execute(f'INSERT INTO {parent} VALUES(1)'),w.execute(f'INSERT INTO {child} VALUES(1,1)'))) as store:
        store.transaction(lambda w:w.execute(f'DELETE FROM {parent}'))
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert restored.read(lambda r:r.query(f'SELECT * FROM {parent}')) == []
        assert restored.read(lambda r:r.query(f'SELECT * FROM {child}')) == []


def test_artifact_dependency_registration_is_transactional_and_restored(tmp_path):
    with make(tmp_path/'run') as store:
        reference = store.prepare_artifact([b'0123456789'])
        def fail(writer):
            writer.include_artifact(reference)
            raise ValueError('rollback')
        with pytest.raises(ValueError):store.transaction(fail)
        assert store.complete(1)['artifacts'] == []
        store.transaction(lambda writer:writer.include_artifact(reference))
        marker = store.complete(2)
        assert marker['artifacts'] == [{'path':reference,'size':10}]
        assert store.complete(2) == marker
        assert store.complete(3)['artifacts'] == []
        with StageStore.restore(store.path,tmp_path/'restored') as restored:
            from society0.kernel.storage import StageReader
            assert StageReader(restored.path).read_artifact(reference,offset=3,size=4) == (b'3456',10)
            restored.transaction(lambda writer:writer.include_artifact(reference))
            assert restored.complete(4)['artifacts'] == []
