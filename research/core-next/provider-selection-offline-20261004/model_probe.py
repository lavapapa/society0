"""隔离 SDK 试验：所有 HTTP 均由 MockTransport 处理，凭据完全合成。"""
import asyncio, base64, importlib.metadata, json, socket, time
import httpx2
from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, UserPromptPart, ToolReturnPart, ToolCallPart, ThinkingPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai_codex import OpenAICodexModel
from pydantic_ai.providers.openai_codex import OpenAICodexProvider, OpenAICodexCredentials
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from openai import AsyncOpenAI

def no_network(*a, **kw): raise AssertionError('Real network disabled')
socket.socket.connect = no_network

def token(exp):
    # 无签名、无身份的测试字符串；SDK 仅将 exp 当刷新提示。
    return 'mock.' + base64.urlsafe_b64encode(json.dumps({'exp': exp}).encode()).decode().rstrip('=') + '.mock'

async def main():
    records=[]; refreshes=[]
    reasoning={'id':'rs_mock','type':'reasoning','summary':[{'type':'summary_text','text':'完整思考'}],'encrypted_content':'mock-opaque-signature'}
    call={'id':'fc_mock','type':'function_call','name':'settle','call_id':'call_mock','arguments':'{"note":"完整中文", "amount":7}','status':'completed','namespace':'society'}
    response={'id':'resp_mock','object':'response','created_at':0,'status':'completed','model':'gpt-5.3-codex','output':[reasoning,call],'usage':{'input_tokens':5,'output_tokens':5,'total_tokens':10}}
    def transport(req):
        if req.url.host=='auth.openai.com':
            refreshes.append(str(req.url))
            return httpx2.Response(200,json={'access_token':token(int(time.time())+3600),'refresh_token':'new-mock-refresh','token_type':'Bearer','expires_in':3600})
        assert req.url.host in {'chatgpt.com','api.openai.com'}
        body=json.loads(req.content); records.append(body)
        events=[{'type':'response.created','response':{**response,'status':'in_progress','output':[]},'sequence_number':0}]
        for i,item in enumerate(response['output']):
            events.extend([{'type':'response.output_item.added','output_index':i,'item':item,'sequence_number':i*2+1}, {'type':'response.output_item.done','output_index':i,'item':item,'sequence_number':i*2+2}])
        events.append({'type':'response.completed','response':response,'sequence_number':9})
        return httpx2.Response(200,headers={'content-type':'text/event-stream'},content=''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode())
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
        provider=OpenAICodexProvider(credentials=OpenAICodexCredentials(access_token=token(0),refresh_token='mock-refresh',account_id='mock-account'),http_client=client)
        provider.client.max_retries=0
        model=OpenAICodexModel('gpt-5.3-codex',provider=provider)
        params=ModelRequestParameters(function_tools=[ToolDefinition(name='settle',parameters_json_schema={'type':'object','properties':{'note':{'type':'string'},'amount':{'type':'integer'}},'required':['note','amount']})])
        history=[ModelRequest(parts=[SystemPromptPart(content='完整系统材料'),UserPromptPart(content='执行一次')],conversation_id='simulation-thread')]
        result=await model.request(history,{'temperature':.7,'max_tokens':100,'parallel_tool_calls':False},params)
        history += [result,ModelRequest(parts=[ToolReturnPart(tool_name='settle',content={'accepted':True},tool_call_id='call_mock')])]
        await model.request(history,None,params)
        assert len(refreshes)==1
        assert records[0]['store'] is False and records[0]['stream'] is True
        assert 'temperature' not in records[0] and 'max_output_tokens' not in records[0]
        assert any(isinstance(p,ToolCallPart) and p.args_as_dict()=={'note':'完整中文','amount':7} for p in result.parts)
        assert any(isinstance(p,ThinkingPart) and p.signature=='mock-opaque-signature' for p in result.parts)
        assert 'mock-opaque-signature' in json.dumps(records[1])
        issues=[]
        if any(t['type']=='function' for t in records[0]['tools']):issues.append('bare_top_level_function_requires_SIWC_namespace_mapping')
        if any(x.get('role')=='system' for x in records[0]['input']):issues.append('explicit_system_message_rejected_by_SIWC')
        codex_records = list(records)
        records.clear()
        sdk = AsyncOpenAI(api_key='synthetic-siwc-token',base_url='https://api.openai.com/v1',http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(transport)),max_retries=0)
        direct_provider = OpenAIProvider(openai_client=sdk)
        profile = dict(OpenAICodexProvider.model_profile('gpt-5.3-codex'))
        profile['openai_system_prompt_role'] = 'developer'
        direct_model = OpenAIResponsesModel('gpt-5.3-codex',provider=direct_provider,profile=profile)
        namespaced = [{'type':'namespace','name':'society','description':'Society0 domain actions','tools':codex_records[0]['tools']}]
        direct_settings = {'extra_body': {'tools': namespaced},'parallel_tool_calls':False}
        siwc_history = [history[0]]
        r = await direct_model.request(siwc_history,direct_settings,params)
        siwc_history += [r,history[-1]]
        await direct_model.request(siwc_history,direct_settings,params)
        assert all(x['role'] != 'system' for x in records[0]['input'] if 'role' in x)
        assert records[0]['tools'][0]['type']=='namespace'
        assert records[1]['input'][3]['namespace']=='society'
        assert all('previous_response_id' not in b and b['stream'] and b['store'] is False for b in records)
        await sdk.close()
        print(json.dumps({'network':'MockTransport only; socket connect blocked','packages':{d.metadata['Name']:d.version for d in importlib.metadata.distributions()},'request_count':len(records),'synthetic_refresh_count':len(refreshes),'tool_arguments_roundtrip':True,'thinking_signature_roundtrip':True,'sdk_retries_explicitly_disabled':True,'SIWC_mismatches':issues,'codex_bodies':codex_records,'SIWC_offline_adapter_bodies':records,'SIWC_tool_namespace_roundtrip':True},ensure_ascii=False,indent=2))
asyncio.run(main())
