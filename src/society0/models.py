"""Public model declarations for Society0.

Users declare models with small value objects. The engine owns the concrete
resource managers so lifecycle, concurrency, and logs remain centralized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .llm_model_types import ModelConfig, ModelRuntime
from .resource_managers import EmbeddingManager, LLMManager


_TOOL_CHOICE_POLICIES = {"native", "auto_restrict"}


@dataclass(slots=True)
class LLMModel:
    id: str
    model: str
    base_url: str
    api_key: Optional[str] = None
    provider_type: str = "openai"
    concurrency: int = 5
    timeout: float = 30.0
    trust_env: bool = True
    api_version: Optional[str] = None
    deployment_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    request_options: Dict[str, Any] = field(default_factory=dict)
    tool_choice_policy: str = "native"

    def __post_init__(self) -> None:
        self.concurrency = _validate_positive_int(self.concurrency, "concurrency")
        self.tool_choice_policy = str(self.tool_choice_policy).strip().lower()
        if self.tool_choice_policy not in _TOOL_CHOICE_POLICIES:
            allowed = ", ".join(sorted(_TOOL_CHOICE_POLICIES))
            raise ValueError(f"tool_choice_policy must be one of: {allowed}")

    @classmethod
    def openai(
        cls,
        *,
        id: str = "default",
        model: str,
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        concurrency: int = 5,
        timeout: float = 30.0,
        trust_env: bool = True,
        request_options: Optional[Dict[str, Any]] = None,
        tool_choice_policy: str = "native",
    ) -> "LLMModel":
        return cls(
            id=id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider_type="openai",
            concurrency=concurrency,
            timeout=timeout,
            trust_env=trust_env,
            request_options=dict(request_options or {}),
            tool_choice_policy=tool_choice_policy,
        )

    @classmethod
    def openai_compatible(
        cls,
        *,
        id: str = "default",
        model: str,
        base_url: str,
        api_key: Optional[str] = None,
        concurrency: int = 5,
        timeout: float = 30.0,
        trust_env: bool = True,
        request_options: Optional[Dict[str, Any]] = None,
        tool_choice_policy: str = "native",
    ) -> "LLMModel":
        return cls(
            id=id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider_type="openai",
            concurrency=concurrency,
            timeout=timeout,
            trust_env=trust_env,
            request_options=dict(request_options or {}),
            tool_choice_policy=tool_choice_policy,
        )

    @classmethod
    def azure_openai(
        cls,
        *,
        id: str = "default",
        model: str,
        base_url: str,
        api_key: Optional[str] = None,
        api_version: str = "2024-02-15-preview",
        deployment_name: Optional[str] = None,
        concurrency: int = 5,
        timeout: float = 30.0,
        request_options: Optional[Dict[str, Any]] = None,
        tool_choice_policy: str = "native",
    ) -> "LLMModel":
        return cls(
            id=id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider_type="azure",
            api_version=api_version,
            deployment_name=deployment_name,
            concurrency=concurrency,
            timeout=timeout,
            request_options=dict(request_options or {}),
            tool_choice_policy=tool_choice_policy,
        )

    @classmethod
    def ollama(
        cls,
        *,
        id: str = "default",
        model: str,
        base_url: str = "http://localhost:11434/v1",
        concurrency: int = 5,
        timeout: float = 120.0,
        request_options: Optional[Dict[str, Any]] = None,
        tool_choice_policy: str = "native",
    ) -> "LLMModel":
        return cls(
            id=id,
            model=model,
            api_key="ollama",
            base_url=base_url,
            provider_type="openai",
            concurrency=concurrency,
            timeout=timeout,
            request_options=dict(request_options or {}),
            tool_choice_policy=tool_choice_policy,
        )

    def endpoint_config(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "api_key": self.api_key or "",
            "base_url": self.base_url,
            "model": self.model,
            "concurrency": self.concurrency,
            "timeout": self.timeout,
            "trust_env": self.trust_env,
            "provider_type": self.provider_type,
            "api_version": self.api_version,
            "deployment_name": self.deployment_name,
            "tool_choice_policy": self.tool_choice_policy,
        }

    def build_manager(self, *, log_context=None) -> LLMManager:
        return LLMManager([self.endpoint_config()], log_context=log_context)

    def build_runtime(self, *, log_context=None) -> tuple[ModelRuntime, LLMManager]:
        manager = self.build_manager(log_context=log_context)

        async def llm_call(payload: Dict[str, Any]) -> Dict[str, Any]:
            request_payload = dict(self.request_options)
            request_payload.update(payload)
            return await manager.request(request_payload)

        config = ModelConfig(
            model_id=self.id,
            name=self.id,
            model_type="llm",
            base_url=self.base_url,
            model=self.model,
            api_key=self.api_key,
            concurrency=self.concurrency,
            timeout=self.timeout,
            provider_type=self.provider_type,
            api_version=self.api_version,
            deployment_name=self.deployment_name,
            tool_choice_policy=self.tool_choice_policy,
            metadata=dict(self.metadata),
        )
        return ModelRuntime(config=config, llm_call=llm_call), manager


@dataclass(slots=True)
class EmbedModel:
    id: str
    model: str
    base_url: str
    api_key: Optional[str] = None
    provider_type: str = "openai"
    concurrency: int = 5
    timeout: float = 30.0
    dimensions: int = 512
    send_dimensions: bool = True
    trust_env: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.concurrency = _validate_positive_int(self.concurrency, "concurrency")

    @classmethod
    def openai(
        cls,
        *,
        id: str = "default_embed",
        model: str,
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        dimensions: int = 1536,
        concurrency: int = 5,
        timeout: float = 30.0,
    ) -> "EmbedModel":
        return cls(
            id=id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider_type="openai",
            dimensions=dimensions,
            concurrency=concurrency,
            timeout=timeout,
        )

    @classmethod
    def openai_compatible(
        cls,
        *,
        id: str = "default_embed",
        model: str,
        base_url: str,
        api_key: Optional[str] = None,
        dimensions: int = 512,
        send_dimensions: bool = True,
        concurrency: int = 5,
        timeout: float = 30.0,
        trust_env: bool = True,
    ) -> "EmbedModel":
        return cls(
            id=id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider_type="openai",
            dimensions=dimensions,
            send_dimensions=send_dimensions,
            concurrency=concurrency,
            timeout=timeout,
            trust_env=trust_env,
        )

    @classmethod
    def ollama(
        cls,
        *,
        id: str = "default_embed",
        model: str,
        base_url: str = "http://localhost:11434",
        dimensions: int = 512,
        concurrency: int = 5,
        timeout: float = 120.0,
        trust_env: bool = False,
    ) -> "EmbedModel":
        return cls(
            id=id,
            model=model,
            api_key="ollama",
            base_url=base_url,
            provider_type="ollama",
            dimensions=dimensions,
            concurrency=concurrency,
            timeout=timeout,
            trust_env=trust_env,
        )

    def endpoint_config(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "api_key": self.api_key or "",
            "base_url": self.base_url,
            "model": self.model,
            "concurrency": self.concurrency,
            "timeout": self.timeout,
            "provider_type": self.provider_type,
            "dimensions": self.dimensions,
            "send_dimensions": self.send_dimensions,
            "trust_env": self.trust_env,
        }

    def build_manager(self, *, log_context=None) -> EmbeddingManager:
        return EmbeddingManager([self.endpoint_config()], log_context=log_context)


def _validate_positive_int(value: Any, label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a positive integer") from None
    if parsed <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return parsed
