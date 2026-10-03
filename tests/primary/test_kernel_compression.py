"""规范 writer 共享压缩资源，正文事实与作用域保持不变。"""
import json
import threading
import zlib
import pytest
from society0.kernel.storage import StageStore, StorageError
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
from society0.kernel.models import ResourceCalls, RESOURCE_SCHEMA


def test_writer_small_path_and_shared_large_pool_close(tmp_path,monkeypatch):
    from society0.kernel import _json_chunks as codec
    created=[]
    native=codec.ThreadPoolExecutor
    def factory(*args,**kwargs):
        pool=native(*args,**kwargs);created.append(pool);return pool
    monkeypatch.setattr(codec,'ThreadPoolExecutor',factory)
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA],compression_workers=2,
                           compression_inflight_bytes=131072) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'small'})
        assert not created
        body={'role':'user','content':'中文🙂\\\"'*100000}
        threads.append_message(tid,body)
        calls=ResourceCalls(store);identifier=calls.begin('test','','',body)
        assert len(created)==1
        assert threads.read_messages(tid)[-1]==body
        assert calls.read(identifier)[0]['payload']==body
        store.complete(1)
    assert created[0]._shutdown
    assert all(not thread.is_alive() for thread in created[0]._threads)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert ThreadStore(restored).read_messages(tid)[-1]==body


def test_writer_encoding_iterator_expires_and_partial_consumption_drains(tmp_path):
    with StageStore.create(tmp_path/'run',['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        retained=[]
        def write(writer):
            stream=writer.encode_chunks({'body':'x'*1000000})
            next(stream)
            retained.append(stream)
        store.transaction(write)
        assert list(retained[0])==[]
        expired=store.transaction(lambda writer:writer.encode_chunks({'body':'later'}))
        with pytest.raises(StorageError,match='scope'):
            next(expired)


def test_parallel_encoder_matches_serial_and_drains_on_invalid_json(tmp_path,monkeypatch):
    from society0.kernel import _json_chunks as codec
    threads=set();native=codec.zlib.compress
    def compress(raw,level):
        threads.add(threading.get_ident())
        assert type(raw) is bytes
        return native(raw,level)
    monkeypatch.setattr(codec.zlib,'compress',compress)
    with StageStore.create(tmp_path/'run',['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        value={'body':'\\"\n中文🙂'*100000,'tail':[1,2.5,None,True]}
        chunks=store.transaction(lambda writer:list(writer.encode_chunks(value)))
        assert json.loads(b''.join(zlib.decompress(body) for _,body in chunks))==value
        assert threading.get_ident() not in threads
        with pytest.raises(ValueError):
            store.transaction(lambda writer:list(writer.encode_chunks([value,float('nan')])))
        store.transaction(lambda writer:writer.execute('INSERT INTO item VALUES(1)'))
        assert store.read(lambda reader:reader.query('SELECT * FROM item'))==[(1,)]


def test_encoder_byte_budget_is_enforced_before_next_submit(monkeypatch):
    from society0.kernel import _json_chunks as codec
    from concurrent.futures import Future
    active=0;peak=0
    class Counted(Future):
        def __init__(self,raw):
            super().__init__();self.raw_size=len(raw);self.counted=True
            self.set_result(zlib.compress(raw,3))
        def result(self,*args,**kwargs):
            nonlocal active
            if self.counted:active-=self.raw_size;self.counted=False
            return super().result(*args,**kwargs)
    class Pool:
        def __init__(self,**kwargs):pass
        def submit(self,function,raw,level):
            nonlocal active,peak
            active+=len(raw);peak=max(peak,active)
            return Counted(raw)
        def shutdown(self,**kwargs):pass
    monkeypatch.setattr(codec,'ThreadPoolExecutor',Pool)
    encoder=codec.ChunkEncoder(4,131072)
    try:
        chunks=list(encoder.encode({'body':'x'*3000000}))
        assert peak<=131072 and active==0
        assert json.loads(b''.join(zlib.decompress(body) for _,body in chunks))['body']=='x'*3000000
    finally:encoder.close()


def test_native_compression_failure_rolls_back_sql_and_pool_remains_usable(tmp_path,monkeypatch):
    from society0.kernel import _json_chunks as codec
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        native=codec.zlib.compress
        def fail(raw,level):raise OSError('compression worker failure')
        monkeypatch.setattr(codec.zlib,'compress',fail)
        with pytest.raises(OSError,match='worker failure'):
            threads.append_message(tid,{'role':'user','content':'x'*1000000})
        assert threads.read_messages(tid)==[]
        monkeypatch.setattr(codec.zlib,'compress',native)
        threads.append_message(tid,{'role':'user','content':'y'*1000000})
        assert threads.read_messages(tid)==[{'role':'user','content':'y'*1000000}]
