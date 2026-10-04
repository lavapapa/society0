"""结果页明确区分任意业务 JSON 与原文引用，保封存块分页局部复用。"""
import json
from contextlib import contextmanager
import pytest
from society0.kernel.datasets import DATASET_SCHEMA,Datasets
from society0.kernel.results import RESULTS_SCHEMA,Results,StepResult,DatasetTable
from society0.kernel.storage import StageStore


@pytest.mark.asyncio
@pytest.mark.parametrize('sealed',[False,True])
async def test_record_reference_shaped_value_remains_exact_json(tmp_path,sealed):
    business={'kind':'record_ref','id':'anything','origin':'run','ordinal':0,'total_bytes':123,
              'dataset':{'kind':'dataset','id':'fake','artifact':'never-read'}}
    values=[business,{'body':'完整🙂'*30000}]
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA+DATASET_SCHEMA) as store:
        results=Results(store)
        rows=DatasetTable(Datasets(store).import_rows('data',values)) if sealed else values
        header=await results.write_phase(1,0,'step',StepResult(tables={'table':rows}))
        reference=header['tables']['table']
        first=results.page(reference,limit=1,max_bytes=1024)
        assert first['items'][0]['value']==business
        assert first['items'][0]['ordinal']==0
        second=results.page(reference,cursor=first['next_cursor'],max_bytes=512)
        assert len(json.dumps(second,ensure_ascii=False,separators=(',',':')).encode())<=512
        row=second['items'][0]
        assert 'payload_ref' in row and 'value' not in row and row['ordinal']==1
        raw=bytearray();offset=0
        while True:
            part=results.read_record(row['payload_ref'],offset=offset,size=32768)
            raw.extend(part['data'])
            if part['next_offset'] is None:break
            offset=part['next_offset']
        assert json.loads(raw)==values[1]
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restore') as restored:
        assert Results(restored).page(reference,limit=1)['items'][0]['value']==business


@pytest.mark.asyncio
async def test_workbench_thousand_small_dataset_rows_fetch_shared_blob_once(tmp_path,monkeypatch):
    from society0.kernel.workbench import _rows
    values=[{'i':i} for i in range(1000)]
    queries=[];opens=[];original=Datasets._open
    class Connection:
        def __init__(self,connection):self.connection=connection
        def execute(self,sql,bindings=()):
            if 'FROM blocks' in sql:queries.append(sql)
            return self.connection.execute(sql,bindings)
    @contextmanager
    def opened(self,reference):
        opens.append(reference['id'])
        with original(self,reference) as (connection,*rest):yield Connection(connection),*rest
    with StageStore.create(tmp_path/'run',RESULTS_SCHEMA+DATASET_SCHEMA) as store:
        ref=Datasets(store).import_rows('tiny',values)
        results=Results(store)
        header=await results.write_phase(1,0,'step',StepResult(tables={'data':DatasetTable(ref)}))
        monkeypatch.setattr(Datasets,'_open',opened)
        assert list(_rows(results,header['tables']['data']))==values
        assert len(queries)==1 and len(opens)==1
