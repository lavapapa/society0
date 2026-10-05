"""非作者核对公开搜索说明与实际查询及策略隔离。"""
import pytest
from society0.kernel.interaction import Actions, Action, ActionResult, InteractionScope, Moment, Ref
from society0.kernel.llm import LLMDriver, LLMPolicy


@pytest.mark.asyncio
async def test_review_literal_search_and_empty_cursor_preserve_all_actions():
    actions=Actions(lambda *args:True)
    for name,description in [('Alpha','Write inventory'),('Beta','Read inventory'),('Star','Literal * marker')]:
        actions.register(Action(name,('demo','item'),description,{'type':'object'},lambda *args:ActionResult('completed')))
    scope=InteractionScope('actor',Moment(1,'decision'));target=Ref('demo','item','1')
    found=[];cursor=None
    while True:
        page=await actions.find(scope,target,query='',limit=1,cursor=cursor)
        assert page.total==3
        found.extend(item.name for item in page.items)
        cursor=page.next_cursor
        if cursor is None:break
    assert found==['Alpha','Beta','Star']
    assert {x.name for x in (await actions.find(scope,target,query='INVENTORY')).items}=={'Alpha','Beta'}
    assert [x.name for x in (await actions.find(scope,target,query='*')).items]==['Star']
    assert (await actions.find(scope,target,query='*inventory*')).total==0


def test_review_call_guidance_does_not_mutate_other_policy_schemas():
    def tools(parallel):
        return LLMDriver(None,None,input_builder=lambda session:[],policy=LLMPolicy(parallel_tool_calls=parallel))._tools()
    enabled_before=tools(True);disabled=tools(False);enabled_after=tools(True)
    assert enabled_before==enabled_after
    assert len(disabled)==len(enabled_before)
    for left,right in zip(disabled,enabled_before):
        assert left['function']['parameters']==right['function']['parameters']
        assert left['function']['name']==right['function']['name']
        assert left['function']['description']==right['function']['description']+' Submit at most one tool call per response.'
    query=next(x for x in enabled_before if x['function']['name']=='action_find')['function']['parameters']
    assert set(query['properties'])=={'target','query','limit','cursor'}
