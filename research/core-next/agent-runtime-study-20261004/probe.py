"""Pydantic AI 2.54.0 的离线语义对照，无网络请求。"""
import asyncio
import json
from typing import Annotated
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext, Tool, ModelRetry, DeferredToolRequests, DeferredToolResults, CallDeferred
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, UserPromptPart, ModelMessagesTypeAdapter
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits


def scripted(*responses):
    observed = []
    async def request(messages, info):
        observed.append(list(messages))
        assert len(observed) <= len(responses), 'unexpected extra model request'
        return responses[len(observed)-1]
    return FunctionModel(request), observed


def call(name, args, ident='call-1'):
    return ModelResponse(parts=[ToolCallPart(name, args, ident)], finish_reason='tool_call')


def text(value='done', finish='stop'):
    return ModelResponse(parts=[TextPart(value)], finish_reason=finish)


async def main():
    report = {}
    # SDK 为有类型的工具处理参数校验及纠正。
    effects = []
    async def increment(amount: int):
        effects.append(amount)
        return amount
    model, seen = scripted(call('increment', {'amount':'invalid'}), call('increment', {'amount':2},'call-2'), text())
    result = await Agent(model, tools=[increment]).run('go')
    assert effects == [2] and len(seen) == 3
    report['typed_validation_retry'] = {'effects':effects, 'requests':len(seen), 'output':result.output}

    # 动态 JSON schema 的 from_schema 并不验证输入。
    effects = []
    async def dynamic(**args):
        effects.append(args)
        return args
    schema = {'type':'object','properties':{'amount':{'type':'integer'}},'required':['amount'],'additionalProperties':False}
    model, seen = scripted(call('dynamic', {'amount':'invalid'}), text())
    result = await Agent(model, tools=[Tool.from_schema(dynamic, 'dynamic', 'test', schema)]).run('go')
    assert effects == [{'amount':'invalid'}]
    report['from_schema_validation_gap'] = effects

    # 完整正文虽被截断，SDK 默认仍可能将其判断为正常输出。
    model, seen = scripted(text('truncated body', 'length'))
    result = await Agent(model).run('go')
    assert result.output == 'truncated body'
    report['length_text_default'] = {'output':result.output, 'requests':len(seen)}

    # SDK 迭代节点允许执行工具前发现截断，避免领域副作用。
    effects = []
    response = call('increment', {'amount':3})
    response.finish_reason = 'length'
    model, seen = scripted(response)
    async with Agent(model, tools=[increment]).iter('go') as run:
        node = run.next_node
        while not Agent.is_end_node(node):
            if Agent.is_call_tools_node(node) and node.model_response.finish_reason == 'length':
                break
            node = await run.next(node)
        original = run.all_messages()
    assert effects == [] and original[-1].finish_reason == 'length'
    report['iter_length_gate'] = {'effects':effects, 'retained_finish_reason':original[-1].finish_reason}

    # 同一 ID 的重复调用需继续由领域回执防重。
    effects = []
    response = ModelResponse(parts=[ToolCallPart('increment',{'amount':4},'same'),ToolCallPart('increment',{'amount':4},'same')],finish_reason='tool_call')
    model, seen = scripted(response, text())
    try:
        await Agent(model, tools=[Tool(increment, sequential=True)]).run('go')
    except Exception as error:
        duplicate_error = type(error).__name__
    assert effects == [] and duplicate_error == 'UnexpectedModelBehavior'
    report['duplicate_call_id_default'] = {'effects':list(effects),'error':duplicate_error}
    model, seen = scripted(call('increment', {'amount':4}, 'same'), call('increment', {'amount':4}, 'same'), text())
    await Agent(model, tools=[increment]).run('go')
    report['duplicate_across_turns'] = effects

    # 终止领域动作之后可以在下一模型请求之前结束本次迭代。
    effects = []
    model, seen = scripted(call('increment', {'amount':5}))
    async with Agent(model, tools=[increment]).iter('go') as run:
        node = run.next_node
        while not Agent.is_end_node(node):
            node = await run.next(node)
            if effects and Agent.is_model_request_node(node):
                break
        messages = run.all_messages()
        # CallToolsNode 把待发送工具回执存在下个节点中；手动结束需发布这一段。
        pending = node.request
    assert effects == [5] and len(seen) == 1
    report['iter_terminal_without_extra_model'] = {'effects':effects, 'requests':len(seen), 'pending_return_parts':len(pending.parts), 'message_history_contains_tool_return':any(getattr(p,'part_kind','')=='tool-return' for m in messages for p in m.parts)}

    # 必需动作条件通过 output_validator 约束自由文字的完成。
    effects = []
    model, seen = scripted(text('early'), call('increment', {'amount':6}), text('done'))
    agent = Agent(model, tools=[increment])
    @agent.output_validator
    async def required(ctx: RunContext, output: str):
        if not effects: raise ModelRetry('Required business action has not completed.')
        return output
    result = await agent.run('go')
    assert effects == [6] and len(seen) == 3
    report['required_action_output_validator'] = {'effects':effects, 'requests':len(seen)}

    # Deferred 跨调用恢复保留全部消息，并无需重新执行先前已完成的动作。
    executions=[]
    async def later(amount:int):
        executions.append(amount)
        raise CallDeferred()
    model, seen = scripted(call('later', {'amount':7}), text('resumed'))
    agent = Agent(model, tools=[later], output_type=[str, DeferredToolRequests])
    first = await agent.run('go', conversation_id='simulation-thread')
    frozen = ModelMessagesTypeAdapter.validate_json(first.all_messages_json())
    second = await agent.run(message_history=frozen, deferred_tool_results=DeferredToolResults(calls={'call-1':{'completed':True}}), conversation_id='simulation-thread')
    assert executions == [7] and second.output == 'resumed'
    assert len(seen[-1]) > len(seen[0])
    report['deferred_roundtrip']={'executions':executions,'output':second.output,'history_messages':len(second.all_messages()), 'separate_run_ids':first.run_id != second.run_id}

    # Structured output 的校验纠正可直接用于 interview/memory 提取。
    class Measurement(BaseModel):
        value: Annotated[int, Field(strict=True, ge=0)]
    model, seen = scripted(call('final_result',{'value':-1}), call('final_result',{'value':8},'call-2'))
    result = await Agent(model, output_type=Measurement).run('measure')
    assert result.output.value == 8 and len(seen) == 2
    report['structured_output_validation']={'value':result.output.value,'requests':len(seen)}
    report['default_request_limit']=UsageLimits().request_limit
    print(json.dumps(report,ensure_ascii=False,indent=2))

asyncio.run(main())
