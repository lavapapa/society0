"""物理 LLM 与嵌入额度相互独立、可选总量按请求计；端到端经真实 SDK 离线传输。
"""
import asyncio

import pytest
from openai.types import CreateEmbeddingResponse
from openai.types.chat import ChatCompletion

from society0.kernel.models import RESOURCE_SCHEMA
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.provider_http import bind_chat, bind_embedding
from tests.primary.test_kernel_models import endpoint
from tests.primary.test_kernel_request_limit import vectors, response


def model_plugin_modules():
    from society0.kernel.models import ModelProvider, EmbeddingProvider
    return ModelProvider, EmbeddingProvider


@pytest.mark.asyncio
async def test_llm_and_embedding_permits_are_independent_objects(tmp_path):
    ModelProvider, EmbeddingProvider = model_plugin_modules()
    llm, embedding = asyncio.Semaphore(2), asyncio.Semaphore(1)
    with StageStore.create(tmp_path / 'run', [*THREAD_SCHEMA, *RESOURCE_SCHEMA]) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        provider = ModelProvider([endpoint(id='m')], threads, request_limit=llm)
        embed = EmbeddingProvider([endpoint(id='e')], store, threads, dimensions=2, request_limit=embedding,
                                  batch_wait_ms=0)
        try:
            async def chat(**kwargs): return response()
            async def embed_call(**kwargs): return vectors(kwargs['input'])
            await bind_chat(provider, chat)
            await bind_embedding(embed, embed_call)
            assert provider.endpoints[0].resources.shared is llm
            assert embed.endpoints[0].resources.shared is embedding
            assert llm is not embedding
        finally:
            await provider.close()
            await embed.close()


@pytest.mark.asyncio
async def test_model_requests_never_consume_the_embedding_permit(tmp_path):
    """LLM 请求只经 LLM 许可；把嵌入许可压到 0 容量不影响模型请求完成。"""
    ModelProvider, EmbeddingProvider = model_plugin_modules()
    llm = asyncio.Semaphore(1)
    embedding = asyncio.Semaphore(1)
    await embedding.acquire()
    with StageStore.create(tmp_path / 'run', [*THREAD_SCHEMA, *RESOURCE_SCHEMA]) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        threads.append_message(tid, {'role': 'user', 'content': 'x'})
        provider = ModelProvider([endpoint(id='m')], threads, request_limit=llm)
        embed = EmbeddingProvider([endpoint(id='e')], store, threads, dimensions=2, request_limit=embedding)
        try:
            async def chat(**kwargs): return response()
            await bind_chat(provider, chat)
            assert (await asyncio.wait_for(provider.request(tid, {}), 5))['content'] == 'done'
            assert embedding.locked()
        finally:
            embedding.release()
            await provider.close()
            await embed.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('total', [1, 2])
async def test_optional_total_is_a_third_independent_permit(tmp_path, total):
    """总量许可与两类服务许可同时生效；它按物理请求计，不替代端点或服务许可。"""
    ModelProvider, EmbeddingProvider = model_plugin_modules()
    llm, embedding = asyncio.Semaphore(4), asyncio.Semaphore(2)
    shared = asyncio.Semaphore(total)
    active = 0
    peak = 0
    release = asyncio.Event()
    entered = asyncio.Event()

    async def physical(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == total:
            entered.set()
        try:
            await release.wait()
            return response()
        finally:
            active -= 1

    with StageStore.create(tmp_path / 'run', [*THREAD_SCHEMA, *RESOURCE_SCHEMA]) as store:
        threads = ThreadStore(store)
        tids = [threads.open(str(index), 0, 'decision') for index in range(total + 2)]
        provider = ModelProvider([endpoint(id='m')], threads, request_limit=llm, total_limit=shared)
        # 同一可选总量同时约束模型与嵌入物理请求；两类服务许可仍各自独立。
        embed = EmbeddingProvider([endpoint(id='e')], store, threads, dimensions=2,
                                  request_limit=embedding, total_limit=shared)
        try:
            async def chat(**kwargs): return await physical()
            await bind_chat(provider, chat)
            tasks = [asyncio.create_task(provider.request(tid, {})) for tid in tids]
            await asyncio.wait_for(entered.wait(), 1)
            assert active == total and peak == total
            assert shared._value == 0
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
            assert shared._value == total
        finally:
            release.set()
            await provider.close()
            await embed.close()


@pytest.mark.asyncio
async def test_cancelled_model_request_releases_total_permit(tmp_path):
    ModelProvider, _ = model_plugin_modules()
    total = asyncio.Semaphore(1)
    entered = asyncio.Event()
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        tid = threads.open('a', 0, 'decision')
        provider = ModelProvider([endpoint(id='m')], threads, total_limit=total)
        async def chat(**kwargs):
            entered.set()
            await asyncio.Event().wait()
        await bind_chat(provider, chat)
        task = asyncio.create_task(provider.request(tid, {}))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert total._value == 0
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
            assert total._value == 1
        finally:
            await provider.close()


@pytest.mark.asyncio
async def test_composition_passes_three_distinct_permits_to_consumers(tmp_path):
    """装配入口把 LLM、嵌入与可选总量分别交给各自消费者，不共享同一个信号量。"""
    from society0.kernel.composition import compose
    from society0.kernel.models import embedding_plugin, model_plugin
    from society0.kernel.services import thread_plugin

    llm, embedding, total = asyncio.Semaphore(3), asyncio.Semaphore(2), asyncio.Semaphore(5)
    models = {'decision': {'endpoints': [endpoint(id='m')]}}
    embeddings = {'memory': {'endpoints': [endpoint(id='e')], 'dimensions': 2}}
    async with compose(tmp_path / 'run', [thread_plugin(),
                                          model_plugin(models, request_limit=llm, total_limit=total),
                                          embedding_plugin(embeddings, request_limit=embedding, total_limit=total)]) as host:
        model_provider = host.service('models', 'models')['decision']
        embed_provider = host.service('embeddings', 'embeddings')['memory']
        assert model_provider.endpoints[0].resources.shared is llm
        assert embed_provider.endpoints[0].resources.shared is embedding
        assert model_provider._total_limit is total
        assert embed_provider._total_limit is total
