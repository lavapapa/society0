"""真实 SDK 的流终态必须先于领域动作。"""
import pytest
from tests.primary.provider_http import bind_chat
from tests.primary.test_kernel_llm import setup,invoke
from society0.kernel.models import ModelProvider
from society0.kernel.llm import LLMPolicy


@pytest.mark.asyncio
@pytest.mark.parametrize('finish',['tool_calls','length',None])
async def test_chat_terminal_or_eof_controls_action_execution(tmp_path,finish):
    store,threads,_,driver,session,actions=setup(tmp_path,[],terminal=True,policy=LLMPolicy(max_turns=1))
    provider=ModelProvider([{'id':'offline','model':'compatible-test','api_key':'unused','concurrency':1,
                            'base_url':'http://unused.invalid/v1','trust_env':False}],threads,max_attempts=1)
    driver.provider=provider
    async def create(**wire):
        return {'id':'fixture','created':0,'model':'compatible-test','object':'chat.completion',
                'choices':[{'index':0,'finish_reason':finish,
                            'message':{'role':'assistant','content':None,'tool_calls':[invoke()]}}]}
    await bind_chat(provider,create)
    try:
        result=await driver.run(session)
        tid=session.cursors['thread_id']
        if finish=='tool_calls':
            assert result.status=='completed' and len(actions)==1
        else:
            assert result.status=='incomplete' and actions==[]
            assert threads.describe(tid)['status']=='incomplete'
            assert any(e['kind'] in ('provider_response','provider_error') for e in threads.tail(tid)['items'])
    finally:await provider.close();store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['openai-responses','siwc'])
@pytest.mark.parametrize('mode',['completed','incomplete','failed','eof','error'])
async def test_responses_terminal_must_precede_action(tmp_path,kind,mode):
    from tests.primary.test_kernel_provider_fields import bind_responses
    store,threads,_,driver,session,actions=setup(tmp_path,[],terminal=True,policy=LLMPolicy(max_turns=1))
    provider=ModelProvider([{'id':'p','model':'gpt-5.3-codex','provider_type':kind,'api_key':'unused','base_url':'http://unused.invalid/v1','trust_env':False,'credentials_directory':tmp_path/'no-account'}],threads,max_attempts=1)
    driver.provider=provider
    tool=invoke()['function']
    await bind_responses(provider,mode=mode,call={'id':'fc_1','type':'function_call','name':tool['name'],'call_id':'call_1','arguments':tool['arguments'],'status':'completed'})
    try:
        result=await driver.run(session);tid=session.cursors['thread_id']
        assert result.status==('completed' if mode=='completed' else 'incomplete')
        assert len(actions)==(1 if mode=='completed' else 0)
        if mode!='completed':
            assert threads.describe(tid)['status']=='incomplete'
            events=threads.tail(tid)['items']
            assert any(e['kind'] in ('provider_error','provider_response') for e in events)
            if mode in ('failed','error'):
                error=next(e for e in events if e['kind']=='provider_error')
                assert error['payload']['payload']['partial_response']['parts']
    finally:await provider.close();store.close()
