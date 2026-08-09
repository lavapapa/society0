from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from society0.logging import ExperimentLogContext
from society0.models import LLMModel
from society0.resource_managers import EndpointConfig, LLMManager


def _tool(name: str) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": f"Call {name}",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class _FakeResponse:
    def __init__(self, tool_name: str) -> None:
        function = SimpleNamespace(name=tool_name, arguments="{}")
        tool_call = SimpleNamespace(id="call-1", type="function", function=function)
        message = SimpleNamespace(
            role="assistant",
            content="",
            reasoning_content="reasoning",
            tool_calls=[tool_call],
        )
        self.choices = [SimpleNamespace(message=message, finish_reason="tool_calls")]
        self.usage = None

    def model_dump(self, **_kwargs):
        return {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "reasoning",
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {"name": "required_action", "arguments": "{}"},
                            }
                        ],
                    },
                }
            ]
        }


def test_llm_model_validates_and_exposes_tool_choice_policy():
    model = LLMModel.openai_compatible(
        model="deepseek-test",
        base_url="https://example.invalid/v1",
        api_key="test-key",
        tool_choice_policy="auto_restrict",
    )

    assert model.endpoint_config()["tool_choice_policy"] == "auto_restrict"
    runtime, manager = model.build_runtime()
    try:
        assert runtime.config.tool_choice_policy == "auto_restrict"
        assert runtime.config.as_public_dict()["config"]["metadata"]["tool_choice_policy"] == "auto_restrict"
    finally:
        import asyncio

        asyncio.run(manager.close())

    with pytest.raises(ValueError, match="tool_choice_policy"):
        LLMModel.openai_compatible(
            model="bad",
            base_url="https://example.invalid/v1",
            tool_choice_policy="provider_magic",
        )


@pytest.mark.asyncio
async def test_auto_restrict_compiles_named_choice_and_audits_resolution(tmp_path):
    context = ExperimentLogContext(tmp_path / "logs")
    thread_id = context.open_agent_thread(
        agent_id="agent-a",
        checkpoint_step=1,
        scope={"kind": "test", "id": "tool-choice"},
    )
    model = LLMModel.openai_compatible(
        model="deepseek-test",
        base_url="https://example.invalid/v1",
        api_key="test-key",
        tool_choice_policy="auto_restrict",
    )
    manager = model.build_manager(log_context=context)
    captured = []

    class _Completions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            return _FakeResponse("required_action")

    manager.clients[model.id] = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions()),
        close=lambda: None,
    )
    payload = {
        "messages": [{"role": "user", "content": "act"}],
        "tools": [_tool("optional_action"), _tool("required_action")],
        "tool_choice": {
            "type": "function",
            "function": {"name": "required_action"},
        },
        "metadata": {"thread_id": thread_id, "agent_id": "agent-a"},
    }
    original = copy.deepcopy(payload)

    try:
        result = await manager.request(payload)
        context.close_agent_thread(thread_id)
        events = context.read_agent_thread_events(thread_id, materialize_payloads=True)
    finally:
        await manager.close()
        context.close()

    assert payload == original
    assert result["tool_calls"][0]["function"]["name"] == "required_action"
    assert captured[0]["tool_choice"] == "auto"
    assert [item["function"]["name"] for item in captured[0]["tools"]] == [
        "required_action"
    ]
    request_event = next(event for event in events if event["event_type"] == "provider_request")
    assert request_event["payload"]["request"]["tool_choice"] == "auto"
    assert request_event["payload"]["tool_choice_resolution"] == {
        "policy": "auto_restrict",
        "requested": {
            "type": "function",
            "function": {"name": "required_action"},
        },
        "effective": "auto",
        "selected_tool_name": "required_action",
        "tools_filtered": True,
        "original_tools_count": 2,
        "effective_tools_count": 1,
    }


def test_auto_restrict_required_uses_auto_without_filtering_tools():
    endpoint = SimpleNamespace(tool_choice_policy="auto_restrict")
    payload = {
        "tools": [_tool("first"), _tool("second")],
        "tool_choice": "required",
    }

    resolved, audit = LLMManager._resolve_tool_choice(endpoint, payload)

    assert resolved["tool_choice"] == "auto"
    assert resolved["tools"] == payload["tools"]
    assert audit["selected_tool_name"] is None
    assert audit["tools_filtered"] is False


def test_auto_restrict_rejects_named_choice_not_present_in_tools():
    endpoint = SimpleNamespace(tool_choice_policy="auto_restrict")
    with pytest.raises(ValueError, match="must match exactly one"):
        LLMManager._resolve_tool_choice(
            endpoint,
            {
                "tools": [_tool("available")],
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "missing"},
                },
            },
        )


def test_endpoint_rejects_unknown_tool_choice_policy():
    with pytest.raises(ValueError, match="tool_choice_policy"):
        EndpointConfig(
            id="bad",
            api_key="test-key",
            base_url="https://example.invalid/v1",
            model="bad",
            concurrency=1,
            tool_choice_policy="unknown",
        )
