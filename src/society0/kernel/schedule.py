"""代码调度的薄入口与独立进度快照。"""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import time
from dataclasses import dataclass
from typing import Protocol
from .runtime import Phase


class Progress:
    """允许丢失的短进度，不访问权威业务数据库。"""
    def __init__(self,path,run_id):
        self.path=Path(path)
        self.run_id=run_id

    def _replace(self,value):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        descriptor,name=tempfile.mkstemp(prefix=self.path.name+'.',dir=self.path.parent)
        try:
            with os.fdopen(descriptor,'w') as stream:json.dump(value,stream,ensure_ascii=False,separators=(',',':'))
            os.replace(name,self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def update(self,**values):
        try:self._replace({'run_id':self.run_id,'updated_at':time.time(),**values})
        except OSError:return False
        return True

    def read(self):
        return json.loads(self.path.read_text())


async def activate(context,identifiers,*,payload=None):
    if hasattr(identifiers,'__aiter__'):
        async for identifier in identifiers:context.activate(identifier,payload)
    else:
        for identifier in identifiers:context.activate(identifier,payload)
    return await context.drain()


@dataclass(frozen=True)
class StepPlan:
    """一次完整步骤的模拟时间与有序业务阶段。"""
    time: object
    phases: tuple[Phase, ...]

    def __post_init__(self):
        object.__setattr__(self, 'phases', tuple(self.phases))
        if any(not isinstance(phase, Phase) for phase in self.phases):
            raise TypeError('step phases must be Phase instances')


class Schedule(Protocol):
    async def next_step(self, completed_step: int) -> StepPlan | None: ...


@dataclass(frozen=True)
class SequenceSchedule:
    """冻结完整时间线；下一项由完整步骤身份定位，读取不推进游标。"""
    times: tuple[object, ...]
    phases: tuple[Phase, ...]

    def __post_init__(self):
        object.__setattr__(self, 'times', tuple(self.times))
        object.__setattr__(self, 'phases', StepPlan(None, self.phases).phases)

    async def next_step(self, completed_step: int) -> StepPlan | None:
        if type(completed_step) is not int or completed_step < 0:
            raise ValueError('completed_step must be a nonnegative integer')
        if completed_step >= len(self.times):
            return None
        return StepPlan(self.times[completed_step], self.phases)


def progress_plugin(*,storage=('storage','store'),name='progress'):
    from .plugins import Plugin
    def install(context):
        store=context.require(*storage)
        context.provide('progress',Progress(store.path/'progress.json',store.read(lambda view:view.run_id)))
    return Plugin(name,(storage[0],),install)


def schedule_plugin(schedule: Schedule, *, name='schedule'):
    from .plugins import Plugin
    def install(context): context.provide('schedule', schedule)
    return Plugin(name, install=install)
