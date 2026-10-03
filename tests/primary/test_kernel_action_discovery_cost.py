"""策略过滤与原生发现合并，单页枚举随模板数线性增长。"""
from dataclasses import asdict
import json
from types import SimpleNamespace
import pytest
from society0.kernel.interaction import Action,Actions,ActionResult,InteractionScope,Moment,Ref
from society0.kernel.llm import LLMPolicy,_Ledger


@pytest.mark.asyncio
@pytest.mark.parametrize('count',[100,10000])
async def test_filtered_discovery_visits_each_template_once_and_has_small_version_cursor(count):
    calls=0
    def available(*args):
        nonlocal calls
        calls+=1
        return True
    actions=Actions(lambda *a:True,revision=lambda scope:('run',1))
    for index in range(count):
        actions.register(Action(f'act{index}',('world','entity'),'action',{'type':'object'},
            lambda *a:ActionResult('completed'),available=available,tags=('selected',)))
    scope=InteractionScope('a',Moment(1,'p'))
    session=SimpleNamespace(actions=actions.bound(scope),actor=SimpleNamespace(id='a'),moment=scope.moment)
    ledger=_Ledger(SimpleNamespace(policy=LLMPolicy(allowed_tags=('selected',))),session,'thread')
    page=await ledger.find(Ref('world','entity','one'),limit=1)
    assert calls==count
    assert page.total==count and len(page.items)==1
    assert len(json.dumps(page.next_cursor))<2048
