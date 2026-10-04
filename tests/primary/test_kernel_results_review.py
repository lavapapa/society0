"""结果原文范围与完整恢复的非作者消费者。"""
import json
import zlib
import pytest
from society0.kernel.results import Results,RESULTS_SCHEMA,StepResult
from society0.kernel.storage import StageStore

@pytest.mark.asyncio
async def test_review_late_result_range_decodes_only_intersecting_chunk(tmp_path,monkeypatch):
    value={'original':'完整🙂"\\'*300000}
    raw=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        results=Results(store)
        header=await results.write_phase(1,0,'phase',StepResult(tables={'rows':iter((value,))}))
        page=results.page(header['tables']['rows'],limit=1000000,max_bytes=512)
        assert len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())<=512
        reference=page['items'][0]['payload_ref']
        assert page['items'][0]['raw_bytes']==len(raw)
        calls=[];decompress=zlib.decompress
        def counted(data,*args,**kwargs):calls.append(len(data));return decompress(data,*args,**kwargs)
        monkeypatch.setattr(zlib,'decompress',counted)
        offset=(len(raw)//65536-1)*65536+17
        part=results.read_record(reference,offset=offset,size=31)
        assert part['data']==raw[offset:offset+31] and part['total_bytes']==len(raw)
        assert len(calls)==1
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restore') as restored:
        assert Results(restored).read_record(reference,offset=offset,size=31)['data']==part['data']

@pytest.mark.asyncio
async def test_review_results_cursor_does_not_cross_runs_or_include_failed_next_step(tmp_path):
    with StageStore.create(tmp_path/'source',RESULTS_SCHEMA) as store:
        results=Results(store)
        header=await results.write_phase(1,0,'p',StepResult(tables={'rows':range(4)}))
        page=results.page(header['tables']['rows'],limit=1)
        store.complete(1)
        def failed():
            yield 1
            raise ValueError('source failed')
        with pytest.raises(ValueError):await results.write_phase(2,0,'p',StepResult(tables={'rows':failed()}))
        store.abort_step()
    with StageStore.restore(tmp_path/'source',tmp_path/'restored') as store:
        results=Results(store)
        with pytest.raises(ValueError,match='cursor'):results.page(header['tables']['rows'],cursor=page['next_cursor'])
        assert [item['value'] for item in results.page(header['tables']['rows'])['items']]==[0,1,2,3]
        with pytest.raises(KeyError):results.phase(2,0)
        assert results.summary()['row_count']==4
