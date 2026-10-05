from tests.primary.scripted_provider import TypedScriptProvider
"""标准提取器在物理请求前释放传入视图，自定义提取器仍取得完整原文。"""
import json
import weakref

import pytest

from society0.kernel.memory import ThreadMemoryExtractor
from tests.primary.test_kernel_memory import setup


@pytest.mark.asyncio
@pytest.mark.parametrize('standard', [True, False])
async def test_extraction_snapshot_ownership_matches_consumer(tmp_path, monkeypatch, standard):
    store, threads, thread, memory, _, _ = setup(tmp_path)
    original = [{'role': 'system', 'content': '完整背景'},
                {'role': 'user', 'content': '完整亲身经历🙂' * 10000}]
    for message in original:
        threads.append_message(thread, message)
    references = []
    native = threads.snapshot_messages

    class SnapshotMessages(list):
        pass

    def snapshot(*args, **kwargs):
        value = native(*args, **kwargs)
        messages = SnapshotMessages(value['messages'])
        references.append(weakref.ref(messages))
        return {**value, 'messages': messages}

    monkeypatch.setattr(threads, 'snapshot_messages', snapshot)

    class Provider:
        async def request(self, tid, options):
            assert references[-1]() is None
            assert threads.read_messages(tid)[:len(original)] == original
            return {'role': 'assistant', 'content': None, 'finish_reason': 'tool_calls',
                    'tool_calls': [{'id': 'memory', 'type': 'function', 'function': {
                        'name': 'extract_memories', 'arguments': json.dumps({'memories': []})}}]}

    async def custom(actor, tid, messages, *, metadata):
        assert references[-1]() is messages
        assert messages == original
        return []

    memory.extract = ThreadMemoryExtractor(threads, TypedScriptProvider(Provider(),threads)) if standard else custom
    try:
        job = await memory.extract_job('a', thread, through=threads.describe(thread)['last_seq'], timestamp=1)
        assert job
        assert references[-1]() is None
    finally:
        await memory.close()
        store.close()

@pytest.mark.asyncio
async def test_agent_extraction_borrows_complete_typed_history_and_keeps_tool_receipt(tmp_path,monkeypatch):
    from pydantic_ai.messages import ModelResponse,ToolCallPart
    from society0.kernel.model_messages import encode
    store,threads,thread,memory,_,_=setup(tmp_path)
    original=[{'role':'system','content':'完整背景'},{'role':'user','content':'完整经历🙂'*10000}]
    for item in original:threads.append_message(thread,item)
    snapshots=[];native=threads.snapshot_messages
    def snapshot(*args,**kwargs):snapshots.append(kwargs.get('raw',False));return native(*args,**kwargs)
    monkeypatch.setattr(threads,'snapshot_messages',snapshot)
    class Provider:
        async def request_model(self,tid,options,*,model_messages):
            if callable(model_messages): model_messages=model_messages()
            assert model_messages and '完整经历' in str(model_messages)
            assert options['tool_choice']['function']['name']=='extract_memories'
            response=ModelResponse(parts=[ToolCallPart('extract_memories',{'memories':[{'content':'我记住完整经历','importance':4}]},'extracted')],finish_reason='tool_call')
            seq=threads.append_message(tid,{'model_message':encode(response)})
            return response,seq,None
    memory.extract=ThreadMemoryExtractor(threads,Provider())
    try:
        await memory.extract_job('a',thread,through=threads.describe(thread)['last_seq'],timestamp=1)
        assert snapshots==[False,True]
        assert threads.read_messages(thread)[-1]['role']=='tool'
        assert threads.read_messages(thread)[:2]==original
    finally:await memory.close();store.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('arguments',[
    {'memories':[{'content':'too long'*100,'importance':4}]},
    {'memories':[{'content':'ok','importance':True}]},
    {'memories':[{'content':'ok','importance':4}]*6},
    {'memories':[{'content':'ok','importance':4,'extra':'not allowed'}]},
])
async def test_agent_invalid_memory_output_never_creates_success_job(tmp_path,arguments):
    store,threads,thread,memory,embed,_=setup(tmp_path)
    threads.append_message(thread,{'role':'system','content':'完整背景'})
    calls=[]
    class Provider:
        async def request(self,tid,options):
            calls.append(True)
            return {'role':'assistant','tool_calls':[{'id':str(len(calls)),'type':'function','function':{'name':'extract_memories','arguments':json.dumps(arguments)}}],'finish_reason':'tool_calls'}
    memory.extract=ThreadMemoryExtractor(threads,TypedScriptProvider(Provider(),threads))
    try:
        with pytest.raises(RuntimeError):await memory.extract_job('a',thread,through=threads.describe(thread)['last_seq'],timestamp=1)
        assert len(calls)==2 and embed.calls==[]
        assert store.read(lambda view:view.query('SELECT count(*) FROM memory_jobs'))==[(0,)]
    finally:await memory.close();store.close()

def test_shared_jsonschema_parser_retains_repair_and_rejects_boolean_importance():
    from society0.memory_extraction_protocol import _parse_memories_from_response
    def response(arguments):return {'tool_calls':[{'id':'m','function':{'name':'extract_memories','arguments':arguments}}]}
    memories,identifier,error=_parse_memories_from_response(response("{memories: [{content: '  保留原始语义  ', importance: 4}]}"))
    assert memories==[{'content':'保留原始语义','importance':4.0}] and identifier=='m' and not error
    assert _parse_memories_from_response(response('{"memories":[{"content":"ok","importance":true}]}'))[2]=='invalid_memory_importance'

@pytest.mark.asyncio
async def test_queued_extractions_materialize_typed_history_after_existing_provider_permit(tmp_path,monkeypatch):
    import asyncio
    from society0.kernel.models import ModelProvider
    from tests.primary.provider_http import bind_chat
    store,threads,thread,memory,_,_=setup(tmp_path)
    ids=[thread]+[threads.open('a',index,'decision') for index in (2,3)]
    for tid in ids:
        threads.append_message(tid,{'role':'system','content':'完整背景'})
        threads.append_message(tid,{'role':'user','content':'排队时无需常驻全文🙂'*10000})
    native=threads.snapshot_messages;typed=[]
    def snapshot(*args,**kwargs):
        if kwargs.get('raw'):typed.append(args[0])
        return native(*args,**kwargs)
    monkeypatch.setattr(threads,'snapshot_messages',snapshot)
    provider=ModelProvider([{'id':'offline','model':'m','api_key':'unused','base_url':'http://unused.invalid/v1','trust_env':False,'concurrency':1}],threads,max_attempts=1,request_jitter=0)
    async def answer(**request):return {'id':'fixture','object':'chat.completion','created':0,'model':'m','choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'extracted','type':'function','function':{'name':'extract_memories','arguments':'{"memories":[]}'}}]}}]}
    await bind_chat(provider,answer)
    memory.extract=ThreadMemoryExtractor(threads,provider)
    semaphore=provider.endpoints[0].resources.endpoint
    await semaphore.acquire()
    tasks=[asyncio.create_task(memory.extract_job('a',tid,through=threads.describe(tid)['last_seq'],timestamp=1)) for tid in ids]
    try:
        for _ in range(5):await asyncio.sleep(0)
        assert typed==[]
        tasks[-1].cancel()
        with pytest.raises(asyncio.CancelledError):await tasks[-1]
        assert typed==[]
        semaphore.release()
        await asyncio.gather(*tasks[:-1])
        assert sorted(typed)==sorted(ids[:-1])
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await provider.close();await memory.close();store.close()

@pytest.mark.asyncio
async def test_lazy_agent_history_survives_physical_retry_and_output_correction(tmp_path,monkeypatch):
    import httpx2
    from society0.kernel.models import ModelProvider
    from tests.primary.provider_http import bind_chat
    store,threads,thread,memory,_,_=setup(tmp_path)
    original=[{'role':'system','content':'完整主体背景'},{'role':'user','content':'亲身经历完整原文🙂'*2000}]
    for item in original:threads.append_message(thread,item)
    snapshots=[];native=threads.snapshot_messages
    def snapshot(*args,**kwargs):
        if kwargs.get('raw'):snapshots.append(args[0])
        return native(*args,**kwargs)
    monkeypatch.setattr(threads,'snapshot_messages',snapshot)
    provider=ModelProvider([{'id':'offline','model':'m','api_key':'unused','base_url':'http://unused.invalid/v1','trust_env':False,'concurrency':1}],threads,max_attempts=2,retry_delay=0,request_jitter=0,request_limit=__import__('asyncio').Semaphore(1))
    wire=[]
    async def answer(**request):
        wire.append(request)
        if len(wire)==1:return httpx2.Response(503,json={'error':{'message':'try again'}})
        arguments={'memories':[{'content':'ok','importance':True}]} if len(wire)==2 else {'memories':[{'content':'我保留经验','importance':4}]}
        return {'id':'fixture','object':'chat.completion','created':0,'model':'m','choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','content':None,'tool_calls':[{'id':'extract-'+str(len(wire)),'type':'function','function':{'name':'extract_memories','arguments':json.dumps(arguments)}}]}}]}
    await bind_chat(provider,answer);memory.extract=ThreadMemoryExtractor(threads,provider)
    try:
        await memory.extract_job('a',thread,through=threads.describe(thread)['last_seq'],timestamp=1)
        assert len(wire)==3 and snapshots==[thread]
        for request in wire:
            assert request['messages'][:2]==original
            assert all(item.get('content')!='' for item in request['messages'])
        assert wire[0]['messages']==wire[1]['messages']
        assert any(item['role']=='tool' for item in wire[2]['messages'])
        requests=[item['payload'] for item in threads.tail(thread)['items'] if item['kind']=='request']
        assert len(requests)==3 and requests[0]['through']==requests[1]['through']<requests[2]['through']
    finally:await provider.close();await memory.close();store.close()

@pytest.mark.asyncio
async def test_multiple_output_calls_use_one_correction_and_keep_full_history(tmp_path):
    store,threads,thread,memory,_,_=setup(tmp_path)
    original=[{'role':'system','content':'完整背景'},{'role':'user','content':'完整亲身经历'}]
    for item in original:threads.append_message(thread,item)
    calls=[]
    class Provider:
        async def request(self,tid,options):
            calls.append(threads.read_messages(tid))
            identifiers=['first','extra'] if len(calls)==1 else ['corrected']
            return {'role':'assistant','tool_calls':[{'id':key,'type':'function','function':{'name':'extract_memories','arguments':'{"memories":[]}'}} for key in identifiers],'finish_reason':'tool_calls'}
    memory.extract=ThreadMemoryExtractor(threads,TypedScriptProvider(Provider(),threads))
    try:
        await memory.extract_job('a',thread,through=threads.describe(thread)['last_seq'],timestamp=1)
        assert len(calls)==2 and all(messages[:2]==original for messages in calls)
    finally:await memory.close();store.close()
