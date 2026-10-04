"""普通目录发现保留类型，结构数据与原文读取继续使用既有入口。"""
import json
import pytest
from society0.kernel.interaction import Information,InteractionScope,Moment
from tests.primary.test_kernel_information_sql import setup
from tests.primary.test_kernel_llm import setup as llm_setup,reply

@pytest.mark.asyncio
async def test_root_sql_container_types_and_unmodified_rows(tmp_path):
    store,provider=setup(tmp_path,count=4);info=Information(lambda scope,op,ref:ref.kind!='hidden')
    info.mount('/market',provider)
    try:
        with InteractionScope('alice',Moment(1,'read')) as scope:
            root=await info.list(scope,'/')
            assert root.items[0]['kind']=='directory'
            first=await info.list(scope,'/market',limit=1)
            second=await info.list(scope,'/market',limit=1,cursor=json.loads(json.dumps(first.next_cursor)))
            for item in first.items+second.items:
                assert item['kind']=='directory'
                assert (await info.stat(scope,item['path'])).kind==item['kind']
            assert first.total==second.total==2 and second.next_cursor is None
            assert [x['path'] for x in first.items+second.items]==['/market/orders','/market/docs']
            rows=await info.list(scope,'/market/orders')
            assert rows.total==2 and all('kind' not in row for row in rows.items)
            files=await info.list_files(scope,'/market/orders')
            assert all(row['kind']=='file' for row in files.items)
            raw=await info.read(scope,files.items[0]['path'])
            assert json.loads(raw.data)['id']==0
    finally:store.close()

@pytest.mark.asyncio
async def test_model_request_explains_container_and_original_file_access(tmp_path):
    store,_,provider,driver,session,_=llm_setup(tmp_path,[reply(text='done')])
    try:
        await driver.run(session)
        tools={t['function']['name']:t['function']['description'] for t in provider.requests[0][1]['tools']}
        assert 'directory' in tools['data_list'] and 'data_query' in tools['data_list']
        assert 'file' in tools['data_read'] and 'document_ref' in tools['data_read']
        assert 'directory' in tools['data_read'] and 'data_list' in tools['data_read']
        assert 'dataset' in tools['data_query'] and 'document_ref' in tools['data_query']
    finally:store.close()
