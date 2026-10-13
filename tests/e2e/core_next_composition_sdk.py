"""公开组合的真实 SDK 发送夹具；离线流与获授权真实部署共用捕获边界。"""
from contextvars import ContextVar
from dataclasses import replace
import json
import uuid

from pydantic_ai.messages import ModelMessagesTypeAdapter
from society0.kernel.models import model_plugin
from society0.kernel.plugins import Plugin
from examples.core_next.composed_cognition import ScriptedProvider, SCHEMA, build as composed_plan, encoded
from tests.primary.provider_http import chat_http_response

SDK_SCHEMA=('CREATE TABLE composition_sdk(id INTEGER PRIMARY KEY,thread_id TEXT NOT NULL,request_seq INTEGER NOT NULL,physical_id TEXT NOT NULL,sdk BLOB NOT NULL,wire BLOB)',)


class SDKCapture(ScriptedProvider):
    def __init__(self,store,threads,config,delegate):
        super().__init__(store,threads,config)
        self.delegate=delegate
        self.pending=ContextVar('sdk-request')

    async def request_model(self,thread_id,options,*,model_messages=None):
        current=self.current.get()
        def load():
            messages=model_messages() if callable(model_messages) else model_messages
            self.pending.set({'thread_id':thread_id,'current':current,'options':options,
                              'typed':ModelMessagesTypeAdapter.dump_python(messages,mode='json')})
            return messages
        return await self.delegate.request_model(thread_id,options,model_messages=load)

    def record(self,kwargs):
        from openai import NOT_GIVEN, omit
        request=self.pending.get()
        thread=request['thread_id']
        sequence=self.threads.describe(thread)['last_seq']
        event=self.threads.read_request(thread,sequence)
        current=request['current']
        sdk={key:value for key,value in kwargs.items() if value is not NOT_GIVEN and value is not omit}
        def write(writer):
            writer.execute('INSERT INTO composition_requests(step,actor,thread_id,request_seq,through,typed,options) VALUES(?,?,?,?,?,?,?)',
                (current.step,current.actor.id,thread,sequence,event['through'],encoded(request['typed']),encoded(request['options'])))
            return writer.query('INSERT INTO composition_sdk(thread_id,request_seq,physical_id,sdk) VALUES(?,?,?,?) RETURNING id',
                (thread,sequence,event['physical_request_id'],encoded(sdk)))[0][0]
        request['sdk_id']=self.store.transaction(write)
        return request


def build_with_profile(config,profile,*,offline):
    plan=composed_plan(config)
    configuration=plan.contract.configuration
    def install(ctx):
        provider=SDKCapture(ctx.require('storage','store'),ctx.require('threads','threads'),configuration,
                            ctx.require('models','models')['main'])
        for endpoint in provider.delegate.endpoints:
            original_start=endpoint.start
            async def start(endpoint=endpoint,original_start=original_start):
                await original_start()
                api=endpoint.client.responses if endpoint.kind in ('openai-responses','siwc','openai-codex') else endpoint.client.chat.completions
                original_create=api.create
                async def create(**kwargs):
                    provider.record(kwargs)
                    return await original_create(**kwargs)
                api.create=create
                if offline:
                    import httpx2
                    async def respond(request):
                        pending=provider.pending.get()
                        wire=json.loads(request.content)
                        provider.store.transaction(lambda writer:writer.execute('UPDATE composition_sdk SET wire=? WHERE id=?',
                            (encoded(wire),pending['sdk_id'])))
                        message={'role':'assistant','content':'确定性完整决定🙂'}
                        finish='stop'
                        if configuration['variant']=='interview':
                            message={'role':'assistant','content':None,'tool_calls':[{'id':uuid.uuid4().hex,'type':'function',
                                'function':{'name':'submit_result','arguments':json.dumps({'measurement':'完整测量🙂'},ensure_ascii=False)}}]}
                            finish='tool_calls'
                        return chat_http_response({'id':uuid.uuid4().hex,'created':0,'model':endpoint.model_name,
                            'choices':[{'index':0,'finish_reason':finish,'message':message}],
                            'usage':{'prompt_tokens':11,'completion_tokens':7,'total_tokens':18}})
                    endpoint.http._transport=httpx2.MockTransport(respond)
            endpoint.start=start
        ctx.provide('provider',provider)
        ctx.provide('activation',provider.activation)
    capture=Plugin('capture',('storage','threads','models'),install,schema=SCHEMA+SDK_SCHEMA)
    roots=tuple(capture if root.name=='capture' else root for root in plan.plugins)+(model_plugin({'main':profile}),)
    public_profile={**profile,'endpoints':[{key:value for key,value in endpoint.items() if key not in ('api_key','base_url')} for endpoint in profile['endpoints']]}
    return replace(plan,plugins=roots,contract=replace(plan.contract,configuration={**configuration,
        'transport':'sdk-offline' if offline else 'sdk-live','provider_profile':public_profile}))


def build(config):
    return build_with_profile(config,{'endpoints':[{'id':'offline-sdk','model':'fixture-sdk','base_url':'http://fixture.invalid/v1',
        'api_key':'sdk-fixture-key','trust_env':False,'concurrency':1}], 'max_attempts':1,
        'request_options':{'temperature':0,'extra_body':{'chat_template_kwargs':{'enable_thinking':False}}}},offline=True)
