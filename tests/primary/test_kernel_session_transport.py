"""实际 SDK JSON 编码与 Driver 会话身份合同。"""
import json
import httpx2 as httpx
from tests.primary.provider_http import chat_http_response
import openai
import pytest
from tests.primary.test_kernel_llm import setup
from society0.kernel.models import ModelProvider


@pytest.mark.asyncio
@pytest.mark.parametrize('transport',[None,'metadata'])
async def test_driver_sdk_session_transport_is_explicit_and_stable(tmp_path,transport):
    store,threads,_,driver,session,_=setup(tmp_path,[])
    options={} if transport is None else {'session_transport':transport}
    provider=ModelProvider([{'id':'p','api_key':'unused','base_url':'https://provider.invalid/v1','model':'m',
        'concurrency':1,'trust_env':False}],threads,max_attempts=1,**options)
    sent=[]
    async def respond(request):
        body=json.loads(request.content);sent.append(body)
        if transport is None and 'metadata' in body:
            return httpx.Response(400,json={'error':{'message':'Unknown name metadata','type':'invalid_request_error'}})
        return chat_http_response({'id':'reply','created':0,'model':'m','object':'chat.completion',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'完整回答'}}]})
    await provider._start()
    provider.endpoints[0].http._transport=httpx.MockTransport(respond)
    driver.provider=provider
    try:
        assert (await driver.run(session)).status=='completed'
        tid=session.cursors['thread_id'];identity=threads.describe(tid)['provider_session_id']
        first=threads.read_messages(tid)
        assert (await driver.run(session)).status=='completed'
        assert session.cursors['thread_id']==tid and threads.describe(tid)['provider_session_id']==identity
        assert sent[1]['messages'][:len(first)]==first
        if transport is None:assert all('metadata' not in body for body in sent)
        else:assert all(body['metadata']['session_id']==identity for body in sent)
        requests=[item for item in threads.tail(tid)['items'] if item['kind']=='request']
        for item in requests:assert threads.read_request(tid,item['seq'])['provider_session_id']==identity
    finally:
        await provider.close();store.close()
