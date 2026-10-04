# 运行、观察与分析

正式运行保存代码/依赖身份、研究配置、模型请求参数与公开 RunContract。runner.json 是运行合同，runner-status.json 是轻进度，timings.jsonl 记录稳定阶段数字。密钥来自环境，使用独立新目录保存每次尝试。

## 读取运行

Observation 提供 status、list_threads、thread_tail、原文范围、result_phases/result_page、resource_usage、action_summary。实时诊断与已完成步骤分别读取。固定历史视图通过 prepare_complete 准备；巨大原文使用引用完整续读，保留总数与继续位置。Memory/Thread/业务 SQL 与工作区引用在同一个完整点恢复。

工作台由 kernel.workbench 从实际运行与结果引用生成静态数据；它不执行研究计划，也不把浏览器草稿直接作为已应用配置。研究配置变更生成新版本并经明确运行请求产生新工件。原始 Thread 的信息暴露与测量结果分别解释。

## 解释成本

区分模型端等待、记忆检索、领域计算、权威存储与展示导出；资源用量的未知值不推算为零。Activation、工具和物理调用计时有包含关系，避免重复累加。范围读取和按需字段保留完整内容的继续访问路径；显式全人口分析与全图构建仍按请求规模计成本。

## Quantitative Analysis

Anchor each explanation to the comparison actually made. Report group sizes, observed values and uncertainty before proposing a mechanism. Agent reasons are self-reported explanations that suggest hypotheses; repeated mentions of one cue do not establish that it caused the outcome or suppressed another cue. A small or absent difference also leaves multiple explanations open.

For each proposed follow-up, state the target factor, what varies, what stays fixed, and which contrast tests the claim. For example, comparing two messages both forwarded by a familiar person while changing official endorsement tests the endorsement condition. To study familiarity at a fixed endorsement level, compare familiar and unfamiliar senders under that same level; to study how the two factors interact, cross both factors. Choose follow-ups to distinguish explanations, including a possible null result, and agree the design with the researcher before running.

Keep the underlying message content fixed when isolating a source cue. Adding an official debunk changes both the authority cue and the corrective information; that comparison measures a combined treatment, not an isolated familiarity or authority effect.

Before handing off an analysis, recompute group totals and derived estimates, and check that each claimed inference follows from its comparison. A rule baseline needs a justified substantive meaning; exceeding an arbitrary fixed score does not establish a reasoning mechanism. An effect disappearing in a repeat weakens evidence for stability, without by itself proving the original difference was noise. State the uncertainty and competing explanations that remain.

Typical checks:

- trends over ticks.
- treatment/control differences.
- persona or group differences.
- missing/failed agent calls.
- variance across repeated runs.
- for recommendation experiments: active pool size, pruning thresholds, scoring weights, final displayed post count, and exposure/impression counts.

Minimal pandas pattern:

```python
import json
import pandas as pd
from pathlib import Path

run_dir = Path("runs/demo")
metrics = pd.DataFrame(json.loads(line) for line in (run_dir / "metrics.jsonl").read_text().splitlines())
```

For tables inside steps:

```python
rows = []
for line in (run_dir / "steps.jsonl").read_text().splitlines():
    item = json.loads(line)
    for row in item["result"].get("tables", {}).get("survey", []):
        rows.append({"step": item["step"], **row})
survey = pd.DataFrame(rows)
```

## Qualitative Analysis

LLM-agent simulation often produces important qualitative material:

- stated reasons.
- generated comments.
- memories cited.
- interview answers.
- failed or surprising cases.

Code outputs can be coded inductively, but the researcher should review categories. Do not outsource substantive interpretation entirely to another LLM without audit.

## Report Shape

Recommended report:

1. Research question.
2. Simulation design: agents, environment, providers, steps, conditions.
3. Measurements and output schemas.
4. Quantitative results.
5. Qualitative patterns.
6. Robustness checks.
7. Limitations and next run.

Always mention model/provider dependence, prompt sensitivity, and the distinction between simulation outputs and empirical observation.

For `social_network` recommendation studies, explicitly report the recommendation condition: `full_scan_until`, pruning settings, chronological/engagement/similarity/network weights, whether embedding similarity was enabled, and `post_count`. These settings shape exposure and should be interpreted like experimental design choices.
