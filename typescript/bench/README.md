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
