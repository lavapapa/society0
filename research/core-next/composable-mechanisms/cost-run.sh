#!/usr/bin/env bash
# 正式试验使用父会话确认的 detached 源码；所有写入位于独立输出目录。
set -euo pipefail
BASE="${BEFORE_SOURCE:-/Users/marvin/Documents/同花顺（2）/research/simulation/society0core}"
AFTER="${AFTER_SOURCE:?请设置已确认提交的detached基准工作树}"
EXPECTED="${AFTER_COMMIT:?请设置父会话确认的完整source commit}"
PYTHON='/Users/marvin/Documents/同花顺（2）/research/simulation/society0core/.venv/bin/python'
SCRIPT="$AFTER/benchmarks/core_composition_cost.py"
[[ "$(git -C "$BASE" rev-parse HEAD)" == '7320b13a7d8d58cbe116f9bbc407224fec05de3c' ]]
[[ "$(git -C "$AFTER" rev-parse HEAD)" == "$EXPECTED" ]]
[[ -z "$(git -C "$AFTER" branch --show-current)" ]]
[[ -z "$(git -C "$BASE" status --porcelain)" ]]
[[ -z "$(git -C "$AFTER" status --porcelain)" ]]
if [[ $# -gt 0 ]]; then
  OUT="$1"
  "$PYTHON" - "$OUT" "$BASE" "$AFTER" <<'PY'
from pathlib import Path
import sys
out=Path(sys.argv[1]).resolve()
for source in sys.argv[2:]:
    assert not out.is_relative_to(Path(source).resolve()), '输出目录必须位于源码工作树外'
assert not out.exists(), '输出目录已存在，请选择新目录'
out.mkdir(parents=True)
PY
else
  OUT="$(mktemp -d /tmp/society0-composition-cost.XXXXXX)"
fi
export PYTHONDONTWRITEBYTECODE=1
printf '输出目录：%s\n' "$OUT"
for n in 100 10000; do
  for mode in rr social social_embedding; do
    PYTHONPATH="$BASE/src:$BASE" "$PYTHON" "$SCRIPT" --mode "$mode" --history "$n" --root "$OUT/before-$mode-$n" --output "$OUT/before-$mode-$n.json"
    PYTHONPATH="$AFTER/src:$AFTER" "$PYTHON" "$SCRIPT" --mode "$mode" --history "$n" --root "$OUT/after-$mode-$n" --output "$OUT/after-$mode-$n.json"
    PYTHONPATH="$AFTER/src:$AFTER" "$PYTHON" "$SCRIPT" --compare "$OUT/before-$mode-$n.json" "$OUT/after-$mode-$n.json" --output "$OUT/compare-$mode-$n.json"
  done
done
[[ "$(git -C "$AFTER" rev-parse HEAD)" == "$EXPECTED" ]]
[[ -z "$(git -C "$AFTER" status --porcelain)" ]]
