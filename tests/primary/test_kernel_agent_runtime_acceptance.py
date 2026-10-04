"""Agent 图承担工具推进，完整 Thread 仅在激活入口物化。"""
import pytest
from pydantic_ai import Agent
from society0.kernel.llm import LLMPolicy
from tests.primary.test_kernel_llm import setup, reply, invoke


@pytest.mark.asyncio
async def test_public_agent_iter_owns_tool_progression_and_retains_receipts(tmp_path, monkeypatch):
    seen=[]; original=Agent.iter
    def observed(self,*args,**kwargs):
        seen.append(kwargs)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(Agent,'iter',observed)
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(invoke()),reply(text='完成')])
    with store:
        result=await driver.run(session)
        assert result.status=='completed' and len(calls)==1
        assert len(seen)==1 and seen[0]['usage_limits'].request_limit is None
        assert threads.get_tool_result(result.value['thread_id'],'c1') is not None


@pytest.mark.asyncio
async def test_more_than_fifty_model_requests_do_not_gain_sdk_default_limit(tmp_path):
    policy=LLMPolicy(required_names=('work',))
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(text='继续')]*51+[reply(invoke())],terminal=True,policy=policy)
    with store:
        assert (await driver.run(session)).status=='completed'
        assert len(provider.requests)==52 and len(calls)==1


@pytest.mark.asyncio
async def test_empty_first_thread_keeps_explicit_empty_input_frame(tmp_path):
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(text='完成')])
    driver.input_builder=lambda session: []
    with store:
        result=await driver.run(session)
        assert result.status=='completed'
        assert threads.read_messages(result.value['thread_id'])[0]=={'role':'user','content':''}


@pytest.mark.asyncio
async def test_tool_argument_error_identifies_schema_path_and_expected_type(tmp_path):
    from tests.primary.test_kernel_llm import call
    target={'namespace':'m','kind':'job','key':'1'}
    bad=call('bad','action_invoke',{'name':'work','target':target,'arguments':'{"value":3}'})
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(bad),reply(invoke('good'))],terminal=True)
    with store:
        assert (await driver.run(session)).status=='completed' and len(calls)==1
        feedback=threads.get_tool_result(session.cursors['thread_id'],'bad')['content']
        import json
        details=json.loads(feedback)['details']
        assert details[0]['path']==['arguments'] and 'object' in details[0]['message']
