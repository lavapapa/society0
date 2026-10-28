# 公开 SDK 使用

本页说明独立安装用户如何用公开包加载 profile、创建 SQLite run、推进并重开历史。使用所选发行候选的 tarball 闭包；候选验收范围读取该候选随附的消费者结果。

## 一、安装

准备一个空目录，以及发行候选提供的公开依赖 tarball、[运行例子](../examples/run-profile.mjs)、[锁定工具](../examples/profile-lock.mjs)和[profiles](../profiles/)。Node 版本要求读取发行包 `engines.node`。

```sh
mkdir public-consumer
cd public-consumer
npm init -y
# RELEASE 指向发行候选提供的公开 tarball 闭包目录。
npm install --offline --ignore-scripts "$RELEASE"/*.tgz
# 把 run-profile.mjs、profile-lock.mjs 和 profiles/ 复制到本目录。
node profile-lock.mjs module-lock.json @society0/host @society0/core @society0/social
node run-profile.mjs --help
```

原生二进制由发行包携带；平台和架构须与候选证据一致。[profile-lock.mjs](../examples/profile-lock.mjs) 从已安装包的公开 metadata export 记录版本、源身份和 patch 顺序。保存生成的锁和安装 lockfile，后续加载使用同一候选。

## 二、运行

先检查有效配置，再创建独立 run。标准输出是一份 JSON；失败信息写入标准错误并以非零状态退出。

```sh
node run-profile.mjs --profile profiles/rule-sqlite.json \
  --lock module-lock.json --directory runs/rule --run-id rule --inspect
node run-profile.mjs --profile profiles/rule-sqlite.json \
  --lock module-lock.json --directory runs/rule --run-id rule
node run-profile.mjs --profile profiles/rule-sqlite.json \
  --lock module-lock.json --directory runs/rule --run-id rule --reopen
```

`--inspect` 经过 ProfileLoader 的真实配置解析和静态依赖检查，输出来源与默认值。执行模式通过 RunDirectory 拥有 SQLite、历史和原生 slots；正常退出前释放借用、关闭 run 并等待宿主资源关闭。重开使用已发布完整点，跳过初始化 seed。`--offline` 会真实挂载 provider 与原生 slots、打开根完整点并观察，跳过业务推进和模型凭据读取；结果明确标为 `offline-mounted`。

- [rule-sqlite](../profiles/rule-sqlite.json)：阶段调度、主体感知和 passive 规则基线。
- [event-sqlite](../profiles/event-sqlite.json)：真实事件源与事件消费确认，例子提交一个事件并关闭输入。
- [social-sqlite](../profiles/social-sqlite.json)：Social 主体、关系、传播、感知与推荐，使用 `driver-llm`、`model-pi` 和 `agent-runtime-pi`；显式选择本机 Qwen 网关。
- [social-codex-sqlite](../profiles/social-codex-sqlite.json)：同一 LLM driver 与 pi runtime，provider 切换为 `model-codex-subscription`；默认关闭账号授权。

结果包含完整点、观察页、历史计数、耗时、RSS/heap 与关闭报告。`advanceAndSaveMs` 是推进与保存的合计时间；`history.timings` 记录实际 JSON 编码、文件发布 IO、压缩/解压流水线与 publication/restore 等计时。嵌套阶段会重叠，应分别报告；压缩流水线包含流 IO，native 阶段包含 worker 启停。纯压缩 CPU、独立流读写和设备 IO 时延保持 `unknown`。

## 三、覆盖与故障

`--patches <patch-array.json>` 接收 ProfileLoader 的 CLI 层 patch 数组。patch 的 `config` 替换该入口的整个配置对象；覆盖时保留所需必填字段。例如新 run 的规则标签覆盖：

```json
[{"id":"rule-passive","config":{"resultTag":"experiment-a"}}]
```

配置字段和更新策略以安装包 `Config`、`plugin.meta.json` 与 `--inspect` 的有效配置为准。会影响结果的配置变化使用新 run／显式分支；保留原 run 的锁和 profile。

遇到 `SOURCE_LOCK` 或缺少 export，检查该候选是否包含所选 profile 的完整公开入口，并重新生成与该安装一致的锁。遇到 `BOOTSTRAP_UNKNOWN`、`HISTORY_UNKNOWN` 或关闭失败，保存完整错误、run 目录及 operationId；通过对应 bootstrap/history/branch 的 `probe` 查询操作结果，依完整点恢复。已有可验证完整点时，可用 `--reopen --observe` 读取源完整点、live revision 与 clean/dirty/unknown 状态，保持业务推进关闭。对 unknown 操作重新发送前先完成事实核实。

Codex 订阅使用独立 provider 与 pi runtime。真实账号登录需要额外授权；离线配置检查与真实订阅调用分别记录。该例子的订阅配置执行门返回 `CODEX_NOT_AUTHORIZED`，`--inspect` 核对配置；`--offline` 验证原生装配和根点消费，真实订阅门仍需单独验收。

## 四、恢复

新建要求 world 与 lineage 目录尚未使用，创建命令重复运行会在挂载数据库之前失败。默认 authority 位于 world 同级的 `lineages/<world目录名>`，identity 为 `--run-id`；结果输出实际 `lineage` 引用。可通过 `--lineage-directory` 和 `--lineage` 指定外部 authority。分叉及其 reopen 保持源 lineage 的目录与 identity，源 world 删除后的证据保留以该独立 authority 为基础。`--reopen` 遇到缺失 authority 会报告 unavailable，保留 unknown；该路径不执行 initialize。

## 五、模型调用方

两个模型 profile 可用 `--offline` 检查完整的原生装配和根点。Social 的真实请求由获授权调用方通过导出的 `runProfile` 接入内存凭据；默认命令行返回 `MODEL_NOT_AUTHORIZED`。凭据引用为 `authorized-local`，实际 provider ID 为 `local-gateway`；驱动选择注册名 `pi/v1`。每次 activation 的上限为两次模型请求和一次工具调用，完成要求为实际 `social.publish` 回执。

```js
import {runProfile} from './run-profile.mjs';
import {InMemoryCredentialStore} from '@earendil-works/pi-ai';

export async function runAuthorizedSocial(options, gatewayKey) {
  return runProfile({...options, allowModelRequests: true, configureHost: async ctx => {
    const owner = await ctx.plugin({inject: ['modelCredentials'], async apply(ctx) {
      const store = new InMemoryCredentialStore();
      await store.modify('local-gateway', () => ({type: 'api_key', key: gatewayKey}));
      ctx.modelCredentials.register('authorized-local', store);
    }});
    await owner.await();
  }});
}
```

该凭据 owner 归属宿主，例子在 run 关闭后释放宿主。候选证据分别报告离线装配、获授权 Social 请求和真实订阅验收；请求次数以实际 attempt 证据核对。

## 六、分叉

[分支例子](../examples/run-profile-branch.mjs) 使用 RunBranch 的公开 plan/create/probe 协议，目标沿用源 SQL schema。复制一份目标 profile，在 `patches` 末尾追加例如 `{"id":"checkpoint-policy","config":{"every":2}}`，即可声明不同策略；目录、run identity、generation 与共享 lineage 由例子补入原生 patch。

```sh
node run-profile-branch.mjs --profile profiles/rule-sqlite.json \
  --directory runs/rule --run-id rule --lock module-lock.json \
  --target-profile rule-fork.json --target-directory runs/rule-b --target-run-id rule-b
node run-profile.mjs --profile runs/rule-b/profile.json \
  --directory runs/rule-b --run-id rule-b --lock module-lock.json \
  --lineage-directory runs/lineages/rule --lineage rule --reopen --observe
```

分支默认观察源与目标，不推进目标。`--advance-target` 显式推进零模型目标；获授权 Social 调用方可导入 `forkProfile`，传入同样的 `configureHost`、`allowModelRequests` 以及 `advanceTarget:true`。结果保留分支 operationId、源/目标完整点、目标有效配置、实际 authority 与关闭状态；目标保存自己的完整 profile，后续 reopen 继续使用所输出的共享 lineage。
