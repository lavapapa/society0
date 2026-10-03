# Society0

Society0 is the simulation engine core for [ICLabSZ/Society_Zero_Universe](https://github.com/ICLabSZ/Society_Zero_Universe), designed to be used as a standalone engine for creating, running, and analyzing social simulation experiments.

The package version is defined by `project.version` in [pyproject.toml](pyproject.toml). The long-term architecture and responsibility boundaries are documented in [PROJECT.md](PROJECT.md); release-specific changes are documented under `docs/release-*.md`.

The intended workflow is agent-assisted: researchers can use their own coding agents, such as Codex, Claude Code, Gemini, CodeWhale, or similar tools, to create experiments, configure models, run simulations, inspect outputs, and draft analysis. The engine provides a general abstraction for agents, environments, memory, model providers, run state, and outputs, so different kinds of social simulation can be built on the same core.

## For Agent

Install the [`skill/`](skill/) directory as a skill using your coding agent's instructions; the exact steps depend on the agent. This prompt starts the first-use conversation. The `pip install -e .` command in Quick Start installs the separate Python simulation engine, not the skill.

After installing the Society0 skill in your coding agent, copy this prompt to choose how you want to begin:

```text
I am new to Society0. Help me choose how to start, and guide me in plain language.
```

Agents reading this repository directly should start from [skill/SKILL.md](skill/SKILL.md).

For a screenshot-based, researcher-facing walkthrough, see [在 WorkBuddy 中开始 Society0 研究](docs/workbuddy-walkthrough.md). It covers installation through an ordinary conversation, configuration review, proposed changes, a small experiment, saved results, and analysis. The accompanying [validation record](docs/validation/workbuddy-2026-10-03.md) distinguishes observed successes from remaining verification work.

## Requirements

- Python `>=3.12`.
- A coding agent that can read and edit a local repository, such as Codex, Claude Code, Gemini, CodeWhale, or another capable coding assistant.
- For LLM-Agent experiments, an LLM provider and an embedding model. The engine does not require a specific embedding-model size. Rule-based experiments do not need model endpoints.

## Quick Start

This example uses rule-based agents to show the engine structure without model endpoints. For a study where LLM Agents make decisions, continue to [LLM Agents](#llm-agents).

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

## Runtime observation

Use the read-only observation service to inspect a running or completed experiment.
The [runtime observation guide](docs/runtime-observation.md) includes the Python API,
CLI and HTTP requests, checkpoint visibility, paging, and local index recovery.

## Step-local state and recovery

Every executing step owns a `StepRuntimeScope`. Environments can access it through
`env.step_runtime`, and code steps through `ctx.runtime_scope`. Use it for cursors,
deduplication sets, and derived indexes that must disappear when the step succeeds,
fails, or is restored. The scope is never serialized into the World checkpoint.

A complete checkpoint is Society0's recovery boundary. If a step raises an unhandled
exception, Society0 writes a non-recoverable diagnostic snapshot with a `StepFailure`
summary and leaves the previous complete marker unchanged. Runners should create a new
engine and resolve that checkpoint with
`PersistenceManager.resolve_last_complete_from(source_run)`; they must not continue the
failed in-memory engine.

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
Memory retrieval is enabled by default. Durable memory writes are explicit: open an Agent
Thread, pass its ID to the interaction, then call `extract_thread_memories(...)` after the
interaction. This keeps the complete Thread, the extraction turn, and the committed memory
receipt in one recoverable sequence.
If your provider gives a known concurrent request limit, use that value; otherwise keep the default 5.

Some OpenAI-compatible embedding models use a fixed output size and reject the optional
`dimensions` request field. Declare those models with `send_dimensions=False` while keeping
`dimensions` set to the vector size Society0 should validate and report.

Some OpenAI-compatible reasoning endpoints accept tools but reject `required` or a named
`tool_choice`. For those endpoints, set `tool_choice_policy="auto_restrict"`. Society0 then
narrows a named-tool request to that single tool and sends `tool_choice="auto"`, while its
local required-action checks still decide whether the round may commit. Leave the default
`"native"` policy for providers that implement the standard OpenAI tool-choice modes.

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
uv run --locked pytest -q
```

Live LLM and embedding tests remain opt-in because they require maintainer-provided endpoint configuration. Their command and environment variables are documented in [skill/references/debugging.md](skill/references/debugging.md).
