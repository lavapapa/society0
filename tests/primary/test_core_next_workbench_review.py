"""工作台导出的独立恢复范围与完整原文消费者。"""
from pathlib import Path
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
from society0.kernel import workbench


def test_review_selected_moment_exports_all_threads_above_query_default(tmp_path):
    moment={'time':1,'phase':'work'}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        for number in range(1001):
            tid=threads.open('actor',moment,'kind-'+str(number))
            threads.append_message(tid,{'role':'user','content':str(number)})
        store.complete(1)
        with StageStore.prepare_readonly(store.path,tmp_path/'view',step=1) as reader:
            sessions=workbench._sessions(reader,ThreadStore(reader),'actor',moment,1,'source')
            assert len(sessions)==1001
            assert [s['events'][1]['data']['content'] for s in sessions]==[str(i) for i in range(1001)]


@pytest.mark.asyncio
async def test_review_temporary_complete_view_is_removed_on_export_error(tmp_path,monkeypatch):
    from society0.kernel.runner import run_plan
    from examples.core_next.conversation_pilot import build
    await run_plan(tmp_path/'run',build({'release':{'commit':'review'},'start':1,'end':1}))
    original=workbench.TemporaryDirectory;paths=[]
    def temporary(**kwargs):
        result=original(dir=tmp_path,**kwargs);paths.append(Path(result.name));return result
    monkeypatch.setattr(workbench,'TemporaryDirectory',temporary)
    original_module=workbench._module
    def fail(*args,**kwargs):raise OSError('render preparation failed')
    monkeypatch.setattr(workbench,'_module',fail)
    with pytest.raises(OSError,match='preparation'):
        workbench.export_payload([workbench.RunSelection(tmp_path/'run','review',(1,),('a',))])
    assert paths and all(not path.exists() for path in paths)
    monkeypatch.setattr(workbench,'_module',original_module)
    payload=workbench.export_payload([workbench.RunSelection(tmp_path/'run','review',(1,),('a',))])
    assert payload['versions'][0]['runs'][0]['complete_step']==1
    assert all(not path.exists() for path in paths)
