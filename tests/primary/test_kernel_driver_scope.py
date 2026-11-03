"""驱动装配保留七个工具及prepare之前的稳定决策作用域。"""
import asyncio

import pytest

from society0.kernel.drivers import llm_driver_plugin
from society0.kernel.plugins import Plugin
from society0.kernel.services import thread_plugin


class FakeModels:
    def __getitem__(self, name):
        return self

    def validate_options(self, options):
        return None


class FakeThreads:
    pass


def _models_plugin():
    # 模型孪生沿用正式插件名与二元引用，装配路径和生产一致。
    def install(context):
        context.provide('models', {'m': FakeModels()})
    return Plugin('models', (), install)


@pytest.mark.asyncio
async def test_driver_plugin_declares_seven_tools(tmp_path):
    from society0.kernel.composition import compose
    plugins = [thread_plugin(), _models_plugin(),
               llm_driver_plugin(provider=('models', 'models', 'm'), input_builder=lambda session: [])]
    async with compose(tmp_path / 'run', plugins) as host:
        driver = host.service('llm_driver', 'factory')(None)
        assert {item['function']['name'] for item in driver._tools()} == {
            'action_find', 'action_describe', 'action_invoke', 'read', 'ls', 'find', 'grep'}


@pytest.mark.asyncio
async def test_decision_scope_is_available_to_preparation_callbacks(tmp_path):
    """prepare() 之前提供决策 Thread、水位、provider、有序工具与租约。"""
    from types import SimpleNamespace
    from society0.kernel.interaction import Action, ActionResult, Actions, Information, InteractionScope, Moment
    from society0.kernel.llm import LLMDriver, LLMPolicy, DecisionScope
    from society0.kernel.runtime import Actor, Session
    from society0.kernel.storage import StageStore
    from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
    from tests.primary.test_kernel_llm import FakeProvider, reply

    store = StageStore.create(tmp_path / 'decision', THREAD_SCHEMA)
    try:
        threads = ThreadStore(store)
        provider = FakeProvider(threads, [reply(text='done')])
        actions = Actions(lambda *a: True)
        actions.register(Action('work', ('m', 'job'), 'work', {'type': 'object'},
                                lambda *a: ActionResult('completed')))
        seen = {}
        async def prepare(context):
            scope = context.decision
            seen.update(kind=type(scope).__name__, through=scope.through,
                        provider=scope.provider, tools=scope.tool_names,
                        route=scope.model_route, lease=scope.lease)
            assert scope.through == threads.describe(scope.thread_id)['last_seq']
        driver = LLMDriver(provider, threads, input_builder=lambda session: [
            {'role': 'user', 'content': 'current input'}], policy=LLMPolicy())
        from society0.kernel.activation import ActivationContext, activation_scope
        scope = InteractionScope('a', Moment(1, 'p'))
        session = Session(Actor('a', driver), scope, Information(lambda *a: True).bound(scope),
                          actions.bound(scope), {}, None, (),
                          SimpleNamespace(prepare_artifact=store.prepare_artifact), step=1)
        original = driver.extensions
        driver.extensions = (lambda context: _extension(context, prepare),)
        try:
            assert (await asyncio.wait_for(driver.run(session), 5)).status == 'completed'
        finally:
            driver.extensions = original
        assert seen['kind'] == 'DecisionScope'
        assert seen['provider'] is provider
        assert seen['tools'][0] == 'action_find' and 'action_invoke' in seen['tools']
        assert isinstance(seen['through'], int) and seen['lease'] is None
    finally:
        store.close()


def _extension(context, prepare):
    from contextlib import asynccontextmanager
    @asynccontextmanager
    async def scope():
        context.preparations.append(prepare)
        yield
    return scope()
