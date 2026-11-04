"""畸形工具调用走 SDK 重试：模型可自行修正，线索留在 Thread，超限才判协议错误。"""
import json

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart

from society0.kernel.model_messages import history
from tests.primary.test_kernel_llm import invoke, reply, setup


def malformed(identifier='bad', name='action_invoke', arguments='{'):
    return {'id': identifier, 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [malformed(), malformed(name='action_resolve_agenda', arguments='{}')],
                         ids=['truncated_json', 'unknown_tool'])
async def test_malformed_call_is_retried_and_activation_recovers(tmp_path, bad):
    store, threads, provider, driver, session, calls = setup(tmp_path, [reply(bad), reply(invoke()), reply(text='done')])
    try:
        result = await driver.run(session)
        assert (result.status, result.reason) == ('completed', 'natural_completion')
        assert len(provider.requests) == 3 and len(calls) == 1
        messages = threads.read_messages(result.value['thread_id'])
        answers = [item for item in messages if item.get('role') == 'tool' and item['tool_call_id'] == 'bad']
        assert len(answers) == 1 and answers[0]['content']
        # 冷恢复重建的历史中，每个调用都有配对结果，非法参数也不会把坏 JSON 发给模型。
        rebuilt = history(messages)
        arguments = [part.args_as_json_str() for message in rebuilt if isinstance(message, ModelResponse)
                     for part in message.parts if isinstance(part, ToolCallPart)]
        assert all(isinstance(json.loads(item), dict) for item in arguments)
    finally:
        store.close()


@pytest.mark.asyncio
async def test_persistently_malformed_call_ends_as_protocol_error(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(malformed(f'bad{index}')) for index in range(3)])
    try:
        result = await driver.run(session)
        assert (result.status, result.reason) == ('incomplete', 'model_protocol_error')
        assert len(provider.requests) == 3 and not calls
    finally:
        store.close()


@pytest.mark.asyncio
async def test_retries_count_against_activation_budget(tmp_path):
    store, _, provider, driver, session, calls = setup(tmp_path, [reply(malformed()), reply(text='done')])
    try:
        result = await driver.run(session)
        assert result.status == 'completed' and result.value['requests'] == 2
    finally:
        store.close()
