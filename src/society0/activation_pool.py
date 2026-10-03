"""Dynamic, bounded work pool for one code-step activation session."""

from __future__ import annotations

import asyncio
import inspect
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


@dataclass(slots=True)
class _ActivationState:
    serial_key: Hashable | None = None
    handler_id: Hashable | None = None
    pending: _PendingActivation | None = None
    follow_up: _PendingActivation | None = None
    running: bool = False
    round_count: int = 0


class ActivationPool:
    """A temporary bounded work pool bound to one step session."""

    def __init__(
        self,
        *,
        capacity: int,
        concurrency_source: str,
        max_activations: int | None = DEFAULT_MAX_ACTIVATIONS,
    ) -> None:
        try:
            parsed_capacity = int(capacity)
        except (TypeError, ValueError):
            raise ValueError("capacity must be a positive integer") from None
        if parsed_capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self.capacity = parsed_capacity
        self.concurrency_source = str(concurrency_source)
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
        self._activation_limit_event = asyncio.Event()
        self.closed = False
        self._started = False
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._states: dict[ActivationKey, _ActivationState] = {}
        self._seen_tokens: set[tuple[ActivationKey, Hashable]] = set()
        self._capacity_semaphore = asyncio.Semaphore(self.capacity)
        self._serial_locks: dict[Hashable, asyncio.Lock] = {}
        self._scheduler_task: asyncio.Task[None] | None = None
        self._execution_tasks: set[asyncio.Task[None]] = set()
        self._execution_errors: list[BaseException] = []
        self._results: list[ActivationResult] = []
        self._sentinel = object()
        self._submission_generation = 0

    async def start(self) -> "ActivationPool":
        if self.closed:
            raise RuntimeError("Activation pool is closed")
        if self._started:
            return self
        self._started = True
        self._scheduler_task = asyncio.create_task(
            self._scheduler(),
            name="society0-activation-pool-scheduler",
        )
        return self

    def submit(
        self,
        key: ActivationKey,
        task: ActivationCallable,
        *,
        payload: Any = None,
        dedupe_token: Hashable | None = None,
        serial_key: Hashable | None = None,
        handler_id: Hashable | None = None,
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
    ) -> ActivationSubmission:
        """Submit agent-related closure work in that agent's serial domain."""

        return self.submit(
            key,
            task,
            payload=payload,
            dedupe_token=dedupe_token,
            serial_key=self.agent_serial_key(agent_id),
            handler_id=handler_id,
        )


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
        self._queue.put_nowait(key)
        return self._accepted_submission(key, "queued")

    enqueue = submit

    @property
    def results(self) -> tuple[ActivationResult, ...]:
        return tuple(self._results)

    async def drain(self, *, raise_on_error: bool = True) -> tuple[ActivationResult, ...]:
        """Wait until all work submitted so far, including follow-up work, is done."""
        while True:
            generation = self._submission_generation
            if await self._wait_for_queue_or_activation_limit():
                raise self._build_activation_limit_error()
            # Queue.join() can be awakened by a transient zero before another
            # ready task submits work. Give those ready tasks one turn, then
            # require both the generation and every internal state to be idle.
            await asyncio.sleep(0)
            if self._activation_limit_reached:
                raise self._build_activation_limit_error()
            if (
                generation == self._submission_generation
                and self._is_idle()
            ):
                break
        if self._execution_errors:
            details = "; ".join(str(error) or repr(error) for error in self._execution_errors)
            raise RuntimeError(f"Activation pool scheduler failed: {details}")
        results = self.results
        failures = tuple(result for result in results if result.status == "error")
        if raise_on_error and failures:
            raise ActivationPoolError(failures)
        return results

    async def _wait_for_queue_or_activation_limit(self) -> bool:
        """Wait for idle work or stop promptly once the finite budget is hit."""

        if self._activation_limit_reached:
            return True
        queue_task = asyncio.create_task(self._queue.join())
        limit_task = asyncio.create_task(self._activation_limit_event.wait())
        try:
            done, _ = await asyncio.wait(
                {queue_task, limit_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if limit_task in done and self._activation_limit_reached:
                return True
            return False
        finally:
            for task in (queue_task, limit_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(queue_task, limit_task, return_exceptions=True)

    @property
    def activations_used(self) -> int:
        """Number of activation rounds that have started in this session."""

        return self._activation_count

    def _build_activation_limit_error(self) -> ActivationLimitError:
        maximum = self.max_activations
        if maximum is None:
            raise RuntimeError("activation limit is not configured")
        pending_keys = tuple(
            key
            for key, state in self._states.items()
            if state.pending is not None or state.follow_up is not None
        )
        active_keys = tuple(
            key for key, state in self._states.items() if state.running
        )
        return ActivationLimitError(
            maximum=maximum,
            used=self._activation_count,
            pending_keys=pending_keys,
            active_keys=active_keys,
        )

    def _accepted_submission(
        self,
        key: ActivationKey,
        disposition: str,
    ) -> ActivationSubmission:
        self._submission_generation += 1
        return ActivationSubmission(key, disposition, True)

    def _is_idle(self) -> bool:
        if not self._queue.empty() or self._execution_tasks:
            return False
        return not any(
            state.pending is not None
            or state.follow_up is not None
            or state.running
            for state in self._states.values()
        )


    async def close(self, *, raise_on_error: bool = True) -> None:
        if self.closed:
            return
        try:
            await self.drain(raise_on_error=raise_on_error)
        except asyncio.CancelledError:
            await self.cancel()
            raise
        except ActivationLimitError:
            await self.cancel()
            raise
        except BaseException:
            await self._stop_runtime()
            raise
        try:
            await self._stop_runtime()
        except asyncio.CancelledError:
            await self.cancel()
            raise

    async def _stop_runtime(self) -> None:
        self.closed = True
        scheduler = self._scheduler_task
        if scheduler is not None and not scheduler.done():
            self._queue.put_nowait(self._sentinel)
            await asyncio.gather(scheduler, return_exceptions=True)
        self._scheduler_task = None

    async def cancel(self) -> None:
        """Cancel running work and discard work that has not started."""
        scheduler = self._scheduler_task
        if self.closed and scheduler is None and not self._execution_tasks:
            return
        self.closed = True
        if scheduler is not None:
            scheduler.cancel()
            await asyncio.gather(scheduler, return_exceptions=True)
            self._scheduler_task = None
        execution_tasks = tuple(self._execution_tasks)
        for execution_task in execution_tasks:
            execution_task.cancel()
        if execution_tasks:
            await asyncio.gather(*execution_tasks, return_exceptions=True)
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._queue.task_done()
        self._states.clear()
        self._serial_locks.clear()

    async def _scheduler(self) -> None:
        while True:
            key = await self._queue.get()
            if key is self._sentinel:
                self._queue.task_done()
                return
            execution_task = asyncio.create_task(
                self._execute_key(key),
                name=f"society0-activation-{key!r}",
            )
            self._execution_tasks.add(execution_task)
            execution_task.add_done_callback(self._execution_done)

    def _execution_done(self, task: asyncio.Task[None]) -> None:
        self._execution_tasks.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self._execution_errors.append(error)
        self._queue.task_done()

    async def _execute_key(self, key: ActivationKey) -> None:
        state = self._states.get(key)
        if state is None or state.pending is None:
            return
        serial_key = state.pending.serial_key
        serial_lock = (
            self._serial_locks.setdefault(serial_key, asyncio.Lock())
            if serial_key is not None
            else None
        )
        serial_acquired = False
        if serial_lock is not None:
            await serial_lock.acquire()
            serial_acquired = True
        try:
            async with self._capacity_semaphore:
                state = self._states.get(key)
                if state is None:
                    return
                pending = state.pending
                if pending is None:
                    return
                if (
                    self.max_activations is not None
                    and self._activation_count >= self.max_activations
                ):
                    self._reach_activation_limit()
                    return
                state.pending = None
                state.running = True
                state.round_count += 1
                self._activation_count += 1
                batch = ActivationBatch(
                    key=key,
                    round=state.round_count,
                    serial_key=pending.serial_key,
                    signals=tuple(pending.signals),
                )
                try:
                    value = await self._invoke_task(pending.task, batch)
                except (asyncio.CancelledError, Exception) as exc:
                    self._results.append(
                        ActivationResult(
                            key=key,
                            round=batch.round,
                            status="error",
                            batch=batch,
                            error=exc,
                        )
                    )
                else:
                    self._results.append(
                        ActivationResult(
                            key=key,
                            round=batch.round,
                            status="success",
                            batch=batch,
                            value=value,
                        )
                    )
                finally:
                    state.running = False
                    if state.follow_up is not None:
                        state.pending = state.follow_up
                        state.follow_up = None
                        if self._activation_limit_reached or (
                            self.max_activations is not None
                            and self._activation_count >= self.max_activations
                        ):
                            self._reach_activation_limit()
                        else:
                            self._queue.put_nowait(key)
        finally:
            if serial_lock is not None and serial_acquired:
                serial_lock.release()

    def _reach_activation_limit(self) -> None:
        self._activation_limit_reached = True
        self._activation_limit_event.set()

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
