"""恢复后路由亲和：已记录端点的 Thread 不得静默改投其他端点。
"""
import asyncio

import pytest

from society0.kernel.models import ModelProvider, ProviderFailure
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.provider_http import bind_chat
from tests.primary.test_kernel_models import endpoint


def chat():
    async def create(**kwargs):
        from openai.types.chat import ChatCompletion
        return ChatCompletion(id='call', created=0, model='m', object='chat.completion',
                              choices=[{'index': 0, 'finish_reason': 'stop',
                                        'message': {'role': 'assistant', 'content': 'done'}}])
    return create


@pytest.mark.asyncio
async def test_route_reorder_reports_unavailable_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr('society0.kernel.threads.random.random', lambda: .5)
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': 'x'})
        provider = ModelProvider([endpoint(id='left'), endpoint(id='right')], threads)
        provider.bind_scope('models.decision')
        # 抽样点落在右端点；两个端点都用同一条离线传输，不依赖未绑定的连接。
        await bind_chat(provider, chat())
        await bind_chat(provider, chat(), endpoint=1)
        try:
            await asyncio.wait_for(provider.request(tid, {}), 5)
            assert threads.last_provider_endpoint(tid, provider='models.decision') == 'right'
            # 端点顺序改变会让同一抽样点落到别处；这里明确失败而非静默改投。
            reordered = ModelProvider([endpoint(id='right'), endpoint(id='left'), endpoint(id='extra')], threads)
            reordered.bind_scope('models.decision')
            await bind_chat(reordered, chat())
            with pytest.raises(ProviderFailure) as caught:
                reordered.verify_route(tid)
            assert caught.value.reason == 'model_route_unavailable'
            await reordered.close()
        finally:
            await provider.close()

@pytest.mark.asyncio
async def test_other_provider_response_on_same_thread_is_not_route_drift(tmp_path, monkeypatch):
    """记忆提取或同 Moment 显式换提供方沿用同一 Thread，不算决策路由漂移。"""
    monkeypatch.setattr('society0.kernel.threads.random.random', lambda: .5)
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': 'x'})
        decision = ModelProvider([endpoint(id='left'), endpoint(id='right')], threads)
        decision.bind_scope('models.decision')
        extract = ModelProvider([endpoint(id='extract')], threads)
        extract.bind_scope('models.extraction')
        await bind_chat(decision, chat())
        await bind_chat(decision, chat(), endpoint=1)
        await bind_chat(extract, chat())
        try:
            await asyncio.wait_for(decision.request(tid, {}), 5)
            assert threads.last_provider_endpoint(tid, provider='models.decision') == 'right'
            # 提取提供方在同一个 decision Thread 追加一次物理响应。
            await asyncio.wait_for(extract.request(tid, {}), 5)
            assert threads.last_provider_endpoint(tid, provider='models.extraction') == 'extract'
            # 决策提供方续会仍然亲和：只在本提供方端点集合里找证据。
            assert decision.verify_route(tid).id == 'right'
            assert extract.verify_route(tid).id == 'extract'
        finally:
            await decision.close()
            await extract.close()

@pytest.mark.asyncio
async def test_removed_endpoint_is_reported_not_silently_rerouted(tmp_path, monkeypatch):
    """旧端点被新配置删除时，投影仍保留旧 id，必须明确报告而不是改投。"""
    monkeypatch.setattr('society0.kernel.threads.random.random', lambda: .5)
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': 'x'})
        original = ModelProvider([endpoint(id='left'), endpoint(id='right')], threads)
        original.bind_scope('models.decision')
        await bind_chat(original, chat())
        await bind_chat(original, chat(), endpoint=1)
        try:
            await asyncio.wait_for(original.request(tid, {}), 5)
            assert threads.last_provider_endpoint(tid, provider='models.decision') == 'right'
            # 新配置只剩另一个端点：同一抽样点必然落到别处，这里必须报告。
            replaced = ModelProvider([endpoint(id='other')], threads)
            replaced.bind_scope('models.decision')
            await bind_chat(replaced, chat())
            with pytest.raises(ProviderFailure) as caught:
                replaced.verify_route(tid)
            assert caught.value.reason == 'model_route_unavailable'
            await replaced.close()
        finally:
            await original.close()


@pytest.mark.asyncio
async def test_unnamed_provider_keeps_one_row_and_rejects_empty_scope(tmp_path, monkeypatch):
    """未声明作用域的单提供方沿用原合同；作用域身份必须是非空字符串。"""
    monkeypatch.setattr('society0.kernel.threads.random.random', lambda: .5)
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': 'x'})
        provider = ModelProvider([endpoint(id='left')], threads)
        await bind_chat(provider, chat())
        try:
            await asyncio.wait_for(provider.request(tid, {}), 5)
            assert threads.last_provider_endpoint(tid) == 'left'
            assert provider.verify_route(tid).id == 'left'
            with pytest.raises(ValueError):
                provider.bind_scope('')
        finally:
            await provider.close()
