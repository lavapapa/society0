from society0.kernel.models import ProviderFailure
from tests.primary.provider_http import bind_chat, bind_embedding
"""资源适配器缓存、关闭与逐项事实的非作者审查。"""
import asyncio
import pytest
import httpx
import openai
from openai.types import CreateEmbeddingResponse
from society0.kernel.models import EmbeddingProvider,RESOURCE_SCHEMA
from society0.kernel.storage import StageStore


def provider(store,**kwargs):
    return EmbeddingProvider([{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1','model':'e',
        'concurrency':1,'dimensions':2,'trust_env':False}],store,dimensions=2,batch_wait_ms=0,**kwargs)


def response(count):
    return CreateEmbeddingResponse(model='e',object='list',usage={'prompt_tokens':count,'total_tokens':count},
        data=[{'object':'embedding','index':i,'embedding':[float(i),2.]} for i in range(count)])


@pytest.mark.asyncio
async def test_review_closed_embedding_provider_rejects_cached_work_without_new_evidence(tmp_path):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        resource=provider(store)
        async def create(**kwargs):return response(len(kwargs['input']))
        await bind_embedding(resource, create)
        await resource.embed(['cached'],metadata={'actor':'a'})
        await resource.close()
        before=store.read(lambda view:view.live_revision)
        with pytest.raises(RuntimeError,match='closed'):
            await resource.embed(['cached'],metadata={'actor':'a'})
        assert store.read(lambda view:view.live_revision)==before


@pytest.mark.asyncio
@pytest.mark.parametrize('status',[400,401,402])
async def test_review_nonretryable_embedding_status_keeps_one_complete_original_request(tmp_path,status):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        resource=provider(store,max_attempts=3,retry_delay=0)
        calls=[]
        async def create(**kwargs):
            calls.append(kwargs)
            raise openai.APIStatusError('rejected',response=httpx.Response(status,request=httpx.Request('POST','http://unused.invalid')),body={'status':status})
        await bind_embedding(resource, create)
        text='完整中文🙂'*3000
        try:
            with pytest.raises(ProviderFailure):await resource.embed([text,'second'],metadata={'actor':'a'})
            assert len(calls)==1
            rows=store.read(lambda view:view.query("SELECT id FROM resource_calls WHERE kind='embedding'"))
            assert len(rows)==1
            evidence=resource.calls.read(rows[0][0])
            assert evidence[0]['payload']['texts']==[text,'second']
            assert evidence[-1]['kind']=='error'
        finally:await resource.close()


@pytest.mark.asyncio
async def test_review_embedding_close_drains_waiter_before_return(tmp_path):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        resource=provider(store)
        entered=asyncio.Event()
        async def create(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        await bind_embedding(resource, create)
        waiter=asyncio.create_task(resource.embed(['pending'],metadata={'actor':'a'}))
        await entered.wait()
        await resource.close()
        assert waiter.done(), 'resource close returned with logical consumer still active'
        with pytest.raises((RuntimeError,asyncio.CancelledError)):await waiter


@pytest.mark.asyncio
async def test_review_declared_embedding_dimension_is_validated_before_cache(tmp_path):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        resource=provider(store)
        async def wrong(**kwargs):
            return CreateEmbeddingResponse(model='e',object='list',usage={'prompt_tokens':1,'total_tokens':1},
                data=[{'object':'embedding','index':0,'embedding':[1.,2.,3.]}])
        await bind_embedding(resource, wrong)
        try:
            with pytest.raises(ProviderFailure,match='dimension'):
                await resource.embed(['text'],metadata={'actor':'a'})
            assert not resource._cache
        finally:await resource.close()


@pytest.mark.asyncio
async def test_review_cache_eviction_retains_exact_physical_sources(tmp_path):
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        resource=provider(store,cache_max_items=1,cache_max_bytes=4096)
        inputs=[]
        async def create(**kwargs):
            inputs.append(list(kwargs['input']))
            return response(len(kwargs['input']))
        await bind_embedding(resource, create)
        try:
            for text in ('first','first','second','first'):
                await resource.embed([text],metadata={'actor':'a','input':text})
                assert len(resource._cache)<=1
                assert resource._cache_bytes<=4096
            assert inputs==[['first'],['second'],['first']]
            uses=store.read(lambda view:view.query("SELECT id FROM resource_calls WHERE kind='embedding_use' ORDER BY rowid"))
            for (identifier,),text in zip(uses,('first','first','second','first')):
                payload=resource.calls.read(identifier)[0]['payload']
                source=payload['sources'][0]
                original=resource.calls.read(source['call_id'])[0]['payload']['texts']
                assert original[source['item_index']]==text
        finally:await resource.close()


@pytest.mark.asyncio
async def test_review_failed_embedding_keeps_thread_to_physical_attempt_link(tmp_path):
    from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
    with StageStore.create(tmp_path/'run',[*RESOURCE_SCHEMA,*THREAD_SCHEMA]) as store:
        threads=ThreadStore(store);tid=threads.open('actor-a',0,'decision')
        resource=EmbeddingProvider([{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1','model':'e',
            'concurrency':1,'dimensions':2,'trust_env':False}],store,threads,dimensions=2,batch_wait_ms=0,max_attempts=1)
        async def rejected(**kwargs):
            raise openai.APIStatusError('rejected',response=httpx.Response(401,request=httpx.Request('POST','http://unused.invalid')),body={})
        await bind_embedding(resource, rejected)
        try:
            with pytest.raises(ProviderFailure):
                await resource.embed(['original'],metadata={'actor':'actor-a','thread_id':tid,'job_id':'memory-job'})
            refs=[item for item in threads.tail(tid)['items'] if item['kind']=='resource_call_ref']
            assert refs, 'failed physical attempt has no Thread/resource identity link'
        finally:await resource.close()


@pytest.mark.asyncio
async def test_review_retry_and_cached_consumer_retain_each_physical_attempt_after_restore(tmp_path):
    from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
    from society0.kernel.models import ResourceCalls
    with StageStore.create(tmp_path/'run', [*RESOURCE_SCHEMA, *THREAD_SCHEMA]) as store:
        threads = ThreadStore(store)
        first = threads.open('a', 0, 'decision')
        second = threads.open('b', 0, 'decision')
        resource = EmbeddingProvider([{'id':'e','api_key':'unused','base_url':'http://unused.invalid/v1',
            'model':'e','concurrency':1,'dimensions':2,'trust_env':False}], store, threads,
            dimensions=2, max_attempts=2, retry_delay=0, batch_wait_ms=0)
        physical = []
        async def create(**kwargs):
            physical.append(kwargs['input'])
            if len(physical) == 1:
                raise openai.InternalServerError('temporary', response=httpx.Response(500,
                    request=httpx.Request('POST','http://unused.invalid')), body={'reason':'temporary'})
            return response(len(kwargs['input']))
        await bind_embedding(resource, create)
        try:
            assert await resource.embed(['original'], metadata={'actor':'a','thread_id':first}) == [[0.,2.]]
            assert await resource.embed(['original','original'], metadata={'actor':'b','thread_id':second}) == [[0.,2.],[0.,2.]]
            assert len(physical) == 2
            store.complete(1)
        finally:
            await resource.close()
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        history = ThreadStore(restored)
        calls = ResourceCalls(restored)
        for tid, count in ((first,1),(second,2)):
            refs = [entry['payload']['call_id'] for entry in history.tail(tid)['items'] if entry['kind']=='resource_call_ref']
            assert len(refs) == 1
            use = calls.read(refs[0])[0]['payload']
            assert len(use['sources']) == count*2
            for position in range(count):
                sources = [source for source in use['sources'] if source['input_index']==position]
                assert len(sources)==2 and sources[0]['call_id'] != sources[1]['call_id']
                for source in sources:
                    events = calls.read(source['call_id'])
                    assert events[0]['payload']['texts'][source['item_index']] == 'original'
                assert calls.read(sources[0]['call_id'])[-1]['kind'] == 'error'
                assert calls.read(sources[1]['call_id'])[-1]['kind'] == 'response'
