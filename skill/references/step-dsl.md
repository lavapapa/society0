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
- `ctx.activation_pool(...)`: temporary bounded pool for work discovered while a step is already running.
- `ctx.result(...)`: structured return helper.

Capability discovery:

```python
ctx.capabilities.names("fov")
ctx.capabilities.names("action")
ctx.capabilities.names("tools")  # alias for actions
ctx.capabilities.names("rule")
ctx.capabilities.names("behavior")
ctx.capabilities.names("action", source="environment")
ctx.capabilities.find("get_trending_posts")
ctx.capabilities.has("rule", "advance_round_robin_with_pairing")
ctx.capabilities.has("behavior", "custom_baseline", source="experiment")
ctx.capabilities.by_source("environment")
ctx.capabilities.by_source("experiment", kind="rules")
ctx.capabilities.get("rule", "env.advance_round_robin_with_pairing")
```

During a step, use `ctx.capabilities.by_source(...)` to distinguish env-provided capabilities from experiment-specific rules and behaviors. If you have a name but do not know its kind, call `ctx.capabilities.find(name)` first; it returns matching FoVs, actions/tools, rules, and behaviors with their sources. `ctx.capabilities.has(...)`, `ctx.capabilities.get(...)`, and `ctx.capabilities.names(...)` accept singular/plural kind names, plus `tool`/`tools` as aliases for `action`/`actions`. They also accept display names, canonical IDs, registry keys, function names, and aliases, so an agent can safely resolve names copied from source code, `summary.json`, or docs. After a run, inspect `summary.json -> capabilities.by_source` for the same distinction in the final report. This is useful when deciding whether to extend an environment or keep one-study logic in the experiment code.

Capability entries include `aliases`, `parameters`, `return_value_schema`, `func_name`, `tags`, `source`, and `environment_type` when available. Before calling `ctx.rule(...)`, `ctx.behavior(...)`, or exposing `actions=[...]`, inspect those fields instead of guessing argument names.

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
    memory_top_k=5,
    extract_memory=True,
    model=None,
    max_tokens=120,
    temperature=0,
    max_turns=3,
    name="feed_interaction",
    reasoning_stages=[{"name": "判断", "desc": "先判断信息可信度，再决定行动。"}],
)
```

Parameters:

- `instruction`: task text.
- `fovs`: environment views to include.
- `actions`: action tag filter; use `None` for default non-memory actions.
- `output`: Pydantic model, dict schema, or `None`.
- `memory`: retrieve and save memory when true.
- `memory_top_k`: maximum memories retrieved per agent when memory is enabled. Default is `10`; use a smaller value such as `3` or `5` for pilots, surveys, or large agent batches.
- `extract_memory`: whether Society0 uses an additional LLM pass to save structured episodic memories. Default is `True` when `memory=True`; set `False` only for an explicitly lightweight pilot where the user accepts less faithful memory.
- `model`: optional model id.
- `max_tokens`: optional cap for each LLM response in this operation. For action-only rounds, set a small value such as `80` or `120`; tool-call capable models otherwise may spend seconds generating unnecessary text.
- `temperature`, `top_p`, `timeout`: optional per-operation LLM request controls.
- `llm_options`: optional dict for provider-compatible request parameters. Do not use it to override `messages`, `tools`, `tool_choice`, `metadata`, `agent_id`, or `model`; Society0 owns those fields.
- `max_turns`: agentic loop limit.
- `concurrency`: optional per-call override for how many LLM agents run at once.
- `name`: trace name.
- `reasoning_stages`: optional cognitive stages for studies that compare decision procedures.
- `terminal_actions`: optional action names that end the agent loop immediately after a successful call because that action is the natural endpoint of the current task. A failed terminal action should not end the round; the model should receive the tool error and correct itself if turns remain.
- `completion_action_tags`: optional action tags that end the agent loop after a successful matching action. Use this when a category of actions completes the round, while other read or lookup tools may still be intermediate.
- `required_actions`: optional action names that must be successfully called by each selected agent for that agent record to count as success. Use this when the experiment design requires a concrete behavior, not just a valid LLM response. When turns remain, Society0 reminds the model to correct a missing required action instead of silently accepting a text-only answer.
- `required_action_tags`: optional action tags that must be successfully called by each selected agent for that agent record to count as success. Use this when the exact action may vary but the behavior category is required. When turns remain, Society0 also reminds the model to satisfy a missing required tag.

During prototypes, use `actions=None` to expose available non-memory actions. Narrow later with `actions=["environment"]` or exact action names after checking the env source or run logs. If an action filter matches no available action, treat that as a configuration error: a FoV belongs in `fovs=[...]`, a rule belongs in `ctx.rule(...)`, and a behavior belongs in `ctx.behavior(...)` or `AgentGroup.behavior(...)`. Do not "fix" this by directly mutating state or bypassing the LLM tool loop when the study is about agent behavior. Use `actions=["memory"]` only when the study explicitly wants agents to call memory tools themselves; `memory=True` already performs framework-managed retrieval and saving.

Use terminal actions for explicit endpoints, not for performance shortcuts. Good examples are actions such as `submit_final_decision`, `cast_vote`, `leave_round`, or `submit_survey_response` when the experiment defines those as final acts in the current instruction.

```python
decisions = await users.instruct(
    "Review the proposal, then submit your final decision for this round.",
    actions=["submit_final_decision"],
    output=None,
    memory=False,
    terminal_actions=["submit_final_decision"],
    max_turns=3,
)
```

Do not mark ordinary intermediate social actions as terminal just because they are expensive. `publish_post`, `like_post`, `comment`, `repost`, and `follow` usually do not end an agent's social behavior round; after publishing, the agent may still inspect results, remember context, or take another action. If the study needs only one post per agent, encode that in the instruction, action set, or step logic, not by pretending publication is terminal.

For social browsing rounds, a common completion boundary is: read tools may happen first, but one real interaction ends the round. Express that with action tags:

```python
result = await users.instruct(
    "Browse the feed. You may inspect trending posts, then make one real interaction if useful.",
    fovs=["recommended_feed"],
    actions=["get_trending_posts", "comment", "like_post", "repost"],
    memory_top_k=5,
    max_turns=3,
    completion_action_tags=["social_write"],
    action_call_limits={"comment": 1, "like_post": 1, "repost": 1},
    required_action_tags=["social_write"],
    name="feed_interaction",
)
```

This does not make read actions terminal. It only stops after a successful action tagged `social_write`, such as `comment`, `like_post`, `repost`, `publish_post`, `follow`, or `unfollow` in the built-in `social_network` env.

If a write action returns a clear semantic failure such as "post not found", Society0 records that action row as `status="error"` and does not treat it as a completed `social_write` action. The agent may use the next turn to correct the ID if `max_turns` allows it. In social-network experiments, tell the agent to use the explicit `post_id` shown in the feed or read-action output, not the author/user ID.

Use `action_call_limits` for bounded social tasks. When every available non-system action has exhausted its explicit limit, Society0 stops the loop without spending another LLM call just to discover that all remaining actions are blocked.

```python
await users.instruct(
    "You must publish exactly one short post for this round.",
    actions=["publish_post"],
    action_call_limits={"publish_post": 1},
    required_actions=["publish_post"],
    max_turns=3,
)
```

`actions=[...]` only controls which tools are available. It does not prove that the agent actually used a tool. When the scientific design requires an action such as publishing, voting, commenting, or submitting a decision, use `required_actions=[...]` and inspect `result.action_counts()` or `summary.json -> events.agent_batches`. The required action or required action tag must be satisfiable by the filtered action set: for example, do not set `actions=["comment"]` with `required_actions=["publish_post"]`, and do not set `actions=["get_trending_posts"]` with `required_action_tags=["social_write"]`. Society0 should report these as configuration errors before spending LLM calls. The event batch summary is the fallback diagnostic even if the step did not write an action table. In `summary.json`, `events.agent_batches.<interaction>.action_semantics` links configured `required_actions`, `required_action_tags`, and `completion_action_tags` to observed successful counts.

Use `result.action_tag_counts()` when the exact action may vary but the behavior category matters. For example, a social interaction round may count successful `social_write` actions across `comment`, `like_post`, and `repost`. Failed action rows are not counted as successful tags.

Do not combine a terminal domain action with a required `output` schema unless the experiment really needs both. Structured output adds a `submit_result` tool, which can require extra LLM calls after the domain action.

## Interview

Use `interview` for measurement, survey, or elicitation.

For social-network recommendation studies, use `fovs=["recommended_feed_preview"]` in interviews when the measurement should not itself count as feed exposure. Use `recommended_feed` for actual browsing behavior where impressions should be recorded.

```python
survey = await users.interview(
    "请评价你对这条消息的可信度，0 表示完全不信，1 表示完全相信。",
    fovs=["recent_posts"],
    output=TrustSurvey,
    retrieve_memory=True,
    memory_top_k=3,
    save_memory=False,
    max_tokens=80,
    temperature=0,
    max_turns=2,
    name="trust_survey",
    reasoning_stages=[{"name": "回忆", "desc": "先回忆相关经历。"}, {"name": "回答", "desc": "再给出结构化回答。"}],
)
```

`interview` intentionally does not expose ordinary actions. It defaults to reading memory but not writing memory, which preserves a measurement-oriented meaning. Structured interview output still uses the agent's submit-result action loop by default; direct JSON output is a lower-fidelity optimization that should only be enabled explicitly in low-level code when the researcher accepts that tradeoff. For large surveys, lower `memory_top_k` first before increasing model concurrency; unnecessary memory snippets increase prompt size for every selected agent.

For surveys and other bounded measurements, set `max_tokens` deliberately. A short structured answer rarely needs a large generation budget, and lower caps make real provider latency easier to control.

## Concurrency

`instruct` and `interview` never need to fan out to all selected agents at once. Their concurrency priority is:

1. explicit `users.instruct(..., concurrency=N)` or `users.interview(..., concurrency=N)`;
2. global `Society0(..., agent_concurrency=N)`;
3. `LLMModel(..., concurrency=N)`;
4. default `5` when the provider limit is unknown.

This is an agent-operation limit: at most N selected agents enter the LLM-facing operation at the same time. The model managers still enforce endpoint-level LLM and embedding semaphores underneath, so provider requests remain bounded even when memory retrieval or embedding calls occur.

After the run, inspect `summary.json -> events.agent_batches.<interaction>.concurrency_source` and `concurrency_source_counts`. This confirms whether the batch used a per-call override, the global `Society0(...)` value, the `LLMModel(...)` concurrency, or the default of 5.

Recommended researcher-facing wording before a run:

```text
This run will let up to N LLM agents think at the same time. If your provider allows a different concurrency limit, we should update LLMModel(..., concurrency=N).
```

Use a per-call override only for special study design reasons, such as slowing a sensitive interview or letting a deterministic pilot run faster. Deterministic `behavior` calls are not governed by the model concurrency policy.

### Dynamic activation pool

Use an activation pool when actions performed by one agent can reveal more
agents that need to run in the same step. The pool continuously fills free
slots, so a fast agent does not wait for the slowest member of a fixed batch.
Its default capacity follows the same resolved runtime concurrency as ordinary
agent calls.

```python
@engine.step(name="respond_to_new_work")
async def respond_to_new_work(ctx):
    async with ctx.activation_pool() as pool:
        for agent_id in ctx.env.initially_active_agent_ids():
            pool.instruct(
                agent_id,
                "Review what changed and act when useful.",
                fovs=["operating_context"],
                actions=["environment"],
                dedupe_token=("initial", ctx.step, agent_id),
            )
```

The context manager drains the pool before the step finishes. While the block
is active, the same object is available as `ctx.env.activation_pool`, so an
environment method or an agent action can synchronously enqueue newly
discovered work:

```python
self.activation_pool.instruct(
    recipient_id,
    "Review what changed and act when useful.",
    fovs=["operating_context"],
    actions=["environment"],
    dedupe_token=("message", message_id),
)
```

`submit(key, async_closure, payload=..., dedupe_token=..., serial_key=...,
handler_id=...)` and
its `enqueue` alias support non-agent asynchronous work. The closure may accept one
`ActivationBatch` argument to inspect all payloads merged into that round.
One key denotes one task contract:

- repeated submissions while queued become one round;
- submissions arriving while that key is running become at most one follow-up round;
- a repeated `(key, dedupe_token)` is ignored for the rest of the pool session;
- different tokens are retained in arrival order.

The first submission binds the key to a handler for the lifetime of the pool.
Passing a different closure under the same key is rejected instead of silently
discarding the later closure. A stable function or bound method normally needs
no explicit `handler_id`. If an environment method creates a new nested closure
on every call, pass the same stable `handler_id` for closures that implement the
same contract.

The default key for `pool.instruct(...)` is the target agent, which prevents the
same agent from entering concurrent instruct rounds. Instruct calls also use a
separate Agent serial key. Environment closures that act as the same Agent can
join that mutual-exclusion domain without merging their work:

```python
pool.submit_agent(
    agent_id,
    ("industry_follow_up", agent_id),
    run_industry_follow_up,
)
```

Tasks with different work keys remain separate rounds, while equal non-null
`serial_key` values prevent them from running concurrently. Waiting for a
serial key does not consume one of the pool's capacity slots, so unrelated
agents can start immediately. Calls merged under one work key should use the
same serial key, closure contract, FoVs, actions, and instruct options. Their
distinct static instruction strings are joined once in arrival order. Put
domain concepts such as inbox items or agenda records in the environment's
payloads and FoV; the core pool only understands work keys, serial keys,
handler identities, payloads, and tokens. Two `instruct` submissions under the
same key must use the same Agent, FoVs, actions, and execution options; a
configuration mismatch is rejected rather than being overwritten by the first
call.

`await pool.drain()` returns queryable `ActivationResult` records after all
queued and follow-up work finishes. The idle check also covers work submitted
on the same event-loop boundary where the queue first becomes empty, so the
context manager cannot detach the environment while a late closure is still
running. If a closure failed, drain raises
`ActivationPoolError` after the remaining work has completed; the same records
remain available through `pool.results`. If the step body itself fails, the
pool cancels running work, discards queued work, and removes
`env.activation_pool`.

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
- experiment-specific logic: register one-study logic with `engine.registry.env.rule(...)`, `engine.registry.env.action(...)`, or `engine.registry.sched.behavior(...)`.

Use `ctx.capabilities.by_source("environment", kind="rule")` and `ctx.capabilities.by_source("experiment", kind="behavior")` when the distinction matters for the study design or final explanation.

Missing rule/behavior names are configuration errors and should fail fast. If an error says a name is registered as an `action`, do not call it with `ctx.behavior(...)`; expose it to LLM agents through `instruct(..., actions=[...])` so the agent loop performs the behavior. If an error says a name is registered as a `fov`, use it in `fovs=[...]` or call the env method directly only for inspection. If these helpers are unavailable in the installed version, use the lower-level source-backed APIs only after reading `src/society0/schedule.py`, `src/society0/function_registry.py`, `src/society0/agent/core.py`, and the target env source. Do not claim a workflow migration is complete until code steps can trigger FoVs, actions, interviews, rules, and behaviors.

## Batch Results

`AgentBatchResult` supports:

```python
result.table()
result.values("trust_score")
result.mean("trust_score")
result.by_agent("alice")
result.success_count
result.error_count
result.action_counts()
result.successful_action_counts()
result.failed_action_counts()
result.action_tag_counts()
result.memory_summary()
result.error_samples(limit=5)
```

`action_counts()` counts all attempts. Use `successful_action_counts()` when explaining completed behavior, and use `failed_action_counts()` when diagnosing tool mistakes or ambiguous instructions. `memory_summary()` reports actual memory retrieval/save/extraction diagnostics returned by the selected agents. Write row-oriented tables so researchers can load them with pandas.

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
