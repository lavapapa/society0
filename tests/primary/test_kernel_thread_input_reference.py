"""请求输入引用：prefix/suffix 持久化、read_request 重建、被引用正文不重复累计用量。
"""
import asyncio

import pytest

from society0.kernel.models import ModelProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.provider_http import bind_chat
from tests.primary.test_kernel_models import endpoint


def chat_stub(text):
    async def create(**kwargs):
        from openai.types.chat import ChatCompletion
        return ChatCompletion(id='call', created=0, model='m', object='chat.completion',
                              choices=[{'index': 0, 'finish_reason': 'stop',
                                        'message': {'role': 'assistant', 'content': text}}])
    return create


@pytest.mark.asyncio
async def test_prefix_and_suffix_references_rebuild_request_and_keep_one_body(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        decision = threads.open('a', 0, 'decision', model_route=.5)
        threads.append_message(decision, {'role': 'system', 'content': '决策完整前缀'})
        threads.append_message(decision, {'role': 'user', 'content': '决策完整当前输入'})
        through = threads.describe(decision)['last_seq']
        helper = threads.open('a', 0, 'memory_recall_query', model_route=threads.model_route(decision))
        threads.append_message(helper, {'role': 'system', 'content': '辅助指令'})
        suffix_through = threads.describe(helper)['last_seq']
        provider = ModelProvider([endpoint(id='m')], threads)
        await bind_chat(provider, chat_stub('{"query":"查历史"}'))
        try:
            await asyncio.wait_for(provider.request_model(
                helper, {'max_tokens': 16},
                prefix={'thread_id': decision, 'after': 0, 'through': through},
                suffix={'thread_id': helper, 'after': 0, 'through': suffix_through}), 5)
            request = next(event for event in threads.tail(helper, limit=100)['items'] if event['kind'] == 'request')
            recorded = threads.read_request(helper, request['seq'])
            assert recorded['prefix'] == {'thread_id': decision, 'after': 0, 'through': through}
            assert recorded['suffix'] == {'thread_id': helper, 'after': 0, 'through': suffix_through}
            contents = [message.get('content') for message in recorded['messages']]
            assert '决策完整前缀' in contents and '辅助指令' in contents
            # 同一段前缀在决策 Thread 只存一份；辅助 Thread 只追加自己的指令与实际响应，
            # 不复制被引用的前缀正文。
            own = threads.snapshot_messages(helper, raw=True)['messages']
            assert own[0] == {'role': 'system', 'content': '辅助指令'}
            assert not [message for message in own if '决策完整' in str(message)]
        finally:
            await provider.close()


@pytest.mark.asyncio
async def test_open_rejects_route_outside_unit_interval(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        with pytest.raises(ValueError):
            threads.open('a', 0, 'decision', model_route=1.)
        assert threads.open('a', 0, 'decision', model_route=.25) is not None
        assert threads.model_route(threads.find('a', 0)) == .25


@pytest.mark.asyncio
async def test_anchor_failure_leaves_no_request_event(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        decision = threads.open('a', 0, 'decision')
        threads.append_message(decision, {'role': 'user', 'content': 'x'})
        provider = ModelProvider([endpoint(id='m')], threads)
        await bind_chat(provider, chat_stub('ok'))
        try:
            with pytest.raises(ValueError):
                await asyncio.wait_for(provider.request_model(
                    decision, {}, prefix={'thread_id': decision, 'after': 0, 'through': 99}), 5)
            assert not [event for event in threads.tail(decision, limit=100)['items']
                        if event['kind'] == 'request']
        finally:
            await provider.close()


@pytest.mark.asyncio
async def test_usage_counts_the_auxiliary_request_only_once(tmp_path):
    """被引用前缀正文不重复累计历史用量；辅助请求的物理调用单独计数。"""
    from society0.kernel import usage
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        decision = threads.open('a', 0, 'decision')
        threads.append_message(decision, {'role': 'system', 'content': '前缀原文'})
        helper = threads.open('a', 0, 'memory_recall_query')
        threads.append_message(helper, {'role': 'user', 'content': '辅助指令'})
        provider = ModelProvider([endpoint(id='m')], threads)
        await bind_chat(provider, chat_stub('{"query":"查历史"}'))
        try:
            await asyncio.wait_for(provider.request_model(
                helper, {}, prefix={'thread_id': decision, 'after': 0, 'through': 1},
                suffix={'thread_id': helper, 'after': 0, 'through': 1}), 5)
            totals = store.read(lambda view: usage.read(view))
            assert totals['totals']['requests'] == 1
            assert totals['totals']['responses'] == 1
            records = store.read(lambda view: view.query('SELECT id,model FROM thread_usage_calls'))
            assert len(records) == 1
        finally:
            await provider.close()

@pytest.mark.asyncio
async def test_reference_beyond_watermark_is_caller_error_before_write(tmp_path):
    """越界引用属于调用方输入错误，在写入前按当前快照报出，并且不留请求事件。"""
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        decision = threads.open('a', 0, 'decision')
        threads.append_message(decision, {'role': 'user', 'content': 'x'})
        with pytest.raises(ValueError):
            threads.record_request(decision, provider_options={}, physical_request_id='p',
                                   prefix={'thread_id': decision, 'after': 0, 'through': 99})
        assert not [event for event in threads.tail(decision, limit=100)['items']
                    if event['kind'] == 'request']
