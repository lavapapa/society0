"""提供方工具调用扩展必须沿消息原样往返。"""
import pytest
from openai.types.chat import ChatCompletion
from society0.kernel.models import ModelProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA


@pytest.mark.asyncio
async def test_tool_call_extensions_survive_normalized_response_and_next_request(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'call a tool'})
        provider=ModelProvider([{'id':'p','api_key':'unused','base_url':'http://unused.invalid/v1','model':'m','concurrency':1,'trust_env':False}],threads,max_attempts=1)
        extension={'google':{'thought_signature':'opaque-provider-data'}}
        tool={'id':'call1','type':'function','function':{'name':'read','arguments':'{}'},'extra_content':extension}
        requests=[]
        async def create(**kwargs):
            requests.append(kwargs)
            message={'role':'assistant','content':None,'tool_calls':[tool]} if len(requests)==1 else {'role':'assistant','content':'done'}
            return ChatCompletion.model_validate({'id':'completion','created':0,'model':'m','object':'chat.completion',
                'choices':[{'index':0,'message':message,'finish_reason':'tool_calls' if len(requests)==1 else 'stop'}]})
        provider.manager.clients['p'].chat.completions.create=create
        try:
            first=await provider.request(tid,{})
            assert first['tool_calls'][0]['extra_content']==extension
            threads.append_message(tid,{key:value for key,value in first.items() if key!='finish_reason'})
            threads.append_message(tid,{'role':'tool','tool_call_id':'call1','content':'original result'})
            await provider.request(tid,{})
            assert requests[1]['messages'][1]['tool_calls'][0]==tool
        finally:await provider.close()
