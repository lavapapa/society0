"""模型阶段的独立消费者：提示可执行，结果仅引用原始正文。"""
import json
import pytest
from society0.kernel.llm import LLMPolicy

@pytest.mark.asyncio
async def test_review_reasoning_guidance_explains_actual_stage_marker(tmp_path):
    from tests.primary.test_kernel_llm import setup,reply
    store,threads,provider,driver,session,_=setup(tmp_path,[reply(text='done')],
        policy=LLMPolicy(reasoning_stages=({'name':'observe','desc':'观察'},)))
    try:
        await driver.run(session)
        assert any('-> stage_begin:' in message.get('content','') for message in provider.requests[0][2])
        assert len(provider.requests)==1
    finally:store.close()

@pytest.mark.asyncio
async def test_review_stage_result_keeps_ranges_instead_of_copying_large_body(tmp_path):
    from tests.primary.test_kernel_llm import setup,reply
    original='前言\n-> stage_begin: observe\n'+('完整正文🙂'*100000)
    store,threads,provider,driver,session,_=setup(tmp_path,[reply(text=original)],
        policy=LLMPolicy(reasoning_stages=({'name':'observe','desc':'观察'},)))
    try:
        result=await driver.run(session)
        stages=result.value['reasoning_stages']
        assert len(json.dumps(stages,ensure_ascii=False))<2048
        segment=next(item for item in stages[0]['segments'] if item['name']=='observe')
        assert original[segment['start']:segment['end']]=='完整正文🙂'*100000
        assert threads.read_messages(result.value['thread_id'])[-1]['content']==original
    finally:store.close()
