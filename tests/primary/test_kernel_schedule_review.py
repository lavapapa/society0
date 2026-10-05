"""完整步骤资源收束的独立消费者。"""
import asyncio
import pytest
from society0.kernel.plugins import Plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin
from society0.kernel.runtime import runtime_plugin,Actor,Phase

@pytest.mark.asyncio
async def test_review_second_close_cancellation_does_not_abandon_driver_cleanup(tmp_path):
    started=asyncio.Event();cleaning=asyncio.Event();release=asyncio.Event();cleaned=[];at_close=[]
    class Driver:
        async def run(self,session):
            started.set()
            try:await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release.wait()
                cleaned.append(True)
    def resource(ctx):ctx.on_close(lambda:at_close.append(bool(cleaned)))
    plugins=[interaction_plugin(lambda *a:True),Plugin('resource',install=resource),
        runtime_plugin([Actor('a',Driver())],information=('interaction','information'),
            actions=('interaction','actions'),store=('storage','store'))]
    manager=compose(tmp_path/'run',plugins);host=await manager.__aenter__()
    runtime=host.service('runtime','runtime')
    async def phase(ctx):ctx.activate('a')
    running=asyncio.create_task(runtime.run_step(1,1,[Phase('act',phase)]))
    await started.wait()
    closing=asyncio.create_task(manager.__aexit__(None,None,None))
    try:
        await cleaning.wait()
        closing.cancel()
        for _ in range(5):await asyncio.sleep(0)
        release.set()
        await asyncio.gather(closing,running,return_exceptions=True)
        assert cleaned==[True] and at_close==[True]
    finally:
        release.set()
        await asyncio.gather(closing,running,return_exceptions=True)
