#!/usr/bin/env python3
"""
验证 submit_result 强制机制与后校验/严格性生效：
- 首轮即注入 submit_result 工具
- LLM 返回包含 submit_result 的 tool_call，arguments.result 为结构化输出
- 结果应被正确提取为 structured_output，并标记 finish_instruction_called=True
"""

import asyncio


def test_submit_result_basic_flow():
    from simengine.core_data import World
    from simengine.agent.core import LLMAgent

    world = World(step=0)
    world.add_agent_data("alice", "tester", "llm")
    agent = LLMAgent("alice", world)

    async def mock_llm_call(payload: dict) -> dict:
        # 当工具被注入时，返回一次 submit_result 的 tool_call
        tools = payload.get("tools") or []
        has_submit = any(
            (t.get("function", {}) or {}).get("name") == "submit_result" for t in tools
        )
        if has_submit:
            return {
                "role": "assistant",
                "content": "-> STAGE_BEGIN: Actions\n提交结果",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "submit_result",
                            "arguments": '{"result": {"x": "ok"}}',
                        },
                    }
                ],
            }
        # 回退普通文本
        return {"role": "assistant", "content": "准备提交"}

    # 初始化认知系统（无需记忆）
    agent.initialize_cognitive_system(
        persona="你是一个认真的测试代理。",
        memory=None,
        llm_call=mock_llm_call,
    )

    async def run():
        # 简单的输出schema（不含required，代码应自动补齐并严格 additionalProperties=false）
        output_schema = {
            "type": "object",
            "properties": {
                "x": {"type": "string"}
            }
        }
        result = await agent.instruct(
            instruction="请生成一个包含字段x的JSON并通过submit_result提交",
            output_schema=output_schema,
        )
        return result

    res = asyncio.run(run())
    assert res.get("status") == "success"
    assert res.get("finish_instruction_called") is True
    so = res.get("structured_output")
    assert isinstance(so, dict) and so.get("x") == "ok"


def test_interview_submit_result_terminates_loop_without_actions_stage():
    from simengine.core_data import World
    from simengine.agent.core import LLMAgent

    world = World(step=0)
    world.add_agent_data("alice", "tester", "llm")
    agent = LLMAgent("alice", world)

    call_counter = {"n": 0}
    tool_choices = []

    async def mock_llm_call(payload: dict) -> dict:
        call_counter["n"] += 1
        tool_choices.append(payload.get("tool_choice"))
        if call_counter["n"] == 1:
            return {
                "role": "assistant",
                "content": "-> STAGE_BEGIN: 动机解释\n提交结果",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "submit_result",
                            "arguments": '{"result": {"x": "ok"}}',
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "不应再进入下一轮", "tool_calls": []}

    agent.initialize_cognitive_system(
        persona="你是一个认真的测试代理。",
        memory=None,
        llm_call=mock_llm_call,
        reasoning_stages=[{"name": "动机解释", "desc": "解释行为动机"}],
    )

    async def run():
        output_schema = {
            "type": "object",
            "properties": {
                "x": {"type": "string"}
            }
        }
        result = await agent.interview(
            question="请提交结构化结果",
            output_schema=output_schema,
        )
        return result

    res = asyncio.run(run())
    assert res.get("status") == "success"
    assert res.get("finish_instruction_called") is True
    assert res.get("total_turns") == 1
    assert tool_choices.count("auto") == 1
    so = res.get("structured_output")
    assert isinstance(so, dict) and so.get("x") == "ok"

    raw_output = res.get("raw_output") or {}
    phases = raw_output.get("phases") or {}
    phase_values = [value for value in phases.values() if isinstance(value, list)]
    assert phase_values
    assert any(
        isinstance(item, dict)
        and item.get("type") == "action_call"
        and item.get("action_name") == "submit_result"
        for phase_value in phase_values
        for item in phase_value
    )
