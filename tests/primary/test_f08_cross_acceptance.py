"""跨作者验收第三驱动、作用域、全时间线恢复和已物化私有结果。"""
from contextlib import asynccontextmanager
from types import SimpleNamespace
import json
import pytest
from society0.kernel.activation import ActivationContext,activation_scope
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.plugins import Plugin
from society0.kernel.interaction import interaction_plugin,InteractionScope,Moment,Information
from society0.kernel.runtime import Phase,DriverResult,runtime_plugin
from society0.kernel.schedule import StepPlan
from society0.kernel.runner import RunPlan,RunContract,run_plan
from society0.kernel.storage import StageReader,StageStore

@pytest.mark.asyncio
async def test_third_driver_plugin_runtime_scope_and_full_timeline_recovery(tmp_path):
    scopes=[];cleanup=[];timeline=('2026-01-01','2026-01-03','2026-01-09')
    def build():
        def install(ctx):
            store=ctx.require('storage','store')
            @asynccontextmanager
            async def notes(context):
                context.messages.append({'role':'user','content':'第三认知扩展的完整材料'})
                try:yield
                finally:cleanup.append((context.session.moment.time,context.result.status))
            class Third:
                async def run(self,session):
                    scopes.append(session.scope)
                    context=ActivationContext(session)
                    async with activation_scope(context,(notes,)):
                        assert context.messages[0]['content']=='第三认知扩展的完整材料'
                        store.transaction(lambda w:w.execute('INSERT INTO actual VALUES(?,?)',(session.step,session.moment.time)))
                        context.experience={'time':session.moment.time,'actual':True}
                        context.result=DriverResult('completed')
                    assert 'activation' not in session.cursors
                    return context.result
            ctx.provide('factory',lambda record:Third())
        class IndependentSchedule:
            async def next_step(self,complete):
                if complete>=len(timeline):return None
                async def phase(ctx):ctx.activate('third');await ctx.drain()
                return StepPlan(timeline[complete],(Phase('decision',phase),))
        return RunPlan([Plugin('third',('storage',),install,schema=('CREATE TABLE actual(step INTEGER PRIMARY KEY,time TEXT NOT NULL)',)),
            actor_plugin({'third':('third','factory')},records=[ActorRecord('third','third')]),interaction_plugin(lambda *args:True),
            runtime_plugin(actor_service=('actors','actors'),information=('interaction','information'),actions=('interaction','actions'),store=('storage','store')),
            Plugin('calendar',install=lambda ctx:ctx.provide('schedule',IndependentSchedule()))],
            RunContract({}, {}, {'driver':'third'}, {'timeline':list(timeline)},{}),schedule=('calendar','schedule'))
    assert (await run_plan(tmp_path/'run',build()))['complete_step']==3
    assert (await run_plan(tmp_path/'restored',build(),source=tmp_path/'run',step=1))['complete_step']==3
    with StageReader(tmp_path/'restored') as reader:
        assert reader.read(lambda v:v.query('SELECT step,time FROM actual ORDER BY step'))==[(1,timeline[0]),(2,timeline[1]),(3,timeline[2])]
    assert cleanup==[(time,'completed') for time in (*timeline,*timeline[1:])]
    for scope in scopes:
        with pytest.raises(Exception):scope.check_active()

@pytest.mark.asyncio
async def test_extension_partial_entry_failure_cleans_earlier_context():
    seen=[];session=SimpleNamespace(cursors={'activation':'previous'})
    @asynccontextmanager
    async def first(context):
        try:seen.append('enter');yield
        finally:seen.append('exit')
    @asynccontextmanager
    async def broken(context):
        raise OSError('extension preparation failed')
        yield
    with pytest.raises(OSError):
        async with activation_scope(ActivationContext(session),(first,broken)):raise AssertionError('unreachable')
    assert seen==['enter','exit'] and session.cursors['activation']=='previous'

@pytest.mark.asyncio
async def test_materialized_grep_survives_source_revocation_restore_and_actor_isolation(tmp_path):
    from society0.kernel.actor_files import ActorFiles
    from society0.kernel.information_sql import SQLInformation,DocumentSpec
    from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
    body='原文目标🙂\n'*40
    schema=(*THREAD_SCHEMA,'CREATE TABLE docs(id INTEGER PRIMARY KEY,body TEXT NOT NULL)','CREATE TABLE access(actor TEXT PRIMARY KEY NOT NULL,allowed INTEGER NOT NULL)')
    with StageStore.create(tmp_path/'run',schema,initialize=lambda w:(w.execute('INSERT INTO docs VALUES(1,?)',(body,)),w.execute('INSERT INTO access VALUES(?,1)',('a',)))) as store:
        information=Information(lambda *args:True)
        information.mount('/docs',SQLInformation('docs',store,{'text':DocumentSpec('docs','id','body',authorize=lambda scope:('EXISTS(SELECT 1 FROM access WHERE actor=? AND allowed=1)',(scope.actor,)),dependencies=('access',))}))
        threads=ThreadStore(store);scope=InteractionScope('a',Moment(1,'read'));tid=threads.open('a',scope.moment,'decision')
        session=SimpleNamespace(actor=SimpleNamespace(id='a'),scope=scope,moment=scope.moment,step=1,information=information.bound(scope),cursors={'thread_id':tid},prepare_artifact=store.prepare_artifact)
        files=ActorFiles(session,threads);result=await files.grep('目标','/world/docs/text')
        assert result['total']==40 and len(result['items'])<result['total']
        store.transaction(lambda w:w.execute('UPDATE access SET allowed=0'))
        with pytest.raises(Exception):await files.read('/world/docs/text/1')
        captured=(await files.read(result['result_path'],size=100000))['data']
        assert len(captured.splitlines())==40
        assert all(json.loads(line)['line']=='原文目标🙂\n' for line in captured.splitlines())
        threads.close(tid,'completed');store.complete(1);await files.close()
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        threads=ThreadStore(store)
        def current(actor):
            scope=InteractionScope(actor,Moment(2,'read'))
            return SimpleNamespace(actor=SimpleNamespace(id=actor),scope=scope,moment=scope.moment,step=2,information=Information(lambda *args:True).bound(scope),cursors={},prepare_artifact=store.prepare_artifact)
        assert (await ActorFiles(current('a'),threads).read(result['result_path'],size=100000))['data']==captured
        with pytest.raises(KeyError):await ActorFiles(current('b'),threads).read(result['result_path'])

@pytest.mark.asyncio
async def test_find_one_page_does_not_reread_full_chunk_per_short_row(tmp_path):
    from society0.kernel.actor_files import ActorFiles
    from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
    from society0.kernel.interaction import Page,ResourceStat,Ref,DocumentChunk
    class Catalog:
        def ref(self,path):return Ref('catalog','files',path)
        def metadata(self,scope,path):
            from society0.kernel.interaction import Unavailable
            raise Unavailable('not a dataset')
        def search_revision(self,scope,path):return 'catalog-v1'
        def stat(self,scope,path):return ResourceStat('directory' if path=='/catalog' else 'file',None if path=='/catalog' else 1,'catalog-v1',self.ref(path))
        def list_files(self,scope,path,*,limit=100,cursor=None):
            start=0 if cursor is None else cursor['offset'];end=min(1000,start+limit)
            return Page([{'path':f'/catalog/{index:04d}.txt','kind':'file'} for index in range(start,end)],1000,{'offset':end} if end<1000 else None,'catalog-v1')
        def read(self,scope,path,**args):return DocumentChunk(b'x',1,None,'catalog-v1',self.ref(path))
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        information=Information(lambda *args:True);information.mount('/catalog',Catalog())
        scope=InteractionScope('a',Moment(1,'find'));threads=ThreadStore(store);tid=threads.open('a',scope.moment,'decision')
        session=SimpleNamespace(actor=SimpleNamespace(id='a'),scope=scope,moment=scope.moment,step=1,information=information.bound(scope),cursors={'thread_id':tid},prepare_artifact=store.prepare_artifact)
        files=ActorFiles(session,threads);original=files._raw;read_bytes=0
        async def counted(path,**args):
            nonlocal read_bytes
            value=await original(path,**args)
            if path.startswith('/results/'):read_bytes+=len(value[0])
            return value
        files._raw=counted
        page=await files.find('*.txt','/world/catalog',limit=100)
        assert page.total==1000 and len(page.items)==100 and page.next_cursor is not None
        assert read_bytes<=131072,f'one short-row page reread {read_bytes} source bytes'

@pytest.mark.asyncio
async def test_result_reference_is_unambiguous_across_actor_activations(tmp_path):
    from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        first=threads.open('a',Moment(1,'decision'),'decision');second=threads.open('a',Moment(2,'decision'),'decision')
        one=store.prepare_artifact((b'first result',));two=store.prepare_artifact((b'different result',))
        threads.register_artifact(first,'same-result',one,actor='a')
        with pytest.raises(ValueError,match='reference'):
            threads.register_artifact(second,'same-result',two,actor='a')
        assert threads.read_actor_artifact('same-result',actor='a')['data']==b'first result'

@pytest.mark.asyncio
async def test_workspace_save_failure_publishes_no_success_memory_checkpoint(tmp_path):
    from society0.kernel.runtime import Actor,Runtime
    from society0.kernel.interaction import Actions
    from society0.kernel.llm import LLMDriver
    from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
    from society0.kernel.memory import Memory,MemoryPolicy,MemoryExtension,MEMORY_SCHEMA
    from tests.primary.test_kernel_llm import FakeProvider,reply
    from tests.primary.test_kernel_memory import Embed,Client
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,*MEMORY_SCHEMA)) as store:
        threads=ThreadStore(store);store.complete(1);closed=[]
        async def extract(*args,**kwargs):return [{'content':'未完成步骤经历','importance':4}]
        memory=Memory(store,threads,embed=Embed(),client=Client(),extract=extract,policy=MemoryPolicy(False,True,False))
        async def close():closed.append(True)
        def save():raise OSError('workspace publication failed')
        shell=SimpleNamespace(has_workspace=True,bind_files=lambda files:None,aclose=close,save_workspace=save)
        driver=LLMDriver(FakeProvider(threads,[reply(text='decision complete')]),threads,
            input_builder=lambda session:[{'role':'system','content':'完整系统背景'}],extensions=(MemoryExtension(memory),),shell_factory=lambda *args:shell)
        runtime=Runtime([Actor('a',driver)],information=Information(lambda *args:True),actions=Actions(lambda *args:True),store=store)
        async def activate(ctx):ctx.activate('a');await ctx.drain()
        with pytest.raises(OSError,match='workspace publication failed'):
            await runtime.run_step(2,2,(Phase('decision',activate),))
        assert store.complete_step==1 and closed==[True]
        with StageReader(tmp_path/'run') as diagnostics:
            observed=ThreadStore(diagnostics);tid=observed.find('a',Moment(2,'decision'));assert observed.describe(tid)['status']=='incomplete'
        await runtime.close();await memory.close()
    with StageStore.restore(tmp_path/'run',tmp_path/'restored',step=1) as store:
        assert store.read(lambda view:view.query('SELECT count(*) FROM memory_rows')[0][0])==0
        assert store.read(lambda view:view.query('SELECT count(*) FROM memory_jobs')[0][0])==0
        assert ThreadStore(store).find('a',Moment(2,'decision')) is None
