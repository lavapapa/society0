---
name: society0
description: "Help humanities, social science, and communication researchers use the Society0 simulation engine: design env-first social simulation experiments from observations, choose or extend environments, configure rule-based or LLM-based agents, write code-driven step(ctx) runs, configure LLM and embedding providers, monitor outputs, analyze results, and debug runtime issues."
---

# Society0

Use this skill to help a non-engineer researcher turn a social phenomenon into a small, runnable Society0 experiment, then inspect outputs and interpret results with appropriate methodological caution.

## Operating Loop

1. Start and maintain a visible todo list for the experiment. Use researcher-facing phases such as clarify phenomenon, design environment, define agents, write steps, run pilot, inspect outputs, analyze results, and refine.
2. Translate the user's observation into: research question, constructs, environment, agents, interaction loop, intervention/control, and measurements.
3. Design the **environment first**: the social setting, visibility rules, possible actions, interaction records, and institution/platform constraints. Agents only become meaningful inside that environment.
   For recommendation experiments, explicitly state the recommendation pool, scoring weights, pruning thresholds, and displayed post count; these are experimental conditions, not neutral plumbing.
4. Choose a built-in environment or propose a new one:
   - Start with `plain` for first surveys, simple state transitions, and rule baselines.
   - Use `social_network` for feeds, posts, endorsements, replies, recommendations, and diffusion.
   - Use `round_robin_conversation` for paired or rotating conversations.
5. Choose agent style:
   - Prefer **LLM-based agents** for interpretation, language, memory, persuasion, trust, identity, interviews, and social meaning.
   - Use **rule-based agents** for baselines, deterministic mechanisms, controls, parameter sweeps, fixtures, or non-linguistic updates.
6. For LLM agents, verify both provider layers: one LLM endpoint and one embedding endpoint. Suggest Ollama locally or OpenAI-compatible hosted providers such as OpenRouter, SiliconFlow, OpenAI, or Claude-compatible routes where appropriate.
7. Explain concurrency in plain language before running. If the user's LLM provider has a known concurrent request limit, set it on `LLMModel(..., concurrency=N)`; if unknown, use 5. `instruct` and `interview` automatically use this limit unless explicitly overridden.
8. For LLM action rounds and surveys, set a bounded `max_tokens` when the expected response is short, and inspect `summary.json` fields such as `total_input_characters`, `total_tools_characters`, `total_payload_characters`, and `outputs.total_bytes` when runtime is slow or run artifacts are large.
9. Treat memory as part of the simulation, not a speed optimization target. `memory=True` retrieves memory and saves extractive memory by default; use `extract_memory=False` only when the user explicitly accepts a lightweight pilot that is less faithful.
10. Use `terminal_actions=[...]` only when an action is semantically the named endpoint of the current task, such as submitting a final decision, leaving a round, or handing in a ballot. For social browsing rounds where read tools may continue but one real write interaction should finish the round, prefer `completion_action_tags=["social_write"]` instead of pretending each social action is terminal. Read actions can return user IDs and post IDs; when calling `comment`, `like_post`, `repost`, or `get_post_details`, use the explicit `post_id` shown by the environment.
11. Create one clean experiment folder per study. Put the experiment code, run outputs, analysis notebooks or scripts, and final report in that folder so runs do not mix.
12. Build the smallest useful run first: a few agents, a few ticks, explicit metrics, one qualitative table, and a clear run directory.
13. Inspect artifacts, explain what happened, then recommend repeated runs, controls, ablations, and sensitivity checks before making research claims. Use checkpoints for full state; default `events.jsonl` is a semantic monitoring log and does not include raw state-change rows.
14. If the user creates a useful environment, finds a bug, or develops a clear need from research practice, help them draft a focused GitHub issue or pull request for Society0.

## Researcher-Friendly Collaboration

Treat the researcher as the domain expert and the agent as the technical assistant. Ask for the observed phenomenon, social setting, actors, information flow, possible actions, and intended measurements; translate those into env, agents, steps, and outputs without forcing the user to learn framework internals. Before each run, summarize the experiment in everyday research language, including provider readiness and concurrency: "This run will let up to N LLM agents think at the same time." After each run, explain both quantitative metrics and qualitative traces, and clearly separate simulation output from empirical evidence.

Keep the todo list visible and update it as work progresses. The todo list should help the researcher see where they are in the experimental workflow, not expose incidental coding chores.

## Minimal Entrypoints

Imports:

```python
from society0 import EmbedModel, LLMModel, Society0
```

Base config:

```python
config = {
    "agent_types": [{"id": "reader", "archetype": "llm"}],
    "agents": [
        {"id": "alice", "type": "reader", "persona": "A skeptical reader.", "state": {"trust": 0.45}}
    ],
    "environment": {"type": "plain", "state": {"topic": "misinformation"}},
}
```

Providers:

```python
llm = LLMModel.openai_compatible(model="chat-model", base_url="https://provider/v1", api_key="...", concurrency=5)
embed = EmbedModel.ollama(model="nomic-embed-text", concurrency=5)
engine = Society0(save_dir="runs/demo", base_config=config, llm=llm, embed=embed)
```

Use `Society0(..., agent_concurrency=N)` only when the experiment should globally override the LLM model's concurrency. Per-call `users.instruct(..., concurrency=N)` and `users.interview(..., concurrency=N)` are higher-priority overrides for special cases.

Experiment workspace:

```text
experiments/trust_pilot/
  experiment.py
  runs/
  analysis.py
  report.md
```

Do not reuse a run directory for a different experiment or model setup. Run artifacts can contain prompts, FoVs, memory retrievals, LLM outputs, interviews, and researcher data; keep them inside the experiment folder and do not commit or share them without review.

Code step:

```python
@engine.step(name="measure_trust")
async def measure_trust(ctx):
    users = ctx.agents.where(type="reader")
    survey = await users.interview("请评价这条信息的可信度。", output=TrustSurvey)
    return ctx.result(metrics={"avg_trust": survey.mean("trust_score")}, tables={"survey": survey.table()})
```

Run:

```python
await engine.run(steps=3)
```

Rule-only baseline:

```python
@engine.step(name="rule_update")
async def rule_update(ctx):
    for agent_id in ctx.agents.where(type="reader").ids():
        ctx.world.agents_data[agent_id]["state"]["trust"] *= 0.95
```

## Read References As Needed

- `references/engine-components.md`: Current Society0 components and how they map to the codebase.
- `references/environment-design.md`: Why environment comes first, built-in environments, FoVs, actions, rules, and how to add a new env.
- `references/agent-design.md`: Agent types, personas, state, properties, models, memory, and reasoning stages.
- `references/step-dsl.md`: CodeSchedule, StepContext, AgentGroup, instruct/interview, results, outputs.
- `references/research-design.md`: Convert social science observations into simulation experiments.
- `references/study-patterns.md`: Reusable study patterns for communication, governance, organization, city, and economy simulations.
- `references/run-monitor-analyze.md`: Monitor runs and analyze quantitative and qualitative outputs.
- `references/debugging.md`: Provider, Chroma, schema, import, memory, and runtime troubleshooting.
- `references/field-examples.md`: Representative generative-agent and LLM social simulation examples.

If the skill or references are not specific enough, inspect the source directly. Start from `src/society0/society.py`, `src/society0/schedule.py`, `src/society0/environment.py`, `src/society0/env/`, and `src/society0/agent/core.py`. Treat source behavior as authoritative.

## Contribution Support

When a researcher wants to contribute, treat their research artifact as the source of truth. Help turn useful environments, reproducible bugs, documentation gaps, and experiment-driven feature ideas into concise issues or focused pull requests. Keep contribution text legible to maintainers and explain the research use case, expected behavior, reproduction steps, and minimal code or output evidence.

## Guardrails

- Do not describe Society0 as a traditional ABM system with LLMs merely swapped in for rules. It is a language-mediated simulation paradigm that can borrow ABM rigor.
- Do not design agents before the environment. The environment defines what agents can see, do, and leave behind as evidence.
- Do not hide provider requirements. LLM agents require working LLM and embedding providers.
- Do not ask researchers to tune concurrency by default. Put known provider limits on the model declaration; use 5 when unknown.
- Do not mix multiple studies in one run folder. Create a fresh experiment folder before writing code, running simulations, or analyzing outputs.
- Do not make first experiments large. Prototype, inspect, then scale.
- Do not overclaim from one run. Treat outputs as simulated evidence requiring robustness checks and researcher interpretation.
