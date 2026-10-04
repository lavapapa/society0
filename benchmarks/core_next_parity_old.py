"""固定旧源码的实际 World/action_loop/Memory/检查点路径；只在隔离子进程运行。"""
import argparse
import asyncio
import copy
import json
from pathlib import Path
import sys

NOTE='完整信息🙂：保留来源、时间与待核实条件。'*8
VECTOR=[1.,.5,-.25,0.]

async def run(args):
    sys.path.insert(0,str(Path(args.old_source)/'src'))
    from society0.core_data import World
    from society0.persistence import PersistenceManager
    from society0.incremental_checkpoint import PersistenceSchema
    from society0.state_persistence import persistent_state_schema,replaceable_map,append_only_list
    from society0.agent.agent_loop import ActionSet,execute_action_loop
    from society0.agent.memory import Memory
    from society0.agent.thread_store import AgentThreadStore
    root=Path(args.run);root.mkdir(parents=True)
    manager=PersistenceManager(str(root))
    schema=PersistenceSchema.compile(persistent_state_schema(balances=replaceable_map(),totals=replaceable_map(),facts=append_only_list()),root_path=('environment','state'))
    prior=None;prefix_equal=True
    if args.source:
        source=Path(args.source)
        prior=json.loads((source/'parity.json').read_text())
        if args.mode=='llm':manager.seed_chroma_store_from(source)
        world,_=await manager.load_checkpoint_from(source)
        assert copy.deepcopy(world.environment_data['state'])==prior['snapshots'][-1]['state']
        old_threads=AgentThreadStore(source,create=False)
        for row in prior['threads']:
            assert old_threads.read_messages(row['thread_id'])==row['messages']
        prefix_equal=True
    else:
        world=World(step=0,event_log_path=str(root/'events.jsonl'))
        world.environment_data['type']='plain'
        world.environment_data['state']={'balances':{'a':100,'b':200},'totals':{'a':0,'b':0},'facts':[]}
    manager.configure_v4(world,schema)
    if args.source:
        world.begin_persistence_tick(0)
        manager.set_checkpoint_base(args.source,world.step,delta=world.seal_persistence_tick)
    root_marker=await manager.publish_root(world,object())
    if prior and prior['threads']:
        from society0.observation import ObservationReader
        observer=ObservationReader(root)
        try:
            observer.sync()
            for row in prior['threads']:
                events=observer.thread_page(row['thread_id'],checkpoint_id=root_marker['checkpoint_id'])['events']
                restored_messages=[event['payload']['message'] for event in events if event['event_type']=='conversation_message']
                assert restored_messages==row['messages']
        finally:observer.close()
    state=lambda:copy.deepcopy(world.environment_data['state'])
    initial=copy.deepcopy(prior['initial']) if prior else state()
    snapshots=copy.deepcopy(prior['snapshots']) if prior else []
    inputs=copy.deepcopy(prior['decision_inputs']) if prior else []
    traces=copy.deepcopy(prior['threads']) if prior else []
    requests=copy.deepcopy(prior['provider_inputs']) if prior else []
    async def embed(texts,dimensions,**kwargs):return {'result':[VECTOR[:] for _ in texts]}
    memories={actor:Memory(actor,manager.get_chroma_client(),embed_call=embed,embedding_dim=4) for actor in ('a','b')} if args.mode=='llm' else {}
    for step in range(world.step+1,args.steps+1):
        world.step=step;world.begin_persistence_tick(step)
        proxy=world.create_environment_state_proxy()
        for index,actor in enumerate(('a','b'),1):
            amount=step*index
            if memories:
                memories[actor].set_memory_view(step,[],world._committed_memory_epoch_ids)
                memories[actor].set_write_epoch(world._active_memory_epoch_id,world._memory_epoch_sequence)
            recalled=await memories[actor].retrieve(NOTE,top_k=100,current_step=step) if memories else []
            material={'actor':actor,'step':step,'state_before':state(),'note':NOTE,'recalled':recalled}
            def settle(amount:int,note:str):
                assert amount==step*index and note==NOTE
                proxy['balances'][actor]-=amount;proxy['totals'][actor]+=amount
                proxy['facts'].append({'actor':actor,'step':step,'amount':amount,'note':note})
                return {'actor':actor,'step':step,'amount':amount,'note':note}
            if args.mode=='rule':
                inputs.append(copy.deepcopy(material))
                settle(amount,NOTE)
            else:
                actions=ActionSet();actions.add_action('settle',settle,'支付并保存完整事实',{'type':'object','properties':{'amount':{'type':'integer'},'note':{'type':'string'}},'required':['amount','note']},tags=['settle'])
                tid=manager.agent_thread_store.open_thread(agent_id=actor,checkpoint_step=step,scope={'step':step})
                async def provider(payload):
                    messages=copy.deepcopy(payload['messages'])
                    expected='PARITY:'+json.dumps(material,ensure_ascii=False,sort_keys=True)
                    assert any(expected in (message.get('content') or '') for message in messages)
                    inputs.append(copy.deepcopy(material));requests.append({'actor':actor,'step':step,'messages':messages})
                    return {'role':'assistant','content':'按完整材料支付。','tool_calls':[{'id':f'{actor}-{step}','type':'function','function':{'name':'settle','arguments':json.dumps({'amount':amount,'note':NOTE},ensure_ascii=False)}}]}
                result=await execute_action_loop(instruction='PARITY:'+json.dumps(material,ensure_ascii=False,sort_keys=True),action_set=actions,system_prompt='按完整材料执行一次settle。',stages=['act'],llm_call=provider,completion_action_tags=['settle'],thread_message_recorder=lambda message:manager.agent_thread_store.append_event(tid,'conversation_message',payload={'message':message}),thread_event_recorder=lambda kind,payload:manager.agent_thread_store.append_event(tid,kind,payload=payload))
                assert result.status=='success'
                manager.agent_thread_store.close_thread(tid,metadata={'status':'success'})
                traces.append({'actor':actor,'step':step,'thread_id':tid,'messages':manager.agent_thread_store.read_messages(tid)})
                await memories[actor].add_memories_batch([{'memory_type':'episodic','content':f'主体{actor}完成第{step}步，支付{amount}。'+NOTE,'timestamp':step,'importance':3}])
        await manager.publish_delta(world.seal_persistence_tick(),object())
        snapshots.append({'step':step,'state':state()})
        assert manager._v4_store.restore(step)['environment']['state']==state()
    exported=[]
    for actor,memory in memories.items():
        memory.set_memory_view(world.step,[],world._committed_memory_epoch_ids)
        for row in memory.export_memories():exported.append({'actor':actor,'step':row['timestamp'],'content':row['content'],'type':row['type'],'importance':row['base_importance'],'embedding':list(map(float,row['embedding']))})
    exported.sort(key=lambda row:(row['actor'],row['step']))
    output={'implementation':'old-96b1f3b','mode':args.mode,'initial':initial,'snapshots':snapshots,'decision_inputs':inputs,'memories':exported,'threads':traces,'provider_inputs':requests,'restored_thread_prefix_equal':prefix_equal}
    manager.close()
    (root/'parity.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
    Path(args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--old-source',default='/tmp/society0-v03-old-96b1f3b');parser.add_argument('--run',required=True);parser.add_argument('--output',required=True);parser.add_argument('--source');parser.add_argument('--steps',type=int,default=3);parser.add_argument('--mode',choices=['rule','llm'],required=True)
    asyncio.run(run(parser.parse_args()))
