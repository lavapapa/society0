"""Codex 订阅使用成熟 OAuth/Responses 适配器，无付费 API key 回退。"""
import pytest
from society0.kernel.models import ModelProvider, EmbeddingProvider
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA


@pytest.mark.asyncio
async def test_native_codex_provider_owns_dedicated_http_and_preserves_adapter(monkeypatch,tmp_path):
    from pydantic_ai.providers import openai_codex
    from pydantic_ai.models.openai_codex import OpenAICodexModel
    credentials=openai_codex.OpenAICodexCredentials(access_token='test-only-access',refresh_token='test-only-refresh',account_id='test-only-account')
    monkeypatch.setattr(openai_codex,'_read_codex_cli_credentials',lambda:credentials)
    store=StageStore.create(tmp_path/'run',THREAD_SCHEMA)
    provider=ModelProvider([{'id':'codex','provider_type':'openai-codex','model':'gpt-5.4','trust_env':False}],ThreadStore(store))
    try:
        await provider._start()
        endpoint=provider.endpoints[0]
        assert isinstance(endpoint.model,OpenAICodexModel)
        assert isinstance(endpoint.model._provider,openai_codex.OpenAICodexProvider)
        assert endpoint.client is endpoint.model._provider.client
        assert endpoint.http.auth is not None
        assert endpoint.secret is None
    finally:
        await provider.close()
        store.close()


def test_codex_subscription_does_not_install_embedding_transport():
    with pytest.raises(ValueError,match='does not provide embeddings'):
        EmbeddingProvider([{'id':'codex','provider_type':'openai-codex','model':'gpt-5.4'}],None,dimensions=4)
