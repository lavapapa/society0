"""共享环境的顺序阶段与有界主体激活；不推进墙钟对应的模拟时间。"""

from __future__ import annotations

import asyncio
import time as time_module
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any
from itertools import chain

from ..activation_pool import ActivationPool, ActivationLimitError, DEFAULT_MAX_ACTIVATIONS
from ..async_utils import invoke_maybe_async
from .interaction import InteractionScope, Moment
from .plugins import Plugin


@dataclass(frozen=True)
class Actor:
    id: str
    driver: Any
    state_ref: Any = None
    config: Any = None


@dataclass(frozen=True)
class DriverResult:
    status: str
    value: Any = None
    reason: str | None = None

    def __post_init__(self):
        if self.status not in ('completed', 'waiting', 'incomplete'):
            raise ValueError('unknown driver status')


@dataclass(frozen=True)
class ActorResult:
    actor_id: str
    round: int
    result: DriverResult


@dataclass(frozen=True)
class Phase:
    name: str
    run: Callable
    prepare: Callable | None = None
    execution: str = 'serial'
    incomplete: str = 'fail_step'
    capacity: int | None = None

    def __post_init__(self):
        if self.execution not in ('serial', 'independent'):
            raise ValueError('phase execution must be serial or independent')
        if self.incomplete not in ('fail_step', 'collect'):
            raise ValueError('incomplete policy must be fail_step or collect')
        if self.capacity is not None:
            if type(self.capacity) is not int or self.capacity < 1:
                raise ValueError('phase capacity must be a positive integer')
            if self.execution == 'serial' and self.capacity != 1:
                raise ValueError('serial phase capacity must be one')


class IncompleteActivation(RuntimeError):
    pass


@dataclass(frozen=True)
class Session:
    actor: Actor
    scope: InteractionScope
    information: Any
    actions: Any
    cursors: dict
    prepared: Any
    signals: tuple
    _phase: PhaseContext
    step: int = field(kw_only=True)

    @property
    def moment(self):
        return self.scope.moment

    @property
    def state_ref(self):
        return self.actor.state_ref

    def prepare_artifact(self, chunks):
        self.scope.check_active()
        return self._phase.prepare_artifact(chunks)

    def activate(self, actor_id, payload=None, *, dedupe_token=None):
        self.scope.check_active()
        return self._phase.activate(actor_id, payload, dedupe_token=dedupe_token)


class PhaseContext:
    def __init__(self, runtime, phase, moment):
        self.moment = moment
        self.prepared = None
        self._runtime = runtime
        self._incomplete = phase.incomplete
        self._actor_order = {}
        self._consumed_results = set()
        self._elapsed = {}
        self._active = True
        self._ready = False
        self._failure = asyncio.get_running_loop().create_future()
        remaining = None if runtime.max_activations is None else runtime.max_activations - runtime._activations_used
        self._exhausted = remaining == 0
        self.capacity = 1 if phase.execution == 'serial' else (phase.capacity if phase.capacity is not None else runtime.capacity)
        self.concurrency_source = 'serial phase' if phase.execution == 'serial' else ('phase' if phase.capacity is not None else 'runtime')
        self._pool = ActivationPool(
            capacity=self.capacity,
            concurrency_source=self.concurrency_source, max_activations=1 if self._exhausted else remaining,
        )

    def prepare_artifact(self, chunks):
        if not self._active:
            raise RuntimeError('phase is closed')
        reference = self._runtime.store.prepare_artifact(chunks)
        self._runtime._artifacts.append(reference)
        return reference

    def activate(self, actor_id, payload=None, *, dedupe_token=None):
        if not self._active or not self._ready or self._failure.done():
            raise RuntimeError('phase is not accepting activations')
        if self._exhausted:
            raise ActivationLimitError(maximum=self._runtime.max_activations,
                                       used=self._runtime._activations_used,
                                       pending_keys=(actor_id,), active_keys=())
        self._actor_order.setdefault(actor_id, len(self._actor_order))

        async def execute(batch):
            if not self._active or self._failure.done():
                raise RuntimeError('phase has failed')
            scope = None
            started=time_module.perf_counter()
            self._runtime._active_count+=1
            self._runtime._observe()
            try:
                actor = self._runtime._actors[actor_id]
                if actor.id != actor_id:
                    raise ValueError('actor mapping key differs from actor identity')
                scope = InteractionScope(actor.id, self.moment)
                session = Session(
                    actor, scope, self._runtime.information.bound(scope), self._runtime.actions.bound(scope),
                    self._runtime._cursors.setdefault(actor.id, {}), self.prepared, batch.payloads, self, step=self._runtime._step,
                )
                result = await actor.driver.run(session)
                if not isinstance(result, DriverResult):
                    raise TypeError('driver must return DriverResult')
                if result.status == 'incomplete' and self._incomplete == 'fail_step':
                    raise IncompleteActivation(result.reason or 'driver incomplete')
                return result
            except BaseException as error:
                if not self._failure.done():
                    self._failure.set_result(error)
                raise
            finally:
                self._elapsed[(actor_id,batch.round)]=time_module.perf_counter()-started
                self._runtime._active_count-=1
                self._runtime._finished_count+=1
                self._runtime._observe()
                if scope is not None:
                    scope.close()

        return self._pool.submit_agent(actor_id, actor_id, execute, payload=payload,
                                       dedupe_token=dedupe_token, handler_id=actor_id)

    @property
    def results(self):
        results = (ActorResult(item.key, item.round, item.value) for item in self._pool.results
                   if item.status == 'success')
        return tuple(sorted(results, key=lambda item: (self._actor_order[item.actor_id], item.round)))

    async def drain(self):
        if not self._active:
            raise RuntimeError('phase is closed')
        await self._pool.drain()
        results = tuple(item for item in self.results
                        if (item.actor_id, item.round) not in self._consumed_results)
        self._consumed_results.update((item.actor_id, item.round) for item in results)
        return results


class Runtime:
    def __init__(self, actors: Iterable[Actor], *, information, actions, store,
                 capacity=1, max_activations=DEFAULT_MAX_ACTIVATIONS,results=None,progress=None,
                 before=(),after=(),step_hooks=None):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('runtime capacity must be a positive integer')
        if isinstance(actors, Mapping):
            self._actors = actors
        else:
            self._actors = {}
            for actor in actors:
                if actor.id in self._actors:
                    raise ValueError(f'duplicate actor: {actor.id}')
                self._actors[actor.id] = actor
        self._before=tuple(before)
        self._after=tuple(after)
        self._step_hooks=step_hooks
        self.results=results
        self.progress=progress
        self._active_count=0
        self._finished_count=0
        self._step=None
        self._phase_name=None
        self._failure_reason=None
        self.last_timing=None
        self.information = information
        self.actions = actions
        self.store = store
        self.capacity = capacity
        self.max_activations = max_activations
        self.last_completed = store.complete_step
        self._state = 'ready'
        self._task = None
        self._close_task = None
        self._moment = None
        self._cursors = {}
        self._activations_used = 0
        self._artifacts = []

    def _observe(self):
        if self.progress is not None:
            self.progress.update(state=self._state,step=self._step,phase=self._phase_name,
                                 active=self._active_count,finished=self._finished_count,capacity=self.capacity,error=self._failure_reason,timing=self.last_timing)

    async def _run_phase(self, time_value, phase,phase_index):
        started=time_module.perf_counter()
        self._phase_name=phase.name
        self._observe()
        moment = Moment(time_value, phase.name)
        if moment != self._moment:
            self._cursors.clear()
            self._moment = moment
        context = PhaseContext(self, phase, moment)
        await context._pool.start()

        async def body():
            if phase.prepare is not None:
                context.prepared = await invoke_maybe_async(phase.prepare, context)
            context._ready = True
            result=await invoke_maybe_async(phase.run, context)
            await context._pool.close()
            return result

        task = asyncio.create_task(body())
        try:
            done, _ = await asyncio.wait((task, context._failure), return_when=asyncio.FIRST_COMPLETED)
            if context._failure in done:
                raise context._failure.result()
            result=await task
            context._active=False
            if self.results is not None:
                await self.results.write_phase(self._step,phase_index,phase.name,result,
                    activations=({'actor_id':item.actor_id,'round':item.round,'status':item.result.status,
                        'reason':item.result.reason,'value':item.result.value,
                        'elapsed_s':context._elapsed[(item.actor_id,item.round)]} for item in context.results),
                    elapsed_s=time_module.perf_counter()-started,
                    capacity=context.capacity,concurrency_source=context.concurrency_source)
        finally:
            context._active = False
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await context._pool.cancel()
            context._failure.cancel()
            self._activations_used += context._pool.activations_used

    @staticmethod
    def _hook(prefix,name,callback):
        async def run(context):return await invoke_maybe_async(callback)
        return Phase(prefix+':'+name,run)

    async def run_step(self, step: int, time, phases: Iterable[Phase]):
        if self._state != 'ready':
            raise RuntimeError('runtime is not ready')
        if type(step) is not int or step != self.store.complete_step + 1:
            raise ValueError('step must follow the last complete step')
        before,after=self._step_hooks() if self._step_hooks is not None else ((),())
        phases=chain((self._hook('before',name,hook) for name,hook in (*before,*self._before)),
                     phases,(self._hook('after',name,hook) for name,hook in (*after,*self._after)))
        self._state = 'running'
        self._step=step
        self.last_timing={'step':step,'phase_s':0.,'finalization_s':0.,'complete_s':0.,'total_s':0.,
                          'failed':False,'complete_step':self.last_completed}
        self._active_count=self._finished_count=0
        self._phase_name=None
        self._failure_reason=None
        self._observe()
        self._activations_used = 0
        self._artifacts = []
        self._task = asyncio.current_task()
        started=time_module.perf_counter()
        phase_count=0
        try:
            for phase_index,phase in enumerate(phases):
                phase_started=time_module.perf_counter()
                try:await self._run_phase(time,phase,phase_index)
                finally:self.last_timing['phase_s']+=time_module.perf_counter()-phase_started
                phase_count+=1
            finalization_started=time_module.perf_counter()
            try:
                if self.results is not None:
                    self.results.write_step(step,time,phase_count=phase_count,activation_count=self._activations_used,
                        elapsed_s=time_module.perf_counter()-started,capacity=self.capacity,max_activations=self.max_activations)
                complete_started=time_module.perf_counter()
                try:publication = self.store.complete(step, artifacts=tuple(self._artifacts))
                finally:self.last_timing['complete_s']=time_module.perf_counter()-complete_started
            finally:self.last_timing['finalization_s']=time_module.perf_counter()-finalization_started
        except BaseException as error:
            self._failure_reason=type(error).__name__
            self._state = 'failed'
            self.last_completed = self.store.complete_step
            self.store.abort_step()
            self.last_timing.update(total_s=time_module.perf_counter()-started,failed=True,complete_step=self.last_completed)
            self._observe()
            raise
        else:
            self.last_completed = step
            self._state = 'ready'
            self._phase_name=None
            self.last_timing.update(total_s=time_module.perf_counter()-started,complete_step=self.last_completed)
            self._observe()
            return publication
        finally:
            self._task = None
            self._artifacts.clear()

    async def close(self):
        if self._task is asyncio.current_task():
            raise RuntimeError('runtime cannot close from its running step')
        if self._close_task is None:
            async def drain():
                task=self._task
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task,return_exceptions=True)
                self._state='closed'
                self._observe()
                self._cursors.clear()
            self._close_task=asyncio.create_task(drain())
        cancelled=False
        while True:
            try:
                await asyncio.shield(self._close_task)
                break
            except asyncio.CancelledError:
                if self._close_task.cancelled():raise
                cancelled=True
        if cancelled:raise asyncio.CancelledError


def runtime_plugin(actors=(), *, information, actions, store, name='runtime',
                   capacity=1, max_activations=DEFAULT_MAX_ACTIVATIONS,
                   actor_service=None,results=None,progress=None):
    """服务依赖以 (插件名, 服务名) 声明，主体可按需从服务加载。"""
    actors = actors if isinstance(actors, Mapping) else tuple(actors)
    dependencies=[information,actions,store]
    dependencies.extend(item for item in (actor_service,results,progress) if item is not None)

    def install(context):
        runtime = Runtime(context.require(*actor_service) if actor_service else actors,
                          information=context.require(*information),
                          actions=context.require(*actions), store=context.require(*store),
                          capacity=capacity, max_activations=max_activations,
                          results=context.require(*results) if results else None,
                          progress=context.require(*progress) if progress else None,
                          step_hooks=context.step_hooks)
        context.on_quiesce(runtime.close)
        context.provide('runtime', runtime)

    return Plugin(name, tuple(dict.fromkeys(item[0] for item in dependencies)), install)
