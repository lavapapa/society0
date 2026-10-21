import json

import pytest
from jsonschema import Draft202012Validator

from society0.kernel.interaction import Action, ActionResult, Ref
from society0.kernel.llm import LLMPolicy, _Ledger
from tests.primary.test_kernel_llm import setup, reply, call


@pytest.mark.asyncio
@pytest.mark.parametrize('strict', [False, True])
async def test_empty_cursor_feedback_allows_next_corrected_call(tmp_path, strict):
    target = {'namespace': 'm', 'kind': 'job', 'key': '1'}
    bad = {'target': target, 'query': '', 'limit': 1, 'cursor': '{}' if strict else {}}
    good = {'target': target, 'query': '', 'limit': 1}
    if strict: good['cursor'] = None
    store, threads, provider, driver, session, _ = setup(tmp_path,
        [reply(call('bad', 'action_find', bad)), reply(call('good', 'action_find', good)), reply(text='done')],
        policy=LLMPolicy(strict_tools=strict, max_turns=64))
    try:
        assert (await driver.run(session)).status == 'completed'
        feedback = [json.loads(m['content']) for m in threads.read_messages(session.cursors['thread_id']) if m['role'] == 'tool']
        assert 'omit cursor or set it to null' in feedback[0]['message']
        assert 'KeyError' not in json.dumps(feedback[0])
        assert feedback[1]['total'] == 1 and feedback[1]['items'][0]['name'] == 'work'
        schema = next(t['function']['parameters'] for t in driver._tools() if t['function']['name'] == 'action_find')
        if not strict:
            assert not Draft202012Validator(schema).is_valid(bad)
            assert Draft202012Validator(schema).is_valid(good)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_cursor_paging_and_changed_query_correction(tmp_path):
    store, threads, _, driver, session, _ = setup(tmp_path, [])
    session.actions.actions.register(Action('other', ('m', 'job'), 'other', {'type': 'object'}, lambda *a: ActionResult('completed')))
    try:
        ledger = _Ledger(driver, session, threads.open('a', session.moment, 'decision'))
        target = Ref('m', 'job', '1')
        first = await ledger.find(target, query='', limit=1)
        assert first.total == 2 and first.next_cursor
        second = await ledger.find(target, query='', limit=1, cursor=first.next_cursor)
        assert second.total == 2 and second.next_cursor is None
        assert {x.name for x in first.items + second.items} == {'work', 'other'}
        with pytest.raises(ValueError, match='new query/target, omit cursor'):
            await ledger.find(target, query='work', limit=1, cursor=first.next_cursor)
        mismatch = await driver._dispatch('action_find', {'target': {'namespace': 'm', 'kind': 'job', 'key': '1'},
            'query': 'work', 'limit': 1, 'cursor': first.next_cursor}, session, ledger, None)
        assert 'new query/target, omit cursor' in mismatch['message']
        assert (await ledger.find(target, query='work', limit=1)).total == 1
        with pytest.raises(ValueError, match='Invalid action_find cursor'):
            await ledger.find(target, cursor={})
    finally:
        store.close()
