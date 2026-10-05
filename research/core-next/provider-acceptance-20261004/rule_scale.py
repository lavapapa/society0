"""独立进程实际规则内核与完整恢复，不调用提供方。"""
import argparse
import asyncio
import json
import platform
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def rss():
    value=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform=='darwin' else value*1024


def disk(path):
    return {str(p.relative_to(path)):p.stat().st_size for p in path.rglob('*') if p.is_file()}


def snapshot(store):
    return store.read(lambda v:{'balances':[list(row) for row in v.iter_query('SELECT id,value FROM balances ORDER BY id')],
        'trade_count':v.query('SELECT COUNT(*) FROM trades')[0][0],
        'thread_count':v.query('SELECT COUNT(*) FROM thread_heads')[0][0]})


def run_case(path,*,history,actors,steps):
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
    from society0.kernel.results import RESULTS_SCHEMA,Results,StepResult
    from society0.kernel.interaction import Actions,Action,ActionResult,Information,Ref
    from society0.kernel.runtime import Runtime,Actor,DriverResult,Phase
    schema=THREAD_SCHEMA+RESULTS_SCHEMA+(
        'CREATE TABLE balances(id TEXT PRIMARY KEY NOT NULL,value INTEGER NOT NULL)',
        'CREATE TABLE trades(id INTEGER PRIMARY KEY NOT NULL,actor TEXT NOT NULL,amount INTEGER NOT NULL)',)
    def initialize(w):
        for i in range(actors):w.execute('INSERT INTO balances VALUES(?,0)',(f'a-{i}',))
        for i in range(history):w.execute('INSERT INTO trades VALUES(?,?,1)',(i,'historical'))
    started=time.perf_counter();cpu=time.process_time()
    store=StageStore.create(path,schema,initialize=initialize)
    initialized=time.perf_counter()-started;initial_peak=rss()
    threads=ThreadStore(store);actions=Actions(lambda *args:True);rows=[]
    def increment(scope,target,args):
        def write(w):
            old=w.query('SELECT value FROM balances WHERE id=?',(scope.actor,))[0][0]
            w.execute('INSERT INTO trades(actor,amount) VALUES(?,1)',(scope.actor,))
            w.execute('UPDATE balances SET value=? WHERE id=?',(old+1,scope.actor))
        store.transaction(write)
        return ActionResult('completed',{'amount':1})
    actions.register(Action('increment',('account','balance'),'Increment once',{'type':'object'},increment,terminal=True))
    class Rule:
        async def run(self,session):
            tid=threads.open(session.actor.id,{'time':session.step,'phase':'decision'},kind='decision')
            threads.append_message(tid,{'role':'user','content':'Increase the account by one.'})
            result=await session.actions.invoke('increment',Ref('account','balance',session.actor.id),{})
            threads.append_message(tid,{'role':'assistant','content':json.dumps(result.value)})
            threads.close(tid,'completed')
            return DriverResult('completed')
    runtime=Runtime([Actor(f'a-{i}',Rule()) for i in range(actors)],store=store,
        information=Information(lambda *args:True),actions=actions,results=Results(store),capacity=actors)
    async def phase(ctx):
        for i in range(actors):ctx.activate(f'a-{i}')
        return StepResult(metrics={'active':actors})
    async def execute():
        try:
            for step in range(1,steps+1):
                begin=time.perf_counter();c=time.process_time()
                await runtime.run_step(step,step,[Phase('decision',phase)])
                files=disk(path)
                rows.append({'step':step,'wall_s':time.perf_counter()-begin,'cpu_s':time.process_time()-c,
                    'timing':runtime.last_timing,'peak_rss_bytes':rss(),
                    'changeset_bytes':sum(size for name,size in files.items() if name.startswith('changesets/')),
                    'directory_bytes':sum(files.values())})
        finally:await runtime.close()
    try:
        asyncio.run(execute());result=snapshot(store);result['complete_step']=store.complete_step
    finally:
        actions.close();store.close()
    return {**result,'history':history,'actors':actors,'steps':rows,'initialize_s':initialized,
        'initialize_peak_rss_bytes':initial_peak,'peak_rss_bytes':rss(),'cpu_s':time.process_time()-cpu,
        'wall_s':time.perf_counter()-started,'files':disk(path),'provider_calls':0,
        'python':sys.version,'platform':platform.platform()}


def restore_case(path):
    from society0.kernel.storage import StageStore
    start=time.perf_counter();cpu=time.process_time()
    with StageStore.restore(path,path.with_name(path.name+'-restored')) as store:
        result=snapshot(store);result['complete_step']=store.complete_step
    return {**result,'restore_s':time.perf_counter()-start,'cpu_s':time.process_time()-cpu,
        'peak_rss_bytes':rss(),'files':disk(path.with_name(path.name+'-restored'))}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--child',choices=['run','restore']);p.add_argument('--path',type=Path)
    p.add_argument('--history',type=int,default=1000);p.add_argument('--actors',type=int,default=8)
    p.add_argument('--steps',type=int,default=5);p.add_argument('--output',type=Path);a=p.parse_args()
    if a.child:
        result=restore_case(a.path) if a.child=='restore' else run_case(a.path,history=a.history,actors=a.actors,steps=a.steps)
        print(json.dumps(result));sys.exit()
    if a.output is None:p.error('--output required')
    if a.output.exists():p.error('output already exists')
    results=[]
    with tempfile.TemporaryDirectory(prefix='society0-rule-scale-') as temp:
        for history,actors in ((1000,8),(10000,8),(100000,8),(10000,32),(10000,128)):
            path=Path(temp)/f'h{history}-a{actors}'
            base=[sys.executable,str(Path(__file__).resolve()),'--path',str(path)]
            run=json.loads(subprocess.check_output(base+['--child','run','--history',str(history),'--actors',str(actors),'--steps',str(a.steps)],text=True))
            restored=json.loads(subprocess.check_output(base+['--child','restore'],text=True))
            assert all(restored[k]==run[k] for k in ('balances','trade_count','thread_count','complete_step'))
            results.append({'run':run,'restore':restored})
    a.output.write_text(json.dumps(results,indent=2))
