"""模型选择只选具名共享提供方；优先级在调用处明确。"""
import pytest
from society0.kernel.model_selection import ModelResolver


def test_model_resolution_priority_and_missing_profile():
    profiles={name:object() for name in ('default','type','actor','override')}
    resolver=ModelResolver(profiles,'default',type_models={'worker':'type'},actor_models={'a':'actor'})
    assert resolver.resolve('other') is profiles['default']
    assert resolver.resolve('other',actor_type='worker') is profiles['type']
    assert resolver.resolve('a',actor_type='worker') is profiles['actor']
    assert resolver.resolve('a',actor_type='worker',override='override') is profiles['override']
    with pytest.raises(KeyError,match='missing'):resolver.resolve('a',override='missing')


def test_model_resolution_requires_declared_default_and_profiles():
    with pytest.raises(ValueError,match='default'):ModelResolver({},'default')
    with pytest.raises(ValueError,match='unknown'):ModelResolver({'d':object()},'d',actor_models={'a':'unknown'})


@pytest.mark.asyncio
async def test_driver_activation_override_preserves_same_thread_and_provider_session(tmp_path):
    from tests.primary.test_kernel_llm import setup,FakeProvider,reply
    store,threads,first,driver,session,_=setup(tmp_path,[reply(text='first full response')])
    second=FakeProvider(threads,[reply(text='second full response')])
    resolver=ModelResolver({'first':first,'second':second},'first')
    overrides=iter(('first','second'))
    driver.provider_selector=lambda s:resolver.resolve(s.actor.id,override=next(overrides))
    try:
        one=await driver.run(session);tid=one.value['thread_id']
        identity=threads.describe(tid)['provider_session_id']
        two=await driver.run(session)
        assert two.value['thread_id']==tid
        assert len(first.requests)==len(second.requests)==1
        assert threads.describe(tid)['provider_session_id']==identity
        assert any(m.get('content')=='first full response' for m in second.requests[0][2])
    finally:store.close()


@pytest.mark.asyncio
async def test_reasoning_stage_guidance_and_unknown_output_preserve_original_without_extra_request(tmp_path):
    from tests.primary.test_kernel_llm import setup,reply
    from society0.kernel.llm import LLMPolicy
    original='前言原文\n-> stage_begin: observe\n完整观察🙂\n-> stage_begin: new_stage\n未知阶段原文'
    policy=LLMPolicy(reasoning_stages=({'name':'observe','desc':'观察完整材料'}, {'name':'decide','desc':'自主决定'}))
    store,threads,provider,driver,session,_=setup(tmp_path,[reply(text=original)],policy=policy)
    try:
        result=await driver.run(session)
        assert result.status=='completed' and len(provider.requests)==1
        assert any('观察完整材料' in m.get('content','') for m in provider.requests[0][2])
        assert threads.read_messages(result.value['thread_id'])[-1]['content']==original
        stages=result.value['reasoning_stages']
        assert stages[0]['segments'][1]['name']=='observe'
        assert stages[0]['segments'][2]['name']=='new_stage'
        assert stages[0]['segments'][2]['known'] is False
        segment=stages[0]['segments'][2]
        assert original[segment['start']:segment['end']]=='未知阶段原文'
    finally:store.close()


@pytest.mark.asyncio
async def test_malformed_stage_marker_is_original_text_and_guidance_not_repeated(tmp_path):
    from tests.primary.test_kernel_llm import setup,reply
    from society0.kernel.llm import LLMPolicy
    original='-> stage_begin: \n未按阶段标注但完整的回答'
    store,threads,provider,driver,session,_=setup(tmp_path,[reply(text=original),reply(text='next')],
        policy=LLMPolicy(reasoning_stages=({'name':'思考','desc':'完整观察'},)))
    try:
        first=await driver.run(session)
        assert first.value['reasoning_stages'][0]['segments']==[{'name':'default','known':True,'start':0,'end':len(original)}]
        await driver.run(session)
        assert len(provider.requests)==2
        assert sum(m.get('content','').startswith('按任务需要思考并行动，可参考以下阶段：') for m in provider.requests[-1][2])==1
        assert any(m.get('content')==original for m in provider.requests[-1][2])
    finally:store.close()
