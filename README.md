# Society0

Society0 is the simulation engine core for [ICLabSZ/Society_Zero_Universe](https://github.com/ICLabSZ/Society_Zero_Universe), designed to be used as a standalone engine for creating, running, and analyzing social simulation experiments.

The intended workflow is agent-assisted: researchers can use their own coding agents, such as Codex, Claude Code, Gemini, CodeWhale, or similar tools, to create experiments, configure models, run simulations, inspect outputs, and draft analysis. The engine provides a general abstraction for agents, environments, memory, model providers, run state, and outputs, so different kinds of social simulation can be built on the same core.

## For Agent

You can copy this into your coding agent:

```text
Open https://github.com/lavapapa/society0/blob/main/README.md, install the Society0 skill, configure the environment, and guide me in stages to understand Society0, design my own simulation experiment, run it, and interpret the outputs.
```

Agents reading this repository directly should start from [skill/SKILL.md](skill/SKILL.md).

## Requirements

- Python `>=3.12`.
- A coding agent that can read and edit a local repository, such as Codex, Claude Code, Gemini, CodeWhale, or another capable coding assistant.
- An LLM provider, such as OpenAI, Ollama, Grok, Qwen, Claude-compatible routes, OpenRouter, SiliconFlow, or another OpenAI-compatible endpoint.
- An embedding model. The engine does not require a specific model size.

## Quick Start

```bash
cd society0core
pip install -e .
```

```python
import asyncio
from society0 import Society0

config = {
    "agent_types": [{"id": "reader", "archetype": "rule"}],
    "agents": [
        {"id": "alice", "type": "reader", "state": {"trust": 0.45}},
        {"id": "bob", "type": "reader", "state": {"trust": 0.70}},
    ],
    "environment": {"type": "plain", "state": {"topic": "misinformation"}},
}

engine = Society0(save_dir="runs/quickstart", base_config=config)


@engine.step(name="measure_trust")
async def measure_trust(ctx):
    ids = ctx.agents.where(type="reader").ids()
    rows = [
        {"agent_id": agent_id, "trust": ctx.world.agents_data[agent_id]["state"]["trust"]}
        for agent_id in ids
    ]
    return ctx.result(
        metrics={"avg_trust": sum(row["trust"] for row in rows) / len(rows)},
        tables={"trust": rows},
    )


asyncio.run(engine.run(steps=3))
```

Outputs are written under the run directory:

```text
steps.jsonl
metrics.jsonl
events.jsonl
summary.json
diagnostics.md
checkpoints/
chroma_store/
```

## LLM Agents

LLM-agent experiments require both an LLM provider and an embedding provider:

```python
from society0 import EmbedModel, LLMModel, Society0

llm = LLMModel.openai_compatible(
    model="your-chat-model",
    base_url="https://your-provider/v1",
    api_key="...",
    concurrency=5,
)

embed = EmbedModel.ollama(
    model="nomic-embed-text",
    base_url="http://localhost:11434",
    concurrency=5,
)

engine = Society0(save_dir="runs/demo", base_config=config, llm=llm, embed=embed)
```

Use `instruct(...)` for behavior/action rounds and `interview(...)` for survey-style measurement.
For LLM agents, `memory=True` retrieves memory and saves extractive memory by default.
If your provider gives a known concurrent request limit, use that value; otherwise keep the default 5.

## External environments

Experiment packages can inject an `Environment` subclass at the Society0 composition root without mutating the built-in environment registry:

```python
from society0 import Environment, Society0


class MyEnvironment(Environment):
    pass


engine = Society0(
    save_dir="runs/custom-env",
    base_config=config,
    environment_factory=MyEnvironment,
)
```

The factory receives the current `World` and must return an `Environment`. Society0 still initializes the environment and registers its decorated FoV and Action capabilities.

## Contributing

Society0 welcomes contributions from social science researchers. If you or your agent creates a useful environment, finds a bug, or has an experiment-driven feature request, ask your coding agent to help open an issue or prepare a focused pull request.

Run the deterministic test suite from the repository root with one command:

```bash
python -m pytest
```

Live LLM and embedding tests remain opt-in because they require maintainer-provided endpoint configuration. Their command and environment variables are documented in [skill/references/debugging.md](skill/references/debugging.md).
