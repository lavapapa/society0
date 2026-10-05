"""非作者审查：重试计时、完整视图与失败诊断的边界。"""
import asyncio
import time
from types import SimpleNamespace
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_type',['openai','ollama'])
async def test_review_embedding_retry_timings_do_not_recount_previous_attempt(provider_type,monkeypatch,tmp_path):
    import httpx2
    from society0.kernel import models
    from society0.kernel.storage import StageStore
    from tests.primary.provider_http import bind_embedding
    clock=[100.];sent=[];original_sleep=asyncio.sleep
    async def pause(seconds):clock[0]+=seconds;await original_sleep(0)
    with StageStore.create(tmp_path/'run',models.RESOURCE_SCHEMA) as store:
        provider=models.EmbeddingProvider([{'id':'m','model':'m','api_key':'unused','base_url':'http://unused.invalid/v1','concurrency':1,'provider_type':provider_type,'trust_env':False}],store,dimensions=2,retry_delay=.1,batch_wait_ms=0)
        async def create(**wire):
            sent.append(wire);clock[0]+=2.
            if len(sent)==1:return httpx2.Response(503,json={'error':{'message':'transient'}})
            return {'object':'list','model':'m','data':[{'object':'embedding','index':0,'embedding':[1.,2.]}],'usage':{'prompt_tokens':1,'total_tokens':1}}
        await bind_embedding(provider,create)
        monkeypatch.setattr(models.time,'perf_counter',lambda:clock[0]);monkeypatch.setattr(models.asyncio,'sleep',pause)
        try:
            assert await provider.embed(['whole'],metadata={'actor':'a'})==[[1.,2.]]
            identifiers=store.read(lambda r:r.query("SELECT id FROM resource_calls WHERE kind='embedding' ORDER BY rowid"))
            terminal=[provider.calls.read(identifier)[-1]['payload']['timing'] for identifier, in identifiers]
            assert len(terminal)==2 and len(sent)==2 and sent[0]==sent[1]
            assert [item['provider_s'] for item in terminal]==[2.,2.]
            assert sum(item['duration_s'] for item in terminal)==pytest.approx(4.)
        finally:await provider.close()


def test_review_prepared_complete_keeps_resource_and_action_projection_fixed(tmp_path):
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
    from society0.kernel.observation import ObservationService
    with StageStore.create(tmp_path/'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 1, 'decision')
        def action(status):
            seq=threads.start_action(tid, {'name':'work','arguments':{}})
            threads.finish_action(tid,seq,{'status':status},status=status,elapsed_s=.5)
        action('completed')
        threads.record_provider_request(tid,provider_options={'model':'m'},physical_request_id='p')
        payload={'physical_request_id':'p','payload':{'response':{'usage':{'total_tokens':7}},
            'timing':{'duration_s':2.,'provider_s':1.5}}}
        threads.record_provider_event(tid,'provider_response',payload)
        # 同一物理响应的后续解码错误保持第一次时长与 token 报告。
        threads.record_provider_event(tid,'provider_decode_error',payload)
        store.complete(1)
        action('error')
        threads.close(tid,'incomplete',reason='domain_error',elapsed_s=3.)
        store.abort_step()
        with ObservationService(store.path,cache_dir=tmp_path/'cache') as service:
            service.prepare_complete(1)
            deadline=time.monotonic()+15
            while (progress:=service.preparation_status())['state']=='preparing':
                assert time.monotonic()<deadline
                time.sleep(.01)
            assert progress['state']=='ready',progress
            view=progress['view']
            complete=service.call('action_summary',{'view':view})
            live=service.call('action_summary',{})
            assert complete['action_counts']=={'work':1} and complete['failed_action_counts']=={}
            assert live['action_counts']=={'work':2} and live['failed_action_counts']=={'work':1}
            usage=service.call('resource_usage',{'view':view})['totals']
            assert usage['total_tokens']==7 and usage['duration_s']==2. and usage['provider_s']==1.5
            assert usage['duration_reports']==1 and usage['errors']==1
