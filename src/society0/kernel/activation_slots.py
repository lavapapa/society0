"""激活租约：按已持久化模型路由为每次完整激活保留端点容量。"""
from __future__ import annotations

import asyncio
import json
import random
from collections import Counter
from dataclasses import asdict, is_dataclass
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActivationSlotPolicy:
    """配置合同：每路由槽位数与可选总量；与物理请求许可分别配置。"""
    per_route: int = 4
    route_capacities: dict = field(default_factory=dict)
    total: int | None = None

    def __post_init__(self):
        if type(self.per_route) is not int or self.per_route < 1:
            raise ValueError('每路由激活槽需要正整数')
        for route, capacity in self.route_capacities.items():
            if not isinstance(route, str) or not route:
                raise ValueError('激活槽路由需要非空标识')
            if type(capacity) is not int or capacity < 1:
                raise ValueError('路由激活槽需要正整数')
        if self.total is not None and (type(self.total) is not int or self.total < 1):
            raise ValueError('激活槽总量需要正整数或空值')

    def capacity(self, route):
        return self.route_capacities.get(route, self.per_route)




@asynccontextmanager
async def _permit(semaphore):
    """单个许可作上下文管理器；归还与获取属于同一取消安全边界。"""
    await semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()


class AdmissionLease:
    """工作池入场已占用的端点槽位：载体交给驱动，计数仍由工作池唯一持有。"""

    def __init__(self, workload, route, capacity, pool):
        self.workload, self.route, self.capacity, self.pool = workload, route, capacity, pool
        self.source = 'pool_admission'

    def close(self):
        """槽位在激活结束时由工作池归还；这里不重复释放，只递出句柄。"""
        return None


class ActivationLease:
    """一次激活的租约句柄；关系统一在 AsyncExitStack 中释放。"""

    def __init__(self, slots, route):
        self.slots, self.route = slots, route
        self.active = True

    async def __aenter__(self):
        self.slots._enter(self.route)
        return self

    async def __aexit__(self, *exc):
        self.close()

    def close(self):
        if self.active:
            self.active = False
            self.slots._leave(self.route)


class ActivationSlots:
    """激活容量与物理许可相互独立：这里只保留端点之上的激活槽。"""

    def __init__(self, policy=None, *, routes=(), endpoint_resolver=None):
        self.policy = policy or ActivationSlotPolicy()
        self.routes = tuple(dict.fromkeys(routes))
        self.endpoint_resolver = endpoint_resolver
        self._semaphores = {route: asyncio.Semaphore(self.policy.capacity(route)) for route in self.routes}
        self._total = asyncio.Semaphore(self.policy.total) if self.policy.total is not None else None
        self.active = Counter()
        self.waiting = Counter()
        self.peak = Counter()

    def route_semaphore(self, route):
        semaphore = self._semaphores.get(route)
        if semaphore is None:
            semaphore = self._semaphores[route] = asyncio.Semaphore(self.policy.capacity(route))
        return semaphore

    def _enter(self, route):
        self.active[route] += 1
        self.peak[route] = max(self.peak.get(route, 0), self.active[route])

    def _leave(self, route):
        if self.active[route] > 0:
            self.active[route] -= 1

    @asynccontextmanager
    async def lease(self, route):
        """租约在激活存续期间保留；同路由超出槽位时等待且不持任何物理许可。

        路由许可与可选总量许可在同一取消安全区获取与归还；任一步被取消都不会
        留下已占用而未登记的路由槽位。
        """
        if not isinstance(route, str) or not route:
            raise ValueError('激活租约需要路由标识')
        self.waiting[route] += 1
        handle = ActivationLease(self, route)
        entered = False
        try:
            async with AsyncExitStack() as stack:
                await stack.enter_async_context(_permit(self.route_semaphore(route)))
                if self._total is not None:
                    await stack.enter_async_context(_permit(self._total))
                # 许可全部到手才从等待转入活跃；等待计数不含已经持有激活的时段。
                self.waiting[route] -= 1
                entered = True
                await stack.enter_async_context(handle)
                yield handle
        finally:
            if not entered:
                self.waiting[route] -= 1

    def snapshot(self, pool=None):
        """观测唯一的激活占用来源：有工作池分组时以池计数为准，避免报告假空闲。"""
        if pool is not None:
            return {'source': 'activation_pool',
                    'policy': {'per_route': self.policy.per_route,
                               'route_capacities': dict(self.policy.route_capacities),
                               'total': self.policy.total},
                    'active': {key: value['active'] for key, value in pool.active_groups.items()},
                    'waiting': dict(pool.waiting_by_group),
                    'total_active': pool.total_active,
                    'capacity': dict(pool.groups)}
        return {'source': 'activation_slots',
                'policy': {'per_route': self.policy.per_route, 'route_capacities': dict(self.policy.route_capacities),
                           'total': self.policy.total},
                'active': dict(self.active), 'waiting': dict(self.waiting), 'peak': dict(self.peak)}


class ActivationWorkload:
    """工作池入场分组：按本次 Moment 的决策 Thread 路由归组，无 Thread 时先抽样。

    ``__call__(actor_id, moment)`` 返回 ``(workload, capacity, route)``；route 是
    同一 Moment、同一主体稳定的模型抽样点，运行期写入 session cursors 交给驱动。
    """

    def __init__(self, threads, slots, *, kind='decision'):
        if not isinstance(slots, ActivationSlots):
            raise TypeError('activation workload requires ActivationSlots')
        self.threads, self.slots, self.kind = threads, slots, kind
        self._sampled = {}
        self._moment_key = None

    def __call__(self, actor_id, moment):
        thread_id = self.threads.find(actor_id, moment, kind=self.kind)
        moment_key = json_key(moment)
        if moment_key != self._moment_key:
            # 抽样只服务当前 Moment；进入下一 Moment 即释放，长跑不累计。
            self._sampled.clear()
            self._moment_key = moment_key
        key = (actor_id, moment_key)
        if thread_id is not None:
            route = self.threads.model_route(thread_id)
            self._sampled[key] = route
        else:
            # 本 Moment 尚无 Thread：抽样值记在游标侧，驱动用同一值显式打开新 Thread。
            route = self._sampled.get(key)
            if route is None:
                route = random.random()
                self._sampled[key] = route
        endpoint = self._endpoint(route)
        return (endpoint, self.slots.policy.capacity(endpoint), route)

    def _endpoint(self, route):
        resolver = self.slots.endpoint_resolver
        if resolver is None:
            raise ValueError('激活工作负载需要端点解析器')
        return resolver(route)


def json_key(moment):
    """Moment 是冻结值；分组键需要稳定且不含对象身份。"""
    if is_dataclass(moment):
        moment = asdict(moment)
    return json.dumps(moment, sort_keys=True, allow_nan=False, default=str)
