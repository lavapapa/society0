"""实际 Runtime 规则与确定性 LLMDriver 完整步骤，剥离外部服务等待。"""
import argparse
import asyncio
import json
import platform
import resource
import os
import subprocess
import time
from pathlib import Path
from society0.kernel.interaction import Actions,Action,ActionResult,Information,Ref
from society0.kernel.runtime import Runtime,Actor,DriverResult,Phase
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
from society0.kernel.results import Results,RESULTS_SCHEMA,StepResult


async def probe(path,*,mode,steps=20,actors=8):
    started=time.perf_counter();cpu=time.process_time();provider=None;provider_calls=0;sdk_seconds=0.;memory_seconds=0.;model_seconds=0.;environment_seconds=0.;host=None
    schema=THREAD_SCHEMA+RESULTS_SCHEMA+('CREATE TABLE balances(id TEXT PRIMARY KEY NOT NULL,value INTEGER NOT NULL)',)
    if mode=='memory':
        from society0.kernel.memory import MEMORY_SCHEMA
        schema+=MEMORY_SCHEMA
    def initialize(writer):
        for index in range(actors):writer.execute('INSERT INTO balances VALUES(?,0)',(f'actor-{index}',))
    store=StageStore.create(path,schema,initialize=initialize)
    threads=ThreadStore(store);results=Results(store);actions=Actions(lambda *a:True)
    transaction=store.transaction;transactions=0;transaction_seconds=0.
    def write(*a,**kw):
        nonlocal transactions,transaction_seconds
        before=time.perf_counter()
        try:return transaction(*a,**kw)
        finally:transactions+=1;transaction_seconds+=time.perf_counter()-before
    store.transaction=write
    def increment(scope,target,arguments):
        nonlocal environment_seconds
        before=time.perf_counter()
        store.transaction(lambda w:w.execute('UPDATE balances SET value=value+1 WHERE id=?',(scope.actor,)))
        environment_seconds+=time.perf_counter()-before
        return ActionResult('completed',{'changed':scope.actor,'amount':1})
    actions.register(Action('increment',('account','balance'),'Increase account by one.',{'type':'object'},increment,terminal=True))
    if mode in ('llm','memory'):
        from society0.kernel.models import ModelProvider
        from society0.kernel.llm import LLMDriver
        from openai.types.chat import ChatCompletion
        from tests.primary.provider_http import bind_chat
        endpoint={'id':'fake','model':'fake','api_key':'unused','base_url':'http://unused.invalid/v1','concurrency':actors,'trust_env':False}
        provider=ModelProvider([endpoint],threads,request_limit=asyncio.Semaphore(actors))
        async def sdk(**kwargs):
            nonlocal provider_calls,sdk_seconds
            before=time.perf_counter();provider_calls+=1
            context=next(json.loads(m['content']) for m in kwargs['messages'] if isinstance(m.get('content'),str) and m['content'].startswith('{') and '"actor"' in m['content'])
            actor=context['actor']
            extracting=any(t['function']['name']=='extract_memories' for t in kwargs.get('tools',[]))
            name='extract_memories' if extracting else 'action_invoke'
            arguments={'memories':[{'content':f"{actor} step {context['step']} increased account by 1",'importance':3}]} if extracting else {
                'name':'increment','target':{'namespace':'account','kind':'balance','key':actor},'arguments':{}}
            response=ChatCompletion(id='physical-'+str(provider_calls),created=0,model='fake',object='chat.completion',
                choices=[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':'完整响应'*256,
                    'tool_calls':[{'id':'increment','type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]}}])
            sdk_seconds+=time.perf_counter()-before;return response
        await bind_chat(provider,sdk)
        request=provider.request_model
        async def measured_request(*a,**kw):
            nonlocal model_seconds
            before=time.perf_counter()
            try:return await request(*a,**kw)
            finally:model_seconds+=time.perf_counter()-before
        provider.request_model=measured_request
        memory=None
        if mode=='memory':
            import chromadb
            from society0.kernel.plugins import Plugin,PluginHost
            from society0.kernel.services import memory_plugin
            from society0.kernel.memory import MemoryPolicy
            class Embed:
                async def embed(self,texts,*,metadata):return [[1.]+[0.]*1023 for text in texts]
            def resources(ctx):
                client=chromadb.PersistentClient(path=str(path/'vectors'));ctx.on_close(client.close)
                ctx.provide('client',client);ctx.provide('embeddings',{'fixed':Embed()});ctx.provide('models',{'fixed':provider})
            host=PluginHost([Plugin('storage',install=lambda ctx:ctx.provide('store',store)),
                Plugin('threads',install=lambda ctx:ctx.provide('threads',threads)),Plugin('resources',install=resources),
                memory_plugin(client=('resources','client'),embedding=('resources','embeddings','fixed'),
                    extraction=('resources','models','fixed'),policy=MemoryPolicy(active_tools=False),recall_query=lambda s:'earlier account changes')])
            await host.__aenter__();memory=host.service('memory','memory')
            def measure(method):
                async def call(*a,**kw):
                    nonlocal memory_seconds
                    before=time.perf_counter()
                    try:return await method(*a,**kw)
                    finally:memory_seconds+=time.perf_counter()-before
                return call
            memory.before_activation=measure(memory.before_activation);memory.after_activation=measure(memory.after_activation)
        driver=LLMDriver(provider,threads,memory=memory,input_builder=lambda session:[{'role':'system','content':'完整决策材料'*1024},
            {'role':'user','content':json.dumps({'actor':session.actor.id,'step':session.step,'task':'increment exactly once'})}])
    else:
        class Rule:
            async def run(self,session):
                await session.actions.invoke('increment',Ref('account','balance',session.actor.id),{})
                return DriverResult('completed')
        driver=Rule()
    runtime=Runtime([Actor(f'actor-{i}',driver) for i in range(actors)],information=Information(lambda *a:True),
                    actions=actions,store=store,results=results,capacity=actors)
    async def phase(ctx):
        for i in range(actors):ctx.activate(f'actor-{i}')
        return StepResult(metrics={'scheduled':actors})
    rows=[]
    try:
        for step in range(1,steps+1):
            tx=transactions;txs=transaction_seconds;sdk=sdk_seconds;mem=memory_seconds;model=model_seconds;env=environment_seconds
            await runtime.run_step(step,step,[Phase('decision',phase)])
            current_rss=int(subprocess.check_output(['ps','-o','rss=','-p',str(os.getpid())],text=True).strip())*1024
            rows.append({'rss_bytes':current_rss,**runtime.last_timing,'transactions':transactions-tx,'transaction_seconds':transaction_seconds-txs,'fake_sdk_seconds':sdk_seconds-sdk,'memory_seconds':memory_seconds-mem,'model_seconds':model_seconds-model,'environment_seconds':environment_seconds-env})
        balances=store.read(lambda r:r.query('SELECT id,value FROM balances ORDER BY id'))
        memory_snapshot=store.read(lambda r:(r.query('SELECT * FROM memory_rows ORDER BY id'),r.query('SELECT * FROM memory_vectors ORDER BY id'),list(r.iter_query('SELECT * FROM memory_chunks ORDER BY id,chunk')))) if mode=='memory' else None
        complete=store.complete_step
        memory_count=store.read(lambda r:r.query('SELECT count(*) FROM memory_rows'))[0][0] if mode=='memory' else 0
    finally:
        await runtime.close()
        if host is not None:await host.__aexit__(None,None,None)
        if provider is not None:await provider.close()
        store.close()
    with StageStore.restore(path,Path(str(path)+'-restored')) as restored:
        equal=restored.read(lambda r:r.query('SELECT id,value FROM balances ORDER BY id'))==balances
        if mode=='memory':
            equal=equal and restored.read(lambda r:(r.query('SELECT * FROM memory_rows ORDER BY id'),r.query('SELECT * FROM memory_vectors ORDER BY id'),list(r.iter_query('SELECT * FROM memory_chunks ORDER BY id,chunk'))))==memory_snapshot
    peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'platform':platform.platform(),'python':platform.python_version(),'mode':mode,'actors':actors,
            'steps':rows,'balances':balances,'complete_step':complete,'restored_equal':equal,'provider_calls':provider_calls,'memory_count':memory_count,
            'peak_rss_bytes':peak if platform.system()=='Darwin' else peak*1024,
            'wall_seconds':time.perf_counter()-started,'cpu_seconds':time.process_time()-cpu,
            'directory_bytes':sum(p.stat().st_size for p in Path(path).rglob('*') if p.is_file()),
            'limits':'same small account action; real SDK with offline MockTransport; transaction time overlaps phase and includes encoding+SQLite FULL commits; memory mode uses formal Memory plugin with fixed extraction/embedding and actual Chroma; shell absent; nested phase/model/memory timings overlap'}


if __name__=='__main__':
    import tempfile
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['rule','llm','memory'],required=True);args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='society0-step-cost-') as directory:
        print(json.dumps(asyncio.run(probe(Path(directory)/'run',mode=args.mode))))
