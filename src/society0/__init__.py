"""Society0：共享环境、可组合机制与主体驱动。"""
from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

__version__ = '5.0.0'

_LAZY_IMPORTS = {
    'compose': ('composition', 'compose'),
    'Plugin': ('plugins', 'Plugin'),
    'ActorRecord': ('actors', 'ActorRecord'),
    'Phase': ('runtime', 'Phase'),
    'DriverResult': ('runtime', 'DriverResult'),
    'RuleDriver': ('schedule', 'RuleDriver'),
    'CodeSchedule': ('schedule', 'CodeSchedule'),
    'StepResult': ('results', 'StepResult'),
    'TableValue': ('results', 'TableValue'),
    'DatasetTable': ('results', 'DatasetTable'),
    'Ref': ('interaction', 'Ref'),
    'Query': ('interaction', 'Query'),
    'Action': ('interaction', 'Action'),
    'ActionResult': ('interaction', 'ActionResult'),
}
__all__ = list(_LAZY_IMPORTS)


def __getattr__(name):
    if name not in _LAZY_IMPORTS:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    module, attribute = _LAZY_IMPORTS[name]
    value = getattr(import_module(f'.kernel.{module}', __name__), attribute)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(__all__))


if TYPE_CHECKING:
    from .kernel.composition import compose
    from .kernel.plugins import Plugin
    from .kernel.actors import ActorRecord
    from .kernel.runtime import Phase, DriverResult
    from .kernel.schedule import RuleDriver, CodeSchedule
    from .kernel.results import StepResult, TableValue, DatasetTable
    from .kernel.interaction import Ref, Query, Action, ActionResult
