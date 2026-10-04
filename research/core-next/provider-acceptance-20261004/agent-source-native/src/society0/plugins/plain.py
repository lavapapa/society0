"""零状态、零领域能力的空白基准插件。"""
from ..kernel.plugins import Plugin


def plain_plugin(*, name='plain'):
    return Plugin(name)
