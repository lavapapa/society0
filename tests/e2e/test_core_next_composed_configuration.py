"""配置错误在规定公共边界明确失败，合法配置跨进程执行。"""
import json
import os
import subprocess
import sys
import pytest
from .test_core_next_composition_entrypoint import config,cli,ROOT,verify

NEGATIVE=('duplicate-source','empty-source-id','bad-service','bad-position','missing-provider','missing-builder',
          'duplicate-plugin','service-cycle','schema-cycle','records-and-data')


@pytest.mark.parametrize('variant',NEGATIVE)
def test_configuration_rejects_specific_invalid_input(tmp_path,variant):
    cfg=tmp_path/'config.json'
    cfg.write_text(json.dumps(config(case='configuration',variant=variant)),encoding='utf-8')
    run=tmp_path/'run'
    program='import asyncio,json,sys; from examples.core_next.composed_cognition import build; from society0.kernel.runner import run_plan; asyncio.run(run_plan(sys.argv[2],build(json.load(open(sys.argv[1])))))'
    result=subprocess.run([sys.executable,'-c',program,str(cfg),str(run)],cwd=ROOT,env=os.environ,text=True,capture_output=True)
    log=tmp_path/'process.log'
    log.write_text(result.stdout+result.stderr,encoding='utf-8')
    assert result.returncode==1,result.stdout+result.stderr
    checked=subprocess.run([sys.executable,'-m','tests.e2e.core_next_composition_assertions','--case','configuration',
        '--config',str(cfg),'--log',str(log),'--exit-code',str(result.returncode),'--run',str(run)],cwd=ROOT,env=os.environ,text=True,capture_output=True)
    assert checked.returncode==0,checked.stdout+checked.stderr
    report=json.loads(checked.stdout)
    assert report['first_difference'] is None and report['checks']


@pytest.mark.parametrize('variant',('legal-two-graphs','external-directory','base-only'))
def test_configuration_public_legal_plan_restores(tmp_path,variant):
    run,result=cli(tmp_path,'run',config(case='configuration',variant=variant))
    assert result.returncode==0,result.stdout+result.stderr
    assert verify(run,case='configuration')['first_difference'] is None
    restored,resumed=cli(tmp_path,'restored',config(case='configuration',variant=variant),source=run,step=1)
    assert resumed.returncode==0,resumed.stdout+resumed.stderr
    assert verify(restored,case='configuration',compare=run)['first_difference'] is None


def test_base_only_public_runner_works_with_optional_packages_blocked(tmp_path):
    cfg=tmp_path/'config.json'
    cfg.write_text(json.dumps(config(case='configuration',variant='base-only')),encoding='utf-8')
    program="""import importlib.abc,sys,runpy
class AbsentOptional(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'pydantic_ai','openai','chromadb','datasets','anthropic','google','authlib'}:
            raise ImportError('optional package absent: '+fullname)
sys.meta_path.insert(0,AbsentOptional())
runpy.run_module('society0.kernel.runner',run_name='__main__')
"""
    result=subprocess.run([sys.executable,'-c',program,'--factory','examples.core_next.composed_cognition:build',
        '--config',str(cfg),'--output',str(tmp_path/'run')],cwd=ROOT,env=os.environ,text=True,capture_output=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert verify(tmp_path/'run',case='configuration')['first_difference'] is None
