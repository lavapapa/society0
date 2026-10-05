"""独立检查可选调温的请求范围，预算和原文仍由原循环处理。"""
import pytest
from society0.kernel.llm import LLMPolicy
from society0.kernel.interaction import Action, ActionResult
from tests.primary.test_kernel_llm import setup, reply, invoke


@pytest.mark.asyncio
async def test_review_empty_temperature_only_next_request_and_budget_stays_cumulative(tmp_path):
    policy = LLMPolicy(max_turns=8, empty_retries=1,
        empty_retry_temperature_delta=.2, request_options={'temperature': .1})
    store, threads, provider, driver, session, _ = setup(tmp_path,
        [reply(), reply(invoke()), reply()], policy=policy)
    with store:
        result = await driver.run(session)
        assert result.status == 'incomplete' and result.reason == 'empty_response'
        assert [r[1]['temperature'] for r in provider.requests] == [.1, .3, .1]
        notes = [e for e in threads.tail(session.cursors['thread_id'])['items']
                 if e['kind'] == 'provider_empty_response_retry']
        assert len(notes) == 1 and notes[0]['payload']['attempt'] == 1
        assert policy.request_options == {'temperature': .1}


@pytest.mark.asyncio
@pytest.mark.parametrize('interruption', ['required_text', 'invalid_parallel'])
async def test_review_repeated_read_early_continue_resets_streak(tmp_path, interruption):
    policy = LLMPolicy(max_turns=6, required_names=('work',),
        repeated_read_temperature_delta=.2, request_options={'temperature': .1})
    middle = reply(text='not yet') if interruption == 'required_text' else reply(invoke('x'), invoke('y'))
    store, threads, provider, driver, session, calls = setup(tmp_path,
        [reply(invoke('r1', 'read')), reply(invoke('r2', 'read')),
         middle, reply(invoke('work')), reply(text='done')], policy=policy)
    session.actions.actions.register(Action('read', ('m', 'job'), 'read', {},
        lambda *args: ActionResult('completed', {'facts': [{'namespace': 'm', 'kind': 'fact', 'key': 'f'}]}),
        read_only=True))
    with store:
        assert (await driver.run(session)).status == 'completed'
        assert [r[1]['temperature'] for r in provider.requests] == [.1, .1, .3, .1, .1]
        assert len(calls) == 1
        notes = [e for e in threads.tail(session.cursors['thread_id'])['items']
                 if e['kind'] == 'provider_repeated_read_diversification']
        assert [e['payload']['streak'] for e in notes] == [1]
        for earlier, later in zip(provider.requests, provider.requests[1:]):
            assert later[2][:len(earlier[2])] == earlier[2]


@pytest.mark.asyncio
@pytest.mark.parametrize('batch', [('new', 'new'), ('write', 'old', 'old')])
async def test_review_any_progress_in_multi_action_turn_clears_read_streak(tmp_path, batch):
    policy = LLMPolicy(parallel_tool_calls=True, max_turns=5,
        repeated_read_temperature_delta=.2, request_options={'temperature': .1})
    responses = [reply(invoke('old1', 'old')), reply(invoke('old2', 'old')),
                 reply(*(invoke(f'b{i}', name) for i, name in enumerate(batch))), reply(text='done')]
    store, threads, provider, driver, session, _ = setup(tmp_path, responses, policy=policy)
    for name in ('old', 'new'):
        session.actions.actions.register(Action(name, ('m', 'job'), name, {},
            lambda *args, name=name: ActionResult('completed', {
                'facts': [{'namespace': 'm', 'kind': 'fact', 'key': name}]}), read_only=True))
    session.actions.actions.register(Action('write', ('m', 'job'), 'write', {},
        lambda *args: ActionResult('completed', {'changed': True})))
    with store:
        assert (await driver.run(session)).status == 'completed'
        assert [r[1]['temperature'] for r in provider.requests] == [.1, .1, .3, .1]
        notes = [e for e in threads.tail(session.cursors['thread_id'])['items']
                 if e['kind'] == 'provider_repeated_read_diversification']
        assert [e['payload']['streak'] for e in notes] == [1]
