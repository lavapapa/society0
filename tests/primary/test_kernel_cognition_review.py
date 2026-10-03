"""主体认知更新与合法游标的非作者验收。"""
import pytest
from tests.primary.test_kernel_cognition import Provider, session
from society0.kernel.storage import StageStore
from society0.kernel.threads import ThreadStore, THREAD_SCHEMA
from society0.kernel.llm import LLMDriver
from society0.kernel.cognition import CognitiveInput


@pytest.mark.asyncio
async def test_review_changed_persona_environment_precision_are_visible_in_existing_thread(tmp_path):
    settings={'environment':'old environment','precision':'old precision'}
    async def perception(current,cursor): return ([],cursor)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store); provider=Provider(threads)
        builder=CognitiveInput(threads,perception,environment=lambda s:settings['environment'],precision=lambda s:settings['precision'])
        driver=LLMDriver(provider,threads,input_builder=builder)
        await driver.run(session(driver))
        changed=session(driver)
        changed.actor.config.persona={'instance':'new persona'}
        changed.actor.config.state={'state':'new state'}
        settings.update(environment='new environment',precision='new precision')
        await driver.run(changed)
        text='\n'.join(m['content'] for m in provider.inputs[-1])
        assert all(value in text for value in ('new persona','new environment','new precision','new state'))
        assert 'old environment' in text


@pytest.mark.asyncio
async def test_review_none_perception_cursor_is_valid_after_completed_initial_input(tmp_path):
    positions=[]
    async def perception(current,cursor):
        positions.append(cursor)
        return ([{'role':'user','content':'new visible observation'}],None)
    with StageStore.create(tmp_path/'run',THREAD_SCHEMA) as store:
        threads=ThreadStore(store); provider=Provider(threads)
        builder=CognitiveInput(threads,perception,environment='stable')
        driver=LLMDriver(provider,threads,input_builder=builder)
        await driver.run(session(driver))
        await driver.run(session(driver))
        messages=provider.inputs[-1]
        assert sum(m['role']=='system' for m in messages)==1
        assert sum(m['content']=='new visible observation' for m in messages)==2
        assert positions==[None,None]
