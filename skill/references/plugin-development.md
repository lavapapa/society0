# 本地插件与自定义机制

当现有 Core/Social 插件无法表达研究机制时，再写本地插件。先确认机制确实需要新插件，而不是用提示或人格去补环境的缺口。

## 什么时候需要插件

需要插件的情况：

- 新的行动需要改变环境状态、写入回执，并遵守事务边界。
- 新的信息源需要按主体、时点或权限提供材料。
- 新的传播、配对、排名、结算或资源机制会跨多个主体和表。
- 新的初始化需要建立领域表、主体、关系或外部数据。

不需要插件的情况：

- 只是换一段人格文本。
- 只是换一个提示或测量题项。
- 只是调整已有配置。
- 只是用规则基线和现有 Core 插件就能完成的顺序实验。

## 插件包的最小结构

本地插件包至少要有：

```text
package.json
index.js            入口，ESM
plugin.meta.json    插件元数据侧车
plugin.schema.json  配置 schema 侧车
plugin.api.json     API 声明侧车
```

`package.json` 要包含：

- `name`、`version`、`type: "module"`。
- `exports` 指向入口。
- `society.plugin` 指向三份侧车。
- `peerDependencies` 里包含 `@society0/host`。

入口模块可以导出 default 函数、类、对象，或命名 `apply`。同时提供 default 和 apply 会产生歧义错误。插件通过宿主 ABI 导出的真实 Context、Service 和 native required inject 接入。

## 侧车声明

`plugin.meta.json` 描述：

- 包名、版本、来源提交、patches 和 host ABI。
- 每个入口的 `exportPath`、作用域 `scope`、允许作用域 `allowedScopes`。
- `configPolicy`：配置字段的更新方式和是否影响结果。
- `metadata`：标题、摘要、配置版本，以及可选的 `companions`、`suggests`。

`plugin.schema.json` 是配置 schema。`plugin.api.json` 声明服务、槽位和依赖。运行期槽位使用要与静态声明对应：

- 插件向别的插件声明的 required 槽位贡献时，该 key 要出现在本插件的 `api.slots.provides`。
- 插件声明 required 槽位、自己又不贡献时，该 key 要出现在 `api.slots.requires`。
- 插件获取别的插件声明的 required 槽位、自己又不贡献时，也要写进 `api.slots.requires`。

第三方插件不依赖本仓库的测试数据，应在外层仓库按同一规则审计。

## migration

领域表通过 migration 建立。入口模块导出 `migrations`，值是数组，或 `(source) => 数组`。每个 migration 的形状：

```js
export const migrations = [{
  owner: 'my-domain',
  from: 0,
  to: 1,
  after: [],
  source: { package: 'my-plugin', entry: '.', version: 1 },
  statements: [{ sql: 'CREATE TABLE my_domain_rows (id TEXT PRIMARY KEY NOT NULL)' }]
}];
```

- `owner + from + to` 唯一。
- `after` 写依赖的其他 migration owner。
- 内容不同的同名 migration 会报冲突。
- 平台按所有活动入口的 `migrations` 导出收集，包括内置 Core/Social 插件。

领域写入使用宿主提供的事务：权威表和当前投影在同一个事务里更新，必要时同时维护索引。不要把跨表写入拆成多个互不知情的事务。

## 在实验目录中加载本地插件

实验目录结构：

```text
<实验目录>/
  node_modules/@society0/{host,core,extras,social} -> 已构建包
  node_modules/@fixture/plugin -> ../plugin
  plugin/package.json  index.js  plugin.meta.json  plugin.schema.json  plugin.api.json
  lock.json
  profile.json
```

步骤：

1. 放好 `node_modules` 和本地插件包。
2. 生成锁：

```sh
society0 lock --base <实验目录> --out <实验目录>/lock.json \
  <本地包名> @society0/host @society0/core @society0/extras @society0/social --json
```

3. profile 的 `lockfile` 指向该锁。
4. 检查：

```sh
society0 check --profile <实验目录>/profile.json --base <实验目录> --json
```

5. 小规模运行：

```sh
society0 run start --profile <实验目录>/profile.json --dir <新 run 目录> \
  --base <实验目录> --ticks 1 --detach --json
```

侧车里的 `source.gitCommit`、`dirty` 和 `patches` 要与 `lock` 得到的一致。最省事的做法是把插件代码提交一次，侧车文件保持未跟踪。

## 插件设计检查

```text
[ ] 机制确实需要新插件，而不是现有配置
[ ] 入口作用域和依赖声明完整
[ ] 配置字段有 schema，未知字段会被拒绝
[ ] 领域表有 migration，依赖顺序明确
[ ] 权威状态、投影和索引在同一事务更新
[ ] 行动区分业务拒绝和执行故障
[ ] 大正文按引用或范围读取，不一次性复制
[ ] 关闭时回收自己拥有的任务和资源
[ ] 没有把密钥、私有地址或研究数据写进公开包
[ ] 小规模运行和失败路径都实际跑过
```

## 发布边界

实验专用机制先留在实验目录或本地插件包里。只有当机制稳定、可复用、有文档和测试，并且不依赖某个私有研究时，才考虑进入公共包。不要把研究数据、运行产物、密钥或私有依赖提交到公开仓库。
