from tests.primary.provider_http import count_dataset_frames
"""非作者：业务值与继续读取身份分层，完整线包预算与共享块工作量。"""
import json
import pytest
from society0.kernel.datasets import DATASET_SCHEMA,Datasets
from society0.kernel.results import RESULTS_SCHEMA,Results,StepResult,DatasetTable
from society0.kernel.storage import StageStore


@pytest.mark.asyncio
@pytest.mark.parametrize('sealed',[False,True])
async def test_review_envelope_pagination_recovers_every_value_with_bounded_wire(tmp_path,sealed):
    collision={'ordinal':3,'raw_bytes':7,'value':None,'payload_ref':{'id':'business-only'},'kind':'record_ref'}
    values=[None,False,2**150,collision,{'long':'汉🙂'*30000}]+[{'i':i,'v':'标记'*20} for i in range(20)]
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA+DATASET_SCHEMA) as store:
        results=Results(store)
        table=DatasetTable(Datasets(store).import_rows('records',values)) if sealed else values
        header=await results.write_phase(1,0,'phase',StepResult(tables={'records':table}))
        reference=header['tables']['records'];cursor=None;actual=[];ordinals=[]
        while True:
            page=results.page(reference,cursor=cursor,limit=100,max_bytes=512)
            assert len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())<=512
            assert page['total']==len(values) and page['items']
            for item in page['items']:
                assert ('value' in item)!=('payload_ref' in item)
                ordinals.append(item['ordinal'])
                if 'value' in item:actual.append(item['value'])
                else:
                    raw=bytearray();offset=0
                    while True:
                        part=results.read_record(item['payload_ref'],offset=offset,size=65536)
                        raw.extend(part['data'])
                        if part['next_offset'] is None:break
                        offset=part['next_offset']
                    actual.append(json.loads(raw))
            cursor=page['next_cursor']
            if cursor is None:break
        assert actual==values and ordinals==list(range(len(values)))


@pytest.mark.asyncio
async def test_review_workbench_shared_frame_is_decompressed_once(tmp_path,monkeypatch):
    import society0.kernel.datasets as datasets
    from society0.kernel.workbench import _rows

    values=[{'v':i,'literal':{'payload_ref':'business'}} for i in range(1000)]
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA+DATASET_SCHEMA) as store:
        ref=Datasets(store).import_rows('records',values)
        result=Results(store)
        table=(await result.write_phase(1,0,'phase',StepResult(tables={'records':DatasetTable(ref)})))['tables']['records']
        calls=count_dataset_frames(monkeypatch)
        assert list(_rows(result,table))==values
        assert len(calls)==1
