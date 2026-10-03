"""先建立插件声明的共享状态，再安装运行服务。"""
from contextlib import AsyncExitStack, asynccontextmanager
from inspect import isawaitable, iscoroutine

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

    def initialize(writer):
        for plugin in ordered:
            if plugin.initialize is not None:
                result = plugin.initialize(writer)
                if isawaitable(result):
                    if iscoroutine(result):
                        result.close()
                    raise TypeError('plugin initialize must be synchronous')

    async with AsyncExitStack() as resources:
        if source is None:
            if step is not None:
                raise ValueError('step requires a restore source')
            store = StageStore.create(path, schema, initialize=initialize)
        else:
            StageStore.validate_schema(source, schema)
            store = StageStore.restore(source, path, step=step)
        resources.enter_context(store)
        await resources.enter_async_context(host)
        yield host
