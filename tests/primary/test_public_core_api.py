"""研究者公共入口的真实规则路径及按需依赖。"""
import os
import subprocess
import sys


def test_public_rule_entrypoint_is_light_and_restores(tmp_path):
    code = '''
import asyncio,sys
from society0 import compose,Plugin,ActorRecord,Phase,RuleDriver,StepResult,Ref,Query,Action,ActionResult
from society0.plugins import actor_plugin,interaction_plugin,runtime_plugin,plain_plugin,thread_plugin
from society0.kernel.runtime import DriverResult
from society0.kernel.observation import Observation
from pathlib import Path
async def main():
    path=Path(sys.argv[1])
    actor=ActorRecord('a','rule')
    def build():
        return [interaction_plugin(lambda *a:True),plain_plugin(),
            actor_plugin({'rule':lambda record:RuleDriver(lambda session:DriverResult('completed',{'whole':'原文'}))},records=[actor]),
            thread_plugin(),runtime_plugin(information=('interaction','information'),actions=('interaction','actions'),store=('storage','store'),actor_service=('actors','actors'))]
    async def phase(context):context.activate('a')
    async with compose(path/'run',build()) as host:
        await host.service('runtime','runtime').run_step(1,1,[Phase('decision',phase)])
        assert Observation(path/'run').status()['complete']['step']==1
    async with compose(path/'restored',build(),source=path/'run') as host:
        assert host.service('storage','store').complete_step==1
    for module in ('society0.core_data','society0.society','openai','chromadb','networkx','bashkit'):
        assert module not in sys.modules,module
asyncio.run(main())
'''
    result=subprocess.run([sys.executable,'-c',code,str(tmp_path)],capture_output=True,text=True,env=os.environ.copy())
    assert result.returncode==0,result.stderr


def test_retired_root_symbols_are_not_public():
    import society0
    assert not {'Society0','World','StateAccessMode','LLMModel','EmbedModel'} & set(society0.__all__)
