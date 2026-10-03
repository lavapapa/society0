"""只读连接复用的非作者查询与生命周期边界。"""
import apsw
import pytest
from society0.kernel.storage import StageReader,StageStore,StorageError
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA


def test_review_reused_thread_observer_leaves_no_snapshot_after_error_or_escape(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        with StageReader(store.path) as reader:
            escaped=[]
            def query(view):
                rows=view.iter_query('SELECT seq FROM thread_events WHERE thread_id=?',(tid,))
                assert next(rows)==(1,)
                escaped.append(rows)
                return view.live_revision
            revision=reader.read(query)
            with pytest.raises(StorageError):next(escaped[0])
            threads.append_message(tid,{'role':'user','content':'new'})
            with pytest.raises(StorageError,match='revision'):reader.read(lambda r:None,expected_revision=revision)
            assert ThreadStore(reader).tail(tid)['total']==2
            assert store._connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').get==(0,0,0)
            assert reader._connection.txn_state()==apsw.SQLITE_TXN_NONE


def test_review_closed_reader_rejects_artifact_operation(tmp_path):
    with StageStore.create(tmp_path/'run',[]) as store:
        ref=store.prepare_artifact([b'original'])
        reader=StageReader(store.path)
        with reader:assert reader.read_artifact(ref)==(b'original',8)
        with pytest.raises(StorageError,match='closed'):reader.read_artifact(ref)
