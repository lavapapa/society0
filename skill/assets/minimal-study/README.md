# 最小规则实验：一条通知的两种后续

这个示例是一个可运行的规则驱动研究：

- 两名主体：`alice` 和 `bob`。
- 三个 tick：第 1 轮 Alice 收到官方通知；第 2 轮 Bob 收到同一通知或一条更正；第 3 轮 Alice 形成一个后续状态。
- `profile.json` 是对照组：Bob 收到官方通知。
- `profile-correction.json` 是处理组：只把第 2 轮 Bob 收到的材料换成更正，其余主体、种子、轮次和测量不变。

示例用 `plain.update` 改变主体属性，行动回执会记录谁在什么时点更新了什么。分叉比较时，比较 Bob 在第 2 轮之后的 `attributes`。

## 目录

```text
minimal-study/
  profile.json               对照组 profile
  profile-correction.json    只改第 2 轮材料的处理组 profile
  lock.mjs                   从已安装包生成 module-lock.json
  README.md
```

## 准备

把本目录复制到实验目录，并确保当前目录能解析到已安装的 `@society0` 包。通常是在安装目录里运行，或让实验目录的 `node_modules` 指向已安装包。

```sh
node lock.mjs
```

这会生成 `module-lock.json`。profile 里的 `lockfile` 指向 `./module-lock.json`。

如果使用发行包附带的 `examples/profile-lock.mjs`，等价命令是：

```sh
node examples/profile-lock.mjs module-lock.json \
  @society0/host @society0/core @society0/extras @society0/social
```

## 检查

```sh
society0 check --profile profile.json --json
```

预期：

```json
{"schema":"society0.check/1","ok":true,"missing":[],"warnings":[]}
```

`check` 只做静态装配，不创建 run，也不推进 tick。

## 运行来源

来源只跑第 1 轮，把第 2、3 轮留给分叉目标：

```sh
society0 run start --profile profile.json --dir runs/source --ticks 1 --detach --json
```

轮询状态，直到 `state` 为 `completed`：

```sh
society0 run status --dir runs/source --json
```

预期 `completedTicks` 为 1，`lastReason` 为 `max ticks reached (1)`。

读取运行摘要：

```sh
society0 run result --dir runs/source --json
```

`result.result.status` 应为 `completed`，`activations.completed` 至少为 1。

## 恢复

`run resume` 从已发布的完整点继续，只对暂停或中断的 run 有意义：

```sh
society0 run resume --dir runs/source --detach --json
```

对已经完成的来源，命令会返回 `RUN_COMPLETED`，这是预期行为，不是失败。如果来源在根创建完成前被中断，可能没有 authority，此时保留目录和错误，重新建立来源，不要反复 resume。

要观察一次真正的暂停与恢复：

1. 让来源运行更长时间，或在启动后发送 `SIGTERM`。
2. 等 `run status` 显示 `paused` 且 `completedTicks` 大于 0。
3. 再运行 `society0 run resume --dir runs/source --detach --json`。
4. 轮询到 `completed`。

## 分叉对照

以下命令假设发行包附带的 `examples/run-profile-branch.mjs` 在当前目录或可访问路径。来源已完成后，从它的当前完整点创建两个目标。

对照：

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

处理：

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

两个目标都从同一个来源完整点开始，种子保持 `public-example-1`，只改第 2 轮 Bob 收到的材料。

## 读取对照结果

```sh
node -e '
const fs = require("fs");
for (const file of ["runs/branch-control.json", "runs/branch-correction.json"]) {
  const report = JSON.parse(fs.readFileSync(file, "utf8")).branch;
  const rows = report.observation.rows.map(row => ({ id: row.id, attributes: JSON.parse(row.attributes) }));
  console.log(file, JSON.stringify({ completed: report.targetPoint.frontier.completedCount, rows }, null, 2));
}
'
```

预期 Bob 的属性：

- 对照组：`belief: "fare-rise"`，`heard: "official-notice"`。
- 处理组：`belief: "no-fare-change"`，`heard: "correction"`。

Alice 的第 3 轮状态两边相同，用来检查其余部分是否保持一致。

分叉目标没有 `entry-status.json`，所以不要对目标目录运行 `society0 run status/result`。结果从上面的 JSON 读取。

需要重新打开目标继续观察时：

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

分叉目标沿用来源 lineage，必须显式给出源 lineage 目录和 identity。

## 这个示例说明什么

- 规则 profile 能完成完整的 `check -> run start -> status -> result -> resume` 流程。
- 同一起点、同一种子、同一主体和同一测量下，只改一个刺激材料，可以观察到主体状态的差异。
- 一次分叉只是一个配对比较；要判断稳定性，需要多个种子和多个来源运行。

## 这个示例不说明什么

- 不说明真实世界中的因果效应。
- 不说明 LLM 主体会给出相同结果。
- 不说明只改一轮材料就能解释长期态度变化。
- 不说明一个种子的结果可以推广到其他种子。
