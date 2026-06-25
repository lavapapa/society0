# Run, Monitor, Analyze

## Before Running

Check:

- Imports use `society0`, not `simengine`.
- LLM agents have both `llm=...` and `embed=...`.
- `LLMModel(..., concurrency=N)` matches the provider's known concurrent request limit; use 5 if unknown.
- The first run is small.
- Output schemas are simple.
- `save_dir` is intentional and not reused accidentally.

## Run Artifacts

Inspect:

```text
steps.jsonl
metrics.jsonl
events.jsonl
summary.json
checkpoints/
logs/
chroma_store/
```

Use `events.jsonl` first for failures and live monitoring. Use `steps.jsonl` for tables, notes, and observations. Use `metrics.jsonl` for time-series analysis. Use `summary.json` for final state, run metadata, resource cost, artifact sizes, and agent-operation summaries.

Checkpoints are complete machine-readable state snapshots and are written in compact JSON to keep long simulations smaller. Read them with `json.loads(...)` or pandas/json tooling rather than treating them as human-facing reports. For explanations to researchers, prefer `summary.json`, `metrics.jsonl`, and designed tables in `steps.jsonl`; use checkpoints when full world state is needed.

Default `events.jsonl` records are monitor-friendly semantic events such as run lifecycle, code-step lifecycle, agent-batch progress, action traces, recommendation traces, and failures. Raw `STATE_CHANGE` rows are hidden by default to keep multi-agent runs readable. If a debugging task truly needs state-change summaries in `events.jsonl`, construct the engine with `Society0(..., log_state_changes=True)`. Do not treat `events.jsonl` or compacted action tables as the complete research data source. Use checkpoints for full world state, `steps.jsonl` and `metrics.jsonl` for designed outputs, `resource_calls.jsonl` for model-call attribution, and Chroma for memory/vector persistence.

`summary.json` includes an `outputs` block that reports artifact size and line-count diagnostics:

```json
{
  "outputs": {
    "total_bytes": 182340,
    "files": {
      "events.jsonl": {"bytes": 6500, "line_count": 42},
      "steps.jsonl": {"bytes": 21000, "line_count": 10},
      "resource_calls.jsonl": {"bytes": 78000, "line_count": 60}
    },
    "checkpoints": {
      "count": 3,
      "total_bytes": 76840
    }
  }
}
```

Use this block to decide whether a run is growing because of monitoring events, designed step outputs, model-call traces, or checkpoints.

`summary.json` includes an `agent_operations` block when step outputs contain agent rows or action rows:

```json
{
  "agent_operations": {
    "browse_round": {
      "agent_count": 20,
      "success_count": 19,
      "error_count": 1,
      "turns_avg": 1.85,
      "turns_max": 3,
      "action_counts": {
        "comment": 12,
        "get_trending_posts": 20,
        "like_post": 5
      },
      "action_tag_counts": {
        "social_read": 20,
        "social_write": 17
      },
      "action_error_count": 1,
      "error_samples": [
        {
          "agent_id": "user_4",
          "status": "error",
          "error": "Missing required action tags for user_4: social_write"
        }
      ],
      "resources": {
        "llm": {
          "call_count": 40,
          "total_duration_sec": 320.5,
          "total_input_characters": 72000,
          "total_tools_characters": 18000,
          "total_payload_characters": 94000,
          "messages_count_max": 4,
          "tools_count_max": 8,
          "total_tokens": 31600
        },
        "embedding": {
          "call_count": 3,
          "texts_count": 20,
          "total_duration_sec": 0.42
        }
      },
      "slowest_agents_by_turns": [
        {"agent_id": "user_7", "total_turns": 3, "status": "success"}
      ]
    }
  }
}
```

Use this block first when explaining what agents did: how many agents succeeded, how many LLM turns were needed, which actions were used, which action tags were successfully completed, which memory path ran, and which agents need inspection. Agent-level `success_count` means the agent operation returned a usable result. `action_counts` counts total action attempts by action name, `successful_action_counts` counts completed attempts, and `failed_action_counts` counts failed tool attempts. `action_tag_counts` counts successful action rows only, so a failed `comment` attempt should not be treated as a completed `social_write` behavior. `action_error_count` is separate and catches recoverable tool mistakes such as trying to comment on a non-existent post before correcting the ID. `memory_summary` reports how many agent records had memory retrieval, saving, extractive memory enabled, extractive memory succeeded, and memory extraction errors. If `turns_avg` or `turns_max` is higher than expected, inspect the step's instruction, FoVs, exposed actions, `completion_action_tags`, `terminal_actions`, and `action_call_limits`.

When `resources` appears inside an agent operation, use it to explain why that specific code step was slow or expensive. `llm.call_count`, `messages_count_max`, `total_input_characters`, `total_tools_characters`, `total_payload_characters`, `total_tokens`, and `total_duration_sec` usually reveal whether the cost came from too many agent turns, large FoVs, large tool schemas, structured-output repair, or a slow provider. `embedding.texts_count` and batched `slowest_calls` show whether memory, post embedding, or semantic recommendation was involved.

`summary.json` also includes a `resources` block when LLM or embedding calls were made:

```json
{
  "resources": {
    "llm": {
      "call_count": 30,
      "error_count": 0,
      "duration_sec_max": 220.35,
      "duration_sec_p90": 180.20,
      "total_duration_sec": 1800.0,
      "total_input_characters": 480000,
      "total_tools_characters": 90000,
      "total_payload_characters": 600000,
      "messages_count_max": 4,
      "tools_count_max": 8,
      "prompt_tokens": 26847,
      "completion_tokens": 4571,
      "slowest_calls": [
        {
          "duration_sec": 220.35,
          "step_name": "browse_round",
          "interaction_type": "instruct",
          "interaction_name": "feed_interaction",
          "agent_id": "user_17"
        }
      ]
    },
    "embedding": {
      "call_count": 19,
      "texts_count": 31
    }
  }
}
```

Use this to explain runtime cost in plain language: how many LLM calls were made, whether failures occurred, which resource had the slowest call, and whether embedding calls were batched. A high `duration_sec_max` means one slow model request held back the run even if concurrency was configured correctly. Use `duration_sec_p50`, `duration_sec_p90`, `duration_sec_p99`, `slowest_calls`, and `by_interaction` to find whether slowness came from a specific step, interaction, agent, or provider.

For prompt-size diagnosis, prefer `total_input_characters`, `total_tools_characters`, `total_payload_characters`, `messages_count_total`, `messages_count_max`, and per-interaction versions of those fields. `total_input_characters` is message content, `total_tools_characters` is the serialized action schema, and `total_payload_characters` approximates the full provider payload excluding internal metadata. Large prompts usually come from long FoVs, too many retrieved memories, too many exposed actions, or multi-turn tool loops. If memory is enabled, check the step code for `memory_top_k`; for pilots and survey-style interviews, values such as `3` or `5` are often enough. If `total_tools_characters` is high, narrow `actions=[...]` to the env tools needed for that step. A browse round with `messages_count_max=4` usually means the agent made at least one read action, received the tool result, and then spent a second LLM call deciding the next action. That can be correct, but it is a real cost.

If `llm.call_count` is higher than the number of selected agents, inspect whether the step used structured output repair, multiple action turns, or default extractive memory. This is often correct: Society0 should preserve tool/action loops and full memory when they are part of the simulation. For an explicitly lightweight pilot, the researcher may set `extract_memory=False`, but that should be documented as a fidelity tradeoff. For action-only or survey-like operations, `max_tokens` can still be bounded when the expected response is short; do not remove tools or terminal/completion semantics merely to reduce latency.

For detailed attribution, inspect `resource_calls.jsonl`. LLM records should include `agent_id`, `step_name`, `interaction_type`, and `interaction_name`. Embedding records can be batched; when a batch covers multiple agents or memory operations, read plural fields such as `agent_ids`, `step_names`, `interaction_types`, and `interaction_names`.

During a long `instruct` or `interview`, `metrics.jsonl` may stay empty until the code step returns. Watch `events.jsonl` instead. `agent_batch_heartbeat` shows in-flight progress while model calls are still running: completed count, started count, in-flight count, pending count, and a sample of running agent ids. `agent_batch_progress` records each completion with the same concurrency state, so short batches without heartbeat events can still be diagnosed. `agent_batch_completed` closes the batch.

## Runtime Explanation

Before running, tell the researcher the effective LLM-agent concurrency in plain language. After running, verify it from `summary.json` or the `run_started` event:

```json
{
  "runtime": {
    "agent_concurrency": 5,
    "agent_concurrency_source": "llm_model"
  }
}
```

Interpretation:

- `society0`: set globally with `Society0(..., agent_concurrency=N)`.
- `llm_model`: inherited from `LLMModel(..., concurrency=N)`.
- `default`: provider limit was unknown, so Society0 used 5.

For most users, do not tune per-call concurrency. Adjust the model declaration if the provider limit is known.

For completed runs, inspect `summary.json -> events.agent_batches` for each `instruct` or `interview`. The batch entry includes configured `concurrency`, `concurrency_source`, `concurrency_source_counts`, cumulative `action_counts`, `successful_action_counts`, `failed_action_counts`, successful cumulative `action_tag_counts`, `action_error_samples`, `memory_summary`, `resources`, `batch_started_count`, `batch_completed_count`, `success_count_total`, `error_count_total`, `completed_count_total`, `duration_sec_total`, and progress diagnostics such as `progress_event_count`, `heartbeat_event_count`, `max_in_flight_count`, `max_pending_count`, and `max_started_count`. The plain `success_count`, `error_count`, `completed_count`, `duration_sec`, and `concurrency_source` fields describe the latest batch event for that interaction name; use the `*_total` fields and `concurrency_source_counts` when a named interaction repeats across ticks. For tick-level explanation, use `summary.json -> events.agent_batches.<interaction>.by_tick`, which has the same counters split by simulation tick. Use these fields to explain whether the run actually had agents in flight, where its concurrency value came from, what direct model-call cost the batch incurred, and whether required behavior categories occurred; do not infer runtime behavior from provider settings alone.

`events.agent_batches.<interaction>.resources` joins direct model calls for the same `interaction_type` and `interaction_name`. For example, `instruct / feed_interaction` includes the LLM calls that ran that agent loop, with `total_input_characters`, `total_tools_characters`, `total_payload_characters`, durations, token counts, and slowest calls. Separate fidelity phases such as extractive memory use their own interaction types such as `memory_extract`; inspect global `summary.json -> resources` and the batch `memory_summary` to explain those costs rather than merging them into the main agent-loop call count.

For default LLM simulations, `instruct(..., memory=True)` should normally show `memory_summary.save_enabled_count > 0`, `memory_summary.extraction_enabled_count > 0`, and resource traces for `memory_extract` and `memory_write`. `interview(...)` should normally show memory retrieval but `save_enabled_count == 0` unless the step explicitly opted into saving measurement memories.

When a step uses `required_actions`, `required_action_tags`, or `completion_action_tags`, also inspect `summary.json -> events.agent_batches.<interaction>.action_semantics`. It connects the configured semantic controls to observed successful action or action-tag counts. Use it to explain, for example, that `publish_post` was required and observed 20 times, or that `social_write` completed 12 times. Do not treat a configured action as completed until the observed count or agent result table proves it.

If `failed_action_counts` is non-empty but the batch `error_count` is zero, the agents may have made recoverable tool mistakes and corrected them in later turns. Inspect `action_error_samples` for the agent ID, action name, arguments, and compact error/result. Do not remove tools or shorten the action loop merely because a recoverable action failed once; first decide whether the failure indicates a prompt/FoV issue, an ambiguous ID, or a real environment constraint.

For deterministic logic, inspect `summary.json -> events.logic_executions`. Repeated rules or behaviors also include `by_tick`, so use that split when explaining policy updates, environment maintenance, rule baselines, or behavior failures over time.

For environment lifecycle maintenance, inspect `summary.json -> events.env_hooks`. Repeated `before_tick` and `after_tick` hooks include `by_tick`, so use that split when explaining cache rebuilds, index refreshes, delayed counter flushes, or hook failures over time. Do not confuse hook duration with LLM-agent thinking time; LLM and embedding calls appear under `resources` and agent batches.

## Quantitative Analysis

Typical checks:

- trends over ticks.
- treatment/control differences.
- persona or group differences.
- missing/failed agent calls.
- `agent_batch_started` / `agent_batch_heartbeat` / `agent_batch_progress` / `agent_batch_completed` events for each `instruct` or `interview`: agent count, concurrency, started count, in-flight count, pending count, completed count, duration, success count, error count, action counts, and successful action tag counts. After the run, prefer `summary.json -> events.agent_batches` for the compact roll-up, especially the cumulative fields when the same interaction name repeats over many ticks.
- `logic_execution_started` / `logic_execution_completed` / `logic_execution_failed` events for `ctx.rule(...)` and deterministic `behavior(...)`: started/completed/failed counts, success/error counts, agent totals, duration totals, and `by_tick`.
- `env_hook_started` / `env_hook_completed` / `env_hook_failed` events for `Environment.before_tick(...)` and `Environment.after_tick(...)`: started/completed/failed counts, duration totals, error samples, and `by_tick`.
- variance across repeated runs.
- for recommendation experiments: active pool size, pruning thresholds, scoring weights, final displayed post count, and exposure/impression counts.

Minimal pandas pattern:

```python
import json
import pandas as pd
from pathlib import Path

run_dir = Path("runs/demo")
metrics = pd.DataFrame(json.loads(line) for line in (run_dir / "metrics.jsonl").read_text().splitlines())
```

For tables inside steps:

```python
rows = []
for line in (run_dir / "steps.jsonl").read_text().splitlines():
    item = json.loads(line)
    for row in item["result"].get("tables", {}).get("survey", []):
        rows.append({"step": item["step"], **row})
survey = pd.DataFrame(rows)
```

## Qualitative Analysis

LLM-agent simulation often produces important qualitative material:

- stated reasons.
- generated comments.
- memories cited.
- interview answers.
- failed or surprising cases.

Code outputs can be coded inductively, but the researcher should review categories. Do not outsource substantive interpretation entirely to another LLM without audit.

## Report Shape

Recommended report:

1. Research question.
2. Simulation design: agents, environment, providers, steps, conditions.
3. Measurements and output schemas.
4. Quantitative results.
5. Qualitative patterns.
6. Robustness checks.
7. Limitations and next run.

Always mention model/provider dependence, prompt sensitivity, and the distinction between simulation outputs and empirical observation.

For `social_network` recommendation studies, explicitly report the recommendation condition: `full_scan_until`, pruning settings, chronological/engagement/similarity/network weights, whether embedding similarity was enabled, and `post_count`. These settings shape exposure and should be interpreted like experimental design choices.
