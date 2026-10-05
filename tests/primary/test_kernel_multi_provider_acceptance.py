"""各成熟低层SDK的离线HTTP、工具原文续发、物理重试与拥有资源关闭。"""
import base64
import json
import httpx
import httpx2
import pytest
from society0.kernel.models import ModelProvider,EmbeddingProvider,RESOURCE_SCHEMA,ProviderFailure
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore,THREAD_SCHEMA
from tests.primary.provider_http import bind_chat

TOOLS=[{'type':'function','function':{'name':'read','parameters':{'type':'object','properties':{'value':{'type':'integer'}},'required':['value']}}}]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['openai','azure','ollama'])
async def test_chat_compatible_actual_sdk_tool_receipt_retry_and_full_wire(tmp_path,kind):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'decision');threads.append_message(tid,{'role':'user','content':'完整材料🙂'})
        provider=ModelProvider([{'id':'p','model':'compatible-test','provider_type':kind,'api_key':'unused','base_url':'http://unused.invalid/v1','api_version':'2024-02-15-preview','trust_env':False}],threads,max_attempts=2,retry_delay=0)
        sent=[]
        async def create(**wire):
            sent.append(wire)
            if len(sent)==1:return httpx2.Response(429,json={'error':{'message':'temporary'}})
            return {'id':'r','object':'chat.completion','created':0,'model':'compatible-test','choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'call_1','type':'function','function':{'name':'read','arguments':'{"value":7}'}}]}}]}
        await bind_chat(provider,create)
        try:
            result=await provider.request(tid,{'tools':TOOLS,'parallel_tool_calls':False})
            assert result['tool_calls'][0]['function']['arguments']=='{"value":7}'
            assert len(sent)==2 and sent[0]==sent[1] and sent[0]['messages'][0]['content']=='完整材料🙂'
            assert sent[0]['parallel_tool_calls'] is False and sent[0]['tool_choice']=='auto'
            threads.append_message(tid,{'role':'tool','tool_call_id':'call_1','content':'完整回执🙂'})
            await provider.request(tid,{'tools':TOOLS,'parallel_tool_calls':False})
            assert sent[-1]['messages'][-1]['content']=='完整回执🙂'
            events=threads.tail(tid)['items'];assert len([e for e in events if e['kind']=='request'])==3
            assert len([e for e in events if e['kind']=='provider_error'])==1
        finally:await provider.close()
        assert provider.endpoints[0].http.is_closed


def anthropic_response():
    events=[('message_start',{'type':'message_start','message':{'id':'msg_1','type':'message','role':'assistant','content':[],'model':'claude-sonnet-4-6','stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':3,'output_tokens':0}}}),
        ('content_block_start',{'type':'content_block_start','index':0,'content_block':{'type':'thinking','thinking':'','signature':''}}),
        ('content_block_delta',{'type':'content_block_delta','index':0,'delta':{'type':'thinking_delta','thinking':'原始思考'}}),
        ('content_block_delta',{'type':'content_block_delta','index':0,'delta':{'type':'signature_delta','signature':'exact-anthropic-signature'}}),
        ('content_block_stop',{'type':'content_block_stop','index':0}),
        ('content_block_start',{'type':'content_block_start','index':1,'content_block':{'type':'tool_use','id':'call_1','name':'read','input':{}}}),
        ('content_block_delta',{'type':'content_block_delta','index':1,'delta':{'type':'input_json_delta','partial_json':'{"value":7}'}}),
        ('content_block_stop',{'type':'content_block_stop','index':1}),
        ('message_delta',{'type':'message_delta','delta':{'stop_reason':'tool_use','stop_sequence':None},'usage':{'output_tokens':4}}),
        ('message_stop',{'type':'message_stop'})]
    return httpx2.Response(200,headers={'content-type':'text/event-stream'},text=''.join('event: '+kind+'\ndata: '+json.dumps(body)+'\n\n' for kind,body in events))


@pytest.mark.asyncio
async def test_anthropic_actual_sdk_signature_tool_result_and_single_retry(tmp_path):
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'decision');threads.append_message(tid,{'role':'user','content':'完整材料🙂'})
        provider=ModelProvider([{'id':'p','model':'claude-sonnet-4-6','provider_type':'anthropic','api_key':'unused','base_url':'http://unused.invalid','trust_env':False}],threads,max_attempts=2,retry_delay=0)
        await provider._start();sent=[]
        def transport(request):
            sent.append(json.loads(request.content))
            if len(sent)==1:return httpx2.Response(500,json={'type':'error','error':{'type':'api_error','message':'temporary'}})
            return anthropic_response()
        endpoint=provider.endpoints[0];endpoint.http._transport=httpx2.MockTransport(transport)
        try:
            result=await provider.request(tid,{'tools':TOOLS})
            assert len(sent)==2 and sent[0]==sent[1] and result['tool_calls'][0]['function']['name']=='read'
            raw=threads.snapshot_messages(tid,raw=True)['messages'][-1]['model_message']
            assert raw['parts'][0]['signature']=='exact-anthropic-signature'
            threads.append_message(tid,{'role':'tool','tool_call_id':result['tool_calls'][0]['id'],'content':'完整回执🙂'})
            await provider.request(tid,{'tools':TOOLS})
            blocks=[part for message in sent[-1]['messages'] for part in message['content'] if isinstance(message['content'],list)]
            assert any(part.get('signature')=='exact-anthropic-signature' for part in blocks)
            assert any(part.get('type')=='tool_result' and
                       ''.join(item['text'] for item in part['content'] if item['type']=='text')=='完整回执🙂' for part in blocks)
        finally:await provider.close()
        assert endpoint.http.is_closed and endpoint.client.max_retries==0


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['status','transport'])
async def test_google_actual_sdk_signature_embedding_retry_and_both_pools_close(tmp_path,monkeypatch,failure):
    import google.genai
    original=google.genai.Client;sent=[];calls=0;clients=[]
    signature=base64.b64encode(b'exact-google-signature').decode()
    def transport(request):
        nonlocal calls
        calls+=1;wire=json.loads(request.content);sent.append((str(request.url),wire))
        if calls==1:
            if failure=='transport':raise httpx.ConnectError('offline transient')
            return httpx.Response(500,json={'error':{'code':500,'status':'INTERNAL','message':'temporary'}})
        if 'batchEmbedContents' in str(request.url):
            return httpx.Response(200,json={'embeddings':[{'values':[1.,2.]} for _ in wire['requests']]})
        response={'candidates':[{'index':0,'content':{'role':'model','parts':[{'functionCall':{'name':'read','args':{'value':7}},'thoughtSignature':signature}]},'finishReason':'STOP'}],
            'usageMetadata':{'promptTokenCount':3,'candidatesTokenCount':4,'totalTokenCount':7},'modelVersion':'gemini-2.5-pro'}
        return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: '+json.dumps(response)+'\n\n')
    def client(**kwargs):
        kwargs['http_options']=kwargs['http_options'].model_copy(update={'async_client_args':{'transport':httpx.MockTransport(transport)},'client_args':{'transport':httpx.MockTransport(lambda r: (_ for _ in ()).throw(AssertionError('unexpected sync request')))}})
        value=original(**kwargs);clients.append(value);return value
    monkeypatch.setattr(google.genai,'Client',client)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA+RESOURCE_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',1,'decision');threads.append_message(tid,{'role':'user','content':'完整材料🙂'})
        config={'id':'p','model':'gemini-2.5-pro','provider_type':'google','api_key':'unused','concurrency':1}
        provider=ModelProvider([config],threads,max_attempts=2,retry_delay=0)
        try:
            result=await provider.request(tid,{'tools':TOOLS})
            assert calls==2 and sent[0][1]==sent[1][1]
            raw=threads.snapshot_messages(tid,raw=True)['messages'][-1]['model_message']
            assert raw['parts'][0]['provider_details']['thought_signature']==signature
            threads.append_message(tid,{'role':'tool','tool_call_id':result['tool_calls'][0]['id'],'content':'完整回执🙂'})
            await provider.request(tid,{'tools':TOOLS})
            parts=[part for message in sent[-1][1]['contents'] for part in message['parts']]
            assert any(part.get('thoughtSignature')==signature for part in parts)
            assert any(part.get('functionResponse',{}).get('name')=='read' for part in parts)
        finally:await provider.close()
        embedding=EmbeddingProvider([{**config,'model':'gemini-embedding-001'}],store,dimensions=2,max_attempts=1)
        try:assert await embedding.embed(['one','two'],metadata={'actor':'a'})==[[1.,2.],[1.,2.]]
        finally:await embedding.close()
        for value in clients:
            assert value._api_client._httpx_client.is_closed
            assert value._api_client._async_httpx_client.is_closed
