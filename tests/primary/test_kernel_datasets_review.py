from tests.primary.provider_http import count_dataset_frames
"""不可变批次存储的独立消费者与工作量边界。"""
import json
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.datasets import Datasets, DATASET_SCHEMA


def test_review_dataset_page_serializes_items_linearly(tmp_path,monkeypatch):
    import society0.kernel.datasets as module
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        data=Datasets(store);reference=data.import_rows('numbers',range(1000))
        real=module._bytes;items_encoded=0
        def counted(value):
            nonlocal items_encoded
            items_encoded+=len(value['items']) if isinstance(value,dict) and 'items' in value else 1
            return real(value)
        monkeypatch.setattr(module,'_bytes',counted)
        page=data.page(reference,limit=1000,max_bytes=1_000_000)
        assert len(page['items'])==1000 and page['next_cursor'] is None
        assert items_encoded<=2000, 'page metadata repeatedly serializes prior items'


def test_review_dataset_restore_changes_cursor_identity_and_readonly_rejects_import(tmp_path):
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        data=Datasets(store);reference=data.import_rows('numbers',[1,2,3])
        cursor=json.loads(json.dumps(data.page(reference,limit=1)['next_cursor']))
        store.complete(1)
        with StageStore.prepare_readonly(store.path,tmp_path/'view') as reader:
            restored=Datasets(reader)
            with pytest.raises(ValueError,match='cursor'):restored.page(reference,cursor=cursor)
            with pytest.raises((AttributeError,RuntimeError)):
                restored.import_rows('forbidden',[4])
            assert restored.page(reference)['total']==3


def test_review_dataset_large_range_and_final_page_budget(tmp_path,monkeypatch):
    import society0.kernel.datasets as module
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        data=Datasets(store);value={'text':'完整🙂'*500000}
        reference=data.import_rows('large',[value]*4)
        page=data.page(reference,max_bytes=512)
        assert len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())<=512
        assert page['next_cursor'] is not None and 'payload_ref' in page['items'][0]
        raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
        calls=count_dataset_frames(monkeypatch)
        assert data.read_payload(reference,0,offset=len(raw)-17,size=17)['data']==raw[-17:]
        # 主键只定位末尾一帧，成本与此前几百帧无关。
        assert len(calls)==1


def test_review_shared_frames_preserve_adjacent_record_ranges(tmp_path,monkeypatch):
    import society0.kernel.datasets as module
    # 第二条起点距共享帧边界7字节，17字节读取跨两帧。
    first='x'*(module.CHUNK_BYTES-9)
    second={'text':'相邻🙂'*20000}
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        data=Datasets(store);reference=data.import_rows('mixed',[first,second,{},None,True,2**90])
        calls=count_dataset_frames(monkeypatch)
        raw=json.dumps(second,ensure_ascii=False,separators=(',',':')).encode()
        assert data.read_payload(reference,1,offset=0,size=17)['data']==raw[:17]
        assert len(calls)==2
        assert [data.get(reference,index) for index in range(6)]==[first,second,{},None,True,2**90]
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert Datasets(restored).get(reference,1)==second


def test_public_observation_requests_read_only_target_chunks_as_history_grows(tmp_path,monkeypatch):
    import base64
    import http.client
    from society0.kernel.observation import ObservationService,make_server
    from tests.primary.http_server import running
    target={'text':'目标🙂'*40000}
    raw=json.dumps(target,ensure_ascii=False,separators=(',',':')).encode()
    calls=count_dataset_frames(monkeypatch)
    for count in (10,5000):
        with StageStore.create(tmp_path/str(count),DATASET_SCHEMA) as store:
            ref=Datasets(store).import_rows('fixed-target',iter([target]+[{'history':i} for i in range(count)]))
            store.complete(1)
            with ObservationService(store.path) as service:
                server=make_server(service,port=0)
                with running(server) as (port,state):
                    for repeat in range(3):
                        before=len(calls)
                        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                        connection.request('POST','/',json.dumps({'method':'result_page','params':{'reference':ref,'limit':1,'max_bytes':512}}),{'Content-Type':'application/json'})
                        response=connection.getresponse();page=json.loads(response.read());connection.close()
                        assert response.status==200 and page['total']==count+1
                        assert page['items'][0]['payload_ref']['dataset']==ref
                        assert len(calls)==before  # 引用页不打开或解压正文。
                        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                        connection.request('POST','/',json.dumps({'method':'read_result_record','params':{'reference':page['items'][0]['payload_ref'],'offset':65530,'size':64}}),{'Content-Type':'application/json'})
                        response=connection.getresponse();part=json.loads(response.read());connection.close()
                        assert response.status==200 and base64.b64decode(part['data'])==raw[65530:65594]
                        assert len(calls)-before==2  # 新HTTP/Observation/reader仍只定位两帧。


@pytest.mark.parametrize('failure_frame',(1,2))
def test_shared_sink_failure_never_flushes_or_publishes_partial_batch(tmp_path,monkeypatch,failure_frame):
    import society0.kernel.datasets as module
    original=module.ChunkWriter;events=[]
    class Observed(original):
        def __init__(self,emit):
            def counted(size,body):
                events.append('frame')
                if events.count('frame')==failure_frame:raise OSError('frame write failed')
                return emit(size,body)
            super().__init__(counted)
        def finish(self):
            events.append('finish')
            return super().finish()
    monkeypatch.setattr(module,'ChunkWriter',Observed)
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        def producer():
            yield {'original':'small'}
            raise ValueError('producer failed')
        with pytest.raises(ValueError,match='producer'):Datasets(store).import_rows('bad',producer())
        assert events==[]
        def batch():
            yield {'small':'prefix'}
            yield {'large':'全文🙂'*100000}
            events.append('producer continued')
            yield {'unreachable':True}
        with pytest.raises(OSError,match='frame write'):Datasets(store).import_rows('bad',batch())
        assert events==['frame']*failure_frame
        assert store.read(lambda view:view.query('SELECT * FROM datasets'))==[]
        assert list((store.path/'artifacts').iterdir())==[]
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restore') as restored:
        assert restored.read(lambda view:view.query('SELECT * FROM datasets'))==[]


def test_shared_frame_cache_is_owned_only_by_one_read_request(tmp_path):
    with StageStore.create(tmp_path/'run',DATASET_SCHEMA) as store:
        data=Datasets(store);ref=data.import_rows('small',[1,2,3])
        with data._open(ref) as (connection,run,count,decode):
            assert decode.cache_info().currsize==0
            decode(0);decode(0)
            assert decode.cache_info().currsize==1 and decode.cache_info().hits==1
        assert decode.cache_info().currsize==0
        with data._open(ref) as (connection,run,count,new_decode):
            assert new_decode.cache_info().currsize==0 and new_decode is not decode
