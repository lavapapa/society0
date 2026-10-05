"""独立消费者核对运行发布与资源退出的错误边界。"""
import json
import pytest
from society0.kernel.plugins import Plugin
from society0.kernel.runner import run_plan
from society0.kernel.storage import StageStore
from tests.primary.test_kernel_runner import plan


@pytest.mark.asyncio
async def test_review_resource_cleanup_failure_is_reported_after_complete(tmp_path):
    candidate=plan([])
    def install(context):
        def close():raise RuntimeError('resource close failed')
        context.on_close(close)
    candidate.plugins.append(Plugin('resource',install=install))
    path=tmp_path/'run'
    with pytest.raises(RuntimeError,match='resource close failed'):
        await run_plan(path,candidate)
    # 已发布业务完整点仍可信，运行退出故障须在独立状态中可见。
    saved=json.loads((path/'runner-status.json').read_text())
    assert saved['complete_step']==2
    assert saved['status']=='failed' and saved['error']=='RuntimeError'
    with StageStore.open(path) as store:
        assert store.complete_step==2


@pytest.mark.asyncio
async def test_review_manifest_failure_releases_resources_and_store(tmp_path,monkeypatch):
    import society0.kernel.runner as module
    seen=[];closed=[];candidate=plan(seen)
    def install(context):
        store=context.require('storage','store')
        context.on_close(lambda:closed.append(store.read(lambda view:view.complete_step)))
    candidate.plugins.append(Plugin('resource',('storage',),install))
    def fail(*args):raise OSError('manifest write failed')
    monkeypatch.setattr(module,'_write_manifest',fail)
    with pytest.raises(OSError,match='manifest write failed'):
        await run_plan(tmp_path/'run',candidate)
    assert seen==[] and closed==[0]
    with StageStore.open(tmp_path/'run') as store:
        assert store.complete_step==0
