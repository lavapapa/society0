"""发布者持有 epoch 集合，World 和主体共享其只读成员查询。"""
from collections.abc import Set


class PublishedEpochs(Set):
    """已提交集合由存储管理器更新；待提交集合可在失败时原位清除。"""

    def __init__(self, epochs: Set[str], pending: Set[str] | None = None):
        self._epochs = epochs
        self._pending = pending if pending is not None else frozenset()

    def __contains__(self, epoch):
        return epoch in self._epochs or epoch in self._pending

    def __iter__(self):
        yield from self._epochs
        yield from (epoch for epoch in self._pending if epoch not in self._epochs)

    def __len__(self):
        return len(self._epochs) + sum(epoch not in self._epochs for epoch in self._pending)
