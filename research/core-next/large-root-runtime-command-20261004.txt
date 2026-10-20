set -eu
cd /tmp/society0-core-next-20261004/v04-source
export PYTHONPATH=.:src:/tmp/society0-core-next-20261004/deps
V04PY=/mnt/data/l20/qin/deploy/lithium-accepted-v0.7.2-20260922/venv/bin/python
V04BASE=/tmp/society0-core-next-20261004
V04PROBE=benchmarks/core_next_large_root_probe_runtime.py
"$V04PY" "$V04PROBE" --mode read --run "$V04BASE/v04-root" > "$V04BASE/v04-read.json"
"$V04PY" benchmarks/core_next_v04_diag.py > "$V04BASE/v04-diag.json"
"$V04PY" "$V04PROBE" --mode restore --run "$V04BASE/v04-root" --target "$V04BASE/v04-whole" > "$V04BASE/v04-fork-whole.json"
"$V04PY" "$V04PROBE" --mode whole --run "$V04BASE/v04-whole" > "$V04BASE/v04-whole.json"
"$V04PY" "$V04PROBE" --mode restore --run "$V04BASE/v04-root" --target "$V04BASE/v04-split" > "$V04BASE/v04-fork-split.json"
"$V04PY" "$V04PROBE" --mode split --run "$V04BASE/v04-split" > "$V04BASE/v04-split.json"
"$V04PY" "$V04PROBE" --mode restore --run "$V04BASE/v04-split" --target "$V04BASE/v04-restored" > "$V04BASE/v04-restore.json"
"$V04PY" "$V04PROBE" --mode read --run "$V04BASE/v04-restored" > "$V04BASE/v04-restored-read.json"
printf done > "$V04BASE/v04-runtime.done"
