"""Python 嵌入者直接两次取消 await run 的最小生命周期验证。"""
import asyncio
import json


async def run_case():
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events = []
    async def child():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            events.append('cleanup_started')
            cleaning.set()
            await release.wait()
            events.append('cleanup_finished')
    async def owner():
        try:
            async with asyncio.TaskGroup() as group:
                group.create_task(child())
                await asyncio.Event().wait()
        finally:
            events.append('resource_close')
    task = asyncio.create_task(owner())
    await started.wait()
    task.cancel()
    await cleaning.wait()
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert events == ['cleanup_started','cleanup_finished','resource_close'], events
    return {'external_cancel_count': 2, 'events': events, 'cancel_propagated': True}

if __name__ == '__main__':print(json.dumps(asyncio.run(run_case()),indent=2))
