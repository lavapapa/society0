"""六位昵称用户讨论平台改版传闻，使用内置社交、长期记忆与动作入口。

模型地址与密钥通过 SOCIETY0_LLM_*、SOCIETY0_EMBED_* 环境变量提供。
先运行 python experiment.py --check --run-dir runs/check-001 检查装配，
再运行 python experiment.py --run-dir runs/pilot-001 进行三轮互动。
"""

import argparse
import asyncio
import json
import os
import platform
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from society0.kernel.actors import ActorRecord, actor_plugin
from society0.kernel.drivers import llm_driver_plugin
from society0.kernel.interaction import interaction_plugin
from society0.kernel.llm import LLMPolicy
from society0.kernel.memory import MemoryPolicy
from society0.kernel.models import embedding_plugin, model_plugin
from society0.kernel.plugins import Plugin
from society0.kernel.results import StepResult, results_plugin
from society0.kernel.runner import RunContract, RunPlan, run_plan
from society0.kernel.runtime import Phase, runtime_plugin
from society0.kernel.schedule import SequenceSchedule, activate
from society0.kernel.services import memory_plugin, thread_plugin
from society0.plugins import cognition_plugin, social_plugin, social_source_plugin
from society0.plugins.cognition import SourceBatch, SourceSpec

import config as C

# required_tags 阻止纯文本自然结束，completion_tags 让带标签的动作调用后收尾。
# 两者只保证“有一次带标签的 completed 调用”，不保证事实新增：重复点赞 changed=False 同样满足。
DECISION_POLICY = LLMPolicy(
    max_turns=C.DECISION["max_turns"],
    max_action_calls=C.DECISION["max_action_calls"],
    required_tags=tuple(C.DECISION["required_tags"]),
    completion_tags=tuple(C.DECISION["completion_tags"]),
)
MEMORY_POLICY = MemoryPolicy(
    auto_write=C.MEMORY["auto_write"],
    auto_recall=C.MEMORY["auto_recall"],
    active_tools=C.MEMORY["active_tools"],
)
MEMBERS = tuple(agent["id"] for agent in C.AGENTS)
SOURCE_ID = "social.main.v1"
ENTRIES_ID = "social.entries.v1"


def resource_plugin():
    """共享服务：模型、嵌入、向量客户端。"""
    def install(ctx):
        import chromadb
        client = chromadb.EphemeralClient()
        ctx.on_close(client.close)
        ctx.provide("client", client)
        ctx.provide("models", ctx.require("models", "models"))
        ctx.provide("embeddings", ctx.require("embeddings", "embeddings"))
    return Plugin("resources", ("models", "embeddings"), install)


def schedule_plugin(moments, social_enabled=True):
    def install(ctx):
        if not social_enabled:
            # 社交组未装配时没有轮次可跑：给出空时间线，装配检查仍可完成。
            ctx.provide("schedule", SequenceSchedule((), ()))
            return
        social = ctx.require("social", "mechanism")
        store = ctx.require("storage", "store")

        def counts(view):
            return {
                "posts": view.query("SELECT COUNT(*) FROM social_posts")[0][0],
                "comments": view.query("SELECT COUNT(*) FROM social_replies")[0][0],
                "likes": view.query("SELECT COUNT(*) FROM social_likes")[0][0],
                "follows": view.query("SELECT COUNT(*) FROM social_edges")[0][0],
            }

        async def round_phase(phase):
            tick = phase.moment.time
            if tick == C.SEED_POST["tick"]:
                social.execute(
                    "publish_post", C.SEED_POST["author"], C.SEED_POST["author"],
                    {"content": C.SEED_POST["content"]}, tick,
                )
            outcomes = await activate(phase, MEMBERS)
            for item in outcomes:
                if item.result.status != "completed":
                    raise RuntimeError(f"第 {tick} 轮未完成：{item.actor.id} / {item.result.status}")
            facts = store.read(counts)
            per_actor = store.read(lambda view: view.query(
                "SELECT author, COUNT(*) FROM social_posts GROUP BY author ORDER BY author"))
            return StepResult(
                metrics={**facts, "activated": len(outcomes)},
                tables={
                    "posts_by_actor": [{"actor": row[0], "posts": row[1]} for row in per_actor],
                    "rounds": [{"round": tick, **facts}],
                },
            )

        ctx.provide("schedule", SequenceSchedule(tuple(moments), [Phase("round", round_phase)]))

    return Plugin("schedule", ("social", "storage") if social_enabled else ("storage",), install)


class EntriesPerception:
    """把当前实际可用的社交动作入口与对象引用写成一条说明。

    帖子按创建顺序列前八条，与推荐流独立。停用 social_entries 后不注入该来源。
    """

    def __init__(self, store, domain="social"):
        self.store, self.domain = store, domain

    def _read(self, view):
        posts = view.query(
            "SELECT id, author, created_tick FROM {0}_posts ORDER BY ordinal".format(self.domain))
        members = view.query(
            "SELECT id FROM {0}_members ORDER BY ordinal".format(self.domain))
        return posts, members

    async def __call__(self, session, previous):
        posts, members = self.store.read(self._read)
        actor = session.actor.id
        post_lines = "、".join(
            "{0}（作者 {1}，第 {2} 轮）".format(p[0], p[1], p[2]) for p in posts[:8]) or "暂无"
        peers = [m[0] for m in members if m[0] != actor]
        member_lines = "、".join(peers) or "暂无"

        text = "\n".join([
            "只通过工具行动：不要去找文件路径，也不要问别人确认，直接调用 action_invoke。",
            "本轮结束前要完成一次写入类动作，只读浏览不算完成。",
            "做什么、对谁做，由你按自己的处境和这一轮看到的内容决定；"
            "下面只给调用格式，不给现成的对象，别因为格式示例就都去动同一条帖子或同一个人。",
            "",
            "调用格式（target 只有这两种写法）：",
            '  帖子：action_invoke(name="<动作名>", '
            'target={"namespace": "social", "kind": "posts", "key": "<帖子 id>"}, arguments={...})',
            '  成员：action_invoke(name="<动作名>", '
            'target={"namespace": "social", "kind": "participants", "key": "<成员 id>"}, arguments={...})',
            "",
            "动作名 / 用哪种 target / arguments：",
            "- social.like_post     帖子    arguments 可省略",
            '- social.comment       帖子    {"content": "你想说的话"}',
            '- social.repost        帖子    {"commentary": "你想加的话"}（可省略）',
            '- social.publish_post  你自己  {"content": "你想发的内容"}',
            "- social.follow        其他成员  arguments 可省略",
            "- social.unfollow      其他成员  arguments 可省略",
            "",
            "这一轮真实存在的对象：",
            "帖子：" + post_lines,
            "成员：" + member_lines + "；你自己是 " + actor + "。",
            "从上面挑你真正想回应的帖子和人；同一个对象上可以做的事不止一种，"
            "点赞、评论、转发、关注是不同选择，按你的性格决定。",
            "",
            "若返回 \"action unavailable\"，是 target 写错了（kind 只能是 posts 或 participants，"
            "key 用上面给出的真实 id），不是动作不存在；换成正确的 target 再调一次即可。",
        ])
        signature = [[p[0], p[1], p[2]] for p in posts] + [[m[0] for m in members]]
        if previous is not None and previous.position == signature:
            return SourceBatch([], signature)
        return SourceBatch([{"role": "user", "content": "可操作入口\n" + text}], signature)


def entries_source_plugin(*, name="social.entries", storage=("storage", "store")):
    def install(ctx):
        ctx.provide("entries", EntriesPerception(ctx.require(*storage)))
    return Plugin(name, requires=(storage[0],), install=install)


def build_plan(*, release, model, embedding, moments=C.ROUNDS):
    moments = tuple(moments)
    flags = {name: bool(value.get("enabled")) for name, value in C.PLUGINS.items()}
    social_on = flags["social"]
    articles = []
    plugins = [
        thread_plugin(),
        interaction_plugin(lambda *args: True),
        results_plugin(),
        actor_plugin(
            {"decide": ("decide_driver", "factory")},
            records=[ActorRecord(a["id"], "decide", persona=a["persona"]) for a in C.AGENTS],
        ),
    ]
    # 社交机制：social 及其 10 个叶插件是一组，总开关同时决定整组是否装配。
    # 语义推荐开启时，social_plugin 会多装配一个 social.semantic 叶插件，并要求嵌入与向量客户端；
    # 这里直接接上 resources 提供的真实绑定（embeddings 的 main 端点、chromadb 客户端），
    # 同时保持关闭时不传这两个参数——social_plugin 会拒绝“关闭语义却给了语义资源”的组合。
    if social_on:
        semantic_on = bool(
            C.SOCIAL_CONFIG["social_media"]["recommendation"].get("use_embedding_similarity"))
        plugins.append(social_plugin(
            MEMBERS,
            edges=C.EDGES,
            config=C.SOCIAL_CONFIG,
            content_length_limit=C.SOCIAL_CONFIG["social_media"]["content_length_limit"],
            embedding=("resources", "main") if semantic_on else None,
            vector_client=("resources", "client") if semantic_on else None,
        ))
    # 两条感知来源都依赖社交组：推荐动态读 social.recommendation，入口来源读 social_posts /
    # social_members 两张表。social 关闭时两张表都不存在，所以入口来源也必须一起不装配，
    # 否则激活时会在读表处报错（开关值被忽略，装配结果与页面显示一致：来源只随 social 走）。
    if social_on and flags["social_source"]:
        plugins.append(social_source_plugin())
        articles.append(SourceSpec(SOURCE_ID, ("social.source", "source")))
    if social_on and flags["social_entries"]:
        plugins.append(entries_source_plugin())
        articles.append(SourceSpec(ENTRIES_ID, ("social.entries", "entries")))
    plugins += [
        cognition_plugin(
            sources=tuple(articles),
            environment=C.ENVIRONMENT_TEXT,
            reminders=C.ROUND_TASK,
            name="cognition",
        ),
        model_plugin({"main": model}),
        embedding_plugin({"main": embedding}),
        resource_plugin(),
        memory_plugin(
            client=("resources", "client"),
            embedding=("resources", "embeddings", "main"),
            extraction=("resources", "models", "main"),
            policy=MEMORY_POLICY,
            recall_query=lambda session: C.MEMORY["recall_query"],
            recall_top_k=C.MEMORY["recall_top_k"],
        ),
        llm_driver_plugin(
            provider=("models", "models", "main"),
            input_builder=("cognition", "input_builder"),
            policy=DECISION_POLICY,
            extensions=(("memory", "extension"),),
            name="decide_driver",
        ),
        runtime_plugin(
            actor_service=("actors", "actors"),
            information=("interaction", "information"),
            actions=("interaction", "actions"),
            store=("storage", "store"),
            results=("results", "results"),
            capacity=C.RUNTIME["capacity"],
            max_activations=C.RUNTIME["max_activations"],
        ),
        schedule_plugin(moments, social_on),
    ]

    dependencies = {"python": platform.python_version()}
    for name in ("society0", "apsw", "jsonschema", "python-rapidjson", "backports-zstd",
                 "pydantic-ai-slim", "openai", "httpx2", "chromadb", "pydantic"):
        try:
            dependencies[name] = version(name)
        except PackageNotFoundError:
            dependencies[name] = "not-installed"

    def profile(options):
        return {
            "endpoints": [{k: v for k, v in endpoint.items() if k != "api_key"} for endpoint in options["endpoints"]],
            "request_options": options.get("request_options", {}),
        }

    return RunPlan(plugins, RunContract(
        release=release,
        dependencies=dependencies,
        configuration={
            "study": C.STUDY,
            "rounds": list(C.ROUNDS),
            "agents": [dict(agent) for agent in C.AGENTS],
            "edges": [{"follower": f, "followee": t} for f, t in C.EDGES],
            "seedPost": C.SEED_POST,
            "social": C.SOCIAL_CONFIG,
            "memory": C.MEMORY,
            "plugins": C.PLUGINS,
            "decision": C.DECISION,
            "runtime": C.RUNTIME,
            "models": {"llm": profile(model), "embedding": profile(embedding)},
        },
        time={"moments": list(moments)},
        budgets={
            "decide": asdict(DECISION_POLICY),
            "memory": asdict(MEMORY_POLICY),
            "runtime": dict(C.RUNTIME),
        },
        credential_env=("SOCIETY0_LLM_API_KEY", "SOCIETY0_EMBED_API_KEY"),
    ))


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="只初始化装配，不请求模型")
    args = parser.parse_args()

    # 模型名以版本内 config.MODEL 为准；环境变量只允许与之一致，
    # 避免运行脚本或旧环境变量把别的模型悄悄带进来。
    llm_id = os.environ.get("SOCIETY0_LLM_MODEL") or C.MODEL["llm"]
    if llm_id != C.MODEL["llm"]:
        raise SystemExit(
            "模型不一致：本版本 config.MODEL['llm'] = {0}，环境变量为 {1}".format(C.MODEL["llm"], llm_id))
    model = {
        "endpoints": [{
            "id": "main",
            "model": llm_id,
            "base_url": os.environ["SOCIETY0_LLM_BASE_URL"],
            "api_key": os.environ.get("SOCIETY0_LLM_API_KEY", "check-only"),
            "concurrency": 2,
            "timeout": 60,
        }],
        "request_options": json.loads(os.environ.get("SOCIETY0_LLM_OPTIONS", "{}")),
    }
    embedding = {
        "endpoints": [{
            "id": "main",
            "model": C.MODEL["embedding"],
            "base_url": os.environ["SOCIETY0_EMBED_BASE_URL"],
            "api_key": os.environ.get("SOCIETY0_EMBED_API_KEY", "check-only"),
            "concurrency": 2,
            "timeout": 60,
            "send_dimensions": False,
        }],
        "dimensions": int(os.environ.get("SOCIETY0_EMBED_DIMENSIONS", str(C.MODEL["embedding_dimensions"]))),
    }
    plan = build_plan(
        release={"commit": os.environ.get("SOCIETY0_RELEASE", "uncommitted")},
        model=model,
        embedding=embedding,
        moments=() if args.check else C.ROUNDS,
    )
    print(json.dumps(await run_plan(args.run_dir, plan), ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
