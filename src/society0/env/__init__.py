"""
This package contains built-in environment extensions for the SimEngine.

This __init__.py file serves as the central registry for all built-in
environments. Environment classes are loaded lazily so a lightweight
experiment does not pay for unrelated optional dependencies at import time.
"""
from typing import Dict, Type
import importlib
import logging

from ..environment import Environment

logger = logging.getLogger(__name__)

_BUILTIN_ENV_CLASS_PATHS = {
    "social_network": "society0.env.social_network.env.SocialNetworkEnv",
    "round_robin_conversation": "society0.env.round_robin.env.RoundRobinConversationEnv",
    "plain": "society0.env.plain.env.PlainEnvironment",
}


class _LazyBuiltinEnvRegistry(dict):
    """兼容旧 dict API 的内置环境懒加载注册表。"""

    def get(self, name, default=None):
        if name not in self:
            self._load(name)
        return super().get(name, default)

    def _load(self, name: str) -> None:
        class_path = _BUILTIN_ENV_CLASS_PATHS.get(str(name))
        if not class_path:
            return
        module_name, _, class_name = class_path.rpartition(".")
        try:
            module = importlib.import_module(module_name)
            cls = getattr(module, class_name)
        except ImportError as e:
            logger.warning(f"Failed to import built-in environment '{name}': {e}")
            return
        except AttributeError as e:
            logger.warning(f"Built-in environment '{name}' has invalid class path {class_path}: {e}")
            return
        _register(str(name), cls)


# 1. Central registry for built-in environment classes
BUILTIN_ENVS: Dict[str, Type[Environment]] = _LazyBuiltinEnvRegistry()

# 2. Registration function for environment classes
def _register(name: str, cls: Type[Environment]):
    """Register an environment class with the given name."""
    if name in BUILTIN_ENVS:
        logger.warning(f"Built-in environment '{name}' is already registered. Overwriting.")
    BUILTIN_ENVS[name] = cls
    logger.debug(f"Registered built-in environment: '{name}'")

# 3. Decorator for environment registration (used in environment classes)
def register_env(name: str):
    """Decorator to register an environment class with a given name."""
    def decorator(cls: Type[Environment]):
        _register(name, cls)
        return cls
    return decorator

logger.info("Environment registry initialized lazily for: %s", list(_BUILTIN_ENV_CLASS_PATHS.keys()))
