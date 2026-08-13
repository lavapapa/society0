"""Misinformation/trust workflow demo using the Society0 code-step DSL."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from society0 import Society0


RUN_DIR = Path("runs/misinformation_trust_demo")

AGENT_STATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "trust": {
            "type": "number",
            "persistence": {"kind": "replaceable"},
        },
        "exposure": {
            "type": "integer",
            "persistence": {"kind": "replaceable"},
        },
    },
}

ENVIRONMENT_STATE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "misinformation_pressure": {
            "type": "number",
            "persistence": {"kind": "replaceable"},
        },
        "correction_strength": {
            "type": "number",
            "persistence": {"kind": "replaceable"},
        },
    },
}


def build_config():
    return {
        "agent_types": [
            {
                "id": "social_user",
                "archetype": "rule",
                "state_schema": AGENT_STATE_SCHEMA,
            },
        ],
        "agents": [
            {"id": "alice", "type": "social_user", "state": {"trust": 0.72, "exposure": 0}},
            {"id": "bob", "type": "social_user", "state": {"trust": 0.54, "exposure": 0}},
            {"id": "chen", "type": "social_user", "state": {"trust": 0.48, "exposure": 0}},
            {"id": "dina", "type": "social_user", "state": {"trust": 0.66, "exposure": 0}},
        ],
        "environment": {
            "type": "plain",
            "state_schema": ENVIRONMENT_STATE_SCHEMA,
            "state": {
                "misinformation_pressure": 0.12,
                "correction_strength": 0.05,
            },
        },
    }


engine = Society0(save_dir=str(RUN_DIR), base_config=build_config())


@engine.step(name="expose_and_update_trust")
async def expose_and_update_trust(ctx):
    users = ctx.agents.where(type="social_user")
    pressure = ctx.world.environment_data["state"]["misinformation_pressure"]
    correction = ctx.world.environment_data["state"]["correction_strength"]
    rows = []

    for agent_id in users.ids():
        state = ctx.world.agents_data[agent_id]["state"]
        exposure = state["exposure"] + 1
        state["exposure"] = exposure
        drift = pressure * (1 - state["trust"]) - correction * state["trust"]
        state["trust"] = max(0.0, min(1.0, state["trust"] + drift))
        rows.append({"agent_id": agent_id, "trust": round(state["trust"], 4), "exposure": exposure})

    avg_trust = sum(row["trust"] for row in rows) / len(rows)
    return ctx.result(
        metrics={"avg_trust": round(avg_trust, 4)},
        tables={"trust_by_agent": rows},
        notes="Updated trust after one exposure/correction cycle.",
    )


@engine.step(name="adjust_environment_pressure")
async def adjust_environment_pressure(ctx):
    avg_trust = sum(
        data["state"]["trust"]
        for data in ctx.world.agents_data.values()
        if data["type"] == "social_user"
    ) / len(ctx.world.agents_data)
    env_state = ctx.world.environment_data["state"]
    if avg_trust < 0.55:
        env_state["correction_strength"] = min(0.12, env_state["correction_strength"] + 0.01)
    return ctx.result(
        observations={"correction_strength": env_state["correction_strength"]},
        notes="Adjusted correction strength based on aggregate trust.",
    )


async def main():
    await engine.run(steps=12)
    summary_path = RUN_DIR / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print(f"Demo complete: {summary_path}")
    print(json.dumps(summary["world_state_summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
