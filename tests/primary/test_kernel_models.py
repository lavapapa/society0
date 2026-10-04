"""提供方显式配置与资源边界。"""
import pytest
from society0.kernel.models import ModelProvider


def endpoint(**overrides):
    return {'id':'fake','api_key':'unused','base_url':'http://unused.invalid/v1','model':'fake',
            'concurrency':3,'trust_env':False,**overrides}


@pytest.mark.asyncio
async def test_model_capacity_defaults_to_endpoint_capacity_and_no_jitter():
    provider=ModelProvider([endpoint()],None)
    try:
        assert provider.manager._global_semaphore._value==3
        assert provider.manager._request_jitter==0
        pool=provider.manager._http_clients[False]._transport._pool
        assert pool._max_connections==3 and pool._max_keepalive_connections==3
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_model_capacity_is_explicitly_configurable():
    provider=ModelProvider([endpoint()],None,global_concurrency=2,http_connections=4,request_jitter=0.001)
    try:
        assert provider.manager._global_semaphore._value==2
        assert provider.manager._request_jitter==0.001
        assert provider.manager._http_clients[False]._transport._pool._max_connections==4
    finally:
        await provider.close()


@pytest.mark.parametrize('options',[{'global_concurrency':0},{'http_connections':False},{'request_jitter':-1}])
def test_invalid_resource_capacity_fails_before_opening_clients(options):
    with pytest.raises(ValueError): ModelProvider([endpoint()],None,**options)


@pytest.mark.asyncio
async def test_profiles_install_through_host_and_preserve_request_defaults(tmp_path):
    from society0.kernel.plugins import Plugin, PluginHost
    from society0.kernel.models import model_plugin
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'complete'})
        plugins=[Plugin('threads',install=lambda ctx:ctx.provide('threads',threads)),
            model_plugin({'decision':{'endpoints':[endpoint()], 'request_options':{'temperature':0,'max_tokens':256}},
                          'analysis':{'endpoints':[endpoint(id='analysis')], 'request_options':{'temperature':0.7}}})]
        async with PluginHost(plugins) as host:
            models=host.service('models','models')
            received=[]
            async def execute(endpoint,payload,**kw):
                received.append(models['decision'].manager._prepare_request_params(payload.copy()))
                return {'content':'done'}
            models['decision'].manager._execute_request=execute
            await models['decision'].request(tid,{'max_tokens':128})
            assert received[0]['temperature']==0 and received[0]['max_tokens']==128
            assert received[0]['messages']==[{'role':'user','content':'complete'}]
            assert models['analysis'].request_options=={'temperature':0.7}
        assert all(client.is_closed for provider in models.values() for client in provider.manager._http_clients.values())


@pytest.mark.asyncio
async def test_provider_credit_error_is_explicit_incomplete_compatible_failure(tmp_path):
    import httpx,openai
    from society0.kernel.models import ProviderFailure
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store); tid=threads.open('a',0,'decision')
        provider=ModelProvider([endpoint()],threads)
        attempts=[]
        async def execute(*args,**kwargs):
            attempts.append(1)
            raise openai.APIStatusError('credits unavailable',response=httpx.Response(402,request=httpx.Request('POST','http://unused.invalid')),body={'code':402})
        provider.manager._execute_request=execute
        try:
            with pytest.raises(ProviderFailure) as error: await provider.request(tid,{})
            assert error.value.reason=='provider_request_error' and attempts==[1]
        finally:await provider.close()


def test_embedding_cache_uses_exact_model_dimensions_text_identity_and_byte_budget():
    from society0.resource_managers import EmbeddingManager
    manager=EmbeddingManager([],cache_max_bytes=512,cache_max_items=10)
    first=manager._make_cache_key('m|x',2,'中文🙂')
    assert first==('m|x',2,'中文🙂')
    assert first!=manager._make_cache_key('m',2,'x|中文🙂')
    assert first!=manager._make_cache_key('m|x',3,'中文🙂')
    manager._cache_put_unlocked(first,[0.1,0.2])
    assert manager._embedding_cache[first]==[0.1,0.2]
    manager._cache_put_unlocked(manager._make_cache_key('m',2,'huge'*1000),[0.1,0.2])
    assert len(manager._embedding_cache)==1 and manager._cache_bytes<=512
    for index in range(100):manager._cache_put_unlocked(manager._make_cache_key('m',2,str(index)),[0.1,0.2])
    assert len(manager._embedding_cache)<10 and manager._cache_bytes<=512


@pytest.mark.asyncio
async def test_model_close_drains_request_and_rejects_later_requests(tmp_path):
    import asyncio
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        provider=ModelProvider([endpoint()],threads)
        entered=asyncio.Event()
        async def request(*args,**kwargs):
            entered.set();await asyncio.Event().wait()
        provider.manager._execute_request=request
        waiter=asyncio.create_task(provider.request(tid,{}));await entered.wait()
        await provider.close()
        assert waiter.done()
        with pytest.raises(asyncio.CancelledError):await waiter
        with pytest.raises(RuntimeError,match='closed'):await provider.request(tid,{})
