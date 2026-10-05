"""重复只读事实的显式温度策略，无额外模型请求或原文缩减。"""
import json
import pytest
from society0.kernel.llm import LLMPolicy
from society0.kernel.interaction import Action,ActionResult
from tests.primary.test_kernel_llm import setup,reply,invoke


@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[False,True])
async def test_repeated_facts_temperature_streak_new_fact_write_and_noop(tmp_path,enabled):
    names=['read','read','read','new','new','noop','new','write','new','raw','raw']
    policy=LLMPolicy(max_turns=12,request_options={'temperature':0.1},
        repeated_read_temperature_delta=0.2 if enabled else None,repeated_read_temperature_max=0.4)
    store,threads,provider,driver,session,calls=setup(tmp_path,
        [reply(invoke(str(i),name=name)) for i,name in enumerate(names)]+[reply(text='done')],policy=policy)
    fact=lambda key:{'namespace':'m','kind':'fact','key':key}
    for name,facts in [('read',[fact('1')]),('new',[fact('2')]),('raw',[])]:
        session.actions.actions.register(Action(name,('m','job'),name,{},
            lambda *a,facts=facts:ActionResult('completed',{'text':'完整原文🙂','facts':facts}),read_only=True))
    for name,changed in [('write',True),('noop',False)]:
        session.actions.actions.register(Action(name,('m','job'),name,{},lambda *a,changed=changed:ActionResult('completed',{'changed':changed})))
    with store:
        assert (await driver.run(session)).status=='completed'
        expected=[.1,.1,.3,.4,.1,.3,.1,.3,.1,.1,.1,.1] if enabled else [.1]*12
        assert [r[1]['temperature'] for r in provider.requests]==expected
        assert policy.request_options=={'temperature':.1}
        tid=session.cursors['thread_id']
        messages=threads.read_messages(tid)
        for m in messages:
            if m.get('role')=='tool' and m['tool_call_id'] not in ('5','7'):
                assert json.loads(m['content'])['result']['value']['text']=='完整原文🙂'
        events=threads.tail(tid)['items']
        rows=[threads.read_request(tid,e['seq']) for e in events if e['kind']=='request']
        assert [r['provider_options']['temperature'] for r in rows]==expected
        notes=[e['payload'] for e in events if e['kind']=='provider_repeated_read_diversification']
        assert [n['streak'] for n in notes]==([1,2,1,1] if enabled else [])
        for left,right in zip(provider.requests,provider.requests[1:]):assert right[2][:len(left[2])]==left[2]


@pytest.mark.asyncio
async def test_read_temperature_resets_on_new_activation(tmp_path):
    policy=LLMPolicy(request_options={'temperature':.1},repeated_read_temperature_delta=.2)
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(invoke('1','read')),reply(invoke('2','read')),reply(text='one'),reply(text='two')],policy=policy)
    session.actions.actions.register(Action('read',('m','job'),'read',{},lambda *a:ActionResult('completed',{'facts':[{'namespace':'m','kind':'fact','key':'x'}]}),read_only=True))
    with store:
        assert (await driver.run(session)).status=='completed'
        assert (await driver.run(session)).status=='completed'
        assert [r[1]['temperature'] for r in provider.requests]==[.1,.1,.3,.1]
