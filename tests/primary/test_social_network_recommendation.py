import json
from pathlib import Path

import pytest

from society0 import Society0
from tests import read_last_v4_checkpoint

pytestmark = pytest.mark.primary


def _social_config(*, recommendation=None, agents=None):
    recommendation = recommendation or {}
    agents = agents or [
        {"id": "viewer", "type": "social_user", "state": {}},
        {"id": "viewer_2", "type": "social_user", "state": {}},
        {"id": "author_old", "type": "social_user", "state": {}},
        {"id": "author_recent", "type": "social_user", "state": {}},
        {"id": "reposter", "type": "social_user", "state": {}},
    ]
    return {
        "agent_types": [{"id": "social_user", "archetype": "rule"}],
        "agents": agents,
        "environment": {
            "type": "social_network",
            "config": {
                "social_media": {
                    "recommendation": {
                        "post_count": 8,
                        "candidate_count": 20,
                        "use_embedding_similarity": False,
                        "chronological_weight": 0.05,
                        "engagement_weight": 1.0,
                        "similarity_weight": 0.0,
                        "network_weight": 0.0,
                        **recommendation,
                    },
                    "content_length_limit": -1,
                }
            },
            "state": {},
        },
    }


def _read_checkpoint(path: Path):
    return read_last_v4_checkpoint(path)


def _post(post_id, *, author_id="author_recent", created_tick=0, likes=0, replies=0, reply_to=None, content=None):
    return {
        "post_id": post_id,
        "author_id": author_id,
        "content": content or f"Content for {post_id}",
        "tags": [],
        "created_tick": created_tick,
        "likes": [f"liker_{idx}" for idx in range(likes)],
        "like_events": [],
        "replies": [
            {
                "reply_id": f"{post_id}_reply_{idx}",
                "author_id": f"commenter_{idx}",
                "content": "reply",
                "created_tick": created_tick + 1,
            }
            for idx in range(replies)
        ],
        "view_count": 0,
        **({"reply_to": reply_to} if reply_to else {}),
    }


def _seed_post(env, post):
    """通过 v4 canonical 容器写入测试帖子及其初始事实。"""
    post_id = str(post["post_id"])
    creation = {
        key: post[key]
        for key in ("post_id", "author_id", "content", "tags", "created_tick", "reply_to")
        if key in post
    }
    env.state["post_creation_facts"][post_id] = creation
    env.state["post_projection"][post_id] = {
        "view_count": int(post.get("view_count", 0) or 0),
        "special_tags": list(post.get("special_tags", []) or []),
    }
    env.state["author_post_facts"].append(
        {"author_id": str(post.get("author_id") or ""), "post_id": post_id}
    )
    for liker_id in post.get("likes", []) or []:
        env.state["post_interaction_facts"].append(
            {
                "kind": "like",
                "post_id": post_id,
                "agent_id": liker_id,
                "created_tick": post.get("created_tick", 0),
            }
        )
    for reply in post.get("replies", []) or []:
        env.state["post_interaction_facts"].append(
            {"kind": "comment", "post_id": post_id, "reply": reply}
        )


def _load_many_posts(env, *, count=1000):
    for idx in range(count):
        _seed_post(
            env,
            _post(
                f"post_recent_{idx:04d}",
                created_tick=idx + 1,
                likes=0,
                replies=0,
            ),
        )
    _seed_post(
        env,
        _post(
            "post_0900_old_high_engagement",
            author_id="author_old",
            created_tick=0,
            likes=40,
            replies=12,
            content="Older but central claim with heavy discussion.",
        ),
    )
    _seed_post(
        env,
        _post(
            "post_0900_repost",
            author_id="reposter",
            created_tick=2,
            reply_to="post_0900_old_high_engagement",
        ),
    )


@pytest.mark.asyncio
async def test_social_network_recommended_feed_public_fov_and_profile_tools(tmp_path, caplog):
    caplog.set_level("WARNING")
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="inspect_capabilities")
    async def inspect_capabilities(ctx):
        registry = ctx.world.get_logic_provider()
        fov_names = ctx.capabilities.names("fov")
        action_names = ctx.capabilities.names("action")
        env_fov_names = ctx.capabilities.names("fov", source="environment")
        env_action_names = ctx.capabilities.names("action", source="environment")
        env_rule_names = ctx.capabilities.names("rule", source="environment")
        trending_matches = ctx.capabilities.find("get_trending_posts")
        profile_tool_matches = ctx.capabilities.find("get_agent_profile", kind="tools", source="environment")
        actionset = ctx.world.assemble_agent_actionset(ctx.world.get_agent("viewer"))

        observed["recommended_feed_entry"] = ctx.world._resolve_fov_entry("recommended_feed")
        observed["recommended_preview_entry"] = ctx.world._resolve_fov_entry("recommended_feed_preview")
        observed["recommended_method_entry"] = ctx.world._resolve_fov_entry("env.recommended_feed")
        observed["legacy_python_alias"] = await ctx.env.get_recommended_feed(
            ctx.world.get_agent("viewer"),
            ctx.env,
        )
        observed["fov_names"] = fov_names
        observed["action_names"] = action_names
        observed["env_fov_names"] = env_fov_names
        observed["env_action_names"] = env_action_names
        observed["env_rule_names"] = env_rule_names
        observed["trending_matches"] = trending_matches
        observed["profile_tool_matches"] = profile_tool_matches
        observed["env_capabilities"] = ctx.capabilities.by_source("environment")
        observed["registry_fovs"] = set(registry.env_fovs.keys())
        observed["registry_actions"] = set(registry.env_agent_tools.keys())
        observed["social_actions"] = set(actionset.filter_by_tags(["social"]).actions.keys())
        observed["social_read_actions"] = set(actionset.filter_by_tags(["social_read"]).actions.keys())
        observed["environment_actions"] = set(actionset.filter_by_tags(["environment"]).actions.keys())
        observed["post_vector_where"] = ctx.env._post_vector_where()
        _seed_post(
            ctx.env,
            _post(
                "post_hot",
                author_id="author_old",
                likes=12,
                replies=3,
                content="Highly discussed campus health policy update.",
            ),
        )
        observed["profile"] = await actionset.call_action("get_agent_profile", agent_id="viewer")
        observed["trending"] = await actionset.call_action("get_trending_posts")
        return None

    await engine.run(steps=1)

    assert observed["recommended_feed_entry"] is not None
    assert observed["recommended_preview_entry"] is not None
    assert observed["recommended_method_entry"] is not None
    assert "推荐" in observed["legacy_python_alias"]
    assert "recommended_feed" in observed["fov_names"]
    assert "recommended_feed_preview" in observed["fov_names"]
    assert "get_recommended_feed" not in observed["fov_names"]
    assert "get_agent_profile" not in observed["fov_names"]
    assert "get_trending_posts" not in observed["fov_names"]
    assert "get_agent_profile" not in observed["env_fov_names"]
    assert "get_trending_posts" not in observed["env_fov_names"]
    assert "get_agent_profile" in observed["action_names"]
    assert "get_trending_posts" in observed["action_names"]
    assert "get_agent_profile" in observed["env_action_names"]
    assert "get_trending_posts" in observed["env_action_names"]
    assert "update_trending_topics" in observed["env_rule_names"]
    assert "get_agent_profile" in {entry["name"] for entry in observed["env_capabilities"]["actions"]}
    assert [(entry["kind"], entry["source"], entry["name"]) for entry in observed["trending_matches"]] == [
        ("action", "environment", "get_trending_posts")
    ]
    assert [(entry["kind"], entry["source"], entry["name"]) for entry in observed["profile_tool_matches"]] == [
        ("action", "environment", "get_agent_profile")
    ]
    profile_capability = next(
        entry for entry in observed["env_capabilities"]["actions"] if entry["name"] == "get_agent_profile"
    )
    assert "environment" in profile_capability["tags"]
    recommended_capability = next(
        entry for entry in observed["env_capabilities"]["fovs"] if entry["name"] == "recommended_feed"
    )
    assert recommended_capability["func_name"] == "recommended_feed"
    assert "env.recommended_feed" in recommended_capability["aliases"]
    assert "env.get_recommended_feed" not in recommended_capability["aliases"]
    assert "get_agent_profile" in observed["registry_actions"]
    assert "get_trending_posts" in observed["registry_actions"]
    assert "get_agent_profile" not in observed["registry_fovs"]
    assert "get_trending_posts" not in observed["registry_fovs"]
    assert {"publish_post", "like_post", "comment", "repost", "follow", "unfollow"}.issubset(
        observed["social_actions"]
    )
    assert "get_agent_profile" not in observed["social_actions"]
    assert {"get_agent_profile", "get_trending_posts", "get_post_details"}.issubset(
        observed["social_read_actions"]
    )
    assert {"publish_post", "get_agent_profile", "get_trending_posts"}.issubset(
        observed["environment_actions"]
    )
    assert observed["post_vector_where"] == {
        "$and": [
            {"branch_id": {"$eq": "main"}},
            {"created_step": {"$lte": 0}},
            {"visible_until_step": {"$gt": 0}},
        ]
    }
    assert "用户资料: viewer" in observed["profile"]
    assert "热门" in observed["trending"]
    assert "帖子 ID: post_hot" in observed["trending"]
    assert "作者用户 ID: author_old" in observed["trending"]
    assert "non-standard signature" not in caplog.text

    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    capabilities = summary["capabilities"]
    assert capabilities["environment_type"] == "social_network"
    assert capabilities["counts"]["fovs"] >= 3
    assert capabilities["counts"]["actions"] >= 9
    assert capabilities["counts"]["rules"] >= 1
    assert capabilities["by_source"]["environment"]["fovs"] >= 3
    assert capabilities["by_source"]["environment"]["actions"] >= 9
    assert capabilities["by_source"]["environment"]["rules"] >= 1
    fov_names = {entry["name"] for entry in capabilities["by_kind"]["fovs"]}
    action_names = {entry["name"] for entry in capabilities["by_kind"]["actions"]}
    rule_names = {entry["name"] for entry in capabilities["by_kind"]["rules"]}
    assert "recommended_feed" in fov_names
    assert "recommended_feed_preview" in fov_names
    assert "get_agent_profile" in action_names
    assert "get_trending_posts" in action_names
    assert "update_trending_topics" in rule_names


@pytest.mark.asyncio
async def test_social_network_recommended_feed_does_not_write_stdout(tmp_path, capsys):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())

    @engine.step(name="render_feed")
    async def render_feed(ctx):
        _seed_post(
            ctx.env,
            _post(
                "post_visible",
                author_id="author_old",
                content="A visible post for stdout regression.",
            ),
        )
        feed = await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        assert "post_visible" in feed

    await engine.run(steps=1)

    captured = capsys.readouterr()
    assert captured.out == ""


@pytest.mark.asyncio
async def test_social_network_state_change_events_are_hidden_by_default_but_checkpoint_is_full(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    full_content = "完整帖子正文应该保留在 checkpoint 中，但不应该重复写满 monitoring events。 " * 20

    @engine.step(name="write_large_post")
    async def write_large_post(ctx):
        _seed_post(
            ctx.env,
            _post(
                "post_full_checkpoint",
                author_id="author_old",
                content=full_content,
                likes=3,
                replies=2,
            ),
        )
        return None

    await engine.run(steps=1)

    event_records = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    post_state_events = [
        event
        for event in event_records
        if event.get("event_type") == "STATE_CHANGE"
        and event.get("target_type") == "environment"
        and (event.get("path") or [])[:1] in (["post_creation_facts"], ["post_projection"])
    ]
    assert post_state_events == []
    assert full_content not in json.dumps(post_state_events, ensure_ascii=False)

    checkpoint = _read_checkpoint(tmp_path)
    checkpoint_post = checkpoint["environment"]["state"]["post_creation_facts"]["post_full_checkpoint"]
    assert checkpoint_post["content"] == full_content


@pytest.mark.asyncio
async def test_social_network_state_change_events_can_be_enabled_for_debugging(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config(), log_state_changes=True)
    full_content = "调试模式可以记录状态变更摘要，但不能把完整正文塞进 events。 " * 20

    @engine.step(name="write_large_post")
    async def write_large_post(ctx):
        _seed_post(
            ctx.env,
            _post(
                "post_full_checkpoint",
                author_id="author_old",
                content=full_content,
                likes=3,
                replies=2,
            ),
        )
        return None

    await engine.run(steps=1)

    event_records = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    post_state_events = [
        event
        for event in event_records
        if event.get("event_type") == "STATE_CHANGE"
        and event.get("target_type") == "environment"
        and (event.get("path") or [])[:1] in (["post_creation_facts"], ["post_projection"])
    ]
    assert post_state_events
    assert "value" not in post_state_events[0]
    assert post_state_events[0]["value_omitted"] is True
    assert post_state_events[0]["value_summary"]["type"] == "dict"
    assert full_content not in json.dumps(post_state_events, ensure_ascii=False)


@pytest.mark.asyncio
async def test_social_network_action_names_in_fovs_get_friendly_error(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="inspect_fov_error")
    async def inspect_fov_error(ctx):
        observed["profile_error"] = ctx.world._fov_not_found_message("get_agent_profile")
        observed["trending_error"] = ctx.world._fov_not_found_message("get_trending_posts")
        return None

    await engine.run(steps=1)

    assert "environment action, not a FoV" in observed["profile_error"]
    assert "actions=['get_agent_profile']" in observed["profile_error"]
    assert "environment action, not a FoV" in observed["trending_error"]
    assert "actions=['get_trending_posts']" in observed["trending_error"]


@pytest.mark.asyncio
async def test_recommendation_scores_full_active_pool(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="recommend")
    async def recommend(ctx):
        _load_many_posts(ctx.env, count=1000)
        viewer = ctx.world.get_agent("viewer")
        candidates = ctx.env._get_real_posts_only(viewer)
        feed = await ctx.env.get_recommended_feed(viewer, ctx.env)
        observed["candidate_count"] = len(candidates)
        observed["recommended_ids"] = list(ctx.env._pending_recommended_posts["viewer"])
        observed["state_recommended_during_step"] = dict(ctx.env.state.get("recommended_posts", {}))
        observed["feed"] = feed
        return ctx.result(metrics={"candidate_count": len(candidates)})

    await engine.run(steps=1)
    checkpoint = _read_checkpoint(tmp_path)

    assert observed["candidate_count"] == 1002
    assert observed["state_recommended_during_step"] == {}
    assert observed["recommended_ids"][0] == "post_0900_old_high_engagement"
    assert checkpoint["environment"]["state"]["recommended_posts"]["viewer"][0] == "post_0900_old_high_engagement"
    assert "post_0900_old_high_engagement" in observed["feed"]
    assert len(observed["recommended_ids"]) == 8


@pytest.mark.asyncio
async def test_recommended_feed_truncates_long_content_and_total_prompt(tmp_path):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_config(
            recommendation={
                "post_count": 8,
                "feed_content_preview_chars": 40,
                "feed_max_chars": 500,
            }
        ),
    )
    observed = {}

    @engine.step(name="truncate_feed")
    async def truncate_feed(ctx):
        long_text = "very long post " * 100
        for idx in range(10):
            _seed_post(
                ctx.env,
                _post(
                    f"long_{idx}",
                    content=f"{long_text} unique {idx}",
                    likes=idx,
                    created_tick=idx,
                ),
            )
        feed = await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        observed["feed"] = feed
        return None

    await engine.run(steps=1)

    assert len(observed["feed"]) <= 560
    assert "very long post very long post very long ..." in observed["feed"]
    assert "推荐流已按 feed_max_chars 截断" in observed["feed"]


@pytest.mark.asyncio
async def test_latest_low_engagement_does_not_dominate(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="recommend")
    async def recommend(ctx):
        _seed_post(
            ctx.env,
            _post("old_high", author_id="author_old", created_tick=0, likes=30, replies=10),
        )
        for idx in range(40):
            _seed_post(ctx.env, _post(f"latest_low_{idx}", created_tick=1000 + idx))
        viewer = ctx.world.get_agent("viewer")
        await ctx.env.get_recommended_feed(viewer, ctx.env)
        observed["recommended_ids"] = list(ctx.env._pending_recommended_posts["viewer"])
        return None

    await engine.run(steps=1)

    assert observed["recommended_ids"][0] == "old_high"
    assert "old_high" in observed["recommended_ids"]
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    trace = next(event for event in events if event.get("event_type") == "social_recommendation_trace")
    score_breakdown = trace["event_data"]["score_breakdown"]
    assert score_breakdown[0]["rank"] == 1
    assert score_breakdown[0]["post_id"] == "old_high"
    assert score_breakdown[0]["engagement_score"] > score_breakdown[1]["engagement_score"]
    assert score_breakdown[0]["engagement_contribution"] > score_breakdown[1]["engagement_contribution"]
    assert score_breakdown[0]["total_score"] > score_breakdown[1]["total_score"]


@pytest.mark.asyncio
async def test_view_counts_flush_after_tick(tmp_path):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_config(recommendation={"post_count": 1}),
    )
    observed = {}

    @engine.step(name="show_feed")
    async def show_feed(ctx):
        _seed_post(ctx.env, _post("post_visible", author_id="author_old", likes=5))
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer_2"), ctx.env)
        observed["view_count_during_step"] = ctx.env._posts_view()["post_visible"].get("view_count", 0)
        return None

    await engine.run(steps=1)

    checkpoint = _read_checkpoint(tmp_path)
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    view_count_state_changes = [
        event
        for event in events
        if event.get("event_type") == "STATE_CHANGE"
        and event.get("path") == ["post_projection", "post_visible", "view_count"]
    ]
    flush_events = [
        event
        for event in events
        if event.get("event_type") == "social_recommendation_state_flushed"
    ]
    trace_events = [
        event
        for event in events
        if event.get("event_type") == "social_recommendation_trace"
    ]
    assert observed["view_count_during_step"] == 0
    assert checkpoint["environment"]["state"]["post_projection"]["post_visible"]["view_count"] == 2
    assert view_count_state_changes == []
    assert len(flush_events) == 1
    assert flush_events[0]["event_data"]["impression_deltas"] == {"post_visible": 2}
    assert len(trace_events) == 2
    assert all(event.get("event") == "social_recommendation_trace" for event in trace_events)
    assert trace_events[0]["event_data"]["raw_candidate_count"] == 1
    assert trace_events[0]["event_data"]["returned_count"] == 1
    assert trace_events[0]["event_data"]["active_pool_count"] == 1
    assert trace_events[0]["event_data"]["record_impression"] is True
    assert isinstance(trace_events[0]["event_data"]["duration_sec"], float)
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    recommendation_summary = summary["events"]["social_recommendations"]
    assert recommendation_summary["trace_count"] == 2
    assert recommendation_summary["flush_count"] == 1
    assert recommendation_summary["unique_agent_count"] == 2
    assert recommendation_summary["raw_candidate_count_max"] == 1
    assert recommendation_summary["active_pool_count_max"] == 1
    assert recommendation_summary["returned_count_total"] == 2
    assert recommendation_summary["impression_delta_total"] == 2
    assert recommendation_summary["recommended_agent_update_count"] == 2
    assert recommendation_summary["state_patch_count"] == 3
    diagnostics = (tmp_path / "diagnostics.md").read_text(encoding="utf-8")
    assert "## Social Recommendation Diagnostics" in diagnostics
    assert "Recommendation traces: 2; agents 2" in diagnostics
    assert "Deferred recommendation flushes: 1; impression delta total 2" in diagnostics


@pytest.mark.asyncio
async def test_trending_posts_action_records_exposure_after_tick(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="trending")
    async def trending(ctx):
        _seed_post(
            ctx.env,
            _post(
                "post_hot",
                author_id="author_old",
                likes=12,
                replies=3,
                content="A compact but highly discussed campus policy update.",
            ),
        )
        actionset = ctx.world.assemble_agent_actionset(ctx.world.get_agent("viewer"))
        observed["trending"] = await actionset.call_action("get_trending_posts")
        observed["view_count_during_step"] = ctx.env._posts_view()["post_hot"].get("view_count", 0)
        return None

    await engine.run(steps=1)
    checkpoint = _read_checkpoint(tmp_path)

    assert "本动作会记录曝光" in observed["trending"]
    assert "帖子 ID: post_hot" in observed["trending"]
    assert "作者用户 ID: author_old" in observed["trending"]
    assert observed["view_count_during_step"] == 0
    assert checkpoint["environment"]["state"]["post_projection"]["post_hot"]["view_count"] == 1


@pytest.mark.asyncio
async def test_recommended_feed_preview_has_no_impression_or_state_side_effect(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="preview_feed")
    async def preview_feed(ctx):
        _seed_post(
            ctx.env,
            _post(
                "post_visible",
                author_id="author_recent",
                created_tick=0,
                likes=10,
            ),
        )
        feed = await ctx.env.preview_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        observed["feed"] = feed
        observed["view_count_during_step"] = ctx.env._posts_view()["post_visible"].get("view_count", 0)
        observed["recommended_posts_during_step"] = dict(ctx.env.state.get("recommended_posts", {}))
        return None

    await engine.run(steps=1)
    checkpoint = _read_checkpoint(tmp_path)

    assert "个性化推荐动态预览" in observed["feed"]
    assert observed["view_count_during_step"] == 0
    assert observed["recommended_posts_during_step"] == {}
    assert checkpoint["environment"]["state"]["post_projection"]["post_visible"]["view_count"] == 0
    assert checkpoint["environment"]["state"]["recommended_posts"] == {}


@pytest.mark.asyncio
async def test_recommended_posts_keeps_all_agents_under_proxy_state(tmp_path):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_config(recommendation={"post_count": 2}),
    )
    observed = {}

    @engine.step(name="multi_feed")
    async def multi_feed(ctx):
        _seed_post(ctx.env, _post("post_visible", author_id="author_old", likes=5))
        _seed_post(ctx.env, _post("post_visible_2", author_id="author_recent", likes=4))
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer_2"), ctx.env)
        observed["recommended_posts"] = {
            agent_id: list(post_ids)
            for agent_id, post_ids in ctx.env._pending_recommended_posts.items()
        }
        observed["state_recommended_during_step"] = dict(ctx.env.state.get("recommended_posts", {}))
        return None

    await engine.run(steps=1)
    checkpoint = _read_checkpoint(tmp_path)
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    recommended_state_changes = [
        event
        for event in events
        if event.get("event_type") == "STATE_CHANGE"
        and event.get("path", [])[:1] == ["recommended_posts"]
        and len(event.get("path", [])) > 1
    ]
    flush_events = [
        event
        for event in events
        if event.get("event_type") == "social_recommendation_state_flushed"
    ]

    assert set(observed["recommended_posts"]) == {"viewer", "viewer_2"}
    assert observed["recommended_posts"]["viewer"]
    assert observed["recommended_posts"]["viewer_2"]
    assert observed["state_recommended_during_step"] == {}
    assert set(checkpoint["environment"]["state"]["recommended_posts"]) == {"viewer", "viewer_2"}
    assert recommended_state_changes == []
    assert len(flush_events) == 1
    assert set(flush_events[0]["event_data"]["recommended_posts"]) == {"viewer", "viewer_2"}
    assert flush_events[0]["event_data"]["state_patches"]


@pytest.mark.asyncio
async def test_notifications_accumulate_without_root_reset(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="likes")
    async def likes(ctx):
        _seed_post(ctx.env, _post("post_target", author_id="author_old", likes=0))
        for liker_id in ["viewer", "viewer_2", "author_recent"]:
            actionset = ctx.world.assemble_agent_actionset(ctx.world.get_agent(liker_id))
            await actionset.call_action(
                "like_post",
                context_provider=lambda: (ctx.world.get_context_stack(), ctx.world.set_context_stack),
                post_id="post_target",
            )
        return None

    await engine.run(steps=1)

    checkpoint = _read_checkpoint(tmp_path)
    observed["notifications"] = checkpoint["environment"]["state"]["notification_facts"]
    notifications = [
        item for item in observed["notifications"] if item["target_agent_id"] == "author_old"
    ]
    assert [item["data"]["interactor_id"] for item in notifications] == ["viewer", "viewer_2", "author_recent"]
    assert len({item["id"] for item in notifications}) == 3


@pytest.mark.asyncio
async def test_recommendation_cache_reused_across_agents(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config(recommendation={"post_count": 1}))
    observed = {}

    @engine.step(name="recommend_twice")
    async def recommend_twice(ctx):
        for idx in range(25):
            _seed_post(ctx.env, _post(f"post_{idx}", created_tick=idx, likes=idx))
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer_2"), ctx.env)
        observed["rebuilds"] = ctx.env._recommendation_cache_rebuild_count
        return None

    await engine.run(steps=1)

    assert observed["rebuilds"] == 1


@pytest.mark.asyncio
async def test_post_embedding_generated_once(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="embed_once")
    async def embed_once(ctx):
        calls = []
        metadata_seen = []

        async def fake_embed(texts, metadata=None):
            calls.append(list(texts))
            metadata_seen.append(dict(metadata or {}))
            return {"result": [[0.1, 0.2, 0.3] for _ in texts], "model": "fake", "dimensions": 3}

        ctx.env._embed_call = fake_embed
        _seed_post(ctx.env, _post("post_once", content="embed me", author_id="author_old"))
        await ctx.env._embed_and_store_post(
            post_id="post_once",
            content="embed me",
            tags=[],
            created_tick=0,
            author_id="author_old",
        )
        await ctx.env._embed_and_store_post(
            post_id="post_once",
            content="embed me",
            tags=[],
            created_tick=0,
            author_id="author_old",
        )
        observed["calls"] = len(calls)
        observed["metadata"] = metadata_seen
        observed["post_state"] = ctx.env._posts_view()["post_once"]
        return None

    await engine.run(steps=1)
    checkpoint = _read_checkpoint(tmp_path)
    checkpoint_post = {
        **checkpoint["environment"]["state"]["post_creation_facts"]["post_once"],
        **checkpoint["environment"]["state"]["post_projection"]["post_once"],
    }

    assert observed["calls"] == 1
    assert observed["metadata"] == [
        {
            "step": 0,
            "step_name": "embed_once",
            "interaction_type": "env_post_embedding",
            "interaction_name": "publish_post",
            "agent_id": "author_old",
            "post_id": "post_once",
        }
    ]
    assert "embedding" not in observed["post_state"]
    assert observed["post_state"]["embedding_ref"] == "post_once"
    assert observed["post_state"]["embedding_model"] == "fake"
    assert observed["post_state"]["embedding_dimensions"] == 3
    assert observed["post_state"]["embedding_indexed"] is True
    assert "embedding" not in checkpoint_post
    assert checkpoint_post["embedding_ref"] == "post_once"


@pytest.mark.asyncio
async def test_publish_post_embeddings_flush_in_one_batch_after_tick(tmp_path):
    class FakeCollection:
        def __init__(self):
            self.upserts = []

        def upsert(self, **kwargs):
            self.upserts.append(kwargs)

    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="publish_batch")
    async def publish_batch(ctx):
        calls = []
        metadata_seen = []
        fake_collection = FakeCollection()

        async def fake_embed(texts, metadata=None):
            calls.append(list(texts))
            metadata_seen.append(dict(metadata or {}))
            return {"result": [[0.1, 0.2, 0.3] for _ in texts], "model": "fake", "dimensions": 3}

        ctx.env._embed_call = fake_embed
        ctx.env._post_collection = fake_collection
        await ctx.env.publish_post(
            context=ctx.world._build_execution_context(caller=ctx.world.get_agent("author_old")),
            content="first batched post",
            tags=["a"],
        )
        await ctx.env.publish_post(
            context=ctx.world._build_execution_context(caller=ctx.world.get_agent("author_recent")),
            content="second batched post",
            tags=["b"],
        )
        observed["calls_during_step"] = len(calls)
        observed["pending_during_step"] = sorted(ctx.env._pending_post_embeddings.keys())
        observed["calls"] = calls
        observed["metadata"] = metadata_seen
        observed["collection"] = fake_collection
        return None

    await engine.run(steps=1)
    checkpoint = _read_checkpoint(tmp_path)
    posts = checkpoint["environment"]["state"]["post_projection"]

    assert observed["calls_during_step"] == 0
    assert observed["pending_during_step"] == ["post_1", "post_2"]
    assert observed["calls"] == [["first batched post\nTags: #a", "second batched post\nTags: #b"]]
    assert observed["metadata"] == [
        {
            "step": 0,
            "step_name": "publish_batch",
            "interaction_type": "env_post_embedding",
            "interaction_name": "publish_post",
            "agent_ids": ["author_old", "author_recent"],
            "post_ids": ["post_1", "post_2"],
        }
    ]
    assert len(observed["collection"].upserts) == 1
    assert observed["collection"].upserts[0]["ids"] == ["post_1", "post_2"]
    assert posts["post_1"]["embedding_ref"] == "post_1"
    assert posts["post_2"]["embedding_ref"] == "post_2"
    assert posts["post_1"]["embedding_model"] == "fake"
    assert posts["post_2"]["embedding_dimensions"] == 3


@pytest.mark.asyncio
async def test_semantic_query_uses_active_pool_not_recent_sample(tmp_path):
    class FakeCollection:
        def __init__(self):
            self.last_n_results = None

        def query(self, *, query_embeddings, n_results, include, where=None):
            self.last_n_results = n_results
            return {"ids": [["old_semantic"]], "distances": [[0.0]]}

    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_config(
            recommendation={
                "use_embedding_similarity": True,
                "chronological_weight": 0.0,
                "engagement_weight": 0.0,
                "similarity_weight": 1.0,
                "network_weight": 0.0,
                "post_count": 3,
            }
        ),
    )
    fake_collection = FakeCollection()
    observed = {}

    @engine.step(name="semantic_recommend")
    async def semantic_recommend(ctx):
        async def fake_embed(texts):
            return {"result": [[0.5, 0.5, 0.5] for _ in texts]}

        ctx.env._embed_call = fake_embed
        ctx.env._post_collection = fake_collection
        _seed_post(
            ctx.env,
            _post(
                "old_semantic",
                author_id="author_old",
                created_tick=0,
                content="Old post about climate risk and trust.",
            ),
        )
        for idx in range(60):
            _seed_post(
                ctx.env,
                _post(
                    f"recent_irrelevant_{idx}",
                    created_tick=1000 + idx,
                    content="Recent unrelated chatter.",
                ),
            )
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        observed["n_results"] = fake_collection.last_n_results
        observed["recommended_ids"] = list(ctx.env._pending_recommended_posts["viewer"])
        return None

    await engine.run(steps=1)

    assert observed["n_results"] >= 61
    assert observed["recommended_ids"][0] == "old_semantic"


@pytest.mark.asyncio
async def test_semantic_query_reuses_identical_preference_text_within_tick(tmp_path):
    class FakeCollection:
        def __init__(self):
            self.query_calls = 0

        def query(self, *, query_embeddings, n_results, include, where=None):
            self.query_calls += 1
            return {
                "ids": [["post_a", "post_b"]],
                "distances": [[0.0, 0.25]],
            }

    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_config(
            recommendation={
                "use_embedding_similarity": True,
                "include_recent_posts_in_query": False,
                "chronological_weight": 0.0,
                "engagement_weight": 0.0,
                "similarity_weight": 1.0,
                "network_weight": 0.0,
                "post_count": 2,
            }
        ),
    )
    fake_collection = FakeCollection()
    observed = {}

    @engine.step(name="semantic_cache")
    async def semantic_cache(ctx):
        embed_calls = []

        async def fake_embed(texts, metadata=None):
            embed_calls.append({"texts": list(texts), "metadata": dict(metadata or {})})
            return {"result": [[0.5, 0.5, 0.5] for _ in texts], "model": "fake", "dimensions": 3}

        ctx.env._embed_call = fake_embed
        ctx.env._post_collection = fake_collection
        ctx.env.graph = None
        _seed_post(ctx.env, _post("post_a", author_id="author_old", likes=5))
        _seed_post(ctx.env, _post("post_b", author_id="author_recent", likes=4))

        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer_2"), ctx.env)

        observed["embed_calls"] = embed_calls
        observed["query_calls"] = fake_collection.query_calls
        return None

    await engine.run(steps=1)

    assert [call["texts"] for call in observed["embed_calls"]] == [["Social feed preference"]]
    assert observed["embed_calls"][0]["metadata"]["agent_id"] == "viewer"
    assert observed["query_calls"] == 1


@pytest.mark.asyncio
async def test_semantic_query_ignores_following_by_default_for_cache_reuse(tmp_path):
    class FakeCollection:
        def __init__(self):
            self.query_calls = 0

        def query(self, *, query_embeddings, n_results, include, where=None):
            self.query_calls += 1
            return {
                "ids": [["post_a", "post_b"]],
                "distances": [[0.0, 0.25]],
            }

    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_config(
            recommendation={
                "use_embedding_similarity": True,
                "include_recent_posts_in_query": False,
                "chronological_weight": 0.0,
                "engagement_weight": 0.0,
                "similarity_weight": 1.0,
                "network_weight": 0.0,
                "post_count": 2,
            }
        ),
    )
    fake_collection = FakeCollection()
    observed = {}

    @engine.step(name="semantic_cache_with_graph")
    async def semantic_cache_with_graph(ctx):
        import networkx as nx

        embed_calls = []

        async def fake_embed(texts, metadata=None):
            embed_calls.append({"texts": list(texts), "metadata": dict(metadata or {})})
            return {"result": [[0.5, 0.5, 0.5] for _ in texts], "model": "fake", "dimensions": 3}

        ctx.env._embed_call = fake_embed
        ctx.env._post_collection = fake_collection
        ctx.env.graph = nx.DiGraph()
        ctx.env.graph.add_edge("viewer", "author_old")
        ctx.env.graph.add_edge("viewer_2", "author_recent")
        _seed_post(ctx.env, _post("post_a", author_id="author_old", likes=5))
        _seed_post(ctx.env, _post("post_b", author_id="author_recent", likes=4))

        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer_2"), ctx.env)

        observed["embed_calls"] = embed_calls
        observed["query_calls"] = fake_collection.query_calls
        return None

    await engine.run(steps=1)

    assert [call["texts"] for call in observed["embed_calls"]] == [["Social feed preference"]]
    assert observed["query_calls"] == 1


@pytest.mark.asyncio
async def test_semantic_query_can_include_following_when_configured(tmp_path):
    class FakeCollection:
        def __init__(self):
            self.query_calls = 0

        def query(self, *, query_embeddings, n_results, include, where=None):
            self.query_calls += 1
            return {
                "ids": [["post_a", "post_b"]],
                "distances": [[0.0, 0.25]],
            }

    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_config(
            recommendation={
                "use_embedding_similarity": True,
                "include_recent_posts_in_query": False,
                "include_following_in_query": True,
                "chronological_weight": 0.0,
                "engagement_weight": 0.0,
                "similarity_weight": 1.0,
                "network_weight": 0.0,
                "post_count": 2,
            }
        ),
    )
    fake_collection = FakeCollection()
    observed = {}

    @engine.step(name="semantic_cache_with_following")
    async def semantic_cache_with_following(ctx):
        import networkx as nx

        embed_calls = []

        async def fake_embed(texts, metadata=None):
            embed_calls.append({"texts": list(texts), "metadata": dict(metadata or {})})
            return {"result": [[0.5, 0.5, 0.5] for _ in texts], "model": "fake", "dimensions": 3}

        ctx.env._embed_call = fake_embed
        ctx.env._post_collection = fake_collection
        ctx.env.graph = nx.DiGraph()
        ctx.env.graph.add_edge("viewer", "author_old")
        ctx.env.graph.add_edge("viewer_2", "author_recent")
        _seed_post(ctx.env, _post("post_a", author_id="author_old", likes=5))
        _seed_post(ctx.env, _post("post_b", author_id="author_recent", likes=4))

        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer"), ctx.env)
        await ctx.env.get_recommended_feed(ctx.world.get_agent("viewer_2"), ctx.env)

        observed["embed_texts"] = [call["texts"][0] for call in embed_calls]
        observed["query_calls"] = fake_collection.query_calls
        return None

    await engine.run(steps=1)

    assert len(observed["embed_texts"]) == 2
    assert any("Following:" in text for text in observed["embed_texts"])
    assert observed["query_calls"] == 2


@pytest.mark.asyncio
async def test_active_pool_prunes_only_after_threshold(tmp_path):
    engine = Society0(
        save_dir=str(tmp_path),
        base_config=_social_config(
            recommendation={
                "full_scan_until": 5,
                "recent_keep_count": 2,
                "top_engagement_keep_count": 1,
                "min_lifetime_ticks": 3,
            }
        ),
    )
    observed = {}

    @engine.step(name="prune_pool")
    async def prune_pool(ctx):
        ctx.world.step = 100
        _seed_post(ctx.env, _post("old_low_0", created_tick=0))
        _seed_post(ctx.env, _post("old_low_1", created_tick=1))
        _seed_post(ctx.env, _post("high_old", created_tick=2, likes=10))
        _seed_post(ctx.env, _post("young_low", created_tick=98))
        _seed_post(ctx.env, _post("recent_1", created_tick=99))
        _seed_post(ctx.env, _post("recent_2", created_tick=100))
        candidates = ctx.env._get_real_posts_only(ctx.world.get_agent("viewer"))
        observed["candidate_ids"] = {post["post_id"] for post in candidates}
        return None

    await engine.run(steps=1)

    assert observed["candidate_ids"] == {"high_old", "young_low", "recent_1", "recent_2"}


@pytest.mark.asyncio
async def test_trending_uses_same_engagement_features(tmp_path):
    engine = Society0(save_dir=str(tmp_path), base_config=_social_config())
    observed = {}

    @engine.step(name="trend")
    async def trend(ctx):
        _seed_post(ctx.env, _post("liked_post", likes=1))
        _seed_post(ctx.env, _post("repost_target"))
        _seed_post(ctx.env, _post("repost_1", reply_to="repost_target", created_tick=1))
        _seed_post(ctx.env, _post("repost_2", reply_to="repost_target", created_tick=2))
        await ctx.rule("update_trending_topics")
        observed["trending"] = list(ctx.env.state["trending_post_ids"])
        return None

    await engine.run(steps=1)

    assert observed["trending"][0] == "repost_target"
