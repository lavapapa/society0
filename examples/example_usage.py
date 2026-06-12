"""Minimal code-driven Society0 example."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from society0 import Society0


CONFIG = {
    "agent_types": [{"id": "participant", "archetype": "rule"}],
    "agents": [
        {"id": "alice", "type": "participant", "state": {"trust": 0.45, "exposures": 0}},
        {"id": "bob", "type": "participant", "state": {"trust": 0.7, "exposures": 0}},
        {"id": "carol", "type": "participant", "state": {"trust": 0.55, "exposures": 0}},
    ],
    "environment": {
        "type": "plain",
        "state": {"misinformation_pressure": 0.04, "correction_strength": 0.02},
    },
}


async def main() -> None:
    run_dir = Path("runs/basic_example")
    engine = Society0(save_dir=str(run_dir), base_config=CONFIG)

    @engine.step(name="daily_trust_update")
    async def daily_trust_update(ctx):
        pressure = ctx.world.environment_data["state"]["misinformation_pressure"]
        correction = ctx.world.environment_data["state"]["correction_strength"]
        rows = []

        for agent_id in ctx.agents.where(type="participant").ids():
            state = ctx.world.agents_data[agent_id]["state"]
            state["exposures"] += 1
            state["trust"] = round(max(0.0, min(1.0, state["trust"] + pressure - correction)), 4)
            rows.append({"agent_id": agent_id, "trust": state["trust"], "exposures": state["exposures"]})

        avg_trust = sum(row["trust"] for row in rows) / len(rows)
        return ctx.result(
            metrics={"avg_trust": round(avg_trust, 4)},
            tables={"participants": rows},
            notes="Updated participant trust after one media exposure tick.",
        )

    await engine.run(steps=5)

    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    print(f"Example complete: {summary['final_step']} ticks -> {run_dir}")


if __name__ == "__main__":
    asyncio.run(main())
