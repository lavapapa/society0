"""共享环境的顺序阶段与有界主体激活；不推进墙钟对应的模拟时间。"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

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
class Phase:
    name: str
    run: Callable
    prepare: Callable | None = None
    execution: str = 'serial'

    def __post_init__(self):
        if self.execution not in ('serial', 'independent'):
            raise ValueError('phase execution must be serial or independent')


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
        self._active = True
        self._ready = False
        self._failure = asyncio.get_running_loop().create_future()
        remaining = None if runtime.max_activations is None else runtime.max_activations - runtime._activations_used
        self._exhausted = remaining == 0
        self._pool = ActivationPool(
            world=None, capacity=1 if phase.execution == 'serial' else runtime.capacity,
            concurrency_source='kernel runtime', max_activations=1 if self._exhausted else remaining,
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
        actor = self._runtime._actors[actor_id]

        async def execute(batch):
            if not self._active or self._failure.done():
                raise RuntimeError('phase has failed')
            scope = InteractionScope(actor.id, self.moment)
            session = Session(
                actor, scope, self._runtime.information.bound(scope), self._runtime.actions.bound(scope),
                self._runtime._cursors.setdefault(actor.id, {}), self.prepared, batch.payloads, self,
            )
            try:
                result = await actor.driver.run(session)
                if not isinstance(result, DriverResult):
                    raise TypeError('driver must return DriverResult')
                if result.status == 'incomplete':
                    raise IncompleteActivation(result.reason or 'driver incomplete')
                return result
            except BaseException as error:
                if not self._failure.done():
                    self._failure.set_result(error)
                raise
            finally:
                scope.close()

        return self._pool.submit_agent(actor.id, actor.id, execute, payload=payload,
                                       dedupe_token=dedupe_token, handler_id=actor.id)

    async def drain(self):
        if not self._active:
            raise RuntimeError('phase is closed')
        return await self._pool.drain()


class Runtime:
    def __init__(self, actors: Iterable[Actor], *, information, actions, store,
                 capacity=1, max_activations=DEFAULT_MAX_ACTIVATIONS):
        self._actors = {}
        for actor in actors:
            if actor.id in self._actors:
                raise ValueError(f'duplicate actor: {actor.id}')
            self._actors[actor.id] = actor
        self.information = information
        self.actions = actions
        self.store = store
        self.capacity = capacity
        self.max_activations = max_activations
        self.last_completed = store.complete_step
        self._state = 'ready'
        self._task = None
        self._moment = None
        self._cursors = {}
        self._activations_used = 0
        self._artifacts = []

    async def _run_phase(self, time, phase):
        moment = Moment(time, phase.name)
        if moment != self._moment:
            self._cursors.clear()
            self._moment = moment
        context = PhaseContext(self, phase, moment)
        await context._pool.start()

        async def body():
            if phase.prepare is not None:
                context.prepared = await invoke_maybe_async(phase.prepare, context)
            context._ready = True
            await invoke_maybe_async(phase.run, context)
            await context._pool.close()

        task = asyncio.create_task(body())
        try:
            done, _ = await asyncio.wait((task, context._failure), return_when=asyncio.FIRST_COMPLETED)
            if context._failure in done:
                raise context._failure.result()
            await task
        finally:
            context._active = False
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await context._pool.cancel()
            context._failure.cancel()
            self._activations_used += context._pool.activations_used

    async def run_step(self, step: int, time, phases: Iterable[Phase]):
        if self._state != 'ready':
            raise RuntimeError('runtime is not ready')
        if type(step) is not int or step != self.store.complete_step + 1:
            raise ValueError('step must follow the last complete step')
        self._state = 'running'
        self._activations_used = 0
        self._artifacts = []
        self._task = asyncio.current_task()
        try:
            for phase in phases:
                await self._run_phase(time, phase)
            publication = self.store.complete(step, artifacts=tuple(self._artifacts))
        except BaseException:
            self._state = 'failed'
            self.last_completed = self.store.complete_step
            self.store.abort_step()
            raise
        else:
            self.last_completed = step
            self._state = 'ready'
            return publication
        finally:
            self._task = None
            self._artifacts.clear()

    async def close(self):
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self._state = 'closed'
        self._cursors.clear()


def runtime_plugin(actors, *, information, actions, store, name='runtime',
                   capacity=1, max_activations=DEFAULT_MAX_ACTIVATIONS):
    """三项服务各以 (插件名, 服务名) 指定；复用主机的依赖生命周期。"""
    actors = tuple(actors)

    def install(context):
        runtime = Runtime(actors, information=context.require(*information),
                          actions=context.require(*actions), store=context.require(*store),
                          capacity=capacity, max_activations=max_activations)
        context.on_close(runtime.close)
        context.provide('runtime', runtime)

    return Plugin(name, tuple(dict.fromkeys((information[0], actions[0], store[0]))), install)
