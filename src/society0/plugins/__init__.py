"""机制与基础服务工厂；选取工厂时加载对应依赖。"""
from importlib import import_module
from typing import TYPE_CHECKING

_MODULES = {
    'plain_plugin': 'society0.plugins.plain',
    'round_robin_plugin': 'society0.plugins.round_robin',
    'social_plugin': 'society0.plugins.social',
    'social_data_plugin': 'society0.plugins.social',
    'social_notifications_plugin': 'society0.plugins.social',
    'social_relations_plugin': 'society0.plugins.social',
    'social_engagement_plugin': 'society0.plugins.social',
    'social_content_plugin': 'society0.plugins.social',
    'social_candidates_plugin': 'society0.plugins.social',
    'social_exposure_plugin': 'society0.plugins.social',
    'social_semantic_plugin': 'society0.plugins.social',
    'social_presentation_plugin': 'society0.plugins.social',
    'social_recommendation_plugin': 'society0.plugins.social_recommendation',
    'social_cognition_plugin': 'society0.plugins.social_cognition',
    'actor_plugin': 'society0.kernel.actors',
    'actor_data_plugin': 'society0.kernel.actors',
    'weighted_ranking_plugin': 'society0.plugins.social_ranking',
    'chronological_ranking_plugin': 'society0.plugins.social_ranking',
    'rule_driver_plugin': 'society0.kernel.drivers',
    'llm_driver_plugin': 'society0.kernel.drivers',
    'interaction_plugin': 'society0.kernel.interaction',
    'runtime_plugin': 'society0.kernel.runtime',
    'schedule_plugin': 'society0.kernel.schedule',
    'progress_plugin': 'society0.kernel.schedule',
    'results_plugin': 'society0.kernel.results',
    'dataset_plugin': 'society0.kernel.datasets',
    'compute_plugin': 'society0.kernel.compute',
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
    from .social import (
        social_plugin, social_data_plugin, social_notifications_plugin,
        social_relations_plugin, social_engagement_plugin, social_content_plugin,
        social_candidates_plugin, social_exposure_plugin, social_semantic_plugin,
        social_presentation_plugin,
    )
    from .social_recommendation import social_recommendation_plugin
    from .social_cognition import social_cognition_plugin
    from ..kernel.actors import actor_plugin, actor_data_plugin
    from .social_ranking import weighted_ranking_plugin, chronological_ranking_plugin
    from ..kernel.drivers import rule_driver_plugin, llm_driver_plugin
    from ..kernel.interaction import interaction_plugin
    from ..kernel.runtime import runtime_plugin
    from ..kernel.schedule import schedule_plugin, progress_plugin
    from ..kernel.results import results_plugin
    from ..kernel.datasets import dataset_plugin
    from ..kernel.compute import compute_plugin
    from ..kernel.services import thread_plugin, memory_plugin
    from ..kernel.models import model_plugin, embedding_plugin
    from ..kernel.workspace import workspace_plugin
