"""完整分析目录脱离源后仍可读跨插件原文，明确无写入/恢复资格。"""
import shutil
import pytest
from society0.kernel.storage import StageStore,StageReader,StorageError
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
from society0.kernel.memory import Memory,MEMORY_SCHEMA
from society0.kernel.results import Results,RESULTS_SCHEMA,StepResult,DatasetTable
from society0.kernel.datasets import Datasets,DATASET_SCHEMA
from tests.primary.test_kernel_memory import Client,Embed


@pytest.mark.asyncio
async def test_analysis_directory_is_self_contained_after_source_removal(tmp_path):
    source=tmp_path/'run';export=tmp_path/'analysis'
    with StageStore.create(source,THREAD_SCHEMA+MEMORY_SCHEMA+RESULTS_SCHEMA+DATASET_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',{'time':'2026-10-04','phase':'decision'},'decision')
        message={'role':'user','content':'完整原文🙂'*20000}
        threads.append_message(tid,message)
        memory=Memory(store,threads,embed=Embed(),client=Client())
        mid=(await memory.seed('a','seed',timestamp=1,entries=[{'content':'记忆正文','metadata':{'source':tid}}]))[0]
        original=memory.get(mid,actor='a');await memory.close()
        dataset=Datasets(store).import_rows('facts',[{'value':2**90},{'value':'完整'}])
        await Results(store).write_phase(1,0,'analysis',StepResult(tables={'facts':DatasetTable(dataset)},metrics={'n':2}))
        store.complete(1)
        # 导出以后未完成的改动不进入已固定分析目录。
        with StageStore.prepare_readonly(source,export,step=1):pass
        threads.append_message(tid,{'role':'user','content':'未完成步骤'})
    shutil.rmtree(source)
    with StageReader(export) as reader:
        assert ThreadStore(reader).read_messages(tid)==[message]
        memory=Memory(reader,ThreadStore(reader),embed=None,client=None)
        assert memory.get(mid,actor='a')==original
        records=[];assert memory.export('a',records.append)==1
        assert records[0]['embedding']==original['embedding']
        results=Results(reader);header=results.phase(1,0)
        assert results.page(header['tables']['facts'])['items']==[{'value':2**90},{'value':'完整'}]
        assert results.metric('analysis','n')==2
    with pytest.raises(StorageError):StageStore.open(export)
    with pytest.raises(StorageError):StageStore.restore(export,tmp_path/'forbidden')
