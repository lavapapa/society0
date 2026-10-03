"""机制与基础服务工厂；选取工厂时加载对应依赖。"""
from importlib import import_module
from typing import TYPE_CHECKING

_MODULES = {
    'plain_plugin': 'society0.plugins.plain',
    'round_robin_plugin': 'society0.plugins.round_robin',
    'social_plugin': 'society0.plugins.social',
    'actor_plugin': 'society0.kernel.actors',
    'interaction_plugin': 'society0.kernel.interaction',
    'runtime_plugin': 'society0.kernel.runtime',
    'schedule_plugin': 'society0.kernel.schedule',
    'progress_plugin': 'society0.kernel.schedule',
    'results_plugin': 'society0.kernel.results',
    'dataset_plugin': 'society0.kernel.datasets',
    'thread_plugin': 'society0.kernel.services',
    'memory_plugin': 'society0.kernel.services',
    'model_plugin': 'society0.kernel.models',
    'embedding_plugin': 'society0.kernel.models',
    'workspace_plugin': 'society0.kernel.workspace',
}
__all__ = list(_MODULES)


def __getattr__(name):
    if name not in _MODULES:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    value = getattr(import_module(_MODULES[name]), name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    from .plain import plain_plugin
    from .round_robin import round_robin_plugin
    from .social import social_plugin
    from ..kernel.actors import actor_plugin
    from ..kernel.interaction import interaction_plugin
    from ..kernel.runtime import runtime_plugin
    from ..kernel.schedule import schedule_plugin, progress_plugin
    from ..kernel.results import results_plugin
    from ..kernel.datasets import dataset_plugin
    from ..kernel.services import thread_plugin, memory_plugin
    from ..kernel.models import model_plugin, embedding_plugin
    from ..kernel.workspace import workspace_plugin
