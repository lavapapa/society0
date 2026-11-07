# Society0 Next TypeScript

这个目录提供 Society0 Next 的原生 Cordis 宿主、CLI、完整 Core、Extras 与 Social，以及 SQLite 原生存储、完整点恢复和分叉能力。生产入口输出 ESM JavaScript 和声明；包名采用项目自己的 `@society0` scope，发布操作由项目单独授权。

使用发行包从[公开 SDK 安装](<docs/public-usage.md#一安装>)开始，再按[四个 profile 的运行入口](<docs/public-usage.md#二运行>)创建、推进和重开 run。[分叉步骤](<docs/public-usage.md#六分叉>)说明目标 profile、事件输入及共享 lineage authority。

## 一、构建

使用 Node 满足[包清单](<package.json>)的 engine 要求，以及 Python 3、Git 和 POSIX patch。构建依赖全部由[锁文件](<package-lock.json>)固定。

```sh
npm ci --ignore-scripts
npm run build
npm test
python3 tests/provenance.test.py
python3 scripts/acceptance.py /absolute/path/outside-repository/new-evidence-directory
```

构建输出 `.build/<package>` 和 `.build/release.json`。验收脚本在指定新目录生成 tarball、逐包离线安装两个 TS 插件和一个 JS+JSDoc 插件、执行严格类型检查与运行测试。输出目录必须在仓库外，并保留安装锁文件与打包文件清单。测试工具自己的 TypeScript 源文件由 Node 原生类型擦除执行；安装的运行库均加载编译后的 JS。

远端执行的 CPU、内存、HOME、cache、tmp 约束由项目执行器负责；本目录脚本继承调用者环境。生产包不使用 lab resolver、源码 alias 或工作区链接。

## 二、使用

插件以[宿主 ABI](<packages/host/src/abi.ts>)导出的真实 Context、Service 和 native required inject 为入口。`@society0/host/abi` 与主入口共享框架身份；`@society0/host/source` 提供框架来源；`@society0/host/order` 提供与 SQLite BINARY 一致的码点比较。增强示例见[服务插件](<tests/fixtures/provider/service.ts>)，两个独立贡献者见[TS 插件](<tests/fixtures/consumer/index.ts>)与[JSDoc 插件](<tests/fixtures/jsdoc/index.js>)。

```ts
import { createHost } from '@society0/host'
import '@fixtures/provider/service'

const host = await createHost({
  baseURL: import.meta.url,
  boot: [
    { specifier: '@fixtures/provider' },
    { specifier: '@fixtures/consumer' },
    { specifier: '@fixtures/jsdoc' },
  ],
})
try {
  console.log(host.ctx.entries.answer(21))
} finally {
  await host.close()
}
```

安装时同时提供框架 cohort、host 与插件 tarball；插件声明 host 或 cordis peer。boot 支持已安装包的显式 exports 子入口，配置为 literal 数据。入口可以是 default 函数/类/对象，或命名 apply 模块；同时提供 default 和 apply 会产生歧义错误。包预检检查物理框架解析路径、peer 范围和 exports 目标；所有 boot 入口完成预检与归一化后才开始 apply。代码中内嵌且隐藏了来源的框架实现不属于这项声明依赖检查的覆盖范围，第三方包仍须经过来源审核。

CLI 使用同一个 createHost：

```sh
society0 --boot ./boot.json
```

boot 文件是 BootEntry 数组，空数组启动纯宿主。进程空闲、SIGINT 或 SIGTERM 会请求真实异步清理，CLI 的顶层 await 等待关闭完成。清理未完成且事件循环已空时，Node 以 unsettled top-level await 诊断和非成功退出码结束；仍有活跃句柄时继续等待。配置参数与返回值以[bootstrap 类型](<packages/host/src/bootstrap.ts>)为准。

## 三、profile 与工具表

四个发行 profile（`event-sqlite`、`rule-sqlite`、`social-sqlite`、`social-codex-sqlite`）统一装配 `@society0/core/public`、`@society0/core/public-self` 与 `@society0/core/moment-views`，根目录提供 `/public` 和 `/self` 两个只读挂载；`/world` 不再装配。`public` 在 `changesPerception` 未关闭时注册 `public.changes` 感知来源。两个 social profile 另装 `@society0/core/action-files`，在 `/self/actions/offers.jsonl` 与 `/self/actions/catalog.jsonl` 提供行动清单的完整文件视图。`information` 服务暂时保留，供 plain/social 的旧来源过渡使用，不再是 agent 的默认工具。

两个 social profile 装配 `@society0/core/actor-tools`。默认工具表恰为 `bash`、`read`、`find`、`grep`、`act` 五个，跨主体、跨 moment 逐字节相同；`legacyTools` 默认为空。只有显式配置 `legacyTools`（`information`、`exploration`）时才注册 `information_*`、`exploration_*`，仅用于对照实验。`event-sqlite` 与 `rule-sqlite` 不含 agent 运行时与工具表。

## 四、来源

[原始来源清单](<vendor-baseline/source-manifest.json>)与原始六包代码保留在 vendor-baseline；[有序补丁](<patches/series.json>)应用后的源码位于 vendor。构建重新应用补丁并逐文件比较，随后机械重映射发布 scope 和导入名。上游许可证逐包保留。每个 tarball 带 SOURCE、构建依赖锁与补丁原文，便于仓外诊断。

vendor 的独立编译允许来源中既有的 implicit any/this；其余 strict 检查保持开启。host、CLI、两个 TS 插件和 JSDoc 插件保持完整 strict，消费者使用 `skipLibCheck=false` 验证真实声明边界。

## 五、验收入口

`npm test` 执行[包清单](<package.json>)中 `scripts.test` 指定的 PR01、PR02 测试。构建、来源与插件打包消费分别由上面的命令执行；完整公开 SDK 用户链使用[生命周期消费者](<tests/pr12-consumer.mjs>)、[四根 PI 消费者](<tests/pr12-four-roots.mjs>)和[模型分叉消费者](<tests/pr12-model-branch.mjs>)，参数指向独立安装目录和新证据目录，具体调用见各入口的参数定义。

宿主基础验收见[生产消费者测试](<tests/consumer.test.mjs>)及[来源失败门](<tests/provenance.test.py>)。移植的[原生生命周期测试](<tests/native/lifecycle.test.ts>)和[Loader 迁移测试](<tests/native/loader-migration.test.ts>)直接消费安装后的包。每次关闭须等待其返回的 Promise 完成，再报告资源已回收。
