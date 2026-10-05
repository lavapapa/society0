"""复制到研究目录的正式两轮 LLM 示例：完整材料、显式记忆及新运行隔离。"""
from tests.primary.scripted_provider import TypedScriptProvider
import importlib.util
import json
from pathlib import Path
import pytest
from society0.kernel.plugins import Plugin
from society0.kernel.runner import run_plan
from society0.kernel.storage import StageReader
from tests.primary.test_kernel_memory import Client,Embed


def load_starter():
    spec=importlib.util.spec_from_file_location('starter',Path(__file__).resolve().parents[2]/'skill/assets/minimal_experiment.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def resources(requests,*,empty=False,fail_recall=False):
    class Provider:
        async def request(self,tid,options):
            messages=threads.read_messages(tid);requests.append(messages)
            names=[item['function']['name'] for item in options['tools']]
            if 'extract_memories' in names:
                name='extract_memories';arguments={'memories':[] if empty else [{'content':'我读到尚无官方确认的地铁消息','importance':3}]}
            elif 'submit_result' in names:
                name='submit_result';arguments={'credibility':3,'reason':'当前信息尚无官方确认'}
            elif not any(m.get('role')=='tool' for m in messages):
                name='action_invoke';arguments={'name':'news.view_details','target':{'namespace':'news','kind':'message','key':'current'},'arguments':{}}
            else:return {'role':'assistant','content':'已看详情；仍须核实来源。','finish_reason':'stop'}
            return {'role':'assistant','content':None,'finish_reason':'tool_calls','tool_calls':[{'id':str(len(requests)),'type':'function','function':{'name':name,'arguments':json.dumps(arguments,ensure_ascii=False)}}]}
    class Embedding:
        async def embed(self,texts,*,metadata):
            if fail_recall:raise RuntimeError('provider unavailable')
            return await Embed()(texts,metadata=metadata)
    threads=None
    def install(ctx):
        nonlocal threads
        threads=ctx.require('threads','threads')
        ctx.provide('models',{'main':TypedScriptProvider(Provider(), threads)});ctx.provide('embeddings',{'main':Embedding()});ctx.provide('client',Client())
    return [Plugin('resources',('threads',),install)]


@pytest.mark.asyncio
async def test_starter_preflight_and_two_llm_steps_keep_current_information_without_memory(tmp_path):
    starter=load_starter();requests=[]
    plan=starter.build_plan(release={'commit':'test'},resource_plugins=resources(requests,empty=True),moments=())
    await run_plan(tmp_path/'check',plan)
    assert requests==[]
    with StageReader(tmp_path/'check') as reader:
        assert reader.read(lambda r:r.query('SELECT COUNT(*) FROM news_views'))==[(0,)]
    plan=starter.build_plan(release={'commit':'test'},resource_plugins=resources(requests,empty=True))
    await run_plan(tmp_path/'run',plan)
    assert len(requests)==4
    interview=next(request for request in requests if any('评价可信度' in (m.get('content') or '') for m in request))
    assert starter.MESSAGE in str(interview)
    with StageReader(tmp_path/'run') as reader:
        assert reader.read(lambda r:r.query('SELECT actor FROM news_views'))==[('alice',)]
    await run_plan(tmp_path/'fresh',starter.build_plan(release={'commit':'test'},resource_plugins=resources([],empty=True),moments=()))
    with StageReader(tmp_path/'fresh') as reader:
        assert reader.read(lambda r:r.query('SELECT COUNT(*) FROM news_views'))==[(0,)]


@pytest.mark.asyncio
async def test_starter_provider_failure_remains_failed_and_does_not_publish(tmp_path):
    starter=load_starter()
    with pytest.raises(RuntimeError,match='provider unavailable'):
        await run_plan(tmp_path/'failed',starter.build_plan(release={'commit':'test'},resource_plugins=resources([],fail_recall=True)))
    assert json.loads((tmp_path/'failed'/'runner-status.json').read_text())['complete_step']==0


def test_starter_freezes_effective_budget_dependencies_and_safe_profiles():
    from importlib.metadata import version
    starter=load_starter()
    endpoint={'id':'test','model':'test','base_url':'http://unused.invalid/v1','api_key':'secret-value','concurrency':2}
    plan=starter.build_plan(release={'commit':'test'},moments=(),model={'endpoints':[endpoint]},embedding={'endpoints':[endpoint],'dimensions':2})
    contract=plan.contract
    assert contract.dependencies['society0']==version('society0')
    assert contract.dependencies['apsw']==version('apsw')
    assert contract.budgets['browse']['max_turns'] is None
    assert contract.budgets['browse']['empty_retries']==1
    assert contract.budgets['runtime']=={'capacity':1,'max_activations':4096}
    assert contract.configuration['models']['llm']['max_attempts']==2
    assert contract.configuration['models']['llm']['global_concurrency']==2
    assert contract.configuration['models']['embedding']['http_connections']==2
    assert contract.configuration['models']['llm']['endpoints'][0]['timeout']==30
    assert 'secret-value' not in str(contract)
    assert 'SOCIETY0_LLM_API_KEY' in contract.credential_env
