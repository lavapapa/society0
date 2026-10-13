"""官方配方返回普通根插件，实际运行、替换和恢复保持显式。"""
import json
import os
import subprocess
import sys

import pytest

from society0.kernel.actors import ActorRecord, actor_data_plugin
from society0.kernel.plugins import Plugin
from society0.kernel.runner import RunContract, RunPlan, run_plan
from society0.kernel.runtime import DriverResult, Phase, Runtime
from society0.kernel.schedule import SequenceSchedule, schedule_plugin
from society0.presets import rule_preset, llm_preset


def contract(*, budgets=None, actors='a', driver='rule', end=1):
    return RunContract(release={'commit': 'preset-deterministic'}, dependencies={'python': sys.version},
                       configuration={'actors': [{'id': actor, 'driver': driver} for actor in actors]},
                       time={'start': 1, 'end': end}, budgets={} if budgets is None else budgets)


def rule(current):
    return DriverResult('completed', {'actor': current.actor.id}, 'rule')


def activate_a(context):
    context.activate('a')


def schedule(start=1, end=1):
    return schedule_plugin(SequenceSchedule(range(start, end+1), [Phase('read', activate_a)]))


@pytest.mark.parametrize('factory,extra', [(rule_preset, {'run': rule}),
    (llm_preset, {'provider': ('provider', 'model'), 'input_builder': ('cognition', 'input_builder')})])
@pytest.mark.parametrize('records', [(), (ActorRecord('a', 'rule'),)])
def test_base_07_records_and_explicit_data_are_mutually_exclusive(factory, extra, records):
    with pytest.raises(ValueError, match='records.*data'):
        factory(records=records, data=('actors.data', 'directory'), allows=lambda *args: True,
                schedule=Plugin('schedule'), **extra)


@pytest.mark.asyncio
async def test_base_05_07_records_generator_is_lazy_and_external_directory_is_installed_once(tmp_path):
    consumed = []
    def records():
        consumed.append('initialized')
        yield ActorRecord('a', 'rule')
    configured = rule_preset(records=records(), run=rule, allows=lambda *args: True, schedule=schedule())
    assert isinstance(configured, tuple) and consumed == []
    result = await run_plan(tmp_path/'run', RunPlan(configured, contract()))
    assert result['complete_step'] == 1 and consumed == ['initialized']
    rebuilt = rule_preset(data=('actors.data', 'directory'), run=rule, allows=lambda *args: True,
                          schedule=schedule(1, 2), plugins=(actor_data_plugin(records=records()),))
    restored = await run_plan(tmp_path/'restored', RunPlan(rebuilt, contract(end=2)), source=tmp_path/'run', step=1)
    assert restored['complete_step'] == 2 and consumed == ['initialized']
    manifest = json.loads((tmp_path/'restored'/'runner.json').read_text())
    assert sum(item['name'] == 'actors.data' for item in manifest['plugins']) == 1
    assert manifest['source']['step'] == 1
    with pytest.raises(ValueError, match='duplicate'):
        await run_plan(tmp_path/'duplicate', RunPlan(rule_preset(records=(ActorRecord('a', 'rule'),),
            run=rule, allows=lambda *args: True, schedule=schedule(), plugins=(actor_data_plugin(),)), contract()))


@pytest.mark.asyncio
async def test_base_04_alternate_runtime_and_schedule_are_actually_called_and_manifest_is_truthful(tmp_path):
    events = []
    class AlternateRuntime:
        def __init__(self, inner):
            self.inner = inner
        @property
        def capacity(self): return self.inner.capacity
        @property
        def max_activations(self): return self.inner.max_activations
        @property
        def last_timing(self): return self.inner.last_timing
        async def run_step(self, number, time, phases):
            events.append(('runtime', number, time))
            return await self.inner.run_step(number, time, phases)
        async def close(self):
            events.append(('close',))
            await self.inner.close()
    def runtime_install(ctx):
        inner = Runtime(ctx.require('actors', 'actors'), information=ctx.require('interaction', 'information'),
                        actions=ctx.require('interaction', 'actions'), store=ctx.require('storage', 'store'),
                        results=ctx.require('results', 'results'), step_hooks=ctx.step_hooks, capacity=3, max_activations=7)
        actual = AlternateRuntime(inner)
        ctx.on_quiesce(actual.close, stage='runtime')
        ctx.provide('executor', actual)
    class AlternateSchedule:
        def __init__(self):
            self.inner = SequenceSchedule((1,), [Phase('read', activate_a)])
        async def next_step(self, completed):
            events.append(('schedule', completed))
            return await self.inner.next_step(completed)
    def schedule_install(ctx):
        ctx.provide('plan', AlternateSchedule())
    configured = rule_preset(records=(ActorRecord('a', 'rule'),), run=rule, allows=lambda *args: True,
        runtime=Plugin('alternative', ('actors', 'interaction', 'storage', 'results'), runtime_install),
        schedule=Plugin('calendar', install=schedule_install))
    assert all(plugin.name != 'runtime' for plugin in configured)
    result = await run_plan(tmp_path/'run', RunPlan(configured, contract(budgets={'capacity': 3, 'max_activations': 7}),
                           runtime=('alternative', 'executor'), schedule=('calendar', 'plan')))
    assert result['status'] == 'completed' and result['complete_step'] == 1
    assert events == [('schedule', 0), ('runtime', 1, 1), ('schedule', 1), ('close',)]
    manifest = json.loads((tmp_path/'run'/'runner.json').read_text())
    assert manifest['effective_runtime'] == {'capacity': 3, 'max_activations': 7}
    assert manifest['runtime'] == ['alternative', 'executor'] and manifest['schedule'] == ['calendar', 'plan']
    assert all(item['name'] != 'runtime' for item in manifest['plugins'])


@pytest.mark.asyncio
async def test_base_02_official_llm_preset_sends_dual_sources_through_full_driver(tmp_path):
    from tests.primary.test_social_cognition import plugins, SOCIAL_SOURCE_ID, NOTICE_SOURCE_ID
    from tests.primary.test_cognition_composition import request_texts
    providers = []
    extras = tuple(plugin for plugin in plugins(providers, capture_typed=True)
                   if plugin.name not in ('actors', 'interaction', 'threads', 'driver', 'runtime'))
    def install(ctx):
        ctx.require('social', 'mechanism').execute('publish_post', 'b', 'b', {'content': '配方社交完整正文🙂'}, 0)
        ctx.require('notice.data', 'notifications').publish('a', '配方通知完整正文🙂')
        ctx.provide('schedule', SequenceSchedule((1,), [Phase('read', activate_a)]))
    configured = llm_preset(records=(ActorRecord(actor, 'llm') for actor in 'abc'), provider=('provider', 'provider'),
        input_builder=('cognition', 'input_builder'), allows=lambda *args: True,
        schedule=Plugin('schedule', ('social', 'notice.data'), install), plugins=extras)
    result = await run_plan(tmp_path/'run', RunPlan(configured, contract(actors='abc', driver='llm')))
    assert result['status'] == 'completed' and result['complete_step'] == 1
    assert len(providers[0].requests) == 1
    texts = request_texts(providers[0].requests[0])
    assert any('配方社交完整正文🙂' in text for text in texts)
    assert any('配方通知完整正文🙂' in text for text in texts)
    manifest = json.loads((tmp_path/'run'/'runner.json').read_text())
    graph = {item['name']: item for item in manifest['plugins']}
    assert graph['cognition']['requires'] == ['threads', 'social.source', 'notice.source']
    assert 'memory' not in graph and 'llm_driver' in graph and 'actors.data' in graph


@pytest.mark.asyncio
@pytest.mark.parametrize('mode,write,recall,active', [('decision', True, False, False), ('decision', False, True, False),
    ('decision', False, False, True), ('decision', True, True, True), ('interview', True, True, True)])
async def test_base_03_explicit_memory_policies_are_independent_with_dual_source_preset(tmp_path, mode, write, recall, active):
    from types import SimpleNamespace
    from society0.kernel.llm import LLMPolicy
    from society0.kernel.memory import MemoryPolicy
    from society0.kernel.services import memory_plugin
    from tests.primary.test_kernel_memory import Client, Embed
    from tests.primary.test_social_cognition import plugins
    from tests.primary.test_cognition_composition import request_texts
    providers, extracted = [], []
    extras = [plugin for plugin in plugins(providers, capture_typed=True)
              if plugin.name not in ('actors', 'interaction', 'threads', 'driver', 'runtime')]
    async def extract(actor, tid, messages, *, metadata):
        texts = [message.get('content', '') for message in messages]
        assert any('记忆配方社交全文' in text for text in texts)
        assert any('记忆配方通知全文' in text for text in texts)
        extracted.append(actor)
        return [{'content': '本次完整源材料对应的记忆经历'}]
    def resources(ctx):
        ctx.provide('client', Client())
        ctx.provide('profiles', {'main': SimpleNamespace(embed=Embed())})
    extras.extend([Plugin('memory_resources', install=resources),
        memory_plugin(client=('memory_resources', 'client'), embedding=('memory_resources', 'profiles', 'main'),
            extract=extract, policy=MemoryPolicy(auto_write=write, auto_recall=recall, active_tools=active),
            recall_query=lambda current: '完整召回查询')])
    counts = []
    async def initialize(ctx):
        ctx.require('social', 'mechanism').execute('publish_post', 'b', 'b', {'content': '记忆配方社交全文'}, 0)
        ctx.require('notice.data', 'notifications').publish('a', '记忆配方通知全文')
        memory = ctx.require('memory', 'memory')
        await memory.finish_job(memory.prepare_job('a', None, 'seed', timestamp=0, entries=[{'content': '此前完整记忆🙂'*100}]))
        async def phase(current):
            current.activate('a'); await current.drain()
            counts.append(ctx.require('storage', 'store').read(lambda view: view.query('SELECT count(*) FROM memory_rows')[0][0]))
        ctx.provide('schedule', SequenceSchedule((1,), [Phase('read', phase)]))
    configured = llm_preset(records=(ActorRecord(actor, 'llm') for actor in 'abc'), provider=('provider', 'provider'),
        input_builder=('cognition', 'input_builder'), allows=lambda *args: True,
        schedule=Plugin('schedule', ('social', 'notice.data', 'memory', 'storage'), initialize), plugins=extras,
        extensions=(('memory', 'extension'),), policy=LLMPolicy(mode=mode))
    result = await run_plan(tmp_path/'run', RunPlan(configured, contract(actors='abc', driver='llm')))
    assert result['status'] == 'completed' and len(providers[0].requests) == 1
    text = '\n'.join(request_texts(providers[0].requests[0]))
    assert ('此前完整记忆🙂'*100 in text) == recall
    assert ('memory_actions_target' in text) == active
    written = write and mode != 'interview'
    assert counts == [1+int(written)] and extracted == (['a'] if written else [])


@pytest.mark.asyncio
@pytest.mark.parametrize('reference', ['provider', 'builder', 'extension', 'cycle'])
async def test_base_06_missing_or_cyclic_refs_fail_before_prepare_or_business(tmp_path, reference):
    prepared = []
    provider = Plugin('provider', requires=('llm_driver',) if reference == 'cycle' else (),
                      prepare=lambda ctx: prepared.append('provider'))
    configured = llm_preset(records=(ActorRecord('a', 'llm'),),
        provider=('missing_provider' if reference == 'provider' else 'provider', 'provider'),
        input_builder=('missing_builder' if reference == 'builder' else 'cognition', 'input_builder'),
        extensions=(('missing_extension', 'extension'),) if reference == 'extension' else (),
        allows=lambda *args: True, schedule=schedule(), plugins=(provider, Plugin('cognition')))
    with pytest.raises(ValueError):
        await run_plan(tmp_path/'invalid', RunPlan(configured, contract()))
    assert prepared == [] and not (tmp_path/'invalid').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['provider', 'builder', 'extension'])
async def test_base_06_missing_service_closes_installed_owners_without_requests(tmp_path, missing):
    from types import SimpleNamespace
    closed, requests = [], []
    async def request(*args, **kwargs):
        requests.append('called')
        raise AssertionError('business must not start')
    def provider(ctx):
        ctx.on_close(closed.append, 'provider')
        ctx.provide('other' if missing == 'provider' else 'provider', SimpleNamespace(request_model=request))
    def builder(ctx):
        ctx.on_close(closed.append, 'builder')
        ctx.provide('other' if missing == 'builder' else 'input_builder', lambda current: [])
    def extension(ctx):
        ctx.on_close(closed.append, 'extension')
        ctx.provide('other' if missing == 'extension' else 'extension', lambda current: None)
    configured = llm_preset(records=(ActorRecord('a', 'llm'),), provider=('provider', 'provider'),
        input_builder=('cognition', 'input_builder'), extensions=(('extension', 'extension'),),
        allows=lambda *args: True, schedule=schedule(), plugins=(Plugin('provider', install=provider),
            Plugin('cognition', install=builder), Plugin('extension', install=extension)))
    with pytest.raises(KeyError):
        await run_plan(tmp_path/'invalid', RunPlan(configured, contract(driver='llm')))
    assert sorted(closed) == ['builder', 'extension', 'provider'] and requests == []
    assert not (tmp_path/'invalid'/'runner.json').exists()


@pytest.mark.parametrize('missing', ['provider', 'input_builder'])
def test_base_06_none_provider_or_builder_fails_at_preset_boundary(missing):
    kwargs = {'provider': ('provider', 'model'), 'input_builder': ('cognition', 'input_builder')}
    kwargs[missing] = None
    with pytest.raises(ValueError):
        llm_preset(allows=lambda *args: True, schedule=Plugin('schedule'), **kwargs)


def test_base_01_rule_preset_import_and_two_mechanism_public_run_have_no_optional_imports(tmp_path):
    code = '''
import builtins, asyncio, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('pydantic_ai', 'society0.kernel.llm', 'society0.kernel.memory', 'society0.plugins.social')):
        raise AssertionError('optional import: '+name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from society0.presets import rule_preset, llm_preset
from examples.core_next.conversation_pilot import build
from society0.kernel.runner import run_plan
plan = build({'start': 1, 'end': 1, 'release': {'commit': 'preset-test'}})
assert 'rule_driver' in [plugin.name for plugin in plan.plugins]
result = asyncio.run(run_plan(sys.argv[1], plan))
assert result['complete_step'] == 1 and result['status'] == 'completed'
assert not any(name.startswith(('pydantic_ai', 'society0.kernel.llm', 'society0.kernel.memory', 'society0.plugins.social')) for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path/'run')], env=os.environ.copy(), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
