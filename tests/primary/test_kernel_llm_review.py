"""LLM 循环的非作者恢复与取消验收。"""
import asyncio
import json
from types import SimpleNamespace
import pytest
from society0.kernel.interaction import Action, ActionResult, Actions, Information, InteractionScope, Moment
from society0.kernel.runtime import Actor, Session
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from society0.kernel.llm import LLMDriver


class Provider:
    def __init__(self, replies):
        self.replies=iter(replies)
        self.requests=0
    async def request(self, tid, options):
        self.requests+=1
        return next(self.replies)


def session(driver, actions):
    scope=InteractionScope('alice',Moment(1,'trade'))
    return Session(Actor('alice',driver),scope,Information(lambda *a:True).bound(scope),
                   actions.bound(scope),{},None,(),None)


@pytest.mark.asyncio
async def test_review_replayed_terminal_receipt_ends_without_another_model_request(tmp_path):
    call={'id':'terminal1','type':'function','function':{'name':'action_invoke','arguments':json.dumps({
        'name':'finish','target':{'namespace':'m','kind':'job','key':'one'},'arguments':'{}'})}}
    reply={'role':'assistant','content':'','tool_calls':[call],'finish_reason':'tool_calls'}
    actions=Actions(lambda *a:True)
    executed=[]
    def finish(*a):
        executed.append(1)
        return ActionResult('completed', {'original':'done'})
    actions.register(Action('finish',('m','job'),'finish',{},finish,terminal=True))
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        first=LLMDriver(Provider([reply]),ThreadStore(store),input_builder=lambda s:[])
        assert (await first.run(session(first,actions))).reason=='terminal_action'
        store.complete(1)
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        provider=Provider([reply,{'role':'assistant','content':'unnecessary closing','finish_reason':'stop'}])
        second=LLMDriver(provider,ThreadStore(restored),input_builder=lambda s:[])
        result=await second.run(session(second,actions))
        assert executed==[1]
        assert provider.requests==1
        assert result.reason=='terminal_action'


@pytest.mark.asyncio
async def test_review_cancelled_domain_call_is_failed_evidence_without_success_receipt(tmp_path):
    started=asyncio.Event()
    cleaned=asyncio.Event()
    async def pending(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
    actions=Actions(lambda *a:True)
    actions.register(Action('pending',('m','job'),'wait',{},pending,terminal=True))
    call={'id':'call','type':'function','function':{'name':'action_invoke','arguments':json.dumps({
        'name':'pending','target':{'namespace':'m','kind':'job','key':'one'},'arguments':'{}'})}}
    response={'role':'assistant','content':'','tool_calls':[call],'finish_reason':'tool_calls'}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        driver=LLMDriver(Provider([response]),threads,input_builder=lambda s:[])
        current=session(driver,actions)
        task=asyncio.create_task(driver.run(current))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert cleaned.is_set()
        tid=current.cursors['thread_id']
        assert threads.describe(tid)['status']=='incomplete'
        assert threads.get_tool_result(tid,'call') is None
        events=threads.tail(tid)['items']
        assert [e['payload']['error_type'] for e in events if e['kind']=='action_error']==['CancelledError']
        assert not [e for e in events if e['kind']=='action_result']
