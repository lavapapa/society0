"""Thread 存储的非作者完整性、范围与生命周期验收。"""
import json

import pytest

from society0.kernel.storage import StageReader, StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore


def create(path):
    store=StageStore.create(path,THREAD_SCHEMA)
    return store,ThreadStore(store)


def test_review_failed_large_append_rolls_back_chunks_and_head(tmp_path):
    store,threads=create(tmp_path/'run')
    with store:
        thread=threads.open('a',0,'working')
        before=threads.describe(thread)
        body={'content':'大段原文🙂'*100000,'invalid':object()}
        with pytest.raises(TypeError): threads.append_message(thread,body)
        assert threads.describe(thread)==before
        assert store.read(lambda r:r.query('SELECT COUNT(*) FROM thread_chunks WHERE thread_id=? AND seq>1',(thread,)))==[(0,)]
        store.complete(1)
        with StageStore.restore(store.path,tmp_path/'restored') as restored:
            assert ThreadStore(restored).describe(thread)==before
            assert ThreadStore(restored).read_messages(thread)==[]


def test_review_range_late_payload_reads_one_block_and_preserves_complete_json(tmp_path,monkeypatch):
    import society0.kernel.threads as module
    store,threads=create(tmp_path/'run')
    with store:
        thread=threads.open('a',0,'working')
        message={'role':'user','content':'甲🙂"\\\n'*150000,'tool_calls':[{'id':'one','unknown':{'x':None}}]}
        seq=threads.append_message(thread,message)
        expected=json.dumps(message,ensure_ascii=False,separators=(',',':')).encode()
        calls=[]
        decompress=module.zlib.decompress
        def counted(data):
            calls.append(len(data))
            return decompress(data)
        monkeypatch.setattr(module.zlib,'decompress',counted)
        page=threads.tail(thread,after_seq=seq-1,inline_payload_bytes=16)
        assert 'payload_ref' in page['items'][0] and calls==[]
        chunk=threads.read_payload(thread,seq,offset=len(expected)-7,size=7)
        assert chunk['data']==expected[-7:] and len(calls)==1
        assert threads.read_messages(thread)==[message]


def test_review_live_tail_snapshot_never_mixes_head_and_new_append(tmp_path):
    store,threads=create(tmp_path/'run')
    with store:
        thread=threads.open('a',0,'working')
        observer=ThreadStore(StageReader(store.path))
        original=observer.store.read
        inserted=[]
        def interleave(callback,**kwargs):
            def inside(view):
                query=view.query
                def q(sql,*args,**options):
                    result=query(sql,*args,**options)
                    if sql.startswith('SELECT actor,') and not inserted:
                        inserted.append(threads.append_message(thread,{'role':'user','content':'new'}))
                    return result
                view.query=q
                return callback(view)
            return original(inside,**kwargs)
        observer.store.read=interleave
        page=observer.tail(thread)
        assert page['total']==1 and [row['seq'] for row in page['items']]==[1]
        observer.store.read=original
        new=observer.tail(thread,after_seq=page['next_seq'])
        assert new['total']==2 and new['items'][0]['payload']['content']=='new'


def test_review_close_reopen_restore_keeps_all_requests_and_diagnostics(tmp_path):
    store,threads=create(tmp_path/'run')
    with store:
        thread=threads.open('a',{'time':4,'phase':'act'},'operating',provider_session_id='stable')
        first=threads.append_message(thread,{'role':'user','content':'first'})
        request=threads.record_request(thread,provider_options={'model':'x','temperature':0},physical_request_id='physical-one')
        threads.close(thread,'waiting')
        threads.reopen(thread)
        threads.append_message(thread,{'role':'user','content':'second'})
        threads.event(thread,'response',{'tool_calls':[{'id':'one','arguments':'{}'}]})
        threads.close(thread,'completed')
        complete=threads.tail(thread)
        store.complete(1)
        threads.reopen(thread)
        threads.event(thread,'failure',{'reason':'uncertain external response'})
        store.abort_step()
        diagnostic=ThreadStore(StageReader(store.path))
        assert diagnostic.tail(thread)['items'][-1]['kind']=='failure'
        assert diagnostic.read_request(thread,request)['messages']==[{'role':'user','content':'first'}]
        with StageStore.restore(store.path,tmp_path/'restored',step=1) as restored:
            recovered=ThreadStore(restored)
            assert recovered.tail(thread)==complete
            assert recovered.describe(thread)['provider_session_id']=='stable'
            assert recovered.read_request(thread,request)['physical_request_id']=='physical-one'
            assert len(recovered.read_messages(thread))==2


def test_review_request_capture_does_not_reconstruct_history(tmp_path,monkeypatch):
    import society0.kernel.threads as module
    store,threads=create(tmp_path/'run')
    with store:
        thread=threads.open('a',0,'working')
        store.transaction(lambda w:[module._append(w,thread,'message',{'role':'user','content':str(i)}) for i in range(2000)])
        def forbidden(*args,**kwargs): raise AssertionError('request capture rebuilt messages')
        monkeypatch.setattr(module,'_messages',forbidden)
        monkeypatch.setattr(module,'_load',forbidden)
        seq=threads.record_request(thread,provider_options={},physical_request_id='physical')
        size=store.read(lambda r:r.query('SELECT raw_bytes FROM thread_events WHERE thread_id=? AND seq=?',(thread,seq)))[0][0]
        assert size<256


def test_review_tool_receipt_failure_is_atomic_and_identity_survives_restore(tmp_path):
    store, threads = create(tmp_path/'run')
    with store:
        tid = threads.open('alice', {'time':1,'phase':'p'}, 'decision')
        call = {'id':'call1','function':{'name':'buy','arguments':'{"quantity":1}'}}
        before = threads.describe(tid)
        with pytest.raises(TypeError):
            threads.save_tool_result(tid, call, {'bad':object()})
        assert threads.describe(tid) == before
        assert threads.get_tool_result(tid,'call1') is None
        seq = threads.save_tool_result(tid,call,'complete original result')
        head = threads.describe(tid)
        assert threads.save_tool_result(tid,call,'complete original result') == seq
        assert threads.describe(tid) == head
        with pytest.raises(ValueError):
            threads.save_tool_result(tid,{**call,'function':{'name':'sell','arguments':'{}'}},'complete original result')
        assert threads.describe(tid) == head
        threads.close(tid,'completed')
        store.complete(1)
        with StageStore.restore(store.path,tmp_path/'restored') as restored:
            recovered=ThreadStore(restored)
            assert recovered.find('alice',{'phase':'p','time':1}) == tid
            assert recovered.get_tool_result(tid,'call1') == {'call':call,'content':'complete original result','metadata':None}
            assert recovered.read_messages(tid)==[{'role':'tool','tool_call_id':'call1','content':'complete original result'}]


def test_review_artifact_actor_isolation_and_restored_full_range(tmp_path):
    import shutil
    store, threads = create(tmp_path/'run')
    with store:
        a=threads.open('alice',0,'decision')
        b=threads.open('bob',0,'decision')
        data=b'\xff\x00'+('原文🙂'*20000).encode()
        artifact=store.prepare_artifact(iter([data[:8000],data[8000:]]))
        threads.register_artifact(a,'shell-old/output/1.stdout',artifact,actor='alice')
        with pytest.raises(PermissionError):
            threads.read_artifact(a,'shell-old/output/1.stdout',actor='bob')
        with pytest.raises(KeyError):
            threads.read_artifact(b,'shell-old/output/1.stdout',actor='bob')
        store.complete(1)
    restored=StageStore.restore(tmp_path/'run',tmp_path/'restored')
    shutil.rmtree(tmp_path/'run')
    with restored:
        recovered=ThreadStore(restored)
        pieces=[]
        offset=0
        while True:
            part=recovered.read_artifact(a,'shell-old/output/1.stdout',actor='alice',offset=offset,size=4096)
            pieces.append(part['data'])
            if part['next_offset'] is None: break
            offset=part['next_offset']
        assert b''.join(pieces)==data
