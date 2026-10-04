"""非作者核对目录提示不会扩张 Provider 接口或污染数据行。"""
import pytest
from society0.kernel.interaction import Information,InteractionScope,Moment,Ref,Page
from society0.kernel.information_sql import SQLInformation,DatasetSpec
from society0.kernel.information_fs import InformationFiles
from society0.kernel.storage import StageStore
from society0.kernel.observation import Observation


@pytest.mark.asyncio
async def test_review_minimal_provider_mounts_and_rows_retain_contract():
    class Provider:
        def ref(self,path):return Ref('demo',path,'')
        async def list(self,scope,path,**kwargs):return Page([{'kind':'business-kind','value':17}],1,None,None)
    hidden=set();info=Information(lambda scope,operation,ref:ref.kind not in hidden)
    info.mount('/one',Provider());info.mount('/two',Provider())
    scope=InteractionScope('a',Moment(1,'read'))
    page=await info.list(scope,'/',limit=1)
    assert page.total==2 and page.items[0]['kind']=='directory'
    assert (await info.list(scope,'/',limit=1,cursor=page.next_cursor)).items[0]['path']=='/two'
    assert (await info.list(scope,'/one')).items==[{'kind':'business-kind','value':17}]
    hidden.add('/two')
    with pytest.raises(ValueError,match='cursor'):
        await info.list(scope,'/',limit=1,cursor=page.next_cursor)
    assert (await info.list(scope,'/')).total==1


@pytest.mark.asyncio
async def test_review_sql_rows_vfs_and_observer_keep_real_types_and_authorization(tmp_path):
    with StageStore.create(tmp_path/'run',['CREATE TABLE items(id INTEGER PRIMARY KEY,kind TEXT NOT NULL)'],
        initialize=lambda w:w.execute("INSERT INTO items VALUES(1,'directory')")) as store:
        hidden=set()
        def factory(reader):
            info=Information(lambda scope,operation,ref:ref.kind not in hidden)
            info.mount('/data',SQLInformation('data',reader,{
                'items':DatasetSpec('items','id',('id','kind')),
                'private':DatasetSpec('items','id',('id','kind'))}))
            return info
        info=factory(store);scope=InteractionScope('a',Moment(1,'read'))
        page=await info.list(scope,'/data',limit=1)
        assert page.total==2 and page.items[0]['kind']=='directory'
        hidden.add('private')
        with pytest.raises(ValueError,match='cursor'):
            await info.list(scope,'/data',limit=1,cursor=page.next_cursor)
        assert (await info.list(scope,'/data')).total==1
        rows=await info.list(scope,'/data/items')
        assert rows.items[0]['kind']=='directory'
        fs=InformationFiles(info.bound(scope),scope)
        try:
            assert (await fs.callback('list','/data'))[0][1]=='directory'
            assert (await fs.callback('list','/data/items'))[0][1]=='file'
        finally:await fs.close()
        with Observation(store.path,information_factory=factory) as observer:
            observed=await observer.query(actor='a',moment={'time':1,'phase':'read'},path='/data/items')
            assert observed['total']==1 and observed['items'][0]['kind']=='directory'
