"""同一规范存储中的 Thread 原文与请求引用。"""
import json
from pathlib import Path
import pytest
from society0.kernel.storage import StageStore, StageReader
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA


def setup(path):
    store = StageStore.create(path, THREAD_SCHEMA)
    return store, ThreadStore(store)


def test_original_messages_requests_and_continuation(tmp_path):
    store, threads = setup(tmp_path / 'run')
    with store:
        thread = threads.open('actor', {'time': 1, 'phase': 'act'}, 'operating', metadata={'note': '原文'})
        messages = [{'role':'system','content':'规则'}, {'role':'user','content':[{'type':'text','text':'你好🌍\u0000"'}]}]
        seqs = [threads.append_message(thread, message) for message in messages]
        request = threads.record_request(thread, provider_options={'model':'test','temperature':0}, physical_request_id='p1')
        threads.append_message(thread, {'role':'assistant','content':'回答'})
        restored = threads.read_request(thread, request)
        assert restored['messages'] == messages
        assert restored['provider_options'] == {'model':'test','temperature':0}
        retry = threads.record_request(thread, message_seqs=seqs[::-1], provider_options={}, physical_request_id='p2', retry_of='p1')
        assert threads.read_request(thread, retry)['messages'] == messages[::-1]
        provider_id = threads.describe(thread)['provider_session_id']
        threads.close(thread, 'waiting')
        with pytest.raises(ValueError): threads.append_message(thread, {'role':'user','content':'closed'})
        threads.reopen(thread)
        threads.close(thread, 'completed')
        assert threads.describe(thread)['provider_session_id'] == provider_id
        outcomes = [item['payload']['outcome'] for item in threads.tail(thread, limit=100)['items'] if item['kind']=='closed']
        assert outcomes == ['waiting','completed']
        assert threads.read_messages(thread) == messages + [{'role':'assistant','content':'回答'}]


def test_tail_cursor_at_end_and_read_only_observer(tmp_path):
    store, threads = setup(tmp_path / 'run')
    with store:
        thread = threads.open('a', 0, 'operating')
        observer = ThreadStore(StageReader(store.path))
        page = observer.tail(thread, limit=1)
        assert page['total'] == 1
        empty = observer.tail(thread, after_seq=page['next_seq'])
        assert empty['items'] == [] and empty['next_seq'] == page['next_seq']
        seq = threads.append_message(thread, {'role':'user','content':'new'})
        assert observer.tail(thread, after_seq=empty['next_seq'])['items'][0]['seq'] == seq
        assert observer.list_threads(actor='a')['total'] == 1
        assert observer.list_threads(actor='b')['total'] == 0


def test_big_utf8_body_chunk_bound_and_restore(tmp_path):
    store, threads = setup(tmp_path / 'run')
    with store:
        thread = threads.open('a', 0, 'operating')
        message = {'role':'user','content':'🌍\u0000\\"'*100_000,'extra':{'values':[1,None,False,2.5]}}
        threads.append_message(thread, message)
        assert store.read(lambda r:r.query('SELECT max(length(payload)) FROM thread_chunks'))[0][0] <= 65536
        assert threads.read_messages(thread) == [message]
        store.complete(1)
        threads.append_message(thread, {'role':'user','content':'pending diagnostic'})
        assert len(ThreadStore(StageReader(store.path)).read_messages(thread)) == 2
        with StageStore.restore(store.path, tmp_path / 'restore') as restored:
            assert ThreadStore(restored).read_messages(thread) == [message]
            assert ThreadStore(restored).describe(thread)['provider_session_id'] == threads.describe(thread)['provider_session_id']


def test_invalid_json_rolls_back_whole_append(tmp_path):
    store, threads = setup(tmp_path / 'run')
    with store:
        thread = threads.open('a', 0, 'operating')
        before = threads.tail(thread)
        for value in [{'content':float('nan')}, {'content':object()}]:
            with pytest.raises((TypeError,ValueError)):
                threads.append_message(thread, value)
        assert threads.tail(thread) == before
        assert threads.read_messages(thread) == []


def test_requests_use_fixed_watermark_and_large_strings_never_full_encode(tmp_path, monkeypatch):
    import society0.kernel.threads as module
    original = module.json.dumps
    def bounded(value, *args, **kwargs):
        if isinstance(value, str):
            assert len(value) <= 8192
        return original(value, *args, **kwargs)
    store, threads = setup(tmp_path / 'run')
    with store:
        thread = threads.open('a', 0, 'operating')
        monkeypatch.setattr(module.json, 'dumps', bounded)
        for index in range(100):
            threads.append_message(thread, {'role':'user','content':'x'*100_000})
        request = threads.record_request(thread, provider_options={}, physical_request_id='req')
        size = store.read(lambda r:r.query('SELECT sum(raw_bytes) FROM thread_chunks WHERE thread_id=? AND seq=?',(thread,request)))[0][0]
        assert size < 256
        assert len(threads.read_request(thread, request)['messages']) == 100


def test_request_reference_validation_and_json_cycles_are_atomic(tmp_path):
    store, threads = setup(tmp_path / 'run')
    with store:
        thread = threads.open('a', 0, 'operating')
        other = threads.open('b', 0, 'operating')
        seq = threads.append_message(other, {'role':'user','content':'other'})
        with pytest.raises(ValueError):
            threads.record_request(thread, message_seqs=[seq], provider_options={}, physical_request_id='bad')
        cyclic = {}; cyclic['self'] = cyclic
        with pytest.raises(ValueError): threads.append_message(thread, cyclic)
        assert threads.tail(thread)['total'] == 1


def test_hot_append_and_tail_work_does_not_grow_with_history(tmp_path):
    import society0.kernel.threads as module
    counts = []
    for history in (1000,10000):
        store, threads = setup(tmp_path / str(history))
        with store:
            thread = threads.open('a',0,'operating')
            store.transaction(lambda writer:[module._append(writer,thread,'message',{'role':'user','content':'old'}) for _ in range(history)])
            store.complete(1)
            write_work = [0]
            store._connection.set_progress_handler(lambda:write_work.__setitem__(0,write_work[0]+1) or False,1)
            seq = threads.append_message(thread,{'role':'user','content':'new'})
            store._connection.set_progress_handler(None)
            read_work = [0]
            original_read = store.read
            def measured(callback, **kwargs):
                def wrap(view):
                    view._connection.set_progress_handler(lambda:read_work.__setitem__(0,read_work[0]+1) or False,1)
                    return callback(view)
                return original_read(wrap,**kwargs)
            store.read = measured
            page = threads.tail(thread,after_seq=seq-1,limit=1)
            assert page['items'][0]['payload']['content'] == 'new'
            counts.append((write_work[0],read_work[0]))
    print({'history_sizes':[1000,10000], 'append_tail_vm_steps':counts})
    assert counts[1][0] < counts[0][0]*2
    assert counts[1][1] < counts[0][1]*2


def test_large_tail_uses_reference_and_range_decodes_only_needed_blocks(tmp_path, monkeypatch):
    import society0.kernel.threads as module
    store, threads = setup(tmp_path / 'run')
    with store:
        thread = threads.open('a',0,'operating')
        message = {'role':'user','content':'引号"\\\n🌍'*1_000_000,'number':-123456789123456789,'float':1.2345e-200}
        seq = threads.append_message(thread,message)
        original = module._load
        monkeypatch.setattr(module,'_load',lambda *a:(_ for _ in ()).throw(AssertionError('full body read')))
        page = threads.tail(thread,after_seq=seq-1,inline_payload_bytes=1024)
        ref = page['items'][0]['payload_ref']
        assert ref['total_bytes'] > 10*1024*1024
        count = [0]
        decompress = module.zlib.decompress
        def counted(body):
            count[0] += 1
            return decompress(body)
        monkeypatch.setattr(module.zlib,'decompress',counted)
        chunk = threads.read_payload(thread,seq,offset=65520,size=64)
        assert count[0] == 2
        expected = json.dumps(message,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
        assert chunk['data'] == expected[65520:65584]
        assert chunk['next_offset'] == 65584 and chunk['total_bytes'] == len(expected)
        assert threads.read_payload(thread,seq,offset=len(expected),size=64)['data'] == b''
        assert threads.tail(thread,after_seq=page['next_seq'])['items'] == []
        monkeypatch.setattr(module,'_load',original)
        assert threads.read_messages(thread) == [message]


def test_find_receipts_and_artifacts_survive_process_restore(tmp_path):
    store, threads = setup(tmp_path/'run')
    with store:
        moment = {'time':1,'phase':'decision'}
        thread = threads.open('a',moment,'decision')
        assert threads.find('a',{'phase':'decision','time':1}) == thread
        assert threads.find('b',moment) is None
        call = {'id':'tool-1','function':{'name':'pay','arguments':'{"amount":5}'}}
        receipt = threads.save_tool_result(thread,call,'paid')
        assert threads.save_tool_result(thread,call,'paid') == receipt
        assert threads.get_tool_result(thread,'tool-1') == {'call':call,'content':'paid','metadata':None}
        with pytest.raises(ValueError):threads.save_tool_result(thread,{**call,'extra':1},'paid')
        ref = store.prepare_artifact([b'0123456789'])
        threads.register_artifact(thread,'shell://result-1',ref,actor='a')
        with pytest.raises(PermissionError):threads.lookup_artifact(thread,'shell://result-1',actor='b')
        store.complete(1)
        with StageStore.restore(store.path,tmp_path/'restored') as restored:
            resumed = ThreadStore(restored)
            assert resumed.find('a',moment) == thread
            assert resumed.get_tool_result(thread,'tool-1') == {'call':call,'content':'paid','metadata':None}
            assert resumed.lookup_artifact(thread,'shell://result-1',actor='a') == ref
            assert resumed.read_artifact(thread,'shell://result-1',actor='a',offset=3,size=4) == {'data':b'3456','total_bytes':10,'next_offset':7,'source':ref}
            assert resumed.read_messages(thread) == [{'role':'tool','tool_call_id':'tool-1','content':'paid'}]


def test_request_snapshot_watermark_survives_interleaved_append(tmp_path):
    store, threads = setup(tmp_path/'run')
    with store:
        thread = threads.open('a',0,'decision')
        old = {'role':'user','content':'before'}
        threads.append_message(thread,old)
        snapshot = threads.snapshot_messages(thread)
        threads.append_message(thread,{'role':'user','content':'after'})
        request = threads.record_request(thread,provider_options={},physical_request_id='retry',through=snapshot['through'])
        assert threads.read_request(thread,request)['messages'] == snapshot['messages'] == [old]
        for value in (-1,True,100000):
            with pytest.raises(ValueError):
                threads.record_request(thread,provider_options={},physical_request_id='bad',through=value)


def test_receipt_metadata_is_atomic_and_restorable(tmp_path):
    store, threads = setup(tmp_path/'run')
    with store:
        thread = threads.open('a',0,'decision')
        call = {'id':'finish','function':{'name':'end','arguments':'{}'}}
        metadata = {'terminal':True,'ledger':[{'name':'end','status':'completed'}],'structured_result':{'ok':True}}
        threads.save_tool_result(thread,call,'done',metadata=metadata)
        store.complete(1)
        with StageStore.restore(store.path,tmp_path/'restore') as restored:
            assert ThreadStore(restored).get_tool_result(thread,'finish')['metadata'] == metadata
        with pytest.raises(ValueError):
            threads.save_tool_result(thread,call,'done',metadata={'terminal':False})


def test_input_messages_and_consumer_cursor_commit_or_rollback_together(tmp_path):
    store,threads=setup(tmp_path/'run')
    with store:
        thread=threads.open('a',0,'decision')
        assert threads.input_cursor(thread,'fov') is None
        messages=[{'role':'user','content':'one'},{'role':'user','content':'two'}]
        threads.append_input(thread,messages,'fov',{'position':2})
        assert threads.read_messages(thread)==messages
        assert threads.input_cursor(thread,'fov')=={'position':2}
        before=threads.describe(thread)
        with pytest.raises(TypeError):
            threads.append_input(thread,[{'role':'user','content':'discard'}],'fov',{'bad':object()})
        assert threads.describe(thread)==before
        assert threads.input_cursor(thread,'fov')=={'position':2}
        assert threads.read_messages(thread)==messages
        store.complete(1)
        with StageStore.restore(store.path,tmp_path/'restore') as restored:
            assert ThreadStore(restored).input_cursor(thread,'fov')=={'position':2}
