"""独立审查：真实 Driver 的输入选择、增量游标及共同协议反例。"""
import copy
import json
import subprocess
import sys
from pathlib import Path
from dataclasses import replace

import pytest

from society0 import Phase, Plugin, compose
from society0.kernel.cognition import InputBatch
from society0.kernel.interaction import Moment
from tests.primary.test_social_cognition import plugins
from tests.reference.basic_env_oracle_protocol import ROOT, assert_equal, clean_env, run_legacy, social_semantics


@pytest.mark.asyncio
async def test_discarded_cognitive_batch_does_not_expose_unpresented_posts(tmp_path):
    providers = []
    configured = plugins(providers)

    def install(ctx):
        social_input = ctx.require('social.cognition', 'input_builder')

        async def select_input(session):
            # 多机制选择器先构建候选认知，当前激活最终选择别的机制。
            await social_input(session)
            return InputBatch([{'role': 'user', 'content': '本次仅处理其他机制'}],
                              'other-mechanism', {'position': 1})

        ctx.provide('builder', select_input)

    configured.append(Plugin('selector', ('social.cognition',), install))
    driver_index = next(i for i, p in enumerate(configured) if p.name == 'driver')
    from society0.plugins import llm_driver_plugin
    configured[driver_index] = llm_driver_plugin(
        provider=('provider', 'provider'), input_builder=('selector', 'builder'), name='driver')
    async with compose(tmp_path / 'selected', configured) as host:
        social = host.service('social', 'mechanism')
        post = social.execute('publish_post', 'b', 'b', {'content': '未选中的秘密正文'}, 0).value['post_id']
        await host.service('runtime', 'runtime').run_step(1, 1, [Phase('read', lambda ctx: ctx.activate('a'))])
        messages = providers[0].requests[0][2]
        assert all('未选中的秘密正文' not in str(m) for m in messages)
        assert social.post_details(post)['view_count'] == 0
        assert social.recommended_ids('a') == []


@pytest.mark.asyncio
async def test_same_moment_metadata_order_and_full_history_persist(tmp_path):
    providers = []
    async with compose(tmp_path / 'run', plugins(providers)) as host:
        social = host.service('social', 'mechanism')
        first = social.execute('publish_post', 'b', 'b', {'content': '第一篇完整原文🙂' * 500}, 0).value['post_id']
        second = social.execute('publish_post', 'c', 'c', {'content': '第二篇完整原文'}, 0).value['post_id']

        async def twice(ctx):
            ctx.activate('a')
            await ctx.drain()
            social.execute('like_post', 'c', first, {}, 1)
            ctx.activate('a')
            await ctx.drain()

        await host.service('runtime', 'runtime').run_step(1, 1, [Phase('read', twice)])
        threads = host.service('threads', 'threads')
        tid = threads.find('a', Moment(1, 'read'))
        history = threads.read_messages(tid)
        payloads = [json.loads(m['content'].split('\n', 1)[1]) for m in history
                    if m['role'] == 'user' and m['content'].startswith('推荐动态\n')]
        assert payloads[0]['recommended_ids'] == [second, first]
        assert payloads[1]['recommended_ids'] == [first, second]
        assert payloads[1]['new_posts'] == []
        assert any(p['post_id'] == first and p['like_count'] == 1 for p in payloads[1]['updated_posts'])
        cursor = threads.input_cursor(tid, 'social.cognition')
        assert cursor['position']['recommended'] == [first, second]
        assert len(providers[0].requests[1][2]) > len(providers[0].requests[0][2])
    async with compose(tmp_path / 'restore', plugins(providers), source=tmp_path / 'run') as host:
        threads = host.service('threads', 'threads')
        assert threads.input_cursor(tid, 'social.cognition') == cursor
        assert threads.read_messages(tid) == history
        await host.service('runtime', 'runtime').run_step(2, 1, [Phase('read', lambda ctx: ctx.activate('a'))])
        after = threads.read_messages(tid)
        assert after[:len(history)] == history
        assert sum('第一篇完整原文🙂' in str(m) for m in after) == 1
        assert host.service('social', 'mechanism').post_details(first)['view_count'] == 1


async def _cognitive_process(directory, source=None):
    providers = []
    async with compose(Path(directory), plugins(providers), source=Path(source) if source else None) as host:
        social = host.service('social', 'mechanism')
        threads = host.service('threads', 'threads')
        if source:
            tid = threads.find('a', Moment(1, 'read'))
            history = threads.read_messages(tid)
            assert social.post_details('post_1')['view_count'] == 1
        new = social.execute('publish_post', 'b', 'b',
                             {'content': '进程二新增全文' if source else '进程一旧帖全文'}, 1).value['post_id']
        await host.service('runtime', 'runtime').run_step(2 if source else 1, 1,
            [Phase('read', lambda ctx: ctx.activate('a'))])
        assert social.post_details(new)['view_count'] == 1
        if source:
            after = threads.read_messages(tid)
            assert after[:len(history)] == history
            assert sum('进程一旧帖全文' in str(m) for m in after) == 1
            assert sum('进程二新增全文' in str(m) for m in after) == 1
            assert social.post_details('post_1')['view_count'] == 1
            assert set(social.recommended_ids('a')) == {'post_1', 'post_2'}


def test_cognitive_thread_and_exposure_restore_in_new_process(tmp_path):
    script = ('import asyncio,sys; '
              'from tests.primary.test_cognition_oracle_review import _cognitive_process; '
              'asyncio.run(_cognitive_process(*sys.argv[1:]))')
    env = clean_env(str(ROOT / 'src') + ':' + str(ROOT))
    for arguments in ([str(tmp_path / 'first')], [str(tmp_path / 'second'), str(tmp_path / 'first')]):
        result = subprocess.run([sys.executable, '-c', script, *arguments],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'serialization', 'effect'])
async def test_composed_receipt_effect_and_complete_boundary(tmp_path, failure):
    providers = []
    observed = []

    def configured():
        result = plugins(providers)

        def install(ctx):
            social_input = ctx.require('social.cognition', 'input_builder')
            store = ctx.require('storage', 'store')
            threads = ctx.require('threads', 'threads')

            async def build(session):
                batch = await social_input(session)
                messages = [*batch.messages, {'role': 'user', 'content': '独立通知原文'}]
                if failure == 'serialization':
                    messages.append({'role': 'user', 'content': object()})

                async def acknowledge(context):
                    context.session.scope.check_active()
                    assert threads.input_cursor(context.thread_id, batch.consumer) == batch.cursor
                    assert any(m.get('content') == '独立通知原文' for m in threads.read_messages(context.thread_id))
                    assert providers[-1].requests == []
                    store.transaction(lambda w: w.execute('INSERT INTO review_receipts VALUES(?)', (session.actor.id,)))
                    observed.append('receipt')
                    if failure == 'effect':
                        raise RuntimeError('回执落盘后故障')

                # replace保留社交批次的材料与曝光效果；追加另一个持久回执消费者。
                return replace(batch, messages=messages, effects=(*batch.effects, acknowledge))

            ctx.provide('builder', build)

        result.append(Plugin('receipts', ('social.cognition', 'storage', 'threads'), install,
            schema=('CREATE TABLE review_receipts(actor TEXT PRIMARY KEY NOT NULL)',)))
        from society0.plugins import llm_driver_plugin
        index = next(i for i, p in enumerate(result) if p.name == 'driver')
        result[index] = llm_driver_plugin(provider=('provider', 'provider'),
            input_builder=('receipts', 'builder'), name='driver')
        return result

    async with compose(tmp_path / 'run', configured()) as host:
        store = host.service('storage', 'store')
        social = host.service('social', 'mechanism')
        post = social.execute('publish_post', 'b', 'b', {'content': '联合输入帖子'}, 0).value['post_id']
        runtime = host.service('runtime', 'runtime')
        await runtime.run_step(1, 0, [])
        if failure:
            with pytest.raises(TypeError if failure == 'serialization' else RuntimeError):
                await runtime.run_step(2, 1, [Phase('read', lambda ctx: ctx.activate('a'))])
            assert store.complete_step == 1
            assert providers[-1].requests == []
        else:
            await runtime.run_step(2, 1, [Phase('read', lambda ctx: ctx.activate('a'))])
            assert store.complete_step == 2
            assert len(providers[-1].requests) == 1
        assert observed == ([] if failure == 'serialization' else ['receipt'])
    async with compose(tmp_path / 'restore', configured(), source=tmp_path / 'run') as host:
        receipts = host.service('storage', 'store').read(lambda r: r.query('SELECT actor FROM review_receipts'))
        assert receipts == ([] if failure else [('a',)])
        social = host.service('social', 'mechanism')
        assert social.post_details(post)['view_count'] == (0 if failure else 1)
        assert social.recommended_ids('a') == ([] if failure else [post])
        threads = host.service('threads', 'threads')
        tid = threads.find('a', Moment(1, 'read'))
        assert (tid is None) == bool(failure)


def test_common_social_protocol_detects_changed_action_outcome(tmp_path):
    recorded = run_legacy(tmp_path / 'oracle')['scenarios']['social_network']
    damaged = copy.deepcopy(recorded)
    damaged['actions'][0]['result'] = 'Not following a: deliberately corrupted outcome'
    with pytest.raises(AssertionError):
        assert_equal(social_semantics(recorded, legacy=True), social_semantics(damaged, legacy=True))
