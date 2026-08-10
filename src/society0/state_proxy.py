"""
State Proxy System: 上下文感知型状态代理

这个模块实现了 DictProxy 和 ListProxy，用于透明地拦截和记录状态修改操作。
代理对象的设计目标是让开发者可以像操作普通 Python 对象一样修改状态，
而所有的变更都会被自动捕获并记录为事件。

v3.0 新增功能：
- AccessContext: 访问上下文，用于权限控制
- agent_editable 权限检查（在 __setitem__ 时）
"""

from collections.abc import MutableMapping, MutableSequence
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Callable, Optional, Union
import logging
import copy

_MISSING = object()

logger = logging.getLogger(__name__)


# =============================================================================
# 访问控制
# =============================================================================

@dataclass
class AccessContext:
    """
    访问上下文，描述当前调用者的权限

    用于控制 Agent 的 behavior/action 对 state 的访问权限。
    Environment 的 rule/fov 拥有完全权限，不受此限制。
    """
    caller_type: str  # 'env_rule', 'env_fov', 'agent_behavior', 'agent_action', 'system'
    caller_id: str    # 具体的 rule/behavior 名称
    state_schema: Dict[str, Any]  # 当前 state 的 schema（含权限元数据）

    def can_write(self, field_name: str) -> bool:
        """
        判断当前调用者能否写入指定字段

        权限规则：
        1. Environment 的 rule/fov: 完全权限
        2. System (World 内部): 完全权限
        3. Agent 的 behavior/action: 检查 schema 中的 agent_editable 字段
        """
        # Environment 的 rule/fov 有完全权限
        if self.caller_type in ['env_rule', 'env_fov', 'system']:
            return True

        # Agent 的 behavior/action 需要检查 schema
        if self.caller_type in ['agent_behavior', 'agent_action']:
            properties = self.state_schema.get('properties', {})
            field_schema = properties.get(field_name, {})

            # 默认值：如果字段不在 schema 中，默认可写（向后兼容）
            return field_schema.get('agent_editable', True)

        # 其他情况（如直接的 Python 代码访问）允许
        return True

    def can_read(self, field_name: str) -> bool:
        """
        判断当前调用者能否读取指定字段

        读取权限目前全部开放（未来可扩展）
        """
        return True


class DictProxy(MutableMapping[str, Any]):
    """
    字典代理类，透明地拦截字典操作并生成状态变更事件。

    核心特性：
    1. 拦截所有修改操作（__setitem__, __delitem__, update, etc.）
    2. 递归创建代理以支持深层嵌套修改
    3. 立即将变更应用到目标字典
    4. 自动生成包含完整上下文的状态变更事件
    5. 🔑 v3.0: 支持基于 AccessContext 的权限控制
    """

    def __init__(
        self,
        target_dict: Dict[str, Any],
        event_recorder: Callable[[Any], None],
        context_provider: Callable[[], List[Dict[str, Any]]],
        path: Tuple[str, ...] = (),
        access_context: Optional[AccessContext] = None  # 🔑 新增
    ):
        """
        初始化字典代理

        Args:
            target_dict: 要代理的真实字典对象
            event_recorder: 事件记录回调，接收一个事件对象
            context_provider: 上下文提供回调，返回当前的 ContextStack
            path: 当前代理在状态树中的路径，如 ('agents', 'alice', 'state')
            access_context: 访问上下文，用于权限控制（可选）
        """
        # 使用 object.__setattr__ 避免触发自己的 __setattr__
        object.__setattr__(self, '_target_dict', target_dict)
        object.__setattr__(self, '_event_recorder', event_recorder)
        object.__setattr__(self, '_context_provider', context_provider)
        object.__setattr__(self, '_path', path)
        object.__setattr__(self, '_access_context', access_context)  # 🔑 新增

    _PROTECTED_ATTRS = {
        "_target_dict",
        "_event_recorder",
        "_context_provider",
        "_path",
        "_access_context",
    }

    @classmethod
    def _is_class_attribute(cls, name: str) -> bool:
        """判断名称是否为类级别已定义的属性/方法。"""
        return any(name in base.__dict__ for base in cls.__mro__)
    
    @staticmethod
    def _snapshot(value: Any) -> Any:
        """捕获值的快照，确保事件记录使用稳定副本。"""
        if value is _MISSING:
            return _MISSING
        try:
            return copy.deepcopy(value)
        except Exception:
            return value

    def _record_change(
        self,
        operation: str,
        key: Optional[str] = None,
        value: Any = _MISSING,
        old_value: Any = _MISSING,
    ) -> None:
        """记录状态变更事件"""
        try:
            # 构建变更路径
            if key is not None:
                change_path = self._path + (str(key),)
            else:
                change_path = self._path
            
            # 创建变更描述
            change = {
                "path": change_path,
                "operation": operation,
            }
            
            if value is not _MISSING:
                change["value"] = value
            if old_value is not _MISSING:
                change["old_value"] = old_value
            
            # 获取当前上下文栈
            context_stack = self._context_provider() if self._context_provider else []
            
            # 创建状态变更事件（这里先用简单的 dict，后续会替换为真正的 Event 类）
            event = {
                "event_type": "STATE_CHANGE",
                "change": change,
                "context_stack": context_stack
            }
            
            # 记录事件
            if self._event_recorder:
                self._event_recorder(event)
                
        except Exception as e:
            logger.error(f"Failed to record state change: {e}")
    
    def __getitem__(self, key: str) -> Any:
        """获取项目，支持递归代理创建"""
        value = self._target_dict[key]

        # 根据值的类型返回相应的代理或原始值
        if isinstance(value, dict):
            return DictProxy(
                target_dict=value,
                event_recorder=self._event_recorder,
                context_provider=self._context_provider,
                path=self._path + (str(key),),
                access_context=self._access_context  # 🔑 传递访问上下文
            )
        elif isinstance(value, list):
            return ListProxy(
                target_list=value,
                event_recorder=self._event_recorder,
                context_provider=self._context_provider,
                path=self._path + (str(key),),
                access_context=self._access_context  # 🔑 传递访问上下文
            )
        else:
            return value

    def __setitem__(self, key: str, value: Any) -> None:
        """设置项目，记录变更并立即生效"""
        # 🔑 权限检查
        if self._access_context:
            if not self._access_context.can_write(key):
                raise PermissionError(
                    f"权限不足：{self._access_context.caller_type} "
                    f"'{self._access_context.caller_id}' 无法修改字段 '{key}'。"
                    f"\n提示：该字段的 agent_editable 为 false，只能由 Environment 的 rule 修改。"
                )

        old_snapshot = self._snapshot(self._target_dict[key]) if key in self._target_dict else _MISSING
        self._target_dict[key] = value
        self._record_change("set", key, self._snapshot(value), old_snapshot)
    
    def __delitem__(self, key: str) -> None:
        """删除项目"""
        old_snapshot = self._snapshot(self._target_dict[key])
        del self._target_dict[key]
        self._record_change("delete", key, None, old_snapshot)
    
    def __contains__(self, key: str) -> bool:
        """检查键是否存在"""
        return key in self._target_dict
    
    def __len__(self) -> int:
        """获取字典长度"""
        return len(self._target_dict)
    
    def __iter__(self):
        """遍历字典键"""
        return iter(self._target_dict)

    def __getattr__(self, name: str) -> Any:
        """支持 attribute-style 访问字典字段。"""
        if name in self._PROTECTED_ATTRS or name.startswith("_"):
            raise AttributeError(name)

        target = self._target_dict
        if name in target:
            return self[name]
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """支持 attribute-style 写入字典字段。"""
        if name in self._PROTECTED_ATTRS or name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        if self._is_class_attribute(name):
            raise AttributeError(f"无法覆盖 DictProxy 内建属性 '{name}'")
        self[name] = value

    def __delattr__(self, name: str) -> None:
        """支持 attribute-style 删除字典字段。"""
        if name in self._PROTECTED_ATTRS or name.startswith("_") or self._is_class_attribute(name):
            raise AttributeError(f"无法删除 DictProxy 内建属性 '{name}'")
        if name in self._target_dict:
            self.__delitem__(name)
        else:
            raise AttributeError(name)

    def __dir__(self):
        """丰富调试体验：列出字段名称。"""
        base = set(super().__dir__())
        base.update(self._target_dict.keys())
        return sorted(base)
    
    def keys(self):
        """获取所有键"""
        return self._target_dict.keys()
    
    def values(self):
        """获取所有值（注意：这些值不是代理对象）"""
        return self._target_dict.values()
    
    def items(self):
        """获取所有键值对（注意：值不是代理对象）"""
        return self._target_dict.items()
    
    def get(self, key: str, default: Any = None) -> Any:
        """安全获取值，支持代理递归"""
        if key in self._target_dict:
            return self[key]  # 使用 __getitem__ 确保返回代理
        return default
    
    def update(self, other: Union[Dict[str, Any], 'DictProxy']) -> None:
        """批量更新字典"""
        if isinstance(other, DictProxy):
            other_dict = other._target_dict
        else:
            other_dict = other
            
        for key, value in other_dict.items():
            self[key] = value  # 使用 __setitem__ 确保每个变更都被记录
    
    def pop(self, key: str, default: Any = _MISSING) -> Any:
        """弹出并返回值，与内建 ``dict.pop`` 保持相同语义。"""
        if key in self._target_dict:
            value = self._target_dict.pop(key)
            self._record_change("delete", key, None, self._snapshot(value))
            return value
        elif default is not _MISSING:
            return default
        else:
            raise KeyError(key)
    
    def clear(self) -> None:
        """清空字典"""
        previous = self._snapshot(dict(self._target_dict))
        self._target_dict.clear()
        self._record_change("clear", value={}, old_value=previous)
    
    def setdefault(self, key: str, default: Any = None) -> Any:
        """设置默认值"""
        if key not in self._target_dict:
            self[key] = default
        return self[key]
    
    def __repr__(self) -> str:
        """字符串表示"""
        return f"DictProxy({self._target_dict!r})"

    def __deepcopy__(self, memo: Dict[int, Any]) -> Dict[str, Any]:
        """生成与代理生命周期脱离的普通字典快照。

        代理只承担运行时访问和变更记录。业务代码使用 ``deepcopy`` 捕获事实
        快照时，不应把嵌套 ``DictProxy`` / ``ListProxy`` 带入 canonical state。
        """

        existing = memo.get(id(self))
        if existing is not None:
            return existing
        result: Dict[str, Any] = {}
        memo[id(self)] = result
        for key, value in self._target_dict.items():
            result[key] = copy.deepcopy(value, memo)
        return result
    
    def __str__(self) -> str:
        """字符串表示"""
        return str(self._target_dict)


class ListProxy(MutableSequence[Any]):
    """
    列表代理类，透明地拦截列表操作并生成状态变更事件。

    核心特性：
    1. 拦截所有修改操作（append, extend, insert, etc.）
    2. 递归创建代理以支持深层嵌套修改
    3. 立即将变更应用到目标列表
    4. 自动生成包含完整上下文的状态变更事件
    5. 🔑 v3.0: 支持 AccessContext 传递（列表本身不检查权限，由父 DictProxy 控制）
    """

    def __init__(
        self,
        target_list: List[Any],
        event_recorder: Callable[[Any], None],
        context_provider: Callable[[], List[Dict[str, Any]]],
        path: Tuple[str, ...] = (),
        access_context: Optional[AccessContext] = None  # 🔑 新增
    ):
        """
        初始化列表代理

        Args:
            target_list: 要代理的真实列表对象
            event_recorder: 事件记录回调，接收一个事件对象
            context_provider: 上下文提供回调，返回当前的 ContextStack
            path: 当前代理在状态树中的路径，如 ('agents', 'alice', 'state', 'inventory')
            access_context: 访问上下文（传递用，列表本身不进行权限检查）
        """
        object.__setattr__(self, '_target_list', target_list)
        object.__setattr__(self, '_event_recorder', event_recorder)
        object.__setattr__(self, '_context_provider', context_provider)
        object.__setattr__(self, '_path', path)
        object.__setattr__(self, '_access_context', access_context)  # 🔑 新增
    
    @staticmethod
    def _snapshot(value: Any) -> Any:
        """捕获值的快照，确保事件记录的稳定性。"""
        if value is _MISSING:
            return _MISSING
        try:
            return copy.deepcopy(value)
        except Exception:
            return value

    def _record_change(
        self,
        operation: str,
        index: Optional[int] = None,
        value: Any = _MISSING,
        old_value: Any = _MISSING,
    ):
        """记录状态变更事件"""
        try:
            # 构建变更路径
            if index is not None:
                change_path = self._path + (str(index),)
            else:
                change_path = self._path
            
            # 创建变更描述
            change = {
                "path": change_path,
                "operation": operation,
            }
            
            if value is not _MISSING:
                change["value"] = value
            if old_value is not _MISSING:
                change["old_value"] = old_value
            
            # 获取当前上下文栈
            context_stack = self._context_provider() if self._context_provider else []
            
            # 创建状态变更事件
            event = {
                "event_type": "STATE_CHANGE",
                "change": change,
                "context_stack": context_stack
            }
            
            # 记录事件
            if self._event_recorder:
                self._event_recorder(event)
                
        except Exception as e:
            logger.error(f"Failed to record state change: {e}")
    
    def __getitem__(self, index: int) -> Any:
        """获取项目，支持递归代理创建"""
        value = self._target_list[index]

        # 根据值的类型返回相应的代理或原始值
        if isinstance(value, dict):
            return DictProxy(
                target_dict=value,
                event_recorder=self._event_recorder,
                context_provider=self._context_provider,
                path=self._path + (str(index),),
                access_context=self._access_context  # 🔑 传递访问上下文
            )
        elif isinstance(value, list):
            return ListProxy(
                target_list=value,
                event_recorder=self._event_recorder,
                context_provider=self._context_provider,
                path=self._path + (str(index),),
                access_context=self._access_context  # 🔑 传递访问上下文
            )
        else:
            return value
    
    def __setitem__(self, index: int, value: Any) -> None:
        """设置项目，记录变更并立即生效"""
        old_snapshot = self._snapshot(self._target_list[index])
        self._target_list[index] = value
        self._record_change("set", index, self._snapshot(value), old_snapshot)
    
    def __delitem__(self, index: int) -> None:
        """删除项目"""
        old_snapshot = self._snapshot(self._target_list[index])
        del self._target_list[index]
        self._record_change("delete", index, None, old_snapshot)
    
    def __len__(self) -> int:
        """获取列表长度"""
        return len(self._target_list)
    
    def __iter__(self):
        """遍历列表（注意：这些值不是代理对象）"""
        return iter(self._target_list)

    def __deepcopy__(self, memo: Dict[int, Any]) -> List[Any]:
        """生成与代理生命周期脱离的普通列表快照。"""

        existing = memo.get(id(self))
        if existing is not None:
            return existing
        result: List[Any] = []
        memo[id(self)] = result
        result.extend(copy.deepcopy(value, memo) for value in self._target_list)
        return result
    
    def append(self, value: Any) -> None:
        """追加元素"""
        index = len(self._target_list)
        self._target_list.append(value)
        self._record_change("append", index, self._snapshot(value))
    
    def extend(self, values: List[Any]) -> None:
        """扩展列表"""
        start_index = len(self._target_list)
        self._target_list.extend(values)
        for offset, item in enumerate(values):
            self._record_change("append", start_index + offset, self._snapshot(item))
    
    def insert(self, index: int, value: Any) -> None:
        """插入元素"""
        self._target_list.insert(index, value)
        self._record_change("insert", index, self._snapshot(value))
    
    def remove(self, value: Any) -> None:
        """移除元素"""
        # 找到要移除的元素的索引
        index = self._target_list.index(value)
        removed = self._target_list.pop(index)
        self._record_change("remove", index, None, self._snapshot(removed))
    
    def pop(self, index: int = -1) -> Any:
        """弹出元素"""
        value = self._target_list.pop(index)
        self._record_change("pop", index, None, self._snapshot(value))
        return value
    
    def clear(self) -> None:
        """清空列表"""
        previous = self._snapshot(list(self._target_list))
        self._target_list.clear()
        self._record_change("clear", value=[], old_value=previous)
    
    def index(self, value: Any, start: int = 0, stop: Optional[int] = None) -> int:
        """查找元素索引"""
        if stop is None:
            return self._target_list.index(value, start)
        else:
            return self._target_list.index(value, start, stop)
    
    def count(self, value: Any) -> int:
        """计算元素出现次数"""
        return self._target_list.count(value)
    
    def reverse(self) -> None:
        """反转列表"""
        previous = self._snapshot(list(self._target_list))
        self._target_list.reverse()
        self._record_change("reverse", value=self._snapshot(list(self._target_list)), old_value=previous)
    
    def sort(self, key=None, reverse=False) -> None:
        """排序列表"""
        previous = self._snapshot(list(self._target_list))
        self._target_list.sort(key=key, reverse=reverse)
        self._record_change("sort", value=self._snapshot(list(self._target_list)), old_value=previous)
    
    def __repr__(self) -> str:
        """字符串表示"""
        return f"ListProxy({self._target_list!r})"
    
    def __str__(self) -> str:
        """字符串表示"""
        return str(self._target_list)
