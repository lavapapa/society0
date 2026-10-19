"""未发布新 store 的原子装配、完整同步次数与根恢复合同。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import apsw
import pytest

from society0.kernel import storage
from society0.kernel.storage import StageStore, StorageError


def schema(count):
    return tuple(f'CREATE TABLE item_{i}(id INTEGER PRIMARY KEY,value TEXT NOT NULL)' for i in range(count))


@pytest.fixture
def initial_trace(monkeypatch):
    statements = []
    def hook(connection):
        if Path(connection.db_filename('main')).name == 'current.sqlite':
            def trace(cursor, sql, bindings):
                statements.append((sql, connection.get_autocommit()))
                return True
            connection.set_exec_trace(trace)
    apsw.connection_hooks.append(hook)
    try:
        yield statements
    finally:
        apsw.connection_hooks.remove(hook)


@pytest.mark.parametrize('count', [1, 16])
def test_schema_bootstrap_and_metadata_share_one_full_transaction(tmp_path, monkeypatch, initial_trace, count):
    finish = StageStore._finish_initial
    captured = []
    def inspect_finish(path, connection, manifest, artifacts):
        # backup 只能在提交后进入；元数据的最后一行也已经持久提交。
        assert connection.get_autocommit()
        assert connection.execute('PRAGMA synchronous').get == 2
        assert connection.execute('PRAGMA foreign_keys').get == 1
        assert connection.execute('SELECT count(*) FROM _stage_table_revisions').get == count
        captured.extend(initial_trace)
        return finish(path, connection, manifest, artifacts)
    monkeypatch.setattr(StageStore, '_finish_initial', staticmethod(inspect_finish))
    def initialize(writer):
        assert not writer._connection.get_autocommit()
        writer.execute("INSERT INTO item_0 VALUES(1,'first')")
        assert writer.query('SELECT value FROM item_0') == [('first',)]
        writer.execute("UPDATE item_0 SET value='complete'")
    with StageStore.create(tmp_path/'run', schema(count), initialize=initialize) as store:
        assert store.read(lambda view:view.query('SELECT value FROM item_0')) == [('complete',)]
        assert store._connection.execute('PRAGMA synchronous').get == 2
        assert store._connection.execute('PRAGMA journal_mode').get == 'wal'
    sql = [statement.upper() for statement, _ in captured]
    assert sum(statement.startswith('BEGIN') for statement in sql) == 1
    assert sql.count('COMMIT') == 1
    assert not any('JOURNAL_MODE=WAL' in statement for statement in sql)
    writes = [(statement, autocommit) for statement, autocommit in captured
              if statement.upper().startswith(('CREATE ', 'INSERT ', 'UPDATE '))]
    assert writes and all(not autocommit for _, autocommit in writes)
    assert not list(tmp_path.glob('run.building-*'))


def test_initial_sync_count_does_not_grow_with_declared_tables(tmp_path):
    base = apsw.vfs_names()[0]
    events = []
    class File(apsw.VFSFile):
        def __init__(self, name, flags):
            self.name = name.filename() if isinstance(name, apsw.URIFilename) else str(name)
            super().__init__(base, name, flags)
        def xSync(self, flags):
            super().xSync(flags)  # 始终执行真实同步。
            events.append(Path(self.name).name)
    class VFS(apsw.VFS):
        def xOpen(self, name, flags):
            return File(name, flags)
    vfs = VFS('initial-sync-'+uuid.uuid4().hex, base, makedefault=True)
    counts = []
    try:
        for count in (1, 16):
            events.clear()
            with StageStore.create(tmp_path/str(count), schema(count)):
                pass
            counts.append(len(events))
    finally:
        apsw.set_default_vfs(base)
        vfs.unregister()
    assert counts[0] > 0
    assert counts[0] == counts[1], counts


@pytest.mark.parametrize('failure', ['ddl', 'initialize', 'commit'])
def test_initial_failure_rolls_back_and_never_publishes(tmp_path, initial_trace, failure):
    escaped = []
    def initialize(writer):
        escaped.append(writer)
        writer.execute("INSERT INTO item_0 VALUES(1,'pending')")
        if failure == 'initialize':
            raise ValueError('initialization failed')
        if failure == 'commit':
            writer._connection.set_commit_hook(lambda: True)
    declared = schema(2)
    if failure == 'ddl':
        declared += ('CREATE TABLE item_0(id INTEGER PRIMARY KEY)',)
    with pytest.raises((apsw.SQLError, apsw.ConstraintError, ValueError)):
        StageStore.create(tmp_path/'run', declared, initialize=initialize)
    assert not (tmp_path/'run').exists()
    assert not list(tmp_path.glob('run.building-*'))
    # 失败的原生 COMMIT 自行回滚；DDL/回调失败由创建器显式回滚。
    if failure != 'commit':
        assert 'ROLLBACK' in [sql.upper() for sql, _ in initial_trace]
    for writer in escaped:
        with pytest.raises(StorageError, match='scope'):
            writer.query('SELECT * FROM item_0')


@pytest.mark.parametrize('sql', ['COMMIT', 'CREATE TABLE extra(id INTEGER PRIMARY KEY)',
                                  'INSERT INTO _stage_runtime VALUES(1,2,3,4,5)'])
def test_initial_writer_keeps_authorization_and_scope(tmp_path, sql):
    escaped = []
    def initialize(writer):
        escaped.append(writer)
        with pytest.raises(apsw.Error):
            writer.execute(sql)
        writer.execute("INSERT INTO item_0 VALUES(1,'authorized')")
    with StageStore.create(tmp_path/'run', schema(1), initialize=initialize):
        pass
    with pytest.raises(StorageError, match='scope'):
        escaped[0].execute('DELETE FROM item_0')


def test_initial_root_restores_in_new_process_and_keeps_normal_step_contract(tmp_path):
    source = tmp_path/'source'
    with StageStore.create(source, schema(2), initialize=lambda writer:writer.execute("INSERT INTO item_0 VALUES(1,'root')")) as store:
        assert store.complete_step == 0
        store.transaction(lambda writer:writer.execute("UPDATE item_0 SET value='step one'"))
        store.complete(1)
    code = '''
import json,sys
from society0.kernel.storage import StageStore
with StageStore.restore(sys.argv[1],sys.argv[2],step=0) as root:
    assert root.complete_step==0
    assert root.read(lambda v:v.query('SELECT value FROM item_0'))==[('root',)]
    assert root._connection.execute('PRAGMA synchronous').get==2
    root.transaction(lambda w:w.execute("UPDATE item_0 SET value='restored step'"))
    root.complete(1)
with StageStore.open(sys.argv[2]) as reopened:
    assert reopened.complete_step==1
    assert reopened.read(lambda v:v.query('SELECT value FROM item_0'))==[('restored step',)]
print(json.dumps({'step':1,'restored':True}))
'''
    result = subprocess.run([sys.executable, '-c', code, str(source), str(tmp_path/'restored')],
                            capture_output=True, text=True, timeout=30, env=os.environ.copy())
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'step':1, 'restored':True}
