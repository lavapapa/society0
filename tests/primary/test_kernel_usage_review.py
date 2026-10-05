from tests.primary.provider_http import bind_chat, bind_embedding
"""物理用量事实的非作者消费者。"""
import asyncio
import pytest
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
from society0.kernel.models import ModelProvider
from society0.kernel.observation import Observation


@pytest.mark.asyncio
async def test_review_cancel_after_physical_dispatch_counts_one_unknown_call(tmp_path):
    config={'id':'endpoint','model':'chat','api_key':'unused','base_url':'http://unused.invalid/v1','trust_env':False,'concurrency':1}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision');threads.append_message(tid,{'role':'user','content':'full'})
        provider=ModelProvider([config],threads,max_attempts=1)
        entered=asyncio.Event()
        async def blocked(**kwargs):entered.set();await asyncio.Event().wait()
        await bind_chat(provider, blocked)
        task=asyncio.create_task(provider.request(tid,{}));await entered.wait();task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):await task
        finally:await provider.close()
        counts=Observation(store.path).resource_usage()['totals']
        assert counts['requests']==1
        assert counts['cancelled']==1
        assert counts['responses']==0 and counts['total_reports']==0
        assert counts['unknown_usage_calls']==1
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert Observation(restored.path).resource_usage()['totals']==counts


def test_review_response_projection_failure_rolls_back_body_and_counters(tmp_path,monkeypatch):
    from society0.kernel import usage
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.record_provider_request(tid,provider_options={'model':'m'},physical_request_id='p')
        before=threads.describe(tid)['last_seq'];original=usage.finish
        def fail(*args,**kwargs):original(*args,**kwargs);raise OSError('projection fs error')
        monkeypatch.setattr(usage,'finish',fail)
        with pytest.raises(OSError):threads.record_provider_event(tid,'provider_response',{
            'physical_request_id':'p','payload':{'raw_response':{'content':'x'*100000,'usage':{'total_tokens':9}}}})
        assert threads.describe(tid)['last_seq']==before
        counts=Observation(store.path).resource_usage()['totals']
        assert counts['requests']==1 and counts['responses']==counts['total_tokens']==counts['total_reports']==0
        assert counts['unknown_usage_calls']==1


@pytest.mark.asyncio
@pytest.mark.parametrize('reported',[None,0,7])
async def test_embedding_missing_zero_and_reported_usage_remain_distinct_after_cache_hit(tmp_path,reported):
    from society0.kernel.models import EmbeddingProvider,RESOURCE_SCHEMA
    from society0.kernel import usage
    with StageStore.create(tmp_path/'run',RESOURCE_SCHEMA) as store:
        provider=EmbeddingProvider([{'id':'e','model':'embedding','api_key':'unused','base_url':'http://unused.invalid/v1','concurrency':1,'trust_env':False}],store,max_attempts=1,batch_wait_ms=0)
        requests=[]
        async def create(**wire):
            requests.append(wire)
            response={'model':'embedding','object':'list','data':[{'index':i,'object':'embedding','embedding':[float(i),1.]} for i,_ in enumerate(wire['input'])]}
            if reported is not None:response['usage']={'prompt_tokens':reported,'total_tokens':reported}
            return response
        await bind_embedding(provider,create)
        try:
            first=await provider.embed(['原文'],metadata={'actor':'a'},dimensions=2)
            before=store.read(usage.read)['totals']
            second=await provider.embed(['原文'],metadata={'actor':'b'},dimensions=2)
            after=store.read(usage.read)['totals']
            assert first==second==[[0.,1.]] and len(requests)==1
            assert before['requests']==after['requests']==1
            assert before['total_tokens']==after['total_tokens']==(reported or 0)
            assert before['total_reports']==after['total_reports']==int(reported is not None)
            assert after['unknown_usage_calls']==int(reported is None)
            assert after['output_reports']==0 and after['embedding_uses']==2
        finally:await provider.close()
