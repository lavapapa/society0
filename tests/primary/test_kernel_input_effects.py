"""认知效果随实际保存的输入批次选择；不依赖构建器的调用次数。"""
import pytest

from society0.kernel.cognition import InputBatch
from tests.primary.test_kernel_cognition import session
from tests.primary.test_kernel_llm import FakeProvider, reply
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from society0.kernel.llm import LLMDriver


@pytest.mark.asyncio
async def test_only_selected_persisted_batch_runs_its_effects(tmp_path):
    effects=[]
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        provider=FakeProvider(threads,[reply(text='done')])
        def build(current):
            discarded=InputBatch([{'role':'user','content':'未选中的材料'}],'discarded',0,
                effects=(lambda context:effects.append('discarded'),))
            def commit(context):
                assert threads.input_cursor(context.thread_id,'selected')['position']==1
                assert threads.read_messages(context.thread_id)[0]['content']=='选中的材料'
                effects.append('selected')
            return InputBatch([{'role':'user','content':'选中的材料'}],'selected',{'position':1},effects=(commit,))
        driver=LLMDriver(provider,threads,input_builder=build)
        await driver.run(session(driver))
        assert effects==['selected']
        assert len(provider.requests)==1


@pytest.mark.asyncio
async def test_invalid_input_runs_no_batch_effect(tmp_path):
    effects=[]
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store)
        provider=FakeProvider(threads,[])
        batch=InputBatch([{'role':'user','content':object()}],'bad',1,
            effects=(lambda context:effects.append('unexpected'),))
        driver=LLMDriver(provider,threads,input_builder=lambda current:batch)
        with pytest.raises(TypeError):await driver.run(session(driver))
        assert effects==[] and provider.requests==[]
