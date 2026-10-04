"""真实公开 SDK，合成 SSE；阻断 socket，不读取任何凭据。"""
import asyncio, json, socket
import httpx2
from openai import AsyncOpenAI
from pydantic_ai.messages import ModelRequest, SystemPromptPart, ToolReturnPart, ModelMessagesTypeAdapter, ToolCallPart, ThinkingPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openai_codex import OpenAICodexProvider
from pydantic_ai.tools import ToolDefinition

def deny(*a, **kw): raise AssertionError('network disabled')
socket.socket.connect = deny
call={'id':'fc_1','type':'function_call','name':'settle','call_id':'call_1','arguments':'{"amount":7}', 'status':'completed','namespace':'society'}
reason={'id':'rs_1','type':'reasoning','summary':[],'encrypted_content':'synthetic-opaque-signature'}
base={'id':'resp_1','object':'response','created_at':0,'model':'gpt-5.3-codex','output':[reason,call],'usage':{'input_tokens':3,'output_tokens':4,'total_tokens':7}}
async def main():
    mode='completed'; wire=[]
    def transport(request):
        wire.append(json.loads(request.content))
        events=[{'type':'response.created','response':dict(base,status='in_progress',output=[]),'sequence_number':0}]
        for i,item in enumerate(base['output']):
            events += [{'type':'response.output_item.added','output_index':i,'item':item,'sequence_number':i*2+1},{'type':'response.output_item.done','output_index':i,'item':item,'sequence_number':i*2+2}]
        if mode=='error': events.append({'type':'error','code':'server_error','message':'synthetic stream failure','param':None,'sequence_number':8})
        elif mode!='eof':
            response=dict(base,status=mode)
            if mode=='incomplete':response['incomplete_details']={'reason':'max_output_tokens'}
            if mode=='failed':response['error']={'code':'server_error','message':'synthetic failed response'}
            events.append({'type':'response.'+mode,'response':response,'sequence_number':9})
        return httpx2.Response(200,headers={'content-type':'text/event-stream'},content=''.join('data: '+json.dumps(x)+'\n\n' for x in events).encode())
    client=httpx2.AsyncClient(transport=httpx2.MockTransport(transport))
    sdk=AsyncOpenAI(api_key='synthetic',http_client=client,max_retries=0)
    profile=dict(OpenAICodexProvider.model_profile('gpt-5.3-codex'));profile['openai_system_prompt_role']='developer'
    model=OpenAIResponsesModel('gpt-5.3-codex',provider=OpenAIProvider(openai_client=sdk),profile=profile)
    params=ModelRequestParameters(function_tools=[ToolDefinition(name='settle',parameters_json_schema={'type':'object','properties':{'amount':{'type':'integer'}},'required':['amount']})])
    settings={'parallel_tool_calls':False,'extra_body':{'tools':[{'type':'namespace','name':'society','description':'domain','tools':[{'type':'function','name':'settle','parameters':params.function_tools[0].parameters_json_schema}]}]}}
    history=[ModelRequest(parts=[SystemPromptPart(content='完整材料')])]
    rows=[]
    for mode in ('incomplete','failed','error','eof','completed'):
        try:
            result=await model.request(history,settings,params)
            rows.append({'case':mode,'returned':True,'finish_reason':result.finish_reason,'provider_details':result.provider_details,'tool_parts':sum(isinstance(p,ToolCallPart) for p in result.parts)})
        except Exception as exc:rows.append({'case':mode,'returned':False,'exception':type(exc).__name__,'message':str(exc)})
    # 与持久化使用同一 typed JSON 编解码，随后真实 SDK 再发。
    saved=ModelMessagesTypeAdapter.dump_json(history+[result,ModelRequest(parts=[ToolReturnPart(tool_name='settle',content={'accepted':True},tool_call_id='call_1')])])
    restored=ModelMessagesTypeAdapter.validate_json(saved)
    await model.request(restored,settings,params)
    assert any(x.get('encrypted_content')=='synthetic-opaque-signature' for x in wire[-1]['input'])
    assert any(x.get('namespace')=='society' and x.get('arguments')=='{"amount":7}' for x in wire[-1]['input'])
    await sdk.close()
    print(json.dumps({'network':'MockTransport; socket denied','cases':rows,'typed_json_roundtrip_bytes':len(saved),'opaque_after_typed_json_and_resend':True,'namespace_and_arguments_after_typed_json_and_resend':True},ensure_ascii=False,indent=2))
asyncio.run(main())
