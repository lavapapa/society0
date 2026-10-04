"""真实验收共享输入按任务提供能力说明，行动目标仍由原任务决定。"""
from tests.primary.scripted_provider import bind_scripted_request
import json
import pytest
from society0.kernel.llm import LLMPolicy
from society0.kernel.models import ModelProvider
from society0.kernel.runner import run_plan
from tests.e2e.core_next_real_support import plan

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['confirm','action'])
async def test_actual_cognitive_input_explains_optional_discovery_without_rewriting_goal(tmp_path,monkeypatch,kind):
    goal='本次未完成步骤的新观察：尚未发布的报价600元。请确认。' if kind=='confirm' else '向当前配对伙伴发送完整消息“共同讨论产业预期”。'
    calls=[]
    async def request(self,tid,options):
        messages=self.threads.read_messages(tid);calls.append(messages)
        system=messages[0]['content']
        assert '按当前目标需要' in system and '从根路径 /' in system
        assert 'total=0' in system and '无可访问的信息挂载' in system
        assert '先用 data_list' not in system
        assert any(goal in m.get('content','') for m in messages)
        if kind=='confirm':return {'role':'assistant','content':'已确认该观察。','finish_reason':'stop'}
        target={'namespace':'chat','kind':'participants','key':'a'}
        tools=[m for m in messages if m['role']=='tool']
        if not tools:name='action_find';args={'target':target,'query':'','limit':20,'cursor':None}
        elif len(tools)==1:
            action=json.loads(tools[-1]['content'])['items'][0]['name']
            name='action_describe';args={'target':target,'name':action}
        else:
            action=json.loads(tools[-1]['content'])['name']
            name='action_invoke';args={'target':target,'name':action,'arguments':{'content':'共同讨论产业预期'}}
        return {'role':'assistant','content':None,'finish_reason':'tool_calls','tool_calls':[{'id':str(len(calls)),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
    bind_scripted_request(monkeypatch, ModelProvider, request)
    ep={'id':'stub','base_url':'http://127.0.0.1:1/v1','api_key':'stub','model':'stub','concurrency':1}
    cfg={'release':'offline-context-contract','llm':{'endpoints':[ep]},'embed':{'endpoints':[ep],'dimensions':4}}
    policy=LLMPolicy(max_turns=8,max_action_calls=4,completion_names=('chat.send_message_to_partner',) if kind=='action' else ())
    current,held=plan(cfg,tmp_path/'run',goals=goal,policy=policy,mechanism='round' if kind=='action' else 'none',actors=('a','b') if kind=='action' else ('a',))
    if kind=='action':
        # 仅检查首主体的实际发现→描述→执行；第二主体使用正常自然完成响应。
        original=request
        async def first_only(self,tid,options):
            if self.threads.describe(tid)['actor']=='b':return {'role':'assistant','content':'已收到。','finish_reason':'stop'}
            return await original(self,tid,options)
        bind_scripted_request(monkeypatch, ModelProvider, first_only)
    receipt=await run_plan(tmp_path/'run',current)
    assert receipt['complete_step']==1
    assert len(calls)==(1 if kind=='confirm' else 3)
