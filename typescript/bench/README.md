# public-vfs 基准

B0 引入 `/public` 与现状 VFS 的合成基准，供 B1/B10 与 C9 共用（DESIGN §12.3）。只生成固定种子的本地合成数据，经真实插件装配驱动会话 API 与 bash；不调用 LLM，不设资源上限。

## 运行

经 `W/tools/run.py` 执行（需要构建产物 `.build/`）：

```
python3 tools/run.py wt/vfs/typescript evidence/logs/vfs-b0-bench.log \
  bash -c 'node bench/public-vfs.mjs --actors 1000 --files 10000 --rows 20 --out /…/W/evidence/bench/public-vfs-baseline.json'
```

参数：`--actors`、`--files`、`--rows`（每文件行数）、`--concurrency`（默认 200）、`--samples`（统一覆盖采样数）、`--scope-files`（find/grep 的取样文件数，默认 2000，见下）、`--out`（JSON 报告路径）。数据由 `(files, rows, bucketSize)` 唯一决定，目录与文件行内容可重复生成。

## 现状模式（B0）

`mode: "current"`：数据放在迁移出来的 `bench_files` 表里，经一个测试用 information 来源 `bench.files` 和挂载 `/world/bench` 暴露给现有 VFS。测的是 42b32f5 的读路径：每次 stat 新开快照、`find` 递归 walk、`grep` 逐文件调用 `files()` 并逐文件 spawn rg、每条 bash 命令新建 Worker。

`find`/`grep` 只在前 `--scope-files` 个文件（`/world/bench/scope`）上取样：基线的 `session.find` 单次遍历 1 万文件超过 30 s 快照租约（实测 `SQLITE_SNAPSHOT_EXPIRED`），而 grep 是 O(N²/32) 重走，1 万文件需要小时级。报告用 `scale.findScopeFiles`/`grepScopeFiles` 与各操作的 `extra.scopeFiles` 标明取样范围；ls/stat/read/并发仍覆盖 1 万文件的完整数据集。B10 在修好下推后把 `--scope-files` 提到全量。

报告字段：`mode`、`generatedAt`、`scale`、`operations.{ls,stat,read,find,grep,bashStart,concurrent}`（各含 `samples/p50Ms/p95Ms/p99Ms/errors`，`snapshotOpens`）与 `counters.{rgProcesses,snapshotOpens,readerQueueWaitMs,renderCount,cacheHitRate,sqliteSnapshotBudgetErrors}`。现状模式没有 `/public` 渲染缓存，`renderCount`/`cacheHitRate`/`readerQueueWaitMs` 为 `null`。

`tests/b0-snapshot-storm.test.mjs` 固定了 17 个并发 `stat` 触发 `SQLITE_SNAPSHOT_BUDGET` 的现状缺陷，B0 阶段保持红色，B3a（U4）实现快照排队后转绿。

## 最终模式（B10）

B10 在 `mode` 中增加 `/public` 路径：同一生成器写入 public-store，经 `public` 挂载与缓存读，输出渲染次数、缓存命中率、rg 进程数与 reader 队列等待，并与本基线报告对比。当前实现遇到非 `current` 模式会显式报 `BENCH_MODE`。
