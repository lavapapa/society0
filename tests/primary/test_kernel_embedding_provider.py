from society0.kernel.models import ProviderFailure
from tests.primary.provider_http import bind_chat, bind_embedding
"""物理合批与逐主体原文来源的真实 SDK 替身验收。"""
import asyncio
import pytest
from openai.types import CreateEmbeddingResponse
from society0.kernel.models import EmbeddingProvider, RESOURCE_SCHEMA
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA


@pytest.mark.asyncio
async def test_shared_physical_batch_preserves_duplicate_texts_and_actor_provenance(tmp_path):
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA]) as store:
        threads=ThreadStore(store)
        first=threads.open('a',0,'decision');second=threads.open('b',0,'decision')
        provider=EmbeddingProvider([{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1','model':'e',
            'concurrency':2,'dimensions':2,'trust_env':False}],store,threads,dimensions=2)
        calls=[]
        async def create(**kwargs):
            calls.append(kwargs)
            return CreateEmbeddingResponse(model='e',object='list',usage={'prompt_tokens':2,'total_tokens':2},
                data=[{'object':'embedding','index':i,'embedding':[float(i+1),10.0]} for i in reversed(range(len(kwargs['input'])))])
        await bind_embedding(provider, create)
        try:
            a,b=await asyncio.gather(provider.embed(['same','same'],metadata={'actor':'a','thread_id':first}),
                                    provider.embed(['different','same'],metadata={'actor':'b','thread_id':second}))
            assert len(calls)==1 and calls[0]['input']==['same','different']
            assert a==[[1.,10.],[1.,10.]] and b==[[2.,10.],[1.,10.]]
            physical=store.read(lambda r:r.query("SELECT id FROM resource_calls WHERE kind='embedding'"))
            assert len(physical)==1
            evidence=provider.calls.read(physical[0][0])
            assert evidence[0]['payload']['texts']==['same','different']
            assert len(evidence[1]['payload']['response']['vectors'])==2
            assert all(any(e['kind']=='resource_call_ref' for e in threads.tail(tid)['items']) for tid in (first,second))
            cached=await provider.embed(['same'],metadata={'actor':'a','thread_id':first})
            assert cached==[[1.,10.]] and len(calls)==1
        finally:await provider.close()


@pytest.mark.asyncio
async def test_one_cancelled_cache_waiter_does_not_cancel_other_actor(tmp_path):
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA]) as store:
        threads=ThreadStore(store); a=threads.open('a',0,'decision');b=threads.open('b',0,'decision')
        provider=EmbeddingProvider([{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1','model':'e','concurrency':1,'dimensions':2,'trust_env':False}],store,threads,dimensions=2)
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def create(**kwargs):
            calls.append(kwargs);entered.set();await release.wait()
            return CreateEmbeddingResponse(model='e',object='list',usage={'prompt_tokens':1,'total_tokens':1},data=[{'object':'embedding','index':0,'embedding':[1.,2.]}])
        await bind_embedding(provider, create)
        try:
            first=asyncio.create_task(provider.embed(['same'],metadata={'actor':'a','thread_id':a}))
            second=asyncio.create_task(provider.embed(['same'],metadata={'actor':'b','thread_id':b}))
            await entered.wait();first.cancel()
            with pytest.raises(asyncio.CancelledError):await first
            release.set()
            assert await second==[[1.,2.]] and len(calls)==1
        finally:await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage',['request','response'])
async def test_required_embedding_evidence_failure_never_splits_or_retries_sdk(tmp_path,stage):
    from society0.kernel.models import ThreadWriteError
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        provider=EmbeddingProvider([{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1','model':'e','concurrency':1,'dimensions':2,'trust_env':False}],store,dimensions=2)
        calls=[]
        async def create(**kwargs):
            calls.append(kwargs)
            return CreateEmbeddingResponse(model='e',object='list',usage={'prompt_tokens':2,'total_tokens':2},data=[{'object':'embedding','index':i,'embedding':[1.,2.]} for i in range(2)])
        await bind_embedding(provider, create)
        def fail(*args,**kwargs):raise OSError('disk full')
        if stage=='request':provider.calls.begin=fail
        else:provider.calls.event=fail
        try:
            with pytest.raises(ThreadWriteError):await provider.embed(['one','two'],metadata={'actor':'a'})
            assert len(calls)==(0 if stage=='request' else 1)
        finally:await provider.close()


def test_resource_events_keep_exact_raw_lengths_for_bounded_observers(tmp_path):
    import json
    from society0.kernel.models import ResourceCalls
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        calls=ResourceCalls(store)
        payload={'text':'中文🙂'*30000}
        identifier=calls.begin('embedding','e','m',payload)
        expected=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        total=store.read(lambda r:r.query('SELECT raw_bytes FROM resource_events WHERE call_id=?',(identifier,))[0][0])
        chunks=store.read(lambda r:r.query('SELECT raw_start,raw_bytes FROM resource_chunks WHERE call_id=? ORDER BY chunk',(identifier,)))
        assert total==len(expected)
        assert chunks[0][0]==0 and chunks[-1][0]+chunks[-1][1]==total
        assert all(size<=65536 for _,size in chunks)


@pytest.mark.asyncio
async def test_embedding_profile_controls_http_capacity_and_batching(tmp_path):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        provider=EmbeddingProvider([{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1','model':'e','concurrency':3,'trust_env':False}],store,
            http_connections=4,batch_texts=7,batch_chars=12345,batch_wait_ms=2)
        try:
            await provider._start()
            assert provider.endpoints[0].http._transport._pool._max_connections==4
            assert provider.batch_texts==7
            assert provider.batch_chars==12345
            assert provider.batch_wait*1000==2
        finally:await provider.close()


@pytest.mark.asyncio
async def test_embedding_plugin_declares_shared_schema_and_closes_named_profiles(tmp_path):
    from society0.kernel.models import embedding_plugin
    from society0.kernel.plugins import Plugin,PluginHost
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        plugin=embedding_plugin({'memory':{'endpoints':[{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1','model':'e','concurrency':1,'trust_env':False}]}},threads=None)
        assert plugin.schema==RESOURCE_SCHEMA
        async with PluginHost([Plugin('storage',install=lambda c:c.provide('store',store)),plugin]) as host:
            provider=host.service('embeddings','embeddings')['memory']
            assert isinstance(provider,EmbeddingProvider)
        assert provider._closed


@pytest.mark.asyncio
async def test_failed_late_joiner_retains_every_original_position_and_partial_success(tmp_path):
    import httpx,openai
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA]) as store:
        threads=ThreadStore(store);a=threads.open('a',0,'decision');b=threads.open('b',0,'decision')
        provider=EmbeddingProvider([{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1','model':'e','concurrency':2,'trust_env':False}],store,threads,dimensions=2,batch_texts=1,batch_wait_ms=0,max_attempts=1)
        entered=asyncio.Event();release=asyncio.Event()
        async def create(**kwargs):
            if kwargs['input']==['bad']:
                entered.set();await release.wait()
                raise openai.APIStatusError('rejected',response=httpx.Response(401,request=httpx.Request('POST','http://unused.invalid')),body={})
            return CreateEmbeddingResponse(model='e',object='list',usage={'prompt_tokens':1,'total_tokens':1},data=[{'object':'embedding','index':0,'embedding':[1.,2.]}])
        await bind_embedding(provider, create)
        try:
            first=asyncio.create_task(provider.embed(['bad','good','bad'],metadata={'actor':'a','thread_id':a}))
            await entered.wait()
            second=asyncio.create_task(provider.embed(['bad'],metadata={'actor':'b','thread_id':b}))
            await asyncio.sleep(0);release.set()
            results=await asyncio.gather(first,second,return_exceptions=True)
            assert all(isinstance(item,ProviderFailure) for item in results)
            for tid,expected in ((a,['bad','good','bad']),(b,['bad'])):
                ref=[e['payload']['call_id'] for e in threads.tail(tid)['items'] if e['kind']=='resource_call_ref'][0]
                use=provider.calls.read(ref)[0]['payload']
                actual=[]
                for source in use['sources']:
                    body=provider.calls.read(source['call_id'])[0]['payload']['texts']
                    actual.append(body[source['item_index']])
                assert actual==expected
        finally:await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('indices',[[0,0],[0],[1,2]])
async def test_invalid_embedding_indices_leave_diagnostic_and_never_cache(tmp_path,indices):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        provider=EmbeddingProvider([{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1','model':'e','concurrency':1,'trust_env':False}],store,dimensions=2,max_attempts=1)
        sent=[]
        async def create(**wire):
            sent.append(wire)
            return CreateEmbeddingResponse(model='e',object='list',usage={'prompt_tokens':2,'total_tokens':2},data=[{'object':'embedding','index':i,'embedding':[float(i),2.]} for i in indices])
        await bind_embedding(provider,create)
        try:
            with pytest.raises(ProviderFailure,match='indices'):await provider.embed(['first','second'],metadata={'actor':'a'})
            assert len(sent)==1 and not provider._cache
            identifier=store.read(lambda r:r.query("SELECT id FROM resource_calls WHERE kind='embedding'"))[0][0]
            events=provider.calls.read(identifier)
            assert events[-1]['kind']=='error' and 'indices' in events[-1]['payload']['error']
            assert [item['index'] for item in events[-1]['payload']['partial_response']['data']]==sorted(indices)
        finally:await provider.close()
