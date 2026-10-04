"""完整阶段结果、一次性表迭代、引用范围与恢复。"""
import json
import pytest
from society0.kernel.results import RESULTS_SCHEMA,Results,StepResult
from society0.kernel.storage import StageStore


@pytest.mark.asyncio
async def test_results_preserve_all_fields_stream_table_once_and_restore(tmp_path):
    iterated=[]
    def rows():
        for index in range(130):
            iterated.append(index)
            yield {'id':index,'text':'完整🙂'*10000 if index==64 else str(index)}
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        results=Results(store)
        reference=await results.write_phase(1,0,'rule',StepResult(metrics={'count':130},tables={'rows':rows()},
            artifacts={'original':'artifact-reference'},observations={'actual':[1,2]},notes='完整说明'),activations=(),elapsed_s=.1)
        assert iterated==list(range(130))
        table=reference['tables']['rows']
        found=[];cursor=None
        while True:
            page=results.page(table,cursor=cursor,limit=9,max_bytes=4096)
            assert page['total']==130
            assert len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())<=4096
            for item in page['items']:
                if 'payload_ref' in item:
                    raw=bytearray();offset=0
                    while True:
                        part=results.read_record(item['payload_ref'],offset=offset,size=16384)
                        raw.extend(part['data'])
                        if part['next_offset'] is None:break
                        offset=part['next_offset']
                    item=json.loads(raw)
                else:item=item['value']
                found.append(item)
            cursor=page['next_cursor']
            if cursor is None:break
        assert [row['id'] for row in found]==list(range(130))
        assert found[64]['text']=='完整🙂'*10000
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        read=Results(restored)
        assert read.phase(1,0)['notes']=='完整说明'
        assert read.summary()['row_count']==130


@pytest.mark.asyncio
async def test_current_metrics_replace_phase_keys_and_preserve_restore(tmp_path):
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        results=Results(store)
        await results.write_phase(1,0,'phase',StepResult(metrics={'gone':1,'value':'完整'*2000}),
            activations=({'actor_id':'a','status':'waiting','round':1,'value':None,'reason':'pause','elapsed_s':.1},))
        await results.write_phase(1,1,'phase',StepResult(metrics={'value':2}),
            activations=({'actor_id':'a','status':'incomplete','round':1,'value':None,'reason':'budget','elapsed_s':.2},))
        assert results.metric('phase','value')==2
        with pytest.raises(KeyError):results.metric('phase','gone')
        assert results.summary()=={'phase_count':2,'activation_count':2,'waiting_count':1,'incomplete_count':1}
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert Results(restored).metric('phase','value')==2


@pytest.mark.asyncio
async def test_result_page_encodes_each_item_once_and_fixed_prefix(tmp_path,monkeypatch):
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA) as store:
        results=Results(store)
        header=await results.write_phase(1,0,'phase',StepResult(tables={'rows':({'i':i} for i in range(1000))}))
        reference=header['tables']['rows']
        encoded_items=0;original=json.dumps
        def counted(value,*args,**kwargs):
            nonlocal encoded_items
            if isinstance(value,dict):encoded_items+=len(value.get('items',()))
            return original(value,*args,**kwargs)
        monkeypatch.setattr(json,'dumps',counted)
        page=results.page(reference,limit=1000,max_bytes=65536)
        assert len(page['items'])==1000
        assert encoded_items<=1000
        first=results.page(reference,limit=1)
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert len(Results(restored).page(reference)['items'])==100
        with pytest.raises(ValueError,match='cursor'):Results(restored).page(reference,cursor=first['next_cursor'])
