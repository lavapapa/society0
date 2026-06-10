import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from simengine.core_data import World, ExecutionContext


@pytest.mark.asyncio
async def test_round_robin_conversation_flow():
    world = World()
    world.environment_data["type"] = "round_robin_conversation"
    world.environment_data["state"]["config"] = {
        "group_size": 4,
        "session_duration_minutes": 12,
        "pairing_strategy": "standard",
        "message_persistence": False,
    }

    agent_ids = [f"agent_{idx}" for idx in range(1, 5)]
    for agent_id in agent_ids:
        world.add_agent_data(agent_id, agent_type="test", archetype="rule")

    env = world.get_environment()

    groups = [[member for member in group] for group in env.state["groups"]]
    assert groups == [agent_ids]
    pairing_status = env._get_pairing_status()
    assert pairing_status.total_rounds == 3

    env_context = ExecutionContext(
        world=world,
        step=None,
        node=None,
        caller=env,
        event_logger=world.event_logger,
        log_context=None,
    )
    await env.initialize_round_messages(env_context, round_number=1)

    agent_1 = world.get_agent("agent_1")
    first_round_pairs = pairing_status.pairing_schedule[0]
    target_pair = next(pair for pair in first_round_pairs if agent_1.id in pair)
    partner_id = target_pair[1] if target_pair[0] == agent_1.id else target_pair[0]
    agent_partner = world.get_agent(partner_id)

    context_a1 = ExecutionContext(
        world=world,
        step=None,
        node=None,
        caller=agent_1,
        event_logger=world.event_logger,
        log_context=None,
    )

    start_result = await env.start_pairing_session(
        context_a1, agent_1.id, agent_partner.id, round_number=1
    )
    assert start_result["status"] == "success"
    assert env.state["conversation_state"][agent_1.id]["current_partner"] == agent_partner.id
    assert env.state["conversation_state"][agent_partner.id]["current_partner"] == agent_1.id

    send_result = await env.send_message_to_partner(context_a1, agent_1, "你好，伙伴！")
    assert send_result["status"] == "success"
    assert env.state["message_counter"] == 1
    assert env.state["round_messages"][1][agent_partner.id][0]["content"] == "你好，伙伴！"

    broadcast_result = await env.broadcast_to_group(context_a1, agent_1, "大家好！")
    assert broadcast_result["status"] == "success"
    receivers = [member for member in groups[0] if member != agent_1.id]
    assert env.state["message_counter"] == 1 + len(receivers)  # 私聊 + 广播
    for receiver in receivers:
        assert env.state["round_messages"][1][receiver], f"{receiver} 未收到广播消息"

    status_payload = await env.get_agent_pairing_status(context_a1, agent_1.id)
    assert status_payload["current_partner"] == agent_partner.id
    assert status_payload["can_converse"] is True

    fov_text = await env.get_conversation_fov(agent_1, env)
    assert "=== 对话环境信息 ===" in fov_text
    assert "send_message_to_partner" in fov_text

    group_fov = await env.get_group_fov(agent_1, env)
    assert "小组成员" in group_fov
    assert f"第 1 轮" in group_fov

    advance_result = await env.advance_round_robin(env_context)
    assert advance_result["status"] == "advanced"
    assert advance_result["new_round"] == 2
    assert env.state["conversation_state"][agent_1.id]["current_partner"] is None

    await env.initialize_round_messages(env_context, round_number=2)
    assert 2 in env.state["round_messages"]
    assert list(env.state["round_messages"][2]["agent_1"]) == []
