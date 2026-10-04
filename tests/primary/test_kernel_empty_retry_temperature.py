from tests.primary.provider_http import bind_chat, bind_embedding
"""空响应温度调整保持激活预算、完整历史与显式请求配置。"""
import pytest
from society0.kernel.llm import LLMPolicy
from tests.primary.test_kernel_llm import setup, reply


@pytest.mark.asyncio
@pytest.mark.parametrize('base,delta,cap,expected', [
    (0.2, 0.3, 1.0, [0.2, 0.5, 0.5]),
    (0.9, 0.3, 1.0, [0.9, 1.0, 1.0]),
    (None, 0.3, 1.0, [None, 0.3, 0.3]),
    (0.2, None, 1.0, [0.2, 0.2, 0.2]),
])
async def test_empty_retry_uses_original_temperature_cap_and_durable_options(tmp_path,base,delta,cap,expected):
    options={} if base is None else {'temperature':base}
    policy=LLMPolicy(empty_retries=2,empty_retry_temperature_delta=delta,
                     empty_retry_temperature_max=cap,request_options=options)
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(),reply(),reply(text='完成')],policy=policy)
    with store:
        assert (await driver.run(session)).status=='completed'
        assert [request[1].get('temperature') for request in provider.requests]==expected
        assert policy.request_options==options==({} if base is None else {'temperature':base})
        assert not calls
        for first,second in zip(provider.requests,provider.requests[1:]):
            assert second[2][:len(first[2])]==first[2]
        events=threads.tail(session.cursors['thread_id'])['items']
        requests=[e for e in events if e['kind']=='request']
        assert [threads.read_request(session.cursors['thread_id'],e['seq'])['provider_options'].get('temperature') for e in requests]==expected
        retries=[e['payload'] for e in events if e['kind']=='provider_empty_response_retry']
        assert len(retries)==(0 if delta is None else 2)
        if retries:
            assert [r['attempt'] for r in retries]==[1,2]
            assert all(r['temperature_before']==(base or 0) and r['temperature_after']==expected[1] for r in retries)


@pytest.mark.asyncio
@pytest.mark.parametrize('max_turns,empty_retries,expected_calls',[ (1,3,1),(5,0,1),(5,1,2)])
async def test_temperature_option_does_not_extend_turn_or_retry_budget(tmp_path,max_turns,empty_retries,expected_calls):
    policy=LLMPolicy(max_turns=max_turns,empty_retries=empty_retries,empty_retry_temperature_delta=0.2)
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply()]*5,policy=policy)
    with store:
        assert (await driver.run(session)).status=='incomplete'
        assert len(provider.requests)==expected_calls and not calls
        events=threads.tail(session.cursors['thread_id'])['items']
        assert sum(e['kind']=='provider_empty_response_retry' for e in events)==expected_calls-1


@pytest.mark.asyncio
async def test_retry_temperature_state_is_local_to_each_activation(tmp_path):
    policy=LLMPolicy(empty_retries=1,empty_retry_temperature_delta=0.2,request_options={'temperature':0.1})
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(),reply(text='first'),reply(),reply(text='second')],policy=policy)
    with store:
        assert (await driver.run(session)).status=='completed'
        first=threads.read_messages(session.cursors['thread_id'])
        assert (await driver.run(session)).status=='completed'
        assert [r[1]['temperature'] for r in provider.requests]==[0.1,0.3,0.1,0.3]
        assert provider.requests[2][2][:len(first)]==first
        assert policy.request_options=={'temperature':0.1}


@pytest.mark.parametrize('options',[
    {'empty_retry_temperature_delta':0}, {'empty_retry_temperature_delta':-0.2},
    {'empty_retry_temperature_delta':float('nan')}, {'empty_retry_temperature_max':0},
])
def test_invalid_temperature_adjustment_is_explicit(options):
    with pytest.raises(ValueError):LLMPolicy(**options)


@pytest.mark.asyncio
async def test_actual_provider_default_temperature_and_physical_retry_keep_full_evidence(tmp_path):
    import httpx
    import openai
    from society0.kernel.models import ModelProvider
    from tests.primary.test_kernel_llm import sdk_response
    policy=LLMPolicy(empty_retries=1,empty_retry_temperature_delta=.2)
    store,threads,unused,driver,session,calls=setup(tmp_path,[],policy=policy)
    provider=ModelProvider([{'id':'p','api_key':'unused','base_url':'http://unused.invalid/v1','model':'fake','concurrency':1,'trust_env':False}],threads,
                           request_options={'temperature':.7},retry_delay=0,max_attempts=2)
    received=[]
    async def create(**options):
        received.append(options)
        if len(received)==1:return sdk_response('')
        if len(received)==2:raise openai.APITimeoutError(request=httpx.Request('POST','http://unused.invalid'))
        return sdk_response('完成')
    await bind_chat(provider, create)
    driver.provider=provider
    try:
        assert (await driver.run(session)).status=='completed'
        assert [r['temperature'] for r in received]==[.7,.9,.9]
        assert received[1]['messages']==received[2]['messages']
        assert received[1]['messages'][:len(received[0]['messages'])]==received[0]['messages']
        events=threads.tail(session.cursors['thread_id'])['items']
        requests=[threads.read_request(session.cursors['thread_id'],e['seq']) for e in events if e['kind']=='request']
        assert [r['provider_options']['temperature'] for r in requests]==[.7,.9,.9]
        assert requests[1]['messages']==requests[2]['messages']
        assert provider.request_options=={'temperature':.7} and policy.request_options=={}
    finally:
        await provider.close()
        store.close()
