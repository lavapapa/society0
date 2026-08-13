"""Checkpoint v4 的历史增长与固定 delta 性能门禁。

这里不设绝对墙钟阈值。墙钟样本只写入 benchmark JSON；门禁观察写入量、
记录数、历史组件读取和子进程 ``ru_maxrss`` 增量是否随 H 线性增长。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from statistics import median

from society0.incremental_checkpoint import (
    PersistenceKind,
    StateDeltaJournal,
    V4CheckpointStore,
)


BENCHMARK = Path(__file__).resolve().parents[2] / "benchmarks" / (
    "v4_incremental_checkpoint_benchmark.py"
)
PRICE = ("environment", "state", "price")
FACTS_BY_ID = ("environment", "state", "facts_by_id")
FACTS = ("environment", "state", "facts")


def _run_report(tmp_path: Path, *extra: str) -> dict:
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        str(BENCHMARK),
        "--output",
        str(output),
        *extra,
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True)
    assert completed.stdout.strip(), "benchmark must emit its JSON report"
    return json.loads(output.read_text(encoding="utf-8"))


def test_history_ab_report_keeps_fixed_delta_and_rss_bounded(tmp_path):
    report = _run_report(
        tmp_path,
        "--history",
        "8",
        "80",
        "--ticks",
        "5",
        "--projection-bytes",
        "128",
        "--control-projection-bytes",
        "512",
    )

    assert report["schema_version"] == "v4_incremental_checkpoint_benchmark_v1"
    cases = report["cases"]
    assert [case["history_ticks"] for case in cases] == [8, 80]
    assert all(case["history_component_reads"] == 0 for case in cases)
    assert all(case["ru_maxrss_delta_bytes"] >= 0 for case in cases)

    # 固定 delta 始终包含一个 replacement、一个 map fact 和一个 list fact；
    # 其记录数与历史 H 无关。
    for case in cases:
        assert case["fixed_delta_records"] == [3] * 5
        assert len(case["fixed_delta_bytes"]) == 5
        assert len(case["wall_time_ns"]) == 5
        assert all(value > 0 for value in case["fixed_delta_bytes"])
        assert all(value >= 0 for value in case["wall_time_ns"])

    low, high = cases
    low_bytes = median(low["fixed_delta_bytes"])
    high_bytes = median(high["fixed_delta_bytes"])
    # H 放大 10 倍时，固定 delta 的输出允许有 UUID/manifest 时间等小幅
    # 噪声，但不应出现同阶的线性放大。
    assert high_bytes <= low_bytes * 2.5 + 512

    low_rss = low["ru_maxrss_delta_bytes"]
    high_rss = high["ru_maxrss_delta_bytes"]
    if low_rss:
        assert high_rss <= low_rss * 8
    else:
        # ru_maxrss 的粒度在不同平台不同；两次固定 delta 都没有触发新的
        # high-water page 时只能报告零，不能用绝对内存阈值制造假失败。
        assert high_rss >= 0

    comparison = report["ab_comparison"]
    assert comparison["history_component_reads"] == {"A": 0, "B": 0}
    assert comparison["records_median_ratio"] == 1.0


def test_large_replaceable_projection_is_an_explicit_positive_control(tmp_path):
    report = _run_report(
        tmp_path,
        "--history",
        "32",
        "--ticks",
        "4",
        "--projection-bytes",
        "64",
        "--control-projection-bytes",
        "2048",
    )
    control = report["replaceable_projection_control"]
    assert control["large_R"] > control["small_R"]
    assert control["large_fixed_delta_bytes_median"] > control[
        "small_fixed_delta_bytes_median"
    ]
    assert "positive control" in control["expected"]


def test_fixed_delta_publish_does_not_read_historical_components(tmp_path, monkeypatch):
    """直接拦截历史段/替换文件读取，避免只依赖计数器自报。"""

    store = V4CheckpointStore(tmp_path)
    store.publish_root(
        (
            {"path": list(PRICE), "operation": "set", "value": 0, "sequence": 0},
            {"path": list(FACTS_BY_ID), "operation": "set", "value": {}, "sequence": 1},
            {"path": list(FACTS), "operation": "set", "value": [], "sequence": 2},
        ),
        metadata={"run_id": "performance-read-guard"},
    )
    declarations = {
        PRICE: PersistenceKind.REPLACEABLE,
        FACTS_BY_ID: PersistenceKind.APPEND_ONLY_MAP,
        FACTS: PersistenceKind.APPEND_ONLY_LIST,
    }
    journal = StateDeltaJournal(declarations)
    for step in range(1, 33):
        journal.begin_tick(step)
        journal.record_map_create(FACTS_BY_ID, f"history-{step}", {"step": step})
        store.publish(journal.seal_tick())

    historical_reads: list[Path] = []
    original_read_bytes = Path.read_bytes

    def guarded_read(path: Path) -> bytes:
        if path.parent in {store.segments_dir, store.replacements_dir}:
            historical_reads.append(path)
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", guarded_read)
    journal.begin_tick(33)
    journal.record_set(PRICE, 1)
    journal.record_map_create(FACTS_BY_ID, "fixed-map", {"step": 33})
    journal.record_append(FACTS, {"id": "fixed-list"})
    marker = store.publish(journal.seal_tick())

    assert historical_reads == []
    assert store.metrics["history_entries_read_while_publishing"] == 0
    assert marker["bytes_written"] > 0
