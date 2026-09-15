"""从新合成的增长状态测量 V4 根发布和独立恢复；不读取历史实验。"""
from __future__ import annotations

import argparse
import gc
import json
import resource
import sys
from pathlib import Path
from time import perf_counter

from society0.incremental_checkpoint import SealedTickDelta, V4CheckpointStore


def rss_bytes():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def row(i):
    return {"id": f"row-{i}", "amount": i, "ratio": i / 7,
            "large_int": 10 ** 100 + i, "text": "经营记录" * 40,
            "items": [i, None, True, {"value": -i}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["generate", "restore", "manager-root"])
    parser.add_argument("root", type=Path)
    parser.add_argument("--rows", type=int, default=20000)
    parser.add_argument("--step", type=int, default=3)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--stdlib-reader", action="store_true")
    args = parser.parse_args()
    if args.baseline:
        # 使用当前提交中的原方法，限定恢复读取路径的同数据对照。
        import ast
        import subprocess
        import society0.incremental_checkpoint as checkpoint
        source = subprocess.check_output(
            ["git", "show", "HEAD:src/society0/incremental_checkpoint.py"], text=True)
        tree = ast.parse(source)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                   and n.name == "V4CheckpointStore")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                      and n.name == "_read_json_component")
        namespace = dict(vars(checkpoint))
        exec(compile(ast.Module(body=[method], type_ignores=[]), "baseline", "exec"), namespace)
        V4CheckpointStore._read_json_component = namespace[method.name]
    if args.stdlib_reader:
        import gzip
        import io
        def read_component(self, relative, expected_sha256, component):
            path = self.root / relative
            if not path.is_file():
                raise FileNotFoundError(f"{component} missing: {relative}")
            raw = path.read_bytes()
            if self._sha256(raw) != expected_sha256:
                raise ValueError(f"{component} content hash mismatch: {relative}")
            if path.suffix == ".gz":
                try:
                    with gzip.open(io.BytesIO(raw), "rt", encoding="utf-8") as handle:
                        return json.load(handle)
                except OSError as exc:
                    raise ValueError(f"{component} gzip is invalid: {relative}") from exc
            return json.loads(raw)
        V4CheckpointStore._read_json_component = read_component
    store = V4CheckpointStore(args.root)
    if args.mode == "manager-root":
        import asyncio
        from society0.core_data import World
        from society0.persistence import PersistenceManager
        from society0 import persistent_state_schema, replaceable_map
        from society0.incremental_checkpoint import PersistenceSchema
        world = World(step=0, event_log_path=str(args.root / "events.jsonl"))
        world.environment_data["type"] = "plain"
        world.environment_data["state"] = {"records": {str(i): row(i) for i in range(args.rows)}}
        manager = PersistenceManager(str(args.root))
        schema = PersistenceSchema.compile(persistent_state_schema(records=replaceable_map()),
                                           root_path=("environment", "state"))
        started = perf_counter()
        manager.configure_v4(world, schema)
        configure_seconds = perf_counter() - started
        started = perf_counter()
        asyncio.run(manager.publish_root(world, schedule=object()))
        publish_seconds = perf_counter() - started
        publish_peak = rss_bytes()
        manager.close()
        world.event_logger.close()
        result = {"manager_configure_seconds": configure_seconds,
                  "manager_root_publish_seconds": publish_seconds,
                  "manager_publish_peak_rss_bytes": publish_peak}
    elif args.mode == "generate":
        rows = {str(i): row(i) for i in range(args.rows)}
        entries = [{"path": ["records"], "operation": "set", "value": rows,
                    "sequence": 0}]
        before = rss_bytes()
        started = perf_counter()
        store.publish_root(entries)
        root_seconds = perf_counter() - started
        del rows, entries
        gc.collect()
        growth_seconds = []
        for step in range(1, args.step + 1):
            start = args.rows + (step - 1) * (args.rows // 4)
            entries = tuple({"path": ["records"], "operation": "map_create",
                             "id": str(i), "value": row(i), "sequence": i - start}
                            for i in range(start, start + args.rows // 4))
            started = perf_counter()
            store.publish(SealedTickDelta(step=step, replacements=(), appends=entries))
            growth_seconds.append(perf_counter() - started)
        result = {"root_publish_seconds": root_seconds, "growth_publish_seconds": growth_seconds,
                  "peak_rss_before_publish_bytes": before}
    else:
        started = perf_counter()
        state = store.restore(args.step)
        elapsed = perf_counter() - started
        restore_peak = rss_bytes()
        expected = args.rows + args.step * (args.rows // 4)
        assert len(state["records"]) == expected
        assert all(state["records"][str(i)] == row(i) for i in range(expected))
        assert type(state["records"]["1"]["large_int"]) is int
        assert type(state["records"]["1"]["ratio"]) is float
        result = {"restore_seconds": elapsed, "restored_rows": expected,
                  "restore_peak_rss_bytes": restore_peak, "baseline": args.baseline,
                  "stdlib_reader": args.stdlib_reader}
    result.update(mode=args.mode, rows=args.rows, step=args.step,
                  peak_rss_bytes=rss_bytes(), python=sys.version.split()[0])
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
