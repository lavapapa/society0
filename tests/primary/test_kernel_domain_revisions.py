"""业务查询版本隔离 Thread 留证，同时追踪数据与访问资格。"""
import json
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.plugins import Plugin,PluginHost
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
from society0.kernel.interaction import Action,ActionResult,InteractionScope,Moment,Ref,Query,interaction_plugin
from society0.kernel.information_sql import SQLInformation,DatasetSpec


SCHEMA=(*THREAD_SCHEMA,'CREATE TABLE items(id INTEGER PRIMARY KEY,body TEXT NOT NULL)',
        'CREATE TABLE rights(actor TEXT PRIMARY KEY NOT NULL,mode INTEGER NOT NULL)')


def initialize(w):
    w.executemany('INSERT INTO items VALUES(?,?)',[(1,'one'),(2,'two'),(3,'three')])
    w.execute('INSERT INTO rights VALUES(?,?)',('a',1))


def services(store):
    return [Plugin('storage',install=lambda ctx:ctx.provide('store',store)),
            interaction_plugin(lambda *args:True,access_dependencies=('rights',))]


def bind(host,store):
    info=host.service('interaction','information');actions=host.service('interaction','actions')
    info.mount('/domain',SQLInformation('domain',store,{'items':DatasetSpec('items','id',('id','body'))}))
    for name in ('first','second'):
        actions.register(Action(name,('domain','items'),'description',{'type':'object'},lambda *args:ActionResult('completed')),dependencies=('items',))
    return info,actions


@pytest.mark.asyncio
@pytest.mark.parametrize('changed',['items','rights'])
async def test_declared_business_and_access_changes_expire_both_page_types(tmp_path,changed):
    with StageStore.create(tmp_path/'run',SCHEMA,initialize=initialize) as store:
        async with PluginHost(services(store)) as host:
            info,actions=bind(host,store)
            scope=InteractionScope('a',Moment(1,'read'));target=Ref('domain','items','1')
            page=await info.query(scope,'/domain/items',Query(limit=1))
            action_page=await actions.find(scope,target,limit=1)
            if changed=='items':store.transaction(lambda w:w.execute("UPDATE items SET body='new' WHERE id=1"))
            else:store.transaction(lambda w:w.execute("UPDATE rights SET mode=2 WHERE actor='a'"))
            with pytest.raises(ValueError,match='cursor'):
                await info.query(scope,'/domain/items',Query(limit=1,cursor=page.next_cursor))
            with pytest.raises(ValueError,match='cursor'):
                await actions.find(scope,target,limit=1,cursor=action_page.next_cursor)


@pytest.mark.asyncio
async def test_rollback_preserves_pages_and_explicit_global_scope_remains_global(tmp_path):
    with StageStore.create(tmp_path/'run',SCHEMA,initialize=initialize) as store:
        async with PluginHost(services(store)) as host:
            info,actions=bind(host,store)
            scope=InteractionScope('a',Moment(1,'read'));target=Ref('domain','items','1')
            page=await info.query(scope,'/domain/items',Query(limit=1))
            action_page=await actions.find(scope,target,limit=1)
            def failed(w):
                w.execute("UPDATE rights SET mode=2 WHERE actor='a'")
                w.execute("UPDATE items SET body='invalid' WHERE id=1")
                raise RuntimeError('rollback')
            with pytest.raises(RuntimeError):store.transaction(failed)
            cursor=json.loads(json.dumps(page.next_cursor))
            assert (await info.query(scope,'/domain/items',Query(limit=1,cursor=cursor))).items[0]['id']==2
            assert (await actions.find(scope,target,limit=1,cursor=json.loads(json.dumps(action_page.next_cursor)))).items[0].name=='second'
            pinned=InteractionScope('a',Moment(1,'read'),revision=store.read(lambda r:r.live_revision))
            ThreadStore(store).open('a',1,'decision')
            from society0.kernel.storage import StorageError
            with pytest.raises(StorageError):await info.query(pinned,'/domain/items',Query())


@pytest.mark.asyncio
async def test_dependency_page_identity_cannot_cross_runs_with_equal_table_versions(tmp_path):
    saved=None
    for name in ('first','second'):
        with StageStore.create(tmp_path/name,SCHEMA,initialize=initialize) as store:
            async with PluginHost(services(store)) as host:
                info,actions=bind(host,store)
                scope=InteractionScope('a',Moment(1,'read'));target=Ref('domain','items','1')
                if saved is None:
                    saved=((await info.query(scope,'/domain/items',Query(limit=1))).next_cursor,
                           (await actions.find(scope,target,limit=1)).next_cursor)
                else:
                    with pytest.raises(ValueError,match='cursor'):await info.query(scope,'/domain/items',Query(limit=1,cursor=saved[0]))
                    with pytest.raises(ValueError,match='cursor'):await actions.find(scope,target,limit=1,cursor=saved[1])
