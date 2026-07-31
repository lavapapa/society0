import asyncio

import pytest

from society0.schedule import AgentGroup


pytestmark = pytest.mark.primary


class _Memory:
    def __init__(self, agent_id, *, fail=False, gate=None):
        self.agent_id = agent_id
        self.fail = fail
        self.gate = gate
        self.calls = []

    async def add_episodic_memory(self, **kwargs):
        self.calls.append(kwargs)
        if self.gate is not None:
            await self.gate.wait()
        if self.fail:
            raise RuntimeError(f"{self.agent_id} write failed")
        return f"memory:{self.agent_id}:{len(self.calls)}"


class _Agent:
    def __init__(self, agent_id, memory):
        self.id = agent_id
        self.memory = memory


class _World:
    step = 7
    event_logger = None
    _default_agent_concurrency = 2
    _default_agent_concurrency_source = "test"

    def __init__(self, agents):
        self._agents = agents

    def get_agent(self, agent_id):
        return self._agents[agent_id]


@pytest.mark.asyncio
async def test_group_remember_writes_one_episode_per_agent_with_trace_and_metadata():
    alice_memory = _Memory("alice")
    bob_memory = _Memory("bob")
    world = _World(
        {
            "alice": _Agent("alice", alice_memory),
            "bob": _Agent("bob", bob_memory),
        }
    )

    result = await AgentGroup(world, ["alice", "bob"]).remember(
        {
            "alice": "本周决定维持报价。",
            "bob": "本周签署了一份采购合同。",
        },
        importance=3.5,
        metadata={"kind": "tick_episode"},
        metadata_by_agent={"bob": {"turn_count": 2}},
        name="week_7",
        timestamp=19,
    )

    assert result.success_count == 2
    assert result.error_count == 0
    assert result.by_agent("alice").value == {"memory_id": "memory:alice:1"}
    assert result.by_agent("bob").value == {"memory_id": "memory:bob:1"}
    assert alice_memory.calls == [
        {
            "content": "本周决定维持报价。",
            "timestamp": 19,
            "importance": 3.5,
            "metadata": {"kind": "tick_episode"},
            "trace": {
                "step": 19,
                "interaction_type": "memory_write",
                "interaction_name": "week_7",
            },
        }
    ]
    assert bob_memory.calls[0]["metadata"] == {
        "kind": "tick_episode",
        "turn_count": 2,
    }


@pytest.mark.asyncio
async def test_group_remember_rejects_missing_or_extra_content_before_any_write():
    memory = _Memory("alice")
    group = AgentGroup(_World({"alice": _Agent("alice", memory)}), ["alice"])

    with pytest.raises(ValueError, match="exactly match"):
        await group.remember({})
    with pytest.raises(ValueError, match="exactly match"):
        await group.remember({"alice": "ok", "bob": "extra"})
    with pytest.raises(ValueError, match="non-empty string"):
        await group.remember({"alice": "   "})

    assert memory.calls == []


@pytest.mark.asyncio
async def test_group_remember_isolates_agent_failures_and_honors_concurrency():
    gate = asyncio.Event()
    alice_memory = _Memory("alice", gate=gate)
    bob_memory = _Memory("bob", fail=True, gate=gate)
    world = _World(
        {
            "alice": _Agent("alice", alice_memory),
            "bob": _Agent("bob", bob_memory),
        }
    )
    task = asyncio.create_task(
        AgentGroup(world, ["alice", "bob"]).remember(
            {"alice": "a", "bob": "b"},
            concurrency=2,
        )
    )

    for _ in range(20):
        if alice_memory.calls and bob_memory.calls:
            break
        await asyncio.sleep(0)
    assert len(alice_memory.calls) == 1
    assert len(bob_memory.calls) == 1
    gate.set()
    result = await task

    assert result.success_count == 1
    assert result.error_count == 1
    assert result.by_agent("bob").error == "bob write failed"


@pytest.mark.asyncio
async def test_group_instruct_can_use_a_domain_tick_for_memory_time():
    captured = []

    class World(_World):
        agents_data = {"alice": {"id": "alice"}}

        async def instruct_agent(self, agent_id, instruction, **kwargs):
            captured.append(kwargs)
            return {"status": "success", "content": "ok"}

    result = await AgentGroup(World({}), ["alice"]).instruct(
        "经营公司。",
        memory=True,
        retrieve_memory=True,
        save_memory=False,
        extract_memory=False,
        current_step=23,
    )

    assert result.success_count == 1
    assert captured[0]["current_step"] == 23
    assert captured[0]["retrieve_memory"] is True
    assert captured[0]["save_memory"] is False
    assert captured[0]["extract_memory"] is False
