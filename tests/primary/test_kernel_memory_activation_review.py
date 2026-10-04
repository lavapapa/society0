from society0.kernel.memory import MemoryExtension
"""记忆策略的独立跨作用域与业务时间消费者。"""
from tests.primary.scripted_provider import TypedScriptProvider
import pytest
from society0.kernel.memory import Memory,MemoryPolicy,MEMORY_SCHEMA
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
from society0.kernel.storage import StageStore
from society0.kernel.runtime import Actor,Runtime,Phase
from society0.kernel.interaction import Actions,Information,Ref
from society0.kernel.llm import LLMDriver
from tests.primary.test_kernel_memory import Client,Embed
from tests.primary.test_kernel_memory_activation import session


@pytest.mark.asyncio
async def test_review_memory_binding_cannot_be_reused_for_another_same_actor_scope(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA+MEMORY_SCHEMA) as store:
        threads=ThreadStore(store);memory=Memory(store,threads,embed=Embed(),client=Client(),policy=MemoryPolicy(False,False,True))
        actions=Actions(lambda *a:True)
        for action in memory.actions():actions.register(action)
        first=session('a',actions=actions);other=session('a',actions=actions)
        tid=threads.open('a',first.moment,'decision')
        async with memory.activation(first,tid):
            with pytest.raises(RuntimeError,match='scope'):
                await other.actions.invoke('memory.remember',Ref('memory','actor','a'),{'content':'wrong scope'})
            assert (await first.actions.invoke('memory.remember',Ref('memory','actor','a'),{'content':'original scope'})).status=='completed'
        await memory.close()


@pytest.mark.asyncio
async def test_review_date_and_nonconsecutive_business_time_use_step_memory_versions(tmp_path):
    async def extract(*args,**kwargs):return [{'content':'完整记忆','importance':3}]
    class Provider:
        async def request(self,*args,**kwargs):return {'role':'assistant','content':'done','finish_reason':'stop'}
    async def run(store,number,business_time):
        threads=ThreadStore(store)
        memory=Memory(store,threads,embed=Embed(),client=Client(),extract=extract,
                      policy=MemoryPolicy(False,True,False))
        driver=LLMDriver(TypedScriptProvider(Provider(), threads),threads,input_builder=lambda s:[{'role':'system','content':'完整背景'}],extensions=(MemoryExtension(memory),))
        runtime=Runtime([Actor('a',driver)],information=Information(lambda *a:True),actions=Actions(lambda *a:True),store=store)
        async def phase(context):context.activate('a');await context.drain()
        try:await runtime.run_step(number,business_time,[Phase('decision',phase)])
        finally:await runtime.close();await memory.close()
        return threads.find('a',{'time':business_time,'phase':'decision'})
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA+MEMORY_SCHEMA) as store:
        tid=await run(store,1,'2026-10-04')
        assert tid
        assert store.read(lambda v:v.query('SELECT timestamp,visible_step FROM memory_rows'))==[(1,1)]
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        await run(store,2,100000)
        assert store.read(lambda v:v.query('SELECT timestamp,visible_step FROM memory_rows ORDER BY timestamp'))==[(1,1),(2,2)]
