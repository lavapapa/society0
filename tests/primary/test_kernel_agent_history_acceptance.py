"""独立消费者：SDK 主循环借用完整历史，恢复后重复行动沿用回执。"""
from types import SimpleNamespace

import pytest

from society0.kernel.interaction import InteractionScope, Moment
from society0.kernel.llm import LLMDriver
from society0.kernel.models import ModelProvider
from society0.kernel.runtime import Actor, Session
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore
from tests.primary.provider_http import bind_chat
from tests.primary.test_kernel_llm import invoke, setup


@pytest.mark.asyncio
async def test_agent_full_history_materializes_once_and_restored_receipt_runs_once(tmp_path, monkeypatch):
    store, threads, _, driver, session, effects = setup(tmp_path, [])
    tid = threads.open('a', session.moment, 'decision')
    original = [{'role': 'user', 'content': '完整原文🙂' * 10000} for _ in range(20)]
    for message in original:
        threads.append_message(tid, message)
    threads.close(tid, 'completed')
    endpoint = {'id': 'test', 'model': 'fake', 'api_key': 'unused',
                'base_url': 'http://unused.invalid/v1', 'trust_env': False, 'concurrency': 1}
    transmissions, materializations, borrowed = [], [], []

    async def exercise(current_threads, current_driver, current_session):
        provider = ModelProvider([endpoint], current_threads, max_attempts=1)
        current_driver.provider = provider
        native_snapshot = current_threads.snapshot_messages
        native_request = provider.request_model

        def snapshot(*args, **kwargs):
            materializations.append(current_threads)
            return native_snapshot(*args, **kwargs)

        async def request(*args, model_messages=None, **kwargs):
            assert model_messages is not None
            borrowed.append(model_messages)
            return await native_request(*args, model_messages=model_messages, **kwargs)

        monkeypatch.setattr(current_threads, 'snapshot_messages', snapshot)
        monkeypatch.setattr(provider, 'request_model', request)
        count = 0

        async def respond(**wire):
            nonlocal count
            transmissions.append(wire['messages'])
            count += 1
            message = {'role': 'assistant', 'content': ''}
            if count == 1:
                message['tool_calls'] = [invoke('stable')]
            else:
                message['content'] = '结束完整原文🙂'
            return {'id': 'response', 'object': 'chat.completion', 'created': 0, 'model': 'fake',
                    'choices': [{'index': 0, 'finish_reason': 'tool_calls' if count == 1 else 'stop',
                                 'message': message}]}

        await bind_chat(provider, respond)
        try:
            assert (await current_driver.run(current_session)).status == 'completed'
        finally:
            await provider.close()

    try:
        await exercise(threads, driver, session)
        before = threads.snapshot_messages(tid, raw=True)['messages']
        provider_session = threads.describe(tid)['provider_session_id']
        store.complete(1)
    finally:
        store.close()

    with StageStore.restore(tmp_path / 'run', tmp_path / 'restored', step=1) as restored:
        recovered = ThreadStore(restored)
        assert recovered.snapshot_messages(tid, raw=True)['messages'] == before
        resumed_driver = LLMDriver(None, recovered, input_builder=lambda session: [])
        resumed_session = Session(Actor('a', resumed_driver), InteractionScope('a', Moment(1, 'p')),
                                  session.information, session.actions, {}, None, (),
                                  SimpleNamespace(prepare_artifact=restored.prepare_artifact), step=1)
        await exercise(recovered, resumed_driver, resumed_session)
        assert resumed_session.cursors['thread_id'] == tid
        assert recovered.describe(tid)['provider_session_id'] == provider_session
        assert recovered.snapshot_messages(tid, raw=True)['messages'][:len(before)] == before

    # 下面两次 snapshot 是测试自己的原文核对；产品每次激活各物化一次。
    assert materializations.count(threads) == materializations.count(recovered) == 2
    assert len(transmissions) == len(borrowed) == 4
    for wire in transmissions:
        assert wire[:len(original)] == original
    assert transmissions[1][:len(transmissions[0])] == transmissions[0]
    assert transmissions[3][:len(transmissions[2])] == transmissions[2]
    assert len(effects) == 1
