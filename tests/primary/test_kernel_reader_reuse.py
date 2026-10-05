"""只读连接所有权、嵌套快照与作用域收尾。"""
import threading
import apsw
import pytest
from society0.kernel.storage import StageReader,StageStore,StorageError

SCHEMA=['CREATE TABLE item(id INTEGER PRIMARY KEY,value INTEGER)']


def test_store_reuses_idle_reader_but_nested_read_gets_new_snapshot(tmp_path,monkeypatch):
    with StageStore.create(tmp_path/'run',SCHEMA,initialize=lambda w:w.execute('INSERT INTO item VALUES(1,7)')) as store:
        connections=[];native=apsw.Connection
        def connect(*args,**kwargs):
            connection=native(*args,**kwargs);connections.append(connection);return connection
        monkeypatch.setattr(apsw,'Connection',connect)
        def outer(view):
            assert view.query('SELECT value FROM item')==[(7,)]
            store.transaction(lambda w:w.execute('UPDATE item SET value=9'))
            assert store.read(lambda nested:nested.query('SELECT value FROM item'))==[(9,)]
            assert view.query('SELECT value FROM item')==[(7,)]
        store.read(outer)
        for _ in range(10):assert store.read(lambda view:view.query('SELECT value FROM item'))==[(9,)]
        assert len(connections)==2
    for connection in connections:
        with pytest.raises(apsw.ConnectionClosedError):connection.execute('SELECT 1')


def test_reader_ends_partial_cursor_and_recovers_after_callback_failure(tmp_path):
    with StageStore.create(tmp_path/'run',SCHEMA,initialize=lambda w:w.executemany('INSERT INTO item VALUES(?,?)',[(1,7),(2,8)])) as store:
        escaped=[];connections=[]
        def read(view):
            stream=view.iter_query('SELECT * FROM item ORDER BY id')
            next(stream);escaped.append(stream);connections.append(view._connection)
            raise ValueError('callback failure')
        with pytest.raises(ValueError,match='callback failure'):store.read(read)
        with pytest.raises(StorageError,match='scope'):next(escaped[0])
        assert connections[0].get_autocommit()
        assert connections[0].txn_state()==apsw.SQLITE_TXN_NONE
        assert store.read(lambda view:view.query('SELECT count(*) FROM item'))==[(2,)]
        store.transaction(lambda w:w.execute('UPDATE item SET value=10'))
        assert store._connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').get==(0,0,0)


def test_explicit_reader_context_reuses_connection_and_binds_thread(tmp_path,monkeypatch):
    with StageStore.create(tmp_path/'run',SCHEMA):pass
    native=apsw.Connection;opened=[]
    def connect(*args,**kwargs):
        connection=native(*args,**kwargs);opened.append(connection);return connection
    monkeypatch.setattr(apsw,'Connection',connect)
    with StageReader(tmp_path/'run') as reader:
        for _ in range(3):assert reader.read(lambda v:v.query('SELECT count(*) FROM item'))==[(0,)]
        assert len(opened)==1
        errors=[]
        def other():
            try:reader.read(lambda view:None)
            except StorageError as error:errors.append(str(error))
        thread=threading.Thread(target=other);thread.start();thread.join()
        assert errors
    with pytest.raises(StorageError,match='closed'):reader.read(lambda view:None)
