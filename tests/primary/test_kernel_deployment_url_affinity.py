"""同一模型的两个部署路径：Thread 抽样把每个 Thread 固定在一个原生部署 URL 上。"""
from types import SimpleNamespace
import json

import httpx2
import pytest

from society0.kernel.interaction import Actions, Information, InteractionScope, Moment
from society0.kernel.llm import LLMDriver, LLMPolicy
from society0.kernel.models import ModelProvider
from society0.kernel.runtime import Actor, Session
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.provider_http import chat_http_response

MODEL = 'Qwen3.6-35B-A3B-FP8'
BASES = ('http://127.0.0.1:4000/openai/deployments/qwen36-0',
         'http://127.0.0.1:4000/openai/deployments/qwen36-1')


def endpoints():
    return [{'id': 'deploy-' + str(index), 'model': MODEL, 'api_key': 'unused', 'base_url': base,
             'trust_env': False, 'concurrency': 1} for index, base in enumerate(BASES)]


async def bind_all(provider, calls):
    await provider._start()
    answer = {'id': 'response', 'object': 'chat.completion', 'created': 0, 'model': MODEL,
              'choices': [{'index': 0, 'finish_reason': 'stop',
                           'message': {'role': 'assistant', 'content': 'done'}}]}
    for endpoint in provider.endpoints:
        base = endpoint.config['base_url']

        async def respond(request, base=base):
            calls.append({'base': base, 'path': request.url.path,
                          'model': json.loads(request.content)['model']})
            return chat_http_response(answer)

        endpoint.http._transport = httpx2.MockTransport(respond)


def session_for(store, actor_id, driver):
    scope = InteractionScope(actor_id, Moment(1, 'p'))
    return Session(Actor(actor_id, driver), scope, Information(lambda *a: True).bound(scope),
                   Actions(lambda *a: True).bound(scope), {}, None, (),
                   SimpleNamespace(prepare_artifact=store.prepare_artifact), step=1)


def test_thread_open_samples_a_route_per_thread(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        routes = [threads.describe(threads.open('a' + str(index), 0, 'decision'))['model_route']
                  for index in range(64)]
        assert all(0 <= route < 1 for route in routes)
        assert len(set(routes)) > 56
        assert any(route < .5 for route in routes) and any(route >= .5 for route in routes)
        store.complete(1)


@pytest.mark.asyncio
async def test_threads_pin_to_one_deployment_path_and_keep_the_group_model(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        provider = ModelProvider(endpoints(), threads, max_attempts=1)
        calls = []
        await bind_all(provider, calls)
        try:
            hits = {}
            for index in range(32):
                actor_id = 'actor-' + str(index).zfill(2)
                driver = LLMDriver(provider, threads, policy=LLMPolicy(),
                                   input_builder=lambda current: [{'role': 'user', 'content': '完整输入'}])
                result = await driver.run(session_for(store, actor_id, driver))
                assert result.status == 'completed'
                route = threads.describe(result.value['thread_id'])['model_route']
                expected = BASES[0] if route < .5 else BASES[1]
                assert calls[-1]['base'] == expected
                hits[expected] = hits.get(expected, 0) + 1
                # 同一 Thread 的下一轮继续使用同一部署路径。
                again = LLMDriver(provider, threads, policy=LLMPolicy(),
                                  input_builder=lambda current: [{'role': 'user', 'content': '下一轮完整输入'}])
                assert (await again.run(session_for(store, actor_id, again))).status == 'completed'
                assert calls[-1]['base'] == expected
            assert set(hits) == set(BASES)
            # wire 上的 model 始终是模型组名，部署由 base_url 路径决定。
            assert {item['model'] for item in calls} == {MODEL}
            assert {item['path'] for item in calls} == {
                '/openai/deployments/qwen36-0/chat/completions',
                '/openai/deployments/qwen36-1/chat/completions'}
        finally:
            await provider.close()
        store.complete(1)
