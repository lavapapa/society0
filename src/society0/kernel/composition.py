"""先建立插件声明的共享状态，再安装运行服务。"""
from contextlib import AsyncExitStack, asynccontextmanager
from inspect import isawaitable, iscoroutine
import sys

from .plugins import Plugin, PluginHost
from .storage import StageStore


@asynccontextmanager
async def compose(path, plugins, *, source=None, step=None):
    """创建或恢复一个共享运行；schema 与 initialize 由领域插件声明。"""
    plugins = tuple(plugins)
    store = None

    def install_storage(context):
        context.provide('store', store)

    host = PluginHost((Plugin('storage', install=install_storage), *plugins))
    order = host._order()
    declared = {plugin.name: plugin for plugin in plugins}
    ordered = [declared[name] for name in order if name != 'storage']
    schema = tuple(ddl for plugin in ordered for ddl in plugin.schema)

    async with AsyncExitStack() as resources:
        if source is None:
            if step is not None:
                raise ValueError('step requires a restore source')
            store = await _create_prepared(path, schema, ordered, resources)
        else:
            StageStore.validate_schema(source, schema)
            store = StageStore.restore(source, path, step=step)
            resources.enter_context(store)
        await resources.enter_async_context(host)
        yield host


async def _create_prepared(path, schema, plugins, resources):
    # 准备闭包仅活在此函数中，正式运行不保留载入的数据。
    initializers = []
    try:
        async with AsyncExitStack() as preparation:
            for plugin in plugins:
                if plugin.initialize is not None:
                    initializers.append(plugin.initialize)
                if plugin.prepare is not None:
                    manager = plugin.prepare()
                    initializer = await preparation.enter_async_context(_preparation_resource(manager))
                    initializers.append(initializer)

            def initialize(writer):
                for callback in initializers:
                    result = callback(writer)
                    if isawaitable(result):
                        if iscoroutine(result):
                            result.close()
                        raise TypeError('plugin initialize must be synchronous')

            store = StageStore.create(path, schema, initialize=initialize)
            # prepare 清理失败时，外层栈仍能释放已发布根的写者锁。
            resources.enter_context(store)
        return store
    finally:
        initializers.clear()


@asynccontextmanager
async def _preparation_resource(manager):
    resource = AsyncExitStack()
    if hasattr(type(manager), '__aenter__'):
        value = await resource.enter_async_context(manager)
    else:
        value = resource.enter_context(manager)
    try:
        yield value
    finally:
        # 初始化失败与清理失败都向调用方传播，资源不能把失败变成成功根。
        await resource.__aexit__(*sys.exc_info())
