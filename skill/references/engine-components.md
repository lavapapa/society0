# Society0 Engine Components

This reference summarizes the current code-driven Society0 core. It uses the uploaded paradigm document as background, but corrects it against the current source: the recommended path is `Society0 + CodeSchedule + step(ctx)`, while the older YAML/Step Flow scheduler is legacy.

## Public Facade

`Society0` is the recommended runtime facade. It loads config, initializes a `World`, injects model managers and persistence, runs code steps, writes JSONL outputs, and saves checkpoints.

Use:

```python
from society0 import Society0, LLMModel, EmbedModel
```

Do not use old public examples based on `Experiment` or `simengine`.

## World

`World` is the unified simulation state container. It owns:

- `agents_data`: agent type, archetype, persona, state, properties, model id.
- `environment_data`: environment type, config, schema, state, globals.
- event logger and state-change machinery.
- FoV lookup and caching.
- `instruct_agent(...)` and `interview_agent(...)` bridges into LLM cognition.

`ctx.world` is an escape hatch. Prefer higher-level DSL methods when possible, but direct state access is acceptable for first experiments and rule baselines.

## Agents

Agents are declared through config, not through user-facing Python classes in normal use.

Key fields:

- `id`: unique agent id.
- `type`: links to an `agent_types` entry.
- `archetype`: usually `"llm"` or `"rule"`.
- `persona`: stable natural-language identity for LLM agents.
- `state`: mutable structured state.
- `properties`: additional metadata.
- `model`: optional model id for future routing.

Use LLM agents for language-rich phenomena. Use rule agents for baselines and deterministic mechanisms.

For details on persona, state, properties, model routing, memory, and reasoning stages, read `agent-design.md`.

## Environment

`Environment` is a proxy-backed state and capability layer. It provides:

- environment state through `env.state`.
- access to other agents through environment methods.
- environment-provided actions through capability decorators.
- optional embedding/vector handles injected by the engine.
- snapshots for persistence.

Built-in environments currently include:

- `plain`: minimal environment for teaching, smoke tests, surveys, and custom code steps.
- `social_network`: social graph, posts, likes, replies, voting, feed/recommendation logic, and FoVs.
- `round_robin_conversation`: pairing and message state for rotation-style conversations.

For experiment design, treat the environment as the first-class research object. It defines visibility, affordances, records, and constraints. For built-in env details and extension patterns, read `environment-design.md`.

## FoV

FoV means Field of View: the part of the world an agent sees for an interaction. FoVs are environment capabilities and can be passed to `instruct` or `interview`.

In the current code, FoV functions are registered in the function registry and resolved by `World.instruct_agent(...)` / `World.interview_agent(...)`. FoV results are formatted into the agent prompt.

Treat FoV as a research object: recommender exposure, social visibility, feed ordering, local context, and institutional constraints can all be encoded as FoV logic.

## Actions And Capabilities

Environment actions are exposed as tool-like capabilities to LLM agents. The current implementation discovers capability metadata from decorators and routes available actions into agent cognition.

In normal code-driven experiments, users usually call:

```python
await group.instruct(..., actions=["environment"])
```

This filters what the agent may do. For early prototypes, `actions=None` exposes available actions; narrow later with `actions=["environment"]`, `actions=["memory"]`, or exact action names. `interview(...)` does not expose ordinary actions and should be used for measurement.

## Logic: Rule And Behavior

In Society0 skill guidance, "logic" means deterministic Python logic that is not an LLM free-form response:

- `rule`: environment-level or system-level update.
- `behavior`: agent-level deterministic behavior.

Both can come from two sources:

- env-provided logic: built into an environment as capabilities, such as round-robin pairing rules or environment-specific participant behaviors.
- experiment-specific logic: written by the user or their coding agent for one study, then registered on the engine.

Use `ctx.capabilities` to discover available FoVs, actions, rules, and behaviors in a code step. Use `ctx.rule(...)`, `ctx.agents.where(...).behavior(...)`, or `ctx.behavior(...)` to execute deterministic logic from CodeSchedule.

## Memory

LLM agents can have Chroma-backed memory. The memory layer supports:

- episodic and semantic memory entries.
- per-agent separation in a shared collection.
- retrieval before LLM calls.
- memory write after `instruct` when enabled.
- memory actions such as remember/recall when available.

Chroma is a required dependency in the current project direction. Do not present embedding as optional for LLM-agent experiments.

## Model Layer

Users declare providers with:

```python
LLMModel.openai(...)
LLMModel.openai_compatible(...)
LLMModel.azure_openai(...)
LLMModel.ollama(...)
EmbedModel.openai(...)
EmbedModel.openai_compatible(...)
EmbedModel.ollama(...)
```

The engine builds internal `LLMManager`, `EmbeddingManager`, and model provider objects. This keeps lifecycle, concurrency, logging, and injection centralized.

## Persistence And Outputs

The runtime writes:

- `steps.jsonl`: step results, tables, notes, observations.
- `metrics.jsonl`: per-step metrics.
- `events.jsonl`: run lifecycle and errors.
- `summary.json`: final run summary.
- `checkpoints/`: initial, periodic, and final world state.
- `chroma_store/`: memory persistence.
- `logs/`: structured resource and simulation logs.

The current default code path avoids the old studio-heavy node diff and streaming snapshot workflow.

## Legacy Schedule

The older selector/operator/converter YAML workflow should be treated as legacy/studio source. It can be useful background, but new skill-guided user experiments should not depend on it.
