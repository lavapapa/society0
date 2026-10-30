"""激活入口的完整历史解码与工具回执落库短计时。"""
import pytest

from society0.kernel.llm import LLMPolicy
from tests.primary.test_kernel_llm import call, reply, setup


@pytest.mark.asyncio
async def test_activation_times_history_decode_and_tool_receipt_write(tmp_path):
    schema = {'type': 'object', 'properties': {'score': {'type': 'integer'}}, 'required': ['score']}
    store, threads, provider, driver, session, calls = setup(
        tmp_path, [reply(call('result', 'submit_result', {'score': 2}))],
        policy=LLMPolicy(mode='interview', result_schema=schema))
    try:
        result = await driver.run(session)
        assert result.status == 'completed'
        timings = result.value['phase_timings']
        for name in ('decode_s', 'tool_save_s', 'model_s', 'other_s'):
            assert timings[name] >= 0, name
        closed = threads.tail(result.value['thread_id'])['items'][-1]
        assert closed['kind'] == 'closed'
        assert closed['payload']['phase_timings'] == timings
        assert closed['payload']['elapsed_s'] >= sum(
            timings[name] for name in ('decode_s', 'tool_save_s', 'model_s'))
    finally:
        store.close()


@pytest.mark.asyncio
async def test_tool_receipt_time_is_absent_when_no_tool_ran(tmp_path):
    store, threads, provider, driver, session, calls = setup(tmp_path, [reply(text='done')])
    try:
        result = await driver.run(session)
        assert result.status == 'completed' and result.reason == 'natural_completion'
        timings = result.value['phase_timings']
        assert timings['decode_s'] >= 0
        assert 'tool_save_s' not in timings
    finally:
        store.close()
