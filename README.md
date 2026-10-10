# Society0 Next · 猎龙

This is the independent, fully redesigned **Next** line, codenamed **猎龙**, intended for **Society0 V2**. Development lives on [`next`](https://github.com/lavapapa/society0/tree/next). The existing architecture remains the official stable line on [`stable/4.1`](https://github.com/lavapapa/society0/tree/stable/4.1), including industry-chain consumers. Install Next from an explicit branch or commit; the repository default selects the stable line.

Society0 is a general social simulation engine for agent-assisted research. Actors share one environment; plugins implement its internal mechanisms. Rule and LLM drivers use the same information and action interfaces, while code schedules define the study's timing and ordering.

The package version is defined in [pyproject.toml](pyproject.toml). This branch contains the redesigned Core; the completed redesign is tracked in [the implementation checklist](docs/core-next/TODO.md), and the next validation phase in [basic-environment migration](docs/core-next/basic-env-migration.md). Existing artifacts must be read using their producing version. The V2 product name defines an independent line; source installation must select Next explicitly, rather than relying on package-version ordering against the stable line.

## Start a study

Follow [the first run and recovery tutorial](docs/core-next/getting-started.md) to install from a chosen source revision, run two rule actors, read actual results and continue the remaining time in a new directory. It uses the base package and needs no model account. Python 3.12 or later and macOS or Linux are supported by the storage implementation.

For assistant-guided research, install the complete [Society0 skill](skill/SKILL.md) folder in your coding assistant's skill directory, preserving its references and assets. The skill guides research design; the Python package executes experiments. A two-round LLM study is [minimal_experiment.py](skill/assets/minimal_experiment.py). It includes persistent identity, perception, a domain action, explicit experience extraction and structured measurement. Its [quickstart](skill/references/runtime-quickstart.md) explains provider setup.

Use the functional dependency groups required by your study: `llm`, `memory`, `social`, `shell` and `observe`. Base rule runs need no model endpoints or Rust build. The LLM file tools use the native filesystem package, with its own wheel and source build requirements; see [installation](docs/core-next/installation.md).

Codex subscription access is configured through the [subscription guide](docs/core-next/subscription-guide.md). The `llm` extra provides the Pydantic AI integration; simulation actions and complete Thread history remain owned by Society0. The [model and driver contracts](docs/core-next/llm-contract.md) describe the integration boundary.

## Compose an environment

`Plugin` declares services, static child plugins, schema and initialization, with separate service and data-prerequisite graphs. `compose` establishes the shared state before installing services. `ActorRecord` describes persistent identity and subjective state; `actor_data_plugin` owns the persistent directory and `actor_plugin` binds it to drivers built only when needed. `rule_driver_plugin` and `llm_driver_plugin` expose factories through ordinary plugin services. Activation extensions share cognition and memory between drivers; restored actor records resolve their stored driver names against those services.

`Information` provides discoverable documents and datasets with authorized totals, continuation cursors and complete original-content reads. `Actions` exposes templates against resource references and rechecks eligibility when invoked. `LLMDriver` offers these through meta tools and an optional Bashkit shell, preserving the full Thread. `RuleDriver` accesses the same structured interfaces.

`Schedule` supplies the next simulation time and ordered `Phase` sequence; `Runtime` executes and publishes that complete step. Serial execution is the default; explicitly independent phases can run concurrent actors. Endpoint and shared request limits separately bound external calls. Completion, waiting and incomplete outcomes remain distinct.

The [two-mechanism conversation plan](examples/core_next/conversation_pilot.py), [external graph initialization](examples/core_next/graph_environment.py), [typed records](examples/core_next/typed_records.py) and [immutable catalog](examples/core_next/immutable_catalog.py) demonstrate reusable mechanisms. The [independent recommendation observer](examples/core_next/recommendation_observer.py) consumes reusable ranking inputs and strategies; the [social contract](docs/core-next/social-contract.md) explains leaf composition and explicit cognition effects. Detailed contracts are in [docs/core-next](docs/core-next).

## Run and inspect

`RunPlan` and `run_plan` freeze the public run contract and execute complete steps. Preserve code, dependency and configuration identities, and supply credentials through the runtime environment. Every attempt uses its own run directory.

```sh
python -m society0.kernel.observation /path/to/run
python -m society0.kernel.observation /path/to/run --serve 8711
```

Install the `observe` extra to start the HTTP server. Observation reads live diagnostics, Thread tails, resource usage, action outcomes and results. Fixed complete views support historical analysis. Large records retain original-content references and byte-range continuation. [The observation contract](docs/core-next/observation-contract.md) explains version boundaries, API examples and costs; the [workbench](docs/core-next/workbench-contract.md) renders recorded experiment outputs.

## Research and verification

Keep observed simulation outcomes separate from empirical claims. Preserve all information needed for decisions, actionable domain operations, original messages and fact ordering. Memory writing, recall and active tools are independently configured. Provider failures and budget truncation remain visible; a successfully readable checkpoint alone does not establish scientific validity.

Run the deterministic suite with the development dependencies. Real endpoint tests use an explicit environment profile and separate output directory.

```sh
uv sync --all-extras
uv run pytest -m 'not real_e2e'
```

The [implementation overview](docs/core-next/implementation-result.md) links the current contracts and measured limits. Architecture boundaries are in [PROJECT.md](PROJECT.md), semantic parity in [the capability map](docs/core-next/capability-parity.md), and historical investigations retain their original source versions under `research/`.
