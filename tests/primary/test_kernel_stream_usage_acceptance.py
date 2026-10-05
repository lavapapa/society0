"""真实 SDK 消费多块 SSE：累计口径与增量口径分别留证，缺失与零分开。"""
import json

import httpx2
import pytest

from society0.kernel import usage
from society0.kernel.models import ModelProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.provider_http import bind_chat


@pytest.mark.asyncio
@pytest.mark.parametrize('continuous', [False, True])
@pytest.mark.parametrize('cache', [None, 0, 7])
async def test_multichunk_sdk_usage_preserves_configured_accounting_and_unknowns(tmp_path, continuous, cache):
    with StageStore.create(tmp_path / 'source', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': '完整原文'})
        config = {'id': 'endpoint', 'model': 'fake', 'api_key': 'unused',
                  'base_url': 'http://unused.invalid/v1', 'trust_env': False, 'concurrency': 1}
        provider = ModelProvider([config], threads, max_attempts=1,
                                 request_options={'openai_continuous_usage_stats': continuous})

        async def respond(**wire):
            assert wire['stream_options'].get('continuous_usage_stats', False) is continuous
            events = []
            inputs, outputs = ([10, 10, 10], [1, 2, 2]) if continuous else ([4, 3, 3], [1, 1, 0])
            reads = [cache] * 3 if continuous else ([None] * 3 if cache is None else ([0] * 3 if cache == 0 else [2, 3, 2]))
            writes = [3] * 3 if continuous else [1, 1, 1]
            for index, (input_tokens, output_tokens) in enumerate(zip(inputs, outputs)):
                counts = {'prompt_tokens': input_tokens, 'completion_tokens': output_tokens,
                          'total_tokens': input_tokens + output_tokens}
                if cache is not None:
                    counts['prompt_tokens_details'] = {'cached_tokens': reads[index], 'cache_write_tokens': writes[index]}
                event = {'id': 'response', 'object': 'chat.completion.chunk', 'created': 0, 'model': 'fake',
                         'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': '完整' if index == 0 else ('回复' if index == 1 else '')},
                                      'finish_reason': 'stop' if index == 2 else None}], 'usage': counts}
                events.append('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n')
            return httpx2.Response(200, headers={'content-type': 'text/event-stream'},
                                   text=''.join(events) + 'data: [DONE]\n\n')

        await bind_chat(provider, respond)
        try:
            assert (await provider.request(tid, {}))['content'] == '完整回复'
        finally:
            await provider.close()
        counts = store.read(usage.read)['totals']
        assert counts['requests'] == counts['responses'] == 1
        assert counts['input_tokens'] == 10 and counts['output_tokens'] == 2 and counts['total_tokens'] == 12
        assert counts['cache_read_tokens'] == (cache or 0)
        assert counts['cache_write_tokens'] == (3 if cache is not None else 0)
        assert counts['cache_read_reports'] == counts['cache_write_reports'] == int(cache is not None)
        assert counts['unknown_cache_read_calls'] == counts['unknown_cache_write_calls'] == int(cache is None)
        assert store.read(lambda view: usage.read(view, actor='a'))['totals'] == counts
        store.complete(1)
    with StageStore.restore(tmp_path / 'source', tmp_path / 'restored') as restored:
        assert restored.read(usage.read)['totals'] == counts
