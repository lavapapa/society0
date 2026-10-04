"""默认对象协议与显式 strict 字符串协议共享领域权限及账本。"""
import json
from types import SimpleNamespace
import pytest
from jsonschema import validate, ValidationError
from society0.kernel.llm import LLMDriver, LLMPolicy
from tests.primary.test_kernel_llm import setup, reply, call

TARGET={'namespace':'m','kind':'job','key':'1'}
FIELDS={'action_invoke':'arguments','data_query':'query','action_find':'cursor','data_list':'cursor'}

@pytest.mark.parametrize('strict',[False,True])
def test_dynamic_schema_has_one_explicit_format(strict):
    tools=LLMDriver(None,None,input_builder=None,policy=LLMPolicy(strict_tools=strict))._tools()
    for tool in tools:
        definition=tool['function'];assert definition['strict'] is strict
        name=definition['name']
        if name not in FIELDS:continue
        schema=definition['parameters']['properties'][FIELDS[name]]
        value={'嵌套':{'array':[1,None,'正文🙂']}}
        validate(json.dumps(value) if strict else value,schema)
        if strict or name!='data_list':
            with pytest.raises(ValidationError):validate(value if strict else json.dumps(value),schema)
        if FIELDS[name]=='cursor':validate(None,schema)

@pytest.mark.asyncio
@pytest.mark.parametrize('strict',[False,True])
async def test_action_native_payload_reaches_ledger_once_and_wrong_format_is_feedback(tmp_path,strict):
    payload={'嵌套':{'array':[1,None,'正文🙂']}}
    encoded=json.dumps(payload,ensure_ascii=False)
    wrong=payload if strict else encoded
    good=encoded if strict else payload
    events=[reply(call('bad','action_invoke',{'name':'work','target':TARGET,'arguments':wrong})),
            reply(call('good','action_invoke',{'name':'work','target':TARGET,'arguments':good}))]
    store,threads,provider,driver,session,calls=setup(tmp_path,events,terminal=True,policy=LLMPolicy(strict_tools=strict,max_turns=2,max_action_calls=1))
    with store:
        result=await driver.run(session)
        assert result.status=='completed' and calls==[(payload,'good')]
        feedback=json.loads(threads.get_tool_result(result.value['thread_id'],'bad')['content'])
        assert feedback['details'][0]['path']==['arguments']
        assert feedback['details'][0]['expected']==('string' if strict else 'object')
        assert len(provider.requests)==2

@pytest.mark.asyncio
@pytest.mark.parametrize('strict',[False,True])
async def test_query_and_cursors_reach_consumers_without_reencoding(strict):
    driver=LLMDriver(None,None,input_builder=None,policy=LLMPolicy(strict_tools=strict))
    seen=[]
    async def consume(*args,**kwargs):seen.append((args,kwargs));return {'ok':True}
    session=SimpleNamespace(information=SimpleNamespace(list=consume,query=consume))
    ledger=SimpleNamespace(find=consume)
    cursor={'identity':'完整标识','cursor':{'offset':3,'details':['🙂',1]}}
    wire=lambda value:json.dumps(value,ensure_ascii=False) if strict else value
    await driver._dispatch('action_find',{'target':TARGET,'query':'','limit':2,'cursor':wire(cursor)},session,ledger,None)
    await driver._dispatch('data_list',{'path':'/data','limit':2,'cursor':wire(cursor)},session,ledger,None)
    await driver._dispatch('data_query',{'path':'/data','query':wire({'limit':2,'cursor':cursor})},session,ledger,None)
    assert seen[0][1]['cursor']==seen[1][1]['cursor']==seen[2][0][1].cursor==cursor
    assert seen[2][0][1].limit==2
    await driver._dispatch('data_query',{'path':'/data','query':wire({'cursor':7})},session,ledger,None)
    assert seen[-1][0][1].cursor==7

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['openai','openai-responses','siwc'])
@pytest.mark.parametrize('strict',[False,True])
async def test_sdk_wire_retains_dynamic_schema_and_explicit_strict(tmp_path,kind,strict):
    from society0.kernel.models import ModelProvider
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
    from tests.primary.provider_http import bind_chat
    from tests.primary.test_kernel_provider_fields import bind_responses
    sent=[]
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store);tid=threads.open('a',0,'decision')
        threads.append_message(tid,{'role':'user','content':'完整输入'})
        provider=ModelProvider([{'id':'offline','api_key':'unused','base_url':'http://unused.invalid/v1',
            'model':'gpt-5.3-codex','provider_type':kind,'concurrency':1,'trust_env':False,
            'credentials_directory':tmp_path/'unused-account'}],threads,max_attempts=1)
        if kind=='openai':
            async def respond(**request):sent.append(request);return {'content':'完成'}
            await bind_chat(provider,respond)
        else:await bind_responses(provider,wire=sent)
        try:
            tools=LLMDriver(None,None,input_builder=None,policy=LLMPolicy(strict_tools=strict))._tools()
            await provider.request(tid,{'tools':tools})
            wire=sent[0]['tools']
            if kind=='siwc':wire=wire[0]['tools']
            if kind=='openai':wire=[item['function'] for item in wire]
            for tool in wire:
                # Chat SDK省略False，服务端默认非严格；Responses与SIWC显式保留。
                assert tool.get('strict',False) is strict
                if kind!='openai':assert tool['strict'] is strict
                if tool['name'] not in FIELDS:continue
                field=tool['parameters']['properties'][FIELDS[tool['name']]]
                value={'任意动态字段':{'多层':[1,'🙂',None]}}
                validate(json.dumps(value) if strict else value,field)
                if strict or tool['name']!='data_list':
                    with pytest.raises(ValidationError):validate(value if strict else json.dumps(value),field)
        finally:await provider.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('strict',[False,True])
@pytest.mark.parametrize('cursor',[3,'opaque-token',[1,'🙂'],False])
async def test_data_list_preserves_provider_owned_scalar_or_sequence_cursor(tmp_path,strict,cursor):
    from society0.kernel.interaction import Page, Ref
    seen=[]
    class Rows:
        def ref(self,path):return Ref('data','rows',path)
        def list(self,scope,path,*,limit,cursor):
            seen.append(cursor)
            return Page([{'value':'完整行'}],10,None,'v1')
    encoded=json.dumps(cursor) if strict else cursor
    store,threads,provider,driver,session,calls=setup(tmp_path,
        [reply(call('page','data_list',{'path':'/rows','limit':1,'cursor':encoded})),reply(text='完成')],
        policy=LLMPolicy(strict_tools=strict))
    session.information.information.mount('/rows',Rows())
    with store:
        assert (await driver.run(session)).status=='completed'
        assert seen==[cursor]
        receipt=json.loads(threads.get_tool_result(session.cursors['thread_id'],'page')['content'])
        assert receipt['items']==[{'value':'完整行'}] and receipt['total']==10
