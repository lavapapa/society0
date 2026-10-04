from society0.kernel.memory import MemoryExtension
"""单个权威记忆服务的逐激活策略冻结、并发与恢复消费者。"""
from tests.primary.scripted_provider import TypedScriptProvider
import asyncio
import json
from types import SimpleNamespace
import pytest
from society0.kernel.memory import Memory, MemoryPolicy, MemoryActivation, MEMORY_SCHEMA
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
from society0.kernel.storage import StageStore
from society0.kernel.interaction import Actions, Information, InteractionScope, Moment, Ref
from society0.kernel.runtime import Actor, Session
from society0.kernel.llm import LLMDriver, LLMPolicy
from tests.primary.test_kernel_memory import Client, Embed


def session(actor,driver=None,actions=None,phase='decision'):
    scope=InteractionScope(actor,Moment(1,phase))
    actions=actions or Actions(lambda *a:True)
    return Session(Actor(actor,driver),scope,Information(lambda *a:True).bound(scope),actions.bound(scope),{},None,(),None, step=1)


@pytest.mark.asyncio
async def test_two_subjects_policy_is_frozen_and_actions_share_it(tmp_path):
    policies={'a':MemoryActivation(MemoryPolicy(False,False,False),1),
              'b':MemoryActivation(MemoryPolicy(False,False,True),3)}
    selected=[]
    def choose(current):selected.append(current.actor.id);return policies[current.actor.id]
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,*MEMORY_SCHEMA)) as store:
        threads=ThreadStore(store); memory=Memory(store,threads,embed=Embed(),client=Client(),policy_selector=choose)
        actions=Actions(lambda *a:True)
        for action in memory.actions():actions.register(action)
        barrier=asyncio.Event(); entered=asyncio.Event()
        async def run(actor):
            current=session(actor,actions=actions)
            tid=threads.open(actor,current.moment,'decision')
            async with memory.activation(current,tid):
                if actor=='a':entered.set();await barrier.wait()
                else:await entered.wait();policies['a']=policies['b'];barrier.set()
                target=Ref('memory','actor',actor)
                found=await current.actions.find(target)
                assert found.total==(0 if actor=='a' else 4)
                if actor=='a':
                    from society0.kernel.interaction import Unavailable
                    with pytest.raises(Unavailable):
                        await current.actions.describe('memory.remember',target)
                    rejected=await current.actions.invoke('memory.remember',target,{'content':'forbidden'})
                    assert rejected.status=='rejected'
                else:
                    result=await current.actions.invoke('memory.remember',target,{'content':'主体 b 原文'})
                    assert result.status=='completed'
            current.scope.close()
        await asyncio.gather(run('a'),run('b'))
        assert selected==['a','b']
        # 同一主体与自然时点的下一次激活重新选择策略。
        current=session('a',actions=actions);tid=threads.find('a',current.moment)
        async with memory.activation(current,tid):
            assert (await current.actions.find(Ref('memory','actor','a'))).total==4
        assert selected==['a','b','a']
        await memory.close()


@pytest.mark.asyncio
async def test_real_driver_recall_top_k_and_same_moment_policy_survive_restore(tmp_path):
    chosen=[MemoryActivation(MemoryPolicy(True,False,False),1)]
    requests=[]
    class Provider:
        async def request(self,tid,options):
            requests.append(threads.read_messages(tid))
            return {'role':'assistant','content':'done','finish_reason':'stop'}
    def make(store):
        nonlocal threads
        threads=ThreadStore(store)
        memory=Memory(store,threads,embed=Embed(),client=Client(),recall_query=lambda s:'记忆',policy_selector=lambda s:chosen[0])
        driver=LLMDriver(TypedScriptProvider(Provider(), threads),threads,input_builder=lambda s:[{'role':'system','content':'完整背景'}],extensions=(MemoryExtension(memory),))
        return memory,driver
    threads=None
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,*MEMORY_SCHEMA)) as store:
        memory,driver=make(store)
        await memory.seed('a','initial',timestamp=0,entries=[{'content':'记忆'+str(i),'importance':3} for i in range(3)])
        await driver.run(session('a',driver))
        recalled=[json.loads(m['content'])['recalled_memories'] for m in requests[-1] if m['role']=='user' and 'recalled_memories' in m['content']]
        assert len(recalled[-1])==1
        store.complete(1);await memory.close()
    chosen[0]=MemoryActivation(MemoryPolicy(True,False,False),3)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as store:
        memory,driver=make(store)
        await driver.run(session('a',driver))
        recalled=[json.loads(m['content'])['recalled_memories'] for m in requests[-1] if m['role']=='user' and 'recalled_memories' in m['content']]
        assert [len(items) for items in recalled]==[1,3]
        tid=threads.find('a',Moment(1,'decision'))
        policies=[e['payload'] for e in threads.tail(tid)['items'] if e['kind']=='memory_policy']
        assert [value['recall_top_k'] for value in policies]==[1,3]
        await memory.close()


@pytest.mark.asyncio
async def test_scope_exit_invalidates_inherited_policy_and_close_drains(tmp_path):
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,*MEMORY_SCHEMA)) as store:
        threads=ThreadStore(store); memory=Memory(store,threads,embed=Embed(),client=Client(),policy=MemoryPolicy(False,False,True))
        actions=Actions(lambda *a:True)
        for action in memory.actions():actions.register(action)
        current=session('a',actions=actions);tid=threads.open('a',current.moment,'decision')
        release=asyncio.Event()
        async def late():
            await release.wait()
            with pytest.raises(RuntimeError,match='activation'):
                await current.actions.find(Ref('memory','actor','a'))
        async with memory.activation(current,tid):task=asyncio.create_task(late())
        release.set();await task
        entered=asyncio.Event();cleaned=asyncio.Event()
        async def pending():
            try:
                async with memory.activation(current,tid):
                    entered.set();await asyncio.Event().wait()
            finally:cleaned.set()
        task=asyncio.create_task(pending());await entered.wait();await memory.close()
        assert task.done() and task.cancelled() and cleaned.is_set()


@pytest.mark.asyncio
async def test_interview_default_does_not_require_extractor(tmp_path):
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,*MEMORY_SCHEMA)) as store:
        threads=ThreadStore(store);memory=Memory(store,threads,embed=Embed(),client=Client(),recall_query=lambda s:'question')
        current=session('a');interview=threads.open('a',current.moment,'interview')
        async with memory.activation(current,interview):
            await memory.after_activation(current,interview,SimpleNamespace(status='completed',value={}),through=0)
        decision=threads.open('a',current.moment,'decision')
        with pytest.raises(ValueError,match='extract'):
            async with memory.activation(current,decision):pass
        await memory.close()


@pytest.mark.asyncio
async def test_real_driver_selective_write_shares_one_service(tmp_path):
    extracted=[]
    async def extract(actor,tid,messages,*,metadata):
        extracted.append((actor,tid,messages))
        return [{'content':'仅由选中主体提取的完整经历','importance':3}]
    class Provider:
        async def request(self,*args):return {'role':'assistant','content':'完整决定','finish_reason':'stop'}
    with StageStore.create(tmp_path/'run',(*THREAD_SCHEMA,*MEMORY_SCHEMA)) as store:
        threads=ThreadStore(store)
        memory=Memory(store,threads,embed=Embed(),client=Client(),extract=extract,
            policy_selector=lambda s:MemoryActivation(MemoryPolicy(False,s.actor.id=='b',False),2))
        driver=LLMDriver(TypedScriptProvider(Provider(), threads),threads,input_builder=lambda s:[{'role':'system','content':'完整主体背景'}],extensions=(MemoryExtension(memory),))
        await asyncio.gather(driver.run(session('a',driver)),driver.run(session('b',driver)))
        assert len(extracted)==1 and extracted[0][0]=='b'
        assert extracted[0][2][0]['content']=='完整主体背景'
        assert extracted[0][2][-1]['content']=='完整决定'
        store.complete(1)
        assert memory._operations=={} and memory._activation.get() is None
        await memory.close()


def test_activation_selection_requires_immutable_policy():
    with pytest.raises(TypeError,match='MemoryPolicy'):
        MemoryActivation(SimpleNamespace(auto_recall=True,auto_write=False,active_tools=False),1)
