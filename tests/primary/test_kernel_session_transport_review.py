"""非作者：显式会话传输在物理重试中保持身份且不改调用方配置。"""
import copy
import json
import httpx
import openai
import pytest
from tests.primary.test_kernel_llm import setup
from society0.kernel.models import ModelProvider

@pytest.mark.asyncio
@pytest.mark.parametrize('transport',[None,'metadata'])
async def test_review_session_transport_preserves_custom_fields_retry_and_config(tmp_path,transport):
    store,threads,_,driver,session,_=setup(tmp_path,[])
    supplied={'extra_body':{'metadata':{'study':'unchanged'},'custom':'kept'}} if transport=='metadata' else {'temperature':0.25}
    original=copy.deepcopy(supplied)
    provider=ModelProvider([{'id':'p','api_key':'unused','base_url':'https://provider.invalid/v1','model':'m','concurrency':1,'trust_env':False}],threads,max_attempts=2,retry_delay=0,request_options=supplied,session_transport=transport)
    sent=[]
    async def respond(request):
        sent.append(json.loads(request.content))
        if len(sent)==1:return httpx.Response(503,json={'error':{'message':'temporary'}})
        return httpx.Response(200,json={'id':'reply','created':0,'model':'m','object':'chat.completion','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'完整回答'}}]})
    await provider.manager.clients['p'].close()
    provider.manager.clients['p']=openai.AsyncOpenAI(api_key='unused',base_url='https://provider.invalid/v1',max_retries=0,http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    driver.provider=provider
    try:
        assert (await driver.run(session)).status=='completed'
        assert len(sent)==2 and sent[0]==sent[1]
        assert supplied==original and provider.request_options==original
        tid=session.cursors['thread_id'];identity=threads.describe(tid)['provider_session_id']
        if transport is None:assert 'metadata' not in sent[0]
        else:
            assert sent[0]['metadata']=={'study':'unchanged','session_id':identity}
            assert sent[0]['custom']=='kept'
        requests=[threads.read_request(tid,item['seq']) for item in threads.tail(tid)['items'] if item['kind']=='request']
        assert len(requests)==2 and all(item['provider_session_id']==identity for item in requests)
    finally:
        await provider.close();store.close()
