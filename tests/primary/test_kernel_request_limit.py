from tests.primary.provider_http import bind_chat, bind_embedding
"""同运行多个模型配置与嵌入物理调用共用显式许可。"""
import asyncio
import pytest
from openai.types import CreateEmbeddingResponse
from openai.types.chat import ChatCompletion
from society0.kernel.models import ModelProvider,EmbeddingProvider,RESOURCE_SCHEMA
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA


def endpoint(identifier):
    return {'id':identifier,'model':identifier,'api_key':'unused','base_url':'http://unused.invalid/v1',
            'concurrency':4,'trust_env':False,'dimensions':2}


def response():
    return ChatCompletion(id='call',created=0,model='m',object='chat.completion',
        choices=[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'done'}}])


def vectors(inputs):
    return CreateEmbeddingResponse(model='e',object='list',usage={'prompt_tokens':1,'total_tokens':1},
        data=[{'object':'embedding','index':i,'embedding':[1.,2.]} for i in range(len(inputs))])


@pytest.mark.asyncio
async def test_two_profiles_and_embedding_share_physical_capacity(tmp_path):
    limit=asyncio.Semaphore(2)
    active=peak=0;entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def physical(kind,kwargs):
        nonlocal active,peak
        active+=1;peak=max(peak,active);calls.append(kind)
        if len(calls)==2:entered.set()
        try:
            await release.wait()
            return vectors(kwargs['input']) if kind=='embedding' else response()
        finally:active-=1
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA]) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'full original'})
        a=ModelProvider([endpoint('a')],threads,request_limit=limit)
        b=ModelProvider([endpoint('b')],threads,request_limit=limit)
        e=EmbeddingProvider([endpoint('e')],store,threads,dimensions=2,request_limit=limit)
        async def ac(**kw):return await physical('a',kw)
        async def bc(**kw):return await physical('b',kw)
        async def ec(**kw):return await physical('embedding',kw)
        await bind_chat(a, ac)
        await bind_chat(b, bc)
        await bind_embedding(e, ec)
        tasks=[asyncio.create_task(a.request(tid,{})),asyncio.create_task(b.request(tid,{})),
               asyncio.create_task(e.embed(['one','two'],metadata={'actor':'a','thread_id':tid}))]
        try:
            await asyncio.wait_for(entered.wait(),1)
            assert active==2
            release.set();await asyncio.gather(*tasks)
            assert peak==2 and sorted(calls)==['a','b','embedding'] and limit._value==2
        finally:
            release.set();await asyncio.gather(a.close(),b.close(),e.close())


@pytest.mark.asyncio
async def test_cache_bypasses_busy_limit_and_close_drains_queued_physical_request(tmp_path):
    class Counted(asyncio.Semaphore):
        acquisitions=0
        def __init__(self,value):super().__init__(value);self.attempted=asyncio.Event()
        async def __aenter__(self):
            self.attempted.set()
            result=await super().__aenter__();self.acquisitions+=1;return result
    limit=Counted(1);entered=asyncio.Event();release=asyncio.Event()
    with StageStore.create(tmp_path/'run',[*THREAD_SCHEMA,*RESOURCE_SCHEMA]) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        m=ModelProvider([endpoint('m')],threads,request_limit=limit)
        e=EmbeddingProvider([endpoint('e')],store,threads,dimensions=2,request_limit=limit,batch_wait_ms=0)
        async def ec(**kw):return vectors(kw['input'])
        async def mc(**kw):entered.set();await release.wait();return response()
        await bind_embedding(e, ec)
        recorded=[];record=e.calls.event
        def evidence(identifier,event,payload):
            recorded.append(event);return record(identifier,event,payload)
        e.calls.event=evidence
        await bind_chat(m, mc)
        try:
            await e.embed(['cached'],metadata={'actor':'a'})
            task=asyncio.create_task(m.request(tid,{}));await asyncio.wait_for(entered.wait(),1)
            assert await asyncio.wait_for(e.embed(['cached'],metadata={'actor':'b'}),1)==[[1.,2.]]
            assert limit.acquisitions==2
            limit.attempted.clear()
            pending=asyncio.create_task(e.embed(['uncached'],metadata={'actor':'a'}))
            await asyncio.wait_for(limit.attempted.wait(),1)
            await asyncio.wait_for(e.close(),1)
            with pytest.raises(asyncio.CancelledError):await pending
            assert limit._value==0
            assert recorded==['response']
            assert store.read(lambda r:r.query("SELECT count(*) FROM resource_calls WHERE kind='embedding'"))[0][0]==1
            release.set();await task
            assert limit._value==1
        finally:
            release.set();await asyncio.gather(m.close(),e.close())


@pytest.mark.asyncio
async def test_required_evidence_failure_and_cancelled_sdk_release_shared_permit(tmp_path,monkeypatch):
    from society0.kernel.models import ThreadWriteError
    limit=asyncio.Semaphore(1);entered=asyncio.Event();calls=[]
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        provider=ModelProvider([endpoint('m')],threads,request_limit=limit)
        async def create(**kwargs):
            calls.append(kwargs);entered.set();await asyncio.Event().wait()
        await bind_chat(provider, create)
        original=threads.record_provider_request
        def fail(*args,**kwargs):raise OSError('disk full')
        try:
            monkeypatch.setattr(threads,'record_provider_request',fail)
            with pytest.raises(ThreadWriteError):await provider.request(tid,{})
            assert limit._value==1 and calls==[]
            monkeypatch.setattr(threads,'record_provider_request',original)
            task=asyncio.create_task(provider.request(tid,{}))
            await asyncio.wait_for(entered.wait(),1)
            await asyncio.wait_for(provider.close(),1)
            with pytest.raises(asyncio.CancelledError):await task
            assert limit._value==1 and len(calls)==1
        finally:await provider.close()


@pytest.mark.asyncio
async def test_factories_share_explicit_limit_across_every_profile(tmp_path):
    from society0.kernel.models import model_plugin,embedding_plugin
    from society0.kernel.services import thread_plugin
    from society0.kernel.composition import compose
    limit=asyncio.Semaphore(3)
    models={name:{'endpoints':[endpoint(name)]} for name in ('one','two')}
    embeddings={'vectors':{'endpoints':[endpoint('e')],'dimensions':2}}
    async with compose(tmp_path/'run',[thread_plugin(),model_plugin(models,request_limit=limit),
                                        embedding_plugin(embeddings,request_limit=limit)]) as host:
        providers=[*host.service('models','models').values(),*host.service('embeddings','embeddings').values()]
        assert all(all(endpoint.resources.shared is limit for endpoint in provider.endpoints) for provider in providers)
    assert limit._value==3

class WaitingLimit(asyncio.Semaphore):
    def __init__(self):
        super().__init__(1)
        self.waiting=asyncio.Event()
    async def __aenter__(self):
        self.waiting.set()
        return await super().__aenter__()


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel',[False,True])
async def test_waiting_request_loads_no_history_and_keeps_original_watermark(tmp_path,monkeypatch,cancel):
    limit=WaitingLimit();await limit.acquire()
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        original={'role':'user','content':'original'*10000}
        threads.append_message(tid,original)
        provider=ModelProvider([endpoint('m')],threads,request_limit=limit)
        snapshots=[];calls=[];read=threads.snapshot_messages
        def snapshot(*args,**kw):snapshots.append(kw);return read(*args,**kw)
        monkeypatch.setattr(threads,'snapshot_messages',snapshot)
        async def create(**kw):calls.append(kw);return response()
        await bind_chat(provider, create)
        task=asyncio.create_task(provider.request(tid,{}))
        try:
            await asyncio.wait_for(limit.waiting.wait(),1)
            assert snapshots==[]
            threads.append_message(tid,{'role':'user','content':'later'})
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
                assert snapshots==[] and calls==[]
                assert not [e for e in threads.tail(tid)['items'] if e['kind'] in {'request','provider_cancelled','provider_error'}]
            else:
                limit.release();await task
                assert calls[0]['messages']==[original]
                request=next(e for e in threads.tail(tid)['items'] if e['kind']=='request')
                assert threads.read_request(tid,request['seq'])['messages']==[original]
        finally:
            await provider.close()
            if limit.locked():limit.release()


@pytest.mark.asyncio
async def test_snapshot_failure_releases_admission_without_physical_attempt(tmp_path,monkeypatch):
    limit=asyncio.Semaphore(1)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        provider=ModelProvider([endpoint('m')],threads,request_limit=limit)
        def fail(*a,**k):raise OSError('snapshot read failed')
        monkeypatch.setattr(threads,'snapshot_messages',fail)
        try:
            with pytest.raises(OSError,match='snapshot read failed'):await provider.request(tid,{})
            assert limit._value==1 and provider.endpoints[0].resources.endpoint._value==4
            assert not [e for e in threads.tail(tid)['items'] if e['kind'] in {'request','provider_error'}]
        finally:await provider.close()


@pytest.mark.asyncio
async def test_retry_backoff_releases_full_history_and_rebuilds_same_watermark(tmp_path,monkeypatch):
    import weakref,httpx,openai
    from society0.kernel import models
    class Messages(list):pass
    paused=asyncio.Event();resume=asyncio.Event();references=[];contents=[]
    real_sleep=asyncio.sleep
    async def sleep(delay):
        if delay==7.123:paused.set();await resume.wait()
        else:await real_sleep(delay)
    monkeypatch.setattr(models.asyncio,'sleep',sleep)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        original={'role':'user','content':'history'*100000}
        threads.append_message(tid,original)
        read=threads.snapshot_messages
        def snapshot(*a,**kw):
            result=read(*a,**kw);result['messages']=Messages(result['messages'])
            references.append(weakref.ref(result['messages']));return result
        monkeypatch.setattr(threads,'snapshot_messages',snapshot)
        provider=ModelProvider([endpoint('m')],threads,retry_delay=7.123)
        async def create(**kw):
            contents.append(kw['messages'][0]['content'])
            if len(contents)==1:
                raise openai.APITimeoutError(request=httpx.Request('POST','http://unused.invalid'))
            assert kw['messages']==[original]
            return response()
        await bind_chat(provider, create)
        task=asyncio.create_task(provider.request(tid,{}))
        try:
            await asyncio.wait_for(paused.wait(),1)
            assert len(references)==1 and references[0]() is None
            threads.append_message(tid,{'role':'user','content':'after first attempt'})
            resume.set();await task
            assert len(references)==2 and contents==[original['content']]*2
        finally:resume.set();await provider.close()
