"""旧提供方策略证据通过新版真实适配器继续保存。"""
import copy
import pytest
from openai.types.chat import ChatCompletion
from society0.kernel.models import ModelProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA


@pytest.mark.asyncio
async def test_auto_restrict_keeps_requested_and_effective_choice_with_thread_request(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'decision')
        threads.append_message(tid,{'role':'user','content':'act'})
        provider=ModelProvider([{'id':'m','model':'m','api_key':'unused','base_url':'http://unused.invalid/v1',
            'concurrency':1,'trust_env':False,'tool_choice_policy':'auto_restrict'}],threads,max_attempts=1)
        captured=[]
        async def create(**kwargs):
            captured.append(kwargs)
            return ChatCompletion.model_validate({'id':'response','object':'chat.completion','created':0,'model':'m',
                'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','tool_calls':[
                    {'id':'c','type':'function','function':{'name':'required_action','arguments':'{}'}}]}}]})
        provider.manager.clients['m'].chat.completions.create=create
        options={'tools':[{'type':'function','function':{'name':name,'parameters':{'type':'object','properties':{}}}}
            for name in ('optional_action','required_action')],
            'tool_choice':{'type':'function','function':{'name':'required_action'}}}
        original=copy.deepcopy(options)
        try:await provider.request(tid,options)
        finally:await provider.close()
        assert options==original
        assert captured[0]['tool_choice']=='auto'
        assert [tool['function']['name'] for tool in captured[0]['tools']]==['required_action']
        request=next(item for item in threads.tail(tid)['items'] if item['kind']=='request')
        assert request['payload']['tool_choice_resolution']=={
            'policy':'auto_restrict','requested':original['tool_choice'],'effective':'auto',
            'selected_tool_name':'required_action','tools_filtered':True,
            'original_tools_count':2,'effective_tools_count':1}
