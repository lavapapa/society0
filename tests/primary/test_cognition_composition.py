"""组合输入保持独立水位与完整实际模型入参；故障不提前确认材料。"""
import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from society0.kernel.actors import ActorRecord, actor_data_plugin
from society0.kernel.composition import compose
from society0.kernel.cognition import InputBatch
from society0.kernel.interaction import Actions, Information, InteractionScope, Moment
from society0.kernel.llm import LLMDriver
from society0.kernel.plugins import Plugin
from society0.kernel.runtime import Actor, Session
from society0.kernel.services import thread_plugin
from society0.plugins.cognition import (
    ComposedInput, CompositePerception, SourceBatch, SourcePosition, SourceSpec, cognition_plugin,
)
from tests.primary.scripted_provider import scripted_response


class CapturingProvider:
    def __init__(self, threads):
        self.threads = threads
        self.requests = []

    async def request_model(self, thread_id, options, *, model_messages=None):
        from pydantic_ai.messages import ModelMessagesTypeAdapter
        actual = model_messages() if callable(model_messages) else model_messages
        self.requests.append(ModelMessagesTypeAdapter.dump_python(actual, mode='json'))
        return scripted_response(self.threads, thread_id, {'content': '完整决定回复🙂', 'finish_reason': 'stop'})


def session(driver, *, actor='a', phase='A', step=1):
    scope = InteractionScope(actor, Moment(1, phase))
    record = SimpleNamespace(persona='完整主体背景🙂' + actor, state={'主观状态': actor}, config={})
    return Session(Actor(actor, driver, config=record), scope,
                   Information(lambda *args: True).bound(scope), Actions(lambda *args: True).bound(scope),
                   {}, None, (), None, step=step)


def request_texts(request):
    return [part['content'] for message in request for part in message['parts']
            if part['part_kind'] in ('system-prompt', 'user-prompt', 'text')]


def data_plugins():
    return [actor_data_plugin(records=[ActorRecord(actor, 'unused') for actor in 'ab']), thread_plugin()]


@pytest.mark.asyncio
@pytest.mark.parametrize('position', [None, 0, {}])
async def test_cog_03_uninitialized_and_saved_empty_positions_are_distinct(position):
    calls = []
    def read(current, previous):
        calls.append(previous)
        return SourceBatch([], position)
    perception = CompositePerception((('one', read),))
    current = session(None)
    first = await perception(current, None)
    second = await perception(current, {**first.position, 'inactive': {'old': 7}})
    assert calls[0] is None and isinstance(calls[1], SourcePosition)
    assert calls[1].position == position
    assert second.position == {'one': position, 'inactive': {'old': 7}}


@pytest.mark.parametrize('sources', [
    (('same', lambda *args: None), ('same', lambda *args: None)),
    (('', lambda *args: None),),
    ((1, lambda *args: None),),
])
def test_cog_05_direct_composition_rejects_bad_ids(sources):
    with pytest.raises(ValueError):
        CompositePerception(sources)


@pytest.mark.parametrize('reference', [(), ('one',), ('one', 'two', 'three'), ('', 'source'), ('source', '')])
def test_cog_05_bad_source_references_fail_before_install(reference):
    with pytest.raises(ValueError):
        cognition_plugin(sources=(SourceSpec('one', reference),))


@pytest.mark.asyncio
async def test_cog_05_duplicate_missing_service_and_nonmapping_cursor_fail(tmp_path):
    with pytest.raises(ValueError):
        cognition_plugin(sources=(SourceSpec('same', ('one', 'source')), SourceSpec('same', ('two', 'source'))))
    plugin = cognition_plugin(sources=(SourceSpec('one', ('source', 'absent')),))
    with pytest.raises(KeyError):
        async with compose(tmp_path/'missing', [*data_plugins(), Plugin('source'), plugin]):
            pass
    with pytest.raises(TypeError, match='mapping'):
        await CompositePerception(())(session(None), 7)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['error', 'cancel'])
async def test_cog_06_second_source_failure_never_commits_or_confirms(tmp_path, failure):
    effects = []
    def first(current, previous):
        return SourceBatch([{'role': 'user', 'content': '第一个来源完整正文'}], 1,
                           effects=(lambda context: effects.append('first'),))
    async def second(current, previous):
        if failure == 'error':
            raise ValueError('source failed')
        raise asyncio.CancelledError()
    plugins = [*data_plugins(), Plugin('sources', install=lambda ctx: (
        ctx.provide('first', first), ctx.provide('second', second))),
        cognition_plugin(sources=(SourceSpec('first', ('sources', 'first')), SourceSpec('second', ('sources', 'second'))))]
    async with compose(tmp_path/failure, plugins) as host:
        threads = host.service('threads', 'threads')
        provider = CapturingProvider(threads)
        driver = LLMDriver(provider, threads, input_builder=host.service('cognition', 'input_builder'))
        current = session(driver)
        with pytest.raises(ValueError if failure == 'error' else asyncio.CancelledError):
            await driver.run(current)
        tid = current.cursors['thread_id']
        assert threads.input_cursor(tid, 'operating_context') is None
        assert threads.read_messages(tid) == [] and effects == [] and provider.requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize('broken', ['first_message', 'last_message', 'cursor', 'context'])
@pytest.mark.parametrize('seeded', [False, True])
async def test_cog_07_input_serialization_is_atomic_and_has_no_effects(tmp_path, broken, seeded):
    effects = []
    def read(current, previous):
        messages = [{'role': 'user', 'content': '完整来源正文🙂'}]
        if broken in ('first_message', 'last_message'):
            messages.insert(0 if broken == 'first_message' else len(messages), {'role': 'user', 'content': object()})
        return SourceBatch(messages, object() if broken == 'cursor' else 1,
                           effects=(lambda context: effects.append('confirmed'),))
    async with compose(tmp_path/broken, data_plugins()) as host:
        threads = host.service('threads', 'threads')
        provider = CapturingProvider(threads)
        if seeded:
            tid = threads.open('a', Moment(1, 'A'), 'decision')
            threads.append_input(tid, [{'role': 'user', 'content': '此前完整材料'}], 'operating_context',
                                 {'position': {'one': 0, 'inactive': 17}}, context={'role': 'system', 'content': '此前完整背景'})
            threads.close(tid, 'completed')
            before = (threads.input_context(tid, 'operating_context'), threads.input_cursor(tid, 'operating_context'), threads.read_messages(tid))
        else:
            before = (None, None, [])
        composed = ComposedInput(threads, CompositePerception((('one', read),)))
        async def builder(current):
            batch = await composed(current)
            return replace(batch, context={'role': 'system', 'content': object()}) if broken == 'context' else batch
        driver = LLMDriver(provider, threads, input_builder=builder)
        current = session(driver)
        with pytest.raises(TypeError):
            await driver.run(current)
        tid = current.cursors['thread_id']
        assert (threads.input_context(tid, 'operating_context'), threads.input_cursor(tid, 'operating_context'), threads.read_messages(tid)) == before
        assert effects == [] and provider.requests == []


@pytest.mark.asyncio
async def test_cog_08_discarded_batch_has_no_effects_and_no_saved_position(tmp_path):
    effects = []
    def read(current, previous):
        return SourceBatch([{'role': 'user', 'content': '被丢弃正文'}], 9,
                           effects=(lambda context: effects.append('discarded'),))
    async with compose(tmp_path/'run', data_plugins()) as host:
        threads = host.service('threads', 'threads')
        provider = CapturingProvider(threads)
        composed = ComposedInput(threads, CompositePerception((('one', read),)))
        async def choose(current):
            await composed(current)
            return InputBatch([{'role': 'user', 'content': '最终完整正文'}], 'operating_context', {'position': {}})
        driver = LLMDriver(provider, threads, input_builder=choose)
        current = session(driver)
        await driver.run(current)
        assert effects == []
        assert threads.input_cursor(current.cursors['thread_id'], 'operating_context') == {'position': {}}
        texts = request_texts(provider.requests[0])
        assert '最终完整正文' in texts and '被丢弃正文' not in texts


@pytest.mark.asyncio
async def test_cog_01_09_effects_see_saved_context_messages_and_positions_before_provider(tmp_path):
    order = []
    async with compose(tmp_path/'run', data_plugins()) as host:
        threads = host.service('threads', 'threads')
        provider = CapturingProvider(threads)
        def source(name):
            def read(current, previous):
                def saved(context):
                    assert not provider.requests
                    assert threads.input_cursor(context.thread_id, 'operating_context') == {'position': {'left': 1, 'right': 2}}
                    assert '完整主体背景' in threads.input_context(context.thread_id, 'operating_context')['content']
                    texts = [m['content'] for m in threads.read_messages(context.thread_id)]
                    assert 'left完整正文' in texts and 'right完整正文' in texts
                    order.append(name)
                return SourceBatch([{'role': 'user', 'content': name + '完整正文'}], 1 if name == 'left' else 2, effects=(saved,))
            return read
        composed = ComposedInput(threads, CompositePerception((('left', source('left')), ('right', source('right')))),
                                 environment='完整环境', precision='完整精度', reminders='完整提醒')
        driver = LLMDriver(provider, threads, input_builder=composed)
        await driver.run(session(driver))
        texts = request_texts(provider.requests[0])
        assert order == ['left', 'right']
        assert texts.index('left完整正文') < texts.index('right完整正文')
        assert sum('主体背景' in text for text in texts) == 1
        assert sum('当前主观状态' in text for text in texts) == 1
        assert any('完整环境' in text and '完整精度' in text for text in texts)
        assert any('完整提醒' in text for text in texts)


@pytest.mark.asyncio
async def test_cog_04_notification_instances_default_to_distinct_schema_and_explicit_service_refs(tmp_path):
    from examples.core_next.composition_support import notification_data_plugin, notification_source_plugin
    configured = [*data_plugins(), notification_data_plugin(name='first.data'), notification_data_plugin(name='second.data'),
                  notification_source_plugin(data=('first.data', 'notifications')),
                  cognition_plugin(sources=(SourceSpec('first.v1', ('notice.source', 'source')),))]
    async with compose(tmp_path/'run', configured) as host:
        first, second = [host.service(name+'.data', 'notifications') for name in ('first', 'second')]
        assert first.table('notices') != second.table('notices')
        assert first.publish('a', '第一实例完整正文') == second.publish('a', '第二实例完整正文') == 1
        threads, provider, driver = driver_for(host)
        await driver.run(session(driver))
        texts = request_texts(provider.requests[0])
        assert any('第一实例完整正文' in text for text in texts)
        assert not any('第二实例完整正文' in text for text in texts)


@pytest.mark.asyncio
async def test_cog_09_duplicate_receipt_is_an_error_and_failed_acknowledgement_rolls_back(tmp_path):
    import apsw
    async with compose(tmp_path/'run', notification_plan()) as host:
        left = host.service('left.data', 'notifications')
        left.publish('a', '已保存正文')
        left.publish('a', '尚未保存正文')
        tid = host.service('threads', 'threads').open('a', Moment(1, 'A'), 'decision')
        left.acknowledge('a', tid, [1])
        with pytest.raises(apsw.ConstraintError):
            left.acknowledge('a', tid, [2, 1])
        assert left.receipts('a') == [(tid, 1)]


def notification_plan(enabled=('left', 'right'), *, order=None):
    from examples.core_next.composition_support import notification_data_plugin, notification_source_plugin
    enabled = tuple(enabled)
    order = enabled if order is None else tuple(order)
    return [*data_plugins(),
            *(notification_data_plugin(name=name+'.data') for name in ('left', 'right')),
            *(notification_source_plugin(name=name+'.source', data=(name+'.data', 'notifications')) for name in enabled),
            cognition_plugin(sources=tuple(SourceSpec(name+'.v1', (name+'.source', 'source')) for name in order))]


def driver_for(host):
    threads = host.service('threads', 'threads')
    provider = CapturingProvider(threads)
    return threads, provider, LLMDriver(provider, threads, input_builder=host.service('cognition', 'input_builder'))


@pytest.mark.asyncio
async def test_cog_02_04_13_independent_positions_survive_phase_return_omission_and_reintroduction(tmp_path):
    original = {'left': '左来源原文🙂'*1000, 'right': '右来源原文🙂'*1000}
    async with compose(tmp_path/'run', notification_plan()) as host:
        threads, provider, driver = driver_for(host)
        left, right = [host.service(name+'.data', 'notifications') for name in ('left', 'right')]
        left.publish('a', original['left'])
        right.publish('a', original['right'])
        first = session(driver)
        await driver.run(first)
        tid = first.cursors['thread_id']
        left.publish('a', '左第二条')
        await driver.run(session(driver))
        right.publish('a', '右第二条')
        await driver.run(session(driver))
        assert threads.input_cursor(tid, 'operating_context') == {'position': {'left.v1': 2, 'right.v1': 2}}
        first_prefix = threads.read_messages(tid)
        await driver.run(session(driver, phase='B'))
        last = session(driver)
        await driver.run(last)
        assert last.cursors['thread_id'] == tid
        assert threads.read_messages(tid)[:len(first_prefix)] == first_prefix
        assert sum(original['left'] in text for text in request_texts(provider.requests[-1])) == 1
        assert sum(original['right'] in text for text in request_texts(provider.requests[-1])) == 1
        assert [(thread, identifier) for thread, identifier in left.receipts('a') if thread == tid] == [(tid, 1), (tid, 2)]
        host.service('storage', 'store').complete(1)
    async with compose(tmp_path/'omitted', notification_plan(('left',)), source=tmp_path/'run', step=1) as host:
        threads, provider, driver = driver_for(host)
        left, right = [host.service(name+'.data', 'notifications') for name in ('left', 'right')]
        right.publish('a', '右积累第三条')
        left.publish('a', '左第三条')
        current = session(driver, step=2)
        await driver.run(current)
        assert current.cursors['thread_id'] == tid
        assert threads.input_cursor(tid, 'operating_context') == {'position': {'left.v1': 3, 'right.v1': 2}}
        texts = request_texts(provider.requests[-1])
        assert any('左第三条' in text for text in texts) and not any('右积累第三条' in text for text in texts)
        assert (tid, 3) not in right.receipts('a')
        host.service('storage', 'store').complete(2)
    async with compose(tmp_path/'reintroduced', notification_plan(order=('right', 'left')), source=tmp_path/'omitted', step=2) as host:
        threads, provider, driver = driver_for(host)
        left, right = [host.service(name+'.data', 'notifications') for name in ('left', 'right')]
        left.publish('a', '左第四条')
        right.publish('a', '右第四条')
        current = session(driver, step=3)
        await driver.run(current)
        assert current.cursors['thread_id'] == tid
        assert threads.input_cursor(tid, 'operating_context') == {'position': {'left.v1': 4, 'right.v1': 4}}
        texts = request_texts(provider.requests[-1])
        for text in (*original.values(), '左第二条', '右第二条', '左第三条', '右积累第三条', '左第四条', '右第四条'):
            assert sum(text in message for message in texts) == 1
        assert next(i for i, text in enumerate(texts) if '右第四条' in text) < next(i for i, text in enumerate(texts) if '左第四条' in text)
        assert (tid, 3) in right.receipts('a') and (tid, 4) in right.receipts('a')
        host.service('storage', 'store').complete(3)


@pytest.mark.asyncio
async def test_cog_13_new_position_semantics_use_new_id_and_keep_old_key_and_history(tmp_path):
    async with compose(tmp_path/'run', notification_plan()) as host:
        threads, provider, driver = driver_for(host)
        for name in ('left', 'right'):
            host.service(name+'.data', 'notifications').publish('a', name+'原始正文')
        current = session(driver)
        await driver.run(current)
        tid = current.cursors['thread_id']
        prefix = threads.read_messages(tid)
        host.service('storage', 'store').complete(1)
    received = []
    def install(ctx):
        data = ctx.require('right.data', 'notifications')
        def read(current, previous):
            received.append(previous)
            after = 0 if previous is None else previous.position['through']
            rows = data.since(current.actor.id, after)
            # 新 ID 是独立的只读格式来源，旧通知保存回执保持原义。
            return SourceBatch([{'role': 'user', 'content': '位置格式v2:'+body} for identifier, body in rows],
                               {'through': rows[-1][0] if rows else after})
        ctx.provide('source', read)
    plugins = notification_plan(('left',))
    plugins[-1] = cognition_plugin(sources=(SourceSpec('left.v1', ('left.source', 'source')), SourceSpec('right.v2', ('v2', 'source'))))
    plugins.append(Plugin('v2', requires=('right.data',), install=install))
    async with compose(tmp_path/'changed', plugins, source=tmp_path/'run', step=1) as host:
        threads, provider, driver = driver_for(host)
        right = host.service('right.data', 'notifications')
        right.publish('a', 'right新增正文')
        current = session(driver, step=2)
        await driver.run(current)
        assert current.cursors['thread_id'] == tid and received == [None]
        assert threads.read_messages(tid)[:len(prefix)] == prefix
        assert threads.input_cursor(tid, 'operating_context') == {'position': {'left.v1': 1, 'right.v1': 1, 'right.v2': {'through': 2}}}
        assert right.receipts('a') == [(tid, 1)]


@pytest.mark.asyncio
@pytest.mark.parametrize('callback_failure', ['first_before', 'first_after', 'second_before', 'second_after'])
@pytest.mark.parametrize('failure_kind', ['error', 'cancel'])
async def test_cog_10_callback_failure_recovers_both_positions_and_receipts_from_complete(tmp_path, callback_failure, failure_kind):
    async with compose(tmp_path/'run', notification_plan()) as host:
        threads, provider, driver = driver_for(host)
        left, right = [host.service(name+'.data', 'notifications') for name in ('left', 'right')]
        left.publish('a', '左可信初始原文')
        right.publish('a', '右可信初始原文')
        current = session(driver)
        await driver.run(current)
        tid = current.cursors['thread_id']
        before = threads.read_messages(tid)
        host.service('storage', 'store').complete(1)
        left.publish('a', '左失败步骤正文')
        right.publish('a', '右失败步骤正文')
        faulty_name, timing = callback_failure.split('_')
        failure_type = ValueError if failure_kind == 'error' else asyncio.CancelledError
        service = host.service(('left' if faulty_name == 'first' else 'right')+'.source', 'source')
        original_read = service.__call__
        def read(current, previous):
            batch = original_read(current, previous)
            async def effect(context):
                if timing == 'before':
                    raise failure_type('callback failed before receipt')
                for callback in batch.effects:
                    callback(context)
                raise failure_type('callback failed after receipt')
            return replace(batch, effects=(effect,))
        # 新组合器保持本次测试原顺序，只有已装配服务的行为被注入故障。
        sources = tuple((name+'.v1', read if name == ('left' if faulty_name == 'first' else 'right') else host.service(name+'.source', 'source')) for name in ('left', 'right'))
        failed = LLMDriver(provider, threads, input_builder=ComposedInput(threads, CompositePerception(sources)))
        with pytest.raises(failure_type, match='callback failed'):
            await failed.run(session(failed, step=2))
        assert host.service('storage', 'store').complete_step == 1
    async with compose(tmp_path/'restored', notification_plan(), source=tmp_path/'run', step=1) as host:
        threads, provider, driver = driver_for(host)
        assert threads.read_messages(tid) == before
        assert threads.input_cursor(tid, 'operating_context') == {'position': {'left.v1': 1, 'right.v1': 1}}
        for name in ('left', 'right'):
            assert host.service(name+'.data', 'notifications').receipts('a') == [(tid, 1)]
        await driver.run(session(driver, step=2))
        assert not any('失败步骤正文' in text for text in request_texts(provider.requests[-1]))


@pytest.mark.asyncio
async def test_cog_11_ordered_extensions_and_actual_memory_see_full_composed_input(tmp_path):
    from contextlib import asynccontextmanager
    from society0.kernel.memory import MemoryPolicy
    from society0.kernel.services import memory_plugin
    from tests.primary.test_kernel_memory import Client, Embed

    events = []
    def recall_query(current):
        context = current.cursors['activation']
        contents = [message['content'] for message in context.inputs.messages]
        assert 'left完整来源' in contents and 'right完整来源' in contents
        events.append('prepare.memory')
        return '完整召回查询'
    def resources(ctx):
        ctx.provide('client', Client())
        ctx.provide('embeddings', {'main': SimpleNamespace(embed=Embed())})
    plugins = [*data_plugins(), Plugin('resources', install=resources),
               memory_plugin(client=('resources', 'client'), embedding=('resources', 'embeddings', 'main'),
                             policy=MemoryPolicy(auto_write=False, auto_recall=True, active_tools=False), recall_query=recall_query)]
    async with compose(tmp_path/'run', plugins) as host:
        threads = host.service('threads', 'threads')
        memory = host.service('memory', 'memory')
        job = memory.prepare_job('a', None, 'seed', timestamp=0, entries=[{'content': '完整历史记忆🙂'*500}])
        await memory.finish_job(job)
        def source(name):
            return lambda current, previous: SourceBatch([{'role': 'user', 'content': name+'完整来源'}], 1,
                effects=(lambda context: events.append('effect.'+name),))
        @asynccontextmanager
        async def extension(context, name):
            events.append('enter.'+name)
            context.messages.append({'role': 'user', 'content': name+'完整扩展'})
            def prepare(current):
                assert len(current.inputs.cursor['position']) == 2
                events.append('prepare.'+name)
            context.preparations.append(prepare)
            try:
                yield
            finally:
                events.append('exit.'+name)
        @asynccontextmanager
        async def memory_extension(context):
            events.append('enter.memory')
            try:
                async with host.service('memory', 'extension')(context):
                    yield
            finally:
                events.append('exit.memory')
        provider = CapturingProvider(threads)
        builder = ComposedInput(threads, CompositePerception((('left', source('left')), ('right', source('right')))))
        driver = LLMDriver(provider, threads, input_builder=builder,
                           extensions=(lambda context: extension(context, 'one'), lambda context: extension(context, 'two'), memory_extension))
        await driver.run(session(driver))
        assert events == ['enter.one', 'enter.two', 'enter.memory', 'prepare.one', 'prepare.two', 'prepare.memory',
                          'effect.left', 'effect.right', 'exit.memory', 'exit.two', 'exit.one']
        texts = request_texts(provider.requests[0])
        for complete in ('left完整来源', 'right完整来源', 'one完整扩展', 'two完整扩展', '完整历史记忆🙂'*500):
            assert any(complete in text for text in texts)


def test_cog_16_cognition_import_keeps_rule_public_path_free_of_optional_dependencies(tmp_path):
    import os
    import subprocess
    import sys
    code = '''
import builtins, asyncio, sys
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith(('pydantic_ai', 'society0.kernel.llm', 'society0.kernel.memory', 'society0.plugins.social')):
        raise AssertionError('optional import: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
from society0.plugins.cognition import SourceSpec, CompositePerception, cognition_plugin
from examples.core_next.rule_run import build
from society0.kernel.runner import run_plan
plan = build({'start': 1, 'end': 1, 'release': {'commit': 'deterministic-test'}})
result = asyncio.run(run_plan(sys.argv[1], plan))
assert result['complete_step'] == 1 and result['status'] == 'completed'
'''
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path/'run')],
                            env={**os.environ, 'SOCIETY0_RUN_REAL_E2E': '0', 'SOCIETY0_RUN_CORE_REAL': '0'},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.asyncio
async def test_cog_16_notice_increment_and_composer_do_not_scan_saved_thread_history(tmp_path, monkeypatch):
    measured = []
    body = '一条完整新增通知🙂'*1000
    for history in (100, 10000):
        async with compose(tmp_path/str(history), notification_plan(('left',))) as host:
            threads = host.service('threads', 'threads')
            data = host.service('left.data', 'notifications')
            data.store.transaction(lambda writer: writer.executemany(
                f'INSERT INTO {data.table("notices")} VALUES(?,?,?)', ((identifier, 'a', b'old') for identifier in range(1, history+1))))
            data.publish('a', body)
            tid = threads.open('a', Moment(1, 'A'), 'decision')
            threads.append_input(tid, [{'role': 'user', 'content': '此前完整历史'} for _ in range(history)],
                                 'operating_context', {'position': {'left.v1': history}})
            def forbidden(*args, **kwargs):
                raise AssertionError('composer must not scan complete Thread')
            monkeypatch.setattr(threads, 'read_messages', forbidden)
            vm = [0]
            original_read = data.store.read
            def read(callback, **kwargs):
                def run(view):
                    view._connection.set_progress_handler(lambda: vm.__setitem__(0, vm[0]+1) or False, 1)
                    try:
                        return callback(view)
                    finally:
                        view._connection.set_progress_handler(None, 0)
                return original_read(run, **kwargs)
            monkeypatch.setattr(data.store, 'read', read)
            current = session(None)
            current.cursors['thread_id'] = tid
            batch = await host.service('cognition', 'input_builder')(current)
            measured.append(vm[0])
            assert len(batch.messages) == 2 and batch.messages[-1]['content'].endswith(body)
            assert batch.cursor == {'position': {'left.v1': history+1}}
            assert data.receipts('a') == []
    print({'notice_thread_history': [100, 10000], 'increment_composer_sql_vm': measured})
    assert measured[1] <= measured[0] + 10


@pytest.mark.asyncio
async def test_cog_12_shared_builder_keeps_interleaved_actors_effects_and_positions_local(tmp_path):
    a_entered, b_entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    effects = []
    async def read(current, previous):
        actor = current.actor.id
        (a_entered if actor == 'a' else b_entered).set()
        await release.wait()
        return SourceBatch([{'role': 'user', 'content': '来源原文:' + actor}], actor,
                           effects=(lambda context: effects.append((context.session.actor.id, actor)),))
    async with compose(tmp_path/'run', data_plugins()) as host:
        threads = host.service('threads', 'threads')
        provider = CapturingProvider(threads)
        builder = ComposedInput(threads, CompositePerception((('shared', read),)))
        driver = LLMDriver(provider, threads, input_builder=builder)
        currents = [session(driver, actor=actor) for actor in 'ab']
        tasks = [asyncio.create_task(driver.run(current)) for current in currents]
        try:
            await asyncio.wait_for(asyncio.gather(a_entered.wait(), b_entered.wait()), 10)
            release.set()
            await asyncio.gather(*tasks)
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        assert sorted(effects) == [('a', 'a'), ('b', 'b')]
        for current in currents:
            actor = current.actor.id
            assert threads.input_cursor(current.cursors['thread_id'], 'operating_context') == {'position': {'shared': actor}}
            texts = [m['content'] for m in threads.read_messages(current.cursors['thread_id'])]
            assert '来源原文:' + actor in texts
            assert '来源原文:' + ('b' if actor == 'a' else 'a') not in texts
