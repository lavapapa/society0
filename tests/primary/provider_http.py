"""离线传输替身：真实 SDK 编码请求、消费 SSE 并转换低层消息。"""
import json
import httpx2


def chat_http_response(value):
    chunks=[]
    for choice in value['choices']:
        message=dict(choice['message'])
        if message.get('tool_calls'):
            message['tool_calls']=[{'index':index,**call} for index,call in enumerate(message['tool_calls'])]
        common={'id':value['id'],'object':'chat.completion.chunk','created':value['created'],'model':value['model']}
        chunks.append({**common,'choices':[{'index':choice['index'],'delta':message,'finish_reason':None}]})
        chunks.append({**common,'choices':[{'index':choice['index'],'delta':{},'finish_reason':choice.get('finish_reason')}]})
    if value.get('usage') is not None:chunks.append({**common,'choices':[],'usage':value['usage']})
    body=''.join('data: '+json.dumps(chunk,ensure_ascii=False)+'\n\n' for chunk in chunks)+'data: [DONE]\n\n'
    return httpx2.Response(200,headers={'content-type':'text/event-stream'},text=body)


async def bind_chat(provider, create, *, endpoint=0):
    await provider._start()
    selected=provider.endpoints[endpoint]
    async def respond(request):
        wire=json.loads(request.content)
        response=await create(**wire)
        if isinstance(response,httpx2.Response):return response
        value=response.model_dump(mode='json') if hasattr(response,'model_dump') else response
        if 'choices' not in value:
            value={'id':'fixture','object':'chat.completion','created':0,'model':selected.model_name,
                   'choices':[{'index':0,'finish_reason':value.get('finish_reason','stop'),
                               'message':{'role':'assistant','content':value.get('content')}}]}
        if not wire.get('stream'):return httpx2.Response(200,json=value)
        return chat_http_response(value)
    selected.http._transport=httpx2.MockTransport(respond)


async def bind_embedding(provider, create, *, endpoint=0):
    await provider._start()
    selected=provider.endpoints[endpoint]
    async def respond(request):
        response=await create(**json.loads(request.content))
        if isinstance(response,httpx2.Response):return response
        return httpx2.Response(200,json=response.model_dump(mode='json') if hasattr(response,'model_dump') else response)
    selected.http._transport=httpx2.MockTransport(respond)


def count_dataset_frames(monkeypatch):
    """计数实际原生帧解压，保持 SQLite 定位与真实 codec。"""
    import society0.kernel.datasets as native
    calls=[]; original=native.decode_chunk
    def counted(body):
        calls.append(len(body))
        return original(body)
    monkeypatch.setattr(native,'decode_chunk',counted)
    return calls
