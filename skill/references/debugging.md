# Debugging

## Start Small

Reduce to one step, one or two agents, and a fresh run directory. Confirm the base config loads before debugging large experiments.

## Import Errors

Symptom:

```text
ModuleNotFoundError: No module named 'society0'
```

Fix:

```bash
pip install -e .
python -m pytest tests/primary
```

Use `society0`, not the old `simengine` import.

## Missing LLM Provider

Symptom:

```text
LLM agents require Society0(..., llm=LLMModel...)
```

Fix:

- Pass an `LLMModel`.
- Pass an `EmbedModel` when using memory/retrieval.
- For no-network smoke tests, use rule agents and avoid LLM `instruct` or `interview`.

## Provider Failures

Check:

- `base_url` includes the right path for the provider.
- API key is present but not committed.
- model name exists.
- Ollama is running and the model is pulled.
- `LLMModel(..., concurrency=N)` is not higher than the provider actually allows; use 5 if the limit is unknown.
- timeout is sufficient.

Use a direct provider smoke test before blaming Society0.

## Chroma Or Embedding Failures

Likely causes:

- embedding endpoint unavailable.
- wrong embedding dimensions.
- reused run directory with a different embedding model.
- stale Chroma files after interruption.

Fix:

- use a fresh `save_dir`.
- match `EmbedModel(..., dimensions=...)` to the provider.
- avoid mixing embedding models in one run directory.

## Structured Output Failures

If `AgentBatchResult.error_count` is nonzero or tables are empty:

- simplify the Pydantic schema.
- ask one question at a time.
- define numeric scales explicitly.
- inspect `result.table()` and `result.by_agent(agent_id)`.

## FoV Failures

If a FoV is missing:

- confirm the environment type supports that FoV.
- confirm the environment module is imported/registered.
- test without FoVs first.
- inspect `events.jsonl` for `fov_failed`.
- read `src/society0/env/<env_name>/env.py` to verify the exact `@fov` names.

## Action Failures

If an LLM agent cannot call an expected environment action:

- start with `actions=None` or `actions=["environment"]` during a prototype; `actions=None` exposes default non-memory actions.
- narrow later by action name or short action tag.

## State Or Output Inspection

Default `events.jsonl` is for readable monitoring and does not include raw state-change rows. When checking whether state changed, inspect `checkpoints/checkpoint_final.json` first. If you need state-change summaries for a focused debugging run, use a fresh run directory and create the engine with `Society0(..., log_state_changes=True)`.

If a run is unexpectedly slow or produces very large files, open `summary.json` and inspect:

- `resources.llm` and `agent_operations.*.resources.llm` for model latency, prompt size, tool-schema size, and turns.
- `resources.embedding` for memory, post, or recommendation embedding calls.
- `outputs.files` and `outputs.checkpoints` for artifact sizes and JSONL line counts.
- read the env source to confirm the exact `@action` method name and parameters.
- remember that `interview(...)` intentionally does not expose ordinary actions.

## State Selection Issues

If selection returns no agents:

- check each agent's `type`.
- check `agent_types` and inherited `archetype`.
- print or return `ctx.agents.all().ids()` in a smoke step.

## Useful Test Commands

```bash
python -m pytest tests/primary
python -m pytest tests/e2e
SOCIETY0_RUN_REAL_E2E=1 python -m pytest tests/e2e/test_society0_real_e2e.py
SOCIETY0_RUN_REAL_E2E=1 SOCIETY0_REAL_E2E_SATURATION_CONCURRENCY=6 python -m pytest tests/e2e/test_society0_real_e2e.py -m saturation
python -m pytest
```

Real E2E requires working LLM and embedding endpoints. Skipped real E2E does not prove provider integration works.

Preferred real E2E endpoint variables:

```bash
export SOCIETY0_RUN_REAL_E2E=1
export SOCIETY0_REAL_E2E_LLM_BASE_URL="https://your-llm-provider/v1"
export SOCIETY0_REAL_E2E_LLM_MODEL="your-chat-model"
export SOCIETY0_REAL_E2E_EMBED_BASE_URL="https://your-embedding-provider/v1"
export SOCIETY0_REAL_E2E_EMBED_MODEL="your-embedding-model"
export SOCIETY0_REAL_E2E_EMBED_PROVIDER="openai_compatible"
export SOCIETY0_REAL_E2E_EMBED_DIMENSIONS=768
```

Keep provider credentials in the local environment or secret manager used by the
test runner; do not commit them to docs or run artifacts. For local Ollama
embeddings, set `SOCIETY0_REAL_E2E_EMBED_PROVIDER=ollama` and
`SOCIETY0_REAL_E2E_EMBED_BASE_URL=http://localhost:11434`.
