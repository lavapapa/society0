"""插件依赖与资源生命周期；不承载仿真状态或调度规则。"""

from collections.abc import Callable, Iterable
from contextlib import AsyncExitStack
from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter
from inspect import isawaitable
import sys
import asyncio
from typing import Any


def _noop(context: "PluginContext") -> None:
    pass


@dataclass(frozen=True)
class Plugin:
    name: str
    requires: tuple[str, ...] = ()
    install: Callable[["PluginContext"], Any] = _noop
    schema: tuple[str, ...] = ()
    initialize: Callable[[Any], Any] | None = None
    prepare: Callable[[], Any] | None = None
    includes: tuple['Plugin', ...] = ()
    schema_requires: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "requires", tuple(self.requires))
        object.__setattr__(self, "schema", tuple(self.schema))
        object.__setattr__(self, "includes", tuple(self.includes))
        object.__setattr__(self, "schema_requires", tuple(self.schema_requires))


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

    def on_step(self, *, before=None, after=None) -> None:
        self._check_installing()
        for destination,callback in ((self._host._before_step,before),(self._host._after_step,after)):
            if callback is not None:
                destination.append((self.name,callback))

    def step_hooks(self):
        if self._host._state != "ready":
            raise RuntimeError("step hooks require a ready PluginHost")
        return tuple(self._host._before_step),tuple(self._host._after_step)

    def on_quiesce(self, callback: Callable, *args: Any, **kwargs: Any) -> None:
        self._check_installing()
        async def stop():
            result=callback(*args,**kwargs)
            if isawaitable(result):await result
        self._host._quiesce.push_async_callback(stop)

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


class _HostStop(Exception):
    """让标准任务组发起一次有序关闭。"""


class PluginHost:
    """单次、串行安装的异步资源作用域；服务按插件名分区。"""

    def __init__(self, plugins: Iterable[Plugin]):
        expanded = {}
        pending = list(reversed(tuple(plugins)))
        while pending:
            plugin = pending.pop()
            if plugin.name in expanded:
                raise ValueError(f"duplicate plugin: {plugin.name}")
            expanded[plugin.name] = plugin
            pending.extend(reversed(plugin.includes))
        self._plugins = tuple(expanded.values())
        self._services: dict[str, dict[str, Any]] = {}
        self._stack = AsyncExitStack()
        self._quiesce = AsyncExitStack()
        self._before_step = []
        self._after_step = []
        self._state = "new"
        self._exit_exception = (None, None, None)
        self._install_error = None

    def _order(self, dependency='requires') -> tuple[str, ...]:
        graph = {plugin.name: getattr(plugin, dependency) for plugin in self._plugins}
        for name, dependencies in graph.items():
            for target in dependencies:
                if target not in graph:
                    raise ValueError(f"missing plugin dependency: {name} {dependency} {target}")
        try:
            return tuple(TopologicalSorter(graph).static_order())
        except CycleError as error:
            raise ValueError(f"plugin {dependency} dependency cycle: {error.args[1]}") from error

    async def __aenter__(self) -> "PluginHost":
        if self._state != "new":
            raise RuntimeError("PluginHost can only be entered once")
        order = self._order()
        self._order('schema_requires')
        self._state = "installing"
        self._ready = asyncio.Event()
        self._stopped = asyncio.Event()
        self._stop = asyncio.Event()
        self._error = None
        self._owner = asyncio.create_task(self._serve(order), name='plugin-host')
        try:
            await self._ready.wait()
        except BaseException:
            self._owner.cancel()
            await self._wait_closed()
            raise
        if self._error is not None:
            raise self._error
        return self

    async def _serve(self, order):
        try:
            try:
                async with asyncio.TaskGroup() as group:
                    group.create_task(self._own_resources(order), name='plugin-resources')
                    group.create_task(self._stop_owner(), name='plugin-stop')
                    await asyncio.Future()
            except* _HostStop:
                pass
        except asyncio.CancelledError:
            pass
        except BaseExceptionGroup as errors:
            self._error = errors.exceptions[0] if len(errors.exceptions) == 1 else errors
        except BaseException as error:
            self._error = error
        finally:
            if self._error is None:
                self._error = self._install_error
            self._ready.set()
            self._stopped.set()

    async def _wait_closed(self):
        async def finish():
            try:
                await self._stopped.wait()
            except asyncio.CancelledError:
                await self._stopped.wait()
        async with asyncio.TaskGroup() as group:
            group.create_task(finish(), name='plugin-close')

    async def _stop_owner(self):
        await self._stop.wait()
        if self._install_error is not None and not isinstance(self._install_error, asyncio.CancelledError):
            raise self._install_error
        raise _HostStop()

    async def _own_resources(self, order):
        try:
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
            self._ready.set()
            await asyncio.Future()
        except asyncio.CancelledError as error:
            if not self._ready.is_set():
                self._install_error = error
                self._exit_exception = sys.exc_info()
            if not self._stop.is_set() and self._exit_exception == (None, None, None):
                self._exit_exception = sys.exc_info()
            raise
        except BaseException as error:
            self._exit_exception = sys.exc_info()
            self._install_error = error
            self._stop.set()
            # 由stop任务先令整个组进入退出，再收束已安装资源。
            await asyncio.Future()
        finally:
            self._state = "closing"
            try:
                try:
                    await self._quiesce.aclose()
                finally:
                    await self._stack.aclose()
            except asyncio.CancelledError as error:
                raise RuntimeError('plugin resource cleanup was cancelled') from error
            finally:
                self._exit_exception = (None, None, None)
                self._services.clear()
                self._before_step.clear()
                self._after_step.clear()
                self._state = "closed"
                self._stop.set()

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self._exit_exception = (exc_type, exc, traceback)
        self._stop.set()
        await self._wait_closed()
        if self._error is not None:
            raise self._error
        return False

    def service(self, plugin: str, service: str) -> Any:
        if self._state != "ready":
            raise RuntimeError("services require a ready PluginHost")
        return self._services[plugin][service]
