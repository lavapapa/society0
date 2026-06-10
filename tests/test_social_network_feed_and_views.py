import asyncio
import os
import sys

import pytest


# Keep import style consistent with existing tests in this repo.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from simengine.core_data import World


@pytest.mark.asyncio
async def test_recommended_feed_non_empty_increments_view_count():
    """
    Regression test:
    - Recommended feed should not be empty when there are posts in state.
    - Each FoV rendering should increment view_count in env state.
    """
    w = World(step=0)
    w.environment_data = {"type": "social_network", "state": {}, "config": {}}
    for i in range(3):
        w.add_agent_data(f"A{i:04d}", "test", archetype="rule")

    env = w.get_environment()

    # Seed one post by A0001 so A0000 can see it.
    env.state["posts"]["post_1"] = {
        "post_id": "post_1",
        "author_id": "A0001",
        "content": "hello",
        "created_tick": 0,
        "tags": [],
        "special_tags": [],
        "likes": [],
        "like_events": [],
        "replies": [],
        "votes": [],
        "view_count": 0,
        "reply_to": None,
    }

    agent = w.get_agent("A0000")

    txt1 = await env.get_recommended_feed(agent, env)
    assert "📱 个性化推荐动态" in txt1
    assert "帖子 post_1" in txt1
    assert env.state["posts"]["post_1"]["view_count"] == 1

    txt2 = await env.get_recommended_feed(agent, env)
    assert "帖子 post_1" in txt2
    assert env.state["posts"]["post_1"]["view_count"] == 2

