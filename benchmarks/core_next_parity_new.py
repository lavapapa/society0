"""V03 新 Core 固定业务输入的实际运行与恢复消费者。"""
import argparse
import asyncio
import json
from pathlib import Path

NOTE='完整信息🙂：保留来源、时间与待核实条件。'*8
VECTOR=[1.0,0.5,-0.25,0.0]


async def run(args):
    import chromadb
    from society0.kernel.composition import compose
    from society0.kernel.plugins import Plugin
    from society0.kernel.services import thread_plugin
    from society0.kernel.interaction import interaction_plugin,Action,ActionResult,Ref
    from society0.kernel.runtime import Actor,DriverResult,Phase,runtime_plugin
    from society0.kernel.llm import LLMDriver,LLMPolicy
    from society0.kernel.memory import Memory,MEMORY_SCHEMA,MemoryPolicy
    prior=json.loads((args.source/'parity.json').read_text()) if args.source else None
    result={'initial':{'balances':{'a':100,'b':200},'totals':{'a':0,'b':0},'facts':[]},
        'snapshots':[],'decision_inputs':[],'memories':[],'threads':{},'provider_inputs':[],'restored_thread_prefix_equal':True}
    if prior:
        for field in ('snapshots','decision_inputs','provider_inputs'):result[field]=prior[field]
    held={}
    def install(ctx):
        store=ctx.require('storage','store');threads=ctx.require('threads','threads')
        held.update(store=store,threads=threads)
        async def embed(texts,**kw):return [list(VECTOR) for _ in texts]
        client=chromadb.EphemeralClient();ctx.on_close(client.close)
        memory=Memory(store,threads,embed=embed,client=client,policy=MemoryPolicy(False,False,False))
        ctx.on_close(memory.close);held['memory']=memory
        def state():
            def read(v):
                accounts=v.query('SELECT actor,balance,total FROM accounts ORDER BY actor')
                facts=[{'actor':a,'step':s,'amount':n,'note':note} for a,s,n,note in v.iter_query('SELECT actor,step,amount,note FROM facts ORDER BY ordinal')]
                return {'balances':{a:b for a,b,t in accounts},'totals':{a:t for a,b,t in accounts},'facts':facts}
            return store.read(read)
        held['state']=state
        def settle(scope,target,value):
            amount=value['amount'];actor=scope.actor
            def write(w):
                w.execute('UPDATE accounts SET balance=balance-?,total=total+? WHERE actor=?',(amount,amount,actor))
                w.execute('INSERT INTO facts(actor,step,amount,note) VALUES(?,?,?,?)',(actor,value['step'],amount,value['note']))
            store.transaction(write)
            return ActionResult('completed',{'actor':actor,**value})
        action=Action('ledger.settle',('ledger','accounts'),'支付并完整保存本次事实',
            {'type':'object','properties':{'step':{'type':'integer'},'amount':{'type':'integer'},'note':{'type':'string'}},
             'required':['step','amount','note'],'additionalProperties':False},settle,terminal=True)
        ctx.require('interaction','actions').register(action)
        async def material(session):
            recalled=[]
            if args.mode=='llm':
                hits=await memory.recall(session.actor.id,NOTE,top_k=100,current_step=session.step)
                recalled=[item['content'] for item in hits]
            return {'actor':session.actor.id,'step':session.step,'state_before':state(),'note':NOTE,'recalled':recalled}
        class Provider:
            async def request(self,tid,options):
                messages=threads.read_messages(tid)
                text=next(m['content'] for m in reversed(messages) if m['role']=='user' and m['content'].startswith('PARITY:'))
                value=json.loads(text.removeprefix('PARITY:'))
                assert value['state_before']==state() and value['note']==NOTE
                result['decision_inputs'].append(value);result['provider_inputs'].append(messages)
                threads.record_request(tid,provider_options=options,physical_request_id=f"{value['actor']}:{value['step']}")
                arguments={'step':value['step'],'amount':value['step']*(1 if value['actor']=='a' else 2),'note':NOTE}
                return {'role':'assistant','content':'依据全部原始资料执行既定支付。','finish_reason':'tool_calls','tool_calls':[{
                    'id':f"{value['actor']}:{value['step']}",'type':'function','function':{'name':'action_invoke','arguments':json.dumps({
                        'name':'ledger.settle','target':{'namespace':'ledger','kind':'accounts','key':value['actor']},'arguments':json.dumps(arguments,ensure_ascii=False)},ensure_ascii=False)}}]}
        async def inputs(session):return [{'role':'system','content':'需保留全部资料的主体。'},{'role':'user','content':'PARITY:'+json.dumps(await material(session),ensure_ascii=False)}]
        llm=LLMDriver(Provider(),threads,input_builder=inputs,policy=LLMPolicy(max_turns=2,max_action_calls=1))
        class Driver:
            async def run(self,session):
                if args.mode=='llm':outcome=await llm.run(session)
                else:
                    value=await material(session);result['decision_inputs'].append(value)
                    await session.actions.invoke('ledger.settle',Ref('ledger','accounts',session.actor.id),
                        {'step':session.step,'amount':session.step*(1 if session.actor.id=='a' else 2),'note':NOTE})
                    outcome=DriverResult('completed')
                if outcome.status!='completed':raise AssertionError(outcome)
                if args.mode=='llm':
                    amount=session.step*(1 if session.actor.id=='a' else 2)
                    content=f'主体{session.actor.id}完成第{session.step}步，支付{amount}。'+NOTE
                    await memory.seed(session.actor.id,f'{session.actor.id}:{session.step}',timestamp=session.step,
                        entries=[{'content':content,'type':'episodic','importance':3}],visible_step=session.step)
                return outcome
        ctx.provide('actors',{a:Actor(a,Driver()) for a in ('a','b')})
    def initialize(w):w.executemany('INSERT INTO accounts VALUES(?,?,0)',[('a',100),('b',200)])
    plugins=[thread_plugin(),interaction_plugin(lambda *a:True),
        Plugin('ledger',('storage','threads','interaction'),install,schema=(
            'CREATE TABLE accounts(actor TEXT PRIMARY KEY NOT NULL,balance INTEGER NOT NULL,total INTEGER NOT NULL)',
            'CREATE TABLE facts(ordinal INTEGER PRIMARY KEY,actor TEXT NOT NULL,step INTEGER NOT NULL,amount INTEGER NOT NULL,note TEXT NOT NULL)',*MEMORY_SCHEMA),initialize=initialize),
        runtime_plugin(actor_service=('ledger','actors'),information=('interaction','information'),actions=('interaction','actions'),store=('storage','store'))]
    async def phase(ctx):
        for actor in ('a','b'):ctx.activate(actor)
        await ctx.drain()
    async with compose(args.output,plugins,source=args.source) as host:
        store=held['store'];threads=held['threads']
        if prior:
            assert held['state']()==prior['snapshots'][-1]['state']
            for tid,messages in prior['threads'].items():assert threads.read_messages(tid)==messages
        for _ in range(args.steps):
            step=store.complete_step+1
            await host.service('runtime','runtime').run_step(step,step,[Phase('decide',phase)])
            result['snapshots'].append({'step':step,'state':held['state']()})
        for actor in ('a','b'):
            entries=[];held['memory'].export(actor,entries.append)
            result['memories'].extend({'actor':actor,'step':item['timestamp'],'content':item['content'],'type':item['type'],
                'importance':item['importance'],'embedding':item['embedding']} for item in entries)
        result['memories'].sort(key=lambda item:(item['actor'],item['step']))
        tids=store.read(lambda v:[row[0] for row in v.iter_query('SELECT id FROM thread_heads ORDER BY ordinal')])
        result['threads']={tid:threads.read_messages(tid) for tid in tids}
    (args.output/'parity.json').write_text(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--source',type=Path)
    parser.add_argument('--mode',choices=['rule','llm'],required=True);parser.add_argument('--steps',type=int,default=3)
    asyncio.run(run(parser.parse_args()))
