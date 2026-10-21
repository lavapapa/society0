"""Thread 固定抽样、真实 SDK 离线传输及原请求预算。"""
import asyncio

import httpx2
import pytest

from society0.kernel.llm import LLMPolicy
from society0.kernel.models import ModelProvider, ProviderFailure
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.provider_http import bind_chat
from tests.primary.test_kernel_models import endpoint
from tests.primary.test_kernel_llm import setup, reply


@pytest.mark.asyncio
async def test_weighted_routes_survive_reopen_restore_and_provider_recreation(tmp_path, monkeypatch):
    draws = iter([0., .249999, .25, .999999])
    monkeypatch.setattr('society0.kernel.threads.random.random', lambda: next(draws))
    configs = [endpoint(id='left', weight=1), endpoint(id='right', weight=3)]
    with StageStore.create(tmp_path/'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tids = [threads.open(str(i), 0, 'decision', provider_session_id='caller-session') for i in range(4)]
        for tid in tids:
            threads.append_message(tid, {'role': 'user', 'content': tid})
        async def exercise(current):
            provider = ModelProvider(configs, current, session_transport='metadata',
                request_options={'temperature': .4, 'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}})
            seen = [[], []]
            async def left(**wire): seen[0].append(wire); return {'content': 'done'}
            async def right(**wire): seen[1].append(wire); return {'content': 'done'}
            await bind_chat(provider, left, endpoint=0)
            await bind_chat(provider, right, endpoint=1)
            try:
                for _ in range(2):
                    await asyncio.gather(*(provider.request(tid, {'max_tokens': 64}) for tid in tids))
                assert [sorted(w['messages'][0]['content'] for w in group) for group in seen] == [
                    sorted(tids[:2]*2), sorted(tids[2:]*2)]
                for group in seen:
                    for wire in group:
                        assert wire['temperature'] == .4 and wire['max_completion_tokens'] == 64
                        assert wire['chat_template_kwargs'] == {'enable_thinking': False}
                        assert wire['metadata']['session_id'] == 'caller-session'
            finally:
                await provider.close()
        await exercise(threads)
        for tid in tids:
            threads.close(tid, 'waiting')
            threads.reopen(tid)
        store.complete(1)
        with StageStore.restore(store.path, tmp_path/'restored') as restored:
            await exercise(ThreadStore(restored))


@pytest.mark.asyncio
async def test_failure_retry_and_next_request_keep_same_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr('society0.kernel.threads.random.random', lambda: .75)
    with StageStore.create(tmp_path/'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': 'complete'})
        provider = ModelProvider([endpoint(id='left'), endpoint(id='right')], threads, retry_delay=0)
        seen = []
        async def left(**wire): raise AssertionError('unexpected failover')
        async def right(**wire):
            seen.append(wire)
            if len(seen) <= 2: return httpx2.Response(503, json={'error': {'message': 'unavailable'}})
            return {'content': 'done'}
        await bind_chat(provider, left, endpoint=0)
        await bind_chat(provider, right, endpoint=1)
        try:
            with pytest.raises(ProviderFailure): await provider.request(tid, {})
            assert (await provider.request(tid, {}))['content'] == 'done'
            assert len(seen) == 3
            events = threads.tail(tid)['items']
            assert all(e['payload']['endpoint'] == 'right' for e in events if e['kind'] in ('provider_error', 'provider_response'))
        finally:
            await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('limit,expected,reason', [(None, 64, 'max_turns'), (4, 5, 'repeated_response')])
async def test_repetition_default_keeps_explicit_64_turn_budget(tmp_path, limit, expected, reason):
    policy = LLMPolicy(max_turns=64, required_names=('work',))
    assert policy.repeated_response_limit is None
    if limit is not None:
        policy = LLMPolicy(max_turns=64, required_names=('work',), repeated_response_limit=limit)
    store, threads, provider, driver, session, _ = setup(tmp_path, [reply(text='same')]*65, policy=policy)
    try:
        result = await driver.run(session)
        assert result.status == 'incomplete'
        assert len(provider.requests) == expected
        assert threads.tail(session.cursors['thread_id'], limit=1000)['items'][-1]['payload']['reason'] == reason
    finally:
        store.close()
