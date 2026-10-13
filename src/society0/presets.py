"""常用单驱动配方：普通 Plugin 元组，领域、模型、认知和调度显式选择。"""
from __future__ import annotations
from collections.abc import Callable, Iterable
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .kernel.actors import ActorRecord
    from .kernel.plugins import Plugin
    from .kernel.runtime import Session

ServiceRef = tuple[str, str]
ModelRef = tuple[str, str] | tuple[str, str, str]
Extension = ServiceRef | Callable[..., Any]


def _actors(records, data, label, driver):
    if records is not None and data is not None:
        raise ValueError('records and data are mutually exclusive')
    from .kernel.actors import actor_plugin
    return actor_plugin({label: (driver, 'factory')}, records=() if records is None else records, data=data)


def _runtime(runtime):
    if runtime is not None:
        return runtime
    from .kernel.runtime import runtime_plugin
    return runtime_plugin(actor_service=('actors', 'actors'), information=('interaction', 'information'),
                          actions=('interaction', 'actions'), store=('storage', 'store'), results=('results', 'results'))


def rule_preset(*, records: Iterable[ActorRecord] | None = None, data: ServiceRef | None = None,
                run: Callable[[Session], Any], allows: Callable[..., Any], schedule: Plugin,
                plugins: Iterable[Plugin] = (), extensions: Iterable[Extension] = (),
                runtime: Plugin | None = None) -> tuple[Plugin, ...]:
    from .kernel.drivers import rule_driver_plugin
    from .kernel.interaction import interaction_plugin
    from .kernel.results import results_plugin
    actors = _actors(records, data, 'rule', 'rule_driver')
    return (interaction_plugin(allows), results_plugin(), rule_driver_plugin(run, extensions=extensions), actors,
            _runtime(runtime), schedule, *plugins)


def llm_preset(*, records: Iterable[ActorRecord] | None = None, data: ServiceRef | None = None,
               provider: ModelRef, input_builder: ServiceRef, allows: Callable[..., Any], schedule: Plugin,
               plugins: Iterable[Plugin] = (), policy: Any = None, extensions: Iterable[Extension] = (),
               shell_factory: Callable[..., Any] | None = None, provider_selector: Callable[..., Any] | None = None,
               runtime: Plugin | None = None) -> tuple[Plugin, ...]:
    if provider is None or input_builder is None:
        raise ValueError('LLM preset requires provider and input_builder references')
    from .kernel.drivers import llm_driver_plugin
    from .kernel.interaction import interaction_plugin
    from .kernel.results import results_plugin
    from .kernel.services import thread_plugin
    actors = _actors(records, data, 'llm', 'llm_driver')
    return (interaction_plugin(allows), results_plugin(), thread_plugin(),
            llm_driver_plugin(provider=provider, input_builder=input_builder, policy=policy, extensions=extensions,
                              shell_factory=shell_factory, provider_selector=provider_selector), actors,
            _runtime(runtime), schedule, *plugins)
