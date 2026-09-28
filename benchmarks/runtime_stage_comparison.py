"""相同逻辑 World 的旧版/当前版连续步骤对照；全部工件保存在临时目录。"""
from __future__ import annotations
import argparse
import asyncio
import copy
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tarfile
import tempfile
from time import perf_counter


def peak_rss():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == 'darwin' else value * 1024


def slope(values):
    center = (len(values) - 1) / 2
    average = statistics.mean(values)
    return sum((i-center)*(v-average) for i,v in enumerate(values)) / sum((i-center)**2 for i in range(len(values)))


async def worker(args):
    from society0.core_data import World
    from society0.persistence import PersistenceManager
    from society0.incremental_checkpoint import PersistenceSchema
    from society0.state_persistence import persistent_state_schema, replaceable_map, append_only_map
    from society0.agent.agent_loop import ActionSet, execute_action_loop
    root = Path(args.run)
    root.mkdir()
    world = World(step=0, event_log_path=str(root/'events.jsonl'))
    world.environment_data['type'] = 'plain'
    world.environment_data['state'] = {
        'active': {str(i): {'balance': 1000 + i, 'quantity': i + 1} for i in range(20)},
        'history': {str(i): {'sequence': i, 'amount': i % 17, 'memo': '完整历史' * 20} for i in range(args.history)}}
    initial = copy.deepcopy(world.environment_data['state'])
    manager = PersistenceManager(str(root))
    manager.configure_v4(world, PersistenceSchema.compile(persistent_state_schema(
        active=replaceable_map(), history=append_only_map()), root_path=('environment', 'state')))
    start = perf_counter()
    await manager.publish_root(world, object())
    bootstrap_sec = perf_counter()-start
    initial_restore = manager._v4_store.restore(0)['environment']['state']
    assert initial_restore == initial
    threads = manager.agent_thread_store
    tid = threads.open_thread(agent_id='synthetic', checkpoint_step=0, scope={'benchmark': 'stage'}) if args.mode == 'agent' else None
    memory = []
    samples = []
    requests = []
    for step in range(1, 21):
        tick_start = perf_counter()
        world.step = step
        world.begin_persistence_tick(step)
        state = world.create_environment_state_proxy()
        start = perf_counter()
        for i in range(20):
            state['active'][str(i)]['balance'] += i + step
            state['history'][str(args.history+(step-1)*20+i)] = {'sequence': args.history+(step-1)*20+i, 'amount': i+step, 'memo': '逐笔新事实' * 20}
        environment_sec = perf_counter()-start
        provider_sec = memory_sec = agent_sec = 0.
        if tid:
            start = perf_counter()
            # DummyMemory 保留全部正文；此阶段不测 Chroma 或嵌入服务。
            mstart = perf_counter()
            recalled = copy.deepcopy(memory)
            memory_sec += perf_counter()-mstart
            actions = ActionSet()
            def adjust():
                state['active']['0']['quantity'] += 1
                return {'quantity': state['active']['0']['quantity']}
            actions.add_action('adjust', adjust, 'Add one unit.', {'type': 'object', 'properties': {}})
            calls = 0
            async def provider(payload):
                nonlocal calls, provider_sec
                pstart = perf_counter()
                requests.append(copy.deepcopy(payload['messages']))
                calls += 1
                reply = {'role': 'assistant', 'content': '', 'tool_calls': [{'id': f'call-{step}', 'type': 'function', 'function': {'name': 'adjust', 'arguments': '{}'}}]} if calls == 1 else {'role': 'assistant', 'content': f'完成第{step}步，保留全部历史。', 'tool_calls': []}
                provider_sec += perf_counter()-pstart
                return reply
            result = await execute_action_loop(instruction=f'第{step}步增加一单位。记忆全文：'+json.dumps(recalled, ensure_ascii=False),
                action_set=actions, system_prompt='Deterministic benchmark agent.', stages=['act'], llm_call=provider,
                prior_messages=threads.read_messages(tid),
                thread_message_recorder=lambda msg: threads.append_event(tid, 'conversation_message', payload={'message': msg}),
                thread_event_recorder=lambda kind, payload: threads.append_event(tid, kind, payload=payload))
            assert result.status == 'success'
            mstart = perf_counter()
            memory.append({'step': step, 'text': f'第{step}步增加一单位，库存为{step+1}。' + '记忆正文' * 50})
            memory_sec += perf_counter()-mstart
            agent_sec = perf_counter()-start
        start = perf_counter()
        delta = world.seal_persistence_tick()
        marker = await manager.publish_delta(delta, object())
        persistence_sec = perf_counter()-start
        tick_elapsed = perf_counter()-tick_start
        # 工件计量在步骤计时区间外，避免目录遍历污染持久化时延。
        artifact_bytes = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
        samples.append({'step':step, 'environment_sec':environment_sec, 'agent_total_sec':agent_sec,
            'provider_sec':provider_sec, 'dummy_memory_sec':memory_sec, 'persistence_sec':persistence_sec,
            'total_sec':tick_elapsed, 'artifact_bytes':artifact_bytes, 'peak_rss_bytes':peak_rss(), 'checkpoint_bytes':marker['bytes_written']})
    # 逐项正确性比较在计时区域之外。
    final = copy.deepcopy(world.environment_data['state'])
    restored = manager._v4_store.restore(20)['environment']['state']
    assert restored == final
    messages = threads.read_messages(tid) if tid else []
    evidence = {'initial':initial, 'final':final, 'restored':restored, 'thread_messages':messages,
                'memory':memory, 'provider_messages':requests}
    manager.close()
    world.event_logger.close()
    report = {'history':args.history, 'mode':args.mode, 'bootstrap_sec':bootstrap_sec, 'samples':samples,
              'slopes':{k:slope([v[k] for v in samples]) for k in samples[0] if k != 'step'},
              'means':{k:statistics.mean(v[k] for v in samples) for k in samples[0] if k != 'step'},
              'final_history_count':len(final['history']), 'thread_message_count':len(messages), 'memory_count':len(memory)}
    Path(args.output).write_text(json.dumps({'report':report, 'evidence':evidence}, ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline', default='7e98f11')
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--run')
    parser.add_argument('--mode', choices=['environment','agent'])
    parser.add_argument('--history',type=int,default=5000)
    parser.add_argument('--output',default='research/runtime-observation/stage-comparison.json')
    args=parser.parse_args()
    if args.worker:
        asyncio.run(worker(args)); return
    script=Path(__file__).resolve()
    repo=script.parent.parent
    results=[]
    with tempfile.TemporaryDirectory(prefix='society0-stage-comparison-') as directory:
        temp=Path(directory)
        archive=temp/'old.tar'
        with archive.open('wb') as stream:
            subprocess.run(['git','archive',args.baseline,'src'],cwd=repo,stdout=stream,check=True)
        old=temp/'old'
        old.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(old, filter='data')
        for history in (5000,50000):
            for mode in ('environment','agent'):
                pair=[]
                for version,source in [(args.baseline,old/'src'),('working-tree',repo/'src')]:
                    stem=f'{history}-{mode}-{version}'
                    output=temp/(stem+'.json')
                    env={**os.environ,'PYTHONPATH':str(source),'SOCIETY0_RUN_REAL_E2E':'0'}
                    with (temp/(stem+'.log')).open('w') as log:
                        proc=subprocess.run([sys.executable,str(script),'--worker','--run',str(temp/stem),'--mode',mode,'--history',str(history),'--output',str(output)],env=env,stdout=log,stderr=log)
                    if proc.returncode:
                        raise RuntimeError((temp/(stem+'.log')).read_text()[-8000:])
                    data=json.loads(output.read_text())
                    data['report']['version']=version
                    results.append(data['report'])
                    pair.append(data['evidence'])
                assert pair[0] == pair[1], f'logical mismatch: {history}/{mode}'
                print(f'Equivalent: history={history} mode={mode}',flush=True)
    Path(args.output).write_text(json.dumps({'baseline':args.baseline,'active_entries':20,'steps':20,
        'comparison':'equal initial/final/restored state, full Thread messages, DummyMemory text and provider message inputs',
        'memory_backend':'local DummyMemory, no Chroma or network; provider local deterministic tool response',
        'results':results},ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
