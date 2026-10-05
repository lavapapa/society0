"""SDK typed 消息是模型正文权威；角色视图供研究者和认知消费者读取。"""
from __future__ import annotations
import json


def encode(response):
    from pydantic_ai.messages import ModelMessagesTypeAdapter
    return ModelMessagesTypeAdapter.dump_python([response], mode='json')[0]


def display(message):
    if 'model_message' not in message: return message
    parts = message['model_message']['parts']
    output = {'role': 'assistant', 'content': ''.join(p['content'] for p in parts if p['part_kind'] == 'text')}
    thoughts = ''.join(p['content'] for p in parts if p['part_kind'] == 'thinking')
    if thoughts: output['reasoning_content'] = thoughts
    calls = [{'id': p['tool_call_id'], 'type': 'function', 'function': {
        'name': p['tool_name'], 'arguments': p['args'] if isinstance(p['args'], str) else json.dumps(p['args'] or {}, ensure_ascii=False)}}
        for p in parts if p['part_kind'] == 'tool-call']
    if calls: output['tool_calls'] = calls
    return output


def history(messages):
    from pydantic_ai.messages import (ModelMessagesTypeAdapter, ModelRequest, ModelResponse,
        SystemPromptPart, UserPromptPart, ToolReturnPart, TextPart, ThinkingPart, ToolCallPart)
    output, names = [], {}
    for message in messages:
        if 'model_message' in message:
            converted = ModelMessagesTypeAdapter.validate_python([message['model_message']])[0]
        elif message['role'] in ('system', 'developer'):
            converted = ModelRequest(parts=[SystemPromptPart(content=message['content'])])
        elif message['role'] == 'user':
            converted = ModelRequest(parts=[UserPromptPart(content=message['content'])])
        elif message['role'] == 'tool':
            identifier = message['tool_call_id']
            converted = ModelRequest(parts=[ToolReturnPart(tool_name=names[identifier], tool_call_id=identifier, content=message['content'])])
        elif message['role'] == 'assistant':
            parts = [TextPart(message['content'])] if message.get('content') else []
            if message.get('reasoning_content'): parts.insert(0, ThinkingPart(message['reasoning_content']))
            for call in message.get('tool_calls', ()):
                parts.append(ToolCallPart(call['function']['name'], call['function']['arguments'], call['id']))
            converted = ModelResponse(parts=parts)
        else: raise ValueError('unsupported Thread message role')
        if isinstance(converted, ModelResponse):
            for part in converted.parts:
                if isinstance(part, ToolCallPart): names[part.tool_call_id] = part.tool_name
        output.append(converted)
    return output
