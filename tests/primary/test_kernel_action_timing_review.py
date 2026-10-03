"""非作者审查：重试计时、完整视图与失败诊断的边界。"""
import asyncio
import time
from types import SimpleNamespace
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_type', ['openai', 'ollama'])
async def test_review_embedding_retry_timings_do_not_recount_previous_attempt(provider_type, monkeypatch):
    import society0.resource_managers as module
    manager = module.EmbeddingManager([{'id':'m','model':'m','api_key':'unused',
        'base_url':'http://unused.invalid/v1','concurrency':1,'provider_type':provider_type}])
    manager._embedding_timeout_schedule = [10., 10.]
    clock = [100.]
    attempts = [0]
    events = []
    async def request(**kwargs):
        attempts[0] += 1
        clock[0] += 2.
        if attempts[0] == 1:
            raise RuntimeError('transient failure')
        if provider_type == 'ollama':
            return {'embeddings': [[1., 2.]]}
        return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1., 2.])])
    async def pause(seconds):
        clock[0] += seconds
    original = manager.clients['m']
    manager.clients['m'] = SimpleNamespace(embeddings=SimpleNamespace(create=request), embed=request)
    monkeypatch.setattr(module, 'time', SimpleNamespace(time=lambda:clock[0]))
    monkeypatch.setattr(module.asyncio, 'sleep', pause)
    monkeypatch.setattr(manager, '_append_agent_thread_event_best_effort',
        lambda metadata,kind,**kwargs:events.append((kind,kwargs['payload'])))
    try:
        await manager._execute_request(manager.endpoints[0], ['whole'], 2)
        terminal = [body['timing'] for kind,body in events if kind in ('embedding_provider_error','embedding_provider_response')]
        assert len(terminal) == 2
        assert [item['provider_s'] for item in terminal] == [2., 2.]
        assert sum(item['duration_s'] for item in terminal) == pytest.approx(4.)
    finally:
        manager.clients['m'] = original
        await manager.close()


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
