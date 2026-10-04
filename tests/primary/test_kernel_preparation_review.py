"""准备作用域不得吞掉初始化或清理异常。"""
import pytest
from society0.kernel.plugins import Plugin
from society0.kernel.composition import compose
from society0.kernel.storage import StageStore


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['root','later_cleanup'])
async def test_review_prepare_manager_cannot_suppress_fatal_failure(tmp_path,failure):
    class Suppressor:
        def __enter__(self):
            def initialize(writer):
                if failure=='root':raise ValueError('original root failure')
            return initialize
        def __exit__(self,*exc):return True
    class Later:
        async def __aenter__(self):return lambda writer:None
        async def __aexit__(self,*exc):raise ValueError('original cleanup failure')
    plugins=[Plugin('first',prepare=Suppressor)]
    if failure=='later_cleanup':plugins.append(Plugin('second',('first',),prepare=Later))
    with pytest.raises(ValueError,match='original'):
        async with compose(tmp_path/'run',plugins):pass
    if failure=='root':assert not (tmp_path/'run').exists()
    else:
        with StageStore.open(tmp_path/'run') as store:assert store.complete_step==0


@pytest.mark.asyncio
async def test_review_prepare_cancellation_closes_file_before_root_publication(tmp_path, monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    from examples.core_next import graph_environment
    source=tmp_path/'graph.json';source.write_text('{}')
    entered=asyncio.Event();release=asyncio.Event();observed=[]
    @asynccontextmanager
    async def prepare():
        with source.open() as stream:
            try:
                entered.set()
                await release.wait()
                yield lambda writer:None
            finally:
                observed.append(stream.closed)
    plugin=graph_environment.graph_plugin(source)
    from dataclasses import replace
    plugin=replace(plugin,prepare=prepare)
    async def run():
        async with compose(tmp_path/'run',[plugin]):pass
    task=asyncio.create_task(run());await entered.wait()
    task.cancel();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert observed==[False] and not (tmp_path/'run').exists()
