"""单运行共享的有界原生进程池；计算结果由调用方按业务顺序安装。"""
from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor
import multiprocessing


class ProcessCompute:
    """提交可序列化的紧凑批次，不传运行对象、数据库连接或可写共享状态。"""
    def __init__(self, *, max_workers, max_pending):
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError('max_workers must be a positive integer')
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError('max_pending must be a positive integer')
        self._pool = ProcessPoolExecutor(max_workers=max_workers,
                                         mp_context=multiprocessing.get_context('spawn'))
        self._slots = asyncio.Semaphore(max_pending)
        self._loop = None
        self._closed = False

    async def run(self, function, /, *args, **kwargs):
        """限制已提交且未完成的任务数；批次字节量和调用方驻留须另行控制。"""
        if self._closed:
            raise RuntimeError('compute service is closed')
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError('compute service belongs to another event loop')
        await self._slots.acquire()
        try:
            if self._closed:
                raise RuntimeError('compute service is closed')
            future = self._pool.submit(function, *args, **kwargs)
        except BaseException:
            self._slots.release()
            raise
        # await 被取消时，运行中的进程仍拥有输入；在原生 future 完成后再归还额度。
        future.add_done_callback(lambda _: loop.call_soon_threadsafe(self._slots.release))
        return await asyncio.wrap_future(future)

    def close(self):
        if not self._closed:
            self._closed = True
            # 标准同步退出确保进程完成后再释放依赖；失败收尾可能等待长计算。
            self._pool.shutdown(wait=True, cancel_futures=True)


def compute_plugin(*, max_workers, max_pending, name='compute'):
    """多个机制显式依赖同一服务，避免每主体或每机制分配进程池。"""
    from .plugins import Plugin
    def install(context):
        compute = ProcessCompute(max_workers=max_workers, max_pending=max_pending)
        context.on_close(compute.close)
        context.provide('compute', compute)
    return Plugin(name, install=install)
