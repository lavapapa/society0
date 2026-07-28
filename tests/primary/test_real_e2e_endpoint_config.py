import pytest

from tests.e2e.real_endpoint_config import EndpointConfigError, load_endpoint_env


def test_real_e2e_endpoint_config_prefers_provider_neutral_env():
    llm_env, embed_env = load_endpoint_env(
        {
            "SOCIETY0_REAL_E2E_LLM_BASE_URL": "https://llm.example.test/v1",
            "SOCIETY0_REAL_E2E_LLM_MODEL": "provider-chat-model",
            "SOCIETY0_REAL_E2E_LLM_API_KEY": "llm-placeholder",
            "SOCIETY0_REAL_E2E_LLM_TOOL_MODE": "prompted_json",
            "SOCIETY0_REAL_E2E_EMBED_BASE_URL": "https://embed.example.test/v1",
            "SOCIETY0_REAL_E2E_EMBED_MODEL": "provider-embed-model",
            "SOCIETY0_REAL_E2E_EMBED_API_KEY": "embed-placeholder",
            "SOCIETY0_REAL_E2E_EMBED_PROVIDER": "openai_compatible",
            "SOCIETY0_REAL_E2E_EMBED_DIMENSIONS": "1024",
        }
    )

    assert llm_env == {
        "LLM_BASE_URL": "https://llm.example.test/v1",
        "LLM_MODEL": "provider-chat-model",
        "LLM_API_KEY": "llm-placeholder",
        "LLM_TIMEOUT": "180",
        "LLM_TOOL_MODE": "prompted_json",
    }
    assert embed_env == {
        "EMBEDDING_BASE_URL": "https://embed.example.test/v1",
        "EMBEDDING_MODEL": "provider-embed-model",
        "EMBEDDING_API_KEY": "embed-placeholder",
        "EMBEDDING_DIMENSIONS": "1024",
        "EMBEDDING_PROVIDER_TYPE": "openai_compatible",
    }


def test_real_e2e_endpoint_config_supports_generic_env_aliases():
    llm_env, embed_env = load_endpoint_env(
        {
            "LLM_BASE_URL": "http://localhost:8000/v1",
            "LLM_MODEL": "local-chat",
            "EMBEDDING_BASE_URL": "http://localhost:11434",
            "EMBEDDING_MODEL": "nomic-embed-text",
            "EMBEDDING_PROVIDER_TYPE": "ollama",
        }
    )

    assert llm_env["LLM_BASE_URL"] == "http://localhost:8000/v1"
    assert llm_env["LLM_MODEL"] == "local-chat"
    assert embed_env["EMBEDDING_BASE_URL"] == "http://localhost:11434"
    assert embed_env["EMBEDDING_MODEL"] == "nomic-embed-text"
    assert embed_env["EMBEDDING_PROVIDER_TYPE"] == "ollama"


def test_real_e2e_endpoint_config_without_direct_env_requires_explicit_fallback():
    with pytest.raises(EndpointConfigError, match="provider-neutral real e2e endpoint variables"):
        load_endpoint_env({})
