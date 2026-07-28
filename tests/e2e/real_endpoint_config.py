"""Provider-neutral endpoint config helpers for real e2e tests."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Mapping


class EndpointConfigError(RuntimeError):
    """Raised when real e2e endpoint settings are not available."""


def load_endpoint_env(environ: Mapping[str, str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Load real LLM and embedding endpoint settings.

    Public, provider-neutral environment variables take precedence. A local
    platform-root fallback remains for maintainers, but open-source users should
    not need the broader Society Zero Universe checkout to run real e2e tests.
    """
    env = environ or os.environ
    direct = _direct_endpoint_env(env)
    if direct is not None:
        return direct
    return _platform_endpoint_env(env)


def _direct_endpoint_env(env: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]] | None:
    llm_base_url = _first_env(env, "SOCIETY0_REAL_E2E_LLM_BASE_URL", "LLM_BASE_URL")
    llm_model = _first_env(env, "SOCIETY0_REAL_E2E_LLM_MODEL", "LLM_MODEL")
    embed_base_url = _first_env(
        env,
        "SOCIETY0_REAL_E2E_EMBED_BASE_URL",
        "SOCIETY0_REAL_E2E_EMBEDDING_BASE_URL",
        "EMBEDDING_BASE_URL",
    )
    embed_model = _first_env(
        env,
        "SOCIETY0_REAL_E2E_EMBED_MODEL",
        "SOCIETY0_REAL_E2E_EMBEDDING_MODEL",
        "EMBEDDING_MODEL",
    )
    if not (llm_base_url and llm_model and embed_base_url and embed_model):
        return None

    llm_env = {
        "LLM_BASE_URL": llm_base_url,
        "LLM_MODEL": llm_model,
        "LLM_API_KEY": _first_env(env, "SOCIETY0_REAL_E2E_LLM_API_KEY", "LLM_API_KEY") or "",
        "LLM_TIMEOUT": _first_env(env, "SOCIETY0_REAL_E2E_LLM_TIMEOUT", "LLM_TIMEOUT") or "180",
        "LLM_TOOL_MODE": _first_env(
            env,
            "SOCIETY0_REAL_E2E_LLM_TOOL_MODE",
            "LLM_TOOL_MODE",
        )
        or "native",
    }
    embedding_env = {
        "EMBEDDING_BASE_URL": embed_base_url,
        "EMBEDDING_MODEL": embed_model,
        "EMBEDDING_API_KEY": _first_env(
            env,
            "SOCIETY0_REAL_E2E_EMBED_API_KEY",
            "SOCIETY0_REAL_E2E_EMBEDDING_API_KEY",
            "EMBEDDING_API_KEY",
        )
        or "",
        "EMBEDDING_DIMENSIONS": _first_env(
            env,
            "SOCIETY0_REAL_E2E_EMBED_DIMENSIONS",
            "SOCIETY0_REAL_E2E_EMBEDDING_DIMENSIONS",
            "EMBEDDING_DIMENSIONS",
        )
        or "768",
        "EMBEDDING_PROVIDER_TYPE": _first_env(
            env,
            "SOCIETY0_REAL_E2E_EMBED_PROVIDER",
            "SOCIETY0_REAL_E2E_EMBEDDING_PROVIDER",
            "EMBEDDING_PROVIDER_TYPE",
        )
        or "openai_compatible",
    }
    endpoints_json = _first_env(
        env,
        "SOCIETY0_REAL_E2E_EMBED_ENDPOINTS_JSON",
        "SOCIETY0_REAL_E2E_EMBEDDING_ENDPOINTS_JSON",
        "EMBEDDING_ENDPOINTS_JSON",
    )
    if endpoints_json:
        embedding_env["EMBEDDING_ENDPOINTS_JSON"] = endpoints_json
    return llm_env, embedding_env


def _platform_endpoint_env(env: Mapping[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    raw_platform_root = env.get("SOCIETY0_PLATFORM_ROOT")
    if not raw_platform_root:
        raise EndpointConfigError(
            "Set provider-neutral real e2e endpoint variables, or set SOCIETY0_PLATFORM_ROOT "
            "for the local platform fallback."
        )

    platform_root = Path(raw_platform_root)
    if not platform_root.exists():
        raise EndpointConfigError(f"Society0 platform repo is not available at {platform_root}")

    secrets_path = platform_root / "core" / "services" / "secrets_service.py"
    try:
        spec = importlib.util.spec_from_file_location("society0_platform_secrets_service", secrets_path)
        if spec is None or spec.loader is None:
            raise EndpointConfigError(f"Cannot load Society0 SecretsService from {secrets_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        secrets_service = module.SecretsService
    except EndpointConfigError:
        raise
    except Exception as exc:  # pragma: no cover - local infrastructure guard
        raise EndpointConfigError(f"Cannot import Society0 SecretsService: {exc}") from exc

    secrets = secrets_service()
    return secrets.get_llm_config(), secrets.get_embedding_config()


def _first_env(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = str(env.get(name) or "").strip()
        if value:
            return value
    return None
