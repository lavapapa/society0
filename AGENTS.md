# Society0 Core Development Principles

This repository contains the standalone `society0` core simulation library. Future agents should treat these principles as durable constraints unless the project owner explicitly changes them.

## Scope

- Work inside `society0core/` for this package. The broader SZU platform is a separate project.
- The public package name is `society0`.
- The recommended runtime path is `Society0 + CodeSchedule + step(ctx)`.
- Legacy YAML workflow code may remain importable, but new runtime features should not depend on legacy schedule compatibility unless requested.
- Do not add campus-network, private-server, or platform-specific endpoint assumptions to product code or public docs. Optional real e2e tests may read endpoints from the local environment, but the open-source core must stay provider-neutral.

## LLM Agent Integrity

- Do not replace LLM-based simulation behavior with shortcuts for speed.
- A selected LLM agent must run the real agent loop through `World.instruct_agent()` or `World.interview_agent()`.
- Default LLM simulations require both `LLMModel` and `EmbedModel`; memory must initialize successfully on the main `Society0` path.
- `AgentGroup.instruct(..., memory=True)` must retrieve memory and save extractive memory by default. Do not downgrade to lightweight memory for speed unless a caller explicitly sets `extract_memory=False`.
- Preserve the full tool/action loop, including action filters, terminal actions, completion action tags, action call limits, reasoning stages, and per-call concurrency. These are semantic modeling controls, not performance shortcuts to remove.
- Treat `terminal_actions` as semantic endpoints only after the terminal action succeeds. A failed terminal action should return feedback to the agent loop so the agent can correct arguments instead of ending the round prematurely.
- Treat `required_actions` and `required_action_tags` as loop-level semantic constraints, not only post-hoc assertions. If turns remain and the model stops before satisfying them, the loop should guide the model to correct the missing behavior.
- Unit tests may use fake managers or fake model responses, but product code must still exercise the same LLM, embedding, memory, FoV, and action plumbing.
- Real endpoint e2e tests must call actual LLM and embedding endpoints when validating model/runtime changes.

## Env-First Architecture

- Experiments should be designed from the environment outward. The env defines the scene, available FoVs, actions, rules, behaviors, and state transitions that agents can perceive and use.
- Agents interact through env-provided capabilities or registered experiment-specific logic, not by mutating unrelated internals.
- Keep general env/system interfaces env-agnostic. If a problem is general, fix the generic abstraction; if it is specific to `social_network`, keep the change in that env.
- Do not add `social_network`-specific assumptions to `Society0`, `CodeSchedule`, `World`, or generic agent APIs.
- Capabilities exposed through `ctx.capabilities`, `ctx.rule(...)`, `ctx.behavior(...)`, FoVs, and actions should work for any env that declares them.

## FoV, Actions, Rules, And Behaviors

- FoVs are read-oriented context for prompts.
- Actions are tools agents may call during `instruct`; they can change state when the env defines that behavior.
- `interview` is a measurement path: it may read FoVs and memory, but must not expose ordinary actions by default and must not save memory unless explicitly requested.
- Rules are environment-level or experiment-level logic called directly from code steps.
- Behaviors are agent-level or experiment-level logic called directly from code steps for selected agents.
- Missing capability errors should be researcher-friendly and should not confuse FoVs with actions.

## Concurrency

- Never allow unlimited fan-out for LLM calls.
- Concurrency priority is: explicit `AgentGroup.instruct/interview(..., concurrency=N)`, then `Society0(agent_concurrency=N)`, then `LLMModel.concurrency`, then default `5`.
- Behaviors and rules may be lightweight, but anything that calls LLM or embedding resources must respect managed concurrency.
- Batch runs should emit enough runtime events for users and agents to understand active concurrency, progress, and failures.

## Logging, Outputs, And State

- Default runs should write clean JSONL/JSON outputs: steps, metrics, events, summary, checkpoints, and resource logs where relevant.
- Avoid raw `print(...)` in runtime paths. Use structured logging or event records.
- Checkpoints store simulation state. Runtime caches, recommendation caches, and other derived indexes should be rebuildable from state and resources unless explicitly designed as research data.
- `log_state_changes=True` is a debugging choice, not the default user path.

## Testing Expectations

- Add focused primary tests for new public APIs and generic abstractions.
- Add env-specific tests for env behavior, especially built-in env rules, behaviors, FoVs, actions, and hooks.
- Add e2e tests for runtime changes that affect model calls, memory, resource managers, persistence, or full experiment flow.
- For real LLM/embedding behavior, use opt-in real endpoint e2e tests rather than weakening product behavior for local speed.
- Do not update stale tests by asserting old internals when the public behavior has intentionally changed.

## Worktree Hygiene

- The worktree may contain unrelated user changes. Do not revert changes you did not make.
- Keep edits scoped to the requested package and behavior.
- Prefer small, reviewable fixes with tests over broad refactors.
