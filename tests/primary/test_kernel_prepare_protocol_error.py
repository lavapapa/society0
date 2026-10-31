"""激活准备阶段的模型协议错误按 incomplete 收束；其他异常仍按原类型失败。"""
from contextlib import asynccontextmanager

import pytest
from pydantic_ai.exceptions import UnexpectedModelBehavior

from society0.kernel.cognition import InputBatch
from society0.kernel.llm import LLMDriver
from society0.kernel.models import ProviderFailure
from society0.kernel.storage import StageStore
from society0.kernel.threads import THREAD_SCHEMA, ThreadStore
from tests.primary.test_kernel_cognition import session
from tests.primary.test_kernel_llm import FakeProvider, reply


class Probe:
    """记录扩展退出时看到的 DriverResult；成功专用写入据此决定是否执行。"""
    def __init__(self, seen): self.seen = seen

    @asynccontextmanager
    async def __call__(self, context):
        yield
        result = context.result
        self.seen.append((None if result is None else result.status,
                          None if result is None else result.reason))


def protocol_error(context):
    raise UnexpectedModelBehavior('malformed planner output: missing query')


@pytest.mark.asyncio
async def test_prepare_protocol_error_is_incomplete_instead_of_failing_phase(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        provider = FakeProvider(threads, [])
        seen = []
        def build(current):
            return InputBatch([{'role': 'user', 'content': '本轮材料'}], 'operating_context',
                              {'position': 1}, effects=(protocol_error,))
        driver = LLMDriver(provider, threads, input_builder=build, extensions=(Probe(seen),))
        current = session(driver)
        result = await driver.run(current)
        assert (result.status, result.reason) == ('incomplete', 'model_protocol_error')
        assert provider.requests == []
        thread = threads.find(current.actor.id, current.moment, 'decision')
        assert threads.describe(thread)['status'] == 'incomplete'
        items = threads.tail(thread, limit=200)['items']
        kinds = [item['kind'] for item in items]
        assert 'model_protocol_error' in kinds
        detail = next(item['payload'] for item in items if item['kind'] == 'model_protocol_error')
        assert 'malformed planner output' in detail['error']
        closed = [item['payload'] for item in items if item['kind'] == 'closed']
        assert closed[-1]['reason'] == 'model_protocol_error'
        assert any(message.get('content') == '本轮材料' for message in threads.read_messages(thread))
        assert seen == [('incomplete', 'model_protocol_error')]


@pytest.mark.asyncio
async def test_prepare_provider_failure_stays_fatal(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        provider = FakeProvider(threads, [])
        def prepare(context):
            raise ProviderFailure('provider_incomplete', 'provider returned no complete response')
        def build(current):
            return InputBatch([{'role': 'user', 'content': '本轮材料'}], 'operating_context',
                              {'position': 1}, effects=(prepare,))
        driver = LLMDriver(provider, threads, input_builder=build)
        current = session(driver)
        with pytest.raises(ProviderFailure):
            await driver.run(current)
        assert provider.requests == []
        thread = threads.find(current.actor.id, current.moment, 'decision')
        assert threads.describe(thread)['status'] == 'incomplete'


@pytest.mark.asyncio
async def test_prepare_plain_error_stays_fatal(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        provider = FakeProvider(threads, [])
        def prepare(context):
            raise RuntimeError('storage damaged')
        def build(current):
            return InputBatch([{'role': 'user', 'content': '本轮材料'}], 'operating_context',
                              {'position': 1}, effects=(prepare,))
        driver = LLMDriver(provider, threads, input_builder=build)
        with pytest.raises(RuntimeError, match='storage damaged'):
            await driver.run(session(driver))


@pytest.mark.asyncio
async def test_prepare_protocol_error_keeps_same_moment_history_and_cursor(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        provider = FakeProvider(threads, [reply(text='先前的决定')])
        driver = LLMDriver(provider, threads,
                           input_builder=lambda current: [{'role': 'user', 'content': '第一轮材料'}])
        current = session(driver)
        first = await driver.run(current)
        assert (first.status, first.reason) == ('completed', 'natural_completion')
        thread = threads.find(current.actor.id, current.moment, 'decision')
        before = [message.get('content') for message in threads.read_messages(thread)]
        session_before = threads.describe(thread)['provider_session_id']
        def build(current):
            return InputBatch([{'role': 'user', 'content': '第二轮材料'}], 'operating_context',
                              {'position': 2}, effects=(protocol_error,))
        seen = []
        second_driver = LLMDriver(provider, threads, input_builder=build, extensions=(Probe(seen),))
        second = await second_driver.run(session(second_driver))
        assert (second.status, second.reason) == ('incomplete', 'model_protocol_error')
        after = [message.get('content') for message in threads.read_messages(thread)]
        assert after[:len(before)] == before
        assert '第二轮材料' in after
        assert threads.describe(thread)['provider_session_id'] == session_before
        assert threads.input_cursor(thread, 'operating_context')['position'] == 2
        # 扩展按正常生命周期退出，看到的是 incomplete 结果，成功专用写入据此跳过。
        assert seen == [('incomplete', 'model_protocol_error')]


@pytest.mark.asyncio
async def test_iter_provider_failure_keeps_reason(tmp_path):
    with StageStore.create(tmp_path / 'run', THREAD_SCHEMA) as store:
        threads = ThreadStore(store)
        provider = FakeProvider(threads, [ProviderFailure('provider_timeout', 'provider timed out')])
        driver = LLMDriver(provider, threads,
                           input_builder=lambda current: [{'role': 'user', 'content': '材料'}])
        result = await driver.run(session(driver))
        assert (result.status, result.reason) == ('incomplete', 'provider_timeout')
