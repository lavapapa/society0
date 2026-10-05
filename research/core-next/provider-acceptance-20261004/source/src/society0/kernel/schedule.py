"""代码调度的薄入口与独立进度快照。"""
from __future__ import annotations
import json
import os
from pathlib import Path
import tempfile
import time
from dataclasses import dataclass
from ..async_utils import invoke_maybe_async
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


class RuleDriver:
    """以同一会话执行同步或异步规则；回调返回 DriverResult。"""
    def __init__(self,run):self._run=run

    async def run(self,session):return await invoke_maybe_async(self._run,session)


async def activate(context,identifiers,*,payload=None):
    if hasattr(identifiers,'__aiter__'):
        async for identifier in identifiers:context.activate(identifier,payload)
    else:
        for identifier in identifiers:context.activate(identifier,payload)
    return await context.drain()


@dataclass(frozen=True)
class PhasedSchedule:
    """显式有序阶段计划；绑定运行时后通过同一个完整步骤入口执行。"""
    phases: tuple[Phase, ...]

    def __post_init__(self):
        object.__setattr__(self, 'phases', tuple(self.phases))
        if any(not isinstance(phase, Phase) for phase in self.phases):
            raise TypeError('schedule phases must be Phase instances')

    def bind(self, runtime):
        return CodeSchedule(runtime, self.phases)


class FixedStep(PhasedSchedule):
    """每一步重复执行一个明确阶段，业务时间由RunPlan.moments提供。"""
    def __init__(self, run, *, name='step', prepare=None, execution='serial',
                 incomplete='fail_step', capacity=None):
        super().__init__((Phase(name, run, prepare, execution, incomplete, capacity),))


class CodeSchedule:
    def __init__(self,runtime,phases):
        self.runtime=runtime
        self.phases=tuple(phases)

    async def run_step(self,step,time):
        return await self.runtime.run_step(step,time,self.phases)

    async def close(self):await self.runtime.close()


def progress_plugin(*,storage=('storage','store'),name='progress'):
    from .plugins import Plugin
    def install(context):
        store=context.require(*storage)
        context.provide('progress',Progress(store.path/'progress.json',store.read(lambda view:view.run_id)))
    return Plugin(name,(storage[0],),install)


def schedule_plugin(phases,*,runtime=('runtime','runtime'),name='schedule'):
    from .plugins import Plugin
    plan = phases if isinstance(phases, PhasedSchedule) else PhasedSchedule(tuple(phases))
    def install(context):context.provide('schedule',plan.bind(context.require(*runtime)))
    return Plugin(name,(runtime[0],),install)
