"""Dynamic, bounded work pool for one code-step activation session."""

from __future__ import annotations

import asyncio
import inspect
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Hashable

from .async_utils import invoke_maybe_async


ActivationKey = Hashable
ActivationCallable = Callable[..., Awaitable[Any]]
DEFAULT_MAX_ACTIVATIONS = 4096


@dataclass(slots=True)
class ActivationResult:
    """Observable outcome of one executed activation round."""

    key: ActivationKey
    round: int
    status: str
    batch: ActivationBatch
    value: Any = None
    error: BaseException | None = None


class ActivationPoolError(RuntimeError):
    """Raised after drain when one or more activation rounds failed."""

    def __init__(self, failures: tuple[ActivationResult, ...]) -> None:
        self.failures = failures
        details = "; ".join(
            f"{result.key!r}: {result.error}" for result in failures
        )
        super().__init__(f"Activation pool completed with {len(failures)} error(s): {details}")


class ActivationLimitError(ActivationPoolError):
    """Raised when a session has unfinished work at its activation limit."""

    def __init__(
        self,
        *,
        maximum: int,
        used: int,
        pending_keys: tuple[ActivationKey, ...],
        active_keys: tuple[ActivationKey, ...],
    ) -> None:
        self.maximum = int(maximum)
        self.max_activations = self.maximum
        self.used = int(used)
        self.pending_keys = tuple(pending_keys)
        self.active_keys = tuple(active_keys)
        self.unfinished_keys = self.pending_keys + self.active_keys
        self.reason = "max_activations"
        super().__init__(())
        self.args = (
            "激活总数上限已达到："
            f"used={self.used}, maximum={self.maximum}, "
            f"pending_keys={self.pending_keys!r}, "
            f"active_keys={self.active_keys!r}",
        )


@dataclass(frozen=True, slots=True)
class ActivationSignal:
    """One caller contribution merged into an activation round."""

    payload: Any = None
    dedupe_token: Hashable | None = None
    instruction: str | None = None


@dataclass(frozen=True, slots=True)
class ActivationBatch:
    """The contributions visible to one execution of a keyed task."""

    key: ActivationKey
    round: int
    serial_key: Hashable | None
    signals: tuple[ActivationSignal, ...]

    @property
    def payloads(self) -> tuple[Any, ...]:
        return tuple(signal.payload for signal in self.signals)

    @property
    def dedupe_tokens(self) -> tuple[Hashable, ...]:
        return tuple(
            signal.dedupe_token
            for signal in self.signals
            if signal.dedupe_token is not None
        )


@dataclass(frozen=True, slots=True)
class ActivationSubmission:
    """Immediate acknowledgement returned by :meth:`ActivationPool.submit`."""

    key: ActivationKey
    disposition: str
    accepted: bool


@dataclass(slots=True)
class _PendingActivation:
    task: ActivationCallable
    signals: list[ActivationSignal]
    serial_key: Hashable | None = None


@dataclass(frozen=True, slots=True)
class ActivationGroup:
    """一类可并发资源的入场额度；同组容量满时排队不占工作者。"""
    key: Hashable
    capacity: int

    def __post_init__(self):
        hash(self.key)
        if type(self.capacity) is not int or self.capacity < 1:
            raise ValueError('activation group capacity must be a positive integer')


@dataclass(slots=True)
class _ActivationState:
    serial_key: Hashable | None = None
    handler_id: Hashable | None = None
    group: ActivationGroup | None = None
    pending: _PendingActivation | None = None
    follow_up: _PendingActivation | None = None
    running: bool = False
    round_count: int = 0


def _group(key, capacity, configured):
    """显式组容量优先；未给容量时取池配置，未声明组则不受组限制。"""
    if key is None:
        return None
    if capacity is None:
        capacity = configured.get(key)
    if capacity is None:
        raise ValueError('activation group requires an explicit capacity')
    return ActivationGroup(key, int(capacity))


class ActivationPool:
    """A temporary bounded work pool bound to one step session."""

    def __init__(
        self,
        *,
        capacity: int,
        concurrency_source: str,
        max_activations: int | None = DEFAULT_MAX_ACTIVATIONS,
        groups: "dict[Hashable, int] | None" = None,
        total_capacity: int | None = None,
    ) -> None:
        try:
            parsed_capacity = int(capacity)
        except (TypeError, ValueError):
            raise ValueError("capacity must be a positive integer") from None
        if parsed_capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self.capacity = parsed_capacity
        self.concurrency_source = str(concurrency_source)
        self.groups = {key: self._positive(value, 'group capacity') for key, value in (groups or {}).items()}
        self.total_capacity = None if total_capacity is None else self._positive(total_capacity, 'total capacity')
        if max_activations is None:
            parsed_max_activations = None
        else:
            try:
                parsed_max_activations = int(max_activations)
            except (TypeError, ValueError):
                raise ValueError("max_activations must be a positive integer or None") from None
            if parsed_max_activations <= 0:
                raise ValueError("max_activations must be a positive integer or None")
        self.max_activations = parsed_max_activations
        self._activation_count = 0
        self._activation_limit_reached = False
        self.closed = False
        self._started = False
        self._queue = deque()
        self._states: dict[ActivationKey, _ActivationState] = {}
        self._seen_tokens: set[tuple[ActivationKey, Hashable]] = set()
        self._serial_active = set()
        self._group_active: dict[Hashable, int] = {}
        self._routes: dict[ActivationKey, float] = {}
        self._total_active = 0
        self._workers = []
        self._failure = None
        self._changed = asyncio.Event()
        self._results: list[ActivationResult] = []
        self._submission_generation = 0

    async def start(self, group: asyncio.TaskGroup) -> "ActivationPool":
        """工作者归调用方的任务组拥有；邮箱不另建后台调度器。"""
        if self.closed or self._started:
            raise RuntimeError("activation pool cannot be started again")
        self._started = True
        self._workers = [group.create_task(self._worker(), name=f'activation-worker-{i}')
                         for i in range(self.capacity)]
        return self

    @staticmethod
    def _positive(value, label):
        if type(value) is not int or value < 1:
            raise ValueError(f'{label} must be a positive integer')
        return value

    def submit(
        self,
        key: ActivationKey,
        task: ActivationCallable,
        *,
        payload: Any = None,
        dedupe_token: Hashable | None = None,
        serial_key: Hashable | None = None,
        handler_id: Hashable | None = None,
        group: Hashable | None = None,
        group_capacity: int | None = None,
    ) -> ActivationSubmission:
        """Submit a keyed closure and merge duplicate requests into one round.

        ``key`` defines the unit that must not run concurrently with itself.
        Repeated submissions while queued are merged into the queued round.
        Submissions while running are merged into exactly one follow-up round.
        Distinct keys with the same non-null ``serial_key`` remain separate but
        execute one at a time; waiting for that serial domain uses no capacity.
        """
        signal = ActivationSignal(payload=payload, dedupe_token=dedupe_token)
        return self._submit_signal(
            key,
            task,
            signal,
            serial_key=serial_key,
            handler_id=task if handler_id is None else handler_id,
            group=_group(group, group_capacity, self.groups),
        )

    def submit_agent(
        self,
        agent_id: str,
        key: ActivationKey,
        task: ActivationCallable,
        *,
        payload: Any = None,
        dedupe_token: Hashable | None = None,
        handler_id: Hashable | None = None,
        group: Hashable | None = None,
        group_capacity: int | None = None,
        route: float | None = None,
    ) -> ActivationSubmission:
        """Submit agent-related closure work in that agent's serial domain.

        ``route`` 是本次入场所选的模型抽样点；排队期间随任务保留，供驱动显式打开 Thread。
        """
        submission = self.submit(
            key,
            task,
            payload=payload,
            dedupe_token=dedupe_token,
            serial_key=self.agent_serial_key(agent_id),
            handler_id=handler_id,
            group=group,
            group_capacity=group_capacity,
        )
        if route is not None:
            self._routes[submission.key] = route
        return submission


    @staticmethod
    def agent_serial_key(agent_id: str) -> tuple[str, str]:
        """Return the serial domain shared by all work for one agent."""
        return ("agent", str(agent_id))

    def _submit_signal(
        self,
        key: ActivationKey,
        task: ActivationCallable,
        signal: ActivationSignal,
        *,
        serial_key: Hashable | None,
        handler_id: Hashable,
        group: ActivationGroup | None = None,
    ) -> ActivationSubmission:
        if self.closed:
            raise RuntimeError("Activation pool is closed")
        if not self._started:
            raise RuntimeError("Activation pool has not started")
        if not callable(task):
            raise TypeError("task must be callable")
        hash(key)
        if serial_key is not None:
            hash(serial_key)
        hash(handler_id)
        state = self._states.get(key)
        if state is not None and state.serial_key != serial_key:
            raise ValueError("serial_key must stay the same for one activation key")
        if state is not None and state.handler_id != handler_id:
            raise ValueError("handler_id must stay the same for one activation key")
        if state is not None and state.group != group:
            raise ValueError("group must stay the same for one activation key")
        dedupe_token = signal.dedupe_token
        if dedupe_token is not None:
            hash(dedupe_token)
            token_key = (key, dedupe_token)
            if token_key in self._seen_tokens:
                return ActivationSubmission(key, "duplicate_token", False)
            self._seen_tokens.add(token_key)

        if state is None:
            state = _ActivationState(
                serial_key=serial_key,
                handler_id=handler_id,
                group=group,
            )
            self._states[key] = state
        if state.running:
            if state.follow_up is None:
                state.follow_up = _PendingActivation(
                    task=task,
                    signals=[signal],
                    serial_key=serial_key,
                )
                return self._accepted_submission(key, "follow_up")
            state.follow_up.signals.append(signal)
            return self._accepted_submission(key, "merged_follow_up")

        if state.pending is not None:
            state.pending.signals.append(signal)
            return self._accepted_submission(key, "merged")

        state.pending = _PendingActivation(
            task=task,
            signals=[signal],
            serial_key=serial_key,
        )
        self._queue.append(key)
        return self._accepted_submission(key, "queued")

    enqueue = submit

    def route_for(self, key):
        """本次入场所选的模型抽样点；未配置分组时为 None。"""
        return self._routes.get(key)

    def group_of(self, key):
        """该任务入场时所属的资源分组；未分组时为 None。"""
        state = self._states.get(key)
        return None if state is None or state.group is None else state.group.key

    @property
    def total_active(self) -> int:
        """当前带资源分组且正在执行的激活数。"""
        return self._total_active

    @property
    def active_groups(self) -> dict:
        """当前占用中的组容量与并发数，用于观测端点阻塞。"""
        return {key: dict(capacity=self.groups.get(key), active=amount)
                for key, amount in self._group_active.items()}

    @property
    def waiting_by_group(self) -> dict:
        """排队中的组分布；同组阻塞的任务不占工作者，另一组可以继续入场。"""
        counts = {}
        for key in self._queue:
            state = self._states[key]
            if state.group is not None:
                counts[state.group.key] = counts.get(state.group.key, 0) + 1
        return counts

    @property
    def results(self) -> tuple[ActivationResult, ...]:
        return tuple(self._results)

    async def drain(self, *, raise_on_error: bool = True) -> tuple[ActivationResult, ...]:
        while True:
            self._changed.clear()
            if self._failure is not None:
                raise ActivationPoolError(tuple(item for item in self._results if item.status == 'error'))
            if self.closed and not self._is_idle():
                raise RuntimeError('activation pool was cancelled')
            if self._activation_limit_reached:
                raise self._build_activation_limit_error()
            generation = self._submission_generation
            if self._is_idle():
                await asyncio.sleep(0)
                if generation == self._submission_generation and self._is_idle():
                    break
                continue
            await self._changed.wait()
        failures = tuple(result for result in self._results if result.status == 'error')
        if failures and raise_on_error:
            raise ActivationPoolError(failures)
        return self.results

    @property
    def activations_used(self) -> int:
        return self._activation_count

    def _build_activation_limit_error(self):
        return ActivationLimitError(maximum=self.max_activations, used=self._activation_count,
            pending_keys=tuple(key for key, state in self._states.items()
                               if state.pending is not None or state.follow_up is not None),
            active_keys=tuple(key for key, state in self._states.items() if state.running))

    def _accepted_submission(self, key, disposition):
        self._submission_generation += 1
        self._changed.set()
        return ActivationSubmission(key, disposition, True)

    def _is_idle(self):
        return not self._queue and not any(state.running or state.pending or state.follow_up
                                          for state in self._states.values())

    async def close(self, *, raise_on_error=True):
        await self.drain(raise_on_error=raise_on_error)
        self.closed = True
        self._changed.set()

    def cancel(self):
        """发出一次取消；等待与资源收束由外层 TaskGroup 负责。"""
        if self.closed:
            return
        self.closed = True
        self._queue.clear()
        for task in self._workers:
            if task is not asyncio.current_task() and not task.cancelling():
                task.cancel()
        self._changed.set()

    def _take(self):
        # 被串行域占用的主体留在邮箱，不占用一个等待中的 worker。
        for _ in range(len(self._queue)):
            key = self._queue.popleft()
            state = self._states[key]
            if state.serial_key is not None and state.serial_key in self._serial_active:
                self._queue.append(key)
                continue
            if state.group is not None and self._group_active.get(state.group.key, 0) >= state.group.capacity:
                self._queue.append(key)
                continue
            # 总量只约束带资源分组的模型激活；无分组规则任务不受其限制。
            if (state.group is not None and self.total_capacity is not None
                    and self._total_active >= self.total_capacity):
                self._queue.append(key)
                continue
            if self.max_activations is not None and self._activation_count >= self.max_activations:
                self._queue.appendleft(key)
                self._activation_limit_reached = True
                self._changed.set()
                return None
            pending = state.pending
            state.pending = None
            state.running = True
            state.round_count += 1
            self._activation_count += 1
            if state.serial_key is not None:
                self._serial_active.add(state.serial_key)
            if state.group is not None:
                self._group_active[state.group.key] = self._group_active.get(state.group.key, 0) + 1
                self._total_active += 1
            return key, state, pending
        return None

    async def _worker(self):
        while not self.closed:
            self._changed.clear()
            selected = self._take()
            if selected is None:
                if self._activation_limit_reached:
                    return
                await self._changed.wait()
                continue
            key, state, pending = selected
            batch = ActivationBatch(key, state.round_count, pending.serial_key, tuple(pending.signals))
            try:
                value = await self._invoke_task(pending.task, batch)
            except BaseException as error:
                self._results.append(ActivationResult(key, batch.round, 'error', batch, error=error))
                if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    if not self.closed:
                        self._failure = error
                        self.cancel()
                    raise
            else:
                self._results.append(ActivationResult(key, batch.round, 'success', batch, value=value))
            finally:
                state.running = False
                self._serial_active.discard(state.serial_key)
                if state.group is not None:
                    remaining = self._group_active.get(state.group.key, 0) - 1
                    if remaining > 0:
                        self._group_active[state.group.key] = remaining
                    else:
                        self._group_active.pop(state.group.key, None)
                    self._total_active = max(0, self._total_active - 1)
                if state.follow_up is not None and not self.closed:
                    state.pending, state.follow_up = state.follow_up, None
                    self._queue.append(key)
                self._changed.set()

    async def _invoke_task(self, task: ActivationCallable, batch: ActivationBatch) -> Any:
        try:
            signature = inspect.signature(task)
        except (TypeError, ValueError):
            return await invoke_maybe_async(task, batch)
        accepts_batch = any(
            parameter.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.VAR_POSITIONAL,
            )
            for parameter in signature.parameters.values()
        )
        if accepts_batch:
            return await invoke_maybe_async(task, batch)
        return await invoke_maybe_async(task)
