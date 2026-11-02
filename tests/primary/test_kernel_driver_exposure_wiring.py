"""驱动装配入口：默认无暴露、直接对象与二元服务引用都走同一条公共装配路径。"""
import asyncio

import pytest

from society0.kernel.drivers import llm_driver_plugin
from society0.kernel.plugins import Plugin
from society0.kernel.services import thread_plugin
from society0.kernel.tool_exposure import ToolExposure


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


def _exposure_plugin():
    def install(context):
        context.provide('decision_tools', ToolExposure(lambda session: []))

    return Plugin('decision_tools_double', (), install)


@pytest.mark.asyncio
@pytest.mark.parametrize('exposure', [None, ToolExposure(lambda session: []), ('decision_tools_double', 'decision_tools')])
async def test_driver_plugin_accepts_absent_object_and_service_reference(tmp_path, exposure):
    from society0.kernel.composition import compose
    plugins = [thread_plugin(), _models_plugin(), _exposure_plugin(),
               llm_driver_plugin(provider=('models', 'models', 'm'), input_builder=lambda session: [],
                                 tool_exposure=exposure)]
    async with compose(tmp_path / 'run', plugins) as host:
        factory = host.service('llm_driver', 'factory')
        driver = factory(None)
        assert driver.tool_exposure is None or isinstance(driver.tool_exposure, ToolExposure)


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
