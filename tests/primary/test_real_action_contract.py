"""真实端点测试合同迁移的确定性负例。"""
import pytest
from society0.agent.agent_loop import ActionSet
from society0.agent.core import LLMAgent
from society0.context_stack import ContextStack

pytestmark = pytest.mark.primary


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["budget", "length"])
async def test_nonterminal_budget_exhaustion_never_becomes_success_memory(failure):
    calls, executed, writes, retrievals = [], [], [], []

    class Memory:
        async def retrieve(self, **kwargs):
            retrievals.append(kwargs)
            return []

        async def add_memories_batch(self, *args, **kwargs):
            writes.append((args, kwargs))
            return []

        async def add_memory(self, *args, **kwargs):
            writes.append((args, kwargs))

    class World:
        agents_data = {"alice": {"id": "alice", "type": "participant", "archetype": "llm",
                                "persona": "Finish the task deliberately.", "state": {},
                                "properties": {}, "reminders": []}}
        event_logger = None

        def get_environment(self):
            return type("Env", (), {"agent_instruction": ""})()

        def get_log_context(self):
            return None

        def get_context_stack(self):
            return ContextStack()

        def set_context_stack(self, stack):
            self.context_stack = stack

    async def place_order():
        executed.append("order")
        return "order accepted"

    async def llm(payload):
        calls.append(payload)
        return {"role": "assistant", "content": "", **({"finish_reason": "length"} if failure == "length" else {}), "tool_calls": [{"id": "place-once",
            "type": "function", "function": {"name": "place_order", "arguments": "{}"}}]}

    actions = ActionSet()
    actions.add_action("place_order", place_order, "Place an order", {"type": "object", "properties": {}})
    agent = LLMAgent("alice", World())
    agent.initialize_cognitive_system(persona="Finish the task deliberately.", memory=Memory(),
                                      llm_call=llm, actionset=actions)
    result = await agent.instruct("Place an order if useful, then assess the result.",
        retrieve_memory=True, max_turns=3, max_action_calls=1)
    assert executed == (["order"] if failure == "budget" else [])
    assert len(calls) == 1
    if failure == "budget":
        assert result["actions"][0]["status"] == "success"
    else:
        assert result["actions"] == []
    assert result["status"] == "error"
    assert result["activation_status"] == "incomplete"
    assert result["termination_reason"] == ("action_budget_exhausted" if failure == "budget" else "output_token_limit")
    assert len(retrievals) == 1
    assert writes == []
    assert all(call.get("metadata", {}).get("interaction_type") != "memory_extract" for call in calls)
