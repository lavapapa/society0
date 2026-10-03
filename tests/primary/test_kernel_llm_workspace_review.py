"""工作区失败后的独立 Thread 状态验收。"""
from types import SimpleNamespace
import pytest
from tests.primary.test_kernel_llm import setup, reply


@pytest.mark.asyncio
async def test_review_workspace_save_failure_leaves_incomplete_thread_and_closes_shell(tmp_path):
    store,threads,provider,driver,session,calls=setup(tmp_path,[reply(text='done')])
    closed=[]
    async def close():closed.append(True)
    shell=SimpleNamespace(snapshot=lambda:b'snapshot',aclose=close)
    driver.shell_factory=lambda *a:shell
    def save(*a):raise OSError('disk full')
    driver.workspace=SimpleNamespace(save_workspace=save)
    try:
        with pytest.raises(OSError,match='disk full'):await driver.run(session)
        assert closed==[True]
        assert threads.describe(session.cursors['thread_id'])['status']=='incomplete'
    finally:store.close()
