"""新版真实验收共享装配；凭据由显式环境提供，公开合同随运行保存。"""
from __future__ import annotations
from society0.kernel.memory import MemoryExtension
import json
import os
import sys
import time
from pathlib import Path


class RealConfiguration(dict):
    def __repr__(self):return "<real endpoint configuration; credentials omitted>"


def configuration():
    required=('SOCIETY0_REAL_RELEASE','SOCIETY0_REAL_OUTPUT','SOCIETY0_REAL_LLM_URL',
              'SOCIETY0_REAL_LLM_MODEL','SOCIETY0_REAL_LLM_KEY','SOCIETY0_REAL_EMBED_URL',
              'SOCIETY0_REAL_EMBED_MODEL','SOCIETY0_REAL_EMBED_KEY')
    missing=[name for name in required if not os.environ.get(name)]
    if missing:raise ValueError('missing real test configuration: '+', '.join(missing))
    return RealConfiguration({
        'release':os.environ['SOCIETY0_REAL_RELEASE'],
        'output':Path(os.environ['SOCIETY0_REAL_OUTPUT']),
        'llm':{'endpoints':[{'id':'real','base_url':os.environ['SOCIETY0_REAL_LLM_URL'],
            'model':os.environ['SOCIETY0_REAL_LLM_MODEL'],'api_key':os.environ['SOCIETY0_REAL_LLM_KEY'],
            'concurrency':int(os.environ.get('SOCIETY0_REAL_MODEL_CAPACITY','2')),'timeout':60,'trust_env':os.environ.get('SOCIETY0_REAL_LLM_TRUST_ENV','0')=='1'}],
            'max_attempts':1,'request_jitter':0,'session_transport':None,
            'request_options':json.loads(os.environ.get('SOCIETY0_REAL_REQUEST_OPTIONS',
                '{"max_tokens":1024,"temperature":0,"parallel_tool_calls":false,"openai_reasoning_effort":"minimal"}'))},
        'embed':{'endpoints':[{'id':'real-embedding','base_url':os.environ['SOCIETY0_REAL_EMBED_URL'],
            'model':os.environ['SOCIETY0_REAL_EMBED_MODEL'],'api_key':os.environ['SOCIETY0_REAL_EMBED_KEY'],
            'concurrency':2,'timeout':60,'trust_env':False,'send_dimensions':False}],
            'dimensions':int(os.environ.get('SOCIETY0_REAL_EMBED_DIMENSIONS','1024')),'max_attempts':1},
    })


def public_profile(config):
    return {kind:{**options,'endpoints':[{key:value for key,value in endpoint.items() if key!='api_key'}
            for endpoint in options['endpoints']]} for kind,options in ((name,config[name]) for name in ('llm','embed'))}


def plan(config,path,*,goals,policy,mechanism='none',memory=False,moments=(1,),actors=('a',),
         capacity=1,phase_capacity=None,setup=None,after=None,fail_after=False,workspace=False,extra_plugins=()):
    """所有案例消费同一正式工厂；测试只提供领域目标与明确结果断言。"""
    from society0.kernel.actors import ActorRecord,actor_plugin
    from society0.kernel.cognition import CognitiveInput
    from society0.kernel.interaction import interaction_plugin
    from society0.kernel.llm import LLMDriver
    from society0.kernel.models import model_plugin,embedding_plugin
    from society0.kernel.plugins import Plugin
    from society0.kernel.results import results_plugin,StepResult
    from society0.kernel.runner import RunPlan,RunContract
    from society0.kernel.runtime import Phase,runtime_plugin
    from society0.kernel.schedule import SequenceSchedule,activate
    from society0.kernel.services import thread_plugin,memory_plugin
    moments=tuple(moments)
    held={'outcomes':[],'after':[],'intervals':[],'active':{},'peak':{}}
    def measured(kind,method):
        async def call(*args,**kwargs):
            started=time.perf_counter()
            held['active'][kind]=held['active'].get(kind,0)+1
            held['peak'][kind]=max(held['peak'].get(kind,0),held['active'][kind])
            try:return await method(*args,**kwargs)
            finally:
                held['active'][kind]-=1
                held['intervals'].append({'kind':kind,'started':started,'ended':time.perf_counter()})
        return call
    def resources(ctx):
        if memory or mechanism=='social':
            import chromadb
            client=chromadb.PersistentClient(path=str(path/'vectors'))
            ctx.on_close(client.close)
            held['vector_client']=client
            ctx.provide('client',client)
    def drivers(ctx):
        threads=ctx.require('threads','threads')
        held.update(threads=threads,store=ctx.require('storage','store'))
        if memory:held['memory']=ctx.require('memory','memory')
        for name,kind in (('models','llm_sdk'),('embeddings','embedding_sdk')):
            for endpoint in ctx.require(name,name)['main'].endpoints:
                original=endpoint.start
                async def start(endpoint=endpoint,original=original,kind=kind):
                    await original()
                    resource=endpoint.client.chat.completions if kind=='llm_sdk' else endpoint.client.embeddings
                    resource.create=measured(kind,resource.create)
                endpoint.start=start
        async def perception(session,cursor):
            goal=goals(session) if callable(goals) else goals
            return ([{'role':'user','content':goal}],session.step)
        input_builder=CognitiveInput(threads,perception,environment=
            '这是共享社会模拟。可按当前目标需要使用信息与行动工具。信息从 /world 用 ls 发现，用 read 读取原文，以 grep 搜索，用 bash data query 对数据集筛选；世界目录 total=0 表示当前无可访问的信息挂载。'
            '需要行动时可用 action_find 发现可用行动，再用 action_describe 获取完整参数。'
            'action_invoke.arguments 使用 JSON 对象。只执行本次任务要求的行动。',
            precision='所有已提供材料均为原文。')
        def shell(session,ledger):
            from society0.kernel.shell import ShellSession
            return ShellSession(session.scope,ctx.require('interaction','information'),bound_actions=ledger,
                result_dir=path/'shell-results',workspace=ctx.require('workspace','workspace'))
        def factory(record):
            driver=LLMDriver(ctx.require('models','models')['main'],threads,input_builder=input_builder,
                policy=policy,extensions=(MemoryExtension(held['memory']),) if memory else (),shell_factory=shell if workspace else None)
            driver.run=measured('activation',driver.run)
            return driver
        return {'llm':factory}
    plugins=[thread_plugin(),interaction_plugin(lambda *args:True),results_plugin(),
        model_plugin({'main':config['llm']}),embedding_plugin({'main':config['embed']}),
        Plugin('vectors',install=resources)]
    if memory:
        plugins.append(memory_plugin(client=('vectors','client'),embedding=('embeddings','embeddings','main'),
            extraction=('models','models','main'),recall_query=lambda session:'本主体重要经历与先前决定'))
    if workspace:
        from society0.kernel.workspace import workspace_plugin
        plugins.append(workspace_plugin())
    plugins.extend(extra_plugins)
    dependencies=('threads','models','embeddings','interaction')+(('memory',) if memory else ())+(('workspace',) if workspace else ())
    plugins.append(actor_plugin(('llm',),requires=dependencies,driver_factory=drivers,records=[
        ActorRecord(actor,'llm',persona='你是主体 '+actor+'。认真遵照本轮任务，保留事实原文。') for actor in actors]))
    if mechanism=='round':
        from society0.plugins.round_robin import round_robin_plugin
        plugins.append(round_robin_plugin(actors,group_size=len(actors),name='chat'))
    if mechanism=='social':
        from society0.plugins.social import social_plugin
        plugins.append(social_plugin(actors,name='social',content_length_limit=-1,edges=[],
            embedding=('embeddings','main'),vector_client=('vectors','client')))
    plugins.append(runtime_plugin(actor_service=('actors','actors'),information=('interaction','information'),
        actions=('interaction','actions'),store=('storage','store'),results=('results','results'),capacity=capacity))
    requires=['runtime','storage','threads','models','embeddings','interaction']
    if memory:requires.append('memory')
    if mechanism!='none':requires.append('chat' if mechanism=='round' else 'social')
    def schedule(ctx):
        held['context']=ctx
        async def prepare(phase):
            if mechanism=='round':ctx.require('chat','mechanism').start_round(phase.moment.time)
            if setup is not None:await setup(ctx,phase,held)
        async def decide(phase):
            outcomes=await activate(phase,actors)
            held['outcomes'].extend(outcomes)
            if after is not None:held['after'].append(await after(ctx,phase,held))
            if fail_after:raise RuntimeError('deliberate incomplete step after actual model activation')
            return StepResult(metrics={'activations':len(outcomes)})
        ctx.provide('schedule',SequenceSchedule(moments,[
            Phase('prepare',prepare),Phase('decision',decide,execution='independent' if capacity>1 or phase_capacity else 'serial',capacity=phase_capacity)]))
    plugins.append(Plugin('schedule',tuple(requires),schedule))
    from importlib.metadata import version,PackageNotFoundError
    packages={}
    for package,enabled in (('openai',True),('apsw',True),('python-rapidjson',True),
            ('chromadb',memory or mechanism=='social'),('bashkit',workspace),('society0-filesystem',workspace)):
        try:installed=version(package)
        except PackageNotFoundError:installed=None
        packages[package]={'version':installed,'enabled':enabled}
    contract=RunContract({'commit':config['release']},{'python':sys.version,'packages':packages,'lockfile':'uv.lock at '+config['release']},
        {'models':public_profile(config),'actors':list(actors),'mechanism':mechanism,'memory':memory,'workspace':workspace,'scenario':path.name,
         'task':goals if isinstance(goals,str) else goals.__module__+'.'+goals.__qualname__},
        {'moments':list(moments)},{'policy':policy.__dict__,'capacity':capacity,'phase_capacity':phase_capacity},
        ('SOCIETY0_REAL_LLM_KEY','SOCIETY0_REAL_EMBED_KEY'))
    return RunPlan(plugins,contract),held
