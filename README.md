# Society0 Next

Society0 is a Python multi-agent simulation engine. Actors share one environment; plugins define information, actions and mechanisms. Rule and LLM drivers use the same interfaces, while explicit schedules control time and ordering. Complete steps preserve state, full Threads and results for analysis and recovery.

This is the **Next** release line. The [release notes](docs/release-next-2.0.0a1.md) identify this alpha; package metadata lives in [pyproject.toml](pyproject.toml). Next development uses `next`; the existing stable architecture remains on `stable/4.1`.

## Get started

Use Python 3.12 or later on macOS or Linux, [uv](https://docs.astral.sh/uv/getting-started/installation/) and Git. Check out the release source and install its locked dependencies:

```sh
git clone --branch next-v2.0.0a1 https://github.com/lavapapa/society0.git
cd society0
uv sync --frozen --all-extras
```

The source installation builds the independent native filesystem package when needed; prepare the Rust toolchain described in the [installation guide](docs/site/content/start/installation.md). Installing dependencies makes no model requests. Model endpoints and credentials belong to your explicit runtime environment.

Follow [the first run and recovery tutorial](docs/core-next/getting-started.md) to run two rule actors, read their results and resume in a new directory. **Run → Results → recovery works entirely through Python and requires no UI.** Rule examples need no model account. [Result analysis](docs/core-next/results-analysis.md) includes complete pagination and large-record reads.

## Public capabilities

- **Core:** Plugin composition, Actor/Driver, Information/Actions, Schedule/Runtime, RunPlan, SQL storage, complete-step recovery, Results and Observation.
- **Agent services:** full Thread history, LLM provider adapters, independent memory policies, file tools, workspace and optional shell.
- **Built-in plugins:** `plain`, multi-source `cognition`, `round_robin`, and all general Social plugins—relationships, publishing, interactions, notifications, trends, recommendation and cognition sources.

Start with the [rule and conversation examples](examples/core_next/README.md), then use the deterministic Social and composition examples to explore mechanisms without a remote model. The [Social guide](docs/site/content/guide/social.md) explains how to select and combine individual plugins.

## Documentation

The Chinese [handbook](https://society0-next.readthedocs.io/zh-cn/latest/) contains tutorials, configuration guides and Python API references. Its [source and build instructions](docs/site/README.md) provide a static site and offline package; signatures and tool schemas are generated from the checked-out source.

- [Configuration and RunPlan](docs/site/content/reference/runner.md)
- [Plugin composition](docs/site/content/guide/composition.md)
- [Results API](docs/site/content/reference/results.md) and [Observation API](docs/site/content/reference/observation.md)
- [Recovery and storage](docs/site/content/guide/storage.md)
- [Project boundaries](PROJECT.md)

**Design inspiration.** Next draws on [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness), its Cordis-powered plugin architecture and [*A Programming Paradigm for Spatiotemporal Composability*](https://arxiv.org/abs/2608.25512). [Shared environment and composition](docs/site/content/concepts/environment.md) describes how Next applies explicit dependencies and resource lifecycles.
