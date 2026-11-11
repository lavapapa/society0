# 因果分支

分支的目标是控制变量：从同一个完整点出发，建立几个只差一个干预或参数的 run，然后比较结果。Society0 的 RunBranch 从源 run 的完整点复制 SQL 状态、历史和 lineage 身份；源 run 不被修改，目标 run 有新的 run id、目录、generation 和 controlSequence。

## 内核实际能做什么

- 从源 run 的当前完整点或指定 `--point-id` 建立目标 run。
- 目标沿用源 SQL schema；分支例子传空 migration 列表，不在目标上重新建表。
- 目标复制源完整点的世界状态、Thread、结果和 artifacts 引用。
- 目标可以换 profile 配置，但必须保留相同的随机 seed（例子使用 `preserve-seed-new-run-address`）。
- 目标与源共享 lineage authority；目标保存自己的 `profile.json`，之后用同一 lineage 目录和 identity 重新打开。
- 目标可以只观察，也可以显式推进。规则目标用 `--advance-target`；模型目标需要额外授权；事件目标还需要提供新的目标事件批次。
- 分支操作有 plan、create、probe 三个步骤和持久 journal。创建异常时保留 operationId 和目标目录，先用 `runBranch.probe` 核实，不要直接重发。

## 不能做什么

- `society0` CLI 没有 branch 子命令。分叉由 `examples/run-profile-branch.mjs` 或公开 SDK 的 `forkProfile` 调用 RunBranch 完成。
- 不能从任意一次激活中间分叉，只能从已发布的完整点分叉。
- 不能用分支例子的 `preserve-seed-new-run-address` 改变 seed；目标 profile 的 `random.seed` 必须与源一致，否则 plan 报 `BRANCH_SEED_POLICY_MISMATCH`。
- 不能在目标 profile 里换掉建表插件或改 schema；目标沿用源 schema。
- 不能在目标里直接改源 run 的数据；源 run 是只读的。
- 不能把模型目标的真实请求当作已授权；例子默认返回 `MODEL_NOT_AUTHORIZED`。Codex 订阅目标返回 `CODEX_NOT_AUTHORIZED`。
- 不能只靠一次分支得出统计结论。一个完整点、一个 seed、一次运行只说明这次运行内的差异。

## 准备

在按[公开 SDK 使用](../typescript/docs/public-usage.md)安装好的 consumer 目录里操作，确保 `examples/`、`profiles/` 和 `module-lock.json` 都在当前目录。源 run 必须先完成并停止写入；分支例子会以只读方式重新打开源 run。

先确认源 run 的状态和最后一个完整点：

```sh
node_modules/.bin/society0 run status --dir runs/rule --json
node_modules/.bin/society0 run result --dir runs/rule --json
```

记下 `result.cut.id` 或 `state.frontier.completedCount`。不传 `--point-id` 时，例子使用重新打开源 run 后看到的当前完整点。

## 建立目标 profile

从源 profile 复制一份，只改要研究的那个因素：

```sh
cp profiles/rule-sqlite.json rule-fork.json
```

例如只改检查点频率，在 `patches` 末尾追加：

```json
{"id":"checkpoint-policy","config":{"every":2}}
```

其他字段保持原样。主体的 id、world 数据、seed 和数据集引用都来自源完整点，不要在目标 profile 里重新定义这些内容。

## 执行分叉

```sh
node run-profile-branch.mjs \
  --profile profiles/rule-sqlite.json \
  --directory runs/rule \
  --run-id rule \
  --lock module-lock.json \
  --target-profile rule-fork.json \
  --target-directory runs/rule-b \
  --target-run-id rule-b
```

参数：

- `--profile`、`--directory`、`--run-id`：源 run 的 profile、目录和 id。
- `--lock`：模块锁；分支例子把它写进目标 profile。
- `--target-profile`：目标 profile。
- `--target-directory`：目标 run 目录，必须尚未使用。
- `--target-run-id`：目标 run id，不能与源相同。
- `--point-id`：可选，指定源完整点。
- `--lineage-directory`、`--lineage`：可选，指定共享 authority；默认沿用源 lineage。
- `--advance-target`：显式推进目标；不传时只观察。
- `--target-events`：事件目标推进时提供的新事件 JSON 数组。
- `--diagnostics`：可选，写分支期间的 history 采样。

实测输出是一个 JSON，关键字段在 `branch` 下：

- `branch.plan.operationId`：本次分支操作 id。
- `branch.sourcePoint.id`：源完整点 id。
- `branch.targetPoint.id`：目标根完整点 id。
- `branch.targetPoint.runId`、`generation`、`controlSequence`：目标身份；示例中 generation 从 `g1` 变为 `g2`，controlSequence 从 `0` 变为 `1`。
- `branch.actorCount`：目标主体数，规则示例为 2。
- `branch.authority`：共享 lineage 目录和 identity。
- `branch.status.state`：目标重新打开后的状态，实测为 `clean`。
- `branch.closed`：目标是否已关闭。

目标目录里会生成 `profile.json`、`branch-origin.json`、`world.sqlite`、`history/` 和 `artifacts/`。

## 观察或推进目标

只观察：

```sh
node run-profile.mjs \
  --profile runs/rule-b/profile.json \
  --directory runs/rule-b \
  --run-id rule-b \
  --lock module-lock.json \
  --lineage-directory runs/lineages/rule \
  --lineage rule \
  --reopen --observe
```

输出的 `reopened` 为 `true`，`result` 为 `{"status":"observation-only","advanced":false}`，`cut.id` 与分支输出的 `targetPoint.id` 相同。

推进规则目标：

```sh
node run-profile-branch.mjs \
  --profile profiles/rule-sqlite.json \
  --directory runs/rule --run-id rule \
  --lock module-lock.json \
  --target-profile rule-fork2.json \
  --target-directory runs/rule-c --target-run-id rule-c \
  --advance-target
```

实测目标以 `result.status: "completed"` 结束，frontier 的 `completedCount` 为 `1`。模型目标需要 `forkProfile` 的 `allowModelRequests` 和授权配置；事件目标的 `--target-events` 必须包含新事件 id，offset 严格大于源完整点且递增，time 沿持久 moment 非递减。

## 做多个平行世界

从同一个源完整点建立三个目标，只改一个因素：

```sh
# 目标 A：基准配置
node run-profile-branch.mjs ... --target-profile rule-a.json \
  --target-directory runs/rule-a --target-run-id rule-a

# 目标 B：改检查点频率
node run-profile-branch.mjs ... --target-profile rule-b.json \
  --target-directory runs/rule-b --target-run-id rule-b

# 目标 C：改一个规则参数
node run-profile-branch.mjs ... --target-profile rule-c.json \
  --target-directory runs/rule-c --target-run-id rule-c
```

三次操作都指向同一个 `--directory`/`--run-id`/`--point-id`，目标目录和 run id 不同。每次都用同一份 lockfile，并在目标 profile 里保留源的 seed、主体和数据集引用。

## 比较结果

目标完成后，读取 `run result` 或重新打开目标：

- `result.status`、`result.reason`：是否完成，为什么结束。
- `result.cut.frontier`：推进边界。
- `activations.total/completed/failed`：激活是否完成。
- 测量记录和 Social 内容：按主体或群体分组比较。
- `branch.authority`、`targetPoint.id`：确认比较的是同一源点下的目标。

比较时把“源完整点 + 目标 profile 的差异 + 目标结果”放在一起。只展示汇总数字，会掩盖哪些主体或群体发生了变化。

## 可信边界

- **同一性**：分支复制的是源完整点。源 run 在分叉后继续推进不会影响已创建的目标。
- **随机性**：例子保留源 seed，因此随机流的起点一致；但模型调用和外部服务仍可能带来非确定性。不要把一次模型 run 的差异全部归因于配置。
- **样本量**：一个源点、一个 seed 只产生一次对照。要扩大结论，需要多个独立 run 或多个完整点，并报告分布而不是单点。
- **改动数量**：一次只改一个因素。改两个以上时，无法从结果区分是哪一个造成的。
- **schema**：目标沿用源 schema。跨 schema 版本的比较不是分支，需要单独建 run。
- **外部数据**：目标引用源完整点里的数据集和 artifacts。保留源 lineage 与 artifacts 目录；不要在比较完成前清理。

## 没有分支脚本时

如果当前环境没有 `run-profile-branch.mjs` 或 RunBranch 服务，只能手动复制起点：

1. 复制源 profile、world 文件和数据集到新目录。
2. 用相同的 seed、主体和初始数据分别启动独立 run。
3. 每个 run 使用自己的目录和 lineage。

这仍然是控制变量，但它不是从同一个完整点复制：各 run 的历史身份不同，也没有共享 lineage。报告时要写明这是“独立重复运行”，不要把它描述成同点分支。
