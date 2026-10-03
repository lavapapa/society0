"""共享编码资源的非作者边界：半消费异常排空与真实恢复。"""
import threading
import json
import zlib
import pytest
from society0.kernel.storage import StageStore,StorageError
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore


def test_review_partial_encoding_base_exception_drains_native_tasks_and_rolls_back(tmp_path,monkeypatch):
    from society0.kernel import _json_chunks as codec
    real=codec.zlib.compress
    lock=threading.Lock();release=threading.Event()
    active=peak=started=0
    main=threading.get_ident();worker_ids=set()
    def compress(raw,level):
        nonlocal active,peak,started
        assert type(raw) is bytes
        with lock:
            started+=1;number=started;active+=1;peak=max(peak,active)
            worker_ids.add(threading.get_ident())
        try:
            if number>1:assert release.wait(2)
            return real(raw,level)
        finally:
            with lock:active-=1
    monkeypatch.setattr(codec.zlib,'compress',compress)
    with StageStore.create(tmp_path/'run',['CREATE TABLE item(id INTEGER PRIMARY KEY)'],compression_workers=2,compression_inflight_bytes=262144) as store:
        def write(writer):
            writer.execute('INSERT INTO item VALUES(1)')
            def emit(size,body):
                threading.Timer(.02,release.set).start()
                raise KeyboardInterrupt('simulated cancellation')
            writer.write_json_chunks({'body':'🙂'*400000},emit)
        with pytest.raises(KeyboardInterrupt):store.transaction(write)
        assert active==0 and peak<=2 and main not in worker_ids
        assert store.read(lambda r:r.query('SELECT * FROM item'))==[]
        store.transaction(lambda w:w.execute('INSERT INTO item VALUES(2)'))


def test_review_chunk_encoder_prefetch_is_bounded_and_recovered_body_is_exact(tmp_path,monkeypatch):
    from society0.kernel import _json_chunks as codec
    generated=0
    native=codec.write_json
    def counted(value,emit):
        def consume(raw):
            nonlocal generated
            generated+=1
            emit(raw)
        native(value,consume)
    monkeypatch.setattr(codec,'write_json',counted)
    value={'role':'user','content':'多字节🙂\\\"\n'*100000}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA,compression_workers=2,compression_inflight_bytes=65536) as store:
        # 四块小载荷判定预读为固定256KiB，实际提交队列另受64KiB上限。
        first=[]
        def write(writer):
            def emit(size,body):
                first.append((size,body))
                assert generated<=4
                raise InterruptedError('stop after first block')
            writer.write_json_chunks(value,emit)
        with pytest.raises(InterruptedError):store.transaction(write)
        size,chunk=first[0]
        assert size==len(zlib.decompress(chunk))==65536
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,value);store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restore') as restored:
        assert ThreadStore(restored).read_messages(tid)==[value]


def test_review_main_thread_interrupt_while_waiting_keeps_future_in_drain_set(tmp_path,monkeypatch):
    from society0.kernel import _json_chunks as codec
    entered=threading.Event();release=threading.Event();finished=threading.Event()
    native_pool=codec.ThreadPoolExecutor
    native_compress=codec.zlib.compress
    def compress(raw,level):
        entered.set();assert release.wait(2)
        try:return native_compress(raw,level)
        finally:finished.set()
    def pool_factory(**kwargs):
        pool=native_pool(**kwargs);submit=pool.submit
        def intercepted(*args,**kw):
            future=submit(*args,**kw)
            def interrupted(*args,**kw):
                assert entered.wait(1)
                raise KeyboardInterrupt('main thread wait interrupted')
            future.result=interrupted
            return future
        pool.submit=intercepted
        return pool
    monkeypatch.setattr(codec,'ThreadPoolExecutor',pool_factory)
    monkeypatch.setattr(codec.zlib,'compress',compress)
    timer=threading.Timer(.1,release.set)
    with StageStore.create(tmp_path/'run',['CREATE TABLE item(id INTEGER PRIMARY KEY)'],compression_workers=1) as store:
        # workers=1走同步短路，显式启用线程路径但仅容纳一个native任务。
        store._encoder.workers=2;store._encoder.inflight_bytes=65536
        timer.start()
        try:
            with pytest.raises(KeyboardInterrupt):
                store.transaction(lambda w:w.write_json_chunks({'body':'x'*1000000},lambda *args:None))
            assert finished.is_set(), 'transaction returned before interrupted native task drained'
        finally:
            release.set();timer.join()
