"""专用工具暴露：选择器形状、注册表 schema 来源、别名与失败边界。
"""
import asyncio
from types import SimpleNamespace

import pytest

from society0.kernel import llm as llm_module
from society0.kernel.interaction import Action, ActionResult, Actions, InteractionScope, Moment, Ref
from society0.kernel.llm import LLMDriver, LLMPolicy
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from society0.kernel.tool_exposure import ExposedAction, ToolExposure, selection_items, tool_alias
from tests.primary.test_kernel_llm import FakeProvider, reply


TARGET = Ref('industry', 'actor', 'a')


def test_selection_accepts_one_item_or_an_ordered_sequence():
    assert selection_items(None) == ()
    assert selection_items(('industry.set_price', TARGET)) == (('industry.set_price', TARGET),)
    assert selection_items([('industry.set_price', TARGET), ('industry.set_plan', TARGET)]) == (
        ('industry.set_price', TARGET), ('industry.set_plan', TARGET))
    assert selection_items([]) == ()
    with pytest.raises(TypeError):
        selection_items({'namespace': 'industry', 'kind': 'actor', 'key': 'a', 'action': 'x'})


def test_named_action_without_target_is_rejected():
    with pytest.raises(TypeError):
        selection_items('industry.set_price')


def test_duplicate_action_in_one_activation_is_rejected():
    with pytest.raises(ValueError):
        selection_items([('industry.set_price', TARGET), ('industry.set_price', TARGET)])


def test_target_may_be_ref_mapping_or_triple():
    assert selection_items([('x', {'namespace': 'industry', 'kind': 'actor', 'key': 'a'})])[0][1] == TARGET
    assert selection_items([('x', ('industry', 'actor', 'a'))])[0][1] == TARGET


def test_alias_is_a_legal_provider_function_name():
    assert tool_alias('industry.set_price') == 'industry_set_price'
    with pytest.raises(ValueError):
        tool_alias('9 leading digit')


def setup_exposure(tmp_path, replies, selection, schema):
    store = StageStore.create(tmp_path / 'run', THREAD_SCHEMA)
    threads = ThreadStore(store)
    provider = FakeProvider(threads, replies)
    actions = Actions(lambda *a: True)
    actions.register(Action('industry.set_price', ('industry', 'actor'), '改价',
                            schema, lambda scope, target, arguments: ActionResult('completed', {'price': arguments['price']})))
    scope = InteractionScope('a', Moment(1, 'p'))
    driver = LLMDriver(provider, threads, input_builder=lambda session: [
        {'role': 'system', 'content': 'persona 完整内容'},
    ], policy=LLMPolicy(), tool_exposure=ToolExposure(lambda session: selection))
    return store, threads, provider, driver, actions, scope


@pytest.mark.asyncio
async def test_exposed_action_schema_and_execution_share_the_registry(tmp_path):
    schema = {'type': 'object', 'properties': {'price': {'type': 'string'}}, 'required': ['price'],
              'additionalProperties': False}
    store, threads, provider, driver, actions, scope = setup_exposure(
        tmp_path, [reply({'id': 'c1', 'type': 'function', 'function': {
            'name': 'industry_set_price', 'arguments': '{"price":"12.50"}'}}), reply(text='done')],
        [('industry.set_price', TARGET)], schema)
    try:
        from society0.kernel.interaction import Information
        from society0.kernel.runtime import Actor, Session
        session = Session(Actor('a', driver), scope, Information(lambda *a: True).bound(scope),
                          actions.bound(scope), {}, None, (), SimpleNamespace(prepare_artifact=store.prepare_artifact), step=1)
        result = await asyncio.wait_for(driver.run(session), 5)
        assert result.status == 'completed'
        options = provider.requests[0][1]
        names = [item['function']['name'] for item in options['tools']]
        assert names[-1] == 'industry_set_price'
        assert options['tools'][-1]['function']['parameters'] == schema
        receipts = [event['payload'] for event in threads.tail(session.cursors['thread_id'], limit=1000)['items']
                    if event['kind'] == 'tool_receipt']
        assert receipts[0]['metadata']['actions'][0]['name'] == 'industry.set_price'
        assert result.value is not None
    finally:
        store.close()


@pytest.mark.asyncio
async def test_unavailable_exposed_action_fails_instead_of_silently_dropping(tmp_path):
    """选定但不可用的动作让本次激活失败，不能悄悄从工具数组消失。"""
    schema = {'type': 'object', 'properties': {}}
    store, threads, provider, driver, actions, scope = setup_exposure(
        tmp_path, [reply(text='done')], [('industry.missing', TARGET)], schema)
    try:
        from society0.kernel.interaction import Information, Unavailable
        from society0.kernel.runtime import Actor, Session
        session = Session(Actor('a', driver), scope, Information(lambda *a: True).bound(scope),
                          actions.bound(scope), {}, None, (), SimpleNamespace(prepare_artifact=store.prepare_artifact), step=1)
        with pytest.raises(Unavailable):
            await asyncio.wait_for(driver.run(session), 5)
    finally:
        store.close()
