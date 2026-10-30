"""物理请求的短计时边界：本地许可、组装、首流事件、结束与落库。"""
import httpx2
import pytest

from society0.kernel import usage
from society0.kernel.models import ModelProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.provider_http import bind_chat

CONFIG = {'id': 'offline', 'model': 'fake', 'api_key': 'unused', 'base_url': 'http://unused.invalid/v1',
          'trust_env': False, 'concurrency': 1}


def answer(content='done'):
    return {'id': 'response', 'object': 'chat.completion', 'created': 0, 'model': 'fake',
            'choices': [{'index': 0, 'finish_reason': 'stop',
                         'message': {'role': 'assistant', 'content': content}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12,
                      'prompt_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 3}}}


@pytest.mark.asyncio
async def test_attempt_boundaries_tile_the_request_and_persist_is_one_short_event(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': '完整输入'})
        provider = ModelProvider([CONFIG], threads, max_attempts=1,
                                 request_options={'openai_continuous_usage_stats': True})
        async def respond(**wire): return answer()
        await bind_chat(provider, respond)
        try:
            assert (await provider.request(tid, {}))['content'] == 'done'
        finally:
            await provider.close()
        items = threads.tail(tid)['items']
        response = next(item for item in items if item['kind'] == 'provider_response')
        timing = response['payload']['payload']['timing']
        for name in ('queue_s', 'jitter_s', 'prepare_s', 'record_s', 'first_event_s', 'first_output_s',
                     'provider_s', 'duration_s'):
            assert timing[name] >= 0, name
        # 各段顺序相加不超过本次尝试总时长，说明边界只切分同一区间而没有重复计量。
        assert timing['duration_s'] >= sum(timing[name] for name in
            ('queue_s', 'jitter_s', 'prepare_s', 'record_s', 'provider_s'))
        assert timing['first_event_s'] <= timing['provider_s']
        assert timing['first_output_s'] <= timing['provider_s']
        persisted = [item for item in items if item['kind'] == 'provider_persist']
        assert len(persisted) == 1
        assert persisted[0]['payload']['physical_request_id'] == response['payload']['physical_request_id']
        assert persisted[0]['payload']['persist_s'] >= 0
        counts = store.read(usage.read)['totals']
        assert counts['requests'] == counts['responses'] == 1 and counts['errors'] == 0
        assert counts['total_tokens'] == 12
        assert counts['cache_read_reports'] == 1 and counts['cache_read_tokens'] == 0
        # 计时事件不是消息，也不改写历史或消息计数。
        assert len(threads.read_messages(tid)) == 2
        assert threads.describe(tid)['message_count'] == 2
        store.complete(1)


@pytest.mark.asyncio
async def test_stream_without_effective_payload_records_no_first_output(tmp_path):
    # 安装版 SDK 丢弃只带 role 或只带 finish 的 chunk；空正文响应因此可以没有任何流事件。
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': '完整输入'})
        provider = ModelProvider([CONFIG], threads, max_attempts=1)
        async def respond(**wire): return answer(content='')
        await bind_chat(provider, respond)
        try:
            assert (await provider.request(tid, {}))['content'] == ''
        finally:
            await provider.close()
        response = next(item for item in threads.tail(tid)['items'] if item['kind'] == 'provider_response')
        timing = response['payload']['payload']['timing']
        assert 'first_output_s' not in timing and 'first_event_s' not in timing
        assert timing['provider_s'] >= 0 and timing['duration_s'] >= timing['provider_s']
        store.complete(1)


@pytest.mark.asyncio
async def test_failed_request_records_only_the_boundaries_it_reached(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': '完整输入'})
        provider = ModelProvider([CONFIG], threads, max_attempts=1)
        async def respond(**wire): return httpx2.Response(400, json={'error': {'message': 'bad'}})
        await bind_chat(provider, respond)
        try:
            with pytest.raises(Exception):
                await provider.request(tid, {})
        finally:
            await provider.close()
        failed = next(item for item in threads.tail(tid)['items'] if item['kind'] == 'provider_error')
        timing = failed['payload']['payload']['timing']
        for name in ('queue_s', 'prepare_s', 'record_s', 'provider_s', 'duration_s'):
            assert timing[name] >= 0, name
        assert 'first_event_s' not in timing and 'first_output_s' not in timing
        assert not [item for item in threads.tail(tid)['items'] if item['kind'] == 'provider_persist']
        store.complete(1)
