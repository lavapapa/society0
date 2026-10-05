"""原生同步 zstd 的帧大小、值、writer 作用域及事务故障。"""
import json
import threading
import pytest
from society0.kernel.storage import StageStore, StorageError
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
from society0.kernel.models import ResourceCalls, RESOURCE_SCHEMA
from society0.kernel import _json_chunks as codec


def test_writer_small_and_large_native_frames_restore(tmp_path):
    before = {t.ident for t in threading.enumerate()}
    with StageStore.create(tmp_path/'run', [*THREAD_SCHEMA, *RESOURCE_SCHEMA]) as store:
        threads = ThreadStore(store); tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role':'user','content':'small'})
        body = {'role':'user','content':'中文🙂\\\"'*100000}
        threads.append_message(tid, body)
        calls = ResourceCalls(store); identifier = calls.begin('test','','',body)
        assert threads.read_messages(tid)[-1] == body
        assert calls.read(identifier)[0]['payload'] == body
        assert {t.ident for t in threading.enumerate()} == before
        sizes = store.read(lambda r:r.query('SELECT raw_bytes FROM thread_chunks'))
        assert all(0 < size <= 65536 for (size,) in sizes)
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert ThreadStore(restored).read_messages(tid)[-1] == body
        assert ResourceCalls(restored).read(identifier)[0]['payload'] == body


def test_writer_encoding_callback_expires(tmp_path):
    with StageStore.create(tmp_path/'run',['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        expired=store.transaction(lambda writer:writer.write_json_chunks)
        with pytest.raises(StorageError,match='scope'):
            expired({'body':'later'},lambda size,body:None)


def test_native_encoder_matches_json_and_invalid_value_rolls_back(tmp_path):
    with StageStore.create(tmp_path/'run',['CREATE TABLE item(id INTEGER PRIMARY KEY)']) as store:
        value={'body':'\\"\n中文🙂'*100000,'tail':[1,2.5,None,True]}
        chunks=[]
        store.transaction(lambda w:w.write_json_chunks(value,lambda size,body:chunks.append((size,body))))
        assert codec.decode_chunks(body for _,body in chunks)==value
        assert all(size==len(codec.decode_chunk(body)) and size<=65536 for size,body in chunks)
        def invalid(w):
            w.execute('INSERT INTO item VALUES(1)')
            w.write_json_chunks([value,float('nan')],lambda *args:None)
        with pytest.raises(ValueError):store.transaction(invalid)
        assert store.read(lambda r:r.query('SELECT * FROM item'))==[]
        store.transaction(lambda w:w.execute('INSERT INTO item VALUES(2)'))
        assert store.read(lambda r:r.query('SELECT * FROM item'))==[(2,)]


def test_encoder_sink_failure_stops_effects_without_pending_tasks():
    chunks=[]; owner=threading.get_ident()
    def fail(size,body):
        chunks.append((size,body))
        assert threading.get_ident()==owner
        raise OSError('sink failure')
    with pytest.raises(OSError,match='sink failure'):
        codec.write_chunks({'body':'x'*3000000},fail)
    assert len(chunks)==1 and chunks[0][0]==65536
    good=[];codec.write_chunks({'body':'x'*3000000},lambda size,body:good.append(body))
    assert codec.decode_chunks(good)=={'body':'x'*3000000}


def test_native_compression_failure_rolls_back_sql_and_remains_usable(tmp_path,monkeypatch):
    native=codec.zstd.ZstdCompressor
    class Failed:
        FLUSH_FRAME=native.FLUSH_FRAME
        def __init__(self,**kwargs):pass
        def compress(self,*args):raise OSError('native compression failure')
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        monkeypatch.setattr(codec.zstd,'ZstdCompressor',Failed)
        with pytest.raises(OSError,match='compression failure'):
            threads.append_message(tid,{'role':'user','content':'x'*1000000})
        assert threads.read_messages(tid)==[]
        monkeypatch.setattr(codec.zstd,'ZstdCompressor',native)
        threads.append_message(tid,{'role':'user','content':'y'*1000000})
        assert threads.read_messages(tid)==[{'role':'user','content':'y'*1000000}]
