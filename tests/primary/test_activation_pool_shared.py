"""来自旧基线的规范激活池边界，移除已退役 World 构造参数。"""
import asyncio
import pytest
from society0.activation_pool import ActivationPool, ActivationLimitError


@pytest.mark.asyncio
async def test_activation_pool_limit_surfaces_unfinished_follow_up_without_running_it():
    pool = ActivationPool(
        capacity=1,
        concurrency_source="test",
        max_activations=1,
    )
    await pool.start()
    calls = []

    async def activate(batch):
        calls.append(batch.round)
        pool.submit("storm", activate, payload="follow-up")

    pool.submit("storm", activate, payload="initial")

    with pytest.raises(ActivationLimitError) as raised:
        await pool.drain()

    error = raised.value
    assert error.maximum == 1
    assert error.used == 1
    assert error.pending_keys == ("storm",)
    assert error.active_keys == ()
    assert calls == [1]
    assert [result.batch.round for result in pool.results] == [1]
    await pool.cancel()


@pytest.mark.asyncio
async def test_drain_waits_for_work_submitted_on_idle_boundary():
    pool = ActivationPool(
        capacity=1,
        concurrency_source="test",
    )
    await pool.start()
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    late_started = asyncio.Event()
    late_release = asyncio.Event()

    async def first():
        first_started.set()
        await first_release.wait()

    async def late():
        late_started.set()
        await late_release.wait()

    pool.submit("first", first)
    await asyncio.wait_for(first_started.wait(), timeout=1)

    async def submit_on_idle_boundary():
        await pool._queue._finished.wait()
        pool.submit("late", late)

    late_submitter = asyncio.create_task(submit_on_idle_boundary())
    await asyncio.sleep(0)
    drain_task = asyncio.create_task(pool.drain())
    await asyncio.sleep(0)

    first_release.set()
    await asyncio.wait_for(late_started.wait(), timeout=1)
    assert drain_task.done() is False

    late_release.set()
    await asyncio.wait_for(drain_task, timeout=1)
    await asyncio.wait_for(late_submitter, timeout=1)
    await pool.close()


@pytest.mark.asyncio
async def test_close_waits_for_work_submitted_on_idle_boundary():
    pool = ActivationPool(
        capacity=1,
        concurrency_source="test",
    )
    await pool.start()
    first_started = asyncio.Event()
    first_release = asyncio.Event()
    late_started = asyncio.Event()
    late_release = asyncio.Event()

    async def first():
        first_started.set()
        await first_release.wait()

    async def late():
        late_started.set()
        await late_release.wait()

    pool.submit("first", first)
    await asyncio.wait_for(first_started.wait(), timeout=1)

    async def submit_on_idle_boundary():
        await pool._queue._finished.wait()
        pool.submit("late", late)

    late_submitter = asyncio.create_task(submit_on_idle_boundary())
    await asyncio.sleep(0)
    close_task = asyncio.create_task(pool.close())
    await asyncio.sleep(0)

    first_release.set()
    await asyncio.wait_for(late_started.wait(), timeout=1)
    assert close_task.done() is False
    assert pool.closed is False

    late_release.set()
    await asyncio.wait_for(close_task, timeout=1)
    await asyncio.wait_for(late_submitter, timeout=1)
    assert pool.closed is True
    assert not pool._execution_tasks
