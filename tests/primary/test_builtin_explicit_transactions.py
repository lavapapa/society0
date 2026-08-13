"""内置环境在显式状态事务模式下的业务写入合同。"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from society0 import Society0, StateAccessMode
from society0.core_data import ExecutionContext
from society0.state_transactions import ReadOnlyDict
from tests import read_last_v4_checkpoint


pytestmark = pytest.mark.primary


def _round_robin_config() -> dict[str, Any]:
    return {
        "agent_types": [{"id": "participant", "archetype": "rule"}],
        "agents": [
            {"id": f"participant_{idx}", "type": "participant", "state": {}}
            for idx in range(4)
        ],
        "environment": {
            "type": "round_robin_conversation",
            "config": {"group_size": 4, "session_duration_minutes": 5},
            "state": {},
        },
    }


def _social_network_config() -> dict[str, Any]:
    return {
        "agent_types": [{"id": "social_user", "archetype": "rule"}],
        "agents": [
            {"id": "author", "type": "social_user", "state": {}},
            {"id": "commenter", "type": "social_user", "state": {}},
            {"id": "liker", "type": "social_user", "state": {}},
            {"id": "viewer", "type": "social_user", "state": {}},
        ],
        "environment": {
            "type": "social_network",
            "config": {
                "distribution": {
                    "type": "random",
                    "params": {"connection_probability": 0.0},
                },
                "social_media": {
                    "recommendation": {
                        "use_embedding_similarity": False,
                        "post_count": 2,
                    },
                    "content_length_limit": -1,
                },
            },
            "state": {},
        },
    }


def _action_context(ctx, agent_id: str) -> ExecutionContext:
    return ExecutionContext(
        world=ctx.world,
        step=None,
        node=None,
        caller=ctx.world.get_agent(agent_id),
        event_logger=ctx.world.event_logger,
        log_context=ctx.log,
    )


def _without_runtime_messages(state: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(state)
    for key in ("config", "groups", "pairing_active_pairs", "active_messages", "message_retention"):
        result.pop(key, None)
    for message in result.get("message_facts", []):
        message.pop("timestamp", None)
    return result


def _without_runtime_social_ids(state: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(state)
    for event in result.get("post_interaction_facts", []):
        reply = event.get("reply")
        if isinstance(reply, dict):
            reply.pop("reply_id", None)
    return result


async def _run_round_robin(tmp_path, mode: StateAccessMode) -> dict[str, Any]:
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_round_robin_config(),
        checkpoint_every=1,
        state_access_mode=mode,
    )

    @engine.step(name="round_robin_write")
    async def round_robin_write(ctx):
        await ctx.rule("advance_round_robin_with_pairing", round_number=1)
        await ctx.env.send_message_to_partner(
            _action_context(ctx, "participant_0"),
            ctx.world.get_agent("participant_0"),
            "hello",
        )
        await ctx.env.broadcast_to_group(
            _action_context(ctx, "participant_0"),
            ctx.world.get_agent("participant_0"),
            "group update",
        )

    await engine.run(steps=1)
    return read_last_v4_checkpoint(tmp_path)["environment"]["state"]


async def _run_social_network(tmp_path, mode: StateAccessMode) -> dict[str, Any]:
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_network_config(),
        checkpoint_every=1,
        state_access_mode=mode,
    )

    @engine.step(name="social_write")
    async def social_write(ctx):
        await ctx.env.publish_post(
            _action_context(ctx, "author"),
            "a post for the explicit transaction test",
            tags=["test"],
        )
        await ctx.env.comment(
            _action_context(ctx, "commenter"),
            "post_1",
            "a reply",
        )
        assert "Successfully liked" in ctx.env.like_post(
            _action_context(ctx, "liker"),
            "post_1",
        )
        await ctx.env.repost(
            _action_context(ctx, "commenter"),
            "post_1",
            "sharing",
        )
        await ctx.env.recommended_feed(ctx.world.get_agent("viewer"), ctx.env)

    await engine.run(steps=1)
    return read_last_v4_checkpoint(tmp_path)["environment"]["state"]


@pytest.mark.asyncio
async def test_round_robin_explicit_success_has_transparent_delta_parity(tmp_path):
    transparent = await _run_round_robin(tmp_path / "transparent", StateAccessMode.TRANSPARENT_PROXY)
    explicit = await _run_round_robin(tmp_path / "explicit", StateAccessMode.EXPLICIT_TRANSACTIONS)

    assert _without_runtime_messages(explicit) == _without_runtime_messages(transparent)


@pytest.mark.asyncio
async def test_round_robin_explicit_business_exception_keeps_canonical_state(tmp_path):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_round_robin_config(),
        checkpoint_every=1,
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )

    @engine.step(name="round_robin_failure")
    async def round_robin_failure(ctx):
        await ctx.rule("advance_round_robin_with_pairing", round_number=1)
        before = copy.deepcopy(ctx.world.environment_data["state"])
        with pytest.raises(RuntimeError, match="round-robin business failure"):
            with ctx.env.write_transaction() as tx:
                await ctx.env._send_message_to_partner_impl(
                    _action_context(ctx, "participant_0"),
                    ctx.world.get_agent("participant_0"),
                    "discard me",
                    state=tx.state,
                )
                assert tx.state["message_facts"][-1]["content"] == "discard me"
                raise RuntimeError("round-robin business failure")
        assert ctx.world.environment_data["state"] == before

    await engine.run(steps=1)


@pytest.mark.asyncio
async def test_social_network_explicit_success_has_transparent_delta_parity(tmp_path):
    transparent = await _run_social_network(tmp_path / "transparent", StateAccessMode.TRANSPARENT_PROXY)
    explicit = await _run_social_network(tmp_path / "explicit", StateAccessMode.EXPLICIT_TRANSACTIONS)

    assert _without_runtime_social_ids(explicit) == _without_runtime_social_ids(transparent)


@pytest.mark.asyncio
async def test_social_network_explicit_business_exception_keeps_canonical_state(tmp_path):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_network_config(),
        checkpoint_every=1,
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )

    @engine.step(name="social_failure")
    async def social_failure(ctx):
        before = copy.deepcopy(ctx.world.environment_data["state"])
        with pytest.raises(RuntimeError, match="social business failure"):
            with ctx.env.write_transaction() as tx:
                await ctx.env._publish_post_impl(
                    _action_context(ctx, "author"),
                    "discard me",
                    tags=["discard"],
                    state=tx.state,
                )
                assert tx.state["post_creation_facts"]["post_1"]["content"] == "discard me"
                raise RuntimeError("social business failure")
        assert ctx.world.environment_data["state"] == before

    await engine.run(steps=1)


@pytest.mark.asyncio
async def test_plain_environment_runs_with_explicit_read_view(tmp_path):
    config = {
        "agent_types": [{"id": "plain_user", "archetype": "rule"}],
        "agents": [{"id": "user", "type": "plain_user", "state": {}}],
        "environment": {"type": "plain", "state": {}},
    }
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=config,
        checkpoint_every=1,
        state_access_mode=StateAccessMode.EXPLICIT_TRANSACTIONS,
    )
    observed: dict[str, Any] = {}

    @engine.step(name="plain_read")
    async def plain_read(ctx):
        observed["state_type"] = type(ctx.env.state)
        observed["state"] = dict(ctx.env.state)

    await engine.run(steps=1)
    assert observed == {"state_type": ReadOnlyDict, "state": {}}

