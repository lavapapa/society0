# Step DSL

The code-driven path is the primary replacement for the old workflow schedule. It should cover selection, instruction, interview, rule execution, behavior execution, result shaping, and run artifacts. If a needed helper is missing from an installed version, inspect source and use `ctx.world` as a temporary escape hatch, then help the user file a focused issue or pull request.

## Runtime Semantics

`engine.run(steps=N)` runs N simulation ticks. Each tick executes all registered code steps in registration order.

```python
@engine.step(name="daily_round", params={"feed_size": 10})
async def daily_round(ctx):
    ...
```

Step functions must be async and should return either `ctx.result(...)` or `None`.

## StepContext

`ctx` provides:

- `ctx.step`: current tick number.
- `ctx.step_name`: current code step name.
- `ctx.world`: raw world state and low-level APIs.
- `ctx.env`: current environment object.
- `ctx.agents`: agent selector.
- `ctx.params`: step params.
- `ctx.log`: runtime log context.
- `ctx.capabilities`: discovery helper for FoVs, actions, rules, and behaviors.
- `ctx.result(...)`: structured return helper.

Capability discovery:

```python
ctx.capabilities.names("fov")
ctx.capabilities.names("action")
ctx.capabilities.names("rule")
ctx.capabilities.names("behavior")
ctx.capabilities.has("rule", "advance_round_robin_with_pairing")
```

## Agent Selection

```python
ctx.agents.all()
ctx.agents.ids(["alice", "bob"])
ctx.agents.where(type="reader")
ctx.agents.where(type="reader", archetype="llm")
ctx.agents.sample(20, seed=42, where={"type": "reader"})
ctx.agents.filter(lambda agent: agent.id.startswith("a"))
```

Call `.ids()` when you need stable ids for tables or direct state edits.

## Instruct

Use `instruct` for behavior rounds where LLM agents may act.

```python
result = await users.instruct(
    "浏览信息流，并决定是否点赞、评论、转发或发帖。",
    fovs=["recommended_feed"],
    actions=["environment", "memory"],
    output=ActionSchema,
    memory=True,
    model=None,
    max_turns=3,
    name="feed_interaction",
    reasoning_stages=[{"name": "判断", "desc": "先判断信息可信度，再决定行动。"}],
)
```

Parameters:

- `instruction`: task text.
- `fovs`: environment views to include.
- `actions`: action tag filter; use `None` for defaults.
- `output`: Pydantic model, dict schema, or `None`.
- `memory`: retrieve and save memory when true.
- `model`: optional model id.
- `max_turns`: agentic loop limit.
- `concurrency`: optional per-call override for how many LLM agents run at once.
- `name`: trace name.
- `reasoning_stages`: optional cognitive stages for studies that compare decision procedures.

During prototypes, use `actions=None` to expose available actions. Narrow later with `actions=["environment"]`, `actions=["memory"]`, or exact action names after checking the env source or run logs.

## Interview

Use `interview` for measurement, survey, or elicitation.

```python
survey = await users.interview(
    "请评价你对这条消息的可信度，0 表示完全不信，1 表示完全相信。",
    fovs=["recent_posts"],
    output=TrustSurvey,
    retrieve_memory=True,
    save_memory=False,
    max_turns=2,
    name="trust_survey",
    reasoning_stages=[{"name": "回忆", "desc": "先回忆相关经历。"}, {"name": "回答", "desc": "再给出结构化回答。"}],
)
```

`interview` intentionally does not expose ordinary actions. It defaults to reading memory but not writing memory, which preserves a measurement-oriented meaning.

## Concurrency

`instruct` and `interview` never need to fan out to all selected agents at once. Their concurrency priority is:

1. explicit `users.instruct(..., concurrency=N)` or `users.interview(..., concurrency=N)`;
2. global `Society0(..., agent_concurrency=N)`;
3. `LLMModel(..., concurrency=N)`;
4. default `5` when the provider limit is unknown.

This is an agent-operation limit: at most N selected agents enter the LLM-facing operation at the same time. The model managers still enforce endpoint-level LLM and embedding semaphores underneath, so provider requests remain bounded even when memory retrieval or embedding calls occur.

Recommended researcher-facing wording before a run:

```text
This run will let up to N LLM agents think at the same time. If your provider allows a different concurrency limit, we should update LLMModel(..., concurrency=N).
```

Use a per-call override only for special study design reasons, such as slowing a sensitive interview or letting a deterministic pilot run faster. Deterministic `behavior` calls are not governed by the model concurrency policy.

## Rule And Behavior

Old workflow schedules had explicit `Rule` and `Behavior` operators. Code steps should be able to express the same ideas:

- rule: environment-level or system-level update, such as advancing a round, recalculating feeds, applying policy, or updating exposure counters.
- behavior: deterministic agent-level behavior, useful for rule agents, baselines, fixtures, or simple mechanism updates.

Use registered rules and behaviors from code steps:

```python
await ctx.rule("advance_round", phase="discussion")
await ctx.agents.where(archetype="rule").behavior("update_trust", delta=-0.02)
await ctx.behavior("update_trust", agents=["alice", "bob"], delta=-0.02)
```

Logic has two sources:

- env-provided logic: an environment can provide `@rule` and `@behavior` capabilities.
- experiment-specific logic: register one-study logic with `engine.registry.env.rule(...)` or `engine.registry.sched.behavior(...)`.

Missing rule/behavior names are configuration errors and should fail fast. If these helpers are unavailable in the installed version, use the lower-level source-backed APIs only after reading `src/society0/schedule.py`, `src/society0/function_registry.py`, `src/society0/agent/core.py`, and the target env source. Do not claim a workflow migration is complete until code steps can trigger FoVs, actions, interviews, rules, and behaviors.

## Batch Results

`AgentBatchResult` supports:

```python
result.table()
result.values("trust_score")
result.mean("trust_score")
result.by_agent("alice")
result.success_count
result.error_count
```

Write row-oriented tables so researchers can load them with pandas.

## Step Results

```python
return ctx.result(
    metrics={"avg_trust": survey.mean("trust_score")},
    tables={"survey": survey.table()},
    artifacts={"prompt_version": "v1"},
    observations={"failed_agents": survey.error_count},
    notes="Completed one trust survey round.",
)
```

Use:

- `metrics` for scalar time-series values.
- `tables` for repeated rows.
- `artifacts` for structured supporting outputs.
- `observations` for debug or qualitative summaries.
- `notes` for concise run interpretation.
