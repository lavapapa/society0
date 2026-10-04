"""保留96b1f3b既有递归凭据过滤合同，并经实际Thread请求与错误留证消费。"""
from copy import deepcopy
import json
import httpx
import openai
from openai.types.chat import ChatCompletion
import pytest
from society0.resource_managers import redact_credentials
from society0.kernel.models import ModelProvider,ProviderFailure
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore


def test_legacy_request_credentials_are_redacted_recursively():
    original={'messages':[{'role':'user','content':'keep'}],'transport':{'headers':{
        'Authorization':'Bearer secret','nested':{'api-key':'secret-2'}},'options':[{'password':'secret-3'}]}}
    before=deepcopy(original);value=redact_credentials(original)
    assert 'secret' not in json.dumps(value,ensure_ascii=False)
    assert value['messages'][0]['content']=='keep'
    assert original==before


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[False,True])
async def test_actual_provider_nested_options_and_evidence_keep_noncredentials(tmp_path,failure):
    secrets=('endpoint-fixture-key','transport-fixture-key','nested-fixture-password','response-fixture-cookie')
    options={'temperature':0.25,'extra_body':{'transport':{'headers':{'Authorization':'Bearer '+secrets[1]},
        'options':[{'password':secrets[2],'note':'keep nested option'}]},'visible':'keep provider option'}}
    original=deepcopy(options);sent=[]
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'decision')
        threads.append_message(tid,{'role':'user','content':'keep full user material'})
        provider=ModelProvider([{'id':'stub','model':'stub','api_key':secrets[0],
            'base_url':'http://unused.invalid/v1','trust_env':False,'concurrency':1}],threads,max_attempts=1)
        async def create(**kwargs):
            sent.append(deepcopy(kwargs))
            if failure:
                response=httpx.Response(400,request=httpx.Request('POST','http://unused.invalid/v1'),
                    headers={'set-cookie':secrets[3],'x-detail':'keep header'},
                    text='failure detail; api_key='+secrets[0])
                raise openai.BadRequestError('failure detail '+secrets[0],response=response,body={'code':'fixture'})
            return ChatCompletion.model_validate({'id':'response','created':0,'model':'stub','object':'chat.completion',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'keep response'}}],
                'provider_details':{'Cookie':secrets[3],'rows':[{'api_key':secrets[0],'note':'keep response detail'}]}})
        provider.manager.clients['stub'].chat.completions.create=create
        try:
            if failure:
                with pytest.raises(ProviderFailure):await provider.request(tid,options)
            else:assert (await provider.request(tid,options))['content']=='keep response'
        finally:await provider.close()
        assert len(sent)==1 and sent[0]['extra_body']==original['extra_body'] and options==original
        items=threads.tail(tid)['items'];request=next(x for x in items if x['kind']=='request')
        restored_request=threads.read_request(tid,request['seq'])
        assert restored_request['messages']==[{'role':'user','content':'keep full user material'}]
        encoded=json.dumps({'items':items,'request':restored_request},ensure_ascii=False)
        for secret in secrets:assert secret not in encoded
        assert 'keep nested option' in encoded and 'keep provider option' in encoded
        assert ('failure detail' if failure else 'keep response detail') in encoded
        assert any(x['kind']==('provider_error' if failure else 'provider_response') for x in items)
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert ThreadStore(restored).tail(tid)['items']==items
