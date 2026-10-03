"""标准 Thread/Memory 插件经过真实 Driver、Runtime 和恢复的消费者。"""
import json
import pytest
from society0.kernel.services import thread_plugin, memory_plugin
from society0.kernel.composition import compose
from society0.kernel.plugins import Plugin
from society0.kernel.interaction import interaction_plugin
from society0.kernel.runtime import Actor, Phase, runtime_plugin
from society0.kernel.llm import LLMDriver
from society0.kernel.memory import MemoryPolicy
from tests.primary.test_kernel_memory import Client, Embed


@pytest.mark.asyncio
async def test_standard_services_two_steps_and_restore(tmp_path):
    held={}; requests=[]; closes=[]
    class Provider:
        async def request(self,tid,options):
            requests.append(held['threads'].read_messages(tid))
            if isinstance(options.get('tool_choice'),dict):
                return {'role':'assistant','content':None,'finish_reason':'tool_calls','tool_calls':[
                    {'id':'extract','type':'function','function':{'name':'extract_memories','arguments':json.dumps({'memories':[{'content':'完整记忆🙂','importance':4}]})}}]}
            return {'role':'assistant','content':'决定完成','finish_reason':'stop'}
    def dependencies(ctx):
        ctx.provide('embeddings',{'small':type('Embedding',(),{'embed':Embed().__call__})()})
        ctx.provide('models',{'small':Provider()})
        ctx.provide('client',Client())
        def close():
            assert held['memory']._closed
            closes.append('dependencies')
        ctx.on_close(close)
    def actors(ctx):
        threads=ctx.require('threads','threads'); memory=ctx.require('memory','memory')
        held.update(threads=threads,memory=memory)
        driver=LLMDriver(ctx.require('resources','models')['small'],threads,
            input_builder=lambda s:[{'role':'system','content':'主体完整背景'}],memory=memory)
        ctx.provide('actors',{'a':Actor('a',driver)})
    def plugins():
        return [thread_plugin(),Plugin('resources',install=dependencies),interaction_plugin(lambda *args:True),
            memory_plugin(client=('resources','client'),embedding=('resources','embeddings','small'),
                extraction=('resources','models','small'),recall_query=lambda s:'完整记忆'),
            Plugin('actors',('threads','memory','resources'),actors),
            runtime_plugin(information=('interaction','information'),actions=('interaction','actions'),store=('storage','store'),actor_service=('actors','actors'))]
    async def activate(ctx):
        ctx.activate('a'); result=await ctx.drain(); assert result[0].result.status=='completed'
    async with compose(tmp_path/'run',plugins()) as host:
        runtime=host.service('runtime','runtime')
        await runtime.run_step(1,1,[Phase('decide',activate)])
        await runtime.run_step(2,2,[Phase('decide',activate)])
        assert host.service('storage','store').complete_step==2
        assert any('完整记忆' in str(message) for message in requests[2])
        assert len(held['memory'].actions())==4
        original=held['threads'].find('a',{'time':2,'phase':'decide'})
    assert closes==['dependencies']
    async with compose(tmp_path/'restored',plugins(),source=tmp_path/'run') as host:
        assert held['threads'].find('a',{'time':2,'phase':'decide'})==original
        hits=await held['memory'].recall('a','完整记忆',top_k=2,current_step=2)
        assert hits and all(item['content']=='完整记忆🙂' for item in hits)
        await host.service('runtime','runtime').run_step(3,3,[Phase('decide',activate)])
    assert closes==['dependencies','dependencies']


def test_auto_write_requires_extraction_and_disabled_write_keeps_other_policy():
    # 提取器要求在已知 Thread kind 的激活装配时检查；interview 默认不写入。
    plugin=memory_plugin(client=('vectors','client'),embedding=('embeddings','embeddings','small'),
        policy=MemoryPolicy(auto_write=False,auto_recall=False,active_tools=True))
    assert 'models' not in plugin.requires


@pytest.mark.asyncio
async def test_memory_without_extractor_can_seed_and_query(tmp_path):
    def dependencies(ctx):
        ctx.provide('embeddings',{'small':type('Embedding',(),{'embed':Embed().__call__})()})
        ctx.provide('client',Client())
    plugins=[thread_plugin(),Plugin('resources',install=dependencies),
        memory_plugin(client=('resources','client'),embedding=('resources','embeddings','small'),
            policy=MemoryPolicy(auto_write=False,auto_recall=False,active_tools=False))]
    async with compose(tmp_path/'seeded',plugins) as host:
        memory=host.service('memory','memory')
        ids=await memory.seed('a','seed',timestamp=1,entries=[{'content':'种子原文','importance':3}])
        assert memory.get(ids[0],actor='a')['content']=='种子原文'
        hits=await memory.recall('a','种子',top_k=1,current_step=1)
        assert hits[0]['content']=='种子原文'
        host.service('storage','store').complete(1)


def test_importing_service_factories_does_not_load_optional_services():
    import os, subprocess, sys
    result=subprocess.run([sys.executable,'-c',"import sys; import society0.kernel.services; assert not any(x in sys.modules for x in ('chromadb','openai','bashkit','society0.kernel.memory','society0.kernel.models'))"],
        env={**os.environ,'PYTHONPATH':'src'},capture_output=True,text=True)
    assert result.returncode==0,result.stderr
