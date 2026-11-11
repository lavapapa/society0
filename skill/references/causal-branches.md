# 控制变量与平行世界

这份 reference 说明怎样在 Society0 里做“同一起点，只改一个因素”的比较，以及哪些结论不能从这种比较里推出来。所有命令都按当前内核能力写；做不到的地方会明确写成替代做法。

## 一、先确定比较设计

在动手分叉前，先写清四件事：

```text
来源运行：
完整点：
控制组配置：
处理组配置（只改一个因素）：
固定项：种子、主体、初始数据、tick 数、测量口径
结果变量：
```

“只改一个因素”不是口号。常见的错误是同时改提示、主体画像和推荐权重，最后把差异归给其中一个。组合干预可以研究，但要写成“组合条件 A 与组合条件 B 的比较”，不能声称分离了单个因素。

一个好的最小设计：

- 来源运行先走到一个完整点，这个点之前的历史在控制组和处理组中完全相同。
- 控制组从该点继续原配置。
- 处理组从同一点继续，只改一个配置、一个输入或一个参数。
- 两边使用相同种子、相同主体、相同初始数据、相同 tick 数和相同测量题项。
- 比较点选在干预之后，且两边都到达相同业务时间。

## 二、内核实际支持什么

### 能做的

- 从源运行的一个完整点创建目标运行。内核的 `runBranch` 服务提供 `plan`、`create`、`probe` 三个入口；`@society0/host/run-branch` 是公开入口。
- 目标运行拥有自己的 world 目录、generation 和 control sequence，但沿用源 lineage 的 attempts authority。
- 目标 profile 是完整 profile，不是补丁片段；目标可以继续推进，也可以只观察。
- 分叉计划记录源随机选择与目标随机选择；默认 `seedPolicy: "preserve-seed-new-run-address"` 要求两边种子一致。
- 目标目录会写入 `branch-origin.json`，其中保存 operationId、源/目标身份和计划日志位置。
- `probe(operationId)` 可以查询已创建、创建中、失败或未知的操作；创建异常时先 probe，不要直接重发。
- 可以从历史完整点创建，而不是只能用当前点；示例脚本的 `--point-id` 接受完整点 id。

### 不能直接做的

- `society0` CLI 没有 `branch` 子命令。分叉通过发行包附带的 `examples/run-profile-branch.mjs` 或宿主 `runBranch` 服务完成。
- 分叉创建的目标目录没有 `entry-status.json` 和 `entry-result.json`，因此 `society0 run status/result --dir <目标目录>` 会报 `RUN_NOT_FOUND`。目标运行的结果要从分叉脚本输出的 JSON 读取，或用 `run-profile.mjs --reopen --observe` 重新打开。
- 示例脚本固定使用 `preserve-seed-new-run-address`，没有“换种子再分叉”的命令行开关。要研究不同种子，通常新建不同种子的来源运行，再分别分叉；也可以在自写脚本里用 `runBranch.plan` 的 `explicit-seed-new-run-address` 策略，但这需要自己写脚本，不是现成命令。
- 分叉固定的是业务历史、主体和配置，不是模型提供方的随机采样。LLM 主体在目标运行里仍会重新请求模型，输出可能不同。

## 三、准备来源运行

### 1. 写一个有余量的来源 profile

来源 profile 至少要有两个 tick：第一个 tick 用于建立共同起点，后面的 tick 留给目标运行。示例最小实验有三轮，来源只跑第 1 轮：

```sh
society0 check --profile profile.json --json
society0 run start --profile profile.json --dir runs/source --ticks 1 --detach --json
```

`--ticks 1` 是上限，来源在完成第 1 个 tick 后停止，后面的 proposal 仍留在来源配置里。目标运行从第 1 个完整点继续时，会接着执行后面的 proposal。

### 2. 等来源到达完整点

```sh
society0 run status --dir runs/source --json
society0 run result --dir runs/source --json
```

状态必须是 `completed` 或至少有一个已发布的完整点。若来源被中断，先看 `lastReason` 和 `completedTicks`；没有完整点时不要分叉，先恢复或重建来源。

### 3. 找到完整点 id

示例脚本默认使用来源当前的完整点，不需要手动传 id。要指定更早的点，用 `--point-id`：

- 运行目录的 `history/points/` 下，每个 JSON 文件名就是完整点 id。
- 分叉输出里的 `sourcePoint.id` 也是同一个 id。
- 自写脚本可以用 `history.describe(pointId)` 或 `history.current()` 取得点信息。

## 四、创建控制组和处理组

以下命令假设发行包附带 `examples/run-profile-branch.mjs`，并且当前目录能解析到已安装的 `@society0` 包。`profile.json` 是控制组 profile，`profile-correction.json` 是只改第二轮刺激材料的处理组 profile。

### 控制组

```sh
node examples/run-profile-branch.mjs \
  --profile profile.json \
  --directory runs/source \
  --run-id source \
  --lock module-lock.json \
  --target-profile profile.json \
  --target-directory runs/branch-control \
  --target-run-id branch-control \
  --advance-target \
  > runs/branch-control.json
```

### 处理组

```sh
node examples/run-profile-branch.mjs \
  --profile profile.json \
  --directory runs/source \
  --run-id source \
  --lock module-lock.json \
  --target-profile profile-correction.json \
  --target-directory runs/branch-correction \
  --target-run-id branch-correction \
  --advance-target \
  > runs/branch-correction.json
```

要点：

- `--profile`、`--directory`、`--run-id` 指向来源运行。
- `--target-profile` 是目标完整 profile；示例会在它的 `patches` 末尾补上目标地址，不改你写的业务配置。
- `--target-directory` 必须是新目录；目标目录已存在时创建失败。
- `--advance-target` 让目标继续推进。不传时只创建并观察目标，不推进。
- 事件驱动的目标还要传 `--target-events <events.json>`，事件偏移必须大于选定完整点并递增。
- 每次分叉都要保留输出的 operationId 和目标目录；创建异常时先 probe。

### 读取目标结果

分叉脚本的标准输出是一份 JSON，包含：

- `branch.plan`：源/目标随机选择、完整点、目标配置、种子策略。
- `branch.receipt`：目标运行摘要和完整点。
- `branch.result`：目标推进结果。
- `branch.targetPoint`：目标完成后的完整点。
- `branch.observation`：主体表的只读观察，含 `id` 和 `attributes`。
- `branch.receipts`：行动回执。
- `branch.authority`：实际使用的 lineage authority。
- `branch.closed`：目标是否已关闭。

控制组和处理组各保存一份 JSON，再比较同一主体、同一时点的属性或回执：

```sh
node -e '
const fs = require("fs");
for (const file of ["runs/branch-control.json", "runs/branch-correction.json"]) {
  const report = JSON.parse(fs.readFileSync(file, "utf8")).branch;
  const rows = report.observation.rows.map(row => ({ id: row.id, attributes: JSON.parse(row.attributes) }));
  console.log(file, JSON.stringify({ point: report.targetPoint.id, completed: report.targetPoint.frontier.completedCount, rows }, null, 2));
}
'
```

如果要重新打开目标目录继续观察：

```sh
node examples/run-profile.mjs \
  --profile runs/branch-control/profile.json \
  --directory runs/branch-control \
  --run-id branch-control \
  --lock module-lock.json \
  --lineage-directory runs/lineages/source \
  --lineage source \
  --reopen --observe
```

分叉目标沿用源 lineage，所以必须显式给出源的 lineage 目录和 identity；不传时会报告 authority 不可用，而不是自动初始化。

## 五、种子的实际用法

### 种子在哪里

Core 的 `@society0/core/random` 使用 `philox4x32-10/v1`，配置字段是 `algorithm` 和 `seed`。默认种子是 `study-1`；公开 profile 示例使用 `public-example-1`。同一 run 内的随机地址由运行、主体、操作和流标识共同决定。

### 固定种子

分叉示例默认使用 `preserve-seed-new-run-address`。来源和目标 profile 的 `random.seed` 必须一致，否则计划阶段报 `BRANCH_SEED_POLICY_MISMATCH`。这正好满足“只改一个干预，种子固定”的要求。

### 换种子

要研究种子敏感性，不要在一个分叉里同时换种子和干预。做法是：

1. 为每个种子建立独立的来源运行。可以用 `--patches` 覆盖 `random` 配置：

```sh
cat > seed-2.json <<'JSON'
[{"id":"random","config":{"seed":"study-2"}}]
JSON

society0 run start --profile profile.json --dir runs/source-seed-2 \
  --ticks 1 --patches seed-2.json --detach --json
```

2. 在每个来源运行上分别建立控制组和处理组。
3. 按种子成对比较，再报告跨种子的方向是否稳定。

`--patches` 的每个元素是 `{id, config}`；`config` 替换该入口的整个配置对象，所以覆盖 `random` 时要把需要的字段写全。配置变化会影响结果，必须用新的 run。

### 不要做的事

- 不要在同一对控制/处理里同时改种子和干预，然后把差异归给干预。
- 不要把一次分叉当成一个样本。一个种子上的一个控制组和一个处理组只是一个配对比较。
- 不要让目标运行沿用来源的旧配置来“省事”；目标 profile 必须是完整、可检查的配置。

## 六、样本、重复与不确定性

### 样本量

分叉不是复制样本。每个来源运行只提供一个起点；目标运行是这个起点上的一个平行世界。要估计稳定性，需要多个独立来源和多个种子：

```text
种子 s1：来源 A1 -> 控制组 A1C / 处理组 A1T
种子 s2：来源 A2 -> 控制组 A2C / 处理组 A2T
种子 s3：来源 A3 -> 控制组 A3C / 处理组 A3T
```

报告时写清：

- 独立来源数、种子数、每个条件的运行数。
- 每个条件的完成 tick 数、失败和未完成数。
- 结果变量的均值、范围或分布，不要只报一个最好看的数。
- 差异是否在多个种子上方向一致；如果不一致，写清不一致本身。

### LLM 不确定性

LLM 主体的输出受提供方、模型版本、温度、提示、工具预算、上下文长度和截断影响。固定种子只固定 Society0 内部的随机流，不固定模型采样。因此：

- 同一配置重复运行仍可能不同。
- 模型自述的理由是定性线索，不是因果机制的证据。
- `stopReason=length`、请求预算耗尽、工具调用失败和 `incomplete` 要单独报告，不能混进成功样本。
- 比较时要保持模型、提供方、提示、`maxTokens`、请求预算和工具预算一致；只改研究因素。

### 测量口径

两边必须使用同一测量题项、同一编码规则、同一观察时点。若一边多跑了一个 tick，比较的就不是同一业务时间。若一边的主体在干预后没有完成必需行动，结果应标记为不完整，而不是当成“没有效果”。

## 七、结论怎么写

一个稳妥的结论句式：

```text
在固定主体、初始数据、种子和测量口径，且两边都到达第 N 个 tick 的条件下，
处理组相对控制组在结果变量 Y 上观察到 [方向/大小/分布差异]。
这次比较支持 [有限说法]；它不支持 [超出设计的说法]。
差异仍可能来自 [替代解释]；下一步用 [具体对照] 区分。
```

可以说的：

- 在这个模拟环境、这组主体和这个测量口径下，干预与结果变量的差异。
- 差异是否在多个种子和重复运行中保持方向。
- 哪些行动回执、状态变化或测量记录支持这个描述。

不能说的：

- 真实世界中的因果效应。
- 对未模拟群体、未模拟平台规则或未模拟时间尺度的推广。
- “模型说它因为 X 所以 Y”就等于 X 导致 Y。
- 一次运行的方向等于稳定机制。
- 组合干预可以拆成单个因素的效应。

## 八、做不到分叉时怎么办

如果当前安装没有 `runBranch` 或示例脚本不可用，只能退到“手动复制起点再分别运行”：

1. 复制同一份 profile、同一份 world 或初始化数据。
2. 固定相同种子、相同主体、相同 tick 数和相同测量。
3. 用新的 run 目录分别运行控制和处理条件。
4. 只比较从起点到同一业务时间的结果。

这种做法只在以下情况成立：

- 运行是确定性的，或者模型采样被完全固定。
- 起点之前没有隐藏状态、外部调用或时间依赖。
- 两边确实从同一初始条件开始。

对 LLM 运行，手动复制起点通常不能保证中间状态相同，因此不能声称是“从同一状态分叉”。此时应把差异写成“两个独立运行之间的差异”，并报告模型和采样带来的不确定性。

## 九、检查表

```text
[ ] 来源运行有已发布的完整点
[ ] 控制组和处理组从同一完整点创建
[ ] 只改了一个主因素；组合干预已写明
[ ] 种子、主体、初始数据、tick 数、测量口径固定
[ ] 两边都到达同一业务时间
[ ] 失败、未完成、预算耗尽单独统计
[ ] 多个种子或重复运行支持稳定性判断
[ ] 结论没有超出模拟环境和测量口径
[ ] 创建异常时已 probe，未直接重发
[ ] 目标结果从分叉 JSON 或 reopen 观察读取，未误用 CLI status/result
```
