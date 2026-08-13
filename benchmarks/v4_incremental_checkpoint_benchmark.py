#!/usr/bin/env python3
"""Checkpoint v4 的 A/B 历史增长基准。

默认命令会为每个历史规模启动一个干净的子进程，再输出 JSON 报告：

    python benchmarks/v4_incremental_checkpoint_benchmark.py \
      --history 100 1000 10000 --ticks 100 \
      --output benchmarks/results/v4-history-ab.json

子进程只在固定 delta 发布阶段测 ``ru_maxrss`` 增量；历史构造阶段的内存
不会混入该指标。墙钟时间写入报告用于观察，脚本和测试不以绝对耗时作门禁。
``--projection-bytes`` 与 ``--control-projection-bytes`` 是预期对照：可替换
投影本身增大时，当前 Tick 的输出自然应该增大。
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from statistics import median
from typing import Any, Iterable


# 允许直接从 checkout 运行脚本，不要求先 pip install -e。
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from society0.incremental_checkpoint import (  # noqa: E402
    PersistenceKind,
    StateDeltaJournal,
    V4CheckpointStore,
)


PRICE = ("environment", "state", "price")
FACTS_BY_ID = ("environment", "state", "facts_by_id")
FACTS = ("environment", "state", "facts")


def _declarations() -> dict[tuple[str, ...], PersistenceKind]:
    return {
        PRICE: PersistenceKind.REPLACEABLE,
        FACTS_BY_ID: PersistenceKind.APPEND_ONLY_MAP,
        FACTS: PersistenceKind.APPEND_ONLY_LIST,
    }


def _root_entries() -> tuple[dict[str, Any], ...]:
    return (
        {"path": list(PRICE), "operation": "set", "value": 0, "sequence": 0},
        {"path": list(FACTS_BY_ID), "operation": "set", "value": {}, "sequence": 1},
        {"path": list(FACTS), "operation": "set", "value": [], "sequence": 2},
    )


def _projection(size: int, seed: int) -> str:
    """生成长度固定且不被 gzip 完全折叠的可替换投影。"""

    if size < 0:
        raise ValueError("projection size must be non-negative")
    # 十六进制计数器避免 ``'x' * size`` 带来的过度压缩；seed 让每个
    # benchmark case 的 payload 仍然是确定的。
    chunks = []
    counter = seed
    while sum(len(chunk) for chunk in chunks) < size:
        chunks.append(f"{counter:08x}")
        counter += 1
    return "".join(chunks)[:size]


def _build_history(root: Path, history: int) -> tuple[V4CheckpointStore, StateDeltaJournal]:
    store = V4CheckpointStore(root)
    store.publish_root(_root_entries(), metadata={"run_id": "v4-history-benchmark"})
    journal = StateDeltaJournal(_declarations())
    for step in range(1, history + 1):
        journal.begin_tick(step)
        journal.record_map_create(
            FACTS_BY_ID,
            f"history-{step}",
            {"payload": "h" * 128, "step": step},
        )
        store.publish(journal.seal_tick())
    return store, journal


def _rss() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux reports KiB.  Normalizing the unit keeps the
    # JSON comparable when the benchmark is run on either development host.
    if sys.platform == "darwin":
        return value
    return value * 1024


def run_worker(*, history: int, ticks: int, projection_bytes: int) -> dict[str, Any]:
    """在一个子进程中执行单个历史规模的固定 delta 测量。"""

    if history < 0 or ticks <= 0 or projection_bytes < 0:
        raise ValueError("history >= 0, ticks > 0 and projection_bytes >= 0 are required")

    with tempfile.TemporaryDirectory(prefix="society0-v4-benchmark-") as temporary:
        store, journal = _build_history(Path(temporary), history)
        bytes_written: list[int] = []
        records_written: list[int] = []
        wall_time_ns: list[int] = []

        # 历史构造已经完成。之后的 ru_maxrss 增量只对应固定 delta 发布。
        rss_before = _rss()
        for offset in range(ticks):
            step = history + offset + 1
            journal.begin_tick(step)
            journal.record_set(PRICE, _projection(projection_bytes, seed=offset))
            journal.record_map_create(
                FACTS_BY_ID,
                f"fixed-map-{offset}",
                {"payload": "fixed", "index": offset},
            )
            journal.record_append(FACTS, {"id": f"fixed-list-{offset}"})
            delta = journal.seal_tick()
            records_written.append(len(delta.replacements) + len(delta.appends))

            started = time.perf_counter_ns()
            marker = store.publish(delta)
            wall_time_ns.append(time.perf_counter_ns() - started)
            bytes_written.append(int(marker["bytes_written"]))
        rss_after = _rss()

        return {
            "history_ticks": history,
            "fixed_ticks": ticks,
            "projection_bytes": projection_bytes,
            "fixed_delta_bytes": bytes_written,
            "fixed_delta_bytes_median": median(bytes_written),
            "fixed_delta_records": records_written,
            "fixed_delta_records_median": median(records_written),
            "history_component_reads": int(
                store.metrics["history_entries_read_while_publishing"]
            ),
            "wall_time_ns": wall_time_ns,
            "ru_maxrss_delta_bytes": max(0, rss_after - rss_before),
        }


def _worker_subprocess(history: int, ticks: int, projection_bytes: int) -> dict[str, Any]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--worker",
        "--history",
        str(history),
        "--ticks",
        str(ticks),
        "--projection-bytes",
        str(projection_bytes),
    ]
    completed = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"benchmark worker returned non-JSON output: {completed.stdout!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("benchmark worker output must be a JSON object")
    return payload


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _ab_comparison(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if len(cases) < 2:
        return {"status": "single_case"}
    low, high = cases[0], cases[-1]
    return {
        "A": {"history_ticks": low["history_ticks"]},
        "B": {"history_ticks": high["history_ticks"]},
        "history_ratio": _ratio(high["history_ticks"], low["history_ticks"]),
        "bytes_median_ratio": _ratio(
            high["fixed_delta_bytes_median"], low["fixed_delta_bytes_median"]
        ),
        "records_median_ratio": _ratio(
            high["fixed_delta_records_median"], low["fixed_delta_records_median"]
        ),
        "rss_delta_ratio": _ratio(
            high["ru_maxrss_delta_bytes"], low["ru_maxrss_delta_bytes"]
        ),
        "history_component_reads": {
            "A": low["history_component_reads"],
            "B": high["history_component_reads"],
        },
        "interpretation": (
            "fixed-delta bytes/records/RSS should stay bounded as append-only history grows; "
            "wall time is observational only"
        ),
    }


def build_report(
    *,
    histories: Iterable[int],
    ticks: int,
    projection_bytes: int,
    control_projection_bytes: int,
) -> dict[str, Any]:
    normalized_histories = sorted(set(int(value) for value in histories))
    if not normalized_histories or normalized_histories[0] < 0:
        raise ValueError("at least one non-negative history size is required")
    cases = [
        _worker_subprocess(history, ticks, projection_bytes)
        for history in normalized_histories
    ]
    control_history = normalized_histories[-1]
    control_small = _worker_subprocess(control_history, ticks, projection_bytes)
    control_large = _worker_subprocess(control_history, ticks, control_projection_bytes)
    return {
        "schema_version": "v4_incremental_checkpoint_benchmark_v1",
        "configuration": {
            "histories": normalized_histories,
            "ticks": ticks,
            "projection_bytes": projection_bytes,
            "control_projection_bytes": control_projection_bytes,
            "rss_metric": "child ru_maxrss delta during fixed-delta publish",
            "wall_time_policy": "report only; no absolute threshold",
        },
        "cases": cases,
        "ab_comparison": _ab_comparison(cases),
        "replaceable_projection_control": {
            "history_ticks": control_history,
            "small_R": projection_bytes,
            "large_R": control_projection_bytes,
            "small_fixed_delta_bytes_median": control_small[
                "fixed_delta_bytes_median"
            ],
            "large_fixed_delta_bytes_median": control_large[
                "fixed_delta_bytes_median"
            ],
            "small_case": control_small,
            "large_case": control_large,
            "expected": (
                "larger replaceable projection R increases current-Tick output; "
                "this is an intentional positive control, not a history-complexity failure"
            ),
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--history", nargs="+", type=int, default=[0, 100, 1000])
    parser.add_argument("--ticks", type=int, default=20)
    parser.add_argument("--projection-bytes", type=int, default=256)
    parser.add_argument("--control-projection-bytes", type=int, default=4096)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.worker:
        payload = run_worker(
            history=args.history[0],
            ticks=args.ticks,
            projection_bytes=args.projection_bytes,
        )
    else:
        payload = build_report(
            histories=args.history,
            ticks=args.ticks,
            projection_bytes=args.projection_bytes,
            control_projection_bytes=args.control_projection_bytes,
        )
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    sys.stdout.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
