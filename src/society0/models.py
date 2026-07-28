"""Public model declarations for Society0.

Users declare models with small value objects. The engine owns the concrete
resource managers so lifecycle, concurrency, and logs remain centralized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .llm_model_types import ModelConfig, ModelRuntime
from .resource_managers import EmbeddingManager, LLMManager


@dataclass(slots=True)
class LLMModel:
    id: str
    model: str
    base_url: str
    api_key: Optional[str] = None
    provider_type: str = "openai"
    concurrency: int = 5
    timeout: float = 30.0
    api_version: Optional[str] = None
    deployment_name: Optional[str] = None
    tool_call_mode: str = "native"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.concurrency = _validate_positive_int(self.concurrency, "concurrency")
        if self.tool_call_mode not in {"native", "prompted_json"}:
            raise ValueError("tool_call_mode must be 'native' or 'prompted_json'")

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
        tool_call_mode: str = "native",
    ) -> "LLMModel":
        return cls(
            id=id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider_type="openai",
            concurrency=concurrency,
            timeout=timeout,
            tool_call_mode=tool_call_mode,
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
        tool_call_mode: str = "native",
    ) -> "LLMModel":
        return cls(
            id=id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            provider_type="openai",
            concurrency=concurrency,
            timeout=timeout,
            tool_call_mode=tool_call_mode,
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
        tool_call_mode: str = "native",
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
            tool_call_mode=tool_call_mode,
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
        tool_call_mode: str = "native",
    ) -> "LLMModel":
        return cls(
            id=id,
            model=model,
            api_key="ollama",
            base_url=base_url,
            provider_type="openai",
            concurrency=concurrency,
            timeout=timeout,
            tool_call_mode=tool_call_mode,
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
            "api_version": self.api_version,
            "deployment_name": self.deployment_name,
            "tool_call_mode": self.tool_call_mode,
        }

    def build_manager(self, *, log_context=None) -> LLMManager:
        return LLMManager([self.endpoint_config()], log_context=log_context)

    def build_runtime(self, *, log_context=None) -> tuple[ModelRuntime, LLMManager]:
        manager = self.build_manager(log_context=log_context)
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
            metadata={**self.metadata, "tool_call_mode": self.tool_call_mode},
        )
        return ModelRuntime(config=config, llm_call=manager.request), manager


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
    def ollama(
        cls,
        *,
        id: str = "default_embed",
        model: str,
        base_url: str = "http://localhost:11434",
        dimensions: int = 512,
        concurrency: int = 5,
        timeout: float = 120.0,
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
