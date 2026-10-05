"""V03：独立进程执行旧／新、规则／固定模型、连续／恢复的逐值对照。"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

FIELDS=('initial','snapshots','decision_inputs','memories')

def compare(outputs):
    report={}
    for mode in ('rule','llm'):
        baseline=outputs[f'old-{mode}-continuous']
        for version in ('old','new'):
            continuous=outputs[f'{version}-{mode}-continuous']
            restored=outputs[f'{version}-{mode}-restored']
            def histories(data):
                value=data['threads']
                return [row['messages'] for row in value] if isinstance(value,list) else list(value.values())
            if histories(continuous)!=histories(restored):raise AssertionError(f'{version}-{mode}: full Thread messages differ after restore')
            if continuous['provider_inputs']!=restored['provider_inputs']:raise AssertionError(f'{version}-{mode}: full provider inputs differ after restore')
            for route in ('continuous','restored'):
                label=f'{version}-{mode}-{route}';data=outputs[label]
                for field in FIELDS:
                    if data[field]!=baseline[field]:raise AssertionError(f'{label}: {field} differs')
                if not data['restored_thread_prefix_equal']:raise AssertionError(f'{label}: restored Thread prefix differs')
                if len(data['snapshots'])!=3 or len(data['decision_inputs'])!=6:raise AssertionError(f'{label}: incomplete run')
                requests=data['provider_inputs']
                if mode=='llm':
                    if len(requests)!=6:raise AssertionError(f'{label}: model request count')
                    for request,expected in zip(requests,data['decision_inputs']):
                        messages=request['messages'] if isinstance(request,dict) else request
                        material=[json.loads(m['content'][7:]) for m in messages if isinstance(m.get('content'),str) and m['content'].startswith('PARITY:')]
                        if material!=[expected]:raise AssertionError(f'{label}: actual provider input differs')
                    threads=data['threads'];messages=[row['messages'] for row in threads] if isinstance(threads,list) else list(threads.values())
                    if len(messages)!=6:raise AssertionError(f'{label}: Thread count')
                    for history in messages:
                        tools=[m for m in history if m.get('role')=='tool']
                        if len(tools)!=1 or expected['note'] not in tools[0]['content']:raise AssertionError(f'{label}: full tool material missing')
                elif requests or data['threads'] or data['memories']:raise AssertionError(f'{label}: unexpected rule model facts')
                report[label]={'fields_equal':list(FIELDS),'steps':3,'decisions':6,'memories':len(data['memories']),
                    'requests':len(requests),'restored_thread_prefix_equal':True,
                    'continuous_restored_full_messages_equal':True,'continuous_restored_full_provider_inputs_equal':True}
    return report

def run_matrix(old_source,output):
    output.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parents[1];outputs={}
    for version in ('old','new'):
        for mode in ('rule','llm'):
            prefix=output/f'{version}-{mode}-prefix'
            for route,steps,source in [('continuous',3,None),('prefix',1,None),('restored',3 if version=='old' else 2,prefix)]:
                label=f'{version}-{mode}-{route}';path=output/label
                command=[sys.executable,str(root/'benchmarks'/f'core_next_parity_{version}.py'),'--mode',mode,'--steps',str(steps)]
                if version=='old':command+=['--old-source',str(old_source),'--run',str(path),'--output',str(path/'parity.json')]
                else:command+=['--output',str(path)]
                if source:command+=['--source',str(source)]
                env=os.environ.copy();env['PYTHONPATH']=str(root/'src')
                with (output/f'{label}.log').open('w') as log:subprocess.run(command,cwd=root,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
                if route!='prefix':outputs[label]=json.loads((path/'parity.json').read_text())
    result=compare(outputs)
    (output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--old-source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(run_matrix(args.old_source,args.output),ensure_ascii=False))
