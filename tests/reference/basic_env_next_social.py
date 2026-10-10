"""与固定旧脚本相同动作的 Next 社交独立进程适配。"""
import argparse
import asyncio
import json
from pathlib import Path

from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.composition import compose
from society0.kernel.interaction import interaction_plugin, InteractionScope, Moment, Ref
from society0.plugins.social import social_plugin

MEMBERS = list("abcd")
CONFIG = {"distribution": {"type": "random", "params": {"connection_probability": 0}},
    "is_directed": True, "social_media": {"content_length_limit": -1, "recommendation": {
        "use_embedding_similarity": False, "chronological_weight": 0.05,
        "engagement_weight": 1.0, "network_weight": 0.0, "similarity_weight": 0.0,
        "post_count": 8, "candidate_count": 20, "full_scan_until": 1,
        "recent_keep_count": 1, "top_engagement_keep_count": 1, "min_lifetime_ticks": 0}}}


async def record_social(directory, source=None, start=0, stop=3):
    plugins = [actor_plugin({"rule": lambda r: None}, records=[ActorRecord(a, "rule") for a in MEMBERS]),
        interaction_plugin(lambda *a: True), social_plugin(MEMBERS, edges=[], config=CONFIG)]
    result = {"actions": [], "steps": []}
    async with compose(directory, plugins, source=source) as host:
        env = host.service("social", "mechanism")
        actions = host.service("interaction", "actions")
        def snapshot():
            identifiers = env.store.read(lambda r: [row[0] for row in r.query('SELECT id FROM social_posts ORDER BY ordinal')])
            return {"posts": {p: env.post_details(p) for p in identifiers},
                "notifications": {a: env.notifications(a, include_consumed=True) for a in MEMBERS},
                "edges": [[a, b] for a in MEMBERS for b in env.profile(a)["following"]]}
        async def action(actor, name, **arguments):
            mapped = dict(arguments)
            if name in ("follow", "unfollow"):
                target = Ref("social", "participants", mapped.pop("target_agent_id"))
            elif name in ("like_post", "comment", "repost"):
                target = Ref("social", "posts", mapped.pop("post_id"))
            else:
                target = Ref("social", "participants", actor)
            value = await actions.invoke(InteractionScope(actor, Moment(tick, "oracle")), "social." + name, target, mapped)
            assert value.status == "completed", (name, value)
            result["actions"].append({"tick": tick, "actor": actor, "name": name,
                "arguments": arguments, "result": value.value, "result_status": value.status, "state": snapshot()})
            return value
        for tick in range(start, stop):
            if tick == 0:
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
                result["intervention"] = env.intervene(tick, target_hashtag="#topic", intervention_rate=1.0,
                                                       tag_to_apply="flagged", draw=lambda: 0.5)
            else:
                await action("b", "publish_post", content="最新冷帖 原文", tags=[])
            before = dict(env.exposure._impressions)
            await env.recommended_feed("d", tick, record_impressions=False)
            env.trending(tick)
            assert before == env.exposure._impressions
            ranked = env.rank("d", tick)
            await env.recommended_feed("d", tick)
            await action("d", "get_trending_posts")
            result["steps"].append({"tick": tick, **snapshot(),
                "ranking": [{"post_id": p["post_id"], "_recommendation_score": p["_recommendation_score"]} for p in ranked],
                "active_pool": [p["post_id"] for p in env.active_pool(tick)],
                "trending_ids": [p["post_id"] for p in env.trending(tick)],
                "pending_exposures": dict(env.exposure._impressions)})
            await env.after_tick()
            host.service("storage", "store").complete(tick + 1)
        result["final"] = snapshot()
        result["recommended"] = {a: env.recommended_ids(a) for a in MEMBERS if env.recommended_ids(a)}
    return result


async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--run", type=Path, required=True)
    p.add_argument("--source", type=Path)
    p.add_argument("--start", type=int, default=0)
    p.add_argument("--stop", type=int, default=3)
    args = p.parse_args()
    result = await record_social(args.run, args.source, args.start, args.stop)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
