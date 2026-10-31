"""行动参数校验失败保留 jsonschema 细节；反馈经真实驱动回执可见。"""
import json

import pytest

from society0.kernel.interaction import Action, ActionResult, Actions, InteractionScope, Moment, Ref

SCHEMA = {
    'type': 'object',
    'properties': {'expected': {'type': 'string'}, 'kind': {'type': 'string', 'enum': ['a', 'b']}},
    'required': ['expected'],
    'additionalProperties': False,
}


def bound_actions(handler):
    calls = []

    def work(scope, target, arguments):
        calls.append(arguments)
        return handler(arguments)

    actions = Actions(lambda *a: True)
    actions.register(Action('work', ('m', 'job'), 'work', SCHEMA, work))
    return actions.bound(InteractionScope('a', Moment(1, 'p'))), calls


@pytest.mark.asyncio
async def test_invalid_action_arguments_carry_jsonschema_details():
    bound, calls = bound_actions(lambda arguments: ActionResult('completed', {}))
    result = await bound.invoke('work', Ref('m', 'job', '1'), {'kind': 'z', 'extra': 1})
    assert result.status == 'rejected' and result.value['reason'] == 'invalid_arguments'
    assert calls == []
    details = result.value['details']
    validators = {item['validator'] for item in details}
    assert {'required', 'additionalProperties', 'enum'} <= validators
    enum = next(item for item in details if item['validator'] == 'enum')
    assert enum['expected'] == ['a', 'b'] and enum['path'] == ['kind']
    missing = next(item for item in details if item['validator'] == 'required')
    assert missing['expected'] == ['expected'] and missing['path'] == []
    extra = next(item for item in details if item['validator'] == 'additionalProperties')
    assert extra['path'] == [] and 'extra' in extra['message']


@pytest.mark.asyncio
async def test_legal_action_result_is_unchanged():
    bound, calls = bound_actions(lambda arguments: ActionResult('completed', {'echo': arguments}))
    result = await bound.invoke('work', Ref('m', 'job', '1'), {'expected': 'ok', 'kind': 'a'})
    assert result.status == 'completed' and result.value == {'echo': {'expected': 'ok', 'kind': 'a'}}
    assert calls == [{'expected': 'ok', 'kind': 'a'}]


@pytest.mark.asyncio
async def test_driver_tool_feedback_carries_details_without_running_handler(tmp_path):
    from tests.primary.test_kernel_llm import invoke, reply, setup
    store, threads, provider, driver, session, calls = setup(
        tmp_path, [reply(invoke(arguments={'kind': 'z', 'extra': 1})), reply(text='done')], schema=SCHEMA)
    result = await driver.run(session)
    assert result.status == 'completed' and result.reason == 'natural_completion'
    assert calls == []
    thread_id = session.cursors['thread_id']
    feedback = next(message for message in threads.read_messages(thread_id) if message['role'] == 'tool')
    payload = json.loads(feedback['content'])
    details = payload['result']['value']['details']
    assert {item['validator'] for item in details} >= {'required', 'additionalProperties', 'enum'}
    assert payload['result']['status'] == 'rejected'
