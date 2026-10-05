"""提供方显式配置、SDK传输、缓存与资源边界。"""
import pytest
from tests.primary.provider_http import bind_chat,bind_embedding
from society0.kernel.models import ModelProvider,EmbeddingProvider,ProviderFailure,RESOURCE_SCHEMA
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore


def endpoint(**overrides):
    return {'id':'fake','api_key':'unused','base_url':'http://unused.invalid/v1','model':'fake',
            'concurrency':3,'trust_env':False,**overrides}


@pytest.mark.asyncio
async def test_model_capacity_defaults_to_endpoint_capacity_and_no_jitter():
    provider=ModelProvider([endpoint()],None)
    try:
        await provider._start()
        assert provider._profile_limit is None
        selected=provider.endpoints[0]
        assert selected.resources.endpoint._value==3 and selected.resources.jitter==0
        pool=selected.http._transport._pool
        assert pool._max_connections==3 and pool._max_keepalive_connections==3
        assert selected.client.max_retries==0
    finally:await provider.close()


@pytest.mark.asyncio
async def test_model_capacity_is_explicitly_configurable():
    provider=ModelProvider([endpoint()],None,global_concurrency=2,http_connections=4,request_jitter=.001)
    try:
        await provider._start()
        assert provider._profile_limit._value==2
        assert provider.endpoints[0].resources.jitter==.001
        assert provider.endpoints[0].http._transport._pool._max_connections==4
    finally:await provider.close()


@pytest.mark.parametrize('options',[{'global_concurrency':0},{'http_connections':False},{'request_jitter':-1}])
def test_invalid_resource_capacity_fails_before_opening_clients(options):
    with pytest.raises(ValueError):ModelProvider([endpoint()],None,**options)


@pytest.mark.asyncio
async def test_profiles_install_through_host_and_preserve_request_defaults(tmp_path):
    from society0.kernel.plugins import Plugin,PluginHost
    from society0.kernel.models import model_plugin
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'complete'})
        plugins=[Plugin('threads',install=lambda c:c.provide('threads',threads)),
                 model_plugin({'decision':{'endpoints':[endpoint()], 'request_options':{'temperature':0,'max_tokens':256}},
                               'analysis':{'endpoints':[endpoint(id='analysis')],'request_options':{'temperature':.7}}})]
        async with PluginHost(plugins) as host:
            models=host.service('models','models');received=[]
            async def create(**wire):received.append(wire);return {'content':'done'}
            await bind_chat(models['decision'],create)
            await models['decision'].request(tid,{'max_tokens':128})
            assert received[0]['temperature']==0 and received[0]['max_completion_tokens']==128
            assert received[0]['messages']==[{'role':'user','content':'complete'}]
            assert models['analysis'].request_options=={'temperature':.7}
        assert models['decision'].endpoints[0].http.is_closed
        assert all(provider._closed for provider in models.values())


@pytest.mark.asyncio
async def test_provider_credit_error_is_explicit_incomplete_compatible_failure(tmp_path):
    import httpx2
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'credits'})
        provider=ModelProvider([endpoint()],threads);attempts=[]
        async def create(**wire):
            attempts.append(wire)
            return httpx2.Response(402,json={'error':{'message':'credits unavailable'}})
        await bind_chat(provider,create)
        try:
            with pytest.raises(ProviderFailure) as error:await provider.request(tid,{})
            assert error.value.reason=='provider_request_error' and len(attempts)==1
        finally:await provider.close()


@pytest.mark.asyncio
async def test_embedding_cache_uses_exact_model_dimensions_text_identity_and_byte_budget(tmp_path):
    from openai.types import CreateEmbeddingResponse
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        a=EmbeddingProvider([endpoint(model='m|x')],store,cache_max_bytes=512,cache_max_items=10,batch_wait_ms=0)
        b=EmbeddingProvider([endpoint(model='m')],store,cache_max_bytes=512,cache_max_items=10,batch_wait_ms=0)
        received=[]
        async def create(**wire):
            received.append(wire)
            return CreateEmbeddingResponse(model=wire['model'],object='list',usage={'prompt_tokens':1,'total_tokens':1},
                data=[{'index':i,'object':'embedding','embedding':[.1]*wire.get('dimensions',2)} for i,_ in enumerate(wire['input'])])
        await bind_embedding(a,create);await bind_embedding(b,create)
        try:
            assert await a.embed(['中文🙂'],metadata={'actor':'a'},dimensions=2)==[[.1,.1]]
            await a.embed(['中文🙂'],metadata={'actor':'a'},dimensions=2)
            await a.embed(['中文🙂'],metadata={'actor':'a'},dimensions=3)
            await b.embed(['x|中文🙂'],metadata={'actor':'a'},dimensions=2)
            assert len(received)==3
            await a.embed(['huge'*1000],metadata={'actor':'a'},dimensions=2)
            assert (2,'huge'*1000) not in a._cache
            for index in range(100):await a.embed([str(index)],metadata={'actor':'a'},dimensions=2)
            assert len(a._cache)<10 and a._cache_bytes<=512
        finally:await a.close();await b.close()


@pytest.mark.asyncio
async def test_model_close_drains_request_and_rejects_later_requests(tmp_path):
    import asyncio
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'wait'})
        provider=ModelProvider([endpoint()],threads);entered=asyncio.Event()
        async def create(**wire):entered.set();await asyncio.Event().wait()
        await bind_chat(provider,create)
        waiter=asyncio.create_task(provider.request(tid,{}));await entered.wait()
        await provider.close()
        with pytest.raises(asyncio.CancelledError):await waiter
        assert waiter.done() and provider.endpoints[0].http.is_closed
        with pytest.raises(RuntimeError,match='closed'):await provider.request(tid,{})


@pytest.mark.asyncio
async def test_native_structured_output_uses_real_sdk_schema_and_preserves_json_body(tmp_path):
    schema={'type':'object','properties':{'score':{'type':'integer'}},'required':['score'],'additionalProperties':False}
    output={'type':'json_schema','json_schema':{'name':'assessment','strict':True,'schema':schema}}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'interview')
        threads.append_message(tid,{'role':'user','content':'完整访谈材料'})
        provider=ModelProvider([endpoint()],threads,max_attempts=1);sent=[]
        async def create(**wire):sent.append(wire);return {'content':'{"score":8}'}
        await bind_chat(provider,create)
        try:
            response=await provider.request(tid,{'response_format':output})
            assert sent[0]['response_format']==output
            assert sent[0]['messages']==[{'role':'user','content':'完整访谈材料'}]
            assert response['content']=='{"score":8}' and response['finish_reason']=='stop'
            assert threads.read_messages(tid)[-1]['content']=='{"score":8}'
        finally:await provider.close()
