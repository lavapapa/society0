"""决策专用动作的授权投影：配置只给动作名与目标，schema 始终取自活动注册表。"""
from __future__ import annotations

from dataclasses import dataclass

from ..async_utils import invoke_maybe_async
from .interaction import Ref


@dataclass(frozen=True)
class ExposedAction:
    """一次激活中出现在模型工具数组的专用动作及其授权 schema。"""
    alias: str
    action: str
    target: Ref
    description: str
    parameters: dict
    strict: bool


def tool_alias(name):
    """提供方函数名只接受字母数字与下划线；别名是动作名的稳定转写。"""
    alias = ''.join(character if character.isalnum() or character in '-_' else '_' for character in name)
    if not alias or not alias[0].isalpha():
        raise ValueError('动作名无法转写为提供方函数名：' + repr(name))
    return alias


def _target(value):
    if isinstance(value, Ref):
        return value
    if isinstance(value, dict):
        return Ref(**value)
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return Ref(*value)
    raise TypeError('专用动作目标需要 Ref、{namespace,kind,key} 或三元组')


def _is_single(item):
    """单项形状是 (动作名, 目标)；动作名必须是字符串，序列则逐项展开。"""
    return isinstance(item, (tuple, list)) and len(item) == 2 and isinstance(item[0], str)


def selection_items(selection):
    """选择器返回单项或有序序列；此处只取身份，schema 始终来自活动注册表。"""
    if selection is None:
        return ()
    if isinstance(selection, (str, dict)):
        raise TypeError('专用动作需要 (动作名, 目标) 或 ExposedAction；导出一份有序序列或单项')
    if isinstance(selection, ExposedAction) or _is_single(selection):
        selection = (selection,)
    items = []
    for item in selection:
        if isinstance(item, ExposedAction):
            items.append((item.action, item.target))
        elif isinstance(item, str):
            raise TypeError('专用动作需要显式目标，动作名必须与 Ref target 一起声明：' + item)
        else:
            name, target = item
            if not isinstance(name, str) or not name:
                raise TypeError('专用动作名需要非空字符串')
            items.append((name, _target(target)))
    if len({name for name, _ in items}) != len(items):
        raise ValueError('同一专用动作在一次激活中重复声明')
    return tuple(items)


class ToolExposure:
    """把配置选定的专用动作投影为工具数组项，并保留别名到规范动作的路由。"""

    def __init__(self, selector):
        if selector is None or not callable(selector):
            raise TypeError('工具暴露选择器需要以 session 调用的同步或异步回调')
        self.selector = selector

    async def actions(self, session, ledger):
        """逐个取得授权描述；选定但不可用的动作明确失败，不静默移出工具数组。"""
        items = selection_items(await invoke_maybe_async(self.selector, session))
        exposed = {}
        for name, target in items:
            description = await ledger.describe_exposed(name, target)
            alias = tool_alias(name)
            if alias in exposed:
                raise ValueError('专用动作别名冲突：' + alias)
            exposed[alias] = ExposedAction(alias, name, target, description.description,
                                           description.parameters, bool(description.strict))
        return exposed
