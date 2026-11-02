"""工作池通用入场分组：同组排队不占工作者，另一组继续，取消后额度归还。
"""
import asyncio

import pytest

from society0.activation_pool import ActivationPool


async def run_pool(pool, submissions):
    async with asyncio.TaskGroup() as group:
        await pool.start(group)
        for key, started, release in submissions:
            async def task(batch, started=started, release=release):
                started.set()
                await release.wait()
                return batch.key
            pool.submit(key, task, group='left', group_capacity=1)
        await asyncio.wait_for(pool.drain(), 5)
        await asyncio.wait_for(pool.close(), 5)


@pytest.mark.asyncio
async def test_same_group_waits_and_other_group_runs():
    pool = ActivationPool(capacity=2, concurrency_source='test', groups={'left': 1, 'right': 1})
    left_started, right_started = asyncio.Event(), asyncio.Event()
    release = asyncio.Event()
    async with asyncio.TaskGroup() as group:
        await pool.start(group)
        async def left(batch):
            left_started.set()
            await release.wait()
        async def right(batch):
            right_started.set()
        pool.submit('a', left, group='left', group_capacity=1)
        pool.submit('b', right, group='right', group_capacity=1)
        await asyncio.wait_for(left_started.wait(), 1)
        await asyncio.wait_for(right_started.wait(), 1)
        assert pool.active_groups['left']['active'] == 1
        queued = pool.submit('c', left, group='left', group_capacity=1)
        assert queued.accepted
        await asyncio.sleep(0)
        assert pool.waiting_by_group == {'left': 1}
        release.set()
        await asyncio.wait_for(pool.drain(), 5)
        await asyncio.wait_for(pool.close(), 5)


@pytest.mark.asyncio
async def test_group_capacity_comes_from_pool_when_omitted():
    pool = ActivationPool(capacity=2, concurrency_source='test', groups={'only': 1})
    release = asyncio.Event()
    entered = asyncio.Event()
    async with asyncio.TaskGroup() as group:
        await pool.start(group)
        async def work(batch):
            entered.set()
            await release.wait()
        pool.submit('x', work, group='only')
        await asyncio.wait_for(entered.wait(), 1)
        assert pool.active_groups['only'] == {'capacity': 1, 'active': 1}
        release.set()
        await asyncio.wait_for(pool.drain(), 5)
        await asyncio.wait_for(pool.close(), 5)


@pytest.mark.asyncio
async def test_missing_group_capacity_is_a_configuration_error():
    pool = ActivationPool(capacity=1, concurrency_source='test')
    with pytest.raises(ValueError):
        pool.submit('x', lambda batch: None, group='unconfigured')


@pytest.mark.asyncio
async def test_total_capacity_limits_grouped_activations_only():
    """跨端点总量只约束带分组模型任务；无分组的规则任务不受影响。"""
    pool = ActivationPool(capacity=2, concurrency_source='test', groups={'left': 5, 'right': 5}, total_capacity=1)
    release = asyncio.Event()
    entered = asyncio.Event()
    async with asyncio.TaskGroup() as group:
        await pool.start(group)
        async def grouped(batch):
            entered.set()
            await release.wait()
        async def plain(batch):
            return 'rule'
        pool.submit('g1', grouped, group='left')
        await asyncio.wait_for(entered.wait(), 1)
        assert pool.total_active == 1
        queued = pool.submit('g2', grouped, group='right')
        assert queued.accepted
        # 无分组任务不受总量限制，应立即执行。
        seen = []
        async def plain(batch):
            seen.append(batch.key)
        pool.submit('rule', plain)
        await asyncio.sleep(.05)
        assert seen == ['rule']
        assert pool.waiting_by_group == {'right': 1}
        release.set()
        await asyncio.wait_for(pool.drain(), 5)
        await asyncio.wait_for(pool.close(), 5)
