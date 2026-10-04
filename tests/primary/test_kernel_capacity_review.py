from tests.primary.provider_http import bind_chat, bind_embedding
"""真实提供方适配器与激活池的独立容量组合。"""
import asyncio
import pytest
from openai.types.chat import ChatCompletion
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA,ThreadStore
from society0.kernel.models import ModelProvider
from society0.kernel.runtime import Actor,Runtime,Phase,DriverResult
from society0.kernel.results import Results,RESULTS_SCHEMA
from society0.kernel.interaction import Information,Actions


@pytest.mark.asyncio
async def test_review_phase_override_runs_three_actors_while_provider_allows_one(tmp_path):
    active=[0,0];peak=[0,0]
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA+RESULTS_SCHEMA) as store:
        threads=ThreadStore(store)
        provider=ModelProvider([{'id':'m','model':'m','api_key':'unused','base_url':'http://unused.invalid/v1','trust_env':False,'concurrency':1}],threads,max_attempts=1)
        async def create(**kwargs):
            active[1]+=1;peak[1]=max(peak[1],active[1])
            try:
                await asyncio.sleep(.01)
                return ChatCompletion(id='r',object='chat.completion',created=0,model='m',choices=[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'complete'}}])
            finally:active[1]-=1
        await bind_chat(provider, create)
        class Driver:
            async def run(self,session):
                active[0]+=1;peak[0]=max(peak[0],active[0])
                try:
                    tid=threads.open(session.actor.id,session.moment,'decision')
                    threads.append_message(tid,{'role':'user','content':'full context'})
                    await provider.request(tid,{})
                    threads.close(tid,'completed')
                    return DriverResult('completed')
                finally:active[0]-=1
        results=Results(store)
        runtime=Runtime([Actor(str(i),Driver()) for i in range(3)],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store,capacity=1,results=results)
        async def phase(ctx):
            for i in range(3):ctx.activate(str(i))
            await ctx.drain()
        try:
            await runtime.run_step(1,1,[Phase('work',phase,execution='independent',capacity=3)])
            assert peak==[3,1]
            header=results.phase(1,0)
            assert header['capacity']==3 and header['concurrency_source']=='phase'
            assert store.complete_step==1
        finally:
            await runtime.close();await provider.close()
