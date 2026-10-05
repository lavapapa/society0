"""共享资源的公开消费者验收：旧 manager 协议迁移至 Provider 与 Thread。"""
import asyncio
from copy import deepcopy
import httpx2
import pytest
from society0.function_registry import normalize_strict_function_parameters
from society0.kernel.models import ModelProvider,EmbeddingProvider,ProviderFailure,RESOURCE_SCHEMA
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
from tests.primary.provider_http import bind_chat,bind_embedding


def endpoint(**options):
    return {'id':'offline','api_key':'unused','base_url':'http://unused.invalid/v1','model':'compatible-test','concurrency':3,'trust_env':False,**options}


def vectors(wire):
    return {'object':'list','model':'compatible-test','usage':{'prompt_tokens':len(wire['input']),'total_tokens':len(wire['input'])},'data':[{'object':'embedding','index':i,'embedding':[float(i),float(len(text))]} for i,text in enumerate(wire['input'])]}


def test_strict_normalization_keeps_optional_enum_nullable():
    normalized = normalize_strict_function_parameters(
        {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "enum": ["buyer", "seller"],
                }
            },
            "required": [],
        }
    )

    assert normalized["properties"]["role"]["type"] == ["string", "null"]
    assert normalized["properties"]["role"]["enum"] == [
        "buyer",
        "seller",
        None,
    ]



@pytest.mark.asyncio
async def test_managers_route_mixed_trust_env_endpoints_to_distinct_pools(tmp_path):
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA]) as store:
        configs=[endpoint(id='direct',trust_env=False),endpoint(id='proxied',trust_env=True)]
        providers=[ModelProvider(configs,ThreadStore(store)),EmbeddingProvider(configs,store)]
        counts=[];pools=[]
        for provider in providers:
            await provider._start()
            assert provider.endpoints[0].http is not provider.endpoints[1].http
            for item,trust in zip(provider.endpoints,(False,True)):
                assert item.http._trust_env is trust and item.client._client is item.http
                pools.append(item.http);counter=[0];counts.append(counter);original=item.http.aclose
                async def close(original=original,counter=counter):counter[0]+=1;await original()
                item.http.aclose=close
        for provider in providers:await provider.close();await provider.close()
        assert all(count==[1] for count in counts) and all(pool.is_closed for pool in pools)


@pytest.mark.asyncio
async def test_openai_azure_and_embedding_clients_disable_sdk_retries(tmp_path):
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA]) as store:
        model=ModelProvider([endpoint(id='openai'),endpoint(id='azure',provider_type='azure',api_version='2024-02-15-preview')],ThreadStore(store))
        embedding=EmbeddingProvider([endpoint()],store)
        try:
            for provider in (model,embedding):
                await provider._start()
                assert all(e.client.max_retries==0 for e in provider.endpoints)
        finally:await model.close();await embedding.close()


@pytest.mark.asyncio
async def test_embedding_microbatch_flushes_same_bucket_batches_in_parallel(tmp_path):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        provider=EmbeddingProvider([endpoint()],store,dimensions=2,batch_texts=2,batch_wait_ms=5)
        active=peak=calls=0
        async def create(**wire):
            nonlocal active,peak,calls
            active+=1;calls+=1;peak=max(peak,active)
            try:await asyncio.sleep(.03);return vectors(wire)
            finally:active-=1
        await bind_embedding(provider,create)
        try:
            results=await asyncio.gather(*(provider.embed([f'unique embedding text {i}'],metadata={'actor':str(i)}) for i in range(8)))
            assert len(results)==8 and all(len(value)==1 for value in results)
            assert calls==4 and 1<peak<=3 and len(provider._cache)==8
            assert len(store.read(lambda r:r.query("SELECT id FROM resource_calls WHERE kind='embedding'")))==4
        finally:await provider.close()


@pytest.mark.asyncio
async def test_embedding_microbatch_preserves_plural_trace_metadata(tmp_path):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        provider=EmbeddingProvider([endpoint()],store,dimensions=2,batch_wait_ms=1)
        metadata={'actor':'alice','step':0,'step_name':'publish_once','interaction_type':'env_post_embedding','interaction_name':'publish_post','agent_ids':['alice','bob'],'post_ids':['post_1','post_2']}
        original=deepcopy(metadata)
        async def create(**wire):return vectors(wire)
        await bind_embedding(provider,create)
        try:
            result=await provider.embed(['post one','post two'],metadata=metadata)
            assert len(result)==2 and metadata==original
            identifier=store.read(lambda r:r.query("SELECT id FROM resource_calls WHERE kind='embedding_use'"))[0][0]
            use=provider.calls.read(identifier)[0]['payload']
            assert use['metadata']==original and [source['input_index'] for source in use['sources']]==[0,1]
            assert provider.calls.read(use['sources'][0]['call_id'])[0]['payload']['texts']==['post one','post two']
        finally:await provider.close()


@pytest.mark.asyncio
async def test_embedding_microbatch_coalesces_distinct_agent_threads(tmp_path):
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA]) as store:
        threads=ThreadStore(store);ids=[threads.open(f'actor-{i}',7,'memory') for i in range(20)]
        provider=EmbeddingProvider([endpoint(concurrency=10)],store,threads,dimensions=2,batch_texts=20,batch_wait_ms=5)
        sent=[]
        async def create(**wire):sent.append(wire);return vectors(wire)
        await bind_embedding(provider,create)
        try:
            results=await asyncio.gather(*(provider.embed([f'actor {i} memory'],metadata={'actor':f'actor-{i}','thread_id':ids[i],'step':7,'memory_ids':[f'memory-{i}'],'interaction_type':'memory_write'}) for i in range(20)))
            assert len(sent)==1 and sent[0]['input']==[f'actor {i} memory' for i in range(20)]
            assert results==[[[float(i),float(len(f'actor {i} memory'))]] for i in range(20)]
            for i,tid in enumerate(ids):
                ref=next(e['payload']['call_id'] for e in threads.tail(tid)['items'] if e['kind']=='resource_call_ref')
                use=provider.calls.read(ref)[0]['payload']
                assert use['metadata']['thread_id']==tid and use['metadata']['memory_ids']==[f'memory-{i}']
                assert provider.calls.read(use['sources'][0]['call_id'])[0]['payload']['texts'][use['sources'][0]['item_index']]==f'actor {i} memory'
        finally:await provider.close()


@pytest.mark.asyncio
async def test_llm_manager_enforces_hard_timeout_and_logs_failure(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',3,'timeout_probe');threads.append_message(tid,{'role':'user','content':'time out'})
        provider=ModelProvider([endpoint(timeout=.01)],threads,max_attempts=1)
        async def slow(**wire):await asyncio.sleep(.05);raise AssertionError('must cancel')
        await bind_chat(provider,slow)
        try:
            with pytest.raises(ProviderFailure):await provider.request(tid,{})
            events=threads.tail(tid)['items'];assert [e['kind'] for e in events if e['kind'].startswith('provider_')]==['provider_error']
            assert len([e for e in events if e['kind']=='request'])==1
            assert next(e for e in events if e['kind']=='provider_error')['payload']['payload']['error'] is not None
        finally:await provider.close()


@pytest.mark.asyncio
async def test_llm_manager_retries_after_first_connection_failure(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'retry');threads.append_message(tid,{'role':'user','content':'retry'})
        provider=ModelProvider([endpoint()],threads,max_attempts=2,retry_delay=0);attempts=0
        async def create(**wire):
            nonlocal attempts
            attempts+=1
            if attempts==1:raise httpx2.ConnectError('temporary connection failure')
            return {'content':'recovered'}
        await bind_chat(provider,create)
        try:
            assert (await provider.request(tid,{}))['content']=='recovered' and attempts==2
            events=threads.tail(tid)['items'];assert len([e for e in events if e['kind']=='request'])==2
            assert len([e for e in events if e['kind']=='provider_error'])==len([e for e in events if e['kind']=='provider_response'])==1
        finally:await provider.close()


@pytest.mark.asyncio
async def test_llm_manager_http_transport_preserves_tool_contract_flags(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'tools');threads.append_message(tid,{'role':'user','content':'probe'})
        provider=ModelProvider([endpoint()],threads);sent=[]
        async def create(**wire):sent.append(wire);return {'content':'ok'}
        await bind_chat(provider,create)
        try:
            result=await provider.request(tid,{'parallel_tool_calls':False,'tools':[{'type':'function','function':{'name':'probe_action','parameters':{'type':'object','properties':{},'required':[],'additionalProperties':False},'strict':True}}]})
            assert result['content']=='ok' and len(sent)==1
            assert sent[0]['parallel_tool_calls'] is False and sent[0]['tools'][0]['function']['strict'] is True
        finally:await provider.close()
