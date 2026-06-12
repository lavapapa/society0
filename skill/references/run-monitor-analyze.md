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

Use `events.jsonl` first for failures. Use `steps.jsonl` for tables, notes, and observations. Use `metrics.jsonl` for time-series analysis. Use `summary.json` for final state and run metadata.

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

## Quantitative Analysis

Typical checks:

- trends over ticks.
- treatment/control differences.
- persona or group differences.
- missing/failed agent calls.
- variance across repeated runs.

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
