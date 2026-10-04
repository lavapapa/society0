"""真实服务恢复测试子进程；第二步模型结束后直接退出。"""
import asyncio
import json
import os
import sys
from pathlib import Path
from tests.e2e.core_next_real_support import configuration,plan
from society0.kernel.llm import LLMPolicy
from society0.kernel.runner import run_plan


async def main(root,mode):
    config=configuration()
    if mode=='source':
        async def exit_in_second_step(ctx,phase,held):
            if held['store'].complete_step==1:os._exit(91)
        current,_=plan(config,root/'source',goals='重要经历：B42订单已经收款500元。请原样确认这条必须记住的信息。',
            policy=LLMPolicy(max_turns=8,max_action_calls=4),memory=True,moments=(1,2),after=exit_in_second_step)
        await run_plan(root/'source',current)
        raise AssertionError('controlled process exit was not reached')
    async def recall(ctx,phase,held):
        hits=await held['memory'].recall('a','B42订单收款',top_k=10,current_step=held['store'].complete_step+1)
        assert any('B42' in row['content'] for row in hits)
        return [row['content'] for row in hits]
    current,held=plan(config,root/'restored',goals='请从先前记忆指出订单代码与已经收款金额。',
        policy=LLMPolicy(max_turns=8,max_action_calls=4),memory=True,moments=(2,),after=recall)
    result=await run_plan(root/'restored',current,source=root/'source',step=1)
    assert result['complete_step']==2
    (root/'restored-result.json').write_text(json.dumps({'complete_step':2,'recalled':held['after']},ensure_ascii=False))


if __name__=='__main__':asyncio.run(main(Path(sys.argv[1]),sys.argv[2]))
