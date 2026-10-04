"""终审发现的权限组合与行动故障边界回归。"""
import asyncio
import json
import pytest
from society0.kernel.information_sql import SQLInformation,DatasetSpec
from society0.kernel.interaction import Information,InteractionScope,Moment,Actions,Action,ActionResult,Ref
from society0.kernel.runtime import Runtime,Actor,DriverResult,Phase
from society0.kernel.storage import StageStore

@pytest.mark.asyncio
async def test_directory_authorization_pages_bind_moment_run_and_access_revision(tmp_path):
    schema=['CREATE TABLE rows(id INTEGER PRIMARY KEY)','CREATE TABLE permissions(id INTEGER PRIMARY KEY)']
    with StageStore.create(tmp_path/'run',schema) as store:
        info=Information(lambda scope,op,ref:ref.kind!='hidden',access_dependencies=('permissions',))
        info.mount('/world',SQLInformation('world',store,{k:DatasetSpec('rows','id',('id',)) for k in ('a','hidden','b')}))
        with InteractionScope('alice',Moment(1,'read')) as scope:
            first=await info.list(scope,'/world',limit=1)
            assert first.total==2
            cursor=json.loads(json.dumps(first.next_cursor))
            second=await info.list_files(scope,'/world',limit=1,cursor=cursor)
            assert [x['path'] for x in second.items]==['/world/b'] and second.next_cursor is None
            store.transaction(lambda w:w.execute('INSERT INTO rows VALUES(1)'))
            assert (await info.list(scope,'/world',cursor=cursor)).total==2
            store.transaction(lambda w:w.execute('INSERT INTO permissions VALUES(1)'))
            with pytest.raises(ValueError):await info.list(scope,'/world',cursor=cursor)
        with InteractionScope('alice',Moment(2,'read')) as scope:
            with pytest.raises(ValueError):await info.list(scope,'/world',cursor=cursor)
        with StageStore.create(tmp_path/'other',schema) as other:
            another=Information(lambda scope,op,ref:ref.kind!='hidden',access_dependencies=('permissions',))
            another.mount('/world',SQLInformation('world',other,{k:DatasetSpec('rows','id',('id',)) for k in ('a','hidden','b')}))
            with InteractionScope('alice',Moment(1,'read')) as scope:
                with pytest.raises(ValueError):await another.list(scope,'/world',cursor=cursor)

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['sync','async','cancel','bad_return'])
async def test_caught_handler_fault_stops_further_actions_and_complete(tmp_path,mode):
    with StageStore.create(tmp_path/'run',['CREATE TABLE facts(id INTEGER PRIMARY KEY)']) as store:
        actions=Actions(lambda *args:True)
        def effect():
            store.transaction(lambda w:w.execute('INSERT INTO facts VALUES(1)'))
            if mode=='cancel':raise asyncio.CancelledError()
            if mode=='bad_return':return None
            raise ValueError('failed handler')
        async def async_effect(*args):
            await asyncio.sleep(0)
            return effect()
        actions.register(Action('act',('n','k'),'act',{'type':'object'},async_effect if mode in ('async','cancel') else lambda *args:effect()))
        class Driver:
            async def run(self,session):
                try:await session.actions.invoke('act',Ref('n','k','1'),{})
                except BaseException:pass
                with pytest.raises(BaseException):await session.actions.invoke('act',Ref('n','k','1'),{})
                return DriverResult('completed')
        runtime=Runtime([Actor('a',Driver())],information=Information(lambda *args:True),actions=actions,store=store)
        with pytest.raises(BaseException):await runtime.run_step(1,1,[Phase('run',lambda ctx:ctx.activate('a'))])
        assert store.complete_step==0
        with StageStore.restore(store.path,tmp_path/'restored') as restored:
            assert restored.read(lambda v:v.query('SELECT * FROM facts'))==[]

@pytest.mark.asyncio
async def test_expected_business_rejection_does_not_poison_step(tmp_path):
    with StageStore.create(tmp_path/'run',['CREATE TABLE facts(id INTEGER PRIMARY KEY)']) as store:
        actions=Actions(lambda *args:True)
        actions.register(Action('reject',('n','k'),'reject',{'type':'object'},lambda *args:ActionResult('rejected',{})))
        class Driver:
            async def run(self,session):
                assert (await session.actions.invoke('reject',Ref('n','k','1'),{})).status=='rejected'
                return DriverResult('completed')
        runtime=Runtime([Actor('a',Driver())],information=Information(lambda *args:True),actions=actions,store=store)
        await runtime.run_step(1,1,[Phase('run',lambda ctx:ctx.activate('a'))])
        assert store.complete_step==1

@pytest.mark.asyncio
async def test_shared_action_fault_invalidates_other_actor_scope_before_next_write():
    faults=[];actions=Actions(lambda *args:True);called=[]
    def fail(*args):raise ValueError('domain failed')
    actions.register(Action('fail',('n','k'),'fail',{'type':'object'},fail))
    actions.register(Action('write',('n','k'),'write',{'type':'object'},lambda *args:(called.append(1),ActionResult('completed',{}))[1]))
    with InteractionScope('a',Moment(1,'p'),_faults=faults) as a,InteractionScope('b',Moment(1,'p'),_faults=faults) as b:
        with pytest.raises(ValueError):await actions.invoke(a,'fail',Ref('n','k','1'),{})
        with pytest.raises(ValueError):await actions.invoke(b,'write',Ref('n','k','1'),{})
        assert called==[]
