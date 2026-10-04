"""实际缓存报告通过物理调用投影保留，并区分缺失与零。"""
import pytest
from society0.kernel.models import ModelProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from society0.kernel import usage
from tests.primary.provider_http import bind_chat


@pytest.mark.asyncio
@pytest.mark.parametrize('reported', [None, 0, 7])
async def test_cache_reports_survive_projection_and_restore(tmp_path, reported):
    config={'id':'e','model':'chat','api_key':'unused','base_url':'http://unused.invalid/v1','concurrency':1,'trust_env':False}
    with StageStore.create(tmp_path/'source', THREAD_SCHEMA) as store:
        threads=ThreadStore(store); tid=threads.open('a',0,'decision')
        threads.append_message(tid, {'role':'user','content':'稳定完整输入'})
        provider=ModelProvider([config],threads,max_attempts=1)
        async def create(**wire):
            counts={'prompt_tokens':10,'completion_tokens':2,'total_tokens':12}
            if reported is not None: counts['prompt_tokens_details']={'cached_tokens':reported,'cache_write_tokens':3}
            return {'id':'r','created':0,'model':'chat','object':'chat.completion',
                    'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'done'}}], 'usage':counts}
        await bind_chat(provider,create)
        try: await provider.request(tid,{})
        finally: await provider.close()
        counts=store.read(usage.read)['totals']
        assert counts['cache_read_tokens']==(reported or 0)
        assert counts['cache_write_tokens']==(3 if reported is not None else 0)
        assert counts['cache_read_reports']==counts['cache_write_reports']==int(reported is not None)
        assert counts['unknown_cache_read_calls']==counts['unknown_cache_write_calls']==int(reported is None)
        assert store.read(lambda view:usage.read(view,actor='a'))['totals']==counts
        store.complete(1)
    with StageStore.restore(tmp_path/'source',tmp_path/'restored') as restored:
        assert restored.read(usage.read)['totals']==counts
