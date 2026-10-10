"""运行固定 v4.1.14 的实际引擎；父进程负责导出源码和隔离 PYTHONPATH。"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import random

SOURCE_COMMIT = "4ff2df74668931aef12b1a4951fab1d97d6a0981"
MEMBERS = ["a", "b", "c", "d"]


def plain(value):
    if hasattr(value, "items"):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)) or type(value).__name__ == "ListProxy":
        return [plain(v) for v in value]
    return value


def message(value):
    # 唯一删除字段：旧消息的墙钟 timestamp；轮次与先后次序保留。
    return {k: plain(v) for k, v in value.items() if k != "timestamp"}


async def record(kind, directory, *, source=None, steps=3):
    from society0 import Society0

    random.seed(17)
    config = {"group_size": 4, "message_persistence": True} if kind == "round_robin_conversation" else {}
    if kind == "social_network":
        config = {
            "distribution": {"type": "random", "params": {"connection_probability": 0}},
            "is_directed": True,
            "social_media": {"content_length_limit": -1, "recommendation": {
                "use_embedding_similarity": False, "chronological_weight": 0.05,
                "engagement_weight": 1.0, "network_weight": 0.0, "similarity_weight": 0.0,
                "post_count": 8, "candidate_count": 20, "full_scan_until": 1,
                "recent_keep_count": 1, "top_engagement_keep_count": 1, "min_lifetime_ticks": 0,
            }},
        }
    engine = Society0(save_dir=str(directory), checkpoint_every=1,
                      source_run=str(source) if source else None,
                      source_step=1 if source else None, base_config={
        "agent_types": [{"id": "rule_actor", "archetype": "rule", "state_schema": {
            "type": "object", "properties": {"conversation_marker": {
                "type": "string", "persistence": {"kind": "replaceable"}},
                "opinion": {"type": "string", "persistence": {"kind": "replaceable"}},
                "confidence": {"type": "integer", "persistence": {"kind": "replaceable"}}},
                "additionalProperties": False}}],
        "agents": [{"id": a, "type": "rule_actor",
                    "persona": f"{a}：谨慎的讨论参与者，保留完整个人设定。" if kind == "round_robin_conversation" else "",
                    "state": {"opinion": f"{a}的独立判断", "confidence": i + 1} if kind == "round_robin_conversation" else {}}
                   for i, a in enumerate(MEMBERS)],
        "environment": {"type": kind, "config": config, "state": {}},
    })
    result = {"actions": [], "steps": []}
    world_ref = []

    @engine.step(name="fixed_oracle_script")
    async def execute(ctx):
        world_ref[:] = [ctx.world]
        env, world = ctx.env, ctx.world
        tick = world.step
        async def action(actor, name, **arguments):
            value = await world.assemble_agent_actionset(world.get_agent(actor)).call_action(name, **arguments)
            entry = {"tick": tick, "actor": actor, "name": name,
                     "arguments": arguments, "result": plain(value)}
            if kind == "social_network":
                entry["state"] = {"posts": plain(env._posts_view()),
                    "notifications": plain(env._notification_view()),
                    "edges": [list(e) for e in env.graph.edges()]}
            result["actions"].append(entry)
            return value
        if kind == "plain":
            result["steps"].append({"tick": tick, "state": plain(env.state),
                                    "actors": [world.get_agent(a).id for a in MEMBERS]})
        elif kind == "round_robin_conversation":
            if tick:
                await env.advance_round_robin(env)
            pairing = await env.advance_round_robin_with_pairing(env, tick + 1)
            for actor in MEMBERS:
                await action(actor, "send_message_to_partner", content=f"r{tick + 1}:{actor}:私信全文")
            await action("a", "broadcast_to_group", content=f"r{tick + 1}:广播全文")
            markers = []
            for actor in MEMBERS:
                markers.append(await env.mark_conversation_participant(world.get_agent(actor), env, marker=f"ready-{tick + 1}"))
            result["steps"].append({
                "round": tick + 1, "pairing": plain(pairing),
                "messages": [message(m) for m in env.state["message_facts"]],
                "inbox": {a: [message(m) for m in env.state["active_messages"][a]] for a in MEMBERS},
                "pairing_status": {a: plain(env.get_agent_pairing_status(a)) for a in MEMBERS},
                "markers": plain(markers),
                "actor_records": {a: {"persona": world.get_agent(a).get_raw_data()["persona"],
                                      "state": plain(world.get_agent(a).state)} for a in MEMBERS},
                "conversation_fov": {a: await env.get_conversation_fov(world.get_agent(a), env) for a in MEMBERS},
                "group_fov": {a: await env.get_group_fov(world.get_agent(a), env) for a in MEMBERS},
            })
        else:
            if tick == 0:
                # 从空有向图通过实际 follow 动作建立固定图。
                for actor, target in [("a", "b"), ("b", "c"), ("c", "a"), ("d", "a")]:
                    await action(actor, "follow", target_agent_id=target)
                await action("a", "publish_post", content="旧热帖 #topic 原文", tags=["topic"])
                await action("b", "publish_post", content="次热帖 原文", tags=[])
                for actor in ["b", "c", "d"]:
                    await action(actor, "like_post", post_id="post_1")
                await action("c", "like_post", post_id="post_2")
                await action("b", "comment", post_id="post_1", content="评论全文")
                await action("d", "repost", post_id="post_1", commentary="转发全文")
            elif tick == 1:
                await action("c", "publish_post", content="新冷帖 原文", tags=[])
                await action("d", "unfollow", target_agent_id="a")
                result["intervention"] = plain(await env.test_intervention_rule(world, "#topic", 1.0, "flagged"))
            else:
                await action("b", "publish_post", content="最新冷帖 原文", tags=[])
            viewer = world.get_agent("d")
            pending_before = dict(env._pending_impressions)
            preview = await env.preview_recommended_feed(viewer, env)
            trending_preview = await env.get_trending_feed(viewer, env)
            assert pending_before == env._pending_impressions, "旧预览意外增加曝光"
            ranked = await env._rank_posts_with_similarity(viewer, env._get_real_posts_only(viewer))
            feed = await env.recommended_feed(viewer, env)
            trending = await action("d", "get_trending_posts")
            result["steps"].append({
                "tick": tick, "edges": [list(e) for e in env.graph.edges()],
                "posts": plain(env._posts_view()),
                "notifications": plain(env._notification_view()),
                "ranking": [{k: plain(v) for k, v in p.items() if k.startswith("_") or k == "post_id"} for p in ranked],
                "active_pool": list(env._get_recommendation_cache()["active_pool_ids"]),
                "trending_ids": [p["post_id"] for p in env._get_top_engagement_posts(None, limit=2)],
                "pending_exposures": dict(env._pending_impressions),
                "preview": preview, "feed": feed, "trending_preview": trending_preview, "trending": trending,
            })
        return None

    if source:
        await engine.restore(source, step=1)
    await engine.run(steps=steps)
    world = world_ref[0]
    result["final_state"] = plain(world.environment_data["state"])
    if kind == "round_robin_conversation":
        result["final_state"]["message_facts"] = [message(m) for m in result["final_state"]["message_facts"]]
        result["final_state"]["active_messages"] = {a: [message(m) for m in ms] for a, ms in result["final_state"]["active_messages"].items()}
        for entry in result["actions"]:
            if isinstance(entry["result"], dict) and "message_data" in entry["result"]:
                entry["result"]["message_data"] = message(entry["result"]["message_data"])
    return result


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--steps", type=int, default=3)
    args = parser.parse_args()
    import society0
    from importlib.metadata import version, PackageNotFoundError
    import sys
    def installed_version(name):
        try:
            return version(name)
        except PackageNotFoundError:
            return None  # 未使用的旧模型后端可以没有安装；如实际调用仍会正常报错。
    result = {"source_commit": SOURCE_COMMIT, "loaded_package": society0.__file__,
              "python": sys.version, "dependencies": {name: installed_version(name) for name in (
                  "pydantic", "networkx", "sortedcontainers", "jsonschema", "json-repair",
                  "chromadb", "ollama", "openai", "PyYAML", "jmespath", "tenacity")},
              "protocol": "basic-env-v4114-1", "scenarios": {}}
    for kind in ["plain", "round_robin_conversation", "social_network"]:
        result["scenarios"][kind] = await record(kind, args.runs / kind,
            source=args.source / kind if args.source else None, steps=args.steps)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
