from tests.primary.provider_http import bind_chat, bind_embedding
"""稳定阶段的短时长事实，保留包含关系。"""
import asyncio
import pytest
from openai.types.chat import ChatCompletion
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
from society0.kernel.models import ModelProvider
from society0.kernel.observation import Observation


@pytest.mark.asyncio
async def test_physical_queue_jitter_provider_times_survive_projection(tmp_path,monkeypatch):
    import society0.resource_managers as module
    monkeypatch.setattr(module.random,'uniform',lambda a,b:b)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        provider=ModelProvider([{'id':'m','model':'m','api_key':'unused','base_url':'http://unused.invalid/v1','trust_env':False,'concurrency':1}],threads,max_attempts=1,request_jitter=.03)
        async def create(**kwargs):
            await asyncio.sleep(.01)
            return ChatCompletion(id='r',object='chat.completion',created=0,model='m',choices=[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'full'}}])
        await bind_chat(provider, create)
        await provider.endpoints[0].resources.endpoint.acquire()
        task=asyncio.create_task(provider.request(tid,{}))
        await asyncio.sleep(.055);provider.endpoints[0].resources.endpoint.release()
        try:await task
        finally:await provider.close()
        counts=Observation(store.path).resource_usage()['totals']
        assert counts['queue_s']>=.015 and counts['jitter_s']>=.025 and counts['provider_s']>=.008
        assert counts['duration_s']>=counts['queue_s']+counts['jitter_s']+counts['provider_s']
        assert counts['provider_reports']==1
        response=next(item for item in threads.tail(tid)['items'] if item['kind']=='provider_response')
        assert response['payload']['payload']['timing']['provider_s']==counts['provider_s']
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        assert Observation(restored.path).resource_usage()['totals']==counts


@pytest.mark.asyncio
async def test_activation_reports_memory_model_and_cleanup_stage_facts(tmp_path):
    from society0.kernel.llm import LLMDriver
    from society0.kernel.interaction import Actions,Information,InteractionScope,Moment
    from society0.kernel.runtime import Session,Actor
    class Provider:
        async def request_model(self,*args,**kwargs):
            from pydantic_ai.messages import ModelResponse,TextPart
            await asyncio.sleep(.01)
            return ModelResponse(parts=[TextPart('done')],finish_reason='stop'),0,None
    class Memory:
        def activation(self,*args):
            from contextlib import nullcontext
            return nullcontext()
        async def before_activation(self,*args):await asyncio.sleep(.01);return []
        async def after_activation(self,*args):await asyncio.sleep(.01)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);driver=LLMDriver(Provider(),threads,input_builder=lambda s:[{'role':'user','content':'当前任务'}],memory=Memory())
        scope=InteractionScope('a',Moment(1,'decision'))
        session=Session(Actor('a',driver),scope,Information(lambda *a:True).bound(scope),Actions(lambda *a:True).bound(scope),{},None,(),None, step=1)
        result=await driver.run(session)
        timings=result.value['phase_timings']
        for name in ('model_s','memory_recall_s','memory_write_s'):assert timings[name]>=.008
        event=threads.tail(result.value['thread_id'])['items'][-1]
        assert event['payload']['phase_timings']==timings
        summary=Observation(store.path).action_summary()
        assert summary['phase_timings']['memory_write_s']['count']==1
        assert summary['phase_timings']['model_s']['total_s']==timings['model_s']


@pytest.mark.asyncio
@pytest.mark.parametrize('fail',[False,True])
async def test_runtime_reports_phase_and_complete_time_without_changing_receipt(tmp_path,fail):
    import time
    from society0.kernel.runtime import Runtime,Phase
    from society0.kernel.interaction import Information,Actions
    from society0.kernel.schedule import Progress
    with StageStore.create(tmp_path/'run',[]) as store:
        original=store.complete
        def complete(*args,**kwargs):
            time.sleep(.01)
            if fail:raise OSError('publish failed')
            return original(*args,**kwargs)
        store.complete=complete
        async def phase(ctx):await asyncio.sleep(.01)
        progress=Progress(tmp_path/'progress.json',store.run_id)
        runtime=Runtime([],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store,progress=progress)
        try:
            if fail:
                with pytest.raises(OSError):await runtime.run_step(1,1,[Phase('env',phase)])
            else:
                receipt=await runtime.run_step(1,1,[Phase('env',phase)])
                assert receipt['step']==1
            timing=runtime.last_timing
            assert timing['phase_s']>=.008 and timing['complete_s']>=.008
            assert timing['finalization_s']>=timing['complete_s']
            assert timing['total_s']>=timing['phase_s']+timing['finalization_s']
            assert timing['complete_step']==(0 if fail else 1) and timing['failed']==fail
            assert progress.read()['timing']==timing
        finally:await runtime.close()
