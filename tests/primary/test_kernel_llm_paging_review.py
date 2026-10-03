"""真实 Driver 自身留证应与信息、权限的分页版本分离。"""
import json
from types import SimpleNamespace

import pytest

from society0.kernel.interaction import Action, ActionResult, InteractionScope, Moment, interaction_plugin
from society0.kernel.information_sql import DatasetSpec, SQLInformation
from society0.kernel.plugins import Plugin, PluginHost
from society0.kernel.runtime import Actor, Session
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
from society0.kernel.llm import LLMDriver


@pytest.mark.asyncio
@pytest.mark.parametrize('tool',['data_query','action_find'])
@pytest.mark.parametrize('changed',[None,'items','access'])
async def test_review_llm_thread_writes_do_not_expire_unchanged_domain_pages(tmp_path,tool,changed):
    with StageStore.create(tmp_path/'run', [*THREAD_SCHEMA,
            'CREATE TABLE items(id INTEGER PRIMARY KEY,value TEXT NOT NULL)',
            'CREATE TABLE access(id INTEGER PRIMARY KEY,n INTEGER NOT NULL)'],
            initialize=lambda w:(w.executemany('INSERT INTO items VALUES(?,?)',[(1,'one'),(2,'two')]),w.execute('INSERT INTO access VALUES(1,0)'))) as store:
        threads=ThreadStore(store)
        received=[]
        class Provider:
            async def request(self,tid,options):
                messages=threads.read_messages(tid)
                tools=[json.loads(message['content']) for message in messages if message['role']=='tool']
                threads.record_request(tid,provider_options=options,physical_request_id=str(len(tools)))
                if tools:
                    received.append(tools[-1])
                if len(tools)==1 and changed:
                    store.transaction(lambda w:w.execute("UPDATE items SET value='changed' WHERE id=1" if changed=='items' else 'UPDATE access SET n=n+1 WHERE id=1'))
                if len(tools)==2:
                    response={'role':'assistant','content':'done','tool_calls':[],'finish_reason':'stop'}
                else:
                    cursor=None if not tools else tools[-1]['next_cursor']
                    arguments=({'path':'/domain/items','query':json.dumps({'limit':1,'cursor':cursor})}
                        if tool=='data_query' else {'target':{'namespace':'domain','kind':'item','key':'1'},'query':'','limit':1,'cursor':json.dumps(cursor) if cursor is not None else None})
                    response={'role':'assistant','content':'','tool_calls':[{'id':str(len(tools)), 'type':'function',
                        'function':{'name':tool,'arguments':json.dumps(arguments)}}],'finish_reason':'tool_calls'}
                threads.event(tid,'provider_response',response)
                return response
        async with PluginHost([Plugin('storage',install=lambda c:c.provide('store',store)),interaction_plugin(lambda *a:True,access_dependencies=('access',))]) as host:
            information=host.service('interaction','information'); actions=host.service('interaction','actions')
            information.mount('/domain',SQLInformation('domain',store,{'items':DatasetSpec('items','id',('id','value'))}))
            for name in ('first','second'):
                actions.register(Action(name,('domain','item'),name,{'type':'object'},lambda *a:ActionResult('completed')),dependencies=('items',))
            driver=LLMDriver(Provider(),threads,input_builder=lambda s:[{'role':'system','content':'Read both complete pages.'}])
            scope=InteractionScope('actor',Moment(1,'read'))
            session=Session(Actor('actor',driver),scope,information.bound(scope),actions.bound(scope),{},None,(),SimpleNamespace(prepare_artifact=store.prepare_artifact), step=1)
            result=await driver.run(session)
            assert result.status=='completed'
            assert len(received)==2
            if changed:
                assert 'error' in received[1]
                return
            assert all('error' not in page for page in received),received
            assert [page['total'] for page in received]==[2,2]
            assert received[1]['next_cursor'] is None
            assert received[0]['items']!=received[1]['items']
