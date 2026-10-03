"""结果输入形状明确，完整值和封存数据集走同一公开读取入口。"""
import json
import pytest
from society0.kernel import results as module
from society0.kernel.datasets import DATASET_SCHEMA, Datasets
from society0.kernel.storage import StageStore


@pytest.mark.asyncio
async def test_table_value_preserves_tight_dataframe_shape_and_restore(tmp_path):
    value={'columns':['name','amount'],'index':[10,'10'],'data':[['甲',2**90],['乙',-3]],
           'index_names':['主体'],'column_names':[None]}
    with StageStore.create(tmp_path/'run',module.RESULTS_SCHEMA) as store:
        results=module.Results(store)
        header=await results.write_phase(1,0,'analysis',module.StepResult(tables={'table':module.TableValue(value)}))
        assert results.page(header['tables']['table'])['items']==[value]
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        results=module.Results(store)
        assert results.page(results.phase(1,0)['tables']['table'])['items']==[value]


@pytest.mark.asyncio
@pytest.mark.parametrize('value',[{'column':[1,2]},'abc',b'abc'])
async def test_ambiguous_table_input_rejected_before_writing(tmp_path,value):
    with StageStore.create(tmp_path/'run',module.RESULTS_SCHEMA) as store:
        results=module.Results(store)
        revision=store.read(lambda v:v.live_revision)
        with pytest.raises(TypeError,match='TableValue|rows'):
            await results.write_phase(1,0,'analysis',module.StepResult(tables={'table':value}))
        assert store.read(lambda v:v.live_revision)==revision


@pytest.mark.asyncio
async def test_dataset_table_reuses_body_and_reads_large_records_after_restore(tmp_path):
    values=[{'n':0,'body':'正文🙂'*20000},{'n':1,'body':'完整'}]
    with StageStore.create(tmp_path/'run',module.RESULTS_SCHEMA+DATASET_SCHEMA) as store:
        datasets=Datasets(store);reference=datasets.import_rows('source',values)
        results=module.Results(store)
        header=await results.write_phase(1,0,'analysis',module.StepResult(tables={'table':module.DatasetTable(reference)}))
        assert header['tables']['table']==reference
        assert results.summary()['row_count']==2
        assert store.read(lambda v:v.query("SELECT count(*) FROM result_sets WHERE kind='table'"))[0][0]==0
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        results=module.Results(store);reference=results.phase(1,0)['tables']['table']
        page=results.page(reference,limit=1,max_bytes=512)
        assert len(json.dumps(page,ensure_ascii=False,separators=(',',':')).encode())<=512
        ref=page['items'][0];raw=bytearray();offset=0
        while True:
            part=results.read_record(ref,offset=offset,size=16384);raw.extend(part['data'])
            if part['next_offset'] is None:break
            offset=part['next_offset']
        assert json.loads(raw)==values[0]
        assert results.page(reference,cursor=page['next_cursor'])['items']==[values[1]]


@pytest.mark.asyncio
async def test_two_mechanisms_share_one_dataset_schema_and_service(tmp_path):
    from society0.kernel import datasets as dataset_module
    from society0.kernel.composition import compose
    from society0.kernel.plugins import Plugin
    def mechanism(name):
        def install(context):
            data=context.require('datasets','datasets')
            context.provide('data',data)
            context.provide('reference',data.import_rows(name,[{'owner':name}]))
        return Plugin(name,('datasets',),install)
    async with compose(tmp_path/'run',[dataset_module.dataset_plugin(),mechanism('market'),mechanism('messages')]) as host:
        assert host.service('market','data') is host.service('messages','data')
        data=host.service('datasets','datasets')
        assert data.get(host.service('market','reference'),0)=={'owner':'market'}
        assert data.get(host.service('messages','reference'),0)=={'owner':'messages'}
