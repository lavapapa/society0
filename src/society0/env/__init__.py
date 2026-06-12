"""
This package contains built-in environment extensions for the SimEngine.

This __init__.py file serves as the central registry for all built-in
environments, automatically importing and registering them to make them
immediately available to the SimEngine without requiring external imports.
"""
from typing import Dict, Type
import logging

from ..environment import Environment

logger = logging.getLogger(__name__)

# 1. Central registry for built-in environment classes
BUILTIN_ENVS: Dict[str, Type[Environment]] = {}

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

# 4. Auto-import and register ALL built-in environments
# This ensures all built-in environments are available immediately
# without requiring external imports

try:
    # Import and register SocialNetworkEnv
    from .social_network.env import SocialNetworkEnv
    _register("social_network", SocialNetworkEnv)
    logger.info("Successfully registered SocialNetworkEnv")
except ImportError as e:
    logger.warning(f"Failed to import SocialNetworkEnv: {e}")

try:
    from .round_robin.env import RoundRobinConversationEnv
    _register("round_robin_conversation", RoundRobinConversationEnv)
    logger.info("Successfully registered RoundRobinConversationEnv")
except ImportError as e:
    logger.warning(f"Failed to import RoundRobinConversationEnv: {e}")

try:
    from .plain.env import PlainEnvironment
    _register("plain", PlainEnvironment)
    logger.info("Successfully registered PlainEnvironment")
except ImportError as e:
    logger.warning(f"Failed to import PlainEnvironment: {e}")

# Add more built-in environments here as they are created:
# try:
#     from .gis.env import GisEnv
#     _register("gis", GisEnv)
#     logger.info("Successfully registered GisEnv")
# except ImportError as e:
#     logger.warning(f"Failed to import GisEnv: {e}")

# try:
#     from .economic.env import EconomicEnv
#     _register("economic", EconomicEnv)
#     logger.info("Successfully registered EconomicEnv")
# except ImportError as e:
#     logger.warning(f"Failed to import EconomicEnv: {e}")

logger.info(f"Environment registry initialized with {len(BUILTIN_ENVS)} built-in environments: {list(BUILTIN_ENVS.keys())}")
