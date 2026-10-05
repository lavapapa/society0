"""已识别 typed opaque 字段经真实 Provider、完整检查点与独立恢复后续发。"""
import json
import httpx2
import pytest
from society0.kernel.models import ModelProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA


def responses_sse(mode='completed',call=None):
    call=call or {'id':'fc_1','type':'function_call','name':'read','call_id':'call_1','arguments':'{"original":7}', 'status':'completed','namespace':'society'}
    reason={'id':'rs_1','type':'reasoning','summary':[],'encrypted_content':'synthetic-opaque-signature'}
    base={'id':'resp_1','object':'response','created_at':0,'model':'gpt-5.3-codex','output':[reason,call],'usage':{'input_tokens':3,'output_tokens':4,'total_tokens':7}}
    events=[{'type':'response.created','response':dict(base,status='in_progress',output=[]),'sequence_number':0}]
    for i,item in enumerate(base['output']):
        events.extend([{'type':'response.output_item.added','output_index':i,'item':item,'sequence_number':i*2+1},{'type':'response.output_item.done','output_index':i,'item':item,'sequence_number':i*2+2}])
    if mode=='error':events.append({'type':'error','code':'server_error','message':'synthetic stream failure','param':None,'sequence_number':8})
    elif mode!='eof':
        response=dict(base,status=mode)
        if mode=='incomplete':response['incomplete_details']={'reason':'max_output_tokens'}
        if mode=='failed':response['error']={'code':'server_error','message':'synthetic failed response'}
        events.append({'type':'response.'+mode,'response':response,'sequence_number':9})
    return httpx2.Response(200,headers={'content-type':'text/event-stream'},content=''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode())


async def bind_responses(provider,*,mode='completed',call=None,wire=None):
    await provider._start()
    endpoint=provider.endpoints[0]
    if endpoint.auth:
        async def token():return 'offline-account-token'
        endpoint.auth.access_token=token
    def respond(request):
        if wire is not None:wire.append(json.loads(request.content))
        return responses_sse(mode,call)
    endpoint.http._transport=httpx2.MockTransport(respond)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['openai-responses','siwc'])
async def test_tool_call_extensions_survive_normalized_response_and_next_request(tmp_path,kind):
    config={'id':'p','api_key':'unused','base_url':'http://unused.invalid/v1','model':'gpt-5.3-codex','provider_type':kind,'concurrency':1,'trust_env':False,'credentials_directory':tmp_path/'no-real-account'}
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'完整材料 call a tool'})
        provider=ModelProvider([config],threads,max_attempts=1)
        await bind_responses(provider)
        try:
            first=await provider.request(tid,{})
            assert first['tool_calls'][0]['function']['arguments']=='{"original":7}'
            raw=threads.snapshot_messages(tid,raw=True)['messages'][-1]['model_message']
            assert raw['parts'][0]['signature']=='synthetic-opaque-signature'
            assert raw['parts'][1]['provider_details']['namespace']=='society'
            threads.append_message(tid,{'role':'tool','tool_call_id':'call_1','content':'original result'})
            store.complete(1)
        finally:await provider.close()
    with StageStore.restore(tmp_path/'run',tmp_path/'restored') as restored:
        threads=ThreadStore(restored);assert threads.snapshot_messages(tid,raw=True)['messages'][-2]['model_message']==raw
        provider=ModelProvider([config],threads,max_attempts=1);sent=[]
        await bind_responses(provider,wire=sent)
        try:
            await provider.request(tid,{})
            items=sent[0]['input']
            assert any(item.get('encrypted_content')=='synthetic-opaque-signature' for item in items)
            assert any(item.get('namespace')=='society' and item.get('arguments')=='{"original":7}' for item in items)
            assert any(item.get('type')=='function_call_output' and item['output']=='original result' for item in items)
        finally:await provider.close()

    # 新解释器从同一完整点恢复；权威 typed 消息和实际 SDK wire 都再校验。
    import asyncio, subprocess, sys
    code = r"""
import asyncio,json,sys
from pathlib import Path
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore
from society0.kernel.models import ModelProvider
from tests.primary.test_kernel_provider_fields import bind_responses
async def main():
    source,target,tid,kind=sys.argv[1:]
    with StageStore.restore(source,target) as store:
        threads=ThreadStore(store)
        raw=threads.snapshot_messages(tid,raw=True)['messages'][-2]['model_message']
        config={'id':'p','api_key':'unused','base_url':'http://unused.invalid/v1','model':'gpt-5.3-codex','provider_type':kind,'concurrency':1,'trust_env':False,'credentials_directory':Path(target)/'no-real-account'}
        provider=ModelProvider([config],threads,max_attempts=1);sent=[]
        await bind_responses(provider,wire=sent)
        try:await provider.request(tid,{})
        finally:await provider.close()
        print(json.dumps({'raw':raw,'input':sent[0]['input']}))
asyncio.run(main())
"""
    child=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',code,str(tmp_path/'run'),
        str(tmp_path/'subprocess-restored'),tid,kind],capture_output=True,text=True,timeout=15,check=True)
    independent=json.loads(child.stdout)
    assert independent['raw']==raw
    items=independent['input']
    assert any(item.get('encrypted_content')=='synthetic-opaque-signature' for item in items)
    assert any(item.get('namespace')=='society' and item.get('arguments')=='{"original":7}' for item in items)
    assert any(item.get('type')=='function_call_output' and item['output']=='original result' for item in items)
