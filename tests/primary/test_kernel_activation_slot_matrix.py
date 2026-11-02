"""激活槽验收矩阵 2/4/6：同端点不抢 worker、不同端点不饿死、规则不占总量、取消释放。

全部复用真实 ActivationPool、ActivationSlots 与实际租约接口。
"""
import asyncio

import pytest

from society0.activation_pool import ActivationPool
from society0.kernel.activation_slots import ActivationSlotPolicy, ActivationSlots


def slot_groups(slots, routes):
    return {route: slots.policy.capacity(route) for route in routes}


@pytest.mark.asyncio
@pytest.mark.parametrize('per_route', [2, 4, 6])
async def test_same_route_stops_at_configured_slots_and_keeps_waiters_out_of_workers(per_route):
    """同端点最多 per_route 个并发；排队任务不占 worker，另一端点可全部完成。"""
    slots = ActivationSlots(ActivationSlotPolicy(per_route=per_route), routes=('left', 'right'))
    # 工作池容量大于单端点槽位，才能验证另一端点不被同端点排队影响。
    pool = ActivationPool(capacity=per_route + 2, concurrency_source='matrix',
                          groups=slot_groups(slots, ('left', 'right')),
                          total_capacity=slots.policy.total)
    release = asyncio.Event()
    left_entered = asyncio.Event()
    progress = {'left_active': 0, 'left_peak': 0, 'right_done': 0}

    async def left(batch):
        progress['left_active'] += 1
        progress['left_peak'] = max(progress['left_peak'], progress['left_active'])
        if progress['left_active'] == per_route:
            left_entered.set()
        try:
            await release.wait()
        finally:
            progress['left_active'] -= 1

    async def right(batch):
        await asyncio.sleep(0)
        progress['right_done'] += 1

    async with asyncio.TaskGroup() as group:
        await pool.start(group)
        for index in range(per_route + 2):
            pool.submit(f'left-{index}', left, group='left')
        # 另一端点额度独立：所有右端点任务必须完成，不能被左端点排队拖住。
        for index in range(per_route + 1):
            pool.submit(f'right-{index}', right, group='right')
        await asyncio.wait_for(left_entered.wait(), 1)
        await _until(lambda: progress['right_done'] == per_route + 1, 1)
        assert progress['left_peak'] == per_route
        assert pool.active_groups['left']['active'] == per_route
        assert pool.waiting_by_group == {'left': 2}
        release.set()
        await asyncio.wait_for(pool.drain(), 5)
        await asyncio.wait_for(pool.close(), 5)
    assert progress['left_peak'] == per_route


async def _until(predicate, timeout):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError('condition not reached in time')
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize('per_route', [2, 4, 6])
async def test_rule_without_group_ignores_model_total(per_route):
    """无分组规则任务不受模型激活总量约束，也不能被模型组排队阻塞。"""
    slots = ActivationSlots(ActivationSlotPolicy(per_route=per_route, total=1), routes=('left', 'right'))
    pool = ActivationPool(capacity=2, concurrency_source='matrix',
                          groups=slot_groups(slots, ('left', 'right')),
                          total_capacity=slots.policy.total)
    release = asyncio.Event()
    entered = asyncio.Event()
    done = []

    async def model(batch):
        entered.set()
        await release.wait()

    async def rule(batch):
        done.append(batch.key)

    async with asyncio.TaskGroup() as group:
        await pool.start(group)
        pool.submit('m1', model, group='left')
        await asyncio.wait_for(entered.wait(), 1)
        # 总量已满：第二组模型任务必须排队。
        pool.submit('m2', model, group='right')
        await asyncio.sleep(.05)
        assert pool.waiting_by_group == {'right': 1}
        pool.submit('rule-1', rule)
        await _until(lambda: done == ['rule-1'], 1)
        assert pool.total_active == 1
        release.set()
        await asyncio.wait_for(pool.drain(), 5)
        await asyncio.wait_for(pool.close(), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize('per_route', [2, 4, 6])
async def test_cancelled_activation_returns_slot_and_total(per_route):
    """取消一次激活必须同时归还路由槽与可选总量，后续激活可继续入场。"""
    slots = ActivationSlots(ActivationSlotPolicy(per_route=per_route, total=per_route),
                            routes=('left',))
    held = []

    async def hold():
        async with slots.lease('left'):
            held.append(asyncio.get_running_loop().time())
            await asyncio.Event().wait()

    tasks = [asyncio.create_task(hold()) for _ in range(per_route)]
    await _until(lambda: len(held) == per_route, 1)
    waiter = asyncio.create_task(hold())
    await asyncio.sleep(0)
    assert slots.waiting['left'] == 1

    # 取消排队者：不占用任何槽位。
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(waiter, 5)
    assert slots.waiting['left'] == 0

    # 取消已持有者：路由槽与总量一起归还。
    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
    assert slots.active['left'] == 0
    async with slots.lease('left'):
        pass
    async with slots.lease('left'):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize('per_route', [2, 4, 6])
async def test_driver_does_not_release_a_second_slot_when_pool_already_admitted(tmp_path, per_route):
    """工作池入场已占槽位时，驱动不再自行申请同一个激活许可。"""
    from types import SimpleNamespace

    from society0.kernel.activation_slots import AdmissionLease
    from society0.kernel.interaction import Action, ActionResult, Actions, Information, InteractionScope, Moment
    from society0.kernel.llm import LLMDriver, LLMPolicy
    from society0.kernel.runtime import Actor, Session
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
    from tests.primary.test_kernel_llm import FakeProvider, reply

    slots = ActivationSlots(ActivationSlotPolicy(per_route=per_route), routes=('left',))
    pool = ActivationPool(capacity=per_route, concurrency_source='matrix', groups={'left': per_route})
    store = StageStore.create(tmp_path / 'run', THREAD_SCHEMA)
    try:
        threads = ThreadStore(store)
        provider = FakeProvider(threads, [reply(text='done')])
        actions = Actions(lambda *a: True)
        actions.register(Action('work', ('m', 'job'), 'work', {'type': 'object'},
                                lambda *a: ActionResult('completed')))
        driver = LLMDriver(provider, threads, input_builder=lambda session: [
            {'role': 'user', 'content': 'x'}], policy=LLMPolicy(), activation_slots=slots)
        moment = Moment(1, 'p')
        scope = InteractionScope('a', moment)
        cursors = {'model_route': .25,
                   'activation_lease': AdmissionLease('left', .25, per_route, pool)}
        session = Session(Actor('a', driver), scope, Information(lambda *a: True).bound(scope),
                          actions.bound(scope), cursors, None, (),
                          SimpleNamespace(prepare_artifact=store.prepare_artifact), step=1)
        assert (await asyncio.wait_for(driver.run(session), 5)).status == 'completed'
        # 驱动沿用池入场租约，没有再次占用路由槽。
        assert slots.active['left'] == 0
        assert 'activation_lease_stack' not in session.cursors
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('per_route', [2, 4, 6])
async def test_driver_acquires_exactly_one_slot_when_no_admission_exists(tmp_path, per_route):
    """没有工作池入场时，驱动按路由申请且只申请一次激活租约。"""
    from types import SimpleNamespace

    from society0.kernel.interaction import Action, ActionResult, Actions, Information, InteractionScope, Moment
    from society0.kernel.llm import LLMDriver, LLMPolicy
    from society0.kernel.runtime import Actor, Session
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
    from tests.primary.test_kernel_llm import FakeProvider, reply

    slots = ActivationSlots(ActivationSlotPolicy(per_route=per_route), routes=('left',))
    store = StageStore.create(tmp_path / 'run', THREAD_SCHEMA)
    try:
        threads = ThreadStore(store)
        provider = FakeProvider(threads, [reply(text='done')])
        actions = Actions(lambda *a: True)
        actions.register(Action('work', ('m', 'job'), 'work', {'type': 'object'},
                                lambda *a: ActionResult('completed')))
        # 真实提供方按 Thread 路由解析端点；这里只补端点解析，其余保持离线脚本行为。
        provider.endpoints_for = lambda route: SimpleNamespace(id='left')
        driver = LLMDriver(provider, threads, input_builder=lambda session: [
            {'role': 'user', 'content': 'x'}], policy=LLMPolicy(), activation_slots=slots)
        moment = Moment(1, 'p')
        scope = InteractionScope('a', moment)
        session = Session(Actor('a', driver), scope, Information(lambda *a: True).bound(scope),
                          actions.bound(scope), {}, None, (),
                          SimpleNamespace(prepare_artifact=store.prepare_artifact), step=1)
        assert (await asyncio.wait_for(driver.run(session), 5)).status == 'completed'
        # 激活结束后租约已释放，峰值只有一次。
        assert slots.active['left'] == 0
        assert slots.peak['left'] == 1
        assert slots.route_semaphore('left')._value == per_route
    finally:
        store.close()
