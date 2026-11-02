"""激活槽与工作池的占用观测：等待不含活跃，池入场是唯一计数来源。
"""
import asyncio

import pytest

from society0.activation_pool import ActivationPool
from society0.kernel.activation_slots import ActivationSlotPolicy, ActivationSlots


@pytest.mark.asyncio
async def test_waiting_stops_when_lease_becomes_active():
    slots = ActivationSlots(ActivationSlotPolicy(per_route=1), routes=('left',))
    release = asyncio.Event()
    entered = asyncio.Event()

    async def holder():
        async with slots.lease('left'):
            entered.set()
            await release.wait()

    task = asyncio.create_task(holder())
    await asyncio.wait_for(entered.wait(), 1)
    # 已获得许可并进入活跃：等待计数为 0，活跃计数为 1。
    assert slots.waiting['left'] == 0 and slots.active['left'] == 1
    waiter = asyncio.create_task(holder())
    await asyncio.sleep(0)
    assert slots.waiting['left'] == 1
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, 5)
    assert slots.waiting['left'] == 0
    release.set()
    await asyncio.wait_for(task, 5)


@pytest.mark.asyncio
async def test_snapshot_reports_the_pool_as_the_single_occupancy_source():
    slots = ActivationSlots(ActivationSlotPolicy(per_route=1), routes=('left', 'right'))
    pool = ActivationPool(capacity=2, concurrency_source='test', groups={'left': 1, 'right': 1})
    release = asyncio.Event()
    entered = asyncio.Event()
    async with asyncio.TaskGroup() as group:
        await pool.start(group)
        async def work(batch):
            entered.set()
            await release.wait()
        pool.submit('a', work, group='left', group_capacity=1)
        await asyncio.wait_for(entered.wait(), 1)
        snapshot = slots.snapshot(pool)
        assert snapshot['source'] == 'activation_pool'
        assert snapshot['active'] == {'left': 1}
        assert snapshot['total_active'] == 1
        release.set()
        await asyncio.wait_for(pool.drain(), 5)
        await asyncio.wait_for(pool.close(), 5)
