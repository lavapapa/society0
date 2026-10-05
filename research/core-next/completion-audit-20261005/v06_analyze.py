"""只读真实结构化旧组的结果、完整原文与成本。"""
import argparse
import json
from pathlib import Path
import importlib.util

def analyze(run,fixture):
    from society0.kernel.storage import StageReader
    from society0.kernel.threads import ThreadStore
    from society0.kernel import usage
    spec=importlib.util.spec_from_file_location('v06_runner',Path(__file__).with_name('v06_runner.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with StageReader(run) as reader:
        threads=ThreadStore(reader)
        tid=threads.find('a',{'time':1,'phase':'decision'})
        messages=threads.read_messages(tid)
        rows=reader.read(lambda v:v.query('SELECT count,total,phrase FROM submissions'))
        submissions=[dict(zip(('count','total','phrase'),r)) for r in rows]
        counts=reader.read(usage.read)
        original=module.structured_originals(fixture,messages)
    calls={c['id']:c for m in messages for c in m.get('tool_calls',[])}
    shells=[]
    for message in messages:
        if message['role']=='tool' and calls[message['tool_call_id']]['function']['name']=='bash':
            call=calls[message['tool_call_id']]
            value=json.loads(message['content'])
            output=None
            if value['stdout'].strip():output=json.loads(value['stdout'])
            shells.append({'call':call,'result':value,'verified_recomputation':value['exit_code']==0 and output=={'count':12,'total':546}})
    expected={'count':len(fixture['prices']),'total':sum(r['amount'] for r in fixture['prices']),
              'phrase':fixture['report'].split('\n核对短语：',1)[1]}
    result={'expected':expected,'actual':submissions,'judgment_correct':submissions==[expected],
            'original_check':original,'messages':messages,'usage':counts,
            'cost':json.loads((run/'v06-cost.json').read_text()),'shell_attempts':shells,
            'recomputation_correct':any(s['verified_recomputation'] for s in shells),
            'independent_task_complete':submissions==[expected] and any(s['verified_recomputation'] for s in shells),
            'frozen_test_result':'failed: first bash stdout selected before later successful correction' if shells and not shells[0]['verified_recomputation'] else 'see original process result'}
    module.save(run/'v06-independent-result.json',result)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--fixture',type=Path,required=True)
    a=p.parse_args();result=analyze(a.run,json.loads(a.fixture.read_text()))
    print(json.dumps({k:result[k] for k in ('expected','actual','judgment_correct','original_check')},ensure_ascii=False))
