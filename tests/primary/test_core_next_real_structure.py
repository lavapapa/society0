"""真实验收计划的离线结构检查；替代模型响应不作为真实服务证据。"""
import json
import pytest
from tests.e2e.core_next_real_support import plan
from society0.kernel.models import ModelProvider,EmbeddingProvider
from society0.kernel.llm import LLMPolicy
from society0.kernel.runner import run_plan


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['decision','interview','memory'])
async def test_official_real_plan_assembles_and_completes_with_explicit_stubs(tmp_path,monkeypatch,mode):
    async def request(self,tid,options):
        forced=options.get('tool_choice')
        extraction=isinstance(forced,dict) and forced['function']['name']=='extract_memories'
        name='extract_memories' if extraction else 'submit_result'
        args={'memories':[{'content':'独立原文','importance':4}]} if extraction else {'score':8}
        if extraction or mode=='interview':
            return {'role':'assistant','content':None,'finish_reason':'tool_calls','tool_calls':[
                {'id':'extract' if extraction else 'answer','type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}
        return {'role':'assistant','content':'主体原文','finish_reason':'stop'}
    async def embed(self,texts,**kwargs):return [[1.,0.,0.,0.] for _ in texts]
    monkeypatch.setattr(ModelProvider,'request',request);monkeypatch.setattr(EmbeddingProvider,'embed',embed)
    endpoint={'id':'stub','base_url':'http://127.0.0.1:1/v1','api_key':'explicit-stub','model':'stub','concurrency':1}
    config={'release':'explicit-offline-fixture','llm':{'endpoints':[endpoint],'max_attempts':1},
        'embed':{'endpoints':[endpoint],'dimensions':4,'max_attempts':1}}
    policy=LLMPolicy(mode='interview' if mode=='interview' else 'decision',max_turns=4,
        result_schema={'type':'object','properties':{'score':{'type':'integer'}},'required':['score'],'additionalProperties':False} if mode=='interview' else None)
    current,held=plan(config,tmp_path/'run',goals='实际装配结构测试',policy=policy,memory=mode=='memory')
    result=await run_plan(tmp_path/'run',current)
    assert result['complete_step']==1
    assert held['outcomes'][0].result.status=='completed'

    if mode=='memory':
        assert held['vector_client']._closed


@pytest.mark.asyncio
@pytest.mark.parametrize('case',[
    'endpoint_smoke_llm_and_embedding','endpoint_saturation_llm_and_embedding_managers',
    'interview_writes_artifacts','saturation_memory_and_logs','phase_capacity_overrides_runtime',
    'memory_roundtrip','complete_boundary_memory_restore','round_robin_action_loop',
    'social_publish_with_memory','environment_action_tag_completion','terminal_rejection_then_success',
    'social_browse_completion_and_memory','multi_tick_social_workflow','vfs_discovery_pagination_original_and_action'])
async def test_real_case_fixture_through_actual_sdk_adapter(tmp_path,monkeypatch,case):
    """SDK 响应由确定脚本提供，用于提前检查正式真实案例的装配和断言。"""
    import asyncio,re
    from openai.resources.chat.completions import AsyncCompletions
    from openai.resources.embeddings import AsyncEmbeddings
    from openai.types.chat import ChatCompletion
    from openai.types import CreateEmbeddingResponse
    from tests.e2e import test_core_next_real as suite
    async def create(self,**request):
        await asyncio.sleep(.02)
        from tests.e2e.core_next_stub_provider import chat_response
        return ChatCompletion.model_validate(chat_response(request))
    async def embedding(self,**request):
        await asyncio.sleep(.01)
        inputs=request['input']
        return CreateEmbeddingResponse(object='list',model=request['model'],usage={'prompt_tokens':len(inputs),'total_tokens':len(inputs)},
            data=[{'object':'embedding','index':i,'embedding':[1.,(sum(map(ord,text))%997)/997.,0.,0.]} for i,text in enumerate(inputs)])
    monkeypatch.setattr(AsyncCompletions,'create',create);monkeypatch.setattr(AsyncEmbeddings,'create',embedding)
    endpoint={'id':'stub','base_url':'http://127.0.0.1:1/v1','api_key':'explicit-stub','model':'stub','concurrency':2}
    config={'release':'explicit-sdk-fixture','output':tmp_path,'llm':{'endpoints':[endpoint],'max_attempts':1},
        'embed':{'endpoints':[endpoint],'dimensions':4,'max_attempts':1}}
    await getattr(suite,'test_real_'+case)(config,tmp_path/'run')


def test_real_process_fixture_runs_crash_and_restore_against_local_stub_http(tmp_path,monkeypatch):
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from threading import Thread
    from tests.e2e.core_next_stub_provider import chat_response
    from tests.e2e.core_next_real_support import configuration
    from tests.e2e.test_core_next_real import test_real_exit_restore_memory_and_observation
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('/embeddings'):
                body={'object':'list','model':value['model'],'usage':{'prompt_tokens':1,'total_tokens':1},
                    'data':[{'object':'embedding','index':i,'embedding':[1.,0.,0.,0.]} for i,_ in enumerate(value['input'])]}
            else:body=chat_response(value)
            raw=json.dumps(body).encode()
            self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    address='http://127.0.0.1:'+str(server.server_port)+'/v1'
    for name,value in {'RELEASE':'offline-process-fixture','OUTPUT':str(tmp_path),'LLM_URL':address,'LLM_MODEL':'stub','LLM_KEY':'explicit-stub',
        'EMBED_URL':address,'EMBED_MODEL':'stub','EMBED_KEY':'explicit-stub','EMBED_DIMENSIONS':'4'}.items():
        monkeypatch.setenv('SOCIETY0_REAL_'+name,value)
    try:test_real_exit_restore_memory_and_observation(configuration(),tmp_path/'process')
    finally:server.shutdown();thread.join();server.server_close()


@pytest.mark.asyncio
async def test_official_actor_driver_factory_can_consume_persistent_workspace(tmp_path):
    from society0.kernel.actors import actor_plugin,ActorRecord
    from society0.kernel.services import thread_plugin
    from society0.kernel.workspace import workspace_plugin
    from society0.kernel.composition import compose
    from society0.kernel.schedule import RuleDriver
    from society0.kernel.runtime import DriverResult
    calls=[]
    def factory(ctx):
        workspace=ctx.require('workspace','workspace')
        def driver(record):
            calls.append((record.id,workspace))
            return RuleDriver(lambda session:DriverResult('completed'))
        return {'rule':driver}
    async with compose(tmp_path/'run',[
        actor_plugin(('rule',),records=[ActorRecord('a','rule')],requires=('workspace',),driver_factory=factory),
        workspace_plugin()]) as host:
        actor=host.service('actors','actors')['a']
        assert actor.id=='a' and calls[0][1] is host.service('workspace','workspace')


@pytest.mark.parametrize('trust',['0','1'])
def test_real_profile_freezes_explicit_llm_proxy_choice(monkeypatch,tmp_path,trust):
    from tests.e2e.core_next_real_support import configuration,public_profile
    for name,value in {'RELEASE':'test','OUTPUT':str(tmp_path),'LLM_URL':'https://unused.invalid/v1','LLM_MODEL':'m',
            'LLM_KEY':'private-test','EMBED_URL':'http://unused.invalid/v1','EMBED_MODEL':'e','EMBED_KEY':'private-test',
            'LLM_TRUST_ENV':trust}.items():monkeypatch.setenv('SOCIETY0_REAL_'+name,value)
    config=configuration()
    assert config['llm']['endpoints'][0]['trust_env']==(trust=='1')
    assert config['embed']['endpoints'][0]['trust_env'] is False
