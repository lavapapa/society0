"""仿真 step 内临时状态的生命周期容器。"""

from __future__ import annotations

from collections.abc import Iterator, MutableMapping
from typing import Any


class _StepNamespace(MutableMapping[str, Any]):
    """在父 scope 失效后拒绝继续读写的映射。"""

    __slots__ = ("_data", "_scope")

    def __init__(self, scope: "StepRuntimeScope") -> None:
        self._scope = scope
        self._data: dict[str, Any] = {}

    def __getitem__(self, key: str) -> Any:
        self._scope._require_active()
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._scope._require_active()
        self._data[key] = value

    def __delitem__(self, key: str) -> None:
        self._scope._require_active()
        del self._data[key]

    def __iter__(self) -> Iterator[str]:
        self._scope._require_active()
        return iter(self._data)

    def __len__(self) -> int:
        self._scope._require_active()
        return len(self._data)

    def invalidate(self) -> None:
        self._data.clear()


class StepRuntimeScope:
    """只在一个正在执行的 step 内有效的非持久化状态。

    这里保存 FoV cursor、阶段索引和去重集合等派生数据。它不属于
    canonical World，也不会进入 checkpoint。step 成功、失败或恢复后，
    旧 scope 都会失效；继续使用旧引用会明确报错。
    """

    __slots__ = ("_active", "_namespaces", "step")

    def __init__(self, step: int) -> None:
        if isinstance(step, bool) or not isinstance(step, int) or step < 0:
            raise ValueError("step 必须是非负整数")
        self.step = step
        self._active = True
        self._namespaces: dict[str, _StepNamespace] = {}

    @property
    def active(self) -> bool:
        return self._active

    def namespace(self, owner: str) -> MutableMapping[str, Any]:
        """返回指定 owner 的 step-local 命名空间。"""

        self._require_active()
        normalized = str(owner or "").strip()
        if not normalized:
            raise ValueError("step runtime namespace owner 不能为空")
        namespace = self._namespaces.get(normalized)
        if namespace is None:
            namespace = _StepNamespace(self)
            self._namespaces[normalized] = namespace
        return namespace

    def invalidate(self) -> None:
        """清空所有派生状态，并使已分发的旧引用失效。"""

        if not self._active:
            return
        for namespace in self._namespaces.values():
            namespace.invalidate()
        self._namespaces.clear()
        self._active = False

    def _require_active(self) -> None:
        if not self._active:
            raise RuntimeError("step runtime scope 已失效")
