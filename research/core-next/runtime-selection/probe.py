"""有限、离线、独立 API 试验；不导入 Society0 产品，不调用模型。"""
import asyncio
import json
import platform
from importlib.metadata import version
from pathlib import Path

import anyio
from bashkit import Bash


async def asyncio_child_cancel():
    state = []
    async def child():
        state.append('write')
        raise asyncio.CancelledError
    async with asyncio.TaskGroup() as group:
        group.create_task(child())
    state.append('group_exited_normally')
    assert state == ['write', 'group_exited_normally']
    return state


async def anyio_scoped_cleanup():
    state = []
    ready, cleaning, release = anyio.Event(), anyio.Event(), anyio.Event()
    scope = anyio.CancelScope()
    async def worker():
        with scope:
            try:
                ready.set()
                await anyio.sleep_forever()
            finally:
                with anyio.CancelScope(shield=True):
                    state.append('cleanup_started')
                    cleaning.set()
                    await release.wait()
                    state.append('cleanup_finished')
    async with anyio.create_task_group() as group:
        group.start_soon(worker)
        await ready.wait()
        scope.cancel()
        await cleaning.wait()
        scope.cancel()
        release.set()
    state.append('resources_may_close')
    assert state == ['cleanup_started', 'cleanup_finished', 'resources_may_close']
    return state


async def anyio_raw_cancel_boundary():
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    state = []
    async def worker():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            with anyio.CancelScope(shield=True):
                state.append('cleanup_started')
                cleaning.set()
                await release.wait()
                state.append('cleanup_finished')
    task = asyncio.create_task(worker())
    await started.wait()
    task.cancel()
    await cleaning.wait()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert state == ['cleanup_started']
    return {'raw_asyncio_task_cancel_pierces_anyio_shield': True, 'events': state}


async def bounded_workers():
    send, receive = anyio.create_memory_object_stream(8)
    active = peak = completed = 0
    async def worker(stream):
        nonlocal active, peak, completed
        async with stream:
            async for _ in stream:
                active += 1
                peak = max(peak, active)
                await anyio.sleep(0)
                completed += 1
                active -= 1
    async with anyio.create_task_group() as group:
        for _ in range(3):
            group.start_soon(worker, receive.clone())
        await receive.aclose()
        async with send:
            for item in range(1000):
                await send.send(item)
    assert peak <= 3 and completed == 1000
    return {'submitted': 1000, 'worker_tasks': 3, 'buffer_capacity': 8, 'peak_active': peak}


def shell_whole_read():
    size, calls = 8 * 1024 * 1024, []
    def lazy():
        calls.append(size)
        return 'x\n' * (size // 2)
    shell = Bash(files={'/large': lazy})
    assert not calls
    first = shell.execute_sync('head -c 4 /large')
    second = shell.execute_sync('head -c 4 /large')
    tail = shell.execute_sync('tail -c 4 /large')
    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout == 'x\nx\n'
    assert calls == [size]
    return {'source_bytes_materialized': sum(calls), 'calls': len(calls), 'first_output_bytes': 4, 'second_output_bytes': 4, 'tail_c_exit': tail.exit_code, 'tail_c_stderr': tail.stderr}


async def main():
    return {'python': platform.python_version(), 'anyio': version('anyio'), 'bashkit': version('bashkit'),
            'asyncio_child_cancel': await asyncio_child_cancel(),
            'anyio_scoped_cleanup': await anyio_scoped_cleanup(),
            'anyio_raw_cancel_boundary': await anyio_raw_cancel_boundary(),
            'bounded_workers': await bounded_workers(), 'bashkit_lazy': shell_whole_read()}


if __name__ == '__main__':
    print(json.dumps(anyio.run(main, backend='asyncio'), indent=2))
