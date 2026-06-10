"""
环境类型发现与注册表构建（读取类元数据，DI 友好）。

职责：
- 导入内置环境包，递归加载子模块。
- 在已加载模块中收集带有 `__env_meta__` 的环境类。
- 构建只读注册表（type_name -> EnvironmentMeta）。

v3.0 更新：
- 使用新的 meta.py 中的 EnvironmentMeta 结构
- capabilities 字段替代原有的 fovs 字段
- 保留 _env_class 引用用于运行时实例化
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from .decorators import ENV_META_ATTR
from .meta import EnvironmentMeta

logger = logging.getLogger(__name__)


def _safe_import(module_name: str) -> None:
    try:
        importlib.import_module(module_name)
    except Exception as e:
        logger.warning(f"环境模块导入失败: {module_name}: {e}")


def _walk_and_import(package) -> None:
    """递归导入包下所有子模块，触发装饰器执行。"""
    if not hasattr(package, "__path__"):
        return
    prefix = package.__name__ + "."
    for _, modname, ispkg in pkgutil.walk_packages(package.__path__, prefix):
        _safe_import(modname)


def discover_builtins() -> Dict[str, EnvironmentMeta]:
    """发现并返回所有内置环境的元数据映射。

    v3.0: 直接返回类上的 EnvironmentMeta，包含：
    - 基本信息 (type_name, display_name, description)
    - Schema 定义 (config_schema, state_schema, agent_managed_fields_schema)
    - Capabilities (从 @capability 装饰的方法中收集)
    - _env_class 引用（用于运行时实例化）
    """
    try:
        pkg = importlib.import_module("simengine.env")
    except Exception as e:
        logger.warning(f"无法导入内置环境包 simengine.env: {e}")
        return {}

    # 递归导入，确保装饰器执行
    _walk_and_import(pkg)

    # 收集元数据
    registry: Dict[str, EnvironmentMeta] = {}
    for _, modname, _ in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        try:
            mod = importlib.import_module(modname)
        except Exception:
            continue

        for _, obj in inspect.getmembers(mod, inspect.isclass):
            meta = getattr(obj, ENV_META_ATTR, None)
            if isinstance(meta, EnvironmentMeta):
                # 🔑 直接使用类上的 EnvironmentMeta
                # capabilities 已经在 @env_type 装饰器中收集完毕
                # _env_class 引用已经附加

                # 后写覆盖前写（同 type_name 仅保留最后一次定义）
                registry[meta.type_name] = meta

                logger.debug(
                    f"Discovered environment '{meta.type_name}' with "
                    f"{len(meta.capabilities)} capabilities from {meta.class_path}"
                )

    logger.info(f"Discovered {len(registry)} built-in environment types")
    return registry


@dataclass(frozen=True)
class EnvRegistry:
    """不可变的环境注册表，供服务层消费。"""
    items: Mapping[str, EnvironmentMeta]

    def to_public_dict(self) -> Dict[str, Any]:
        """以可序列化的形式导出（用于 API 返回）。

        v3.0: 使用 EnvironmentMeta.to_dict() 进行序列化，
        排除 _env_class 引用。
        """
        out: Dict[str, Any] = {}
        for k, meta in self.items.items():
            # 使用 meta.to_dict() 获取完整的可序列化数据
            serialized = meta.to_dict()

            # 为 API 返回简化的视图（保持向后兼容）
            out[k] = {
                "type": meta.type_name,
                "display_name": meta.display_name,
                "description": meta.description,
                "class_path": meta.class_path,
                "config_schema": meta.config_schema,
                "state_schema": meta.state_schema,
                "agent_managed_fields_schema": meta.agent_managed_fields_schema,
                "builtin_state_fields": meta.builtin_state_fields,
                "capabilities": [
                    {
                        "name": cap.name,
                        "kind": cap.kind,
                        "description": cap.description,
                        "tags": cap.tags
                    }
                    for cap in meta.capabilities
                ]
            }
        return out
