"""插件依赖与资源生命周期；不承载仿真状态或调度规则。"""

from collections.abc import Callable, Iterable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from inspect import isawaitable
import sys
from typing import Any


def _noop(context: "PluginContext") -> None:
    pass


@dataclass(frozen=True)
class Plugin:
    name: str
    requires: tuple[str, ...] = ()
    install: Callable[["PluginContext"], Any] = _noop

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires", tuple(self.requires))


class PluginContext:
    """安装时注册资源；声明的依赖在本插件关闭期间仍可访问。"""

    def __init__(self, host: "PluginHost", plugin: Plugin):
        self._host = host
        self._plugin = plugin
        self.name = plugin.name
        self._installing = True

    def _check_installing(self) -> None:
        if not self._installing or self._host._state != "installing":
            raise RuntimeError("plugin registration requires an active installer")

    def provide(self, name: str, value: Any) -> None:
        self._check_installing()
        services = self._host._services[self.name]
        if name in services:
            raise ValueError(f"duplicate service: {self.name}.{name}")
        services[name] = value

    def require(self, plugin: str, service: str) -> Any:
        if self._host._state == "closed":
            raise RuntimeError("plugin context is closed")
        if plugin not in self._plugin.requires:
            raise ValueError(f"undeclared plugin dependency: {plugin}")
        return self._host._services[plugin][service]

    def on_close(self, callback: Callable, *args: Any, **kwargs: Any) -> None:
        self._check_installing()

        async def close():
            result = callback(*args, **kwargs)
            if isawaitable(result):
                await result

        self._host._stack.push_async_callback(close)

    async def enter_context(self, manager: Any) -> Any:
        self._check_installing()
        resource = AsyncExitStack()
        if hasattr(type(manager), "__aenter__"):
            value = await resource.enter_async_context(manager)
        else:
            value = resource.enter_context(manager)

        async def close():
            # 每个资源接收作用域原始异常，无法吞掉其他资源的清理失败。
            await resource.__aexit__(*self._host._exit_exception)

        self._host._stack.push_async_callback(close)
        return value


class PluginHost:
    """单次、串行安装的异步资源作用域；服务按插件名分区。"""

    def __init__(self, plugins: Iterable[Plugin]):
        self._plugins = tuple(plugins)
        self._services: dict[str, dict[str, Any]] = {}
        self._stack = AsyncExitStack()
        self._state = "new"
        self._exit_exception = (None, None, None)

    def _order(self) -> tuple[str, ...]:
        graph = {}
        for plugin in self._plugins:
            if plugin.name in graph:
                raise ValueError(f"duplicate plugin: {plugin.name}")
            graph[plugin.name] = plugin.requires
        for name, dependencies in graph.items():
            for dependency in dependencies:
                if dependency not in graph:
                    raise ValueError(f"missing plugin dependency: {name} requires {dependency}")
        try:
            return tuple(TopologicalSorter(graph).static_order())
        except CycleError as error:
            raise ValueError(f"plugin dependency cycle: {error.args[1]}") from error

    async def __aenter__(self) -> "PluginHost":
        if self._state != "new":
            raise RuntimeError("PluginHost can only be entered once")
        self._state = "installing"
        try:
            order = self._order()
            plugins = {plugin.name: plugin for plugin in self._plugins}
            for name in order:
                plugin = plugins[name]
                self._services[name] = {}
                context = PluginContext(self, plugin)
                try:
                    result = plugin.install(context)
                    if isawaitable(result):
                        await result
                finally:
                    context._installing = False
            self._state = "ready"
            return self
        except BaseException:
            await self.__aexit__(*sys.exc_info())
            raise

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self._state = "closing"
        self._exit_exception = (exc_type, exc, traceback)
        try:
            await self._stack.aclose()
        finally:
            self._exit_exception = (None, None, None)
            self._services.clear()
            self._state = "closed"
        return False

    def service(self, plugin: str, service: str) -> Any:
        if self._state != "ready":
            raise RuntimeError("services require a ready PluginHost")
        return self._services[plugin][service]
