"""独立复验真实提供方已观测的0读命中／未知写计数组合。"""
import pytest
from society0.kernel.models import ModelProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
from society0.kernel import usage
from tests.primary.provider_http import bind_chat

@pytest.mark.asyncio
async def test_explicit_zero_read_and_null_write_remain_distinct(tmp_path):
    with StageStore.create(tmp_path/'source',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'synthetic'})
        provider=ModelProvider([{'id':'e','model':'test','api_key':'unused','base_url':'http://unused.invalid/v1','trust_env':False}],threads,max_attempts=1)
        async def response(**wire):
            return {'id':'r','created':0,'model':'test','object':'chat.completion',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'done'}}],
                'usage':{'prompt_tokens':8099,'completion_tokens':1,'total_tokens':8100,
                    'prompt_tokens_details':{'cached_tokens':0,'cache_write_tokens':None},
                    'prompt_cache_hit_tokens':0,'prompt_cache_miss_tokens':8099}}
        await bind_chat(provider,response)
        try:await provider.request(tid,{})
        finally:await provider.close()
        actual=store.read(usage.read)['totals']
        assert actual['cache_read_tokens']==0 and actual['cache_read_reports']==1
        assert actual['unknown_cache_read_calls']==0
        assert actual['cache_write_tokens']==0 and actual['cache_write_reports']==0
        assert actual['unknown_cache_write_calls']==1
        store.complete(1)
    with StageStore.restore(tmp_path/'source',tmp_path/'restored') as restored:
        assert restored.read(usage.read)['totals']==actual
