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
async def test_review_graph_repeated_cancel_drains_native_read_before_file_close(tmp_path, monkeypatch):
    import asyncio
    import threading
    from examples.core_next import graph_environment
    source = tmp_path / 'graph.json'
    source.write_text('{}')
    entered = threading.Event()
    release = threading.Event()
    observed = []
    def blocked_load(stream):
        entered.set()
        release.wait(5)
        observed.append(stream.closed)
        return {'nodes': [], 'edges': []}
    monkeypatch.setattr(graph_environment.json, 'load', blocked_load)
    async def run():
        async with compose(tmp_path / 'run', [graph_environment.graph_plugin(source)]):
            pass
    task = asyncio.create_task(run())
    try:
        while not entered.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        for _ in range(5):
            await asyncio.sleep(0)
        assert not task.done(), 'native read must drain before closing its file'
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    assert observed == [False]
    assert not (tmp_path / 'run').exists()
