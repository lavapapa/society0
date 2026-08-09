"""LLM 模型运行期类型定义。

该模块提供 SimEngine 在运行时解析与管理 LLM 配置所需的最小结构。
设计遵循“类型简单、依赖明确、易于序列化”的原则，确保既能在服务端
环境中复用，也能被沙箱脚本安全导入。
"""

from __future__ import annotations

import random
import threading
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple


@dataclass(slots=True)
class ModelConfig:
    """包含敏感信息在内的完整模型配置。"""

    model_id: str
    name: str
    model_type: str  # 目前仅支持 "llm"
    base_url: str
    model: str
    api_key: Optional[str] = None
    concurrency: Optional[int] = None
    timeout: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Azure OpenAI 特定字段
    provider_type: Optional[str] = None
    api_version: Optional[str] = None
    deployment_name: Optional[str] = None
    tool_choice_policy: str = "native"

    def as_public_dict(self) -> Dict[str, Any]:
        """返回脱敏后的配置，用于对外展示。"""
        config_data = {
            "base_url": self.base_url,
            "model": self.model,
            "concurrency": self.concurrency,
            "timeout": self.timeout,
            "metadata": dict(self.metadata),
        }

        if self.provider_type:
            config_data["metadata"]["provider_type"] = self.provider_type
        if self.api_version:
            config_data["metadata"]["api_version"] = self.api_version
        if self.deployment_name:
            config_data["metadata"]["deployment_name"] = self.deployment_name
        config_data["metadata"]["tool_choice_policy"] = self.tool_choice_policy

        return {
            "model_id": self.model_id,
            "name": self.name,
            "type": self.model_type,
            "config": config_data,
        }

    def as_secret_dict(self) -> Dict[str, Any]:
        """返回包含敏感字段的配置，用于存储。"""
        config_data = {
            "base_url": self.base_url,
            "model": self.model,
            "api_key": self.api_key,
            "concurrency": self.concurrency,
            "timeout": self.timeout,
            "metadata": dict(self.metadata),
        }

        if self.provider_type:
            config_data["provider_type"] = self.provider_type
        if self.api_version:
            config_data["api_version"] = self.api_version
        if self.deployment_name:
            config_data["deployment_name"] = self.deployment_name
        config_data["tool_choice_policy"] = self.tool_choice_policy

        return {
            "model_id": self.model_id,
            "name": self.name,
            "type": self.model_type,
            "config": config_data,
        }


@dataclass(slots=True)
class ModelRuntime:
    """运行期模型配置，封装配置与调用函数。"""

    config: ModelConfig | Any
    llm_call: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass(slots=True)
class ModelSelection:
    """模型选择结果，包含模型 ID 与运行期配置。"""

    model_id: str
    runtime: ModelRuntime


class ModelProvider:
    """在运行期提供模型配置的轻量服务。"""

    def __init__(
        self,
        *,
        models: Dict[str, ModelRuntime],
        default_model_id: str,
    ) -> None:
        if default_model_id not in models:
            raise ValueError("default_model_id 必须存在于 models 中")

        self._models = dict(models)
        self._default_model_id = default_model_id
        self._lock = threading.RLock()
        self._load_counters: Dict[str, int] = {model_id: 0 for model_id in models}

    def get(self, model_id: Optional[str]) -> ModelSelection:
        """返回指定模型的配置，若不存在则回退到默认模型。"""
        target_id = model_id or self._default_model_id
        with self._lock:
            runtime = self._models.get(target_id)
            if runtime is None:
                target_id = self._default_model_id
                runtime = self._models[target_id]
            self._load_counters[target_id] = self._load_counters.get(target_id, 0) + 1
        return ModelSelection(model_id=target_id, runtime=runtime)

    def register_models(
        self,
        models: Dict[str, ModelRuntime],
        *,
        default_model_id: Optional[str] = None,
    ) -> None:
        """重新注册模型配置，可用于实验准备阶段刷新。"""
        with self._lock:
            self._models = dict(models)
            if not self._models:
                raise ValueError("模型列表不能为空")

            if default_model_id is not None:
                if default_model_id not in self._models:
                    raise ValueError("给定的 default_model_id 不存在于模型列表中")
                self._default_model_id = default_model_id
            elif self._default_model_id not in self._models:
                self._default_model_id = next(iter(self._models))

            self._load_counters = {model_id: 0 for model_id in self._models}

    def choose_by_name(self, model_name: str) -> Optional[ModelSelection]:
        """根据模型名称（非 ID）进行随机选择，用于同模型多配置。"""
        candidates: List[Tuple[str, ModelRuntime]] = []
        with self._lock:
            for model_id, runtime in self._models.items():
                if getattr(runtime.config, "model", None) == model_name:
                    candidates.append((model_id, runtime))

            if not candidates:
                return None

            weights = [self._weight_for(model_id) for model_id, _ in candidates]
            chosen_index = self._weighted_random_choice(weights)
            chosen_id, chosen_runtime = candidates[chosen_index]
            self._load_counters[chosen_id] = self._load_counters.get(chosen_id, 0) + 1

        return ModelSelection(model_id=chosen_id, runtime=chosen_runtime)

    def get_default(self) -> ModelSelection:
        """返回默认模型配置。"""
        return self.get(self._default_model_id)

    def has_model(self, model_id: str) -> bool:
        with self._lock:
            return model_id in self._models

    def get_default_model_id(self) -> str:
        return self._default_model_id

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------
    def _weight_for(self, model_id: str) -> float:
        """根据当前调用次数简单计算权重（次数越少越优先）。"""
        calls = self._load_counters.get(model_id, 0)
        return 1.0 / (1 + calls)

    @staticmethod
    def _weighted_random_choice(weights: Iterable[float]) -> int:
        ordered_weights = list(weights)
        total = sum(ordered_weights)
        if total <= 0:
            return 0
        boundary = random.random() * total
        cumulative = 0.0
        for index, weight in enumerate(ordered_weights):
            cumulative += weight
            if boundary <= cumulative:
                return index
        return len(ordered_weights) - 1


__all__ = [
    "ModelConfig",
    "ModelRuntime",
    "ModelSelection",
    "ModelProvider",
]
