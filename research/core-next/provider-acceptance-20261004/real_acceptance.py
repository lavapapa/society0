"""默认仅列计划；执行时由父进程注入环境，脚本不读取或打印密钥。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

CASES=('test_real_endpoint_smoke_llm_and_embedding','test_real_round_robin_action_loop',
       'test_real_exit_restore_memory_and_observation')

BUDGETS={'test_real_endpoint_smoke_llm_and_embedding': {'activations': 1,
                                                'turns': 8,
                                                'auto_write_activations': 0},
 'test_real_endpoint_saturation_llm_and_embedding_managers': {'activations': 3,
                                                              'turns': 8,
                                                              'auto_write_activations': 0},
 'test_real_interview_writes_artifacts': {'activations': 1,
                                          'turns': 8,
                                          'auto_write_activations': 0},
 'test_real_saturation_memory_and_logs': {'activations': 2,
                                          'turns': 8,
                                          'auto_write_activations': 2},
 'test_real_phase_capacity_overrides_runtime': {'activations': 3,
                                                'turns': 8,
                                                'auto_write_activations': 0},
 'test_real_memory_roundtrip': {'activations': 2, 'turns': 8, 'auto_write_activations': 2},
 'test_real_complete_boundary_memory_restore': {'activations': 2,
                                                'turns': 8,
                                                'auto_write_activations': 2},
 'test_real_round_robin_action_loop': {'activations': 2, 'turns': 8, 'auto_write_activations': 0},
 'test_real_social_publish_with_memory': {'activations': 1,
                                          'turns': 8,
                                          'auto_write_activations': 1},
 'test_real_environment_action_tag_completion': {'activations': 1,
                                                 'turns': 8,
                                                 'auto_write_activations': 0},
 'test_real_terminal_rejection_then_success': {'activations': 1,
                                               'turns': 8,
                                               'auto_write_activations': 0},
 'test_real_social_browse_completion_and_memory': {'activations': 2,
                                                   'turns': 8,
                                                   'auto_write_activations': 2},
 'test_real_multi_tick_social_workflow': {'activations': 2,
                                          'turns': 8,
                                          'auto_write_activations': 2},
 'test_real_exit_restore_memory_and_observation': {'activations': 3,
                                                   'turns': 8,
                                                   'auto_write_activations': 3},
 'test_real_vfs_discovery_pagination_original_and_action': {'activations': 1,
                                                            'turns': 20,
                                                            'auto_write_activations': 0}}

def command(mode,output):
    if mode not in ('chain','remaining'):raise ValueError(mode)
    cases=CASES if mode=='chain' else tuple(name for name in BUDGETS if name not in CASES)
    return [sys.executable,'-m','pytest','-o','addopts=','-q','-x',
        *['tests/e2e/test_core_next_real.py::'+name for name in cases],
        '--junitxml='+str(output/(mode+'.xml'))]

async def effects(source,output):
    from tests.e2e.core_next_real_support import configuration,plan
    from society0.kernel.llm import LLMPolicy
    from society0.kernel.memory import MemoryPolicy
    from society0.kernel.runner import run_plan
    config=configuration();rows=[]
    for recall in (False,True):
        destination=output/('recall-on' if recall else 'recall-off')
        async def setup(ctx,phase,held,recall=recall):
            held['memory'].policy=MemoryPolicy(auto_recall=recall,auto_write=False,active_tools=False)
            from society0.kernel import usage
            held['usage_before']=held['store'].read(usage.read)
        candidate,held=plan(config,destination,goals='请依据当前可见材料，指出先前订单代码与已经收款金额。资料不足请明确说明，勿猜测。',
            policy=LLMPolicy(max_turns=8,max_action_calls=4),memory=True,moments=(2,),setup=setup)
        await run_plan(destination,candidate,source=source,step=1)
        from society0.kernel.storage import StageReader
        from society0.kernel.threads import ThreadStore
        with StageReader(destination) as reader:
            threads=ThreadStore(reader);tid=threads.find('a',{'time':2,'phase':'decision'})
            messages=threads.read_messages(tid)
            answer='\n'.join(str(m.get('content','')) for m in messages if m.get('role')=='assistant')
            from society0.kernel import usage
            counts=reader.read(usage.read)
        rows.append({'auto_recall':recall,'messages':messages,'answer':answer,
            'order_code_mentioned':'B42' in answer,'amount_mentioned':'500' in answer,
            'scoring_note':'字面出现仅供定位，正确陈述需结合完整回答人工核对；单次消融不估计统计效果。',
            'usage_before':held['usage_before'],'usage_after':counts,'outcomes':[x.result.status for x in held['outcomes']]})
    (output/'effects.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['chain','remaining','effects']);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source',type=Path);p.add_argument('--execute',action='store_true');a=p.parse_args()
    if not a.execute:
        print(json.dumps(command(a.mode,a.output) if a.mode!='effects' else {'source':str(a.source),'output':str(a.output),'contrast':'auto_recall off/on'},ensure_ascii=False));sys.exit()
    a.output.mkdir(parents=True,exist_ok=False)
    if a.mode!='effects':
        # 环境值由主控注入；这里仅声明真实验收开关和工件路径。
        env=dict(os.environ,SOCIETY0_RUN_CORE_REAL='1',SOCIETY0_REAL_OUTPUT=str(a.output))
        raise SystemExit(subprocess.call(command(a.mode,a.output),env=env))
    if a.source is None:p.error('effects requires --source pointing at the complete source run')
    asyncio.run(effects(a.source,a.output))
