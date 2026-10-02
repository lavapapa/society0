"""两轮 LLM Agent 起步示例：查看消息、保存经验、测量判断。"""

import argparse
import asyncio
import copy
import json
import os
import shutil
from pathlib import Path

from pydantic import BaseModel, Field
from society0 import EmbedModel, LLMModel, Society0


CONFIG = {
    "agent_types": [{
        "id": "reader", "archetype": "llm",
        "state_schema": {
            "type": "object", "additionalProperties": False,
            "properties": {"attention": {
                "type": "string", "persistence": {"kind": "replaceable"},
            }},
        },
    }],
    "agents": [{
        "id": "alice", "type": "reader", "persona": "关注本地交通消息的通勤者。",
        "state": {"attention": "正常"}, "properties": {"cohort": "pilot"},
    }],
    "environment": {
        "type": "plain",
        "state": {
            "message": "某个本地账号称下月地铁末班车将提前；尚无官方通知。",
            "detail_views": [],
        },
        "state_schema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "message": {"type": "string", "persistence": {"kind": "replaceable"}},
                "detail_views": {
                    "type": "array", "items": {"type": "string"},
                    "persistence": {"kind": "append_only_list"},
                },
            },
        },
    },
}


class CredibilitySurvey(BaseModel):
    credibility: int = Field(ge=1, le=7, description="1 完全不信，7 完全相信")
    reason: str = Field(description="一句话解释判断依据")


def require_success(batch, phase):
    if batch.error_count:
        raise RuntimeError(f"{phase}失败：{batch.error_samples(limit=3)}")


def build_engine(run_dir, llm, embed):
    engine = Society0(save_dir=str(run_dir), base_config=copy.deepcopy(CONFIG), llm=llm, embed=embed)

    @engine.registry.env.fov(desc="信息流中的消息摘要。")
    def message_preview(agent, env):
        return env.state["message"]

    @engine.registry.env.action(desc="查看消息详情与来源，记录本次查看。", tags=["read"])
    def view_details(agent, env):
        env.state["detail_views"].append(agent.id)
        return {"message": env.state["message"], "source": "本地账号，未见官方证实"}

    # 每个 tick 都执行全部注册步骤；用分支表达前后两轮。
    @engine.step(name="exposure_then_measurement")
    async def protocol(ctx):
        readers = ctx.agents.where(type="reader")
        if ctx.step == 0:
            threads = {
                agent_id: ctx.log.open_agent_thread(
                    agent_id=agent_id, checkpoint_step=ctx.step + 1,
                    scope={"kind": "exposure", "tick": ctx.step},
                )
                for agent_id in readers.ids()
            }
            browsing = await readers.instruct(
                "查看消息详情，再说明你目前怎样理解这则消息。",
                fovs=["message_preview"], actions=["view_details"],
                required_actions=["view_details"], retrieve_memory=True,
                thread_ids_by_agent=threads, max_turns=3, max_tokens=512,
                temperature=0, name="browse",
            )
            require_success(browsing, "浏览")
            memories = await readers.extract_thread_memories(
                threads, timestamp=ctx.step,
                idempotency_key=f"exposure:{ctx.step}", name="remember_exposure",
            )
            require_success(memories, "保存本轮经验")
            return ctx.result(tables={"browsing": browsing.table(), "memory": memories.table()})

        survey = await readers.interview(
            "结合当前呈现的消息和此前经验，评价这则消息的可信度，给出 1–7 分和一句理由。",
            fovs=["message_preview"], output=CredibilitySurvey,
            retrieve_memory=True, memory_top_k=3,
            max_turns=2, max_tokens=512, temperature=0, name="credibility_survey",
        )
        require_success(survey, "测量")
        return ctx.result(
            metrics={"mean_credibility": survey.mean("credibility")},
            tables={"survey": survey.table()},
        )

    return engine


async def main():
    parser = argparse.ArgumentParser(description="两轮 Society0 LLM Agent 起步实验")
    parser.add_argument("--run-dir", required=True, type=Path, help="本次新建的产物目录")
    parser.add_argument("--check", action="store_true", help="初始化配置并生成检查产物，不执行实验轮次")
    args = parser.parse_args()
    if args.run_dir.exists():
        parser.error(f"目录已存在，请为本次检查或试运行选择新目录：{args.run_dir}")
    # 由 AI 将本地模型配置文件加载到这些环境变量；程序不输出凭据。
    llm = LLMModel.openai_compatible(
        model=os.environ["SOCIETY0_LLM_MODEL"], base_url=os.environ["SOCIETY0_LLM_BASE_URL"],
        api_key=os.environ["SOCIETY0_LLM_API_KEY"], concurrency=1, timeout=60,
    )
    embed = EmbedModel.openai_compatible(
        model=os.environ["SOCIETY0_EMBED_MODEL"], base_url=os.environ["SOCIETY0_EMBED_BASE_URL"],
        api_key=os.environ["SOCIETY0_EMBED_API_KEY"],
        dimensions=int(os.environ["SOCIETY0_EMBED_DIMENSIONS"]),
        send_dimensions=False, concurrency=1, timeout=60,
    )
    engine = build_engine(args.run_dir, llm, embed)
    shutil.copyfile(__file__, args.run_dir / "experiment-source.py")
    (args.run_dir / "config-used.json").write_text(
        json.dumps({"config": CONFIG, "models": {
            "llm": llm.model, "embedding": embed.model,
            "embedding_dimensions": embed.dimensions, "concurrency": 1,
        }}, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    await engine.run(steps=0 if args.check else 2)
    summary = json.loads((args.run_dir / "summary.json").read_text(encoding="utf-8"))
    print(json.dumps({"failed": summary["failed"], "steps_completed": summary["steps_completed"],
                      "run_dir": str(args.run_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    asyncio.run(main())
