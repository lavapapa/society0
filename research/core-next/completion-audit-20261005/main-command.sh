#!/usr/bin/env bash
set -eu
RUSTUP_TOOLCHAIN=1.95.0 uv sync --frozen --all-extras --python 3.12
uv pip install --python .venv/bin/python sqlite-vec==0.1.6
.venv/bin/python -m pytest tests/primary tests/e2e tests/experiments -m 'not real_e2e' --junitxml=research/core-next/completion-audit-20261005/main-full.xml
.venv/bin/python -m society0.kernel.runner --factory examples.core_next.conversation_pilot:build --config research/core-next/completion-audit-20261005/main-pilot-config.json --output research/core-next/completion-audit-20261005/main-pilot
.venv/bin/python -m society0.kernel.workbench --run research/core-next/completion-audit-20261005/main-pilot --version main-0e0b368 --steps 1 2 --actors a b c d --output research/core-next/completion-audit-20261005/main-workbench-payload.json
cd tools/workbench-template
npm ci
CORE_NEXT_WORKBENCH_PAYLOAD="$(pwd)/../../research/core-next/completion-audit-20261005/main-workbench-payload.json" npm test
