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

B10 增加 `mode: "public"` 路径：同一生成器写入 public-store（`/public/bench/files/<bucket>/<file>.jsonl`，100 桶 × 100 文件），经 `public` 挂载、moment-views 冻结视图与渲染缓存读取；另装配 `public-self`（`/self/profile.json`）和 exploration-shell 池。`--mode current` 仍是现状 `/world` 基线，用于对照。

```
node bench/public-vfs.mjs --mode public --actors 1000 --files 10000 --rows 20 \
  --concurrency 200 --moments 100 --change-percent 1 --checkpoints 1,10,100 \
  --directory /var/tmp/society0-next-b10-<时间> \
  --out ../evidence/bench/public-vfs-final.json
```

最终报告在原字段之外增加：

- `operations.{ls,stat,read,find,grep,bashStart,selfRead,concurrent}`；`grep.extra.coldMs`/`regexMs`、`concurrent.extra.throughputPerSecond`/`readerQueueWaitMs`/`sqliteSnapshotBudgetErrors`。
- `coldRender`：1 万行文件空缓存首次读取；`write1000`：1 事务 upsert 1000 行（含 FTS）；`share`：N 个 agent 读同一文件的渲染次数。
- `history.checkpoints[]`：第 1、10、100 个 moment 的 ls/stat/read/find/grep 与当时的 SQLite 文件大小。
- `database`（main/wal/shm 字节）、`memory`（峰值 RSS、maxRSS、运行时 load average）、`counters`（渲染次数、缓存命中率、rg 进程数、快照打开次数、reader 队列等待、`SQLITE_SNAPSHOT_BUDGET` 次数）。
- `config` 与 `snapshotLeaseSource`：快照租约 30 s 的实际来源。

### 单项 profile

`--focus ls|read|find|cold|concurrent|history-find --profile-out <file.cpuprofile>`：装配与播种完成后，只对目标操作循环，用 `node:inspector` 程序化启停 profiler，避免把播种噪声算进来。`--focus-iterations` 默认 30；`cold` 固定 1 次，`concurrent` 固定跑一次 200 会话混合负载，`history-find` 先推进 `--moments` 个 moment 再测 find。

其他基准参数：`--note-pad`（每行 note 追加字符数，用于 2M 行/400 MB 规模）、`--mix read|mixed`（并发只读或混合）、`--task-concurrency`（TaskSupervisor 并发，默认 256；默认 8 会限制 200 并发基准）、`--concurrent-sessions`（并发基准复用会话数，默认 24，报告 `operations.concurrent.extra.snapshotQueuePeak`）、`--reader-workers`、`--max-snapshots`、`--share-agents`。

### 快照过期探针

```
node bench/public-vfs.mjs --mode public --expiry-probe --shell-timeout-ms 120000 \
  --out ../evidence/bench/public-vfs-snapshot-expiry-120000.json
```

执行 `sleep 32; cat /public/...`，并在 session 上记录 RPC 错误。B10 r1 起会话租约读 `database.maxSnapshotAgeMs`，shell 以该值的 95% 作为命令超时并在取到 worker 后才开视图，因此长命令稳定以 `SHELL_TIMEOUT` 结束，不会出现 `SQLITE_SNAPSHOT_EXPIRED`；r0 的旧探针结果保留在 `public-vfs-snapshot-expiry-120000.json`。租约取值来源见 `snapshotLeaseSource`。

# Bench（C9）

与 B0 的 `bench/public-vfs.mjs` 共用目录与 JSON 报告字段约定：每份报告都有
`schemaVersion / mode / generatedAt / scale / operations / counters / durationMs`，
结果写到 `W/evidence/bench/`。所有基准只做本地合成数据，不调用 LLM，也不改资源上限。

| 脚本 | 内容 | 默认规模 |
|---|---|---|
| `rule-1k.mjs` | B1：规则驱动、每 tick 写 metrics.jsonl | 1000 主体 × 50 tick |
| `fake-llm-1k.mjs` | B2：fake provider，确定性延迟模型，不 sleep | 1000 主体 × 20 tick |
| `history.mjs` | B3：空步链发布、物化、分叉 | 200 / 1000 / 10001 点 |
| `perf-curve.mjs` | 曲线检测器：每个计数器线性 + 二次回归 | `--min-ticks` 默认 20 |
| `baseline-gate.mjs` | `baseline.json` 回归门槛（成本型指标退化 >10% 失败） | — |

运行：

```sh
node bench/rule-1k.mjs --out ../evidence/bench/b1.json
node bench/fake-llm-1k.mjs --out ../evidence/bench/b2.json
node bench/history.mjs --lengths 200,1000 --out ../evidence/bench/b3.json
node bench/perf-curve.mjs <metrics.jsonl> --out curve.json
```

`perf-curve` 的失败口径：

- 斜率相对前 5 个 tick 的均值超过 `maxSlopePerTick`（默认 2%/tick）；
- 或二次项为正且相对线性拟合的 R² 改善超过 0.2（疑似 O(T²)）；
- 报告 `fastest` 指出增长最快的计数器。

B2 使用 fake provider 的确定性延迟模型（`a + b×promptTok`，带 KV 超限惩罚），
只记录模型化延迟而不真实等待；`commitsPerActivation` 来自 fake driver 每激活
一个事务的事实，C1 计数器合并后应改为从 C0 指标读取。
