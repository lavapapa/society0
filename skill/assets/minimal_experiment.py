"""两轮 LLM 研究计划：完整感知、查看详情、显式保存经验、结构化测量。"""
import argparse
import asyncio
import json
import os
import inspect
import platform
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from society0.kernel.actors import ActorRecord,actor_plugin
from society0.kernel.cognition import CognitiveInput
from society0.kernel.interaction import Action,ActionResult,interaction_plugin
from society0.kernel.llm import LLMDriver,LLMPolicy
from society0.kernel.memory import MemoryPolicy
from society0.kernel.plugins import Plugin
from society0.kernel.results import StepResult,results_plugin
from society0.kernel.runner import RunPlan,RunContract,run_plan
from society0.kernel.runtime import Phase,runtime_plugin
from society0.activation_pool import DEFAULT_MAX_ACTIVATIONS
from society0.kernel.schedule import CodeSchedule,activate
from society0.kernel.selection import result_mean,result_rows
from society0.kernel.services import thread_plugin,memory_plugin

MESSAGE='某个本地账号称下月地铁末班车将提前；尚无官方通知。'
SURVEY={'type':'object','properties':{'credibility':{'type':'integer','minimum':1,'maximum':7},
    'reason':{'type':'string'}},'required':['credibility','reason'],'additionalProperties':False}


def build_plan(*,release,resource_plugins=None,model=None,embedding=None,moments=(1,2)):
    """resources 提供具名 models/embeddings/client；正式端点从运行环境取得。"""
    browse_policy=LLMPolicy(required_names=('news.view_details',))
    interview_policy=LLMPolicy(mode='interview',result_schema=SURVEY)
    if resource_plugins is None:
        from society0.kernel.models import model_plugin,embedding_plugin,ModelProvider,EmbeddingProvider
        def resolved(provider, supplied):
            options={name:parameter.default for name,parameter in inspect.signature(provider).parameters.items()
                if parameter.kind is inspect.Parameter.KEYWORD_ONLY and parameter.default is not inspect.Parameter.empty and name!='request_limit'}
            options.update(supplied)
            options['endpoints']=[{'timeout':30.0,**endpoint} for endpoint in options['endpoints']]
            capacity=max(1,sum(endpoint['concurrency'] for endpoint in options['endpoints']))
            if options.get('http_connections') is None:options['http_connections']=capacity
            if provider is ModelProvider and options['global_concurrency'] is None:options['global_concurrency']=capacity
            return options
        model=resolved(ModelProvider,model)
        embedding=resolved(EmbeddingProvider,embedding)
        def vectors(ctx):
            import chromadb
            client=chromadb.EphemeralClient();ctx.on_close(client.close);ctx.provide('client',client)
            ctx.provide('models',ctx.require('models','models'))
            ctx.provide('embeddings',ctx.require('embeddings','embeddings'))
        resource_plugins=[model_plugin({'main':model}),embedding_plugin({'main':embedding}),
            Plugin('resources',('models','embeddings'),vectors)]
    def news(ctx):
        store=ctx.require('storage','store')
        def detail(scope,target,arguments):
            if target.key!='current':return ActionResult('rejected',{'reason':'message_unavailable'})
            store.transaction(lambda w:w.execute('INSERT INTO news_views(actor) VALUES(?)',(scope.actor,)))
            return ActionResult('completed',{'message':MESSAGE,'source':'本地账号，未见官方证实'})
        ctx.require('interaction','actions').register(Action('news.view_details',('news','message'),
            '查看完整消息及来源，并记录查看事实。',{'type':'object','properties':{},'additionalProperties':False},detail))
    def drivers(ctx):
        threads=ctx.require('threads','threads');memory=ctx.require('memory','memory')
        provider=ctx.require('resources','models')['main']
        async def perceive(session,cursor):
            task=('调用 news.view_details 查看详情，然后说明你的判断。目标为 '
                  '{"namespace":"news","kind":"message","key":"current"}。' if session.step==1 else '评价可信度，提交 1–7 分与理由。')
            return ([{'role':'user','content':MESSAGE+'\n'+task}],session.step)
        inputs=CognitiveInput(threads,perceive,environment='消息传播研究；信息与来源供主体自主判断。',precision='完整原文')
        class ExposureMemory:
            # 实验明确在浏览完成、原 Thread 关闭前提取；测量阶段不写入记忆。
            activation=memory.activation
            before_activation=memory.before_activation
            async def after_activation(self,session,thread_id,result):
                if result.status=='completed':
                    job=await memory.extract_job(session.actor.id,thread_id,through=result.value['memory_input_through'],timestamp=session.step)
                    await memory.finish_job(job)
        browse=LLMDriver(provider,threads,input_builder=inputs,memory=ExposureMemory(),
            policy=browse_policy)
        interview=LLMDriver(provider,threads,input_builder=inputs,memory=memory,
            policy=interview_policy)
        class StudyDriver:
            async def run(self,session):
                return await (browse if session.step==1 else interview).run(session)
        return {'reader':lambda record:StudyDriver()}
    def schedule(ctx):
        async def study(phase):
            outcomes=await activate(phase,('alice',))
            for item in outcomes:
                if item.result.status!='completed':raise RuntimeError('主体未完成：'+str(item.result.reason))
            return StepResult(metrics={'mean_credibility':result_mean(outcomes,('result','credibility'))},tables={'responses':result_rows(outcomes)})
        ctx.provide('schedule',CodeSchedule(ctx.require('runtime','runtime'),[Phase('study',study)]))
    plugins=[thread_plugin(),interaction_plugin(lambda *args:True),results_plugin(),*resource_plugins,
        memory_plugin(client=('resources','client'),embedding=('resources','embeddings','main'),extraction=('resources','models','main'),
            policy=MemoryPolicy(auto_write=False,auto_recall=True,active_tools=True),recall_query=lambda session:'此前看到的消息与判断'),
        Plugin('news',('storage','interaction'),news,schema=('CREATE TABLE news_views(ordinal INTEGER PRIMARY KEY,actor TEXT NOT NULL)',)),
        actor_plugin(('reader',),records=[ActorRecord('alice','reader',persona='关注本地交通消息的通勤者。',state={'attention':'正常'})],
            requires=('threads','memory','resources'),driver_factory=drivers),
        runtime_plugin(actor_service=('actors','actors'),information=('interaction','information'),actions=('interaction','actions'),
            store=('storage','store'),results=('results','results'),capacity=1,max_activations=DEFAULT_MAX_ACTIVATIONS),
        Plugin('schedule',('runtime','memory','news'),schedule)]
    moments=tuple(moments)
    profiles={name:{**options,'endpoints':[{key:value for key,value in endpoint.items() if key!='api_key'} for endpoint in options['endpoints']]}
        for name,options in (('llm',model),('embedding',embedding)) if options is not None}
    dependencies={name:version(name) for name in ('society0','apsw','jsonschema','python-rapidjson','backports-zstd','pydantic-ai-slim','openai','httpx2','chromadb','pydantic','json-repair')}
    dependencies['python']=platform.python_version()
    return RunPlan(plugins,moments,RunContract(release=release,dependencies=dependencies,
        configuration={'models':profiles,'message':MESSAGE,'memory':{'auto_write':False,'auto_recall':True,'active_tools':True},'survey':SURVEY},
        time={'moments':list(moments)},budgets={'browse':asdict(browse_policy),'interview':asdict(interview_policy),
            'runtime':{'capacity':1,'max_activations':DEFAULT_MAX_ACTIVATIONS}},
        credential_env=('SOCIETY0_LLM_API_KEY','SOCIETY0_EMBED_API_KEY')))


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir',required=True,type=Path)
    parser.add_argument('--check',action='store_true',help='仅初始化，不请求提供方')
    args=parser.parse_args()
    def endpoint(prefix):
        return {'id':prefix.lower(),'model':os.environ[prefix+'_MODEL'],'base_url':os.environ[prefix+'_BASE_URL'],
            'api_key':os.environ[prefix+'_API_KEY'],'concurrency':1,'timeout':60}
    model={'endpoints':[endpoint('SOCIETY0_LLM')],'request_options':json.loads(os.environ.get('SOCIETY0_LLM_OPTIONS','{}'))}
    embedding={'endpoints':[{**endpoint('SOCIETY0_EMBED'),'send_dimensions':False}],'dimensions':int(os.environ['SOCIETY0_EMBED_DIMENSIONS'])}
    plan=build_plan(release={'commit':os.environ['SOCIETY0_RELEASE']},model=model,embedding=embedding,moments=() if args.check else (1,2))
    print(json.dumps(await run_plan(args.run_dir,plan),ensure_ascii=False))


if __name__=='__main__':asyncio.run(main())
