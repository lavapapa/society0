"""Thread 事件归属完整步骤，在分叉后仍保持原完整前缀。"""
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA


def test_thread_publication_step_survives_fork_and_new_incomplete_work(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        store.complete(1)
        threads.append_message(tid,{'role':'user','content':'second'})
        store.complete(2)
        threads.append_message(tid,{'role':'user','content':'dirty'})
        assert store.read(lambda r:r.query('SELECT seq,publish_step FROM thread_events ORDER BY seq'))==[(1,1),(2,2),(3,3)]
    with StageStore.restore(tmp_path/'run',tmp_path/'fork',step=1) as store:
        threads=ThreadStore(store)
        assert threads.tail(tid)['total']==1
        threads.append_message(tid,{'role':'user','content':'new branch'})
        assert store.read(lambda r:r.query('SELECT seq,publish_step FROM thread_events ORDER BY seq'))==[(1,1),(2,2)]
        store.complete(2)
